# Idempotency #1312 — Spec-Conformant Rebuild: Implementation Plan

**Status:** REVIEW-HARDENED — R1/R2/R3 adversarial pass applied (see "Review corrections" at end; those OVERRIDE the body where they conflict). 2026-06-09.
**Branch:** `feature/b6-idempotency-replay-table`.
**Reads:** `SYNTHESIS.md` (contract + decisions), `grounding/{A,B,C}-*.md` (detail). This is the build plan.

**▶ IMPLEMENTATION START HERE:** (1) Capture baseline — `tox -e bdd -- -k idempotency` + the create-flow idempotency tests (needs Docker/DB) — so we know green-vs-xfail BEFORE the breaking required-key change. (2) Implement on the commit-units in **"Review corrections § Sequencing fix"** at the END of this file — NOT the body's 9-step order. The **"Review corrections"** section OVERRIDES the body wherever they conflict. The CLAUDE.md spec-grounding gate is already added (CLAUDE.md § AdCP Spec Version). Branch is already `feature/b6-idempotency-replay-table` (no branch cut needed).

## Settled decisions
- **β (chosen):** repurpose `idempotency_attempts` as a generic SUCCESS-response cache (store envelope + hash + TTL; replay verbatim; conflict on hash mismatch). Wire `create_media_buy` now; substrate generalizes to other mutating tools (fast-follow).
- **D2:** restore required `idempotency_key`; missing → emit via existing schema validation as `VALIDATION_ERROR` (storyboard-accepted alternative to `INVALID_REQUEST`); correctable. *(verify V2)*
- **D3 (RESOLVED — keep terminal):** `IDEMPOTENCY_CONFLICT` recovery stays `terminal`, matching the installed SDK validator + the existing `AdCPIdempotencyConflictError` + `test_adcp_exceptions.py:122`. Document the spec(correctable)-vs-SDK(terminal) divergence in a comment, mirroring `AdCPAuthenticationError` (exceptions.py:342-346). Do NOT switch to correctable (fights SDK validator, breaks the test).
- **D-rate:** rate-limiting is a spec MUST but ungraded + orthogonal → explicit fast-follow, not this PR.
- **EXPIRED:** out of scope (spec-permitted); honor + advertise TTL durably; post-TTL = miss → re-execute.

## Two latent spec bugs β fixes (beyond the headline rejection-caching inversion)
1. **Rejection-caching → success-caching** (the headline): never cache errors; retry-after-error re-executes.
2. **Live-advisory-rebuild breaks byte-for-byte** (media_buy_create.py:1626-1631): the current replay rebuilds the property_list advisory "live" so a flipped capability changes the replay — a byte-for-byte violation. β replays the stored envelope verbatim (advisory frozen), which is spec-correct.
3. **Atomicity:** β commits the success-cache write in the MediaBuy's own transaction, closing the crash-between-success-and-cache window the SDK's own `PgBackend` docstring flags as a limitation.

## Target control flow (success-caching middleware) — create_media_buy
```
# boundary/schema: idempotency_key REQUIRED → missing rejected as VALIDATION_ERROR before _impl
hash = canonical_request_hash(req)                       # always (key now required)
with UoW(tenant) as uow:                                 # READ
    cached = uow.idempotency_attempts.find_by_key(scope, "create_media_buy", key)
    if cached:
        if cached.payload_hash != hash:
            raise AdCPIdempotencyConflictError(...)       # terminal, documented; body = code+msg only
        return replay_verbatim(cached.response_envelope, replayed=True)   # handler NOT run
# miss → run create logic ...
with UoW(tenant) as create_uow:                          # SAME txn as the buy
    buy = create_uow.media_buys.create_from_request(req, identity)
    success = CreateMediaBuyResult(response=CreateMediaBuySuccess(...), status=...)
    create_uow.idempotency_attempts.record_success(scope, "create_media_buy", key, hash, success.serialize())
    return success
# TOCTOU: concurrent miss → unique(scope,key) IntegrityError on record_success → re-read attempt → replay verbatim
```
**Scope:** `(tenant_id, principal_id, account_id?, "create_media_buy", key)` — see V1.

## Change-set

### REMOVE (rejection machinery — no place in success-caching)
- `_raise_idempotency_rejection_replay` (media_buy_create.py:1488-1512) — error replay.
- `_cache_rejection_envelope` (1515-1566), `_cache_rejection_and_raise` (1569-1584).
- All `_cache_rejection_and_raise(...)` call sites → become plain `raise <typed error>` (error propagates, not cached; retry re-executes). *(Agent B: ~media_buy_create.py:2343, 3285, 3379, + any sibling)*
- The rejection-replay probe (1764-1780): the `find_by_key` rejection branch + `_raise_idempotency_rejection_replay` call.
- `AdCPError.replayed` attribute (exceptions.py:184-187, 214) + the `if exc.replayed` envelope branch (~643-644) — no error is ever replayed under β. *(dead once the above go)*
- `AdCPIdempotencyExpiredError` (EXPIRED out of scope) + `IDEMPOTENCY_EXPIRED` references in our code.
- `_build_idempotency_hit_result` (1587-1643) — the MediaBuy re-derivation + live-advisory-rebuild; replaced by verbatim replay.

### INVERT (flip rejection→success semantics)
- The repo: `IdempotencyAttemptRepository.record_rejection` → `record_success` (idempotency_attempt.py:75-110); `find_by_key` stays (generic); flip ALL docstrings ("rejection"→"success response"). The model docstring (models.py:979-998) too.
- `_impl` probe (media_buy_create.py:1734-1781): make `idempotency_attempts` the authoritative SUCCESS replay + conflict source; remove the MediaBuy-based replay branch (keep `MediaBuy.idempotency_key` unique constraint as TOCTOU/dup-booking backstop only).
- Store success: at BOTH create paths (pending ~media_buy.py create call from :2538; auto-approved from :3438) record the success envelope in the SAME UoW/txn.
- Schemas: restore `idempotency_key` as REQUIRED on `CreateMediaBuyRequest` (undo the 2 relaxations, _base.py ~1443/1631 region) + delete the false "generated at the transport boundary" comments.
- Port (don't delete) `tests/integration/test_idempotency_replay.py` from rejection-replay (`assert_replayed_rejection`, `seed_rejection`, `TestTransientRejectionNotCached`) to success-replay.

### KEEP
- `src/core/idempotency_canonical.py` (non-lock-in) + `rfc8785` dep + its tests.
- `AdCPIdempotencyConflictError` (terminal, documented divergence comment added).
- `MediaBuy.idempotency_key` column + partial unique index (models.py:926, 968-973) + `find_by_idempotency_key` (#1217, on main) — TOCTOU/dup-booking backstop.
- `idempotency_attempts` table + `IdempotencyAttemptRepository` — REPURPOSED, not removed (overrides Agent B's "remove" lean: needed for byte-for-byte + conflict).
- Capability `idempotency=Idempotency(supported=True, replay_ttl_seconds=86400)` (capabilities.py — both sites) + `DEFAULT_REPLAY_TTL`.

### ADD
- `replayed: bool = False` on `CreateMediaBuyResult` (_base.py:283-300); inject in `_serialize` beside `status`, omit-when-false:
  `if self.replayed: result["replayed"] = True`. Single choke point → uniform across MCP/A2A/REST. *(verify V3)*
- `record_success(...)` storing `success.serialize()` (the verbatim envelope, WITHOUT replayed) + `payload_hash`.
- Success-side conflict: hash compare on the `idempotency_attempts` hit (already present at 1770-1775 for the rejection store — move to the success store).
- Equivalence-pin test (non-lock-in): assert our `canonical_payload_hash` == `adcp.server.idempotency.canonical_json_sha256` over a payload corpus + `EXCLUDED_FIELDS` parity. Mirror `tests/unit/test_adcp_spec_version.py`.
- *(V1, recommended)* `account_id` to the scope: column on `idempotency_attempts` + rework unique index → migration.

## Migration story
- Table + `payload_hash` already exist (097b909c7b5f creates table; 1d9b1402eacb adds payload_hash) — committed, never edited. β reuses them as-is (semantic flip only).
- New migration(s) only if: (a) V1 adds `account_id` to scope + unique index; (b) optional: `payload_hash` NOT NULL (skip for now, stays nullable).
- Re-verify single migration head post-rebase (zero-tolerance guard).

## Test plan (wire-level, not mock-only)
| Scenario | MCP | A2A (`on_message_send`, real auth) | REST `TestClient` | IMPL |
|---|---|---|---|---|
| Success replay | top-level `replayed:true` + same media_buy_id + identical body | DataPart `replayed:true` + same id | JSON `replayed:true` + same id | returns replayed result |
| Conflict | `assert_envelope_shape(wire_error_envelope,"IDEMPOTENCY_CONFLICT",recovery="terminal")` | same on DataPart | same on body | raises typed error |
| Missing key | `assert_envelope_shape(...,"VALIDATION_ERROR")` *(V2)* | same | same | n/a (schema layer) |
- NEW success-replay assertion helper (current `assert_envelope_shape` is error-only, envelope_assertions.py:60-62): reads `result.payload` + top-level `replayed` + body equality vs the captured original.
- BDD: success-replay / conflict / missing-key scenarios; pass-or-xfail; register step module in `pytest_plugins`; compare replayed `media_buy_id` to the captured original (not a re-derived expression). 7 BDD structural guards apply.

## Guard / quality hygiene
- `no_error_code_kwarg_in_impl`, `no_error_construction_in_impl` (cap stays 1 = principal-not-found, untouched), `no_value_error_in_impl`, repository-pattern (DB via repo/UoW/factories), `no_model_dump_in_impl` (keep `model_dump` out of `_impl`; hasher wraps it at idempotency_canonical.py:79), schema-inheritance, query-type-safety, migration-completeness + single-head.
- Regenerate `.duplication-baseline` DOWNWARD after removing rejection code (it shrinks; never grow).
- Remove any allowlist entries the deleted rejection code held *(Agent C: essentially none — verify with the stale-entry test)*.

## Open verify-points (confirm during build; leans noted)
- **V1 (scope fidelity — main open correctness call):** spec scope is `(agent, account, key)`; our table omits `account_id` (`ResolvedIdentity` has it as a SEPARATE field from `principal_id`). Two accounts under one principal reusing a key for different payloads would FALSELY conflict. **Lean: add `account_id` to scope (small migration).** Confirm whether principal already encapsulates account in our model.
- **V2:** restoring required `idempotency_key` → does schema validation emit `VALIDATION_ERROR` uniformly across MCP/A2A/REST? If not, add a typed boundary check. (Breaking change: callers/tests/harness without a key must be updated — enumerate blast radius.)
- **V3:** confirm `result["status"]` level == protocol-envelope top-level per transport so `replayed` lands top-level for each (covered by the wire matrix).
- **V4:** freezing the property_list advisory (byte-for-byte) is the spec-correct behavior; confirm no test depends on the old live-rebuild.

## Sequencing (commit order — each `make quality`-green)
1. Schema: restore required `idempotency_key` + delete false comments; fix the blast radius (callers/tests pass a key). *(V2)*
2. `replayed` field on `CreateMediaBuyResult` + `_serialize`; new success-replay test assertion.
3. Repo + model: `record_rejection`→`record_success`, docstrings flip; *(V1)* add `account_id` scope + migration.
4. `_impl`: rip out rejection machinery + `_build_idempotency_hit_result`; wire verbatim success-cache replay + conflict + atomic store at both create paths; TOCTOU via unique-index re-read.
5. exceptions.py: remove `AdCPError.replayed` + `if exc.replayed` branch + `AdCPIdempotencyExpiredError`; add the D3 divergence comment.
6. Port `test_idempotency_replay.py` to success; full wire matrix; BDD scenarios.
7. Equivalence-pin test.
8. Guard hygiene: regenerate `.duplication-baseline`, single migration head, allowlist sweep.
9. Full suite (`./run_all_tests.sh ci`) before any push-readiness claim.

---

## Review corrections (post R1/R2/R3 — 2026-06-09)

**Verdict:** no spec premise refuted; β CONFIRMED REQUIRED for byte-for-byte. R3's leaner α (re-derive envelope from the buy row) is REJECTED on correctness — re-derivation reflects *current* status/valid_actions, which drifts from the original = the same byte-for-byte violation as the live-rebuild bug. Review docs: `review/R1-spec-storyboard.md`, `review/R2-bdd-tests.md`, `review/R3-arch-guards-optimal.md`.

### Confirmed
- **D3 keep `terminal`** — storyboard conflict step grades CODE only (`idempotency.yaml:460-471`, `["IDEMPOTENCY_CONFLICT","CONFLICT"]`, no recovery check); terminal passes storyboard AND matches SDK. SYNTHESIS.md's "lean correctable" was the stale error.
- **V1 add account_id** — `create_media_buy` takes per-request `account: AccountReference` → `identity.account_id`; one principal targets many accounts → false conflicts without it.
- **S2 missing-key `VALIDATION_ERROR`** — storyboard-accepted (`idempotency.yaml:202` = `["INVALID_REQUEST","VALIDATION_ERROR"]`).

### Corrections that change the plan
1. **ATOMICITY — DROP the overclaim.** `_impl` uses ~22 separate `MediaBuyUoW` commits; the success envelope is built after all of them; the adapter booking precedes DB writes (orphan-warning already at `:2560`). "Same-txn cache" needs restructuring AND can't close the booking↔buy gap. → Adopt the SDK model: **best-effort cache write after the buy commits; on failure log + retry re-executes** (caught by `MediaBuy.idempotency_key` unique index). Remove "closes the crash window" framing.
2. **`model_dump` guard (A2).** `success.serialize()` doesn't exist; serializing the envelope = `model_dump`, BANNED in `_impl` (`test_architecture_no_model_dump_in_impl.py`, zero allowlist for this file). → Serialize INSIDE the repo's `record_success` (model in, repo serializes).
3. **account_id NULL handling.** account_id nullable; default PG unique index treats NULLs as distinct → re-opens dup-booking for no-account buys. → unique index needs `NULLS NOT DISTINCT` (verify PG version) or a sentinel.
4. **byte-for-byte degraded path.** TOCTOU/cache-miss-but-buy-exists returns a re-derived **frozen-advisory** envelope (rare degraded case; document). Keep a frozen-advisory reconstruction helper for this path only (read inside the recovery UoW — DetachedInstanceError hazard).
5. **DRY (A2).** Success-store + degraded reconstruction repeated across paths = shared helpers (CLAUDE.md DRY invariant); name them.
6. **Required-key SCOPE (B5/B6).** Restrict requiredness to **create_media_buy ONLY** (delete its schema-inheritance allowlist override at `test_architecture_schema_inheritance.py:229`; leave Update/Sync = fast-follow) — avoids the **80 UC-003 update_media_buy BDD nodes** encoding the OLD contract (absent→proceeds, 8-char). Blast radius (create only): ~37 test files omit a key + harness fixture (`tests/harness/media_buy_create.py:269`) + `media_buy_helpers.py` + `MinLen(16)`+pattern on every key literal. **RUN `tox -e bdd -- -k idempotency` to capture pass/xfail BEFORE the change.**
7. **Retire the live-rebuild test** — `TestIdempotencyReplayRebuildsAdvisory` (`test_property_list_unsupported_advisory.py:274-347`) asserts the OLD live-rebuild; rewrite to assert FROZEN.

### Tests/storyboard steps to ADD (were missing)
- **`no_duplicate_webhooks_on_replay`** — storyboard's central replay invariant; assert handler/adapter NOT re-invoked. FIX the vacuous `then_no_duplicate_booking` (`uc002:751` reads `adapter_create_call_count` that nothing sets → always passes); harness must actually count adapter calls.
- **`verify_media_buy_count`** — exactly 2 buy rows after the replay sequence (not 3).
- **`fresh_key_new_resource`** — different key + identical payload → NEW media_buy_id.
- Atomicity/degraded test; advisory-FROZEN test; INVERT `test_non_transient_adapter_rejection_is_cached` (nothing is cached now); PORT `test_idempotency_race.py` (it calls the deleted `_build_idempotency_hit_result`).

### Gold-standard gaps to close
- **Cleanup/eviction** for `idempotency_attempts` (growth worse under β — every success stored; read-path TTL keeps correctness). Explicit deferral-with-tracking OR lightweight opportunistic eviction.
- **`replayed`-only-on-genuine-success** invariant (`CreateMediaBuyResult.response` is `Success|Error`; never set on the principal-not-found Error return at `:1711`).
- **`TransportResult` has no `replayed` field** (`transport.py:85-90`) + `extra="forbid"` → harness must pop `replayed` from the body AND surface it (e.g. `TransportResult.replayed`) or the success-replay parse crashes.
- Verify `create-media-buy-response.json` admits a top-level `replayed` (schema roundtrip) before relying on it.
- Replay-hit / conflict-rate metrics (observability).
- Guards also touched: `wrapper_typed_params`, `wrapper_field_descriptions` (docstring rewrite); `migration_completeness` `drop_index(..., table_name=)` records the index name (false-coverage quirk — use explicit table arg).
- Keep `record_success` WIRED to a production caller (after rejection callers are deleted, the repo's only callers go away — slipping the wiring leaves the whole table+repo dead).

### Sequencing fix (A5 — NOT atomic-per-commit as written)
Real atomic units: **{required-key + ALL its test/harness/BDD/caller fixes in ONE commit}**; **{rename `record_rejection`→`record_success` + its caller in ONE commit}**; **{delete rejection code + port integration tests TOGETHER}**. Re-derive the commit plan on these boundaries.

---

## POST-MERGE RE-GROUNDING — β on main's α (2026-06-09, SUPERSEDES the change-set above)

**Why this section exists:** the branch was 34 commits behind `origin/main`; synced via merge `911c46bad`. The body above was grounded against the STALE branch (the branch's rejection-caching machinery). That machinery is GONE — we took main's `media_buy_create.py`. The real rebuild target is now **main's own idempotency (the α approach)**, which the plan had treated as a rejected hypothetical. User reconfirmed **β** (verbatim cache in `idempotency_attempts`) as the storage, layered on main's α.

### Post-merge state (verified line numbers in the merged tree)
- **main's α (to replace):** `_create_media_buy_impl` probe `if req.idempotency_key:` (media_buy_create.py **1596-1623**) → `find_by_idempotency_key` → `_build_idempotency_hit_result` (**1476-1532**, RE-DERIVES: live status + live property_list advisory rebuild — the byte-for-byte gap, main's author chose it intentionally w/ a FIXME). Two TOCTOU `IntegrityError` recoveries also call it: **2393** (pending_approval), **3245** (auto-approved).
- **main's keep-as-backstop:** `MediaBuy.idempotency_key` (models.py **926**) + partial unique index (**968**); `MediaBuyRepository.find_by_idempotency_key`.
- **branch's β substrate (dormant, re-wire):** `IdempotencyAttempt` model (models.py **978+**: tenant/principal/tool/key + `response_envelope` + `payload_hash` + `expires_at`, unique index); `IdempotencyAttemptRepository` (idempotency_attempt.py — `find_by_key` **47** filters expires_at; `record_rejection` **75** ALREADY stores `payload_hash` and its docstring ALREADY describes true-replay-vs-IDEMPOTENCY_CONFLICT; `expire_old` **112**, no prod caller); `idempotency_canonical.py` hasher; migrations `097b909c7b5f` (table) + `1d9b1402eacb` (payload_hash).
- **main's #1307 architecture is canonical (USE IT):** Pattern A fully drained — `PATTERN_A_PER_FILE_CAP={}` + inline `# structural-guard:` markers (NOT the dict-cap). `assert_envelope_shape(...)` now REQUIRES `recovery=` kwarg (tests/CLAUDE.md). Two-layer envelope + typed `AdCPError` raises at boundaries; `resolve_principal_or_raise`. `AdCPIdempotencyConflictError`/`AdCPIdempotencyExpiredError` survived (exceptions.py union); `IDEMPOTENCY_*` codes in the canonical vocab.

### β-on-α change-set (the real one)
1. **`replayed` plumbing (Commit 1):** `CreateMediaBuyResult.replayed` (_base.py) — DONE in tree (survived stash). Reapply the 2 serializer tests onto main's rewritten `tests/unit/test_media_buy.py`. only-on-success invariant added in Commit 3.
2. **Required key (Commit 2, create-only, BREAKING):** `idempotency_key` required on `CreateMediaBuyRequest` + MinLen(16)+pattern; missing → VALIDATION_ERROR. Blast radius re-enumerated on the MERGED tree (main rewrote test files). Drop schema-inheritance allowlist override IF present post-merge (re-check).
3. **Repo flip (Commit 3a):** `record_rejection`→`record_success` (accept the MODEL, serialize INSIDE the repo — model_dump guard; the dict-taking signature would force `_impl` to call model_dump). Flip docstrings (file header + class + method) rejection→success. Add `account_id` to scope: column + unique index `NULLS NOT DISTINCT` + migration (V1). Wire `expire_old` to a prod caller OR documented deferral (Commit 5).
4. **Replace α with β verbatim replay (Commit 3b):** rewrite the probe (1596-1623) — key now always present → look up `idempotency_attempts(find_by_key, scope, "create_media_buy", key)`: hit+hash-match → replay `response_envelope` VERBATIM + `replayed=True` (handler NOT run); hit+hash-mismatch → `AdCPIdempotencyConflictError` (terminal, D3); miss → create, then `record_success(model, payload_hash)`. Compute hash via `idempotency_canonical`. **Keep `_build_idempotency_hit_result` ONLY as the TOCTOU degraded fallback (2393/3245)** when the winner's β row isn't visible yet — but FIX it to FROZEN advisory (drop the live rebuild) OR prefer a β-cache re-read; document the rare degraded path. DRY: shared store + (degraded) reconstruct helpers.
5. **exceptions.py (Commit 3c):** remove `AdCPIdempotencyExpiredError` (EXPIRED out of scope) + its vocab entry + its test; keep `AdCPIdempotencyConflictError` (terminal + D3 divergence comment). `AdCPError.replayed` attr does NOT exist on main (branch-only, gone with main's exceptions.py union — VERIFY none remains).
6. **Tests (Commit 4):** PORT `test_idempotency_replay.py` (imports gone `_raise_idempotency_rejection_replay`/`_cache_rejection_envelope` → rewrite to success-replay); adapt `test_idempotency_attempt_repository.py` (record_rejection→record_success) + `test_idempotency_race.py` (now hits β + frozen fallback); wire matrix w/ `recovery=` kwarg; BDD create scenarios. `assert_replayed_rejection` harness helper (rejection-era) → rewrite/replace with success-replay assertion.
7. **Equivalence-pin + gaps (Commit 5):** hasher == SDK `canonical_json_sha256`; eviction wiring; metrics.
8. **Guards/suite (Commit 6):** single migration head (re-verify post-merge — branch + main both added migrations → likely MULTIPLE heads now, must reconcile); `.duplication-baseline`; `./run_all_tests.sh ci` (xdist fix first).

### Immediate next (this is where implementation resumes)
Reapply Commit 1's 2 serializer tests onto the merged `test_media_buy.py`; verify lint+mypy+unit. Then Commit 2 (required key) — re-enumerate blast radius on the MERGED tree first. CHECK FOR MULTIPLE MIGRATION HEADS early (branch + main both added migrations).
