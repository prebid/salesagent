# Hand-authored feature: create_media_buy against a catalog holding a product buyers are not offered (#1845).
#
# A product no authorized property backs has no valid publisher_properties entry
# (core/product.json: required, minItems 1), so get_products leaves it out and
# get_product_catalog does too. Neither the product nor its tenant's other products may
# fail on it: an offered product still sells, and the unoffered one is unknown to the buyer
# (error-code.json PRODUCT_NOT_FOUND), on the auto-approved path and on the path that
# holds the buy for human review alike: both read products through the one offered-products
# read, so an unoffered product is refused before anything is stored.
#
# @T-UC-002-ext- routes through the wired UC-002 create dispatch on every transport.
# Ungraded by the 3.2.7 storyboards: universal/error-compliance.yaml grades a nonexistent
# product id, not one the catalog holds and does not offer.

@analysis-2026-11-03 @schema-v3.1
Feature: BR-UC-002 A catalog holding a product buyers are not offered
  create_media_buy reads the same catalog get_products offers.

  @T-UC-002-ext-unoffered-other-product @extension @requires_db
  Scenario: an offered product sells in a tenant that also holds an unoffered one
    Given a valid create_media_buy request
    And the account exists and is active
    And the tenant also holds product "unoffered" that no authorized property backs
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the result should be success

  @T-UC-002-ext-unoffered-product @extension @error @requires_db
  Scenario: the unoffered product is not found
    Given a valid create_media_buy request
    And the account exists and is active
    And the tenant also holds product "unoffered" that no authorized property backs
    But a package references the unoffered product "unoffered"
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "PRODUCT_NOT_FOUND"
    And the error recovery should be "correctable"
    And the wire error details should include missing_product_ids "unoffered"
    And no media buy record should be persisted

  # A publisher's adagents.json authorizes this agent only by an authorized_agents[] entry
  # naming it; one naming another agent authorizes nothing for this one
  # (authorized-properties.mdx :209-210). The seller ingests the file the publisher serves,
  # so a product selling only that publisher is not offered, and a buy of it is refused as
  # for any unoffered product.
  @T-UC-002-ext-unoffered-other-agent @extension @error @requires_db
  Scenario: a buy of a product whose publisher's file authorizes only another agent is not found
    Given a valid create_media_buy request
    And the account exists and is active
    And the publisher "news-psatest.com" lists property "news_home" named "News home" in its adagents.json
    And the publisher "news-psatest.com" authorizes "https://other-seller.example.com/mcp" for property "news_home"
    And the seller ingested the adagents.json of "news-psatest.com"
    And the seller offers product "other_agent" naming publisher_properties [{"publisher_domain": "news-psatest.com", "selection_type": "all"}]
    But a package references the unoffered product "other_agent"
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "PRODUCT_NOT_FOUND"
    And the error recovery should be "correctable"
    And the wire error details should include missing_product_ids "other_agent"
    And no media buy record should be persisted

  # A format option naming the seller's own host as its publisher resolves in no catalog:
  # publisher_domain names the publisher whose adagents.json formats[] declares the option
  # (core/format-option-ref.json), and this agent publishes none. core/product.json
  # format_options: an unresolved format-option reference "MUST fail closed". So the product
  # is not offered, and a buy selecting that option is refused as for any unoffered product.
  @T-UC-002-ext-agent-host-format-option @extension @error @requires_db
  Scenario: a buy selecting a format option under the seller's own host is refused
    Given a valid create_media_buy request
    And the account exists and is active
    And the tenant also holds product "host_option" declaring format option "homepage_mrec" under the seller's own host
    But a package references product "host_option" selecting format option "homepage_mrec" of the seller's own host
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "PRODUCT_NOT_FOUND"
    And the error recovery should be "correctable"
    And the wire error details should include missing_product_ids "host_option"
    And no media buy record should be persisted

  # A product references a publisher's format option by {publisher_domain,
  # format_option_id}; the publisher's stored adagents.json formats[] declares no such
  # option, so the reference resolves nowhere and fails closed (core/product.json
  # format_options): the product is not offered, and the buy is refused as for any
  # unoffered product.
  @T-UC-002-ext-unresolved-publisher-format-option @extension @error @requires_db
  Scenario: a buy selecting a publisher format option its publisher's catalog does not declare is refused
    Given a valid create_media_buy request
    And the account exists and is active
    And the tenant also holds product "missing_option" referencing format option "news_mrec" of "news-psatest.com", which its publisher's catalog does not declare
    But a package references product "missing_option" selecting format option "news_mrec" of "news-psatest.com"
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "PRODUCT_NOT_FOUND"
    And the error recovery should be "correctable"
    And the wire error details should include missing_product_ids "missing_option"
    And no media buy record should be persisted

  @T-UC-002-ext-unoffered-product-manual-approval @extension @error @requires_db
  Scenario: the unoffered product is not found when the tenant requires manual approval
    Given a valid create_media_buy request
    And the account exists and is active
    And the tenant requires manual approval
    And the tenant also holds product "unoffered" that no authorized property backs
    But a package references the unoffered product "unoffered"
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "PRODUCT_NOT_FOUND"
    And the error recovery should be "correctable"
    And the wire error details should include missing_product_ids "unoffered"
    And no media buy record should be persisted
