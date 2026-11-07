# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# What it grades: the geo targeting a Google Ad Manager seller books on the line item for a
# package's targeting_overlay, and the geo codes and systems the seller refuses before it
# creates anything in GAM. AdCP 3.2.7:
# - core/targeting.json: each geo field "Restrict[s] delivery" to the listed places, so a
#   code dropped on the way to the ad server widens the buy. On geo_regions: "At create or
#   update, sellers MUST reject unsupported identifiers and MUST NOT silently widen, drop, or
#   partially apply the list";
# - docs/reference/migration/geo-targeting.mdx: "Inclusion fields are combined with AND
#   logic"; exclusion fields "act as AND NOT";
# - docs/media-buy/advanced-topics/targeting.mdx (Exclusion Semantics, Capabilities): "If a
#   seller does not support a requested direction, it MUST return a validation error rather
#   than silently ignore the field"; docs/protocol/get_adcp_capabilities.mdx states the same
#   for a seller that "only supports one direction"; targeting.mdx: "Not all geographic
#   granularities are supported by all publishers";
# - targeting.mdx (geo_regions): "At create or update, unsupported dimensions, countries, or
#   values outside Product `overlay_support` produce `UNSUPPORTED_FEATURE`";
# - docs/building/by-layer/L3/error-handling.mdx (Server-narrowed public elements): a value
#   the public schema accepts and this seller rejects on its private state is
#   `POLICY_VIOLATION` or `UNSUPPORTED_FEATURE`, never `VALIDATION_ERROR` against the public
#   schema.
#
# A seller can state the regions it sells: core/geo-region-support.json lists exact values per
# country, and targeting.mdx says "A seller that cannot honor every protocol-valid value MUST
# return structured country/value Product support instead". This seller declares
# `geo_regions: true`, which targeting.mdx calls "a coarse, informative rollup", refuses the
# regions outside its geo data, and carries no `overlay_support` on any product, so a buyer
# cannot read those refusals from a declaration.
#
# An overlay whose inclusion fields share no place is refused INVALID_REQUEST
# (details.problems) before any adapter runs. The pin does not state that case. Its nearest
# clauses are targeting.mdx's same-value overlap ("Sellers SHOULD reject ... and return a
# descriptive error"), which covers one value in an inclusion and an exclusion, and "A
# supported create-time selection with no executable inventory produces
# `PRODUCT_UNAVAILABLE`", which names another code. Graded for every adapter by BR-UC-002
# @T-UC-002-ext-f-geo-disjoint and BR-UC-003 @T-UC-003-ext-geo-disjoint.
#
# Storyboards at the pin do grade regions: dist/compliance/3.2.7/domains/media-buy/scenarios/
# geo_region_targeting.yaml, geo_region_all_values_support.yaml and
# geo_region_exclusion_support.yaml. Each requires a structured declaration (for example
# media_buy.execution.targeting.geo_regions.countries.FR.values), so none applies to a seller
# declaring `geo_regions: true`, and none observes a GAM line item.
#
# The expected ids are the scenario's literals, never read from the seller's data files.
# Country and region ids are rows of Google's published geo target file
# (developers.google.com/ad-manager/api/geotargets). 200501 is GAM's own Geo_Target
# DMA_REGION row for "New York NY"; 200803 (Los Angeles) applies the same numbering.
#
# Transports: a2a, mcp and rest. The live server has no stand-in GAM network, so the
# setup declares itself unrealizable on e2e (GamSellerMixin.sell_through_gam).
#
# @source repo=adcp ref=v3.2.7 path=dist/schemas/3.2.7/core/targeting.json
# @source repo=adcp ref=v3.2.7 path=dist/schemas/3.2.7/core/geo-region-support.json
# @source repo=adcp ref=v3.2.7 path=docs/reference/migration/geo-targeting.mdx
# @source repo=adcp ref=v3.2.7 path=docs/media-buy/advanced-topics/targeting.mdx
# @source repo=adcp ref=v3.2.7 path=docs/protocol/get_adcp_capabilities.mdx
# @source repo=adcp ref=v3.2.7 path=docs/building/by-layer/L3/error-handling.mdx
# @source repo=adcp ref=v3.2.7 path=dist/compliance/3.2.7/domains/media-buy/scenarios/geo_region_targeting.yaml
Feature: A Google Ad Manager seller books exactly the geo the buyer's targeting overlay names (local)

  @T-UC-002-local-gam-geo-booked @extension
  Scenario Outline: The line item restricts delivery to the overlay's places - <case>
    Given a valid create_media_buy request
    And the account exists and is active
    And the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001"
    And the package targeting_overlay is <overlay>
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager received one line item whose geo targeting includes "<targeted>" and excludes "<excluded>"

    Examples:
      | case                              | overlay                                                                                                                                      | targeted          | excluded     |
      | a state                           | {"geo_regions": ["US-NY"]}                                                                                                                   | 21167             |              |
      | a state within its country        | {"geo_countries": ["US"], "geo_regions": ["US-NY"]}                                                                                          | 21167             |              |
      | states cut to the listed country  | {"geo_countries": ["US"], "geo_regions": ["US-TX", "CA-ON"]}                                                                                 | 21176             |              |
      | a DMA within its country          | {"geo_countries": ["US"], "geo_metros": [{"system": "nielsen_dma", "values": ["501"]}]}                                                      | 200501            |              |
      | two DMAs                          | {"geo_metros": [{"system": "nielsen_dma", "values": ["501", "803"]}]}                                                                        | 200501,200803     |              |
      | two countries                     | {"geo_countries": ["US", "CA"]}                                                                                                              | 2840,2124         |              |
      | Alabama                           | {"geo_regions": ["US-AL"]}                                                                                                                   | 21133             |              |
      | provinces                         | {"geo_regions": ["CA-ON", "CA-AB"]}                                                                                                          | 20121,20113       |              |
      | UK nations                        | {"geo_regions": ["GB-SCT", "GB-WLS", "GB-NIR"]}                                                                                              | 20342,20343,20341 |              |
      | exclusions kept                   | {"geo_countries": ["US"], "geo_regions_exclude": ["US-NY"], "geo_metros_exclude": [{"system": "nielsen_dma", "values": ["803"]}]}            | 2840              | 21167,200803 |

  @T-UC-002-local-gam-geo-refused @extension @error
  Scenario Outline: A geo code or system the seller cannot place is refused before GAM is called - <case>
    Given a valid create_media_buy request
    And the account exists and is active
    And the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001"
    And the package targeting_overlay is <overlay>
    When the Buyer Agent sends the create_media_buy request
    Then the response contains error code UNSUPPORTED_FEATURE
    And the wire error details should include capability "<capability>"
    And the wire error details should include rejected_value "<value>"
    And Google Ad Manager received no order and no line item

    Examples:
      | case                                      | overlay                                                                                                       | capability            | value          |
      | a region with no GAM location             | {"geo_regions": ["DE-BY"]}                                                                                    | geo_regions           | DE-BY          |
      | the same, beside its country              | {"geo_countries": ["DE"], "geo_regions": ["DE-BY"]}                                                           | geo_regions           | DE-BY          |
      | an excluded country with no GAM location  | {"geo_countries": ["US"], "geo_countries_exclude": ["QZ"]}                                                    | geo_countries_exclude | QZ             |
      | an unknown DMA                            | {"geo_metros": [{"system": "nielsen_dma", "values": ["999"]}]}                                                | geo_metros            | 999            |
      | an unknown excluded DMA                   | {"geo_countries": ["US"], "geo_metros_exclude": [{"system": "nielsen_dma", "values": ["999"]}]}               | geo_metros_exclude    | 999            |
      | a state with a DMA                        | {"geo_regions": ["US-NY"], "geo_metros": [{"system": "nielsen_dma", "values": ["501"]}]}                      | geo_metros            | 501            |
      | a metro system other than Nielsen         | {"geo_countries": ["GB"], "geo_metros": [{"system": "uk_itl1", "values": ["TLG"]}]}                           | geo_system            | uk_itl1        |
      | the same, excluded                        | {"geo_countries": ["GB"], "geo_metros_exclude": [{"system": "eurostat_nuts2", "values": ["DE1"]}]}            | geo_system            | eurostat_nuts2 |
    # QZ matches the pinned ^[A-Z]{2}$ pattern and is not an assigned ISO 3166-1 code.
    # 999 is not a Nielsen DMA. A state and a DMA overlap only in part, and GAM's
    # location list (which it ORs) cannot express that part.
