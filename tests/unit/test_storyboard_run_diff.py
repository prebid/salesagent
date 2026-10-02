"""The storyboard run diff compares passing SETS, and a lost pass is a failure.

Two properties carry the whole tool, and both have a way of going wrong silently:

* a skip marker must not count as a pass — otherwise a tree that SKIPPED more storyboards
  scores higher than one that executed them, which is how a regression reads as progress;
* a step that passed on the base and not on the head must make the tool exit non-zero,
  because that is the only thing it exists to catch.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.audit.storyboard_run_diff import _steps, main


def _record(path: Path, steps: list[tuple[str, str, str, bool]], *, with_ids: bool = True) -> None:
    """Write a runner-shaped record holding exactly *steps* (track, scenario, task, passed).

    Each step gets a distinct ``step_id`` unless *with_ids* is False, which is how a record
    predating the id field is simulated.
    """
    tracks: dict[str, dict] = {}
    for index, (track, scenario, task, passed) in enumerate(steps):
        t = tracks.setdefault(track, {"track": track, "scenarios": []})
        sc = next((s for s in t["scenarios"] if s["scenario"] == scenario), None)
        if sc is None:
            sc = {"scenario": scenario, "steps": []}
            t["scenarios"].append(sc)
        step: dict = {"task": task, "passed": passed}
        if with_ids:
            step["step_id"] = f"s{index}"
        sc["steps"].append(step)
    path.write_text(json.dumps({"tracks": list(tracks.values())}))


def test_skip_marker_is_not_counted_as_a_pass(tmp_path: Path) -> None:
    rec = tmp_path / "storyboard_run_mcp.json"
    _record(
        rec,
        [
            ("core", "webhook_emission/requirement_unmet", "Storyboard skipped: requires 'x'", True),
            ("core", "capability_discovery", "get_adcp_capabilities", True),
        ],
    )
    steps = _steps(rec)
    assert len(steps) == 1, (
        "a requirement_unmet marker is the runner reporting a successful SKIP, not a graded pass; "
        f"counting it rewards skipping. got {sorted(steps)}"
    )
    # Asserted on the SCENARIO, not on the whole key: the key carries a step_id now, and a
    # test that pins the key's spelling grades the keying rather than the exclusion.
    assert "capability_discovery" in next(iter(steps)), sorted(steps)
    assert not [k for k in steps if "requirement_unmet" in k], sorted(steps)


def test_a_lost_pass_exits_nonzero(tmp_path: Path, monkeypatch, capsys) -> None:
    base, head = tmp_path / "base", tmp_path / "head"
    base.mkdir()
    head.mkdir()
    _record(base / "storyboard_run_mcp.json", [("core", "s", "kept", True), ("core", "s", "dropped", True)])
    _record(head / "storyboard_run_mcp.json", [("core", "s", "kept", True), ("core", "s", "dropped", False)])

    monkeypatch.setattr("sys.argv", ["diff", str(base), str(head), "--protocol", "mcp"])
    assert main() == 1, "a step that passed on the base and fails on the head is the regression this catches"
    # The second step's id, which is what the key names now.
    assert "core::s::s1" in capsys.readouterr().out


def test_gaining_passes_is_not_a_failure(tmp_path: Path, monkeypatch) -> None:
    """A head that grades more steps than its base has more passes AND more failures."""
    base, head = tmp_path / "base", tmp_path / "head"
    base.mkdir()
    head.mkdir()
    _record(base / "storyboard_run_mcp.json", [("core", "s", "kept", True)])
    _record(head / "storyboard_run_mcp.json", [("core", "s", "kept", True), ("core", "s", "extra", True)])

    monkeypatch.setattr("sys.argv", ["diff", str(base), str(head), "--protocol", "mcp"])
    assert main() == 0


def test_a_missing_record_is_loud(tmp_path: Path, monkeypatch, capsys) -> None:
    """A protocol with no record was NOT compared, and must not be reported as agreement."""
    base, head = tmp_path / "base", tmp_path / "head"
    base.mkdir()
    head.mkdir()
    _record(base / "storyboard_run_mcp.json", [("core", "s", "kept", True)])

    monkeypatch.setattr("sys.argv", ["diff", str(base), str(head), "--protocol", "mcp"])
    assert main() == 2
    assert "no storyboard record" in capsys.readouterr().err


def test_steps_sharing_a_task_keep_separate_identities(tmp_path: Path) -> None:
    """THE DEFECT: a repeated task used to collapse into one entry, last-write-wins.

    Measured over the 181 pinned storyboards at 3.1.1, 427 of 1155 steps share a task with a
    sibling — ``deterministic_testing`` alone has 19 named ``comply_test_controller``. Under
    the task key those 19 were one entry, so a step that regressed while a sibling passed was
    invisible, which is the only thing this tool exists to catch.
    """
    rec = tmp_path / "storyboard_run_mcp.json"
    _record(
        rec,
        [
            ("mb", "invalid_transitions", "update_media_buy", True),
            ("mb", "invalid_transitions", "update_media_buy", True),
            ("mb", "invalid_transitions", "update_media_buy", False),
        ],
    )
    steps = _steps(rec)
    assert len(steps) == 3, f"three steps named one task collapsed into {len(steps)} identit(ies): {steps}"
    assert sorted(steps.values()) == [False, True, True], steps


def test_a_lost_pass_among_siblings_sharing_a_task_is_caught(tmp_path: Path, monkeypatch) -> None:
    """The regression the collapse concealed: one of three same-named steps stops passing."""
    base, head = tmp_path / "base", tmp_path / "head"
    base.mkdir()
    head.mkdir()
    three = [("mb", "invalid_transitions", "update_media_buy", True)] * 3
    _record(base / "storyboard_run_mcp.json", three)
    _record(
        head / "storyboard_run_mcp.json",
        [
            ("mb", "invalid_transitions", "update_media_buy", True),
            ("mb", "invalid_transitions", "update_media_buy", False),
            ("mb", "invalid_transitions", "update_media_buy", True),
        ],
    )

    monkeypatch.setattr("sys.argv", ["diff", str(base), str(head), "--protocol", "mcp"])
    assert main() == 1, "a sibling that stopped passing must be a lost pass, not an equal count"


def test_one_missing_protocol_record_is_fatal(tmp_path: Path, monkeypatch, capsys) -> None:
    """A run that published one protocol and lost the other yields NO verdict.

    This printed the error and exited 0 whenever any protocol had survived, so a
    half-measured run read green — the thing the error message itself warns about.
    """
    base, head = tmp_path / "base", tmp_path / "head"
    base.mkdir()
    head.mkdir()
    for d in (base, head):
        _record(d / "storyboard_run_mcp.json", [("core", "s", "kept", True)])
    # a2a is absent from both

    monkeypatch.setattr("sys.argv", ["diff", str(base), str(head)])
    assert main() == 2
    assert "no storyboard record" in capsys.readouterr().err


def test_a_zero_graded_comparison_is_refused(tmp_path: Path, monkeypatch, capsys) -> None:
    """Two empty sets are equal, which is the absence of a measurement rather than agreement."""
    base, head = tmp_path / "base", tmp_path / "head"
    base.mkdir()
    head.mkdir()
    for d in (base, head):
        _record(d / "storyboard_run_mcp.json", [("core", "webhook/requirement_unmet", "Storyboard skipped: x", True)])

    monkeypatch.setattr("sys.argv", ["diff", str(base), str(head), "--protocol", "mcp"])
    assert main() == 2
    assert "nothing was graded" in capsys.readouterr().err
