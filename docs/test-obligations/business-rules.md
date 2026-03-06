# Business Rules -- Test Obligations

## 3.6 Upgrade Impact

The following rules are directly affected by the adcp 3.2.0 -> 3.6.0 upgrade:

| Rule | Impact | Bug |
|------|--------|-----|
| BR-RULE-006 | PricingOption XOR now covers 9 models (cpm, vcpm, cpc, cpcv, cpv, cpp, cpa, time, flat_rate). CPA has `exclusiveMinimum: 0` on fixed_price. | salesagent-mq3n (PricingOption delivery lookup string vs integer PK) |
| BR-RULE-007 | Product schema now has `additional_properties: true`; new fields: channels, catalog_match, catalog_types, conversion_tracking, data_provider_signals, forecast, signal_targeting_allowed | salesagent-qo8a (6 Product fields missing from DB -- FIXED) |
| BR-RULE-008 | Budget positivity unchanged but total_budget now schema-validated | -- |
| BR-RULE-011 | min_spend_per_package now explicit in all 9 v3 pricing models | salesagent-mq3n |
| BR-RULE-015 | Creative now uses v3 creative-asset schema (format_id is object, not string) | salesagent-goy2 (Creative extends wrong adcp type) |
| BR-RULE-021 | XOR identification now applies to performance feedback as well | salesagent-7gnv (MediaBuy boundary drops buyer_campaign_ref, creative_deadline, ext) |
| BR-RULE-043 | Context echo now applies to capabilities and accounts endpoints | -- |
| BR-RULE-048 | Signal activation is new in v3 | -- |
| BR-RULE-051-078 | Performance feedback, capabilities, accounts, content standards, property lists are all new v3 domains | -- |

## Rules

### BR-RULE-001: Brand Manifest Policy Enforcement
**Obligation ID** BR-RULE-001-01
**Layer** behavioral
**Invariant:** The system enforces `brand_manifest_policy` at product discovery entry. Three levels: `require_auth`, `require_brand`, `public`. Default is `require_auth`.
**Scenario:**
```gherkin
Given a tenant with brand_manifest_policy set to "require_brand"
When a buyer requests products without providing a brand manifest
Then the request is rejected

Given a tenant with brand_manifest_policy set to "public"
When an anonymous buyer requests products
Then the request proceeds and products are returned
```
**Priority:** P1
**Affected by 3.6:** Yes -- v3 enforces `brand` (BrandReference) at all boundaries per recent refactor (96239407)

---

### BR-RULE-002: Brief Policy Compliance
**Obligation ID** BR-RULE-002-01
**Layer** behavioral
**Invariant:** When advertising_policy is enabled, the buyer's brief is checked via LLM. BLOCKED briefs are rejected. RESTRICTED briefs with manual review enabled are rejected. Service unavailable fails open.
**Scenario:**
```gherkin
Given a tenant with advertising_policy enabled
When a buyer submits a brief evaluated as BLOCKED
Then the request is rejected with POLICY_VIOLATION

Given a tenant with advertising_policy enabled
When the LLM policy service is unavailable
Then the request proceeds (fail-open)
```
**Priority:** P2
**Affected by 3.6:** No

---

### BR-RULE-003: Principal-Scoped Product Visibility
**Obligation ID** BR-RULE-003-01
**Layer** behavioral
**Invariant:** Products with `allowed_principal_ids` are visible only to listed principals. Products without restrictions are visible to all. Anonymous users cannot see restricted products.
**Scenario:**
```gherkin
Given a product with allowed_principal_ids = ["principal_A"]
When principal_B requests products
Then the restricted product is not included in results

Given a product with allowed_principal_ids = null
When an anonymous user requests products
Then the product is included in results
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-004: Anonymous Pricing Suppression
**Obligation ID** BR-RULE-004-01
**Layer** behavioral
**Invariant:** Anonymous requests have `pricing_options` set to empty array on every product.
**Scenario:**
```gherkin
Given a product with 3 pricing options
When an anonymous user requests products
Then the product has pricing_options = []

Given a product with 3 pricing options
When an authenticated user requests products
Then the product has all 3 pricing options
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-005: AI Ranking Minimum Threshold
**Obligation ID** BR-RULE-005-01
**Layer** schema
**Invariant:** When AI ranking is applied, products scoring below 0.1 are filtered out. Products >= 0.1 are sorted descending. Without ranking, no threshold.
**Scenario:**
```gherkin
Given AI ranking is active and product scores [0.05, 0.15, 0.9]
When products are returned
Then only products scoring >= 0.1 are included (0.15, 0.9) sorted descending

Given no brief is provided
When products are returned
Then all products are included regardless of score
```
**Priority:** P2
**Affected by 3.6:** No

---

### BR-RULE-006: PricingOption XOR Constraint
**Obligation ID** BR-RULE-006-01
**Layer** schema
**Invariant:** Each pricing option must have exactly one of `fixed_price` or `floor_price`. Both or neither is invalid. CPA always has `fixed_price`.
**Scenario:**
```gherkin
Given a pricing option with fixed_price=10 and floor_price=null
Then the pricing option is valid

Given a pricing option with both fixed_price=10 and floor_price=5
Then the pricing option is invalid

Given a CPA pricing option
Then fixed_price is required and floor_price must be null
```
**Priority:** P0
**Affected by 3.6:** Yes -- 9 pricing models now; CPA has `exclusiveMinimum: 0` on fixed_price. Relates to salesagent-mq3n.

---

### BR-RULE-007: Product Schema Validity
**Obligation ID** BR-RULE-007-01
**Layer** schema
**Invariant:** Each product must have >= 1 format_id, >= 1 publisher_property, >= 1 pricing_option. Conversion failure is treated as data corruption and fails the entire request.
**Scenario:**
```gherkin
Given a product with 0 format_ids
When the product is converted to AdCP schema
Then a ValueError is raised and the request fails

Given a product with 1 format_id, 1 property, 1 pricing_option
When the product is converted to AdCP schema
Then conversion succeeds
```
**Priority:** P0
**Affected by 3.6:** Yes -- Product schema now has `additional_properties: true` and 6 new fields. Relates to salesagent-qo8a (FIXED).

---

### BR-RULE-008: Budget Positivity
**Obligation ID** BR-RULE-008-01
**Layer** behavioral
**Invariant:** Total budget must be strictly positive (> 0). Schema allows 0 but business rule rejects it.
**Scenario:**
```gherkin
Given a media buy with total_budget.amount = 0
When create_media_buy is called
Then the request is rejected

Given a media buy with total_budget.amount = 100
When create_media_buy is called
Then budget validation passes
```
**Priority:** P0
**Affected by 3.6:** No

---

### BR-RULE-009: Single Currency Per Media Buy
**Obligation ID** BR-RULE-009-01
**Layer** behavioral
**Invariant:** All packages must use the same currency. Currency must be in tenant's CurrencyLimit table.
**Scenario:**
```gherkin
Given two packages with currencies ["USD", "EUR"]
When create_media_buy is called
Then the request is rejected for mixed currencies

Given two packages both using "USD" and USD is in tenant's CurrencyLimit
When create_media_buy is called
Then currency validation passes
```
**Priority:** P0
**Affected by 3.6:** No

---

### BR-RULE-010: No Duplicate Products Per Media Buy
**Obligation ID** BR-RULE-010-01
**Layer** behavioral
**Invariant:** Each product_id can appear at most once across all packages in a media buy.
**Scenario:**
```gherkin
Given two packages both referencing product_id="prod_1"
When create_media_buy is called
Then the request is rejected for duplicate product

Given two packages with distinct product_ids
When create_media_buy is called
Then validation passes
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-011: Minimum Spend Per Package
**Obligation ID** BR-RULE-011-01
**Layer** schema
**Invariant:** Package budget must meet min_spend from product pricing or tenant currency limit fallback.
**Scenario:**
```gherkin
Given a product with min_spend_per_package=500 and package budget=400
When create_media_buy is called
Then the request is rejected for budget below minimum

Given no product min_spend and tenant min_package_budget=100 and budget=50
When create_media_buy is called
Then the request is rejected

Given no minimum configured at any level
When create_media_buy is called
Then minimum spend check is skipped
```
**Priority:** P1
**Affected by 3.6:** Yes -- all 9 v3 pricing models now have explicit min_spend_per_package field

---

### BR-RULE-012: Maximum Daily Spend Cap
**Obligation ID** BR-RULE-012-01
**Layer** schema
**Invariant:** Daily budget (package_budget / max(1, flight_days)) must not exceed tenant's max_daily_package_spend.
**Scenario:**
```gherkin
Given tenant max_daily_package_spend=1000 and package budget=5000 over 3 days (daily=1667)
When create_media_buy is called
Then the request is rejected for exceeding daily cap

Given no max_daily_package_spend configured
When create_media_buy is called
Then daily cap check is skipped
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-013: DateTime Validity
**Obligation ID** BR-RULE-013-01
**Layer** behavioral
**Invariant:** start_time must be in the future, end_time must be after start_time. "asap" (case-sensitive) resolves to current UTC.
**Scenario:**
```gherkin
Given start_time is "asap"
When create_media_buy is called
Then start_time resolves to current UTC and bypasses past-time check

Given start_time is "ASAP" (wrong case)
When create_media_buy is called
Then the value is not recognized and fails validation

Given end_time <= start_time
When create_media_buy is called
Then the request is rejected
```
**Priority:** P0
**Affected by 3.6:** No

---

### BR-RULE-014: Targeting Overlay Validation
**Obligation ID** BR-RULE-014-01
**Layer** behavioral
**Invariant:** Unknown field names rejected, managed-only dimensions cannot be set by buyers, same geo value cannot be in both include and exclude lists.
**Scenario:**
```gherkin
Given a targeting overlay with unknown field "custom_segment"
When create_media_buy is called
Then the request is rejected

Given a targeting overlay with geo "US" in both include and exclude
When create_media_buy is called
Then the request is rejected
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-015: Creative Asset Validation
**Obligation ID** BR-RULE-015-01
**Layer** behavioral
**Invariant:** Reference creatives must have URL and dimensions. Generative formats are exempt. Errors collected non-fail-fast.
**Scenario:**
```gherkin
Given a reference creative without a URL
When creative validation runs
Then an error is collected for the missing URL

Given a generative format creative without a URL
When creative validation runs
Then the creative passes validation (exempt)
```
**Priority:** P1
**Affected by 3.6:** Yes -- Creative format_id is now an object (agent_url + id), not a string. Relates to salesagent-goy2.

---

### BR-RULE-017: Approval Workflow Determination
**Obligation ID** BR-RULE-017-01
**Layer** behavioral
**Invariant:** If tenant `human_review_required` or adapter `manual_approval_required` is true, media buy enters pending state. Default is human_review_required=true.
**Scenario:**
```gherkin
Given tenant human_review_required=false and adapter manual_approval_required=false
When create_media_buy is called
Then the media buy is auto-approved

Given tenant human_review_required=true
When create_media_buy is called
Then the media buy enters pending manual approval state
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-018: Atomic Response Semantics
**Obligation ID** BR-RULE-018-01
**Layer** schema
**Invariant:** Responses contain EITHER success data OR error data, never both. Enforced by oneOf schema.
**Scenario:**
```gherkin
Given a successful media buy creation
When the response is returned
Then it contains success fields and no errors field

Given a validation failure
When the response is returned
Then it contains errors array and no success fields
```
**Priority:** P0
**Affected by 3.6:** Yes -- now applies to performance feedback and account sync responses as well

---

### BR-RULE-020: Adapter Atomicity
**Obligation ID** BR-RULE-020-01
**Layer** behavioral
**Invariant:** If adapter call fails on auto-approval path, no DB records are persisted. Manual approval path persists in pending state before adapter.
**Scenario:**
```gherkin
Given auto-approval path and adapter returns error
When create_media_buy processes
Then no database records are created

Given manual approval path
When create_media_buy processes
Then records are persisted in pending state before adapter execution
```
**Priority:** P0
**Affected by 3.6:** No

---

### BR-RULE-021: Dual Identification (XOR)
**Obligation ID** BR-RULE-021-01
**Layer** behavioral
**Invariant:** Update/performance operations must use exactly one of media_buy_id or buyer_ref. Both or neither is invalid.
**Scenario:**
```gherkin
Given an update request with both media_buy_id and buyer_ref
When update_media_buy is called
Then the request is rejected by schema validation

Given an update request with only buyer_ref
When update_media_buy is called
Then the system resolves the media buy via buyer_ref lookup
```
**Priority:** P0
**Affected by 3.6:** Yes -- now applies to performance feedback. Relates to salesagent-7gnv (MediaBuy boundary drops buyer_campaign_ref).

---

### BR-RULE-022: Partial Update Semantics
**Obligation ID** BR-RULE-022-01
**Layer** behavioral
**Invariant:** Only fields present in request are modified. Omitted fields unchanged. Empty updates rejected.
**Scenario:**
```gherkin
Given an update request with only budget field
When update_media_buy processes
Then only budget is changed; all other fields retain current values

Given an update request with no updatable fields
When update_media_buy is called
Then the request is rejected
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-024: Creative Replacement Semantics
**Obligation ID** BR-RULE-024-01
**Layer** behavioral
**Invariant:** creative_ids or creative_assignments completely replaces the existing set. Not a merge.
**Scenario:**
```gherkin
Given existing creative assignments [A, B, C] and update provides [B, D]
When update_media_buy processes
Then assignments become [B, D]; A and C are deleted
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-026: Creative Assignment Validation
**Obligation ID** BR-RULE-026-01
**Layer** behavioral
**Invariant:** Creatives in error/rejected state cannot be assigned. Format must be compatible with product. All errors returned as INVALID_CREATIVES.
**Scenario:**
```gherkin
Given a creative in "error" state
When creative assignment is attempted
Then the request is rejected with INVALID_CREATIVES

Given a creative with incompatible format
When creative assignment is attempted
Then the request is rejected with INVALID_CREATIVES
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-028: Placement ID Validation
**Obligation ID** BR-RULE-028-01
**Layer** behavioral
**Invariant:** placement_ids must be valid for the product. Products without placement support reject placement_ids.
**Scenario:**
```gherkin
Given a placement_id not valid for the package's product
When creative assignment is attempted
Then the request is rejected with invalid_placement_ids

Given a product that does not support placement targeting
When creative assignment includes placement_ids
Then the request is rejected
```
**Priority:** P2
**Affected by 3.6:** No

---

### BR-RULE-029: Webhook Delivery Contract
**Obligation ID** BR-RULE-029-01
**Layer** behavioral
**Invariant:** Webhooks use monotonically increasing sequence numbers, typed notifications, and exponential backoff retry for 5xx. 4xx not retried.
**Scenario:**
```gherkin
Given a webhook delivery attempt fails with 503
When the retry policy executes
Then the system retries up to 3 times with exponential backoff (1s, 2s, 4s + jitter)

Given a webhook delivery attempt fails with 400
When the retry policy evaluates
Then the system does not retry (client error)

Given notification_type is "final"
When the webhook payload is assembled
Then next_expected_at is omitted
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-030: Multi-Entity Identification (OR)
**Obligation ID** BR-RULE-030-01
**Layer** behavioral
**Invariant:** Delivery requests use optional media_buy_ids (priority) and/or buyer_refs. Neither returns all. Partial resolution silently omits missing. Zero results return empty array.
**Scenario:**
```gherkin
Given both media_buy_ids and buyer_refs provided
When get_media_buy_delivery is called
Then only media_buy_ids are used (priority rule)

Given neither media_buy_ids nor buyer_refs provided
When get_media_buy_delivery is called
Then all media buys for the principal are returned

Given some media_buy_ids do not exist
When get_media_buy_delivery is called
Then results include only found media buys (partial, no error)
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-031: Format Discovery Filter Conjunction
**Obligation ID** BR-RULE-031-01
**Layer** behavioral
**Invariant:** All filters combine as AND. Results sorted by type then name.
**Scenario:**
```gherkin
Given type_filter="display" and name_search="banner"
When list_creative_formats is called
Then only formats matching BOTH display type AND "banner" name are returned

Given any valid format discovery request
When results are returned
Then they are sorted by format type then name
```
**Priority:** P2
**Affected by 3.6:** No

---

### BR-RULE-033: Validation Mode Semantics
**Obligation ID** BR-RULE-033-01
**Layer** behavioral
**Invariant:** strict mode aborts on assignment error. lenient mode logs warning and continues. Default is strict. Per-creative failures always produce action=failed regardless of mode.
**Scenario:**
```gherkin
Given validation_mode="strict" and an assignment error occurs
When sync_creatives processes
Then a ToolError is raised and remaining assignments are aborted

Given validation_mode="lenient" and an assignment error occurs
When sync_creatives processes
Then a warning is logged and the remaining assignments continue
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-034: Cross-Principal Creative Isolation
**Obligation ID** BR-RULE-034-01
**Layer** behavioral
**Invariant:** Creative lookup always filters by tenant_id + principal_id + creative_id. Cross-principal collision silently creates new creative.
**Scenario:**
```gherkin
Given creative_id "cr_1" exists under principal_A
When principal_B syncs a creative with creative_id "cr_1"
Then a new creative is created for principal_B (no error, no cross-visibility)
```
**Priority:** P0
**Affected by 3.6:** No

---

### BR-RULE-035: Creative Format Validation
**Obligation ID** BR-RULE-035-01
**Layer** behavioral
**Invariant:** format_id is required. Non-HTTP agent_url skips external validation. HTTP agents checked for reachability and format registration.
**Scenario:**
```gherkin
Given a creative with format_id having non-HTTP agent_url
When format validation runs
Then external validation is skipped

Given a creative with format_id whose HTTP agent is unreachable
When format validation runs
Then a ValueError with agent-unreachable message is raised
```
**Priority:** P1
**Affected by 3.6:** Yes -- format_id structure changed (object with agent_url + id). Relates to salesagent-goy2.

---

### BR-RULE-036: Generative Creative Build
**Obligation ID** BR-RULE-036-01
**Layer** behavioral
**Invariant:** Creative is generative when format has output_format_ids. Prompt priority: asset roles > inputs[0].context_description > name (create only). Update without prompt preserves existing data.
**Scenario:**
```gherkin
Given a format with output_format_ids = ["fmt_responsive"]
When a creative is synced with assets containing a "message" role
Then the message content is used as the generative build prompt

Given a format with output_format_ids and an update request with no prompt
When the creative is updated
Then the generative build is skipped and existing data is preserved
```
**Priority:** P2
**Affected by 3.6:** No

---

### BR-RULE-037: Creative Approval Workflow
**Obligation ID** BR-RULE-037-01
**Layer** behavioral
**Invariant:** approval_mode determines routing: auto-approve (immediate), require-human (pending + workflow + Slack), ai-powered (pending + workflow + background AI). Default is require-human.
**Scenario:**
```gherkin
Given tenant approval_mode = "auto-approve"
When a creative is synced
Then status is set to "approved" with no workflow steps

Given tenant approval_mode = "require-human"
When a creative is synced
Then status is "pending_review", workflow steps created, Slack notification sent immediately
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-038: Assignment Package Validation
**Obligation ID** BR-RULE-038-01
**Layer** behavioral
**Invariant:** Package lookup joins MediaPackage to MediaBuy filtered by tenant_id. Strict/lenient per BR-RULE-033. Existing assignments idempotently updated.
**Scenario:**
```gherkin
Given a package_id not found in any media buy for this tenant
When strict mode assignment is attempted
Then a ToolError is raised

Given an assignment for the same creative-package pair already exists
When assignment is attempted again
Then the existing assignment is updated (weight reset to 100)
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-039: Assignment Format Compatibility
**Obligation ID** BR-RULE-039-01
**Layer** schema
**Invariant:** Format compatibility checks normalized agent_url and exact format_id against product's format_ids. Empty format_ids means all allowed.
**Scenario:**
```gherkin
Given product format_ids accepts agent "http://agent.com/mcp" id "banner_300x250"
When a creative with agent_url "http://agent.com/mcp/" and id "banner_300x250" is assigned
Then URL normalization strips trailing "/" and the format matches

Given a product with empty format_ids
When any creative format is assigned
Then format compatibility passes (all formats allowed)
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-040: Media Buy Status Transition on Assignment
**Obligation ID** BR-RULE-040-01
**Layer** behavioral
**Invariant:** Draft media buy with non-null approved_at transitions to pending_creatives on creative assignment. Other statuses unchanged.
**Scenario:**
```gherkin
Given media buy status="draft" and approved_at is set
When a creative assignment is made
Then status transitions to "pending_creatives"

Given media buy status="draft" and approved_at is null
When a creative assignment is made
Then status remains "draft"
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-041: Discovery Endpoint Authentication
**Obligation ID** BR-RULE-041-01
**Layer** behavioral
**Invariant:** Authentication optional for discovery. Invalid tokens treated as missing (MCP). A2A requires valid token if one is provided. Data not scoped by identity.
**Scenario:**
```gherkin
Given no authentication token
When list_authorized_properties is called
Then the system returns full discovery data with principal as "anonymous"

Given an invalid/expired token via MCP
When list_authorized_properties is called
Then the token is treated as absent and full data is returned

Given an invalid token via A2A
When discover_seller_capabilities is called
Then the request is rejected with authentication error
```
**Priority:** P1
**Affected by 3.6:** Yes -- now also covers capabilities endpoint

---

### BR-RULE-042: Property Portfolio Assembly
**Obligation ID** BR-RULE-042-01
**Layer** behavioral
**Invariant:** All registered publisher partnerships returned regardless of verification status. Sorted alphabetically. Empty portfolio returns empty array with description.
**Scenario:**
```gherkin
Given a tenant with 3 publisher partnerships (2 verified, 1 unverified)
When list_authorized_properties is called
Then all 3 publishers are returned sorted alphabetically

Given a tenant with no publisher partnerships
When list_authorized_properties is called
Then an empty publisher_domains array is returned with a portfolio_description
```
**Priority:** P1
**Affected by 3.6:** No

---

### BR-RULE-043: Context Echo Invariant
**Obligation ID** BR-RULE-043-01
**Layer** schema
**Invariant:** Request context is echoed unchanged in the response. Context is opaque. Applies to all response paths.
**Scenario:**
```gherkin
Given a request with context = {"trace_id": "abc123"}
When the response is returned
Then context = {"trace_id": "abc123"} is in the response

Given a request without context
When the response is returned
Then context is absent from the response
```
**Priority:** P1
**Affected by 3.6:** Yes -- now covers capabilities (GAP: not yet echoed) and accounts endpoints

---

### BR-RULE-044: Advertising Policy Disclosure
**Obligation ID** BR-RULE-044-01
**Layer** behavioral
**Invariant:** When advertising_policy enabled and at least one policy array non-empty, human-readable summary included. Omitted when disabled or all arrays empty.
**Scenario:**
```gherkin
Given tenant advertising_policy enabled with prohibited_categories = ["tobacco"]
When list_authorized_properties is called
Then advertising_policies field contains a summary mentioning tobacco

Given tenant advertising_policy disabled
When list_authorized_properties is called
Then advertising_policies field is omitted
```
**Priority:** P2
**Affected by 3.6:** No

---

### BR-RULE-045: Publisher Domain Filter Validation
**Obligation ID** BR-RULE-045-01
**Layer** schema
**Invariant:** Domain must match lowercase alphanumeric pattern. Filter array must have >= 1 item. Valid but non-matching domains yield empty results (not error).
**Scenario:**
```gherkin
Given filter with domain "CNN.COM" (uppercase)
When list_authorized_properties is called
Then the request is rejected with DOMAIN_INVALID_FORMAT

Given filter with domain "nonexistent.com" (valid format, no match)
When list_authorized_properties is called
Then the request succeeds with empty results for that domain
```
**Priority:** P2
**Affected by 3.6:** No

---

### BR-RULE-047: Signal Filter Conjunction & Defaults
**Obligation ID** BR-RULE-047-01
**Layer** behavioral
**Invariant:** Signal filters are optional, combine as AND. max_results limits final count.
**Scenario:**
```gherkin
Given catalog_types=["marketplace"] and max_cpm=5.0
When get_signals is called
Then only marketplace signals with cpm <= 5.0 are returned

Given max_results=3 and 10 signals match
When results are returned
Then only 3 signals are included
```
**Priority:** P2
**Affected by 3.6:** Yes -- signals domain is new in v3

---

### BR-RULE-048: Signal Activation Validation
**Obligation ID** BR-RULE-048-01
**Layer** behavioral
**Invariant:** Premium signals (IDs starting with "premium_") require manual approval. Response is atomic (success XOR error).
**Scenario:**
```gherkin
Given signal_id = "premium_auto_intenders"
When activate_signal is called
Then APPROVAL_REQUIRED error is returned

Given a valid non-premium signal_id
When activate_signal is called
Then activation proceeds and deployments are returned
```
**Priority:** P2
**Affected by 3.6:** Yes -- signals domain is new in v3

---

### BR-RULE-049: Per-Filter Format Discovery Semantics
**Obligation ID** BR-RULE-049-01
**Layer** behavioral
**Invariant:** type=exact match, format_ids=id match with silent exclusion, asset_types=OR, dimensions=ANY render, is_responsive=bidirectional, name_search=case-insensitive substring.
**Scenario:**
```gherkin
Given type_filter="video"
When list_creative_formats is called
Then only formats with category "video" are returned

Given asset_types=["image", "video"]
When list_creative_formats is called
Then formats with either image OR video assets are returned (OR semantics)

Given is_responsive=false
When list_creative_formats is called
Then only formats with no responsive dimensions are returned
```
**Priority:** P2
**Affected by 3.6:** No

---

### BR-RULE-050: Per-Filter Signal Discovery Semantics
**Obligation ID** BR-RULE-050-01
**Layer** behavioral
**Invariant:** catalog_types and data_providers use OR within-filter. max_cpm and min_coverage enforce numeric thresholds. signal_spec is case-insensitive substring.
**Scenario:**
```gherkin
Given catalog_types=["marketplace", "custom"]
When get_signals is called
Then signals of either marketplace OR custom type are returned

Given max_cpm=2.0 and a signal with cpm=2.5
When get_signals filters
Then that signal is excluded
```
**Priority:** P2
**Affected by 3.6:** Yes -- signals domain is new in v3

---

### BR-RULE-051: Performance Index Scale Semantics
**Obligation ID** BR-RULE-051-01
**Layer** schema
**Invariant:** 0.0 = no value, 1.0 = expected, > 1.0 = above expected. Must be >= 0. Scores < 0.8 trigger optimization recommendation.
**Scenario:**
```gherkin
Given performance_index = -0.5
When provide_performance_feedback is called
Then the request is rejected by schema validation

Given performance_index = 0.3
When performance feedback is processed
Then the system flags low performance and recommends optimization
```
**Priority:** P2
**Affected by 3.6:** Yes -- performance feedback is new in v3

---

### BR-RULE-052: Capabilities Graceful Degradation
**Obligation ID** BR-RULE-052-01
**Layer** behavioral
**Invariant:** When internal deps fail, return valid but degraded response. No tenant = minimal. Adapter failure = default channels/targeting. DB failure = placeholder domain. Never propagate error to caller.
**Scenario:**
```gherkin
Given no tenant context can be resolved
When discover_seller_capabilities is called
Then a minimal response with adcp v3 + supported_protocols=[media_buy] is returned

Given adapter lookup fails
When capabilities are assembled
Then channels default to [display] and targeting defaults to geo_countries=true, geo_regions=true
```
**Priority:** P1
**Affected by 3.6:** Yes -- capabilities endpoint is new in v3

---

### BR-RULE-053: Channel Alias Resolution
**Obligation ID** BR-RULE-053-01
**Layer** behavioral
**Invariant:** "video" maps to "olv", "audio" maps to "streaming_audio". Unrecognized channels silently dropped.
**Scenario:**
```gherkin
Given adapter reports channel "video"
When capabilities response is assembled
Then channel is mapped to "olv" in the response

Given adapter reports channel "metaverse"
When capabilities response is assembled
Then that channel is silently dropped (not included)
```
**Priority:** P2
**Affected by 3.6:** Yes -- capabilities endpoint is new in v3

---

### BR-RULE-054: Account Access Scoping
**Obligation ID** BR-RULE-054-01
**Layer** behavioral
**Invariant:** list_accounts returns only accounts accessible to the authenticated agent. No accounts = empty array, not error.
**Scenario:**
```gherkin
Given agent_A has access to 2 accounts
When agent_A calls list_accounts
Then only those 2 accounts are returned

Given agent_B has no accessible accounts
When agent_B calls list_accounts
Then an empty accounts array is returned (not an error)
```
**Priority:** P1
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### BR-RULE-055: Account Operation Authentication Policy
**Obligation ID** BR-RULE-055-01
**Layer** behavioral
**Invariant:** sync_accounts requires valid auth. list_accounts works without auth but scopes results. Unauthenticated list returns empty array.
**Scenario:**
```gherkin
Given no valid authentication
When sync_accounts is called
Then AUTH_TOKEN_INVALID error is returned

Given no authentication
When list_accounts is called
Then an empty accounts array is returned (not an error)
```
**Priority:** P0
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### BR-RULE-056: Sync Upsert Semantics
**Obligation ID** BR-RULE-056-01
**Layer** behavioral
**Invariant:** sync_accounts creates new or updates existing, returning per-account action (created/updated/unchanged/failed). House is echoed.
**Scenario:**
```gherkin
Given a new account not on the seller
When sync_accounts is called
Then per-account result has action=created with seller-assigned account_id

Given an existing account with no changes
When sync_accounts is called
Then per-account result has action=unchanged
```
**Priority:** P1
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### BR-RULE-057: Sync Atomic Response
**Obligation ID** BR-RULE-057-01
**Layer** behavioral
**Invariant:** Response contains EITHER accounts[] (success) OR errors[] (error), never both. Per-account failures are within the success variant.
**Scenario:**
```gherkin
Given sync_accounts processes 3 accounts, 1 fails
When the response is returned
Then response is success variant with accounts[] (including action=failed for 1)

Given an authentication failure
When sync_accounts is called
Then response is error variant with errors[], no accounts[]
```
**Priority:** P0
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### BR-RULE-058: Brand Identity Resolution
**Obligation ID** BR-RULE-058-01
**Layer** behavioral
**Invariant:** Brands identified by house domain + optional brand_id, resolved via /.well-known/brand.json. House echoed in response.
**Scenario:**
```gherkin
Given account with house="acme.com" and brand_id="widgets"
When sync_accounts processes the account
Then brand identity resolved via acme.com/.well-known/brand.json

Given a per-account result is returned
Then it echoes the same house value from the request
```
**Priority:** P2
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### BR-RULE-059: Billing Model Policy
**Obligation ID** BR-RULE-059-01
**Layer** behavioral
**Invariant:** Seller assigns billing model, may override buyer's request with warning. Omitted billing uses seller default.
**Scenario:**
```gherkin
Given buyer requests billing model "brand_direct" but seller only supports "operator"
When sync_accounts processes
Then billing is set to "operator" with a per-account warning explaining the override
```
**Priority:** P2
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### BR-RULE-060: Account Approval Workflow
**Obligation ID** BR-RULE-060-01
**Layer** behavioral
**Invariant:** Accounts requiring review enter pending_approval with setup info (message required, optional url/expiry). Push notification webhook for async updates.
**Scenario:**
```gherkin
Given an account requires seller review
When sync_accounts processes
Then per-account result has status=pending_approval with setup.message

Given an account does not require review
When sync_accounts processes
Then per-account result has status=active (no setup)
```
**Priority:** P2
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### BR-RULE-061: Delete Missing Deactivation Policy
**Obligation ID** BR-RULE-061-01
**Layer** behavioral
**Invariant:** delete_missing=true deactivates absent accounts scoped to authenticated agent only. Default is false.
**Scenario:**
```gherkin
Given delete_missing=true and agent previously synced accounts [A, B, C] but current request has [A, B]
When sync_accounts processes
Then account C is deactivated

Given delete_missing=true and agent_X previously synced [X1]
When agent_Y syncs with delete_missing=true without X1
Then X1 is NOT affected (agent-scoped deactivation)
```
**Priority:** P1
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### BR-RULE-062: Dry Run Preview Mode
**Obligation ID** BR-RULE-062-01
**Layer** schema
**Invariant:** dry_run=true returns what would change without applying modifications. Response includes dry_run=true.
**Scenario:**
```gherkin
Given dry_run=true
When sync_accounts is called
Then response includes dry_run=true and per-account results, but no state is changed

Given dry_run=false (or omitted)
When sync_accounts is called
Then changes are applied normally
```
**Priority:** P2
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### BR-RULE-063: Content Standards Authentication
**Obligation ID** BR-RULE-063-01
**Layer** behavioral
**Invariant:** All content standards CRUD operations require valid authentication. No anonymous access.
**Scenario:**
```gherkin
Given no authentication token
When any content standards operation is called
Then the operation is rejected with authentication error

Given a valid authentication token
When create_content_standards is called
Then the operation proceeds under the resolved tenant and principal
```
**Priority:** P1
**Affected by 3.6:** Yes -- content standards domain is new in v3

---

### BR-RULE-064: Content Standards Scope Requirements
**Obligation ID** BR-RULE-064-01
**Layer** schema
**Invariant:** Scope requires languages (minItems: 1). countries_all uses AND logic. channels_any uses OR logic. countries and channels are optional.
**Scenario:**
```gherkin
Given a content standard with scope languages_any=["en"] and countries_all=["US", "UK"]
When the standard is applied
Then it applies to content in English AND in both US AND UK

Given a content standard with scope channels_any=["display", "social"]
When the standard is applied
Then it applies to display OR social channels
```
**Priority:** P2
**Affected by 3.6:** Yes -- content standards domain is new in v3

---

### BR-RULE-065: Scope Conflict Detection
**Obligation ID** BR-RULE-065-01
**Layer** behavioral
**Invariant:** Create/update that would overlap scope with existing standard for same tenant is rejected with SCOPE_CONFLICT and conflicting_standards_id.
**Scenario:**
```gherkin
Given an existing standard covering scope {en, US, display}
When creating a new standard with overlapping scope {en, US, display}
Then the operation is rejected with SCOPE_CONFLICT and the existing standard's ID
```
**Priority:** P2
**Affected by 3.6:** Yes -- content standards domain is new in v3

---

### BR-RULE-066: Content Standards Immutable Versioning
**Obligation ID** BR-RULE-066-01
**Layer** behavioral
**Invariant:** Updates create new versions. Partial fields supported. standards_id remains stable across versions.
**Scenario:**
```gherkin
Given an existing content standard with policy_text="v1 policy"
When update is called with policy_text="v2 policy"
Then a new version is created; previous version preserved; same standards_id returned
```
**Priority:** P2
**Affected by 3.6:** Yes -- content standards domain is new in v3

---

### BR-RULE-067: Content Standards Referential Integrity
**Obligation ID** BR-RULE-067-01
**Layer** behavioral
**Invariant:** Cannot delete standard referenced by active media buys. Unreferenced delete cascades versions and exemplars.
**Scenario:**
```gherkin
Given a content standard referenced by 2 active media buys
When delete is called
Then STANDARDS_IN_USE error is returned

Given a content standard with no active media buy references
When delete is called
Then the standard, all versions, and calibration exemplars are deleted
```
**Priority:** P2
**Affected by 3.6:** Yes -- content standards domain is new in v3

---

### BR-RULE-068: Content Standards List Filter Semantics
**Obligation ID** BR-RULE-068-01
**Layer** behavioral
**Invariant:** Within-dimension OR, cross-dimension AND. No filters returns all tenant standards.
**Scenario:**
```gherkin
Given filter channels=["display", "social"] and languages=["en"]
When list_content_standards is called
Then standards matching (display OR social) AND (en) are returned
```
**Priority:** P3
**Affected by 3.6:** Yes -- content standards domain is new in v3

---

### BR-RULE-069: Calibration Exemplar Polymorphism
**Obligation ID** BR-RULE-069-01
**Layer** schema
**Invariant:** Exemplars accept URL references or artifact objects (oneOf). Both may coexist. URL references resolved to artifacts on ingest.
**Scenario:**
```gherkin
Given calibration_exemplars.pass contains both URL references and artifact objects
When create_content_standards processes
Then both formats are accepted in the same collection
```
**Priority:** P3
**Affected by 3.6:** Yes -- content standards domain is new in v3

---

### BR-RULE-070: Property List Authentication
**Obligation ID** BR-RULE-070-01
**Layer** behavioral
**Invariant:** All property list CRUD operations require authenticated principal. No tenant = rejected.
**Scenario:**
```gherkin
Given no valid authentication credentials
When any property list operation is called
Then LIST_ACCESS_DENIED is returned

Given valid auth but tenant cannot be resolved
When create_property_list is called
Then the request is rejected with tenant error
```
**Priority:** P1
**Affected by 3.6:** Yes -- property lists domain is new in v3

---

### BR-RULE-071: Property List Tenant Isolation
**Obligation ID** BR-RULE-071-01
**Layer** behavioral
**Invariant:** Property lists scoped to auth-derived tenant. Cross-tenant access returns NOT_FOUND (not ACCESS_DENIED) to prevent enumeration.
**Scenario:**
```gherkin
Given list_id "list_1" belongs to tenant_A
When tenant_B requests get_property_list("list_1")
Then LIST_NOT_FOUND is returned (prevents information disclosure)
```
**Priority:** P0
**Affected by 3.6:** Yes -- property lists domain is new in v3

---

### BR-RULE-072: Property Source Validation
**Obligation ID** BR-RULE-072-01
**Layer** behavioral
**Invariant:** base_properties uses discriminated union (publisher_tags/publisher_ids/identifiers). Non-empty selection arrays required. Omitted base_properties = entire catalog.
**Scenario:**
```gherkin
Given base_properties with selection_type="publisher_tags" and publisher_domain="example.com" and tags=["sports"]
When create_property_list processes
Then the selection is valid

Given base_properties omitted
When create_property_list processes
Then the system resolves against the agent's entire property catalog
```
**Priority:** P2
**Affected by 3.6:** Yes -- property lists domain is new in v3

---

### BR-RULE-073: Property List Filter Requirements
**Obligation ID** BR-RULE-073-01
**Layer** behavioral
**Invariant:** filters object requires both countries_all (AND) and channels_any (OR) as non-empty arrays. Evaluated at resolution time.
**Scenario:**
```gherkin
Given filters with countries_all=["US", "UK"] and channels_any=["display"]
When property list is resolved
Then only properties with data in US AND UK that support display are included
```
**Priority:** P2
**Affected by 3.6:** Yes -- property lists domain is new in v3

---

### BR-RULE-074: Auth Token One-Shot Delivery
**Obligation ID** BR-RULE-074-01
**Layer** behavioral
**Invariant:** auth_token returned exactly once in create response. Not in any subsequent response. No recovery mechanism.
**Scenario:**
```gherkin
Given create_property_list succeeds
When the response is returned
Then it includes auth_token

Given get_property_list is called for the same list
When the response is returned
Then auth_token is NOT included
```
**Priority:** P1
**Affected by 3.6:** Yes -- property lists domain is new in v3

---

### BR-RULE-075: Update Replacement Semantics
**Obligation ID** BR-RULE-075-01
**Layer** behavioral
**Invariant:** Update uses full replacement per field. webhook_url only in update (not create). Empty string removes webhook.
**Scenario:**
```gherkin
Given update_property_list with base_properties=[new_set]
When the update processes
Then base_properties completely replaces existing (not merged)

Given update_property_list with webhook_url=""
When the update processes
Then the previously set webhook URL is removed
```
**Priority:** P2
**Affected by 3.6:** Yes -- property lists domain is new in v3

---

### BR-RULE-076: Property List Referential Integrity
**Obligation ID** BR-RULE-076-01
**Layer** behavioral
**Invariant:** get/update/delete require existing list_id. Missing = LIST_NOT_FOUND. Delete blocked by active media buys (LIST_IN_USE).
**Scenario:**
```gherkin
Given list_id "nonexistent" does not exist
When get_property_list is called
Then LIST_NOT_FOUND is returned with the provided list_id

Given list_id "list_1" is referenced by an active media buy
When delete_property_list is called
Then LIST_IN_USE is returned; list is not deleted
```
**Priority:** P1
**Affected by 3.6:** Yes -- property lists domain is new in v3

---

### BR-RULE-077: Property List Resolution and Pagination
**Obligation ID** BR-RULE-077-01
**Layer** behavioral
**Invariant:** resolve=true (default) resolves filters against current catalog. max_results 1-10000, default 1000. Cursor-based pagination.
**Scenario:**
```gherkin
Given resolve=true and max_results=100
When get_property_list is called
Then up to 100 resolved identifiers are returned with a cursor for next page

Given resolve=false
When get_property_list is called
Then identifiers are not returned; pagination params have no effect
```
**Priority:** P2
**Affected by 3.6:** Yes -- property lists domain is new in v3

---

### BR-RULE-078: Property List Filtering
**Obligation ID** BR-RULE-078-01
**Layer** behavioral
**Invariant:** list-property-lists supports optional filtering by principal (exact) and name (case-insensitive substring). Unfiltered returns all tenant lists.
**Scenario:**
```gherkin
Given name_contains="sports"
When list_property_lists is called
Then only lists whose name contains "sports" (case-insensitive) are returned

Given no filters
When list_property_lists is called
Then all property lists for the tenant are returned
```
**Priority:** P3
**Affected by 3.6:** Yes -- property lists domain is new in v3
