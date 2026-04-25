### F1 — A compromised Dependabot PR is identified (tj-actions-class)


**Trigger**: post-merge alert (GitHub Security Advisory, OSS Security mailing list, or CodeQL retro-scan) reveals that a recently-merged Dependabot PR contained a malicious payload. The 24h cooldown was bypassed, OR the payload was added before the cooldown.
**Severity**: P0.
**Detection time**: hours to weeks (depends on disclosure).
**Affected PR(s)**: any post-PR-1.

**Symptoms**
- Upstream advisory: "Versions X.Y.Z of <pkg> contained a malicious payload."
- Our `git log` shows we merged that bump.
- Possible suspicious CI artifacts: unusual outbound connections, new env reads, stray files in `node_modules`/`.venv`.

**Verification**
```bash
git log --grep="<pkg>" --since="<approx-date>" --oneline
gh run list --workflow=ci.yml --created="<approx-date>..now" --limit=50 --json databaseId,conclusion,headSha
```
Check Audit Log for `GITHUB_TOKEN` usage:
```bash
gh api '/orgs/<org>/audit-log?phrase=action:repo+repo:salesagent&per_page=100' \
  --jq '.[] | select(.created_at > "<date>") | {action, actor, created_at}'
```

**Immediate response (first 15 min — P0)**
1. **Revert the merge commit on main.** Use `@chrishuie` bypass per ADR-002 — this is the legitimate emergency case.
   ```bash
   git revert -m 1 <merge-sha>
   ```
2. **Rotate `GITHUB_TOKEN` and all repo secrets immediately.** GitHub UI: Settings → Secrets → re-create each. The compromised CI run had token access.
3. **Pin the affected dep to the last-known-good version** in `pyproject.toml` and `uv.lock`. Open an emergency PR.
4. **Audit recent merges (7-30 days back).** Look for anomalous CI patterns: unexpected egress, new secrets reads, unusual artifacts.

**Stabilization (next 1-4 hours)**
1. **Notify upstream** if not already disclosed. File a security advisory on our repo if the threat affects users.
2. **Pull harden-runner forward** as a P0 follow-up if it wasn't yet adopted. Block all unexpected egress.
3. **Review ALL Dependabot PRs in the open queue.** Apply manual diff review before merging anything.
4. Communicate via SECURITY.md channel if this affected users (downstream consumers of our package, if any).

**Recovery (longer-term)**
- Adopt harden-runner if not already. Make it a required check.
- Add 24h cooldown enforcement: a workflow that blocks Dependabot merges < 24h since PR open.
- Consider Sigstore/cosign verification of release artifacts (D4 tripwire).

**Post-incident**
- File a public retrospective if the incident affected anyone downstream.
- Update SECURITY.md threat model.
- Update D5 rationale with this case as evidence.
- Risk register: add a new R-entry for "compromised dep merged".
- Audit `02-risk-register.md` for related risks.

**Why this happens (root cause)**
Upstream supply-chain compromises are the primary threat the rollout is designed to mitigate (pin-and-review). A compromise that bypasses the review (e.g., the attacker waited > 24h, the maintainer skimmed) still passes the controls — defense in depth (harden-runner, attestations, signed releases) closes the gap further.

**Related scenarios**
- See also: D1 (harden-runner block), D2 (force-pushed action), F3 (bypass exploit).

---
