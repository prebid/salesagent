# Hand-authored feature — not compiled from adcp-req.
#
# Encodes a UC-006 sync_creatives response invariant the upstream LLM derivation
# is blind to: per the pinned 3.1 oneOf (sync-creatives-response.json @
# v3.1-04f59d2d5), SyncCreativesSuccess is required:['creatives'] and per-item
# failures (action='failed') live INSIDE the success variant. So a synchronously
# processed sync — even when EVERY creative fails validation — is the success
# variant and always carries a creatives array; it never collapses to the error
# variant. BR-UC-006 has no scenario asserting this all-failed boundary (the
# analog exists for accounts: BR-UC-011 'all per-account failures still success').
#
# Companion files in this directory survive `python scripts/compile_bdd.py --merge`
# (filename-scoped prune preserves non-adcp-req files); see
# BR-UC-002-manual-overrides.feature. PR1399 R3-F2.

Feature: BR-UC-006 Creatives Invariants (hand-authored companion)
  As a Buyer
  I want an all-failed sync to still return the success variant with per-item failures
  So that I always receive a creatives array and can see what failed per creative

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context

  @T-UC-006-all-failed-success-variant @creative-invariant @post-f2
  Scenario: All creatives fail validation — response is still the success variant
    Given the Buyer has 3 creatives that all fail validation
    When the Buyer Agent syncs the creatives
    Then the response is the success variant carrying a creatives array
    And every creative result has action "failed"
    And the response does not carry an operation-level errors array
