# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# What it grades: a full-library replace (delete_missing) leaves a creative that an active,
# non-paused package delivers in place, refused on that creative, and archives one that no
# delivering package holds.
#
# AdCP 3.2.7 docs/creative/task-reference/sync_creatives.mdx: delete_missing "Cannot delete
# creatives assigned to active, non-paused packages"; best practice 6: "Creatives assigned to
# active, non-paused packages cannot be updated or deleted via `delete_missing`"; the error
# table: INVALID_STATE, "Creative is assigned to an active, non-paused package (blocks updates
# and `delete_missing` deletions)". The spec does not say per item or whole request; this seller
# answers per creative, as it does for a content change.
#
# Ungraded by the storyboard runner.
#
# @source repo=adcp ref=v3.2.7 path=docs/creative/task-reference/sync_creatives.mdx
Feature: UC-006 sync_creatives — delete_missing and active delivery (local)

  @T-UC-006-local-delete-missing-active-delivery
  Scenario: delete_missing refuses a creative an active, non-paused package delivers, and keeps it
    Given the Buyer is authenticated
    And a sync request that replaces the whole library
    And the library creative "cr-delivering", absent from the request, is assigned to a package of a buy in status "active" whose package is not paused
    When the Buyer Agent syncs the creatives
    Then creative "cr-delivering" failed with error INVALID_STATE and recovery correctable
    And the library creative "cr-delivering" is not archived

  @T-UC-006-local-delete-missing-not-delivering
  Scenario Outline: delete_missing archives a creative no delivering package holds - <state>
    Given the Buyer is authenticated
    And a sync request that replaces the whole library
    And the library creative "cr-idle", absent from the request, is assigned to a package of a buy in status "<status>" whose package is <paused>
    When the Buyer Agent syncs the creatives
    Then the request should proceed as a full-library replace
    And the library creative "cr-idle" is archived

    Examples:
      | state          | status        | paused     |
      | paused package | active        | paused     |
      | paused buy     | paused        | not paused |
      | before start   | pending_start | not paused |
