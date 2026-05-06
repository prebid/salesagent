"""Embedded-mode auth bypass for per-tenant admin UI routes.

When ``MANAGED_INSTANCE=true``, requests to ``/tenant/<id>/*`` may be
authenticated via the ``X-Identity-*`` headers forwarded by the upstream
proxy (Scope3 Storefront, etc.) instead of the salesagent's Google
OAuth flow. Two tenant fields drive the decision:

- ``external_org_id`` — when set, embedded auth is *available* for the
  tenant. The header ``X-Identity-Org-Id`` must match this value.
- ``is_embedded`` — when ``True``, embedded auth is *required* (OAuth
  fallback is rejected). When ``False``, embedded auth is offered when
  headers are present but OAuth still works for header-less requests.

This lets a tenant migrate from OAuth to embedded gradually: set
``external_org_id`` first to enable preview via the storefront iframe
while production OAuth users keep working unchanged, then flip
``is_embedded=True`` once cut over.

Failure modes match the published contract at
``docs/integration/managed-mode-identity-contract.md``:

- ``is_embedded=True`` + missing/incomplete headers → 403 ``identity_required``
- Headers present but malformed (invalid role, etc.) → 403 ``identity_required``
  (always — never falls through to OAuth, which would mask the malformed
  header set as anonymous)
- ``X-Identity-Org-Id`` doesn't match the tenant's ``external_org_id``
  → 403 ``identity_org_mismatch``
- ``is_embedded=False`` + no headers → falls through to Google OAuth
- ``external_org_id`` unset → embedded mode unavailable, always OAuth

The role enum (``admin | member | viewer``) is parsed but not yet
enforced for fine-grained authorization — sprint 4 hardening will scope
nav and hide platform-config pages by role. Today any valid role grants
the same level of access (UI sees the user as authenticated for that
tenant).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select

from src.admin.middleware.identity_propagation import (
    InvalidPropagatedIdentity,
    PropagatedIdentity,
    read_identity_from_request,
)
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbeddedAuthOk:
    """Bypass succeeded — caller should populate ``g.user`` and proceed."""

    identity: PropagatedIdentity
    tenant_id: str


@dataclass(frozen=True)
class EmbeddedAuthDeny:
    """Bypass rejected — caller should return ``(error, message, 403)``."""

    error: Literal["identity_required", "identity_org_mismatch", "identity_role_invalid"]
    message: str


@dataclass(frozen=True)
class EmbeddedAuthPassthrough:
    """Embedded-mode doesn't apply — caller should run normal OAuth checks."""


EmbeddedAuthResult = EmbeddedAuthOk | EmbeddedAuthDeny | EmbeddedAuthPassthrough


def is_managed_instance() -> bool:
    """``MANAGED_INSTANCE=true`` toggles the contract on globally.

    Re-read on every check so tests can flip the env var without process
    restart. Cheap — single env var lookup.
    """
    return os.environ.get("MANAGED_INSTANCE", "").lower() == "true"


def _load_tenant(tenant_id: str) -> Tenant | None:
    with get_db_session() as session:
        return session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()


def authorize_embedded_request(request, tenant_id: str) -> EmbeddedAuthResult:
    """Resolve embedded-mode auth for a per-tenant admin UI request.

    ``request`` must expose Flask's request shape (``.headers`` is the
    only attribute used; the helper is decoupled from Flask).

    Returns:
        - ``EmbeddedAuthPassthrough`` when ``MANAGED_INSTANCE`` is unset OR
          the tenant isn't embedded — caller falls through to
          its normal auth path.
        - ``EmbeddedAuthDeny`` when the tenant IS embedded but the headers
          are missing, malformed, or claim a different org. Caller MUST
          return 403 with the named error code.
        - ``EmbeddedAuthOk`` when the headers authorize this request.
          Caller populates ``g.user`` and proceeds.
    """
    if not is_managed_instance():
        return EmbeddedAuthPassthrough()

    tenant = _load_tenant(tenant_id)
    if tenant is None:
        # Not our concern — let the route handler 404 / 403 as it sees fit.
        return EmbeddedAuthPassthrough()

    # Stash on Flask ``g`` so ``_maybe_block_embedded_write`` and any other
    # request-scoped consumer can reuse the load. The instance is detached
    # from its session by the time it lands here; callers reading scalar
    # columns are fine, but they must not lazy-load relationships.
    try:
        from flask import g, has_request_context

        if has_request_context():
            g.tenant = tenant
    except RuntimeError:  # pragma: no cover — has_request_context guards this
        pass

    if tenant.external_org_id is None:
        # Tenant has no upstream org binding, so X-Identity-Org-Id can't
        # be matched. Embedded mode is unavailable; fall through to OAuth.
        return EmbeddedAuthPassthrough()

    is_embedded_required = bool(getattr(tenant, "is_embedded", False))

    try:
        identity = read_identity_from_request(request)
    except InvalidPropagatedIdentity as exc:
        # Malformed headers reach this branch only when the caller
        # *attempted* to use embedded auth — never silently fall through
        # to OAuth, that would mask the bad header set.
        return EmbeddedAuthDeny(error="identity_required", message=str(exc))

    if identity is None:
        if is_embedded_required:
            return EmbeddedAuthDeny(
                error="identity_required",
                message=(
                    f"Tenant {tenant_id!r} is is_embedded — request must include "
                    "X-Identity-Email, X-Identity-Org-Id, X-Identity-Role, X-Identity-Source headers."
                ),
            )
        # Embedded-optional tenant + no headers → caller didn't ask for
        # embedded auth. Hand off to the OAuth path.
        return EmbeddedAuthPassthrough()

    if identity.org_id != tenant.external_org_id:
        return EmbeddedAuthDeny(
            error="identity_org_mismatch",
            message=(
                f"X-Identity-Org-Id {identity.org_id!r} does not match tenant {tenant_id!r}'s "
                f"external_org_id {tenant.external_org_id!r}."
            ),
        )

    return EmbeddedAuthOk(identity=identity, tenant_id=tenant_id)


def is_embedded_view(tenant: Tenant | None = None) -> bool:
    """Whether the current request is operating in an embedded view.

    Two independent signals — either is sufficient:

    1. ``g.user["embedded_mode"]`` — set by ``synthetic_user_dict`` when
       the request was authorized via ``X-Identity-*`` headers. This is
       the per-request signal that powers *preview mode*: an
       ``is_embedded=False`` tenant accessed through the storefront
       iframe should be treated as embedded for that request without
       flipping the tenant flag.
    2. ``tenant.is_embedded`` — the persistent tenant flag. Covers test
       harnesses that bypass ``require_tenant_access`` (no synthetic
       user) and any other path where the per-request signal is missing
       but the tenant is permanently embedded.

    In production, condition 2 is a strict subset of condition 1 because
    ``is_embedded=True`` requires header auth (see
    ``authorize_embedded_request``), so a request reaching a view function
    on such a tenant always has ``embedded_mode`` set. The fallback exists
    for test-mode and defense-in-depth.

    Used for two kinds of decisions where the per-request signal is the
    correct policy input:

    - **Rendering**: chrome stripping, hiding edit affordances, lock-banner
      pages. The page should look embedded for the duration of this
      request.
    - **Mutation gating**: ``require_tenant_access`` blocks
      POST/PUT/DELETE/PATCH on embedded views because the upstream platform
      owns the tenant's state. Preview requests must be blocked too — if
      we used only ``tenant.is_embedded``, a header-auth caller could POST
      against a non-embedded tenant whose preview the storefront is
      serving.

    Do NOT use this for purely *static* tenant properties (e.g., the API
    output that describes the tenant's persisted configuration): those
    should continue to read ``tenant.is_embedded`` directly.
    """
    from flask import g, has_request_context

    if has_request_context():
        user = getattr(g, "user", None)
        if isinstance(user, dict) and bool(user.get("embedded_mode")):
            return True

    if tenant is not None and bool(getattr(tenant, "is_embedded", False)):
        return True
    return False


def synthetic_user_dict(identity: PropagatedIdentity) -> dict[str, object]:
    """Build the ``g.user`` dict that downstream code reads.

    Matches the shape Google OAuth produces (``email`` key) plus
    embedded-mode breadcrumbs so audit logs can distinguish the auth
    source. This dict is request-scoped — never written to the Flask
    session cookie, since embedded-mode auth is stateless.
    """
    return {
        "email": identity.email,
        "embedded_mode": True,
        "org_id": identity.org_id,
        "role": identity.role,
        "source": identity.source,
        "user_id": identity.user_id,
    }
