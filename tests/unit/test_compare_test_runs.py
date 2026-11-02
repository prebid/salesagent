"""A run-to-run diff that only counts new failures misses the worst regression.

``passed -> failed`` is what a red suite already tells you. ``passed -> xfailed``
is what nothing tells you: a test that stopped grading while every layer stayed
green. A merge in this repo once reverted 205 passing scenarios exactly that way.
``scripts/compare_test_runs.py`` derives the fallout between two report sets, and
these tests plant each transition — in both directions — and check it is found.

The point of deriving rather than declaring: the fallout list is a dict diff over
``test-results/<tag>/*.json``, which every suite already writes. There is no
ledger to maintain and nothing to drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.compare_test_runs import report, transitions

pytestmark = [pytest.mark.unit]


def _write(results_dir: Path, suite: str, **outcome_by_test: str) -> None:
    """Write one suite report holding just the fields the differ reads."""
    results_dir.mkdir(parents=True, exist_ok=True)
    tests = [{"nodeid": nodeid, "outcome": outcome} for nodeid, outcome in outcome_by_test.items()]
    (results_dir / f"{suite}.json").write_text(json.dumps({"tests": tests}), encoding="utf-8")


def _outcomes(results_dir: Path) -> dict:
    from scripts._suite_reports import scan_suite_outcomes

    return scan_suite_outcomes(str(results_dir))


def test_every_planted_transition_is_found_in_both_directions(tmp_path: Path):
    """Forward AND reverse. A tool that reports only regressions is half a tool."""
    _write(tmp_path / "old", "unit", a="passed", b="passed", c="xfailed", d="skipped", e="passed")
    _write(tmp_path / "new", "unit", a="failed", b="xfailed", c="passed", d="passed", e="passed")

    grouped = transitions(_outcomes(tmp_path / "old"), _outcomes(tmp_path / "new"))

    assert grouped[("passed", "failed")] == [("unit", "a")]
    assert grouped[("passed", "xfailed")] == [("unit", "b")]
    assert grouped[("xfailed", "passed")] == [("unit", "c")]
    assert grouped[("skipped", "passed")] == [("unit", "d")]
    assert grouped[("passed", "passed")] == [("unit", "e")]


def test_the_silent_coverage_regression_is_called_out_by_name(tmp_path: Path, capsys):
    """passed -> xfailed must be labelled, not just counted among the rest.

    It is the transition that leaves the suite green, so it is the one a reader
    scanning the output will otherwise walk past.
    """
    _write(tmp_path / "old", "bdd_inprocess", scenario="passed")
    _write(tmp_path / "new", "bdd_inprocess", scenario="xfailed")

    report(str(tmp_path / "old"), str(tmp_path / "new"), limit=40)

    printed = capsys.readouterr().out
    assert "passed -> xfailed: 1" in printed
    assert "SILENT COVERAGE REGRESSION" in printed
    assert "bdd_inprocess: scenario" in printed


def test_a_test_that_disappeared_is_reported_not_dropped(tmp_path: Path, capsys):
    """Deleting a test and xfailing it remove the same amount of grading."""
    _write(tmp_path / "old", "unit", stays="passed", vanishes="passed")
    _write(tmp_path / "new", "unit", stays="passed", arrives="passed")

    report(str(tmp_path / "old"), str(tmp_path / "new"), limit=40)

    printed = capsys.readouterr().out
    assert "ONLY IN OLD (removed since OLD): 1" in printed
    assert "unit: vanishes" in printed
    assert "ONLY IN NEW (added since OLD): 1" in printed
    assert "unit: arrives" in printed


def test_the_same_nodeid_in_two_suites_is_two_tests(tmp_path: Path):
    """Joining on nodeid alone would let one suite's result mask another's."""
    _write(tmp_path / "old", "bdd_inprocess", shared="passed")
    _write(tmp_path / "old", "bdd_e2e", shared="passed")
    _write(tmp_path / "new", "bdd_inprocess", shared="passed")
    _write(tmp_path / "new", "bdd_e2e", shared="failed")

    grouped = transitions(_outcomes(tmp_path / "old"), _outcomes(tmp_path / "new"))

    assert grouped[("passed", "failed")] == [("bdd_e2e", "shared")]
    assert grouped[("passed", "passed")] == [("bdd_inprocess", "shared")]


def test_a_results_dir_with_no_outcomes_is_refused(tmp_path: Path):
    """Silence must not read as "nothing changed" — that is the wrong answer."""
    (tmp_path / "empty").mkdir()

    with pytest.raises(ValueError, match="no per-test outcomes"):
        _outcomes(tmp_path / "empty")
