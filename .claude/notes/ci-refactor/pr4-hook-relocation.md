# PR 4 — Hook relocation + structural guards

**Drift items closed:** PD16, PD17, PD18, PD19, PD20, PD21, PD22
**Estimated effort:** 2 days
**Depends on:** PR 3 Phase C merged (CI / Quality Gate must exist before deleting hooks whose work moves there)
**Blocks:** PR 5 (PR 5 is independent but conventionally lands after PR 4 to keep the rollout's commit history clean)
**Decisions referenced:** D9, D17, D18

## Scope

Per-hook reassignment per the layered architecture. Drops warm pre-commit latency from ~23s to ~1.7s (10× improvement). Migrates 5 grep-based hooks to AST-based structural guards. Moves **9** medium-cost hooks to `pre-push` stage (per D27 — was 5; Blocker #2 fix). Migrates 4 expensive hooks to CI-only. Deletes 6 dead/advisory hooks. Adds `@pytest.mark.architecture` marker per D12.

**Internal commit ordering is load-bearing:** all new structural guards must pass on main BEFORE any hook is deleted. The spec enforces this.

## Out of scope

- Pre-commit-uv installation (zero `language: python` hooks remain after PR 2; pre-commit-uv has no effect)
- Re-litigating decision D7 (prek)
- New CI checks beyond `CI / Quality Gate` work absorption (PR 3 owns the workflow)
- v2.0's `.guard-baselines/` migration (those become entries in CLAUDE.md once v2.0 phases land; PR 4 reserves space)

## Internal commit sequence

ORDER IS LOAD-BEARING. Guards added before hook deletions.

### Commit 1 — `test: register @pytest.mark.architecture marker; extend _architecture_helpers.py`

Files:
- `pyproject.toml` (add to `[tool.pytest.ini_options].markers`)
- `tests/unit/_architecture_helpers.py` (**EXTEND** — file already created in PR 2 commit 8 as ~30-line baseline; this PR grows it to ~221 lines with the AST-walking helpers below)

Per D12.

**Ownership rule (resolves Blocker #3):** PR 2 commit 8 creates the baseline (`repo_root`, `parse_module` mtime-keyed cache, `iter_function_defs`, `iter_call_expressions`, `src_python_files`). PR 4 commit 1 EXTENDS by appending the additional helpers (`iter_workflow_files`, `iter_compose_files`, `iter_action_uses`, `iter_python_version_anchors`, `iter_postgres_image_refs`, `assert_violations_match_allowlist`, `assert_anchor_consistency`, `format_failure`). The final reconciled module is at `.claude/notes/ci-refactor/drafts/_architecture_helpers.py` (221 lines) — lift verbatim during execution.

```toml
[tool.pytest.ini_options]
markers = [
    "architecture: structural guards (run with -m architecture)",
    # ... existing markers preserved
]
```

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
# File exists from PR 2 commit 8 baseline
test -f tests/unit/_architecture_helpers.py
grep -q 'parse_module' tests/unit/_architecture_helpers.py   # baseline marker
grep -q 'architecture:' pyproject.toml

# This commit adds the extended helpers (must all be importable):
uv run python -c "from tests.unit._architecture_helpers import (
    parse_module, iter_function_defs, iter_call_expressions, src_python_files, repo_root,
    iter_workflow_files, iter_compose_files, iter_action_uses,
    iter_python_version_anchors, iter_postgres_image_refs,
    assert_violations_match_allowlist, assert_anchor_consistency, format_failure,
); print('OK')"
```

### Commit 2 — `test: backfill @pytest.mark.architecture on existing 27 guards`

Files:
- 23 existing `tests/unit/test_architecture_*.py` files (add marker to each test function)
- 3 transport-boundary guards (`test_no_toolerror_in_impl.py`, `test_transport_agnostic_impl.py`, `test_impl_resolved_identity.py`)

Per D12, every existing structural guard test function gets `@pytest.mark.architecture`.

Mechanical operation. For each file, prepend `@pytest.mark.architecture` to every `def test_*(...)` line.

Verification:
```bash
for f in tests/unit/test_architecture_*.py tests/unit/test_no_toolerror_in_impl.py tests/unit/test_transport_agnostic_impl.py tests/unit/test_impl_resolved_identity.py; do
  test -f "$f" || continue
  test_count=$(grep -c '^def test_\|^    def test_' "$f")
  marker_count=$(grep -B1 'def test_' "$f" | grep -c '@pytest.mark.architecture')
  [[ "$test_count" == "$marker_count" ]] || { echo "marker missing in $f: $marker_count/$test_count"; exit 1; }
done
# Run them via marker
uv run pytest tests/unit/ -m architecture -v 2>&1 | tail -3
```

### Commit 3 — `test: add 5 new structural guards (PR 4 migrations)`

Files:
- `tests/unit/test_architecture_no_tenant_config.py` (new)
- `tests/unit/test_architecture_jsontype_columns.py` (new)
- `tests/unit/test_architecture_no_defensive_rootmodel.py` (new)
- `tests/unit/test_architecture_import_usage.py` (new, ports logic from `.pre-commit-hooks/check_import_usage.py`)
- `tests/unit/test_architecture_query_type_safety.py` (extend with two new test functions: `test_no_legacy_session_query` and `test_models_use_mapped_not_column`)

Each guard pattern:

```python
"""<Hook name> structural guard.

Replaces .pre-commit-hooks/<original-script> per PR 4 of CI/pre-commit refactor (#1234).
"""
import ast
import pytest
from tests.unit._architecture_helpers import parse_module, src_python_files, repo_root, iter_call_expressions


@pytest.mark.architecture
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
@pytest.mark.architecture
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
@pytest.mark.architecture
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


@pytest.mark.architecture
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

**`test_architecture_import_usage.py`** — ports `.pre-commit-hooks/check_import_usage.py` (243 LOC) to AST. The guard is the heaviest of the new ones; profile to confirm < 2s.

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
  grep -q '@pytest.mark.architecture' "$f"
done
# All new + extended guards pass on main:
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

Closes PD16. **Resolves Blocker #2:** post-rollout commit-stage hook count must reach ≤12. Current baseline: **37 commit-stage** today (drifted +1 from the 36 in `research/empirical-baseline.md` due to a hook added post-measurement). Math: 37 − 15 deletions − **9** moves to pre-push − 1 consolidation = **12 commit-stage hooks** (at the ≤12 ceiling). If the baseline drifts further upward by execution time, identify additional candidates for pre-push from `mcp-cors-allowlist`, `check-no-private-ssm`, or any other hook with average duration > 200ms.

Verification:
```bash
for hook in check-docs-links check-route-conflicts type-ignore-no-regression \
            adcp-contract-tests mcp-contract-validation \
            mcp-schema-alignment check-tenant-context-order ast-grep-bdd-guards \
            check-migration-completeness; do
  yq ".repos[].hooks[] | select(.id == \"$hook\") | .stages" .pre-commit-config.yaml | grep -q pre-push
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

Closes PD16, PD17, PD18, PD19, PD20, PD22.

Verification:
```bash
for hook in no-tenant-config enforce-jsontype check-rootmodel-access enforce-sqlalchemy-2-0 \
            check-import-usage check-gam-auth-support check-response-attribute-access \
            check-roundtrip-tests check-code-duplication check-parameter-alignment \
            pytest-unit mcp-endpoint-tests suggest-test-factories no-skip-integration-v2 \
            check-migration-heads; do
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
[[ "$HOOKS_COMMIT" -le 12 ]] || { echo "commit hook count $HOOKS_COMMIT > 12 — see PR 4 §Commit 5 for additional pre-push candidates"; exit 1; }
```

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

### Commit 9 — `docs: update CLAUDE.md guards table for PR 4 additions`

Files:
- `CLAUDE.md` (extend the structural-guards table per D18)

Add 4 new rows (B1-B5 minus the extension):
- No tenant_config column access
- JSONType columns
- No defensive RootModel
- Import usage

CLAUDE.md guard count post-PR-4: 32 (28 from PR 2 corrections + 4 PR 4 additions). v2.0's 9 guards from `.guard-baselines/` will land separately.

Update guard count in CLAUDE.md from "24" to "32" wherever it appears.

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
5. Layer 4 — CI required checks (the 11 frozen names)
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
- `@pytest.mark.architecture` marker

Verification:
```bash
for f in docs/development/ci-pipeline.md docs/development/structural-guards.md; do
  test -f "$f"
  [[ $(wc -l < "$f") -ge 100 ]]
done
grep -qE 'Layer 1.*pre-commit' docs/development/ci-pipeline.md
grep -qE 'pre_commit_no_additional_deps' docs/development/structural-guards.md
```

## Acceptance criteria

From issue #1234 §Acceptance criteria, scoped to PR 4:

- [ ] `.pre-commit-config.yaml` hook count ≤ 12 for pre-commit stage
- [ ] `stages: [pre-push]` used for medium-cost hooks
- [ ] 5 new test_architecture_*.py files exist (4 new + 1 extension), all passing in `tox -e unit`
- [ ] No `always_run: true` except file-level hygiene hooks
- [ ] No advisory-only hooks (`|| echo`, `|| true`) remain
- [ ] Warm `time pre-commit run --all-files` completes in < 5s

Plus agent-derived:

- [ ] `@pytest.mark.architecture` marker registered and backfilled to all 27 existing guards
- [ ] `tests/unit/_architecture_helpers.py` shared module exists
- [ ] `.pre-commit-coverage-map.yml` documents every deleted/moved hook's replacement
- [ ] `check_repo_invariants.py` consolidates the 2 remaining grep one-liners
- [ ] CLAUDE.md guards table accurate post-PR-4 (32 rows)
- [ ] `docs/development/ci-pipeline.md` rewritten with the 5-layer model
- [ ] `docs/development/structural-guards.md` extended with PR 2 + PR 4 additions

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
[[ "$HOOKS_PUSH" -ge 5 ]]

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

echo "[4/8] New guards exist and pass..."
for f in test_architecture_no_tenant_config test_architecture_jsontype_columns \
         test_architecture_no_defensive_rootmodel test_architecture_import_usage; do
  test -f "tests/unit/${f}.py"
done
uv run pytest tests/unit/ -m architecture -x

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
git push origin main   # USER ACTION
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
