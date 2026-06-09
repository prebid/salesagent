# AdCP Idempotency — Authoritative Conformance Contract (spec 3.0.1 / SDK adcp 4.3.0)

Read-only spec grounding for the spec-conformant rebuild of PR #1312. Every claim is
labeled **OBSERVED** (verbatim quote + URL/command) or **INFERRED** (derived, with basis).

## 0. Provenance & version pinning (OBSERVED)

- In-repo pin verified:
  `uv run python -c "import adcp; print(adcp.get_adcp_sdk_version(), adcp.get_adcp_spec_version())"`
  → **SDK `4.3.0`, spec `3.0.1`**.
- Upstream repo: `github.com/adcontextprotocol/adcp`.
  - Tag `v3.0.1` → annotated tag object, tagged **2026-04-28T12:44:07Z**, points at commit
    `2a24ca5f070f36bf7d1793651a1f0fb4f50084dd`.
  - Tag `v3.0.0` → tagged **2026-04-22T09:49:30Z**, commit `4472eb295cf49e6a0ff5dcf69e72749da8ca31ae`.

### Repo-layout reconciliation (OBSERVED — corrects a stale assumption)

The SDK docstrings cite `docs/building/implementation/security.mdx#idempotency`. Memory
said `dist/docs/<version>/...`. **Both are real, but the version folders are subtle:**

- At the **v3.0.1 tag**, immutable doc snapshots under `dist/docs/` go up to **`3.0.0`**
  only — there is **NO `dist/docs/3.0.1/` folder at the v3.0.1 tag**.
  (`gh api ".../contents/dist/docs?ref=v3.0.1"` → `2.5.3, 3.0.0-beta.3, 3.0.0-rc.1, 3.0.0-rc.2, 3.0.0`.)
- The authoritative **version-pinned 3.0.1 idempotency prose** is therefore the
  `dist/docs/3.0.0/building/implementation/security.mdx` snapshot **as it stood at the v3.0.1 tag**
  (blob SHA `76089beebf2c159ecd9a2ec69db7b2386b32caba`, 199 567 bytes). This file is **byte-identical
  on `main`** (same SHA) — it is an immutable per-version snapshot.
- A separate `dist/docs/3.0.1/.../security.mdx` exists **on `main` today** (added after the tag;
  blob SHA `945f20c0cb626c2e59a68dec879d280f353ce89f`, 202 421 bytes). **Its idempotency section is
  line-for-line identical** to the 3.0.0 snapshot — the only diffs are internal cross-reference URLs
  (`/dist/docs/3.0.0/...` vs `/dist/docs/3.0.1/...`); the 2.8 KB size delta is elsewhere in the file.
- **Why this is sound:** the CHANGELOG `## 3.0.1` section states verbatim (line 5):
  > "3.0.1 is a stable-surface no-op for 3.0-conformant agents. Skills bundle in `/protocol/3.0.1.tgz`,
  > normative clarifications, additive fields on experimental surfaces..."
  3.0.1 is a tarball/skills repackaging patch (commit `10aa2b3`, closes #3116/#3117). It changed
  **no** idempotency normative content.

**Canonical source URLs cited below** (pinned to the v3.0.1 tag commit):
- Prose: `https://raw.githubusercontent.com/adcontextprotocol/adcp/2a24ca5f070f36bf7d1793651a1f0fb4f50084dd/dist/docs/3.0.0/building/implementation/security.mdx`
  (also available on `main` at `.../dist/docs/3.0.1/building/implementation/security.mdx`, identical idempotency section).
  NOTE: `raw.githubusercontent.com` + `gh api .../contents` both return HTTP 404 for this path at the
  blob level for WebFetch/curl in this environment; the content was retrieved via the **git blobs API**:
  `gh api repos/adcontextprotocol/adcp/git/blobs/76089beebf2c159ecd9a2ec69db7b2386b32caba`.
- Storyboard: `dist/compliance/3.0.1/universal/idempotency.yaml`, blob SHA `da74e434856acd180e371ea7025ea1be23d80fe5`.
  **Byte-identical at the v3.0.1 tag and on `main`** (verified SHA match). Retrieved via
  `curl -sL https://raw.githubusercontent.com/adcontextprotocol/adcp/2a24ca5f070f36bf7d1793651a1f0fb4f50084dd/dist/compliance/3.0.1/universal/idempotency.yaml`.

### When the rule entered the spec (OBSERVED)

- CHANGELOG `## 3.0.0` section (header at line 210; next header `## 3.0.0-rc.3` at line 410), entry
  at line 216:
  > "43586d6, c1d2ff1: Require `idempotency_key` on all mutating requests; formalize seller declaration
  > as discriminated oneOf (#2315, #2436, #2447). Every mutating task now requires an `idempotency_key`
  > in the request envelope, matching `^[A-Za-z0-9_.:-]{16,255}$`; AdCP Verified additionally requires a
  > cryptographically-random UUID v4. Fresh key per logical operation; reuse only to retry a failed
  > request with the identical payload."
  > (line 218) "Sellers declare dedup semantics on `get_adcp_capabilities` as
  > `adcp.idempotency = { supported: true, replay_ttl_seconds: <1h–7d, 24h recommended> }` OR
  > `{ supported: false }`. When `supported: true`, sellers respond `replayed: true` on exact replay,
  > `IDEMPOTENCY_CONFLICT` when the same key accompanies a different payload, and `IDEMPOTENCY_EXPIRED`
  > after the declared TTL. ..."
- **Conclusion (INFERRED from the above):** the full idempotency contract (key required, capability
  oneOf, replayed/conflict/expired) **entered at spec 3.0.0** (tagged 2026-04-22; protocol tarball
  `3.0.0.tgz` published 2026-04-22 per CHANGELOG line 11) and is **unchanged in 3.0.1**. It predates
  PR #1312. The new error codes (`IDEMPOTENCY_CONFLICT`/`IDEMPOTENCY_EXPIRED`) and the security model
  trace to PR #2315/#2436/#2447 and the security.mdx introduction (CHANGELOG line 272, commit 8856f2e,
  #2381).

---

## A. `idempotency_key` requirement

**OBSERVED — security.mdx line 281 (verbatim):**
> "`idempotency_key` is **required** on every mutating AdCP task request (`create_media_buy`,
> `update_media_buy`, `sync_creatives`, `activate_signal`, `acquire_rights`, `creative_approval`,
> `update_rights`, `build_creative`, `calibrate_content`, `create_content_standards`,
> `update_content_standards`, `create_property_list`, `update_property_list`, `delete_property_list`,
> `create_collection_list`, `update_collection_list`, `delete_collection_list`, `log_event`,
> `provide_performance_feedback`, `report_usage`, `report_plan_outcome`, `si_initiate_session`,
> `si_send_message`, and the `sync_*` tasks). **Sellers MUST reject any mutating request that omits it
> with `INVALID_REQUEST`.** ..."

**OBSERVED — validated BEFORE cache lookup — security.mdx rule 1 line 287 (verbatim):**
> "1. **Schema validation runs first.** Sellers MUST validate the request against its schema
> (including presence and format of `idempotency_key`) BEFORE consulting the idempotency cache.
> A malformed request returns `INVALID_REQUEST` without ever touching the cache — otherwise cache
> misses become a timing side channel that leaks whether schema validation accepted the key format.
> Validation errors are never cached (per rule 2)."

**OBSERVED — storyboard `missing_key` phase, step `create_media_buy_missing_key`:**
> "Send a create_media_buy with no idempotency_key. The agent MUST reject this with INVALID_REQUEST
> (or VALIDATION_ERROR as an accepted alternative) and MUST NOT create a media buy."
> validation: `error_code allowed_values: ["INVALID_REQUEST", "VALIDATION_ERROR"]`;
> expected `recovery: correctable`, `field: idempotency_key (recommended)`.

**OBSERVED — key format — SDK `CreateMediaBuyRequest.idempotency_key` JSON schema:**
> required = True; `minLength: 16, maxLength: 255, pattern: "^[A-Za-z0-9_.:-]{16,255}$"`.
(`uv run python -c "from adcp.types import CreateMediaBuyRequest; ..."`.)

**Contract:** required on every mutating task (closed list above + all `sync_*`); missing → `INVALID_REQUEST`
(storyboard also accepts `VALIDATION_ERROR`); validated as part of schema validation, which runs
**before** any cache consultation; format `^[A-Za-z0-9_.:-]{16,255}$`; AdCP Verified wants UUID v4.

---

## B. What is cached (successes only; errors never; retry-after-error re-executes)

**OBSERVED — security.mdx rule 2 line 288 (verbatim):**
> "2. **First call is canonical.** On **task success** (`status: completed` or `status: submitted` for
> async operations), the seller stores the inner response payload (not the protocol envelope) keyed by
> `(authenticated_agent, account_id, idempotency_key)` along with a hash of the canonical request
> payload. ... **The cache entry is immutable** — even if the async task subsequently completes, fails,
> or is canceled, a replay within the TTL MUST return the originally-cached `submitted` response (with
> `replayed: true`), NOT the current terminal state. ..."

**OBSERVED — security.mdx rule 3 line 289 (verbatim) — THE LOAD-BEARING RULE:**
> "3. **Only successful responses are cached.** On any error — validation, governance denial, transport
> failure, internal error — the key is **not** stored. A retry re-executes. This matches buyer intent:
> a retry after a 5xx should try again, not replay a failure. It also prevents a buyer's malformed
> request from being locked into a key for its full TTL."

**OBSERVED — storyboard `narrative` item 5 (verbatim):**
> "5. Error responses (returned envelopes, thrown envelopes, and uncaught exceptions) do not cache.
> The next request carrying the same key re-executes the handler. This applies to every recovery class,
> including terminal — terminal states in AdCP are mostly state-dependent (e.g., ACCOUNT_SUSPENDED flips
> after buyer remediation) and cached error replay would mask legitimate recovery. Handler authors must
> mutate state last: a handler that writes state then returns or throws an error envelope will
> double-write on retry. See security.mdx#idempotency rule 3 ('Only successful responses are cached') ..."

**OBSERVED — storyboard `key_reuse_conflict` reviewer check 3 (verbatim):**
> "Reviewer MUST confirm the seller's documentation states that errored requests release the idempotency
> claim, or manually probe the behavior (force a deterministic terminal error, then retry with the same
> key and a valid payload — the seller MUST return a fresh success, not IDEMPOTENCY_CONFLICT and not the
> cached error). See security.mdx#idempotency rule 3."

**Contract:** cache **successes only** (`status: completed`, or `submitted` for async). On ANY error
(validation, governance, transport, internal — every recovery class incl. terminal) the key is **NOT**
stored. Retry-after-error **re-executes the handler**. Handlers must mutate state last so an error after a
write doesn't double-write on retry. **This is the exact inverse of PR #1312** (which cached + replayed
rejections).

---

## C. `replayed` flag — placement, name, byte-for-byte

**OBSERVED — security.mdx rule 4 line 290 (verbatim):**
> "4. **Replay returns the cached response.** ... The seller injects `replayed: true` onto the outgoing
> protocol envelope at response time — `replayed` is an envelope-level field produced by the idempotency
> layer, NOT part of the cached inner response. Injection at replay time keeps the cached payload
> byte-stable across replays regardless of envelope changes (new `timestamp`, rotated
> `governance_context`, etc.). **Transport-specific note for MCP:** MCP tool responses do not have a
> separate envelope slot; servers MAY expose `replayed` inside the tool result object itself (e.g., at the
> top of the structured return) or via a response metadata field. **REST and A2A responses use the
> envelope field directly.**"

**OBSERVED — security.mdx "Response-level replay indicator" line 324-339 (verbatim):**
> "The protocol envelope carries a top-level `replayed` boolean on responses to mutating requests:"
> ```json
> { "status": "completed", "replayed": true, "timestamp": "...", "payload": { "media_buy_id": "..." } }
> ```
> "`replayed` is produced by the seller's idempotency layer at response time, not stored in the cache.
> On a fresh execution it is `false` (or omitted — buyers MUST treat omission as `false`). On a cached
> replay it is `true`; the inner `payload` is **byte-for-byte what was stored on the original successful
> execution**. Envelope fields (`timestamp`, `context_id`, etc.) may differ — they describe the current
> response, not the cached one."

**OBSERVED — SDK `ProtocolEnvelope.replayed`:** type `bool | None`, default `False`. Description:
> "Set to true when this response is a cached replay returned for an idempotency_key that was already
> processed. Set to false (or omitted) when the request was executed fresh. ... Only present on responses
> to mutating requests that carry idempotency_key."
The SDK also redeclares `replayed` on response models: `CreateCollectionListResponse`,
`CreatePropertyListResponse`, `DeleteCollectionListResponse`, `DeletePropertyListResponse`,
`ReportPlanOutcomeResponse`, `SyncPlansResponse`, `TaskResult`, `UpdateCollectionListResponse`,
`UpdatePropertyListResponse` (and `ProtocolEnvelope`). **Note: `CreateMediaBuyResponse` is a UnionType
in the SDK; `replayed` is carried by `ProtocolEnvelope` / `TaskResult` rather than a flat
`CreateMediaBuyResponse` field — verify per-tool when wiring.**

**OBSERVED — storyboard assertions:**
- Fresh exec (`create_media_buy_initial`, `create_media_buy_fresh_key`):
  `check: field_value_or_absent, path: "replayed", allowed_values: [false]` — "If reported on fresh
  execution, replayed must be false" (MAY be omitted; if present, not `true`).
- Replay (`create_media_buy_replay`): `check: field_value, path: "replayed", value: true`, AND
  `media_buy_id` MUST equal `$context.initial_media_buy_id`.

**Contract:** field name **`replayed`** (boolean). Placement: **top-level on the protocol envelope** for
REST/A2A; for **MCP** (no envelope slot) it MAY go at the top of the structured tool-result object or a
metadata field. Injected by the idempotency layer at response time, NOT stored in cache. Fresh = `false`
or omitted (buyers treat omission as `false`). Replay = `true`, and the inner `payload` is **byte-for-byte**
the original stored success (envelope fields like `timestamp`/`context_id` MAY differ).

---

## D. Conflict — same key + different canonical payload

**OBSERVED — security.mdx rule 5 line 291 (verbatim):**
> "5. **Key reuse with a different canonical payload is a conflict.** Same key, different canonical hash
> within the replay window MUST be rejected with `IDEMPOTENCY_CONFLICT`. Sellers MUST NOT silently apply
> the second request."

**OBSERVED — IDEMPOTENCY_CONFLICT response shape, security.mdx line 348-365 (verbatim):**
> "Standard AdCP error envelope. The error body:
> - MUST include `code: "IDEMPOTENCY_CONFLICT"` and a human-readable `message`
> - MUST NOT include the cached response, the original payload, a canonical-form diff, or any fingerprint
>   derived from them. A `field` json-pointer hint seems harmless but reveals schema shape ... Sellers
>   MUST NOT emit one. ..."
> ```json
> { "errors": [ { "code": "IDEMPOTENCY_CONFLICT",
>   "message": "idempotency_key was used with a different payload within the replay window. ...",
>   "recovery": "correctable" } ], "context": { "correlation_id": "..." } }
> ```

**OBSERVED — storyboard `key_reuse_conflict`:** `error_code allowed_values: ["IDEMPOTENCY_CONFLICT", "CONFLICT"]`
(CONFLICT is "accepted as a fallback for sellers that haven't adopted the new error code yet");
expected `recovery: correctable (buyer should use a fresh UUID v4)`. Reviewer checks forbid leaking
the cached payload / any fingerprint / any `field` pointer (read-oracle defense).

**HTTP status: NOT specified in spec prose or storyboard (see UNCERTAIN).**

**Recovery-class divergence (OBSERVED):**
- **Spec prose + storyboard:** `IDEMPOTENCY_CONFLICT` recovery = **`correctable`** (security.mdx line 361).
- **Installed SDK 4.3.0** `STANDARD_ERROR_CODES['IDEMPOTENCY_CONFLICT']` = **`{'recovery': 'terminal', ...}`**;
  same for `IDEMPOTENCY_EXPIRED` = `terminal`. (`uv run python -c "from adcp.server.helpers import STANDARD_ERROR_CODES ..."`.)
- Neither `IDEMPOTENCY_CONFLICT` nor `IDEMPOTENCY_EXPIRED` appears in `error-handling.mdx` (0 matches) —
  they are defined **only** in security.mdx.
- **This is the documented "SDK table diverges from spec" pattern** (reference_adcp_sdk_spec_mapping item 6).
  The spec intent is `correctable` (retry with a fresh UUID v4); the installed SDK classifies `terminal`.

**Contract:** same key + different canonical hash within TTL → `IDEMPOTENCY_CONFLICT` (storyboard accepts
`CONFLICT` as legacy fallback). Recovery per spec = `correctable`; the SDK table says `terminal`
(divergence — code matching the installed SDK should note "SDK table diverges from spec"). Error body
exposes **only** code + message; MUST NOT leak cached payload / diff / fingerprint / `field` pointer.

---

## E. Canonical equivalence — RFC 8785 JCS + closed exclusion list

**OBSERVED — security.mdx "Payload equivalence" line 302-322 (verbatim):**
> "'Equivalent' means **identical canonical JSON form**, not field-by-field semantic comparison. Sellers
> MUST determine equivalence by hashing the canonical form and comparing hashes. The canonical form is
> [RFC 8785 JSON Canonicalization Scheme (JCS)] — number serialization, key ordering, and escaping all
> follow JCS §3 normatively."
>
> "**Fields excluded from the hash** (closed list — sellers MUST NOT extend it):
> - `idempotency_key` — the key itself
> - `context` — buyer-opaque echo data (trace IDs, correlation IDs) changes on retry by design
> - `governance_context` — on the envelope; may be a refreshed signed token on retry
> - `push_notification_config.authentication.credentials` — may be a rotated bearer token. The URL and
>   scheme remain in the hash; only the credential value is excluded."
>
> "Everything else in the request body — including `ext` — is included, and 'missing optional field' is
> NOT equivalent to 'field explicitly set to null' (JCS preserves the distinction, and so does the hash).
> ... Sellers MUST NOT extend the exclusion list via capabilities, config, or extension — the list is
> fixed by this spec ... **Any future addition to the exclusion list is a breaking change** ..."

**OBSERVED — reference implementation, security.mdx line 315:**
> "**Reference implementation**: `SHA-256(JCS(payload - excluded_fields))`."
> Python libs cited: `pyjcs` or the RFC 8785 appendix reference impl (line 318).
> "AdCP SDK middleware ships JCS canonicalization so sellers don't roll their own." (line 322)

**OBSERVED — webhook-URL stability, security.mdx line 380 (verbatim):**
> "... buyers MUST NOT change `push_notification_config.url` between retries with the same key; URL is
> part of the canonical hash and rotating it triggers `IDEMPOTENCY_CONFLICT`. Rotate the key when
> changing webhook configuration."

**Contract:** equivalence = byte-identical **RFC 8785 JCS** canonical form, compared via hash
(`SHA-256(JCS(payload - excluded_fields))`). **Closed exclusion list (exactly 4, sellers MUST NOT extend):**
`idempotency_key`, `context`, `governance_context`, `push_notification_config.authentication.credentials`
(only the credential value — URL + scheme stay in the hash). Everything else (incl. `ext`) is in the hash;
null ≠ absent.

---

## F. Scope — per-(agent, account); cross-principal/tenant security

**OBSERVED — security.mdx line 281 (verbatim):**
> "Keys are scoped per `(authenticated agent, account)` — they have no meaning across agents on the same
> seller, across accounts under the same agent, or across sellers. Scoping by both dimensions prevents
> cross-account cache collisions when one agent (e.g. an agency) acts on multiple accounts: an
> identical-looking `create_media_buy` under account A and account B is two distinct buys, never one
> cached response replayed across the two."

**OBSERVED — cache key tuple, security.mdx rule 2 line 288:**
> "... keyed by `(authenticated_agent, account_id, idempotency_key)` along with a hash of the canonical
> request payload."

**OBSERVED — `si_send_message` narrower scope, security.mdx line 372 (verbatim):**
> "`si_send_message` needs a narrower scope ... The key is scoped
> `(authenticated_agent, account_id, session_id, idempotency_key)`."

**OBSERVED — cross-scope oracle defense, security.mdx line 397 (verbatim):**
> "The three-state response (`success` / `IDEMPOTENCY_CONFLICT` / `IDEMPOTENCY_EXPIRED`) is an existence
> oracle for idempotency keys. ... The per-`(agent, account)` scoping above is the primary defense — an
> attacker authenticated as agent A cannot probe agent B's keys, and a caller scoped to account A cannot
> probe account B's keys even under a shared agent credential. ... **Sellers MUST NOT surface
> `IDEMPOTENCY_EXPIRED` across scope boundaries or to unauthenticated callers.** Sellers SHOULD also avoid
> distinguishable timing between 'key exists' and 'key does not exist' lookups ..."

**OBSERVED — storyboard `invariants` + cross-step assertions:** `idempotency.conflict_no_payload_leak`
(catches stolen-key read oracle) and `context.no_secret_echo` (credential echo). Storyboard security
note: harness MUST mint fresh UUID v4 per RUN; the compliance principal SHOULD be a sandbox account
whose idempotency cache is isolated per run.

**Contract:** scope is **`(authenticated_agent, account_id, idempotency_key)`** (+ `session_id` for
`si_send_message`). Note the spec's terminology: **agent** = the software placing the call, **account** =
the billing relationship — NOT "principal" (security.mdx retired "principal", CHANGELOG line 272). Keys
have no meaning across agents, across accounts, or across sellers. Cross-scope security: never surface
the conflict/expired oracle across scope boundaries or to unauthenticated callers; per-(agent,account)
scoping is the primary read-oracle defense.

---

## G. `replay_ttl_seconds` — bounds + capability advertisement shape

**OBSERVED — security.mdx rule 7 line 295 (verbatim):**
> "7. **Replay window is declared, not inferred.** Sellers MUST declare
> `capabilities.idempotency.replay_ttl_seconds` on `get_adcp_capabilities` (minimum 3600s / 1h,
> recommended 86400s / 24h, maximum 604800s / 7d). Clients MUST NOT fall back to an assumed default — a
> seller with no declaration is non-compliant and MUST be treated as unsafe for retry-sensitive
> operations."

**OBSERVED — durability is normative, security.mdx rule 6 sub-clause line 294 (verbatim, abridged):**
> "**Durability is normative.** The declared `replay_ttl_seconds` is a durability contract, not a
> best-effort cache hint. Sellers MUST back the idempotency cache with storage that survives process
> restarts, pod replacements, region failovers, and operator-initiated cache flushes for the declared
> TTL. In-memory-only stores ... are non-conformant whenever `replay_ttl_seconds` exceeds process
> lifetime — which is always true at the 3600 s floor. ... Sellers MUST NOT declare a
> `replay_ttl_seconds` higher than their cache tier can durably honor, and MUST fail-closed
> (`IDEMPOTENCY_EXPIRED`) rather than fail-open (silent re-execution) when they cannot distinguish 'never
> seen' from 'evicted under declared TTL.'"

**OBSERVED — capability `oneOf` shape — SDK `GetAdcpCapabilitiesResponse` `$defs`:**
- `Idempotency` (supported:true variant): `properties.supported.const = true`;
  `properties.replay_ttl_seconds` → `type: integer, minimum: 3600, maximum: 604800`;
  **`required: ['supported', 'replay_ttl_seconds']`**. (Also a `account_id_is_opaque` optional bool field.)
- `Idempotency1` (supported:false variant): `properties.supported.const = false`; `required: ['supported']`.
- Discriminated `oneOf` on `supported` (CHANGELOG line 216: "formalize seller declaration as discriminated oneOf").

**OBSERVED — storyboard `capability_discovery` step:**
> validations require `adcp.idempotency.supported == true` and `field_present: adcp.idempotency.replay_ttl_seconds`;
> expected text: "adcp.idempotency with supported: true and replay_ttl_seconds (minimum 3600s; recommended
> 86400s or longer)". Sellers declaring `supported: false` MUST skip this storyboard.

**Contract:** advertised under `get_adcp_capabilities` → `adcp.idempotency` as a discriminated `oneOf`:
either `{ supported: true, replay_ttl_seconds: <int> }` (TTL **required**, **min 3600 / rec 86400 / max
604800**) or `{ supported: false }`. Clients MUST NOT assume a default; missing block = non-compliant.
The TTL is a **durability contract** (must survive restarts/failovers).

---

## H. IDEMPOTENCY_EXPIRED is OPTIONAL / conditional (the in-scope decision the mission confirms)

**OBSERVED — security.mdx rule 6 line 292 (verbatim) — MAY evict / SHOULD reject:**
> "6. **Expired keys are rejected explicitly.** After `replay_ttl_seconds` elapses the seller **MAY**
> evict the cache entry. A request arriving after eviction with a key the seller has seen **SHOULD** be
> rejected with `IDEMPOTENCY_EXPIRED` rather than silently treated as new — silent re-execution is exactly
> the double-booking footgun the key was meant to prevent. Sellers SHOULD allow a ±60s clock-skew window
> at the TTL boundary ..."

**OBSERVED — security.mdx rule 6 durability sub-clause line 294 (verbatim, the conditional):**
> "... MUST fail-closed (`IDEMPOTENCY_EXPIRED`) rather than fail-open (silent re-execution) **when they
> cannot distinguish 'never seen' from 'evicted under declared TTL.'**"

**OBSERVED — SDK `replay_ttl_seconds` description (the code-generated-from-spec type, verbatim) — the
clearest "optional" language:**
> "... a replay past the window returns IDEMPOTENCY_EXPIRED **when the seller can still distinguish 'seen
> and evicted' from 'never seen'**. Minimum 3600 (1h); recommended 86400 (24h). Maximum 604800 (7 days) ..."
(`uv run python -c "... GetAdcpCapabilitiesResponse ... $defs['Idempotency'] ..."`.)

**OBSERVED — storyboard does NOT drive an EXPIRED phase:** the `narrative` and `key_reuse_conflict`
section note an end-to-end terminal-error/expiry phase is **deferred** ("deferred pending a generic
force-error controller verb (see adcontextprotocol/adcp#2760)"). There is **no `IDEMPOTENCY_EXPIRED`
step in the executable storyboard** — it is not a graded conformance gate at 3.0.1.

**Reconciliation of the apparent tension (INFERRED, from the verbatim quotes above):** the keywords are
`MAY evict` + `SHOULD reject with IDEMPOTENCY_EXPIRED`, conditioned on **"when the seller can still
distinguish 'seen and evicted' from 'never seen'."** A seller that **cannot** make that distinction (e.g.,
a pure TTL-based store with no tombstone of evicted keys) is **not** required to emit `IDEMPOTENCY_EXPIRED`
— for it, an expired/evicted entry legitimately becomes a **cache miss → re-execute**. The single hard
MUST is **fail-closed vs fail-open ONLY when you cannot distinguish** — i.e., the prohibition is on
*declaring a TTL you cannot durably honor*, not on omitting EXPIRED per se.
**This is consistent with treating expired entries as a cache miss when the seller doesn't retain
evicted-key tombstones**, which is the design decision already made for the rebuild. The conformance
storyboard graders for 3.0.1 do not test EXPIRED, so omitting it does not fail conformance.

**Contract:** `IDEMPOTENCY_EXPIRED` is **OPTIONAL / conditional** — `MAY evict`, `SHOULD reject` only
"when the seller can still distinguish 'seen and evicted' from 'never seen'." A seller without evicted-key
tombstones may treat expired → cache miss → re-execute. The storyboard does not grade EXPIRED (deferred,
#2760). **Decision to put EXPIRED out of scope is spec-permitted.** (Caveat: this is paired with the
durability MUST — the project's declared `replay_ttl_seconds` must be one its store can durably honor for
that window; choosing a short, durable TTL keeps "expired = miss = re-execute" honest.)

---

## I. Rate limiting — `RATE_LIMITED` on per-(agent,account) cache inserts

**OBSERVED — security.mdx rule 8 line 296 (verbatim):**
> "8. **Cache-growth defense.** Sellers MUST apply per-`(authenticated_agent, account)` rate limits on
> idempotency cache inserts separately from request rate limits, and **MUST return `RATE_LIMITED`** ...
> when the per-agent insert rate exceeds the configured ceiling rather than let the cache grow unbounded.
> ... The natural bound is `inserts_per_hour × replay_ttl_hours ≤ max_cache_rows_per_agent`."

**OBSERVED — recommended ceiling, security.mdx line 298 (verbatim, abridged):**
> "**Recommended ceiling: 60 inserts/sec per agent sustained (3,600/min), with burst allowance up to 300
> inserts/sec over rolling 10-second windows.** ... **The numeric recommendations are SHOULD-level; the
> rate-limit-and-reject-with-`RATE_LIMITED` behavior itself is MUST.** Sellers MUST expose the ceiling as a
> tunable configuration parameter — the 60/300/3,600 values are first-deployment starting points ...
> Sellers SHOULD NOT publish their exact configured ceiling numerically in capability responses ..."

**OBSERVED — security.mdx line 300 (verbatim, abridged):**
> "The ceiling is per `(authenticated_agent, account)` ... `RATE_LIMITED` rejections MUST populate
> `retry_after` (seconds) ... and MUST NOT be cached as idempotency responses (rule 3: only successful
> responses are cached)."

**OBSERVED — storyboard `narrative` cache-growth-defense note:**
> "**Cache-growth defense (MUST, not yet runtime-graded).** ... sellers MUST apply per-agent rate limits
> on idempotency-cache inserts and MUST return RATE_LIMITED ... This storyboard **does not yet drive the
> high-volume burst** that would exercise the ceiling — runtime grading is tracked as a follow-up ...
> Sellers SHOULD self-attest to this requirement until the runtime phase lands."

**OBSERVED — `RATE_LIMITED` recovery class:** `transient` in both error-handling.mdx (line 211) and the
installed SDK (`STANDARD_ERROR_CODES['RATE_LIMITED'] = {'recovery': 'transient', ...}`). Requires
`retry_after`.

**Contract:** the **behavior is MUST** — per-(agent,account) rate limit on cache *inserts*, separate from
request rate limits, returning **`RATE_LIMITED`** (recovery `transient`, with `retry_after`) when exceeded;
RATE_LIMITED responses are NOT cached. The **numeric ceilings are SHOULD-level** (60/sec sustained, burst
300/sec over 10s windows, 3 600/min) and MUST be a tunable config param (not published numerically in
capabilities). It is **NOT a graded conformance gate at 3.0.1** (storyboard defers runtime grading; "SHOULD
self-attest"). **INFERRED scope note:** for this rebuild, RATE_LIMITED is a real MUST in the spec but
untested by the runner; whether to implement now is a project scope call, not a conformance blocker.

---

## J. Which tools the contract applies to

**OBSERVED — security.mdx line 281 (the explicit list, verbatim):**
> "`create_media_buy`, `update_media_buy`, `sync_creatives`, `activate_signal`, `acquire_rights`,
> `creative_approval`, `update_rights`, `build_creative`, `calibrate_content`, `create_content_standards`,
> `update_content_standards`, `create_property_list`, `update_property_list`, `delete_property_list`,
> `create_collection_list`, `update_collection_list`, `delete_collection_list`, `log_event`,
> `provide_performance_feedback`, `report_usage`, `report_plan_outcome`, `si_initiate_session`,
> `si_send_message`, and the `sync_*` tasks"

**OBSERVED — out of scope, security.mdx line 283:**
> "This section applies only to AdCP task requests. OpenRTB bid streams have their own semantics ... and
> are out of scope."

**OBSERVED — read-only ops exempt, security.mdx line 389:**
> "Read-only operations (`get_products`, `list_accounts`, etc.) are safe to issue against such a seller;
> only mutating requests require the declaration."

**OBSERVED — storyboard uses `create_media_buy` as canonical:** prerequisites note "Sellers that do not
support create_media_buy SHOULD still pass idempotency compliance on whichever mutating task they do
implement." The storyboard's graded mutating task is `create_media_buy`; it also calls `get_media_buys`
(read) for the dedup-count verification.

**Contract:** applies to **all mutating AdCP task requests** — the explicit closed list above plus all
`sync_*` tasks. Read-only tools (`get_products`, `list_accounts`, `get_media_buys`, etc.) are exempt.
OpenRTB bid streams out of scope. The conformance storyboard exercises it via `create_media_buy`, but the
obligation is on every mutating task the seller implements.

---

## UNCERTAIN / COULD NOT VERIFY

1. **HTTP status code for `IDEMPOTENCY_CONFLICT` (and EXPIRED).** Neither security.mdx, the storyboard,
   nor error-handling.mdx specifies an HTTP status. The CONFLICT example uses the AdCP error envelope
   (`errors[].code`), not an HTTP-status assertion. (INFERRED, not confirmed: AdCP errors are carried in
   the body envelope; the spec appears transport-status-agnostic for these codes. Do not assert a specific
   HTTP code without further grounding.) **COULD NOT VERIFY.**
2. **Recovery-class for `IDEMPOTENCY_CONFLICT`/`EXPIRED` — spec vs SDK conflict (OBSERVED but unresolved
   which the rebuild should emit).** Spec prose + storyboard say `correctable`; installed SDK 4.3.0
   `STANDARD_ERROR_CODES` says `terminal`. Both observed and quoted above. The rebuild must choose; per
   reference_adcp_sdk_spec_mapping item 7, a subclass's `_default_error_code` is hard-asserted against the
   SDK's standard set, and the SDK is the local source of truth for the *code's existence*, while the spec
   is the source of truth for *intended recovery*. **Flagging as a decision point, not resolving it here.**
3. **`replayed` exact placement for `CreateMediaBuyResponse` specifically.** The spec says envelope-level
   (REST/A2A) or top-of-tool-result (MCP). The SDK carries `replayed` on `ProtocolEnvelope` and several
   response models, but `CreateMediaBuyResponse` is a `UnionType` and I did not enumerate every union arm
   for a flat `replayed`. **Verify per-tool when wiring** (OBSERVED that `ProtocolEnvelope`/`TaskResult`
   carry it; the per-tool union arm is UNVERIFIED).
4. **Full schema JSON for the `adcp.idempotency` block at the wire level.** The 3.0.1-tag protocol/schema
   artifacts are packaged as `.tgz` (`dist/protocol/3.0.1.tgz`, `dist/schemas/...`), which I did not unpack;
   I used the **installed SDK 4.3.0 generated models** as the proxy (they are generated from the same spec
   version and verified to report spec 3.0.1). The SDK shape (`Idempotency` / `Idempotency1` discriminated
   oneOf, required `[supported, replay_ttl_seconds]`, min 3600/max 604800) is OBSERVED from the SDK; the raw
   schema JSON at the tag is INFERRED-equivalent but not byte-verified against the tarball.
5. **WebFetch/curl/`gh api contents` 404 on the security.mdx blob path** in this environment. Content was
   retrieved instead via the **git blobs API by SHA** (`gh api .../git/blobs/<sha>`), which returned the
   full file (199 567 bytes, decoded; line numbers cited match that decode). The 404 is an
   environment/path quirk, not evidence the file is absent — its blob SHA and size were independently
   confirmed via `gh api .../contents?ref=...`.
6. **`activate_signal` / SI-specific nuances** (e.g., `si_send_message` session-scoped replay, the
   `SESSION_NOT_FOUND`-or-`IDEMPOTENCY_EXPIRED` fallback when session state advanced) are OBSERVED in prose
   (lines 370-376) but out of the immediate rebuild's likely scope (create_media_buy-centric). Captured for
   completeness; not deeply analyzed.
