"""Then steps for payload and field assertions.

Every assertion operates on real production response objects:
    ctx["response"] — ListCreativeFormatsResponse on success
    ctx["error"] — Exception on failure

No stub mode. No dict intermediaries.
"""

from __future__ import annotations

from collections.abc import Sequence

from pytest_bdd import parsers, then

# ── Helpers ────────────────────────────────────────────────────────────


def _get_formats(ctx: dict) -> list:
    """Extract formats list from response, handling both object and dict shapes."""
    resp = ctx.get("response")
    if resp is None:
        return []
    # Real response object
    if hasattr(resp, "formats"):
        formats = resp.formats or []
        # Convert to dicts for uniform assertion access
        result = []
        for f in formats:
            d = {}
            d["name"] = f.name if hasattr(f, "name") else None
            d["type"] = f.type.value if hasattr(f.type, "value") else str(f.type) if f.type else None
            if hasattr(f, "format_id") and f.format_id is not None:
                d["format_id"] = {
                    "agent_url": str(f.format_id.agent_url) if hasattr(f.format_id, "agent_url") else None,
                    "id": f.format_id.id if hasattr(f.format_id, "id") else None,
                }
            if hasattr(f, "assets") and f.assets:
                d["assets"] = [{"type": getattr(a, "asset_type", getattr(a, "type", "unknown"))} for a in f.assets]
            result.append(d)
        return result
    # Dict fallback (shouldn't happen in new architecture)
    return resp.get("formats", []) if isinstance(resp, dict) else []


# ── Format catalog assertions ────────────────────────────────────────


@then("the response should include all registered formats")
def then_all_formats(ctx: dict) -> None:
    formats = _get_formats(ctx)
    registered = ctx.get("registry_formats", [])
    assert len(formats) == len(registered), f"Expected {len(registered)} formats, got {len(formats)}"


@then("the response should include an empty formats array")
def then_empty_formats(ctx: dict) -> None:
    formats = _get_formats(ctx)
    assert len(formats) == 0, f"Expected 0 formats, got {len(formats)}"


@then("the response should include only display formats")
def then_only_display(ctx: dict) -> None:
    for f in _get_formats(ctx):
        assert f["type"] == "display", f"Expected type 'display', got '{f['type']}'"


@then("no video formats should be present in the results")
def then_no_video(ctx: dict) -> None:
    for f in _get_formats(ctx):
        assert f.get("type") != "video", f"Unexpected video format: {f.get('name')}"


@then("the response should include creative_agents referrals")
def then_has_referrals(ctx: dict) -> None:
    """Assert response contains creative_agents with well-formed referral entries."""
    resp = ctx.get("response")
    referrals = getattr(resp, "creative_agents", None) or []
    assert len(referrals) > 0, "Expected creative agent referrals in response"
    # Verify referrals have expected structure (not just arbitrary non-empty list)
    for ref in referrals:
        assert getattr(ref, "agent_url", None), f"Referral missing agent_url: {ref}"


@then("each referral should include the agent URL and supported capabilities")
def then_referral_fields(ctx: dict) -> None:
    resp = ctx.get("response")
    referrals = getattr(resp, "creative_agents", None) or []
    for ref in referrals:
        assert getattr(ref, "agent_url", None), f"Missing agent_url in referral: {ref}"
        assert getattr(ref, "capabilities", None), f"Missing capabilities in referral: {ref}"


# ── Format field presence ────────────────────────────────────────────


@then("each format should include a format_id with agent_url and id")
def then_format_id_fields(ctx: dict) -> None:
    for f in _get_formats(ctx):
        fid = f.get("format_id")
        assert fid is not None, f"Format '{f.get('name')}' missing format_id"
        assert fid.get("agent_url"), f"Format '{f.get('name')}' format_id missing agent_url"
        assert fid.get("id"), f"Format '{f.get('name')}' format_id missing id"


@then("each format should include a name and type category")
def then_format_name_type(ctx: dict) -> None:
    for f in _get_formats(ctx):
        assert f.get("name"), f"Format missing name: {f}"
        assert f.get("type"), f"Format missing type: {f}"


@then("each format should include asset requirements with type and dimensions")
def then_format_assets(ctx: dict) -> None:
    """Assert each format's assets have both type AND dimensions fields."""
    formats = _get_formats(ctx)
    formats_with_assets = [f for f in formats if f.get("assets")]
    for f in formats_with_assets:
        for a in f["assets"]:
            assert "type" in a, f"Asset in format '{f.get('name')}' missing type"
            assert "dimensions" in a, f"Asset in format '{f.get('name')}' missing dimensions"


# ── Sorting assertions ──────────────────────────────────────────────


@then("the results should be sorted by format type then name")
def then_sorted_type_name(ctx: dict) -> None:
    formats = _get_formats(ctx)
    if len(formats) <= 1:
        return
    sort_keys = [(f.get("type", ""), f.get("name", "")) for f in formats]
    assert sort_keys == sorted(sort_keys), f"Formats not sorted by type then name: {sort_keys}"


@then("the results should be ordered:")
def then_results_ordered(ctx: dict, datatable: Sequence[Sequence[object]]) -> None:
    formats = _get_formats(ctx)
    headers = [str(cell) for cell in datatable[0]]
    expected = [{headers[i]: str(cell) for i, cell in enumerate(row)} for row in datatable[1:]]
    actual = [{"name": f.get("name"), "type": f.get("type")} for f in formats]
    assert actual == expected, f"Expected order {expected}, got {actual}"


# ── Specific format inclusion/exclusion ──────────────────────────────


@then("no formats should be returned")
def then_no_formats(ctx: dict) -> None:
    formats = _get_formats(ctx)
    assert len(formats) == 0, f"Expected 0 formats, got {len(formats)}"


@then(parsers.parse('only "{name}" should be returned'))
def then_only_named(ctx: dict, name: str) -> None:
    formats = _get_formats(ctx)
    assert len(formats) == 1, f"Expected exactly 1 format, got {len(formats)}: {[f.get('name') for f in formats]}"
    assert formats[0].get("name") == name, f"Expected format '{name}', got '{formats[0].get('name')}'"


@then(parsers.parse('"{name}" should be returned'))
def then_named_returned(ctx: dict, name: str) -> None:
    names = [f.get("name") for f in _get_formats(ctx)]
    assert name in names, f"Expected '{name}' in results, got {names}"


@then(parsers.parse('"{name}" should not be returned'))
def then_named_not_returned(ctx: dict, name: str) -> None:
    names = [f.get("name") for f in _get_formats(ctx)]
    assert name not in names, f"Did not expect '{name}' in results, got {names}"


@then(parsers.parse('"{a}", "{b}", and "{c}" should all be returned'))
def then_three_returned(ctx: dict, a: str, b: str, c: str) -> None:
    names = [f.get("name") for f in _get_formats(ctx)]
    for name in [a, b, c]:
        assert name in names, f"Expected '{name}' in results, got {names}"


@then(parsers.parse('the returned format type should be "{fmt_type}"'))
def then_returned_type(ctx: dict, fmt_type: str) -> None:
    for f in _get_formats(ctx):
        assert f.get("type") == fmt_type, f"Expected type '{fmt_type}', got '{f.get('type')}'"


# ── Partition/boundary test outcomes ──────────────────────────────────
# These verify that production code either:
#   - returned a valid response (expected="valid")
#   - raised an error (expected="invalid")
#
# Two regex steps cover all partition ("filtering should result in") and
# boundary ("handling should be") scenarios. The captured field name is
# unused — the When step already applied the filter; the Then step only
# checks accept/reject outcome.


def _assert_partition_outcome(ctx: dict, expected: str) -> None:
    """Assert partition/boundary test outcome against real production results.

    "valid" means production code returned successfully (response exists).
    "invalid" means production code raised an error (error exists).
    """
    if expected == "valid":
        assert "error" not in ctx, f"Expected valid result but got error: {ctx.get('error')}"
        assert "response" in ctx, "Expected response but none found"
    elif expected == "invalid":
        assert "error" in ctx, "Expected error but operation succeeded"
    else:
        raise AssertionError(f"Unexpected outcome value: {expected}")


@then(parsers.re(r"the (?P<field>.+) filtering should result in (?P<expected>\w+)"))
def then_partition_filtering_result(ctx: dict, field: str, expected: str) -> None:
    """Generic partition test: any '<field> filtering should result in <expected>'."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.re(r"the (?P<field>.+) handling should be (?P<expected>\w+)"))
def then_boundary_handling_result(ctx: dict, field: str, expected: str) -> None:
    """Generic boundary test: any '<field> handling should be <expected>'."""
    _assert_partition_outcome(ctx, expected)
