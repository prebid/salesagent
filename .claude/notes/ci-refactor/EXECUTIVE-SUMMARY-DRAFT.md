# B. The "if you only read ONE file" doc

**Recommendation: CREATE this file.** Currently no such file exists; the closest is `00-MASTER-INDEX.md` which is too thin (75 lines) and `03-decision-log.md` which is too detailed (192 lines of policy). A 150-200-line executive summary would let a fresh agent operate from a single read.

**Suggested location:** `.claude/notes/ci-refactor/EXECUTIVE-SUMMARY.md`

**Sketch (~180 lines content):**

```markdown
# CI/Pre-commit Refactor — Executive Summary

If you have time to read ONE file before being parachuted in, this is it.

## What is this?
A 5-PR rollout (issue #1234) that brings salesagent to top-tier OSS supply-chain
posture. ~14-18 engineer-days, ~5 calendar weeks. PRs land sequentially.

## Where to find everything
- This file: executive summary; read first
- 00-MASTER-INDEX.md: status table, calendar, success criteria
- 03-decision-log.md: 21 locked decisions (D1-D21) + 4 open (D-pending-1..4)
- 02-risk-register.md: top 10 risks (R1-R10) + tripwires
- 01-pre-flight-checklist.md: admin steps (A1-A10) + agent steps (P1-P6)
- pr<N>-<slug>.md: per-PR spec (load only the one for your current PR)
- templates/executor-prompt.md: agent operating contract
- escalations/: where you write if you can't proceed

## The 5 PRs (sequencing)
| PR | What it does | Effort | Blocks |
|---|---|---|---|
| 1 | Governance + supply-chain hardening (CODEOWNERS, SECURITY.md, dependabot, zizmor, CodeQL advisory, SHA-pin all hooks/actions) | 2.5 days | 2, 3 |
| 2 | uv.lock as single source: replace mirrors-mypy and psf/black with local hooks; delete [project.optional-dependencies].dev; re-enable pydantic.mypy | 4-6 days | 3 |
| 3 | CI authoritative + reusable workflows; 11 frozen check names; 3-phase merge (overlap → atomic flip → cleanup) | 3-4 days | 4 |
| 4 | Hook relocation: 5 grep hooks → AST guards; 5 to pre-push; 4 to CI-only; 6 deleted | 2 days | 5 |
| 5 | Version consolidation: Python, Postgres, uv anchors single-sourced; black/ruff py312 | 2 days | none |

## The 21 locked decisions in one screen
- D1: Solo maintainer (@chrishuie sole CODEOWNERS)
- D2: Branch protection + @chrishuie bypass (ADR-002)
- D3: Mypy in CI, not pre-commit
- D4: Signed commits deferred
- D5: NEVER auto-merge Dependabot (sustainability tripwire: 5 backlog → recruit)
- D6: No merge queue
- D7: Pre-commit (not prek) for CI; prek optional-local
- D8: No pre-commit.ci
- D9: .claude/ out of scope; CLAUDE.md guards table updated in PR 4
- D10: CodeQL Path C — advisory 2 weeks, gating Week 5
- D11: Coverage advisory 4 weeks at 53.5%
- D12: pre-commit autoupdate --freeze
- D13: Fix pydantic.mypy errors in PR 2 (tripwire >200)
- D14: Migrate ui-tests extras → dependency-groups
- D15: Delete Gemini key fallback (unconditional mock)
- D16: Dependabot ignores adcp until #1217 merges
- D17: 11 frozen CI check names (verbatim list)
- D18: 27 existing guards + 4 in PR 4 + 9 in v2.0 = 41 final
- D19: Per-PR specs, not master doc
- D20: Path 1 sequencing (#1234 first, v2.0 rebases)
- D21: Root CONTRIBUTING.md is canonical

## The 11 frozen CI check names (D17)
CI / Quality Gate, CI / Type Check, CI / Schema Contract, CI / Unit Tests,
CI / Integration Tests, CI / E2E Tests, CI / Admin UI Tests, CI / BDD Tests,
CI / Migration Roundtrip, CI / Coverage, CI / Summary

## What you must NEVER do (any PR)
- Push to origin (user owns this)
- Run gh pr create (user owns this)
- Mutate branch protection via gh api -X PATCH (user owns this — admin only)
- Use --no-verify, --ignore, --deselect, pytest.mark.skip to bypass failures
- Bundle CSRF middleware into PR 1 (deferred to v2.0)
- Auto-merge Dependabot PRs
- Touch files outside your PR's spec scope

## When to STOP (escalation triggers)
- Branch-protection action requested → admin only; ask user
- Mypy delta >200 in PR 2 → D13 tripwire; comment out plugin, file follow-up
- Phase A check fails on main → don't flip; investigate
- harden-runner block-mode locks out CI → inverse audit-mode
- Dependabot backlog ≥5 → pause forward work, clear backlog (D5)
- Test fails you can't diagnose in 15 min → escalate to user

## How to escalate
Write `.claude/notes/ci-refactor/escalations/pr<N>-<topic>.md` with:
- What you were trying to do
- What blocked you (with command output)
- What you tried
- What you think should happen
Then STOP. The user reads, decides, you resume.

## After you finish a PR
1. All commits on local branch (NOT pushed)
2. Run full verification: bash .claude/notes/ci-refactor/scripts/verify-pr<N>.sh
3. Run ./run_all_tests.sh
4. Generate PR description from templates/pr-description.md
5. Update 00-MASTER-INDEX.md status row to "ready"
6. Report to user; user owns push and PR creation
```

This file would be ~3k tokens, replacing the ~5k of items 1-3 in the minimal bundle. Net savings: 2-3k tokens of cold-start. Worth creating.

---
