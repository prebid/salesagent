"""Regression tests for the liveness join in storyboard_check_index.build().

storyboard_check_index derived ``covered_by`` from tag presence alone: a
``@storyboard-v3.1``-tagged scenario with zero bound step definitions counted as
"covered" forever (the finding). This file proves the fix: two
published headline numbers (``claimed-by-a-scenario`` vs ``graded-by-a-live-scenario``),
and a ``graduation_candidate`` flag on entries the BDD suite locally xfails as a known
gap that the real conformance ledger does not currently measure as failing.

The real, exhaustive proof needs the live pinned compliance tree (``build()`` reads
per-check text that the vendored offline fixture doesn't carry) — same
``requires_pinned_bundle`` contract as this repo's other storyboard_check_index consumers
(``test_architecture_storyboard_wireability.py``, ``test_architecture_storyboard_ledger.py``).
Liveness itself is injected via ``scenario_liveness_join.build_index``'s
``artifact_path``/``env_routes`` parameters (monkeypatched), so this test needs neither a
real BDD run nor a real ``ENV_ROUTES`` registry entry — only the real check/storyboard
join, which is the thing under test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(REPO_ROOT))

from scripts.audit import scenario_liveness_join, storyboard_check_index, storyboard_spec  # noqa: E402
from tests.unit._storyboard_guard_env import (  # noqa: E402
    ADCP_HOME,
    requires_pinned_bundle,
)

# Captured before any test monkeypatches scenario_liveness_join.load_artifact --
# tests below patch that name to a fixed-path lambda, so the lambda body must call
# the ORIGINAL function object, not the (by-then-patched) module attribute.
_real_load_artifact = scenario_liveness_join.load_artifact


class _Route:
    """A stand-in EnvRoute row. The join resolves through
    ``storyboard_spec.resolve_env_route``, which reads ``when``/``uc`` off a row."""

    def __init__(self, xfail_reason: str | None = None, *, uc: str | None = None, when=None) -> None:
        self.xfail_reason = xfail_reason
        self.uc = uc
        self.when = when


@requires_pinned_bundle
def test_no_artifact_publishes_zero_graded_and_zero_candidates(monkeypatch, tmp_path: Path) -> None:
    """Baseline: with no BDD run this session, nothing can be claimed graded or a
    graduation candidate -- missing measurement must render as a gap, not a guess.

    The absent artifact is pinned to a tmp path instead of being assumed. The
    default location, ``test-results/bdd_scenario_liveness.json``, is gitignored,
    so this test used to pass on a fresh clone and FAIL on any checkout where a
    real BDD run had ever written one (observed: with_live_scenario == 89). That
    made the result a function of ambient filesystem state rather than of the
    behaviour under test -- the same defect shape as the storyboard grader that
    collected a whole directory and inherited its npm state.
    """
    monkeypatch.setenv(storyboard_spec.ARTIFACT_ENV_VAR, str(tmp_path / "no-such-liveness-artifact.json"))
    result = storyboard_check_index.build(REPO_ROOT, ADCP_HOME)
    assert result["totals"]["with_scenario"] > 0, "fixture broken: no on-path check is claimed by any scenario"
    assert result["totals"]["with_live_scenario"] == 0
    assert result["totals"]["graduation_candidates"] == 0
    assert all(not r["graded_by_live_scenario"] for r in result["records"])
    assert all(not r["graduation_candidate"] for r in result["records"])


@requires_pinned_bundle
def test_registry_wired_scenario_grades_its_claimed_checks(monkeypatch, tmp_path: Path) -> None:
    """A steps-bound scenario whose UC bucket IS a registry row grades every check
    its storyboard claims -- the actual join, not a bool the fixture hands us."""
    scenario_id = "T-UC-019-storyboard-post-create-status-poll"
    artifact = tmp_path / "liveness.json"
    artifact.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "scenario_id": scenario_id,
                        "steps_bound": True,
                        "ledgered": False,
                        # Routing keys on the MARKER SET, and the artifact
                        # record is the join's only source for it.
                        "marker_names": [scenario_id],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        scenario_liveness_join, "load_artifact", lambda _path, _fixed=artifact: _real_load_artifact(_fixed)
    )
    monkeypatch.setattr(scenario_liveness_join, "load_env_routes", lambda: [_Route(uc="UC-019")])

    result = storyboard_check_index.build(REPO_ROOT, ADCP_HOME)
    claiming = [r for r in result["records"] if scenario_id in r["scenarios"]]
    assert claiming, "fixture broken: no on-path check is claimed by T-UC-019-storyboard-post-create-status-poll"
    assert all(r["graded_by_live_scenario"] for r in claiming)
    assert all(r["scenario_liveness"][scenario_id]["registry_wired"] is True for r in claiming)
    assert result["totals"]["with_live_scenario"] >= len(claiming)


@requires_pinned_bundle
def test_steps_bound_without_registry_row_does_not_grade(monkeypatch, tmp_path: Path) -> None:
    """steps_bound alone (the old 4-of-21 hand measurement) is not enough -- a UC
    bucket absent from ENV_ROUTES must stay ungraded even when its steps run for real."""
    scenario_id = "T-UC-006-storyboard-multi-format-sync"
    artifact = tmp_path / "liveness.json"
    artifact.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "scenario_id": scenario_id,
                        "steps_bound": True,
                        "ledgered": False,
                        # Routing keys on the MARKER SET, and the artifact
                        # record is the join's only source for it.
                        "marker_names": [scenario_id],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        scenario_liveness_join, "load_artifact", lambda _path, _fixed=artifact: _real_load_artifact(_fixed)
    )
    monkeypatch.setattr(scenario_liveness_join, "load_env_routes", lambda: [_Route(uc="UC-019")])  # UC-006 absent

    result = storyboard_check_index.build(REPO_ROOT, ADCP_HOME)
    claiming = [r for r in result["records"] if scenario_id in r["scenarios"]]
    assert claiming, "fixture broken: no on-path check is claimed by T-UC-006-storyboard-multi-format-sync"
    assert all(not r["graded_by_live_scenario"] for r in claiming)
    assert all(r["scenario_liveness"][scenario_id]["registry_wired"] is False for r in claiming)


def _ledgered_scenario_result(monkeypatch, tmp_path: Path, scenario_id: str, *, exercised: set[str] | None = None):
    """``build()`` with one scenario reporting ``ledgered=True`` and no harness wiring.

    ``exercised`` stands in for the conformance ledger holding a row for those
    storyboards — the only evidence this repo carries that a run reached them.
    """
    artifact = tmp_path / "liveness.json"
    artifact.write_text(
        json.dumps({"scenarios": [{"scenario_id": scenario_id, "steps_bound": True, "ledgered": True}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        scenario_liveness_join, "load_artifact", lambda _path, _fixed=artifact: _real_load_artifact(_fixed)
    )
    monkeypatch.setattr(scenario_liveness_join, "load_env_routes", lambda: [])  # not registry-wired at all
    if exercised is not None:
        monkeypatch.setattr(storyboard_check_index, "_exercised_storyboards", lambda _repo: exercised)
    else:
        # PIN THE OTHER INPUT TOO. `_exercised_storyboards` prefers a published
        # storyboard_collected.json (salesagent-v03pe.3) and falls back to the failure
        # ledger only when none exists. test-results/ is gitignored, so whether that file
        # is on disk depends on whether someone has run the storyboard suite in this
        # worktree -- and a partial artifact from a degraded run narrows the exercised set.
        # A guard whose verdict moves with ambient disk state is not a guard, so point the
        # reader at a path that cannot exist and let it use the committed ledger, which is
        # the input these cases are actually about.
        monkeypatch.setattr(
            storyboard_check_index, "_collected_artifact_path", lambda _repo: tmp_path / "no-such-artifact.json"
        )
    return storyboard_check_index.build(REPO_ROOT, ADCP_HOME)


@requires_pinned_bundle
def test_ledgered_scenario_marks_graduation_candidate_when_a_run_reached_the_storyboard(
    monkeypatch, tmp_path: Path
) -> None:
    """A scenario ledgered as a known gap, for a check the real conformance run REACHED
    and did not measure FAILING, is a graduation candidate -- independent of wiring."""
    scenario_id = "T-UC-019-storyboard-post-create-status-poll"
    result = _ledgered_scenario_result(monkeypatch, tmp_path, scenario_id, exercised={"media_buy_seller"})

    candidates = [
        r
        for r in result["records"]
        if scenario_id in r["scenarios"] and r["measured"] == storyboard_check_index.MEASURED_NOT_FAILING
    ]
    assert candidates, "fixture broken: no reached-and-not-failing check is claimed by T-UC-019-...-poll"
    assert all(r["graduation_candidate"] for r in candidates)
    # Not registry-wired -> not graded -- graduation_candidate does not require it.
    assert all(not r["graded_by_live_scenario"] for r in candidates)
    assert result["totals"]["graduation_candidates"] >= len(candidates)

    rendered = storyboard_check_index.render(result)
    assert "## 4. Graduation candidates" in rendered
    assert f"`{scenario_id}`" in rendered.split("## 4. Graduation candidates")[1].split("## 5.")[0]


@requires_pinned_bundle
def test_a_ledgered_scenario_on_an_unmeasured_check_is_not_a_graduation_candidate(monkeypatch, tmp_path: Path) -> None:
    """The salesagent-b341x.25 defect: a check nobody has been shown to run was offered
    as ready to graduate.

    The ledger records FAILURES only, so "no row" covers both "the run graded this and
    it passed" and "no run ever reached it". Measured at the 3.1.1 pin this is not a
    corner case -- ALL 51 checks this scenario claims belong to
    ``protocols/media-buy/index.yaml``, for which the ledger holds no row at all, and
    not one of the 20 claiming scenarios in the tree claims a storyboard a run has been
    shown to reach. Every graduation candidate the old report could produce was over an
    unmeasured check.
    """
    scenario_id = "T-UC-019-storyboard-post-create-status-poll"
    result = _ledgered_scenario_result(monkeypatch, tmp_path, scenario_id)

    unmeasured = [
        r
        for r in result["records"]
        if scenario_id in r["scenarios"] and r["measured"] == storyboard_check_index.MEASURED_NOT_MEASURED
    ]
    assert unmeasured, "fixture broken: this scenario's claims should all be NOT MEASURED at the 3.1.1 pin"
    assert not any(r["graduation_candidate"] for r in unmeasured), (
        "a check whose storyboard the conformance ledger has no row for has not been shown to "
        "run, so there is no run to graduate it against. Offering it puts checks nobody has "
        "graded on a list a human is meant to act on."
    )


@requires_pinned_bundle
def test_ungradable_checks_are_never_graduation_candidates(monkeypatch, tmp_path: Path) -> None:
    """comply_test_controller-gated checks can never graduate -- excluded even when
    their claiming scenario is ledgered, because 'ungradable' != 'no ledger entry'."""
    scenario_id = "T-UC-019-storyboard-post-create-status-poll"
    artifact = tmp_path / "liveness.json"
    artifact.write_text(
        json.dumps({"scenarios": [{"scenario_id": scenario_id, "steps_bound": True, "ledgered": True}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        scenario_liveness_join, "load_artifact", lambda _path, _fixed=artifact: _real_load_artifact(_fixed)
    )
    monkeypatch.setattr(scenario_liveness_join, "load_env_routes", lambda: [])

    result = storyboard_check_index.build(REPO_ROOT, ADCP_HOME)
    ungradable = [r for r in result["records"] if scenario_id in r["scenarios"] and r["requires_controller"]]
    if not ungradable:
        pytest.skip("no controller-gated check claimed by T-UC-019-...-poll in the current pinned tree")
    assert all(not r["graduation_candidate"] for r in ungradable)
