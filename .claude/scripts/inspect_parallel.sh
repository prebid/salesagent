#!/usr/bin/env bash
# Parallel BDD step inspection across all domain step files.
#
# Usage:
#   .claude/scripts/inspect_parallel.sh [--output-dir DIR]
#
# Slices step files into groups, runs the v2 inspector on each in parallel,
# then merges results into a combined report.
#
# Output: one report per slice + combined summary in OUTPUT_DIR/
# Default OUTPUT_DIR: .claude/reports/inspect-parallel-$(date +%d%m%y_%H%M)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSPECTOR="/Users/konst/.claude/plugins/cache/agentic-toolkit/qa-bdd/0.2.0/skills/inspect-steps/scripts/inspect_bdd_steps.py"
FEATURES_DIR="$PROJECT_ROOT/tests/bdd/features"
STEPS_DIR="$PROJECT_ROOT/tests/bdd/steps"

# Parse args
OUTPUT_DIR="${1:-.claude/reports/inspect-parallel-$(date +%d%m%y_%H%M)}"
mkdir -p "$OUTPUT_DIR"

# Create patched inspector with timeout=600 and --then-only=False
PATCHED="/tmp/inspect_bdd_parallel.py"
sed 's/timeout=180/timeout=600/;s/"--then-only", action="store_true", default=True/"--then-only", action="store_true", default=False/' \
    "$INSPECTOR" > "$PATCHED"

echo "=== Parallel BDD Step Inspection ==="
echo "Inspector: $INSPECTOR"
echo "Features:  $FEATURES_DIR"
echo "Output:    $OUTPUT_DIR"
echo ""

# Define slices: name → list of step files
declare -A SLICES
SLICES[uc002-create]="$STEPS_DIR/domain/uc002_create_media_buy.py"
SLICES[uc003]="$STEPS_DIR/domain/uc003_update_media_buy.py $STEPS_DIR/domain/uc003_ext_error_scenarios.py"
SLICES[uc004]="$STEPS_DIR/domain/uc004_delivery.py"
SLICES[uc005-uc006]="$STEPS_DIR/domain/uc005_creative_formats.py $STEPS_DIR/domain/uc006_sync_creatives.py"
SLICES[uc011]="$STEPS_DIR/domain/uc011_accounts.py"
SLICES[uc019]="$STEPS_DIR/domain/uc019_query_media_buys.py"
SLICES[uc026]="$STEPS_DIR/domain/uc026_package_media_buy.py"
SLICES[generic]="$STEPS_DIR/domain/admin_accounts.py $STEPS_DIR/domain/uc002_nfr.py $STEPS_DIR/domain/uc002_task_query.py $(ls $STEPS_DIR/generic/*.py 2>/dev/null | grep -v __pycache__ | grep -v __init__ | tr '\n' ' ')"

# Prepare temp dirs and launch
PIDS=()
for name in "${!SLICES[@]}"; do
    slice_dir="/tmp/inspect-slice-$name"
    rm -rf "$slice_dir"
    mkdir -p "$slice_dir"

    # Copy files into slice dir
    for f in ${SLICES[$name]}; do
        [ -f "$f" ] && cp "$f" "$slice_dir/"
    done

    file_count=$(ls "$slice_dir"/*.py 2>/dev/null | wc -l | tr -d ' ')
    echo "Launching $name ($file_count files)..."

    python3 "$PATCHED" \
        --steps-dir "$slice_dir" \
        --features-dir "$FEATURES_DIR" \
        --output "$OUTPUT_DIR/$name.md" \
        --pass1-only \
        > "$OUTPUT_DIR/$name.log" 2>&1 &
    PIDS+=("$!:$name")
done

echo ""
echo "All ${#PIDS[@]} slices launched. Waiting..."
echo ""

# Wait for all and report
FAILED=0
for pid_name in "${PIDS[@]}"; do
    pid="${pid_name%%:*}"
    name="${pid_name##*:}"
    if wait "$pid"; then
        # Extract summary from log
        flagged=$(grep -o "[0-9]* flagged" "$OUTPUT_DIR/$name.log" 2>/dev/null | head -1 || echo "? flagged")
        total=$(grep -o "Found [0-9]* step" "$OUTPUT_DIR/$name.log" 2>/dev/null | head -1 || echo "? steps")
        echo "  ✓ $name: $total, $flagged → $OUTPUT_DIR/$name.md"
    else
        echo "  ✗ $name: FAILED (see $OUTPUT_DIR/$name.log)"
        FAILED=$((FAILED + 1))
    fi
done

# Generate combined summary
echo ""
echo "=== Combined Summary ==="
TOTAL_STEPS=0
TOTAL_FLAGGED=0
for name in "${!SLICES[@]}"; do
    if [ -f "$OUTPUT_DIR/$name.md" ]; then
        steps=$(grep "Steps scanned" "$OUTPUT_DIR/$name.md" 2>/dev/null | grep -o "[0-9]*" || echo 0)
        flagged=$(grep "Flagged for deep" "$OUTPUT_DIR/$name.md" 2>/dev/null | grep -o "[0-9]*" || echo 0)
        TOTAL_STEPS=$((TOTAL_STEPS + steps))
        TOTAL_FLAGGED=$((TOTAL_FLAGGED + flagged))
        echo "  $name: $steps steps, $flagged flagged"
    fi
done
echo ""
echo "Total: $TOTAL_STEPS steps inspected, $TOTAL_FLAGGED flagged"
echo "Reports: $OUTPUT_DIR/*.md"

if [ "$FAILED" -gt 0 ]; then
    echo "WARNING: $FAILED slices failed"
    exit 1
fi
