"""
Production-Grade Prometheus Metrics for E-commerce Event-Driven System.

Amazon SDE-level observability covering:
1. HTTP request latency (p50, p95, p99)
2. Error rate per service/endpoint
3. In-flight requests
4. Kafka processing time per message
5. Dead-letter queue count
6. End-to-end order completion latency

Why these metrics matter:
- p95/p99 latency: Catches tail latency issues that affect user experience
- Error rate: Immediate signal for service degradation
- In-flight requests: Detects backpressure and capacity issues
- Kafka processing time: Identifies slow consumers causing lag
- DLQ count: Tracks poison messages requiring investigation
- E2E latency: Business-critical SLA metric
"""

import time
import functools
from typing import Optional, Dict, Any, Callable
from contextlib import contextmanager
from prometheus_client import Counter, Histogram, Gauge, Summary, Info
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST


# =============================================================================
# HTTP REQUEST METRICS
# =============================================================================

# Request latency with percentile-friendly buckets
# Buckets chosen to capture p50 (~50ms), p95 (~200ms), p99 (~500ms)
HTTP_REQUEST_LATENCY = Histogram(
    'http_request_duration_seconds',
    'HTTP request latency in seconds',
    ['service', 'endpoint', 'method', 'status_code'],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.0, 2.5, 5.0, 10.0]
)

# Total request count for calculating error rate
HTTP_REQUEST_TOTAL = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['service', 'endpoint', 'method', 'status_code']
)

# In-flight requests - critical for detecting backpressure
# High values indicate the service is struggling to keep up
HTTP_IN_FLIGHT_REQUESTS = Gauge(
    'http_in_flight_requests',
    'Number of HTTP requests currently being processed',
    ['service']
)

# Error counter for quick error rate calculation
HTTP_ERRORS_TOTAL = Counter(
    'http_errors_total',
    'Total HTTP errors (4xx and 5xx)',
    ['service', 'endpoint', 'error_type']  # error_type: client_error, server_error
)


# =============================================================================
# KAFKA METRICS
# =============================================================================

# Message processing time - identifies slow consumers
KAFKA_MESSAGE_PROCESSING_SECONDS = Histogram(
    'kafka_message_processing_seconds',
    'Time to process a single Kafka message',
    ['service', 'topic', 'event_type'],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

# Consumer lag - critical for detecting falling behind
# High lag = consumers can't keep up with producers
KAFKA_CONSUMER_LAG = Gauge(
    'kafka_consumer_lag_messages',
    'Number of messages consumer is behind',
    ['service', 'topic', 'partition', 'consumer_group']
)

# Dead-letter queue count - poison messages requiring investigation
# Non-zero values require immediate attention
KAFKA_DLQ_MESSAGES_TOTAL = Counter(
    'kafka_dlq_messages_total',
    'Messages sent to dead-letter queue',
    ['service', 'topic', 'error_reason']
)

KAFKA_DLQ_CURRENT_SIZE = Gauge(
    'kafka_dlq_current_size',
    'Current number of messages in DLQ',
    ['service', 'topic']
)

# Message throughput
KAFKA_MESSAGES_PRODUCED_TOTAL = Counter(
    'kafka_messages_produced_total',
    'Total messages produced to Kafka',
    ['service', 'topic']
)

KAFKA_MESSAGES_CONSUMED_TOTAL = Counter(
    'kafka_messages_consumed_total',
    'Total messages consumed from Kafka',
    ['service', 'topic', 'consumer_group']
)

# Duplicate detection - idempotency tracking
KAFKA_DUPLICATE_MESSAGES_TOTAL = Counter(
    'kafka_duplicate_messages_total',
    'Duplicate messages detected and skipped',
    ['service', 'topic', 'event_type']
)


# =============================================================================
# END-TO-END WORKFLOW METRICS
# =============================================================================

# Order completion latency - business-critical SLA metric
# Measures time from order.created to order.confirmed
ORDER_E2E_LATENCY_SECONDS = Histogram(
    'order_e2e_latency_seconds',
    'End-to-end order completion latency (created to confirmed)',
    ['order_type'],  # standard, express, etc.
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0]
)

# Order state transitions
ORDER_STATE_TRANSITIONS_TOTAL = Counter(
    'order_state_transitions_total',
    'Order state transitions',
    ['from_state', 'to_state']
)

# Order completion rate
ORDER_COMPLETION_TOTAL = Counter(
    'order_completion_total',
    'Total orders by final status',
    ['status']  # confirmed, cancelled, failed
)


# =============================================================================
# INVENTORY METRICS
# =============================================================================

# Oversell incidents - MUST be zero in production
# Any non-zero value is a critical bug
INVENTORY_OVERSELL_INCIDENTS = Counter(
    'inventory_oversell_incidents_total',
    'Inventory oversell incidents (should always be 0)',
    ['sku_id', 'warehouse']
)

# Reservation metrics
INVENTORY_RESERVATIONS_TOTAL = Counter(
    'inventory_reservations_total',
    'Total inventory reservations',
    ['status']  # reserved, confirmed, released, expired
)

# Stock levels
INVENTORY_STOCK_LEVEL = Gauge(
    'inventory_stock_level',
    'Current stock level',
    ['sku_id', 'warehouse']
)

INVENTORY_RESERVED_STOCK = Gauge(
    'inventory_reserved_stock',
    'Currently reserved stock',
    ['sku_id', 'warehouse']
)


# =============================================================================
# PAYMENT METRICS
# =============================================================================

PAYMENT_PROCESSING_SECONDS = Histogram(
    'payment_processing_seconds',
    'Payment processing latency',
    ['payment_method', 'status'],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0]
)

PAYMENT_TOTAL = Counter(
    'payment_total',
    'Total payments by status',
    ['status', 'payment_method']  # success, failed, timeout, declined
)

PAYMENT_AMOUNT_TOTAL = Counter(
    'payment_amount_total_cents',
    'Total payment amount in cents',
    ['status', 'currency']
)


# =============================================================================
# DATABASE METRICS
# =============================================================================

DB_QUERY_LATENCY_SECONDS = Histogram(
    'db_query_latency_seconds',
    'Database query latency',
    ['service', 'operation', 'table'],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)

DB_CONNECTIONS_ACTIVE = Gauge(
    'db_connections_active',
    'Active database connections',
    ['service', 'pool']
)

DB_CONNECTIONS_WAITING = Gauge(
    'db_connections_waiting',
    'Requests waiting for database connection',
    ['service', 'pool']
)


# =============================================================================
# REDIS METRICS
# =============================================================================

REDIS_OPERATION_LATENCY_SECONDS = Histogram(
    'redis_operation_latency_seconds',
    'Redis operation latency',
    ['service', 'operation'],
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1]
)

REDIS_CACHE_HITS_TOTAL = Counter(
    'redis_cache_hits_total',
    'Redis cache hits',
    ['service', 'cache_name']
)

REDIS_CACHE_MISSES_TOTAL = Counter(
    'redis_cache_misses_total',
    'Redis cache misses',
    ['service', 'cache_name']
)


# =============================================================================
# CIRCUIT BREAKER METRICS
# =============================================================================

CIRCUIT_BREAKER_STATE = Gauge(
    'circuit_breaker_state',
    'Circuit breaker state (0=closed, 1=open, 2=half-open)',
    ['service', 'dependency']
)

CIRCUIT_BREAKER_FAILURES_TOTAL = Counter(
    'circuit_breaker_failures_total',
    'Circuit breaker failure count',
    ['service', 'dependency']
)


# =============================================================================
# SERVICE INFO
# =============================================================================

SERVICE_INFO = Info(
    'service',
    'Service information'
)


# =============================================================================
# INSTRUMENTATION DECORATORS
# =============================================================================

def track_http_request(service: str, endpoint: str, method: str = "POST"):
    """
    Decorator to track HTTP request metrics.
    
    Tracks:
    - Request latency (histogram for percentiles)
    - Request count
    - In-flight requests
    - Error rate
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            HTTP_IN_FLIGHT_REQUESTS.labels(service=service).inc()
            start_time = time.time()
            status_code = "200"
            
            try:
                result = await func(*args, **kwargs)
                # Extract status code if available
                if hasattr(result, 'status_code'):
                    status_code = str(result.status_code)
                return result
            except Exception as e:
                status_code = "500"
                error_type = "server_error"
                HTTP_ERRORS_TOTAL.labels(
                    service=service,
                    endpoint=endpoint,
                    error_type=error_type
                ).inc()
                raise
            finally:
                latency = time.time() - start_time
                HTTP_REQUEST_LATENCY.labels(
                    service=service,
                    endpoint=endpoint,
                    method=method,
                    status_code=status_code
                ).observe(latency)
                HTTP_REQUEST_TOTAL.labels(
                    service=service,
                    endpoint=endpoint,
                    method=method,
                    status_code=status_code
                ).inc()
                HTTP_IN_FLIGHT_REQUESTS.labels(service=service).dec()
        
        return wrapper
    return decorator


def track_kafka_message(service: str, topic: str):
    """
    Decorator to track Kafka message processing.
    
    Tracks:
    - Processing time
    - Message count
    - Errors (sent to DLQ)
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(message: Dict[str, Any], *args, **kwargs):
            event_type = message.get("event_type", "unknown")
            start_time = time.time()
            
            try:
                result = await func(message, *args, **kwargs)
                KAFKA_MESSAGES_CONSUMED_TOTAL.labels(
                    service=service,
                    topic=topic,
                    consumer_group=f"{service}-group"
                ).inc()
                return result
            except Exception as e:
                KAFKA_DLQ_MESSAGES_TOTAL.labels(
                    service=service,
                    topic=topic,
                    error_reason=type(e).__name__
                ).inc()
                raise
            finally:
                latency = time.time() - start_time
                KAFKA_MESSAGE_PROCESSING_SECONDS.labels(
                    service=service,
                    topic=topic,
                    event_type=event_type
                ).observe(latency)
        
        return wrapper
    return decorator


def track_db_operation(service: str, operation: str, table: str):
    """Decorator to track database operations."""
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                return await func(*args, **kwargs)
            finally:
                latency = time.time() - start_time
                DB_QUERY_LATENCY_SECONDS.labels(
                    service=service,
                    operation=operation,
                    table=table
                ).observe(latency)
        return wrapper
    return decorator


@contextmanager
def track_payment_processing(payment_method: str):
    """Context manager for tracking payment processing."""
    start_time = time.time()
    status = "success"
    try:
        yield
    except Exception:
        status = "failed"
        raise
    finally:
        latency = time.time() - start_time
        PAYMENT_PROCESSING_SECONDS.labels(
            payment_method=payment_method,
            status=status
        ).observe(latency)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def record_order_e2e_latency(created_at: float, confirmed_at: float, order_type: str = "standard"):
    """Record end-to-end order latency."""
    latency = confirmed_at - created_at
    ORDER_E2E_LATENCY_SECONDS.labels(order_type=order_type).observe(latency)


def record_oversell_incident(sku_id: str, warehouse: str = "default"):
    """Record an oversell incident - this should never happen!"""
    INVENTORY_OVERSELL_INCIDENTS.labels(sku_id=sku_id, warehouse=warehouse).inc()


def record_duplicate_message(service: str, topic: str, event_type: str):
    """Record a duplicate message that was skipped."""
    KAFKA_DUPLICATE_MESSAGES_TOTAL.labels(
        service=service,
        topic=topic,
        event_type=event_type
    ).inc()


def update_consumer_lag(service: str, topic: str, partition: int, consumer_group: str, lag: int):
    """Update Kafka consumer lag metric."""
    KAFKA_CONSUMER_LAG.labels(
        service=service,
        topic=topic,
        partition=str(partition),
        consumer_group=consumer_group
    ).set(lag)


def update_stock_levels(sku_id: str, warehouse: str, available: int, reserved: int):
    """Update inventory stock level metrics."""
    INVENTORY_STOCK_LEVEL.labels(sku_id=sku_id, warehouse=warehouse).set(available)
    INVENTORY_RESERVED_STOCK.labels(sku_id=sku_id, warehouse=warehouse).set(reserved)


def set_service_info(service: str, version: str, environment: str):
    """Set service information."""
    SERVICE_INFO.info({
        'service': service,
        'version': version,
        'environment': environment
    })
