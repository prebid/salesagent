# Escalations

Executor agents write `escalations/pr<N>-<topic>.md` and STOP when they hit a wall they
cannot resolve in 15 minutes (per `templates/executor-prompt.md` Rule 17).

## File naming

`pr<N>-<topic>.md` — examples:
- `pr3-phase-b-422.md`
- `pr1-zizmor-100-findings.md`
- `pr4-deletion-failure.md`
- `pr2-mypy-explosion.md`

## File contents

Each escalation file should contain, in this order:

1. **What the agent was trying to do** (the operation, the PR, the commit number).
2. **What blocked them** — paste exact command output, error messages, line numbers.
3. **What they tried** before escalating (commands, alternative approaches).
4. **What they think should happen** — the agent's hypothesis for resolution.

Then STOP. The agent's terminal message MUST be the single line:

```
ESCALATION: see escalations/pr<N>-<topic>.md
```

Per Rule 17, this is the user notification mechanism — do not bury the escalation in prose.

## Resolution lifecycle

When the user resolves an escalation, the file moves to `escalations/resolved/<filename>` with
a one-line resolution note appended at the top. The agent resumes from the user's resolution.

## Why this directory exists

The escalation protocol was unwritten until the 2026-04-25 P0 sweep (Round 5 ops-readiness
reviewer found "first escalation will ENOENT" because the directory didn't exist). This
directory + naming convention closes that gap.
