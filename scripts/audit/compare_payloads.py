#!/usr/bin/env python3
"""Compare two runs by the REQUEST EACH TEST DISPATCHED, and refuse to guess.

A SIBLING to ``compare_runs.py``, not a leg of it. That tool diffs per-nodeid
OUTCOMES, which is the right outer check and cannot see the thing this one exists
for: a migration that NORMALIZES a seeded payload -- repairing an invalid literal
into a valid one, or flipping which production branch a request reaches -- leaves
every outcome exactly where it was. The scenario stays green and grades a
different request than it graded before. Outcome identity is silent about that;
the payload is not.

    python3 scripts/audit/compare_payloads.py <baseline-dir> <new-dir> [artifact.json ...]

Artifacts are read from ``<dir>/payloads/`` (written by
``tests/bdd/payload_capture.py``, copied there by ``run_all_tests.sh``). Exit
status is 1 when any nodeid CHANGED or reads NOT_MEASURED, so this can gate.

THE VERDICT IS THREE-VALUED, and the third value is the whole design.

    SAME          the nodeid dispatched an identical payload list.
    CHANGED       it dispatched something different. Every one needs an explanation.
    NOT_MEASURED  the baseline recorded this nodeid and the new run did not.

NOT_MEASURED FAILS. It is never "no change" and never a skip. A missing
measurement reading as agreement is precisely the defect salesagent-b341x.18
removed from the conformance census (75f4317f6 is the worked example), and
reintroducing it inside the gate built to catch silent repairs would be
indefensible. It is enforced in three places, and only the last is here:

  1. THE RECORDER writes a row for every nodeid it ran, including an empty list --
     so "ran and dispatched nothing" (66% of them) is distinguishable from "did
     not run".
  2. THE SERIALIZER refuses a value it cannot write, at the dispatch, rather than
     truncating a shard at session end.
  3. THIS COMPARATOR reports it and exits 1.

THE TOLERANCE IS A RULE, NOT A NUMBER. Two runs of the same code disagree on a
handful of bdd_inprocess nodeids, always in the transport parameter: the identical
scenario appears as ``[rest-<example>]`` in one run and ``[mcp-<example>]`` in the
other (salesagent-1iidr). The rule is: a nodeid absent from one side is compared
against its TRANSPORT-STRIPPED TWIN on the other, and if a twin exists the row is
COMPARED, not excused. No twin, NOT_MEASURED. The observed count appears nowhere
in this file -- it falls out as an observation, and a number in the code would
quietly excuse the twenty-first disappearance.

Note the token is sometimes the WHOLE parameter id (``[mcp]``) and sometimes the
first of several (``[mcp-<example>-3]``). A stripper written only for the second
form silently reports false NOT_MEASURED rows; ``_strip_transport`` handles both
and ``tests/unit/test_compare_payloads.py`` pins each.

SOUNDNESS. The twin rule is only valid because the capture happens BEFORE any
per-transport shaping, so the same scenario dispatches the same payload on a2a,
mcp and rest. That is a property of where ``gate_and_record`` sits, stated in its
docstring, and it is the reason the fold that moved the shaping had to land first.
"""

from __future__ import annotations

import collections
import json
import pathlib
import re
import sys
from typing import Any

#: The transport token pytest writes into a parametrized nodeid. Both forms:
#: ``[mcp]`` (the whole parameter id) and ``[mcp-<rest of the id>]``.
_TRANSPORT_TOKEN = re.compile(r"\[(a2a|mcp|rest|e2e_rest|impl)(?=[\]-])")

#: Where ``run_all_tests.sh`` puts payload artifacts inside a results directory.
#: A SUBDIRECTORY on purpose: ``compare_runs.py`` globs ``*.json`` at the results
#: root and would read a payload artifact as a pytest report, whose ``tests`` key
#: is absent -- printing a phantom "baseline 0 new 0 ... CLEAN" row instead of
#: failing. Out of its glob is out of its way.
PAYLOAD_SUBDIR = "payloads"


def load(artifact: pathlib.Path) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    """``(run scope, nodeid -> dispatched payloads)`` for one artifact."""
    data = json.loads(artifact.read_text())
    return data.get("run") or {}, data.get("nodes") or {}


def scope_refusal(label: str, scope: dict[str, Any], nodes: dict[str, list[Any]]) -> str | None:
    """Why this artifact is not a measurement of its own scope, or ``None``.

    Read on the artifact's OWN recorded run block rather than inferred from a short
    file, which is ``scenario_liveness``'s lesson: seeding an artifact with 900
    records and re-running under ``-k`` rewrote it to one record, and the 899 became
    "dormant" with nothing in the file saying a narrower question had been asked.

    NARROWNESS ITSELF IS NOT THE FAULT, and refusing it here was wrong: the
    ``bdd_e2e`` suite is defined as ``-k "e2e_rest or e2e_admin"``, so a blanket
    refusal on ``-k`` would reject the only artifact that suite can produce. What is
    unsound is comparing two artifacts that answered DIFFERENT questions, and that is
    a pairwise property -- see :func:`scope_mismatch`. What is checked here is
    whether this artifact measured the scope it claims: nothing observed, or fewer
    rows than the session collected (an aborted run, whose silences are not
    measurements).
    """
    if not nodes:
        return f"{label}: artifact records ZERO nodeids — the session observed nothing"
    collected = int(scope.get("collected") or 0)
    if collected and len(nodes) < collected:
        return (
            f"{label}: recorded {len(nodes)} nodeids but the session collected {collected} — "
            "the run did not finish, so its silences are not measurements"
        )
    return None


def scope_mismatch(old_scope: dict[str, Any], new_scope: dict[str, Any]) -> str | None:
    """Whether the two artifacts answered the same question, stated as a refusal.

    A run narrowed by PATH is invisible here (pytest records no path expression), and
    deliberately so: a path-narrowed run compared against a wide baseline already
    fails the way that matters, because every baseline nodeid it did not run reads
    NOT_MEASURED. The two expressions pytest DOES record are compared, because a
    silently different ``-k`` produces two files that each look complete.
    """
    for key, flag in (("selection", "-k"), ("markers", "-m")):
        if (old_scope.get(key) or "") != (new_scope.get(key) or ""):
            return (
                f"the two runs answered different questions: baseline {flag} "
                f"{old_scope.get(key)!r} vs new {flag} {new_scope.get(key)!r}"
            )
    return None


def _strip_transport(nodeid: str) -> str:
    """The nodeid with its transport parameter token replaced by a placeholder."""
    return _TRANSPORT_TOKEN.sub("[<t>", nodeid)


def _twins(nodeids: set[str]) -> dict[str, list[str]]:
    """Transport-stripped id -> the nodeids that share it."""
    grouped: dict[str, list[str]] = collections.defaultdict(list)
    for nodeid in nodeids:
        grouped[_strip_transport(nodeid)].append(nodeid)
    return grouped


def compare(baseline_dir: pathlib.Path, new_dir: pathlib.Path, artifact: str) -> int:
    old_path = baseline_dir / PAYLOAD_SUBDIR / artifact
    new_path = new_dir / PAYLOAD_SUBDIR / artifact
    print(f"\n=== {artifact} ===")
    for path, side in ((old_path, "baseline"), (new_path, "new")):
        if not path.exists():
            # NOT a "cannot compare, moving on". An absent artifact IS the
            # not-measured case, one level up from a nodeid.
            print(f"  NOT_MEASURED: {side} artifact missing at {path}")
            return 1

    old_scope, old = load(old_path)
    new_scope, now = load(new_path)
    for label, scope, nodes in (("baseline", old_scope, old), ("new", new_scope, now)):
        refusal = scope_refusal(label, scope, nodes)
        if refusal is not None:
            print(f"  REFUSED: {refusal}")
            return 1
    mismatch = scope_mismatch(old_scope, new_scope)
    if mismatch is not None:
        print(f"  REFUSED: {mismatch}")
        return 1

    shared = old.keys() & now.keys()
    only_old = old.keys() - now.keys()
    only_new = now.keys() - old.keys()

    # Resolve disappearances through their transport twins BEFORE judging them.
    new_twins = _twins(only_new)
    resolved: dict[str, str] = {}
    unmatched: list[str] = []
    for nodeid in sorted(only_old):
        candidates = [c for c in new_twins.get(_strip_transport(nodeid), []) if c not in resolved.values()]
        if candidates:
            resolved[nodeid] = sorted(candidates)[0]
        else:
            unmatched.append(nodeid)

    pairs = [(n, n) for n in shared] + list(resolved.items())
    changed = sorted(o for o, n in pairs if old[o] != now[n])

    print(
        f"  baseline {len(old):6}   new {len(now):6}   compared {len(pairs):6}   "
        f"via-twin {len(resolved):6}   not-measured {len(unmatched):6}"
    )

    if changed:
        print(f"\n  NODEIDS THAT DISPATCHED A DIFFERENT PAYLOAD: {len(changed)}  <-- each needs an explanation")
        for nodeid in changed[:10]:
            other = dict(pairs).get(nodeid, nodeid)
            print(f"    {nodeid[:104]}")
            for line in _describe(old[nodeid], now[other])[:4]:
                print(f"         {line}")
        if len(changed) > 10:
            print(f"    ... {len(changed) - 10} more")
    else:
        print("\n  PAYLOADS: zero nodeids dispatched a different request")

    if unmatched:
        print(f"\n  NOT_MEASURED (baseline recorded it, this run did not, no transport twin): {len(unmatched)}")
        for nodeid in unmatched[:10]:
            print(f"    {nodeid[:104]}")

    # Accounting, not a verdict. A nodeid this run recorded and the baseline did not is a
    # NEW test: there is no earlier payload for it to have silently diverged from, so it
    # cannot be a repair. Printed rather than swallowed, because "where did these come
    # from" is a question a reader will have, and an unexplained batch of them means the
    # two runs were not the same suite.
    appeared = sorted(only_new - set(resolved.values()))
    if appeared:
        print(f"\n  NEW (this run recorded it, baseline did not, no transport twin): {len(appeared)}")
        for nodeid in appeared[:10]:
            print(f"    {nodeid[:104]}")

    return 1 if (changed or unmatched) else 0


def _describe(before: list[Any], after: list[Any]) -> list[str]:
    """Human-readable first differences between two dispatch lists."""
    if len(before) != len(after):
        return [f"dispatch COUNT {len(before)} -> {len(after)}"]
    lines: list[str] = []
    for index, (b, a) in enumerate(zip(before, after, strict=True)):
        if b == a:
            continue
        for path, was, now in _leaf_diffs(b, a):
            lines.append(f"dispatch[{index}] {path}: {was!r:.60} -> {now!r:.60}")
    return lines or ["(structures differ)"]


def _leaf_diffs(before: Any, after: Any, path: str = "") -> list[tuple[str, Any, Any]]:
    """Every differing leaf, as ``(dotted path, before, after)``."""
    if isinstance(before, dict) and isinstance(after, dict):
        diffs: list[tuple[str, Any, Any]] = []
        for key in sorted(before.keys() | after.keys()):
            here = f"{path}.{key}" if path else key
            if key not in before:
                diffs.append((here, "<absent>", after[key]))
            elif key not in after:
                diffs.append((here, before[key], "<absent>"))
            else:
                diffs.extend(_leaf_diffs(before[key], after[key], here))
        return diffs
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        diffs = []
        for index, (b, a) in enumerate(zip(before, after, strict=True)):
            diffs.extend(_leaf_diffs(b, a, f"{path}[{index}]"))
        return diffs
    return [] if before == after else [(path or "<root>", before, after)]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 2:
        print(__doc__)
        return 2
    baseline, new = pathlib.Path(args[0]), pathlib.Path(args[1])
    artifacts = args[2:] or sorted(p.name for p in (new / PAYLOAD_SUBDIR).glob("*.json"))
    if not artifacts:
        print(f"NOT_MEASURED: no payload artifacts under {new / PAYLOAD_SUBDIR}")
        return 1
    status = 0
    for artifact in artifacts:
        status |= compare(baseline, new, artifact)
    print(
        "\n"
        + (
            "DIRTY: a dispatched payload changed, or a nodeid was not measured"
            if status
            else "CLEAN: every nodeid dispatched the same request"
        )
    )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
