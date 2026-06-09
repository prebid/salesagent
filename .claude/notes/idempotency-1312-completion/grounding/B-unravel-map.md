# B — Unravel Map: PR #1312 rejection-caching idempotency

**Purpose:** complete, verified file:line inventory of the CURRENT (wrong, rejection-caching) idempotency
implementation, classified REMOVE / INVERT / KEEP, to plan the spec-conformant rebuild (cache SUCCESS only,
never cache errors, re-execute on retry-after-error; missing key → INVALID_REQUEST; exact replay → cached
success + top-level `replayed: true`; same-key-different-hash → IDEMPOTENCY_CONFLICT; **EXPIRED out of scope**).

All rows verified by reading the file at the cited line on branch `feature/b6-idempotency-replay-table`
(HEAD `9a689f8c2`). Classification legend:
- **REMOVE** — rejection-caching machinery with no place in success-caching.
- **INVERT** — exists but must flip rejection→success semantics (or required-key relaxation that must flip back).
- **KEEP** — already correct / reusable (canonical hashing, success-side idempotency, typed conflict error).
- **NEUTRAL/OOS** — touched by branch but not idempotency-semantic, or pre-existing-on-main (out of scope of the unravel).

---

## Scope: commits this PR added (feature work, ignoring merge/format commits)

```
49af79a45 feat(idempotency): typed AdCPIdempotencyConflictError / AdCPIdempotencyExpiredError
28aab1bb7 feat(idempotency): payload_hash column + RFC 8785 canonical hasher
577ebc8f0 feat(idempotency): surface replayed in the two-layer error envelope
adcdfdc64 feat(idempotency): convert rejection-replay to raise-based (Pattern A) + migrate tests
9a689f8c2 test(schema-alignment): allowlist get-products push_notification_config (adcp 4.3.0 drift)  [drift, not idempotency]
```
(Plus earlier history `cbf5ef527` / `122180fc7` / `94a4a8948` "cache rejection envelopes for replay (B6, #1303 contract 7)" — squashed/re-landed across rebases; the four feat commits above are the live shape.)

---

## 1. `src/core/tools/media_buy_create.py` (the `_impl` flow)

| file:line | what | class | why |
|---|---|---|---|
| `:18` `from sqlalchemy.exc import IntegrityError` | import | KEEP | also used by the success-side TOCTOU recovery (`media_buys` unique index). Still needed. |
| `:43` `AdCPIdempotencyConflictError` import | import | KEEP | conflict error survives. |
| `:1488-1512` `_raise_idempotency_rejection_replay()` | reconstructs cached rejection envelope → `AdCPError.synthesize(...)`, sets `exc.replayed=True`, raises | **REMOVE** | replaying a cached **error** is the inverse of the spec. Whole function dies. |
| `:1511` `exc.replayed = True` (inside above) | marks the replayed **error** | **REMOVE** | `replayed` belongs on a replayed **success**, never an error. |
| `:1515-1566` `_cache_rejection_envelope()` | writes the rejection wire envelope + payload_hash to `IdempotencyAttempt` via `record_rejection` | **REMOVE** | caching rejections is the core defect. |
| `:1569-1584` `_cache_rejection_and_raise()` | cache-then-raise wrapper | **REMOVE** | wrapper over the remove-target; all 4 call sites die with it. |
| `:1587-1643` `_build_idempotency_hit_result()` | re-queries the winning `MediaBuy` by key, rebuilds `CreateMediaBuySuccess` | **INVERT** | success-replay path is correct and stays — but it builds `CreateMediaBuySuccess` **without `replayed=True`** (`:1633-1641`). Must set `replayed=True` on replay. Used by both the happy-path probe and 3 TOCTOU recovery sites. |
| `:1612-1616` `raise AdCPValidationError("... not found after race resolution")` | guards the re-query | KEEP | legit post-race safety; unrelated to rejection caching. |
| `:1732-1734` `from ...idempotency_canonical import canonical_request_hash` + `payload_hash = canonical_request_hash(req) if req.idempotency_key else None` | computes canonical hash once | **INVERT** | hashing is correct/reusable, but it's computed only `if req.idempotency_key` (relaxed key) and is currently fed to the **rejection** cache + a rejection-only conflict check. In the rebuild it must key the **success** cache + success conflict check, and key must be required. |
| `:1735-1762` success-replay probe: `find_by_idempotency_key(...)` → `_build_idempotency_hit_result(...)` | returns existing buy on key match | **INVERT** | correct direction (success replay) but: (a) returns no `replayed:true`; (b) **does NOT compare `payload_hash`** → a same-key/different-payload retry that already succeeded silently returns the original instead of IDEMPOTENCY_CONFLICT. Conflict detection must move HERE. |
| `:1754-1761` FIXME(idempotency-adapter) advisory-rebuild note | known limitation | NEUTRAL | pre-existing caveat on the success path; not rejection machinery. |
| `:1764-1768` `cached_rejection = idem_uow.idempotency_attempts.find_by_key(...)` | rejection-cache lookup | **REMOVE** | the rejection lookup branch. |
| `:1769-1775` `if cached_rejection is not None:` → hash-mismatch → `raise AdCPIdempotencyConflictError(...)` | conflict-on-hash-mismatch, keyed off the **rejection** row | **INVERT** | the conflict-raise itself (code, message, suggestion, recovery=terminal) is spec-correct and reusable — but it must compare against the **success** row's hash, not the rejection row's. Move into the success branch. |
| `:1776-1780` rejection replay → `_raise_idempotency_rejection_replay(...)` | replays cached error | **REMOVE** | inverse of spec. |
| `:1719-1731` block comment ("two lookups… rejection re-raises the cached rejection…") | doc of the wrong model | **INVERT** | rewrite to the success-caching model (also says "adcp 3.12 spec" — our pin is 3.0.1 via adcp 4.3.0). |
| `:2332-2349` early-validation `except (AdCPError, ValueError, PermissionError)` → `_cache_rejection_and_raise(typed, ...)` | caches validation rejections | **REMOVE** (the `_cache_rejection_and_raise` call); audit/normalize stays | errors must NOT be cached; on rebuild this becomes audit-then-raise (no cache). `ctx_manager.audit_step_failure_if_present` + `normalize_to_adcp_error` are KEEP. |
| `:2535-2570` pending-approval create (`create_from_request(... status="pending_approval" ...)`) + IntegrityError→`_build_idempotency_hit_result` | success persist + success-side TOCTOU recovery | **INVERT** | success persist stays; but `create_from_request` here **does not persist payload_hash** (no `media_buys.payload_hash` column exists — see §4). To detect conflict on a later replay, the success row needs a stored hash. Recovery call must also propagate `replayed=True`. |
| `:2904-2912` GAM-config-validation failure → `_cache_rejection_and_raise(AdCPValidationError(...))` | caches a config rejection | **REMOVE** (the cache call); raise stays | error, must re-execute on retry. |
| `:3285-3291` missing start/end time → `_cache_rejection_and_raise(AdCPValidationError(...))` | caches a validation rejection | **REMOVE** (the cache call); raise stays | error, must re-execute. |
| `:3358-3386` adapter-error branch: `AdCPError.synthesize(...)` then `if recovery_value != "transient": _cache_rejection_and_raise(adapter_exc, ...)` else `raise` | caches non-transient adapter rejections | **REMOVE** (the cache call); raise stays | adapter rejection is an error; never cache. The transient-skip distinction becomes moot (nothing cached). `AdCPError.synthesize` reconstruction stays. |
| `:3433-3469` auto-approved create (`create_from_request(... status=media_buy_status ...)`) + IntegrityError→`_build_idempotency_hit_result` | success persist + success-side TOCTOU recovery | **INVERT** | same as pending path: persist payload_hash on success; recovery must carry `replayed=True`. |
| `:4115-4124` MCP wrapper `idempotency_key` Annotated param + description | wrapper param | **INVERT** (docstring) / KEEP (param) | param plumbing stays. Description ("returns the cached rejection envelope verbatim") describes the wrong model — rewrite. |
| `:4149` MCP docstring "Optional client-supplied key for replay-after-rejection" | docstring | **INVERT** | wrong-model wording. |
| `:4172` `idempotency_key=idempotency_key` (into `CreateMediaBuyRequest`) | wrapper→request | KEEP | plumbing. |
| `:4208` A2A raw param `idempotency_key: str \| None = None` | wrapper param | KEEP | plumbing. |
| `:4230` A2A docstring "Optional client-supplied key for replay-after-rejection" | docstring | **INVERT** | wrong-model wording. |
| `:4254` `idempotency_key=idempotency_key` (A2A → request) | wrapper→request | KEEP | plumbing. |

**Highest-risk in this file:** the 4 `_cache_rejection_and_raise` call sites (`:2343`, `:2906`, `:3285`, `:3379`) and the rejection probe (`:1764-1780`) — removing them changes the error path of EVERY create_media_buy rejection. The success-replay probe (`:1741-1762`) and `_build_idempotency_hit_result` are load-bearing and must be carefully INVERTED (add hash compare + `replayed`), not removed.

---

## 2. `src/core/exceptions.py`

| file:line | what | class | why |
|---|---|---|---|
| `:184-187` `replayed: bool` instance attr on `AdCPError` (+ docstring "rejection-replay path sets it") | error-replay marker | **REMOVE** | `replayed` on an **error** has no place; the marker moves to the success response type. (If kept transitionally it must never be set true on an error.) |
| `:214` `self.replayed = False` in `AdCPError.__init__` | default init | **REMOVE** (with the attr) | tied to the error-replay marker. |
| `:563-572` `AdCPIdempotencyConflictError(AdCPConflictError)` (409, `IDEMPOTENCY_CONFLICT`, recovery=terminal) | typed conflict error | **KEEP** | spec-correct; reused by the success-side conflict path. |
| `:575-585` `AdCPIdempotencyExpiredError` (410, `IDEMPOTENCY_EXPIRED`, terminal) | typed expired error | **REMOVE** | EXPIRED is OUT OF SCOPE (expired → cache miss → re-execute). The typed class should be deleted. |
| `:603-645` `build_two_layer_error_envelope(exc)` | wire envelope builder (SSOT) | **KEEP** (builder) / **REMOVE** (lines `:641-644`) | the builder is the wire SSOT and stays. But `:641-644` `if exc.replayed: envelope["replayed"] = True` emits `replayed` on an **error** envelope — REMOVE (success-caching never emits a replayed error). |
| `:415-420` `AdCPConflictError` (409, CONFLICT, correctable) | parent of conflict error | KEEP | base class, unrelated. |
| `:429-439` `AdCPGoneError` (410, INVALID_STATE) | 410 base | NEUTRAL | prior PLAN wanted EXPIRED to extend this; moot now that EXPIRED is dropped. |

**Note:** `AdCPIdempotencyConflictError` extends `AdCPConflictError` but overrides recovery to `terminal` (parent is `correctable`) — intentional, documented at `:566-568`. Keep as-is.

---

## 3. `src/core/database/repositories/idempotency_attempt.py` (the rejection store)

| file:line | what | class | why |
|---|---|---|---|
| `:1-11` module docstring ("cached rejection envelopes… handles the rejection path") | doc | **REMOVE/INVERT** | describes the rejection store. If the table is repurposed as a **success** cache, rewrite; if dropped, delete. |
| `:24` `DEFAULT_REPLAY_TTL = timedelta(seconds=86400)` | TTL constant | **REMOVE** (or repurpose) | TTL only matters for EXPIRED, now out of scope. A success cache that never expires errors needs no error-TTL; if a success-replay TTL is wanted it's a separate decision. Drop unless rebuild reuses the table for success rows. |
| `:27-45` `IdempotencyAttemptRepository.__init__` / `tenant_id` | repo shell | **REMOVE** (or repurpose) | tenant-scoped CRUD over the rejection table. Survives only if the table is repurposed for success caching. |
| `:47-73` `find_by_key(...)` (filters `expires_at > now`, "expired treated as absent") | rejection lookup | **REMOVE** (or **INVERT**) | this IS the rejection-cache read. Note `:69` `expires_at > current` already collapses absent+expired (i.e. expired → re-execute) — which is the spec behavior for EXPIRED-out-of-scope, but on the wrong (rejection) store. |
| `:75-110` `record_rejection(...)` (writes envelope + payload_hash + expiry) | rejection write | **REMOVE** (or **INVERT** to `record_success`) | core rejection-write. If repurposed: store success response + hash, not a rejection envelope. |
| `:112-132` `expire_old(...)` ("No production caller wired yet… grows unbounded") | TTL reaper | **REMOVE** | dead (no caller); tied to TTL/EXPIRED. The "unbounded growth" caveat disappears once rejections aren't cached. |
| (whole file) | | **REMOVE-or-INVERT decision point** | If rebuild caches successes in a NEW table/structure keyed by hash, this entire file is REMOVE. If rebuild repurposes `idempotency_attempts` to hold success responses, it's INVERT (rename methods, change semantics). Rebuild design must pick one. |

---

## 4. Models + migrations

| file:line | what | class | why |
|---|---|---|---|
| `src/core/database/models.py:926` `MediaBuy.idempotency_key: Mapped[str \| None]` | success-side idempotency column | **KEEP** | the correct success idempotency; added by main (#1217, migration `d40df2c92316`, 2026-04-17), NOT this PR. |
| `models.py:967-974` partial unique index `idx_media_buys_idempotency_key` (tenant, principal, key, WHERE key NOT NULL) | success-side unique index | **KEEP** | enforces success-side idempotency + drives the TOCTOU IntegrityError recovery. Pre-existing on main. |
| `models.py:978-1044` `IdempotencyAttempt` model ("Cached rejection envelope…") | rejection table model | **REMOVE** (or **INVERT**) | the rejection store ORM. `:1013` `payload_hash`, `:1018-1022` `response_envelope` ("Cached rejection envelope"), `:1023` `expires_at`. If repurposed for success caching, INVERT (rename/repoint comments, drop `expires_at` if no success-TTL). |
| `models.py` — **absence of `media_buys.payload_hash`** | missing column | **GAP (rebuild adds)** | the success path stores NO payload_hash, so success-side conflict detection is impossible today. Rebuild must add `media_buys.payload_hash` (the prior PLAN's "F8") OR store the hash wherever the success cache lives. |
| `alembic/.../097b909c7b5f_add_idempotency_attempts_table.py` (whole) | creates `idempotency_attempts` table (down_revision `b4e2bffdd4f8`) | **REMOVE** (or repurpose) | this PR's table-creation migration. Dropping the table = new down migration (never edit committed migrations). |
| `alembic/.../1d9b1402eacb_add_payload_hash_to_idempotency_attempts.py` (whole) | adds `payload_hash` to `idempotency_attempts` (down_revision `ee84c805a0b1`) | **REMOVE** (or repurpose) | this PR's payload_hash column migration. |
| `alembic/.../ee84c805a0b1_merge_idempotency_replay_table_with_.py` (whole) | empty merge head joining `097b909c7b5f` + main's `597485e1799a` | NEUTRAL/REMOVE | merge migration; only relevant for graph topology. Single-head must be preserved on rebuild. |
| `alembic/.../d40df2c92316_add_idempotency_key_to_media_buys.py` | adds `media_buys.idempotency_key` + index | **KEEP / OOS** | **on main** (#1217), NOT this PR. Do not touch. |

---

## 5. `src/core/tools/capabilities.py` (capability advertisement)

| file:line | what | class | why |
|---|---|---|---|
| `:21` `Idempotency,` import | SDK type import | KEEP | the capability type stays. |
| `:92` `idempotency=Idempotency(supported=True, replay_ttl_seconds=86400)` (site 1) | advertises idempotency | **KEEP / REVIEW** | idempotency IS supported in the rebuild → keep `supported=True`. But `replay_ttl_seconds` ties to the EXPIRED window (now out of scope). Confirm whether the spec capability still requires a TTL value when EXPIRED isn't implemented; adjust/justify. |
| `:265` `idempotency=Idempotency(supported=True, replay_ttl_seconds=86400)` (site 2) | second advertisement site | **KEEP / REVIEW** | same as site 1 — both sites must stay in sync (DRY: consider one constant). |

---

## 6. Schemas (required-key relaxation) + protocol_envelope

| file:line | what | class | why |
|---|---|---|---|
| `src/core/schemas/_base.py:1441-1446` `CreateMediaBuyRequest`: comment + `idempotency_key: str \| None = None  # type: ignore[assignment]` | relaxes adcp-4.3 REQUIRED key → optional | **INVERT** | spec mandates key REQUIRED, missing → INVALID_REQUEST. Must flip back to required (or enforce required at the boundary). **The comment claims "idempotency_key is generated at the transport boundary when not supplied" — FALSE: no such generation exists for create_media_buy (verified, see UNCERTAIN below).** |
| `_base.py:1630-1634` `UpdateMediaBuyRequest`: same relaxation | relaxes REQUIRED key → optional | **INVERT** | same as above; same false generation claim at `:1631`. |
| `_base.py:196` `class CreateMediaBuySuccess(AdCPCreateMediaBuySuccess)` — **no `replayed` field** | success response type | **GAP (rebuild adds)** | success-replay cannot surface `replayed: true` today — the field does not exist. Rebuild must add `replayed: bool = False` here (and serialize it). The prior PLAN claimed this would be added; it was NOT. |
| `src/core/protocol_envelope.py:58-173` local `ProtocolEnvelope` (BaseModel) — **no `replayed` field**; `wrap()` doesn't set it | hand-rolled envelope | **GAP / verify-dead** | library `ProtocolEnvelope` has `replayed`; this local one does not. Prior PLAN called `wrap()` "dead" and said `replayed` belongs on `CreateMediaBuySuccess` not here. Confirm `wrap()` is unused on the create path before deciding. Not rejection machinery — a missing-feature gap. |

---

## 7. Local canonical hasher `src/core/idempotency_canonical.py`

| file:line | what | class | why |
|---|---|---|---|
| (whole file `:1-79`) | RFC 8785 canonical hashing with closed exclusion list | **KEEP** | spec-correct and explicitly non-lock-in (keeps own `rfc8785` rather than `adcp.server.idempotency` per the new direction). |
| `:27` `_EXCLUDED_FIELDS = {idempotency_key, context, governance_context}` | top-level exclusions | KEEP | spec closed list. |
| `:31` `_NESTED_EXCLUSIONS` (webhook credentials) | nested exclusion | KEEP | rotated credential must not change the hash. |
| `:59-69` `canonical_payload_hash(payload)` | dict → sha256 hex | KEEP | reused for success-cache keying + conflict detection. |
| `:72-79` `canonical_request_hash(request)` | Pydantic model → hash (does `model_dump` here so `_impl` stays no-model-dump) | KEEP | the production entry point; keep. |
| `pyproject.toml` `rfc8785` dependency | dep pin | KEEP | only importer is this file; keep since we keep the hasher. (Prior PLAN's ARCH-1 wanted to delete this fork + drop `rfc8785` — that is REVERSED by the new non-lock-in direction.) |

---

## 8. Tests asserting current (rejection) behavior

| file:line | what | class | why |
|---|---|---|---|
| `tests/integration/test_idempotency_replay.py` (whole, 486 lines) | replay-after-**rejection** through `_impl` | **INVERT** | entire premise inverts. Sub-classes: `TestRaiseIdempotencyRejectionReplay` (`:41`), `TestCacheRejectionEnvelopeWritesRow` (`:77`), `TestImplReplaysCachedRejection` (`:166`), `TestWirePathReplay` (`:290`, MCP/A2A/REST), `TestTransientRejectionNotCached` (`:371`) — all REMOVE/INVERT. The wire-path scaffolding (harness env at `:30`, transport dispatch) is reusable for success-replay tests. |
| `test_idempotency_replay.py:80-130` `test_no_key_is_noop`, `test_duplicate_cache_is_swallowed_via_integrity_error` | rejection-cache write tests | **REMOVE** | test the rejection write path. |
| `test_idempotency_replay.py:231-287` `test_unrelated_key_does_not_replay` | rejection isolation | **INVERT** | becomes success-isolation. |
| `test_idempotency_replay.py:425-477` `test_transient_/non_transient_adapter_rejection_is(_not)_cached` | transient-skip caching | **REMOVE** | nothing cached on error in the rebuild; distinction is moot. |
| `tests/integration/test_idempotency_attempt_repository.py` (whole, 325 lines) | `record_rejection` / `find_by_key` / `expire_old` CRUD | **REMOVE** (or **INVERT** if table repurposed) | classes `TestRecordRejection` (`:40`), `TestFindByKey` (`:110`), `TestExpireOld` (`:251`). `expire_old` tests (`:251-325`) REMOVE outright (EXPIRED gone). |
| `tests/integration/test_idempotency_race.py` (whole, 35 matches) | success-side TOCTOU on `media_buys` unique index | **KEEP** | tests the CORRECT success path: `TestIdempotencyRaceDbLevel` (`:46`), `TestBuildIdempotencyHitResult` (`:96`), `TestIdempotencyRaceRecovery` (`:140`) — all about `find_by_idempotency_key` + `_build_idempotency_hit_result`, not rejection caching. May need `replayed:true` assertion added. |
| `tests/unit/test_idempotency_canonical.py` (whole, `:12-78`) | pure hasher tests | **KEEP** | tests the hasher we're keeping. |
| `tests/unit/test_adcp_exceptions.py:102-110` `test_idempotency_conflict_error` | conflict typed-error | **KEEP** | conflict survives. |
| `test_adcp_exceptions.py:121-128` `test_idempotency_conflict_wire_envelope` | conflict wire shape | **KEEP** | conflict survives. |
| `test_adcp_exceptions.py:112-119` `test_idempotency_expired_error` | expired typed-error | **REMOVE** | EXPIRED out of scope. |
| `test_adcp_exceptions.py:130-137` `test_idempotency_expired_wire_envelope` | expired wire shape | **REMOVE** | EXPIRED out of scope. |
| `test_adcp_exceptions.py:139-149` `test_replayed_flag_surfaces_in_envelope` | asserts `replayed` on an **error** envelope | **REMOVE** | `replayed` on errors dies. (A new success-side `replayed` test replaces it.) |
| `tests/unit/test_media_buy.py:1187-1247` `test_idempotency_replay_returns_existing` | mock success-replay | **KEEP/INVERT** | tests success replay (correct direction); add `replayed:true` assertion. Mock-only (P23) — prefer the integration version. |
| `test_media_buy.py:1249-1299` `test_idempotency_absent_proceeds_normally` | absent key → proceeds | **INVERT** | spec says absent key → INVALID_REQUEST, not "proceeds." |
| `test_media_buy.py:1302-1362` `test_idempotency_cached_rejection_replayed` | mock rejection-replay | **REMOVE** | mock-only rejection replay; dies. |
| `test_media_buy.py:1364-1432` `test_idempotency_new_key_proceeds` | new key → new buy | **KEEP** | new-key path stays. |
| `tests/integration/test_create_media_buy_account_wire.py` (4 matches) | account-wire test (sets `idempotency_key=`) | NEUTRAL | incidental key usage, not rejection-semantic. |
| `tests/unit/test_error_format_consistency.py` (+`IDEMPOTENCY_CONFLICT`/`+IDEMPOTENCY_EXPIRED` in known-codes set) | wire-code allowlist | **KEEP** `IDEMPOTENCY_CONFLICT` / **REMOVE** `IDEMPOTENCY_EXPIRED` | conflict stays, expired drops. |
| `tests/unit/test_architecture_no_error_construction_in_impl.py` (cap `media_buy_create.py` 3→1) | guard allowlist | NEUTRAL | merge-driven cap change, not idempotency-specific. Re-derive cap after rebuild. |
| `tests/unit/test_pydantic_schema_alignment.py` (+`push_notification_config`) | drift allowlist | NEUTRAL/OOS | the `9a689f8c2` drift commit; unrelated to idempotency. |

### Test harness (`tests/harness/`)

| file:line | what | class | why |
|---|---|---|---|
| `tests/harness/assertions.py:106-132` `assert_replayed_rejection(...)` | asserts an ERROR result with `replayed:true` | **REMOVE** | success-caching never replays an error. Replace with a success-replay assertion. |
| `tests/harness/media_buy_create.py:150-174` `seed_rejection(...)` (calls `record_rejection`) | seeds a cached rejection row | **REMOVE/INVERT** | becomes `seed_success` (a prior successful buy) if needed. |
| `tests/harness/media_buy_create.py:328-340` `parse_rest_response` ("A replayed rejection has errors and no media_buy_id") | parses replayed-rejection wire shape | **INVERT** | replay is now a success payload (has media_buy_id + replayed:true), not an error envelope. |
| `tests/harness/__init__.py:49,79` export `assert_replayed_rejection` | re-export | **REMOVE** | tied to the removed assertion. |
| `tests/harness/dispatchers.py` (`wire_error_envelope = ... or _wire_envelope_from_exception(exc)`) | general wire-envelope plumbing | KEEP | general harness infra (also used by A2A); not rejection-specific. |
| `tests/helpers/adcp_factories.py` (idempotency match) | factory sets `idempotency_key` | NEUTRAL | incidental. |

### Wiring (UoW / repo registry)

| file:line | what | class | why |
|---|---|---|---|
| `src/core/database/repositories/__init__.py:21,42` export `IdempotencyAttemptRepository` | registry | **REMOVE** (or INVERT) | tied to the rejection repo. |
| `src/core/database/repositories/uow.py:40,136,143,149` `idempotency_attempts: IdempotencyAttemptRepository \| None` wiring | UoW property | **REMOVE** (or INVERT) | tied to the rejection repo. |

---

## 9. BDD (`tests/bdd/`)

**VERIFIED: this PR touched ZERO files under `tests/bdd/`** (`git diff --name-only main...HEAD -- tests/bdd/` is empty). All BDD idempotency content is pre-existing on main.

| file | what | class | why |
|---|---|---|---|
| `tests/bdd/features/BR-UC-002-create-media-buy.feature` | **has NO idempotency/replay scenario** | **GAP (rebuild authors)** | the create_media_buy feature has no idempotency Scenario at all. The prior PLAN's "author UC-002 idempotency scenario" is **net-new authoring**, not an unravel item. |
| `tests/bdd/steps/domain/uc002_create_media_buy.py:594-655, 753-755` | idempotency_key step defs (replay/absent/length) | **KEEP / OOS** | came from main merges, not this PR. Describe success-replay + "absent key proceeds without protection" — closer to spec than this PR's model. |
| `BR-UC-003-update-media-buy.feature:343-373, 897-917, 1051-1091` + `uc003_update_media_buy.py` | update_media_buy idempotency scenarios | **KEEP / OOS** | pre-existing; not this PR. |
| `BR-UC-006-sync-creatives.feature:922-958`, `BR-UC-017-...:225-...,929-1130`, `BR-UC-009`, `BR-UC-011`, `BR-UC-026` + their steps | idempotency for other tools | **KEEP / OOS** | all pre-existing; not this PR's rejection model. |

---

## Counts

- **REMOVE:** ~24 distinct sites (helpers `_raise_idempotency_rejection_replay`, `_cache_rejection_envelope`, `_cache_rejection_and_raise`; the 4 cache-and-raise call sites; rejection probe `:1764-1780`; `AdCPError.replayed` attr + init + envelope emission; `AdCPIdempotencyExpiredError`; entire rejection repo + its UoW/registry wiring + 2 migrations; the rejection-asserting tests + harness `assert_replayed_rejection`/`seed_rejection`/exports + expired tests + the mock rejection-replay test).
- **INVERT:** ~13 sites (success-replay probe `:1741-1762` + `_build_idempotency_hit_result` need hash-compare + `replayed`; the conflict-raise moves to the success branch; payload_hash computation repoints to success; both `create_from_request` success persists need a stored hash; the 2 schema relaxations; wrapper docstrings; `test_idempotency_replay.py` + repo tests if table repurposed; harness `parse_rest_response`; `test_media_buy.py` success tests get `replayed`/absent-key inversion).
- **KEEP:** ~12 sites (entire `idempotency_canonical.py` + `rfc8785` dep + its tests; `AdCPIdempotencyConflictError` + its 2 tests; `MediaBuy.idempotency_key` column + index + `find_by_idempotency_key` + `test_idempotency_race.py`; capability `supported=True` (×2, with TTL review); wire plumbing in dispatchers; conflict in error-format set; all pre-existing BDD).
- **GAP (rebuild ADDS, not in current code):** `media_buys.payload_hash` column + migration; `replayed: bool` on `CreateMediaBuySuccess`; success-side conflict comparison; UC-002 BDD idempotency scenario.
- **NEUTRAL/OOS:** the schema-alignment drift commit, the guard cap change, account-wire test, `d40df2c92316` migration (on main), all pre-existing BDD.

## Highest-risk items
1. **The 4 `_cache_rejection_and_raise` call sites** (`media_buy_create.py:2343, 2906, 3285, 3379`) — they sit in every rejection exit. Removing the cache call while preserving the raise/audit/normalize is delicate; the early-validation site (`:2332-2349`) also does `audit_step_failure_if_present` + `normalize_to_adcp_error` which must survive.
2. **`_build_idempotency_hit_result` + success-replay probe** (`:1587-1643`, `:1741-1762`) — load-bearing success path with 3 additional TOCTOU callers. Must be INVERTED (add hash compare + `replayed:true`), and the conflict-raise relocated here, without breaking the race recovery.
3. **Missing `media_buys.payload_hash`** — success-side conflict detection is impossible until this column (or an equivalent success-cache hash store) is added. This is the structural reason the current model could only do conflict detection on the rejection store.
4. **Schema required-key relaxation** (`_base.py:1446, 1634`) paired with the **false "generated at boundary" claim** — flipping to required (or boundary-enforced) changes the contract for every caller that omits the key.
5. **Migration removal** — `097b909c7b5f` + `1d9b1402eacb` + the merge head; dropping the table needs a NEW down migration (never edit committed ones) and must preserve single-head.

## UNCERTAIN / things to verify in design phase
- **[OBSERVED] No idempotency_key generation exists for create/update_media_buy.** `grep` for `uuid`/`generate` near `idempotency_key` finds only `accounts.py:464,711` (sync_accounts). The schema comments at `_base.py:1443` and `:1631` ("generated at the transport boundary when not supplied") are **FALSE** for these tools — the key stays `None` and idempotency is silently skipped. [INFERRED] this means today, omitting the key = no idempotency at all (matches the pre-existing BDD "absent key proceeds without protection"), which the spec-conformant rebuild must change to INVALID_REQUEST.
- **[OBSERVED] `protocol_envelope.py` local `ProtocolEnvelope` has no `replayed` and its `wrap()` doesn't reference it.** [INFERRED, NOT verified] whether `wrap()` is on the live create_media_buy response path or dead — the prior PLAN called it "dead." Must confirm before deciding where `replayed` surfaces (almost certainly on `CreateMediaBuySuccess`, per §6).
- **Rejection-repo fate is a design fork, not a fact:** REMOVE the whole `idempotency_attempt.py` table+repo, OR repurpose it as a success-response cache keyed by hash. The map flags both; the rebuild design must choose. If the success path already idempotents via `media_buys` + a new `payload_hash` column, the separate table may be entirely unnecessary (REMOVE).
- **Capability TTL:** `replay_ttl_seconds=86400` at `capabilities.py:92,265` advertises a window for a behavior (EXPIRED) the rebuild won't implement. [UNCERTAIN] whether the adcp 4.3 `Idempotency` capability type requires/permits omitting `replay_ttl_seconds`, or whether advertising it without EXPIRED enforcement is a conformance problem. Verify against the SDK type + spec.

## Where the prior PLAN.md inventory was WRONG (verified fresh)
1. **PLAN said (line ~130/51) the first migration `097b909c7b5f` is "for idempotency_attempts.payload_hash".** FALSE — `097b909c7b5f` creates the **table** (no payload_hash); `1d9b1402eacb` adds payload_hash. [OBSERVED: both migration files read.]
2. **PLAN's "F8" assumes adding `media_buys.payload_hash` is the remaining work, implying the success path otherwise does conflict detection.** Partially misleading: [OBSERVED] the success-replay probe (`:1741-1762`) does NO hash comparison at all today; conflict detection exists ONLY on the rejection store (`:1770`). So success-side conflict is entirely absent, not just missing a column.
3. **PLAN (2c) said it would add `replayed` to `CreateMediaBuySuccess` and local `ProtocolEnvelope`.** [OBSERVED] neither has `replayed` — those edits were never made. `replayed` exists ONLY on `AdCPError` (the error path). So success-replay surfacing of `replayed` does not exist.
4. **PLAN (Phase 3 3f) framed "author UC-002 BDD idempotency scenario."** [OBSERVED] this PR touched zero BDD files and UC-002 has no idempotency scenario — correct that it's net-new, but it is NOT part of the current (wrong) implementation to unravel; it's greenfield for the rebuild.
5. **PLAN's ARCH-1 (delete local hasher, import `adcp.server.idempotency`, drop `rfc8785`) is REVERSED by the new direction** (keep our own hasher, non-lock-in). The local hasher + `rfc8785` are KEEP, not REMOVE.
6. **PLAN line refs are stale** (e.g. `:1748`/`:2563`/`:3462` for TOCTOU, `:1772` for conflict, `:1539` for the SSOT comment, `:1587-1644` for the hit-result). They are CLOSE but were written against an earlier base; the live refs in this map supersede them.
