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

pytestmark = pytest.mark.infra

SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "scripts"
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFTEST = REPO_ROOT / "tests" / "bdd" / "conftest.py"
CAPTURED_SLICE = REPO_ROOT / "tests" / "fixtures" / "bdd_audit" / "captured_slice.json"

# Canonical present-transport set for xpass fixtures (must not drift from
# bdd_audit_common / conftest parametrize ids).
_PRESENT_TRANSPORTS = ("a2a", "mcp", "rest")


def _xpassed(base: str, *transports: str) -> list[dict[str, str]]:
    """Build xpassed json-report rows for ``base`` × transports."""
    chosen = transports or _PRESENT_TRANSPORTS
    return [{"nodeid": f"{base}[{t}]", "outcome": "xpassed"} for t in chosen]


def _load(
    name: str,
    request: pytest.FixtureRequest | None = None,
    *,
    directory: Path = SCRIPTS,
):
    path = directory / f"{name}.py"
    if not path.exists():
        pytest.fail(f"missing script: {path}")
    scripts_dir = str(SCRIPTS)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    # Reuse already-imported modules so `is` identity pins hold across importers.
    if name in sys.modules:
        return sys.modules[name]
    # Ensure shared helpers are loaded once before any consumer imports them.
    if name != "bdd_audit_common" and "bdd_audit_common" not in sys.modules:
        _load("bdd_audit_common", request)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    def _teardown() -> None:
        # Keep bdd_audit_common resident for sibling module fixtures; only
        # drop leaf script modules. Path cleanup is session-end only.
        if name != "bdd_audit_common":
            sys.modules.pop(name, None)

    if request is not None:
        request.addfinalizer(_teardown)
    return module


@pytest.fixture(scope="module")
def bdd_audit_common(request):
    return _load("bdd_audit_common", request)


@pytest.fixture(scope="module")
def captured_bdd_slice() -> dict:
    """One shared pytest-json-report-shaped ``bdd.json`` slice for classifier pins.

    Production artifact *shape* (created/duration/setup/call/teardown, crash
    messages, null longrepr) — not a thin hand-built dict per pin.
    """
    if not CAPTURED_SLICE.is_file():
        pytest.fail(f"missing captured bdd.json fixture: {CAPTURED_SLICE}")
    return json.loads(CAPTURED_SLICE.read_text(encoding="utf-8"))


def _slice_entry(
    captured_bdd_slice: dict,
    *,
    nodeid: str | None = None,
    outcome: str | None = None,
    nodeid_contains: str | None = None,
) -> dict:
    """Select exactly one test entry from the shared captured slice."""
    matches = []
    for entry in captured_bdd_slice["tests"]:
        if nodeid is not None and entry["nodeid"] != nodeid:
            continue
        if outcome is not None and entry["outcome"] != outcome:
            continue
        if nodeid_contains is not None and nodeid_contains not in entry["nodeid"]:
            continue
        matches.append(entry)
    assert len(matches) == 1, (
        f"expected 1 slice entry (nodeid={nodeid!r} outcome={outcome!r} "
        f"contains={nodeid_contains!r}), got {len(matches)}"
    )
    return matches[0]


def _slice_entries(
    captured_bdd_slice: dict,
    *,
    nodeid_contains: str | None = None,
    outcomes: set[str] | None = None,
) -> list[dict]:
    """Select zero-or-more entries from the shared captured slice."""
    selected = []
    for entry in captured_bdd_slice["tests"]:
        if nodeid_contains is not None and nodeid_contains not in entry["nodeid"]:
            continue
        if outcomes is not None and entry["outcome"] not in outcomes:
            continue
        selected.append(entry)
    return selected


@pytest.fixture(scope="module")
def bdd_full_audit(request, bdd_audit_common):
    return _load("bdd_full_audit", request)


@pytest.fixture(scope="module")
def salvage_audit(request, bdd_audit_common):
    return _load("salvage_audit_output", request)


@pytest.fixture(scope="module")
def audit_xfails(request, bdd_audit_common):
    return _load("audit_xfails", request)


@pytest.fixture(scope="module")
def cross_reference_audit(request, bdd_audit_common):
    return _load("cross_reference_audit", request)


@pytest.fixture(scope="module")
def graduate_pending(request, bdd_audit_common):
    """Load scripts/graduate_pending.py (sibling of .claude/scripts)."""
    return _load("graduate_pending", request, directory=REPO_ROOT / "scripts")


def _premature_source() -> str:
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


@pytest.fixture
def premature_step(tmp_path: Path, audit_xfails):
    """Shared (steps_file, step) fixture for crash/drift pins."""
    source = _premature_source()
    steps_file = tmp_path / "steps.py"
    steps_file.write_text(source)
    premature = audit_xfails.find_premature_xfails(tmp_path)
    assert len(premature) == 1
    return steps_file, premature[0]


def _json_report_test(
    path: str | Path,
    lineno: int,
    *,
    phase: str = "call",
    message: str = "_pytest.outcomes.XFailed: not ready",
    nodeid: str = "t::s[a2a]",
    keywords: list | None = None,
) -> dict:
    """Uniform pytest-json-report slice (no wasxfail key)."""
    crash = {"path": str(path), "lineno": lineno, "message": message}
    entry: dict = {
        "nodeid": nodeid,
        "keywords": keywords or [],
        "outcome": "xfailed",
    }
    if phase == "setup":
        entry["setup"] = {"outcome": "failed", "crash": crash}
    else:
        entry["setup"] = {"outcome": "passed"}
        entry["call"] = {"outcome": "skipped", "crash": crash, "longrepr": f"E   {message}"}
    return entry


class TestTransportHelpers:
    """Shared extract_* must agree and recognize e2e_rest."""

    def test_e2e_rest_recognizes_e2e_rest(self, bdd_audit_common) -> None:
        nodeid = "tests/bdd/test_uc004.py::test_s[e2e_rest]"
        assert bdd_audit_common.extract_transport(nodeid) == "e2e_rest"
        assert bdd_audit_common.extract_scenario_base(nodeid) == "tests/bdd/test_uc004.py::test_s"

    def test_extract_longrepr_e_line(self, bdd_audit_common) -> None:
        longrepr = "E   AssertionError: boom\n    other"
        assert bdd_audit_common.extract_longrepr_e_line(longrepr) == "AssertionError: boom"
        assert bdd_audit_common.extract_longrepr_e_line("no error") == ""

    def test_all_transport_enum_values(self, bdd_audit_common) -> None:
        from tests.harness.transport import Transport

        for v in Transport:
            assert bdd_audit_common.extract_transport(f"t::s[{v.value}]") == v.value

    def test_extract_uc_from_nodeid_and_path(self, bdd_audit_common) -> None:
        assert bdd_audit_common.extract_uc("tests/bdd/test_uc004.py::test_s[a2a]") == "UC-004"
        assert bdd_audit_common.extract_uc("tests/bdd/steps/uc019/then_steps.py") == "UC-019"
        assert bdd_audit_common.extract_uc("no-uc-here.py") == "GENERIC"

    def test_shared_helper_identity_per_importer(
        self, bdd_audit_common, bdd_full_audit, audit_xfails, cross_reference_audit
    ) -> None:
        assert bdd_full_audit.extract_transport is bdd_audit_common.extract_transport
        assert audit_xfails.extract_transport is bdd_audit_common.extract_transport
        assert cross_reference_audit.extract_transport is bdd_audit_common.extract_transport


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

    def test_single_transport_needs_confirmation(self, bdd_full_audit) -> None:
        all_entries = [self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[e2e_rest]")]
        result = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert result.category == "GRADUATE_CONFIRM"
        assert result.present_count == 1

    def test_single_non_e2e_transport_needs_confirmation(self, bdd_full_audit) -> None:
        all_entries = [self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[a2a]")]
        result = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert result.category == "GRADUATE_CONFIRM"
        assert result.present_count == 1

    @pytest.mark.parametrize(
        "order",
        [
            ("xfailed", "xpassed"),
            ("xpassed", "xfailed"),
        ],
        ids=["failing-first", "passing-last"],
    )
    def test_mixed_outline_examples_both_orders(self, bdd_full_audit, order) -> None:
        """Worst-outcome must not graduate regardless of example-row order."""
        o1, o2 = order
        all_entries = [
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_o[e2e_rest-ex1]", o1),
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_o[e2e_rest-ex2]", o2),
        ]
        result = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert result.category == "PARTIAL_XPASS"

    def test_skipped_outline_example_blocks_graduation(
        self, bdd_full_audit, bdd_audit_common, captured_bdd_slice
    ) -> None:
        """``skipped`` in the outcome vocabulary must block transport graduation."""
        outline = _slice_entries(captured_bdd_slice, nodeid_contains="::test_outline[")
        assert {e["outcome"] for e in outline} >= {"skipped", "xpassed"}
        all_entries = [self._entry(bdd_full_audit, e["nodeid"], e["outcome"]) for e in outline]
        xpassed = next(e for e in all_entries if e.outcome == "xpassed")
        result = bdd_full_audit.classify_xpass(xpassed, all_entries)
        assert result.category == "PARTIAL_XPASS"
        grade = bdd_audit_common.grade_base(
            "tests/bdd/test_uc004.py::test_outline",
            [(e.nodeid, e.outcome) for e in all_entries],
        )
        assert "e2e_rest" not in grade.passing
        assert "e2e_rest" in grade.missing

    def test_strict_subset_is_partial_xpass(self, bdd_full_audit) -> None:
        all_entries = [
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[a2a]", "xpassed"),
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[mcp]", "xfailed"),
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[rest]", "xfailed"),
        ]
        result = bdd_full_audit.classify_xpass(all_entries[0], all_entries)
        assert result.category == "PARTIAL_XPASS"

    def test_mixed_examples_empty_passing_is_partial(self, bdd_full_audit, bdd_audit_common) -> None:
        all_entries = [
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[a2a]", "xfailed"),
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[mcp]", "xfailed"),
        ]
        # Need an xpassed entry to enter classify_xpass; use a sibling then check grade
        grade = bdd_audit_common.grade_base(
            "tests/bdd/test_uc004.py::test_s",
            [(e.nodeid, e.outcome) for e in all_entries],
        )
        assert grade.mixed_examples is True
        assert grade.passing == set()
        # When called via classify with an xpassed sibling row that still aggregates to fail:
        xpass_entry = self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[a2a]", "xpassed")
        mixed = [
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[a2a-ex1]", "xfailed"),
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[a2a-ex2]", "xpassed"),
            self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[mcp]", "xfailed"),
        ]
        result = bdd_full_audit.classify_xpass(xpass_entry, mixed)
        assert result.category == "PARTIAL_XPASS"

    def test_e2e_rest_less_artifact_force_confirm(self, bdd_full_audit) -> None:
        """Incomplete artifact (no e2e_rest anywhere) → no firm graduate."""
        all_entries = [
            self._entry(bdd_full_audit, f"tests/bdd/test_uc004.py::test_s[{t}]") for t in ("a2a", "mcp", "rest")
        ]
        result = bdd_full_audit.classify_xpass(all_entries[0], all_entries, force_confirm=True)
        assert result.category == "GRADUATE_CONFIRM"

    def test_impl_does_not_defeat_confirm_gate(self, bdd_audit_common) -> None:
        grade = bdd_audit_common.grade_base(
            "t::s",
            [
                ("t::s[e2e_rest]", "xpassed"),
                ("t::s[impl]", "xpassed"),
            ],
        )
        assert grade.graduates is True
        assert grade.needs_confirmation is True

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
            tag_reasons_map={},
            all_entries=all_entries,
        )
        cats = {i.category for i in items}
        assert cats == {"GRADUATE", "PARTIAL_XPASS"}
        assert len(items) == 2

    def test_generate_work_items_confirm_title_for_e2e_rest_only(self, bdd_full_audit) -> None:
        all_entries = [self._entry(bdd_full_audit, "tests/bdd/test_uc004.py::test_s[e2e_rest]")]
        items = bdd_full_audit.generate_work_items(
            failed=[],
            xfailed=[],
            xpassed=all_entries,
            inspector_flags=[],
            tag_reasons_map={},
            all_entries=all_entries,
        )
        assert len(items) == 1
        assert items[0].category == "GRADUATE_CONFIRM"
        assert "needs confirmation" in items[0].title

    def test_generate_work_items_plain_graduate_title(self, bdd_full_audit) -> None:
        all_entries = [
            self._entry(bdd_full_audit, f"tests/bdd/test_uc004.py::test_s[{t}]") for t in ("a2a", "mcp", "rest")
        ]
        items = bdd_full_audit.generate_work_items(
            failed=[],
            xfailed=[],
            xpassed=all_entries,
            inspector_flags=[],
            tag_reasons_map={},
            all_entries=all_entries,
        )
        assert len(items) == 1
        assert items[0].category == "GRADUATE"
        assert items[0].title.startswith("Graduate (all 3 present)")
        assert "needs confirmation" not in items[0].title


class TestClassifyXpassedAudit:
    """audit_xfails.classify_xpassed uses the same present-transport rule."""

    def test_three_transport_xpass_is_stale_not_partial(self, audit_xfails) -> None:
        base = "tests/bdd/test_uc004.py::test_s"
        all_tests = _xpassed(base)
        buckets = audit_xfails.classify_xpassed(all_tests)
        assert buckets.graduate == {base}
        assert buckets.confirm == set()

    def test_e2e_rest_only_needs_confirmation_not_stale(self, audit_xfails) -> None:
        base = "tests/bdd/test_uc004.py::test_s"
        all_tests = [{"nodeid": f"{base}[e2e_rest]", "outcome": "xpassed"}]
        buckets = audit_xfails.classify_xpassed(all_tests)
        assert buckets.graduate == set()
        assert buckets.confirm == {base}

    def test_single_non_e2e_transport_needs_confirmation_not_stale(self, audit_xfails) -> None:
        base = "tests/bdd/test_uc004.py::test_s"
        all_tests = [{"nodeid": f"{base}[a2a]", "outcome": "xpassed"}]
        buckets = audit_xfails.classify_xpassed(all_tests)
        assert buckets.confirm == {base}

    def test_strict_subset_is_partial(self, audit_xfails) -> None:
        base = "tests/bdd/test_uc004.py::test_s"
        all_tests = [
            {"nodeid": f"{base}[a2a]", "outcome": "xpassed"},
            {"nodeid": f"{base}[mcp]", "outcome": "xfailed"},
            {"nodeid": f"{base}[rest]", "outcome": "xfailed"},
        ]
        buckets = audit_xfails.classify_xpassed(all_tests)
        assert buckets.partial_passing == {base: {"a2a"}}
        assert buckets.partial_missing == {base: {"mcp", "rest"}}

    @pytest.mark.parametrize(
        "order",
        [
            ("xfailed", "xpassed"),
            ("xpassed", "xfailed"),
        ],
        ids=["failing-first", "passing-last"],
    )
    def test_mixed_outline_examples_both_orders(self, audit_xfails, order) -> None:
        base = "tests/bdd/test_uc004.py::test_outline"
        o1, o2 = order
        all_tests = [
            {"nodeid": f"{base}[e2e_rest-ex1]", "outcome": o1},
            {"nodeid": f"{base}[e2e_rest-ex2]", "outcome": o2},
        ]
        buckets = audit_xfails.classify_xpassed(all_tests)
        assert buckets.graduate == set()
        assert buckets.confirm == set()

    def test_skipped_outline_example_blocks_graduation(self, audit_xfails, captured_bdd_slice) -> None:
        """``skipped`` must keep the transport out of ``passing`` / graduate."""
        base = "tests/bdd/test_uc004.py::test_outline"
        outline = _slice_entries(captured_bdd_slice, nodeid_contains="::test_outline[")
        assert {e["outcome"] for e in outline} >= {"skipped", "xpassed"}
        buckets = audit_xfails.classify_xpassed(outline)
        assert buckets.graduate == set()
        assert buckets.confirm == set()
        assert buckets.partial_passing.get(base, set()) == set()
        assert "e2e_rest" in buckets.partial_missing.get(base, set())

    def test_mixed_examples_routed_to_partial(self, audit_xfails) -> None:
        base = "tests/bdd/test_uc004.py::test_s"
        # worst for a2a is xfailed → mixed_examples (no passing)
        all_tests = [
            {"nodeid": f"{base}[a2a-ex1]", "outcome": "xfailed"},
            {"nodeid": f"{base}[a2a-ex2]", "outcome": "xpassed"},
            {"nodeid": f"{base}[mcp]", "outcome": "xfailed"},
        ]
        buckets = audit_xfails.classify_xpassed(all_tests)
        assert buckets.partial_passing == {base: set()}
        assert buckets.partial_missing == {base: {"a2a", "mcp"}}
        assert buckets.graduate == set()

    def test_e2e_rest_less_no_firm_graduate(self, audit_xfails) -> None:
        base = "tests/bdd/test_uc004.py::test_s"
        all_tests = _xpassed(base)
        buckets = audit_xfails.classify_xpassed(all_tests, force_confirm=True)
        assert buckets.graduate == set()
        assert buckets.confirm == {base}

    def test_ledger_member_without_e2e_rest_confirms(self, audit_xfails) -> None:
        """Ledger-marked base with no e2e_rest row must confirm, not firm-graduate."""
        base = "tests/bdd/test_uc004.py::test_s"
        all_tests = _xpassed(base)
        buckets = audit_xfails.classify_xpassed(all_tests, ledger_bases={base})
        assert buckets.graduate == set()
        assert buckets.confirm == {base}


class TestGraduationOperands:
    """Pin core transport_coverage / grade_base operands (S3)."""

    def test_passed_outcome_graduates(self, bdd_audit_common) -> None:
        coverage = bdd_audit_common.transport_coverage({"a2a": "passed", "mcp": "passed"})
        assert coverage.graduates is True
        assert coverage.passing == {"a2a", "mcp"}

    def test_skipped_is_missing_not_graduating(self, bdd_audit_common) -> None:
        coverage = bdd_audit_common.transport_coverage({"a2a": "passed", "mcp": "skipped"})
        assert coverage.graduates is False
        assert coverage.passing == {"a2a"}
        assert coverage.missing == {"mcp"}

    def test_empty_present_not_graduating(self, bdd_audit_common) -> None:
        coverage = bdd_audit_common.transport_coverage({})
        assert coverage.graduates is False

    def test_single_xfailed_needs_confirmation_false(self, bdd_audit_common) -> None:
        grade = bdd_audit_common.grade_base(
            "t::s",
            [("t::s[a2a]", "xfailed")],
        )
        assert grade.graduates is False
        assert grade.needs_confirmation is False
        assert grade.mixed_examples is True


class TestParseTestResultsCallSites:
    """Pin shared-helper call sites inside parse_test_results (S4 / S10)."""

    def test_bdd_full_audit_parse_round_trip(self, bdd_full_audit, captured_bdd_slice) -> None:
        entries = bdd_full_audit.parse_test_results(CAPTURED_SLICE)
        by_nodeid = {e.nodeid: e for e in entries}
        populated_raw = _slice_entry(captured_bdd_slice, nodeid_contains="test_longrepr_populated")
        empty_raw = _slice_entry(captured_bdd_slice, nodeid_contains="test_longrepr_empty")
        populated = by_nodeid[populated_raw["nodeid"]]
        empty = by_nodeid[empty_raw["nodeid"]]
        assert populated.error == "AssertionError: boom"
        assert "AssertionError: boom" in populated.longrepr
        assert bdd_full_audit.extract_transport(populated.nodeid) == "e2e_rest"
        assert empty.error == ""
        assert empty.longrepr == ""
        # Full outcome vocabulary present in the shared slice.
        assert {e["outcome"] for e in captured_bdd_slice["tests"]} >= {
            "passed",
            "skipped",
            "xfailed",
            "xpassed",
            "failed",
        }

    def test_cross_reference_parse_round_trip(self, cross_reference_audit, captured_bdd_slice) -> None:
        outcomes = cross_reference_audit.parse_test_results(CAPTURED_SLICE)
        failed = next(o for o in outcomes if "test_longrepr_failed" in o.nodeid)
        assert failed.transport == "mcp"
        assert failed.error == "ValueError: nope"
        assert failed.outcome == _slice_entry(captured_bdd_slice, nodeid_contains="test_longrepr_failed")["outcome"]

    def test_audit_xfails_parse_round_trip(self, audit_xfails, captured_bdd_slice) -> None:
        xfailed, xpassed, all_tests, artifact_root = audit_xfails.parse_test_results(CAPTURED_SLICE)
        assert artifact_root == captured_bdd_slice["root"] == "/app"
        assert len(xfailed) >= 1
        assert len(xpassed) >= 1
        assert len(all_tests) == len(captured_bdd_slice["tests"])
        simple = next(t for t in xfailed if "test_xfail_simple" in t["nodeid"])
        assert audit_xfails.extract_transport(simple["nodeid"]) == "a2a"


class TestConftestTagParser:
    """Canonical parse_conftest_xfail_tags + tag_reasons (S8)."""

    def test_real_conftest_floor_count(self, bdd_audit_common) -> None:
        if not CONFTEST.exists():
            pytest.fail(f"missing conftest: {CONFTEST}")
        tag_map = bdd_audit_common.parse_conftest_xfail_tags(CONFTEST)
        reasons = bdd_audit_common.tag_reasons(CONFTEST)
        assert len(tag_map) >= 100
        assert len(reasons) == len(tag_map)
        assert all(reasons[k] == tag_map[k].reason for k in tag_map)

    def test_audit_xfails_uses_shared_parser(self, audit_xfails, bdd_audit_common) -> None:
        assert audit_xfails.parse_conftest_xfail_tags is bdd_audit_common.parse_conftest_xfail_tags


class TestCrashMessageClassification:
    """B1 — reason from setup/call crash.message, not wasxfail."""

    def test_harness_gap_from_setup_crash_message(self, audit_xfails, captured_bdd_slice) -> None:
        test = _slice_entry(captured_bdd_slice, nodeid_contains="test_harness_yet")
        assert "wasxfail" not in test  # realistic json-report shape
        assert "harness not yet wired" in test["setup"]["crash"]["message"].lower()
        entry = audit_xfails.classify_xfail(test, {}, [])
        assert entry.category == "HARNESS_GAP"
        assert entry.xfail_source == "conftest:auto"

    def test_harness_gap_from_no_harness_wired_catch_all(self, audit_xfails, captured_bdd_slice) -> None:
        """Pin live catch-all ``No harness wired for {uc}`` (conftest else arm)."""
        test = _slice_entry(captured_bdd_slice, nodeid_contains="test_harness_env")
        message = test["setup"]["crash"]["message"]
        assert "No harness wired for UC-004" in message
        assert "No harness environment" not in message
        entry = audit_xfails.classify_xfail(test, {}, [])
        assert entry.category == "HARNESS_GAP"
        assert entry.xfail_source == "conftest:auto"

    def test_harness_gap_from_harness_not_wired_without_yet(self, audit_xfails, captured_bdd_slice) -> None:
        """Pin ``harness not wired`` (no ``yet``) as its own Priority-2 disjunct."""
        test = _slice_entry(captured_bdd_slice, nodeid_contains="test_harness_wired")
        message = test["setup"]["crash"]["message"]
        assert "not yet" not in message.lower()
        assert "No harness environment" not in message
        assert "harness not wired" in message.lower()
        entry = audit_xfails.classify_xfail(test, {}, [])
        assert entry.category == "HARNESS_GAP"
        assert entry.xfail_source == "conftest:auto"

    def test_deterministic_tag_order_picks_lower_sorted_tag(
        self, audit_xfails, bdd_audit_common, captured_bdd_slice
    ) -> None:
        """Two conflicting tags: ``sorted(tags)`` must pick the lower-sorted key."""
        test = _slice_entry(captured_bdd_slice, nodeid_contains="test_conflicting_tags")
        tags = {kw for kw in test["keywords"] if isinstance(kw, str) and kw.startswith("T-")}
        assert tags == {"T-UC-004-b-partition", "T-UC-004-a-production"}
        tag_map = {
            "T-UC-004-b-partition": bdd_audit_common.TagReason("partition arm", "partial_impl"),
            "T-UC-004-a-production": bdd_audit_common.TagReason("production arm", "production_gap"),
        }
        entry = audit_xfails.classify_xfail(test, tag_map, [])
        assert entry.category == "PRODUCTION_GAP"
        assert entry.xfail_source == "conftest:tag:T-UC-004-a-production"
        assert entry.reason == "production arm"

    def test_missing_step_from_crash_message(self, audit_xfails) -> None:
        # Real pytest-bdd wording is "Step definition is not found" (not "not
        # found for") — only the StepDefinitionNotFoundError type name matches
        # on real data. See test_missing_step_from_bare_error_type below.
        test = _json_report_test(
            "/tmp/x.py",
            1,
            phase="setup",
            message="StepDefinitionNotFoundError: Step definition is not found: Then foo. "
            'Line 3 in scenario "Do the thing"',
        )
        entry = audit_xfails.classify_xfail(test, {}, [])
        assert entry.category == "MISSING_STEP"

    def test_missing_step_from_bare_error_type(self, audit_xfails) -> None:
        # Pin the load-bearing disjunct alone: no "Step definition not found"
        # human phrase anywhere in the message, only the bare error type name.
        test = _json_report_test("/tmp/x.py", 1, phase="setup", message="StepDefinitionNotFoundError")
        assert "Step definition not found" not in test["setup"]["crash"]["message"]
        entry = audit_xfails.classify_xfail(test, {}, [])
        assert entry.category == "MISSING_STEP"


class TestContainerPathRemap:
    """B2 — /app-rooted crash paths remapped onto host step paths."""

    def test_container_path_matches_host_step(self, audit_xfails, premature_step, tmp_path: Path) -> None:
        steps_file, step = premature_step
        # Relative path under tmp_path as if under /app
        rel = steps_file.name
        container_path = f"/app/{rel}"
        mid = (step.lineno + step.end_lineno) // 2
        test = _json_report_test(container_path, mid, phase="call")
        # Without remap: no match
        entry_bare = audit_xfails.classify_xfail(test, {}, [step])
        assert entry_bare.category != "PREMATURE_XFAIL"
        # With remap host_root=tmp_path, artifact_root=/app: match
        entry = audit_xfails.classify_xfail(
            test,
            {},
            [step],
            artifact_root="/app",
            host_root=tmp_path,
        )
        assert entry.category == "PREMATURE_XFAIL"

    def test_drift_warning_fires_after_remap(self, audit_xfails, premature_step, tmp_path: Path) -> None:
        steps_file, step = premature_step
        container_path = f"/app/{steps_file.name}"
        test = _json_report_test(container_path, step.end_lineno + 1, phase="call")
        warnings = audit_xfails.crash_line_drift_warnings(test, [step], artifact_root="/app", host_root=tmp_path)
        assert len(warnings) == 1
        assert "line-drift/stale-json?" in warnings[0]


class TestPrematureXfailCrashMatch:
    """PREMATURE_XFAIL matches setup/call crash path+lineno → enclosing step."""

    def _xfail_lineno(self, source: str) -> int:
        return next(
            n.lineno
            for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "xfail"
        )

    def test_crash_inside_premature_step_classifies(self, audit_xfails, premature_step) -> None:
        steps_file, step = premature_step
        source = steps_file.read_text()
        xfail_lineno = self._xfail_lineno(source)
        entry = audit_xfails.classify_xfail(
            _json_report_test(steps_file.resolve(), xfail_lineno, phase="call"),
            {},
            [step],
        )
        assert entry.category == "PREMATURE_XFAIL"
        assert "then_premature" in entry.reason

    def test_crash_just_past_end_lineno_is_not_premature(self, audit_xfails, premature_step) -> None:
        steps_file, step = premature_step
        test = _json_report_test(
            steps_file.resolve(),
            step.end_lineno + 1,
            phase="call",
            message="_pytest.outcomes.XFailed: UC harness not wired",
        )
        entry = audit_xfails.classify_xfail(test, {}, [step])
        assert entry.category != "PREMATURE_XFAIL"
        warnings = audit_xfails.crash_line_drift_warnings(test, [step])
        assert len(warnings) == 1

    def test_in_range_crash_in_known_step_file_no_drift_warning(self, audit_xfails, premature_step) -> None:
        steps_file, step = premature_step
        mid = (step.lineno + step.end_lineno) // 2
        test = _json_report_test(steps_file.resolve(), mid, phase="call")
        assert audit_xfails.crash_line_drift_warnings(test, [step]) == []

    def test_setup_phase_crash_also_matches(self, audit_xfails, premature_step) -> None:
        steps_file, step = premature_step
        mid = (step.lineno + step.end_lineno) // 2
        test = _json_report_test(steps_file.resolve(), mid, phase="setup")
        entry = audit_xfails.classify_xfail(test, {}, [step])
        assert entry.category == "PREMATURE_XFAIL"

    def test_crash_in_different_file_in_range_is_not_premature(
        self, audit_xfails, premature_step, tmp_path: Path
    ) -> None:
        _steps_file, step = premature_step
        other = tmp_path / "conftest.py"
        other.write_text("# decoy crash site\n")
        in_range = (step.lineno + step.end_lineno) // 2
        entry = audit_xfails.classify_xfail(
            _json_report_test(other.resolve(), in_range, phase="setup", message="UC harness not wired"),
            {},
            [step],
        )
        assert entry.category != "PREMATURE_XFAIL"
        # known_paths filter: decoy path not in premature set → no drift warning
        assert (
            audit_xfails.crash_line_drift_warnings(
                _json_report_test(other.resolve(), in_range, phase="setup"),
                [step],
            )
            == []
        )

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
        assert "then_with_int_expr" not in names


class TestSalvageDedupe:
    """Pin kind-scoped deep-trace dedup and ANSI parse (S10 / N5 / N15)."""

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

    def test_intra_call_duplicate_entries_write_once(self, salvage_audit, tmp_path: Path) -> None:
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

    def test_store_key_line_number_distinguishes_same_name(self, salvage_audit, tmp_path: Path) -> None:
        """Pre-seeded store at line 7 + step index at line 42 for same name."""
        store = tmp_path / "store.jsonl"
        store.write_text(
            json.dumps(
                {
                    "kind": "triage",
                    "verdict": "FLAG",
                    "step": {
                        "file_path": "a.py",
                        "line_number": 7,
                        "step_type": "then",
                        "step_text": "a",
                        "function_name": "then_a",
                        "source_text": "pass",
                    },
                }
            )
            + "\n"
        )
        step_index = tmp_path / "steps.jsonl"
        step_index.write_text(
            json.dumps(
                {
                    "step": {
                        "file_path": "a.py",
                        "line_number": 42,
                        "step_type": "then",
                        "step_text": "a",
                        "function_name": "then_a",
                        "source_text": "pass",
                    }
                }
            )
            + "\n"
        )
        parsed = {
            "pass1": [{"index": 1, "func_name": "then_a", "verdict": "FLAG"}],
            "pass2": [],
            "pass1_total": 1,
            "pass2_total": 0,
            "pass2_crashed_at": 0,
        }
        salvage_audit.write_to_store(parsed, store, step_index)
        records = [json.loads(line) for line in store.read_text().splitlines() if line.strip()]
        triage = [r for r in records if r["kind"] == "triage"]
        lines = sorted(r["step"]["line_number"] for r in triage)
        assert lines == [7, 42]

    def test_step_index_normalizes_real_lines_and_dedupes(self, salvage_audit, tmp_path: Path) -> None:
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
        assert by_name["then_a"]["line_number"] == 42
        assert by_name["then_b"]["line_number"] == 99

    def test_non_numeric_line_number_degrades_instead_of_raising(self, salvage_audit) -> None:
        """iter_jsonl_records tolerates corrupt JSONL; _normalize_step must too.

        A non-numeric ``line_number`` (hand-edited store, foreign producer)
        must degrade to ``0`` here, not raise and abort the whole salvage
        read path one call down from the loader's deliberate tolerance.
        """
        record = salvage_audit._normalize_step({"function_name": "then_x", "line_number": "unknown"})
        assert record["line_number"] == 0
        assert record["function_name"] == "then_x"

    def test_write_to_store_survives_corrupt_line_number_in_step_index(self, salvage_audit, tmp_path: Path) -> None:
        store = tmp_path / "store.jsonl"
        step_index = tmp_path / "steps.jsonl"
        step_index.write_text(
            json.dumps(
                {
                    "step": {
                        "file_path": "tests/bdd/steps/a.py",
                        "line_number": "not-a-number",
                        "step_type": "then",
                        "step_text": "a",
                        "function_name": "then_a",
                        "source_text": "pass",
                    }
                }
            )
            + "\n"
        )
        parsed = {
            "pass1": [{"index": 1, "func_name": "then_a", "verdict": "FLAG"}],
            "pass2": [],
            "pass1_total": 1,
            "pass2_total": 0,
            "pass2_crashed_at": 0,
        }
        # Must not raise ValueError — this is the exact degraded input salvage exists to rescue.
        salvage_audit.write_to_store(parsed, store, step_index)
        records = [json.loads(line) for line in store.read_text().splitlines() if line.strip()]
        assert records[0]["step"]["line_number"] == 0

    def test_parse_raw_output_strips_ansi(self, salvage_audit, tmp_path: Path) -> None:
        raw = tmp_path / "out.txt"
        # Interleaved ANSI inside the matched span (real rich/pytest coloring).
        # Whole-line wrappers would still match with strip_ansi neutered.
        raw.write_text(
            "[1/2] \x1b[1mthen_foo\x1b[0m... \x1b[31mFLAG\x1b[0m\n"
            "[2/2] \x1b[1mthen_bar\x1b[0m... \x1b[32mPASS\x1b[0m\n"
            "1 flagged, 1 passed\n"
        )
        parsed = salvage_audit.parse_raw_output(raw)
        assert len(parsed["pass1"]) == 2
        assert parsed["pass1"][0]["func_name"] == "then_foo"
        assert parsed["pass1"][0]["verdict"] == "FLAG"
        assert parsed["pass1_total"] == 2

    def test_parse_raw_output_raises_on_row_total_mismatch(self, salvage_audit, tmp_path: Path) -> None:
        raw = tmp_path / "out.txt"
        # Same interleaved fixture; totals disagree with row count → ValueError guard.
        raw.write_text(
            "[1/2] \x1b[1mthen_foo\x1b[0m... \x1b[31mFLAG\x1b[0m\n"
            "[2/2] \x1b[1mthen_bar\x1b[0m... \x1b[32mPASS\x1b[0m\n"
            "50 flagged, 49 passed\n"
        )
        with pytest.raises(ValueError, match="pass1 row count"):
            salvage_audit.parse_raw_output(raw)

    def test_step_record_key_set_oracle(self, bdd_audit_common) -> None:
        # Tuple preserves declaration order (JSONL line dedupe / PYTHONHASHSEED).
        assert bdd_audit_common.STEP_RECORD_KEYS == (
            "file_path",
            "line_number",
            "step_type",
            "step_text",
            "function_name",
            "source_text",
        )
        assert set(bdd_audit_common.STEP_RECORD_KEYS) == set(bdd_audit_common.StepRecord.__annotations__)


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
        assert "STALE_CONFIRM" in text.split("### By use case")[1].split("\n")[2]


class TestArtifactCensusAndLedger:
    """Pin census → force_confirm and ledger parser (Jul-31 safety net)."""

    def test_census_with_e2e_rest_is_complete(self, bdd_audit_common) -> None:
        census = bdd_audit_common.artifact_transport_census(
            [
                ("t::s[a2a]", "xpassed"),
                ("t::s[e2e_rest]", "xpassed"),
            ]
        )
        assert census.has_e2e_rest is True
        assert census.incomplete_e2e is False

    def test_census_without_e2e_rest_is_incomplete(self, bdd_audit_common) -> None:
        census = bdd_audit_common.artifact_transport_census(
            [
                ("t::s[a2a]", "xpassed"),
                ("t::s[mcp]", "xpassed"),
                ("t::s[rest]", "xpassed"),
            ]
        )
        assert census.has_e2e_rest is False
        assert census.incomplete_e2e is True

    def test_load_e2e_rest_known_failure_bases(self, bdd_audit_common, tmp_path: Path) -> None:
        ledger = tmp_path / "ledger.txt"
        ledger.write_text(
            "# comment\ntests/bdd/test_uc004.py::test_s[e2e_rest]\n\ntests/bdd/test_uc019.py::test_other\n"
        )
        bases = bdd_audit_common.load_e2e_rest_known_failure_bases(ledger)
        assert bases == {
            "tests/bdd/test_uc004.py::test_s",
            "tests/bdd/test_uc019.py::test_other",
        }

    def test_load_bdd_artifact_empty_refuses(self, bdd_audit_common, tmp_path: Path) -> None:
        report = tmp_path / "empty.json"
        report.write_text(json.dumps({"tests": []}))
        with pytest.raises(bdd_audit_common.EmptyArtifactError) as exc:
            bdd_audit_common.load_bdd_artifact(report)
        assert "0 tests" in exc.value.message

    def test_cli_load_or_exit_empty_exits_2(self, bdd_audit_common, tmp_path: Path) -> None:
        report = tmp_path / "empty.json"
        report.write_text(json.dumps({"tests": []}))
        with pytest.raises(SystemExit) as exc:
            bdd_audit_common.cli_load_or_exit(report)
        assert exc.value.code == 2

    def test_load_bdd_artifact_sets_force_confirm(self, bdd_audit_common, tmp_path: Path) -> None:
        report = tmp_path / "bdd.json"
        report.write_text(
            json.dumps(
                {
                    "root": "/app",
                    "tests": [
                        {"nodeid": "t::s[a2a]", "outcome": "xpassed"},
                        {"nodeid": "t::s[mcp]", "outcome": "xpassed"},
                    ],
                }
            )
        )
        loaded = bdd_audit_common.load_bdd_artifact(report)
        assert loaded.force_confirm is True
        assert loaded.artifact_root == "/app"
        assert len(loaded.tests) == 2

    def test_grade_base_ledger_member_confirms(self, bdd_audit_common) -> None:
        grade = bdd_audit_common.grade_base(
            "t::s",
            [("t::s[a2a]", "xpassed"), ("t::s[mcp]", "xpassed"), ("t::s[rest]", "xpassed")],
            ledger_member=True,
        )
        assert grade.graduates is True
        assert grade.needs_confirmation is True

    def test_grade_base_ledger_member_with_e2e_rest_present_does_not_confirm(self, bdd_audit_common) -> None:
        """The ledger-confirm gate is keyed on e2e_rest ABSENCE for this base.

        A ledger member whose e2e_rest row is present *and passing* must not
        route to confirm on that account — the guard exists to catch bases
        that skip e2e_rest, not ones that genuinely ran and passed it.
        """
        grade = bdd_audit_common.grade_base(
            "t::s",
            [
                ("t::s[a2a]", "xpassed"),
                ("t::s[mcp]", "xpassed"),
                ("t::s[rest]", "xpassed"),
                ("t::s[e2e_rest]", "xpassed"),
            ],
            ledger_member=True,
        )
        assert grade.graduates is True
        assert grade.needs_confirmation is False


class TestGraduatePendingAnalyze:
    """Pin graduate_pending.analyze three-way split + empty refusal."""

    def test_empty_artifact_exits_2(self, graduate_pending, bdd_audit_common, tmp_path: Path) -> None:
        report = tmp_path / "empty.json"
        report.write_text(json.dumps({"tests": []}))
        with pytest.raises(bdd_audit_common.EmptyArtifactError):
            graduate_pending.analyze(str(report))
        with pytest.raises(SystemExit) as exc:
            bdd_audit_common.cli_load_or_exit(report)
        assert exc.value.code == 2

    def test_three_way_split_and_ledger(self, graduate_pending, tmp_path: Path) -> None:
        report = tmp_path / "bdd.json"
        report.write_text(
            json.dumps(
                {
                    "tests": [
                        # firm graduate (3 transports, not ledger)
                        {"nodeid": "tests/bdd/test_uc004.py::test_full[a2a]", "outcome": "xpassed", "keywords": []},
                        {"nodeid": "tests/bdd/test_uc004.py::test_full[mcp]", "outcome": "xpassed", "keywords": []},
                        {"nodeid": "tests/bdd/test_uc004.py::test_full[rest]", "outcome": "xpassed", "keywords": []},
                        # confirm via single transport
                        {"nodeid": "tests/bdd/test_uc004.py::test_one[a2a]", "outcome": "xpassed", "keywords": []},
                        # partial
                        {"nodeid": "tests/bdd/test_uc004.py::test_part[a2a]", "outcome": "xpassed", "keywords": []},
                        {"nodeid": "tests/bdd/test_uc004.py::test_part[mcp]", "outcome": "xfailed", "keywords": []},
                    ]
                }
            )
        )
        ledger = tmp_path / "ledger.txt"
        ledger.write_text("")  # empty ledger
        analysis = graduate_pending.analyze(str(report), ledger_path=ledger)
        assert analysis["force_confirm"] is True  # no e2e_rest in artifact
        # force_confirm routes all graduates to confirm
        assert analysis["graduate_all"] == []
        confirm_bases = {base for _, base in analysis["graduate_confirm"]}
        assert "tests/bdd/test_uc004.py::test_full" in confirm_bases
        assert "tests/bdd/test_uc004.py::test_one" in confirm_bases
        partial_bases = {base for _, base, _ in analysis["graduate_partial"]}
        assert "tests/bdd/test_uc004.py::test_part" in partial_bases

    def test_ledger_routes_to_confirm_when_e2e_present(self, graduate_pending, tmp_path: Path) -> None:
        base = "tests/bdd/test_uc004.py::test_s"
        report = tmp_path / "bdd.json"
        report.write_text(
            json.dumps(
                {
                    "tests": [
                        {"nodeid": f"{base}[a2a]", "outcome": "xpassed", "keywords": []},
                        {"nodeid": f"{base}[mcp]", "outcome": "xpassed", "keywords": []},
                        {"nodeid": f"{base}[rest]", "outcome": "xpassed", "keywords": []},
                        # e2e_rest present elsewhere so force_confirm is False
                        {"nodeid": "tests/bdd/test_other.py::test_e[e2e_rest]", "outcome": "passed", "keywords": []},
                    ]
                }
            )
        )
        ledger = tmp_path / "ledger.txt"
        ledger.write_text(f"{base}\n")
        analysis = graduate_pending.analyze(str(report), ledger_path=ledger)
        assert analysis["force_confirm"] is False
        assert analysis["graduate_all"] == []
        confirm_bases = {b for _, b in analysis["graduate_confirm"]}
        assert base in confirm_bases

    def test_non_ledger_base_firm_graduates_when_e2e_present(self, graduate_pending, tmp_path: Path) -> None:
        """graduate_all must be non-empty for the case it exists to hold.

        Multi-transport, not on the ledger, and e2e_rest present in the
        artifact so force_confirm is False — this base must firm-graduate,
        not route to confirm. Reverting the firm-graduate routing in
        ``graduate_pending.analyze`` (or the ledger/force_confirm gates)
        must move this base out of ``graduate_all``.
        """
        base = "tests/bdd/test_uc004.py::test_firm"
        report = tmp_path / "bdd.json"
        report.write_text(
            json.dumps(
                {
                    "tests": [
                        {"nodeid": f"{base}[a2a]", "outcome": "xpassed", "keywords": []},
                        {"nodeid": f"{base}[mcp]", "outcome": "xpassed", "keywords": []},
                        {"nodeid": f"{base}[rest]", "outcome": "xpassed", "keywords": []},
                        # e2e_rest present elsewhere so force_confirm is False
                        {"nodeid": "tests/bdd/test_other.py::test_e[e2e_rest]", "outcome": "passed", "keywords": []},
                    ]
                }
            )
        )
        ledger = tmp_path / "ledger.txt"
        ledger.write_text("")  # base is not a ledger member
        analysis = graduate_pending.analyze(str(report), ledger_path=ledger)
        assert analysis["force_confirm"] is False
        graduate_bases = {b for _, b in analysis["graduate_all"]}
        confirm_bases = {b for _, b in analysis["graduate_confirm"]}
        assert base in graduate_bases
        assert base not in confirm_bases


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
        assert grade.mixed_examples is False
        assert grade.bucket == "confirm"
        assert grade.outcomes == {"a2a": "xpassed"}

    def test_transport_coverage_named_fields(self, bdd_audit_common) -> None:
        coverage = bdd_audit_common.transport_coverage({"a2a": "xpassed", "mcp": "xfailed"})
        assert coverage.graduates is False
        assert coverage.passing == {"a2a"}
        assert coverage.missing == {"mcp"}

    def test_short_base(self, bdd_audit_common) -> None:
        assert bdd_audit_common.short_base("tests/bdd/test_uc004.py::test_s") == "test_s"
        assert bdd_audit_common.short_base("bare") == "bare"

    def test_short_nodeid(self, bdd_audit_common) -> None:
        assert bdd_audit_common.short_nodeid("tests/bdd/test_uc004.py::test_s[a2a]") == "test_s[a2a]"
        assert bdd_audit_common.short_nodeid("x" * 100, limit=10) == "x" * 10


class TestSurvivingMechanismArms:
    """S9 — pin surviving mechanism arms after dead-arm deletion."""

    def test_partial_impl_from_tag_map(self, audit_xfails, bdd_audit_common) -> None:
        test = _json_report_test(
            "/x", 1, phase="call", message="xfail", nodeid="t::s[a2a]", keywords=["T-UC-004-partition-foo"]
        )
        entry = audit_xfails.classify_xfail(
            test,
            {"T-UC-004-partition-foo": bdd_audit_common.TagReason("partition reason", "partial_impl")},
            [],
        )
        assert entry.category == "PARTIAL_IMPL"

    def test_production_gap_from_tag_map(self, audit_xfails, bdd_audit_common) -> None:
        test = _json_report_test(
            "/x", 1, phase="setup", message="xfail", nodeid="t::s[a2a]", keywords=["T-UC-002-main-1"]
        )
        entry = audit_xfails.classify_xfail(
            test,
            {"T-UC-002-main-1": bdd_audit_common.TagReason("not built", "production_gap")},
            [],
        )
        assert entry.category == "PRODUCTION_GAP"

    def test_stale_strict_via_xpass_strict_longrepr(self, bdd_full_audit) -> None:
        entry = bdd_full_audit.TestEntry(
            nodeid="t::s[a2a]",
            outcome="failed",
            longrepr="[XPASS(strict)] unexpected pass",
            error="",
        )
        bucket, cat, _ = bdd_full_audit.classify_failure(entry)
        assert bucket == "FIX_NOW"
        assert cat == "STALE_STRICT_XFAIL"


class TestAug17HarnessAndCoverage:
    """Aug-17 KM: live harness vocab, unknown transport, unparametrized bucket."""

    def test_unknown_transport_blocks_firm_graduate(self, bdd_audit_common) -> None:
        grade = bdd_audit_common.grade_base(
            "t::s",
            [
                ("t::s[a2a]", "xpassed"),
                ("t::s[mcp]", "xpassed"),
                ("t::s[rest]", "xpassed"),
                ("t::s[e2e_rest]", "xpassed"),
                ("t::s[newtransport]", "xfailed"),
            ],
        )
        assert grade.bucket == "partial"
        assert "newtransport" in grade.missing
        assert grade.graduates is False

    def test_unparametrized_base_has_own_bucket(self, bdd_audit_common, bdd_full_audit) -> None:
        grade = bdd_audit_common.grade_base("t::admin_only", [("t::admin_only", "xpassed")])
        assert grade.present_count == 0
        assert grade.bucket == "unparametrized"
        entry = bdd_full_audit.TestEntry(nodeid="t::admin_only", outcome="xpassed", longrepr="", error="")
        classified = bdd_full_audit.classify_xpass(entry, [entry])
        assert classified.category == "UNPARAMETRIZED"

    def test_conftest_harness_literals_are_harness_gap(self, audit_xfails) -> None:
        """Every live ``pytest.xfail("…harness…wired…")`` literal must classify HARNESS_GAP."""
        src = CONFTEST.read_text(encoding="utf-8")
        tree = ast.parse(src)
        literals: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = ""
            if isinstance(func, ast.Attribute) and func.attr == "xfail":
                name = "xfail"
            elif isinstance(func, ast.Name) and func.id == "xfail":
                name = "xfail"
            if name != "xfail" or not node.args:
                continue
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                literals.append(arg0.value)
            elif isinstance(arg0, ast.JoinedStr):
                # f-string — approximate with joined constant parts
                parts = []
                for v in arg0.values:
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        parts.append(v.value)
                    else:
                        parts.append("X")
                literals.append("".join(parts))
        harness_wired = [lit for lit in literals if "harness" in lit.lower() and "wired" in lit.lower()]
        assert harness_wired, "expected at least one harness+wired xfail literal in conftest"
        for lit in harness_wired:
            test = _json_report_test("/x", 1, phase="setup", message=f"_pytest.outcomes.XFailed: {lit}")
            entry = audit_xfails.classify_xfail(test, {}, [])
            assert entry.category == "HARNESS_GAP", lit

    def test_transport_re_covers_conftest_parametrize_ids(self, bdd_audit_common) -> None:
        """``_TRANSPORT_RE`` must recognize every id in the live parametrize tuple."""
        src = CONFTEST.read_text(encoding="utf-8")
        # Heuristic: ids appear as quoted tokens near pytest_generate_tests transport list.
        # Pin the known post-#1417 set explicitly + assert RE matches each.
        expected = ("a2a", "mcp", "rest", "e2e_rest")
        for tid in expected:
            assert bdd_audit_common.extract_transport(f"t::s[{tid}]") == tid
        # Also ensure e2e_* variants recognized
        for tid in ("e2e_mcp", "e2e_a2a", "impl"):
            assert bdd_audit_common.extract_transport(f"t::s[{tid}]") == tid
        assert "parametrize" in src  # conftest still owns the list

    def test_captured_slice_crash_paths_under_artifact_root(self, captured_bdd_slice) -> None:
        root = captured_bdd_slice["root"]
        assert isinstance(root, str) and root
        for test in captured_bdd_slice["tests"]:
            for phase in ("setup", "call", "teardown"):
                crash = (test.get(phase) or {}).get("crash")
                if not crash:
                    continue
                path = crash.get("path")
                assert isinstance(path, str) and path.startswith(root), (test["nodeid"], path)

    def test_summary_iterates_module_categories(self, audit_xfails, tmp_path: Path, capsys) -> None:
        """stderr SUMMARY must walk ``XFAIL_CATEGORIES`` (not a hand list)."""
        report = tmp_path / "bdd.json"
        report.write_text(
            json.dumps(
                {
                    "root": "/app",
                    "tests": [
                        {
                            "nodeid": "t::s[a2a]",
                            "outcome": "xfailed",
                            "keywords": [],
                            "setup": {
                                "outcome": "skipped",
                                "crash": {
                                    "path": "/app/tests/bdd/conftest.py",
                                    "lineno": 1,
                                    "message": "_pytest.outcomes.XFailed: No harness wired for UC-019",
                                },
                            },
                        },
                        {"nodeid": "t::other[e2e_rest]", "outcome": "passed", "keywords": []},
                    ],
                }
            )
        )
        out = tmp_path / "out.md"
        old = sys.argv
        try:
            sys.argv = ["audit_xfails.py", str(report), "--output", str(out)]
            audit_xfails.main()
        finally:
            sys.argv = old
        err = capsys.readouterr().err
        assert "HARNESS_GAP" in err
        assert audit_xfails.XFAIL_CATEGORIES == tuple(audit_xfails.CATEGORY_DESC.keys())
        assert "PARTIAL_PASS" in audit_xfails.XFAIL_CATEGORIES


class TestGraduatePendingTagPins:
    """Aug-17 #4 — tag_candidates / tag_blockers / print_report must be pinned."""

    def test_analyze_tag_candidates_and_blockers(self, graduate_pending, tmp_path: Path) -> None:
        report = tmp_path / "bdd.json"
        report.write_text(
            json.dumps(
                {
                    "tests": [
                        {
                            "nodeid": "tests/bdd/test_uc004.py::test_full[a2a]",
                            "outcome": "xpassed",
                            "keywords": ["T-UC-004-grad", "pending"],
                        },
                        {
                            "nodeid": "tests/bdd/test_uc004.py::test_full[mcp]",
                            "outcome": "xpassed",
                            "keywords": ["T-UC-004-grad", "pending"],
                        },
                        {
                            "nodeid": "tests/bdd/test_uc004.py::test_full[rest]",
                            "outcome": "xpassed",
                            "keywords": ["T-UC-004-grad", "pending"],
                        },
                        {
                            "nodeid": "tests/bdd/test_uc004.py::test_part[a2a]",
                            "outcome": "xpassed",
                            "keywords": ["T-UC-004-block", "pending"],
                        },
                        {
                            "nodeid": "tests/bdd/test_uc004.py::test_part[mcp]",
                            "outcome": "xfailed",
                            "keywords": ["T-UC-004-block", "pending"],
                        },
                        # keep force_confirm False
                        {"nodeid": "tests/bdd/test_other.py::test_e[e2e_rest]", "outcome": "passed", "keywords": []},
                    ]
                }
            )
        )
        ledger = tmp_path / "ledger.txt"
        ledger.write_text("")
        analysis = graduate_pending.analyze(str(report), ledger_path=ledger)
        assert analysis["force_confirm"] is False
        assert "T-UC-004-grad" in analysis["tag_candidates"]
        assert "T-UC-004-block" in analysis["tag_blockers"]
        assert "T-UC-004-grad" in analysis["test_tags"]["tests/bdd/test_uc004.py::test_full"]
        # firm graduate tuple is (scenario, base)
        assert any(base.endswith("::test_full") for _, base in analysis["graduate_all"])

    def test_print_report_smoke(self, graduate_pending, tmp_path: Path, capsys) -> None:
        report = tmp_path / "bdd.json"
        ledger = tmp_path / "empty.txt"
        ledger.write_text("")
        report.write_text(
            json.dumps(
                {
                    "tests": [
                        {
                            "nodeid": "tests/bdd/test_uc004.py::test_full[a2a]",
                            "outcome": "xpassed",
                            "keywords": ["T-UC-004-grad"],
                        },
                        {
                            "nodeid": "tests/bdd/test_uc004.py::test_full[mcp]",
                            "outcome": "xpassed",
                            "keywords": ["T-UC-004-grad"],
                        },
                        {
                            "nodeid": "tests/bdd/test_uc004.py::test_full[rest]",
                            "outcome": "xpassed",
                            "keywords": ["T-UC-004-grad"],
                        },
                        {"nodeid": "tests/bdd/test_other.py::test_e[e2e_rest]", "outcome": "passed", "keywords": []},
                    ]
                }
            )
        )
        analysis = graduate_pending.analyze(str(report), ledger_path=ledger)
        graduate_pending.print_report(analysis)
        out = capsys.readouterr().out
        assert out
        assert "T-UC-004-grad" in out or "Graduate" in out or "graduate" in out.lower()


class TestInspectParallelCliContract:
    """Aug-17 — ``--features-dir`` must stay accepted if the wrapper passes it."""

    def test_inspect_parallel_flags_accepted_by_parser(self) -> None:
        wrapper = SCRIPTS / "inspect_parallel.sh"
        text = wrapper.read_text(encoding="utf-8")
        src = (SCRIPTS / "inspect_bdd_steps.py").read_text(encoding="utf-8")
        assert "--features-dir" in text
        assert '"--features-dir"' in src
