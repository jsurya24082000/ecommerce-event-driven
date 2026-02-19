"""
Order Service - Order Management with Event-Driven Updates
"""

import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional
from uuid import uuid4
from decimal import Decimal
from enum import Enum

from fastapi import FastAPI, Depends, HTTPException, status, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import Column, String, DateTime, Numeric, Integer, ForeignKey, select
from sqlalchemy.ext.asyncio import AsyncSession

import sys
sys.path.append('..')
from shared.config import get_settings
from shared.database import Base, get_db, init_db
from shared.kafka_client import KafkaProducer, KafkaConsumer, EventTypes, Topics
from shared.redis_client import RedisClient, CacheKeys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

# Kafka and Redis clients
kafka_producer = KafkaProducer()
kafka_consumer = KafkaConsumer(
    topics=[Topics.INVENTORY, Topics.PAYMENTS],
    group_id="order-service"
)
redis_client = RedisClient()


# Enums
class OrderStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


# Database Models
class Order(Base):
    __tablename__ = "orders"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(36), nullable=False, index=True)
    status = Column(String(50), default=OrderStatus.PENDING)
    total_amount = Column(Numeric(10, 2), nullable=False)
    shipping_address = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OrderItem(Base):
    __tablename__ = "order_items"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    order_id = Column(String(36), ForeignKey("orders.id"), nullable=False, index=True)
    product_id = Column(String(36), nullable=False)
    product_name = Column(String(255))
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)


# Pydantic Schemas
class OrderItemCreate(BaseModel):
    product_id: str
    product_name: str
    quantity: int
    unit_price: float


class OrderCreate(BaseModel):
    items: List[OrderItemCreate]
    shipping_address: str


class OrderItemResponse(BaseModel):
    id: str
    product_id: str
    product_name: str
    quantity: int
    unit_price: float
    
    class Config:
        from_attributes = True


class OrderResponse(BaseModel):
    id: str
    user_id: str
    status: str
    total_amount: float
    shipping_address: str
    created_at: datetime
    items: List[OrderItemResponse] = []
    
    class Config:
        from_attributes = True


# Event Handlers
async def handle_inventory_reserved(event: dict):
    """Handle inventory reserved event."""
    order_id = event.get("order_id")
    logger.info(f"Inventory reserved for order {order_id}")
    
    # Initiate payment
    await kafka_producer.publish(
        Topics.PAYMENTS,
        {
            "event_type": EventTypes.PAYMENT_INITIATED,
            "order_id": order_id,
            "amount": event.get("total_amount"),
            "user_id": event.get("user_id"),
            "timestamp": datetime.utcnow().isoformat()
        },
        key=order_id
    )


async def handle_inventory_released(event: dict):
    """Handle inventory released event (order cancelled/failed)."""
    order_id = event.get("order_id")
    logger.info(f"Inventory released for order {order_id}")


async def handle_payment_completed(event: dict):
    """Handle payment completed event."""
    order_id = event.get("order_id")
    logger.info(f"Payment completed for order {order_id}")
    
    # Update order status
    # In production, use database session properly
    await kafka_producer.publish(
        Topics.ORDERS,
        {
            "event_type": EventTypes.ORDER_CONFIRMED,
            "order_id": order_id,
            "timestamp": datetime.utcnow().isoformat()
        },
        key=order_id
    )


async def handle_payment_failed(event: dict):
    """Handle payment failed event."""
    order_id = event.get("order_id")
    logger.info(f"Payment failed for order {order_id}")
    
    # Release inventory
    await kafka_producer.publish(
        Topics.INVENTORY,
        {
            "event_type": EventTypes.INVENTORY_RELEASED,
            "order_id": order_id,
            "items": event.get("items", []),
            "timestamp": datetime.utcnow().isoformat()
        },
        key=order_id
    )


# Register event handlers
kafka_consumer.register_handler(EventTypes.INVENTORY_RESERVED, handle_inventory_reserved)
kafka_consumer.register_handler(EventTypes.INVENTORY_RELEASED, handle_inventory_released)
kafka_consumer.register_handler(EventTypes.PAYMENT_COMPLETED, handle_payment_completed)
kafka_consumer.register_handler(EventTypes.PAYMENT_FAILED, handle_payment_failed)


# Background consumer task
async def start_consumer():
    """Start Kafka consumer in background."""
    await kafka_consumer.start()
    await kafka_consumer.consume()


# Lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Order Service...")
    await init_db()
    await kafka_producer.start()
    await redis_client.connect()
    
    # Start consumer in background
    consumer_task = asyncio.create_task(start_consumer())
    
    yield
    
    # Shutdown
    consumer_task.cancel()
    await kafka_producer.stop()
    await kafka_consumer.stop()
    await redis_client.disconnect()
    logger.info("Order Service stopped")


# FastAPI App
app = FastAPI(
    title="Order Service",
    description="Order Management with Event-Driven Updates",
    version="1.0.0",
    lifespan=lifespan
)


# Mock auth dependency (in production, validate JWT)
async def get_current_user_id() -> str:
    return "user-123"  # Mock user ID


# API Endpoints
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "order-service"}


@app.post("/api/v1/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    order_data: OrderCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Create a new order."""
    # Calculate total
    total_amount = sum(
        item.quantity * item.unit_price for item in order_data.items
    )
    
    # Create order
    order = Order(
        user_id=user_id,
        total_amount=total_amount,
        shipping_address=order_data.shipping_address,
        status=OrderStatus.PENDING
    )
    db.add(order)
    await db.flush()
    
    # Create order items
    items = []
    for item_data in order_data.items:
        item = OrderItem(
            order_id=order.id,
            product_id=item_data.product_id,
            product_name=item_data.product_name,
            quantity=item_data.quantity,
            unit_price=item_data.unit_price
        )
        db.add(item)
        items.append(item)
    
    await db.commit()
    await db.refresh(order)
    
    # Publish order created event
    await kafka_producer.publish(
        Topics.ORDERS,
        {
            "event_type": EventTypes.ORDER_CREATED,
            "order_id": order.id,
            "user_id": user_id,
            "total_amount": float(total_amount),
            "items": [
                {
                    "product_id": item.product_id,
                    "quantity": item.quantity,
                    "unit_price": float(item.unit_price)
                }
                for item in items
            ],
            "timestamp": datetime.utcnow().isoformat()
        },
        key=order.id
    )
    
    logger.info(f"Order created: {order.id}")
    
    return OrderResponse(
        id=order.id,
        user_id=order.user_id,
        status=order.status,
        total_amount=float(order.total_amount),
        shipping_address=order.shipping_address,
        created_at=order.created_at,
        items=[OrderItemResponse(
            id=item.id,
            product_id=item.product_id,
            product_name=item.product_name,
            quantity=item.quantity,
            unit_price=float(item.unit_price)
        ) for item in items]
    )


@app.get("/api/v1/orders", response_model=List[OrderResponse])
async def list_orders(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """List user's orders."""
    result = await db.execute(
        select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc())
    )
    orders = result.scalars().all()
    
    return [OrderResponse(
        id=order.id,
        user_id=order.user_id,
        status=order.status,
        total_amount=float(order.total_amount),
        shipping_address=order.shipping_address,
        created_at=order.created_at,
        items=[]
    ) for order in orders]


@app.get("/api/v1/orders/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get order details."""
    # Check cache
    cached = await redis_client.get(CacheKeys.order(order_id))
    if cached:
        return OrderResponse(**cached)
    
    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.user_id == user_id)
    )
    order = result.scalar_one_or_none()
    
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Get items
    items_result = await db.execute(
        select(OrderItem).where(OrderItem.order_id == order_id)
    )
    items = items_result.scalars().all()
    
    response = OrderResponse(
        id=order.id,
        user_id=order.user_id,
        status=order.status,
        total_amount=float(order.total_amount),
        shipping_address=order.shipping_address,
        created_at=order.created_at,
        items=[OrderItemResponse(
            id=item.id,
            product_id=item.product_id,
            product_name=item.product_name,
            quantity=item.quantity,
            unit_price=float(item.unit_price)
        ) for item in items]
    )
    
    # Cache response
    await redis_client.set(CacheKeys.order(order_id), response.model_dump(), ttl=300)
    
    return response


@app.put("/api/v1/orders/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Cancel an order."""
    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.user_id == user_id)
    )
    order = result.scalar_one_or_none()
    
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    if order.status not in [OrderStatus.PENDING, OrderStatus.CONFIRMED]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel order in {order.status} status"
        )
    
    order.status = OrderStatus.CANCELLED
    await db.commit()
    
    # Invalidate cache
    await redis_client.delete(CacheKeys.order(order_id))
    
    # Publish cancellation event
    await kafka_producer.publish(
        Topics.ORDERS,
        {
            "event_type": EventTypes.ORDER_CANCELLED,
            "order_id": order_id,
            "user_id": user_id,
            "timestamp": datetime.utcnow().isoformat()
        },
        key=order_id
    )
    
    logger.info(f"Order cancelled: {order_id}")
    
    return {"message": "Order cancelled successfully"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
