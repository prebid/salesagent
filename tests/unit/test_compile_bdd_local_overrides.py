"""Regression tests for Sales Agent-owned BDD scenario overrides."""

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_compiler():
    script = Path(__file__).parents[2] / "scripts" / "compile_bdd.py"
    spec = importlib.util.spec_from_file_location("compile_bdd_local_override_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_local_override_replaces_and_verifies_compiled_scenario(tmp_path, monkeypatch):
    """A private-upstream override remains reproducible from Sales Agent alone."""
    compiler = _load_compiler()
    output_dir = tmp_path / "features"
    output_dir.mkdir()
    feature_path = output_dir / "BR-UC-999-local.feature"
    feature_path.write_text(
        "# Generated from adcp-req @ deadbeef on now (merge mode)\n"
        "# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge\n\n"
        "Feature: Local\n\n"
        "  @T-UC-999-main\n"
        "  Scenario: Stale upstream contract\n"
        "    Given stale input\n"
        "    Then stale result\n"
    )
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text(
        "schema_version: 1\n"
        "overrides:\n"
        "  - feature: BR-UC-999-local.feature\n"
        "    scenario_id: T-UC-999-main\n"
        "    authority: local authority\n"
        "    reason: upstream is unavailable\n"
        "    gherkin: |\n"
        "      @T-UC-999-main\n"
        "      Scenario: Local contract\n"
        "        Given current input\n"
        "        Then current result\n"
    )
    monkeypatch.setattr(compiler, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(compiler, "LOCAL_OVERRIDES_PATH", overrides)

    compiler.apply_local_overrides_to_compiled_features()

    assert "Scenario: Local contract" in feature_path.read_text()
    assert compiler.verify_local_overrides()

    feature_path.write_text(feature_path.read_text().replace("current result", "drifted result"))
    assert not compiler.verify_local_overrides()

    feature_path.write_text(
        feature_path.read_text().replace("drifted result", "current result") + feature_path.read_text()
    )
    assert not compiler.verify_local_overrides()


def test_local_override_preserves_unrelated_compiled_text(tmp_path, monkeypatch):
    """Targeted replacement cannot reformat comments or neighboring scenarios."""
    compiler = _load_compiler()
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text(
        "schema_version: 1\noverrides:\n"
        "  - feature: BR-UC-999-local.feature\n    scenario_id: T-UC-999-main\n"
        "    authority: local\n    reason: local\n    gherkin: |\n"
        "      @T-UC-999-main\n      Scenario: Replacement\n        Then new\n"
    )
    monkeypatch.setattr(compiler, "LOCAL_OVERRIDES_PATH", overrides)
    original = "# preserve exactly\n\nFeature: Local\n\n  @T-UC-999-main\n  Scenario: Old\n    Then old\n\n  # neighbor comment\n  @T-UC-999-next\n  Scenario: Next\n    Then unchanged\n"
    rendered = compiler._apply_local_overrides_to_text("BR-UC-999-local.feature", original)
    assert "# preserve exactly\n\nFeature: Local\n\n" in rendered
    assert "  # neighbor comment\n  @T-UC-999-next\n  Scenario: Next\n    Then unchanged\n" in rendered


def test_local_override_does_not_match_a_prefixed_scenario_id(tmp_path, monkeypatch):
    """Exact tag matching prevents a local override corrupting a sibling scenario."""
    compiler = _load_compiler()
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text(
        "schema_version: 1\noverrides:\n"
        "  - feature: BR-UC-999-local.feature\n    scenario_id: T-UC-999-main\n"
        "    authority: local\n    reason: local\n    gherkin: |\n"
        "      @T-UC-999-main\n      Scenario: Replacement\n        Then new\n"
    )
    monkeypatch.setattr(compiler, "LOCAL_OVERRIDES_PATH", overrides)
    original = (
        "Feature: Local\n\n  @T-UC-999-main\n  Scenario: Old\n    Then old\n\n"
        "  @T-UC-999-main-sibling\n  Scenario: Sibling\n    Then untouched\n"
    )
    rendered = compiler._apply_local_overrides_to_text("BR-UC-999-local.feature", original)
    assert "Scenario: Replacement" in rendered
    assert "@T-UC-999-main-sibling\n  Scenario: Sibling\n    Then untouched" in rendered


def test_local_override_rejects_a_duplicate_target_tag(tmp_path, monkeypatch):
    """Applying an override must not leave a second stale target behind."""
    compiler = _load_compiler()
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text(
        "schema_version: 1\noverrides:\n"
        "  - feature: BR-UC-999-local.feature\n    scenario_id: T-UC-999-main\n"
        "    authority: local\n    reason: local\n    gherkin: |\n"
        "      @T-UC-999-main\n      Scenario: Replacement\n        Then new\n"
    )
    monkeypatch.setattr(compiler, "LOCAL_OVERRIDES_PATH", overrides)
    original = (
        "Feature: Local\n\n  @T-UC-999-main\n  Scenario: First\n    Then old\n\n"
        "  @T-UC-999-main\n  Scenario: Second\n    Then stale\n"
    )

    with pytest.raises(ValueError, match="duplicated.*T-UC-999-main"):
        compiler._apply_local_overrides_to_text("BR-UC-999-local.feature", original)


def test_normal_compilation_applies_local_override(tmp_path, monkeypatch):
    """The normal --uc/--all rendering path cannot overwrite a local decision."""
    compiler = _load_compiler()
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text(
        "schema_version: 1\n"
        "overrides:\n"
        "  - feature: BR-UC-999-local.feature\n"
        "    scenario_id: T-UC-999-main\n"
        "    authority: local authority\n"
        "    reason: upstream is unavailable\n"
        "    gherkin: |\n"
        "      @T-UC-999-main\n"
        "      Scenario: Local contract\n"
        "        Given current input\n"
        "        Then current result\n"
    )
    source = tmp_path / "BR-UC-999-local.feature"
    source.write_text(
        "Feature: Local\n\n"
        "# @contextgit id=T-UC-999-main\n"
        "Scenario: Stale upstream contract\n"
        "  Given stale input\n"
        "  Then stale result\n"
    )
    monkeypatch.setattr(compiler, "LOCAL_OVERRIDES_PATH", overrides)

    _uc_key, rendered, _new, _ids = compiler.compile_feature(source, {"mappings": {}}, "deadbeef", dry_run=True)

    assert "Scenario: Local contract" in rendered
    assert "stale result" not in rendered


def test_repository_local_overrides_are_applied():
    """The checked-in override manifest and compiled artifacts stay synchronized."""
    compiler = _load_compiler()
    assert compiler.verify_local_overrides()


def test_normal_verify_reaches_local_override_verification(tmp_path, monkeypatch):
    """The normal --verify path honors the renderer contract and checks local overrides."""
    compiler = _load_compiler()
    source = tmp_path / "BR-UC-999-local.feature"
    source.write_text(
        "Feature: Local\n\n"
        "# @contextgit id=T-UC-999-main\n"
        "Scenario: Current contract\n"
        "  Given current input\n"
        "  Then current result\n"
    )
    output_dir = tmp_path / "compiled"
    output_dir.mkdir()
    traceability = {"source": {"commit": "deadbeef"}, "mappings": {}}
    feature = compiler.parse_feature_file(source.read_text())
    expected, _new, _ids = compiler._render_feature(feature, traceability, "UC-999", source.name, "deadbeef")
    (output_dir / source.name).write_text(expected)
    local_verify_called = False

    def verify_local_overrides() -> bool:
        nonlocal local_verify_called
        local_verify_called = True
        return True

    monkeypatch.setattr(compiler, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(compiler, "_get_commit_sha", lambda _path: "deadbeef")
    monkeypatch.setattr(compiler, "_find_feature_files", lambda _path: [source])
    monkeypatch.setattr(compiler, "_load_traceability", lambda _path: traceability)
    monkeypatch.setattr(compiler, "verify_local_overrides", verify_local_overrides)

    assert compiler.verify_features(tmp_path)
    assert local_verify_called
