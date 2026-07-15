#!/usr/bin/env python3
"""
Pre-commit hook to track and prevent increases in `# type: ignore` comments.

This hook enforces a ratcheting approach to type checking:
- Prevents new type: ignore comments from being added
- Tracks the current count in .type-ignore-baseline
- Automatically updates baseline when count decreases
- Compares the baseline VALUE against origin/main so a committed baseline
  raise cannot slip through green (the count-vs-local-baseline check alone
  is blind to it)
- Encourages gradual improvement of type safety

Uses shared ``count_ratchet`` for the create/compare/auto-lower skeleton, CLI
prelude, and int baseline codec; the origin/main raise guard stays here
(baseline-file integrity, not a count method).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from count_ratchet import (
    int_baseline_io,
    parse_ratchet_args,
    read_int_baseline,
    resolve_ratchet_paths,
    run_count_ratchet,
)

BASELINE_FILE = ".type-ignore-baseline"
SRC_DIR = "src"
MAIN_REF = "origin/main"
KEY = "type_ignores"
KEYS = (KEY,)


def read_main_baseline(repo_root: Path) -> int:
    """Read the baseline value committed on origin/main.

    Hard-fails (SystemExit) when the ref or file is unresolvable: a quiet
    skip here would recreate the exact blind spot this check exists to
    close (a baseline raise passing CI green).
    """
    result = subprocess.run(
        ["git", "show", f"{MAIN_REF}:{BASELINE_FILE}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Cannot resolve {MAIN_REF}:{BASELINE_FILE}", file=sys.stderr)
        print(f"  {result.stderr.strip()}", file=sys.stderr)
        print("  Run: git fetch origin main", file=sys.stderr)
        raise SystemExit(1)
    try:
        return int(result.stdout.strip())
    except ValueError:
        print(f"{MAIN_REF}:{BASELINE_FILE} is not an integer: {result.stdout!r}", file=sys.stderr)
        raise SystemExit(1) from None


def check_baseline_not_raised(repo_root: Path, local_baseline: int) -> int:
    """Fail when the local baseline value exceeds origin/main's.

    The count-vs-baseline ratchet only sees `current > baseline`; committing
    a RAISED baseline file passes it silently. Allowlists only shrink.
    """
    main_baseline = read_main_baseline(repo_root)
    if local_baseline > main_baseline:
        print("Baseline value raised vs origin/main!", file=sys.stderr)
        print(f"  {MAIN_REF}: {main_baseline}", file=sys.stderr)
        print(f"  Local:       {local_baseline} (+{local_baseline - main_baseline})", file=sys.stderr)
        print("", file=sys.stderr)
        print("  The type-ignore baseline may only shrink. Remove the new", file=sys.stderr)
        print("  # type: ignore comments instead of raising the baseline.", file=sys.stderr)
        return 1
    return 0


def raise_probe_value(*, baseline_count: int | None, current_count: int, update_baseline: bool) -> int:
    """Value probed against origin/main before any baseline write.

    Create / ``--update-baseline`` probe the post-write value (``current``).
    Normal compare probes ``min(baseline, current)`` so an auto-lower cannot
    mask a committed raise, and a failed probe never writes.
    """
    if baseline_count is None or update_baseline:
        return current_count
    return min(baseline_count, current_count)


def count_type_ignores(src_path: Path) -> int:
    """Count all # type: ignore comments in Python files within src/."""
    count = 0
    pattern = re.compile(r"#\s*type:\s*ignore")

    for py_file in src_path.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
            count += len(pattern.findall(content))
        except Exception as e:
            print(f"Warning: Could not read {py_file}: {e}", file=sys.stderr)

    return count


def main() -> int:
    args = parse_ratchet_args("Check that # type: ignore count doesn't increase")
    repo_root, src_path, baseline_file = resolve_ratchet_paths(baseline_name=BASELINE_FILE)
    read_baseline, write_baseline = int_baseline_io(KEY)

    current_count = count_type_ignores(src_path)
    baseline_count = read_int_baseline(baseline_file)

    # The committed baseline VALUE may only shrink relative to origin/main —
    # checked on every run so a raised baseline can never ride through green.
    # Runs *before* any write (create / --update-baseline / auto-lower).
    if (
        check_baseline_not_raised(
            repo_root,
            raise_probe_value(
                baseline_count=baseline_count,
                current_count=current_count,
                update_baseline=args.update_baseline,
            ),
        )
        != 0
    ):
        return 1

    return run_count_ratchet(
        keys=KEYS,
        current={KEY: current_count},
        baseline_file=baseline_file,
        update_baseline=args.update_baseline,
        read_baseline=read_baseline,
        write_baseline=write_baseline,
        increase_header="Type ignore count increased!",
        increase_hints=(
            "Fix the type errors instead of adding # type: ignore comments.",
            "Run: mypy src/your_file.py --config-file=mypy.ini",
        ),
        format_key=lambda _key: "type: ignore",
    )


if __name__ == "__main__":
    sys.exit(main())
