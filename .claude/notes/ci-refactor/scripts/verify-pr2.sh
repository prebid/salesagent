#!/usr/bin/env bash
# Verification for PR 2 — uv.lock single-source for pre-commit deps
set -uo pipefail
fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  ok: $*"; }

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

echo "PR 2 verification: complete"
