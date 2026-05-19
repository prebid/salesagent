"""Centralized error logging for MCP tools.

This module provides a decorator that wraps MCP tools to automatically log errors
to the activity feed and audit logs, giving tenants visibility into failures.
"""

import functools
import inspect
import json
import logging
from collections.abc import Callable
from typing import Any, NoReturn

from fastmcp.exceptions import ToolError
from fastmcp.server import Context as FastMCPContext

logger = logging.getLogger(__name__)


class AdCPToolError(ToolError):
    """MCP boundary ToolError carrying a two-layer AdCP error envelope.

    FastMCP serializes ``raise <ToolError>`` as
    ``CallToolResult(isError=True, content=[TextContent(text=str(error))])``.
    With a single ``str`` arg, ``str(self)`` returns the JSON-encoded envelope
    verbatim, so storyboard runners can ``JSON.parse(content[0].text)`` and
    read both ``adcp_error.code`` and ``errors[0].code``.

    The envelope is also exposed as ``self.envelope`` so audit logging,
    activity feed, and REST fallback code can read it without re-parsing.

    ``status_code`` mirrors the source ``AdCPError.status_code`` so REST
    routes catching this exception emit the right HTTP status. Defaults to
    500 for compatibility with paths that don't supply a typed source (the
    plain ToolError fallback in ``_handle_tool_error``).
    """

    def __init__(self, envelope: dict[str, Any], status_code: int = 500):
        self.envelope = envelope
        self.status_code = status_code
        super().__init__(json.dumps(envelope))


def _extract_tenant_and_principal(context: Any) -> tuple[str | None, str | None]:
    """Extract tenant_id and principal_id from context.

    Handles both FastMCP Context and ToolContext.

    Args:
        context: The context object (FastMCP Context or ToolContext)

    Returns:
        Tuple of (tenant_id, principal_id), either may be None
    """
    tenant_id = None
    principal_id = None

    # Try ToolContext first (has direct attributes)
    if hasattr(context, "tenant_id"):
        tenant_id = context.tenant_id
    if hasattr(context, "principal_id"):
        principal_id = context.principal_id

    # If we have tenant_id, we're done
    if tenant_id:
        return tenant_id, principal_id

    # Try to extract from FastMCP Context
    if isinstance(context, FastMCPContext):
        try:
            from src.core.transport_helpers import resolve_identity_from_context

            identity = resolve_identity_from_context(context, require_valid_token=False, protocol="mcp")
            if identity:
                if identity.tenant_id:
                    tenant_id = identity.tenant_id
                if identity.principal_id:
                    principal_id = identity.principal_id
        except Exception:
            logger.debug("Could not extract identity for error logging", exc_info=True)

    return tenant_id, principal_id


def extract_error_info(error: Exception) -> tuple[str, str, str | None]:
    """Extract error code, message, and recovery hint from an exception.

    For AdCPToolError, reads directly from the carried two-layer envelope.
    For AdCPError, uses the exception's error_code, message, and recovery attributes.
    For plain ToolError, attempts to parse structured (code, message, recovery) format
    for backward compatibility with code that raises ToolError directly.

    Args:
        error: The exception to extract info from

    Returns:
        Tuple of (error_code, error_message, recovery) where recovery may be None
    """
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPToolError):
        first = error.envelope["errors"][0]
        return first["code"], first.get("message", ""), first.get("recovery")
    if isinstance(error, AdCPError):
        return error.error_code, error.message, error.recovery
    elif isinstance(error, ToolError):
        # Plain ToolError raised by other code paths — preserve legacy parsing.
        # ToolError may be constructed as ToolError("CODE", "message", "recovery")
        # or ToolError("CODE", "message") or ToolError("message")
        if error.args:
            first_arg = str(error.args[0])
            is_error_code = (
                len(first_arg) <= 50
                and first_arg.isupper()
                and " " not in first_arg
                and first_arg.replace("_", "").isalnum()
            )
            if is_error_code and len(error.args) > 1:
                # Structured format: ToolError("CODE", "message") or ("CODE", "message", "recovery")
                recovery = str(error.args[2]) if len(error.args) > 2 else None
                return first_arg, str(error.args[1]), recovery
            else:
                # Single-arg format: ToolError("message")
                return "TOOL_ERROR", str(error), None
        return "TOOL_ERROR", str(error), None
    else:
        return type(error).__name__, str(error), None


def _log_tool_error(tool_name: str, error: Exception, tenant_id: str | None, principal_id: str | None) -> None:
    """Log tool errors to activity feed and audit logs.

    Args:
        tool_name: Name of the tool that failed
        error: The exception that occurred
        tenant_id: Tenant ID if available
        principal_id: Principal ID if available
    """
    if not tenant_id:
        # Can't log to activity feed without tenant context
        logger.warning(f"Tool {tool_name} failed without tenant context: {error}")
        return

    # Extract error code, message, and recovery hint
    error_code, error_message, _recovery = extract_error_info(error)

    # Log to activity feed for real-time visibility
    try:
        from src.services.activity_feed import activity_feed

        activity_feed.log_error(
            tenant_id=tenant_id,
            principal_name=principal_id or "anonymous",
            error_message=f"{tool_name}: {error_message}",
            error_code=error_code,
        )
    except Exception as e:
        logger.debug(f"Failed to log error to activity feed: {e}")

    # Log to audit log for persistent record
    try:
        from src.core.audit_logger import get_audit_logger

        audit_logger = get_audit_logger("MCP", tenant_id)
        audit_logger.log_operation(
            operation=tool_name,
            principal_name=principal_id or "anonymous",
            principal_id=principal_id or "anonymous",
            adapter_id="mcp_server",
            success=False,
            error=error_message,
        )
    except Exception as e:
        logger.debug(f"Failed to log error to audit log: {e}")


def _translate_to_tool_error(error: Exception) -> NoReturn:
    """Translate typed exceptions to AdCPToolError at the MCP boundary.

    AdCPError → AdCPToolError carrying a two-layer envelope built by
    ``build_two_layer_error_envelope()``. ValueError and PermissionError are
    wrapped in synthetic AdCPValidationError / AdCPAuthorizationError so they
    produce the same envelope shape. Already-translated AdCPToolError and
    plain ToolError pass through.

    This function always raises — it never returns.
    """
    from src.core.exceptions import (
        AdCPAuthorizationError,
        AdCPError,
        AdCPValidationError,
        build_two_layer_error_envelope,
    )

    if isinstance(error, ToolError):
        # Includes AdCPToolError — already in wire shape.
        raise
    if isinstance(error, AdCPError):
        envelope = build_two_layer_error_envelope(error)
        raise AdCPToolError(envelope, status_code=error.status_code) from error
    if isinstance(error, ValueError):
        synthetic: AdCPError = AdCPValidationError(str(error))
        raise AdCPToolError(build_two_layer_error_envelope(synthetic), status_code=synthetic.status_code) from error
    if isinstance(error, PermissionError):
        synthetic = AdCPAuthorizationError(str(error))
        raise AdCPToolError(build_two_layer_error_envelope(synthetic), status_code=synthetic.status_code) from error
    raise


def _handle_tool_exception(tool_func: Callable, error: Exception, args: tuple, kwargs: dict) -> NoReturn:
    """Shared exception path for both sync and async ``with_error_logging`` wrappers.

    Extracts tenant/principal from a Context found in positional or keyword args,
    logs the error to activity feed + audit log, then translates to AdCPToolError
    at the MCP boundary. Always raises — never returns.
    """
    context = None
    for arg in args:
        if isinstance(arg, FastMCPContext) or hasattr(arg, "tenant_id"):
            context = arg
            break
    if context is None:
        for v in kwargs.values():
            if isinstance(v, FastMCPContext) or hasattr(v, "tenant_id"):
                context = v
                break

    tenant_id, principal_id = _extract_tenant_and_principal(context) if context else (None, None)
    _log_tool_error(tool_func.__name__, error, tenant_id, principal_id)
    _translate_to_tool_error(error)


def with_error_logging(tool_func: Callable) -> Callable:
    """Decorator to add centralized error logging to an MCP tool.

    This wrapper catches exceptions from tool calls and logs them to:
    - Activity feed (for real-time tenant visibility)
    - Audit log (for persistent records)

    The error is then re-raised so MCP handles it normally.

    Usage:
        mcp.tool()(with_error_logging(my_tool))

    Args:
        tool_func: The tool function to wrap

    Returns:
        Wrapped function with error logging
    """
    is_async = inspect.iscoroutinefunction(tool_func)

    if is_async:

        @functools.wraps(tool_func)
        async def async_wrapper(*args, **kwargs) -> Any:
            try:
                return await tool_func(*args, **kwargs)
            except Exception as e:
                _handle_tool_exception(tool_func, e, args, kwargs)

        return async_wrapper

    @functools.wraps(tool_func)
    def sync_wrapper(*args, **kwargs) -> Any:
        try:
            return tool_func(*args, **kwargs)
        except Exception as e:
            _handle_tool_exception(tool_func, e, args, kwargs)

    return sync_wrapper
