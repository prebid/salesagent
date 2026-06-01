---
name: team
description: Launch a coordinated agent team to work on parallel tasks
arguments:
  - name: prompt
    description: What the team should do (e.g., "execute 18h.1 x2h.10.1 in parallel")
    required: true
---

# Launch Agent Team: $ARGUMENTS

## MANDATORY: Worktree-isolated executors (no `team_name`)

For any spawn that creates N ≥ 2 concurrent executors, EACH spawn MUST use:

- `subagent_type: "executor"` (`.claude/agents/executor.md`)
- `isolation: "worktree"` — the Claude Code harness primitive
- `run_in_background: true` — so all executors run concurrently
- **NO `team_name` / NO `TeamCreate`.** Combining `team_name` (the
  `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` path) with `isolation: "worktree"`
  is silently downgraded by the harness: the executor is routed to the team
  coordination context and runs on the **shared main working copy** instead
  of an isolated worktree. This was observed first-hand (parallel executors
  clobbering `src/app.py`, a tree-wide `git stash` swallowing a sibling's
  uncommitted files). `team_name` is prohibited for parallel execution.

Coordinate via the **agentId returned by each spawn** (`SendMessage` accepts
the agentId as `to`) and the harness's automatic `<task-notification>` on
completion. Do NOT use `TeamCreate`, `TaskCreate`, or `SendMessage`-by-name.

Solo spawns (N = 1) MAY run on the main working copy and skip
`isolation: "worktree"`.

## Protocol

### Step 0: Resolve the team-lead HEAD SHA (binding)

Before any spawn, from the main repo root:

```bash
git rev-parse HEAD
```

Capture the full 40-char SHA. The harness allocates each worktree's branch
from the default branch tip, **not** from the team-lead's HEAD — so an
executor's worktree can start on a stale base. Thread this SHA literally
into every executor prompt; the executor resets to it on entry (see Step 2).

### Step 1: Provision ONE shared Postgres for the wave

Worktree-isolated executors each have their own working copy but
`agent-db.sh` derives its container name from `.claude/skills` (constant
across worktrees), so per-executor `agent-db` instances collide. Instead the
team-lead brings up ONE shared Postgres and threads its `DATABASE_URL` into
every executor prompt. The `integration_db` fixture creates a unique
database per test, so a single server is safe under concurrency.

```bash
eval $(bash .claude/skills/agent-db/agent-db.sh up)
echo "$DATABASE_URL"      # thread this literal value into every prompt
```

### Step 2: Plan the work

Break the prompt into parallel work items. For beads IDs, `bd show <id>` to
read description/acceptance criteria and confirm unblocked.

**Grouping:** one executor per non-overlapping file set. Two executors
editing the same source file still produce a merge conflict for the lead —
worktree isolation removes the project-wide-tooling race, not deliberate
same-file collisions. Treat shared allowlists
(`obligation_test_quality_allowlist.json`, `.duplication-baseline`,
guard allowlists) as lead-reconciled after the wave, never edited in parallel.

**Formula selection** (cook machinery lives in the dev-practices plugin):
- Bug → `bug-triage.yaml`
- Needs research / TDD / unknowns → `task-execute.yaml`
- Well-defined (test rewrite, mechanical, allowlist fix) → `task-single.yaml`

### Step 3: Spawn executors

For each work item, build the prompt from this template, substituting
`<HEAD_SHA>` (Step 0), `<DATABASE_URL>` (Step 1), `<TASK_IDS>`, `<FORMULA>`,
`<VAR>` (`BUG_IDS` for bug-triage, else `TASK_IDS`), and `<FILES>`:

```
Execute beads task(s): <TASK_IDS>

## Team-lead HEAD SHA at spawn time
<HEAD_SHA>

## Shared Postgres for this wave
export DATABASE_URL="<DATABASE_URL>"
export ADCP_TESTING=true
export ENCRYPTION_KEY="PEg0SNGQyvzi4Nft-ForSzK8AGXyhRtql1MgoUsfUHk="  # TEST ONLY
export GEMINI_API_KEY="test_key"
Do NOT run agent-db.sh or `docker compose up` — use the DATABASE_URL above.

## Worktree base reset (required, per .claude/commands/team.md Step 0)
On entry: `git rev-parse HEAD`. If it differs from <HEAD_SHA>, run
`git reset --hard <HEAD_SHA>` ONCE, then re-verify with `git rev-parse HEAD`.
Make every edit via paths inside your worktree so the commit captures them.

## Task work
Cook:
python3 /Users/konst/projects/pi-agentic-coding/plugins/dev-practices/skills/execute/scripts/cook_formula.py \
  --formula /Users/konst/projects/pi-agentic-coding/plugins/dev-practices/skills/execute/formulas/<FORMULA> \
  --var "<VAR>=<TASK_IDS>" --epic-title "Execute: <TASK_IDS>"
Then walk atoms: bd ready → bd show <atom> → execute → bd close <atom> → repeat.
Your files (stay in scope): <FILES>

## Commit (required, on your worktree branch)
Self-check `make quality` + targeted `tox -e integration -- -k <area>` against
the shared DATABASE_URL. Then `git commit` your scoped files on your
`worktree-agent-<id>` branch (NO `git add .`, NO `--no-verify`, NO `bd sync`,
NO AI/Claude attribution anywhere). Do NOT run `./run_all_tests.sh` (full
Docker stack collides with siblings) — the lead runs it once post-merge.
Do NOT close the parent beads task; the lead closes after merge+verify.

## Completion message
SendMessage to the team-lead with the structured report from
`.claude/agents/executor.md` "Communication on completion": entry HEAD,
whether a reset was needed, final HEAD, files changed, commit SHA + subject,
self-check results, and any follow-ups (do NOT file them yourself).
```

Spawn all executors **in a single message** for maximum parallelism:

```
Agent:
  description: "<short>"
  subagent_type: "executor"
  isolation: "worktree"
  run_in_background: true
  prompt: <template with substitutions>
```

Record each returned `agentId`.

### Step 4: Wait for completions

Executors run in the background; the harness sends a `<task-notification>`
per completion. Do NOT poll, sleep, or pre-read output files. If an executor
needs input it messages you — reply via `SendMessage` with its agentId.

### Step 5: Verify and merge each worktree sequentially

For each completed executor, in order:

1. Read its structured completion message.
2. Confirm lineage descends from `<HEAD_SHA>`:
   ```bash
   git -C .claude/worktrees/agent-<id> log --oneline -5
   ```
3. AI-attribution scan (project rule — none allowed):
   ```bash
   git -C .claude/worktrees/agent-<id> log --format='%B' <sha> -1 \
     | grep -iE "co-authored-by|generated .*with .*claude|noreply@anthropic|🤖" \
     && echo "LEAK — fix before merge" || echo "CLEAN"
   ```
4. Cherry-pick onto the main branch (linear history; conflicts rare under
   file-set discipline, resolve by hand — **never** auto-resolve `.beads/`):
   ```bash
   git cherry-pick <executor-commit-sha>
   ```

### Step 6: Authoritative full-suite gate (binding)

After ALL executor commits are merged, ONCE, on the merged tree:

```bash
./run_all_tests.sh
```

Executor `make quality` self-checks were informational. This merged-tree run
is the gate (it catches cross-file duplication, format drift, and
integration/e2e/bdd interactions no single worktree could see). JSON results
persist in `test-results/<ddmmyy_HHmm>/` — review those if terminal output
is lost.

**Test Integrity — ZERO TOLERANCE** (CLAUDE.md): never skip, `xfail`,
`--deselect`, or rationalize a failure as "pre-existing"/"infra". Fix it on
the merged tree and re-run, or report it as a blocker. Pre-existing failures
tracked in their own beads are reported explicitly, never silently accepted.

### Step 7: Close, commit, tear down

- Close fully-completed beads: `bd close <id> [...]`. **NEVER run `bd sync`
  or any bd-sync variant** (it overwrites shared JSONL and destroys tickets
  across worktrees — hard project rule).
- Stage precisely (never `git add .`); commit with a Conventional-Commits
  message, NO AI/Claude attribution.
- Tear down worktrees only AFTER all commits are merged:
  ```bash
  git worktree remove .claude/worktrees/agent-<id>
  ```
  Do not remove a worktree with unmerged changes; do not touch sibling
  worktrees or stashes.

## User's Request

$ARGUMENTS
