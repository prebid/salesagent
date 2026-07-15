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

Uses shared ``count_ratchet`` for the create/compare/auto-lower skeleton, CLI
prelude, and JSON baseline codec; this module owns the pylint count method only.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from count_ratchet import json_baseline_io, parse_ratchet_args, resolve_ratchet_paths, run_count_ratchet

BASELINE_FILE = ".duplication-baseline"
SCOPES = ("src", "tests", "scripts")


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


def main() -> int:
    args = parse_ratchet_args("Check that code duplication count doesn't increase")
    _repo_root, _src_path, baseline_file = resolve_ratchet_paths(baseline_name=BASELINE_FILE)
    read_baseline, write_baseline = json_baseline_io(SCOPES)

    print("Scanning for code duplication (pylint R0801)...")
    current = {
        "src": count_duplications("src/"),
        "tests": count_duplications("tests/"),
        "scripts": count_duplications("scripts/"),
    }

    return run_count_ratchet(
        keys=SCOPES,
        current=current,
        baseline_file=baseline_file,
        update_baseline=args.update_baseline,
        read_baseline=read_baseline,
        write_baseline=write_baseline,
        increase_header="Code duplication increased! DRY is a non-negotiable invariant.",
        increase_hints=(
            "Extract repeated logic into shared helper functions.",
            "",
            "To inspect violations:",
            "  uv run pylint --disable=all --enable=R0801 src/",
            "  uv run pylint --disable=all --enable=R0801 tests/",
        ),
        format_key=lambda scope: f"{scope}/",
        unit="duplicate blocks",
    )


if __name__ == "__main__":
    sys.exit(main())
