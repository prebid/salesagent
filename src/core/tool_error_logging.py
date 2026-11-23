"""Boundary error recording, and the carrier MCP raises a failure through.

``record_boundary_error`` is what every transport's failure passes through on the way to the
server-side sinks: the stdlib log, the activity feed and the audit log. ``AdCPToolError`` is
MCP's wire marker for a failure -- the response body, raised as the ``ToolError`` FastMCP
renders as ``isError=True``.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from src.core.errors.codes import Recovery
from src.core.exceptions import AdCPSalesAgentError

if TYPE_CHECKING:
    from src.core.resolved_identity import PublicIdentity

logger = logging.getLogger(__name__)


class AdCPToolError(ToolError):
    """MCP's wire marker for a failure: the response body, raised.

    FastMCP renders ``raise <ToolError>`` as
    ``CallToolResult(isError=True, content=[TextContent(text=str(error))])``, so ``str(self)``
    is the JSON-encoded body and a buyer parses ``content[0].text`` to read either
    ``adcp_error.code`` or ``errors[0].code``. The body is also exposed as ``self.envelope``
    for a reader that already holds the exception.
    """

    def __init__(self, envelope: dict[str, Any]):
        self.envelope = envelope
        super().__init__()

    def __str__(self) -> str:
        return json.dumps(self.envelope)


def extract_error_info(error: Exception) -> tuple[str, str, Recovery | None]:
    """The (code, message, recovery) an exception carries, for the server-side record.

    An ``AdCPToolError`` carries them in its body; an ``AdCPSalesAgentError`` derives them from
    its code; anything else is recorded under its type name.
    """
    if isinstance(error, AdCPToolError):
        first = error.envelope["errors"][0]
        return first["code"], first.get("message", ""), _coerce_recovery(first.get("recovery"))
    if isinstance(error, AdCPSalesAgentError):
        return error.error_code, error.message, error.recovery
    return type(error).__name__, str(error), None


def _coerce_recovery(value: object) -> Recovery | None:
    """``value`` as a ``Recovery`` when it is one of the three wire strings, else ``None``."""
    if not isinstance(value, str):
        return None
    try:
        return Recovery(value)
    except ValueError:
        return None


def record_boundary_error(
    transport: str,
    operation: str,
    error: Exception,
    *,
    identity: PublicIdentity | None = None,
) -> None:
    """Record an error at a transport boundary uniformly across MCP/A2A/REST.

    Args:
        transport: ``"mcp"``, ``"a2a"``, or ``"rest"`` -- the audit logger's source string.
        operation: Tool/skill/route name.
        error: The exception that fired at the boundary.
        identity: The resolved caller, when the boundary had one. Without it the activity
            feed and audit log are skipped; the log line still captures the error.

    Behavior:
        1. stdlib logger: WARNING for a typed ``AdCPSalesAgentError`` (the buyer-correctable
           path), ERROR with ``exc_info=True`` for an untyped exception so on-call sees the
           traceback.
        2. ``activity_feed.log_error`` (when ``tenant_id`` present), so the operator UI
           surfaces the error in real time.
        3. ``get_audit_logger(transport.upper(), tenant_id).log_operation`` (when
           ``tenant_id`` present), the persistent record.

    Every sink is wrapped: an observability failure cannot replace the buyer's original
    error. Sink failures log at WARNING so a quiet outage in audit infrastructure is findable.
    """
    error_code, error_message, _recovery = extract_error_info(error)
    transport_upper = transport.upper()
    tenant_id = identity.tenant_id if identity is not None else None
    principal_id = identity.principal_id if identity is not None else None

    if isinstance(error, AdCPSalesAgentError):
        # A typed error is the buyer-correctable path, so WARNING. This is the ONE
        # record of what broke underneath: the traceback is attached when the error
        # carries a cause, either raised ``from`` it (``__cause__``) or handed to
        # ``internal_detail`` without ``from`` (``with_retry`` re-raised a mapped
        # error bare), and ``exc_info`` on the error prints its ``__cause__`` or
        # ``__context__`` chain, so the caught exception is in the record either way.
        # Nothing logs ``internal_detail`` separately: ``adcp_error_for`` used to write
        # it a second time at ERROR, and one cause was two tracebacks
        # (salesagent-3cs7o.24). Without a cause of either kind there is nothing a
        # traceback adds.
        #
        # ``field`` and ``details`` are named because ``message`` alone cannot say
        # WHICH refusal this was. Post-ADR-010 the message is a read-only property over
        # CODE_TABLE -- a function of the CODE, not of the raise site -- so every one of
        # ``create_media_buy``'s twenty-odd AdCPValidationError sites logs the identical
        # sentence, and a conformance failure reading "Request contains invalid field
        # values" names nothing anyone can act on (measured: four media_buy storyboard
        # checks failing indistinguishably, salesagent-basxl). The two values that DO
        # vary are already wire-bound -- ``field`` is the error's field pointer and
        # ``details`` its typed details payload -- so this adds no exposure, only the
        # ability to tell one refusal from another. ONE site, so every tool and every
        # transport gets it; instrumenting raise sites would be the same log twenty
        # times.
        logger.warning(
            "%s boundary translating %s to envelope: %s - %s (operation=%s field=%s details=%s)",
            transport_upper,
            type(error).__name__,
            error_code,
            error_message,
            operation,
            error.field,
            error.details,
            exc_info=error if (error.__cause__ is not None or error.internal_detail is not None) else None,
        )
    else:
        logger.error(
            "%s boundary untyped %s: %s (operation=%s)",
            transport_upper,
            type(error).__name__,
            error_message,
            operation,
            exc_info=True,
        )

    if not tenant_id:
        return

    try:
        from src.services.activity_feed import activity_feed

        activity_feed.log_error(
            tenant_id=tenant_id,
            principal_name=principal_id or "anonymous",
            error_message=f"{operation}: {error_message}",
            error_code=error_code,
        )
    except Exception as e:
        logger.warning("Failed to log %s error to activity feed: %s", transport_upper, e)

    try:
        from src.core.audit_logger import get_audit_logger

        audit_logger = get_audit_logger(transport_upper, tenant_id)
        audit_logger.log_operation(
            operation=operation,
            principal_name=principal_id or "anonymous",
            principal_id=principal_id or "anonymous",
            adapter_id=f"{transport}_boundary",
            success=False,
            error=error_message,
        )
    except Exception as e:
        logger.warning("Failed to log %s error to audit log: %s", transport_upper, e)
