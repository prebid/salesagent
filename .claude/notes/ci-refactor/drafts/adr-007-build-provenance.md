# ADR-007: Build provenance attestation

## Status

Accepted (2026-04-25). Implemented in PR 6 of the CI/pre-commit refactor
follow-ups (issue #1234). Tripwire: if Sigstore's Rekor transparency log
experiences sustained downtime (>4h), evaluate a fallback to long-lived
cosign keypair signing.

## Context

PR 6 introduces `provenance: mode=max` on all `docker/build-push-action` steps
and `actions/attest-build-provenance` for cosign keyless image signing. Build
provenance attestations are SLSA Level 2 evidence about how an artifact was
built — what source commit, what builder, what dependencies, what build flags.

Without explicit provenance, downstream consumers (e.g., an enterprise
pulling `ghcr.io/prebid/salesagent`) cannot:

1. Verify which source commit produced the image.
2. Confirm the image came from this repo's official CI rather than a
   compromised fork or local build.
3. Audit the build environment that produced the image.

This matters for the salesagent project specifically because Prebid.org's
ecosystem includes Fortune-50 ad tech consumers who increasingly require
SLSA evidence as part of vendor assessments.

Adding provenance has measurable overhead: ~40s per image build for the
in-toto attestation generation and Rekor log entry.

## Decision

Adopt `provenance: mode=max` (full SLSA provenance) on all image builds
configured in `.github/workflows/release-please.yml` (the existing
`publish-docker` job, EXTENDED by PR 6 commit 2; no separate `release.yml`
exists or will be created) and any docker-build job that publishes to
`ghcr.io/prebid/salesagent`. Pair this with `actions/attest-build-provenance`
to write the attestation to GitHub's storage AND to Sigstore's public Rekor
transparency log via OIDC.

**Verification path (downstream consumer).**

Preferred — `gh attestation verify` (insulates from cosign v3/v4 churn):
```bash
gh attestation verify oci://ghcr.io/prebid/salesagent@sha256:<digest> --owner prebid
```

If using raw `cosign verify` directly, cosign v3+ requires the `--bundle`
flag (mandatory; was optional in v2.x):
```bash
cosign verify \
  --certificate-identity-regexp 'https://github.com/prebid/salesagent/\.github/workflows/release-please\.yml@refs/heads/main' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  --bundle <bundle.json> \
  ghcr.io/prebid/salesagent@sha256:<digest>
```

This is documented in the PR 6 spec and in `CONTRIBUTING.md` under a
"Verifying images" section.

**Sigstore keyless-signing identity.** The workflow's `id-token: write`
permission produces an OIDC token claiming the workflow identity:

```
https://github.com/prebid/salesagent/.github/workflows/release-please.yml@refs/heads/main
```

(release-please runs on `push` to `main`, not on tag push; the certificate
identity reflects the workflow's actual trigger.)

Sigstore mints a short-lived certificate from this token. The certificate's
identity claim is the **workflow identity**, not a personal email. This is
the right behavior; do not configure with a personal-email-based signing
key.

**Per-architecture signing scope.** The plan signs the manifest-list digest
(the `linux/amd64,linux/arm64` index), not per-platform digests. Downstream
verifiers using `gh attestation verify oci://...` against the index see the
attestation. Per-platform signing (one signature per arch, accessible via
`docker buildx imagetools inspect --raw`) is L3-class work and is deferred.
Document the scope in CONTRIBUTING.md so consumers do not expect per-arch
sigs that don't exist.

**harden-runner version floor.** PR 6 pins `step-security/harden-runner` to
**v2.16.0+** (NOT v2.12.x). v2.12.0 fixed CVE-2025-32955 (sudo-bypass via
container escape), but v2.13+ patches additional medium DoH/DNS-over-TCP
egress-bypass advisories (GHSA-46g3-37rh-v698). Use
`disable-sudo-and-containers: true` everywhere; `disable-sudo: true` is
bypassable.

## Options considered

**Option A — `provenance: mode=min`.** Rejected. Insufficient SLSA evidence;
the minimal mode omits build-input metadata that downstream verifiers need.

**Option B — `provenance: mode=max` with keyless attestation (chosen).**
Full SLSA L2 evidence; reasonable per-build overhead; Sigstore Rekor
provides public verifiability without managing a signing key.

**Option C — Long-lived cosign keypair.** Rejected. Key management overhead
(rotation, revocation, secret storage in GitHub Secrets); compromise risk if
the secret leaks; rotation breaks downstream verifier configurations. The
keyless flow eliminates all of this.

**Option D — Skip attestation entirely.** Rejected. Falls below the
Fortune-50 standard expected by Prebid.org's downstream consumers; loses an
eventual SLSA L3 path that depends on this groundwork.

## Consequences

**Positive.**
- Downstream consumers can verify provenance via `gh attestation verify`.
- Sigstore Rekor provides a public, append-only audit log of every signed
  image — independent of GitHub's storage.
- Workflow-identity signing avoids PII leakage that personal-key signing
  would create.
- Establishes the foundation for a future SLSA L3 hermetic-build path.

**Negative.**
- ~40s per build overhead (acceptable on release jobs; not on per-PR builds).
- New tooling for verifiers (`gh attestation verify`) — documented in
  CONTRIBUTING.md, but onboarding cost is non-zero.
- Dependency on Sigstore's Rekor log being available. Brief outages are
  tolerable; sustained downtime triggers the tripwire below.

## Tripwire

If Sigstore's Rekor log experiences **sustained downtime exceeding 4 hours**:

1. Pause release builds (the workflow will fail attestation publication).
2. Evaluate a fallback to long-lived cosign keypair signing as a temporary
   measure (key stored in `secrets.COSIGN_KEY`). Note: long-lived secrets
   are exposed to every workflow that names them — restrict scope.
3. Document the temporary configuration; revert to keyless once Rekor
   stabilizes.

Until that tripwire fires, keyless signing is preferred. We do not
preemptively maintain a long-lived key; the operational cost outweighs the
contingency benefit at the current Rekor reliability level.

**Sigstore Rekor v2 transition.** Sigstore is rolling out 2025/2026 Rekor v2
instances with planned URL changes. The trust root is TUF-distributed and
cosign auto-upgrades; no manual change needed for verifiers. However, the
Rekor URL printed in cosign's verbose output may change in early 2026 —
maintainers should not hard-code the URL in custom verification scripts.

## Reproducible builds (known L2 limitation)

Two builds of the same source produce different digests today because:
- BuildKit emits non-deterministic timestamps in image layers.
- `docker buildx` does not set `SOURCE_DATE_EPOCH` by default.
- File order in tar layers is filesystem-dependent.

This weakens the "verify → reproduce" workflow: a downstream consumer can
verify the signature, but cannot independently rebuild and confirm the same
digest.

**Status**: known L2 limitation, deferred to L3 work. Mitigation path:
- Set `SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)` in the build job.
- Use `docker buildx --provenance=mode=max,reproducible=true` (when GA).
- Pin BuildKit minor version in the workflow.

## SLSA L3 path

L3 requires:
- **Hermetic builds** — no network access during build (apart from a pinned
  package mirror); `docker buildx --provenance=mode=max,reproducible=true`
  is a step toward this but does not fully achieve hermeticity.
- **Self-hosted ephemeral runners** — to control the build environment and
  prevent supply-chain attacks via shared github-hosted runner state.
- **Builder-trust evidence** — published SBOM of the builder image itself.

Out of scope for #1234. Track as a post-2026 follow-up if the Fortune-50
consumer base requires L3.

## 2026 attack landscape (validates this ADR's controls)

Recent supply-chain incidents reinforce that runtime egress control +
SHA-pinned actions + dependency-review are essential, not optional:

- **LiteLLM PyPI compromise** (2026-03-24) — versions 1.82.7/1.82.8 were
  backdoored via PyPI credentials stolen through a compromised Trivy
  GitHub Action. 3-hour quarantine window before PyPI removal. `pip-audit`
  alone won't catch this within the window; runtime egress control
  (harden-runner block-mode) is the load-bearing defense.
- **Axios npm attack** (2026-03-30) — `axios@1.14.1` and `0.30.4` dropped
  a RAT via postinstall hook. Salesagent doesn't use npm directly, but the
  pattern (postinstall execution) reinforces dependency-review urgency.
- **Bitwarden CLI npm attack** (2026-04) — preinstall hook bootstrapped
  Bun runtime + obfuscated stealer targeting AI coding-tool configs.
  CODEOWNERS-protected `.pre-commit-config.yaml` + SHA-pinned hooks already
  guard against analogous attacks on this repo.

These attacks validate the plan's core controls; they do not change the
ADR's decision but reinforce the urgency of harden-runner block-mode (PR 6
Commit 3) and dependency-review (PR 6 Commit 4).
```
