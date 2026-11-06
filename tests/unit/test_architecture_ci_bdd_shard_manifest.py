"""Guard: BDD CI shards cover every tests/bdd file exactly once."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.ci.shard_split import (
    SHARD_COUNTS,
    _assign_greedy_by_cost,
    assign_files_to_shards,
    bdd_scenario_count,
    file_cost,
    list_suite_files,
)
from scripts.ci.workflow_helpers import CI_WORKFLOW_PATH

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.arch_guard
def test_ci_bdd_matrix_matches_shard_config() -> None:
    workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
    matrix = workflow["jobs"]["bdd-tests-shard"]["strategy"]["matrix"]["shard"]
    assert matrix == list(range(1, SHARD_COUNTS["bdd"] + 1))


@pytest.mark.arch_guard
def test_ci_bdd_shard_job_name_uses_matrix_total() -> None:
    """Shard denominator must follow matrix size (not a hardcoded literal)."""
    workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
    name = workflow["jobs"]["bdd-tests-shard"]["name"]
    assert "strategy.job-total" in name, (
        "bdd-tests-shard job name must use strategy.job-total for the shard denominator."
    )


@pytest.mark.arch_guard
def test_bdd_shards_have_discoverable_scenario_counts() -> None:
    for path in list_suite_files("bdd", repo_root=_REPO_ROOT):
        assert bdd_scenario_count(path, repo_root=_REPO_ROOT) >= 1


@pytest.mark.arch_guard
def test_bdd_greedy_split_rejects_shard_count_above_file_count() -> None:
    files = list_suite_files("bdd", repo_root=_REPO_ROOT)
    with pytest.raises(ValueError, match="shard would be empty"):
        _assign_greedy_by_cost(files, len(files) + 1, _REPO_ROOT)


@pytest.mark.arch_guard
def test_bdd_shard_measured_load_is_balanced() -> None:
    """Shard totals stay within 20% of each other, measured in SECONDS.

    Graded on ``file_cost`` -- the recorded duration -- and not on scenario count,
    because scenario count is what let the split go unbalanced while this guard
    stayed green. On run innet_150926_0531 the counts were 586 against 569, a 1.03
    ratio this tolerance passes comfortably, while the shards actually held 120 and
    91 minutes and CI killed both at its 25-minute cap.

    The band is tighter than the 1.35 it replaces because the signal is now the
    thing that matters: with longest-first packing over measured costs there is no
    reason for a 20% spread, and a wider band would re-admit the skew.
    """
    buckets = assign_files_to_shards("bdd", repo_root=_REPO_ROOT)
    loads = [sum(file_cost(path, repo_root=_REPO_ROOT) for path in paths) for paths in buckets.values()]
    assert loads, "BDD shard assignment produced no files"
    assert min(loads) > 0, f"a shard carries no measurable work: {loads}"
    assert max(loads) / min(loads) <= 1.20, (
        "BDD shard measured loads too skewed "
        f"(minutes): { ({k: round(v / 60, 1) for k, v in zip(buckets.keys(), loads, strict=True)}) }. "
        "Refresh scripts/ci/bdd_shard_timings.json from a recent run."
    )


@pytest.mark.arch_guard
def test_new_bdd_file_is_priced_not_treated_as_free() -> None:
    """A file absent from the timing map costs its scenarios at the suite rate.

    The failure this prevents: an unpriced file reads as zero cost, so every new
    file lands on whichever shard therefore looks emptiest, and the newest tests --
    the ones most likely to be slow -- pile onto one runner.
    """
    unknown = "tests/bdd/test_not_in_the_timing_map.py"
    from scripts.ci.shard_split import _recorded_timings

    assert unknown not in _recorded_timings()
    known = next(iter(_recorded_timings()))
    assert file_cost(known, repo_root=_REPO_ROOT) > 0
