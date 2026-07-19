# Hand-authored feature — not compiled from adcp-req.
#
# AdCP 3.1.1 requires idempotency_key on sync-accounts-request.json. The
# generated UC-011 storyboards have no missing-key row and the shared harness
# normally supplies a safe key, so this companion explicitly omits it and
# grades the real A2A, MCP, and REST envelopes.

Feature: UC-011 AdCP 3.1.1 required sync idempotency key
  As a Buyer
  I want sync_accounts to reject a missing idempotency key
  So that every mutating request carries an explicit retry identity

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context

  @T-UC-011-local-required-idempotency-v311 @sync @idempotency-key @schema-v3.1
  Scenario: Missing sync_accounts idempotency_key is rejected on the wire
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends sync_accounts without the required idempotency_key
    Then the operation should fail
    And the wire error should be VALIDATION_ERROR with suggestion
