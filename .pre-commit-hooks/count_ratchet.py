"""Shared count-baseline ratchet driver for pre-commit / quality-ci hooks.

Extracted so type-ignore, duplication, ruff-complexity (and future mypy)
ratchets share one create / ``--update-baseline`` / compare / auto-lower
control flow (ADR-009 / #1613 review). Count methods and baseline codecs
stay per-hook; this module owns the ratchet skeleton only.
"""

from __future__ import annotations

import json
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
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    """Create / update / compare / auto-lower a multi-key count baseline.

    Returns a process exit code (0 ok, 1 regression).
    """
    label = format_key or (lambda key: key)
    unit_suffix = f" {unit}" if unit else ""

    baseline = read_baseline(baseline_file)

    if baseline is None:
        print(
            f"No baseline found. Creating {baseline_file.name}: {format_counts(current, keys)}",
            file=out,
        )
        write_baseline(baseline_file, current)
        return 0

    if update_baseline:
        print(
            f"Updating baseline: {format_counts(baseline, keys)} -> {format_counts(current, keys)}",
            file=out,
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
                file=err,
            )
            failed = True
        elif cur < base:
            print(f"  {display}: {cur}{unit_suffix} (-{base - cur} fixed vs baseline {base})", file=out)
        else:
            print(f"  {display}: {cur}{unit_suffix} (unchanged)", file=out)

    if failed:
        print("", file=err)
        print(increase_header, file=err)
        for hint in increase_hints:
            print(hint, file=err)
        return 1

    normalized_baseline = {key: baseline.get(key, 0) for key in keys}
    if dict(current) != normalized_baseline:
        print(f"Automatically updating {baseline_file.name}...", file=out)
        write_baseline(baseline_file, current)

    return 0
