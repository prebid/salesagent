# PR 3 Phase A — Overlap (new workflows alongside test.yml)

## Briefing
**Where we are.** Week 3. PR 1 + PR 2 merged: governance + uv.lock single-source. Pre-commit drift gone. Calendar position: structural CI rewrite begins.

**What this PR does.** Phase A is the FIRST of 3 phases. It introduces `.github/actions/setup-env/action.yml` (composite), `.github/workflows/_pytest.yml` (reusable), `.github/workflows/ci.yml` (orchestrator with 11 frozen check names per D17), `.github/scripts/migration_roundtrip.sh`, `.coverage-baseline=53.5` (D11). It also fixes #1233 D5/D6/D10/D12/D14 in legacy `test.yml`. Phase A is INTENTIONALLY redundant: both old (`test.yml`) and new (`ci.yml`) workflows run for ≥48h to confirm new check names are real before Phase B's atomic flip.

**Architectural corrections to issue #1234.** `_setup-env` is a **composite action** (`.github/actions/setup-env/action.yml`), NOT a reusable workflow — composite actions can be steps within a job. `_postgres.yml` does NOT exist standalone — services collapse into `_pytest.yml` declared unconditionally (GitHub Actions doesn't support conditional services).

**You can rely on.** PR 1's SHA-pinning convention. PR 2's local mypy/black hooks (`CI / Quality Gate` runs `pre-commit run --all-files` which uses them). `branch-protection-snapshot.json` + `branch-protection-snapshot-required-checks.json` from pre-flight A1+A2 (your rollback target). `.pre-commit-coverage-map.yml` does NOT exist yet — that lands in PR 4.

**You CANNOT do.** Phase B's `gh api -X PATCH` flip — admin-only. Phase C's `test.yml` deletion — separate follow-up PR. Modifying branch-protection in any way. Adding required checks beyond the 11 frozen names per D17 — that's a contract.

**Concurrent activity.** v2.0 phase PRs landing on `test.yml` are HIGHEST conflict surface. Coordinate before opening Phase A. PR #1217 merged or not, doesn't matter — PR 3 doesn't reference adcp.

**Files (heat map).**
- New: `.github/actions/setup-env/action.yml`, `.github/workflows/_pytest.yml`, `.github/workflows/ci.yml`, `.github/scripts/migration_roundtrip.sh`, `.coverage-baseline`.
- Modified: `.github/workflows/test.yml` — fixes #1233 D6 (lines 382-387 ruff `|| true`), D5/D14 (line 347 hardcoded `ADCP_SALES_PORT`), D10 (test using `pytest.skip` on network), D12 (creative agent unconditional).
- DO NOT touch: `.pre-commit-config.yaml` (PR 4), `pyproject.toml` (PR 5), anything in `src/`.

**Verification environment.** TTL guard requires `branch-protection-snapshot-required-checks.json` (rollback target). Coverage measurement (`coverage.json`) freshness 30 days. `yq` (`uv add --dev yq` if missing) for YAML assertions.

**Escalation triggers.**
- New `ci.yml` doesn't produce all 11 frozen check names on a real PR (Phase A soak fails) → STOP, file escalation.
- Coverage combine fails artifact-merge (test-results structure) → debug `download-artifact@v4 --pattern coverage-* --merge-multiple`.
- `migration_roundtrip.sh` shows schema drift after downgrade-base + upgrade-head → real bug in alembic migrations (separate fix; don't merge until resolved).

**Key facts from prior rounds.**
1. The 11 frozen check names per D17 are a **CONTRACT**. Any deviation breaks Phase B's atomic flip. Verify exact strings: `'CI / Quality Gate'`, `'CI / Type Check'`, `'CI / Schema Contract'`, `'CI / Unit Tests'`, `'CI / Integration Tests'`, `'CI / E2E Tests'`, `'CI / Admin UI Tests'`, `'CI / BDD Tests'`, `'CI / Migration Roundtrip'`, `'CI / Coverage'`, `'CI / Summary'`.
2. Postgres services in `_pytest.yml` are unconditional — non-DB tests ignore them (trivial overhead). GitHub Actions does NOT support conditional `services:` blocks.
3. `pytest-xdist` validated safe per `tests/conftest_db.py:323-348` UUID-per-test DB pattern. Use `-n auto` in integration env.
4. `.coverage-baseline` value is exactly `53.5` (current 55.56% from A7 minus 2pp safety margin per D11). Advisory for 4 weeks.
5. `app_id` is intentionally OMITTED from the Phase B PATCH body — allows any GitHub App (incl. GitHub Actions) to satisfy each check.
6. The `coverage` job uses `actions/download-artifact@v4` with `merge-multiple: true` — don't pin v3.
7. `concurrency: cancel-in-progress` is on PRs only (`github.event_name == 'pull_request'`); main-branch pushes do NOT cancel.
