# Pre-flight TTL Guard

## DELIVERABLE 4 — Pre-flight TTL guard (run FIRST in every PR)

This block is the **literal first step** in every PR's checklist. Paste verbatim:

```bash
# ─── Pre-flight freshness gate ──────────────────────────────────────────
NOTES=.claude/notes/ci-refactor
mtime() { stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null || echo 0; }
require_fresh() {
  local f="$1" ttl_days="$2" purpose="$3"
  local age=$(( $(date +%s) - $(mtime "$f") ))
  local limit=$(( ttl_days * 86400 ))
  if [[ ! -f "$f" ]]; then
    echo "MISSING: $f — $purpose"; return 1
  fi
  if (( age > limit )); then
    echo "STALE: $f is $((age/86400)) days old (>$ttl_days). Re-run pre-flight before continuing — $purpose"
    return 1
  fi
  echo "OK: $f ($((age/86400))d old)"
}

# Required for every PR
require_fresh "$NOTES/branch-protection-snapshot.json" 7 "pre-flight A1; rollback target for PR 3 Phase B" || exit 1

# Per-PR additions (uncomment the ones for THIS PR):
# PR 1:    require_fresh .zizmor-preflight.txt 7  "pre-flight P3; PR 1 commit 11" || exit 1
# PR 2:    require_fresh .mypy-baseline.txt 7    "pre-flight P2; PR 2 D13 measurement" || exit 1
# PR 3:    require_fresh "$NOTES/branch-protection-snapshot-required-checks.json" 7 "rollback target for Phase B flip" || exit 1
# PR 3:    require_fresh coverage.json 30        "pre-flight A7; .coverage-baseline calibration" || exit 1
echo "Pre-flight artifacts fresh — proceed."
```

If a check fails: STOP. Re-run the missing/stale pre-flight item from `01-pre-flight-checklist.md` (admin-only A* items must be run by `@chrishuie`; agent-runnable P* items may be re-captured by you).

---
