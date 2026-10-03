"""
Prometheus Monitoring for FastAPI
FREE metrics collection and monitoring
"""
from prometheus_client import Counter, Histogram, Gauge, generate_latest
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware
import time
from typing import Callable

# Define Prometheus metrics
REQUEST_COUNT = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status']
)

REQUEST_LATENCY = Histogram(
    'http_request_duration_seconds',
    'HTTP request latency',
    ['method', 'endpoint']
)

ACTIVE_REQUESTS = Gauge(
    'http_requests_active',
    'Active HTTP requests'
)

ENDPOINT_ERROR_RATE = Gauge(
    'endpoint_error_rate',
    'Error rate per endpoint',
    ['endpoint']
)

class PrometheusMiddleware(BaseHTTPMiddleware):
    """Middleware for Prometheus metrics collection"""

    def __init__(self, app):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        # Track active requests
        ACTIVE_REQUESTS.inc()

        # Start timing
        start_time = time.time()

        # Get endpoint path
        path = request.url.path
        method = request.method

        try:
            # Process request
            response = await call_next(request)

            # Record metrics
            REQUEST_COUNT.labels(
                method=method,
                endpoint=path,
                status=response.status_code
            ).inc()

            # Record latency
            REQUEST_LATENCY.labels(
                method=method,
                endpoint=path
            ).observe(time.time() - start_time)

            return response

        finally:
            # Decrement active requests
            ACTIVE_REQUESTS.dec()


def setup_prometheus(app: FastAPI):
    """Setup Prometheus monitoring on FastAPI app"""

    # Add middleware - FastAPI will instantiate it properly
    app.add_middleware(PrometheusMiddleware)

    # Add metrics endpoint
    @app.get("/metrics", response_class=PlainTextResponse)
    async def get_metrics():
        """Prometheus metrics endpoint"""
        return generate_latest()

    # Add health check endpoint
    @app.get("/health")
    async def health_check():
        """Health check endpoint for monitoring"""
        return {"status": "healthy"}


# Helper function to create Grafana dashboard config
def generate_grafana_dashboard():
    """Generate Grafana dashboard configuration"""
    return {
        "dashboard": {
            "title": "FastAPI Monitoring",
            "panels": [
                {
                    "title": "Request Rate",
                    "targets": [
                        {
                            "expr": "rate(http_requests_total[5m])"
                        }
                    ]
                },
                {
                    "title": "Response Time",
                    "targets": [
                        {
                            "expr": "histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))"
                        }
                    ]
                },
                {
                    "title": "Error Rate",
                    "targets": [
                        {
                            "expr": "rate(http_requests_total{status=~\"4..|5..\"}[5m])"
                        }
                    ]
                },
                {
                    "title": "Active Requests",
                    "targets": [
                        {
                            "expr": "http_requests_active"
                        }
                    ]
                }
            ]
        }
    }
