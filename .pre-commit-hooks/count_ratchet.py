"""Shared count-baseline ratchet driver for pre-commit / quality-ci hooks.

Extracted so type-ignore, duplication, ruff-complexity (and future mypy)
ratchets share one create / ``--update-baseline`` / compare / auto-lower
control flow (ADR-009 / #1613 review). Each hook keeps only its count
method; baseline codecs, CLI prelude, and tooling-failure guards live here.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TextIO


def read_json_baseline(baseline_file: Path, keys: Sequence[str]) -> dict[str, int] | None:
    """Load a JSON object baseline; missing keys default to 0."""
    if not baseline_file.exists():
        return None
    try:
        data = json.loads(baseline_file.read_text())
        if not isinstance(data, dict):
            raise ValueError(f"baseline must be a JSON object, got {type(data).__name__}")
        return {key: int(data.get(key, 0)) for key in keys}
    except (ValueError, OSError, TypeError) as e:
        print(f"Warning: Could not read baseline from {baseline_file}: {e}", file=sys.stderr)
        return None


def write_json_baseline(baseline_file: Path, counts: Mapping[str, int], keys: Sequence[str]) -> None:
    """Write a JSON baseline with a stable key order."""
    payload = {key: int(counts[key]) for key in keys}
    baseline_file.write_text(json.dumps(payload, indent=2) + "\n")


def read_int_baseline(baseline_file: Path) -> int | None:
    """Load a single-integer baseline file."""
    if not baseline_file.exists():
        return None
    try:
        return int(baseline_file.read_text().strip())
    except (ValueError, OSError) as e:
        print(f"Warning: Could not read baseline from {baseline_file}: {e}", file=sys.stderr)
        return None


def write_int_baseline(baseline_file: Path, count: int) -> None:
    """Write a single-integer baseline file."""
    baseline_file.write_text(f"{count}\n")


def int_baseline_io(
    key: str,
) -> tuple[Callable[[Path], dict[str, int] | None], Callable[[Path, Mapping[str, int]], None]]:
    """Reader/writer pair for a single-integer baseline exposed as a one-key dict."""

    def read_baseline(baseline_file: Path) -> dict[str, int] | None:
        value = read_int_baseline(baseline_file)
        if value is None:
            return None
        return {key: value}

    def write_baseline(baseline_file: Path, counts: Mapping[str, int]) -> None:
        write_int_baseline(baseline_file, int(counts[key]))

    return read_baseline, write_baseline


def json_baseline_io(
    keys: Sequence[str],
) -> tuple[Callable[[Path], dict[str, int] | None], Callable[[Path, Mapping[str, int]], None]]:
    """Reader/writer pair for a multi-key JSON baseline."""

    def read_baseline(baseline_file: Path) -> dict[str, int] | None:
        return read_json_baseline(baseline_file, keys)

    def write_baseline(baseline_file: Path, counts: Mapping[str, int]) -> None:
        write_json_baseline(baseline_file, counts, keys)

    return read_baseline, write_baseline


def parse_ratchet_args(description: str) -> argparse.Namespace:
    """Shared argparse for count-ratchet hooks (``--update-baseline`` only)."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Force update baseline to current count(s) (↑ must be justified in review)",
    )
    return parser.parse_args()


def resolve_ratchet_paths(
    *,
    baseline_name: str,
    src_dirname: str = "src",
) -> tuple[Path, Path, Path]:
    """Return ``(repo_root, src_path, baseline_file)``; exit 1 if ``src/`` is missing."""
    repo_root = Path(__file__).resolve().parent.parent
    src_path = repo_root / src_dirname
    if not src_path.exists():
        print(f"Error: {src_dirname}/ directory not found", file=sys.stderr)
        raise SystemExit(1)
    return repo_root, src_path, repo_root / baseline_name


def run_counting_tool(
    cmd: Sequence[str],
    *,
    cwd: Path,
    has_findings: Callable[[subprocess.CompletedProcess[str]], bool],
    label: str,
    truncate: int = 800,
) -> subprocess.CompletedProcess[str]:
    """Run a count tooling command; abort on fatal / empty-findings exit 1."""
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode not in (0, 1) or (result.returncode == 1 and not has_findings(result)):
        print(f"ERROR: {label} failed while counting:", file=sys.stderr)
        print((result.stderr or result.stdout or "")[:truncate], file=sys.stderr)
        raise SystemExit(2)
    return result


def format_counts(counts: Mapping[str, int], keys: Sequence[str]) -> str:
    return ", ".join(f"{key}={counts[key]}" for key in keys)


def run_count_ratchet(
    *,
    keys: Sequence[str],
    current: Mapping[str, int],
    baseline_file: Path,
    update_baseline: bool,
    read_baseline: Callable[[Path], dict[str, int] | None],
    write_baseline: Callable[[Path, Mapping[str, int]], None],
    increase_header: str,
    increase_hints: Sequence[str],
    format_key: Callable[[str], str] | None = None,
    unit: str = "",
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Create / update / compare / auto-lower a multi-key count baseline.

    Returns a process exit code (0 ok, 1 regression).
    ``out`` / ``err`` default to ``None`` and resolve at call time so tests can
    inject ``io.StringIO`` (defaults bound at def-time shadow ``sys.stdout``).
    """
    out_stream = sys.stdout if out is None else out
    err_stream = sys.stderr if err is None else err
    label = format_key or (lambda key: key)
    unit_suffix = f" {unit}" if unit else ""

    baseline = read_baseline(baseline_file)

    if baseline is None:
        print(
            f"No baseline found. Creating {baseline_file.name}: {format_counts(current, keys)}",
            file=out_stream,
        )
        write_baseline(baseline_file, current)
        return 0

    if update_baseline:
        print(
            f"Updating baseline: {format_counts(baseline, keys)} -> {format_counts(current, keys)}",
            file=out_stream,
        )
        write_baseline(baseline_file, current)
        return 0

    failed = False
    for key in keys:
        base = baseline.get(key, 0)
        cur = current[key]
        display = label(key)
        if cur > base:
            print(
                f"  {display}: {cur}{unit_suffix} (+{cur - base} NEW vs baseline {base})",
                file=err_stream,
            )
            failed = True
        elif cur < base:
            print(
                f"  {display}: {cur}{unit_suffix} (-{base - cur} fixed vs baseline {base})",
                file=out_stream,
            )
        else:
            print(f"  {display}: {cur}{unit_suffix} (unchanged)", file=out_stream)

    if failed:
        print("", file=err_stream)
        print(increase_header, file=err_stream)
        for hint in increase_hints:
            print(hint, file=err_stream)
        return 1

    normalized_baseline = {key: baseline.get(key, 0) for key in keys}
    if dict(current) != normalized_baseline:
        print(f"Automatically updating {baseline_file.name}...", file=out_stream)
        write_baseline(baseline_file, current)

    return 0
