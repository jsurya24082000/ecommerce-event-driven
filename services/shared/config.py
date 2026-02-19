"""
Shared configuration for all microservices.
"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Service Info
    service_name: str = "ecommerce-service"
    debug: bool = False
    
    # Database
    database_url: str = "postgresql+asyncpg://ecommerce:ecommerce123@localhost:5432/ecommerce"
    
    # Redis
    redis_url: str = "redis://localhost:6379/0"
    
    # Kafka
    kafka_bootstrap_servers: str = "localhost:29092"
    
    # JWT
    jwt_secret: str = "your-super-secret-jwt-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60
    
    # Service URLs
    user_service_url: str = "http://localhost:8001"
    order_service_url: str = "http://localhost:8002"
    inventory_service_url: str = "http://localhost:8003"
    payment_service_url: str = "http://localhost:8004"
    
    class Config:
        env_file = ".env"
        extra = "allow"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
