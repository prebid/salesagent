# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# What it grades: the objects a Google Ad Manager seller sends its ad server when a buyer's
# create_media_buy succeeds. AdCP 3.1.1 create_media_buy.mdx requires a seller to return
# synchronous success with a seller-issued media_buy_id when every product is
# non_guaranteed. The sales-non-guaranteed storyboard's create_media_buy step carries the
# `upstream_traffic` check ("a real non-guaranteed buy creates line items / orders /
# campaigns in the seller's adserver"), which this repo's storyboard runner cannot observe
# from the buyer's wire (docs/test-obligations/storyboard-wireability.yaml). These
# scenarios are the in-process grading of that check for a GAM seller: the real
# GoogleAdManager adapter runs, and only Google's SOAP client is stood in. A buy booked at
# create and held for a creative under review (enums/media-buy-status.json,
# pending_creatives: "the seller has already accepted the buy") is not booked again when the
# seller approves that creative.
#
# Transports: a2a, mcp and rest. The live server has no stand-in GAM network, so the
# setup declares itself unrealizable on e2e (GamSellerMixin.sell_through_gam).
#
# @source repo=adcp ref=v3.1.1 path=docs/media-buy/task-reference/create_media_buy.mdx
# @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/specialisms/sales-non-guaranteed/index.yaml pointer=/phases/create_buy
Feature: A Google Ad Manager seller books the buyer's media buy in Google Ad Manager (local)

  @T-UC-002-local-gam-order @extension
  Scenario: A non-guaranteed buy is booked as a GAM order for the buyer's advertiser, with a line item on the product's ad unit
    Given a valid create_media_buy request
    And the account exists and is active
    And the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the buyer holds a media buy booked in Google Ad Manager as one order, for advertiser "3000001", trafficked by "2000001"
    And Google Ad Manager received one line item, in that order, targeting ad unit "4000001" with its descendants

  # The buy is booked when it is created and held for the creative under review. The approved
  # creative is created in GAM and placed on the line item at create, once (#2378). Approving
  # the held creative uploads it to the same order and approves that order (#1690); the
  # creative placed at create is not uploaded again.
  @T-UC-002-local-gam-creatives-on-create @extension
  Scenario: Creatives named on create reach the booked line item, and approving the held one books no second order
    Given a valid create_media_buy request
    And the account exists and is active
    And the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001"
    And the package references the buyer's approved 300x250 image creative "cr-ready" showing "https://example.com/ready.png" and its creative "cr-held" showing "https://example.com/held.png" that awaits the seller's review
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one image creative for advertiser "3000001" showing "https://example.com/ready.png"
    And Google Ad Manager associated each creative it created with the line item it created
    When the seller approves creative "cr-held"
    Then the buyer holds a media buy booked in Google Ad Manager as one order, for advertiser "3000001", trafficked by "2000001"
    And Google Ad Manager created image creatives for advertiser "3000001" showing "https://example.com/ready.png, https://example.com/held.png"
    And Google Ad Manager associated each creative it created with the line item it created
    And Google Ad Manager was asked to approve the order it created, on booking and on the creative's approval

  # A creative GAM holds from an earlier buy is placed on the new line item, not created
  # again. A re-sync can put such a creative back under review; it is then held like any
  # other, and placed when the seller approves it. Approving one that is already on the line
  # item sends no second association.
  @T-UC-002-local-gam-held-creative-on-create @extension
  Scenario Outline: A creative GAM already holds is placed on the new line item once: at create when approved, else on its approval
    Given a valid create_media_buy request
    And the account exists and is active
    And the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001"
    And the package references the buyer's 300x250 image creative "cr-placed" showing "https://example.com/placed.png", <status> and already held by Google Ad Manager as creative "7000001"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created no creative
    And Google Ad Manager associated <associated> with the line item it created
    When the seller approves creative "cr-placed"
    Then Google Ad Manager created no creative
    And Google Ad Manager associated creative "7000001" with the line item it created

    Examples:
      | status                       | associated         |
      | approved                     | creative "7000001" |
      | awaiting the seller's review | no creative        |
