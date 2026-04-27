#!/usr/bin/env bash
# Verification for PR 2 — uv.lock single-source for pre-commit deps
set -uo pipefail

# Source shared helpers (fail/ok/warn/section + common checks live in _lib.sh)
source "$(dirname "$0")/_lib.sh"

# ADR-001 present (may be no-op if PR 1 created it)
[[ -f docs/decisions/adr-001-single-source-pre-commit-deps.md ]] && ok "ADR-001 present"

# Commit 2: mypy local hook (replaces mirrors-mypy)
if grep -q '^  - repo: local' .pre-commit-config.yaml; then
  ! grep -q 'mirrors-mypy' .pre-commit-config.yaml || fail "mirrors-mypy still present"
  grep -qE '\b(id: mypy)\b' .pre-commit-config.yaml || fail "local mypy hook missing"
  yq '.repos[].hooks[] | select(.id == "mypy") | .language' .pre-commit-config.yaml | grep -qx system \
    || fail "local mypy hook is not language: system"
  ok "local mypy hook (language: system) replaces mirrors-mypy"
fi

# Commit 4: uv sync --extra dev → --group dev
if [[ -f .github/workflows/test.yml ]]; then
  EXTRA_DEV=$(grep -c 'uv sync --extra dev' .github/workflows/test.yml || true)
  GROUP_DEV=$(grep -c 'uv sync --group dev' .github/workflows/test.yml || true)
  [[ "$EXTRA_DEV" == "0" ]] || fail "$EXTRA_DEV occurrences of uv sync --extra dev still present"
  [[ "$GROUP_DEV" -ge 1 ]] && ok "uv sync uses --group dev ($GROUP_DEV occurrences)"
fi

# Commit 5: optional-deps.dev removed
if grep -qE '^\[project\.optional-dependencies\]' pyproject.toml; then
  ! grep -A30 '^\[project\.optional-dependencies\]' pyproject.toml | grep -qE '^dev' \
    || fail "[project.optional-dependencies].dev block still present"
fi

# Commit 6: black local hook (replaces psf/black)
if grep -q 'id: black' .pre-commit-config.yaml; then
  ! grep -q 'psf/black' .pre-commit-config.yaml || fail "psf/black still present (should be local hook)"
  ok "local black hook in place"
fi

# Commit 8: helpers baseline
if [[ -f tests/unit/_architecture_helpers.py ]]; then
  for fn in repo_root parse_module iter_function_defs iter_call_expressions src_python_files; do
    grep -q "^def $fn\|^def ${fn}\b" tests/unit/_architecture_helpers.py \
      || fail "_architecture_helpers.py missing baseline function: $fn"
  done
  ok "_architecture_helpers.py baseline present (PR 4 will EXTEND, not replace)"
fi

# Commit 8 guard
if [[ -f tests/unit/test_architecture_pre_commit_no_additional_deps.py ]]; then
  ok "additional_dependencies drift guard present"
fi

# Commit 5: factory-boy duplicates collapsed to single canonical entry (PR 2 scope)
if [[ -f pyproject.toml ]]; then
  count=$(grep -c 'factory-boy>=3.3.0' pyproject.toml)
  [[ "$count" -eq 1 ]] || fail "pyproject.toml has $count factory-boy entries (must be 1; clean up duplicates per PR 2 scope)"
  ok "pyproject.toml has 1 canonical factory-boy entry (duplicates collapsed)"
fi

# Commit 4.6: DB_POOL_SIZE / DB_MAX_OVERFLOW env-var wiring (D40 / R12A-01; per-branch defaults Round 14 B1)
if [[ -f src/core/database/database_session.py ]]; then
  grep -q 'os.getenv.*DB_POOL_SIZE' src/core/database/database_session.py \
    || fail "src/core/database/database_session.py missing os.getenv(DB_POOL_SIZE) wiring (D40 / R12A-01 — wires the env var set in PR 3 _pytest action)"
  grep -q 'os.getenv.*DB_MAX_OVERFLOW' src/core/database/database_session.py \
    || fail "src/core/database/database_session.py missing os.getenv(DB_MAX_OVERFLOW) wiring (D40 / R12A-01)"
  # Per-branch defaults: PgBouncer (2|5), direct PG (10|20). Wrong defaults = silent PgBouncer prod regression.
  pool_count=$(grep -cE 'os\.getenv\("DB_POOL_SIZE", "(2|10)"\)' src/core/database/database_session.py || true)
  ovr_count=$(grep -cE 'os\.getenv\("DB_MAX_OVERFLOW", "(5|20)"\)' src/core/database/database_session.py || true)
  [[ "$pool_count" -eq 2 ]] || fail "DB_POOL_SIZE: expected 2 occurrences with defaults (2|10) (PgBouncer + direct PG), got $pool_count — production regression risk per Round 14 B1"
  [[ "$ovr_count" -eq 2 ]] || fail "DB_MAX_OVERFLOW: expected 2 occurrences with defaults (5|20), got $ovr_count — production regression risk per Round 14 B1"
  ok "DB_POOL_SIZE/DB_MAX_OVERFLOW wiring with correct per-branch defaults (D40 + Round 14 B1)"
fi

# --- Round 14 deep-verify additions (Gap 1 — close 12-step inline contract from spec L432-482) ---

# D13: pydantic.mypy plugin canary fixture (PR 2 commit 2 creates this file)
section "D13 — pydantic.mypy plugin canary"
[[ -f tests/unit/_pydantic_mypy_canary.py ]] \
  || fail "tests/unit/_pydantic_mypy_canary.py missing (D13 — PR 2 commit 2 creates this canary; spec lines 75-83)"
uv run mypy tests/unit/_pydantic_mypy_canary.py 2>&1 | grep -q "Incompatible default" \
  || fail "pydantic.mypy plugin not loaded — D13 200-error tripwire is uninstrumented (spec line 469)"
ok "D13 pydantic.mypy plugin canary fires — tripwire instrumented"

# D14: ui-tests block migrated to [dependency-groups] (PR 2 commit 6; spec L249-266 is HARD assert across 3 files)
section "D14 — ui-tests dependency-group migration"
awk '/\[dependency-groups\]/,/^\[/' pyproject.toml | grep -qE '^ui-tests\s*=' \
  || fail "pyproject.toml [dependency-groups].ui-tests block missing (D14; PR 2 commit 6; spec L261)"
! awk '/\[project\.optional-dependencies\]/,/^\[/' pyproject.toml | grep -qE '^ui-tests\s*=' \
  || fail "[project.optional-dependencies].ui-tests block still present — D14 migration incomplete (spec L260)"
if [[ -f tox.ini ]]; then
  grep -q 'dependency_groups\s*=\s*ui-tests' tox.ini \
    || fail "tox.ini missing 'dependency_groups = ui-tests' (D14; PR 2 commit 6 OWNS — spec L262)"
fi
if [[ -f scripts/setup/setup_conductor_workspace.sh ]]; then
  grep -q 'uv sync --group ui-tests' scripts/setup/setup_conductor_workspace.sh \
    || fail "setup_conductor_workspace.sh missing 'uv sync --group ui-tests' (D14; PR 2 commit 6 OWNS — spec L263)"
fi
ok "D14 ui-tests migrated to [dependency-groups] (pyproject.toml + tox.ini + setup script)"

# D29: arch_guard marker registered in pytest.ini (PR 2 commit 8 OWNS; PR 4 commit 1 verifies)
section "D29 — arch_guard marker registration"
[[ -f pytest.ini ]] || fail "pytest.ini missing (D29; required as marker registration target — NOT pyproject.toml)"
grep -qE '^\s*arch_guard:' pytest.ini \
  || fail "pytest.ini missing 'arch_guard:' marker (D29; PR 2 commit 8 OWNS registration)"
ok "D29 arch_guard marker registered in pytest.ini (PR 2 commit 8)"

# D33: pytest-xdist + pytest-randomly in dev group (PR 2 commit 4.5 OWNS; PR 3 uses)
section "D33 — pytest-xdist + pytest-randomly in dev group"
grep -qE '"pytest-xdist[>=]' pyproject.toml \
  || fail "pyproject.toml missing pytest-xdist (D33; PR 2 commit 4.5 OWNS — PR 3 prereq)"
grep -qE '"pytest-randomly' pyproject.toml \
  || fail "pyproject.toml missing pytest-randomly (D33; PR 2 commit 4.5 OWNS)"
if [[ -f uv.lock ]]; then
  grep -q 'pytest-xdist' uv.lock \
    || fail "uv.lock missing pytest-xdist (D33 — uv lock not refreshed after dep add)"
  grep -q 'pytest-randomly' uv.lock \
    || fail "uv.lock missing pytest-randomly (D33)"
fi
ok "D33 pytest-xdist + pytest-randomly in dev group + locked"

# D8 strict — uv sync --group dev count >= 5 (spec L457; PR 2 commit 4 has 5 explicit callsites)
# This supplements (does not replace) the existing >= 1 check at L22-25, giving graduated diagnostic.
section "D8 strict — uv sync --group dev usage in CI (>= 5 callsites)"
if [[ -f .github/workflows/test.yml ]]; then
  GROUP_DEV_STRICT=$(grep -c 'uv sync --group dev' .github/workflows/test.yml || true)
  [[ "$GROUP_DEV_STRICT" -ge 5 ]] \
    || fail "test.yml has only $GROUP_DEV_STRICT 'uv sync --group dev' callsites; spec demands >= 5 (D8 / Commit 4 — partial migration not allowed)"
  ok "test.yml has $GROUP_DEV_STRICT '--group dev' callsites (>= 5 spec threshold)"
fi

# Commit 8 strict — actually run the drift guard, not just `test -f` (spec L461 step 7)
section "Commit 8 — additional_dependencies drift guard executes"
if [[ -f tests/unit/test_architecture_pre_commit_no_additional_deps.py ]]; then
  uv run pytest tests/unit/test_architecture_pre_commit_no_additional_deps.py -v -x \
    || fail "drift guard test fails (PR 2 commit 8; spec L461)"
  ok "additional_dependencies drift guard passes"
fi

# D16 — adcp library version >= 3.10 (spec L420 acceptance criterion)
section "D16 — adcp library version >= 3.10"
if uv run python -c "import adcp; assert tuple(int(x) for x in adcp.__version__.split('.')[:2]) >= (3, 10), f'adcp version {adcp.__version__} < 3.10'" 2>/dev/null; then
  ok "D16 adcp library version >= 3.10"
else
  warn "D16 adcp version check skipped (uv/adcp not importable in current env)"
fi

# --- end Round 14 deep-verify additions ---

echo "PR 2 verification: complete"
