# PR 6 — Image supply chain (drafted by prior agent; optional)

## Briefing
**Where we are.** Week 5-6 (slack). PR 1-5 merged. OpenSSF Scorecard at ≥7.5; this pushes toward 8.5+. Calendar: optional Fortune-50 layer.

**What this PR does (8 commits).** Hardens the build itself. (1) **harden-runner** in audit mode for 1 week then flip to `egress-policy: block` with allowed-endpoints list. (2) **cosign keyless signing** of release images via OIDC. (3) **dependency-review-action** as a gating check on PRs with vulnerable transitive deps. (4) Flip CodeQL `continue-on-error: true` → `false` (per D10 tripwire if findings ≤ 5 at end of Week 4). (5) **SLSA provenance `mode=max`** in release workflow. (6) **Repo settings hygiene** via `gh api` (disable wikis if unused, enforce squash-merge, etc.). (7) Optional **pytest-benchmark** in CI / Coverage job for performance regression detection.

**You can rely on.** All five preceding PRs merged. SHA-pinning convention. CodeQL tripwire decision available (D10).

**You CANNOT do.** Block contributor PRs on harden-runner egress without 1 week audit-mode soak. Sign anything without the OIDC trust setup verified by `@chrishuie`.

**Files (heat map).**
- New: `.github/workflows/release.yml` (or extend existing release-please.yml) for cosign + SLSA; `.github/dependency-review-config.yml`.
- Modified: every workflow gets a `step-security/harden-runner` step (audit first, block second); `codeql.yml` flip continue-on-error.
- Repo settings: gh api `-X PATCH /repos/prebid/salesagent` toggles.

**Escalation.** harden-runner block mode breaks a workflow → revert to audit; investigate which endpoint was missed; add to allowed-endpoints. cosign signing fails → check OIDC token format, fudgepoints workflow `id-token: write` permission.
