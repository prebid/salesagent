# PR 4 — Hook relocation + structural guards

**Drift items closed:** PD16, PD17, PD18, PD19, PD20, PD21, PD22
**Estimated effort:** 2 days
**Depends on:** PR 3 Phase C merged (CI / Quality Gate must exist before deleting hooks whose work moves there)
**Blocks:** PR 5 (PR 5 is independent but conventionally lands after PR 4 to keep the rollout's commit history clean)
**Decisions referenced:** D3, D9, D12, D17, D18, D27, D29, D31, D44

**Pre-requisite (per A25):** PR 4 deletes hooks whose work moved to `CI / Quality Gate`. Phase B (PR 3) MUST be complete and the 14 frozen checks MUST be enforced as required-checks before PR 4 commit 7 lands. Verify:
```bash
gh api repos/$OWNER/$REPO/branches/main/protection/required_status_checks --jq '.checks | length'
# expect: 14
```
Without this, deleting commit-stage hooks creates a coverage gap until PR 3 lands.

## Scope

Per-hook reassignment per the layered architecture. Drops warm pre-commit latency from ~23s to ~1.7s (10× improvement). Migrates 5 grep-based hooks to AST-based structural guards. Moves **10** medium-cost hooks to `pre-push` stage (per D27 — `mypy` is the 10th, added per D3). Migrates 4 expensive hooks to CI-only. Deletes 6 dead/advisory hooks. Adds `@pytest.mark.arch_guard` marker per D12.

**Internal commit ordering is load-bearing:** all new structural guards must pass on main BEFORE any hook is deleted. The spec enforces this.

> **Marker disambiguation rationale.** `tests/conftest.py:25-45` registers `architecture` as an entity-marker auto-applied by filename pattern (`test_architecture_*.py`, `no_toolerror_in_impl`, `transport_agnostic_impl`, etc.). PR 4 originally planned a second `architecture` marker for structural guards; same name, different semantics → silent conflation under `pytest -m architecture` (selects union of entity-tagged + guard-tagged). Rename the new structural-guard marker to `arch_guard` to disambiguate. The entity-marker stays as-is.

## Out of scope

- Pre-commit-uv installation (zero `language: python` hooks remain after PR 2; pre-commit-uv has no effect)
- Re-litigating decision D7 (prek)
- New CI checks beyond `CI / Quality Gate` work absorption (PR 3 owns the workflow)
- v2.0's `.guard-baselines/` migration (those become entries in CLAUDE.md once v2.0 phases land; PR 4 reserves space)

## Pre-flight measurements

These steps run BEFORE Commit 1 to capture empirical numbers used by later commits. Record results in the PR description.

### Pre-flight P8 — mypy warm-time capture (P1)

Before Commit 5 moves `mypy` to `pre-push`, verify it fits the Layer-2 budget (`~10-20s` per the layered architecture spec).

```bash
# Run the same invocation used by `make quality` and the planned pre-push hook.
for i in 1 2 3; do
  time uv run mypy src/ --config-file=mypy.ini > /dev/null 2>&1
done
# Record runs 2 and 3 (run 1 includes cold cache build); take the max as warm time.
```

Decision tree:
- **warm ≤ 20s**: proceed with mypy on pre-push as planned in Commit 5 (the 10th move).
- **warm > 20s**: defer mypy from pre-push, keep CI-only per D3's original framing. Re-do D27 math: `36 − 13 − 9 − 2 + 1 = 13` — OVER ceiling. Requires one more delete/move; candidates: `no-hardcoded-urls` (move to pre-push if Pattern #6 enforcement gets a structural-guard equivalent first), or consolidate `mcp-schema-alignment` into the new `repo-invariants` hook from Commit 6.

Document the decision and the measured warm time in the PR description.

## Internal commit sequence

ORDER IS LOAD-BEARING. Guards added before hook deletions.

### Commit 1 — `chore(pre-commit): add default_install_hook_types + minimum_pre_commit_version; verify arch_guard marker; extend _architecture_helpers.py; document pre-commit floor in contributing.md`

Files:
- `.pre-commit-config.yaml` (add `default_install_hook_types: [pre-commit, pre-push]` per D31 AND `minimum_pre_commit_version: 3.2.0` per D44 at top, before `repos:`)
- `tests/unit/_architecture_helpers.py` (**EXTEND** — file already created in PR 2 commit 8 as ~30-line baseline; this PR grows it to ~221 lines with the AST-walking helpers below)
- `docs/development/contributing.md` (**Round 14 B4 add**: document the 3.2.0 minimum pre-commit version + upgrade commands. Currently neither `CONTRIBUTING.md` nor `docs/development/contributing.md` mentions any pre-commit floor; without this, a contributor on Debian-stable's older pre-commit will hit a `FatalError` from `minimum_pre_commit_version` and have no in-doc remediation path. Add a 3-4 line block under the existing "pre-commit" subsection: "This repo requires pre-commit ≥3.2.0 (for the `pre-push` stage name introduced in 3.2.0). If `pre-commit install` errors with a version-too-old `FatalError`, run `uv tool install pre-commit` (preferred) or `pip install --upgrade pre-commit`.")
- `pytest.ini` — **NOT modified by this PR**. Marker registration is owned by PR 2 commit 8 (per D29). This commit VERIFIES (grep) registration; does not re-write.

Per D12 (helpers structure), D29 (marker name), D31 (`default_install_hook_types` directive).

**Ownership rule (resolves Blocker #3):**
- `_architecture_helpers.py`: PR 2 commit 8 creates the baseline (`repo_root`, `parse_module` mtime-keyed cache, `iter_function_defs`, `iter_call_expressions`, `src_python_files`). PR 4 commit 1 EXTENDS by appending the additional helpers (`iter_workflow_files`, `iter_compose_files`, `iter_action_uses`, `iter_python_version_anchors`, `iter_postgres_image_refs`, `assert_violations_match_allowlist`, `assert_anchor_consistency`, `format_failure`). The final reconciled module is at `.claude/notes/ci-refactor/drafts/_architecture_helpers.py` (221 lines) — lift verbatim during execution.
- `pytest.ini [pytest].markers` `arch_guard` registration: PR 2 commit 8 OWNS the write. PR 4 commit 1 VERIFIES only (avoids dual-registration).

`.pre-commit-config.yaml` change (add the directives at the top of the file, before any `repos:` block):

```yaml
# .pre-commit-config.yaml — top of file
# D44: minimum_pre_commit_version raises a FatalError at `pre-commit install` time
# on pre-commit < 3.2.0. The 3.2.0 floor is required because PR 4 uses the modern
# `pre-push` stage name (introduced in 3.2.0; legacy name is `push`). On pre-commit
# 2.11–3.1.x, `pre-push` is unrecognized → the 10 hooks at `stages: [pre-push]`
# silently do not register. minimum_pre_commit_version makes the version mismatch
# loud (FatalError, exits non-zero) rather than letting the warning slip past.
# (Round 14 B4: rationale corrected — `default_install_hook_types` itself is a
# pre-commit ≥2.11.0 feature; the 3.2.0 floor is for the `pre-push` stage name.)
minimum_pre_commit_version: 3.2.0

# D31: auto-install both pre-commit AND pre-push hook types when contributors
# run `pre-commit install` (no need for `--hook-type pre-push` qualifier).
# Without this, the 10 hooks moved to stages: [pre-push] (per D27) silently
# don't run on contributor machines — see D31 / R33.
default_install_hook_types: [pre-commit, pre-push]

repos:
  # ... existing repos preserved verbatim ...
```

This is the load-bearing two-liner that makes D27's hook math operational. Top-OSS norm (pydantic, FastAPI, ruff). Without `default_install_hook_types`, `pre-commit install` only installs pre-commit-stage hooks; pre-push tier silently no-ops. Without `minimum_pre_commit_version: 3.2.0`, contributors on older pre-commit versions get a warning about the unknown `default_install_hook_types` key (easy to miss in install output) AND have the modern `pre-push` stage name silently rejected — both failure modes are made loud by the version floor.

`tests/unit/_architecture_helpers.py`:

```python
"""Shared helpers for AST-based structural guard tests.

Used by tests/unit/test_architecture_*.py to keep guards fast and consistent.
"""
import ast
import functools
import pathlib
from collections.abc import Iterator


@functools.lru_cache(maxsize=2048)
def parse_module(path: pathlib.Path) -> ast.Module:
    """Parse a Python file once per process. Guards share parsed ASTs."""
    return ast.parse(path.read_text(), filename=str(path))


def iter_function_defs(tree: ast.Module) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def iter_call_expressions(tree: ast.Module, name: str | None = None) -> Iterator[ast.Call]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if name is None:
                yield node
                continue
            if isinstance(node.func, ast.Name) and node.func.id == name:
                yield node
            elif isinstance(node.func, ast.Attribute) and node.func.attr == name:
                yield node


def src_python_files(repo: pathlib.Path) -> Iterator[pathlib.Path]:
    """Yield every .py file under src/, excluding migrations and the original GAM file."""
    excluded_paths = {
        repo / "src" / "adapters" / "google_ad_manager_original.py",
    }
    for path in (repo / "src").rglob("*.py"):
        if path in excluded_paths:
            continue
        yield path


def repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]
```

Verification:
```bash
# default_install_hook_types directive (D31 — load-bearing)
grep -q '^default_install_hook_types:' .pre-commit-config.yaml || \
  { echo "FAIL: default_install_hook_types missing from .pre-commit-config.yaml"; exit 1; }
grep -E '^default_install_hook_types:.*pre-commit.*pre-push' .pre-commit-config.yaml || \
  { echo "FAIL: directive present but missing pre-commit + pre-push values"; exit 1; }
# minimum_pre_commit_version directive (D44 — protects D31 from silent ignore on pre-commit <3.2)
grep -qE '^minimum_pre_commit_version:\s*3\.2' .pre-commit-config.yaml || \
  { echo "FAIL: minimum_pre_commit_version: 3.2.0 missing from .pre-commit-config.yaml (D44)"; exit 1; }

# File exists from PR 2 commit 8 baseline
test -f tests/unit/_architecture_helpers.py
grep -q 'parse_module' tests/unit/_architecture_helpers.py   # baseline marker
# Marker registered in pytest.ini by PR 2 commit 8 (verify-only; PR 4 does NOT write here)
grep -q '^[[:space:]]*arch_guard:' pytest.ini

# This commit adds the extended helpers (must all be importable):
uv run python -c "from tests.unit._architecture_helpers import (
    parse_module, iter_function_defs, iter_call_expressions, src_python_files, repo_root,
    iter_workflow_files, iter_compose_files, iter_action_uses,
    iter_python_version_anchors, iter_postgres_image_refs,
    assert_violations_match_allowlist, assert_anchor_consistency, format_failure,
); print('OK')"

# `--strict-markers` is set in pytest.ini, so an unregistered `arch_guard` would error
# before any guard test runs — verifying registration is required.

# After this commit, contributors who run `pre-commit install` get BOTH hook types
# automatically (no --hook-type pre-push needed). Verify on a fresh clone:
#   git clone <repo> /tmp/scratch && cd /tmp/scratch
#   uv run pre-commit install
#   ls .git/hooks/ | grep -E '^(pre-commit|pre-push)$'   # both present
```

### Commit 1.5 — `test: AST guard pre-existing-violation audit (P0 gate before hook deletion)`

Before Commit 7 deletes the legacy grep hooks, every new AST guard in commit 3 must pass against current `main`. Empirical scan surfaced ~18 pre-existing violations of the `check-rootmodel-access` AST equivalent that the current grep hook tolerates (because grep runs `pass_filenames: true` and only sees the changed-file slice). The new tree-wide guard sees them all.

Known pre-existing `check-rootmodel-access` violations on main:
- `src/core/helpers/account_helpers.py:121`
- `tests/unit/test_targeting_normalizer.py` (lines 17, 22, 27, 33, 43, 50, 56, 62, 92)
- `tests/unit/test_adcp_contract.py` (lines 2624, 2674, 2797)
- `tests/unit/test_auth_removal_simple.py:145`
- `tests/unit/test_pricing_option_rootmodel.py:43`
- `tests/unit/test_product_schema_obligations.py:1691`
- `tests/unit/test_datetime_string_parsing.py:238`
- `tests/integration/test_a2a_skill_invocation.py:77` (already carries `# noqa: rootmodel`, would be honored)

For each new AST guard, choose ONE remediation path:

- **Option A — remediate in this PR.** Fix the violations alongside the guard. Estimate for `check-rootmodel-access`: ~50 LOC of refactor across 8 files (replace `hasattr(x, "root")` with explicit `isinstance(x, RootModel)` or model-field checks).
- **Option B — allowlist with FIXME.** Expand the guard's `ALLOWED_FILES` set (already shown for `src/a2a_server/adcp_a2a_server.py` re a2a-sdk polymorphism); add a `# FIXME(salesagent-xxxx): pre-existing — tracked in ...` comment at each call site referencing a tracking issue. Allowlists must shrink, never grow (per CLAUDE.md structural-guard rules).

Apply the same audit pattern to:
- `test_architecture_no_tenant_config.py` — scan main for any `tenant.config[...]` or `tenant.config` attribute access.
- `test_architecture_jsontype_columns.py` — scan `src/core/database/` for `Column(JSON, ...)` / `mapped_column(JSON, ...)`.
- `test_architecture_import_usage.py` — see also Commit 3 note: tree-wide expansion of an already-AST-based hook may surface cross-module unused imports the per-file scan missed.
- Extended `test_architecture_query_type_safety.py::test_no_legacy_session_query` and `::test_models_use_mapped_not_column` — scan main for `session.query(...)` and top-level `Column(...)` in models.

Hard gate: each of these must pass on main BEFORE Commit 7 runs:

```bash
uv run pytest tests/unit/test_architecture_no_defensive_rootmodel.py -v -x
uv run pytest tests/unit/test_architecture_no_tenant_config.py -v -x
uv run pytest tests/unit/test_architecture_jsontype_columns.py -v -x
uv run pytest tests/unit/test_architecture_import_usage.py -v -x
uv run pytest tests/unit/test_architecture_query_type_safety.py -v -x
```

If any guard fails on main after the chosen remediation path, ESCALATE — do NOT proceed to Commit 7. Record the chosen path (A or B) and the violation count per guard in the PR description.

### Commit 2 — `test: backfill @pytest.mark.arch_guard on existing 27 guards`

Files:
- 23 existing `tests/unit/test_architecture_*.py` files (add marker to each test function)
- 3 transport-boundary guards (`test_no_toolerror_in_impl.py`, `test_transport_agnostic_impl.py`, `test_impl_resolved_identity.py`)

Per D12, every existing structural guard test function gets `@pytest.mark.arch_guard`.

Mechanical operation. For each file, prepend `@pytest.mark.arch_guard` to every `def test_*(...)` line.

Note: these files ALSO carry the entity-marker `architecture` (auto-applied by filename pattern in `tests/conftest.py`). The two markers coexist — `arch_guard` selects the structural-guard subset; `architecture` selects the entity-tagged superset. They overlap but are not identical (entity-marker also covers tests not yet tagged with `arch_guard`).

Verification:
```bash
for f in tests/unit/test_architecture_*.py tests/unit/test_no_toolerror_in_impl.py tests/unit/test_transport_agnostic_impl.py tests/unit/test_impl_resolved_identity.py; do
  test -f "$f" || continue
  test_count=$(grep -c '^def test_\|^    def test_' "$f")
  marker_count=$(grep -B1 'def test_' "$f" | grep -c '@pytest.mark.arch_guard')
  [[ "$test_count" == "$marker_count" ]] || { echo "marker missing in $f: $marker_count/$test_count"; exit 1; }
done
# Run them via the new marker
uv run pytest tests/unit/ -m arch_guard -v 2>&1 | tail -3
# Sanity: entity-marker `architecture` still works (filename auto-tag, not changed)
uv run pytest tests/unit/ -m architecture --collect-only 2>&1 | tail -3
```

### Commit 3 — `test: add 5 new structural guards + lift frozen-checks guard from drafts/ (PR 4 migrations)`

Files:
- `tests/unit/test_architecture_no_tenant_config.py` (new)
- `tests/unit/test_architecture_jsontype_columns.py` (new)
- `tests/unit/test_architecture_no_defensive_rootmodel.py` (new)
- `tests/unit/test_architecture_import_usage.py` (new, ports logic from `.pre-commit-hooks/check_import_usage.py`)
- `tests/unit/test_architecture_query_type_safety.py` (extend with two new test functions: `test_no_legacy_session_query` and `test_models_use_mapped_not_column`)
- **`tests/unit/test_architecture_required_ci_checks_frozen.py` (lift verbatim from `.claude/notes/ci-refactor/drafts/guards/test_architecture_required_ci_checks_frozen.py`)** — referenced as if-existing in PR 3 + PR 6 specs but no PR commit previously lifted it (per R12B-01). Without this lift, R36 mitigation (PR 6 commit 4 updating the expected list) cannot land — the file doesn't exist for PR 6 to modify.

```bash
# Lift step (do this BEFORE writing the 5 new guards, so the helpers import is consistent):
cp .claude/notes/ci-refactor/drafts/guards/test_architecture_required_ci_checks_frozen.py \
   tests/unit/test_architecture_required_ci_checks_frozen.py
```

The drafts/ original stays as audit trail (per D36 lifecycle: drafts/ → production location). The lifted file's _BARE_JOB_NAMES tuple already lists 14 names per R11A-01; PR 6 commit 4 will extend to 15 per R36.

Each guard pattern:

```python
"""<Hook name> structural guard.

Replaces .pre-commit-hooks/<original-script> per PR 4 of CI/pre-commit refactor.
"""
import ast
import pytest
from tests.unit._architecture_helpers import parse_module, src_python_files, repo_root, iter_call_expressions


@pytest.mark.arch_guard
def test_<invariant_name>():
    repo = repo_root()
    violations = []
    for path in src_python_files(repo):
        tree = parse_module(path)
        # ... AST walk specific to this invariant
    assert not violations, "\n".join(violations)
```

**`test_architecture_no_tenant_config.py`** — replaces `no-tenant-config` hook:

```python
@pytest.mark.arch_guard
def test_no_tenant_config_access():
    repo = repo_root()
    violations = []
    for path in src_python_files(repo):
        tree = parse_module(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "config":
                if isinstance(node.value, ast.Name) and node.value.id == "tenant":
                    violations.append(f"{path}:{node.lineno}")
            elif isinstance(node, ast.Subscript):
                if (isinstance(node.value, ast.Name) and node.value.id == "tenant"
                    and isinstance(node.slice, ast.Constant) and node.slice.value == "config"):
                    violations.append(f"{path}:{node.lineno}")
    assert not violations, f"Use per-field columns, not tenant.config: {violations}"
```

**`test_architecture_jsontype_columns.py`** — replaces `enforce-jsontype` hook:

```python
@pytest.mark.arch_guard
def test_json_columns_use_jsontype():
    repo = repo_root()
    violations = []
    db_dir = repo / "src" / "core" / "database"
    for path in db_dir.rglob("*.py"):
        tree = parse_module(path)
        for call in iter_call_expressions(tree):
            # Look for Column(JSON, ...) or mapped_column(JSON, ...)
            func_name = None
            if isinstance(call.func, ast.Name):
                func_name = call.func.id
            elif isinstance(call.func, ast.Attribute):
                func_name = call.func.attr
            if func_name not in {"Column", "mapped_column"}:
                continue
            if not call.args:
                continue
            first_arg = call.args[0]
            if isinstance(first_arg, ast.Name) and first_arg.id == "JSON":
                violations.append(f"{path}:{call.lineno} — use JSONType, not JSON")
    assert not violations, "\n".join(violations)
```

**`test_architecture_no_defensive_rootmodel.py`** — replaces `check-rootmodel-access` hook:

```python
ALLOWED_FILES = {
    # a2a-sdk polymorphism legitimately uses hasattr(x, "root")
    "src/a2a_server/adcp_a2a_server.py",
}


@pytest.mark.arch_guard
def test_no_defensive_rootmodel_access():
    repo = repo_root()
    violations = []
    for path in (*src_python_files(repo), *(repo / "tests").rglob("*.py")):
        rel = str(path.relative_to(repo))
        if rel in ALLOWED_FILES:
            continue
        tree = parse_module(path)
        for call in iter_call_expressions(tree, "hasattr"):
            if (len(call.args) >= 2
                and isinstance(call.args[1], ast.Constant)
                and call.args[1].value == "root"):
                # Allow # noqa: rootmodel via line comment
                violations.append(f"{path}:{call.lineno}")
    assert not violations, "\n".join(violations)
```

**`test_architecture_import_usage.py`** — extends `.pre-commit-hooks/check_import_usage.py` (already AST-based, 243 LOC; uses `ast.parse` + `ImportCollector`/`UsageCollector` visitors at lines 15-105) from per-file mode (`pass_filenames: true`) to tree-wide invocation. The existing visitors are reused; only the file-iteration scope changes. The guard is the heaviest of the new ones; profile to confirm < 2s.

Tree-wide expansion may surface violations the per-file scan missed (cross-module unused imports — e.g., a symbol imported in module A and only used by module B's now-deleted code path). Apply Commit 1.5's audit pattern: scan main, choose remediate vs allowlist, document in the PR description.

**Extend `test_architecture_query_type_safety.py`** with two new test functions:
- `test_no_legacy_session_query` — fails on `session.query(Foo)` patterns (replaces `enforce-sqlalchemy-2-0` hook)
- `test_models_use_mapped_not_column` — fails on top-level `Column(...)` instead of `mapped_column(...)` in models

Verification:
```bash
for f in tests/unit/test_architecture_no_tenant_config.py \
         tests/unit/test_architecture_jsontype_columns.py \
         tests/unit/test_architecture_no_defensive_rootmodel.py \
         tests/unit/test_architecture_import_usage.py; do
  test -f "$f"
  grep -q '@pytest.mark.arch_guard' "$f"
done
# All new + extended guards pass on main (per Commit 1.5 audit gate):
uv run pytest tests/unit/test_architecture_no_tenant_config.py \
              tests/unit/test_architecture_jsontype_columns.py \
              tests/unit/test_architecture_no_defensive_rootmodel.py \
              tests/unit/test_architecture_import_usage.py \
              tests/unit/test_architecture_query_type_safety.py -v -x
```

### Commit 4 — `chore(pre-commit): add coverage map for hook deletions`

Files:
- `.pre-commit-coverage-map.yml` (new, ~30 lines)

Maps every hook PR 4 deletes to its replacement enforcement point. Used by the verification step and by CLAUDE.md updates.

```yaml
# Map of pre-commit hooks deleted/moved in PR 4 → where their enforcement now lives.
# Used by .claude/notes/ci-refactor/scripts/verify-pr4.sh.
no-tenant-config:
  enforced_by: guard
  location: tests/unit/test_architecture_no_tenant_config.py
enforce-jsontype:
  enforced_by: guard
  location: tests/unit/test_architecture_jsontype_columns.py
check-rootmodel-access:
  enforced_by: guard
  location: tests/unit/test_architecture_no_defensive_rootmodel.py
enforce-sqlalchemy-2-0:
  enforced_by: guard
  location: tests/unit/test_architecture_query_type_safety.py::test_no_legacy_session_query
check-import-usage:
  enforced_by: guard
  location: tests/unit/test_architecture_import_usage.py
check-gam-auth-support:
  enforced_by: ci-step
  location: .github/workflows/ci.yml::quality-gate
check-response-attribute-access:
  enforced_by: ci-step
  location: .github/workflows/ci.yml::quality-gate
check-roundtrip-tests:
  enforced_by: ci-step
  location: .github/workflows/ci.yml::quality-gate
check-code-duplication:
  enforced_by: ci-step
  location: .github/workflows/ci.yml::quality-gate
check-migration-heads:
  enforced_by: guard-existing
  location: tests/unit/test_architecture_single_migration_head.py
check-parameter-alignment:
  enforced_by: guard-existing
  location: tests/unit/test_architecture_boundary_completeness.py
adcp-contract-tests:
  enforced_by: pre-push + ci
  location: stages_prepush + .github/workflows/ci.yml::schema-contract
mcp-contract-validation:
  enforced_by: pre-push + ci
  location: stages_prepush + .github/workflows/ci.yml::schema-contract
check-docs-links:
  enforced_by: pre-push
  location: stages_prepush
check-route-conflicts:
  enforced_by: pre-push
  location: stages_prepush
type-ignore-no-regression:
  enforced_by: pre-push
  location: stages_prepush
no-skip-integration-v2:
  enforced_by: deleted
  location: dead-code (tests/integration_v2/ does not exist)
mcp-endpoint-tests:
  enforced_by: deleted
  location: dead-echo (entry was a printed help message)
suggest-test-factories:
  enforced_by: deleted
  location: advisory (never failed)
pytest-unit:
  enforced_by: deleted
  location: redundant (CI / Unit Tests is authoritative)
```

Verification:
```bash
test -f .pre-commit-coverage-map.yml
yamllint -d relaxed .pre-commit-coverage-map.yml
uv run python -c "
import yaml
m = yaml.safe_load(open('.pre-commit-coverage-map.yml'))
for hook, target in m.items():
    assert target['enforced_by'] in {'guard', 'guard-existing', 'ci-step', 'pre-push', 'pre-push + ci', 'deleted'}
    assert target.get('location'), hook
print(f'Coverage map: {len(m)} entries')
"
```

### Commit 5 — `refactor(pre-commit): move medium-cost hooks to pre-push stage`

Files:
- `.pre-commit-config.yaml` (modify hook definitions)

Add `stages: [pre-push]` to:
- `check-docs-links`
- `check-route-conflicts`
- `type-ignore-no-regression`
- `adcp-contract-tests`
- `mcp-contract-validation`
- `mcp-schema-alignment` (medium-cost YAML schema validation; only matters when schemas/ change)
- `check-tenant-context-order` (Python script invocation; not formatter-fast)
- `ast-grep-bdd-guards` (only relevant pre-push since BDD tests run there)
- `check-migration-completeness` (only matters if `alembic/versions/` changed)
- `mypy` (per D3 — conditional on Pre-flight P8 warm-time check; CI's `CI / Type Check` job is authoritative)

Closes PD16. **Resolves Blocker #2:** post-rollout commit-stage hook count must reach ≤12. **Real baseline (disk-verified): 36 effective commit-stage hooks** — 40 active `- id:` entries (line 187 `adcp-schema-sync` is commented out) minus 4 at `stages: [manual]` (`smoke-tests`, `test-migrations`, `pytest-unit`, `mcp-endpoint-tests`). Earlier "thirty-three-effective" framing was off by 3 (40 vs the assumed 36 + 4 vs 3 manual).

Of plan's 16 total deletions, **3 are already manual** (`pytest-unit`, `mcp-endpoint-tests`, `test-migrations`) — they reduce the dead-manual count, not the commit-stage count. So the deletion sweep removes 13 commit-stage hooks plus 3 manual stubs (`pytest-unit`, `mcp-endpoint-tests`, `test-migrations`) = 16 total deletions; only the 13 commit-stage count toward the math reduction since the 3 manual stubs were already excluded from the effective baseline.

**Real math:** 36 effective commit-stage − 13 commit-stage deletions − **10** moves to pre-push − 1 consolidation = **12 commit-stage hooks** (exactly at ≤12 ceiling, zero headroom).

**Math note:** the `−1` consolidation term is shorthand for `−2 deletions + 1 new consolidation hook` (Commit 6 deletes the `no-skip-tests` and `no-fn-calls` grep one-liners and adds the single `repo-invariants` hook). Equivalent expanded form: `36 − 13 − 10 − 2 + 1 = 12`.

The 10 moves to pre-push:
1. `check-docs-links`
2. `check-route-conflicts`
3. `type-ignore-no-regression`
4. `adcp-contract-tests`
5. `mcp-contract-validation`
6. `mcp-schema-alignment`
7. `check-tenant-context-order`
8. `ast-grep-bdd-guards`
9. `check-migration-completeness`
10. **`mypy`** (per D3 — PR 2 creates the local mypy hook at commit-stage during the migration window for invocation parity; PR 4 moves it to pre-push because CI's `CI / Type Check` job is authoritative). Without this 10th move, math is `36−13−9−1=13`, OVER ceiling.

**Coordination with v2.0**: PR 4 (this spec) now owns `test-migrations` deletion (it was previously delegated to a v2.0 phase PR). Net commit-stage count math is unchanged because `test-migrations` is already `stages: [manual]` and was excluded from the 36-effective baseline. Verify post-rebase: if a v2.0 phase PR landed first and already deleted `test-migrations`, drop it from PR 4's Commit 7 deletion list — otherwise the deletion fails.

**Zero headroom warning**: if v2.0 phase PRs add ANY new commit-stage hook before PR 4 lands, the math goes over ceiling. Re-verify disk count at PR 4 authoring time; if >36 effective commit-stage, identify an additional move candidate from `no-hardcoded-urls` (Pattern #6 gate; could move to pre-push if Pattern #6 enforcement gets a structural-guard equivalent), `check-ast` (fast but adds startup overhead per file), or any hook with average duration > 200ms.

Verification:
```bash
# All 10 hooks must be at pre-push stage (mypy is conditional — see Pre-flight P8)
for hook in check-docs-links check-route-conflicts type-ignore-no-regression \
            adcp-contract-tests mcp-contract-validation \
            mcp-schema-alignment check-tenant-context-order ast-grep-bdd-guards \
            check-migration-completeness mypy; do
  yq ".repos[].hooks[] | select(.id == \"$hook\") | .stages" .pre-commit-config.yaml | grep -q pre-push \
    || { echo "hook $hook not at pre-push stage"; exit 1; }
done
```

### Commit 6 — `refactor(pre-commit): consolidate grep one-liners into check_repo_invariants.py`

Files:
- `.pre-commit-hooks/check_repo_invariants.py` (new, ~80 lines)
- `.pre-commit-config.yaml` (replace `no-skip-tests` and `no-fn-calls` `sh -c` hooks with single Python hook)

Closes PD21 (smaller scope than issue claimed — only 2 grep one-liners remain after Group B migrations).

```python
#!/usr/bin/env python3
"""Repo invariants — consolidates grep-based pre-commit hooks.

Each check function returns a list of "<file>:<line>: <message>" strings.
Adding a new check: write a function, add to CHECKS list, done.
"""
import re
import sys
from pathlib import Path


def check_no_skip_tests(files: list[Path]) -> list[str]:
    """Forbid @pytest.mark.skip without skip_ci justification."""
    pattern = re.compile(r'@pytest\.mark\.skip(?!_ci)')
    out = []
    for f in files:
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if pattern.search(line):
                out.append(f"{f}:{i}: @pytest.mark.skip forbidden (use skip_ci with justification)")
    return out


def check_no_fn_calls(files: list[Path]) -> list[str]:
    """Detect untyped fn() calls in places typed signatures are expected."""
    # ... port logic from existing hook
    return []


CHECKS = [check_no_skip_tests, check_no_fn_calls]


def main(argv: list[str]) -> int:
    files = [Path(p) for p in argv[1:] if p.endswith(".py")]
    if not files:
        return 0
    all_errors: list[str] = []
    for check in CHECKS:
        all_errors.extend(check(files))
    if all_errors:
        for e in all_errors:
            print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

`.pre-commit-config.yaml` change:

```yaml
      - id: repo-invariants
        name: Repo invariants (no-skip-tests, no-fn-calls, etc.)
        entry: uv run python .pre-commit-hooks/check_repo_invariants.py
        language: system
        types: [python]
        pass_filenames: true
```

Verification:
```bash
test -f .pre-commit-hooks/check_repo_invariants.py
uv run python -m py_compile .pre-commit-hooks/check_repo_invariants.py
! grep -qE '^\s+- id: no-skip-tests$' .pre-commit-config.yaml
! grep -qE '^\s+- id: no-fn-calls$' .pre-commit-config.yaml
yq '.repos[].hooks[] | select(.id == "repo-invariants") | .id' .pre-commit-config.yaml | grep -qx repo-invariants
uv run pre-commit run repo-invariants --all-files
```

### Commit 7 — `refactor(pre-commit): delete migrated and dead hooks`

Files:
- `.pre-commit-config.yaml` (delete the following hook definitions)

Hooks deleted (migrated):
- `no-tenant-config` (covered by guard B1)
- `enforce-jsontype` (covered by guard B2)
- `check-rootmodel-access` (covered by guard B3)
- `enforce-sqlalchemy-2-0` (covered by extended query_type_safety guard)
- `check-import-usage` (covered by guard B5)
- `check-gam-auth-support` (covered by CI / Quality Gate)
- `check-response-attribute-access` (covered by CI / Quality Gate)
- `check-roundtrip-tests` (covered by CI / Quality Gate)
- `check-code-duplication` (covered by CI / Quality Gate; pylint R0801 ratchet preserved)

Hooks deleted (dead/advisory):
- `check-parameter-alignment` (advisory `|| echo`; covered by `test_architecture_boundary_completeness.py` per D-pending-2)
- `pytest-unit` (advisory `|| echo`; CI / Unit Tests is authoritative)
- `mcp-endpoint-tests` (entry is a literal `echo` string)
- `suggest-test-factories` (advisory)
- `no-skip-integration-v2` (greps `tests/integration_v2/` which doesn't exist)
- `check-migration-heads` (redundant with `test_architecture_single_migration_head.py` per PD22)
- `test-migrations` (`.pre-commit-config.yaml:153-158`; references `adcp_local.db` and `migrate.py` which don't exist on disk; uses sqlite which violates "Postgres exclusively" per CLAUDE.md). Already at `stages: [manual]` so net commit-stage count is unchanged. Previously delegated to v2.0 phase PR; brought into PR 4 to consolidate dead-hook removal.

Closes PD16, PD17, PD18, PD19, PD20, PD22.

Verification:
```bash
for hook in no-tenant-config enforce-jsontype check-rootmodel-access enforce-sqlalchemy-2-0 \
            check-import-usage check-gam-auth-support check-response-attribute-access \
            check-roundtrip-tests check-code-duplication check-parameter-alignment \
            pytest-unit mcp-endpoint-tests suggest-test-factories no-skip-integration-v2 \
            check-migration-heads test-migrations; do
  ! grep -qE "^\s+- id: $hook$" .pre-commit-config.yaml || { echo "still exists: $hook"; exit 1; }
done

# Hook count
HOOKS_COMMIT=$(uv run python -c "
import yaml
cfg = yaml.safe_load(open('.pre-commit-config.yaml'))
default = cfg.get('default_stages', ['pre-commit', 'commit'])
n = 0
for r in cfg['repos']:
    for h in r['hooks']:
        stages = h.get('stages', default)
        if 'pre-commit' in stages or 'commit' in stages:
            n += 1
print(n)
")
[[ "$HOOKS_COMMIT" -le 12 ]] || { echo "commit hook count $HOOKS_COMMIT > 12 — D27 P0 ceiling exceeded; identify additional pre-push candidates per PR 4 §Commit 5 zero-headroom warning"; exit 1; }
[[ "$HOOKS_COMMIT" -ge 10 ]] || { echo "commit hook count $HOOKS_COMMIT < 10 — likely an over-deletion; re-verify the 13 deletion list"; exit 1; }

# Soft-warning band: at exactly 11/12, the next added Layer-1 hook will hit the hard cap.
# Warn at exactly 11; investigate via D27 / pr4-hook-relocation.md.
if [[ "$HOOKS_COMMIT" -eq 11 ]]; then
  echo "WARNING: hook count is 11/12 ceiling — adding a Layer-1 hook will hit hard cap."
  echo "         Investigate next-move candidates via D27 / pr4-hook-relocation.md."
  echo "         Realistic future moves include no-hardcoded-urls (if Pattern #6 gets a"
  echo "         structural-guard equivalent) or check-ast (fast but adds startup overhead)."
fi
```

**Structural guard `test_architecture_pre_commit_hook_count` enforces the same band.** The guard:
- Hard fail when `count > 12` (D27 P0 ceiling).
- Hard fail when `count < 10` (over-deletion sentinel).
- Soft warn (printed via `pytest.warns` or `print` to stderr; non-blocking) when `count == 11`, listing the candidate next-moves above.

### Commit 8 — `chore: latency baseline post-PR-4`

Capture the new warm latency:

```bash
pre-commit clean
pre-commit run --all-files >/dev/null 2>&1 || true
{ time pre-commit run --all-files >/dev/null; } 2>&1 | tee .pre-commit-latency-after.txt
```

Acceptance: warm < 5s (issue #1234's bar; the bar may tighten to < 2s after PR 4's hook moves stabilize — handled as an inline acceptance criterion adjustment, not a separate decision-log item).

If wall-clock > 5s, profile and fix or escalate.

Verification:
```bash
test -f .pre-commit-latency-after.txt
T=$(grep -oE 'real[[:space:]]+[0-9]+m[0-9.]+s' .pre-commit-latency-after.txt | tail -1)
python -c "
import re, sys
m = re.match(r'real\s+(\d+)m([\d.]+)s', '$T')
secs = int(m[1]) * 60 + float(m[2])
print(f'warm latency: {secs:.2f}s')
sys.exit(0 if secs < 5 else 1)
"
```

### Commit 9 — DEFERRED to post-v2.0-rebase

**Action:** PR 4 commit 9 (CLAUDE.md guards table audit) **defers** to a post-v2.0-rebase
follow-up commit. Rationale: v2.0 phase PR adds 3 of D18's 5 "missing rows" (`bdd_obligation_sync`,
`bdd_no_direct_call_impl`, `test_marker_coverage`). PR 4 commit 9 only adds the residual 2
(`test_architecture_no_silent_except.py`, `test_architecture_production_session_add.py`), and
even those should land AFTER v2.0 rebases to avoid table churn. The full ~81-row table audit
(post-v2.0; D18 revised in Round 8 — was 73, drift-corrected after v2.0 architecture/ count re-verified at 27 not 31) is a separate follow-up, not a PR 4 deliverable.

**PR 4 commit 9 minimal scope:** add ONLY the 2 residual rows; verify all PR 4-introduced
guards (4 new + 1 extended) appear in the table. Defer the broader 23→~81 audit (per D18 Round 8 revision — was ~73; corrected after v2.0 architecture/ count was re-verified at 27, not 31).

---
**EXECUTOR: SKIP THIS LEGACY BLOCK. The deferral above is canonical.**
---

### Commit 9 (LEGACY SPEC — DO NOT EXECUTE — preserved for reference only)

Files:
- `CLAUDE.md` (extend the structural-guards table per D18)

Add 4 new rows (B1-B5 minus the extension):
- No tenant_config column access
- JSONType columns
- No defensive RootModel
- Import usage

CLAUDE.md guard count post-PR-4: the table audit DEFERS to post-v2.0-rebase. PR 4 commit 9
adds only the 2 residual rows (`test_architecture_no_silent_except.py`,
`test_architecture_production_session_add.py`) plus the 4 new + 1 extended PR 4 guards.
Final count after v2.0 lands: **~81** rows (27 baseline + 1 PR 2 + 4 PR 4 + 1 PR 5 + 8 PR 1/3/6
governance + 27 v2.0 architecture tests + 4 v2.0 top-level + 9 v2.0 baseline JSONs) per D18 Round 8 revision (was ~73; corrected after v2.0 architecture/ count was re-verified at 27, not 31). Do NOT update the
"~81" number in CLAUDE.md until v2.0 phase PRs land — premature update creates phantom rows.

Verification:
```bash
# Every PR 4 guard has a row
for f in test_architecture_no_tenant_config.py test_architecture_jsontype_columns.py \
         test_architecture_no_defensive_rootmodel.py test_architecture_import_usage.py; do
  grep -qF "$f" CLAUDE.md || { echo "missing in CLAUDE.md: $f"; exit 1; }
done
# Guard count text reflects 32
grep -qE '32 (existing )?guards' CLAUDE.md || grep -qE '\b32\b' CLAUDE.md
```

### Commit 10 — `docs: update ci-pipeline.md and structural-guards.md for layered model`

Files:
- `docs/development/ci-pipeline.md` (rewrite/expand the existing ~70-line file)
- `docs/development/structural-guards.md` (extend with PR 4 + PR 2 additions)

Both files exist; this is a rewrite/expansion, not a new file.

Outline for `ci-pipeline.md` post-rollout:
1. Purpose
2. Layer 1 — pre-commit stage (~1-2s)
3. Layer 2 — pre-push stage (~10-20s)
4. Layer 3 — pytest structural guards (in tox -e unit)
5. Layer 4 — CI required checks (the 14 frozen names per D17 amended by D30)
6. Layer 5 — manual / on-demand
7. Decision tree (when to run what)
8. Coverage baseline mechanics
9. Duplication baseline mechanics
10. Type-ignore baseline mechanics
11. How to add a new hook
12. How to add a new structural guard

For `structural-guards.md` additions:
- Pre-commit no additional_deps Guard (PR 2)
- No tenant_config column access Guard (PR 4)
- JSONType columns Guard (PR 4)
- No defensive RootModel Guard (PR 4)
- Import usage Guard (PR 4)
- AST-helper utility (`tests/unit/_architecture_helpers.py`)
- `@pytest.mark.arch_guard` marker (note: the filename pattern `test_architecture_*.py` and the helper module name remain `_architecture_helpers.py` — only the pytest marker name changes; entity-marker `architecture` from `tests/conftest.py` is unaffected)

Verification:
```bash
for f in docs/development/ci-pipeline.md docs/development/structural-guards.md; do
  test -f "$f"
  [[ $(wc -l < "$f") -ge 100 ]]
done
grep -qE 'Layer 1.*pre-commit' docs/development/ci-pipeline.md
grep -qE 'pre_commit_no_additional_deps' docs/development/structural-guards.md
```

### Commit 10a — `chore(pre-commit): install pre-push hook stage; add arch-guards entry`

**Required follow-on commit.** Without this, the 10 hooks moved to `stages: [pre-push]` in commit 5 don't actually run for contributors who haven't installed the pre-push hook stage.

Files:
- `.pre-commit-config.yaml` — add the `arch-guards` pre-push entry from
  `drafts/precommit-prepush-hook.md:5-15` (a single hook that runs `pytest tests/unit/ -m arch_guard -x -q`).

Plus a documentation step (folded into commit 10b below):
- `CONTRIBUTING.md` (or `docs/development/contributing.md` per D21) — add a one-line
  "Install both stages: `pre-commit install --hook-type pre-commit --hook-type pre-push`"
  instruction in the local-setup section.

Without this commit, contributors run `pre-commit install` (default = pre-commit stage only),
push commits, and discover the 10 pre-push hooks via failed CI rather than locally.

Verification:
```bash
# arch-guards entry exists at pre-push stage
yq '.repos[].hooks[] | select(.id == "arch-guards") | .stages' .pre-commit-config.yaml \
  | grep -q pre-push
# CONTRIBUTING.md (canonical copy per D21 — `docs/development/contributing.md`) mentions both stages
grep -q 'hook-type pre-commit' docs/development/contributing.md
grep -q 'hook-type pre-push' docs/development/contributing.md
```

### Commit 10a-bis — `chore(make): pre-push install nudge in `make quality``

**Required follow-on commit.** Existing contributors already have `pre-commit install` (commit-stage only) but not `--hook-type pre-push`. After PR 4, their pre-push hooks won't fire locally — CI catches it but the user-experience is "I thought my hooks ran." This commit adds a non-blocking nudge.

Files:
- `scripts/check-hook-install.sh` (new, ~10 lines)
- `Makefile` — prepend `scripts/check-hook-install.sh` to the `quality:` target

`scripts/check-hook-install.sh`:
```bash
#!/bin/bash
if [[ ! -f .git/hooks/pre-push ]]; then
  echo "WARN: pre-push hooks not installed locally."
  echo "      Run: pre-commit install --hook-type pre-push"
  echo "      (CI still catches everything; this is a local-feedback warning.)"
fi
```

Non-blocking by design — emits a warning, exits 0. The script is the FIRST command in `make quality` so contributors see the warning every quality-gate run until they install the hook stage.

Update both `CONTRIBUTING.md` (root) and `docs/development/contributing.md` (canonical per D21) to document the pre-push install requirement, with the same `pre-commit install --hook-type pre-commit --hook-type pre-push` snippet.

Verification:
```bash
test -x scripts/check-hook-install.sh
grep -qE '^[[:space:]]*scripts/check-hook-install\.sh' Makefile
# Both docs mention pre-push install
grep -q 'hook-type pre-push' CONTRIBUTING.md
grep -q 'hook-type pre-push' docs/development/contributing.md
```

### Commit 10b — `chore(pre-commit): classify no-hardcoded-urls`

**Required follow-on commit.** PR 4's original spec did not classify the
`no-hardcoded-urls` hook (Critical Pattern #6 enforcement — gates JS/template hardcoded
URLs). Decision: **KEEP IN PRE-COMMIT** (commit-stage). Rationale: the hook runs in <100ms
on changed files only, gates a critical-path pattern (script_root discipline) that has no
structural-guard equivalent, and contributor JS edits benefit from immediate fail-fast
feedback. Cannot move to pre-push without losing that feedback; cannot delete without
losing the invariant.

Files:
- `.pre-commit-config.yaml` — add a `# kept in pre-commit — Pattern #6 gate, no guard equivalent` comment near the `no-hardcoded-urls` hook entry. No structural change.

Verification:
```bash
# Hook is at commit-stage (default; not in stages: [...])
yq '.repos[].hooks[] | select(.id == "no-hardcoded-urls")' .pre-commit-config.yaml | grep -v 'stages:'
```

## Acceptance criteria

From issue #1234 §Acceptance criteria, scoped to PR 4:

- [ ] `.pre-commit-config.yaml` hook count ≤ 12 for pre-commit stage (real target: 10, with 2-hook headroom)
- [ ] `stages: [pre-push]` used for medium-cost hooks
- [ ] 6 guard files added/modified: 4 new (`test_architecture_no_tenant_config.py`, `test_architecture_jsontype_columns.py`, `test_architecture_no_defensive_rootmodel.py`, `test_architecture_import_usage.py`) + 1 extension (`test_architecture_query_type_safety.py` gets two new test functions) + 1 lifted from drafts (`test_architecture_required_ci_checks_frozen.py` per R12B-01) — all passing in `tox -e unit`
- [ ] No `always_run: true` except file-level hygiene hooks
- [ ] No advisory-only hooks (`|| echo`, `|| true`) remain
- [ ] Warm `time pre-commit run --all-files` completes in < 5s

Plus agent-derived:

- [ ] `@pytest.mark.arch_guard` marker registered in `pytest.ini` and backfilled to all 27 existing guards
- [ ] `tests/unit/_architecture_helpers.py` shared module exists
- [ ] `.pre-commit-coverage-map.yml` documents every deleted/moved hook's replacement
- [ ] `check_repo_invariants.py` consolidates the 2 remaining grep one-liners
- [ ] CLAUDE.md guards table audit DEFERRED to post-v2.0-rebase (PR 4 commit 9 adds only the residual 2 missing rows; broader audit follows v2.0)
- [ ] `docs/development/ci-pipeline.md` rewritten with the 5-layer model
- [ ] `docs/development/structural-guards.md` extended with PR 2 + PR 4 additions

## Hook → Stage Reference Table

Canonical post-PR-4 mapping. Lift verbatim into `docs/development/ci-pipeline.md` (Commit 10) and reference from `CONTRIBUTING.md`. Counts are projections; verify against `.pre-commit-config.yaml` after Commit 7.

### Layer 1 — pre-commit (commit-stage; exactly 12 hooks; budget ~1-2s warm)

**Source of truth**: this table is post-PR-4 disk truth. Verified: `36 effective − 13 commit-stage deletions − 10 moves − 2 grep consolidation sources + 1 new repo-invariants = 12`.

| # | Hook ID | Owner | Purpose |
|---|---|---|---|
| 1 | `ruff` (with `--fix`) | astral-sh/ruff-pre-commit | Format + lint Python (incl. C90, PLR) |
| 2 | `black` | psf/black | Format Python |
| 3 | `trailing-whitespace` | pre-commit-hooks | File hygiene |
| 4 | `end-of-file-fixer` | pre-commit-hooks | File hygiene |
| 5 | `check-yaml` | pre-commit-hooks | File hygiene |
| 6 | `check-merge-conflict` | pre-commit-hooks | File hygiene |
| 7 | `check-ast` | pre-commit-hooks | Python syntax validation |
| 8 | `check-json` | pre-commit-hooks | JSON syntax validation |
| 9 | `check-added-large-files` | pre-commit-hooks | File hygiene |
| 10 | `debug-statements` | pre-commit-hooks | Forbid `pdb`/`breakpoint()` |
| 11 | `no-hardcoded-urls` | local | Pattern #6 gate (script_root); kept here per Commit 10b |
| 12 | `repo-invariants` | local (Commit 6) | NEW consolidation hook (no-skip-tests, no-fn-calls) |

### Layer 2 — pre-push (push-stage; budget ~10-20s)

| Hook ID | Owner | Purpose |
|---|---|---|
| `arch-guards` | local (Commit 10a) | Runs `pytest tests/unit/ -m arch_guard -x -q` |
| `check-docs-links` | local | Docs link integrity (moved from commit-stage) |
| `check-route-conflicts` | local | Flask route conflicts (moved) |
| `type-ignore-no-regression` | local | type:ignore baseline ratchet (moved) |
| `adcp-contract-tests` | local | AdCP schema contract (moved) |
| `mcp-contract-validation` | local | MCP transport contract (moved) |
| `mcp-schema-alignment` | local | YAML schema validation (moved) |
| `check-tenant-context-order` | local | Python invocation (moved) |
| `ast-grep-bdd-guards` | local | BDD step structural checks (moved) |
| `check-migration-completeness` | local | Alembic migration completeness (moved) |
| `mypy` | local | Type check on changed src tree (moved per D3, conditional on Pre-flight P8) |

### Layer 3 — pytest structural guards (in `tox -e unit`; in CI only)

Run via `pytest tests/unit/ -m arch_guard -x` (Layer 2 hook above runs same command). 27 baseline + 4 new + 1 extended = 32 guards post-PR-4 (count rises post-v2.0).

### Layer 4 — CI required checks (the 14 frozen names per D17 amended by D30; see PR 3)

The canonical 14 (rendered names — workflow `name: CI` + bare job name; GitHub auto-prefixes per D26):

`CI / Quality Gate` · `CI / Type Check` · `CI / Schema Contract` · `CI / Security Audit` · `CI / Quickstart` · `CI / Smoke Tests` · `CI / Unit Tests` · `CI / Integration Tests` · `CI / E2E Tests` · `CI / Admin UI Tests` · `CI / BDD Tests` · `CI / Migration Roundtrip` · `CI / Coverage` · `CI / Summary`.

(Earlier draft revisions listed only eleven check names with several inventions — `Lint`, `Format`, `Coverage Report` — and shorthand `Admin Tests` instead of `Admin UI Tests`. None of those exist. The list above mirrors D17/D30/D26 verbatim. PR 6's `Security / Dependency Review` is OUTSIDE the 14; it lives in `security.yml` namespace.)

### Layer 5 — manual / on-demand (`stages: [manual]`)

| Hook ID | Status | Purpose |
|---|---|---|
| `smoke-tests` | active | Run via `pre-commit run smoke-tests --hook-stage manual` |
| `pytest-unit` | DELETED in Commit 7 | Was redundant with `CI / Unit Tests` |
| `mcp-endpoint-tests` | DELETED in Commit 7 | Was a literal echo string |
| `test-migrations` | DELETED in Commit 7 | sqlite + missing files (per Change 5) |

## Verification (full PR-level)

```bash
bash .claude/notes/ci-refactor/scripts/verify-pr4.sh
```

Inline:

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "[1/8] Hook count..."
HOOKS_COMMIT=$(uv run python -c "
import yaml
cfg = yaml.safe_load(open('.pre-commit-config.yaml'))
default = cfg.get('default_stages', ['pre-commit', 'commit'])
n = 0
for r in cfg['repos']:
    for h in r['hooks']:
        stages = h.get('stages', default)
        if 'pre-commit' in stages or 'commit' in stages:
            n += 1
print(n)
")
[[ "$HOOKS_COMMIT" -le 12 ]]

echo "[2/8] Pre-push hooks present..."
HOOKS_PUSH=$(uv run python -c "
import yaml
cfg = yaml.safe_load(open('.pre-commit-config.yaml'))
n = sum(1 for r in cfg['repos'] for h in r['hooks'] if 'pre-push' in (h.get('stages') or []))
print(n)
")
# Expect 10 moved hooks + 1 new arch-guards entry = 11; threshold ≥ 10 is loose to tolerate
# the Pre-flight P8 mypy decision (mypy may be deferred from pre-push if warm > 20s).
[[ "$HOOKS_PUSH" -ge 10 ]] || { echo "expected ≥10 pre-push hooks, got $HOOKS_PUSH"; exit 1; }

echo "[3/8] Latency..."
pre-commit clean
pre-commit run --all-files >/dev/null 2>&1 || true   # warm
T=$( { time pre-commit run --all-files >/dev/null; } 2>&1 | grep -oE 'real[[:space:]]+[0-9]+m[0-9.]+s' | tail -1)
python -c "
import re, sys
m = re.match(r'real\s+(\d+)m([\d.]+)s', '$T')
secs = int(m[1]) * 60 + float(m[2])
sys.exit(0 if secs < 5 else 1)
"

echo "[4/8] New guards exist and pass + default_install_hook_types directive..."
for f in test_architecture_no_tenant_config test_architecture_jsontype_columns \
         test_architecture_no_defensive_rootmodel test_architecture_import_usage; do
  test -f "tests/unit/${f}.py"
done
# Use the new structural-guard marker `arch_guard` (not the entity-marker `architecture`)
uv run pytest tests/unit/ -m arch_guard -x
# Sanity: marker is registered in pytest.ini (PR 2 commit 8 owns the write; PR 4 verifies)
grep -q '^[[:space:]]*arch_guard:' pytest.ini
# D31 — default_install_hook_types directive (load-bearing for D27 hook math; mitigates R33)
grep -q '^default_install_hook_types:' .pre-commit-config.yaml || { echo "MISSING D31 directive"; exit 1; }
grep -E '^default_install_hook_types:.*pre-commit.*pre-push' .pre-commit-config.yaml || { echo "D31 directive incomplete"; exit 1; }

echo "[5/8] CI absorption..."
grep -q 'CI / Quality Gate' .github/workflows/ci.yml

echo "[6/8] Coverage map..."
test -f .pre-commit-coverage-map.yml

echo "[7/8] Docs updated..."
[[ $(wc -l < docs/development/ci-pipeline.md) -ge 100 ]]
[[ $(wc -l < docs/development/structural-guards.md) -ge 50 ]]

echo "[8/8] CLAUDE.md table accurate..."
ls tests/unit/test_architecture_*.py | xargs -n1 basename | while read f; do
  grep -qF "$f" CLAUDE.md || { echo "NOT IN CLAUDE.md: $f"; exit 1; }
done

echo "PR 4 verification PASSED"
```

## Risks (scoped to PR 4)

- **R7 — `make quality` regression after hook deletion**: mitigation — internal commit ordering enforces guards-pass-on-main BEFORE hook-delete; coverage map; red-team test list (next section)

## Red-team tests (one per migrated hook)

For each migrated invariant, on a scratch branch:

| Hook | Inject violation | Expected |
|---|---|---|
| `no-tenant-config` | `tenant.config["foo"]` in `src/core/scratch.py` | `test_architecture_no_tenant_config` fails |
| `enforce-jsontype` | `Column(JSON)` in a model | `test_architecture_jsontype_columns` fails |
| `check-rootmodel-access` | `hasattr(x, "root")` in a service file | `test_architecture_no_defensive_rootmodel` fails |
| `enforce-sqlalchemy-2-0` | `session.query(Foo)` | extended `test_architecture_query_type_safety` fails |
| `check-import-usage` | reference unimported `os.path` | `test_architecture_import_usage` fails |
| `check-gam-auth-support` | violate the auth-support contract | CI / Quality Gate fails |
| `check-code-duplication` | introduce duplicated block | CI / Quality Gate fails (pylint R0801) |

Document the test runs in PR description.

## Rollback plan

```bash
git revert -m 1 <PR4-merge-sha>
# admin: pushes via UI; agent does NOT run this command
pre-commit clean && pre-commit install
```

Pre-commit reverts cleanly because hook deletion is symmetric. CI auto-rebalances (CI-only steps no longer needed but harmless until next PR removes).

Recovery: < 10 minutes.

## Merge tolerance

- **PR #1217 (adcp 3.12)**: tolerated. PR 4 doesn't reference adcp.
- **v2.0 phase PR landing on `.pre-commit-hooks/check_hardcoded_urls.py`**: tolerated. PR 4 doesn't touch that file.
- **v2.0 phase PR landing on `.pre-commit-config.yaml`**: high conflict surface; coordinate before opening.
- **v2.0 phase PR landing on `CLAUDE.md`**: tolerated (different sections; v2.0 modifies the patterns text, PR 4 modifies the guards table).
- **v2.0 phase PR landing on `tests/unit/test_architecture_*.py`**: tolerated. v2.0 adds `.guard-baselines/*.json`, not new architecture test files.

## Coordination notes for the maintainer

1. **Before authoring**: PR 3 Phase C must be merged. Verify `CI / Quality Gate` job exists in `ci.yml` and is required.
2. **Between commit 3 (new guards) and commit 7 (hook deletions)**: pause and red-team each guard manually. Document results in the PR description.
3. **After commit 8**: confirm warm latency < 5s. If higher, escalate before commit 9 docs updates.
4. **After merge**: monitor `pre-commit run --all-files` time on first contributor PR; expect ~1.5-2s warm.
