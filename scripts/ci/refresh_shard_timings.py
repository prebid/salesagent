#!/usr/bin/env python3
"""Regenerate the per-file BDD timing map the CI sharder balances on.

Reads a pytest-json-report produced by a real BDD run and writes the total
wall-clock each test FILE consumed, in seconds, to
``scripts/ci/bdd_shard_timings.json``.

Why a recorded map and not a live measurement: the sharder runs in
``shard_paths.py`` BEFORE any test executes, so the only cost signal available at
split time is one a previous run left behind.

Why measured seconds and not scenario counts, which is what the sharder used
before: a Gherkin scenario is not a unit of work. One scenario may be an outline
with twelve examples across three transports, and another may xfail at fixture
setup for nothing. Measured on run innet_150926_0531, the scenario-count estimate
called the two shards even (586 against 569) while they actually held 120 and 91
minutes of work, and CI killed both at the 25-minute cap with one at 50% and the
other at 88%.

The absolute numbers are environment-specific -- they come from whichever box ran
the report -- and that is fine. The sharder only ever compares files with each
other, so what has to transfer is the RATIO between files, not the seconds.

Usage:
    python3 scripts/ci/refresh_shard_timings.py <path-to-bdd-report.json> [...]

Several reports may be passed; each file's durations are summed across them and
then averaged per report, which smooths a single noisy run.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OUT = _REPO_ROOT / "scripts" / "ci" / "bdd_shard_timings.json"

_PHASES = ("setup", "call", "teardown")


def durations_by_file(report_paths: list[Path]) -> dict[str, float]:
    """Total seconds per test file, averaged over the reports supplied."""
    totals: dict[str, float] = collections.defaultdict(float)
    seen_in: collections.Counter[str] = collections.Counter()
    for path in report_paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        per_run: dict[str, float] = collections.defaultdict(float)
        for test in report.get("tests", []):
            nodeid = test.get("nodeid", "")
            if "::" not in nodeid:
                continue
            test_file = nodeid.split("::", 1)[0]
            per_run[test_file] += sum(float(test.get(phase, {}).get("duration", 0.0)) for phase in _PHASES)
        for test_file, seconds in per_run.items():
            totals[test_file] += seconds
            seen_in[test_file] += 1
    return {name: round(totals[name] / seen_in[name], 3) for name in sorted(totals)}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path, help="pytest-json-report file(s) from a BDD run")
    args = parser.parse_args(argv)

    missing = [str(p) for p in args.reports if not p.is_file()]
    if missing:
        print(f"report(s) not found: {', '.join(missing)}", file=sys.stderr)
        return 1

    timings = durations_by_file(args.reports)
    if not timings:
        print("no per-file durations found in the report(s)", file=sys.stderr)
        return 1

    _OUT.write_text(json.dumps(timings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    total = sum(timings.values())
    print(f"wrote {_OUT.relative_to(_REPO_ROOT)}: {len(timings)} files, {total / 60:.1f} minutes total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
