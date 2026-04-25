#!/usr/bin/env bash
# Pre-flip rendered-name capture for PR 3 Phase B (D17 + D26).
#
# GitHub renders status checks as `<workflow.name> / <job.name>` and the
# branch-protection `context` field requires an exact-string match. Reusable
# workflow nesting can produce 3-segment names. Capture actual names BEFORE
# the Phase B atomic flip; if they diverge, update the PATCH body to match.
#
# Run after Phase A is merged and at least 2 PRs have shown the new check
# names green.
set -euo pipefail

REPO="${REPO:-prebid/salesagent}"
PR_QUERY="${PR_QUERY:-phase a}"

PR_SHA=$(gh pr list --repo "$REPO" --state merged --limit 1 --search "$PR_QUERY" \
  --json headRefOid --jq '.[0].headRefOid')

if [[ -z "$PR_SHA" || "$PR_SHA" == "null" ]]; then
  echo "ERROR: no merged PR found matching search '$PR_QUERY'" >&2
  echo "Provide PR_QUERY=<search-string> or PR_SHA=<sha> environment variable." >&2
  exit 2
fi

OUT=/tmp/rendered-names.txt
gh api "repos/${REPO}/commits/${PR_SHA}/check-runs" --paginate \
  --jq '.check_runs[].name' | sort -u > "$OUT"

cat <<'EOF' | sort -u > /tmp/expected-names.txt
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

if diff -q /tmp/expected-names.txt <(grep -F -f /tmp/expected-names.txt "$OUT" | sort -u) >/dev/null; then
  echo "OK: all 11 expected check-run names found in rendered output."
  echo "Phase B PATCH body is safe to apply as-written."
  exit 0
else
  echo "DIVERGENCE: rendered names differ from expected." >&2
  echo "" >&2
  echo "Rendered (sample):" >&2
  head -20 "$OUT" >&2
  echo "" >&2
  echo "Expected:" >&2
  cat /tmp/expected-names.txt >&2
  echo "" >&2
  echo "Action required: update PR 3 Phase B Step 2 PATCH body to use the rendered names verbatim, OR flatten the reusable workflow so the calling job's name is what publishes." >&2
  exit 1
fi
