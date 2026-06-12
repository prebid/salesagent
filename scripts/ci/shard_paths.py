#!/usr/bin/env python3
"""Print test paths for a CI BDD shard (one path per line)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.ci.shard_split import SHARD_COUNTS, paths_for_shard  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=sorted(SHARD_COUNTS))
    parser.add_argument("shard", type=int, help="1-based shard index")
    args = parser.parse_args()
    try:
        paths = paths_for_shard(args.suite, args.shard)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not paths:
        print(f"No tests assigned to {args.suite} shard {args.shard}", file=sys.stderr)
        return 1
    print("\n".join(paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
