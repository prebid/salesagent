# Path 2 Fallback Briefing — v2.0 (PR #1221) lands first

**Authoring round:** Round 14 B3.
**Status:** Contingency document; activated only if D20 tripwire fires.

---

## 1. Trigger

This briefing activates when the **D20 sequencing tripwire** fires:

- **No v2.0 phase PR has merged into main** (`gh pr list --label v2.0 --base main --state merged` returns empty), AND
- **PR #1221 has not been broken into phase PRs** within **14 calendar days** from issue #1234 PR 1 review start.

If both conditions hold at day 14, the rollout is in Path-2 territory and Phase B should not proceed under Path-1 assumptions.

---

## 2. Decision criterion — operational definition of "blocked"

D20 selected Path 1 (issue #1234 lands first; v2.0 rebases) under the assumption that v2.0 phase PRs would land in a reasonable cadence. The operational definition of v2.0 being "blocked" — and therefore Path 2 being viable — is:

```bash
# Both conditions must hold:
gh api repos/prebid/salesagent/pulls/1221 --jq '.merged'
# returns: false

gh pr list --label v2.0 --base main --state merged
# returns: empty (no v2.0 phase PRs have landed into main)
```

If both checks return the "blocked" sentinel for **≥14 calendar days from PR 1 review start**, decision authority is **@chrishuie** (the user). Document the firing in an `escalations/D20-tripwire-<YYYYMMDD>.md` doc and consult this briefing for the per-PR impact assessment.

---

## 3. Impact on each PR (Path 2: v2.0 lands first)

If Path 2 activates, the issue #1234 PR series rebases onto a v2.0-merged main rather than the other way around. Per-PR impact:

### PR 1 — supply-chain hardening (zizmor + pinact + Scorecard + harden-runner)

**Unaffected.** Governance changes (CODEOWNERS, branch protection, required checks) and supply-chain hardening land cleanly on either base. ADR-001 / ADR-002 do not depend on v2.0 file layout. **No re-spec needed.**

### PR 2 — `uv.lock` single source of truth

**Reduced scope.** v2.0 already deletes `[project.optional-dependencies].dev` from `pyproject.toml`. The "delete the dev block" commit becomes "verify the block is absent" — a one-line confirmation rather than an edit.

**Impact:** ~30 LOC of PR 2's diff disappears. Recompute commit graph; verify-pr2.sh's "dev block absent" assertion still applies.

### PR 3 — CI authoritative (advisory→required flip)

**File-overlap matrix re-spec required.** The architecture-tests directory `tests/unit/architecture/` arrives on v2.0 with new files we don't currently account for in the file-overlap matrix (`00-MASTER-INDEX.md:54-64`). PR 3's required-checks list assumes a specific test inventory.

**Action items:**
- Re-derive the required-checks list against v2.0-merged main's `tests/unit/architecture/` inventory.
- Update `00-MASTER-INDEX.md` file-overlap matrix to reflect new overlaps.
- Update `verify-pr3.sh` if check names changed.

### PR 4 — pre-commit hook relocation (commit→pre-push)

**Hook deletion list double-counts test-migrations.** v2.0 already deletes the test-migration hooks that PR 4's deletion list includes. Re-deriving D27's math:

- v2.0-merged main's effective commit-stage count is **likely lower than 36** (estimate: 32-34, depending on which test-migration hooks already moved).
- PR 4's deletion list shrinks proportionally; the relocation list (10 hooks → pre-push) may be unaffected.
- The CLAUDE.md "Structural Guards" table targets ~81 entries directly without the rebase complexity Path-1 anticipates.

**Action items:**
- Re-run `capture-hook-baseline.sh` against v2.0-merged main; expect a different number than 36.
- Update D27's math derivation in `03-decision-log.md` to reflect the new baseline.
- `verify-pr4.sh` reads `.hook-baseline.txt`; the file's `expected_baseline` field needs the new number.

### PR 5 — version consolidation

**Unaffected.** Version-bumping is local to `pyproject.toml` / `uv.lock`; v2.0 doesn't change the version-string layout in a way that affects this PR.

### PR 6 — image supply chain (harden-runner audit→block)

**Possible release-please.yml conflicts.** v2.0 may modify release-please configuration; rebase the harden-runner audit→block flip carefully. Verify the emergency-revert workflow (Commit 3b) doesn't conflict with any v2.0 release automation.

**Action items:**
- Diff `.github/workflows/release-please.yml` against the Path-1 base; resolve any merge conflicts before merging Commit 4.
- Re-run the Commit 2.5 scratch-test (`escalations/harden-runner-revert-test-evidence.md`) against the rebased branch to confirm the revert workflow still functions.

---

## 4. Re-rebase cost

If Path 2 activates after PR 1 has already merged on a Path-1 base, **all subsequent issue #1234 PRs require rebase onto a 341-file v2.0-merged main**. This is a significantly harder rebase target than Path 1 anticipated:

- Mechanical conflicts in `pyproject.toml`, `.pre-commit-config.yaml`, and `tests/unit/architecture/`.
- File-overlap matrix invalidated; rebuild required.
- Verify-script assertions (verify-pr2/3/4.sh) may need updates if file paths or counts shifted.
- Branch-protection required-checks list re-derivation (see PR 3 above).

ADR-001 (DRY enforcement), D26 (uv.lock as authoritative source), D31 (release-please CI gate), D44 (propagation discipline) are all **unaffected** by which base lands first — they're invariants, not file paths.

**Estimated re-rebase cost:** ~1-2 days of focused work for an experienced contributor. Do NOT attempt this work without explicit go-ahead from @chrishuie.

---

## 5. What to NOT do

- **Don't try to land issue #1234 PRs out-of-order against an unmodified main.** Path 2 means v2.0 has merged; subsequent issue #1234 PRs MUST rebase onto v2.0-merged main, not skip it.
- **Don't deny v2.0 phase PRs without explicit decision.** Blocking v2.0 to preserve Path-1 sequencing exceeds the rollout's authority. The user decides Path 1 vs Path 2 if D20 fires.
- **Don't bundle Path-2 rebase work into the in-flight PR.** Rebases are separate commits; never amend.
- **Don't update D20's decision in `03-decision-log.md`.** If Path 2 activates, append a new decision (e.g., D45) recording the path switch with the trigger evidence, rather than mutating D20.

---

## 6. Decision authority

**@chrishuie (the user) decides Path 1 vs Path 2** if the D20 tripwire fires. This briefing exists to make the decision faster and more informed, not to pre-empt it.

When the tripwire fires:
1. Document the firing in `escalations/D20-tripwire-<YYYYMMDD>.md` with the `gh` query outputs as evidence.
2. Brief @chrishuie with this document and the per-PR impact summary.
3. Wait for explicit go-ahead before any rebase or path-switch action.

---

## References

- **`00-MASTER-INDEX.md:54-64`** — file-overlap matrix (Path-1 baseline)
- **D20** in `03-decision-log.md` — sequencing decision (selected Path 1)
- **`REBASE-PROTOCOL.md`** — rebase mechanics (assumes Path 1; needs Path-2 addendum if activated)
- **`runbooks/E4-account-lockout-recovery.md`** — lockout case (orthogonal but may co-occur)
