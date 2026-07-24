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
from scripts.ci.workflow_helpers import CI_WORKFLOW_PATH

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COLLECT_TAG = re.compile(r"^<(Dir|Package|Module) ([^>]+)>$")


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

    stack: list[str] = []
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        stripped = line.strip()
        match = _COLLECT_TAG.match(stripped)
        if match is None:
            continue
        kind, name = match.groups()
        if kind == "Module":
            if "bdd" not in stack:
                continue
            bdd_idx = stack.index("bdd")
            if bdd_idx < 1:
                continue
            paths.add("/".join(stack[bdd_idx - 1 : bdd_idx + 1] + [name]))
        elif kind in {"Dir", "Package"}:
            stack.append(name)

    if not paths:
        pytest.fail("pytest --collect-only tests/bdd/ returned no BDD module paths")
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


def test_bdd_scenario_count_supports_selected_scenario_bindings(tmp_path: Path) -> None:
    """A driver may bind a reviewed subset without collecting a known-gap feature wholesale."""
    feature_dir = tmp_path / "tests/bdd/features"
    feature_dir.mkdir(parents=True)
    (feature_dir / "selected.feature").write_text(
        """Feature: selected\n  Scenario: one\n    Given x\n  Scenario: two\n    Given x\n  Scenario: unbound\n    Given x\n""",
        encoding="utf-8",
    )
    test_path = tmp_path / "tests/bdd/test_selected.py"
    test_path.write_text(
        """from pytest_bdd import scenario\n_FEATURE = \"features/selected.feature\"\n@scenario(_FEATURE, \"one\")\ndef test_one(): pass\n@scenario(_FEATURE, \"two\")\ndef test_two(): pass\n""",
        encoding="utf-8",
    )

    assert bdd_scenario_count("tests/bdd/test_selected.py", repo_root=tmp_path) == 2


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
