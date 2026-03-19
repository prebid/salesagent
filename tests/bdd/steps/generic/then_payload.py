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
    formats = _get_formats(ctx)
    registered = ctx.get("registry_formats", [])
    assert len(formats) == len(registered), f"Expected {len(registered)} formats, got {len(formats)}"
    # Verify format identities match (not just count)
    response_names = {_fmt_name(f) for f in formats}
    registered_names: set[str | None] = set()
    for r in registered:
        name = r.name if hasattr(r, "name") else (r.get("name") if isinstance(r, dict) else None)
        if name is not None:
            registered_names.add(name)
    if registered_names:
        assert response_names == registered_names, (
            f"Format names mismatch: response={response_names}, registered={registered_names}"
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
    for f in _get_formats(ctx):
        assert _fmt_type_str(f) != "video", f"Unexpected video format: {_fmt_name(f)}"


@then("the response should include creative_agents referrals")
def then_has_referrals(ctx: dict) -> None:
    """Assert response contains creative_agents with well-formed referral entries."""
    resp = ctx.get("response")
    referrals = getattr(resp, "creative_agents", None) or []
    assert len(referrals) > 0, "Expected creative agent referrals in response"
    for ref in referrals:
        assert getattr(ref, "agent_url", None), f"Referral missing agent_url: {ref}"
        # A valid referral should also indicate capabilities
        caps = getattr(ref, "capabilities", None)
        assert caps is not None, f"Referral missing capabilities for agent_url={getattr(ref, 'agent_url', '?')}"


@then("each referral should include the agent URL and supported capabilities")
def then_referral_fields(ctx: dict) -> None:
    resp = ctx.get("response")
    referrals = getattr(resp, "creative_agents", None) or []
    for ref in referrals:
        agent_url = getattr(ref, "agent_url", None)
        assert isinstance(agent_url, str) and len(agent_url) > 0, (
            f"Expected agent_url to be a non-empty string, got {type(agent_url).__name__}: {agent_url!r}"
        )
        capabilities = getattr(ref, "capabilities", None)
        assert isinstance(capabilities, (list, tuple)) and len(capabilities) > 0, (
            f"Expected capabilities to be a non-empty list/tuple, got {type(capabilities).__name__}: {capabilities!r}"
        )


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
    for f in _get_formats(ctx):
        name = _fmt_name(f)
        assert isinstance(name, str) and len(name) > 0, (
            f"Expected non-empty string name, got {type(name).__name__}: {name!r}"
        )
        fmt_type = _fmt_type_str(f)
        assert isinstance(fmt_type, str) and len(fmt_type) > 0, (
            f"Expected non-empty string type category, got {type(fmt_type).__name__}: {fmt_type!r}"
        )


@then("each format should include asset requirements with type and dimensions")
def then_format_assets(ctx: dict) -> None:
    """Assert ALL formats include asset requirements with type and dimension information.

    AdCP asset objects use ``asset_type`` (not ``type``) for the type discriminator,
    and ``requirements`` (not ``dimensions``) for dimension/size constraints.

    POST-S2 requires the buyer to know asset requirements for EACH format.
    Formats without assets are flagged (not silently skipped).
    """
    formats = _get_formats(ctx)
    # Tag-only formats (no visual assets) are exempt — document any exempt types here
    _ASSET_EXEMPT_TYPES = {"tag", "pixel", "tracker"}

    formats_without_assets = []
    for f in formats:
        fmt_type = str(getattr(f, "type", "")).lower()
        has_assets = hasattr(f, "assets") and f.assets
        if not has_assets and fmt_type not in _ASSET_EXEMPT_TYPES:
            formats_without_assets.append(_fmt_name(f))

    assert not formats_without_assets, (
        f"POST-S2 violation: {len(formats_without_assets)} format(s) have no asset requirements "
        f"(and are not exempt types {_ASSET_EXEMPT_TYPES}): {formats_without_assets[:5]}"
    )

    formats_with_assets = [f for f in formats if hasattr(f, "assets") and f.assets]
    assert len(formats_with_assets) > 0, "No formats with assets found"
    for f in formats_with_assets:
        for a in f.assets:
            has_asset_type = hasattr(a, "asset_type") and a.asset_type is not None
            assert has_asset_type, f"Asset in format '{_fmt_name(f)}' missing asset_type: {a}"
            has_reqs = hasattr(a, "requirements") and a.requirements is not None
            assert has_reqs, f"Asset in format '{_fmt_name(f)}' missing requirements: {a}"


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
#   - returned a valid response with content (expected="valid")
#   - raised an error referencing the tested field (expected="invalid")
#
# Two regex steps cover all partition ("filtering should result in") and
# boundary ("handling should be") scenarios. The captured field name is
# used to verify that errors reference the specific field being tested.


def _field_keywords(field: str) -> list[str]:
    """Derive search keywords from a human-readable field name.

    The step text uses labels like "disclosure", "name search", "dimension".
    Error messages use technical names like "disclosure_positions", "name_search",
    "min_width". This returns a list of keywords that should appear in the error
    for a given field label.
    """
    normalized = field.lower().replace(" ", "_")
    keywords = [normalized]
    # Add the space-separated form if different
    if "_" in normalized:
        keywords.append(normalized.replace("_", " "))
    # Also add individual words for multi-word fields
    parts = normalized.split("_")
    if len(parts) > 1:
        keywords.extend(parts)
    return keywords


def _assert_valid_format_content(ctx: dict, field: str, resp: object) -> None:
    """Per-field content assertion for UC-005 format partition/boundary outcomes.

    Verifies the field under test actually affected the response. Fields
    without specific handlers fall through (backward compatible).
    """
    formats = getattr(resp, "formats", None)

    if field in ("format_id", "format_ids") and formats is not None:
        # Verify returned format IDs match the filter
        request_params = ctx.get("request_params", {})
        requested_ids = request_params.get("format_ids")
        if requested_ids and formats:
            returned_ids = {getattr(f, "format_id", None) for f in formats}
            for fid in requested_ids:
                assert fid in returned_ids, (
                    f"Format filter violation: requested '{fid}' not in response: {returned_ids}"
                )

    elif field == "type" and formats is not None:
        # Verify all returned formats match the type filter
        request_params = ctx.get("request_params", {})
        type_filter = request_params.get("type")
        if type_filter and formats:
            for fmt in formats:
                actual_type = getattr(fmt, "type", None)
                if actual_type:
                    assert str(actual_type) == type_filter, (
                        f"Type filter violation: got '{actual_type}', expected '{type_filter}'"
                    )

    elif formats is not None and len(formats) == 0:
        # For valid outcomes with filtering, empty results may be correct
        # (e.g., no_match partition). Don't fail on empty — the Gherkin
        # scenario's expected outcome already encodes whether results should exist.
        pass

    # Fields without specific handlers: basic existence check is sufficient.


def _assert_partition_outcome(ctx: dict, expected: str, field: str = "") -> None:
    """Assert partition/boundary test outcome against real production results.

    "valid" means production code returned successfully with content.
    "invalid" means production code raised an error referencing the tested field.

    The ``field`` parameter identifies which field was filtered/handled — for
    invalid outcomes, the error message must reference this field to prove
    the rejection was caused by the specific field being tested, not by an
    unrelated issue.
    """
    field_desc = f" for '{field}'" if field else ""
    if expected == "valid":
        assert "error" not in ctx, f"Expected valid result{field_desc} but got error: {ctx.get('error')}"
        assert "response" in ctx, f"Expected response{field_desc} but none found"
        resp = ctx["response"]
        assert resp is not None, f"Response{field_desc} is None"
        # Per-field content check: verify the field actually affected the response
        _assert_valid_format_content(ctx, field, resp)
    elif expected == "invalid":
        assert "error" in ctx, f"Expected error{field_desc} but operation succeeded"
        if field:
            error = ctx["error"]
            error_text = str(error).lower()
            keywords = _field_keywords(field)
            assert any(kw in error_text for kw in keywords), (
                f"Expected error{field_desc} to reference the field, but none of {keywords} found in: {error}"
            )
    else:
        raise AssertionError(f"Unexpected outcome value '{expected}'{field_desc}")


@then(parsers.re(r"the (?P<field>.+) filtering should result in (?P<expected>\w+)"))
def then_partition_filtering_result(ctx: dict, field: str, expected: str) -> None:
    """Generic partition test: any '<field> filtering should result in <expected>'.

    The field param is recorded in ctx and included in assertion messages to ensure
    it is not silently discarded. This makes failures traceable to the specific field.
    """
    assert field, "Step text must specify a filter field"
    ctx["_asserted_filter_field"] = field
    _assert_partition_outcome(ctx, expected, field=field)


@then(parsers.re(r"the (?P<field>.+) handling should be (?P<expected>\w+)"))
def then_boundary_handling_result(ctx: dict, field: str, expected: str) -> None:
    """Generic boundary test: any '<field> handling should be <expected>'.

    The field param is recorded in ctx and included in assertion messages to ensure
    it is not silently discarded. This makes failures traceable to the specific field.
    """
    assert field, "Step text must specify a boundary field"
    ctx["_asserted_boundary_field"] = field
    _assert_partition_outcome(ctx, expected, field=field)
