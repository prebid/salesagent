# PR 6 — Image supply chain (Week 6 follow-up; resolves D25)

## Checklist

Sequenced as TWO sub-PRs with ≥2-week separation. Sub-PR A first, Sub-PR B after audit-mode telemetry is captured.

### Sub-PR A — first PR (Week 6 commits 1, 2, 4, 5; optional 6, 7)

```
[ ] Pre-flight TTL guard (paste from .claude/notes/ci-refactor/preflight-ttl-guard.md)
[ ] git checkout -b ci/ci-refactor-pr6a-supply-chain-audit

Commits:

[ ] 1. ci(security): add harden-runner in audit mode to all workflows
       harden-runner@<SHA> v2.16.0+ as first step in every Ubuntu job.
       MUST use `disable-sudo-and-containers: true` (CVE-2025-32955).
       NOT `disable-sudo: true` — that flag is bypassable via Docker.
       egress-policy: audit (NOT block — audit-mode soak runs ≥2 weeks
       before Sub-PR B flips to block).

[ ] 2. ci(release): extend release-please.yml publish-docker with cosign + SBOM + provenance
       Files: .github/workflows/release-please.yml (MODIFY — do NOT create release.yml,
              the existing publish-docker already does multi-arch + Docker Hub).
       Add:  - sigstore/cosign-installer + cosign sign --yes loop over $TAGS
             - actions/attest-build-provenance@v2 with push-to-registry: true
             - sbom: true and provenance: mode=max in build-push-action
             - id-token: write at job level (top-level permissions stay as today)
       Preserve: platforms: linux/amd64,linux/arm64
       Preserve: Docker Hub login + ${{ secrets.DOCKERHUB_USER }}/salesagent image
       Preserve: cache-from/cache-to settings
       Add ADR-007 (lift verbatim from drafts/adr-007-build-provenance.md to
            docs/decisions/adr-007-build-provenance.md).

[ ] 4. ci(security): dependency-review-action as gating check
       Files: .github/workflows/security.yml (extend OR new)
              fail-on-severity: moderate, deny-licenses: GPL-3.0,AGPL-3.0
              harden-runner block-mode with disable-sudo-and-containers: true
              + minimal allowed-endpoints (api.github.com:443, github.com:443)
       ADMIN ACTION (do NOT run yourself): user runs after merge:
         scripts/add-required-check.sh "Security / Dependency Review"

[ ] 5. ci(codeql): flip continue-on-error: true → false (per D10 tripwire)
       PRE-CHECK: gh api 'repos/prebid/salesagent/code-scanning/alerts?state=open' --jq 'length'
                  must return ≤ 5; else STOP — extend advisory window or accept indefinite advisory.
       Files: .github/workflows/codeql.yml — remove continue-on-error from analyze step.

[ ] 6. (optional) ops: repo settings hygiene
       ADMIN ACTION (operator runs, not agent):
         gh api -X PATCH /repos/prebid/salesagent with: has_wiki=false (if unused),
         allow_squash_merge=true, allow_merge_commit=false, delete_branch_on_merge=true.

[ ] 7. (optional) test: pytest-benchmark in CI / Coverage
       Files: .github/workflows/ci.yml — extend coverage job with pytest-benchmark store-and-compare.

After Sub-PR A:
[ ] make quality
[ ] ./run_all_tests.sh
[ ] bash .claude/notes/ci-refactor/scripts/verify-pr6.sh
[ ] Open Sub-PR A; user owns push + PR creation
[ ] Begin 2-week audit-mode soak window
```

### Sub-PR B — at least 2 weeks after Sub-PR A merges

```
[ ] Pre-flight: capture audit-mode telemetry from StepSecurity dashboard
       https://app.stepsecurity.io/github/prebid/salesagent/actions/runs/<run_id>
       (URLs auto-added to the GitHub job summary by harden-runner)
       Click through ~3-5 representative runs (one per workflow type).
       Aggregate the unique outbound endpoints; investigate any unfamiliar ones
       BEFORE adding to allowlist.
[ ] git checkout -b ci/ci-refactor-pr6b-egress-block

Commits:

[ ] 3. ci(security): flip harden-runner from audit to block mode with allowlist
       Files: .github/workflows/ci.yml (and any other workflows with audit-mode harden-runner)
       Change: egress-policy: audit → block
       Add:    allowed-endpoints: > <newline-separated host:port list from telemetry>
       Keep:   disable-sudo-and-containers: true (CVE-2025-32955)
       Verify: subsequent CI runs succeed; unexpected egress causes "blocked endpoint" failure.

After Sub-PR B:
[ ] make quality
[ ] ./run_all_tests.sh
[ ] bash .claude/notes/ci-refactor/scripts/verify-pr6.sh
[ ] Open Sub-PR B; user owns push + PR creation
[ ] Re-run OpenSSF Scorecard — target ≥7.5 verified
[ ] Update 00-MASTER-INDEX.md status row to merged YYYY-MM-DD
```

### Escalation triggers — STOP and write `escalations/pr6-<topic>.md`

- Existing `release-please.yml` workflow has changed since planning → reconcile before extending
- harden-runner block-mode rejects a legitimate egress that wasn't seen during audit → revert to audit, capture more telemetry, do NOT silently allowlist
- cosign signing fails on first release → verify `id-token: write` at JOB level + correct OIDC issuer
- Multi-arch QEMU step fails → check `setup-qemu-action` SHA pin and runner availability
- CodeQL findings >5 at end of Week 4 → do NOT flip; extend advisory and triage
