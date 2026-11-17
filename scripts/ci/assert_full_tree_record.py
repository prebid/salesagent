#!/usr/bin/env python3
"""Fail with a cause when the full-tree collection record did not reach a job.

Three of the four observers in ``tests/collection/`` name ``tests/bdd`` as the
target they read, and only the in-network job produces a record with that
target: the shard jobs invoke pytest with a shard's file list, so their records
are path-scoped.

Without this check the absence surfaces as 32 separate test failures, each
reporting the same missing record, and the job's log says nothing about which
producer failed to deliver. Run it after gathering the artifacts.
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

FULL_TREE = ["tests/bdd"]


def main(directory: str) -> int:
    paths = sorted(glob.glob(str(Path(directory) / "*.json")))
    records = []
    for path in paths:
        try:
            records.append((path, json.loads(Path(path).read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"::error::{path} is not readable as a collection record: {exc}")
            return 1

    full = [r for _, r in records if r.get("target") == FULL_TREE]
    if full:
        print(f"full-tree records: {len(full)} (of {len(records)} total)")
        return 0

    print("::error::No full-tree (tests/bdd) collection record reached this job.")
    print("Only the in-network job produces one; the shard jobs are path-scoped.")
    if not records:
        print(f"  {directory} holds no records at all.")
    for path, record in records:
        print(f"  {Path(path).name}: target={record.get('target')} filter={record.get('filter')!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "bdd-artifacts/collection-manifest"))
