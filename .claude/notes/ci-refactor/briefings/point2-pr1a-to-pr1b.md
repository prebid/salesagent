## Cold-start briefing — Point 2: PR 1a (governance + ADRs) merged; PR 1b (workflow hardening) not yet started

**Where you are in the rollout**
- Calendar week: Week 1, Friday
- PRs merged: PR 1a (commits 1-7 of original PR 1 spec — SECURITY.md, CONTRIBUTING.md, CODEOWNERS, dependabot.yml, security.yml, codeql.yml, ADRs)
- PRs in flight: PR 1b (about to author — commits 8-11 of original PR 1 spec)
- PRs pending: PR 2, PR 3 (3-phase), PR 4, PR 5
- v2.0 phase PR coordination: v2.0 has not begun carving. Coordination still on Path 1

**What you can rely on (already true on main)**
- `SECURITY.md` exists at repo root — references `https://github.com/prebid/salesagent/security/advisories/new`
- `CONTRIBUTING.md` rewritten (≥80 lines, mentions the 11 frozen CI check names per D17)
- `docs/development/contributing.md` deleted or thin pointer
- `.github/CODEOWNERS` exists with `* @chrishuie` default + critical-path coverage of `.github/`, `.pre-commit-config.yaml`, `pyproject.toml`, `uv.lock`, `Dockerfile`, `alembic/`, `src/core/auth*`, `tests/unit/test_architecture_*.py`
- `.github/dependabot.yml` exists with 4 ecosystems (pip, github-actions, pre-commit, docker), no auto-merge, `open-pull-requests-limit: 5`, `ignore: adcp` per D16
- `.github/workflows/security.yml` exists (zizmor + pip-audit, scheduled Monday 06:00 PT)
- `.github/workflows/codeql.yml` exists with `continue-on-error: true` per D10 Path C (advisory)
- `.github/codeql/codeql-config.yml` exists
- `docs/decisions/adr-001-single-source-pre-commit-deps.md`, `adr-002-solo-maintainer-bypass.md`, `adr-003-pull-request-target-trust.md` all exist with `## Status` sections
- `pyproject.toml` has `[project.urls]` block, no placeholder description
- Branch protection: `@chrishuie` is on bypass list (post-merge admin step from PR 1a coordination notes — verify by checking if first contributor PR has been auto-routed to CODEOWNERS)
- First Dependabot run has fired (Saturday UTC after PR 1a's Friday merge); some PRs may already be open — review status

**What you must NOT do**
- Do not re-add anything PR 1a delivered (CODEOWNERS, ADRs, etc.)
- Do not advance into PR 2 territory: no mypy/black local-hook migration
- Do not modify CodeQL gating until Week 5 D10 tripwire — `continue-on-error: true` stays
- Do not bypass CODEOWNERS review on this PR (you're now under the regime PR 1a established; the bypass is for the maintainer's emergency use)
- Do not push to origin or open PR — user-owned

**Files you'll touch in this PR (heat map)**
- Primary (modify): `.pre-commit-config.yaml` (SHA-freeze the 4 external hooks at lines 262, 275, 281, 289 — but verify line numbers still match; v2.0 hasn't touched but always check). Each `rev:` becomes a 40-char SHA with `# frozen: v<tag>` trailing comment
- Primary (modify): `.github/workflows/test.yml`, `pr-title-check.yml`, `release-please.yml`, `ipr-agreement.yml` — every `uses: actions/<name>@v<X>` reference becomes `uses: actions/<name>@<40-char-sha>  # v<X>`. Add top-level `permissions:` block to each
- Primary (modify): `.github/workflows/test.yml:342` (Gemini key fallback → unconditional `GEMINI_API_KEY: test_key_for_mocking` per D15)
- Primary (new): `.github/zizmor.yml` (configure audit rules)
- Primary (modify): `.github/workflows/pr-title-check.yml`, `.github/workflows/ipr-agreement.yml` (add `# zizmor: ignore[dangerous-triggers]` allowlist comments referencing ADR-003)

**Verification environment**
- `make quality` green from PR 1a's merge — verify with `make quality`
- After each commit: `uv run pre-commit run --all-files` (catches SHA-freeze breakage early)
- After SHA-pin commit: `uvx zizmor .github/workflows/ --min-severity medium` should exit 0 (or 1 with only allowlisted findings)

**Specific commands to run FIRST (in order)**
1. `cd /Users/quantum/Documents/ComputedChaos/salesagent && git status` — must be clean
2. `git checkout main && git pull` — sync to latest (PR 1a is here)
3. `git checkout -b chore/ci-refactor-pr1b-workflow-hardening`
4. `gh pr list --author "app/dependabot" --state open --json number,title --jq '.[]| "#\(.number): \(.title)"'` — review Dependabot backlog (Week 1 deluge expected)
5. `cat .claude/notes/ci-refactor/.zizmor-preflight.txt` — re-read the findings list since CODEOWNERS now requires you to address each
6. Verify PR 1a artifacts on disk: `test -f SECURITY.md && test -f .github/CODEOWNERS && test -f docs/decisions/adr-001-single-source-pre-commit-deps.md && echo "PR 1a complete"`

**Decisions in effect (cite the ones that matter for this PR)**
D5 (Dependabot backlog tripwire — check before continuing), D12 (autoupdate --freeze procedure for SHA-freeze), D15 (Gemini fallback unconditional delete), D17 (frozen check names — must be referenced verbatim if you touch CONTRIBUTING.md)

**Risks active right now**
- R4 (zizmor 50+): now has actual numbers from `.zizmor-preflight.txt`. If you find more in the actual workflow files, file a follow-up issue
- R9 (Dependabot deluge): if backlog ≥5, PAUSE PR 1b authoring per D5 sustainability tripwire — clear backlog first

**Escalation triggers**
- Dependabot backlog ≥5 open PRs: STOP, clear them before continuing
- Any `pre-commit autoupdate --freeze` bump breaks `pre-commit run --all-files`: hold the offending hook at previous version with explicit `pre-commit autoupdate --freeze --repo <url>` for the others (per D12)
- zizmor surfaces unexpected dangerous-triggers in any workflow not covered by ADR-003: STOP, write `.claude/notes/ci-refactor/escalations/pr1b-extra-dangerous-trigger.md` with details

**How to resume the work**
Read `.claude/notes/ci-refactor/pr1-supply-chain-hardening.md` §"Internal commit sequence" commits 8-11. Use the SHA-resolution loop in commit 9 verbatim. Each commit is verified by a one-liner; run them.

---
