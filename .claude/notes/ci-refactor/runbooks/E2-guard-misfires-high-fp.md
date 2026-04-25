### E2 — A structural guard misfires consistently (high false-positive rate)


**Trigger**: contributor reports the same guard failing on unrelated PRs over multiple weeks.
**Severity**: P2.
**Detection time**: pattern emerges over weeks.
**Affected PR(s)**: PR 4 (guard authors); ongoing.

**Symptoms**
- Contributor PRs unrelated to the guard's domain fail it.
- Allowlist grows fast (multiple new entries per week).

**Verification**
```bash
git log -p tests/unit/test_architecture_<guard>.py | head -100
git log --since="3 months" --diff-filter=M tests/unit/test_architecture_<guard>.py
```
Frequent edits to the allowlist signal high FP rate.

**Immediate response (first 15 min)**
1. Read the guard's logic. Is it AST-based and precise, or grep-based and lossy?
2. Identify the FP class: misclassified comments? misparsed string literals? false matches across module boundaries?

**Stabilization (next 1-4 hours)**
1. If the guard logic has a bug, fix it. Tighten the AST visitor.
2. If the guard catches legitimate cases that need to be allowed: add to the allowlist with `# FIXME(salesagent-XXXX)` and document the legitimate pattern in `docs/development/structural-guards.md`.
3. If the guard is fundamentally over-broad: file an issue to redesign or retire it.

**Recovery (longer-term)**
- Track guard FP rate quarterly. Retire guards that exceed an FP threshold.

**Post-incident**
- Update R7 mitigation if the guard was a key R7 enforcement.
- Update `02-risk-register.md` with the guard FP class.

**Why this happens (root cause)**
AST-scanning guards are precise on the patterns they were designed for. Codebase evolves; new patterns emerge that look similar to violations but aren't. Guards need maintenance.

**Related scenarios**
- See also: E3 (allowlist stagnation — opposite direction).

---
