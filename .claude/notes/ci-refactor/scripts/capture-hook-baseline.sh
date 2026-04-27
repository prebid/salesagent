#!/usr/bin/env bash
# Pre-flight P10 — capture pre-commit hook count baseline (Round 14 M5).
#
# **Run BEFORE PR 1 is authored.** Captures the current hook count baseline
# so PR 4's hook math (D27: 36 effective − 13 deletions − 10 to pre-push − 1
# consolidation = 12) can be verified against drift. Without this, a
# contributor adding a hook between PR 1 author start and PR 4 merge causes
# silent math overflow at PR 4 acceptance time (week 4-5).
#
# git-log of .pre-commit-config.yaml shows ~1 hook modification every ~6 weeks.
# Across the PR1→PR4 calendar window, historical drift probability is ~70% — not theoretical.
#
# Output: .claude/notes/ci-refactor/.hook-baseline.txt (committed alongside
# other pre-flight evidence). verify-pr4.sh reads this file to check the
# baseline hasn't shifted from 36.
#
# Tripwire: if drift detected mid-rollout, see runbooks/PR4-partial-deletion-recovery.md
# for the decision tree (baseline+1: identify 11th move; baseline+2: two more
# moves OR delete the new hook; baseline≥+3: STOP and re-architect).

set -euo pipefail

CFG=".pre-commit-config.yaml"
[[ -f "$CFG" ]] || { echo "FAIL: $CFG not found in $(pwd)"; exit 1; }

TOTAL=$(grep -cE "^[[:space:]]*- id: " "$CFG")
MANUAL=$(grep -cE "stages: \[manual\]" "$CFG" || echo 0)
EFFECTIVE=$((TOTAL - MANUAL))

OUT=".claude/notes/ci-refactor/.hook-baseline.txt"
mkdir -p "$(dirname "$OUT")"
{
  echo "captured_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "captured_from_sha: $(git rev-parse HEAD 2>/dev/null || echo 'no-git')"
  echo "config_file: $CFG"
  echo "total_hooks: $TOTAL"
  echo "manual_hooks: $MANUAL"
  echo "effective_commit_stage: $EFFECTIVE"
  echo "expected_baseline: 36"
} > "$OUT"

echo "Hook baseline captured to $OUT:"
cat "$OUT"
echo ""

if [[ "$EFFECTIVE" -ne 36 ]]; then
  echo "WARN: effective commit-stage count is $EFFECTIVE (expected 36)."
  echo "      D27 math may need re-derivation before PR 4 author start."
  echo "      See runbooks/PR4-partial-deletion-recovery.md for decision tree."
  exit 0  # warn-only; downstream verify-pr4.sh enforces blocking
fi

echo "OK: baseline matches D27 expected count (36)."
