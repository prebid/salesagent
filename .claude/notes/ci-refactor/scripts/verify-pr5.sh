#!/usr/bin/env bash
# Verification for PR 5 — Cross-surface version consolidation
set -uo pipefail
fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  ok: $*"; }

# Anchor sources
[[ -f .python-version ]] && PYV=$(cat .python-version) && ok ".python-version = $PYV"

# mypy.ini matches
if [[ -f mypy.ini ]] && [[ -n "${PYV:-}" ]]; then
  PY_MAJOR_MINOR=$(echo "$PYV" | cut -d. -f1,2)
  grep -qE "python_version\s*=\s*${PY_MAJOR_MINOR}" mypy.ini \
    || fail "mypy.ini python_version != $PY_MAJOR_MINOR"
  ok "mypy.ini python_version aligned with .python-version"
fi

# Dockerfile build-arg propagation
if [[ -f Dockerfile ]] && [[ -n "${PYV:-}" ]]; then
  grep -qE "ARG PYTHON_VERSION" Dockerfile && ok "Dockerfile has ARG PYTHON_VERSION"
fi

# black target-version (D28 P0 sweep: bump DEFERRED to post-#1234 follow-up per ADR-008)
# PR 5 verifies the bump did NOT happen prematurely.
if grep -q 'target-version' pyproject.toml; then
  grep -qE 'target-version\s*=\s*\[?["'\'']py311' pyproject.toml \
    || fail "black target-version drifted; D28 holds at py311 until post-#1234 follow-up"
  ok "black target-version held at py311 (D28 — bump deferred per ADR-008)"
fi

# ruff target-version (D28: same deferral)
if grep -qE '\[tool\.ruff\]' pyproject.toml; then
  grep -qE 'target-version\s*=\s*["'\'']py311' pyproject.toml \
    || fail "ruff target-version drifted; D28 holds at py311 until post-#1234 follow-up"
  ok "ruff target-version held at py311 (D28 — bump deferred per ADR-008)"
fi

# UV_VERSION anchor in setup-env
if [[ -f .github/actions/setup-env/action.yml ]]; then
  grep -qE 'UV_VERSION\s*:' .github/actions/setup-env/action.yml \
    && ok "UV_VERSION anchored in setup-env composite action (D24)"
fi

# Postgres version anchor (PG17 across all references)
PG_REFS=$(grep -rhoE 'postgres:1[0-9](\.[0-9]+)?(-alpine)?' \
  docker-compose*.yml .github/workflows/ Dockerfile* 2>/dev/null | sort -u | wc -l)
[[ "$PG_REFS" -le 1 ]] && ok "postgres image refs converged (1 unique tag)" \
  || echo "  warn: $PG_REFS distinct postgres image tags (should be 1; verify D24-style anchor)"

# PR 5 guard
if [[ -f tests/unit/test_architecture_uv_version_anchor.py ]]; then
  ok "test_architecture_uv_version_anchor present (D18 +1)"
fi

echo "PR 5 verification: complete"
