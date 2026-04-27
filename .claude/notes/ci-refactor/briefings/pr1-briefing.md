# PR 1 — Supply-chain hardening

## Briefing
**Where we are.** Week 1. Nothing has merged yet. You are the first executor; `@chrishuie` has completed pre-flight A1-A25, P1-P10. Calendar position: rollout opens.

**What this PR does.** Adds the supply-chain governance layer: CODEOWNERS auto-routes reviews to `@chrishuie`; `dependabot.yml` opens grouped weekly PRs (capped 5/ecosystem, no auto-merge per D5); `SECURITY.md` exposes the private-vuln channel; root `CONTRIBUTING.md` becomes a thin pointer to `docs/development/contributing.md` (594 lines — canonical content, per D21); `.pre-commit-config.yaml` external `rev:` strings are SHA-frozen via `pre-commit autoupdate --freeze` per D12 — **psf/black HELD at 25.1.0** per ADR-008 (autoupdate would jump to 26.3.0 with 2026-style reformat — deferred to post-#1234 PR); every workflow `uses:` line is SHA-pinned with a `# v<tag>` trailing comment; `permissions:` blocks added to every workflow (BOTH `release-please.yml` AND `ipr-agreement.yml` already have top-level — verified disk-truth); **`persist-credentials: false`** on every `actions/checkout`; **codeql-action pinned to v4** (was v3 — v3 deprecates Dec 2026); `security.yml` (zizmor + pip-audit + **pinact + actionlint**) and `codeql.yml` (advisory per D10 Path C) ship; ADR-001/2/3 land. **Commit 10 (Gemini) MOVED to PR 3** (PR 3 rewrites test.yml wholesale; redundant to fix it twice). Drift items closed: PD3, PD4, PD5, PD6, PD7, PD13, PD14, PD15, PD23.

**Pre-flight dependencies.** A24 (Phase B dry-run on sandbox repo) and A25 (hardware MFA on @chrishuie + ADR-010 SPOF acceptance) were added to pre-flight scope. These are NOT PR 1 prerequisites — PR 1 can land without them — but they DO block PR 3 Phase B. Situational awareness only.

**You can rely on (already done).** Pre-flight artifacts in `.claude/notes/ci-refactor/`: `branch-protection-snapshot.json`, `branch-protection-snapshot-required-checks.json`, `.zizmor-preflight.txt` (~35 findings), guards-on-disk.txt, OpenSSF Scorecard baseline, `mypy-plugins-baseline.txt` (A13). `@chrishuie` has set CodeQL to "Advanced" mode (A5), enabled Dependabot + private vuln reporting (A3, A4), audited `allow_auto_merge` to be `false` (A11 — R30 mitigation), drained Dependabot queue ≤2 (A12), and confirmed bypass-list addition feasibility (A14).

**You CANNOT do.** No `git push`, no `gh pr create` (`feedback_user_owns_git_push.md`). No `gh api -X PATCH` mutations on branches/main. No flipping CodeQL `continue-on-error: true` to `false` — that's a Week 5 admin action. No editing `src/admin/` (v2.0 territory). No introducing CSRF middleware (deferred to v2.0's `src/admin/csrf.py`).

**Concurrent activity.** PR #1217 (adcp 3.12) is open; PR 1 doesn't touch `adcp` so tolerated. v2.0 phase PRs may land on `pyproject.toml` — rebase mechanically, but do NOT re-introduce `[project.optional-dependencies].dev` if v2.0 has deleted it.

**Files (heat map).**
- Heavy edits: `.pre-commit-config.yaml` (lines 263, 276, 282, 290 SHA-freeze), `CONTRIBUTING.md` (full rewrite), all 4 `.github/workflows/*.yml` (perms + SHA-pinning).
- New files: `.github/CODEOWNERS`, `.github/dependabot.yml`, `.github/workflows/security.yml`, `.github/workflows/codeql.yml`, `.github/codeql/codeql-config.yml`, `.github/zizmor.yml`, `SECURITY.md`, `docs/decisions/adr-001-…md`, `adr-002-…md`, `adr-003-…md`.
- One-line edit: `pyproject.toml` (description + `[project.urls]`).
- **MOVED to PR 3: Gemini fallback fix** (`test.yml:342`) — PR 3 rewrites test.yml wholesale; the fix lands in `_pytest/action.yml`'s env block as part of PR 3's commit sequence.
- DO NOT touch: anything in `src/`, `tests/`, `.guard-baselines/`, `mypy.ini`.

**Verification environment.** Before starting, `make quality` should be green on main. You'll need: `uv` (project venv), `gh` authenticated, `yamllint` (`uv add --dev yamllint` if missing), `uvx zizmor` (no install needed; uvx fetches). Tooling caveat: `pre-commit autoupdate --freeze` requires network; if PyPI is flaky, retry per-hook with `--repo <url>`.

**Escalation triggers (this PR).**
- `.zizmor-preflight.txt` shows >50 medium+ findings → file follow-up issue, fix only load-bearing (excessive-permissions, dangerous-triggers); allowlist with comments.
- `pre-commit autoupdate --freeze` breaks `pre-commit run --all-files` on main → hold the breaking hook at previous SHA; document in PR description.
- A workflow `uses:` line points to an action whose tag ref doesn't resolve to a commit SHA via `gh api` → check the action repo manually; if it's a release-only tag, document in PR.
- CodeQL on first run shows >99 findings → STOP, write to `.claude/notes/ci-refactor/escalations/pr1-codeql-findings.md`, surface to user. (D10 expects ~99; >99 means the projection was wrong.)

**Key facts (you must know).**
1. zizmor's CLI mutually excludes `--annotations` and `--advanced-security`; if you write `.github/zizmor.yml`, don't enable both.
2. The external pre-commit hooks that get SHA-frozen are: `pre-commit-hooks` (line 263), `astral-sh/ruff-pre-commit` (282), `pre-commit/mirrors-mypy` (290 — replaced in PR 2). **`psf/black` is INTENTIONALLY OMITTED from autoupdate-freeze** per ADR-008 — autoupdate would jump 25.1.0 → 26.3.0 (2026-style global reformat). Use `pre-commit autoupdate --freeze --repo <url>` for each non-black hook. Don't worry that mirrors-mypy is about to be deleted in PR 2; freeze it anyway for clean blame.
3. The CONTRIBUTING.md outline in the spec §"Embedded CONTRIBUTING.md outline" lists the 14 frozen check names from D17 — paste them verbatim.
4. `[project.urls]` block must use these 5 keys (matched by `verify-pr1.sh`): `Homepage`, `Repository`, `Issues`, `Documentation`, `Changelog`. (Earlier "Source" framing was wrong; spec uses `Repository`.)
5. ADR-001 is referenced by PR 2 but committed here so the directory exists. ADR-002 documents the bypass; ADR-003 documents `pull_request_target` trust boundary for `pr-title-check.yml` + `ipr-agreement.yml`.
