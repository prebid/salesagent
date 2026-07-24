"""Structured logging configuration for OAuth and other operations.

Supports two modes:
- Production (Fly.io): JSON format for log aggregation
- Development: Human-readable format
"""

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from src.core.config import is_production as config_is_production
from src.core.logging_utils import sanitize_log_value


class ClientDisconnectFilter(logging.Filter):
    """Filter out noisy ClientDisconnect errors from MCP library.

    These are normal occurrences when clients disconnect mid-request.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Filter out ClientDisconnect stack traces
        if "ClientDisconnect" in record.getMessage():
            return False
        if record.exc_info and record.exc_info[0]:
            exc_name = record.exc_info[0].__name__
            if exc_name == "ClientDisconnect":
                return False
        return True


class DeprecationFilter(logging.Filter):
    """Filter out deprecation warnings from MCP library."""

    def filter(self, record: logging.LogRecord) -> bool:
        if "DeprecationWarning" in record.getMessage():
            return False
        return True


class JSONFormatter(logging.Formatter):
    """JSON log formatter for production environments.

    Outputs single-line JSON that Fly.io and other log aggregators handle correctly.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Add extra fields from record (non-standard attributes passed via extra={})
        # Standard LogRecord attributes to exclude
        standard_attrs = {
            "name",
            "msg",
            "args",
            "created",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "exc_info",
            "exc_text",
            "thread",
            "threadName",
            "taskName",
            "message",
        }
        extra_fields = {k: v for k, v in record.__dict__.items() if k not in standard_attrs}
        if extra_fields:
            log_entry["extra"] = extra_fields

        return json.dumps(log_entry)


class SingleLineFormatter(logging.Formatter):
    """Keep development logs and rendered tracebacks on one physical line."""

    def format(self, record: logging.LogRecord) -> str:
        return sanitize_log_value(super().format(record), max_length=20_000)


def setup_structured_logging() -> None:
    """Setup structured JSON logging for production environments.

    In production, configures all loggers to output single-line JSON.
    This prevents multiline log messages from appearing as separate log entries.
    """
    # config.is_production() is the canonical ENVIRONMENT/PRODUCTION union; the
    # log path deliberately ADDS the deploy-platform signal (FLY_APP_NAME) so a
    # platform deploy gets JSON logs even if neither env-var convention is set.
    # Union only ever widens production detection — never weakens it.
    is_production = bool(os.environ.get("FLY_APP_NAME")) or config_is_production()

    if is_production:
        # Configure root logger with JSON formatter
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)

        # Remove existing handlers
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        # Add JSON formatter handler
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        root_logger.addHandler(handler)

        # Also configure common library loggers that might have their own handlers
        for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error", "fastmcp", "starlette"]:
            lib_logger = logging.getLogger(logger_name)
            lib_logger.handlers = []
            lib_logger.addHandler(handler)
            lib_logger.propagate = False

        # Suppress noisy MCP library loggers
        # ClientDisconnect is a normal event when clients disconnect mid-request
        mcp_loggers = [
            "mcp.server.streamable_http",
            "mcp.server.streamable_http_manager",
            "mcp.server.lowlevel.server",
        ]
        for logger_name in mcp_loggers:
            mcp_logger = logging.getLogger(logger_name)
            mcp_logger.addFilter(ClientDisconnectFilter())
            mcp_logger.addFilter(DeprecationFilter())
            # Set to WARNING to reduce INFO-level noise (session creation messages)
            mcp_logger.setLevel(logging.WARNING)

        logging.info("JSON structured logging enabled for production")
    else:
        # Development mode - use standard format
        # force=True ensures configuration is applied even if logging was already configured
        handler = logging.StreamHandler()
        handler.setFormatter(SingleLineFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


# Create custom logger for OAuth operations
oauth_logger = logging.getLogger("adcp.oauth")


class StructuredLogger:
    """Structured logger for OAuth and other operations."""

    def __init__(self, logger_name: str = "adcp.oauth"):
        self.logger = logging.getLogger(logger_name)

    def log_oauth_operation(
        self,
        operation: str,
        success: bool,
        details: dict[str, Any] | None = None,
        error: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Log OAuth operations with structured data."""

        log_data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "operation": operation,
            "success": success,
            "type": "oauth_operation",
        }

        if details:
            log_data["details"] = details

        if error:
            log_data["error"] = error

        if duration_ms is not None:
            log_data["duration_ms"] = duration_ms

        # Log as structured JSON
        if success:
            self.logger.info(json.dumps(log_data))
        else:
            self.logger.error(json.dumps(log_data))

    def log_gam_oauth_config_load(self, success: bool, client_id_prefix: str = "", error: str = "") -> None:
        """Log GAM OAuth configuration loading."""
        details = {}
        if client_id_prefix:
            details["client_id_prefix"] = client_id_prefix

        self.log_oauth_operation(
            operation="gam_oauth_config_load", success=success, details=details, error=error if not success else None
        )

    def log_oauth_token_refresh(self, success: bool, error: str = "", duration_ms: float = 0) -> None:
        """Log OAuth token refresh attempts."""
        self.log_oauth_operation(
            operation="oauth_token_refresh",
            success=success,
            error=error if not success else None,
            duration_ms=duration_ms,
        )

    def log_gam_client_creation(self, success: bool, error: str = "") -> None:
        """Log GAM client creation attempts."""
        self.log_oauth_operation(operation="gam_client_creation", success=success, error=error if not success else None)


# Global structured logger instance
oauth_structured_logger = StructuredLogger()


def setup_oauth_logging() -> None:
    """Setup structured logging for OAuth operations."""
    # Configure OAuth logger
    oauth_logger.setLevel(logging.INFO)

    # Add handler if not already present
    if not oauth_logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        oauth_logger.addHandler(handler)

    oauth_logger.info("OAuth structured logging initialized")


def log_safe(value: object) -> str:
    """Neutralize control characters in request-provided values before logging.

    Buyer-supplied ids (creative_id, package_id) flow into log lines; a
    newline embedded in one would forge log entries (CodeQL py/log-injection).
    Delegates to :func:`src.core.logging_utils.sanitize_log_value` so the two
    sanitizers share one escape table — note this ESCAPES control characters
    (``\n`` renders as ``\\n``) where the historical implementation stripped
    them; ids stay correlatable, only the rendered log text changes. No
    truncation: callers pass whole pre-formatted messages, not single values.

    CANONICAL IDIOM for new code: ``logger.x("... %s", sanitize_log_value(v))``
    — sanitize each interpolated VALUE and keep the static message text
    unescaped. ``log_safe(f"...")`` is the legacy whole-message form (same
    escape table, so equally injection-safe) kept for its existing call sites;
    prefer the ``%s`` form when writing or touching log lines.
    """
    return sanitize_log_value(value, max_length=None)
