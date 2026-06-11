"""Guard: BDD CI shards cover every tests/bdd file exactly once."""

from __future__ import annotations

import re
import subprocess
import sys
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
from tests.unit.workflow_helpers import CI_WORKFLOW_PATH

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BDD_MODULE_LINE = re.compile(r"^\s*<Module ([^>]+)>\s*$")


def _pytest_bdd_module_paths(repo_root: Path) -> set[str]:
    """Collect BDD test module paths via pytest (independent of shard_split glob)."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/bdd/",
            "--collect-only",
            "-q",
            "--no-header",
        ],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        pytest.fail(f"pytest --collect-only tests/bdd/ failed: {msg}")

    paths: set[str] = set()
    for line in result.stdout.splitlines():
        match = _BDD_MODULE_LINE.match(line)
        if match:
            paths.add(f"tests/bdd/{match.group(1)}")
    if not paths:
        pytest.fail("pytest --collect-only tests/bdd/ returned no <Module …> entries")
    return paths


@pytest.mark.arch_guard
def test_bdd_shards_partition_suite() -> None:
    expected = _pytest_bdd_module_paths(_REPO_ROOT)
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
