## ⚠️ STATUS 2026-06-09: SUPERSEDED — DIRECTION CHANGED TO SPEC-CONFORMANT REBUILD

**Confirmed finding:** PR #1312's core model (cache + replay *rejection* envelopes; missing key = no-op) is the **inverse of AdCP spec 3.0.1** (our pin). Spec mandates: cache **successes only**; on any error the key is **NOT stored** and a retry **re-executes**; missing key → **INVALID_REQUEST**.

**Timeline (airtight — the spec was NOT changed under us):**
- Idempotency contract present in spec **3.0.0** (snapshot 2026-04-26) and **3.0.1** (2026-04-28) — verbatim "Only successful responses are cached" / "A retry re-executes" / missing key MUST → INVALID_REQUEST.
- Salesagent pinned `adcp>=4.3.0` (= spec 3.0.1) by 2026-05-14.
- #1303 filed 2026-05-14; #1312 started 2026-05-17 → spec rule predated the work by ~3 weeks; project was already on 3.0.1.
- Divergence origin: internal contract **#1303 item 7** ("replay after rejection"), written contrary to the already-adopted spec. Implementation faithfully built the wrong contract.
- Corroboration: spec CHANGELOG logs a live conformance failure 2026-06-01 (`create_media_buy_replay: IDEMPOTENCY_CONFLICT`) — the success-caching model is actively enforced by the runner.

**Spec-conformant target model (AdCP 3.0.1 §Idempotency, `dist/docs/3.0.1/building/implementation/security.mdx`) — 8 rules:**
1. `idempotency_key` REQUIRED on every mutating request; missing → `INVALID_REQUEST` (validate BEFORE cache lookup).
2. Cache **only successful** responses, keyed by canonical payload hash.
3. On ANY error (validation/governance/transport/internal): do NOT store; retry re-executes.
4. Exact replay (same key + same hash) → cached success with top-level `replayed: true` (byte-for-byte original).
5. Same key + different hash within window → `IDEMPOTENCY_CONFLICT`.
6. Key seen but evicted past `replay_ttl_seconds` → `IDEMPOTENCY_EXPIRED` (±60s clock-skew at boundary).
7. Applies to ALL mutating tools (create_media_buy, update_media_buy, sync_creatives, …) — NOT create-only.
8. Per-(agent,account) cache-insert rate limiting → `RATE_LIMITED`.
(Recovery class: storyboard says CONFLICT=correctable; installed SDK table says terminal — reconcile per project SDK-vs-spec policy.)

**Current state to UNRAVEL:** `IdempotencyAttempt` table caches REJECTIONS w/ TTL + payload_hash; `_cache_rejection_and_raise`; rejection-replay sets `replayed`; success path = permanent `MediaBuy.idempotency_key` (no hash/replayed/expiry); local `CreateMediaBuyRequest` RELAXES required key → optional; capability advertises `supported=true` (`capabilities.py:92,265`); update/sync accept the key but implement nothing.

**Survives from the prior plan below:** Phase 0 merge of main (still required; empirically 6 conflicts, 1 real landmine [audit rename @ `media_buy_create.py:2320`, inside a conflict hunk], 1 guard fails [`no_error_code_kwarg` — add 2 keyed entries: `_raise_idempotency_rejection_replay`, `_create_media_buy_impl`]; no new migration head). The **F8 success-caching direction is now the CORE**, not an add-on (prior F8 design had a TOCTOU hole across 3 sites `:1748`/`:2563`/`:3462` → fix in `_build_idempotency_hit_result`; `replayed` belongs on `CreateMediaBuySuccess`, NOT the dead `ProtocolEnvelope.wrap`). ARCH-1 (delete the local hash fork for `adcp.server.idempotency`) still valid. Guard-audit constraints still apply.

**POST-COMPACTION AGENDA (max-thinking):**
1. **Ground in the spec, in-repo** — establish a repeatable mechanism so we plan from spec text, not assumptions. See `[[reference_adcp_spec_grounding]]`. Investigate what the installed `adcp` pkg bundles (codes/types only — NOT the behavioral contract); decide whether to vendor a pinned spec snapshot in-repo and/or add a docs pointer + CLAUDE.md gate.
2. **Map the unravel** — full inventory of every rejection-cache touchpoint (model, migrations, repo, `_impl` call sites, tests, BDD, capability claim, schema-field relaxation) to remove/repurpose.
3. **Design the conformant rebuild** — success-caching across mutating tools; missing-key reject; conflict/expired/replayed; restore required key; rate-limit + clock-skew. Decide PR decomposition (now far bigger than contract item 7).
4. **Escalate the lesson** — memory written (`[[feedback_ground_protocol_work_in_spec_not_assumptions]]`); propose CLAUDE.md gate: protocol-behavior PRs cite spec section+version before impl.

---

# PR #1312 — Idempotency Completion: Gold-Standard Execution Plan
> NOTE: the plan below (complete-the-expansion) is SUPERSEDED by the status block above. Retained for the parts that survive (Phase 0 merge mechanics, guard/harness constraints, F8 design + its TOCTOU fix).

**Branch:** `feature/b6-idempotency-replay-table` · **Base:** `main` · **PR:** #1312
**Status at planning:** DIRTY/CONFLICTING with main; HEAD `9a689f8c2` (= the commit Konstantine's 2026-06-08 round-3 review was written against).
**Direction (decided):** Complete the June-7 idempotency expansion to **full AdCP conformance**, not contract-item-7 minimum.

This plan is the single source of truth for addressing Konstantine's round-3 review + the conformance gap his review did not cover. Every item is verified against code at HEAD; line refs are live unless noted.

---

## 0. Decisions on record

| Decision | Choice | Rationale |
|---|---|---|
| Scope of June-7 surface | **Complete it** (conflict + expiry + replayed, fully wired + tested) | Spec-defined behavior; partial shipping is what drew 3 review rounds |
| F8 (success-side conflict) | **Include — full conformance** via `media_buys.payload_hash` column + compare on success-replay | Conformance probe replays a *successful* call with a mutated payload and MUST get `IDEMPOTENCY_CONFLICT`; today the success path skips the hash check |
| ARCH-4 (cache → builder coupling) | **Keep `build_two_layer_error_envelope`; reply with SSOT reasoning** | #1307 (merged) reinforced it as the single wire-shape SSOT and deprecated `to_adcp_error`; hand-rolling a projection would duplicate the wire shape |
| BDD UC-002 idempotency scenario | **Author now** | UC-017 already has the replay-scenario pattern to copy; completes contract-7's behavioral principal |
| TEST-2 (assertions.py:128 fallback) | **Concede + harden (1 line) AND reply with evidence it was unreachable** | Finding is FALSE-as-stated (fallback is dead on wire transports), but removing the dead fallback aligns with P28 at near-zero cost |

## Spec basis (AdCP 3.0.1 / adcp 4.3.0)

Source: docs.adcontextprotocol.org idempotency contract. When `adcp.idempotency.supported: true` the seller **MUST**:
1. return `replayed: true` on exact replay (same key + same canonical payload);
2. return `IDEMPOTENCY_CONFLICT` when the same key carries a **different** payload — **regardless of whether the original succeeded or was rejected**;
3. return `IDEMPOTENCY_EXPIRED` once the key is replayed past `replay_ttl_seconds`.

Verified: `IDEMPOTENCY_CONFLICT` (409/terminal) and `IDEMPOTENCY_EXPIRED` (410/terminal) are both standard codes in adcp 4.3.0 `STANDARD_ERROR_CODES`; the typed classes already emit them correctly. The status table auto-derives via `__subclasses__()` (no P32 edit needed).

---

## Phase 0 — Rebase / merge main  **(HIGHEST RISK — do first, isolated commit, no feature work mixed in)**

#1307 ("error-drain Pattern A + boundary ValueErrors, PR 2 of 3") merged to main 2026-06-08 and did **the same Pattern A refactor we did, differently.** Merging produces 6 conflicting files plus landmines that auto-merge silently.

### 6 conflicting files + resolution
| File | Resolution |
|---|---|
| `src/core/exceptions.py` | Additive — keep both subclass blocks (ours: idempotency classes + `replayed`; theirs: taxonomy classes). No semantic overlap. |
| `src/core/tools/media_buy_create.py` | Keep **our** `_cache_rejection_and_raise` (B6's whole point). Adopt main's `audit_workflow_step_failure_if_present` rename. Fold main's `details={"config_errors": ...}` enrichment into the GAM-config hunk. **Convert the principal-not-found `return Error(code="AUTH_REQUIRED")` at ~`:1713` to `resolve_principal_or_raise`** (main's contract). Preserve our `account`/`idempotency_key` params on `create_media_buy_raw`. |
| `tests/unit/test_architecture_no_error_construction_in_impl.py` | Adopt main's drained-cap (`{}`) + `# structural-guard:` marker mechanism; drop our `media_buy_create.py:1` cap entry. |
| `tests/unit/test_error_format_consistency.py` | Additive — keep both set entries. |
| `tests/unit/test_pydantic_schema_alignment.py` | One-line dedup (both add `push_notification_config` to the same `get-products` key). |
| `tests/unit/test_create_media_buy_behavioral.py` | **Take main's delete** — main moved this unit→integration; our edits are subsumed by main's integration rewrite (which already asserts `AdCPAdapterError`/`SERVICE_UNAVAILABLE` and `AdCPAuthenticationError`/"Principal ID not found"). |

### 3 runtime landmines (auto-merge hides these — a hunk-only resolve ships them broken)
1. **Rename:** `audit_step_failure_if_present` → `audit_workflow_step_failure_if_present` (gone from main's `src/`). Our 3 call sites in `media_buy_create.py` (~2341, ~4013, ~4019; 2 outside conflict hunks) → `AttributeError` if missed. Also a stale doc-comment in `test_update_media_buy_behavioral.py:1820`. Fix: `git grep audit_step_failure_if_present` after merge → 0 hits required.
2. **a2a kwargs:** our `account=`/`idempotency_key=` on the `create_media_buy_raw(...)` call in `adcp_a2a_server.py` only work if the `media_buy_create.py` conflict is resolved keeping **our** signature. If resolved "take main," it `TypeError`s.
3. **Stale return:** `return Error(code="AUTH_REQUIRED")` at `~:1713` — same as the file-resolution note above; convert to `resolve_principal_or_raise`.

### 2 inherited guard failures (#1307 added 9 new guards; these two fail on the merged tree until fixed)
- `test_architecture_no_error_code_kwarg_in_impl` — `KNOWN_VIOLATIONS` capped at exactly 2. We have 2 legitimate `AdCPError.synthesize(error_code=...)` sites in `media_buy_create.py` (replay reconstruction `~:1503`, adapter-rejection reconstruction `~:3370`). **Add both to `KNOWN_VIOLATIONS` and bump the cap assertion to 4.**
- `test_architecture_no_error_construction_in_impl` — main drained the cap; our `Error(code="AUTH_REQUIRED")` at `~:1713` fails → fixed by the `resolve_principal_or_raise` conversion (don't marker it; eliminate it).

### Phase 0 acceptance
- `git grep audit_step_failure_if_present src/` → 0
- `uv run alembic heads` → single head
- `make quality` green on merged tree (the 9 new guards run inside Unit Tests)
- `tox -e integration` on a **fresh DB** (persistent agent-db masks fresh-schema failures)
- mypy `adcp` pin unchanged (already 4.3.0 both sides — no action)

---

## Phase 1 — ARCH-1: delete the SDK fork (foundation; everything hashing-related sits on it)

- **Targets:** `src/core/idempotency_canonical.py`, `pyproject.toml:11`.
- **Do:** replace local `_EXCLUDED_FIELDS` / `_NESTED_EXCLUSIONS` / `_drop_nested` / `strip_excluded_fields` / `canonical_payload_hash` with imports from `adcp.server.idempotency` (`EXCLUDED_FIELDS`, `strip_excluded_fields`, `canonical_json_sha256`). **Keep only** the `canonical_request_hash(request)` 2-line wrapper (does `model_dump(mode="json")` → satisfies the no-model-dump-in-impl guard; SDK has no equivalent). Do **not** adopt the SDK's replay store (`IdempotencyStore`/`PgBackend`) — we keep our raise-based model. Drop the `rfc8785` pin (only importer is this file).
- **Verified:** SDK functions are byte-equivalent (incl. the nested credential exclusion); identical hash on the test vector. Safe.
- **Guard:** none structural (`idempotency_canonical.py` is `src/core/`, outside the no-model-dump scan set). `rfc8785` removal → `uv-secure` audit runs only in full suite.
- **Acceptance:** `git grep canonical_payload_hash` → 0 callers after migration; new byte-equivalence test pins `local == adcp.server.idempotency.canonical_json_sha256(payload)` so the delegation can't silently drift; `tox -e unit` (import errors are invisible to ruff — F821 ignored).

---

## Phase 2 — Behavioral completions

### 2a. IDEMPOTENCY_CONFLICT — rejection path (TEST-1a / ARCH-2b)
- Raise site already exists (`media_buy_create.py:~1772`). No `error_code=` kwarg (compliant with P34). Item is the **test** (see 3a).

### 2b. IDEMPOTENCY_EXPIRED — wire the dead class (ARCH-2a / TEST-3a)
- **Targets:** `src/core/database/repositories/idempotency_attempt.py:find_by_key` (~:47-73), `media_buy_create.py` rejection-replay branch, `src/core/exceptions.py:575`.
- **Design:** spec wants `IDEMPOTENCY_EXPIRED` returned (not silent re-eval). `find_by_key` currently filters `expires_at > now` → collapses "absent" and "expired." Add a path that distinguishes an **expired-but-present** row (e.g. a second narrow query, or return a sentinel/typed result) and raise `AdCPIdempotencyExpiredError` from `_impl`. Repository returns the signal; `_impl` raises (keeps the impl-layer guards happy).
- **Reparent (track/batch):** make `AdCPIdempotencyExpiredError` extend `AdCPGoneError` (the existing 410 base) instead of hand-rolling `_default_status_code=410`. Override `error_code`→`IDEMPOTENCY_EXPIRED` and `recovery`→`terminal` (parent is `INVALID_STATE`/`correctable`). Add a P10 deliberate-deviation comment.
- **Guard:** `media_buy_create.py` is in `COMPLEX_MODULE_FILES` (no `ToolError`); raise the typed `AdCPError` ✓. Don't add a `raise ValueError` (cap=2). No silent except.
- **Acceptance:** `git grep "AdCPIdempotencyExpiredError(" src/` → ≥1 raise site; wire test per transport (3b).

### 2c. `replayed: true` on success-replay (ARCH-3)
- **Targets:** `src/core/protocol_envelope.py:58` (local `ProtocolEnvelope`), `src/core/schemas/_base.py:196` (`CreateMediaBuySuccess`), `_build_idempotency_hit_result` (`media_buy_create.py:~1587-1644`).
- **Design:** add `replayed: bool = False` to the local `ProtocolEnvelope` (library `ProtocolEnvelope` already has it; local is hand-rolled `BaseModel`) and to `CreateMediaBuySuccess` (library success type has no `replayed` → genuinely new field, no `KNOWN_OVERRIDES` needed). Set `replayed=True` in `_build_idempotency_hit_result` on the replay branch.
- **Serialization:** `CreateMediaBuySuccess` has a custom `@model_serializer` — a scalar `bool` passes through automatically (no Pattern-#4 override needed). Local `ProtocolEnvelope.model_dump` forces `exclude_none=True`; `replayed=False` is not None so it emits. **Decide false-vs-omitted** — spec says "false or omitted"; recommend default `False`, emitted.
- **Guard:** schema-inheritance guard does not scan `protocol_envelope.py` or `AdCP*`-prefixed `CreateMediaBuySuccess` (pre-existing blind spots) → no guard trip. Run `pytest tests/unit/test_adcp_contract.py` after the schema edit (mandatory).

### 2d. F8 — success-side conflict detection (full conformance; **new, beyond the review**)
- **Targets:** new Alembic migration adding `media_buys.payload_hash` (nullable); MediaBuy creation success path (write `canonical_request_hash(req)`); success-replay probe (`media_buy_create.py:~1741-1748`); `MediaBuyRepository.find_by_idempotency_key`.
- **Design:** on first successful create, persist `payload_hash`. On success-replay hit, compare incoming `canonical_request_hash(req)` to the stored hash → mismatch raises `AdCPIdempotencyConflictError`; match returns the existing buy with `replayed=True`. Existing rows have `NULL` hash → no conflict check (graceful, mirrors the rejection path's `payload_hash is not None` guard).
- **Migration:** second migration in this PR (first is `097b909c7b5f` for `idempotency_attempts.payload_hash`). Non-empty `upgrade()`+`downgrade()`; `postgresql.JSONB`/proper column types; verify single head after Phase 0; `Mapped[str | None]` typing on the model column.
- **Guard:** migration-completeness + single-migration-head. Repository pattern (all access via `MediaBuyRepository`/UoW).
- **Acceptance:** conformance probe test — create (success) → replay same key + mutated payload → `IDEMPOTENCY_CONFLICT`; create → replay same key + same payload → success with `replayed: true`.

### 2e. ARCH-4 — keep the builder (decided: push back)
- **No code change** to `_cache_rejection_envelope`'s use of `build_two_layer_error_envelope`. It is the #1307-reinforced SSOT for wire shape; `to_adcp_error` is deprecated; caching a wire envelope for verbatim replay is a legitimate non-boundary use. Add a one-line comment at `:1539` citing the SSOT rationale. Covered in the review reply (Phase 5).

### 2f. `expire_old()` caller (track/batch, open 3 rounds)
- Decide with 2b: either wire a periodic caller (out-of-scope for a background-scheduler in this PR) **or** add an explicit comment that storage growth is unbounded until a cleanup task lands, with a tracked follow-up. Recommend: document + follow-up (scheduler wiring is a separate concern, already disclosed in the PR body).

---

## Phase 3 — Tests (gold-standard: wire envelope, not reconstructed exceptions)

**Assertion standard for all new error/conflict/expiry wire tests:** `assert_envelope_shape(result.wire_error_envelope, "<CODE>", recovery="terminal", message_substr=...)` across **MCP + A2A + REST** (P24/P28/P38). Not `isinstance`, not `error.error_code` (reconstruction is lossy). A2A leg must drive `on_message_send` via `call_via`. Seed rows via `record_rejection(...)` / factories — never raw `session.add()`.

| # | Test | Detail |
|---|---|---|
| 3a | Conflict behavioral (rejection) | `TestImplRaisesConflictOnPayloadHashMismatch`: seed `record_rejection(..., payload_hash="known")`, call `_impl` + one wire transport with a mismatching request, assert `IDEMPOTENCY_CONFLICT`. Update `seed_rejection` harness helper to accept `payload_hash`. Fix the overstated docstring at `test_idempotency_replay.py:6`. |
| 3b | Expiry behavioral | seed an **expired** row, replay → assert `IDEMPOTENCY_EXPIRED` (wire) + that the impl raises, not silent re-eval. |
| 3c | F8 success conflict + replayed | create (success) → replay mutated payload → `IDEMPOTENCY_CONFLICT`; create → exact replay → `replayed: true`. Wire + impl. |
| 3d | RFC 8785 vectors | parametrized known-answer digests incl. a number-canonicalization and a unicode case that distinguish RFC 8785 from `json.dumps(sort_keys=True)` (the mutation the vector must catch). Assert the hex digest, not serializer-internal text (P40). |
| 3e | `canonical_request_hash` | test the production entry point with a real `CreateMediaBuyRequest` (factory): equals `canonical_payload_hash(req.model_dump(mode="json"))`, stable across calls, excluded fields don't change the hash. |
| 3f | BDD UC-002 | author replay + conflict + expiry scenario(s) + steps in `BR-UC-002-create-media-buy.feature`, modeled on UC-017's `@T-UC-017-part-idempotency`. Every Then asserts a value (no trivial/`_pending` steps). |
| 3g | Fix `test_no_key_is_noop` (`:119-130`) | add a positive assertion: open a UoW, assert `find_by_key(...) is None` (no row written). |
| 3h | Fix `test_unrelated_key_does_not_replay` (`:231-287`) | real weakness is the `dry_run=True` no-raise path asserting nothing — assert positively that a fresh result returned / the seeded envelope was not served and no `replayed=True`, not relying on the `except` firing. |
| 3i | Delete `test_idempotency_cached_rejection_replayed` (`test_media_buy.py:~1301-1362`) | mock-only; duplicates real-DB coverage (P23). |

---

## Phase 4 — DRY + cleanup

| # | Item | Detail |
|---|---|---|
| 4a | Lift `_RepoEnv` → shared base (TEST-4) | one class in `tests/harness/_base.py`; migrate **all 6** copies (`test_idempotency_replay.py:30`, `test_idempotency_attempt_repository.py:21`, `test_idempotency_race.py:35`, `test_account_repository.py:17`, `test_tenant_config_repository.py:18`, `test_creative_repository.py:31`). Partial migration risks leaving a counting cluster or pushing `.duplication-baseline` UP. Verify with `uv run pylint --disable=all --enable=R0801 tests/` before commit; stage the auto-updated `.duplication-baseline` in the same commit. |
| 4b | `DEFAULT_REPLAY_TTL` constant (track/batch) | consolidate to one `REPLAY_TTL_SECONDS = 86400` int (for `capabilities.py:92,265`), derive `DEFAULT_REPLAY_TTL = timedelta(seconds=REPLAY_TTL_SECONDS)` for the repo. Do **not** fold the coincidental `86400`s in `mock_ad_server.py`/admin. Watch import direction (capabilities → repository is backwards; pick a neutral home). |
| 4c | `Mapped[dict[str, Any]]` | `models.py` `response_envelope: Mapped[dict]` → `Mapped[dict[str, Any]]`. |
| 4d | Redundant tenant FK | `models.py` IdempotencyAttempt standalone `tenant_id` FK coexists with the composite FK to principals (which already FKs tenants). Drop the redundant one if it doesn't change query behavior — **verify** no tenant-scoped query depends on it before removing. |
| 4e | `LibraryAccountReference` import-in-function | hoist `adcp_a2a_server.py:124` to module level (P6). |
| 4f | TEST-2 harden (1 line) | drop the `or result.synthesized_error_envelope` fallback in `tests/harness/assertions.py:128` (dead on wire transports; aligns with P28). |

---

## Phase 5 — Review replies (technical only; no internal framing)

- **TEST-2:** evidence it was FALSE-as-stated (synthesized envelope is `None` on MCP/A2A/REST; `assert is_error` at `:127` fails loud first) — and note we hardened the dead fallback anyway (4f). Mention the *real*, pre-existing, out-of-scope softness at `dispatchers.py:69` so the steelman is acknowledged.
- **ARCH-4:** explain keep-the-builder: #1307 reinforced `build_two_layer_error_envelope` as the wire-shape SSOT and deprecated `to_adcp_error`; the cache stores a wire envelope for verbatim replay, a legitimate non-boundary use; hand-rolling a projection would duplicate the wire shape.
- **`Union[...]` migration syntax:** push back — it's the repo-wide alembic template; modernizing only two migrations is inconsistent and we never edit committed migrations. Suggest a separate repo-wide chore if wanted.
- **"three-copy seed block":** ask Konstantine to point at the exact block — could not locate a byte-identical 3× block (flagged unverified).

---

## Pattern checklist (P1–P42) — pre-review gate

- **P3** ship-with-caller: `AdCPIdempotencyExpiredError` (2b) and `expire_old` (2f) — wire or document.
- **P24/P28/P38** wire envelope + pin `error_code`: all Phase-3 wire tests.
- **P34** no `error_code=` bypass: existing code clean; keep new raises clean; the merged guard enforces it (Phase 0 allowlist update).
- **P9/P14** single-line-revert pin tests: each conflict/expiry/replayed/F8 branch has a test that goes red on revert.
- **P10/P21** deliberate-deviation comments + docstrings match code: `AdCPIdempotencyConflictError` recovery override, expiry silent-vs-raise decision, ARCH-4 SSOT comment.
- **P4/P13/DRY** delete duplicates in same commit: SDK fork (1), `_RepoEnv` (4a), TTL constant (4b).
- **P2** no vacuous asserts: 3g/3h.
- **P23** delete mock-only dup: 3i.
- **P6** hoist inline imports: 4e.

## Structural-guard checklist

no-model-dump-in-impl (keep wrapper outside `_impl`) · no-toolerror-in-impl (raise `AdCPError`) · no-value-error-in-impl (cap=2, don't grow) · no-error-construction-in-impl (cap drained on main — don't add `Error(code=)`; the AUTH_REQUIRED site → `resolve_principal_or_raise`) · no-error-code-kwarg-in-impl (add 2 synthesize sites to `KNOWN_VIOLATIONS`, cap→4) · repository-pattern (factories/repo, never `session.add`) · schema-inheritance (no trip; verify) · migration-completeness + single-head (the new F8 migration) · code-duplication baseline (stage on decrease).

## Closeout gates (before any "done")

1. `make quality` (incl. `check_code_duplication` — stage `.duplication-baseline`)
2. `pytest tests/unit/test_adcp_contract.py` (after schema edits)
3. `tox -e unit` (catches import errors ruff misses)
4. `tox -e integration` + `tox -e bdd` on a **fresh DB**
5. `./run_all_tests.sh ci` (full suite — `make quality` skips uv-secure + network schema-alignment)
6. **Two-phase audit:** after implementation, run an Opus audit subagent (explicit memory Read) over the diff before any push.

## Open / unverified

- "three-copy seed block" location (ask Konstantine).
- Redundant tenant FK removal (4d) — verify no query depends on it first.
- `expire_old` caller (2f) — confirm document-and-defer vs wire.
