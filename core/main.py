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
import re

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

# Import for side-effect: registers the SQLAlchemy session listener that
# evicts the webhook-signing credential cache on commit. Must run before
# any session opens so rotations observed via the ORM trigger eviction.
import src.services.webhook_signing  # noqa: F401
from core.middleware.admin_mount import AdminWSGIMount
from core.middleware.agent_card_public_url import AgentCardPublicUrlMiddleware
from core.middleware.dual_credential_audit import DualCredentialAuditMiddleware
from core.middleware.scheduler_lifespan import SchedulerLifespanMiddleware
from core.middleware.spec_defaults import SpecDefaultsMiddleware
from core.middleware.transport_detect import TransportDetectMiddleware
from core.platforms.gam import GamPlatform
from core.platforms.mock import MockSellerPlatform
from core.proposal.manager import SalesAgentProposalManager
from core.stores.accounts import SalesagentAccountStore
from src.core.database.database_session import get_db_session
from src.core.database.models import Principal as PrincipalRow
from src.core.database.models import Tenant as TenantRow
from src.core.signing import SigningVerifyMiddleware

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
            row = session.scalars(select(TenantRow).filter_by(tenant_id="default", is_active=True)).first()
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
            row = session.scalars(select(TenantRow).filter_by(subdomain=subdomain, is_active=True)).first()
        else:
            # Strategy 3: virtual_host (production custom domain).
            row = session.scalars(select(TenantRow).filter_by(virtual_host=host, is_active=True)).first()

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
        row = session.scalars(select(PrincipalRow).filter_by(access_token=token)).first()
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
        row = session.scalars(select(TenantRow).filter_by(tenant_id=tenant_id, is_active=True)).first()

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
    from adcp.types.generated_poc.bundled.protocol.get_adcp_capabilities_response import Features

    from src.core.tools.capabilities import IDEMPOTENCY_REPLAY_TTL_SECONDS

    capabilities = DecisioningCapabilities(
        specialisms=["sales-non-guaranteed"],
        adcp=Adcp(
            major_versions=[3],
            idempotency=IdempotencySupported(supported=True, replay_ttl_seconds=IDEMPOTENCY_REPLAY_TTL_SECONDS),
        ),
        account=CapabilitiesAccount(supported_billing=["operator"]),
        media_buy=MediaBuy(
            supported_pricing_models=["cpm"],
            # inline_creative_management: sync_creatives / list_creatives
            # tools land creatives synchronously without a separate review
            # round-trip.
            #
            # property_list_filtering is intentionally NOT declared here.
            # ``_get_products_impl`` does its own property-list filtering,
            # but declaring the capability tells the SDK to route through
            # its own ``PropertyListFetcher`` plug — and we don't ship one,
            # so SDK boot fails fast (``no PropertyListFetcher was
            # wired``). Declare when we wire that plug.
            features=Features(inline_creative_management=True),
        ),
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


async def _start_schedulers() -> None:
    """Boot background schedulers when the ASGI server comes up."""
    from src.services.delivery_webhook_scheduler import start_delivery_webhook_scheduler
    from src.services.media_buy_status_scheduler import start_media_buy_status_scheduler

    await start_delivery_webhook_scheduler()
    await start_media_buy_status_scheduler()


async def _stop_schedulers() -> None:
    """Stop background schedulers on shutdown."""
    from src.services.delivery_webhook_scheduler import stop_delivery_webhook_scheduler
    from src.services.media_buy_status_scheduler import stop_media_buy_status_scheduler

    await stop_media_buy_status_scheduler()
    await stop_delivery_webhook_scheduler()


DEFAULT_DEV_TENANT_SUBDOMAINS: tuple[str, ...] = (
    "default",
    "acme",
    "beta",
    "wonderstruck",
    "test",
)


_VALID_DEV_TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _parse_dev_tenant_env(raw: str | None) -> tuple[str, ...]:
    """Parse ``DEV_TENANT_SUBDOMAINS`` into a tenant tuple, or return defaults.

    Falls back to :data:`DEFAULT_DEV_TENANT_SUBDOMAINS` when:
      * the var is unset (``raw is None``);
      * the var is set but empty / whitespace-only — treating empty as
        "use defaults" avoids the footgun of accidentally exporting an
        empty value (e.g. unset shell var interpolation) and silently
        locking out every dev tenant.

    Names are validated against ``^[a-z0-9][a-z0-9-]*$`` (RFC-1123-ish
    DNS labels). Invalid names are dropped with a warning rather than
    raising, so a single bad entry doesn't break boot.
    """
    if raw is None:
        return DEFAULT_DEV_TENANT_SUBDOMAINS
    candidates = [name.strip() for name in raw.split(",")]
    candidates = [name for name in candidates if name]
    if not candidates:
        return DEFAULT_DEV_TENANT_SUBDOMAINS

    valid: list[str] = []
    for name in candidates:
        if _VALID_DEV_TENANT_RE.match(name):
            valid.append(name)
        else:
            logger.warning("DEV_TENANT_SUBDOMAINS: dropping invalid tenant name %r", name)
    return tuple(valid) if valid else DEFAULT_DEV_TENANT_SUBDOMAINS


def _allowed_hosts() -> list[str]:
    """FastMCP DNS-rebinding allowlist for dev/prod base domains.

    FastMCP's DNS-rebinding ``_validate_host`` only supports exact
    matches and ``host:*`` port wildcards — NOT subdomain wildcards
    like ``*.localhost``. Per-tenant subdomains have to be enumerated
    explicitly OR we drop DNS-rebinding protection (relying on
    Starlette's TrustedHostMiddleware further out).

    For local dev we enumerate the well-known tenant subdomains
    (``default.localhost``, ``acme.localhost``, etc.). Override at boot
    via ``DEV_TENANT_SUBDOMAINS`` (comma-separated). See
    :func:`_parse_dev_tenant_env` for the parsing contract.

    Production deployments either enumerate a known closed set OR set
    ``enable_dns_rebinding_protection=False`` and rely on the cloud
    LB / WAF for Host validation.

    Tracked upstream: MCP framework needs subdomain wildcards or a
    callable Host validator for multi-tenant deployments. (See #26.)
    """
    base = ["localhost", "127.0.0.1", "0.0.0.0"]

    dev_tenants = _parse_dev_tenant_env(os.getenv("DEV_TENANT_SUBDOMAINS"))

    # Both .localhost and .localtest.me — localtest.me is the alias we
    # actually use (Google OAuth accepts it as a real public TLD; .localhost
    # is rejected as not-a-public-TLD).
    for tenant in dev_tenants:
        base.append(f"{tenant}.localhost")
        base.append(f"{tenant}.localtest.me")
    # Bare localtest.me itself, in case the operator hits the apex.
    base.append("localtest.me")
    return base


def _serve_kwargs(
    *,
    include_scheduler: bool,
    include_subdomain_routing: bool = True,
) -> dict:
    """Build the kwargs dict shared by :func:`main` and :func:`build_app`.

    Centralising the kwargs keeps the production server and the test
    harness on the same wiring — auth, admin mount, CORS, validation,
    etc. The hooks that diverge between production and in-process tests:

    * ``include_scheduler``: production runs background schedulers via
      :class:`SchedulerLifespanMiddleware`; tests skip them so background
      polling doesn't race the test DB lifecycle.
    * ``include_subdomain_routing``: production resolves the tenant from
      the ``Host`` header via :class:`SubdomainTenantMiddleware` (and
      404s on unknown hosts); tests rely on the bearer-token chain to
      scope identity to a tenant, so the host check would only get in
      the way of arbitrary test base URLs.
    """
    router = build_router()

    # Mount Flask admin alongside MCP + A2A so one binary owns every
    # surface. WSGIMiddleware bridges it to ASGI; AdminWSGIMount
    # dispatches a known set of path prefixes (/admin, /static, /auth,
    # /tenant, etc.) to it before the inner serve() dispatcher routes
    # the rest to A2A.
    from src.admin.app import create_app as _create_admin_app

    admin_wsgi = WSGIMiddleware(_create_admin_app())

    asgi_middleware: list = [
        (AdminWSGIMount, {"wsgi_app": admin_wsgi}),
        # TransportDetectMiddleware sets a ``current_transport`` ContextVar
        # based on the URL path so platform methods know whether the
        # inbound request was MCP or A2A. Webhook payload shape is
        # transport-matched (A2A buyers expect ``Task``, MCP buyers expect
        # ``McpWebhookPayload``); without this signal the platform
        # defaults to MCP and A2A buyers get the wrong shape. Runs after
        # ``AdminWSGIMount`` so admin paths short-circuit before this
        # fires (admin traffic doesn't carry buyer transport semantics).
        # See issue #202.
        (TransportDetectMiddleware, {}),
        # DualCredentialAuditMiddleware logs WARNING when an inbound
        # request carries two different bearer tokens (one in
        # ``Authorization: Bearer`` and one in ``x-adcp-auth``). Restores
        # the audit signal the deleted bearer-translation shim used to
        # emit (per #194 follow-up). Never logs token values; only
        # SHA-256 fingerprints for log correlation.
        (DualCredentialAuditMiddleware, {}),
        # SpecDefaultsMiddleware backfills wire fields the spec marks as
        # required but instructs sellers to default for pre-v3 clients
        # (e.g. GetProductsRequest.buying_mode → 'brief'). Sits *outside*
        # the SDK validation boundary so the defaults land before the
        # typed-dispatcher rejects the payload.
        (SpecDefaultsMiddleware, {}),
        # AgentCardPublicUrlMiddleware rewrites localhost URLs in the
        # /.well-known/agent-card.json response with the request's public
        # host (X-Forwarded-Host / Host). The framework hardcodes
        # ``http://localhost:{port}/`` at server-init time and exposes no
        # hook for injecting a public URL — without this rewrite, SDK
        # clients reading the card try to reach the internal socket and
        # all A2A discovery cascades to "fetch failed" (#103).
        (AgentCardPublicUrlMiddleware, {}),
    ]
    if include_subdomain_routing:
        subdomain_router = build_subdomain_router()
        asgi_middleware.append(
            (SubdomainTenantMiddleware, {"router": subdomain_router}),
        )
    if include_scheduler:
        asgi_middleware.append(
            (
                SchedulerLifespanMiddleware,
                {
                    "startups": [_start_schedulers],
                    "shutdowns": [_stop_schedulers],
                },
            )
        )
    # SigningVerifyMiddleware verifies RFC 9421 signatures on inbound
    # buyer-protocol traffic and stashes verified state on
    # ``scope["state"]``. AdminWSGIMount runs first so the admin paths
    # short-circuit before signing inspects them; this entry runs LAST
    # so it only sees buyer-protocol traffic. See
    # docs/design/signing-non-embedded.md.
    asgi_middleware.append((SigningVerifyMiddleware, {}))

    port = int(os.environ.get("ADCP_PORT") or os.environ.get("PORT") or 3001)

    return {
        "router": router,
        "name": "salesagent-core",
        "port": port,
        "transport": "both",
        # PSA fires buyer-protocol webhooks via
        # ``src/services/protocol_webhook_service.py`` itself (the in-house
        # path also covers signing for non-embedded tenants). Auto-emit on
        # the SDK side would double-fire.
        "auto_emit_completion_webhooks": False,
        # Bearer-token auth wraps both MCP and A2A legs. Per-leg knobs
        # (adcp>=4.5.0): MCP keeps the legacy ``x-adcp-auth: <raw>`` header
        # baked into early adopters; A2A uses the spec-canonical RFC 6750
        # ``Authorization: Bearer <token>`` (the SDK default), matching what
        # a2a-sdk clients emit out of the box. No bearer-translation shim.
        "auth": BearerTokenAuth(
            validate_token=_validate_token,
            mcp_header_name="x-adcp-auth",
            mcp_bearer_prefix_required=False,
        ),
        "asgi_middleware": asgi_middleware,
        "context_factory": auth_context_factory,
        "allowed_hosts": _allowed_hosts(),
        "allowed_origins": [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:8000").split(",")],
        "streaming_responses": os.environ.get("ADCP_STREAMING_RESPONSES", "false").lower() == "true",
        "enable_debug_endpoints": os.environ.get("ADCP_ENABLE_DEBUG_ENDPOINTS", "false").lower() == "true",
        "enable_dns_rebinding_protection": (os.environ.get("ADCP_DNS_REBINDING_PROTECTION", "true").lower() == "true"),
    }


def build_app():
    """Build the unified MCP+A2A ASGI app for in-process tests.

    Mirrors :func:`main`'s production wiring (auth, admin mount,
    subdomain routing, CORS, validation) but does NOT bind a uvicorn
    socket and does NOT install the scheduler lifespan — schedulers
    run real DB polling that races with test fixtures.

    Routes the unified app the same way production does: MCP at
    ``/mcp``, A2A at ``/`` (host root), Flask admin via WSGI middleware.
    Tests drive it through ``httpx.ASGITransport`` (or Starlette's
    ``TestClient``) for end-to-end transport verification without
    standing up a server.

    The Flask admin and scheduler imports happen inside this function
    to keep import-time side effects minimal — call sites that only
    need the production server (or vice versa) don't pay the other
    surface's import cost.
    """
    # Build the handler ourselves so we can hand it to the SDK's
    # private app-builder. The decisioning.serve() one-shot wrapper
    # binds uvicorn; we want the ASGI app without the socket.
    from adcp.decisioning.serve import create_adcp_server_from_platform
    from adcp.server.serve import _apply_asgi_middleware, _build_mcp_and_a2a_app

    kwargs = _serve_kwargs(include_scheduler=False, include_subdomain_routing=False)
    router = kwargs.pop("router")
    asgi_middleware = kwargs.pop("asgi_middleware")
    auto_emit = kwargs.pop("auto_emit_completion_webhooks")

    handler, _executor, _registry = create_adcp_server_from_platform(
        router,
        auto_emit_completion_webhooks=auto_emit,
    )

    # _build_mcp_and_a2a_app accepts a subset of serve()'s kwargs.
    # Filter the ones the inner builder takes; the rest are uvicorn /
    # debug-endpoint concerns that don't apply to the in-process app.
    build_kwargs = {
        "name": kwargs["name"],
        "port": kwargs["port"],
        "host": "127.0.0.1",
        "instructions": None,
        "test_controller": None,
        "context_factory": kwargs["context_factory"],
        "streaming_responses": kwargs["streaming_responses"],
        "allowed_hosts": kwargs["allowed_hosts"],
        "allowed_origins": kwargs["allowed_origins"],
        # Tests use arbitrary base URLs (testserver, default.localhost,
        # 127.0.0.1, etc.); production's host allowlist isn't useful in
        # this context. Disable explicitly so requests to e.g.
        # ``http://testserver/mcp/`` aren't rejected before the tool
        # dispatcher runs.
        "enable_dns_rebinding_protection": False,
        "auth": kwargs["auth"],
    }

    app = _build_mcp_and_a2a_app(handler, **build_kwargs)
    return _apply_asgi_middleware(app, asgi_middleware)


def main() -> None:
    """Boot the unified salesagent server.

    Runs MCP at ``/mcp``, A2A at ``/`` (host root per AdCP convention),
    Flask admin via WSGI middleware. Single binary, one event loop.

    Called by ``scripts/run_server.py`` in production. Direct invocation
    via ``python -m core.main`` is supported for local dev.
    """
    logging.basicConfig(level=logging.INFO)

    kwargs = _serve_kwargs(include_scheduler=True)
    router = kwargs.pop("router")
    serve(router, **kwargs)


if __name__ == "__main__":
    main()
