# Local compiler overlays for derivative runner/fixture defects.
#
# The boundary overlay replaces a fragile hand-counted over-max value with a
# token the bound Given step expands to exactly 256 valid-pattern characters.
# scripts/compile_bdd.py applies this replacement in both wholesale and merge
# modes. (The earlier supported=false replay reconciliation was removed when
# create_media_buy replay was restored — the upstream replay scenario now
# grades production directly.)

Feature: BR-UC-002 local capability reconciliation

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
      | value                                      | violation                        |
      | short                                      | minLength 16 violated            |
      | key with spaces in it that is long enough | pattern [A-Za-z0-9_.:-] violated |
      | key/with/slashes/that/is/also/long/enough | pattern [A-Za-z0-9_.:-] violated |
      | <256 chars>                                | maxLength 255 violated           |
