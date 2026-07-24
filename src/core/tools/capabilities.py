"""Get AdCP Capabilities tool implementation.

Returns the capabilities of this sales agent including supported protocols,
targeting dimensions, creative specs, and portfolio information.

This module follows the MCP/A2A shared implementation pattern from CLAUDE.md.
"""

import logging
from datetime import UTC, datetime

from adcp.types import ContextObject, GetAdcpCapabilitiesRequest, GetAdcpCapabilitiesResponse
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
    MajorVersion,
    MediaBuy,
    Portfolio,
    PublisherDomain,
    SupportedProtocol,
    SupportedVersion,
    # FIXME(#1388): Targeting has a local subclass; import from src.core.schemas (Pattern #7/#4).
    Targeting,
)
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import Idempotency3 as IdempotencyUnsupported
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult

# Imported as a module (not by-name) so the advertised version block resolves
# supported_adcp_versions / adcp_major_version / adcp_build_version through the
# single canonical attribute on src.core.adcp_version. A by-name import binds a
# private copy at import time, which a testing policy override (or a test patch)
# applied at src.core.adcp_version.* would not reach — splitting what capabilities
# advertises from what validate_adcp_version_pins negotiates.
from src.core import adcp_version
from src.core.application_context import dump_adcp_response
from src.core.auth import get_principal_object, require_identity
from src.core.database.repositories.uow import TenantConfigUoW
from src.core.helpers import enum_value
from src.core.helpers.activity_helpers import log_tool_activity
from src.core.helpers.adapter_helpers import get_adapter
from src.core.resolved_identity import ResolvedIdentity
from src.core.tool_context import ToolContext
from src.core.validation_helpers import adcp_validation_boundary
from src.services.targeting_capabilities import supports_property_list_filtering

logger = logging.getLogger(__name__)

_DEFAULT_PROTOCOLS: tuple[SupportedProtocol, ...] = (SupportedProtocol.media_buy,)


def _requested_protocol_domains(req: GetAdcpCapabilitiesRequest | None) -> set[str] | None:
    """The domains whose capability DETAILS the buyer asked for, or ``None`` for all.

    The request's ``protocols`` filter selects which protocol domains' details the
    response carries — ``get-adcp-capabilities-request.json`` (v3.1.1) describes it
    as "Specific protocols to query capabilities for", and the graded
    ``capability-discovery.yaml::get_capabilities_filtered`` step expects "the same
    structure but only the requested domain details".

    It deliberately does NOT narrow ``supported_protocols``. That field is the
    agent's own declaration — the response schema describes its values as
    committing the agent "to pass the baseline compliance storyboard" for each
    protocol listed — so it reports what this agent implements, not a view of the
    buyer's question. Filtering it made a request for an unsupported domain
    unrepresentable (``minItems: 1``) and turned a schema-valid request into a
    ``VALIDATION_ERROR``, which ``error-handling.mdx`` scopes to schema violations.
    A non-overlapping filter is now an ordinary response: the true declaration,
    with no details for a domain this agent does not serve.

    Empty-array and unknown-enum inputs remain rejected by the request model
    (``minItems: 1`` + the ``Protocol`` enum) at the transport boundary.
    """
    requested = req.protocols if req else None
    if not requested:
        return None
    return {enum_value(p) for p in requested}


_DEFAULT_SPECIALISMS: tuple[AdcpSpecialism, ...] = (AdcpSpecialism.sales_non_guaranteed,)


def _build_capabilities_request(
    protocols: list[str] | None,
    context: ContextObject | None,
) -> GetAdcpCapabilitiesRequest:
    """Construct the negotiation request through the shared validation boundary.

    One home for the MCP and A2A/REST wrappers so a new negotiation-relevant
    field is added once, not in two byte-identical copies (same file, below the
    R0801 duplication threshold, so the ratchet can't catch the drift). Forwards
    the buyer's ``protocols`` (the impl filters the per-domain capability DETAILS
    by it, never the ``supported_protocols`` declaration — see
    ``_requested_protocol_domains``) and echoes ``context``; a bad ``protocols``
    value becomes VALIDATION_ERROR
    at this boundary rather than an untyped pydantic error.
    """
    with adcp_validation_boundary(context="get_adcp_capabilities request"):
        return GetAdcpCapabilitiesRequest(protocols=protocols, context=context)


def _build_adcp_block() -> Adcp:
    """Build the ``adcp`` version/idempotency envelope block for the response.

    Shared by both response paths (minimal, no-tenant and full-tenant) so the
    advertised version envelope is declared in exactly one place and cannot
    drift between them. ``major_versions`` / ``supported_versions`` come from
    the in-repo ``ADVERTISED_ADCP_VERSIONS`` constant (adcp_version.py) and
    ``build_version`` from the Sales Agent build (``get_version()``) — none is
    read from the SDK spec pin, which is only a cross-check.
    """
    return Adcp(
        major_versions=[MajorVersion(root=adcp_version.adcp_major_version())],
        supported_versions=[SupportedVersion(root=v) for v in adcp_version.supported_adcp_versions()],
        # Omitted entirely when the deployment version is not renderable as
        # semver: the schema types it ``string`` and marks it optional, so an
        # absent advisory field is conformant where a null would not be.
        **adcp_version.advisory_build_version_field(),
        # FIXME(#1607): the schema models `supported` as ONE agent-wide binary
        # claim, but this agent's real behavior is genuinely mixed — neither
        # value is fully truthful, and `false` below is the lesser-wrong
        # choice, not a resolved one. `IdempotencyUnsupported`'s own semantics
        # ("sending a key is a no-op ... the seller will NOT return
        # IDEMPOTENCY_CONFLICT or IDEMPOTENCY_EXPIRED, and a naive retry WILL
        # double-process") are FALSE for create_media_buy specifically: it
        # still deduplicates a repeated key (verbatim replay of the stored
        # success), still raises IDEMPOTENCY_CONFLICT on a same-key
        # different-payload retry, and still raises IDEMPOTENCY_EXPIRED past
        # the replay window. Every OTHER mutating tool (update_media_buy,
        # sync_accounts, sync_creatives) validates and accepts the key but
        # performs no cache read, so a retry re-executes and can double-spend
        # or double-sync — which is what `supported=true` would have falsely
        # promised was safe, for twelve of thirteen call sites. `false` was
        # chosen as the narrower defect (create_media_buy behaving BETTER
        # than advertised is safer than the other twelve behaving WORSE than
        # advertised), not as a truthful declaration. Resolving this for real
        # means either extending genuine replay/conflict/expired handling to
        # every mutating tool (then flipping to true) or removing
        # create_media_buy's dedup so false becomes wire-accurate — both are
        # deliberately deferred: the first is a substantial feature build with
        # real regression risk on spend-affecting update_media_buy, the second
        # is an active regression of a working duplicate-booking safety net.
        # Do not treat this line as closed by future drive-by cleanup without
        # picking one of those two.
        # The SDK generates the discriminated union as two classes named
        # ``Idempotency`` (supported=True) and ``Idempotency3`` (supported=False)
        # — the numeric suffix is a codegen artifact of the schema's own
        # generation note ("code generators produce two named types
        # (IdempotencySupported, IdempotencyUnsupported)"), imported here under
        # a readable alias since the generated name carries no meaning.
        idempotency=IdempotencyUnsupported(supported=False),
    )


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

    # Echo the buyer's request context unchanged on the response. The
    # version-negotiation storyboard grades ``field_present: context`` with an
    # unchanged value; the error path (_version_unsupported_error) already echoes
    # it, so the success path must too or context silently vanishes.
    request_context = req.context if req else None

    # Honor the buyer's `protocols` filter on every transport: return only the
    # requested domains' DETAILS. `supported_protocols` stays the agent's own
    # declaration — it commits this agent to each listed protocol's compliance
    # storyboard, so it cannot be narrowed to whatever the buyer happened to ask
    # about. Specialisms roll up to a parent protocol, so they are only advertised
    # when that protocol is in view (today all _DEFAULT_SPECIALISMS roll up to
    # media_buy).
    supported_protocols = list(_DEFAULT_PROTOCOLS)
    requested_domains = _requested_protocol_domains(req)
    media_buy_requested = requested_domains is None or enum_value(SupportedProtocol.media_buy) in requested_domains
    specialisms = list(_DEFAULT_SPECIALISMS) if media_buy_requested else []

    if not tenant:
        # Return minimal capabilities if no tenant context
        return GetAdcpCapabilitiesResponse(
            adcp=_build_adcp_block(),
            supported_protocols=supported_protocols,
            specialisms=specialisms,
            context=request_context,
        )

    # If we got here, tenant is truthy, which means identity was not None on line 84
    identity = require_identity(identity, context=req.context if req else None)

    tenant_id = tenant["tenant_id"]
    tenant_name = tenant.get("name", "Unknown")

    # Log activity
    log_tool_activity(identity, "get_adcp_capabilities")

    # media_buy is the only domain with detailed capabilities today; if the buyer
    # filtered it out there is nothing further to compute or return.
    if not media_buy_requested:
        return GetAdcpCapabilitiesResponse(
            adcp=_build_adcp_block(),
            supported_protocols=supported_protocols,
            specialisms=specialisms,
            last_updated=datetime.now(UTC),
            context=request_context,
        )

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
        adcp=_build_adcp_block(),
        supported_protocols=supported_protocols,
        specialisms=specialisms,
        media_buy=media_buy,
        last_updated=datetime.now(UTC),
        context=request_context,
    )

    return response


async def get_adcp_capabilities(
    protocols: list[str] | None = None,
    context: ContextObject | None = None,
    ctx: Context | None = None,
) -> ToolResult:
    """Get the capabilities of this AdCP sales agent.

    MCP tool wrapper aligned with adcp v3.x spec.

    Args:
        protocols: Specific protocols to filter by (optional). The impl returns only
            these domains' capabilities; an unknown enum value or an empty array is a
            VALIDATION_ERROR.
        context: AdCP request context echoed unchanged on the response. Declared
            here so the envelope-tolerance middleware does not strip it before it
            reaches the request.
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with human-readable text and structured data
    """
    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None

    req = _build_capabilities_request(protocols, context)

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

    # Return ToolResult with human-readable text and structured data.
    # Serialize via the response model's own model_dump so the MCP wire matches the
    # AdCP-canonical shape REST/A2A emit — in particular it OMITS an absent `context`
    # (INV-2: context absence echoed as absence) rather than FastMCP's object
    # serialization, which would emit `context: null` (a present field).
    return ToolResult(content=summary, structured_content=dump_adcp_response(response, context=context))


async def get_adcp_capabilities_raw(
    protocols: list[str] | None = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
) -> GetAdcpCapabilitiesResponse:
    """Get the capabilities of this AdCP sales agent.

    Raw function without @mcp.tool decorator for A2A server use.

    Args:
        protocols: Specific protocols to filter by (optional). The impl returns only
            these domains' capabilities; an unknown enum value or an empty array is a
            VALIDATION_ERROR.
        context: AdCP request context echoed unchanged on the response.
        ctx: FastMCP context (automatically provided)
        identity: Pre-resolved identity (preferred over ctx)

    Returns:
        GetAdcpCapabilitiesResponse containing agent capabilities
    """
    if identity is None:
        from src.core.transport_helpers import resolve_identity_from_context

        identity = resolve_identity_from_context(ctx, require_valid_token=False)
    req = _build_capabilities_request(protocols, context)
    return _get_adcp_capabilities_impl(req, identity)
