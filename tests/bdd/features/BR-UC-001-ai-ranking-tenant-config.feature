# Local feature (not generated): AI product ranking and the SELLER's own AI configuration.
#
# BR-RULE-005 is the ranking rule: when the buyer supplies a brief and the seller
# has configured a product_ranking_prompt, products[] comes back ordered by
# relevance_score descending, and anything scoring below 0.1 is dropped.
#
# What is graded here is where the ranking's AI configuration comes FROM. It used
# to come from the platform environment key alone, so a seller who configured AI
# through the Admin UI — which writes the tenant's ai_config column — silently got
# catalog order back with no way to tell. Both scenarios therefore REMOVE the
# platform key in their Given step (tests/conftest.py sets it for every test in the
# suite, and is_ai_enabled returns true on that key by itself), so the tenant's own
# configuration is the only AI configuration in play and neither expected outcome
# below is reachable without reading it.
#
# Deliberately NOT added to BR-UC-GET-PRODUCTS-inventory-profile.feature, which is
# scoped to selection_type inference, and deliberately not bound to the generated
# BR-UC-001-discover-available-inventory.feature, whose 121 scenarios have no
# step definitions.
#
# The last Then line of the second scenario grades the ENVELOPE, not the payload. The
# A2A handler stamps a `success` marker on every get_products response and derived it
# from "is errors[] non-empty", so the moment this advisory existed an advisory-carrying
# seller reported success=false on every discovery call over A2A. Pinned
# core/protocol-envelope.json: "Non-fatal warnings populate ONLY payload.errors[] with
# severity: warning - the envelope MUST NOT carry adcp_error for non-failures." Without
# that line the two payload assertions above pass with the marker inverted.
#
# The scoring step says "called with that prompt and the buyer's brief" because that
# is graded, not decoration. rank_products_async takes the seller's ranking prompt as
# custom_prompt and the buyer's brief as brief, the prompt builder concatenates both,
# and swapping them changes only the wording sent to the model — so no wire assertion
# can see it. The stub behind that step checks which text arrived as which argument;
# with the two exchanged in production, these scenarios and the whole of
# tests/unit/test_get_products_impl_coverage.py were green (39 passed).

Feature: AI product ranking honours the seller's own AI configuration
  As a Buyer Agent
  I want products[] ordered by the seller's configured relevance model
  So that the order reflects my brief instead of the seller's catalog order

  Background:
    Given a tenant is configured for product discovery

  @T-UC-001-ranking-tenant-ai-config @BR-RULE-005 @requires_db
  Scenario: The tenant's own ai_config drives ranking when no platform key exists
    Given the seller's catalog contains, in catalog order:
      | product_id        | name            |
      | prod_a_banner     | Homepage Banner |
      | prod_b_video      | Preroll Video   |
      | prod_c_newsletter | Newsletter Slot |
    And the seller configured AI ranking in ai_config with the prompt "Prefer video inventory"
    And the ranking model, called with that prompt and the buyer's brief, scores:
      | product_id        | relevance_score |
      | prod_b_video      | 0.90            |
      | prod_a_banner     | 0.20            |
      | prod_c_newsletter | 0.05            |
    When the buyer requests products with the brief "video campaign for a sports launch"
    Then the response products are exactly, in order:
      | product_id    |
      | prod_b_video  |
      | prod_a_banner |
    And the response does not contain the product "prod_c_newsletter"
    And the response carries no advisory errors

  @T-UC-001-ranking-unavailable-advisory @BR-RULE-005 @requires_db
  Scenario: Ranking configured with no AI configuration returns catalog order and says so
    Given the seller's catalog contains, in catalog order:
      | product_id        | name            |
      | prod_a_banner     | Homepage Banner |
      | prod_b_video      | Preroll Video   |
      | prod_c_newsletter | Newsletter Slot |
    And the seller configured AI ranking with no ai_config and the prompt "Prefer video inventory"
    When the buyer requests products with the brief "video campaign for a sports launch"
    Then the response products are exactly, in order:
      | product_id        |
      | prod_a_banner     |
      | prod_b_video      |
      | prod_c_newsletter |
    And the response carries one advisory error with code "CONFIGURATION_ERROR"
    And the advisory error is a non-fatal warning about field "products[]"
    And the response envelope still reports the task as successful
