### A4 — `uv run mypy` produces unbounded errors after pydantic.mypy plugin re-enabled (PR 2)


**Trigger**: PR 2 commit 2 runs the new `local` mypy hook; the pydantic.mypy plugin loads for the first time; error count exceeds 200 (D13 tripwire).
**Severity**: P1.
**Detection time**: immediate at commit 2.
**Affected PR(s)**: PR 2 only.

**Symptoms**
- `uv run mypy src/ --config-file=mypy.ini` returns 250+ errors.
- Many are `[arg-type]`, `[call-overload]`, or `[misc]` in `src/core/schemas*.py`.
- `.mypy-baseline.txt` (P2 pre-flight) showed a much smaller number.

**Verification**
```bash
uv run mypy src/ --config-file=mypy.ini > .mypy-current.txt 2>&1 || true
echo "before: $(grep -c 'error:' .mypy-baseline.txt)"
echo "after:  $(grep -c 'error:' .mypy-current.txt)"
diff <(sort .mypy-baseline.txt) <(sort .mypy-current.txt) | head -30
```
Distinguish from A2 by counting: > 200 = D13 tripwire fires; ≤ 200 = fix in PR 2 per D13.

**Immediate response (first 15 min)**
1. **STOP at commit 2 of PR 2.** Do not proceed to commit 3.
2. Comment out `plugins = pydantic.mypy` in `mypy.ini:3`. Add `# FIXME(salesagent-XXXX): re-enable in follow-up PR; D13 tripwire fired (>200 errors)`.
3. File a beads/GitHub issue: "PR 2 follow-up — re-enable pydantic.mypy and fix N errors". Link to D13.

**Stabilization (next 1-4 hours)**
1. Re-run commit 2 verification with the plugin commented out: error count should match `.mypy-baseline.txt`.
2. Update PR 2's description: add a "Deferred" section explaining the plugin is parked.
3. Continue with commits 3-N as planned. Commit 3 ("fix pydantic.mypy errors") becomes a no-op or is deleted entirely.

**Recovery (longer-term)**
- Follow-up PR (PR 2.1) re-enables the plugin and fixes errors in batches. Consider one batch per `src/core/schemas*.py` file.

**Post-incident**
- Update `02-risk-register.md` R2: mark D13 tripwire fired; note the deferred PR.
- Update `03-decision-log.md` D13 with a "fired YYYY-MM-DD" line.

**Why this happens (root cause)**
The plugin has been silently disabled since project inception. The pre-flight P2 baseline was captured WITHOUT the plugin loading, so it understates the post-enable count. D13 anticipated this and built in the tripwire.

**Related scenarios**
- See also: A1 (P2 missing → cannot evaluate D13), C1 (PR #1217 mid-PR-2 → may shift baseline).

---
