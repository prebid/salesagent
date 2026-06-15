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
# salesagent-fbdb (PR1399 R3-F1).

Feature: BR-UC-011 Account Validation (hand-authored companion)
  As a Buyer
  I want a brandless account entry to be rejected with a clear validation error
  So that I get a correctable 400, never an opaque 500

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context

  @T-UC-011-sync-brandless @sync @validation @post-f1 @post-f2
  Scenario: Sync rejects an account entry that omits brand
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with a brandless account entry
    Then the brandless entry is rejected with a correctable VALIDATION_ERROR
