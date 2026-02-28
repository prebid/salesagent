# UC-002: Create Media Buy -- Test Obligations

## Source

- Requirements: `/Users/konst/projects/adcp-req/docs/requirements/use-cases/UC-002-create-media-buy/`
- Use Case ID: BR-UC-002
- Files analyzed:
  - UC-002.md (overview, preconditions, postconditions, business rules)
  - UC-002-main-mcp.md (main flow, 19 steps)
  - UC-002-alt-asap.md (ASAP start timing)
  - UC-002-alt-creatives.md (inline creative upload)
  - UC-002-alt-manual.md (manual approval / HITL)
  - UC-002-alt-proposal.md (proposal-based ordering)
  - UC-002-ext-a.md through UC-002-ext-q.md (17 error extensions)
- Business rules: BR-RULE-006, 008, 009, 010, 011, 012, 013, 014, 015, 017, 018, 020, 026

## 3.6 Upgrade Impact

### salesagent-7gnv: MediaBuy Boundary Drops Fields

The adcp 3.6.0 library `CreateMediaBuyRequest` now includes fields that salesagent's MCP/A2A
boundary may not propagate to the adapter or persist in the database:

| Field | In adcp 3.6 Request | In adcp 3.6 Success Response | Impact |
|-------|---------------------|------------------------------|--------|
| `buyer_campaign_ref` | Yes (optional) | Yes (optional) | Request field accepted by Pydantic but not stored in DB `media_buys` table; retrieved from `raw_request` JSON on list. Response field exists on `AdCPCreateMediaBuySuccess` but adapters do not populate it. |
| `ext` | Yes (optional, `ExtensionObject`) | Yes (optional, `ExtensionObject`) | Accepted at boundary but not propagated through pipeline or stored. Round-trip broken: buyer sends ext, response has no ext. |
| `account_id` | Yes (optional) | No (response has `account` object) | Accepted at boundary but ignored in most validation. Not stored. Not propagated to response. |
| `creative_deadline` | No (request-side) | Yes (optional, `AwareDatetime`) | Adapters set `creative_deadline` on success response but salesagent boundary may not pass it through to the MCP/A2A caller. |
| `account` | No (request-side) | Yes (optional, `Account` object) | New in 3.6 response. Adapters do not populate it. |
| `sandbox` | No (request-side) | Yes (optional, `bool`) | New in 3.6 response. Adapters do not populate it. |

### salesagent-goy2: Creative Extends Wrong adcp Type

The salesagent `Creative` model may extend the wrong adcp base type, causing schema
mismatches when creatives are included in media buy requests or responses.

### salesagent-mq3n: PricingOption Delivery Lookup String vs Integer PK

When resolving `pricing_option_id` during step 9 (pricing validation), the system may
compare string IDs against integer primary keys, causing pricing options to never match
and all pricing lookups to fail.

### salesagent-qo8a: 6 Product Fields Missing from DB (FIXED)

Product fields added in adcp 3.6 now have database columns. This was already fixed.
Relevant to UC-002 because product lookups during validation (step 7) now include
these fields.

---

## Test Scenarios

### Precondition Tests

#### Scenario: PRE-C1 -- System Operational
**Obligation ID** UC-002-PRECOND-01
**Layer** behavioral
**Given** the Seller Agent is not running or not accepting requests
**When** a Buyer Agent sends a `create_media_buy` request
**Then** the request fails with a connection or service unavailable error
**Priority:** P1

#### Scenario: PRE-C2 -- Buyer Authenticated
**Obligation ID** UC-002-PRECOND-02
**Layer** behavioral
**Given** a `create_media_buy` request without authentication credentials
**When** the request reaches the system
**Then** the system returns an `authentication_error` before any processing
**Business Rule:** (precondition)
**Priority:** P0

#### Scenario: PRE-C3 -- Tenant Setup Complete
**Obligation ID** UC-002-PRECOND-03
**Layer** behavioral
**Given** a tenant that has NOT completed the setup checklist
**When** an authenticated Buyer sends a `create_media_buy` request
**Then** the system returns an error indicating the tenant setup is incomplete
**Priority:** P1

---

### Main Flow: Auto-Approved Media Buy (Package-Based, MCP)
Source: UC-002-main-mcp.md

#### Scenario: Happy Path -- Full Pipeline Auto-Approved
**Obligation ID** UC-002-MAIN-01
**Layer** behavioral
**Given** an authenticated Buyer with a configured tenant (auto_create_enabled=true)
**And** a valid request with buyer_ref, packages (valid product_ids, budgets, pricing_option_ids), brand, start_time (future), end_time (after start_time)
**When** the Buyer Agent sends `create_media_buy` via MCP
**Then** the system creates a media buy with status `pending_activation` or `active`
**And** the response contains `media_buy_id`, `buyer_ref`, and `packages` array
**And** the protocol envelope status is `completed`
**And** each package has `package_id`, `product_id`, `budget`, and pricing details
**Business Rule:** POST-S1, POST-S2, POST-S3, POST-S4, POST-S5, POST-S6
**Priority:** P0

#### Scenario: Step 1 -- Request Contains All Required Fields
**Obligation ID** UC-002-MAIN-02
**Layer** schema
**Given** a well-formed `create_media_buy` request
**When** the request is submitted
**Then** the system accepts the request with `buyer_ref`, `packages`, `brand`, `start_time`, `end_time`
**And** optional fields `account_id`, `reporting_webhook`, `artifact_webhook` are accepted if present
**Priority:** P0

#### Scenario: Step 2 -- Authentication Extracts principal_id
**Obligation ID** UC-002-MAIN-03
**Layer** behavioral
**Given** a valid MCP request with authentication credentials
**When** the system processes authentication
**Then** it extracts `principal_id` from context and validates the principal exists in database
**Priority:** P0

#### Scenario: Step 3 -- Tenant Setup Validation
**Obligation ID** UC-002-MAIN-04
**Layer** behavioral
**Given** an authenticated request for a tenant
**When** the system checks tenant setup
**Then** it verifies setup checklist tasks are complete before proceeding
**Priority:** P1

#### Scenario: Step 4 -- Ordering Mode Detection (Package-Based)
**Obligation ID** UC-002-MAIN-05
**Layer** behavioral
**Given** a request WITHOUT `proposal_id`
**When** the system checks ordering mode
**Then** it proceeds with package-based validation
**And** does NOT attempt proposal resolution
**Priority:** P0

#### Scenario: Step 4 -- Ordering Mode Detection (Proposal-Based Branch)
**Obligation ID** UC-002-MAIN-06
**Layer** schema
**Given** a request WITH `proposal_id`
**When** the system checks ordering mode
**Then** it branches to the proposal-based flow (alt-proposal)
**Priority:** P0

#### Scenario: Step 5 -- Total Budget Positive
**Obligation ID** UC-002-MAIN-07
**Layer** schema
**Given** packages with budgets summing to a positive total
**When** the system validates total budget
**Then** validation passes and proceeds to date/time validation
**Business Rule:** BR-RULE-008 INV-1
**Priority:** P0

#### Scenario: Step 6 -- DateTime Valid (Future Start, End After Start)
**Obligation ID** UC-002-MAIN-08
**Layer** schema
**Given** `start_time` is a future ISO 8601 datetime and `end_time` is after `start_time`
**When** the system validates timing
**Then** validation passes
**Business Rule:** BR-RULE-013 INV-1
**Priority:** P0

#### Scenario: Step 7 -- Package Validation (Products Exist, No Duplicates)
**Obligation ID** UC-002-MAIN-09
**Layer** behavioral
**Given** packages with valid `product_id` values that exist in tenant catalog, and no duplicates
**When** the system validates packages
**Then** validation passes
**Business Rule:** BR-RULE-010 INV-1
**Priority:** P0

#### Scenario: Step 8 -- Currency Validation (Supported by Tenant and Ad Server)
**Obligation ID** UC-002-MAIN-10
**Layer** behavioral
**Given** all packages use the same currency via pricing options
**And** the currency is configured in tenant's CurrencyLimit table
**And** the currency is supported by the ad server
**When** the system validates currency
**Then** validation passes
**Business Rule:** BR-RULE-009 INV-1
**Priority:** P0

#### Scenario: Step 9 -- Pricing Model Resolution
**Obligation ID** UC-002-MAIN-11
**Layer** schema
**Given** each package has a `pricing_option_id` that matches a pricing option on its product
**When** the system resolves pricing
**Then** the pricing model is selected for each package
**And** for auction models, bid_price is validated against floor_price
**Business Rule:** BR-RULE-006
**Priority:** P0

#### Scenario: Step 10 -- Minimum Spend Validation
**Obligation ID** UC-002-MAIN-12
**Layer** schema
**Given** each package budget >= the product's `min_spend_per_package`
**When** the system validates minimum spend
**Then** validation passes
**Business Rule:** BR-RULE-011 INV-1
**Priority:** P1

#### Scenario: Step 11 -- Maximum Daily Spend Validation
**Obligation ID** UC-002-MAIN-13
**Layer** schema
**Given** each package's daily budget (budget / flight_days) <= `max_daily_package_spend`
**When** the system validates daily spend
**Then** validation passes
**Business Rule:** BR-RULE-012 INV-1
**Priority:** P1

#### Scenario: Step 12 -- Targeting Overlay Validation
**Obligation ID** UC-002-MAIN-14
**Layer** behavioral
**Given** packages with valid targeting overlays (known fields, no managed-only dimensions, no geo overlap)
**When** the system validates targeting
**Then** validation passes
**Business Rule:** BR-RULE-014
**Priority:** P1

#### Scenario: Step 13 -- Auto-Approval Determination
**Obligation ID** UC-002-MAIN-15
**Layer** behavioral
**Given** tenant `auto_create_enabled=true` and product config allows auto-approval
**When** the system determines approval path
**Then** it selects auto-approval and proceeds to adapter execution
**Business Rule:** BR-RULE-017 INV-1
**Priority:** P0

#### Scenario: Step 14 -- Inline Creative Processing (None Present)
**Obligation ID** UC-002-MAIN-16
**Layer** schema
**Given** no inline creatives in the request
**When** the system checks for inline creatives
**Then** it skips creative upload and proceeds to format validation
**Priority:** P1

#### Scenario: Step 15 -- Format ID Validation
**Obligation ID** UC-002-MAIN-17
**Layer** behavioral
**Given** packages with valid FormatId objects (registered agents, existing formats)
**When** the system validates format IDs
**Then** validation passes
**Priority:** P1

#### Scenario: Step 16 -- Creative Validation (Reference Creatives Valid)
**Obligation ID** UC-002-MAIN-18
**Layer** schema
**Given** reference creatives with valid URLs and dimensions
**When** the system validates creatives
**Then** validation passes
**Business Rule:** BR-RULE-015 INV-1
**Priority:** P1

#### Scenario: Step 17 -- Adapter Execution Success
**Obligation ID** UC-002-MAIN-19
**Layer** behavioral
**Given** all validation passes
**When** the system calls the ad server adapter
**Then** the adapter creates a campaign, line items, and targeting in the ad server
**And** the adapter returns a success response with `media_buy_id`
**Business Rule:** BR-RULE-020 INV-1
**Priority:** P0

#### Scenario: Step 18 -- Persistence After Adapter Success
**Obligation ID** UC-002-MAIN-20
**Layer** behavioral
**Given** the adapter returned success
**When** the system persists the media buy
**Then** it creates a media buy record with status `pending_activation` or `active`
**And** it creates package records and creative assignment records
**Business Rule:** BR-RULE-020 INV-1
**Priority:** P0

#### Scenario: Step 19 -- Response in Protocol Envelope
**Obligation ID** UC-002-MAIN-21
**Layer** behavioral
**Given** the media buy was created and persisted successfully
**When** the system returns the response
**Then** it wraps the `CreateMediaBuySuccess` in a protocol envelope with status `completed`
**And** the response is atomic (success data only, no error fields)
**Business Rule:** BR-RULE-018 INV-1
**Priority:** P0

---

### 3.6 Upgrade: Boundary Field Propagation (salesagent-7gnv)

#### Scenario: buyer_campaign_ref Accepted at Request Boundary
**Obligation ID** UC-002-UPG-01
**Layer** behavioral
**Given** a `create_media_buy` request with `buyer_campaign_ref: "CAMP-2024-Q1"`
**When** the request is parsed by the system
**Then** the `buyer_campaign_ref` field is accepted (not rejected by validation)
**And** it is stored or propagated such that it can be returned in `list_media_buys`
**Business Rule:** (3.6 field propagation)
**Priority:** P1 (salesagent-7gnv)

#### Scenario: buyer_campaign_ref Roundtrip Through Create and List
**Obligation ID** UC-002-UPG-02
**Layer** behavioral
**Given** a media buy created with `buyer_campaign_ref: "CAMP-2024-Q1"`
**When** the buyer calls `list_media_buys`
**Then** the returned media buy includes `buyer_campaign_ref: "CAMP-2024-Q1"`
**Priority:** P1 (salesagent-7gnv)

#### Scenario: buyer_campaign_ref in Success Response
**Obligation ID** UC-002-UPG-03
**Layer** behavioral
**Given** a media buy created with `buyer_campaign_ref: "CAMP-2024-Q1"`
**When** the `CreateMediaBuySuccess` response is serialized
**Then** `buyer_campaign_ref` appears in the response JSON
**Priority:** P1 (salesagent-7gnv)

#### Scenario: ext Field Accepted at Request Boundary
**Obligation ID** UC-002-UPG-04
**Layer** behavioral
**Given** a `create_media_buy` request with `ext: {"custom_field": "value"}`
**When** the request is parsed
**Then** the `ext` field is accepted (not rejected by extra="forbid")
**And** the `ExtensionObject` is propagated to the response
**Priority:** P1 (salesagent-7gnv)

#### Scenario: ext Field Roundtrip
**Obligation ID** UC-002-UPG-05
**Layer** behavioral
**Given** a media buy created with `ext: {"custom_field": "value"}`
**When** the `CreateMediaBuySuccess` response is returned
**Then** `ext` is present in the response with the same data
**Priority:** P1 (salesagent-7gnv)

#### Scenario: account_id Accepted at Request Boundary
**Obligation ID** UC-002-UPG-06
**Layer** schema
**Given** a `create_media_buy` request with `account_id: "acc_123"`
**When** the request is parsed
**Then** the `account_id` field is accepted (not rejected by validation)
**Priority:** P2 (salesagent-7gnv)

#### Scenario: account Field in Success Response
**Obligation ID** UC-002-UPG-07
**Layer** behavioral
**Given** the adcp 3.6 `CreateMediaBuySuccess` has an optional `account` field
**When** a media buy is created with `account_id` in the request
**Then** the response should include an `account` object if the seller supports account-based isolation
**Priority:** P2 (salesagent-7gnv)

#### Scenario: creative_deadline in Success Response
**Obligation ID** UC-002-UPG-08
**Layer** schema
**Given** an adapter that sets `creative_deadline` on the success response
**When** the response is serialized at the MCP/A2A boundary
**Then** `creative_deadline` is present in the response as an ISO 8601 datetime
**Priority:** P2 (salesagent-7gnv)

#### Scenario: sandbox Flag in Success Response
**Obligation ID** UC-002-UPG-09
**Layer** behavioral
**Given** the adcp 3.6 `CreateMediaBuySuccess` has an optional `sandbox` field
**When** the system returns a success response
**Then** `sandbox` is present if the tenant is in sandbox mode, absent otherwise
**Priority:** P3 (salesagent-7gnv)

---

### Alternative Flow: ASAP Start Timing
Source: UC-002-alt-asap.md

#### Scenario: ASAP Start Time Resolution
**Obligation ID** UC-002-ALT-ASAP-START-TIMING-01
**Layer** behavioral
**Given** a request with `start_time: "asap"` (lowercase, exact match)
**When** the system validates timing
**Then** it resolves `start_time` to `datetime.now(UTC)`
**And** the past-time check is inherently satisfied
**And** validation proceeds with `end_time` > resolved start time
**Business Rule:** BR-RULE-013 INV-4
**Priority:** P0

#### Scenario: ASAP Persisted as Resolved DateTime
**Obligation ID** UC-002-ALT-ASAP-START-TIMING-02
**Layer** behavioral
**Given** a request with `start_time: "asap"`
**When** the media buy is created successfully
**Then** the persisted `start_time` is a specific datetime, not the literal `"asap"`
**Priority:** P1

#### Scenario: ASAP Flight Days Calculation
**Obligation ID** UC-002-ALT-ASAP-START-TIMING-03
**Layer** behavioral
**Given** a request with `start_time: "asap"` and an `end_time` 7 days from now
**When** the system calculates flight days for daily spend validation
**Then** it uses the resolved start time (now) to compute approximately 7 flight days
**Business Rule:** BR-RULE-012
**Priority:** P1

#### Scenario: ASAP Case Sensitivity -- Wrong Case Rejected
**Obligation ID** UC-002-ALT-ASAP-START-TIMING-04
**Layer** schema
**Given** a request with `start_time: "ASAP"` (uppercase)
**When** the system validates timing
**Then** it does NOT resolve as ASAP
**And** it fails with a date/time validation error (not a valid ISO 8601 datetime)
**Business Rule:** BR-RULE-013 INV-5
**Priority:** P2

#### Scenario: ASAP with end_time Before Now
**Obligation ID** UC-002-ALT-ASAP-START-TIMING-05
**Layer** schema
**Given** a request with `start_time: "asap"` and `end_time` in the past
**When** the system validates timing
**Then** it fails because `end_time` <= resolved start time
**Business Rule:** BR-RULE-013 INV-3
**Priority:** P1

---

### Alternative Flow: With Inline Creatives
Source: UC-002-alt-creatives.md

#### Scenario: Inline Creatives Uploaded and Assigned
**Obligation ID** UC-002-ALT-WITH-INLINE-CREATIVES-01
**Layer** behavioral
**Given** a request with packages containing a `creatives` array (inline creative assets)
**When** the system processes the request
**Then** it uploads the inline creative assets to the creative library
**And** generates `creative_id`s for each uploaded creative
**And** updates package creative assignments with the new IDs
**Business Rule:** BR-RULE-015
**Priority:** P1

#### Scenario: Inline Creative Format Validation
**Obligation ID** UC-002-ALT-WITH-INLINE-CREATIVES-02
**Layer** behavioral
**Given** inline creatives with FormatId objects
**When** the system validates formats
**Then** it verifies the format agent is registered and the format ID exists on the agent
**Priority:** P1

#### Scenario: Generative Format Exempt from URL/Dimension Validation
**Obligation ID** UC-002-ALT-WITH-INLINE-CREATIVES-03
**Layer** schema
**Given** an inline creative with a generative format (has `output_format_ids`)
**When** the system validates creative assets
**Then** it skips URL and dimension validation for that creative
**Business Rule:** BR-RULE-015 INV-3
**Priority:** P1

#### Scenario: Three Mutually Exclusive Creative Modes
**Obligation ID** UC-002-ALT-WITH-INLINE-CREATIVES-04
**Layer** schema
**Given** a package
**When** it specifies creatives
**Then** only ONE of `creative_ids`, `creatives`, or `creative_assignments` should be provided
**Priority:** P2

#### Scenario: Unapproved Creatives May Trigger Manual Approval
**Obligation ID** UC-002-ALT-WITH-INLINE-CREATIVES-05
**Layer** behavioral
**Given** a request with inline creatives that are unapproved
**When** the system determines the approval path
**Then** the presence of unapproved creatives may trigger manual approval (alt-manual)
**Business Rule:** BR-RULE-017
**Priority:** P2

---

### Alternative Flow: Manual Approval Required (HITL)
Source: UC-002-alt-manual.md

#### Scenario: Manual Approval Path -- Tenant Requires Review
**Obligation ID** UC-002-ALT-MANUAL-APPROVAL-REQUIRED-01
**Layer** behavioral
**Given** a tenant with `human_review_required: true`
**And** all validation passes
**When** the system determines approval path
**Then** it enters the manual approval flow
**Business Rule:** BR-RULE-017 INV-2
**Priority:** P0

#### Scenario: Manual Approval Path -- Adapter Requires Review
**Obligation ID** UC-002-ALT-MANUAL-APPROVAL-REQUIRED-02
**Layer** behavioral
**Given** an adapter with `manual_approval_required: true`
**And** all validation passes
**When** the system determines approval path
**Then** it enters the manual approval flow
**Business Rule:** BR-RULE-017 INV-3
**Priority:** P1

#### Scenario: Pending Media Buy Created with Status pending_approval
**Obligation ID** UC-002-ALT-MANUAL-APPROVAL-REQUIRED-03
**Layer** behavioral
**Given** manual approval is required
**When** the system creates the media buy
**Then** it persists with status `pending_approval`
**And** generates a permanent `media_buy_id` (format: `mb_{uuid}`)
**And** creates pending `MediaPackage` records
**And** creates `CreativeAssignment` records if applicable
**Priority:** P0

#### Scenario: Workflow Step Created for Approval
**Obligation ID** UC-002-ALT-MANUAL-APPROVAL-REQUIRED-04
**Layer** behavioral
**Given** manual approval is required
**When** the system creates the media buy
**Then** it creates a workflow step with status `requires_approval`
**And** the workflow step is linked to the media buy
**Priority:** P1

#### Scenario: Seller Notification Sent
**Obligation ID** UC-002-ALT-MANUAL-APPROVAL-REQUIRED-05
**Layer** behavioral
**Given** manual approval is required
**When** the media buy is persisted
**Then** the system sends a Slack notification to the Seller for approval review
**Priority:** P2

#### Scenario: Response Envelope Status is submitted
**Obligation ID** UC-002-ALT-MANUAL-APPROVAL-REQUIRED-06
**Layer** behavioral
**Given** manual approval is required
**When** the system returns the response
**Then** the protocol envelope status is `submitted` (NOT `completed`)
**And** the response includes `media_buy_id` and `workflow_step_id`
**Priority:** P0

#### Scenario: No Adapter Execution Before Approval
**Obligation ID** UC-002-ALT-MANUAL-APPROVAL-REQUIRED-07
**Layer** behavioral
**Given** manual approval is required
**When** the system processes the request
**Then** it does NOT call the ad server adapter
**And** no campaign is created in the ad server until Seller approves
**Business Rule:** BR-RULE-020 INV-3
**Priority:** P0

#### Scenario: Seller Approves -- Adapter Executed
**Obligation ID** UC-002-ALT-MANUAL-APPROVAL-REQUIRED-08
**Layer** behavioral
**Given** a pending media buy awaiting Seller approval
**When** the Seller approves the media buy
**Then** the system executes the ad server adapter
**And** updates the media buy status to `pending_activation` or `active`
**And** notifies the Buyer via webhook
**Priority:** P1

#### Scenario: Seller Rejects -- Buyer Notified
**Obligation ID** UC-002-ALT-MANUAL-APPROVAL-REQUIRED-09
**Layer** behavioral
**Given** a pending media buy awaiting Seller approval
**When** the Seller rejects the media buy
**Then** the system updates the status
**And** notifies the Buyer via webhook with rejection reason
**Priority:** P1

#### Scenario: Buyer Can Poll Approval Progress
**Obligation ID** UC-002-ALT-MANUAL-APPROVAL-REQUIRED-10
**Layer** behavioral
**Given** a pending media buy with `task_id` in the response
**When** the Buyer calls `tasks/get` with the `task_id`
**Then** the Buyer receives the current approval status
**Priority:** P2

---

### Alternative Flow: Proposal-Based Media Buy
Source: UC-002-alt-proposal.md

#### Scenario: Proposal-Based Happy Path
**Obligation ID** UC-002-ALT-PROPOSAL-BASED-MEDIA-01
**Layer** behavioral
**Given** a valid `proposal_id` from a previous `get_products` response
**And** `total_budget` with positive amount and matching currency
**When** the Buyer sends `create_media_buy` with `proposal_id` and `total_budget`
**Then** the system resolves the proposal, derives packages from allocations
**And** creates a media buy with the derived packages
**And** returns success with protocol envelope status `completed`
**Business Rule:** POST-S11
**Priority:** P0

#### Scenario: Step 4 -- Proposal Resolution
**Obligation ID** UC-002-ALT-PROPOSAL-BASED-MEDIA-02
**Layer** behavioral
**Given** a valid, non-expired `proposal_id`
**When** the system resolves the proposal
**Then** it finds the proposal from the previous `get_products` session
**And** validates `expires_at` has not passed
**Priority:** P0

#### Scenario: Step 5 -- Total Budget Validation for Proposal
**Obligation ID** UC-002-ALT-PROPOSAL-BASED-MEDIA-03
**Layer** behavioral
**Given** `total_budget.amount` > 0 and currency matches proposal's guidance
**When** the system validates the total budget
**Then** validation passes
**Business Rule:** BR-RULE-008
**Priority:** P0

#### Scenario: Step 6 -- Package Derivation from Allocations
**Obligation ID** UC-002-ALT-PROPOSAL-BASED-MEDIA-04
**Layer** behavioral
**Given** a resolved proposal with product allocations
**When** the system derives packages
**Then** for each allocation, it creates a package with:
  - `product_id` from the allocation
  - `budget` = `total_budget.amount * allocation_percentage / 100`
  - `pricing_option_id` from allocation or product default
**Priority:** P0

#### Scenario: Step 7 -- DateTime Validation on Proposal Path
**Obligation ID** UC-002-ALT-PROPOSAL-BASED-MEDIA-05
**Layer** schema
**Given** a proposal-based request with valid `start_time` and `end_time`
**When** the system validates timing
**Then** it applies the same date/time rules as the package-based path
**Business Rule:** BR-RULE-013
**Priority:** P1

#### Scenario: Step 8 -- Derived Package Products Still Exist
**Obligation ID** UC-002-ALT-PROPOSAL-BASED-MEDIA-06
**Layer** behavioral
**Given** packages derived from proposal allocations
**When** the system validates product existence
**Then** all products from the proposal still exist in the catalog
**Priority:** P1

#### Scenario: Step 9 -- Derived Package Minimum Spend
**Obligation ID** UC-002-ALT-PROPOSAL-BASED-MEDIA-07
**Layer** schema
**Given** derived packages with budgets calculated from allocation percentages
**When** the system validates minimum spend
**Then** each derived package budget >= product's `min_spend_per_package`
**Business Rule:** BR-RULE-011
**Priority:** P1

#### Scenario: Step 10 -- Derived Package Maximum Daily Spend
**Obligation ID** UC-002-ALT-PROPOSAL-BASED-MEDIA-08
**Layer** schema
**Given** derived packages
**When** the system validates daily spend caps
**Then** each derived package's daily budget <= `max_daily_package_spend`
**Business Rule:** BR-RULE-012
**Priority:** P1

#### Scenario: Proposal-Based Skips Duplicate Product Check
**Obligation ID** UC-002-ALT-PROPOSAL-BASED-MEDIA-09
**Layer** schema
**Given** a proposal-based request
**When** the system validates packages
**Then** it does NOT check for duplicate product_ids (proposal guarantees uniqueness)
**Priority:** P2

#### Scenario: Proposal-Based Skips Per-Package Currency Validation
**Obligation ID** UC-002-ALT-PROPOSAL-BASED-MEDIA-10
**Layer** schema
**Given** a proposal-based request with `total_budget.currency`
**When** the system validates currency
**Then** it uses `total_budget.currency` instead of per-package pricing option currency
**Priority:** P2

#### Scenario: Daypart Targets from Proposal Converted to Targeting Overlays
**Obligation ID** UC-002-ALT-PROPOSAL-BASED-MEDIA-11
**Layer** schema
**Given** a proposal with `daypart_targets` per allocation
**When** the system derives packages
**Then** daypart targets are converted to targeting overlays on derived packages
**Priority:** P2

---

### Extension *a: Budget Validation Failure
Source: UC-002-ext-a.md, BR-RULE-008

#### Scenario: Total Budget is Zero
**Obligation ID** UC-002-EXT-A-01
**Layer** schema
**Given** packages with budgets summing to exactly 0
**When** the system validates total budget
**Then** it returns error: "Invalid budget: 0. Budget must be positive."
**And** error code is `validation_error`
**And** system state is unchanged
**Business Rule:** BR-RULE-008 INV-2
**Priority:** P0

#### Scenario: Total Budget is Negative
**Obligation ID** UC-002-EXT-A-02
**Layer** schema
**Given** packages with budgets summing to a negative value
**When** the system validates total budget
**Then** it returns error with the negative amount and "Budget must be positive" message
**And** error code is `validation_error`
**Business Rule:** BR-RULE-008 INV-2
**Priority:** P0

#### Scenario: Budget Schema vs Code Strictness (G10)
**Obligation ID** UC-002-EXT-A-03
**Layer** schema
**Given** a total budget of exactly 0 (allowed by schema `minimum: 0`)
**When** the system validates
**Then** the system rejects it (code enforces > 0, stricter than schema)
**Business Rule:** BR-RULE-008 (code stricter than schema)
**Priority:** P1

---

### Extension *b: Product Not Found
Source: UC-002-ext-b.md

#### Scenario: One or More Product IDs Not in Catalog
**Obligation ID** UC-002-EXT-B-01
**Layer** behavioral
**Given** packages referencing `product_id` values that do not exist in the tenant's catalog
**When** the system validates products
**Then** it returns error: "Product(s) not found: {missing_ids}"
**And** error code is `validation_error`
**And** system state is unchanged
**Priority:** P0

#### Scenario: No Products Specified (Empty Packages)
**Obligation ID** UC-002-EXT-B-02
**Layer** schema
**Given** a request with no packages (or all packages lack `product_id`)
**When** the system validates products
**Then** it returns error: "At least one product is required."
**Priority:** P0

#### Scenario: Package Missing product_id Field
**Obligation ID** UC-002-EXT-B-03
**Layer** schema
**Given** a package in the request that has no `product_id`
**When** the system validates
**Then** it returns error: "Package {buyer_ref} must specify product_id."
**Priority:** P1

---

### Extension *c: DateTime Validation Failure
Source: UC-002-ext-c.md, BR-RULE-013

#### Scenario: Start Time in the Past
**Obligation ID** UC-002-EXT-C-01
**Layer** schema
**Given** `start_time` is an ISO 8601 datetime that is in the past
**When** the system validates timing
**Then** it returns error: "Invalid start time: {value}. Start time cannot be in the past."
**Business Rule:** BR-RULE-013 INV-2
**Priority:** P0

#### Scenario: End Time Before Start Time
**Obligation ID** UC-002-EXT-C-02
**Layer** schema
**Given** `end_time` is before or equal to `start_time`
**When** the system validates timing
**Then** it returns error: "Invalid time range: end time ({end}) must be after start time ({start})."
**Business Rule:** BR-RULE-013 INV-3
**Priority:** P0

#### Scenario: End Time Equal to Start Time
**Obligation ID** UC-002-EXT-C-03
**Layer** schema
**Given** `end_time` equals `start_time` exactly
**When** the system validates timing
**Then** it rejects (end time must be strictly after start time)
**Business Rule:** BR-RULE-013 INV-3
**Priority:** P1

#### Scenario: Missing start_time
**Obligation ID** UC-002-EXT-C-04
**Layer** schema
**Given** a request with `start_time` null or missing
**When** the system validates timing
**Then** it returns error: "start_time is required"
**Priority:** P0

#### Scenario: Missing end_time
**Obligation ID** UC-002-EXT-C-05
**Layer** schema
**Given** a request with `end_time` null or missing
**When** the system validates timing
**Then** it returns error: "end_time is required"
**Priority:** P0

#### Scenario: Naive Datetime Treated as UTC
**Obligation ID** UC-002-EXT-C-06
**Layer** schema
**Given** a `start_time` without timezone information
**When** the system parses the datetime
**Then** it treats the naive datetime as UTC
**Priority:** P2

---

### Extension *d: Currency Mismatch / Not Supported
Source: UC-002-ext-d.md, BR-RULE-009

#### Scenario: Currency Not in Tenant CurrencyLimit Table
**Obligation ID** UC-002-EXT-D-01
**Layer** behavioral
**Given** packages using a currency that has no `CurrencyLimit` entry for the tenant
**When** the system validates currency
**Then** it returns error: "Currency {code} is not supported by this publisher."
**And** error code is `validation_error`
**Business Rule:** BR-RULE-009 INV-3
**Priority:** P0

#### Scenario: Currency Not Supported by GAM Network
**Obligation ID** UC-002-EXT-D-02
**Layer** behavioral
**Given** packages using a currency supported by the tenant but NOT by the GAM network config
**When** the system validates currency
**Then** it returns error: "Currency {code} is not supported by the GAM network."
**And** the error includes the list of supported currencies
**Priority:** P1

#### Scenario: Mixed Currencies Across Packages (Implicit)
**Obligation ID** UC-002-EXT-D-03
**Layer** schema
**Given** packages where pricing options reference different currencies
**When** the system determines the request currency
**Then** it uses the first package's pricing option currency as the canonical currency
**And** rejects packages with mismatching currencies
**Business Rule:** BR-RULE-009 INV-2
**Priority:** P1

#### Scenario: Currency Fallback Chain
**Obligation ID** UC-002-EXT-D-04
**Layer** schema
**Given** a package without explicit currency in pricing option
**When** the system determines currency
**Then** it follows the fallback chain: pricing option currency -> legacy field -> "USD"
**Priority:** P2

---

### Extension *e: Duplicate Products
Source: UC-002-ext-e.md, BR-RULE-010

#### Scenario: Same product_id in Multiple Packages
**Obligation ID** UC-002-EXT-E-01
**Layer** schema
**Given** two or more packages referencing the same `product_id`
**When** the system validates products
**Then** it returns error: "Duplicate product_id(s) found in packages: {ids}. Each product can only be used once per media buy."
**And** error code is `validation_error`
**Business Rule:** BR-RULE-010 INV-2
**Priority:** P0

#### Scenario: Duplicate Check Not Protocol-Level
**Obligation ID** UC-002-EXT-E-02
**Layer** schema
**Given** the duplicate product_id constraint is code-enforced (G12)
**When** validating against the schema
**Then** the schema allows duplicates but the system rejects them
**Priority:** P2

---

### Extension *f: Targeting Validation Failure
Source: UC-002-ext-f.md, BR-RULE-014

#### Scenario: Unknown Targeting Fields
**Obligation ID** UC-002-EXT-F-01
**Layer** behavioral
**Given** a targeting overlay with field names not recognized by the system (e.g., `mood`)
**When** the system validates targeting
**Then** it returns error: "Targeting validation failed: Unknown targeting field(s): {fields}"
**Business Rule:** BR-RULE-014 INV-1
**Priority:** P1

#### Scenario: Managed-Only Dimension Set by Buyer
**Obligation ID** UC-002-EXT-F-02
**Layer** behavioral
**Given** a targeting overlay that sets a dimension reserved for publisher control
**When** the system validates targeting
**Then** it returns error: "{dimension} is managed by the publisher and cannot be set by buyers"
**Business Rule:** BR-RULE-014 INV-2
**Priority:** P1

#### Scenario: Geo Inclusion/Exclusion Overlap
**Obligation ID** UC-002-EXT-F-03
**Layer** schema
**Given** a targeting overlay with the same geographic value in both include and exclude lists
**When** the system validates targeting
**Then** it returns error: "{value} appears in both inclusion and exclusion for {dimension}"
**Business Rule:** BR-RULE-014 INV-3
**Priority:** P1

#### Scenario: Multiple Targeting Violations Collected
**Obligation ID** UC-002-EXT-F-04
**Layer** schema
**Given** a targeting overlay with violations across all three layers (unknown, managed-only, geo overlap)
**When** the system validates targeting
**Then** all violations are collected and returned together in a single error response
**Priority:** P2

#### Scenario: Empty/Absent Targeting Overlay is Valid
**Obligation ID** UC-002-EXT-F-05
**Layer** schema
**Given** a package with no targeting_overlay (null or absent)
**When** the system validates targeting
**Then** validation passes without error
**Business Rule:** BR-RULE-014 INV-4
**Priority:** P1

---

### Extension *g: Creative Validation Failure
Source: UC-002-ext-g.md, BR-RULE-015

#### Scenario: Reference Creative Missing URL
**Obligation ID** UC-002-EXT-G-01
**Layer** schema
**Given** a reference creative without a URL in its assets
**When** the system validates creatives
**Then** the error "Reference creative missing required URL field in assets" is collected
**Business Rule:** BR-RULE-015 INV-2
**Priority:** P1

#### Scenario: Reference Creative Missing Dimensions
**Obligation ID** UC-002-EXT-G-02
**Layer** schema
**Given** a reference creative without width or height in its assets
**When** the system validates creatives
**Then** the error "Reference creative missing dimensions" is collected
**Business Rule:** BR-RULE-015 INV-2
**Priority:** P1

#### Scenario: Generative Format Exempt from URL Validation
**Obligation ID** UC-002-EXT-G-03
**Layer** schema
**Given** a creative with a generative format (has `output_format_ids`)
**When** the system validates creatives
**Then** it skips URL and dimension validation for that creative
**Business Rule:** BR-RULE-015 INV-3
**Priority:** P1

#### Scenario: All Invalid Creatives Reported Together
**Obligation ID** UC-002-EXT-G-04
**Layer** behavioral
**Given** multiple creatives with validation failures
**When** the system validates creatives
**Then** it collects ALL errors (does not fail-fast) and returns them in a single response
**And** error code is `INVALID_CREATIVES`
**Priority:** P2

---

### Extension *h: Format ID Validation Failure
Source: UC-002-ext-h.md

#### Scenario: Plain String Format ID Rejected
**Obligation ID** UC-002-EXT-H-01
**Layer** schema
**Given** a format ID that is a plain string (e.g., `"banner_300x250"`) instead of a FormatId object
**When** the system validates format IDs
**Then** it returns error: "Plain string format IDs are not supported. Per AdCP spec, format_ids must be FormatId objects with {agent_url, id}."
**And** error code is `FORMAT_VALIDATION_ERROR`
**Priority:** P1

#### Scenario: Unregistered Creative Agent
**Obligation ID** UC-002-EXT-H-02
**Layer** behavioral
**Given** a FormatId object with an `agent_url` that is not registered
**When** the system validates format IDs
**Then** it returns error: "Creative agent not registered: {agent_url}. Registered agents: {list}."
**Priority:** P1

#### Scenario: Format Not Found on Registered Agent
**Obligation ID** UC-002-EXT-H-03
**Layer** behavioral
**Given** a FormatId object with a registered `agent_url` but unknown `id`
**When** the system validates format IDs
**Then** it returns error: "Format not found on agent. agent_url={url}, format_id={id}."
**Priority:** P1

#### Scenario: FormatId Object Missing Required Fields
**Obligation ID** UC-002-EXT-H-04
**Layer** schema
**Given** a FormatId object where `agent_url` or `id` is missing/empty
**When** the system validates format IDs
**Then** it returns error: "FormatId object missing required fields. Both agent_url and id are required."
**Priority:** P1

---

### Extension *i: Authentication Error
Source: UC-002-ext-i.md

#### Scenario: No Principal in Context
**Obligation ID** UC-002-EXT-I-01
**Layer** behavioral
**Given** a `create_media_buy` request without valid authentication (principal_id is null)
**When** the system attempts authentication
**Then** it returns error: "Principal ID not found in context - authentication required"
**And** error code is `authentication_error`
**And** system state is unchanged
**Priority:** P0

#### Scenario: Principal Not Found in Database
**Obligation ID** UC-002-EXT-I-02
**Layer** behavioral
**Given** a request with a `principal_id` that does not match any principal in the database
**When** the system authenticates
**Then** it returns error: "Principal {id} not found" with code `authentication_error`
**Priority:** P0

#### Scenario: Authentication Always Required (No Anonymous Path)
**Obligation ID** UC-002-EXT-I-03
**Layer** behavioral
**Given** a `create_media_buy` request (unlike `get_products` which allows anonymous)
**When** the request has no authentication
**Then** it is ALWAYS rejected (no anonymous path for media buy creation)
**Priority:** P0

---

### Extension *j: Adapter Execution Failure
Source: UC-002-ext-j.md, BR-RULE-020

#### Scenario: Adapter Returns Error
**Obligation ID** UC-002-EXT-J-01
**Layer** behavioral
**Given** all validation passes but the ad server adapter returns `CreateMediaBuyError`
**When** the system processes the adapter response
**Then** it returns the adapter error in the protocol envelope with `status: failed`
**And** error code is `adapter_error`
**And** the error includes adapter-specific details
**Priority:** P0

#### Scenario: No Database Record on Adapter Failure
**Obligation ID** UC-002-EXT-J-02
**Layer** behavioral
**Given** the adapter call fails
**When** the system handles the failure
**Then** no media buy record is persisted in the database
**And** no package records are created
**And** system state is completely unchanged
**Business Rule:** BR-RULE-020 INV-2
**Priority:** P0

#### Scenario: Adapter Failure is All-or-Nothing
**Obligation ID** UC-002-EXT-J-03
**Layer** behavioral
**Given** the adapter call is the critical boundary between validation and execution
**When** the adapter fails
**Then** all prior validation state is discarded
**And** the buyer can retry the same request
**Business Rule:** BR-RULE-018, BR-RULE-020
**Priority:** P0

---

### Extension *k: Maximum Daily Spend Exceeded
Source: UC-002-ext-k.md, BR-RULE-012

#### Scenario: Daily Budget Exceeds Max Daily Cap
**Obligation ID** UC-002-EXT-K-01
**Layer** schema
**Given** a package where `budget / flight_days` exceeds `max_daily_package_spend`
**When** the system validates daily spend
**Then** it returns error with the computed daily budget, the maximum allowed, and an explanation
**And** error code is `validation_error`
**Business Rule:** BR-RULE-012 INV-2
**Priority:** P1

#### Scenario: Flight Days Minimum of 1
**Obligation ID** UC-002-EXT-K-02
**Layer** schema
**Given** a media buy with `start_time` and `end_time` on the same day (0 days apart)
**When** the system calculates flight days
**Then** it uses a minimum of 1 day for the calculation
**Business Rule:** BR-RULE-012 INV-4
**Priority:** P2

#### Scenario: No Max Daily Spend Configured -- Check Skipped
**Obligation ID** UC-002-EXT-K-03
**Layer** behavioral
**Given** a tenant/currency with no `max_daily_package_spend` configured (null)
**When** the system validates daily spend
**Then** the check is skipped and validation passes
**Business Rule:** BR-RULE-012 INV-3
**Priority:** P2

---

### Extension *l: Proposal Not Found or Expired
Source: UC-002-ext-l.md

#### Scenario: Proposal ID Not Found
**Obligation ID** UC-002-EXT-L-01
**Layer** behavioral
**Given** a `proposal_id` that does not match any known proposal
**When** the system resolves the proposal
**Then** it returns error with code `PROPOSAL_NOT_FOUND`
**And** system state is unchanged
**Priority:** P0

#### Scenario: Proposal Expired
**Obligation ID** UC-002-EXT-L-02
**Layer** behavioral
**Given** a `proposal_id` that exists but has `expires_at` in the past
**When** the system resolves the proposal
**Then** it returns error with code `PROPOSAL_EXPIRED`
**And** system state is unchanged
**Priority:** P0

#### Scenario: Recovery -- Fresh Proposal via get_products
**Obligation ID** UC-002-EXT-L-03
**Layer** behavioral
**Given** a failed proposal resolution
**When** the buyer calls `get_products` again
**Then** they receive fresh proposals with valid `proposal_id` values
**Priority:** P2

---

### Extension *m: Proposal Budget Mismatch
Source: UC-002-ext-m.md

#### Scenario: Total Budget Amount <= 0
**Obligation ID** UC-002-EXT-M-01
**Layer** behavioral
**Given** a proposal-based request with `total_budget.amount` <= 0
**When** the system validates the total budget
**Then** it returns error with code `BUDGET_BELOW_MINIMUM`
**Business Rule:** BR-RULE-008
**Priority:** P0

#### Scenario: Total Budget Below Proposal Minimum Guidance
**Obligation ID** UC-002-EXT-M-02
**Layer** schema
**Given** `total_budget.amount` is below the proposal's `total_budget_guidance.min`
**When** the system validates the total budget
**Then** it returns error with code `BUDGET_BELOW_MINIMUM`
**And** error includes the minimum required from proposal guidance
**Priority:** P1

#### Scenario: Total Budget Currency Mismatch with Proposal
**Obligation ID** UC-002-EXT-M-03
**Layer** behavioral
**Given** `total_budget.currency` does not match the proposal's `total_budget_guidance.currency`
**When** the system validates the total budget
**Then** it returns error with code `CURRENCY_MISMATCH`
**Priority:** P1

#### Scenario: Derived Package Below Product min_spend
**Obligation ID** UC-002-EXT-M-04
**Layer** schema
**Given** a valid total budget that, after allocation derivation, produces a package below `min_spend_per_package`
**When** the system validates derived packages
**Then** it returns error identifying which packages are under-funded and the minimum required
**And** error code is `BUDGET_BELOW_MINIMUM`
**Business Rule:** BR-RULE-011
**Priority:** P1

---

### Extension *n: Pricing Option Validation Failure
Source: UC-002-ext-n.md, BR-RULE-006

#### Scenario: Pricing Option ID Not Found on Product
**Obligation ID** UC-002-EXT-N-01
**Layer** behavioral
**Given** a package with `pricing_option_id` that does not exist on the referenced product
**When** the system validates pricing
**Then** it returns error with code `PRICING_ERROR`
**And** the message identifies the package and the invalid pricing_option_id
**Priority:** P0

#### Scenario: Product Has No Pricing Options
**Obligation ID** UC-002-EXT-N-02
**Layer** behavioral
**Given** a product with no pricing options defined
**When** a package references that product
**Then** the system returns error with code `PRICING_ERROR`
**Priority:** P1

#### Scenario: Auction Pricing Without bid_price
**Obligation ID** UC-002-EXT-N-03
**Layer** schema
**Given** a package selecting an auction pricing model (floor_price set) but no `bid_price` provided
**When** the system validates pricing
**Then** it returns error with code `PRICING_ERROR` indicating bid_price is required for auction models
**Business Rule:** BR-RULE-006
**Priority:** P0

#### Scenario: bid_price Below Floor Price
**Obligation ID** UC-002-EXT-N-04
**Layer** schema
**Given** a package with `bid_price` less than the pricing option's `floor_price`
**When** the system validates pricing
**Then** it returns error with code `PRICING_ERROR` indicating bid is below floor
**Priority:** P0

#### Scenario: Fixed Pricing Without Rate
**Obligation ID** UC-002-EXT-N-05
**Layer** schema
**Given** a fixed pricing model selected but the product has no rate defined
**When** the system validates pricing
**Then** it returns error with code `PRICING_ERROR`
**Priority:** P1

#### Scenario: PricingOption XOR Constraint -- Both Fixed and Floor
**Obligation ID** UC-002-EXT-N-06
**Layer** schema
**Given** a pricing option with both `fixed_price` and `floor_price` set
**When** the system validates
**Then** it rejects as invalid (XOR constraint)
**Business Rule:** BR-RULE-006 INV-3
**Priority:** P1

#### Scenario: PricingOption XOR Constraint -- Neither Fixed Nor Floor
**Obligation ID** UC-002-EXT-N-07
**Layer** schema
**Given** a pricing option with neither `fixed_price` nor `floor_price` set
**When** the system validates
**Then** it rejects as invalid (XOR constraint)
**Business Rule:** BR-RULE-006 INV-4
**Priority:** P1

#### Scenario: salesagent-mq3n -- String vs Integer PK Lookup
**Obligation ID** UC-002-EXT-N-08
**Layer** behavioral
**Given** a `pricing_option_id` as a string
**When** the system looks up the pricing option in the database
**Then** it correctly matches the string ID to the database record (not failing due to string/integer mismatch)
**Priority:** P0 (regression: salesagent-mq3n)

---

### Extension *o: Creative Not Found in Library
Source: UC-002-ext-o.md

#### Scenario: Creative IDs Not in Database
**Obligation ID** UC-002-EXT-O-01
**Layer** behavioral
**Given** a package with `creative_assignments` referencing `creative_id` values not in the creative library
**When** the system resolves creative IDs
**Then** it returns error with code `CREATIVES_NOT_FOUND` listing the missing IDs
**And** system state is unchanged
**Business Rule:** BR-RULE-026
**Priority:** P1

---

### Extension *p: Creative Format Mismatch
Source: UC-002-ext-p.md, BR-RULE-026

#### Scenario: Creative Format Does Not Match Product Formats
**Obligation ID** UC-002-EXT-P-01
**Layer** behavioral
**Given** a creative whose `format_id` does not match any of the product's supported `format_ids`
**When** the system validates creative assignments
**Then** it returns error with code `CREATIVE_FORMAT_MISMATCH`
**And** the error identifies the mismatched creative and the product's expected formats
**Business Rule:** BR-RULE-026 INV-4
**Priority:** P1

---

### Extension *q: Creative Upload Failed
Source: UC-002-ext-q.md, BR-RULE-020

#### Scenario: Ad Server Rejects Creative Upload
**Obligation ID** UC-002-EXT-Q-01
**Layer** behavioral
**Given** a media buy successfully created in the ad server (adapter success)
**But** the subsequent creative upload to the ad server platform fails
**When** the system handles the upload failure
**Then** it returns error with code `CREATIVE_UPLOAD_FAILED`
**And** the error includes platform-specific error details
**Priority:** P1

#### Scenario: Partial Execution State on Creative Upload Failure
**Obligation ID** UC-002-EXT-Q-02
**Layer** behavioral
**Given** the media buy order was already created in the ad server
**And** the creative upload fails
**When** the system handles the failure
**Then** the media buy order MAY exist in the ad server (partial execution)
**And** this is a divergence from the all-or-nothing atomicity of ext-j
**Business Rule:** BR-RULE-020 (atomicity concern)
**Priority:** P1

---

### Cross-Cutting: Atomic Response Semantics
Source: BR-RULE-018

#### Scenario: Success Response Contains No Error Fields
**Obligation ID** UC-002-CC-ATOMIC-RESPONSE-SEMANTICS-01
**Layer** behavioral
**Given** a successful media buy creation
**When** the response is serialized
**Then** it contains success fields (`media_buy_id`, `buyer_ref`, `packages`)
**And** does NOT contain an `errors` array
**Business Rule:** BR-RULE-018 INV-1
**Priority:** P0

#### Scenario: Error Response Contains No Success Fields
**Obligation ID** UC-002-CC-ATOMIC-RESPONSE-SEMANTICS-02
**Layer** behavioral
**Given** a failed media buy creation
**When** the response is serialized
**Then** it contains an `errors` array
**And** does NOT contain success fields (`media_buy_id`, `packages`)
**Business Rule:** BR-RULE-018 INV-2
**Priority:** P0

#### Scenario: Response Is Never Both Success and Error
**Obligation ID** UC-002-CC-ATOMIC-RESPONSE-SEMANTICS-03
**Layer** behavioral
**Given** any `create_media_buy` operation
**When** the response is returned
**Then** it is impossible for the response to contain both success fields and error fields
**Business Rule:** BR-RULE-018 INV-3
**Priority:** P0

---

### Cross-Cutting: Adapter Atomicity
Source: BR-RULE-020

#### Scenario: Auto-Approval -- Persistence Only After Adapter Success
**Obligation ID** UC-002-CC-ADAPTER-ATOMICITY-01
**Layer** behavioral
**Given** auto-approval path
**When** the adapter returns success
**Then** media buy, packages, and creative assignments are persisted in the database
**Business Rule:** BR-RULE-020 INV-1
**Priority:** P0

#### Scenario: Auto-Approval -- No Persistence on Adapter Failure
**Obligation ID** UC-002-CC-ADAPTER-ATOMICITY-02
**Layer** behavioral
**Given** auto-approval path
**When** the adapter returns error
**Then** NO database records are created
**Business Rule:** BR-RULE-020 INV-2
**Priority:** P0

#### Scenario: Manual Approval -- Persistence Before Adapter Call
**Obligation ID** UC-002-CC-ADAPTER-ATOMICITY-03
**Layer** behavioral
**Given** manual approval path
**When** the system determines manual approval is required
**Then** records are persisted in pending state BEFORE adapter execution
**And** adapter execution is deferred until Seller approval
**Business Rule:** BR-RULE-020 INV-3
**Priority:** P0

---

### Cross-Cutting: Minimum Spend Per Package
Source: BR-RULE-011

#### Scenario: Package Budget Meets Product Minimum
**Obligation ID** UC-002-CC-MINIMUM-SPEND-PER-01
**Layer** schema
**Given** a package with budget >= product's `min_spend_per_package`
**When** the system validates minimum spend
**Then** validation passes
**Business Rule:** BR-RULE-011 INV-1
**Priority:** P1

#### Scenario: Package Budget Below Product Minimum
**Obligation ID** UC-002-CC-MINIMUM-SPEND-PER-02
**Layer** schema
**Given** a package with budget < product's `min_spend_per_package`
**When** the system validates minimum spend
**Then** it returns error with code `validation_error`
**Business Rule:** BR-RULE-011 INV-2
**Priority:** P1

#### Scenario: No Product min_spend -- Fallback to Currency Limit
**Obligation ID** UC-002-CC-MINIMUM-SPEND-PER-03
**Layer** schema
**Given** a product without `min_spend_per_package` but tenant has `min_package_budget` in CurrencyLimit
**When** the system validates minimum spend
**Then** it uses the currency limit's `min_package_budget` as the floor
**Business Rule:** BR-RULE-011 INV-3
**Priority:** P2

#### Scenario: No Minimum Configured at Any Level
**Obligation ID** UC-002-CC-MINIMUM-SPEND-PER-04
**Layer** schema
**Given** neither product nor currency limit has a minimum spend configured
**When** the system validates minimum spend
**Then** the check is skipped
**Business Rule:** BR-RULE-011 INV-4
**Priority:** P2

---

### Cross-Cutting: Creative Assignment Validation
Source: BR-RULE-026

#### Scenario: Creative in Error State Cannot Be Assigned
**Obligation ID** UC-002-CC-CREATIVE-ASSIGNMENT-VALIDATION-01
**Layer** behavioral
**Given** a creative with status `error`
**When** it is included in creative_assignments for a package
**Then** the system rejects with code `INVALID_CREATIVES`
**Business Rule:** BR-RULE-026 INV-2
**Priority:** P1

#### Scenario: Creative in Rejected State Cannot Be Assigned
**Obligation ID** UC-002-CC-CREATIVE-ASSIGNMENT-VALIDATION-02
**Layer** behavioral
**Given** a creative with status `rejected`
**When** it is included in creative_assignments
**Then** the system rejects with code `INVALID_CREATIVES`
**Business Rule:** BR-RULE-026 INV-3
**Priority:** P1

#### Scenario: Creative in Valid State with Compatible Format
**Obligation ID** UC-002-CC-CREATIVE-ASSIGNMENT-VALIDATION-03
**Layer** behavioral
**Given** a creative in valid state with format matching the product's supported formats
**When** it is included in creative_assignments
**Then** assignment proceeds successfully
**Business Rule:** BR-RULE-026 INV-1
**Priority:** P1

---

### Cross-Cutting: Schema Compliance

#### Scenario: CreateMediaBuyRequest Accepts All adcp 3.6 Fields
**Obligation ID** UC-002-CC-SCHEMA-COMPLIANCE-01
**Layer** schema
**Given** a request with all adcp 3.6 fields: `account_id`, `artifact_webhook`, `brand`, `buyer_campaign_ref`, `buyer_ref`, `context`, `end_time`, `ext`, `packages`, `po_number`, `proposal_id`, `reporting_webhook`, `start_time`, `total_budget`
**When** the request is parsed by the salesagent `CreateMediaBuyRequest` model
**Then** all fields are accepted without validation errors
**Priority:** P0 (3.6 upgrade)

#### Scenario: CreateMediaBuySuccess Exposes All adcp 3.6 Response Fields
**Obligation ID** UC-002-CC-SCHEMA-COMPLIANCE-02
**Layer** schema
**Given** a successful media buy creation
**When** the response is serialized at the MCP boundary
**Then** the response schema includes: `account`, `buyer_campaign_ref`, `buyer_ref`, `context`, `creative_deadline`, `ext`, `media_buy_id`, `packages`, `sandbox`
**Priority:** P1 (3.6 upgrade)

#### Scenario: PackageRequest Accepts All adcp 3.6 Fields
**Obligation ID** UC-002-CC-SCHEMA-COMPLIANCE-03
**Layer** schema
**Given** a package request with all adcp 3.6 fields: `bid_price`, `budget`, `buyer_ref`, `catalog`, `creative_assignments`, `creatives`, `ext`, `format_ids`, `impressions`, `optimization_goal`, `pacing`, `paused`, `pricing_option_id`, `product_id`, `targeting_overlay`
**When** the package is parsed
**Then** all fields are accepted without validation errors
**Priority:** P1 (3.6 upgrade)

---

### Postcondition Tests

#### Scenario: POST-F1 -- System State Unchanged on Any Failure
**Obligation ID** UC-002-POST-01
**Layer** behavioral
**Given** any validation or adapter failure
**When** the error response is returned
**Then** no media buy, package, or creative assignment records exist in the database
**And** no campaign exists in the ad server (unless ext-q partial execution)
**Business Rule:** POST-F1
**Priority:** P0

#### Scenario: POST-F2 -- Error Response Contains Specific Error Code
**Obligation ID** UC-002-POST-02
**Layer** schema
**Given** any failure
**When** the error response is returned
**Then** it contains a specific error code (not generic)
**And** the buyer can identify the exact failure
**Business Rule:** POST-F2
**Priority:** P0

#### Scenario: POST-F3 -- Error Response Contains Recovery Guidance
**Obligation ID** UC-002-POST-03
**Layer** behavioral
**Given** any validation failure
**When** the error response is returned
**Then** the message includes enough information for the buyer to fix and retry
**Business Rule:** POST-F3
**Priority:** P1

#### Scenario: POST-S2 -- Buyer Can Track via media_buy_id and buyer_ref
**Obligation ID** UC-002-POST-04
**Layer** behavioral
**Given** a successful media buy creation
**When** the response is returned
**Then** it includes both `media_buy_id` (publisher ID) and `buyer_ref` (buyer's ID)
**And** both can be used to query the media buy later
**Business Rule:** POST-S2
**Priority:** P0

---

### Shared Implementation Pattern (Critical Pattern #5)

#### Scenario: MCP and A2A Use Same Implementation
**Obligation ID** UC-002-SHARED-IMPLEMENTATION-PATTERN-01
**Layer** schema
**Given** the `create_media_buy` tool
**When** called via MCP (`@mcp.tool()`) or A2A (`create_media_buy_raw()`)
**Then** both paths call the same `_create_media_buy_impl()` function
**And** produce identical validation and responses
**Priority:** P1

---

## Summary Statistics

| Category | Count |
|----------|-------|
| Main flow scenarios | 20 |
| 3.6 boundary field scenarios | 10 |
| Alt: ASAP scenarios | 5 |
| Alt: Inline creatives scenarios | 5 |
| Alt: Manual approval scenarios | 10 |
| Alt: Proposal-based scenarios | 12 |
| Ext *a: Budget validation | 3 |
| Ext *b: Product not found | 3 |
| Ext *c: DateTime validation | 6 |
| Ext *d: Currency mismatch | 4 |
| Ext *e: Duplicate products | 2 |
| Ext *f: Targeting validation | 5 |
| Ext *g: Creative validation | 4 |
| Ext *h: Format ID validation | 4 |
| Ext *i: Authentication | 3 |
| Ext *j: Adapter failure | 3 |
| Ext *k: Max daily spend | 3 |
| Ext *l: Proposal not found | 3 |
| Ext *m: Proposal budget | 4 |
| Ext *n: Pricing validation | 8 |
| Ext *o: Creative not found | 1 |
| Ext *p: Creative format mismatch | 1 |
| Ext *q: Creative upload failed | 2 |
| Cross-cutting: Atomic response | 3 |
| Cross-cutting: Adapter atomicity | 3 |
| Cross-cutting: Min spend | 4 |
| Cross-cutting: Creative assignment | 3 |
| Cross-cutting: Schema compliance | 3 |
| Postcondition tests | 4 |
| Shared impl pattern | 1 |
| **Total** | ****141** |

## Priority Distribution

| Priority | Count | Description |
|----------|-------|-------------|
| P0 | ~45 | Must-have for 3.6 upgrade: auth, budget, product lookup, datetime, pricing, adapter atomicity, response semantics, boundary fields |
| P1 | ~65 | Important: targeting, creatives, formats, min/max spend, approval workflow, field propagation |
| P2 | ~25 | Moderate: edge cases, fallback chains, case sensitivity, implicit currency |
| P3 | ~6 | Nice-to-have: sandbox flag, cosmetic response details |

## Open Questions (from Requirements)

These gaps may require additional test scenarios once resolved:

- G9: Package schema requires only `package_id`, but code enforces 4 required fields
- G10: Budget > 0 in code vs >= 0 in schema (covered by ext-a scenario)
- G11: Single currency constraint -- protocol-level or implementation choice?
- G12: No duplicate product_ids -- protocol-level or implementation choice?
- G14: Format ID must be object -- schema allows string but code rejects
- G20: Reporting webhook frequency -- only daily supported
- G24: Creative update semantics -- replace or merge?
- G30: Can packages be provided alongside proposal_id for overrides?
- G31: What validation applies to proposal-derived packages?
- G32: Product from proposal removed between get_products and create_media_buy?
- G33: How does account_id interact with proposal execution?
