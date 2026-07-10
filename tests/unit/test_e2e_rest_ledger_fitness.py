"""Fitness function for the e2e_rest known-failures ledger.

PR #1420 review finding: unlike the duplication baseline and
the structural-guard allowlists, the e2e_rest ledger
(``tests/bdd/e2e_rest_known_failures.txt``) had no ratchet and no stale-entry
test, so it could silently grow or accumulate dead nodeids after a feature/param
rename. Two invariants, enforced in two places:

1. **No silent growth or shrinkage** — enforced by the exact-set lock in
   ``test_e2e_rest_ledger_state.py`` (``EXPECTED_LEDGER``): any added, removed,
   or re-added entry fails there and must be justified in the same change. A
   separate count ceiling derived from that same pin could never fail
   independently, so this module no longer carries one (#1430 review: the old
   ``count <= len(EXPECTED_LEDGER)`` ratchet was tautological).
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


def _ledger_entries() -> list[str]:
    return [
        line.strip() for line in _LEDGER.read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]


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
