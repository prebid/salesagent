#!/usr/bin/env bash
# PR 3 Phase B — atomic branch-protection flip (ADMIN-ONLY).
#
# DO NOT RUN AS AN AGENT. Per `feedback_user_owns_git_push.md`, only the
# user runs branch-protection mutations. This script exists so the user
# has a single command to invoke; the agent should never call it.
#
# Pre-conditions:
#   - PR 3 Phase A merged and on main for ≥48 hours
#   - At least 2 PRs have shown the new check names green
#   - capture-rendered-names.sh has been run and matches expected
#   - branch-protection-snapshot.json captured pre-flip (rollback contract)
#   - A24 dry-run completed on a sandbox repo
#   - Repo has the `phase-b-in-progress` label created (see pr3-phase-b-checklist.md prereqs)
set -euo pipefail

# D45 day-of-week guard — Phase B is FORBIDDEN on Fri/Sat/Sun (and US-federal-holiday-eve;
# operator must enforce holiday-eve manually since holiday lookup is jurisdiction-bound).
DAY=$(date +%u)  # 1=Mon ... 7=Sun
if [[ "$DAY" -ge 5 ]]; then
  echo "ERROR: D45 forbids Phase B execution on Friday (5), Saturday (6), or Sunday (7)." >&2
  echo "Today is day $DAY of week. Reschedule for Mon-Thu." >&2
  echo "Override with FORCE=1 ./flip-branch-protection.sh (NOT RECOMMENDED — see D45 rationale)." >&2
  [[ "${FORCE:-0}" == "1" ]] || exit 1
  echo "WARNING: FORCE=1 override active; proceeding against D45 guidance." >&2
fi

REPO="${REPO:-prebid/salesagent}"
SNAPSHOT="${SNAPSHOT:-.claude/notes/ci-refactor/branch-protection-snapshot.json}"
DRY_RUN="${DRY_RUN:-0}"

[[ -f "$SNAPSHOT" ]] || { echo "ERROR: pre-flip snapshot missing: $SNAPSHOT (see pre-flight A1)" >&2; exit 2; }

# Phase B mutex — prevent concurrent flips by multiple admins. Uses a GitHub-issue
# semaphore: open issue = lock held; closed = released. Stale locks must be cleared
# manually by an admin (gh issue close <number> --comment "Stale lock cleared").
EXISTING=$(gh issue list --repo "$REPO" --label "phase-b-in-progress" --state open --json number --jq 'length' 2>/dev/null || echo 0)
if [[ "$EXISTING" -gt 0 ]]; then
  echo "ERROR: An issue with label 'phase-b-in-progress' is already open." >&2
  echo "Either another admin is currently executing Phase B, OR a previous attempt left a stale lock." >&2
  echo "Verify: gh issue list --repo $REPO --label phase-b-in-progress" >&2
  echo "If stale: gh issue close <number> --comment 'Stale lock cleared'" >&2
  exit 1
fi
ISSUE_NUMBER=$(gh issue create --repo "$REPO" \
  --title "PHASE-B-IN-PROGRESS [$(date +%FT%H:%M)]" \
  --label "phase-b-in-progress" \
  --body "Phase B branch-protection flip in progress by ${USER:-admin}. Auto-closes on script completion." \
  --json number --jq '.number')
trap 'gh issue close "$ISSUE_NUMBER" --comment "Phase B flip script exited."' EXIT

echo "Confirming pre-flight: rendered names check"
"$(dirname "$0")/capture-rendered-names.sh" || { echo "ERROR: rendered names diverge — fix before flipping" >&2; exit 2; }

# Idempotency check: if we're already in target state, exit 0 with informational message
# 14 frozen names per D17 amended by D30 (D30 added Smoke Tests, Security Audit, Quickstart).
EXISTING_CONTEXTS=$(gh api "repos/${REPO}/branches/main/protection/required_status_checks" \
  --jq '.checks[].context' 2>/dev/null | sort | tr '\n' '|')
EXPECTED_CONTEXTS=$(printf 'CI / Admin UI Tests|CI / BDD Tests|CI / Coverage|CI / E2E Tests|CI / Integration Tests|CI / Migration Roundtrip|CI / Quality Gate|CI / Quickstart|CI / Schema Contract|CI / Security Audit|CI / Smoke Tests|CI / Summary|CI / Type Check|CI / Unit Tests|')
if [[ "$EXISTING_CONTEXTS" == "$EXPECTED_CONTEXTS" ]]; then
  echo "Already in target state — 14 frozen contexts match. No action needed."
  exit 0
fi

# Capture pre-flip state for diff/rollback (ratchet snapshot)
PRE_FLIP_FILE="${SNAPSHOT%.json}-pre-flip.json"
gh api "repos/${REPO}/branches/main/protection" -H "Accept: application/vnd.github+json" \
  > "$PRE_FLIP_FILE"
echo "Pre-flip snapshot saved to $PRE_FLIP_FILE (used for rollback if PATCH fails)."

if [[ "$DRY_RUN" == "1" ]]; then
  echo "DRY_RUN=1 — printing PATCH body and exiting without applying:"
  cat <<'EOF'
{
  "strict": true,
  "checks": [
    {"context": "CI / Quality Gate"},
    {"context": "CI / Type Check"},
    {"context": "CI / Schema Contract"},
    {"context": "CI / Security Audit"},
    {"context": "CI / Quickstart"},
    {"context": "CI / Smoke Tests"},
    {"context": "CI / Unit Tests"},
    {"context": "CI / Integration Tests"},
    {"context": "CI / E2E Tests"},
    {"context": "CI / Admin UI Tests"},
    {"context": "CI / BDD Tests"},
    {"context": "CI / Migration Roundtrip"},
    {"context": "CI / Coverage"},
    {"context": "CI / Summary"}
  ]
}
EOF
  exit 0
fi

read -p "Phase B atomic flip — type 'FLIP' to proceed (any other input cancels): " CONFIRM
[[ "$CONFIRM" == "FLIP" ]] || { echo "Cancelled." >&2; exit 0; }

gh api -X PATCH \
  "/repos/${REPO}/branches/main/protection/required_status_checks" \
  -H "Accept: application/vnd.github+json" \
  --input - <<'EOF'
{
  "strict": true,
  "checks": [
    {"context": "CI / Quality Gate"},
    {"context": "CI / Type Check"},
    {"context": "CI / Schema Contract"},
    {"context": "CI / Security Audit"},
    {"context": "CI / Quickstart"},
    {"context": "CI / Smoke Tests"},
    {"context": "CI / Unit Tests"},
    {"context": "CI / Integration Tests"},
    {"context": "CI / E2E Tests"},
    {"context": "CI / Admin UI Tests"},
    {"context": "CI / BDD Tests"},
    {"context": "CI / Migration Roundtrip"},
    {"context": "CI / Coverage"},
    {"context": "CI / Summary"}
  ]
}
EOF

echo ""
echo "Flip applied. Verifying..."
gh api "repos/${REPO}/branches/main/protection/required_status_checks" \
  --jq '.checks[].context' | sort > /tmp/protected-now   # canonical field; .contexts[] is deprecated
sort > /tmp/expected-now <<'EOF'
CI / Quality Gate
CI / Type Check
CI / Schema Contract
CI / Security Audit
CI / Quickstart
CI / Smoke Tests
CI / Unit Tests
CI / Integration Tests
CI / E2E Tests
CI / Admin UI Tests
CI / BDD Tests
CI / Migration Roundtrip
CI / Coverage
CI / Summary
EOF
diff /tmp/protected-now /tmp/expected-now && echo "OK: 14 frozen contexts match." || \
  { echo "MISMATCH — investigate immediately, see PR 3 Phase B rollback section" >&2; exit 1; }
