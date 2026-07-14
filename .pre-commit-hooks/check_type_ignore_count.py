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
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

BASELINE_FILE = ".type-ignore-baseline"
SRC_DIR = "src"
MAIN_REF = "origin/main"


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
        print(f"❌ Cannot resolve {MAIN_REF}:{BASELINE_FILE}", file=sys.stderr)
        print(f"   {result.stderr.strip()}", file=sys.stderr)
        print("   Run: git fetch origin main", file=sys.stderr)
        raise SystemExit(1)
    try:
        return int(result.stdout.strip())
    except ValueError:
        print(f"❌ {MAIN_REF}:{BASELINE_FILE} is not an integer: {result.stdout!r}", file=sys.stderr)
        raise SystemExit(1) from None


def check_baseline_not_raised(repo_root: Path, local_baseline: int) -> int:
    """Fail when the local baseline value exceeds origin/main's.

    The count-vs-baseline ratchet only sees `current > baseline`; committing
    a RAISED baseline file passes it silently. Allowlists only shrink.
    """
    main_baseline = read_main_baseline(repo_root)
    if local_baseline > main_baseline:
        print("❌ Baseline value raised vs origin/main!", file=sys.stderr)
        print(f"   {MAIN_REF}: {main_baseline}", file=sys.stderr)
        print(f"   Local:       {local_baseline} (+{local_baseline - main_baseline})", file=sys.stderr)
        print("", file=sys.stderr)
        print("   The type-ignore baseline may only shrink. Remove the new", file=sys.stderr)
        print("   # type: ignore comments instead of raising the baseline.", file=sys.stderr)
        return 1
    return 0


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


def read_baseline(baseline_file: Path) -> int | None:
    """Read the baseline count from the baseline file."""
    if not baseline_file.exists():
        return None

    try:
        content = baseline_file.read_text().strip()
        return int(content)
    except (ValueError, OSError) as e:
        print(f"Warning: Could not read baseline from {baseline_file}: {e}", file=sys.stderr)
        return None


def write_baseline(baseline_file: Path, count: int) -> None:
    """Write the baseline count to the baseline file."""
    baseline_file.write_text(f"{count}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that # type: ignore count doesn't increase")
    parser.add_argument("--update-baseline", action="store_true", help="Force update baseline to current count")
    args = parser.parse_args()

    # Get paths relative to repo root
    repo_root = Path(__file__).parent.parent
    src_path = repo_root / SRC_DIR
    baseline_file = repo_root / BASELINE_FILE

    if not src_path.exists():
        print(f"Error: {SRC_DIR}/ directory not found", file=sys.stderr)
        return 1

    # Count current type: ignore comments
    current_count = count_type_ignores(src_path)

    # Read baseline
    baseline_count = read_baseline(baseline_file)

    # Handle missing baseline
    if baseline_count is None:
        print(f"📝 No baseline found. Creating {BASELINE_FILE} with current count: {current_count}")
        write_baseline(baseline_file, current_count)
        return check_baseline_not_raised(repo_root, current_count)

    # Handle --update-baseline flag
    if args.update_baseline:
        print(f"📝 Updating baseline from {baseline_count} to {current_count}")
        write_baseline(baseline_file, current_count)
        return check_baseline_not_raised(repo_root, current_count)

    # The committed baseline VALUE may only shrink relative to origin/main —
    # checked on every run so a raised baseline can never ride through green.
    if check_baseline_not_raised(repo_root, min(baseline_count, current_count)) != 0:
        return 1

    # Compare counts
    if current_count > baseline_count:
        increase = current_count - baseline_count
        print("❌ Type ignore count increased!", file=sys.stderr)
        print(f"   Baseline: {baseline_count}", file=sys.stderr)
        print(f"   Current:  {current_count} (+{increase})", file=sys.stderr)
        print("", file=sys.stderr)
        print("   Fix the type errors instead of adding # type: ignore comments.", file=sys.stderr)
        print("   Run: mypy src/your_file.py --config-file=mypy.ini", file=sys.stderr)
        return 1

    elif current_count == baseline_count:
        print(f"✓ Type ignore count unchanged: {current_count}")
        return 0

    else:  # current_count < baseline_count
        decrease = baseline_count - current_count
        print(f"🎉 Type ignore count decreased from {baseline_count} to {current_count} (-{decrease})!")
        print(f"   Automatically updating {BASELINE_FILE}...")
        write_baseline(baseline_file, current_count)
        return 0


if __name__ == "__main__":
    sys.exit(main())
