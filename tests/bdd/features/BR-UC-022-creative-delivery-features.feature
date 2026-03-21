# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-022 Creative Delivery & Features
  As a Buyer
  I want to retrieve creative-level delivery metrics and evaluate creative manifests for governance features
  So that I can understand which creative executions performed best and assess creative compliance

  # Postconditions verified:
  #   POST-S1: Buyer knows the delivery performance of each creative (impressions, clicks, spend, video metrics, conversions)
  #   POST-S2: Buyer can see variant-level breakdowns with rendered manifests showing what was actually served
  #   POST-S3: Buyer knows the reporting period (start/end timestamps, timezone) covered by the metrics
  #   POST-S4: Buyer knows the currency used for monetary values in the response
  #   POST-S5: Buyer knows the total variant count per creative, even when truncated by max_variants
  #   POST-S6: Buyer knows the governance evaluation results for the submitted creative (feature values with confidence scores)
  #   POST-S7: Buyer knows when each feature evaluation was performed and when it expires
  #   POST-S8: Buyer can access the full assessment report via detail_url (when provided by vendor)
  #   POST-F1: System state is unchanged on failure (both operations are read-only)
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Buyer knows how to fix the issue and retry (via error suggestion and recovery classification)
  #
  # Rules: BR-RULE-169 (scoping filter), BR-RULE-013 (datetime), BR-RULE-161 (manifest validity),
  #        BR-RULE-132 (capability gate), BR-RULE-170 (variant truncation), BR-RULE-171 (expiry/confidence)
  # Extensions: A (SCOPING_FILTER_REQUIRED), B (MEDIA_BUY_NOT_FOUND), C (CREATIVE_NOT_FOUND),
  #   D (DATE_RANGE_INVALID), E (DATE_INVALID_FORMAT), F (CREATIVE_MANIFEST_REQUIRED),
  #   G (MANIFEST_VALIDATION_ERROR), H (ACCOUNT_NOT_FOUND), I (FEATURE_NOT_SUPPORTED),
  #   J (GOVERNANCE_AGENT_UNAVAILABLE), K (ADAPTER_UNAVAILABLE)

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And the Buyer Agent has an authenticated connection via MCP


  @T-UC-022-main-delivery @main-flow @get-delivery @happy-path @post-s1 @post-s2 @post-s3 @post-s4
  Scenario: Get creative delivery -- standard creative with full delivery data
    Given media buy "mb-001" exists with creative "cr-001" having 3 variants
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-001"]
    Then the response contains creative "cr-001" with delivery metrics
    And each creative includes totals with impressions, clicks, spend, and CTR
    And each creative includes a variants array with variant_id, impressions, clicks, and spend
    And the response includes reporting_period with start and end timestamps
    And the response includes currency as an ISO 4217 code
    # POST-S1: Buyer knows delivery performance of each creative
    # POST-S2: Buyer can see variant-level breakdowns
    # POST-S3: Buyer knows reporting period
    # POST-S4: Buyer knows currency

  @T-UC-022-main-delivery-variants @main-flow @get-delivery @happy-path @post-s2 @post-s5
  Scenario: Get creative delivery -- variant manifests and variant_count are present
    Given media buy "mb-002" exists with creative "cr-002" having 5 variants each with a rendered manifest
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-002"]
    Then each variant includes an optional manifest (rendered creative specification)
    And the creative includes variant_count reflecting the total number of variants
    And variant_count equals the length of the variants array when no truncation
    # POST-S2: Buyer sees variant-level breakdowns with rendered manifests
    # POST-S5: Buyer knows total variant count

  @T-UC-022-main-delivery-daterange @main-flow @get-delivery @happy-path @post-s3
  Scenario: Get creative delivery -- with date range filter
    Given media buy "mb-003" exists with creative delivery data spanning multiple dates
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-003"] and start_date "2026-01-01" and end_date "2026-01-31"
    Then the response includes reporting_period reflecting the requested date range
    And the reporting_period includes optional timezone
    # POST-S3: Buyer knows the reporting period

  @T-UC-022-main-delivery-pagination @main-flow @get-delivery @happy-path
  Scenario: Get creative delivery -- with pagination
    Given multiple media buys exist with many creatives
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-004"] and pagination max_results 10
    Then the response includes pagination with limit, offset, has_more, and optional total
    And the creatives array contains at most 10 entries

  @T-UC-022-main-delivery-buyer-ref @main-flow @get-delivery @happy-path @post-s1
  Scenario: Get creative delivery -- scoped by buyer reference
    Given media buy with buyer_ref "campaign-spring-2026" exists with creative delivery data
    When the Buyer Agent invokes get_creative_delivery with media_buy_buyer_refs ["campaign-spring-2026"]
    Then the response contains creative delivery data for the matching media buy
    # POST-S1: Buyer knows delivery performance

  @T-UC-022-main-delivery-creative-ids @main-flow @get-delivery @happy-path @post-s1
  Scenario: Get creative delivery -- scoped by creative IDs
    Given creative "cr-005" exists across multiple media buys
    When the Buyer Agent invokes get_creative_delivery with creative_ids ["cr-005"]
    Then the response contains delivery data for creative "cr-005" across all associated buys
    # POST-S1: Buyer knows delivery performance

  @T-UC-022-main-delivery-video @main-flow @get-delivery @happy-path @post-s1
  Scenario: Get creative delivery -- video creative with quartile metrics
    Given media buy "mb-006" exists with a video creative having quartile completion data
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-006"]
    Then the delivery metrics include video quartile fields (q1, q2, q3, completion)
    And the delivery metrics include conversion data when available
    # POST-S1: Buyer knows video delivery metrics

  @T-UC-022-main-delivery-gen @main-flow @get-delivery @happy-path @post-s2
  Scenario: Get creative delivery -- generative creative (Tier 3) with generation_context
    Given media buy "mb-007" exists with a generative creative having variants with generation_context
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-007"]
    Then each variant includes optional generation_context for Tier 3 generative creatives
    # POST-S2: Buyer sees variant-level details including generation context

  @T-UC-022-main-features @main-flow @get-features @happy-path @post-s6 @post-s7
  Scenario: Get creative features -- full governance evaluation with confidence and expiry
    Given the seller declares creative_features capability as true
    And a governance agent is available for creative evaluation
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the response contains a results array of creative-feature-result objects
    And each result includes feature_id and value (boolean, number, or string)
    And each result optionally includes confidence (0..1), measured_at, and expires_at
    # POST-S6: Buyer knows governance evaluation results
    # POST-S7: Buyer knows evaluation timing and expiry

  @T-UC-022-main-features-detail @main-flow @get-features @happy-path @post-s8
  Scenario: Get creative features -- response includes detail_url
    Given the seller declares creative_features capability as true
    And a governance agent provides a detailed assessment report URL
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the response includes an optional detail_url linking to the vendor's full assessment report
    # POST-S8: Buyer can access full assessment report

  @T-UC-022-main-features-filter @main-flow @get-features @happy-path @post-s6
  Scenario: Get creative features -- filtered by feature_ids
    Given the seller declares creative_features capability as true
    And a governance agent supports features "brand_safety" and "content_category"
    When the Buyer Agent invokes get_creative_features with feature_ids ["brand_safety"]
    Then the response contains results only for the requested feature "brand_safety"
    # POST-S6: Buyer knows evaluation results for requested features

  @T-UC-022-main-features-account @main-flow @get-features @happy-path @post-s6
  Scenario: Get creative features -- with account context
    Given the seller declares creative_features capability as true
    And account "acct-brand-001" exists
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest and account "acct-brand-001"
    Then the response contains governance evaluation results scoped to the account context
    # POST-S6: Buyer knows evaluation results

  @T-UC-022-ext-a @extension @ext-a @error @get-delivery @post-f1 @post-f2 @post-f3
  Scenario: Creative delivery fails -- no scoping filters provided
    Given no scoping filters are included in the delivery request
    When the Buyer Agent invokes get_creative_delivery without media_buy_ids, media_buy_buyer_refs, or creative_ids
    Then the operation should fail
    And the error code should be "SCOPING_FILTER_REQUIRED"
    And the error message should contain "at least one scoping filter"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "media_buy_ids"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows at least one scoping filter is required
    # POST-F3: Buyer knows to provide media_buy_ids, media_buy_buyer_refs, or creative_ids

  @T-UC-022-ext-a-empty @extension @ext-a @error @get-delivery @post-f1 @post-f2 @post-f3
  Scenario: Creative delivery fails -- scoping filter present but empty array
    Given the delivery request includes media_buy_ids as an empty array []
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids []
    Then the operation should fail
    And the error code should be "SCOPING_FILTER_EMPTY"
    And the error message should contain "at least one element"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "Add at least one identifier"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows the array must have at least one element
    # POST-F3: Buyer knows to add an identifier or remove the field

  @T-UC-022-ext-b @extension @ext-b @error @get-delivery @post-f1 @post-f2 @post-f3
  Scenario: Creative delivery fails -- media buy not found by ID
    Given media buy "mb-nonexistent" does not exist in the tenant database
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-nonexistent"]
    Then the operation should fail
    And the error code should be "MEDIA_BUY_NOT_FOUND"
    And the error message should contain "media buy"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "verify media buy"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows which media buy identifiers could not be resolved
    # POST-F3: Buyer knows to verify media buy IDs

  @T-UC-022-ext-b-ref @extension @ext-b @error @get-delivery @post-f1 @post-f2 @post-f3
  Scenario: Creative delivery fails -- media buy not found by buyer reference
    Given buyer reference "ref-nonexistent" does not resolve to any media buy
    When the Buyer Agent invokes get_creative_delivery with media_buy_buyer_refs ["ref-nonexistent"]
    Then the operation should fail
    And the error code should be "MEDIA_BUY_NOT_FOUND"
    And the error message should contain "media buy"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "verify"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows which buyer references could not be resolved
    # POST-F3: Buyer knows to verify buyer references

  @T-UC-022-ext-c @extension @ext-c @error @get-delivery @post-f1 @post-f2 @post-f3
  Scenario: Creative delivery fails -- creative not found
    Given creative "cr-nonexistent" does not exist in the tenant database
    When the Buyer Agent invokes get_creative_delivery with creative_ids ["cr-nonexistent"]
    Then the operation should fail
    And the error code should be "CREATIVE_NOT_FOUND"
    And the error message should contain "creative"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "verify creative"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows which creative identifiers could not be resolved
    # POST-F3: Buyer knows to verify creative IDs

  @T-UC-022-ext-d @extension @ext-d @error @get-delivery @post-f1 @post-f2 @post-f3
  Scenario: Creative delivery fails -- start_date after end_date
    Given media buy "mb-010" exists
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-010"] and start_date "2026-03-15" and end_date "2026-03-01"
    Then the operation should fail
    And the error code should be "DATE_RANGE_INVALID"
    And the error message should contain "start_date"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "start_date is before end_date"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows the date range is invalid
    # POST-F3: Buyer knows to provide start_date before end_date

  @T-UC-022-ext-d-equal @extension @ext-d @error @get-delivery @post-f1 @post-f2 @post-f3
  Scenario: Creative delivery fails -- start_date equals end_date
    Given media buy "mb-011" exists
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-011"] and start_date "2026-03-10" and end_date "2026-03-10"
    Then the operation should fail
    And the error code should be "DATE_RANGE_INVALID"
    And the error message should contain "start_date"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "before end_date"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows equal dates are invalid
    # POST-F3: Buyer knows start_date must be strictly before end_date

  @T-UC-022-ext-e @extension @ext-e @error @get-delivery @post-f1 @post-f2 @post-f3
  Scenario: Creative delivery fails -- invalid date format for start_date
    Given media buy "mb-012" exists
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-012"] and start_date "03/15/2026"
    Then the operation should fail
    And the error code should be "DATE_INVALID_FORMAT"
    And the error message should contain "date"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "YYYY-MM-DD"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows which date field has invalid format
    # POST-F3: Buyer knows to use YYYY-MM-DD format

  @T-UC-022-ext-e-end @extension @ext-e @error @get-delivery @post-f1 @post-f2 @post-f3
  Scenario: Creative delivery fails -- invalid date format for end_date
    Given media buy "mb-013" exists
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-013"] and start_date "2026-03-01" and end_date "2026/03/31"
    Then the operation should fail
    And the error code should be "DATE_INVALID_FORMAT"
    And the error message should contain "date"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "YYYY-MM-DD"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows end_date has invalid format
    # POST-F3: Buyer knows to use YYYY-MM-DD format

  @T-UC-022-ext-f @extension @ext-f @error @get-features @post-f1 @post-f2 @post-f3
  Scenario: Creative features fails -- creative_manifest missing
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features without creative_manifest
    Then the operation should fail
    And the error code should be "CREATIVE_MANIFEST_REQUIRED"
    And the error message should contain "creative_manifest"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "format_id and assets"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows creative_manifest is required
    # POST-F3: Buyer knows to provide a manifest with format_id and assets

  @T-UC-022-ext-g-format @extension @ext-g @error @get-features @post-f1 @post-f2 @post-f3
  Scenario: Creative features fails -- manifest missing format_id
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with a manifest missing format_id
    Then the operation should fail
    And the error code should be "MANIFEST_VALIDATION_ERROR"
    And the error message should contain "format_id"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "format_id with agent_url and id"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows format_id is missing
    # POST-F3: Buyer knows the required manifest structure

  @T-UC-022-ext-g-assets @extension @ext-g @error @get-features @post-f1 @post-f2 @post-f3
  Scenario: Creative features fails -- manifest missing assets
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with a manifest that has format_id but no assets
    Then the operation should fail
    And the error code should be "MANIFEST_VALIDATION_ERROR"
    And the error message should contain "assets"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "assets map"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows assets are missing
    # POST-F3: Buyer knows to include an assets map

  @T-UC-022-ext-g-agent-url @extension @ext-g @error @get-features @post-f1 @post-f2 @post-f3
  Scenario: Creative features fails -- format_id missing agent_url
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with a manifest where format_id has id but no agent_url
    Then the operation should fail
    And the error code should be "MANIFEST_VALIDATION_ERROR"
    And the error message should contain "agent_url"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "agent_url"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows agent_url is missing from format_id
    # POST-F3: Buyer knows to include agent_url in format_id

  @T-UC-022-ext-g-id @extension @ext-g @error @get-features @post-f1 @post-f2 @post-f3
  Scenario: Creative features fails -- format_id missing id field
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with a manifest where format_id has agent_url but no id
    Then the operation should fail
    And the error code should be "MANIFEST_VALIDATION_ERROR"
    And the error message should contain "id"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "format_id"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows id is missing from format_id
    # POST-F3: Buyer knows to include id in format_id

  @T-UC-022-ext-h-delivery @extension @ext-h @error @get-delivery @post-f1 @post-f2 @post-f3
  Scenario: Creative delivery fails -- account not found
    Given account "acct-nonexistent" does not exist
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-014"] and account "acct-nonexistent"
    Then the operation should fail
    And the error code should be "ACCOUNT_NOT_FOUND"
    And the error message should contain "account"
    And the error recovery should be "terminal"
    And the error should include "suggestion" field
    And the suggestion should contain "verify account"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows the account reference could not be resolved
    # POST-F3: Buyer knows to verify the account identifier

  @T-UC-022-ext-h-features @extension @ext-h @error @get-features @post-f1 @post-f2 @post-f3
  Scenario: Creative features fails -- account not found
    Given the seller declares creative_features capability as true
    And account "acct-nonexistent" does not exist
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest and account "acct-nonexistent"
    Then the operation should fail
    And the error code should be "ACCOUNT_NOT_FOUND"
    And the error message should contain "account"
    And the error recovery should be "terminal"
    And the error should include "suggestion" field
    And the suggestion should contain "list_accounts"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows the account reference could not be resolved
    # POST-F3: Buyer knows to discover valid accounts

  @T-UC-022-ext-i @extension @ext-i @error @get-features @post-f1 @post-f2 @post-f3
  Scenario: Creative features fails -- unsupported feature_id
    Given the seller declares creative_features capability as true
    And the governance agent does not support feature "nonexistent_metric"
    When the Buyer Agent invokes get_creative_features with feature_ids ["nonexistent_metric"]
    Then the operation should fail
    And the error code should be "FEATURE_NOT_SUPPORTED"
    And the error message should contain "feature"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "get_adcp_capabilities"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows which feature_ids are not evaluable
    # POST-F3: Buyer knows to omit the filter or check capabilities

  @T-UC-022-ext-i-cap @extension @ext-i @error @get-features @post-f1 @post-f2 @post-f3
  Scenario: Creative features fails -- creative_features capability not declared
    Given the seller does not declare creative_features capability (absent or false)
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error message should contain "creative_features"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "capabilities"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows the seller does not support creative features
    # POST-F3: Buyer knows to check capabilities or try a different seller

  @T-UC-022-ext-j @extension @ext-j @error @get-features @post-f1 @post-f2 @post-f3
  Scenario: Creative features fails -- governance agent unavailable
    Given the seller declares creative_features capability as true
    And the governance agent is unavailable or times out
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the operation should fail
    And the error code should be "GOVERNANCE_AGENT_UNAVAILABLE"
    And the error message should contain "governance"
    And the error recovery should be "transient"
    And the error should include "retry_after" field
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows governance agent is unavailable
    # POST-F3: Buyer knows to retry after the suggested delay

  @T-UC-022-ext-k @extension @ext-k @error @get-delivery @post-f1 @post-f2 @post-f3
  Scenario: Creative delivery fails -- ad server adapter unavailable
    Given media buy "mb-015" exists
    And the ad server adapter is unavailable or times out
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-015"]
    Then the operation should fail
    And the error code should be "ADAPTER_UNAVAILABLE"
    And the error message should contain "adapter"
    And the error recovery should be "transient"
    And the error should include "retry_after" field
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows the ad server adapter is unavailable
    # POST-F3: Buyer knows to retry after the suggested delay

  @T-UC-022-inv-169-1 @invariant @BR-RULE-169
  Scenario: INV-1 holds -- single scoping filter with at least one element
    Given media buy "mb-020" exists
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-020"]
    Then the scoping filter validation passes
    And the system proceeds to resolve media buys

  @T-UC-022-inv-169-2 @invariant @BR-RULE-169 @error
  Scenario: INV-2 violated -- none of three scoping fields present
    When the Buyer Agent invokes get_creative_delivery with only optional params start_date "2026-01-01"
    Then the operation should fail
    And the error code should be "SCOPING_FILTER_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "media_buy_ids"

  @T-UC-022-inv-169-3 @invariant @BR-RULE-169 @error
  Scenario: INV-3 violated -- scoping field present with empty array
    When the Buyer Agent invokes get_creative_delivery with creative_ids []
    Then the operation should fail
    And the error code should be "SCOPING_FILTER_EMPTY"
    And the error should include "suggestion" field
    And the suggestion should contain "at least one identifier"

  @T-UC-022-inv-169-4 @invariant @BR-RULE-169
  Scenario: INV-4 holds -- multiple scoping filters applied as intersection
    Given media buy "mb-021" exists with creative "cr-021"
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-021"] and creative_ids ["cr-021"]
    Then the scoping filter validation passes
    And the response returns only creative delivery data matching both filters

  @T-UC-022-inv-161-1 @invariant @BR-RULE-161
  Scenario: INV-1 holds -- manifest contains format_id and assets
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with a manifest containing format_id and assets
    Then the manifest structural validation passes

  @T-UC-022-inv-161-1-violated @invariant @BR-RULE-161 @error
  Scenario: INV-1 violated -- manifest missing format_id
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with a manifest containing only assets (no format_id)
    Then the operation should fail
    And the error code should be "MANIFEST_VALIDATION_ERROR"
    And the error should include "suggestion" field
    And the suggestion should contain "format_id"

  @T-UC-022-inv-161-2 @invariant @BR-RULE-161
  Scenario: INV-2 holds -- format_id contains agent_url and id
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with a manifest where format_id has agent_url "https://agent.example.com" and id "fmt-001"
    Then the format_id structural validation passes

  @T-UC-022-inv-161-2-violated @invariant @BR-RULE-161 @error
  Scenario: INV-2 violated -- format_id missing agent_url
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with a manifest where format_id has id but no agent_url
    Then the operation should fail
    And the error code should be "MANIFEST_VALIDATION_ERROR"
    And the error should include "suggestion" field
    And the suggestion should contain "agent_url"

  @T-UC-022-inv-161-3 @invariant @BR-RULE-161
  Scenario: INV-3 holds -- format_id with width and height both present
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with a manifest where format_id includes width 300 and height 250
    Then the format_id structural validation passes (co-dependent dimensions satisfied)

  @T-UC-022-inv-161-3-violated @invariant @BR-RULE-161 @error
  Scenario: INV-3 violated -- format_id has width but no height
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with a manifest where format_id has width 300 but no height
    Then the operation should fail
    And the error code should be "MANIFEST_VALIDATION_ERROR"
    And the error message should contain "height"
    And the error should include "suggestion" field
    And the suggestion should contain "width and height"

  @T-UC-022-inv-161-4 @invariant @BR-RULE-161
  Scenario: INV-4 holds -- asset key matches lowercase alphanumeric pattern
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with a manifest having asset key "hero_image"
    Then the asset key validation passes

  @T-UC-022-inv-161-4-violated @invariant @BR-RULE-161 @error
  Scenario: INV-4 violated -- asset key contains invalid characters
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with a manifest having asset key "Hero-Image!"
    Then the operation should fail
    And the error code should be "MANIFEST_VALIDATION_ERROR"
    And the error message should contain "asset key"
    And the error should include "suggestion" field
    And the suggestion should contain "lowercase alphanumeric"

  @T-UC-022-inv-161-5 @invariant @BR-RULE-161
  Scenario: INV-5 holds -- asset value conforms to recognized asset type schema
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with a manifest having a valid image asset value
    Then the asset value validation passes

  @T-UC-022-inv-170-1 @invariant @BR-RULE-170
  Scenario: INV-1 holds -- truncation applied when actual count exceeds max_variants
    Given media buy "mb-030" exists with creative "cr-030" having 10 variants
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-030"] and max_variants 3
    Then the variants array contains exactly 3 entries
    And variant_count equals 10 (the true total)

  @T-UC-022-inv-170-2 @invariant @BR-RULE-170
  Scenario: INV-2 holds -- no truncation when actual count <= max_variants
    Given media buy "mb-031" exists with creative "cr-031" having 3 variants
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-031"] and max_variants 5
    Then the variants array contains exactly 3 entries
    And variant_count equals 3

  @T-UC-022-inv-170-3 @invariant @BR-RULE-170
  Scenario: INV-3 holds -- all variants returned when max_variants omitted
    Given media buy "mb-032" exists with creative "cr-032" having 7 variants
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-032"] without max_variants
    Then the variants array contains all 7 entries
    And variant_count equals 7 (equals length of variants array)

  @T-UC-022-inv-170-4 @invariant @BR-RULE-170 @error
  Scenario: INV-4 violated -- max_variants less than 1 rejected
    Given media buy "mb-033" exists
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-033"] and max_variants 0
    Then the operation should fail
    And the error code should be "MAX_VARIANTS_INVALID"
    And the error message should contain "max_variants"
    And the error should include "suggestion" field
    And the suggestion should contain "at least 1"

  @T-UC-022-inv-171-1 @invariant @BR-RULE-171
  Scenario: INV-1 holds -- confidence within valid range 0..1
    Given the seller declares creative_features capability as true
    And the governance agent returns a result with confidence 0.85
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the result includes confidence 0.85

  @T-UC-022-inv-171-1-violated @invariant @BR-RULE-171 @error
  Scenario: INV-1 violated -- confidence outside valid range
    Given the seller declares creative_features capability as true
    And the governance agent returns a result with confidence 1.5
    When the system processes the feature result
    Then the confidence value is rejected as out of range
    And the error code should be "CONFIDENCE_OUT_OF_RANGE"
    And the error should include "suggestion" field
    And the suggestion should contain "0..1 range"

  @T-UC-022-inv-171-2 @invariant @BR-RULE-171
  Scenario: INV-2 holds -- expires_at in the future indicates current evaluation
    Given the seller declares creative_features capability as true
    And the governance agent returns a result with expires_at in the future
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the result includes expires_at indicating the evaluation is current
    And the buyer can rely on the result

  @T-UC-022-inv-171-3 @invariant @BR-RULE-171
  Scenario: INV-3 holds -- expires_at in the past indicates stale evaluation
    Given the seller declares creative_features capability as true
    And the governance agent returns a result with expires_at "2026-01-01T00:00:00Z" (in the past)
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the result is still returned with the stale expires_at
    And the buyer should treat the result as advisory and re-evaluate

  @T-UC-022-inv-171-4 @invariant @BR-RULE-171
  Scenario: INV-4 holds -- expires_at absent means no expiry signal
    Given the seller declares creative_features capability as true
    And the governance agent returns a result without expires_at
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the result does not include expires_at
    And the buyer determines refresh cadence independently

  @T-UC-022-inv-171-5 @invariant @BR-RULE-171
  Scenario: INV-5 holds -- measured_at before expires_at defines valid window
    Given the seller declares creative_features capability as true
    And the governance agent returns a result with measured_at "2026-03-01T10:00:00Z" and expires_at "2026-03-08T10:00:00Z"
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the result includes both measured_at and expires_at
    And expires_at is after measured_at defining the evaluation validity window

  @T-UC-022-inv-132-1 @invariant @BR-RULE-132
  Scenario: INV-1 holds -- creative_features capability declared true
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the capability gate passes and the request is processed

  @T-UC-022-inv-132-2 @invariant @BR-RULE-132 @error
  Scenario: INV-2 violated -- creative_features capability absent
    Given the seller does not declare creative_features capability
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "capabilities"

  @T-UC-022-partition-scoping @partition @scoping_filter
  Scenario Outline: Scoping filter partition validation - <partition>
    Given the delivery request context is set up
    When the Buyer Agent invokes get_creative_delivery with scoping filter <filter_config>
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition             | filter_config                                                                                            | outcome                                         |
      | single_media_buy_ids  | {"media_buy_ids": ["mb-001"]}                                                                            | success with delivery data                       |
      | single_buyer_refs     | {"media_buy_buyer_refs": ["buyer-ref-001"]}                                                              | success with delivery data                       |
      | single_creative_ids   | {"creative_ids": ["cr-001"]}                                                                             | success with delivery data                       |
      | dual_filters          | {"media_buy_ids": ["mb-001"], "creative_ids": ["cr-001"]}                                                | success with delivery data                       |
      | all_filters           | {"media_buy_ids": ["mb-001"], "media_buy_buyer_refs": ["ref-1"], "creative_ids": ["cr-001"]}             | success with delivery data                       |

    Examples: Invalid partitions
      | partition     | filter_config                  | outcome                                                            |
      | no_filter     | {"start_date": "2026-01-01"}   | error "SCOPING_FILTER_REQUIRED" with suggestion                    |
      | empty_array   | {"media_buy_ids": []}          | error "SCOPING_FILTER_EMPTY" with suggestion                       |

  @T-UC-022-boundary-scoping @boundary @scoping_filter
  Scenario Outline: Scoping filter boundary validation - <boundary_point>
    Given the delivery request context is set up
    When the Buyer Agent invokes get_creative_delivery with scoping filter <filter_config>
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                    | filter_config                                                                                            | outcome                                                            |
      | zero filters present                              | {"start_date": "2026-01-01"}                                                                             | error "SCOPING_FILTER_REQUIRED" with suggestion                    |
      | one filter with exactly one element               | {"media_buy_ids": ["mb-001"]}                                                                            | success with delivery data                                         |
      | one filter with empty array []                    | {"media_buy_ids": []}                                                                                    | error "SCOPING_FILTER_EMPTY" with suggestion                       |
      | all three filters each with one element           | {"media_buy_ids": ["mb-001"], "media_buy_buyer_refs": ["ref-1"], "creative_ids": ["cr-001"]}             | success with delivery data                                         |
      | two filters present, one absent                   | {"media_buy_ids": ["mb-001"], "creative_ids": ["cr-001"]}                                                | success with delivery data                                         |

  @T-UC-022-partition-truncation @partition @variant_truncation
  Scenario Outline: Variant truncation partition validation - <partition>
    Given a creative exists with <actual_variants> variants
    When the Buyer Agent invokes get_creative_delivery with max_variants <max_variants_param>
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition       | actual_variants | max_variants_param | outcome                                                                       |
      | no_truncation   | 5               | omitted            | success with 5 variants and variant_count 5                                   |
      | exact_match     | 5               | 5                  | success with 5 variants and variant_count 5                                   |
      | truncated       | 10              | 3                  | success with 3 variants and variant_count 10                                  |
      | boundary_min    | 50              | 1                  | success with 1 variant and variant_count 50                                   |
      | zero_variants   | 0               | omitted            | success with 0 variants and variant_count 0                                   |

    Examples: Invalid partitions
      | partition              | actual_variants | max_variants_param | outcome                                                    |
      | max_variants_zero      | 5               | 0                  | error "MAX_VARIANTS_INVALID" with suggestion               |
      | max_variants_negative  | 5               | -1                 | error "MAX_VARIANTS_INVALID" with suggestion               |

  @T-UC-022-boundary-truncation @boundary @variant_truncation
  Scenario Outline: Variant truncation boundary validation - <boundary_point>
    Given a creative exists with <actual_variants> variants
    When the Buyer Agent invokes get_creative_delivery with max_variants <max_variants_param>
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                              | actual_variants | max_variants_param | outcome                                                    |
      | max_variants = 0 (below minimum)                            | 5               | 0                  | error "MAX_VARIANTS_INVALID" with suggestion               |
      | max_variants = 1 (boundary minimum)                         | 50              | 1                  | success with 1 variant and variant_count 50                |
      | max_variants omitted (no truncation)                        | 5               | omitted            | success with 5 variants and variant_count 5                |
      | variant_count == max_variants exactly (no truncation needed) | 5               | 5                  | success with 5 variants and variant_count 5                |
      | variant_count == max_variants + 1 (minimal truncation)      | 6               | 5                  | success with 5 variants and variant_count 6                |
      | zero variants with max_variants = 1 (empty result)          | 0               | 1                  | success with 0 variants and variant_count 0                |

  @T-UC-022-partition-expiry @partition @evaluation_expiry
  Scenario Outline: Evaluation expiry partition validation - <partition>
    Given the seller declares creative_features capability as true
    And the governance agent returns a result with quality metadata <metadata>
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition            | metadata                                                                                                         | outcome                                                                                |
      | full_metadata        | {"confidence": 0.95, "measured_at": "2026-03-10T10:00:00Z", "expires_at": "2026-03-17T10:00:00Z"}               | success with confidence, measured_at, and expires_at                                   |
      | confidence_only      | {"confidence": 0.8}                                                                                              | success with confidence only                                                           |
      | temporal_only        | {"measured_at": "2026-03-10T10:00:00Z", "expires_at": "2026-03-17T10:00:00Z"}                                   | success with measured_at and expires_at, no confidence                                 |
      | minimal              | {}                                                                                                               | success with only feature_id and value                                                 |
      | low_confidence       | {"confidence": 0.05}                                                                                             | success with low confidence                                                            |
      | high_confidence      | {"confidence": 0.99}                                                                                             | success with high confidence                                                           |
      | expired_evaluation   | {"expires_at": "2026-01-01T00:00:00Z", "measured_at": "2025-12-01T00:00:00Z"}                                   | success with stale evaluation (buyer should refresh)                                   |

    Examples: Invalid partitions
      | partition                | metadata                  | outcome                                                   |
      | confidence_below_range   | {"confidence": -0.01}     | error "CONFIDENCE_OUT_OF_RANGE" with suggestion            |
      | confidence_above_range   | {"confidence": 1.01}      | error "CONFIDENCE_OUT_OF_RANGE" with suggestion            |

  @T-UC-022-boundary-expiry @boundary @evaluation_expiry
  Scenario Outline: Evaluation expiry boundary validation - <boundary_point>
    Given the seller declares creative_features capability as true
    And the governance agent returns a result with quality metadata <metadata>
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                          | metadata                                                                                                         | outcome                                                      |
      | confidence = 0 (boundary minimum: complete uncertainty) | {"confidence": 0}                                                                                                | success with confidence 0                                    |
      | confidence = 1 (boundary maximum: full certainty)       | {"confidence": 1}                                                                                                | success with confidence 1                                    |
      | confidence = -0.01 (below minimum)                      | {"confidence": -0.01}                                                                                            | error "CONFIDENCE_OUT_OF_RANGE" with suggestion              |
      | confidence = 1.01 (above maximum)                       | {"confidence": 1.01}                                                                                             | error "CONFIDENCE_OUT_OF_RANGE" with suggestion              |
      | expires_at == measured_at (zero-length validity window)  | {"confidence": 0.5, "measured_at": "2026-03-10T10:00:00Z", "expires_at": "2026-03-10T10:00:00Z"}                | success with zero-length validity window                     |
      | expires_at absent (no expiry signal)                    | {"confidence": 0.8}                                                                                              | success with no expiry signal                                |
      | all quality fields absent (minimal result)              | {}                                                                                                               | success with only feature_id and value                       |

  @T-UC-022-inv-013-daterange @invariant @BR-RULE-013 @error
  Scenario: BR-RULE-013 INV-3 applied to delivery -- end_date not after start_date
    Given media buy "mb-040" exists
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-040"] and start_date "2026-06-15" and end_date "2026-06-10"
    Then the operation should fail
    And the error code should be "DATE_RANGE_INVALID"
    And the error should include "suggestion" field
    And the suggestion should contain "start_date is before end_date"

  @T-UC-022-feature-bool @main-flow @get-features @post-s6
  Scenario: Creative features -- boolean feature value
    Given the seller declares creative_features capability as true
    And the governance agent returns feature "brand_safety" with boolean value true
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the result includes feature "brand_safety" with value true
    # POST-S6: Buyer knows boolean governance result

  @T-UC-022-feature-number @main-flow @get-features @post-s6
  Scenario: Creative features -- numeric feature value
    Given the seller declares creative_features capability as true
    And the governance agent returns feature "quality_score" with numeric value 0.87
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the result includes feature "quality_score" with value 0.87
    # POST-S6: Buyer knows numeric governance result

  @T-UC-022-feature-string @main-flow @get-features @post-s6
  Scenario: Creative features -- string feature value
    Given the seller declares creative_features capability as true
    And the governance agent returns feature "content_category" with string value "entertainment"
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the result includes feature "content_category" with value "entertainment"
    # POST-S6: Buyer knows string governance result

  @T-UC-022-tier-standard @main-flow @get-delivery @post-s2
  Scenario: Creative delivery -- standard creative (Tier 1: 1:1 creative-to-variant)
    Given media buy "mb-050" exists with a standard creative having exactly 1 variant
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-050"]
    Then the creative has variant_count 1 and variants array with 1 entry
    # POST-S2: Buyer sees single variant for standard creative

  @T-UC-022-tier-asset-group @main-flow @get-delivery @post-s2
  Scenario: Creative delivery -- asset group optimization (Tier 2: one variant per asset combination)
    Given media buy "mb-051" exists with an asset-group creative having 4 variants (one per combination)
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-051"]
    Then the creative has variant_count 4 and variants array with 4 entries
    # POST-S2: Buyer sees multiple asset group variants

  @T-UC-022-features-error-branch @extension @ext-j @error @get-features @post-f2 @post-f3
  Scenario: Creative features error response uses error branch of discriminated union
    Given the seller declares creative_features capability as true
    And the governance agent fails with an internal error
    When the Buyer Agent invokes get_creative_features with a valid creative_manifest
    Then the response conforms to the error branch of get-creative-features-response
    And the response contains an errors array with standard error objects
    And each error includes code, message, and recovery
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix

  @T-UC-022-feature-ids-empty @extension @ext-i @error @get-features @post-f1 @post-f2 @post-f3
  Scenario: Creative features fails -- feature_ids provided as empty array
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with feature_ids []
    Then the operation should fail
    And the error message should contain "feature_ids"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "at least one feature"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows feature_ids must have at least one element
    # POST-F3: Buyer knows to provide at least one feature or omit the parameter

  @T-UC-022-pagination-valid @main-flow @get-delivery
  Scenario: Creative delivery -- pagination with valid max_results
    Given multiple creatives exist across media buys
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-060"] and pagination max_results 50
    Then the response includes pagination and at most 50 creative entries

  @T-UC-022-pagination-min @boundary @get-delivery
  Scenario: Creative delivery -- pagination max_results at minimum (1)
    Given multiple creatives exist across media buys
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-061"] and pagination max_results 1
    Then the response includes pagination and at most 1 creative entry

  @T-UC-022-pagination-max @boundary @get-delivery
  Scenario: Creative delivery -- pagination max_results at maximum (100)
    Given multiple creatives exist across media buys
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-062"] and pagination max_results 100
    Then the response includes pagination and at most 100 creative entries

  @T-UC-022-pagination-zero @boundary @get-delivery @error
  Scenario: Creative delivery fails -- pagination max_results below minimum
    Given media buy "mb-063" exists
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-063"] and pagination max_results 0
    Then the operation should fail
    And the error message should contain "max_results"
    And the error should include "suggestion" field
    And the suggestion should contain "1"

  @T-UC-022-pagination-over @boundary @get-delivery @error
  Scenario: Creative delivery fails -- pagination max_results above maximum
    Given media buy "mb-064" exists
    When the Buyer Agent invokes get_creative_delivery with media_buy_ids ["mb-064"] and pagination max_results 101
    Then the operation should fail
    And the error message should contain "max_results"
    And the error should include "suggestion" field
    And the suggestion should contain "100"

  @T-UC-022-partition-manifest @partition @manifest_validity
  Scenario Outline: Manifest validity partition validation - <partition>
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with manifest configuration <config>
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition                   | config                                                                                                                                                           | outcome                                           |
      | complete_manifest           | {"format_id": {"agent_url": "https://agent.example.com", "id": "fmt1"}, "assets": {"banner": {}}}                                                               | success                                           |
      | manifest_with_dimensions    | {"format_id": {"agent_url": "https://agent.example.com", "id": "fmt1", "width": 300, "height": 250}, "assets": {"banner": {}}}                                  | success                                           |
      | manifest_with_provenance    | {"format_id": {"agent_url": "https://agent.example.com", "id": "fmt1"}, "assets": {"banner": {}}, "provenance": {"source": "user"}}                             | success                                           |

    Examples: Invalid partitions
      | partition              | config                                                                                                            | outcome                                                        |
      | missing_format_id      | {"assets": {"banner": {}}}                                                                                        | error "MANIFEST_VALIDATION_ERROR" with suggestion              |
      | missing_assets         | {"format_id": {"agent_url": "https://agent.example.com", "id": "fmt1"}}                                          | error "MANIFEST_VALIDATION_ERROR" with suggestion              |
      | invalid_asset_key      | {"format_id": {"agent_url": "https://agent.example.com", "id": "fmt1"}, "assets": {"Banner Image": {}}}          | error "MANIFEST_VALIDATION_ERROR" with suggestion              |
      | missing_agent_url      | {"format_id": {"id": "fmt1"}, "assets": {"banner": {}}}                                                          | error "MANIFEST_VALIDATION_ERROR" with suggestion              |
      | width_without_height   | {"format_id": {"agent_url": "https://agent.example.com", "id": "fmt1", "width": 300}, "assets": {"banner": {}}}  | error "MANIFEST_VALIDATION_ERROR" with suggestion              |

  @T-UC-022-boundary-manifest @boundary @manifest_validity
  Scenario Outline: Manifest validity boundary validation - <boundary_point>
    Given the seller declares creative_features capability as true
    When the Buyer Agent invokes get_creative_features with manifest configuration <config>
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                      | config                                                                                                                                  | outcome                                                         |
      | manifest with format_id + assets (minimal valid)    | {"format_id": {"agent_url": "https://agent.example.com", "id": "fmt1"}, "assets": {"img": {}}}                                        | success                                                         |
      | manifest missing format_id                          | {"assets": {"banner": {}}}                                                                                                             | error "MANIFEST_VALIDATION_ERROR" with suggestion               |
      | manifest missing assets                             | {"format_id": {"agent_url": "https://agent.example.com", "id": "fmt1"}}                                                               | error "MANIFEST_VALIDATION_ERROR" with suggestion               |
      | manifest with empty assets object {}                | {"format_id": {"agent_url": "https://agent.example.com", "id": "fmt1"}, "assets": {}}                                                 | success (empty assets accepted structurally)                    |
      | asset key 'a' (minimal valid pattern)               | {"format_id": {"agent_url": "https://agent.example.com", "id": "fmt1"}, "assets": {"a": {}}}                                          | success                                                         |
      | asset key 'Banner-Image' (uppercase + hyphen)       | {"format_id": {"agent_url": "https://agent.example.com", "id": "fmt1"}, "assets": {"Banner-Image": {}}}                               | error "MANIFEST_VALIDATION_ERROR" with suggestion               |
      | format_id with width=1, height=1 (minimum dimensions) | {"format_id": {"agent_url": "https://agent.example.com", "id": "fmt1", "width": 1, "height": 1}, "assets": {"img": {}}}             | success                                                         |
      | format_id with width=0 (below minimum)              | {"format_id": {"agent_url": "https://agent.example.com", "id": "fmt1", "width": 0, "height": 0}, "assets": {"img": {}}}               | error "MANIFEST_VALIDATION_ERROR" with suggestion               |
      | format_id with width but no height                  | {"format_id": {"agent_url": "https://agent.example.com", "id": "fmt1", "width": 300}, "assets": {"img": {}}}                          | error "MANIFEST_VALIDATION_ERROR" with suggestion               |

