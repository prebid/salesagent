# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# What it grades: the terms get_media_buy_delivery prices each package of a buy at.
# media-buy/get-media-buy-delivery-response.json: `by_package[].rate` is, for fixed-rate
# pricing, "the agreed rate". pricing-options/cpc-option.json: `pricing_option_id` is unique
# only within its product. So a package's clicks are priced at the rate agreed for that
# package: not at its own product's option as the seller has since repriced it, and not at
# another product's option that shares the id.
#
# A package stored without its terms has no agreed rate to report. The response schema
# requires `pricing_model`, `rate` and `currency` on every `by_package` entry, so the seller
# refuses the request with INTERNAL_ERROR, naming the package (#2130); it does not price the
# package from the catalog's option under its id. The pin does not say whether the refusal
# covers the whole request or only that buy; the last scenario asserts the request-wide
# answer the seller gives today.
#
# Ungraded by storyboard: delivery_reporting's get_delivery step validates the response
# schema and the presence of `media_buy_deliveries`, not per-package figures.
#
# Transports: a2a, mcp, rest and e2e_rest.
#
# @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/media-buy/get-media-buy-delivery-response.json
# @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/pricing-options/cpc-option.json pointer=/properties/pricing_option_id
Feature: get_media_buy_delivery prices each package at the terms agreed for it (local)

  @T-UC-004-local-unreported-package-cpc-own-rate @extension
  Scenario: A CPC package's clicks are priced at its own agreed rate, not at the catalog's current options under its id
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And package "pkg_cpc" uses pricing_model "cpc" with rate 0.50 and currency "USD"
    And the seller's catalog now prices package "pkg_cpc"'s pricing_option_id at cpc rate 2.00, on the package's own product and on another product
    And the ad server adapter reports 1000 impressions and 10.00 spend for "pkg_cpc"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the delivery report for "mb-001" has exactly these packages:
      | package | impressions | spend | pricing_model | rate | clicks |
      | pkg_cpc | 1000        | 10.00 | cpc           | 0.50 | 20     |

  @T-UC-004-local-unreported-package-no-stored-terms @extension
  Scenario: A package stored without its pricing terms is refused, not priced from the catalog's options under its id
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And package "pkg_001" was stored without its pricing terms
    And the seller's catalog now prices package "pkg_001"'s pricing_option_id at cpm rate 2.00, on the package's own product and on another product
    And the ad server adapter reports 1000 impressions and 10.00 spend for "pkg_001"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response contains error code INTERNAL_ERROR
    And the wire error details should include package_id "pkg_001"
