# UC-004: Deliver Media Buy Metrics -- Test Obligations

## Source

- Requirement: [UC-004: Deliver Media Buy Metrics](adcp-req/docs/requirements/use-cases/UC-004-deliver-media-buy-metrics/BR-UC-004.md)
- Files analyzed: BR-UC-004.md, BR-UC-004-main-mcp.md, BR-UC-004-ext-a through BR-UC-004-ext-g, BR-UC-004-alt-webhook.md, BR-UC-004-alt-filtered.md, BR-UC-004-alt-date-range.md
- Business Rules: BR-RULE-013, BR-RULE-018, BR-RULE-029, BR-RULE-030

## 3.6 Upgrade Impact

### salesagent-mq3n: PricingOption delivery lookup compares string to integer PK -- CRITICAL
- **ROOT CAUSE**: When delivery metrics are fetched, the code looks up `pricing_option_id` (a string like `"cpm_usd_fixed"`) to match it to a PricingOption database record whose PK is an integer. This comparison always fails silently, meaning delivery metrics are never correctly associated with their pricing context.
- **Impact scope**: Main flow step 8 (assembling per-media-buy delivery metrics requires pricing context to compute spend, effective_rate, etc.), package-level breakdowns (step 9), and aggregated totals (step 10).
- **Test obligation**: Every delivery metrics test must verify that `pricing_option_id` lookup uses the correct type -- string identifier, not integer PK. The lookup must resolve through the string `pricing_option_id` field, not the integer primary key.
- **Regression tests needed**:
  1. PricingOption lookup in delivery path uses string `pricing_option_id` field (not integer PK)
  2. Delivery metrics correctly include spend data (which requires successful pricing lookup)
  3. Package-level delivery status correctly reflects pricing model (CPM vs CPC vs FLAT_RATE compute differently)
  4. End-to-end: create media buy with pricing -> fetch delivery -> verify pricing_option_id round-trips correctly

### salesagent-7gnv: MediaBuy boundary drops buyer_campaign_ref, creative_deadline, ext fields
- **MODERATE for UC-004**: Delivery responses reference media buy identifiers including `buyer_ref`. If `buyer_campaign_ref` is dropped during serialization, the delivery response will have missing identification data, making it impossible for the buyer to correlate delivery data with their own campaign references.
- **Test obligation**: Verify `buyer_ref` / `buyer_campaign_ref` is present in `media_buy_deliveries[]` entries.

### salesagent-goy2: Creative extends wrong adcp type
- **LOW for UC-004**: Creative-level breakdowns are in the schema (G44) but not currently populated by code. If creative-level delivery is implemented in 3.6, the wrong base type would cause issues.
- **Test obligation**: If creative-level breakdowns are populated, verify creative model compatibility.

### Schema changes in adcp 3.6
- `GetMediaBuyDeliveryRequest` extends `LibraryGetMediaBuyDeliveryRequest` -- verify the library base class fields match expectations.
- `GetMediaBuyDeliveryResponse` extends `LibraryGetMediaBuyDeliveryResponse` with `NestedModelSerializerMixin` -- verify nested serialization of `media_buy_deliveries[]` and `aggregated_totals`.
- Delivery metrics fields (impressions, spend, clicks, ctr, video, conversions, viewability) -- verify schema compatibility with 3.6 `delivery-metrics.json`.

---

## Test Scenarios

### Main Flow: Polling Delivery Metrics
Source: BR-UC-004-main-mcp.md

#### Scenario: Happy path -- fetch delivery for single media buy by media_buy_id
**Obligation ID** UC-004-MAIN-01
**Layer** behavioral
**Given** an authenticated buyer who owns a media buy with active delivery data
**When** the buyer sends `get_media_buy_delivery` with `media_buy_ids: ["mb_123"]`
**Then** the system returns a success response in a protocol envelope with status `completed`, containing `reporting_period`, `currency`, `media_buy_deliveries` array with one entry, and optional `aggregated_totals`
**Business Rule** BR-RULE-018 (atomic response), BR-RULE-030 (multi-entity identification)
**Priority** P0 -- core happy path

#### Scenario: Happy path -- fetch delivery for single media buy by buyer_ref
**Obligation ID** UC-004-MAIN-02
**Layer** behavioral
**Given** an authenticated buyer who owns a media buy with buyer_ref="my_campaign_1"
**When** the buyer sends `get_media_buy_delivery` with `buyer_refs: ["my_campaign_1"]`
**Then** the system resolves via buyer_ref and returns delivery metrics
**Business Rule** BR-RULE-030 (INV-2: buyer_refs used when media_buy_ids absent)
**Priority** P0 -- alternative identification path

#### Scenario: Fetch delivery for multiple media buys
**Obligation ID** UC-004-MAIN-03
**Layer** behavioral
**Given** an authenticated buyer who owns media buys mb_1, mb_2, mb_3
**When** the buyer sends `get_media_buy_delivery` with `media_buy_ids: ["mb_1", "mb_2", "mb_3"]`
**Then** the system returns delivery data for all three in `media_buy_deliveries[]` and aggregated totals across all three
**Business Rule** BR-RULE-030 (array-based identification)
**Priority** P1

#### Scenario: Neither media_buy_ids nor buyer_refs provided -- return all
**Obligation ID** UC-004-MAIN-04
**Layer** behavioral
**Given** an authenticated buyer who owns 5 media buys
**When** the buyer sends `get_media_buy_delivery` with no identifiers
**Then** the system returns delivery data for ALL media buys owned by the principal
**Business Rule** BR-RULE-030 (INV-4: neither provided = all for principal)
**Priority** P1

#### Scenario: media_buy_ids takes precedence over buyer_refs
**Obligation ID** UC-004-MAIN-05
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer sends `get_media_buy_delivery` with BOTH `media_buy_ids` and `buyer_refs`
**Then** the system resolves by `media_buy_ids` only; `buyer_refs` is ignored
**Business Rule** BR-RULE-030 (INV-3: media_buy_ids wins)
**Priority** P1

#### Scenario: Default date range applied when not specified
**Obligation ID** UC-004-MAIN-06
**Layer** schema
**Given** an authenticated buyer requesting delivery metrics
**When** no `start_date` or `end_date` is provided
**Then** the system defaults to last 30 days (implementation default; see gap G40 re: schema says lifetime)
**Business Rule** Main flow step 6 (default date range)
**Priority** P1

#### Scenario: Response includes reporting_period
**Obligation ID** UC-004-MAIN-07
**Layer** schema
**Given** a successful delivery query
**When** the response is returned
**Then** `reporting_period` contains `start` and `end` dates matching the query window
**Business Rule** POST-S3 (buyer knows reporting period)
**Priority** P1

#### Scenario: Response includes currency
**Obligation ID** UC-004-MAIN-08
**Layer** schema
**Given** a successful delivery query
**When** the response is returned
**Then** `currency` field is present with a valid currency code
**Business Rule** Main flow step 11 (response assembly); gap G39 re: top-level vs per-media-buy
**Priority** P1

#### Scenario: Package-level delivery breakdowns present
**Obligation ID** UC-004-MAIN-09
**Layer** behavioral
**Given** a media buy with two packages that have delivery data
**When** the buyer queries delivery metrics
**Then** each media buy delivery entry includes package-level breakdowns with impressions, spend, clicks, CTR, video metrics, and conversions
**Business Rule** POST-S2 (package-level breakdowns)
**Priority** P1

#### Scenario: Package delivery status computed correctly
**Obligation ID** UC-004-MAIN-10
**Layer** behavioral
**Given** a media buy with packages in various delivery states
**When** the buyer queries delivery metrics
**Then** each package has a computed `delivery_status` (delivering, completed, budget_exhausted, flight_ended, goal_met)
**Business Rule** Main flow step 9
**Priority** P1

#### Scenario: Aggregated totals computed across media buys
**Obligation ID** UC-004-MAIN-11
**Layer** behavioral
**Given** delivery data for 3 media buys: mb_1 (1000 impressions, $50 spend), mb_2 (2000 impressions, $100 spend), mb_3 (500 impressions, $25 spend)
**When** the buyer queries all three
**Then** `aggregated_totals` has impressions=3500, spend=$175, and weighted CTR
**Business Rule** POST-S4, main flow step 10
**Priority** P1

#### Scenario: Response wrapped in protocol envelope
**Obligation ID** UC-004-MAIN-12
**Layer** behavioral
**Given** a successful delivery query
**When** the system returns the response
**Then** the protocol envelope has `status: completed`
**Business Rule** BR-RULE-018, main flow step 12
**Priority** P1

#### Scenario: MCP transport wraps in ToolResult with content and structured_content
**Obligation ID** UC-004-MAIN-13
**Layer** behavioral
**Given** a delivery query via MCP
**When** the response is returned
**Then** the MCP `ToolResult` contains both `content` (human-readable) and `structured_content` (typed response)
**Business Rule** Transport note in main flow
**Priority** P2

#### Scenario: pricing_option_id lookup uses string field not integer PK [3.6 UPGRADE -- CRITICAL]
**Obligation ID** UC-004-MAIN-14
**Layer** behavioral
**Given** a media buy with a PricingOption record where string `pricing_option_id`="cpm_usd_fixed" and integer PK=42
**When** delivery metrics are assembled and the system looks up pricing context
**Then** the lookup uses the string field `pricing_option_id`="cpm_usd_fixed" (NOT integer PK 42)
**Business Rule** salesagent-mq3n (string-to-integer comparison bug)
**Priority** P0 -- CRITICAL upgrade regression, silent data loss

#### Scenario: Delivery spend data correct when pricing lookup succeeds [3.6 UPGRADE]
**Obligation ID** UC-004-MAIN-15
**Layer** behavioral
**Given** a media buy with CPM pricing at $5.00 and 10,000 delivered impressions
**When** delivery metrics are fetched with correct pricing_option_id resolution
**Then** spend is computed as $50.00 (10,000 / 1,000 * $5.00)
**Business Rule** salesagent-mq3n (delivery metrics require correct pricing)
**Priority** P0 -- validates bug fix

#### Scenario: buyer_ref present in media_buy_deliveries entries [3.6 UPGRADE]
**Obligation ID** UC-004-MAIN-16
**Layer** behavioral
**Given** a media buy created with `buyer_campaign_ref="buyer_camp_1"`
**When** delivery metrics are returned
**Then** each `media_buy_deliveries` entry includes the `buyer_ref` field matching "buyer_camp_1"
**Business Rule** salesagent-7gnv (boundary drops fields)
**Priority** P1 -- upgrade regression

#### Scenario: Partial resolution -- some IDs not found returns partial results
**Obligation ID** UC-004-MAIN-17
**Layer** behavioral
**Given** an authenticated buyer owns mb_1 and mb_2, but mb_999 does not exist
**When** the buyer sends `get_media_buy_delivery` with `media_buy_ids: ["mb_1", "mb_999", "mb_2"]`
**Then** the system returns delivery data for mb_1 and mb_2 only (no error for mb_999)
**Business Rule** BR-RULE-030 (INV-5: partial resolution, found only)
**Priority** P1

#### Scenario: Zero identifiers resolve -- empty result
**Obligation ID** UC-004-MAIN-18
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer sends `get_media_buy_delivery` with `media_buy_ids: ["nonexistent_1"]`
**Then** the system returns empty `media_buy_deliveries` array (no error)
**Business Rule** BR-RULE-030 (INV-6: zero resolve = empty array)
**Priority** P2

#### Scenario: Delivery metrics include all standard fields
**Obligation ID** UC-004-MAIN-19
**Layer** behavioral
**Given** a media buy with active delivery
**When** delivery metrics are returned
**Then** the metrics include: impressions, spend, clicks, ctr, and where applicable: video_completion, conversions, viewability
**Business Rule** Schema: delivery-metrics.json fields
**Priority** P1

#### Scenario: Unpopulated schema fields handled gracefully (gaps G42, G44)
**Obligation ID** UC-004-MAIN-20
**Layer** behavioral
**Given** a delivery response
**When** the system assembles metrics
**Then** `daily_breakdown`, `effective_rate`, `viewability` metrics, and `creative_level_breakdowns` may be null/empty without error (known gaps G42, G44)
**Business Rule** Known gaps G42, G44
**Priority** P3 -- gap documentation

---

### Alt: Status-Filtered Delivery Query
Source: BR-UC-004-alt-filtered.md

#### Scenario: Filter by status "active"
**Obligation ID** UC-004-ALT-STATUS-FILTERED-DELIVERY-01
**Layer** schema
**Given** a buyer owns 5 media buys: 3 active, 1 paused, 1 completed
**When** the buyer sends `get_media_buy_delivery` with `status_filter: "active"`
**Then** the response includes delivery data for the 3 active media buys only
**Business Rule** Alt-filtered step 6
**Priority** P1

#### Scenario: Filter by status "completed"
**Obligation ID** UC-004-ALT-STATUS-FILTERED-DELIVERY-02
**Layer** behavioral
**Given** a buyer owns media buys in various states
**When** the buyer sends `get_media_buy_delivery` with `status_filter: "completed"`
**Then** only completed media buys are included in the response
**Business Rule** Alt-filtered step 6
**Priority** P2

#### Scenario: Filter by status "paused"
**Obligation ID** UC-004-ALT-STATUS-FILTERED-DELIVERY-03
**Layer** behavioral
**Given** a buyer owns media buys with some paused
**When** the buyer sends `get_media_buy_delivery` with `status_filter: "paused"`
**Then** only paused media buys are included
**Business Rule** Alt-filtered step 6
**Priority** P2

#### Scenario: No media buys match filter -- empty result
**Obligation ID** UC-004-ALT-STATUS-FILTERED-DELIVERY-04
**Layer** behavioral
**Given** a buyer owns only active media buys
**When** the buyer sends `get_media_buy_delivery` with `status_filter: "completed"`
**Then** the response has empty `media_buy_deliveries[]` array (success, not error)
**Business Rule** Alt-filtered step 7
**Priority** P1

#### Scenario: Default status_filter is "active"
**Obligation ID** UC-004-ALT-STATUS-FILTERED-DELIVERY-05
**Layer** behavioral
**Given** a buyer does not specify `status_filter`
**When** the delivery query runs
**Then** the default filter of "active" is applied (per schema)
**Business Rule** Alt-filtered response note
**Priority** P2

#### Scenario: Status filter "all" returns everything
**Obligation ID** UC-004-ALT-STATUS-FILTERED-DELIVERY-06
**Layer** behavioral
**Given** a buyer owns media buys in all states
**When** the buyer sends `get_media_buy_delivery` with `status_filter: "all"`
**Then** all media buys are included regardless of status
**Business Rule** Alt-filtered response note
**Priority** P2

#### Scenario: Valid status values accepted
**Obligation ID** UC-004-ALT-STATUS-FILTERED-DELIVERY-07
**Layer** behavioral
**Given** valid status values: active, pending, paused, completed, failed, reporting_delayed, all
**When** each is used as `status_filter`
**Then** the system accepts the value without error
**Business Rule** Alt-filtered response note
**Priority** P2

---

### Alt: Custom Date Range Query
Source: BR-UC-004-alt-date-range.md

#### Scenario: Custom date range -- both start and end provided
**Obligation ID** UC-004-ALT-CUSTOM-DATE-RANGE-01
**Layer** behavioral
**Given** a buyer requests delivery for a specific week
**When** the buyer sends `start_date: "2026-03-01"` and `end_date: "2026-03-07"`
**Then** the adapter is queried for that date range, and `reporting_period` in the response matches the requested dates
**Business Rule** BR-RULE-013, alt-date-range step 7
**Priority** P1

#### Scenario: Only start_date provided -- end defaults to now
**Obligation ID** UC-004-ALT-CUSTOM-DATE-RANGE-02
**Layer** schema
**Given** a buyer provides only `start_date: "2026-02-01"`
**When** the delivery query runs
**Then** `end_date` defaults to the current date/time
**Business Rule** Alt-date-range note
**Priority** P2

#### Scenario: Only end_date provided -- start defaults to creation date
**Obligation ID** UC-004-ALT-CUSTOM-DATE-RANGE-03
**Layer** schema
**Given** a media buy created on 2026-01-15 and the buyer provides only `end_date: "2026-03-01"`
**When** the delivery query runs
**Then** `start_date` defaults to the media buy's creation date (2026-01-15)
**Business Rule** Alt-date-range note
**Priority** P2

#### Scenario: Custom date range overrides default 30-day window
**Obligation ID** UC-004-ALT-CUSTOM-DATE-RANGE-04
**Layer** behavioral
**Given** a delivery query with `start_date` and `end_date` covering 90 days
**When** the query runs
**Then** the 30-day default is NOT applied; the full 90-day window is used
**Business Rule** Alt-date-range step 7 (overrides default)
**Priority** P1

---

### Alt: Webhook Push Reporting
Source: BR-UC-004-alt-webhook.md

#### Scenario: Scheduled webhook delivery -- happy path
**Obligation ID** UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
**Layer** behavioral
**Given** a media buy with `reporting_webhook` configured (url, authentication, reporting_frequency=daily, requested_metrics=[impressions, spend])
**When** the scheduler fires at the configured frequency
**Then** the system queries the adapter for delivery data, assembles metrics filtered to requested_metrics, signs the payload, and sends POST to the webhook URL
**Business Rule** BR-RULE-029, alt-webhook steps 1-8
**Priority** P1

#### Scenario: Webhook payload includes notification_type
**Obligation ID** UC-004-ALT-WEBHOOK-PUSH-REPORTING-02
**Layer** behavioral
**Given** a scheduled webhook delivery
**When** the payload is assembled
**Then** `notification_type` is set to one of: `scheduled`, `final`, `delayed`, `adjusted`
**Business Rule** POST-S9, alt-webhook step 4
**Priority** P1

#### Scenario: Notification type "scheduled" for normal periodic delivery
**Obligation ID** UC-004-ALT-WEBHOOK-PUSH-REPORTING-03
**Layer** behavioral
**Given** a normal periodic delivery trigger
**When** the webhook fires
**Then** `notification_type: "scheduled"`
**Business Rule** Alt-webhook step 4
**Priority** P2

#### Scenario: Notification type "final" for completed campaign
**Obligation ID** UC-004-ALT-WEBHOOK-PUSH-REPORTING-04
**Layer** behavioral
**Given** a media buy whose campaign has ended
**When** the final webhook fires
**Then** `notification_type: "final"` and `next_expected_at` is omitted
**Business Rule** BR-RULE-029 (INV-2: final = no next_expected_at)
**Priority** P1

#### Scenario: Monotonically increasing sequence_number per media buy
**Obligation ID** UC-004-ALT-WEBHOOK-PUSH-REPORTING-05
**Layer** behavioral
**Given** three consecutive webhook deliveries for the same media buy
**When** each delivery is sent
**Then** sequence_number values are strictly increasing (e.g., 1, 2, 3) and persisted across restarts
**Business Rule** BR-RULE-029 (INV-1: strictly monotonically increasing)
**Priority** P1

#### Scenario: next_expected_at computed for non-final deliveries
**Obligation ID** UC-004-ALT-WEBHOOK-PUSH-REPORTING-06
**Layer** behavioral
**Given** a daily reporting frequency
**When** a scheduled delivery is sent
**Then** `next_expected_at` is approximately 24 hours after current delivery
**Business Rule** Alt-webhook step 6, POST-S10
**Priority** P2

#### Scenario: Webhook payload signed with HMAC-SHA256
**Obligation ID** UC-004-ALT-WEBHOOK-PUSH-REPORTING-07
**Layer** behavioral
**Given** a webhook configured with HMAC-SHA256 authentication
**When** the payload is delivered
**Then** the POST request includes the HMAC signature header
**Business Rule** POST-S8 (buyer can verify authenticity)
**Priority** P1

#### Scenario: Webhook payload signed with Bearer token
**Obligation ID** UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
**Layer** behavioral
**Given** a webhook configured with Bearer token authentication
**When** the payload is delivered
**Then** the POST request includes the Bearer token in Authorization header
**Business Rule** POST-S8
**Priority** P2

#### Scenario: Webhook does NOT include aggregated_totals
**Obligation ID** UC-004-ALT-WEBHOOK-PUSH-REPORTING-09
**Layer** behavioral
**Given** a webhook delivery
**When** the payload is assembled
**Then** `aggregated_totals` is NOT included (polling only per gap G43)
**Business Rule** Alt-webhook note, gap G43
**Priority** P2

#### Scenario: Webhook filters to requested_metrics
**Obligation ID** UC-004-ALT-WEBHOOK-PUSH-REPORTING-10
**Layer** behavioral
**Given** a webhook configured with `requested_metrics: [impressions, clicks]`
**When** the delivery payload is assembled
**Then** only impressions and clicks metrics are included (spend, video, etc. excluded)
**Business Rule** Alt-webhook step 3
**Priority** P2

#### Scenario: Only active media buys trigger webhook delivery
**Obligation ID** UC-004-ALT-WEBHOOK-PUSH-REPORTING-11
**Layer** behavioral
**Given** a media buy that is paused
**When** the scheduler fires
**Then** the webhook is NOT triggered for paused media buys
**Business Rule** Alt-webhook precondition (active delivery status)
**Priority** P2

#### Scenario: Endpoint acknowledges with 2xx
**Obligation ID** UC-004-ALT-WEBHOOK-PUSH-REPORTING-12
**Layer** behavioral
**Given** a webhook delivery sent to the buyer's endpoint
**When** the endpoint responds with 200 OK
**Then** the system records successful delivery and updates circuit breaker state to healthy
**Business Rule** Alt-webhook step 9-10
**Priority** P1

---

### Extension *a: Authentication Error
Source: BR-UC-004-ext-a.md

#### Scenario: No principal_id in request context
**Obligation ID** UC-004-EXT-A-01
**Layer** behavioral
**Given** a request without valid authentication credentials
**When** the system attempts to extract `principal_id`
**Then** the system returns error `principal_id_missing` with protocol envelope status `failed`
**Business Rule** POST-F1 (state unchanged), POST-F2 (buyer knows error)
**Priority** P0 -- security gate

#### Scenario: System state unchanged on auth failure
**Obligation ID** UC-004-EXT-A-02
**Layer** behavioral
**Given** an authentication failure
**When** the error is returned
**Then** no delivery data is returned, and no state is modified (read-only operation)
**Business Rule** POST-F1
**Priority** P1

---

### Extension *b: Principal Not Found
Source: BR-UC-004-ext-b.md

#### Scenario: Principal ID not in tenant database
**Obligation ID** UC-004-EXT-B-01
**Layer** behavioral
**Given** a request with a valid authentication token but the principal does not exist in the tenant database
**When** the system queries for the principal
**Then** the system returns error `principal_not_found`
**Business Rule** POST-F2
**Priority** P1

---

### Extension *c: Media Buy Not Found
Source: BR-UC-004-ext-c.md

#### Scenario: Media buy identifier does not exist
**Obligation ID** UC-004-EXT-C-01
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer requests delivery for `media_buy_ids: ["nonexistent_id"]`
**Then** the system returns error `media_buy_not_found` including the unresolved identifier
**Business Rule** BR-RULE-030 (resolution failure)
**Priority** P1

#### Scenario: Partial failure -- some IDs not found
**Obligation ID** UC-004-EXT-C-02
**Layer** behavioral
**Given** an authenticated buyer owns mb_1 but mb_999 does not exist
**When** the buyer requests delivery for `media_buy_ids: ["mb_1", "mb_999"]`
**Then** per BR-RULE-030 INV-5, partial results are returned (mb_1 only). However, ext-c says error is returned. Test must verify which behavior the implementation follows.
**Business Rule** BR-RULE-030 (INV-5 partial resolution) vs ext-c (error for not found) -- VERIFY IMPLEMENTATION
**Priority** P1 -- specification conflict to resolve

#### Scenario: buyer_ref does not resolve
**Obligation ID** UC-004-EXT-C-03
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer requests delivery for `buyer_refs: ["no_such_ref"]`
**Then** the system returns error `media_buy_not_found`
**Business Rule** BR-RULE-030
**Priority** P1

---

### Extension *d: Ownership Mismatch
Source: BR-UC-004-ext-d.md

#### Scenario: Principal does not own the media buy
**Obligation ID** UC-004-EXT-D-01
**Layer** behavioral
**Given** buyer A is authenticated, and media buy mb_owned_by_B belongs to buyer B
**When** buyer A requests delivery for `media_buy_ids: ["mb_owned_by_B"]`
**Then** the system returns error `media_buy_not_found` (NOT `ownership_mismatch` -- security: does not reveal existence)
**Business Rule** PRE-BIZ3, security note in ext-d
**Priority** P0 -- security gate

#### Scenario: Ownership error returns media_buy_not_found not ownership_mismatch
**Obligation ID** UC-004-EXT-D-02
**Layer** behavioral
**Given** a media buy that exists but is owned by another principal
**When** the non-owner requests delivery
**Then** the error code is `media_buy_not_found` (no information leakage about existence)
**Business Rule** Ext-d security note
**Priority** P0 -- security requirement

#### Scenario: Mixed ownership -- some owned, some not
**Obligation ID** UC-004-EXT-D-03
**Layer** behavioral
**Given** buyer A owns mb_1 and mb_2, buyer B owns mb_3
**When** buyer A requests delivery for `media_buy_ids: ["mb_1", "mb_2", "mb_3"]`
**Then** the system returns error for the entire request (mb_3 fails ownership) OR partial results (per BR-RULE-030 INV-5) -- VERIFY IMPLEMENTATION
**Business Rule** BR-RULE-030 vs PRE-BIZ3 -- behavior when some fail ownership check
**Priority** P1 -- specification conflict to resolve

---

### Extension *e: Invalid Date Range
Source: BR-UC-004-ext-e.md

#### Scenario: start_date equals end_date
**Obligation ID** UC-004-EXT-E-01
**Layer** behavioral
**Given** an authenticated buyer with valid media buys
**When** the buyer sends `start_date: "2026-03-15"` and `end_date: "2026-03-15"`
**Then** the system returns error `invalid_date_range`
**Business Rule** BR-RULE-013 (start_date >= end_date is invalid)
**Priority** P1

#### Scenario: start_date after end_date
**Obligation ID** UC-004-EXT-E-02
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer sends `start_date: "2026-03-20"` and `end_date: "2026-03-10"`
**Then** the system returns error `invalid_date_range`
**Business Rule** BR-RULE-013
**Priority** P1

#### Scenario: State unchanged on date range error
**Obligation ID** UC-004-EXT-E-03
**Layer** behavioral
**Given** an invalid date range
**When** the error is returned
**Then** no delivery data is fetched or returned (read-only operation, no state change)
**Business Rule** POST-F1
**Priority** P2

---

### Extension *f: Adapter Error
Source: BR-UC-004-ext-f.md

#### Scenario: Adapter unavailable
**Obligation ID** UC-004-EXT-F-01
**Layer** behavioral
**Given** valid request with authenticated buyer and resolved media buys
**When** the ad server adapter is unavailable (network error, timeout)
**Then** the system returns error `adapter_error`
**Business Rule** POST-F2 (buyer knows delivery data could not be retrieved)
**Priority** P1

#### Scenario: Adapter returns internal error
**Obligation ID** UC-004-EXT-F-02
**Layer** behavioral
**Given** valid request with authenticated buyer
**When** the ad server adapter returns a 500 internal server error
**Then** the system returns error `adapter_error`
**Business Rule** Ext-f step 7b
**Priority** P2

#### Scenario: Adapter failure logged to audit trail
**Obligation ID** UC-004-EXT-F-03
**Layer** behavioral
**Given** an adapter failure
**When** the error occurs
**Then** the adapter failure is logged to the audit trail (NFR-003)
**Business Rule** Ext-f step 7c
**Priority** P2

#### Scenario: State unchanged on adapter error
**Obligation ID** UC-004-EXT-F-04
**Layer** behavioral
**Given** an adapter error
**When** the error is returned
**Then** no state is modified (read-only operation)
**Business Rule** POST-F1
**Priority** P2

---

### Extension *g: Webhook Delivery Failure
Source: BR-UC-004-ext-g.md

#### Scenario: Transient failure -- 5xx triggers retry with exponential backoff
**Obligation ID** UC-004-EXT-G-01
**Layer** behavioral
**Given** a webhook endpoint that returns 503
**When** the first delivery attempt fails
**Then** the system retries up to 3 times with exponential backoff (1s, 2s, 4s + jitter)
**Business Rule** BR-RULE-029 (INV-3: 5xx/network = retry with backoff)
**Priority** P1

#### Scenario: Retry succeeds on second attempt
**Obligation ID** UC-004-EXT-G-02
**Layer** behavioral
**Given** a webhook endpoint that fails on first attempt but succeeds on second
**When** the retry fires
**Then** the delivery is recorded as successful
**Business Rule** Ext-g transient flow step 8d
**Priority** P2

#### Scenario: All retries exhausted -- circuit breaker opens
**Obligation ID** UC-004-EXT-G-03
**Layer** behavioral
**Given** a webhook endpoint that fails 3 consecutive times
**When** all retry attempts are exhausted
**Then** the circuit breaker opens for this endpoint, the delivery is marked `reporting_delayed`, and subsequent deliveries are suppressed
**Business Rule** Ext-g persistent flow steps 8c-8e
**Priority** P1

#### Scenario: Circuit breaker half-open probe
**Obligation ID** UC-004-EXT-G-04
**Layer** behavioral
**Given** an open circuit breaker for a webhook endpoint
**When** the circuit breaker timer fires
**Then** the system attempts a half-open probe to check endpoint recovery
**Business Rule** Ext-g persistent flow step 8f
**Priority** P2

#### Scenario: 4xx error -- no retry (client error)
**Obligation ID** UC-004-EXT-G-05
**Layer** behavioral
**Given** a webhook endpoint that returns 401 Forbidden
**When** the delivery attempt fails
**Then** the system does NOT retry (authentication errors are not transient), marks webhook as failed
**Business Rule** BR-RULE-029 (INV-4: 4xx = no retry)
**Priority** P1

#### Scenario: 401/403 authentication rejection -- webhook marked failed
**Obligation ID** UC-004-EXT-G-06
**Layer** behavioral
**Given** a webhook delivery where HMAC signature verification fails at the endpoint
**When** the endpoint returns 401/403
**Then** the system logs authentication rejection, does not retry, and marks the webhook as failed
**Business Rule** Ext-g auth flow steps 8c-8e
**Priority** P1

#### Scenario: Buyer must reconfigure credentials after auth rejection
**Obligation ID** UC-004-EXT-G-07
**Layer** behavioral
**Given** a webhook marked as failed due to 401/403
**When** the buyer wants to resume webhook deliveries
**Then** the buyer must reconfigure webhook credentials via UC-003 (Update Media Buy)
**Business Rule** Ext-g recovery step 3
**Priority** P3 -- documentation

#### Scenario: Webhook failures do not produce synchronous error to buyer
**Obligation ID** UC-004-EXT-G-08
**Layer** behavioral
**Given** a webhook delivery failure (any type)
**When** the failure occurs
**Then** there is no synchronous error response to the buyer (no request/response cycle); the buyer detects missing reports via sequence number gaps
**Business Rule** Ext-g postcondition note
**Priority** P2

---

## Cross-Cutting Concerns for 3.6 Upgrade

### PricingOption Type Consistency [salesagent-mq3n]

#### Scenario: PricingOption model has string pricing_option_id field accessible for lookup
**Obligation ID** UC-004-PRICINGOPTION-TYPE-CONSISTENCY-01
**Layer** behavioral
**Given** the PricingOption model in salesagent
**When** delivery code looks up a PricingOption by its identifier
**Then** the lookup uses the string `pricing_option_id` field (e.g., "cpm_usd_fixed"), NOT the integer primary key
**Business Rule** salesagent-mq3n
**Priority** P0 -- CRITICAL, blocks all delivery metrics

#### Scenario: PricingOption string-to-integer comparison detected and rejected
**Obligation ID** UC-004-PRICINGOPTION-TYPE-CONSISTENCY-02
**Layer** behavioral
**Given** code that compares `pricing_option_id` string to a database integer PK
**When** the comparison is evaluated
**Then** the comparison must be caught by a type check or unit test (never silently succeed/fail)
**Business Rule** salesagent-mq3n (silent data loss prevention)
**Priority** P0

#### Scenario: End-to-end delivery metrics with CPM pricing
**Obligation ID** UC-004-PRICINGOPTION-TYPE-CONSISTENCY-03
**Layer** behavioral
**Given** a media buy created with CPM pricing option (string ID "cpm_usd_fixed"), 10,000 delivered impressions, rate $2.50 CPM
**When** `get_media_buy_delivery` is called
**Then** spend is correctly computed as $25.00, and the pricing option is correctly identified in the response
**Business Rule** salesagent-mq3n (end-to-end validation)
**Priority** P0

#### Scenario: End-to-end delivery metrics with CPC pricing
**Obligation ID** UC-004-PRICINGOPTION-TYPE-CONSISTENCY-04
**Layer** behavioral
**Given** a media buy created with CPC pricing option, 500 clicks, rate $0.50 CPC
**When** `get_media_buy_delivery` is called
**Then** spend is correctly computed as $250.00, and the pricing option is correctly identified
**Business Rule** salesagent-mq3n
**Priority** P1

#### Scenario: End-to-end delivery metrics with FLAT_RATE pricing
**Obligation ID** UC-004-PRICINGOPTION-TYPE-CONSISTENCY-05
**Layer** behavioral
**Given** a media buy created with FLAT_RATE pricing option, total rate $5,000
**When** `get_media_buy_delivery` is called
**Then** spend reflects the flat rate correctly
**Business Rule** salesagent-mq3n
**Priority** P1

### Response Serialization [salesagent-7gnv]

#### Scenario: GetMediaBuyDeliveryResponse nested serialization
**Obligation ID** UC-004-RESPONSE-SERIALIZATION-SALESAGENT-01
**Layer** schema
**Given** a delivery response with multiple media_buy_deliveries, each containing packages
**When** `model_dump()` is called on the response
**Then** all nested models (media_buy_deliveries, packages, delivery_metrics) are correctly serialized (NestedModelSerializerMixin applied)
**Business Rule** Critical pattern #4 (explicit nested serialization)
**Priority** P1

#### Scenario: Delivery response preserves ext fields
**Obligation ID** UC-004-RESPONSE-SERIALIZATION-SALESAGENT-02
**Layer** behavioral
**Given** a media buy with extension fields in the delivery context
**When** the delivery response is serialized
**Then** `ext` fields are preserved in the output
**Business Rule** salesagent-7gnv
**Priority** P1
