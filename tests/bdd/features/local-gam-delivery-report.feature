# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# What it grades: a buyer's get_media_buy_delivery for a buy a Google Ad Manager seller
# booked, while GAM has not finished reporting the period. AdCP 3.1.1
# get_media_buy_delivery.mdx line 15: "If delivery data for a buy is genuinely unavailable
# (e.g., the ad server has not yet reported a flight), the seller returns the buy in
# `media_buy_deliveries` with zero or partial metrics; the seller does not omit it". Line 67
# and line 609 scope `reporting_delayed` to webhook context, and
# get-media-buy-delivery-response.json defines `expected_availability` as "only present when
# status is reporting_delayed", so a buyer's pull carries the buy's lifecycle status and no
# `expected_availability`. The same schema's `errors` holds "reporting platform issues", so a
# report job GAM fails is an `errors[]` entry for that buy.
#
# The delivery_reporting storyboard's get_delivery step ("media_buy_deliveries array with at
# least one entry") runs against the Mock adapter, so the storyboard runner never reaches a
# GAM seller. These scenarios grade that obligation for a GAM seller, with the real
# GoogleAdManager adapter and only Google's SOAP client and report download stood in. The
# buyer observes the outcome on the get_media_buy_delivery response.
#
# Transports: a2a, mcp and rest. The live server has no stand-in GAM network, so the setup
# declares itself unrealizable on e2e (GamSellerMixin.sell_through_gam).
#
# @source repo=adcp ref=v3.1.1 path=docs/media-buy/task-reference/get_media_buy_delivery.mdx
# @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/protocols/media-buy/scenarios/delivery_reporting.yaml pointer=/phases/simulate_and_verify
Feature: get_media_buy_delivery reports a Google Ad Manager buy while GAM has not finalised the period (local)

  # The requests name no dates, so their window ends now: a period GAM has not finalised.

  @T-UC-004-local-gam-unfinished-period @extension
  Scenario: A buy GAM is still reporting is returned with the figures GAM has so far
    Given the seller reports delivery from Google Ad Manager, which has reported 1200 impressions and 4.80 spend so far for the buyer's advertiser "3000001"
    And a media buy "mb-001" owned by "buyer-001" with status "active"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response is compliant with the get_media_buy_delivery spec
    And the response should include "mb-001" with 1200 impressions and 4.8 spend
    And the response should include the media buy status "active"
    And the media_buy_deliveries entry for "mb-001" omits the "expected_availability" field
    And the response should not include an error for "mb-001"

  @T-UC-004-local-gam-nothing-reported @extension
  Scenario: A buy GAM has not reported yet is returned with zero delivery
    Given the seller reports delivery from Google Ad Manager, which has reported nothing yet for the buyer's advertiser "3000001"
    And a media buy "mb-001" owned by "buyer-001" with status "active"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response is compliant with the get_media_buy_delivery spec
    And the response should include "mb-001" with zero impressions and zero spend
    And the response should not include an error for "mb-001"

  @T-UC-004-local-gam-report-failed @extension
  Scenario: A GAM report job that fails is a reporting platform issue for that buy
    Given the seller reports delivery from Google Ad Manager, which fails the report job for the buyer's advertiser "3000001"
    And a media buy "mb-001" owned by "buyer-001" with status "active"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response is compliant with the get_media_buy_delivery spec
    And the response errors include code "SERVICE_UNAVAILABLE" for media buy "mb-001"
    # Whether the buy is also returned in media_buy_deliveries with zero metrics (line 15) is #2404.
