# Hand-authored feature — get_products buying_mode three-mode contract (not compiled by scripts/compile_bdd.py)
@requires_db
Feature: get_products buying_mode three-mode contract (brief / wholesale / refine)

  AdCP 3.0.1: a get_products request declares buyer intent via buying_mode.
  'brief' — the publisher curates products from the provided brief; 'wholesale' —
  the buyer requests raw inventory and a brief must not be provided; 'refine' —
  iterate on a previous response via the refine array. v3 clients MUST include
  buying_mode; cross-mode violations and v3 omission reject with INVALID_REQUEST
  (the version-aware wrapper owns required-ness; the model owns cross-mode rules).

  Brief-mode AI ranking order (relevance_score descending) is enforced by the
  ranker unit tests; at the BDD layer the harness ranker is disabled, so these
  scenarios assert the mode-distinguishing, transport-observable contract:
  wholesale omits brief_relevance, refine returns refinement_applied, and the
  validation paths reject with INVALID_REQUEST.

  Covers: UC-001-MODE-BRIEF-01
  Covers: UC-001-MODE-WHOLESALE-01
  Covers: UC-001-MODE-REFINE-01
  Covers: UC-001-MODE-VALIDATION-01

  Background:
    Given the buyer is authenticated for buying-mode discovery
    And the product catalog contains buying-mode test products

  @buying_mode @bm-brief
  Scenario: Brief mode returns the curated catalog
    When the Buyer Agent sends a get_products request with:
      | field       | value                            |
      | buying_mode | brief                            |
      | brief       | Display ads for tech audience Q4 |
    Then the response status should be "completed"
    And the response should contain "products" array
    And the response includes the buying-mode catalog products

  @buying_mode @bm-wholesale
  Scenario: Wholesale mode returns raw inventory without brief relevance
    When the Buyer Agent sends a get_products request with:
      | field       | value     |
      | buying_mode | wholesale |
    Then the response status should be "completed"
    And the response should contain "products" array
    And no product should include a brief_relevance value

  @buying_mode @bm-refine
  Scenario: Refine mode returns refinement_applied for each ask
    When the Buyer Agent sends a get_products request with:
      | field       | value                                                            |
      | buying_mode | refine                                                           |
      | refine      | [{"scope": "request", "ask": "more video options less display"}] |
    Then the response status should be "completed"
    And the response should contain "products" array
    And the response should contain "refinement_applied" array
    And each refinement_applied entry should have a recognized status

  @buying_mode @bm-reject-wholesale-brief
  Scenario: Wholesale mode with a brief is rejected (cross-mode violation)
    When the Buyer Agent sends a get_products request with:
      | field       | value     |
      | buying_mode | wholesale |
      | brief       | video ads |
    Then the get_products request is rejected with error code "INVALID_REQUEST"

  @buying_mode @bm-reject-missing-mode
  Scenario: A v3 client omitting buying_mode is rejected
    When the Buyer Agent sends a get_products request with:
      | field        | value       |
      | adcp_version | 3.0.6       |
      | brief        | display ads |
    Then the get_products request is rejected with error code "INVALID_REQUEST"

  @buying_mode @bm-reject-finalize
  Scenario: Refine mode with action=finalize is rejected (proposal commit unsupported)
    When the Buyer Agent sends a get_products request with:
      | field       | value                                                              |
      | buying_mode | refine                                                             |
      | refine      | [{"scope": "proposal", "proposal_id": "p1", "action": "finalize"}] |
    Then the get_products request is rejected with error code "INVALID_REQUEST"
