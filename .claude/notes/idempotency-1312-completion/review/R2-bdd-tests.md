# R2 — BDD + Test-Harness Adversarial Review (idempotency #1312 rebuild)

Branch `feature/b6-idempotency-replay-table` @ `9a689f8c2`. Read-only.
Every claim labelled **OBSERVED** (verified at cited `file:line`) or **INFERRED**.
Verdicts: CONFIRMED (plan claim holds) / REFUTED (plan claim wrong) / NEEDS-ADJUSTMENT.

Lead with the high-cost / REFUTED items.

---

## TOP-LINE (read this first)

1. **B5 blast radius is LARGER and BROADER than the plan scopes.** Restoring required
   `idempotency_key` is NOT a `create_media_buy`-only change. The schema field is shared and
   the SDK marks it required on **4** request types. There are **131 `CreateMediaBuyRequest(`
   construction call sites across 43 test files; 37 of those files NEVER pass an
   `idempotency_key`** (OBSERVED). The SDK constraint is **MinLen(16)** + pattern
   `^[A-Za-z0-9_.:-]{16,255}$` (OBSERVED) — so even files that DO pass a key may pass a short
   one. This is a large, mostly-hidden cost the plan under-states.

2. **B5/B6 — the plan's missing-key decision FIGHTS a live BDD contract.** The compiled BDD
   features encode the OLD spec: `idempotency_key` **absent → "proceeds without protection" →
   status completed** (OBSERVED, BR-UC-003 feature lines 359-373, BR-RULE-081 INV-1) and an
   **8-char min** (line 907). The plan (D2) makes absent → **rejected** and min → **16**. That
   FLIPS at least 2 currently-passing BDD scenarios to failing, plus ~12 partition/boundary
   nodes that assert an 8-char key is accepted. These are `update_media_buy` scenarios, but they
   ride the SAME schema field. **REFUTES the plan's implicit assumption that the breaking change
   is contained to create.**

3. **B5 — the plan's `VALIDATION_ERROR` choice for missing-key contradicts the BDD `INVALID_REQUEST`
   contract.** Every idempotency-format BDD scenario asserts `error code should be "INVALID_REQUEST"`
   (OBSERVED, BR-UC-003 lines 904/920/1076-1104). The BDD `then_error_code` step is an **exact
   match** (OBSERVED `then_error.py:94-99`) and Pydantic `ValidationError` maps to
   `VALIDATION_ERROR` (OBSERVED `then_error.py:31-36`). The conftest already documents this exact
   mismatch as a "spec-production gap" for sibling fields (OBSERVED `conftest.py:123-128`,
   `T-UC-002-ext-c` etc.). So the plan's "VALIDATION_ERROR is storyboard-accepted" needs to be
   reconciled with these existing `INVALID_REQUEST`-expecting BDD nodes — they will need new
   xfails or the plan must emit `INVALID_REQUEST`.

4. **B2/S6 — success-replay across the wire is BLOCKED by the harness today, and the plan's S6
   note is incomplete.** All three wire transports reconstruct success via
   `parse_rest_response` → `CreateMediaBuySuccess(**data)` under `extra="forbid"` (OBSERVED
   `media_buy_create.py:328-343`). A top-level `replayed` key survives into that call (the A2A
   strip only removes `message`/`success` — OBSERVED `_base.py:609-614`; MCP/REST pass
   `structured_content`/JSON straight in — OBSERVED `_base.py:693`, `dispatchers.py:164). It will
   raise ValidationError. AND `TransportResult` has **no `replayed` field** (OBSERVED
   `transport.py:85-90`) — so even after popping it, there is nowhere for the success-replay
   assertion to read it. The plan says "harness must pop replayed" but does not specify the
   surfacing mechanism. NEEDS-ADJUSTMENT.

5. **B6 — the plan FORGETS that it deletes `_build_idempotency_hit_result`, which 3 active race
   tests depend on.** `tests/integration/test_idempotency_race.py` (3 tests) imports and calls
   `_build_idempotency_hit_result` (OBSERVED lines 102/125/153/197). The change-set deletes that
   helper (PLAN-REBUILD line 48). Those tests must be ported, not just left — the plan's test
   plan never mentions `test_idempotency_race.py`.

---

## B1 — Wire matrix achievable (success-replay + conflict + missing-key × MCP/A2A/REST)

**Verdict: NEEDS-ADJUSTMENT (achievable for errors; success-replay needs harness work; "fire same
request twice" is supported but not via a single helper).**

- OBSERVED — `MediaBuyCreateEnv.call_via(transport, ...)` routes through real pipelines:
  - MCP: `_run_mcp_client` drives in-memory FastMCP `Client` with the **real token→DB→identity**
    auth chain (`_base.py:616-709`, header patch + patch-called guard at 690-692).
  - A2A: `call_a2a` → `_run_a2a_handler` drives `handler.on_message_send` and reads
    `artifacts[0]` DataPart (`_base.py:481-614`, `media_buy_create.py:290-302`). This is exactly
    the gold-standard the memories require.
  - REST: `RestDispatcher` → FastAPI `TestClient` → real HTTP body (`dispatchers.py:143-165`).
  So conflict + missing-key error matrices are fully achievable at the wire today.

- **Firing the SAME request twice in one test: SUPPORTED but manual.** OBSERVED — the existing
  `TestWirePathReplay._run_wire_replay` (`test_idempotency_replay.py:323-339`) does it by
  **seeding** a cached row (`env.seed_rejection`) then doing ONE `call_via`. For SUCCESS replay
  you instead need: first `call_via` (creates the buy + caches success), then a SECOND `call_via`
  with the same key. There is **no `seed_success` helper yet** (the existing `seed_rejection`
  writes a `CreateMediaBuyError` via `record_rejection` — `media_buy_create.py:150-174`). Both
  approaches work; the harness does not prevent two dispatches in one `with env:` block. INFERRED
  — the cleanest path is two real `call_via` calls (proves the cache WRITE happens on call 1),
  which is strictly better than seeding.

- **REFUTES grounding-C's claim** (C §0 INFERRED) that success caching "may be fully served by
  the existing MediaBuy row." The harness `seed_rejection` and the production replay both key off
  `idempotency_attempts` (`find_by_key`), and the verbatim-replay contract needs the stored
  envelope, so the table is load-bearing (matches PLAN-REBUILD's β decision). Consistent.

- **Caveat (B1):** the success-replay assertion needs `TransportResult` to surface top-level
  `replayed` (see B2/S6). Until that harness change lands, the success-replay row of the matrix
  CANNOT be asserted at the wire — the test would crash in `parse_rest_response` under
  `extra="forbid"`. This is a prerequisite, not optional.

---

## B2 — Success-replay assertion (assert_envelope_shape is error-only; new helper needed)

**Verdict: CONFIRMED (assert_envelope_shape is error-only) + NEEDS-ADJUSTMENT (the new helper has
unstated prerequisites).**

- OBSERVED — `assert_envelope_shape` REQUIRES `adcp_error` + `errors[]`
  (`envelope_assertions.py:60-62`). It is strictly error-only. A success replay has neither →
  the plan's claim is CONFIRMED.

- OBSERVED — `TransportResult.payload` is the reconstructed `CreateMediaBuySuccess`
  (`transport.py:85`, populated by `parse_rest_response`). There is **no `replayed` field** on
  `TransportResult` (only `payload`, `envelope`, `error`, `wire_error_envelope`,
  `synthesized_error_envelope`, `raw_response` — `transport.py:85-90`). OBSERVED.

- **Exact shape the new success-replay assertion needs (and the plan omits):**
  1. `result.is_success` is True (payload is a `CreateMediaBuySuccess`).
  2. `result.payload.media_buy_id == <captured original id>` (compare to the id returned by
     call 1, NOT a re-derived expression — `reference_bdd_harness_pitfalls` #7).
  3. **Top-level `replayed is True`** — and there is NOWHERE to read it. The harness MUST:
     - pop `replayed` in `parse_rest_response` BEFORE `CreateMediaBuySuccess(**data)` (else
       `extra="forbid"` ValidationError — OBSERVED `media_buy_create.py:338-342`, extra mode
       `_base.py:1439`), AND
     - re-attach it so a test can assert it. Options: (a) reconstruct
       `CreateMediaBuyResult(response=..., status=..., replayed=...)` and read
       `result.payload.replayed`? — NO, `replayed` lives on the *wrapper* not the payload
       (PLAN-REBUILD S1); (b) add a `replayed` field to `TransportResult` and populate it in
       each dispatcher; or (c) read it off `result.raw_response.json()` for REST only (won't
       work for A2A/MCP). INFERRED — option (b) is the only uniform one, and it is NOT in the
       plan's change-set.
  4. Body equality vs the captured original: assert the replayed payload's salient fields
     (`media_buy_id`, `packages[].package_id`, `status`) equal call 1's. Verbatim byte-equality
     is hard to assert post-reconstruction (the harness reconstructs a fresh model); INFERRED —
     assert field-equality on the reconstructed payload, and assert the adapter was called
     exactly once (proves no re-execution) — but see B6 on the broken call-count.

---

## B3 — BDD mechanics (pass-or-xfail; pytest_plugins; 7 structural guards)

**Verdict: NEEDS-ADJUSTMENT. The plan/grounding mis-state the BDD starting point and the
"no duplicate booking" Then is currently vacuous.**

- **REFUTES grounding-C §5** ("there is currently NO idempotency BDD feature/steps"). OBSERVED:
  - `uc002_create_media_buy.py` already defines idempotency steps: `given_no_idempotency_key`
    (626), `given_idempotency_key_set` (633), `when_send_same_request_with_key` (647, a SUCCESS
    replay When), `then_no_duplicate_booking` (751). OBSERVED.
  - `BR-UC-003-update-media-buy.feature` has LIVE idempotency scenarios (343-373, 897-923,
    1051-1104) — `@T-UC-003-idempotency-valid`, `-absent`, partition, boundary. OBSERVED.
  - Collection confirms **80 idempotency test nodes** in `test_uc003_update_media_buy.py`
    (OBSERVED, `pytest --collect-only` grep count = 80, incl.
    `test_idempotency_key__absent_proceeds_without_protection[impl|a2a|mcp|rest]`).
  - So the rebuild is NOT greenfield BDD; it must reconcile with existing scenarios.

- **`when_send_same_request_with_key` is an ORPHAN.** OBSERVED — no feature file uses the text
  "sends the same create_media_buy request with idempotency_key" (grep of `tests/bdd/features/`
  returns nothing). It is a dead step definition (likely from a prior idempotency attempt). A new
  success-replay scenario in a UC-002 feature would make it live — but the feature is
  auto-generated (`# DO NOT EDIT -- re-run: python scripts/compile_bdd.py`, OBSERVED BR-UC-003
  header), so you cannot hand-add the scenario; it must come from the adcp-req spec source +
  `compile_bdd.py`. INFERRED — this is a real obstacle: the plan says "author the .feature via
  the compile pipeline," but the spec snapshot the features are compiled from (schema
  v3.0.0-rc.1, adcp-req @ c7db1f45, 2026-05-20 — OBSERVED header) PREDATES the success-caching
  rule and encodes "absent proceeds without protection." Adding a conformant success-replay
  scenario requires the spec source to carry it.

- **`then_no_duplicate_booking` is VACUOUS today.** OBSERVED — it reads
  `ctx.get("adapter_create_call_count", 0)` (uc002:759) and asserts `<= 1`. **Nothing anywhere
  sets `adapter_create_call_count`** (grep across `tests/bdd/` + `tests/harness/` finds only the
  READ at uc002:759). So it is `0 <= 1` → always passes (`reference_bdd_harness_pitfalls` #7
  circularity). If the plan reuses this Then for "no second booking," it proves nothing unless
  the harness is taught to count adapter `create_media_buy` calls. NEEDS-ADJUSTMENT.

- **pytest_plugins:** OBSERVED — `uc002_create_media_buy` IS registered (`conftest.py:44`).
  BUT `uc003_update_media_buy` is **NOT** in `pytest_plugins` (the list ends at line ~50; uc003
  absent). It is only transitively imported by `uc006_sync_creatives.py:516` and
  `uc003_ext_error_scenarios.py:16`. There is **no `test_architecture_bdd_step_module_reachability.py`
  on this branch** (the file the memory cites does not exist — OBSERVED `ls` miss). So whether the
  UC-003 idempotency scenarios resolve their steps (some from registered uc002, some from
  unregistered uc003) and pass-vs-xfail must be confirmed by RUNNING, which I could not do
  (no DB up). UNCERTAIN — flagged below.

- **7 structural guards:** all 7 guard files exist (OBSERVED `ls tests/unit/test_architecture_bdd_*`).
  A new success-replay/conflict/missing-key step set CAN satisfy them, but two are live risks:
  - `no_trivial_assertions` / `assertion_strength`: the success-replay Then must compare
    `media_buy_id` to a captured original AND assert `replayed is True` against a real source —
    the vacuous `adapter_create_call_count<=1` pattern would be flagged or, worse, pass the AST
    guard while being semantically dead (pitfall #7).
  - `no_request_in_then`: the Then must read the captured result, not the request. The plan's
    "compare replayed media_buy_id to the captured original" satisfies this IF the original id
    is captured into ctx by a prior step/When, not re-read from the request.

---

## B4 — Equivalence-pin test (test-only SDK import of canonical_json_sha256)

**Verdict: CONFIRMED. No lock-in violation; SDK fn importable; mirror pattern exists.**

- OBSERVED — `from adcp.server.idempotency import canonical_json_sha256, EXCLUDED_FIELDS,
  strip_excluded_fields` imports cleanly in the test env (`uv run python -c ...` → "OK import",
  `EXCLUDED_FIELDS = frozenset({'governance_context', 'context', 'idempotency_key'})`).
- OBSERVED — `from adcp.server.*` is ALREADY a production import pattern (`exceptions.py:15`,
  `context_manager.py:357`, `media_buy_create.py:26`, `media_buy_update.py:18`, `main.py:285`,
  `media_buy_list.py:57`; test side `test_architecture_error_code_compliance.py:17`). A test-only
  import of the pure hash function violates **no** guard. The non-lock-in concern is adopting the
  SDK *replay store* (`IdempotencyStore`/`PgBackend`), explicitly documented as the reason for
  the fork (`idempotency_canonical.py:11-16`). The pin imports only the hash primitive.
- OBSERVED — `tests/unit/test_adcp_spec_version.py` exists and is the right mirror (a single
  CI guard that fails loudly on drift). `tests/unit/test_idempotency_canonical.py` exists and
  tests the INTERNAL contract only (not cross-SDK equivalence) — so the new pin is additive.
- OBSERVED — our `_EXCLUDED_FIELDS` = `{idempotency_key, context, governance_context}`
  (`idempotency_canonical.py:27`) == SDK `EXCLUDED_FIELDS` (top-level). Our `_NESTED_EXCLUSIONS`
  (`...:31`) adds `push_notification_config.authentication.credentials`, which the SDK strips by
  a different mechanism — H2 parity assertion should be top-level only, with the nested case
  proven by the H1 corpus (matches grounding-C §6).
- Caveat: H1 must be a corpus (key-reorder, nested-credential, unicode, number-encoding), not a
  single payload (P9/P28). The plan says "corpus" — good.

---

## B5 — BREAKING-CHANGE BLAST RADIUS (HIGH PRIORITY)

**Verdict: REFUTED that this is contained / cheap. The blast radius is large and crosses into
update + sync paths and into the live BDD contract.**

### Construction-site count (OBSERVED)
- `CreateMediaBuyRequest(` in **src/**: 5 grep hits across 3 files; 2 are real production
  constructors (`media_buy_create.py:4162` MCP wrapper, `:4244` A2A raw wrapper), both pass
  `idempotency_key=idempotency_key` where the wrapper param defaults to `None`
  (OBSERVED `:4172`, `:4254`). The 3rd is `media_buy_create.py:666`
  (`CreateMediaBuyRequest(**raw_request_data)`). 1 is a docstring (`validation_helpers.py:119`),
  1 is the class def (`_base.py`).
- `CreateMediaBuyRequest(` in **tests/**: **131 call sites across 43 files** (OBSERVED grep).
- Files that construct it and **NEVER** mention `idempotency_key`: **37** (OBSERVED enumerated
  list in the run log) — every direct construction there raises `ValidationError` once the field
  is required, regardless of `extra` mode (a *required* missing field always raises; `extra`
  only governs unknown fields).
- The harness itself: `MediaBuyCreateEnv.call_impl` builds `CreateMediaBuyRequest(**kwargs)`
  with no default key (OBSERVED `media_buy_create.py:269`); `tests/integration/media_buy_helpers.py`
  also constructs it without a key. So the harness fixture and helpers break too → every
  `MediaBuyCreateEnv`-based test that doesn't pass a key breaks. This is the ~80
  `brand=`+create-flow test files (OBSERVED heuristic count).

### The min-length / pattern cost (OBSERVED, plan under-states)
- SDK `CreateMediaBuyRequest.idempotency_key`: **required=True, MinLen(16), MaxLen(255),
  pattern `^[A-Za-z0-9_.:-]{16,255}$`** (OBSERVED via `model_fields`). Same for
  `UpdateMediaBuyRequest` (OBSERVED). So restoring requiredness ALSO restores the 16-char min +
  charset. Test keys like `"abc12345-retry-001"` pass (18 chars), but `"abc1234"` (7), any UUID
  with chars outside the set, or any short literal break. The plan never mentions the format
  constraint — it is part of "restore required."

### Schema-inheritance guard (OBSERVED, plan omits)
- `test_architecture_schema_inheritance.py` KNOWN_OVERRIDES allowlists **FOUR**
  `idempotency_key` optional-override entries: `CreateMediaBuyRequest` (line 229),
  `SyncAccountsRequest` (231), `SyncCreativesRequest` (232), `UpdateMediaBuyRequest` (234)
  (OBSERVED). If the plan removes the `| None = None` override on `CreateMediaBuyRequest` (to
  inherit the required library field), it MUST delete the line-229 allowlist entry or the
  stale-entry half of the guard fails. The plan's guard-hygiene section lists schema-inheritance
  but does NOT call out this specific deletion.

### Production caller behavior (OBSERVED)
- No boundary key-generation exists for create (only `SyncAccountsRequest` self-generates at
  `accounts.py:464,711`). So a buyer omitting the key → `CreateMediaBuyRequest(...)` raises
  `ValidationError` → caught at `:4174`/`:4256` → `AdCPValidationError(format_validation_error(...))`
  → **`VALIDATION_ERROR`** on the wire (OBSERVED). This is the mechanism the plan's D2 relies on,
  and it does produce `VALIDATION_ERROR`, NOT `INVALID_REQUEST`.

### Contract-test check (OBSERVED)
- `tests/unit/test_adcp_contract.py` has **zero** references to `idempotency_key` (OBSERVED grep
  empty) — so there is no contract test asserting the field is optional that needs flipping. The
  requiredness assertion lives ONLY in the schema-inheritance guard allowlist (above) and is
  enforced implicitly by Pydantic at construction.

**Realistic cost:** flipping requiredness is a wide mechanical sweep — 37 test files + the
harness + helpers + the schema-inheritance allowlist entry, plus the format-length conformance
of every key literal. The plan's "fix the blast radius (callers/tests pass a key)" line (Seq
step 1) treats this as a one-liner; it is the single largest mechanical task in the sequence.

---

## B6 — Completeness (what the plan FORGETS)

**Verdict: several forgotten tests + one forgotten contradiction.**

### TESTS THE PLAN FORGOT
1. **`tests/integration/test_idempotency_race.py` (3 tests) depends on
   `_build_idempotency_hit_result`** — the helper the change-set DELETES (PLAN-REBUILD line 48).
   OBSERVED: imports/calls at `test_idempotency_race.py:102,125,153,197`. The plan's test plan
   never names this file. Under β the TOCTOU recovery becomes "re-read the `idempotency_attempts`
   row and replay verbatim" (PLAN-REBUILD control-flow line 35), NOT "re-derive via
   `_build_idempotency_hit_result`." These 3 tests must be PORTED (test-integrity: don't delete to
   close a gap). The DB-level test `test_duplicate_idempotency_key_raises_integrity_error` (49-93)
   still holds (the `MediaBuy.idempotency_key` unique index is KEPT — PLAN-REBUILD line 60), but
   the two recovery tests (96-218) pin the deleted helper.

2. **No test asserting the success-cache write is ATOMIC with the buy create (same txn).**
   OBSERVED — today the create path opens its OWN `MediaBuyUoW` at `media_buy_create.py:2536`
   (pending) / 3438-region (auto-approved), and the rejection cache uses a SEPARATE
   `_CacheUoW` at `:1542`. The plan's atomicity claim (PLAN-REBUILD "Two latent bugs" #3) is NEW
   behavior — `record_success` must be called INSIDE the create UoW. There is **no `record_success`
   method yet** (OBSERVED grep miss). A test that the success row and the MediaBuy row commit in
   one transaction (e.g. inject a failure after `create_from_request` but before commit → assert
   NEITHER row exists) is the only thing that pins atomicity. The plan does not list it.

3. **A test that an ERROR is NOT cached (retry re-executes).** OBSERVED — the existing
   `TestTransientRejectionNotCached` (test_idempotency_replay.py:371-487) tests
   transient-not-cached but its CONTROL asserts non-transient errors ARE cached
   (`test_non_transient_adapter_rejection_is_cached`, 452-486). Under β, **NO error is ever
   cached** (PLAN-REBUILD line 15/44). So the control test INVERTS: a terminal adapter rejection
   must now leave **no** `idempotency_attempts` row, and a retry must re-execute (call the adapter
   a second time). The plan says "port test_idempotency_replay.py" but does not flag that the
   `test_non_transient_..._is_cached` assertion must be inverted, and the whole
   `TestTransientRejectionNotCached` premise ("transient skip vs cache") collapses to "nothing is
   cached" — the transient/terminal distinction disappears. This is a semantic rewrite, not a port.

4. **A test that the advisory is FROZEN on replay** (PLAN-REBUILD latent-bug #2,
   `media_buy_create.py:1626-1631` live-rebuild). The plan's V4 calls for confirming "no test
   depends on the old live-rebuild," but does NOT add a POSITIVE test that the replayed advisory
   equals the STORED one even when the underlying capability flips. INFERRED — this is the
   byte-for-byte regression guard; without it the freeze is unenforced.

5. **Missing-key wire matrix vs the existing `INVALID_REQUEST` BDD nodes.** The plan adds a
   missing-key wire test asserting `VALIDATION_ERROR` (PLAN-REBUILD test table). But the LIVE BDD
   nodes (`test_idempotency_key_partition_validation[...-too_short-...-error "INVALID_REQUEST"...]`,
   80 nodes) assert `INVALID_REQUEST`. These two cannot both be green. The plan must either (a)
   emit `INVALID_REQUEST` (needs a new typed `AdCP*Error` — grounding-C E2, none exists today,
   OBSERVED only mapping is `NOT_FOUND→INVALID_REQUEST` at `exceptions.py:36`), or (b) add
   conftest xfails for the `INVALID_REQUEST` idempotency nodes citing the same "VALIDATION_ERROR vs
   INVALID_REQUEST" gap already used at `conftest.py:123-128`. The plan picks `VALIDATION_ERROR`
   (D2) but never reconciles the BDD side.

### Mock-only-vs-wire check (per memory)
- The plan's matrix is correctly wire-level (MCP `call_via` / A2A `on_message_send` / REST
  `TestClient`) — matches the gold standard. No mock-only smell in the plan's ERROR matrix.
- **BUT** the success-replay row CANNOT be wire-tested until `TransportResult` surfaces
  `replayed` (B2/S6). If that is skipped, the only place to assert `replayed:true` is the IMPL
  transport (`call_impl` returns the model directly), which the memory explicitly warns is NOT a
  wire test. So skipping S6 forces the success-replay assertion to be effectively IMPL-only =
  the mock-only failure mode in spirit. Flagged.

---

## UNCERTAIN (could not verify without running / out of scope)

1. **Do the 80 UC-003 idempotency BDD nodes currently PASS or XFAIL?** I confirmed they COLLECT
   (OBSERVED) and that only `T-UC-003-ext-p-short/long` (2 tags) are in `_UC003_EXT_XFAILS`
   (`conftest.py:555-556`, `strict=False`). The `-absent`, `-valid`, partition, and boundary tags
   are NOT xfailed → they run for real. But pass-vs-xfail depends on (a) whether uc003's
   unregistered step module still resolves its steps at runtime and (b) current production
   behavior. I could not run BDD (no DB up). This matters because it decides whether the plan
   FLIPS a green→red (regression) or red→still-red (xfail churn). HIGH-VALUE to run
   `tox -e bdd -- -k "idempotency and uc003"` before building.

2. **Whether adding a conformant UC-002 success-replay `.feature` scenario is possible.** The
   features are compiled from an adcp-req spec snapshot dated 2026-05-20 / schema v3.0.0-rc.1
   (OBSERVED header) that encodes the OLD "absent proceeds" rule. Whether the current spec source
   (post-3.0.0 success-caching rule) carries a success-replay scenario for `compile_bdd.py` to
   emit is not determinable from this repo's compiled artifacts alone. If it does not, the plan's
   BDD scenarios cannot be added via the sanctioned pipeline without a spec-source bump.

3. **`replayed` omit-vs-explicit-false on the wire for storyboard runners.** The error builder
   omits when false (`exceptions.py:643-644`, OBSERVED). The plan mirrors that (omit-when-false).
   Whether storyboard runners require explicit `false` is not verifiable here (no storyboard in
   repo). Low risk — the error path already omits and passes.

4. **Exact `account_id` migration shape (V1).** OBSERVED — `find_by_key` scopes on
   `(tenant_id, principal_id, tool_name, idempotency_key)` with NO `account_id`
   (`idempotency_attempt.py:62-72`), while `MediaBuy.create_from_request` already persists
   `account_id=identity.account_id` (`media_buy_create.py:2551`). So V1's premise (account_id
   absent from the cache scope but present on identity/MediaBuy) is OBSERVED-true. Whether two
   accounts under one principal actually reuse keys in practice (the false-conflict scenario) is a
   product judgment I can't settle.

---

## Cross-reference to plan's own verify-points
- **V2 (missing-key → VALIDATION_ERROR uniformly across transports):** mechanically YES via
  `CreateMediaBuyRequest(...)` raising in both wrappers (OBSERVED `:4162/4244`), and REST coerces
  similarly. But it produces `VALIDATION_ERROR`, colliding with the BDD `INVALID_REQUEST`
  contract (B5/B6). V2's "enumerate blast radius" is the 37-file + harness sweep (B5).
- **V3 (`status`/`replayed` top-level per transport):** OBSERVED — `_serialize` puts `status`
  top-level (`_base.py:296-300`); A2A/MCP/REST all dump the same wrapper (A2A `_serialize_for_a2a`
  → `model_dump`, MCP `structured_content`, REST `model_dump`). So a sibling `replayed` WOULD land
  top-level on all three. CONFIRMED for the PRODUCTION wire — the gap is purely on the HARNESS
  reconstruction side (B2/S6).
- **V4 (no test depends on old live-rebuild):** `test_idempotency_race.py`'s
  `TestBuildIdempotencyHitResult` (96-138) DOES depend on `_build_idempotency_hit_result`, which
  contains the re-derivation. So V4 is REFUTED as stated — a test does depend on it; it must be
  ported, not just "confirmed clear."
