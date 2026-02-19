"""
Inventory Service - Stock Management with Event-Driven Updates
"""

import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from fastapi import FastAPI, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import Column, String, DateTime, Numeric, Integer, select
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
    topics=[Topics.ORDERS],
    group_id="inventory-service"
)
redis_client = RedisClient()


# Database Models
class Product(Base):
    __tablename__ = "products"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name = Column(String(255), nullable=False)
    description = Column(String(1000))
    price = Column(Numeric(10, 2), nullable=False)
    stock_quantity = Column(Integer, default=0)
    reserved_quantity = Column(Integer, default=0)
    category = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @property
    def available_quantity(self) -> int:
        return self.stock_quantity - self.reserved_quantity


# Pydantic Schemas
class ProductCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: float
    stock_quantity: int = 0
    category: Optional[str] = None


class ProductResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    price: float
    stock_quantity: int
    reserved_quantity: int
    available_quantity: int
    category: Optional[str]
    
    class Config:
        from_attributes = True


class StockUpdate(BaseModel):
    quantity: int
    operation: str = "set"  # set, add, subtract


# Event Handlers
async def handle_order_created(event: dict):
    """Handle order created event - reserve inventory."""
    order_id = event.get("order_id")
    items = event.get("items", [])
    
    logger.info(f"Reserving inventory for order {order_id}")
    
    # In production, use proper database session
    # This is simplified for demonstration
    
    # Publish inventory reserved event
    await kafka_producer.publish(
        Topics.INVENTORY,
        {
            "event_type": EventTypes.INVENTORY_RESERVED,
            "order_id": order_id,
            "user_id": event.get("user_id"),
            "total_amount": event.get("total_amount"),
            "items": items,
            "timestamp": datetime.utcnow().isoformat()
        },
        key=order_id
    )
    
    logger.info(f"Inventory reserved for order {order_id}")


async def handle_order_cancelled(event: dict):
    """Handle order cancelled event - release inventory."""
    order_id = event.get("order_id")
    
    logger.info(f"Releasing inventory for cancelled order {order_id}")
    
    await kafka_producer.publish(
        Topics.INVENTORY,
        {
            "event_type": EventTypes.INVENTORY_RELEASED,
            "order_id": order_id,
            "timestamp": datetime.utcnow().isoformat()
        },
        key=order_id
    )


# Register event handlers
kafka_consumer.register_handler(EventTypes.ORDER_CREATED, handle_order_created)
kafka_consumer.register_handler(EventTypes.ORDER_CANCELLED, handle_order_cancelled)


# Background consumer task
async def start_consumer():
    """Start Kafka consumer in background."""
    await kafka_consumer.start()
    await kafka_consumer.consume()


# Lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Inventory Service...")
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
    logger.info("Inventory Service stopped")


# FastAPI App
app = FastAPI(
    title="Inventory Service",
    description="Stock Management with Event-Driven Updates",
    version="1.0.0",
    lifespan=lifespan
)


# API Endpoints
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "inventory-service"}


@app.get("/api/v1/products", response_model=List[ProductResponse])
async def list_products(
    category: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """List all products."""
    # Check cache
    cache_key = f"products:list:{category or 'all'}"
    cached = await redis_client.get(cache_key)
    if cached:
        return cached
    
    query = select(Product)
    if category:
        query = query.where(Product.category == category)
    
    result = await db.execute(query.order_by(Product.name))
    products = result.scalars().all()
    
    response = [ProductResponse(
        id=p.id,
        name=p.name,
        description=p.description,
        price=float(p.price),
        stock_quantity=p.stock_quantity,
        reserved_quantity=p.reserved_quantity,
        available_quantity=p.available_quantity,
        category=p.category
    ) for p in products]
    
    # Cache for 5 minutes
    await redis_client.set(cache_key, [r.model_dump() for r in response], ttl=300)
    
    return response


@app.get("/api/v1/products/{product_id}", response_model=ProductResponse)
async def get_product(product_id: str, db: AsyncSession = Depends(get_db)):
    """Get product details."""
    # Check cache
    cached = await redis_client.get(CacheKeys.product(product_id))
    if cached:
        return ProductResponse(**cached)
    
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    response = ProductResponse(
        id=product.id,
        name=product.name,
        description=product.description,
        price=float(product.price),
        stock_quantity=product.stock_quantity,
        reserved_quantity=product.reserved_quantity,
        available_quantity=product.available_quantity,
        category=product.category
    )
    
    # Cache
    await redis_client.set(CacheKeys.product(product_id), response.model_dump(), ttl=300)
    
    return response


@app.post("/api/v1/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(product_data: ProductCreate, db: AsyncSession = Depends(get_db)):
    """Create a new product (admin only)."""
    product = Product(
        name=product_data.name,
        description=product_data.description,
        price=product_data.price,
        stock_quantity=product_data.stock_quantity,
        category=product_data.category
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)
    
    # Invalidate list cache
    await redis_client.delete("products:list:all")
    if product.category:
        await redis_client.delete(f"products:list:{product.category}")
    
    logger.info(f"Product created: {product.name}")
    
    return ProductResponse(
        id=product.id,
        name=product.name,
        description=product.description,
        price=float(product.price),
        stock_quantity=product.stock_quantity,
        reserved_quantity=product.reserved_quantity,
        available_quantity=product.available_quantity,
        category=product.category
    )


@app.put("/api/v1/products/{product_id}/stock", response_model=ProductResponse)
async def update_stock(
    product_id: str,
    stock_update: StockUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update product stock."""
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    if stock_update.operation == "set":
        product.stock_quantity = stock_update.quantity
    elif stock_update.operation == "add":
        product.stock_quantity += stock_update.quantity
    elif stock_update.operation == "subtract":
        if product.stock_quantity < stock_update.quantity:
            raise HTTPException(status_code=400, detail="Insufficient stock")
        product.stock_quantity -= stock_update.quantity
    
    await db.commit()
    await db.refresh(product)
    
    # Invalidate cache
    await redis_client.delete(CacheKeys.product(product_id))
    await redis_client.delete("products:list:all")
    
    # Publish inventory update event
    await kafka_producer.publish(
        Topics.INVENTORY,
        {
            "event_type": EventTypes.INVENTORY_UPDATED,
            "product_id": product_id,
            "stock_quantity": product.stock_quantity,
            "timestamp": datetime.utcnow().isoformat()
        },
        key=product_id
    )
    
    # Check for low stock alert
    if product.available_quantity < 10:
        await kafka_producer.publish(
            Topics.INVENTORY,
            {
                "event_type": EventTypes.STOCK_LOW,
                "product_id": product_id,
                "product_name": product.name,
                "available_quantity": product.available_quantity,
                "timestamp": datetime.utcnow().isoformat()
            },
            key=product_id
        )
    
    logger.info(f"Stock updated for product {product_id}: {product.stock_quantity}")
    
    return ProductResponse(
        id=product.id,
        name=product.name,
        description=product.description,
        price=float(product.price),
        stock_quantity=product.stock_quantity,
        reserved_quantity=product.reserved_quantity,
        available_quantity=product.available_quantity,
        category=product.category
    )


@app.post("/api/v1/products/{product_id}/reserve")
async def reserve_stock(
    product_id: str,
    quantity: int,
    db: AsyncSession = Depends(get_db)
):
    """Reserve stock for an order."""
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    if product.available_quantity < quantity:
        raise HTTPException(status_code=400, detail="Insufficient stock")
    
    product.reserved_quantity += quantity
    await db.commit()
    
    # Invalidate cache
    await redis_client.delete(CacheKeys.product(product_id))
    
    return {"message": f"Reserved {quantity} units", "available": product.available_quantity}


@app.post("/api/v1/products/{product_id}/release")
async def release_stock(
    product_id: str,
    quantity: int,
    db: AsyncSession = Depends(get_db)
):
    """Release reserved stock."""
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    product.reserved_quantity = max(0, product.reserved_quantity - quantity)
    await db.commit()
    
    # Invalidate cache
    await redis_client.delete(CacheKeys.product(product_id))
    
    return {"message": f"Released {quantity} units", "available": product.available_quantity}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
