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
configured in `.github/workflows/release.yml` and any docker-build job that
publishes to `ghcr.io/prebid/salesagent`. Pair this with
`actions/attest-build-provenance` to write the attestation to GitHub's
storage AND to Sigstore's public Rekor transparency log via OIDC.

**Verification path (downstream consumer).**

```bash
gh attestation verify oci://ghcr.io/prebid/salesagent@sha256:<digest> --owner prebid
```

This is documented in the PR 6 spec and in `CONTRIBUTING.md` under a
"Verifying images" section.

**Sigstore keyless-signing identity.** The workflow's `id-token: write`
permission produces an OIDC token claiming the workflow identity:

```
https://github.com/prebid/salesagent/.github/workflows/release.yml@refs/tags/v*
```

Sigstore mints a short-lived certificate from this token. The certificate's
identity claim is the **workflow identity**, not a personal email. This is
the right behavior; do not configure with a personal-email-based signing
key.

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
   measure (key stored in `secrets.COSIGN_KEY`).
3. Document the temporary configuration; revert to keyless once Rekor
   stabilizes.

Until that tripwire fires, keyless signing is preferred. We do not
preemptively maintain a long-lived key; the operational cost outweighs the
contingency benefit at the current Rekor reliability level.
```
