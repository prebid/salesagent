# PR 3 Phase C — Cleanup (delete legacy test.yml)

## Checklist

```
[ ] Pre-flight TTL guard
[ ] Verify Phase B stable ≥48h:
    gh run list --workflow=ci.yml --branch=main --limit=3 --json conclusion \
      --jq '[.[].conclusion] | all(. == "success")'  # true

[ ] git checkout -b chore/ci-refactor-pr3-phase-c-cleanup

[ ] 1. chore(ci): delete legacy test.yml workflow
       File: rm .github/workflows/test.yml
       Verify: ! test -f .github/workflows/test.yml && \
               gh run list --workflow=ci.yml --branch=main --limit=3 --json conclusion \
                 --jq '[.[].conclusion] | all(. == "success")'

[ ] 2. docs: update ci-pipeline.md (current state section only — PR 4 owns full rewrite)
       File: docs/development/ci-pipeline.md (point "current state" at ci.yml)

[ ] make quality
[ ] bash .claude/notes/ci-refactor/scripts/verify-pr3-phase-c.sh

Post-merge:
- Update 00-MASTER-INDEX.md: PR 3 → "merged"
- Issue #1234 comment listing closed PDs
- Wait ≥48h before opening PR 4 (no race with hook deletions)
```
