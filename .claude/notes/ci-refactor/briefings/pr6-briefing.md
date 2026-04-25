# PR 6 — Image supply chain (Week 6 follow-up; resolves D25)

## Briefing

**Where we are.** Week 6. PR 1-5 merged. OpenSSF Scorecard at ≥6.5 from PR 1; this commit pushes toward ≥7.5 by satisfying `Signed-Releases` (cosign), `Pinned-Dependencies` (already at 10/10 from PR 1's SHA-pin), and the SLSA tier the build attestation reaches.

**What this PR does (~7 commits, sequenced as TWO sub-PRs ≥2 weeks apart).**

**Sub-PR A — first PR ships:**
1. **harden-runner** in audit mode on every Ubuntu job. v2.12.0+ pinned (CVE-2025-32955); `disable-sudo-and-containers: true`. NOT block-mode.
2. **Extend `release-please.yml` `publish-docker` job** with cosign keyless signing + SBOM + provenance:max. Multi-arch (amd64+arm64) AND Docker Hub publishing PRESERVED — this is an extension of the existing job, not a new workflow.
4. **dependency-review-action** as PR-blocking check (admin runs `scripts/add-required-check.sh "Security / Dependency Review"` after merge — agent does NOT run gh api PATCH).
5. **Flip CodeQL** `continue-on-error: true` → `false` (per D10 tripwire if findings ≤ 5 at end of Week 4 — verify via `gh api 'repos/prebid/salesagent/code-scanning/alerts?state=open' --jq 'length'` first).

**Sub-PR B — at least 2 weeks after Sub-PR A merges:**
3. **harden-runner block-mode** with allowlist captured from 2-week audit telemetry (StepSecurity dashboard URL pattern documented in spec Commit 3).

**Plus optional commits 6-7 in either sub-PR:**
6. Repo settings hygiene via `gh api -X PATCH` (admin-only).
7. Optional `pytest-benchmark` in CI Coverage job.

**You can rely on.** All 5 preceding PRs merged. SHA-pinning convention from PR 1. `.github/.action-shas.txt` artifact (PR 1 commit 9). CodeQL findings count from D10 tripwire check. Existing `release-please.yml` `publish-docker` job builds + pushes multi-arch GHCR + Docker Hub on `release_created`.

**You CANNOT do.**
- Replace the existing `release-please.yml` workflow with a new `release.yml` — they would race for tag-driven publishes. EXTEND the existing `publish-docker` job instead.
- Drop multi-arch (`linux/amd64,linux/arm64`) or Docker Hub publishing — both are preserved.
- Use `harden-runner`'s `disable-sudo: true` flag — bypassable via Docker per [CVE-2025-32955](https://www.sysdig.com/blog/security-mechanism-bypass-in-harden-runner-github-action). Use `disable-sudo-and-containers: true` and pin to v2.12.0+.
- Block contributor PRs on harden-runner egress without ≥2 weeks audit-mode soak in Sub-PR A.
- Run any `gh api -X PATCH branches/main/...` yourself (admin-only — operator runs `scripts/add-required-check.sh`).

**Files (heat map).**
- Modified: `.github/workflows/release-please.yml` (extend `publish-docker` job — preserve multi-arch + Docker Hub).
- Modified: `.github/workflows/ci.yml`, `_pytest.yml`, `security.yml`, `codeql.yml` — add harden-runner step.
- Modified: `.github/workflows/codeql.yml` — flip continue-on-error.
- Added: `.github/workflows/security.yml` — dependency-review job (or extend if already present).
- Added: `docs/decisions/adr-007-build-provenance.md` — reconciles cosign + attest-build-provenance overlap.

**Escalation.**
- harden-runner block-mode breaks a workflow → revert to audit; investigate which endpoint was missed; add to `allowed-endpoints` ONLY after supply-chain investigation (a new endpoint can be a typosquatted action).
- cosign signing fails → check `id-token: write` is at the JOB level (not just top-level); check OIDC token format.
- CodeQL flip increases finding count → revert; file follow-up; do not bundle gating with finding-resolution.
- Existing release-please workflow conflict → STOP; the spec requires extension, not replacement. Re-read spec §"Critical context".

**Verification.** `bash .claude/notes/ci-refactor/scripts/verify-pr6.sh` after each commit; full check after the sub-PR completes.
