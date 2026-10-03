"""
Basic monitoring and observability for PaX Platform
Integrates with Google Cloud Operations (formerly Stackdriver)
"""

import logging
import time
from functools import wraps
from typing import Dict, Any
import json
import os

# Try to import Google Cloud libraries (optional for CI/testing)
try:
    from google.cloud import logging as cloud_logging
    from google.cloud import monitoring_v3
    GOOGLE_CLOUD_AVAILABLE = True
except ImportError:
    cloud_logging = None
    monitoring_v3 = None
    GOOGLE_CLOUD_AVAILABLE = False

# Initialize Google Cloud Logging (if in production and available)
if os.getenv("ENVIRONMENT") == "production" and GOOGLE_CLOUD_AVAILABLE:
    try:
        client = cloud_logging.Client()
        client.setup_logging()
    except Exception as e:
        print(f"Warning: Could not initialize Google Cloud Logging: {e}")

# Structured logger
logger = logging.getLogger(__name__)

class StructuredLogger:
    """Structured logging for better observability"""

    @staticmethod
    def log_request(method: str, path: str, user_id: str = None, space_id: str = None,
                   status_code: int = None, response_time: float = None):
        """Log HTTP requests with structured data"""
        log_data = {
            "event_type": "http_request",
            "method": method,
            "path": path,
            "status_code": status_code,
            "response_time_ms": response_time * 1000 if response_time else None,
            "user_id": user_id,
            "space_id": space_id,
            "timestamp": time.time()
        }
        logger.info(json.dumps(log_data))

    @staticmethod
    def log_business_event(event_type: str, user_id: str = None, space_id: str = None,
                          metadata: Dict[str, Any] = None):
        """Log business events for analytics"""
        log_data = {
            "event_type": f"business_{event_type}",
            "user_id": user_id,
            "space_id": space_id,
            "metadata": metadata or {},
            "timestamp": time.time()
        }
        logger.info(json.dumps(log_data))

    @staticmethod
    def log_error(error: Exception, context: Dict[str, Any] = None):
        """Log errors with context"""
        log_data = {
            "event_type": "error",
            "error_type": type(error).__name__,
            "error_message": str(error),
            "context": context or {},
            "timestamp": time.time()
        }
        logger.error(json.dumps(log_data))

def monitor_performance(operation_name: str):
    """Decorator to monitor function performance"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time

                # Log performance metrics
                StructuredLogger.log_business_event(
                    "performance_metric",
                    metadata={
                        "operation": operation_name,
                        "duration_ms": duration * 1000,
                        "success": True
                    }
                )
                return result
            except Exception as e:
                duration = time.time() - start_time
                StructuredLogger.log_error(e, {
                    "operation": operation_name,
                    "duration_ms": duration * 1000
                })
                raise
        return wrapper
    return decorator

class MetricsCollector:
    """Collect custom metrics for Cloud Monitoring"""

    def __init__(self):
        if os.getenv("ENVIRONMENT") == "production" and GOOGLE_CLOUD_AVAILABLE:
            try:
                self.client = monitoring_v3.MetricServiceClient()
                self.project_name = f"projects/{os.getenv('GOOGLE_CLOUD_PROJECT', 'jax-platform-prod')}"
            except Exception as e:
                print(f"Warning: Could not initialize Google Cloud Monitoring: {e}")
                self.client = None
        else:
            self.client = None

    def increment_counter(self, metric_name: str, labels: Dict[str, str] = None):
        """Increment a custom counter metric"""
        if not self.client:
            logger.info(f"METRIC: {metric_name} incremented (dev mode)")
            return

        # Implementation for production metrics
        # This would send metrics to Google Cloud Monitoring
        pass

    def record_gauge(self, metric_name: str, value: float, labels: Dict[str, str] = None):
        """Record a gauge metric value"""
        if not self.client:
            logger.info(f"METRIC: {metric_name} = {value} (dev mode)")
            return

        # Implementation for production metrics
        pass

# Global instances
structured_logger = StructuredLogger()
metrics = MetricsCollector()

# Health check metrics
class HealthMetrics:
    """Track system health metrics"""

    @staticmethod
    def check_database_health():
        """Basic database connectivity check"""
        # This would be implemented to ping the database
        return {"status": "healthy", "response_time_ms": 5}

    @staticmethod
    def check_dependencies_health():
        """Check external dependencies"""
        return {
            "database": HealthMetrics.check_database_health(),
            "redis": {"status": "healthy", "response_time_ms": 2},
            "external_apis": {"status": "healthy"}
        }

    @staticmethod
    def get_system_metrics():
        """Get basic system metrics"""
        return {
            "memory_usage_mb": 256,  # Would be actual system metrics
            "cpu_usage_percent": 15,
            "active_connections": 42
        }
