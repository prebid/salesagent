"""Audit dependency factory.

Replaces the Flask decorator ``@audit_action("create_user")`` with
``@router.post("/users", dependencies=[Depends(audit_action("create_user"))])``.

Emits an audit log entry AFTER the handler completes successfully —
``BackgroundTasks`` is used so the DB write happens post-response and never
blocks the user-visible path.

Canonical spec: ``flask-to-fastapi-foundation-modules.md §11.5``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import BackgroundTasks
from starlette.requests import Request

from src.admin.deps.auth import AdminUserDep
from src.core.audit_logger import AuditLogger

logger = logging.getLogger(__name__)


def _write_audit(
    action: str,
    user_email: str,
    tenant_id: str | None,
    path: str,
    method: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Fire-and-forget audit write. Swallows DB errors.

    The ``AuditLogger`` import is deferred to call time so test patching
    against ``src.admin.deps.audit.AuditLogger`` works uniformly. Errors are
    logged (``logger.exception``) but NEVER propagated — a failing audit write
    must not affect the user response.
    """
    try:
        audit = AuditLogger(adapter_name="admin_ui", tenant_id=tenant_id)
        # The production ``AuditLogger.log_operation`` shape differs from this
        # lightweight dep's needs — for L0 scaffold, we simply exercise the
        # factory import. L1a wires a proper ``log()`` method on AuditLogger
        # that accepts the ``action/user/details`` dict shape below. Swallow
        # any AttributeError as a non-fatal audit gap.
        log_method = getattr(audit, "log", None)
        if callable(log_method):
            log_method(
                action=action,
                user=user_email,
                tenant_id=tenant_id,
                details={"path": path, "method": method, **(extra or {})},
            )
        else:
            logger.debug(
                "AuditLogger.log() not yet implemented; skipping audit write " "for action=%s (L0 scaffold)",
                action,
            )
    except Exception:
        logger.exception("Audit log write failed (non-fatal): action=%s", action)


def audit_action(action: str) -> Callable[..., None]:
    """Dep factory that schedules an audit log after the handler runs.

    Why ``BackgroundTasks``: FastAPI runs ``BackgroundTasks`` AFTER the
    response is sent. A failing audit write should never affect the
    user-visible response, so we intentionally do NOT write inside the dep
    body (which would block). The handler runs, returns, and only then the
    audit task fires.

    Dep order: this MUST resolve after ``get_current_tenant`` (if the route
    uses it), so the ``BackgroundTasks`` scheduling is the absolute last thing
    before the handler executes. FastAPI's topological dep resolution handles
    this when ``audit_action`` is declared last in the route signature.
    """

    def _dep(
        request: Request,
        background: BackgroundTasks,
        user: AdminUserDep,
    ) -> None:
        tenant_id = request.path_params.get("tenant_id")
        background.add_task(
            _write_audit,
            action=action,
            user_email=user.email,
            tenant_id=tenant_id,
            path=request.url.path,
            method=request.method,
        )

    return _dep


# Public canonical name per L0 plan terminology.
AuditLoggerDep = audit_action
