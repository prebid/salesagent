"""Deterministic CI test sharding for BDD parallel jobs.

Files are bin-packed HEAVIEST FIRST onto the shard holding the least work so far,
where "work" is the MEASURED seconds that file took in a recorded run
(``bdd_shard_timings.json``, refreshed by ``refresh_shard_timings.py``). Ties, and
files with no recorded timing, fall back to a scenario-count estimate.

Two things here were wrong before and each one alone unbalanced the split.

COST. The load was a file's Gherkin scenario count. A scenario is not a unit of
work: one may be an outline of twelve examples across three transports, another
may xfail at fixture setup for nothing. Measured on run innet_150926_0531 the
count called the two shards even -- 586 against 569, and their collected node
counts agreed too at 4096 against 4207 -- while they actually held 120 and 91
minutes. CI killed both at its 25-minute cap, one at 50% and the other at 88%.
The estimate cannot see a scenario going from dormant to executing, which is
precisely what this branch has spent weeks doing.

ORDER. Files were placed in sorted PATH order. Greedy bin-packing only balances
when the biggest items are placed first: sorted by name, a 45-minute file can
arrive last and land on whichever shard is already full. Descending cost is the
standard longest-processing-time rule, and it is what makes the greedy choice
below worth making.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_PLAIN_SCENARIO = re.compile(r"^\s*Scenario:\s", re.MULTILINE)
_SCENARIO_OUTLINE = re.compile(r"^\s*Scenario Outline:", re.MULTILINE)
_SCENARIOS_CALL = re.compile(r"""scenarios\s*\(\s*["'](features/[^"']+)["']""")
#: ``@scenario(FEATURE, "name")``: a module binding a feature one scenario at a time, because
#: most of that feature has no steps yet. Each decorator is one bound scenario.
_SCENARIO_DECORATOR = re.compile(r"^@scenario\s*\(", re.MULTILINE)

# Keep in sync with strategy.matrix.shard in .github/workflows/ci.yml
# (bdd-tests-shard). Pinned by test_ci_bdd_matrix_matches_shard_config.
#
# FOUR, not two, and not the three that looks like it should do. The suite holds
# ~212 minutes of measured work; at -n auto on a 4-vCPU runner that is ~53 minutes
# of wall-clock. Split three ways it is ~17.7 minutes per shard plus ~1.5 minutes
# of setup, which clears the 25-minute cap only while the balance stays perfect and
# leaves nothing for the next scenarios to go live into. Four gives ~13 minutes and
# real headroom.
SHARD_COUNTS: dict[str, int] = {
    "bdd": 4,
}

# Measured seconds per test file, from a recorded run. Absolute values are
# environment-specific; only the RATIO between files is used, so a map recorded on
# one box balances correctly on another.
_TIMINGS_PATH = Path(__file__).resolve().parent / "bdd_shard_timings.json"

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
        bound_by_name = len(_SCENARIO_DECORATOR.findall(text))
        if bound_by_name:
            return bound_by_name
        raise ValueError(f"No scenarios() or @scenario() feature binding found in {test_path}")
    feature_path = root / "tests/bdd" / match.group(1)
    if not feature_path.is_file():
        raise ValueError(f"Feature file not found for {test_path}: {feature_path}")
    feature_text = feature_path.read_text(encoding="utf-8")
    return len(_PLAIN_SCENARIO.findall(feature_text)) + len(_SCENARIO_OUTLINE.findall(feature_text))


@cache
def _recorded_timings() -> dict[str, float]:
    """Measured seconds per test file, or an empty map when none is recorded."""
    if not _TIMINGS_PATH.is_file():
        return {}
    return {str(k): float(v) for k, v in json.loads(_TIMINGS_PATH.read_text(encoding="utf-8")).items()}


def file_cost(test_path: str, repo_root: Path | None = None) -> float:
    """The balancing cost of one test file, in seconds.

    A measured duration when the recorded map has one. Otherwise the file's
    scenario count priced at the recorded suite's average seconds-per-scenario --
    so a NEW test file is estimated at the suite's going rate rather than treated
    as free. A file assumed free is the failure mode that matters here: every
    unpriced file lands on the same shard, because that shard keeps looking empty.

    With no recorded map at all (a fresh checkout, or the file deleted), this
    degrades to the bare scenario count, which is the behaviour this module had
    before timings existed.
    """
    root = repo_root or _REPO_ROOT
    timings = _recorded_timings()
    measured = timings.get(test_path)
    if measured is not None:
        return measured
    scenarios = bdd_scenario_count(test_path, repo_root=root)
    return scenarios * _seconds_per_scenario(root)


@cache
def _seconds_per_scenario(repo_root: Path) -> float:
    """The recorded suite's average seconds per scenario, for pricing new files.

    1.0 when nothing is recorded, which makes :func:`file_cost` return the plain
    scenario count and reproduces the pre-timings behaviour exactly.
    """
    timings = _recorded_timings()
    if not timings:
        return 1.0
    total_seconds = sum(timings.values())
    total_scenarios = 0
    for test_path in timings:
        try:
            total_scenarios += bdd_scenario_count(test_path, repo_root=repo_root)
        except (FileNotFoundError, ValueError):
            continue  # a file the map remembers but the tree no longer has
    return total_seconds / total_scenarios if total_scenarios else 1.0


def _assign_greedy_by_cost(
    files: list[str],
    shard_count: int,
    repo_root: Path,
) -> dict[int, list[str]]:
    """Longest-processing-time-first greedy bin-packing.

    Heaviest file first onto the lightest shard. Sorting by descending cost is what
    makes greedy placement balance at all; the path-ordered version this replaced
    could hand a 45-minute file to an already-full shard simply because its name
    sorted last. The path is the tiebreak so the split stays deterministic for two
    files of identical cost -- shard_paths.py is called once per shard, in separate
    CI jobs, and they must agree without communicating.
    """
    if shard_count > len(files):
        raise ValueError(f"shard_count={shard_count} exceeds {len(files)} bdd files; a shard would be empty")
    loads: dict[int, float] = dict.fromkeys(range(1, shard_count + 1), 0.0)
    buckets: dict[int, list[str]] = {i: [] for i in range(1, shard_count + 1)}
    for path in sorted(files, key=lambda p: (-file_cost(p, repo_root=repo_root), p)):
        shard = min(range(1, shard_count + 1), key=lambda index: (loads[index], index))
        buckets[shard].append(path)
        loads[shard] += file_cost(path, repo_root=repo_root)
    return {index: sorted(paths) for index, paths in buckets.items()}


def assign_files_to_shards(suite: str, repo_root: Path | None = None) -> dict[int, list[str]]:
    if suite != "bdd":
        raise KeyError(f"Unknown suite {suite!r}")
    root = repo_root or _REPO_ROOT
    files = list_suite_files(suite, repo_root=root)
    shard_count = SHARD_COUNTS[suite]
    return _assign_greedy_by_cost(files, shard_count, root)


def paths_for_shard(suite: str, shard: int, repo_root: Path | None = None) -> list[str]:
    buckets = assign_files_to_shards(suite, repo_root=repo_root)
    if shard not in buckets:
        raise ValueError(f"Shard {shard} out of range 1..{SHARD_COUNTS[suite]} for {suite}")
    return buckets[shard]
