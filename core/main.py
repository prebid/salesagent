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
from collections.abc import Callable
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
    Signals,
    SignalsFeatures,
    SupportedProtocol,
    WebhookSigning,
)
from adcp.server import (
    BearerTokenAuth,
    CallableSubdomainTenantRouter,
    Principal,
    SubdomainTenantMiddleware,
    Tenant,
    ToolContext,
    auth_context_factory,
)
from adcp.server.mcp_tools import (
    ADCP_TOOL_DEFINITIONS,
    DISCOVERY_TOOLS,
    _ensure_pydantic_schemas_applied,
)
from adcp.server.spec_compat import _spec_compat_hooks_impl

from src.core.slim_schemas import CREATE_MEDIA_BUY_SLIM_SCHEMA, UPDATE_MEDIA_BUY_SLIM_SCHEMA

# Optionally replace large tool inputSchemas with compact versions.
#
# adcp's _generate_pydantic_schemas() inlines all $refs, turning the
# CreateMediaBuyRequest schema into ~93 000 lines of JSON (~2.2 MB) and the
# UpdateMediaBuyRequest schema into an even larger blob (~4.2 MB).  Either
# volume fills an LLM context window on tools/list, making the tool unusable.
#
# Set ADCP_COMPACT_TOOL_SCHEMAS=true to activate the slim schemas.
# When unset or false, the full adcp-generated schema is used (default).
#
# Strategy: call _ensure_pydantic_schemas_applied() eagerly so it sets
# _schemas_applied=True.  Subsequent lazy calls on tools/list become
# no-ops.  We then overwrite each tool's inputSchema with our slim
# version — it sticks for the lifetime of the process.
#
# Runtime validation is unchanged: the functions still validate the
# incoming request against the full Pydantic request models.
_ensure_pydantic_schemas_applied()
if os.environ.get("ADCP_COMPACT_TOOL_SCHEMAS", "").lower() == "true":
    _SLIM_SCHEMAS = {
        "create_media_buy": CREATE_MEDIA_BUY_SLIM_SCHEMA,
        "update_media_buy": UPDATE_MEDIA_BUY_SLIM_SCHEMA,
    }
    for _tool in ADCP_TOOL_DEFINITIONS:
        _slim = _SLIM_SCHEMAS.get(_tool["name"])
        if _slim is not None:
            _tool["inputSchema"] = _slim
from sqlalchemy import select

# Import for side-effect: registers the SQLAlchemy session listener that
# fires ``sync.completed`` / ``sync.failed`` webhooks on SyncJob terminal
# transitions (issue #463). Must run before any session opens so sync
# workers' terminal commits trigger emission regardless of code path.
import src.admin.services.sync_webhook_emission  # noqa: F401

# Import for side-effect: registers the SQLAlchemy session listener that
# evicts the webhook-signing credential cache on commit. Must run before
# any session opens so rotations observed via the ORM trigger eviction.
import src.services.webhook_signing  # noqa: F401
from core.decisioning.proposal_store import close_proposal_store, get_proposal_store
from core.middleware.admin_mount import AdminWSGIMount
from core.middleware.dual_credential_audit import DualCredentialAuditMiddleware
from core.middleware.origin_guard import BuyerProtocolOriginGuardMiddleware
from core.platforms.gam import GamPlatform
from core.platforms.mock import MockSellerPlatform
from core.proposal.manager import SalesAgentProposalManager
from core.stores.accounts import SalesagentAccountStore
from src.core.database.database_session import get_db_session
from src.core.database.models import Principal as PrincipalRow
from src.core.database.models import Tenant as TenantRow
from src.core.embedded_identity_tokens import TOKEN_PREFIX, resolve_embedded_identity_token
from src.core.env import env_bool
from src.core.middleware.embedded_buyer_auth_bridge import EmbeddedBuyerAuthBridgeMiddleware
from src.core.middleware.tracing import TracingMiddleware
from src.core.signing import SigningVerifyMiddleware
from src.core.telemetry import shutdown_telemetry

logger = logging.getLogger(__name__)

PreValidationHook = Callable[[str, dict[str, Any]], dict[str, Any]]


def _env_bool(name: str, *, default: bool) -> bool:
    """Parse an environment boolean with an explicit default."""
    return env_bool(name, default=default)


def _env_optional_positive_float(name: str, *, default: float) -> float | None:
    """Parse an optional positive float environment value."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    if raw.lower() in {"", "0", "none", "off", "disable", "disabled"}:
        return None
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using default %s", name, raw, default)
        return default
    if value <= 0:
        logger.warning("%s=%r is not positive; using default %s", name, raw, default)
        return default
    return value


def _env_positive_int(name: str) -> int | None:
    """Parse an optional positive integer environment value."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; leaving unset", name, raw)
        return None
    if value <= 0:
        logger.warning("%s=%r is not positive; leaving unset", name, raw)
        return None
    return value


# Tools callable without a bearer token. Buyers need to discover the agent
# before they have credentials.
#
# Passed as ``BearerTokenAuth.mcp_discovery_tools`` in :func:`_serve_kwargs`.
# Every entry must exist in ``adcp.server.mcp_tools.ADCP_TOOL_DEFINITIONS``
# or ``BearerTokenAuth`` rejects it at construction via ``validate_discovery_set``.
# ``list_accounts`` is deliberately excluded — salesagent's
# ``_list_accounts_impl`` enforces BR-RULE-055 INV-3 and raises
# ``AUTH_TOKEN_INVALID`` for unauthenticated callers, so gating it pre-auth
# here only funnels rejected callers into the impl-layer error path.
AUTH_OPTIONAL_TOOLS = frozenset(
    {
        "get_adcp_capabilities",
        "get_products",
        "get_signals",
        "list_creative_formats",
    }
)


def _strict_request_fields_by_tool() -> dict[str, set[str]]:
    """Return request fields salesagent supports for each AdCP tool.

    The SDK's library request models intentionally allow extra fields, but
    salesagent's local request models are strict in dev/CI. This map restores
    that local strictness at the wire boundary before permissive SDK models can
    accept or drop unknown fields.
    """
    from src.core.schemas import (
        CreateMediaBuyRequest,
        GetMediaBuyDeliveryRequest,
        GetMediaBuysRequest,
        GetProductsRequest,
        GetSignalsRequest,
        ListCreativeFormatsRequest,
        ListCreativesRequest,
        SyncCreativesRequest,
        UpdateMediaBuyRequest,
    )

    return {
        "create_media_buy": set(CreateMediaBuyRequest.model_fields),
        "get_media_buy_delivery": set(GetMediaBuyDeliveryRequest.model_fields),
        "get_media_buys": set(GetMediaBuysRequest.model_fields),
        "get_products": set(GetProductsRequest.model_fields),
        "get_signals": set(GetSignalsRequest.model_fields),
        "list_creative_formats": set(ListCreativeFormatsRequest.model_fields),
        "list_creatives": {
            "created_after",
            "created_before",
            "fields",
            "filters",
            "format",
            "include_assignments",
            "include_performance",
            "include_sub_assets",
            "limit",
            "media_buy_id",
            "media_buy_ids",
            "page",
            "search",
            "sort_by",
            "sort_order",
            "status",
            "tags",
        }
        | set(ListCreativesRequest.model_fields),
        "sync_creatives": set(SyncCreativesRequest.model_fields),
        # update_media_buy is advertised as separate media_buy_id + patch params,
        # while the impl consumes a unified UpdateMediaBuyRequest.
        "update_media_buy": {"media_buy_id", "patch"} | set(UpdateMediaBuyRequest.model_fields),
    }


def _with_dev_unknown_field_rejection(
    hooks: dict[str, PreValidationHook],
) -> dict[str, PreValidationHook]:
    """Compose SDK spec-compat hooks with salesagent strict-extra checks."""
    fields_by_tool = _strict_request_fields_by_tool()
    wrapped: dict[str, PreValidationHook] = dict(hooks)

    def make_hook(tool_name: str, base_hook: PreValidationHook | None, known_fields: set[str]) -> PreValidationHook:
        def hook(name: str, params: dict[str, Any]) -> dict[str, Any]:
            normalized = base_hook(name, params) if base_hook is not None else params
            from src.core.request_compat import normalize_request_params

            normalized = normalize_request_params(tool_name, normalized).params
            from src.core.config import is_production

            if not is_production():
                unknown = sorted(normalized.keys() - known_fields)
                if unknown:
                    fields = ", ".join(unknown)
                    raise ValueError(f"Unknown field(s) for {tool_name}: {fields}")
            return normalized

        return hook

    for tool_name, known_fields in fields_by_tool.items():
        wrapped[tool_name] = make_hook(tool_name, wrapped.get(tool_name), known_fields)
    return wrapped


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
    embedded_identity = resolve_embedded_identity_token(token)
    if embedded_identity is not None:
        return Principal(
            caller_identity=embedded_identity.principal_id,
            tenant_id=embedded_identity.tenant_id,
        )
    if token.startswith(TOKEN_PREFIX):
        return None
    with get_db_session() as session:
        row = session.scalars(select(PrincipalRow).filter_by(access_token=token)).first()
    if row is None:
        return None
    return Principal(
        caller_identity=row.principal_id,
        tenant_id=row.tenant_id,
    )


def auth_context_factory_with_discovery_fallback(meta):
    """Wrap :func:`adcp.server.auth.auth_context_factory` to recover the
    authenticated principal on the MCP discovery-tool path.

    ``BearerTokenAuth.mcp_discovery_tools`` (adcp 5.6.0 #745) instructs the
    transport gate to bypass bearer-token validation for the configured
    tool set. The bypass clears ``request.state`` and the SDK auth
    ContextVars to ``None`` even when the buyer DID send a token — by
    design, so unauthenticated discovery works. The downside: authenticated
    buyers hitting a discovery tool reach the dispatch task with no
    resolved principal, so ``SalesagentAccountStore.resolve`` and
    ``_build_identity`` (which both read ``current_principal`` /
    ``current_tenant``) fall over with ``ACCOUNT_NOT_FOUND`` or
    ``AUTH_TOKEN_INVALID``.

    This factory runs in the dispatch task. When the upstream factory
    returns an anonymous ``ToolContext`` we re-extract the bearer token
    from the request headers, validate it via :func:`_validate_token`,
    populate the SDK ContextVars and return a populated
    ``ToolContext``. ContextVars set here propagate to ``_resolve_account``
    and ``_build_identity`` downstream in the same task. Anonymous
    buyers (no token) get the original anonymous ``ToolContext``
    unchanged so genuinely unauthenticated discovery still works.
    """
    ctx = auth_context_factory(meta)
    if ctx.caller_identity is not None or ctx.tenant_id is not None:
        return ctx
    request = getattr(meta, "request_context", None)
    if request is None or not hasattr(request, "headers"):
        return ctx
    token: str | None = None
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    if not token:
        token = request.headers.get("x-adcp-auth")
    if not token:
        return ctx
    principal = _validate_token(token)
    if principal is None:
        return ctx

    # Lazy imports — avoid pulling adcp.server.auth internals at module load.
    from adcp.decisioning.context import AuthInfo
    from adcp.server.auth import current_principal, current_tenant

    current_principal.set(principal.caller_identity)
    current_tenant.set(principal.tenant_id)

    return ToolContext(
        request_id=ctx.request_id,
        caller_identity=principal.caller_identity,
        tenant_id=principal.tenant_id,
        metadata={
            **(ctx.metadata or {}),
            "adcp.auth_info": AuthInfo(
                kind="bearer",
                principal=principal.caller_identity,
                credential=None,
            ),
        },
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


class SalesagentPlatformRouter(LazyPlatformRouter):
    """Lazy tenant router with SDK-native request-scoped capabilities."""

    def get_adcp_capabilities_for_request(
        self,
        params: Any = None,
        context: ToolContext | None = None,
    ) -> DecisioningCapabilities | None:
        from core.platforms._capabilities_envelope import capabilities_for_request

        return capabilities_for_request(self.capabilities, params=params, context=context)


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

    from core.platforms._delegate import (
        SUPPORTED_ADCP_VERSIONS,
        SUPPORTED_MAJOR_VERSIONS,
        install_adcp_wire_version_compat,
    )
    from src.core.tools.capabilities import IDEMPOTENCY_REPLAY_TTL_SECONDS

    install_adcp_wire_version_compat()

    capabilities = DecisioningCapabilities(
        specialisms=["sales-non-guaranteed", "signal-owned"],
        webhook_signing_managed_externally=True,
        adcp=Adcp(
            major_versions=sorted(SUPPORTED_MAJOR_VERSIONS),
            supported_versions=list(SUPPORTED_ADCP_VERSIONS),
            idempotency=IdempotencySupported(supported=True, replay_ttl_seconds=IDEMPOTENCY_REPLAY_TTL_SECONDS),
        ),
        # Both billing modes are supported at the platform level.
        # ``"agent"`` is gated per-principal by ``Principal.billing_enabled``
        # in :func:`src.core.tools.accounts._check_billing_policy` —
        # capabilities advertises what the seller offers, sync_accounts
        # enforces who can use it.
        account=CapabilitiesAccount(supported_billing=["operator", "agent"]),
        media_buy=MediaBuy(
            supported_pricing_models=["cpm"],
            supports_proposals=True,
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
        signals=Signals(discovery_modes=["brief", "wholesale"], features=SignalsFeatures(catalog_signals=True)),
        webhook_signing=WebhookSigning(supported=False, legacy_hmac_fallback=True),
        supported_protocols=[SupportedProtocol.media_buy, SupportedProtocol.signals],
    )
    # ProposalManager is wired per-tenant. Today every active tenant
    # gets the same SalesAgentProposalManager — get_products subsumed
    # via the framework's primitive (#38). The router auto-routes
    # buying_mode='refine' to manager.refine_products when the
    # manager's capabilities declare it; v1 of the manager doesn't,
    # so refine falls through to get_products (the buyer-side wire
    # contract is unchanged).
    proposal_managers = _build_proposal_managers()
    # Single shared ProposalStore returned by the factory for every
    # tenant — tenant isolation runs inside the store on
    # ``expected_account_id`` (the framework passes the principal's
    # account on every call). Wiring this lets the storyboard's
    # ``proposal_finalize`` flow work end-to-end: ``get_products(brief)``
    # persists the proposal, then ``create_media_buy(proposal_id=X)``
    # resolves it and derives packages from the proposal's allocations.
    # Without the store wired, the framework's ``proposal_dispatch``
    # short-circuits at ``hasattr(platform, "proposal_store_for_tenant")``
    # and the create_media_buy call falls through to a 0-package
    # payload → INVALID_REQUEST.
    #
    # ``proposal_store_factory`` is the lazy-shape kwarg added in
    # adcp 5.4 (#722) for parity with ``PlatformRouter.proposal_stores=``.
    # We use the factory shape (not the eager dict) because the store
    # is a single shared instance — boot-time tenant enumeration would
    # miss tenants registered after boot, but the factory has no such
    # coupling. The framework calls this on every dispatch; the
    # closure return is O(1).
    #
    # The store itself is the upstream :class:`PgProposalStore`
    # (adcp 5.5.0, adcontextprotocol/adcp-client-python#732). Cross-tenant
    # rejection, CAS for state transitions, TTL bookkeeping, and the
    # ``ON CONFLICT`` upsert all live in the library — our pool wiring
    # in :mod:`core.decisioning.proposal_store` is the only local glue.
    #
    # ``get_proposal_store`` is called *inside* the factory closure
    # rather than eagerly here so unit tests can ``build_router()``
    # without setting ``DATABASE_URL``. The factory only fires when the
    # framework dispatches a proposal-aware tool, by which point the
    # production server has a live DSN.
    router = SalesagentPlatformRouter(
        accounts=SalesagentAccountStore(),
        factory=build_platform_for_tenant,
        capabilities=capabilities,
        proposal_managers=proposal_managers,
        proposal_store_factory=lambda _tenant_id: get_proposal_store(),
    )
    # validate_idempotency_wiring inspects the platform handed to serve()
    # for @IdempotencyStore.wrap decorators. The router shell has none:
    # dedup is wired one indirection deeper, on the per-tenant platforms
    # the factory produces (mock + gam both wrap their mutating methods).
    # ``_adcp_idempotency_external`` is the SDK's documented escape hatch for
    # that composition pattern. The validator reads it with
    # ``getattr(platform, "_adcp_idempotency_external", False)``; use setattr to
    # match the SDK's read-side ergonomics without adding ``type: ignore``.
    setattr(router, "_adcp_idempotency_external", True)  # noqa: B010
    return router


def _resolve_public_url(request: Any) -> str:
    """Per-request agent-card public URL resolver.

    Wired into ``serve(public_url=...)`` (adcp 5.4.0, callable resolver
    from #650 + composed-lifespan fix from #680). The SDK calls this
    on every fetch of ``/.well-known/agent-card.json``; the return value
    is validated as an absolute URL with ``https://`` required for
    non-loopback hosts.

    Precedence:

    1. ``PUBLIC_URL`` env var when set — single-host deployments pin
       the card URL explicitly and don't want header-driven rewrites.
    2. ``X-Forwarded-Host`` (first entry of a comma-chain) — set by
       load balancers terminating TLS for multi-tenant subdomain
       deployments.
    3. ``Host`` header — direct deploys without a proxy.
    4. ``http://localhost:{port}/`` fallback when no headers are
       available (unlikely outside synthetic requests).

    Scheme is ``X-Forwarded-Proto`` when present; otherwise ``http``
    for loopback hosts and ``https`` everywhere else (matches the SDK's
    own ``_validate_card_url`` rule).
    """
    static = os.environ.get("PUBLIC_URL")
    if static:
        return static.rstrip("/") + "/"

    headers = request.headers
    managed_instance = _env_bool("MANAGED_INSTANCE", default=False)
    forwarded_host = "" if managed_instance else headers.get("x-forwarded-host", "").split(",", 1)[0].strip()
    host = forwarded_host or headers.get("host", "").split(",", 1)[0].strip()

    if not host:
        port = int(os.environ.get("ADCP_PORT") or os.environ.get("PORT") or 3001)
        return f"http://localhost:{port}/"

    forwarded_proto = "" if managed_instance else headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    hostname = host.split(":", 1)[0]
    is_loopback = hostname in ("localhost", "127.0.0.1", "0.0.0.0") or hostname.endswith(".localhost")
    scheme = forwarded_proto or ("http" if is_loopback else "https")
    return f"{scheme}://{host}/"


async def _start_schedulers() -> None:
    """Boot background schedulers when the ASGI server comes up."""
    from src.services.adapter_sync_scheduler import (
        start_adapter_inventory_guidance_sync_scheduler,
        start_adapter_reporting_sync_scheduler,
    )
    from src.services.delivery_webhook_scheduler import start_delivery_webhook_scheduler
    from src.services.gam_pricing_availability_scheduler import start_gam_pricing_availability_scheduler
    from src.services.gam_signal_coverage_scheduler import start_gam_signal_coverage_scheduler
    from src.services.media_buy_status_scheduler import start_media_buy_status_scheduler

    await start_delivery_webhook_scheduler()
    await start_media_buy_status_scheduler()
    await start_adapter_reporting_sync_scheduler()
    await start_adapter_inventory_guidance_sync_scheduler()
    await start_gam_pricing_availability_scheduler()
    await start_gam_signal_coverage_scheduler()


async def _stop_schedulers() -> None:
    """Stop background schedulers on shutdown."""
    from src.services.adapter_sync_scheduler import (
        stop_adapter_inventory_guidance_sync_scheduler,
        stop_adapter_reporting_sync_scheduler,
    )
    from src.services.delivery_webhook_scheduler import stop_delivery_webhook_scheduler
    from src.services.gam_pricing_availability_scheduler import stop_gam_pricing_availability_scheduler
    from src.services.gam_signal_coverage_scheduler import stop_gam_signal_coverage_scheduler
    from src.services.media_buy_status_scheduler import stop_media_buy_status_scheduler

    await stop_gam_signal_coverage_scheduler()
    await stop_gam_pricing_availability_scheduler()
    await stop_adapter_inventory_guidance_sync_scheduler()
    await stop_adapter_reporting_sync_scheduler()
    await stop_media_buy_status_scheduler()
    await stop_delivery_webhook_scheduler()


async def _shutdown_telemetry_hook() -> None:
    shutdown_telemetry()


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


def _allowed_origins() -> list[str]:
    """FastMCP-compatible Origin allowlist for browser protocol requests."""
    return [origin.strip() for origin in os.environ.get("ALLOWED_ORIGINS", "http://localhost:8000").split(",")]


def _enable_dns_rebinding_protection(*, include_subdomain_routing: bool) -> bool:
    """Return the MCP-layer DNS rebinding setting for this process.

    When the SDK subdomain tenant router is enabled, it validates the
    normalized Host header against the tenant table before requests reach
    the MCP transport. FastMCP's own host validator only supports exact
    hostnames plus ``host:*`` port wildcards, so it cannot represent the
    dynamic ``<tenant>.<SALES_AGENT_DOMAIN>`` hosts that provisioning
    returns. In that routed mode, default the inner MCP check off and let
    the tenant router be the host allowlist.

    Operators can still force the FastMCP check on or off explicitly with
    ``ADCP_DNS_REBINDING_PROTECTION``.
    """
    explicit = os.environ.get("ADCP_DNS_REBINDING_PROTECTION")
    if explicit is not None:
        return explicit.lower() == "true"
    return not include_subdomain_routing


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
      :func:`adcp.server.serve`'s ``on_startup`` / ``on_shutdown`` hooks
      (adcp 5.4.0 #713); tests skip them so background polling doesn't
      race the test DB lifecycle.
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
    allowed_origins = _allowed_origins()

    asgi_middleware: list = [
        (TracingMiddleware, {}),
        (AdminWSGIMount, {"wsgi_app": admin_wsgi}),
        (EmbeddedBuyerAuthBridgeMiddleware, {}),
        # DualCredentialAuditMiddleware logs WARNING when an inbound
        # request carries two different bearer tokens (one in
        # ``Authorization: Bearer`` and one in ``x-adcp-auth``). Restores
        # the audit signal the deleted bearer-translation shim used to
        # emit (per #194 follow-up). Never logs token values; only
        # SHA-256 fingerprints for log correlation.
        (DualCredentialAuditMiddleware, {}),
        # FastMCP's DNS-rebinding switch controls both Host and Origin
        # validation. Routed deployments disable the inner Host check
        # because dynamic tenant hosts cannot be represented in its exact
        # allowlist, but SubdomainTenantMiddleware only validates Host.
        # Preserve the Origin half for browser-driven MCP/A2A requests.
        (BuyerProtocolOriginGuardMiddleware, {"allowed_origins": allowed_origins}),
    ]
    managed_instance = _env_bool("MANAGED_INSTANCE", default=False)
    use_subdomain_routing = include_subdomain_routing and not managed_instance
    if use_subdomain_routing:
        subdomain_router = build_subdomain_router()
        asgi_middleware.append(
            (SubdomainTenantMiddleware, {"router": subdomain_router}),
        )
    # SigningVerifyMiddleware verifies RFC 9421 signatures on inbound
    # buyer-protocol traffic and stashes verified state on
    # ``scope["state"]``. AdminWSGIMount runs first so the admin paths
    # short-circuit before signing inspects them; this entry runs LAST
    # so it only sees buyer-protocol traffic. See
    # docs/design/signing-non-embedded.md.
    asgi_middleware.append((SigningVerifyMiddleware, {}))

    port = int(os.environ.get("ADCP_PORT") or os.environ.get("PORT") or 3001)

    # Background schedulers wire as serve()'s native lifespan hooks
    # (adcp 5.4.0 #713). transport="both" is required for these to fire,
    # which we already pass below.
    #
    # PgProposalStore's pool opens lazily on first method call via
    # ``_LazyOpenPgProposalStore`` — no ``on_startup`` entry needed,
    # which is the right behavior for integration tests that rebuild
    # the store against per-test databases. ``close_proposal_store``
    # on shutdown drains in-flight connections before serve() exits.
    on_startup = [_start_schedulers] if include_scheduler else None
    on_shutdown = (
        [_stop_schedulers, close_proposal_store, _shutdown_telemetry_hook]
        if include_scheduler
        else [close_proposal_store, _shutdown_telemetry_hook]
    )

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
        # Bearer-token auth wraps both MCP and A2A legs.
        # ``Authorization: Bearer <token>`` is the spec-canonical carrier
        # on both legs. ``x-adcp-auth: <raw>`` is an additive alias on
        # the MCP leg only — kept so legacy MCP buyers interoperate with
        # off-the-shelf MCP and a2a-sdk clients without code changes.
        #
        # ``mcp_discovery_tools`` gates ``tools/call`` at the transport:
        # tools in the set bypass the bearer check (still 401 on a bad
        # token, but a missing one is fine); everything else requires
        # valid auth pre-dispatch. The set must cover what AdCP buyers
        # need to discover the agent before they have credentials.
        # See ``AUTH_OPTIONAL_TOOLS`` above for the canonical definition.
        "auth": BearerTokenAuth(
            validate_token=_validate_token,
            mcp_discovery_tools=DISCOVERY_TOOLS | AUTH_OPTIONAL_TOOLS,
            mcp_legacy_header_aliases=["x-adcp-auth"],
        ),
        "asgi_middleware": asgi_middleware,
        "context_factory": auth_context_factory_with_discovery_fallback,
        "allowed_hosts": _allowed_hosts(),
        "allowed_origins": allowed_origins,
        "streaming_responses": os.environ.get("ADCP_STREAMING_RESPONSES", "false").lower() == "true",
        "enable_debug_endpoints": os.environ.get("ADCP_ENABLE_DEBUG_ENDPOINTS", "false").lower() == "true",
        "enable_dns_rebinding_protection": _enable_dns_rebinding_protection(
            include_subdomain_routing=use_subdomain_routing
        ),
        # MCP streamable-HTTP session mode (adcp>=5.0). Keep stateful mode
        # as the default because reused sessions avoid repeated initialize +
        # tools/list overhead on chatty AdCP flows. Preserve the SDK's
        # 30-minute default idle reap so valid stateful clients can pause
        # and reuse a session without surprise 404s. Operators can tune
        # ADCP_SESSION_IDLE_TIMEOUT down for one-shot service clients, set
        # it to "none"/"off"/"0" to disable idle reaping, and set a hard
        # active-session cap with ADCP_MAX_ACTIVE_SESSIONS.
        #
        # ``FASTMCP_STATELESS_HTTP`` env alone won't work —
        # ``adcp.server.serve`` overrides FastMCP's reader by setting
        # ``mcp.settings.stateless_http`` from this kwarg directly. Set
        # ``ADCP_STATELESS_HTTP=true`` only for deployments where session
        # affinity is unavailable or client reuse is impossible. See
        # https://gofastmcp.com/v2/deployment/http for the upstream
        # recommendation.
        "stateless_http": _env_bool("ADCP_STATELESS_HTTP", default=False),
        "session_idle_timeout": _env_optional_positive_float("ADCP_SESSION_IDLE_TIMEOUT", default=1800.0),
        "max_active_sessions": _env_positive_int("ADCP_MAX_ACTIVE_SESSIONS"),
        # Per-request agent-card public URL resolver. Honors PUBLIC_URL
        # env when set (single-host) and otherwise derives from
        # X-Forwarded-Host / Host (multi-tenant subdomain). See
        # :func:`_resolve_public_url`. adcp 5.4.0 #680 made callable
        # public_url safe under ``transport='both'``.
        "public_url": _resolve_public_url,
        # Heuristic backfills for pre-v3 / pre-4.4 buyers — defaults
        # ``get_products.buying_mode='brief'`` when omitted (spec says
        # sellers SHOULD default this for pre-v3 clients) and infers
        # ``sync_creatives`` ``asset_type`` discriminators / wraps bare
        # ``format_id`` strings / demotes image→url when dims absent.
        #
        # ``spec_compat_hooks()`` is deprecated in adcp 5.2 (#667) — removal
        # target 6.0. Migration path is the typed AdapterPair registry in
        # ``adcp.compat.legacy.v2_5``, but that only fires when buyers
        # **declare** ``adcp_version='2.5'`` / ``adcp_major_version=2``. The
        # hook here is unconditional, which our integration tests rely on
        # for tag-less buyers omitting these required fields. We use the
        # private ``_spec_compat_hooks_impl`` (no DeprecationWarning) — same
        # symbol the SDK's own test suite uses for the same reason. Drop
        # this when 6.0 ships or when we update tests to declare
        # ``adcp_version`` explicitly.
        "pre_validation_hooks": _with_dev_unknown_field_rejection(_spec_compat_hooks_impl()),
        "on_startup": on_startup,
        "on_shutdown": on_shutdown,
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
    # adcp 5.1 ships ``adcp.testing.build_asgi_app`` (#626), but it composes
    # the MCP leg only — A2A isn't included, so any in-process test that
    # POSTs to ``/`` (A2A host-root) gets a 401 from the MCP-side auth
    # middleware that sees a missing ``x-adcp-auth`` header on the A2A
    # ``Authorization: Bearer`` request. Until the SDK exposes a public
    # both-transports test builder, fall back to the private
    # ``_build_mcp_and_a2a_app`` symbol which production's ``serve()``
    # uses internally and wraps BOTH legs with auth. Filed upstream:
    # request public ``adcp.testing.build_asgi_app(transport="both")``.
    from adcp.decisioning.serve import create_adcp_server_from_platform
    from adcp.server.serve import _apply_asgi_middleware, _build_mcp_and_a2a_app

    kwargs = _serve_kwargs(include_scheduler=False, include_subdomain_routing=False)
    router = kwargs.pop("router")
    asgi_middleware = kwargs.pop("asgi_middleware")
    auto_emit = kwargs.pop("auto_emit_completion_webhooks")
    # ``public_url`` is a production-shaping concern (writes the canonical
    # A2A base URL into the agent-card response); tests neither read nor
    # assert on it, so drop it from the in-process app.
    kwargs.pop("public_url", None)
    pre_validation_hooks = kwargs.pop("pre_validation_hooks", None)

    handler, _executor, _registry = create_adcp_server_from_platform(
        router,
        auto_emit_completion_webhooks=auto_emit,
    )

    app = _build_mcp_and_a2a_app(
        handler,
        name=kwargs["name"],
        port=kwargs["port"],
        host="127.0.0.1",
        instructions=None,
        test_controller=None,
        context_factory=kwargs["context_factory"],
        streaming_responses=kwargs["streaming_responses"],
        allowed_hosts=kwargs["allowed_hosts"],
        allowed_origins=kwargs["allowed_origins"],
        # Tests use arbitrary base URLs (testserver, default.localhost,
        # 127.0.0.1); production's host allowlist isn't useful here.
        # Disable so requests to ``http://testserver/mcp/`` aren't
        # rejected before the tool dispatcher runs.
        enable_dns_rebinding_protection=False,
        auth=kwargs["auth"],
        stateless_http=kwargs["stateless_http"],
        session_idle_timeout=kwargs["session_idle_timeout"],
        max_active_sessions=kwargs["max_active_sessions"],
        pre_validation_hooks=pre_validation_hooks,
        # Forward lifespan hooks so the proposal-store close hook
        # fires on shutdown (``open_proposal_store`` is gone — pool
        # opens lazily on first async call now). ``main()``'s
        # ``serve()`` call wires these natively; ``build_app`` has
        # to forward them explicitly because it bypasses ``serve``.
        on_startup=kwargs["on_startup"],
        on_shutdown=kwargs["on_shutdown"],
    )
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
