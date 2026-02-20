"""
FastAPI Middleware for Production-Grade Observability.

Provides:
1. Request/response metrics (latency, status codes, in-flight)
2. Correlation ID propagation
3. Structured logging
4. Error tracking

Usage:
    from fastapi_middleware import setup_observability
    
    app = FastAPI()
    setup_observability(app, service_name="order-service")
"""

import time
import uuid
import logging
from typing import Callable
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from .metrics import (
    HTTP_REQUEST_LATENCY,
    HTTP_REQUEST_TOTAL,
    HTTP_IN_FLIGHT_REQUESTS,
    HTTP_ERRORS_TOTAL,
    set_service_info,
)

logger = logging.getLogger(__name__)


class MetricsMiddleware(BaseHTTPMiddleware):
    """
    Middleware to track HTTP request metrics.
    
    Tracks:
    - Request latency (histogram for p50/p95/p99)
    - Request count by status code
    - In-flight requests (gauge)
    - Error rate
    
    Why these metrics matter:
    - p95/p99 latency: Catches tail latency affecting user experience
    - In-flight requests: Detects backpressure and capacity issues
    - Error rate: Immediate signal for service degradation
    """
    
    def __init__(self, app, service_name: str):
        super().__init__(app)
        self.service_name = service_name
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Track in-flight requests
        HTTP_IN_FLIGHT_REQUESTS.labels(service=self.service_name).inc()
        
        # Start timing
        start_time = time.time()
        
        # Extract or generate correlation ID
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        request.state.correlation_id = correlation_id
        
        # Process request
        status_code = "500"
        try:
            response = await call_next(request)
            status_code = str(response.status_code)
            
            # Add correlation ID to response
            response.headers["X-Correlation-ID"] = correlation_id
            
            return response
            
        except Exception as e:
            # Track error
            HTTP_ERRORS_TOTAL.labels(
                service=self.service_name,
                endpoint=request.url.path,
                error_type="server_error"
            ).inc()
            
            logger.error(
                f"Request failed: path={request.url.path}, "
                f"correlation_id={correlation_id}, error={str(e)}"
            )
            raise
            
        finally:
            # Record metrics
            latency = time.time() - start_time
            endpoint = self._normalize_path(request.url.path)
            
            HTTP_REQUEST_LATENCY.labels(
                service=self.service_name,
                endpoint=endpoint,
                method=request.method,
                status_code=status_code
            ).observe(latency)
            
            HTTP_REQUEST_TOTAL.labels(
                service=self.service_name,
                endpoint=endpoint,
                method=request.method,
                status_code=status_code
            ).inc()
            
            HTTP_IN_FLIGHT_REQUESTS.labels(service=self.service_name).dec()
            
            # Log request
            logger.info(
                f"Request completed: method={request.method}, path={endpoint}, "
                f"status={status_code}, latency={latency:.3f}s, "
                f"correlation_id={correlation_id}"
            )
    
    def _normalize_path(self, path: str) -> str:
        """
        Normalize path for metrics to avoid high cardinality.
        
        /api/orders/123 -> /api/orders/{id}
        /api/users/abc-def -> /api/users/{id}
        """
        parts = path.split("/")
        normalized = []
        
        for part in parts:
            # Replace UUIDs
            if len(part) == 36 and part.count("-") == 4:
                normalized.append("{id}")
            # Replace numeric IDs
            elif part.isdigit():
                normalized.append("{id}")
            # Replace SKU patterns
            elif part.startswith("SKU-"):
                normalized.append("{sku}")
            else:
                normalized.append(part)
        
        return "/".join(normalized)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Middleware to propagate correlation IDs for distributed tracing.
    
    Why this matters:
    - Enables tracing requests across microservices
    - Essential for debugging distributed systems
    - Required for root cause analysis
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Get or create correlation ID
        correlation_id = request.headers.get(
            "X-Correlation-ID",
            request.headers.get("X-Request-ID", str(uuid.uuid4()))
        )
        
        # Store in request state
        request.state.correlation_id = correlation_id
        
        # Process request
        response = await call_next(request)
        
        # Add to response headers
        response.headers["X-Correlation-ID"] = correlation_id
        
        return response


def setup_observability(
    app: FastAPI,
    service_name: str,
    version: str = "1.0.0",
    environment: str = "production"
):
    """
    Setup production-grade observability for a FastAPI service.
    
    Adds:
    - Metrics middleware (latency, errors, in-flight)
    - Correlation ID propagation
    - Prometheus metrics endpoint
    - Health check endpoint
    """
    
    # Set service info
    set_service_info(service_name, version, environment)
    
    # Add middleware (order matters - first added is outermost)
    app.add_middleware(MetricsMiddleware, service_name=service_name)
    app.add_middleware(CorrelationIdMiddleware)
    
    # Add metrics endpoint
    @app.get("/metrics")
    async def metrics():
        """Prometheus metrics endpoint."""
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST
        )
    
    # Add health endpoint
    @app.get("/health")
    async def health():
        """Health check endpoint for load balancers."""
        return {
            "status": "healthy",
            "service": service_name,
            "version": version
        }
    
    # Add readiness endpoint
    @app.get("/ready")
    async def ready():
        """Readiness check for Kubernetes."""
        # Add actual dependency checks here
        return {"status": "ready"}
    
    logger.info(f"Observability setup complete for {service_name}")


def get_correlation_id(request: Request) -> str:
    """Get correlation ID from request."""
    return getattr(request.state, "correlation_id", str(uuid.uuid4()))
