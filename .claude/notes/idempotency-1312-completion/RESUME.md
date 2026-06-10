# RESUME — idempotency #1312 rebuild (session entry point)

Read this FIRST, then `PLAN-REBUILD.md` ("POST-MERGE RE-GROUNDING — β on main's α" section
at the end is authoritative) and `SYNTHESIS.md`. Branch: `feature/b6-idempotency-replay-table`.

## DONE (origin/feature/b6-idempotency-replay-table, PR #1312)
- `911c46bad` merge origin/main (34-commit sync) + `1faf55d1e` wrapper-threading merge fix
- `e7f4e9ada` top-level `replayed` marker · `d24a85f86` success-cache substrate
  (model + migration `7a8c3e1170a5` + repo) · `37575b747` docs/spec-grounding gate
- `a9beca2e7` THE core inversion: verbatim success-cache replay
- `01873b31d` ruff-format fix (CI lint) · `59eeb619d` post-audit hardening
- **CI green on `59eeb619d`: EVERY check passed** (Unit, Integration ×5, BDD, E2E,
  Quickstart, Security, CodeQL, Lint)
- **Commit 2 (required key)** — local commit after 59eeb619d: see below. Pushed: check
  `git log origin/feature/b6-idempotency-replay-table..HEAD`.

## The β model implemented (src/core/tools/media_buy_create.py)
Probe: `canonical_request_hash(req)` → `find_by_key(tenant, principal, account_id, tool, key)`
→ `_raise_on_payload_conflict` (IDEMPOTENCY_CONFLICT on hash mismatch; `record_success`
REQUIRES payload_hash) → `_replay_cached_success` verbatim + `replayed=True`, or None on
schema-drifted envelope → treated as MISS. 3 success returns → `_cache_and_return` (errors/
dry-run never cached). TOCTOU → `_replay_after_race(request_hash=...)`: same conflict rule →
verbatim replay → degraded `_build_idempotency_hit_result` (spec-minimal packages, advisory
omitted/frozen, pending_approval→"submitted", no replayed marker; divergences documented).
`MediaBuy.idempotency_key` unique index = dup-booking backstop. capabilities
`replay_ttl_seconds` derives from `DEFAULT_REPLAY_TTL`.

## Required idempotency_key (Commit 2) — IMPLEMENTED
- Create-side override DELETED (`_base.py` ~1450) — inherits library REQUIRED field
  (MinLen 16 + MaxLen 255 + pattern). Update/sync stay optional (create-first rollout;
  comments + schema-inheritance allowlist updated, create entry REMOVED).
- Wrappers omit-when-absent → "Field required" VALIDATION_ERROR. Approval-resume injects
  synthetic `legacy-approval-<id>` for pre-requirement stored raw_requests.
- Test surface: harness `_ensure_idempotency_key` (call_impl/_flatten_request/
  build_rest_body) + `OMIT_IDEMPOTENCY_KEY` sentinel; `make_mock_uow` defaults
  idempotency_attempts.find_by_key→None (probe runs on EVERY keyed create — bare MagicMock
  = spurious conflict); BDD `_ensure_request_defaults` injects per-scenario key;
  adcp_factories + e2e `adcp_request_builder` inject; ~45 test files keyed
  (unit: deterministic literals ≥16 chars; integration: f"int-key-{uuid4().hex}" —
  MUST be per-call-unique, the cache is live and persists in agent-db).
- New pins: missing/short/pattern rejection + acceptance + update-optional
  (test_media_buy.TestIdempotencyKeyRequired) + REST wire missing-key VALIDATION_ERROR
  (test_idempotency_replay.TestMissingKeyRejectedAtWire). Obsolete absent-key test removed.

## Validation state (Commit 2, local)
unit 4753/0 (`make quality` green, baseline untouched) · BDD 1522/0 (plain pytest, like CI)
· integration 2008 passed / ONLY infra-gated remain: test_creative_agent_live 18E (needs
`scripts/creative-agent-stack.sh up`, the authoritative runner provisions it) +
test_mcp_client_util 2F (external DNS audience-agent.fly.dev unreachable locally). Both
files untouched by the branch; CI green previously.
**Integration env gotcha:** `source .claude/skills/.agent-db.env` (ENCRYPTION_KEY +
GEMINI_API_KEY!) then override DATABASE_URL with the real port (docker ps; was 54081).
Missing ENCRYPTION_KEY = 13 phantom get_products policy failures.

## REMAINING
1. **BDD create scenarios + wire matrix** (Commit 4 of plan): success-replay/conflict/
   missing-key × MCP/A2A/REST. PREREQ: harness `TransportResult.replayed` surfacing
   (frozen dataclass transport.py:85) + REST parse pops `replayed` (currently silently
   swallowed). Fix vacuous `then_no_duplicate_booking` (uc002) + adapter-call counter;
   `no_duplicate_webhooks_on_replay`, `verify_media_buy_count`, `fresh_key_new_resource`.
2. **Equivalence-pin** (hasher == `adcp.server.idempotency.canonical_json_sha256` +
   EXCLUDED_FIELDS parity — audit verified behaviorally; pin it) + wire `expire_old` to a
   production caller or documented deferral + replay/conflict metrics + optional
   alter-column migration for the stale rejection-era DB comment (audit B:N1).
3. **Guard gate + full suite**: add pytest-xdist dev dep (tox -e bdd `-n auto` is broken
   locally without it; CI unaffected — runs plain pytest), then `./run_all_tests.sh ci`.

## Commit gotchas
- `SKIP=black UV_FROZEN=1 git commit`; commits auto-background → VERIFY HEAD VIA SHA
  (a notes commit silently aborted once; tail-truncated logs hide the hook abort).
- Run `uv run ruff format <touched>` BEFORE every commit (CI lint failed once on this).
- Never pipe full-suite pytest to `tail` in a backgrounded task — the failure list is lost;
  `tee` to a file instead. Don't run two DB suites concurrently (contention flakes).
- User owns push authorization. Repo COMMITS `.claude/notes/`.
