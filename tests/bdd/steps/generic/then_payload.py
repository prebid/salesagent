"""Then steps for payload and field assertions.

Every assertion operates on real production response objects:
    ctx["response"] — ListCreativeFormatsResponse on success
    ctx["error"] — Exception on failure

No stub mode. No dict intermediaries — assertions access Format attributes directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pytest_bdd import parsers, then

# ── Helpers ────────────────────────────────────────────────────────────


def _get_formats(ctx: dict) -> list[Any]:
    """Extract formats list from response as real Format objects."""
    resp = ctx.get("response")
    if resp is None:
        return []
    if hasattr(resp, "formats"):
        return list(resp.formats or [])
    return []


def _fmt_type_str(f: Any) -> str | None:
    """Get format type as a string value (enum .value or str)."""
    if f.type is None:
        return None
    return f.type.value if hasattr(f.type, "value") else str(f.type)


def _fmt_name(f: Any) -> str | None:
    """Get format name."""
    return f.name if hasattr(f, "name") else None


# ── Format catalog assertions ────────────────────────────────────────


@then("the response should include all registered formats")
def then_all_formats(ctx: dict) -> None:
    """Assert response includes ALL registered formats — identity check, not just count."""
    formats = _get_formats(ctx)
    registered = ctx.get("registry_formats", [])
    assert len(formats) == len(registered), f"Expected {len(registered)} formats, got {len(formats)}"
    # Verify format identity — not just count but the same formats by name
    returned_names = {_fmt_name(f) for f in formats}
    registered_names = {_fmt_name(r) if hasattr(r, "name") else str(r) for r in registered}
    assert returned_names == registered_names, (
        f"Format identity mismatch: returned={returned_names}, registered={registered_names}"
    )


@then("the response should include an empty formats array")
def then_empty_formats(ctx: dict) -> None:
    formats = _get_formats(ctx)
    assert len(formats) == 0, f"Expected 0 formats, got {len(formats)}"


@then("the response should include only display formats")
def then_only_display(ctx: dict) -> None:
    for f in _get_formats(ctx):
        assert _fmt_type_str(f) == "display", f"Expected type 'display', got '{_fmt_type_str(f)}'"


@then("no video formats should be present in the results")
def then_no_video(ctx: dict) -> None:
    """Assert no video formats are present in the results.

    Step text claims "no video formats" — assert exactly that.
    """
    video_formats = [f for f in _get_formats(ctx) if _fmt_type_str(f) == "video"]
    assert not video_formats, (
        f"Expected no video formats but found {len(video_formats)}: {[_fmt_name(f) for f in video_formats]}"
    )


@then("the response should include creative_agents referrals")
def then_has_referrals(ctx: dict) -> None:
    """Assert response contains creative_agents with well-formed referral entries."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    referrals = getattr(resp, "creative_agents", None) or []
    assert len(referrals) > 0, "Expected creative agent referrals in response"
    for ref in referrals:
        assert getattr(ref, "agent_url", None), f"Referral missing agent_url: {ref}"
        # Referrals must include capabilities to be well-formed
        capabilities = getattr(ref, "capabilities", None)
        assert capabilities is not None, f"Referral missing capabilities: {ref}"


@then("each referral should include the agent URL and supported capabilities")
def then_referral_fields(ctx: dict) -> None:
    """Assert each referral has a well-formed agent_url AND non-empty capabilities list.

    Strengthens then_has_referrals by verifying:
    - agent_url looks like a URL (starts with http)
    - capabilities is a non-empty list (not just truthy)
    """
    resp = ctx.get("response")
    referrals = getattr(resp, "creative_agents", None) or []
    assert len(referrals) > 0, "No referrals to verify — expected at least one creative agent"
    for ref in referrals:
        url = getattr(ref, "agent_url", None)
        assert url, f"Missing agent_url in referral: {ref}"
        assert isinstance(url, str) and url.startswith("http"), f"agent_url should be a URL (http/https), got: {url!r}"
        caps = getattr(ref, "capabilities", None)
        assert caps is not None, f"Missing capabilities in referral: {ref}"
        assert isinstance(caps, list) and len(caps) > 0, f"capabilities should be a non-empty list, got: {caps!r}"


# ── Format field presence ────────────────────────────────────────────


@then("each format should include a format_id with agent_url and id")
def then_format_id_fields(ctx: dict) -> None:
    for f in _get_formats(ctx):
        fid = f.format_id if hasattr(f, "format_id") else None
        assert fid is not None, f"Format '{_fmt_name(f)}' missing format_id"
        assert getattr(fid, "agent_url", None), f"Format '{_fmt_name(f)}' format_id missing agent_url"
        assert getattr(fid, "id", None), f"Format '{_fmt_name(f)}' format_id missing id"


@then("each format should include a name and type category")
def then_format_name_type(ctx: dict) -> None:
    # Known valid format type category values (from adcp FormatType/FormatCategory enum)
    valid_types = {"audio", "video", "display", "native", "dooh", "rich_media", "universal"}
    for f in _get_formats(ctx):
        name = _fmt_name(f)
        assert name, f"Format missing name: {f}"
        assert isinstance(name, str), f"Format name is not a string: {type(name)}"
        type_str = _fmt_type_str(f)
        assert type_str, f"Format missing type: {f}"
        assert type_str in valid_types, (
            f"Format '{name}' has invalid type category '{type_str}', expected one of {valid_types}"
        )


@then("each format should include asset requirements with type and dimensions")
def then_format_assets(ctx: dict) -> None:
    """Assert EVERY format has asset requirements with type (asset_type) AND dimensions (on renders).

    Step text says 'each format' — a format missing asset requirements entirely is a failure,
    not a format to silently skip.
    """
    formats = _get_formats(ctx)
    assert len(formats) > 0, "No formats in response — cannot verify asset requirements"

    for f in formats:
        has_assets = hasattr(f, "assets") and f.assets
        has_renders = hasattr(f, "renders") and f.renders
        # Step says "each format should include asset requirements" — every format must have
        # at least assets or renders (not silently skip formats without them)
        assert has_assets or has_renders, (
            f"Format '{_fmt_name(f)}' has neither assets nor renders — "
            f"step requires 'each format' to include asset requirements"
        )
        # Verify assets have type indicators
        if has_assets:
            for a in f.assets:
                has_type = hasattr(a, "asset_type") or hasattr(a, "type") or hasattr(a, "asset_id")
                assert has_type, f"Asset in format '{_fmt_name(f)}' missing type indicator"
        # Verify renders have dimensions
        if has_renders:
            for r in f.renders:
                dims = getattr(r, "dimensions", None)
                assert dims is not None, f"Render in format '{_fmt_name(f)}' missing dimensions"
                assert getattr(dims, "width", None) is not None or getattr(dims, "min_width", None) is not None, (
                    f"Render dimensions in format '{_fmt_name(f)}' missing width"
                )


# ── Sorting assertions ──────────────────────────────────────────────


@then("the results should be sorted by format type then name")
def then_sorted_type_name(ctx: dict) -> None:
    formats = _get_formats(ctx)
    if len(formats) <= 1:
        return
    sort_keys = [(_fmt_type_str(f) or "", _fmt_name(f) or "") for f in formats]
    assert sort_keys == sorted(sort_keys), f"Formats not sorted by type then name: {sort_keys}"


@then("the results should be ordered:")
def then_results_ordered(ctx: dict, datatable: Sequence[Sequence[object]]) -> None:
    formats = _get_formats(ctx)
    headers = [str(cell) for cell in datatable[0]]
    expected = [{headers[i]: str(cell) for i, cell in enumerate(row)} for row in datatable[1:]]
    actual = [{"name": _fmt_name(f), "type": _fmt_type_str(f)} for f in formats]
    assert actual == expected, f"Expected order {expected}, got {actual}"


# ── Specific format inclusion/exclusion ──────────────────────────────


@then("no formats should be returned")
def then_no_formats(ctx: dict) -> None:
    formats = _get_formats(ctx)
    assert len(formats) == 0, f"Expected 0 formats, got {len(formats)}"


@then(parsers.parse('only "{name}" should be returned'))
def then_only_named(ctx: dict, name: str) -> None:
    formats = _get_formats(ctx)
    names = [_fmt_name(f) for f in formats]
    assert len(formats) == 1, f"Expected exactly 1 format, got {len(formats)}: {names}"
    assert _fmt_name(formats[0]) == name, f"Expected format '{name}', got '{_fmt_name(formats[0])}'"


@then(parsers.parse('"{name}" should be returned'))
def then_named_returned(ctx: dict, name: str) -> None:
    names = [_fmt_name(f) for f in _get_formats(ctx)]
    assert name in names, f"Expected '{name}' in results, got {names}"


@then(parsers.parse('"{name}" should not be returned'))
def then_named_not_returned(ctx: dict, name: str) -> None:
    names = [_fmt_name(f) for f in _get_formats(ctx)]
    assert name not in names, f"Did not expect '{name}' in results, got {names}"


@then(parsers.parse('"{a}", "{b}", and "{c}" should all be returned'))
def then_three_returned(ctx: dict, a: str, b: str, c: str) -> None:
    names = [_fmt_name(f) for f in _get_formats(ctx)]
    for name in [a, b, c]:
        assert name in names, f"Expected '{name}' in results, got {names}"


@then(parsers.parse('the returned format type should be "{fmt_type}"'))
def then_returned_type(ctx: dict, fmt_type: str) -> None:
    for f in _get_formats(ctx):
        assert _fmt_type_str(f) == fmt_type, f"Expected type '{fmt_type}', got '{_fmt_type_str(f)}'"


# ── Partition/boundary test outcomes ──────────────────────────────────
# These verify that production code either:
#   - returned a valid response (expected="valid")
#   - raised an error (expected="invalid")
#
# Two regex steps cover all partition ("filtering should result in") and
# boundary ("handling should be") scenarios. The captured field name is
# unused — the When step already applied the filter; the Then step only
# checks accept/reject outcome.


# Known filter fields used in partition/boundary tests (catches Gherkin typos)
_KNOWN_FILTER_FIELDS = frozenset(
    {
        "type",
        "format_ids",
        "asset_types",
        "dimension",
        "responsive",
        "name search",
        "wcag",
        "disclosure_positions",
        "disclosure",
        "output_format_ids",
        "input_format_ids",
        "creative agent type",
        "creative agent asset type",
    }
)


def _assert_partition_outcome(ctx: dict, field: str, expected: str) -> None:
    """Assert partition/boundary test outcome against real production results.

    "valid" means production code returned successfully AND produced a well-formed
    response with a formats array. "invalid" means production code raised an error.
    The field parameter is validated against known filter fields to catch Gherkin typos.
    """
    assert field in _KNOWN_FILTER_FIELDS, (
        f"Unknown filter field '{field}' in partition/boundary test. Known fields: {sorted(_KNOWN_FILTER_FIELDS)}"
    )
    if expected == "valid":
        assert "error" not in ctx, f"Expected valid result but got error: {ctx.get('error')}"
        resp = ctx.get("response")
        assert resp is not None, "Expected response but none found"
        # "valid" means the filter was accepted and a well-formed response was produced.
        # Verify response has the expected structure (formats array present).
        if hasattr(resp, "formats"):
            assert isinstance(resp.formats, list), f"Expected formats to be a list, got {type(resp.formats)}"
    elif expected == "invalid":
        assert "error" in ctx, "Expected error but operation succeeded"
    else:
        raise AssertionError(f"Unexpected outcome value: {expected}")


@then(parsers.re(r"the (?P<field>.+) filtering should result in (?P<expected>\w+)"))
def then_partition_filtering_result(ctx: dict, field: str, expected: str) -> None:
    """Generic partition test: any '<field> filtering should result in <expected>'.

    Inlines assertion logic so the step body is self-contained.
    """
    assert field in _KNOWN_FILTER_FIELDS, (
        f"Unknown filter field '{field}' in partition test. Known fields: {sorted(_KNOWN_FILTER_FIELDS)}"
    )
    if expected == "valid":
        assert "error" not in ctx, f"Expected valid result but got error: {ctx.get('error')}"
        resp = ctx.get("response")
        assert resp is not None, "Expected response but none found"
        if hasattr(resp, "formats"):
            assert isinstance(resp.formats, list), f"Expected formats to be a list, got {type(resp.formats)}"
    elif expected == "invalid":
        assert "error" in ctx, "Expected error but operation succeeded"
    else:
        raise AssertionError(f"Unexpected outcome value: {expected}")


@then(parsers.re(r"the (?P<field>.+) handling should be (?P<expected>\w+)"))
def then_boundary_handling_result(ctx: dict, field: str, expected: str) -> None:
    """Generic boundary test: any '<field> handling should be <expected>'."""
    _assert_partition_outcome(ctx, field, expected)
