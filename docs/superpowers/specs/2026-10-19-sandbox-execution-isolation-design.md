# Sandbox execution isolation

Date: 2026-10-19

Status: approved for implementation, including #2041, on 2026-10-19

Base: upstream `main`, `713aee8b5b498b7dcc4298ea8509ee35ec1f6ba8` (refreshed before final validation).
Implementation began on `8953c4649aa2be762961f1869d6ed2fcdfc8f4e4`; the refresh adds only the unrelated #2358 account-settings form fix.

## Outcome and scope

A sandbox account must complete the media-buy lifecycle through simulated ad-server
execution while retaining the tenant's normal approval workflow. Approval must remain
meaningful: a sandbox buy requiring human review stays pending until the normal
workflow permits execution. Approval then produces simulated platform results.

This replacement for [PR #1864](https://github.com/prebid/salesagent/pull/1864)
addresses the remaining execution defect in
[#2074](https://github.com/prebid/salesagent/issues/2074). It uses the central account
identity introduced by merged [PR #1721](https://github.com/prebid/salesagent/pull/1721).
The old branch's account-enrichment mechanism and per-caller sandbox flags are obsolete.

**Approved scope extension:**
[#2041](https://github.com/prebid/salesagent/issues/2041) explicitly assigned the
mock adapter's extra validation restrictions to a separate follow-up. This design
recommends including that limited correction in the replacement PR: using a simulator
must preserve the configured adapter's input rules. The proposal below includes it;
the user explicitly approved that overlap on 2026-10-19 under the repository's
scope and deduplication agreements. Broader validation cleanup remains separate.

**Approved scheduler extension:** on 2026-10-19 the user approved the narrow
[#1644](https://github.com/prebid/salesagent/issues/1644) caller-session repair needed
for scheduled delivery. Capture plain report inputs, close the read session before
account resolution and delivery, then use fresh work sessions while preserving
duplicate-send checks and force behavior. Remove the obsolete scheduler guard entries
and add real scheduler regressions. Shared-session infrastructure and the other
#1644 call sites remain outside this PR.

Demo-credential authorization in [#1522](https://github.com/prebid/salesagent/issues/1522)
is excluded. Capability declarations and the response-marker sweep tracked in
[#1874](https://github.com/prebid/salesagent/issues/1874) and
[#1918](https://github.com/prebid/salesagent/issues/1918) remain separate. There is no
database migration, new dependency, or public request-field change in this proposal.

## Contract and evidence

The repository pins AdCP **3.1.1**, through `adcp==6.6.0`, as recorded in
[the version document](../../adcp-spec-version.md). The following pinned upstream
sources ground the intended behavior. SDK types are a cross-check.

| Behavior | Authoritative source | Conformance coverage |
| --- | --- | --- |
| Account determines sandbox mode; sandbox requests cannot call a real ad platform or create real billing records | AdCP 3.1.1 [Sandbox, Seller implementation and Protocol compliance](https://github.com/adcontextprotocol/adcp/blob/v3.1.1/docs/media-buy/advanced-topics/sandbox.mdx#seller-implementation) | Real platform-call suppression is ungraded by the inspected storyboards; local negative-side-effect tests are required. |
| Apply normal input validation and return simulated response data | The same Sandbox section | Validation equivalence is ungraded by the inspected storyboards; paired live/sandbox tests are required. |
| Preserve submitted task envelope when approval defers creation | [create_media_buy_async storyboard](https://github.com/adcontextprotocol/adcp/blob/v3.1.1/dist/compliance/3.1.1/protocols/media-buy/scenarios/create_media_buy_async.yaml), `submitted_arm_response.create_media_buy_submitted` | Grades the submitted response; normal tenant approval policy and later human approval are local acceptance requirements. |
| An update must address a buy owned by the referenced account | [update_media_buy task reference](https://github.com/adcontextprotocol/adcp/blob/v3.1.1/docs/media-buy/task-reference/update_media_buy.mdx) | [invalid_transitions](https://github.com/adcontextprotocol/adcp/blob/v3.1.1/dist/compliance/3.1.1/protocols/media-buy/scenarios/invalid_transitions.yaml), `unknown_media_buy.update_unknown_media_buy`, grades the not-found envelope. Cross-account isolation needs a local test. |
| Every mutating update advances the media-buy revision | AdCP 3.1.1 [update_media_buy, Optimistic concurrency](https://github.com/adcontextprotocol/adcp/blob/v3.1.1/docs/media-buy/task-reference/update_media_buy.mdx#optimistic-concurrency) | Ungraded by the inspected storyboards; local package-mutation tests assert persisted state and revision advancement. |
| A supplied delivery account filters results; an omitted account can span accessible accounts | [get-media-buy-delivery request schema](https://github.com/adcontextprotocol/adcp/blob/v3.1.1/dist/schemas/3.1.1/media-buy/get-media-buy-delivery-request.json) | [delivery_reporting](https://github.com/adcontextprotocol/adcp/blob/v3.1.1/dist/compliance/3.1.1/protocols/media-buy/scenarios/delivery_reporting.yaml), `simulate_and_verify.get_delivery`, grades reporting. Mixed-account routing needs local tests. |

Seller-side stored-account failures reuse the existing persisted-state exception. The
AdCP 3.1.1 [error-code schema](https://github.com/adcontextprotocol/adcp/blob/v3.1.1/dist/schemas/3.1.1/enums/error-code.json),
`enumDescriptions.CONFIGURATION_ERROR` and `enumMetadata.CONFIGURATION_ERROR`,
define seller misconfiguration as terminal for the buyer and prohibit secrets in
the error message. Applying that existing category to a missing stored account is
a local safety decision, ungraded by the inspected storyboards. This code requires
transport failure markers. Other optional snapshot failures retain the endpoint's
existing partial-result behavior.

Preserving the tenant's approval policy is an explicit user requirement. The upstream
async storyboard does not mandate a particular seller approval configuration.

## Design

### Select execution centrally

Keep `get_adapter(identity)` as the execution entry point. The full account already
exists on `AccountIdentity`; do not add a second sandbox flag or re-resolve account
references inside each tool.

Resolve the configured adapter type using the existing precedence:
`AdapterConfig.adapter_type`, then the tenant fallback. The sandbox branch must run
before credential assembly or any vendor adapter/client constructor. A sandbox account
selects an internal sandbox adapter. Live accounts retain the configured adapter.

Use a tenant-scoped repository projection for the non-secret configuration needed by
that decision and the approval policy. Do not call the GAM credential builder to obtain
an approval flag. Share the effective approval-policy calculation with live construction
so defaults cannot drift. Preserve existing adapter-specific handling of nullable flags.

This updates #2074's historical suggestion to avoid every `AdapterConfig` read:
preserving configured approval requires reading non-secret policy, while credentials
and vendor clients remain outside the sandbox execution path. The capabilities helper
`resolve_manual_approval_signal` is unsuitable for enforcement because its documented
null handling differs from the live factory.

### Preserve approval and validation

Creation continues through the existing tenant and adapter approval gates, including
product-level auto-creation settings. Updates retain their current approval rules.
This PR must not normalize differences between create and update approval policy.
Existing approval notifications and protocol completion callbacks remain part of the
normal workflow; the execution isolation rule prohibits real platform and billing work.

After approval, resolve the account from the stored buy and select the simulator again.
Do not auto-approve a sandbox buy or introduce a second mock approval delay/rejection.
GAM's platform order approval/polling is distinct from human approval and must never be
entered for sandbox execution.

Separate the configured adapter's validation from the selected execution implementation.
Make the existing adapter request validator callable without constructing a vendor client,
sharing its implementation with the live path. The sandbox adapter delegates to that
configured validator. A GAM sandbox therefore uses GAM's supported pricing models and
does not acquire the mock's extra budget/impression limits. A tenant configured to use
the mock retains its existing mock rules. GAM's locally decidable builder refusals
run during prevalidation too. Kevel and Triton reuse their existing targeting checks;
Kevel's optional feature flags retain their live defaults. GAM's tenant-specific
AXE/custom-key resolution stays in live execution because it depends on configured
keys and vendor lookups.

Replace the create path's concrete-class check for GAM product configuration with the
resolved configured adapter type. Reuse `GAMProductConfigService` and generate missing
defaults in a local copy for sandbox validation. Sandbox execution must not persist
generated defaults to shared product rows. Preserve live behavior and the existing
ordering of validation relative to approval gates; verify both immediate and approved
execution paths. This is limited reuse of rules already implemented by the application,
not an emulation of every validation a remote vendor might perform.

### Reuse simulation safely

Add a small internal `SandboxAdServerAdapter` using the existing adapter interface.
Extract shared pure response/calculation helpers from the mock where they are actually
reused. Do not route sandbox buyers through the mock's test-scenario parser, simulated
rejections, or AI-assisted order naming. Existing explicit conformance-controller
behavior stays at its established boundary.

Use the application's stored sandbox buy and packages as the source for subsequent
updates and reporting. Query them through repositories scoped to the resolved account.
Generate unique simulated platform IDs independent of purchase-order text. Avoid the
mock's process-global buy dictionary and its fallback that invents delivery for a missing
buy. This allows a new process or approval callback to resume from persisted state
without contacting a vendor. Reuse existing seeded delivery data only after ownership
has been established.

Persist only sandbox-owned buy/workflow data and simulated results associated with those
buys. Keep synthetic platform references on buy/package state using existing storage;
do not put a simulated platform creative ID into the shared creative's live
`data.platform_creative_id`. A creative may be assigned to both a live and a sandbox buy.
Sandbox execution must neither reuse that live platform reference nor overwrite it.

The sandbox adapter marks its implemented snapshot support through the existing internal
`AdapterCapabilities.supports_realtime_reporting` flag. Seller-facing capability
declarations continue to use the configured tenant adapter class; this flag only
allows the listing path to dispatch simulated snapshots.

The create module's deferred creative paths currently access GAM-specific managers.
Give sandbox execution an explicit path through the existing adapter-level creative
operation; preserve the existing GAM path. Reuse a focused shared dispatch helper for
the approval and later creative-push callers. No general creative-upload rewrite or new
manager abstraction is needed. Sandbox creative handling must return a real simulated
result rather than silently skip an unsupported manager.

### Keep the buy's account throughout its lifecycle

| Entry point | Required behavior |
| --- | --- |
| Create | Boundary-resolved account selects execution; stored buy retains that account. |
| Update | Resolve the buy within tenant, principal and account scope before any workflow/domain mutation or adapter call. A sibling account's buy returns `MEDIA_BUY_NOT_FOUND`, even if both accounts are sandbox. |
| Human approval | Retain `execute_approved_media_buy`'s stored account resolution; verify creation and creative processing use it throughout. |
| Later creative approval/push | Read the target buy's account alongside its owner. Validate assignment ownership and use that account for execution. |
| Account-scoped delivery/list | Filter before dispatch, using the repository's existing account filter. |
| Unscoped delivery/list snapshots | Group accessible buys by their stored account and use a resolved identity per group. Combine results using the existing response contract. Listing without snapshots needs no adapter. |
| Scheduled delivery | Resolve the stored account for the target buy before selecting execution. |
| Admin media-buy detail | Use the displayed buy's owner and account for delivery reads. |

For stored-buy execution, a missing or unresolvable account is a seller configuration
failure, never permission to assume live mode. Use the existing typed error machinery.
The pinned `enums/error-code.json` requires transport failure markers for
`CONFIGURATION_ERROR`, so unresolved stored-account failures propagate through the
shared error boundary on both reporting endpoints. Other optional snapshot failures
retain their unavailable-row advisory behavior. Listing without snapshots needs no
adapter execution and still returns the stored row. These cases are locally graded;
the inspected storyboards do not cover them. This safety rule can expose legacy buys
with null accounts; this PR does not infer or backfill their ownership.

`identity_of` opens database sessions, and the current session helper is thread-scoped.
Resolve identities outside active units of work: capture scalar ownership IDs, close the
read unit, resolve identity, then open the work unit. Re-read and verify ownership in the
work unit before mutation. Do not pass ORM objects between those phases or introduce a
nested identity lookup that closes the caller's session. Apply this to account grouping
and the affected admin callback as well as creative push.

## Expected change boundary

The file map identifies the proposed implementation radius. It is not permission for
adjacent cleanup; any further subsystem or migration requires a new scope decision.

| Area | Expected files and reason |
| --- | --- |
| Adapter selection/policy | `src/core/helpers/adapter_helpers.py`; `src/core/database/repositories/adapter_config.py`: central routing and non-secret policy projection. |
| Simulation/validation | New `src/adapters/sandbox.py`; `src/adapters/base.py`; `src/adapters/mock_ad_server.py`: sandbox execution plus shared validation/calculation helpers. Approved review extensions: `src/adapters/google_ad_manager.py`, `src/adapters/gam/managers/targeting.py`, `src/adapters/kevel.py`, and `src/adapters/triton_digital.py` share existing local targeting refusals.  |
| Buy ownership lookup | `src/core/database/repositories/media_buy.py`: account-scoped lookup where existing methods are insufficient. |
| Lifecycle orchestration | `src/core/tools/media_buy_create.py`, `media_buy_update.py`, `media_buy_delivery.py`, `media_buy_list.py`: affected account propagation and dispatch. |
| Scheduled delivery | `src/services/delivery_webhook_scheduler.py`: close caller sessions before stored-account execution; retain duplicate-send behavior. Remove its obsolete nested-UoW and raw-select guard entries and add focused scheduler regressions. |
| Admin reads and approval | `src/admin/blueprints/operations.py`: resolve owner/account outside the work session and invoke the approval callback after the route's existing approval commit and session closure. |
| Verification | Existing adapter/lifecycle test modules and focused new sandbox tests; integration setup uses factories. |

Transport wrappers continue using the shared boundary from #1721. No new per-tool
identity-enrichment registry or AST allowlist is proposed.

## Acceptance and validation

| Test | Evidence required |
| --- | --- |
| Central selection | Sandbox operations cannot construct any configured vendor client or invoke credential builders; paired live controls still select the configured adapter. Cover configuration precedence. |
| Approval preservation | With the same tenant settings, live and sandbox create produce the same approval decision. Sandbox remains pending until approval; after approval its domain state and workflow completion persist with zero vendor calls. Cover nullable flags and product auto-creation settings. |
| Validation equivalence | Paired configured-adapter cases cover supported/unsupported pricing and the existing local targeting refusals in GAM, Kevel, and Triton. GAM cases exercise its real targeting builder as an independent execution oracle, including technology fields and metro-system restrictions. Positive controls accept GAM/Kevel display and Triton audio. Kevel controls preserve enabled feature flags and media-buy frequency-cap refusal. These targeting checks are locally graded, ungraded by the inspected storyboards. Mock-only budget/impression limits stay scoped to mock; GAM product validation runs without shared catalog writes. |
| Account isolation | Wrong-account update is rejected before writes. Scoped reports omit sibling accounts. Mixed live/sandbox reads route each group correctly. Missing stored account cannot fall back to a vendor. |
| Creative callbacks | Initial and later approval complete through sandbox creative execution; a shared creative's live platform data remains unchanged. Query persisted results from a fresh session to catch closed-session write loss. |
| Simulation continuity | Fresh adapter instances/process state can read a stored sandbox buy. Repeated purchase-order text across accounts cannot alias simulated buys; missing buys do not receive fabricated delivery. |
| Boundary/side effects | Exercise the existing real transport harness with sandbox requests and instrument vendor entry points/outbound platform traffic. Assert wire error envelopes and persisted domain state, rather than only mocked factory arguments. |

Validation requires focused unit and PostgreSQL integration/admin coverage with the
required stack, then `make quality` before committing and the repository's `make pre-pr`
checks before opening the PR. Exact results belong in the implementation reports and PR
description. Apply the repository-guidelines review and ORM lifecycle review, followed
by test-suite review through the post-change review flow. Failures block readiness.

## Proposed PR

Title: **fix: isolate sandbox media-buy execution**

The description should identify #2074, link this design's pinned spec/storyboard
evidence, and explain that it supersedes #1864 using main's central account identity.
Include #2041 and its validation tests, as approved by the user.
Do not close the credential/capability/marker follow-ups as part of this work.

Refresh main and repeat the overlap search immediately before implementation and again
before PR creation. Keep the old PR open until the replacement is available for review;
closing it requires a separate user instruction.


## Review corrections approved 2026-10-23

The replacement review at `1dc19db09` identifies six corrections. GAM targeting
validation was completed in `db7da6d6e`. The remaining changes preserve live adapter
selection, including its mock fallback, and share that selection with sandbox
validation. Broadstreet dispatch changes remain in #2024. Seeded sandbox delivery
reads the tenant-scoped `DeliverySimulationConfig` row directly; the pre-existing
mock adapter test-flag dependency remains in #2255.

Stored-account resolution retains the central resolver's typed account refusals.
AdCP 3.1.1 [error-code schema](https://github.com/adcontextprotocol/adcp/blob/v3.1.1/dist/schemas/3.1.1/enums/error-code.json)
classifies `ACCOUNT_SETUP_REQUIRED` and `PERMISSION_DENIED` as correctable, and
`ACCOUNT_SUSPENDED` and `ACCOUNT_PAYMENT_REQUIRED` as terminal. Reporting preserves
these codes in its per-buy advisories. Missing stored ownership or missing seller
identity state still fails with `CONFIGURATION_ERROR`. This reporting placement is
a local policy, ungraded by the inspected storyboards.

The approved one-file extension, `repositories/push_notification_config.py`, owns
the newest-active-registration lookup for both approval decisions and scheduled
reports. A stable ID breaks equal creation-time ties. Each caller copies the same
registration fields before releasing its session. This preserves the existing
registration and notification contracts.

For combined sandbox package updates, the response contains one entry per package
with its final stored pause state. This follows the post-update-state requirement
in AdCP 3.1.1 [update_media_buy, Success Response](https://github.com/adcontextprotocol/adcp/blob/v3.1.1/docs/media-buy/task-reference/update_media_buy.mdx#success-response).
The broader, pre-existing sparse-package response shape remains outside this PR.
Combined-update consistency is locally graded, ungraded by the inspected storyboards.
The mixed-account snapshot scenario creates its live buy through the real seller
and checks positive metrics and an empty advisory array.
