# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# What the pinned schema mandates: dry_run is "preview changes without applying
# them. Returns what would be created/updated/deleted" (creative/
# sync-creatives-request.json#/properties/dry_run), and the per-creative result's
# ``changes`` is "Field names that were modified (only present when
# action=updated)". A preview is therefore a CLAIM about the live run, and the
# only oracle for that claim is the live run itself. Nothing here hand-writes what
# a preview "should" say — that would re-encode today's behaviour as a contract.
#
# Method: the SAME payload is dispatched twice against one tenant, first with
# dry_run true and then live. The preview persists nothing, so the live run starts
# from the state the preview saw; its creatives[] is what the preview had to
# predict. Cases that need seeded state (an existing creative, a package with a
# product) get it before the first dispatch, and the case's own precondition on the
# live outcome is the non-vacuity control: a preview that matches a live run which
# did nothing has matched nothing.
#
# These cases replaced integration tests that compared _impl's returned DTOs across
# two separate tenants. On the wire the comparison is the buyer's: the document the
# preview returned against the document the live run returned.
#
# The conformance storyboard cannot grade this: `dry_run` appears nowhere in
# dist/compliance/3.1.1. BR-UC-006-sync-creatives.feature carries no dry_run
# scenario either; the other local dry_run feature grades out-of-transaction
# effects, not the preview's content.
#
# @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/creative/sync-creatives-request.json pointer=/properties/dry_run
Feature: UC-006 sync_creatives — a dry_run preview describes the outcome the live run produces (local)

  @T-UC-006-local-dryrun-parity @dry-run @invariant
  Scenario Outline: a dry_run preview matches the live run — <case>
    Given the Buyer is authenticated
    And a dry-run parity payload "<case>"
    When the Buyer Agent previews the sync and then syncs it live
    Then the live run produces the outcome the case expects
    And the preview and the live run answer the same
    And the preview added nothing to the library

    Examples: Creatives
      | case                                              |
      | two identical entries on one id                   |
      | two entries on one id that differ                 |
      | third entry resolves against what the second left |
      | distinct creative ids are not collapsed           |
      | single entry update of a seeded creative          |
      | delete_missing with two seeded and one new        |

    Examples: Assignments
      | case                                              |
      | existing creative with a valid assignment         |
      | new creative synced and assigned in one payload   |
      | format update and assignment in one payload       |
      | package not found in lenient mode                 |
      | assignment-only reference to a missing creative   |
      | strict package not found refuses both arms        |
      | format mismatch in lenient mode                   |
