"""
Enhanced logging for Google Ad Manager operations.

This module provides structured logging with:
- Operation tracking with correlation IDs
- Performance metrics
- Audit trail for compliance
- Integration with monitoring systems
"""

import logging
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import Enum
from functools import wraps
from typing import Any

# Configure structured logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

logger = logging.getLogger(__name__)


class GAMOperation(Enum):
    """Enumeration of GAM operations for consistent logging."""

    CREATE_ORDER = "create_order"
    CREATE_LINE_ITEM = "create_line_item"
    CREATE_CREATIVE = "create_creative"
    ASSOCIATE_CREATIVE = "associate_creative"
    UPDATE_ORDER = "update_order"
    UPDATE_LINE_ITEM = "update_line_item"
    PAUSE_ORDER = "pause_order"
    RESUME_ORDER = "resume_order"
    GET_REPORT = "get_report"
    QUERY_INVENTORY = "query_inventory"
    VALIDATE_TARGETING = "validate_targeting"


class GAMLogContext:
    """Context manager for structured logging of GAM operations."""

    def __init__(
        self,
        operation: GAMOperation,
        principal_id: str,
        media_buy_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self.operation = operation
        self.principal_id = principal_id
        self.media_buy_id = media_buy_id
        self.correlation_id = str(uuid.uuid4())
        self.metadata = metadata or {}
        self.start_time: float | None = None
        self.end_time: float | None = None
        self.success = False
        self.error: Exception | None = None
        self.api_calls: list[dict[str, Any]] = []

    def add_api_call(
        self,
        service: str,
        method: str,
        request_data: dict[str, Any],
        response_data: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ):
        """Record an API call made during this operation."""
        self.api_calls.append(
            {
                "service": service,
                "method": method,
                "request_summary": self._summarize_request(request_data),
                "response_summary": self._summarize_response(response_data) if response_data else None,
                "duration_ms": duration_ms,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

    def _summarize_request(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a summary of request data for logging."""
        # Don't log sensitive data
        safe_fields = ["name", "orderId", "lineItemId", "advertiserId", "status", "priority", "targeting", "budget"]

        summary = {}
        for field in safe_fields:
            if field in data:
                if field == "budget" and isinstance(data[field], dict):
                    # Sanitize budget info
                    summary[field] = {
                        "currency": data[field].get("currencyCode"),
                        "amount": "***",  # Don't log actual amounts
                    }
                elif field == "targeting":
                    # Summarize targeting
                    summary[field] = self._summarize_targeting(data[field])
                else:
                    summary[field] = data[field]

        return summary

    def _summarize_response(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a summary of response data for logging."""
        if isinstance(data, dict):
            return {"id": data.get("id"), "name": data.get("name"), "status": data.get("status")}
        elif isinstance(data, list):
            return {"count": len(data), "first_id": data[0].get("id") if data else None}
        return {"type": str(type(data))}

    def _summarize_targeting(self, targeting: dict[str, Any]) -> dict[str, Any]:
        """Summarize targeting for logging."""
        summary: dict[str, Any] = {}

        if "geoTargeting" in targeting:
            geo = targeting["geoTargeting"]
            summary["geo"] = {
                "targeted_count": len(geo.get("targetedLocations", [])),
                "excluded_count": len(geo.get("excludedLocations", [])),
            }

        if "technologyTargeting" in targeting:
            tech = targeting["technologyTargeting"]
            summary["technology"] = {
                "devices": len(tech.get("deviceCategories", [])),
                "os": len(tech.get("operatingSystems", [])),
                "browsers": len(tech.get("browsers", [])),
            }

        if "customTargeting" in targeting:
            summary["custom_keys"] = len(targeting["customTargeting"])

        return summary

    def to_dict(self) -> dict[str, Any]:
        """Convert context to dictionary for logging."""
        duration_ms = None
        if self.start_time and self.end_time:
            duration_ms = (self.end_time - self.start_time) * 1000

        return {
            "correlation_id": self.correlation_id,
            "operation": self.operation.value,
            "principal_id": self.principal_id,
            "media_buy_id": self.media_buy_id,
            "success": self.success,
            "duration_ms": duration_ms,
            "api_call_count": len(self.api_calls),
            "metadata": self.metadata,
            "error": str(self.error) if self.error else None,
            "timestamp": datetime.now(UTC).isoformat(),
        }


@contextmanager
def log_gam_operation(
    operation: GAMOperation,
    principal_id: str,
    media_buy_id: str | None = None,
    metadata: dict[str, Any] | None = None,
):
    """
    Context manager for logging GAM operations.

    Usage:
        with log_gam_operation(GAMOperation.CREATE_ORDER, "principal_123") as ctx:
            # Your GAM API calls here
            ctx.add_api_call("OrderService", "createOrders", order_data)
    """
    context = GAMLogContext(operation, principal_id, media_buy_id, metadata)
    context.start_time = time.time()

    # Log operation start
    logger.info(
        f"GAM operation started: {operation.value}",
        extra={"correlation_id": context.correlation_id, "principal_id": principal_id, "operation": operation.value},
    )

    try:
        yield context
        context.success = True

    except Exception as e:
        context.success = False
        context.error = e

        # Log error
        logger.error(
            f"GAM operation failed: {operation.value}",
            extra={"correlation_id": context.correlation_id, "error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        raise

    finally:
        context.end_time = time.time()

        # Log operation completion
        log_data = context.to_dict()

        if context.success:
            logger.info(f"GAM operation completed: {operation.value}", extra=log_data)

        # Log detailed API calls for debugging
        if logger.isEnabledFor(logging.DEBUG):
            for api_call in context.api_calls:
                logger.debug(
                    f"GAM API call: {api_call['service']}.{api_call['method']}",
                    extra={"correlation_id": context.correlation_id, "api_call": api_call},
                )

        # Send metrics to monitoring system
        _send_metrics(context)

        # Store in audit log
        _store_audit_log(context)


def log_api_call(service: str, method: str, duration_ms: float | None = None):
    """
    Decorator for logging individual GAM API calls.

    Usage:
        @log_api_call("OrderService", "createOrders")
        def create_order(self, order_data):
            return self.order_service.createOrders([order_data])
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()

            try:
                result = func(*args, **kwargs)
                duration = (time.time() - start_time) * 1000

                logger.debug(
                    f"GAM API call successful: {service}.{method}",
                    extra={"service": service, "method": method, "duration_ms": duration, "success": True},
                )

                return result

            except Exception as e:
                duration = (time.time() - start_time) * 1000

                logger.error(
                    f"GAM API call failed: {service}.{method}",
                    extra={
                        "service": service,
                        "method": method,
                        "duration_ms": duration,
                        "success": False,
                        "error": str(e),
                    },
                )
                raise

        return wrapper

    return decorator


class GAMMetrics:
    """Collect and report metrics for GAM operations."""

    def __init__(self):
        self.operation_counts = {}
        self.operation_durations = {}
        self.error_counts = {}
        self.api_call_counts = {}

    def record_operation(self, context: GAMLogContext):
        """Record metrics from an operation context."""
        op_name = context.operation.value

        # Count operations
        if op_name not in self.operation_counts:
            self.operation_counts[op_name] = {"success": 0, "failure": 0}

        if context.success:
            self.operation_counts[op_name]["success"] += 1
        else:
            self.operation_counts[op_name]["failure"] += 1

            # Count errors by type
            error_type = type(context.error).__name__ if context.error else "Unknown"
            if error_type not in self.error_counts:
                self.error_counts[error_type] = 0
            self.error_counts[error_type] += 1

        # Record duration
        if context.start_time and context.end_time:
            duration = (context.end_time - context.start_time) * 1000
            if op_name not in self.operation_durations:
                self.operation_durations[op_name] = []
            self.operation_durations[op_name].append(duration)

        # Count API calls
        for api_call in context.api_calls:
            key = f"{api_call['service']}.{api_call['method']}"
            if key not in self.api_call_counts:
                self.api_call_counts[key] = 0
            self.api_call_counts[key] += 1

    def get_metrics(self) -> dict[str, Any]:
        """Get current metrics summary."""
        metrics = {
            "operations": self.operation_counts,
            "errors": self.error_counts,
            "api_calls": self.api_call_counts,
            "durations": {},
        }

        # Calculate duration statistics
        for op_name, durations in self.operation_durations.items():
            if durations:
                metrics["durations"][op_name] = {
                    "count": len(durations),
                    "mean": sum(durations) / len(durations),
                    "min": min(durations),
                    "max": max(durations),
                    "p50": sorted(durations)[len(durations) // 2],
                    "p95": sorted(durations)[int(len(durations) * 0.95)],
                }

        return metrics


# Global metrics instance
_metrics = GAMMetrics()


def _send_metrics(context: GAMLogContext):
    """Send metrics to monitoring system."""
    _metrics.record_operation(context)

    # In production, this would send to DataDog, CloudWatch, etc.
    # For now, just log periodically
    # Use module-level counter instead of function attribute for type safety
    global _send_metrics_call_count
    if "_send_metrics_call_count" not in globals():
        _send_metrics_call_count = 0
    _send_metrics_call_count += 1

    if _send_metrics_call_count % 100 == 0:
        logger.info("GAM metrics summary", extra={"metrics": _metrics.get_metrics()})


# Module-level counter for metrics
_send_metrics_call_count = 0


def _store_audit_log(context: GAMLogContext):
    """Store operation in audit log."""
    # In production, this would write to database
    # For now, we'll use the existing audit logger

    audit_data = {
        "correlation_id": context.correlation_id,
        "operation": context.operation.value,
        "principal_id": context.principal_id,
        "media_buy_id": context.media_buy_id,
        "success": context.success,
        "api_calls": len(context.api_calls),
        "duration_ms": (
            (context.end_time - context.start_time) * 1000 if context.start_time and context.end_time else None
        ),
        "error": str(context.error) if context.error else None,
    }

    # This would integrate with the existing audit_logger
    logger.info(f"AUDIT: GAM {context.operation.value}", extra=audit_data)


# Utility functions for common logging patterns


def log_dry_run(operation: str, details: dict[str, Any]):
    """Log dry-run operations consistently."""
    logger.info(
        f"[DRY RUN] Would execute: {operation}", extra={"dry_run": True, "operation": operation, "details": details}
    )


def log_validation_error(field: str, value: Any, reason: str):
    """Log validation errors consistently."""
    logger.error(
        f"Validation error: {field}",
        extra={"field": field, "value": str(value)[:100], "reason": reason},  # Truncate for safety
    )


def log_configuration(config_type: str, config: dict[str, Any]):
    """Log configuration details safely."""
    # Remove sensitive fields
    safe_config = {k: v for k, v in config.items() if k not in ["service_account_key_file", "access_token", "api_key"]}

    logger.info(f"GAM configuration loaded: {config_type}", extra={"config_type": config_type, "config": safe_config})
