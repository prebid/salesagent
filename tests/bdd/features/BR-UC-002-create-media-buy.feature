# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

@analysis-2026-03-09 @schema-v3.1
Feature: BR-UC-002 Create Media Buy
  As a Buyer (via Buyer Agent)
  I want to create a media buy for advertising inventory
  So that my advertising campaign is live on the publisher's ad server

  # Postconditions verified:
  #   POST-S1: Buyer knows their media buy has been created and is activating
  #   POST-S2: Buyer can track the media buy via media_buy_id
  #   POST-S3: Buyer knows each package's allocation, product, and pricing
  #   POST-S4: Buyer's advertising campaign is live (or activating) on the ad server
  #   POST-S5: Buyer receives an unambiguous success confirmation
  #   POST-S6: Buyer knows the request completed successfully
  #   POST-S7: Buyer knows their media buy is awaiting seller approval
  #   POST-S8: Seller knows there is a media buy requiring their review
  #   POST-S9: Buyer can track the pending media buy via task_id
  #   POST-S10: Buyer knows how to check approval progress
  #   POST-S11: Buyer knows the proposal was successfully executed with their total budget
  #   POST-S12: Buyer knows the media buy was rejected and the reason for rejection
  #   POST-F1: System state is unchanged on failure (all-or-nothing semantics)
  #   POST-F2: Buyer knows what failed, the specific error code, and the recovery classification
  #   POST-F3: Buyer knows how to fix the issue and retry (correctable) or escalate (terminal)

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant exists with completed setup checklist
    And the Buyer is authenticated with a valid principal_id



  @T-UC-002-main @main-flow @post-s1 @post-s2 @post-s3 @post-s4 @post-s5 @post-s6
  Scenario: Auto-approved media buy with valid package-based request
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request with:
    | field          | value                        |
    | account        | account_id "acc-001"         |
    | brand          | domain "acme.com"            |
    | start_time     | 2026-04-01T00:00:00Z         |
    | end_time       | 2026-04-30T23:59:59Z         |
    And the request includes 2 packages with valid product_ids
    And each package has a positive budget meeting minimum spend
    And all packages use the same currency "USD"
    And each package has a valid pricing_option_id
    And the account "acc-001" exists and is active
    And the ad server adapter is available
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the response status should be "completed"
    And the response should include a "media_buy_id"
    And the response should include packages with allocations
    And each package should include product_id, budget, and pricing details
    # POST-S1: Buyer knows their media buy has been created and is activating
    # POST-S2: Buyer can track the media buy via media_buy_id
    # POST-S3: Buyer knows each package's allocation, product, and pricing
    # POST-S4: Buyer's advertising campaign is live (or activating) on the ad server
    # POST-S5: Buyer receives an unambiguous success confirmation
    # POST-S6: Buyer knows the request completed successfully

  @T-UC-002-alt-manual @alternative @alt-manual @post-s7 @post-s8 @post-s9 @post-s10
  Scenario: Manual approval required -- media buy enters pending state
    Given the tenant has "human_review_required" set to true
    And a valid create_media_buy request with account "acc-001"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response status should be "submitted"
    # Reconciled to spec 3.1.1 (PR #1567 round-2 item 2): create-media-buy-response.json
    # oneOf CreateMediaBuySubmitted requires status="submitted" + task_id and has
    # NO media_buy_id/confirmed_at/revision — they land on the task's completion
    # artifact. The pre-3.1.1 assertions (media_buy_id + workflow_step_id) claimed
    # confirmation of a not-yet-committed buy. Mirrors the BR-UC-003 reconciliation.
    And the response should contain a task_id
    And the response should NOT contain "media_buy_id" field
    And the response should NOT contain "confirmed_at" field
    And the response should NOT contain "revision" field
    And a Slack notification should be sent to the Seller
    # POST-S7: Buyer knows their media buy is awaiting seller approval
    # POST-S8: Seller knows there is a media buy requiring their review
    # POST-S9: Buyer can track the pending media buy via task_id
    # POST-S10: Buyer knows how to check approval progress

  @T-UC-002-alt-manual-reject @alternative @alt-manual @post-s12
  Scenario: Seller rejects a pending media buy
    Given a media buy exists in "pending_approval" state
    When the Seller rejects the media buy with reason "Budget too low for Q1 campaign"
    Then the media buy status should be "rejected"
    And the response should include "rejection_reason" containing "Budget too low"
    And the Buyer should be notified via webhook
    # POST-S12: Buyer knows the media buy was rejected and the reason for rejection
    # --- Alt: ASAP Start Timing ---

  @T-UC-002-alt-asap @alternative @alt-asap @post-s1 @post-s4
  Scenario: ASAP start timing resolves to current UTC
    Given a valid create_media_buy request with start_time "asap"
    And the account exists and is active
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the create_media_buy request
    Then the system should resolve start_time to current UTC
    And the campaign should be immediately activating
    And the response should include resolved start_time (not literal "asap")
    # POST-S1: Buyer knows their media buy has been created and is activating
    # POST-S4: Buyer's advertising campaign is live (or activating) on the ad server
    # --- Alt: With Inline Creatives ---

  @T-UC-002-alt-creatives @alternative @alt-creatives @post-s1
  Scenario: Media buy with inline creative uploads
    Given a valid create_media_buy request
    And the account exists and is active
    And the request includes packages with inline "creatives" array
    And each creative has a valid format_id, name, and assets with URL and dimensions
    And the creative agent has the referenced formats registered
    When the Buyer Agent sends the create_media_buy request
    Then the system should upload the creatives to the creative library
    And the system should assign the uploaded creatives to packages
    And the response should include the created media buy with creative assignments
    # POST-S1: Buyer knows their media buy has been created and is activating
    # --- Alt: Proposal-Based ---

  @T-UC-002-alt-proposal @alternative @alt-proposal @post-s1 @post-s2 @post-s3 @post-s11
  Scenario: Proposal-based media buy executes get_products proposal
    Given a valid create_media_buy request with:
    | field          | value                        |
    | proposal_id    | prop-2026-001                |
    | total_budget   | amount 5000, currency "USD"  |
    | account        | account_id "acc-001"         |
    | brand          | domain "acme.com"            |
    | start_time     | 2026-04-01T00:00:00Z         |
    | end_time       | 2026-04-30T23:59:59Z         |
    And the account "acc-001" exists and is active
    And proposal "prop-2026-001" exists and has not expired
    And the proposal has 3 product allocations
    When the Buyer Agent sends the create_media_buy request
    Then the system should derive packages from proposal allocations
    And the total_budget should be distributed per allocation percentages
    And the response should include the created media buy with derived packages
    # POST-S1: Buyer knows their media buy has been created and is activating
    # POST-S2: Buyer can track the media buy via media_buy_id
    # POST-S3: Buyer knows each package's allocation, product, and pricing
    # POST-S11: Buyer knows the proposal was successfully executed with their total budget

  @T-UC-002-ext-a @extension @ext-a @error @post-f1 @post-f2 @post-f3
  Scenario: Budget validation failure -- total budget is zero
    Given a valid create_media_buy request
    And the account exists and is active
    But all package budgets sum to 0
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "BUDGET_TOO_LOW"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "positive"
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-b: Product Not Found ---

  @T-UC-002-ext-b @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Product not found in tenant catalog
    Given a valid create_media_buy request
    And the account exists and is active
    But a package references product_id "prod-nonexistent" which does not exist
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "PRODUCT_NOT_FOUND"
    And the error recovery should be "correctable"
    And the error message should contain "prod-nonexistent"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-c: DateTime Validation Failure ---

  @T-UC-002-ext-c @extension @ext-c @error @post-f1 @post-f2 @post-f3
  Scenario: Start time is in the past
    Given a valid create_media_buy request
    And the account exists and is active
    But start_time is "2020-01-01T00:00:00Z" (in the past)
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error message should contain "past"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue

  @T-UC-002-ext-c-end @extension @ext-c @error
  Scenario: End time is before start time
    Given a valid create_media_buy request
    And the account exists and is active
    But end_time is before start_time
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error message should contain "end time"
    And the error should include "suggestion" field
    # --- ext-d: Currency Not Supported ---

  @T-UC-002-ext-d @extension @ext-d @error @post-f1 @post-f2 @post-f3
  Scenario: Currency not supported by tenant
    Given a valid create_media_buy request
    And the account exists and is active
    But the packages use currency "XYZ" which is not in the tenant's CurrencyLimit table
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error recovery should be "correctable"
    And the error message should contain "XYZ"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-e: Duplicate Products ---

  @T-UC-002-ext-e @extension @ext-e @error @post-f1 @post-f2 @post-f3
  Scenario: Duplicate product_id across packages
    Given a valid create_media_buy request with 2 packages
    And the account exists and is active
    But both packages reference the same product_id "prod-001"
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error message should contain "Duplicate"
    And the error message should contain "prod-001"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-f: Targeting Validation Failure ---

  @T-UC-002-ext-f @extension @ext-f @error @post-f1 @post-f2 @post-f3
  Scenario: Targeting overlay contains unknown field
    Given a valid create_media_buy request
    And the account exists and is active
    But a package targeting_overlay contains unknown field "weather_targeting"
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error message should contain "Unknown targeting"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue

  @T-UC-002-ext-f-managed @extension @ext-f @error
  Scenario: Targeting overlay sets a managed-only dimension
    Given a valid create_media_buy request
    And the account exists and is active
    But a package targeting_overlay sets a managed-only dimension
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error message should contain "managed"
    And the error should include "suggestion" field

  @T-UC-002-ext-f-geo @extension @ext-f @error
  Scenario: Targeting overlay has geo include/exclude overlap
    Given a valid create_media_buy request
    And the account exists and is active
    But a package targeting_overlay includes "US" in both geo_countries and geo_countries_exclude
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # --- ext-g: Creative Validation Failure ---

  @T-UC-002-ext-g @extension @ext-g @error @post-f1 @post-f2 @post-f3
  Scenario: Creative missing required URL field
    Given a valid create_media_buy request with inline creatives
    And the account exists and is active
    But a creative is missing the required URL in assets
    And the creative format is not generative
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error message should contain "URL"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-h: Format ID Validation Failure ---

  @T-UC-002-inv-015-6 @invariant @BR-RULE-015 @error
  Scenario: INV-6 holds -- creative asset lacking a valid asset_type discriminator is rejected
    Given a create_media_buy request with an inline creative whose assets map value lacks an "asset_type" field
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a validation error
    And the error code should be "INVALID_REQUEST"
    And the error should reference the unresolvable asset_type discriminator
    And the error should include "suggestion" field
    # BR-RULE-015 INV-6: assets value lacking asset_type or carrying an unregistered value (not one of the 14 AssetVariant types) -> INVALID_REQUEST
    # --- ext-h: Format ID Validation Failure ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-ext-h @extension @ext-h @error @post-f1 @post-f2 @post-f3
  Scenario: Format ID is a plain string instead of object
    Given a valid create_media_buy request
    And the account exists and is active
    But a package format_id is a plain string "banner_300x250" instead of a FormatId object
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error message should contain "FormatId"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue

  @T-UC-002-ext-h-agent @extension @ext-h @error
  Scenario: Format ID references unregistered creative agent
    Given a valid create_media_buy request
    And the account exists and is active
    But a package format_id references an unregistered agent_url
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error message should contain "not registered"
    And the error should include "suggestion" field
    # --- ext-i: Authentication Error ---

  @T-UC-002-ext-i @extension @ext-i @error @post-f1 @post-f2 @post-f3
  Scenario: Authentication failure -- no principal in context
    Given the Buyer has no authentication credentials
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error message should contain "Principal"
    And the error message should contain "authentication"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-j: Adapter Execution Failure ---

  @T-UC-002-ext-j @extension @ext-j @error @post-f1 @post-f2 @post-f3
  Scenario: Adapter execution failure -- ad server returns error
    Given a valid create_media_buy request that passes all validation
    And the account exists and is active
    But the ad server adapter returns an error
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And no media buy record should be persisted in the database
    And the response status should be "failed"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure (all-or-nothing)
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-k: Maximum Daily Spend Exceeded ---

  @T-UC-002-ext-k @extension @ext-k @error @post-f1 @post-f2 @post-f3
  Scenario: Daily budget exceeds maximum daily spend cap
    Given a valid create_media_buy request
    And the account exists and is active
    And the tenant has max_daily_package_spend configured at 1000
    But a package has budget 50000 over a 2-day flight (daily = 25000)
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "BUDGET_TOO_LOW"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-l: Proposal Not Found or Expired ---

  @T-UC-002-ext-l @extension @ext-l @error @post-f1 @post-f2 @post-f3
  Scenario: Proposal not found or expired
    Given a valid create_media_buy request with proposal_id "prop-expired"
    And the account exists and is active
    But proposal "prop-expired" does not exist or has expired
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "PROPOSAL_EXPIRED"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "get_products"
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-m: Proposal Budget Mismatch ---

  @T-UC-002-ext-m @extension @ext-m @error @post-f1 @post-f2 @post-f3
  Scenario: Proposal total budget below guidance minimum
    Given a valid create_media_buy request with proposal_id and total_budget amount 10
    And the account exists and is active
    But the proposal's total_budget_guidance.min is 1000
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "BUDGET_TOO_LOW"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-n: Pricing Option Validation Failure ---

  @T-UC-002-ext-n @extension @ext-n @error @post-f1 @post-f2 @post-f3
  Scenario: Pricing option not found on product
    Given a valid create_media_buy request
    And the account exists and is active
    But a package references pricing_option_id "po-nonexistent" not found on the product
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue

  @T-UC-002-ext-n-bid @extension @ext-n @error
  Scenario: Auction pricing without bid_price
    Given a valid create_media_buy request
    And the account exists and is active
    And a package selects an auction pricing option but provides no bid_price
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion" field

  @T-UC-002-ext-n-floor @extension @ext-n @error
  Scenario: Bid price below floor price
    Given a valid create_media_buy request
    And the account exists and is active
    And a package has bid_price 0.50 but floor_price is 1.00
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion" field
    # --- ext-o: Creative Not Found in Library ---

  @T-UC-002-ext-webhook-ssrf @extension @ext-webhook-ssrf @error @post-f1 @post-f2 @post-f3
  Scenario: Reporting webhook URL targeting a blocked host is rejected
    Given a valid create_media_buy request
    And the request includes a reporting_webhook with url "http://169.254.169.254/latest/meta-data/"
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # Repo-local SSRF policy (ungraded extension): reuses AdCP 3.1.1
    # VALIDATION_ERROR / recovery=correctable enum values + suggestion on
    # MCP/REST/A2A tool transports. Schema is silent on SSRF. A2A-native
    # push-config endpoints (message/send configuration,
    # setTaskPushNotificationConfig) map the same gate to InvalidParamsError
    # with the AdCP VALIDATION_ERROR envelope in data= — unit-pinned, not this scenario.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json (recovery via enumMetadata)

  @T-UC-002-ext-o @extension @ext-o @error @post-f1 @post-f2 @post-f3
  Scenario: Creative IDs not found in library
    Given a valid create_media_buy request
    And the account exists and is active
    But a package creative_assignment references creative_id "cr-nonexistent"
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "CREATIVE_REJECTED"
    And the error message should contain "cr-nonexistent"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-p: Creative Format Mismatch ---

  @T-UC-002-ext-p @extension @ext-p @error @post-f1 @post-f2 @post-f3
  Scenario: Creative format does not match product supported formats
    Given a valid create_media_buy request
    And the account exists and is active
    But a creative's format_id does not match any of the product's supported format_ids
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "CREATIVE_REJECTED"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-q: Creative Upload Failed ---

  @T-UC-002-ext-q @extension @ext-q @error @post-f2 @post-f3
  Scenario: Creative upload to ad server fails
    Given a valid create_media_buy request with inline creatives that passes all validation
    And the account exists and is active
    But the ad server rejects the creative upload
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "SERVICE_UNAVAILABLE"
    And the error should include "suggestion" field
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-r: Account Not Found ---

  @T-UC-002-ext-r @extension @ext-r @error @post-f1 @post-f2 @post-f3
  Scenario: Account not found -- explicit account_id
    Given a valid create_media_buy request with account_id "acc-nonexistent"
    But the account_id does not exist in the seller's account store
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "ACCOUNT_NOT_FOUND"
    And the error recovery should be "terminal"
    And the error should include "suggestion" field
    And the suggestion should contain "list_accounts"
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows to escalate (terminal)

  @T-UC-002-ext-r-nk @extension @ext-r @error
  Scenario: Account not found -- natural key
    Given a valid create_media_buy request with account natural key brand "unknown.com" operator "unknown.com"
    But no account matches the brand + operator combination
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "ACCOUNT_NOT_FOUND"
    And the error recovery should be "terminal"
    And the error should include "suggestion" field
    # --- ext-s: Account Setup Required ---

  @T-UC-002-ext-s @extension @ext-s @error @post-f1 @post-f2 @post-f3
  Scenario: Account requires setup before use
    Given a valid create_media_buy request with account_id "acc-new"
    And the account "acc-new" exists but requires setup (billing not configured)
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "ACCOUNT_SETUP_REQUIRED"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the error should include "details" with setup instructions
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-t: Account Ambiguous ---

  @T-UC-002-ext-t @extension @ext-t @error @post-f1 @post-f2 @post-f3
  Scenario: Account ambiguous -- natural key matches multiple accounts
    Given a valid create_media_buy request with account natural key brand "multi-brand.com" operator "agency.com"
    And the natural key matches 3 accounts
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "ACCOUNT_AMBIGUOUS"
    And the error recovery should be "correctable"
    And the error message should contain "3 accounts"
    And the error should include "suggestion" field
    And the suggestion should contain "account_id"
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-u: Optimization Goal Validation Failure ---

  @T-UC-002-ext-u @extension @ext-u @error @post-f1 @post-f2 @post-f3
  Scenario: Optimization goal with unsupported metric
    Given a valid create_media_buy request
    And the account exists and is active
    But a package has optimization_goal with kind "metric" and metric "attention_score" not in supported set
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error recovery should be "correctable"
    And the error message should contain "attention_score"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue

  @T-UC-002-ext-u-event @extension @ext-u @error
  Scenario: Optimization goal with unregistered event source
    Given a valid create_media_buy request
    And the account exists and is active
    But a package has optimization_goal with kind "event" and unregistered event_source_id
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error message should contain "not registered"
    And the error should include "suggestion" field
    And the suggestion should contain "sync_event_sources"
    # --- ext-v: Catalog Validation Failure ---

  @T-UC-002-ext-v @extension @ext-v @error @post-f1 @post-f2 @post-f3
  Scenario: Duplicate catalog types on a package
    Given a valid create_media_buy request
    And the account exists and is active
    But a package has two catalogs both with type "product"
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error message should contain "duplicate catalog type"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue

  @T-UC-002-ext-v-notfound @extension @ext-v @error
  Scenario: Catalog ID not found in synced catalogs
    Given a valid create_media_buy request
    And the account exists and is active
    But a package references catalog_id "cat-nonexistent" not found in synced catalogs
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error message should contain "not found"
    And the error should include "suggestion" field
    And the suggestion should contain "sync_catalogs"

  @T-UC-002-inv-006-1 @invariant @BR-RULE-006
  Scenario: INV-1 holds -- fixed_price set and floor_price null (valid fixed pricing)
    Given a valid create_media_buy request
    And the account exists and is active
    And a package pricing option has fixed_price set and floor_price null
    When the Buyer Agent sends the create_media_buy request
    Then the pricing validation should pass

  @T-UC-002-inv-006-2 @invariant @BR-RULE-006
  Scenario: INV-2 holds -- floor_price set and fixed_price null (valid auction pricing)
    Given a valid create_media_buy request
    And the account exists and is active
    And a package pricing option has floor_price set and fixed_price null
    And the package has a bid_price above the floor
    When the Buyer Agent sends the create_media_buy request
    Then the pricing validation should pass

  @T-UC-002-inv-006-3 @invariant @BR-RULE-006 @error
  Scenario: INV-3 violated -- both fixed_price and floor_price set
    Given a valid create_media_buy request
    And the account exists and is active
    But a package pricing option has both fixed_price and floor_price set
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error should include "suggestion" field

  @T-UC-002-inv-006-5 @invariant @BR-RULE-006 @v31
  Scenario: INV-5 holds -- bid-based auction with max_bid treats bid_price as a ceiling
    Given a valid create_media_buy request
    And the account exists and is active
    And a package uses a bid-based auction pricing model with max_bid set to true
    And the buyer supplies a bid_price above the floor_price
    When the Buyer Agent sends the create_media_buy request
    Then the pricing option should be accepted
    And the buyer bid_price should be interpreted as a ceiling rather than an exact price
    And the seller may clear at any price between the floor_price and the bid_price
    # BR-RULE-006 INV-5 (v3.1): max_bid=true on cpm/cpc/cpcv/cpv/vcpm makes bid_price a ceiling
    # --- BR-RULE-008: Budget Positivity ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-008-1 @invariant @BR-RULE-008
  Scenario: INV-1 holds -- total budget greater than zero
    Given a valid create_media_buy request with total budget 5000
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the budget validation should pass

  @T-UC-002-inv-008-2 @invariant @BR-RULE-008 @error
  Scenario: INV-2 violated -- total budget is zero or negative
    Given a valid create_media_buy request with total budget 0
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "BUDGET_TOO_LOW"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # --- BR-RULE-013: DateTime Validity ---

  @T-UC-002-inv-010-3 @invariant @BR-RULE-010 @v31
  Scenario: INV-3 holds -- proposal mode has no buyer packages array for the uniqueness check
    Given a valid create_media_buy request in proposal mode with a proposal_id and total_budget
    And the account exists and is active
    And the request supplies no buyer packages array
    When the Buyer Agent sends the create_media_buy request
    Then the product-uniqueness check should have no applicable buyer input
    And the request should not be rejected on the product-uniqueness rule
    # BR-RULE-010 INV-3 (v3.1): uniqueness binds the buyer-supplied manual packages array, absent in proposal mode
    # --- BR-RULE-012: Maximum Daily Spend Cap ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-012-5 @invariant @BR-RULE-012 @v31
  Scenario: INV-5 holds -- legacy mode validates total budget against the daily cap
    Given a valid create_media_buy request
    And the account exists and is active
    And the request uses legacy mode with no per-package budgets
    And the tenant has max_daily_package_spend configured
    When the Buyer Agent sends the create_media_buy request
    Then the media buy total budget should be validated against the daily cap as a single daily figure
    # BR-RULE-012 INV-5 (v3.1): legacy (no per-package budgets) validates the total against the cap
    # --- BR-RULE-013: DateTime Validity ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-013-4 @invariant @BR-RULE-013
  Scenario: INV-4 holds -- start_time is literal "asap" (case-sensitive)
    Given a valid create_media_buy request with start_time "asap"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the system should resolve start_time to current UTC
    And the date validation should pass

  @T-UC-002-inv-013-5 @invariant @BR-RULE-013 @error
  Scenario: INV-5 violated -- start_time is "ASAP" wrong case
    Given a valid create_media_buy request with start_time "ASAP"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # --- BR-RULE-017: Approval Workflow Determination ---

  @T-UC-002-inv-017-1 @invariant @BR-RULE-017
  Scenario: INV-1 holds -- both flags false results in auto-approval
    Given a valid create_media_buy request
    And the account exists and is active
    And tenant human_review_required is false
    And adapter manual_approval_required is false
    When the Buyer Agent sends the create_media_buy request
    Then the approval path should be auto-approved
    And the media buy should proceed to adapter execution

  @T-UC-002-inv-017-2 @invariant @BR-RULE-017
  Scenario: INV-2 holds -- tenant human_review_required triggers manual approval
    Given a valid create_media_buy request
    And the account exists and is active
    And tenant human_review_required is true
    When the Buyer Agent sends the create_media_buy request
    Then the approval path should be manual
    And the media buy should enter pending state

  @T-UC-002-inv-017-3 @invariant @BR-RULE-017
  Scenario: INV-3 holds -- adapter manual_approval_required triggers manual approval
    Given a valid create_media_buy request
    And the account exists and is active
    And adapter manual_approval_required is true
    When the Buyer Agent sends the create_media_buy request
    Then the approval path should be manual
    And the media buy should enter pending state
    # --- BR-RULE-018: Atomic Response Semantics ---

  @T-UC-002-inv-017-4 @invariant @BR-RULE-017 @v31
  Scenario: INV-4 holds -- a paused approval task surfaces input-required then resolves
    Given a create_media_buy request that requires manual approval
    And the response returned a submitted task envelope with a task_id
    When the approval task is paused awaiting the human decision
    Then the task status should be "input-required"
    And on completion the task should resolve to "completed" with a media_buy_id on the completion artifact or to "rejected"
    And "pending_approval" should never be used as a MediaBuyStatus value
    # BR-RULE-017 INV-4 (v3.1): approval modeled at the task layer; pending_approval is not a MediaBuyStatus
    # --- BR-RULE-018: Atomic Response Semantics ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-018-1 @invariant @BR-RULE-018
  Scenario: INV-1 holds -- successful creation has success fields only
    Given a valid create_media_buy request that passes all validation
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should have success fields
    And the response should NOT have an "errors" field

  @T-UC-002-inv-018-2 @invariant @BR-RULE-018 @error
  Scenario: INV-2 holds -- validation failure has errors array only
    Given a create_media_buy request that fails validation
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should have an "errors" array
    And the response should NOT have success fields (media_buy_id, packages)
    And each error should include "suggestion" field

  @T-UC-002-inv-018-4 @invariant @BR-RULE-018
  Scenario: INV-4 holds -- transient error includes retry_after hint
    Given a create_media_buy request
    And the account exists and is active
    And the system returns a transient error (RATE_LIMITED)
    When the Buyer Agent sends the create_media_buy request
    Then the error recovery should be "transient"
    And the error should include "retry_after" field

  @T-UC-002-inv-018-5 @invariant @BR-RULE-018
  Scenario: INV-5 holds -- correctable error includes suggestion and field
    Given a create_media_buy request that fails with a correctable error
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the error should include "field" field

  @T-UC-002-inv-018-6 @invariant @BR-RULE-018
  Scenario: INV-6 holds -- terminal error signals agent to escalate
    Given a create_media_buy request with account_id that does not exist
    When the Buyer Agent sends the create_media_buy request
    Then the error code should be "ACCOUNT_NOT_FOUND"
    And the error recovery should be "terminal"
    # --- BR-RULE-020: Adapter Atomicity ---

  @T-UC-002-inv-018-8 @invariant @BR-RULE-018 @v31
  Scenario: INV-8 holds -- two-layer envelope and payload error model
    Given a create_media_buy request that produces a response
    When the task fails fatally
    Then both the protocol envelope "adcp_error" field and the payload "errors" array should be populated
    And when only a non-fatal warning occurs only the payload "errors" array should carry it with severity "warning"
    And a non-fatal warning should not set the envelope "adcp_error" field
    And the envelope should not emit the legacy "task_status" or "response_status" fields
    # BR-RULE-018 INV-8 (v3.1): fatal => adcp_error + payload errors[]; warning => payload-only
    # --- BR-RULE-020: Adapter Atomicity ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-020-1 @invariant @BR-RULE-020
  Scenario: INV-1 holds -- adapter success persists all records
    Given a valid create_media_buy request that passes all validation
    And the account exists and is active
    And the ad server adapter returns success
    When the Buyer Agent sends the create_media_buy request
    Then the media buy record should be persisted in the database
    And the package records should be persisted
    And the creative assignment records should be persisted

  @T-UC-002-inv-020-2 @invariant @BR-RULE-020 @error
  Scenario: INV-2 holds -- adapter failure creates no records
    Given a valid create_media_buy request that passes all validation
    And the account exists and is active
    But the ad server adapter returns an error
    When the Buyer Agent sends the create_media_buy request
    Then no media buy record should be persisted
    And no package records should be persisted
    And the error should include "suggestion" field

  @T-UC-002-inv-020-3 @invariant @BR-RULE-020
  Scenario: INV-3 holds -- manual approval path persists in pending state
    Given a valid create_media_buy request
    And the account exists and is active
    And approval path is manual
    When the Buyer Agent sends the create_media_buy request
    Then the media buy record should be persisted with status "pending_approval"
    And the package records should be persisted
    # --- BR-RULE-026: Creative Assignment Validation ---

  @T-UC-002-inv-020-4 @invariant @BR-RULE-020 @v31
  Scenario: INV-4 holds -- pre-adapter validation failure never invokes the adapter
    Given a valid create_media_buy request
    And the account exists and is active
    But pre-adapter validation fails on creative completeness or adapter pricing/budget constraints
    When the Buyer Agent sends the create_media_buy request
    Then the ad server adapter should never be invoked
    And no database records should be created
    # BR-RULE-020 INV-4 (v3.1): validation precedes the adapter call; failure persists nothing
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-020-5 @invariant @BR-RULE-020 @v31
  Scenario: INV-5 holds -- dry-run mode never invokes the adapter and persists nothing
    Given a valid create_media_buy request
    And the account exists and is active
    And the request is sent in dry-run mode
    When the Buyer Agent sends the create_media_buy request
    Then the ad server adapter should never be invoked
    And a simulated success should be returned
    And no database records should be created
    # BR-RULE-020 INV-5 (v3.1): dry-run validates fully but never calls the adapter or persists
    # --- BR-RULE-026: Creative Assignment Validation ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-026-1 @invariant @BR-RULE-026
  Scenario: INV-1 holds -- all creatives valid and formats compatible
    Given a valid create_media_buy request with creative assignments
    And the account exists and is active
    And all referenced creatives exist in valid state with compatible formats
    When the Buyer Agent sends the create_media_buy request
    Then the creative assignment should proceed

  @T-UC-002-inv-026-2 @invariant @BR-RULE-026 @error
  Scenario: INV-2 violated -- creative in error state rejected
    Given a valid create_media_buy request with creative assignments
    And the account exists and is active
    But a referenced creative is in "error" state
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error should include "suggestion" field

  @T-UC-002-inv-026-4 @invariant @BR-RULE-026 @error
  Scenario: INV-4 violated -- creative format incompatible with product
    Given a valid create_media_buy request with creative assignments
    And the account exists and is active
    But a creative format is incompatible with the product's supported formats
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error should include "suggestion" field
    # --- BR-RULE-080: Account Resolution Validation ---

  @T-UC-002-inv-080-1 @invariant @BR-RULE-080 @error
  Scenario: INV-1 violated -- account field absent from request
    Given a create_media_buy request without account field
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # --- BR-RULE-087: Optimization Goal Validation ---

  @T-UC-002-inv-080-11 @invariant @BR-RULE-080 @v31 @open-question
  Scenario: INV-11 -- account in terminal lifecycle status cannot create media buys
    Given a create_media_buy request
    But the resolved account has a terminal lifecycle status of "rejected" or "closed"
    When the Buyer Agent sends the create_media_buy request
    Then the account should be treated as not operational and unable to create media buys
    And no dedicated error code exists in error-code.json for this state
    And the resolution outcome is unspecified by the protocol
    # BR-RULE-080 INV-11 (v3.1): account-status.json defines rejected/closed; no matching error code (open question G-acct-lifecycle)
    # --- BR-RULE-087: Optimization Goal Validation ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-087-5 @invariant @BR-RULE-087 @error
  Scenario: INV-5 violated -- duplicate priority values in optimization goals
    Given a valid create_media_buy request
    And the account exists and is active
    But a package has two optimization goals with the same priority value
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error message should contain "priority"
    And the error should include "suggestion" field

  @T-UC-002-inv-087-6 @invariant @BR-RULE-087 @error
  Scenario: INV-6 violated -- optimization_goals array empty
    Given a valid create_media_buy request
    And the account exists and is active
    But a package has optimization_goals as an empty array
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field

  @T-UC-002-inv-087-7 @invariant @BR-RULE-087 @error
  Scenario: INV-7 violated -- per_ad_spend target without value_field on event source
    Given a valid create_media_buy request
    And the account exists and is active
    But a package has an event kind optimization goal with target kind "per_ad_spend"
    And no event_sources entry has value_field set
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field

  @T-UC-002-partition-budget-amount @partition @budget-amount
  Scenario Outline: Budget amount partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And a package budget is set to <value>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- pricing_option_xor partitions (BR-RULE-006) ---

    Examples: Valid partitions
      | partition        | value    | outcome                          |
      | positive_amount  | 1000.00  | budget validation passes         |

    Examples: Invalid partitions
      | partition        | value | outcome                                      |
      | zero_amount      | 0     | error BUDGET_TOO_LOW with suggestion          |
      | negative_amount  | -50   | error BUDGET_TOO_LOW with suggestion          |

  @T-UC-002-partition-pricing-option-xor @partition @pricing-option-xor
  Scenario Outline: Pricing option XOR partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the pricing option configuration is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- currency_consistency partitions (BR-RULE-009) ---

    Examples: Valid partitions
      | partition         | outcome                         |
      | fixed_pricing     | pricing validation passes       |
      | auction_pricing   | pricing validation passes       |
      | cpa_model         | pricing validation passes       |

    Examples: Invalid partitions
      | partition         | outcome                         |
      | both_set          | error with suggestion           |
      | neither_set       | error with suggestion           |

  @T-UC-002-partition-currency-consistency @partition @currency-consistency
  Scenario Outline: Currency consistency partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the currency scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- product_uniqueness partitions (BR-RULE-010) ---

    Examples: Valid partitions
      | partition                | outcome                      |
      | single_package           | currency validation passes   |
      | all_same_currency        | currency validation passes   |
      | currency_in_tenant_table | currency validation passes   |
      | currency_in_ad_server    | currency validation passes   |

    Examples: Invalid partitions
      | partition                  | outcome                                   |
      | mixed_currencies           | error UNSUPPORTED_FEATURE with suggestion  |
      | currency_not_in_tenant     | error UNSUPPORTED_FEATURE with suggestion  |
      | currency_not_in_ad_server  | error UNSUPPORTED_FEATURE with suggestion  |

  @T-UC-002-partition-product-uniqueness @partition @product-uniqueness
  Scenario Outline: Product uniqueness partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the product scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- minimum_spend partitions (BR-RULE-011) ---

    Examples: Valid partitions
      | partition           | outcome                         |
      | single_package      | product validation passes       |
      | distinct_products   | product validation passes       |

    Examples: Invalid partitions
      | partition           | outcome                         |
      | duplicate_product   | error with suggestion           |

  @T-UC-002-partition-minimum-spend @partition @minimum-spend
  Scenario Outline: Minimum spend partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the minimum spend scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- daily_spend_cap partitions (BR-RULE-012) ---

    Examples: Valid partitions
      | partition                  | outcome                      |
      | budget_meets_product_min   | minimum spend passes         |
      | budget_meets_tenant_min    | minimum spend passes         |
      | no_minimum_configured      | minimum spend check skipped  |

    Examples: Invalid partitions
      | partition                  | outcome                         |
      | budget_below_product_min   | error with suggestion           |
      | budget_below_tenant_min    | error with suggestion           |

  @T-UC-002-partition-daily-spend-cap @partition @daily-spend-cap
  Scenario Outline: Daily spend cap partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the daily spend cap scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- start_time partitions (BR-RULE-013) ---

    Examples: Valid partitions
      | partition             | outcome                          |
      | below_cap             | daily spend validation passes    |
      | cap_not_configured    | daily spend check skipped        |
      | at_cap_exactly        | daily spend validation passes    |

    Examples: Invalid partitions
      | partition             | outcome                                      |
      | exceeds_cap           | error BUDGET_EXCEEDED with suggestion         |

  @T-UC-002-partition-start-time @partition @start-time
  Scenario Outline: Start time partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the start_time is <value>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- end_time partitions (BR-RULE-013) ---

    Examples: Valid partitions
      | partition              | value                      | outcome                      |
      | asap_literal           | asap                       | start time resolves to now   |
      | future_iso_datetime    | 2026-04-01T00:00:00Z       | start time accepted          |
      | future_naive_datetime  | 2026-04-01T00:00:00        | start time treated as UTC    |

    Examples: Invalid partitions
      | partition              | value                      | outcome                               |
      | past_datetime          | 2020-01-01T00:00:00Z       | error INVALID_REQUEST with suggestion  |
      | absent                 | null                        | error INVALID_REQUEST with suggestion  |
      | wrong_case_asap        | ASAP                        | error INVALID_REQUEST with suggestion  |

  @T-UC-002-partition-end-time @partition @end-time
  Scenario Outline: End time partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And start_time is "2026-04-01T00:00:00Z"
    And end_time is <value>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- targeting_overlay partitions (BR-RULE-014) ---

    Examples: Valid partitions
      | partition              | value                      | outcome                    |
      | after_start_time       | 2026-04-30T23:59:59Z       | end time accepted          |

    Examples: Invalid partitions
      | partition              | value                      | outcome                               |
      | equal_to_start         | 2026-04-01T00:00:00Z       | error INVALID_REQUEST with suggestion  |
      | before_start           | 2026-03-15T00:00:00Z       | error INVALID_REQUEST with suggestion  |
      | absent                 | null                        | error INVALID_REQUEST with suggestion  |

  @T-UC-002-partition-targeting-overlay @partition @targeting-overlay
  Scenario Outline: Targeting overlay partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the targeting overlay scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- creative_asset partitions (BR-RULE-015) ---

    Examples: Valid partitions
      | partition                          | outcome                         |
      | absent_overlay                     | targeting validation passes     |
      | valid_overlay                      | targeting validation passes     |
      | empty_overlay                      | targeting validation passes     |
      | single_geo_dimension               | targeting validation passes     |
      | multiple_dimensions                | targeting validation passes     |
      | frequency_cap_suppress_only        | targeting validation passes     |
      | frequency_cap_max_impressions_only | targeting validation passes     |
      | frequency_cap_combined             | targeting validation passes     |
      | keyword_targeting                  | targeting validation passes     |
      | proximity_travel_time              | targeting validation passes     |
      | proximity_radius                   | targeting validation passes     |
      | proximity_geometry                 | targeting validation passes     |

    Examples: Invalid partitions
      | partition                          | outcome                                      |
      | unknown_field                      | error INVALID_REQUEST with suggestion          |
      | managed_only_dimension             | error INVALID_REQUEST with suggestion          |
      | geo_overlap                        | error INVALID_REQUEST with suggestion          |
      | device_type_overlap                | error INVALID_REQUEST with suggestion          |
      | proximity_method_conflict          | error INVALID_REQUEST with suggestion          |
      | frequency_cap_missing_fields       | error INVALID_REQUEST with suggestion          |
      | keyword_duplicate                  | error INVALID_REQUEST with suggestion          |

  @T-UC-002-partition-creative-asset @partition @creative-asset
  Scenario Outline: Creative asset partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the creative scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- approval_workflow partitions (BR-RULE-017) ---

    Examples: Valid partitions
      | partition                           | outcome                       |
      | no_creatives                        | creative validation passes    |
      | assignments_only                    | creative validation passes    |
      | uploads_only                        | creative validation passes    |
      | both_paths                          | creative validation passes    |
      | assignment_with_weight_zero         | creative validation passes    |
      | assignment_with_placement_targeting | creative validation passes    |

    Examples: Invalid partitions
      | partition                           | outcome                                        |
      | creative_not_found                  | error CREATIVE_REJECTED with suggestion          |
      | format_mismatch                     | error CREATIVE_REJECTED with suggestion          |
      | missing_required_assets             | error CREATIVE_REJECTED with suggestion          |
      | exceeds_max_creatives               | error INVALID_REQUEST with suggestion            |

  @T-UC-002-partition-approval-workflow @partition @approval-workflow
  Scenario Outline: Approval workflow partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the approval scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- account_ref partitions (BR-RULE-080) ---

    Examples: All partitions (no invalid -- all are valid workflow paths)
      | partition                    | outcome                      |
      | auto_approve                 | auto-approved path taken     |
      | pending_human_review         | manual approval required     |
      | pending_adapter_approval     | manual approval required     |

  @T-UC-002-partition-account-ref @partition @account
  Scenario Outline: Account reference partition validation - <partition>
    Given a create_media_buy request with account configuration <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- optimization_goals partitions (BR-RULE-087) ---

    Examples: Valid partitions
      | partition                    | outcome                               |
      | explicit_account_id          | account resolution succeeds           |
      | natural_key_unambiguous      | account resolution succeeds           |
      | natural_key_sandbox          | account resolution succeeds           |
      # account is OPTIONAL on CreateMediaBuyRequest (account-management mid-spec):
      # an omitted account field is accepted and the buy is created.
      | missing_account              | account resolution succeeds           |

    Examples: Invalid partitions
      | partition                    | outcome                                         |
      | invalid_oneOf_both           | error VALIDATION_ERROR                             |
      | explicit_not_found           | error ACCOUNT_NOT_FOUND terminal                  |
      | natural_key_not_found        | error ACCOUNT_NOT_FOUND terminal                  |
      | natural_key_ambiguous        | error ACCOUNT_AMBIGUOUS correctable               |
      | account_setup_required       | error ACCOUNT_SETUP_REQUIRED correctable           |
      | account_payment_required     | error ACCOUNT_PAYMENT_REQUIRED terminal            |
      | account_suspended            | error ACCOUNT_SUSPENDED terminal                  |

  @T-UC-002-partition-optimization-goals @partition @optimization-goals
  Scenario Outline: Optimization goals partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the optimization goal scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- catalog_distinct_type partitions (BR-RULE-089) ---

    Examples: Valid partitions
      | partition                              | outcome                              |
      | single_metric_goal                     | optimization validation passes       |
      | single_event_goal                      | optimization validation passes       |
      | multiple_goals_unique_priorities       | optimization validation passes       |
      | metric_completed_views_with_duration   | optimization validation passes       |
      | metric_reach_with_unit                 | optimization validation passes       |
      | event_goal_with_attribution_window     | optimization validation passes       |
      | metric_goal_with_target                | optimization validation passes       |
      | event_goal_with_roas_target            | optimization validation passes       |
      | goals_at_max_count                     | optimization validation passes       |
      | reach_with_target_frequency            | optimization validation passes       |
      | event_multi_source_dedup               | optimization validation passes       |

    Examples: Invalid partitions
      | partition                              | outcome                                          |
      | unsupported_metric                     | error UNSUPPORTED_FEATURE with suggestion          |
      | unregistered_event_source              | error INVALID_REQUEST with suggestion              |
      | duplicate_priority                     | error INVALID_REQUEST with suggestion              |
      | unsupported_view_duration              | error UNSUPPORTED_FEATURE with suggestion          |
      | unsupported_reach_unit                 | error UNSUPPORTED_FEATURE with suggestion          |
      | unsupported_attribution_window         | error UNSUPPORTED_FEATURE with suggestion          |
      | empty_array                            | error INVALID_REQUEST with suggestion              |
      | exceeds_max_goals                      | error INVALID_REQUEST with suggestion              |
      | unsupported_target_kind                | error UNSUPPORTED_FEATURE with suggestion          |
      | value_target_without_value_field       | error INVALID_REQUEST with suggestion              |
      | metric_not_supported_by_product        | error UNSUPPORTED_FEATURE with suggestion          |
      | event_not_supported_by_product         | error UNSUPPORTED_FEATURE with suggestion          |

  @T-UC-002-partition-catalog-distinct-type @partition @catalog-distinct-type
  Scenario Outline: Catalog distinct type partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the catalog scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- format_id_structure partitions ---

    Examples: Valid partitions
      | partition              | outcome                            |
      | no_catalogs            | catalog validation passes          |
      | single_catalog         | catalog validation passes          |
      | distinct_types         | catalog validation passes          |
      | max_distinct_types     | catalog validation passes          |

    Examples: Invalid partitions
      | partition              | outcome                                      |
      | duplicate_catalog_type | error INVALID_REQUEST with suggestion          |
      | multiple_duplicates    | error INVALID_REQUEST with suggestion          |
      | catalog_not_found      | error INVALID_REQUEST with suggestion          |

  @T-UC-002-partition-format-id-structure @partition @format-id-structure
  Scenario Outline: Format ID structure partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the format ID scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- persistence_timing partitions (BR-RULE-020) ---

    Examples: Valid partitions
      | partition              | outcome                      |
      | valid_format_id        | format validation passes     |

    Examples: Invalid partitions
      | partition              | outcome                      |
      | plain_string           | error with suggestion        |
      | missing_agent_url      | error with suggestion        |
      | missing_id             | error with suggestion        |
      | unregistered_agent     | error with suggestion        |
      | unknown_format         | error with suggestion        |

  @T-UC-002-partition-persistence-timing @partition @persistence-timing
  Scenario Outline: Persistence timing partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the persistence timing scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- tasks_sort_field partitions ---

    Examples: Valid partitions
      | partition                       | outcome                                      |
      | auto_approve_adapter_success    | all records persisted after adapter success   |
      | manual_approval_pending         | records persisted in pending state            |

    Examples: Invalid partitions
      | partition                       | outcome                                      |
      | auto_approve_adapter_failure    | no records persisted after adapter failure    |

  @T-UC-002-partition-tasks-sort-field @partition @tasks-sort-field
  Scenario Outline: Tasks sort field partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the task list sort field is <partition>
    When the Buyer Agent queries the task list
    Then the result should be <outcome>
    # --- sort_direction partitions ---

    Examples: Valid partitions
      | partition       | outcome                               |
      | created_at      | tasks sorted by creation timestamp     |
      | updated_at      | tasks sorted by update timestamp       |
      | status          | tasks sorted by status value           |
      | task_type       | tasks sorted by operation type         |
      | domain          | tasks sorted by AdCP domain            |
      | omitted         | defaults to created_at sort            |

    Examples: Invalid partitions
      | partition       | outcome                               |
      | unknown_value   | error unknown sort field               |

  @T-UC-002-partition-sort-direction @partition @sort-direction
  Scenario Outline: Sort direction partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the sort direction is <partition>
    When the Buyer Agent queries the task list
    Then the result should be <outcome>
    # --- adcp_domain partitions ---

    Examples: Valid partitions
      | partition       | outcome                               |
      | asc             | results in ascending order             |
      | desc            | results in descending order            |
      | omitted         | defaults to desc order                 |

    Examples: Invalid partitions
      | partition       | outcome                               |
      | unknown_value   | error unknown sort direction           |

  @T-UC-002-partition-adcp-domain @partition @adcp-domain
  Scenario Outline: AdCP domain partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the domain filter is <partition>
    When the Buyer Agent queries the task list
    Then the result should be <outcome>
    # --- task_status partitions ---

    Examples: Valid partitions
      | partition       | outcome                               |
      | media_buy       | tasks filtered to media-buy domain     |
      | signals         | tasks filtered to signals domain       |
      | governance      | tasks filtered to governance domain    |
      | creative        | tasks filtered to creative domain      |
      | domain_array    | tasks filtered to multiple domains     |
      | omitted         | tasks from all domains returned        |

    Examples: Invalid partitions
      | partition       | outcome                               |
      | unknown_value   | error unknown domain value             |
      | empty_array     | error empty array violates minItems    |

  @T-UC-002-partition-task-status @partition @task-status
  Scenario Outline: Task status partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the task status filter is <partition>
    When the Buyer Agent queries the task list
    Then the result should be <outcome>
    # --- task_type partitions ---

    Examples: Valid partitions
      | partition        | outcome                               |
      | submitted        | tasks filtered to submitted status     |
      | working          | tasks filtered to working status       |
      | input_required   | tasks filtered to input-required       |
      | completed        | tasks filtered to completed status     |
      | canceled         | tasks filtered to canceled status      |
      | failed           | tasks filtered to failed status        |
      | rejected         | tasks filtered to rejected status      |
      | auth_required    | tasks filtered to auth-required        |
      | unknown_status   | tasks filtered to unknown status       |
      | status_array     | tasks filtered to multiple statuses    |
      | omitted          | tasks of all statuses returned         |

    Examples: Invalid partitions
      | partition        | outcome                               |
      | unknown_value    | error unknown status value             |
      | empty_array      | error empty array violates minItems    |

  @T-UC-002-partition-task-type @partition @task-type
  Scenario Outline: Task type partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the task type filter is <partition>
    When the Buyer Agent queries the task list
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition              | outcome                                 |
      | create_media_buy       | tasks filtered to create_media_buy       |
      | update_media_buy       | tasks filtered to update_media_buy       |
      | sync_creatives         | tasks filtered to sync_creatives         |
      | activate_signal        | tasks filtered to activate_signal        |
      | get_signals            | tasks filtered to get_signals            |
      | create_property_list   | tasks filtered to create_property_list   |
      | update_property_list   | tasks filtered to update_property_list   |
      | get_property_list      | tasks filtered to get_property_list      |
      | list_property_lists    | tasks filtered to list_property_lists    |
      | delete_property_list   | tasks filtered to delete_property_list   |
      | sync_accounts          | tasks filtered to sync_accounts          |
      | get_creative_delivery  | tasks filtered to get_creative_delivery  |
      | sync_event_sources     | tasks filtered to sync_event_sources     |
      | log_event              | tasks filtered to log_event              |
      | task_type_array        | tasks filtered to multiple types         |
      | omitted                | tasks of all types returned              |

    Examples: Invalid partitions
      | partition              | outcome                                 |
      | unknown_value          | error unknown task type                  |
      | empty_array            | error empty array violates minItems      |

  @T-UC-002-boundary-budget-amount @boundary @budget-amount
  Scenario Outline: Budget amount boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And a package budget is set to <value>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- pricing_option_xor boundaries (BR-RULE-006) ---

    Examples: Boundary values
      | boundary_point                       | value | outcome                                      |
      | amount = 0 (rejected by rule)        | 0     | error BUDGET_TOO_LOW with suggestion          |
      | amount = 0.01 (minimum positive)     | 0.01  | budget validation passes                     |
      | amount negative                      | -1    | error BUDGET_TOO_LOW with suggestion          |

  @T-UC-002-boundary-pricing-option-xor @boundary @pricing-option-xor
  Scenario Outline: Pricing option XOR boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the pricing option configuration is <config>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- currency_consistency boundaries (BR-RULE-009) ---

    Examples: Boundary values
      | boundary_point                                   | config                  | outcome                         |
      | fixed_price only (valid fixed)                   | fixed_price=10.00       | pricing validation passes       |
      | floor_price only (valid auction)                 | floor_price=1.00        | pricing validation passes       |
      | both present (mutually exclusive)                | fixed+floor             | error with suggestion           |
      | neither present, v3.1 schema (auction, no floor) | neither (v3.1 schema)   | pricing validation passes       |
      | neither present, salesagent code enforcement     | neither (code rejects)  | error with suggestion           |
      | max_bid=true on bid-based auction model          | max_bid=true            | pricing validation passes       |

  @T-UC-002-boundary-currency-consistency @boundary @currency-consistency
  Scenario Outline: Currency consistency boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the currency configuration is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- product_uniqueness boundaries (BR-RULE-010) ---

    Examples: Boundary values
      | boundary_point                           | config           | outcome                                  |
      | single package (trivially valid)         | 1 pkg USD        | currency validation passes               |
      | two packages, same currency              | 2 pkg USD+USD    | currency validation passes               |
      | two packages, same currency (via pricing options) | 2 pkg same pricing_option_id currency | currency validation passes      |
      | two packages, different currencies       | 2 pkg USD+EUR    | error UNSUPPORTED_FEATURE with suggestion |
      | two packages, different currencies (via pricing options) | 2 pkg divergent pricing_option_id currency | error UNSUPPORTED_FEATURE with suggestion |
      | currency not in tenant table             | 1 pkg XYZ        | error UNSUPPORTED_FEATURE with suggestion |
      | currency not in ad-server network currencies | 1 pkg JPY (GAM no JPY) | error UNSUPPORTED_FEATURE with suggestion |

  @T-UC-002-boundary-product-uniqueness @boundary @product-uniqueness
  Scenario Outline: Product uniqueness boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the product configuration is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- minimum_spend boundaries (BR-RULE-011) ---

    Examples: Boundary values
      | boundary_point                           | config            | outcome                      |
      | single package (trivially unique)        | 1 pkg prod-A      | product validation passes    |
      | two packages, different products         | 2 pkg prod-A,B    | product validation passes    |
      | two packages, same product_id            | 2 pkg prod-A,A    | error with suggestion        |
      | proposal_id supplied, packages omitted (no buyer packages array) | proposal_id, no packages | product validation passes |

  @T-UC-002-boundary-minimum-spend @boundary @minimum-spend
  Scenario Outline: Minimum spend boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the minimum spend configuration is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- daily_spend_cap boundaries (BR-RULE-012) ---

    Examples: Boundary values
      | boundary_point                                                 | config              | outcome                      |
      | budget = product min_spend (exact match)                       | budget=100 min=100  | minimum spend passes         |
      | budget = product min_spend - 0.01                              | budget=99.99 min=100 | error with suggestion        |
      | budget = tenant min_package_budget (exact, no product min)     | budget=50 tmin=50   | minimum spend passes         |
      | budget = tenant min_package_budget - 0.01 (no product min)    | budget=49.99 tmin=50 | error with suggestion        |
      | no min configured at any level                                 | budget=1 no-min     | minimum spend passes         |

  @T-UC-002-boundary-daily-spend-cap @boundary @daily-spend-cap
  Scenario Outline: Daily spend cap boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the daily spend scenario is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- start_time boundaries (BR-RULE-013) ---

    Examples: Boundary values
      | boundary_point                           | config              | outcome                                      |
      | daily budget = cap (at limit)            | daily=1000 cap=1000 | daily spend passes                           |
      | daily budget > cap (exceeds)             | daily=1001 cap=1000 | error BUDGET_EXCEEDED with suggestion         |
      | cap not configured (skipped)             | daily=9999 no-cap   | daily spend passes                           |
      | flight duration 0 days (floor to 1)      | 0-day-flight        | daily spend passes                           |

  @T-UC-002-boundary-start-time @boundary @start-time
  Scenario Outline: Start time boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And start_time is <value>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- end_time boundaries (BR-RULE-013) ---

    Examples: Boundary values
      | boundary_point             | value                    | outcome                               |
      | literal 'asap'             | asap                     | start time resolves to now            |
      | future ISO datetime        | 2026-04-01T00:00:00Z     | start time accepted                   |
      | past datetime              | 2020-01-01T00:00:00Z     | error INVALID_REQUEST with suggestion |
      | 'ASAP' wrong case          | ASAP                     | error INVALID_REQUEST with suggestion |
      | absent (null)              | null                     | error INVALID_REQUEST with suggestion |

  @T-UC-002-boundary-start-time-package-scope @boundary @start-time
  Scenario Outline: Package-scope start_time boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And a package in packages[] carries its own start_time of <value>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- end_time boundaries (BR-RULE-013) ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

    Examples: Boundary values
      | boundary_point          | value | outcome                               |
      | 'asap' at package scope | asap  | error INVALID_REQUEST with suggestion |

  @T-UC-002-boundary-end-time @boundary @end-time
  Scenario Outline: End time boundary validation - <boundary_point>
    Given a valid create_media_buy request with start_time "2026-04-01T00:00:00Z"
    And the account exists and is active
    And end_time is <value>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- targeting_overlay boundaries (BR-RULE-014) ---

    Examples: Boundary values
      | boundary_point                       | value                    | outcome                               |
      | end_time after start_time            | 2026-04-30T23:59:59Z     | end time accepted                     |
      | end_time = start_time (rejected)     | 2026-04-01T00:00:00Z     | error INVALID_REQUEST with suggestion |
      | end_time before start_time           | 2026-03-15T00:00:00Z     | error INVALID_REQUEST with suggestion |
      | absent (null)                        | null                     | error INVALID_REQUEST with suggestion |

  @T-UC-002-boundary-targeting-overlay @boundary @targeting-overlay
  Scenario Outline: Targeting overlay boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the targeting overlay scenario is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- creative_asset boundaries (BR-RULE-015) ---

    Examples: Boundary values
      | boundary_point                                    | config                | outcome                                      |
      | absent overlay                                    | no overlay            | targeting validation passes                  |
      | empty {} overlay                                  | empty                 | targeting validation passes                  |
      | valid known fields                                | geo_countries=US      | targeting validation passes                  |
      | unknown field name                                | weather=sunny         | error INVALID_REQUEST with suggestion          |
      | managed-only dimension                            | managed dimension     | error INVALID_REQUEST with suggestion          |
      | geo include/exclude overlap                       | US in both lists      | error INVALID_REQUEST with suggestion          |
      | device_type include/exclude overlap               | mobile in both        | error INVALID_REQUEST with suggestion          |
      | geo_proximity with travel_time only               | travel_time=30m       | targeting validation passes                  |
      | geo_proximity with radius only                    | radius=5km            | targeting validation passes                  |
      | geo_proximity with geometry only                  | geometry=polygon      | targeting validation passes                  |
      | geo_proximity with travel_time AND radius         | travel+radius         | error INVALID_REQUEST with suggestion          |
      | frequency_cap suppress only                       | suppress=24h          | targeting validation passes                  |
      | frequency_cap max_impressions with per+window     | max=3 per=1 win=24h   | targeting validation passes                  |
      | frequency_cap max_impressions without per         | max=3 no-per          | error INVALID_REQUEST with suggestion          |
      | keyword_targets with unique tuples                | kw=shoes exact        | targeting validation passes                  |
      | keyword_targets with duplicate (keyword, match_type) | kw=shoes exact x2 | error INVALID_REQUEST with suggestion          |

  @T-UC-002-boundary-creative-asset @boundary @creative-asset
  Scenario Outline: Creative asset boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the creative scenario is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- approval_workflow boundaries (BR-RULE-017) ---

    Examples: Boundary values
      | boundary_point                    | config               | outcome                                       |
      | no creatives (valid)              | no creatives         | creative validation passes                    |
      | valid library reference           | assignment cr-001    | creative validation passes                    |
      | valid inline upload               | upload with format   | creative validation passes                    |
      | creative_id not in library        | assignment cr-bad    | error CREATIVE_REJECTED with suggestion        |
      | format not in product             | wrong format         | error CREATIVE_REJECTED with suggestion        |
      | weight = 0 (paused)               | weight=0             | creative validation passes                    |
      | weight = 100 (max)                | weight=100           | creative validation passes                    |
      | 101 inline creatives              | 101 uploads          | error INVALID_REQUEST with suggestion          |

  @T-UC-002-boundary-approval-workflow @boundary @approval-workflow
  Scenario Outline: Approval workflow boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the approval configuration is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- account_ref boundaries (BR-RULE-080) ---

    Examples: Boundary values
      | boundary_point                        | config                | outcome                      |
      | both flags false (auto-approve)       | both=false            | auto-approved path taken     |
      | tenant flag true (pending)            | tenant_hr=true        | manual approval required     |
      | adapter flag true (pending)           | adapter_ma=true       | manual approval required     |

  @T-UC-002-boundary-account-ref @boundary @account
  Scenario Outline: Account reference boundary validation - <boundary_point>
    Given a create_media_buy request with account: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- optimization_goals boundaries (BR-RULE-087) ---

    Examples: Boundary values
      | boundary_point                                       | config                   | outcome                                          |
      | account_id present + account exists + active         | acc-001 active           | account resolution succeeds                      |
      | account_id present + not found                       | acc-bad not-found        | error ACCOUNT_NOT_FOUND terminal                 |
      | brand + operator present + single match + active     | brand+op single match    | account resolution succeeds                      |
      | brand + operator present + no match                  | brand+op no match        | error ACCOUNT_NOT_FOUND terminal                 |
      | brand + operator present + multiple matches          | brand+op multi match     | error ACCOUNT_AMBIGUOUS correctable              |
      | account resolved + setup incomplete                  | acc setup-needed         | error ACCOUNT_SETUP_REQUIRED correctable          |
      | account resolved + payment due                       | acc payment-due          | error ACCOUNT_PAYMENT_REQUIRED terminal           |
      | account resolved + suspended                         | acc suspended            | error ACCOUNT_SUSPENDED terminal                 |
      | account field absent                                 | no account               | account resolution succeeds                      |
      | both account_id and brand/operator present           | both fields              | error VALIDATION_ERROR                            |
      | brand + operator + sandbox:true present + sandbox account exists + active | brand+op+sandbox active | account resolution succeeds                      |

  @T-UC-002-boundary-optimization-goals @boundary @optimization-goals
  Scenario Outline: Optimization goals boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the optimization goals scenario is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- catalog_distinct_type boundaries (BR-RULE-089) ---

    Examples: Boundary values
      | boundary_point                                                          | config                   | outcome                                          |
      | optimization_goals present with 1 element (minItems boundary)           | 1 metric goal            | optimization validation passes                   |
      | optimization_goals present with 0 elements (below minItems)             | empty array              | error INVALID_REQUEST with suggestion             |
      | optimization_goals array at max_optimization_goals (cap boundary)       | at max count             | optimization validation passes                   |
      | optimization_goals array at max_optimization_goals + 1 (above cap)      | above max count          | error INVALID_REQUEST with suggestion             |
      | priority = 1 (minimum valid)                                            | priority=1               | optimization validation passes                   |
      | priority = 0 (below minimum)                                            | priority=0               | error INVALID_REQUEST with suggestion             |
      | view_duration_seconds = 0.001 (just above exclusiveMinimum 0)           | vds=0.001                | optimization validation passes                   |
      | view_duration_seconds = 0 (at exclusiveMinimum boundary)                | vds=0                    | error UNSUPPORTED_FEATURE with suggestion          |
      | target.value = 0.001 (just above exclusiveMinimum 0)                    | target=0.001             | optimization validation passes                   |
      | target.value = 0 (at exclusiveMinimum boundary)                         | target=0                 | error UNSUPPORTED_FEATURE with suggestion          |
      | kind = 'metric' with metric field present (valid branch)                | metric kind valid        | optimization validation passes                   |
      | kind = 'event' with event_sources field present (valid branch)          | event kind valid         | optimization validation passes                   |
      | product has metric_optimization + metric goal submitted                 | metric capable           | optimization validation passes                   |
      | product lacks metric_optimization + metric goal submitted               | no metric capability     | error UNSUPPORTED_FEATURE with suggestion          |
      | product has conversion_tracking + event goal submitted                  | event capable            | optimization validation passes                   |
      | product lacks conversion_tracking + event goal submitted                | no event capability      | error UNSUPPORTED_FEATURE with suggestion          |
      | target_frequency.min = 1, max = 3 (min <= max, valid)                   | freq min=1 max=3         | optimization validation passes                   |
      | target_frequency.min = 5, max = 3 (min > max, invalid)                  | freq min=5 max=3         | error INVALID_REQUEST with suggestion             |
      | per_ad_spend target + value_field present on event source               | roas with value_field    | optimization validation passes                   |
      | per_ad_spend target + no value_field on any event source                | roas no value_field      | error INVALID_REQUEST with suggestion             |

  @T-UC-002-boundary-catalog-distinct-type @boundary @catalog-distinct-type
  Scenario Outline: Catalog distinct type boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the catalog configuration is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- format_id_structure boundaries ---

    Examples: Boundary values
      | boundary_point                                                               | config               | outcome                                      |
      | 0 catalogs (field absent)                                                    | absent               | catalog validation passes                    |
      | 0 catalogs (empty array)                                                     | empty array          | catalog validation passes                    |
      | 1 catalog (uniqueness trivially satisfied)                                   | 1 product            | catalog validation passes                    |
      | 2 catalogs, different types (product + store)                                | product+store        | catalog validation passes                    |
      | 2 catalogs, same type (product + product) — first duplicate violation        | product+product      | error INVALID_REQUEST with suggestion          |
      | 3 catalogs, two share same type (product + product + store)                  | 2prod+store          | error INVALID_REQUEST with suggestion          |
      | 13 catalogs, all 13 distinct enum values                                     | all 13 types         | catalog validation passes                    |
      | Two packages each with type=product (distinct per-package, not cross-package) | cross-pkg product    | catalog validation passes                    |
      | catalog_id references a synced catalog                                       | valid catalog_id     | catalog validation passes                    |
      | catalog_id references a non-existent catalog                                 | bad catalog_id       | error INVALID_REQUEST with suggestion          |

  @T-UC-002-boundary-format-id-structure @boundary @format-id-structure
  Scenario Outline: Format ID structure boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the format ID scenario is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- persistence_timing boundaries (BR-RULE-020) ---

    Examples: Boundary values
      | boundary_point                                | config              | outcome                      |
      | valid object (registered agent + known format) | valid FormatId      | format validation passes     |
      | plain string (wrong type)                      | "banner_300x250"    | error with suggestion        |
      | missing agent_url                              | no agent_url        | error with suggestion        |
      | unregistered agent                             | bad agent_url       | error with suggestion        |
      | unknown format id                              | unknown format      | error with suggestion        |

  @T-UC-002-boundary-persistence-timing @boundary @persistence-timing
  Scenario Outline: Persistence timing boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the persistence timing scenario is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # --- tasks_sort_field boundaries ---

    Examples: Boundary values
      | boundary_point                              | config                | outcome                                      |
      | adapter returns success (auto-approval)     | auto-approve success  | all records persisted after adapter success   |
      | adapter returns error (auto-approval)       | auto-approve failure  | no records persisted after adapter failure    |
      | manual approval detected (pending state)    | manual approval       | records persisted in pending state            |

  @T-UC-002-boundary-tasks-sort-field @boundary @tasks-sort-field
  Scenario Outline: Tasks sort field boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the task list sort field boundary is: <config>
    When the Buyer Agent queries the task list
    Then the result should be <outcome>
    # --- sort_direction boundaries ---

    Examples: Boundary values
      | boundary_point                            | config          | outcome                               |
      | created_at (first enum value, default)    | created_at      | tasks sorted by creation timestamp     |
      | domain (last enum value)                  | domain          | tasks sorted by AdCP domain            |
      | Not provided (uses default created_at)    | omitted         | defaults to created_at sort            |
      | priority (not in enum)                    | priority        | error unknown sort field               |

  @T-UC-002-boundary-sort-direction @boundary @sort-direction
  Scenario Outline: Sort direction boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the sort direction boundary is: <config>
    When the Buyer Agent queries the task list
    Then the result should be <outcome>
    # --- adcp_domain boundaries ---

    Examples: Boundary values
      | boundary_point                      | config      | outcome                               |
      | asc (first enum value)              | asc         | results in ascending order             |
      | desc (last enum value, default)     | desc        | results in descending order            |
      | Not provided (uses default desc)    | omitted     | defaults to desc order                 |
      | ascending (not in enum)             | ascending   | error unknown sort direction           |

  @T-UC-002-boundary-adcp-domain @boundary @adcp-domain
  Scenario Outline: AdCP domain boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the domain filter boundary is: <config>
    When the Buyer Agent queries the task list
    Then the result should be <outcome>
    # --- task_status boundaries ---

    Examples: Boundary values
      | boundary_point                                      | config                    | outcome                               |
      | media-buy (first enum value)                        | media-buy                 | tasks filtered to media-buy domain     |
      | creative (last enum value)                          | creative                  | tasks filtered to creative domain      |
      | ["media-buy", "signals"] (multi-domain array)       | media-buy+signals         | tasks filtered to multiple domains     |
      | Not provided (no domain filtering)                  | omitted                   | tasks from all domains returned        |
      | analytics (not in enum)                             | analytics                 | error unknown domain value             |
      | [] (empty array, violates minItems)                 | empty array               | error empty array violates minItems    |

  @T-UC-002-boundary-task-status @boundary @task-status
  Scenario Outline: Task status boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the task status filter boundary is: <config>
    When the Buyer Agent queries the task list
    Then the result should be <outcome>
    # --- task_type boundaries ---

    Examples: Boundary values
      | boundary_point                                                        | config                              | outcome                               |
      | submitted (first enum value)                                          | submitted                           | tasks filtered to submitted status     |
      | unknown (last enum value)                                             | unknown                             | tasks filtered to unknown status       |
      | ["submitted", "working", "input-required"] (multi-status array)       | submitted+working+input-required    | tasks filtered to multiple statuses    |
      | Not provided (no status filtering)                                    | omitted                             | tasks of all statuses returned         |
      | pending (not in enum)                                                 | pending                             | error unknown status value             |
      | [] (empty array, violates minItems)                                   | empty array                         | error empty array violates minItems    |

  @T-UC-002-boundary-task-type @boundary @task-type
  Scenario Outline: Task type boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the task type filter boundary is: <config>
    When the Buyer Agent queries the task list
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                                    | config                             | outcome                               |
      | create_media_buy (first enum value)                               | create_media_buy                   | tasks filtered to create_media_buy     |
      | log_event (last enum value)                                       | log_event                          | tasks filtered to log_event            |
      | ["create_media_buy", "update_media_buy"] (multi-type array)       | create_media_buy+update_media_buy  | tasks filtered to multiple types       |
      | Not provided (no task type filtering)                             | omitted                            | tasks of all types returned            |
      | delete_media_buy (not in enum)                                    | delete_media_buy                   | error unknown task type                |
      | [] (empty array, violates minItems)                               | empty array                        | error empty array violates minItems    |

  @T-UC-002-nfr-001 @nfr @nfr-001
  Scenario: Security hardening -- request validation and rate limiting
    Given a valid create_media_buy request
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the system should validate authentication before any business logic
    And the system should enforce rate limiting on the endpoint
    And the system should validate payload size limits

  @T-UC-002-nfr-003 @nfr @nfr-003
  Scenario: Audit logging -- all steps are logged
    Given a valid create_media_buy request
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the system should log the protocol audit entry
    And the approval decision should be logged
    And the adapter execution should be logged

  @T-UC-002-nfr-004 @nfr @nfr-004
  Scenario: Response latency -- within SLA
    Given a valid create_media_buy request
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should be returned within 15 seconds (p95)

  @T-UC-002-nfr-006 @nfr @nfr-006
  Scenario: Minimum order size enforcement
    Given a valid create_media_buy request
    And the account exists and is active
    And the tenant has minimum order size requirements
    When the Buyer Agent sends the create_media_buy request
    Then the system should validate budget against minimum order requirements

  @T-UC-002-sandbox-happy @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account creates simulated media buy with sandbox flag
    Given a valid create_media_buy request with packages
    And the request targets a sandbox account
    When the Buyer Agent sends the create_media_buy request
    Then the response status should be "completed"
    And the response should include sandbox equals true
    And no real ad platform orders should have been created
    And no real billing records should have been created
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-2: real ad platform calls suppressed
    # BR-RULE-209 INV-3: real billing suppressed
    # BR-RULE-209 INV-4: response includes sandbox: true

  @T-UC-002-sandbox-production @invariant @br-rule-209 @sandbox
  Scenario: Production account media buy response does not include sandbox flag
    Given a valid create_media_buy request with packages
    And the request targets a production account
    When the Buyer Agent sends the create_media_buy request
    Then the response status should be "completed"
    And the response should not include a sandbox field
    # BR-RULE-209 INV-5: production account -> sandbox absent

  @T-UC-002-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account with invalid budget returns real validation error
    Given a create_media_buy request with total_budget of 0
    And the request targets a sandbox account
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

  @T-UC-002-sandbox-natural-key @invariant @br-rule-209 @sandbox @account
  Scenario: v3.1 natural-key reference with sandbox true resolves to the sandbox account without prior provisioning
    Given a valid create_media_buy request with packages
    And the request uses a natural-key account reference with brand and operator and sandbox true
    When the Buyer Agent sends the create_media_buy request
    Then the reference should resolve to the sandbox account for that brand and operator
    And the response should include sandbox equals true
    And no real ad platform orders should have been created
    # BR-RULE-209 INV-8 + BR-RULE-080 INV-10: natural-key (brand+operator+sandbox:true) resolves to sandbox account without prior sync_accounts provisioning
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-idempotency-replay @v31 @idempotency-key @post-s1 @ext-w @happy-path
  Scenario: v3.1 idempotency_key replay returns existing media buy without re-execution
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request with idempotency_key "abc123-replay-xyz-9876"
    And the account "acc-001" exists and is active
    And a media buy was already created for the same seller with that idempotency_key
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the response status should be "completed"
    And the response should include the previously created "media_buy_id"
    And no new ad platform order should have been created
    # v3.1: idempotency_key uniquely identifies (seller, request) pair
    # POST-S5: Buyer receives an unambiguous success confirmation
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-idempotency-missing @v31 @idempotency-key @validation @post-f2 @ext-w
  Scenario: v3.1 idempotency_key missing fails request validation
    Given a create_media_buy request with the idempotency_key field omitted
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a validation error
    And the error should reference the missing "idempotency_key" field
    And the error should include "suggestion" field
    # v3.1: idempotency_key is required on create-media-buy-request
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-idempotency-pattern-invalid @v31 @idempotency-key @validation @post-f2 @ext-w
  Scenario Outline: v3.1 idempotency_key violates length/pattern constraints
    Given a create_media_buy request with idempotency_key "<value>"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a validation error
    And the error should reference idempotency_key constraint "<violation>"
    And the error should include "suggestion" field
    # v3.1: idempotency_key pattern ^[A-Za-z0-9_.:-]{16,255}$
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

    Examples:
      | value                                                | violation                              |
      | short                                                | minLength 16 violated                  |
      | key with spaces in it that is long enough           | pattern [A-Za-z0-9_.:-] violated       |
      | key/with/slashes/that/is/also/long/enough           | pattern [A-Za-z0-9_.:-] violated       |
      | AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA | maxLength 255 violated                 |

  @T-UC-002-v31-idempotency-in-flight @v31 @idempotency-key @error-details @post-f2 @post-f3 @ext-w
  Scenario: v3.1 idempotency_key matching an in-flight request rejects with IDEMPOTENCY_IN_FLIGHT
    Given a create_media_buy request with idempotency_key "buy-2026-q1-inflight-001"
    And a prior request for the same (seller, account, idempotency_key) pair is still in flight
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a terminal failure
    And the error code should be "IDEMPOTENCY_IN_FLIGHT"
    And the error should include "retry_after" field
    And the error should include "suggestion" field
    And no new media buy should have been created
    # BR-RULE-211 INV-4: in-flight key MAY reject with IDEMPOTENCY_IN_FLIGHT; buyer MUST NOT mint a fresh key
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-idempotency-expired @v31 @idempotency-key @error-details @post-f2 @post-f3 @ext-w
  Scenario: v3.1 idempotency_key whose cached response has expired rejects with IDEMPOTENCY_EXPIRED
    Given a create_media_buy request with idempotency_key "buy-2026-q1-expired-001"
    And the (seller, account, idempotency_key) pair was recorded but its cached response expired past replay_ttl_seconds
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a correctable failure
    And the error code should be "IDEMPOTENCY_EXPIRED"
    And the error should include "suggestion" field
    And no new media buy should have been created
    # BR-RULE-211 INV-5: expired cached response -> IDEMPOTENCY_EXPIRED; buyer MUST perform natural-key existence check before minting fresh key
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-idempotency-canonical-comparison @v31 @idempotency-key @post-s1 @ext-w @happy-path
  Scenario: v3.1 idempotency replay with reordered fields and whitespace is treated as identical
    Given a media buy was already created for the same seller with idempotency_key "buy-2026-q1-canon-001"
    And a create_media_buy request with idempotency_key "buy-2026-q1-canon-001" whose payload differs only in field ordering and insignificant whitespace
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the response should include the previously created "media_buy_id"
    And no new ad platform order should have been created
    # BR-RULE-211 INV-6: canonical comparison is semantic; field ordering / insignificant whitespace MUST NOT affect outcome
    # --- v3.1: submitted task envelope discrimination ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-submitted-envelope-shape @v31 @submitted-envelope @post-s7 @post-s9
  Scenario: v3.1 submitted envelope carries task_id but not media_buy_id
    Given the tenant has "human_review_required" set to true
    And a valid create_media_buy request with account "acc-001"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response status should be "submitted"
    And the response should include a "task_id"
    And the response should not include a media_buy_id at the envelope level
    And the response should not include a packages array at the envelope level
    # v3.1: media_buy_id and packages land on the task's completion artifact, not the envelope
    # v3.1: not.anyOf excludes media_buy_id, packages, sandbox from submitted shape
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-submitted-envelope-message @v31 @submitted-envelope @prompt-injection
  Scenario: v3.1 submitted envelope optional message field is untrusted seller input
    Given the tenant requires manual approval
    And a valid create_media_buy request with account "acc-001"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    And the seller emits a submitted envelope with a human-readable message
    Then the response status should be "submitted"
    And the buyer SHOULD treat the message field as untrusted input
    And the buyer SHOULD escape the message before rendering to HTML
    And the buyer SHOULD sanitize the message before passing to an LLM prompt context
    # v3.1: message field is bounded to maxLength 2000, plain text only
    # v3.1: message MUST be treated as untrusted seller input (prompt-injection defense)
    # --- v3.1: synchronous success response fields ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-success-revision-and-actions @v31 @sync-success @post-s1 @post-s3
  Scenario: v3.1 sync success response carries revision, confirmed_at, valid_actions
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the response status should be "completed"
    And the response should include a "media_buy_id"
    And the response should include "confirmed_at" as an ISO 8601 timestamp
    And the response should include "revision" with an integer value of at least 1
    And the response should include a "valid_actions" array
    And every value in valid_actions should be a member of the media-buy-valid-action enum
    # v3.1: confirmed_at — order confirmation timestamp
    # v3.1: revision — initial value >= 1 for optimistic concurrency on update_media_buy
    # v3.1: valid_actions — subset of [pause, resume, cancel, update_budget, update_dates, update_packages, add_packages, sync_creatives]
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-success-status-values @v31 @sync-success @media-buy-status
  Scenario Outline: v3.1 sync success status is a MediaBuyStatus enum value, never "submitted"
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request producing the "<initial_state>" condition
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the response status should be "completed"
    And the response should include a "status" field with value "<media_buy_status>"
    And the response status field should not equal "submitted"
    # v3.1: synchronous-success status field carries MediaBuyStatus, not task-level "submitted"
    # v3.1: "submitted" is a task-level literal, exclusive to the submitted envelope shape
    # --- v3.1: governance plan_id ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

    Examples:
      | initial_state                       | media_buy_status   |
      | creatives missing for all packages  | pending_creatives  |
      | creatives present, future flight    | pending_start      |
      | creatives present, asap flight      | active             |

  @T-UC-002-v31-plan-id-required-when-governance @v31 @governance @plan-id @validation @ext-x
  Scenario: v3.1 plan_id required when account has governance_agents configured
    Given a create_media_buy request with the plan_id field omitted
    And the account "acc-001" exists and is active
    And the account has governance_agents configured
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a validation error
    And the error should reference the missing "plan_id" field
    And the error should include "suggestion" field
    # v3.1: plan_id is required when governance_agents is non-empty on the account
    # BR-RULE-212 INV-1: governance configured + plan_id absent -> INVALID_REQUEST (correctable)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-plan-id-resolves @v31 @governance @plan-id @ext-x @happy-path
  Scenario: v3.1 plan_id present and resolvable forwards governance plan and proceeds
    Given a valid create_media_buy request with plan_id "plan-001"
    And the account "acc-001" exists and is active
    And the account has governance_agents configured
    And the plan "plan-001" resolves and is accessible to the account
    When the Buyer Agent sends the create_media_buy request
    Then the request should proceed past the governance gate
    And the plan_id should be forwarded to check_governance
    # BR-RULE-212 INV-2: governance configured + plan_id resolves -> proceeds
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-plan-id-not-found @v31 @governance @plan-id @error-details @post-f2 @post-f3 @ext-x
  Scenario: v3.1 plan_id that does not resolve rejects with PLAN_NOT_FOUND
    Given a create_media_buy request with plan_id "plan-missing"
    And the account "acc-001" exists and is active
    And the account has governance_agents configured
    And the plan "plan-missing" does not exist or is not accessible to the account
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a correctable failure
    And the error code should be "PLAN_NOT_FOUND"
    And the error should include "suggestion" field
    # BR-RULE-212 INV-3: plan_id present but unresolvable -> PLAN_NOT_FOUND (uniform response to prevent enumeration)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-plan-id-optional-without-governance @v31 @governance @plan-id @ext-x @happy-path
  Scenario: v3.1 plan_id optional when account has no governance_agents configured
    Given a valid create_media_buy request with the plan_id field omitted
    And the account "acc-001" exists and is active
    And the account has no governance_agents configured
    When the Buyer Agent sends the create_media_buy request
    Then the request should proceed past the governance gate
    And the governance path should be skipped
    # BR-RULE-212 INV-4: no governance agents -> plan_id optional, governance skipped
    # --- v3.1: invoice_recipient override ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-invoice-recipient-echoed @v31 @invoice-recipient @post-s5
  Scenario: v3.1 invoice_recipient overrides default billing and is echoed in response
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request with an invoice_recipient override
    And the invoice_recipient is authorized for the account
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the response status should be "completed"
    And the response should include an "invoice_recipient" matching the request
    And the response invoice_recipient should not include any bank-detail fields
    # v3.1: per-buy billing override; seller MUST validate invoice_recipient is authorized for the account
    # v3.1: bank details are write-only and omitted from the response
    # --- v3.1: io_acceptance for proposal IO signing ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-io-acceptance-required @v31 @io-acceptance @proposal-based @error-details @post-f2 @post-f3 @ext-x
  Scenario: v3.1 proposal requires IO signature but io_acceptance is missing -- rejected with IO_REQUIRED
    Given a valid create_media_buy request with proposal_id "prop-abc-2026"
    And the proposal's insertion_order requires_signature is true
    And the request does not include an io_acceptance field
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a correctable failure
    And the error code should be "IO_REQUIRED"
    And the error should include "suggestion" field
    # BR-RULE-213 INV-1: requires_signature true + io_acceptance absent -> IO_REQUIRED (correctable)
    # v3.1 supersession: BR-RULE-213 mandates IO_REQUIRED reject-and-resubmit (replaces the earlier task-envelope-for-IO-signing modeling)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-io-acceptance-provided @v31 @io-acceptance @proposal-based @post-s11 @ext-x @happy-path
  Scenario: v3.1 io_acceptance with matching io_id and signatory completes proposal execution synchronously
    Given a valid create_media_buy request with proposal_id "prop-abc-2026"
    And the proposal's insertion_order requires_signature is true
    And the request includes io_acceptance with:
    | field         | value                                |
    | io_id         | prop-abc-2026.io.v1                  |
    | accepted_at   | 2026-04-01T12:00:00Z                 |
    | signatory     | buyer-agent-12345                    |
    And the io_acceptance.io_id matches the proposal's insertion_order.io_id
    And the account "acc-001" exists and is active
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the response status should be "completed"
    And the response should include a "media_buy_id"
    # v3.1: io_acceptance carries the signing metadata; signature_id is optional
    # BR-RULE-213 INV-3: complete io_acceptance -> gate satisfied, proceeds
    # POST-S11: Buyer knows the proposal was successfully executed with their total budget
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-io-acceptance-incomplete @v31 @io-acceptance @proposal-based @validation @post-f2 @ext-x
  Scenario Outline: v3.1 io_acceptance missing a required member rejects with INVALID_REQUEST
    Given a create_media_buy request with proposal_id "prop-abc-2026" and an io_acceptance missing "<missing_member>"
    And the proposal's insertion_order requires_signature is true
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a validation error
    And the error code should be "INVALID_REQUEST"
    And the error should reference the missing "<missing_member>" member
    And the error should include "suggestion" field
    # BR-RULE-213 INV-2: io_acceptance present but missing io_id/accepted_at/signatory -> INVALID_REQUEST naming member(s)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

    Examples:
      | missing_member |
      | io_id          |
      | accepted_at    |
      | signatory      |

  @T-UC-002-v31-io-acceptance-not-required @v31 @io-acceptance @proposal-based @ext-x @happy-path
  Scenario: v3.1 io_acceptance optional when proposal does not require signature
    Given a valid create_media_buy request with proposal_id "prop-no-sig-2026" and the io_acceptance field omitted
    And the proposal's insertion_order requires_signature is false
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the request should proceed to further validation
    And the io_acceptance field should not affect processing
    # BR-RULE-213 INV-4: requires_signature false/absent -> io_acceptance optional, no effect
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-io-acceptance-signature-id @v31 @io-acceptance @proposal-based @ext-x @happy-path
  Scenario: v3.1 optional signature_id is recorded without altering the acceptance gate
    Given a valid create_media_buy request with proposal_id "prop-abc-2026"
    And the proposal's insertion_order requires_signature is true
    And the request includes io_acceptance with io_id, accepted_at, signatory, and signature_id "sig-998"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the IO acceptance gate should be satisfied
    And the signature_id should be recorded as a signing-service reference
    # BR-RULE-213 INV-5: optional signature_id recorded as reference, does not alter gate outcome
    # --- v3.1: advertiser_industry per buy ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-advertiser-industry-per-buy @v31 @advertiser-industry @post-s1
  Scenario: v3.1 advertiser_industry on the request targets a single industry per buy
    Given the tenant is configured for auto-approval
    And the brand "acme.com" operates across industries ["healthcare.wellness", "cpg"]
    And a valid create_media_buy request with advertiser_industry "healthcare.wellness"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the response status should be "completed"
    And the seller should map advertiser_industry to its platform-native industry code
    # v3.1: a brand may operate across multiple industries; each media buy targets exactly one
    # --- v3.1: package paused flag ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-package-paused-on-creation @v31 @paused @post-s1
  Scenario: v3.1 package created with paused=true does not deliver impressions
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request with one package having paused set to true
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the response status should be "completed"
    And the paused package should be created in a paused state
    And the paused package should not deliver impressions until resumed
    # v3.1: paused flag on package-request (default false); paused packages do not serve
    # --- v3.1: committed_metrics negotiation ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-committed-metrics-standard-accepted @v31 @committed-metrics @post-s3
  Scenario: v3.1 committed_metrics standard scope -- seller accepts and stamps committed_at
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request
    And the request package includes committed_metrics with one entry of scope "standard"
    And the proposed standard metric_id is present in the product's available_metrics
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the response status should be "completed"
    And the response package committed_metrics should echo the standard metric_id
    And the response package committed_metrics entry should include a "committed_at" timestamp
    # v3.1: committed_at is stamped by the seller on accept; request-side entries do not carry it
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-committed-metrics-rejected @v31 @committed-metrics @validation @post-f2
  Scenario: v3.1 committed_metrics references metric not in product available_metrics -- TERMS_REJECTED
    Given a valid create_media_buy request
    And the request package includes committed_metrics with standard metric_id not in the product's available_metrics
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate an error
    And the error code should be "TERMS_REJECTED"
    And the error should reference the offending committed_metrics entry
    # v3.1: seller SHOULD reject with TERMS_REJECTED when the proposal exceeds product capability
    # --- v3.1: planned_delivery transparency ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-planned-delivery-with-governance @v31 @planned-delivery @governance @post-s3
  Scenario: v3.1 planned_delivery present in response when account has governance agents
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request with plan_id "plan-2026-q1"
    And the account "acc-001" has governance_agents configured
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the response status should be "completed"
    And the response should include a "planned_delivery" block
    # v3.1: planned_delivery describes what the seller will actually run (geo, channels, flight, freq caps, budget)
    # v3.1: present when account has governance_agents OR seller chooses to provide delivery transparency
    # --- v3.1: artifact_webhook for governance content delivery ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-artifact-webhook-config @v31 @artifact-webhook @governance
  Scenario: v3.1 artifact_webhook configured for content delivery to governance agent
    Given a valid create_media_buy request
    And the request includes an artifact_webhook with delivery_mode "realtime"
    And the artifact_webhook authentication scheme is "Bearer"
    And the account "acc-001" exists and is active
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the response status should be "completed"
    And the seller should accept the artifact_webhook configuration for governance content delivery
    # v3.1: artifact_webhook enables governance content-adjacency validation
    # v3.1: same authentication structure as reporting_webhook (Bearer or HMAC-SHA256, both deprecated; RFC 9421 preferred)
    # --- v3.1: agency_estimate_number ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-agency-estimate-number-buy-level @v31 @agency-estimate-number @broadcast
  Scenario: v3.1 agency_estimate_number at media-buy level travels with the order
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request with agency_estimate_number "AE-2026-Q1-12345"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the response status should be "completed"
    And the agency_estimate_number should be persisted on the media buy
    # v3.1: primary financial reference for broadcast buys; maxLength 100
    # v3.1: per-package agency_estimate_number MAY override the buy-level value
    # --- v3.1 wave A: BUDGET_TOO_LOW error-details routing ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-error-budget-too-low-details @v31 @error-details @budget-too-low @post-f2 @post-f3
  Scenario: v3.1 BUDGET_TOO_LOW error carries minimum_budget and currency in details
    Given a create_media_buy request with total_budget 50
    And the product minimum spend is 500 USD
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a terminal failure
    And the error code should be "BUDGET_TOO_LOW"
    And the error details should include minimum_budget 500
    And the error details should include currency "USD"
    # v3.1: error-details/budget-too-low.json — recommended details for BUDGET_TOO_LOW
    # v3.1: recovery classification correctable — buyer adjusts and retries
    # --- v3.1 wave A: IDEMPOTENCY_CONFLICT error-details routing (idempotency revision mismatch) ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-error-conflict-details @v31 @error-details @conflict @idempotency-key @post-f2 @ext-w
  Scenario: v3.1 IDEMPOTENCY_CONFLICT error carries resource_id and version info on idempotency-key replay with divergent payload
    Given a create_media_buy request with idempotency_key "buy-2026-q1-conflict-001"
    And a prior media buy "mb-789" exists for the same (seller, idempotency_key) pair with revision 1
    And the new request payload diverges from the prior request payload
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a correctable failure
    And the error code should be "IDEMPOTENCY_CONFLICT"
    And the error details should include resource_id "mb-789"
    And the error details should include current_version 1
    And the error should include "suggestion" field
    # v3.1: error-details/conflict.json — recommended details for IDEMPOTENCY_CONFLICT
    # v3.1: recovery correctable — resend the exact original payload, or mint a fresh idempotency_key for the changed order
    # --- v3.1 wave A: POLICY_VIOLATION error-details routing ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-error-policy-violation-details @v31 @error-details @policy-violation @post-f2 @post-f3 @ext-y
  Scenario: v3.1 POLICY_VIOLATION error carries policy_id and violated_rules
    Given a create_media_buy request that violates the seller's editorial policy
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a terminal failure
    And the error code should be "POLICY_VIOLATION"
    And the error details should include a "policy_id" field
    And the error details should include a "violated_rules" array
    And the error should include "suggestion" field
    # v3.1: error-details/policy-violation.json — recommended details for POLICY_VIOLATION
    # v3.1: policy_url MAY be present for full policy text retrieval
    # --- v3.1 wave A: BILLING_NOT_SUPPORTED error-details routing ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-error-billing-not-supported-capability @v31 @error-details @billing-not-supported @scope-capability @post-f2 @post-f3 @ext-z
  Scenario: v3.1 BILLING_NOT_SUPPORTED scope=capability echoes supported_billing
    Given a create_media_buy request with billing value "agent"
    And the seller's supported_billing capability is ["operator", "advertiser"]
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a terminal failure
    And the error code should be "BILLING_NOT_SUPPORTED"
    And the error details scope should be "capability"
    And the error details supported_billing should equal ["operator", "advertiser"]
    And the error should include "suggestion" field
    # v3.1: error-details/billing-not-supported.json — capability-gate variant
    # v3.1: advice — buyer chooses from supported_billing and retries
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-error-billing-not-supported-account @v31 @error-details @billing-not-supported @scope-account @post-f2 @ext-z
  Scenario: v3.1 BILLING_NOT_SUPPORTED scope=account is emitted only when agent identity is established
    Given a create_media_buy request with billing value "agent"
    And the seller's supported_billing capability includes "agent" generally
    And the seller does not accept "agent" billing on the account "acc-001" relationship
    And the caller's agent identity is established
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a terminal failure
    And the error code should be "BILLING_NOT_SUPPORTED"
    And the error details scope should be "account"
    And the error should include "suggestion" field
    # v3.1: error-details/billing-not-supported.json — per-account-relationship gate
    # v3.1: scope MUST be omitted on the unauthenticated/unestablished-identity path to avoid acting as a per-account oracle
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-error-billing-not-supported-unauth-omits-scope @v31 @error-details @billing-not-supported @uniform-response @post-f2 @ext-z
  Scenario: v3.1 BILLING_NOT_SUPPORTED omits scope when agent identity is not established
    Given a create_media_buy request with billing value "agent"
    And the caller's agent identity is NOT established
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a terminal failure
    And the error code should be "BILLING_NOT_SUPPORTED"
    And the error details should NOT include a "scope" field
    And the error should include "suggestion" field
    # v3.1: uniform-response rule — emitting scope=account on an unauthenticated path leaks per-account state
    # --- v3.1 wave A: BILLING_NOT_PERMITTED_FOR_AGENT error-details routing ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-error-billing-not-permitted-for-agent-suggested @v31 @error-details @billing-not-permitted-for-agent @autonomous-retry @post-f2 @post-f3 @ext-z
  Scenario: v3.1 BILLING_NOT_PERMITTED_FOR_AGENT with suggested_billing enables autonomous retry
    Given a create_media_buy request with billing value "agent"
    And the seller's supported_billing capability includes "agent" generally
    And the calling agent's commercial relationship with the seller is passthrough-only
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a terminal failure
    And the error code should be "BILLING_NOT_PERMITTED_FOR_AGENT"
    And the error details rejected_billing should be "agent"
    And the error details suggested_billing should be "operator"
    And the error should include "suggestion" field
    # v3.1: error-details/billing-not-permitted-for-agent.json — minimal shape
    # v3.1: suggested_billing carries AT MOST ONE canonical retry value
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-error-billing-not-permitted-for-agent-terminal @v31 @error-details @billing-not-permitted-for-agent @terminal-pending-onboarding @post-f2 @ext-z
  Scenario: v3.1 BILLING_NOT_PERMITTED_FOR_AGENT without suggested_billing is terminal-pending-onboarding
    Given a create_media_buy request with billing value "advertiser"
    And the calling agent has no retryable billing value for this seller's commercial relationship
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a terminal failure
    And the error code should be "BILLING_NOT_PERMITTED_FOR_AGENT"
    And the error details rejected_billing should be "advertiser"
    And the error details should NOT include a "suggested_billing" field
    And the error should include "suggestion" field
    # v3.1: terminal-pending-onboarding — buyer must complete offline payments-relationship onboarding
    # v3.1: sellers MUST NOT add ad-hoc keys carrying per-agent commercial state
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-billing-eligible-proceeds @v31 @billing @post-s6 @ext-z @happy-path
  Scenario: v3.1 billing party in supported_billing and agent-permitted proceeds past the billing gate
    Given a valid create_media_buy request with billing value "operator"
    And the seller's supported_billing capability includes "operator"
    And the calling agent's commercial relationship permits "operator" billing
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the request should proceed past the billing eligibility gate
    # BR-RULE-214 INV-1: eligible billing party -> proceeds to next validation
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-214-8 @invariant @BR-RULE-214 @billing @v31 @open-question
  Scenario: INV-8 -- unauthorized invoice_recipient override fails the billing-eligibility gate
    Given a create_media_buy request that carries an invoice_recipient overriding the account default billing entity
    And the account exists and is active
    But the invoice_recipient is not authorized for the account
    When the Buyer Agent sends the create_media_buy request
    Then the seller should validate the invoice_recipient authorization before the buy proceeds
    And the buy should be rejected at the buy-time billing-eligibility gate
    And no dedicated error code is defined in v3.1 for this rejection
    # BR-RULE-214 INV-8 (v3.1): entity-level gate orthogonal to capability/per-account/per-agent gates; error code is an open question
    # --- v3.1 wave A: offering / offering-asset-group lifecycle ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-offering-referenced-via-package-catalog @v31 @offering @offering-asset-group @catalog-driven @post-s3
  Scenario: v3.1 media buy with catalog-driven package references brand offering with typed asset groups
    Given a valid create_media_buy request
    And a package targets a catalog whose items are brand offerings
    And the referenced offering "summer-sale" has structured asset groups including "headlines" and "images_landscape"
    And the account "acc-001" exists and is active
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the response status should be "completed"
    And the package should be bound to the offering's asset groups for creative assembly
    # v3.1: core/offering.json — promotable brand offering with assets[] of core/offering-asset-group.json
    # v3.1: asset_group_id values come from per-format offering_asset_constraints (discoverable via list_creative_formats)
    # --- v3.1 wave A: catchment in targeting.store_catchments ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-catchment-isochrone @v31 @catchment @store-catchments @targeting @post-s1
  Scenario: v3.1 targeting.store_catchments with isochrone method (travel_time + transport_mode)
    Given a valid create_media_buy request
    And the targeting includes a store_catchments entry with catchment_id "drive", travel_time {value:15, unit:"min"}, and transport_mode "driving"
    And the account "acc-001" exists and is active
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the catchment should be accepted as an isochrone definition
    # v3.1: core/catchment.json — oneOf travel_time+transport_mode XOR radius XOR geometry
    # v3.1: travel_time requires transport_mode
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-catchment-radius @v31 @catchment @store-catchments @targeting @post-s1
  Scenario: v3.1 targeting.store_catchments with simple radius method
    Given a valid create_media_buy request
    And the targeting includes a store_catchments entry with catchment_id "local" and radius {value:5, unit:"km"}
    And the account "acc-001" exists and is active
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the catchment should be accepted as a radius definition
    # v3.1: core/catchment.json — radius variant (exclusiveMinimum 0)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-catchment-xor-violation @v31 @catchment @store-catchments @validation @post-f2
  Scenario: v3.1 catchment with both radius and geometry violates oneOf and is rejected
    Given a create_media_buy request
    And the targeting includes a store_catchments entry providing BOTH radius and geometry
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a validation error
    And the error should reference the catchment oneOf constraint
    # v3.1: core/catchment.json — oneOf enforces exactly one method per catchment
    # --- v3.1 wave A: price (catalog/proposal pricing surface) ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-price-shape-on-proposal-item @v31 @price @proposal-based @post-s11
  Scenario: v3.1 proposal item carries price with amount, ISO 4217 currency, and period
    Given a proposal "prop-001" referenced by proposal_id
    And the proposal's product allocation item carries a price {amount: 1200, currency: "USD", period: "one_time"}
    And the total_budget covers the proposal allocations
    And the account "acc-001" exists and is active
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the response status should be "completed"
    # v3.1: core/price.json — amount >= 0, currency ^[A-Z]{3}$, optional period (night|month|year|one_time)
    # --- v3.1 wave A: frequency_cap.scope vocabulary ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-frequency-cap-scope-package @v31 @frequency-cap-scope @targeting @post-s1
  Scenario: v3.1 frequency_cap.scope accepts the registered "package" value
    Given a valid create_media_buy request
    And the targeting frequency_cap.scope is "package"
    And the account "acc-001" exists and is active
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    # v3.1: enums/frequency-cap-scope.json — sole registered value is "package"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-frequency-cap-scope-unregistered @v31 @frequency-cap-scope @targeting @validation @post-f2
  Scenario: v3.1 frequency_cap.scope rejects unregistered values
    Given a create_media_buy request
    And the targeting frequency_cap.scope is "campaign"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should indicate a validation error
    And the error should reference the frequency_cap.scope enum
    # v3.1: cross-package and cross-buy scoping NOT supported in this protocol release

  @T-UC-002-boundary-sandbox-response @boundary @sandbox @br-rule-209
  Scenario Outline: v3.1 sandbox response-semantics boundary validation - <boundary_point>
    Given a create_media_buy request whose account resolution matches "<boundary_point>"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

    Examples: Boundary values
      | boundary_point                                              | outcome                                |
      | sandbox: true in response (sandbox account)                 | success with sandbox echoed true       |
      | sandbox absent in response (production account)             | success with sandbox omitted           |
      | sandbox: false in response (explicit production)            | success with sandbox false             |
      | sandbox account with invalid budget (real validation error) | error BUDGET_TOO_LOW with suggestion   |
      | sandbox: true on create_media_buy synchronous success shape | success with sandbox echoed true       |
      | sandbox present on create_media_buy terminal-failure (errors) shape | error response without sandbox field |

  @T-UC-002-boundary-billing-eligibility @boundary @billing @br-rule-214
  Scenario Outline: v3.1 billing eligibility boundary validation - <boundary_point>
    Given a create_media_buy request whose resolved billing reflects "<boundary_point>"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

    Examples: Boundary values
      | boundary_point                                                                                                     | outcome                                              |
      | billing resolved = operator; operator in supported_billing; agent permitted                                        | proceeds past billing gate                           |
      | billing resolved = advertiser; seller's supported_billing = [operator, agent]                                      | error BILLING_NOT_SUPPORTED with suggestion          |
      | billing resolved = operator; seller supports operator generally; no operator billing relationship on this account  | error BILLING_NOT_SUPPORTED scope account with suggestion |
      | billing resolved = agent; agent in supported_billing; calling agent is passthrough-only                            | error BILLING_NOT_PERMITTED_FOR_AGENT with suggestion |
      | billing resolved = agent; caller unauthenticated                                                                   | error BILLING_NOT_SUPPORTED with suggestion          |
      | invoice_recipient supplied = business entity not authorized for this account                                       | error rejected at buy-time billing gate (no dedicated code, open question) |

  @T-UC-002-boundary-plan-id-governance @boundary @governance @plan-id @br-rule-212
  Scenario Outline: v3.1 governance plan_id boundary validation - <boundary_point>
    Given a create_media_buy request and account governance state matching "<boundary_point>"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

    Examples: Boundary values
      | boundary_point                                                  | outcome                               |
      | governance agents configured + plan_id present + plan resolves  | proceeds past governance gate         |
      | governance agents configured + plan_id absent                   | error INVALID_REQUEST with suggestion |
      | governance agents configured + plan_id present + plan not found  | error PLAN_NOT_FOUND with suggestion  |
      | no governance agents + plan_id absent                           | proceeds, governance skipped          |
      | no governance agents + plan_id present                          | proceeds, governance skipped          |

  @T-UC-002-boundary-io-acceptance @boundary @io-acceptance @br-rule-213
  Scenario Outline: v3.1 io_acceptance gate boundary validation - <boundary_point>
    Given a proposal-based create_media_buy request matching "<boundary_point>"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

    Examples: Boundary values
      | boundary_point                                                                 | outcome                               |
      | requires_signature false, io_acceptance absent (no gate)                       | proceeds, no IO gate                  |
      | requires_signature true, io_acceptance present with all required members       | proceeds, gate satisfied              |
      | requires_signature true, io_acceptance present with signature_id also supplied | proceeds, gate satisfied              |
      | requires_signature false, io_acceptance present (no-op)                        | proceeds, io_acceptance ignored       |
      | requires_signature true, io_acceptance absent (gate blocks)                    | error IO_REQUIRED with suggestion     |
      | requires_signature true, io_acceptance present but io_id missing               | error INVALID_REQUEST with suggestion |
      | requires_signature true, io_acceptance present but accepted_at missing         | error INVALID_REQUEST with suggestion |
      | requires_signature true, io_acceptance present but signatory missing           | error INVALID_REQUEST with suggestion |

  @T-UC-002-boundary-creative-asset-discriminator @boundary @creative-asset @br-rule-015
  Scenario Outline: v3.1 creative asset discriminator boundary validation - <boundary_point>
    Given a create_media_buy request with an inline creative matching "<boundary_point>"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/create-media-buy-request.json

    Examples: Boundary values
      | boundary_point                                          | outcome                               |
      | asset slot value with missing asset_type discriminator  | error INVALID_REQUEST with suggestion |
      | asset slot value with unknown asset_type value          | error INVALID_REQUEST with suggestion |

  @T-UC-002-boundary-targeting-collection-list @boundary @targeting-overlay @collection-list @br-rule-014
  Scenario Outline: v3.1 targeting_overlay collection-list boundary validation - <boundary_point>
    Given a create_media_buy request with package targeting matching "<boundary_point>"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                            | outcome                      |
      | collection_list with valid agent_url and list_id          | targeting validation passes  |
      | collection_list_exclude with valid agent_url and list_id  | targeting validation passes  |
      | both collection_list and collection_list_exclude set      | targeting validation passes  |

  @T-UC-002-storyboard-async-submitted-envelope-task-id-roundtrip @storyboard-v3.1 @v3-1 @submitted-envelope @async @task-id-roundtrip
  Scenario: Async submitted envelope -- task_id matches deterministic value registered via comply_test_controller
    Given a comply_test_controller directive registered force_create_media_buy_arm with arm "submitted" and task_id "task_async_signed_io_q2"
    And the directive is keyed to the caller's authenticated sandbox account
    When the Buyer Agent sends create_media_buy under the registered sandbox account
    Then the response should carry status "submitted"
    And the response should carry task_id "task_async_signed_io_q2"
    And the response should NOT carry media_buy_id on the submitted envelope
    And the response should NOT carry packages on the submitted envelope
    And the task_id on the response should match the value registered by the controller directive
    # create_media_buy_async storyboard: a controller directive
    # (force_create_media_buy_arm with task_id=X) registers a deterministic task_id
    # against the caller's sandbox account. The subsequent create_media_buy MUST
    # honor that directive and return status=submitted with task_id=X. Sellers
    # that fabricate a fresh task_id break the buyer's polling contract -- buyers
    # cannot tell whether the buy is queued or confirmed.
    # create_media_buy_async: deterministic task_id roundtrips through registered controller directive
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/governance_approved.yaml

  @T-UC-002-storyboard-governance-approved @storyboard-v3.1 @v3-1 @governance @governance-decision
  Scenario: Governance approved -- seller creates the buy and propagates the governance decision payload
    Given the buyer's governance agent has returned decision "APPROVED" for the proposed buy
    And the buyer attaches the governance_decision payload to the create_media_buy request
    When the Buyer Agent sends create_media_buy with the governance_decision payload
    Then the response should carry status "active" or "pending_start"
    And the response should carry the media_buy_id
    And the response should echo the governance_decision with decision "APPROVED"
    # governance_approved storyboard: the buyer's governance agent (orchestrator side)
    # returns an APPROVED decision before create_media_buy. The seller persists the
    # buy with the governance_decision payload echoed in the response.
    # governance_approved: APPROVED decision flows through to the persisted buy
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/governance_conditions.yaml

  @T-UC-002-storyboard-governance-with-conditions @storyboard-v3.1 @v3-1 @governance @governance-decision @conditions
  Scenario: Governance approved with conditions -- seller attaches conditions to the buy
    Given the buyer's governance agent has returned decision "APPROVED_WITH_CONDITIONS" with a non-empty conditions array
    And the buyer attaches the governance_decision payload to the create_media_buy request
    When the Buyer Agent sends create_media_buy with the governance_decision payload
    Then the response should carry the media_buy_id
    And the response should echo the governance_decision with decision "APPROVED_WITH_CONDITIONS"
    And the response should carry the conditions array attached to the persisted buy
    # governance_conditions storyboard: governance returns APPROVED_WITH_CONDITIONS
    # carrying a conditions array. The seller persists the buy AND surfaces the
    # conditions on the response so downstream systems (delivery monitoring,
    # billing) can enforce them.
    # governance_conditions: conditions persist on the buy for downstream enforcement
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/governance_denied.yaml

  @T-UC-002-storyboard-governance-denied @storyboard-v3.1 @v3-1 @governance @governance-decision @rejection
  Scenario: Governance denied -- seller rejects the buy with GOVERNANCE_DENIED and propagates denial rationale
    Given the buyer's governance agent has returned decision "DENIED" with a denial reason
    And the buyer attaches the governance_decision payload to the create_media_buy request
    When the Buyer Agent sends create_media_buy with the governance_decision payload
    Then the operation should fail
    And the error code should be "GOVERNANCE_DENIED"
    And the error details should include the denial reason from the governance decision
    # governance_denied storyboard: governance returns DENIED. The seller MUST
    # reject create_media_buy with error code GOVERNANCE_DENIED and propagate
    # the denial reason on the error details so the buyer's orchestrator can
    # log and either escalate to human or retry with reduced scope.
    # governance_denied: seller surfaces governance denial as a structured error
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/governance_denied_recovery.yaml

  @T-UC-002-storyboard-governance-denied-recovery @storyboard-v3.1 @v3-1 @governance @recovery
  Scenario: Governance denied recovery -- buyer shrinks the buy to within plan limits and retries successfully
    Given a previous create_media_buy attempt failed with error code "GOVERNANCE_DENIED"
    And the buyer reduces the proposed buy to within the governance agent's spending authority
    And the buyer obtains a new "APPROVED" decision from the governance agent
    When the Buyer Agent sends a corrected create_media_buy with the new APPROVED governance_decision
    Then the response should carry the media_buy_id
    And the response should carry status "active" or "pending_start"
    # governance_denied_recovery storyboard: after GOVERNANCE_DENIED, the buyer
    # reduces the buy (typically budget or scope) to fit within the governance
    # agent's authority, re-runs the governance check (orchestrator-side), and
    # resubmits. The seller MUST accept the retried buy on its merits without
    # caching the prior denial.
    # governance_denied_recovery: corrected buy with new APPROVED decision succeeds
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/inventory_list_no_match.yaml

  @T-UC-002-storyboard-inventory-list-no-match @storyboard-v3.1 @v3-1 @inventory-list @no-match
  Scenario: Inventory list references that resolve to zero inventory -- zero-forecast or informative error, never silent success
    Given the buyer references a property_list whose entries do not match any seller inventory
    And the buyer references a collection_list whose entries do not match any seller inventory
    When the Buyer Agent sends create_media_buy with the no-match list references in targeting_overlay
    Then the response should NOT be a silent success with normal forecast numbers
    And one of the following two outcomes should be observed:
    | outcome              | required behavior                                                                       |
    | zero_forecast_accept | buy accepted with packages reporting zero deliverable inventory and a mismatch message  |
    | informative_error    | error code "PRODUCT_UNAVAILABLE" or "INVALID_REQUEST" with findings identifying lists |
    # inventory_list_no_match storyboard: the buyer references a PropertyListReference
    # and CollectionListReference that resolve to nothing in the seller's catalog.
    # The seller MUST either (a) accept the buy with zero-forecast reporting OR
    # (b) reject with PRODUCT_UNAVAILABLE/INVALID_REQUEST carrying findings
    # identifying which list matched nothing. Silently-successful buys with normal
    # forecast numbers, crashes, or non-AdCP error shapes are compliance failures.
    # inventory_list_no_match: empty intersection MUST be surfaced truthfully
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/inventory_list_targeting.yaml

  @T-UC-002-storyboard-inventory-list-targeting-parity @storyboard-v3.1 @v3-1 @inventory-list @property-list @collection-list
  Scenario: PropertyListReference and CollectionListReference honored in package targeting on create_media_buy
    Given the buyer holds a property_list (agent_url, list_id) that matches seller inventory
    And the buyer holds a collection_list (agent_url, list_id) that matches seller inventory
    When the Buyer Agent sends create_media_buy with property_list and collection_list in package targeting_overlay
    Then the response should carry the media_buy_id
    And the persisted package targeting should reflect the property_list and collection_list references
    # inventory_list_targeting storyboard: the seller MUST accept
    # PropertyListReference (agent_url+list_id) and CollectionListReference
    # (agent_url+list_id) in package targeting_overlay on create_media_buy. The
    # storyboard also asserts parity with update_media_buy (covered in UC-003)
    # so the buyer can manage list-bound buys post-creation.
    # inventory_list_targeting: list-based targeting honored on create
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/measurement_terms_rejected.yaml

  @T-UC-002-storyboard-measurement-terms-rejected @storyboard-v3.1 @v3-1 @measurement-terms @rejection
  Scenario: Measurement terms unworkable for the seller -- TERMS_REJECTED with terms identified in error details
    Given the buyer attaches measurement_terms that the seller will not accept
    When the Buyer Agent sends create_media_buy with the unworkable measurement_terms
    Then the operation should fail
    And the error code should be "TERMS_REJECTED"
    And the error details should identify which measurement_terms are unworkable
    # measurement_terms_rejected storyboard: the buyer proposes measurement_terms
    # the seller will not accept. Seller MUST reject with TERMS_REJECTED, identifying
    # the unworkable terms so the buyer can retry with seller-compatible terms.
    # Distinct from the existing UC-002 TERMS_REJECTED scenario which targets
    # committed_metrics references not in product available_metrics; this storyboard
    # tests the measurement_terms field specifically.
    # measurement_terms_rejected: seller refuses unworkable measurement_terms with structured error
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/pending_creatives_to_start.yaml

  @T-UC-002-storyboard-pending-creatives-state-transition @storyboard-v3.1 @v3-1 @lifecycle @pending-creatives @pending-start
  Scenario: Media buy created without creatives sits in pending_creatives until sync_creatives completes, then transitions to pending_start
    Given the buyer sends create_media_buy without inline creatives
    When the Buyer Agent sends the create_media_buy request
    Then the response should carry status "pending_creatives"
    When the buyer subsequently completes sync_creatives for all required creatives
    Then the buy's status should transition to "pending_start"
    # pending_creatives_to_start storyboard: when a buy is created without inline
    # creatives, the seller persists it in pending_creatives. Once the buyer calls
    # sync_creatives and all required creatives reach an acceptable status, the
    # buy transitions to pending_start. The state transition is observable via
    # get_media_buys (covered in UC-019) but the anchor is the buy state on UC-002.
    # pending_creatives_to_start: creative sync unblocks pending_creatives -> pending_start
