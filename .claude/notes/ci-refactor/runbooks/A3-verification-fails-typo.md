### A3 — A commit's verification fails (typo in verification script)


**Trigger**: commit's code change is correct, but the verification command itself is mistyped, points at a moved file, or asserts an outdated invariant.
**Severity**: P2.
**Detection time**: immediate.
**Affected PR(s)**: All — most likely on PR 3 (10 commits, large verification footprint) and PR 4 (10 commits, bash-heavy).

**Symptoms**
- The change in the diff matches the spec's intent.
- `make quality` and the relevant tests pass.
- The verification block fails with a parse error, a missing-file error, or an assertion that doesn't match reality.

**Verification**
```bash
# Run only the change side, ignoring the verifier
make quality
uv run pytest tests/unit/  # if applicable to the commit
# Then run the verification block manually, line by line
```
If `make quality` is green but only the spec's verification script fails, it's a verifier bug.

**Immediate response (first 15 min)**
1. Confirm the commit is intent-correct: the change matches what the spec describes.
2. Reproduce the verification failure with `bash -x` to surface which line errs.
3. Edit the verification block IN THE SPEC FILE (not the commit) to match reality. Note this in the PR description as "spec-fix".

**Stabilization (next 1-4 hours)**
1. Commit the spec edit as a separate trailing commit on the same PR (e.g., `chore: fix verification script typo`).
2. Continue.

**Recovery (longer-term)**
- None.

**Post-incident**
- File no issue — spec edits are normal.
- If the typo class repeats (e.g., multiple `yq` selectors are wrong), audit the rest of the spec proactively.

**Why this happens (root cause)**
Specs are hand-written; verifications are tested only when their commit lands. Typos slip through review.

**Related scenarios**
- See also: A2 (genuine bug — distinguish first).

---
