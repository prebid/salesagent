# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

@analysis-2026-03-09 @schema-v3.0.0-rc.1
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
  #   POST-S8: Buyer knows the implementation date is null (pending approval)
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
    And the response should contain buyer_ref
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

  @T-UC-003-main-buyer-ref @main-flow @identification @post-s1 @post-s4 @post-s5
  Scenario: Package budget update -- identified by buyer_ref
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field     | value     |
    | buyer_ref | my_ref_01 |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 7500    |
    And the buyer_ref "my_ref_01" resolves to the existing media buy
    And the package "pkg_001" exists in the media buy
    And the updated daily spend does not exceed max_daily_package_spend
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the response should contain media_buy_id
    And the response should contain buyer_ref "my_ref_01"
    # POST-S1: Buyer knows media buy updated
    # POST-S4: Unambiguous success
    # POST-S5: Request completed

  @T-UC-003-alt-pause @alt-flow @pause @post-s1 @post-s4 @post-s5 @post-s6
  Scenario: Pause campaign -- auto-applied
    Given the tenant is configured for auto-approval
    And the media buy is in "active" status
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | paused       | true        |
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the response should contain media_buy_id "mb_existing"
    And the response should contain affected_packages
    And the response envelope should include a sandbox flag
    # POST-S1: Buyer knows media buy paused
    # POST-S4: Unambiguous success
    # POST-S5: Request completed
    # POST-S6: Sandbox flag present

  @T-UC-003-alt-resume @alt-flow @resume @post-s1 @post-s4 @post-s5
  Scenario: Resume campaign -- auto-applied
    Given the tenant is configured for auto-approval
    And the media buy is in "paused" status
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | paused       | false       |
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the response should contain media_buy_id "mb_existing"
    # POST-S1: Buyer knows media buy resumed
    # POST-S4: Unambiguous success
    # POST-S5: Request completed

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

  @T-UC-003-adapter-failure @invariant @BR-RULE-020 @error
  Scenario: Adapter atomicity -- failure rolls back all changes
    Given the tenant is configured for auto-approval
    And the ad server adapter returns an error
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And no database records should be modified
    And the error should include "suggestion" field
    # BR-RULE-020 INV-2: Adapter error → no changes persisted
    # POST-F1: System state unchanged
    # POST-F3: Suggestion present

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

  @T-UC-003-ext-b-buyer-ref @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Media buy not found -- by buyer_ref
    Given a valid update_media_buy request with:
    | field     | value            |
    | buyer_ref | unknown_ref      |
    And no media buy exists with buyer_ref "unknown_ref"
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "PRODUCT_NOT_FOUND"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: Error explains media buy not found
    # POST-F3: Suggestion for recovery

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

  @T-UC-003-ext-o @extension @ext-o @error @post-f1 @post-f2 @post-f3
  Scenario: Adapter failure -- ad server returns error
    Given the tenant is configured for auto-approval
    And the ad server adapter returns an error during update
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And no database records should be modified
    And the error should include "suggestion" field
    # BR-RULE-020 INV-2: Adapter error → no changes persisted
    # POST-F1: System state unchanged
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

  @T-UC-003-ext-q-rejected @extension @ext-q @error @post-f1 @post-f2 @post-f3
  Scenario: Terminal status rejection -- media buy status is "rejected"
    Given the media buy is in "rejected" status
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_STATUS"
    And the error should include "recovery" field with value "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "new media buy"
    # POST-F1: System state unchanged
    # POST-F2: Error code INVALID_STATUS
    # POST-F3: Suggestion to create new media buy

  @T-UC-003-ext-q-canceled @extension @ext-q @error @post-f1 @post-f2 @post-f3
  Scenario: Terminal status rejection -- media buy status is "canceled"
    Given the media buy is in "canceled" status
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_STATUS"
    And the error should include "suggestion" field
    And the suggestion should contain "new media buy"
    # POST-F1: System state unchanged
    # POST-F2: Error explains terminal status
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-q-completed @extension @ext-q @error @post-f1 @post-f2 @post-f3
  Scenario: Terminal status rejection -- media buy status is "completed"
    Given the media buy is in "completed" status
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_STATUS"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: Error explains terminal status
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

  @T-UC-003-partition-immutable-fields @partition @immutable_fields
  Scenario Outline: Immutable fields partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with <update_content>
    And the media buy "mb_existing" exists with status "active"
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Valid partitions
      | partition              | update_content                              | outcome |
      | only_updatable_fields  | budget and targeting updates only            | success |

    Examples: Invalid partitions
      | partition                      | update_content                              | outcome                                      |
      | product_id_attempted           | product_id=prod_new (immutable)             | error "SCHEMA_VALIDATION_ERROR" — rejected   |
      | format_ids_attempted           | format_ids=[fmt_new] (immutable)            | error "SCHEMA_VALIDATION_ERROR" — rejected   |
      | pricing_option_id_attempted    | pricing_option_id=po_new (immutable)        | error "SCHEMA_VALIDATION_ERROR" — rejected   |

  @T-UC-003-boundary-immutable-fields @boundary @immutable_fields
  Scenario Outline: Immutable fields boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with <update_content>
    And the media buy "mb_existing" exists with status "active"
    When the Buyer Agent sends the update_media_buy request
    Then the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                  | update_content                           | outcome                                      |
      | package with only updatable fields              | budget and targeting updates only         | success                                      |
      | product_id in update payload (schema rejects)   | product_id=prod_new (immutable)          | error "SCHEMA_VALIDATION_ERROR" — rejected   |
      | format_ids in update payload (schema rejects)   | format_ids=[fmt_new] (immutable)         | error "SCHEMA_VALIDATION_ERROR" — rejected   |

