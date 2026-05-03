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
    # Identity check: returned format IDs must match registered format IDs
    returned_ids = set()
    for f in formats:
        fid = getattr(f, "format_id", None)
        if fid is not None:
            returned_ids.add(getattr(fid, "id", None))
    registered_ids = set()
    for r in registered:
        fid = getattr(r, "format_id", None)
        if fid is not None:
            registered_ids.add(getattr(fid, "id", None))
    if registered_ids:
        assert returned_ids == registered_ids, (
            f"Format identity mismatch: returned {returned_ids}, "
            f"registered {registered_ids}. "
            f"Extra: {returned_ids - registered_ids}, Missing: {registered_ids - returned_ids}"
        )


@then("the response should include an empty formats array")
def then_empty_formats(ctx: dict) -> None:
    formats = _get_formats(ctx)
    assert len(formats) == 0, f"Expected 0 formats, got {len(formats)}"


def _asset_type_strs(f: Any) -> set[str]:
    """Extract normalized asset type strings from a Format object."""
    assets = getattr(f, "assets", None) or []
    raw = {getattr(a, "asset_type", None) for a in assets}
    return {at.value if hasattr(at, "value") else str(at) for at in raw if at is not None}


@then("the response should include only formats with image assets")
def then_only_image_assets(ctx: dict) -> None:
    """Assert every returned format has at least one image asset."""
    formats = _get_formats(ctx)
    assert len(formats) > 0, "Expected at least one format, got 0"
    for f in formats:
        types = _asset_type_strs(f)
        assert "image" in types, f"Format '{_fmt_name(f)}' has no image assets, asset_types={types}"


@then("no video-only formats should be present in the results")
def then_no_video_only(ctx: dict) -> None:
    """Assert no returned format has only video assets (no image assets)."""
    for f in _get_formats(ctx):
        if _asset_type_strs(f) == {"video"}:
            raise AssertionError(f"Format '{_fmt_name(f)}' is video-only, should be excluded")


@then("the response should include creative_agents referrals")
def then_has_referrals(ctx: dict) -> None:
    """Assert response contains creative_agents with well-formed referral entries."""
    resp = ctx.get("response")
    referrals = getattr(resp, "creative_agents", None) or []
    assert len(referrals) > 0, "Expected creative agent referrals in response"
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
        fid = f.format_id if hasattr(f, "format_id") else None
        assert fid is not None, f"Format '{_fmt_name(f)}' missing format_id"
        assert getattr(fid, "agent_url", None), f"Format '{_fmt_name(f)}' format_id missing agent_url"
        assert getattr(fid, "id", None), f"Format '{_fmt_name(f)}' format_id missing id"


@then("each format should include asset requirements with type and dimensions")
def then_format_assets(ctx: dict) -> None:
    """Assert formats with assets have typed assets and formats with renders have dimensions."""
    formats = _get_formats(ctx)
    formats_with_assets = [f for f in formats if hasattr(f, "assets") and f.assets]
    for f in formats_with_assets:
        for a in f.assets:
            # Assets are typed (Assets, Assets5=video, etc.) — check the asset_id value, not just presence
            asset_id = getattr(a, "asset_id", None)
            assert asset_id is not None and str(asset_id), (
                f"Asset in format '{_fmt_name(f)}' has empty/missing asset_id"
            )
    # Check renders have dimensions
    formats_with_renders = [f for f in formats if hasattr(f, "renders") and f.renders]
    for f in formats_with_renders:
        for r in f.renders:
            dimensions = getattr(r, "dimensions", None)
            assert dimensions is not None, f"Render in format '{_fmt_name(f)}' has None dimensions"


# ── Sorting assertions ──────────────────────────────────────────────


@then("the results should be sorted by name")
def then_sorted_name(ctx: dict) -> None:
    formats = _get_formats(ctx)
    if len(formats) <= 1:
        return
    names = [_fmt_name(f) or "" for f in formats]
    assert names == sorted(names), f"Formats not sorted by name: {names}"


@then("the results should be ordered by name:")
def then_results_ordered_by_name(ctx: dict, datatable: Sequence[Sequence[object]]) -> None:
    formats = _get_formats(ctx)
    headers = [str(cell) for cell in datatable[0]]
    expected = [{headers[i]: str(cell) for i, cell in enumerate(row)} for row in datatable[1:]]
    actual = [{"name": _fmt_name(f)} for f in formats]
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

    "valid" means production code accepted the input (response exists, no error).
    "invalid" means production code rejected the input (error raised).

    This is intentionally a binary accept/reject gate — it tests whether the
    production code correctly classifies an input as valid or invalid. Content
    verification (what the response contains) belongs in separate Then steps
    that follow this one in the scenario (e.g., then_only_image_assets, then_all_formats).
    """
    if expected == "valid":
        assert "error" not in ctx, f"Expected valid result but got error: {ctx.get('error')}"
        assert "response" in ctx, "Expected response but none found"
        # Verify the response is non-degenerate (not an empty shell)
        resp = ctx["response"]
        if hasattr(resp, "formats"):
            assert resp.formats is not None, "Response has formats=None — likely a production bug"
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
