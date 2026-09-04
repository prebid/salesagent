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
from adcp.types.generated_poc.enums.channels import MediaChannel
from adcp.types.generated_poc.enums.specialism import AdcpSpecialism
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
    TrustedMatch,
)
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from sqlalchemy.exc import SQLAlchemyError

from src.core.auth import get_principal_object, require_identity
from src.core.database.repositories.idempotency_attempt import DEFAULT_REPLAY_TTL
from src.core.database.repositories.uow import TenantConfigUoW, TMPProviderUoW
from src.core.helpers import enum_value
from src.core.helpers.activity_helpers import log_tool_activity
from src.core.helpers.adapter_helpers import get_adapter
from src.core.logging_config import log_safe
from src.core.resolved_identity import ResolvedIdentity
from src.core.tool_context import ToolContext
from src.core.tools._mcp import mcp_result
from src.services.targeting_capabilities import supports_property_list_filtering

logger = logging.getLogger(__name__)

#: The experimental feature id for the Trusted Match surfaces this agent implements.
#:
#: AdCP 3.1.1, ``docs/reference/experimental-status`` (restated in the pinned
#: ``protocol/get-adcp-capabilities-response.json`` → ``experimental_features``):
#: "Sellers that implement any experimental surface MUST list its feature id
#: here… a seller that does not list a surface is asserting it does not implement
#: it." Seller-side Package Sync (``src/services/tmp_provider_sync``) is a
#: ``trusted_match.core`` surface and runs on every media-buy create/update, so
#: this declaration is owed the moment a tenant has a provider registered — there
#: is no "silently experimental" mode (#1197 review).
TRUSTED_MATCH_FEATURE_ID = "trusted_match.core"

#: The response contract this tool serves, declared once so tests grade the emitted
#: array against the schema's own item pattern rather than re-typing it. Resolved
#: by ``tests/helpers/pinned_schema`` and kept resolvable by the citation guard.
CAPABILITIES_RESPONSE_SCHEMA = "protocol/get-adcp-capabilities-response.json"


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


def _has_syncable_providers(tenant_id: str) -> bool:
    """Whether this tenant runs TMP — the one read both TMP declarations use.

    Two things on the capabilities response mean "this seller has TMP": the
    ``experimental_features`` entry (``trusted_match.core``) and the presence of
    ``media_buy.execution.trusted_match``. Both come from this, so they cannot
    disagree.

    Derived from the state that makes the surface real rather than from a
    constant: a tenant with no syncable TMP provider has nothing to sync, and a
    tenant with one has the seller-side Package Sync surface live on all four
    transports.

    The question is asked through ``TMPProviderRepository.has_syncable()`` — the
    same ``_SYNCABLE_STATUSES`` the two advertised surfaces filter on. Answering
    it with ``list_all()`` made a tenant whose only registration is ``inactive``
    advertise ``trusted_match.core`` while discovery returned ``[]`` and the sync
    no-oped (#1197 review).

    The caller omits both declarations when this is false — for
    ``experimental_features`` that means omitting the field rather than sending
    ``[]``, since the schema types it as an array and an empty one carries no more
    information than absence.

    Note what omission means: AdCP 3.1.1
    ``reference/experimental-status.mdx`` — "Sellers that do not list an
    experimental surface MUST NOT implement it — there is no 'silently
    experimental' mode". Omitting the field is therefore a positive claim, not a
    safe default, and ``fire_tmp_sync`` keeps POSTing to this tenant's providers
    either way. So only a *storage* failure is swallowed here; a programming
    error must surface rather than silently un-declare a live surface.
    """
    try:
        with TMPProviderUoW(tenant_id) as uow:
            return uow.tmp_providers.has_syncable()
    except SQLAlchemyError as e:
        logger.warning(
            "[TMP capabilities] Could not determine TMP deployment for tenant %s: %s",
            log_safe(tenant_id),
            e,
        )
        return False


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

    # One read of "does this tenant run TMP?", used by both declarations below.
    tmp_is_deployed = _has_syncable_providers(tenant_id)

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

    # Build execution capabilities.
    #
    # `trusted_match`'s presence is itself the declaration — the schema says
    # "Presence of this object indicates the seller has TMP infrastructure
    # deployed" — so it comes from the SAME predicate as the experimental_features
    # entry below. Emitting one and not the other would have the agent telling a
    # buyer it implements trusted_match.core while the capability block that means
    # "TMP is deployed here" was absent (#1197 review).
    #
    # `surfaces` is deliberately not populated: it enumerates which surface types
    # (website, ctv_app, …) the seller supports via TMP, which this codebase does
    # not model anywhere, and the property is optional. Declaring presence without
    # inventing a surface list is the truthful shape. Populating it is #1993.
    execution = Execution(
        targeting=targeting,
        trusted_match=TrustedMatch() if tmp_is_deployed else None,
    )

    # Build media_buy capabilities
    media_buy = MediaBuy(
        portfolio=portfolio,
        features=features,
        execution=execution,
    )

    # Build response
    # specialisms declaration activates the storyboard scenarios bundled under
    # `sales-non-guaranteed` (`inventory_list_targeting`, `inventory_list_no_match`,
    # `delivery_reporting`, `pending_creatives_to_start`, `invalid_transitions`).
    # The runner gates scenarios by specialism, not by `supported_protocols` alone.
    #
    # We declare the specialism even though `pending_creatives_to_start` and
    # `invalid_transitions` are not yet fully green. Storyboard compliance runs
    # are advisory — no required CI job executes them — so those scenario
    # failures don't block merge, and the public declaration forces
    # prioritization of the remaining gaps instead of hiding them.
    response = GetAdcpCapabilitiesResponse(
        adcp=Adcp(
            major_versions=[MajorVersion(root=3)],
            idempotency=Idempotency(supported=True, replay_ttl_seconds=int(DEFAULT_REPLAY_TTL.total_seconds())),
        ),
        supported_protocols=[SupportedProtocol.media_buy],
        specialisms=[AdcpSpecialism.sales_non_guaranteed],
        # NOT added to supported_protocols: a stable protocol claim also commits
        # the agent to that protocol's baseline compliance storyboard. The
        # experimental declaration is the obligation the diff creates.
        experimental_features=[TRUSTED_MATCH_FEATURE_ID] if tmp_is_deployed else None,
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

    return mcp_result(response, content=summary)


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
