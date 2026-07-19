"""Regression tests for deterministic local BDD scenario overlays."""

from pathlib import Path

from scripts import compile_bdd

FEATURE_FILENAME = "BR-UC-002-create-media-buy.feature"
SCENARIO_ID = "T-UC-002-v31-idempotency-replay"
BOUNDARY_SCENARIO_ID = "T-UC-002-v31-idempotency-pattern-invalid"

UPSTREAM_SOURCE = f"""\
# @contextgit id=BR-UC-002 type=feature
Feature: BR-UC-002 Create Media Buy

# @contextgit id={SCENARIO_ID} type=test upstream=[BR-UC-002-ext-w,BR-RULE-211]
@v31 @idempotency-key @post-s1 @ext-w @happy-path
Scenario: v3.1 idempotency_key replay returns existing media buy without re-execution
  Given the tenant is configured for auto-approval
  When the Buyer Agent sends the create_media_buy request
  Then the response should include the previously created "media_buy_id"

# @contextgit id={BOUNDARY_SCENARIO_ID} type=test upstream=[BR-UC-002-ext-w,BR-RULE-211]
@v31 @idempotency-key @validation @post-f2 @ext-w
Scenario Outline: v3.1 idempotency_key violates length/pattern constraints
  Given a create_media_buy request with idempotency_key "<value>"
  When the Buyer Agent sends the create_media_buy request
  Then the response should indicate a validation error

  Examples:
    | value | violation |
    | AAAA  | maxLength 255 violated |
"""

LEGACY_COMPILED = f"""\
# Generated from adcp-req @ old on 2026-01-01T00:00:00Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

Feature: BR-UC-002 Create Media Buy

  @{SCENARIO_ID} @v31 @idempotency-key @post-s1 @ext-w @happy-path
  Scenario: v3.1 idempotency_key replay returns existing media buy without re-execution
    Given the tenant is configured for auto-approval
    When the Buyer Agent sends the create_media_buy request
    Then the response should include the previously created "media_buy_id"

  @{BOUNDARY_SCENARIO_ID} @v31 @idempotency-key @validation @post-f2 @ext-w
  Scenario Outline: v3.1 idempotency_key violates length/pattern constraints
    Given a create_media_buy request with idempotency_key "<value>"
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a validation error

    Examples:
      | value | violation |
      | AAAA  | maxLength 255 violated |
"""


def _write_source(directory: Path) -> Path:
    source = directory / FEATURE_FILENAME
    source.write_text(UPSTREAM_SOURCE)
    return source


def test_wholesale_compile_applies_supported_false_reconciliation(tmp_path):
    """The ``--all`` path cannot restore the stale replay assertion."""
    source = _write_source(tmp_path)

    _, output, _, scenario_ids = compile_bdd.compile_feature(source, {}, "test-sha", dry_run=True)

    assert scenario_ids == {SCENARIO_ID, BOUNDARY_SCENARIO_ID}
    assert "Advertised unsupported idempotency_key does not suppress create execution" in output
    assert 'the response should include a newly created "media_buy_id"' in output
    assert "exactly one new media buy should have been persisted" in output
    assert "include the previously created" not in output
    assert "| <256 chars>" in output
    assert "| AAAA" not in output


def test_merge_classifies_supported_false_reconciliation_as_local_overlay(tmp_path):
    """The ``--merge`` path applies the overlay without a semantic-merge gap."""
    source = _write_source(tmp_path)
    legacy = tmp_path / "legacy.feature"
    legacy.write_text(LEGACY_COMPILED)

    _, output, manifest_entries, scenario_ids, _, bucket_counts = compile_bdd.merge_feature(
        source,
        legacy,
        {},
        "test-sha",
    )

    assert scenario_ids == {SCENARIO_ID, BOUNDARY_SCENARIO_ID}
    assert manifest_entries == []
    assert bucket_counts == {"LOCAL-OVERLAY": 2}
    assert "Advertised unsupported idempotency_key does not suppress create execution" in output
    assert 'the response should include a newly created "media_buy_id"' in output
    assert "include the previously created" not in output
    assert "| <256 chars>" in output
    assert "| AAAA" not in output


def test_multiple_overlays_apply_by_generated_position_not_overlay_file_order(tmp_path, monkeypatch):
    """Reverse-authored overlays cannot invalidate precomputed block ranges."""
    feature_filename = "BR-UC-999-overlay-order.feature"
    overlay_dir = tmp_path / "overlays"
    overlay_dir.mkdir()
    monkeypatch.setattr(compile_bdd, "OVERLAY_DIR", overlay_dir)
    (overlay_dir / feature_filename).write_text(
        """\
Feature: Reverse overlay order

  @T-UC-999-second
  Scenario: overlaid second
    Then the second replacement survives

  @T-UC-999-first
  Scenario: overlaid first
    Then the first replacement survives
"""
    )
    generated = """\
# Generated from adcp-req @ test on 2026-01-01T00:00:00Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: Generated order

  @T-UC-999-first
  Scenario: stale first
    Then stale first step

  @T-UC-999-second
  Scenario: stale second
    Then stale second step
"""

    output = compile_bdd._apply_scenario_overlays(feature_filename, generated)

    assert "stale first" not in output
    assert "stale second" not in output
    assert "the first replacement survives" in output
    assert "the second replacement survives" in output
    assert output.index("overlaid first") < output.index("overlaid second")
