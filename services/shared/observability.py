"""
Observability Module - Metrics, Tracing, and Logging.

Amazon-style operational excellence:
- Prometheus metrics for dashboards
- Distributed tracing with correlation IDs
- Structured logging for debugging

Metrics to track:
- p95 latency per endpoint
- Kafka lag per consumer group
- DB query p95
- Redis hit rate
- Order success rate, payment failure rate
- Inventory oversell incidents (should be 0)
"""

import time
import logging
import uuid
import functools
from typing import Optional, Dict, Any, Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from prometheus_client import Counter, Histogram, Gauge, Summary

# =============================================================================
# PROMETHEUS METRICS
# =============================================================================

# Request metrics
REQUEST_COUNT = Counter(
    'ecommerce_requests_total',
    'Total requests',
    ['service', 'endpoint', 'method', 'status']
)

REQUEST_LATENCY = Histogram(
    'ecommerce_request_latency_seconds',
    'Request latency in seconds',
    ['service', 'endpoint'],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

# Kafka metrics
KAFKA_MESSAGES_PRODUCED = Counter(
    'ecommerce_kafka_messages_produced_total',
    'Total Kafka messages produced',
    ['service', 'topic']
)

KAFKA_MESSAGES_CONSUMED = Counter(
    'ecommerce_kafka_messages_consumed_total',
    'Total Kafka messages consumed',
    ['service', 'topic', 'consumer_group']
)

KAFKA_CONSUMER_LAG = Gauge(
    'ecommerce_kafka_consumer_lag',
    'Kafka consumer lag',
    ['service', 'topic', 'partition', 'consumer_group']
)

KAFKA_PROCESSING_TIME = Histogram(
    'ecommerce_kafka_processing_seconds',
    'Kafka message processing time',
    ['service', 'event_type'],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

# Database metrics
DB_QUERY_LATENCY = Histogram(
    'ecommerce_db_query_latency_seconds',
    'Database query latency',
    ['service', 'operation'],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)

DB_CONNECTIONS_ACTIVE = Gauge(
    'ecommerce_db_connections_active',
    'Active database connections',
    ['service']
)

# Redis metrics
REDIS_OPERATIONS = Counter(
    'ecommerce_redis_operations_total',
    'Total Redis operations',
    ['service', 'operation', 'status']
)

REDIS_HIT_RATE = Gauge(
    'ecommerce_redis_hit_rate',
    'Redis cache hit rate',
    ['service']
)

REDIS_LATENCY = Histogram(
    'ecommerce_redis_latency_seconds',
    'Redis operation latency',
    ['service', 'operation'],
    buckets=[0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1]
)

# Business metrics
ORDER_COUNT = Counter(
    'ecommerce_orders_total',
    'Total orders',
    ['status']  # created, confirmed, cancelled, completed
)

PAYMENT_COUNT = Counter(
    'ecommerce_payments_total',
    'Total payments',
    ['status']  # initiated, completed, failed, refunded
)

INVENTORY_RESERVATIONS = Counter(
    'ecommerce_inventory_reservations_total',
    'Total inventory reservations',
    ['status']  # reserved, confirmed, released, expired
)

INVENTORY_OVERSELL_INCIDENTS = Counter(
    'ecommerce_inventory_oversell_incidents_total',
    'Inventory oversell incidents (should be 0)',
    ['sku_id']
)

# =============================================================================
# DISTRIBUTED TRACING
# =============================================================================

class TraceContext:
    """Context for distributed tracing."""
    
    def __init__(
        self,
        correlation_id: Optional[str] = None,
        span_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        service_name: str = "unknown"
    ):
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self.span_id = span_id or str(uuid.uuid4())[:8]
        self.parent_span_id = parent_span_id
        self.service_name = service_name
        self.start_time = time.time()
        self.attributes: Dict[str, Any] = {}
    
    def add_attribute(self, key: str, value: Any):
        """Add attribute to trace."""
        self.attributes[key] = value
    
    def to_headers(self) -> Dict[str, str]:
        """Convert to HTTP/Kafka headers."""
        return {
            "X-Correlation-ID": self.correlation_id,
            "X-Span-ID": self.span_id,
            "X-Parent-Span-ID": self.parent_span_id or "",
            "X-Service-Name": self.service_name
        }
    
    @classmethod
    def from_headers(cls, headers: Dict[str, str], service_name: str) -> "TraceContext":
        """Create context from incoming headers."""
        return cls(
            correlation_id=headers.get("X-Correlation-ID"),
            parent_span_id=headers.get("X-Span-ID"),
            service_name=service_name
        )
    
    def child_span(self, operation: str) -> "TraceContext":
        """Create child span."""
        return TraceContext(
            correlation_id=self.correlation_id,
            parent_span_id=self.span_id,
            service_name=self.service_name
        )


# =============================================================================
# STRUCTURED LOGGING
# =============================================================================

class StructuredLogger:
    """Structured JSON logger with trace context."""
    
    def __init__(self, service_name: str):
        self.service_name = service_name
        self.logger = logging.getLogger(service_name)
    
    def _format_log(
        self,
        level: str,
        message: str,
        trace_ctx: Optional[TraceContext] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Format log entry as structured JSON."""
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "service": self.service_name,
            "message": message,
        }
        
        if trace_ctx:
            log_entry["correlation_id"] = trace_ctx.correlation_id
            log_entry["span_id"] = trace_ctx.span_id
            if trace_ctx.parent_span_id:
                log_entry["parent_span_id"] = trace_ctx.parent_span_id
        
        log_entry.update(kwargs)
        return log_entry
    
    def info(self, message: str, trace_ctx: Optional[TraceContext] = None, **kwargs):
        self.logger.info(self._format_log("INFO", message, trace_ctx, **kwargs))
    
    def warning(self, message: str, trace_ctx: Optional[TraceContext] = None, **kwargs):
        self.logger.warning(self._format_log("WARNING", message, trace_ctx, **kwargs))
    
    def error(self, message: str, trace_ctx: Optional[TraceContext] = None, **kwargs):
        self.logger.error(self._format_log("ERROR", message, trace_ctx, **kwargs))
    
    def debug(self, message: str, trace_ctx: Optional[TraceContext] = None, **kwargs):
        self.logger.debug(self._format_log("DEBUG", message, trace_ctx, **kwargs))


# =============================================================================
# DECORATORS FOR INSTRUMENTATION
# =============================================================================

def track_request(service: str, endpoint: str):
    """Decorator to track request metrics."""
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status = "success"
            
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                raise
            finally:
                latency = time.time() - start_time
                REQUEST_LATENCY.labels(service=service, endpoint=endpoint).observe(latency)
                REQUEST_COUNT.labels(
                    service=service,
                    endpoint=endpoint,
                    method="POST",
                    status=status
                ).inc()
        
        return wrapper
    return decorator


def track_db_query(service: str, operation: str):
    """Decorator to track database query metrics."""
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                latency = time.time() - start_time
                DB_QUERY_LATENCY.labels(service=service, operation=operation).observe(latency)
        
        return wrapper
    return decorator


def track_kafka_processing(service: str, event_type: str):
    """Decorator to track Kafka message processing."""
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                latency = time.time() - start_time
                KAFKA_PROCESSING_TIME.labels(
                    service=service,
                    event_type=event_type
                ).observe(latency)
        
        return wrapper
    return decorator


@contextmanager
def track_operation(service: str, operation: str, metric: Histogram):
    """Context manager for tracking operation latency."""
    start_time = time.time()
    try:
        yield
    finally:
        latency = time.time() - start_time
        metric.labels(service=service, operation=operation).observe(latency)
