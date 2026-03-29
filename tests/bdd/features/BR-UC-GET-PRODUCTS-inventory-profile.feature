# Generated from adcp-req — manual feature for issue #1162 (selection_type inference)

Feature: Product discovery with inventory profile publisher_properties
  As a Buyer Agent
  I want to discover products whose publisher_properties come from inventory profiles
  So that I can purchase ad placements even when profile data lacks the selection_type discriminator

  # Issue: #1162
  # Root cause: Product.effective_properties returns raw inventory profile
  # publisher_properties without inferring selection_type. AdCP 2.13.0+
  # requires the PublisherPropertySelector discriminated union to have
  # selection_type ("all", "by_id", or "by_tag").
  #
  # Postconditions verified:
  #   POST-S1: Buyer receives a valid product list (no validation errors)
  #   POST-S2: Each product's publisher_properties includes selection_type
  #   POST-S3: selection_type is correctly inferred from the data
  #   POST-S4: Legacy extra fields are stripped from publisher_properties
  #   POST-F1: Invalid property_ids fall back to selection_type "all"

  Background:
    Given a tenant is configured for product discovery
    And the tenant has a pricing option for each product


  @inventory_profile @selection_type @requires_db
  Scenario: Profile with property_ids infers selection_type "by_id"
    Given an inventory profile with publisher_properties:
      | publisher_domain | property_ids |
      | example.com      | homepage     |
    And a product linked to that inventory profile
    When I request products
    Then the response contains the product
    And the product publisher_properties selection_type is "by_id"
    And the product publisher_properties includes property_ids "homepage"

  @inventory_profile @selection_type @requires_db
  Scenario: Profile with property_tags infers selection_type "by_tag"
    Given an inventory profile with publisher_properties:
      | publisher_domain | property_tags |
      | example.com      | premium       |
    And a product linked to that inventory profile
    When I request products
    Then the response contains the product
    And the product publisher_properties selection_type is "by_tag"
    And the product publisher_properties includes property_tags "premium"

  @inventory_profile @selection_type @requires_db
  Scenario: Profile with only publisher_domain infers selection_type "all"
    Given an inventory profile with publisher_properties:
      | publisher_domain |
      | example.com      |
    And a product linked to that inventory profile
    When I request products
    Then the response contains the product
    And the product publisher_properties selection_type is "all"

  @inventory_profile @selection_type @requires_db
  Scenario: Profile with selection_type already present passes through
    Given an inventory profile with publisher_properties:
      | publisher_domain | property_tags | selection_type |
      | example.com      | premium       | by_tag         |
    And a product linked to that inventory profile
    When I request products
    Then the response contains the product
    And the product publisher_properties selection_type is "by_tag"
    And the product publisher_properties includes property_tags "premium"

  @inventory_profile @selection_type @requires_db
  Scenario: Profile with invalid property_ids falls back to selection_type "all"
    Given an inventory profile with publisher_properties:
      | publisher_domain | property_ids |
      | example.com      | weather.com  |
    And a product linked to that inventory profile
    When I request products
    Then the response contains the product
    And the product publisher_properties selection_type is "all"

  @inventory_profile @selection_type @requires_db
  Scenario: Profile with legacy extra fields strips them
    Given an inventory profile with publisher_properties:
      | publisher_domain | property_ids | property_name | property_type | identifiers |
      | example.com      | homepage     | Legacy Name   | website       | old_id      |
    And a product linked to that inventory profile
    When I request products
    Then the response contains the product
    And the product publisher_properties selection_type is "by_id"
    And the product publisher_properties does not contain "property_name"
    And the product publisher_properties does not contain "property_type"
    And the product publisher_properties does not contain "identifiers"
