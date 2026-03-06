# UC-003: Update Media Buy -- Test Obligations

## Source

- Requirement: [UC-003: Update Media Buy](adcp-req/docs/requirements/use-cases/UC-003-update-media-buy/UC-003.md)
- Files analyzed: UC-003.md, UC-003-main-mcp.md, UC-003-ext-a through UC-003-ext-o, UC-003-alt-pause.md, UC-003-alt-timing.md, UC-003-alt-budget.md, UC-003-alt-creative-ids.md, UC-003-alt-creatives-inline.md, UC-003-alt-creative-assignments.md, UC-003-alt-targeting.md, UC-003-alt-manual.md
- Business Rules: BR-RULE-008, BR-RULE-012, BR-RULE-013, BR-RULE-017, BR-RULE-018, BR-RULE-020, BR-RULE-021, BR-RULE-022, BR-RULE-024, BR-RULE-026, BR-RULE-028

## 3.6 Upgrade Impact

### salesagent-7gnv: MediaBuy boundary drops buyer_campaign_ref, creative_deadline, ext fields
- **CRITICAL**: The `UpdateMediaBuyRequest` and related response models must preserve `buyer_campaign_ref`, `creative_deadline`, and `ext` fields across the boundary. In adcp 3.6, these fields may be newly required or renamed.
- Impact on: Main flow step 11 (response must include `buyer_ref`), all alternative flows returning success responses, and the request schema accepting `buyer_ref` for identification.
- Test obligation: Verify roundtrip serialization of `buyer_campaign_ref`, `creative_deadline`, and `ext` in both request and response models.

### salesagent-mq3n: PricingOption delivery lookup compares string to integer PK
- **MODERATE for UC-003**: While primarily a UC-004 issue, the `pricing_option_id` field appears in `AdCPPackageUpdate` and `AffectedPackage`. If update responses include pricing information, the string/integer mismatch could surface.
- Test obligation: Verify `pricing_option_id` type consistency in package update and affected package models.

### salesagent-goy2: Creative extends wrong adcp type
- **HIGH for creative flows**: UC-003 alt-creative-ids, alt-creatives-inline, and alt-creative-assignments all depend on creative models. If Creative extends the wrong base type, validation in steps 6-8 of alt-creative-ids may produce wrong results.
- Test obligation: Verify creative model inheritance chain is correct for all creative assignment validation paths.

### Schema changes in adcp 3.6
- `UpdateMediaBuyRequest` extends `LibraryUpdateMediaBuyRequest1` -- verify the library base class still matches expectations after 3.6 upgrade.
- `AdCPPackageUpdate` extends `LibraryPackageUpdate1` -- verify package update schema compatibility.
- `AffectedPackage` extends `LibraryPackage` -- verify affected package schema has all required fields.

---

## Test Scenarios

### Main Flow: Package Budget Update (Auto-Applied)
Source: UC-003-main-mcp.md

#### Scenario: Happy path -- update package budget via media_buy_id
**Obligation ID** UC-003-MAIN-01
**Layer** behavioral
**Given** an authenticated buyer who owns a media buy with one package, and the tenant is configured for auto-approval
**When** the buyer sends `update_media_buy` with `media_buy_id` and one `packages` entry containing `package_id` and updated `budget`
**Then** the system returns a success response in a protocol envelope with status `completed`, containing `media_buy_id`, `buyer_ref`, `implementation_date` (non-null), and `affected_packages` array with the updated package
**Business Rule** BR-RULE-018 (atomic response: success XOR error), BR-RULE-022 (partial update semantics)
**Priority** P0 -- core happy path

#### Scenario: Happy path -- update package budget via buyer_ref
**Obligation ID** UC-003-MAIN-02
**Layer** behavioral
**Given** an authenticated buyer who owns a media buy, identified by `buyer_ref`
**When** the buyer sends `update_media_buy` with `buyer_ref` (no `media_buy_id`) and a package budget update
**Then** the system resolves the media buy via buyer_ref and returns success with updated package
**Business Rule** BR-RULE-021 (dual identification XOR)
**Priority** P0 -- alternative identification path

#### Scenario: Partial update semantics -- omitted fields unchanged
**Obligation ID** UC-003-MAIN-03
**Layer** behavioral
**Given** a media buy with `start_time`, `end_time`, `budget`, and `paused` all set
**When** the buyer sends `update_media_buy` with only a package budget change (no timing, no pause)
**Then** `start_time`, `end_time`, and `paused` remain at their original values; only the specified package budget changes
**Business Rule** BR-RULE-022 (INV-1: present fields updated, INV-2: omitted fields unchanged)
**Priority** P0 -- fundamental contract

#### Scenario: Empty update rejected
**Obligation ID** UC-003-MAIN-04
**Layer** behavioral
**Given** an authenticated buyer who owns a media buy
**When** the buyer sends `update_media_buy` with only identification (no updatable fields)
**Then** the system returns an error (request must not be empty)
**Business Rule** BR-RULE-022 (INV-3: no updatable fields = rejected)
**Priority** P1

#### Scenario: Currency limit validation on package budget update
**Obligation ID** UC-003-MAIN-05
**Layer** behavioral
**Given** a media buy with a 30-day flight and a tenant with `max_daily_package_spend` of $1000
**When** the buyer updates a package budget to $50,000 (daily spend = $1,667)
**Then** the system rejects with `budget_limit_exceeded`
**Business Rule** BR-RULE-012 (INV-2: daily budget > max)
**Priority** P1

#### Scenario: Currency limit passes when max not configured
**Obligation ID** UC-003-MAIN-06
**Layer** behavioral
**Given** a media buy and a tenant with no `max_daily_package_spend` configured
**When** the buyer updates a package budget to any positive amount
**Then** the daily spend check is skipped; update proceeds
**Business Rule** BR-RULE-012 (INV-3: no max configured = check skipped)
**Priority** P2

#### Scenario: Adapter called with correct action for package budget
**Obligation ID** UC-003-MAIN-07
**Layer** behavioral
**Given** a valid package budget update request that passes all validation
**When** the system processes the update
**Then** the adapter `update_media_buy()` is called with action `update_package_budget`, the correct `package_id`, and the new budget
**Business Rule** BR-RULE-020 (adapter atomicity)
**Priority** P1

#### Scenario: Database persisted after successful adapter call
**Obligation ID** UC-003-MAIN-08
**Layer** behavioral
**Given** a valid package budget update where the adapter returns success
**When** the adapter call completes successfully
**Then** the `MediaPackage` record and `package_config` are updated with the new budget
**Business Rule** BR-RULE-020 (INV-1: adapter success = records persisted)
**Priority** P1

#### Scenario: Response includes affected_packages array
**Obligation ID** UC-003-MAIN-09
**Layer** behavioral
**Given** a successful package budget update
**When** the system returns the success response
**Then** the `affected_packages` array contains the updated package with its current state
**Business Rule** BR-RULE-018 (atomic response: success fields present, no errors)
**Priority** P1

#### Scenario: Response wrapped in protocol envelope with status completed
**Obligation ID** UC-003-MAIN-10
**Layer** behavioral
**Given** a successful auto-applied update
**When** the system returns the response
**Then** the protocol envelope has `status: completed` and the response body contains success data without error fields
**Business Rule** BR-RULE-018 (INV-1: success = no errors field)
**Priority** P1

#### Scenario: buyer_campaign_ref preserved in response [3.6 UPGRADE]
**Obligation ID** UC-003-MAIN-11
**Layer** behavioral
**Given** a media buy that was created with a `buyer_campaign_ref`
**When** the update succeeds and the response is serialized
**Then** the `buyer_ref` field in the response matches the original `buyer_campaign_ref` value
**Business Rule** salesagent-7gnv (boundary drops fields)
**Priority** P0 -- upgrade regression

#### Scenario: ext fields preserved in request/response roundtrip [3.6 UPGRADE]
**Obligation ID** UC-003-MAIN-12
**Layer** behavioral
**Given** a media buy with `ext` extension fields
**When** an update request includes or the response returns `ext` data
**Then** the `ext` fields are preserved without data loss through serialization/deserialization
**Business Rule** salesagent-7gnv (boundary drops fields)
**Priority** P0 -- upgrade regression

---

### Alt: Pause/Resume Campaign
Source: UC-003-alt-pause.md

#### Scenario: Pause an active media buy
**Obligation ID** UC-003-ALT-PAUSE-RESUME-CAMPAIGN-01
**Layer** behavioral
**Given** an authenticated buyer who owns an active (unpaused) media buy
**When** the buyer sends `update_media_buy` with `paused: true`
**Then** the adapter is called with action `pause_media_buy`, the media buy status is updated to paused, and the response has status `completed`
**Business Rule** BR-RULE-018
**Priority** P0 -- core alternative path

#### Scenario: Resume a paused media buy
**Obligation ID** UC-003-ALT-PAUSE-RESUME-CAMPAIGN-02
**Layer** behavioral
**Given** an authenticated buyer who owns a paused media buy
**When** the buyer sends `update_media_buy` with `paused: false`
**Then** the adapter is called with action `resume_media_buy`, the media buy status is updated to active, and the response has status `completed`
**Business Rule** BR-RULE-018
**Priority** P0 -- core alternative path

#### Scenario: Pause skips budget/currency validation
**Obligation ID** UC-003-ALT-PAUSE-RESUME-CAMPAIGN-03
**Layer** behavioral
**Given** a media buy where the currency limit would be exceeded
**When** the buyer sends a pause request (no budget changes)
**Then** the pause proceeds without currency validation (no financial parameters changed)
**Business Rule** Divergence from main flow -- no currency/budget validation for pause
**Priority** P2

#### Scenario: Pause is a campaign-level action (all packages affected)
**Obligation ID** UC-003-ALT-PAUSE-RESUME-CAMPAIGN-04
**Layer** schema
**Given** a media buy with three packages
**When** the buyer sends `update_media_buy` with `paused: true`
**Then** the `affected_packages` in the response includes all three packages
**Business Rule** Campaign-level action targets entire media buy
**Priority** P2

#### Scenario: Pause may require manual approval
**Obligation ID** UC-003-ALT-PAUSE-RESUME-CAMPAIGN-05
**Layer** behavioral
**Given** a tenant where pause operations require manual approval (adapter config)
**When** the buyer sends a pause request
**Then** the system enters the manual approval flow (status `submitted`)
**Business Rule** BR-RULE-017
**Priority** P2

---

### Alt: Update Timing
Source: UC-003-alt-timing.md

#### Scenario: Update end_time only
**Obligation ID** UC-003-ALT-UPDATE-TIMING-01
**Layer** behavioral
**Given** a media buy with start_time=2026-03-01 and end_time=2026-03-31
**When** the buyer sends `update_media_buy` with `end_time=2026-04-15` (no start_time)
**Then** the end_time is updated to 2026-04-15, start_time remains 2026-03-01, and the response has status `completed`
**Business Rule** BR-RULE-022 (partial update), BR-RULE-013 (datetime validity)
**Priority** P1

#### Scenario: Update start_time only
**Obligation ID** UC-003-ALT-UPDATE-TIMING-02
**Layer** behavioral
**Given** a media buy with start_time=2026-03-01 and end_time=2026-03-31
**When** the buyer sends `update_media_buy` with `start_time=2026-03-05` (no end_time)
**Then** the start_time is updated to 2026-03-05, end_time remains 2026-03-31
**Business Rule** BR-RULE-022, BR-RULE-013
**Priority** P1

#### Scenario: Update both start_time and end_time
**Obligation ID** UC-003-ALT-UPDATE-TIMING-03
**Layer** behavioral
**Given** a media buy
**When** the buyer updates both `start_time` and `end_time` with valid values where end > start
**Then** both dates are updated and daily spend is recalculated against all packages
**Business Rule** BR-RULE-013 (INV-1: valid range), BR-RULE-012 (daily spend recalc)
**Priority** P1

#### Scenario: Daily spend recalculated when flight shortened
**Obligation ID** UC-003-ALT-UPDATE-TIMING-04
**Layer** behavioral
**Given** a media buy with budget=$30,000 over 30 days (daily=$1,000) and max_daily=$1,500
**When** the buyer updates end_time to shorten the flight to 15 days (daily=$2,000)
**Then** the system rejects with `budget_limit_exceeded` because daily spend ($2,000) > max ($1,500)
**Business Rule** BR-RULE-012 (daily spend recalculation on timing change)
**Priority** P1

#### Scenario: Timing update does NOT sync to ad server (known gap G35)
**Obligation ID** UC-003-ALT-UPDATE-TIMING-05
**Layer** behavioral
**Given** a valid timing update
**When** the system processes the update
**Then** the dates are updated in the database only; no adapter call is made for timing changes
**Business Rule** Known gap G35 (database-only update)
**Priority** P2 -- gap documentation

---

### Alt: Campaign-Level Budget
Source: UC-003-alt-budget.md

#### Scenario: Update campaign-level budget
**Obligation ID** UC-003-ALT-CAMPAIGN-LEVEL-BUDGET-01
**Layer** behavioral
**Given** a media buy with campaign budget=$10,000
**When** the buyer sends `update_media_buy` with top-level `budget=15000`
**Then** the campaign budget is updated to $15,000 and the response has status `completed`
**Business Rule** BR-RULE-008 (budget > 0), BR-RULE-018
**Priority** P1

#### Scenario: Campaign budget must be positive
**Obligation ID** UC-003-ALT-CAMPAIGN-LEVEL-BUDGET-02
**Layer** behavioral
**Given** an authenticated buyer who owns a media buy
**When** the buyer sends `update_media_buy` with `budget=0`
**Then** the system rejects with `invalid_budget`
**Business Rule** BR-RULE-008 (INV-2: budget <= 0 rejected)
**Priority** P1

#### Scenario: Negative campaign budget rejected
**Obligation ID** UC-003-ALT-CAMPAIGN-LEVEL-BUDGET-03
**Layer** behavioral
**Given** an authenticated buyer who owns a media buy
**When** the buyer sends `update_media_buy` with `budget=-100`
**Then** the system rejects with `invalid_budget`
**Business Rule** BR-RULE-008 (INV-2)
**Priority** P2

#### Scenario: Campaign budget update recalculates daily spend
**Obligation ID** UC-003-ALT-CAMPAIGN-LEVEL-BUDGET-04
**Layer** behavioral
**Given** a media buy with 10-day flight and max_daily_package_spend=$500
**When** the buyer updates campaign budget to $10,000 (daily=$1,000)
**Then** the system rejects with `budget_limit_exceeded`
**Business Rule** BR-RULE-012
**Priority** P1

#### Scenario: Campaign budget update does NOT sync to ad server (known gap G35)
**Obligation ID** UC-003-ALT-CAMPAIGN-LEVEL-BUDGET-05
**Layer** behavioral
**Given** a valid campaign budget update
**When** the system processes the update
**Then** the budget is updated in the database only; no adapter call is made
**Business Rule** Known gap G35
**Priority** P2

---

### Alt: Update Creative IDs
Source: UC-003-alt-creative-ids.md

#### Scenario: Replace package creatives via creative_ids
**Obligation ID** UC-003-ALT-UPDATE-CREATIVE-IDS-01
**Layer** behavioral
**Given** a package with existing creative assignments [C1, C2] and creatives C3, C4 exist in the library
**When** the buyer sends a package update with `creative_ids: [C3, C4]`
**Then** existing assignments [C1, C2] are removed, new assignments [C3, C4] are created (replacement semantics)
**Business Rule** BR-RULE-024 (INV-1: creative_ids replaces all existing)
**Priority** P0 -- core creative path

#### Scenario: Creative existence validation
**Obligation ID** UC-003-ALT-UPDATE-CREATIVE-IDS-02
**Layer** behavioral
**Given** creative C1 exists in the library but C999 does not
**When** the buyer sends a package update with `creative_ids: [C1, C999]`
**Then** the system returns error `creatives_not_found` listing C999
**Business Rule** Existence check (ext-i)
**Priority** P1

#### Scenario: Creative state validation -- error state rejected
**Obligation ID** UC-003-ALT-UPDATE-CREATIVE-IDS-03
**Layer** behavioral
**Given** creative C1 is in `error` status
**When** the buyer sends a package update with `creative_ids: [C1]`
**Then** the system returns error `INVALID_CREATIVES` identifying C1's error state
**Business Rule** BR-RULE-026 (INV-2: error state rejected)
**Priority** P1

#### Scenario: Creative state validation -- rejected state rejected
**Obligation ID** UC-003-ALT-UPDATE-CREATIVE-IDS-04
**Layer** behavioral
**Given** creative C1 is in `rejected` status
**When** the buyer sends a package update with `creative_ids: [C1]`
**Then** the system returns error `INVALID_CREATIVES` identifying C1's rejected state
**Business Rule** BR-RULE-026 (INV-3: rejected state rejected)
**Priority** P1

#### Scenario: Creative format compatibility check
**Obligation ID** UC-003-ALT-UPDATE-CREATIVE-IDS-05
**Layer** behavioral
**Given** a package for product expecting "display" format, and creative C1 has "video" format
**When** the buyer sends a package update with `creative_ids: [C1]`
**Then** the system returns error `INVALID_CREATIVES` with format mismatch details
**Business Rule** BR-RULE-026 (INV-4: format incompatible)
**Priority** P1

#### Scenario: Change set computation -- added and removed creatives
**Obligation ID** UC-003-ALT-UPDATE-CREATIVE-IDS-06
**Layer** behavioral
**Given** a package with existing assignments [C1, C2, C3] and the buyer sends `creative_ids: [C2, C4]`
**When** the system computes the change set
**Then** C4 is identified as added, C1 and C3 are identified as removed, C2 is unchanged
**Business Rule** BR-RULE-024 (replacement semantics)
**Priority** P1

#### Scenario: Creative update has no adapter call
**Obligation ID** UC-003-ALT-UPDATE-CREATIVE-IDS-07
**Layer** behavioral
**Given** a valid creative ID update
**When** the system processes the update
**Then** creative assignments are persisted directly to the database without an adapter call
**Business Rule** Divergence from main flow
**Priority** P2

#### Scenario: Immutable package fields cannot be changed
**Obligation ID** UC-003-ALT-UPDATE-CREATIVE-IDS-08
**Layer** behavioral
**Given** a package with `product_id`, `format_ids`, `pricing_option_id`
**When** the buyer attempts to update `product_id` or `pricing_option_id` in a package update
**Then** the system rejects or ignores the immutable fields (schema constraint)
**Business Rule** Schema constraint -- immutable fields
**Priority** P2

#### Scenario: Creative model uses correct adcp base type [3.6 UPGRADE]
**Obligation ID** UC-003-ALT-UPDATE-CREATIVE-IDS-09
**Layer** behavioral
**Given** creative models extend adcp library types
**When** creative validation runs during creative_ids update
**Then** the creative model correctly extends the adcp 3.6 Creative type (not a wrong/old type)
**Business Rule** salesagent-goy2 (Creative extends wrong adcp type)
**Priority** P0 -- upgrade regression

---

### Alt: Upload Inline Creatives
Source: UC-003-alt-creatives-inline.md

#### Scenario: Upload and assign inline creatives to package
**Obligation ID** UC-003-ALT-UPLOAD-INLINE-CREATIVES-01
**Layer** behavioral
**Given** a package identified by `package_id` and the buyer provides 3 inline creative assets
**When** the buyer sends a package update with `creatives: [{asset1}, {asset2}, {asset3}]`
**Then** the system calls `_sync_creatives_impl()`, creates creative library records, creates `CreativeAssignment` records, and returns success
**Business Rule** BR-RULE-018
**Priority** P1

#### Scenario: Inline creatives use additive semantics (not replacement)
**Obligation ID** UC-003-ALT-UPLOAD-INLINE-CREATIVES-02
**Layer** behavioral
**Given** a package with existing creative assignments [C1, C2]
**When** the buyer sends inline creatives [C3, C4]
**Then** the package ends up with assignments [C1, C2, C3, C4] (additive, unlike creative_ids)
**Business Rule** Divergence from creative_ids: additive vs replacement
**Priority** P1

#### Scenario: Maximum 100 inline creatives enforced
**Obligation ID** UC-003-ALT-UPLOAD-INLINE-CREATIVES-03
**Layer** schema
**Given** a package update request
**When** the buyer provides 101 inline creative assets
**Then** schema validation rejects the request (maxItems: 100)
**Business Rule** Schema constraint
**Priority** P2

#### Scenario: Sync failure rolls back (no partial media buy update)
**Obligation ID** UC-003-ALT-UPLOAD-INLINE-CREATIVES-04
**Layer** behavioral
**Given** a package update with inline creatives
**When** `_sync_creatives_impl()` fails during upload
**Then** the media buy is not modified; error `creative_sync_failed` is returned
**Business Rule** POST-F1 (system state unchanged on failure)
**Priority** P1

---

### Alt: Update Creative Assignments
Source: UC-003-alt-creative-assignments.md

#### Scenario: Update creative assignments with weights
**Obligation ID** UC-003-ALT-UPDATE-CREATIVE-ASSIGNMENTS-01
**Layer** behavioral
**Given** a package with existing assignments, and creatives C1, C2 exist in the library
**When** the buyer sends `creative_assignments: [{creative_id: C1, weight: 70}, {creative_id: C2, weight: 30}]`
**Then** existing assignments are replaced; new assignments have the specified weights
**Business Rule** BR-RULE-024 (INV-2: creative_assignments replaces all)
**Priority** P1

#### Scenario: Update creative assignments with placement targeting
**Obligation ID** UC-003-ALT-UPDATE-CREATIVE-ASSIGNMENTS-02
**Layer** behavioral
**Given** a product with placements [P1, P2, P3] and creatives C1, C2 in the library
**When** the buyer sends `creative_assignments: [{creative_id: C1, placement_ids: [P1, P2]}]`
**Then** C1 is assigned with placement targeting for P1 and P2
**Business Rule** BR-RULE-028 (INV-1: valid placement_ids)
**Priority** P1

#### Scenario: Invalid placement IDs rejected
**Obligation ID** UC-003-ALT-UPDATE-CREATIVE-ASSIGNMENTS-03
**Layer** behavioral
**Given** a product with placements [P1, P2] only
**When** the buyer sends `creative_assignments: [{creative_id: C1, placement_ids: [P1, P999]}]`
**Then** the system returns error `invalid_placement_ids` identifying P999
**Business Rule** BR-RULE-028 (INV-2: invalid placement_id)
**Priority** P1

#### Scenario: Product does not support placement targeting
**Obligation ID** UC-003-ALT-UPDATE-CREATIVE-ASSIGNMENTS-04
**Layer** behavioral
**Given** a product that does not support placement-level targeting
**When** the buyer sends `creative_assignments` with `placement_ids`
**Then** the system rejects with `invalid_placement_ids`
**Business Rule** BR-RULE-028 (INV-3: product doesn't support placement)
**Priority** P2

#### Scenario: Creative existence validated for assignments
**Obligation ID** UC-003-ALT-UPDATE-CREATIVE-ASSIGNMENTS-05
**Layer** behavioral
**Given** creative C999 does not exist in the library
**When** the buyer sends `creative_assignments: [{creative_id: C999}]`
**Then** the system returns an error indicating the creative was not found
**Business Rule** Existence check
**Priority** P1

---

### Alt: Update Targeting Overlay
Source: UC-003-alt-targeting.md

#### Scenario: Update targeting overlay on package
**Obligation ID** UC-003-ALT-UPDATE-TARGETING-OVERLAY-01
**Layer** behavioral
**Given** a package with existing targeting and new targeting data
**When** the buyer sends a package update with `targeting_overlay: {geo: {include: ["US"]}}`
**Then** the targeting overlay replaces the existing targeting in `package_config.targeting`
**Business Rule** Direct persistence (replacement)
**Priority** P1

#### Scenario: Targeting overlay not validated (known gap G36)
**Obligation ID** UC-003-ALT-UPDATE-TARGETING-OVERLAY-02
**Layer** behavioral
**Given** invalid targeting data (e.g., unknown fields, conflicting geo)
**When** the buyer sends a targeting overlay update
**Then** the system persists the targeting directly WITHOUT validation (unlike UC-002)
**Business Rule** Known gap G36 (no validation on update)
**Priority** P2 -- gap documentation

#### Scenario: Targeting update has no adapter call
**Obligation ID** UC-003-ALT-UPDATE-TARGETING-OVERLAY-03
**Layer** behavioral
**Given** a valid targeting overlay update
**When** the system processes the update
**Then** targeting changes are persisted to the database only; no adapter call is made
**Business Rule** Divergence from main flow
**Priority** P2

---

### Alt: Manual Approval Required
Source: UC-003-alt-manual.md

#### Scenario: Update enters pending state when manual approval required
**Obligation ID** UC-003-ALT-MANUAL-APPROVAL-REQUIRED-01
**Layer** behavioral
**Given** a tenant where `human_review_required` is true and the operation type matches `manual_approval_operations`
**When** the buyer sends a valid update request
**Then** the system creates a workflow step with status `requires_approval`, returns protocol envelope with status `submitted`, `implementation_date: null`, and `workflow_step_id`
**Business Rule** BR-RULE-017 (INV-2/INV-3: manual approval triggers pending state)
**Priority** P1

#### Scenario: Manual approval response has implementation_date null
**Obligation ID** UC-003-ALT-MANUAL-APPROVAL-REQUIRED-02
**Layer** behavioral
**Given** a manual approval scenario
**When** the system returns the response
**Then** `implementation_date` is `null` (pending approval)
**Business Rule** POST-S7 (implementation date is null)
**Priority** P1

#### Scenario: Adapter execution deferred until seller approval
**Obligation ID** UC-003-ALT-MANUAL-APPROVAL-REQUIRED-03
**Layer** behavioral
**Given** a pending update awaiting approval
**When** the seller approves the update
**Then** the adapter is called at approval time (not at request time)
**Business Rule** BR-RULE-020 (INV-3: manual path = records persisted in pending state, adapter deferred)
**Priority** P1

#### Scenario: Seller rejects the update
**Obligation ID** UC-003-ALT-MANUAL-APPROVAL-REQUIRED-04
**Layer** behavioral
**Given** a pending update awaiting approval
**When** the seller rejects the update
**Then** the buyer is notified via webhook with the rejection reason; the update is NOT applied
**Business Rule** Alt-manual step 14
**Priority** P2

#### Scenario: Buyer can poll task status while pending
**Obligation ID** UC-003-ALT-MANUAL-APPROVAL-REQUIRED-05
**Layer** behavioral
**Given** a submitted update with a `workflow_step_id`
**When** the buyer polls `tasks/get` with the workflow step ID
**Then** the buyer can see the current approval status
**Business Rule** Alt-manual response note
**Priority** P2

---

### Extension *a: Authentication Error
Source: UC-003-ext-a.md

#### Scenario: No principal in context
**Obligation ID** UC-003-EXT-A-01
**Layer** behavioral
**Given** a request without valid authentication (no principal_id)
**When** the system processes the update request
**Then** the system returns error `authentication_error` with protocol envelope status `failed`
**Business Rule** POST-F1 (state unchanged), POST-F2 (buyer knows error)
**Priority** P0 -- security gate

#### Scenario: Principal not found in database
**Obligation ID** UC-003-EXT-A-02
**Layer** behavioral
**Given** a request with a principal_id that does not exist in the database
**When** the system processes the update request
**Then** the system returns error `authentication_error`
**Business Rule** POST-F1, POST-F2
**Priority** P0 -- security gate

#### Scenario: System state unchanged on auth failure
**Obligation ID** UC-003-EXT-A-03
**Layer** behavioral
**Given** an authentication failure
**When** the error is returned
**Then** no media buy, package, or creative records are modified
**Business Rule** POST-F1
**Priority** P1

---

### Extension *b: Media Buy Not Found
Source: UC-003-ext-b.md

#### Scenario: media_buy_id does not resolve
**Obligation ID** UC-003-EXT-B-01
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer sends `update_media_buy` with a non-existent `media_buy_id`
**Then** the system returns error `media_buy_not_found`
**Business Rule** BR-RULE-021
**Priority** P1

#### Scenario: buyer_ref does not resolve
**Obligation ID** UC-003-EXT-B-02
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer sends `update_media_buy` with a non-existent `buyer_ref`
**Then** the system returns error `media_buy_not_found`
**Business Rule** BR-RULE-021
**Priority** P1

#### Scenario: Both media_buy_id and buyer_ref provided (XOR violation)
**Obligation ID** UC-003-EXT-B-03
**Layer** schema
**Given** an authenticated buyer
**When** the buyer sends `update_media_buy` with BOTH `media_buy_id` and `buyer_ref`
**Then** schema validation rejects the request
**Business Rule** BR-RULE-021 (INV-2: both provided = rejected)
**Priority** P1

#### Scenario: Neither media_buy_id nor buyer_ref provided (XOR violation)
**Obligation ID** UC-003-EXT-B-04
**Layer** schema
**Given** an authenticated buyer
**When** the buyer sends `update_media_buy` with NEITHER `media_buy_id` nor `buyer_ref`
**Then** schema validation rejects the request
**Business Rule** BR-RULE-021 (INV-3: neither provided = rejected)
**Priority** P1

---

### Extension *c: Ownership Mismatch
Source: UC-003-ext-c.md

#### Scenario: Principal does not own the media buy
**Obligation ID** UC-003-EXT-C-01
**Layer** behavioral
**Given** an authenticated principal (buyer A) and a media buy owned by a different principal (buyer B)
**When** buyer A sends `update_media_buy` for buyer B's media buy
**Then** the system returns a permission error (PermissionError from `_verify_principal()`)
**Business Rule** PRE-BIZ3 (ownership verification)
**Priority** P0 -- security gate

#### Scenario: State unchanged on ownership mismatch
**Obligation ID** UC-003-EXT-C-02
**Layer** behavioral
**Given** an ownership mismatch error
**When** the error is returned
**Then** the media buy remains unmodified
**Business Rule** POST-F1
**Priority** P1

---

### Extension *d: Budget Validation
Source: UC-003-ext-d.md

#### Scenario: Zero budget rejected for campaign-level update
**Obligation ID** UC-003-EXT-D-01
**Layer** behavioral
**Given** an authenticated buyer updating campaign budget
**When** the buyer provides `budget: 0`
**Then** the system returns error `invalid_budget`
**Business Rule** BR-RULE-008 (INV-2: budget <= 0)
**Priority** P1

#### Scenario: Negative budget rejected for campaign-level update
**Obligation ID** UC-003-EXT-D-02
**Layer** behavioral
**Given** an authenticated buyer updating campaign budget
**When** the buyer provides `budget: -500`
**Then** the system returns error `invalid_budget`
**Business Rule** BR-RULE-008 (INV-2)
**Priority** P2

---

### Extension *e: Date Range Invalid
Source: UC-003-ext-e.md

#### Scenario: end_time equals start_time
**Obligation ID** UC-003-EXT-E-01
**Layer** behavioral
**Given** a media buy with start_time=2026-03-01
**When** the buyer sends `update_media_buy` with `end_time=2026-03-01`
**Then** the system returns error `invalid_date_range`
**Business Rule** BR-RULE-013 (INV-3: end_time <= start_time)
**Priority** P1

#### Scenario: end_time before start_time
**Obligation ID** UC-003-EXT-E-02
**Layer** behavioral
**Given** a media buy with start_time=2026-03-15
**When** the buyer sends `update_media_buy` with `end_time=2026-03-01`
**Then** the system returns error `invalid_date_range`
**Business Rule** BR-RULE-013 (INV-3)
**Priority** P1

#### Scenario: end_time before existing start_time when only end_time updated
**Obligation ID** UC-003-EXT-E-03
**Layer** behavioral
**Given** a media buy with start_time=2026-03-15
**When** the buyer sends `update_media_buy` with only `end_time=2026-03-10` (no start_time)
**Then** the system uses existing start_time for comparison and returns `invalid_date_range`
**Business Rule** BR-RULE-013 (existing value used for omitted field)
**Priority** P1

#### Scenario: new start_time after existing end_time when only start_time updated
**Obligation ID** UC-003-EXT-E-04
**Layer** behavioral
**Given** a media buy with end_time=2026-03-31
**When** the buyer sends `update_media_buy` with only `start_time=2026-04-15` (no end_time)
**Then** the system uses existing end_time for comparison and returns `invalid_date_range`
**Business Rule** BR-RULE-013
**Priority** P1

---

### Extension *f: Currency Not Supported
Source: UC-003-ext-f.md

#### Scenario: Media buy currency not in tenant config
**Obligation ID** UC-003-EXT-F-01
**Layer** behavioral
**Given** a media buy with currency=GBP and a tenant that only supports USD
**When** the buyer sends an update that triggers currency validation
**Then** the system returns error `currency_not_supported`
**Business Rule** Currency tenant configuration
**Priority** P2

---

### Extension *g: Daily Spend Cap Exceeded
Source: UC-003-ext-g.md

#### Scenario: Updated budget exceeds daily spend cap
**Obligation ID** UC-003-EXT-G-01
**Layer** behavioral
**Given** a package with a 10-day flight and max_daily_package_spend=$500
**When** the buyer updates the package budget to $10,000 (daily=$1,000)
**Then** the system returns error `budget_limit_exceeded`
**Business Rule** BR-RULE-012 (INV-2: daily > max)
**Priority** P1

#### Scenario: Daily spend calculation uses minimum 1 day for flight
**Obligation ID** UC-003-EXT-G-02
**Layer** schema
**Given** a media buy with same-day start and end (0 flight days)
**When** daily spend is calculated
**Then** the system uses minimum 1 day for the divisor (not zero)
**Business Rule** BR-RULE-012 (INV-4: minimum 1 day)
**Priority** P2

---

### Extension *h: Missing Package ID
Source: UC-003-ext-h.md

#### Scenario: Package update without package_id
**Obligation ID** UC-003-EXT-H-01
**Layer** behavioral
**Given** a valid update request with a packages array entry
**When** the package entry has no `package_id` (and no `buyer_ref`)
**Then** the system returns error `missing_package_id`
**Business Rule** PRE-BIZ7 (package XOR identification)
**Priority** P1

#### Scenario: Known gap -- buyer_ref at package level may not be implemented (G38)
**Obligation ID** UC-003-EXT-H-02
**Layer** behavioral
**Given** a package update with `buyer_ref` instead of `package_id`
**When** the system attempts to resolve the package
**Then** the behavior may vary (code requires `package_id` per known gap G38)
**Business Rule** Known gap G38
**Priority** P3 -- gap documentation

---

### Extension *i: Creative IDs Not Found
Source: UC-003-ext-i.md

#### Scenario: One creative ID not found in library
**Obligation ID** UC-003-EXT-I-01
**Layer** behavioral
**Given** creative C1 exists but C999 does not
**When** the buyer sends `creative_ids: [C1, C999]`
**Then** the system returns error `creatives_not_found` with C999 in the missing list
**Business Rule** PRE-BIZ8
**Priority** P1

#### Scenario: All creative IDs not found
**Obligation ID** UC-003-EXT-I-02
**Layer** behavioral
**Given** no referenced creatives exist
**When** the buyer sends `creative_ids: [C999, C998]`
**Then** the system returns error `creatives_not_found` listing all missing IDs
**Business Rule** PRE-BIZ8
**Priority** P2

---

### Extension *j: Creative Validation Failure
Source: UC-003-ext-j.md

#### Scenario: Creative in error state
**Obligation ID** UC-003-EXT-J-01
**Layer** behavioral
**Given** creative C1 has status `error`
**When** the buyer sends `creative_ids: [C1]`
**Then** the system returns error `INVALID_CREATIVES` with C1's error state details
**Business Rule** BR-RULE-026 (INV-2)
**Priority** P1

#### Scenario: Creative in rejected state
**Obligation ID** UC-003-EXT-J-02
**Layer** behavioral
**Given** creative C1 has status `rejected`
**When** the buyer sends `creative_ids: [C1]`
**Then** the system returns error `INVALID_CREATIVES` with C1's rejected state details
**Business Rule** BR-RULE-026 (INV-3)
**Priority** P1

#### Scenario: Creative format mismatch
**Obligation ID** UC-003-EXT-J-03
**Layer** behavioral
**Given** a package for a "display" product and creative C1 has "video" format
**When** the buyer sends `creative_ids: [C1]`
**Then** the system returns error `INVALID_CREATIVES` with format mismatch details
**Business Rule** BR-RULE-026 (INV-4)
**Priority** P1

#### Scenario: All validation errors collected and returned together
**Obligation ID** UC-003-EXT-J-04
**Layer** behavioral
**Given** C1 is in `error` state, C2 has format mismatch
**When** the buyer sends `creative_ids: [C1, C2]`
**Then** the system returns `INVALID_CREATIVES` with BOTH errors (collected, not fail-fast)
**Business Rule** BR-RULE-026 (all errors collected)
**Priority** P2

---

### Extension *k: Creative Sync Failure
Source: UC-003-ext-k.md

#### Scenario: Inline creative upload fails
**Obligation ID** UC-003-EXT-K-01
**Layer** behavioral
**Given** a package update with inline creatives
**When** `_sync_creatives_impl()` fails during upload
**Then** the system returns error `creative_sync_failed` with failure details
**Business Rule** POST-F1 (state unchanged)
**Priority** P1

#### Scenario: Media buy unmodified on sync failure
**Obligation ID** UC-003-EXT-K-02
**Layer** behavioral
**Given** a creative sync failure
**When** the error is returned
**Then** the media buy and package records remain unchanged (partial uploads may leave orphan assets)
**Business Rule** POST-F1
**Priority** P1

---

### Extension *l: Package Not Found
Source: UC-003-ext-l.md

#### Scenario: Package ID does not belong to media buy
**Obligation ID** UC-003-EXT-L-01
**Layer** behavioral
**Given** a media buy with packages [P1, P2] and package P99 belongs to a different media buy
**When** the buyer sends a package update with `package_id: P99`
**Then** the system returns error `package_not_found`
**Business Rule** Step 8 (package resolution within media buy)
**Priority** P1

#### Scenario: Package ID does not exist at all
**Obligation ID** UC-003-EXT-L-02
**Layer** behavioral
**Given** a media buy and a package_id that exists nowhere
**When** the buyer sends a package update with the invalid package_id
**Then** the system returns error `package_not_found`
**Business Rule** Step 8
**Priority** P1

---

### Extension *m: Invalid Placement IDs
Source: UC-003-ext-m.md

#### Scenario: Placement ID not valid for product
**Obligation ID** UC-003-EXT-M-01
**Layer** behavioral
**Given** a product with placements [P1, P2] and creative assignment with `placement_ids: [P1, P999]`
**When** the system validates placement IDs
**Then** the system returns error `invalid_placement_ids`
**Business Rule** BR-RULE-028 (INV-2)
**Priority** P1

#### Scenario: Placement targeting on unsupported product
**Obligation ID** UC-003-EXT-M-02
**Layer** behavioral
**Given** a product that does not support placement-level targeting
**When** the buyer includes `placement_ids` in creative assignments
**Then** the system returns error `invalid_placement_ids`
**Business Rule** BR-RULE-028 (INV-3)
**Priority** P2

---

### Extension *n: Insufficient Privileges
Source: UC-003-ext-n.md

#### Scenario: Non-admin principal attempts admin-only operation
**Obligation ID** UC-003-EXT-N-01
**Layer** behavioral
**Given** a non-admin principal and an adapter operation requiring admin privileges (e.g., activating guaranteed items in GAM)
**When** the adapter checks privilege requirements
**Then** the system returns error `insufficient_privileges`
**Business Rule** Adapter privilege check
**Priority** P2

---

### Extension *o: Adapter/Workflow Failure
Source: UC-003-ext-o.md

#### Scenario: Adapter returns network error
**Obligation ID** UC-003-EXT-O-01
**Layer** behavioral
**Given** a valid update request that passes all validation
**When** the adapter call fails due to network error
**Then** the system returns error `activation_workflow_failed` with adapter error details
**Business Rule** BR-RULE-020 (INV-2: adapter error = no records created)
**Priority** P1

#### Scenario: Adapter returns API quota error
**Obligation ID** UC-003-EXT-O-02
**Layer** behavioral
**Given** a valid update request
**When** the adapter call fails due to API quota exceeded
**Then** the system returns error `activation_workflow_failed`
**Business Rule** BR-RULE-020
**Priority** P2

#### Scenario: Workflow creation failure during manual approval setup
**Obligation ID** UC-003-EXT-O-03
**Layer** behavioral
**Given** a valid update request where manual approval is required
**When** the workflow step creation fails (database error)
**Then** the system returns error `workflow_creation_failed`
**Business Rule** Ext-o step 7b
**Priority** P2

#### Scenario: All-or-nothing semantics -- no DB changes on adapter failure
**Obligation ID** UC-003-EXT-O-04
**Layer** behavioral
**Given** an adapter execution failure during auto-approval
**When** the error is returned
**Then** no database records are updated (budget, dates, creative assignments all unchanged)
**Business Rule** BR-RULE-020 (INV-2), POST-F1
**Priority** P0 -- data integrity

#### Scenario: Error response is atomic (error only, no success fields)
**Obligation ID** UC-003-EXT-O-05
**Layer** behavioral
**Given** any error case in UC-003
**When** the system returns an error response
**Then** the response contains error fields only (no success fields like `affected_packages`)
**Business Rule** BR-RULE-018 (INV-2: error response has no success fields; INV-3: mixed = schema violation)
**Priority** P1
