# PR 6 — Image signing + advanced supply-chain hardening

**Drift items closed:** D25 (was D-pending-4) (harden-runner adoption), plus net-new Fortune-50 patterns
**Estimated effort:** 1.5-2 days
**Depends on:** PR 1 merged (zizmor baseline, SHA-pinning convention), PR 3 Phase C merged (`ci.yml` is authoritative)
**Blocks:** none — final follow-up after the 5-PR core rollout
**Decisions referenced:** D2, D10, D17, D25 (was D-pending-4)

## Scope

Lift the project from "good supply-chain posture" (PR 1's CODEOWNERS, dependabot,
SHA-pinning, zizmor, advisory CodeQL) to "Fortune-50 supply-chain posture":
keyless image signing, runtime egress control, dependency-review gating, gating
CodeQL, build provenance, and repo-settings hygiene. Optionally adds a
performance regression suite.

This PR is **gated supply-chain hardening only.** No code changes, no schema
changes, no test refactor. Each commit is independently revertible.

## Out of scope

- Signed commits / tags (deferred per D4)
- New CodeQL languages (Python only)
- SLSA Level 3+ (this PR reaches Level 2; L3 needs ephemeral isolated build infra)
- Kubernetes admission policy / cosign verify in deploy targets
- Replacement of `harden-runner` audit data with curated allowlist BEFORE 2 weeks of audit data exists

## Internal commit sequence

Commits are independent; revert any one without breaking others.

### Commit 1 — `ci: enable harden-runner in audit-mode on every job`

Files:
- `.github/workflows/ci.yml`, `security.yml`, `codeql.yml`, `scorecard.yml` (P0 sweep addition; created in commit 7b), `release-please.yml` (`publish-docker` job — extended in commit 2) — add `step-security/harden-runner` step at the top of every `runs-on: ubuntu-latest` job. **NOTE:** harden-runner is NOT added inside `.github/actions/_pytest/action.yml` (composite — Decision-4); composites don't get their own runner. The composite executes inside `ci.yml`'s integration/e2e/admin/bdd/migration-roundtrip jobs which are already hardened by their job-level harden-runner step.

```yaml
    steps:
      - uses: step-security/harden-runner@<SHA>  # v2.19.0+ — SHA must resolve to v2.19.0; required for CVE-2025-32955 fix and post-CVE hardening
        with:
          egress-policy: audit
          disable-sudo-and-containers: true   # NOT disable-sudo:true (bypassable per CVE-2025-32955)
      - uses: ./.github/actions/setup-env
```

**Pinning requirement (revised 2026-04-25 P0 sweep):** the `<SHA>` for `harden-runner` must resolve to **v2.19.0 or later** (NOT v2.12.0).
- v2.12.0 (April 2025) introduced `disable-sudo-and-containers: true` and fixed [CVE-2025-32955](https://www.sysdig.com/blog/security-mechanism-bypass-in-harden-runner-github-action). It is the floor for CVE-2025-32955 mitigation but NOT sufficient on its own.
- **v2.13+** patches additional medium DoH/DNS-over-TCP egress-bypass advisories ([GHSA-46g3-37rh-v698](https://github.com/step-security/harden-runner/security/advisories)).
- **v2.16.0** captures all post-CVE advisories.
- **v2.17–v2.19** add additional hardening above the v2.16 baseline. **v2.19.0** is the current floor.

Use `disable-sudo-and-containers: true` everywhere; `disable-sudo: true` is bypassable. SHA-pin to a [v2.19.0+ release tag](https://github.com/step-security/harden-runner/releases) using the SHA-resolution loop established in PR 1 commit 9.

Verification:
```bash
COUNT=$(grep -RhoE 'uses: step-security/harden-runner@' .github/workflows/ | wc -l)
[[ $COUNT -ge 5 ]]
grep -q 'egress-policy: audit' .github/workflows/ci.yml
grep -q 'disable-sudo-and-containers: true' .github/workflows/ci.yml
! grep -RnE '^\s+disable-sudo:\s+true\s*$' .github/workflows/   # bypassable; must be absent
```

Acceptance: every Ubuntu CI job runs harden-runner; audit data populates the StepSecurity dashboard.

### Commit 2 — `ci: extend release-please.yml publish-docker with cosign + SBOM + provenance`

**Critical context:** the existing `.github/workflows/release-please.yml` already has a `publish-docker` job that builds + pushes multi-arch images (`linux/amd64,linux/arm64`) to BOTH `ghcr.io/${{ github.repository }}` AND `${{ secrets.DOCKERHUB_USER }}/salesagent` on a `release_created` gate. This PR 6 commit EXTENDS that job to add cosign keyless signing + SBOM + provenance — it does NOT create a new `release.yml` workflow (a new workflow on `tags: ['v*']` would race with release-please's tag push and produce duplicate publishes).

Files:
- `.github/workflows/release-please.yml` (modify the existing `publish-docker` job — see diff below)

**Runner version check.** Docker actions v4–v7 require GitHub Actions Runner ≥2.327.1. GitHub-hosted runners satisfy. If GHES self-hosted runners are in use, verify version before pinning. `actions/checkout@v6` requires Runner ≥2.329.0 (separate constraint).

**Diff from existing `publish-docker` job** (preserves multi-arch + Docker Hub):

```yaml
  publish-docker:
    needs: release-please
    if: ${{ needs.release-please.outputs.release_created }}
    runs-on: ubuntu-latest
    timeout-minutes: 30
    # NEW: explicit job-level permissions (PR 1 commit 9 added top-level `permissions:`; this scopes to job)
    permissions:
      contents: read
      packages: write
      id-token: write       # NEW: cosign keyless OIDC + attest-build-provenance
      attestations: write   # NEW: attest-build-provenance writes Sigstore bundle
    steps:
      # NEW: harden-runner from PR 6 commit 1 (carry-forward; CVE-2025-32955 fix)
      - uses: step-security/harden-runner@<SHA>   # v2.19.0+ — see commit 1 for SHA
        with:
          egress-policy: audit
          disable-sudo-and-containers: true

      - uses: actions/checkout@<SHA>  # v4 — SHA from .github/.action-shas.txt (PR 1 commit 9)
        with:
          persist-credentials: false

      - name: Set up QEMU
        uses: docker/setup-qemu-action@<SHA>      # v4.0.0
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@<SHA>    # v4.0.0

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@<SHA>           # v4.1.0
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Log in to Docker Hub
        uses: docker/login-action@<SHA>           # v4.1.0 — PRESERVED from existing workflow
        with:
          username: ${{ secrets.DOCKERHUB_USER }}
          password: ${{ secrets.DOCKERHUB_PASSWORD }}

      - id: meta
        name: Extract metadata for Docker
        uses: docker/metadata-action@<SHA>        # v5
        with:
          images: |
            ghcr.io/${{ github.repository }}
            ${{ secrets.DOCKERHUB_USER }}/salesagent
          tags: |
            type=semver,pattern={{version}},value=${{ needs.release-please.outputs.version }}
            type=semver,pattern={{major}}.{{minor}},value=${{ needs.release-please.outputs.version }}
            type=semver,pattern={{major}},value=${{ needs.release-please.outputs.version }}
            type=raw,value=latest

      - id: build
        name: Build and push Docker image
        uses: docker/build-push-action@<SHA>      # v7.1.0
        with:
          context: .
          file: Dockerfile
          push: true
          platforms: linux/amd64,linux/arm64       # PRESERVED — multi-arch
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
          # NEW: SLSA provenance + SBOM
          provenance: mode=max
          sbom: true

      # NEW: cosign keyless signing for both registries
      - uses: sigstore/cosign-installer@<SHA>     # v4.1.1 — SHA must resolve to v4.1.1; v3.x cannot install Cosign 3.0+
      - name: Sign image (keyless)
        env:
          DIGEST: ${{ steps.build.outputs.digest }}
          TAGS: ${{ steps.meta.outputs.tags }}
          BUNDLE_PATH: /tmp/cosign-bundle-${{ github.run_id }}.json
        run: |
          for tag in $TAGS; do
            cosign sign --yes --bundle "${BUNDLE_PATH}" "${tag}@${DIGEST}"
          done

      # NEW: build-provenance attestation (separate from cosign — see ADR-007 reconciliation)
      - uses: actions/attest-build-provenance@<SHA>  # v4.1.0 — SHA must resolve to v4.1.0; v2 is two majors stale
        with:
          subject-name: ghcr.io/${{ github.repository }}
          subject-digest: ${{ steps.build.outputs.digest }}
          push-to-registry: true
```

The trigger (`on: push: branches: [main]` gated by `release_created`) and the `release-please` job stay unchanged. No new workflow file is created.

Verification:
```bash
# We're modifying an existing file, not creating one.
test -f .github/workflows/release-please.yml
yamllint -d relaxed .github/workflows/release-please.yml

# Multi-arch + Docker Hub PRESERVED
grep -q 'platforms: linux/amd64,linux/arm64' .github/workflows/release-please.yml
grep -q 'DOCKERHUB_USER' .github/workflows/release-please.yml

# New supply-chain fields present
grep -q 'cosign sign --yes --bundle' .github/workflows/release-please.yml   # --bundle required in Cosign v3+
grep -q 'actions/attest-build-provenance' .github/workflows/release-please.yml
grep -q 'sbom: true' .github/workflows/release-please.yml
grep -q 'provenance: mode=max' .github/workflows/release-please.yml

# CVE-2025-32955 fix
grep -q 'disable-sudo-and-containers: true' .github/workflows/release-please.yml

# OIDC permissions scoped to job (top-level remains `permissions: contents: write, pull-requests: write, packages: write`)
grep -q 'id-token: write' .github/workflows/release-please.yml
```

Acceptance: tag push produces signed multi-platform image at `ghcr.io/prebid/salesagent:vX.Y.Z` plus provenance attestation. Verify:
```bash
cosign verify ghcr.io/prebid/salesagent:vX.Y.Z \
  --certificate-identity-regexp 'https://github.com/prebid/salesagent/.+' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com'
```

### Commit 3 — `ci: flip harden-runner from audit to block-mode with allowlist`

**Prerequisite:** ≥2 weeks of audit data after commit 1 has merged. Open as a follow-up PR, not bundled with commit 1.

Files: all workflow files modified by commit 1 — change `egress-policy: audit` → `block`, add `allowed-endpoints:`.

```yaml
    steps:
      - uses: step-security/harden-runner@<SHA>  # v2.19.0+
        with:
          egress-policy: block
          disable-sudo-and-containers: true   # CVE-2025-32955 mitigation; carry-forward from commit 1
          allowed-endpoints: >
            api.github.com:443
            github.com:443
            objects.githubusercontent.com:443
            raw.githubusercontent.com:443
            codeload.github.com:443
            pkg-containers.githubusercontent.com:443
            uploads.github.com:443
            pypi.org:443
            files.pythonhosted.org:443
            registry.npmjs.org:443
            astral.sh:443
            ghcr.io:443
            registry-1.docker.io:443
            auth.docker.io:443
            production.cloudflare.docker.com:443
            index.docker.io:443
            app.stepsecurity.io:443
            apiurl.stepsecurity.io:443
            *.actions.githubusercontent.com:443
            *.blob.core.windows.net:443
            rekor.sigstore.dev:443
            fulcio.sigstore.dev:443
            tuf-repo-cdn.sigstore.dev:443
```

**The static allowlist is starter material.** The audit-mode soak window's StepSecurity dashboard output is the authoritative source. Before flipping to block-mode (Commit 3), extract the dashboard's "Suggested allowed endpoints" and replace this list. Static enumeration WILL be incomplete; rely on telemetry.

**Allowlist additions in 2026-04-25 P0 sweep** (Round 6 scenarios reviewer caught these):
- `codeload.github.com:443` — autobuild source download (CodeQL workflow + `curl https://github.com/.../archive/...`)
- `pkg-containers.githubusercontent.com:443` — GHCR layer pulls
- `*.actions.githubusercontent.com:443` and `*.blob.core.windows.net:443` — GitHub artifact upload/download
- `index.docker.io:443` — Docker Hub publish (release-please.yml's existing publish-docker job)
- `app.stepsecurity.io:443` + `apiurl.stepsecurity.io:443` — StepSecurity dashboard telemetry
- `rekor.sigstore.dev:443` + `fulcio.sigstore.dev:443` + `tuf-repo-cdn.sigstore.dev:443` — Sigstore for cosign keyless signing (RELEASE-ONLY; if the audit window did NOT include a release, these endpoints won't appear in telemetry — see "Force release dry-run" below)
- `registry.npmjs.org:443` — any GitHub Action that internally `npm install`s (Round 9 P1 addition)
- `raw.githubusercontent.com:443` — pinact installer fetch path; `objects.githubusercontent.com` does NOT cover `raw.githubusercontent.com` (Round 9 P1 addition)

**Force release dry-run during audit window** (2026-04-25 P0 sweep): release-please.yml is gated on `release_created`; if no release happens during the 2-week audit window, sigstore + Docker Hub publish endpoints never appear in telemetry. Before flipping block-mode, force a dry-run:

```bash
# Trigger release-please workflow on main; the publish-docker job runs and exercises
# all release-only endpoints (cosign, Docker Hub, GHCR pkg, sigstore TUF).
gh workflow run release-please.yml --ref main
gh run watch  # wait for completion; check StepSecurity dashboard for new endpoints
```

If the audit window did NOT include a release, do NOT flip block-mode for `release-please.yml`. Either run the dry-run above first, OR keep release-please.yml in audit-mode while flipping ci.yml + security.yml to block-mode.

**How to extract the allowlist from audit-mode telemetry** (do this before opening commit 3):

1. After every CI run during the 2-week audit window, the GitHub job step summary contains a link of the form `https://app.stepsecurity.io/github/prebid/salesagent/actions/runs/<run_id>` (the StepSecurity insights URL — added automatically by harden-runner).
2. Click through ~3-5 representative runs (one of each workflow type: ci, codeql, security, `_pytest` composite invocation, release-please-dry-run).
3. The dashboard "Outbound calls" table lists every endpoint hit with hit count + step name. Copy each unique `host:port` from non-anomalous calls. Use the StepSecurity "Suggested allowed endpoints" export to JSON if available.
4. Aggregate into a single deduplicated list. Manual aggregation works fine — there are typically <25 unique endpoints (the block above is the empirically derived 2026-04 list for this repo).
5. The block above is a typical profile for a Python+Docker+Sigstore repo. Do NOT add new endpoints without supply-chain investigation; a new endpoint can indicate a typosquatted action or compromised dependency.

Reference: [StepSecurity 2-week soak procedure](https://docs.stepsecurity.io/harden-runner/getting-started) and `research/external-tool-yaml.md` §4 "2-week soak procedure".

Verification:
```bash
! grep -q 'egress-policy: audit' .github/workflows/ci.yml
grep -q 'egress-policy: block' .github/workflows/ci.yml
grep -q 'allowed-endpoints:' .github/workflows/ci.yml
grep -q 'disable-sudo-and-containers: true' .github/workflows/ci.yml   # CVE-2025-32955
! grep -RnE '^\s+disable-sudo:\s+true\s*$' .github/workflows/   # bypassable variant absent
```

Acceptance: subsequent CI runs succeed with block-mode active; unexpected egress causes a "blocked endpoint" failure with a clear log message.

### Commit 3b (new) — Emergency revert workflow

**File**: `.github/workflows/harden-runner-emergency-revert.yml`

When block-mode locks out CI, recovery is admin-only unless we provide a manual-dispatch revert workflow. This workflow lets anyone with write access flip every workflow back to `audit` mode and open a PR with the revert.

```yaml
name: harden-runner-emergency-revert
on:
  workflow_dispatch:
    inputs:
      reason:
        description: 'Brief reason for revert (logged in PR body)'
        required: true
permissions: { contents: write, pull-requests: write }
jobs:
  revert:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@<sha>  # v6.0.2
        with: { persist-credentials: false }
      - run: |
          for f in .github/workflows/*.yml; do
            sed -i 's/egress-policy: block/egress-policy: audit/g' "$f"
          done
          git checkout -b harden-runner-emergency-revert-${{ github.run_id }}
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git config user.name "harden-runner-emergency-revert"
          git commit -am "ops: emergency revert harden-runner to audit mode"
          gh pr create --title "ops: emergency revert harden-runner to audit" --body "Reason: ${{ inputs.reason }}"
```

**Trigger eligibility**: anyone with write access. Faster than waiting for solo maintainer when block-mode locks out main.

**Post-revert protocol**: post-mortem within 24h; identify the unallowlisted egress destination; either add to allowlist OR investigate whether the call is suspicious; flip back to block-mode after fix.

### Commit 4 — `ci: add dependency-review-action as PR-blocking check`

Files:
- `.github/workflows/security.yml` — add `dependency-review` job
- Branch-protection update — **ADMIN-ONLY action**, runs after merge via `scripts/add-required-check.sh "Security / Dependency Review"` (NEW companion to `flip-branch-protection.sh`); the executor agent does NOT run `gh api -X PATCH branches/main/...` per `feedback_user_owns_git_push.md`.

```yaml
  dependency-review:
    name: 'Security / Dependency Review'
    runs-on: ubuntu-latest
    if: github.event_name == 'pull_request'
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: step-security/harden-runner@<SHA>   # v2.19.0+ — CVE-2025-32955 fix
        with:
          egress-policy: block
          disable-sudo-and-containers: true       # CVE-2025-32955 mitigation
          allowed-endpoints: >
            api.github.com:443
            github.com:443
      - uses: actions/checkout@<SHA>
        with:
          persist-credentials: false
      - uses: actions/dependency-review-action@<SHA>  # v4
        with:
          fail-on-severity: moderate
          comment-summary-in-pr: on-failure
          deny-licenses: GPL-3.0, AGPL-3.0
```

**ADMIN ACTION (executor MUST NOT run this):** after this commit merges, the user adds `Security / Dependency Review` to branch protection's required checks list:

```bash
# Non-destructive read first
CURRENT=$(gh api repos/prebid/salesagent/branches/main/protection/required_status_checks \
  --jq '[.checks[].context]')
NEW=$(echo "$CURRENT" | jq '. + ["Security / Dependency Review"] | unique')

# PATCH using the same shape as flip-branch-protection.sh
gh api -X PATCH \
  /repos/prebid/salesagent/branches/main/protection/required_status_checks \
  -H "Accept: application/vnd.github+json" \
  --input - <<EOF
{
  "strict": true,
  "checks": $(echo "$NEW" | jq '[.[] | {context: .}]')
}
EOF
```

Verification:
```bash
grep -q 'actions/dependency-review-action' .github/workflows/security.yml
grep -q 'fail-on-severity: moderate' .github/workflows/security.yml
grep -q 'disable-sudo-and-containers: true' .github/workflows/security.yml
yamllint -d relaxed .github/workflows/security.yml
```

Acceptance: PR adding a known-CVE'd dependency fails "Security / Dependency Review" with a comment listing the offending package.

### Commit 4b — `ci: enable zizmor auditor persona for secrets-outside-env rule`

Files:
- `.github/workflows/security.yml` — update the existing zizmor invocation (added in PR 1 commit 5) to add `--persona=auditor`

The `secrets-outside-env` rule is auditor-persona-only in zizmor 1.24+; it does NOT fire by default. To surface findings of secrets used outside of `env:` blocks (a common cause of token-leakage bugs), invoke zizmor with the auditor persona:

```yaml
- run: uvx --from 'zizmor==1.24.1' zizmor --format=github --min-severity=medium --persona=auditor .
```

**Version requirement:** zizmor 1.24+ required for the `--persona=auditor` flag and the `secrets-outside-env` rule. Pin via `uvx --from 'zizmor==1.24.1'` to make the version explicit; let dependabot bump it.

Verification:
```bash
grep -q -- '--persona=auditor' .github/workflows/security.yml
grep -qE "zizmor==1\.24\.[0-9]+" .github/workflows/security.yml
```

Acceptance: zizmor flags secrets used outside `env:` blocks; existing PR 1 zizmor allowlist still honored via `.github/zizmor.yml`.

### Commit 5 — `ci: flip CodeQL from advisory to gating`

Files:
- `.github/workflows/codeql.yml` — remove `continue-on-error: true` from `analyze` step (added by PR 1 per D10 Path C)

Per D10 tripwire — only after Week 4 review confirms ≤5 outstanding findings.

```yaml
# Before:
      - uses: github/codeql-action/analyze@<SHA>
        with:
          category: '/language:${{ matrix.language }}'
        continue-on-error: true   # PER D10 PATH C — advisory until 2026-05-30

# After:
      - uses: github/codeql-action/analyze@<SHA>
        with:
          category: '/language:${{ matrix.language }}'
```

Plus admin: add `CodeQL / analyze (python)` to required-checks (verify exact context name in actions/runs panel before flipping).

Verification:
```bash
! grep -q 'continue-on-error: true' .github/workflows/codeql.yml
gh api 'repos/prebid/salesagent/code-scanning/alerts?state=open' --jq 'length' \
  | xargs -I{} bash -c '[[ {} -le 5 ]]'
```

Acceptance: CodeQL findings now block PR merges. File follow-up issues for remaining findings (do not allowlist in code).

### Commit 6 — `ci: add provenance: mode=max attestation + ADR-007`

Files:
- Verify `release-please.yml` has `provenance: mode=max` in the publish-docker job (set in commit 2)
- `docs/decisions/adr-007-build-provenance.md` (new, ~50 lines)

ADR-007 documents why we use `mode=max` over default and accept the ~40s overhead per build.

Verification:
```bash
grep -q 'provenance: mode=max' .github/workflows/release-please.yml
test -f docs/decisions/adr-007-build-provenance.md
grep -q '## Status' docs/decisions/adr-007-build-provenance.md
```

Acceptance: `cosign verify-attestation` reports type `https://slsa.dev/provenance/v1` with `buildDefinition.externalParameters` fully populated.

### Commit 7 — `chore(repo): web-commit signoff + branch deletion + secret scanning`

Admin actions (no file changes; documented for the rollout record). **All steps are ADMIN-ONLY — DO NOT RUN AS AGENT.**

1. Settings → General → "Require contributors to sign off on web-based commits" → enable
2. Branch protection → main → "Restrict who can delete this branch" → admins only
3. Verify force-push disabled (already implied by PR 1)
4. Settings → Code security → Secret scanning → enable + Push protection → enable
5. **(P0 sweep addition)** GitHub Container Registry tag immutability — Settings → Packages → `salesagent` → "Tag immutability" → set to immutable for `:vX.Y.Z` semver tags. Prevents an attacker who gains push access from replaying old tags. (GA 2025; enterprise-only previously.)
6. **(P0 sweep addition, R30 mitigation)** Settings → General → Pull Requests → "Allow auto-merge" → DISABLE. Pre-flight A11 audited this before PR 1; verify it's still off post-PR-6 (A23/R30 mitigation chain).

Verification (post-admin-action):
```bash
gh api repos/prebid/salesagent --jq '.web_commit_signoff_required' | grep -q 'true'
gh api repos/prebid/salesagent --jq '.security_and_analysis.secret_scanning.status' | grep -q '"enabled"'
gh api repos/prebid/salesagent --jq '.security_and_analysis.secret_scanning_push_protection.status' | grep -q '"enabled"'
gh api repos/prebid/salesagent/branches/main/protection/allow_force_pushes --jq '.enabled' | grep -q 'false'
gh api repos/prebid/salesagent --jq '.allow_auto_merge' | grep -q 'false'   # R30 mitigation
# Tag immutability is package-scoped; verify via UI (no public API as of 2026-04)
```

Acceptance: each `gh api` check returns the expected enabled state; tag-immutability confirmed manually.

### Commit 7b (P0 sweep addition) — `ci: add OpenSSF Scorecard self-host workflow`

**New commit added in 2026-04-25 P0 sweep.** Plan claims a Scorecard target of ≥7.5/10 but no committed `.github/workflows/scorecard.yml` workflow file existed. Self-hosted Scorecard produces deterministic scores (vs. waiting for the external public dashboard's weekly scrape) AND publishes findings to the Code Scanning tab via SARIF, so they show up alongside CodeQL.

Files:
- `.github/workflows/scorecard.yml` (new — lift verbatim from `research/external-tool-yaml.md` §3)

Skeleton:
```yaml
name: OpenSSF Scorecard

on:
  branch_protection_rule:        # fires on bp changes — early signal
  schedule:
    - cron: '0 6 * * 1'          # weekly Monday 06:00 UTC
  push:
    branches: [main]

permissions: read-all

jobs:
  analysis:
    name: 'Scorecard analysis'
    runs-on: ubuntu-latest
    permissions:
      security-events: write     # to upload SARIF
      id-token: write             # for OIDC (Sigstore signing of result)
      contents: read
    steps:
      - uses: actions/checkout@<SHA>  # v4
        with:
          persist-credentials: false
      - uses: ossf/scorecard-action@<SHA>  # v2.4.3+ — SHA must resolve to v2.4.3 (2024-09-30) or later; v2.5.0 does not exist on the releases page
        with:
          results_file: results.sarif
          results_format: sarif
          publish_results: true
      - uses: actions/upload-artifact@<SHA>  # v4
        with:
          name: SARIF file
          path: results.sarif
          retention-days: 5
      - uses: github/codeql-action/upload-sarif@<SHA>  # v4
        with:
          sarif_file: results.sarif
```

Verification:
```bash
test -f .github/workflows/scorecard.yml
yamllint -d relaxed .github/workflows/scorecard.yml
grep -q 'ossf/scorecard-action' .github/workflows/scorecard.yml
grep -qE 'publish_results:\s*true' .github/workflows/scorecard.yml
grep -q 'branch_protection_rule' .github/workflows/scorecard.yml
```

Acceptance: Scorecard workflow runs weekly + on every BP change; results appear in the Code Scanning tab.

### Commit 8 (optional) — `test(perf): pytest-benchmark regression suite`

Files:
- `pyproject.toml` — add `pytest-benchmark` to `[dependency-groups].dev`
- `tests/perf/test_perf_critical_paths.py` (new, ~100 lines)
- `.github/workflows/ci.yml` — add `CI / Performance` job (advisory 4 weeks, then gating)
- `.perf-baseline.json` (new) — committed baseline JSON

Hot paths to benchmark: `_get_products_impl`, `_create_media_buy_impl`, `_get_media_buy_delivery_impl`, schema-validation hot paths (`Product.model_dump()`, `MediaBuy.model_dump()`).

```yaml
  performance:
    name: 'CI / Performance'
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: ./.github/actions/setup-env
      - run: |
          uv run pytest tests/perf/ \
            --benchmark-only \
            --benchmark-json=/tmp/bench.json \
            --benchmark-compare=.perf-baseline.json \
            --benchmark-compare-fail=mean:25%
```

Threshold: 25% slower-than-baseline fails. Re-baseline manually for intentional slowdowns.

Verification:
```bash
test -f tests/perf/test_perf_critical_paths.py
test -f .perf-baseline.json
grep -q 'pytest-benchmark' pyproject.toml
yq '.jobs.performance.name' .github/workflows/ci.yml | grep -q 'CI / Performance'
```

Acceptance: a PR causing >25% regression in a benchmarked path fails the performance check.

**Optionality:** if PR 6's effort budget is tight, defer commit 8 to PR 7. Commits 1-7 are load-bearing.

## Acceptance criteria

- [ ] `harden-runner` runs on every Ubuntu job (audit → block over 2-week cutover)
- [ ] Tag pushes produce cosign-signed images at `ghcr.io/prebid/salesagent`
- [ ] Tag pushes produce SLSA Level 2 build-provenance attestations
- [ ] `dependency-review-action` is a required check on PRs
- [ ] CodeQL is gating (no `continue-on-error: true` in `codeql.yml`)
- [ ] All published image builds use `provenance: mode=max`
- [ ] Web-commit signoff enabled on the repo
- [ ] Secret scanning + push protection enabled
- [ ] (Optional) `tests/perf/` exists with ≥4 benchmarks; CI gates on 25% regression

Plus agent-derived:
- [ ] `release-please.yml` has top-level `permissions: {}` (job-level overrides where needed)
- [ ] All new actions are SHA-pinned
- [ ] ADR-007 (build-provenance) exists; CLAUDE.md ADR table updated
- [ ] `make quality` passes

## Verification (full PR-level)

```bash
bash .claude/notes/ci-refactor/scripts/verify-pr6.sh
```

Inline:
```bash
#!/usr/bin/env bash
set -euo pipefail

echo "[1/8] harden-runner present..."
[[ $(grep -RhoE 'uses: step-security/harden-runner@' .github/workflows/ | wc -l) -ge 5 ]]

echo "[2/8] release-please.yml signs images..."
test -f .github/workflows/release-please.yml
grep -q 'cosign sign --yes --bundle' .github/workflows/release-please.yml   # --bundle required in Cosign v3+
grep -q 'actions/attest-build-provenance' .github/workflows/release-please.yml

echo "[3/8] dependency-review configured..."
grep -q 'actions/dependency-review-action' .github/workflows/security.yml

echo "[4/8] CodeQL gating..."
! grep -q 'continue-on-error: true' .github/workflows/codeql.yml

echo "[5/8] provenance: mode=max..."
grep -q 'provenance: mode=max' .github/workflows/release-please.yml

echo "[6/8] ADR-007 present..."
test -f docs/decisions/adr-007-build-provenance.md

echo "[7/8] All new uses: SHA-pinned..."
[[ $(grep -hoE 'uses: [^ ]+@[^ ]+' .github/workflows/release-please.yml | grep -vcE '@[a-f0-9]{40}') == "0" ]]

echo "[8/8] make quality passes..."
make quality

echo "PR 6 verification PASSED"
```

## Risks (scoped to PR 6)

- **harden-runner block-mode false positives.** Mitigation: 2-week audit window; allowlist generated from real telemetry. Rollback: revert commit 3 (`block` → `audit`).
- **CodeQL flip exposes triage backlog.** Mitigation: D10 tripwire — flip only when ≤5 findings; file follow-up issues. Rollback: re-add `continue-on-error: true`.
- **dependency-review false positives.** Mitigation: deny list narrow (GPL/AGPL); fail-on-severity `moderate` not `low`. Rollback: bump severity threshold to `high`.
- **cosign keyless OIDC unavailability.** Mitigation: GitHub OIDC has high SLO. Rollback: ephemeral key (`cosign generate-key-pair`) — documented in ADR-007 tripwire.
- **Performance suite false positives from xdist variance.** Mitigation: 25% threshold; min_rounds=50 stabilizes. Rollback: delete commit 8.

## Rollback plan

Each commit is independently revertible:
```bash
git revert <commit-N-sha>
```

| Commit | Revert impact |
|---|---|
| 1 (audit) | None on CI; loses telemetry |
| 2 (cosign) | New tags unsigned; existing untouched |
| 3 (block) | Falls back to audit; no breakage |
| 4 (dep-review) | Removes new required check; admin removes from branch protection |
| 5 (CodeQL gating) | Returns to advisory; findings still visible |
| 6 (provenance max) | New builds use `mode=min` |
| 7 (repo settings) | Revert via UI; no code change |
| 8 (perf suite) | Removes the gate |

Full revert: `git revert -m 1 <PR6-merge-sha>`. Recovery: <10 min plus admin actions for branch protection.

## Merge tolerance

- **PR #1217 (adcp 3.12)**: tolerated.
- **v2.0 phase PR landing on `.github/workflows/`**: medium conflict on `ci.yml` (harden-runner steps). Coordinate.
- **v2.0 phase PR landing on `Dockerfile`**: tolerated.

## Coordination notes for the maintainer

1. **Before authoring**: PR 1 + PR 3 Phase C must be merged.
2. **Two-week audit window**: commit 1 (audit) and commit 3 (block) ship in *different* PRs separated by ≥2 weeks of telemetry.
3. **Pre-flight CodeQL count**: before commit 5, run `gh api 'repos/prebid/salesagent/code-scanning/alerts?state=open' --jq 'length'`. If > 5, hold commit 5; file triage issues per D10 tripwire.
4. **Release workflow dry-run**: tag a `v0.0.0-dry` test tag in a throwaway branch, verify `cosign tree ghcr.io/prebid/salesagent:v0.0.0-dry`, then delete.
5. **After merge**: admin actions in commit 7 require maintainer login to GitHub UI. Do not delegate.
6. **Issue #1234**: this PR closes the rollout's optional Fortune-50 follow-up per D25 (was D-pending-4). Comment on #1234 with closure date and final OpenSSF Scorecard score.
```

---
