"""Admin UI action logging decorator.

Automatically logs admin UI actions to the audit_logs table for compliance and visibility.

DECORATOR ORDER CONVENTION:
    @route_decorator
    @require_tenant_access()  # First: Check authorization
    @log_admin_action()       # Second: Log authorized actions only

This order ensures we only log actions by authorized users.
"""

import logging
from collections.abc import Callable, Mapping
from functools import wraps
from typing import Any

from flask import g, request, session

logger = logging.getLogger(__name__)

# Sensitive field patterns to exclude from audit logs
_SENSITIVE_PATTERNS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "secret_key",
        "webhook_secret",
        "signing_secret",
        "token",
        "access_token",
        "refresh_token",
        "bearer_token",
        "api_token",
        "auth_token",
        "key",
        "api_key",
        "apikey",
        "private_key",
        "public_key",
        "priv_key",
        "credential",
        "credentials",
        "authorization",
        "auth",
        "bearer",
        "oauth",
        "oauth_token",
        "oauth_secret",
        "cert",
        "certificate",
    }
)

_SENSITIVE_SUFFIXES = ("_secret", "_token", "_key", "_password", "_credential", "_pwd")


# Header names whose values must always be redacted in any reflective output.
# Lower-case for case-insensitive comparison; Flask preserves header casing as
# received from the client, so we lower the lookup side.
_SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-adcp-auth",
        "x-api-key",
        "x-auth-token",
        "x-csrf-token",
        "x-session-token",
    }
)


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of ``headers`` with token-bearing values masked.

    Used by debug/diagnostic endpoints that reflect request headers — the
    canonical bearer carriers (``Authorization``, ``x-adcp-auth``,
    ``Cookie``, etc.) become ``"<redacted>"`` so a misconfigured proxy
    or shared admin session doesn't leak live tokens through the
    response body. Empty values pass through as-is to preserve operator
    visibility into "header present, value missing" cases.
    """
    redacted: dict[str, str] = {}
    for name, value in headers.items():
        lower = name.lower()
        if lower in _SENSITIVE_HEADER_NAMES or _is_sensitive_field(name):
            redacted[name] = "<redacted>" if value else ""
        else:
            redacted[name] = value
    return redacted


def _is_sensitive_field(field_name: str) -> bool:
    """Check if field name indicates sensitive data.

    Args:
        field_name: Name of the field to check

    Returns:
        True if field is likely to contain sensitive data
    """
    lower_field = field_name.lower()

    # Check exact matches
    if lower_field in _SENSITIVE_PATTERNS:
        return True

    # Check suffixes
    if lower_field.endswith(_SENSITIVE_SUFFIXES):
        return True

    return False


def _sanitize_value(value: Any) -> str:
    """Sanitize and truncate value for audit log storage.

    Args:
        value: Value to sanitize

    Returns:
        Sanitized string (max 100 characters)
    """
    value_str = str(value)
    return value_str[:100] if len(value_str) > 100 else value_str


def _extract_safe_request_data() -> dict[str, str]:
    """Extract non-sensitive data from request (form or JSON).

    Returns:
        Dictionary of safe field names to sanitized values
    """
    safe_fields: dict[str, str] = {}

    # Form data
    if request.form:
        for key, value in request.form.items():
            if not _is_sensitive_field(key):
                safe_fields[key] = _sanitize_value(value)

    # JSON data
    elif request.is_json:
        json_data = request.get_json() or {}
        for key, value in json_data.items():
            if not _is_sensitive_field(key):
                safe_fields[key] = _sanitize_value(value)

    return safe_fields


def _get_or_create_audit_logger(tenant_id: str):
    """Get or create audit logger for this request.

    Uses Flask's g object to cache the logger for the request lifecycle,
    reducing database connection overhead.

    Args:
        tenant_id: Tenant ID

    Returns:
        AuditLogger instance
    """
    from src.core.audit_logger import get_audit_logger

    # Cache key includes tenant_id to handle multi-tenant requests
    cache_key = f"audit_logger_{tenant_id}"

    if not hasattr(g, cache_key):
        setattr(g, cache_key, get_audit_logger("AdminUI", tenant_id))

    return getattr(g, cache_key)


def log_admin_action(
    operation_name: str,
    extract_details: Callable[..., dict[str, Any]] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to log admin UI actions to audit_logs table.

    Args:
        operation_name: Name of the operation (e.g., "update_tenant_settings")
        extract_details: Optional function to extract details from request/response
                        Signature: extract_details(result, **kwargs) -> dict

    Usage:
        @log_admin_action("update_tenant_settings")
        def update_settings(tenant_id):
            # ... implementation ...
            return result

        @log_admin_action("create_product", extract_details=lambda r, **kw: {"product_id": kw.get("product_id")})
        def create_product(tenant_id, product_id):
            # ... implementation ...
            return result
    """

    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(f)
        def decorated_function(*args: Any, **kwargs: Any) -> Any:
            # Get user from session
            user_info = session.get("user", {})
            if isinstance(user_info, dict):
                user_email: str = user_info.get("email", "unknown")
            else:
                user_email = str(user_info) if user_info else "unknown"

            # Get tenant_id from kwargs (most admin routes have this)
            tenant_id: str | None = kwargs.get("tenant_id")

            # Call the actual route function
            result: Any = None
            success: bool = True
            error_message: str | None = None

            try:
                result = f(*args, **kwargs)
            except Exception as e:
                # Log failed actions too
                success = False
                error_message = str(e)
                # Re-raise to let Flask handle it
                raise
            finally:
                # Log the admin action (even if it failed)
                if tenant_id:
                    try:
                        # Use cached audit logger to reduce DB connections
                        audit_logger = _get_or_create_audit_logger(tenant_id)

                        # Extract additional details if provided
                        details: dict[str, Any] = {
                            "user": user_email,
                            "action": operation_name,
                            "method": request.method,
                        }

                        if extract_details and callable(extract_details):
                            try:
                                extracted = extract_details(result, **kwargs)
                                if isinstance(extracted, dict):
                                    details.update(extracted)
                            except Exception as e:
                                logger.warning(f"Failed to extract details for {operation_name}: {e}")

                        # Add request data for POST requests (sanitized)
                        if request.method == "POST":
                            safe_fields = _extract_safe_request_data()
                            if safe_fields:
                                details["request_data"] = safe_fields

                        audit_logger.log_operation(
                            operation=operation_name,
                            principal_name=user_email,
                            principal_id=user_email,
                            adapter_id="admin_ui",
                            success=success,
                            details=details,
                            error=error_message,
                            tenant_id=tenant_id,
                        )
                    except Exception as e:
                        # Don't fail the request if audit logging fails
                        logger.warning(f"Failed to write admin audit log for {operation_name}: {e}")

            return result

        return decorated_function

    return decorator
