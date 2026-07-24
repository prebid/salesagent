# Hand-authored feature — not compiled from adcp-req.
#
# The pinned authoritative AdCP 3.1.1 schema at
# dist/schemas/3.1.1/creative/sync-creatives-request.json requires
# idempotency_key and constrains it to 16–255 characters matching
# ^[A-Za-z0-9_.:-]{16,255}$. The derivative adcp-req UC-006 examples still
# encode the pre-3.1 optional/8-character contract. This companion keeps the
# current wire contract graded without editing a generated file; retire it once
# adcp-req's T-UC-006-partition-idempotency-key source scenario is reconciled.

Feature: UC-006 AdCP 3.1.1 idempotency key validation
  As a Buyer
  I want every sync_creatives transport to validate the required request key
  So that malformed mutating requests fail consistently before execution

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context

  @T-UC-006-local-idempotency-v311 @creative-idempotency-v311 @partition @idempotency-key
  Scenario Outline: AdCP 3.1.1 idempotency key boundary — <partition>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And idempotency_key is <key_value>
    When the Buyer Agent syncs the creative
    Then <expected>

    Examples: Valid keys
      | partition      | key_value                                | expected                            |
      | typical_valid  | "abc12345-retry-001"                     | the request should proceed normally |
      | boundary_min   | "1234567890abcdef"                       | the request should proceed normally |
      | uuid_format    | "550e8400-e29b-41d4-a716-446655440000"   | the request should proceed normally |

    Examples: Invalid keys
      | partition      | key_value          | expected                                             |
      | absent         |                    | the wire error should be VALIDATION_ERROR naming "idempotency_key" with suggestion |
      | empty_string   | ""                 | the wire error should be VALIDATION_ERROR naming "idempotency_key" with suggestion |
      | too_short      | "1234567890abcde"  | the wire error should be VALIDATION_ERROR naming "idempotency_key" with suggestion |
      | too_long       | "a]x256"           | the wire error should be VALIDATION_ERROR naming "idempotency_key" with suggestion |
      | invalid_char   | "1234567890abcde/" | the wire error should be VALIDATION_ERROR naming "idempotency_key" with suggestion |
