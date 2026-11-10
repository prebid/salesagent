#!/usr/bin/env python3
"""Diff two storyboard runs by the SET of steps each one passed.

Counts cannot answer "did this change break conformance". Two runs that pass 102 steps
each may pass different 102, and a storyboard step that PASSES produces no pytest item,
so neither the suite summary nor ``compare_runs.py`` (which diffs pytest nodeids) can see
a pass at all. The only thing left comparing conformance run-to-run was ``passed=N``, and
an equal N was read as "the same checks passed" when it meant "the same number passed".

This reads the runner's OWN record -- ``test-results/storyboard_run_<protocol>.json``,
the ``--json`` stdout published by ``tests/storyboard/test_storyboard_conformance.py`` --
and reports gained, lost and still-failing as sets of identified steps.

Every lost pass carries the HEAD run's own disposition for that step -- skipped (with the
runner's ``skip_reason``), failed, or absent from the record entirely. Without it the three
are indistinguishable, and only the middle one is a regression: a corpus bump that adds a
prerequisite turns passes into SKIPS, which removes their failing items and makes the
failure count IMPROVE while conformance drops.

A step is identified by ``<track>::<scenario>::<task>``, which is what the runner nests
its results under and what ``storyboard_collected.json`` keys its check ids on.

SKIP MARKERS ARE NOT PASSES. A storyboard whose requirement is unmet contributes one
``requirement_unmet`` step that the runner records as passed -- the skip itself
succeeded. A run where that storyboard executes has no such marker, so counting it as a
pass makes a tree that skipped MORE look like it passed more. They are dropped.

Exit status: 1 if any step passed on the BASE and not on the HEAD, 0 otherwise. A
regression is a lost pass; gaining passes and gaining failures are both reported but
neither fails this script, because a run that grades more steps than its base
legitimately has more of both.

Read-only. Emits a table, or ``--json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: A storyboard the runner declined to execute still emits one step, recorded as passed.
#: Counting it would reward skipping.
_SKIP_MARKERS = ("requirement_unmet", "Storyboard skipped")

_PROTOCOLS = ("mcp", "a2a")

#: Steps whose identity had to come from ``task`` because the record carried no id, and
#: identities that two steps shared. Both are reported: a comparison resting on either is
#: weaker than it looks, and the second one is the defect that made a lost pass invisible.
_FALLBACK_KEYS: set[str] = set()
_COLLIDED_KEYS: set[str] = set()


def _walk(record: Path) -> tuple[dict[str, bool], dict[str, str]]:
    """One pass over *record*: the graded-pass map, and EVERY step's disposition.

    Two maps rather than one because the first provokes a question it cannot answer. The
    pass map drops every skipped step, which is correct for comparing passing SETS and is
    exactly why a lost pass used to arrive with no cause attached: the step is simply gone
    from the head side, indistinguishable from a step the head run never had.

    Measured on the 3.1.1 -> 3.2.1 corpus bump, that ambiguity was the whole diagnosis. 84
    passes were lost on both protocols and the failure count went DOWN (15 -> 3), because
    the corpus added ``prerequisites.controller_seeding: true`` to 19 storyboards and this
    agent does not expose ``comply_test_controller`` -- so 57 of the 84 became
    ``missing_test_controller`` skips, 19 became ``capability_profile_mismatch`` (the
    3.2-mandated ``covers_content_digest: required`` posture makes the legacy signing
    vectors structurally ungradable), and 2 ceased to exist because 3.2 deleted the phase.
    Three different diseases, one indistinguishable symptom. The disposition map is the
    difference between reading that off the records and re-deriving it by hand.

    The disposition is the HEAD run's own word for what it did -- ``skip:<skip_reason>``,
    ``not_selected:<selection_reason>``, ``skip_marker``, ``passed``, ``failed`` -- never
    this tool's interpretation of it. A graded verdict overwrites a skip for the same
    identity; a skip never overwrites a graded verdict.
    """
    data = json.loads(record.read_text())
    graded: dict[str, bool] = {}
    disposition: dict[str, str] = {}
    for track in data.get("tracks") or []:
        for scenario in track.get("scenarios") or []:
            for step in scenario.get("steps") or []:
                # ORDER MATTERS, and it is measured against REAL runner records rather than
                # against the storyboard definitions. The yaml gives every step an ``id``, but
                # the runner does NOT emit one -- a record carries ``step``, ``task``,
                # ``details``, ``duration_ms``, ``observation_data``, ``passed`` and nothing
                # else. So ``step_id`` is here only for a future runner that publishes it, and
                # what actually identifies a step today is ``step``, its title: measured over
                # 358 steps, keying on ``step`` loses 0 identities and keying on ``task`` loses
                # 90. ``task`` is the tool name, repeated by every step that calls it.
                identity = step.get("step_id") or step.get("id") or step.get("step")
                if not identity:
                    identity = step.get("task")
                    _FALLBACK_KEYS.add(f"{scenario.get('scenario')}::{identity}")
                key = f"{track.get('track')}::{scenario.get('scenario')}::{identity}"
                if step.get("skipped"):
                    disposition.setdefault(key, f"skip:{step.get('skip_reason') or 'unspecified'}")
                    continue
                if step.get("selection_reason"):
                    disposition.setdefault(key, f"not_selected:{step.get('selection_reason')}")
                    continue
                # Matched against the SCENARIO and TASK, never the key. The key used to carry
                # the task, so testing the key happened to work; keyed on ``step_id`` it would
                # miss a marker that the task names and the scenario does not.
                named = f"{scenario.get('scenario') or ''}::{step.get('task') or step.get('step') or ''}"
                if any(marker in named for marker in _SKIP_MARKERS):
                    disposition.setdefault(key, "skip_marker")
                    continue
                if key in graded:
                    # Two steps with ONE identity: the collapse this keying exists to avoid.
                    # Loud, because a silent overwrite is what made a lost pass invisible.
                    _COLLIDED_KEYS.add(key)
                graded[key] = bool(step.get("passed"))
                disposition[key] = "passed" if graded[key] else "failed"
    return graded, disposition


def _steps(record: Path) -> dict[str, bool]:
    """Every step in *record*, keyed ``track::scenario::step_id``, mapped to passed.

    KEYED ON ``step_id``, NOT ``task``. A task repeats within one storyboard constantly --
    measured over the 181 pinned storyboards at 3.1.1: 1155 steps, every one carrying an
    ``id``, and 427 of them (36%) share a task with a sibling. ``deterministic_testing``
    alone has 19 steps named ``comply_test_controller``.

    Keying on the task merged those into one entry, so the LAST one read won: a storyboard
    with four ``update_media_buy`` steps reported one verdict for all four, and a step that
    regressed while a sibling still passed was invisible. That is the one thing this tool
    exists to catch, and the collapse made it silently uncatchable. ``step_id`` is also what
    the repo's own ledger identifies a check by (``tests/storyboard/collected.py``).

    ``task`` remains the fallback for a record predating the id, and the fallback is COUNTED
    so a caller can see how much of a comparison rests on it.

    Skip markers are excluded here rather than by the caller, so no consumer can
    accidentally count one.

    The walk itself is :func:`_walk`, shared with the disposition map so the two cannot
    disagree about which steps are graded.
    """
    return _walk(record)[0]


def runner_score(record: Path) -> dict[str, Any]:
    """The runner's OWN counts. The authoritative score, never re-derived here.

    Deriving it from the step list is a trap with several floors: whole tracks come back
    ``status=skip`` or ``silent`` with every step marked ``passed``, 244 individual steps
    carry ``skipped``, and a dozen carry ``selection_reason``. Each filter looks like the
    last one needed and none of them reproduces ``steps_passed``. So this tool reports the
    runner's number and compares step IDENTITIES; it never offers a score of its own.
    """
    summary = json.loads(record.read_text()).get("summary") or {}
    return {k: summary.get(f"steps_{k}") for k in ("passed", "failed", "skipped", "not_selected")}


def _record_for(where: Path, protocol: str) -> Path | None:
    """The record for *protocol* under *where*, whether it is a run dir or its parent.

    Accepts the published location (``test-results/storyboard_run_<p>.json``), a pulled
    run directory (``<run>/storyboard/storyboard_run_<p>.json``), and a bare file.
    """
    if where.is_file():
        return where
    for candidate in (
        where / f"storyboard_run_{protocol}.json",
        where / "storyboard" / f"storyboard_run_{protocol}.json",
        where / "test-results" / f"storyboard_run_{protocol}.json",
    ):
        if candidate.is_file():
            return candidate
    return None


def _compare(base: dict[str, bool], head: dict[str, bool], head_disposition: dict[str, str]) -> dict[str, Any]:
    """Gained, lost and still-failing, with each lost pass carrying the head's own verdict.

    ``lost_cause`` is what turns the lost list from a symptom into a diagnosis. A pass the
    head run SKIPPED and a pass the head run FAILED are different diseases that the set
    difference renders identically, and the skip is the one that hides: it removes its own
    failing item, so a tree that skipped more can report fewer failures. ``absent`` means
    the head record has no step of that identity at all -- usually a phase the corpus
    deleted, or a storyboard that collapsed to a single capability-gate marker.
    """
    base_pass = {k for k, v in base.items() if v}
    head_pass = {k for k, v in head.items() if v}
    lost = sorted(base_pass - head_pass)
    lost_cause = {k: head_disposition.get(k, "absent") for k in lost}
    causes: dict[str, int] = {}
    for cause in lost_cause.values():
        causes[cause] = causes.get(cause, 0) + 1
    return {
        "base_graded": len(base),
        "head_graded": len(head),
        "base_passed": len(base_pass),
        "head_passed": len(head_pass),
        "identical": sorted(base_pass) == sorted(head_pass),
        "lost": lost,
        "lost_cause": lost_cause,
        "lost_by_cause": dict(sorted(causes.items(), key=lambda kv: (-kv[1], kv[0]))),
        "gained": sorted(head_pass - base_pass),
        "failing_both": sorted({k for k, v in base.items() if not v} & {k for k, v in head.items() if not v}),
        "newly_failing": sorted({k for k, v in head.items() if not v} - set(base)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base", type=Path, help="run dir or record the HEAD is compared against")
    parser.add_argument("head", type=Path, help="run dir or record under test")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument("--protocol", choices=_PROTOCOLS, help="one protocol instead of both")
    args = parser.parse_args()

    protocols = (args.protocol,) if args.protocol else _PROTOCOLS
    report: dict[str, Any] = {}
    missing: list[str] = []
    for protocol in protocols:
        base_rec = _record_for(args.base, protocol)
        head_rec = _record_for(args.head, protocol)
        if base_rec is None or head_rec is None:
            missing.append(f"{protocol} (base={base_rec is not None} head={head_rec is not None})")
            continue
        base_graded, _ = _walk(base_rec)
        head_graded, head_disposition = _walk(head_rec)
        comparison = _compare(base_graded, head_graded, head_disposition)
        comparison["base_score"] = runner_score(base_rec)
        comparison["head_score"] = runner_score(head_rec)
        report[protocol] = comparison

    if missing:
        # Loud AND fatal, never a silent partial. This used to exit 2 only when EVERY protocol
        # was missing, so a run that published mcp and lost a2a printed the error and still
        # exited 0 — the half-measured verdict this paragraph claims to prevent.
        print(f"ERROR: no storyboard record for: {', '.join(missing)}", file=sys.stderr)
        print(
            "       Records come from the runner's --json stdout, published per protocol. A run "
            "that died before writing one cannot be compared, so no verdict is offered for any "
            "protocol: a comparison of the ones that survived is not a comparison of the run.",
            file=sys.stderr,
        )
        return 2

    empty = [p for p, r in report.items() if not r["base_graded"] or not r["head_graded"]]
    if empty:
        # Zero graded steps on either side compares nothing and used to print IDENTICAL.
        print(
            f"ERROR: nothing was graded for: {', '.join(sorted(empty))}. Two empty sets are equal, "
            "which is not agreement — it is the absence of a measurement.",
            file=sys.stderr,
        )
        return 2

    if _COLLIDED_KEYS:
        # Cannot happen with step_id present; if it does, the comparison is under-counting
        # again and must not report a verdict.
        print(
            f"ERROR: {len(_COLLIDED_KEYS)} step identit(ies) were claimed twice, so a verdict "
            f"would rest on whichever was read last: {sorted(_COLLIDED_KEYS)[:5]}",
            file=sys.stderr,
        )
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for protocol, r in sorted(report.items()):
            verdict = "IDENTICAL" if r["identical"] else "DIFFERENT"
            print(f"[{protocol}] passing sets {verdict}")
            print(f"  runner score (authoritative) base={r['base_score']} head={r['head_score']}")
            print(f"  graded-step identities compared: base={r['base_graded']} head={r['head_graded']}")
            print(f"  of those passing: base={r['base_passed']} head={r['head_passed']}  (NOT the score above)")
            print(f"  lost ({len(r['lost'])}) — passed on base, not on head:")
            if r["lost_by_cause"]:
                # The histogram first, because it is the diagnosis and the list is only its
                # evidence: one cause with 57 entries is one fix, and 57 scattered causes
                # are 57. Reading the list without it is how a corpus-wide skip gets
                # mistaken for a production regression.
                print("    by head disposition:")
                for cause, count in r["lost_by_cause"].items():
                    print(f"      {count:5d}  {cause}")
            for step in r["lost"]:
                print(f"    - [{r['lost_cause'][step]}] {step}")
            print(f"  gained ({len(r['gained'])}):")
            for step in r["gained"]:
                print(f"    + {step}")
            print(f"  failing on both: {len(r['failing_both'])}   newly graded and failing: {len(r['newly_failing'])}")
        if _FALLBACK_KEYS:
            print(
                f"\nNOTE: {len(_FALLBACK_KEYS)} step(s) carried no id, so their identity came from "
                f"the task name and siblings sharing that name are indistinguishable."
            )

    return 1 if any(r["lost"] for r in report.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
