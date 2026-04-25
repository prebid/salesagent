### E3 — An allowlist hasn't shrunk in 6 months (D27 tripwire)


**Trigger**: monthly allowlist audit shows zero shrinkage for 6 months.
**Severity**: P3.
**Detection time**: monthly cadence.
**Affected PR(s)**: ongoing decay.

**Symptoms**
- `git log --since="6 months ago" -- tests/unit/test_architecture_*.py` shows additions but no deletions in allowlist arrays.
- FIXME comments in source files reference issue numbers without resolution dates.

**Verification**
```bash
for f in tests/unit/test_architecture_*.py; do
  added=$(git log --since="6 months ago" --numstat -- "$f" | awk '{added += $1} END {print added}')
  deleted=$(git log --since="6 months ago" --numstat -- "$f" | awk '{deleted += $2} END {print deleted}')
  printf "%-60s +%s -%s\n" "$f" "$added" "$deleted"
done
```

**Immediate response (first 15 min)**
1. Identify the 1-2 worst offenders.
2. Skim recent FIXMEs; are some genuinely fixable?

**Stabilization (next 1-4 hours)**
1. Schedule a focused fix-and-shrink sprint (1-2 days, dedicated).
2. **Do not add new entries during the sprint.**
3. Pick allowlists where FIXME comments reference active beads/GH issues — those have known fixes.

**Recovery (longer-term)**
- Consider ADR-004 ("retirement criteria for guards"): if a guard's allowlist hasn't shrunk in 12 months AND the guarded pattern is no longer producing new violations, retire the guard.

**Post-incident**
- Update CLAUDE.md guard table if any guards retire.
- Update D18 with new count.

**Why this happens (root cause)**
Allowlists are pre-existing-debt placeholders. They're meant to shrink. Without a forcing function, they don't.

**Related scenarios**
- See also: E2 (FP-driven growth — different direction), D18 (guard count).

---
