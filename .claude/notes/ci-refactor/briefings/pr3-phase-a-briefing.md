# PR 3 Phase A — Overlap (new workflows alongside test.yml)

## Briefing
**Where we are.** Week 3. PR 1 + PR 2 merged: governance + uv.lock single-source. Pre-commit drift gone. Calendar position: structural CI rewrite begins.

**What this PR does.** Phase A is the FIRST of 3 phases. It introduces `.github/actions/setup-env/action.yml` (composite), `.github/actions/_pytest/action.yml` (composite — Decision-4 from 2026-04-25 P0 sweep, NOT a reusable workflow), `.github/workflows/ci.yml` (orchestrator with 11 frozen BARE-name jobs per D17 + D26), `.github/scripts/migration_roundtrip.sh`, `.coverage-baseline=53.5` (D11 — hard-gate from day 1, revised 2026-04-25). It also fixes #1233 D5/D6/D10/D12/D14 in legacy `test.yml` plus D15/PD24 (Gemini fallback, moved from PR 1 commit 10). Phase A is INTENTIONALLY redundant: both old (`test.yml`) and new (`ci.yml`) workflows run for ≥48h to confirm new check names are real before Phase B's atomic flip.

**Architectural corrections to issue #1234** (revised 2026-04-25 P0 sweep — Decision-4):
- `_setup-env` is a **composite action**, NOT a reusable workflow.
- `_pytest` is ALSO a composite action (was reusable in earlier plan; Decision-4 changed this). Reusable-workflow nesting renders status checks as 3-segment names (`CI / Unit Tests / pytest`); composites don't add segments.
- Postgres services live at the calling-job level in `ci.yml`, NOT in `_pytest` (composites can't declare services). Each DB-needing job (integration/e2e/admin/bdd/migration-roundtrip) declares its own `services:` block.

**You can rely on.** PR 1's SHA-pinning convention. PR 2's local mypy/black hooks (`CI / Quality Gate` runs `pre-commit run --all-files` which uses them). `branch-protection-snapshot.json` + `branch-protection-snapshot-required-checks.json` from pre-flight A1+A2 (your rollback target). `.pre-commit-coverage-map.yml` does NOT exist yet — that lands in PR 4.

**You CANNOT do.** Phase B's `gh api -X PATCH` flip — admin-only. Phase C's `test.yml` deletion — separate follow-up PR. Modifying branch-protection in any way. Adding required checks beyond the 11 frozen names per D17 — that's a contract.

**Concurrent activity.** v2.0 phase PRs landing on `test.yml` are HIGHEST conflict surface. Coordinate before opening Phase A. PR #1217 merged or not, doesn't matter — PR 3 doesn't reference adcp.

**Files (heat map).**
- New: `.github/actions/setup-env/action.yml`, `.github/actions/_pytest/action.yml` (composite — NOT `.github/workflows/_pytest.yml`), `.github/workflows/ci.yml`, `.github/scripts/migration_roundtrip.sh`, `.coverage-baseline`.
- Modified: `.github/workflows/test.yml` — fixes #1233 D6 (lines 382-387 ruff `|| true`), D5/D14 (line 347 hardcoded `ADCP_SALES_PORT`), D10 (test using `pytest.skip` on network), D12 (creative agent unconditional).
- DO NOT touch: `.pre-commit-config.yaml` (PR 4), `pyproject.toml` (PR 5), anything in `src/`.

**Verification environment.** TTL guard requires `branch-protection-snapshot-required-checks.json` (rollback target). Coverage measurement (`coverage.json`) freshness 30 days. `yq` (`uv add --dev yq` if missing) for YAML assertions.

**Escalation triggers.**
- New `ci.yml` doesn't produce all 11 frozen check names on a real PR (Phase A soak fails) → STOP, file escalation.
- Coverage combine fails artifact-merge (test-results structure) → debug `download-artifact@v4 --pattern coverage-* --merge-multiple`.
- `migration_roundtrip.sh` shows schema drift after downgrade-base + upgrade-head → real bug in alembic migrations (separate fix; don't merge until resolved).

**Key facts from prior rounds.**
1. The 11 frozen check names per D17 + D26 are a **CONTRACT**. The job names in `ci.yml` are BARE (e.g., `name: 'Quality Gate'`); GitHub renders them as `CI / Quality Gate` because the workflow is `name: CI`. Including `'CI /'` in a job name produces `CI / CI / Quality Gate` (the D26 bug). Any deviation breaks Phase B's atomic flip. Branch protection's PATCH body uses the rendered names: `'CI / Quality Gate'`, `'CI / Type Check'`, `'CI / Schema Contract'`, `'CI / Unit Tests'`, `'CI / Integration Tests'`, `'CI / E2E Tests'`, `'CI / Admin UI Tests'`, `'CI / BDD Tests'`, `'CI / Migration Roundtrip'`, `'CI / Coverage'`, `'CI / Summary'`.
2. Postgres services live at the calling-job level in `ci.yml` (NOT in `_pytest` — composites can't declare services). Each DB-needing job (integration/e2e/admin/bdd/migration-roundtrip) has its own `services: postgres:` block.
3. `pytest-xdist` validated safe per `tests/conftest_db.py:323-348` UUID-per-test DB pattern. Use `-n auto` in integration env.
4. `.coverage-baseline` value is exactly `53.5` (current 55.56% from A7 minus 2pp safety margin per D11). **Hard-gate from PR 3 day 1** (D11 revised in 2026-04-25 P0 sweep — no advisory window).
5. `app_id` is **intentionally OMITTED** from the Phase B PATCH body — allows any GitHub App (incl. GitHub Actions) to satisfy each check. R20 mitigation lives elsewhere (24h cooldown label workflow + A11 `allow_auto_merge` pre-flight audit + R23 daily branch-protection snapshot Action). The earlier "app_id IS included" framing was provisional and was rejected; spec/flip-script/Phase B checklist all omit `app_id`.
6. The `coverage` job uses `actions/download-artifact@v4` with `merge-multiple: true` — don't pin v3.
7. `concurrency: cancel-in-progress` is on PRs only (`github.event_name == 'pull_request'`); main-branch pushes do NOT cancel.
