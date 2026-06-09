# Idempotency #1312 — Pre-rebuild test baseline

Captured 2026-06-09 on `feature/b6-idempotency-replay-table` @ `9a689f8c2`, BEFORE any
spec-conformant rebuild edits. This is the green-vs-xfail reference to compare against
after the breaking required-key change + success-caching inversion.

DB: agent-db `agent-pg-skills` (bare Postgres 17). Real published port verified via
`docker ps` (the `.agent-db.env` port is auto-reallocated per `up` and can be stale).

## BDD — `-k idempotency`
- **140 xfailed / 0 passed / 0 failed.**
- Files: `tests/bdd/test_uc003_update_media_buy.py`, `tests/bdd/test_uc006_sync_creatives.py`.
- NO UC002 (create_media_buy) idempotency BDD scenarios matched — they do not exist yet
  (we ADD success-replay / conflict / missing-key for create).
- These 140 are xfail-managed update/sync stubs. Review correction #6 keeps the required-key
  change CREATE-ONLY, so they must NOT flip xfail→fail. Re-run `-k idempotency` after changes.

## Integration + unit — idempotency files
- **110 passed / 0 failed** (8 pre-existing `uow.session` deprecation warnings, unrelated).
- Files run together (single pytest process — avoids concurrent agent-db contention):
  - `tests/integration/test_idempotency_replay.py` (12) — rejection-replay; PORT to success.
  - `tests/integration/test_idempotency_race.py` (3) — calls `_build_idempotency_hit_result`; PORT.
  - `tests/integration/test_idempotency_attempt_repository.py` (11) — repo; flip rejection→success.
  - `tests/integration/test_create_media_buy_account_wire.py` (3).
  - `tests/unit/test_idempotency_canonical.py` (8) — KEEP (non-lock-in hasher).
  - `tests/unit/test_adcp_exceptions.py` (55) — incl. `:122` pins IDEMPOTENCY_CONFLICT+terminal (D3).
  - `tests/unit/test_property_list_unsupported_advisory.py` (18) — incl.
    `TestIdempotencyReplayRebuildsAdvisory` asserting OLD live-rebuild; REWRITE to frozen.

## Known infra issue (resolve before the full-suite pre-push gate)
- `pytest-xdist` is absent from `pyproject.toml` and both venvs, yet `tox -e bdd` invokes
  `-n auto --dist loadfile` → `tox -e bdd` errors (exit 4) in this environment. Baseline was
  captured via direct `uv run pytest` (no xdist) instead. `./run_all_tests.sh ci` uses tox →
  must fix (add xdist to dev group, or drop `-n auto`) before any full-suite gate / push claim.

## Re-derived commit plan (atomic units, each `make quality`-green)
Per PLAN-REBUILD.md "Sequencing fix (A5)" + review corrections. Repo method rename CANNOT be
isolated (its only callers are the rejection machinery being deleted) → it merges into the core
inversion commit.

1. **`replayed` plumbing (additive, non-breaking).** `CreateMediaBuyResult.replayed` +
   `_serialize` omit-when-false + only-on-genuine-success invariant; `TransportResult.replayed`
   + harness pop/surface; new success-replay assertion helper; confirm response JSON schema
   admits top-level `replayed`. Field defaults false / never set yet → no behavior change.
2. **Required `idempotency_key` (BREAKING, create-only).** Restore required on
   `CreateMediaBuyRequest` (undo 2 relaxations) + MinLen(16)+pattern + delete false
   "generated at boundary" comments + drop schema-inheritance allowlist override
   (`test_architecture_schema_inheritance.py:229`). Blast radius (~37 test files + harness
   `media_buy_create.py:269` + `media_buy_helpers.py`) all pass a key. Missing-key →
   VALIDATION_ERROR uniform across MCP/A2A/REST (V2).
3. **CORE inversion + repo flip + integration-test port (ONE commit).** rename
   `record_rejection`→`record_success` (serialize INSIDE repo — model_dump guard) + account_id
   scope column + unique index `NULLS NOT DISTINCT` + migration; delete
   `_raise_idempotency_rejection_replay`/`_cache_rejection_envelope`/`_cache_rejection_and_raise`/
   `_build_idempotency_hit_result`; rewrite `_impl` probe = success replay + conflict +
   best-effort store at both create paths + degraded frozen-advisory reconstruction (DRY helpers);
   remove `AdCPError.replayed` + `if exc.replayed` branch + `AdCPIdempotencyExpiredError` +
   D3 divergence comment; PORT `test_idempotency_replay.py`/`test_idempotency_race.py`, INVERT
   `test_non_transient_adapter_rejection_is_cached`, REWRITE `TestIdempotencyReplayRebuildsAdvisory`
   (frozen); regenerate `.duplication-baseline` downward.
4. **BDD create scenarios + wire matrix.** success-replay / conflict / missing-key BDD
   (register in `pytest_plugins`); fix vacuous `then_no_duplicate_booking` + harness adapter-call
   counter (`no_duplicate_webhooks_on_replay`, `verify_media_buy_count`, `fresh_key_new_resource`);
   MCP/A2A(`on_message_send`,real auth)/REST wire tests.
5. **Equivalence-pin + gold-standard gaps.** our hash == `adcp.server.idempotency.canonical_json_sha256`
   + EXCLUDED_FIELDS parity; eviction/cleanup (wire `expire_old` or documented deferral); metrics.
6. **Guard hygiene + full suite.** single migration head, allowlist sweep, final
   `.duplication-baseline`; `./run_all_tests.sh ci` (after xdist fix).
