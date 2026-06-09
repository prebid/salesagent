# Idempotency #1312 — Spec-Conformant Rebuild: Synthesis & Decision Register

**Status:** GROUNDING COMPLETE — gating decision D1 open. 2026-06-09.
**Branch:** `feature/b6-idempotency-replay-table`.
**Supersedes:** `PLAN.md` (the old "complete-the-expansion" plan). THIS is the live entry point.
**Grounding detail:** `grounding/A-spec-storyboard-contract.md`, `grounding/B-unravel-map.md`, `grounding/C-architecture-bdd-obligations.md`.

## The grounded contract (AdCP 3.0.1; OBSERVED unless noted)
- Spec rule entered 3.0.0 (tagged 2026-04-22) / 3.0.1 (2026-04-28) — **predates #1312**. Not a spec change under us.
- **SUCCESS-ONLY caching.** security.mdx rule 3 verbatim: *"Only successful responses are cached. On any error … the key is not stored. A retry re-executes."* #1312 built the inverse.
- **idempotency_key REQUIRED** on every mutating request; missing → `INVALID_REQUEST` (storyboard also accepts `VALIDATION_ERROR`), at **schema-validation time, before cache lookup**. Format `^[A-Za-z0-9_.:-]{16,255}$`.
- **`replayed`**: boolean, **top-level on the envelope** (REST/A2A) / top of the structured result (MCP); **injected at response time, NOT stored**; inner payload is the original success **verbatim**. Fresh = false/omitted.
- **Conflict**: same key + different canonical hash within TTL → `IDEMPOTENCY_CONFLICT`. Body = **code + message ONLY** (no cached payload / diff / fingerprint / `field` — read-oracle defense).
- **Canonical equivalence**: RFC 8785 JCS; `SHA-256(JCS(payload − excluded))`; **CLOSED 4-field exclusion list**: `idempotency_key`, `context`, `governance_context`, `push_notification_config.authentication.credentials`. Our local hasher is **byte-identical to the SDK (verified)** → non-lock-in + equivalence-pin test.
- **Scope**: `(authenticated agent, account_id, key)` — spec retired "principal"; maps to our `(tenant, principal)`. Never surface the conflict oracle across scope or to unauth callers.
- **TTL** (`replay_ttl_seconds`): min 3600 / rec 86400 / max 604800; REQUIRED in capabilities when `supported:true`; a **durability contract** — the declared window must be honored.
- **EXPIRED**: spec MAY-evict / SHOULD-reject; storyboard has **no** expired step (deferred #2760). Out-of-scope decision is **spec-permitted**; caveat: durably honor the advertised TTL (no early eviction), treat post-TTL as miss → re-execute.
- **Rate limiting** (`RATE_LIMITED` on per-(agent,account) cache inserts): behavior is a spec MUST; numeric ceilings SHOULD; **NOT graded at 3.0.1**.
- **Tool scope**: ALL mutating tasks (closed list: create/update_media_buy, sync_*, activate_signal, …). Only `create_media_buy` is storyboard-graded at 3.0.1.

## The conformant model (success-caching middleware)
On a mutating request:
1. Validate key present → else `INVALID_REQUEST`.
2. Compute canonical hash.
3. Look up `(scope, key)`:
   - **hit + hash match** → return stored success **verbatim** + `replayed:true` (handler NOT run).
   - **hit + hash mismatch** → `IDEMPOTENCY_CONFLICT` (handler NOT run).
   - **miss** → run handler; on success store `(scope, key, hash, response)`; on error store nothing.
4. Concurrent same-key misses are caught by the `MediaBuy.idempotency_key` unique constraint (TOCTOU backstop → resolves to a replay).

## Decision register
- **D1 (GATING) — storage shape.**
  - **(β, RECOMMENDED)** Repurpose `idempotency_attempt` as a **generic success-response cache**: store envelope + canonical hash + TTL keyed by `(scope, key)`. Byte-for-byte faithful (returns the stored original), generalizes to all mutating tools, wire `create_media_buy` now / others fast-follow. The bug was *what's stored* (rejections) + the *raise-on-replay* flow, **not** the table.
  - **(α)** MediaBuy-native: add `payload_hash` to `media_buys`, delete the `idempotency_attempt` table; replay rides on the on-main `MediaBuy.idempotency_key` and **re-derives** the success envelope. Leanest / most code deleted, but **create_media_buy-only** and **re-derivation risks the verbatim-replay contract**.
  - NOTE: both research agents initially leaned "delete the table"; the verbatim-replay requirement is why repurpose-not-delete is recommended.
- **D2 (lean: DO IT)** — restore required `idempotency_key` + reject missing → `INVALID_REQUEST`. Spec-mandated + **BREAKING** (we silently skip today; no transport-boundary key generation exists). Needs a **new typed `INVALID_REQUEST` error** (no existing class emits it; `AdCPValidationError` = `VALIDATION_ERROR`).
- **D3 (RESOLVED — keep `terminal`)** — the conformance storyboard grades the conflict CODE only (`idempotency.yaml:460-471`, no recovery check), so `terminal` (matching the SDK validator + the existing `AdCPIdempotencyConflictError` + `test_adcp_exceptions.py:122`) passes storyboard. The earlier "lean correctable" was wrong. Keep terminal + a documented spec(correctable)-vs-SDK(terminal) divergence comment mirroring `AdCPAuthenticationError` (exceptions.py:342-346).
- **D-rate (lean: FAST-FOLLOW)** — rate-limiting is a spec MUST but ungraded + orthogonal to the replay core → explicit follow-up, not this PR.

## Settled (no user input needed)
- `replayed` on `CreateMediaBuyResult`, injected in `_serialize`, omit-when-false; stored body never carries it.
- Canonical hasher KEEP (non-lock-in) + equivalence-pin test vs `adcp.server.idempotency.canonical_json_sha256` + exclusion-set parity.
- EXPIRED type removed; TTL kept + honored + advertised.
- Test matrix: {success-replay, conflict, missing-key} × {MCP, A2A `on_message_send` w/ real auth chain, REST TestClient}, **wire-level not mock-only**; `assert_envelope_shape` for errors + a NEW success-replay assertion (reads `result.payload` + top-level `replayed`).
- BDD: success-replay / conflict / missing-key, pass-or-xfail, registered in `pytest_plugins`, compare replayed `media_buy_id` to the **captured original** (not a re-derived expression).
- Guards: regenerate `.duplication-baseline` downward, single migration head (re-verify post-rebase), no `error_code=` kwarg in `_impl`, repository pattern (DB via repo/UoW/factories).
