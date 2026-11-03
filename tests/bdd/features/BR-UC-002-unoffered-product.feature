# Hand-authored feature: create_media_buy against a catalog holding a product buyers are not offered (#1845).
#
# A product no verified property backs has no valid publisher_properties entry
# (core/product.json: required, minItems 1), so get_products leaves it out and
# get_product_catalog does too. Neither the product nor its tenant's other products may
# fail on it: an offered product still sells, and the unoffered one is unknown to the buyer
# (error-code.json PRODUCT_NOT_FOUND). The auto-approved path only: see the PR notes on
# the human-review path.
#
# @T-UC-002-ext- routes through the wired UC-002 create dispatch on every transport.
# Ungraded by the 3.1.1 storyboards.

@analysis-2026-11-03 @schema-v3.1
Feature: BR-UC-002 A catalog holding a product buyers are not offered
  An auto-approved create_media_buy reads the same catalog get_products offers.

  @T-UC-002-ext-unoffered-other-product @extension @requires_db
  Scenario: an offered product sells in a tenant that also holds an unoffered one
    Given a valid create_media_buy request
    And the account exists and is active
    And the tenant also holds product "unoffered" that no verified property backs
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the result should be success

  @T-UC-002-ext-unoffered-product @extension @error @requires_db
  Scenario: the unoffered product is not found
    Given a valid create_media_buy request
    And the account exists and is active
    And the tenant also holds product "unoffered" that no verified property backs
    But a package references the unoffered product "unoffered"
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "PRODUCT_NOT_FOUND"
    And the error recovery should be "correctable"
    And the wire error details should include product_id "unoffered"
    And no media buy record should be persisted
