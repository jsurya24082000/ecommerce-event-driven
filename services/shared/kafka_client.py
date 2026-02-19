"""
Kafka producer and consumer utilities.
"""

import json
import logging
from typing import Callable, Dict, Any, Optional
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
from .config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class KafkaProducer:
    """Async Kafka producer for publishing events."""
    
    def __init__(self):
        self._producer: Optional[AIOKafkaProducer] = None
        
    async def start(self):
        """Start the Kafka producer."""
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            key_serializer=lambda k: k.encode('utf-8') if k else None
        )
        await self._producer.start()
        logger.info("Kafka producer started")
        
    async def stop(self):
        """Stop the Kafka producer."""
        if self._producer:
            await self._producer.stop()
            logger.info("Kafka producer stopped")
            
    async def publish(self, topic: str, event: Dict[str, Any], key: str = None):
        """
        Publish an event to a Kafka topic.
        
        Args:
            topic: Kafka topic name
            event: Event data to publish
            key: Optional partition key
        """
        if not self._producer:
            raise RuntimeError("Producer not started")
            
        await self._producer.send_and_wait(topic, value=event, key=key)
        logger.debug(f"Published to {topic}: {event.get('event_type', 'unknown')}")


class KafkaConsumer:
    """Async Kafka consumer for subscribing to events."""
    
    def __init__(self, topics: list, group_id: str):
        self.topics = topics
        self.group_id = group_id
        self._consumer: Optional[AIOKafkaConsumer] = None
        self._handlers: Dict[str, Callable] = {}
        
    def register_handler(self, event_type: str, handler: Callable):
        """Register a handler for a specific event type."""
        self._handlers[event_type] = handler
        
    async def start(self):
        """Start the Kafka consumer."""
        self._consumer = AIOKafkaConsumer(
            *self.topics,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=self.group_id,
            value_deserializer=lambda v: json.loads(v.decode('utf-8')),
            auto_offset_reset='earliest',
            enable_auto_commit=True
        )
        await self._consumer.start()
        logger.info(f"Kafka consumer started for topics: {self.topics}")
        
    async def stop(self):
        """Stop the Kafka consumer."""
        if self._consumer:
            await self._consumer.stop()
            logger.info("Kafka consumer stopped")
            
    async def consume(self):
        """Consume messages and dispatch to handlers."""
        if not self._consumer:
            raise RuntimeError("Consumer not started")
            
        async for message in self._consumer:
            try:
                event = message.value
                event_type = event.get('event_type')
                
                if event_type in self._handlers:
                    await self._handlers[event_type](event)
                else:
                    logger.warning(f"No handler for event type: {event_type}")
                    
            except Exception as e:
                logger.error(f"Error processing message: {e}")


# Event types
class EventTypes:
    # User events
    USER_REGISTERED = "user.registered"
    USER_UPDATED = "user.updated"
    
    # Order events
    ORDER_CREATED = "order.created"
    ORDER_CONFIRMED = "order.confirmed"
    ORDER_CANCELLED = "order.cancelled"
    ORDER_COMPLETED = "order.completed"
    
    # Inventory events
    INVENTORY_RESERVED = "inventory.reserved"
    INVENTORY_RELEASED = "inventory.released"
    INVENTORY_UPDATED = "inventory.updated"
    STOCK_LOW = "inventory.stock_low"
    
    # Payment events
    PAYMENT_INITIATED = "payment.initiated"
    PAYMENT_COMPLETED = "payment.completed"
    PAYMENT_FAILED = "payment.failed"
    PAYMENT_REFUNDED = "payment.refunded"


# Kafka topics
class Topics:
    USERS = "users"
    ORDERS = "orders"
    INVENTORY = "inventory"
    PAYMENTS = "payments"
    NOTIFICATIONS = "notifications"
