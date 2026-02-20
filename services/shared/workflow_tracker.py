"""
End-to-End Workflow Tracking for E-commerce Event-Driven System.

Tracks:
1. Order workflow latency (order.created → order.confirmed)
2. Idempotency and duplicate event handling
3. Oversell detection in inventory
4. Kafka consumer lag monitoring

Why this matters:
- E2E latency is the customer-facing SLA metric
- Duplicate detection prevents double-charging and double-shipping
- Oversell detection catches critical inventory bugs
- Consumer lag indicates system health and capacity
"""

import time
import asyncio
import logging
from typing import Dict, Any, Optional, Set
from datetime import datetime, timezone
from dataclasses import dataclass, field
from collections import defaultdict
import redis.asyncio as redis
from prometheus_client import Histogram, Counter, Gauge

from .metrics import (
    ORDER_E2E_LATENCY_SECONDS,
    ORDER_STATE_TRANSITIONS_TOTAL,
    ORDER_COMPLETION_TOTAL,
    KAFKA_DUPLICATE_MESSAGES_TOTAL,
    KAFKA_CONSUMER_LAG,
    INVENTORY_OVERSELL_INCIDENTS,
    INVENTORY_STOCK_LEVEL,
    INVENTORY_RESERVED_STOCK,
)

logger = logging.getLogger(__name__)


# =============================================================================
# END-TO-END WORKFLOW LATENCY TRACKER
# =============================================================================

@dataclass
class OrderWorkflow:
    """Tracks a single order through its lifecycle."""
    order_id: str
    created_at: float
    confirmed_at: Optional[float] = None
    cancelled_at: Optional[float] = None
    current_state: str = "created"
    events: list = field(default_factory=list)
    
    def record_event(self, event_type: str, timestamp: float):
        """Record an event in the workflow."""
        self.events.append({
            "event_type": event_type,
            "timestamp": timestamp,
            "elapsed_ms": (timestamp - self.created_at) * 1000
        })


class WorkflowTracker:
    """
    Tracks end-to-end workflow latency for orders.
    
    Workflow: order.created → inventory.reserved → payment.completed → order.confirmed
    
    Uses Redis for distributed tracking across services.
    """
    
    def __init__(self, redis_client: redis.Redis, service_name: str):
        self.redis = redis_client
        self.service_name = service_name
        self.prefix = "workflow:"
        self.ttl = 3600  # 1 hour TTL for workflow data
    
    async def start_workflow(self, order_id: str, order_type: str = "standard") -> None:
        """
        Record the start of an order workflow.
        
        Called when order.created event is published.
        """
        workflow_key = f"{self.prefix}{order_id}"
        workflow_data = {
            "order_id": order_id,
            "order_type": order_type,
            "created_at": time.time(),
            "state": "created",
            "events": "order.created"
        }
        
        await self.redis.hset(workflow_key, mapping=workflow_data)
        await self.redis.expire(workflow_key, self.ttl)
        
        logger.info(f"Workflow started: order_id={order_id}")
    
    async def record_event(self, order_id: str, event_type: str, new_state: str) -> None:
        """
        Record an event in the workflow.
        
        Called by each service as it processes the order.
        """
        workflow_key = f"{self.prefix}{order_id}"
        timestamp = time.time()
        
        # Get current state for transition tracking
        current_state = await self.redis.hget(workflow_key, "state")
        if current_state:
            current_state = current_state.decode() if isinstance(current_state, bytes) else current_state
            ORDER_STATE_TRANSITIONS_TOTAL.labels(
                from_state=current_state,
                to_state=new_state
            ).inc()
        
        # Update workflow
        events = await self.redis.hget(workflow_key, "events")
        events = events.decode() if isinstance(events, bytes) else (events or "")
        events = f"{events},{event_type}" if events else event_type
        
        await self.redis.hset(workflow_key, mapping={
            "state": new_state,
            "events": events,
            f"{event_type}_at": timestamp
        })
        
        logger.info(f"Workflow event: order_id={order_id}, event={event_type}, state={new_state}")
    
    async def complete_workflow(self, order_id: str, status: str = "confirmed") -> Optional[float]:
        """
        Complete the workflow and record E2E latency.
        
        Called when order.confirmed or order.cancelled event is published.
        Returns the E2E latency in seconds.
        """
        workflow_key = f"{self.prefix}{order_id}"
        completed_at = time.time()
        
        # Get workflow data
        workflow_data = await self.redis.hgetall(workflow_key)
        if not workflow_data:
            logger.warning(f"Workflow not found: order_id={order_id}")
            return None
        
        # Decode bytes
        workflow_data = {
            k.decode() if isinstance(k, bytes) else k: 
            v.decode() if isinstance(v, bytes) else v 
            for k, v in workflow_data.items()
        }
        
        created_at = float(workflow_data.get("created_at", completed_at))
        order_type = workflow_data.get("order_type", "standard")
        
        # Calculate E2E latency
        e2e_latency = completed_at - created_at
        
        # Record metrics
        ORDER_E2E_LATENCY_SECONDS.labels(order_type=order_type).observe(e2e_latency)
        ORDER_COMPLETION_TOTAL.labels(status=status).inc()
        
        # Update workflow
        await self.redis.hset(workflow_key, mapping={
            "state": status,
            "completed_at": completed_at,
            "e2e_latency_seconds": e2e_latency
        })
        
        logger.info(
            f"Workflow completed: order_id={order_id}, status={status}, "
            f"e2e_latency={e2e_latency:.3f}s"
        )
        
        return e2e_latency
    
    async def get_workflow_stats(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get workflow statistics for an order."""
        workflow_key = f"{self.prefix}{order_id}"
        data = await self.redis.hgetall(workflow_key)
        
        if not data:
            return None
        
        return {
            k.decode() if isinstance(k, bytes) else k: 
            v.decode() if isinstance(v, bytes) else v 
            for k, v in data.items()
        }


# =============================================================================
# IDEMPOTENCY AND DUPLICATE DETECTION
# =============================================================================

class IdempotencyTracker:
    """
    Tracks processed events to detect and skip duplicates.
    
    Why this matters:
    - Kafka guarantees at-least-once delivery, not exactly-once
    - Without idempotency, we risk double-charging customers
    - Without idempotency, we risk double-shipping orders
    
    Implementation:
    - Store event IDs in Redis with TTL
    - Check before processing, skip if already seen
    - Track duplicate rate as a metric
    """
    
    def __init__(self, redis_client: redis.Redis, service_name: str):
        self.redis = redis_client
        self.service_name = service_name
        self.prefix = f"idempotency:{service_name}:"
        self.ttl = 86400  # 24 hour TTL for idempotency keys
    
    async def is_duplicate(self, event_id: str, event_type: str) -> bool:
        """
        Check if an event has already been processed.
        
        Returns True if duplicate (should skip), False if new (should process).
        """
        key = f"{self.prefix}{event_id}"
        
        # Try to set the key (NX = only if not exists)
        result = await self.redis.set(key, "1", nx=True, ex=self.ttl)
        
        if result is None:
            # Key already existed - this is a duplicate
            KAFKA_DUPLICATE_MESSAGES_TOTAL.labels(
                service=self.service_name,
                topic="events",
                event_type=event_type
            ).inc()
            logger.info(f"Duplicate event detected: event_id={event_id}, type={event_type}")
            return True
        
        return False
    
    async def mark_processed(self, event_id: str) -> None:
        """Explicitly mark an event as processed."""
        key = f"{self.prefix}{event_id}"
        await self.redis.set(key, "1", ex=self.ttl)
    
    async def get_duplicate_count(self) -> int:
        """Get count of duplicate events detected (from metrics)."""
        # This would typically come from Prometheus, but we can track locally too
        pattern = f"{self.prefix}*"
        keys = await self.redis.keys(pattern)
        return len(keys)


# =============================================================================
# OVERSELL DETECTION
# =============================================================================

class OversellDetector:
    """
    Detects and tracks inventory oversell incidents.
    
    Oversell = selling more inventory than physically available
    
    This should NEVER happen in production. Any incident is a critical bug.
    
    Detection methods:
    1. Check stock level before reservation
    2. Verify after reservation that available >= 0
    3. Periodic reconciliation with physical inventory
    """
    
    def __init__(self, service_name: str = "inventory"):
        self.service_name = service_name
        self.incidents: list = []
    
    def check_and_record(
        self,
        sku_id: str,
        warehouse: str,
        requested_qty: int,
        available_qty: int,
        reserved_qty: int
    ) -> bool:
        """
        Check for oversell condition and record if detected.
        
        Returns True if oversell detected, False otherwise.
        """
        # Oversell condition: trying to reserve more than available
        if requested_qty > available_qty:
            self._record_incident(
                sku_id=sku_id,
                warehouse=warehouse,
                requested_qty=requested_qty,
                available_qty=available_qty,
                reserved_qty=reserved_qty,
                reason="requested_exceeds_available"
            )
            return True
        
        # Check for negative available (should never happen)
        if available_qty < 0:
            self._record_incident(
                sku_id=sku_id,
                warehouse=warehouse,
                requested_qty=requested_qty,
                available_qty=available_qty,
                reserved_qty=reserved_qty,
                reason="negative_available_stock"
            )
            return True
        
        return False
    
    def verify_post_reservation(
        self,
        sku_id: str,
        warehouse: str,
        new_available: int,
        new_reserved: int
    ) -> bool:
        """
        Verify stock levels after reservation.
        
        Returns True if oversell detected, False otherwise.
        """
        if new_available < 0:
            self._record_incident(
                sku_id=sku_id,
                warehouse=warehouse,
                requested_qty=0,
                available_qty=new_available,
                reserved_qty=new_reserved,
                reason="post_reservation_negative"
            )
            return True
        
        return False
    
    def _record_incident(
        self,
        sku_id: str,
        warehouse: str,
        requested_qty: int,
        available_qty: int,
        reserved_qty: int,
        reason: str
    ):
        """Record an oversell incident."""
        incident = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sku_id": sku_id,
            "warehouse": warehouse,
            "requested_qty": requested_qty,
            "available_qty": available_qty,
            "reserved_qty": reserved_qty,
            "reason": reason
        }
        
        self.incidents.append(incident)
        
        # Record metric - this is CRITICAL
        INVENTORY_OVERSELL_INCIDENTS.labels(
            sku_id=sku_id,
            warehouse=warehouse
        ).inc()
        
        logger.error(
            f"OVERSELL INCIDENT DETECTED: sku={sku_id}, warehouse={warehouse}, "
            f"requested={requested_qty}, available={available_qty}, reason={reason}"
        )
    
    def update_stock_metrics(self, sku_id: str, warehouse: str, available: int, reserved: int):
        """Update stock level metrics for monitoring."""
        INVENTORY_STOCK_LEVEL.labels(sku_id=sku_id, warehouse=warehouse).set(available)
        INVENTORY_RESERVED_STOCK.labels(sku_id=sku_id, warehouse=warehouse).set(reserved)


# =============================================================================
# KAFKA CONSUMER LAG TRACKER
# =============================================================================

class ConsumerLagTracker:
    """
    Tracks Kafka consumer lag.
    
    Consumer lag = difference between latest offset and committed offset
    
    High lag indicates:
    - Consumers can't keep up with producers
    - Processing is too slow
    - Need to scale consumers or optimize processing
    
    This is a critical operational metric.
    """
    
    def __init__(self, service_name: str, consumer_group: str):
        self.service_name = service_name
        self.consumer_group = consumer_group
        self.lag_history: Dict[str, list] = defaultdict(list)
    
    def update_lag(self, topic: str, partition: int, lag: int):
        """Update consumer lag metric."""
        KAFKA_CONSUMER_LAG.labels(
            service=self.service_name,
            topic=topic,
            partition=str(partition),
            consumer_group=self.consumer_group
        ).set(lag)
        
        # Track history for trend analysis
        key = f"{topic}:{partition}"
        self.lag_history[key].append({
            "timestamp": time.time(),
            "lag": lag
        })
        
        # Keep only last 100 samples
        if len(self.lag_history[key]) > 100:
            self.lag_history[key] = self.lag_history[key][-100:]
        
        # Alert if lag is high
        if lag > 10000:
            logger.warning(
                f"High consumer lag: topic={topic}, partition={partition}, "
                f"lag={lag}, consumer_group={self.consumer_group}"
            )
    
    def get_lag_trend(self, topic: str, partition: int) -> Dict[str, Any]:
        """Get lag trend for a topic/partition."""
        key = f"{topic}:{partition}"
        history = self.lag_history.get(key, [])
        
        if not history:
            return {"trend": "unknown", "samples": 0}
        
        lags = [h["lag"] for h in history]
        
        return {
            "current": lags[-1] if lags else 0,
            "min": min(lags),
            "max": max(lags),
            "avg": sum(lags) / len(lags),
            "trend": "increasing" if len(lags) > 1 and lags[-1] > lags[0] else "stable",
            "samples": len(lags)
        }
    
    async def fetch_lag_from_kafka(self, admin_client) -> Dict[str, int]:
        """
        Fetch actual lag from Kafka admin client.
        
        This would typically use kafka-python's AdminClient.
        """
        # Placeholder - actual implementation depends on Kafka client
        # In production, use:
        # from kafka import KafkaAdminClient
        # admin = KafkaAdminClient(bootstrap_servers=...)
        # consumer_groups = admin.list_consumer_groups()
        # offsets = admin.list_consumer_group_offsets(group_id)
        pass


# =============================================================================
# COMBINED TRACKER
# =============================================================================

class DistributedTracker:
    """
    Combined tracker for all workflow metrics.
    
    Provides a single interface for:
    - Workflow tracking
    - Idempotency
    - Oversell detection
    - Consumer lag
    """
    
    def __init__(self, redis_client: redis.Redis, service_name: str, consumer_group: str = None):
        self.workflow = WorkflowTracker(redis_client, service_name)
        self.idempotency = IdempotencyTracker(redis_client, service_name)
        self.oversell = OversellDetector(service_name)
        self.consumer_lag = ConsumerLagTracker(
            service_name, 
            consumer_group or f"{service_name}-group"
        )
        self.service_name = service_name
    
    async def process_event(
        self,
        event_id: str,
        event_type: str,
        handler,
        *args,
        **kwargs
    ):
        """
        Process an event with full tracking.
        
        1. Check for duplicates
        2. Process if not duplicate
        3. Track processing time
        """
        # Check idempotency
        if await self.idempotency.is_duplicate(event_id, event_type):
            return {"status": "skipped", "reason": "duplicate"}
        
        # Process event
        start_time = time.time()
        try:
            result = await handler(*args, **kwargs)
            return {"status": "processed", "result": result}
        except Exception as e:
            logger.error(f"Event processing failed: event_id={event_id}, error={e}")
            raise
