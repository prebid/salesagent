# Hand-authored feature — not compiled from adcp-req.
#
# Encodes a UC-011 sync_accounts validation obligation that the upstream LLM
# derivation is blind to: the pinned 3.1 spec marks every accounts[] entry
# required:[brand,operator,billing] (sync-accounts-request.json @ v3.1-04f59d2d5),
# yet BR-UC-011 has no scenario for a brandless entry. SDK 5.7 added an
# account-reference arm (Accounts3) that makes brand optional, so a brandless
# entry must be rejected as a clean buyer-correctable 400 — not crash with a 500.
#
# Companion files in this directory survive `python scripts/compile_bdd.py --merge`
# (filename-scoped prune preserves non-adcp-req files); see
# BR-UC-002-manual-overrides.feature / BR-UC-002-nfr-enforcement.feature.
# PR1399 R3-F1.

Feature: BR-UC-011 Account Validation (hand-authored companion)
  As a Buyer
  I want account requests to fail with the correct boundary error
  So that I can recover without receiving misleading protocol disclosures

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context

  @T-UC-011-sync-brandless @sync @validation @post-f1 @post-f2
  Scenario: Sync rejects an account entry that omits brand
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with a brandless account entry
    Then the brandless entry is rejected with a correctable VALIDATION_ERROR

  # Local cross-transport policy (ungraded by the pinned AdCP storyboard):
  # authenticate before validating a version pin, so an invalid caller cannot
  # learn the seller's authoritative supported_versions list. Both pin forms
  # are included; the in-process matrix makes this 2 x 3 = 6 wire cases, with
  # two additional REST cases when the E2E matrix is enabled.
  @T-UC-011-auth-before-version @sync @auth @v31 @error @boundary
  Scenario Outline: Invalid authentication wins over an unsupported <pin_field> pin
    Given the Buyer Agent presents an invalid bearer token
    When the Buyer Agent sends a sync_accounts request with unsupported <pin_field> "<pin_value>"
    Then the real wire response is a correctable AUTH_REQUIRED envelope
    And the authentication rejection does not disclose supported_versions

    Examples:
      | pin_field          | pin_value |
      | adcp_version       | 4.0       |
      | adcp_major_version | 4         |

  # Missing-token sibling of the invalid-token outline above. The two cases
  # exercise DIFFERENT rejection paths: resolve_identity RAISES for an invalid
  # bearer, but RETURNS a principal-less identity when no bearer is presented,
  # so it is each boundary's principal-less guard that must win over the
  # version check here. Grading it on the real wire pins that guard —
  # deleting it would let an unauthenticated caller learn supported_versions
  # from the VERSION_UNSUPPORTED rejection.
  @T-UC-011-auth-missing-before-version @sync @auth @v31 @error @boundary
  Scenario Outline: Missing authentication wins over an unsupported <pin_field> pin
    Given the Buyer Agent presents no bearer token
    When the Buyer Agent sends a sync_accounts request with unsupported <pin_field> "<pin_value>"
    Then the real wire response is a correctable AUTH_REQUIRED envelope
    And the authentication rejection does not disclose supported_versions

    Examples:
      | pin_field          | pin_value |
      | adcp_version       | 4.0       |
      | adcp_major_version | 4         |
