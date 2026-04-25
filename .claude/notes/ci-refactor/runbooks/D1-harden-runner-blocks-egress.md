### D1 — harden-runner block-mode rejects a legitimate egress


**Trigger**: a CI run fails with `blocked endpoint api.unexpected.com` from harden-runner; investigation shows the endpoint is required by a legitimate tool (e.g., a build-info collector, telemetry endpoint).
**Severity**: P1 — blocks the affected job.
**Detection time**: immediate.
**Affected PR(s)**: future PR adopting harden-runner (D-pending-4 — not in 5-PR rollout, but relevant).

**Symptoms**
- harden-runner step in job fails with a blocked-endpoint message.
- Same job worked yesterday.

**Verification**
```bash
gh run view --log <run-id> | grep -A2 'blocked endpoint'
```
Read the blocked URL carefully.

**Immediate response (first 15 min)**
1. **Do NOT blindly add the URL to the allowlist.** Could be a supply-chain attack (a compromised dep phoning home).
2. Investigate: search the codebase and `uv.lock` for the host. Find which package emits the call. Check the package's recent releases for tampering signs.
3. **If uncertain about legitimacy, revert harden-runner to `egress-policy: audit`** for 1 week. Capture full traffic. Add the endpoint with a justification only after confirming.

**Stabilization (next 1-4 hours)**
1. If legit: add to `allowed-endpoints:` with a comment naming the package and reason.
2. If suspicious: open a security investigation; freeze the affected dep version; consider rolling back recent dependabot merges.

**Recovery (longer-term)**
- None for legit case. For suspicious case, pursue F1 runbook.

**Post-incident**
- Document the new allowlist entry in an ADR if it's significant (e.g., a new external service).
- Update SECURITY.md if the threat model shifts.

**Why this happens (root cause)**
harden-runner whitelists known-good egresses. Any new dependency or tool may need a new entry. Block-mode forces this conversation before allowing new traffic.

**Related scenarios**
- See also: F1 (compromised PR — overlapping concern), D2 (action force-push — different supply-chain vector).

---
