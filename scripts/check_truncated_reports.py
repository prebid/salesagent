#!/usr/bin/env python3
"""Fail a run whose suites reported fewer items than they collected.

A suite that reported FEWER items than it collected did not pass -- it died
partway and said nothing. Not hypothetical: an unserializable pytest report
kills an xdist worker, and the session then ends after relaying only the tests
already collected back, behind a summary line reading "0 failed". Measured on
tests/unit at 4/8/14 workers before the fix: collected 5846 but reported
5430 / 5348 / 5271, every run "0 failed".

The delta is recorded in every suite's own JSON, so one predicate covers all of
them and cannot be regressed by whatever truncates a run next.

This lives in its own script because there are TWO runner paths and both flip
the unit suite to xdist: ``run_all_tests.sh`` (the in-network Docker path) and
``run_all_tests_host.sh`` (which ``quick`` and ``ci <target>`` exec into, and
which otherwise decides success from tox's exit code alone). A predicate that
existed on only one of them would leave the documented no-Docker path -- the one
CLAUDE.md points developers at -- unable to see a truncated run.

Usage: python3 -m scripts.check_truncated_reports <results-dir>
Exit 0 when every suite is whole, 1 when any suite is short.
"""

from __future__ import annotations

import os
import sys

from scripts._suite_reports import scan_suite_summaries


def _truncation_problems(name: str, summary: dict) -> list[str]:
    """The truncation judgment for one suite: short means items never reported."""
    collected, total = summary.get("collected"), summary.get("total")
    if collected is None or total is None:
        return []

    # Subtract deselection. pytest-json-report's `collected` counts what
    # collection FOUND, before -m/-k filtering; `total` counts what ran. A
    # suite with a marker expression is legitimately short by exactly
    # `deselected`, and treating that as truncation is a false positive --
    # measured on the plain `bdd` env, which reports collected 9895 /
    # deselected 323 / total 9572.
    deselected = summary.get("deselected", 0)
    expected = collected - deselected
    if total >= expected:
        return []

    return [
        f"  {name}: collected {collected} (minus {deselected} deselected = {expected} expected) "
        f"but reported {total} -- {expected - total} item(s) never reported "
        f"(summary claims {summary.get('failed', 0)} failed)"
    ]


def truncation_report(results_dir: str) -> list[str]:
    """One line per suite that is short, empty when every suite is whole."""
    return scan_suite_summaries(results_dir, _truncation_problems)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {os.path.basename(argv[0])} <results-dir>", file=sys.stderr)
        return 2

    problems = truncation_report(argv[1])
    if not problems:
        return 0

    print("")
    print("ERROR: a suite reported fewer items than it collected -- the run is TRUNCATED, not green:")
    for line in problems:
        print(line)
    print("       Look for INTERNALERROR in the suite output above; an xdist worker died.")
    print("")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
