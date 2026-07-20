"""Get AdCP Capabilities tool implementation.

Returns the capabilities of this sales agent including supported protocols,
targeting dimensions, creative specs, and portfolio information.

This module follows the MCP/A2A shared implementation pattern from CLAUDE.md.
"""

import logging
from datetime import UTC, datetime

from adcp.types import GetAdcpCapabilitiesRequest, GetAdcpCapabilitiesResponse
from adcp.types.generated_poc.core.media_buy_features import MediaBuyFeatures
from adcp.types.generated_poc.core.postal_area_support import (
    PostalAreaSupport,  # adcp 6.6: standalone GeoPostalAreas removed; capabilities use PostalAreaSupport
)
from adcp.types.generated_poc.enums.billing_party import BillingParty
from adcp.types.generated_poc.enums.channels import MediaChannel
from adcp.types.generated_poc.enums.specialism import AdcpSpecialism
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
    Account as AccountCapability,  # capability sub-object; distinct from the domain Account schema
)
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
    Adcp,
    Execution,
    GeoMetros,
    Idempotency,
    MajorVersion,
    MediaBuy,
    Portfolio,
    PublisherDomain,
    SupportedProtocol,
    # FIXME(#1388): Targeting has a local subclass; import from src.core.schemas (Pattern #7/#4).
    Targeting,
)
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult

from src.core.auth import get_principal_object, require_identity
from src.core.database.repositories.idempotency_attempt import DEFAULT_REPLAY_TTL
from src.core.database.repositories.uow import TenantConfigUoW
from src.core.helpers import enum_value
from src.core.helpers.activity_helpers import log_tool_activity
from src.core.helpers.adapter_helpers import get_adapter
from src.core.resolved_identity import ResolvedIdentity
from src.core.tool_context import ToolContext
from src.services.targeting_capabilities import supports_property_list_filtering

logger = logging.getLogger(__name__)


# Mapping from adapter channel names to MediaChannel enum values
CHANNEL_MAPPING: dict[str, MediaChannel] = {
    "display": MediaChannel.display,
    "olv": MediaChannel.olv,
    "video": MediaChannel.olv,  # alias
    "social": MediaChannel.social,
    "search": MediaChannel.search,
    "ctv": MediaChannel.ctv,
    "linear_tv": MediaChannel.linear_tv,
    "radio": MediaChannel.radio,
    "streaming_audio": MediaChannel.streaming_audio,
    "audio": MediaChannel.streaming_audio,  # alias
    "podcast": MediaChannel.podcast,
    "dooh": MediaChannel.dooh,
    "ooh": MediaChannel.ooh,
    "print": MediaChannel.print,
    "cinema": MediaChannel.cinema,
    "email": MediaChannel.email,
    "gaming": MediaChannel.gaming,
    "retail_media": MediaChannel.retail_media,
    "influencer": MediaChannel.influencer,
    "affiliate": MediaChannel.affiliate,
    "product_placement": MediaChannel.product_placement,
}


# BillingParty values this seller supports. The accounts.billing column permits
# {operator, agent} (ck_accounts_billing) and sync_accounts enforces the tenant's
# `supported_billing` policy against those parties, so both are honest defaults.
_DEFAULT_SUPPORTED_BILLING: list[BillingParty] = [BillingParty.operator, BillingParty.agent]


def _build_account_capability(tenant: dict | None) -> AccountCapability:
    """Build the `account` capability object with an HONEST sandbox declaration.

    sandbox=False (#1329 gap 13): this seller stores a per-account `sandbox` flag
    (a natural-key discriminator for sync_accounts + a list_accounts filter) but has
    NO behavioral isolation — a media buy under a sandbox account routes to the exact
    same live adapter path as production; `account.sandbox` is wholly disconnected
    from the `dry_run` testing hook (the only "no real spend" switch). The spec field
    mandates "Requests using a sandbox account perform no real platform calls or
    spend" (get-adcp-capabilities-response.json, AdCP 3.1.1); declaring `true`
    without that isolation is the same wire-honesty defect as `catalog_management=True`
    (fixed in PR #1276 R7-1). Declared False until behavioral sandbox isolation ships.
    The field is ungraded by the 3.1.1 storyboards, so there is no coverage cost to
    declaring it honestly. Mirrors the `catalog_management` / `property_list_filtering`
    honesty rationale on MediaBuyFeatures.

    require_operator_auth=False: accounts are buyer-declared via sync_accounts
    (brand + operator natural key, BR-RULE-056) — operators do not authenticate.

    supported_billing (required by the schema): the billing parties this seller
    accepts — from tenant config `supported_billing` when set, else {operator, agent}
    (what the accounts.billing constraint permits).

    required_for_products and account_financials default to False on the library type,
    and False is the honest value here: get_products is auth-optional and needs no
    account (required_for_products=False), and this seller exposes no account financial
    detail (account_financials=False). authorization_endpoint is left absent (no
    operator-auth endpoint, consistent with require_operator_auth=False).
    """
    valid_parties = {b.value for b in BillingParty}
    configured = tenant.get("supported_billing") if tenant else None
    billing = [BillingParty(v) for v in (configured or []) if v in valid_parties]
    if not billing:
        billing = list(_DEFAULT_SUPPORTED_BILLING)
    return AccountCapability(
        supported_billing=billing,
        sandbox=False,
        require_operator_auth=False,
    )


def _get_adcp_capabilities_impl(
    req: GetAdcpCapabilitiesRequest | None = None, identity: ResolvedIdentity | None = None
) -> GetAdcpCapabilitiesResponse:
    """Shared implementation for get_adcp_capabilities.

    Returns the capabilities of this sales agent per AdCP spec.

    Args:
        req: GetAdcpCapabilitiesRequest (optional, currently unused)
        identity: Resolved identity from transport boundary

    Returns:
        GetAdcpCapabilitiesResponse containing agent capabilities
    """
    # Extract principal and tenant from resolved identity
    principal_id = identity.principal_id if identity else None
    tenant = identity.tenant if identity else None

    if not tenant:
        # Return minimal capabilities if no tenant context
        return GetAdcpCapabilitiesResponse(
            adcp=Adcp(
                major_versions=[MajorVersion(root=3)],
                idempotency=Idempotency(supported=True, replay_ttl_seconds=int(DEFAULT_REPLAY_TTL.total_seconds())),
            ),
            supported_protocols=[SupportedProtocol.media_buy],
            specialisms=[AdcpSpecialism.sales_non_guaranteed],
            account=_build_account_capability(None),
        )

    # If we got here, tenant is truthy, which means identity was not None on line 84
    identity = require_identity(identity, context=req.context if req else None)

    tenant_id = tenant["tenant_id"]
    tenant_name = tenant.get("name", "Unknown")

    # Log activity
    log_tool_activity(identity, "get_adcp_capabilities")

    # Get adapter to determine channels and capabilities
    primary_channels: list[MediaChannel] = []
    adapter = None
    try:
        # Get the Principal object to pass to adapter
        principal = get_principal_object(principal_id, tenant_id=identity.tenant_id) if principal_id else None

        if principal:
            adapter = get_adapter(principal, dry_run=True, tenant=tenant)
            if adapter and hasattr(adapter, "default_channels"):
                for channel_name in adapter.default_channels:
                    if channel_name.lower() in CHANNEL_MAPPING:
                        primary_channels.append(CHANNEL_MAPPING[channel_name.lower()])
    except Exception as e:
        logger.warning(f"Could not get adapter channels: {e}")

    # Default to display if we couldn't determine from adapter
    if not primary_channels:
        primary_channels = [MediaChannel.display]

    # Get publisher domains from database
    publisher_domains: list[PublisherDomain] = []
    try:
        with TenantConfigUoW(tenant_id) as uow:
            assert uow.tenant_config is not None
            partners = uow.tenant_config.list_publisher_partners()
            for partner in partners:
                if partner.publisher_domain:
                    publisher_domains.append(PublisherDomain(root=partner.publisher_domain))
    except Exception as e:
        logger.warning(f"Could not get publisher domains: {e}")

    # If no domains found, use a placeholder
    if not publisher_domains:
        # Use tenant name as placeholder domain
        publisher_domains = [PublisherDomain(root=f"{tenant.get('subdomain', 'unknown')}.example.com")]

    # Get advertising policies from tenant config
    advertising_policies: str | None = None
    if tenant.get("advertising_policy"):
        policy = tenant["advertising_policy"]
        if isinstance(policy, dict) and policy.get("description"):
            advertising_policies = policy["description"]

    # Build portfolio
    portfolio = Portfolio(
        description=f"Advertising inventory from {tenant_name}",
        primary_channels=primary_channels if primary_channels else None,
        publisher_domains=publisher_domains,
        advertising_policies=advertising_policies,
    )

    # Build features - be honest about what we actually support
    # These should be adapter-dependent in the future
    features = MediaBuyFeatures(
        # inline_creative_management: We have sync_creatives/list_creatives tools
        inline_creative_management=True,
        # property_list_filtering: True iff the bound adapter actually compiles
        # `targeting_overlay.property_list` into native ad-server targeting.
        # Today no adapter sets this — capability remains False; create/update
        # emit per-package UNSUPPORTED_FEATURE advisories on the success envelope
        # so buyers can see the silent-drop window. Kevel's siteId resolver flips
        # this True and the other 4 adapters hard-reject — same source of truth
        # via `supports_property_list_filtering()`.
        property_list_filtering=supports_property_list_filtering(adapter),
        # catalog_management: declared False until a sync_catalogs tool ships.
        # AdCP spec binds this flag to the buyer-driven sync_catalogs task
        # (SyncCatalogsRequest with account + catalogs[] + delete_missing) —
        # NOT the internal admin CRUD over the products table. Declaring True
        # without the tool would let buyers reach the boundary and get
        # UNSUPPORTED_FEATURE there instead of being warned at capability
        # discovery. Mirrors the property_list_filtering=False rationale above.
        catalog_management=False,
    )

    # Build targeting capabilities from adapter
    targeting_caps = None
    if adapter and hasattr(adapter, "get_targeting_capabilities"):
        targeting_caps = adapter.get_targeting_capabilities()

    # Build GeoMetros if any metro targeting is supported
    geo_metros = None
    if targeting_caps and any(
        [
            targeting_caps.nielsen_dma,
            targeting_caps.eurostat_nuts2,
            targeting_caps.uk_itl1,
            targeting_caps.uk_itl2,
        ]
    ):
        geo_metros = GeoMetros(
            nielsen_dma=targeting_caps.nielsen_dma or None,
            eurostat_nuts2=targeting_caps.eurostat_nuts2 or None,
            uk_itl1=targeting_caps.uk_itl1 or None,
            uk_itl2=targeting_caps.uk_itl2 or None,
        )

    # Build PostalAreaSupport if any postal targeting is supported
    geo_postal_areas = None
    if targeting_caps and any(
        [
            targeting_caps.us_zip,
            targeting_caps.us_zip_plus_four,
            targeting_caps.ca_fsa,
            targeting_caps.ca_full,
            targeting_caps.gb_outward,
            targeting_caps.gb_full,
            targeting_caps.de_plz,
            targeting_caps.fr_code_postal,
            targeting_caps.au_postcode,
        ]
    ):
        geo_postal_areas = PostalAreaSupport(
            us_zip=targeting_caps.us_zip or None,
            us_zip_plus_four=targeting_caps.us_zip_plus_four or None,
            ca_fsa=targeting_caps.ca_fsa or None,
            ca_full=targeting_caps.ca_full or None,
            gb_outward=targeting_caps.gb_outward or None,
            gb_full=targeting_caps.gb_full or None,
            de_plz=targeting_caps.de_plz or None,
            fr_code_postal=targeting_caps.fr_code_postal or None,
            au_postcode=targeting_caps.au_postcode or None,
        )

    targeting = Targeting(
        geo_countries=targeting_caps.geo_countries if targeting_caps else True,
        geo_regions=targeting_caps.geo_regions if targeting_caps else True,
        geo_metros=geo_metros,
        geo_postal_areas=geo_postal_areas,
    )

    # Build execution capabilities
    execution = Execution(
        targeting=targeting,
    )

    # Build media_buy capabilities
    media_buy = MediaBuy(
        portfolio=portfolio,
        features=features,
        execution=execution,
    )

    # Specialisms audit (AdCP 3.1.1, #1329 gap 14). Each specialism maps to a
    # compliance storyboard bundle at /compliance/3.1.1/specialisms/{id}/, gated by
    # BOTH its parent protocol (must appear in supported_protocols) AND its
    # `required_tools` (compliance/3.1.1/index.json), which must all be implemented
    # end-to-end. We declare `media_buy` only, so any specialism whose parent
    # protocol is governance/creative/brand/signals/sponsored-intelligence is out on
    # the parent-protocol rule alone. The full audit against index.json:
    #
    #   DECLARED:
    #   - sales-non-guaranteed  — required_tools {sync_governance, get_products,
    #       create_media_buy}, all now implemented (sync_governance landed with #1329);
    #       storyboard grades sync_governance -> accounts[0].status="synced" (met).
    #
    #   NOT DECLARED (media_buy protocol, tool gap):
    #   - sales-guaranteed      — same required_tools; the submitted-task/IO-approval
    #       path exists, but the guaranteed IO-approval storyboard is not yet verified
    #       green end-to-end. Candidate for a follow-up once confirmed.
    #   - sales-broadcast-tv    — needs FCC-cancellation semantics we don't implement.
    #   - sales-catalog-driven  — needs conversion tracking + catalog we don't implement.
    #   - sales-social          — required_tools include sync_audiences, sync_catalogs,
    #       sync_event_sources, preview_creative (none implemented).
    #   - sales-proposal-mode   — DEPRECATED in 3.1 (folded into sales-guaranteed); do
    #       not declare a deprecated slot even though its tools happen to be present.
    #   - audience-sync         — needs sync_audiences (not implemented).
    #   - governance-aware-seller — needs the check_governance enforcement loop; we
    #       register bindings via sync_governance but deliberately do NOT enforce them.
    #
    #   NOT DECLARED (parent protocol not in supported_protocols):
    #   - collection-lists, content-standards, property-lists, governance-delivery-monitor,
    #       governance-spend-authority (parent: governance — not declared)
    #   - creative-ad-server, creative-generative, creative-template, creative-transformers
    #       (parent: creative — we CALL remote creative agents' build_creative; we don't
    #       EXPOSE it as our own tool, so the seller does not host the creative protocol)
    #   - brand-rights (parent: brand), signal-marketplace/signal-owned (parent: signals —
    #       signals tools were intentionally removed; they belong to dedicated signal
    #       agents), sponsored-intelligence (parent: sponsored-intelligence; PREVIEW/ungraded)
    #
    #   OTHER:
    #   - signed-requests — DEPRECATED in 3.1, no bundle; expressed via the
    #       `request_signing.supported` capability, not a specialism.
    response = GetAdcpCapabilitiesResponse(
        adcp=Adcp(
            major_versions=[MajorVersion(root=3)],
            idempotency=Idempotency(supported=True, replay_ttl_seconds=int(DEFAULT_REPLAY_TTL.total_seconds())),
        ),
        supported_protocols=[SupportedProtocol.media_buy],
        specialisms=[AdcpSpecialism.sales_non_guaranteed],
        account=_build_account_capability(tenant),
        media_buy=media_buy,
        last_updated=datetime.now(UTC),
    )

    return response


async def get_adcp_capabilities(
    protocols: list[str] | None = None,
    ctx: Context | None = None,
) -> ToolResult:
    """Get the capabilities of this AdCP sales agent.

    MCP tool wrapper aligned with adcp v3.x spec.

    Args:
        protocols: Specific protocols to query (optional, currently ignored)
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with human-readable text and structured data
    """
    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None

    # Build request object (currently minimal)
    req = GetAdcpCapabilitiesRequest()

    # Call shared implementation
    response = _get_adcp_capabilities_impl(req, identity)

    # Build human-readable summary
    protocols = [enum_value(p) for p in response.supported_protocols]
    summary_parts = [
        f"AdCP v{response.adcp.major_versions[0].root} Capabilities",
        f"Supported protocols: {', '.join(protocols)}",
    ]

    if response.media_buy and response.media_buy.portfolio:
        portfolio = response.media_buy.portfolio
        if portfolio.description:
            summary_parts.append(f"Portfolio: {portfolio.description}")
        if portfolio.primary_channels:
            channels = [enum_value(c) for c in portfolio.primary_channels]
            summary_parts.append(f"Channels: {', '.join(channels)}")

    summary = "\n".join(summary_parts)

    # Return ToolResult with human-readable text and structured data
    return ToolResult(content=summary, structured_content=response)


async def get_adcp_capabilities_raw(
    protocols: list[str] | None = None,
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
) -> GetAdcpCapabilitiesResponse:
    """Get the capabilities of this AdCP sales agent.

    Raw function without @mcp.tool decorator for A2A server use.

    Args:
        protocols: Specific protocols to query (optional, currently ignored)
        ctx: FastMCP context (automatically provided)
        identity: Pre-resolved identity (preferred over ctx)

    Returns:
        GetAdcpCapabilitiesResponse containing agent capabilities
    """
    if identity is None:
        from src.core.transport_helpers import resolve_identity_from_context

        identity = resolve_identity_from_context(ctx, require_valid_token=False)
    req = GetAdcpCapabilitiesRequest()
    return _get_adcp_capabilities_impl(req, identity)
