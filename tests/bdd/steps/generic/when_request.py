"""When steps — dispatch requests through the CreativeFormatsEnv harness.

Every step calls production code directly. No stub mode.

Steps store results in ctx:
    ctx["response"] — ListCreativeFormatsResponse on success
    ctx["error"] — Exception on failure
"""

from __future__ import annotations

import json
from typing import Any

from pytest_bdd import parsers, when

from src.core.schemas import FormatId, ListCreativeFormatsRequest
from tests.harness.transport import Transport

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"


# ── Helpers ──────────────────────────────────────────────────────────


def _call(ctx: dict, req: ListCreativeFormatsRequest | None = None) -> None:
    """Dispatch through ctx['transport'] (defaults to IMPL for backward compat)."""
    transport = ctx.get("transport")
    if transport is not None:
        _call_via(ctx, transport, req=req)
    else:
        env = ctx["env"]
        try:
            ctx["response"] = env.call_impl(req=req)
        except Exception as exc:
            ctx["error"] = exc


def _call_via(ctx: dict, transport: str | Transport, req: ListCreativeFormatsRequest | None = None) -> None:
    """Call env.call_via for transport-specific dispatch."""
    if isinstance(transport, Transport):
        t = transport
    else:
        transport_map = {"a2a": Transport.A2A, "mcp": Transport.MCP, "rest": Transport.REST}
        t = transport_map.get(transport, Transport.IMPL)
    env = ctx["env"]

    kwargs: dict[str, Any] = {}
    if req is not None:
        if t == Transport.MCP:
            kwargs.update(req.model_dump(exclude_none=True))
        else:
            kwargs["req"] = req

    try:
        result = env.call_via(t, **kwargs)
        if result.is_error:
            ctx["error"] = result.error
        else:
            ctx["response"] = result.payload
    except Exception as exc:
        ctx["error"] = exc


def _build_req(**kwargs: Any) -> ListCreativeFormatsRequest | None:
    """Build a ListCreativeFormatsRequest, returning None if no filters."""
    if not kwargs:
        return None
    return ListCreativeFormatsRequest(**kwargs)


# ── A2A transport ────────────────────────────────────────────────────


@when("the Buyer Agent sends a list_creative_formats task via A2A with no filters")
def when_send_a2a_no_filters(ctx: dict) -> None:
    _call_via(ctx, "a2a")


@when("the Buyer Agent sends a list_creative_formats task via A2A")
def when_send_a2a(ctx: dict) -> None:
    _call_via(ctx, "a2a")


@when(parsers.parse('the Buyer Agent sends a list_creative_formats task via A2A with type filter "{type_filter}"'))
def when_send_a2a_type_filter(ctx: dict, type_filter: str) -> None:
    # type filter removed in adcp 3.12 — delegate to unfiltered
    when_send_a2a_no_filters(ctx)


@when(parsers.parse('the Buyer Agent sends a list_creative_formats task via A2A with type "{type_value}"'))
def when_send_a2a_type_value(ctx: dict, type_value: str) -> None:
    # type filter removed in adcp 3.12 — delegate to unfiltered
    when_send_a2a_no_filters(ctx)


# ── MCP transport ────────────────────────────────────────────────────


@when("the Buyer Agent calls list_creative_formats MCP tool with no filters")
def when_call_mcp_no_filters(ctx: dict) -> None:
    _call_via(ctx, "mcp")


@when("the Buyer Agent calls list_creative_formats MCP tool")
def when_call_mcp(ctx: dict) -> None:
    _call_via(ctx, "mcp")


@when(parsers.parse('the Buyer Agent calls list_creative_formats MCP tool with type "{type_value}"'))
def when_call_mcp_type(ctx: dict, type_value: str) -> None:
    # type filter removed in adcp 3.12 — delegate to unfiltered
    when_call_mcp_no_filters(ctx)


# ── Generic format request (transport-agnostic) ──────────────────────


@when(
    parsers.re(
        r"the Buyer Agent (?:requests the format catalog"
        r"|requests all formats with no filters"
        r"|sends a list_creative_formats request)"
    )
)
def when_request_unfiltered(ctx: dict) -> None:
    """Any phrasing of 'make an unfiltered format request'."""
    _call(ctx)


@when("the Buyer Agent sends a list_creative_formats request with invalid dimension filters")
def when_send_request_invalid_dimensions(ctx: dict) -> None:
    try:
        req = ListCreativeFormatsRequest(min_width=-1)
        _call(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


# ── Filter: type + asset_types combined ──────────────────────────────


@when(parsers.parse('the Buyer Agent requests formats with type "{fmt_type}" and asset_types {asset_types}'))
def when_request_type_and_asset(ctx: dict, fmt_type: str, asset_types: str) -> None:
    # type filter was removed from ListCreativeFormatsRequest in adcp 3.12;
    # only asset_types filter is applied
    parsed_assets = json.loads(asset_types)
    try:
        req = ListCreativeFormatsRequest(asset_types=parsed_assets)
        _call(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


# ── Filter: asset_types + name_search combined ──────────────────────


@when(parsers.parse('the Buyer Agent requests formats with asset_types {asset_types} and name_search "{name_search}"'))
def when_request_asset_types_and_name_search(ctx: dict, asset_types: str, name_search: str) -> None:
    parsed_assets = json.loads(asset_types)
    try:
        req = ListCreativeFormatsRequest(asset_types=parsed_assets, name_search=name_search)
        _call(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


# ── Filter: type only ────────────────────────────────────────────────


@when(parsers.parse('the Buyer Agent requests formats with type filter "{fmt_type}"'))
def when_request_type_filter(ctx: dict, fmt_type: str) -> None:
    # type filter was removed from ListCreativeFormatsRequest in adcp 3.12
    _call(ctx)


# ── Filter: format_ids ───────────────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with format_ids filter {filter_value}"))
def when_request_format_ids(ctx: dict, filter_value: str) -> None:
    parsed = json.loads(filter_value)
    try:
        format_ids = [FormatId(agent_url=DEFAULT_AGENT_URL, id=fid) for fid in parsed]
        req = ListCreativeFormatsRequest(format_ids=format_ids)
        _call(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


# ── Filter: asset_types ─────────────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with asset_types filter {filter_value}"))
def when_request_asset_types(ctx: dict, filter_value: str) -> None:
    parsed = json.loads(filter_value)
    try:
        req = ListCreativeFormatsRequest(asset_types=parsed)
        _call(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


# ── Filter: min_width / max_width ────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with min_width {min_w:d}"))
def when_request_min_width(ctx: dict, min_w: int) -> None:
    _call(ctx, req=ListCreativeFormatsRequest(min_width=min_w))


@when(parsers.parse("the Buyer Agent requests formats with min_width {min_w:d} and max_width {max_w:d}"))
def when_request_min_max_width(ctx: dict, min_w: int, max_w: int) -> None:
    _call(ctx, req=ListCreativeFormatsRequest(min_width=min_w, max_width=max_w))


# ── Filter: is_responsive ───────────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with is_responsive {value}"))
def when_request_responsive(ctx: dict, value: str) -> None:
    _call(ctx, req=ListCreativeFormatsRequest(is_responsive=value.lower() == "true"))


# ── Filter: name_search ─────────────────────────────────────────────


@when(parsers.parse('the Buyer Agent requests formats with name_search "{search}"'))
def when_request_name_search(ctx: dict, search: str) -> None:
    _call(ctx, req=ListCreativeFormatsRequest(name_search=search))


# ── Filter: disclosure_positions ─────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with disclosure_positions filter {filter_value}"))
def when_request_disclosure_positions(ctx: dict, filter_value: str) -> None:
    parsed = json.loads(filter_value)
    try:
        req = ListCreativeFormatsRequest(disclosure_positions=parsed)
        _call(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


# ── Filter: output_format_ids ────────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with output_format_ids filter {filter_value}"))
def when_request_output_format_ids(ctx: dict, filter_value: str) -> None:
    parsed = json.loads(filter_value)
    try:
        fmt_ids = [FormatId(agent_url=fid["agent_url"], id=fid["id"]) for fid in parsed] if parsed else []
        req = ListCreativeFormatsRequest(output_format_ids=fmt_ids if fmt_ids else [])
        _call(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


# ── Filter: input_format_ids ────────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with input_format_ids filter {filter_value}"))
def when_request_input_format_ids(ctx: dict, filter_value: str) -> None:
    parsed = json.loads(filter_value)
    try:
        fmt_ids = [FormatId(agent_url=fid["agent_url"], id=fid["id"]) for fid in parsed] if parsed else []
        req = ListCreativeFormatsRequest(input_format_ids=fmt_ids if fmt_ids else [])
        _call(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


# ── Partition dispatch steps ──────────────────────────────────────────
# Each partition When step maps the semantic label to an actual filter
# and calls production code through the harness.


def _partition_type(ctx: dict, partition: str) -> None:
    """Map type partition label to filter and call harness.

    type filter was removed from ListCreativeFormatsRequest in adcp 3.12.
    All partitions now dispatch an unfiltered request.
    """
    _call(ctx)


def _partition_format_ids(ctx: dict, partition: str) -> None:
    """Map format_ids partition label to filter and call harness."""
    known_ids = ctx.get("known_format_ids", [])
    if partition == "omitted":
        _call(ctx)
    elif partition == "all_ids_match":
        req = ListCreativeFormatsRequest(format_ids=known_ids)
        _call(ctx, req=req)
    elif partition == "partial_match":
        req = ListCreativeFormatsRequest(format_ids=known_ids[:1])
        _call(ctx, req=req)
    elif partition == "no_match":
        no_match = [FormatId(agent_url=DEFAULT_AGENT_URL, id="nonexistent")]
        req = ListCreativeFormatsRequest(format_ids=no_match)
        _call(ctx, req=req)
    else:
        try:
            req = ListCreativeFormatsRequest(format_ids=[FormatId(agent_url=DEFAULT_AGENT_URL, id=partition)])
            _call(ctx, req=req)
        except Exception as exc:
            ctx["error"] = exc


def _partition_asset_types(ctx: dict, partition: str) -> None:
    """Map asset_types partition label to filter and call harness."""
    if partition == "omitted":
        _call(ctx)
    elif partition == "single_type_match":
        _call(ctx, req=ListCreativeFormatsRequest(asset_types=["image"]))
    elif partition == "multiple_types_or":
        _call(ctx, req=ListCreativeFormatsRequest(asset_types=["image", "video"]))
    elif partition == "no_matching_formats":
        _call(ctx, req=ListCreativeFormatsRequest(asset_types=["webhook"]))
    else:
        try:
            _call(ctx, req=ListCreativeFormatsRequest(asset_types=[partition]))
        except Exception as exc:
            ctx["error"] = exc


def _partition_dimension(ctx: dict, partition: str) -> None:
    """Map dimension partition label to filter and call harness."""
    if partition == "omitted":
        _call(ctx)
    elif partition == "width_only":
        _call(ctx, req=ListCreativeFormatsRequest(min_width=300))
    elif partition == "height_only":
        _call(ctx, req=ListCreativeFormatsRequest(min_height=50))
    elif partition == "width_and_height":
        _call(ctx, req=ListCreativeFormatsRequest(min_width=300, min_height=50))
    elif partition == "no_render_match":
        _call(ctx, req=ListCreativeFormatsRequest(min_width=9999))
    elif partition == "no_dimension_info":
        _call(ctx, req=ListCreativeFormatsRequest(min_width=1))
    else:
        try:
            _call(ctx, req=ListCreativeFormatsRequest(min_width=int(partition)))
        except Exception as exc:
            ctx["error"] = exc


def _partition_responsive(ctx: dict, partition: str) -> None:
    """Map responsive partition label to filter and call harness."""
    if partition == "omitted":
        _call(ctx)
    elif partition == "responsive_true":
        _call(ctx, req=ListCreativeFormatsRequest(is_responsive=True))
    elif partition == "responsive_false":
        _call(ctx, req=ListCreativeFormatsRequest(is_responsive=False))
    else:
        try:
            _call(ctx, req=ListCreativeFormatsRequest(is_responsive=partition.lower() == "true"))
        except Exception as exc:
            ctx["error"] = exc


def _partition_name_search(ctx: dict, partition: str) -> None:
    """Map name_search partition label to filter and call harness."""
    names = ctx.get("named_formats", ["Standard Banner", "Video Interstitial", "Native Card"])
    if partition == "omitted":
        _call(ctx)
    elif partition == "exact_name":
        _call(ctx, req=ListCreativeFormatsRequest(name_search=names[0]))
    elif partition == "partial_match":
        _call(ctx, req=ListCreativeFormatsRequest(name_search="Banner"))
    elif partition == "case_insensitive":
        _call(ctx, req=ListCreativeFormatsRequest(name_search="standard banner"))
    elif partition == "no_match":
        _call(ctx, req=ListCreativeFormatsRequest(name_search="ZZZZZ_NO_MATCH"))
    else:
        _call(ctx, req=ListCreativeFormatsRequest(name_search=partition))


def _partition_wcag(ctx: dict, partition: str) -> None:
    """Map wcag_level partition label to filter and call harness."""
    from adcp.types.generated_poc.enums.wcag_level import WcagLevel

    wcag_map = {"level_a": WcagLevel.A, "level_aa": WcagLevel.AA, "level_aaa": WcagLevel.AAA}
    if partition == "not_provided":
        _call(ctx)
    elif partition in wcag_map:
        _call(ctx, req=ListCreativeFormatsRequest(wcag_level=wcag_map[partition]))
    else:
        try:
            _call(ctx, req=ListCreativeFormatsRequest(wcag_level=partition))
        except Exception as exc:
            ctx["error"] = exc


def _partition_disclosure(ctx: dict, partition: str) -> None:
    """Map disclosure_positions partition label to filter and call harness."""
    if partition == "omitted":
        _call(ctx)
    elif partition == "single_position":
        _call(ctx, req=ListCreativeFormatsRequest(disclosure_positions=["prominent"]))
    elif partition == "multiple_positions_all_match":
        _call(ctx, req=ListCreativeFormatsRequest(disclosure_positions=["prominent", "footer"]))
    elif partition == "all_positions":
        _call(
            ctx,
            req=ListCreativeFormatsRequest(
                disclosure_positions=["prominent", "footer", "overlay", "audio", "corner", "inline", "before", "after"]
            ),
        )
    elif partition == "no_matching_formats":
        _call(ctx, req=ListCreativeFormatsRequest(disclosure_positions=["corner"]))
    elif partition == "empty_array":
        try:
            _call(ctx, req=ListCreativeFormatsRequest(disclosure_positions=[]))
        except Exception as exc:
            ctx["error"] = exc
    elif partition == "duplicate_positions":
        try:
            _call(ctx, req=ListCreativeFormatsRequest(disclosure_positions=["prominent", "prominent"]))
        except Exception as exc:
            ctx["error"] = exc
    else:
        try:
            _call(ctx, req=ListCreativeFormatsRequest(disclosure_positions=[partition]))
        except Exception as exc:
            ctx["error"] = exc


def _partition_output_format_ids(ctx: dict, partition: str) -> None:
    """Map output_format_ids partition label to filter and call harness."""
    known = ctx.get("known_output_format_ids", [])
    if partition == "omitted":
        _call(ctx)
    elif partition == "single_format_id":
        _call(ctx, req=ListCreativeFormatsRequest(output_format_ids=known[:1]))
    elif partition == "multiple_ids_any_match":
        extra = FormatId(agent_url=DEFAULT_AGENT_URL, id="nonexistent")
        _call(ctx, req=ListCreativeFormatsRequest(output_format_ids=known[:1] + [extra]))
    elif partition == "no_matching_formats":
        no_match = [FormatId(agent_url=DEFAULT_AGENT_URL, id="nonexistent")]
        _call(ctx, req=ListCreativeFormatsRequest(output_format_ids=no_match))
    elif partition == "format_without_output_ids":
        _call(ctx, req=ListCreativeFormatsRequest(output_format_ids=known[:1]))
    elif partition == "empty_array":
        try:
            _call(ctx, req=ListCreativeFormatsRequest(output_format_ids=[]))
        except Exception as exc:
            ctx["error"] = exc
    elif partition == "invalid_format_id_missing_agent_url":
        try:
            _call(
                ctx,
                req=ListCreativeFormatsRequest(
                    output_format_ids=[FormatId(id="some-id")]  # type: ignore[call-arg]
                ),
            )
        except Exception as exc:
            ctx["error"] = exc
    elif partition == "invalid_format_id_missing_id":
        try:
            _call(
                ctx,
                req=ListCreativeFormatsRequest(
                    output_format_ids=[FormatId(agent_url=DEFAULT_AGENT_URL)]  # type: ignore[call-arg]
                ),
            )
        except Exception as exc:
            ctx["error"] = exc
    else:
        try:
            _call(
                ctx,
                req=ListCreativeFormatsRequest(output_format_ids=[FormatId(agent_url=DEFAULT_AGENT_URL, id=partition)]),
            )
        except Exception as exc:
            ctx["error"] = exc


def _partition_input_format_ids(ctx: dict, partition: str) -> None:
    """Map input_format_ids partition label to filter and call harness."""
    known = ctx.get("known_input_format_ids", [])
    if partition == "omitted":
        _call(ctx)
    elif partition == "single_format_id":
        _call(ctx, req=ListCreativeFormatsRequest(input_format_ids=known[:1]))
    elif partition == "multiple_ids_any_match":
        extra = FormatId(agent_url=DEFAULT_AGENT_URL, id="nonexistent")
        _call(ctx, req=ListCreativeFormatsRequest(input_format_ids=known[:1] + [extra]))
    elif partition == "no_matching_formats":
        no_match = [FormatId(agent_url=DEFAULT_AGENT_URL, id="nonexistent")]
        _call(ctx, req=ListCreativeFormatsRequest(input_format_ids=no_match))
    elif partition == "format_without_input_ids":
        _call(ctx, req=ListCreativeFormatsRequest(input_format_ids=known[:1]))
    elif partition == "empty_array":
        try:
            _call(ctx, req=ListCreativeFormatsRequest(input_format_ids=[]))
        except Exception as exc:
            ctx["error"] = exc
    elif partition == "invalid_format_id_missing_agent_url":
        try:
            _call(
                ctx,
                req=ListCreativeFormatsRequest(
                    input_format_ids=[FormatId(id="some-id")]  # type: ignore[call-arg]
                ),
            )
        except Exception as exc:
            ctx["error"] = exc
    elif partition == "invalid_format_id_missing_id":
        try:
            _call(
                ctx,
                req=ListCreativeFormatsRequest(
                    input_format_ids=[FormatId(agent_url=DEFAULT_AGENT_URL)]  # type: ignore[call-arg]
                ),
            )
        except Exception as exc:
            ctx["error"] = exc
    else:
        try:
            _call(
                ctx,
                req=ListCreativeFormatsRequest(input_format_ids=[FormatId(agent_url=DEFAULT_AGENT_URL, id=partition)]),
            )
        except Exception as exc:
            ctx["error"] = exc


@when(parsers.parse('the Buyer Agent requests creative formats with type filter "{partition}"'))
def when_partition_type_filter(ctx: dict, partition: str) -> None:
    _partition_type(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with format_ids "{partition}"'))
def when_partition_format_ids(ctx: dict, partition: str) -> None:
    _partition_format_ids(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with asset_types "{partition}"'))
def when_partition_asset_types(ctx: dict, partition: str) -> None:
    _partition_asset_types(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with dimension filter "{partition}"'))
def when_partition_dimension(ctx: dict, partition: str) -> None:
    _partition_dimension(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with is_responsive "{partition}"'))
def when_partition_responsive(ctx: dict, partition: str) -> None:
    _partition_responsive(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with name_search "{partition}"'))
def when_partition_name_search(ctx: dict, partition: str) -> None:
    _partition_name_search(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with wcag_level "{partition}"'))
def when_partition_wcag(ctx: dict, partition: str) -> None:
    _partition_wcag(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with disclosure_positions "{partition}"'))
def when_partition_disclosure(ctx: dict, partition: str) -> None:
    _partition_disclosure(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with output_format_ids "{partition}"'))
def when_partition_output_ids(ctx: dict, partition: str) -> None:
    _partition_output_format_ids(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with input_format_ids "{partition}"'))
def when_partition_input_ids(ctx: dict, partition: str) -> None:
    _partition_input_format_ids(ctx, partition)


# ── Boundary dispatch steps ──────────────────────────────────────────
# Boundary steps reuse the same partition mapping — the boundary_point
# label is just a more descriptive partition label.


@when(parsers.parse('the Buyer Agent requests creative formats at type boundary "{boundary_point}"'))
def when_boundary_type(ctx: dict, boundary_point: str) -> None:
    # Map human-readable boundary labels to partition labels
    mapping = {
        "display (valid enum)": "display",
        "video (valid enum)": "video",
        "omitted (no filter)": "omitted",
        "invalid type (rejected)": "invalid_type",
    }
    _partition_type(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at format_ids boundary "{boundary_point}"'))
def when_boundary_format_ids(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "all IDs match": "all_ids_match",
        "partial match (some excluded)": "partial_match",
        "no IDs match (empty result)": "no_match",
        "omitted (no filter)": "omitted",
    }
    _partition_format_ids(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at asset_types boundary "{boundary_point}"'))
def when_boundary_asset_types(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "single asset type match": "single_type_match",
        "multiple types OR semantics": "multiple_types_or",
        "omitted (no filter)": "omitted",
        "brief (new asset type for generative formats)": "brief",
        "catalog (new asset type for catalog-based formats)": "catalog",
        "no formats match (empty result)": "no_matching_formats",
        "Unknown string not in enum": "unknown_asset_type",
        "promoted_offerings (removed from enum)": "removed_promoted_offerings",
    }
    _partition_asset_types(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at dimension boundary "{boundary_point}"'))
def when_boundary_dimension(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "width filter only": "width_only",
        "height filter only": "height_only",
        "width and height combined": "width_and_height",
        "omitted (no dimension filter)": "omitted",
        "no render matches constraints": "no_render_match",
    }
    _partition_dimension(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at responsive boundary "{boundary_point}"'))
def when_boundary_responsive(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "is_responsive = true": "responsive_true",
        "is_responsive = false": "responsive_false",
        "is_responsive omitted": "omitted",
    }
    _partition_responsive(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at name_search boundary "{boundary_point}"'))
def when_boundary_name_search(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "exact name match": "exact_name",
        "partial substring match": "partial_match",
        "case-insensitive match": "case_insensitive",
        "omitted (no filter)": "omitted",
        "no match (empty result)": "no_match",
    }
    _partition_name_search(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at wcag_level boundary "{boundary_point}"'))
def when_boundary_wcag(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "A (first enum value — minimum conformance)": "level_a",
        "AAA (last enum value — highest conformance)": "level_aaa",
        "Not provided (no filter)": "not_provided",
        "Unknown string not in enum": "unknown_value",
    }
    _partition_wcag(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at disclosure boundary "{boundary_point}"'))
def when_boundary_disclosure(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "single position ['prominent'] (min array size)": "single_position",
        "all 8 positions (max meaningful array)": "all_positions",
        "omitted (no filter)": "omitted",
        "format has no supported_disclosure_positions (excluded)": "no_matching_formats",
        "empty array []": "empty_array",
        "unknown position string 'sidebar'": "sidebar",
        "duplicate positions ['prominent','prominent']": "duplicate_positions",
    }
    _partition_disclosure(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at output_format_ids boundary "{boundary_point}"'))
def when_boundary_output_ids(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "single FormatId (min array size)": "single_format_id",
        "multiple FormatIds, one matches (ANY semantics)": "multiple_ids_any_match",
        "omitted (no filter)": "omitted",
        "format has no output_format_ids (excluded)": "format_without_output_ids",
        "no formats match requested output IDs": "no_matching_formats",
        "empty array []": "empty_array",
        "FormatId missing agent_url": "invalid_format_id_missing_agent_url",
        "FormatId missing id": "invalid_format_id_missing_id",
    }
    _partition_output_format_ids(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at input_format_ids boundary "{boundary_point}"'))
def when_boundary_input_ids(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "single FormatId (min array size)": "single_format_id",
        "multiple FormatIds, one matches (ANY semantics)": "multiple_ids_any_match",
        "omitted (no filter)": "omitted",
        "format has no input_format_ids (excluded)": "format_without_input_ids",
        "no formats match requested input IDs": "no_matching_formats",
        "empty array []": "empty_array",
        "FormatId missing agent_url": "invalid_format_id_missing_agent_url",
        "FormatId missing id": "invalid_format_id_missing_id",
    }
    _partition_input_format_ids(ctx, mapping.get(boundary_point, boundary_point))


# ── Creative agent format queries (partition / boundary) ─────────────
# These test a separate API (creative agent format querying), not
# list_creative_formats. Marked xfail in conftest.py.


@when(parsers.parse('the Buyer Agent queries creative agent formats with type "{partition}"'))
def when_query_agent_type(ctx: dict, partition: str) -> None:
    ctx["partition"] = partition
    ctx["filter_under_test"] = "creative_agent_format_type"


@when(parsers.parse('the Buyer Agent queries creative agent formats with asset_types "{partition}"'))
def when_query_agent_asset_types(ctx: dict, partition: str) -> None:
    ctx["partition"] = partition
    ctx["filter_under_test"] = "creative_agent_asset_type"


@when(parsers.parse('the Buyer Agent queries creative agent formats at type boundary "{boundary_point}"'))
def when_boundary_agent_type(ctx: dict, boundary_point: str) -> None:
    ctx["boundary_point"] = boundary_point
    ctx["filter_under_test"] = "creative_agent_format_type"


@when(parsers.parse('the Buyer Agent queries creative agent formats at asset_types boundary "{boundary_point}"'))
def when_boundary_agent_asset_types(ctx: dict, boundary_point: str) -> None:
    ctx["boundary_point"] = boundary_point
    ctx["filter_under_test"] = "creative_agent_asset_type"
