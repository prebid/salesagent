# PR 1 — Supply-chain hardening

## Briefing
**Where we are.** Week 1. Nothing has merged yet. You are the first executor; `@chrishuie` has completed pre-flight A1-A10, P1-P6. Calendar position: rollout opens.

**What this PR does.** Adds the supply-chain governance layer: CODEOWNERS auto-routes reviews to `@chrishuie`; `dependabot.yml` opens grouped weekly PRs (capped 5/ecosystem, no auto-merge per D5); `SECURITY.md` exposes the private-vuln channel; `CONTRIBUTING.md` rewritten from 20 → ~120 lines per D21; `.pre-commit-config.yaml` external `rev:` strings are SHA-frozen via `pre-commit autoupdate --freeze` per D12; every workflow `uses:` line is SHA-pinned with a `# v<tag>` trailing comment; `permissions:` blocks added to every workflow; `security.yml` (zizmor + pip-audit) and `codeql.yml` (advisory per D10 Path C) ship; ADR-001/2/3 land. Drift items closed: PD3, PD4, PD5, PD6, PD7, PD13, PD14, PD15, PD23, PD24.

**You can rely on (already done).** Pre-flight artifacts in `.claude/notes/ci-refactor/`: `branch-protection-snapshot.json`, `branch-protection-snapshot-required-checks.json`, `.zizmor-preflight.txt` (~35 findings), guards-on-disk.txt, OpenSSF Scorecard baseline. `@chrishuie` has set CodeQL to "Advanced" mode (A5) and enabled Dependabot + private vuln reporting (A3, A4).

**You CANNOT do.** No `git push`, no `gh pr create` (`feedback_user_owns_git_push.md`). No `gh api -X PATCH` mutations on branches/main. No flipping CodeQL `continue-on-error: true` to `false` — that's a Week 5 admin action. No editing `src/admin/` (v2.0 territory). No introducing CSRF middleware (deferred to v2.0's `src/admin/csrf.py`).

**Concurrent activity.** PR #1217 (adcp 3.12) is open; PR 1 doesn't touch `adcp` so tolerated. v2.0 phase PRs may land on `pyproject.toml` — rebase mechanically, but do NOT re-introduce `[project.optional-dependencies].dev` if v2.0 has deleted it.

**Files (heat map).**
- Heavy edits: `.pre-commit-config.yaml` (lines 262, 275, 281, 289 SHA-freeze), `CONTRIBUTING.md` (full rewrite), all 4 `.github/workflows/*.yml` (perms + SHA-pinning).
- New files: `.github/CODEOWNERS`, `.github/dependabot.yml`, `.github/workflows/security.yml`, `.github/workflows/codeql.yml`, `.github/codeql/codeql-config.yml`, `.github/zizmor.yml`, `SECURITY.md`, `docs/decisions/adr-001-…md`, `adr-002-…md`, `adr-003-…md`.
- One-line edit: `pyproject.toml` (description + `[project.urls]`), `.github/workflows/test.yml:342` (Gemini key).
- DO NOT touch: anything in `src/`, `tests/`, `.guard-baselines/`, `mypy.ini`.

**Verification environment.** Before starting, `make quality` should be green on main. You'll need: `uv` (project venv), `gh` authenticated, `yamllint` (`uv add --dev yamllint` if missing), `uvx zizmor` (no install needed; uvx fetches). Tooling caveat: `pre-commit autoupdate --freeze` requires network; if PyPI is flaky, retry per-hook with `--repo <url>`.

**Escalation triggers (this PR).**
- `.zizmor-preflight.txt` shows >50 medium+ findings → file follow-up issue, fix only load-bearing (excessive-permissions, dangerous-triggers); allowlist with comments.
- `pre-commit autoupdate --freeze` breaks `pre-commit run --all-files` on main → hold the breaking hook at previous SHA; document in PR description.
- A workflow `uses:` line points to an action whose tag ref doesn't resolve to a commit SHA via `gh api` → check the action repo manually; if it's a release-only tag, document in PR.
- CodeQL on first run shows >99 findings → STOP, write to `.claude/notes/ci-refactor/escalations/pr1-codeql-findings.md`, surface to user. (D10 expects ~99; >99 means the projection was wrong.)

**Key facts from prior rounds (you must know).**
1. zizmor's CLI mutually excludes `--annotations` and `--advanced-security`; if you write `.github/zizmor.yml`, don't enable both.
2. The 4 external pre-commit hooks that get SHA-frozen are: `pre-commit-hooks` (line 262), `psf/black` (276 — note PR 2 will replace this entirely), `astral-sh/ruff-pre-commit` (281), `pre-commit/mirrors-mypy` (289 — also replaced in PR 2). Don't worry that 2 of the 4 are about to be deleted; freeze them anyway for clean blame.
3. The CONTRIBUTING.md outline in the spec §"Embedded CONTRIBUTING.md outline" lists the 11 frozen check names from D17 — paste them verbatim.
4. `[project.urls]` block must use SPDX-shaped keys (`Homepage`, `Source`, `Issues`, `Changelog`).
5. ADR-001 is referenced by PR 2 but committed here so the directory exists. ADR-002 documents the bypass; ADR-003 documents `pull_request_target` trust boundary for `pr-title-check.yml` + `ipr-agreement.yml`.
