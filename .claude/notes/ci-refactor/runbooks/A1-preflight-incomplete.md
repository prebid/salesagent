### A1 — Pre-flight checklist incomplete when executor agent starts


**Trigger**: agent begins PR 1 or PR 2 work but a pre-flight artifact (`.mypy-baseline.txt`, `.zizmor-preflight.txt`, `branch-protection-snapshot.json`, `required-checks-current.txt`) is missing.
**Severity**: P1 — blocks PR authoring.
**Detection time**: immediate (commit-1 verification fails).
**Affected PR(s)**: PR 1 (P3 zizmor, A1/A2 snapshot), PR 2 (P2 mypy baseline).

**Symptoms**
- Agent's first verification step exits with `No such file or directory: .mypy-baseline.txt`.
- Agent's PR description has empty "before" metric.
- Tripwire decisions (D13, R3) cannot be evaluated.

**Verification**
```bash
test -f .mypy-baseline.txt && wc -l .mypy-baseline.txt
test -f .zizmor-preflight.txt && wc -l .zizmor-preflight.txt
test -f .claude/notes/ci-refactor/branch-protection-snapshot.json
test -f .claude/notes/ci-refactor/required-checks-current.txt
```
A missing file means a pre-flight step was skipped. Some are admin-only (A1-A25) — agents cannot retroactively run them.

**Immediate response (first 15 min)**
1. **STOP authoring.** Do not run agent-runnable steps (P1-P10) yet — running them out of order can mask the missing admin baseline.
2. Identify which artifacts are missing (run the verification block above).
3. Classify: **admin-only** (A1-A25) or **agent-runnable** (P1-P10).
4. Escalate to the user (`@chrishuie`) with a list of the missing admin-only artifacts. Quote `01-pre-flight-checklist.md` line ranges.

**Stabilization (next 1-4 hours)**
1. User runs the missing admin-only steps. Agent runs missing P1-P10 steps.
2. Re-verify all 16 pre-flight checkboxes.
3. Sign off in `01-pre-flight-checklist.md` (each `- [ ]` → `- [x]`).
4. Resume PR authoring from commit 1.

**Recovery (longer-term)**
- None — this is a one-time gate.

**Post-incident**
- File a follow-up issue if a P-step revealed unexpected drift (e.g., line numbers shifted in `pyproject.toml`).
- Update `01-pre-flight-checklist.md` if any step's command is stale.

**Why this happens (root cause)**
Pre-flight checklist is split between admin (`gh api`, GitHub UI) and agent. Agents cannot perform admin steps. If the user starts an agent session without running A1-A25 first, the agent has no baseline to compare against.

**Related scenarios**
- See also: A4 (pydantic.mypy explosion — relies on P2 baseline), B3 (branch-protection flip relies on A1 snapshot).

---
