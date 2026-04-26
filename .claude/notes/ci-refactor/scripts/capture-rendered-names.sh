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

# Prefer explicit PR_SHA argument (avoids fragile title-search). Fallback: PR_QUERY string.
# Default PR_QUERY is "phase a" but a contributor naming the PR `feat(ci): authoritative`
# would not match — set PR_SHA explicitly in that case.
if [[ -z "${PR_SHA:-}" ]]; then
  PR_QUERY="${PR_QUERY:-phase a}"
  PR_SHA=$(gh pr list --repo "$REPO" --state merged --limit 1 --search "$PR_QUERY" \
    --json headRefOid --jq '.[0].headRefOid')
  if [[ -z "$PR_SHA" || "$PR_SHA" == "null" ]]; then
    echo "ERROR: no merged PR found matching search '$PR_QUERY'" >&2
    echo "Set PR_SHA=<sha> explicitly, or PR_QUERY=<search-string>." >&2
    exit 2
  fi
fi
echo "Probing rendered names for PR_SHA=${PR_SHA}"

# Decision-4 design-time check: confirm ci.yml uses composite actions
# (./.github/actions/...) rather than reusable workflows (./.github/workflows/_*.yml)
# for test-suite jobs. Hoisted ABOVE the runtime probe so it runs before any exit.
if [[ -f .github/workflows/ci.yml ]]; then
  if grep -qE '^\s+uses:\s+\./\.github/workflows/_' .github/workflows/ci.yml; then
    echo "ERROR: ci.yml references a reusable workflow (./.github/workflows/_...)." >&2
    echo "Per Decision-4, test-suite jobs MUST use composite actions" >&2
    echo "(./.github/actions/...) to avoid 3-segment rendered names." >&2
    echo "Fix ci.yml before running the runtime probe." >&2
    exit 2
  fi
fi

OUT=/tmp/rendered-names.txt
gh api "repos/${REPO}/commits/${PR_SHA}/check-runs" --paginate \
  --jq '.check_runs[].name' | sort -u > "$OUT"

# 14 frozen names per D17 amended by D30 (D30 added Smoke Tests, Security Audit, Quickstart).
cat <<'EOF' | sort -u > /tmp/expected-names.txt
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

if diff -q /tmp/expected-names.txt <(grep -F -f /tmp/expected-names.txt "$OUT" | sort -u) >/dev/null; then
  echo "OK: all 14 expected check-run names found in rendered output."
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
  echo "Action required: post Decision-4, all test-suite jobs MUST use the composite action" >&2
  echo "(./.github/actions/_pytest), NOT a reusable workflow (./.github/workflows/_*.yml)." >&2
  echo "If divergence is from a re-introduced reusable workflow, audit ci.yml jobs and convert." >&2
  echo "If divergence is from a job rename, update ci.yml AND the Phase B PATCH body." >&2
  echo "Do NOT flip Phase B until rendered names match — the structural guard" >&2
  echo "test_architecture_required_ci_checks_frozen.py should have caught this; if it didn't," >&2
  echo "the guard's check is incomplete." >&2
  exit 1
fi
