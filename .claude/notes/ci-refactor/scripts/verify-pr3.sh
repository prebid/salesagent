#!/usr/bin/env bash
# Verification for PR 3 — CI authoritative + composite actions
#
# Usage: ./verify-pr3.sh [phase]   # phase = a|b|c|all (default: all)
#   a   — Phase A: ci.yml exists, 14 jobs declared, composite actions exist, creative-agent script-step pattern
#   b   — Phase B: NOTHING (admin-only via flip-branch-protection.sh — no design-time check beyond Phase A's)
#   c   — Phase C: test.yml deleted, ci.yml is sole CI workflow, 3 successful main runs
#   all — runs a + c (b is intentionally a no-op here; admin executes via separate script)
set -uo pipefail

# Source shared verification helpers (fail/ok/warn/section/check_*)
source "$(dirname "$0")/_lib.sh"

PHASE="${1:-all}"
case "$PHASE" in
  a|b|c|all) ;;
  *) fail "Unknown phase '$PHASE' — must be a|b|c|all" ;;
esac

# Phase A: ci.yml exists
if [[ "$PHASE" == "a" || "$PHASE" == "all" ]]; then
section "Phase A — design-time checks (ci.yml + composites + xdist gate)"
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

  # All 14 frozen check names per D17 amended by D30 (D30 added Smoke Tests, Security Audit,
  # Quickstart) exist as bare job-name strings (D26: rendered = `CI` + ` / ` + bare).
  # Quoted form ('Foo' / "Foo") OR unquoted bare form (   name: Foo) — accept all three.
  for name in 'Quality Gate' 'Type Check' 'Schema Contract' 'Security Audit' 'Quickstart' \
              'Smoke Tests' 'Unit Tests' 'Integration Tests' 'E2E Tests' 'Admin UI Tests' \
              'BDD Tests' 'Migration Roundtrip' 'Coverage' 'Summary'; do
    grep -qF "name: '$name'" .github/workflows/ci.yml || \
      grep -qF "name: \"$name\"" .github/workflows/ci.yml || \
      grep -qE "^\s+name:\s+${name}\s*$" .github/workflows/ci.yml || \
      fail "ci.yml missing job name: $name"
  done

  # workflow_dispatch trigger preserved (D37) — required by Phase B Step 1b
  # rendered-name capture and Phase B Step 2.5 in-flight PR drain.
  grep -qE '^\s+workflow_dispatch:' .github/workflows/ci.yml \
    || fail "ci.yml missing workflow_dispatch: trigger (D37, R37 mitigation)"

  # Develop branch trigger — covers existing test.yml branches: [main, develop] until v2.0 ships
  grep -qE 'branches:\s+\[main,\s*develop\]|branches:\s+\[\s*main\s*,\s*develop\s*\]' .github/workflows/ci.yml \
    || fail "ci.yml triggers must include 'develop' branch (formal deprecation deferred)"

  ok "ci.yml present, properly structured, 14 frozen bare names + workflow_dispatch + develop branch"
fi

# Decision-4: _pytest is a composite action, NOT a reusable workflow.
# Reusable form would re-introduce 3-segment rendered names ('CI / Unit Tests / pytest').
if [[ -f .github/workflows/_pytest.yml ]]; then
  fail ".github/workflows/_pytest.yml exists — Decision-4 requires composite at .github/actions/_pytest/action.yml instead"
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

# Coverage baseline (D11 — hard-gate from PR 3 day 1, not advisory)
if [[ -f .coverage-baseline ]]; then
  [[ "$(cat .coverage-baseline)" == "53.5" ]] || fail ".coverage-baseline != 53.5"
  ok ".coverage-baseline = 53.5 (D11: hard-gate from PR 3 day 1, ratchet-only-stable)"
  # Hard-gate must be in ci.yml — `--fail-under=$(cat .coverage-baseline)`
  grep -q -- '--fail-under=$(cat .coverage-baseline)' .github/workflows/ci.yml \
    || fail "ci.yml coverage job missing --fail-under=\$(cat .coverage-baseline) (D11 hard gate)"
fi

# Gemini fallback fix (PR 3 commit 11 — moved from PR 1 commit 10)
if [[ -f .github/actions/_pytest/action.yml ]]; then
  ! grep -q 'secrets.GEMINI_API_KEY' .github/actions/_pytest/action.yml \
    || fail "_pytest/action.yml still references secrets.GEMINI_API_KEY (D15: must be unconditional mock)"
  if grep -q 'GEMINI_API_KEY' .github/actions/_pytest/action.yml; then
    grep -q "GEMINI_API_KEY: test_key_for_mocking" .github/actions/_pytest/action.yml \
      || fail "_pytest/action.yml GEMINI_API_KEY is not the unconditional mock"
    ok "Gemini key is unconditional mock (D15/PD24, moved to PR 3)"
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

# D39 — creative-agent runs as docker-run script steps (NOT services blocks).
# Filelock + worker-id gate (PR 3 commit 4c) verified separately below.
if [[ -f .github/workflows/ci.yml ]]; then
  # Negative: no creative-agent or creative-postgres in services: blocks
  ! grep -qE '^\s+creative-agent:' .github/workflows/ci.yml \
    || fail "ci.yml has services-block creative-agent — D39 requires docker-run script-step pattern"
  ! grep -qE '^\s+creative-postgres:' .github/workflows/ci.yml \
    || fail "ci.yml has services-block creative-postgres — D39 requires docker-run script-step pattern"
  # Positive: docker network + docker run pattern
  grep -q 'docker network create creative-net' .github/workflows/ci.yml \
    || fail "ci.yml missing 'docker network create creative-net' (D39)"
  grep -q 'docker run -d --network creative-net --name creative-agent' .github/workflows/ci.yml \
    || fail "ci.yml missing creative-agent docker-run step (D39)"
  grep -q 'ca70dd1e2a6c' .github/workflows/ci.yml \
    || fail "ci.yml missing pinned creative-agent commit ca70dd1e2a6c (D32; refresh per A23 if stale)"
  grep -qE "CREATIVE_AGENT_URL: 'http://localhost:9999/api/creative-agent'" .github/workflows/ci.yml \
    || fail "ci.yml integration-tests env: CREATIVE_AGENT_URL must be 'http://localhost:9999/api/creative-agent'"
  ok "creative-agent docker-run script-step pattern present (D39)"
fi

# D33 — pytest-xdist + pytest-randomly in dev group (added by PR 2 commit 4.5)
grep -qE '"pytest-xdist[>=]' pyproject.toml || fail "pyproject.toml missing pytest-xdist (D33; added by PR 2 commit 4.5)"
grep -qE '"pytest-randomly' pyproject.toml || fail "pyproject.toml missing pytest-randomly (D33; added by PR 2 commit 4.5)"

# Filelock + worker-id gate around migrate.py (PR 3 commit 4c)
if [[ -f tests/conftest_db.py ]]; then
  grep -q 'from filelock import FileLock' tests/conftest_db.py \
    || fail "tests/conftest_db.py missing 'from filelock import FileLock' (PR 3 commit 4c)"
  grep -q 'PYTEST_XDIST_WORKER' tests/conftest_db.py \
    || fail "tests/conftest_db.py missing PYTEST_XDIST_WORKER worker-id gate (PR 3 commit 4c)"
  ok "filelock + worker-id gate present in conftest_db.py (PR 3 commit 4c)"
fi

# D40 — DB_POOL_SIZE wired in app code
if [[ -f src/core/database/database_session.py ]]; then
  grep -q 'os.getenv("DB_POOL_SIZE"' src/core/database/database_session.py \
    || fail "database_session.py must read DB_POOL_SIZE env var (D40; pre-PR-3 commit)"
  grep -q 'os.getenv("DB_MAX_OVERFLOW"' src/core/database/database_session.py \
    || fail "database_session.py must read DB_MAX_OVERFLOW env var (D40)"
  ok "DB_POOL_SIZE + DB_MAX_OVERFLOW env vars wired in app code (D40 operational)"
fi

# Retention-days on upload-artifact (caps quota burn at 7d default vs GHA's 90d)
if [[ -f .github/actions/_pytest/action.yml ]]; then
  grep -q 'retention-days: 7' .github/actions/_pytest/action.yml \
    || fail "_pytest/action.yml missing retention-days: 7"
fi

# #1228 A5 — every ci.yml job has explicit timeout-minutes
if [[ -f .github/workflows/ci.yml ]]; then
  uv run python -c "
import yaml, sys
cfg = yaml.safe_load(open('.github/workflows/ci.yml'))
missing = [name for name, j in cfg.get('jobs', {}).items() if 'timeout-minutes' not in j]
if missing:
  print('FAIL: jobs missing timeout-minutes (would inherit GHA 360-min default):', file=sys.stderr)
  for m in missing: print(f'  - {m}', file=sys.stderr)
  sys.exit(1)
" || fail "ci.yml has jobs missing timeout-minutes (#1228 A5)"
  ok "all ci.yml jobs have explicit timeout-minutes (#1228 A5 closed)"
fi

# #1228 C5 — uv cache key hashes pyproject.toml too
if [[ -f .github/actions/setup-env/action.yml ]]; then
  grep -q 'pyproject.toml' .github/actions/setup-env/action.yml \
    || fail "setup-env/action.yml cache-dependency-glob must include pyproject.toml (#1228 C5; stale-cache class)"
  ok "uv cache-dependency-glob includes pyproject.toml (#1228 C5 closed)"
fi

fi   # end Phase A block

# Phase B is intentionally a no-op here — admin-only via flip-branch-protection.sh.
# Design-time checks for the 14-context PATCH body live in flip-branch-protection.sh
# itself (idempotency check, diff-against-expected). Nothing to verify here that
# Phase A doesn't already cover.
if [[ "$PHASE" == "b" ]]; then
  section "Phase B — admin-only (no design-time checks beyond Phase A's)"
  ok "Phase B is admin-only; run flip-branch-protection.sh manually (per A24 dry-run + D45 day guard)"
fi

# Phase C: test.yml deleted + ci.yml is sole CI workflow + 3 successful main runs
if [[ "$PHASE" == "c" || "$PHASE" == "all" ]]; then
  section "Phase C — cleanup checks"
  if [[ -f .github/workflows/test.yml ]]; then
    fail "test.yml still exists — Phase C must delete .github/workflows/test.yml"
  fi
  ok "test.yml deleted"
  # Verify ci.yml is the sole CI orchestrator (no other top-level CI workflows)
  for f in .github/workflows/*.yml; do
    case "$(basename "$f")" in
      ci.yml|codeql.yml|security.yml|release-please.yml|scorecard.yml|dependency-review.yml|labels.yml|stale.yml) ;;
      _*) ;;  # internal/utility workflows OK
      *)
        # Surface any unexpected sibling that might be acting as a competing CI
        warn "Unexpected sibling workflow: $f (review whether this duplicates ci.yml's responsibilities)"
        ;;
    esac
  done
  # 3 successful ci.yml runs on main
  if command -v gh >/dev/null 2>&1; then
    success_count=$(gh run list --workflow=ci.yml --branch=main --limit=3 --json conclusion \
      --jq 'map(select(.conclusion == "success")) | length' 2>/dev/null || echo 0)
    [[ "$success_count" -eq 3 ]] \
      || fail "ci.yml has $success_count successful main runs in last 3; need 3 for Phase C close"
    ok "ci.yml has 3/3 successful main runs"
  else
    warn "gh CLI not available; skipping main-run success check (run manually)"
  fi
fi

echo "PR 3 verification: complete (phase=$PHASE)"
