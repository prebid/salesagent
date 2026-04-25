## Cold-start briefing — Point 1: Pre-flight just completed; PR 1 has not yet started

**Where you are in the rollout**
- Calendar week: Week 1, Monday/Tuesday
- PRs merged: none
- PRs in flight: none (PR 1 about to be authored)
- PRs pending: PR 1, PR 2, PR 3 (3-phase), PR 4, PR 5; PR 6 (Fortune-50 hardening) is post-rollout follow-up
- v2.0 phase PR coordination: v2.0 (PR #1221) is open as planning artifact; has not begun carving. Path 1 sequencing chosen per D20 — issue #1234 lands first

**What you can rely on (already true)**
- A1: `branch-protection-snapshot.json` saved at `.claude/notes/ci-refactor/branch-protection-snapshot.json` (the rollback target for PR 3 Phase B)
- A2: `required-checks-current.txt` saved at `.claude/notes/ci-refactor/required-checks-current.txt`
- A3: GitHub private vulnerability reporting confirmed enabled on the repo
- A4: Dependabot alerts + security updates confirmed enabled
- A5: CodeQL set to "Advanced" (confirmation that PR 1's custom workflow takes precedence)
- A6: PR #1217 fate decided — "land around it" default (PR 2 spec tolerates merge mid-review)
- A7: Coverage baseline captured (~55.56% per agent inspection 2026-04). `.coverage-baseline = 53.5` per D11
- A8: Pre-commit warm latency baseline captured (issue claimed 18-30s; actual number is in your snapshot)
- A9: OpenSSF Scorecard baseline captured (target post-rollout ≥7.5/10)
- A10: CSRF Path C plan confirmed (advisory CodeQL for 2 weeks, flip to gating Week 5)
- P1: drift evidence re-verified (line numbers in `.pre-commit-config.yaml` and `pyproject.toml` still valid)
- P2: `.mypy-baseline.txt` captured (errors-before count for PR 2)
- P3: `.zizmor-preflight.txt` captured (~35 expected findings)
- P4: PR #1221 file-overlap matrix verified
- P5: `guards-on-disk.txt` snapshot saved (26-27 entries)
- P6: `tests/ui/` confirmed live; D14 plan stands

**What you must NOT do**
- Do not modify branch protection — D2 admin actions are owned by `@chrishuie` (per `feedback_user_owns_git_push.md`)
- Do not run `gh pr create` or `git push` — user owns remote operations
- Do not bundle CSRF middleware into PR 1 (D10 chose Path C; Flask-WTF CSRFProtect is deferred to v2.0)
- Do not touch any file under `src/` (production code is untouched in PR 1)
- Do not pre-empt PR 2's territory: no mypy/black local-hook migration here
- Do not add `harden-runner` (D-pending-4 deferred to PR 6 follow-up)

**Files you'll touch in this PR (heat map)**
- Primary (new): `SECURITY.md`, `.github/CODEOWNERS`, `.github/dependabot.yml`, `.github/workflows/security.yml`, `.github/workflows/codeql.yml`, `.github/codeql/codeql-config.yml`, `.github/zizmor.yml`, `docs/decisions/adr-001-…md`, `docs/decisions/adr-002-…md`, `docs/decisions/adr-003-…md`
- Primary (modify): `pyproject.toml` (description + `[project.urls]`), `CONTRIBUTING.md` (rewrite), `.pre-commit-config.yaml` (SHA-freeze 4 hooks), all 4 `.github/workflows/*.yml` files (SHA-pin actions, add top-level `permissions:`)
- Secondary (delete or thin pointer): `docs/development/contributing.md`
- Do not touch: `.github/workflows/test.yml` integration sections (PR 3 territory), `mypy.ini`, anything under `src/`

**Verification environment**
- `make quality` should report green BEFORE you start. Verify: `cd /Users/quantum/Documents/ComputedChaos/salesagent && make quality`
- After each commit, `make quality` must still be green
- Tooling: `uv` installed; `gh` authenticated with classic PAT having `repo` scope (or fine-grained `Administration: read`); `yamllint` available via pip if needed; `uvx zizmor` and `uvx pip-audit` work via `uv`

**Specific commands to run FIRST (in order)**
1. `cd /Users/quantum/Documents/ComputedChaos/salesagent && git status` — must be clean
2. `git checkout main && git pull` — sync to latest
3. `git checkout -b chore/ci-refactor-pr1-supply-chain-hardening`
4. `cat .claude/notes/ci-refactor/.zizmor-preflight.txt | head -50` — review the actual findings count
5. `cat .claude/notes/ci-refactor/.mypy-baseline.txt | head -10` — note PR 2's pre-flight context
6. `make quality` — confirm green baseline before changes

**Decisions in effect (cite the ones that matter for this PR)**
D1, D2, D5, D10, D12, D15, D16, D17, D21. Most load-bearing: D10 (Path C — CodeQL advisory, `continue-on-error: true` on `analyze` step), D12 (autoupdate --freeze procedure), D17 (the 11 frozen check names — cited in CONTRIBUTING.md, NOT enforced yet)

**Risks active right now**
- R3: CodeQL findings explode — mitigated by D10 Path C; PR 1 ships advisory
- R4: zizmor finds >50 — `.zizmor-preflight.txt` shows actual count; if >50, fix only load-bearing findings, file follow-up issue for the rest
- R9: Dependabot deluge Week 1 — `open-pull-requests-limit: 5` per ecosystem caps initial cycle ~13 PRs

**Escalation triggers**
- zizmor count >50: file follow-up issue, descope to load-bearing findings only
- `pre-commit autoupdate --freeze` breaks any hook on main: hold individual hooks at previous version per D12 tripwire
- Any branch-protection action requested: STOP — admin action belongs to user
- CodeQL workflow can't be added because Advanced mode wasn't actually flipped: re-run A5 procedure

**How to resume the work**
Read `.claude/notes/ci-refactor/pr1-supply-chain-hardening.md` end-to-end. It has 11 internal commits, each self-contained with verification one-liners. Use templates/executor-prompt.md as your operating contract. Each commit corresponds to ~1 file group; do not batch.

---
