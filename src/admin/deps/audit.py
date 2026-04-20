"""L0-12 STUB (Red commit) — real implementation lands in Green commit.

Canonical spec: flask-to-fastapi-foundation-modules.md §11.5.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import BackgroundTasks
from starlette.requests import Request

from src.admin.deps.auth import AdminUserDep


def _write_audit(
    action: str,
    user_email: str,
    tenant_id: str | None,
    path: str,
    method: str,
    extra: dict[str, Any] | None = None,
) -> None:  # pragma: no cover
    """STUB — no-op."""
    return None


def audit_action(action: str) -> Callable[..., None]:
    """STUB — returns a dep that does nothing (no BackgroundTasks scheduling)."""

    def _dep(
        request: Request,
        background: BackgroundTasks,
        user: AdminUserDep,
    ) -> None:
        return None

    return _dep


AuditLoggerDep = audit_action
