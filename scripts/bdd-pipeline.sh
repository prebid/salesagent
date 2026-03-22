#!/bin/bash
set -uo pipefail
# Note: NOT using set -e — we handle errors explicitly per-command.
# pipefail is kept for safety but individual pipes use || true where needed.

# =============================================================================
# bdd-pipeline.sh — Process BDD step wiring tasks via claude -p
#
# Three-phase cycle per batch:
#   1. EXECUTE: claude -p runs each task (fresh context, zero cognitive overhead)
#   2. TEST:    ./run_all_tests.sh produces JSON reports
#   3. EVALUATE: claude -p reads test results and decides continue/stop/fix
#
# The evaluator agent is the key — it reads test JSON, compares against
# baseline, diagnoses failures, and can create fix tasks or halt the pipeline.
#
# Usage:
#   ./scripts/bdd-pipeline.sh --epic 9vgz --uc UC-002       # UC-002 tasks
#   ./scripts/bdd-pipeline.sh --epic 9vgz --batch 5          # 5 tasks per cycle
#   ./scripts/bdd-pipeline.sh --epic 9vgz --dry-run          # show plan only
#   ./scripts/bdd-pipeline.sh 9vgz.1 9vgz.2 9vgz.3          # explicit tasks
#   ./scripts/bdd-pipeline.sh --epic 9vgz --quick            # make quality instead of full suite
# =============================================================================

# === Parse flags ===
BATCH_SIZE=5
DRY_RUN=false
EPIC=""
UC_FILTER=""
QUICK_MODE=false
TASKS=()

while [[ $# -gt 0 ]]; do
  case $1 in
    --epic)    EPIC="$2"; shift 2 ;;
    --uc)      UC_FILTER="$2"; shift 2 ;;
    --batch)   BATCH_SIZE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --quick)   QUICK_MODE=true; shift ;;
    -*)        echo "Unknown flag: $1"; exit 1 ;;
    *)         TASKS+=("$1"); shift ;;
  esac
done

# === Setup ===
CLAUDE="claude -p --dangerously-skip-permissions"
LOGDIR="./logs/bdd-pipeline-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOGDIR"

# Baseline from Phase 1 gate (established 2026-03-22)
BASELINE_UNIT=4077
BASELINE_BDD_PASSED=1460

GIT_INSTRUCTION="IMPORTANT: Do NOT run git push or bd sync. The pipeline handles git coordination. DO commit your changes with a descriptive message."

# === Discover tasks from epic if none specified ===
if [ ${#TASKS[@]} -eq 0 ] && [ -n "$EPIC" ]; then
  echo "Discovering tasks from epic salesagent-$EPIC..."
  TASK_LIST=$(bd show "$EPIC" 2>/dev/null | grep "↳ ○" | awk '{print $3}' | sed 's/://' || true)

  for TASK_ID in $TASK_LIST; do
    TASK_ID="${TASK_ID#salesagent-}"
    if [ -n "$UC_FILTER" ]; then
      TITLE=$(bd show "$TASK_ID" 2>/dev/null | head -1 || true)
      if echo "$TITLE" | grep -qi "$UC_FILTER" 2>/dev/null; then
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
echo "Tasks ($TOTAL):  ${TASKS[*]}"
echo "Batch size:      $BATCH_SIZE"
echo "Test mode:       $([ "$QUICK_MODE" = true ] && echo 'make quality' || echo './run_all_tests.sh')"
echo "Baseline:        unit=$BASELINE_UNIT bdd=$BASELINE_BDD_PASSED"
echo "Logs:            $LOGDIR"
echo "Started:         $(date)"
echo ""

if [ "$DRY_RUN" = true ]; then
  echo "DRY RUN — would process $TOTAL tasks:"
  for T in "${TASKS[@]}"; do
    TITLE=$(bd show "$T" 2>/dev/null | head -1 | sed 's/^[^·]*· //' | sed 's/ *\[.*//' || true)
    echo "  - $T: ${TITLE:-???}"
  done
  exit 0
fi

# === Main loop: batch → test → evaluate ===
TASK_INDEX=0
TOTAL_COMPLETED=0
TOTAL_FAILED=0
PIPELINE_HALT=false

while [ "$TASK_INDEX" -lt "$TOTAL" ] && [ "$PIPELINE_HALT" = false ]; do

  # --- Phase 1: EXECUTE batch ---
  BATCH_END=$(( TASK_INDEX + BATCH_SIZE ))
  [ "$BATCH_END" -gt "$TOTAL" ] && BATCH_END=$TOTAL
  BATCH_NUM=$(( TASK_INDEX / BATCH_SIZE + 1 ))
  BATCH_COMPLETED=0
  BATCH_FAILED=0

  echo "══════════════════════════════════════════════════════════════"
  echo "  BATCH $BATCH_NUM: tasks $(( TASK_INDEX + 1 ))-$BATCH_END of $TOTAL"
  echo "══════════════════════════════════════════════════════════════"

  while [ "$TASK_INDEX" -lt "$BATCH_END" ]; do
    TASK_ID="${TASKS[$TASK_INDEX]}"
    LOG="$LOGDIR/task-$TASK_ID.log"
    TITLE=$(bd show "$TASK_ID" 2>/dev/null | head -1 | sed 's/^[^·]*· //' | sed 's/ *\[.*//' || true)

    # Skip already-closed tasks
    ALREADY_CLOSED=$(bd show "$TASK_ID" 2>/dev/null | head -1 | grep -o "CLOSED" || true)
    if [ "$ALREADY_CLOSED" = "CLOSED" ]; then
      echo ""
      echo "[$(( TASK_INDEX + 1 ))/$TOTAL] $TASK_ID: ${TITLE:-???} [SKIP — already closed]"
      BATCH_COMPLETED=$((BATCH_COMPLETED + 1))
      TASK_INDEX=$((TASK_INDEX + 1))
      continue
    fi

    echo ""
    echo "[$(( TASK_INDEX + 1 ))/$TOTAL] $TASK_ID: ${TITLE:-???}"

    $CLAUDE "/dev-practices:execute $TASK_ID

Context: BDD step wiring task. Wire step definitions for the scenarios in the task title. Use existing harness infrastructure (MediaBuyCreateEnv, MediaBuyUpdateEnv, MediaBuyListEnv). Follow patterns in:
- tests/bdd/steps/generic/given_media_buy.py
- tests/bdd/steps/generic/then_media_buy.py
- tests/bdd/steps/domain/uc002_create_media_buy.py
- tests/bdd/steps/domain/uc003_update_media_buy.py
- tests/bdd/steps/domain/uc019_query_media_buys.py
- tests/bdd/steps/domain/uc026_package_media_buy.py

If production code doesn't implement expected behavior (spec-production gap), record the gap in task notes and move on. Goal is to wire steps that exercise real production code.

$GIT_INSTRUCTION" \
      > "$LOG" 2>&1 || true

    # Check if the beads task was actually closed (not log content — ask beads directly)
    TASK_STATUS=$(bd show "$TASK_ID" 2>/dev/null | head -1 | grep -o "CLOSED" || true)
    if [ "$TASK_STATUS" = "CLOSED" ]; then
      echo "  [done]"
      BATCH_COMPLETED=$((BATCH_COMPLETED + 1))
    else
      echo "  [FAIL] — check $LOG"
      BATCH_FAILED=$((BATCH_FAILED + 1))
    fi

    TASK_INDEX=$((TASK_INDEX + 1))
  done

  TOTAL_COMPLETED=$((TOTAL_COMPLETED + BATCH_COMPLETED))
  TOTAL_FAILED=$((TOTAL_FAILED + BATCH_FAILED))

  # --- Phase 2: TEST ---
  echo ""
  echo "── Testing after batch $BATCH_NUM ──"
  bd sync 2>&1 | tail -1 || true

  TEST_LOG="$LOGDIR/test-batch-$BATCH_NUM.log"

  if [ "$QUICK_MODE" = true ]; then
    echo "  Running make quality..."
    make quality > "$TEST_LOG" 2>&1 || true
  else
    echo "  Running ./run_all_tests.sh (takes ~5 min)..."
    ./run_all_tests.sh > "$TEST_LOG" 2>&1 || true
  fi

  # Collect test results for evaluator
  RESULTS_SUMMARY="$LOGDIR/results-batch-$BATCH_NUM.txt"

  if [ "$QUICK_MODE" = true ]; then
    # Extract unit test counts from make quality output
    UNIT_LINE=$(grep -E "passed|failed" "$TEST_LOG" | tail -1 || echo "unknown")
    echo "make quality: $UNIT_LINE" > "$RESULTS_SUMMARY"
  else
    # Parse JSON test reports
    LATEST_REPORT=$(ls -td test-results/*/ 2>/dev/null | head -1)
    if [ -n "$LATEST_REPORT" ]; then
      echo "Test results from $LATEST_REPORT:" > "$RESULTS_SUMMARY"
      for f in "$LATEST_REPORT"*.json; do
        SUITE=$(basename "$f" .json)
        python3 -c "
import json
d = json.load(open('$f'))
s = d.get('summary', {})
print(f'  {\"$SUITE\"}: passed={s.get(\"passed\",0)} failed={s.get(\"failed\",0)} xfailed={s.get(\"xfailed\",0)} xpassed={s.get(\"xpassed\",0)}')
" >> "$RESULTS_SUMMARY" 2>/dev/null
      done

      # BDD detailed breakdown
      BDD_REPORT="$LATEST_REPORT/bdd.json"
      if [ -f "$BDD_REPORT" ]; then
        python3 -c "
import json
from collections import Counter
d = json.load(open('$BDD_REPORT'))
outcomes = Counter(t['outcome'] for t in d.get('tests', []))
total_passing = outcomes.get('passed', 0) + outcomes.get('xpassed', 0)
print(f'  BDD total passing: {total_passing} (baseline: $BASELINE_BDD_PASSED)')
print(f'  BDD delta: +{total_passing - $BASELINE_BDD_PASSED}')

# Failures by UC
fails = [t for t in d.get('tests', []) if t['outcome'] == 'failed']
uc_fails = Counter()
for t in fails:
    n = t['nodeid']
    if 'uc002' in n: uc_fails['UC-002'] += 1
    elif 'uc003' in n: uc_fails['UC-003'] += 1
    elif 'uc019' in n: uc_fails['UC-019'] += 1
    elif 'uc026' in n: uc_fails['UC-026'] += 1
    else: uc_fails['other'] += 1
if uc_fails:
    print('  BDD failures by UC:')
    for uc, c in sorted(uc_fails.items()):
        print(f'    {uc}: {c}')
" >> "$RESULTS_SUMMARY" 2>/dev/null
      fi
    fi
  fi

  cat "$RESULTS_SUMMARY"

  # --- Phase 3: EVALUATE ---
  echo ""
  echo "── Evaluating batch $BATCH_NUM ──"

  EVAL_LOG="$LOGDIR/eval-batch-$BATCH_NUM.log"
  EVAL_VERDICT="$LOGDIR/verdict-batch-$BATCH_NUM.txt"

  $CLAUDE "You are the pipeline evaluator. Read the test results and decide whether to CONTINUE or STOP.

## Test Results
$(cat "$RESULTS_SUMMARY")

## Batch Info
- Batch $BATCH_NUM: $BATCH_COMPLETED completed, $BATCH_FAILED failed (of $BATCH_SIZE)
- Total progress: $TOTAL_COMPLETED/$TOTAL completed, $TOTAL_FAILED failed
- Remaining: $(( TOTAL - TASK_INDEX )) tasks
- Baseline: unit=$BASELINE_UNIT, bdd_passing=$BASELINE_BDD_PASSED

## Decision Rules
1. If unit tests regressed (fewer than $BASELINE_UNIT passed) → STOP
2. If BDD passing count decreased from baseline → STOP (regression)
3. If BDD passing count increased or stayed same → CONTINUE (progress)
4. If make quality failed on formatting/lint → STOP (needs fix before more changes)
5. If all batch tasks failed → STOP (systemic issue)
6. If 1-2 batch tasks failed but others succeeded → CONTINUE (isolated issues)

## Output Format
Write EXACTLY one line to stdout — either:
  CONTINUE: <brief reason>
  STOP: <brief reason explaining what needs fixing>

Nothing else. No markdown, no explanation. Just the verdict line." \
    > "$EVAL_LOG" 2>&1 || true

  # Extract verdict (last non-empty line that starts with CONTINUE or STOP)
  VERDICT=$(grep -E "^(CONTINUE|STOP):" "$EVAL_LOG" 2>/dev/null | tail -1 || echo "STOP: evaluator produced no verdict")
  echo "$VERDICT" > "$EVAL_VERDICT"
  echo "  Verdict: $VERDICT"

  if echo "$VERDICT" | grep -q "^STOP"; then
    echo ""
    echo "  *** Pipeline halted by evaluator ***"
    echo "  Review: $EVAL_LOG"
    echo ""
    echo "  Resume with remaining tasks:"
    REMAINING=("${TASKS[@]:$TASK_INDEX}")
    echo "  ./scripts/bdd-pipeline.sh ${REMAINING[*]}"
    PIPELINE_HALT=true
  fi

  echo ""
done

# === Final report ===
echo "══════════════════════════════════════════════════════════════"
echo "  PIPELINE RESULTS"
echo "══════════════════════════════════════════════════════════════"
echo "Completed: $TOTAL_COMPLETED / $TOTAL"
echo "Failed:    $TOTAL_FAILED"
echo "Halted:    $PIPELINE_HALT"
echo "Logs:      $LOGDIR"
echo "Finished:  $(date)"

if [ "$TOTAL_FAILED" -gt 0 ]; then
  echo ""
  echo "Failed tasks:"
  for LOG in "$LOGDIR"/task-*.log; do
    TASK=$(basename "$LOG" .log | sed 's/task-//')
    TSTATUS=$(bd show "$TASK" 2>/dev/null | head -1 | grep -o "CLOSED" || true)
    if [ "$TSTATUS" != "CLOSED" ]; then
      echo "  - $TASK → $LOG"
    fi
  done
fi

# Print all verdicts
echo ""
echo "Batch verdicts:"
for V in "$LOGDIR"/verdict-*.txt; do
  [ -f "$V" ] && echo "  $(basename "$V" .txt): $(cat "$V")"
done

echo ""
echo "=== Pipeline complete ==="
