# Consolidated Landing Schedule

## DELIVERABLE 3 — Consolidated landing schedule

### Calendar

| Week | PR opens | PR merges | Decisions land | Admin actions (operator) |
|---|---|---|---|---|
| **Week 0 (pre-flight)** | — | — | A1-A25, P1-P10 | snapshot branch protection, capture baselines, enable Dependabot+CodeQL Advanced, audit `allow_auto_merge`=false (A11), drain Dependabot queue (A12), snapshot mypy plugin block (A13), confirm @chrishuie bypass-list feasibility (A14), Phase B sandbox dry-run (A24), hardware MFA on @chrishuie + ADR-010 SPOF acceptance (A25) |
| **Week 1 (Fri-Sun start)** | PR 1 (Mon) | PR 1 (Fri) | D10 Path C confirmed | post-merge: configure `@chrishuie` bypass; weekend Dependabot triage |
| **Week 2** | PR 2 (Tue) | PR 2 (Thu) | D13 pydantic delta verified | re-run OpenSSF Scorecard (delta vs A9) |
| **Week 3** | PR 3 Phase A (Mon) | PR 3 Phase A (Wed) | D17 freeze active | 48h soak window observed on 2-3 real PRs |
| **Week 4** | — (Phase B is admin-only) | PR 3 Phase B Mon (admin per A24+A25 prereqs); Phase C Wed (after 48h soak); soak/monitor | D11 coverage gating decision | **atomic `gh api -X PATCH` flip via `flip-branch-protection.sh`** (D45 day-of-week guard enforced; FORBIDDEN Fri/Sat/Sun + holiday eve), admin verification PR, then test.yml deletion |
| **Week 5** | PR 4 (Mon), PR 5 (Tue) | PR 4 (Mon) + PR 5 (Tue/Thu); flip CodeQL to gating | D10 tripwire (CodeQL → gating?) | flip CodeQL `continue-on-error` off if findings ≤ 5 |
| **Week 6 (slack)** | PR 6 (optional) | PR 6 if greenlit | D25 (harden-runner v2.19.0+ adoption) | final OpenSSF Scorecard ≥ 7.5; close #1234 |

**Phase B day-of-week guard (D45):** `flip-branch-protection.sh` enforces Mon-Thu execution. Override possible with `FORCE=1` env var (NOT RECOMMENDED — see R11C-02 for solo-maintainer weekend lockout cascade).

**Phase B prerequisites (Round 13):** A24 (Phase B dry-run on sandbox) + A25 (hardware MFA on @chrishuie + ADR-010 SPOF acceptance documented) MUST be complete before Phase B execution. See `01-pre-flight-checklist.md`.

### Dependency graph

```
[Pre-flight A1-A25 + P1-P10]
        │
        ▼
    [PR 1] ───── establishes: SHA-pin convention, CODEOWNERS, dependabot, ADR-001/2/3
        │
        ├──────────────► [PR 6 image supply chain] (independent; can run anytime ≥ Week 5)
        │
        ▼
    [PR 2] ───── establishes: local hooks, uv.lock as SoT, ui-tests group, pydantic.mypy live
        │
        ▼
[PR 3 Phase A] ──── new ci.yml runs alongside test.yml (≥48h soak)
        │
        ▼
[PR 3 Phase B] ──── ATOMIC FLIP (admin only, ~5 min window) — HIGHEST RISK
        │
        ▼
[PR 3 Phase C] ──── delete test.yml (≥48h after Phase B stable)
        │
        ├──────────────► [PR 5] (independent of PR 4)
        │
        ▼
    [PR 4] ───── delete absorbed hooks; warm latency 23s → ~1.7s
        │
        ▼
    [PR 5] ───── version anchor consolidation (Python/Postgres/uv); py312 target bumps DEFERRED per D28
        │
        ▼
    [Close #1234; final Scorecard ≥ 7.5]
```

### Critical-path callouts

1. **PR 3 Phase B is the highest-risk single moment in the rollout.** A wrong `gh api -X PATCH` body locks merging on main. Mitigation: pre-flight A1+A2 saved the inverse JSON; rollback is a 5-minute one-liner. Operator runs this; agents do NOT.
2. **PR 4 must wait for PR 3 Phase C** — if hooks are deleted before `CI / Quality Gate` is required, main loses enforcement.
3. **PR 1 lands on a Friday** — Saturday Dependabot cron has weekend triage runway; backlog tripwire is 5 PRs.
4. **PR 2 commit 4 must precede commit 5** — `--extra dev` callsites must move to `--group dev` BEFORE the `[project.optional-dependencies].dev` block is deleted, or CI breaks.
5. **PR 4 commits 1-3 (guards) must merge GREEN before commits 5-7 (hook deletes)** — same PR, but ordering enforces "guards pass on main BEFORE delete." Reviewers verify by reading the commit list, not by running tests at HEAD.

---
