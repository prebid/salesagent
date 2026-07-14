"""Unit tests for scripts/check_dormant_scenarios.py (#1603).

The script's subprocess layer (git diff, pytest run) is environment-bound;
these tests pin the pure logic: xfail-line classification (dormant wiring
gaps vs documented spec gaps, transport params collapsed) and the mapping
from touched paths to the BDD test modules that bind them.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_dormant_scenarios.py"
_spec = importlib.util.spec_from_file_location("check_dormant_scenarios", _SCRIPT)
cds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cds)


SAMPLE_OUTPUT = """
XFAIL tests/bdd/test_uc026_package_media_buy.py::test_create_package_via_mcp[a2a] - No harness wired for None
XFAIL tests/bdd/test_uc026_package_media_buy.py::test_create_package_via_mcp[mcp] - No harness wired for None
XFAIL tests/bdd/test_uc003_update_media_buy.py::test_pause_campaign[a2a] - UC-003 harness not yet wired for non-extension scenarios
XFAIL tests/bdd/test_uc003_update_media_buy.py::test_new_step[mcp] - Step definition not found: Given "something"
XFAIL tests/bdd/test_uc003_update_media_buy.py::test_main_flow[a2a] - implementation_date, budget, sandbox not populated in update response — spec-production gap
XFAIL tests/bdd/test_uc003_update_media_buy.py::test_outline[a2a-account_unsupported-operator-["operator"]-absent] - No harness wired for None
XPASS tests/bdd/test_uc002_create_media_buy.py::test_replay[mcp] - graduated
1 passed, 4 xfailed in 2.10s
"""


class TestClassify:
    def test_splits_dormant_from_documented(self):
        dormant, documented = cds.classify(SAMPLE_OUTPUT)
        dormant_scenarios = {s for names in dormant.values() for s in names}
        assert "test_create_package_via_mcp" in dormant_scenarios
        assert "test_pause_campaign" in dormant_scenarios
        assert "test_new_step" in dormant_scenarios
        # documented spec gap is NOT dormant
        documented_scenarios = {s for names in documented.values() for s in names}
        assert "test_main_flow" in documented_scenarios
        assert "test_main_flow" not in dormant_scenarios

    def test_transport_params_collapse_to_one_scenario(self):
        dormant, _ = cds.classify(SAMPLE_OUTPUT)
        assert dormant["No harness wired for None"].issuperset({"test_create_package_via_mcp"})
        # two transports, one scenario
        count = sum(1 for s in dormant["No harness wired for None"] if s == "test_create_package_via_mcp")
        assert count == 1

    def test_nested_brackets_in_outline_params_collapse(self):
        dormant, _ = cds.classify(SAMPLE_OUTPUT)
        all_names = {s for names in dormant.values() for s in names}
        assert "test_outline" in all_names
        assert not any("[" in s for s in all_names)

    def test_xpass_lines_ignored(self):
        dormant, documented = cds.classify(SAMPLE_OUTPUT)
        every = {s for names in (*dormant.values(), *documented.values()) for s in names}
        assert "test_replay" not in every


class TestMapPaths:
    def test_domain_step_module_maps_to_uc_test_module(self):
        modules, notes = cds.map_paths_to_modules(["tests/bdd/steps/domain/uc026_package_media_buy.py"])
        assert any(m.name == "test_uc026_package_media_buy.py" for m in modules)
        assert notes == []

    def test_test_module_maps_to_itself(self):
        modules, _ = cds.map_paths_to_modules(["tests/bdd/test_uc003_update_media_buy.py"])
        assert [m.name for m in modules] == ["test_uc003_update_media_buy.py"]

    def test_feature_file_maps_to_binding_module(self):
        modules, _ = cds.map_paths_to_modules(["tests/bdd/features/BR-UC-003-update-media-buy.feature"])
        assert any(m.name == "test_uc003_update_media_buy.py" for m in modules)

    def test_conftest_touch_yields_note_not_full_sweep(self):
        modules, notes = cds.map_paths_to_modules(["tests/bdd/conftest.py"])
        assert modules == set()
        assert any("--all" in n for n in notes)

    def test_bdd_relevance_filter(self):
        assert cds.is_bdd_relevant("tests/bdd/steps/domain/uc003_update_media_buy.py")
        assert cds.is_bdd_relevant("tests/harness/media_buy_dual.py")
        assert not cds.is_bdd_relevant("src/core/tools/media_buy_update.py")
