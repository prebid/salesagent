#!/bin/bash
set -euo pipefail

# =============================================================================
# bdd-pipeline.sh — Process BDD step wiring tasks via claude -p
#
# Each task gets a fresh Claude context. Quality gates run between batches.
# Full test suite runs at the end for regression verification.
#
# Usage:
#   ./scripts/bdd-pipeline.sh 9vgz.1 9vgz.2 9vgz.3      # explicit tasks
#   ./scripts/bdd-pipeline.sh --epic 9vgz                  # all tasks in epic
#   ./scripts/bdd-pipeline.sh --epic 9vgz --uc UC-002      # filter by UC
#   ./scripts/bdd-pipeline.sh --epic 9vgz --batch 5         # quality gate every 5 tasks
#   ./scripts/bdd-pipeline.sh --epic 9vgz --dry-run         # show plan only
#   ./scripts/bdd-pipeline.sh --epic 9vgz --skip-full-suite # skip final ./run_all_tests.sh
# =============================================================================

# === Parse flags ===
BATCH_SIZE=5
DRY_RUN=false
EPIC=""
UC_FILTER=""
SKIP_FULL=false
TASKS=()

while [[ $# -gt 0 ]]; do
  case $1 in
    --epic)             EPIC="$2"; shift 2 ;;
    --uc)               UC_FILTER="$2"; shift 2 ;;
    --batch)            BATCH_SIZE="$2"; shift 2 ;;
    --dry-run)          DRY_RUN=true; shift ;;
    --skip-full-suite)  SKIP_FULL=true; shift ;;
    -*)                 echo "Unknown flag: $1"; exit 1 ;;
    *)                  TASKS+=("$1"); shift ;;
  esac
done

# === Setup ===
CLAUDE="claude -p --dangerously-skip-permissions"
LOGDIR="./logs/bdd-pipeline-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOGDIR"

GIT_INSTRUCTION="IMPORTANT: Do NOT run git push or bd sync. The pipeline handles git coordination. DO commit your changes with a descriptive message."

# === Discover tasks from epic if none specified ===
if [ ${#TASKS[@]} -eq 0 ] && [ -n "$EPIC" ]; then
  echo "Discovering tasks from epic salesagent-$EPIC..."

  # Get all open children of the epic
  TASK_LIST=$(bd show "$EPIC" 2>/dev/null | grep "↳ ○" | awk '{print $3}' | sed 's/://')

  for TASK_ID in $TASK_LIST; do
    # Strip "salesagent-" prefix if present
    TASK_ID="${TASK_ID#salesagent-}"

    # Apply UC filter if specified
    if [ -n "$UC_FILTER" ]; then
      TITLE=$(bd show "$TASK_ID" 2>/dev/null | head -1 || echo "")
      if echo "$TITLE" | grep -qi "$UC_FILTER"; then
        TASKS+=("$TASK_ID")
      fi
    else
      TASKS+=("$TASK_ID")
    fi
  done
fi

TOTAL=${#TASKS[@]}
if [ "$TOTAL" -eq 0 ]; then
  echo "No tasks to process. Specify task IDs or --epic <id>."
  exit 0
fi

echo "=== BDD Pipeline ==="
echo "Tasks ($TOTAL): ${TASKS[*]}"
echo "Batch size:     $BATCH_SIZE (quality gate every $BATCH_SIZE tasks)"
echo "Logs:           $LOGDIR"
echo "Started:        $(date)"
echo ""

if [ "$DRY_RUN" = true ]; then
  echo "DRY RUN — would process $TOTAL tasks:"
  for T in "${TASKS[@]}"; do
    TITLE=$(bd show "$T" 2>/dev/null | head -1 | sed 's/^[^·]*· //' | sed 's/ *\[.*//')
    echo "  - $T: ${TITLE:-???}"
  done
  exit 0
fi

# === Process tasks ===
COMPLETED=0
FAILED=0
BATCH_COUNT=0

for TASK_ID in "${TASKS[@]}"; do
  LOG="$LOGDIR/task-$TASK_ID.log"
  TITLE=$(bd show "$TASK_ID" 2>/dev/null | head -1 | sed 's/^[^·]*· //' | sed 's/ *\[.*//' || echo "???")

  echo "[$(( COMPLETED + FAILED + 1 ))/$TOTAL] $TASK_ID: $TITLE"

  # Run claude -p with the /execute skill
  $CLAUDE "/dev-practices:execute $TASK_ID

Context: This is a BDD step wiring task. Wire the step definitions for the scenarios listed in the task title. Use the existing harness infrastructure (MediaBuyCreateEnv, MediaBuyUpdateEnv, MediaBuyListEnv). Follow the patterns established in:
- tests/bdd/steps/generic/given_media_buy.py (Given steps)
- tests/bdd/steps/generic/then_media_buy.py (Then steps)
- tests/bdd/steps/domain/uc002_create_media_buy.py (UC-002 domain steps)
- tests/bdd/steps/domain/uc003_update_media_buy.py (UC-003 domain steps)
- tests/bdd/steps/domain/uc019_query_media_buys.py (UC-019 domain steps)
- tests/bdd/steps/domain/uc026_package_media_buy.py (UC-026 domain steps)

If a scenario fails because the production code doesn't implement the expected behavior (spec-production gap), that's OK — record the gap in the task notes and move on. The goal is to wire the steps so they exercise real production code, not to fix production bugs.

$GIT_INSTRUCTION" \
    > "$LOG" 2>&1

  EXIT_CODE=$?

  if [ $EXIT_CODE -eq 0 ]; then
    echo "  [done] $TASK_ID"
    COMPLETED=$((COMPLETED + 1))
  else
    echo "  [FAIL] $TASK_ID (exit=$EXIT_CODE) — check $LOG"
    FAILED=$((FAILED + 1))
  fi

  BATCH_COUNT=$((BATCH_COUNT + 1))

  # === Quality gate every BATCH_SIZE tasks ===
  if [ "$BATCH_COUNT" -ge "$BATCH_SIZE" ]; then
    BATCH_COUNT=0
    echo ""
    echo "=== Quality Gate (after $COMPLETED tasks) ==="

    # Sync beads
    bd sync 2>&1 | tail -1 || true

    # Run make quality
    QUALITY_LOG="$LOGDIR/quality-after-$COMPLETED.log"
    if make quality > "$QUALITY_LOG" 2>&1; then
      PASS_COUNT=$(tail -1 "$QUALITY_LOG" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || echo "?")
      echo "  make quality: $PASS_COUNT passed"
    else
      echo "  make quality: FAILED — check $QUALITY_LOG"
      echo "  Stopping pipeline. Fix quality issues before continuing."
      echo ""
      echo "  Resume with remaining tasks:"
      REMAINING_TASKS=("${TASKS[@]:$((COMPLETED + FAILED))}")
      echo "  ./scripts/bdd-pipeline.sh ${REMAINING_TASKS[*]}"
      break
    fi
    echo ""
  fi
done

# === Final quality gate ===
echo ""
echo "=== Final Quality Gate ==="
bd sync 2>&1 | tail -1 || true

QUALITY_LOG="$LOGDIR/quality-final.log"
if make quality > "$QUALITY_LOG" 2>&1; then
  PASS_COUNT=$(tail -1 "$QUALITY_LOG" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || echo "?")
  echo "  make quality: $PASS_COUNT passed"
else
  echo "  make quality: FAILED — check $QUALITY_LOG"
fi

# === Full test suite (optional) ===
if [ "$SKIP_FULL" = false ] && [ "$COMPLETED" -gt 0 ]; then
  echo ""
  echo "=== Full Test Suite ==="
  FULL_LOG="$LOGDIR/run-all-tests.log"
  echo "  Running ./run_all_tests.sh (this takes ~5 minutes)..."

  if ./run_all_tests.sh > "$FULL_LOG" 2>&1; then
    echo "  Full suite: PASSED"
  else
    echo "  Full suite: FAILED (check $FULL_LOG)"
  fi

  # Parse JSON reports
  LATEST_REPORT=$(ls -td test-results/*/ 2>/dev/null | head -1)
  if [ -n "$LATEST_REPORT" ]; then
    echo "  Reports: $LATEST_REPORT"
    for f in "$LATEST_REPORT"*.json; do
      SUITE=$(basename "$f" .json)
      PASSED=$(python3 -c "import json; d=json.load(open('$f')); print(d.get('summary',{}).get('passed',0))" 2>/dev/null || echo "?")
      SUITE_FAILED=$(python3 -c "import json; d=json.load(open('$f')); print(d.get('summary',{}).get('failed',0))" 2>/dev/null || echo "?")
      echo "    $SUITE: $PASSED passed, $SUITE_FAILED failed"
    done
  fi
fi

# === Report ===
echo ""
echo "=== Pipeline Results ==="
echo "Completed: $COMPLETED / $TOTAL"
echo "Failed:    $FAILED"
echo "Logs:      $LOGDIR"
echo "Finished:  $(date)"

if [ "$FAILED" -gt 0 ]; then
  echo ""
  echo "Failed tasks (check logs):"
  for LOG in "$LOGDIR"/task-*.log; do
    TASK=$(basename "$LOG" .log | sed 's/task-//')
    if ! grep -q "bd close" "$LOG" 2>/dev/null; then
      echo "  - $TASK"
    fi
  done
fi

echo ""
echo "=== Pipeline complete ==="
