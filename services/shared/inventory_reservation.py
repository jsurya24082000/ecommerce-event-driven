"""
Inventory Reservation System with Consistency Guarantees.

This is the hardest scaling problem - preventing overselling.

Strategy:
1. Reservation + Expiry Model
   - Reserve stock with TTL (10 minutes)
   - Auto-release if payment fails/times out
   
2. Atomic Updates (Optimistic Concurrency)
   - UPDATE inventory SET available = available - x WHERE sku=? AND available>=x
   - Returns affected rows = 0 if insufficient stock
   
3. Idempotency
   - Each reservation has unique ID
   - Duplicate requests return existing reservation

Interview line: "Inventory is the consistency boundary; we use atomic updates + 
idempotent events to prevent oversell."
"""

import logging
import uuid
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class ReservationStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    RELEASED = "released"
    EXPIRED = "expired"


@dataclass
class StockReservation:
    """Stock reservation with TTL."""
    reservation_id: str
    order_id: str
    sku_id: str
    quantity: int
    status: ReservationStatus
    created_at: str
    expires_at: str
    confirmed_at: Optional[str] = None
    released_at: Optional[str] = None


class InventoryReservationService:
    """
    Manages inventory reservations with consistency guarantees.
    
    Uses Redis for fast reservation tracking with TTL,
    and Postgres for durable stock levels.
    """
    
    RESERVATION_TTL_SECONDS = 600  # 10 minutes
    
    def __init__(self, redis_client, db_session_factory):
        self.redis = redis_client
        self.db_factory = db_session_factory
    
    async def reserve_stock(
        self,
        order_id: str,
        items: List[Dict[str, Any]],
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Reserve stock for an order with atomic updates.
        
        Args:
            order_id: Order ID
            items: List of {sku_id, quantity}
            idempotency_key: Optional key for idempotent requests
            
        Returns:
            Reservation result with status and reservation IDs
        """
        # Idempotency check
        if idempotency_key:
            existing = await self._get_existing_reservation(idempotency_key)
            if existing:
                logger.info(f"Returning existing reservation for {idempotency_key}")
                return existing
        
        reservation_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self.RESERVATION_TTL_SECONDS)
        
        reservations = []
        failed_items = []
        
        async with self.db_factory() as session:
            for item in items:
                sku_id = item["sku_id"]
                quantity = item["quantity"]
                
                # Atomic update with optimistic concurrency
                success = await self._atomic_reserve(
                    session, sku_id, quantity, reservation_id
                )
                
                if success:
                    reservation = StockReservation(
                        reservation_id=f"{reservation_id}:{sku_id}",
                        order_id=order_id,
                        sku_id=sku_id,
                        quantity=quantity,
                        status=ReservationStatus.PENDING,
                        created_at=now.isoformat(),
                        expires_at=expires_at.isoformat()
                    )
                    reservations.append(reservation)
                    
                    # Store in Redis with TTL for expiry tracking
                    await self._store_reservation(reservation)
                else:
                    failed_items.append({
                        "sku_id": sku_id,
                        "requested": quantity,
                        "reason": "insufficient_stock"
                    })
            
            if failed_items:
                # Rollback successful reservations
                await self._rollback_reservations(session, reservations)
                await session.rollback()
                
                return {
                    "success": False,
                    "reservation_id": None,
                    "failed_items": failed_items,
                    "message": "Insufficient stock for some items"
                }
            
            await session.commit()
        
        # Store idempotency key
        if idempotency_key:
            await self._store_idempotency(idempotency_key, reservation_id)
        
        logger.info(
            f"Reserved stock for order {order_id}: "
            f"{len(reservations)} items, expires at {expires_at}"
        )
        
        return {
            "success": True,
            "reservation_id": reservation_id,
            "reservations": [asdict(r) for r in reservations],
            "expires_at": expires_at.isoformat()
        }
    
    async def _atomic_reserve(
        self,
        session,
        sku_id: str,
        quantity: int,
        reservation_id: str
    ) -> bool:
        """
        Atomic stock reservation using optimistic concurrency.
        
        SQL: UPDATE inventory 
             SET available = available - :qty, reserved = reserved + :qty
             WHERE sku_id = :sku AND available >= :qty
             
        Returns True if update succeeded (affected rows > 0)
        """
        # This would be actual SQLAlchemy in production
        query = """
            UPDATE inventory 
            SET available_quantity = available_quantity - :quantity,
                reserved_quantity = reserved_quantity + :quantity,
                updated_at = NOW()
            WHERE sku_id = :sku_id 
            AND available_quantity >= :quantity
        """
        
        try:
            result = await session.execute(
                query,
                {"sku_id": sku_id, "quantity": quantity}
            )
            return result.rowcount > 0
        except Exception as e:
            logger.error(f"Atomic reserve failed for {sku_id}: {e}")
            return False
    
    async def confirm_reservation(self, reservation_id: str) -> bool:
        """
        Confirm reservation after successful payment.
        Moves reserved stock to sold.
        """
        reservations = await self._get_reservations(reservation_id)
        
        if not reservations:
            logger.warning(f"No reservations found for {reservation_id}")
            return False
        
        async with self.db_factory() as session:
            for res in reservations:
                if res["status"] != ReservationStatus.PENDING:
                    continue
                
                # Move from reserved to sold
                query = """
                    UPDATE inventory 
                    SET reserved_quantity = reserved_quantity - :quantity,
                        sold_quantity = sold_quantity + :quantity,
                        updated_at = NOW()
                    WHERE sku_id = :sku_id
                """
                await session.execute(
                    query,
                    {"sku_id": res["sku_id"], "quantity": res["quantity"]}
                )
                
                # Update reservation status
                await self._update_reservation_status(
                    res["reservation_id"],
                    ReservationStatus.CONFIRMED
                )
            
            await session.commit()
        
        logger.info(f"Confirmed reservation {reservation_id}")
        return True
    
    async def release_reservation(
        self,
        reservation_id: str,
        reason: str = "cancelled"
    ) -> bool:
        """
        Release reservation (payment failed, order cancelled, or expired).
        Returns reserved stock to available.
        """
        reservations = await self._get_reservations(reservation_id)
        
        if not reservations:
            logger.warning(f"No reservations found for {reservation_id}")
            return False
        
        async with self.db_factory() as session:
            for res in reservations:
                if res["status"] not in [ReservationStatus.PENDING]:
                    continue
                
                # Return reserved stock to available
                query = """
                    UPDATE inventory 
                    SET reserved_quantity = reserved_quantity - :quantity,
                        available_quantity = available_quantity + :quantity,
                        updated_at = NOW()
                    WHERE sku_id = :sku_id
                """
                await session.execute(
                    query,
                    {"sku_id": res["sku_id"], "quantity": res["quantity"]}
                )
                
                # Update reservation status
                status = (
                    ReservationStatus.EXPIRED if reason == "expired"
                    else ReservationStatus.RELEASED
                )
                await self._update_reservation_status(
                    res["reservation_id"], status
                )
            
            await session.commit()
        
        logger.info(f"Released reservation {reservation_id}: {reason}")
        return True
    
    async def _store_reservation(self, reservation: StockReservation):
        """Store reservation in Redis with TTL."""
        key = f"reservation:{reservation.reservation_id}"
        await self.redis.setex(
            key,
            self.RESERVATION_TTL_SECONDS,
            asdict(reservation)
        )
        
        # Add to order's reservation set
        await self.redis.sadd(
            f"order_reservations:{reservation.order_id}",
            reservation.reservation_id
        )
    
    async def _get_reservations(self, reservation_id: str) -> List[Dict]:
        """Get all reservations for a reservation ID."""
        # In production, query Redis or DB
        return []
    
    async def _get_existing_reservation(self, idempotency_key: str) -> Optional[Dict]:
        """Check for existing reservation by idempotency key."""
        key = f"idempotency:{idempotency_key}"
        return await self.redis.get(key)
    
    async def _store_idempotency(self, idempotency_key: str, reservation_id: str):
        """Store idempotency mapping."""
        key = f"idempotency:{idempotency_key}"
        await self.redis.setex(key, 3600, reservation_id)  # 1 hour TTL
    
    async def _update_reservation_status(
        self,
        reservation_id: str,
        status: ReservationStatus
    ):
        """Update reservation status in Redis."""
        key = f"reservation:{reservation_id}"
        # Update status field
        pass
    
    async def _rollback_reservations(self, session, reservations: List[StockReservation]):
        """Rollback reservations on partial failure."""
        for res in reservations:
            query = """
                UPDATE inventory 
                SET available_quantity = available_quantity + :quantity,
                    reserved_quantity = reserved_quantity - :quantity
                WHERE sku_id = :sku_id
            """
            await session.execute(
                query,
                {"sku_id": res.sku_id, "quantity": res.quantity}
            )


class ReservationExpiryWorker:
    """
    Background worker to expire stale reservations.
    Runs periodically to release reservations that weren't confirmed.
    """
    
    def __init__(self, reservation_service: InventoryReservationService):
        self.service = reservation_service
        self._running = False
    
    async def start(self, interval_seconds: int = 60):
        """Start the expiry worker."""
        self._running = True
        logger.info("Reservation expiry worker started")
        
        while self._running:
            try:
                await self._process_expired_reservations()
            except Exception as e:
                logger.error(f"Expiry worker error: {e}")
            
            await asyncio.sleep(interval_seconds)
    
    async def stop(self):
        """Stop the expiry worker."""
        self._running = False
        logger.info("Reservation expiry worker stopped")
    
    async def _process_expired_reservations(self):
        """Find and release expired reservations."""
        # In production, scan Redis for expired keys or use sorted set
        # with expiry timestamps
        pass
