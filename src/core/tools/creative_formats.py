"""AdCP tool implementation.

This module contains tool implementations following the MCP/A2A shared
implementation pattern from CLAUDE.md.

SDK 5.7 type:ignore tracking (adcontextprotocol/adcp-client-python#913):
- [valid-type] on lines ~98, ~236: SDK asset class unions (ImageFormatAsset |
  VideoFormatAsset | ...) are dynamically resolved type factories; mypy cannot
  validate the union. Permanent until upstream ships StrEnum.
"""

import asyncio
import concurrent.futures
import logging
import time
from typing import TYPE_CHECKING

# FIXME(#1388): FormatId has a local subclass; import from src.core.schemas (Pattern #7/#4).
from adcp.types import (
    AudioFormatAsset,
    CreativeAgentCapability,
    HtmlFormatAsset,
    ImageFormatAsset,
    TextFormatAsset,
    UrlFormatAsset,
    VideoFormatAsset,
)
from adcp.types import Format as AdcpFormat
from adcp.utils.format_assets import get_format_assets

# Format subclass preserved through backward-compatibility helper (PEP 695 type param below).
from src.core.exceptions import AdCPSalesAgentError, AdCPServiceUnavailableError
from src.core.helpers import enum_value

logger = logging.getLogger(__name__)

# Capabilities advertised for every creative agent referral in
# list_creative_formats. AdCP design principle: capabilities are commitments —
# the conformance runner probes each advertised capability, so only declare
# what the registry integration actually backs:
#   - validation/preview: backed by the agent's `preview_creative` tool
#     (CreativeAgentRegistry.preview_creative, used by sync_creatives for
#     validation + preview generation).
#   - assembly: backed by the agent's `build_creative` tool
#     (CreativeAgentRegistry.build_creative, used by sync_creatives refinement).
#   - delivery is deliberately NOT advertised: it commits to variant-level
#     delivery reporting via a `get_creative_delivery` task, which this agent
#     does not implement.
# All configured agents (default + tenant DB agents) are called through the
# registry's uniform client surface, and agent config carries no per-agent
# capability metadata yet, so a single static set is the best truthful
# declaration available today. Caveat: the registry does not verify a remote
# agent's tool surface up front — a tenant-registered agent that lacks one of
# these tools fails at call time. Per-agent capability tracking is the
# follow-up that would close that gap.
ADVERTISED_CREATIVE_AGENT_CAPABILITIES: tuple[CreativeAgentCapability, ...] = (
    CreativeAgentCapability.validation,
    CreativeAgentCapability.assembly,
    CreativeAgentCapability.preview,
)


def _ensure_backward_compatible_format[FormatT: AdcpFormat](f: FormatT) -> FormatT:
    """Return *f* unchanged, as the same Format subclass it was given.

    Nothing is reshaped: the pinned Format declares one ``assets`` list whose entries
    carry their own ``required`` flag, so there is no second field to populate from it.

    Args:
        f: Format object from creative agent

    Returns:
        The same object, unchanged.
    """
    return f


from adcp import ErrorCode

from src.core.audit_logger import get_audit_logger
from src.core.resolved_identity import PublicIdentity
from src.core.schemas import Error as AdCPResponseError

if TYPE_CHECKING:
    from src.core.creative_agent_registry import FormatFetchResult
from src.core.schemas import ListCreativeFormatsRequest, ListCreativeFormatsResponse, format_id_identity


def _infer_asset_type(asset_id: str) -> str:
    """Infer asset type from asset ID naming convention.

    Args:
        asset_id: Asset identifier (e.g., "front_image", "youtube_url", "headline")

    Returns:
        Asset type string (image, video, text, url)
    """
    asset_lower = asset_id.lower()
    if "image" in asset_lower or "logo" in asset_lower:
        return "image"
    elif "video" in asset_lower or "youtube" in asset_lower:
        return "video"
    elif "url" in asset_lower or "click" in asset_lower:
        return "url"
    elif "html" in asset_lower:
        return "html"
    else:
        return "text"  # Default to text for headlines, body, captions, etc.


# Each adcp Assets variant uses a Literal discriminator for asset_type.
# Map asset type strings to the correct class.
_ASSET_TYPE_TO_CLASS: dict[str, type] = {
    "image": ImageFormatAsset,
    "video": VideoFormatAsset,
    "audio": AudioFormatAsset,
    "text": TextFormatAsset,
    "html": HtmlFormatAsset,
    "url": UrlFormatAsset,
}


def _make_asset(
    asset_id: str, asset_type: str, required: bool
) -> ImageFormatAsset | VideoFormatAsset | AudioFormatAsset | TextFormatAsset | HtmlFormatAsset | UrlFormatAsset:  # type: ignore[valid-type]
    """Build the correct Assets variant for a given asset type string."""
    cls = _ASSET_TYPE_TO_CLASS.get(asset_type, TextFormatAsset)  # default to text
    return cls(
        item_type="individual",
        asset_id=asset_id,
        asset_type=asset_type,
        required=required,
    )


def _list_creative_formats_impl(
    req: ListCreativeFormatsRequest | None, identity: PublicIdentity
) -> ListCreativeFormatsResponse:
    """List all available creative formats (AdCP spec endpoint).

    Returns formats from all registered creative agents (default + tenant-specific).
    Uses CreativeAgentRegistry for dynamic format discovery with caching.
    Supports optional filtering by type, standard_only, category, and format_ids.
    """
    start_time = time.time()

    # Use default request if none provided
    # All ListCreativeFormatsRequest fields have defaults (None) per AdCP spec
    if req is None:
        req = ListCreativeFormatsRequest()

    principal_id = identity.principal_id
    # Always a tenant: a request naming no seller is refused CONFIGURATION_ERROR by the
    # resolver, so there is no empty-format answer for one.
    tenant = identity.tenant

    # Get formats from all registered creative agents via registry
    from src.core.creative_agent_registry import FormatFetchResult, get_creative_agent_registry

    try:
        registry = get_creative_agent_registry()
    except AdCPSalesAgentError:
        raise
    except Exception as e:
        logger.error(f"Failed to create creative agent registry: {e}", exc_info=True)
        raise AdCPServiceUnavailableError(internal_detail=e) from e

    # Use list_all_formats_with_errors() to get per-agent error reporting (FD-ERR-01, FD-ERR-02)
    try:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                lambda: asyncio.run(registry.list_all_formats_with_errors(tenant_id=tenant.tenant_id))
            )
            fetch_result: FormatFetchResult = future.result()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            fetch_result = loop.run_until_complete(registry.list_all_formats_with_errors(tenant_id=tenant.tenant_id))
        finally:
            loop.close()

    formats = fetch_result.formats
    agent_errors = _route_agent_failures(fetch_result, req)

    # Get formats from adapter if it provides them (e.g., Broadstreet acting as both sales and creative agent)
    # Check adapter type from tenant config and load formats without instantiating the full adapter
    try:
        from src.core.database.repositories.uow import TenantConfigUoW

        with TenantConfigUoW(tenant.tenant_id) as uow:
            assert uow.tenant_config is not None
            config_row = uow.tenant_config.get_adapter_config()
            adapter_type = config_row.adapter_type if config_row else None

            if adapter_type == "broadstreet":
                # Import Broadstreet templates and convert to formats
                from src.adapters.broadstreet.config_schema import BROADSTREET_TEMPLATES
                from src.core.schemas import Format, FormatId, url

                agent_url = f"broadstreet://{tenant.tenant_id}"

                for template_id, template in BROADSTREET_TEMPLATES.items():
                    try:
                        format_id = FormatId(
                            id=f"broadstreet_{template_id}",
                            agent_url=url(agent_url),
                        )

                        # Build assets list using the correct Assets variant per type
                        assets_list: list[  # type: ignore[valid-type]
                            ImageFormatAsset
                            | VideoFormatAsset
                            | AudioFormatAsset
                            | TextFormatAsset
                            | HtmlFormatAsset
                            | UrlFormatAsset
                        ] = []
                        for asset_id in template.get("required_assets", []):
                            asset_type = _infer_asset_type(asset_id)
                            assets_list.append(_make_asset(asset_id, asset_type, required=True))
                        for asset_id in template.get("optional_assets", []):
                            asset_type = _infer_asset_type(asset_id)
                            assets_list.append(_make_asset(asset_id, asset_type, required=False))

                        fmt = Format(
                            format_id=format_id,
                            name=str(template["name"]),
                            description=str(template["description"]) if template.get("description") else None,
                            assets=assets_list if assets_list else None,
                            is_standard=False,
                            platform_config=None,
                            category=None,
                            requirements=None,
                            iab_specification=None,
                            accepts_3p_tags=None,
                        )
                        formats.append(fmt)
                    except Exception as e:
                        # FIXME(#1566): silent per-item failure — unparseable template is
                        # dropped from the formats response with no signal to the caller.
                        # Allowlisted in test_architecture_no_silent_loop_failures.py.
                        logger.warning(f"Failed to parse Broadstreet template {template_id}: {e}")
                        continue

                logger.info(f"Added {len(BROADSTREET_TEMPLATES)} Broadstreet formats")
    except Exception as e:
        # FIXME(#1566): silent degradation — the adapter's formats are dropped from
        # the response with no errors[] entry, so a failed lookup is indistinguishable
        # from "this adapter provides none".
        # Allowlisted in test_architecture_no_silent_loop_failures.py.
        logger.debug(f"Could not get adapter formats: {e}")

    # Apply filters from request
    if req.format_ids:
        # v3.1 federation contract: a format_id is identified by the (agent_url, id)
        # PAIR, not id alone (core/format-id.json requires [agent_url, id]; the
        # list_formats storyboard step matches references with
        # match_keys: [agent_url, id]). Matching on id alone would mis-resolve a
        # third-party reference (foreign agent_url) to a local format that merely
        # shares an id — fabricating a local entry for a format this seller does
        # not host. A foreign-agent reference simply matches nothing here and drops
        # out as an observation, never a fabricated entry (storyboard
        # scope.equals=$agent_url, on_out_of_scope=warn).
        requested_identities = {format_id_identity(fid) for fid in req.format_ids}
        formats = [f for f in formats if format_id_identity(f.format_id) in requested_identities]

    # Helper functions to extract properties from Format structure per AdCP spec
    def is_format_responsive(f) -> bool:
        """Check if format is responsive by examining renders.dimensions.responsive."""
        if not f.renders:
            return False
        for render in f.renders:
            dims = getattr(render, "dimensions", None)
            if dims and getattr(dims, "responsive", None):
                responsive = dims.responsive
                # Responsive if either width or height is fluid
                if getattr(responsive, "width", False) or getattr(responsive, "height", False):
                    return True
        return False

    def get_format_dimensions(f) -> list[tuple[int | None, int | None]]:
        """Get all (width, height) pairs from format renders."""
        dimensions: list[tuple[int | None, int | None]] = []
        if not f.renders:
            return dimensions
        for render in f.renders:
            dims = getattr(render, "dimensions", None)
            if dims:
                w = getattr(dims, "width", None)
                h = getattr(dims, "height", None)
                if w is not None or h is not None:
                    dimensions.append((w, h))
        return dimensions

    def get_format_disclosure_positions(f) -> set[str]:
        """The disclosure positions a format declares, by the pin's two-source order.

        ``media-buy/list-creative-formats-request.json`` states the order on the
        ``disclosure_positions`` filter itself: "Filter to formats that support all of
        these disclosure positions. When a format has disclosure_capabilities, match
        against those positions. Otherwise fall back to
        supported_disclosure_positions." So ``disclosure_capabilities`` is not merged
        with the flat list — it SUPERSEDES it, which ``core/format.json`` says in the
        other direction ("When present, supersedes supported_disclosure_positions...
        The flat supported_disclosure_positions field is retained for backward
        compatibility").

        A format declaring NEITHER yields the empty set, and that is the pinned answer
        rather than a missing-data escape: ``core/format.json`` on
        ``supported_disclosure_positions`` — "When omitted, the format makes no
        disclosure rendering guarantees — creative agents SHOULD treat this as
        incompatible with briefs that require specific disclosure positions." An empty
        set is a subset of nothing the filter can request (the request's ``minItems: 1``
        means a present filter always asks for at least one position), so such a format
        drops out, which is what that SHOULD requires.

        Normalized through ``enum_value`` on both sides for the same reason
        ``get_format_asset_types`` is: the request carries ``DisclosurePosition``
        members while a registry format may carry the plain strings it was built from,
        and comparing a member to its own value silently matches nothing.
        """
        if f.disclosure_capabilities:
            return {enum_value(c.position) for c in f.disclosure_capabilities if c.position is not None}
        return {enum_value(p) for p in (f.supported_disclosure_positions or [])}

    def get_format_asset_types(f) -> set[str]:
        """Get all asset types from format's assets.

        Uses adcp.utils.get_format_assets() which handles backward compatibility
        with deprecated assets_required field automatically.
        """
        types: set[str] = set()
        for asset_req in get_format_assets(f):
            # Handle both individual assets and repeatable groups
            asset_type = getattr(asset_req, "asset_type", None)
            if asset_type:
                types.add(str(asset_type))
            # For repeatable groups, check nested assets
            assets = getattr(asset_req, "assets", None)
            if assets:
                for asset in assets:
                    at = getattr(asset, "asset_type", None)
                    if at:
                        types.add(str(at))
        return types

    # Filter by is_responsive (AdCP filter)
    # Checks renders.dimensions.responsive per AdCP spec
    if req.is_responsive is not None:
        formats = [f for f in formats if is_format_responsive(f) == req.is_responsive]

    # Filter by name_search (case-insensitive partial match)
    if req.name_search:
        search_term = req.name_search.lower()
        formats = [f for f in formats if search_term in f.name.lower()]

    # Filter by asset_types - formats must support at least one of the requested types
    if req.asset_types:
        # Normalize requested asset types to string values for comparison.
        # adcp 3.6.0: req.asset_types contains AssetContentType enums; use .value to get string.
        # Format assets now use plain string literals, so must compare using .value not str(enum).
        requested_types = {enum_value(at) for at in req.asset_types}
        formats = [f for f in formats if get_format_asset_types(f) & requested_types]

    # Filter by dimension constraints
    # Per AdCP spec, matches if ANY render has dimensions matching the constraints
    # Formats without dimension info are excluded when dimension filters are applied
    if req.min_width is not None:
        formats = [f for f in formats if any(w and w >= req.min_width for w, h in get_format_dimensions(f))]
    if req.max_width is not None:
        formats = [f for f in formats if any(w and w <= req.max_width for w, h in get_format_dimensions(f))]
    if req.min_height is not None:
        formats = [f for f in formats if any(h and h >= req.min_height for w, h in get_format_dimensions(f))]
    if req.max_height is not None:
        formats = [f for f in formats if any(h and h <= req.max_height for w, h in get_format_dimensions(f))]

    # Filter by wcag_level - hierarchical: A < AA < AAA
    # Formats must meet at least the requested level; formats without accessibility are excluded
    if req.wcag_level is not None:
        from adcp.types import WcagLevel

        _WCAG_ORDER = {WcagLevel.A: 1, WcagLevel.AA: 2, WcagLevel.AAA: 3}
        min_level = _WCAG_ORDER.get(req.wcag_level, 0)
        formats = [
            f
            for f in formats
            if f.accessibility is not None and _WCAG_ORDER.get(f.accessibility.wcag_level, 0) >= min_level
        ]

    # Filter by disclosure_positions — AND semantics, unlike every filter around it.
    # media-buy/list-creative-formats-request.json: "Filter to formats that support all
    # of these disclosure positions" -- "all", not "any", so the
    # requested set must be a SUBSET of what the format declares. asset_types and the
    # two format-id filters intersect (OR); this one contains (AND), and getting that
    # backwards would hand a buyer a format missing a position they asked for.
    # get_format_disclosure_positions owns the disclosure_capabilities ->
    # supported_disclosure_positions fallback the same schema prescribes.
    if req.disclosure_positions:
        requested_positions = {enum_value(p) for p in req.disclosure_positions}
        formats = [f for f in formats if requested_positions <= get_format_disclosure_positions(f)]

    # Filter by output_format_ids / input_format_ids (OR semantics each).
    # These $ref the same core/format-id.json schema as format_ids, so they carry
    # the same (agent_url, id) federation identity — match on the pair via
    # format_id_identity, never id alone, for the same reason as the format_ids
    # filter above (id-only would mis-resolve a foreign reference to a local format
    # sharing an id). The storyboard grades refs_resolve on the top-level format_id
    # only, but the contract is symmetric across every FormatId reference.
    for req_ids, attr in (
        (req.output_format_ids, "output_format_ids"),
        (req.input_format_ids, "input_format_ids"),
    ):
        if req_ids:
            requested = {format_id_identity(fid) for fid in req_ids}
            formats = [
                f
                for f in formats
                if getattr(f, attr) and {format_id_identity(fid) for fid in getattr(f, attr)} & requested
            ]

    # Sort formats by name for consistent ordering
    # (type field removed in adcp 3.12)
    formats.sort(key=lambda f: f.name or "")

    # A no-op today: the helper returns each format unchanged, because the pinned Format
    # carries required/optional on the asset itself. Nothing is populated here.
    formats = [_ensure_backward_compatible_format(f) for f in formats]

    # Apply cursor-based pagination (AdCP PaginationRequest spec)
    total_count = len(formats)
    max_results = 50  # AdCP default
    start_index = 0

    if req.pagination is not None:
        if req.pagination.max_results is not None:
            max_results = req.pagination.max_results
        if req.pagination.cursor is not None:
            import base64

            try:
                start_index = int(base64.b64decode(req.pagination.cursor).decode("utf-8"))
            except ValueError:
                start_index = 0

    end_index = start_index + max_results
    has_more = end_index < total_count
    page_formats = formats[start_index:end_index]

    # Build pagination response
    from adcp.types import PaginationResponse

    next_cursor = None
    if has_more:
        import base64

        next_cursor = base64.b64encode(str(end_index).encode("utf-8")).decode("utf-8")

    pagination_response = PaginationResponse(
        has_more=has_more,
        cursor=next_cursor,
        total_count=total_count,
    )

    # Build creative_agents referrals from registry (POST-S4)
    from adcp.types.generated_poc.media_buy.list_creative_formats_response import (
        CreativeAgent as AdcpCreativeAgent,
    )

    creative_agents_list: list[AdcpCreativeAgent] | None = None
    try:
        agents = registry._get_tenant_agents(tenant.tenant_id)
        if agents:
            creative_agents_list = []
            for agent in agents:
                creative_agents_list.append(
                    AdcpCreativeAgent(
                        agent_url=agent.agent_url,
                        agent_name=agent.name,
                        capabilities=list(ADVERTISED_CREATIVE_AGENT_CAPABILITIES),
                    )
                )
    except Exception:
        # FIXME(#1566): silent degradation — creative_agents referrals are dropped
        # from the response with no errors[] entry, so the buyer reads a referral
        # lookup failure as "this seller federates to no creative agents".
        # Allowlisted in test_architecture_no_silent_loop_failures.py.
        logger.warning("Failed to build agent referrals for tenant %s", tenant.tenant_id, exc_info=True)

    # Log the operation
    audit_logger = get_audit_logger("AdCP", tenant.tenant_id)
    audit_logger.log_operation(
        operation="list_creative_formats",
        principal_name=principal_id or "anonymous",
        principal_id=principal_id or "anonymous",
        adapter_id="N/A",
        success=True,
        details={
            "format_count": len(page_formats),
            "total_count": total_count,
            "standard_formats": len([f for f in page_formats if f.is_standard]),
            "custom_formats": len([f for f in page_formats if not f.is_standard]),
            "format_count_standard": len([f for f in page_formats if f.is_standard]),
        },
    )

    # Create response (no message/specification_version - not in adapter schema)
    # Determine sandbox flag from identity (BR-RULE-209 INV-4)
    sandbox_flag: bool | None = None

    # Format list from registry is compatible with library Format type
    response = ListCreativeFormatsResponse(
        message=f"Found {len(page_formats)} creative format{'s' if len(page_formats) != 1 else ''}."
        if page_formats
        else "No creative formats are currently supported.",
        formats=page_formats,
        creative_agents=creative_agents_list,
        errors=agent_errors if agent_errors else None,
        pagination=pagination_response,
        sandbox=sandbox_flag,
    )

    # Always return Pydantic model - MCP wrapper will handle serialization
    # Schema enhancement (if needed) should happen in the MCP wrapper, not here
    return response


def _route_agent_failures(
    fetch_result: "FormatFetchResult",
    req: ListCreativeFormatsRequest,
) -> list[AdCPResponseError]:
    """Split creative-agent fetch failures by whether the REQUEST referenced them.

    Two different buyer-facing conditions share one internal cause, and only the
    boundary can tell them apart -- the registry never sees ``req``:

    * The buyer asked for a format from that agent (``req.format_ids`` carries a
      matching ``agent_url``). Then the reference did not resolve, and
      list_creative_formats.mdx:654 is explicit -- "REFERENCE_NOT_FOUND |
      Requested format_id doesn't exist, or referenced creative agent is
      unavailable / not accessible. error.field MUST identify which typed
      parameter failed to resolve." Hence ``field="format_ids"``: the MUST is on
      naming the parameter, and "format_ids" is the typed parameter that failed
      to resolve.
    * Nobody asked for it; the agent merely failed during seller-side
      aggregation. That is the unreferenced branch, and it keeps the
      AGENT_UNREACHABLE advisory with ``field="formats"`` naming the response
      section it degrades.

    RECOVERY DIFFERS, and that is the buyer-visible point of the split:
    AGENT_UNREACHABLE is transient (retry may help), REFERENCE_NOT_FOUND is
    correctable (retrying the same format_ids never will -- fix the reference).
    Both derive from the code via CODE_TABLE, so neither is authored here.

    Correlation comes from ``failed_agent_urls``, which is parallel to
    ``errors`` and never reaches the wire; the advisory itself deliberately
    omits the agent_url because every wire field is client-facing.
    """
    requested_agent_urls = {str(fid.agent_url) for fid in (req.format_ids or [])}
    if not requested_agent_urls or not fetch_result.failed_agent_urls:
        return fetch_result.errors

    routed: list[AdCPResponseError] = []
    for index, advisory in enumerate(fetch_result.errors):
        failed_url = fetch_result.failed_agent_urls[index] if index < len(fetch_result.failed_agent_urls) else None
        if failed_url is not None and str(failed_url) in requested_agent_urls:
            routed.append(AdCPResponseError.of(ErrorCode.REFERENCE_NOT_FOUND, field="format_ids"))
        else:
            routed.append(advisory)
    return routed
