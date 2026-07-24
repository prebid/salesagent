# PR #1546 — AdCP 3.1.1 protocol grounding

This note records the authoritative AdCP contract used for the protocol-facing
changes in PR #1546. The repository pins `adcp==6.6.0`, which maps to AdCP
3.1.1. All upstream paths below are at the `adcontextprotocol/adcp` tag
`v3.1.1`.

AdCP 3.1.1 is a patch of the 3.1 release, so its schemas and compliance
storyboards are published under `3.1.1`, while the unchanged 3.1 prose shipped
at that tag remains under `dist/docs/3.1.0`.

## Version negotiation and context echo

Authoritative sources:

- `dist/docs/3.1.0/protocol/get_adcp_capabilities.mdx`, section
  **Version Negotiation**
- `dist/docs/3.1.0/reference/versioning.mdx`
- `dist/schemas/3.1.1/core/version-envelope.json`
- `dist/schemas/3.1.1/error-details/version-unsupported.json`
- `dist/schemas/3.1.1/protocol/get-adcp-capabilities-request.json`
- `dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json`
- `dist/schemas/3.1.1/core/context.json` (an opaque
  `additionalProperties: true` object)
- `dist/docs/3.1.0/building/by-layer/L2/context-sessions.mdx`, section
  **Normative echo contract**, especially rules 2 and 5 (echo application
  context on success and error without removing or retyping fields)

Grading status:

- `dist/compliance/3.1.1/universal/version-negotiation.yaml` grades
  `adcp.supported_versions`, response-envelope `adcp_version`, and an unchanged
  request `context` echo. In 3.1.1 the release-precision advertisement and
  envelope echo are advisory checks; the context-presence and exact-value checks
  are not marked advisory.
- `dist/compliance/3.1.1/universal/error-compliance.yaml`, phase
  `version_negotiation`, grades `VERSION_UNSUPPORTED` for an unsupported major.
  Its release-precision sibling is advisory in 3.1; both paths grade unchanged
  context echo.
- The local cross-major, below-minimum same-major, and unmatched-prerelease
  resolution cases in `BR-UC-010-version-negotiation.feature` are companion
  coverage and are not separate published 3.1.1 storyboard steps.

Decision for this PR: real MCP, A2A, and REST scenarios treat the captured wire
response as the sole context-echo oracle. Only the non-wire IMPL test path may
inspect a typed response directly. Serialization distinguishes omission from
an explicitly supplied JSON null: generated SDK defaults may omit unset schema
fields, but must not delete null-valued keys inside the buyer-owned opaque
context. The same shared serializer is used for success responses and typed
error envelopes.

The two version fields have deliberately separate sources. AdCP
`supported_versions` is derived from the SDK's pinned spec release (`3.1.1` →
wire release `3.1`). Advisory `build_version` identifies the Sales Agent
deployment lineage for incident triage and comes from the seller package
version (`src.core.version.get_version()`); it is not another spelling of the
AdCP spec release and is never a negotiation candidate. The same seller build
identifier is emitted in capabilities and `VERSION_UNSUPPORTED` details.

## Capability protocol filtering

Authoritative sources:

- `dist/schemas/3.1.1/protocol/get-adcp-capabilities-request.json`, property
  `protocols` (the five-value enum and `minItems: 1`)
- `dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json`, property
  `supported_protocols`
- `dist/compliance/3.1.1/universal/capability-discovery.yaml`, step
  `get_capabilities_filtered`

The published filtered-discovery step sends `protocols: ["media_buy"]` and
expects a schema-valid filtered response with unchanged context — its stated
expectation is "the same structure but only the requested domain details". The
local three-transport companion runs that request over MCP, A2A, and REST and
asserts the real wire. Unknown enum values and an empty array are
schema-grounded `VALIDATION_ERROR` cases.

The filter narrows the response's per-domain capability DETAIL sections; it does
not narrow `supported_protocols`. That field is the agent's own declaration —
the response schema describes each listed value as committing the agent "to pass
the baseline compliance storyboard" for that protocol — so it reports what this
agent implements, independent of what the buyer asked about.

A valid but unsupported-only filter (`["signals"]` against this media_buy-only
seller) is therefore an ordinary success: the true declaration, with no detail
sections. An earlier revision rejected it with `VALIDATION_ERROR`, reasoning
that `supported_protocols` has `minItems: 1` and so could not represent the
empty intersection. That reasoning only holds if the field is filtered in the
first place, and it put a schema-valid request on an error path that
`error-handling.mdx` scopes to schema violations.

## Authentication-before-version precedence

No pinned AdCP 3.1.1 prose, schema, or compliance step was identified that
mandates whether authentication or version negotiation must win when both are
invalid. This PR authenticates first as a local, ungraded non-disclosure
policy: an unauthenticated caller cannot use version errors to probe seller
capabilities. The UC-011 companion proves that precedence on the real wire; it
is not presented as an upstream conformance requirement.

## Required idempotency keys and the seller capability

Authoritative sources:

- `dist/schemas/3.1.1/account/sync-accounts-request.json`
- `dist/schemas/3.1.1/creative/sync-creatives-request.json`
- `dist/schemas/3.1.1/media-buy/create-media-buy-request.json`
- `dist/schemas/3.1.1/media-buy/update-media-buy-request.json`
- `dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json`
- `dist/compliance/3.1.1/universal/read-tool-idempotency.yaml`
- `dist/docs/3.1.0/protocol/get_adcp_capabilities.mdx`, section
  **adcp / idempotency**

The sync-accounts, sync-creatives, create-media-buy, and update-media-buy
request schemas require `idempotency_key`; the field is a string of 16–255 characters matching
`^[A-Za-z0-9_.:-]{16,255}$`.

The capabilities schema is a discriminated union. `supported: true` requires
`replay_ttl_seconds`; `supported: false` means the seller does not deduplicate
retries and requires `replay_ttl_seconds` and `in_flight_max_seconds` to be
absent.

The 3.1.1 `read-tool-idempotency` storyboard says the every-request envelope
also applies to reads. Its `read_requests_accept_idempotency_key` phase grades
valid supplied keys on `get_adcp_capabilities`, `get_products`, `list_accounts`,
`list_creative_formats`, and `list_creatives`. Its
`omitted_key_grace_handled` branch explicitly permits either acceptance or
rejection when a read omits the key during 3.1; this seller takes the
compatibility-accept branch. The local ingress registry applies the same
behavior to all eight standard reads registered on MCP, including
`get_media_buys`, `get_media_buy_delivery`, and `list_tasks`. A2A and REST apply
the same validator to the subset of those operations that each transport
actually exposes; `list_tasks` remains intentionally MCP-only as documented in
`docs/development/a2a-mcp-agent-flows.md`, so this PR makes no A2A/REST
`list_tasks` claim. Rejecting a malformed *supplied* read key before stripping
it is a local, ungraded consistency rule using the same 16–255 character
constraint.

### Dedupe scope of `idempotency.supported: true` (residual, #1607)

The agent-wide capability block advertises `idempotency.supported: true`, and
the spec defines `supported` as whether the seller deduplicates replays. Today
exactly ONE tool deduplicates: `create_media_buy` (verbatim replay of the
stored success; a conflicting payload rejects). The other twelve
`require_idempotency_key(` call sites — including the spend-affecting
`update_media_buy`, plus `sync_accounts` and `sync_creatives` — VALIDATE and
accept the key but perform no cache read, so a retried request re-executes.

The advertised value is therefore broader than the implemented behavior, and
the hazard the spec names for that gap is buyer double-spend on a retried
mutation. This is a known, accepted residual for this PR, tracked at #1607
(extend dedupe through the same repository seam); it is recorded here and in
the Scope section of the PR description rather than being narrowed to
`supported: false`, which would understate `create_media_buy` and turn off the
graded replay phases it does satisfy.

Grading status:

- `dist/compliance/3.1.1/universal/idempotency.yaml` contains a `missing_key`
  phase and the replay, changed-payload conflict, fresh-key, and concurrent
  first-insert-wins checks.
- That storyboard validates replay behavior for sellers declaring
  `supported: true`. Its capability check explicitly treats `supported: false`
  as a valid, advisory declaration for which replay-window phases are not
  applicable; the published file notes that a complete storyboard precondition
  gate is still pending runner support.

Decision for this PR: `create_media_buy` implements verbatim replay, so the
seller advertises the `supported: true` discriminant with `replay_ttl_seconds`.
A repeated `idempotency_key` on create replays the stored success verbatim
(`replayed: true`); a same-key different-canonical-payload request rejects with
`IDEMPOTENCY_CONFLICT`; errors are never cached (a retry after an error
re-executes); the `media_buys` unique index remains the dup-booking backstop
with a fail-closed degraded path; the per-scope insert ceiling guards
admission. The required key is validated on every create/update/sync-creatives/
sync-accounts boundary; on those OTHER writes it is accepted but **not yet
deduplicated** — the partial-dedupe gap is tracked in #1607, which extends
dedupe through the same `IdempotencyAttemptRepository`. On registered standard
reads, omission is accepted under the 3.1 grace and a valid supplied key is
validated inert metadata.

The `dist/compliance/3.1.1/universal/idempotency.yaml` replay / changed-payload
conflict / fresh-key phases now grade against this seller's live create
behavior under the advertised `supported: true`.

The concurrent-first-insert-wins phase grades what the BUYER observes — one
`media_buy_id` across both responses — and that holds: the unique index makes
the second insert lose and the loser never returns a second buy. It is NOT full
rule-9 conformance. Rule 9 requires the canonical hash on an in-flight claim row
written BEFORE the downstream invoke ("write-claim-before-invoke"), and this
seller writes the hash on the committed `MediaBuy`, so the adapter call at
`media_buy_create.py` runs before the duplicate is detected. A concurrent
same-key retry can therefore book a second ad-server order whose orphan the
code notes at the degraded-replay site. The claim row is the reservation
subsystem, tracked separately; the gap is stated here rather than implied to be
covered.

### Generated UC-002 status

The upstream replay scenario `T-UC-002-v31-idempotency-replay` is LIVE in the
generated UC-002 feature — it grades production replay on a2a/mcp/rest as plain
PASS (original `media_buy_id` returned, `replayed: true`, adapter not
re-invoked, persisted set byte-stable), so no local overlay reconciles it. The
one remaining local overlay
(`tests/bdd/overlays/BR-UC-002-create-media-buy.feature`) is the boundary
fixture only: it replaces the upstream hand-counted over-max key with the
declarative `<256 chars>` token, which the bound Given step expands to exactly
256 valid-pattern characters so the maxLength boundary cannot silently drift.
`compile_bdd.py` applies exact-ID overlays in both `--all` and scenario-merge
modes, records provenance, and fails if the target ID disappears; unit coverage
grades both compiler paths.

The upstream supported-true phases this seller does NOT yet implement
(in-flight tracking, expired-window, canonical-comparison, conflict-details)
remain visible in the generated feature but unwired — tracked for the
reservation-subsystem rebuild (#1683). The local applicability guard asserts
the live `supported: true` discriminant matches the enforced replay window and
that the unimplemented phases stay visible-not-claimed.

## Update-media-buy revision

Authoritative source:

- `dist/schemas/3.1.1/media-buy/update-media-buy-request.json`, property
  `revision`

When supplied, `revision` is an optimistic-concurrency precondition. The schema
requires the seller to compare it atomically with the write and return
`CONFLICT` when it does not equal the current revision.

Grading status: no dedicated revision/optimistic-concurrency storyboard is
published under `dist/compliance/3.1.1`; this repository's BR-UC-003 scenarios
are local schema-derived coverage, not a claim that the upstream compliance
runner grades this behavior.

Decision for this PR: the seller does not yet implement the required atomic
comparison. Every transport must preserve field presence and route any supplied
`revision` to the shared fail-loud guard, which rejects the request without
applying the update.

Omission remains valid and proceeds. Explicit JSON `null` is REJECTED as
schema-invalid (`INVALID_REQUEST`) — it is not treated as a spelling of
omission. Grounding: the pinned
`dist/schemas/3.1.1/media-buy/update-media-buy-request.json` defines `revision`
as `{"type": "integer", "minimum": 1}`, with no `anyOf`-null member, so a JSON
`null` violates the type constraint outright; it is schema-invalid, not merely
uncontemplated. An earlier draft of this decision reasoned the opposite way —
that null should be accepted as equivalent to omission because "the SDK models
revision as `int | None = None`, so a conformant client that never set it
serializes null." That premise is false: at the pinned adcp 6.6.0,
`UpdateMediaBuyRequest(...).model_dump()` OMITS an unset `revision` entirely
rather than serializing it as `null`, so no conformant client emits `null` in
the first place, and there is no compatibility reason to accept it. `null`
therefore falls through to the same `INVALID_REQUEST` branch as `0` / `"7"` /
`7.5`, consistently across MCP, A2A, and REST. This is a safety posture that
prevents an unprotected lost update; it is an explicit implementation gap, not
a claim of full revision conformance.

## Push-notification and reporting webhook delivery

Authoritative sources:

- `docs/building/by-layer/L3/webhooks.mdx` at tag `v3.1.1`
- `dist/schemas/3.1.1/core/push-notification-config.json`
- `dist/schemas/3.1.1/core/mcp-webhook-payload.json`
- `dist/schemas/3.1.1/core/reporting-webhook.json`
- `dist/schemas/3.1.1/media-buy/media-buy-delivery-webhook-result.json`
- `dist/compliance/3.1.1/universal/webhook-emission.yaml`
- `dist/compliance/3.1.1/test-vectors/webhook-signing/`

Those sources define the buyer-facing configuration/payload and signed-webhook
contract. The published webhook-emission storyboard grades emission and the
normative signing contract. This implementation still lacks the signing-key
infrastructure needed to claim RFC 9421 default-signing conformance; the legacy
authentication/signing paths covered here are therefore not represented as
full conformance to that portion of the storyboard.

The following changes are **local security hardening, ungraded by the AdCP
3.1.1 storyboard**: require HTTPS outside the explicit private-test opt-in,
reject URL userinfo, reject private/reserved DNS results, pin the validated IP
to the socket, refuse environment proxies and redirects, close streamed
responses, reject non-finite JSON, sign and transmit the same exact bytes, and
treat 3xx/4xx/security-policy refusals as permanent rather than retryable.
Registration DNS and delivery DNS/socket work use bounded worker bulkheads with
hard caller deadlines; timed-out work retains its permit until the underlying
blocking call actually finishes, preventing timeout floods from creating an
unbounded executor queue.
These controls must be applied at registration before workflow/database writes
and rechecked at delivery to protect legacy rows and DNS rebinding. They are not
described as AdCP-mandated URL or retry semantics.

Decision for this PR: invalid callback targets fail with a buyer-correctable
validation error before core execution or persistence. Existing legacy HTTP or
otherwise unsafe rows are refused at delivery without retry. The local tests
grade these policies across registration and outbound transport; no dedicated
AdCP compliance step is claimed for them.
