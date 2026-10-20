#!/usr/bin/env python3
"""Print a run's parallelism evidence, and fail on a self-contradiction.

`tests/_worker_profile.py` writes one record per process; this turns that
directory into the per-suite table a reader needs, in the run log where CI and
agents already look. Without it the records are written on every in-network run
and nothing reads them.

## What the numbers are for

`startup_per_worker_s` is the cost that does NOT shrink by adding workers: each
one pays its own import and collection. Read against `tests_s_total / workers`,
it says whether another worker still pays for itself.

`idle_s_total` against `tests_s_total` is scheduling waste -- workers sitting
empty while others finish. A high ratio indicts the DISTRIBUTION (`--dist` mode,
worker count), not the tests.

`slowest_worker_wall_s` is what the suite actually waits for. Compared against
the suite's wall clock, the remainder is orchestration -- plus the startup
before this plugin can observe anything, which it cannot see (see the note above
`_T_IMPORT`).

## What it fails on, and what it does not

It fails only on a self-contradiction: under `--dist load` every worker collects
the whole suite, so the collected counts must agree. Disagreement means the
workers did not see the same suite, which is a collection or sharding defect.
That check needs no baseline.

It deliberately sets NO threshold on startup or idle. A threshold is a ratchet,
a ratchet needs a baseline, and a baseline nobody agreed on turns an
observation into a gate that fails for reasons no one chose. Print the numbers;
gate only the contradiction.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main(directory: str) -> int:
    path = Path(directory)
    if not path.is_dir():
        print(f"worker profile: no directory at {directory}, nothing to report")
        return 0

    # Imported here, after the directory check, rather than at module scope. The
    # aggregation lives in tests/_worker_profile.py and is not duplicated, but a
    # caller with no profile to read should not need the tests package present
    # to say so -- run_all_tests.sh is exercised against a stubbed docker in a
    # workdir that carries the runner and its scripts, not the suite.
    from tests._worker_profile import summarise

    suites = summarise(path)["suites"]
    if not suites:
        print(f"worker profile: {directory} holds no records")
        return 0

    print("Worker profile (per suite):")
    header = f"  {'suite':<22}{'wrk':>4}{'collected':>11}{'startup/wrk':>13}{'tests_s':>10}{'idle_s':>9}{'slowest':>9}"
    print(header)
    print(f"  {'-' * (len(header) - 2)}")

    disagreements = []
    for name, s in suites.items():
        collected = s["collected_per_worker"]
        shown = "DISAGREE" if collected is None else str(collected)
        if collected is None:
            disagreements.append((name, s["collected_values"]))
        print(
            f"  {name:<22}{s['workers']:>4}{shown:>11}{s['startup_per_worker_s']:>13.1f}"
            f"{s['tests_s_total']:>10.1f}{s['idle_s_total']:>9.1f}{s['slowest_worker_wall_s']:>9.1f}"
        )

    for name, s in suites.items():
        workers = s["workers"]
        if workers > 1 and s["tests_s_total"]:
            per_worker_tests = s["tests_s_total"] / workers
            if s["startup_per_worker_s"] > per_worker_tests:
                print(
                    f"  note: {name} spends more per worker on startup "
                    f"({s['startup_per_worker_s']:.1f}s) than that worker spends running tests "
                    f"({per_worker_tests:.1f}s) -- another worker costs more than it returns."
                )

    if disagreements:
        print("::error::Workers disagreed on what they collected.")
        for name, values in disagreements:
            print(f"  {name}: collected {values} across its workers")
        print("Under --dist load every worker collects the whole suite, so these must agree.")
        print("A disagreement means the workers did not see the same suite.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "test-results/worker-profile"))
