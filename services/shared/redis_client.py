"""
Redis caching utilities.
"""

import json
import logging
from typing import Any, Optional
import redis.asyncio as redis
from .config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class RedisClient:
    """Async Redis client for caching."""
    
    def __init__(self):
        self._client: Optional[redis.Redis] = None
        
    async def connect(self):
        """Connect to Redis."""
        self._client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True
        )
        await self._client.ping()
        logger.info("Redis connected")
        
    async def disconnect(self):
        """Disconnect from Redis."""
        if self._client:
            await self._client.close()
            logger.info("Redis disconnected")
            
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        if not self._client:
            return None
        value = await self._client.get(key)
        if value:
            return json.loads(value)
        return None
        
    async def set(self, key: str, value: Any, ttl: int = 3600):
        """Set value in cache with TTL."""
        if not self._client:
            return
        await self._client.setex(key, ttl, json.dumps(value))
        
    async def delete(self, key: str):
        """Delete key from cache."""
        if not self._client:
            return
        await self._client.delete(key)
        
    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        if not self._client:
            return False
        return await self._client.exists(key) > 0
        
    async def incr(self, key: str) -> int:
        """Increment counter."""
        if not self._client:
            return 0
        return await self._client.incr(key)
        
    async def expire(self, key: str, ttl: int):
        """Set expiration on key."""
        if not self._client:
            return
        await self._client.expire(key, ttl)


# Cache key patterns
class CacheKeys:
    USER = "user:{user_id}"
    USER_SESSION = "session:{token}"
    PRODUCT = "product:{product_id}"
    PRODUCT_LIST = "products:list"
    ORDER = "order:{order_id}"
    INVENTORY = "inventory:{product_id}"
    RATE_LIMIT = "rate_limit:{ip}:{endpoint}"
    
    @staticmethod
    def user(user_id: str) -> str:
        return f"user:{user_id}"
    
    @staticmethod
    def product(product_id: str) -> str:
        return f"product:{product_id}"
    
    @staticmethod
    def order(order_id: str) -> str:
        return f"order:{order_id}"
    
    @staticmethod
    def inventory(product_id: str) -> str:
        return f"inventory:{product_id}"
