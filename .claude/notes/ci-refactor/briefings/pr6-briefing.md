# PR 6 — Image supply chain (Week 6 follow-up; resolves D25)

## Briefing

**Where we are.** Week 6. PR 1-5 merged. OpenSSF Scorecard at ≥6.5 from PR 1; this commit pushes toward ≥7.5 by satisfying `Signed-Releases` (cosign), `Pinned-Dependencies` (already at 10/10 from PR 1's SHA-pin), and the SLSA tier the build attestation reaches.

**What this PR does (~7 commits, sequenced as TWO sub-PRs ≥2 weeks apart).**

**Sub-PR A — first PR ships:**
1. **harden-runner** in audit mode on every Ubuntu job. **v2.19.0+** pinned (DoH/DNS-bypass GHSA floor v2.16.0; CVE-2025-32955 mitigated since v2.12.0; v2.19.0 is current as of 2026-04-26); `disable-sudo-and-containers: true`. NOT block-mode.
2. **Split `release-please.yml` `publish-docker` into TWO jobs** per R29: `build-and-push` (no cosign) + `sign-and-attest` (needs: build-and-push). Add cosign keyless signing + SBOM + provenance:max. Multi-arch (amd64+arm64) AND Docker Hub publishing PRESERVED. Add D47 CI-green gate with polling loop (handles 5-30s eventual-consistency lag).
4. **dependency-review-action** as PR-blocking check (admin runs `scripts/add-required-check.sh "Security / Dependency Review"` after merge — agent does NOT run gh api PATCH).
5. **Flip CodeQL** `continue-on-error: true` → `false` (per D10 tripwire if findings ≤ 5 at end of Week 4 — verify via `gh api 'repos/prebid/salesagent/code-scanning/alerts?state=open' --jq 'length'` first).

**Sub-PR B — at least 2 weeks after Sub-PR A merges:**
3. **harden-runner block-mode** with allowlist captured from 2-week audit telemetry (StepSecurity dashboard URL pattern documented in spec Commit 3).

**Plus optional commits 6-7 in either sub-PR:**
6. Repo settings hygiene via `gh api -X PATCH` (admin-only).
7. Optional `pytest-benchmark` in CI Coverage job.

**You can rely on.** All 5 preceding PRs merged. SHA-pinning convention from PR 1. `.github/.action-shas.txt` artifact (PR 1 commit 9). CodeQL findings count from D10 tripwire check. Existing `release-please.yml` `publish-docker` job builds + pushes multi-arch GHCR + Docker Hub on `release_created`.

**You CANNOT do.**
- Replace the existing `release-please.yml` workflow with a new `release.yml` — they would race for tag-driven publishes. EXTEND the existing `publish-docker` job instead. **No `release.yml` exists or will be created.**
- Drop multi-arch (`linux/amd64,linux/arm64`) or Docker Hub publishing — both are PRESERVED (verified disk-truth: `release-please.yml:72` already builds multi-arch).
- Use `harden-runner`'s `disable-sudo: true` flag — bypassable via Docker per [CVE-2025-32955](https://www.sysdig.com/blog/security-mechanism-bypass-in-harden-runner-github-action) (CVE-2025-32955 was patched in v2.12.0). Use `disable-sudo-and-containers: true` and pin to **v2.19.0+** — DoH/DNS-bypass GHSA floor is v2.16.0; v2.19.0 is the current pin as of 2026-04-26.
- Block contributor PRs on harden-runner egress without ≥2 weeks audit-mode soak in Sub-PR A.
- Run any `gh api -X PATCH branches/main/...` yourself (admin-only — operator runs `scripts/add-required-check.sh`).

**Files (heat map).**
- Modified: `.github/workflows/release-please.yml` (split `publish-docker` into `build-and-push` + `sign-and-attest` per R29; add `sha:` to release-please outputs; add D47 CI-green gate with polling loop; preserve multi-arch + Docker Hub).
- Modified: `.github/workflows/ci.yml`, `.github/actions/_pytest/action.yml` (composite, NOT `.github/workflows/_pytest.yml`), `security.yml`, `codeql.yml` — add harden-runner step. New workflow: `.github/workflows/scorecard.yml` (Commit 7b).
- Modified: `.github/workflows/codeql.yml` — flip continue-on-error.
- Added: `.github/workflows/security.yml` — dependency-review job (or extend if already present).
- Added: `docs/decisions/adr-007-build-provenance.md` — reconciles cosign + attest-build-provenance overlap.

**Escalation.**
- harden-runner block-mode breaks a workflow → revert to audit; investigate which endpoint was missed; add to `allowed-endpoints` ONLY after supply-chain investigation (a new endpoint can be a typosquatted action).
- cosign signing fails → check `id-token: write` is at the JOB level (not just top-level); check OIDC token format.
- CodeQL flip increases finding count → revert; file follow-up; do not bundle gating with finding-resolution.
- Existing release-please workflow conflict → STOP; the spec requires extension, not replacement. Re-read spec §"Critical context".

**Verification.** `bash .claude/notes/ci-refactor/scripts/verify-pr6.sh` after each commit; full check after the sub-PR completes.
