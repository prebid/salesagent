# PR4 — Mid-Commit Hook Deletion Failure Recovery

> Runbook for the scenario: PR 4 commit 5 (the hook-deletion sweep) is in progress;
> `make quality` fails after partial deletion (e.g., hooks 1-7 of 13 deleted, hook 8 deletion
> exposes a regression). Goal: restore to known-good state, identify root cause, resume safely.

## Symptom

- Hooks 1-N of 13 deleted in `.pre-commit-config.yaml`
- `make quality` (or `pre-commit run --all-files`) fails on a structural-guard test
  that the deletion sweep did NOT explicitly account for
- Working tree contains partial deletions; subsequent commits compound the breakage

## Recovery procedure

### 1. Capture pre-recovery state

```bash
git log --oneline -5 > /tmp/pr4-recovery-pre-state.txt
git diff main..HEAD .pre-commit-config.yaml > /tmp/pr4-precommit-partial-diff.txt
make quality 2>&1 | tee /tmp/pr4-recovery-failure.log
```

These artifacts are the diagnostic record for the escalation file (if needed in step 4).

### 2. Restore to known-good state

```bash
# Restore the entire .pre-commit-config.yaml to the parent of the deletion sweep.
# Adjust HEAD~N to point to the commit BEFORE the deletion sweep started.
git checkout HEAD~1 -- .pre-commit-config.yaml

pre-commit clean
pre-commit install --hook-type pre-commit --hook-type pre-push
make quality
```

Expected: `make quality` passes (this is the known-good baseline).

### 3. Identify root cause

Examine `/tmp/pr4-recovery-failure.log` to identify which structural guard fired. Common patterns:

- Hook deletion exposed a structural-guard test that was failing latently (e.g., guard's
  ratcheting baseline is set lower than current violation count)
- Hook deletion removed a check that the structural guard depends on (rare)
- Hook deletion's commit ordering violated R7 (guards before deletions)
- A v2.0 phase PR landed during the sweep and its `.pre-commit-config.yaml` edits
  re-introduced or removed a hook the sweep already accounted for (D27 + v2.0 collision note
  in the decision log)

### 4. Decide path forward

**Option A**: identify the specific hook whose deletion triggered the failure; defer that
hook's deletion to a follow-up PR. Re-run the deletion sweep with that hook excluded.

**Option B**: if root cause is a structural guard's baseline being stale, file a fast-track
PR to update the baseline (per ADR-006), then resume PR 4.

**Option C**: escalate to user. Write `escalations/pr4-deletion-failure.md` with the
diagnostic artifacts from step 1; STOP per executor-prompt.md Rule 17 (terminal message
must be the single line `ESCALATION: see escalations/pr4-deletion-failure.md`).

### 5. Resume the deletion sweep

```bash
git checkout pr4-deletion-sweep  # resume the feature branch
# apply the corrected sweep (hooks 1-K deleted, where K excludes the problematic hook)
make quality
git commit -m "refactor(precommit): delete absorbed-by-guard hooks (sweep K of 13)"
```

## Tripwire

If recovery requires more than 2 hook re-deletions in sequence, the sweep design itself
is wrong. STOP and re-architect: the structural-guard coverage is incomplete, OR D27's
"36 effective − 13 − 10 − 1 = 12" math has been invalidated by an interleaved v2.0 PR.

## References

- D27 (decision log) — hook reallocation math (real baseline 36 effective commit-stage)
- R7 (risk register) — `make quality` regression after hook deletion
- ADR-006 — allowlist pattern (relevant if root cause is a stale baseline)
- executor-prompt.md Rule 14 — pre-flight is the rollback contract
- executor-prompt.md Rule 17 — escalation terminal message rule
- runbooks/G3-pr4-revert-interdependent.md — for post-merge revert (different scenario)
