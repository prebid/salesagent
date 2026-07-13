# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

@analysis-2026-03-09 @schema-v3.1
Feature: BR-UC-003 Update Media Buy
  As a Buyer (via Buyer Agent)
  I want to update an existing media buy
  So that my advertising campaign reflects my latest requirements

  # Postconditions verified:
  #   POST-S1: Buyer knows their media buy has been updated
  #   POST-S2: Buyer can see which packages were affected
  #   POST-S3: Buyer knows the implementation date (when changes take effect)
  #   POST-S4: Buyer receives an unambiguous success confirmation
  #   POST-S5: Buyer knows the request completed successfully
  #   POST-S6: Buyer knows whether the response is from sandbox mode
  #   POST-S7: Buyer knows their update is awaiting seller approval
  #   POST-S8: Buyer receives a task_id to poll tasks/get; implementation_date is not conveyed on the Submitted envelope.
  #   POST-F1: System state is unchanged on failure (all-or-nothing semantics)
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Buyer knows how to fix the issue and retry

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant exists with completed setup checklist
    And the Buyer is authenticated with a valid principal_id
    And the Buyer owns an existing media buy with media_buy_id "mb_existing"
    And the media buy is in "active" status



  @T-UC-003-main @main-flow @post-s1 @post-s2 @post-s3 @post-s4 @post-s5 @post-s6
  Scenario: Package budget update -- auto-applied via media_buy_id
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    And the updated daily spend does not exceed max_daily_package_spend
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the response should contain media_buy_id "mb_existing"
    And the response should NOT contain "buyer_ref" field
    And the response should contain an implementation_date that is not null
    And the response should contain affected_packages including "pkg_001"
    And the affected package should show the updated budget of 5000
    And the response envelope should include a sandbox flag
    # POST-S1: Buyer knows media buy updated (response contains media_buy_id)
    # POST-S2: Buyer can see pkg_001 was affected (affected_packages)
    # POST-S3: Buyer knows implementation_date (not null)
    # POST-S4: Buyer receives unambiguous success (envelope status "completed")
    # POST-S5: Buyer knows request completed successfully (status "completed")
    # POST-S6: Buyer knows sandbox mode (sandbox flag present)

  @T-UC-003-alt-timing @alt-flow @timing @post-s1 @post-s3 @post-s4 @post-s5
  Scenario: Update timing -- extend end_time
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value                    |
    | media_buy_id | mb_existing              |
    | end_time     | 2026-06-30T23:59:59.000Z |
    And the new end_time is after the existing start_time
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the response should contain an implementation_date that is not null
    # POST-S1: Buyer knows media buy updated
    # POST-S3: Implementation date present
    # POST-S4: Unambiguous success
    # POST-S5: Request completed

  @T-UC-003-alt-timing-asap @alt-flow @timing @post-s1 @post-s4
  Scenario: Update timing -- start_time as "asap"
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | start_time   | asap        |
    And the existing end_time is in the future
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    # POST-S1: Buyer knows start_time updated to current UTC time
    # POST-S4: Unambiguous success

  @T-UC-003-alt-budget @alt-flow @budget @post-s1 @post-s4 @post-s5
  Scenario: Update campaign-level budget
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | budget       | 25000       |
    And the budget 25000 is greater than zero
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the response should contain media_buy_id "mb_existing"
    # POST-S1: Buyer knows budget updated
    # POST-S4: Unambiguous success
    # POST-S5: Request completed

  @T-UC-003-alt-creative-assignments @alt-flow @creatives @post-s1 @post-s2 @post-s4 @post-s5
  Scenario: Update creative assignments -- replacement semantics
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes creative_assignments with:
    | creative_id | weight | placement_ids |
    | cr_001      | 60     | plc_a         |
    | cr_002      | 40     | plc_b         |
    And all referenced creative_ids exist in the creative library
    And all referenced creatives are in valid state (not error or rejected)
    And all placement_ids are valid for the product
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the response should contain affected_packages including "pkg_001"
    # POST-S1: Buyer knows creatives updated
    # POST-S2: Buyer sees affected packages
    # POST-S4: Unambiguous success
    # POST-S5: Request completed

  @T-UC-003-alt-creatives-inline @alt-flow @creatives-inline @post-s1 @post-s2 @post-s4
  Scenario: Upload inline creatives and assign to package
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes inline creatives with valid content
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the response should contain affected_packages including "pkg_001"
    # POST-S1: Buyer knows inline creatives uploaded
    # POST-S2: Buyer sees affected packages
    # POST-S4: Unambiguous success

  @T-UC-003-alt-targeting @alt-flow @targeting @post-s1 @post-s2 @post-s4 @post-s5
  Scenario: Update targeting overlay -- replacement semantics
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field             | value                                |
    | package_id        | pkg_001                              |
    | targeting_overlay | {"geo_countries": ["US", "CA"]}      |
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the response should contain affected_packages including "pkg_001"
    # POST-S1: Buyer knows targeting updated
    # POST-S2: Buyer sees affected packages
    # POST-S4: Unambiguous success
    # POST-S5: Request completed

  @T-UC-003-alt-optimization-goals @alt-flow @optimization-goals @post-s1 @post-s2 @post-s4 @post-s5
  Scenario: Update optimization goals -- replacement semantics
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes optimization_goals:
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the response should contain affected_packages including "pkg_001"
    # POST-S1: Buyer knows goals updated (replacement semantics)
    # POST-S2: Buyer sees affected packages
    # POST-S4: Unambiguous success
    # POST-S5: Request completed

  @T-UC-003-alt-keyword-ops @alt-flow @keyword-ops @post-s1 @post-s2 @post-s4 @post-s5
  Scenario: Add keyword targets incrementally
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes keyword_targets_add:
    And the package "pkg_001" exists in the media buy
    And no targeting_overlay.keyword_targets is present in the same package update
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the response should contain affected_packages including "pkg_001"
    # POST-S1: Buyer knows keywords added
    # POST-S2: Buyer sees affected packages
    # POST-S4: Unambiguous success
    # POST-S5: Request completed

  @T-UC-003-alt-keyword-remove @alt-flow @keyword-ops @post-s1 @post-s2
  Scenario: Remove keyword targets incrementally
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes keyword_targets_remove:
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    # POST-S1: Buyer knows keywords removed
    # POST-S2: Buyer sees affected packages

  @T-UC-003-alt-negative-keywords @alt-flow @keyword-ops @post-s1
  Scenario: Add and remove negative keywords incrementally
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes negative_keywords_add:
    And the package update includes negative_keywords_remove:
    And no targeting_overlay.negative_keywords is present in the same package update
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    # POST-S1: Buyer knows negative keywords updated

  @T-UC-003-alt-manual @alt-flow @manual-approval @post-s7 @post-s8
  Scenario: Update requires manual approval -- pending state
    Given the tenant is configured for manual approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 50000   |
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "submitted"
    And the response should contain media_buy_id "mb_existing"
    And the response should contain implementation_date that is null
    # POST-S7: Buyer knows update awaiting seller approval (status "submitted")
    # POST-S8: implementation_date is null (pending approval)

  @T-UC-003-partial-update @invariant @BR-RULE-022
  Scenario: Partial update -- omitted fields remain unchanged
    Given the tenant is configured for auto-approval
    And the existing media buy has start_time "2026-04-01T00:00:00Z" and end_time "2026-06-30T23:59:59Z"
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 8000    |
    And the package "pkg_001" exists in the media buy
    And the request does NOT include start_time, end_time, or paused fields
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the existing start_time and end_time should remain unchanged
    # BR-RULE-022 INV-1: Field present → updated
    # BR-RULE-022 INV-2: Field omitted → unchanged

  @T-UC-003-empty-update @invariant @BR-RULE-022 @error
  Scenario: Empty update -- no updatable fields specified
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request does not include any updatable fields
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "at least one updatable field"
    # BR-RULE-022 INV-3: No updatable fields → rejected
    # POST-F1: System state unchanged
    # POST-F2: Error code explains failure
    # POST-F3: Suggestion for recovery

  @T-UC-003-idempotency-valid @invariant @BR-RULE-081
  Scenario: Idempotency key -- valid key accepted
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field           | value                                |
    | media_buy_id    | mb_existing                          |
    | idempotency_key | 550e8400-e29b-41d4-a716-446655440000 |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    # BR-RULE-081 INV-2: Key 8-255 chars accepted

  @T-UC-003-idempotency-absent @invariant @BR-RULE-081
  Scenario: Idempotency key -- absent, proceeds without protection
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request does NOT include an idempotency_key
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    # BR-RULE-081 INV-1: Key absent → proceeds without idempotency

  @T-UC-003-account-absent @invariant @schema-v3.1
  Scenario: Account -- absent is now rejected (v3.1 required)
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request does NOT include an account field
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # v3.1: root required-set is [idempotency_key, account, media_buy_id]; account is required
    #       for governance checks and account resolution (PRE-BIZ16, main steps 2 + 3a)

  @T-UC-003-atomic-success @invariant @BR-RULE-018
  Scenario: Atomic response -- success has no errors field
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response should contain media_buy_id
    And the response should NOT contain an "errors" field
    # BR-RULE-018 INV-1: Success → no errors field

  @T-UC-003-atomic-error @invariant @BR-RULE-018
  Scenario: Atomic response -- error has no success fields
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | budget       | -100        |
    When the Buyer Agent sends the update_media_buy request
    Then the response should contain an "errors" array
    And the response should NOT contain "media_buy_id" field
    And the response should NOT contain "buyer_ref" field
    And the response should NOT contain "affected_packages" field
    # BR-RULE-018 INV-2: Error → no success fields

  @T-UC-003-approval-auto @invariant @BR-RULE-017
  Scenario: Approval workflow -- both flags false, auto-approved
    Given the tenant human_review_required is false
    And the adapter manual_approval_required is false
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    # BR-RULE-017 INV-1: Both false → auto-approved

  @T-UC-003-approval-tenant @invariant @BR-RULE-017
  Scenario: Approval workflow -- tenant requires manual review
    Given the tenant human_review_required is true
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "submitted"
    And the response should contain implementation_date that is null
    # BR-RULE-017 INV-2: Tenant flag true → manual approval

  @T-UC-003-approval-adapter @invariant @BR-RULE-017
  Scenario: Approval workflow -- adapter requires manual review
    Given the tenant human_review_required is false
    And the adapter manual_approval_required is true
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "submitted"
    And the response should contain implementation_date that is null
    # BR-RULE-017 INV-3: Adapter flag true → manual approval

  @T-UC-003-adapter-success @invariant @BR-RULE-020
  Scenario: Adapter atomicity -- success persists changes
    Given the tenant is configured for auto-approval
    And the ad server adapter returns success
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the package budget should be persisted as 5000
    # BR-RULE-020 INV-1: Adapter success → changes persisted

  @T-UC-003-creative-replace @invariant @BR-RULE-024
  Scenario: Creative replacement -- new set replaces existing
    Given the tenant is configured for auto-approval
    And the package "pkg_001" has existing creative assignments [cr_old_1, cr_old_2]
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes creative_assignments with:
    | creative_id | weight |
    | cr_new_1    | 70     |
    | cr_new_2    | 30     |
    And all referenced creatives are valid
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the package "pkg_001" should have creative assignments [cr_new_1, cr_new_2]
    And the old assignments [cr_old_1, cr_old_2] should be removed
    # BR-RULE-024 INV-2: creative_assignments provided → replaces all existing

  @T-UC-003-ext-a @extension @ext-a @error @post-f1 @post-f2 @post-f3
  Scenario: Authentication error -- no principal in context
    Given the Buyer has no authentication credentials
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "authentication_error"
    And the error message should contain "authentication"
    And the error should include "suggestion" field
    And the suggestion should contain "valid credentials"
    # POST-F1: System state unchanged
    # POST-F2: Error explains authentication failed
    # POST-F3: Suggestion to obtain valid credentials

  @T-UC-003-ext-a-unknown @extension @ext-a @error @post-f1 @post-f2 @post-f3
  Scenario: Authentication error -- principal not found in database
    Given the Buyer is authenticated as principal "unknown_principal"
    And the principal "unknown_principal" does not exist in the database
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "authentication_error"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: Error explains principal not found
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-b @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Media buy not found -- by media_buy_id
    Given a valid update_media_buy request with:
    | field        | value         |
    | media_buy_id | mb_nonexistent |
    And no media buy exists with media_buy_id "mb_nonexistent"
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "PRODUCT_NOT_FOUND"
    And the error should include "suggestion" field
    And the suggestion should contain "verify"
    # POST-F1: System state unchanged
    # POST-F2: Error explains media buy not found
    # POST-F3: Suggestion to verify ID

  @T-UC-003-ext-c @extension @ext-c @error @post-f1 @post-f2 @post-f3
  Scenario: Ownership mismatch -- principal does not own media buy
    Given the Buyer is authenticated as principal "principal_other"
    And the media buy "mb_existing" is owned by principal "principal_owner"
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "ACCOUNT_NOT_FOUND"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: Error explains ownership mismatch
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-d @extension @ext-d @error @post-f1 @post-f2 @post-f3
  Scenario: Budget validation -- campaign budget zero
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | budget       | 0           |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "BUDGET_TOO_LOW"
    And the error should include "recovery" field with value "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "positive"
    # POST-F1: System state unchanged
    # POST-F2: Error code BUDGET_TOO_LOW
    # POST-F3: Suggestion to provide positive budget

  @T-UC-003-ext-d-negative @extension @ext-d @error @post-f1 @post-f2 @post-f3
  Scenario: Budget validation -- campaign budget negative
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | budget       | -500        |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "BUDGET_TOO_LOW"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: Error code BUDGET_TOO_LOW
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-e @extension @ext-e @error @post-f1 @post-f2 @post-f3
  Scenario: Date range invalid -- end_time before start_time
    Given the existing media buy has start_time "2026-04-01T00:00:00Z"
    And a valid update_media_buy request with:
    | field        | value                    |
    | media_buy_id | mb_existing              |
    | end_time     | 2026-03-15T00:00:00Z     |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "end_time must be after start_time"
    # POST-F1: System state unchanged
    # POST-F2: Error explains date range invalid
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-e-equal @extension @ext-e @error @post-f1 @post-f2 @post-f3
  Scenario: Date range invalid -- end_time equals start_time
    Given the existing media buy has start_time "2026-04-01T00:00:00Z"
    And a valid update_media_buy request with:
    | field        | value                    |
    | media_buy_id | mb_existing              |
    | end_time     | 2026-04-01T00:00:00Z     |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-013 INV-3: end_time <= start_time rejected
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-f @extension @ext-f @error @post-f1 @post-f2 @post-f3
  Scenario: Currency not supported -- tenant does not support media buy currency
    Given the existing media buy uses currency "JPY"
    And the tenant does not have "JPY" in CurrencyLimit table
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "currency"
    # POST-F1: System state unchanged
    # POST-F2: Error explains unsupported currency
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-g @extension @ext-g @error @post-f1 @post-f2 @post-f3
  Scenario: Daily spend cap exceeded -- updated budget exceeds daily max
    Given the tenant has max_daily_package_spend of 1000
    And the media buy flight is 10 days
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 50000   |
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "BUDGET_TOO_LOW"
    And the error should include "recovery" field with value "correctable"
    And the error should include "suggestion" field
    # BR-RULE-012 INV-2: daily budget > cap → rejected
    # POST-F1: System state unchanged
    # POST-F2: Error explains daily cap exceeded
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-h @extension @ext-h @error @post-f1 @post-f2 @post-f3
  Scenario: Missing package ID -- package update without identifier
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update without package_id or buyer_ref
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "package_id"
    # POST-F1: System state unchanged
    # POST-F2: Error explains missing package identifier
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-i @extension @ext-i @error @post-f1 @post-f2 @post-f3
  Scenario: Creative not found -- referenced creative_id not in library
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes creative_assignments with:
    | creative_id |
    | cr_missing  |
    And creative "cr_missing" does not exist in the creative library
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "CREATIVE_REJECTED"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: Error explains creative not found
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-j-error @extension @ext-j @error @post-f1 @post-f2 @post-f3
  Scenario: Creative validation -- creative in error state
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes creative_assignments with:
    | creative_id |
    | cr_error    |
    And creative "cr_error" is in "error" state
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "CREATIVE_REJECTED"
    And the error should include "suggestion" field
    # BR-RULE-026 INV-2: creative in error state → rejected
    # POST-F1: System state unchanged
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-j-rejected @extension @ext-j @error @post-f1 @post-f2 @post-f3
  Scenario: Creative validation -- creative in rejected state
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes creative_assignments with:
    | creative_id |
    | cr_rejected |
    And creative "cr_rejected" is in "rejected" state
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "CREATIVE_REJECTED"
    And the error should include "suggestion" field
    # BR-RULE-026 INV-3: creative in rejected state → rejected
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-j-format @extension @ext-j @error @post-f1 @post-f2 @post-f3
  Scenario: Creative validation -- format incompatible with product
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes creative_assignments with:
    | creative_id |
    | cr_wrong_fmt |
    And creative "cr_wrong_fmt" has a format incompatible with package product
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "CREATIVE_REJECTED"
    And the error should include "suggestion" field
    # BR-RULE-026 INV-4: format mismatch → rejected
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-k @extension @ext-k @error @post-f1 @post-f2 @post-f3
  Scenario: Creative sync failure -- inline creative upload fails
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with inline creatives
    And the creative upload/sync process fails
    And the package exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    # POST-F1: System state unchanged
    # POST-F2: Error explains sync failure
    # POST-F3: Suggestion to retry

  @T-UC-003-ext-l @extension @ext-l @error @post-f1 @post-f2 @post-f3
  Scenario: Package not found -- package_id not in media buy
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value          |
    | package_id | pkg_nonexistent |
    | budget     | 5000           |
    And package "pkg_nonexistent" does not exist in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "package"
    # POST-F1: System state unchanged
    # POST-F2: Error explains package not found
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-m @extension @ext-m @error @post-f1 @post-f2 @post-f3
  Scenario: Invalid placement IDs -- placement not valid for product
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes creative_assignments with:
    | creative_id | placement_ids    |
    | cr_001      | plc_nonexistent  |
    And placement "plc_nonexistent" is not valid for the package product
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "invalid_placement_ids"
    And the error should include "suggestion" field
    # BR-RULE-028 INV-2: invalid placement_id → rejected
    # POST-F1: System state unchanged
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-m-unsupported @extension @ext-m @error @post-f1 @post-f2 @post-f3
  Scenario: Invalid placement IDs -- product does not support placement targeting
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes creative_assignments with:
    | creative_id | placement_ids |
    | cr_001      | plc_any       |
    And the package product does not support placement-level targeting
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "invalid_placement_ids"
    And the error should include "suggestion" field
    # BR-RULE-028 INV-3: product doesn't support placement targeting → rejected
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-n @extension @ext-n @error @post-f1 @post-f2 @post-f3
  Scenario: Insufficient privileges -- admin-only action without admin role
    Given the Buyer does not have admin privileges
    And the update operation requires admin privileges
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error should include "suggestion" field
    And the suggestion should contain "privileges"
    # POST-F1: System state unchanged
    # POST-F2: Error explains insufficient privileges
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-p-short @extension @ext-p @error @post-f1 @post-f2 @post-f3
  Scenario: Idempotency key too short -- below 8 characters
    Given a valid update_media_buy request with:
    | field           | value       |
    | media_buy_id    | mb_existing |
    | idempotency_key | abc1234     |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "at least 8 characters"
    # BR-RULE-081 INV-3: key < 8 chars → rejected
    # POST-F1: System state unchanged
    # POST-F2: Error explains key too short
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-p-long @extension @ext-p @error @post-f1 @post-f2 @post-f3
  Scenario: Idempotency key too long -- above 255 characters
    Given a valid update_media_buy request with:
    | field           | value                    |
    | media_buy_id    | mb_existing              |
    | idempotency_key | <256 character string>   |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "255 characters"
    # BR-RULE-081 INV-4: key > 255 chars → rejected
    # POST-F1: System state unchanged
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-r-keyword @extension @ext-r @error @post-f1 @post-f2 @post-f3
  Scenario: Keyword operation conflict -- keyword_targets_add with targeting_overlay.keyword_targets
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes keyword_targets_add and targeting_overlay.keyword_targets
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "incremental operations" or "full replacement"
    # BR-RULE-083 INV-1: keyword_targets_add + overlay.keyword_targets → rejected
    # POST-F1: System state unchanged
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-r-negative @extension @ext-r @error @post-f1 @post-f2 @post-f3
  Scenario: Keyword operation conflict -- negative_keywords_add with targeting_overlay.negative_keywords
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes negative_keywords_add and targeting_overlay.negative_keywords
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-083 INV-2: negative_keywords_add + overlay.negative_keywords → rejected
    # POST-F1: System state unchanged
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-r-cross-ok @invariant @BR-RULE-083
  Scenario: Keyword operations -- cross-dimension mixing is valid
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes keyword_targets_add AND targeting_overlay.negative_keywords
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    # BR-RULE-083 INV-3: cross-dimension mixing (keyword_targets_add + overlay.negative_keywords) → accepted

  @T-UC-003-ext-r-cross-ok-2 @invariant @BR-RULE-083
  Scenario: Keyword operations -- negative_keywords_add with targeting_overlay.keyword_targets is valid
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes negative_keywords_add AND targeting_overlay.keyword_targets
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    # BR-RULE-083 INV-4: cross-dimension mixing (negative_keywords_add + overlay.keyword_targets) → accepted

  @T-UC-003-partition-idempotency-key @partition @idempotency_key
  Scenario Outline: Idempotency key partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    And the idempotency_key is set to <value>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition      | value                                  | outcome |
      | absent         | <not provided>                         | success |
      | typical_valid  | abc12345-retry-001                     | success |
      | boundary_min   | 12345678                               | success |
      | boundary_max   | <255 character string>                 | success |
      | uuid_format    | 550e8400-e29b-41d4-a716-446655440000   | success |

    Examples: Invalid partitions
      | partition      | value          | outcome                                              |
      | empty_string   |                | error "INVALID_REQUEST" with suggestion               |
      | too_short      | abc1234        | error "INVALID_REQUEST" with suggestion               |
      | too_long       | <256 chars>    | error "INVALID_REQUEST" with suggestion               |

  @T-UC-003-boundary-idempotency-key @boundary @idempotency_key
  Scenario Outline: Idempotency key boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    And the idempotency_key is set to <value>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                  | value               | outcome                                |
      | absent (field not provided)     | <not provided>      | success                                |
      | empty string (length 0)         |                     | error "INVALID_REQUEST" with suggestion |
      | length 7 (min - 1)             | abc1234             | error "INVALID_REQUEST" with suggestion |
      | length 8 (min, inclusive)       | 12345678            | success                                |
      | length 9 (min + 1)             | 123456789           | success                                |
      | length 254 (max - 1)           | <254 char string>   | success                                |
      | length 255 (max, inclusive)     | <255 char string>   | success                                |
      | length 256 (max + 1)           | <256 char string>   | error "INVALID_REQUEST" with suggestion |

  @T-UC-003-partition-media-buy-status @partition @media_buy_status
  Scenario Outline: Media buy status partition validation - <partition>
    Given the media buy is in "<status>" status
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition                  | status               | outcome |
      | status_pending_activation  | pending_activation   | success |
      | status_active              | active               | success |
      | status_paused              | paused               | success |

    Examples: Invalid partitions
      | partition            | status     | outcome                                      |
      | terminal_rejected    | rejected   | error "INVALID_STATUS" with suggestion        |
      | terminal_canceled    | canceled   | error "INVALID_STATUS" with suggestion        |
      | terminal_completed   | completed  | error "INVALID_STATUS" with suggestion        |

  @T-UC-003-boundary-media-buy-status @boundary @media_buy_status
  Scenario Outline: Media buy status boundary validation - <boundary_point>
    Given the media buy is in "<status>" status
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                            | status               | outcome                                  |
      | pending_activation (non-terminal, updatable) | pending_activation | success                                  |
      | active (non-terminal, updatable)          | active               | success                                  |
      | paused (non-terminal, updatable)          | paused               | success                                  |
      | rejected (terminal, update blocked)       | rejected             | error "INVALID_STATUS" with suggestion   |
      | canceled (terminal, update blocked)       | canceled             | error "INVALID_STATUS" with suggestion   |
      | completed (terminal, update blocked)      | completed            | error "INVALID_STATUS" with suggestion   |

  @T-UC-003-partition-budget-amount @partition @budget_amount
  Scenario Outline: Budget amount partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value    |
    | package_id | pkg_001  |
    | budget     | <amount> |
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    And the updated daily spend does not exceed max_daily_package_spend
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition       | amount | outcome |
      | positive_amount | 100.00 | success |

    Examples: Invalid partitions
      | partition       | amount | outcome                                      |
      | zero_amount     | 0      | error "BUDGET_TOO_LOW" with suggestion        |
      | negative_amount | -50    | error "BUDGET_TOO_LOW" with suggestion        |

  @T-UC-003-boundary-budget-amount @boundary @budget_amount
  Scenario Outline: Budget amount boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value    |
    | package_id | pkg_001  |
    | budget     | <amount> |
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                  | amount | outcome                                      |
      | amount = 0 (rejected by rule)   | 0      | error "BUDGET_TOO_LOW" with suggestion        |
      | amount = 0.01 (minimum positive) | 0.01  | success                                      |
      | amount negative                 | -1     | error "BUDGET_TOO_LOW" with suggestion        |

  @T-UC-003-partition-daily-spend-cap @partition @daily_spend_cap
  Scenario Outline: Daily spend cap partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value    |
    | package_id | pkg_001  |
    | budget     | <budget> |
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    And the tenant max_daily_package_spend is <cap_config>
    And the media buy flight duration is <flight_days> days
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition          | budget | cap_config | flight_days | outcome |
      | below_cap          | 5000   | 1000       | 10          | success |
      | cap_not_configured | 5000   | not set    | 10          | success |
      | at_cap_exactly     | 10000  | 1000       | 10          | success |

    Examples: Invalid partitions
      | partition    | budget | cap_config | flight_days | outcome                                      |
      | exceeds_cap  | 50000  | 1000       | 10          | error "BUDGET_TOO_LOW" with suggestion        |

  @T-UC-003-boundary-daily-spend-cap @boundary @daily_spend_cap
  Scenario Outline: Daily spend cap boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value    |
    | package_id | pkg_001  |
    | budget     | <budget> |
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    And the tenant max_daily_package_spend is <cap_config>
    And the media buy flight duration is <flight_days> days
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                       | budget | cap_config | flight_days | outcome                                      |
      | daily budget = cap (at limit)        | 10000  | 1000       | 10          | success                                      |
      | daily budget > cap (exceeds)         | 10001  | 1000       | 10          | error "BUDGET_TOO_LOW" with suggestion        |
      | cap not configured (skipped)         | 99999  | not set    | 10          | success                                      |
      | flight duration 0 days (floor to 1)  | 500    | 1000       | 0           | success                                      |

  @T-UC-003-partition-media-buy-identification @partition @media_buy_identification
  Scenario Outline: Media buy identification partition validation - <partition>
    Given a valid update_media_buy request with identification: <id_config>
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition          | id_config                        | outcome |
      | media_buy_id_only  | media_buy_id=mb_existing         | success |
      | buyer_ref_only     | buyer_ref=my_ref_01              | success |

    Examples: Invalid partitions
      | partition       | id_config                                   | outcome                                      |
      | both_provided   | media_buy_id=mb_existing,buyer_ref=my_ref_01 | error "INVALID_REQUEST" with suggestion       |
      | neither_provided | <none>                                      | error "INVALID_REQUEST" with suggestion       |

  @T-UC-003-boundary-media-buy-identification @boundary @media_buy_identification
  Scenario Outline: Media buy identification boundary validation - <boundary_point>
    Given a valid update_media_buy request with identification: <id_config>
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                       | id_config                                   | outcome                                  |
      | media_buy_id only (primary path)     | media_buy_id=mb_existing                    | success                                  |
      | buyer_ref only (fallback path)       | buyer_ref=my_ref_01                         | success                                  |
      | both identifiers (ambiguous)         | media_buy_id=mb_existing,buyer_ref=my_ref_01 | error "INVALID_REQUEST" with suggestion  |
      | neither identifier (missing)         | <none>                                       | error "INVALID_REQUEST" with suggestion  |

  @T-UC-003-partition-frequency-cap-suppress @partition @frequency_cap_suppress
  Scenario Outline: Frequency cap suppress partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field             | value   |
    | package_id        | pkg_001 |
    And the package targeting_overlay includes frequency_cap with suppress: <suppress_value>
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition                     | suppress_value                                                                                                                | outcome |
      | typical_minutes               | {"interval": 60, "unit": "minutes"}                                                                                          | success |
      | typical_hours                 | {"interval": 24, "unit": "hours"}                                                                                            | success |
      | typical_days                  | {"interval": 7, "unit": "days"}                                                                                              | success |
      | campaign_full_flight          | {"interval": 1, "unit": "campaign"}                                                                                          | success |
      | boundary_min_interval         | {"interval": 1, "unit": "minutes"}                                                                                           | success |
      | suppress_with_max_impressions | {"suppress": {"interval": 60, "unit": "minutes"}, "max_impressions": 3, "per": "devices", "window": {"interval": 1, "unit": "days"}} | success |
      | deprecated_suppress_minutes   | {"suppress_minutes": 60}                                                                                                     | success |
      | suppress_only                 | {"suppress": {"interval": 30, "unit": "minutes"}}                                                                            | success |
      | suppress_minutes_zero         | {"suppress_minutes": 0}                                                                                                      | success |

    Examples: Invalid partitions
      | partition                    | suppress_value                         | outcome                                      |
      | interval_zero                | {"interval": 0, "unit": "minutes"}     | error with suggestion                        |
      | interval_negative            | {"interval": -1, "unit": "minutes"}    | error with suggestion                        |
      | campaign_interval_not_one    | {"interval": 5, "unit": "campaign"}    | error with suggestion                        |
      | unknown_unit                 | {"interval": 7, "unit": "weeks"}       | error with suggestion                        |
      | missing_interval             | {"unit": "minutes"}                    | error with suggestion                        |
      | missing_unit                 | {"interval": 5}                        | error with suggestion                        |
      | no_frequency_control         | {}                                     | error with suggestion                        |
      | wrong_type_suppress          | "60"                                   | error with suggestion                        |
      | suppress_minutes_negative    | {"suppress_minutes": -1}               | error with suggestion                        |

  @T-UC-003-boundary-frequency-cap-suppress @boundary @frequency_cap_suppress
  Scenario Outline: Frequency cap suppress boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field             | value   |
    | package_id        | pkg_001 |
    And the package targeting_overlay includes frequency_cap with suppress: <suppress_value>
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                                    | suppress_value                                                                                                                | outcome                  |
      | interval = 0 (below minimum)                                     | {"interval": 0, "unit": "minutes"}                                                                                           | error with suggestion    |
      | interval = 1 (boundary minimum)                                  | {"interval": 1, "unit": "minutes"}                                                                                           | success                  |
      | interval = -1 (negative)                                         | {"interval": -1, "unit": "minutes"}                                                                                          | error with suggestion    |
      | unit=campaign, interval=1                                        | {"interval": 1, "unit": "campaign"}                                                                                          | success                  |
      | unit=campaign, interval=2                                        | {"interval": 2, "unit": "campaign"}                                                                                          | error with suggestion    |
      | unit = 'minutes'                                                 | {"interval": 60, "unit": "minutes"}                                                                                          | success                  |
      | unit = 'hours'                                                   | {"interval": 24, "unit": "hours"}                                                                                            | success                  |
      | unit = 'days'                                                    | {"interval": 7, "unit": "days"}                                                                                              | success                  |
      | unit = 'campaign'                                                | {"interval": 1, "unit": "campaign"}                                                                                          | success                  |
      | unit = 'weeks' (unknown)                                         | {"interval": 7, "unit": "weeks"}                                                                                             | error with suggestion    |
      | suppress present, suppress_minutes absent, max_impressions absent | {"suppress": {"interval": 30, "unit": "minutes"}}                                                                           | success                  |
      | suppress absent, suppress_minutes=60                             | {"suppress_minutes": 60}                                                                                                     | success                  |
      | all three absent (empty frequency_cap)                           | {}                                                                                                                            | error with suggestion    |
      | suppress + max_impressions both present (AND semantics)          | {"suppress": {"interval": 60, "unit": "minutes"}, "max_impressions": 3, "per": "devices", "window": {"interval": 1, "unit": "days"}} | success        |
      | suppress_minutes = -1 (below minimum 0)                          | {"suppress_minutes": -1}                                                                                                     | error with suggestion    |
      | suppress_minutes = 0 (boundary minimum)                          | {"suppress_minutes": 0}                                                                                                      | success                  |
      | suppress is string '60' (wrong type)                             | "60"                                                                                                                          | error with suggestion    |
      | suppress = {} (missing both fields)                              | {"suppress": {}}                                                                                                              | error with suggestion    |

  @T-UC-003-partition-optimization-goals @partition @optimization_goals
  Scenario Outline: Optimization goals partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes optimization_goals: <goals_value>
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition                        | goals_value                                                                                                                                                             | outcome |
      | single_metric_goal               | [{"kind": "metric", "metric": "clicks"}]                                                                                                                                | success |
      | single_event_goal                | [{"kind": "event", "event_sources": [{"event_source_id": "pixel-1", "event_type": "purchase"}]}]                                                                        | success |
      | multiple_mixed_goals             | [{"kind": "metric", "metric": "clicks", "priority": 1}, {"kind": "event", "event_sources": [{"event_source_id": "px", "event_type": "purchase"}], "priority": 2}]      | success |
      | metric_with_cost_per_target      | [{"kind": "metric", "metric": "clicks", "target": {"kind": "cost_per", "value": 0.50}}]                                                                                 | success |
      | metric_with_threshold_rate       | [{"kind": "metric", "metric": "views", "target": {"kind": "threshold_rate", "value": 0.70}}]                                                                            | success |
      | event_with_roas_target           | [{"kind": "event", "event_sources": [{"event_source_id": "px", "event_type": "purchase", "value_field": "value"}], "target": {"kind": "per_ad_spend", "value": 4.0}}]   | success |
      | event_with_maximize_value        | [{"kind": "event", "event_sources": [{"event_source_id": "px", "event_type": "purchase", "value_field": "value"}], "target": {"kind": "maximize_value"}}]                | success |
      | reach_with_frequency             | [{"kind": "metric", "metric": "reach", "reach_unit": "individuals", "target_frequency": {"min": 1, "max": 3, "window": {"interval": 7, "unit": "days"}}}]              | success |
      | completed_views_with_duration    | [{"kind": "metric", "metric": "completed_views", "view_duration_seconds": 15}]                                                                                          | success |
      | event_with_attribution_window    | [{"kind": "event", "event_sources": [{"event_source_id": "px", "event_type": "purchase"}], "attribution_window": {"post_click": {"interval": 7, "unit": "days"}, "post_view": {"interval": 1, "unit": "days"}}}] | success |
      | event_multi_source_dedup         | [{"kind": "event", "event_sources": [{"event_source_id": "pixel", "event_type": "purchase", "value_field": "value"}, {"event_source_id": "api", "event_type": "purchase", "value_field": "order_total", "value_factor": 0.01}]}] | success |
      | goals_with_explicit_priorities   | [{"kind": "metric", "metric": "clicks", "priority": 1}, {"kind": "metric", "metric": "views", "priority": 2}]                                                          | success |
      | refund_event_with_negative_factor | [{"kind": "event", "event_sources": [{"event_source_id": "px", "event_type": "refund", "value_field": "value", "value_factor": -1}]}]                                   | success |

    Examples: Invalid partitions
      | partition              | goals_value                                                                                                        | outcome                                      |
      | empty_array            | []                                                                                                                  | error with suggestion                        |
      | missing_kind           | [{"metric": "clicks"}]                                                                                              | error with suggestion                        |
      | invalid_kind           | [{"kind": "custom"}]                                                                                                | error with suggestion                        |
      | metric_missing_metric  | [{"kind": "metric"}]                                                                                                | error with suggestion                        |
      | invalid_metric_enum    | [{"kind": "metric", "metric": "conversions"}]                                                                       | error with suggestion                        |
      | event_missing_sources  | [{"kind": "event"}]                                                                                                 | error with suggestion                        |
      | event_sources_empty    | [{"kind": "event", "event_sources": []}]                                                                            | error with suggestion                        |
      | target_value_zero      | [{"kind": "metric", "metric": "clicks", "target": {"kind": "cost_per", "value": 0}}]                                | error with suggestion                        |
      | target_value_negative  | [{"kind": "metric", "metric": "clicks", "target": {"kind": "cost_per", "value": -1.0}}]                             | error with suggestion                        |
      | priority_zero          | [{"kind": "metric", "metric": "clicks", "priority": 0}]                                                             | error with suggestion                        |
      | priority_negative      | [{"kind": "metric", "metric": "clicks", "priority": -1}]                                                            | error with suggestion                        |
      | view_duration_zero     | [{"kind": "metric", "metric": "completed_views", "view_duration_seconds": 0}]                                       | error with suggestion                        |
      | event_source_id_empty  | [{"kind": "event", "event_sources": [{"event_source_id": "", "event_type": "purchase"}]}]                            | error with suggestion                        |

  @T-UC-003-boundary-optimization-goals @boundary @optimization_goals
  Scenario Outline: Optimization goals boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes optimization_goals: <goals_value>
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                          | goals_value                                                                                                                                                                         | outcome                  |
      | empty array (0 items)                                   | []                                                                                                                                                                                   | error with suggestion    |
      | single goal (1 item — boundary min)                     | [{"kind": "metric", "metric": "clicks"}]                                                                                                                                            | success                  |
      | many goals (no maxItems)                                | [{"kind": "metric", "metric": "clicks", "priority": 1}, {"kind": "event", "event_sources": [{"event_source_id": "px", "event_type": "purchase"}], "priority": 2}]                   | success                  |
      | kind = 'metric'                                         | [{"kind": "metric", "metric": "clicks"}]                                                                                                                                            | success                  |
      | kind = 'event'                                          | [{"kind": "event", "event_sources": [{"event_source_id": "pixel-1", "event_type": "purchase"}]}]                                                                                    | success                  |
      | kind omitted                                            | [{"metric": "clicks"}]                                                                                                                                                               | error with suggestion    |
      | kind = unknown string                                   | [{"kind": "custom"}]                                                                                                                                                                 | error with suggestion    |
      | priority = 0                                            | [{"kind": "metric", "metric": "clicks", "priority": 0}]                                                                                                                             | error with suggestion    |
      | priority = 1 (boundary min)                             | [{"kind": "metric", "metric": "clicks", "priority": 1}, {"kind": "metric", "metric": "views", "priority": 2}]                                                                      | success                  |
      | priority omitted                                        | [{"kind": "metric", "metric": "clicks"}]                                                                                                                                            | success                  |
      | duplicate priorities (same value on two goals)          | [{"kind": "metric", "metric": "clicks", "priority": 1}, {"kind": "metric", "metric": "views", "priority": 1}]                                                                      | success                  |
      | target.value = 0 (exclusive minimum)                    | [{"kind": "metric", "metric": "clicks", "target": {"kind": "cost_per", "value": 0}}]                                                                                                | error with suggestion    |
      | target.value = 0.001 (just above zero)                  | [{"kind": "metric", "metric": "clicks", "target": {"kind": "cost_per", "value": 0.001}}]                                                                                            | success                  |
      | target omitted                                          | [{"kind": "metric", "metric": "clicks"}]                                                                                                                                            | success                  |
      | event_sources empty array                               | [{"kind": "event", "event_sources": []}]                                                                                                                                             | error with suggestion    |
      | event_sources single entry (boundary min)               | [{"kind": "event", "event_sources": [{"event_source_id": "pixel-1", "event_type": "purchase"}]}]                                                                                    | success                  |
      | event_sources multiple entries                          | [{"kind": "event", "event_sources": [{"event_source_id": "pixel", "event_type": "purchase", "value_field": "value"}, {"event_source_id": "api", "event_type": "purchase", "value_field": "order_total", "value_factor": 0.01}]}] | success |
      | view_duration_seconds = 0                               | [{"kind": "metric", "metric": "completed_views", "view_duration_seconds": 0}]                                                                                                       | error with suggestion    |
      | view_duration_seconds = 0.001 (just above zero)         | [{"kind": "metric", "metric": "completed_views", "view_duration_seconds": 0.001}]                                                                                                   | success                  |
      | optimization_goals present — replaces all               | [{"kind": "metric", "metric": "clicks"}]                                                                                                                                            | success                  |
      | optimization_goals omitted — preserves existing         | <not provided>                                                                                                                                                                       | success                  |

  @T-UC-003-partition-keyword-targets-add @partition @keyword_targets_add
  Scenario Outline: Keyword targets add partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes keyword_targets_add: <kw_value>
    And no targeting_overlay.keyword_targets is present in the same package update
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition              | kw_value                                                                                                                                | outcome |
      | typical_add            | [{"keyword": "running shoes", "match_type": "broad"}, {"keyword": "nike sneakers", "match_type": "exact"}]                              | success |
      | boundary_min_array     | [{"keyword": "laptop", "match_type": "phrase"}]                                                                                         | success |
      | boundary_min_keyword   | [{"keyword": "a", "match_type": "broad"}]                                                                                               | success |
      | add_with_bid_price     | [{"keyword": "shoes", "match_type": "exact", "bid_price": 2.50}]                                                                        | success |
      | add_without_bid_price  | [{"keyword": "shoes", "match_type": "broad"}]                                                                                           | success |
      | upsert_existing        | [{"keyword": "shoes", "match_type": "exact", "bid_price": 3.00}]                                                                        | success |
      | zero_bid_price         | [{"keyword": "shoes", "match_type": "broad", "bid_price": 0}]                                                                           | success |
      | all_match_types        | [{"keyword": "shoes", "match_type": "broad"}, {"keyword": "shoes", "match_type": "phrase"}, {"keyword": "shoes", "match_type": "exact"}] | success |
      | cross_dimension_valid  | [{"keyword": "shoes", "match_type": "broad"}]                                                                                           | success |

    Examples: Invalid partitions
      | partition              | kw_value                                                        | outcome                  |
      | empty_array            | []                                                               | error with suggestion    |
      | empty_keyword          | [{"keyword": "", "match_type": "broad"}]                         | error with suggestion    |
      | missing_keyword        | [{"match_type": "broad"}]                                        | error with suggestion    |
      | missing_match_type     | [{"keyword": "shoes"}]                                           | error with suggestion    |
      | invalid_match_type     | [{"keyword": "shoes", "match_type": "fuzzy"}]                    | error with suggestion    |
      | negative_bid_price     | [{"keyword": "shoes", "match_type": "exact", "bid_price": -1.00}] | error with suggestion   |
      | conflict_with_overlay  | <with targeting_overlay.keyword_targets present>                  | error with suggestion    |

  @T-UC-003-boundary-keyword-targets-add @boundary @keyword_targets_add
  Scenario Outline: Keyword targets add boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes keyword_targets_add: <kw_value>
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                                            | kw_value                                                                     | outcome                  |
      | array length 0 (empty)                                                    | []                                                                            | error with suggestion    |
      | array length 1 (minimum valid)                                            | [{"keyword": "laptop", "match_type": "phrase"}]                               | success                  |
      | keyword length 0 (empty string)                                           | [{"keyword": "", "match_type": "broad"}]                                      | error with suggestion    |
      | keyword length 1 (single char)                                            | [{"keyword": "a", "match_type": "broad"}]                                     | success                  |
      | match_type = 'broad'                                                      | [{"keyword": "shoes", "match_type": "broad"}]                                 | success                  |
      | match_type = 'phrase'                                                     | [{"keyword": "shoes", "match_type": "phrase"}]                                | success                  |
      | match_type = 'exact'                                                      | [{"keyword": "shoes", "match_type": "exact"}]                                 | success                  |
      | match_type = 'unknown'                                                    | [{"keyword": "shoes", "match_type": "unknown"}]                               | error with suggestion    |
      | bid_price = -0.01                                                         | [{"keyword": "shoes", "match_type": "exact", "bid_price": -0.01}]             | error with suggestion    |
      | bid_price = 0                                                             | [{"keyword": "shoes", "match_type": "broad", "bid_price": 0}]                 | success                  |
      | bid_price = 0.01                                                          | [{"keyword": "shoes", "match_type": "exact", "bid_price": 0.01}]              | success                  |
      | keyword_targets_add WITH targeting_overlay.keyword_targets                | <with overlay present>                                                        | error with suggestion    |
      | keyword_targets_add WITHOUT targeting_overlay.keyword_targets             | [{"keyword": "shoes", "match_type": "broad"}]                                 | success                  |
      | keyword_targets_add WITH targeting_overlay.negative_keywords (cross-dimension) | [{"keyword": "shoes", "match_type": "broad"}]                           | success                  |

  @T-UC-003-partition-keyword-targets-remove @partition @keyword_targets_remove
  Scenario Outline: Keyword targets remove partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes keyword_targets_remove: <kw_value>
    And no targeting_overlay.keyword_targets is present in the same package update
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition            | kw_value                                                                                                                                | outcome |
      | typical_remove       | [{"keyword": "running shoes", "match_type": "broad"}, {"keyword": "nike sneakers", "match_type": "exact"}]                              | success |
      | boundary_min_array   | [{"keyword": "laptop", "match_type": "phrase"}]                                                                                         | success |
      | boundary_min_keyword | [{"keyword": "a", "match_type": "broad"}]                                                                                               | success |
      | remove_nonexistent   | [{"keyword": "nonexistent", "match_type": "exact"}]                                                                                     | success |
      | all_match_types      | [{"keyword": "shoes", "match_type": "broad"}, {"keyword": "shoes", "match_type": "phrase"}, {"keyword": "shoes", "match_type": "exact"}] | success |
      | cross_dimension_valid | [{"keyword": "shoes", "match_type": "broad"}]                                                                                          | success |

    Examples: Invalid partitions
      | partition              | kw_value                                                        | outcome                  |
      | empty_array            | []                                                               | error with suggestion    |
      | empty_keyword          | [{"keyword": "", "match_type": "broad"}]                         | error with suggestion    |
      | missing_keyword        | [{"match_type": "broad"}]                                        | error with suggestion    |
      | missing_match_type     | [{"keyword": "shoes"}]                                           | error with suggestion    |
      | invalid_match_type     | [{"keyword": "shoes", "match_type": "fuzzy"}]                    | error with suggestion    |
      | conflict_with_overlay  | <with targeting_overlay.keyword_targets present>                  | error with suggestion    |

  @T-UC-003-boundary-keyword-targets-remove @boundary @keyword_targets_remove
  Scenario Outline: Keyword targets remove boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes keyword_targets_remove: <kw_value>
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                                                | kw_value                                           | outcome                  |
      | array length 0 (empty)                                                        | []                                                  | error with suggestion    |
      | array length 1 (minimum valid)                                                | [{"keyword": "laptop", "match_type": "phrase"}]     | success                  |
      | keyword length 0 (empty string)                                               | [{"keyword": "", "match_type": "broad"}]             | error with suggestion    |
      | keyword length 1 (single char)                                                | [{"keyword": "a", "match_type": "broad"}]            | success                  |
      | match_type = 'broad'                                                          | [{"keyword": "shoes", "match_type": "broad"}]        | success                  |
      | match_type = 'phrase'                                                         | [{"keyword": "shoes", "match_type": "phrase"}]       | success                  |
      | match_type = 'exact'                                                          | [{"keyword": "shoes", "match_type": "exact"}]        | success                  |
      | match_type = 'unknown'                                                        | [{"keyword": "shoes", "match_type": "unknown"}]      | error with suggestion    |
      | keyword_targets_remove WITH targeting_overlay.keyword_targets                 | <with overlay present>                               | error with suggestion    |
      | keyword_targets_remove WITHOUT targeting_overlay.keyword_targets              | [{"keyword": "shoes", "match_type": "broad"}]        | success                  |
      | remove pair that exists in current list                                       | [{"keyword": "shoes", "match_type": "broad"}]        | success                  |
      | remove pair that does NOT exist in current list (no-op)                       | [{"keyword": "nonexistent", "match_type": "exact"}]  | success                  |

  @T-UC-003-partition-negative-keywords-add @partition @negative_keywords_add
  Scenario Outline: Negative keywords add partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes negative_keywords_add: <nk_value>
    And no targeting_overlay.negative_keywords is present in the same package update
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition              | nk_value                                                                                                                                | outcome |
      | typical_add            | [{"keyword": "cheap", "match_type": "broad"}, {"keyword": "free shipping", "match_type": "phrase"}]                                     | success |
      | boundary_min_array     | [{"keyword": "discount", "match_type": "exact"}]                                                                                        | success |
      | boundary_min_keyword   | [{"keyword": "x", "match_type": "broad"}]                                                                                               | success |
      | add_duplicate          | [{"keyword": "cheap", "match_type": "broad"}]                                                                                           | success |
      | all_match_types        | [{"keyword": "free", "match_type": "broad"}, {"keyword": "free", "match_type": "phrase"}, {"keyword": "free", "match_type": "exact"}]   | success |
      | cross_dimension_valid  | [{"keyword": "cheap", "match_type": "broad"}]                                                                                           | success |

    Examples: Invalid partitions
      | partition              | nk_value                                                        | outcome                  |
      | empty_array            | []                                                               | error with suggestion    |
      | empty_keyword          | [{"keyword": "", "match_type": "broad"}]                         | error with suggestion    |
      | missing_keyword        | [{"match_type": "broad"}]                                        | error with suggestion    |
      | missing_match_type     | [{"keyword": "cheap"}]                                           | error with suggestion    |
      | invalid_match_type     | [{"keyword": "cheap", "match_type": "fuzzy"}]                    | error with suggestion    |
      | conflict_with_overlay  | <with targeting_overlay.negative_keywords present>                | error with suggestion    |

  @T-UC-003-boundary-negative-keywords-add @boundary @negative_keywords_add
  Scenario Outline: Negative keywords add boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes negative_keywords_add: <nk_value>
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                                                  | nk_value                                             | outcome                  |
      | array length 0 (empty)                                                          | []                                                    | error with suggestion    |
      | array length 1 (minimum valid)                                                  | [{"keyword": "discount", "match_type": "exact"}]      | success                  |
      | keyword length 0 (empty string)                                                 | [{"keyword": "", "match_type": "broad"}]               | error with suggestion    |
      | keyword length 1 (single char)                                                  | [{"keyword": "x", "match_type": "broad"}]              | success                  |
      | match_type = 'broad'                                                            | [{"keyword": "cheap", "match_type": "broad"}]          | success                  |
      | match_type = 'phrase'                                                           | [{"keyword": "cheap", "match_type": "phrase"}]         | success                  |
      | match_type = 'exact'                                                            | [{"keyword": "cheap", "match_type": "exact"}]          | success                  |
      | match_type = 'unknown'                                                          | [{"keyword": "cheap", "match_type": "unknown"}]        | error with suggestion    |
      | negative_keywords_add WITH targeting_overlay.negative_keywords                  | <with overlay present>                                 | error with suggestion    |
      | negative_keywords_add WITHOUT targeting_overlay.negative_keywords               | [{"keyword": "cheap", "match_type": "broad"}]          | success                  |
      | add pair that already exists in list (duplicate no-op)                          | [{"keyword": "cheap", "match_type": "broad"}]          | success                  |
      | negative_keywords_add WITH targeting_overlay.keyword_targets (cross-dimension)  | [{"keyword": "cheap", "match_type": "broad"}]          | success                  |

  @T-UC-003-partition-negative-keywords-remove @partition @negative_keywords_remove
  Scenario Outline: Negative keywords remove partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes negative_keywords_remove: <nk_value>
    And no targeting_overlay.negative_keywords is present in the same package update
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition              | nk_value                                                                                                                                | outcome |
      | typical_remove         | [{"keyword": "cheap", "match_type": "broad"}, {"keyword": "free shipping", "match_type": "phrase"}]                                     | success |
      | boundary_min_array     | [{"keyword": "discount", "match_type": "exact"}]                                                                                        | success |
      | boundary_min_keyword   | [{"keyword": "x", "match_type": "broad"}]                                                                                               | success |
      | remove_nonexistent     | [{"keyword": "nonexistent", "match_type": "exact"}]                                                                                     | success |
      | all_match_types        | [{"keyword": "free", "match_type": "broad"}, {"keyword": "free", "match_type": "phrase"}, {"keyword": "free", "match_type": "exact"}]   | success |
      | cross_dimension_valid  | [{"keyword": "cheap", "match_type": "broad"}]                                                                                           | success |

    Examples: Invalid partitions
      | partition              | nk_value                                                          | outcome                  |
      | empty_array            | []                                                                 | error with suggestion    |
      | empty_keyword          | [{"keyword": "", "match_type": "broad"}]                           | error with suggestion    |
      | missing_keyword        | [{"match_type": "broad"}]                                          | error with suggestion    |
      | missing_match_type     | [{"keyword": "cheap"}]                                             | error with suggestion    |
      | invalid_match_type     | [{"keyword": "cheap", "match_type": "fuzzy"}]                      | error with suggestion    |
      | conflict_with_overlay  | <with targeting_overlay.negative_keywords present>                  | error with suggestion    |

  @T-UC-003-boundary-negative-keywords-remove @boundary @negative_keywords_remove
  Scenario Outline: Negative keywords remove boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes negative_keywords_remove: <nk_value>
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                                                      | nk_value                                              | outcome                  |
      | array length 0 (empty)                                                              | []                                                     | error with suggestion    |
      | array length 1 (minimum valid)                                                      | [{"keyword": "discount", "match_type": "exact"}]       | success                  |
      | keyword length 0 (empty string)                                                     | [{"keyword": "", "match_type": "broad"}]                | error with suggestion    |
      | keyword length 1 (single char)                                                      | [{"keyword": "x", "match_type": "broad"}]               | success                  |
      | match_type = 'broad'                                                                | [{"keyword": "cheap", "match_type": "broad"}]           | success                  |
      | match_type = 'phrase'                                                               | [{"keyword": "cheap", "match_type": "phrase"}]          | success                  |
      | match_type = 'exact'                                                                | [{"keyword": "cheap", "match_type": "exact"}]           | success                  |
      | match_type = 'unknown'                                                              | [{"keyword": "cheap", "match_type": "unknown"}]         | error with suggestion    |
      | negative_keywords_remove WITH targeting_overlay.negative_keywords                   | <with overlay present>                                  | error with suggestion    |
      | negative_keywords_remove WITHOUT targeting_overlay.negative_keywords                | [{"keyword": "cheap", "match_type": "broad"}]           | success                  |
      | remove pair that exists in current list                                             | [{"keyword": "cheap", "match_type": "broad"}]           | success                  |
      | remove pair that does NOT exist in current list (no-op)                             | [{"keyword": "nonexistent", "match_type": "exact"}]     | success                  |

  @T-UC-003-partition-targeting-overlay @partition @targeting_overlay
  Scenario Outline: Targeting overlay partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field             | value   |
    | package_id        | pkg_001 |
    And the package targeting_overlay is set to: <overlay_value>
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition                        | overlay_value                                                                     | outcome |
      | absent_overlay                   | <not provided>                                                                    | success |
      | valid_overlay                    | {"geo_countries": ["US", "CA"]}                                                   | success |
      | empty_overlay                    | {}                                                                                | success |
      | single_geo_dimension             | {"geo_countries": ["US"]}                                                         | success |
      | multiple_dimensions              | {"geo_countries": ["US"], "device_platform": ["iOS"]}                              | success |
      | frequency_cap_suppress_only      | {"frequency_cap": {"suppress": {"interval": 30, "unit": "minutes"}}}              | success |
      | frequency_cap_max_impressions_only | {"frequency_cap": {"max_impressions": 3, "per": "devices", "window": {"interval": 1, "unit": "days"}}} | success |
      | frequency_cap_combined           | {"frequency_cap": {"suppress": {"interval": 60, "unit": "minutes"}, "max_impressions": 3, "per": "devices", "window": {"interval": 1, "unit": "days"}}} | success |
      | keyword_targeting                | {"keyword_targets": [{"keyword": "shoes", "match_type": "broad"}]}                | success |
      | proximity_travel_time            | {"geo_proximity": [{"travel_time": 30, "transport_mode": "driving"}]}             | success |
      | proximity_radius                 | {"geo_proximity": [{"radius": 10}]}                                               | success |
      | proximity_geometry               | {"geo_proximity": [{"geometry": "precomputed-poly-id"}]}                          | success |

    Examples: Invalid partitions
      | partition                    | overlay_value                                                                          | outcome                                      |
      | unknown_field                | {"nonexistent_field": ["value"]}                                                       | error "INVALID_REQUEST" with suggestion       |
      | managed_only_dimension       | {"publisher_managed_dim": ["value"]}                                                   | error "INVALID_REQUEST" with suggestion       |
      | geo_overlap                  | {"geo_countries": ["US"], "geo_countries_exclude": ["US"]}                              | error "INVALID_REQUEST" with suggestion       |
      | device_type_overlap          | {"device_type": ["mobile"], "device_type_exclude": ["mobile"]}                         | error "INVALID_REQUEST" with suggestion       |
      | proximity_method_conflict    | {"geo_proximity": [{"travel_time": 30, "transport_mode": "driving", "radius": 10}]}    | error "INVALID_REQUEST" with suggestion       |
      | frequency_cap_missing_fields | {"frequency_cap": {"max_impressions": 3}}                                              | error "INVALID_REQUEST" with suggestion       |
      | keyword_duplicate            | {"keyword_targets": [{"keyword": "shoes", "match_type": "broad"}, {"keyword": "shoes", "match_type": "broad"}]} | error "INVALID_REQUEST" with suggestion |

  @T-UC-003-boundary-targeting-overlay @boundary @targeting_overlay
  Scenario Outline: Targeting overlay boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field             | value   |
    | package_id        | pkg_001 |
    And the package targeting_overlay is set to: <overlay_value>
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                               | overlay_value                                                                          | outcome                                  |
      | absent overlay                               | <not provided>                                                                         | success                                  |
      | empty {} overlay                             | {}                                                                                     | success                                  |
      | valid known fields                           | {"geo_countries": ["US"]}                                                              | success                                  |
      | unknown field name                           | {"nonexistent_field": ["value"]}                                                       | error "INVALID_REQUEST" with suggestion  |
      | managed-only dimension                       | {"publisher_managed_dim": ["value"]}                                                   | error "INVALID_REQUEST" with suggestion  |
      | geo include/exclude overlap                  | {"geo_countries": ["US"], "geo_countries_exclude": ["US"]}                              | error "INVALID_REQUEST" with suggestion  |
      | device_type include/exclude overlap           | {"device_type": ["mobile"], "device_type_exclude": ["mobile"]}                        | error "INVALID_REQUEST" with suggestion  |
      | geo_proximity with travel_time only          | {"geo_proximity": [{"travel_time": 30, "transport_mode": "driving"}]}                  | success                                  |
      | geo_proximity with radius only               | {"geo_proximity": [{"radius": 10}]}                                                   | success                                  |
      | geo_proximity with geometry only             | {"geo_proximity": [{"geometry": "precomputed-poly-id"}]}                               | success                                  |
      | geo_proximity with travel_time AND radius    | {"geo_proximity": [{"travel_time": 30, "transport_mode": "driving", "radius": 10}]}   | error "INVALID_REQUEST" with suggestion  |
      | frequency_cap suppress only                  | {"frequency_cap": {"suppress": {"interval": 30, "unit": "minutes"}}}                  | success                                  |
      | frequency_cap max_impressions with per+window | {"frequency_cap": {"max_impressions": 3, "per": "devices", "window": {"interval": 1, "unit": "days"}}} | success               |
      | frequency_cap max_impressions without per    | {"frequency_cap": {"max_impressions": 3}}                                              | error "INVALID_REQUEST" with suggestion  |
      | keyword_targets with unique tuples           | {"keyword_targets": [{"keyword": "shoes", "match_type": "broad"}]}                     | success                                  |
      | keyword_targets with duplicate (keyword, match_type) | {"keyword_targets": [{"keyword": "shoes", "match_type": "broad"}, {"keyword": "shoes", "match_type": "broad"}]} | error "INVALID_REQUEST" with suggestion |

  @T-UC-003-partition-start-time @partition @start_time
  Scenario Outline: Start time partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value        |
    | media_buy_id | mb_existing  |
    | start_time   | <start_value> |
    And the existing end_time is in the future
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition              | start_value              | outcome |
      | asap_literal           | asap                     | success |
      | future_iso_datetime    | 2026-05-01T00:00:00Z     | success |
      | future_naive_datetime  | 2026-05-01T00:00:00      | success |

    Examples: Invalid partitions
      | partition          | start_value          | outcome                  |
      | past_datetime      | 2020-01-01T00:00:00Z | error with suggestion    |
      | wrong_case_asap    | ASAP                 | error with suggestion    |

  @T-UC-003-boundary-start-time @boundary @start_time
  Scenario Outline: Start time boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value        |
    | media_buy_id | mb_existing  |
    | start_time   | <start_value> |
    And the existing end_time is in the future
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point          | start_value              | outcome                  |
      | literal 'asap'          | asap                     | success                  |
      | future ISO datetime     | 2026-05-01T00:00:00Z     | success                  |
      | past datetime           | 2020-01-01T00:00:00Z     | error with suggestion    |
      | 'ASAP' wrong case       | ASAP                     | error with suggestion    |
      | absent (null)           |                          | error with suggestion    |

  @T-UC-003-partition-end-time @partition @end_time
  Scenario Outline: End time partition validation - <partition>
    Given the existing media buy has start_time "2026-04-01T00:00:00Z"
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | end_time     | <end_value>  |
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition          | end_value                | outcome |
      | after_start_time   | 2026-06-30T23:59:59Z     | success |

    Examples: Invalid partitions
      | partition          | end_value                | outcome                  |
      | equal_to_start     | 2026-04-01T00:00:00Z     | error with suggestion    |
      | before_start       | 2026-03-15T00:00:00Z     | error with suggestion    |

  @T-UC-003-boundary-end-time @boundary @end_time
  Scenario Outline: End time boundary validation - <boundary_point>
    Given the existing media buy has start_time "2026-04-01T00:00:00Z"
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | end_time     | <end_value>  |
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                   | end_value                | outcome                  |
      | end_time after start_time        | 2026-06-30T23:59:59Z     | success                  |
      | end_time = start_time (rejected) | 2026-04-01T00:00:00Z     | error with suggestion    |
      | end_time before start_time       | 2026-03-15T00:00:00Z     | error with suggestion    |
      | absent (null)                    |                          | error with suggestion    |

  @T-UC-003-partition-approval-workflow @partition @approval_workflow
  Scenario Outline: Approval workflow partition validation - <partition>
    Given the tenant human_review_required is <tenant_flag>
    And the adapter manual_approval_required is <adapter_flag>
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition                  | tenant_flag | adapter_flag | outcome                  |
      | auto_approve               | false       | false        | success (completed)      |
      | pending_human_review       | true        | false        | success (submitted)      |
      | pending_adapter_approval   | false       | true         | success (submitted)      |

  @T-UC-003-boundary-approval-workflow @boundary @approval_workflow
  Scenario Outline: Approval workflow boundary validation - <boundary_point>
    Given the tenant human_review_required is <tenant_flag>
    And the adapter manual_approval_required is <adapter_flag>
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                     | tenant_flag | adapter_flag | outcome                  |
      | both flags false (auto-approve)    | false       | false        | success (completed)      |
      | tenant flag true (pending)         | true        | false        | success (submitted)      |
      | adapter flag true (pending)        | false       | true         | success (submitted)      |

  @T-UC-003-partition-creative-replacement @partition @creative_replacement
  Scenario Outline: Creative replacement partition validation - <partition>
    Given the tenant is configured for auto-approval
    And the package "pkg_001" has existing creative assignments [cr_old_1, cr_old_2]
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package creative update mode is: <mode>
    And all referenced creatives are valid
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition                    | mode                                              | outcome |
      | creative_ids_replace         | creative_ids=[cr_new_1, cr_new_2]                 | success |
      | creative_assignments_replace | creative_assignments=[{cr_new_1, weight:70}]      | success |
      | add_new_creative             | creative_ids=[cr_old_1, cr_old_2, cr_new_3]       | success |
      | remove_existing_creative     | creative_ids=[cr_old_1]                           | success |

  @T-UC-003-boundary-creative-replacement @boundary @creative_replacement
  Scenario Outline: Creative replacement boundary validation - <boundary_point>
    Given the tenant is configured for auto-approval
    And the package "pkg_001" has existing creative assignments [cr_old_1, cr_old_2]
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package creative update mode is: <mode>
    And all referenced creatives are valid
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                            | mode                                              | outcome |
      | creative_ids replaces all existing         | creative_ids=[cr_new_1, cr_new_2]                 | success |
      | creative_assignments replaces all          | creative_assignments=[{cr_new_1, weight:70}]      | success |
      | new creative added (not in existing)       | creative_ids=[cr_old_1, cr_old_2, cr_new_3]       | success |
      | existing creative removed (not in new)     | creative_ids=[cr_old_1]                           | success |

  @T-UC-003-partition-creative-state @partition @creative_state_validation
  Scenario Outline: Creative state validation partition - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes creative_assignments referencing creative in state: <creative_state>
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition                              | creative_state      | outcome |
      | all_valid_state_compatible_format      | approved            | success |

    Examples: Invalid partitions
      | partition            | creative_state  | outcome                                        |
      | error_state          | error           | error "CREATIVE_REJECTED" with suggestion       |
      | format_incompatible  | wrong_format    | error "CREATIVE_REJECTED" with suggestion       |

  @T-UC-003-boundary-creative-state @boundary @creative_state_validation
  Scenario Outline: Creative state validation boundary - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes creative_assignments referencing creative in state: <creative_state>
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                              | creative_state  | outcome                                        |
      | all creatives valid state and format         | approved        | success                                        |
      | creative in error state                     | error           | error "CREATIVE_REJECTED" with suggestion       |
      | format incompatible with product            | wrong_format    | error "CREATIVE_REJECTED" with suggestion       |

  @T-UC-003-partition-placement-id @partition @placement_id_validation
  Scenario Outline: Placement ID validation partition - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes creative_assignments with placement configuration: <placement_config>
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition               | placement_config                       | outcome |
      | all_valid_for_product   | placement_ids=[plc_a, plc_b] (valid)   | success |
      | no_placement_ids        | no placement_ids specified             | success |

    Examples: Invalid partitions
      | partition                    | placement_config                              | outcome                                          |
      | invalid_placement_id         | placement_ids=[plc_invalid] (not in product)  | error "invalid_placement_ids" with suggestion     |
      | product_no_placement_support | placement_ids=[plc_a] (product unsupported)   | error "invalid_placement_ids" with suggestion     |

  @T-UC-003-boundary-placement-id @boundary @placement_id_validation
  Scenario Outline: Placement ID validation boundary - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes creative_assignments with placement configuration: <placement_config>
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                       | placement_config                              | outcome                                      |
      | all placement IDs valid              | placement_ids=[plc_a, plc_b] (valid)          | success                                      |
      | no placement IDs (untargeted)        | no placement_ids specified                    | success                                      |
      | product without placement support    | placement_ids=[plc_a] (product unsupported)   | error "invalid_placement_ids" with suggestion |

  @T-UC-003-partition-adapter-dispatch @partition @adapter_dispatch
  Scenario Outline: Adapter dispatch partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes <update_fields>
    And the media buy "mb_existing" exists with status "active"
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition            | update_fields                          | outcome |
      | single_field_update  | 1 package with budget update only      | success |
      | multi_field_update   | 1 package with budget and targeting    | success |

    Examples: Invalid partitions
      | partition     | update_fields                    | outcome                                 |
      | empty_update  | no updatable fields in request   | error "EMPTY_UPDATE" with suggestion    |

  @T-UC-003-boundary-adapter-dispatch @boundary @adapter_dispatch
  Scenario Outline: Adapter dispatch boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes <update_config>
    And the media buy "mb_existing" exists with status "active"
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                              | update_config                          | outcome                                 |
      | request with exactly one updatable field    | 1 package with budget update only      | success                                 |
      | request with all updatable fields           | packages with all updatable fields     | success                                 |
      | request with zero updatable fields          | no updatable fields in request         | error "EMPTY_UPDATE" with suggestion    |

  @T-UC-003-partition-persistence-timing @partition @persistence_timing
  Scenario Outline: Persistence timing partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package with budget update
    And the media buy "mb_existing" exists with status "active"
    And the tenant approval mode is <approval_mode>
    And the adapter <adapter_result>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition                       | approval_mode  | adapter_result   | outcome                          |
      | auto_approve_adapter_success    | auto-approval  | returns success  | success with persisted records   |
      | manual_approval_pending         | manual         | not yet called   | success with pending status      |

    Examples: Invalid partitions
      | partition                       | approval_mode  | adapter_result   | outcome                          |
      | auto_approve_adapter_failure    | auto-approval  | returns error    | error "ADAPTER_ERROR" — no records persisted |

  @T-UC-003-boundary-persistence-timing @boundary @persistence_timing
  Scenario Outline: Persistence timing boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package with budget update
    And the media buy "mb_existing" exists with status "active"
    And the tenant approval mode is <approval_mode>
    And the adapter <adapter_result>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                              | approval_mode  | adapter_result   | outcome                                       |
      | adapter returns success (auto-approval)     | auto-approval  | returns success  | success with persisted records                |
      | adapter returns error (auto-approval)       | auto-approval  | returns error    | error "ADAPTER_ERROR" — no records persisted  |
      | manual approval detected (pending state)    | manual         | not yet called   | success with pending status                   |

  @T-UC-003-partition-principal-ownership @partition @principal_ownership
  Scenario Outline: Principal ownership partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the media buy "mb_existing" exists with owner <owner>
    And the authenticated principal is <principal>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition      | owner       | principal   | outcome |
      | owner_matches  | buyer_001   | buyer_001   | success |

    Examples: Invalid partitions
      | partition       | owner       | principal   | outcome                                        |
      | owner_mismatch  | buyer_001   | buyer_999   | error "PERMISSION_DENIED" with suggestion      |

  @T-UC-003-boundary-principal-ownership @boundary @principal_ownership
  Scenario Outline: Principal ownership boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the media buy "mb_existing" exists with owner <owner>
    And the authenticated principal is <principal>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                    | owner       | principal   | outcome                                        |
      | principal matches owner           | buyer_001   | buyer_001   | success                                        |
      | principal differs from owner      | buyer_001   | buyer_999   | error "PERMISSION_DENIED" with suggestion      |

  @T-UC-003-v31-error-budget-too-low @extension @ext-d @error @v3.1 @error-details @post-f1 @post-f2 @post-f3
  Scenario: BUDGET_TOO_LOW error carries v3.1 details shape (minimum_budget + currency)
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | budget       | 100         |
    And the seller's minimum budget for this media buy is 500 USD
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "BUDGET_TOO_LOW"
    And the error "details" object should include "minimum_budget" with value 500
    And the error "details" object should include "currency" with value "USD"
    And the "currency" value should match ISO 4217 alphabetic format
    And the error should include a "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: Error code BUDGET_TOO_LOW with structured details
    # POST-F3: Buyer Agent can adjust budget without re-querying products (suggestion guides the fix)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-v31-error-conflict-version @error @v3.1 @error-details @concurrency @ext-s @post-f1 @post-f2 @post-f3
  Scenario: CONFLICT error carries v3.1 details shape (resource_id + expected/current version)
    Given the media buy "mb_existing" is at version 7
    And the Buyer Agent's last-read version of "mb_existing" is 5
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | budget       | 25000       |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "CONFLICT"
    And the error "details" object should include "resource_id" with value "mb_existing"
    And the error "details" object should include "expected_version" with value 5
    And the error "details" object should include "current_version" with value 7
    And the error should include a "suggestion" field
    # POST-F1: System state unchanged (no partial write)
    # POST-F2: CONFLICT details enable optimistic-concurrency retry
    # POST-F3: Buyer Agent re-reads at current_version and retries (suggestion guides the fix)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-v31-error-idempotency-conflict @error @v3.1 @error-details @idempotency @post-f1 @post-f2 @post-f3
  Scenario: IDEMPOTENCY_CONFLICT error carries v3.1 details shape with ETag versions
    Given idempotency_key "upd-20260521-001" was previously used with a different request body
    And the recorded ETag for that key is "W/\"etag-abc\""
    And a valid update_media_buy request with:
    | field           | value             |
    | media_buy_id    | mb_existing       |
    | idempotency_key | upd-20260521-001  |
    | budget          | 30000             |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "IDEMPOTENCY_CONFLICT"
    And the error "details" object should include "resource_id" with value "mb_existing"
    And the error "details" object should include "current_version" with value "W/\"etag-abc\""
    And the error should include a "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: IDEMPOTENCY_CONFLICT details accept string ETag (not only numeric versions)
    # POST-F3: Buyer Agent retries with a fresh idempotency_key (suggestion guides the fix)

  @T-UC-003-storyboard-media-buy-not-found @storyboard-v3.1 @v3-1 @structured-errors @media-buy-not-found
  Scenario: update_media_buy with unknown media_buy_id returns structured MEDIA_BUY_NOT_FOUND, not a 500
    Given the buyer fabricates a media_buy_id that does not exist in the seller catalog
    When the Buyer Agent sends update_media_buy with the unknown media_buy_id and paused true
    Then the operation should fail
    And the error code should be "MEDIA_BUY_NOT_FOUND"
    And the error recovery hint should indicate correctable
    And the response should echo the context.correlation_id unchanged
    And the response should NOT be a 500 or non-AdCP error shape
    # invalid_transitions Phase 1 (unknown_media_buy): the buyer references a fabricated
    # media_buy_id. The seller MUST return MEDIA_BUY_NOT_FOUND with recovery=correctable
    # and context echoed unchanged. Sellers returning 500s or silent successes fail.
    # invalid_transitions: unknown media_buy_id surfaces as structured MEDIA_BUY_NOT_FOUND
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/invalid_transitions.yaml

  @T-UC-003-storyboard-package-not-found @storyboard-v3.1 @v3-1 @structured-errors @package-not-found
  Scenario: update_media_buy with known media_buy_id but unknown package_id returns PACKAGE_NOT_FOUND
    Given the media buy exists in the seller catalog
    And the buyer references a package_id that does not belong to the media buy
    When the Buyer Agent sends update_media_buy targeting the unknown package
    Then the operation should fail
    And the error code should be "PACKAGE_NOT_FOUND"
    And the response should echo the context.correlation_id unchanged
    # invalid_transitions Phase 3 (unknown_package): media_buy_id resolves but the buyer
    # references a package_id that does not belong to it. Seller MUST return
    # PACKAGE_NOT_FOUND, distinguishing from MEDIA_BUY_NOT_FOUND so the buyer knows
    # whether to retry against the buy or fix the package reference.
    # invalid_transitions: distinct PACKAGE_NOT_FOUND error code separates buy-level from package-level lookup failure
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/invalid_transitions.yaml

  @T-UC-003-storyboard-not-cancellable-on-recancel @storyboard-v3.1 @v3-1 @structured-errors @not-cancellable @terminal-state
  Scenario: Re-cancel of a canceled media buy returns NOT_CANCELLABLE, not silent success
    Given the media buy is in "canceled" status
    When the Buyer Agent sends update_media_buy with canceled true on the already-canceled buy
    Then the operation should fail
    And the error code should be "NOT_CANCELLABLE"
    And the response should echo the context.correlation_id unchanged
    # invalid_transitions Phase 4 (double_cancel): cancel a buy (success), then try to
    # cancel the same buy again. canceled is terminal per the AdCP spec; the second
    # cancel cannot succeed. Seller MUST return NOT_CANCELLABLE (the schema reserves
    # this code for "media buy cannot be canceled in its current state"). Distinct from
    # the existing terminal_canceled INVALID_STATE scenario which targets non-cancel
    # updates; NOT_CANCELLABLE is reserved for re-cancel attempts specifically.
    # invalid_transitions: re-cancel of terminal canceled buy is NOT_CANCELLABLE, not silent success
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/creative_fate_after_cancellation.yaml

  @T-UC-003-storyboard-creative-fate-after-cancellation @storyboard-v3.1 @v3-1 @cancellation @creative-library @lifecycle-decoupling
  Scenario: Canceling a media buy releases package-creative assignments but leaves creatives in the library with review state intact
    Given a media buy has been canceled
    And the canceled buy had a package with creative assignments
    When the buyer subsequently calls list_creatives for the same account
    Then the creatives that were assigned to the canceled buy's package should still appear in the library
    And the creatives' review status should be unchanged from before the cancellation
    And the creatives should NOT be auto-flipped to status "rejected" as a side effect of the cancellation
    And the creatives should remain reusable by creative_id in a subsequent create_media_buy or sync_creatives
    # creative_fate_after_cancellation storyboard: per the creative library model,
    # creative state and assignment state are SEPARATE. Canceling a buy releases
    # the package-creative assignments but the underlying creatives remain in the
    # library, reusable by creative_id in a subsequent create_media_buy or
    # sync_creatives. A seller MUST NOT implicitly reject a creative because its
    # containing buy was canceled -- a rejection MUST be a deliberate review
    # decision with its own rejection_reason.
    # creative_fate_after_cancellation: creative lifecycle decoupled from media buy lifecycle

  @T-UC-003-partition-revision @partition @revision @schema-v3.1
  Scenario Outline: Revision optimistic-concurrency partition validation - <partition>
    Given the media buy "mb_existing" is at revision 7
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | budget       | 9000        |
    And the request revision is set to <value>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>
    # v3.1 (revision_optimistic_concurrency.yaml): revision optional; minimum 1; mismatch -> CONFLICT
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: Valid partitions
      | partition       | value          | outcome |
      | absent          | <not provided> | success |
      | matches_current | 7              | success |

    Examples: Invalid partitions
      | partition      | value | outcome                            |
      | stale_revision | 5     | error "CONFLICT" with suggestion   |
      | ahead_revision | 99    | error "CONFLICT" with suggestion   |
      | below_min      | 0             | error "INVALID_REQUEST" with suggestion |
      # A non-numeric string (not a coercible numeral like "7", which pydantic
      # lax-coerces to a valid int on the JSON transports) so the wrong-TYPE is
      # rejected consistently on every transport, not just A2A. The numeric-string
      # divergence itself is deferred and tracked in #1582.
      | wrong_type     | "not-an-int"  | error "INVALID_REQUEST" with suggestion |

  @T-UC-003-boundary-revision @boundary @revision @schema-v3.1
  Scenario Outline: Revision optimistic-concurrency boundary validation - <boundary_point>
    Given the media buy "mb_existing" is at revision <current>
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | budget       | 9000        |
    And the request revision is set to <value>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: Boundary values
      | boundary_point                  | current | value          | outcome                            |
      | revision absent (LWW)           | 7       | <not provided> | success                            |
      | revision 1, buy at 1 (min match)| 1       | 1              | success                            |
      | revision 0 (below minimum 1)    | 7       | 0              | error "INVALID_REQUEST" with suggestion |
      | revision below current (stale)  | 7       | 6              | error "CONFLICT" with suggestion   |
      | revision above current (ahead)  | 7       | 8              | error "CONFLICT" with suggestion   |

  @T-UC-003-revision-success-increments @invariant @BR-RULE-215 @concurrency @schema-v3.1 @post-s1
  Scenario: Successful update increments and returns the new revision (INV-4)
    Given the tenant is configured for auto-approval
    And the media buy "mb_existing" is at revision 7
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | budget       | 9000        |
    And the request revision is set to 7
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the response should contain a revision with value 8
    And the response should contain a valid_actions array
    # BR-RULE-215 INV-4: a mutating update increments the stored revision and returns the new value
    # INT-002: success response carries valid_actions so the buyer can plan the next call without a get_media_buys round-trip
    # POST-S1: Buyer knows the media buy was updated (new revision)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-revision-and-idempotency-independent @invariant @BR-RULE-215 @concurrency @idempotency @schema-v3.1
  Scenario: revision and idempotency_key are evaluated independently (INV-6)
    Given the tenant is configured for auto-approval
    And the media buy "mb_existing" is at revision 7
    And a valid update_media_buy request with:
    | field           | value                                |
    | media_buy_id    | mb_existing                          |
    | idempotency_key | 550e8400-e29b-41d4-a716-446655440000 |
    | budget          | 9000                                 |
    And the request revision is set to 7
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    # BR-RULE-215 INV-6: idempotency-replay check (BR-RULE-211) and revision check are independent; neither subsumes the other
    # ---------- BR-RULE-214: Billing Arrangement Eligibility ----------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-ext-t @extension @ext-t @error @schema-v3.1 @post-f1 @post-f2 @post-f3
  Scenario: Invoice recipient not authorized for the account is rejected (ext-t)
    Given the Buyer is authenticated with a valid principal_id
    And a valid update_media_buy request with:
    | field             | value                  |
    | media_buy_id      | mb_existing            |
    | invoice_recipient | acme-finance-not-on-acct |
    | budget            | 12000                  |
    And the invoice_recipient "acme-finance-not-on-acct" is not authorized for this account
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion" field
    # BR-RULE-214 INV-8: invoice_recipient override must be authorized for the account before the operation proceeds
    # POST-F1: System state unchanged (no billing redirected); POST-F3: omit override or supply an authorized recipient
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-billing-not-supported @invariant @BR-RULE-214 @billing @error @schema-v3.1 @post-f2 @post-f3
  Scenario Outline: Billing party not supported is rejected with scope - <partition>
    Given the Buyer is authenticated with a valid principal_id
    And the resolved billing party is "<billing_party>"
    And the seller's supported_billing is <supported>
    And the seller's account billing relationship is "<acct_relationship>"
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | budget       | 12000       |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "BILLING_NOT_SUPPORTED"
    And the error "details" object should include "scope" with value "<scope>"
    And the error should include "suggestion" field
    # BR-RULE-214 INV-2/INV-3 (billing_eligibility.yaml)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples:
      | partition            | billing_party | supported              | acct_relationship | scope      |
      | capability_unsupported | advertiser  | ["operator","agent"]   | present           | capability |
      | account_unsupported    | operator    | ["operator"]           | absent            | account    |

  @T-UC-003-billing-not-permitted-agent @invariant @BR-RULE-214 @billing @error @schema-v3.1 @post-f2 @post-f3
  Scenario: Billing party not permitted for the calling agent (INV-4/6/7)
    Given the Buyer is authenticated with an established agent identity
    And the resolved billing party "agent" is in the seller's supported_billing
    And the calling agent's commercial relationship is "passthrough_only"
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | budget       | 12000       |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "BILLING_NOT_PERMITTED_FOR_AGENT"
    And the error "details" object should include "rejected_billing"
    And the error "details" object should NOT include "rate_cards" or "payment_terms" or "credit_limit"
    And the error should include "suggestion" field
    # BR-RULE-214 INV-4: per-agent rejection; details carry rejected_billing and MAY carry one suggested_billing only
    # INV-6/INV-7: present suggested_billing -> autonomous retry; absent -> terminal-pending-onboarding (surface to human)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-billing-unauth-identity @invariant @BR-RULE-214 @billing @error @schema-v3.1 @post-f2
  Scenario: Unestablished agent identity falls back to BILLING_NOT_SUPPORTED with no scope (INV-5)
    Given the caller has no established agent identity
    And the underlying reason is a per-agent billing restriction
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | budget       | 12000       |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "BILLING_NOT_SUPPORTED"
    And the error "details" object should NOT include "scope"
    And the error code should NOT be "BILLING_NOT_PERMITTED_FOR_AGENT"
    And the error should include "suggestion" field
    # BR-RULE-214 INV-5: per-agent gate MUST NOT fire without established identity; return BILLING_NOT_SUPPORTED (scope omitted)
    # ---------- BR-RULE-217: Mid-Flight Package Addition ----------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-ext-u @extension @ext-u @error @schema-v3.1 @post-f1 @post-f2 @post-f3
  Scenario: New packages on a seller that does not support mid-flight additions (ext-u)
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes new_packages with one complete package-request
    And the media buy's valid_actions does NOT advertise "add_packages"
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error should include "suggestion" field
    # BR-RULE-217 INV-1: new_packages on a non-supporting seller -> UNSUPPORTED_FEATURE
    # POST-F3: create the additional packages via a separate create_media_buy
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-new-packages-add @invariant @BR-RULE-217 @schema-v3.1 @post-s1 @post-s2
  Scenario: Supporting seller adds new packages atomically (INV-2/INV-4)
    Given the tenant is configured for auto-approval
    And the media buy's valid_actions advertises "add_packages"
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes new_packages with two complete package-requests
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And both new packages should appear in the affected_packages
    # BR-RULE-217 INV-2/INV-4: supporting seller advertises add_packages and adds all entries atomically (all-or-none)
    # POST-S1/S2: Buyer knows the media buy was updated and which packages were affected
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-new-packages-incomplete @invariant @BR-RULE-217 @error @schema-v3.1 @post-f2 @post-f3
  Scenario: New package entry missing a required package-request field is rejected (INV-3)
    Given the media buy's valid_actions advertises "add_packages"
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes new_packages with an entry missing product_id
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion" field
    # BR-RULE-217 INV-3: each new_packages entry must be a complete package-request (product_id, budget, pricing_option_id)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-new-packages-duplicate @invariant @BR-RULE-010 @error @schema-v3.1 @post-f1 @post-f2
  Scenario: New package duplicating an existing product on the media buy is rejected (BR-RULE-010 INV-4)
    Given the media buy's valid_actions advertises "add_packages"
    And the media buy already has a package for product "prod_news_300x250"
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes new_packages with a package for product "prod_news_300x250"
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion" field
    # BR-RULE-010 INV-4: on update, a new_packages product_id duplicating another entry or an existing package is rejected
    # ---------- BR-RULE-216: Campaign & Package Cancellation Semantics ----------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-ext-v @extension @ext-v @error @schema-v3.1 @post-f1 @post-f2 @post-f3
  Scenario: Cancellation refused when the buy cannot be canceled in its current state (ext-v)
    Given the media buy "mb_existing" is in "active" status
    And the media buy has committed delivery that the seller cannot cancel mid-flight
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | canceled     | true        |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "NOT_CANCELLABLE"
    And the error should include "suggestion" field
    # BR-RULE-216 INV-4: buy not cancellable in current state -> NOT_CANCELLABLE (correctable)
    # POST-F3: pause instead (paused: true) or contact the seller

  @T-UC-003-cancel-reason-boundary @boundary @cancellation @schema-v3.1
  Scenario Outline: cancellation_reason length boundary - <boundary_point>
    Given the tenant is configured for auto-approval
    And the media buy "mb_existing" is in "active" status
    And a valid update_media_buy request with:
    | field    | value       |
    | media_buy_id | mb_existing |
    | canceled | true        |
    And the cancellation_reason is set to <value>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>
    # BR-RULE-216 INV-2: cancellation_reason MUST be at most 500 characters
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: Boundary values
      | boundary_point         | value             | outcome                                 |
      | reason 500 chars (max) | <500 char string> | success                                 |
      | reason 501 chars (max+1) | <501 char string> | error "VALIDATION_ERROR" with suggestion |

  @T-UC-003-cancel-package @invariant @BR-RULE-216 @cancellation @schema-v3.1 @post-s2
  Scenario: Package-level cancellation stops only that package (INV-6)
    Given the tenant is configured for auto-approval
    And the media buy "mb_existing" is in "active" status with packages "pkg_001" and "pkg_002"
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | canceled   | true    |
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And package "pkg_001" should be canceled
    And the media buy status should NOT be "canceled"
    # BR-RULE-216 INV-6: per-package canceled:true cancels only that package; the media buy is not canceled
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-cancel-irreversible @invariant @BR-RULE-216 @cancellation @error @schema-v3.1 @post-f2 @post-f3
  Scenario: canceled:false is rejected -- cancellation is irreversible (INV-1/3)
    Given the media buy "mb_existing" is in "active" status
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | canceled     | false       |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion" field
    # BR-RULE-216 INV-1: canceled only valid value is const true; INV-3: cancellation is irreversible (no "uncancel")
    # ---------- BR-RULE-198: Package Immutable Fields After Creation (immutable field guard on package update) ----------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-ext-w @extension @ext-w @error @schema-v3.1 @post-f1 @post-f2 @post-f3
  Scenario: Package update including an immutable field is rejected (ext-w)
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value      |
    | package_id | pkg_001    |
    | product_id | prod_other |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-198 INV-1: product_id is immutable post-create; package-update root `not` constraint rejects it
    # POST-F3: remove the immutable field, or create a new media buy to change product/formats/pricing
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-partition-immutable-package-field @partition @immutable_field_guard @schema-v3.1
  Scenario Outline: Immutable package-field guard partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update containing <forbidden_field>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>
    # BR-RULE-198 (immutable_field_guard.yaml): product_id / format_ids / pricing_option_id forbidden in package-update
    # ---------- BR-RULE-219: Committed Metrics Append-Only ----------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: Valid partitions
      | partition         | forbidden_field          | outcome |
      | no_immutable_keys | only budget (updatable)  | success |

    Examples: Invalid partitions
      | partition                       | forbidden_field                          | outcome                                  |
      | product_id_present              | product_id                               | error "INVALID_REQUEST" with suggestion |
      | format_ids_present              | format_ids                               | error "INVALID_REQUEST" with suggestion |
      | pricing_option_id_present       | pricing_option_id                        | error "INVALID_REQUEST" with suggestion |
      | multiple_immutable_keys_present | product_id and pricing_option_id         | error "INVALID_REQUEST" with suggestion |

  @T-UC-003-committed-metrics-append @invariant @BR-RULE-219 @schema-v3.1 @post-s1
  Scenario: Appending a new committed metric is accepted and the set grows monotonically (INV-1/4)
    Given the tenant is configured for auto-approval
    And the media buy "mb_existing" has a committed metric "impressions" with committed_at "2026-04-29T10:53:00Z"
    And a valid update_media_buy request that appends a new committed metric "viewable_rate"
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the committed metric "impressions" should remain present with committed_at "2026-04-29T10:53:00Z"
    And the committed metric "viewable_rate" should be present with its own committed_at
    # BR-RULE-219 INV-1: append new (scope, metric_id, qualifier) accepted; INV-4: prior entries preserved (monotonic)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-committed-metrics-immutable @invariant @BR-RULE-219 @error @schema-v3.1 @post-f1 @post-f2
  Scenario Outline: Modifying or removing a committed metric is rejected - <partition>
    Given the media buy "mb_existing" has a committed metric "impressions" with committed_at "2026-04-29T10:53:00Z"
    And a valid update_media_buy request that <mutation>
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion" field
    # BR-RULE-219 INV-2/INV-3: existing committed entries cannot be modified or removed (suggested code IMMUTABLE_FIELD)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples:
      | partition                | mutation                                                  |
      | modify_existing_committed_at | rewrites the committed_at of the existing impressions entry |
      | remove_existing_entry    | drops the existing impressions entry                       |
      | shrink_contract          | submits a strict subset that retracts a committed entry    |

  @T-UC-003-committed-metrics-noop @invariant @BR-RULE-219 @schema-v3.1 @post-s5
  Scenario Outline: Byte-identical resubmit and omission are non-mutating no-ops (INV-5/6) - <partition>
    Given the tenant is configured for auto-approval
    And the media buy "mb_existing" has a committed metric "impressions" with committed_at "2026-04-29T10:53:00Z"
    And a valid update_media_buy request that <case>
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the committed metric "impressions" should remain unchanged
    # ---------- BR-RULE-209 INV-10: Sandbox on update non-success shapes ----------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples:
      | partition                 | case                                                       |
      | resubmit_existing_unchanged | re-submits the existing committed_metrics byte-for-byte unchanged |
      | omit_field_entirely       | omits committed_metrics entirely                            |

  @T-UC-003-sandbox-update-nonsuccess @invariant @BR-RULE-209 @sandbox @schema-v3.1 @post-f2
  Scenario Outline: sandbox is forbidden on update non-success response shapes (INV-10) - <shape>
    Given the request's account reference resolves to a sandbox account
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the update resolves to the <shape> response shape because <trigger>
    When the Buyer Agent sends the update_media_buy request
    Then the response payload should NOT contain a "sandbox" field
    # BR-RULE-209 INV-10: update_media_buy three-way oneOf -- sandbox appears only on synchronous success
    # ---------- BR-RULE-013 INV-6: package-level start_time 'asap' forbidden ----------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples:
      | shape            | trigger                                            |
      | terminal failure | a validation error rejects the update              |
      | submitted task   | manual approval queues the update (UpdateMediaBuySubmitted) |

  @T-UC-003-package-start-time-asap @invariant @BR-RULE-013 @error @schema-v3.1 @post-f2 @post-f3
  Scenario: Package-level start_time 'asap' is schema-forbidden (INV-6)
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | start_time | asap    |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-013 INV-6: 'asap' is forbidden (not/const) at package scope; only an ISO 8601 date-time is accepted
    # (media-buy-level start_time still accepts 'asap' -- see T-UC-003-boundary-start-time)

  @T-UC-003-bva-sandbox-response @boundary @sandbox @schema-v3.1 @post-s6 @post-f2
  Scenario Outline: sandbox response-field boundary - <boundary>
    Given an update_media_buy request whose resolved account is <account_context>
    And the update resolves to the <response_shape> response shape
    When the Buyer Agent sends the update_media_buy request
    Then the sandbox response-field outcome should be <outcome>
    # ---------- start_time package-scope boundary (start_time.yaml) ----------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: sandbox response semantics (BR-UC-003)
      | boundary                                                            | account_context    | response_shape      | outcome                              |
      | sandbox: true in response (sandbox account)                         | a sandbox account  | synchronous success | sandbox flag present and true        |
      | sandbox: false in response (explicit production)                    | a production account | synchronous success | sandbox flag present and false     |
      | sandbox absent in response (production account)                     | a production account | synchronous success | sandbox flag omitted                |
      | sandbox: true on update_media_buy synchronous success shape         | a sandbox account  | synchronous success | sandbox flag present and true        |
      | sandbox account with invalid budget (real validation error)         | a sandbox account  | terminal failure    | rejected with a real validation error (not a silent ok) |
      | sandbox present on update_media_buy terminal-failure (errors) shape  | a sandbox account  | terminal failure    | sandbox field forbidden on this shape |
      | sandbox present on update_media_buy submitted task envelope         | a sandbox account  | submitted task      | sandbox field forbidden on this shape |

  @T-UC-003-bva-start-time-package-scope @boundary @start_time @schema-v3.1 @post-f2
  Scenario Outline: start_time boundary at package scope - <boundary>
    Given a valid update_media_buy request for "mb_existing"
    And the request includes 1 package update where start_time is <value>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>
    # ---------- billing eligibility boundaries (billing_eligibility.yaml, BR-RULE-214) ----------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: start_time package-scope boundary (BR-UC-003)
      | boundary                | value | outcome                            |
      | 'asap' at package scope | asap  | error "INVALID_REQUEST" — rejected |

  @T-UC-003-bva-billing-eligibility @boundary @billing @schema-v3.1 @post-f2
  Scenario Outline: billing eligibility boundary - <boundary>
    Given an update_media_buy request for "mb_existing" with the billing condition: <boundary>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>
    # ---------- targeting_overlay collection_list boundaries (targeting_overlay.yaml, BR-RULE-014) ----------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: billing eligibility (BR-UC-002/003)
      | boundary                                                                                                            | outcome                                       |
      | billing resolved = operator; operator in supported_billing; agent permitted                                         | accepted                                      |
      | billing resolved = advertiser; seller's supported_billing = [operator, agent]                                       | error "BILLING_NOT_SUPPORTED" — rejected      |
      | billing resolved = operator; seller supports operator generally; no operator billing relationship on this account   | error "BILLING_NOT_SUPPORTED" — rejected      |
      | billing resolved = agent; agent in supported_billing; calling agent is passthrough-only                             | error "BILLING_NOT_PERMITTED_FOR_AGENT" — rejected |
      | billing resolved = agent; caller unauthenticated                                                                    | error "BILLING_NOT_SUPPORTED" — rejected (identity-fallback) |
      | invoice_recipient supplied = business entity not authorized for this account                                        | rejected (invoice_recipient unauthorized)     |

  @T-UC-003-bva-targeting-overlay-collection-list @boundary @targeting_overlay @schema-v3.1 @post-f2
  Scenario Outline: targeting_overlay collection_list boundary - <boundary>
    Given a valid update_media_buy request for "mb_existing"
    And the request carries a targeting_overlay where <boundary>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>
    # ---------- immutable_field_guard boundaries (immutable_field_guard.yaml, BR-RULE-198) ----------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: collection_list overlay (BR-UC-002/003)
      | boundary                                              | outcome  |
      | collection_list with valid agent_url and list_id      | accepted |
      | collection_list_exclude with valid agent_url and list_id | accepted |
      | both collection_list and collection_list_exclude set  | accepted |

  @T-UC-003-bva-immutable-field-guard @boundary @immutable_field_guard @schema-v3.1 @post-f2
  Scenario Outline: immutable field guard boundary - <boundary>
    Given a valid update_media_buy request for "mb_existing"
    And the request includes a package update where <boundary>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>
    # ---------- package_immutable_fields boundaries (uc026_immutable_fields.yaml, BR-RULE-198) ----------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: package-update root guard (BR-UC-003)
      | boundary                                                            | outcome                              |
      | package update with only updatable fields (no forbidden keys)       | accepted                             |
      | product_id present in package-update (root `not` rejects)           | error "INVALID_REQUEST" — rejected  |
      | format_ids present in package-update (root `not` rejects)           | error "INVALID_REQUEST" — rejected  |
      | pricing_option_id present in package-update (root `not` rejects)    | error "INVALID_REQUEST" — rejected  |
      | product_id + pricing_option_id both present (root `not`/anyOf rejects) | error "INVALID_REQUEST" — rejected |

  @T-UC-003-bva-package-immutable-fields @boundary @immutable_field_guard @schema-v3.1 @post-f2
  Scenario Outline: package immutable fields boundary - <boundary>
    Given a valid update_media_buy request for "mb_existing"
    And the request includes a package update where the payload <boundary>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>
    # ---------- committed_metrics append-only boundaries (committed_metrics_append_only.yaml, BR-RULE-219) ----------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: PackageUpdate immutable-field guard (BR-UC-003)
      | boundary                              | outcome                                 |
      | update with only mutable fields       | accepted                                |
      | update includes product_id            | error "INVALID_REQUEST" with suggestion |
      | update includes format_ids            | error "INVALID_REQUEST" with suggestion |
      | update includes pricing_option_id     | error "INVALID_REQUEST" with suggestion |
      | update includes all three immutable fields | error "INVALID_REQUEST" with suggestion |

  @T-UC-003-bva-committed-metrics @boundary @committed_metrics @schema-v3.1 @post-f2
  Scenario Outline: committed_metrics append-only boundary - <boundary>
    Given a media buy "mb_existing" whose committed_metrics already contains a committed contract
    And an update_media_buy request that <boundary>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>
    # v3.1 core/package.json committed_metrics: sellers MUST reject modify/remove of existing
    # entries with validation_error, suggested code IMMUTABLE_FIELD (BR-RULE-219 INV-2/INV-3).
    # ---------- idempotency_key boundaries (idempotency_key.yaml, BR-RULE-081) ----------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: committed_metrics append-only (BR-UC-003)
      | boundary                                          | outcome                                       |
      | add a new vendor-scoped entry                     | accepted                                      |
      | re-submit existing entries unchanged (idempotent) | accepted                                      |
      | omit field entirely (partial-update, no change)   | accepted                                      |
      | modify committed_at of an existing entry          | error "VALIDATION_ERROR" (suggested_code IMMUTABLE_FIELD) — rejected |
      | modify qualifier of an existing entry             | error "VALIDATION_ERROR" (suggested_code IMMUTABLE_FIELD) — rejected |
      | remove an existing entry                          | error "VALIDATION_ERROR" (suggested_code IMMUTABLE_FIELD) — rejected |
      | shrink contract to a strict subset                | error "VALIDATION_ERROR" (suggested_code IMMUTABLE_FIELD) — rejected |

  @T-UC-003-bva-idempotency-key @boundary @idempotency_key @schema-v3.1 @post-f2
  Scenario Outline: idempotency_key boundary - <boundary>
    Given a valid update_media_buy request for "mb_existing"
    And the request's idempotency_key matches the boundary <boundary>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>
    # ---------- product_uniqueness boundaries (product_uniqueness.yaml, BR-RULE-010) ----------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: idempotency_key boundaries (BR-UC-003)
      | boundary                                       | outcome                                            |
      | absent (field not provided)                    | error "INVALID_REQUEST" — rejected (v3.1 requires idempotency_key) |
      | valid length, disallowed character (e.g. space) | error "VALIDATION_ERROR" (suggested_code IDEMPOTENCY_KEY_INVALID_FORMAT) — rejected |

  @T-UC-003-bva-product-uniqueness @boundary @product_uniqueness @schema-v3.1 @post-f2
  Scenario Outline: product uniqueness boundary - <boundary>
    Given an update_media_buy request for "mb_existing" whose package set is described by <boundary>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>
    # ---------- approval_workflow submitted-envelope boundaries (approval_workflow.yaml) ----------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: product uniqueness across packages (BR-UC-002/003)
      | boundary                                                                | outcome              |
      | single package (trivially unique)                                       | accepted             |
      | two packages, different products                                        | accepted             |
      | two packages, same product_id                                           | rejected (duplicate) |
      | proposal_id supplied, packages omitted (no buyer packages array)        | accepted             |
      | update new_packages[] adds a product_id not present elsewhere           | accepted             |
      | update new_packages[] product_id duplicates an existing media buy package | rejected (duplicate) |
      | update new_packages[] contains two entries with the same product_id     | rejected (duplicate) |

  @T-UC-003-bva-approval-workflow @boundary @approval_workflow @schema-v3.1 @post-s7 @post-s8
  Scenario Outline: approval workflow submitted-envelope boundary - <boundary>
    Given an update_media_buy request for "mb_existing"
    And the tenant/adapter approval configuration is <boundary>
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: approval workflow (BR-UC-003)
      | boundary                                  | outcome                                    |
      | tenant flag true (submitted task envelope) | submitted task envelope (awaiting approval) |
      | adapter flag true (submitted task envelope) | submitted task envelope (awaiting approval) |
