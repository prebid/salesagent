#!/usr/bin/env python3
"""Deterministic scenario-by-scenario diff of two pytest-json-report bdd.json runs.

Joins tests by exact nodeid (transport + example params are part of the nodeid,
so this is a stable key). Reports the full outcome-transition matrix on the
intersection, plus added/removed nodeids, with a per-use-case breakdown.

The headline metric is the regression count: nodeids that were ``passed`` in the
OLD run and are ``xfailed`` (or ``failed``/``error``) in the NEW run.

Usage:
    python3 scripts/compare_bdd_runs.py OLD_bdd.json NEW_bdd.json

To compare two branches fairly, generate both reports under identical conditions
(same DB type, same flags, serial to avoid xdist report loss), e.g.:

    pytest tests/bdd/ -p no:randomly -o addopts="" -q \
        --json-report --json-report-file=OLD_bdd.json
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict


def load(path: str) -> dict[str, str]:
    """Return {nodeid: outcome} for every test in a bdd.json report."""
    data = json.load(open(path))
    return {t["nodeid"]: t["outcome"] for t in data["tests"]}


def uc_of(nodeid: str) -> str:
    m = re.search(r"test_(uc\d+)", nodeid)
    return m.group(1) if m else "other"


def main() -> None:
    old_path, new_path = sys.argv[1], sys.argv[2]
    old = load(old_path)
    new = load(new_path)

    old_ids, new_ids = set(old), set(new)
    both = old_ids & new_ids
    only_old = old_ids - new_ids  # removed scenarios/variants
    only_new = new_ids - old_ids  # added scenarios/variants (e.g. 3.1)

    print("=" * 72)
    print(f"OLD: {old_path}")
    print(f"NEW: {new_path}")
    print("=" * 72)
    print(f"OLD total nodeids : {len(old_ids)}")
    print(f"NEW total nodeids : {len(new_ids)}")
    print(f"In BOTH (joinable): {len(both)}")
    print(f"Only in OLD (removed): {len(only_old)}")
    print(f"Only in NEW (added)  : {len(only_new)}")
    print(f"\nOLD outcome totals: {dict(Counter(old.values()))}")
    print(f"NEW outcome totals: {dict(Counter(new.values()))}")

    bad = ("xfailed", "failed", "error")
    trans: Counter = Counter((old[n], new[n]) for n in both)
    print("\n" + "-" * 72)
    print("TRANSITION MATRIX on the joinable intersection (old -> new):")
    print("-" * 72)
    for (o, n), c in sorted(trans.items(), key=lambda x: -x[1]):
        flag = "   <== REGRESSION" if (o == "passed" and n in bad) else ""
        if o in bad and n == "passed":
            flag = "   (improvement)"
        print(f"  {o:>8} -> {n:<8} : {c:>6}{flag}")

    regressions = sorted(n for n in both if old[n] == "passed" and new[n] in bad)
    improvements = sorted(n for n in both if old[n] in bad and new[n] == "passed")
    print("\n" + "=" * 72)
    print(f"HEADLINE  passed(OLD) -> xfailed/failed(NEW)  REGRESSIONS: {len(regressions)}")
    print(f"          xfailed/failed(OLD) -> passed(NEW)  improvements: {len(improvements)}")
    print("=" * 72)

    by_uc_reg: dict[str, int] = defaultdict(int)
    for n in regressions:
        by_uc_reg[uc_of(n)] += 1
    print("\nRegressions by use case (passed -> xfailed/failed):")
    print("  " + (", ".join(f"{u}={c}" for u, c in sorted(by_uc_reg.items())) if by_uc_reg else "(none)"))

    print("\nPer-UC joinable summary (intersection only):")
    print(f"  {'UC':<8}{'join':>6}{'pass>pass':>10}{'pass>xf':>9}{'xf>pass':>9}{'xf>xf':>7}{'other':>7}")
    rows: dict[str, Counter] = defaultdict(Counter)
    for n in both:
        o, w = old[n], new[n]
        r = rows[uc_of(n)]
        r["join"] += 1
        if o == "passed" and w == "passed":
            r["pp"] += 1
        elif o == "passed" and w in bad:
            r["px"] += 1
        elif o in bad and w == "passed":
            r["xp"] += 1
        elif o in bad and w in bad:
            r["xx"] += 1
        else:
            r["other"] += 1
    for uc in sorted(rows):
        r = rows[uc]
        print(f"  {uc:<8}{r['join']:>6}{r['pp']:>10}{r['px']:>9}{r['xp']:>9}{r['xx']:>7}{r['other']:>7}")

    if regressions:
        with open("test-results/bdd-regressions.txt", "w") as f:
            f.write("\n".join(f"{new[n]}\t{n}" for n in regressions))
        print("\nFull regression nodeid list -> test-results/bdd-regressions.txt")


if __name__ == "__main__":
    main()
