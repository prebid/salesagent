# PR 6 — Image signing + advanced supply-chain hardening

**Drift items closed:** D-pending-4 (harden-runner adoption), plus net-new Fortune-50 patterns
**Estimated effort:** 1.5-2 days
**Depends on:** PR 1 merged (zizmor baseline, SHA-pinning convention), PR 3 Phase C merged (`ci.yml` is authoritative)
**Blocks:** none — final follow-up after the 5-PR core rollout
**Decisions referenced:** D2, D10, D17, D-pending-4

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
- `.github/workflows/ci.yml`, `security.yml`, `codeql.yml`, `_pytest.yml` — add `step-security/harden-runner` step at the top of every `runs-on: ubuntu-latest` job

```yaml
    steps:
      - uses: step-security/harden-runner@<SHA>  # v2
        with:
          egress-policy: audit
      - uses: ./.github/actions/setup-env
```

Verification:
```bash
COUNT=$(grep -RhoE 'uses: step-security/harden-runner@' .github/workflows/ | wc -l)
[[ $COUNT -ge 5 ]]
grep -q 'egress-policy: audit' .github/workflows/ci.yml
```

Acceptance: every Ubuntu CI job runs harden-runner; audit data populates the StepSecurity dashboard.

### Commit 2 — `ci: cosign keyless signing for GHCR + Docker Hub images`

Files:
- `.github/workflows/release.yml` (new, ~110 lines) — handles tag-triggered image push + sign

```yaml
name: Release Image

on:
  push:
    tags: ['v*']
  workflow_dispatch:

permissions: {}

jobs:
  build-sign-publish:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    permissions:
      contents: read
      packages: write
      id-token: write
      attestations: write
    steps:
      - uses: step-security/harden-runner@<SHA>  # v2
        with:
          egress-policy: audit
      - uses: actions/checkout@<SHA>  # v4
        with:
          persist-credentials: false
      - uses: docker/setup-buildx-action@<SHA>  # v3
      - uses: docker/login-action@<SHA>  # v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - id: meta
        uses: docker/metadata-action@<SHA>  # v5
        with:
          images: ghcr.io/prebid/salesagent
          tags: |
            type=ref,event=tag
            type=sha,prefix=sha-
      - id: build
        uses: docker/build-push-action@<SHA>  # v6
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          provenance: mode=max
          sbom: true
      - uses: sigstore/cosign-installer@<SHA>  # v3
      - name: Sign image (keyless)
        env:
          DIGEST: ${{ steps.build.outputs.digest }}
          TAGS: ${{ steps.meta.outputs.tags }}
        run: |
          for tag in $TAGS; do
            cosign sign --yes "${tag}@${DIGEST}"
          done
      - uses: actions/attest-build-provenance@<SHA>  # v2
        with:
          subject-name: ghcr.io/prebid/salesagent
          subject-digest: ${{ steps.build.outputs.digest }}
          push-to-registry: true
```

Verification:
```bash
test -f .github/workflows/release.yml
yamllint -d relaxed .github/workflows/release.yml
grep -q 'cosign sign --yes' .github/workflows/release.yml
grep -q 'actions/attest-build-provenance' .github/workflows/release.yml
grep -qE '^permissions:\s*\{?\s*\}?' .github/workflows/release.yml
grep -q 'id-token: write' .github/workflows/release.yml
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
      - uses: step-security/harden-runner@<SHA>  # v2
        with:
          egress-policy: block
          allowed-endpoints: >
            api.github.com:443
            github.com:443
            objects.githubusercontent.com:443
            pypi.org:443
            files.pythonhosted.org:443
            astral.sh:443
            ghcr.io:443
            registry-1.docker.io:443
            auth.docker.io:443
            production.cloudflare.docker.com:443
            uploads.github.com:443
```

The exact list is captured from audit-mode telemetry; the above is a typical profile.

Verification:
```bash
! grep -q 'egress-policy: audit' .github/workflows/ci.yml
grep -q 'egress-policy: block' .github/workflows/ci.yml
grep -q 'allowed-endpoints:' .github/workflows/ci.yml
```

Acceptance: subsequent CI runs succeed with block-mode active; unexpected egress causes a "blocked endpoint" failure with a clear log message.

### Commit 4 — `ci: add dependency-review-action as PR-blocking check`

Files:
- `.github/workflows/security.yml` — add `dependency-review` job
- Branch-protection update (admin action) to add `Security / Dependency Review`

```yaml
  dependency-review:
    name: 'Security / Dependency Review'
    runs-on: ubuntu-latest
    if: github.event_name == 'pull_request'
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: step-security/harden-runner@<SHA>
        with:
          egress-policy: block
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

Branch-protection (admin runs after merge):
```bash
gh api repos/prebid/salesagent/branches/main/protection/required_status_checks \
  --jq '.contexts' > /tmp/checks.json
jq '. + ["Security / Dependency Review"]' /tmp/checks.json > /tmp/checks-new.json
# PATCH back per PR 3 Phase B procedure
```

Verification:
```bash
grep -q 'actions/dependency-review-action' .github/workflows/security.yml
grep -q 'fail-on-severity: moderate' .github/workflows/security.yml
yamllint -d relaxed .github/workflows/security.yml
```

Acceptance: PR adding a known-CVE'd dependency fails "Security / Dependency Review" with a comment listing the offending package.

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
- Verify `release.yml` has `provenance: mode=max` (set in commit 2)
- `docs/decisions/adr-007-build-provenance.md` (new, ~50 lines)

ADR-007 documents why we use `mode=max` over default and accept the ~40s overhead per build.

Verification:
```bash
grep -q 'provenance: mode=max' .github/workflows/release.yml
test -f docs/decisions/adr-007-build-provenance.md
grep -q '## Status' docs/decisions/adr-007-build-provenance.md
```

Acceptance: `cosign verify-attestation` reports type `https://slsa.dev/provenance/v1` with `buildDefinition.externalParameters` fully populated.

### Commit 7 — `chore(repo): web-commit signoff + branch deletion + secret scanning`

Admin actions (no file changes; documented for the rollout record):

1. Settings → General → "Require contributors to sign off on web-based commits" → enable
2. Branch protection → main → "Restrict who can delete this branch" → admins only
3. Verify force-push disabled (already implied by PR 1)
4. Settings → Code security → Secret scanning → enable + Push protection → enable

Verification (post-admin-action):
```bash
gh api repos/prebid/salesagent --jq '.web_commit_signoff_required' | grep -q 'true'
gh api repos/prebid/salesagent --jq '.security_and_analysis.secret_scanning.status' | grep -q '"enabled"'
gh api repos/prebid/salesagent --jq '.security_and_analysis.secret_scanning_push_protection.status' | grep -q '"enabled"'
gh api repos/prebid/salesagent/branches/main/protection/allow_force_pushes --jq '.enabled' | grep -q 'false'
```

Acceptance: each `gh api` check returns the expected enabled state.

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
- [ ] `release.yml` has top-level `permissions: {}`
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

echo "[2/8] release.yml signs images..."
test -f .github/workflows/release.yml
grep -q 'cosign sign --yes' .github/workflows/release.yml
grep -q 'actions/attest-build-provenance' .github/workflows/release.yml

echo "[3/8] dependency-review configured..."
grep -q 'actions/dependency-review-action' .github/workflows/security.yml

echo "[4/8] CodeQL gating..."
! grep -q 'continue-on-error: true' .github/workflows/codeql.yml

echo "[5/8] provenance: mode=max..."
grep -q 'provenance: mode=max' .github/workflows/release.yml

echo "[6/8] ADR-007 present..."
test -f docs/decisions/adr-007-build-provenance.md

echo "[7/8] All new uses: SHA-pinned..."
[[ $(grep -hoE 'uses: [^ ]+@[^ ]+' .github/workflows/release.yml | grep -vcE '@[a-f0-9]{40}') == "0" ]]

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
6. **Issue #1234**: this PR closes the rollout's optional Fortune-50 follow-up per D-pending-4. Comment on #1234 with closure date and final OpenSSF Scorecard score.
```

---
