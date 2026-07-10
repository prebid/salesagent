# Verification command log — /execute batch 2026-07-10

Session: 8-ticket batch on `feature/e2e-harness-wiring` (0hjn, q0fq, ovc1, 8e18,
4ks0, e3jv via task-single epic salesagent-g05n; qkk4 via task-execute epic
salesagent-8qro; kb5q via bug-triage epic salesagent-eue7). Every command below
was actually run this session, listed in execution order with what it verified.

## Batch baseline (g05n.1)

```bash
make quality 2>&1 | tail -8
# 5318 passed, 9 skipped, 26 xfailed — clean slate before any change
```

## salesagent-0hjn — CI merge-blocking + escape-hatch locks

```bash
# New guard file (4 tests incl. 2 meta-tests on live detectors)
uv run pytest tests/unit/test_architecture_e2e_rest_escape_hatches.py -v

# ci.yml structural validation (Summary.needs + result check present)
uv run python -c "import yaml; d=yaml.safe_load(open('.github/workflows/ci.yml')); s=d['jobs']['summary']; \
  assert 'bdd-in-network' in s['needs']; assert 'bdd-in-network.result' in s['steps'][-1]['run']"

make quality   # 5322 passed (+4 new guard tests)
```

Pin-generation scans (one-off, embedded in the guard as live detectors):
- AST scan of `pytest_collection_modifyitems` for `if` nodes referencing
  `is_e2e_rest` whose subtree reaches an `xfail` attribute → 20 conditions.
- AST scan of `tests/harness/*.py` (excl. `test_*`/`_realize.py`) for
  `e2e_unsupported(...)` calls + `raise E2EUnsupportedSetup(...)` → 3 sites.

## salesagent-q0fq — roas/cpa literals + citation re-grounding

```bash
# Spec grounding (pinned schema, NOT the SDK):
git -C ~/projects/adcp show 04f59d2d5:static/schemas/source/core/delivery-metrics.json
git -C ~/projects/adcp show 04f59d2d5:static/schemas/source/media-buy/get-media-buy-delivery-response.json
# → aggregated_totals.roas = "total conversion_value / total spend", cpa = "total spend / total conversions"

# Behavioral verify across transports (DB via agent-db, auto-started):
scripts/run-test.sh tests/bdd/test_uc004_deliver_media_buy_metrics.py \
  -k "roas_and_cost_per_acquisition" -p no:randomly
# 3 passed (a2a/mcp/rest) with pytest.approx(2.0)/approx(25.0) literals

make quality   # 5322 passed
```

## salesagent-ovc1 — log_safe sweep (CWE-117)

```bash
uv run ruff check src/core/tools/media_buy_create.py src/core/tools/creatives/_assignments.py src/core/logging_config.py
uv run ruff check --fix src/core/tools/creatives/_assignments.py   # I001 import order
make quality   # 5322 passed
```

## salesagent-8e18 — nits + redundant index migration

```bash
uv run alembic revision -m "drop redundant idx_delivery_sim_tenant"   # 823974a5553e
uv run alembic heads                                                  # single head preserved

# Migration roundtrip exactly as the CI job runs it (agent-db Postgres):
eval $(.claude/skills/agent-db/agent-db.sh up)
uv run python scripts/ci/migration_roundtrip.py
# upgrade head → downgrade 64f0fff7d954 → upgrade head: PASS

scripts/run-test.sh tests/integration/test_timestamptz_migration.py -x -q       # 2 passed
scripts/run-test.sh tests/integration/test_create_media_buy_behavioral.py -q \
  -k "TestFormatSpecTransientErrors"                                            # 2 passed
make quality   # 5322 passed
```

## salesagent-4ks0 — test placement + read-back hygiene + leak assertions

```bash
# Which read-backs were NEW from the fix round (not pre-existing debt):
git log -L 736,746:tests/integration/test_creative_sync_behavioral.py
git show 555069ffe -- tests/integration/test_creative_sync_behavioral.py | grep -c "^+.*get_db_session"

scripts/run-test.sh tests/integration/test_creative_sync_behavioral.py -q -k "TestAssignmentProcessing"
# 9 passed — incl. rewritten cross-principal test (leak markers + positive control)
make quality   # 5323 passed
```

## salesagent-e3jv — guard meta-drift

```bash
# No external refs before deleting dead detectors:
grep -rn "count_shared_validator_calls\|_REQUIRED_CALL_SITES\|_LEDGER_CEILING" tests/ src/ scripts/ --include="*.py"

uv run pytest tests/unit/test_architecture_create_paths_share_creative_validation.py \
  tests/unit/test_e2e_rest_ledger_fitness.py \
  tests/unit/test_architecture_creative_lookup_principal_scoped.py -q -p no:randomly
# 15 passed — new meta-tests target the LIVE per-branch detector (incl. orelse fixture)
make quality   # 5324 passed
```

## Epic g05n final (g05n.20)

```bash
make quality   # 5324 passed vs baseline 5318 (+6, 0 regressions)
bd sync
```

## salesagent-qkk4 — repository delegation (task-execute)

```bash
# Baseline (8qro.1): make quality (5324) PLUS full integration suite.
# LESSON: bare `tox -e integration` red-herrings 18 errors + 1 fail in
# test_creative_agent_live.py — those tests REQUIRE the pinned reference
# creative agent, not the live public host (which 429s). Correct env:
scripts/creative-agent-stack.sh up
export CREATIVE_AGENT_URL=$(scripts/creative-agent-stack.sh url)
eval $(.claude/skills/agent-db/agent-db.sh up)
tox -e integration
# baseline: 2165 passed, 0 failed (12m 37s)

# Disease scan (8qro.3), reproducible:
# AST scan grouping repository methods by (select-model, filter-key-set) across
# classes in src/core/database/repositories/*.py, plus:
grep -rn "principal_id: str | None" src/core/database/repositories/

# Refactor-guard tests (written by dp-test-author agent, verified by me):
scripts/run-test.sh tests/integration/test_creative_repository.py -q \
  -k "TestAssignmentRepoGetCreativeById or TestAssignmentRepoGetProductById"   # 4 passed PRE-refactor

# Post-implement (8qro.7):
scripts/run-test.sh tests/integration/test_creative_repository.py -q          # 25 passed
uv run python .pre-commit-hooks/check_code_duplication.py                     # src/ 35 unchanged → no ratchet
make quality                                                                  # 5324 (after fixing
#   tests/unit/test_creative_repository.py::test_default_weight_100 — it relied
#   on the removed principal_id=None default; updated to pass principal_id="p1")
scripts/run-test.sh tests/integration/test_creative_sync_behavioral.py -q     # 63 passed

# Sweep verify (8qro.8): scan re-run verbatim → 4 MIGRATE rows gone.
# Final (8qro.10): tox -e integration (same env as baseline) → 2169 passed, 0 failed
```

## salesagent-kb5q — CREATIVE_NOT_FOUND (bug-triage)

```bash
# Spec-grounding gate BEFORE design:
git -C ~/projects/adcp show 04f59d2d5:static/schemas/source/enums/error-code.json
# → CREATIVE_NOT_FOUND exists: correctable + "MUST return uniformly for any
#   creative_id not owned by the calling account"
uv run python -c "from adcp.server.helpers import STANDARD_ERROR_CODES; ..."
# → SDK helper table (35 codes) LACKS it; SDK ErrorCode enum HAS it; pinned enum
#   has 64 codes — helper trails spec by 29 ("SDK not authoritative")
grep -rn "CREATIVE_NOT_FOUND" .venv/lib/python3.12/site-packages/adcp/

# Reproduction (eue7.2) — failed for the right reason (VALIDATION_ERROR on wire):
scripts/run-test.sh tests/integration/test_creative_sync_behavioral.py -q \
  -k "test_strict_mode_unknown_assignment_creative"

# Trace-similar scan: ERROR_CODE_MAPPING keys ∩ pinned enum → 3 demoted spec codes
# (CREATIVE_NOT_FOUND here; CONFIGURATION_ERROR + BILLING_NOT_SUPPORTED → salesagent-n4p2)

# Post-fix:
uv run pytest tests/unit/test_adcp_exceptions.py tests/unit/test_error_code_mapping.py \
  tests/unit/test_error_boundary_translation.py tests/unit/test_architecture_error_code_compliance.py \
  tests/unit/test_error_envelope.py tests/unit/test_error_format_consistency.py \
  tests/unit/test_context_manager_fail_workflow_step.py -q -p no:randomly       # 201 passed
scripts/run-test.sh tests/integration/test_creative_sync_behavioral.py -q \
  -k "test_strict_mode_unknown_assignment_creative"                             # 1 passed (repro green)
make quality                                                                    # 5324 passed

# E2E verify (eue7.9) — wire behavior changed, so BDD across transports:
scripts/run-test.sh tests/bdd/test_uc006_sync_creatives.py -q -p no:randomly    # 57 passed / 645 xfailed
scripts/run-test.sh tests/bdd/test_uc003_update_media_buy.py -q -p no:randomly  # 105 passed / 1249 xfailed
#   (uc003 confirms the storyboard-graded CREATIVE_REJECTED update surface intact)

# Regression verify (eue7.11) — in flight at time of writing:
tox -e integration   # same creative-agent + agent-db env; vs 2169 baseline
```

## Long-runner execution pattern (lesson re-learned this session)

Piping a long gate to `tail` under the 10-min Bash timeout kills it with zero
output. Correct pattern:

```bash
nohup tox -e integration > "$LOG" 2>&1 < /dev/null & disown
# then a separate background watcher:
until ! kill -0 <pid> 2>/dev/null; do sleep 15; done; tail -3 "$LOG"
```

## Batch endgame (pending at time of writing)

```bash
./run_all_tests.sh    # full suite on the external box (hetzner2) — the
                      # user-mandated final gate for the batch, then push
gh pr view 1430 --json mergeable,mergeStateStatus
```
