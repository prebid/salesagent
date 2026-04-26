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

# Commit 4.6: DB_POOL_SIZE / DB_MAX_OVERFLOW env-var wiring (D40 / R12A-01)
if [[ -f src/core/database/database_session.py ]]; then
  grep -q 'os.getenv.*DB_POOL_SIZE' src/core/database/database_session.py \
    || fail "src/core/database/database_session.py missing os.getenv(DB_POOL_SIZE) wiring (D40 / R12A-01 — wires the env var set in PR 3 _pytest action)"
  ok "DB_POOL_SIZE env-var wiring present (D40)"
fi

echo "PR 2 verification: complete"
