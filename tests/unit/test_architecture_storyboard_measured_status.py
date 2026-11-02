"""Structural guard: "no ledger entry" is not a pass, and a zero is not a measurement.

Two residual proxies in the two most careful instruments in ``scripts/audit/``. Both
already carry the honest three-state shape this epic (salesagent-v03pe) is building;
these are the seams where a derived value collapsed it again.

**1. ``storyboard_check_index``: "no ledger entry" read as passing.**
``tests/storyboard/known_failures.txt`` records FAILURES only — its own header says so
— so an empty result covers BOTH "the in-network run graded this and it passed" and
"no run ever reached this check". Measured at the 3.1.1 pin, 427 graded checks read
"no ledger entry", and 355 of them (83%) belonged to storyboards the ledger holds no
row for at all. Those 355 were offered to every reader as clean, and the derived
``graduation_candidate`` offered a subset of them as ready to graduate — a check
nobody has been shown to run, on a list a human is meant to act on.

The ledger does establish ONE thing: a run got as far as any storyboard it holds a
row for. So the column splits into ``not failing`` (a run reached this storyboard and
this check is not among its failures) and ``NOT MEASURED`` (no row anywhere for the
storyboard, so nothing has been established).

**2. ``storyboard_check_index``: "graded by a LIVE scenario: 0" with no run behind it.**
Without ``test-results/bdd_scenario_liveness.json`` every scenario reports
``measured_this_run=False``, so the total is 0 — indistinguishable in the report from
"a run happened and nothing is live". The flag now travels with the number.

NOT graded here, and deliberately: ``scenario_liveness_join.registry_wired`` returning
False for a UC with no ``ENV_ROUTES`` row. salesagent-b341x.25 filed that as a
two-state boolean over a three-state reality; it is not. ``tests/bdd/conftest.py``
routes on the SAME ``storyboard_spec.resolve_env_route`` and, when it returns None,
calls ``pytest.xfail("No harness wired for ...")``. The scenario grades nothing, so
``registry_wired=False`` is a measured statement about production behaviour, not an
absence of measurement.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.audit import storyboard_check_index as index  # noqa: E402
from tests.unit._storyboard_guard_env import ADCP_HOME, requires_pinned_bundle  # noqa: E402

_ALL_MEASURED = {
    index.MEASURED_FAILING,
    index.MEASURED_NOT_FAILING,
    index.MEASURED_NOT_MEASURED,
    index.MEASURED_UNGRADABLE,
    index.MEASURED_GATED,
}


def test_a_storyboard_no_run_reached_is_not_measured_rather_than_clean() -> None:
    """The defect, at the verdict step: absence of a failure row is not a pass."""
    verdict = index.measured_status(gated=False, failing=False, step_controller=False, storyboard_exercised=False)
    assert verdict == index.MEASURED_NOT_MEASURED, (
        "a check whose storyboard the ledger has no row for has not been shown to run. "
        "Reporting it as anything other than NOT MEASURED folds an unmeasured check into "
        "the passing side, which is what 'no ledger entry' did for 355 of 759 graded checks."
    )


def test_a_run_that_reached_the_storyboard_yields_a_different_verdict() -> None:
    """The other half of the split — otherwise NOT MEASURED would just be a rename."""
    reached = index.measured_status(gated=False, failing=False, step_controller=False, storyboard_exercised=True)
    assert reached == index.MEASURED_NOT_FAILING
    assert reached != index.MEASURED_NOT_MEASURED, (
        "'a run reached this storyboard and did not fail this check' and 'no run has been "
        "shown to reach it' must be distinguishable — collapsing them is the original defect"
    )


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"gated": True, "failing": True, "step_controller": True, "storyboard_exercised": True}, "MEASURED_GATED"),
        ({"gated": False, "failing": True, "step_controller": True, "storyboard_exercised": True}, "MEASURED_FAILING"),
        (
            {"gated": False, "failing": False, "step_controller": True, "storyboard_exercised": True},
            "MEASURED_UNGRADABLE",
        ),
    ],
)
def test_the_precedence_is_the_one_the_column_documents(kwargs: dict, expected: str) -> None:
    """A ledgered failure outranks the controller gate: it PROVES the run reached the
    assertions, so reporting `ungradable` there would discard a real measurement."""
    assert index.measured_status(**kwargs) == getattr(index, expected)


def test_every_verdict_the_function_can_return_is_in_the_vocabulary() -> None:
    """A verdict outside the vocabulary is a value no total counts."""
    produced = {
        index.measured_status(gated=gated, failing=failing, step_controller=controller, storyboard_exercised=exercised)
        for gated in (True, False)
        for failing in (True, False)
        for controller in (True, False)
        for exercised in (True, False)
    }
    assert produced <= _ALL_MEASURED, f"undeclared verdict(s): {sorted(produced - _ALL_MEASURED)}"
    assert produced == _ALL_MEASURED, (
        f"declared but unreachable verdict(s): {sorted(_ALL_MEASURED - produced)}. A status in the "
        "vocabulary that no input produces publishes a structural zero, which reads as a finding."
    )


@requires_pinned_bundle
def test_the_measured_statuses_partition_the_graded_set() -> None:
    """Published totals, over the real pin: every graded check in exactly one status."""
    result = index.build(REPO_ROOT, ADCP_HOME)
    totals = result["totals"]
    counted = totals["failing"] + totals["not_failing"] + totals["not_measured"] + totals["ungradable"]
    assert counted == totals["graded_checks"], (
        f"the four measured statuses account for {counted} of {totals['graded_checks']} graded "
        "checks. A check in the denominator and in no status is a check nobody reports."
    )
    assert totals["not_measured"] > 0, (
        "at the 3.1.1 pin 355 graded checks belong to storyboards the ledger has no row for. "
        "A zero here means the split stopped working, not that everything is measured."
    )


@requires_pinned_bundle
def test_an_unmeasured_check_is_never_a_graduation_candidate() -> None:
    """Graduation needs a run to graduate against."""
    result = index.build(REPO_ROOT, ADCP_HOME)
    offenders = sorted(
        f"{r['storyboard_id']}::{r['step_id']}"
        for r in result["records"]
        if r["graduation_candidate"] and r["measured"] != index.MEASURED_NOT_FAILING
    )
    assert offenders == [], (
        f"{len(offenders)} check(s) are offered as graduation candidates without a run behind "
        "them. The xpass-graduation workflow inspects production per scenario; a check nobody "
        "has been shown to grade is an unmeasured check, not a candidate:\n" + "\n".join(f"  {o}" for o in offenders)
    )


@requires_pinned_bundle
def test_the_report_refuses_to_print_a_liveness_number_it_did_not_measure(monkeypatch) -> None:
    """With no BDD run joined, "graded by a LIVE scenario: 0" is not a measurement."""
    result = index.build(REPO_ROOT, ADCP_HOME)

    unmeasured = index.render({**result, "liveness_measured": False})
    assert "graded by a LIVE scenario: **NOT MEASURED**" in unmeasured, (
        "with no liveness artifact joined the report must say NOT MEASURED. A 0 there is "
        "indistinguishable from 'a run happened and nothing is live'."
    )

    measured = index.render({**result, "liveness_measured": True})
    assert "graded by a LIVE scenario (steps bound" in measured
    assert f"of {result['totals']['with_scenario']} claimed" in measured, (
        "when it IS measured the number must carry its denominator — the claimed set, not the "
        "graded set, is what a liveness count is quoted over"
    )
