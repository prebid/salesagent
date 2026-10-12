# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# What it grades: get_media_buy_delivery for a buy a Google Ad Manager seller just created.
# AdCP 3.1.1 media-buy/get-media-buy-delivery-response.json: `by_package[].package_id` is the
# "Seller's package identifier", the one create_media_buy returned in `packages[].package_id`;
# `by_package[].rate` is, for fixed-rate pricing, "the agreed rate". pricing-options/cpm-option.json:
# `pricing_option_id` is "Unique identifier for this pricing option within the product", so two
# products may each have an option with the same id at different rates. A GAM package's delivery
# is its line item's: GAM's report rows carry the line item id.
#
# The delivery_reporting storyboard's get_delivery step checks the response schema and the
# presence of `media_buy_deliveries`; no storyboard step correlates `by_package[].package_id`
# with the create response or grades per-package figures. These scenarios grade that, with
# the real GoogleAdManager adapter and only Google's SOAP client and report download stood in.
# The buyer observes the outcome on the get_media_buy_delivery response.
#
# Transports: a2a, mcp and rest. The live server has no stand-in GAM network, so the setup
# declares itself unrealizable on e2e (GamSellerMixin.sell_through_gam).
#
# @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/media-buy/get-media-buy-delivery-response.json
# @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/pricing-options/cpm-option.json pointer=/properties/pricing_option_id
# @source repo=adcp ref=v3.1.1 path=docs/media-buy/task-reference/get_media_buy_delivery.mdx
Feature: get_media_buy_delivery reports each package of a Google Ad Manager buy as created (local)

  Background:
    Given a valid create_media_buy request
    And the account exists and is active
    And the seller's non-guaranteed products have pricing options:
      | product_id  | pricing_option_id | pricing_model | fixed_price | floor_price |
      | leaderboard | cpm_usd_fixed     | cpm           | 10.00       |             |
      | rectangle   | cpm_usd_fixed     | cpm           | 12.00       |             |
    And the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001"
    And the buyer's packages, over a 30-day flight, are:
      | product_id  | pricing_option_id | budget | bid_price |
      | leaderboard | cpm_usd_fixed     | 500    |           |
      | rectangle   | cpm_usd_fixed     | 600    |           |
    And the buyer already has another active media buy with this seller
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed

  @T-UC-004-local-gam-readback-each-package @extension
  Scenario: Each package is reported under its own id, with its line item's delivery, at its product's rate
    Given Google Ad Manager reports 1000 impressions and 10.00 spend for the "leaderboard" package's line item, and 3000 impressions and 36.00 spend for the "rectangle" package's line item
    When the Buyer Agent requests delivery for the media buy it just created
    Then the delivery report covers only the media buy it just created, with exactly these packages under the ids create_media_buy returned:
      | package     | impressions | spend | pricing_model | rate  | currency |
      | leaderboard | 1000        | 10.00 | cpm           | 10.00 | USD      |
      | rectangle   | 3000        | 36.00 | cpm           | 12.00 | USD      |

  @T-UC-004-local-gam-readback-unreported-package @extension
  Scenario: A package whose line item GAM has not reported is reported with zero delivery
    Given Google Ad Manager reports 1000 impressions and 10.00 spend for the "leaderboard" package's line item, and nothing for the "rectangle" package's line item
    When the Buyer Agent requests delivery for the media buy it just created
    Then the delivery report covers only the media buy it just created, with exactly these packages under the ids create_media_buy returned:
      | package     | impressions | spend | pricing_model | rate  | currency |
      | leaderboard | 1000        | 10.00 | cpm           | 10.00 | USD      |
      | rectangle   | 0           | 0.00  | cpm           | 12.00 | USD      |
