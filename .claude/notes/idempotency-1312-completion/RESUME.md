# Idempotency rebuild (PR #1312) — final state

Companion docs: `PLAN-REBUILD.md` (the authoritative design, esp. "POST-MERGE RE-GROUNDING"),
`SYNTHESIS.md` (why the rebuild exists), `grounding/` (spec citations), `review/` (adversarial
pre-implementation reviews). Branch: `feature/b6-idempotency-replay-table`.

## What shipped
- **Core inversion**: `create_media_buy` idempotency is a verbatim SUCCESS-cache replay
  (AdCP 3.0.1) — the prior rejection-replay design was inverse to the spec and was removed.
- **Probe** (`src/core/tools/media_buy_create.py`): `canonical_request_hash(req)` →
  `find_by_key(tenant, principal, account_id, tool, key)` → `_raise_on_payload_conflict`
  (IDEMPOTENCY_CONFLICT on hash mismatch) → `_replay_cached_success` (verbatim + top-level
  `replayed: true`; a schema-drifted stored envelope is treated as a MISS, never a 500).
- **Stores**: the three real success returns go through `_cache_and_return`; errors and
  dry-runs are never cached, so a retry after an error re-executes. The cache write also
  runs `expire_old` (opportunistic, tenant-scoped eviction; read-path TTL alone already
  guarantees replay correctness).
- **TOCTOU**: unique-index race losers resolve via `_replay_after_race` — same conflict
  rule, verbatim replay when the winner's cache row is visible, else the documented
  degraded re-derivation (`_build_idempotency_hit_result`: spec-minimal packages, advisory
  omitted, `pending_approval` → "submitted", no replayed marker).
- **Required key**: `CreateMediaBuyRequest.idempotency_key` inherits the library's REQUIRED
  field (MinLen 16 / MaxLen 255 / pattern). Missing key → "Field required" VALIDATION_ERROR
  at the boundary. Update/sync keys stay optional (deliberate create-first rollout).
  Approval-resume injects a synthetic `legacy-approval-<id>` key for raw_requests stored
  before the requirement.
- **A2A parity fix**: the boundary no longer mints a random `po_number` default — it broke
  legitimate A2A replays (hash drift) and cross-transport payload parity.
- **Hasher**: salesagent-native RFC 8785 module (`idempotency_canonical.py`) with byte-parity
  to `adcp.server.idempotency` pinned by `TestSdkEquivalencePin`; RecursionError on
  pathological nesting rejects as a typed validation error. Adopting the SDK helpers
  outright is deferred to the SDK v5 bump.

## Test surface
- Wire matrix (`test_idempotency_wire_matrix.py`): replay / conflict / fresh-key /
  missing-key across IMPL+A2A+MCP+REST; REST replay body byte-equal to the original plus
  exactly the `replayed` marker; adapter not re-invoked on replay; wire legs assert REAL
  wire bytes (the IMPL leg grades the synthesized envelope, by definition).
- BDD: the v3.1 replay scenario passes on all four transports; the missing-key scenario is
  wired and strict-xfailed on the pre-existing suggestion-field gap (graduates when
  production populates structured suggestions).
- Repository/replay/race/eviction integration suites + unit contract pins
  (missing/short/pattern rejection, update-side optionality, serializer marker).
- Test keys must be ≥16 chars and, in integration tests, per-call-unique
  (`f"int-key-{uuid4().hex}"`) — the cache is live and the agent-db persists across runs.
  Harness `OMIT_IDEMPOTENCY_KEY` expresses key absence; `make_mock_uow` defaults the probe
  to a cache miss (a bare MagicMock reads as a hit with a mismatching hash).

## Environment gotchas (local runs)
- `source .claude/skills/.agent-db.env` (ENCRYPTION_KEY + GEMINI_API_KEY) and override
  DATABASE_URL with the real container port (`docker ps`). Missing ENCRYPTION_KEY shows up
  as phantom get_products policy failures.
- `SKIP=black UV_FROZEN=1 git commit` (pre-commit black ping-pongs with ruff; UV_FROZEN
  avoids a mid-commit uv.lock re-resolve). Run `uv run ruff format <touched>` before
  committing — CI lint checks ruff formatting.
- Don't run two DB suites concurrently against the same agent-db (contention flakes).
- `test_creative_agent_live` needs `scripts/creative-agent-stack.sh up` (the authoritative
  runner provisions it); `test_mcp_client_util` needs outbound DNS.
