# PR 6 — Image supply chain (Week 6 follow-up; resolves D25)

## Checklist

Sequenced as TWO sub-PRs with ≥2-week separation. Sub-PR A first, Sub-PR B after audit-mode telemetry is captured.

### Sub-PR A — first PR (Week 6 commits 1, 2, 4, 5; optional 6, 7)

```
[ ] Pre-flight TTL guard (paste from .claude/notes/ci-refactor/preflight-ttl-guard.md)
[ ] git checkout -b ci/ci-refactor-pr6a-supply-chain-audit

Commits:

[ ] 1. ci(security): add harden-runner in audit mode to all workflows
       harden-runner@<SHA> v2.19.0+ as first step in every Ubuntu job.
       (DoH/DNS-bypass GHSA floor v2.16.0; CVE-2025-32955 mitigated since v2.12.0;
        v2.19.0 is current as of 2026-04-26.)
       MUST use `disable-sudo-and-containers: true` (CVE-2025-32955).
       NOT `disable-sudo: true` — that flag is bypassable via Docker.
       egress-policy: audit (NOT block — audit-mode soak runs ≥2 weeks
       before Sub-PR B flips to block).

[ ] 2a. ci(release): declare `sha: ${{ github.sha }}` in release-please.yml outputs
       (D47 gate prerequisite; without this, the gate's `?head_sha=$RELEASE_SHA`
       query is empty-substituted and the gate becomes a no-op — R44 unmitigated.)
       Verify: yq '.jobs."release-please".outputs.sha' .github/workflows/release-please.yml | grep -q 'github.sha'

[ ] 2. ci(release): split publish-docker into TWO jobs per R29; add cosign + SBOM + provenance
       Files: .github/workflows/release-please.yml (MODIFY — do NOT create release.yml,
              the existing publish-docker already does multi-arch + Docker Hub).
       Split: publish-docker → build-and-push (no cosign) + sign-and-attest (needs: build-and-push)
              per R29 mitigation (Cosign + Rekor outage cascade — registered but never applied
              until Round 13 audit caught the gap).
       Add:  - D47 CI-green gate with polling loop (6 × 30s for eventual-consistency tolerance)
             - sigstore/cosign-installer + cosign sign --yes --bundle loop over $TAGS (in sign-and-attest)
             - actions/attest-build-provenance@v4.1.0 with push-to-registry: true
             - sbom: true and provenance: mode=max in build-push-action
             - id-token: write at job level (sign-and-attest job; top-level permissions stay as today)
             - SOURCE_DATE_EPOCH from git commit timestamp; rewrite-timestamp=true
       Preserve: platforms: linux/amd64,linux/arm64
       Preserve: Docker Hub login + ${{ secrets.DOCKERHUB_USER }}/salesagent image
       Preserve: cache-from/cache-to settings
       Add ADR-007 (lift verbatim from drafts/adr-007-build-provenance.md to
            docs/decisions/adr-007-build-provenance.md).
       Verify: PR 6 has TWO publish jobs: build-and-push (no cosign) + sign-and-attest (needs: build-and-push) per R29 mitigation
       Verify: D47 gate uses polling loop (6× retries × 30s) for eventual-consistency tolerance

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

[ ] 2.5. test(ci): scratch-test harden-runner emergency revert workflow (Round 14 M8)
       Pre-merge verification only — no source changes in this commit; only adds
       evidence file: escalations/harden-runner-revert-test-evidence.md
       MUST land BEFORE Commit 3 (audit→block flip). The block-mode failure path
       is the emergency-revert workflow; an untested escape hatch is operational
       malpractice. Procedure: dispatch the workflow on a scratch branch, verify
       it opens a revert PR with correct sed-substituted YAML, record evidence.
       Tripwire: if scratch test fails, fix Commit 3b before Commit 3.
       Verify: escalations/harden-runner-revert-test-evidence.md exists on disk.

[ ] 3b. ci(security): add harden-runner emergency-revert workflow (Round 13)
       Files: .github/workflows/harden-runner-emergency-revert.yml (NEW)
       MUST land BEFORE Commit 3 (audit→block flip). Provides operator-side
       recovery when block-mode locks out CI: any write-access user can
       gh workflow run harden-runner-emergency-revert.yml with a reason; the
       workflow sed-substitutes egress-policy: block → audit across all
       workflows and opens a revert PR.
       Permissions: { contents: write, pull-requests: write }.
       Verify: workflow file exists; permissions block correct;
       persist-credentials: false on checkout step.

[ ] 3. ci(security): flip harden-runner from audit to block mode with allowlist
       Files: .github/workflows/ci.yml (and any other workflows with audit-mode harden-runner)
       Change: egress-policy: audit → block
       Add:    allowed-endpoints: > <newline-separated host:port list from telemetry>
       Keep:   disable-sudo-and-containers: true (CVE-2025-32955)
       Keep:   v2.19.0+ pin (DoH/DNS-bypass GHSA floor v2.16.0)
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
