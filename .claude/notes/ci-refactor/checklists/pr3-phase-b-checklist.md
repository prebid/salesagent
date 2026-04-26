# PR 3 Phase B — Atomic flip (admin only, NO PR)

**PRE-REQUISITE (per A24):** Phase B must be dry-run on a sandbox repo (fork, throwaway org) BEFORE executing on production main. The dry-run records:
- Snapshot capture works
- PATCH succeeds with the 14-context body
- Inverse PATCH (rollback) succeeds with the snapshot
- Idempotency check works on second run

Operator confirms dry-run completion via `[ ]` in pre-flight A24 checklist BEFORE STEP 1 below.

**PRE-REQUISITE (per A24 setup):** Repo must have a `phase-b-in-progress` label created (used by the script's mutex):
```bash
gh label create phase-b-in-progress --color FFA500 --description "Phase B branch-protection flip is currently being executed; do not run a second flip"
```

## Checklist

```
[ ] A24 dry-run completed on sandbox repo (snapshot → PATCH → rollback → idempotency check all green)

[ ] Verify pre-flip snapshot exists:
    test -f .claude/notes/ci-refactor/branch-protection-snapshot-required-checks.json
    [[ -s .claude/notes/ci-refactor/branch-protection-snapshot-required-checks.json ]]

[ ] Verify Phase A is on main and stable ≥48h:
    gh run list --workflow=ci.yml --branch=main --limit=10 --json conclusion,createdAt \
      --jq '[.[] | select(.conclusion == "success")] | length'  # ≥3
    gh pr list --state merged --limit 5 --json number,checkSuites  # confirm new check names appeared

[ ] STEP 2 — Atomic flip:
    bash .claude/notes/ci-refactor/scripts/flip-branch-protection.sh
    Token: classic PAT with `repo` OR fine-grained PAT with Administration:write.
    Script confirms idempotency, captures pre-flip snapshot to `branch-protection-snapshot-pre-flip.json`,
    prompts for "FLIP" confirmation, and emits the canonical 14-context PATCH (D17 amended by D30).

    **DO NOT** manually copy a JSON body. The script is the single source of truth for the 14 contexts.

[ ] STEP 3 — Verify (script does this internally):
    Script's final `diff /tmp/protected-now /tmp/expected-now` MUST exit 0.

[ ] STEP 4 — Open trivial PR (e.g., comment-only) to validate:
    git checkout -b chore/phase-b-validation
    echo "" >> CONTRIBUTING.md
    git commit -am "chore: phase B validation no-op"
    # User pushes & opens PR; observe ALL 14 check names show as required.

If script fails at any step — IMMEDIATE ROLLBACK:
gh api -X PATCH /repos/prebid/salesagent/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  --input branch-protection-snapshot-pre-flip.json
Recovery: <5 minutes. Investigate, then retry the flip.

Post-flip:
- Update 00-MASTER-INDEX.md: Phase B → "merged"
- Wait ≥48h for stability before Phase C
- Comment on issue #1234: "Phase B atomic flip complete; old check names no longer required"
```
