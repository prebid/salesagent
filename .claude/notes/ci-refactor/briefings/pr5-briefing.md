# PR 5 — Cross-surface version consolidation

## Briefing
**Where we are.** Week 5. PR 1-4 merged. Calendar: closing the rollout.

**What this PR does.** Single source of truth per dimension. Python: `.python-version` referenced via `python-version-file:` everywhere; `Dockerfile` uses `ARG PYTHON_VERSION`. Postgres: every reference `postgres:17-alpine`. uv: `COPY --from=ghcr.io/astral-sh/uv:<version>` in Dockerfile; `version:` pin in setup-uv. Bumps `[tool.black].target-version` and `[tool.ruff].target-version` from `py311` → `py312`. Drift closed: PD9, PD10, PD11, PD12.

**You can rely on.** PR 3 Phase C deleted `test.yml` — most of those references are gone. PR 4 hook latency stable. `_pytest.yml` already uses `postgres:17-alpine`. Guards backfill from PR 4 means `@pytest.mark.architecture` is registered.

**You CANNOT do.** Bump Python beyond 3.12; bump uv beyond 0.11.6 pin; bump Postgres beyond 17. Add Fortune-50 patterns (harden-runner, SBOM) — PR 6.

**Concurrent activity.** v2.0 phase PRs landing on `pyproject.toml` lines 117 (black) or 138 (ruff) are HIGH conflict; coordinate. Dockerfile FROM lines = medium.

**Files.** `Dockerfile`, `.github/workflows/_pytest.yml` + `ci.yml`, `.github/actions/setup-env/action.yml`, `pyproject.toml`, new `tests/unit/test_architecture_uv_version_anchor.py`. Plus reformat diff (black + ruff target-version bumps).

**Escalation.** PG17 regression in integration tests; large reformat diff (>100 files separate-commit it).

**Key facts.**
1. uv 0.11.6 pinned everywhere — drift between `Dockerfile ARG UV_VERSION` and `setup-env action default` is the new structural guard.
2. PG15 → PG17: dev compose already uses 17, so this is mostly aligning CI. `_pytest.yml` already uses 17-alpine.
3. Black target-version is a list `['py312']` (note the brackets); ruff is a string `"py312"`.
4. `[tool.ruff] --select UP` autofix may produce a large diff — separate commit (commit 7) recommended.
