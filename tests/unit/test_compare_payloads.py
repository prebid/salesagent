"""A missing measurement must FAIL, and the tolerance must be a rule.

``compare_payloads.py`` exists because outcome identity cannot see a silent repair.
It is only worth having if two things hold, and both are graded here:

* NOT_MEASURED EXITS 1. A nodeid the baseline recorded and this run did not is
  never "no change" and never a skip. That is the defect salesagent-b341x.18
  removed from the conformance census; reintroducing it inside the gate built to
  catch silent repairs would be indefensible.
* THE TRANSPORT-TWIN TOLERANCE IS DERIVED. Two runs of the same code disagree on a
  handful of nodeids, always in the transport parameter. The rule is "compare a
  disappeared nodeid against its transport-stripped twin", the observed count
  appears nowhere in the code, and — the trap that was actually walked into — the
  token is sometimes the WHOLE parameter id (``[mcp]``) and sometimes the first of
  several (``[mcp-example-3]``). Both forms are pinned below; a stripper written
  for only the second reports false NOT_MEASURED rows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.audit.compare_payloads import PAYLOAD_SUBDIR, _strip_transport, main

#: No ``collected`` key: ``_write`` derives it from the rows unless a test states
#: one, which is how the "unfinished run" case says what it means.
FULL_SCOPE = {"selection": "", "markers": "", "deselected": 0, "workers": 0}


def _write(directory: Path, nodes: dict[str, Any], scope: dict[str, Any] | None = None) -> None:
    payloads = directory / PAYLOAD_SUBDIR
    payloads.mkdir(parents=True, exist_ok=True)
    run = dict(scope or FULL_SCOPE)
    run.setdefault("collected", len(nodes))
    (payloads / "bdd_inprocess.json").write_text(json.dumps({"run": run, "nodes": nodes}))


@pytest.fixture
def runs(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "before", tmp_path / "after"


# ── The three verdicts ───────────────────────────────────────────────


def test_identical_payloads_are_clean(runs: tuple[Path, Path], capsys: pytest.CaptureFixture) -> None:
    before, after = runs
    nodes = {"t::a[mcp]": [{"creative_id": "c1"}], "t::b[rest]": []}
    _write(before, nodes)
    _write(after, nodes)
    assert main([str(before), str(after)]) == 0
    assert "CLEAN" in capsys.readouterr().out


def test_a_changed_payload_is_dirty_and_names_the_field(runs: tuple[Path, Path], capsys: pytest.CaptureFixture) -> None:
    before, after = runs
    _write(before, {"t::a[mcp]": [{"creative_id": "c1", "name": ""}], "t::b[rest]": []})
    _write(after, {"t::a[mcp]": [{"creative_id": "c1", "name": "Repaired"}], "t::b[rest]": []})
    assert main([str(before), str(after)]) == 1
    out = capsys.readouterr().out
    assert "DIRTY" in out
    assert "name: '' -> 'Repaired'" in out


def test_a_nodeid_the_baseline_recorded_and_this_run_did_not_fails(
    runs: tuple[Path, Path], capsys: pytest.CaptureFixture
) -> None:
    before, after = runs
    _write(before, {"t::a[mcp]": [], "t::gone[rest]": []})
    _write(after, {"t::a[mcp]": []})
    assert main([str(before), str(after)]) == 1
    out = capsys.readouterr().out
    assert "NOT_MEASURED" in out
    assert "t::gone[rest]" in out


def test_an_absent_artifact_is_not_measured_rather_than_skipped(
    runs: tuple[Path, Path], capsys: pytest.CaptureFixture
) -> None:
    before, after = runs
    _write(before, {"t::a[mcp]": []})
    (after / PAYLOAD_SUBDIR).mkdir(parents=True)
    (after / PAYLOAD_SUBDIR / "bdd_inprocess.json").unlink(missing_ok=True)
    assert main([str(before), str(after), "bdd_inprocess.json"]) == 1
    assert "NOT_MEASURED" in capsys.readouterr().out


# ── The tolerance, as a rule ─────────────────────────────────────────


def test_the_transport_token_is_stripped_in_both_forms() -> None:
    """``[mcp]`` is the whole parameter id; ``[mcp-...]`` is the first of several.
    A stripper written as ``\\[(a2a|mcp|rest)-`` misses the first, and the twin sets
    then read as not identical — a false NOT_MEASURED."""
    assert _strip_transport("t::test_brandblock[mcp]") == "t::test_brandblock[<t>]"
    assert _strip_transport("t::test_caps[rest-Example-3]") == "t::test_caps[<t>-Example-3]"
    assert _strip_transport("t::test_plain") == "t::test_plain"


def test_a_flapped_nodeid_is_compared_through_its_twin_not_excused(runs: tuple[Path, Path]) -> None:
    """The scenario ran; only its transport parameter moved. Comparing it is right —
    EXCUSING it would let a payload change hide behind a rotating parameter id."""
    before, after = runs
    _write(before, {"t::c[mcp]": [{"x": 1}]})
    _write(after, {"t::c[rest]": [{"x": 1}]})
    assert main([str(before), str(after)]) == 0

    _write(after, {"t::c[rest]": [{"x": 2}]})
    assert main([str(before), str(after)]) == 1


def test_a_disappearance_with_no_twin_is_not_measured(runs: tuple[Path, Path], capsys: pytest.CaptureFixture) -> None:
    before, after = runs
    _write(before, {"t::c[mcp]": [{"x": 1}]})
    _write(after, {"t::different[rest]": [{"x": 1}]})
    assert main([str(before), str(after)]) == 1
    out = capsys.readouterr().out
    assert "NOT_MEASURED" in out and "t::c[mcp]" in out
    # The other side of the same split, reported as accounting rather than as a verdict:
    # a nodeid with no earlier payload cannot have silently diverged from one.
    assert "NEW (this run recorded it, baseline did not" in out
    assert "t::different[rest]" in out


# ── Refusals ─────────────────────────────────────────────────────────


def test_an_empty_artifact_is_refused_rather_than_read_as_agreement(runs: tuple[Path, Path]) -> None:
    before, after = runs
    _write(before, {"t::a[mcp]": []})
    _write(after, {}, scope={**FULL_SCOPE, "collected": 0})
    assert main([str(before), str(after)]) == 1


def test_an_unfinished_run_is_refused(runs: tuple[Path, Path], capsys: pytest.CaptureFixture) -> None:
    """Fewer rows than the session collected: the run died partway, so the nodeids it
    never reached are silences, not measurements."""
    before, after = runs
    _write(before, {"t::a[mcp]": [], "t::b[mcp]": []})
    _write(after, {"t::a[mcp]": []}, scope={**FULL_SCOPE, "collected": 2})
    assert main([str(before), str(after)]) == 1
    assert "did not finish" in capsys.readouterr().out


def test_two_runs_that_answered_different_questions_are_refused(
    runs: tuple[Path, Path], capsys: pytest.CaptureFixture
) -> None:
    """A narrowed run is a valid measurement of its own scope — ``bdd_e2e`` IS
    ``-k "e2e_rest or e2e_admin"``. Two DIFFERENT scopes is what cannot be compared."""
    before, after = runs
    _write(before, {"t::a[mcp]": []}, scope={**FULL_SCOPE, "selection": "e2e_rest"})
    _write(after, {"t::a[mcp]": []}, scope={**FULL_SCOPE, "selection": "storyboard"})
    assert main([str(before), str(after)]) == 1
    assert "different questions" in capsys.readouterr().out


def test_the_same_narrow_scope_on_both_sides_compares(runs: tuple[Path, Path]) -> None:
    before, after = runs
    scope = {**FULL_SCOPE, "selection": "e2e_rest"}
    _write(before, {"t::a[e2e_rest]": [{"x": 1}]}, scope=scope)
    _write(after, {"t::a[e2e_rest]": [{"x": 1}]}, scope=scope)
    assert main([str(before), str(after)]) == 0
