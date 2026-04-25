# PR 3 Phase B — Atomic flip (admin only, NO PR)

## Checklist

```
[ ] Verify pre-flip snapshot exists:
    test -f .claude/notes/ci-refactor/branch-protection-snapshot-required-checks.json
    [[ -s .claude/notes/ci-refactor/branch-protection-snapshot-required-checks.json ]]

[ ] Verify Phase A is on main and stable ≥48h:
    gh run list --workflow=ci.yml --branch=main --limit=10 --json conclusion,createdAt \
      --jq '[.[] | select(.conclusion == "success")] | length'  # ≥3
    gh pr list --state merged --limit 5 --json number,checkSuites  # confirm new check names appeared

[ ] STEP 2 — Atomic flip:
    gh api -X PATCH \
      /repos/prebid/salesagent/branches/main/protection/required_status_checks \
      -H "Accept: application/vnd.github+json" \
      --input - <<'EOF'
    {
      "strict": true,
      "checks": [
        {"context": "CI / Quality Gate"},
        {"context": "CI / Type Check"},
        {"context": "CI / Schema Contract"},
        {"context": "CI / Unit Tests"},
        {"context": "CI / Integration Tests"},
        {"context": "CI / E2E Tests"},
        {"context": "CI / Admin UI Tests"},
        {"context": "CI / BDD Tests"},
        {"context": "CI / Migration Roundtrip"},
        {"context": "CI / Coverage"},
        {"context": "CI / Summary"}
      ]
    }
    EOF
    Token: classic PAT with `repo` OR fine-grained PAT with Administration:write.
    `app_id` intentionally omitted — any GitHub App can satisfy.

[ ] STEP 3 — Verify (within 60 seconds of step 2):
    gh api repos/prebid/salesagent/branches/main/protection/required_status_checks \
      --jq '.contexts[]' | sort > /tmp/protected
    cat <<'EOF' | sort > /tmp/expected
    CI / Quality Gate
    CI / Type Check
    CI / Schema Contract
    CI / Unit Tests
    CI / Integration Tests
    CI / E2E Tests
    CI / Admin UI Tests
    CI / BDD Tests
    CI / Migration Roundtrip
    CI / Coverage
    CI / Summary
    EOF
    diff /tmp/protected /tmp/expected   # MUST be empty

[ ] STEP 4 — Open trivial PR (e.g., comment-only) to validate:
    git checkout -b chore/phase-b-validation
    echo "" >> CONTRIBUTING.md
    git commit -am "chore: phase B validation no-op"
    # User pushes & opens PR; observe all 11 check names show as required.

If any step fails — IMMEDIATE ROLLBACK:
gh api -X PATCH /repos/prebid/salesagent/branches/main/protection/required_status_checks \
  -H "Accept: application/vnd.github+json" \
  --input .claude/notes/ci-refactor/branch-protection-snapshot-required-checks.json
Recovery: <5 minutes. Investigate, then retry the flip.

Post-flip:
- Update 00-MASTER-INDEX.md: Phase B → "merged"
- Wait ≥48h for stability before Phase C
- Comment on issue #1234: "Phase B atomic flip complete; old check names no longer required"
```
