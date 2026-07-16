"""Unit pins for .claude/scripts BDD audit tooling (#1664 / #1665).

These scripts are agent meta-tooling, not production. Unit coverage is the
right bar: each correctness fix in the PR must fail the suite if reverted.
"""

from __future__ import annotations

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
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bdd_full_audit():
    return _load("bdd_full_audit")


@pytest.fixture(scope="module")
def salvage_audit():
    return _load("salvage_audit_output")


@pytest.fixture(scope="module")
def audit_xfails():
    return _load("audit_xfails")


class TestClassifyXpass:
    """Pin GRADUATE vs PARTIAL_XPASS bucketing (#1665 review)."""

    def _entry(self, bdd_full_audit, nodeid: str, outcome: str = "xpassed"):
        return bdd_full_audit.TestEntry(nodeid=nodeid, outcome=outcome)

    def test_all_four_transports_graduate(self, bdd_full_audit) -> None:
        all_entries = [
            self._entry(bdd_full_audit, f"tests/bdd/test_uc004.py::test_s[{t}]") for t in ("impl", "a2a", "mcp", "rest")
        ]
        bucket, cat, detail = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert bucket == "FIX_NOW"
        assert cat == "GRADUATE"
        assert "All transports pass" in detail

    def test_strict_subset_is_partial_xpass(self, bdd_full_audit) -> None:
        all_entries = [
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[impl]"),
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[a2a]"),
        ]
        bucket, cat, detail = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert bucket == "FIX_NOW"
        assert cat == "PARTIAL_XPASS"
        assert "missing" in detail

    def test_generate_work_items_splits_graduate_and_partial(self, bdd_full_audit) -> None:
        graduate = [
            self._entry(bdd_full_audit, f"tests/bdd/test_uc004.py::test_full[{t}]")
            for t in ("impl", "a2a", "mcp", "rest")
        ]
        partial = [
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_part[impl]"),
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_part[mcp]"),
        ]
        xpassed = graduate + partial
        items = bdd_full_audit.generate_work_items(
            failed=[],
            xfailed=[],
            xpassed=xpassed,
            inspector_flags=[],
            tag_reasons={},
            strict_tags=set(),
            all_entries=xpassed,
        )
        cats = {i.category for i in items}
        assert cats == {"GRADUATE", "PARTIAL_XPASS"}
        assert len(items) == 2


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


class TestPrematureXfailMatch:
    """Pin identifier-boundary matching for PREMATURE_XFAIL."""

    def test_substring_name_does_not_false_positive(self, audit_xfails) -> None:
        entry = audit_xfails.classify_xfail(
            {
                "nodeid": "t::s[impl]",
                "wasxfail": "check_xy blew up",
                "keywords": [],
                "call": {"longrepr": "in check_xy"},
            },
            {},
            {"check_x"},
            set(),
            {},
        )
        assert entry.category != "PREMATURE_XFAIL"

    def test_identifier_boundary_matches(self, audit_xfails) -> None:
        entry = audit_xfails.classify_xfail(
            {
                "nodeid": "t::s[impl]",
                "wasxfail": "then_premature not ready",
                "keywords": [],
                "call": {"longrepr": "File steps.py, in then_premature\n  pytest.xfail"},
            },
            {},
            {"then_premature"},
            set(),
            {},
        )
        assert entry.category == "PREMATURE_XFAIL"

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
        assert "then_premature" in premature
        # Leading non-string Constant must NOT be skipped as a docstring, so
        # the first meaningful stmt is `0` → not premature.
        assert "then_with_int_expr" not in premature


class TestReportIteratesFixNowDict:
    """Pin generate_report uses FIX_NOW keys (no parallel hardcoded list)."""

    def test_partial_xpass_section_rendered_from_dict(self, bdd_full_audit) -> None:
        item = bdd_full_audit.WorkItem(
            title="Partial xpass (gaps remain): UC-004",
            bucket="FIX_NOW",
            category="PARTIAL_XPASS",
            uc="UC-004",
            test_count=2,
            details="Passes: ['a2a'], missing: ['impl', 'mcp', 'rest']",
        )
        report = bdd_full_audit.generate_report(
            [item],
            summary={"passed": 0, "failed": 0, "xfailed": 0, "xpassed": 2},
            output_path=None,
        )
        assert "### PARTIAL_XPASS" in report
        assert "PARTIAL_XPASS" in bdd_full_audit.FIX_NOW
