#!/usr/bin/env bash
# Pre-flight P9 — stale-string drift guard (per D46).
#
# **Wired to `make quality`** via Pre-flight P9 — also runnable manually before sweep-round close.
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
  '\b11 check names\b'              # superseded by "14 check names" (D30)
  '\b11 required checks\b'          # superseded by "14 required checks" (D30)
  '\b11 names\b'                    # superseded by "14 names" (D30)
  '\b33 effective\b'                # superseded by "36 effective" (D27 revision)
  '\b33−13−9−1\b'                   # superseded by "36−13−10−1=12" (D27 revision)
  '\b33-13-9-1\b'                   # ASCII variant of the same supersession
  '\b9 to pre-push\b'               # superseded by "10 to pre-push" (D27 + D3 mypy)
  '\b73-row\b'                      # superseded by "81 final" (D18 revised)
  '\b~73 final\b'                   # same
  '\b0\.11\.6\b'                    # superseded by uv 0.11.7
  '\bD1-D28\b'                      # superseded by current D-list (D46+)
  '\bD1-D38\b'                      # same
  '\bD1-D40\b'                      # superseded by D1-D48
  '\bD1-D44\b'                      # same
  '\bD1-D45\b'                      # superseded by D1-D46+ (D47/D48 added)
  '\bD1-D46\b'                      # superseded by D1-D48 (D47 + D48 added)
  '\bD1-D47\b'                      # superseded by D1-D48 (D48 added)
  '\bR1-R10\b'                      # superseded by R1-R47
  '\bR1-R37\b'                      # superseded by R1-R47
  '\bR1-R42\b'                      # superseded by R1-R47
  '\bR1-R43\b'                      # superseded by R1-R47 (R44 + R45/46/47 added)
  '\bR1-R44\b'                      # superseded by R1-R47 (R45/46/47 added)
  '18 rules'                        # superseded by 19 rules (Rule 19 added; current count may be 21+)
  'promoted to standalone drafts'   # ADR-001/002/003 are inline per drafts/README.md
)

# Files / paths explicitly allowlisted as audit-trail / history-marker / pattern-definition.
# These intentionally retain older language for historical accuracy or pattern-naming.
declare -a ALLOWLIST=(
  "${CORPUS}/RESUME-HERE.md"          # has sweep audit-trail sections
  "${CORPUS}/architecture.md"          # P1 deferred per RESUME-HERE; banner declares stale; forwards to D30
  "${CORPUS}/REFACTOR-RUNBOOK.md"     # superseded; kept as audit trail
  "${CORPUS}/03-decision-log.md"      # change-log + history-marker contexts; D17/D30 explicit reference
  "${CORPUS}/01-pre-flight-checklist.md"  # P9 description names the pattern strings
  "${CORPUS}/EXECUTIVE-SUMMARY.md"    # D-list and R-list rendering sections; D17/D46 entries reference patterns by name
  "${CORPUS}/00-MASTER-INDEX.md"      # round-sweep summaries cite older counts in deltas
  "${CORPUS}/02-risk-register.md"     # superseded entries documented as audit trail
  "${CORPUS}/research/"               # read-only audit trail
  "${CORPUS}/runbooks/B3-branch-protection-flip-422.md"  # error narrative contains pattern by reference
  "${CORPUS}/runbooks/B4-test-summary-needs-skipped.md"  # error narrative contains pattern by reference
  "${CORPUS}/scripts/check-stale-strings.sh"  # this script's own pattern list
)

# Allowlist filtering happens in the per-line loop below (more flexible than
# grep's --exclude flags since the allowlist mixes files and directory prefixes).
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
