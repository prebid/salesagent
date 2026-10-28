# Local feature (not generated): get_products and the SELLER's own AI configuration.
#
# Two get_products features read a tenant's AI configuration: BR-RULE-005 ranking and
# the advertising-policy check. Neither read the column the Admin UI writes
# (tenants.ai_config): ranking read only the platform environment key, the policy gate
# only the legacy gemini_api_key column. A seller who configured AI in the UI got no
# ranking and no policy check.
#
# The ranking scenarios REMOVE the platform key in their Given (tests/conftest.py sets
# it for every test, and is_ai_enabled returns true on that key alone), so the tenant's
# own configuration is the only one in play and the expected order is not reachable
# without reading it.
#
# The two fallback scenarios follow main's ruling on ranking failures (the
# structural-guard comment at the ranking `except` in src/core/tools/products.py):
# ranking degrades ORDER only, so a failure returns catalog order on a completed
# response. They cover the two ways reading the tenant's own configuration can fail,
# which reading only the platform key never could.
#
# The scoring step says "called with that prompt and the buyer's brief" because that is
# graded, not decoration: see ProductEnv.set_ranking_scores.
#
# Which column wins, a malformed row resolving to nothing, and a keyless row resolving
# to nothing are properties of TenantAIConfig (pure functions of their input) and are
# graded on that module in tests/unit/test_ai_service_factory.py.
#
# Deliberately not bound to the generated BR-UC-001-discover-available-inventory.feature,
# whose scenarios have no step definitions.

Feature: get_products honours the seller's own AI configuration
  As a Buyer Agent
  I want ranking and policy checks to run on the seller's own AI configuration
  So that the response reflects the relevance model and policy the seller configured

  Background:
    Given a tenant is configured for product discovery
    And the seller's catalog contains, in catalog order:
      | product_id        | name            |
      | prod_a_banner     | Homepage Banner |
      | prod_b_video      | Preroll Video   |
      | prod_c_newsletter | Newsletter Slot |

  @T-UC-001-ranking-tenant-ai-config @BR-RULE-005 @requires_db
  Scenario Outline: The seller's own AI configuration drives ranking when no platform key exists
    Given the seller configured AI ranking <where> with the prompt "Prefer video inventory"
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

    Examples:
      | where                               |
      | in ai_config                        |
      | in the legacy gemini_api_key column |

  @T-UC-001-ranking-fallback @BR-RULE-005 @requires_db
  Scenario Outline: Ranking the seller's configuration cannot run returns catalog order
    Given the seller configured AI ranking with <configuration> and the prompt "Prefer video inventory"
    When the buyer requests products with the brief "video campaign for a sports launch"
    Then the response products are exactly, in order:
      | product_id        |
      | prod_a_banner     |
      | prod_b_video      |
      | prod_c_newsletter |

    Examples:
      | configuration                                      |
      | an ai_config the model factory refuses to build    |
      | a stored ai_config that does not parse             |

  @T-UC-001-policy-tenant-ai-config @requires_db
  Scenario: The policy check runs for a seller whose AI is configured only in ai_config
    Given the seller's AI is configured only in ai_config
    And the seller has advertising-policy checks enabled
    And the seller's policy model blocks the brief
    When the buyer requests products with the brief "video campaign for a sports launch"
    Then the response contains error code POLICY_VIOLATION

  @T-UC-001-policy-keyless-ai-config @requires_db
  Scenario: An ai_config row without an API key leaves policy checks off
    Given the seller's ai_config names a provider and a model but no API key
    And the seller has advertising-policy checks enabled
    And the seller's policy model blocks the brief
    When the buyer requests products with the brief "video campaign for a sports launch"
    Then the response products are exactly, in order:
      | product_id        |
      | prod_a_banner     |
      | prod_b_video      |
      | prod_c_newsletter |
