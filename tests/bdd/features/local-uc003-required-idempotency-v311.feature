# Hand-authored feature — not compiled from adcp-req.
#
# The pinned AdCP 3.1.1 update-media-buy request schema requires
# idempotency_key. The generated UC-003 file still contains a contradictory
# pre-3.1 scenario where omission succeeds, and the shared harness normally
# supplies safe keys to unrelated update scenarios. This scenario uses the
# explicit omission sentinel and grades all real wire transports.

Feature: UC-003 AdCP 3.1.1 required update idempotency key
  As a Buyer
  I want update_media_buy to reject a missing idempotency key
  So that every mutating request has an explicit retry identity

  @T-UC-003-local-required-idempotency-v311 @idempotency-key @schema-v3.1
  Scenario: Missing update idempotency_key is rejected on the wire
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
      | field        | value       |
      | media_buy_id | mb_existing |
      | paused       | true        |
    And the update request explicitly omits idempotency_key from the wire
    When the Buyer Agent sends the update_media_buy request
    Then the wire error should be VALIDATION_ERROR naming "idempotency_key" with suggestion
