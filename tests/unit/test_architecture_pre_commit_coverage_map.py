"""Guard: .pre-commit-coverage-map.yml entries are valid and referentially intact.

Ensures hook migration mappings stay accurate after renames — phantom paths fail
loudly instead of silently (Pattern 3, #1455).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.ci.workflow_helpers import load_ci_workflow
from tests.unit._architecture_helpers import (
    iter_pre_commit_hooks,
    parse_module,
    repo_root,
)

COVERAGE_MAP_PATH = Path(".pre-commit-coverage-map.yml")
MAKEFILE_PATH = Path("Makefile")

ALLOWED_ENFORCED_BY = frozenset(
    {
        "guard",
        "guard-existing",
        "ci-step",
        "pre-push",
        "pre-push + ci-step",
        "pre-push + ci",
        "ci",
        "deleted",
        "consolidated",
    }
)

_REQUIRED_KEYS = frozenset({"enforced_by", "location"})


def _load_coverage_map(path: Path | None = None) -> dict[str, Any]:
    map_path = path or (repo_root() / COVERAGE_MAP_PATH)
    return yaml.safe_load(map_path.read_text(encoding="utf-8"))


def _pre_commit_hook_ids(cfg_path: Path | None = None) -> set[str]:
    return {hook["id"] for hook in iter_pre_commit_hooks(path=cfg_path)}


def _pre_commit_hook_entries(cfg_path: Path | None = None) -> dict[str, str]:
    return {hook["id"]: hook["entry"] for hook in iter_pre_commit_hooks(path=cfg_path)}


def _pre_commit_hook_scripts(cfg_path: Path | None = None) -> dict[str, list[str]]:
    """Map hook id → script basenames referenced by the hook entry command."""
    scripts: dict[str, list[str]] = {}
    for hook in iter_pre_commit_hooks(path=cfg_path):
        entry = hook["entry"]
        basenames = [Path(part).name for part in entry.split() if part.endswith(".py")]
        scripts[hook["id"]] = basenames
    return scripts


def _pre_commit_hook_stages(cfg_path: Path | None = None) -> dict[str, list[str]]:
    return {hook["id"]: hook["stages"] for hook in iter_pre_commit_hooks(path=cfg_path)}


def _makefile_quality_ci_body(makefile_path: Path | None = None) -> str:
    path = makefile_path or (repo_root() / MAKEFILE_PATH)
    lines = path.read_text(encoding="utf-8").splitlines()
    body: list[str] = []
    in_target = False
    for line in lines:
        if line.startswith("quality-ci:"):
            in_target = True
            continue
        if in_target:
            if line and not line[0].isspace():
                break
            body.append(line)
    return "\n".join(body)


def _schema_contract_pytest_paths(workflow: dict[str, Any] | None = None) -> list[str]:
    jobs = (workflow or load_ci_workflow())["jobs"]
    job = jobs["schema-contract"]
    paths: list[str] = []
    for step in job.get("steps", []):
        uses = step.get("uses", "")
        if not str(uses).endswith("_pytest"):
            continue
        path = step.get("with", {}).get("paths")
        if path:
            paths.append(str(path))
    assert paths, "schema-contract job must declare pytest paths"
    return paths


def _guard_location_parts(location: str) -> tuple[Path, str | None]:
    """Return repo-relative guard file path and optional ``::test_name`` suffix."""
    if "::" in location:
        file_part, test_name = location.split("::", 1)
        return Path(file_part), test_name
    return Path(location), None


def _guard_test_exists(repo: Path, location: str) -> bool:
    path, test_name = _guard_location_parts(location)
    if test_name is None:
        return True
    guard_file = repo / path
    if not guard_file.is_file():
        return False
    tree = parse_module(guard_file)
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == test_name for node in ast.walk(tree)
    )


def _hook_script_names(hook_id: str, hook_scripts: dict[str, list[str]]) -> list[str]:
    """Candidate script basenames for a coverage-map hook id."""
    from_config = hook_scripts.get(hook_id, [])
    underscored = hook_id.replace("-", "_")
    return sorted({*from_config, f"{hook_id}.py", f"{underscored}.py"})


def _script_in_quality_ci(hook_id: str, quality_ci: str, hook_scripts: dict[str, list[str]]) -> bool:
    return any(name in quality_ci for name in _hook_script_names(hook_id, hook_scripts))


def _pytest_paths_from_hook_entry(entry: str) -> list[str]:
    """Extract pytest file/dir targets from a hook entry command."""
    if "pytest" not in entry:
        return []
    return [part for part in entry.split() if part.startswith("tests/")]


def _validate_entry_schema(hook_id: str, entry: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(entry, dict):
        return [f"{hook_id}: entry must be a mapping"]
    missing = _REQUIRED_KEYS - entry.keys()
    if missing:
        errors.append(f"{hook_id}: missing keys {sorted(missing)}")
    enforced_by = entry.get("enforced_by")
    if enforced_by not in ALLOWED_ENFORCED_BY:
        errors.append(f"{hook_id}: invalid enforced_by {enforced_by!r}")
    return errors


def _validate_entry_references(
    hook_id: str,
    entry: dict[str, Any],
    *,
    repo: Path,
    hook_ids: set[str],
    hook_entries: dict[str, str],
    hook_scripts: dict[str, list[str]],
    hook_stages: dict[str, list[str]],
    quality_ci: str,
    schema_contract_paths: list[str],
) -> list[str]:
    errors: list[str] = []
    enforced_by = entry["enforced_by"]
    location = str(entry["location"])

    if enforced_by in {"guard", "guard-existing"}:
        path, test_name = _guard_location_parts(location)
        guard_file = repo / path
        if not guard_file.is_file():
            errors.append(f"{hook_id}: guard location missing: {path}")
        elif test_name is not None and not _guard_test_exists(repo, location):
            errors.append(f"{hook_id}: guard test missing: {location}")

    if enforced_by in {"ci-step", "pre-push + ci-step"}:
        if "Makefile::quality-ci" not in location:
            errors.append(f"{hook_id}: ci-step location must reference Makefile::quality-ci")
        elif not _script_in_quality_ci(hook_id, quality_ci, hook_scripts):
            errors.append(f"{hook_id}: no matching script for hook in Makefile quality-ci")

    if enforced_by in {"pre-push", "pre-push + ci-step", "pre-push + ci"}:
        if hook_id not in hook_ids:
            errors.append(f"{hook_id}: pre-push hook id missing from .pre-commit-config.yaml")
        elif "stages_prepush" in location and "pre-push" not in hook_stages.get(hook_id, []):
            errors.append(f"{hook_id}: location claims stages_prepush but hook lacks pre-push stage")

    if enforced_by in {"pre-push + ci", "ci"}:
        if "schema-contract" not in location:
            errors.append(f"{hook_id}: {enforced_by} location must reference schema-contract job")
        else:
            expected_paths = _pytest_paths_from_hook_entry(hook_entries.get(hook_id, ""))
            for expected in expected_paths:
                if expected not in schema_contract_paths:
                    errors.append(f"{hook_id}: schema-contract job must run {expected} (from hook entry)")

    return errors


def validate_coverage_map(
    coverage_map: dict[str, Any],
    *,
    repo: Path | None = None,
    hook_ids: set[str] | None = None,
    hook_entries: dict[str, str] | None = None,
    hook_scripts: dict[str, list[str]] | None = None,
    hook_stages: dict[str, list[str]] | None = None,
    quality_ci: str | None = None,
    schema_contract_paths: list[str] | None = None,
) -> list[str]:
    """Return all schema and referential-integrity errors for *coverage_map*."""
    root = repo or repo_root()
    cfg_path = root / Path(".pre-commit-config.yaml")
    hooks = hook_ids if hook_ids is not None else _pre_commit_hook_ids(cfg_path)
    entries = hook_entries if hook_entries is not None else _pre_commit_hook_entries(cfg_path)
    scripts = hook_scripts if hook_scripts is not None else _pre_commit_hook_scripts(cfg_path)
    stages = hook_stages if hook_stages is not None else _pre_commit_hook_stages(cfg_path)
    ci_body = quality_ci if quality_ci is not None else _makefile_quality_ci_body(root / MAKEFILE_PATH)
    contract_paths = schema_contract_paths if schema_contract_paths is not None else _schema_contract_pytest_paths()

    errors: list[str] = []
    for hook_id, entry in coverage_map.items():
        errors.extend(_validate_entry_schema(hook_id, entry))
        if isinstance(entry, dict) and entry.keys() >= _REQUIRED_KEYS:
            errors.extend(
                _validate_entry_references(
                    hook_id,
                    entry,
                    repo=root,
                    hook_ids=hooks,
                    hook_entries=entries,
                    hook_scripts=scripts,
                    hook_stages=stages,
                    quality_ci=ci_body,
                    schema_contract_paths=contract_paths,
                )
            )
    return errors


@pytest.mark.arch_guard
def test_coverage_map_schema_and_references() -> None:
    coverage_map = _load_coverage_map()
    errors = validate_coverage_map(coverage_map)
    assert not errors, "Coverage map validation failed:\n" + "\n".join(f"  {e}" for e in errors)


@pytest.mark.arch_guard
def test_coverage_map_prepush_ci_step_entries() -> None:
    """Post-#1454: moved hooks must show pre-push + ci-step + Makefile::quality-ci."""
    coverage_map = _load_coverage_map()
    for hook_id in (
        "check-route-conflicts",
        "type-ignore-no-regression",
        "check-docs-links",
        "no-hardcoded-urls",
    ):
        entry = coverage_map[hook_id]
        assert entry["enforced_by"] == "pre-push + ci-step", hook_id
        assert "stages_prepush" in entry["location"], hook_id
        assert "Makefile::quality-ci" in entry["location"], hook_id


@dataclass(frozen=True)
class _BrokenEntryProbe:
    hook_id: str
    entry: Any
    expected_substr: str
    hook_ids: set[str] | None = None
    hook_entries: dict[str, str] | None = None
    hook_scripts: dict[str, list[str]] | None = None
    hook_stages: dict[str, list[str]] | None = None
    quality_ci: str | None = None
    schema_contract_paths: list[str] | None = None


_BROKEN_ENTRY_PROBES = (
    _BrokenEntryProbe("scalar-entry", "not-a-mapping", "entry must be a mapping"),
    _BrokenEntryProbe("missing-keys", {}, "missing keys"),
    _BrokenEntryProbe(
        "bad-enforced-by",
        {"enforced_by": "bogus", "location": "nowhere"},
        "invalid enforced_by",
    ),
    _BrokenEntryProbe(
        "phantom-guard",
        {"enforced_by": "guard", "location": "tests/unit/test_architecture_does_not_exist.py"},
        "guard location missing",
    ),
    _BrokenEntryProbe(
        "phantom-guard-test",
        {
            "enforced_by": "guard",
            "location": "tests/unit/test_architecture_pre_commit_coverage_map.py::test_does_not_exist",
        },
        "guard test missing",
    ),
    _BrokenEntryProbe(
        "ci-step-no-makefile",
        {"enforced_by": "ci-step", "location": "nowhere"},
        "ci-step location must reference Makefile::quality-ci",
    ),
    _BrokenEntryProbe(
        "ci-step-no-script",
        {"enforced_by": "ci-step", "location": "Makefile::quality-ci"},
        "no matching script for hook",
        quality_ci="",
    ),
    _BrokenEntryProbe(
        "phantom-prepush",
        {"enforced_by": "pre-push", "location": "stages_prepush"},
        "pre-push hook id missing",
        hook_ids=set(),
    ),
    _BrokenEntryProbe(
        "prepush-stage-mismatch",
        {
            "enforced_by": "pre-push + ci-step",
            "location": "stages_prepush + Makefile::quality-ci",
        },
        "location claims stages_prepush but hook lacks pre-push stage",
        hook_ids={"prepush-stage-mismatch"},
        hook_stages={"prepush-stage-mismatch": ["commit"]},
        hook_scripts={"prepush-stage-mismatch": ["check_docs_links.py"]},
        quality_ci="check_docs_links.py",
    ),
    _BrokenEntryProbe(
        "prepush-ci-no-job",
        {"enforced_by": "pre-push + ci", "location": "stages_prepush"},
        "location must reference schema-contract job",
        hook_ids={"prepush-ci-no-job"},
        hook_stages={"prepush-ci-no-job": ["pre-push"]},
        hook_entries={"prepush-ci-no-job": "uv run pytest tests/unit/test_adcp_contract.py"},
    ),
    _BrokenEntryProbe(
        "prepush-ci-missing-test",
        {
            "enforced_by": "pre-push + ci",
            "location": "stages_prepush + .github/workflows/ci.yml::schema-contract",
        },
        "schema-contract job must run tests/unit/test_adcp_contract.py",
        hook_ids={"prepush-ci-missing-test"},
        hook_stages={"prepush-ci-missing-test": ["pre-push"]},
        hook_entries={"prepush-ci-missing-test": "uv run pytest tests/unit/test_adcp_contract.py"},
        schema_contract_paths=["tests/integration/test_mcp_contract_validation.py"],
    ),
    _BrokenEntryProbe(
        "ci-no-job",
        {"enforced_by": "ci", "location": "nowhere"},
        "location must reference schema-contract job",
    ),
    _BrokenEntryProbe(
        "ci-missing-test",
        {
            "enforced_by": "ci",
            "location": ".github/workflows/ci.yml::schema-contract",
        },
        "schema-contract job must run tests/unit/test_adcp_contract.py",
        hook_entries={"ci-missing-test": "uv run pytest tests/unit/test_adcp_contract.py"},
        schema_contract_paths=["tests/integration/test_mcp_contract_validation.py"],
    ),
)


@pytest.mark.arch_guard
@pytest.mark.parametrize("probe", _BROKEN_ENTRY_PROBES, ids=lambda p: p.hook_id)
def test_coverage_map_parser_catches_broken_entries(probe: _BrokenEntryProbe, tmp_path: Path) -> None:
    """Self-test: each validation branch must fail on a deliberately broken entry."""
    repo = repo_root()
    probe_path = tmp_path / "probe.yml"
    probe_path.write_text(
        yaml.safe_dump({probe.hook_id: probe.entry}),
        encoding="utf-8",
    )
    errors = validate_coverage_map(
        _load_coverage_map(probe_path),
        repo=repo,
        hook_ids=probe.hook_ids,
        hook_entries=probe.hook_entries,
        hook_scripts=probe.hook_scripts,
        hook_stages=probe.hook_stages,
        quality_ci=probe.quality_ci,
        schema_contract_paths=probe.schema_contract_paths,
    )
    assert any(probe.expected_substr in e for e in errors), (
        f"expected error containing {probe.expected_substr!r}, got: {errors}"
    )
