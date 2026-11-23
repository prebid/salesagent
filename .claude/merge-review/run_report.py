#!/usr/bin/env python3
"""Per-suite counts for a test-results run, ordered by the reports' EMBEDDED timestamp.

Directory mtime is NOT the run order. ``innet_170926_0547`` has a LATER mtime than
``innet_170926_0629`` while being the OLDER run -- fetching rewrites files after the fact, so
mtime records when results were copied, not when they were produced. Every ordering here uses
the ``created`` field pytest-json-report writes INTO each report.

Usage: run_report.py [<dir> ...]     (no args = every innet_* dir, oldest first)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[2] / "test-results"


def created(d: Path) -> float:
    stamps = []
    for f in d.glob("*.json"):
        try:
            v = json.load(open(f)).get("created")
        except Exception:
            continue
        if v:
            stamps.append(v)
    return min(stamps) if stamps else 0.0


def counts(d: Path) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for f in sorted(d.glob("*.json")):
        try:
            rep = json.load(open(f))
        except Exception:
            continue
        tests = rep.get("tests")
        if tests is None:
            continue
        tally: dict[str, int] = {}
        for t in tests:
            tally[t.get("outcome", "?")] = tally.get(t.get("outcome", "?"), 0) + 1
        out[f.stem] = tally
    return out


def main() -> int:
    dirs = [Path(a) for a in sys.argv[1:]] or sorted(RESULTS.glob("innet_*"))
    for d in sorted(dirs, key=created):
        c = counts(d)
        total_f = sum(t.get("failed", 0) + t.get("error", 0) for t in c.values())
        print(f"\n=== {d.name}  (created {created(d):.0f})  FAILED+ERROR={total_f}")
        for suite, tally in sorted(c.items()):
            bits = " ".join(f"{k}={v}" for k, v in sorted(tally.items()))
            print(f"   {suite:16s} {bits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
