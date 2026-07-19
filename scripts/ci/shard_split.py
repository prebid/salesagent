"""Deterministic CI test sharding for BDD parallel jobs.

BDD files are assigned one at a time (sorted path order) to the shard with the
lowest current scenario count. Ties go to the lowest shard index. Whole-feature
``scenarios(...)`` bindings count scenarios in Gherkin; selected ``@scenario``
bindings count only the explicitly collected cases.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_PLAIN_SCENARIO = re.compile(r"^\s*Scenario:\s", re.MULTILINE)
_SCENARIO_OUTLINE = re.compile(r"^\s*Scenario Outline:", re.MULTILINE)

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
    tree = ast.parse(text, filename=test_path)
    constants = {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }

    def feature_arg(call: ast.Call) -> str | None:
        if not call.args:
            return None
        value = call.args[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        if isinstance(value, ast.Name):
            return constants.get(value.id)
        return None

    whole_features: list[str] = []
    selected_features: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "scenarios":
            path = feature_arg(node)
            if path is not None:
                whole_features.append(path)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name):
                    if decorator.func.id == "scenario":
                        path = feature_arg(decorator)
                        if path is not None:
                            selected_features.append(path)

    if whole_features and selected_features:
        raise ValueError(f"Mixed scenarios() and @scenario bindings in {test_path}")
    if not whole_features and not selected_features:
        raise ValueError(f"No scenarios()/@scenario feature binding found in {test_path}")

    bound_features = whole_features or selected_features
    for feature in bound_features:
        feature_path = root / "tests/bdd" / feature
        if not feature_path.is_file():
            raise ValueError(f"Feature file not found for {test_path}: {feature_path}")

    if selected_features:
        return len(selected_features)

    count = 0
    for feature in whole_features:
        feature_text = (root / "tests/bdd" / feature).read_text(encoding="utf-8")
        count += len(_PLAIN_SCENARIO.findall(feature_text)) + len(_SCENARIO_OUTLINE.findall(feature_text))
    return count


def _assign_greedy_by_scenario_count(
    files: list[str],
    shard_count: int,
    repo_root: Path,
) -> dict[int, list[str]]:
    if shard_count > len(files):
        raise ValueError(f"shard_count={shard_count} exceeds {len(files)} bdd files; a shard would be empty")
    loads: dict[int, int] = dict.fromkeys(range(1, shard_count + 1), 0)
    buckets: dict[int, list[str]] = {i: [] for i in range(1, shard_count + 1)}
    for path in files:
        shard = min(range(1, shard_count + 1), key=lambda index: (loads[index], index))
        buckets[shard].append(path)
        loads[shard] += bdd_scenario_count(path, repo_root=repo_root)
    return buckets


def assign_files_to_shards(suite: str, repo_root: Path | None = None) -> dict[int, list[str]]:
    if suite != "bdd":
        raise KeyError(f"Unknown suite {suite!r}")
    root = repo_root or _REPO_ROOT
    files = list_suite_files(suite, repo_root=root)
    shard_count = SHARD_COUNTS[suite]
    return _assign_greedy_by_scenario_count(files, shard_count, root)


def paths_for_shard(suite: str, shard: int, repo_root: Path | None = None) -> list[str]:
    buckets = assign_files_to_shards(suite, repo_root=repo_root)
    if shard not in buckets:
        raise ValueError(f"Shard {shard} out of range 1..{SHARD_COUNTS[suite]} for {suite}")
    return buckets[shard]
