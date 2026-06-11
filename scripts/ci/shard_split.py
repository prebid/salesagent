"""Deterministic CI test sharding for BDD parallel jobs.

BDD files are assigned one at a time (sorted path order) to the shard with the
lowest current scenario count. Ties go to the lowest shard index. Scenario counts
are read from each test file's ``scenarios("features/....feature")`` binding.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_SCENARIO_LINE = re.compile(r"^\s*Scenario", re.MULTILINE)
_SCENARIOS_CALL = re.compile(r"""scenarios\s*\(\s*["'](features/[^"']+)["']""")

# Keep in sync with strategy.matrix.shard in .github/workflows/ci.yml (bdd-tests-shard).
SHARD_COUNTS: dict[str, int] = {
    "bdd": 2,
}

SUITE_GLOBS: dict[str, str] = {
    "bdd": "tests/bdd/test_*.py",
}


def list_suite_files(suite: str, repo_root: Path | None = None) -> list[str]:
    if suite not in SUITE_GLOBS:
        raise KeyError(f"Unknown suite {suite!r}")
    root = repo_root or _REPO_ROOT
    return sorted(str(p.relative_to(root)) for p in root.glob(SUITE_GLOBS[suite]))


def bdd_scenario_count(test_path: str, repo_root: Path | None = None) -> int:
    """Return the number of Gherkin scenarios bound by a BDD test module."""
    root = repo_root or _REPO_ROOT
    text = (root / test_path).read_text(encoding="utf-8")
    match = _SCENARIOS_CALL.search(text)
    if match is None:
        raise ValueError(f"No scenarios() feature binding found in {test_path}")
    feature_path = root / "tests/bdd" / match.group(1)
    if not feature_path.is_file():
        raise ValueError(f"Feature file not found for {test_path}: {feature_path}")
    return len(_SCENARIO_LINE.findall(feature_path.read_text(encoding="utf-8")))


def _assign_round_robin(files: list[str], shard_count: int) -> dict[int, list[str]]:
    buckets: dict[int, list[str]] = {i: [] for i in range(1, shard_count + 1)}
    for index, path in enumerate(files):
        buckets[(index % shard_count) + 1].append(path)
    return buckets


def _assign_greedy_by_scenario_count(
    files: list[str],
    shard_count: int,
    repo_root: Path,
) -> dict[int, list[str]]:
    loads: dict[int, int] = dict.fromkeys(range(1, shard_count + 1), 0)
    buckets: dict[int, list[str]] = {i: [] for i in range(1, shard_count + 1)}
    for path in files:
        shard = min(range(1, shard_count + 1), key=lambda index: (loads[index], index))
        buckets[shard].append(path)
        loads[shard] += bdd_scenario_count(path, repo_root=repo_root)
    return buckets


def assign_files_to_shards(suite: str, repo_root: Path | None = None) -> dict[int, list[str]]:
    root = repo_root or _REPO_ROOT
    files = list_suite_files(suite, repo_root=root)
    shard_count = SHARD_COUNTS[suite]
    if suite == "bdd":
        return _assign_greedy_by_scenario_count(files, shard_count, root)
    return _assign_round_robin(files, shard_count)


def paths_for_shard(suite: str, shard: int, repo_root: Path | None = None) -> list[str]:
    buckets = assign_files_to_shards(suite, repo_root=repo_root)
    if shard not in buckets:
        raise ValueError(f"Shard {shard} out of range 1..{SHARD_COUNTS[suite]} for {suite}")
    return buckets[shard]
