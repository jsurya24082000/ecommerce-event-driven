"""
Payment Service - Mock Payment Processing with Event-Driven Updates
"""

import logging
import asyncio
import random
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from uuid import uuid4
from decimal import Decimal
from enum import Enum

from fastapi import FastAPI, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import Column, String, DateTime, Numeric, select
from sqlalchemy.ext.asyncio import AsyncSession

import sys
sys.path.append('..')
from shared.config import get_settings
from shared.database import Base, get_db, init_db
from shared.kafka_client import KafkaProducer, KafkaConsumer, EventTypes, Topics
from shared.redis_client import RedisClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

# Kafka and Redis clients
kafka_producer = KafkaProducer()
kafka_consumer = KafkaConsumer(
    topics=[Topics.PAYMENTS],
    group_id="payment-service"
)
redis_client = RedisClient()


# Enums
class PaymentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class PaymentMethod(str, Enum):
    CREDIT_CARD = "credit_card"
    DEBIT_CARD = "debit_card"
    PAYPAL = "paypal"
    BANK_TRANSFER = "bank_transfer"


# Database Models
class Payment(Base):
    __tablename__ = "payments"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    order_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    amount = Column(Numeric(10, 2), nullable=False)
    status = Column(String(50), default=PaymentStatus.PENDING)
    payment_method = Column(String(50), default=PaymentMethod.CREDIT_CARD)
    transaction_id = Column(String(100))  # External payment gateway ID
    error_message = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# Pydantic Schemas
class PaymentCreate(BaseModel):
    order_id: str
    amount: float
    payment_method: str = "credit_card"
    card_number: Optional[str] = None  # Mock - don't store real card numbers!
    card_expiry: Optional[str] = None
    card_cvv: Optional[str] = None


class PaymentResponse(BaseModel):
    id: str
    order_id: str
    user_id: str
    amount: float
    status: str
    payment_method: str
    transaction_id: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True


class RefundRequest(BaseModel):
    reason: Optional[str] = None


# Mock Payment Gateway
class MockPaymentGateway:
    """Simulates external payment gateway."""
    
    @staticmethod
    async def process_payment(amount: float, payment_method: str) -> dict:
        """Process payment (mock implementation)."""
        # Simulate processing delay
        await asyncio.sleep(random.uniform(0.5, 2.0))
        
        # 95% success rate
        success = random.random() < 0.95
        
        if success:
            return {
                "success": True,
                "transaction_id": f"TXN-{uuid4().hex[:12].upper()}",
                "message": "Payment processed successfully"
            }
        else:
            return {
                "success": False,
                "transaction_id": None,
                "message": random.choice([
                    "Card declined",
                    "Insufficient funds",
                    "Payment gateway timeout",
                    "Invalid card details"
                ])
            }
    
    @staticmethod
    async def process_refund(transaction_id: str, amount: float) -> dict:
        """Process refund (mock implementation)."""
        await asyncio.sleep(random.uniform(0.3, 1.0))
        
        # 98% success rate for refunds
        success = random.random() < 0.98
        
        if success:
            return {
                "success": True,
                "refund_id": f"REF-{uuid4().hex[:12].upper()}",
                "message": "Refund processed successfully"
            }
        else:
            return {
                "success": False,
                "refund_id": None,
                "message": "Refund processing failed"
            }


payment_gateway = MockPaymentGateway()


# Event Handlers
async def handle_payment_initiated(event: dict):
    """Handle payment initiated event from order service."""
    order_id = event.get("order_id")
    amount = event.get("amount")
    user_id = event.get("user_id")
    
    logger.info(f"Processing payment for order {order_id}, amount: ${amount}")
    
    # Process payment
    result = await payment_gateway.process_payment(amount, "credit_card")
    
    if result["success"]:
        # Publish payment completed
        await kafka_producer.publish(
            Topics.PAYMENTS,
            {
                "event_type": EventTypes.PAYMENT_COMPLETED,
                "order_id": order_id,
                "user_id": user_id,
                "amount": amount,
                "transaction_id": result["transaction_id"],
                "timestamp": datetime.utcnow().isoformat()
            },
            key=order_id
        )
        logger.info(f"Payment completed for order {order_id}")
    else:
        # Publish payment failed
        await kafka_producer.publish(
            Topics.PAYMENTS,
            {
                "event_type": EventTypes.PAYMENT_FAILED,
                "order_id": order_id,
                "user_id": user_id,
                "amount": amount,
                "error": result["message"],
                "timestamp": datetime.utcnow().isoformat()
            },
            key=order_id
        )
        logger.info(f"Payment failed for order {order_id}: {result['message']}")


# Register event handlers
kafka_consumer.register_handler(EventTypes.PAYMENT_INITIATED, handle_payment_initiated)


# Background consumer task
async def start_consumer():
    """Start Kafka consumer in background."""
    await kafka_consumer.start()
    await kafka_consumer.consume()


# Lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Payment Service...")
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
    logger.info("Payment Service stopped")


# FastAPI App
app = FastAPI(
    title="Payment Service",
    description="Mock Payment Processing with Event-Driven Updates",
    version="1.0.0",
    lifespan=lifespan
)


# Mock auth dependency
async def get_current_user_id() -> str:
    return "user-123"


# API Endpoints
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "payment-service"}


@app.post("/api/v1/payments", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
async def create_payment(
    payment_data: PaymentCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Process a payment."""
    # Create payment record
    payment = Payment(
        order_id=payment_data.order_id,
        user_id=user_id,
        amount=payment_data.amount,
        payment_method=payment_data.payment_method,
        status=PaymentStatus.PROCESSING
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    
    # Process payment
    result = await payment_gateway.process_payment(
        payment_data.amount,
        payment_data.payment_method
    )
    
    if result["success"]:
        payment.status = PaymentStatus.COMPLETED
        payment.transaction_id = result["transaction_id"]
        
        # Publish success event
        await kafka_producer.publish(
            Topics.PAYMENTS,
            {
                "event_type": EventTypes.PAYMENT_COMPLETED,
                "payment_id": payment.id,
                "order_id": payment.order_id,
                "user_id": user_id,
                "amount": float(payment.amount),
                "transaction_id": payment.transaction_id,
                "timestamp": datetime.utcnow().isoformat()
            },
            key=payment.order_id
        )
    else:
        payment.status = PaymentStatus.FAILED
        payment.error_message = result["message"]
        
        # Publish failure event
        await kafka_producer.publish(
            Topics.PAYMENTS,
            {
                "event_type": EventTypes.PAYMENT_FAILED,
                "payment_id": payment.id,
                "order_id": payment.order_id,
                "user_id": user_id,
                "error": result["message"],
                "timestamp": datetime.utcnow().isoformat()
            },
            key=payment.order_id
        )
    
    await db.commit()
    await db.refresh(payment)
    
    logger.info(f"Payment {payment.id}: {payment.status}")
    
    if payment.status == PaymentStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=payment.error_message
        )
    
    return PaymentResponse(
        id=payment.id,
        order_id=payment.order_id,
        user_id=payment.user_id,
        amount=float(payment.amount),
        status=payment.status,
        payment_method=payment.payment_method,
        transaction_id=payment.transaction_id,
        created_at=payment.created_at
    )


@app.get("/api/v1/payments/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get payment details."""
    result = await db.execute(
        select(Payment).where(Payment.id == payment_id, Payment.user_id == user_id)
    )
    payment = result.scalar_one_or_none()
    
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    return PaymentResponse(
        id=payment.id,
        order_id=payment.order_id,
        user_id=payment.user_id,
        amount=float(payment.amount),
        status=payment.status,
        payment_method=payment.payment_method,
        transaction_id=payment.transaction_id,
        created_at=payment.created_at
    )


@app.post("/api/v1/payments/{payment_id}/refund", response_model=PaymentResponse)
async def refund_payment(
    payment_id: str,
    refund_request: RefundRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Refund a payment."""
    result = await db.execute(
        select(Payment).where(Payment.id == payment_id, Payment.user_id == user_id)
    )
    payment = result.scalar_one_or_none()
    
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    if payment.status != PaymentStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot refund payment in {payment.status} status"
        )
    
    # Process refund
    result = await payment_gateway.process_refund(
        payment.transaction_id,
        float(payment.amount)
    )
    
    if result["success"]:
        payment.status = PaymentStatus.REFUNDED
        
        # Publish refund event
        await kafka_producer.publish(
            Topics.PAYMENTS,
            {
                "event_type": EventTypes.PAYMENT_REFUNDED,
                "payment_id": payment.id,
                "order_id": payment.order_id,
                "user_id": user_id,
                "amount": float(payment.amount),
                "refund_id": result["refund_id"],
                "reason": refund_request.reason,
                "timestamp": datetime.utcnow().isoformat()
            },
            key=payment.order_id
        )
        
        await db.commit()
        await db.refresh(payment)
        
        logger.info(f"Payment {payment_id} refunded")
    else:
        raise HTTPException(
            status_code=500,
            detail="Refund processing failed"
        )
    
    return PaymentResponse(
        id=payment.id,
        order_id=payment.order_id,
        user_id=payment.user_id,
        amount=float(payment.amount),
        status=payment.status,
        payment_method=payment.payment_method,
        transaction_id=payment.transaction_id,
        created_at=payment.created_at
    )


@app.get("/api/v1/payments/order/{order_id}", response_model=PaymentResponse)
async def get_payment_by_order(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get payment for an order."""
    result = await db.execute(
        select(Payment).where(Payment.order_id == order_id, Payment.user_id == user_id)
    )
    payment = result.scalar_one_or_none()
    
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    return PaymentResponse(
        id=payment.id,
        order_id=payment.order_id,
        user_id=payment.user_id,
        amount=float(payment.amount),
        status=payment.status,
        payment_method=payment.payment_method,
        transaction_id=payment.transaction_id,
        created_at=payment.created_at
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
