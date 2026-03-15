#!/usr/bin/env python3
"""
Pre-commit hook to detect and prevent code duplication using pylint's similarities checker.

Enforces a ratcheting approach — the duplication count can only go down, never up:
- New duplicated blocks fail the build immediately
- Fixing existing duplication automatically lowers the baseline
- Separate baselines for src/ and tests/

Uses pylint R0801 (duplicate-code) with these filters:
- Ignores imports, docstrings, comments, and function signatures
- Minimum 6 similar lines to trigger (catches copy-paste-modify patterns)
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

BASELINE_FILE = ".duplication-baseline"


def count_duplications(directory: str) -> int:
    """Run pylint similarities on a directory and count R0801 violations."""
    # Similarity tuning (min-similarity-lines, ignore-imports, etc.) lives in
    # pyproject.toml [tool.pylint.similarities] — single source of truth.
    cmd = [
        sys.executable,
        "-m",
        "pylint",
        "--disable=all",
        "--enable=R0801",
        directory,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).parent.parent)
    count = result.stdout.count("R0801")

    # pylint exit code bitmask: bit 0 = fatal error, bit 5 = usage error.
    # If pylint crashed (non-zero with fatal/usage bits) and found 0 violations,
    # the count is untrustworthy — abort to prevent auto-ratchet from zeroing baseline.
    if result.returncode & 33 and count == 0:
        print(f"ERROR: pylint crashed on {directory} (exit code {result.returncode}):", file=sys.stderr)
        print(result.stderr[:500], file=sys.stderr)
        sys.exit(2)

    return count


def read_baseline(baseline_file: Path) -> dict[str, int] | None:
    """Read baseline counts from the baseline file (JSON format)."""
    if not baseline_file.exists():
        return None
    try:
        return json.loads(baseline_file.read_text())
    except (ValueError, OSError) as e:
        print(f"Warning: Could not read baseline from {baseline_file}: {e}", file=sys.stderr)
        return None


def write_baseline(baseline_file: Path, counts: dict[str, int]) -> None:
    """Write baseline counts to the baseline file."""
    baseline_file.write_text(json.dumps(counts, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that code duplication count doesn't increase")
    parser.add_argument("--update-baseline", action="store_true", help="Force update baseline to current counts")
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    baseline_file = repo_root / BASELINE_FILE

    # Count current duplications
    print("Scanning for code duplication (pylint R0801)...")
    src_count = count_duplications("src/")
    tests_count = count_duplications("tests/")
    current = {"src": src_count, "tests": tests_count}

    # Read baseline
    baseline = read_baseline(baseline_file)

    # Handle missing baseline
    if baseline is None:
        print(f"No baseline found. Creating {BASELINE_FILE}:")
        print(f"  src/   = {src_count} duplicate blocks")
        print(f"  tests/ = {tests_count} duplicate blocks")
        write_baseline(baseline_file, current)
        return 0

    # Handle --update-baseline flag
    if args.update_baseline:
        print(
            f"Updating baseline: src/ {baseline.get('src', '?')} -> {src_count}, tests/ {baseline.get('tests', '?')} -> {tests_count}"
        )
        write_baseline(baseline_file, current)
        return 0

    # Compare counts
    failed = False
    for scope in ("src", "tests"):
        baseline_count = baseline.get(scope, 0)
        current_count = current[scope]

        if current_count > baseline_count:
            increase = current_count - baseline_count
            print(f"  {scope}/:  {current_count} duplicate blocks (+{increase} NEW)", file=sys.stderr)
            failed = True
        elif current_count < baseline_count:
            decrease = baseline_count - current_count
            print(f"  {scope}/:  {current_count} duplicate blocks (-{decrease} fixed)")
        else:
            print(f"  {scope}/:  {current_count} duplicate blocks (unchanged)")

    if failed:
        print("", file=sys.stderr)
        print("Code duplication increased! DRY is a non-negotiable invariant.", file=sys.stderr)
        print("Extract repeated logic into shared helper functions.", file=sys.stderr)
        print("", file=sys.stderr)
        print("To inspect violations:", file=sys.stderr)
        print(
            "  uv run pylint --disable=all --enable=R0801 src/",
            file=sys.stderr,
        )
        print(
            "  uv run pylint --disable=all --enable=R0801 tests/",
            file=sys.stderr,
        )
        return 1

    # Auto-update baseline if any count decreased
    if current != {k: baseline.get(k, 0) for k in current}:
        print(f"Automatically updating {BASELINE_FILE}...")
        write_baseline(baseline_file, current)

    return 0


if __name__ == "__main__":
    sys.exit(main())
