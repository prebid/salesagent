#!/usr/bin/env python3
"""Fold the BDD shards' published artifacts into the one suite they are.

The in-network runner splits bdd_inprocess into per-shard tox envs so each process
collects only its own files. Every consumer downstream of that split reads FILES and
expects one per suite: check_truncated_reports, report_suite_failures, `cassini status`,
compare_runs and compare_payloads all key on one row per suite, and every baseline names
bdd_inprocess.

Hard-fails rather than merging what it happens to find. check_truncated_reports compares
collected-deselected against total PER FILE and cannot see a file that is ABSENT, so
emitting a surviving shard alone would produce an internally consistent report and a green
run with half the suite never executed. Summing the counts is what keeps that predicate
binding on the merged file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ci.shard_split import SHARD_COUNTS  # noqa: E402

SHARD_GLOB = "bdd-inprocess-s*.json"
MERGED_NAME = "bdd_inprocess.json"


class ShardMergeError(RuntimeError):
    """The shard artifacts on disk do not add up to the suite."""


def _read_shards(directory: Path, expected: int) -> tuple[list[Path], list[dict[str, Any]]]:
    """The shard files in ``directory``, requiring exactly ``expected`` of them.

    The count is the point. An unreadable file is caught by the read, but a MISSING one is
    simply absent from the glob. It also closes the other direction: run_all_tests.sh asks
    shard_paths.py for shards 1 and 2 by hand, so raising SHARD_COUNTS["bdd"] would send a
    third of the suite to a shard nothing runs, and that drift fails here.
    """
    paths = sorted(directory.glob(SHARD_GLOB))
    if len(paths) != expected:
        raise ShardMergeError(f"expected {expected} shard file(s) in {directory}, got {[p.name for p in paths]}")
    try:
        return paths, [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    except (OSError, json.JSONDecodeError) as exc:
        raise ShardMergeError(f"cannot read a shard file in {directory}: {exc}") from exc


def _sum_ints(mappings: list[dict[str, Any]]) -> dict[str, int]:
    summed: dict[str, int] = {}
    for mapping in mappings:
        for key, value in mapping.items():
            if isinstance(value, int):
                summed[key] = summed.get(key, 0) + value
    return summed


def merge_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """One pytest-json-report for the suite. A failed shard fails the merged report."""
    merged = dict(reports[0])
    for key in ("tests", "collectors", "warnings"):
        merged[key] = [entry for report in reports for entry in report.get(key, [])]
    merged["summary"] = _sum_ints([report.get("summary", {}) for report in reports])
    merged["exitcode"] = max(report.get("exitcode", 0) for report in reports)
    merged["duration"] = max(report.get("duration", 0) for report in reports)
    return merged


def merge_payloads(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """One payload capture for the suite: the union of its nodes and the sum of its scope.

    Shards hold disjoint files, so a nodeid in two of them means the split is broken, and
    picking one would hide that.
    """
    nodes: dict[str, Any] = {}
    for artifact in artifacts:
        repeated = nodes.keys() & artifact.get("nodes", {}).keys()
        if repeated:
            raise ShardMergeError(f"{len(repeated)} node(s) appear in two shards, e.g. {sorted(repeated)[0]}")
        nodes.update(artifact.get("nodes", {}))

    scopes = [artifact.get("run", {}) for artifact in artifacts]
    run = {**scopes[0], **_sum_ints(scopes)}
    run["exitstatus"] = max(scope.get("exitstatus", 0) for scope in scopes)
    return {"run": run, "nodes": {k: nodes[k] for k in sorted(nodes)}}


def main(results_dir: str, payload_subdir: str) -> int:
    """Merge a run's shard reports and payload captures, or change nothing on disk.

    The parts stay behind on a failure, because they are the evidence of what went wrong.
    """
    expected = SHARD_COUNTS["bdd"]
    report_dir, payload_dir = Path(results_dir), Path(results_dir) / payload_subdir
    try:
        report_paths, reports = _read_shards(report_dir, expected)
        payload_paths, payloads = _read_shards(payload_dir, expected)
        merged_report, merged_payload = merge_reports(reports), merge_payloads(payloads)
    except ShardMergeError as exc:
        print(f"merge_shard_reports: {exc}", file=sys.stderr)
        return 1

    (report_dir / MERGED_NAME).write_text(json.dumps(merged_report, indent=2), encoding="utf-8")
    (payload_dir / MERGED_NAME).write_text(json.dumps(merged_payload, indent=2), encoding="utf-8")
    for path in (*report_paths, *payload_paths):
        path.unlink()
    print(f"merged {expected} shards -> {report_dir / MERGED_NAME} ({merged_report['summary'].get('total', 0)} tests)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: merge_shard_reports.py <results-dir> <payload-subdir>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
