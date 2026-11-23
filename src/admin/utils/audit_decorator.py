"""Admin UI action logging decorator.

Automatically logs admin UI actions to the audit_logs table for compliance and visibility.

DECORATOR ORDER CONVENTION:
    @route_decorator
    @require_tenant_access()  # First: Check authorization
    @log_admin_action()       # Second: Log authorized actions only

This order ensures we only log actions by authorized users.
"""

import re
from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import g, request, session

from src.core.exceptions import AdCPSalesAgentError
from src.core.operator_errors import best_effort
from src.core.webhook_validator import UNPARSEABLE_WEBHOOK_URL_FOR_LOG, url_for_record

#: What the audit row of an action that raised records. The exception itself is logged
#: under a reference by the admin error handler; its text never reaches the audit log.
_RAISED_MESSAGE = "The action failed with an error; the server log has the details"

# Which field names mark a secret. Names are normalised first (see _normalize_field_name),
# then a name is sensitive if it CONTAINS one of these...
_SENSITIVE_SUBSTRINGS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "credential",
    "webhook",
    "api_key",
    "apikey",
    "private_key",
    "key_json",
    "keyfile",
    "service_account",
    "authorization",
    "bearer",
    "cookie",
    "signature",
    "database_url",
    "connection_string",
)

# ...or has one of these as a whole ``_``-separated word, so a short marker such as ``key``
# or ``dsn`` does not match inside an unrelated word (``keyword``, ``adsnetwork``).
_SENSITIVE_WORDS = frozenset({"key", "auth", "oauth", "cert", "certificate", "dsn", "jwt", "pwd"})

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

_MAX_VALUE_LENGTH = 100
# Deliberate ceilings: an audit row is a summary of what was submitted, not a copy of it.
# Containers nested deeper than this, and items past the cap, are left out.
_MAX_DEPTH = 4
_MAX_ITEMS = 50


def _normalize_field_name(field_name: str) -> str:
    """``xApiKey``, ``X-Api-Key`` and ``x api key`` all become ``x_api_key``."""
    return re.sub(r"[-\s.]+", "_", _CAMEL_BOUNDARY.sub("_", field_name.strip())).lower()


def _is_sensitive_field(field_name: str) -> bool:
    """Whether a field with this name may hold a secret."""
    name = _normalize_field_name(field_name)
    return any(part in name for part in _SENSITIVE_SUBSTRINGS) or not _SENSITIVE_WORDS.isdisjoint(name.split("_"))


def _sanitize_value(value: Any) -> Any:
    """A scalar as an audit row may hold it.

    ``None``, booleans and numbers are kept. Anything else becomes a string cut to 100
    characters, and an http(s) URL is first reduced by the one URL reducer
    (:func:`~src.core.webhook_validator.url_for_record`).
    """
    if value is None or isinstance(value, bool | int | float):
        return value
    text = str(value).strip()
    if text.lower().startswith(("http://", "https://")):
        text = url_for_record(text, keep_path=False) or UNPARSEABLE_WEBHOOK_URL_FOR_LOG
    return text[:_MAX_VALUE_LENGTH]


def _redact(value: Any, depth: int = 0) -> Any:
    """*value* with every sensitive-named key dropped at every level of nesting.

    A container is walked, never ``str()``-ed: the repr of a dict carries its secret
    values whatever its own key is called. Scalars go through :func:`_sanitize_value`.
    """
    if isinstance(value, dict):
        if depth >= _MAX_DEPTH:
            return "[omitted]"
        return {
            str(key): _redact(item, depth + 1)
            for key, item in list(value.items())[:_MAX_ITEMS]
            if not _is_sensitive_field(str(key))
        }
    if isinstance(value, list | tuple):
        if depth >= _MAX_DEPTH:
            return "[omitted]"
        return [_redact(item, depth + 1) for item in value[:_MAX_ITEMS]]
    return _sanitize_value(value)


def _declared_request_fields(audit_fields: tuple[str, ...]) -> dict[str, Any]:
    """The submitted values of the fields a route named in ``audit_fields``, and nothing else.

    The body is NOT copied by default: what reaches a row every tenant user reads is what the
    route declared, so an undeclared field, whatever its name, never gets there.
    """
    if not audit_fields:
        return {}
    if request.form:
        submitted: Any = request.form
    elif request.is_json:
        submitted = request.get_json(silent=True)
    else:
        return {}
    # A JSON body that is not an object (a list, a string) has no fields to pick.
    if not hasattr(submitted, "get"):
        return {}
    return {name: submitted.get(name) for name in audit_fields if name in submitted}


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


def record_admin_action_failure(error: AdCPSalesAgentError | str) -> None:
    """Mark the current admin action as FAILED even though the handler returns normally.

    The audit row (shown in the admin UI) records ``str(error)``: a typed error's text is
    its code's ``CODE_TABLE`` sentence, and a ``str`` is the handler's own sentence. No
    other exception is accepted, because its text is not ours to show.

    A handler that catches its own domain refusal, flashes it, and returns a
    redirect never raises — so :func:`log_admin_action` sees it return
    and ``success`` becomes ``True``. The refusal is then written to the audit log
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
    audit_fields: tuple[str, ...] = (),
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to log admin UI actions to audit_logs table.

    Every tenant user, viewers included, reads the rows this writes, so nothing from the
    request body is stored unless the route names it, and the assembled ``details`` are
    redacted once (secret-named keys dropped at every level, URLs reduced) before writing.

    Args:
        operation_name: Name of the operation (e.g., "update_tenant_settings")
        extract_details: Optional function to extract details from request/response
                        Signature: extract_details(result, **kwargs) -> dict
        audit_fields: Request fields (form or top-level JSON) to copy into
                      ``details["request_data"]`` on a POST. Empty by default: no body.

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

            # Call the actual route function. It failed unless it returned: what it raised
            # goes on to the admin error handler, which logs it, and the audit row (shown in
            # the admin UI) records fixed text rather than the exception's.
            result: Any = None
            success: bool = False
            error_message: str | None = _RAISED_MESSAGE

            try:
                result = f(*args, **kwargs)
                success, error_message = True, None
            finally:
                # A handler that CAUGHT its own refusal returned normally, so the block
                # above marked it a success. Without this, such a refusal audits as a
                # success — see record_admin_action_failure.
                success, error_message = _apply_handled_failure(success, error_message)

                # Log the admin action (even if it failed). Auditing never fails the request.
                if tenant_id:
                    with best_effort(f"Could not write the admin audit log for {operation_name}"):
                        # Use cached audit logger to reduce DB connections
                        audit_logger = _get_or_create_audit_logger(tenant_id)

                        # Extract additional details if provided
                        details: dict[str, Any] = {
                            "user": user_email,
                            "action": operation_name,
                            "method": request.method,
                        }

                        if extract_details and callable(extract_details):
                            with best_effort(f"Could not extract audit details for {operation_name}"):
                                extracted = extract_details(result, **kwargs)
                                if isinstance(extracted, dict):
                                    details.update(extracted)

                        if request.method == "POST" and (declared := _declared_request_fields(audit_fields)):
                            details["request_data"] = declared

                        audit_logger.log_operation(
                            operation=operation_name,
                            principal_name=user_email,
                            principal_id=user_email,
                            adapter_id="admin_ui",
                            success=success,
                            details=_redact(details),
                            error=error_message,
                            tenant_id=tenant_id,
                        )

            return result

        return decorated_function

    return decorator
