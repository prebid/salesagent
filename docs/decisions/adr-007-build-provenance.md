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
configured in `.github/workflows/release-please.yml` and any docker-build job
that publishes to `ghcr.io/prebid/salesagent`. Pair this with
`actions/attest-build-provenance` to write the attestation to GitHub's storage
AND to Sigstore's public Rekor transparency log via OIDC.

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

## Consequences

**Positive.**

- Downstream consumers can verify provenance via `gh attestation verify`.
- Sigstore Rekor provides a public, append-only audit log of every signed image.
- Workflow-identity signing avoids PII leakage that personal-key signing would create.

**Negative.**

- ~40s per build overhead (acceptable on release jobs; not on per-PR builds).
- Dependency on Sigstore's Rekor log being available.

## Tripwire

If Sigstore's Rekor log experiences **sustained downtime exceeding 4 hours**:

1. Pause release builds (the workflow will fail attestation publication).
2. Evaluate a fallback to long-lived cosign keypair signing as a temporary measure.
3. Document the temporary configuration; revert to keyless once Rekor stabilizes.
