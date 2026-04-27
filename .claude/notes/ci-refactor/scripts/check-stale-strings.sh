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
# **Round 14 extension** — added 18 new patterns covering A-list/P-list ranges,
# rule-count drift (19/20/21), harden-runner v2.16+ → v2.19.0+ floor, setup-uv
# version drift (v1-v7), calendar drift (5 weeks), engineer-day estimates across
# rounds, and the broken `\b~73 final\b` regex (`~` is a non-word character so
# `\b` boundary fails) — replaced with a literal `~73 final` pattern.
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
  '\b11 jobs\b'                     # superseded by "14 jobs" (D30)
  '\b11 internal commits\b'         # superseded by "10" (Commit 10 moved to PR 3 per Round 5/6)
  '\b11 commits\b'                  # same
  '\b33 effective\b'                # superseded by "36 effective" (D27 revision)
  '\b33−13−9−1\b'                   # superseded by "36−13−10−1=12" (D27 revision)
  '\b33-13-9-1\b'                   # ASCII variant of the same supersession
  '\b9 to pre-push\b'               # superseded by "10 to pre-push" (D27 + D3 mypy)
  '\b73-row\b'                      # superseded by "81 final" (D18 revised)
  '~73 final'                       # same — note: literal, NOT \b-delimited (~ is non-word, \b fails)
  '\bv2\.0 contributes 31 architecture\b'  # superseded by "27 architecture" (D18 drift-corrected Round 8)
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
  '\bA1-A10\b'                      # superseded by A1-A25 (Round 14 — A26 deleted in agent-team reframe; A25 is current)
  '\bA1-A14\b'                      # same
  '\bA1-A22\b'                      # same
  '\bA1-A23\b'                      # same
  '\bA1-A24\b'                      # same
  '\bA1-A26\b'                      # same — superseded by A1-A25 (Round 14 — A26 deleted)
  '\bP1-P6\b'                       # superseded by P1-P10 (Round 14 P10 hook-baseline added; P10 is current)
  '\bP1-P7\b'                       # same
  '\bP1-P8\b'                       # same
  '\bP1-P9\b'                       # same
  '18 rules'                        # superseded by 19 rules (Rule 19 added; current count may be 21+)
  '\b19 rules\b'                    # superseded by current count (rules continue to evolve)
  '\b20 rules\b'                    # same
  '\b21 rules\b'                    # same
  'harden-runner v2\.16\+'          # superseded by v2.19.0+ floor (Round 13 boss-level review)
  'harden-runner v2\.16\.0\+'       # same
  'v2\.16\.0\+ for CVE-2025-32955'  # CVE attribution corrected to GHSA-46g3-37rh-v698 + GHSA-g699-3x6g-wm3g (Round 13)
  'astral-sh/setup-uv@v[1-7]\b'     # superseded by v8.x (Round 13 corpus-wide bump)
  '\b5 weeks\b'                     # calendar superseded by 6 weeks part-time
  '\b5-week\b'                      # same
  '\b15-19 engineer-days\b'         # superseded by 20.25-24.5 engineer-days (Round 13 final)
  '\b16\.5-20 engineer-days\b'      # same
  '\b18\.5-22 engineer-days\b'     # same — hidden-scope cell sum, not headline
  '\b19-23 engineer-days\b'         # same
  '\b19\.5-23\.5 engineer-days\b'   # same
  '\b19\.75-23\.75 engineer-days\b' # same
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
