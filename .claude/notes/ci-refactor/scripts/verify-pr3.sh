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

  # All 11 frozen check names exist as bare job-name strings (D26: rendered = `CI` + ` / ` + bare).
  # Quoted form ('Foo' / "Foo") OR unquoted bare form (   name: Foo) — accept all three.
  for name in 'Quality Gate' 'Type Check' 'Schema Contract' 'Unit Tests' 'Integration Tests' \
              'E2E Tests' 'Admin UI Tests' 'BDD Tests' 'Migration Roundtrip' 'Coverage' 'Summary'; do
    grep -qF "name: '$name'" .github/workflows/ci.yml || \
      grep -qF "name: \"$name\"" .github/workflows/ci.yml || \
      grep -qE "^\s+name:\s+${name}\s*$" .github/workflows/ci.yml || \
      fail "ci.yml missing job name: $name"
  done

  # Develop branch trigger (P0 sweep — covers existing test.yml branches: [main, develop] until v2.0 ships)
  grep -qE 'branches:\s+\[main,\s*develop\]|branches:\s+\[\s*main\s*,\s*develop\s*\]' .github/workflows/ci.yml \
    || fail "ci.yml triggers must include 'develop' branch (P0 sweep — formal deprecation deferred)"

  ok "ci.yml present, properly structured, 11 frozen bare names + develop branch"
fi

# Decision-4 (P0 sweep): _pytest is a composite action, NOT a reusable workflow.
# Reusable form would re-introduce 3-segment rendered names ('CI / Unit Tests / pytest').
if [[ -f .github/workflows/_pytest.yml ]]; then
  fail ".github/workflows/_pytest.yml exists — Decision-4 (P0 sweep) requires composite at .github/actions/_pytest/action.yml instead"
fi
if [[ -f .github/actions/_pytest/action.yml ]]; then
  yamllint -d relaxed .github/actions/_pytest/action.yml >/dev/null 2>&1 || fail "_pytest/action.yml fails yamllint"
  grep -qE 'using:\s+["\x27]?composite' .github/actions/_pytest/action.yml \
    || fail "_pytest/action.yml not a composite action"
  ok "_pytest composite action present (Decision-4)"
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

# Coverage baseline (D11 revised in 2026-04-25 P0 sweep — hard-gate from PR 3 day 1, not advisory)
if [[ -f .coverage-baseline ]]; then
  [[ "$(cat .coverage-baseline)" == "53.5" ]] || fail ".coverage-baseline != 53.5"
  ok ".coverage-baseline = 53.5 (D11: hard-gate from PR 3 day 1, ratchet-only-stable)"
  # Hard-gate must be in ci.yml — `--fail-under=$(cat .coverage-baseline)`
  grep -q -- '--fail-under=$(cat .coverage-baseline)' .github/workflows/ci.yml \
    || fail "ci.yml coverage job missing --fail-under=\$(cat .coverage-baseline) (D11 hard gate)"
fi

# Gemini fallback fix (PR 3 commit 11 — moved from PR 1 commit 10 in P0 sweep)
if [[ -f .github/actions/_pytest/action.yml ]]; then
  ! grep -q 'secrets.GEMINI_API_KEY' .github/actions/_pytest/action.yml \
    || fail "_pytest/action.yml still references secrets.GEMINI_API_KEY (D15: must be unconditional mock)"
  if grep -q 'GEMINI_API_KEY' .github/actions/_pytest/action.yml; then
    grep -q "GEMINI_API_KEY: test_key_for_mocking" .github/actions/_pytest/action.yml \
      || fail "_pytest/action.yml GEMINI_API_KEY is not the unconditional mock"
    ok "Gemini key is unconditional mock (D15/PD24, moved to PR 3 in P0 sweep)"
  fi
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
