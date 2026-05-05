"""Entrypoint: build LazyPlatformRouter + admin mount + serve MCP/A2A.

One Starlette binary serves every surface — no nginx. Layout:

* ``/`` and ``/.well-known/agent-card.json`` → A2A (host root IS the
  A2A surface per AdCP convention)
* ``/mcp`` → MCP transport
* ``/admin/*``, ``/static/*``, ``/auth/*``, ``/tenant/*``, ``/api/*``,
  ``/login``, ``/logout``, ``/health``, ``/metrics``, ``/debug/*``,
  ``/test/*``, ``/create_tenant``, ``/signup`` → Flask admin via
  :class:`a2wsgi.WSGIMiddleware`

Multi-tenancy via the ``Host`` header — Starlette middleware resolves
the tenant before token auth runs.

For development without DNS::

    /etc/hosts:
        127.0.0.1 default.localhost acme.localhost beta.localhost

    PORT=3001 uv run python -m core.main

    # Then connect any AdCP MCP buyer to:
    http://default.localhost:3001/mcp
    # Or any A2A buyer to:
    http://default.localhost:3001/
    # Or browse the admin UI at:
    http://default.localhost:3001/admin/
"""

from __future__ import annotations

import logging
import os
from typing import Any

from a2wsgi import WSGIMiddleware
from adcp.decisioning import (
    DecisioningCapabilities,
    DecisioningPlatform,
    LazyPlatformRouter,
    serve,
)
from adcp.decisioning.capabilities import (
    Account as CapabilitiesAccount,
)
from adcp.decisioning.capabilities import (
    Adcp,
    IdempotencySupported,
    MediaBuy,
    SupportedProtocol,
)
from adcp.server import (
    BearerTokenAuth,
    CallableSubdomainTenantRouter,
    Principal,
    SubdomainTenantMiddleware,
    Tenant,
    auth_context_factory,
)
from sqlalchemy import select

from core.middleware.admin_mount import AdminWSGIMount
from core.platforms.gam import GamPlatform
from core.platforms.mock import MockSellerPlatform
from core.proposal.manager import SalesAgentProposalManager
from core.stores.accounts import SalesagentAccountStore
from src.core.database.database_session import get_db_session
from src.core.database.models import Principal as PrincipalRow
from src.core.database.models import Tenant as TenantRow

logger = logging.getLogger(__name__)


# ---- Tenant resolution (uses adcp PR #544 CallableSubdomainTenantRouter) ----


_BARE_DEV_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0"}


async def _resolve_tenant(host: str) -> Tenant | None:
    """Map a normalized host (lower-cased, port-stripped) to a Tenant.

    Strategies in order:

    1. Bare dev host (``localhost``, ``127.0.0.1``) → ``default`` tenant.
       Lets nginx-fronted dev (no subdomain DNS) keep working without
       requiring buyers to set ``Host: default.localhost``.
    2. Subdomain prefix on a known suffix (``.localhost``,
       ``.localtest.me``, ``.lvh.me``, ``.example.com``) → look up the
       row by ``subdomain``.
    3. Otherwise → look up by ``virtual_host`` (production custom-domain
       path; tenants register their public hostname directly).

    Embedded-mode buyers don't rely on this — they pass the tenant via
    ``X-Identity-*`` headers and the auth chain reads it from there. This
    resolver is for tenant-scoped subdomains and direct dev requests.
    """
    if host in _BARE_DEV_HOSTS:
        with get_db_session() as session:
            row = session.scalars(
                select(TenantRow).filter_by(tenant_id="default", is_active=True)
            ).first()
        if row is None:
            return None
        return Tenant(id=row.tenant_id, display_name=row.name)

    # Strip known dev/prod suffixes; whatever's left is the subdomain.
    # localtest.me / lvh.me are public-DNS aliases for 127.0.0.1 we use
    # in dev because Google OAuth rejects *.localhost ("not a public
    # top-level domain"). example.com is the prod placeholder.
    # SALES_AGENT_DOMAIN is the configured prod base domain
    # (e.g. ``sales-agent.scope3.com``) — added so production tenant
    # subdomains resolve via the same strategy as dev.
    subdomain = host
    suffixes = [".localhost", ".localtest.me", ".lvh.me", ".example.com"]
    if sales_domain := os.environ.get("SALES_AGENT_DOMAIN"):
        suffixes.append(f".{sales_domain.lower()}")
    for suffix in suffixes:
        if subdomain.endswith(suffix):
            subdomain = subdomain[: -len(suffix)]
            break

    with get_db_session() as session:
        if subdomain != host:
            # Strategy 2: subdomain-on-known-suffix lookup.
            row = session.scalars(
                select(TenantRow).filter_by(subdomain=subdomain, is_active=True)
            ).first()
        else:
            # Strategy 3: virtual_host (production custom domain).
            row = session.scalars(
                select(TenantRow).filter_by(virtual_host=host, is_active=True)
            ).first()

    if row is None:
        return None
    return Tenant(id=row.tenant_id, display_name=row.name)


def build_subdomain_router() -> CallableSubdomainTenantRouter:
    """Create the tenant router with a 60-second cache.

    The cache is small but bounded — tenants change infrequently relative
    to request volume, so even a low-traffic seller hits the cache often
    enough to benefit. ``invalidate(host)`` is called from the admin
    flow when a tenant is created / deactivated / has its subdomain
    rotated (M2 wiring).
    """
    return CallableSubdomainTenantRouter(
        _resolve_tenant,
        cache_size=512,
        cache_ttl_seconds=60.0,
    )


# ---- Token auth (uses adcp PR #545 BearerTokenAuthMiddleware kwargs) -------


def _validate_token(token: str) -> Principal | None:
    """Resolve an ``x-adcp-auth`` token to the matching :class:`Principal`.

    Looks up ``Principal.access_token`` in the existing salesagent
    schema. Returns ``None`` (NOT raises) on miss — see the
    ``BearerTokenAuthMiddleware`` docstring for the security rationale.

    Memory profile: each call opens a short-lived DB session, queries by
    indexed token column, returns the row. No state retained outside the
    session. Per-request cost; matches the framework's expected shape.
    """
    if not token:
        return None
    with get_db_session() as session:
        row = session.scalars(
            select(PrincipalRow).filter_by(access_token=token)
        ).first()
    if row is None:
        return None
    return Principal(
        caller_identity=row.principal_id,
        tenant_id=row.tenant_id,
    )


# ---- Per-tenant DecisioningPlatform factory -------------------------------


async def build_platform_for_tenant(tenant_id: str) -> DecisioningPlatform:
    """Per-tenant ``DecisioningPlatform`` factory for :class:`LazyPlatformRouter`.

    First-request build with bounded LRU+TTL caching upstream — boot path
    is O(1) instead of O(N tenants × auth-handshake), and inactive
    tenants get evicted under tenant churn.

    - ``ad_server == 'google_ad_manager'`` → :class:`GamPlatform`
      (reads real Placements from the tenant's GAM network on first call)
    - anything else (default ``mock``) → :class:`MockSellerPlatform`
      (reads from the salesagent ``products`` table)
    """
    with get_db_session() as session:
        row = session.scalars(
            select(TenantRow).filter_by(tenant_id=tenant_id, is_active=True)
        ).first()

    if row is None:
        # LazyPlatformRouter callers already passed the SubdomainTenantMiddleware
        # filter, so a missing/inactive tenant here is genuinely unexpected.
        raise LookupError(f"tenant {tenant_id!r} not found or inactive")

    if row.ad_server == "google_ad_manager":
        logger.info(f"  built GamPlatform for tenant {tenant_id!r}")
        return GamPlatform()
    logger.info(f"  built MockSellerPlatform for tenant {tenant_id!r}")
    return MockSellerPlatform()


def _build_proposal_managers() -> dict[str, SalesAgentProposalManager]:
    """Bind a :class:`SalesAgentProposalManager` to every active
    tenant. Same instance shared across tenants — v1 of the manager
    has no per-tenant configuration. Tenants registered AFTER boot
    don't pick up the manager until restart; that's acceptable while
    the surface is stateless. Persistent DRAFT proposals (v2) move
    this mapping to a runtime-resolvable structure or a default
    factory.
    """
    shared = SalesAgentProposalManager()
    with get_db_session() as session:
        rows = session.scalars(select(TenantRow).filter_by(is_active=True)).all()
    return {row.tenant_id: shared for row in rows}


def build_router() -> LazyPlatformRouter:
    capabilities = DecisioningCapabilities(
        specialisms=["sales-non-guaranteed"],
        adcp=Adcp(
            major_versions=[3],
            idempotency=IdempotencySupported(supported=True, replay_ttl_seconds=86400),
        ),
        account=CapabilitiesAccount(supported_billing=["operator"]),
        media_buy=MediaBuy(supported_pricing_models=["cpm"]),
        supported_protocols=[SupportedProtocol.media_buy],
    )
    # ProposalManager is wired per-tenant. Today every active tenant
    # gets the same SalesAgentProposalManager — get_products subsumed
    # via the framework's primitive (#38). The router auto-routes
    # buying_mode='refine' to manager.refine_products when the
    # manager's capabilities declare it; v1 of the manager doesn't,
    # so refine falls through to get_products (the buyer-side wire
    # contract is unchanged).
    proposal_managers = _build_proposal_managers()
    router = LazyPlatformRouter(
        accounts=SalesagentAccountStore(),
        factory=build_platform_for_tenant,
        capabilities=capabilities,
        proposal_managers=proposal_managers,
    )
    # validate_idempotency_wiring inspects the platform handed to serve()
    # for @IdempotencyStore.wrap decorators. The router shell has none —
    # dedup is wired one indirection deeper, on the per-tenant platforms
    # the factory produces (mock + gam both wrap their mutating methods).
    # Setting the escape hatch tells the boot validator dedup IS wired,
    # just not on this object. Tracked upstream (LazyPlatformRouter +
    # validate_idempotency_wiring composition).
    router._adcp_idempotency_external = True
    return router


def _allowed_hosts() -> list[str]:
    """FastMCP DNS-rebinding allowlist for dev/prod base domains.

    FastMCP's DNS-rebinding ``_validate_host`` only supports exact
    matches and ``host:*`` port wildcards — NOT subdomain wildcards
    like ``*.localhost``. Per-tenant subdomains have to be enumerated
    explicitly OR we drop DNS-rebinding protection (relying on
    Starlette's TrustedHostMiddleware further out).

    For local dev we enumerate the well-known tenant subdomains
    (``default.localhost``, ``acme.localhost``, etc.). Production
    deployments either enumerate a known closed set OR set
    ``enable_dns_rebinding_protection=False`` and rely on the cloud
    LB / WAF for Host validation.

    Tracked upstream: MCP framework needs subdomain wildcards or a
    callable Host validator for multi-tenant deployments.
    """
    base = ["localhost", "127.0.0.1", "0.0.0.0"]
    # Local dev tenant subdomains. Add new tenants here when they're
    # registered (admin UI is on the kill-nginx-spike followups).
    # Both .localhost and .localtest.me — localtest.me is the alias we
    # actually use (Google OAuth accepts it as a real public TLD; .localhost
    # is rejected as not-a-public-TLD).
    dev_tenants = ["default", "acme", "beta", "wonderstruck", "test"]
    for tenant in dev_tenants:
        base.append(f"{tenant}.localhost")
        base.append(f"{tenant}.localtest.me")
    # Bare localtest.me itself, in case the operator hits the apex.
    base.append("localtest.me")
    return base


def main() -> None:
    """Boot the unified salesagent server.

    Runs MCP at ``/mcp``, A2A at ``/`` (host root per AdCP convention),
    Flask admin via WSGI middleware. Single binary, one event loop.

    Called by ``scripts/run_server.py`` in production. Direct invocation
    via ``python -m core.main`` is supported for local dev.
    """
    logging.basicConfig(level=logging.INFO)

    port = int(os.environ.get("ADCP_PORT") or os.environ.get("PORT") or 3001)

    router = build_router()
    subdomain_router = build_subdomain_router()

    # Mount Flask admin alongside MCP + A2A so one binary owns every
    # surface. The Flask app is unchanged from the legacy stack —
    # WSGIMiddleware bridges it to ASGI, AdminWSGIMount dispatches a
    # known set of path prefixes (/admin, /static, /auth, /tenant, etc.)
    # to it before the inner serve() dispatcher routes the rest to A2A.
    from src.admin.app import create_app as _create_admin_app

    admin_wsgi = WSGIMiddleware(_create_admin_app())

    serve(
        router,
        name="salesagent-core",
        port=port,
        # MCP at /mcp, A2A at / on one Starlette binary. context_factory
        # and asgi_middleware are shared.
        transport="both",
        # PSA fires buyer-protocol webhooks via
        # ``src/services/protocol_webhook_service.py`` itself (the in-house
        # path also covers signing for non-embedded tenants). Auto-emit on
        # the SDK side would double-fire. See bokelley/salesagent#6 for
        # the planned migration to ``PgWebhookDeliverySupervisor``.
        auto_emit_completion_webhooks=False,
        # ``auth=BearerTokenAuth(...)`` (PR #566 / salesagent task #33)
        # wires the bearer-token middleware on BOTH the MCP and A2A
        # legs from one config. Replaces the prior ScopedAuthMiddleware
        # workaround that ran the MCP-only BearerTokenAuthMiddleware
        # against /mcp/* and left A2A unauthenticated. A2A's public
        # agent-card discovery at /.well-known/agent-card.json stays
        # reachable; A2A messaging endpoints now require the same
        # x-adcp-auth token MCP does.
        auth=BearerTokenAuth(
            validate_token=_validate_token,
            header_name="x-adcp-auth",
            bearer_prefix_required=False,
        ),
        # AdminWSGIMount stays as ASGI middleware — it short-circuits
        # /admin/*, /static/*, /auth/*, /tenant/*, etc. to the Flask
        # WSGI app BEFORE the auth chain fires (Flask owns its own
        # session-cookie auth). Subdomain tenant resolution still runs
        # on every non-admin request.
        asgi_middleware=[
            (AdminWSGIMount, {"wsgi_app": admin_wsgi}),
            (SubdomainTenantMiddleware, {"router": subdomain_router}),
        ],
        context_factory=auth_context_factory,
        allowed_hosts=_allowed_hosts(),
        # CORS at the SDK level — the legacy stack used FastAPI's
        # CORSMiddleware against ``ALLOWED_ORIGINS``. Same env, same
        # default (``http://localhost:8000``).
        allowed_origins=[o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:8000").split(",")],
        # Streaming responses: the SDK can keep the HTTP connection open
        # for long-running A2A tasks (delivery polls, async media-buy
        # creation). Default off for backwards compat with non-streaming
        # buyers; flip via env once we've validated buyer SDKs handle it.
        streaming_responses=os.environ.get("ADCP_STREAMING_RESPONSES", "false").lower() == "true",
        # Debug endpoints surface internal state (route map, traffic
        # counts, handler registry). Off by default — flip with
        # ``ADCP_ENABLE_DEBUG_ENDPOINTS=true`` for local debugging.
        enable_debug_endpoints=os.environ.get("ADCP_ENABLE_DEBUG_ENDPOINTS", "false").lower() == "true",
        # DNS-rebinding protection rejects requests whose Host header
        # isn't on the allow-list. ``allowed_hosts`` already feeds the
        # check; this flag explicitly enables/disables enforcement.
        # Default ON (the SDK's safe default); a deployment behind a
        # cloud LB / WAF that already validates Host can set
        # ``ADCP_DNS_REBINDING_PROTECTION=false`` to skip the second
        # check.
        enable_dns_rebinding_protection=(
            os.environ.get("ADCP_DNS_REBINDING_PROTECTION", "true").lower() == "true"
        ),
    )


if __name__ == "__main__":
    main()
