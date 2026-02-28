# Constraints -- Test Obligations

## 3.6 Upgrade Impact

The following constraint groups are directly affected by the adcp 3.2.0 -> 3.6.0 upgrade:

| Constraint Area | Impact | Related Bugs |
|----------------|--------|--------------|
| product.yaml | `additional_properties: true`; 6 new fields (channels, catalog_match, catalog_types, conversion_tracking, data_provider_signals, forecast, signal_targeting_allowed); publisher_properties uses new selector schema | salesagent-qo8a (FIXED) |
| pricing-option.yaml | 9 pricing models (cpm, vcpm, cpc, cpcv, cpv, cpp, cpa, time, flat_rate); delivery is now a reference object, not an integer | salesagent-mq3n (delivery lookup string vs int PK) |
| media-buy.yaml | New fields: account_id, proposal_id, buyer_campaign_ref, creative_deadline, ext | salesagent-7gnv (boundary drops fields) |
| create-media-buy-request.yaml | New fields: account_id, proposal_id, brand (BrandReference), artifact_webhook; packages conditionally required | salesagent-7gnv |
| create-media-buy-response.yaml | New fields: warnings, ext | -- |
| update-media-buy-request.yaml | New fields: account_id, buyer_campaign_ref, ext | salesagent-7gnv |
| async-response-*.yaml | Entirely new schemas for per-status async responses | -- |
| All account_* constraints | Entire accounts domain is new in v3 | -- |
| All content_standards_* constraints | Entire content standards domain is new in v3 | -- |
| All property_list_* constraints | Entire property lists domain is new in v3 | -- |
| All signal_* constraints | Entire signals domain is new in v3 | -- |
| capabilities_* constraints | Capabilities endpoint is new in v3 | -- |
| auth/principal_id.yaml | Discovery auth pattern now covers capabilities + accounts | -- |

## Constraints

### product: Product Entity Schema
**Obligation ID** CONSTR-PRODUCT-01
**Layer** schema
**Requirement:** Product must have required fields (product_id, name, description, publisher_properties, format_ids, delivery_type, delivery_measurement, pricing_options). v3 changes `additional_properties` from false to true. New optional fields: channels, catalog_match, catalog_types, conversion_tracking, data_provider_signals, forecast, signal_targeting_allowed.
**Scenario:**
```gherkin
Given a product with all required fields populated
When serialized to AdCP schema
Then all required fields are present and extra fields are allowed

Given a product with new v3 field channels=["display", "olv"]
When serialized to AdCP schema
Then channels array is included with uniqueItems enforcement
```
**Priority:** P0
**Affected by 3.6:** Yes -- additional_properties changed, 6+ new fields

---

### pricing-option: Pricing Option Entity Schema
**Obligation ID** CONSTR-PRICING-OPTION-01
**Layer** schema
**Requirement:** PricingOption requires pricing_model (enum of 9 models), currency (ISO 4217), and exactly one of fixed_price/floor_price (XOR). v3 adds model-specific sub-schemas under /schemas/pricing/. delivery field is now an object reference, not an integer.
**Scenario:**
```gherkin
Given a pricing option with pricing_model="cpm" and fixed_price=5.0
When validated against the v3 schema
Then the option is valid with the cpm sub-schema applied

Given a pricing option with delivery field as integer
When processed in v3
Then the system must handle the delivery field as an object reference, not integer PK
```
**Priority:** P0
**Affected by 3.6:** Yes -- 9 pricing models, delivery field type change. Directly relates to salesagent-mq3n.

---

### media-buy: Media Buy Entity Schema
**Obligation ID** CONSTR-MEDIA-BUY-01
**Layer** schema
**Requirement:** MediaBuy requires media_buy_id, buyer_ref, status. v3 adds: account_id, buyer_campaign_ref, creative_deadline, ext. additional_properties: true.
**Scenario:**
```gherkin
Given a media buy with buyer_campaign_ref="CAMP-2024-Q1"
When serialized to AdCP schema
Then buyer_campaign_ref is preserved in the response

Given a media buy with ext={"custom_field": "value"}
When serialized to AdCP schema
Then ext object is preserved unchanged
```
**Priority:** P0
**Affected by 3.6:** Yes -- new fields. Directly relates to salesagent-7gnv (boundary drops buyer_campaign_ref, creative_deadline, ext).

---

### package: Package Entity Schema
**Obligation ID** CONSTR-PACKAGE-01
**Layer** schema
**Requirement:** Package requires product_id, budget, pricing_option. v3 changes: additional_properties: true, delivery is object reference. Targeting overlay is optional.
**Scenario:**
```gherkin
Given a package with all required fields and a targeting_overlay
When validated
Then the package is valid

Given a package in update mode with product_id in payload
When update schema validates
Then product_id is rejected (immutable field, not in update schema)
```
**Priority:** P0
**Affected by 3.6:** Yes -- delivery field type change

---

### targeting: Targeting Schema
**Obligation ID** CONSTR-TARGETING-01
**Layer** schema
**Requirement:** Targeting object supports geo_countries, geo_regions, geo_dma, geo_zip, and custom dimensions. v3: additional_properties: true.
**Scenario:**
```gherkin
Given targeting with geo_countries include=["US"] and exclude=["US"]
When validated
Then rejected (same value in include and exclude per BR-RULE-014)
```
**Priority:** P1
**Affected by 3.6:** Yes -- additional_properties changed

---

### targeting_overlay: Targeting Overlay Validation
**Obligation ID** CONSTR-TARGETING-OVERLAY-01
**Layer** behavioral
**Requirement:** Targeting overlay applied on packages validates: unknown fields rejected, managed-only dimensions rejected, geo overlap rejected. Empty/absent is valid.
**Scenario:**
```gherkin
Given a targeting overlay with unknown field "custom_xyz"
When validated
Then the overlay is rejected with unknown field error

Given an empty targeting overlay {}
When validated
Then the overlay passes validation
```
**Priority:** P1
**Affected by 3.6:** No

---

### create-media-buy-request: Create Media Buy Request Schema
**Obligation ID** CONSTR-CREATE-MEDIA-BUY-REQUEST-01
**Layer** behavioral
**Requirement:** Required: buyer_ref, brand, start_time, end_time. v3: packages no longer unconditionally required (conditional on proposal_id). New fields: account_id, proposal_id, artifact_webhook. brand is now BrandReference object.
**Scenario:**
```gherkin
Given a create request with proposal_id and total_budget but no packages
When validated against v3 schema
Then the request is valid (proposal mode)

Given a create request without buyer_ref
When validated
Then the request is rejected (required field)

Given a create request with brand as a BrandReference object
When processed
Then brand is validated as BrandReference, not plain string
```
**Priority:** P0
**Affected by 3.6:** Yes -- conditional packages, new fields, brand type change

---

### create-media-buy-response: Create Media Buy Response Schema
**Obligation ID** CONSTR-CREATE-MEDIA-BUY-RESPONSE-01
**Layer** behavioral
**Requirement:** Atomic: success variant (media_buy_id, buyer_ref, packages, status) OR error variant (errors[]). v3 adds: warnings, ext to success variant.
**Scenario:**
```gherkin
Given a successful creation with warnings
When the response is assembled
Then both success fields and warnings are present, no errors field
```
**Priority:** P0
**Affected by 3.6:** Yes -- warnings, ext fields added

---

### update-media-buy-request: Update Media Buy Request Schema
**Obligation ID** CONSTR-UPDATE-MEDIA-BUY-REQUEST-01
**Layer** behavioral
**Requirement:** XOR identification (media_buy_id or buyer_ref). Partial update semantics. v3 adds: account_id, buyer_campaign_ref, ext.
**Scenario:**
```gherkin
Given an update request with buyer_campaign_ref="CAMP-2024-Q2"
When processed
Then buyer_campaign_ref is updated on the media buy

Given an update request with ext={"tracking": "abc"}
When processed
Then ext field is preserved
```
**Priority:** P0
**Affected by 3.6:** Yes -- new fields. Directly relates to salesagent-7gnv.

---

### update-media-buy-response: Update Media Buy Response Schema
**Obligation ID** CONSTR-UPDATE-MEDIA-BUY-RESPONSE-01
**Layer** behavioral
**Requirement:** Atomic: success OR error. v3 adds warnings, ext to success variant.
**Scenario:**
```gherkin
Given an update that produces warnings
When the response is returned
Then warnings array is included alongside success fields
```
**Priority:** P1
**Affected by 3.6:** Yes -- warnings, ext fields

---

### get-products-request: Get Products Request Schema
**Obligation ID** CONSTR-GET-PRODUCTS-REQUEST-01
**Layer** behavioral
**Requirement:** Optional brief, brand, budget, filters. v3 adds channels filter, product-filters object with delivery_type.
**Scenario:**
```gherkin
Given a get_products request with channels=["display", "ctv"]
When products are filtered
Then only products matching those channels are returned
```
**Priority:** P1
**Affected by 3.6:** Yes -- new filter fields

---

### get-products-response: Get Products Response Schema
**Obligation ID** CONSTR-GET-PRODUCTS-RESPONSE-01
**Layer** schema
**Requirement:** products array with relevance_score, matching context echo. v3: additional_properties: true on products, proposal_id in response.
**Scenario:**
```gherkin
Given products returned with proposal_id
When the response is assembled
Then proposal_id is included for use in create_media_buy proposal mode
```
**Priority:** P1
**Affected by 3.6:** Yes -- proposal_id, additional_properties

---

### protocol-envelope: Protocol Envelope Schema
**Obligation ID** CONSTR-PROTOCOL-ENVELOPE-01
**Layer** behavioral
**Requirement:** Wrapper with status (9-value enum), payload, optional context_id, task_id, message, timestamp, push_notification_config. State machine: submitted/working/input-required are non-terminal; completed/failed/canceled/rejected/auth-required are terminal.
**Scenario:**
```gherkin
Given a response with status="submitted"
When a webhook is configured
Then webhook notification is triggered for async updates

Given a response with status="completed"
Then no webhook is triggered (terminal state)
```
**Priority:** P1
**Affected by 3.6:** Yes -- protocol envelope is fundamental to v3 async patterns

---

### async-response-get-products: Get Products Async Responses
**Obligation ID** CONSTR-ASYNC-RESPONSE-GET-PRODUCTS-01
**Layer** schema
**Requirement:** Per-status response schemas: submitted (estimated_completion), working (percentage, current_step), input-required (reason, partial_results, suggestions). All include context + ext.
**Scenario:**
```gherkin
Given a long-running product search
When status transitions to "working"
Then the response includes percentage and current_step fields

Given status is "input-required" with reason="CLARIFICATION_NEEDED"
When the response is returned
Then partial_results may be included to help inform the clarification
```
**Priority:** P2
**Affected by 3.6:** Yes -- entirely new in v3

---

### async-response-create-media-buy: Create Media Buy Async Responses
**Obligation ID** CONSTR-ASYNC-RESPONSE-CREATE-MEDIA-BUY-01
**Layer** behavioral
**Requirement:** Per-status schemas: submitted (context, ext), working (percentage, current_step), input-required (reason: APPROVAL_REQUIRED | BUDGET_EXCEEDS_LIMIT, errors).
**Scenario:**
```gherkin
Given a create_media_buy that requires approval
When status is "input-required"
Then reason="APPROVAL_REQUIRED" maps to HITL pattern
```
**Priority:** P2
**Affected by 3.6:** Yes -- entirely new in v3

---

### get-media-buy-delivery-request: Delivery Request Schema
**Obligation ID** CONSTR-GET-MEDIA-BUY-DELIVERY-REQUEST-01
**Layer** schema
**Requirement:** Optional media_buy_ids (priority), buyer_refs, start_date, end_date, status_filter, account_id. media_buy_ids takes precedence. Neither = all principal's buys.
**Scenario:**
```gherkin
Given both media_buy_ids and buyer_refs provided
When delivery is queried
Then only media_buy_ids are used

Given start_date after end_date
When delivery is queried
Then the request is rejected (inverted date range)
```
**Priority:** P1
**Affected by 3.6:** Yes -- account_id filter is new

---

### reporting-webhook: Webhook Configuration Schema
**Obligation ID** CONSTR-REPORTING-WEBHOOK-01
**Layer** behavioral
**Requirement:** url (URI), authentication (schemes: Bearer|HMAC-SHA256, credentials min 32 chars), reporting_frequency (hourly|daily|monthly), optional requested_metrics, token (min 16 chars). Payload: notification_type, sequence_number, next_expected_at (conditional), partial_data.
**Scenario:**
```gherkin
Given webhook credentials with 31 characters
When webhook configuration is validated
Then it is rejected (minimum 32 chars)

Given HMAC-SHA256 signing
When a webhook is delivered
Then X-ADCP-Signature and X-ADCP-Timestamp headers are present
```
**Priority:** P1
**Affected by 3.6:** No

---

### brand_manifest_policy: Brand Manifest Policy Gate
**Obligation ID** CONSTR-BRAND-MANIFEST-POLICY-01
**Layer** schema
**Requirement:** Enum: public, require_auth, require_brand. Default require_auth. require_brand requires brand field in request.
**Scenario:**
```gherkin
Given policy="require_brand" and no brand in request
When get_products is called
Then the request is rejected

Given default policy (require_auth) and authenticated caller
When get_products is called
Then the request proceeds
```
**Priority:** P1
**Affected by 3.6:** Yes -- brand is now BrandReference object, enforcement at all boundaries

---

### publisher_domains (response): Portfolio Assembly Output
**Obligation ID** CONSTR-PUBLISHER-DOMAINS-PORTFOLIO-01
**Layer** behavioral
**Requirement:** Array of domain strings, sorted alphabetically. All partnerships included regardless of verification. Empty = empty array.
**Scenario:**
```gherkin
Given publishers ["xyz.com", "abc.com"]
When list_authorized_properties returns
Then domains are ["abc.com", "xyz.com"] (alphabetical)
```
**Priority:** P1
**Affected by 3.6:** No

---

### publisher_domains_filter (request): Domain Filter
**Obligation ID** CONSTR-PUBLISHER-DOMAINS-FILTER-01
**Layer** schema
**Requirement:** Optional array, minItems: 1. Pattern: lowercase alphanumeric + hyphens + dots. Invalid format = DOMAIN_INVALID_FORMAT. Valid non-matching = empty results.
**Scenario:**
```gherkin
Given filter=["CNN.COM"]
Then rejected with DOMAIN_INVALID_FORMAT

Given filter=[]
Then rejected (minItems: 1)
```
**Priority:** P2
**Affected by 3.6:** No

---

### advertising_policies: Policy Disclosure
**Obligation ID** CONSTR-ADVERTISING-POLICIES-01
**Layer** schema
**Requirement:** Optional string (minLength: 1, maxLength: 10000). Present only when policy enabled AND at least one array non-empty. Omitted otherwise.
**Scenario:**
```gherkin
Given policy enabled with prohibited_categories=["gambling"]
Then advertising_policies field contains "gambling" text

Given policy enabled but all arrays empty
Then advertising_policies field is omitted entirely
```
**Priority:** P2
**Affected by 3.6:** No

---

### validation_mode: Sync Creatives Validation Mode
**Obligation ID** CONSTR-VALIDATION-MODE-01
**Layer** schema
**Requirement:** Enum: strict|lenient. Default strict. Strict aborts on assignment error. Lenient logs warning and continues.
**Scenario:**
```gherkin
Given validation_mode="partial" (unknown value)
Then schema validation error
```
**Priority:** P1
**Affected by 3.6:** No

---

### brief_policy: Brief Policy Compliance
**Obligation ID** CONSTR-BRIEF-POLICY-01
**Layer** behavioral
**Requirement:** Behavioral constraint. Policy disabled = unchecked. BLOCKED = POLICY_VIOLATION. Service unavailable = fail-open.
**Scenario:**
```gherkin
Given policy enabled and LLM returns BLOCKED
Then request rejected with POLICY_VIOLATION
```
**Priority:** P2
**Affected by 3.6:** No

---

### principal_visibility: Principal-Scoped Visibility
**Obligation ID** CONSTR-PRINCIPAL-VISIBILITY-01
**Layer** behavioral
**Requirement:** null/empty allowed_principal_ids = visible to all. Non-empty = only listed principals. Anonymous cannot see restricted.
**Scenario:**
```gherkin
Given allowed_principal_ids=["p1"] and caller is anonymous
Then product is suppressed
```
**Priority:** P1
**Affected by 3.6:** No

---

### anonymous_pricing: Anonymous Pricing Suppression
**Obligation ID** CONSTR-ANONYMOUS-PRICING-01
**Layer** behavioral
**Requirement:** Authenticated = full pricing. Anonymous = pricing_options=[].
**Scenario:**
```gherkin
Given anonymous request
Then every product has pricing_options=[]
```
**Priority:** P1
**Affected by 3.6:** No

---

### relevance_threshold: AI Ranking Threshold
**Obligation ID** CONSTR-RELEVANCE-THRESHOLD-01
**Layer** behavioral
**Requirement:** Score >= 0.1 included, < 0.1 excluded. Range 0.0-1.0. No ranking = no threshold.
**Scenario:**
```gherkin
Given ranking active and score=0.09
Then product excluded

Given score=0.1
Then product included (boundary)
```
**Priority:** P2
**Affected by 3.6:** No

---

### pricing_option_xor: Fixed/Floor Price XOR
**Obligation ID** CONSTR-PRICING-OPTION-XOR-01
**Layer** schema
**Requirement:** Exactly one of fixed_price or floor_price. Both = invalid. Neither = invalid. CPA always fixed_price.
**Scenario:**
```gherkin
Given both fixed_price and floor_price set
Then pricing option is invalid
```
**Priority:** P0
**Affected by 3.6:** Yes -- 9 models; CPA has exclusiveMinimum: 0

---

### product_min_cardinality: Product Array Minimums
**Obligation ID** CONSTR-PRODUCT-MIN-CARDINALITY-01
**Layer** schema
**Requirement:** format_ids >= 1, publisher_properties >= 1, pricing_options >= 1. Empty = ValueError.
**Scenario:**
```gherkin
Given product with format_ids=[]
Then conversion fails with ValueError
```
**Priority:** P0
**Affected by 3.6:** No

---

### currency_consistency: Currency Across Packages
**Obligation ID** CONSTR-CURRENCY-CONSISTENCY-01
**Layer** behavioral
**Requirement:** All packages same currency. Currency in tenant CurrencyLimit table.
**Scenario:**
```gherkin
Given packages with ["USD", "EUR"]
Then rejected for mixed currencies
```
**Priority:** P0
**Affected by 3.6:** No

---

### product_uniqueness: Product ID Uniqueness
**Obligation ID** CONSTR-PRODUCT-UNIQUENESS-01
**Layer** behavioral
**Requirement:** No duplicate product_id across packages in a media buy.
**Scenario:**
```gherkin
Given two packages both with product_id="prod_1"
Then rejected for duplicate product
```
**Priority:** P1
**Affected by 3.6:** No

---

### creative_asset: Creative Asset Conditional Presence
**Obligation ID** CONSTR-CREATIVE-ASSET-01
**Layer** behavioral
**Requirement:** Reference creatives require url+width+height. Generative formats exempt. Errors collected.
**Scenario:**
```gherkin
Given reference creative missing url
Then error collected
```
**Priority:** P1
**Affected by 3.6:** Yes -- creative asset structure changed in v3

---

### budget_amount: Budget Positivity
**Obligation ID** CONSTR-BUDGET-AMOUNT-01
**Layer** schema
**Requirement:** amount > 0. Schema minimum: 0 but business rule requires > 0.
**Scenario:**
```gherkin
Given amount=0
Then rejected by business rule (not schema)
```
**Priority:** P0
**Affected by 3.6:** No

---

### daily_spend_cap: Daily Spend Cap
**Obligation ID** CONSTR-DAILY-SPEND-CAP-01
**Layer** schema
**Requirement:** daily_budget = budget / max(1, flight_days) <= max_daily_package_spend. Cap not configured = skipped.
**Scenario:**
```gherkin
Given budget=10000, flight=2 days, cap=4000 (daily=5000)
Then rejected (5000 > 4000)
```
**Priority:** P1
**Affected by 3.6:** No

---

### start_time: Start Time Validation
**Obligation ID** CONSTR-START-TIME-01
**Layer** behavioral
**Requirement:** Required. "asap" (case-sensitive) = current UTC. Must be future. Naive = UTC.
**Scenario:**
```gherkin
Given start_time in the past
Then rejected

Given start_time="asap"
Then resolves to now
```
**Priority:** P0
**Affected by 3.6:** No

---

### end_time: End Time Validation
**Obligation ID** CONSTR-END-TIME-01
**Layer** schema
**Requirement:** Required. Must be strictly after start_time. Naive = UTC.
**Scenario:**
```gherkin
Given end_time = start_time
Then rejected (must be strictly after)
```
**Priority:** P0
**Affected by 3.6:** No

---

### creative_replacement: Creative Replacement Semantics
**Obligation ID** CONSTR-CREATIVE-REPLACEMENT-01
**Layer** behavioral
**Requirement:** creative_ids/creative_assignments replaces all existing. Not a merge.
**Scenario:**
```gherkin
Given existing [A,B,C] and update provides [B,D]
Then result is [B,D]; A,C deleted
```
**Priority:** P1
**Affected by 3.6:** No

---

### creative_state_validation: Creative State + Format Compatibility
**Obligation ID** CONSTR-CREATIVE-STATE-VALIDATION-01
**Layer** behavioral
**Requirement:** error/rejected state cannot be assigned. Format must be compatible with product.
**Scenario:**
```gherkin
Given creative in "error" state
Then assignment rejected with INVALID_CREATIVES
```
**Priority:** P1
**Affected by 3.6:** No

---

### placement_id_validation: Placement ID Validation
**Obligation ID** CONSTR-PLACEMENT-ID-VALIDATION-01
**Layer** behavioral
**Requirement:** All placement_ids must be valid for product. Product without placement support rejects placement_ids.
**Scenario:**
```gherkin
Given invalid placement_id for product
Then rejected with invalid_placement_ids
```
**Priority:** P2
**Affected by 3.6:** No

---

### approval_workflow: Approval Workflow Determination
**Obligation ID** CONSTR-APPROVAL-WORKFLOW-01
**Layer** behavioral
**Requirement:** Dual-flag: tenant human_review_required (default true) + adapter manual_approval_required. Either true = pending.
**Scenario:**
```gherkin
Given both flags false
Then auto-approved
```
**Priority:** P1
**Affected by 3.6:** No

---

### media_buy_resolution: Media Buy Resolution (OR)
**Obligation ID** CONSTR-MEDIA-BUY-RESOLUTION-01
**Layer** behavioral
**Requirement:** Optional media_buy_ids (priority), buyer_refs (fallback), neither = all. Partial resolution, zero results = empty array.
**Scenario:**
```gherkin
Given neither media_buy_ids nor buyer_refs
Then all principal's media buys returned
```
**Priority:** P1
**Affected by 3.6:** No

---

### delivery_date_range: Delivery Date Range
**Obligation ID** CONSTR-DELIVERY-DATE-RANGE-01
**Layer** schema
**Requirement:** start_date < end_date. Both omitted = full campaign range.
**Scenario:**
```gherkin
Given start_date = end_date
Then rejected (zero-length period)
```
**Priority:** P2
**Affected by 3.6:** No

---

### format_type_filter: Format Type Filter
**Obligation ID** CONSTR-FORMAT-TYPE-FILTER-01
**Layer** schema
**Requirement:** FormatCategory enum (audio, video, display, native, dooh, rich_media, universal). Exact match.
**Scenario:**
```gherkin
Given type_filter="display"
Then only display formats returned
```
**Priority:** P2
**Affected by 3.6:** No

---

### format_ids_filter: Format IDs Filter
**Obligation ID** CONSTR-FORMAT-IDS-FILTER-01
**Layer** behavioral
**Requirement:** Array of FormatId. Matches on id field. Non-matching silently excluded.
**Scenario:**
```gherkin
Given format_ids=["fmt_1", "fmt_nonexistent"]
Then only fmt_1 returned (fmt_nonexistent silently excluded)
```
**Priority:** P2
**Affected by 3.6:** No

---

### dimension_filter: Dimension Filter
**Obligation ID** CONSTR-DIMENSION-FILTER-01
**Layer** behavioral
**Requirement:** min/max width/height. ANY render match semantics. Formats without dimension info excluded.
**Scenario:**
```gherkin
Given min_width=300 and max_width=728
Then formats where ANY render has width in [300,728] are returned
```
**Priority:** P3
**Affected by 3.6:** No

---

### is_responsive_filter: Responsive Filter
**Obligation ID** CONSTR-IS-RESPONSIVE-FILTER-01
**Layer** schema
**Requirement:** Boolean. true=only responsive. false=only non-responsive. Omitted=all.
**Scenario:**
```gherkin
Given is_responsive=true
Then only formats with at least one responsive render returned
```
**Priority:** P3
**Affected by 3.6:** No

---

### name_search_filter: Name Search Filter
**Obligation ID** CONSTR-NAME-SEARCH-FILTER-01
**Layer** behavioral
**Requirement:** Case-insensitive substring match on format name.
**Scenario:**
```gherkin
Given name_search="BANNER"
Then formats with "banner" in name returned (case-insensitive)
```
**Priority:** P3
**Affected by 3.6:** No

---

### asset_types_filter: Asset Types Filter
**Obligation ID** CONSTR-ASSET-TYPES-FILTER-01
**Layer** schema
**Requirement:** OR semantics. Checks both individual and group assets. Enum: image, video, audio, text, etc.
**Scenario:**
```gherkin
Given asset_types=["image", "video"]
Then formats with either image OR video assets returned
```
**Priority:** P3
**Affected by 3.6:** No

---

### signal_catalog_types_filter: Signal Catalog Types Filter
**Obligation ID** CONSTR-SIGNAL-CATALOG-TYPES-FILTER-01
**Layer** schema
**Requirement:** Enum: marketplace, custom, owned. OR semantics within filter. minItems: 1.
**Scenario:**
```gherkin
Given catalog_types=["marketplace", "custom"]
Then signals of either type returned
```
**Priority:** P2
**Affected by 3.6:** Yes -- signals domain is new in v3

---

### signal_max_cpm_filter: Signal Max CPM Filter
**Obligation ID** CONSTR-SIGNAL-MAX-CPM-FILTER-01
**Layer** schema
**Requirement:** Number, minimum: 0. Signals with cpm > max_cpm excluded.
**Scenario:**
```gherkin
Given max_cpm=0
Then only free signals returned

Given max_cpm=-1
Then rejected (minimum: 0)
```
**Priority:** P2
**Affected by 3.6:** Yes -- signals domain is new in v3

---

### signal_min_coverage_filter: Signal Min Coverage Filter
**Obligation ID** CONSTR-SIGNAL-MIN-COVERAGE-FILTER-01
**Layer** schema
**Requirement:** Number, 0-100. Signals with coverage < threshold excluded.
**Scenario:**
```gherkin
Given min_coverage=100
Then only full-coverage signals returned

Given min_coverage=101
Then rejected (maximum: 100)
```
**Priority:** P2
**Affected by 3.6:** Yes -- signals domain is new in v3

---

### signal_max_results: Signal Max Results
**Obligation ID** CONSTR-SIGNAL-MAX-RESULTS-01
**Layer** schema
**Requirement:** Integer, minimum: 1. Applied as array slice after filtering.
**Scenario:**
```gherkin
Given max_results=0
Then rejected (minimum: 1)

Given max_results=5 and 10 signals match
Then only 5 returned
```
**Priority:** P2
**Affected by 3.6:** Yes -- signals domain is new in v3

---

### signal_agent_segment_id: Signal Agent Segment ID
**Obligation ID** CONSTR-SIGNAL-AGENT-SEGMENT-ID-01
**Layer** behavioral
**Requirement:** Required for activate_signal. Premium IDs (prefix "premium_") trigger APPROVAL_REQUIRED. Auth required.
**Scenario:**
```gherkin
Given signal_id="premium_auto_intenders"
Then APPROVAL_REQUIRED returned

Given no authentication
Then ToolError for missing auth
```
**Priority:** P2
**Affected by 3.6:** Yes -- signals domain is new in v3

---

### signal_data_providers_filter: Signal Data Providers Filter
**Obligation ID** CONSTR-SIGNAL-DATA-PROVIDERS-FILTER-01
**Layer** behavioral
**Requirement:** Array of strings. OR semantics. Case-sensitive match.
**Scenario:**
```gherkin
Given data_providers=["Oracle", "LiveRamp"]
Then signals from either provider returned
```
**Priority:** P3
**Affected by 3.6:** Yes -- signals domain is new in v3

---

### signal_spec: Signal Spec Query
**Obligation ID** CONSTR-SIGNAL-SPEC-01
**Layer** schema
**Requirement:** Natural language string. Case-insensitive substring match against name/description/type. Required if signal_ids omitted (anyOf).
**Scenario:**
```gherkin
Given signal_spec="auto intenders" and no signal_ids
Then signals matching "auto intenders" in name/description returned

Given neither signal_spec nor signal_ids
Then schema validation rejects (anyOf violation)
```
**Priority:** P2
**Affected by 3.6:** Yes -- signals domain is new in v3

---

### signal_deliver_to: Signal Delivery Targets
**Obligation ID** CONSTR-SIGNAL-DELIVER-TO-01
**Layer** schema
**Requirement:** Required object with deployments (minItems: 1) and countries (minItems: 1, pattern ^[A-Z]{2}$).
**Scenario:**
```gherkin
Given countries=["us"] (lowercase)
Then rejected (pattern requires uppercase)

Given deployments=[]
Then rejected (minItems: 1)
```
**Priority:** P2
**Affected by 3.6:** Yes -- signals domain is new in v3

---

### format_id_structure: Format ID Object Structure
**Obligation ID** CONSTR-FORMAT-ID-STRUCTURE-01
**Layer** schema
**Requirement:** Object with required agent_url (URI) and id (string). Not a plain string.
**Scenario:**
```gherkin
Given format_id as plain string "banner_300x250"
Then rejected (must be object with agent_url + id)

Given format_id={agent_url: "http://agent.com", id: "banner_300x250"}
Then valid
```
**Priority:** P0
**Affected by 3.6:** Yes -- format_id is now typed object in v3. Relates to salesagent-goy2.

---

### principal_ownership: Principal Ownership Verification
**Obligation ID** CONSTR-PRINCIPAL-OWNERSHIP-01
**Layer** behavioral
**Requirement:** Authenticated principal must match media buy owner. Mismatch = PermissionError or not_found.
**Scenario:**
```gherkin
Given principal_A tries to update media buy owned by principal_B
Then PermissionError or media_buy_not_found returned
```
**Priority:** P0
**Affected by 3.6:** No

---

### immutable_fields: Immutable Package Fields
**Obligation ID** CONSTR-IMMUTABLE-FIELDS-01
**Layer** behavioral
**Requirement:** product_id, format_ids, pricing_option_id not in update schema. Schema-enforced immutability.
**Scenario:**
```gherkin
Given update request with product_id in package payload
Then rejected by schema (field not present in update schema)
```
**Priority:** P1
**Affected by 3.6:** No

---

### principal_id (sync_creatives): Authentication Context
**Obligation ID** CONSTR-PRINCIPAL-AUTHENTICATION-CONTEXT-01
**Layer** behavioral
**Requirement:** Required non-empty string from auth context. Used for creative isolation.
**Scenario:**
```gherkin
Given no authentication context
Then AUTH_REQUIRED error

Given empty principal_id string
Then AUTH_REQUIRED error
```
**Priority:** P0
**Affected by 3.6:** No

---

### media_buy_identification: XOR Identification
**Obligation ID** CONSTR-MEDIA-BUY-IDENTIFICATION-01
**Layer** schema
**Requirement:** Exactly one of media_buy_id or buyer_ref (oneOf). Both = rejected. Neither = rejected.
**Scenario:**
```gherkin
Given both media_buy_id and buyer_ref
Then schema validation rejects

Given neither
Then schema validation rejects
```
**Priority:** P0
**Affected by 3.6:** Yes -- now applies to performance feedback requests

---

### performance_index: Performance Index Scale
**Obligation ID** CONSTR-PERFORMANCE-INDEX-01
**Layer** schema
**Requirement:** Number, minimum: 0. 0.0=no value, 1.0=expected, >1.0=above expected. <0.8 triggers optimization.
**Scenario:**
```gherkin
Given performance_index=-0.5
Then rejected (below minimum)

Given performance_index=0.79
Then optimization recommendation triggered
```
**Priority:** P2
**Affected by 3.6:** Yes -- performance feedback is new in v3

---

### measurement_period: Measurement Period
**Obligation ID** CONSTR-MEASUREMENT-PERIOD-01
**Layer** schema
**Requirement:** Required object with start (date-time) and end (date-time). No schema-level start < end validation.
**Scenario:**
```gherkin
Given measurement_period with missing start
Then schema validation rejects

Given valid ISO 8601 start and end
Then accepted
```
**Priority:** P2
**Affected by 3.6:** Yes -- performance feedback is new in v3

---

### metric_type: Metric Type Enum
**Obligation ID** CONSTR-METRIC-TYPE-01
**Layer** schema
**Requirement:** Enum: overall_performance, conversion_rate, brand_lift, click_through_rate, completion_rate, viewability, brand_safety, cost_efficiency. Default: overall_performance.
**Scenario:**
```gherkin
Given metric_type omitted
Then defaults to "overall_performance"

Given metric_type="engagement_rate"
Then rejected (not in enum)
```
**Priority:** P3
**Affected by 3.6:** Yes -- performance feedback is new in v3

---

### feedback_source: Feedback Source Enum
**Obligation ID** CONSTR-FEEDBACK-SOURCE-01
**Layer** schema
**Requirement:** Enum: buyer_attribution, third_party_measurement, platform_analytics, verification_partner. Default: buyer_attribution.
**Scenario:**
```gherkin
Given feedback_source omitted
Then defaults to "buyer_attribution"
```
**Priority:** P3
**Affected by 3.6:** Yes -- performance feedback is new in v3

---

### perf_feedback_package_id: Package ID in Performance Feedback
**Obligation ID** CONSTR-PERF-FEEDBACK-PACKAGE-ID-01
**Layer** schema
**Requirement:** Optional string, minLength: 1. When omitted, feedback applies to overall media buy.
**Scenario:**
```gherkin
Given package_id="" (empty string)
Then rejected (minLength: 1)

Given package_id omitted
Then feedback applies at media buy level
```
**Priority:** P2
**Affected by 3.6:** Yes -- performance feedback is new in v3

---

### perf_feedback_creative_id: Creative ID in Performance Feedback
**Obligation ID** CONSTR-PERF-FEEDBACK-CREATIVE-ID-01
**Layer** schema
**Requirement:** Optional string, minLength: 1. When omitted, feedback applies at package/media buy level.
**Scenario:**
```gherkin
Given creative_id="" (empty string)
Then rejected (minLength: 1)
```
**Priority:** P3
**Affected by 3.6:** Yes -- performance feedback is new in v3

---

### status_filter: Delivery Status Filter
**Obligation ID** CONSTR-STATUS-FILTER-01
**Layer** schema
**Requirement:** Enum: pending_activation, active, paused, completed. Single string or array (minItems: 1). Omitted = no filter.
**Scenario:**
```gherkin
Given status_filter=["active", "paused"]
Then only active and paused media buys' delivery data returned

Given status_filter="failed"
Then rejected (not in enum)
```
**Priority:** P2
**Affected by 3.6:** Yes -- status enum values may have changed

---

### webhook_credentials: Webhook Authentication Credentials
**Obligation ID** CONSTR-WEBHOOK-CREDENTIALS-01
**Layer** schema
**Requirement:** schemes: Bearer|HMAC-SHA256, credentials min 32 chars. HMAC signs with X-ADCP-Signature + X-ADCP-Timestamp.
**Scenario:**
```gherkin
Given credentials with 31 characters
Then rejected

Given scheme="Basic"
Then rejected (not in enum)
```
**Priority:** P1
**Affected by 3.6:** No

---

### channels: Advertising Media Channel Enum
**Obligation ID** CONSTR-CHANNELS-01
**Layer** schema
**Requirement:** 18 values: display, olv, social, search, ctv, linear_tv, radio, streaming_audio, podcast, dooh, ooh, print, cinema, email, gaming, retail_media, influencer, affiliate, product_placement. Array with minItems: 1.
**Scenario:**
```gherkin
Given channels=["display", "ctv"]
Then products matching either channel returned

Given channels=[]
Then rejected (minItems: 1)
```
**Priority:** P2
**Affected by 3.6:** Yes -- channels enum expanded in v3

---

### delivery_type: Delivery Type Enum
**Obligation ID** CONSTR-DELIVERY-TYPE-01
**Layer** schema
**Requirement:** Enum: guaranteed, non_guaranteed. Optional filter.
**Scenario:**
```gherkin
Given delivery_type="guaranteed"
Then only guaranteed products returned
```
**Priority:** P2
**Affected by 3.6:** No

---

### pacing: Budget Pacing Strategy
**Obligation ID** CONSTR-PACING-01
**Layer** schema
**Requirement:** Enum: even, asap, front_loaded. Default: even.
**Scenario:**
```gherkin
Given pacing omitted
Then defaults to "even"

Given pacing="accelerated"
Then rejected (not in enum)
```
**Priority:** P2
**Affected by 3.6:** No

---

### delivery_mode: Artifact Webhook Delivery Mode
**Obligation ID** CONSTR-DELIVERY-MODE-01
**Layer** schema
**Requirement:** Enum: realtime, batched. Required in artifact_webhook. batched requires batch_frequency.
**Scenario:**
```gherkin
Given delivery_mode="batched" and no batch_frequency
Then rejected (batch_frequency required when batched)
```
**Priority:** P2
**Affected by 3.6:** Yes -- artifact_webhook is new in v3

---

### batch_frequency: Artifact Webhook Batch Frequency
**Obligation ID** CONSTR-BATCH-FREQUENCY-01
**Layer** schema
**Requirement:** Enum: hourly, daily. Required when delivery_mode=batched.
**Scenario:**
```gherkin
Given delivery_mode="batched" and batch_frequency="hourly"
Then valid

Given delivery_mode="realtime" and batch_frequency omitted
Then valid (not applicable)
```
**Priority:** P3
**Affected by 3.6:** Yes -- artifact_webhook is new in v3

---

### reporting_frequency: Reporting Webhook Frequency
**Obligation ID** CONSTR-REPORTING-FREQUENCY-01
**Layer** schema
**Requirement:** Enum: hourly, daily, monthly. Required in reporting_webhook. GAP: only daily implemented.
**Scenario:**
```gherkin
Given reporting_frequency="hourly"
Then schema-valid but silently skipped in implementation (GAP)
```
**Priority:** P2
**Affected by 3.6:** No

---

### task_status: Task Status Enum
**Obligation ID** CONSTR-TASK-STATUS-01
**Layer** schema
**Requirement:** 9 values: submitted, working, input-required, completed, canceled, failed, rejected, auth-required, unknown. Filter accepts single or array (minItems: 1).
**Scenario:**
```gherkin
Given task_status=["submitted", "working"]
Then only tasks in those states returned
```
**Priority:** P2
**Affected by 3.6:** Yes -- task lifecycle is fundamental to v3 async

---

### task_type: Task Type Enum
**Obligation ID** CONSTR-TASK-TYPE-01
**Layer** schema
**Requirement:** 14 values covering all AdCP domains. Filter accepts single or array (minItems: 1).
**Scenario:**
```gherkin
Given task_type="sync_accounts"
Then only sync_accounts tasks returned

Given task_type="delete_media_buy"
Then rejected (not in enum)
```
**Priority:** P2
**Affected by 3.6:** Yes -- new task types for v3 domains

---

### wcag_level: WCAG Accessibility Level
**Obligation ID** CONSTR-WCAG-LEVEL-01
**Layer** schema
**Requirement:** Enum: A, AA, AAA. Hierarchical. Optional filter on creative formats.
**Scenario:**
```gherkin
Given wcag_level="AA"
Then formats meeting at least AA returned
```
**Priority:** P3
**Affected by 3.6:** No

---

### adcp_domain: AdCP Domain Enum
**Obligation ID** CONSTR-ADCP-DOMAIN-01
**Layer** schema
**Requirement:** Enum: media_buy, governance, signals. Used in capabilities response.
**Scenario:**
```gherkin
Given supported domains reported
Then each domain is from the adcp_domain enum
```
**Priority:** P2
**Affected by 3.6:** Yes -- new domains in v3

---

### available_metric: Available Metric Enum
**Obligation ID** CONSTR-AVAILABLE-METRIC-01
**Layer** schema
**Requirement:** 10 values: impressions, clicks, conversions, spend, ctr, cpm, viewability, completion_rate, frequency, reach.
**Scenario:**
```gherkin
Given requested_metrics=["impressions", "clicks"]
Then webhook payload includes those metrics
```
**Priority:** P3
**Affected by 3.6:** No

---

### creative_agent_format_type: Creative Agent Format Type
**Obligation ID** CONSTR-CREATIVE-AGENT-FORMAT-TYPE-01
**Layer** schema
**Requirement:** FormatCategory enum for creative agent context (same as format_type_filter).
**Scenario:**
```gherkin
Given creative agent reports format type "display"
Then valid FormatCategory enum value
```
**Priority:** P3
**Affected by 3.6:** No

---

### creative_agent_asset_type: Creative Agent Asset Type
**Obligation ID** CONSTR-CREATIVE-AGENT-ASSET-TYPE-01
**Layer** schema
**Requirement:** AssetContentType enum for creative agent context.
**Scenario:**
```gherkin
Given creative agent reports asset type "image"
Then valid AssetContentType enum value
```
**Priority:** P3
**Affected by 3.6:** No

---

### tasks_sort_field: Tasks Sort Field
**Obligation ID** CONSTR-TASKS-SORT-FIELD-01
**Layer** schema
**Requirement:** Enum for sorting tasks list results.
**Scenario:**
```gherkin
Given sort_by a valid tasks sort field
Then results are sorted accordingly
```
**Priority:** P3
**Affected by 3.6:** Yes -- task listing is new in v3

---

### creative_status: Creative Status Enum
**Obligation ID** CONSTR-CREATIVE-STATUS-01
**Layer** schema
**Requirement:** Status values for creative lifecycle: pending_review, approved, rejected, error, etc.
**Scenario:**
```gherkin
Given creative status filter with valid status
Then only creatives in that status returned
```
**Priority:** P2
**Affected by 3.6:** No

---

### sort_direction: Sort Direction Enum
**Obligation ID** CONSTR-SORT-DIRECTION-01
**Layer** schema
**Requirement:** Enum: asc, desc. Used with sort fields.
**Scenario:**
```gherkin
Given sort_direction="asc"
Then results sorted ascending
```
**Priority:** P3
**Affected by 3.6:** No

---

### creative_sort_field: Creative Sort Field
**Obligation ID** CONSTR-CREATIVE-SORT-FIELD-01
**Layer** schema
**Requirement:** Enum for sorting creatives list results.
**Scenario:**
```gherkin
Given sort_by a valid creative sort field
Then results sorted accordingly
```
**Priority:** P3
**Affected by 3.6:** No

---

### preview_output_format: Preview Output Format
**Obligation ID** CONSTR-PREVIEW-OUTPUT-FORMAT-01
**Layer** schema
**Requirement:** Enum for creative preview output format.
**Scenario:**
```gherkin
Given preview output_format is valid enum value
Then preview is generated in that format
```
**Priority:** P3
**Affected by 3.6:** No

---

### list_creatives_fields: List Creatives Response Fields
**Obligation ID** CONSTR-LIST-CREATIVES-FIELDS-01
**Layer** schema
**Requirement:** Defines which fields are included in list_creatives response.
**Scenario:**
```gherkin
Given list_creatives request with field selection
Then response includes only requested fields
```
**Priority:** P3
**Affected by 3.6:** No

---

### approval_mode: Approval Mode Enum
**Obligation ID** CONSTR-APPROVAL-MODE-01
**Layer** schema
**Requirement:** Enum: auto-approve, require-human, ai-powered. Default: require-human.
**Scenario:**
```gherkin
Given approval_mode not set
Then defaults to "require-human"
```
**Priority:** P1
**Affected by 3.6:** No

---

### sampling_method: Sampling Method Enum
**Obligation ID** CONSTR-SAMPLING-METHOD-01
**Layer** schema
**Requirement:** Enum for content standards sampling.
**Scenario:**
```gherkin
Given sampling_method is valid enum value
Then content standard uses that sampling approach
```
**Priority:** P3
**Affected by 3.6:** Yes -- content standards is new in v3

---

### protocols: Supported Protocols Enum
**Obligation ID** CONSTR-PROTOCOLS-01
**Layer** schema
**Requirement:** Enum: media_buy, governance, signals. Lists supported protocol areas in capabilities.
**Scenario:**
```gherkin
Given capabilities response
Then supported_protocols contains valid protocol enum values
```
**Priority:** P2
**Affected by 3.6:** Yes -- new protocols in v3

---

### context_echo: Context Echo Constraint
**Obligation ID** CONSTR-CONTEXT-ECHO-01
**Layer** schema
**Requirement:** Request context echoed unchanged in response. Opaque object. Applies to success, empty, and error paths. GAP: capabilities endpoint does not echo context.
**Scenario:**
```gherkin
Given request with context={"trace":"abc"}
Then response has context={"trace":"abc"}

Given capabilities request with context
Then context is NOT echoed (known GAP)
```
**Priority:** P1
**Affected by 3.6:** Yes -- now applies to capabilities and accounts endpoints

---

### event_type: Event Type Enum
**Obligation ID** CONSTR-EVENT-TYPE-01
**Layer** schema
**Requirement:** Enum for marketing/conversion events (log_event task).
**Scenario:**
```gherkin
Given event_type is valid enum value
Then event is logged
```
**Priority:** P3
**Affected by 3.6:** Yes -- events domain is new in v3

---

### sync_atomic_response: Sync Accounts Atomic Response
**Obligation ID** CONSTR-SYNC-ATOMIC-RESPONSE-01
**Layer** behavioral
**Requirement:** Success variant (accounts[]) XOR error variant (errors[]). Per-account failure is within success variant.
**Scenario:**
```gherkin
Given operation-level auth failure
Then error variant with errors[], no accounts[]

Given 3 accounts processed, 1 failed
Then success variant with accounts[] (including action=failed)
```
**Priority:** P0
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### sync_upsert_semantics: Sync Accounts Upsert
**Obligation ID** CONSTR-SYNC-UPSERT-SEMANTICS-01
**Layer** behavioral
**Requirement:** Creates new or updates existing. Per-account action: created/updated/unchanged/failed. House echoed.
**Scenario:**
```gherkin
Given new account
Then action=created with seller-assigned account_id

Given identical account re-synced
Then action=unchanged
```
**Priority:** P1
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### dry_run_preview: Dry Run Mode
**Obligation ID** CONSTR-DRY-RUN-PREVIEW-01
**Layer** schema
**Requirement:** dry_run=true returns what would change without applying. Response includes dry_run=true.
**Scenario:**
```gherkin
Given dry_run=true
Then no state changes; response shows would-be actions
```
**Priority:** P2
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### delete_missing_policy: Delete Missing Deactivation
**Obligation ID** CONSTR-DELETE-MISSING-POLICY-01
**Layer** behavioral
**Requirement:** delete_missing=true deactivates absent accounts scoped to agent. Default false.
**Scenario:**
```gherkin
Given delete_missing=true and account absent from request
Then account deactivated

Given delete_missing not specified (default false)
Then absent accounts unchanged
```
**Priority:** P1
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### billing: Billing Model
**Obligation ID** CONSTR-BILLING-01
**Layer** schema
**Requirement:** Billing model enum for account sync.
**Scenario:**
```gherkin
Given billing model in sync request
Then seller assigns or overrides per policy
```
**Priority:** P2
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### billing_model_policy: Billing Model Override Policy
**Obligation ID** CONSTR-BILLING-MODEL-POLICY-01
**Layer** behavioral
**Requirement:** Seller may override unsupported billing model with warning. Omitted = seller default.
**Scenario:**
```gherkin
Given unsupported billing model requested
Then overridden with warning in per-account result
```
**Priority:** P2
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### brand_identity_resolution: Brand Identity Resolution
**Obligation ID** CONSTR-BRAND-IDENTITY-RESOLUTION-01
**Layer** behavioral
**Requirement:** House domain + optional brand_id. Resolved via /.well-known/brand.json.
**Scenario:**
```gherkin
Given house="acme.com" and brand_id="widgets"
Then resolved via acme.com/.well-known/brand.json
```
**Priority:** P2
**Affected by 3.6:** Yes -- brand identity is new in v3

---

### si_termination_reason: Structured Interaction Termination Reason
**Obligation ID** CONSTR-SI-TERMINATION-REASON-01
**Layer** schema
**Requirement:** Enum for why a structured interaction ended.
**Scenario:**
```gherkin
Given termination with valid reason
Then reason is from the enum
```
**Priority:** P3
**Affected by 3.6:** Yes -- new in v3

---

### si_transaction_action: Structured Interaction Transaction Action
**Obligation ID** CONSTR-SI-TRANSACTION-ACTION-01
**Layer** schema
**Requirement:** Enum for transaction actions in structured interactions.
**Scenario:**
```gherkin
Given transaction action is valid enum value
Then action processed
```
**Priority:** P3
**Affected by 3.6:** Yes -- new in v3

---

### channel_mapping: Channel Alias Mapping
**Obligation ID** CONSTR-CHANNEL-MAPPING-01
**Layer** behavioral
**Requirement:** "video" -> "olv", "audio" -> "streaming_audio". Case-insensitive matching. Unrecognized channels silently dropped.
**Scenario:**
```gherkin
Given adapter reports "video"
Then mapped to "olv"

Given adapter reports "metaverse" (unknown)
Then silently dropped
```
**Priority:** P2
**Affected by 3.6:** Yes -- capabilities is new in v3

---

### account_access_scoping: Account Access Scoping
**Obligation ID** CONSTR-ACCOUNT-ACCESS-SCOPING-01
**Layer** behavioral
**Requirement:** list_accounts returns only agent-accessible accounts. Status filter narrows within accessible set.
**Scenario:**
```gherkin
Given agent has access to 3 accounts, status_filter="active"
Then only active accounts among the 3 are returned
```
**Priority:** P1
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### account_approval_workflow: Account Approval Workflow
**Obligation ID** CONSTR-ACCOUNT-APPROVAL-WORKFLOW-01
**Layer** behavioral
**Requirement:** Accounts requiring review enter pending_approval with setup info.
**Scenario:**
```gherkin
Given account requires review
Then status=pending_approval with setup.message
```
**Priority:** P2
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### account_auth_policy: Account Authentication Policy
**Obligation ID** CONSTR-ACCOUNT-AUTH-POLICY-01
**Layer** behavioral
**Requirement:** sync_accounts requires valid auth. list_accounts allows anonymous (empty results).
**Scenario:**
```gherkin
Given no auth on sync_accounts
Then AUTH_TOKEN_INVALID error
```
**Priority:** P0
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### account_status: Account Status Enum
**Obligation ID** CONSTR-ACCOUNT-STATUS-01
**Layer** behavioral
**Requirement:** Account lifecycle status values including pending_approval, active, suspended, deactivated.
**Scenario:**
```gherkin
Given account status filter with valid status
Then only matching accounts returned
```
**Priority:** P2
**Affected by 3.6:** Yes -- accounts domain is new in v3

---

### capabilities_degradation: Capabilities Graceful Degradation
**Obligation ID** CONSTR-CAPABILITIES-DEGRADATION-01
**Layer** behavioral
**Requirement:** No tenant = minimal response. Adapter failure = default channels/targeting. DB failure = placeholder domain. Never propagate error.
**Scenario:**
```gherkin
Given adapter lookup fails
Then channels defaults to [display], targeting defaults to geo

Given DB query fails
Then placeholder domain used
```
**Priority:** P1
**Affected by 3.6:** Yes -- capabilities is new in v3

---

### capabilities_features: Capabilities Feature Flags
**Obligation ID** CONSTR-CAPABILITIES-FEATURES-01
**Layer** schema
**Requirement:** Boolean feature flags in capabilities response (signals, content_standards, accounts, etc.).
**Scenario:**
```gherkin
Given capabilities response assembled
Then feature flags reflect tenant configuration
```
**Priority:** P2
**Affected by 3.6:** Yes -- capabilities is new in v3

---

### capabilities_targeting: Capabilities Targeting Support
**Obligation ID** CONSTR-CAPABILITIES-TARGETING-01
**Layer** behavioral
**Requirement:** Targeting dimensions supported by the seller. Defaults: geo_countries=true, geo_regions=true.
**Scenario:**
```gherkin
Given adapter provides targeting capabilities
Then response reflects adapter-reported targeting dimensions

Given adapter fails
Then defaults to geo_countries=true, geo_regions=true
```
**Priority:** P2
**Affected by 3.6:** Yes -- capabilities is new in v3

---

### content_standards_calibration_exemplars: Calibration Exemplars
**Obligation ID** CONSTR-CONTENT-STANDARDS-CALIBRATION-EXEMPLARS-01
**Layer** schema
**Requirement:** Optional. Pass/fail arrays of URL references or artifact objects (oneOf polymorphism). URL resolved to artifact on ingest.
**Scenario:**
```gherkin
Given pass exemplars with URL references
Then accepted and resolved to artifacts

Given pass exemplars with artifact objects
Then accepted directly
```
**Priority:** P3
**Affected by 3.6:** Yes -- content standards is new in v3

---

### content_standards_list_filters: Content Standards List Filters
**Obligation ID** CONSTR-CONTENT-STANDARDS-LIST-FILTERS-01
**Layer** behavioral
**Requirement:** Optional filters by channels (OR), languages (OR), countries (OR). Cross-dimension AND. No filters = all.
**Scenario:**
```gherkin
Given channels=["display"] and languages=["en"]
Then standards matching display AND en returned
```
**Priority:** P3
**Affected by 3.6:** Yes -- content standards is new in v3

---

### content_standards_policy: Content Standards Policy Text
**Obligation ID** CONSTR-CONTENT-STANDARDS-POLICY-01
**Layer** behavioral
**Requirement:** Required string containing the policy content. Free-form text describing acceptable/unacceptable content.
**Scenario:**
```gherkin
Given create with policy text
Then stored as current version
```
**Priority:** P2
**Affected by 3.6:** Yes -- content standards is new in v3

---

### content_standards_scope: Content Standards Scope
**Obligation ID** CONSTR-CONTENT-STANDARDS-SCOPE-01
**Layer** schema
**Requirement:** languages_any required (minItems: 1). countries_all optional (AND). channels_any optional (OR).
**Scenario:**
```gherkin
Given scope with languages_any=[] (empty)
Then rejected (minItems: 1)

Given scope with countries_all=["US", "UK"]
Then standard applies in BOTH US AND UK
```
**Priority:** P2
**Affected by 3.6:** Yes -- content standards is new in v3

---

### content_standards_standards_id: Content Standards ID
**Obligation ID** CONSTR-CONTENT-STANDARDS-STANDARDS-ID-01
**Layer** behavioral
**Requirement:** Stable identifier across versions. Same ID through updates. System-assigned on create.
**Scenario:**
```gherkin
Given create returns standards_id="std_1"
When update is called
Then response still has standards_id="std_1"
```
**Priority:** P2
**Affected by 3.6:** Yes -- content standards is new in v3

---

### auth/principal_id: Discovery Authentication
**Obligation ID** CONSTR-AUTH-PRINCIPAL-ID-01
**Layer** behavioral
**Requirement:** Authentication optional for discovery (require_valid_token=false). Invalid tokens degraded to anonymous (MCP). A2A requires valid token if provided. No data scoping by identity.
**Scenario:**
```gherkin
Given invalid token via MCP on discovery endpoint
Then treated as anonymous, full data returned

Given invalid token via A2A on discovery endpoint
Then rejected with authentication error
```
**Priority:** P1
**Affected by 3.6:** Yes -- now covers capabilities + accounts endpoints

---

### discovery_auth: Discovery Auth Pattern
**Obligation ID** CONSTR-DISCOVERY-AUTH-01
**Layer** behavioral
**Requirement:** Discovery endpoints allow anonymous access. Invalid tokens treated as absent. Identical data regardless of auth state.
**Scenario:**
```gherkin
Given authenticated caller on list_authorized_properties
Then receives same data as anonymous caller
```
**Priority:** P1
**Affected by 3.6:** Yes -- pattern extends to new v3 endpoints

---

### property_type: Property Type Enum
**Obligation ID** CONSTR-PROPERTY-TYPE-01
**Layer** schema
**Requirement:** Enum for property types in property list definitions.
**Scenario:**
```gherkin
Given property_type is valid enum value
Then property list entry is valid
```
**Priority:** P3
**Affected by 3.6:** Yes -- property lists is new in v3

---

### property_list_auth_token: Property List Auth Token
**Obligation ID** CONSTR-PROPERTY-LIST-AUTH-TOKEN-01
**Layer** behavioral
**Requirement:** Returned once in create response. Not in get/list/update/delete. min 32 chars. No recovery.
**Scenario:**
```gherkin
Given create_property_list succeeds
Then auth_token in response

Given get_property_list called
Then auth_token NOT in response
```
**Priority:** P1
**Affected by 3.6:** Yes -- property lists is new in v3

---

### property_list_base_properties: Base Properties Source
**Obligation ID** CONSTR-PROPERTY-LIST-BASE-PROPERTIES-01
**Layer** schema
**Requirement:** Discriminated union: publisher_tags (domain+tags), publisher_ids (domain+ids), identifiers (ids). Non-empty arrays. Omitted = entire catalog.
**Scenario:**
```gherkin
Given selection_type="publisher_tags" with empty tags array
Then rejected (non-empty required)
```
**Priority:** P2
**Affected by 3.6:** Yes -- property lists is new in v3

---

### property_list_filters: Property List Filters
**Obligation ID** CONSTR-PROPERTY-LIST-FILTERS-01
**Layer** schema
**Requirement:** When present, both countries_all and channels_any required as non-empty arrays. Evaluated at resolution time.
**Scenario:**
```gherkin
Given filters with countries_all but no channels_any
Then rejected (both required)
```
**Priority:** P2
**Affected by 3.6:** Yes -- property lists is new in v3

---

### property_list_list_id: Property List ID
**Obligation ID** CONSTR-PROPERTY-LIST-LIST-ID-01
**Layer** behavioral
**Requirement:** System-assigned unique identifier. Used for get/update/delete. Must exist within tenant for operations.
**Scenario:**
```gherkin
Given list_id not found in tenant
Then LIST_NOT_FOUND returned
```
**Priority:** P1
**Affected by 3.6:** Yes -- property lists is new in v3

---

### property_list_name: Property List Name
**Obligation ID** CONSTR-PROPERTY-LIST-NAME-01
**Layer** behavioral
**Requirement:** Required string for property list. Used in name_contains search filter.
**Scenario:**
```gherkin
Given name_contains="sports" in list filter
Then only lists with "sports" in name returned
```
**Priority:** P3
**Affected by 3.6:** Yes -- property lists is new in v3

---

### property_list_pagination: Property List Pagination
**Obligation ID** CONSTR-PROPERTY-LIST-PAGINATION-01
**Layer** schema
**Requirement:** max_results 1-10000, default 1000. Cursor-based pagination for resolved identifiers.
**Scenario:**
```gherkin
Given max_results=0
Then rejected (minimum: 1)

Given max_results=10001
Then rejected (maximum: 10000)
```
**Priority:** P2
**Affected by 3.6:** Yes -- property lists is new in v3

---

### property_list_resolve: Property List Resolution
**Obligation ID** CONSTR-PROPERTY-LIST-RESOLVE-01
**Layer** behavioral
**Requirement:** resolve=true (default) evaluates filters. resolve=false returns metadata only.
**Scenario:**
```gherkin
Given resolve=false
Then identifiers not resolved, only metadata returned
```
**Priority:** P2
**Affected by 3.6:** Yes -- property lists is new in v3

---

### property_list_webhook_url: Property List Webhook URL
**Obligation ID** CONSTR-PROPERTY-LIST-WEBHOOK-URL-01
**Layer** behavioral
**Requirement:** Only in update (not create). Empty string removes webhook. URI format when set.
**Scenario:**
```gherkin
Given webhook_url in create request
Then rejected (not in create schema)

Given webhook_url="" in update
Then previously set webhook removed
```
**Priority:** P2
**Affected by 3.6:** Yes -- property lists is new in v3

---

### creative_scope: Cross-Principal Creative Isolation
**Obligation ID** CONSTR-CREATIVE-SCOPE-01
**Layer** behavioral
**Requirement:** Triple key: tenant_id + principal_id + creative_id. Cross-principal collision = silent create.
**Scenario:**
```gherkin
Given same creative_id under different principal
Then new creative created silently (no cross-visibility)
```
**Priority:** P0
**Affected by 3.6:** No

---

### format_id_validation: Creative Format Validation
**Obligation ID** CONSTR-FORMAT-ID-VALIDATION-01
**Layer** behavioral
**Requirement:** format_id required. Non-HTTP agent_url skips external validation. HTTP agent checked for reachability + format registration.
**Scenario:**
```gherkin
Given missing format_id
Then CREATIVE_FORMAT_REQUIRED error

Given unknown format on HTTP agent
Then CREATIVE_FORMAT_UNKNOWN error
```
**Priority:** P1
**Affected by 3.6:** Yes -- format_id is now typed object

---

### generative_build: Generative Creative Build
**Obligation ID** CONSTR-GENERATIVE-BUILD-01
**Layer** behavioral
**Requirement:** Generative when output_format_ids truthy. Prompt priority: assets > inputs > name. Update without prompt = skip. GEMINI_API_KEY required.
**Scenario:**
```gherkin
Given generative format without GEMINI_API_KEY
Then CREATIVE_GEMINI_KEY_MISSING error
```
**Priority:** P2
**Affected by 3.6:** No

---

### assignment_package: Assignment Package Validation
**Obligation ID** CONSTR-ASSIGNMENT-PACKAGE-01
**Layer** behavioral
**Requirement:** Package lookup joins MediaPackage to MediaBuy filtered by tenant. Strict/lenient per validation_mode. Idempotent upsert (weight=100).
**Scenario:**
```gherkin
Given package not found in tenant's media buys (strict mode)
Then ToolError raised

Given existing assignment for same creative-package
Then weight reset to 100 (idempotent)
```
**Priority:** P1
**Affected by 3.6:** No

---

### assignment_format: Assignment Format Compatibility
**Obligation ID** CONSTR-ASSIGNMENT-FORMAT-01
**Layer** behavioral
**Requirement:** URL normalization (strip trailing "/" and "/mcp"). Both agent_url AND format_id must match. Empty format_ids = all allowed.
**Scenario:**
```gherkin
Given agent_url "http://agent.com/" and product expects "http://agent.com"
Then URL normalization makes them match

Given product has empty format_ids
Then all creative formats allowed
```
**Priority:** P1
**Affected by 3.6:** No

---

### media_buy_status: Media Buy Status Transition
**Obligation ID** CONSTR-MEDIA-BUY-STATUS-01
**Layer** behavioral
**Requirement:** draft + approved_at transitions to pending_creatives on assignment. Draft without approved_at stays draft. Non-draft unchanged.
**Scenario:**
```gherkin
Given draft media buy with approved_at set
When creative assigned
Then status becomes pending_creatives
```
**Priority:** P1
**Affected by 3.6:** No

---

### minimum_spend: Minimum Spend Per Package
**Obligation ID** CONSTR-MINIMUM-SPEND-01
**Layer** schema
**Requirement:** Product min_spend_per_package (primary) or tenant min_package_budget (fallback). Neither = skipped.
**Scenario:**
```gherkin
Given product min_spend=500 and budget=499.99
Then rejected (BUDGET_BELOW_MINIMUM)
```
**Priority:** P1
**Affected by 3.6:** Yes -- all 9 pricing models have min_spend_per_package

---

### persistence_timing: Adapter Atomicity Gate
**Obligation ID** CONSTR-PERSISTENCE-TIMING-01
**Layer** behavioral
**Requirement:** Auto-approval: persist only after adapter success. Adapter failure = no records. Manual approval: persist in pending before adapter.
**Scenario:**
```gherkin
Given auto-approval and adapter fails
Then no database records created
```
**Priority:** P0
**Affected by 3.6:** No

---

### adapter_dispatch: Partial Update Semantics
**Obligation ID** CONSTR-ADAPTER-DISPATCH-01
**Layer** behavioral
**Requirement:** Present fields modified, omitted fields unchanged. At least one field required.
**Scenario:**
```gherkin
Given update with no updatable fields
Then rejected (EMPTY_UPDATE)
```
**Priority:** P1
**Affected by 3.6:** No
