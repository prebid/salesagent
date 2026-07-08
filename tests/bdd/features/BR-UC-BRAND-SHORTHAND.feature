# Hand-authored feature for #1324 — brand string/dict shorthand coercion.
# Covers get_products and create_media_buy (_BRAND_TOOLS) across all transports.

Feature: Brand string shorthand coercion on _BRAND_TOOLS
  As a Buyer Agent sending AdCP v3 brand shorthand
  I want brand inputs normalized consistently on get_products and create_media_buy
  So that discovery and booking accept URL/domain shorthand with typed rejects

  # Issue: #1324
  # Spec: AdCP 3.1.0-beta.3 BrandReference.domain (lowercase DNS hostname).
  # Storyboard string shorthand: ungraded seller-side compatibility layer.

  @brand_shorthand @get_products @requires_db
  Scenario Outline: get_products accepts valid brand shorthand
    Given a tenant is configured for product discovery
    And an inventory profile with only domain "example.com"
    And a product linked to that inventory profile with pricing
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

  @brand_shorthand @get_products @requires_db
  Scenario Outline: get_products rejects malformed brand
    Given a tenant is configured for product discovery
    And an inventory profile with only domain "example.com"
    And a product linked to that inventory profile with pricing
    When the buyer requests products with brand <brand>
    Then the request is rejected with VALIDATION_ERROR naming field "brand"

    Examples:
      | brand                |
      | "https://["          |
      | "acme.com/products"  |
      | "my_brand.com"       |
      | "https://münchen.de" |

  @brand_shorthand @create_media_buy @requires_db
  Scenario Outline: create_media_buy accepts valid brand shorthand
    Given a tenant is configured for media buy creation
    When the buyer sends create_media_buy with brand <brand>
    Then the create_media_buy request succeeds

    Examples:
      | brand                          |
      | "acme.com"                     |
      | "ACME.COM"                     |
      | "https://test.example"         |
      | "http://acme.com/path"         |
      | {"domain": "acme.com"}         |
      | {"domain": "ACME.COM"}         |
      | {"domain": "https://acme.com"} |

  @brand_shorthand @create_media_buy @requires_db
  Scenario Outline: create_media_buy rejects malformed brand
    Given a tenant is configured for media buy creation
    When the buyer sends create_media_buy with brand <brand>
    Then the request is rejected with VALIDATION_ERROR naming field "brand"

    Examples:
      | brand                |
      | "https://["          |
      | "acme.com/products"  |
      | "my_brand.com"       |
      | "https://münchen.de" |
