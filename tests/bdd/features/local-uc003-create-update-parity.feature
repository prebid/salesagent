# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# create_media_buy and update_media_buy each carry their own copy of the business logic,
# and the copies disagree. A divergence is not patched in one of the two: each one is
# stated here as the behaviour both tools owe, on update_media_buy (the side that
# diverges), and xfailed against prebid/salesagent#2483, which moves both tools onto
# shared services so a divergence cannot exist. Each scenario graduates when that lands.
#
# The approval divergence (BR-RULE-017: the tenant's human_review_required is honoured on
# create and ignored on update) is already stated by BR-UC-003's own scenarios
# (@T-UC-003-approval-tenant and the tenant-only rows of the approval-workflow outlines),
# so it is routed there rather than restated here.
#
# @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/update_media_buy.mdx
# @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/creatives
Feature: UC-003 update_media_buy — the work it shares with create_media_buy is done the same way (local)

  # BR-UC-003's Background, unchanged: its last Given maps the label "mb_existing" to the
  # seeded buy.
  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant exists with completed setup checklist
    And the Buyer is authenticated
    And the Buyer owns an existing media buy with media_buy_id "mb_existing"
    And the media buy is in "active" status

  @T-UC-003-local-inline-creatives-ad-server-push @creatives-inline @post-s1
  Scenario: an inline creative added on update is pushed to the ad server, as on create
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package update includes inline creatives with valid content
    And the package "pkg_001" exists in the media buy
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the ad server receives the inline creative "inline-cr-001"
    # @T-UC-003-alt-creatives-inline grades what the buyer reads back; this grades that the
    # creative reaches the ad server, which no response field shows. On create_media_buy a
    # package's inline creatives are synced to the library, assigned, AND pushed to the ad
    # server (adapter.add_creative_assets, read by the same Then seam the T-UC-002
    # canonical-creative scenarios use). update_media_buy does the first two and not the
    # third, so a creative added to a live buy is assigned and never serves.
    # update_media_buy.mdx: "The `creatives` array replaces the package's inline creative
    # bodies"; the pin says nothing of the ad server, which is seller-internal, so the
    # obligation is that the same request element is served the same way by both tools.
    # Storyboard: ungraded (inline_creatives_without_sync reads the buyer-side readback only).
