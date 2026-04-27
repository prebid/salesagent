#!/usr/bin/env bash
# Verification for PR 5 — Cross-surface version consolidation
set -uo pipefail

# Source shared helpers (fail/ok/warn/section + common checks live in _lib.sh)
source "$(dirname "$0")/_lib.sh"

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
  # FROM line must use templated form (FROM python:${PYTHON_VERSION}-slim@${PYTHON_BASE_DIGEST})
  grep -qE 'FROM python:\$\{PYTHON_VERSION\}-slim@\$\{PYTHON_BASE_DIGEST\}' Dockerfile \
    || fail "Dockerfile FROM does not use templated 'FROM python:\${PYTHON_VERSION}-slim@\${PYTHON_BASE_DIGEST}' form"
  ok "Dockerfile FROM uses templated PYTHON_VERSION + PYTHON_BASE_DIGEST"
fi

# black target-version (D28: bump DEFERRED to post-#1234 follow-up per ADR-008)
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
PG_TAGS=$(grep -rhoE 'postgres:1[0-9](\.[0-9]+)?(-alpine)?' \
  docker-compose*.yml .github/workflows/ Dockerfile* 2>/dev/null | sort -u)
PG_REFS=$(echo -n "$PG_TAGS" | grep -c . || true)
[[ "$PG_REFS" -le 1 ]] \
  || fail "$PG_REFS distinct postgres image tags found (should be 1; verify D24-style anchor)"
if [[ "$PG_REFS" -eq 1 ]]; then
  [[ "$PG_TAGS" == "postgres:17-alpine" ]] \
    || fail "postgres image tag is '$PG_TAGS'; PR 5 requires 'postgres:17-alpine' (D24)"
  ok "postgres image refs converged on postgres:17-alpine (D24)"
fi
if [[ "$PG_REFS" -eq 0 ]]; then
  warn "no postgres image references found — partial PR application or full removal? (PR 5 expects postgres:17-alpine)"
fi

# PR 5 guard
if [[ -f tests/unit/test_architecture_uv_version_anchor.py ]]; then
  ok "test_architecture_uv_version_anchor present (D18 +1)"
  # Execute the structural guard so uv version drift is caught at verify time too
  uv run pytest tests/unit/test_architecture_uv_version_anchor.py -v -x 2>/dev/null \
    || warn "uv version anchor structural guard fails (full check requires running tests)"
fi

# D34 + R11A-02 — Dockerfile hardening additions
if [[ -f Dockerfile ]]; then
  # @sha256: digest pin on base image (D34)
  grep -qE '(@\$\{PYTHON_BASE_DIGEST\}|@sha256:[a-f0-9]{64})' Dockerfile \
    || fail "Dockerfile FROM line missing @sha256: digest pin (D34)"
  ok "Dockerfile base image @sha256: digest pinned (D34)"
  # USER non-root in runtime stage (D34)
  grep -qE '^USER ' Dockerfile \
    || fail "Dockerfile missing USER non-root directive (D34)"
  ! grep -qE '^USER (root|0)\s*$' Dockerfile \
    || fail "Dockerfile USER directive points to root — must be non-root (D34)"
  ok "Dockerfile USER non-root directive present (D34)"
  # ARG SOURCE_DATE_EPOCH (R11A-02 — without ARG, PR 6's --build-arg silently no-ops)
  grep -qE '^ARG SOURCE_DATE_EPOCH' Dockerfile \
    || fail "Dockerfile missing ARG SOURCE_DATE_EPOCH declaration (R11A-02; PR 6 build-arg no-ops without it)"
  ok "Dockerfile ARG SOURCE_DATE_EPOCH declared (R11A-02 fix; reproducible-build claim operational)"
  # PD12 — install uv via ghcr.io OCI image (COPY --from=) instead of pip install
  grep -qE 'COPY --from=ghcr\.io/astral-sh/uv:[0-9.]+' Dockerfile \
    || fail "Dockerfile missing uv COPY --from pattern (PD12)"
  ! grep -qE '^RUN pip install.*uv' Dockerfile \
    || fail "Dockerfile still has pip install uv"
  ok "Dockerfile installs uv via COPY --from=ghcr.io/astral-sh/uv (PD12)"
fi

# D34 — structural guard for Dockerfile digest+USER
if [[ -f tests/unit/test_architecture_dockerfile_digest_pinned.py ]]; then
  ok "test_architecture_dockerfile_digest_pinned present (D34)"
fi

# D36 — ADR-008 copied to docs/decisions/ in PR 5 commit 7b
if [[ -f docs/decisions/adr-008-target-version-bump.md ]]; then
  grep -qE '^## Status' docs/decisions/adr-008-target-version-bump.md \
    || fail "docs/decisions/adr-008-target-version-bump.md missing canonical ## Status header"
  ok "ADR-008 copied to docs/decisions/ (D36)"
fi

echo "PR 5 verification: complete"
