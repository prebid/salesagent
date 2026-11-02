#!/usr/bin/env python3
"""Name the suites whose JSON report shows failures OR errors.

``error`` is pytest's count for an item that died in SETUP or TEARDOWN, and it
is NOT part of ``summary.failed``. A run whose only problem is a fixture blowing
up therefore reads ``failed 0`` in every report while tox exits 1 -- so the
number a reader reaches for first says the suite is clean. That is exactly how
one rotating ``DeadlockDetected`` in a bdd_e2e fixture stayed invisible for two
consecutive full runs (#2048): the count was 0, the exit code was
1, and nothing on stdout connected the two.

This lives in its own script for the same reason
``check_truncated_reports.py`` does: there are TWO runner paths --
``run_all_tests.sh`` (in-network Docker) and ``run_all_tests_host.sh`` (which
``quick`` and ``ci <target>`` exec into) -- and a predicate on only one of them
leaves the other unable to see the problem. The host path is the worse case: it
decides success purely from tox exit codes and never looked at the reports at
all.

Usage: python3 -m scripts.report_suite_failures <results-dir>
Exit 0 when every suite reports 0 failed and 0 error, 1 otherwise.
"""

from __future__ import annotations

import os
import sys

from scripts._suite_reports import scan_suite_summaries


def _failure_problems(name: str, summary: dict) -> list[str]:
    """The failure judgment for one suite: any failure or any error is a finding."""
    failed = summary.get("failed", 0)
    errored = summary.get("error", 0)
    if not failed and not errored:
        return []

    line = f"  {name}: failed={failed} error={errored}"
    if errored and not failed:
        # The whole point of the script: say out loud that the headline
        # number is zero and the suite still did not pass.
        line += "  <- 0 failed, but errors are setup/teardown deaths, not passes"
    return [line]


def failure_report(results_dir: str) -> list[str]:
    """One line per suite reporting failures or errors; empty when all clean."""
    return scan_suite_summaries(results_dir, _failure_problems)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {os.path.basename(argv[0])} <results-dir>", file=sys.stderr)
        return 2

    problems = failure_report(argv[1])
    if not problems:
        return 0

    print("")
    print("ERROR: suites reported failures or errors:")
    for line in problems:
        print(line)
    print("       'error' items never ran their test body -- a fixture raised. Read the")
    print("       suite output for the setup traceback, not the assertion list.")
    print("")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
