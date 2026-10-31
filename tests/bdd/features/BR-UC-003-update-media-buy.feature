# Generated from adcp-req @ cac2015cd7436b762053f469b952f94f262cf02f on 2026-08-31T20:30:38Z (merge mode)

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
    And the Buyer is authenticated
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
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
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
    | end_time     | 2099-06-30T23:59:59.000Z |
    And the new end_time is after the existing start_time
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
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
    And the existing media buy has start_time "2099-01-01T00:00:00Z" and end_time "2099-02-01T00:00:00Z"
    And the existing end_time is in the future
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
    And the media buy's stored start_time should be the instant the update took effect
    # POST-S1: Buyer knows start_time updated to current UTC time
    # POST-S4: Unambiguous success
    # LOCAL CORRECTION: POST-S1 was never checked, so a seller that dropped "asap" or stored
    # the literal passed. The buy now starts far in the future, and the stored start must be
    # the instant the update took effect: core/start-timing.json "asap" is "Start campaign as
    # soon as possible", and update-media-buy-response.json implementation_date is the
    # "ISO 8601 timestamp when changes take effect". Both instants are the seller's own clock.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/start-timing.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-response.json

  @T-UC-003-alt-budget @alt-flow @budget @post-s1 @post-s4 @post-s5
  Scenario: Update campaign-level budget
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 25000 |
    And the budget 25000 is greater than zero
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
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
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
    And the response should contain affected_packages including "pkg_001"
    And the package "pkg_001" should have creative assignments [cr_001, cr_002]
    # POST-S1: Buyer knows creatives updated
    # POST-S2: Buyer sees affected packages
    # POST-S4: Unambiguous success
    # POST-S5: Request completed
    # "all placement_ids are valid for the product" now makes the product declare plc_a and
    # plc_b: placement_ids are "routing IDs within already-purchased inventory", and a
    # product declaring no placements has none to route to. The last Then reads the stored
    # assignment set, which is what "replacement semantics" names.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/creative-assignment.json pointer=/properties/placement_ids
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/creative_assignments

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
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
    And the response should contain affected_packages including "pkg_001"
    When the Buyer Agent reads media buy "mb_existing" back with get_media_buys
    Then the package "pkg_001" should report creative approvals for exactly ["inline-cr-001"]
    # POST-S1: Buyer knows inline creatives uploaded
    # POST-S2: Buyer sees affected packages
    # POST-S4: Unambiguous success
    # LOCAL CORRECTION: the generated Thens read only the status and affected_packages, which a
    # seller that never uploaded or assigned the inline creative answers too. The readback is
    # the pin storyboard's: an inline creative on a package update is read back from
    # get_media_buys media_buys[].packages[].creative_approvals[].creative_id.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/creatives
    # @source repo=adcp ref=v3.2.7 path=dist/compliance/3.2.7/protocols/media-buy/scenarios/inline_creatives_without_sync.yaml

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
    And the package "pkg_001" has stored targeting_overlay {"geo_countries": ["GB"]}
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
    And the response should contain affected_packages including "pkg_001"
    And the package "pkg_001" should have stored targeting_overlay geo_countries ["US", "CA"]
    # POST-S1: Buyer knows targeting updated
    # POST-S2: Buyer sees affected packages
    # POST-S4: Unambiguous success
    # POST-S5: Request completed
    # LOCAL CORRECTION: nothing read the overlay back, so a seller that ignored it passed.
    # The package now starts with geo_countries ["GB"], and the stored overlay must carry
    # the requested value in its place: core/targeting-input.json, on update "a non-null
    # value replaces that dimension". The response cannot show it (affected_packages does
    # not carry targeting_overlay), so the record is read.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/targeting-input.json

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
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error should include "suggestion" field
    # REGROUNDED: this seller does not support keyword targeting and never claimed to.
    # core/targeting-overlay-support.json makes Product.overlay_support the binding
    # selectable-targeting contract -- "presence means the seller can apply protocol-valid
    # values" -- and this seller declares none, so non-support is already discoverable.
    # core/targeting.json then requires a fresh create/update to be "applied exactly or
    # rejected", and core/targeting-input.json requires all supplied dimensions to be
    # applied atomically, so the obligation for a seller that cannot apply is a REFUSAL.
    # The previous Then steps asserted success and affected_packages, which is the
    # obligation of a seller that SUPPORTS keyword targeting.
    # POST-F1: System state unchanged -- the refusal precedes the UoW
    # POST-F2: Error code explains failure
    # POST-F3: Suggestion for recovery ("check get_adcp_capabilities and remove
    #          unsupported fields"), which points the buyer at the capability surface

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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error should include "suggestion" field
    # REGROUNDED for the same reason as T-UC-003-alt-keyword-ops above: removal is as
    # unsupported as addition, and core/targeting.json's apply-exactly-or-reject MUST does
    # not distinguish them. This scenario PASSED before, asserting a success this seller
    # answered without applying anything.
    # POST-F1: System state unchanged
    # POST-F2: Error code explains failure

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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error should include "suggestion" field
    # REGROUNDED for the same reason as the two keyword_targets scenarios above.
    # core/targeting-overlay-support.json declares negative_keywords alongside
    # keyword_targets, both $ref keywordSupport, and this seller declares neither.
    # This scenario PASSED before, on a success the seller answered without applying
    # either operation.
    # POST-F1: System state unchanged
    # POST-F2: Error code explains failure

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
    Then the response is compliant with the update_media_buy submitted spec
    And the response status should be "submitted"
    And the response should contain a task_id
    And the response should NOT contain "media_buy_id" field
    And the response should NOT contain "implementation_date" field
    # POST-S7: Buyer knows update awaiting seller approval (status "submitted")
    # POST-S8: Buyer receives a task_id to poll; implementation_date is NOT conveyed on the Submitted envelope

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
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
    And the existing start_time and end_time should remain unchanged
    # BR-RULE-022 INV-1: Field present → updated
    # BR-RULE-022 INV-2: Field omitted → unchanged

  @T-UC-003-empty-update @invariant @BR-RULE-022
  Scenario: Empty update -- no updatable fields specified
    Given the tenant is configured for auto-approval
    And the existing media buy has start_time "2026-04-01T00:00:00Z" and end_time "2026-06-30T23:59:59Z"
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request does not include any updatable fields
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy success spec
    And the response should contain media_buy_id "mb_existing"
    And the response should list no affected packages
    And the existing start_time and end_time should remain unchanged
    # BR-RULE-022 INV-2: Field omitted → unchanged
    # LOCAL CORRECTION: the generated scenario refused this request with INVALID_REQUEST
    # (BR-RULE-022 INV-3, "No updatable fields → rejected"). The pin requires no updatable
    # field: update-media-buy-request.json lists only idempotency_key, account and
    # media_buy_id in /required and has no anyOf/minProperties over the rest, so a request
    # carrying just those three is valid and nothing in the pin refuses it. What the pin
    # does say about such an update is graded instead: an omitted field leaves the buy as
    # it was, and affected_packages names "each modified package" ("Campaign-level updates
    # that do not modify packages may return an empty array"), so it names none here.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json pointer=/required
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-response.json pointer=/oneOf/0/properties/affected_packages

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
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
    # BR-RULE-081 INV-2: Key 8-255 chars accepted

  @T-UC-003-idempotency-absent @invariant @BR-RULE-081 @schema-v3.1
  Scenario: Idempotency key -- absent is now rejected (v3.1 required)
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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # v3.1: root required-set is [idempotency_key, account, media_buy_id], so an absent key
    #       is a schema rejection, not an unprotected update. Reconciled against
    #       adcp/_schemas/3.1/media-buy/update-media-buy-request.json (/required) at the
    #       pinned spec version 3.1.1 (adcp==6.6.0).
    # BR-RULE-081 INV-1 read "key absent → proceeds without idempotency". That obligation
    #       predates the field becoming required; the key is no longer optional, so there is
    #       no unprotected path for it to describe. Same shape as @T-UC-003-account-absent
    #       below, and it agrees with @T-UC-003-bva-idempotency-key's "absent (field not
    #       provided) → error INVALID_REQUEST — rejected (v3.1 requires idempotency_key)".

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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
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
    Then the response is compliant with the update_media_buy success spec
    And the response should contain media_buy_id
    And the response should NOT contain an "errors" field
    # BR-RULE-018 INV-1: Success → no errors field

  @T-UC-003-atomic-error @invariant @BR-RULE-018
  Scenario: Atomic response -- error has no success fields
    Given a valid update_media_buy request with:
    | field        | value          |
    | media_buy_id | mb_nonexistent |
    | paused       | true           |
    And no media buy exists with media_buy_id "mb_nonexistent"
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the response should contain an "errors" array
    And the response should NOT contain "media_buy_id" field
    And the response should NOT contain "buyer_ref" field
    And the response should NOT contain "affected_packages" field
    # BR-RULE-018 INV-2: Error → no success fields
    # LOCAL CORRECTION: the generated request paused the Background's ACTIVE buy, which the
    # pinned state machine permits ("pause" is a valid action for active), so nothing in the
    # scenario produced the error its Thens grade. The request now names a buy the seller
    # does not hold, which the pinned enums/error-code.json answers with MEDIA_BUY_NOT_FOUND
    # ("Referenced media buy does not exist or is not accessible to the requesting agent"),
    # the same trigger @T-UC-003-ext-b uses.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-response.json

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
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
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
    Then the response is compliant with the update_media_buy submitted spec
    And the response status should be "submitted"
    And the response should contain a task_id
    And the response should NOT contain "media_buy_id" field
    And the response should NOT contain "implementation_date" field
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
    Then the response is compliant with the update_media_buy submitted spec
    And the response status should be "submitted"
    And the response should contain a task_id
    And the response should NOT contain "media_buy_id" field
    And the response should NOT contain "implementation_date" field
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
    | budget     | 7500    |
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
    And the package budget should be persisted as 7500
    # BR-RULE-020 INV-1: Adapter success → changes persisted
    # LOCAL CORRECTION: the generated budget was 5000, which is the budget the Background's
    # package is seeded with, so "persisted as 5000" held whether or not the update wrote
    # anything. 7500 differs from the seed, so only a persisted write satisfies the Then.

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
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
    And the package "pkg_001" should have creative assignments [cr_new_1, cr_new_2]
    And the old assignments [cr_old_1, cr_old_2] should be removed
    # BR-RULE-024 INV-2: creative_assignments provided → replaces all existing

  @T-UC-003-ext-a @extension @ext-a @error @post-f1 @post-f2 @post-f3
  Scenario: Authentication error -- no principal in context
    Given the Buyer has no authentication credentials
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | paused       | true        |
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "AUTH_MISSING"
    And the error should include "suggestion" field
    And the suggestion should contain "credentials"
    # LOCAL DIVERGENCE from the generated scenario — mirror upstream in adcp-req.
    # POST-F3 is graded on the SUGGESTION, not the message: CODE_TABLE's AUTH_MISSING
    # suggestion is "provide credentials via the auth header and retry", while its
    # message is "No credentials were presented" — so the incoming branch's companion
    # assertion on the message containing "authentication" is NOT restored with it.
    # POST-F1: System state unchanged
    # POST-F2: Error explains authentication failed
    # POST-F3: Suggestion to obtain credentials

  @T-UC-003-ext-a-unknown @extension @ext-a @error @post-f1 @post-f2 @post-f3
  Scenario: Authentication error -- principal not found in database
    Given the Buyer is authenticated as principal "unknown_principal"
    And the principal "unknown_principal" does not exist in the database
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | paused       | true        |
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "AUTH_MISSING"
    And the error should include "suggestion" field
    # NOTE (#2092, salesagent-otc5, salesagent-z9e0): a principal_id
    # with no backing DB row resolves to identity.principal_id=None (the real
    # resolve_identity() nulls it on a failed token->principal lookup, and the
    # BDD harness's identity_for() now mirrors that — salesagent-z9e0). So
    # require_principal_id fires FIRST with AUTH_MISSING, before
    # update_media_buy's ownership check (MEDIA_BUY_NOT_FOUND, see
    # @T-UC-003-ext-c) is ever reached — that check only
    # fires for a principal_id that resolved but doesn't own the media buy, a
    # genuinely different case from "principal_id never resolved". This now
    # matches the scenario's own title on every transport.
    # POST-F1: System state unchanged
    # POST-F2: Error explains principal not found
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-b @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Media buy not found -- by media_buy_id
    Given a valid update_media_buy request with:
    | field        | value          |
    | media_buy_id | mb_nonexistent |
    | paused       | true           |
    And no media buy exists with media_buy_id "mb_nonexistent"
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "MEDIA_BUY_NOT_FOUND"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: Error explains media buy not found
    # POST-F3: Suggestion to verify ID

  @T-UC-003-ext-c @extension @ext-c @error @post-f1 @post-f2 @post-f3
  Scenario: Ownership mismatch -- principal does not own media buy
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the media buy "mb_existing" exists with owner principal_owner
    And the authenticated principal is principal_other
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "MEDIA_BUY_NOT_FOUND"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: Error explains ownership mismatch
    # POST-F3: Suggestion for recovery
    # LOCAL CORRECTION (mirror upstream in adcp-req), two parts:
    # 1. The generated line asserted ACCOUNT_NOT_FOUND. The pin answers a buy owned by
    #    another principal exactly as an absent buy (@T-UC-003-ext-b): "the body MUST NOT
    #    distinguish 'unauthorized' from 'not found'", and MEDIA_BUY_NOT_FOUND is the code
    #    for a buy that "does not exist or is not accessible to the requesting agent".
    #    The caller's account resolves, so ACCOUNT_NOT_FOUND would name the wrong object.
    # 2. The generated Givens authenticated as a principal with no row, which the resolver
    #    refuses with AUTH_MISSING before ownership is ever checked (the same mechanism
    #    @T-UC-003-ext-a-unknown grades). They now use the ownership outlines' Givens: the
    #    caller exists and can reach the request's account, so ownership is the only
    #    difference from the Background's buyer.
    # @source repo=adcp ref=v3.2.7 path=docs/building/by-layer/L1/security.mdx section="Agent and Account Isolation"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/MEDIA_BUY_NOT_FOUND

  @T-UC-003-ext-d @extension @ext-d @error @post-f1 @post-f2 @post-f3
  Scenario: Budget validation -- campaign budget zero
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 0 |
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "BUDGET_TOO_LOW"
    And the error should include "recovery" field with value "correctable"
    And the error should include "suggestion" field
    And the error should include "field" field with value "packages[0].budget"
    # POST-F1: System state unchanged
    # POST-F2: Error code BUDGET_TOO_LOW
    # POST-F3: Suggestion to provide positive budget

  @T-UC-003-ext-d-negative @extension @ext-d @error @post-f1 @post-f2 @post-f3
  # A NEGATIVE package budget is a SCHEMA violation, not a business-rule one:
  # package-update.json declares budget as {"type": "number", "minimum": 0}, so the SDK model
  # rejects it before any minimum-budget rule can run. BUDGET_TOO_LOW is what a budget that is
  # schema-valid but below the tenant minimum earns; -5 never gets that far. (Whether a schema
  # violation should surface as VALIDATION_ERROR or INVALID_REQUEST is #1604; the
  # spec's enum descriptions say INVALID_REQUEST, and we currently emit VALIDATION_ERROR on
  # mcp/a2a. This row records today's behavior, not an endorsement of the code.)
  Scenario: Budget validation -- package budget negative is rejected by the schema
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | -500 |
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: Error code BUDGET_TOO_LOW
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-e @extension @ext-e @error @post-f1 @post-f2 @post-f3
  Scenario: Date range invalid -- end_time before start_time
    Given the existing media buy has start_time "2099-04-01T00:00:00Z"
    And a valid update_media_buy request with:
    | field        | value                    |
    | media_buy_id | mb_existing              |
    | end_time     | 2099-03-15T00:00:00Z     |
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the response error field is end_time
    And the error should include "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: Error explains date range invalid
    # POST-F3: Suggestion for recovery
    # LOCAL CORRECTION (with -ext-e-equal): POST-F2 was ungraded, so a refusal naming any
    # other field passed; the error field now names end_time, the member the inverted window
    # is refused at. The dates moved from 2026 to 2099, as in @T-UC-003-partition-end-time:
    # a window in the past let a refusal of a past end_time stand in for the inversion.
    # INVALID_REQUEST is the code production answers (refuse_inverted_flight) and the code
    # create_media_buy.mdx "Flight date validation" names: "Out-of-range or inverted dates
    # SHOULD return an INVALID_REQUEST error". The error-compliance storyboard's
    # reversed_dates_error step accepts it ("INVALID_REQUEST is accepted").
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/create_media_buy.mdx
    # @source repo=adcp ref=v3.2.7 path=dist/compliance/3.2.7/universal/error-compliance.yaml pointer=/phases/1/steps/3/narrative

  @T-UC-003-ext-e-equal @extension @ext-e @error @post-f1 @post-f2 @post-f3
  Scenario: Date range invalid -- end_time equals start_time
    Given the existing media buy has start_time "2099-04-01T00:00:00Z"
    And a valid update_media_buy request with:
    | field        | value                    |
    | media_buy_id | mb_existing              |
    | end_time     | 2099-04-01T00:00:00Z     |
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the response error field is end_time
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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: Error explains unsupported currency
    # POST-F3: Suggestion for recovery
    # LOCAL CORRECTION: the generated line asserted INVALID_REQUEST. JPY is a schema-valid
    # ISO 4217 code, so nothing about the request is malformed; the pinned enum's code for
    # "a requested feature or field is not supported by this seller" is UNSUPPORTED_FEATURE,
    # which is what create answers for the same currency gate (@T-UC-002-ext-d).
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/UNSUPPORTED_FEATURE

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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "BUDGET_EXCEEDED"
    And the error should include "recovery" field with value "correctable"
    And the error should include "suggestion" field
    # LOCAL CORRECTION: the generated line asserted BUDGET_TOO_LOW, which the pinned enum
    # defines as "Budget is below the seller's minimum" -- the opposite condition. A budget
    # whose daily spend exceeds the cap is BUDGET_EXCEEDED ("Operation would exceed the
    # allocated budget"), recovery correctable.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/BUDGET_EXCEEDED
    # BR-RULE-012 INV-2: daily budget > cap → rejected
    # POST-F1: System state unchanged
    # POST-F2: Error explains daily cap exceeded
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-h @extension @ext-h @error @post-f1 @post-f2 @post-f3
  Scenario: Missing package ID -- package update without identifier
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update without package_id
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the error should include "field" field with value "packages[0].package_id"
    # REQUEST-ROOTED, per core/error.json: `field` is "JSONPath-lite" and the spec's own
    # example is 'packages[0].targeting'. A bare 'package_id' names a field without saying
    # which package it belonged to, which for a multi-package update is not actionable.
    #
    # This row previously read 'package_id', on the argument that the validator "sees one
    # entry and cannot know its position". That premise no longer holds: the validator
    # raises a pydantic error carrying loc=("package_id",), and pydantic nests it under the
    # enclosing collection, so the index is supplied by pydantic itself. The same change is
    # what gets this rejection an ENVELOPE at all on MCP -- a typed error raised inside a
    # TypeAdapter run was masked by FastMCP into a prose ToolError with no code or field.
    # POST-F1: System state unchanged
    # POST-F2: Error explains missing package identifier
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-i @extension @ext-i @error @post-f1 @post-f2 @post-f3
  Scenario Outline: Creative not found -- referenced creative_id not in library (<array>)
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update references creative "cr_missing" via <array>
    And creative "cr_missing" does not exist in the creative library
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    # adcp 3.1.1 enums/error-code.json: CREATIVE_NOT_FOUND is MANDATED uniformly for
    # any creative_id not owned by the calling account ("never distinguish 'exists in
    # another tenant' from 'does not exist'", anti-enumeration). Was CREATIVE_REJECTED,
    # which the same enum defines as a content-policy review failure.
    And the error code should be "CREATIVE_NOT_FOUND"
    # 3.1.1 L3/error-handling.mdx: error.field MUST name the ARRAY parameter itself,
    # and the two request members that reference creatives are different arrays --
    # which is the whole reason this is an outline rather than one scenario. A single
    # hard-coded pointer would be right for one caller and silently wrong for the other.
    And the response error field is packages[0].<array>
    # Same paragraph: "Sellers MAY enumerate specific unresolvable elements in
    # error.details -- but only when the elements were supplied verbatim by the caller."
    # cr_missing was. Enumerating does not breach the anti-enumeration MUST above,
    # which forbids DISTINGUISHING "exists elsewhere" from "does not exist"; every
    # unresolvable id is listed identically, so the two remain indistinguishable.
    And the wire error details should include missing_creative_ids "cr_missing"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: Error explains creative not found
    # POST-F3: Suggestion for recovery

    Examples: the two request members that reference creatives
      | array                |
      | creative_ids         |
      | creative_assignments |

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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    # adcp 3.1.1: INVALID_STATE is "Operation is not permitted for the resource's
    # current status". CREATIVE_REJECTED is "Creative failed content policy review",
    # whose pinned details shape is {policy_id, policy_url, reasons} — nothing this
    # path can populate.
    And the error code should be "INVALID_STATE"
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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    # INVALID_STATE, even though the creative's STATUS is "rejected": the refusal here
    # is "you cannot assign a creative in this state", not a fresh policy review. The
    # policy rejection already happened and was reported when the status was set.
    And the error code should be "INVALID_STATE"
    And the error should include "suggestion" field
    # BR-RULE-026 INV-3: creative in rejected state → rejected
    # POST-F3: Suggestion for recovery

  # The multi-subject half. Every scenario above drives ONE creative, so a refusal that
  # named only the first would satisfy all of them — the buyer would fix it, resubmit,
  # and meet the second, one round trip per bad creative with no way to know how many
  # remain. adcp 3.1.1 leaves the shape open (core/error.json types `details` as a free
  # object and reserves `issues[]` for per-FIELD schema failures, which a state refusal
  # has no pointer or keyword for), so this grades the per-ENTITY channel this repo
  # uses: details.problems, one entry per creative with the state that disqualified it.
  @T-UC-003-ext-j-multi @extension @ext-j @error @post-f1 @post-f2 @post-f3
  Scenario: Creative validation -- every unassignable creative is named, not just the first
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes creative_assignments with:
    | creative_id |
    | cr_error    |
    | cr_rejected |
    And creative "cr_error" is in "error" state
    And creative "cr_rejected" is in "rejected" state
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_STATE"
    And the error details should name each rejected creative with its state
    # POST-F1: System state unchanged
    # POST-F2: Error explains which creatives blocked the update, and why

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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    # adcp 3.1.1: VALIDATION_ERROR is "violates business rules beyond schema
    # validation". The creative is fine; the ASSIGNMENT is what the product refuses.
    And the error code should be "VALIDATION_ERROR"
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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error should include "suggestion" field
    And the error code should be "SERVICE_UNAVAILABLE"
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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "PACKAGE_NOT_FOUND"
    And the error should include "suggestion" field
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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "VALIDATION_ERROR"
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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error should include "suggestion" field
    And the error code should be "PERMISSION_DENIED"
    # POST-F1: System state unchanged
    # POST-F2: Error explains insufficient privileges
    # POST-F3: Suggestion for recovery

  @T-UC-003-ext-p-short @extension @ext-p @error @post-f1 @post-f2 @post-f3
  Scenario: Idempotency key too short -- below 16 characters
    Given a valid update_media_buy request with:
    | field           | value       |
    | media_buy_id    | mb_existing |
    | idempotency_key | abc1234     |
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # BR-RULE-081 INV-3: key < 16 chars → rejected (schema minLength 16; value/format → VALIDATION_ERROR)
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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # BR-RULE-081 INV-4: key > 255 chars → rejected (schema maxLength 255; value/format → VALIDATION_ERROR)
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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error should include "suggestion" field
    # BR-RULE-083 INV-3 says cross-dimension mixing is WELL-FORMED -- that rule is about the
    # REQUEST's shape, and it still holds. But the request carries keyword_targets_add, which
    # this seller cannot apply and does not declare support for, so it is refused on
    # CAPABILITY before its shape is ever in question. docs/media-buy/specification.mdx
    # orders the checks: "available_actions authorization first, then product capability,
    # then accepted commercial terms, so a request that fails more than one returns the
    # earliest code". A well-formed request for an unsupported capability is still refused.
    #
    # So INV-3's "accepted" outcome is not observable on this seller, and this scenario
    # cannot grade it. It grades the refusal instead, honestly. If keyword targeting is ever
    # implemented (salesagent-8qela.8 chose refusal over implementation), this reverts to
    # asserting acceptance and INV-3 becomes gradable again.

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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error should include "suggestion" field
    # BR-RULE-083 INV-4, the mirror of INV-3 above, and regrounded for the same reason: the
    # request is well-formed and carries negative_keywords_add, which this seller cannot
    # apply. core/targeting-overlay-support.json declares negative_keywords alongside
    # keyword_targets and this seller declares neither, so the refusal is the obligation it
    # holds. INV-4's "accepted" outcome is not observable here either.

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Valid partitions
      | partition      | value                                  | outcome |
      | typical_valid  | abc12345-retry-001                     | success |
      | boundary_min   | 1234567890123456                       | success |
      | boundary_max   | <255 character string>                 | success |
      | uuid_format    | 550e8400-e29b-41d4-a716-446655440000   | success |

    Examples: Invalid partitions
      | partition      | value          | outcome                                              |
      | absent         | <not provided> | error "INVALID_REQUEST" with suggestion              |
      | empty_string   |                | error "INVALID_REQUEST" with suggestion              |
      | too_short      | abc1234        | error "INVALID_REQUEST" with suggestion              |
      | too_long       | <256 character string> | error "INVALID_REQUEST" with suggestion      |
    # The `absent` row moved from Valid to Invalid: v3.1 lists idempotency_key in the root
    # /required set of adcp/_schemas/3.1/media-buy/update-media-buy-request.json, at the
    # pinned spec version 3.1.1 (adcp==6.6.0), so omitting it is a schema rejection.
    # LOCAL CORRECTION: the empty_string row asserted VALIDATION_ERROR. An empty key fails the
    # pinned idempotency_key minLength 16 and pattern, a schema violation, so the code is
    # INVALID_REQUEST -- the same answer the boundary outline's "empty string (length 0)" row
    # already grades.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json pointer=/properties/idempotency_key

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Boundary values
      | boundary_point                  | value               | outcome                                |
      | absent (field not provided)     | <not provided>      | error "INVALID_REQUEST" with suggestion |
      | empty string (length 0)         |                     | error "INVALID_REQUEST" with suggestion |
      | length 15 (min - 1)            | <15 char string>    | error "INVALID_REQUEST" with suggestion |
      | length 16 (min, inclusive)      | <16 char string>    | success                                |
      | length 17 (min + 1)            | <17 char string>    | success                                |
      | length 254 (max - 1)           | <254 char string>   | success                                |
      | length 255 (max, inclusive)     | <255 char string>   | success                                |
      | length 256 (max + 1)           | <256 char string>   | error "INVALID_REQUEST" with suggestion |
    # The absent row said `success`: v3.1 lists idempotency_key in the root /required set of
    # adcp/_schemas/3.1/media-buy/update-media-buy-request.json, at the pinned spec version
    # 3.1.1 (adcp==6.6.0), so an omitted key is rejected rather than served unprotected.

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    # LOCAL CORRECTION: the first valid row was status "pending_activation", which is not a
    # value of the pinned enums/media-buy-status.json. The state gate finds no actions for
    # it in the SDK state machine and then allows every action, so that row could not fail
    # whatever the rule for a buy awaiting its start. It is now "pending_start", the pinned
    # status for that state, which the gate checks: a budget update maps to update_budget
    # and update_packages, and pending_start must allow both for the row to succeed.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/media-buy-status.json

    Examples: Valid partitions
      | partition                  | status               | outcome |
      | status_pending_start       | pending_start        | success |
      | status_active              | active               | success |
      | paused_budget_change       | paused               | accepted |

    Examples: Invalid partitions
      | partition              | status     | outcome                                      |
      | terminal_rejected      | rejected   | error "INVALID_STATE" with suggestion        |
      | terminal_canceled    | canceled   | error "INVALID_STATE" with suggestion        |
      | terminal_completed   | completed  | error "INVALID_STATE" with suggestion        |
    # LOCAL CORRECTION (mirror upstream in adcp-req): paused_packages_locked asserted
    # INVALID_STATE for a package update that changes only the budget. It is now the valid
    # paused_budget_change row, for the reason given on @T-UC-003-boundary-media-buy-status.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/media-buy-valid-action.json pointer=/enumMetadata/update_budget

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Boundary values
      | boundary_point                            | status               | outcome                                  |
      | pending_activation (non-terminal, updatable) | pending_activation | success                                  |
      | active (non-terminal, updatable)          | active               | success                                  |
      | paused (packages locked, budget/dates ok) | paused               | accepted                                 |
      | rejected (terminal, update blocked)       | rejected             | error "INVALID_STATE" with suggestion   |
      | canceled (terminal, update blocked)       | canceled             | error "INVALID_STATE" with suggestion   |
      | completed (terminal, update blocked)      | completed            | error "INVALID_STATE" with suggestion   |
    # LOCAL CORRECTION (mirror upstream in adcp-req): the paused row asserted INVALID_STATE
    # for a request whose only change is packages[].budget, against its own label ("budget/
    # dates ok"). The pin maps packages[].budget to the budget actions (the normative action
    # table; media-buy-valid-action.json lists it in update_budget's update_fields), a paused
    # buy here advertises update_budget, and a seller rejects only an action it does not
    # advertise, with ACTION_NOT_ALLOWED. INVALID_STATE is the code for a terminal buy.
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/update_media_buy.mdx section="Action vocabulary and field mapping"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/media-buy-valid-action.json pointer=/enumMetadata/update_budget

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Valid partitions
      | partition       | amount | outcome                        |
      | positive_amount | 100.00 | success with persisted records |
    # LOCAL CORRECTION: positive_amount asserted a bare "success", which a seller that ignored
    # the budget answers too. package-update.json budget: "Updated hard spend cap for this
    # package"; the row now reads the stored package budget back.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/budget

    Examples: Invalid partitions
      | partition       | amount | outcome                                      |
      | zero_amount     | 0      | error "BUDGET_TOO_LOW" with suggestion        |
      | negative_amount | -50    | error "INVALID_REQUEST" with suggestion       |
    # LOCAL CORRECTION: negative_amount asserted BUDGET_TOO_LOW. The pinned package-update.json
    # gives budget "minimum": 0, so -50 is a schema violation (INVALID_REQUEST); only a
    # schema-valid amount below the seller's minimum, like 0, is BUDGET_TOO_LOW.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/budget

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
    And the tenant sets no min_package_budget
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Boundary values
      | boundary_point                  | amount | outcome                                      |
      | amount = 0 (rejected by rule)   | 0      | error "BUDGET_TOO_LOW" with suggestion        |
      | amount = 0.01 (minimum positive) | 0.01  | success                                      |
      | amount negative                 | -1     | error "INVALID_REQUEST" with suggestion       |
    # LOCAL CORRECTION: "amount negative" asserted BUDGET_TOO_LOW. The pinned package-update.json
    # gives budget "minimum": 0, so -1 is a schema violation, answered INVALID_REQUEST.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/budget
    # LOCAL CORRECTION: the outline grades the request's budget boundaries, which the pin sets
    # at "minimum": 0; a seller's own package minimum is policy layered on top. The seeded
    # tenant's min_package_budget of 100 refused 0.01 BUDGET_TOO_LOW and made the 0 row pass
    # with or without the positivity rule, so the outline clears it.

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Valid partitions
      | partition          | budget | cap_config | flight_days | outcome |
      | below_cap          | 5000   | 1000       | 10          | success |
      | cap_not_configured | 5000   | not set    | 10          | success |
      | at_cap_exactly     | 10000  | 1000       | 10          | success |

    Examples: Invalid partitions
      | partition    | budget | cap_config | flight_days | outcome                                      |
      | exceeds_cap  | 50000  | 1000       | 10          | error "BUDGET_EXCEEDED" with suggestion       |
    # LOCAL CORRECTION: exceeds_cap asserted BUDGET_TOO_LOW ("Budget is below the seller's
    # minimum"). A daily spend above the cap is BUDGET_EXCEEDED in the pinned enum.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/BUDGET_EXCEEDED

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Boundary values
      | boundary_point                       | budget | cap_config | flight_days | outcome                                      |
      | daily budget = cap (at limit)        | 10000  | 1000       | 10          | success                                      |
      | daily budget > cap (exceeds)         | 10001  | 1000       | 10          | error "BUDGET_EXCEEDED" with suggestion       |
      | cap not configured (skipped)         | 99999  | not set    | 10          | success                                      |
      | flight duration 0 days (floor to 1)  | 500    | 1000       | 0           | success                                      |
    # LOCAL CORRECTION: the exceeds row asserted BUDGET_TOO_LOW ("Budget is below the seller's
    # minimum"). A daily spend above the cap is BUDGET_EXCEEDED in the pinned enum.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/BUDGET_EXCEEDED

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Valid partitions
      | partition          | id_config                        | outcome  |
      | media_buy_id_only  | media_buy_id=mb_existing         | accepted |

    Examples: Invalid partitions
      | partition       | id_config                                   | outcome                                      |
      | neither_provided | <none>                                      | error "INVALID_REQUEST" on media_buy_id with suggestion |

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Boundary values
      | boundary_point                       | id_config                                   | outcome                                  |
      | media_buy_id only (primary path)     | media_buy_id=mb_existing                    | accepted                                 |
      | neither identifier (missing)         | <none>                                       | error "INVALID_REQUEST" on media_buy_id with suggestion  |
    # LOCAL CORRECTION (both identification outlines): the success rows ended in bare
    # "success", which any non-error answer satisfies, so a seller that updated some other
    # buy passed; "accepted" requires the success response's media_buy_id to be the buy the
    # request named. The missing-identifier rows pin the field the refusal names: media_buy_id
    # is in the request's required list.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json pointer=/required

  @T-UC-003-partition-frequency-cap-suppress @partition @frequency_cap_suppress
  Scenario Outline: Frequency cap suppress partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field             | value   |
    | package_id        | pkg_001 |
    And the package targeting_overlay includes frequency_cap: <frequency_cap>
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Valid partitions
      | partition                     | frequency_cap                                                                                                                 | outcome                                |
      | typical_minutes               | {"suppress": {"interval": 60, "unit": "minutes"}}                                                                            | accepted with the frequency cap stored |
      | typical_hours                 | {"suppress": {"interval": 24, "unit": "hours"}}                                                                              | accepted with the frequency cap stored |
      | typical_days                  | {"suppress": {"interval": 7, "unit": "days"}}                                                                                | accepted with the frequency cap stored |
      | campaign_full_flight          | {"suppress": {"interval": 1, "unit": "campaign"}}                                                                            | accepted with the frequency cap stored |
      | boundary_min_interval         | {"suppress": {"interval": 1, "unit": "minutes"}}                                                                             | accepted with the frequency cap stored |
      | suppress_with_max_impressions | {"suppress": {"interval": 60, "unit": "minutes"}, "max_impressions": 3, "per": "devices", "window": {"interval": 1, "unit": "days"}} | accepted with the frequency cap stored |
      | deprecated_suppress_minutes   | {"suppress_minutes": 60}                                                                                                     | accepted with the frequency cap stored |
      | suppress_only                 | {"suppress": {"interval": 30, "unit": "minutes"}}                                                                            | accepted with the frequency cap stored |
      | suppress_minutes_zero         | {"suppress_minutes": 0}                                                                                                      | accepted with the frequency cap stored |

    Examples: Invalid partitions
      | partition                    | frequency_cap                                       | outcome                                                                                              |
      | interval_zero                | {"suppress": {"interval": 0, "unit": "minutes"}}    | error "INVALID_REQUEST" on packages[0].targeting_overlay.frequency_cap.suppress.interval with suggestion  |
      | interval_negative            | {"suppress": {"interval": -1, "unit": "minutes"}}   | error "INVALID_REQUEST" on packages[0].targeting_overlay.frequency_cap.suppress.interval with suggestion  |
      | campaign_interval_not_one    | {"suppress": {"interval": 5, "unit": "campaign"}}   | error "VALIDATION_ERROR" on packages[0].targeting_overlay.frequency_cap.suppress.interval with suggestion |
      | unknown_unit                 | {"suppress": {"interval": 7, "unit": "weeks"}}      | error "INVALID_REQUEST" on packages[0].targeting_overlay.frequency_cap.suppress.unit with suggestion      |
      | missing_interval             | {"suppress": {"unit": "minutes"}}                   | error "INVALID_REQUEST" on packages[0].targeting_overlay.frequency_cap.suppress.interval with suggestion  |
      | missing_unit                 | {"suppress": {"interval": 5}}                       | error "INVALID_REQUEST" on packages[0].targeting_overlay.frequency_cap.suppress.unit with suggestion      |
      | no_frequency_control         | {}                                                  | error "INVALID_REQUEST" on packages[0].targeting_overlay.frequency_cap with suggestion                    |
      | wrong_type_suppress          | {"suppress": "60"}                                  | error "INVALID_REQUEST" on packages[0].targeting_overlay.frequency_cap.suppress with suggestion           |
      | suppress_minutes_negative    | {"suppress_minutes": -1}                            | error "INVALID_REQUEST" on packages[0].targeting_overlay.frequency_cap.suppress_minutes with suggestion   |
    # LOCAL CORRECTION (both frequency-cap outlines): the generated rows put a bare Duration
    # ({"interval": 60, "unit": "minutes"}) in place of the WHOLE frequency_cap, which
    # core/frequency-cap.json refuses whatever the Duration holds (its anyOf needs suppress,
    # suppress_minutes or max_impressions, and interval/unit are not its members). The valid
    # Duration rows therefore failed and the invalid ones passed on that unrelated refusal.
    # Each row now sends the whole cap with the Duration under suppress, as the column name
    # always meant. Success rows read the stored cap back; error rows name the code and the
    # field the pin's own constraint sits on (duration.json interval minimum 1 and unit enum,
    # frequency-cap.json anyOf and suppress_minutes minimum 0). A campaign Duration whose
    # interval is not 1 is schema-valid and refused by the rule in duration.json's interval
    # description, so it is VALIDATION_ERROR, the code get_media_buy_delivery already gives
    # the same Duration rule (BR-UC-004 campaign_interval_not_one).
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/frequency-cap.json pointer=/anyOf
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/duration.json pointer=/properties/interval
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/VALIDATION_ERROR

  @T-UC-003-boundary-frequency-cap-suppress @boundary @frequency_cap_suppress
  Scenario Outline: Frequency cap suppress boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field             | value   |
    | package_id        | pkg_001 |
    And the package targeting_overlay includes frequency_cap: <frequency_cap>
    And the package "pkg_001" exists in the media buy
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                                    | frequency_cap                                                                                                                 | outcome                                                                                              |
      | interval = 0 (below minimum)                                     | {"suppress": {"interval": 0, "unit": "minutes"}}                                                                             | error "INVALID_REQUEST" on packages[0].targeting_overlay.frequency_cap.suppress.interval with suggestion  |
      | interval = 1 (boundary minimum)                                  | {"suppress": {"interval": 1, "unit": "minutes"}}                                                                             | accepted with the frequency cap stored                                                               |
      | interval = -1 (negative)                                         | {"suppress": {"interval": -1, "unit": "minutes"}}                                                                            | error "INVALID_REQUEST" on packages[0].targeting_overlay.frequency_cap.suppress.interval with suggestion  |
      | unit=campaign, interval=1                                        | {"suppress": {"interval": 1, "unit": "campaign"}}                                                                            | accepted with the frequency cap stored                                                               |
      | unit=campaign, interval=2                                        | {"suppress": {"interval": 2, "unit": "campaign"}}                                                                            | error "VALIDATION_ERROR" on packages[0].targeting_overlay.frequency_cap.suppress.interval with suggestion |
      | unit = 'minutes'                                                 | {"suppress": {"interval": 60, "unit": "minutes"}}                                                                            | accepted with the frequency cap stored                                                               |
      | unit = 'hours'                                                   | {"suppress": {"interval": 24, "unit": "hours"}}                                                                              | accepted with the frequency cap stored                                                               |
      | unit = 'days'                                                    | {"suppress": {"interval": 7, "unit": "days"}}                                                                                | accepted with the frequency cap stored                                                               |
      | unit = 'campaign'                                                | {"suppress": {"interval": 1, "unit": "campaign"}}                                                                            | accepted with the frequency cap stored                                                               |
      | unit = 'weeks' (unknown)                                         | {"suppress": {"interval": 7, "unit": "weeks"}}                                                                               | error "INVALID_REQUEST" on packages[0].targeting_overlay.frequency_cap.suppress.unit with suggestion      |
      | suppress present, suppress_minutes absent, max_impressions absent | {"suppress": {"interval": 30, "unit": "minutes"}}                                                                           | accepted with the frequency cap stored                                                               |
      | suppress absent, suppress_minutes=60                             | {"suppress_minutes": 60}                                                                                                     | accepted with the frequency cap stored                                                               |
      | all three absent (empty frequency_cap)                           | {}                                                                                                                            | error "INVALID_REQUEST" on packages[0].targeting_overlay.frequency_cap with suggestion                    |
      | suppress + max_impressions both present (AND semantics)          | {"suppress": {"interval": 60, "unit": "minutes"}, "max_impressions": 3, "per": "devices", "window": {"interval": 1, "unit": "days"}} | accepted with the frequency cap stored                                                        |
      | suppress_minutes = -1 (below minimum 0)                          | {"suppress_minutes": -1}                                                                                                     | error "INVALID_REQUEST" on packages[0].targeting_overlay.frequency_cap.suppress_minutes with suggestion   |
      | suppress_minutes = 0 (boundary minimum)                          | {"suppress_minutes": 0}                                                                                                      | accepted with the frequency cap stored                                                               |
      | suppress is string '60' (wrong type)                             | {"suppress": "60"}                                                                                                            | error "INVALID_REQUEST" on packages[0].targeting_overlay.frequency_cap.suppress with suggestion           |
      | suppress = {} (missing both fields)                              | {"suppress": {}}                                                                                                              | error "INVALID_REQUEST" on packages[0].targeting_overlay.frequency_cap.suppress.interval with suggestion  |

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Valid partitions
      | partition                        | goals_value                                                                                                                                                             | outcome |
      | single_metric_goal               | [{"kind": "metric", "metric": "clicks"}]                                                                                                                                | error UNSUPPORTED_FEATURE with suggestion |
      | single_event_goal                | [{"kind": "event", "event_sources": [{"event_source_id": "pixel-1", "event_type": "purchase"}]}]                                                                        | error UNSUPPORTED_FEATURE with suggestion |
      | multiple_mixed_goals             | [{"kind": "metric", "metric": "clicks", "priority": 1}, {"kind": "event", "event_sources": [{"event_source_id": "px", "event_type": "purchase"}], "priority": 2}]      | error UNSUPPORTED_FEATURE with suggestion |
      | metric_with_cost_per_target      | [{"kind": "metric", "metric": "clicks", "target": {"kind": "cost_per", "value": 0.50}}]                                                                                 | error UNSUPPORTED_FEATURE with suggestion |
      | metric_with_threshold_rate       | [{"kind": "metric", "metric": "views", "target": {"kind": "threshold_rate", "value": 0.70}}]                                                                            | error UNSUPPORTED_FEATURE with suggestion |
      | event_with_roas_target           | [{"kind": "event", "event_sources": [{"event_source_id": "px", "event_type": "purchase", "value_field": "value"}], "target": {"kind": "per_ad_spend", "value": 4.0}}]   | error UNSUPPORTED_FEATURE with suggestion |
      | event_with_maximize_value        | [{"kind": "event", "event_sources": [{"event_source_id": "px", "event_type": "purchase", "value_field": "value"}], "target": {"kind": "maximize_value"}}]                | error UNSUPPORTED_FEATURE with suggestion |
      | reach_with_frequency             | [{"kind": "metric", "metric": "reach", "reach_unit": "individuals", "target_frequency": {"min": 1, "max": 3, "window": {"interval": 7, "unit": "days"}}}]              | error UNSUPPORTED_FEATURE with suggestion |
      | completed_views_with_duration    | [{"kind": "metric", "metric": "completed_views", "view_duration_seconds": 15}]                                                                                          | error UNSUPPORTED_FEATURE with suggestion |
      | event_with_attribution_window    | [{"kind": "event", "event_sources": [{"event_source_id": "px", "event_type": "purchase"}], "attribution_window": {"post_click": {"interval": 7, "unit": "days"}, "post_view": {"interval": 1, "unit": "days"}}}] | error UNSUPPORTED_FEATURE with suggestion |
      | event_multi_source_dedup         | [{"kind": "event", "event_sources": [{"event_source_id": "pixel", "event_type": "purchase", "value_field": "value"}, {"event_source_id": "api", "event_type": "purchase", "value_field": "order_total", "value_factor": 0.01}]}] | error UNSUPPORTED_FEATURE with suggestion |
      | goals_with_explicit_priorities   | [{"kind": "metric", "metric": "clicks", "priority": 1}, {"kind": "metric", "metric": "views", "priority": 2}]                                                          | error UNSUPPORTED_FEATURE with suggestion |
      | refund_event_with_negative_factor | [{"kind": "event", "event_sources": [{"event_source_id": "px", "event_type": "refund", "value_field": "value", "value_factor": -1}]}]                                   | error UNSUPPORTED_FEATURE with suggestion |

    Examples: Invalid partitions
      | partition              | goals_value                                                                                                        | outcome                                      |
      | empty_array            | []                                                                                                                  | error INVALID_REQUEST on packages[0].optimization_goals with suggestion |
      | missing_kind           | [{"metric": "clicks"}]                                                                                              | error INVALID_REQUEST on packages[0].optimization_goals[0] with suggestion |
      | invalid_kind           | [{"kind": "custom"}]                                                                                                | error INVALID_REQUEST on packages[0].optimization_goals[0] with suggestion |
      | metric_missing_metric  | [{"kind": "metric"}]                                                                                                | error INVALID_REQUEST on packages[0].optimization_goals[0].metric with suggestion |
      | invalid_metric_enum    | [{"kind": "metric", "metric": "conversions"}]                                                                       | error INVALID_REQUEST on packages[0].optimization_goals[0].metric with suggestion |
      | event_missing_sources  | [{"kind": "event"}]                                                                                                 | error INVALID_REQUEST on packages[0].optimization_goals[0].event_sources with suggestion |
      | event_sources_empty    | [{"kind": "event", "event_sources": []}]                                                                            | error INVALID_REQUEST on packages[0].optimization_goals[0].event_sources with suggestion |
      | target_value_zero      | [{"kind": "metric", "metric": "clicks", "target": {"kind": "cost_per", "value": 0}}]                                | error INVALID_REQUEST on packages[0].optimization_goals[0].target.value with suggestion |
      | target_value_negative  | [{"kind": "metric", "metric": "clicks", "target": {"kind": "cost_per", "value": -1.0}}]                             | error INVALID_REQUEST on packages[0].optimization_goals[0].target.value with suggestion |
      | priority_zero          | [{"kind": "metric", "metric": "clicks", "priority": 0}]                                                             | error INVALID_REQUEST on packages[0].optimization_goals[0].priority with suggestion |
      | priority_negative      | [{"kind": "metric", "metric": "clicks", "priority": -1}]                                                            | error INVALID_REQUEST on packages[0].optimization_goals[0].priority with suggestion |
      | view_duration_zero     | [{"kind": "metric", "metric": "completed_views", "view_duration_seconds": 0}]                                       | error INVALID_REQUEST on packages[0].optimization_goals[0].view_duration_seconds with suggestion |
      | event_source_id_empty  | [{"kind": "event", "event_sources": [{"event_source_id": "", "event_type": "purchase"}]}]                            | error INVALID_REQUEST on packages[0].optimization_goals[0].event_sources[0].event_source_id with suggestion |
    # REGROUNDED to the pin. The product this outline seeds declares neither
    # metric_optimization nor conversion_tracking, and core/product.json makes those the
    # capability: metric_optimization "Presence indicates the product supports
    # optimization_goals with kind: 'metric'", and "event optimization goals are only
    # accepted on products with conversion_tracking". So the schema-valid goals (the
    # "Valid partitions", which expected success) are refused with UNSUPPORTED_FEATURE.
    # The schema-invalid goals are refused before that, by the request schema, as
    # INVALID_REQUEST on the member that broke it (package-update.json minItems 1;
    # optimization-goal.json kind discriminator, metric enum, event_sources minItems 1,
    # target.value exclusiveMinimum 0, priority minimum 1, view_duration_seconds
    # exclusiveMinimum 0, event_source_id minLength 1). The bare "error with suggestion"
    # these rows carried was also satisfied by the UNSUPPORTED_FEATURE refusal, so it
    # could not tell a schema rejection from the capability one.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/metric_optimization
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/conversion_tracking
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/optimization-goal.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/optimization_goals

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    # LOCAL CORRECTION. Each row now names where the request stops, so the boundary it
    # names is what is graded. Below a schema boundary the request schema refuses the goal:
    # INVALID_REQUEST ("violates schema constraints") on the member the constraint sits on
    # (optimization_goals minItems 1; kind the discriminator; priority minimum 1;
    # target.value and view_duration_seconds exclusiveMinimum 0; event_sources minItems 1).
    # At or above it the goal is well-formed and reaches the seller, which refuses it:
    # core/product.json makes optimization support a product declaration ("Presence
    # indicates the product supports optimization_goals with kind: 'metric'", and the same
    # for conversion_tracking and kind: 'event'), the seeded product declares neither, and
    # this seller applies no goals, so an update replacing them is answered
    # UNSUPPORTED_FEATURE, as @T-UC-003-alt-keyword-ops answers keyword targeting. These
    # rows asserted "success" for a seller that supports goals and "error with suggestion"
    # for any refusal, so the capability refusal satisfied every error row whatever the goal.
    # The omitted row sends a package update without goals, which nothing refuses and which
    # modifies no package.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/optimization-goal.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/optimization_goals
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/metric_optimization
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/conversion_tracking
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/UNSUPPORTED_FEATURE

    Examples: Boundary values
      | boundary_point                                          | goals_value                                                                                                                                                                         | outcome                  |
      | empty array (0 items)                                   | []                                                                                                                                                                                   | error "INVALID_REQUEST" on packages[0].optimization_goals with suggestion |
      | single goal (1 item — boundary min)                     | [{"kind": "metric", "metric": "clicks"}]                                                                                                                                            | error "UNSUPPORTED_FEATURE" with suggestion |
      | many goals (no maxItems)                                | [{"kind": "metric", "metric": "clicks", "priority": 1}, {"kind": "event", "event_sources": [{"event_source_id": "px", "event_type": "purchase"}], "priority": 2}]                   | error "UNSUPPORTED_FEATURE" with suggestion |
      | kind = 'metric'                                         | [{"kind": "metric", "metric": "clicks"}]                                                                                                                                            | error "UNSUPPORTED_FEATURE" with suggestion |
      | kind = 'event'                                          | [{"kind": "event", "event_sources": [{"event_source_id": "pixel-1", "event_type": "purchase"}]}]                                                                                    | error "UNSUPPORTED_FEATURE" with suggestion |
      | kind omitted                                            | [{"metric": "clicks"}]                                                                                                                                                               | error "INVALID_REQUEST" on packages[0].optimization_goals[0] with suggestion |
      | kind = unknown string                                   | [{"kind": "custom"}]                                                                                                                                                                 | error "INVALID_REQUEST" on packages[0].optimization_goals[0] with suggestion |
      | priority = 0                                            | [{"kind": "metric", "metric": "clicks", "priority": 0}]                                                                                                                             | error "INVALID_REQUEST" on packages[0].optimization_goals[0].priority with suggestion |
      | priority = 1 (boundary min)                             | [{"kind": "metric", "metric": "clicks", "priority": 1}, {"kind": "metric", "metric": "views", "priority": 2}]                                                                      | error "UNSUPPORTED_FEATURE" with suggestion |
      | priority omitted                                        | [{"kind": "metric", "metric": "clicks"}]                                                                                                                                            | error "UNSUPPORTED_FEATURE" with suggestion |
      | duplicate priorities (same value on two goals)          | [{"kind": "metric", "metric": "clicks", "priority": 1}, {"kind": "metric", "metric": "views", "priority": 1}]                                                                      | error "UNSUPPORTED_FEATURE" with suggestion |
      | target.value = 0 (exclusive minimum)                    | [{"kind": "metric", "metric": "clicks", "target": {"kind": "cost_per", "value": 0}}]                                                                                                | error "INVALID_REQUEST" on packages[0].optimization_goals[0].target.value with suggestion |
      | target.value = 0.001 (just above zero)                  | [{"kind": "metric", "metric": "clicks", "target": {"kind": "cost_per", "value": 0.001}}]                                                                                            | error "UNSUPPORTED_FEATURE" with suggestion |
      | target omitted                                          | [{"kind": "metric", "metric": "clicks"}]                                                                                                                                            | error "UNSUPPORTED_FEATURE" with suggestion |
      | event_sources empty array                               | [{"kind": "event", "event_sources": []}]                                                                                                                                             | error "INVALID_REQUEST" on packages[0].optimization_goals[0].event_sources with suggestion |
      | event_sources single entry (boundary min)               | [{"kind": "event", "event_sources": [{"event_source_id": "pixel-1", "event_type": "purchase"}]}]                                                                                    | error "UNSUPPORTED_FEATURE" with suggestion |
      | event_sources multiple entries                          | [{"kind": "event", "event_sources": [{"event_source_id": "pixel", "event_type": "purchase", "value_field": "value"}, {"event_source_id": "api", "event_type": "purchase", "value_field": "order_total", "value_factor": 0.01}]}] | error "UNSUPPORTED_FEATURE" with suggestion |
      | view_duration_seconds = 0                               | [{"kind": "metric", "metric": "completed_views", "view_duration_seconds": 0}]                                                                                                       | error "INVALID_REQUEST" on packages[0].optimization_goals[0].view_duration_seconds with suggestion |
      | view_duration_seconds = 0.001 (just above zero)         | [{"kind": "metric", "metric": "completed_views", "view_duration_seconds": 0.001}]                                                                                                   | error "UNSUPPORTED_FEATURE" with suggestion |
      | optimization_goals present — replaces all               | [{"kind": "metric", "metric": "clicks"}]                                                                                                                                            | error "UNSUPPORTED_FEATURE" with suggestion |
      | optimization_goals omitted — preserves existing         | <not provided>                                                                                                                                                                       | accepted with no package modified |

  # REGROUNDED: the four keyword partition outlines in this section (keyword_targets_add,
  # keyword_targets_remove, negative_keywords_add, negative_keywords_remove).
  # Valid rows: core/targeting.json says of keyword_targets and of negative_keywords
  # "Seller must declare support in get_adcp_capabilities", and of every fresh request "A
  # fresh create/update request MUST be applied exactly or rejected". This seller declares
  # neither dimension (the reason @T-UC-003-alt-keyword-ops records), so a protocol-valid
  # keyword operation is refused with UNSUPPORTED_FEATURE. These rows used to expect bare
  # success, which is the obligation of a seller that supports keyword targeting.
  # Invalid rows: each breaks one package-update.json constraint (minItems 1, keyword
  # minLength 1, required keyword and match_type, the match-type enum, bid_price minimum 0).
  # The request DTO refuses them at the boundary, before the capability refusal, so each row
  # names INVALID_REQUEST and the field the wire carries. Their former "error with
  # suggestion" accepted any refusal, so the UNSUPPORTED_FEATURE every keyword operation
  # receives satisfied it whether or not the constraint was enforced.
  # conflict_with_overlay is REMOVED from each outline. Its sentinel put the overlay
  # dimension on the package, and the outline's own Given "no targeting_overlay.<dimension>
  # is present in the same package update" then removed it, so the row never sent the
  # conflict. targeting.mdx "Managing keywords after launch" states the conflict, and
  # @T-UC-003-ext-r-keyword, @T-UC-003-ext-r-negative and the boundary outlines' WITH-overlay
  # rows send it, with no such Given.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/targeting.json
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json
  # @source repo=adcp ref=v3.2.7 path=docs/media-buy/advanced-topics/targeting.mdx
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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Valid partitions
      | partition              | kw_value                                                                                                                                | outcome                                     |
      | typical_add            | [{"keyword": "running shoes", "match_type": "broad"}, {"keyword": "nike sneakers", "match_type": "exact"}]                              | error "UNSUPPORTED_FEATURE" with suggestion |
      | boundary_min_array     | [{"keyword": "laptop", "match_type": "phrase"}]                                                                                         | error "UNSUPPORTED_FEATURE" with suggestion |
      | boundary_min_keyword   | [{"keyword": "a", "match_type": "broad"}]                                                                                               | error "UNSUPPORTED_FEATURE" with suggestion |
      | add_with_bid_price     | [{"keyword": "shoes", "match_type": "exact", "bid_price": 2.50}]                                                                        | error "UNSUPPORTED_FEATURE" with suggestion |
      | add_without_bid_price  | [{"keyword": "shoes", "match_type": "broad"}]                                                                                           | error "UNSUPPORTED_FEATURE" with suggestion |
      | upsert_existing        | [{"keyword": "shoes", "match_type": "exact", "bid_price": 3.00}]                                                                        | error "UNSUPPORTED_FEATURE" with suggestion |
      | zero_bid_price         | [{"keyword": "shoes", "match_type": "broad", "bid_price": 0}]                                                                           | error "UNSUPPORTED_FEATURE" with suggestion |
      | all_match_types        | [{"keyword": "shoes", "match_type": "broad"}, {"keyword": "shoes", "match_type": "phrase"}, {"keyword": "shoes", "match_type": "exact"}] | error "UNSUPPORTED_FEATURE" with suggestion |
      | cross_dimension_valid  | [{"keyword": "shoes", "match_type": "broad"}]                                                                                           | error "UNSUPPORTED_FEATURE" with suggestion |

    Examples: Invalid partitions
      | partition              | kw_value                                                          | outcome                                                                         |
      | empty_array            | []                                                                | error "INVALID_REQUEST" on packages[0].keyword_targets_add with suggestion              |
      | empty_keyword          | [{"keyword": "", "match_type": "broad"}]                          | error "INVALID_REQUEST" on packages[0].keyword_targets_add[0].keyword with suggestion   |
      | missing_keyword        | [{"match_type": "broad"}]                                         | error "INVALID_REQUEST" on packages[0].keyword_targets_add[0].keyword with suggestion   |
      | missing_match_type     | [{"keyword": "shoes"}]                                            | error "INVALID_REQUEST" on packages[0].keyword_targets_add[0].match_type with suggestion |
      | invalid_match_type     | [{"keyword": "shoes", "match_type": "fuzzy"}]                     | error "INVALID_REQUEST" on packages[0].keyword_targets_add[0].match_type with suggestion |
      | negative_bid_price     | [{"keyword": "shoes", "match_type": "exact", "bid_price": -1.00}] | error "INVALID_REQUEST" on packages[0].keyword_targets_add[0].bid_price with suggestion  |

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                                            | kw_value                                                                     | outcome                  |
      | array length 0 (empty)                                                    | []                                                                            | error INVALID_REQUEST on packages[0].keyword_targets_add with suggestion |
      | array length 1 (minimum valid)                                            | [{"keyword": "laptop", "match_type": "phrase"}]                               | error UNSUPPORTED_FEATURE with suggestion |
      | keyword length 0 (empty string)                                           | [{"keyword": "", "match_type": "broad"}]                                      | error INVALID_REQUEST on packages[0].keyword_targets_add[0].keyword with suggestion |
      | keyword length 1 (single char)                                            | [{"keyword": "a", "match_type": "broad"}]                                     | error UNSUPPORTED_FEATURE with suggestion |
      | match_type = 'broad'                                                      | [{"keyword": "shoes", "match_type": "broad"}]                                 | error UNSUPPORTED_FEATURE with suggestion |
      | match_type = 'phrase'                                                     | [{"keyword": "shoes", "match_type": "phrase"}]                                | error UNSUPPORTED_FEATURE with suggestion |
      | match_type = 'exact'                                                      | [{"keyword": "shoes", "match_type": "exact"}]                                 | error UNSUPPORTED_FEATURE with suggestion |
      | match_type = 'unknown'                                                    | [{"keyword": "shoes", "match_type": "unknown"}]                               | error INVALID_REQUEST on packages[0].keyword_targets_add[0].match_type with suggestion |
      | bid_price = -0.01                                                         | [{"keyword": "shoes", "match_type": "exact", "bid_price": -0.01}]             | error INVALID_REQUEST on packages[0].keyword_targets_add[0].bid_price with suggestion |
      | bid_price = 0                                                             | [{"keyword": "shoes", "match_type": "broad", "bid_price": 0}]                 | error UNSUPPORTED_FEATURE with suggestion |
      | bid_price = 0.01                                                          | [{"keyword": "shoes", "match_type": "exact", "bid_price": 0.01}]              | error UNSUPPORTED_FEATURE with suggestion |
      | keyword_targets_add WITH targeting_overlay.keyword_targets                | <with overlay present>                                                        | error UNSUPPORTED_FEATURE with suggestion |
      | keyword_targets_add WITHOUT targeting_overlay.keyword_targets             | [{"keyword": "shoes", "match_type": "broad"}]                                 | error UNSUPPORTED_FEATURE with suggestion |
      | keyword_targets_add WITH targeting_overlay.negative_keywords (cross-dimension) | <with targeting_overlay.negative_keywords present>                     | error UNSUPPORTED_FEATURE with suggestion |
    # REGROUNDED to the pin for this seller, which declares no keyword targeting.
    # targeting.mdx: "Seller must declare execution.targeting.keyword_targets in
    # get_adcp_capabilities"; core/targeting.json: a fresh create/update "MUST be applied
    # exactly or rejected". So every schema-valid keyword_targets_add is refused with
    # UNSUPPORTED_FEATURE (the rows that said success), the same answer
    # @T-UC-003-alt-keyword-ops gives. Each schema-invalid row is refused earlier, by the
    # request schema, as INVALID_REQUEST on the member that broke it (minItems 1, keyword
    # minLength 1, match-type enum, bid_price minimum 0); the bare "error with suggestion"
    # these rows carried was satisfied by the UNSUPPORTED_FEATURE refusal too, so it could
    # not tell the two apart. Both overlay rows now send their overlay (the cross-dimension
    # row used to send none). targeting.mdx's SHOULD-level conflict check for
    # targeting_overlay.keyword_targets beside keyword_targets_add is not reachable on a
    # seller that applies neither, so the WITH row is refused as unsupported too.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/keyword_targets_add
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/advanced-topics/targeting.mdx section="keyword_targets"
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/advanced-topics/targeting.mdx section="Managing keywords after launch"

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Valid partitions
      | partition            | kw_value                                                                                                                                | outcome                                     |
      | typical_remove       | [{"keyword": "running shoes", "match_type": "broad"}, {"keyword": "nike sneakers", "match_type": "exact"}]                              | error "UNSUPPORTED_FEATURE" with suggestion |
      | boundary_min_array   | [{"keyword": "laptop", "match_type": "phrase"}]                                                                                         | error "UNSUPPORTED_FEATURE" with suggestion |
      | boundary_min_keyword | [{"keyword": "a", "match_type": "broad"}]                                                                                               | error "UNSUPPORTED_FEATURE" with suggestion |
      | remove_nonexistent   | [{"keyword": "nonexistent", "match_type": "exact"}]                                                                                     | error "UNSUPPORTED_FEATURE" with suggestion |
      | all_match_types      | [{"keyword": "shoes", "match_type": "broad"}, {"keyword": "shoes", "match_type": "phrase"}, {"keyword": "shoes", "match_type": "exact"}] | error "UNSUPPORTED_FEATURE" with suggestion |
      | cross_dimension_valid | [{"keyword": "shoes", "match_type": "broad"}]                                                                                          | error "UNSUPPORTED_FEATURE" with suggestion |

    Examples: Invalid partitions
      | partition              | kw_value                                      | outcome                                                                                     |
      | empty_array            | []                                            | error "INVALID_REQUEST" on packages[0].keyword_targets_remove with suggestion               |
      | empty_keyword          | [{"keyword": "", "match_type": "broad"}]      | error "INVALID_REQUEST" on packages[0].keyword_targets_remove[0].keyword with suggestion    |
      | missing_keyword        | [{"match_type": "broad"}]                     | error "INVALID_REQUEST" on packages[0].keyword_targets_remove[0].keyword with suggestion    |
      | missing_match_type     | [{"keyword": "shoes"}]                        | error "INVALID_REQUEST" on packages[0].keyword_targets_remove[0].match_type with suggestion |
      | invalid_match_type     | [{"keyword": "shoes", "match_type": "fuzzy"}] | error "INVALID_REQUEST" on packages[0].keyword_targets_remove[0].match_type with suggestion |

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                                                | kw_value                                           | outcome                  |
      | array length 0 (empty)                                                        | []                                                  | error INVALID_REQUEST on packages[0].keyword_targets_remove with suggestion |
      | array length 1 (minimum valid)                                                | [{"keyword": "laptop", "match_type": "phrase"}]     | error UNSUPPORTED_FEATURE with suggestion |
      | keyword length 0 (empty string)                                               | [{"keyword": "", "match_type": "broad"}]             | error INVALID_REQUEST on packages[0].keyword_targets_remove[0].keyword with suggestion |
      | keyword length 1 (single char)                                                | [{"keyword": "a", "match_type": "broad"}]            | error UNSUPPORTED_FEATURE with suggestion |
      | match_type = 'broad'                                                          | [{"keyword": "shoes", "match_type": "broad"}]        | error UNSUPPORTED_FEATURE with suggestion |
      | match_type = 'phrase'                                                         | [{"keyword": "shoes", "match_type": "phrase"}]       | error UNSUPPORTED_FEATURE with suggestion |
      | match_type = 'exact'                                                          | [{"keyword": "shoes", "match_type": "exact"}]        | error UNSUPPORTED_FEATURE with suggestion |
      | match_type = 'unknown'                                                        | [{"keyword": "shoes", "match_type": "unknown"}]      | error INVALID_REQUEST on packages[0].keyword_targets_remove[0].match_type with suggestion |
      | keyword_targets_remove WITH targeting_overlay.keyword_targets                 | <with overlay present>                               | error UNSUPPORTED_FEATURE with suggestion |
      | keyword_targets_remove WITHOUT targeting_overlay.keyword_targets              | [{"keyword": "shoes", "match_type": "broad"}]        | error UNSUPPORTED_FEATURE with suggestion |
      | remove pair that exists in current list                                       | [{"keyword": "shoes", "match_type": "broad"}]        | error UNSUPPORTED_FEATURE with suggestion |
      | remove pair that does NOT exist in current list (no-op)                       | [{"keyword": "nonexistent", "match_type": "exact"}]  | error UNSUPPORTED_FEATURE with suggestion |
    # REGROUNDED on the grounds given under @T-UC-003-boundary-keyword-targets-add: removal
    # is as unsupported as addition on this seller (@T-UC-003-alt-keyword-remove), so a
    # schema-valid keyword_targets_remove is UNSUPPORTED_FEATURE and a schema-invalid one is
    # INVALID_REQUEST on the broken member. The exists / no-op pair is the SHOULD of a seller
    # that holds a keyword list, which this one does not; both rows are refused alike.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/keyword_targets_remove

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Valid partitions
      | partition              | nk_value                                                                                                                                | outcome                                     |
      | typical_add            | [{"keyword": "cheap", "match_type": "broad"}, {"keyword": "free shipping", "match_type": "phrase"}]                                     | error "UNSUPPORTED_FEATURE" with suggestion |
      | boundary_min_array     | [{"keyword": "discount", "match_type": "exact"}]                                                                                        | error "UNSUPPORTED_FEATURE" with suggestion |
      | boundary_min_keyword   | [{"keyword": "x", "match_type": "broad"}]                                                                                               | error "UNSUPPORTED_FEATURE" with suggestion |
      | add_duplicate          | [{"keyword": "cheap", "match_type": "broad"}]                                                                                           | error "UNSUPPORTED_FEATURE" with suggestion |
      | all_match_types        | [{"keyword": "free", "match_type": "broad"}, {"keyword": "free", "match_type": "phrase"}, {"keyword": "free", "match_type": "exact"}]   | error "UNSUPPORTED_FEATURE" with suggestion |
      | cross_dimension_valid  | [{"keyword": "cheap", "match_type": "broad"}]                                                                                           | error "UNSUPPORTED_FEATURE" with suggestion |

    Examples: Invalid partitions
      | partition              | nk_value                                      | outcome                                                                                    |
      | empty_array            | []                                            | error "INVALID_REQUEST" on packages[0].negative_keywords_add with suggestion               |
      | empty_keyword          | [{"keyword": "", "match_type": "broad"}]      | error "INVALID_REQUEST" on packages[0].negative_keywords_add[0].keyword with suggestion    |
      | missing_keyword        | [{"match_type": "broad"}]                     | error "INVALID_REQUEST" on packages[0].negative_keywords_add[0].keyword with suggestion    |
      | missing_match_type     | [{"keyword": "cheap"}]                        | error "INVALID_REQUEST" on packages[0].negative_keywords_add[0].match_type with suggestion |
      | invalid_match_type     | [{"keyword": "cheap", "match_type": "fuzzy"}] | error "INVALID_REQUEST" on packages[0].negative_keywords_add[0].match_type with suggestion |

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                                                  | nk_value                                             | outcome                  |
      | array length 0 (empty)                                                          | []                                                    | error INVALID_REQUEST on packages[0].negative_keywords_add with suggestion |
      | array length 1 (minimum valid)                                                  | [{"keyword": "discount", "match_type": "exact"}]      | error UNSUPPORTED_FEATURE with suggestion |
      | keyword length 0 (empty string)                                                 | [{"keyword": "", "match_type": "broad"}]               | error INVALID_REQUEST on packages[0].negative_keywords_add[0].keyword with suggestion |
      | keyword length 1 (single char)                                                  | [{"keyword": "x", "match_type": "broad"}]              | error UNSUPPORTED_FEATURE with suggestion |
      | match_type = 'broad'                                                            | [{"keyword": "cheap", "match_type": "broad"}]          | error UNSUPPORTED_FEATURE with suggestion |
      | match_type = 'phrase'                                                           | [{"keyword": "cheap", "match_type": "phrase"}]         | error UNSUPPORTED_FEATURE with suggestion |
      | match_type = 'exact'                                                            | [{"keyword": "cheap", "match_type": "exact"}]          | error UNSUPPORTED_FEATURE with suggestion |
      | match_type = 'unknown'                                                          | [{"keyword": "cheap", "match_type": "unknown"}]        | error INVALID_REQUEST on packages[0].negative_keywords_add[0].match_type with suggestion |
      | negative_keywords_add WITH targeting_overlay.negative_keywords                  | <with overlay present>                                 | error UNSUPPORTED_FEATURE with suggestion |
      | negative_keywords_add WITHOUT targeting_overlay.negative_keywords               | [{"keyword": "cheap", "match_type": "broad"}]          | error UNSUPPORTED_FEATURE with suggestion |
      | add pair that already exists in list (duplicate no-op)                          | [{"keyword": "cheap", "match_type": "broad"}]          | error UNSUPPORTED_FEATURE with suggestion |
      | negative_keywords_add WITH targeting_overlay.keyword_targets (cross-dimension)  | <with targeting_overlay.keyword_targets present>       | error UNSUPPORTED_FEATURE with suggestion |
    # REGROUNDED to the pin for this seller, which declares no negative-keyword support
    # (targeting.mdx: "Seller must declare execution.targeting.negative_keywords"), on the
    # grounds given under @T-UC-003-boundary-keyword-targets-add: schema-valid adds are
    # refused UNSUPPORTED_FEATURE, as @T-UC-003-alt-negative-keywords is; schema-invalid
    # ones are INVALID_REQUEST on the broken member (minItems 1, keyword minLength 1,
    # match-type enum). The duplicate no-op is the SHOULD of a seller that applies the
    # list, which this one does not. The cross-dimension row now sends its overlay.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/negative_keywords_add
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/advanced-topics/targeting.mdx section="negative_keywords"

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Valid partitions
      | partition              | nk_value                                                                                                                                | outcome                                     |
      | typical_remove         | [{"keyword": "cheap", "match_type": "broad"}, {"keyword": "free shipping", "match_type": "phrase"}]                                     | error "UNSUPPORTED_FEATURE" with suggestion |
      | boundary_min_array     | [{"keyword": "discount", "match_type": "exact"}]                                                                                        | error "UNSUPPORTED_FEATURE" with suggestion |
      | boundary_min_keyword   | [{"keyword": "x", "match_type": "broad"}]                                                                                               | error "UNSUPPORTED_FEATURE" with suggestion |
      | remove_nonexistent     | [{"keyword": "nonexistent", "match_type": "exact"}]                                                                                     | error "UNSUPPORTED_FEATURE" with suggestion |
      | all_match_types        | [{"keyword": "free", "match_type": "broad"}, {"keyword": "free", "match_type": "phrase"}, {"keyword": "free", "match_type": "exact"}]   | error "UNSUPPORTED_FEATURE" with suggestion |
      | cross_dimension_valid  | [{"keyword": "cheap", "match_type": "broad"}]                                                                                           | error "UNSUPPORTED_FEATURE" with suggestion |

    Examples: Invalid partitions
      | partition              | nk_value                                      | outcome                                                                                       |
      | empty_array            | []                                            | error "INVALID_REQUEST" on packages[0].negative_keywords_remove with suggestion               |
      | empty_keyword          | [{"keyword": "", "match_type": "broad"}]      | error "INVALID_REQUEST" on packages[0].negative_keywords_remove[0].keyword with suggestion    |
      | missing_keyword        | [{"match_type": "broad"}]                     | error "INVALID_REQUEST" on packages[0].negative_keywords_remove[0].keyword with suggestion    |
      | missing_match_type     | [{"keyword": "cheap"}]                        | error "INVALID_REQUEST" on packages[0].negative_keywords_remove[0].match_type with suggestion |
      | invalid_match_type     | [{"keyword": "cheap", "match_type": "fuzzy"}] | error "INVALID_REQUEST" on packages[0].negative_keywords_remove[0].match_type with suggestion |

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Boundary values
      | boundary_point                                                                      | nk_value                                              | outcome                  |
      | array length 0 (empty)                                                              | []                                                     | error INVALID_REQUEST on packages[0].negative_keywords_remove with suggestion |
      | array length 1 (minimum valid)                                                      | [{"keyword": "discount", "match_type": "exact"}]       | error UNSUPPORTED_FEATURE with suggestion |
      | keyword length 0 (empty string)                                                     | [{"keyword": "", "match_type": "broad"}]                | error INVALID_REQUEST on packages[0].negative_keywords_remove[0].keyword with suggestion |
      | keyword length 1 (single char)                                                      | [{"keyword": "x", "match_type": "broad"}]               | error UNSUPPORTED_FEATURE with suggestion |
      | match_type = 'broad'                                                                | [{"keyword": "cheap", "match_type": "broad"}]           | error UNSUPPORTED_FEATURE with suggestion |
      | match_type = 'phrase'                                                               | [{"keyword": "cheap", "match_type": "phrase"}]          | error UNSUPPORTED_FEATURE with suggestion |
      | match_type = 'exact'                                                                | [{"keyword": "cheap", "match_type": "exact"}]           | error UNSUPPORTED_FEATURE with suggestion |
      | match_type = 'unknown'                                                              | [{"keyword": "cheap", "match_type": "unknown"}]         | error INVALID_REQUEST on packages[0].negative_keywords_remove[0].match_type with suggestion |
      | negative_keywords_remove WITH targeting_overlay.negative_keywords                   | <with overlay present>                                  | error UNSUPPORTED_FEATURE with suggestion |
      | negative_keywords_remove WITHOUT targeting_overlay.negative_keywords                | [{"keyword": "cheap", "match_type": "broad"}]           | error UNSUPPORTED_FEATURE with suggestion |
      | remove pair that exists in current list                                             | [{"keyword": "cheap", "match_type": "broad"}]           | error UNSUPPORTED_FEATURE with suggestion |
      | remove pair that does NOT exist in current list (no-op)                             | [{"keyword": "nonexistent", "match_type": "exact"}]     | error UNSUPPORTED_FEATURE with suggestion |
    # REGROUNDED on the grounds given under @T-UC-003-boundary-negative-keywords-add:
    # a schema-valid negative_keywords_remove is UNSUPPORTED_FEATURE on this seller
    # (@T-UC-003-alt-negative-keywords) and a schema-invalid one is INVALID_REQUEST on the
    # broken member; the exists / no-op pair is refused alike.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/negative_keywords_remove

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
    And an earlier update gave package "pkg_001" the targeting_overlay {"geo_regions": ["US-NY"]}
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Valid partitions
      | partition                        | overlay_value                                                                     | outcome |
      | absent_overlay                   | <not provided>                                                                    | success with the stored overlay patched per dimension |
      | valid_overlay                    | {"geo_countries": ["US", "CA"]}                                                   | success with the stored overlay patched per dimension |
      | empty_overlay                    | {}                                                                                | success with the stored overlay patched per dimension |
      | single_geo_dimension             | {"geo_countries": ["US"]}                                                         | success with the stored overlay patched per dimension |
      | multiple_dimensions              | {"geo_countries": ["US"], "device_platform": ["iOS"]}                              | success |
      | frequency_cap_suppress_only      | {"frequency_cap": {"suppress": {"interval": 30, "unit": "minutes"}}}              | success with the stored overlay patched per dimension |
      | frequency_cap_max_impressions_only | {"frequency_cap": {"max_impressions": 3, "per": "devices", "window": {"interval": 1, "unit": "days"}}} | success with the stored overlay patched per dimension |
      | frequency_cap_combined           | {"frequency_cap": {"suppress": {"interval": 60, "unit": "minutes"}, "max_impressions": 3, "per": "devices", "window": {"interval": 1, "unit": "days"}}} | success with the stored overlay patched per dimension |
      | proximity_travel_time            | {"geo_proximity": [{"travel_time": 30, "transport_mode": "driving"}]}             | success |
      | proximity_radius                 | {"geo_proximity": [{"radius": 10}]}                                               | success |
      | proximity_geometry               | {"geo_proximity": [{"geometry": "precomputed-poly-id"}]}                          | success |

    Examples: Invalid partitions
      | partition                    | overlay_value                                                                          | outcome                                      |
      | unknown_field                | {"nonexistent_field": ["value"]}                                                       | error "INVALID_REQUEST" with suggestion       |
      | undeclared_dimension         | {"publisher_managed_dim": ["value"]}                                                   | error "INVALID_REQUEST" with suggestion       |
      | geo_overlap                  | {"geo_countries": ["US"], "geo_countries_exclude": ["US"]}                              | error "INVALID_REQUEST" with suggestion       |
      | device_type_overlap          | {"device_type": ["mobile"], "device_type_exclude": ["mobile"]}                         | error "INVALID_REQUEST" with suggestion       |
      | proximity_method_conflict    | {"geo_proximity": [{"lat": 40.7128, "lng": -74.006, "travel_time": {"value": 30, "unit": "min"}, "transport_mode": "driving", "radius": {"value": 10, "unit": "km"}}]} | error "INVALID_REQUEST" with suggestion |
      | frequency_cap_missing_fields | {"frequency_cap": {"max_impressions": 3}}                                              | error "INVALID_REQUEST" with suggestion       |
      | keyword_duplicate            | {"keyword_targets": [{"keyword": "shoes", "match_type": "broad"}, {"keyword": "shoes", "match_type": "broad"}]} | error "INVALID_REQUEST" with suggestion |
      | keyword_targeting            | {"keyword_targets": [{"keyword": "shoes", "match_type": "broad"}]}                     | error "UNSUPPORTED_FEATURE" on packages[0].targeting_overlay.keyword_targets with suggestion |
      | stored_geo_overlap           | {"geo_regions_exclude": ["US-NY"]}                                                     | error "INVALID_REQUEST" with suggestion       |
    # stored_geo_overlap: the sent overlay alone has no overlap, but it patches the package's
    # effective targeting, which already holds geo_regions ["US-NY"] from the earlier update.
    # core/targeting.json geo_regions_exclude: "values also present in geo_regions are
    # invalid", so the patched result is refused rather than stored.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/targeting.json pointer=/properties/geo_regions_exclude
    # LOCAL CORRECTION, three parts.
    # (1) The valid rows asserted a bare "success", which a seller that dropped the overlay
    # answers too. An update's overlay is a per-dimension patch (update_media_buy.mdx, Package
    # Update Object: "Omit the overlay or a dimension inside it to leave that dimension
    # unchanged; provide a non-null value to replace it"), so the package first holds a
    # geo_regions value from an earlier update and each row reads back the stored overlay: the
    # sent dimensions as sent, geo_regions kept. That is what makes absent_overlay and
    # empty_overlay observable at all.
    # (2) keyword_targeting expected acceptance. core/targeting.json keyword_targets: "Seller
    # must declare support in get_adcp_capabilities", and get-adcp-capabilities-response.json
    # keyword_targets: "Presence indicates support for targeting_overlay.keyword_targets";
    # this seller declares none, so the pinned answer is UNSUPPORTED_FEATURE on the dimension
    # ("A fresh create/update request MUST be applied exactly or rejected").
    # (3) proximity_method_conflict sent travel_time and radius as bare numbers, a shape
    # targeting.json refuses whatever the conflict rule, so the row passed without it. It now
    # sends a pin-shaped entry ({value, unit} objects from lat/lng) that breaks only the
    # geo_proximity item oneOf (one method per entry).
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/update_media_buy.mdx section="Package Update Object"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/targeting_overlay
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/targeting.json pointer=/properties/keyword_targets
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/targeting.json pointer=/properties/geo_proximity/items/oneOf
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

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
    And an earlier update gave package "pkg_001" the targeting_overlay {"geo_regions": ["US-NY"]}
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Boundary values
      | boundary_point                               | overlay_value                                                                          | outcome                                  |
      | absent overlay                               | <not provided>                                                                         | success with the stored overlay patched per dimension |
      | empty {} overlay                             | {}                                                                                     | success with the stored overlay patched per dimension |
      | valid known fields                           | {"geo_countries": ["US"]}                                                              | success with the stored overlay patched per dimension |
      | unknown field name                           | {"nonexistent_field": ["value"]}                                                       | error "INVALID_REQUEST" with suggestion  |
      | undeclared dimension                         | {"publisher_managed_dim": ["value"]}                                                   | error "INVALID_REQUEST" with suggestion  |
      | geo include/exclude overlap                  | {"geo_countries": ["US"], "geo_countries_exclude": ["US"]}                              | error "INVALID_REQUEST" with suggestion  |
      | device_type include/exclude overlap           | {"device_type": ["mobile"], "device_type_exclude": ["mobile"]}                        | error "INVALID_REQUEST" with suggestion  |
      | geo_proximity with travel_time only          | {"geo_proximity": [{"travel_time": 30, "transport_mode": "driving"}]}                  | success                                  |
      | geo_proximity with radius only               | {"geo_proximity": [{"radius": 10}]}                                                   | success                                  |
      | geo_proximity with geometry only             | {"geo_proximity": [{"geometry": "precomputed-poly-id"}]}                               | success                                  |
      | geo_proximity with travel_time AND radius    | {"geo_proximity": [{"lat": 40.7128, "lng": -74.006, "travel_time": {"value": 30, "unit": "min"}, "transport_mode": "driving", "radius": {"value": 10, "unit": "km"}}]} | error "INVALID_REQUEST" with suggestion |
      | frequency_cap suppress only                  | {"frequency_cap": {"suppress": {"interval": 30, "unit": "minutes"}}}                  | success with the stored overlay patched per dimension |
      | frequency_cap max_impressions with per+window | {"frequency_cap": {"max_impressions": 3, "per": "devices", "window": {"interval": 1, "unit": "days"}}} | success with the stored overlay patched per dimension |
      | frequency_cap max_impressions without per    | {"frequency_cap": {"max_impressions": 3}}                                              | error "INVALID_REQUEST" with suggestion  |
      | keyword_targets with unique tuples           | {"keyword_targets": [{"keyword": "shoes", "match_type": "broad"}]}                     | error "UNSUPPORTED_FEATURE" on packages[0].targeting_overlay.keyword_targets with suggestion |
      | keyword_targets with duplicate (keyword, match_type) | {"keyword_targets": [{"keyword": "shoes", "match_type": "broad"}, {"keyword": "shoes", "match_type": "broad"}]} | error "INVALID_REQUEST" with suggestion |
    # LOCAL CORRECTION, for the reasons the partition outline above records: the success rows
    # read back the stored overlay patched per dimension over an earlier geo_regions value;
    # "keyword_targets with unique tuples" is refused UNSUPPORTED_FEATURE by a seller that
    # declares no keyword targeting; and "geo_proximity with travel_time AND radius" sends a
    # pin-shaped entry, so only the item oneOf refuses it.
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/update_media_buy.mdx section="Package Update Object"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/targeting.json pointer=/properties/keyword_targets
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/targeting.json pointer=/properties/geo_proximity/items/oneOf

  @T-UC-003-partition-start-time @partition @start_time
  Scenario Outline: Start time partition validation - <partition>
    Given the existing media buy has start_time "2026-01-01T00:00:00Z"
    And a valid update_media_buy request with:
    | field        | value        |
    | media_buy_id | mb_existing  |
    | start_time   | <start_value> |
    And the existing end_time is in the future
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Valid partitions
      | partition              | start_value              | outcome                   |
      | asap_literal           | asap                     | start_time stored as now  |
      | future_iso_datetime    | {7 days from now}        | start_time stored as sent |
      | past_datetime          | 2020-01-01T00:00:00Z     | start_time stored as sent |

    Examples: Invalid partitions
      | partition             | start_value          | outcome                                               |
      | wrong_case_asap       | ASAP                 | error "INVALID_REQUEST" on start_time with suggestion |
      | future_naive_datetime | 2026-05-01T00:00:00  | error "INVALID_REQUEST" with suggestion               |
    # LOCAL CORRECTION: future_naive_datetime sat with the valid partitions and expected
    # success. The pinned core/start-timing.json admits the const "asap" or a string of
    # format date-time, which is RFC 3339 and requires a UTC offset, so a naive timestamp
    # is a schema violation: INVALID_REQUEST.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/start-timing.json
    # LOCAL CORRECTION (start time on UPDATE), shared with @T-UC-003-boundary-start-time:
    # - past_datetime expected a refusal. The pin's past-start refusal is scoped to new buys
    #   (create_media_buy.mdx "Flight date validation": "For new media buys, the top-level
    #   start_time MUST be either "asap" or a date-time that is not in the past"), and
    #   update_media_buy.mdx lists no such refusal: start_time is "Updated campaign start
    #   time" and a start shift is the update_flight_dates action. A schema-valid start_time
    #   the pin does not refuse is applied, so the row grades that it was.
    # - the valid rows said only "success", which a seller that dropped start_time passes.
    #   They now read the stored buy: "asap" is resolved to the instant the update was
    #   applied, and a concrete date-time is stored as sent. The Given seeds a start far
    #   from either, so an ignored start_time cannot match. The future value is relative,
    #   because a fixed date expires (2026-05-01 had).
    # - wrong_case_asap names its code and field: "ASAP" is neither the const "asap" nor a
    #   date-time, which is a schema violation on start_time.
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/create_media_buy.mdx
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/update_media_buy.mdx

  @T-UC-003-boundary-start-time @boundary @start_time
  Scenario Outline: Start time boundary validation - <boundary_point>
    Given the existing media buy has start_time "2026-01-01T00:00:00Z"
    And a valid update_media_buy request with:
    | field        | value        |
    | media_buy_id | mb_existing  |
    | start_time   | <start_value> |
    And the existing end_time is in the future
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    # LOCAL CORRECTION: see @T-UC-003-partition-start-time for the success rows and the past
    # row. The row that read "absent (null)" sent an empty string (the table cell is empty),
    # so it is named for what it sends; "" is neither "asap" nor a date-time.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/start-timing.json

    Examples: Boundary values
      | boundary_point          | start_value              | outcome                                               |
      | literal 'asap'          | asap                     | start_time stored as now                              |
      | future ISO datetime     | {7 days from now}        | start_time stored as sent                             |
      | past datetime           | 2020-01-01T00:00:00Z     | start_time stored as sent                             |
      | 'ASAP' wrong case       | ASAP                     | error "INVALID_REQUEST" on start_time with suggestion |
      | empty string            |                          | error "INVALID_REQUEST" on start_time with suggestion |

  @T-UC-003-partition-end-time @partition @end_time
  Scenario Outline: End time partition validation - <partition>
    Given the existing media buy has start_time "2099-04-01T00:00:00Z"
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | end_time     | <end_value>  |
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    # LOCAL CORRECTION: the invalid rows said `error with suggestion`, which names no code
    # and accepted any refusal, so a seller refusing for a reason other than the inverted
    # window passed them. They now name the code and the field. The pinned
    # create_media_buy.mdx "Flight date validation" says "Out-of-range or inverted dates
    # SHOULD return an INVALID_REQUEST error"; the pinned update page states no other code
    # for the same rule, and @T-UC-003-ext-e / -ext-e-equal grade INVALID_REQUEST already.
    # The dates moved from 2026 to 2099: a window in the past let a refusal of a past
    # end_time stand in for the inversion this outline grades.
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/create_media_buy.mdx
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json

    Examples: Valid partitions
      | partition          | end_value                | outcome |
      | after_start_time   | 2099-06-30T23:59:59Z     | success |

    Examples: Invalid partitions
      | partition          | end_value                | outcome                                         |
      | equal_to_start     | 2099-04-01T00:00:00Z     | error "INVALID_REQUEST" on end_time with suggestion |
      | before_start       | 2099-03-15T00:00:00Z     | error "INVALID_REQUEST" on end_time with suggestion |

  @T-UC-003-boundary-end-time @boundary @end_time
  Scenario Outline: End time boundary validation - <boundary_point>
    Given the existing media buy has start_time "2099-04-01T00:00:00Z" and end_time "2099-05-31T23:59:59Z"
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | end_time     | <end_value>  |
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    # LOCAL CORRECTION: the rows were graded by "error with suggestion", which accepts any
    # refusal, and by a bare "success", which reads nothing back. The refusal rows now pin
    # the code and the field: INVALID_REQUEST on end_time, the one code for this rule that
    # create and update share (refuse_inverted_flight) and that @T-UC-003-partition-end-time
    # and @T-UC-003-ext-e / -ext-e-equal grade for the same inputs. create_media_buy.mdx
    # "Flight date validation": inverted dates "SHOULD return an INVALID_REQUEST error". The
    # error-compliance storyboard's reversed_dates_error step (a create_media_buy step) calls
    # VALIDATION_ERROR canonical but states "INVALID_REQUEST is accepted", so INVALID_REQUEST
    # is the code every pinned text accepts. The accepted rows read the stored flight. The
    # dates moved to 2099 so the only defect in a refused row is the order of the two
    # times, and the seeded end_time differs from every requested one.
    # LOCAL CORRECTION: the "absent (null)" row sent end_time "", which is neither absent nor
    # null, and expected a refusal. end_time is optional on update ("No" in the request
    # table, and absent from the request schema's required list), so an update that omits
    # it is accepted and leaves the stored end_time as it was.
    # @source repo=adcp ref=v3.2.7 path=dist/compliance/3.2.7/universal/error-compliance.yaml pointer=/phases/1/steps/3/narrative
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/create_media_buy.mdx section="Flight date validation"
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/update_media_buy.mdx (request table: end_time optional)
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json pointer=/required

    Examples: Boundary values
      | boundary_point                   | end_value                | outcome                                             |
      | end_time after start_time        | 2099-06-30T23:59:59Z     | accepted with the requested end_time stored         |
      | end_time = start_time (rejected) | 2099-04-01T00:00:00Z     | error "INVALID_REQUEST" on end_time with suggestion |
      | end_time before start_time       | 2099-03-15T00:00:00Z     | error "INVALID_REQUEST" on end_time with suggestion |
      | absent (omitted)                 | (omitted)                | accepted with the stored flight unchanged           |

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    And the package "pkg_001" should have creative assignments [<stored>]

    Examples: Valid partitions
      | partition                    | mode                                                      | stored                       | outcome |
      | replace_all_existing         | creative_assignments=[{cr_new_1}, {cr_new_2}]             | cr_new_1, cr_new_2           | success |
      | creative_assignments_replace | creative_assignments=[{cr_new_1, weight:70}]              | cr_new_1:70                  | success |
      | add_new_creative             | creative_assignments=[{cr_old_1}, {cr_old_2}, {cr_new_3}] | cr_old_1, cr_old_2, cr_new_3 | success |
      | remove_existing_creative     | creative_assignments=[{cr_old_1}]                         | cr_old_1                     | success |
    # LOCAL CORRECTION (mirror upstream in adcp-req), two parts:
    # 1. Three rows sent packages[].creative_ids, which the pinned package-update.json does
    #    not declare. Replacement goes through creative_assignments ("Replace creative
    #    assignments for this package ... Uses replacement semantics"), so every row now uses
    #    it; creative_ids_replace is renamed replace_all_existing for that reason.
    # 2. Every row ended in a bare success, which a seller that appended, or ignored the
    #    array, also produced. Each row now names the assignment set the seller must store,
    #    and the weighted row the stored weight.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/creative_assignments

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    And the package "pkg_001" should have creative assignments [<stored>]

    Examples: Boundary values
      | boundary_point                            | mode                                                       | stored                       | outcome |
      | new set replaces all existing              | creative_assignments=[{cr_new_1}, {cr_new_2}]              | cr_new_1, cr_new_2           | success |
      | creative_assignments replaces all          | creative_assignments=[{cr_new_1, weight:70}]               | cr_new_1:70                  | success |
      | new creative added (not in existing)       | creative_assignments=[{cr_old_1}, {cr_old_2}, {cr_new_3}]  | cr_old_1, cr_old_2, cr_new_3 | success |
      | existing creative removed (not in new)     | creative_assignments=[{cr_old_1}]                          | cr_old_1                     | success |
    # LOCAL CORRECTION (mirror upstream in adcp-req), two parts:
    # 1. Three rows sent packages[].creative_ids, which the pinned package-update.json does
    #    not declare. Replacement goes through creative_assignments ("Replace creative
    #    assignments for this package ... Uses replacement semantics"), so every row now uses
    #    it; "creative_ids replaces all existing" is renamed "new set replaces all existing".
    # 2. Every row ended in a bare success, which a seller that appended, or ignored the
    #    array, also produced. Each row now names the assignment set the seller must store,
    #    and the weighted row the stored weight.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/creative_assignments

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Valid partitions
      | partition                              | creative_state      | outcome |
      | all_valid_state_compatible_format      | approved            | success |

    Examples: Invalid partitions
      | partition            | creative_state  | outcome                                        |
      | error_state          | error           | error "INVALID_STATE" with suggestion          |
      | format_incompatible  | wrong_format    | error "VALIDATION_ERROR" with suggestion       |

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Boundary values
      | boundary_point                              | creative_state  | outcome                                        |
      | all creatives valid state and format         | approved        | success                                        |
      | creative in error state                     | error           | error "INVALID_STATE" with suggestion          |
      | format incompatible with product            | wrong_format    | error "VALIDATION_ERROR" with suggestion       |

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Valid partitions
      | partition               | placement_config                       | outcome                                                           |
      | all_valid_for_product   | placement_ids=[plc_a, plc_b] (valid)   | success with each assignment's placement_ids stored as requested |
      | no_placement_ids        | no placement_ids specified             | success with each assignment's placement_ids stored as requested |

    Examples: Invalid partitions
      | partition                    | placement_config                              | outcome                                          |
      | invalid_placement_id         | placement_ids=[plc_invalid] (not in product)  | error "VALIDATION_ERROR" with suggestion     |
      | product_no_placement_support | placement_ids=[plc_a] (product unsupported)   | error "UNSUPPORTED_FEATURE" with suggestion     |
    # LOCAL CORRECTION: the two valid rows read "success", which checked only that some
    # response came back. A placement_id routes a creative "within already-purchased
    # inventory", so the success the rows claim is the routing the seller recorded: the
    # outcome now reads the package's stored assignments back. The product declares
    # plc_a and plc_b (mode included, which every package buys) on every row but the
    # unsupported one, so the rows differ in the product as well as in the request.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/creative-assignment.json pointer=/properties/placement_ids
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/placement.json pointer=/properties/mode

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Boundary values
      | boundary_point                       | placement_config                              | outcome                                      |
      | all placement IDs valid              | placement_ids=[plc_a, plc_b] (valid)          | success                                      |
      | no placement IDs (untargeted)        | no placement_ids specified                    | success                                      |
      | product without placement support    | placement_ids=[plc_a] (product unsupported)   | error "UNSUPPORTED_FEATURE" with suggestion |

  @T-UC-003-partition-adapter-dispatch @partition @adapter_dispatch
  Scenario Outline: Adapter dispatch partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes <update_fields>
    And the media buy "mb_existing" exists with status "active"
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    # LOCAL CORRECTION, two kinds. (1) The valid rows asserted bare "success", which a seller
    # that dropped the update also earns; they now read the stored package back ("success
    # with persisted records": the requested budget, and every targeting dimension sent).
    # (2) empty_update expected INVALID_REQUEST, which the pin does not require: the request
    # schema's required list is idempotency_key, account and media_buy_id, with no anyOf or
    # minProperties over the updatable fields. A request carrying none is accepted, and
    # modifies no package ("affected_packages ... for each modified package").
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json pointer=/required
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-response.json pointer=/oneOf/0/properties/affected_packages

    Examples: Valid partitions
      | partition            | update_fields                          | outcome                        |
      | single_field_update  | 1 package with budget update only      | success with persisted records |
      | multi_field_update   | 1 package with budget and targeting    | success with persisted records |

    Examples: A request carrying no updatable field
      | partition     | update_fields                    | outcome                           |
      | empty_update  | no updatable fields in request   | accepted with no package modified |

  @T-UC-003-boundary-adapter-dispatch @boundary @adapter_dispatch
  Scenario Outline: Adapter dispatch boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes <update_config>
    And the media buy "mb_existing" exists with status "active"
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    # LOCAL CORRECTION, for the reasons @T-UC-003-partition-adapter-dispatch states: the
    # success rows read the stored package back, and the zero-field row is accepted
    # without a package modified. The all-fields request pauses the active buy rather than
    # resuming it: the action table maps paused false to resume, which an active buy does
    # not offer, and maps paused true to pause, which it does. One request carrying the
    # pause with the package and flight changes is one atomic mutation, so every change in
    # it is applied.
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/update_media_buy.mdx (Action vocabulary and field mapping; Compatibility transaction boundary)
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/media-buys/lifecycle.mdx (Discovering valid actions at runtime)

    Examples: Boundary values
      | boundary_point                              | update_config                          | outcome                           |
      | request with exactly one updatable field    | 1 package with budget update only      | success with persisted records    |
      | request with all updatable fields           | packages with all updatable fields     | success with persisted records    |
      | request with zero updatable fields          | no updatable fields in request         | accepted with no package modified |

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Valid partitions
      | partition                       | approval_mode  | adapter_result   | outcome                          |
      | auto_approve_adapter_success    | auto-approval  | returns success  | success with persisted records   |
      | manual_approval_pending         | manual         | not yet called   | success with pending status      |

    Examples: Invalid partitions
      | partition                       | approval_mode  | adapter_result   | outcome                          |
      | auto_approve_adapter_failure    | auto-approval  | returns error    | error "SERVICE_UNAVAILABLE" — no records persisted |

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Boundary values
      | boundary_point                              | approval_mode  | adapter_result   | outcome                                       |
      | adapter returns success (auto-approval)     | auto-approval  | returns success  | success with persisted records                |
      | adapter returns error (auto-approval)       | auto-approval  | returns error    | error "SERVICE_UNAVAILABLE" — no records persisted  |
      | manual approval detected (pending state)    | manual         | not yet called   | success with pending status                   |

  @T-UC-003-partition-principal-ownership @partition @principal_ownership
  Scenario Outline: Principal ownership partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the media buy "mb_existing" exists with owner <owner>
    And the authenticated principal is <principal>
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Valid partitions
      | partition      | owner       | principal   | outcome |
      | owner_matches  | buyer_001   | buyer_001   | success |

    Examples: Invalid partitions
      | partition       | owner       | principal   | outcome                                        |
      | owner_mismatch  | buyer_001   | buyer_999   | error "MEDIA_BUY_NOT_FOUND" with suggestion    |
    # LOCAL CORRECTION (mirror upstream in adcp-req): the generated row asserted
    # PERMISSION_DENIED, which tells the caller the buy exists. The pin forbids that
    # answer: a buy the caller cannot access is answered as an absent one (@T-UC-003-ext-b).
    # @source repo=adcp ref=v3.2.7 path=docs/building/by-layer/L1/security.mdx section="Agent and Account Isolation"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/MEDIA_BUY_NOT_FOUND

  @T-UC-003-boundary-principal-ownership @boundary @principal_ownership
  Scenario Outline: Principal ownership boundary validation - <boundary_point>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the media buy "mb_existing" exists with owner <owner>
    And the authenticated principal is <principal>
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: Boundary values
      | boundary_point                    | owner       | principal   | outcome                                        |
      | principal matches owner           | buyer_001   | buyer_001   | success                                        |
      | principal differs from owner      | buyer_001   | buyer_999   | error "MEDIA_BUY_NOT_FOUND" with suggestion    |
    # LOCAL CORRECTION (mirror upstream in adcp-req): PERMISSION_DENIED -> MEDIA_BUY_NOT_FOUND,
    # for the reason given on @T-UC-003-partition-principal-ownership.
    # @source repo=adcp ref=v3.2.7 path=docs/building/by-layer/L1/security.mdx section="Agent and Account Isolation"

  @T-UC-003-v31-error-budget-too-low @extension @ext-d @error @v3.1 @error-details @post-f1 @post-f2 @post-f3
  Scenario: BUDGET_TOO_LOW error carries v3.1 details shape (minimum_budget + currency)
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 100 |
    And the seller's minimum budget for this media buy is 500 USD
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "BUDGET_TOO_LOW"
    And the error "details" object should include "minimum_budget" with value 500
    And the error "details" object should include "currency" with value "USD"
    And the "currency" value should match ISO 4217 alphabetic format
    And the error should include a "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: Error code BUDGET_TOO_LOW with structured details
    # POST-F3: Buyer Agent can adjust budget without re-querying products (suggestion guides the fix)
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-v31-error-conflict-version @error @v3.1 @error-details @concurrency @ext-s @post-f1 @post-f2 @post-f3
  Scenario: CONFLICT error carries v3.1 details shape (resource_id + expected/current version)
    Given the media buy "mb_existing" is at revision 7
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | paused       | true        |
    And the request revision is set to 5
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "CONFLICT"
    And the wire error details should include resource_id "mb_existing"
    And the wire error details should include expected_version "5"
    And the wire error details should include current_version "7"
    And the error should include a "suggestion" field
    # POST-F1: System state unchanged (no partial write)
    # POST-F2: CONFLICT details enable optimistic-concurrency retry
    # POST-F3: Buyer Agent re-reads at current_version and retries (suggestion guides the fix)
    # LOCAL CORRECTION: the generated Givens named a "version" and a buyer-side "last-read
    # version", neither of which the pinned update request can carry. The pin's only
    # optimistic-concurrency token on update_media_buy is `revision`: "Expected current
    # revision for optimistic concurrency ... sellers MUST reject the update with CONFLICT if
    # the media buy's current revision does not match". The Givens now seed the buy's
    # revision and send the stale one, through the steps @T-UC-003-partition-revision uses.
    # The details keys are error-details/conflict.json's: resource_id, expected_version
    # ("Version or ETag the client was operating against") and current_version, read off the
    # wire rather than off a reconstructed exception.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json pointer=/properties/revision
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/error-details/conflict.json

  @T-UC-003-v31-error-idempotency-conflict @error @v3.1 @error-details @idempotency @post-f1 @post-f2 @post-f3
  Scenario: IDEMPOTENCY_CONFLICT error carries v3.1 details shape with ETag versions
    Given idempotency_key "upd-20260521-001" was previously used with a different request body
    And a valid update_media_buy request with:
    | field           | value             |
    | media_buy_id    | mb_existing       |
    | idempotency_key | upd-20260521-001  |
    | paused       | true        |
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "IDEMPOTENCY_CONFLICT"
    And the wire error object carries no "field" key
    And the error should include a "suggestion" field
    # POST-F1: System state unchanged
    # POST-F3: Buyer Agent retries with a fresh idempotency_key (suggestion guides the fix)
    # LOCAL CORRECTION: the generated scenario seeded an ETag for the key and asserted it
    # came back as details.current_version, plus a details.resource_id. The pinned
    # L1/security.mdx "IDEMPOTENCY_CONFLICT response shape" says the error body "MUST include
    # code: IDEMPOTENCY_CONFLICT and a human-readable message" and "MUST NOT include the
    # cached response, the original payload, a canonical-form diff, or any fingerprint
    # derived from them. A field json-pointer hint ... Sellers MUST NOT emit one." An ETag of
    # the cached response is such a fingerprint, and error-details/conflict.json (the shape
    # with resource_id/current_version) is the CONFLICT shape, not this one. Both details
    # Thens and the ETag Given are gone; the field-pointer prohibition replaces them. The
    # first request is spent on the wire, so the hash the conflict is detected against is
    # the one production computed.
    # @source repo=adcp ref=v3.2.7 path=docs/building/by-layer/L1/security.mdx

  @T-UC-003-storyboard-media-buy-not-found @storyboard-v3.1 @v3-1 @structured-errors @media-buy-not-found
  Scenario: update_media_buy with unknown media_buy_id returns structured MEDIA_BUY_NOT_FOUND, not a 500
    Given the buyer fabricates a media_buy_id that does not exist in the seller catalog
    When the Buyer Agent sends update_media_buy with the unknown media_buy_id and paused true
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "MEDIA_BUY_NOT_FOUND"
    And the error recovery hint should indicate correctable
    And the response should echo the context.correlation_id unchanged
    And the response should NOT be a 500 or non-AdCP error shape
    # invalid_transitions Phase 1 (unknown_media_buy): the buyer references a fabricated
    # media_buy_id. The seller MUST return MEDIA_BUY_NOT_FOUND with recovery=correctable
    # and context echoed unchanged. Sellers returning 500s or silent successes fail.
    # invalid_transitions: unknown media_buy_id surfaces as structured MEDIA_BUY_NOT_FOUND
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/invalid_transitions.yaml phase=unknown_media_buy step=update_unknown_media_buy

  @T-UC-003-storyboard-package-not-found @storyboard-v3.1 @v3-1 @structured-errors @package-not-found
  Scenario: update_media_buy with known media_buy_id but unknown package_id returns PACKAGE_NOT_FOUND
    Given the media buy exists in the seller catalog
    And the buyer references a package_id that does not belong to the media buy
    When the Buyer Agent sends update_media_buy targeting the unknown package
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "PACKAGE_NOT_FOUND"
    And the response should echo the context.correlation_id unchanged
    # invalid_transitions Phase 3 (unknown_package): media_buy_id resolves but the buyer
    # references a package_id that does not belong to it. Seller MUST return
    # PACKAGE_NOT_FOUND, distinguishing from MEDIA_BUY_NOT_FOUND so the buyer knows
    # whether to retry against the buy or fix the package reference.
    # invalid_transitions: distinct PACKAGE_NOT_FOUND error code separates buy-level from package-level lookup failure
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/invalid_transitions.yaml phase=unknown_package step=update_unknown_package

  @T-UC-003-storyboard-not-cancellable-on-recancel @storyboard-v3.1 @v3-1 @structured-errors @not-cancellable @terminal-state
  Scenario: Re-cancel of a canceled media buy returns NOT_CANCELLABLE, not silent success
    Given the media buy is in "canceled" status
    When the Buyer Agent sends update_media_buy with canceled true on the already-canceled buy
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "NOT_CANCELLABLE"
    And the error recovery hint should indicate correctable
    And the response should NOT be a 500 or non-AdCP error shape
    And the response should echo the context.correlation_id unchanged
    # The recovery-hint and envelope-shape lines pin the two halves the bare code
    # check leaves open: the pinned vocabulary classifies NOT_CANCELLABLE as
    # recovery "correctable" (adcp v3.1.1 enums/error-code.json, recovery
    # classification block), and the storyboard runner parses the two-layer AdCP
    # envelope, so a 500 or a non-AdCP body is a distinct failure from a wrong
    # code. Both bind to steps the sibling storyboard scenario already uses.
    # invalid_transitions Phase 4 (double_cancel): cancel a buy (success), then try to
    # cancel the same buy again. canceled is terminal per the AdCP spec; the second
    # cancel cannot succeed. Seller MUST return NOT_CANCELLABLE (the schema reserves
    # this code for "media buy cannot be canceled in its current state"). Distinct from
    # the existing terminal_canceled INVALID_STATE scenario which targets non-cancel
    # updates; NOT_CANCELLABLE is reserved for re-cancel attempts specifically.
    # invalid_transitions: re-cancel of terminal canceled buy is NOT_CANCELLABLE, not silent success
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/invalid_transitions.yaml phase=double_cancel step=second_cancel

  @T-UC-003-storyboard-creative-fate-after-cancellation @v3-1 @cancellation @creatives
  Scenario: Canceling a media buy releases its package-creative assignments from the get_media_buys readback
    Given the tenant is configured for auto-approval
    And the package "pkg_001" has existing creative assignments [cr_001, cr_002]
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | canceled     | true        |
    When the Buyer Agent reads media buy "mb_existing" back with get_media_buys
    Then the package "pkg_001" should report creative approvals for exactly ["cr_001", "cr_002"]
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy success spec
    And the wire media_buy_status should be "canceled"
    When the Buyer Agent reads media buy "mb_existing" back with get_media_buys
    Then the package "pkg_001" should report creative approvals for exactly []
    # LOCAL CORRECTION, to the pin's answer for a seller that declares no creative library.
    # The release of assignments on cancel binds every seller: specification.mdx, media buy
    # state transitions, "When a media buy transitions to `rejected` or `canceled`, all
    # package-creative assignments on that media buy are released", and the buyer observes
    # it through get_media_buys: "Released assignments no longer appear in `get_media_buys`
    # responses for that buy." The has_creative_library flag scopes only what happens to the
    # creatives afterwards, so the generated list_creatives and library-reuse Thens are
    # dropped: creative-libraries.mdx, "The decoupled library lifecycle applies when the
    # seller also advertises `creative.has_creative_library: true`", which this seller does
    # not. The storyboard this came from is gated on that flag, so no storyboard grades the
    # non-declaring case and the scenario carries no storyboard tag.
    # The first readback proves the assignments are on the wire before the cancel, so the
    # empty set after it cannot pass on a seller that never reports them. The cancel goes
    # through update_media_buy rather than a seeded canceled row: the release is a property
    # of the transition, and a seeded canceled buy that still holds assignments is a state
    # a conforming seller never reaches.
    # Production applies no buyer cancellation yet: update_media_buy reads `canceled` only to
    # refuse a re-cancel of a terminal buy, so no path moves a buy to `canceled` or releases
    # its assignments. Tracked by prebid/salesagent#1261, which names this release.
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/specification.mdx section="Media Buy State Transitions"
    # @source repo=adcp ref=v3.2.7 path=docs/creative/creative-libraries.mdx section="Path 2: Inline creatives on the package"

  @T-UC-003-partition-revision @partition @revision @schema-v3.1
  Scenario Outline: Revision optimistic-concurrency partition validation - <partition>
    Given the media buy "mb_existing" is at revision 7
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | paused       | true        |
    And the request revision is set to <value>
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    # v3.1 (revision_optimistic_concurrency.yaml): revision optional; minimum 1; mismatch -> CONFLICT
    # LOCAL CORRECTION: the valid rows read a bare "success", which a seller ignoring revision
    # passes identically. update_media_buy.mdx: "Every mutating update increments revision and
    # returns the new value in the success response", and update-media-buy-response.json
    # requires revision, "Revision number after this update". paused: true is a mutation, so
    # a buy at 7 must report and store 8, whether or not the request carried the token.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-response.json
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/update_media_buy.mdx

    Examples: Valid partitions
      | partition       | value          | outcome               |
      | absent          | <not provided> | success at revision 8 |
      | matches_current | 7              | success at revision 8 |

    Examples: Invalid partitions
      | partition      | value | outcome                            |
      | stale_revision | 5     | error "CONFLICT" with suggestion   |
      | ahead_revision | 99    | error "CONFLICT" with suggestion   |
      | below_min      | 0     | error "INVALID_REQUEST" with suggestion |
      | wrong_type     | "7"   | error "INVALID_REQUEST" with suggestion |

  @T-UC-003-boundary-revision @boundary @revision @schema-v3.1
  Scenario Outline: Revision optimistic-concurrency boundary validation - <boundary_point>
    Given the media buy "mb_existing" is at revision <current>
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | paused       | true        |
    And the request revision is set to <value>
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

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
    | paused       | true        |
    And the request revision is set to 7
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
    And the response should contain a revision with value 8
    And the response should contain a valid_actions array
    # BR-RULE-215 INV-4: a mutating update increments the stored revision and returns the new value
    # INT-002: success response carries valid_actions so the buyer can plan the next call without a get_media_buys round-trip
    # POST-S1: Buyer knows the media buy was updated (new revision)
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-revision-and-idempotency-independent @invariant @BR-RULE-215 @concurrency @idempotency @schema-v3.1
  Scenario: revision and idempotency_key are evaluated independently (INV-6)
    Given the tenant is configured for auto-approval
    And the media buy "mb_existing" is at revision 7
    And a valid update_media_buy request with:
    | field           | value                                |
    | media_buy_id    | mb_existing                          |
    | idempotency_key | 550e8400-e29b-41d4-a716-446655440000 |
    | paused       | true        |
    And the request revision is set to 7
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
    # BR-RULE-215 INV-6: idempotency-replay check (BR-RULE-211) and revision check are independent; neither subsumes the other
    # ---------- BR-RULE-214: Billing Arrangement Eligibility ----------
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-ext-t @extension @ext-t @error @schema-v3.1 @post-f1 @post-f2 @post-f3
  Scenario: Invoice recipient not authorized for the account is rejected (ext-t)
    Given the Buyer is authenticated
    And a valid update_media_buy request with:
    | field             | value                  |
    | media_buy_id      | mb_existing            |
    | invoice_recipient | acme-finance-not-on-acct |
    | paused       | true        |
    And the invoice_recipient "acme-finance-not-on-acct" is not authorized for this account
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion" field
    And the suggestion should contain "authorized"
    # LOCAL DIVERGENCE from the generated scenario — mirror upstream in adcp-req.
    # The three Thens above are satisfied by ANY VALIDATION_ERROR carrying a
    # suggestion, and on [mcp] one arrives for an unrelated reason: update_media_buy
    # types its parameters, so the spec-defined `invoice_recipient` field (present in
    # update-media-buy-request.json @ v3.1.1) is refused as an "Unexpected keyword
    # argument" before any authorization check runs. That made this strict-xfail
    # XPASS on [mcp] alone while the production gap it ledgers — BR-RULE-214 is not
    # implemented on any transport — was untouched. The suggestion is what tells the
    # two refusals apart: POST-F3 below requires it to offer an AUTHORIZED recipient,
    # which a schema-shape rejection cannot say.
    # BR-RULE-214 INV-8: invoice_recipient override must be authorized for the account before the operation proceeds
    # POST-F1: System state unchanged (no billing redirected); POST-F3: omit override or supply an authorized recipient
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

  # REMOVED at the pin: @T-UC-003-billing-not-supported, @T-UC-003-billing-not-permitted-agent
  # and @T-UC-003-billing-unauth-identity graded BILLING_NOT_SUPPORTED /
  # BILLING_NOT_PERMITTED_FOR_AGENT as outcomes of update_media_buy. The pinned
  # update-media-buy-request.json declares no `billing` property, so an update requests no
  # billing value, and the pinned enums/error-code.json defines both codes against "the
  # requested `billing` value" ("The seller declines the requested `billing` value ...").
  # The pinned conformance catalog places the two-gate dispatch on sync_accounts
  # ("billing_gate_dispatch: Two-gate dispatch on `sync_accounts.billing` rejection"), and
  # docs/accounts/tasks/sync_accounts.mdx lists both codes for that task. That behaviour is
  # graded in BR-UC-011-manage-accounts.feature. The invoice_recipient authorization in
  # BR-RULE-214 IS an update_media_buy obligation and stays in @T-UC-003-ext-t.
  # ---------- BR-RULE-217: Mid-Flight Package Addition ----------
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json
  # @source repo=adcp ref=v3.2.7 path=docs/building/verification/conformance.mdx
  # @source repo=adcp ref=v3.2.7 path=docs/accounts/tasks/sync_accounts.mdx

  @T-UC-003-ext-u @extension @ext-u @error @schema-v3.1 @post-f1 @post-f2 @post-f3
  Scenario: New packages on a seller that does not support mid-flight additions (ext-u)
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes new_packages with one complete package-request
    And the media buy's valid_actions does NOT advertise "add_packages"
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error should include "suggestion" field
    # BR-RULE-217 INV-1: new_packages on a non-supporting seller -> UNSUPPORTED_FEATURE
    # POST-F3: create the additional packages via a separate create_media_buy
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-ext-u-valid-actions @extension @ext-u @BR-RULE-217 @schema-v3.1 @post-s1
  Scenario: A seller without mid-flight package additions does not advertise add_packages (ext-u)
    Given the tenant is configured for auto-approval
    And the existing media buy has start_time "2020-01-01T00:00:00Z" and end_time "2099-02-01T00:00:00Z"
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
    Then the response is compliant with the update_media_buy success spec
    And the response should succeed
    And the wire valid_actions should include "update_packages"
    And the wire valid_actions should not include "add_packages"
    # LOCAL ADDITION, the advertisement half of BR-RULE-217 INV-1 graded on the wire.
    # update-media-buy-request.json /properties/new_packages: "Sellers that support mid-flight
    # package additions advertise add_packages in both valid_actions[] (deprecated) and as an
    # entry in available_actions[] (authoritative)." This seller does not, so a live buy's
    # valid_actions carries update_packages (its active status allows it) and not add_packages.
    # The flight window spans today so the buy date-refines to active, not completed (whose
    # valid_actions is empty and would satisfy "not include" vacuously).
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json pointer=/properties/new_packages

  @T-UC-003-new-packages-add @invariant @BR-RULE-217 @schema-v3.1 @post-s1 @post-s2
  Scenario: Supporting seller adds new packages atomically (INV-2/INV-4)
    Given the tenant is configured for auto-approval
    And the media buy's valid_actions advertises "add_packages"
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes new_packages with two complete package-requests
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
    And both new packages should appear in the affected_packages
    # BR-RULE-217 INV-2/INV-4: supporting seller advertises add_packages and adds all entries atomically (all-or-none)
    # POST-S1/S2: Buyer knows the media buy was updated and which packages were affected
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-new-packages-incomplete @invariant @BR-RULE-217 @error @schema-v3.1 @post-f2 @post-f3
  Scenario: New package entry missing a required package-request field is rejected (INV-3)
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes new_packages with an entry missing product_id
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "new_packages[0].product_id"
    And the error should include "suggestion" field
    # BR-RULE-217 INV-3: each new_packages entry must be a complete package-request (product_id, budget, pricing_option_id)
    # CORRECTED against the pin. new_packages items are package-request.json, which lists
    # product_id in /required, so an entry without it fails the request schema. A missing
    # required field is INVALID_REQUEST ("missing required fields, or violates schema
    # constraints", enums/error-code.json), not VALIDATION_ERROR ("beyond schema
    # validation"), and L1/security.mdx "Schema validation runs first" puts that refusal
    # ahead of everything the seller decides. So the refusal does not depend on whether the
    # seller supports mid-flight additions, and the "valid_actions advertises add_packages"
    # precondition is dropped (this seller does not advertise it, which made the scenario
    # stop at its own Given). The field Then pins which entry and member were refused.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json pointer=/properties/new_packages
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/required
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json
    # @source repo=adcp ref=v3.2.7 path=docs/building/by-layer/L1/security.mdx

  @T-UC-003-new-packages-duplicate @invariant @BR-RULE-010 @error @schema-v3.1 @post-f1 @post-f2
  Scenario: New package duplicating an existing product on the media buy is rejected (BR-RULE-010 INV-4)
    Given the media buy's valid_actions advertises "add_packages"
    And the media buy already has a package for product "prod_news_300x250"
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes new_packages with a package for product "prod_news_300x250"
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion" field
    # BR-RULE-010 INV-4: on update, a new_packages product_id duplicating another entry or an existing package is rejected
    # ---------- BR-RULE-216: Campaign & Package Cancellation Semantics ----------
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-ext-v @extension @ext-v @error @schema-v3.1 @post-f1 @post-f2 @post-f3
  Scenario: Cancellation refused when the buy cannot be canceled in its current state (ext-v)
    Given the media buy "mb_existing" is in "active" status
    And the media buy has committed delivery that the seller cannot cancel mid-flight
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | canceled     | true        |
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    # BR-RULE-216 INV-2: cancellation_reason MUST be at most 500 characters
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: Boundary values
      | boundary_point         | value             | outcome                                 |
      | reason 500 chars (max) | <500 char string> | success                                 |
      | reason 501 chars (max+1) | <501 char string> | error "INVALID_REQUEST" with suggestion |

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
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
    And package "pkg_001" should be canceled
    And the media buy status should NOT be "canceled"
    # BR-RULE-216 INV-6: per-package canceled:true cancels only that package; the media buy is not canceled
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-cancel-irreversible @invariant @BR-RULE-216 @cancellation @error @schema-v3.1 @post-f2 @post-f3
  Scenario: canceled:false is rejected -- cancellation is irreversible (INV-1/3)
    Given the media buy "mb_existing" is in "active" status
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | canceled     | false       |
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-216 INV-1: canceled only valid value is const true; INV-3: cancellation is irreversible (no "uncancel")
    # ---------- BR-RULE-198: Package Immutable Fields After Creation (immutable field guard on package update) ----------
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-198 INV-1: product_id is immutable post-create; package-update root `not` constraint rejects it
    # POST-F3: remove the immutable field, or create a new media buy to change product/formats/pricing
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-package-update-immutable-keyword @immutable_field_guard @error @schema-v3.1 @post-f2
  Scenario Outline: every fully-immutable field in a package update is refused by the root not - <immutable_field>
    Given a valid update_media_buy request for "mb_existing"
    And the request includes a package update where the payload carries <immutable_field>
    When the Buyer Agent sends the update_media_buy request
    Then the update is refused INVALID_REQUEST naming the package update's "<immutable_field>" under keyword not
    # LOCAL ADDITION. package-update.json at the pin lists six "Fully-immutable fields
    # (product_id, format_ids, format_option_refs, format_kind, params, pricing_option_id)"
    # and its root `not` anyOf requires each one. The generated outlines above name three of
    # them and grade only the code, which an undeclared field also earns in dev (extra
    # forbidden -> INVALID_REQUEST) and which production (extra ignore) never reaches: there
    # the field is dropped and the update succeeds. So this outline also grades issues[0]:
    # the field's own pointer and keyword not, which only the named immutable-field refusal
    # carries (the generic refusal says additionalProperties). core/error.json defines
    # issues[].keyword as the "Schema keyword that rejected the payload", the validator's
    # native name, and in package-update.json that keyword is the root not; the schema
    # carries no readOnly. A selector reaches update_media_buy only on
    # new_packages[] (update-media-buy-request.json /properties/new_packages, whose items are
    # package-request.json).
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/not
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/error.json pointer=/properties/issues

    Examples: the six fields the package-update root `not` forbids
      | immutable_field    |
      | product_id         |
      | format_ids         |
      | format_option_refs |
      | format_kind        |
      | params             |
      | pricing_option_id  |

    # The same root `not` names two more single members, which are not fully-immutable fields
    # but are refused through the same branch shape (`required: [name]`): the pre-GA
    # capability_ids ("Pre-GA capability_ids is also rejected rather than accepted as an
    # extension", package-update.json's description) and budget_cap_timezone. Both were
    # accepted: neither is a declared property, so the accepted-shape strip dropped them.
    Examples: the other single members the package-update root `not` forbids
      | immutable_field     |
      | capability_ids      |
      | budget_cap_timezone |

  @T-UC-003-package-update-bidding-with-bid-price @immutable_field_guard @error @schema-v3.1 @post-f2
  Scenario: a package update carrying a bidding object beside bid_price is refused AMBIGUOUS_BIDDING_POLICY
    Given a valid update_media_buy request for "mb_existing"
    And the request includes a package update where the payload carries bidding with bid_price
    When the Buyer Agent sends the update_media_buy request
    Then the update is refused AMBIGUOUS_BIDDING_POLICY naming the package update itself
    # LOCAL ADDITION. package-update.json's root `not` has one branch of two members:
    # required [bidding, bid_price] with bidding typed object. bid_price's own description:
    # "A package update MUST NOT supply both a non-null bidding object and bid_price. During
    # migration, bidding:null MAY accompany bid_price". The pinned enum has a code for exactly
    # this condition, AMBIGUOUS_BIDDING_POLICY: "The same effective package combines the
    # canonical bidding block with legacy bid_price ... Sellers MUST reject rather than
    # choosing a winner. Recovery: correctable". Both members are declared, so nothing strips
    # them; the refusal must come ahead of the UNSUPPORTED_FEATURE either member earns alone.
    # No single member is at fault, so the field is the package object.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/not
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/bid_price
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/AMBIGUOUS_BIDDING_POLICY

  @T-UC-003-package-update-catalogs-duplicate-type @invariant @error @post-f1 @post-f2
  Scenario: a catalogs replacement repeating a catalog type is refused and the stored catalogs are kept
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value                                                                                  |
    | package_id | pkg_001                                                                                |
    | catalogs   | [{"type": "product", "catalog_id": "cat-p1"}, {"type": "product", "catalog_id": "cat-p2"}] |
    And the package "pkg_001" exists in the media buy
    And an earlier update gave package "pkg_001" the catalogs [{"type": "store", "catalog_id": "cat-store"}]
    When the Buyer Agent sends the update_media_buy request
    Then the response contains error code VALIDATION_ERROR
    And the response error field is packages[0].catalogs
    And the stored catalogs of package "pkg_001" should be [{"type": "store", "catalog_id": "cat-store"}]
    # LOCAL ADDITION. package-update.json catalogs: "the provided array replaces the current
    # list", so the replacement becomes the package's catalogs, and core/package.json catalogs:
    # "Each catalog MUST have a distinct type ... sellers MUST reject requests containing
    # multiple catalogs of the same type with a validation_error". The package's array is at
    # fault, not one entry, so the field names it. The stored catalogs are the earlier
    # update's store catalog: the error arm of update-media-buy-response.json is "operation
    # failed, no changes applied", and the row is the only proof the refusal came before the
    # write.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/package.json pointer=/properties/catalogs
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/catalogs
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-response.json pointer=/oneOf/1

  @T-UC-003-partition-immutable-package-field @partition @immutable_field_guard @schema-v3.1
  Scenario Outline: Immutable package-field guard partition validation - <partition>
    Given a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update containing <forbidden_field>
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    # BR-RULE-198 (immutable_field_guard.yaml): product_id / format_ids / pricing_option_id forbidden in package-update
    # LOCAL CORRECTION: the outcomes could not see the guard they name. The invalid rows read
    # a bare INVALID_REQUEST, which dev's accepted-shape strip also answers for any undeclared
    # member (issues keyword additionalProperties), so deleting the immutable-field refusal
    # left them green. They now require each sent member refused under keyword "not", the
    # keyword media-buy/package-update.json rejects them with (its root not: anyOf of
    # required), as core/error.json asks ("the validator's native keyword name"). The valid
    # row read a bare success; it now requires the budget change to be stored.
    # ---------- BR-RULE-219: Committed Metrics Append-Only ----------
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/not
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/error.json

    Examples: Valid partitions
      | partition         | forbidden_field          | outcome                        |
      | no_immutable_keys | only budget (updatable)  | success with persisted records |

    Examples: Invalid partitions
      | partition                       | forbidden_field                          | outcome                                                  |
      | product_id_present              | product_id                               | refused INVALID_REQUEST by the package update's root not |
      | format_ids_present              | format_ids                               | refused INVALID_REQUEST by the package update's root not |
      | pricing_option_id_present       | pricing_option_id                        | refused INVALID_REQUEST by the package update's root not |
      | multiple_immutable_keys_present | product_id and pricing_option_id         | refused INVALID_REQUEST by the package update's root not |

  # REMOVED at the pin: @T-UC-003-committed-metrics-append, -immutable and -noop each sent
  # "an update_media_buy request that" appends, rewrites, drops or re-submits the package's
  # committed_metrics. The pinned update request has no such member:
  # update-media-buy-request.json carries packages[] of package-update.json, and neither
  # schema declares committed_metrics (it is declared on core/package.json, the seller's
  # package, and on package-request.json for a NEW package). Under this seller's accepted
  # shape (root CLAUDE.md pattern 7) an undeclared member is refused in dev and dropped in
  # production, so no row of these outlines can reach the append-only rule it names. The
  # obligation is also capability-gated: core/media-buy-features.json says a seller
  # declaring committed_metrics_supported "MUST honor append-only mid-flight metric
  # additions via update_media_buy", and this seller declares committed_metrics_supported
  # false (src/services/seller_capabilities.py). Restore these once the pin defines the
  # update-side carrier and the seller declares the capability.
  # ---------- BR-RULE-209 INV-10: Sandbox on update non-success shapes ----------
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/media-buy-features.json pointer=/properties/committed_metrics_supported

  @T-UC-003-sandbox-update-nonsuccess @invariant @BR-RULE-209 @sandbox @schema-v3.1 @post-f2
  Scenario Outline: sandbox is forbidden on update non-success response shapes (INV-10) - <shape>
    Given the request's account reference resolves to a sandbox account
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the update resolves to the <shape> response shape because <trigger>
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy error spec
    And the error code should be "<error_code>"
    And the response payload should NOT contain a "sandbox" field
    # BR-RULE-209 INV-10: update_media_buy three-way oneOf -- sandbox appears only on synchronous success
    # LOCAL CORRECTION: the "submitted task" row is removed. The pinned
    # update-media-buy-response.json forbids sandbox on the terminal-failure arm
    # (UpdateMediaBuyError: not anyOf [... {required: [sandbox]} ...]) but not on the
    # submitted arm, whose `not` lists only media_buy_id and warnings and whose
    # additionalProperties is true. Asserting "forbidden" there grades a prohibition the
    # pin does not make.
    # LOCAL CORRECTION: the terminal-failure row was triggered by a negative package budget,
    # which the request schema refuses before the boundary resolves the account, so the
    # sandbox account never reached the refusal and a failure builder that stamped sandbox
    # from it could not show. The row now names a package the buy does not hold: that
    # refusal happens inside the update, after the sandbox account is resolved, and its code
    # is pinned so a refusal from any earlier stage fails the row. The compliance check
    # names the error arm, the one that carries the prohibition.
    # ---------- BR-RULE-013 INV-6: package-level start_time 'asap' forbidden ----------
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-response.json pointer=/oneOf/1/not
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/update_media_buy.mdx (error table: PACKAGE_NOT_FOUND)

    Examples:
      | shape            | trigger                                                        | error_code        |
      | terminal failure | the package update names a package the media buy does not hold | PACKAGE_NOT_FOUND |

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
    Then the response is compliant with the update_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-013 INV-6: 'asap' is forbidden (not/const) at package scope; only an ISO 8601 date-time is accepted
    # (media-buy-level start_time still accepts 'asap' -- see T-UC-003-boundary-start-time)

  @T-UC-003-bva-sandbox-response @boundary @sandbox @schema-v3.1 @post-s6 @post-f2
  Scenario Outline: sandbox response-field boundary - <boundary>
    Given an update_media_buy request whose resolved account is <account_context>
    And the update resolves to the <response_shape> response shape
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the sandbox response-field outcome should be <outcome>
    # LOCAL CORRECTION, two rows. (1) "sandbox: false in response (explicit production)"
    # asserted "present and false" for the SAME input as the next row, which asserts
    # "omitted", so one of the two failed whatever the seller sent. The pinned success arm
    # says only "When true, this response contains simulated data from sandbox mode", so a
    # production account may carry false or nothing; the row now asserts "not true", the
    # reading UC-005's sandbox outline gives the same row. (2) "sandbox present on
    # update_media_buy submitted task envelope" is removed: the submitted arm does not forbid
    # sandbox (its `not` lists only media_buy_id and warnings; additionalProperties true).
    # The two terminal-failure rows grade the seller's own budget refusal (BUDGET_TOO_LOW on
    # packages[0].budget, for a budget of 0 the schema admits), made after the sandbox
    # account was resolved: "Sandbox errors are real validation errors. If a buyer sends an
    # invalid budget using a sandbox account, return a real error". A refusal the request
    # schema makes is answered before the account is resolved, so it cannot show either.
    # ---------- start_time package-scope boundary (start_time.yaml) ----------
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-response.json
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/advanced-topics/sandbox.mdx

    Examples: sandbox response semantics (BR-UC-003)
      | boundary                                                            | account_context    | response_shape      | outcome                              |
      | sandbox: true in response (sandbox account)                         | a sandbox account  | synchronous success | sandbox flag present and true        |
      | sandbox: false in response (explicit production)                    | a production account | synchronous success | sandbox flag not true              |
      | sandbox absent in response (production account)                     | a production account | synchronous success | sandbox flag omitted                |
      | sandbox: true on update_media_buy synchronous success shape         | a sandbox account  | synchronous success | sandbox flag present and true        |
      | sandbox account with invalid budget (real validation error)         | a sandbox account  | terminal failure    | rejected with a real validation error (not a silent ok) |
      | sandbox present on update_media_buy terminal-failure (errors) shape  | a sandbox account  | terminal failure    | sandbox field forbidden on this shape |

  @T-UC-003-bva-start-time-package-scope @boundary @start_time @schema-v3.1 @post-f2
  Scenario Outline: start_time boundary at package scope - <boundary>
    Given a valid update_media_buy request for "mb_existing"
    And the request includes 1 package update where start_time is <value>
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    # ---------- billing eligibility boundaries (billing_eligibility.yaml, BR-RULE-214) ----------
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: start_time package-scope boundary (BR-UC-003)
      | boundary                | value | outcome                            |
      | 'asap' at package scope | asap  | error "INVALID_REQUEST" — rejected |

  @T-UC-003-bva-billing-eligibility @boundary @billing @schema-v3.1 @post-f2
  Scenario Outline: billing eligibility boundary - <boundary>
    Given an update_media_buy request for "mb_existing" with the billing condition: <boundary>
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    # LOCAL CORRECTION: the five "billing resolved = ..." rows are removed, for the reason
    # recorded where @T-UC-003-billing-not-supported stood: the pinned update request carries
    # no `billing`, and the billing gates are sync_accounts behaviour (BR-UC-011). The
    # invoice_recipient row is an update_media_buy obligation ("The seller MUST validate the
    # invoice recipient is authorized for this account") and stays; its outcome now names
    # the code @T-UC-003-ext-t grades for the same refusal, so the two cannot disagree.
    # ---------- targeting_overlay collection_list boundaries (targeting_overlay.yaml, BR-RULE-014) ----------
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json pointer=/properties/invoice_recipient

    Examples: billing eligibility (BR-UC-002/003)
      | boundary                                                                                                            | outcome                                       |
      | invoice_recipient supplied = business entity not authorized for this account                                        | error "VALIDATION_ERROR" — rejected (invoice_recipient unauthorized) |

  @T-UC-003-bva-targeting-overlay-collection-list @boundary @targeting_overlay @schema-v3.1 @post-f2
  Scenario Outline: targeting_overlay collection_list boundary - <boundary>
    Given a valid update_media_buy request for "mb_existing"
    And the request carries a targeting_overlay where <boundary>
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    # LOCAL CORRECTION: the rows expected "accepted". core/targeting.json describes both
    # collection_list and collection_list_exclude as "Seller must declare support in
    # get_adcp_capabilities", and get-adcp-capabilities-response.json's
    # execution.targeting.collection_list reads "False or absence makes no product-level
    # promise. Product.overlay_support is authoritative". This seller declares neither, at
    # either level, and the same targeting.json says "A fresh create/update request MUST be
    # applied exactly or rejected". An accepted reference was stored and never applied, so the
    # pinned answer is UNSUPPORTED_FEATURE naming the dimension, before any mutation: the base
    # request's budget change must not land either. With both set, collection_list is named.
    # ---------- immutable_field_guard boundaries (immutable_field_guard.yaml, BR-RULE-198) ----------
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/targeting.json pointer=/properties/collection_list
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

    Examples: collection_list overlay (BR-UC-002/003)
      | boundary                                              | outcome  |
      | collection_list with valid agent_url and list_id      | error "UNSUPPORTED_FEATURE" on packages[0].targeting_overlay.collection_list with suggestion — no records persisted |
      | collection_list_exclude with valid agent_url and list_id | error "UNSUPPORTED_FEATURE" on packages[0].targeting_overlay.collection_list_exclude with suggestion — no records persisted |
      | both collection_list and collection_list_exclude set  | error "UNSUPPORTED_FEATURE" on packages[0].targeting_overlay.collection_list with suggestion — no records persisted |

  @T-UC-003-bva-immutable-field-guard @boundary @immutable_field_guard @schema-v3.1 @post-f2
  Scenario Outline: immutable field guard boundary - <boundary>
    Given a valid update_media_buy request for "mb_existing"
    And the request includes a package update where <boundary>
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    # ---------- package_immutable_fields boundaries (uc026_immutable_fields.yaml, BR-RULE-198) ----------
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

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
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    # ---------- committed_metrics append-only boundaries (committed_metrics_append_only.yaml, BR-RULE-219) ----------
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: PackageUpdate immutable-field guard (BR-UC-003)
      | boundary                              | outcome                                 |
      | update with only mutable fields       | accepted                                |
      | update includes product_id            | error "INVALID_REQUEST" with suggestion |
      | update includes format_ids            | error "INVALID_REQUEST" with suggestion |
      | update includes pricing_option_id     | error "INVALID_REQUEST" with suggestion |
      | update includes all three immutable fields | error "INVALID_REQUEST" with suggestion |

  # REMOVED at the pin: @T-UC-003-bva-committed-metrics, for the reason recorded where
  # @T-UC-003-committed-metrics-append stood -- the pinned update request declares no
  # committed_metrics member, so none of its rows can be sent.
  # ---------- idempotency_key boundaries (idempotency_key.yaml, BR-RULE-081) ----------
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

  @T-UC-003-bva-idempotency-key @boundary @idempotency_key @schema-v3.1 @post-f2
  Scenario Outline: idempotency_key boundary - <boundary>
    Given a valid update_media_buy request for "mb_existing"
    And the request's idempotency_key matches the boundary <boundary>
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    # ---------- product_uniqueness boundaries (product_uniqueness.yaml, BR-RULE-010) ----------
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json

    Examples: idempotency_key boundaries (BR-UC-003)
      | boundary                                       | outcome                                            |
      | absent (field not provided)                    | error "INVALID_REQUEST" — rejected (v3.1 requires idempotency_key) |
      | valid length, disallowed character (e.g. space) | error "INVALID_REQUEST" (suggested_code IDEMPOTENCY_KEY_INVALID_FORMAT) — rejected |

  @T-UC-003-bva-product-uniqueness @boundary @product_uniqueness @schema-v3.1 @post-f2
  Scenario Outline: product uniqueness boundary - <boundary>
    Given an update_media_buy request for "mb_existing" whose package set is described by <boundary>
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>
    # LOCAL CORRECTION: four create_media_buy rows are removed. "single package", "two
    # packages, different products", "two packages, same product_id" and "proposal_id
    # supplied" describe a create request's package set. On update, packages[] items are
    # package-update.json, whose root `not` rejects product_id (so no update can name a
    # product there), and update-media-buy-request.json declares no proposal_id. The
    # new_packages rows are the update's own product-uniqueness surface and stay.
    # REGROUNDED: all three rows are refused with UNSUPPORTED_FEATURE on new_packages.
    # update-media-buy-request.json /properties/new_packages: "Sellers that do not support
    # this MUST reject with ACTION_NOT_ALLOWED (preferred) or UNSUPPORTED_FEATURE (legacy)".
    # This seller does not add packages to a live buy and does not advertise add_packages
    # (@T-UC-003-ext-u-valid-actions grades that on the wire). The MUST covers every
    # new_packages request, so the refusal comes before the product set is examined: a
    # duplicate set is refused for the same reason as a distinct one. The rows used to expect
    # "accepted" and VALIDATION_ERROR, which is the obligation of a seller that supports
    # mid-flight additions. They grade that no package set, distinct or duplicate, gets past
    # the refusal. Each row's request carries only new_packages, so no other field can earn
    # the refusal.
    # ---------- approval_workflow submitted-envelope boundaries (approval_workflow.yaml) ----------
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/not
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/update-media-buy-request.json pointer=/properties/new_packages

    Examples: product uniqueness across packages (BR-UC-002/003)
      | boundary                                                                  | outcome                                                    |
      | update new_packages[] adds a product_id not present elsewhere             | error "UNSUPPORTED_FEATURE" on new_packages with suggestion |
      | update new_packages[] product_id duplicates an existing media buy package | error "UNSUPPORTED_FEATURE" on new_packages with suggestion |
      | update new_packages[] contains two entries with the same product_id       | error "UNSUPPORTED_FEATURE" on new_packages with suggestion |

  @T-UC-003-bva-approval-workflow @boundary @approval_workflow @schema-v3.1 @post-s7 @post-s8
  Scenario Outline: approval workflow submitted-envelope boundary - <boundary>
    Given an update_media_buy request for "mb_existing"
    And the tenant/adapter approval configuration is <boundary>
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy spec
    And the result should be <outcome>

    Examples: approval workflow (BR-UC-003)
      | boundary                                  | outcome                                    |
      | tenant flag true (submitted task envelope) | submitted task envelope (awaiting approval) |
      | adapter flag true (submitted task envelope) | submitted task envelope (awaiting approval) |
