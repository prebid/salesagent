# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# Upstream gap: BR-UC-011-manage-accounts.feature grades dry_run and
# delete_missing SEPARATELY — dry-run partitions, delete-missing partitions — and
# never combines them, and no scenario repeats one natural key under dry_run
# against an account that already exists. Both gaps hid the same defect (#1721):
# the preview reported an outcome no real run produces. The conformance
# storyboard cannot close them either — neither `dry_run` nor `delete_missing`
# appears anywhere in dist/compliance/3.1.1 (286 files scanned at tag v3.1.1),
# so this behaviour is UNGRADED upstream.
#
# What the pinned schema mandates, and why these are bug-fix scenarios rather
# than proposals: dry_run "preview what would change without applying. Returns
# what would be created/updated/deactivated", and deactivation is the ONLY effect
# delete_missing has. So a preview that omits the closures does not return what
# the sentence promises. The response shape is already legal — action=updated
# with status=closed is exactly what a live delete_missing emits — so previewing
# adds no field and no enum value.
#
# Reconcile upstream in adcp-req (dry_run × delete_missing, and a repeated-key
# preview scenario), then retire this file in favour of the regenerated one.
#
# @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/dry_run
# @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/delete_missing
# @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-response.json pointer=/properties/accounts/items/properties/status
Feature: UC-011 sync_accounts — a dry_run preview describes an outcome a real run produces (local)

  @T-UC-011-local-dryrun-delete-missing @sync @dry-run @delete-missing @invariant
  Scenario: delete_missing under dry_run previews the closures it would perform
    Given the Buyer Agent has an authenticated connection
    And the agent previously synced accounts for brand domain "acme-corp.com" and "old-brand.com"
    When the Buyer Agent sends a sync_accounts request with dry_run true and delete_missing true and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response includes dry_run true
    And the response includes a result for brand domain "old-brand.com" showing deactivation
    And brand domain "old-brand.com" remains in its current state
    # The buyer runs this exact combination to learn WHICH accounts would close
    # before doing it for real. Reporting none of them is the failure; closing any
    # of them is the other failure. Both are asserted, in that order.

  @T-UC-011-local-dryrun-repeat-key @sync @dry-run @invariant
  Scenario: a repeated entry under dry_run is graded against what the earlier entry would leave
    Given the Buyer Agent has an authenticated connection
    And the agent previously synced accounts for brand domain "acme-corp.com" only
    When the Buyer Agent sends a sync_accounts request with dry_run true and:
    | brand.domain    | operator      | billing    |
    | acme-corp.com   | acme-corp.com | advertiser |
    | acme-corp.com   | acme-corp.com | advertiser |
    Then the response includes dry_run true
    And result 1 on the wire shows brand domain "acme-corp.com" with action "updated" and billing "advertiser"
    And result 2 on the wire shows brand domain "acme-corp.com" with action "unchanged" and billing "advertiser"
    And brand domain "acme-corp.com" remains in its current state
    And the persisted account for brand domain "acme-corp.com" still has billing "operator"
    # The account is seeded with billing "operator". A live run applies entry 1 and
    # then finds nothing left for entry 2, so it answers [updated, unchanged] and
    # echoes the SUBMITTED billing on both. Two independent ways the preview got
    # this wrong: it graded entry 2 against the untouched row (so [updated,
    # updated]), and it echoed "operator" — the value being replaced — as though
    # it were the value the buyer would get.
