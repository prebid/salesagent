#!/usr/bin/env python3
"""Merge pytest-json-report shard reports into one suite report.

The in-network runner splits bdd_inprocess into per-shard tox envs so each
process collects only its own files. Every consumer downstream of that split
reads FILES and expects one per suite: `cp .tox/*.json`, the RC reconciliation
in run_all_tests.sh, scripts/check_truncated_reports.py, `cassini status`, and
the epic baseline all key on one row per suite.

Hard-fails rather than merging what it happens to find. check_truncated_reports
compares collected-deselected against total PER FILE and cannot see a file that
is ABSENT, so emitting a surviving shard alone would produce an internally
consistent report and a green run with half the suite never executed -- on a box
that has already killed a shard with SIGKILL under memory pressure. Summing the
counts is what keeps that predicate binding on the merged file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def merge(out_path: str, expected: int, shard_paths: list[str]) -> int:
    """Merge `shard_paths` into `out_path`, requiring exactly `expected` of them.

    The count is the point. A shard whose file is UNREADABLE is caught by the
    read below, but a shard whose file is MISSING is simply absent from the
    caller's glob -- nothing downstream knows two were expected. Merging the
    survivor alone yields an internally consistent report that
    check_truncated_reports.py passes (its predicate is per-file), so the run
    goes green with half the suite never executed. That is the failure this
    script exists to prevent, and only an expected count can see it.

    It also closes the other direction: run_all_tests.sh asks shard_paths.py for
    shards 1 and 2 by hand, so raising SHARD_COUNTS["bdd"] would send a third of
    the suite to a shard nothing runs. Passing SHARD_COUNTS through means that
    drift fails here instead of silently shrinking the suite.
    """
    if len(shard_paths) != expected:
        print(
            f"merge_shard_reports: expected {expected} shard report(s), got {len(shard_paths)}: "
            f"{shard_paths or '(none)'}",
            file=sys.stderr,
        )
        return 1

    reports = []
    for path in shard_paths:
        try:
            reports.append(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"merge_shard_reports: cannot read {path}: {exc}", file=sys.stderr)
            return 1

    merged = dict(reports[0])
    merged["tests"] = [t for r in reports for t in r.get("tests", [])]

    summary: dict[str, int] = {}
    for r in reports:
        for key, value in r.get("summary", {}).items():
            if isinstance(value, int):
                summary[key] = summary.get(key, 0) + value
    merged["summary"] = summary

    merged["duration"] = max((r.get("duration", 0) or 0) for r in reports)
    merged["shards_merged"] = [Path(p).stem for p in shard_paths]

    Path(out_path).write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print(
        f"merged {len(reports)} shard report(s) -> {out_path} "
        f"({summary.get('total', 0)} tests, {summary.get('passed', 0)} passed)"
    )
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("usage: merge_shard_reports.py <out.json> <expected-count> <shard.json>...", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(merge(sys.argv[1], int(sys.argv[2]), sys.argv[3:]))
