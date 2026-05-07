#!/usr/bin/env python3
"""
Pre-commit hook to detect and prevent code duplication using pylint's similarities checker.

Enforces a ratcheting approach — only NEW duplicate blocks fail the build:
- Each duplicate block is fingerprinted by the set of modules involved
- The baseline stores the SET of known fingerprints
- A new fingerprint not in the baseline → fail
- A fingerprint that disappears (because the duplication was fixed or files
  involved were deleted) → auto-removed from baseline
- Separate baselines for src/ and tests/

Why module-set fingerprints, not counts: the previous implementation compared
raw counts. That caused spurious failures when files were deleted because
pylint's similarity detector compares against a different file set, surfacing
pre-existing duplicates that were previously masked. Fingerprinting by which
modules participate in each duplicate is invariant to file deletions in
unrelated parts of the tree.

Trade-off: if modules A and B share two distinct duplicate blocks, both
collapse to one fingerprint, so adding a third duplicate between A and B
won't be caught. Acceptable — that pair is already a known offender and
will surface in any inspection of pylint output.

Uses pylint R0801 (duplicate-code) with these filters:
- Ignores imports, docstrings, comments, and function signatures
- Minimum 6 similar lines to trigger (catches copy-paste-modify patterns)
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

BASELINE_FILE = ".duplication-baseline"

# Lines that mark structure inside pylint's R0801 output:
# - "tests/foo.py:1:0: R0801: Similar lines in 2 files" → start of block
# - "==module.path:[start:end]" → header line listing one of the duplicate locations
_R0801_HEADER_RE = re.compile(r":\d+:\d+: R0801: Similar lines in \d+ files")
_LOCATION_RE = re.compile(r"^==([\w.]+):\[\d+:\d+\]")


def _parse_blocks(pylint_stdout: str) -> list[str]:
    """Parse pylint R0801 output into a list of duplicate-block fingerprints.

    Each block in the output looks like:

        path/to/file.py:1:0: R0801: Similar lines in 2 files
        ==module.a:[10:20]
        ==module.b:[40:50]
        <duplicated code lines>
        <last line ends with " (duplicate-code)">

    Returns one fingerprint per block — the sorted, comma-joined module names
    listed in the ``==module:[start:end]`` header lines. Line numbers are
    deliberately excluded so unrelated edits don't shift fingerprints, and the
    actual duplicate text is excluded because pylint non-deterministically
    picks which copy to display when N>2 modules share the same block.
    """
    fingerprints: list[str] = []
    lines = pylint_stdout.splitlines()
    i = 0
    n = len(lines)

    while i < n:
        if not _R0801_HEADER_RE.search(lines[i]):
            i += 1
            continue

        i += 1
        modules: list[str] = []
        while i < n:
            match = _LOCATION_RE.match(lines[i])
            if not match:
                break
            modules.append(match.group(1))
            i += 1

        if modules:
            fingerprints.append(",".join(sorted(modules)))

    return fingerprints


def scan_duplications(directory: str) -> list[str]:
    """Run pylint similarities on a directory and return fingerprints of
    each R0801 duplicate-code block found.
    """
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
    fingerprints = _parse_blocks(result.stdout)

    # pylint exit code bitmask: bit 0 = fatal error, bit 5 = usage error.
    # If pylint crashed (non-zero with fatal/usage bits) and found 0 violations,
    # the count is untrustworthy — abort to prevent auto-ratchet from zeroing baseline.
    if result.returncode & 33 and not fingerprints:
        print(f"ERROR: pylint crashed on {directory} (exit code {result.returncode}):", file=sys.stderr)
        print(result.stderr[:500], file=sys.stderr)
        sys.exit(2)

    return fingerprints


def read_baseline(baseline_file: Path) -> dict[str, list[str]] | None:
    """Read baseline fingerprint sets from the baseline file (JSON format).

    Supports legacy integer-count baselines (``{"src": 48, "tests": 96}``) by
    treating them as missing — the next successful run rewrites the baseline
    in the new format.
    """
    if not baseline_file.exists():
        return None
    try:
        raw = json.loads(baseline_file.read_text())
    except (ValueError, OSError) as e:
        print(f"Warning: Could not read baseline from {baseline_file}: {e}", file=sys.stderr)
        return None

    # Legacy format detection: values are ints, not lists. Migrate by
    # signalling "no baseline" so the first run with this script seeds it.
    if any(isinstance(v, int) for v in raw.values()):
        print(
            f"Note: legacy count-based baseline in {BASELINE_FILE} detected; "
            "regenerating with fingerprint-based format.",
            file=sys.stderr,
        )
        return None

    return raw


def write_baseline(baseline_file: Path, fingerprints: dict[str, list[str]]) -> None:
    """Write baseline fingerprint sets to the baseline file (deduped + sorted for stable diffs)."""
    sorted_fingerprints = {scope: sorted(set(fps)) for scope, fps in fingerprints.items()}
    baseline_file.write_text(json.dumps(sorted_fingerprints, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that no NEW code duplication blocks were introduced")
    parser.add_argument("--update-baseline", action="store_true", help="Force update baseline to current fingerprints")
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    baseline_file = repo_root / BASELINE_FILE

    print("Scanning for code duplication (pylint R0801)...")
    current = {
        "src": scan_duplications("src/"),
        "tests": scan_duplications("tests/"),
    }

    baseline = read_baseline(baseline_file)

    if baseline is None:
        print(f"No baseline found. Creating {BASELINE_FILE}:")
        for scope, fps in current.items():
            print(f"  {scope}/   = {len(set(fps))} duplicate blocks")
        write_baseline(baseline_file, current)
        return 0

    if args.update_baseline:
        for scope, fps in current.items():
            old_count = len(set(baseline.get(scope, [])))
            print(f"Updating baseline: {scope}/ {old_count} -> {len(set(fps))}")
        write_baseline(baseline_file, current)
        return 0

    failed = False
    changed = False
    for scope in ("src", "tests"):
        baseline_set = set(baseline.get(scope, []))
        current_set = set(current[scope])

        new = current_set - baseline_set
        removed = baseline_set - current_set

        if new:
            print(f"  {scope}/:  {len(current_set)} duplicate blocks (+{len(new)} NEW)", file=sys.stderr)
            failed = True
        elif removed:
            print(f"  {scope}/:  {len(current_set)} duplicate blocks (-{len(removed)} fixed)")
            changed = True
        else:
            print(f"  {scope}/:  {len(current_set)} duplicate blocks (unchanged)")

    if failed:
        print("", file=sys.stderr)
        print("Code duplication increased! DRY is a non-negotiable invariant.", file=sys.stderr)
        print("Extract repeated logic into shared helper functions.", file=sys.stderr)
        print("", file=sys.stderr)
        print("To inspect violations:", file=sys.stderr)
        print("  uv run pylint --disable=all --enable=R0801 src/", file=sys.stderr)
        print("  uv run pylint --disable=all --enable=R0801 tests/", file=sys.stderr)
        return 1

    # Auto-update baseline when fingerprints disappeared (fixes or file
    # deletions that masked duplicates) so the ratchet keeps shrinking.
    if changed:
        print(f"Automatically updating {BASELINE_FILE}...")
        write_baseline(baseline_file, current)

    return 0


if __name__ == "__main__":
    sys.exit(main())
