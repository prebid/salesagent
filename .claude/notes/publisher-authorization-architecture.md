# Publisher Authorization & Agent Identity — Target Architecture

Status: DESIGN (2026-07-29, deep pass triggered by salesagent-piyo). Grounded in three
exploration reports: pinned-spec surface (v3.1.1 via `git -C ~/projects/adcp show v3.1.1:`),
codebase current-state map, and verification-mechanics audit. Citations inline.

Related: salesagent-piyo (fabricated publisher_domains), #1291 (RFC 9421 signing),
salesagent-7x8t (key provisioning), GH #1697 (webhook SSRF opt-out).

---

## 1. The protocol's trust model (what we must serve)

Two documents, two owners, one buyer-side verification algorithm:

| Document | Hosted at | Owner asserts | Keys |
|---|---|---|---|
| `brand.json` + JWKS | agent operator's domain | "this agent is mine, here are its keys" | operator-minted |
| `adagents.json` | publisher's domain | "that agent may sell my inventory" | optional `signing_keys[]` pin |

The buyer runs two independent checks (spec: `docs/governance/property/authorized-properties.mdx` L167-205):

1. **Identity**: signature → `capabilities.identity.brand_json_url` → brand.json `agents[]`
   (byte-equal URL match, `security.mdx` step 5) → `jwks_uri` → JWKS.
2. **Authorization**: `capabilities.media_buy.portfolio.publisher_domains` → fetch each
   `https://{domain}/.well-known/adagents.json` → our agent URL must appear in
   `authorized_agents[]` (canonicalized match per `authorized-agent-base.json`).

Load-bearing spec facts (each verified against the v3.1.1 tag):

- **`list_authorized_properties` is retired in v3.** Portfolio advertising lives on
  `get_adcp_capabilities` (`get_adcp_capabilities.mdx` L1058-1060; last schema at
  3.0.0-beta.1). Our `properties.py` tool implements a tool that does not exist in the pin.
- **`portfolio` is optional; inside it `publisher_domains` is required with `minItems: 1`.**
  (`get-adcp-capabilities-response.json`.) So "no publishers" is expressed by omitting
  `portfolio`, never by an empty array and never by a fabricated domain.
- **`identity.brand_json_url` becomes mandatory the moment any signing posture is declared**
  (`request_signing.supported_for`/`required_for` non-empty, `webhook_signing.supported=true`,
  or any `key_origins` subfield) — storyboard-enforced in 3.x, schema-required in 4.0.
  Declaring signing without identity is a rejectable response.
- **Publishers MUST pin `signing_keys` for mutating-scope agents** (`adagents.mdx` L732).
  Our own adagents.json (tenant-is-publisher case) already does this (`trust_root.py`).
  The pin overrides JWKS **only for sell-side webhook delivery**; request signing always
  resolves via brand.json `jwks_uri` (`security.mdx` step 6).
- **Seller-side self-verification is SPEC-SILENT.** Nothing requires a seller to verify its
  own `publisher_domains` claims; the model is buyer-verifies-seller. Anything we build here
  is a local product decision and must be labeled as such, not spec-cited.
- **The spec contradicts itself on missing adagents.json**: `adagents.mdx` L1257 says fail
  open, `authorized-properties.mdx` L244 says fail closed (and its pseudocode implements
  fail-closed). Upstream defect; we must pick one and cite the contradiction.
- **`revoked_publisher_domains[]` MUST be honored** by validators (adcp#4504; SDK helpers
  `_get_revoked_publisher_domains`/`filter_revoked_selectors` exist and we call neither).
- The five-state trust vocabulary in `docs/verification/overview.mdx` — `inline`,
  `mutual_assertion`, `one_sided_brand`, `one_sided_house`, `standalone` — with the explicit
  rule: *"one_sided_brand … Do not treat as authorization. Any domain can claim any publisher
  unilaterally."* Only `inline` and `mutual_assertion` close the chain.
- **No 3.1.1 storyboard grades `portfolio`/`publisher_domains`** (the mdx claims compliance
  testing validates presence; no storyboard does — upstream prose/storyboard divergence).
- Prose for 3.1.1 lives at `docs/**` in the tag; `dist/docs/3.1.1/` does not exist.
  `request-signing.mdx` is non-normative and its error vocabulary is wrong; `security.mdx`
  is the authority.

## 2. The two topologies

**T1 — tenant is the publisher.** The tenant's agent origin *is* a publisher domain. We host
both documents. Authorization is `inline`: our own adagents.json at our own origin, listing
our own agent with our own properties. Self-attestation is legitimate here *because we host
the file at the domain being attested* — the domain owner is speaking.

**T2 — tenant represents other publishers.** We host brand.json + JWKS at the tenant's
origin. Each represented publisher must host adagents.json at *their* domain naming our
canonical agent URL. We can never produce that file for them; we can only (a) generate the
exact content they should host, and (b) observe whether they did.

Everything below is organized around making both topologies first-class and making the
"observe" loop real.

## 3. Product decisions (embedded in this design)

**D1 — Advertise attested domains only.** `publisher_domains` = { attested T2 domains } ∪
{ T1 self domains }. Spec-silent, so this is ours to choose. Rationale: the buyer's algorithm
fails closed on every domain we list that doesn't reciprocate (`one_sided_brand` = "do not
treat as authorization"), so advertising an unattested claim can only produce buyer-side
verification failures against us. Advertising what a buyer's check would confirm is the only
claim worth making. No attested domains and no T1 self domain → omit `portfolio`.

**D2 — Retire the `list_authorized_properties` tool.** It does not exist in the 3.1.1 pin
(removed in v3). Keeping it violates our own spec-grounding gate. The dormant
BR-UC-007 feature file (which grades "all partnerships included regardless of verification" —
the opposite of D1) is graded against a retired tool and must be reconciled upstream, not
wired. The AuthorizedProperty *model* survives — it feeds the trust root — the tool goes.

**D3 — Fail closed on missing/unlisted adagents.json for our own attestation loop.** The
upstream prose is contradictory (§1); we adopt the fail-closed reading — it matches the
buyer pseudocode, our "No Quiet Failures" rule, and the D1 posture. For *transient* fetch
failures we follow the spec's only normative ladder (`managed-networks.mdx` L619-626):
serve the last good attestation for up to 24h of failures, absolute cap 7 days from last
successful fetch, then the attestation lapses.

**D4 — No verification bypass in production code, ever.** The `is_dev or is_mock`
auto-verify branch is deleted, not gated harder. Dev convenience is provided by *real*
verification against a dev-stack-hosted publisher fixture (a static adagents.json served by
the test/dev stack), so dev and prod run the identical code path. This is the same rule
`trust_root.py` already states for fabrication, applied to the consumer side.

## 4. Target domain model

### 4.1 Tenant public identity — stop fabricating, make it explicit

The tenant's public origin is the anchor for *everything* (brand.json URL, JWKS, adagents
match string, T1 publisher domain). Today it's fabricated in four places when absent
(`Tenant.primary_domain` models.py:228, three `Product.publisher_properties` fallbacks
~models.py:420-440) and hand-rolled in three more (`publisher_partners.py:246,332,494`).

Target:
- `src/core/agent_identity.py` is the **only** derivation module (it already exists and is
  correct). The `publisher_partners` ladder migrates to `canonical_agent_url` — completing
  the migration `authorized_properties.py:215` documents as its purpose.
- `Tenant.primary_domain` returns `virtual_host`, else the routable
  `{subdomain}.{SALES_AGENT_DOMAIN}` host when configured, else **None**. The
  `.example.com` literal is banned from `src/` by a structural guard.
- A tenant with `primary_domain is None` has **no public identity**: it cannot advertise a
  portfolio, cannot publish claims, and its setup checklist says exactly that. "Cannot claim"
  replaces "claim a fake domain" everywhere (capabilities, Product.publisher_properties,
  trust-root routing).

### 4.2 `PublisherPartner` → attestation state machine

Replace the boolean `is_verified` + string `sync_status` with an explicit attestation state,
aligned to the spec's five-state vocabulary projected onto the seller's viewpoint:

```
status: claimed | attested | unattested | lapsed
```

- `claimed` — row exists (operator declared "we represent X"), never successfully checked.
  Never advertised (D1).
- `attested` — publisher's adagents.json fetched and our canonical URL matched.
  Corresponds to `mutual_assertion` once we advertise it. Advertised.
- `unattested` — fetched successfully; we are **not** listed, or the domain appears in
  `revoked_publisher_domains[]`, or the file is invalid (file-level MUST-abort per
  `adagents.mdx` L1275). Not advertised. Carries `failure_reason`.
- `lapsed` — was attested; fetches have failed past the 7-day absolute cap (D3).
  Not advertised.

New columns (migration): `attestation_status` (CHECK), `last_attested_at`,
`last_checked_at`, `next_check_at`, `attestation_snapshot` (JSONType — the matched
`authorized_agents[]` entry + `catalog_etag`/ETag for conditional refresh),
`matched_identifier` (which of our published URL shapes the publisher listed — §4.4),
`failure_reason`. Drop `is_verified` after consumers migrate. T1 rows are *not* stored here —
the tenant's own domain is derived, not claimed (no self-row to keep in sync).

Transient fetch failure is **not** a state: it updates `last_checked_at` and a failure
counter, keeps the prior status, and only 7 days of continuous failure moves
`attested → lapsed` (spec ladder, D3). A transient CDN outage is not a revocation.

### 4.3 `AuthorizedProperty` — split the two roles it currently conflates

Today one table serves (a) OUR properties feeding OUR adagents.json (trust root reads
`list_for_publisher_domain(agent_origin_host)`) and (b) cached copies of represented
publishers' property definitions — with five writers stamping `verified` for different
reasons.

Target: an explicit `origin` discriminator (`owned` | `represented`):
- **`owned`** (T1): authored by the tenant (admin UI / bulk upload). No verification
  status at all — these rows ARE the attestation; we publish them in our adagents.json.
  Only `owned` rows feed `build_adagents_json` (replacing the fragile domain-equality
  selector with an explicit predicate).
- **`represented`** (T2): a read-only projection of the publisher's adagents.json for our
  agent, written **only** by the attestation service (§5) as part of a successful attest —
  same fetch, same document, no second fetch (kills the fetch-twice/disagree defect).
  Their freshness is the parent authorization's freshness; no per-row verification columns.
- `verification_status`/`verification_checked_at` and `property_verification_service`
  retire once both roles are explicit (the service's job — "does the publisher's file back
  this row" — becomes the attestation service's definition of `represented` rows).

## 5. The attestation service (the one verifier)

One service, `PublisherAttestationService`, replacing: the sync route's inline logic, the
auto-verify branch, `property_verification_service`, and the discovery service's
authorization-blind import.

**Check algorithm** (per authorization row):
1. Fetch via SDK `fetch_adagents_with_cache` **without** `client=` (keeps the SDK's IP-pinned
   transport, redirect policy, size caps; conditional refresh via ETag).
2. Honor `revoked_publisher_domains[]` first (MUST) — listed → `unattested(revoked)`.
3. Match `authorized_agents[]` against **all** identifier shapes we publish: the canonical
   origin (`canonical_agent_url`) and every endpoint URL (`agent_endpoint_urls`). Record
   which shape matched in `matched_identifier`. We publish two shapes (origin in
   adagents.json, endpoint URLs in brand.json) and the SDK's `normalize_url` is
   path-sensitive — matching only one is the current silent-failure defect. A non-canonical
   match attests but surfaces an advisory in the admin UI ("publisher listed your endpoint
   URL; canonical is …").
4. On match: write `attested`, snapshot the entry, project its properties into
   `represented` AuthorizedProperty rows in the same unit of work (one fetch, one truth).
5. Not listed / invalid file → `unattested` + `failure_reason` distinguishing "not listed"
   from "listed under an unrecognized shape" (diagnostic lists the URLs we looked for AND
   what the file contained).
6. Schedule `next_check_at`: from the response `Cache-Control`, floor 1h, ceiling 24h
   (mirrors the spec's signing-key-pin default and the ≥24h general guidance).

**Execution model**: never in the HTTP request cycle (the async-architecture rule; the sync
route today holds a Postgres session across 30s of network I/O). Attestation runs as jobs on
the shared background worker (§5a) — a periodic sweep task enqueues one check job per
authorization row `WHERE next_check_at <= now()`, with a queueing lock per
`(tenant_id, publisher_domain)` so a domain is never checked concurrently. Admin
"check now" defers the same job directly and returns 202 with the row's current state; the
UI polls the row. `next_check_at` stays in OUR table as the source of scheduling truth — the
queue carries execution, not domain state. This also gives us revocation detection for
free — the sweep re-checks forever, and a publisher deleting our entry moves us
`attested → unattested` within one TTL.

## 5a. Background work architecture (shared, not attestation-specific)

Attestation must not get a bespoke loop, because the codebase already carries a whole family
of background work with no home, in four wrong shapes: two in-process asyncio loops
(`DeliveryWebhookScheduler`, `MediaBuyStatusScheduler` — die on deploy, and would DUPLICATE
if uvicorn ever runs >1 worker), unsupervised daemon threads spawned from request handlers
(GAM order-approval polling from inside `create_media_buy`, GAM inventory sync from the
admin UI, mock delivery simulator, AI-review ThreadPoolExecutor), synchronous adapter I/O
inline in Flask requests (`sync_api.py` full GAM sync; `execute_approved_media_buy` from
three handlers), and write-only queue tables (`webhook_delivery_log.next_retry_at` written,
never read; all retry/circuit-breaker state in process memory). The owner-confirmed
direction (async-sync-architecture note) already mandates: accept → validate → `201
pending` → background worker calls GAM/etc → update status → notify.

**Target: one worker process consuming a Postgres-backed job queue.**

- **Technology: Procrastinate** (PostgreSQL-based task queue; PG 13+; sync + async APIs;
  periodic tasks; retries with backoff; stalled-job recovery; per-job queueing locks; CLI
  worker; MIT). Verified against its docs 2026-07-29.
  - Why not Celery/RQ/Dramatiq/arq: all need Redis/RabbitMQ — new stateful infrastructure
    in a deliberately Postgres-exclusive system.
  - Why not hand-rolled SKIP LOCKED table: visibility timeouts, stalled-job recovery,
    retry semantics and poison-pill handling are subtle and already solved; owning them is
    debt, not control.
  - Why not the issue-draft idea of reusing `WorkflowStep` as the queue: WorkflowStep
    models human-in-the-loop approval, drained by people in the admin UI. Overloading it as
    a machine queue conflates two lifecycles; instead, the adapter-execution JOB calls
    `execute_approved_media_buy` — the entry point survives, the table stays what it is.
  - Risk noted: Procrastinate is seeking additional maintainers. Bounded by usage shape —
    jobs are thin wrappers that call services; the schema is plain Postgres; swapping the
    queue later touches only the job layer.
- **Deployment**: a `worker` service in docker-compose, same image, worker entrypoint. Web
  processes only defer jobs. Horizontal scaling is native (Postgres locking); no leader
  election. Test/dev: Procrastinate's in-memory/eager modes run jobs inline.
- **Job design rules**: jobs are thin and idempotent — claim, call a service, service
  writes domain state through repositories/UoW. Domain scheduling state (`next_check_at`,
  `next_retry_at`) lives in domain tables; the queue holds execution attempts only. Every
  job carries `tenant_id`; queueing locks prevent per-entity concurrency.
- **Migration order** (each retires one wrong-shaped mechanism; separate epic, filed
  independently of this design): (1) attestation ships on the worker from day one — it is
  the proving ground; (2) webhook delivery + retries (makes `next_retry_at` real, removes
  in-memory queue); (3) adapter execution — the `201 pending` flow for
  create/update_media_buy and GAM inventory sync (removes inline Flask I/O and both daemon
  threads); (4) periodic status transitions + delivery webhooks (retires both asyncio
  loops); (5) delete the dead supercronic cron.

**Auth + hardening on the blueprint** (immediate, independent of the redesign):
`@require_tenant_access()` on all five routes (today: none — unauthenticated writes to any
tenant's authorization state, mounted at both `/` and `/admin`); stop returning raw
`str(e)`; repository/UoW instead of raw sessions (admin UoW mandate).

## 6. Wire surfaces

### 6.1 `get_adcp_capabilities`

- `portfolio`: emitted only when the attested/self domain set is non-empty (D1);
  `publisher_domains` from that set; drop the placeholder (salesagent-piyo). The
  degradation-advisory pattern already in the file handles "repository unavailable";
  zero-domains is the honest common case, not a degradation.
- `identity`: emit `brand_json_url` via the existing (currently dead) producer
  `agent_identity.brand_json_url`, plus `key_origins.request_signing` via `jwks_origin` —
  **in the same change** that flips any signing posture true, because the conditional
  requiredness binds them (§1). A tenant with no public identity (§4.1) declares no signing
  posture and no identity — consistent, schema-valid.
- `request_signing`/`webhook_signing`: replace the hardcoded `supported=False` constants
  with the posture functions that already exist (`webhook_signing_posture` is already used
  by the outbound sender; capabilities must stop contradicting it).
- Remove `identity` from the `capability_declarations.py` refusal list once emitted.

### 6.2 `list_authorized_properties`

Retired per D2: remove tool registration from all transports, remove the schemas, reconcile
BR-UC-007 upstream (it grades a tool the pin removed — and its INV-1 "include regardless of
verification" is the pre-D1 posture). This is a protocol-behavior change: PR cites
`get_adcp_capabilities.mdx` L1058 ("removed in v3") + the migration table.

### 6.3 Trust-root documents (mostly already correct)

`build_adagents_json` keeps its never-fabricate contract; its input becomes explicitly
`owned` properties (§4.3). `build_brand_json`/`build_jwks` unchanged. The `.well-known`
routes stay Host-routed and unauthenticated.

## 7. Onboarding — from CRUD editor to guided attestation

The current admin surface writes rows without checking coherence with anything. Target—
built on the existing `setup_checklist_service` (which already models "provisionable
per-tenant resources" and already carries the signing-key item):

**Step 1 — Public identity.** Set `virtual_host` (or confirm routable subdomain). The
checklist verifies it *live*: fetch our own `https://{origin}/.well-known/brand.json`
through the public route and confirm it returns our document. A key nobody can fetch and a
domain that doesn't route are both caught here (the gap
`scripts/ops/provision_signing_key.py:9-12` names).

**Step 2 — Signing key.** Existing checklist item + `provision_signing_key`; surfaced during
tenant creation rather than discoverable-only.

**Step 3 — Declare inventory topology, per publisher:**
- *"This is my own property"* (T1): creates `owned` AuthorizedProperty rows → immediately
  visible in our own adagents.json (the checklist shows the live document diff before/after).
- *"I represent publisher X"* (T2): creates a `claimed` authorization row and generates the
  **exact adagents.json content the publisher must host** — our canonical agent URL,
  `authorized_for`, and our `signing_keys` pin (the mutating-scope MUST, `adagents.mdx`
  L732, and the pin is authoritative for our sell-side webhooks — generating it FOR the
  publisher is how we make that requirement real). UI renders: the JSON snippet, the
  well-known path, and "merge into your existing file" guidance. The attestation loop then
  polls; the row visibly progresses `claimed → attested` (or surfaces the precise mismatch).
  **Nothing the operator does in our UI can mark it attested.**

**Step 4 — Go live.** Portfolio appears in capabilities automatically once ≥1 domain is
attested/self (D1) — there is no "publish" button to forget and no way to advertise more
than is true.

**Tenant creation unification** (supporting work, admin-UoW-mandate-aligned): five raw
`Tenant(...)` insert paths exist; the super-admin form silently drops three fields and
creates none of the dependent rows the system requires (CurrencyLimit, PropertyTag). One
`TenantProvisioningService` used by all entry points, with the checklist as its contract.

## 8. Testing & guards

- **BDD**: wire dormant BR-UC-025 (supply-path authorization — auth-check, auth-statuses,
  auth-independence, Extension M) across all four transports; it grades exactly this work.
  Author scenarios for: attested-only portfolio emission (D1), omit-portfolio-when-empty
  (piyo), identity emission bound to signing posture, attestation state transitions
  (claimed→attested, attested→unattested on de-listing, revoked_publisher_domains,
  7-day lapse). Reconcile BR-UC-007 upstream (D2). Dev-stack publisher fixture (D4) serves
  real adagents.json documents for these — mutual_assertion and one_sided cases both.
- **Factories**: `PublisherAuthorizationFactory` with per-state traits (today's factory
  hardcodes verified; the unverified path is untested).
- **Structural guards** (ratchet, shrink-only): (1) `.example.com` literal banned in `src/`;
  (2) `attestation_status`/`is_verified` writes only inside the attestation service;
  (3) extend the existing webhook-sender-boundary style to "no adagents fetch outside the
  attestation service".
- **Integration**: attestation service against the fixture publisher (real Postgres, real
  HTTP through the SDK transport); trust-root suite already strong, add the
  cross-publisher-domain exclusion negative.

## 9. Sequencing

- **P0 — stop the bleeding** (independently shippable, this branch or next):
  auth decorators on publisher_partners routes; salesagent-piyo fix (omit portfolio,
  no placeholder); delete `.example.com` fallbacks in `models.py` (return None; fix the
  three Product call sites to skip/omit); delete the auto-verify + property-fabrication
  branch (D4).
- **P1 — model**: attestation state machine migration; `origin` split on
  AuthorizedProperty; factories + state-transition tests.
- **P2 — worker infrastructure + attestation service**: introduce the Procrastinate worker
  service (§5a) with attestation as its first job family; SDK-cached fetch, multi-shape
  matching, revoked-domains handling, represented-property projection; retire
  property_verification_service; migrate sync route to 202-defer. Migration of the OTHER
  background work onto the worker is a separate epic (§5a migration order), unblocked by P2.
- **P3 — wire**: attested-only portfolio; identity + truthful signing postures (one
  change, bound by conditional requiredness); retire list_authorized_properties (D2);
  BR-UC-025 wiring + new scenarios.
- **P4 — onboarding**: snippet generator; checklist steps 1/3/4; live self-checks;
  TenantProvisioningService unification.

Defects from the audit not owned by this design — FILED 2026-08-04: dead cron → GH #1851
(new); background-worker architecture decision + broadened migration inventory → comment on
GH #1069 (canonical epic; related #1658, #1717); creative_agent_registry unvalidated fetch,
adapter no-timeout sites, and the #1697-deferred IP-pinning/body-cap requirements → comment
on GH #1589 (secure outbound-fetch wrapper; guard-matcher angle in #1691). The
webhook-sender `client=`/SSRF finding is NOT filed upstream — webhook_sender_factory.py
exists only on the A3 branch, so it's in-scope for the epic's reimplementation (§5a note +
the #1589 comment's "sharp edge" input cover it).

## 10. Spec-grounding citations for the PRs

- Portfolio omission/requiredness: `dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json`
  (`portfolio` not in `media_buy.required`; `publisher_domains` required, `minItems: 1`). Storyboard: ungraded
  (no 3.1.1 storyboard grades portfolio — upstream divergence from mdx L177, flag when citing).
- identity/brand_json_url conditional requiredness: same schema, `identity` description +
  `x-adcp-validation`; `docs/building/by-layer/L1/security.mdx` (normative discovery, 8 steps).
- list_authorized_properties retirement: `docs/protocol/get_adcp_capabilities.mdx` L1058-1060;
  `dist/compliance/3.1.1/specialisms/signal-owned/index.yaml` L32.
- adagents.json contract: `dist/schemas/3.1.1/adagents.json`; `docs/governance/property/adagents.mdx`
  (well-known path L86-102; empty-array semantics L263; signing_keys MUST L732; malformed-file
  ladder L1275-1283).
- Fail-closed choice: cite BOTH `adagents.mdx` L1257 (open) and `authorized-properties.mdx`
  L244 (closed) — upstream contradiction, we adopt closed (D3).
- Freshness ladder: `docs/governance/property/managed-networks.mdx` L619-626.
- Seller-side self-check: spec-silent (documented sweep in the spec-surface report) — D1 is a
  product decision, labeled as such.
