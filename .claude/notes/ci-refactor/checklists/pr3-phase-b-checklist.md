# PR 3 Phase B — Atomic flip (admin only, NO PR)

**PRE-REQUISITE (per A24):** Phase B must be dry-run on a sandbox repo (fork, throwaway org) BEFORE executing on production main. The dry-run records:
- Snapshot capture works
- PATCH succeeds with the 14-context body
- Inverse PATCH (rollback) succeeds with the snapshot
- Idempotency check works on second run

Operator confirms dry-run completion via `[ ]` in pre-flight A24 checklist BEFORE STEP 1 below.

**PRE-REQUISITE (per A25 — BLOCKER):** Hardware MFA confirmed on @chrishuie + SPOF acceptance documented in ADR-002. Without A25 completed in pre-flight sign-off, R20 + R30 (CRITICAL severity) attack chains have no mitigation in place. Verify the `[ ]` for A25 in `01-pre-flight-checklist.md` is checked before STEP 1 below.

**PRE-REQUISITE (per A20 — Round 11 R11C-07):** In-flight fork PRs snapshotted, coordination comments posted to each. Without A20, fork PRs will show "expected — waiting for status" indefinitely post-flip because `gh workflow run --ref refs/pull/<n>/head` returns 403 for fork branches. Verify `.claude/notes/ci-refactor/inflight-fork-prs-snapshot.json` exists AND each listed fork PR has a coordination comment from the maintainer.

**PRE-REQUISITE (per A22 — D45 enforcement):** Today is Mon-Thu AND tomorrow is NOT a US/EU federal holiday. Day-of-week is enforced by the flip script (`date +%u`); holiday-eve check is jurisdiction-bound and operator-verified — run the loop in pre-flight A22 manually before STEP 1.

**PRE-REQUISITE (per R39):** SHA-256 of `branch-protection-snapshot.json` recorded in pre-flight notes; re-verify before flip:
```bash
sha256sum .claude/notes/ci-refactor/branch-protection-snapshot.json
# Compare to value recorded at A1 capture time. If mismatch, snapshot was corrupted — DO NOT FLIP.
```

**PRE-REQUISITE (per A24 setup):** Repo must have a `phase-b-in-progress` label created (used by the script's mutex):
```bash
gh label create phase-b-in-progress --color FFA500 --description "Phase B branch-protection flip is currently being executed; do not run a second flip"
```

## Checklist

```
[ ] A24 dry-run completed on sandbox repo (snapshot → PATCH → rollback → idempotency check all green)

[ ] A24 dry-run evidence file on disk (closes A24 trust gap; checkbox alone is not sufficient):
    test -f .claude/notes/ci-refactor/escalations/phase-b-dry-run-evidence.md

[ ] A25 hardware MFA confirmed on @chrishuie + SPOF acceptance documented in ADR-002 (BLOCKER per pre-flight)

[ ] A20 in-flight fork PRs snapshotted (`.claude/notes/ci-refactor/inflight-fork-prs-snapshot.json`) and coordination comments posted to each fork PR (Round 11 R11C-07)

[ ] A22 holiday-eve check passed (script enforces day-of-week; holiday lookup is operator-run from pre-flight A22)

[ ] R39 SHA-256 of branch-protection-snapshot.json matches recorded value at A1 capture (corruption check)

[ ] Verify pre-flip snapshots exist (BOTH files; A1 captures full snapshot, extract derived from it):
    test -f .claude/notes/ci-refactor/branch-protection-snapshot.json && \
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
