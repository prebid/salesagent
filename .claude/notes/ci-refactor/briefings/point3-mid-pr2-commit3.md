## Cold-start briefing — Point 3: PR 2 in flight, commit 3 in progress (pydantic.mypy errors being fixed)

**Where you are in the rollout**
- Calendar week: Week 2, mid-week
- PRs merged: PR 1 (governance + workflow hardening, fully merged)
- PRs in flight: PR 2 — commits 1 and 2 are made on the local branch; commit 3 is in progress
- PRs pending: PR 3 (3-phase), PR 4, PR 5
- v2.0 phase PR coordination: PR #1217 (adcp 3.10→3.12) status — verify with `gh pr view 1217 --json state`. v2.0 phase PRs may have started landing; verify

**FORENSICS: What the LAST agent likely did before context wiped**
A previous agent was working through commit 3 of PR 2: "fix(types): address pydantic.mypy plugin errors surfaced in PR 2." The trigger sequence:

1. Commit 1 added/verified `docs/decisions/adr-001-single-source-pre-commit-deps.md` (no-op if PR 1 already added it)
2. Commit 2 swapped `mirrors-mypy` for a local `uv run mypy` hook in `.pre-commit-config.yaml` — this re-enabled the silently-disabled `pydantic.mypy` plugin
3. Running `uv run mypy src/ --config-file=mypy.ini` after commit 2 produced ~78 new errors (the plugin's contribution). Per D13, fix in this PR; tripwire if delta >200
4. Agent began making targeted fixes — most likely in `src/core/schemas*.py` and `src/core/tools/*/`, possibly with inline `# type: ignore[arg-type]` for genuinely-Pydantic-internal cases

**Files you'll touch in this PR (heat map)**
- Primary: wherever new mypy errors land (typically `src/core/schemas.py`, `src/core/schemas_*.py`, `src/core/tools/*/`)
- Do not touch yet (later commits): `pyproject.toml` `[project.optional-dependencies].dev` (commit 5), `[dependency-groups].ui-tests` migration (commit 6), `psf/black` swap (commit 7)

**Verification environment**
- `make quality` is currently RED on this branch (commit 2 introduced the mypy errors; commit 3 fixes them)
- After each fix: `uv run mypy src/ --config-file=mypy.ini 2>&1 | grep -c 'error:'` to track progress
- Must reach exit-0 on `uv run mypy src/ --config-file=mypy.ini` before marking commit 3 done

**"Did the previous agent leave anything broken?" (forensics checklist)**
- [ ] Run `git status` — anything uncommitted? It's the in-flight fix work; don't `git stash` it without inspecting
- [ ] Run `git log origin/main..HEAD --oneline` — should show 2 commits if the previous agent reached commit 2 cleanly. If it shows 0 or 1, you're earlier than expected
- [ ] Run `git diff` — see what fixes the previous agent staged but didn't commit. If the diff includes `# type: ignore[...]` comments, those are the agent's in-progress fixes; review them for correctness before committing
- [ ] Check `.mypy-baseline.txt` — the pre-flight P2 baseline (errors-before, when plugin was silently disabled)
- [ ] Check `.mypy-current.txt` if it exists — the agent may have captured the post-commit-2 state. Compare with `uv run mypy src/ --config-file=mypy.ini > /tmp/now.txt 2>&1` to see how many errors remain
- [ ] Look for `.claude/notes/ci-refactor/escalations/pr2-*.md` — if exists, the agent escalated; read it before resuming
- [ ] Verify the branch name matches expected: `git branch --show-current` should be `feat/ci-refactor-pr2-uvlock-single-source` or similar

**What you can rely on (already true on the local branch)**
- Commit 1: ADR-001 exists at `docs/decisions/adr-001-single-source-pre-commit-deps.md`
- Commit 2: `.pre-commit-config.yaml` no longer has `mirrors-mypy`; has new `local` mypy hook with `entry: uv run mypy --config-file=mypy.ini`, `language: system`
- `mypy.ini:3` `plugins =` line still references `pydantic.mypy` (we want it active)
- `.mypy-baseline.txt` from pre-flight P2 is in `.claude/notes/ci-refactor/`

**What you must NOT do**
- Do not abandon `pydantic.mypy` (commenting out from `mypy.ini:3`) unless delta exceeds 200 (D13 tripwire). The current ~78 is well below
- Do not advance to commit 4 (`uv sync --extra dev` → `--group dev`) until mypy is green — commit 4 unblocks commit 5 (deletion of `[project.optional-dependencies].dev`); CI will be red between commits 4 and 5 if order slips
- Do not pre-empt commit 5 by deleting `[project.optional-dependencies].dev` now
- Do not amend commit 2; create a new commit 3 with the fixes

**Specific commands to run FIRST (in order)**
1. `cd /Users/quantum/Documents/ComputedChaos/salesagent && git status` — assess uncommitted work
2. `git log origin/main..HEAD --oneline` — confirm commits 1 and 2 are present
3. `git diff` — review the previous agent's in-flight changes
4. `uv run mypy src/ --config-file=mypy.ini 2>&1 | grep -c 'error:'` — current error count
5. `uv run mypy src/ --config-file=mypy.ini 2>&1 | grep -oE 'error: \[[a-z-]+\]' | sort | uniq -c | sort -rn | head` — error breakdown by category
6. `diff .claude/notes/ci-refactor/.mypy-baseline.txt <(uv run mypy src/ --config-file=mypy.ini 2>&1) | head -50` — show new errors vs baseline
7. Cross-check delta against D13 tripwire: if current minus baseline >200, STOP and escalate

**Decisions in effect**
D13 (this is the active decision driving the in-flight commit), D14 (ui-tests migration coming in commit 6), D20 (verify v2.0 has not re-introduced `[project.optional-dependencies].dev` since pre-flight)

**Risks active right now**
- R2 (pydantic.mypy 200+): live; if you discover delta >200 after factoring in any partial progress, escalate
- R5 (PR #1217 merges mid-review): tolerable — `uv run mypy` reads `uv.lock` at invocation time. No semantic change

**Escalation triggers**
- Mypy delta exceeds 200: STOP, comment out `pydantic.mypy` from `mypy.ini:3`, file follow-up issue, document deferral in PR description, continue with commit 3 as no-op
- Any fix requires adding `# type: ignore` to >10% of `src/core/schemas*.py` lines: smell test failed, escalate
- Any fix requires changing public API of a Pydantic model in a way that affects AdCP contract: STOP, run `uv run pytest tests/unit/test_adcp_contract.py -v` first; if it fails, you're touching the wrong thing

**How to resume the work**
1. Identify what the previous agent had fixed: `git diff` against any stashed work, plus inspection of edited files
2. Run `uv run mypy src/ --config-file=mypy.ini` and triage remaining errors by file
3. Apply the same fix pattern (real type fix vs `# type: ignore[arg-type]`) consistently. CLAUDE.md Pattern #4 (Pydantic nested serialization) is a common trigger
4. After each batch of ~10 fixes: `uv run mypy src/ --config-file=mypy.ini` (catch regressions)
5. When mypy is exit-0: `git add` the fixes, commit as `fix(types): address pydantic.mypy plugin errors surfaced in PR 2`. Body should mention error count reduction (e.g., "78 → 0")
6. Resume PR 2 spec at commit 4 (`chore(ci): migrate uv sync --extra dev → --group dev`)

**Where to find context**
- `.claude/notes/ci-refactor/pr2-uvlock-single-source.md` — your spec
- `.claude/notes/ci-refactor/03-decision-log.md` D13 — the policy you're enforcing
- `.mypy-baseline.txt` — the pre-flight baseline
- CLAUDE.md Pattern #4 — Pydantic nested serialization (likely error pattern)
- `.claude/notes/ci-refactor/escalations/pr2-*.md` if exists — previous escalation

---
