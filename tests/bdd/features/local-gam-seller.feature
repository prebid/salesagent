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
# GoogleAdManager adapter runs, and only Google's SOAP client is stood in.
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
