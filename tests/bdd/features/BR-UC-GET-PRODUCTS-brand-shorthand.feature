# Manual feature for #1324 — brand string shorthand coercion on get_products.
# Companion to BR-UC-GET-PRODUCTS-inventory-profile; reuses the wired
# get_products dispatch (_call_get_products → env.call_via, all transports).

Feature: get_products coerces brand string shorthand
  As a Buyer Agent sending AdCP v3 brand shorthand
  I want get_products to accept a domain/URL string as the brand
  So that discovery works without hand-building a BrandReference

  # Issue: #1324
  # Spec: AdCP 3.1.0-beta.3 get_products brand (BrandReference.domain hostname).
  # Storyboard brand-discovery / string shorthand: ungraded (seller-side
  # acceptance of URL/domain string as BrandReference is a salesagent
  # compatibility layer for storyboard runners).

  Background:
    Given a tenant is configured for product discovery
    And an inventory profile with only domain "example.com"
    And a product linked to that inventory profile with pricing

  @brand_shorthand @requires_db
  Scenario Outline: valid brand shorthand is accepted and discovery succeeds
    When the buyer requests products with brand <brand>
    Then the response contains at least one product

    Examples:
      | brand                          |
      | "acme.com"                     |
      | "ACME.COM"                     |
      | "https://test.example"         |
      | "http://acme.com/path"         |
      | {"domain": "acme.com"}         |
      | {"domain": "ACME.COM"}         |
      | {"domain": "https://acme.com"} |

  @brand_shorthand @requires_db
  Scenario Outline: malformed brand is rejected with a typed validation error
    When the buyer requests products with brand <brand>
    Then the request is rejected with VALIDATION_ERROR naming field "brand"

    Examples:
      | brand                |
      | "https://["          |
      | "acme.com/products"  |
      | "my_brand.com"       |
      | "https://münchen.de" |
