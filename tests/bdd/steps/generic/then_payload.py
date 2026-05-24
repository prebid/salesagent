"""Then steps for payload and field assertions.

Every assertion operates on real production response objects:
    ctx["response"] — ListCreativeFormatsResponse on success
    ctx["error"] — Exception on failure

No stub mode. No dict intermediaries — assertions access Format attributes directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from pytest_bdd import parsers, then

from src.core.exceptions import AdCPError

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
            # Assets are typed (Assets, Assets81=video, etc.) — check the asset_id or type attribute
            assert hasattr(a, "asset_id"), f"Asset in format '{_fmt_name(f)}' missing asset_id"
    # Check renders have dimensions
    formats_with_renders = [f for f in formats if hasattr(f, "renders") and f.renders]
    for f in formats_with_renders:
        for r in f.renders:
            assert hasattr(r, "dimensions"), f"Render in format '{_fmt_name(f)}' missing dimensions"


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
# These verify that production code, when exercised on a specific named
# dimension/boundary (the captured <field>), produced the scenario's
# expected outcome:
#   - expected="valid":   a schema-valid success response of the operation
#                          under test, with its required success collection
#                          present and correctly typed (not a junk shell).
#   - expected="invalid"/ a genuine validation/AdCP rejection (not an
#     "error":             arbitrary exception, not a bare truthy "error").
#
# Two regex steps cover all partition ("filtering should result in") and
# boundary ("handling should be") scenarios.
#
# The step text names <field> as the dimension under test. The When steps
# (out of this module's scope) record only ctx["response"]/ctx["error"],
# so the field cannot be matched against a recorded per-field outcome.
# Instead the step asserts the field is a *known* dimension of the
# operation under test — an empty or unrecognized field means a misnamed
# scenario and must fail loudly rather than pass on an unrelated outcome.

# Known partition/boundary dimensions across UC-004 (delivery) and UC-005
# (creative formats). A field outside this set is a scenario authoring
# error — the step must reject it instead of silently passing.
_KNOWN_PARTITION_FIELDS: frozenset[str] = frozenset(
    {
        # UC-005 — list_creative_formats filters
        "format_ids",
        "asset_types",
        "dimension",
        "responsive",
        "name search",
        "wcag",
        "disclosure",
        "disclosure_positions",
        "output_format_ids",
        "input_format_ids",
        "creative agent type",
        "creative agent asset type",
        # UC-004 — get_media_buy_delivery filters
        "account",
        "attribution_window",
        "reporting_dimensions",
        "daily breakdown",
        "status",
        "date",
        "date range",
        "sampling",
        "sampling method",
        "credentials",
    }
)

# Per-response-type success collections. A "valid" outcome must carry a
# present, correctly-typed instance of one of these — proving the response
# is a real result of the operation, not an empty/degenerate shell.
_SUCCESS_COLLECTION_ATTRS: tuple[str, ...] = (
    "formats",  # ListCreativeFormatsResponse
    "media_buy_deliveries",  # GetMediaBuyDeliveryResponse
    "aggregated_totals",  # GetMediaBuyDeliveryResponse
)


def _is_real_rejection(err: Any) -> bool:
    """True iff *err* is a genuine validation/AdCP rejection with a message.

    An arbitrary ``RuntimeError`` (e.g. a crash, a harness wiring bug) is
    NOT a valid "the input was rejected" outcome — it must fail the step.
    """
    if isinstance(err, AdCPError):
        return bool(getattr(err, "message", "") or str(err))
    if isinstance(err, PydanticValidationError):
        return err.error_count() > 0
    return False


def _assert_schema_valid_success(resp: Any) -> None:
    """Assert *resp* is a schema-valid success response of the operation.

    The response must be a Pydantic model, must not carry a non-empty
    ``errors`` list, and must expose at least one known success collection
    that is present and a ``list`` / structured object (not ``None``).
    """
    assert isinstance(resp, BaseModel), (
        f"Expected a schema-valid response model for a 'valid' outcome, got {type(resp).__name__}: {resp!r}"
    )
    embedded_errors = getattr(resp, "errors", None)
    assert not embedded_errors, f"'valid' outcome but response embeds errors: {embedded_errors}"

    present = [a for a in _SUCCESS_COLLECTION_ATTRS if hasattr(resp, a)]
    assert present, (
        f"Response {type(resp).__name__} exposes none of the expected "
        f"success collections {_SUCCESS_COLLECTION_ATTRS} — not a recognized "
        f"result of the operation under test"
    )
    for attr in present:
        value = getattr(resp, attr)
        assert value is not None, (
            f"'valid' outcome but response.{attr} is None — likely a production bug (degenerate success shell)"
        )
        if attr in ("formats", "media_buy_deliveries"):
            assert isinstance(value, list), (
                f"'valid' outcome but response.{attr} is {type(value).__name__}, expected a list"
            )


def _assert_partition_outcome(ctx: dict, field: str, expected: str) -> None:
    """Assert the <field> partition/boundary produced <expected>.

    "valid"  → production accepted the input: a schema-valid success
               response of the operation, no error recorded.
    "invalid"/"error" → production rejected the input with a genuine
               validation/AdCP error (not an arbitrary exception).

    The captured ``field`` must name a known dimension of the operation
    under test; an empty/unknown field, or a context with neither a
    response nor an error, fails the step loudly (it would otherwise pass
    vacuously on an unrelated outcome).
    """
    normalized_field = (field or "").strip()
    assert normalized_field, "Step captured an empty <field> — misnamed scenario"
    assert normalized_field in _KNOWN_PARTITION_FIELDS, (
        f"'{normalized_field}' is not a known partition/boundary dimension "
        f"— the step text claims filtering/handling acted on this field but "
        f"it is unrecognized (misnamed scenario or untracked dimension)"
    )

    has_response = "response" in ctx and ctx.get("response") is not None
    has_error = "error" in ctx and ctx.get("error") is not None
    assert has_response or has_error, (
        f"Neither a response nor an error was recorded for the "
        f"'{normalized_field}' {expected} scenario — the operation was "
        f"never exercised; cannot verify the outcome"
    )

    if expected == "valid":
        assert not has_error, (
            f"Expected the '{normalized_field}' input to be accepted but production raised: {ctx.get('error')!r}"
        )
        _assert_schema_valid_success(ctx["response"])
    elif expected in ("invalid", "error"):
        assert has_error, (
            f"Expected production to reject the '{normalized_field}' input "
            f"but it succeeded with: {ctx.get('response')!r}"
        )
        err = ctx["error"]
        assert _is_real_rejection(err), (
            f"Expected a genuine validation/AdCP rejection for the "
            f"'{normalized_field}' input, got "
            f"{type(err).__name__}: {err!r}"
        )
    else:
        raise AssertionError(
            f"Unexpected outcome word '{expected}' for field '{normalized_field}' — expected one of valid/invalid/error"
        )


@then(parsers.re(r"the (?P<field>.+) filtering should result in (?P<expected>\w+)"))
def then_partition_filtering_result(ctx: dict, field: str, expected: str) -> None:
    """Generic partition test: '<field> filtering should result in <expected>'.

    Verifies the named ``field`` is a real dimension of the operation and
    that the recorded outcome genuinely matches ``expected`` (schema-valid
    success xor genuine rejection) — not merely that some response or some
    error exists.
    """
    _assert_partition_outcome(ctx, field, expected)


@then(parsers.re(r"the (?P<field>.+) handling should be (?P<expected>\w+)"))
def then_boundary_handling_result(ctx: dict, field: str, expected: str) -> None:
    """Generic boundary test: '<field> handling should be <expected>'.

    Same contract as :func:`then_partition_filtering_result`, scoped to the
    named boundary ``field``. An unexercised or misnamed field fails loudly
    instead of passing on an unrelated generic outcome.
    """
    _assert_partition_outcome(ctx, field, expected)
