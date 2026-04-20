"""Admin router factory — assembles the admin ``APIRouter`` that the root
FastAPI app includes at the canonical ``/tenant/{tenant_id}`` prefix.

D1 (2026-04-16) canonical URL routing: admin routes are mounted at a single
canonical prefix ``/tenant/{tenant_id}/...``. ``/admin/*`` is NOT a separate
mount — instead, a pure-ASGI ``LegacyAdminRedirectMiddleware`` (lands at
L1c) 308-redirects ``/admin/<x>/<rest>`` requests to the canonical form.
Rationale: Starlette's ``Router.url_path_for()`` returns the FIRST match by
registration order; a dual ``include_router`` would silently collapse
``url_for`` resolution to one target, making templates fragile. One mount,
one name, deterministic reverse routing.

At L0 the router is EMPTY. Each subsequent layer adds real routers:

  - L1a: public/core routers (login, logout, healthcheck)
  - L1b: auth + OIDC cutover
  - L1c: 8 low-risk HTML routers
  - L1d: 14 medium/high-risk HTML routers + 4 JSON APIs
  - L2 : Flask removal + TrustedHostMiddleware

Invariants held at every layer:

  - ``redirect_slashes=True`` (Invariant #2: Starlette does not accept both
    ``/foo`` and ``/foo/`` without this)
  - ``include_in_schema=False`` (Invariant #2 + adcp-safety.md:141-165:
    admin is NOT part of the AdCP OpenAPI surface)
  - Every route in admin routers carries ``name="admin_<blueprint>_<endpoint>"``
    (Invariant #1: ``url_for()`` is the only reverse-routing mechanism)

Canonical spec: ``flask-to-fastapi-foundation-modules.md §11.10``;
``flask-to-fastapi-adcp-safety.md:141-165``.

Sync L0-L4 per Invariant #4 — router construction has no I/O and stays
framework-agnostic across the migration.
"""

from __future__ import annotations

from fastapi import APIRouter


def build_admin_router() -> APIRouter:
    """Return a fresh admin ``APIRouter`` with the canonical prefix + flags.

    Empty at L0. Subsequent layers include real feature routers via
    ``router.include_router(feature_router.router, prefix="/<feature>")``.
    """
    return APIRouter(
        prefix="/tenant/{tenant_id}",
        tags=["admin"],
        include_in_schema=False,
        redirect_slashes=True,
    )
