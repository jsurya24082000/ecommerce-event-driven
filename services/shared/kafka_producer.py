"""
Enhanced Kafka Producer with Partitioning Strategy and Reliability Patterns.

Partitioning Strategy:
- orders topic: partition key = orderId
- inventory topic: partition key = skuId  
- payments topic: partition key = orderId

Features:
- Idempotent producer (exactly-once semantics)
- Automatic retries with backoff
- Dead letter queue for failed messages
- Correlation ID propagation for tracing
"""

import json
import logging
import uuid
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

logger = logging.getLogger(__name__)


@dataclass
class EventEnvelope:
    """Standard event envelope with metadata for reliability."""
    event_id: str
    event_type: str
    timestamp: str
    correlation_id: str
    source_service: str
    payload: Dict[str, Any]
    retry_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PartitionStrategy:
    """Kafka partition key strategies for ordering guarantees."""
    
    @staticmethod
    def order_key(order_id: str) -> str:
        """Orders partitioned by order_id for ordering guarantees."""
        return f"order:{order_id}"
    
    @staticmethod
    def inventory_key(sku_id: str) -> str:
        """Inventory partitioned by sku_id to avoid races on same SKU."""
        return f"sku:{sku_id}"
    
    @staticmethod
    def payment_key(order_id: str) -> str:
        """Payments partitioned by order_id to correlate with orders."""
        return f"payment:{order_id}"
    
    @staticmethod
    def user_key(user_id: str) -> str:
        """Users partitioned by user_id."""
        return f"user:{user_id}"


class ReliableKafkaProducer:
    """
    Production-grade Kafka producer with reliability patterns.
    
    Features:
    - Idempotent producer (enable.idempotence=true)
    - Automatic retries with exponential backoff
    - Dead letter queue for permanent failures
    - Event envelope with correlation ID
    """
    
    def __init__(
        self,
        bootstrap_servers: str,
        service_name: str,
        max_retries: int = 3,
        retry_backoff_ms: int = 100
    ):
        self.bootstrap_servers = bootstrap_servers
        self.service_name = service_name
        self.max_retries = max_retries
        self.retry_backoff_ms = retry_backoff_ms
        self._producer: Optional[AIOKafkaProducer] = None
        
    async def start(self):
        """Start the idempotent Kafka producer."""
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            key_serializer=lambda k: k.encode('utf-8') if k else None,
            # Idempotent producer settings
            enable_idempotence=True,
            acks='all',
            retries=self.max_retries,
            max_in_flight_requests_per_connection=5,
            # Compression for throughput
            compression_type='lz4',
            # Batching for efficiency
            linger_ms=5,
            batch_size=16384,
        )
        await self._producer.start()
        logger.info(f"Reliable Kafka producer started for {self.service_name}")
        
    async def stop(self):
        """Stop the producer gracefully."""
        if self._producer:
            await self._producer.stop()
            logger.info("Kafka producer stopped")
    
    async def publish(
        self,
        topic: str,
        event_type: str,
        payload: Dict[str, Any],
        partition_key: str,
        correlation_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> bool:
        """
        Publish an event with reliability guarantees.
        
        Args:
            topic: Kafka topic
            event_type: Type of event (e.g., 'order.created')
            payload: Event data
            partition_key: Key for partitioning (ensures ordering)
            correlation_id: Trace ID for distributed tracing
            headers: Additional Kafka headers
            
        Returns:
            True if published successfully, False otherwise
        """
        if not self._producer:
            raise RuntimeError("Producer not started")
        
        # Create event envelope
        envelope = EventEnvelope(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            correlation_id=correlation_id or str(uuid.uuid4()),
            source_service=self.service_name,
            payload=payload
        )
        
        # Prepare headers
        kafka_headers = [
            ("correlation_id", envelope.correlation_id.encode()),
            ("event_type", event_type.encode()),
            ("source", self.service_name.encode()),
        ]
        if headers:
            kafka_headers.extend([(k, v.encode()) for k, v in headers.items()])
        
        # Publish with retry
        for attempt in range(self.max_retries + 1):
            try:
                await self._producer.send_and_wait(
                    topic,
                    value=envelope.to_dict(),
                    key=partition_key,
                    headers=kafka_headers
                )
                logger.debug(
                    f"Published {event_type} to {topic} "
                    f"[key={partition_key}, correlation_id={envelope.correlation_id}]"
                )
                return True
                
            except KafkaError as e:
                envelope.retry_count = attempt + 1
                if attempt < self.max_retries:
                    wait_time = self.retry_backoff_ms * (2 ** attempt) / 1000
                    logger.warning(
                        f"Kafka publish failed (attempt {attempt + 1}), "
                        f"retrying in {wait_time}s: {e}"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    # Send to dead letter queue
                    await self._send_to_dlq(topic, envelope, str(e))
                    return False
        
        return False
    
    async def _send_to_dlq(
        self,
        original_topic: str,
        envelope: EventEnvelope,
        error_reason: str
    ):
        """Send failed message to dead letter queue."""
        dlq_payload = {
            "original_topic": original_topic,
            "original_event": envelope.to_dict(),
            "error_reason": error_reason,
            "failed_at": datetime.now(timezone.utc).isoformat()
        }
        
        try:
            await self._producer.send_and_wait(
                "dead-letter",
                value=dlq_payload,
                key=f"dlq:{envelope.event_id}"
            )
            logger.error(
                f"Message sent to DLQ: {envelope.event_type} "
                f"[event_id={envelope.event_id}, reason={error_reason}]"
            )
        except Exception as e:
            logger.critical(f"Failed to send to DLQ: {e}")
    
    async def publish_order_event(
        self,
        event_type: str,
        order_id: str,
        payload: Dict[str, Any],
        correlation_id: Optional[str] = None
    ) -> bool:
        """Publish order event with order_id as partition key."""
        return await self.publish(
            topic="orders",
            event_type=event_type,
            payload=payload,
            partition_key=PartitionStrategy.order_key(order_id),
            correlation_id=correlation_id
        )
    
    async def publish_inventory_event(
        self,
        event_type: str,
        sku_id: str,
        payload: Dict[str, Any],
        correlation_id: Optional[str] = None
    ) -> bool:
        """Publish inventory event with sku_id as partition key."""
        return await self.publish(
            topic="inventory",
            event_type=event_type,
            payload=payload,
            partition_key=PartitionStrategy.inventory_key(sku_id),
            correlation_id=correlation_id
        )
    
    async def publish_payment_event(
        self,
        event_type: str,
        order_id: str,
        payload: Dict[str, Any],
        correlation_id: Optional[str] = None
    ) -> bool:
        """Publish payment event with order_id as partition key."""
        return await self.publish(
            topic="payments",
            event_type=event_type,
            payload=payload,
            partition_key=PartitionStrategy.payment_key(order_id),
            correlation_id=correlation_id
        )
