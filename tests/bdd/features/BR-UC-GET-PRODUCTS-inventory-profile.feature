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

  Background:
    Given a tenant is configured for product discovery


  @inventory_profile @selection_type @requires_db
  Scenario: Profile with property_ids infers selection_type "by_id"
    Given an inventory profile with property_ids "homepage" for domain "example.com"
    And a product linked to that inventory profile with pricing
    When the buyer requests products
    Then the response contains at least one product
    And the first product publisher_properties selection_type is "by_id"
    And the first product publisher_properties property_ids contains "homepage"

  @inventory_profile @selection_type @requires_db
  Scenario: Profile with property_tags infers selection_type "by_tag"
    Given an inventory profile with property_tags "premium" for domain "example.com"
    And a product linked to that inventory profile with pricing
    When the buyer requests products
    Then the response contains at least one product
    And the first product publisher_properties selection_type is "by_tag"
    And the first product publisher_properties property_tags contains "premium"

  @inventory_profile @selection_type @requires_db
  Scenario: Profile with only publisher_domain infers selection_type "all"
    Given an inventory profile with only domain "example.com"
    And a product linked to that inventory profile with pricing
    When the buyer requests products
    Then the response contains at least one product
    And the first product publisher_properties selection_type is "all"

  @inventory_profile @selection_type @requires_db
  Scenario: Profile with selection_type already present passes through
    Given an inventory profile with property_tags "premium" for domain "example.com" and selection_type "by_tag"
    And a product linked to that inventory profile with pricing
    When the buyer requests products
    Then the response contains at least one product
    And the first product publisher_properties selection_type is "by_tag"
    And the first product publisher_properties property_tags contains "premium"

  @inventory_profile @selection_type @requires_db
  Scenario: Profile with invalid property_ids falls back to selection_type "all"
    Given an inventory profile with property_ids "weather.com" for domain "example.com"
    And a product linked to that inventory profile with pricing
    When the buyer requests products
    Then the response contains at least one product
    And the first product publisher_properties selection_type is "all"

  @inventory_profile @selection_type @requires_db
  Scenario: Profile with extra metadata fields preserves them
    Given an inventory profile with property_ids "homepage" for domain "example.com" and legacy fields
    And a product linked to that inventory profile with pricing
    When the buyer requests products
    Then the response contains at least one product
    And the first product publisher_properties selection_type is "by_id"
    And the first product publisher_properties property_ids contains "homepage"
