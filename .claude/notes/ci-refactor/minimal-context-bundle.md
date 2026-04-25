# A. The "minimal context bundle" for any fresh agent

The set of files an agent MUST read (in order) to be operational. Token estimates are approximate (lines × ~12 tokens/line average for markdown).

| Order | File | Lines | Token estimate | Purpose |
|---|---|---|---|---|
| 1 | `.claude/notes/ci-refactor/00-MASTER-INDEX.md` | 75 | ~1k | Status overview, sequencing, success criteria |
| 2 | `.claude/notes/ci-refactor/03-decision-log.md` | 192 | ~2.5k | Every locked decision; cited by D# in specs |
| 3 | `.claude/notes/ci-refactor/02-risk-register.md` | 128 | ~1.5k | Top 10 risks with mitigation/rollback |
| 4 | `.claude/notes/ci-refactor/01-pre-flight-checklist.md` | 198 | ~2.5k | Admin actions A1-A10 + agent steps P1-P6 |
| 5 | The per-PR spec for the PR you're working on (one of) | 280-1010 | 4-12k | Source of truth for that PR |
| 6 | `.claude/notes/ci-refactor/templates/executor-prompt.md` | 121 | ~1.5k | Operating contract |
| 7 | `CLAUDE.md` (project root) | ~700 | ~9k | Codebase patterns; non-negotiable |

**Total cold-start budget: ~22-28k tokens** depending on which PR's spec you load. Read order matters: status → decisions → risks → pre-flight → spec → contract → patterns. The "if you only read ONE file" doc (B below) collapses items 1-3 into a single 200-line executive summary, dropping cold-start to ~14-20k.

---
