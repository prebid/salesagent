### F2 — `secrets.GEMINI_API_KEY` leaked via fork PR


**Trigger**: a fork PR triggers a `pull_request_target` workflow (or any workflow with secrets access) that prints or exfiltrates the env. Any secret leak.
**Severity**: P0.
**Detection time**: hours to never (depends on detection).
**Affected PR(s)**: pre-PR-1 only — D15 deletes the conditional Gemini key fallback.

**Symptoms**
- A fork PR's CI logs contain real secret values (uncommon — GitHub's log redaction usually catches).
- A request to an external service uses our credentials from a fork's CI run.
- GitHub Security alert about a leaked secret.

**Verification**
```bash
# Audit which workflows have pull_request_target
grep -r 'pull_request_target' .github/workflows/
# For each, check what secrets it reads
gh secret list
# Pre-PR-1: there's a gemini fallback at test.yml:342
grep -n GEMINI_API_KEY .github/workflows/*.yml
```

**Immediate response (first 15 min — P0)**
1. **Rotate the leaked key IMMEDIATELY** at the upstream (Google AI Console → revoke + regenerate).
2. **Update the GitHub secret** with the new value.
3. **Audit the GitHub Audit Log** for token usage:
   ```bash
   gh api '/orgs/<org>/audit-log?phrase=action:repo&per_page=100' \
     --jq '.[] | select(.actor != "<expected-bot>")'
   ```
4. **Check upstream service logs** (Google AI Console) for usage from unexpected IPs or quotas.

**Stabilization (next 1-4 hours)**
1. **After PR 1, this scenario is impossible:** D15 replaces `${{ secrets.GEMINI_API_KEY || 'test_key_for_mocking' }}` with `GEMINI_API_KEY: test_key_for_mocking` (unconditional). Fork PRs cannot exfiltrate the real key because workflows don't read it.
2. If pre-PR-1 and the leak occurred: confirm no workflow with `pull_request_target` reads any other secret unsafely. If any do, narrow them to `pull_request` (no secrets).

**Recovery (longer-term)**
- Adopt secret scanning push protection (Settings → Code security → Secret scanning).
- Audit ALL `pull_request_target` workflows for secret reads. Pin them to internal events only.

**Post-incident**
- File a security advisory.
- Update SECURITY.md.
- Confirm D15 is in PR 1 and merged ASAP — it eliminates this attack surface.

**Why this happens (root cause)**
`pull_request_target` runs in the context of the base repo, with secret access, but on the head's tree. A malicious fork can modify a workflow file to read and exfiltrate secrets. D15 + permissions narrowing eliminates this for our specific case.

**Related scenarios**
- See also: D5 (attest-fork-pr — different fork interaction), F1, F3.

---
