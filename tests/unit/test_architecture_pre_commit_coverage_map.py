"""Guard: .pre-commit-coverage-map.yml entries are valid and referentially intact.

Ensures hook migration mappings stay accurate after renames — phantom paths fail
loudly instead of silently (Pattern 3, #1455).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.unit._architecture_helpers import repo_root

COVERAGE_MAP_PATH = Path(".pre-commit-coverage-map.yml")
PRE_COMMIT_CONFIG_PATH = Path(".pre-commit-config.yaml")
MAKEFILE_PATH = Path("Makefile")
CI_WORKFLOW_PATH = Path(".github/workflows/ci.yml")

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


def _load_coverage_map(path: Path = COVERAGE_MAP_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _pre_commit_hook_ids(cfg_path: Path = PRE_COMMIT_CONFIG_PATH) -> set[str]:
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    return {hook["id"] for repo in cfg["repos"] for hook in repo["hooks"]}


def _pre_commit_hook_scripts(cfg_path: Path = PRE_COMMIT_CONFIG_PATH) -> dict[str, list[str]]:
    """Map hook id → script basenames referenced by the hook entry command."""
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    scripts: dict[str, list[str]] = {}
    for repo in cfg["repos"]:
        for hook in repo["hooks"]:
            entry = hook.get("entry", "")
            basenames = [Path(part).name for part in entry.split() if part.endswith(".py")]
            scripts[hook["id"]] = basenames
    return scripts


def _makefile_quality_ci_body(makefile_path: Path = MAKEFILE_PATH) -> str:
    lines = makefile_path.read_text(encoding="utf-8").splitlines()
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


def _schema_contract_job_text(ci_path: Path = CI_WORKFLOW_PATH) -> str:
    text = ci_path.read_text(encoding="utf-8")
    match = re.search(r"^  schema-contract:\n(.*?)(?=^  \w|\Z)", text, flags=re.MULTILINE | re.DOTALL)
    assert match, "schema-contract job not found in ci.yml"
    return match.group(1)


def _guard_location_path(location: str) -> Path:
    """Strip ``::test_name`` suffix and return repo-relative guard file path."""
    file_part = location.split("::", 1)[0]
    return Path(file_part)


def _hook_script_names(hook_id: str, hook_scripts: dict[str, list[str]]) -> list[str]:
    """Candidate script basenames for a coverage-map hook id."""
    from_config = hook_scripts.get(hook_id, [])
    underscored = hook_id.replace("-", "_")
    return sorted({*from_config, f"{hook_id}.py", f"{underscored}.py"})


def _script_in_quality_ci(hook_id: str, quality_ci: str, hook_scripts: dict[str, list[str]]) -> bool:
    return any(name in quality_ci for name in _hook_script_names(hook_id, hook_scripts))


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
    hook_scripts: dict[str, list[str]],
    quality_ci: str,
    schema_contract: str,
) -> list[str]:
    errors: list[str] = []
    enforced_by = entry["enforced_by"]
    location = str(entry["location"])

    if enforced_by in {"guard", "guard-existing"}:
        path = repo / _guard_location_path(location)
        if not path.is_file():
            errors.append(f"{hook_id}: guard location missing: {path.relative_to(repo)}")

    if enforced_by in {"ci-step", "pre-push + ci-step"}:
        if "Makefile::quality-ci" not in location:
            errors.append(f"{hook_id}: ci-step location must reference Makefile::quality-ci")
        elif not _script_in_quality_ci(hook_id, quality_ci, hook_scripts):
            errors.append(f"{hook_id}: no matching script for hook in Makefile quality-ci")

    if enforced_by in {"pre-push", "pre-push + ci-step", "pre-push + ci"}:
        if hook_id not in hook_ids:
            errors.append(f"{hook_id}: pre-push hook id missing from .pre-commit-config.yaml")

    if enforced_by == "pre-push + ci":
        if "schema-contract" not in location:
            errors.append(f"{hook_id}: pre-push + ci location must reference schema-contract job")
        elif hook_id == "adcp-contract-tests" and "test_adcp_contract.py" not in schema_contract:
            errors.append(f"{hook_id}: schema-contract job must run test_adcp_contract.py")

    if enforced_by == "ci":
        if "schema-contract" not in location:
            errors.append(f"{hook_id}: ci location must reference schema-contract job")
        elif hook_id == "mcp-contract-validation" and "test_mcp_contract_validation.py" not in schema_contract:
            errors.append(f"{hook_id}: schema-contract job must run test_mcp_contract_validation.py")

    return errors


def validate_coverage_map(
    coverage_map: dict[str, Any],
    *,
    repo: Path | None = None,
    hook_ids: set[str] | None = None,
    hook_scripts: dict[str, list[str]] | None = None,
    quality_ci: str | None = None,
    schema_contract: str | None = None,
) -> list[str]:
    """Return all schema and referential-integrity errors for *coverage_map*."""
    root = repo or repo_root()
    hooks = hook_ids if hook_ids is not None else _pre_commit_hook_ids(root / PRE_COMMIT_CONFIG_PATH)
    scripts = hook_scripts if hook_scripts is not None else _pre_commit_hook_scripts(root / PRE_COMMIT_CONFIG_PATH)
    ci_body = quality_ci if quality_ci is not None else _makefile_quality_ci_body(root / MAKEFILE_PATH)
    contract = schema_contract if schema_contract is not None else _schema_contract_job_text(root / CI_WORKFLOW_PATH)

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
                    hook_scripts=scripts,
                    quality_ci=ci_body,
                    schema_contract=contract,
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


@pytest.mark.arch_guard
def test_coverage_map_parser_catches_broken_guard_location() -> None:
    """Self-test: a phantom guard path must fail validation."""
    repo = repo_root()
    probe = repo / ".pre-commit-coverage-map.probe.yml"
    probe.write_text(
        yaml.safe_dump(
            {
                "phantom-guard": {
                    "enforced_by": "guard",
                    "location": "tests/unit/test_architecture_does_not_exist.py",
                }
            }
        ),
        encoding="utf-8",
    )
    try:
        errors = validate_coverage_map(_load_coverage_map(probe), repo=repo)
        assert any("phantom-guard" in e and "missing" in e for e in errors)
    finally:
        probe.unlink(missing_ok=True)
