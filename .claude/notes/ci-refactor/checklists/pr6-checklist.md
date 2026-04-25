# PR 6 — Image supply chain (drafted by prior agent; optional)

## Checklist

```
[ ] Pre-flight TTL guard
[ ] git checkout -b ci/ci-refactor-pr6-image-supply-chain

Commits:

[ ] 1. ci(security): add harden-runner in audit mode to all workflows
       Each workflow gets step-security/harden-runner@<SHA> as the first step with egress-policy: audit.
       Soak for 1 week before commit 2.

[ ] 2. ci(security): flip harden-runner to block mode + allowed-endpoints
       Read week-1 audit logs (Settings → Security → Runner allowed lists), build allowlist, set egress-policy: block.

[ ] 3. ci(release): cosign keyless signing via OIDC
       Files: .github/workflows/release-please.yml (extend) — add cosign-installer + sign step
              with id-token: write permission scope.

[ ] 4. ci(security): dependency-review-action as gating check
       Files: new .github/workflows/dependency-review.yml + dependency-review-config.yml
              fail-on-severity: moderate, label: 'license-review'

[ ] 5. ci(codeql): flip continue-on-error: true → false (per D10 tripwire)
       Only if end-of-Week-4 CodeQL finding count ≤ 5; else extend advisory or accept indefinite.

[ ] 6. ci(release): SLSA provenance mode=max
       Files: extend release workflow with slsa-framework/slsa-github-generator@<SHA>

[ ] 7. ops: repo settings hygiene
       Operator runs gh api PATCH /repos/prebid/salesagent with: has_wiki=false (if unused),
         allow_squash_merge=true, allow_merge_commit=false, delete_branch_on_merge=true.
       (Agent does NOT run; admin-only.)

[ ] 8. test: optional pytest-benchmark in CI / Coverage
       Files: .github/workflows/ci.yml — extend coverage job with pytest-benchmark store-and-compare.

After: make quality + ./run_all_tests.sh
Post-merge: re-run OpenSSF Scorecard (target 8.5+). Close any harden-runner egress findings.
```
