# PR 2 — uv.lock as single source of truth for pre-commit deps

**Drift items closed:** PD1, PD2, PD8 (partial — already done on v2.0 branch per D20)
**Estimated effort:** 4-6 days (pydantic.mypy delta dominates)
**Depends on:** PR 1 merged
**Blocks:** PR 3 (PR 3 depends on the local-hook pattern this PR establishes)
**Decisions referenced:** D3, D7, D8, D13, D14, D16, D20

## Scope

Eliminate `additional_dependencies` drift in `.pre-commit-config.yaml`. Make `uv.lock` the sole source of truth for Python dependencies in pre-commit. Replace external `mirrors-mypy` and `psf/black` repo blocks with `local` hooks that invoke `uv run mypy` and `uv run black` at `language: system`.

Reactivates the silently-disabled `pydantic.mypy` plugin (currently declared in `mypy.ini:3` but never loaded because pydantic was never in `additional_dependencies`). Per D13, the resulting error delta is fixed in this PR.

Per D14, also migrates `[project.optional-dependencies].ui-tests` to `[dependency-groups].ui-tests` for PEP 735 alignment.

## Out of scope

- Hook architecture redesign (move to pre-push, etc.) → PR 4
- CI workflow changes (the `--extra dev` → `--group dev` migration here is in support of the deletion; the broader CI restructure is PR 3)
- Re-enabling pre-commit-uv (zero `language: python` hooks remain after this PR; pre-commit-uv has no benefit)
- `[project.optional-dependencies].dev` deletion **on the v2.0 branch already done**; verify the merge tolerance section before re-introducing

## Internal commit sequence

ORDER IS LOAD-BEARING. CI will be red between commits 4 and 5 unless commit 4 lands first.

### Commit 1 — `docs: add ADR-001 (single-source pre-commit deps)`

If ADR-001 was already added in PR 1 commit 7, this commit is a no-op. If not, lift the embedded draft from PR 1 spec into `docs/decisions/adr-001-single-source-pre-commit-deps.md`.

Verification:
```bash
test -f docs/decisions/adr-001-single-source-pre-commit-deps.md
grep -q '^# ADR-001:' docs/decisions/adr-001-single-source-pre-commit-deps.md
grep -q '## Status' docs/decisions/adr-001-single-source-pre-commit-deps.md
```

### Commit 2 — `refactor(pre-commit): replace mirrors-mypy with local uv run mypy`

Files:
- `.pre-commit-config.yaml` (delete lines 289-305 mirrors-mypy block; add new `local` hook)

Closes PD1.

**Rationale (do NOT frame as "mirrors-mypy is deprecated" — it is not):** The `pre-commit/mirrors-mypy` repo is actively maintained. The reason to migrate is that mirrors-mypy runs mypy in pre-commit's *isolated* virtualenv where the project's deps aren't visible. mypy then silently emits `--ignore-missing-imports`-grade output that diverges from a direct `uv run mypy src/` invocation, masking real type errors. The `language: system` local hook below invokes mypy against the project's actual environment, restoring the type contract the project claimed to enforce. References: [Jared Khan – mypy in pre-commit](https://jaredkhan.com/blog/mypy-pre-commit), [mypy issue #13916](https://github.com/python/mypy/issues/13916).

The new hook (under `repos[0].hooks` — the existing `local` repo block):

```yaml
      - id: mypy
        name: mypy (project venv)
        entry: uv run mypy --config-file=mypy.ini
        language: system
        types: [python]
        require_serial: true
        pass_filenames: true
        files: '^src/'
        exclude: '^src/adapters/google_ad_manager_original\.py$'
```

Pre-commit on this commit will surface the pydantic.mypy plugin's errors. **STOP at this commit and capture the baseline:**

```bash
uv run mypy src/ --config-file=mypy.ini > .mypy-current.txt 2>&1 || true
diff .mypy-baseline.txt .mypy-current.txt | head -50
echo "errors before: $(grep -c 'error:' .mypy-baseline.txt)"
echo "errors after: $(grep -c 'error:' .mypy-current.txt)"
```

If the delta is > 200 (D13 tripwire), STOP and escalate. Comment out `pydantic.mypy` from `mypy.ini:3` temporarily, file a follow-up issue, and continue with mypy still loading — just without the pydantic plugin. Document the deferral in the PR description.

If the delta is ≤ 200, proceed to Commit 3.

**Plugin canary verification (new).** The D13 ">200 errors" tripwire cannot distinguish "plugin loaded with 200 errors" from "plugin not loaded → 0 errors counted." Without a canary, the tripwire is uninstrumented. Create a deliberate canary at `tests/unit/_pydantic_mypy_canary.py`:

```python
from pydantic import BaseModel, Field

class CanaryModel(BaseModel):
    # pydantic.mypy plugin should flag this assignment as type-incompatible
    value: int = Field(default="not_an_int")  # type-error iff plugin loaded
```

Verification command:
```bash
uv run mypy tests/unit/_pydantic_mypy_canary.py 2>&1 | grep -q "Incompatible default"
```

If mypy does NOT report the expected error, the plugin failed to load and the migration is broken — STOP. See [pydantic.mypy plugin docs](https://docs.pydantic.dev/latest/integrations/mypy/) for plugin behavior. This assertion is also added to verify-pr2.sh below.

Verification (assumes pydantic plugin error fixes in commit 3):
```bash
yq '.repos[0].hooks[] | select(.id == "mypy") | .language' .pre-commit-config.yaml | grep -qx system
yq '.repos[0].hooks[] | select(.id == "mypy") | .entry' .pre-commit-config.yaml | grep -q 'uv run mypy'
! grep -q 'mirrors-mypy' .pre-commit-config.yaml
! grep -q 'additional_dependencies:' .pre-commit-config.yaml || \
  echo "WARN: additional_dependencies still exists; commit 7 will remove it for psf/black"
```

### Commit 3 — `fix(types): address pydantic.mypy plugin errors surfaced in PR 2`

Files: variable — wherever the new errors land. Typically:
- `src/core/schemas.py`
- `src/core/schemas_*.py`
- `src/core/tools/*/`

Inline `# type: ignore[arg-type]` is acceptable for genuinely-Pydantic-internal cases (e.g., `model_dump()` return type when `exclude=True` fields are involved, per CLAUDE.md Pattern #4). Real type bugs get fixed.

Verification:
```bash
uv run mypy src/ --config-file=mypy.ini  # exit 0
uv run pre-commit run mypy --all-files   # exit 0
```

If the delta from baseline is non-zero but not regressing (i.e., we fixed all the new errors plus some pre-existing ones), document the count change in the PR description.

### Commit 4 — `chore(ci): migrate uv sync --extra dev → --group dev`

Files:
- `.github/workflows/test.yml` (modify lines ~60, 103, 171, 316, 379)
- Any `Makefile` references (search and replace)
- Any `scripts/` references
- `Dockerfile` (if it uses `--extra dev`; verify with grep)

Critical: this commit MUST land before commit 5 deletes the `[project.optional-dependencies].dev` block, otherwise CI breaks.

Verification:
```bash
[[ $(grep -c 'uv sync --extra dev' .github/workflows/test.yml) == "0" ]]
[[ $(grep -c 'uv sync --group dev' .github/workflows/test.yml) -ge "5" ]]
[[ $(grep -rE 'pip install -e \.\[dev\]|--extra dev' Makefile scripts/ docs/ 2>/dev/null | wc -l) == "0" ]]
```

### Commit 4.5 — `chore(deps): add pytest-xdist + pytest-randomly to dev group (PR 3 prereq)`

Files:
- `pyproject.toml` (modify `[dependency-groups].dev` block)

Per **D33**. PR 3 commit 4b's `integration_db` template-clone optimization requires `pytest-xdist≥3.6` (xdist is invoked from `_pytest` composite action with `-n auto`). PR 3 spec line 9 declared this as a precondition ("MUST be added... before this PR's xdist commits land. Best location: PR 2 commit 4 or 5") — this commit fulfills that contract.

`pytest-randomly` adopts the order-independence enforcement standard from Django, attrs, structlog. Combined with PR 3's `--dist=loadscope`, it surfaces hidden inter-test dependencies that the project's UUID-per-test DB pattern otherwise hides.

```toml
# pyproject.toml [dependency-groups].dev — append both entries
[dependency-groups]
dev = [
    # ... existing dev dependencies preserved ...
    "pytest-xdist>=3.6",          # PR 3 commit 4b template-clone (xdist -n auto + --dist=loadscope)
    "pytest-randomly",            # order-independence enforcement (D33)
]
```

`filelock>=3.20.3` is already a main dependency at `pyproject.toml:48` — no addition needed for PR 3 commit 4c's filelock+worker-id gate.

Why a dedicated commit (between 4 and 5):
- Commit 4 (extra → group migration) does not add NEW deps; it only changes invocation.
- Commit 5 deletes `[project.optional-dependencies].dev`. Adding new dev deps in commit 5 would make commit 5's diff harder to review.
- A standalone commit makes the "PR 3 precondition fulfillment" reviewable independently. Spec contract: PR 3 commits 4b and 4c MUST find these packages on main when they author.

Verification:
```bash
# Both packages present in dev group after this commit
uv run python -c "
import tomllib
data = tomllib.loads(open('pyproject.toml','rb').read().decode())
deps = data['dependency-groups']['dev']
assert any('pytest-xdist' in d for d in deps), 'pytest-xdist missing'
assert any('pytest-randomly' in d for d in deps), 'pytest-randomly missing'
print('OK')
"
# Filelock unchanged (still in main deps, not added to dev)
grep -qE '^\s+"filelock>=' pyproject.toml
# Lockfile updated (uv lock will re-run as part of `uv add`)
grep -q 'pytest-xdist' uv.lock
grep -q 'pytest-randomly' uv.lock
```

### Commit 4.6 — `feat(db): wire DB_POOL_SIZE + DB_MAX_OVERFLOW env-var override (D40 / R12A-01 fix)`

Files:
- `src/core/database/database_session.py` (modify lines 108-109 PgBouncer branch + lines 124-125 direct PG branch)

Per **D40**. PR 3's `_pytest/action.yml` env block sets `DB_POOL_SIZE=4` + `DB_MAX_OVERFLOW=8` to mitigate Postgres connection saturation under xdist `-n auto` (R31). Without this commit wiring the env vars in app code, the override silently no-ops because both branches of `database_session.py` hardcode the values as Python literals. This commit closes the gap BEFORE PR 3 lands.

```python
# src/core/database/database_session.py — at the top with other imports
import os

# … existing code …

# Around line 108-109 (PgBouncer branch — current literals: pool_size=2, max_overflow=5)
engine = create_engine(
    database_url,
    pool_size=int(os.getenv("DB_POOL_SIZE", "2")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
    # … other args preserved …
)

# Around line 124-125 (direct PG branch — current literals: pool_size=10, max_overflow=20)
engine = create_engine(
    database_url,
    pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "20")),
    # … other args preserved …
)
```

**Defaults preserve existing production behavior** (PgBouncer branch keeps 2/5, direct PG keeps 10/20). Only CI overrides via `_pytest/action.yml` env. No production code path changes.

Why a separate commit (between current 4.5 and 5):
- Single-purpose change reviewable independently
- Lands BEFORE PR 3 sets the env vars (otherwise PR 3's env override is inert until this lands)
- `verify-pr3.sh` greps for `os.getenv("DB_POOL_SIZE"` to enforce the wiring exists

Verification:
```bash
grep -q 'os.getenv("DB_POOL_SIZE"' src/core/database/database_session.py
grep -q 'os.getenv("DB_MAX_OVERFLOW"' src/core/database/database_session.py
# Defaults preserved: PgBouncer 2/5 + direct PG 10/20
grep -E 'os.getenv\("DB_POOL_SIZE", "(2|10)"\)' src/core/database/database_session.py | wc -l   # expect: 2
grep -E 'os.getenv\("DB_MAX_OVERFLOW", "(5|20)"\)' src/core/database/database_session.py | wc -l  # expect: 2
# Smoke: import still works
uv run python -c "from src.core.database.database_session import get_db_session; print('OK')"
```

### Commit 5 — `refactor(deps): delete [project.optional-dependencies].dev (PEP 735 dependency-groups is canonical)`

Files:
- `pyproject.toml` (delete lines ~60-78)

**Coordination per D20:** verify the v2.0 branch's `[project.optional-dependencies].dev` deletion has not already landed on main via a v2.0 phase PR. If it has, this commit is a no-op (good — evidence the rollout coordinated correctly).

**ALSO delete duplicates:** `pyproject.toml` currently has `factory-boy>=3.3.0` declared 6× across `[project.optional-dependencies]` (line ~61, ~69, ~71) AND `[dependency-groups].dev` (line ~88, ~96, ~98). Verify with `grep -c 'factory-boy>=3.3.0' pyproject.toml` returns 1 after the deletion (single canonical entry in `[dependency-groups].dev` per PEP 735).

Closes PD8.

Verification:
```bash
! grep -q '^\[project\.optional-dependencies\]' pyproject.toml || \
  ! awk '/\[project\.optional-dependencies\]/,/^\[/' pyproject.toml | grep -qE '^dev\s*='
# ui-tests block still present (D14 migrates it next, doesn't delete)
grep -qE 'ui-tests' pyproject.toml
# Duplicate factory-boy entries collapsed to 1 canonical entry
[[ "$(grep -c 'factory-boy>=3.3.0' pyproject.toml)" == "1" ]] || \
  { echo "factory-boy duplicates not collapsed"; exit 1; }
```

### Commit 6 — `refactor(deps): migrate ui-tests extras to PEP 735 dependency-group`

Files:
- `pyproject.toml` (move `[project.optional-dependencies].ui-tests` → `[dependency-groups].ui-tests`)
- `tox.ini` (line 77: `extras = ui-tests` → `dependency_groups = ui-tests`)
- `scripts/setup/setup_conductor_workspace.sh` (line 212: `--extra ui-tests` → `--group ui-tests`)

Per D14.

Verification:
```bash
! awk '/\[project\.optional-dependencies\]/,/^\[/' pyproject.toml | grep -qE '^ui-tests\s*='
awk '/\[dependency-groups\]/,/^\[/' pyproject.toml | grep -qE '^ui-tests\s*='
grep -q 'dependency_groups = ui-tests' tox.ini
grep -q 'uv sync --group ui-tests' scripts/setup/setup_conductor_workspace.sh
# Verify the env still works
uv run tox -e ui --notest   # quick sanity check; full ui run requires playwright install
```

### Commit 7 — `refactor(pre-commit): replace psf/black with local uv run black`

Files:
- `.pre-commit-config.yaml` (delete lines 275-279 psf/black block; add new local hook)

Closes PD2.

```yaml
      - id: black
        name: black (project venv)
        entry: uv run black
        language: system
        types: [python]
        require_serial: false
        exclude: '^alembic/versions/'
```

Verification:
```bash
yq '.repos[0].hooks[] | select(.id == "black") | .language' .pre-commit-config.yaml | grep -qx system
! grep -q 'psf/black' .pre-commit-config.yaml
uv run pre-commit run black --all-files
# Confirm version parity: uv run black --version matches uv.lock
[[ "$(uv run black --version | awk '{print $2}')" == "$(grep -A1 '^name = .black.$' uv.lock | grep version | head -1 | awk -F'"' '{print $2}')" ]]
```

### Commit 8 — `test: add structural guard for additional_dependencies drift`

Files:
- `tests/unit/test_architecture_pre_commit_no_additional_deps.py` (new, ~40 lines)
- `tests/unit/_architecture_helpers.py` (**new baseline** — ~30 lines: `repo_root`, `parse_module` mtime-keyed cache, `iter_function_defs`, `iter_call_expressions`, `src_python_files`)

**Coordination with PR 4 (Blocker #3):** This commit creates the baseline only. PR 4 commit 1 EXTENDS the file by appending workflow/compose/anchor helpers and assertion utilities, growing it to ~221 lines. The final reconciled module is at `.claude/notes/ci-refactor/drafts/_architecture_helpers.py`. PR 4 commit 1's verification pre-asserts that this file already exists.

The guard:

```python
"""Asserts .pre-commit-config.yaml declares no project libraries via additional_dependencies.

See ADR-001 (docs/decisions/adr-001-single-source-pre-commit-deps.md).
"""
import pathlib
import re
import tomllib

import pytest
import yaml

ALLOWLIST_PREFIXES = ("types-",)


def _package_name(spec: str) -> str:
    """Strip version/extras: 'sqlalchemy[mypy]==2.0.36' -> 'sqlalchemy'."""
    return re.split(r"[\[=<>!~;]", spec, 1)[0].strip().lower()


@pytest.mark.arch_guard
def test_no_additional_deps_for_project_libs():
    repo = pathlib.Path(__file__).resolve().parents[2]
    cfg = yaml.safe_load((repo / ".pre-commit-config.yaml").read_text())
    toml = tomllib.loads((repo / "pyproject.toml").read_text())

    project_deps: set[str] = set()
    for dep in toml["project"].get("dependencies", []):
        project_deps.add(_package_name(dep))
    for grp in toml.get("dependency-groups", {}).values():
        for dep in grp:
            project_deps.add(_package_name(dep))

    violations = []
    for repo_entry in cfg["repos"]:
        for hook in repo_entry.get("hooks", []):
            for add_dep in hook.get("additional_dependencies", []):
                name = _package_name(add_dep)
                if name.startswith(ALLOWLIST_PREFIXES):
                    continue
                if name in project_deps:
                    violations.append(
                        f"{hook['id']} -> {add_dep} (also in pyproject.toml; see ADR-001)"
                    )

    assert not violations, "\n".join(violations)
```

Add `@pytest.mark.arch_guard` marker registration to `pytest.ini` `[pytest]` section under the existing `markers =` continuation lines (NOT `pyproject.toml [tool.pytest.ini_options]` — the project uses `pytest.ini` with `--strict-markers` as its pytest config source). The marker name is `arch_guard` to avoid collision with the entity-marker `architecture` that PR 4 addresses:

```ini
[pytest]
markers =
    arch_guard: structural guards (run with -m arch_guard)
    # ... existing markers
```

**Ownership note:** This commit OWNS the `arch_guard` marker registration in `pytest.ini`. PR 4 commit 1 VERIFIES the registration via `grep`, does NOT re-write. Earlier spec revisions had both PRs registering the marker; the verify-only stance in PR 4 prevents duplicate writes.

Verification:
```bash
test -f tests/unit/test_architecture_pre_commit_no_additional_deps.py
test -f tests/unit/_architecture_helpers.py
uv run pytest tests/unit/test_architecture_pre_commit_no_additional_deps.py -v -x
# Red-team:
git stash   # save current state
sed -i.bak 's/      - id: black/      - id: black\n        additional_dependencies:\n          - factory-boy>=3.3.0/' .pre-commit-config.yaml
uv run pytest tests/unit/test_architecture_pre_commit_no_additional_deps.py -v 2>&1 | grep -q "factory-boy"
git stash pop
rm .pre-commit-config.yaml.bak
# Confirms the guard catches a violation
```

### Commit 9 — `docs: update CLAUDE.md guards table to include pre-commit drift guard`

Files:
- `CLAUDE.md` (modify the structural guards table — add new row, fix the 8 existing inaccuracies discovered in pre-flight per D18)

This commit also corrects:
- Add 5 missing rows for guards on disk (`test_architecture_no_silent_except.py`, `test_architecture_bdd_no_direct_call_impl.py`, `test_architecture_bdd_obligation_sync.py`, `test_architecture_production_session_add.py`, `test_architecture_test_marker_coverage.py`)
- Remove 3 phantom rows (guards listed but not on disk)
- Audit table column count (per D18, target post-PR-2: 28; PR 4 adds 4 more; v2.0 contributes 27 architecture tests + 9 baseline JSONs; PR 1/3/6 governance adds 8 — final ~81 post-v2.0-rebase per D18 Round 8 revision (was ~73; corrected after v2.0 architecture/ count was re-verified at 27, not 31))

Verification:
```bash
# Every row's test file exists
awk '/^\| .* \| .* \| `(test_.*\.py)` \|$/ { print $NF }' CLAUDE.md | tr -d '|`' | while read f; do
  test -f "tests/unit/$f" || echo "MISSING: $f"
done | head
# Every architecture test on disk has a row in the table
ls tests/unit/test_architecture_*.py | xargs -n1 basename | while read f; do
  grep -q "\`$f\`" CLAUDE.md || echo "NOT IN TABLE: $f"
done | head
```

## Acceptance criteria

From issue #1234 §Acceptance criteria, scoped to PR 2:

- [ ] `grep -c 'additional_dependencies:' .pre-commit-config.yaml` returns 0
- [ ] `mirrors-mypy` and `psf/black` repo hooks removed; local `uv run` variants in place
- [ ] `[project.optional-dependencies].dev` removed from `pyproject.toml`
- [ ] `tests/unit/test_architecture_pre_commit_no_additional_deps.py` exists and passes
- [ ] Fake violation in scratch branch correctly fails the guard (red-team test executed)
- [ ] ADR `docs/decisions/adr-001-single-source-pre-commit-deps.md` exists

Plus agent-derived:

- [ ] `@pytest.mark.arch_guard` marker registered in `pytest.ini` `[pytest].markers`
- [ ] `tests/unit/_architecture_helpers.py` shared helper module exists
- [ ] `[project.optional-dependencies].ui-tests` migrated to `[dependency-groups].ui-tests` (D14)
- [ ] All 5 `uv sync --extra dev` callsites in `test.yml` migrated to `--group dev`
- [ ] `make quality` passes (including pydantic.mypy plugin re-enabled)
- [ ] `mypy.ini:3` plugins line still references `pydantic.mypy` (not commented out)
- [ ] `tox -e ui --notest` succeeds (verifies ui-tests group migration)
- [ ] CLAUDE.md guards table accurate against disk
- [ ] adcp library version ≥3.10 (per D16; verified by verify-pr2.sh line 466-468)
- [ ] Commit 4.6: DB_POOL_SIZE / DB_MAX_OVERFLOW env vars wired in `src/core/database/database_session.py` (per D40 / R12A-01; replaces hardcoded pool sizes with `os.getenv`). Brief code expectation — **per-branch defaults preserved**: PgBouncer branch (`is_pgbouncer == True`, lines 108-109) keeps default `(2, 5)`; direct PG branch (lines 124-125) keeps default `(10, 20)`. Wrong-default landing causes silent PgBouncer prod regression — see spec body §Commit 4.6 + Round 14 B1.
- [ ] No duplicate factory-boy entries in pyproject.toml (`grep -c 'factory-boy>=3.3.0' pyproject.toml` returns 1)

## Verification (full PR-level)

```bash
bash .claude/notes/ci-refactor/scripts/verify-pr2.sh
```

Inline:

```bash
#!/usr/bin/env bash
set -euo pipefail

fail() { echo "FAIL: $*" >&2; exit 1; }

echo "[1/12] No additional_dependencies for project libs..."
[[ $(grep -c 'additional_dependencies:' .pre-commit-config.yaml) == "0" ]]

echo "[2/12] mirrors-mypy and psf/black repo blocks gone..."
! grep -q 'mirrors-mypy' .pre-commit-config.yaml
! grep -q 'psf/black' .pre-commit-config.yaml

echo "[3/12] Local hooks present..."
yq '.repos[0].hooks[] | select(.id == "mypy") | .language' .pre-commit-config.yaml | grep -qx system
yq '.repos[0].hooks[] | select(.id == "black") | .language' .pre-commit-config.yaml | grep -qx system

echo "[4/12] [project.optional-dependencies].dev deleted..."
! awk '/\[project\.optional-dependencies\]/,/^\[/' pyproject.toml | grep -qE '^dev\s*='

echo "[5/12] ui-tests migrated to dependency-groups..."
awk '/\[dependency-groups\]/,/^\[/' pyproject.toml | grep -qE '^ui-tests\s*='

echo "[6/12] CI uses --group dev..."
[[ $(grep -c 'uv sync --extra dev' .github/workflows/test.yml) == "0" ]]
[[ $(grep -c 'uv sync --group dev' .github/workflows/test.yml) -ge "5" ]]

echo "[7/12] Structural guard..."
test -f tests/unit/test_architecture_pre_commit_no_additional_deps.py
uv run pytest tests/unit/test_architecture_pre_commit_no_additional_deps.py -v -x

echo "[8/12] mypy passes..."
uv run mypy src/ --config-file=mypy.ini

echo "[9/12] pydantic.mypy plugin canary loads (proves plugin active, not silently dropped)..."
test -f tests/unit/_pydantic_mypy_canary.py || fail "canary fixture missing"
uv run mypy tests/unit/_pydantic_mypy_canary.py 2>&1 | grep -q "Incompatible default" \
  || fail "pydantic.mypy plugin not loaded — D13 tripwire is uninstrumented"

echo "[10/12] arch_guard marker registered in pytest.ini..."
grep -q '^[[:space:]]*arch_guard:' pytest.ini || fail "arch_guard marker not registered in pytest.ini"

echo "[11/12] adcp version current (was stale at 3.2.0 pre-migration)..."
# Confirm post-migration adcp version is current (was stale at 3.2.0 pre-migration)
uv run python -c "import adcp; assert adcp.__version__ >= '3.10', f'expected adcp>=3.10, got {adcp.__version__}'"

echo "[12/12] make quality passes..."
make quality

echo "PR 2 verification PASSED"
```

## Risks (scoped to PR 2)

- **R2 — Pydantic.mypy errors > 200**: mitigation — pre-flight P2 captures count; D13 tripwire defers plugin re-enablement
- **R5 — PR #1217 merges mid-review**: mitigation — `uv run` reads `uv.lock` at invocation time, no semantic change
- **R6 — v2.0 phase PR lands on `pyproject.toml` mid-review**: mitigation — `[project.optional-dependencies].dev` already deleted on v2.0; rebase carefully so this PR doesn't re-introduce it

## Rollback plan

PR 2 has more conditional rollback than PR 1 because of the `--extra dev` → `--group dev` ordering.

**Option A — full revert (preferred):**
```bash
git revert -m 1 <PR2-merge-sha>
# admin: pushes via UI; agent does NOT run this command
```

If revert breaks CI (because `[project.optional-dependencies].dev` is referenced by callsite that was deleted in PR 2 commit 4), the revert is incomplete; need to reapply commit 4's `--extra dev` references.

**Option B — graceful degradation:**
Keep the `local` mypy and black hooks (they work fine) but add back `additional_dependencies` for safety:

```bash
git checkout -b fix/pr2-graceful-rollback
# Edit .pre-commit-config.yaml: keep new local mypy/black hooks
# Re-add the original mirrors-mypy block alongside (drift returns but CI works)
# Or restore [project.optional-dependencies].dev block as a duplicate
git add -p && git commit -m "fix: restore deleted optional-dependencies dev block"
```

Recovery: < 30 minutes.

## Merge tolerance

- **PR #1217 (adcp 3.12)**: tolerated. The local mypy hook validates against whatever `uv.lock` resolves at the time the hook runs.
- **v2.0 phase PR landing on `pyproject.toml`**: blocking if it touches `[project.optional-dependencies].dev` or `[dependency-groups]`. Since v2.0 has ALREADY deleted the optional-deps block, the conflict surface is `[dependency-groups].dev` line ordering — mechanical rebase. **Verify the v2.0 branch did not remove `pytest-asyncio` from `[dependency-groups].dev`** (it's load-bearing for `tests/ui/`).
- **v2.0 phase PR landing on `.pre-commit-config.yaml`**: high conflict surface. Coordinate before opening PR 2.

## Coordination notes for the maintainer

1. **Before authoring**: capture `.mypy-baseline.txt` per pre-flight P2.
2. **Empirical pre-check (new)**: before authoring commit 8, verify `pytest.ini` exists at repo root and contains `[pytest]` section with a `markers =` block. If `pyproject.toml [tool.pytest.ini_options]` is being used instead, escalate — pytest.ini takes precedence when both are present and that's the project's config source.
   ```bash
   test -f pytest.ini || { echo "pytest.ini missing — escalate"; exit 1; }
   grep -q '^\[pytest\]' pytest.ini || { echo "pytest.ini lacks [pytest] section — escalate"; exit 1; }
   awk '/^\[pytest\]/,/^\[/' pytest.ini | grep -qE '^markers\s*=' || { echo "pytest.ini lacks markers block — escalate"; exit 1; }
   ! grep -qE '^\[tool\.pytest\.ini_options\]' pyproject.toml || { echo "pyproject.toml has competing [tool.pytest.ini_options] — escalate"; exit 1; }
   ```
3. **Between commits 2 and 3**: review the pydantic.mypy error delta. If > 200, escalate per D13. Verify the canary fixture (`tests/unit/_pydantic_mypy_canary.py`) flags as expected — proves the plugin is actually loaded and the D13 tripwire is instrumented.
4. **After commit 4**: verify CI is green BEFORE proceeding to commit 5 (the optional-deps deletion).
5. **After commit 7**: run `uv run black --version` and verify the version matches `uv.lock`'s resolved black version. PD2's drift is gone if these match.
6. **After merge**: open a follow-up issue to remove the temporary `# type: ignore[...]` comments added in commit 3 (track them with grep `git grep '# type: ignore' src/`).
