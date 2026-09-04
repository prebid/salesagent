"""Guard: BDD CI shards cover every tests/bdd file exactly once."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.ci.shard_split import (
    SHARD_COUNTS,
    _assign_greedy_by_scenario_count,
    assign_files_to_shards,
    bdd_scenario_count,
    list_suite_files,
)
from scripts.ci.workflow_helpers import CI_WORKFLOW_PATH
from tests._collection_manifest import BDD_TREE, load, manifest_dir

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _pytest_bdd_module_paths() -> set[str]:
    """Every BDD module the real run collected, independent of shard_split's glob.

    Reads the FULL-TREE record, never a shard's. A shard is invoked with the
    paths `shard_paths.py` produced, so its rows ARE the assignment, and
    comparing those against `assign_files_to_shards()` below would be circular:
    a file the glob misses is absent from both sides and the guard would agree
    with itself. `[testenv:bdd]` and `[testenv:bdd_e2e]` are both invoked as
    `pytest tests/bdd/`, so the full-tree record exists on either run path and
    is derived from pytest's own collection rather than from the glob.

    `-k e2e_rest` on the bdd_e2e path is irrelevant here: rows are retained
    before deselection, so the module set is complete either way.
    """
    rows = load(manifest_dir(), target=BDD_TREE)
    paths = {row["nodeid"].split("::")[0] for row in rows}
    if not paths:
        pytest.fail("the full-tree collection record contains no BDD module paths")
    return paths


@pytest.mark.arch_guard
def test_bdd_shards_partition_suite() -> None:
    expected = _pytest_bdd_module_paths()
    buckets = assign_files_to_shards("bdd", repo_root=_REPO_ROOT)
    assigned = {path for paths in buckets.values() for path in paths}

    assert len(buckets) == SHARD_COUNTS["bdd"]
    assert assigned == expected


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
        _assign_greedy_by_scenario_count(files, len(files) + 1, _REPO_ROOT)


@pytest.mark.arch_guard
def test_bdd_shard_scenario_load_is_balanced() -> None:
    """Greedy min-load assignment should keep shard totals within ~35%."""
    buckets = assign_files_to_shards("bdd", repo_root=_REPO_ROOT)
    loads = [sum(bdd_scenario_count(path, repo_root=_REPO_ROOT) for path in paths) for paths in buckets.values()]
    assert loads, "BDD shard assignment produced no files"
    assert max(loads) / min(loads) <= 1.35, (
        f"BDD shard scenario loads too skewed: {dict(zip(buckets.keys(), loads, strict=True))}"
    )
