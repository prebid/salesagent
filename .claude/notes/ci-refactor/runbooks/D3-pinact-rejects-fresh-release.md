### D3 — pinact rejects a freshly-released action


**Trigger**: PR adds `uses: new-action@v1.2.3` with a valid GitHub release. pinact (in CI) flags it as unpinned.
**Severity**: P3.
**Detection time**: immediate.
**Affected PR(s)**: any PR adding a new action.

**Symptoms**
- pinact step fails with `not pinned to SHA: new-action@v1.2.3`.

**Verification**
```bash
pinact run --check
```

**Immediate response (first 15 min)**
1. Run `pinact run --update` locally. Tool resolves the tag to its commit SHA.
2. Stage the resulting `.github/workflows/*.yml` diff (now `@<sha> # v1.2.3`).
3. Commit and push.

**Stabilization (next 1-4 hours)**
1. Re-run CI; pinact passes.

**Recovery (longer-term)**
- None.

**Post-incident**
- None.

**Why this happens (root cause)**
pinact's policy is "every `uses:` must be a 40-char SHA". Author forgot to run pinact locally before pushing. The tool exists exactly for this.

**Related scenarios**
- See also: D2 (force-push), B1 (zizmor unpinned-uses overlap).

---
