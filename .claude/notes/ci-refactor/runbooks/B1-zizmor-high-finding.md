### B1 — zizmor reports high-severity finding on a PR you're authoring


**Trigger**: PR 1's `Security Audit` job (zizmor step) emits `dangerous-triggers` or `excessive-permissions` at HIGH severity on a workflow you just modified.
**Severity**: P2.
**Detection time**: 1-3 min after push.
**Affected PR(s)**: PR 1 primarily; any PR that touches `.github/workflows/`.

**Symptoms**
- Job fails with `error: excessive-permissions` or `error: dangerous-triggers`.
- The flagged workflow is one you just edited (or just SHA-pinned).

**Verification**
```bash
uvx zizmor .github/workflows/ --min-severity high 2>&1 | tee /tmp/zizmor.txt
diff <(grep '^error:' .zizmor-preflight.txt | sort) <(grep '^error:' /tmp/zizmor.txt | sort)
```
A new finding (line not in `.zizmor-preflight.txt`) is what your PR introduced.

**Immediate response (first 15 min)**
1. Read the finding's location: file, line, rule.
2. Decide: **fix** (add `permissions: {}` block, narrow trigger) or **allowlist** (with `# zizmor: ignore[<rule>]` + ADR-003 reference).
3. **Never suppress without an ADR justification.** ADR-003 is the umbrella ADR for legitimate `pull_request_target` cases (pr-title-check, ipr-agreement). New cases need an ADR.

**Stabilization (next 1-4 hours)**
1. Common cases:
   - `excessive-permissions` → add top-level `permissions: {}` and per-job `permissions:` allowlists.
   - `dangerous-triggers` (pull_request_target) → narrow to `paths:` filter + add `# zizmor: ignore[dangerous-triggers]` + ADR-003 reference.
   - `unpinned-uses` → run `pinact run` (D3 path).
   - `template-injection` → quote/escape `${{ }}` interpolations into env vars; never inline into `run:`.
2. Re-run zizmor locally; confirm clean.
3. Commit, push, watch CI.

**Recovery (longer-term)**
- None.

**Post-incident**
- If a new ADR was needed, file it under `docs/decisions/`.
- Update `.zizmor-preflight.txt` if the baseline shifted.

**Why this happens (root cause)**
Zizmor scans every workflow on every PR. Any new workflow file or any `gh action` update can introduce findings.

**Related scenarios**
- See also: D3 (pinact handles unpinned), F1 (compromised PR — different threat model), B2 (CodeQL — different scanner).

---
