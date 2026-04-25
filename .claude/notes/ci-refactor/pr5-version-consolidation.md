# PR 5 — Cross-surface version consolidation

**Drift items closed:** PD9, PD10, PD11, PD12
**Estimated effort:** 2 days
**Depends on:** PR 3 Phase C merged (uses the `_pytest.yml` reusable workflow's Postgres anchor); independent of PR 4
**Blocks:** none (final PR of rollout)
**Decisions referenced:** D-pending-3 (UV_VERSION resolution)

## Scope

Consolidate Python, Postgres, and uv version anchors across the repo. Single source of truth per dimension:
- **Python**: `.python-version` (canonical), referenced via `python-version-file:` in workflows and `ARG PYTHON_VERSION` in Dockerfile
- **Postgres**: single image tag (`postgres:17-alpine`) referenced everywhere
- **uv**: SHA-pinned `COPY --from=ghcr.io/astral-sh/uv:<version>` in Dockerfile; `version: <pin>` in setup-uv action

Bumps `[tool.black].target-version` and `[tool.ruff].target-version` from `py311` → `py312` (matches `requires-python>=3.12`).

## Out of scope

- Python version bump (3.12 → 3.13) — this PR only aligns existing anchors, doesn't change the version
- Postgres version bump beyond unifying — PG17 chosen because dev compose already uses it
- uv version bump — pinning current 0.11.6, not upgrading
- New Fortune-50 patterns (harden-runner, SBOM, etc.) — defer to PR 6 follow-up

## Internal commit sequence

Order doesn't matter much; group by surface to keep commits reviewable.

### Commit 1 — `chore(python): consolidate Python 3.12 anchors via .python-version`

Files:
- `Dockerfile` (lines 4, 43)
- `.github/workflows/test.yml` (line 11 `PYTHON_VERSION:` env var)
- Any `setup-python@` references in workflows

`.python-version` is already canonical (5 bytes, contains `3.12`). PR 5 makes everything else read FROM it.

`Dockerfile` change:

```dockerfile
# Before:
FROM python:3.12-slim AS builder
# ... stages
FROM python:3.12-slim

# After:
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim AS builder
# ... stages
FROM python:${PYTHON_VERSION}-slim
```

Caller (CI workflow or local build):
```bash
docker build --build-arg PYTHON_VERSION=$(cat .python-version) .
```

For local `docker compose`, the `compose.yaml` files reference the Dockerfile and inherit the ARG default. No compose change needed unless Python version differs from the ARG default.

`.github/workflows/test.yml` change (and any other workflow):

```yaml
# Before:
env:
  PYTHON_VERSION: '3.12'
# ...
      - uses: actions/setup-python@<SHA>
        with:
          python-version: ${{ env.PYTHON_VERSION }}

# After:
# (PYTHON_VERSION env var deleted)
# ...
      - uses: actions/setup-python@<SHA>
        with:
          python-version-file: .python-version
```

Same for `setup-uv` action; it auto-detects `.python-version`:
```yaml
- uses: astral-sh/setup-uv@<SHA>
  with:
    version: '0.11.6'
    python-version-file: .python-version
```

Verification:
```bash
PY=$(cat .python-version | tr -d 'v\n')
PY_SHORT=$(echo "$PY" | cut -d. -f1-2)            # 3.12
PY_NODOT=$(echo "$PY_SHORT" | tr -d .)            # 312

grep -qE "^python_version\s*=\s*${PY_SHORT}" mypy.ini
grep -qE "^ARG PYTHON_VERSION=${PY_SHORT}" Dockerfile
[[ $(grep -RE 'python-version:\s' .github/workflows/ | grep -v 'python-version-file' | wc -l) == "0" ]]
[[ $(grep -RE "python:${PY_SHORT}-slim" Dockerfile docker-compose*.yml | grep -v 'PYTHON_VERSION' | wc -l) == "0" ]] || \
  echo "Dockerfile/compose still has hardcoded python:3.12-slim"
```

### Commit 2 — `chore(postgres): unify CI Postgres images to 17-alpine`

Files:
- `.github/workflows/test.yml:135` (`postgres:15` → `postgres:17-alpine`) — but this file is being deleted in PR 3 Phase C; if PR 5 lands AFTER Phase C, only need to update `_pytest.yml`
- `.github/workflows/_pytest.yml` (already `postgres:17-alpine` per PR 3)
- `.github/workflows/test.yml:196` (`postgres:16` creative agent) — same caveat; if test.yml is gone, this commit is empty for that line

Closes PD10.

If PR 5 lands AFTER PR 3 Phase C, this commit only verifies `_pytest.yml` already uses 17-alpine and that no other reference exists.

Verification:
```bash
PG_VALUES=$(grep -hE 'postgres:[0-9][^"]*' .github/workflows/*.yml docker-compose*.yml 2>/dev/null | \
            sort -u | grep -oE 'postgres:[0-9a-z.-]+')
[[ $(echo "$PG_VALUES" | wc -l) == "1" ]]
echo "$PG_VALUES" | grep -qx 'postgres:17-alpine'
```

### Commit 3 — `chore(uv): pin uv via COPY --from in Dockerfile`

Files:
- `Dockerfile:24` (replace `pip install uv` with `COPY --from=ghcr.io/astral-sh/uv:<version>`)

Closes PD12.

```dockerfile
# Before:
RUN pip install --no-cache-dir uv

# After:
ARG UV_VERSION=0.11.6
COPY --from=ghcr.io/astral-sh/uv:${UV_VERSION} /uv /uvx /usr/local/bin/
```

This is uv's official 2026 Docker recommendation: faster (no pip layer), cache-friendly, SHA-pinnable via `uv:0.11.6@sha256:<digest>`.

For maximum supply-chain rigor, also pin by digest:
```dockerfile
ARG UV_VERSION=0.11.6
ARG UV_DIGEST=sha256:abc123...   # capture from `docker pull ghcr.io/astral-sh/uv:0.11.6 && docker inspect`
COPY --from=ghcr.io/astral-sh/uv:${UV_VERSION}@${UV_DIGEST} /uv /uvx /usr/local/bin/
```

Verification:
```bash
grep -qE 'COPY --from=ghcr\.io/astral-sh/uv:[0-9.]+' Dockerfile
! grep -qE '^RUN pip install.*uv' Dockerfile
UV_VER=$(grep -E 'COPY --from=ghcr.io/astral-sh/uv:' Dockerfile | sed -E 's|.*uv:([^ @]+).*|\1|')
[[ -n "$UV_VER" ]]
```

### Commit 4 — `chore(uv): align UV_VERSION across Dockerfile and workflows`

Files:
- `.github/workflows/_pytest.yml` (verify `version: '0.11.6'` matches `Dockerfile` ARG default)
- `.github/workflows/ci.yml` (any direct uv references)
- `.github/actions/setup-env/action.yml` (verify default uv-version matches)

Closes PD11. Per D-pending-3 default, anchor in `_setup-env` action:

```yaml
inputs:
  uv-version:
    description: 'uv version to install'
    required: false
    default: '0.11.6'   # MUST match Dockerfile ARG UV_VERSION
```

Add a structural guard test:

```python
# tests/unit/test_architecture_uv_version_anchor.py
"""Asserts uv version is consistent across Dockerfile and workflow files."""
import re
from pathlib import Path

import pytest


@pytest.mark.architecture
def test_uv_version_consistent():
    repo = Path(__file__).resolve().parents[2]
    dockerfile = (repo / "Dockerfile").read_text()
    setup_env = (repo / ".github" / "actions" / "setup-env" / "action.yml").read_text()

    docker_match = re.search(r'ARG UV_VERSION=([\d.]+)', dockerfile)
    assert docker_match, "Dockerfile missing ARG UV_VERSION"
    docker_ver = docker_match.group(1)

    setup_match = re.search(r"default:\s*['\"]([\d.]+)['\"]", setup_env)
    assert setup_match, "setup-env action missing uv-version default"
    setup_ver = setup_match.group(1)

    assert docker_ver == setup_ver, \
        f"uv version drift: Dockerfile={docker_ver}, setup-env={setup_ver}"
```

Verification:
```bash
test -f tests/unit/test_architecture_uv_version_anchor.py
uv run pytest tests/unit/test_architecture_uv_version_anchor.py -v
```

### Commit 5 — `refactor(format): bump black target-version to py312`

Files:
- `pyproject.toml:117` (`target-version = ['py311']` → `target-version = ['py312']`)

Closes PD9 (partial).

Pre-flight measurement (mandatory):
```bash
uvx black==26.3.1 --check --diff --target-version py312 src/ 2>&1 | tee /tmp/black-py312.txt
grep -cE '^[+-]' /tmp/black-py312.txt
```

If diff is large (> 100 files reformatted), commit the reformat as a separate commit immediately after this one. Use `git commit --no-verify` only if you understand the reformat is mechanical (NOT a feature flag bypass).

Verification:
```bash
uv run python -c "
import tomllib
d = tomllib.load(open('pyproject.toml','rb'))
assert 'py312' in d['tool']['black']['target-version']
"
uv run black --check src/   # passes after the reformat commit lands
```

### Commit 6 — `refactor(format): bump ruff target-version to py312`

Files:
- `pyproject.toml:138` (`target-version = "py311"` → `target-version = "py312"`)

Closes PD9 (full).

Pre-flight measurement:
```bash
uv run ruff check src/ tests/ --target-version py312 --statistics
uv run ruff check src/ tests/ --target-version py312 --select UP --statistics
```

If new violations appear (typically UP040 TypeAlias, UP032 f-string, UP041 except), apply the autofix:

```bash
uv run ruff check src/ tests/ --target-version py312 --fix --select UP
```

Verification:
```bash
uv run python -c "
import tomllib
d = tomllib.load(open('pyproject.toml','rb'))
assert d['tool']['ruff']['target-version'] == 'py312'
"
uv run ruff check src/   # passes
uv run ruff format --check src/   # passes
```

### Commit 7 — `chore: black reformat + ruff fix (target-version py312)`

If commits 5 and 6 produced diff, commit it here.

Files: variable (whatever was reformatted).

Verification:
```bash
make quality   # green
```

### Commit 8 — `chore: regression checks against PG17`

Files: none new; verifies the migration.

Run integration tests against PG17 to verify no regression:

```bash
docker run --rm -d --name pg17-test \
  -e POSTGRES_USER=adcp_user -e POSTGRES_PASSWORD=test_password -e POSTGRES_DB=adcp_test \
  -p 5432:5432 postgres:17-alpine
sleep 5
DATABASE_URL=postgresql://adcp_user:test_password@localhost:5432/adcp_test \
  uv run tox -e integration
docker stop pg17-test
```

If any test fails specifically against PG17 but passes on PG15/16, file a follow-up issue. Don't block PR 5 merge unless the failure is a tenant-isolation or schema correctness issue.

Verification:
```bash
make quality
tox -e integration   # at least once locally before opening PR
docker compose up -d --wait
docker compose down
```

## Acceptance criteria

From issue #1234 §Acceptance criteria, scoped to PR 5:

- [ ] Single Python version string in canonical source (`.python-version`)
- [ ] Single Postgres image tag used across CI + compose (`postgres:17-alpine`)
- [ ] `grep 'target-version' pyproject.toml` shows py312 in all tool configs

Plus agent-derived:

- [ ] `Dockerfile` uses `ARG PYTHON_VERSION` and `COPY --from=ghcr.io/astral-sh/uv:<pin>`
- [ ] All workflow `setup-python` references use `python-version-file: .python-version` (not hardcoded `3.12`)
- [ ] `setup-uv` references use `python-version-file: .python-version` and explicit `version:` pin
- [ ] `tests/unit/test_architecture_uv_version_anchor.py` exists and passes (per D-pending-3 default)
- [ ] `make quality` passes
- [ ] `tox -e integration` passes against PG17

## Verification (full PR-level)

```bash
bash .claude/notes/ci-refactor/scripts/verify-pr5.sh
```

Inline:

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "[1/6] Single Python anchor..."
PY=$(cat .python-version | tr -d 'v\n')
PY_SHORT=$(echo "$PY" | cut -d. -f1-2)
grep -qE "^python_version\s*=\s*${PY_SHORT}" mypy.ini
grep -qE "^ARG PYTHON_VERSION=${PY_SHORT}" Dockerfile
[[ $(grep -RE 'python-version:\s' .github/workflows/ | grep -v 'python-version-file' | wc -l) == "0" ]]

echo "[2/6] Single Postgres anchor..."
PG_VALUES=$(grep -hE 'postgres:[0-9][^"]*' .github/workflows/*.yml docker-compose*.yml 2>/dev/null | sort -u)
[[ $(echo "$PG_VALUES" | wc -l) == "1" ]]
echo "$PG_VALUES" | grep -qx 'postgres:17-alpine' || \
  echo "$PG_VALUES" | grep -q 'postgres:17-alpine'

echo "[3/6] uv pin in Dockerfile..."
grep -qE 'COPY --from=ghcr\.io/astral-sh/uv:[0-9.]+' Dockerfile
! grep -qE '^RUN pip install.*uv' Dockerfile

echo "[4/6] uv version anchor consistent..."
uv run pytest tests/unit/test_architecture_uv_version_anchor.py -v

echo "[5/6] Black + ruff target-version py312..."
uv run python -c "
import tomllib
d = tomllib.load(open('pyproject.toml','rb'))
assert 'py312' in d['tool']['black']['target-version'], 'black not py312'
assert d['tool']['ruff']['target-version'] == 'py312', 'ruff not py312'
"

echo "[6/6] make quality + integration regression..."
make quality
tox -e integration

echo "PR 5 verification PASSED"
```

## Risks (scoped to PR 5)

- Risk: PG15→PG17 migration breaks an integration test that assumes specific Postgres-major behavior.
  - Mitigation: pre-merge `tox -e integration` against PG17 catches it; PR 3's `_pytest.yml` already uses PG17 so any breakage will surface during PR 3 phase A overlap.
- Risk: `target-version py312` ruff bump produces a large diff in the same PR.
  - Mitigation: separate the reformat into its own commit (commit 7) for review clarity.

## Rollback plan

```bash
git revert -m 1 <PR5-merge-sha>
git push origin main   # USER ACTION
docker compose build --no-cache && docker compose up -d --wait
```

Recovery: < 15 minutes (Docker rebuild dominates).

If only one piece is wrong (e.g., black reformat broke something), revert just that commit:

```bash
git revert <commit-7-sha>   # the reformat commit
```

## Merge tolerance

- **PR #1217 (adcp 3.12)**: tolerated. PR 5 doesn't reference adcp.
- **v2.0 phase PR landing on `pyproject.toml`**: high conflict on lines 117 (black), 138 (ruff). Coordinate before opening.
- **v2.0 phase PR landing on `Dockerfile`**: medium conflict on the FROM lines. Mechanical rebase.
- **v2.0 phase PR landing on `docker-compose*.yml`**: low conflict; both modify but on different lines.

## Coordination notes for the maintainer

1. **Before authoring**: PR 3 Phase C must be merged (so the `_pytest.yml` reusable workflow exists and is the Postgres anchor).
2. **Pre-flight ruff/black measurement**: run `uvx black --check --diff --target-version py312 src/` and `uv run ruff check --target-version py312 src/ --statistics` to size commits 5-6.
3. **PG17 regression**: run `tox -e integration` against a local PG17 instance BEFORE opening the PR. Document the run in the PR description.
4. **After merge**: close issue #1234 with a comment listing all closed PD items + final OpenSSF Scorecard score.
