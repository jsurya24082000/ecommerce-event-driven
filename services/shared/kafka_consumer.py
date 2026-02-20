"""
Enhanced Kafka Consumer with Reliability Patterns.

Features:
- Consumer groups for horizontal scaling
- Idempotency via processed event tracking
- Retry with exponential backoff
- Dead letter queue for failed messages
- Correlation ID propagation for tracing
"""

import json
import logging
import asyncio
from typing import Callable, Dict, Any, Optional, Set
from datetime import datetime, timezone
from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError

logger = logging.getLogger(__name__)


class IdempotencyStore:
    """
    Track processed event IDs to ensure idempotency.
    In production, use Redis with TTL for distributed idempotency.
    """
    
    def __init__(self, redis_client=None, ttl_seconds: int = 86400):
        self._processed: Set[str] = set()
        self._redis = redis_client
        self._ttl = ttl_seconds
    
    async def is_processed(self, event_id: str) -> bool:
        """Check if event was already processed."""
        if self._redis:
            return await self._redis.exists(f"processed:{event_id}")
        return event_id in self._processed
    
    async def mark_processed(self, event_id: str):
        """Mark event as processed."""
        if self._redis:
            await self._redis.setex(f"processed:{event_id}", self._ttl, "1")
        else:
            self._processed.add(event_id)
            # Limit in-memory set size
            if len(self._processed) > 100000:
                self._processed.clear()


class ReliableKafkaConsumer:
    """
    Production-grade Kafka consumer with reliability patterns.
    
    Features:
    - Consumer group for horizontal scaling
    - Idempotent message processing
    - Retry with exponential backoff
    - Dead letter queue for permanent failures
    - Manual offset commit after successful processing
    """
    
    def __init__(
        self,
        topics: list,
        group_id: str,
        bootstrap_servers: str,
        service_name: str,
        max_retries: int = 3,
        retry_backoff_ms: int = 100,
        redis_client=None
    ):
        self.topics = topics
        self.group_id = group_id
        self.bootstrap_servers = bootstrap_servers
        self.service_name = service_name
        self.max_retries = max_retries
        self.retry_backoff_ms = retry_backoff_ms
        
        self._consumer: Optional[AIOKafkaConsumer] = None
        self._handlers: Dict[str, Callable] = {}
        self._idempotency = IdempotencyStore(redis_client)
        self._running = False
        
    def register_handler(self, event_type: str, handler: Callable):
        """Register a handler for a specific event type."""
        self._handlers[event_type] = handler
        logger.info(f"Registered handler for {event_type}")
        
    async def start(self):
        """Start the Kafka consumer."""
        self._consumer = AIOKafkaConsumer(
            *self.topics,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            value_deserializer=lambda v: json.loads(v.decode('utf-8')),
            # Manual commit for reliability
            enable_auto_commit=False,
            auto_offset_reset='earliest',
            # Consumer settings for reliability
            max_poll_records=100,
            session_timeout_ms=30000,
            heartbeat_interval_ms=10000,
        )
        await self._consumer.start()
        self._running = True
        logger.info(
            f"Kafka consumer started: group={self.group_id}, topics={self.topics}"
        )
        
    async def stop(self):
        """Stop the consumer gracefully."""
        self._running = False
        if self._consumer:
            await self._consumer.stop()
            logger.info("Kafka consumer stopped")
    
    async def consume(self):
        """
        Consume messages with reliability guarantees.
        
        Processing flow:
        1. Check idempotency (skip if already processed)
        2. Process with retry logic
        3. On success: commit offset, mark processed
        4. On failure: send to DLQ after max retries
        """
        if not self._consumer:
            raise RuntimeError("Consumer not started")
        
        logger.info(f"Starting consumption loop for {self.service_name}")
        
        async for message in self._consumer:
            if not self._running:
                break
                
            try:
                event = message.value
                event_id = event.get('event_id')
                event_type = event.get('event_type')
                correlation_id = event.get('correlation_id', 'unknown')
                
                # Extract correlation ID from headers if available
                if message.headers:
                    for key, value in message.headers:
                        if key == 'correlation_id':
                            correlation_id = value.decode()
                            break
                
                logger.debug(
                    f"Received {event_type} [event_id={event_id}, "
                    f"correlation_id={correlation_id}, partition={message.partition}]"
                )
                
                # Idempotency check
                if event_id and await self._idempotency.is_processed(event_id):
                    logger.info(f"Skipping duplicate event: {event_id}")
                    await self._consumer.commit()
                    continue
                
                # Process with retry
                success = await self._process_with_retry(
                    event, event_type, correlation_id
                )
                
                if success:
                    # Mark as processed and commit
                    if event_id:
                        await self._idempotency.mark_processed(event_id)
                    await self._consumer.commit()
                else:
                    # Send to DLQ (already done in _process_with_retry)
                    await self._consumer.commit()
                    
            except Exception as e:
                logger.error(f"Unexpected error processing message: {e}")
                # Commit to avoid infinite loop on poison messages
                await self._consumer.commit()
    
    async def _process_with_retry(
        self,
        event: Dict[str, Any],
        event_type: str,
        correlation_id: str
    ) -> bool:
        """Process event with retry logic."""
        handler = self._handlers.get(event_type)
        
        if not handler:
            logger.warning(f"No handler for event type: {event_type}")
            return True  # Don't retry unknown events
        
        for attempt in range(self.max_retries + 1):
            try:
                await handler(event, correlation_id)
                return True
                
            except Exception as e:
                if attempt < self.max_retries:
                    wait_time = self.retry_backoff_ms * (2 ** attempt) / 1000
                    logger.warning(
                        f"Handler failed (attempt {attempt + 1}/{self.max_retries + 1}), "
                        f"retrying in {wait_time}s: {e}"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(
                        f"Handler failed after {self.max_retries + 1} attempts: {e}"
                    )
                    await self._send_to_dlq(event, str(e))
                    return False
        
        return False
    
    async def _send_to_dlq(self, event: Dict[str, Any], error_reason: str):
        """Send failed message to dead letter queue."""
        # This would use a producer - simplified here
        logger.error(
            f"DLQ: {event.get('event_type')} [event_id={event.get('event_id')}, "
            f"reason={error_reason}]"
        )


class ConsumerGroup:
    """
    Manage multiple consumers for different topics.
    Each service instance joins the same consumer group for load balancing.
    """
    
    def __init__(self, service_name: str, bootstrap_servers: str):
        self.service_name = service_name
        self.bootstrap_servers = bootstrap_servers
        self._consumers: Dict[str, ReliableKafkaConsumer] = {}
    
    def add_consumer(
        self,
        name: str,
        topics: list,
        group_id: str,
        handlers: Dict[str, Callable]
    ) -> ReliableKafkaConsumer:
        """Add a consumer to the group."""
        consumer = ReliableKafkaConsumer(
            topics=topics,
            group_id=group_id,
            bootstrap_servers=self.bootstrap_servers,
            service_name=self.service_name
        )
        
        for event_type, handler in handlers.items():
            consumer.register_handler(event_type, handler)
        
        self._consumers[name] = consumer
        return consumer
    
    async def start_all(self):
        """Start all consumers."""
        for name, consumer in self._consumers.items():
            await consumer.start()
            logger.info(f"Started consumer: {name}")
    
    async def stop_all(self):
        """Stop all consumers."""
        for name, consumer in self._consumers.items():
            await consumer.stop()
            logger.info(f"Stopped consumer: {name}")
    
    async def run_all(self):
        """Run all consumers concurrently."""
        tasks = [
            asyncio.create_task(consumer.consume())
            for consumer in self._consumers.values()
        ]
        await asyncio.gather(*tasks)
