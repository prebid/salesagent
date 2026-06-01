# Hand-authored feature — restructured NFR enforcement scenarios for UC-002.
#
# The auto-generated BR-UC-002-create-media-buy.feature has NFR scenarios
# that send a VALID request and then expect the Then step to prove enforcement
# by sending a SECOND (invalid) request. This forces dispatch-inside-Then —
# a When action in a Then step.
#
# These scenarios restructure the tests so Given sets up the violation,
# When sends ONE request, and Then asserts on the ONE outcome.
# The original scenarios (nfr-001, nfr-006) are xfailed in conftest.py
# in favor of these replacements.
#
# See: test_architecture_bdd_no_request_in_then.py (dispatch-in-Then guard)

@analysis-2026-03-09 @schema-v3.0.0-rc.1
Feature: BR-UC-002 NFR Enforcement (restructured)
  Negative-path tests for non-functional requirements.
  Each scenario sets up a specific violation in Given and verifies
  the rejection in Then — no second dispatch needed.

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant exists with completed setup checklist
    And the Buyer is authenticated with a valid principal_id

  # Replaces nfr-001 "Then the system should validate authentication before any business logic"
  # Original scenario sent a valid request then probed with bad creds in Then.
  # This scenario sends the bad-creds request directly.
  @T-UC-002-nfr-001-enforcement @nfr @nfr-001
  Scenario: Unauthenticated request is rejected before business logic
    Given a valid create_media_buy request
    And the account exists and is active
    But the request has no valid authentication
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail with authentication error
    And no adapter calls should have been made

  # Replaces nfr-006 "Then the system should validate budget against minimum order requirements"
  # Original scenario sent an adequate-budget request then probed with low budget in Then.
  # This scenario sends the below-minimum request directly.
  @T-UC-002-nfr-006-enforcement @nfr @nfr-006
  Scenario: Budget below minimum order size is rejected
    Given a valid create_media_buy request
    And the account exists and is active
    And the tenant has minimum order size requirements
    But the package budget is below the minimum
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error should indicate minimum spend requirement
