# R1 — SPEC + STORYBOARD Adversarial Review (idempotency #1312 rebuild)

**Role:** Refute every spec/storyboard assumption in `PLAN-REBUILD.md`. An assumption survives ONLY if
confirmed against the actual spec prose or storyboard YAML (verbatim + URL).
**Date:** 2026-06-09. **Read-only.** No code edited.

## Provenance (OBSERVED — re-verified this session, not trusted from grounding doc)

- In-repo pin: `uv run python -c "import adcp; print(adcp.get_adcp_sdk_version(), adcp.get_adcp_spec_version())"`
  → **`4.3.0 3.0.1`**.
- **Storyboard** `dist/compliance/3.0.1/universal/idempotency.yaml` — blob SHA
  `da74e434856acd180e371ea7025ea1be23d80fe5`, **identical at v3.0.1 tag and main** (both verified this
  session via `gh api .../contents?ref=v3.0.1` and `?ref=main`). Decoded 28 905 bytes / 593 lines via
  `gh api repos/adcontextprotocol/adcp/git/blobs/da74e...`. The `contents` API returned the SHA fine here
  (the grounding-doc 404 note did not reproduce for the SHA lookups).
- **Prose** `dist/docs/3.0.0/building/implementation/security.mdx` as it stood at the v3.0.1 tag — blob SHA
  `76089beebf2c159ecd9a2ec69db7b2386b32caba` (verified current at `ref=v3.0.1` this session). Decoded
  199 567 bytes. Idempotency section = lines 277–476.
- The grounding doc `grounding/A-spec-storyboard-contract.md` checks out against both raw sources — its
  verbatim quotes match. I re-pulled both sources independently rather than trusting it.

---

## LEAD: REFUTED / CONFLICT / NEEDS-ADJUSTMENT (the items that change the plan)

### S10 / D3 — IDEMPOTENCY_CONFLICT recovery class → **CONFIRMED the plan's choice (terminal) is SAFE; the grounding-doc/SYNTHESIS framing of a "storyboard vs SDK conflict" is REFUTED.**

This was flagged as the highest-risk possible plan error. **It is not an error.** The storyboard does NOT
assert recovery class on the conflict step.

- **OBSERVED — storyboard `key_reuse_conflict` → `create_media_buy_conflict` → `validations:` block
  (idempotency.yaml lines 460–471)** — the ONLY programmatic checks are:
  ```yaml
  validations:
    - check: error_code
      allowed_values: ["IDEMPOTENCY_CONFLICT", "CONFLICT"]
    - check: field_present
      path: "context"
    - check: field_value
      path: "context.correlation_id"
      value: "idempotency--create_media_buy_conflict"
  ```
  **There is no `check` on `recovery` anywhere in the conflict step.** `recovery: correctable` appears only
  inside the human-readable `expected:` prose (line 441: "recovery: correctable (buyer should use a fresh
  UUID v4)") — prose, not a graded assertion.
- **OBSERVED — installed SDK 4.3.0** (`uv run python -c "from adcp.server.helpers import STANDARD_ERROR_CODES; ..."`):
  `IDEMPOTENCY_CONFLICT -> terminal`, `IDEMPOTENCY_EXPIRED -> terminal`, `CONFLICT -> correctable`,
  `INVALID_REQUEST -> correctable`, `VALIDATION_ERROR -> correctable`, `RATE_LIMITED -> transient`.
- **OBSERVED — spec prose** (security.mdx line 361) sets `"recovery": "correctable"` in the example body.

**Verdict: the storyboard grades only the error CODE on the conflict step, not the recovery class.** Emitting
`IDEMPOTENCY_CONFLICT` with recovery `terminal` (to match the SDK validator and the existing
`AdCPIdempotencyConflictError` + `test_adcp_exceptions.py:122`) PASSES the graded storyboard. There is NO
storyboard-vs-SDK conflict at runtime. The only divergence is prose-intent (correctable) vs SDK-table
(terminal), which the plan already handles by documenting it (mirroring `AdCPAuthenticationError`).
**Plan D3 stands. The SYNTHESIS.md D3 lean ("follow spec → correctable") was the WRONG lean; PLAN-REBUILD's
reversal to terminal is correct and storyboard-safe.** (NEEDS-ADJUSTMENT only to SYNTHESIS.md's stale framing,
not to the plan.)

### S5 / V4 — byte-for-byte freeze of the property_list advisory → **NEEDS-ADJUSTMENT.** Byte-for-byte IS required (S5 CONFIRMED). But the plan's V4 claim "confirm no test depends on the old live-rebuild" is **REFUTED** — a test class explicitly asserts live-rebuild semantics.

- **OBSERVED — spec mandates byte-for-byte** (security.mdx line 339): *"On a cached replay it is `true`; the
  inner `payload` is **byte-for-byte what was stored on the original successful execution**."* Also rule 2
  line 288: *"**The cache entry is immutable** — even if the async task subsequently completes, fails, or is
  canceled, a replay within the TTL MUST return the originally-cached … response."* So freezing the advisory
  (storing the fresh-time envelope verbatim) is spec-correct; the current live-rebuild at
  `media_buy_create.py:1626-1631` is a genuine byte-for-byte violation. **S5 = CONFIRMED.**
- **OBSERVED — V4 is wrong as stated.** `tests/unit/test_property_list_unsupported_advisory.py:274-347`
  defines `class TestIdempotencyReplayRebuildsAdvisory` with two tests
  (`test_replay_rebuild_helper_returns_advisory_when_capability_off`,
  `test_replay_rebuild_disappears_when_capability_flips_true`). Their docstrings assert *"the advisory must
  be rebuilt live, not read from cache … rather than locking in stale Day-1 state"* — the exact opposite of
  byte-for-byte. **A test DOES depend on the old semantics.**
- **MITIGATION (OBSERVED — softens the impact):** Those two tests call the *helper*
  `build_property_list_unsupported_advisories(...)` **directly** (lines 308, 343), not
  `_build_idempotency_hit_result` and not the replay path. The helper lives in
  `src/services/targeting_capabilities.py:228` and is **NOT in the plan's REMOVE list**. So the two tests
  will still PASS post-rebuild (the helper is unchanged) — they just stop testing replay. Their class
  name + docstrings become semantically stale/contradictory with the new byte-for-byte behavior.
- **ACTION FOR PLAN:** Add an explicit step to **update/retire `TestIdempotencyReplayRebuildsAdvisory`** —
  its premise ("rebuild live on replay") is being inverted to "freeze at fresh-time." Leaving it green-but-
  contradictory is a latent trap for the next reader (it documents behavior the codebase no longer has).
  Note also the helper is called on all four fresh success paths
  (`media_buy_create.py:2841, 3000, 3342, 3880`); the verbatim store at the fresh path is what freezes the
  advisory — confirm the advisory is computed and present in the serialized envelope BEFORE
  `record_success`, or the frozen replay will be missing advisories the fresh call returned.

### S4 / V1 — account_id in the scope → **CONFIRMED account MUST be in the scope, and the plan's lean (ADD `account_id`) is correct and necessary.** This is the highest-priority correctness item and the plan has it right.

(a) **Does the spec REQUIRE account in scope, distinct from the agent? YES — OBSERVED.**
- security.mdx rule 2 line 288 (verbatim): *"… keyed by `(authenticated_agent, account_id, idempotency_key)`
  along with a hash of the canonical request payload."*
- security.mdx line 281 (verbatim): *"Keys are scoped per `(authenticated agent, account)` … Scoping by
  both dimensions prevents cross-account cache collisions when one agent (e.g. an agency) acts on multiple
  accounts: an identical-looking `create_media_buy` under account A and account B is two distinct buys,
  never one cached response replayed across the two."*
- **agent ≠ account.** security.mdx line 161 (verbatim): *"The authenticated agent is how the seller knows
  *who is calling*; the `account` on the request is *what billing relationship the call is acting on*."*
- account_id is also a named oracle-surface component (line 401): *"`account_id` is part of every
  idempotency scope tuple."*

(b) **Does the spec include `tool_name` in scope? NO — including it is a DEVIATION (but a SAFE one).**
- The spec scope tuple is `(authenticated_agent, account_id, idempotency_key)` (line 288) — NO task/tool
  dimension. For `si_send_message` it ADDS `session_id` (line 372), never a tool name.
- Our table has `tool_name` in the key (`idempotency_attempt.py:30`, `models.py:1007`, unique index
  `models.py:1035-1042`). **This is NARROWER than spec** (a key reused across two different tools would
  collide in spec scope but not in ours).
- **Does it cause a conformance problem? NO at 3.0.1.** The storyboard only exercises `create_media_buy`
  (single tool — see S8), so it can never drive a cross-tool key reuse. The buyer obligation (line 380:
  "unique key per `(seller, request)`") means a well-behaved buyer never reuses a key across tools anyway.
  **INFERRED:** keeping `tool_name` is over-segmentation, not under-segmentation — it can only ever *split*
  a scope the spec would merge, producing a FALSE MISS (re-execute) where spec would CONFLICT. That is a
  spec deviation but ungraded and low-risk; it does NOT create a false replay or cross-account leak. Flag it,
  but it is not a blocker. (If generalizing to other tools later, revisit: two different mutating tasks
  sharing a key is a spec conflict we would silently allow.)

(c) **In OUR codebase, can one authenticated principal create buys for MULTIPLE accounts? YES — OBSERVED,
this is the decisive evidence that account_id MUST join the scope.**
- `create_media_buy` accepts a per-request `account: AccountReference | None`
  (`media_buy_create.py:4106-4114`), described as *"scoping this buy to a sub-account the authenticated
  agent manages."* Same on the A2A raw fn (`:4207`).
- The boundary calls `enrich_identity_with_account(identity, req.account)`
  (`media_buy_create.py:4184, 4267`), which resolves the AccountReference and returns
  `identity.model_copy(update={"account_id": account_id})` (`transport_helpers.py:138`). So `account_id` is
  request-derived, not principal-derived — one principal, many accounts per session.
- **The current idempotency scope OMITS account_id.** Repo: `find_by_key` filters on
  `(tenant_id, principal_id, tool_name, idempotency_key)` only (`idempotency_attempt.py:47-73`); model
  unique index is the same four columns (`models.py:1035-1042`); no `account_id` column exists on
  `IdempotencyAttempt` (`models.py:978-1044`).
- **CONSEQUENCE (OBSERVED → INFERRED):** principal P creates a buy for account A with key K (payload X),
  then creates a buy for account B with the *same* key K (payload Y). Under the current scope these collide
  on `(tenant, P, create_media_buy, K)`; the second call sees a hash mismatch and would emit a SPURIOUS
  `IDEMPOTENCY_CONFLICT` for what the spec says are *"two distinct buys, never one cached response"* (line
  281). If payloads happened to match, it would be a worse failure — a cross-account replay. **This is a
  real correctness/security gap, exactly what the spec's two-dimension scoping exists to prevent.**

**Verdict S4/V1: CONFIRMED. `account_id` MUST join the scope.** The plan's lean (add column + rework unique
index + migration) is correct and necessary, not optional. **One caveat the plan must handle:** `account_id`
is nullable (a buy with no `account` reference → `identity.account_id is None`). The scope/unique-index must
treat NULL account_id consistently (e.g., COALESCE to a sentinel, or a NULL-safe unique index) so two
no-account buys under one principal with different payloads still conflict correctly and don't bypass the
constraint (Postgres treats NULLs as distinct in unique indexes by default — two NULL-account rows with the
same key would NOT collide, re-opening the dup-booking window). Verify the migration uses a NULLS NOT
DISTINCT index or a sentinel.

---

## S1–S10 verdicts (full)

### S1 — success-only caching; errors NEVER cached; retry-after-error re-executes → **CONFIRMED**
- **OBSERVED — security.mdx rule 3 line 289 (verbatim):** *"3. **Only successful responses are cached.** On
  any error — validation, governance denial, transport failure, internal error — the key is **not** stored.
  A retry re-executes. … It also prevents a buyer's malformed request from being locked into a key for its
  full TTL."*
- **OBSERVED — storyboard narrative item 5 (lines 46–57, verbatim):** *"Error responses (returned
  envelopes, thrown envelopes, and uncaught exceptions) do not cache. The next request carrying the same key
  re-executes the handler. This applies to every recovery class, including terminal …"*
- **OBSERVED — storyboard `key_reuse_conflict` reviewer_check 3 (lines 476, verbatim):** *"… force a
  deterministic terminal error, then retry with the same key and a valid payload — the seller MUST return a
  fresh success, not IDEMPOTENCY_CONFLICT and not the cached error."*
- This is the exact inverse of what #1312 shipped (cache + replay rejections). Plan's headline fix = CONFIRMED.
  URL: `.../git/blobs/76089beebf2c159ecd9a2ec69db7b2386b32caba` (security.mdx line 289).

### S2 — missing-key emits VALIDATION_ERROR (not INVALID_REQUEST), storyboard accepts both → **CONFIRMED**
- **OBSERVED — storyboard `create_media_buy_missing_key` validations (idempotency.yaml lines 200–203,
  verbatim):**
  ```yaml
  - check: error_code
    allowed_values: ["INVALID_REQUEST", "VALIDATION_ERROR"]
    description: "Missing idempotency_key rejected with INVALID_REQUEST or VALIDATION_ERROR"
  ```
  **The storyboard's graded code set for the missing-key step is exactly `["INVALID_REQUEST",
  "VALIDATION_ERROR"]`.** Emitting `VALIDATION_ERROR` PASSES. S2 = CONFIRMED.
- **CAVEAT (NEEDS-ADJUSTMENT, two ungraded `expected:` fields the plan should still honor):** the step's
  `expected:` prose (lines 178–183) additionally states `recovery: correctable` and `field: idempotency_key
  (recommended)`. These are NOT in the `validations:` block (so not runtime-graded), but:
  - `VALIDATION_ERROR` is `correctable` in the SDK (verified above) → recovery is satisfied automatically. ✓
  - `field: idempotency_key` is "(recommended)", i.e. SHOULD not MUST, and ungraded. The plan emits via
    existing schema validation; confirm during build that the resulting envelope's `field`/error detail
    points at `idempotency_key` if cheaply available, but this is not a conformance blocker.
- **Note (prose, line 281 + rule narrative):** spec PROSE says missing → `INVALID_REQUEST` ("Sellers MUST
  reject … with `INVALID_REQUEST`"). The STORYBOARD relaxes this to accept `VALIDATION_ERROR` as an
  alternative. Since the storyboard is the executable graded contract, `VALIDATION_ERROR` is conformant. The
  plan should note it is choosing the storyboard-accepted alternative over the prose's literal `INVALID_REQUEST`
  (deliberate, defensible — and avoids inventing a new `INVALID_REQUEST`-emitting error class, which
  SYNTHESIS D2 had flagged as otherwise required).

### S3 — conflict body: code + message ONLY (no payload/diff/fingerprint/field pointer) → **CONFIRMED**
- **OBSERVED — security.mdx "IDEMPOTENCY_CONFLICT response shape" lines 350–353 (verbatim):** *"- MUST
  include `code: "IDEMPOTENCY_CONFLICT"` and a human-readable `message`. - MUST NOT include the cached
  response, the original payload, a canonical-form diff, or any fingerprint derived from them. A `field`
  json-pointer hint seems harmless but reveals schema shape … Sellers MUST NOT emit one."*
- **OBSERVED — storyboard reviewer_checks (idempotency.yaml lines 473–476, verbatim):** *"Error body MUST
  NOT include the cached payload, the original request, or any fingerprint (hash, digest, field diff). …
  Error body MAY include code + message only. No `field` json-pointer …"*
- Plan's read-oracle defense (code + msg only) = CONFIRMED.

### S4 / V1 — scope by (tenant, principal, account_id?, tool, key); ADD account_id → **CONFIRMED (must add).**
See LEAD section above for full evidence (a/b/c) + the NULL-account_id caveat.

### S5 — byte-for-byte replay; freeze the advisory → **CONFIRMED (byte-for-byte) / NEEDS-ADJUSTMENT (V4 test claim).**
See LEAD section above. Byte-for-byte is mandated (security.mdx 339, 288). The V4 sub-claim "no test depends
on live-rebuild" is REFUTED (`test_property_list_unsupported_advisory.py:274-347`), but those tests hit the
surviving helper, not the replay path, so they stay green — they become semantically stale and should be
retired/rewritten.

### S6 — EXPIRED out-of-scope while advertising replay_ttl_seconds=86400; honor TTL durably; post-TTL = miss → **CONFIRMED, with a sharp durability caveat the plan already states but MUST enforce.**
- **OBSERVED — storyboard has NO EXPIRED step.** I read all 7 phases of idempotency.yaml: `capability_discovery,
  missing_key, replay_same_payload (+ no_duplicate_webhooks_on_replay), key_reuse_conflict,
  fresh_key_new_resource, verify_media_buy_count`. **No `IDEMPOTENCY_EXPIRED` step exists.** The narrative
  (lines 55–57) explicitly defers it: *"An end-to-end phase that drives a deterministic terminal error and
  replays the key is deferred pending a generic force-error controller verb (see adcontextprotocol/adcp#2760)."*
  → **Advertising a TTL while never emitting IDEMPOTENCY_EXPIRED does NOT create a storyboard/conformance
  gap at 3.0.1.** No graded step fails if post-TTL is a silent cache-miss. S6 storyboard-side = CONFIRMED.
- **OBSERVED — EXPIRED is MAY/SHOULD, conditioned on distinguishability** (security.mdx rule 6 line 292,
  verbatim): *"After `replay_ttl_seconds` elapses the seller **MAY** evict the cache entry. A request
  arriving after eviction with a key the seller has seen **SHOULD** be rejected with `IDEMPOTENCY_EXPIRED`
  …"* And the durability sub-clause (line 294): *"… MUST fail-closed (`IDEMPOTENCY_EXPIRED`) rather than
  fail-open (silent re-execution) **when they cannot distinguish 'never seen' from 'evicted under declared
  TTL.'**"* A store with no evicted-key tombstone legitimately treats expired → miss → re-execute. Spec-
  permitted to omit EXPIRED. CONFIRMED.
- **MUST-level durability caveat — OBSERVED (security.mdx rule 6 line 294, verbatim, the part the plan must
  honor):** *"The declared `replay_ttl_seconds` is a durability contract, not a best-effort cache hint.
  Sellers MUST back the idempotency cache with storage that survives process restarts, pod replacements,
  region failovers, and operator-initiated cache flushes for the declared TTL. … Sellers MUST NOT declare a
  `replay_ttl_seconds` higher than their cache tier can durably honor."*
  - **Must we retain successes for the full 86400s with no early eviction? YES, for the declared window.**
    The plan declares `replay_ttl_seconds=86400` and stores in Postgres (durable across restarts) ✓. BUT:
    - **OBSERVED GAP:** the existing `expire_old` has *"No production caller is wired yet"*
      (`idempotency_attempt.py:118-122`) — so today nothing prunes. Under β (success cache) the rows are
      successes, not rejections. **No early eviction is the SAFE direction** (over-retention ≥ declared TTL
      is fine; the violation is *under*-retention). But `find_by_key` filters `expires_at > now`
      (`idempotency_attempt.py:69`), so a row is treated as a miss the instant its 24h TTL passes → silent
      re-execute. That is spec-permitted (we don't claim EXPIRED) **only because we don't distinguish
      seen-and-evicted from never-seen.** Confirm the plan does NOT add a tombstone/seen-set that would make
      us *able* to distinguish — because the moment we can distinguish, rule 6 line 294 flips the SHOULD into
      a near-MUST and silent re-execute becomes fail-open. The plan's "EXPIRED out of scope" is only honest
      while the store has no evicted-key memory. **Document this coupling explicitly.**
    - **Is there a storyboard step that fails on post-TTL miss? NO** (verified — no EXPIRED step). So S6 is
      conformance-safe.

### S7 — replayed: top-level on envelope beside `status`; per-transport; name `replayed`; boolean; omit-when-false OK → **CONFIRMED**
- **OBSERVED — top-level placement + name + bool** (security.mdx "Response-level replay indicator" lines
  326–339, verbatim): *"The protocol envelope carries a top-level `replayed` boolean on responses to mutating
  requests:"* with the example `{ "status": "completed", "replayed": true, "timestamp": "...", "payload": {…} }`
  — `replayed` is a sibling of `status`. ✓
- **OBSERVED — per-transport** (security.mdx rule 4 line 290, verbatim): *"**Transport-specific note for
  MCP:** MCP tool responses do not have a separate envelope slot; servers MAY expose `replayed` inside the
  tool result object itself (e.g., at the top of the structured return) or via a response metadata field.
  **REST and A2A responses use the envelope field directly.**"* → REST/A2A envelope-level, MCP top-of-
  structured-result. Plan's "single choke point in `_serialize` → uniform across MCP/A2A/REST" is consistent;
  V3 (confirm `result["status"]` level == protocol-envelope top-level per transport) is the right thing to
  verify at wire time. CONFIRMED at spec level.
- **OBSERVED — omit-when-false is acceptable** (security.mdx line 339, verbatim): *"On a fresh execution it
  is `false` (or omitted — buyers MUST treat omission as `false`)."* And storyboard fresh steps use
  `field_value_or_absent, allowed_values: [false]` (idempotency.yaml lines 281–284, 532–535) — "passes when
  absent OR present-and-matching" (comment lines 274–280). So `omit-when-false` PASSES the fresh-exec checks.
- **OBSERVED — replay must be `true`** (storyboard `create_media_buy_replay` lines 352–355, verbatim):
  `check: field_value, path: "replayed", value: true`. So on replay the field MUST be present and `true`
  (not omitted). Plan's "inject true on replay, omit on fresh" satisfies both. CONFIRMED.

### S8 — storyboard grades ONLY create_media_buy at 3.0.1 → **CONFIRMED**
- **OBSERVED — every mutating step uses `task: create_media_buy`** (idempotency.yaml: lines 169, 227, 301,
  430, 493). The only other task is `get_media_buys` (read, dedup count, line 560) and `get_adcp_capabilities`
  (line 111) and `expect_webhook` (line 387, runner-contract step). No `update_media_buy`/`sync_*`/etc.
- **OBSERVED — prerequisites (idempotency.yaml lines 88–92, verbatim):** *"The storyboard uses
  create_media_buy as the canonical mutating request. Sellers that do not support create_media_buy SHOULD
  still pass idempotency compliance on whichever mutating task they do implement."*
- **OBSERVED — prose obligation is broader** (security.mdx line 281): the contract applies to ~25 mutating
  tasks + all `sync_*`. So "create_media_buy now / other tools fast-follow" is conformance-complete for 3.0.1
  grading, but the spec obligation extends to every mutating tool the seller implements. Plan's scoping =
  CONFIRMED for the graded gate; the fast-follow framing is correct.

### S10 / D3 — keep IDEMPOTENCY_CONFLICT recovery = terminal → **CONFIRMED (storyboard-safe).** See LEAD.

---

## PLAN OMITS (spec/storyboard requirements not addressed by the plan)

1. **`no_duplicate_webhooks_on_replay` side-effect invariant (storyboard step, idempotency.yaml lines
   365–410).** The storyboard's *central* replay invariant is "replaying with the same key MUST NOT produce
   duplicate side effects" — specifically webhooks. The plan's verbatim-replay (handler NOT run on hit)
   structurally satisfies this (no handler = no new webhook), which is good. **But the plan's test matrix has
   no "no duplicate side effects on replay" assertion.** The storyboard grades this step as `not_applicable`
   only when the runner lacks a webhook receiver — it is a real graded invariant otherwise. **Recommend** the
   plan add a test asserting the replay path does NOT invoke the adapter / does NOT enqueue a
   webhook/notification (the plan already says "handler NOT run" in the control flow — make that a tested
   assertion, not just a comment). This is the single most important *behavioral* gap vs the storyboard.

2. **`verify_media_buy_count` end-to-end dedup check (storyboard phase, idempotency.yaml lines 545–593).**
   The storyboard's final gate calls `get_media_buys` and asserts **exactly TWO** media buys exist across all
   steps (initial+replay dedup to one; fresh-key is the second) — "Not three." The plan's MediaBuy
   `idempotency_key` unique constraint is the backstop, but **the plan does not test the row-count invariant**
   (that a replay creates zero new MediaBuy rows). Recommend a test asserting the DB has one MediaBuy after
   initial+replay (not two).

3. **`fresh_key_new_resource` (storyboard phase, idempotency.yaml lines 478–543).** A DIFFERENT key with an
   IDENTICAL payload MUST create a NEW media_buy_id (proves dedup is keyed on the key, not on payload
   fingerprint). The plan's test matrix has {success-replay, conflict, missing-key} but **NOT a fresh-key /
   different-key-same-payload case.** This is a graded storyboard step. Recommend adding it.

4. **`replayed` schema location for `CreateMediaBuyResponse` specifically (carried over from grounding
   UNCERTAIN #3, still open).** The plan adds `replayed` to `CreateMediaBuyResult` (`_base.py:283-300`) and
   injects in `_serialize`. The SDK carries `replayed` on `ProtocolEnvelope`/`TaskResult`, and
   `CreateMediaBuyResponse` is a UnionType — **the plan should verify the response_schema check
   (idempotency.yaml lines 343-344: `check: response_schema` against `create-media-buy-response.json`) still
   passes with a top-level `replayed` field present.** If the response schema is `extra="forbid"`-strict in
   CI and `replayed` isn't an allowed field on the create-media-buy-response shape, the storyboard's
   `response_schema` check on the replay step could fail. **Verify the create-media-buy-response.json (or the
   SDK union arm) admits `replayed` at the level the plan injects it.** (UNCERTAIN — I did not unpack the
   3.0.1 schema tarball; flagged for build-time verification.)

5. **`account_id_is_opaque` capability field (OBSERVED, minor).** The SDK `Idempotency` capability variant
   has an optional `account_id_is_opaque` bool (grounding A §G, line 334). The plan's capability advertises
   only `supported:true, replay_ttl_seconds:86400`. Not required (it's optional), not graded by the
   storyboard, but note it exists if the team wants to signal natural-key hashing (relevant given our
   implicit-accounts `{brand, operator}` model — security.mdx line 401 requires hashing natural keys with a
   seller-local salt before using as a cache-scope component). **Not a blocker; flag for completeness.**

6. **Natural-key account hashing for oracle safety (security.mdx line 401, MUST for implicit-accounts).**
   *"Sellers operating under the implicit-accounts model (natural-key `{brand, operator}`) MUST hash the
   natural key with a seller-local salt before using it as a cache-scope component."* If our resolved
   `account_id` (from `resolve_account`) is a natural-key-derived value rather than a server-assigned UUID,
   adding it raw to the scope/index may not satisfy this MUST. **Verify what `resolve_account` returns** (UUID
   vs natural-key string) when wiring V1. Ungraded at 3.0.1, but a real spec MUST. (INFERRED relevance; not
   verified what resolve_account emits.)

---

## UNCERTAIN / COULD NOT VERIFY

1. **HTTP status code for IDEMPOTENCY_CONFLICT.** Neither security.mdx nor the storyboard specifies an HTTP
   status — the conflict is carried in the AdCP error envelope (`errors[].code`), and the storyboard asserts
   only `error_code`, not an HTTP status. Do NOT assert a specific HTTP code for the conflict without further
   grounding. (OBSERVED absence; INFERRED transport-status-agnostic.)

2. **create-media-buy-response.json admits top-level `replayed`** (PLAN OMITS #4). The graded `response_schema`
   check on the replay step parses against that schema. I did not unpack `dist/protocol/3.0.1.tgz` /
   `dist/schemas/` — used the installed SDK 4.3.0 models as proxy (which DO carry `replayed` on
   `ProtocolEnvelope`/`TaskResult`/several response models, but `CreateMediaBuyResponse` is a UnionType I did
   not exhaustively enumerate). **Verify at build time** with a roundtrip of a replay envelope through the
   SDK's `CreateMediaBuyResponse` validation.

3. **What `resolve_account` returns** (PLAN OMITS #6) — UUID vs natural-key string — was not verified; bears
   on the line-401 natural-key-hashing MUST when account_id joins the scope.

4. **Postgres NULL-distinct behavior on the new account_id unique index** (LEAD S4 caveat). I asserted (from
   general Postgres semantics) that two NULL-account rows with the same key would NOT collide under a default
   unique index, re-opening a dup window. The plan must choose `NULLS NOT DISTINCT` (PG15+) or a sentinel.
   I did not verify the project's Postgres version supports `NULLS NOT DISTINCT`. **Verify PG version / index
   strategy at migration time.**

---

## Bottom line

- **No REFUTED plan blocker on the core S1–S10 spec assumptions** — every spec/storyboard premise the plan
  rests on is CONFIRMED, including the one flagged as a likely error (S10/D3 terminal is storyboard-safe
  because the conflict step grades CODE only, not recovery).
- **Two NEEDS-ADJUSTMENT corrections inside surviving assumptions:** (S5/V4) a test class asserts the
  live-rebuild semantics being inverted — retire/rewrite it; (S2) honor the ungraded `recovery=correctable` +
  `field` SHOULDs.
- **The highest-correctness item (S4/V1, add account_id) is CONFIRMED as necessary** — verified against both
  the spec scope tuple AND our codebase (one principal, many accounts via `req.account` +
  `enrich_identity_with_account`). The plan has it right; add the NULL-account_id index caveat.
- **Three storyboard graded steps the test plan OMITS:** no-duplicate-webhooks-on-replay, fresh-key-new-
  resource, and the 2-row dedup count. These are graded conformance gates the plan's matrix doesn't cover.
- **One latent durability coupling to document:** "EXPIRED out of scope" is only spec-honest while the store
  keeps NO evicted-key tombstone; adding a seen-set would flip silent-re-execute into fail-open.
