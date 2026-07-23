"""Unit pins for .claude/scripts BDD audit tooling (#1664 / #1665).

These scripts are agent meta-tooling, not production. Unit coverage is the
right bar: each correctness fix in the PR must fail the suite if reverted.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "scripts"


def _load(name: str):
    path = SCRIPTS / f"{name}.py"
    if not path.exists():
        pytest.fail(f"missing script: {path}")
    # Shared helpers live beside the scripts; ensure import resolves under importlib.
    scripts_dir = str(SCRIPTS)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bdd_audit_common():
    return _load("bdd_audit_common")


@pytest.fixture(scope="module")
def bdd_full_audit():
    return _load("bdd_full_audit")


@pytest.fixture(scope="module")
def salvage_audit():
    return _load("salvage_audit_output")


@pytest.fixture(scope="module")
def audit_xfails():
    return _load("audit_xfails")


class TestTransportHelpers:
    """Shared extract_* must agree and recognize e2e_rest (#1665 review)."""

    def test_e2e_rest_not_truncated_to_rest(self, bdd_audit_common) -> None:
        nodeid = "tests/bdd/test_uc004.py::test_s[e2e_rest]"
        assert bdd_audit_common.extract_transport(nodeid) == "e2e_rest"
        assert bdd_audit_common.extract_scenario_base(nodeid) == "tests/bdd/test_uc004.py::test_s"

    def test_wire_transports(self, bdd_audit_common) -> None:
        for t in ("a2a", "mcp", "rest"):
            nodeid = f"tests/bdd/test_uc004.py::test_s[{t}]"
            assert bdd_audit_common.extract_transport(nodeid) == t

    def test_extract_uc_from_nodeid_and_path(self, bdd_audit_common) -> None:
        assert bdd_audit_common.extract_uc("tests/bdd/test_uc004.py::test_s[a2a]") == "UC-004"
        assert bdd_audit_common.extract_uc("tests/bdd/steps/uc019/then_steps.py") == "UC-019"
        assert bdd_audit_common.extract_uc("no-uc-here.py") == "GENERIC"

    def test_cross_reference_sees_e2e_rest(self) -> None:
        """cross_reference_audit must use shared extract_transport (not old regex)."""
        xref = _load("cross_reference_audit")
        assert xref.extract_transport("tests/bdd/test_uc004.py::test_s[e2e_rest]") == "e2e_rest"


class TestClassifyXpass:
    """Pin GRADUATE vs PARTIAL_XPASS on transports *present for the base*."""

    def _entry(self, bdd_full_audit, nodeid: str, outcome: str = "xpassed"):
        return bdd_full_audit.TestEntry(nodeid=nodeid, outcome=outcome)

    def test_all_present_wire_transports_graduate(self, bdd_full_audit) -> None:
        """Real post-#1417 three-transport UC → GRADUATE (no impl required)."""
        all_entries = [
            self._entry(bdd_full_audit, f"tests/bdd/test_uc004.py::test_s[{t}]") for t in ("a2a", "mcp", "rest")
        ]
        bucket, cat, detail = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert bucket == "FIX_NOW"
        assert cat == "GRADUATE"
        assert "All 3 present transports pass" in detail

    def test_two_transport_uc_graduates_without_rest(self, bdd_full_audit) -> None:
        """UC-019 style (a2a+mcp only) must not demand rest."""
        all_entries = [self._entry(bdd_full_audit, f"tests/bdd/test_uc019.py::test_s[{t}]") for t in ("a2a", "mcp")]
        _, cat, detail = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert cat == "GRADUATE"
        assert "All 2 present transports pass" in detail

    def test_single_transport_needs_confirmation(self, bdd_full_audit) -> None:
        """Single-/e2e_rest-only present sets must not auto-graduate."""
        all_entries = [self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[e2e_rest]")]
        _, cat, detail = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert cat == "GRADUATE_CONFIRM"
        assert "needs confirmation" in detail

    def test_mixed_outline_examples_same_transport_not_graduate(self, bdd_full_audit) -> None:
        """Outline rows for one transport: xpassed+xfailed must not graduate."""
        all_entries = [
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_o[e2e_rest-ex1]", "xpassed"),
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_o[e2e_rest-ex2]", "xfailed"),
        ]
        _, cat, _ = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert cat == "PARTIAL_XPASS"

    def test_strict_subset_is_partial_xpass(self, bdd_full_audit) -> None:
        all_entries = [
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[a2a]", "xpassed"),
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[mcp]", "xfailed"),
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[rest]", "xfailed"),
        ]
        bucket, cat, detail = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert bucket == "FIX_NOW"
        assert cat == "PARTIAL_XPASS"
        assert "missing" in detail
        assert "mcp" in detail

    def test_generate_work_items_splits_graduate_and_partial(self, bdd_full_audit) -> None:
        graduate = [
            self._entry(bdd_full_audit, f"tests/bdd/test_uc004.py::test_full[{t}]") for t in ("a2a", "mcp", "rest")
        ]
        partial = [
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_part[a2a]", "xpassed"),
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_part[mcp]", "xfailed"),
        ]
        all_entries = graduate + partial
        items = bdd_full_audit.generate_work_items(
            failed=[],
            xfailed=[],
            xpassed=[e for e in all_entries if e.outcome == "xpassed"],
            inspector_flags=[],
            tag_reasons={},
            strict_tags=set(),
            all_entries=all_entries,
        )
        cats = {i.category for i in items}
        assert cats == {"GRADUATE", "PARTIAL_XPASS"}
        assert len(items) == 2
        graduate_item = next(i for i in items if i.category == "GRADUATE")
        assert graduate_item.title.startswith("Graduate (all 3 present):")
        partial_item = next(i for i in items if i.category == "PARTIAL_XPASS")
        assert "gaps remain" in partial_item.title


class TestClassifyXpassedAudit:
    """audit_xfails.classify_xpassed uses the same present-transport rule."""

    def test_three_transport_xpass_is_stale_not_partial(self, audit_xfails) -> None:
        all_tests = [
            {"nodeid": f"tests/bdd/test_uc004.py::test_s[{t}]", "outcome": "xpassed"} for t in ("a2a", "mcp", "rest")
        ]
        graduate, partial = audit_xfails.classify_xpassed(all_tests)
        assert len(graduate) == 1
        assert partial == {}

    def test_strict_subset_is_partial(self, audit_xfails) -> None:
        """Mirror test_strict_subset_is_partial_xpass — pin the partial branch."""
        base = "tests/bdd/test_uc004.py::test_s"
        all_tests = [
            {"nodeid": f"{base}[a2a]", "outcome": "xpassed"},
            {"nodeid": f"{base}[mcp]", "outcome": "xfailed"},
            {"nodeid": f"{base}[rest]", "outcome": "xfailed"},
        ]
        graduate, partial = audit_xfails.classify_xpassed(all_tests)
        assert graduate == set()
        assert partial == {base: {"a2a"}}

    def test_mixed_outline_examples_same_transport_do_not_graduate(self, audit_xfails) -> None:
        """Last-wins would graduate; worst-outcome must keep graduate empty."""
        base = "tests/bdd/test_uc004.py::test_outline"
        all_tests = [
            {"nodeid": f"{base}[e2e_rest-ex1]", "outcome": "xpassed"},
            {"nodeid": f"{base}[e2e_rest-ex2]", "outcome": "xfailed"},
        ]
        graduate, partial = audit_xfails.classify_xpassed(all_tests)
        assert graduate == set()
        assert partial == {}  # no passing transport after worst-outcome aggregate


class TestSalvageDedupe:
    """Pin kind-scoped deep-trace dedup (#1665 review)."""

    def _parsed(self):
        return {
            "pass1": [{"index": 1, "func_name": "then_a", "verdict": "FLAG"}],
            "pass2": [{"index": 1, "func_name": "then_a", "severity": "WEAK"}],
            "pass1_total": 1,
            "pass2_total": 1,
            "pass2_crashed_at": 2,
        }

    def test_second_write_does_not_grow_deep_count(self, salvage_audit, tmp_path: Path) -> None:
        store = tmp_path / "store.jsonl"
        salvage_audit.write_to_store(self._parsed(), store, None)
        salvage_audit.write_to_store(self._parsed(), store, None)
        records = [json.loads(line) for line in store.read_text().splitlines() if line.strip()]
        deep = [r for r in records if r["kind"] == "deep"]
        triage = [r for r in records if r["kind"] == "triage"]
        assert len(deep) == 1
        assert len(triage) == 1

    def test_triage_and_deep_same_name_line_both_survive(self, salvage_audit, tmp_path: Path) -> None:
        store = tmp_path / "store.jsonl"
        salvage_audit.write_to_store(self._parsed(), store, None)
        records = [json.loads(line) for line in store.read_text().splitlines() if line.strip()]
        kinds = sorted(r["kind"] for r in records)
        assert kinds == ["deep", "triage"]
        assert all(r["step"]["function_name"] == "then_a" for r in records)
        assert all(r["step"]["line_number"] == 0 for r in records)


class TestPrematureXfailCrashMatch:
    """PREMATURE_XFAIL matches setup/call crash path+lineno → enclosing step."""

    def _premature_source(self) -> str:
        return textwrap.dedent(
            """
            from pytest_bdd import then
            import pytest

            @then("x")
            def then_premature():
                \"\"\"doc\"\"\"
                pytest.xfail("not ready")
            """
        )

    def _xfail_lineno(self, source: str) -> int:
        return next(
            n.lineno
            for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "xfail"
        )

    def test_crash_inside_premature_step_classifies(self, audit_xfails, tmp_path: Path) -> None:
        source = self._premature_source()
        steps_file = tmp_path / "steps.py"
        steps_file.write_text(source)
        premature = audit_xfails.find_premature_xfails(tmp_path)
        assert len(premature) == 1
        step = premature[0]
        assert step.name == "then_premature"

        # Real pytest.xfail() inside a step body is under call.crash (setup passed).
        xfail_lineno = self._xfail_lineno(source)
        entry = audit_xfails.classify_xfail(
            {
                "nodeid": "t::s[a2a]",
                "wasxfail": "",
                "keywords": [],
                "setup": {"outcome": "passed"},
                "call": {
                    "outcome": "skipped",
                    "crash": {
                        "path": str(steps_file.resolve()),
                        "lineno": xfail_lineno,
                        "message": "_pytest.outcomes.XFailed: not ready",
                    },
                    "longrepr": "E   _pytest.outcomes.XFailed: not ready",
                },
            },
            {},
            premature,
            set(),
            {},
        )
        assert entry.category == "PREMATURE_XFAIL"
        assert "then_premature" in entry.reason

    def test_crash_just_past_end_lineno_is_not_premature(self, audit_xfails, tmp_path: Path) -> None:
        """Boundary pin: crash at end_lineno+1 must not enclose the step."""
        source = self._premature_source()
        steps_file = tmp_path / "steps.py"
        steps_file.write_text(source)
        premature = audit_xfails.find_premature_xfails(tmp_path)
        assert len(premature) == 1
        step = premature[0]
        entry = audit_xfails.classify_xfail(
            {
                "nodeid": "t::s[a2a]",
                "wasxfail": "UC harness not wired",
                "keywords": [],
                "setup": {"outcome": "passed"},
                "call": {
                    "outcome": "skipped",
                    "crash": {
                        "path": str(steps_file.resolve()),
                        "lineno": step.end_lineno + 1,
                        "message": "_pytest.outcomes.XFailed: not ready",
                    },
                },
            },
            {},
            premature,
            set(),
            {},
        )
        assert entry.category != "PREMATURE_XFAIL"

    def test_crash_in_different_file_in_range_is_not_premature(self, audit_xfails, tmp_path: Path) -> None:
        """Path-equality must reject: in-range lineno in a *different* file.

        A far-out lineno alone would already fail the range check, so this
        pin puts the crash line inside the premature step's span while the
        crash path is another file — only ``resolved == step.path`` decides.
        """
        source = self._premature_source()
        steps_file = tmp_path / "steps.py"
        steps_file.write_text(source)
        premature = audit_xfails.find_premature_xfails(tmp_path)
        assert len(premature) == 1
        step = premature[0]
        other = tmp_path / "conftest.py"
        other.write_text("# decoy crash site\n")
        in_range = (step.lineno + step.end_lineno) // 2
        assert step.lineno <= in_range <= step.end_lineno
        entry = audit_xfails.classify_xfail(
            {
                "nodeid": "t::s[a2a]",
                "wasxfail": "UC harness not wired",
                "keywords": [],
                "setup": {
                    "outcome": "failed",
                    "crash": {
                        "path": str(other.resolve()),
                        "lineno": in_range,
                        "message": "_pytest.outcomes.XFailed: UC harness not wired",
                    },
                },
            },
            {},
            premature,
            set(),
            {},
        )
        assert entry.category != "PREMATURE_XFAIL"

    def test_find_premature_skips_only_string_docstrings(self, audit_xfails, tmp_path: Path) -> None:
        source = textwrap.dedent(
            """
            from pytest_bdd import then
            import pytest

            @then("x")
            def then_premature():
                \"\"\"doc\"\"\"
                pytest.xfail("not ready")

            @then("y")
            def then_with_int_expr():
                0
                pytest.xfail("unreachable if 0 counts as body")
            """
        )
        (tmp_path / "steps.py").write_text(source)
        premature = audit_xfails.find_premature_xfails(tmp_path)
        names = {p.name for p in premature}
        assert "then_premature" in names
        # Leading non-string Constant must NOT be skipped as a docstring, so
        # the first meaningful stmt is `0` → not premature.
        assert "then_with_int_expr" not in names


class TestReportIteratesFixNowDict:
    """Pin generate_report uses FIX_NOW keys (no parallel hardcoded list)."""

    def test_partial_xpass_section_rendered_from_dict(self, bdd_full_audit) -> None:
        item = bdd_full_audit.WorkItem(
            title="Partial xpass (gaps remain): UC-004",
            bucket="FIX_NOW",
            category="PARTIAL_XPASS",
            uc="UC-004",
            test_count=2,
            details="Passes: ['a2a'], missing: ['mcp', 'rest']",
        )
        report = bdd_full_audit.generate_report(
            [item],
            summary={"passed": 0, "failed": 0, "xfailed": 0, "xpassed": 2},
            output_path=None,
        )
        assert "### PARTIAL_XPASS" in report
        assert "PARTIAL_XPASS" in bdd_full_audit.FIX_NOW
