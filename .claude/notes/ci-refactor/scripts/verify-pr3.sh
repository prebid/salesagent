#!/usr/bin/env bash
# Verification for PR 3 — CI authoritative + reusable workflows (Phase A only)
# Phase B (atomic flip) is admin-only — see flip-branch-protection.sh
set -uo pipefail
fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  ok: $*"; }

# Phase A: ci.yml exists
if [[ -f .github/workflows/ci.yml ]]; then
  yamllint -d relaxed .github/workflows/ci.yml >/dev/null 2>&1 || fail "ci.yml fails yamllint"

  # Workflow naming bug check — job name MUST NOT include "CI / " prefix
  ! grep -qE "name: ['\"]CI / " .github/workflows/ci.yml \
    || fail "ci.yml has 'CI / X' job names — produces 'CI / CI / X' rendered (D26)"

  # Workflow header is `name: CI`
  grep -qE '^name: CI$' .github/workflows/ci.yml || fail "ci.yml workflow name must be 'CI'"

  # Top-level permissions
  grep -qE '^permissions:\s*\{?\s*\}?' .github/workflows/ci.yml \
    || fail "ci.yml missing top-level 'permissions: {}'"

  # Concurrency
  grep -qE '^concurrency:' .github/workflows/ci.yml \
    || fail "ci.yml missing 'concurrency:' block"

  # All 11 frozen check names exist as job-name strings (rendered name = workflow + ' / ' + job)
  for name in 'Quality Gate' 'Type Check' 'Schema Contract' 'Unit Tests' 'Integration Tests' \
              'E2E Tests' 'Admin UI Tests' 'BDD Tests' 'Migration Roundtrip' 'Coverage' 'Summary'; do
    grep -qF "name: '$name'" .github/workflows/ci.yml || \
      grep -qF "name: \"$name\"" .github/workflows/ci.yml || \
      fail "ci.yml missing job name: $name"
  done
  ok "ci.yml present, properly structured, 11 frozen names"
fi

# Reusable workflow
if [[ -f .github/workflows/_pytest.yml ]]; then
  yamllint -d relaxed .github/workflows/_pytest.yml >/dev/null 2>&1 || fail "_pytest.yml fails yamllint"
  grep -q 'workflow_call:' .github/workflows/_pytest.yml || fail "_pytest.yml missing workflow_call trigger"
  ok "_pytest.yml reusable workflow present"
fi

# Composite action
if [[ -f .github/actions/setup-env/action.yml ]]; then
  grep -q 'composite' .github/actions/setup-env/action.yml || fail "setup-env not a composite action"
  ok "setup-env composite action present"
fi

# Migration roundtrip script
if [[ -f .github/scripts/migration_roundtrip.sh ]]; then
  [[ -x .github/scripts/migration_roundtrip.sh ]] || fail "migration_roundtrip.sh not executable"
  bash -n .github/scripts/migration_roundtrip.sh || fail "migration_roundtrip.sh syntax error"
  ok "migration_roundtrip.sh present and executable"
fi

# Coverage baseline
if [[ -f .coverage-baseline ]]; then
  [[ "$(cat .coverage-baseline)" == "53.5" ]] || fail ".coverage-baseline != 53.5"
  ok ".coverage-baseline = 53.5 (per D11, advisory for 4 weeks)"
fi

# Action SHAs reused from PR 1
if [[ -f .github/.action-shas.txt ]]; then
  ok ".github/.action-shas.txt present (lifted from PR 1 commit 9)"
fi

# No || true / continue-on-error in test.yml ruff invocations (closes #1233 D6)
if [[ -f .github/workflows/test.yml ]]; then
  if grep -q 'ruff' .github/workflows/test.yml; then
    ! grep -E 'uv run ruff (check|format) [^|]*\| true' .github/workflows/test.yml \
      || fail "ruff invocation still has '|| true'"
  fi
fi

echo "PR 3 verification: complete (Phase A scope; Phase B is admin-only)"
