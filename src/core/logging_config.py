"""Structured logging configuration for OAuth and other operations.

Supports two modes:
- Production (Fly.io): JSON format for log aggregation
- Development: Human-readable format
"""

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

try:
    from opentelemetry import trace
except ImportError:
    trace = None  # type: ignore[assignment]


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


class UvicornAccessNoiseFilter(logging.Filter):
    """Drop ``uvicorn.access`` lines for the two endpoints that dominate
    access-log volume: ``/mcp[/]`` (MCP pollers) and ``/health`` (Fly's
    TCP+HTTP health checks). 1000s of lines per minute per machine on the
    canonical-case 200 path.

    Status-code policy differs per surface:

    * ``/mcp[/]`` — drop ``2xx`` AND ``401``. The 401 carve-out matters
      because anonymous internet traffic hammers ``/mcp`` constantly (bot
      probes, misconfigured clients) and every rejection emits a 401
      access line + a paired structured ``adcp.server.auth`` log. The
      structured log is the actual signal; the access line is dupe noise.
      Other 4xx (403/404/422/...) and all 5xx still log — those indicate
      a real problem worth investigating.
    * ``/health`` — drop ``2xx`` only. A 4xx/5xx on the health check
      surface always means a config or platform bug worth seeing.

    Any other path (admin UI, ``/a2a``, ``/.well-known/*``, etc.) is
    unaffected regardless of status code.
    """

    _MCP_NOISE = re.compile(r'"(?:GET|POST|HEAD|OPTIONS) /mcp/?(?:\?\S*)? HTTP/[\d.]+" (?:2\d\d|401)')
    _HEALTH_NOISE = re.compile(r'"(?:GET|POST|HEAD|OPTIONS) /health(?:\?\S*)? HTTP/[\d.]+" 2\d\d')

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not (self._MCP_NOISE.search(msg) or self._HEALTH_NOISE.search(msg))


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

        if trace is not None:
            span = trace.get_current_span()
            ctx = span.get_span_context() if span else None
            if ctx and ctx.is_valid:
                log_entry["trace_id"] = format(ctx.trace_id, "032x")

        return json.dumps(log_entry)


def setup_structured_logging() -> None:
    """Setup structured JSON logging for production environments.

    In production (Fly.io), configures all loggers to output single-line JSON.
    This prevents multiline log messages from appearing as separate log entries.
    """
    is_production = bool(os.environ.get("FLY_APP_NAME") or os.environ.get("PRODUCTION"))

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
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            force=True,
        )

    # Suppress /mcp and /health access-log spam in BOTH modes. Pollers + Fly
    # health checks generate the bulk of access-log volume; failures still
    # surface because the filter only drops 2xx.
    logging.getLogger("uvicorn.access").addFilter(UvicornAccessNoiseFilter())


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
