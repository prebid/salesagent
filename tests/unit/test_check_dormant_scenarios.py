"""Unit tests for scripts/check_dormant_scenarios.py (#1603).

The script's subprocess layer (git diff, pytest run) is environment-bound;
these tests pin the pure logic: xfail-line classification (dormant wiring
gaps vs documented spec gaps, transport params collapsed), the mapping from
touched paths to the BDD test modules that bind them, and the run-result guard
(mocked subprocess) that fails a broken pytest run instead of reporting a false
all-clear.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
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


class TestRunGuard:
    """The clean no-DB run is exit 0 (skips + xfails); any nonzero exit means the
    run broke (collection/import error, failed test), so the checker must surface
    it and fail -- never report a false 'no dormant scenarios'."""

    def _run_main(self, monkeypatch, returncode, stdout="", stderr=""):
        monkeypatch.setattr(
            cds,
            "run_without_db",
            lambda modules: subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr),
        )
        monkeypatch.setattr(
            sys, "argv", ["check_dormant_scenarios.py", "--paths", "tests/bdd/test_uc003_update_media_buy.py"]
        )
        return cds.main()

    def test_broken_run_surfaced_not_reported_clean(self, monkeypatch, capsys):
        rc = self._run_main(monkeypatch, returncode=1, stderr="ImportError: cannot import name 'ByGeoItem'")
        out = capsys.readouterr().out
        assert rc == 1, "a broken pytest run must fail the checker, not exit 0"
        assert "did not run cleanly" in out
        assert "ByGeoItem" in out
        assert "no dormant scenarios" not in out

    def test_collection_error_exit_2_also_surfaced(self, monkeypatch, capsys):
        rc = self._run_main(monkeypatch, returncode=2, stderr="ERROR collecting test_x.py")
        out = capsys.readouterr().out
        assert rc == 1
        assert "did not run cleanly (exit 2)" in out
        assert "no dormant scenarios" not in out

    def test_clean_run_still_classifies_dormant(self, monkeypatch, capsys):
        rc = self._run_main(monkeypatch, returncode=0, stdout=SAMPLE_OUTPUT)
        out = capsys.readouterr().out
        assert "DORMANT scenario" in out
        assert rc == 0  # informational (non-strict) reports but does not fail
