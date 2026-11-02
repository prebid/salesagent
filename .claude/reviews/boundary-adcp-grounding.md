# AdCP Spec-Grounding Review — the `_boundary.py` seam

**Scope**: `src/core/tools/_boundary.py`, `src/core/idempotency_replay.py`,
`src/core/idempotency_canonical.py`, `src/core/database/repositories/idempotency_attempt.py`,
`src/core/tools/media_buy_create.py` (race/degraded paths), and the three transport entry
points (`src/core/main.py::_call_tool`, `src/routes/api_v1.py::_rest_handler`,
`src/a2a_server/adcp_a2a_server.py`), on `feature/spec-gaps-1210`.
**Pinned version**: `adcp==6.6.0` → AdCP 3.1.1. Prose read from
`repos/adcontextprotocol/adcp:docs/building/by-layer/L1/security.mdx` (§Request Safety →
Idempotency, rules 1–10, "Payload equivalence", "Server-side tool wrapper conformance",
"Response-level replay indicator"). Schemas read from the vendored
`.venv/lib/python3.12/site-packages/adcp/_schemas/3.1/`. Graded storyboards read from
`repos/adcontextprotocol/adcp:static/compliance/source/universal/{idempotency,read-tool-idempotency}.yaml`.
**Date**: 2026-09-06

## Verdict on the compliance claim

**Partly upheld.** The seam is more conformant than what it replaced on three axes I
verified (SG-C1…C3 below). It is not more conformant unqualifiedly: it extends the replay
contract from one tool to four, and three of the four newly-keyed tools replay *without the
spec's `replayed` marker* and *without any dup-execution backstop*. Two of the findings
below (SG-01, SG-03) are newly reachable precisely because of the extension.

## Findings

### SG-01: three of the four keyed tools replay with no `replayed` marker; `update_media_buy` emits `replayed: false` on a replay
- **Severity**: Critical
- **Rule**: SG-2 (pinned schema is authoritative over the local DTO)
- **File**: `src/core/tools/_boundary.py:138-141`; `src/core/schemas/_base.py:881-895` (`TaskResultEnvelope`, no `replayed`) vs `:916` (`CreateMediaBuyResult.replayed`); `src/core/schemas/creative.py` (`SyncCreativesResponse`); `src/core/schemas/account.py` (`SyncAccountsResponse`)
- **Spec ref**: security.mdx rule 4 — *"The seller injects `replayed: true` onto the outgoing protocol envelope at response time"*; §Response-level replay indicator — *"The protocol envelope carries a top-level `replayed` boolean on responses to any request that resolved via the idempotency cache."* Schema: `_schemas/3.1/core/protocol-envelope.json#/properties/replayed`, composed via `allOf[1]` ("Protocol Envelope") into `bundled/media-buy/update-media-buy-response.json`, `bundled/creative/sync-creatives-response.json` and `bundled/media-buy/create-media-buy-response.json`.
- **Description**: `_deserializer_for` injects the marker only when `"replayed" in model.model_fields`, and the model it consults is the *outer* response model. Only `CreateMediaBuyResult` declares the field. So `update_media_buy`, `sync_creatives` and `sync_accounts` — all three newly enrolled in the cache by this branch — replay a stored payload with no marker at all. `update_media_buy` is worse than silent: `UpdateMediaBuySubmitted` (the SDK parent) *does* declare `replayed: bool = False`, and `TaskResultEnvelope._serialize` flattens the inner response without the `result.pop("replayed", None)` / re-set that `CreateMediaBuyResult._serialize` performs — so a replayed submitted update goes onto the wire carrying `replayed: false`, positively asserting a fresh execution. This is the same on every transport: REST returns `response.model_dump(mode="json")` directly and A2A/MCP serialize the same model; there is no separate envelope slot that could add it.
- **Buyer-facing consequence**: the spec names four buyer uses of the marker, and all four break. An agent doing side-effect suppression ("Campaign updated!" notifications, LLM memory writes, downstream tool calls) re-emits on every retry; billing reconciliation counts a replay as a new execution; a buyer routing on state-tracking fields treats a historical snapshot as a current-state read. On `update_media_buy` the buyer is not merely uninformed, it is misinformed.
- **Reproduction**:
  ```
  uv run python -c "from src.core.tools.registry import TOOLS; from src.core.tools._boundary import _response_model_for, _spec_declares_idempotency_key; print([(n, _response_model_for(s.impl).__name__, 'replayed' in _response_model_for(s.impl).model_fields) for n,s in TOOLS.items() if _spec_declares_idempotency_key(s.dto)])"
  # [('sync_accounts','SyncAccountsResponse',False), ('create_media_buy','CreateMediaBuyResult',True),
  #  ('update_media_buy','UpdateMediaBuyResult',False), ('sync_creatives','SyncCreativesResponse',False)]

  uv run python -c "from src.core.schemas._base import UpdateMediaBuyResult, UpdateMediaBuySubmitted; print(UpdateMediaBuyResult(response=UpdateMediaBuySubmitted(task_id='t',status='submitted'),status='submitted').model_dump(mode='json'))"
  # {'task_id': 't', 'status': 'submitted', 'replayed': False}   <-- on a replay this stays False

  python3 -c "import json;d=json.load(open('.venv/lib/python3.12/site-packages/adcp/_schemas/3.1/bundled/media-buy/update-media-buy-response.json'));print([s['title'] for s in d['allOf'] if 'replayed' in s.get('properties',{})])"
  # ['Protocol Envelope']
  ```
- **Recommended fix**: declare `replayed: bool = False` on `TaskResultEnvelope` (covering `UpdateMediaBuyResult`) and on `SyncCreativesResponse` / `SyncAccountsResponse`, citing `_schemas/3.1/core/protocol-envelope.json#/properties/replayed`; move `CreateMediaBuyResult`'s pop-and-reset serializer up to `TaskResultEnvelope` so the wrapper is the single source on both result types. `tests/integration/test_sync_creatives_idempotency.py` currently asserts nothing about `replayed` — add the wire assertion there and for `sync_accounts` / `update_media_buy`, on every transport, matching `test_idempotency_wire_matrix.py:73-99`.

### SG-02: hashing the validated model drops unknown fields in production, so genuinely different payloads replay instead of conflicting
- **Severity**: High
- **Rule**: SG-2
- **File**: `src/core/idempotency_canonical.py:74`; called at `src/core/tools/_boundary.py:252`
- **Spec ref**: security.mdx §Payload equivalence — *"'Equivalent' means **identical canonical JSON form**, not field-by-field semantic comparison. Sellers MUST determine equivalence by hashing the canonical form and comparing hashes."* and *"Everything else in the request body — including `ext` — is included, and 'missing optional field' is NOT equivalent to 'field explicitly set to null'."* Rule 5 — *"Same key, different canonical hash within the replay window MUST be rejected with `IDEMPOTENCY_CONFLICT`. Sellers MUST NOT silently apply the second request."*
- **Description**: `canonical_request_hash` hashes `req.model_dump(mode="json")`. `TestHashInputIsTheValidatedRequest` (`tests/integration/test_idempotency_wire_matrix.py:290-315`) already records this as a known divergence and argues it is "ENCODING only" and "in the SAFE direction". **That analysis is incomplete on the axis that matters.** Under `ENVIRONMENT=production` every request DTO runs `extra="ignore"` (`src/core/config.py:172-178`), and every published AdCP request schema declares `additionalProperties: true` — so a forward-compatible field a 3.2 buyer legitimately sends is *dropped by pydantic before the hash is taken*. Two requests whose canonical JSON genuinely differs then hash equal. Same root cause, second instance: an omitted optional field and the same field explicitly sent at its default (`dry_run: false`) collapse to one hash, where JCS-over-the-wire distinguishes them.
- **Buyer-facing consequence**: a buyer reuses one key across two different requests and receives the *first* request's response with no error. It is not the double-execution hazard, but rule 5 exists so the buyer learns their key management is wrong; here they silently do not. On `create_media_buy` and `update_media_buy` the buyer believes the second request took effect.
- **Reproduction**:
  ```
  ENVIRONMENT=production uv run python -c "
  from src.core.schemas.account import SyncAccountsRequest
  from src.core.idempotency_canonical import canonical_request_hash, canonical_payload_hash
  base = dict(idempotency_key='k'*20, accounts=[])
  a = SyncAccountsRequest(**base, some_future_field='A'); b = SyncAccountsRequest(**base, some_future_field='B')
  print('ours  :', canonical_request_hash(a) == canonical_request_hash(b))   # True  -> replays
  print('spec  :', canonical_payload_hash({**base,'some_future_field':'A'}) == canonical_payload_hash({**base,'some_future_field':'B'}))  # False -> MUST conflict
  print('default collapse:', canonical_request_hash(SyncAccountsRequest(**base)) == canonical_request_hash(SyncAccountsRequest(**base, dry_run=False)))  # True; spec: False
  "
  ```
- **Recommended fix**: capture the received body once, at the same seam that already normalizes the envelope (`RequestCompatMiddleware` / the REST body dependency / the A2A params dict), and hand `canonical_payload_hash(body)` to `invoke` alongside the DTO. That is one value threaded from one place, not the per-transport threading that produced #2214 — the defect there was that *some* transports computed it, not that a body was captured. Failing that, correct `TestHashInputIsTheValidatedRequest`'s docstring: the divergence is not encoding-only, and `test_a_genuinely_different_payload_still_conflicts` only holds for *declared* fields.

### SG-03: rule 9 (first-insert-wins) is not implemented; the second concurrent request executes the side effect
- **Severity**: High
- **Rule**: SG-1 (no citation for the chosen behavior) / SG-3 (an on-wire error code the implementation never emits)
- **File**: `src/core/idempotency_replay.py:87-121` (the only row is written *after* success); `src/core/tools/media_buy_create.py:3684-3693` and `:1987-2020`; `src/core/database/models.py:1287-1299` (the `media_buys` backstop index)
- **Spec ref**: security.mdx rule 9 — *"Sellers MUST resolve the race deterministically; they MUST NOT execute the side effect twice and MUST NOT silently drop the second request. Resolution is a `(unique constraint, INSERT … ON CONFLICT DO NOTHING)` pattern on the scope tuple: the first row to land owns execution and stores the canonical payload hash on the in-flight row (NOT a sentinel)"*, and the two permitted policies (wait-and-replay, or `IDEMPOTENCY_IN_FLIGHT` with `error.retry_after`).
- **Description**: `IdempotencyAttemptRepository.record_success` inserts only on the success path — there is no in-flight row, which is a weaker position than the sentinel pattern the spec explicitly forbids. The second concurrent request misses the cache and runs the implementation to completion. For `create_media_buy` the adapter has already returned by the time the `media_buys` partial unique index fires (`media_buy_create.py:3665-3684`), so the downstream order is created twice — the code says so itself: *"An orphan adapter-side order may exist."* The wire answer is `SERVICE_UNAVAILABLE` with `retry_after=1`, not `IDEMPOTENCY_IN_FLIGHT`, which is in the pinned enum and never used anywhere in `src/`. For the three tools this branch newly enrolled there is no backstop index at all, so both concurrent syncs/updates apply, both return fresh responses, and the loser's cache write is swallowed as an `IntegrityError` at `idempotency_replay.py:122-130`. Same-key-different-payload during the in-flight window is also not detectable at INSERT time, which rule 9's final paragraph makes a named non-conformance.
- **Buyer-facing consequence**: a buyer whose transport timeout fires and who retries with the same key — the exact scenario rule 9 is written for — double-books a media buy at the ad server, or applies a creative sync twice. Rule 10 conformance (write-claim-before-invoke / thread-buyer-key) is absent for the same reason; it is reviewer-graded, and there is no runbook to grade.
- **Reproduction**:
  ```
  grep -rn "IDEMPOTENCY_IN_FLIGHT" src/ | wc -l    # 0
  python3 -c "import json;print([c for c in json.load(open('.venv/lib/python3.12/site-packages/adcp/_schemas/3.1/enums/error-code.json'))['enum'] if 'IDEMP' in c])"
  # ['IDEMPOTENCY_CONFLICT', 'IDEMPOTENCY_EXPIRED', 'IDEMPOTENCY_IN_FLIGHT']
  grep -n "record_success\|response_envelope" src/core/database/repositories/idempotency_attempt.py   # written only with a completed envelope
  python3 -c "
  import re;s=open('src/core/database/models.py').read()
  print('media_buys backstop:', 'idx_media_buys_idempotency_key' in s)
  print('creatives/accounts backstop:', bool(re.search(r'idx_(creatives|accounts)_idempotency', s)))"
  ```
- **Recommended fix**: reserve the scope row *before* calling the implementation — `INSERT (tenant, principal, account, key, payload_hash, status='in_flight') ON CONFLICT DO NOTHING`, with `response_envelope` NULL and the real canonical hash present at INSERT time; on conflict, compare hashes (→ `IDEMPOTENCY_CONFLICT`) then apply one declared policy (`AdCPIdempotencyInFlightError` with `retry_after`, or wait-and-replay). Release the row on failure and on handler timeout per rule 9's last paragraph, and declare `capabilities.idempotency.in_flight_max_seconds` (`src/core/idempotency_policy.py:104` already carries the field and leaves it `None`). The `_raise_degraded_replay_outcome` ladder then becomes the fallback it is described as, rather than the whole race resolution.

### SG-04: cacheability is a failure denylist where the spec gives a success allowlist
- **Severity**: Medium
- **Rule**: SG-3 (on-wire enum values)
- **File**: `src/core/tools/_boundary.py:152` (`_FAILED_STATUSES`), `:155-168`
- **Spec ref**: security.mdx rule 2 — *"On **task success** (`status: completed` or `status: submitted` for async operations), the seller stores the inner response payload"*; rule 3 — *"Only successful responses are cached."* Pinned enum `_schemas/3.1/enums/task-status.json` lists nine values.
- **Description**: `_FAILED_STATUSES` names four (`failed`, `rejected`, `canceled`, `unknown`); anything else is cached. That leaves `working`, `input-required` and `auth-required` — three non-terminal, non-success statuses — on the cacheable side. The inline comment argues a denylist because "a status the spec adds later is far more likely to be another non-terminal or another failure than a new success"; the spec's own framing is the opposite shape, and the three statuses it already defines that the denylist misses are exactly the "another non-terminal" case the comment predicts. This is latent today (I traced all `CreateMediaBuyResult` / `UpdateMediaBuyResult` construction sites in `media_buy_create.py` and `media_buy_update.py`: only `completed`, `submitted`, `failed` are produced), so I have no buyer-facing reproduction — but it is new code in this diff and it is graded by nothing.
- **Buyer-facing consequence** (if reached): an `auth-required` or `input-required` response is frozen into the key for 24h; the buyer authenticates or supplies the input, retries with the same key per rule 9's instruction, and replays the blocked response.
- **Reproduction**:
  ```
  python3 -c "import json;print(json.load(open('.venv/lib/python3.12/site-packages/adcp/_schemas/3.1/enums/task-status.json'))['enum'])"
  # ['submitted','working','input-required','completed','canceled','failed','rejected','auth-required','unknown']
  uv run python -c "from src.core.tools._boundary import _FAILED_STATUSES; print(sorted({'submitted','working','input-required','completed','canceled','failed','rejected','auth-required','unknown'} - _FAILED_STATUSES - {'completed','submitted'}))"
  # ['auth-required', 'input-required', 'working']
  ```
- **Recommended fix**: invert to the spec's allowlist — cache iff `status in {"completed", "submitted"}` or the response is not a task envelope — and cite `security.mdx` rule 2. The comment's real concern (a new status silently not cached) is answered by asserting the allowlist against `enums/task-status.json` in a unit test, which makes an added status loud rather than silent. The `sync_creatives` case the brief asked about is separate and **conformant**: rule 2 explicitly names *"per-record `status` arrays on `sync_creatives` / `sync_accounts`"* as state-tracking fields that MUST replay from cache, so a success carrying per-item failures should be and is cached. `SyncCreativesResponse` has no top-level `errors` field, so the "success status with populated `errors[]`" shape is unrepresentable there; on `CreateMediaBuySuccess` / `UpdateMediaBuySuccess` an `errors[]` alongside `status: completed` is advisory and correctly cacheable.

### SG-05: a cached row that no longer validates degrades to silent re-execution of a mutation
- **Severity**: Medium
- **Rule**: SG-1
- **File**: `src/core/tools/_boundary.py:135-137` (returns `None` on validation failure), consumed as a miss at `src/core/idempotency_replay.py:84` and `_boundary.py:262-264`
- **Spec ref**: security.mdx rule 6, durability paragraph — *"Sellers … MUST fail-closed (`IDEMPOTENCY_EXPIRED`) rather than fail-open (silent re-execution) when they cannot distinguish 'never seen' from 'evicted under declared TTL.'"*
- **Description**: when the response model changes between the deploy that wrote a cache row and the one replaying it inside the TTL, `_deserializer_for` logs a warning and returns `None`, and the boundary re-executes. The seller here *can* distinguish — the row is in hand — and chooses fail-open anyway. For `create_media_buy` the `media_buys` unique index converts this into `IDEMPOTENCY_EXPIRED` via `_raise_degraded_replay_outcome`; for `update_media_buy`, `sync_creatives` and `sync_accounts` nothing catches it and the mutation runs a second time. The related post-TTL case has the same asymmetry: `expire_old` (`idempotency_attempt.py:230-250`) DELETEs expired rows, so a key the seller has seen returns to "never seen" and re-executes silently, where rule 6 says it SHOULD be `IDEMPOTENCY_EXPIRED`.
- **Buyer-facing consequence**: a routine deploy inside the replay window turns a safe retry into a second sync / second update.
- **Reproduction**:
  ```
  sed -n '127,144p' src/core/tools/_boundary.py     # except -> log -> return None
  sed -n '73,85p'  src/core/idempotency_replay.py   # None from deserialize is indistinguishable from a miss
  sed -n '230,250p' src/core/database/repositories/idempotency_attempt.py  # expire_old DELETEs
  ```
- **Recommended fix**: distinguish "no row" from "row present, unrevivable" — raise `AdCPIdempotencyExpiredError` for the latter, which is the fail-closed answer rule 6 names and is already the answer `create_media_buy` gives via its backstop. For eviction, keep a tombstone (or stop deleting and rely on the read-path TTL filter) so "seen but expired" stays answerable for the three tools that have no `media_buys` equivalent.

### SG-06: three docstrings state the grounding incorrectly
- **Severity**: Low
- **Rule**: SG-1
- **Files**: `src/core/tools/_boundary.py:31-33` and `:199-200`; `src/core/idempotency_canonical.py:23-27`; `src/core/schemas/account.py:141-144`
- **Spec ref**: security.mdx §Payload equivalence (closed list of **four**, one nested); `adcp._idempotency.IDEMPOTENT_TASKS`
- **Description**: (a) `_boundary.py:31-33` says `canonical_request_hash` "strips the spec's closed exclusion list (`idempotency_key`, `context`, `governance_context`)" — the spec's closed list has four entries; the fourth, `push_notification_config.authentication.credentials`, *is* stripped (by the SDK's `_NESTED_EXCLUSIONS`), so the code is right and the docstring undercounts the contract it cites. (b) `_boundary.py:199-200` says `IDEMPOTENT_TASKS` "lists exactly the four tools this predicate selects"; it lists 29 — the *intersection* with this repo's registry is the four, which is what the test at `tests/unit/test_boundary_idempotency_rules.py:107-112` correctly asserts with `<=`. (c) `idempotency_canonical.py:23-27` says "transport wrappers thread the raw wire payload to `canonical_payload_hash`, the spec's equivalence input" — this branch deleted every such wrapper; `canonical_payload_hash` now has no production caller through a transport, which is the whole substance of SG-02 and the docstring reads as though it were still covered. (d) `account.py:141-144` argues `sync_accounts` "still does not consume it (only media_buy_create and creatives/_sync reach idempotency_replay)"; the boundary consumes it for `sync_accounts` as of this branch.
- **Recommended fix**: correct all four in place; (c) is the one that matters, since it is the only place a reader is told where the spec's equivalence input is taken.

### SG-07: two rule-6/rule-9 SHOULDs are unimplemented
- **Severity**: Low
- **Rule**: SG-1
- **File**: `src/core/database/repositories/idempotency_attempt.py:118-124`; `src/core/idempotency_policy.py:104`
- **Spec ref**: rule 6 — *"Sellers SHOULD allow a ±60s clock-skew window at the TTL boundary … so that a retry arriving seconds after nominal expiry is still replayed from cache rather than treated as fresh."* Rule 9 — *"Sellers SHOULD also declare `capabilities.idempotency.in_flight_max_seconds`."*
- **Description**: `find_by_key` filters `expires_at > now` with no tolerance, so a retry one second past nominal expiry re-executes instead of replaying — the same fail-open SG-05 describes, reached by clock skew rather than by deploy. `get_idempotency_posture` returns `in_flight_max_seconds=None`; that is honest while SG-03 stands, and should be declared with the in-flight row.
- **Recommended fix**: subtract 60s from `current` in `find_by_key`'s predicate (the model already carries the bound-checking machinery); declare `in_flight_max_seconds` as part of the SG-03 fix.

## What I checked and found conformant

Named explicitly, because the compliance claim rests on them.

- **SG-C1 — Rule 1 ordering, on all three transports.** *"Sellers MUST validate the request against its schema … BEFORE consulting the idempotency cache."* MCP builds the DTO at `src/core/main.py:445` before calling `invoke`; REST's body model *is* the DTO (`api_v1.py:44-58`), re-validated after the path merge at `:84`; A2A validates into the DTO via `TOOLS[...].validate(...)` before `invoke_tool` (e.g. `adcp_a2a_server.py:1833`). A malformed request never touches the cache, so the timing side channel rule 1 names does not exist.
- **SG-C2 — Account resolution precedes the probe, and must.** `_boundary.invoke:241-247` enriches identity before `_keyed_scope`. The cache scope is `(authenticated agent, account_id, idempotency_key)`, so resolution is a precondition of computing the key, not an ordering choice; the spec constrains the *validation*/cache order and nothing else. An unresolvable account raises out of `enrich_identity_with_account` (`transport_helpers.py:136-146`) before any cache row is read, and `require_principal_id` runs first — so an unauthenticated caller cannot reach natural-key resolution and cannot learn tenant-wide match counts via `ACCOUNT_AMBIGUOUS` (§Agent and Account Isolation: *"Cross-account reads MUST return a generic 'not found' rather than leak existence"*). Scope leakage via the cache is structurally impossible: every query carries the tenant/principal/account prefix (`idempotency_attempt.py:52-66`).
- **SG-C3 — Rule 2's stored shape and immutability.** `_cacheable_body` stores the *inner* domain response and `record_success` writes `{"status": <protocol status>, "response": <dump>}`; `_deserializer_for` is the exact inverse. Nothing re-reads the resource on replay, so state-tracking fields are not refreshed. The async branch is right: `protocol_status=getattr(result, "status", None)` stores the result's own status, so a create awaiting approval stores `submitted` with its original `task_id` (`CreateMediaBuySubmitted`) and replays as `submitted` even after the task completes — which is exactly what rule 2's async bullet requires, and `test_idempotency_wire_matrix.py:96-99` grades byte-equality plus the marker.
- **SG-C4 — One cache, no tool dimension.** `tool_name` is recorded for observability and is not a scope column (`idempotency_attempt.py:99-113`), so a key reused on another tool hits the same row and conflicts on its differing hash. That is rule 5 read together with the migration text: *"Reusing a key on another tool is a different payload and returns `IDEMPOTENCY_CONFLICT`."* Per-tool caches would have made that unreachable.
- **SG-C5 — Rule 6 durability.** The cache is a Postgres table (`IdempotencyAttempt`), so it survives restarts and pod replacement for the declared 24h. The spec's named non-conformance (*"In-memory-only stores … are non-conformant"*) does not apply.
- **SG-C6 — Rule 7.** `get_idempotency_posture` declares `replay_ttl_seconds=86400`, and `IdempotencyPosture.check_bounds` enforces the schema's 3600–604800 range plus the cross-field `in_flight_max_seconds <= replay_ttl_seconds` rule, raising `CONFIGURATION_ERROR` rather than clamping.
- **SG-C7 — Rule 8.** `enforce_insert_ceiling` runs on a cache MISS only (`idempotency_replay.py:74-82`), scoped per `(tenant, principal, account)` — rule 8's scope. The separate read/write counters rule 8 requires apply only to sellers that cache keyed reads; this one does not (SG-C8), so a single budget is correct here.
- **SG-C8 — Which tools are keyed: `_spec_declares_idempotency_key` is conformant, and this is the finding I most expected to write and could not.** The predicate honours a key only when the SDK ancestor declares it, selecting exactly `create_media_buy`, `update_media_buy`, `sync_creatives`, `sync_accounts`. At 3.1.1 **no** read schema declares the property — I checked `account/list-accounts-request.json`, `get-products-request.json`, `list-creatives-request.json`, `get-media-buys-request.json`, `get-adcp-capabilities-request.json`; all are `idempotency_key: absent, additionalProperties: true`. The counter-evidence I went looking for and weighed: rule 1's *"they MUST accept and apply the replay contract when a caller supplies one"*, rule 8's read-traffic sizing which names *"`list_accounts` across 5 accounts at 1Hz"* as contributing cache inserts, and the `replayed` schema description's *"the cache holds read responses too."* None of them overturns the reading, and the graded storyboard settles it: `static/compliance/source/universal/read-tool-idempotency.yaml` grades the read tools on **tolerance only** — accept the envelope, return a schema-valid response, echo `context` unchanged — and its narrative states outright that *"The existing `idempotency` storyboard continues to cover mutating replay semantics, conflict detection, TTL declaration, and `replayed: true`."* Rule 8's phrasing is conditional throughout (*"Operators that cache keyed reads SHOULD adopt a split budget"*), i.e. caching reads is permitted, not required. The `list_accounts` DTO's local `idempotency_key` is correctly excluded from the announcement (`_announced_shape.py`) and correctly not honoured. **Conformant.**
- **SG-C9 — Read-tool tolerance is real and graded end-to-end.** §Server-side tool wrapper conformance names FastMCP strict signatures as the trap. `tests/harness/test_forward_compat_acceptance.py::TestReadToolIdempotencyEnvelope` dispatches the full 3.1 envelope through `Client(mcp)` under `ENVIRONMENT=production` for `list_accounts` and `get_products` and asserts `not is_error` plus an unchanged `context` echo, with a documented two-stage mechanism and a stated mutation check. The storyboard also names `list_creative_formats`, `list_creatives` and `get_adcp_capabilities`; those three are not in the parametrization — worth adding, but the mechanism is tool-independent.
- **SG-C10 — The exclusion list is complete; correcting prebid/salesagent#2215.** The premise in that issue is true of the frozenset and the conclusion does not follow. `adcp==6.6.0` ships the nested exclusion separately from `EXCLUDED_FIELDS`: `adcp/server/idempotency/canonicalize.py:37-39` defines `_NESTED_EXCLUSIONS = (("push_notification_config","authentication","credentials"),)` and `strip_excluded_fields` applies it at `:55-56`. All four of the spec's closed-list entries are honoured, and it holds through the model path — a rotated webhook credential does not change the hash. The repo's suite pins this correctly: `test_exclusion_set_is_the_spec_closed_list` pins the three top-level names as literals and `test_nested_webhook_credential_excluded` pins the fourth behaviourally. Verify with:
  ```
  ENVIRONMENT=production uv run python -c "
  from src.core.schemas.account import SyncAccountsRequest
  from src.core.idempotency_canonical import canonical_request_hash
  pnc=lambda c:{'url':'https://buyer.example/hook','authentication':{'schemes':['Bearer'],'credentials':c}}
  b=dict(idempotency_key='k'*20, accounts=[])
  print(canonical_request_hash(SyncAccountsRequest(**b,push_notification_config=pnc('A'*40))) ==
        canonical_request_hash(SyncAccountsRequest(**b,push_notification_config=pnc('B'*40))))  # True"
  ```
  #2215 should be closed, or rewritten to describe SG-02, which is the real equivalence defect.

## Notes (out of scope for this PR)

- **`dry_run` responses are now cached.** On `main`, `_cache_and_return` documented "errors and dry-runs are not [cached]"; the boundary caches any non-failed status, so a `dry_run: true` create with a key now takes a cache row, and a subsequent real create reusing that key gets `IDEMPOTENCY_CONFLICT`. That is what rule 5 prescribes for a differing canonical payload, so the new behavior is the conformant one — recorded because it is an unremarked change and it consumes rule-8 insert budget.
- **`tests/integration/test_idempotency_wire_matrix.py:262-268`** carries a graduation comment claiming #2214 is closed by the uniform capture point. The uniformity claim is correct and verified. The comment should not be read as closing the equivalence question, which SG-02 reopens on a different axis.
- **The ±60s skew gap (SG-07) predates this branch** — `find_by_key`'s predicate is unchanged from `main`. It is listed because this branch extends its blast radius from one tool to four.
