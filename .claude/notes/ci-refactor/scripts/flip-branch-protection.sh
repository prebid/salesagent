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
set -euo pipefail

REPO="${REPO:-prebid/salesagent}"
SNAPSHOT="${SNAPSHOT:-.claude/notes/ci-refactor/branch-protection-snapshot.json}"

[[ -f "$SNAPSHOT" ]] || { echo "ERROR: pre-flip snapshot missing: $SNAPSHOT (see pre-flight A1)" >&2; exit 2; }

echo "Confirming pre-flight: rendered names check"
"$(dirname "$0")/capture-rendered-names.sh" || { echo "ERROR: rendered names diverge — fix before flipping" >&2; exit 2; }

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
  --jq '.contexts[]' | sort > /tmp/protected-now
sort > /tmp/expected-now <<'EOF'
CI / Quality Gate
CI / Type Check
CI / Schema Contract
CI / Unit Tests
CI / Integration Tests
CI / E2E Tests
CI / Admin UI Tests
CI / BDD Tests
CI / Migration Roundtrip
CI / Coverage
CI / Summary
EOF
diff /tmp/protected-now /tmp/expected-now && echo "OK: 11 frozen contexts match." || \
  { echo "MISMATCH — investigate immediately, see PR 3 Phase B rollback section" >&2; exit 1; }
