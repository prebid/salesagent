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

    def test_e2e_rest_recognizes_e2e_rest(self, bdd_audit_common) -> None:
        nodeid = "tests/bdd/test_uc004.py::test_s[e2e_rest]"
        assert bdd_audit_common.extract_transport(nodeid) == "e2e_rest"
        assert bdd_audit_common.extract_scenario_base(nodeid) == "tests/bdd/test_uc004.py::test_s"

    def test_extract_longrepr_e_line(self, bdd_audit_common) -> None:
        longrepr = "E   AssertionError: boom\n    other"
        assert bdd_audit_common.extract_longrepr_e_line(longrepr) == "AssertionError: boom"
        assert bdd_audit_common.extract_longrepr_e_line("no error") == ""

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
        result = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert result.bucket == "FIX_NOW"
        assert result.category == "GRADUATE"
        assert result.present_count == 3
        assert "All 3 present transports pass" in result.detail

    def test_two_transport_uc_graduates_without_rest(self, bdd_full_audit) -> None:
        """UC-019 style (a2a+mcp only) must not demand rest."""
        all_entries = [self._entry(bdd_full_audit, f"tests/bdd/test_uc019.py::test_s[{t}]") for t in ("a2a", "mcp")]
        result = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert result.category == "GRADUATE"
        assert result.present_count == 2
        assert "All 2 present transports pass" in result.detail

    def test_single_transport_needs_confirmation(self, bdd_full_audit) -> None:
        """Single-/e2e_rest-only present sets must not auto-graduate."""
        all_entries = [self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[e2e_rest]")]
        result = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert result.category == "GRADUATE_CONFIRM"
        assert result.present_count == 1
        assert "needs confirmation" in result.detail

    def test_single_non_e2e_transport_needs_confirmation(self, bdd_full_audit) -> None:
        """Lone a2a must hit present_count==1 even when not e2e_rest.

        e2e_rest-only cases trip both disjuncts; this pin makes
        ``present_count == 1`` load-bearing on its own.
        """
        all_entries = [self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[a2a]")]
        result = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert result.category == "GRADUATE_CONFIRM"
        assert result.present_count == 1
        assert "needs confirmation" in result.detail
        assert "a2a" in result.detail

    def test_mixed_outline_examples_same_transport_not_graduate(self, bdd_full_audit) -> None:
        """Outline rows: failing first + passing last must not graduate.

        Last-wins would keep xpassed and falsely graduate; worst-outcome must
        stay PARTIAL. Passing-last is the only order that reddens on revert.
        """
        all_entries = [
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_o[e2e_rest-ex1]", "xfailed"),
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_o[e2e_rest-ex2]", "xpassed"),
        ]
        result = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert result.category == "PARTIAL_XPASS"

    def test_strict_subset_is_partial_xpass(self, bdd_full_audit) -> None:
        all_entries = [
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[a2a]", "xpassed"),
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[mcp]", "xfailed"),
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[rest]", "xfailed"),
        ]
        result = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert result.bucket == "FIX_NOW"
        assert result.category == "PARTIAL_XPASS"
        assert "missing" in result.detail
        assert "mcp" in result.detail

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

    def test_generate_work_items_confirm_title_for_e2e_rest_only(self, bdd_full_audit) -> None:
        """GRADUATE_CONFIRM work-item title must say needs confirmation."""
        all_entries = [self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[e2e_rest]")]
        items = bdd_full_audit.generate_work_items(
            failed=[],
            xfailed=[],
            xpassed=all_entries,
            inspector_flags=[],
            tag_reasons={},
            strict_tags=set(),
            all_entries=all_entries,
        )
        assert len(items) == 1
        item = items[0]
        assert item.category == "GRADUATE_CONFIRM"
        assert "needs confirmation" in item.title


class TestClassifyXpassedAudit:
    """audit_xfails.classify_xpassed uses the same present-transport rule."""

    def test_three_transport_xpass_is_stale_not_partial(self, audit_xfails) -> None:
        all_tests = [
            {"nodeid": f"tests/bdd/test_uc004.py::test_s[{t}]", "outcome": "xpassed"} for t in ("a2a", "mcp", "rest")
        ]
        buckets = audit_xfails.classify_xpassed(all_tests)
        assert len(buckets.graduate) == 1
        assert buckets.confirm == set()
        assert buckets.partial_passing == {}
        assert buckets.partial_missing == {}

    def test_e2e_rest_only_needs_confirmation_not_stale(self, audit_xfails) -> None:
        """Mirror bdd_full_audit GRADUATE_CONFIRM — lone e2e_rest must not be STALE."""
        base = "tests/bdd/test_uc004.py::test_s"
        all_tests = [{"nodeid": f"{base}[e2e_rest]", "outcome": "xpassed"}]
        buckets = audit_xfails.classify_xpassed(all_tests)
        assert buckets.graduate == set()
        assert buckets.confirm == {base}
        assert buckets.partial_passing == {}
        assert buckets.partial_missing == {}

    def test_single_non_e2e_transport_needs_confirmation_not_stale(self, audit_xfails) -> None:
        """Lone a2a must confirm via present_count==1 (not the e2e_rest disjunct)."""
        base = "tests/bdd/test_uc004.py::test_s"
        all_tests = [{"nodeid": f"{base}[a2a]", "outcome": "xpassed"}]
        buckets = audit_xfails.classify_xpassed(all_tests)
        assert buckets.graduate == set()
        assert buckets.confirm == {base}
        assert buckets.partial_passing == {}
        assert buckets.partial_missing == {}

    def test_strict_subset_is_partial(self, audit_xfails) -> None:
        """Mirror test_strict_subset_is_partial_xpass — pin the partial branch."""
        base = "tests/bdd/test_uc004.py::test_s"
        all_tests = [
            {"nodeid": f"{base}[a2a]", "outcome": "xpassed"},
            {"nodeid": f"{base}[mcp]", "outcome": "xfailed"},
            {"nodeid": f"{base}[rest]", "outcome": "xfailed"},
        ]
        buckets = audit_xfails.classify_xpassed(all_tests)
        assert buckets.graduate == set()
        assert buckets.confirm == set()
        assert buckets.partial_passing == {base: {"a2a"}}
        assert buckets.partial_missing == {base: {"mcp", "rest"}}

    def test_mixed_outline_examples_same_transport_do_not_graduate(self, audit_xfails) -> None:
        """Last-wins would graduate; worst-outcome must keep graduate empty.

        Failing example first + passing last is the only order that reddens
        when the aggregate reverts to last-wins.
        """
        base = "tests/bdd/test_uc004.py::test_outline"
        all_tests = [
            {"nodeid": f"{base}[e2e_rest-ex1]", "outcome": "xfailed"},
            {"nodeid": f"{base}[e2e_rest-ex2]", "outcome": "xpassed"},
        ]
        buckets = audit_xfails.classify_xpassed(all_tests)
        assert buckets.graduate == set()
        assert buckets.confirm == set()
        assert buckets.partial_passing == {}  # no passing transport after worst-outcome aggregate
        assert buckets.partial_missing == {}


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

    def test_intra_call_duplicate_entries_write_once(self, salvage_audit, tmp_path: Path) -> None:
        """_append_if_new must dedupe identical entries within a single write call."""
        store = tmp_path / "store.jsonl"
        parsed = {
            "pass1": [
                {"index": 1, "func_name": "then_a", "verdict": "FLAG"},
                {"index": 2, "func_name": "then_a", "verdict": "FLAG"},
            ],
            "pass2": [],
            "pass1_total": 2,
            "pass2_total": 0,
            "pass2_crashed_at": None,
        }
        salvage_audit.write_to_store(parsed, store, None)
        records = [json.loads(line) for line in store.read_text().splitlines() if line.strip()]
        triage = [r for r in records if r["kind"] == "triage"]
        assert len(triage) == 1

    def test_step_index_normalizes_real_lines_and_dedupes(self, salvage_audit, tmp_path: Path) -> None:
        """step_index path: normalize real file/line; identical dedupe; distinct lines survive."""
        store = tmp_path / "store.jsonl"
        step_index = tmp_path / "steps.jsonl"
        step_index.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "step": {
                                "file_path": "tests/bdd/steps/a.py",
                                "line_number": "42",
                                "step_type": "then",
                                "step_text": "a",
                                "function_name": "then_a",
                                "source_text": "pass",
                            }
                        }
                    ),
                    json.dumps(
                        {
                            "step": {
                                "file_path": "tests/bdd/steps/b.py",
                                "line_number": 99,
                                "step_type": "then",
                                "step_text": "b",
                                "function_name": "then_b",
                                "source_text": "pass",
                            }
                        }
                    ),
                ]
            )
            + "\n"
        )
        parsed = {
            "pass1": [
                {"index": 1, "func_name": "then_a", "verdict": "FLAG"},
                {"index": 2, "func_name": "then_a", "verdict": "FLAG"},
                {"index": 3, "func_name": "then_b", "verdict": "PASS"},
            ],
            "pass2": [],
            "pass1_total": 3,
            "pass2_total": 0,
            "pass2_crashed_at": 0,
        }
        salvage_audit.write_to_store(parsed, store, step_index)
        records = [json.loads(line) for line in store.read_text().splitlines() if line.strip()]
        triage = [r for r in records if r["kind"] == "triage"]
        assert len(triage) == 2
        by_name = {r["step"]["function_name"]: r["step"] for r in triage}
        assert by_name["then_a"]["file_path"] == "tests/bdd/steps/a.py"
        assert by_name["then_a"]["line_number"] == 42
        assert by_name["then_b"]["file_path"] == "tests/bdd/steps/b.py"
        assert by_name["then_b"]["line_number"] == 99
        assert by_name["then_a"]["file_path"] != "unknown"
        assert by_name["then_a"]["line_number"] != 0


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
        test = {
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
        }
        entry = audit_xfails.classify_xfail(test, {}, premature)
        assert entry.category != "PREMATURE_XFAIL"
        warnings = audit_xfails.crash_line_drift_warnings(test, premature)
        assert len(warnings) == 1
        assert "line-drift/stale-json?" in warnings[0]

    def test_in_range_crash_in_known_step_file_no_drift_warning(self, audit_xfails, tmp_path: Path) -> None:
        """Negative pin: in-range crash in a known premature step file → no drift warning."""
        source = self._premature_source()
        steps_file = tmp_path / "steps.py"
        steps_file.write_text(source)
        premature = audit_xfails.find_premature_xfails(tmp_path)
        assert len(premature) == 1
        step = premature[0]
        mid = (step.lineno + step.end_lineno) // 2
        assert step.lineno <= mid <= step.end_lineno
        test = {
            "nodeid": "t::s[a2a]",
            "wasxfail": "",
            "keywords": [],
            "setup": {"outcome": "passed"},
            "call": {
                "outcome": "skipped",
                "crash": {
                    "path": str(steps_file.resolve()),
                    "lineno": mid,
                    "message": "_pytest.outcomes.XFailed: not ready",
                },
            },
        }
        assert audit_xfails.crash_line_drift_warnings(test, premature) == []

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


class TestAuditXfailsReportDry:
    """Pin audit_xfails.generate_report iterates category_desc (no twin list)."""

    def test_category_table_includes_stale_confirm_from_desc(self, audit_xfails) -> None:
        report = audit_xfails.AuditReport(total_xfailed=0, total_xpassed=1)
        entry = audit_xfails.XfailEntry(
            nodeid="tests/bdd/test_uc004.py::test_s[a2a]",
            scenario_base="tests/bdd/test_uc004.py::test_s",
            transport="a2a",
            category="STALE_CONFIRM",
            reason="needs confirmation",
        )
        report.xpassed_entries.append(entry)
        text = audit_xfails.generate_report(report)
        assert "| STALE_CONFIRM |" in text
        assert "### Graduation needs confirmation" in text
        assert "- test_s" in text


class TestGradeResultNamedFields:
    """grade_base returns GradeResult — callers must read fields by name."""

    def test_grade_result_fields(self, bdd_audit_common) -> None:
        grade = bdd_audit_common.grade_base(
            "tests/bdd/test_uc004.py::test_s",
            [("tests/bdd/test_uc004.py::test_s[a2a]", "xpassed")],
        )
        assert grade.graduates is True
        assert grade.needs_confirmation is True
        assert grade.present_count == 1
        assert grade.passing == {"a2a"}
        assert grade.missing == set()

    def test_transport_coverage_named_fields(self, bdd_audit_common) -> None:
        coverage = bdd_audit_common.transport_coverage({"a2a": "xpassed", "mcp": "xfailed"})
        assert coverage.graduates is False
        assert coverage.passing == {"a2a"}
        assert coverage.missing == {"mcp"}

    def test_short_base(self, bdd_audit_common) -> None:
        assert bdd_audit_common.short_base("tests/bdd/test_uc004.py::test_s") == "test_s"
        assert bdd_audit_common.short_base("bare") == "bare"
