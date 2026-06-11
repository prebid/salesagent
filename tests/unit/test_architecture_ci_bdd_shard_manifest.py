"""Guard: BDD CI shards cover every tests/bdd file exactly once."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.ci.shard_split import (
    SHARD_COUNTS,
    assign_files_to_shards,
    bdd_scenario_count,
    list_suite_files,
)
from tests.unit.workflow_helpers import CI_WORKFLOW_PATH

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.arch_guard
def test_bdd_shards_partition_suite() -> None:
    expected = list_suite_files("bdd", repo_root=_REPO_ROOT)
    buckets = assign_files_to_shards("bdd", repo_root=_REPO_ROOT)
    assigned = [path for paths in buckets.values() for path in paths]

    assert len(buckets) == SHARD_COUNTS["bdd"]
    assert set(assigned) == set(expected)
    assert len(assigned) == len(expected)


@pytest.mark.arch_guard
def test_ci_bdd_matrix_matches_shard_config() -> None:
    workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
    matrix = workflow["jobs"]["bdd-tests-shard"]["strategy"]["matrix"]["shard"]
    assert matrix == list(range(1, SHARD_COUNTS["bdd"] + 1))


@pytest.mark.arch_guard
def test_bdd_shards_have_discoverable_scenario_counts() -> None:
    for path in list_suite_files("bdd", repo_root=_REPO_ROOT):
        assert bdd_scenario_count(path, repo_root=_REPO_ROOT) >= 0


@pytest.mark.arch_guard
def test_bdd_shard_scenario_load_is_balanced() -> None:
    """Greedy min-load assignment should keep shard totals within ~35%."""
    buckets = assign_files_to_shards("bdd", repo_root=_REPO_ROOT)
    loads = [sum(bdd_scenario_count(path, repo_root=_REPO_ROOT) for path in paths) for paths in buckets.values()]
    assert loads, "BDD shard assignment produced no files"
    assert max(loads) / min(loads) <= 1.35, (
        f"BDD shard scenario loads too skewed: {dict(zip(buckets.keys(), loads, strict=True))}"
    )
