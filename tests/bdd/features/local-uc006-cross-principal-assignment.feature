# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
# Upstream gap: the BR-UC-006 storyboard has no scenario
# combining `assignments` with a second principal — the surface where the
# cross-principal FK-500/leak bug lived (PR #1430 review). Per the pinned
# error-code enum, sellers MUST handle a creative_id not owned by the calling
# account uniformly with "does not exist" (no cross-tenant/principal
# enumeration). Reconcile upstream in adcp-req, then retire this file in
# favor of the regenerated scenario.
Feature: UC-006 sync_creatives — cross-principal assignment references (local)

  @T-UC-006-local-xp-assignment @BR-RULE-034 @invariant
  Scenario: assignment referencing another principal's creative is skipped, not created
    Given the Buyer is authenticated as principal "buyer-B"
    And a creative "creative-xp" exists for principal "buyer-A" in the same tenant
    When the Buyer Agent syncs an assignment of creative "creative-xp" to a package owned by the authenticated principal
    Then the sync operation should not fail
    And no assignment should exist for creative "creative-xp" in the tenant
