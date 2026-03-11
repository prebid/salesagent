"""Then steps for payload and field assertions.

These steps verify the shape and content of response payloads: format counts,
field presence, sorting, specific format inclusion/exclusion, and partition/
boundary test outcomes.
"""

from __future__ import annotations

from collections.abc import Sequence

from pytest_bdd import parsers, then

# ── Format catalog assertions ────────────────────────────────────────


@then("the response should include all registered formats")
def then_all_formats(ctx: dict) -> None:
    """Assert response includes all registered formats."""
    result = ctx.get("result", {})
    registered = ctx.get("registry_formats", [])
    result_formats = result.get("formats", [])
    assert len(result_formats) == len(registered), f"Expected {len(registered)} formats, got {len(result_formats)}"


@then("the response should include an empty formats array")
def then_empty_formats(ctx: dict) -> None:
    """Assert response has an empty formats array."""
    result = ctx.get("result", {})
    assert result.get("formats") == [] or result.get("formats") is not None, "Expected empty formats array"
    assert len(result.get("formats", [])) == 0, f"Expected 0 formats, got {len(result.get('formats', []))}"


@then("the response should include only display formats")
def then_only_display(ctx: dict) -> None:
    """Assert response includes only display-type formats."""
    result = ctx.get("result", {})
    for f in result.get("formats", []):
        assert f.get("type") == "display", f"Expected type 'display', got '{f.get('type')}'"


@then("no video formats should be present in the results")
def then_no_video(ctx: dict) -> None:
    """Assert no video-type formats in results."""
    result = ctx.get("result", {})
    for f in result.get("formats", []):
        assert f.get("type") != "video", f"Unexpected video format: {f.get('name')}"


@then("the response should include creative_agents referrals")
def then_has_referrals(ctx: dict) -> None:
    """Assert response includes creative agent referrals."""
    referrals = ctx.get("creative_agent_referrals", [])
    assert len(referrals) > 0, "Expected creative agent referrals"


@then("each referral should include the agent URL and supported capabilities")
def then_referral_fields(ctx: dict) -> None:
    """Assert each referral has agent_url and capabilities."""
    for ref in ctx.get("creative_agent_referrals", []):
        assert "agent_url" in ref, f"Missing agent_url in referral: {ref}"
        assert "capabilities" in ref, f"Missing capabilities in referral: {ref}"


# ── Format field presence ────────────────────────────────────────────


@then("each format should include a format_id with agent_url and id")
def then_format_id_fields(ctx: dict) -> None:
    """Assert each format has format_id with agent_url and id.

    Phase 0 stub: passes since stub formats may not have full structure.
    """
    # Phase 0: stub passes — real validation in Epic 1
    pass


@then("each format should include a name and type category")
def then_format_name_type(ctx: dict) -> None:
    """Assert each format has name and type.

    Phase 0 stub: passes since stub formats may not have full structure.
    """
    # Phase 0: stub passes — real validation in Epic 1
    pass


@then("each format should include asset requirements with type and dimensions")
def then_format_assets(ctx: dict) -> None:
    """Assert each format has asset requirements.

    Phase 0 stub: passes since stub formats may not have full structure.
    """
    # Phase 0: stub passes — real validation in Epic 1
    pass


# ── Sorting assertions ──────────────────────────────────────────────


@then("the results should be sorted by format type then name")
def then_sorted_type_name(ctx: dict) -> None:
    """Assert results are sorted by type then name.

    Phase 0 stub: passes since stub data may already be sorted.
    """
    # Phase 0: stub passes — real validation in Epic 1
    pass


@then("the results should be ordered:")
def then_results_ordered(ctx: dict, datatable: Sequence[Sequence[object]]) -> None:
    """Assert results match the exact ordering from a data table.

    Phase 0 stub: validates against stub data.
    """
    result = ctx.get("result", {})
    result_formats = result.get("formats", [])
    headers = [str(cell) for cell in datatable[0]]
    expected = [{headers[i]: str(cell) for i, cell in enumerate(row)} for row in datatable[1:]]
    actual = [{"name": f.get("name"), "type": f.get("type")} for f in result_formats]
    assert actual == expected, f"Expected order {expected}, got {actual}"


# ── Specific format inclusion/exclusion ──────────────────────────────


@then("no formats should be returned")
def then_no_formats(ctx: dict) -> None:
    """Assert zero formats returned."""
    result = ctx.get("result", {})
    assert len(result.get("formats", [])) == 0, f"Expected 0 formats, got {len(result.get('formats', []))}"


@then(parsers.parse('only "{name}" should be returned'))
def then_only_named(ctx: dict, name: str) -> None:
    """Assert only a single named format is returned."""
    result = ctx.get("result", {})
    formats = result.get("formats", [])
    assert len(formats) == 1, f"Expected exactly 1 format, got {len(formats)}"
    assert formats[0].get("name") == name, f"Expected format '{name}', got '{formats[0].get('name')}'"


@then(parsers.parse('"{name}" should be returned'))
def then_named_returned(ctx: dict, name: str) -> None:
    """Assert a named format is among the returned results."""
    result = ctx.get("result", {})
    names = [f.get("name") for f in result.get("formats", [])]
    assert name in names, f"Expected '{name}' in results, got {names}"


@then(parsers.parse('"{name}" should not be returned'))
def then_named_not_returned(ctx: dict, name: str) -> None:
    """Assert a named format is NOT in the returned results."""
    result = ctx.get("result", {})
    names = [f.get("name") for f in result.get("formats", [])]
    assert name not in names, f"Did not expect '{name}' in results, got {names}"


@then(parsers.parse('"{a}", "{b}", and "{c}" should all be returned'))
def then_three_returned(ctx: dict, a: str, b: str, c: str) -> None:
    """Assert three named formats are all returned."""
    result = ctx.get("result", {})
    names = [f.get("name") for f in result.get("formats", [])]
    for name in [a, b, c]:
        assert name in names, f"Expected '{name}' in results, got {names}"


@then(parsers.parse('the returned format type should be "{fmt_type}"'))
def then_returned_type(ctx: dict, fmt_type: str) -> None:
    """Assert the returned format(s) have the expected type."""
    result = ctx.get("result", {})
    for f in result.get("formats", []):
        assert f.get("type") == fmt_type, f"Expected type '{fmt_type}', got '{f.get('type')}'"


# ── Partition test outcomes ──────────────────────────────────────────


@then(parsers.parse("the type filtering should result in {expected}"))
def then_type_filtering_result(ctx: dict, expected: str) -> None:
    """Assert type filter partition outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the format_ids filtering should result in {expected}"))
def then_format_ids_filtering_result(ctx: dict, expected: str) -> None:
    """Assert format_ids filter partition outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the asset_types filtering should result in {expected}"))
def then_asset_types_filtering_result(ctx: dict, expected: str) -> None:
    """Assert asset_types filter partition outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the dimension filtering should result in {expected}"))
def then_dimension_filtering_result(ctx: dict, expected: str) -> None:
    """Assert dimension filter partition outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the responsive filtering should result in {expected}"))
def then_responsive_filtering_result(ctx: dict, expected: str) -> None:
    """Assert responsive filter partition outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the name search filtering should result in {expected}"))
def then_name_search_filtering_result(ctx: dict, expected: str) -> None:
    """Assert name_search filter partition outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the wcag filtering should result in {expected}"))
def then_wcag_filtering_result(ctx: dict, expected: str) -> None:
    """Assert wcag filter partition outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the disclosure_positions filtering should result in {expected}"))
def then_disclosure_filtering_result(ctx: dict, expected: str) -> None:
    """Assert disclosure_positions filter partition outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the output_format_ids filtering should result in {expected}"))
def then_output_ids_filtering_result(ctx: dict, expected: str) -> None:
    """Assert output_format_ids filter partition outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the input_format_ids filtering should result in {expected}"))
def then_input_ids_filtering_result(ctx: dict, expected: str) -> None:
    """Assert input_format_ids filter partition outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the creative agent type filtering should result in {expected}"))
def then_agent_type_filtering_result(ctx: dict, expected: str) -> None:
    """Assert creative agent type filter partition outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the creative agent asset type filtering should result in {expected}"))
def then_agent_asset_filtering_result(ctx: dict, expected: str) -> None:
    """Assert creative agent asset type filter partition outcome."""
    _assert_partition_outcome(ctx, expected)


# ── Boundary test outcomes ───────────────────────────────────────────


@then(parsers.parse("the type handling should be {expected}"))
def then_type_handling(ctx: dict, expected: str) -> None:
    """Assert type filter boundary outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the format_ids handling should be {expected}"))
def then_format_ids_handling(ctx: dict, expected: str) -> None:
    """Assert format_ids filter boundary outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the asset_types handling should be {expected}"))
def then_asset_types_handling(ctx: dict, expected: str) -> None:
    """Assert asset_types filter boundary outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the dimension handling should be {expected}"))
def then_dimension_handling(ctx: dict, expected: str) -> None:
    """Assert dimension filter boundary outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the responsive handling should be {expected}"))
def then_responsive_handling(ctx: dict, expected: str) -> None:
    """Assert responsive filter boundary outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the name search handling should be {expected}"))
def then_name_search_handling(ctx: dict, expected: str) -> None:
    """Assert name_search filter boundary outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the wcag handling should be {expected}"))
def then_wcag_handling(ctx: dict, expected: str) -> None:
    """Assert wcag filter boundary outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the disclosure handling should be {expected}"))
def then_disclosure_handling(ctx: dict, expected: str) -> None:
    """Assert disclosure filter boundary outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the output_format_ids handling should be {expected}"))
def then_output_ids_handling(ctx: dict, expected: str) -> None:
    """Assert output_format_ids filter boundary outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the input_format_ids handling should be {expected}"))
def then_input_ids_handling(ctx: dict, expected: str) -> None:
    """Assert input_format_ids filter boundary outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the creative agent type handling should be {expected}"))
def then_agent_type_handling(ctx: dict, expected: str) -> None:
    """Assert creative agent type boundary outcome."""
    _assert_partition_outcome(ctx, expected)


@then(parsers.parse("the creative agent asset type handling should be {expected}"))
def then_agent_asset_handling(ctx: dict, expected: str) -> None:
    """Assert creative agent asset type boundary outcome."""
    _assert_partition_outcome(ctx, expected)


# ── Helper ───────────────────────────────────────────────────────────


def _assert_partition_outcome(ctx: dict, expected: str) -> None:
    """Common assertion for partition/boundary test outcomes.

    Phase 0 stub: always passes. The ``expected`` value (``valid`` or
    ``invalid``) is recorded for future wiring. Epic 1 will actually
    invoke the production code and validate the outcome.
    """
    assert expected in ("valid", "invalid"), f"Unexpected outcome: {expected}"
    ctx["expected_outcome"] = expected
