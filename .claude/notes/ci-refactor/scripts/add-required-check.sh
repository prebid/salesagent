#!/usr/bin/env bash
# Append a required-check context to branch protection (ADMIN-ONLY).
#
# DO NOT RUN AS AN AGENT. Per `feedback_user_owns_git_push.md`, only the
# user runs branch-protection mutations. This script exists as the
# operator's single command for adding an additional required check
# without disturbing the 11 frozen names from `flip-branch-protection.sh`.
#
# Pre-conditions:
#   - The new check has rendered green on at least 2 PRs
#   - branch-protection-snapshot.json captured pre-change (rollback contract)
#
# Usage:
#   ./add-required-check.sh "Security / Dependency Review"
set -euo pipefail

REPO="${REPO:-prebid/salesagent}"
NEW_CHECK="${1:?Usage: $0 \"<context-name>\"}"

# Verify the check has actually rendered (rendered names diverge from spec'd names — same gotcha as Phase B)
gh api "repos/${REPO}/commits/main/check-runs" --paginate --jq '.check_runs[].name' \
  | grep -Fxq "$NEW_CHECK" \
  || { echo "ERROR: '$NEW_CHECK' not found in recent main check-runs. Has the workflow merged?" >&2; exit 2; }

# Read current contexts
CURRENT=$(gh api "repos/${REPO}/branches/main/protection/required_status_checks" \
  --jq '[.checks[].context]')

# Idempotent: skip if already present
if echo "$CURRENT" | jq -e --arg c "$NEW_CHECK" '. | index($c)' >/dev/null; then
  echo "Already present: '$NEW_CHECK' — no change."
  exit 0
fi

# Append + dedupe
NEW=$(echo "$CURRENT" | jq --arg c "$NEW_CHECK" '. + [$c] | unique')

read -p "Add required check '$NEW_CHECK' to main branch protection — type 'ADD' to proceed: " CONFIRM
[[ "$CONFIRM" == "ADD" ]] || { echo "Cancelled." >&2; exit 0; }

gh api -X PATCH \
  "/repos/${REPO}/branches/main/protection/required_status_checks" \
  -H "Accept: application/vnd.github+json" \
  --input - <<EOF
{
  "strict": true,
  "checks": $(echo "$NEW" | jq '[.[] | {context: .}]')
}
EOF

# Verify (uses canonical `.checks[].context`; `.contexts[]` is deprecated)
gh api "repos/${REPO}/branches/main/protection/required_status_checks" \
  --jq '.checks[].context' | grep -Fxq "$NEW_CHECK" \
  && echo "OK: '$NEW_CHECK' is now a required check." \
  || { echo "MISMATCH — investigate immediately." >&2; exit 1; }
