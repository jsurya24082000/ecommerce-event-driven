"""
Outbox Pattern Implementation for Reliable Event Publishing.

The biggest real-world bug: DB commit succeeds but Kafka publish fails (or vice versa).

Solution: Transactional Outbox Pattern
1. Write business data + outbox_events row in SAME DB transaction
2. Background worker publishes outbox rows to Kafka
3. Mark as published after successful Kafka send

Interview line: "Outbox pattern guarantees we never lose events between DB and Kafka."
"""

import logging
import asyncio
import json
import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class OutboxStatus(str, Enum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


@dataclass
class OutboxEvent:
    """Event stored in outbox table."""
    id: str
    aggregate_type: str  # e.g., 'order', 'payment'
    aggregate_id: str    # e.g., order_id
    event_type: str      # e.g., 'order.created'
    payload: Dict[str, Any]
    partition_key: str
    topic: str
    status: OutboxStatus
    created_at: str
    published_at: Optional[str] = None
    retry_count: int = 0
    error_message: Optional[str] = None


class OutboxRepository:
    """
    Repository for outbox events.
    
    Table schema:
    CREATE TABLE outbox_events (
        id UUID PRIMARY KEY,
        aggregate_type VARCHAR(50) NOT NULL,
        aggregate_id VARCHAR(100) NOT NULL,
        event_type VARCHAR(100) NOT NULL,
        payload JSONB NOT NULL,
        partition_key VARCHAR(100) NOT NULL,
        topic VARCHAR(100) NOT NULL,
        status VARCHAR(20) DEFAULT 'pending',
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        published_at TIMESTAMP WITH TIME ZONE,
        retry_count INT DEFAULT 0,
        error_message TEXT,
        INDEX idx_outbox_status_created (status, created_at)
    );
    """
    
    def __init__(self, db_session):
        self.session = db_session
    
    async def insert(self, event: OutboxEvent) -> str:
        """
        Insert outbox event in the SAME transaction as business data.
        This is called within the service's business transaction.
        """
        query = """
            INSERT INTO outbox_events 
            (id, aggregate_type, aggregate_id, event_type, payload, 
             partition_key, topic, status, created_at)
            VALUES (:id, :aggregate_type, :aggregate_id, :event_type, :payload,
                    :partition_key, :topic, :status, :created_at)
        """
        
        await self.session.execute(query, {
            "id": event.id,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "event_type": event.event_type,
            "payload": json.dumps(event.payload),
            "partition_key": event.partition_key,
            "topic": event.topic,
            "status": event.status.value,
            "created_at": event.created_at
        })
        
        return event.id
    
    async def get_pending_events(self, limit: int = 100) -> List[OutboxEvent]:
        """Get pending events for publishing."""
        query = """
            SELECT * FROM outbox_events 
            WHERE status = 'pending' 
            ORDER BY created_at ASC 
            LIMIT :limit
            FOR UPDATE SKIP LOCKED
        """
        
        result = await self.session.execute(query, {"limit": limit})
        rows = result.fetchall()
        
        return [
            OutboxEvent(
                id=row.id,
                aggregate_type=row.aggregate_type,
                aggregate_id=row.aggregate_id,
                event_type=row.event_type,
                payload=json.loads(row.payload),
                partition_key=row.partition_key,
                topic=row.topic,
                status=OutboxStatus(row.status),
                created_at=row.created_at.isoformat(),
                published_at=row.published_at.isoformat() if row.published_at else None,
                retry_count=row.retry_count,
                error_message=row.error_message
            )
            for row in rows
        ]
    
    async def mark_published(self, event_id: str):
        """Mark event as successfully published."""
        query = """
            UPDATE outbox_events 
            SET status = 'published', published_at = NOW()
            WHERE id = :id
        """
        await self.session.execute(query, {"id": event_id})
    
    async def mark_failed(self, event_id: str, error: str):
        """Mark event as failed with error message."""
        query = """
            UPDATE outbox_events 
            SET status = 'failed', 
                retry_count = retry_count + 1,
                error_message = :error
            WHERE id = :id
        """
        await self.session.execute(query, {"id": event_id, "error": error})
    
    async def increment_retry(self, event_id: str):
        """Increment retry count."""
        query = """
            UPDATE outbox_events 
            SET retry_count = retry_count + 1
            WHERE id = :id
        """
        await self.session.execute(query, {"id": event_id})


class OutboxPublisher:
    """
    Background worker that publishes outbox events to Kafka.
    
    Runs continuously, polling for pending events and publishing them.
    Ensures at-least-once delivery (consumers must be idempotent).
    """
    
    MAX_RETRIES = 5
    POLL_INTERVAL_SECONDS = 1
    BATCH_SIZE = 100
    
    def __init__(
        self,
        db_session_factory,
        kafka_producer,
        service_name: str
    ):
        self.db_factory = db_session_factory
        self.producer = kafka_producer
        self.service_name = service_name
        self._running = False
    
    async def start(self):
        """Start the outbox publisher worker."""
        self._running = True
        logger.info(f"Outbox publisher started for {self.service_name}")
        
        while self._running:
            try:
                published = await self._publish_batch()
                if published == 0:
                    # No events, wait before polling again
                    await asyncio.sleep(self.POLL_INTERVAL_SECONDS)
            except Exception as e:
                logger.error(f"Outbox publisher error: {e}")
                await asyncio.sleep(self.POLL_INTERVAL_SECONDS)
    
    async def stop(self):
        """Stop the outbox publisher."""
        self._running = False
        logger.info("Outbox publisher stopped")
    
    async def _publish_batch(self) -> int:
        """Publish a batch of pending events."""
        async with self.db_factory() as session:
            repo = OutboxRepository(session)
            events = await repo.get_pending_events(self.BATCH_SIZE)
            
            if not events:
                return 0
            
            published_count = 0
            
            for event in events:
                try:
                    # Publish to Kafka
                    await self.producer.publish(
                        topic=event.topic,
                        event_type=event.event_type,
                        payload=event.payload,
                        partition_key=event.partition_key,
                        correlation_id=event.id
                    )
                    
                    # Mark as published
                    await repo.mark_published(event.id)
                    published_count += 1
                    
                    logger.debug(
                        f"Published outbox event: {event.event_type} "
                        f"[id={event.id}, topic={event.topic}]"
                    )
                    
                except Exception as e:
                    if event.retry_count >= self.MAX_RETRIES:
                        await repo.mark_failed(event.id, str(e))
                        logger.error(
                            f"Outbox event failed permanently: {event.id} - {e}"
                        )
                    else:
                        await repo.increment_retry(event.id)
                        logger.warning(
                            f"Outbox publish failed (retry {event.retry_count + 1}): "
                            f"{event.id} - {e}"
                        )
            
            await session.commit()
            
            if published_count > 0:
                logger.info(f"Published {published_count} outbox events")
            
            return published_count


def create_outbox_event(
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    payload: Dict[str, Any],
    topic: str,
    partition_key: str
) -> OutboxEvent:
    """
    Helper to create an outbox event.
    
    Usage in service:
        async with db.transaction():
            # Business logic
            order = await create_order(...)
            
            # Create outbox event in same transaction
            outbox_event = create_outbox_event(
                aggregate_type="order",
                aggregate_id=order.id,
                event_type="order.created",
                payload=order.to_dict(),
                topic="orders",
                partition_key=f"order:{order.id}"
            )
            await outbox_repo.insert(outbox_event)
    """
    return OutboxEvent(
        id=str(uuid.uuid4()),
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload=payload,
        partition_key=partition_key,
        topic=topic,
        status=OutboxStatus.PENDING,
        created_at=datetime.now(timezone.utc).isoformat()
    )
