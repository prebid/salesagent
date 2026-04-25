### D5 — `attest-build-provenance` fails on fork PR


**Trigger**: a forked-repo PR runs the build workflow; the `attest-build-provenance` step fails with `id-token: write not allowed`.
**Severity**: P2.
**Detection time**: immediate.
**Affected PR(s)**: PR 1 followup (build provenance is post-rollout candidate).

**Symptoms**
- Fork PR's build job fails ONLY on the attestation step.
- Same workflow on internal PRs passes.

**Verification**
```bash
gh pr view <num> --json headRepository --jq '.headRepository.isFork'
```
If `true`, fork PR; secrets and `id-token: write` are restricted.

**Immediate response (first 15 min)**
1. Gate the attestation step:
   ```yaml
   - name: attest-build-provenance
     if: github.event.pull_request.head.repo.full_name == github.repository
     uses: actions/attest-build-provenance@<sha>
     ...
   ```
2. Push the fix to the workflow as a fast-track PR.

**Stabilization (next 1-4 hours)**
1. Re-test on a fork PR; attestation step is now skipped (not failed).

**Recovery (longer-term)**
- None.

**Post-incident**
- Document the gate in the workflow with a comment.
- Update CONTRIBUTING.md if external contributors might be confused.

**Why this happens (root cause)**
Forks cannot use `id-token: write` (security boundary). Attestations require it. Skip on forks; attest only on internal merges.

**Related scenarios**
- See also: F2 (fork PR + secrets — different threat).

---
