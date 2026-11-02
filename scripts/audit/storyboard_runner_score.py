#!/usr/bin/env python3
"""The storyboard runner's own score per protocol, which no pytest summary can show.

A storyboard check that PASSES produces no pytest item. The conformance suite's
item list is therefore its FAILURES, and reading the job as green says only that
the ledger covered every failure — it says nothing about how many checks passed.
That number lives in the runner's own ``summary_<protocol>.json``, and until this
script it was legible only by opening a local artifact, which is why the figure
this job exists to hold travelled by memory: 30 passed per protocol over 72
storyboards, reproduced across six in-network runs.

Reporting only. No floor is asserted here, deliberately: a floor compares a CI
number against a box number, and those are different populations until this has
published the CI-side figure often enough to show they are the same one.
Asserting across environments before then manufactures a failure with no defect
behind it, which is the opposite of what this report is for.

Read-only. Emits JSON, or ``--markdown``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from scripts.audit import storyboard_spec  # noqa: E402

#: Where ``run_all_tests.sh storyboard`` leaves the runner's per-protocol summary.
RESULTS_GLOB = "test-results/*/storyboard/summary_*.json"


def _score(path: Path) -> dict[str, Any]:
    """One protocol's score. ``storyboards`` counts what the runner actually executed."""
    data = json.loads(path.read_text(encoding="utf-8"))
    executed = data.get("storyboardsExecuted") or data.get("storyboards_executed") or []
    return {
        "protocol": path.name[len("summary_") : -len(".json")],
        "run": path.parent.parent.name,
        "passed": data.get("passed"),
        "failed": data.get("failed"),
        "skipped": data.get("skipped"),
        "storyboards": len(executed),
    }


def build(repo: Path, adcp: Path) -> dict[str, Any]:
    """Every per-protocol score under the newest run directory that has one.

    ``adcp`` is unused: the subject is what a run MEASURED, not what the pin
    declares. It stays in the signature because ``run_cli`` supplies the one
    argument shape every audit script here takes.

    Scores come from ONE run directory — the newest that contains any summary.
    Pooling protocols across runs would put an mcp figure from one execution
    beside an a2a figure from another and print them as a pair.
    """
    del adcp
    summaries = sorted(repo.glob(RESULTS_GLOB))
    newest = max((p.parent.parent for p in summaries), default=None, key=lambda d: d.name)
    chosen = [p for p in summaries if newest is not None and p.parent.parent == newest]
    return {
        "run": newest.name if newest else None,
        "scores": sorted((_score(p) for p in chosen), key=lambda s: s["protocol"]),
        "runs_available": sorted({p.parent.parent.name for p in summaries}),
    }


def render(result: dict[str, Any]) -> str:
    """A markdown table, or an explicit statement that nothing was measured.

    The empty case is NOT an empty table. An instrument that prints a shape when
    it read no input is the failure mode
    ``tests/unit/test_architecture_audit_scripts_have_a_subject.py`` was written
    about: a reader takes the shape for a measurement. So the absent case says
    what is absent and where it was looked for.
    """
    lines = ["### Storyboard runner score", ""]
    if not result["scores"]:
        lines += [
            f"**NOT MEASURED** — no `{RESULTS_GLOB}` under the repository root.",
            "",
            "The runner reported no per-protocol summary, so this run's pass count is",
            "unknown rather than zero. If the conformance step above failed before the",
            "runner executed, that failure is the finding and this is its consequence.",
        ]
        return "\n".join(lines) + "\n"

    lines += [
        f"From `{result['run']}`. A passing check produces no pytest item, so these",
        "counts are the runner's own and cannot be read off the suite's summary.",
        "",
        "| protocol | passed | failed | skipped | storyboards |",
        "|---|---|---|---|---|",
    ]
    for s in result["scores"]:
        lines.append(f"| {s['protocol']} | {s['passed']} | {s['failed']} | {s['skipped']} | {s['storyboards']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    return storyboard_spec.run_cli(__doc__ or "", build, render)


if __name__ == "__main__":
    raise SystemExit(main())
