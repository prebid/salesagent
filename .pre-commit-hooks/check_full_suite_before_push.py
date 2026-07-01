#!/usr/bin/env python3
"""Pre-push gate: the full suite must have verified the exact tree being pushed.

``run_all_tests.sh`` records ``git rev-parse HEAD`` into its results directory
(``test-results/<ts>/HEAD``). This hook fails the push unless the newest
results directory matches the current HEAD — the mechanical form of the
"full suite before every push" rule, which memory-level discipline has
repeatedly failed to hold under momentum.

Bypass (deliberate, visible): ``ALLOW_PUSH_WITHOUT_SUITE=1 git push``
for doc-only or emergency pushes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if os.environ.get("ALLOW_PUSH_WITHOUT_SUITE") == "1":
        print("check-full-suite: bypassed via ALLOW_PUSH_WITHOUT_SUITE=1")
        return 0

    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()

    results_root = Path("test-results")
    run_dirs = sorted(
        (d for d in results_root.iterdir() if d.is_dir()) if results_root.is_dir() else [],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    newest = run_dirs[0] if run_dirs else None
    recorded = (newest / "HEAD").read_text().strip() if newest and (newest / "HEAD").is_file() else None

    if recorded == head:
        print(f"check-full-suite: {newest.name} verified HEAD {head[:9]}")
        return 0

    print("check-full-suite: the full suite has not verified this exact tree.")
    print(f"  HEAD:               {head[:9]}")
    if newest is None:
        print("  newest suite run:   none found under test-results/")
    else:
        print(f"  newest suite run:   {newest.name} (verified: {recorded[:9] if recorded else 'no HEAD recorded'})")
    print("  Run ./run_all_tests.sh ci, then push.")
    print("  Deliberate bypass: ALLOW_PUSH_WITHOUT_SUITE=1 git push")
    return 1


if __name__ == "__main__":
    sys.exit(main())
