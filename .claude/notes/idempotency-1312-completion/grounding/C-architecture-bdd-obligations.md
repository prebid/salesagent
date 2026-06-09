# C — Architecture + Test Obligations for the #1312 Idempotency Rebuild

Read-only research artifact. Every claim is labelled **OBSERVED** (verified against
current code at the cited `file:line`) or **INFERRED** (reasoning from the observed facts).
Branch: `feature/b6-idempotency-replay-table`. SDK: `adcp==4.3.0` (spec 3.0.1).

## 0. What is being rebuilt (the delta)

OBSERVED — the current branch implements **rejection-replay** (cache the *error* envelope,
re-raise it on retry marked `replayed=true`). The success path already exists separately via
`MediaBuy.idempotency_key`.

- Rejection caching lives in `src/core/tools/media_buy_create.py`:
  - `_raise_idempotency_rejection_replay` (1488), `_cache_rejection_envelope` (1515),
    `_cache_rejection_and_raise` (1569) — OBSERVED.
  - Call sites: 2343, 3285, 3379 (`_cache_rejection_and_raise`); the rejection lookup +
    conflict raise at 1764-1780 — OBSERVED.
- Storage: `IdempotencyAttempt` model (`src/core/database/models.py:978`) +
  `IdempotencyAttemptRepository` (`src/core/database/repositories/idempotency_attempt.py`)
  + UoW wiring (`uow.py:40,136,143,149`) — OBSERVED.
- The success path: `find_by_idempotency_key` (`repositories/media_buy.py:63`) +
  `_build_idempotency_hit_result` (`media_buy_create.py:1587`) returning a
  `CreateMediaBuyResult(response=CreateMediaBuySuccess(...))` — OBSERVED.

The rebuild's target behavior (per the mission): **cache successful responses**, replay with
top-level `replayed: true` on a SUCCESS envelope, `IDEMPOTENCY_CONFLICT` on hash mismatch,
`INVALID_REQUEST` on missing key. EXPIRED is out of scope.

INFERRED — the rebuild **deletes** the rejection-caching machinery (the three helpers +
their three call sites + `_raise_idempotency_rejection_replay`) and re-points the `replayed`
marker from the *error* envelope onto the *success* envelope. Whether the
`IdempotencyAttempt` table/repo stays depends on whether success caching is keyed off the
existing `MediaBuy.idempotency_key` row (already present) or off a new success-response
cache. See §2 and the UNCERTAIN section.

---

## 1. Two-layer error envelope SSOT

**OBSERVED — the canonical builder** is
`build_two_layer_error_envelope(exc: AdCPError) -> dict` at
`src/core/exceptions.py:603`. Wire shape it emits (640-645):
```
{
  "adcp_error": {code, message, recovery, field?, suggestion?, details?},  # envelope mirror
  "errors":     [ {same single error object} ],                            # payload layer
  "context":    {...},          # only when exc.context set (638-640)
  "replayed":   True,           # ONLY when exc.replayed is True (643-644)  <-- rejection-replay artifact
}
```
- Both codes pass through `ERROR_CODE_MAPPING` via `exc.wire_error_code`
  (`exceptions.py:216-226`, builder uses it at 623) — OBSERVED.
- The three boundary translators are pinned by a guard to call this builder:
  `src/core/tool_error_logging.py::_translate_to_tool_error` (MCP),
  `src/a2a_server/adcp_a2a_server.py::AdCPRequestHandler._build_error_envelope` (A2A),
  `src/app.py::adcp_error_handler` (REST) — OBSERVED at
  `tests/unit/test_architecture_error_envelope_two_layer.py:30-34`.

**OBSERVED — Pattern A (raise-based, typed) is already followed.** `_impl` raises typed
`AdCPError` subclasses; the boundary runs the builder. The typed idempotency subclasses
already exist:
- `AdCPIdempotencyConflictError(AdCPConflictError)` — `error_code="IDEMPOTENCY_CONFLICT"`,
  `recovery="terminal"` (`exceptions.py:563-572`) — OBSERVED.
- `AdCPIdempotencyExpiredError` — 410, `IDEMPOTENCY_EXPIRED`, terminal (575-585) — OBSERVED.
  Mission says EXPIRED is out of scope → INFERRED this class + its mapping should be removed
  (or it becomes dead code a reviewer will flag).

**OBSERVED — IDEMPOTENCY_CONFLICT is a STANDARD code.** `STANDARD_ERROR_CODES["IDEMPOTENCY_CONFLICT"]
= {recovery: "terminal", ...}`. So the typed class's `recovery="terminal"` already matches the
SDK table — no ERROR_CODE_MAPPING entry needed, no spec-divergence comment needed.
`INVALID_REQUEST` is also standard (`recovery: correctable`).

### Obligations
| # | Obligation | What the rebuild must do | Source |
|---|---|---|---|
| E1 | Conflict raised as typed class, no `error_code=` kwarg | Raise `AdCPIdempotencyConflictError(msg, suggestion=...)` — never `AdCPError(..., error_code="IDEMPOTENCY_CONFLICT")`. The class carries the code (P34). | `exceptions.py:563`; current raise is already correct at `media_buy_create.py:1772` (preserve shape, relocate). |
| E2 | Missing-key → `INVALID_REQUEST`, typed | Mission says missing key = INVALID_REQUEST. There is NO dedicated `AdCP*Error` whose `_default_error_code="INVALID_REQUEST"`. Either raise `AdCPValidationError` and rely on… **NO** — `AdCPValidationError` emits `VALIDATION_ERROR` (`exceptions.py:332`), not `INVALID_REQUEST`. INFERRED: add a typed subclass (e.g. `AdCPMissingIdempotencyKeyError` with `_default_error_code="INVALID_REQUEST"`, recovery correctable) rather than `AdCPValidationError(error_code="INVALID_REQUEST")` (the kwarg is the P34 `synthesize()`-bypass smell). | `exceptions.py:328-333`; `_ALLOWED_CODES` includes INVALID_REQUEST (STANDARD). |
| E3 | `replayed` must NOT ride the error envelope after rebuild | The success-caching model puts `replayed:true` on a SUCCESS, not an error. Remove the `if exc.replayed: envelope["replayed"]=True` branch (`exceptions.py:643-644`) AND the `replayed: bool` instance attr (184-187, 214) **iff** no error path sets it anymore. If left in with no setter, it's dead code a reviewer flags (P30/P22). | `exceptions.py:184-187, 214, 641-645` |
| E4 | Don't leak `error_code=`/`details["internal_code"]` | New idempotency raises use typed classes; if any boundary fallback truly needs a synthesized code, use `AdCPError.synthesize(...)` (the only sanctioned override, `exceptions.py:228`). | P34/P35; `exceptions.py:228-265` |
| E5 | EXPIRED removal is complete | If EXPIRED is dropped: remove `AdCPIdempotencyExpiredError` (575), its tests, and any docstring/capability mention, OR justify keeping it. Leaving an unraised subclass = dead taxonomy (P30). | `exceptions.py:575` |

INFERRED gap: there is **no automated guard** forbidding the `error_code=` kwarg in `_impl`
(P34 is a review pattern only). I grepped `tests/unit/test_architecture_*.py` /
`test_no_*.py` — none scan for `AdCP*Error(error_code=...)`. So E1/E2/E4 are enforced by
**review**, not by `make quality`. Pre-existing `error_code=` sites in this file:
`media_buy_create.py:1920, 1939, 2014, 2327` (all `AdCPValidationError(error_code=...)`,
unrelated to idempotency) — OBSERVED; not the rebuild's to fix but a reviewer may note the
inconsistency.

---

## 2. Success-envelope `replayed` — the correct single injection point

### Serialization chain (all three transports converge)
OBSERVED — every transport serializes the SAME `CreateMediaBuyResult` model:
- **MCP**: `ToolResult(content=str(result), structured_content=result)` —
  `media_buy_create.py:4194`. FastMCP serializes via the model's `@model_serializer`.
- **A2A**: `_serialize_for_a2a(result)` → `model_dump(mode="json")` —
  `adcp_a2a_server.py:1464` (+ `_serialize_for_a2a` at 1342).
- **REST**: `response.model_dump(mode="json")` — `routes/api_v1.py:239`.

OBSERVED — `CreateMediaBuyResult._serialize` (`src/core/schemas/_base.py:296-300`) is the
**single choke point**. It dumps `self.response` and injects the protocol `status` at the top
level:
```python
@model_serializer(mode="wrap")
def _serialize(self, serializer, info):
    result = self.response.model_dump(mode=info.mode, context=info.context)
    result["status"] = self.status
    return result
```
`status` is a *top-level envelope* field that lives on the wrapper, NOT on the payload model.
**`replayed` is exactly analogous** — it is an envelope-level transparency marker, a sibling
of `status`.

### Is `replayed` a new field or library-provided?
- OBSERVED — the adcp SDK **`ProtocolEnvelope` type DOES carry `replayed`**:
  `adcp.types.ProtocolEnvelope.model_fields` includes `replayed`. So the spec models it at
  the **envelope** level.
- OBSERVED — the **success payload** type we extend
  (`CreateMediaBuySuccessResponse`, aliased `AdCPCreateMediaBuySuccess`,
  `_base.py:38`) does **NOT** have `replayed`
  (`CreateMediaBuySuccess.model_fields` has no `replayed`). So it is genuinely a new
  envelope-level field for OUR serialization, not a payload field.
- OBSERVED — our hand-rolled `src/core/protocol_envelope.py::ProtocolEnvelope` is a SEPARATE
  class that also lacks `replayed`, and has **ZERO production callers**
  (grep `ProtocolEnvelope.wrap(` in `src/` matches only its own docstring at
  `protocol_envelope.py:24,31` and test files). **Do NOT inject `replayed` there** — it is
  dead infrastructure. (P3/P30: wiring a field into an uncalled class proves nothing.)

### The correct injection point
INFERRED — add `replayed` to `CreateMediaBuyResult` (the wrapper), defaulting to `False`/omit,
and emit it in `_serialize` next to `status`, omitting when false (matches spec
"false or omitted" and the existing error builder's `if exc.replayed` gate). The
success-replay producer `_build_idempotency_hit_result` (`media_buy_create.py:1633`) sets it
true; the fresh-success path leaves it false. This gives ONE place, consistent across MCP/A2A/REST.

Why the wrapper and not `CreateMediaBuySuccess`:
- `status` already lives on the wrapper, not the payload — `replayed` is the same kind of
  envelope-level marker (OBSERVED `_base.py:293-300`).
- Putting it on `CreateMediaBuySuccess` would (a) require redeclaring a field the library base
  lacks and (b) muddle the payload-vs-envelope boundary the codebase enforces
  (`_base.py:201-202` docstrings: "Protocol fields … added by the protocol layer").

### Obligations
| # | Obligation | What the rebuild must do | Source |
|---|---|---|---|
| S1 | Single top-level injection | Add `replayed: bool = False` to `CreateMediaBuyResult`; in `_serialize` set `result["replayed"]=True` only when true (omit otherwise). | `_base.py:283-300` |
| S2 | Producer sets it | `_build_idempotency_hit_result` (success replay) returns the result with `replayed=True`; fresh create returns default false. | `media_buy_create.py:1633-1643` |
| S3 | Do NOT touch `protocol_envelope.py` | That `ProtocolEnvelope` is uncalled; injecting there is a no-op a reviewer flags. | `protocol_envelope.py` (0 prod callers, OBSERVED) |
| S4 | `_impl` returns a model, no `model_dump` | The producer returns `CreateMediaBuyResult`; serialization stays at the boundary. No `.model_dump()` added in `_impl`. | guard `test_architecture_no_model_dump_in_impl.py` |
| S5 | Three-transport parity | One serializer change covers all three (they all dump `CreateMediaBuyResult`). Add a wire test per transport that the success-replay carries top-level `replayed:true` (§4). | MCP 4194 / A2A 1464 / REST 239 (OBSERVED) |
| S6 | Harness round-trip must pop `replayed` | `MediaBuyCreateEnv.parse_rest_response` (`tests/harness/media_buy_create.py:328`) does `status=data.pop("status")` then `CreateMediaBuySuccess(**data)`. Under `extra="forbid"` (dev/CI), a leftover top-level `replayed` key will raise. The harness must `data.pop("replayed", ...)` before constructing the payload AND surface it (e.g. carry onto the reconstructed `CreateMediaBuyResult`) so A2A/MCP/REST success-replay tests can assert it. | `media_buy_create.py:328-343` (OBSERVED); `extra` mode `_base.py:193` |

---

## 3. Structural guards that fire on this rebuild

All are AST/introspection tests run by `make quality`. For each: what keeps it green, and
whether a #1312 allowlist entry must be removed when rejection-caching is deleted.

| Guard (test file) | What it enforces | Rebuild action to stay green | Allowlist delta |
|---|---|---|---|
| **No Error(code=) in _impl** `test_architecture_no_error_construction_in_impl.py` | `Error(code=...)` literal cap per file | `media_buy_create.py` cap is **1** (line 50, the principal-not-found AUTH_REQUIRED return at `media_buy_create.py:1713`). Don't add `Error(code=...)`; raise typed. Caps must EQUAL actual (test_caps_only_shrink). If deleting rejection code changes nothing here (it raises typed, not `Error(code=)`), cap stays 1. | Verify cap still ==1 after deletion; if the principal-not-found return is also refactored, lower to 0. OBSERVED cap dict line 50. |
| **No raise ValueError in _impl** `test_architecture_no_value_error_in_impl.py` | `raise ValueError` cap per file | `media_buy_create.py` cap is **2** (line 56: the 286 null-session guard + 821 agent_url guard). Idempotency code must not add `raise ValueError`. | Stays 2 unless a deleted helper held one (the rejection helpers don't — OBSERVED). |
| **No model_dump in _impl** `test_architecture_no_model_dump_in_impl.py` | banned `.model_dump()` in `*_impl` | Don't call `.model_dump()` in `_impl`. The canonical hasher already wraps it (`idempotency_canonical.py:79` `canonical_request_hash` does `request.model_dump(mode="json")` OUTSIDE `_impl`). Keep that boundary. `media_buy_create.py` has **no** entries in KNOWN_VIOLATIONS (only media_buy_update/products/listing). | No delta expected. If success-caching serializes a response for storage, do it via repository, NOT `model_dump()` in `_impl`. |
| **No get_db_session in _impl / repository pattern** `test_architecture_repository_pattern.py` | `_impl` uses repos not `get_db_session()`; tests use factories not `session.add()` | `IMPL_SESSION_ALLOWLIST` is **empty (set())** — zero tolerance. All idempotency DB access must go through a repository inside a UoW (the existing code already uses `MediaBuyUoW` + `idempotency_attempts`/`media_buys` repos). New success-cache reads/writes use repo methods. New integration tests use factories (`TenantFactory`, `PrincipalFactory`, `MediaBuyFactory`), never `session.add()`. | Two media_buy_repository allowlist entries reference idempotency tests (lines 252-257) — keep iff those tests survive. INFERRED: if rejection tests are deleted, no repository-pattern allowlist entry changes (those are integration *test* fixtures, not the deleted helpers). |
| **Query type safety** `test_architecture_query_type_safety.py` | DB query filter types match column types | `MediaBuy.idempotency_key` is `String(255)` (`models.py:926`). Lookups pass `str`. The repo `find_by_idempotency_key(idempotency_key: str, ...)` already does (`media_buy.py:63`). No int-cast needed for the key; if any new query filters a PK, cast at boundary. | No delta. |
| **Boundary completeness** `test_architecture_boundary_completeness.py` | MCP+A2A wrappers forward every `_impl` param | The rebuild does NOT add a new `_impl` parameter (`idempotency_key` lives on `req`, not as an `_impl` arg). `_create_media_buy_impl(req, push_notification_config, identity, context_id)` unchanged. So this guard is unaffected — but the wrappers' `idempotency_key` field plumbing (MCP 4172, REST body 68/236, A2A skill param) must be preserved so the key reaches `req`. | No `_impl` signature change → no allowlist delta. |
| **Schema inheritance** `test_architecture_schema_inheritance.py` | local schema classes extend `Library*` base | If adding a field to `CreateMediaBuyResult`, that class is a `SalesAgentBaseModel` (not a Library mirror) — fine. Do NOT add `replayed` by re-deriving the library success type. If a NEW typed error subclass is added (E2), it extends `AdCPError` (not a library type) — also fine (the guard targets schema mirrors, not exceptions). | No delta. |
| **Migration completeness** `test_architecture_migration_completeness.py` | every non-merge migration has non-empty `upgrade()`+`downgrade()`, and downgrade references the same tables | If the rebuild DROPS the `idempotency_attempts` table (because success-caching reuses `MediaBuy.idempotency_key`), write a NEW migration whose `upgrade()` drops the table and `downgrade()` recreates it (referencing `idempotency_attempts`). NEVER edit the committed `097b909c7b5f` / `1d9b1402eacb` migrations. Both branches' migrations already have non-empty up/down (OBSERVED `097b…:27-80`, `1d9b…:22-32`). | INFERRED: a drop migration is the only completeness-relevant new artifact; it must reverse cleanly. |
| **Single migration head** `test_architecture_single_migration_head.py` | exactly one alembic head, zero tolerance | The branch already has a merge migration `ee84c805a0b1` reconciling `097b909c7b5f` + `597485e1799a` (OBSERVED `ee84…:16`). Any NEW migration must chain off the current single head; after rebase onto main, re-check `alembic heads`. If a drop migration is added, it becomes the new head. | No allowlist (zero tolerance). Re-verify head count post-rebase. |
| **Code duplication (DRY)** `check_code_duplication.py` (pre-commit + make quality) | duplicate-block count cannot increase (ratchet `.duplication-baseline`) | Deleting the three near-identical rejection helpers REDUCES duplication — good. But the success-replay path (`_build_idempotency_hit_result`) is already shared; don't reintroduce copy-paste between the early happy-path lookup (1748) and the TOCTOU recovery (2563, 3462). After deletion, the baseline may need lowering (a make-quality run will dirty `.duplication-baseline`). | INFERRED: regenerate `.duplication-baseline` after the deletion (it can only shrink). |
| **Error envelope two-layer** `test_architecture_error_envelope_two_layer.py` | 3 boundaries call `build_two_layer_error_envelope` | Untouched — don't remove the builder calls. The conflict/missing-key errors flow through these boundaries unchanged. | No delta. |
| **Error code compliance** `test_architecture_error_code_compliance.py` | every `Error(code=)` literal + every `AdCPError` subclass `_default_error_code` ∈ STANDARD ∪ INTERNAL ∪ SPEC | New typed subclass codes must be standard. `IDEMPOTENCY_CONFLICT`/`INVALID_REQUEST` are STANDARD (OBSERVED). If E2 adds `AdCPMissingIdempotencyKeyError(_default_error_code="INVALID_REQUEST")`, it passes. If `AdCPIdempotencyExpiredError` is removed, its `IDEMPOTENCY_EXPIRED` default goes with it. | No allowlist; just keep codes standard. |

OBSERVED — the `#1312`-introduced guard FILE is
`tests/unit/test_architecture_no_error_construction_in_impl.py` (in the branch diff). Its
`media_buy_create.py: 1` cap is the principal-not-found return, **not** rejection code, so the
cap is unaffected by deleting rejection helpers. INFERRED — the main allowlist risk is the
`.duplication-baseline` ratchet (must be regenerated downward) and the migration head count
(re-verify after any drop migration + post-rebase).

---

## 4. Wire-envelope test matrix (NOT mock-only)

Policy (OBSERVED `tests/CLAUDE.md` "Error Verification Policy" + `wire_envelope_policy.md`):
new ERROR-path tests assert on `result.wire_error_envelope` via `assert_envelope_shape(...)`.
SUCCESS-path tests assert on `result.payload` + the top-level `replayed` marker.

**OBSERVED — per-transport `wire_error_envelope` authenticity** (`tests/CLAUDE.md` table):
- REST → real HTTP body; MCP → JSON in `ToolError`; A2A → failed-Task DataPart; IMPL → `None`
  (use `synthesized_error_envelope`, which CANNOT catch a boundary regression — both sides call
  the same builder). **So error-path wire coverage must run on REST/MCP/A2A, not IMPL.**

**OBSERVED — drive the real boundary, real auth chain:**
- A2A must drive `handler.on_message_send` and assert on `result.artifacts[0]` DataPart, with
  the real token→DB→identity chain via `ServerCallContext.state[AUTH_CONTEXT_STATE_KEY]`
  (`feedback_a2a_harness_real_auth_chain`). The `MediaBuyCreateEnv.call_a2a`
  (`media_buy_create.py:290`) already delegates to `_run_a2a_handler` which drives
  `on_message_send` — OBSERVED. Reuse it; do NOT call `_handle_*_skill` directly.
- MCP via in-memory FastMCP `Client` (`call_mcp` → `_run_mcp_client`, 304) — OBSERVED.
- REST via FastAPI `TestClient` (`get_rest_client` / `call_via(Transport.REST)`) — OBSERVED.

**The harness already exists**: `MediaBuyCreateEnv` (`tests/harness/media_buy_create.py`) with
`setup_default_data()` (real auth token + tenant + principal), `call_via(transport, ...)`, and
`setup_media_buy_data()`. Reuse it.

### Required matrix (3 scenarios × 3 wire transports + IMPL where meaningful)

| Scenario | IMPL | MCP wire | A2A wire (`on_message_send`) | REST wire (`TestClient`) | Assertion |
|---|---|---|---|---|---|
| **Success replay** (same key, same payload → original buy + `replayed:true`) | call_impl: result.payload.media_buy_id == original; result envelope `replayed` true | call_via(MCP): top-level `replayed:true` + same media_buy_id | call_via(A2A): DataPart top-level `replayed:true` + same media_buy_id | call_via(REST): JSON top-level `replayed:true` + same media_buy_id | NEW success-replay assertion (NOT `assert_envelope_shape`, which is error-only) — see T-helper below |
| **Conflict** (same key, DIFFERENT canonical payload → `IDEMPOTENCY_CONFLICT`) | synthesized envelope: code IDEMPOTENCY_CONFLICT, recovery terminal | wire_error_envelope: `assert_envelope_shape(..., "IDEMPOTENCY_CONFLICT", recovery="terminal")` | same on DataPart | same on HTTP body | `assert_envelope_shape(result.wire_error_envelope, "IDEMPOTENCY_CONFLICT", recovery="terminal")` |
| **Missing key** (mission: `INVALID_REQUEST`) | synthesized: code INVALID_REQUEST | wire_error_envelope INVALID_REQUEST | same | same | `assert_envelope_shape(result.wire_error_envelope, "INVALID_REQUEST")` |

OBSERVED gaps in current tests that must be REPLACED/REMOVED:
- `tests/integration/test_idempotency_replay.py` is entirely rejection-replay:
  `assert_replayed_rejection` (asserts an ERROR envelope with `replayed:true`),
  `seed_rejection`, `record_rejection`, `_raise_idempotency_rejection_replay`,
  `TestTransientRejectionNotCached` — all OBSERVED. These pin the OLD behavior; the rebuild
  must rewrite them for success-replay (or delete + re-author). **Test-integrity rule: do not
  delete to close a gap — port the coverage to the new model.**
- `tests/harness/assertions.py::assert_replayed_rejection` (106-133) asserts the *error*
  envelope carries `replayed:true` — OBSERVED. After rebuild, the analogous helper must assert
  a *success* envelope carries top-level `replayed:true`. INFERRED: add
  `assert_replayed_success(result, *, media_buy_id=...)` (success has no `assert_envelope_shape`
  counterpart today — that helper is error-only, `envelope_assertions.py:22`).
- `tests/harness/media_buy_create.py::seed_rejection` (150) +
  `record_rejection` usage — OBSERVED; become obsolete if success-caching reuses the existing
  `MediaBuy.idempotency_key` row (you seed by creating a real MediaBuy via factory/first call).

INFERRED — `assert_envelope_shape` has NO success variant (it requires `adcp_error` + `errors`,
`envelope_assertions.py:60-62`). The success-replay assertion must read `result.payload`
(reconstructed `CreateMediaBuySuccess`) and the top-level `replayed` — which means
`TransportResult`/the env must surface the top-level `replayed` (it currently drops everything
but `payload`+`status`; see S6). This is a concrete harness obligation, not optional.

---

## 5. BDD obligations

OBSERVED — there is currently **no idempotency BDD feature/steps**. Grep of `tests/bdd/` is
warranted before authoring; the mission's rebuild adds the first idempotency BDD scenarios.

Meta-rule (OBSERVED `reference_bdd_harness_patterns` #54 + `tests/CLAUDE.md`): **every BDD test
passes or xfails, never runtime-fails.** Runtime failure ⇒ usually a missing `_XFAIL_TAGS`
entry, missing `_detect_uc` branch, missing `pytest_plugins` registration, or
`_STATUSLESS_SUCCESS_ATTRS` gap.

### BDD structural guards that fire
| Guard | Requirement for idempotency steps |
|---|---|
| `test_architecture_bdd_no_pass_steps.py` | Then steps must ASSERT, not delegate to `_pending()`/`pass`. |
| `test_architecture_bdd_no_trivial_assertions.py` / `_assertion_strength.py` | Then steps compare VALUES, not just truthiness/existence. The success-replay Then must compare the replayed `media_buy_id` to the ORIGINAL (an independently-captured expected, not a re-derived production expression — `reference_bdd_harness_pitfalls` #7) AND assert `replayed is True`. |
| `test_architecture_bdd_no_dict_registry.py` | Given steps use factories, not raw dicts. |
| `test_architecture_bdd_no_duplicate_steps.py` | No 3+ identical step bodies. |
| `test_architecture_bdd_no_silent_env.py` | No `ctx.get("env")` / `hasattr(env, ...)` in steps. |
| `test_architecture_bdd_no_request_in_then.py` | Then steps read captured result, not the request. |
| `test_architecture_bdd_no_direct_call_impl.py` | When steps use `dispatch_request`/`call_via`, never `env.call_impl()` directly (false-positive coverage). |

### Gold-standard scenarios (INFERRED from spec + patterns)
1. **Success replay**: Given a media buy created with `idempotency_key=K`; When the buyer
   re-issues create_media_buy with the same `K` and the same payload; Then the response
   `media_buy_id` equals the original AND the envelope `replayed` is `true` AND no second
   ad-server booking occurs. (Compare media_buy_id to the captured original — not to a literal.)
2. **Conflict**: Given a media buy created with `K`; When the buyer re-issues with `K` but a
   DIFFERENT payload; Then the error code is `IDEMPOTENCY_CONFLICT` with `recovery=terminal`
   (assert via wire-envelope Then step).
3. **Missing key**: When create_media_buy is issued without an `idempotency_key` and the
   tenant requires one (or per the mission's `INVALID_REQUEST` rule); Then the error code is
   `INVALID_REQUEST`. (Confirm against the actual spec contract — see UNCERTAIN.)

### BDD plumbing obligations
- Register the new step module in `tests/bdd/conftest.py` `pytest_plugins` (OBSERVED
  `reference_bdd_harness_patterns` #24 + `_pitfalls` #8) — an UNREGISTERED step module is DEAD
  (auto-xfails), so a "passing" assertion there is vacuous.
- Add a `_detect_uc` branch + any `_XFAIL_TAGS` entry if production isn't ready, keyed by tag.
- The success-replay Then needs the response type's success collection handled in
  `_STATUSLESS_SUCCESS_ATTRS` only if `CreateMediaBuySuccess` lacks `.status` — it HAS `status`
  (`CreateMediaBuySuccess.model_fields` includes `status`), so likely no entry needed (verify).
- Feature files are auto-generated (`# DO NOT EDIT -- re-run: python scripts/compile_bdd.py`,
  `reference_bdd_harness_patterns` #23) — author the `.feature` via the compile pipeline / spec
  source, not by hand-editing, or the alignment guard fires.

OBSERVED — wire-envelope BDD Then steps exist for errors
(`tests/bdd/steps/generic/then_error.py` referenced in `_pitfalls`); add success-replay Then
variants alongside, and (per `wire_envelope_policy` migration path) prefer wire-envelope
assertions over reconstructed-exception assertions for the conflict/missing-key Then steps.

---

## 6. Equivalence-pin test for the non-lock-in canonical hasher

**OBSERVED — the SDK exposes the exact primitive to pin against:**
`adcp.server.idempotency.canonical_json_sha256(payload: dict[str, Any]) -> str` (+
`strip_excluded_fields`, `EXCLUDED_FIELDS`). Our hasher is
`src/core/idempotency_canonical.py::canonical_payload_hash` (59) which deliberately uses
`rfc8785` directly to avoid adopting the SDK's return-based replay store (docstring 1-14).

**OBSERVED — they currently agree byte-for-byte** (I ran both):
- `EXCLUDED_FIELDS`: SDK `{context, governance_context, idempotency_key}` == ours
  `_EXCLUDED_FIELDS` (`idempotency_canonical.py:27`).
- Hash equality verified on a simple payload AND on a payload differing only in
  `push_notification_config.authentication.credentials` (both strip it → equal hash). So our
  explicit `_NESTED_EXCLUSIONS` (31) matches the SDK's behavior too.
- `canonical_json_sha256(p) == canonical_payload_hash(p)` for the corpus I tried.

**OBSERVED — the existing pin pattern to mirror** is
`tests/unit/test_adcp_spec_version.py`: it asserts `adcp.get_adcp_spec_version() ==
EXPECTED_SPEC_VERSION` with a docstring telling the maintainer what to do when it drifts.
That is the template: a single CI guard that fails loudly on divergence and documents the
reconciliation step. (There is already a unit test for our hasher's *internal* contract:
`tests/unit/test_idempotency_canonical.py` — key-order invariance, exclusions — OBSERVED. The
new test is the cross-SDK *equivalence* pin, which that file does not do.)

### Obligation
| # | Obligation | What the rebuild must do | Source |
|---|---|---|---|
| H1 | Equivalence pin over a corpus | Add a unit test asserting `canonical_payload_hash(p) == adcp.server.idempotency.canonical_json_sha256(p)` for a corpus (simple, key-reordered, nested-credential, excluded-fields, unicode/number-encoding edge cases). Docstring states intent: "we keep our own hasher to avoid the SDK replay-store lock-in; this pins behavioral equivalence so drift is caught." | mirror `test_adcp_spec_version.py`; SDK fn OBSERVED |
| H2 | Exclusion-set parity pin | Assert `_EXCLUDED_FIELDS == adcp.server.idempotency.EXCLUDED_FIELDS` (top-level), with a comment that the nested credential exclusion is ours-additionally and verified equal by H1's corpus. | `idempotency_canonical.py:27`; SDK `EXCLUDED_FIELDS` OBSERVED |
| H3 | Don't import the SDK store | Keep using `rfc8785` directly; the pin proves equivalence WITHOUT adopting `IdempotencyStore`/`PgBackend`. | `idempotency_canonical.py:1-14` (deliberate-deviation already documented; satisfies P10) |

INFERRED — H1 must be a corpus, not a single payload (P9/P28: a one-payload check is weak; the
SDK could diverge on nested/unicode/number edge cases). Build the corpus to exercise RFC 8785's
number canonicalization and the nested-exclusion path, since those are where two independent
implementations most plausibly drift.

---

## Summary of allowlist deltas when rejection-caching is deleted
- `test_architecture_no_error_construction_in_impl.py`: `media_buy_create.py:1` cap is the
  principal-not-found return, NOT rejection code → unchanged (verify ==1 post-deletion). OBSERVED.
- `test_architecture_no_value_error_in_impl.py`: `media_buy_create.py:2` cap (286/821) →
  unchanged (rejection helpers hold no `raise ValueError`). OBSERVED.
- `test_architecture_repository_pattern.py`: `IMPL_SESSION_ALLOWLIST` empty → unchanged; the
  two `test_media_buy_repository.py` idempotency entries (252-257) stay iff those tests survive.
- `.duplication-baseline`: must be REGENERATED downward (deletion reduces dup blocks). INFERRED.
- Migration heads: re-verify exactly one head after any drop migration + after rebase onto main.
- Error envelope `replayed` branch (`exceptions.py:643-644`) + `replayed` attr: remove iff no
  error path sets it (E3). OBSERVED location.

---

## UNCERTAIN (verify before building)

1. **Does success-caching need the `IdempotencyAttempt` table at all?** The success path
   already works off `MediaBuy.idempotency_key` + `find_by_idempotency_key` (OBSERVED). If the
   rebuild caches *successful responses* and replays them, that may be fully served by the
   existing MediaBuy row — making `IdempotencyAttempt` (and its repo/migrations) deletable. OR
   the rebuild wants a generic success-response cache (for tools beyond create_media_buy), which
   would KEEP/repurpose the table. This decision drives whether a drop migration is needed and
   whether the repo/UoW wiring is removed. NOT determinable from code alone.

2. **Missing-key = `INVALID_REQUEST` — is this spec-correct, and is it unconditional?** The
   mission states it. But the current capability advertises optional idempotency
   (`replay_ttl_seconds`); a buyer omitting the key is "opting out" per the existing repo
   docstring (`idempotency_attempt.py` find/record are no-ops on missing key — OBSERVED). So
   `INVALID_REQUEST` for a missing key may apply only when the tenant/tool REQUIRES idempotency,
   not always. Confirm against the AdCP spec (`security.mdx#idempotency`) and the capability
   contract before adding an unconditional raise. (Wrong-here = a behavioral regression buyers
   feel.) There is also NO existing `AdCP*Error` mapping to `INVALID_REQUEST` as its default —
   E2 requires a new typed subclass.

3. **`recovery` for `IDEMPOTENCY_CONFLICT`.** The typed class uses `terminal`
   (`exceptions.py:572`) and the SDK STANDARD table agrees (OBSERVED). But the class docstring
   cites "replay_ttl_seconds capability contract" — confirm the spec doesn't classify it
   `correctable` (resend the original payload recovers). If spec says correctable, this is a
   deliberate-deviation needing the P10 inline comment; if terminal, leave as is.

4. **EXPIRED scope.** Mission says out of scope. `AdCPIdempotencyExpiredError` (575),
   `IDEMPOTENCY_EXPIRED` in the SDK table, the repo `expires_at`/`find_by_key` TTL filter
   (`idempotency_attempt.py:69`), and the `DEFAULT_REPLAY_TTL=86400` all exist. Decide: remove
   EXPIRED entirely, or keep the TTL plumbing but never surface EXPIRED. Leaving an unraised
   subclass is dead taxonomy a reviewer flags (P30). NOT determinable from the mission alone.

5. **`replayed` omit-vs-false on the wire.** Spec ProtocolEnvelope models `replayed` as a bool;
   the existing error builder OMITS it when false (`exceptions.py:643`). INFERRED the success
   serializer should match (omit when false). Confirm storyboard runners accept omitted (they
   do for the error path today) vs require explicit `false`.

6. **Whether a P34 guard should be ADDED.** There is no automated guard forbidding the
   `error_code=` kwarg in `_impl` (OBSERVED — grepped all `test_architecture_*`/`test_no_*`).
   Given this rebuild adds idempotency raises and the codebase has a memory-escalation rule
   ("a lesson violated ≥2× must escalate to a guard"), a reviewer may expect a new guard
   forbidding `AdCP*Error(error_code=...)` outside `synthesize()`. Out of strict rebuild scope,
   but flag it.
