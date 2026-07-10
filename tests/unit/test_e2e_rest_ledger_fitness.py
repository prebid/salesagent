"""Fitness function for the e2e_rest known-failures ledger.

PR #1420 review finding: unlike the duplication baseline and
the structural-guard allowlists, the e2e_rest ledger
(``tests/bdd/e2e_rest_known_failures.txt``) had no ratchet and no stale-entry
test, so it could silently grow or accumulate dead nodeids after a feature/param
rename. Two invariants:

1. **Monotonic** — the entry count may only DECREASE. Graduating a scenario
   lowers ``_LEDGER_CEILING``; re-adding one trips the guard, forcing a fix of
   the e2e_rest scenario instead.
2. **No stale entries** — every ledger nodeid must resolve to a currently
   collected test item. A param/feature rename that orphans a nodeid is caught
   here rather than silently masking a never-run scenario.
"""

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LEDGER = _REPO_ROOT / "tests" / "bdd" / "e2e_rest_known_failures.txt"

# Ratchet ceiling — this may only ever DECREASE. When you graduate ledger
# entries, lower it to the new count. It must never be raised.
#
# One-time owner-approved recalibration 2026-07-09, 308 -> 317: perf/parallelize-
# test-suite enabled parallel e2e_rest (E2E_PER_WORKER), which for the first time
# exercises 14 pre-existing MAIN scenarios (UC-004 #1545, UC-005 #1479, UC-018
# BR-RULE-034 #1551) over the real HTTP transport. They fail for transport-inherent
# reasons (mock-injection invisible, e2e_rest auth/tenant-context, wire not stashed)
# unrelated to the adcp 6.6 bump — the run surface expanded, so this is a genuinely
# new baseline, not a re-add of a graduated scenario. Deferred to the e2e_rest
# retirement epic salesagent-rlgl (tracked: salesagent-5p68); the ceiling drops as
# those scenarios are retired.
_LEDGER_CEILING = 317


def _ledger_entries() -> list[str]:
    return [
        line.strip() for line in _LEDGER.read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]


def test_ledger_count_is_monotonic_non_increasing():
    count = len(_ledger_entries())
    assert count <= _LEDGER_CEILING, (
        f"e2e_rest ledger grew to {count} (ceiling {_LEDGER_CEILING}). The ledger may only "
        "shrink — fix the e2e_rest scenario instead of re-adding it to the ledger."
    )


def test_every_ledger_entry_resolves_to_a_collected_item():
    entries = set(_ledger_entries())

    # Collect the bdd suite with the e2e_rest transport enabled. -n0 satisfies the
    # BDD_E2E_ENABLED xdist guard; addopts is cleared so -q prints bare nodeids.
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/bdd",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "no:randomly",
            "-n0",
        ],
        cwd=_REPO_ROOT,
        env={**os.environ, "BDD_E2E_ENABLED": "true", "BDD_XDIST_N": "0"},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"bdd collection failed (rc={proc.returncode}):\n{proc.stderr[-2000:]}"

    collected = {line.strip() for line in proc.stdout.splitlines() if "::" in line}
    stale = sorted(e for e in entries if e not in collected)
    assert not stale, (
        f"{len(stale)} stale e2e_rest ledger nodeid(s) resolve to no collected test "
        "(feature/param rename?). Remove them from the ledger:\n  " + "\n  ".join(stale[:20])
    )
