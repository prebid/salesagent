"""Guard: .pre-commit-coverage-map.yml entries are valid and referentially intact.

Ensures hook migration mappings stay accurate after renames — phantom paths fail
loudly instead of silently (Pattern 3, #1455).
"""

from __future__ import annotations

import ast
import re
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
TOX_PATH = Path("tox.ini")
RUNNER_PATH = Path("run_all_tests.sh")

ALLOWED_ENFORCED_BY = frozenset(
    {
        "guard",
        "guard-existing",
        "ci-step",
        "pre-push",
        "pre-push + ci-step",
        "pre-push + ci",
        # A ratchet must name an enforcement that actually EXECUTES. pre-push
        # never fires here (git push is never run) and ci.yml triggers only on
        # push/PR to main|develop, so the guard is the load-bearing half and the
        # other two are documentation of where the check ALSO lives
        # (salesagent-aemue.13; rule enforced by
        # test_architecture_ratchet_enforcement.py).
        "guard + pre-push + ci-step",
        # ``gate`` = the full-suite run (run_all_tests.sh / cassini run), for
        # ratchets whose counters are too slow for the unit suite. Validated:
        # the named tox env must exist AND be in the suite list the gate runs,
        # otherwise "gate" would be one more claim nothing checks.
        "gate + pre-push + ci-step",
        "gate + ci-step",
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


def _gate_env_runs_in_the_full_suite(repo: Path, env_name: str) -> bool:
    """True when *env_name* is a real tox env AND the gate actually runs it."""
    tox_body = (repo / TOX_PATH).read_text(encoding="utf-8")
    if f"[testenv:{env_name}]" not in tox_body:
        return False
    env_list = re.search(r"^env_list\s*=\s*(.+)$", tox_body, re.MULTILINE)
    if not env_list or env_name not in {part.strip() for part in env_list.group(1).split(",")}:
        return False
    all_suites = re.search(r'ALL_SUITES="([^"]+)"', (repo / RUNNER_PATH).read_text(encoding="utf-8"))
    return bool(all_suites) and env_name in {part.strip() for part in all_suites.group(1).split(",")}


def _gate_env_name(location: str) -> str | None:
    """``tox.ini::<env>`` component of a possibly compound location."""
    for part in (p.strip() for p in location.split(" + ")):
        if part.startswith("tox.ini::"):
            return part.split("::", 1)[1].split(" -> ", 1)[0].strip()
    return None


def _guard_component(location: str) -> str:
    """The guard part of a possibly compound ``a + b + c`` location.

    Compound locations exist because one hook can be enforced in several places
    at once; only the ``tests/`` component names the guard.
    """
    for part in (p.strip() for p in location.split(" + ")):
        if part.startswith("tests/"):
            return part
    return location


def _guard_location_parts(location: str) -> tuple[Path, str | None]:
    """Return repo-relative guard file path and optional ``::test_name`` suffix."""
    location = _guard_component(location)
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


def _schema_contract_paths_from_location(location: str) -> list[str]:
    """Extract explicit pytest paths after `` -> `` in a schema-contract location."""
    if " -> " not in location:
        return []
    _, paths_part = location.rsplit(" -> ", 1)
    return [part.strip() for part in paths_part.split(",") if part.strip()]


def _schema_contract_expected_paths(
    hook_id: str,
    location: str,
    hook_entries: dict[str, str],
) -> list[str]:
    """Paths the coverage map claims schema-contract must run for *hook_id*."""
    explicit = _schema_contract_paths_from_location(location)
    from_entry = _pytest_paths_from_hook_entry(hook_entries.get(hook_id, ""))
    seen: set[str] = set()
    ordered: list[str] = []
    for path in (*explicit, *from_entry):
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


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

    if {"guard", "guard-existing"} & set(enforced_by.split(" + ")):
        path, test_name = _guard_location_parts(location)
        guard_file = repo / path
        if not guard_file.is_file():
            errors.append(f"{hook_id}: guard location missing: {path}")
        elif test_name is not None and not _guard_test_exists(repo, location):
            errors.append(f"{hook_id}: guard test missing: {location}")

    if "gate" in enforced_by.split(" + "):
        env_name = _gate_env_name(location)
        if env_name is None:
            errors.append(f"{hook_id}: gate location must name a tox env as tox.ini::<env>")
        elif not _gate_env_runs_in_the_full_suite(repo, env_name):
            errors.append(f"{hook_id}: tox env {env_name!r} is not in tox env_list and run_all_tests.sh ALL_SUITES")

    if "ci-step" in enforced_by.split(" + "):
        if "Makefile::quality-ci" not in location:
            errors.append(f"{hook_id}: ci-step location must reference Makefile::quality-ci")
        elif not _script_in_quality_ci(hook_id, quality_ci, hook_scripts):
            errors.append(f"{hook_id}: no matching script for hook in Makefile quality-ci")

    if "pre-push" in enforced_by.split(" + "):
        if hook_id not in hook_ids:
            errors.append(f"{hook_id}: pre-push hook id missing from .pre-commit-config.yaml")
        elif "stages_prepush" in location and "pre-push" not in hook_stages.get(hook_id, []):
            errors.append(f"{hook_id}: location claims stages_prepush but hook lacks pre-push stage")

    if enforced_by in {"pre-push + ci", "ci"}:
        if "schema-contract" not in location:
            errors.append(f"{hook_id}: {enforced_by} location must reference schema-contract job")
        else:
            explicit_paths = _schema_contract_paths_from_location(location)
            expected_paths = _schema_contract_expected_paths(hook_id, location, hook_entries)
            if enforced_by == "ci" and hook_id not in hook_ids and not explicit_paths:
                errors.append(f"{hook_id}: ci-only entry must declare schema-contract test path via ' -> ' in location")
            if not expected_paths:
                errors.append(f"{hook_id}: no schema-contract pytest paths declared (location ' -> ' or hook entry)")
            for expected in expected_paths:
                if expected not in schema_contract_paths:
                    errors.append(f"{hook_id}: schema-contract job must run {expected}")

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
    """Post-#1454: moved hooks must show pre-push + ci-step + Makefile::quality-ci.

    ``type-ignore-no-regression`` left this list in salesagent-aemue.13: as a
    RATCHET it now additionally carries a ``guard``, because pre-push and
    Makefile::quality-ci both fail to run under this repo's documented workflow.
    The three that remain are not ratchets and keep the older shape.
    """
    coverage_map = _load_coverage_map()
    for hook_id in (
        "check-route-conflicts",
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
    # The SAME probe one enforced_by value over. Without it, narrowing the guard branch to
    # the bare string "guard" -- which is what this merge nearly shipped -- silently stops
    # validating every `guard-existing` entry's location, and no probe goes red.
    _BrokenEntryProbe(
        "phantom-guard-existing",
        {
            "enforced_by": "guard-existing",
            "location": "tests/unit/test_architecture_does_not_exist.py",
        },
        "guard location missing",
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
            "location": ".github/workflows/ci.yml::schema-contract -> tests/integration/test_mcp_contract_validation.py",
        },
        "schema-contract job must run tests/integration/test_mcp_contract_validation.py",
        hook_entries={},
        schema_contract_paths=["tests/unit/test_adcp_contract.py"],
    ),
    _BrokenEntryProbe(
        "gate-no-env",
        {"enforced_by": "gate + ci-step", "location": "Makefile::quality-ci"},
        "gate location must name a tox env",
        hook_scripts={"gate-no-env": ["check_code_duplication.py"]},
        quality_ci="check_code_duplication.py",
    ),
    _BrokenEntryProbe(
        "gate-phantom-env",
        {
            "enforced_by": "gate + ci-step",
            "location": "tox.ini::not_a_real_env + Makefile::quality-ci",
        },
        "is not in tox env_list and run_all_tests.sh ALL_SUITES",
        hook_scripts={"gate-phantom-env": ["check_code_duplication.py"]},
        quality_ci="check_code_duplication.py",
    ),
    _BrokenEntryProbe(
        "ci-only-no-path",
        {
            "enforced_by": "ci",
            "location": ".github/workflows/ci.yml::schema-contract",
        },
        "ci-only entry must declare schema-contract test path via ' -> ' in location",
        hook_ids=set(),
        hook_entries={},
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


#: Hooks that carry a ratcheting baseline file. Derived, never hand-listed: a hook
#: qualifies by naming a ``.*baseline*`` path in its own source, so a new ratchet is
#: enrolled by existing rather than by someone remembering to edit a tuple here.
_BASELINE_PATH_RE = re.compile(r"""["'](\.[\w-]*baseline[\w.-]*)["']""")


def _baseline_carrying_hooks(root: Path | None = None) -> dict[str, str]:
    """``{hook script name: baseline file it ratchets}`` for every such hook."""
    hooks_dir = (root or repo_root()) / ".pre-commit-hooks"
    found: dict[str, str] = {}
    for script in sorted(hooks_dir.glob("check_*.py")):
        match = _BASELINE_PATH_RE.search(script.read_text(encoding="utf-8"))
        if match:
            found[script.name] = match.group(1)
    return found


@pytest.mark.arch_guard
def test_every_ratcheting_hook_is_invoked_by_quality_ci() -> None:
    """A ratchet nobody runs is worse than no ratchet: it reads as enforced.

    ``check_fixme_citation_count.py`` shipped with ``.fixme-citation-baseline`` and
    was referenced by nothing — not ``quality-ci``, not ``.pre-commit-config.yaml``.
    The dormancy was demonstrable rather than theoretical: its baseline recorded
    ``tests_fixme_beads: 120`` while the tree held 119, so the auto-lower-on-decrease
    it advertises had never once executed. Nothing could make it go red, so the count
    it claimed to ratchet was free to grow.

    Enrollment is the whole contract. Both halves of a ratchet — the counter and the
    baseline — have to arrive with the line that runs them, or the baseline is a file
    that only ever agrees with itself.
    """
    quality_ci = _makefile_quality_ci_body(repo_root() / MAKEFILE_PATH)
    unenrolled = {name: baseline for name, baseline in _baseline_carrying_hooks().items() if name not in quality_ci}
    assert not unenrolled, (
        "these hooks ratchet a baseline but are never invoked by `make quality-ci`, so they "
        f"cannot fail: {unenrolled}. Add `uv run python .pre-commit-hooks/<hook>` to the "
        "quality-ci target beside its siblings, or delete the hook and its baseline — a gate "
        "that cannot go red is worse than no gate, because it reads as enforced."
    )
