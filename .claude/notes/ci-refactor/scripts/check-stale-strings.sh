#!/usr/bin/env bash
# Pre-flight P9 — stale-string drift guard (per D46, Round 12).
#
# Each sweep round adds new content to per-PR specs. Historically the propagation
# across non-spec surfaces (verify scripts, briefings, executor template, admin
# scripts, architecture.md) trails by 1-2 rounds. This script catches stale-string
# patterns like "11 frozen", "D1-D28", "R1-R10" outside explicitly allowlisted
# audit-trail files. Run before declaring any sweep round complete.
#
# Exit 0: corpus is clean of propagation drift across production-facing surfaces.
# Exit 1: stale strings found; fix before declaring the sweep complete.
set -euo pipefail

CORPUS=".claude/notes/ci-refactor"

# Patterns that are stale (superseded by current canonical state) when found
# OUTSIDE the allowlist. Add new patterns as decisions evolve.
declare -a PATTERNS=(
  '11 frozen check names'           # superseded by 14 (D17 amended by D30)
  '11 frozen names'                 # same
  '\b11 frozen\b'                   # same
  '\bD1-D28\b'                      # superseded by D1-D46 (Round 12)
  '\bD1-D38\b'                      # superseded by D1-D46 (Round 11/12 intermediate)
  '\bD1-D45\b'                      # superseded by D1-D46 (Round 12)
  '\bR1-R10\b'                      # superseded by R1-R43 (Round 12)
  '\bR1-R37\b'                      # superseded by R1-R43 (Round 11 intermediate)
  '\bR1-R42\b'                      # superseded by R1-R43 (Round 12 intermediate)
  '18 rules'                        # superseded by 19 rules (Round 9 added Rule 19)
  'promoted to standalone drafts'   # ADR-001/002/003 are inline per drafts/README.md
)

# Files / paths explicitly allowlisted as audit-trail / history-marker / pattern-definition.
# These intentionally retain older language for historical accuracy or pattern-naming.
declare -a ALLOWLIST=(
  "${CORPUS}/RESUME-HERE.md"          # has sweep audit-trail sections
  "${CORPUS}/architecture.md"          # banner declares stale; forwards to D30
  "${CORPUS}/REFACTOR-RUNBOOK.md"     # superseded; kept as audit trail
  "${CORPUS}/03-decision-log.md"      # change-log entries may cite older counts; D17/D30 explicit reference
  "${CORPUS}/01-pre-flight-checklist.md"  # P9 description names the pattern strings
  "${CORPUS}/EXECUTIVE-SUMMARY.md"    # D17/D46 entries reference the patterns by name
  "${CORPUS}/00-MASTER-INDEX.md"      # round-sweep summaries cite older counts in deltas
  "${CORPUS}/research/"               # read-only audit trail
  "${CORPUS}/scripts/check-stale-strings.sh"  # this script's own pattern list
)

# Build a -path-not exclusion for grep
EXCLUDES=()
for p in "${ALLOWLIST[@]}"; do
  EXCLUDES+=(--exclude-dir="$(basename "$p")")
done

EXIT=0
for pattern in "${PATTERNS[@]}"; do
  # grep for pattern; filter out allowlisted files via Python loop (cleaner than nested
  # --exclude paths since some allowlist entries are files not dirs)
  hits=$(grep -RnE "$pattern" "$CORPUS" 2>/dev/null || true)
  filtered=""
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    file="${line%%:*}"
    skip=0
    for allow in "${ALLOWLIST[@]}"; do
      if [[ "$file" == "$allow"* ]]; then
        skip=1
        break
      fi
    done
    [[ "$skip" == "0" ]] && filtered+="${line}"$'\n'
  done <<< "$hits"

  if [[ -n "${filtered// /}" ]]; then
    echo "STALE PATTERN FOUND: $pattern" >&2
    echo "$filtered" >&2
    echo "" >&2
    EXIT=1
  fi
done

if [[ "$EXIT" == "0" ]]; then
  echo "OK: corpus clean of stale-string drift (D46 / P9 — propagation discipline)."
else
  echo "" >&2
  echo "Stale strings found in production-facing files (non-allowlisted)." >&2
  echo "Update each occurrence to current canonical state before declaring the sweep round complete." >&2
  echo "Allowlist (files where stale strings are intentional audit-trail):" >&2
  for a in "${ALLOWLIST[@]}"; do echo "  - $a" >&2; done
fi

exit "$EXIT"
