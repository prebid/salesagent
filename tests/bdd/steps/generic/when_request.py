"""When steps for dispatching requests (A2A, MCP, generic filter requests).

These steps invoke operations and store results (or errors) in ``ctx``.

Phase 0: stubs that record the request intent in ctx without calling
production code. Epic 1 will wire these to the test harness.
"""

from __future__ import annotations

import json

from pytest_bdd import parsers, when

# ── A2A transport ────────────────────────────────────────────────────


@when("the Buyer Agent sends a list_creative_formats task via A2A with no filters")
def when_send_a2a_no_filters(ctx: dict) -> None:
    """Send list_creative_formats via A2A with no filters."""
    ctx["transport"] = "a2a"
    ctx["request_filters"] = {}
    formats = list(ctx.get("registry_formats", []))
    formats.sort(key=lambda f: (f.get("type", ""), f.get("name", "")))
    ctx["result"] = {"formats": formats, "status": "completed"}


@when("the Buyer Agent sends a list_creative_formats task via A2A")
def when_send_a2a(ctx: dict) -> None:
    """Send list_creative_formats via A2A (may have no tenant)."""
    ctx["transport"] = "a2a"
    ctx["request_filters"] = {}
    if not ctx.get("has_tenant", True) or not ctx.get("has_auth", True):
        ctx["error"] = {
            "code": "TENANT_REQUIRED",
            "message": "Tenant context could not be determined",
            "suggestion": "Provide authentication credentials or tenant identification",
        }
    else:
        ctx["result"] = {"formats": ctx.get("registry_formats", []), "status": "completed"}


@when(parsers.parse('the Buyer Agent sends a list_creative_formats task via A2A with type filter "{type_filter}"'))
def when_send_a2a_type_filter(ctx: dict, type_filter: str) -> None:
    """Send list_creative_formats via A2A with a type filter."""
    ctx["transport"] = "a2a"
    ctx["request_filters"] = {"type": type_filter}
    formats = [f for f in ctx.get("registry_formats", []) if f.get("type") == type_filter]
    ctx["result"] = {"formats": formats, "status": "completed"}


@when(parsers.parse('the Buyer Agent sends a list_creative_formats task via A2A with type "{type_value}"'))
def when_send_a2a_type_value(ctx: dict, type_value: str) -> None:
    """Send list_creative_formats via A2A with a type parameter (may be invalid)."""
    ctx["transport"] = "a2a"
    ctx["request_filters"] = {"type": type_value}
    valid_types = {"display", "video", "audio", "native", "dooh"}
    if type_value not in valid_types:
        ctx["error"] = {
            "code": "VALIDATION_ERROR",
            "message": f"Invalid parameter: type '{type_value}' is not valid",
            "suggestion": f"Use one of: {', '.join(sorted(valid_types))}",
        }
    else:
        formats = [f for f in ctx.get("registry_formats", []) if f.get("type") == type_value]
        ctx["result"] = {"formats": formats, "status": "completed"}


# ── MCP transport ────────────────────────────────────────────────────


@when("the Buyer Agent calls list_creative_formats MCP tool with no filters")
def when_call_mcp_no_filters(ctx: dict) -> None:
    """Call list_creative_formats MCP tool with no filters."""
    ctx["transport"] = "mcp"
    ctx["request_filters"] = {}
    formats = list(ctx.get("registry_formats", []))
    formats.sort(key=lambda f: (f.get("type", ""), f.get("name", "")))
    ctx["result"] = {"formats": formats, "status": "completed"}


@when("the Buyer Agent calls list_creative_formats MCP tool")
def when_call_mcp(ctx: dict) -> None:
    """Call list_creative_formats MCP tool (may have no tenant)."""
    ctx["transport"] = "mcp"
    ctx["request_filters"] = {}
    if not ctx.get("has_tenant", True):
        ctx["error"] = {
            "code": "TENANT_REQUIRED",
            "message": "Tenant context could not be determined",
            "suggestion": "Provide authentication credentials or tenant identification",
        }
    else:
        ctx["result"] = {"formats": ctx.get("registry_formats", []), "status": "completed"}


@when(parsers.parse('the Buyer Agent calls list_creative_formats MCP tool with type "{type_value}"'))
def when_call_mcp_type(ctx: dict, type_value: str) -> None:
    """Call list_creative_formats MCP tool with a type parameter."""
    ctx["transport"] = "mcp"
    ctx["request_filters"] = {"type": type_value}
    valid_types = {"display", "video", "audio", "native", "dooh"}
    if type_value not in valid_types:
        ctx["error"] = {
            "code": "VALIDATION_ERROR",
            "message": f"Invalid parameter: type '{type_value}' is not valid",
            "suggestion": f"Use one of: {', '.join(sorted(valid_types))}",
        }
    else:
        formats = [f for f in ctx.get("registry_formats", []) if f.get("type") == type_value]
        ctx["result"] = {"formats": formats, "status": "completed"}


# ── Generic format request (transport-agnostic) ──────────────────────


@when("the Buyer Agent requests the format catalog")
def when_request_catalog(ctx: dict) -> None:
    """Request the full format catalog (no filters, transport-agnostic)."""
    ctx["request_filters"] = {}
    formats = list(ctx.get("registry_formats", []))
    formats.sort(key=lambda f: (f.get("type", ""), f.get("name", "")))
    ctx["result"] = {"formats": formats, "status": "completed"}


@when("the Buyer Agent requests all formats with no filters")
def when_request_all_no_filters(ctx: dict) -> None:
    """Request all formats with no filters."""
    ctx["request_filters"] = {}
    formats = list(ctx.get("registry_formats", []))
    formats.sort(key=lambda f: (f.get("type", ""), f.get("name", "")))
    ctx["result"] = {"formats": formats, "status": "completed"}


@when("the Buyer Agent sends a list_creative_formats request")
def when_send_request_generic(ctx: dict) -> None:
    """Send a list_creative_formats request (sandbox scenarios)."""
    ctx["request_filters"] = {}
    result: dict = {
        "formats": ctx.get("registry_formats", []),
        "status": "completed",
    }
    # Only include sandbox flag when explicitly True (production omits it)
    if ctx.get("sandbox") is True:
        result["sandbox"] = True
    ctx["result"] = result


@when("the Buyer Agent sends a list_creative_formats request with invalid dimension filters")
def when_send_request_invalid_dimensions(ctx: dict) -> None:
    """Send a list_creative_formats request with invalid dimension filters."""
    ctx["request_filters"] = {"min_width": -1}
    ctx["error"] = {
        "code": "VALIDATION_ERROR",
        "message": "Invalid dimension filter parameters",
        "suggestion": "Provide positive integer values for dimension filters",
    }


# ── Filter: type + asset_types combined ──────────────────────────────


@when(parsers.parse('the Buyer Agent requests formats with type "{fmt_type}" and asset_types {asset_types}'))
def when_request_type_and_asset(ctx: dict, fmt_type: str, asset_types: str) -> None:
    """Request formats with combined type and asset_types filters."""
    parsed_assets = json.loads(asset_types)
    ctx["request_filters"] = {"type": fmt_type, "asset_types": parsed_assets}
    # Stub: filter registry_formats by both type AND asset type
    result = []
    for f in ctx.get("registry_formats", []):
        if f.get("type") != fmt_type:
            continue
        f_asset_types = {a["type"] for a in f.get("assets", [])}
        if any(at in f_asset_types for at in parsed_assets):
            result.append(f)
    ctx["result"] = {"formats": result, "status": "completed"}


# ── Filter: type only ────────────────────────────────────────────────


@when(parsers.parse('the Buyer Agent requests formats with type filter "{fmt_type}"'))
def when_request_type_filter(ctx: dict, fmt_type: str) -> None:
    """Request formats with type filter."""
    ctx["request_filters"] = {"type": fmt_type}
    formats = [f for f in ctx.get("registry_formats", []) if f.get("type") == fmt_type]
    ctx["result"] = {"formats": formats, "status": "completed"}


# ── Filter: format_ids ───────────────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with format_ids filter {filter_value}"))
def when_request_format_ids(ctx: dict, filter_value: str) -> None:
    """Request formats with format_ids filter."""
    parsed = json.loads(filter_value)
    ctx["request_filters"] = {"format_ids": parsed}
    result = []
    for f in ctx.get("registry_formats", []):
        fid = f.get("format_id", {})
        if isinstance(fid, dict) and fid.get("id") in parsed:
            result.append(f)
    ctx["result"] = {"formats": result, "status": "completed"}


# ── Filter: asset_types ─────────────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with asset_types filter {filter_value}"))
def when_request_asset_types(ctx: dict, filter_value: str) -> None:
    """Request formats with asset_types filter (OR semantics)."""
    parsed = json.loads(filter_value)
    ctx["request_filters"] = {"asset_types": parsed}
    result = []
    for f in ctx.get("registry_formats", []):
        f_assets = {a["type"] for a in f.get("assets", [])}
        group_assets = set()
        for g in f.get("asset_groups", []):
            group_assets.update(g.get("types", []))
        all_assets = f_assets | group_assets
        if any(at in all_assets for at in parsed):
            result.append(f)
    ctx["result"] = {"formats": result, "status": "completed"}


# ── Filter: min_width / max_width ────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with min_width {min_w:d}"))
def when_request_min_width(ctx: dict, min_w: int) -> None:
    """Request formats with min_width filter."""
    ctx["request_filters"] = {"min_width": min_w}
    result = []
    for f in ctx.get("registry_formats", []):
        renders = f.get("renders", [])
        if any(r.get("width", 0) >= min_w for r in renders):
            result.append(f)
    ctx["result"] = {"formats": result, "status": "completed"}


@when(parsers.parse("the Buyer Agent requests formats with min_width {min_w:d} and max_width {max_w:d}"))
def when_request_min_max_width(ctx: dict, min_w: int, max_w: int) -> None:
    """Request formats with min_width and max_width filters."""
    ctx["request_filters"] = {"min_width": min_w, "max_width": max_w}
    result = []
    for f in ctx.get("registry_formats", []):
        renders = f.get("renders", [])
        if any(min_w <= r.get("width", 0) <= max_w for r in renders):
            result.append(f)
    ctx["result"] = {"formats": result, "status": "completed"}


# ── Filter: is_responsive ───────────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with is_responsive {value}"))
def when_request_responsive(ctx: dict, value: str) -> None:
    """Request formats with is_responsive filter."""
    is_resp = value.lower() == "true"
    ctx["request_filters"] = {"is_responsive": is_resp}
    result = [f for f in ctx.get("registry_formats", []) if f.get("responsive") == is_resp]
    ctx["result"] = {"formats": result, "status": "completed"}


# ── Filter: name_search ─────────────────────────────────────────────


@when(parsers.parse('the Buyer Agent requests formats with name_search "{search}"'))
def when_request_name_search(ctx: dict, search: str) -> None:
    """Request formats with name_search filter (case-insensitive substring)."""
    ctx["request_filters"] = {"name_search": search}
    result = [f for f in ctx.get("registry_formats", []) if search.lower() in f.get("name", "").lower()]
    ctx["result"] = {"formats": result, "status": "completed"}


# ── Filter: disclosure_positions ─────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with disclosure_positions filter {filter_value}"))
def when_request_disclosure_positions(ctx: dict, filter_value: str) -> None:
    """Request formats with disclosure_positions filter (AND semantics)."""
    parsed = json.loads(filter_value)
    ctx["request_filters"] = {"disclosure_positions": parsed}

    # Validate
    valid_positions = {"prominent", "footer", "overlay", "audio", "corner", "inline", "before", "after"}
    if not parsed:
        ctx["error"] = {
            "code": "DISCLOSURE_POSITIONS_EMPTY",
            "message": "At least 1 item is required",
            "suggestion": "Provide at least one position or omit the filter",
        }
        return
    if len(parsed) != len(set(parsed)):
        ctx["error"] = {
            "code": "DISCLOSURE_POSITIONS_DUPLICATES",
            "message": "Duplicate values are not allowed",
            "suggestion": "Remove duplicate positions",
        }
        return
    for p in parsed:
        if p not in valid_positions:
            ctx["error"] = {
                "code": "DISCLOSURE_POSITIONS_INVALID_VALUE",
                "message": f"'{p}' is not a valid disclosure position",
                "suggestion": "Use valid DisclosurePosition enum values",
            }
            return

    result = []
    for f in ctx.get("registry_formats", []):
        supported = f.get("supported_disclosure_positions")
        if supported is None:
            continue
        if all(p in supported for p in parsed):
            result.append(f)
    ctx["result"] = {"formats": result, "status": "completed"}


# ── Filter: output_format_ids ────────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with output_format_ids filter {filter_value}"))
def when_request_output_format_ids(ctx: dict, filter_value: str) -> None:
    """Request formats with output_format_ids filter (OR semantics)."""
    parsed = json.loads(filter_value)
    ctx["request_filters"] = {"output_format_ids": parsed}

    # Validate
    if not parsed:
        ctx["error"] = {
            "code": "OUTPUT_FORMAT_IDS_EMPTY",
            "message": "At least 1 item is required",
            "suggestion": "Provide at least one FormatId or omit the filter",
        }
        return
    for fid in parsed:
        if not isinstance(fid, dict) or "agent_url" not in fid or "id" not in fid:
            ctx["error"] = {
                "code": "OUTPUT_FORMAT_IDS_INVALID_STRUCTURE",
                "message": "FormatId must include agent_url and id",
                "suggestion": "Include agent_url (URI) and id fields",
            }
            return

    result = []
    for f in ctx.get("registry_formats", []):
        output_ids = f.get("output_format_ids")
        if output_ids is None:
            continue
        for requested in parsed:
            if any(o.get("agent_url") == requested["agent_url"] and o.get("id") == requested["id"] for o in output_ids):
                result.append(f)
                break
    ctx["result"] = {"formats": result, "status": "completed"}


# ── Filter: input_format_ids ────────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with input_format_ids filter {filter_value}"))
def when_request_input_format_ids(ctx: dict, filter_value: str) -> None:
    """Request formats with input_format_ids filter (OR semantics)."""
    parsed = json.loads(filter_value)
    ctx["request_filters"] = {"input_format_ids": parsed}

    # Validate
    if not parsed:
        ctx["error"] = {
            "code": "INPUT_FORMAT_IDS_EMPTY",
            "message": "At least 1 item is required",
            "suggestion": "Provide at least one FormatId or omit the filter",
        }
        return
    for fid in parsed:
        if not isinstance(fid, dict) or "agent_url" not in fid or "id" not in fid:
            ctx["error"] = {
                "code": "INPUT_FORMAT_IDS_INVALID_STRUCTURE",
                "message": "FormatId must include agent_url and id",
                "suggestion": "Include agent_url (URI) and id fields",
            }
            return

    result = []
    for f in ctx.get("registry_formats", []):
        input_ids = f.get("input_format_ids")
        if input_ids is None:
            continue
        for requested in parsed:
            if any(i.get("agent_url") == requested["agent_url"] and i.get("id") == requested["id"] for i in input_ids):
                result.append(f)
                break
    ctx["result"] = {"formats": result, "status": "completed"}


# ── Partition / boundary dispatch steps ──────────────────────────────
# These steps dispatch parameterized partition and boundary scenarios.
# They store the partition/boundary_point in ctx for the Then step.


@when(parsers.parse('the Buyer Agent requests creative formats with type filter "{partition}"'))
def when_partition_type_filter(ctx: dict, partition: str) -> None:
    """Partition test for type filter."""
    ctx["partition"] = partition
    ctx["filter_under_test"] = "type"
    ctx["result"] = {"status": "completed", "partition_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats with format_ids "{partition}"'))
def when_partition_format_ids(ctx: dict, partition: str) -> None:
    """Partition test for format_ids filter."""
    ctx["partition"] = partition
    ctx["filter_under_test"] = "format_ids"
    ctx["result"] = {"status": "completed", "partition_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats with asset_types "{partition}"'))
def when_partition_asset_types(ctx: dict, partition: str) -> None:
    """Partition test for asset_types filter."""
    ctx["partition"] = partition
    ctx["filter_under_test"] = "asset_types"
    ctx["result"] = {"status": "completed", "partition_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats with dimension filter "{partition}"'))
def when_partition_dimension(ctx: dict, partition: str) -> None:
    """Partition test for dimension filter."""
    ctx["partition"] = partition
    ctx["filter_under_test"] = "dimension"
    ctx["result"] = {"status": "completed", "partition_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats with is_responsive "{partition}"'))
def when_partition_responsive(ctx: dict, partition: str) -> None:
    """Partition test for is_responsive filter."""
    ctx["partition"] = partition
    ctx["filter_under_test"] = "is_responsive"
    ctx["result"] = {"status": "completed", "partition_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats with name_search "{partition}"'))
def when_partition_name_search(ctx: dict, partition: str) -> None:
    """Partition test for name_search filter."""
    ctx["partition"] = partition
    ctx["filter_under_test"] = "name_search"
    ctx["result"] = {"status": "completed", "partition_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats with wcag_level "{partition}"'))
def when_partition_wcag(ctx: dict, partition: str) -> None:
    """Partition test for wcag_level filter."""
    ctx["partition"] = partition
    ctx["filter_under_test"] = "wcag_level"
    ctx["result"] = {"status": "completed", "partition_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats with disclosure_positions "{partition}"'))
def when_partition_disclosure(ctx: dict, partition: str) -> None:
    """Partition test for disclosure_positions filter."""
    ctx["partition"] = partition
    ctx["filter_under_test"] = "disclosure_positions"
    ctx["result"] = {"status": "completed", "partition_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats with output_format_ids "{partition}"'))
def when_partition_output_ids(ctx: dict, partition: str) -> None:
    """Partition test for output_format_ids filter."""
    ctx["partition"] = partition
    ctx["filter_under_test"] = "output_format_ids"
    ctx["result"] = {"status": "completed", "partition_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats with input_format_ids "{partition}"'))
def when_partition_input_ids(ctx: dict, partition: str) -> None:
    """Partition test for input_format_ids filter."""
    ctx["partition"] = partition
    ctx["filter_under_test"] = "input_format_ids"
    ctx["result"] = {"status": "completed", "partition_applied": True}


# ── Boundary dispatch steps ──────────────────────────────────────────


@when(parsers.parse('the Buyer Agent requests creative formats at type boundary "{boundary_point}"'))
def when_boundary_type(ctx: dict, boundary_point: str) -> None:
    """Boundary test for type filter."""
    ctx["boundary_point"] = boundary_point
    ctx["filter_under_test"] = "type"
    ctx["result"] = {"status": "completed", "boundary_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats at format_ids boundary "{boundary_point}"'))
def when_boundary_format_ids(ctx: dict, boundary_point: str) -> None:
    """Boundary test for format_ids filter."""
    ctx["boundary_point"] = boundary_point
    ctx["filter_under_test"] = "format_ids"
    ctx["result"] = {"status": "completed", "boundary_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats at asset_types boundary "{boundary_point}"'))
def when_boundary_asset_types(ctx: dict, boundary_point: str) -> None:
    """Boundary test for asset_types filter."""
    ctx["boundary_point"] = boundary_point
    ctx["filter_under_test"] = "asset_types"
    ctx["result"] = {"status": "completed", "boundary_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats at dimension boundary "{boundary_point}"'))
def when_boundary_dimension(ctx: dict, boundary_point: str) -> None:
    """Boundary test for dimension filter."""
    ctx["boundary_point"] = boundary_point
    ctx["filter_under_test"] = "dimension"
    ctx["result"] = {"status": "completed", "boundary_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats at responsive boundary "{boundary_point}"'))
def when_boundary_responsive(ctx: dict, boundary_point: str) -> None:
    """Boundary test for responsive filter."""
    ctx["boundary_point"] = boundary_point
    ctx["filter_under_test"] = "is_responsive"
    ctx["result"] = {"status": "completed", "boundary_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats at name_search boundary "{boundary_point}"'))
def when_boundary_name_search(ctx: dict, boundary_point: str) -> None:
    """Boundary test for name_search filter."""
    ctx["boundary_point"] = boundary_point
    ctx["filter_under_test"] = "name_search"
    ctx["result"] = {"status": "completed", "boundary_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats at wcag_level boundary "{boundary_point}"'))
def when_boundary_wcag(ctx: dict, boundary_point: str) -> None:
    """Boundary test for wcag_level filter."""
    ctx["boundary_point"] = boundary_point
    ctx["filter_under_test"] = "wcag_level"
    ctx["result"] = {"status": "completed", "boundary_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats at disclosure boundary "{boundary_point}"'))
def when_boundary_disclosure(ctx: dict, boundary_point: str) -> None:
    """Boundary test for disclosure filter."""
    ctx["boundary_point"] = boundary_point
    ctx["filter_under_test"] = "disclosure_positions"
    ctx["result"] = {"status": "completed", "boundary_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats at output_format_ids boundary "{boundary_point}"'))
def when_boundary_output_ids(ctx: dict, boundary_point: str) -> None:
    """Boundary test for output_format_ids filter."""
    ctx["boundary_point"] = boundary_point
    ctx["filter_under_test"] = "output_format_ids"
    ctx["result"] = {"status": "completed", "boundary_applied": True}


@when(parsers.parse('the Buyer Agent requests creative formats at input_format_ids boundary "{boundary_point}"'))
def when_boundary_input_ids(ctx: dict, boundary_point: str) -> None:
    """Boundary test for input_format_ids filter."""
    ctx["boundary_point"] = boundary_point
    ctx["filter_under_test"] = "input_format_ids"
    ctx["result"] = {"status": "completed", "boundary_applied": True}


# ── Creative agent format queries (partition / boundary) ─────────────


@when(parsers.parse('the Buyer Agent queries creative agent formats with type "{partition}"'))
def when_query_agent_type(ctx: dict, partition: str) -> None:
    """Partition test for creative agent format type."""
    ctx["partition"] = partition
    ctx["filter_under_test"] = "creative_agent_format_type"
    ctx["result"] = {"status": "completed", "partition_applied": True}


@when(parsers.parse('the Buyer Agent queries creative agent formats with asset_types "{partition}"'))
def when_query_agent_asset_types(ctx: dict, partition: str) -> None:
    """Partition test for creative agent asset types."""
    ctx["partition"] = partition
    ctx["filter_under_test"] = "creative_agent_asset_type"
    ctx["result"] = {"status": "completed", "partition_applied": True}


@when(parsers.parse('the Buyer Agent queries creative agent formats at type boundary "{boundary_point}"'))
def when_boundary_agent_type(ctx: dict, boundary_point: str) -> None:
    """Boundary test for creative agent format type."""
    ctx["boundary_point"] = boundary_point
    ctx["filter_under_test"] = "creative_agent_format_type"
    ctx["result"] = {"status": "completed", "boundary_applied": True}


@when(parsers.parse('the Buyer Agent queries creative agent formats at asset_types boundary "{boundary_point}"'))
def when_boundary_agent_asset_types(ctx: dict, boundary_point: str) -> None:
    """Boundary test for creative agent asset types."""
    ctx["boundary_point"] = boundary_point
    ctx["filter_under_test"] = "creative_agent_asset_type"
    ctx["result"] = {"status": "completed", "boundary_applied": True}
