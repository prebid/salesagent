"""Admin UI action logging decorator.

Automatically logs admin UI actions to the audit_logs table for compliance and visibility.

DECORATOR ORDER CONVENTION:
    @route_decorator
    @require_tenant_access()  # First: Check authorization
    @log_admin_action()       # Second: Log authorized actions only

This order ensures we only log actions by authorized users.
"""

import logging
from collections.abc import Callable
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


_HANDLED_FAILURE_KEY = "_admin_action_failure"


def record_admin_action_failure(error: object) -> None:
    """Mark the current admin action as FAILED even though the handler returns normally.

    A handler that catches its own domain refusal, flashes it, and returns a
    redirect never raises — so :func:`log_admin_action`'s ``except`` never fires
    and ``success`` stays ``True``. The refusal is then written to the audit log
    as a successful admin action.

    That is not hypothetical: ``register_webhook`` refuses an SSRF-blocked URL
    and returns a friendly redirect, and both that shape and its predecessor (a
    return-code check with the same early return) audited as successes.

    Raising instead would be the other fix, and is wrong here: the operator
    should get a flash, not a 500. So the handler says so explicitly.
    """
    g.__setattr__(_HANDLED_FAILURE_KEY, error)


def _apply_handled_failure(success: bool, error_message: str | None) -> tuple[bool, str | None]:
    """Fold a handler-declared failure into the outcome the audit row records.

    Kept out of the wrapper so the branch does not push ``log_admin_action`` past
    the C901 ceiling the repo ratchets (ADR-009 / GH #1610).
    """
    handled = getattr(g, _HANDLED_FAILURE_KEY, None)
    if handled is None:
        return success, error_message
    g.__delattr__(_HANDLED_FAILURE_KEY)
    return False, str(handled)


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

            # The tenant this action belongs to, resolved so it survives either decorator
            # ORDER. Flask hands URL parameters to the OUTERMOST wrapper as kwargs, so
            # reading only kwargs works when this decorator is outermost and silently
            # returns None when it is not: require_tenant_access calls
            # ``f(tenant_id, *args, **kwargs)`` positionally, so tenant_id arrives in args.
            # Since the row is written only ``if tenant_id``, that produced NO audit row at
            # all rather than a wrong one — auditing off, silently, on every route that put
            # authorization outside (which is the order this module's own docstring asks
            # for). request.view_args holds the URL parameters whatever the wrapper chain
            # did with them, so it is the reliable source.
            tenant_id: str | None = kwargs.get("tenant_id") or (request.view_args or {}).get("tenant_id")

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
                # A handler that CAUGHT its own refusal and returned normally never
                # reaches the except above. Without this, such a refusal audits as a
                # success — see record_admin_action_failure.
                success, error_message = _apply_handled_failure(success, error_message)

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
