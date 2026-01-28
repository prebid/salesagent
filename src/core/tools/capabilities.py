"""Get AdCP Capabilities tool implementation.

Returns the capabilities of this sales agent including supported protocols,
targeting dimensions, creative specs, and portfolio information.

This module follows the MCP/A2A shared implementation pattern from CLAUDE.md.
"""

import logging
from datetime import UTC, datetime

from adcp.types import GetAdcpCapabilitiesRequest, GetAdcpCapabilitiesResponse
from adcp.types.generated_poc.core.media_buy_features import MediaBuyFeatures
from adcp.types.generated_poc.enums.channels import MediaChannel
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
    Adcp,
    Execution,
    GeoMetros,
    GeoPostalAreas,
    MajorVersion,
    MediaBuy,
    Portfolio,
    PublisherDomain,
    SupportedProtocol,
    Targeting,
)
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from sqlalchemy import select

from src.core.auth import get_principal_from_context, get_principal_object
from src.core.config_loader import get_current_tenant, set_current_tenant
from src.core.database.database_session import get_db_session
from src.core.database.models import PublisherPartner
from src.core.helpers.activity_helpers import log_tool_activity
from src.core.helpers.adapter_helpers import get_adapter
from src.core.tool_context import ToolContext

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


def _get_adcp_capabilities_impl(
    req: GetAdcpCapabilitiesRequest | None = None, context: Context | ToolContext | None = None
) -> GetAdcpCapabilitiesResponse:
    """Shared implementation for get_adcp_capabilities.

    Returns the capabilities of this sales agent per AdCP spec.

    Args:
        req: GetAdcpCapabilitiesRequest (optional, currently unused)
        context: FastMCP Context for tenant/principal resolution

    Returns:
        GetAdcpCapabilitiesResponse containing agent capabilities
    """
    # Get tenant and principal from context
    # Authentication is OPTIONAL for capabilities endpoint (returns public info)
    principal_id, tenant = get_principal_from_context(
        context,
        require_valid_token=False,
    )

    # Set tenant context if returned, or try to get existing context
    if tenant:
        set_current_tenant(tenant)
    else:
        try:
            tenant = get_current_tenant()
        except RuntimeError:
            # No tenant context available - return minimal capabilities
            tenant = None

    if not tenant:
        # Return minimal capabilities if no tenant context
        return GetAdcpCapabilitiesResponse(
            adcp=Adcp(major_versions=[MajorVersion(root=3)]),
            supported_protocols=[SupportedProtocol.media_buy],
        )

    tenant_id = tenant["tenant_id"]
    tenant_name = tenant.get("name", "Unknown")

    # Log activity
    if context:
        log_tool_activity(context, "get_adcp_capabilities")

    # Get adapter to determine channels and capabilities
    primary_channels: list[MediaChannel] = []
    adapter = None
    try:
        # Get the Principal object to pass to adapter
        principal = get_principal_object(principal_id) if principal_id else None

        if principal:
            adapter = get_adapter(principal, dry_run=True)
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
        with get_db_session() as session:
            stmt = select(PublisherPartner).filter_by(tenant_id=tenant_id)
            partners = session.scalars(stmt).all()
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
        # content_standards: We have creative review but not full AdCP content standards
        content_standards=False,
        # inline_creative_management: We have sync_creatives/list_creatives tools
        inline_creative_management=True,
        # property_list_filtering: Not implemented yet
        property_list_filtering=False,
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

    # Build GeoPostalAreas if any postal targeting is supported
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
        geo_postal_areas = GeoPostalAreas(
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

    # Build response
    response = GetAdcpCapabilitiesResponse(
        adcp=Adcp(major_versions=[MajorVersion(root=3)]),
        supported_protocols=[SupportedProtocol.media_buy],
        media_buy=media_buy,
        last_updated=datetime.now(UTC),
    )

    return response


async def get_adcp_capabilities(
    protocols: list[str] | None = None,
    ctx: Context | ToolContext | None = None,
) -> ToolResult:
    """Get the capabilities of this AdCP sales agent.

    MCP tool wrapper aligned with adcp v3.x spec.

    Args:
        protocols: Specific protocols to query (optional, currently ignored)
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with human-readable text and structured data
    """
    # Build request object (currently minimal)
    req = GetAdcpCapabilitiesRequest()

    # Call shared implementation
    response = _get_adcp_capabilities_impl(req, ctx)

    # Build human-readable summary
    protocols = [p.value if hasattr(p, "value") else str(p) for p in response.supported_protocols]
    summary_parts = [
        f"AdCP v{response.adcp.major_versions[0].root} Capabilities",
        f"Supported protocols: {', '.join(protocols)}",
    ]

    if response.media_buy and response.media_buy.portfolio:
        portfolio = response.media_buy.portfolio
        if portfolio.description:
            summary_parts.append(f"Portfolio: {portfolio.description}")
        if portfolio.primary_channels:
            channels = [c.value if hasattr(c, "value") else str(c) for c in portfolio.primary_channels]
            summary_parts.append(f"Channels: {', '.join(channels)}")

    summary = "\n".join(summary_parts)

    # Return ToolResult with human-readable text and structured data
    response_dict = response.model_dump(mode="json")
    return ToolResult(content=summary, structured_content=response_dict)


async def get_adcp_capabilities_raw(
    protocols: list[str] | None = None,
    ctx: Context | ToolContext | None = None,
) -> GetAdcpCapabilitiesResponse:
    """Get the capabilities of this AdCP sales agent.

    Raw function without @mcp.tool decorator for A2A server use.

    Args:
        protocols: Specific protocols to query (optional, currently ignored)
        ctx: FastMCP context (automatically provided)

    Returns:
        GetAdcpCapabilitiesResponse containing agent capabilities
    """
    req = GetAdcpCapabilitiesRequest()
    return _get_adcp_capabilities_impl(req, ctx)
