"""Admin UI auth dependencies: ``AdminUser``, ``AdminRedirect``, Annotated aliases.

Sync L0-L4 foundation per ``.claude/notes/flask-to-fastapi/CLAUDE.md`` Invariant #4.
Replaces Flask decorators:

- ``@require_auth(admin_only=True)``       → ``AdminUserDep`` / ``SuperAdminDep``
- ``@require_tenant_access()``             → ``CurrentTenantDep`` (HTML, in ``tenant.py``)
- ``@require_tenant_access(api_mode=True)``→ ``CurrentTenantJsonDep`` (JSON)
- ``flask.g.user``                         → injected via the Annotated alias

Canonical spec: ``flask-to-fastapi-foundation-modules.md §11.4``.

Relationship to ``ResolvedIdentity`` (``src/core/resolved_identity.py``):
    ``ResolvedIdentity`` is MCP/A2A/REST-API identity (principal-centric, token-based).
    ``AdminUser`` is admin-UI identity (human, session-cookie-based).
    They are DISTINCT — an admin UI user has no ``principal_id``, and an MCP principal
    has no admin role. Handlers that need BOTH construct a ``ResolvedIdentity``
    separately at the handler level; no cross-pollination at the dep layer.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from starlette.requests import Request

from src.core.database.database_session import get_db_session
from src.core.database.models import TenantManagementConfig

logger = logging.getLogger(__name__)

Role = Literal["super_admin", "tenant_admin", "tenant_user", "test"]


@dataclass(frozen=True)
class AdminUser:
    """Immutable admin-UI identity.

    ``email`` is always lowercased at construction time — centralizing that here
    removes 40+ ``.lower()`` calls across routers. ``is_test_user`` is set only
    when ``ADCP_AUTH_TEST_MODE=true`` AND the session contains a ``test_user``
    key; the flag enables the test-fixture bypass path in ``CurrentTenantDep``.
    """

    email: str
    role: Role
    is_test_user: bool = False

    def __post_init__(self) -> None:
        if self.email != self.email.lower():
            object.__setattr__(self, "email", self.email.lower())


class AdminRedirect(Exception):
    """Raised by admin deps to signal 303 redirect to login.

    Caught by an app-level exception handler. An exception (not a ``Response``
    return) is necessary because FastAPI deps cannot return responses — they
    must raise.
    """

    def __init__(self, to: str, next_url: str = "") -> None:
        super().__init__(f"redirect to {to}")
        self.to = to
        self.next_url = next_url


class AdminAccessDenied(Exception):
    """Raised when a user is authenticated but lacks tenant access.

    Distinct from ``HTTPException(403)`` so the app-level handler can render a
    templated 403 page for HTML routes rather than returning JSON.
    """

    def __init__(self, message: str = "Access denied") -> None:
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_email(raw: Any) -> str:
    """Safely extract an email from the session's ``user`` field.

    Legacy Flask code stored either a string email or a dict with an ``email``
    key (OAuth claims). Handle both; return "" for anything else.
    """
    if isinstance(raw, dict):
        return str(raw.get("email") or "").strip().lower()
    if isinstance(raw, str):
        return raw.strip().lower()
    return ""


def is_super_admin(email: str) -> bool:
    """Check super-admin status.

    Resolution order mirrors ``src/admin/utils/helpers.py:132``:

    1. ``SUPER_ADMIN_EMAILS`` env var (comma list of exact emails).
    2. ``SUPER_ADMIN_DOMAINS`` env var (comma list of domains).
    3. ``TenantManagementConfig`` row ``super_admin_emails`` (db fallback).
    4. ``TenantManagementConfig`` row ``super_admin_domains``.

    Sync at L0-L4 (Invariant #4). Opens its own short-lived
    ``with get_db_session()`` — this is the ONE helper where nested session
    opening is tolerated because the caller chain precedes request scope and
    has no ``SessionDep`` to thread through. NO session-level caching (the
    Flask version cached, causing staleness after env var changes).
    """
    if not email:
        return False
    email_l = email.lower()
    domain = email_l.split("@", 1)[1] if "@" in email_l else ""

    env_emails = {e.strip().lower() for e in os.environ.get("SUPER_ADMIN_EMAILS", "").split(",") if e.strip()}
    if email_l in env_emails:
        return True

    env_domains = {d.strip().lower() for d in os.environ.get("SUPER_ADMIN_DOMAINS", "").split(",") if d.strip()}
    if domain and domain in env_domains:
        return True

    try:
        with get_db_session() as db:
            emails_cfg = db.scalars(select(TenantManagementConfig).filter_by(config_key="super_admin_emails")).first()
            if emails_cfg and emails_cfg.config_value:
                db_emails = {e.strip().lower() for e in emails_cfg.config_value.split(",") if e.strip()}
                if email_l in db_emails:
                    return True

            domains_cfg = db.scalars(select(TenantManagementConfig).filter_by(config_key="super_admin_domains")).first()
            if domains_cfg and domains_cfg.config_value:
                db_domains = {d.strip().lower() for d in domains_cfg.config_value.split(",") if d.strip()}
                if domain and domain in db_domains:
                    return True
    except Exception as e:
        logger.warning("is_super_admin DB check failed, env result used: %s", e)

    return False


def _get_admin_user_or_none(request: Request) -> AdminUser | None:
    """Read the session and produce an ``AdminUser``, or ``None`` if unauthenticated.

    Test-mode bypass: when ``ADCP_AUTH_TEST_MODE=true`` AND session contains a
    ``test_user`` key, construct an ``AdminUser`` with ``is_test_user=True``
    and ``role=session["test_user_role"]``. This is the ONLY place the test
    bypass is honored — ``CurrentTenantDep`` trusts ``is_test_user`` without
    re-checking.

    Both the env var AND the session key must be set. Neither alone is
    sufficient — this prevents stale test session cookies from granting access
    after ``ADCP_AUTH_TEST_MODE`` is flipped off.
    """
    try:
        session = request.session
    except AssertionError:
        # SessionMiddleware not installed (unit test without middleware).
        return None

    test_mode = os.environ.get("ADCP_AUTH_TEST_MODE", "").lower() == "true"
    if test_mode and "test_user" in session:
        email = _extract_email(session["test_user"])
        if not email:
            return None
        role = session.get("test_user_role", "tenant_user")
        if role not in ("super_admin", "tenant_admin", "tenant_user", "test"):
            role = "tenant_user"
        return AdminUser(email=email, role=role, is_test_user=True)

    raw = session.get("user")
    if raw is None:
        return None
    email = _extract_email(raw)
    if not email:
        return None
    role_resolved: Role = "super_admin" if is_super_admin(email) else "tenant_user"
    return AdminUser(email=email, role=role_resolved, is_test_user=False)


# ---------------------------------------------------------------------------
# Public deps
# ---------------------------------------------------------------------------


def get_admin_user_optional(request: Request) -> AdminUser | None:
    """Return ``AdminUser`` if authenticated, else ``None``.

    Used by handlers that render differently for anonymous vs authenticated
    users (e.g., landing pages with login link).
    """
    return _get_admin_user_or_none(request)


def get_admin_user(request: Request) -> AdminUser:
    """Return ``AdminUser`` or raise ``AdminRedirect`` to login."""
    user = _get_admin_user_or_none(request)
    if user is None:
        raise AdminRedirect(to="/admin/login", next_url=str(request.url))
    return user


def get_admin_user_json(request: Request) -> AdminUser:
    """Same as ``get_admin_user`` but raises ``HTTPException(401)`` for JSON endpoints."""
    user = _get_admin_user_or_none(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user


AdminUserDep = Annotated[AdminUser, Depends(get_admin_user)]
AdminUserJsonDep = Annotated[AdminUser, Depends(get_admin_user_json)]
AdminUserOptional = Annotated[AdminUser | None, Depends(get_admin_user_optional)]


def require_super_admin(user: AdminUserDep) -> AdminUser:
    """Block non-super-admin callers with 403 (HTML chain)."""
    if user.role != "super_admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin required")
    return user


def require_super_admin_json(user: AdminUserJsonDep) -> AdminUser:
    """Block non-super-admin callers with 403 (JSON chain — 401 on anon)."""
    if user.role != "super_admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin required")
    return user


SuperAdminDep = Annotated[AdminUser, Depends(require_super_admin)]
SuperAdminJsonDep = Annotated[AdminUser, Depends(require_super_admin_json)]


# Canonical names used by L0 plan terminology:
CurrentUserDep = AdminUserDep
RequireAdminDep = AdminUserDep  # require_auth equivalent — any authenticated admin user
RequireSuperAdminDep = SuperAdminDep
