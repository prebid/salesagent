# Manual feature for issue #2334: AI product ranking fails open.

Feature: Products are returned unranked when the AI ranking provider fails
  As a Buyer Agent
  I want get_products to answer when the seller's AI ranking provider is down
  So that a seller-side outage costs me the ordering, not the products

  # Ranking reorders the products and drops those scored below 0.1. AdCP 3.1.1 defines no
  # ordering guarantee for get_products and no incomplete[] scope for ranking, so when the
  # provider fails the seller returns the products unranked: the catalog order, with no
  # threshold applied, and no error. This is BR-RULE-005 INV-4 (T-UC-001-inv-005-4 in
  # BR-UC-001-discover-available-inventory.feature, which is not bound, see #1947).
  #
  # The provider failure is real. The tenant configures Gemini in its ai_config, and every
  # test process points Gemini at a closed local port (GOOGLE_GEMINI_BASE_URL, set by
  # tests/conftest.py and on the e2e server by docker-compose.e2e.yml). google-genai
  # raises httpx.ConnectError for it, unwrapped by pydantic-ai, which is the failure #2334
  # reported as INTERNAL_ERROR.

  Background:
    Given a tenant is configured for product discovery


  @T-UC-GET-PRODUCTS-ranking-provider-unreachable @ranking_fail_open @requires_db
  Scenario: an unreachable AI ranking provider returns the products unranked
    Given the tenant ranks products with the prompt "Rank by relevance to sports"
    And the tenant's AI provider is "gemini" with API key "unreachable-provider-key"
    And the seller stores products "rank-c", "rank-a", "rank-b"
    When the buyer requests products
    Then the buyer receives products "rank-a", "rank-b", "rank-c" in catalog order with no errors
