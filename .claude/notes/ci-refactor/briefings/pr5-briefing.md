# PR 5 — Cross-surface version consolidation

## Briefing
**Where we are.** Week 5. PR 1-4 merged. Calendar: closing the rollout.

**What this PR does.** Single source of truth per dimension. Python: `.python-version` referenced via `python-version-file:` everywhere; `Dockerfile` uses `ARG PYTHON_VERSION`. Postgres: every reference `postgres:17-alpine`. uv: `COPY --from=ghcr.io/astral-sh/uv:<version>` in Dockerfile; `version:` pin in setup-uv. **Black + ruff target-version bumps DEFERRED per D28 (P0 sweep — separate post-#1234 PR per ADR-008).** Drift closed: PD9 (partial — Python/uv/Postgres anchors only), PD10, PD11, PD12.

**You can rely on.** PR 3 Phase C deleted `test.yml` — most of those references are gone. PR 4 hook latency stable. `_pytest/action.yml` (composite — Decision-4 from P0 sweep) and `ci.yml` per-job services already use `postgres:17-alpine`. Guards backfill from PR 4 means `@pytest.mark.arch_guard` is registered.

**You CANNOT do.** Bump Python beyond 3.12; bump uv beyond 0.11.6 pin; bump Postgres beyond 17. Add Fortune-50 patterns (harden-runner, SBOM) — PR 6.

**Concurrent activity.** v2.0 phase PRs landing on `pyproject.toml` lines 117 (black) or 138 (ruff) are HIGH conflict; coordinate. Dockerfile FROM lines = medium.

**Files.** `Dockerfile`, `.github/workflows/ci.yml`, `.github/actions/_pytest/action.yml` (composite, NOT `_pytest.yml`), `.github/actions/setup-env/action.yml`, `pyproject.toml` (Python anchor only — target-version bumps deferred per D28), new `tests/unit/test_architecture_uv_version_anchor.py`.

**Escalation.** PG17 regression in integration tests; large reformat diff (>100 files separate-commit it).

**Key facts.**
1. uv 0.11.6 pinned everywhere — drift between `Dockerfile ARG UV_VERSION` and `setup-env action default` is the new structural guard.
2. PG15 → PG17: dev compose already uses 17 (verified disk-truth); CI's `ci.yml` per-job `services: postgres:` blocks (Decision-4 — services live in caller, not composite) already use `postgres:17-alpine`. PR 5 commit 2 only needs to align Dockerfile (and any remaining workflow stragglers).
3. **Target-version bump deferred per D28 (P0 sweep — ADR-008).** Black/ruff stay at `py311` in PR 5; bump happens in a post-#1234 follow-up PR with hand-applied UP040 fixes (the 2026-04-14 incident pattern is what D28 prevents replaying).
4. `--select UP` autofix is FORBIDDEN in PR 5 (per `feedback_no_unsafe_autofix.md` — that's the exact pattern D28 defers).
