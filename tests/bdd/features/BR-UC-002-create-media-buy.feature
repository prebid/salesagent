# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

@analysis-2026-03-09 @schema-v3.0.0-rc.1
Feature: BR-UC-002 Create Media Buy
  As a Buyer (via Buyer Agent)
  I want to create a media buy for advertising inventory
  So that my advertising campaign is live on the publisher's ad server

  # Postconditions verified:
  #   POST-S1: Buyer knows their media buy has been created and is activating
  #   POST-S2: Buyer can track the media buy via media_buy_id and buyer_ref
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
    | buyer_ref      | campaign-2026-q1             |
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
    And the response should include "buyer_ref" matching "campaign-2026-q1"
    And the response should include packages with allocations
    And each package should include product_id, budget, and pricing details
    # POST-S1: Buyer knows their media buy has been created and is activating
    # POST-S2: Buyer can track the media buy via media_buy_id and buyer_ref
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
    And the response should include a "media_buy_id"
    And the response should include a "workflow_step_id"
    And the response status should be "submitted"
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
    | buyer_ref      | campaign-2026-q1             |
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
    # POST-S2: Buyer can track the media buy via media_buy_id and buyer_ref
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
    And the error code should be "PRICING_ERROR"
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
    And the error code should be "PRICING_ERROR"
    And the error should include "suggestion" field

  @T-UC-002-ext-n-floor @extension @ext-n @error
  Scenario: Bid price below floor price
    Given a valid create_media_buy request
    And the account exists and is active
    And a package has bid_price 0.50 but floor_price is 1.00
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "PRICING_ERROR"
    And the error should include "suggestion" field
    # --- ext-o: Creative Not Found in Library ---

  @T-UC-002-ext-o @extension @ext-o @error @post-f1 @post-f2 @post-f3
  Scenario: Creative IDs not found in library
    Given a valid create_media_buy request
    And the account exists and is active
    But a package creative_assignment references creative_id "cr-nonexistent"
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "CREATIVES_NOT_FOUND"
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
    And the error code should be "CREATIVE_FORMAT_MISMATCH"
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
    And the error code should be "CREATIVE_UPLOAD_FAILED"
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
    # ── Hand-authored: authorization boundary (PR #1170 review) ──

  @T-UC-002-account-access-denied-id @extension @account @auth @security @hand-authored
  Scenario: Account resolution by ID denied when agent lacks access
    Given a valid create_media_buy request with account_id "acc_other_agent"
    And the account exists but is accessible only to a different agent
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "AUTHORIZATION_ERROR"
    And the error message should contain "access"
    # Security: ID resolution enforces has_access() — agent cannot use another agent's account

  @T-UC-002-account-access-denied-natural-key @extension @account @auth @security @hand-authored
  Scenario: Account resolution by natural key denied when agent lacks access
    Given a valid create_media_buy request with account natural key brand "other-agent.com" operator "other-agent.com"
    And the natural key resolves to an account accessible only to a different agent
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "AUTHORIZATION_ERROR"
    And the error message should contain "access"
    # Security: natural key resolution must have same auth behavior as ID resolution

  @T-UC-002-sandbox-access-denied @extension @account @sandbox @security @hand-authored
  Scenario: Sandbox account resolution denied when agent lacks access
    Given a valid create_media_buy request with account_id "acc_sandbox_other"
    And the sandbox account exists but is accessible only to a different agent
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "AUTHORIZATION_ERROR"
    # Edge case: sandbox flag doesn't bypass access checks

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

  @T-UC-002-inv-006-4 @invariant @BR-RULE-006 @error
  Scenario: INV-4 violated -- neither fixed_price nor floor_price set
    Given a valid create_media_buy request
    And the account exists and is active
    But a package pricing option has neither fixed_price nor floor_price
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error should include "suggestion" field
    # --- BR-RULE-008: Budget Positivity ---

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

    Examples: Invalid partitions
      | partition                | outcome                                   |
      | mixed_currencies         | error UNSUPPORTED_FEATURE with suggestion  |
      | currency_not_in_tenant   | error UNSUPPORTED_FEATURE with suggestion  |

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
      | exceeds_cap           | error BUDGET_TOO_LOW with suggestion           |

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

    Examples: Invalid partitions
      | partition                    | outcome                                         |
      | missing_account              | error INVALID_REQUEST with suggestion             |
      | invalid_oneOf_both           | error INVALID_REQUEST with suggestion             |
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
      | boundary_point                      | config                  | outcome                         |
      | fixed_price only (valid fixed)      | fixed_price=10.00       | pricing validation passes       |
      | floor_price only (valid auction)    | floor_price=1.00        | pricing validation passes       |
      | both present (ambiguous)            | fixed+floor             | error with suggestion           |
      | neither present (undefined)         | neither                 | error with suggestion           |

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
      | two packages, different currencies       | 2 pkg USD+EUR    | error UNSUPPORTED_FEATURE with suggestion |
      | currency not in tenant table             | 1 pkg XYZ        | error UNSUPPORTED_FEATURE with suggestion |

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
      | daily budget > cap (exceeds)             | daily=1001 cap=1000 | error BUDGET_TOO_LOW with suggestion          |
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
      | account field absent                                 | no account               | error INVALID_REQUEST with suggestion             |
      | both account_id and brand/operator present           | both fields              | error INVALID_REQUEST with suggestion             |

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

