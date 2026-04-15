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
    formats = _get_formats(ctx)
    # Derive expected display count from the registry (real catalog subset)
    registered = ctx.get("registry_formats", [])
    expected_display = [r for r in registered if _fmt_type_str(r) == "display"]
    assert len(formats) == len(expected_display), (
        f"Expected {len(expected_display)} display formats, got {len(formats)}"
    )
    for f in formats:
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
    """Assert EVERY format has asset requirements with the exact required keys.

    UC-005 POST-S2: Asset requirements included per format. The Gherkin claim
    is "asset requirements with type and dimensions" — enforce the exact
    required key set per adcp spec:

      - asset items MUST carry: ``asset_type`` AND ``asset_id`` (type + identity)
      - render items MUST carry: ``dimensions`` with a width field
        (``width`` for fixed, or ``min_width`` for responsive)

    A format missing asset requirements entirely is a failure — the step says
    "each format", not "most formats".
    """
    # Per adcp v1.2.1 AssetRequirement: asset_type is the type enum, asset_id is
    # the identifier. These are the minimum keys the buyer needs to understand
    # what to submit.
    required_asset_keys = {"asset_type", "asset_id"}

    formats = _get_formats(ctx)
    assert len(formats) > 0, "No formats in response — cannot verify asset requirements"

    for f in formats:
        has_assets = hasattr(f, "assets") and f.assets
        has_renders = hasattr(f, "renders") and f.renders
        assert has_assets or has_renders, (
            f"Format '{_fmt_name(f)}' has neither assets nor renders — "
            f"step requires 'each format' to include asset requirements"
        )

        if has_assets:
            for a in f.assets:
                # Enumerate actual keys on the asset (via model_dump for Pydantic
                # or attribute introspection for plain objects).
                if hasattr(a, "model_dump"):
                    asset_keys = set(a.model_dump(exclude_none=True).keys())
                else:
                    asset_keys = {k for k in dir(a) if not k.startswith("_") and getattr(a, k, None) is not None}
                missing = required_asset_keys - asset_keys
                assert not missing, (
                    f"Asset in format '{_fmt_name(f)}' missing required keys {missing} "
                    f"(has: {asset_keys & required_asset_keys or 'none of the required keys'}). "
                    f"Spec requires each asset requirement to carry asset_type + asset_id."
                )
                # asset_type value must be a concrete string (enum value), not None/empty.
                asset_type = getattr(a, "asset_type", None)
                asset_type_str = asset_type.value if hasattr(asset_type, "value") else asset_type
                assert asset_type_str, (
                    f"Asset in format '{_fmt_name(f)}' has empty asset_type — buyers cannot know what media to upload"
                )

        if has_renders:
            for r in f.renders:
                dims = getattr(r, "dimensions", None)
                assert dims is not None, f"Render in format '{_fmt_name(f)}' missing dimensions"
                # Enumerate dimension keys and require at least one width specification.
                if hasattr(dims, "model_dump"):
                    dim_keys = set(dims.model_dump(exclude_none=True).keys())
                else:
                    dim_keys = {k for k in dir(dims) if not k.startswith("_") and getattr(dims, k, None) is not None}
                width_keys = {"width", "min_width"}
                assert dim_keys & width_keys, (
                    f"Render dimensions in format '{_fmt_name(f)}' missing width specification. "
                    f"Expected one of {width_keys}, got keys: {dim_keys}"
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

    Shared helper for both ``then_partition_filtering_result`` (filtering) and
    ``then_boundary_handling_result`` (boundary). Keeping the logic in ONE place
    prevents the two steps from drifting apart and enforces consistent
    filter-field spell-checking.

    Semantics:
      - ``expected == "valid"`` → production returned successfully AND the
        response is well-formed (has a ``formats`` list). This proves the
        filter was accepted, not just that no exception leaked.
      - ``expected == "invalid"`` → production raised an error; the response
        must NOT be present (otherwise the filter was silently accepted).

    The ``field`` parameter is validated against ``_KNOWN_FILTER_FIELDS`` to
    catch Gherkin typos at test-collection time rather than letting a wrong
    filter name pass silently.
    """
    assert field in _KNOWN_FILTER_FIELDS, (
        f"Unknown filter field '{field}' in partition/boundary test. Known fields: {sorted(_KNOWN_FILTER_FIELDS)}"
    )
    if expected == "valid":
        assert "error" not in ctx, f"Expected valid filter result for '{field}' but got error: {ctx.get('error')}"
        resp = ctx.get("response")
        assert resp is not None, (
            f"Expected response for '{field}' filter but ctx['response'] is absent — "
            "production did not produce a result"
        )
        # "valid" means the filter was accepted AND produced a well-formed list.
        assert hasattr(resp, "formats"), (
            f"Response for '{field}' filter missing 'formats' attribute — "
            f"got {type(resp).__name__} which is not a ListCreativeFormatsResponse shape"
        )
        assert isinstance(resp.formats, list), (
            f"Expected 'formats' to be a list for '{field}' filter, got {type(resp.formats).__name__}"
        )
    elif expected == "invalid":
        assert "error" in ctx, (
            f"Expected '{field}' filter to be rejected as invalid, but operation succeeded "
            f"with response: {ctx.get('response')!r}"
        )
        # On invalid input, there should be NO valid response — a success response
        # alongside an error means the filter was silently accepted.
        resp = ctx.get("response")
        assert resp is None, (
            f"Expected no response on invalid '{field}' filter, got both error "
            f"{ctx.get('error')!r} AND response {resp!r}"
        )
    else:
        raise AssertionError(f"Unexpected outcome value '{expected}' for '{field}' — expected 'valid' or 'invalid'")


@then(parsers.re(r"the (?P<field>.+) filtering should result in (?P<expected>\w+)"))
def then_partition_filtering_result(ctx: dict, field: str, expected: str) -> None:
    """Generic partition test: any '<field> filtering should result in <expected>'.

    Delegates to ``_assert_partition_outcome`` — same logic as
    ``then_boundary_handling_result`` to guarantee consistent semantics
    across partition and boundary Gherkin phrasings.
    """
    _assert_partition_outcome(ctx, field, expected)


@then(parsers.re(r"the (?P<field>.+) handling should be (?P<expected>\w+)"))
def then_boundary_handling_result(ctx: dict, field: str, expected: str) -> None:
    """Generic boundary test: any '<field> handling should be <expected>'."""
    _assert_partition_outcome(ctx, field, expected)
