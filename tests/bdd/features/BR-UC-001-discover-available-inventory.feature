# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-001 Discover Available Inventory
  As a Buyer (via Buyer Agent)
  I want to discover what advertising inventory matches my campaign requirements
  So that I can evaluate products and proceed to purchasing

  # Postconditions verified:
  #   POST-S1: Buyer knows what inventory matches their request (may be empty)
  #   POST-S2: Buyer can evaluate each product's pricing, formats, delivery measurement, and catalog match data
  #   POST-S3: Buyer sees products ordered by relevance to their brief (when buying_mode is brief)
  #   POST-S4: Buyer only sees products they are authorized to access (implementation-only — not in protocol)
  #   POST-S5: Buyer knows the discovery request completed successfully
  #   POST-S6: Buyer can evaluate publisher-recommended proposals with budget allocations
  #   POST-S7: Buyer knows whether more results are available and how to retrieve them
  #   POST-S8: Buyer knows whether catalog matching was applied and which items matched
  #   POST-S9: Buyer knows the status of each refinement request
  #   POST-S10: Buyer receives only the requested product fields when sparse field selection is used
  #   POST-F1: System state is unchanged (read-only operation)
  #   POST-F2: Buyer knows why the request failed
  #   POST-F3: Buyer knows how to fix the issue and retry

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant exists with at least one product in the catalog


  @T-UC-001-main @main-flow @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Main flow - brief mode discovery via MCP
    Given the Buyer is authenticated with a valid principal_id
    And the tenant brand_manifest_policy is "require_auth"
    And the tenant has an advertising_policy configured
    And the product catalog contains products with valid schema (format_ids, publisher_properties, pricing_options, delivery_measurement)
    When the Buyer Agent sends a get_products request with:
    | field        | value                               |
    | buying_mode  | brief                               |
    | brief        | Display ads for tech audience Q4     |
    | brand        | {"domain": "acme.com"}              |
    Then the response status should be "completed"
    And the response should contain "products" array
    And each product should have product_id, name, format_ids, publisher_properties, pricing_options, and delivery_measurement
    And the products should be ordered by relevance_score descending
    And each product should include brief_relevance explanation
    # POST-S1: Buyer knows what inventory matches their brief
    # POST-S2: Buyer can evaluate each product's pricing, formats, delivery measurement
    # POST-S3: Products ordered by relevance to brief
    # POST-S4: Only authorized products visible
    # POST-S5: Status is completed

  @T-UC-001-alt-wholesale @alternative @alt-wholesale @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Wholesale mode - raw inventory access without curation
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with:
    | field        | value                    |
    | buying_mode  | wholesale                |
    | brand        | {"domain": "acme.com"}   |
    Then the response status should be "completed"
    And the response should contain "products" array
    And the products should NOT be ranked by relevance (catalog order)
    And the products should NOT include brief_relevance field
    And the response should NOT contain "proposals" array
    # POST-S1: Buyer knows what inventory is available
    # POST-S2: Buyer can evaluate pricing, formats, delivery measurement
    # POST-S4: Only authorized products visible
    # POST-S5: Status is completed

  @T-UC-001-alt-refine @alternative @alt-refine @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Refine mode - iterate on previous discovery results
    Given the Buyer is authenticated with a valid principal_id
    And a previous get_products response returned products and proposals
    When the Buyer Agent sends a get_products request with:
    | field        | value                                                           |
    | buying_mode  | refine                                                          |
    | refine       | [{"scope": "request", "ask": "more video options less display"}] |
    Then the response status should be "completed"
    And the response should contain "products" array
    And the response should contain "refinement_applied" array
    And each refinement_applied entry should have a "status" field
    # POST-S1: Buyer knows the updated inventory after refinement
    # POST-S5: Status is completed
    # POST-S9: Buyer knows the status of each refinement request

  @T-UC-001-alt-anonymous @alternative @alt-anonymous @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Anonymous discovery - pricing suppressed
    Given the Buyer has no authentication credentials
    And the tenant brand_manifest_policy is "public"
    When the Buyer Agent sends a get_products request with:
    | field        | value                           |
    | buying_mode  | brief                           |
    | brief        | Looking for display ad inventory |
    Then the response status should be "completed"
    And the response should contain "products" array
    And every product should have pricing_options as an empty array
    And no products with allowed_principal_ids restrictions should be visible
    # POST-S1: Buyer knows what unrestricted inventory is available
    # POST-S4: Only unrestricted products visible
    # POST-S5: Status is completed

  @T-UC-001-alt-empty @alternative @alt-empty @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Empty results - no matching products
    Given the Buyer is authenticated with a valid principal_id
    And no products match the specified filters and brief
    When the Buyer Agent sends a get_products request with:
    | field        | value                              |
    | buying_mode  | brief                              |
    | brief        | Extremely niche product requirement |
    Then the response status should be "completed"
    And the response "products" array should be empty
    # POST-S1: Buyer knows no inventory matches (empty is valid success)
    # POST-S5: Status is completed

  @T-UC-001-alt-filtered @alternative @alt-filtered @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Filtered discovery - structured AdCP filters applied
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with:
    | field        | value                                              |
    | buying_mode  | brief                                              |
    | brief        | Video ads for US market                             |
    | filters      | {"delivery_type": "guaranteed", "countries": ["US"]} |
    Then the response status should be "completed"
    And the response should contain "products" array
    And every product should match the delivery_type "guaranteed"
    And every product should have countries overlapping with ["US"]
    # POST-S1: Buyer knows what inventory matches their filters
    # POST-S2: Buyer can evaluate each product
    # POST-S5: Status is completed

  @T-UC-001-alt-paginated @alternative @alt-paginated @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Paginated discovery - first page with more results available
    Given the Buyer is authenticated with a valid principal_id
    And the product catalog contains more products than the requested page size
    When the Buyer Agent sends a get_products request with:
    | field        | value                           |
    | buying_mode  | brief                           |
    | brief        | Display ads                     |
    | pagination   | {"max_results": 10}             |
    Then the response status should be "completed"
    And the response should contain at most 10 products
    And the response pagination should have "has_more" as true
    And the response pagination should include a "cursor" value
    # POST-S1: Buyer knows what inventory matches (partial)
    # POST-S5: Status is completed
    # POST-S7: Buyer knows more results are available and has cursor

  @T-UC-001-alt-paginated-next @alternative @alt-paginated @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Paginated discovery - subsequent page via cursor
    Given the Buyer is authenticated with a valid principal_id
    And a previous get_products response included a pagination cursor
    When the Buyer Agent sends a get_products request with:
    | field        | value                                          |
    | buying_mode  | brief                                          |
    | brief        | Display ads                                    |
    | pagination   | {"cursor": "<opaque_cursor>", "max_results": 10} |
    Then the response status should be "completed"
    And the response should contain the next page of products
    And the pagination should indicate whether more results exist
    # POST-S7: Buyer knows if more pages available

  @T-UC-001-alt-paginated-last @alternative @alt-paginated @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Paginated discovery - last page with no more results
    Given the Buyer is authenticated with a valid principal_id
    And a previous get_products response indicated more results
    When the Buyer Agent sends a get_products request with the cursor for the last page
    Then the response status should be "completed"
    And the response pagination should have "has_more" as false
    And the response pagination should NOT include a "cursor" value
    # POST-S7: Buyer knows this is the last page

  @T-UC-001-alt-proposal @alternative @alt-proposal @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Discovery with proposals - publisher-recommended media plans
    Given the Buyer is authenticated with a valid principal_id
    And the seller has proposal generation capability enabled
    When the Buyer Agent sends a get_products request with:
    | field        | value                                          |
    | buying_mode  | brief                                          |
    | brief        | Video campaign for holiday season, $50k budget  |
    | brand        | {"domain": "acme.com"}                         |
    Then the response status should be "completed"
    And the response should contain "products" array
    And the response should contain "proposals" array
    And each proposal should have proposal_id, name, and allocations
    And each allocation should reference a product_id from the products array
    And each allocation should have allocation_percentage
    And the sum of allocation_percentages within a proposal should equal 100
    # POST-S1: Buyer knows matching inventory
    # POST-S5: Status is completed
    # POST-S6: Buyer can evaluate proposals with budget allocations

  @T-UC-001-alt-catalog @alternative @alt-catalog @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Catalog-driven discovery - typed catalog matching
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with:
    | field        | value                                                                          |
    | buying_mode  | brief                                                                          |
    | brief        | Promote our product catalog                                                    |
    | brand        | {"domain": "acme.com"}                                                         |
    | catalog      | {"type": "product", "catalog_id": "gmc-primary"}                               |
    Then the response status should be "completed"
    And the response should contain "products" array
    And matched products should include "catalog_match" data with matched_count and submitted_count
    And the response should have "catalog_applied" as true
    # POST-S1: Buyer knows matching inventory
    # POST-S5: Status is completed
    # POST-S8: Buyer knows catalog matching was applied and which items matched

  @T-UC-001-alt-sparse @alternative @alt-sparse @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Sparse field selection - lightweight discovery with selected fields
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with:
    | field        | value                               |
    | buying_mode  | brief                               |
    | brief        | Display ads                         |
    | fields       | ["pricing_options", "format_ids"]    |
    Then the response status should be "completed"
    And each product should contain product_id and name (always included)
    And each product should contain pricing_options and format_ids (requested fields)
    And each product should NOT contain unrequested fields like description or channels
    # POST-S5: Status is completed
    # POST-S10: Buyer receives only requested fields

  @T-UC-001-ext-a @extension @ext-a @error @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Extension *a - brief blocked by advertising policy
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has an advertising_policy configured and enabled
    And the brief content violates the advertising policy (LLM returns BLOCKED)
    When the Buyer Agent sends a get_products request with:
    | field        | value                               |
    | buying_mode  | brief                               |
    | brief        | Tobacco advertising for teens        |
    Then the operation should fail with error code "POLICY_VIOLATION"
    And the error message should contain the LLM-provided reason
    And the error should include "suggestion" field
    And the suggestion should contain "revise" or "comply"
    # POST-F1: System state is unchanged
    # POST-F2: Buyer knows brief violated policy
    # POST-F3: Buyer knows to revise brief

  @T-UC-001-ext-a-restricted @extension @ext-a @error @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Extension *a - brief restricted with manual review required
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has an advertising_policy with require_manual_review enabled
    And the brief content is flagged as RESTRICTED by the LLM
    When the Buyer Agent sends a get_products request with:
    | field        | value                               |
    | buying_mode  | brief                               |
    | brief        | Alcohol advertising campaign         |
    Then the operation should fail with error code "POLICY_VIOLATION"
    And the error message should include restrictions details
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged
    # POST-F2: Buyer knows brief was restricted
    # POST-F3: Buyer knows how to revise

  @T-UC-001-ext-a-failopen @extension @ext-a @degradation @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Extension *a - policy service unavailable (fail-open)
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has an advertising_policy configured
    And the policy LLM service is unavailable
    When the Buyer Agent sends a get_products request with:
    | field        | value                               |
    | buying_mode  | brief                               |
    | brief        | Standard display campaign            |
    Then the response status should be "completed"
    And the response should contain "products" array
    # Policy check fails open - request proceeds normally

  @T-UC-001-ext-b @extension @ext-b @error @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Extension *b - authentication required but caller is anonymous
    Given the Buyer has no authentication credentials
    And the tenant brand_manifest_policy is "require_auth"
    When the Buyer Agent sends a get_products request with:
    | field        | value        |
    | buying_mode  | brief        |
    | brief        | Display ads  |
    Then the operation should fail with error code "authentication_error"
    And the error message should contain "Authentication required"
    And the error should include "suggestion" field
    And the suggestion should contain "credentials" or "authenticate"
    # POST-F1: System state is unchanged
    # POST-F2: Buyer knows authentication is required
    # POST-F3: Buyer knows to obtain credentials

  @T-UC-001-ext-c @extension @ext-c @error @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: Extension *c - brand required but not provided
    Given the Buyer is authenticated with a valid principal_id
    And the tenant brand_manifest_policy is "require_brand"
    When the Buyer Agent sends a get_products request with:
    | field        | value                               |
    | buying_mode  | brief                               |
    | brief        | Display ads for tech audience        |
    Then the operation should fail with error code "validation_error"
    And the error message should contain "Brand required"
    And the error should include "suggestion" field
    And the suggestion should contain "brand" or "domain"
    # POST-F1: System state is unchanged
    # POST-F2: Buyer knows brand is required
    # POST-F3: Buyer knows to provide brand reference

  @T-UC-001-ext-d @extension @ext-d @error @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: Extension *d - buying mode constraint violation - <violation>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with <invalid_fields>
    Then the operation should fail with error code "validation_error"
    And the error message should contain "<error_message>"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged
    # POST-F2: Buyer knows which constraint was violated
    # POST-F3: Buyer knows how to fix the request

    Examples:
      | violation                              | invalid_fields                                                    | error_message                                              |
      | missing buying_mode (v3 client)        | no buying_mode field                                              | buying_mode is required                                    |
      | brief mode without brief               | buying_mode=brief, no brief field                                 | brief is required when buying_mode is 'brief'              |
      | wholesale mode with brief              | buying_mode=wholesale, brief present                              | brief must not be provided when buying_mode is 'wholesale' |
      | wholesale mode with refine             | buying_mode=wholesale, refine present                             | refine must not be provided when buying_mode is 'wholesale'|
      | refine mode without refine array       | buying_mode=refine, no refine array                               | refine array is required when buying_mode is 'refine'      |
      | refine mode with brief                 | buying_mode=refine, brief present, refine present                 | brief must not be provided when buying_mode is 'refine'    |
      | brief mode with refine                 | buying_mode=brief, brief present, refine present                  | refine must not be provided when buying_mode is 'brief'    |

  @T-UC-001-inv-001 @invariant @BR-RULE-001 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-001 INV-1 holds - require_auth policy with authenticated caller
    Given the tenant brand_manifest_policy is "require_auth"
    And the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a valid get_products request
    Then the request should proceed to product discovery
    # INV-1 holds: policy is require_auth and request is authenticated

  @T-UC-001-inv-001-v @invariant @BR-RULE-001 @error @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-001 INV-1 violated - require_auth policy with unauthenticated caller
    Given the tenant brand_manifest_policy is "require_auth"
    And the Buyer has no authentication credentials
    When the Buyer Agent sends a get_products request
    Then the operation should fail
    And the error should indicate authentication is required
    And the error should include "suggestion" field
    # INV-1 violated: policy is require_auth and request is unauthenticated

  @T-UC-001-inv-001-2 @invariant @BR-RULE-001 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-001 INV-2 holds - require_brand policy with brand provided
    Given the tenant brand_manifest_policy is "require_brand"
    And the Buyer is authenticated with a valid principal_id
    And the request includes brand {"domain": "acme.com"}
    When the Buyer Agent sends a valid get_products request
    Then the request should proceed to product discovery
    # INV-2 holds: policy is require_brand and brand is provided

  @T-UC-001-inv-001-2v @invariant @BR-RULE-001 @error @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-001 INV-2 violated - require_brand policy without brand
    Given the tenant brand_manifest_policy is "require_brand"
    And the Buyer is authenticated with a valid principal_id
    And the request does NOT include a brand field
    When the Buyer Agent sends a get_products request
    Then the operation should fail
    And the error should indicate brand is required
    And the error should include "suggestion" field
    # INV-2 violated: policy is require_brand and no brand provided

  @T-UC-001-inv-001-3 @invariant @BR-RULE-001 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-001 INV-3 holds - public policy allows any caller
    Given the tenant brand_manifest_policy is "public"
    And the Buyer has no authentication credentials
    When the Buyer Agent sends a get_products request
    Then the request should proceed to product discovery
    # INV-3 holds: policy is public, request proceeds regardless

  @T-UC-001-inv-002 @invariant @BR-RULE-002 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-002 INV-1 violated - policy enabled and brief BLOCKED
    Given the tenant has advertising_policy enabled
    And the LLM evaluates the brief as BLOCKED
    When the Buyer Agent sends a get_products request with a non-compliant brief
    Then the operation should fail with error code "POLICY_VIOLATION"
    # INV-1 violated: policy enabled and brief content is BLOCKED

  @T-UC-001-inv-002-2 @invariant @BR-RULE-002 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-002 INV-2 violated - RESTRICTED with manual review required
    Given the tenant has advertising_policy enabled with require_manual_review
    And the LLM evaluates the brief as RESTRICTED
    When the Buyer Agent sends a get_products request
    Then the operation should fail
    # INV-2 violated: policy enabled, RESTRICTED with manual review required

  @T-UC-001-inv-002-3 @invariant @BR-RULE-002 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-002 INV-3 holds - policy disabled, check skipped
    Given the tenant has advertising_policy disabled
    When the Buyer Agent sends a get_products request with any brief content
    Then the request should proceed to product discovery without policy check
    # INV-3 holds: policy disabled, check skipped

  @T-UC-001-inv-002-4 @invariant @BR-RULE-002 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-002 INV-4 holds - policy service unavailable, fail-open
    Given the tenant has advertising_policy enabled
    And the LLM policy service is unavailable
    When the Buyer Agent sends a get_products request
    Then the request should proceed (fail-open behavior)
    # INV-4 holds: policy service unavailable, fail-open

  @T-UC-001-inv-002-5 @invariant @BR-RULE-002 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-002 INV-5 holds - policy evaluation returns ALLOWED
    Given the tenant has advertising_policy enabled
    And the LLM evaluates the brief as ALLOWED
    When the Buyer Agent sends a get_products request with a compliant brief
    Then the request should proceed with full product catalog
    # INV-5 holds: policy evaluation returns ALLOWED

  @invariant @BR-RULE-003 @analysis-2026-03-09 @schema-v3.0.0-rc.1 @implementation-only
  Scenario: BR-RULE-003 INV-1 holds - principal in allowed list
    Given a product has allowed_principal_ids ["buyer-123"]
    And the Buyer is authenticated as principal "buyer-123"
    When the Buyer Agent sends a get_products request
    Then the product should be visible in results
    # INV-1 holds: principal is in the allow-list

  @T-UC-001-inv-003-2v @invariant @BR-RULE-003 @analysis-2026-03-09 @schema-v3.0.0-rc.1 @implementation-only
  Scenario: BR-RULE-003 INV-2 violated - principal NOT in allowed list
    Given a product has allowed_principal_ids ["buyer-456"]
    And the Buyer is authenticated as principal "buyer-123"
    When the Buyer Agent sends a get_products request
    Then the product should NOT be visible in results (silently filtered)
    # INV-2 violated: principal is NOT in allow-list (no error, product just hidden)

  @T-UC-001-inv-003-3 @invariant @BR-RULE-003 @analysis-2026-03-09 @schema-v3.0.0-rc.1 @implementation-only
  Scenario: BR-RULE-003 INV-3 holds - no allowed_principal_ids restriction
    Given a product has allowed_principal_ids as null
    When the Buyer Agent sends a get_products request
    Then the product should be visible to all principals
    # INV-3 holds: no restriction, visible to all

  @T-UC-001-inv-003-4v @invariant @BR-RULE-003 @analysis-2026-03-09 @schema-v3.0.0-rc.1 @implementation-only
  Scenario: BR-RULE-003 INV-4 violated - anonymous request with restricted product
    Given a product has allowed_principal_ids ["buyer-123"]
    And the Buyer has no authentication credentials
    And the tenant brand_manifest_policy is "public"
    When the Buyer Agent sends a get_products request
    Then the product should NOT be visible in results (silently filtered)
    # INV-4 violated: anonymous request and product has allowed_principal_ids (no error, product just hidden)

  @T-UC-001-inv-004 @invariant @BR-RULE-004 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-004 INV-1 holds - anonymous request, pricing suppressed
    Given the Buyer has no authentication credentials
    And the tenant brand_manifest_policy is "public"
    When the Buyer Agent sends a get_products request
    Then every product should have pricing_options as an empty array
    # INV-1 holds: anonymous request, pricing stripped

  @T-UC-001-inv-004-2 @invariant @BR-RULE-004 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-004 INV-2 holds - authenticated request, full pricing retained
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request
    Then every product should retain its full pricing_options
    # INV-2 holds: authenticated request, full pricing

  @T-UC-001-inv-005 @invariant @BR-RULE-005 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-005 INV-1 violated - product below 0.1 threshold excluded
    Given the Buyer is authenticated with a valid principal_id
    And ranking is applied (brief provided, ranking prompt configured)
    And a product has relevance_score 0.05
    When the system applies AI ranking
    Then the product should be excluded from results
    # INV-1 violated: ranking applied and product scores < 0.1

  @T-UC-001-inv-005-2 @invariant @BR-RULE-005 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-005 INV-2 holds - product at or above 0.1 threshold included
    Given the Buyer is authenticated with a valid principal_id
    And ranking is applied (brief provided, ranking prompt configured)
    And a product has relevance_score 0.15
    When the system applies AI ranking
    Then the product should be included in results sorted by score descending
    # INV-2 holds: ranking applied and product scores >= 0.1

  @T-UC-001-inv-005-3 @invariant @BR-RULE-005 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-005 INV-3 holds - no brief provided, no threshold applied
    Given the Buyer is authenticated with a valid principal_id
    And no brief is provided (wholesale mode)
    When the Buyer Agent sends a get_products request
    Then all products should be returned without ranking or threshold filtering
    # INV-3 holds: no brief, no threshold applied

  @T-UC-001-inv-005-4 @invariant @BR-RULE-005 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-005 INV-4 holds - ranking service fails, products returned unranked
    Given the Buyer is authenticated with a valid principal_id
    And ranking is configured but the AI ranking service fails
    When the Buyer Agent sends a get_products request with a brief
    Then products should be returned unranked with no threshold applied
    # INV-4 holds: ranking service fails, products returned unranked

  @T-UC-001-inv-006 @invariant @BR-RULE-006 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-006 INV-1 holds - fixed_price set, floor_price null (fixed pricing)
    Given a product has a pricing_option with fixed_price set and floor_price null
    When the system validates the pricing option
    Then the pricing option is valid (fixed pricing model)
    # INV-1 holds: fixed_price set and floor_price null

  @T-UC-001-inv-006-2 @invariant @BR-RULE-006 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-006 INV-2 holds - floor_price set, fixed_price null (auction pricing)
    Given a product has a pricing_option with floor_price set and fixed_price null
    When the system validates the pricing option
    Then the pricing option is valid (auction pricing model)
    # INV-2 holds: floor_price set and fixed_price null

  @T-UC-001-inv-006-3v @invariant @BR-RULE-006 @error @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-006 INV-3 violated - both fixed_price and floor_price set
    Given a product has a pricing_option with both fixed_price and floor_price set
    When the system validates the pricing option
    Then the pricing option is invalid (ambiguous pricing model)
    And the error should include "suggestion" field
    # INV-3 violated: both set

  @T-UC-001-inv-006-4v @invariant @BR-RULE-006 @error @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-006 INV-4 violated - neither fixed_price nor floor_price set
    Given a product has a pricing_option with neither fixed_price nor floor_price set
    When the system validates the pricing option
    Then the pricing option is invalid (undefined pricing)
    And the error should include "suggestion" field
    # INV-4 violated: neither set

  @T-UC-001-inv-007 @invariant @BR-RULE-007 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-007 INV-1 holds - product has all required fields
    Given a product has >= 1 format_id, >= 1 publisher_property, >= 1 pricing_option, and delivery_measurement with provider
    When the system converts the product to AdCP schema
    Then the conversion should succeed
    # INV-1 holds: all required fields present

  @T-UC-001-inv-007-2v @invariant @BR-RULE-007 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-007 INV-2 violated - product has 0 format_ids
    Given a product has 0 format_ids
    When the system converts the product to AdCP schema
    Then the conversion should fail with ValueError
    # INV-2 violated: 0 format_ids

  @T-UC-001-inv-007-3v @invariant @BR-RULE-007 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-007 INV-3 violated - product has 0 publisher_properties
    Given a product has 0 publisher_properties
    When the system converts the product to AdCP schema
    Then the conversion should fail with ValueError
    # INV-3 violated: 0 publisher_properties

  @T-UC-001-inv-007-4v @invariant @BR-RULE-007 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-007 INV-4 violated - product has 0 pricing_options
    Given a product has 0 pricing_options
    When the system converts the product to AdCP schema
    Then the conversion should fail with ValueError
    # INV-4 violated: 0 pricing_options

  @T-UC-001-inv-007-5 @invariant @BR-RULE-007 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-007 INV-5 holds - missing delivery_measurement, adapter provides default
    Given a product has no delivery_measurement in the database
    When the system converts the product to AdCP schema
    Then the conversion should succeed with adapter-specific default delivery_measurement
    # INV-5 holds: adapter fallback for delivery_measurement

  @T-UC-001-inv-079 @invariant @BR-RULE-079 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-079 INV-6 holds - pre-v3 client without buying_mode defaults to brief
    Given a pre-v3 client sends a get_products request without buying_mode
    And the request includes a brief
    When the system processes the request
    Then the system should default buying_mode to "brief"
    And the request should proceed through the brief-mode pipeline
    # INV-6 holds: pre-v3 backward compatibility

  @T-UC-001-inv-084 @invariant @BR-RULE-084 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-084 INV-1 holds - catalog with brand present
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with catalog and brand
    Then the request should proceed to catalog-driven discovery
    # INV-1 holds: catalog present and brand present

  @T-UC-001-inv-084-2v @invariant @BR-RULE-084 @error @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-084 INV-2 violated - catalog without brand
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with catalog but no brand
    Then the operation should fail with validation error
    And the error should indicate brand is required when catalog is provided
    And the error should include "suggestion" field
    # INV-2 violated: catalog present and brand absent

  @T-UC-001-inv-084-3 @invariant @BR-RULE-084 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-084 INV-3 holds - no catalog, no dependency constraint
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request without catalog
    Then the catalog-brand dependency should not apply
    And the request should proceed normally
    # INV-3 holds: no catalog, no constraint

  @T-UC-001-inv-085 @invariant @BR-RULE-085 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-085 INV-1 holds - refinement_applied length matches refine array
    Given the Buyer sends a refine request with 3 entries
    When the system returns refinement_applied
    Then refinement_applied should have exactly 3 entries
    # INV-1 holds: length(refinement_applied) = length(request.refine)

  @T-UC-001-inv-085-2 @invariant @BR-RULE-085 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-085 INV-2 holds - positional correspondence maintained
    Given the Buyer sends a refine request with [request-scope, product-scope, proposal-scope] entries
    When the system returns refinement_applied
    Then refinement_applied[0] should correspond to the request-scope entry
    And refinement_applied[1] should correspond to the product-scope entry
    And refinement_applied[2] should correspond to the proposal-scope entry
    # INV-2 holds: positional correspondence

  @T-UC-001-inv-085-3 @invariant @BR-RULE-085 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-085 INV-3 holds - each entry has required status field
    Given the Buyer sends a refine request
    When the system returns refinement_applied
    Then each entry in refinement_applied should have a "status" field with value "applied", "partial", or "unable"
    # INV-3 holds: entry MUST include status

  @T-UC-001-inv-086 @invariant @BR-RULE-086 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-086 INV-1 holds - valid request-scoped entry
    Given a refine entry with scope "request" and ask "more video options"
    When the system validates the refine entry
    Then the entry should be accepted as valid request-scoped refinement
    # INV-1 holds: scope=request, ask present, no id/action

  @T-UC-001-inv-086-8v @invariant @BR-RULE-086 @error @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-086 INV-8 violated - proposal scope with more_like_this
    Given a refine entry with scope "proposal", id "prop-456", action "more_like_this"
    When the system validates the refine entry
    Then the entry should be rejected: more_like_this not valid for proposal scope
    And the error should include "suggestion" field
    # INV-8 violated: proposal scope with more_like_this

  @T-UC-001-inv-086-10 @invariant @BR-RULE-086 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: BR-RULE-086 INV-10 holds - omit action with ask provided (ask ignored)
    Given a refine entry with scope "product", id "prod-123", action "omit", ask "not relevant"
    When the system validates the refine entry
    Then the entry should be accepted as valid (ask is ignored for omit action)
    # INV-10 holds: ask ignored when action is omit

  @T-UC-001-partition-buying-mode @partition @buying_mode @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: buying_mode partition validation - <partition>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with buying_mode configuration <partition>
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # brand_manifest_policy partitions
    # ----------------------------------------------------------

    Examples: Valid partitions
      | partition         | outcome                          |
      | brief_mode        | request proceeds to brief pipeline |
      | wholesale_mode    | request proceeds to wholesale pipeline |
      | refine_mode       | request proceeds to refine pipeline |
      | pre_v3_default    | request defaults to brief pipeline |

    Examples: Invalid partitions
      | partition                    | outcome                                       |
      | missing_buying_mode          | error: buying_mode is required                 |
      | unknown_value                | error: buying_mode must be one of enum values  |
      | brief_mode_missing_brief     | error: brief required for brief mode           |
      | brief_mode_with_refine       | error: refine prohibited for brief mode        |
      | wholesale_with_brief         | error: brief prohibited for wholesale mode     |
      | wholesale_with_refine        | error: refine prohibited for wholesale mode    |
      | refine_mode_missing_refine   | error: refine required for refine mode         |
      | refine_mode_empty_refine     | error: refine array must have >= 1 entry       |
      | refine_mode_with_brief       | error: brief prohibited for refine mode        |

  @T-UC-001-partition-brand-policy @partition @brand_manifest_policy @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: brand_manifest_policy partition validation - <partition>
    Given the tenant brand_manifest_policy is configured
    When the Buyer Agent sends a get_products request under <partition> conditions
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # brief_policy partitions
    # ----------------------------------------------------------

    Examples: Valid partitions
      | partition                      | outcome                                  |
      | public_policy                  | request proceeds (no restrictions)       |
      | require_auth_authenticated     | request proceeds (auth satisfied)        |
      | require_brand_with_brand       | request proceeds (brand satisfied)       |

    Examples: Invalid partitions
      | partition                      | outcome                                  |
      | require_auth_anonymous         | error: authentication required            |
      | require_brand_no_brand         | error: brand required by policy           |

  @T-UC-001-partition-brief-policy @partition @brief_policy @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: brief_policy partition validation - <partition>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request under <partition> conditions
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # allowed_principal_ids partitions
    # ----------------------------------------------------------

    Examples: Valid partitions
      | partition                   | outcome                                  |
      | policy_disabled             | request proceeds (no check performed)    |
      | policy_allowed              | request proceeds (LLM returned ALLOWED)  |
      | policy_service_unavailable  | request proceeds (fail-open)             |

    Examples: Invalid partitions
      | partition                   | outcome                                  |
      | policy_blocked              | error: POLICY_VIOLATION                  |
      | policy_restricted_review    | error: POLICY_VIOLATION (restricted)     |

  @T-UC-001-partition-principal @partition @allowed_principal_ids @analysis-2026-03-09 @schema-v3.0.0-rc.1 @implementation-only
  Scenario Outline: allowed_principal_ids partition validation - <partition>
    Given a product with allowed_principal_ids configuration
    When the Buyer Agent sends a get_products request under <partition> conditions
    Then the product visibility should be <outcome>
    # ----------------------------------------------------------
    # anonymous_pricing partitions
    # ----------------------------------------------------------

    Examples: Valid partitions
      | partition             | outcome              |
      | unrestricted_null     | visible to all       |
      | unrestricted_empty    | visible to all       |
      | principal_in_list     | visible to caller    |

    Examples: Invalid partitions
      | partition               | outcome                |
      | principal_not_in_list   | product suppressed     |
      | anonymous_restricted    | product suppressed     |

  @T-UC-001-partition-anon-pricing @partition @anonymous_pricing @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: anonymous_pricing partition validation - <partition>
    Given pricing suppression logic is applied
    When the Buyer Agent sends a get_products request under <partition> conditions
    Then the pricing result should be <outcome>
    # ----------------------------------------------------------
    # relevance_score partitions
    # ----------------------------------------------------------

    Examples: Valid partitions
      | partition                    | outcome                          |
      | authenticated_full_pricing   | full pricing options returned    |
      | anonymous_suppressed         | pricing_options set to []        |

  @T-UC-001-partition-relevance @partition @relevance_score @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: relevance_score partition validation - <partition>
    Given AI ranking is applied to products
    When a product has relevance in the <partition> range
    Then the product should be <outcome>
    # ----------------------------------------------------------
    # pricing_option_xor partitions
    # ----------------------------------------------------------

    Examples: Valid partitions
      | partition              | outcome                     |
      | above_threshold        | included in results         |
      | ranking_not_applied    | included (no ranking)       |

    Examples: Invalid partitions
      | partition              | outcome                     |
      | below_threshold        | excluded from results       |

  @T-UC-001-partition-pricing-xor @partition @pricing_option_xor @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: pricing_option_xor partition validation - <partition>
    Given a product has pricing_option configuration
    When the system validates the pricing option XOR constraint
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # product_required_fields partitions
    # ----------------------------------------------------------

    Examples: Valid partitions
      | partition           | outcome                |
      | fixed_pricing       | valid (fixed price)    |
      | auction_pricing     | valid (auction price)  |
      | cpa_model           | valid (CPA always fixed) |

    Examples: Invalid partitions
      | partition           | outcome                |
      | both_set            | invalid (ambiguous)    |
      | neither_set         | invalid (undefined)    |

  @T-UC-001-partition-product-fields @partition @product_required_fields @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: product_required_fields partition validation - <partition>
    Given a product in the database with specific field configuration
    When the system converts the product to AdCP schema
    Then the conversion result should be <outcome>
    # ----------------------------------------------------------
    # catalog_brand_dependency partitions
    # ----------------------------------------------------------

    Examples: Valid partitions
      | partition                          | outcome                              |
      | all_required_present               | conversion succeeds                  |
      | delivery_measurement_from_adapter  | conversion succeeds (adapter default)|
      | format_ids_from_profile            | conversion succeeds (profile resolved)|

    Examples: Invalid partitions
      | partition                              | outcome                            |
      | empty_format_ids                       | conversion fails (ValueError)      |
      | empty_publisher_properties             | conversion fails (ValueError)      |
      | empty_pricing_options                  | conversion fails (ValueError)      |
      | missing_delivery_measurement_provider  | conversion fails (schema violation)|

  @T-UC-001-partition-catalog-brand @partition @catalog @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: catalog brand dependency partition validation - <partition>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with <partition> field combination
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # refinement_applied partitions
    # ----------------------------------------------------------

    Examples: Valid partitions
      | partition               | outcome                                 |
      | catalog_with_brand      | request proceeds to catalog discovery   |
      | no_catalog_no_brand     | request proceeds (no catalog dependency)|
      | no_catalog_with_brand   | request proceeds (brand-scoped)         |

    Examples: Invalid partitions
      | partition               | outcome                                    |
      | catalog_without_brand   | error: brand required when catalog provided |

  @T-UC-001-partition-refinement @partition @refinement_applied @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: refinement_applied partition validation - <partition>
    Given a refine mode response is being assembled
    When the refinement_applied array is in the <partition> state
    Then the response validity should be <outcome>
    # ----------------------------------------------------------
    # refine_entry partitions
    # ----------------------------------------------------------

    Examples: Valid partitions
      | partition                       | outcome                                  |
      | exact_match_all_applied         | valid (all entries applied)               |
      | exact_match_mixed_status        | valid (mixed statuses)                   |
      | exact_match_all_unable          | valid (all unable with notes)            |
      | single_entry                    | valid (minimum 1:1 match)                |
      | status_only_minimal             | valid (only status, no optional fields)  |
      | with_echo_fields                | valid (scope and id echoed)              |
      | absent_in_refine_mode           | valid (SHOULD, not MUST)                 |
      | absent_in_non_refine_mode       | valid (not applicable)                   |

    Examples: Invalid partitions
      | partition                       | outcome                                  |
      | count_mismatch_fewer            | invalid: fewer entries than refine array |
      | count_mismatch_more             | invalid: more entries than refine array  |
      | missing_status                  | invalid: status field required           |
      | invalid_status_value            | invalid: status not in enum              |
      | present_in_non_refine_mode      | invalid: unexpected in non-refine mode   |

  @T-UC-001-partition-refine-entry @partition @refine_entry @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: refine_entry partition validation - <partition>
    Given a refine array entry is being validated
    When the entry matches the <partition> configuration
    Then the validation result should be <outcome>
    # ----------------------------------------------------------
    # delivery_type partitions
    # ----------------------------------------------------------

    Examples: Valid partitions
      | partition                          | outcome                               |
      | request_scope_typical              | valid (request-scoped with ask)       |
      | product_scope_include              | valid (product include)               |
      | product_scope_include_with_ask     | valid (product include with ask)      |
      | product_scope_omit                 | valid (product omit)                  |
      | product_scope_more_like_this       | valid (product more_like_this)        |
      | product_scope_mlt_with_ask         | valid (product MLT with ask)          |
      | proposal_scope_include             | valid (proposal include)              |
      | proposal_scope_include_with_ask    | valid (proposal include with ask)     |
      | proposal_scope_omit               | valid (proposal omit)                 |
      | omit_with_ask_ignored              | valid (ask ignored for omit)          |
      | mixed_scope_array                  | valid (array with mixed scopes)       |

    Examples: Invalid partitions
      | partition                          | outcome                                     |
      | request_scope_with_id              | invalid: id forbidden for request scope      |
      | request_scope_with_action          | invalid: action forbidden for request scope  |
      | request_scope_missing_ask          | invalid: ask required for request scope      |
      | request_scope_empty_ask            | invalid: ask minLength 1 violated            |
      | product_scope_missing_id           | invalid: id required for product scope       |
      | product_scope_empty_id             | invalid: id minLength 1 violated             |
      | product_scope_missing_action       | invalid: action required for product scope   |
      | product_scope_invalid_action       | invalid: action not in product enum          |
      | proposal_scope_more_like_this      | invalid: more_like_this not for proposal     |
      | proposal_scope_missing_id          | invalid: id required for proposal scope      |
      | missing_scope                      | invalid: scope required                      |
      | invalid_scope                      | invalid: scope not in enum                   |
      | empty_array                        | invalid: refine array minItems 1 violated    |
      | id_not_found                       | invalid: referenced ID not found             |

  @T-UC-001-partition-delivery-type @partition @delivery_type @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: delivery_type partition validation - <partition>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with delivery_type filter <partition>
    Then the filter result should be <outcome>
    # ----------------------------------------------------------
    # channels partitions
    # ----------------------------------------------------------

    Examples: Valid partitions
      | partition          | outcome                              |
      | guaranteed         | only guaranteed products returned    |
      | non_guaranteed     | only non-guaranteed products returned|
      | not_provided       | all delivery types returned          |

    Examples: Invalid partitions
      | partition          | outcome                              |
      | unknown_value      | error: unknown delivery_type value   |

  @T-UC-001-partition-channels @partition @channels @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: channels partition validation - <partition>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with channels filter <partition>
    Then the filter result should be <outcome>

    Examples: Valid partitions
      | partition            | outcome                                |
      | display              | products matching display returned     |
      | olv                  | products matching olv returned         |
      | social               | products matching social returned      |
      | search               | products matching search returned      |
      | ctv                  | products matching ctv returned         |
      | linear_tv            | products matching linear_tv returned   |
      | radio                | products matching radio returned       |
      | streaming_audio      | products matching streaming_audio returned |
      | podcast              | products matching podcast returned     |
      | dooh                 | products matching dooh returned        |
      | ooh                  | products matching ooh returned         |
      | print                | products matching print returned       |
      | cinema               | products matching cinema returned      |
      | email                | products matching email returned       |
      | gaming               | products matching gaming returned      |
      | retail_media         | products matching retail_media returned|
      | influencer           | products matching influencer returned  |
      | affiliate            | products matching affiliate returned   |
      | product_placement    | products matching product_placement returned |
      | not_provided         | all channels returned                  |

    Examples: Invalid partitions
      | partition            | outcome                                |
      | unknown_channel      | error: unknown channel value            |
      | empty_array          | error: channels minItems 1 violated     |

  @T-UC-001-boundary-buying-mode @boundary @buying_mode @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: buying_mode boundary validation - <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request at boundary <boundary_point>
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # brand_manifest_policy boundaries
    # ----------------------------------------------------------

    Examples: Boundary values
      | boundary_point                                                            | outcome   |
      | buying_mode='brief' with brief present (valid brief mode)                 | valid     |
      | buying_mode='wholesale' with no brief, no refine (valid wholesale mode)   | valid     |
      | buying_mode='refine' with refine=[1 entry] (valid refine mode, minItems boundary) | valid |
      | buying_mode absent, pre-v3 client (defaulted to brief)                    | valid     |
      | buying_mode absent, v3 client (required field missing)                    | invalid   |
      | buying_mode='auction' (unknown enum value)                                | invalid   |
      | buying_mode='brief', brief absent (required companion missing)            | invalid   |
      | buying_mode='brief', refine present (prohibited companion present)        | invalid   |
      | buying_mode='wholesale', brief present (prohibited companion present)     | invalid   |
      | buying_mode='wholesale', refine present (prohibited companion present)    | invalid   |
      | buying_mode='refine', refine absent (required companion missing)          | invalid   |
      | buying_mode='refine', refine=[] (minItems:1 boundary violation)           | invalid   |
      | buying_mode='refine', brief present (prohibited companion present)        | invalid   |

  @T-UC-001-boundary-brand-policy @boundary @brand_manifest_policy @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: brand_manifest_policy boundary validation - <boundary_point>
    Given the tenant brand_manifest_policy is configured
    When the Buyer Agent sends a get_products request at boundary <boundary_point>
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # brief_policy boundaries
    # ----------------------------------------------------------

    Examples: Boundary values
      | boundary_point                   | outcome   |
      | public policy (no restrictions)  | valid     |
      | require_auth + authenticated     | valid     |
      | require_auth + anonymous         | invalid   |
      | require_brand + brand present    | valid     |
      | require_brand + no brand         | invalid   |

  @T-UC-001-boundary-brief-policy @boundary @brief_policy @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: brief_policy boundary validation - <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request at boundary <boundary_point>
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # allowed_principal_ids boundaries
    # ----------------------------------------------------------

    Examples: Boundary values
      | boundary_point                               | outcome   |
      | policy disabled                              | valid     |
      | LLM returns ALLOWED                          | valid     |
      | LLM returns BLOCKED                          | invalid   |
      | LLM service unavailable (fail-open)          | valid     |

  @T-UC-001-boundary-principal @boundary @allowed_principal_ids @analysis-2026-03-09 @schema-v3.0.0-rc.1 @implementation-only
  Scenario Outline: allowed_principal_ids boundary validation - <boundary_point>
    Given a product with specific allowed_principal_ids configuration
    When the Buyer Agent sends a get_products request at boundary <boundary_point>
    Then the visibility result should be <outcome>
    # ----------------------------------------------------------
    # anonymous_pricing boundaries
    # ----------------------------------------------------------

    Examples: Boundary values
      | boundary_point                     | outcome   |
      | allowed_principal_ids null         | valid     |
      | allowed_principal_ids empty array  | valid     |
      | principal in allow-list            | valid     |
      | principal not in allow-list        | invalid   |
      | anonymous + restricted product     | invalid   |

  @T-UC-001-boundary-anon-pricing @boundary @anonymous_pricing @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: anonymous_pricing boundary validation - <boundary_point>
    Given pricing suppression logic is applied
    When the Buyer Agent sends a get_products request at boundary <boundary_point>
    Then the pricing result should be <outcome>
    # ----------------------------------------------------------
    # relevance_score boundaries
    # ----------------------------------------------------------

    Examples: Boundary values
      | boundary_point                     | outcome   |
      | authenticated (full pricing)       | valid     |
      | anonymous (pricing suppressed)     | valid     |

  @T-UC-001-boundary-relevance @boundary @relevance_score @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: relevance_score boundary validation - <boundary_point>
    Given AI ranking is applied to products
    When a product has relevance score at boundary <boundary_point>
    Then the product should be <outcome>
    # ----------------------------------------------------------
    # pricing_option_xor boundaries
    # ----------------------------------------------------------

    Examples: Boundary values
      | boundary_point                               | outcome   |
      | score = 0.1 (threshold, included)            | valid     |
      | score = 0.09 (just below threshold)          | invalid   |
      | score = 0.0 (minimum)                        | invalid   |
      | ranking not applied (no brief)               | valid     |

  @T-UC-001-boundary-pricing-xor @boundary @pricing_option_xor @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: pricing_option_xor boundary validation - <boundary_point>
    Given a product has pricing_option configuration
    When the system validates the pricing option at boundary <boundary_point>
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # product_required_fields boundaries
    # ----------------------------------------------------------

    Examples: Boundary values
      | boundary_point                            | outcome   |
      | fixed_price only (valid fixed)            | valid     |
      | floor_price only (valid auction)          | valid     |
      | both present (ambiguous)                  | invalid   |
      | neither present (undefined)               | invalid   |

  @T-UC-001-boundary-product-fields @boundary @product_required_fields @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: product_required_fields boundary validation - <boundary_point>
    Given a product in the database with specific field configuration
    When the system converts the product at boundary <boundary_point>
    Then the conversion result should be <outcome>
    # ----------------------------------------------------------
    # catalog_brand_dependency boundaries
    # ----------------------------------------------------------

    Examples: Boundary values
      | boundary_point                                                    | outcome   |
      | all arrays with 1 item + delivery_measurement with provider       | valid     |
      | format_ids empty (0 items)                                        | invalid   |
      | publisher_properties empty (0 items)                              | invalid   |
      | pricing_options empty (0 items)                                   | invalid   |
      | delivery_measurement absent from DB, adapter provides default     | valid     |
      | delivery_measurement without provider field                       | invalid   |

  @T-UC-001-boundary-catalog-brand @boundary @catalog @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: catalog brand dependency boundary validation - <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request at boundary <boundary_point>
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # refinement_applied boundaries
    # ----------------------------------------------------------

    Examples: Boundary values
      | boundary_point                                                       | outcome   |
      | catalog present + brand present (both provided)                      | valid     |
      | catalog absent + brand absent (neither provided)                     | valid     |
      | catalog absent + brand present (brand alone)                         | valid     |
      | catalog present + brand absent (dependency violation)                | invalid   |
      | catalog present + brand with domain only (minimal brand-ref)         | valid     |
      | catalog present + brand with domain and brand_id (full brand-ref)    | valid     |

  @T-UC-001-boundary-refinement @boundary @refinement_applied @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: refinement_applied boundary validation - <boundary_point>
    Given a refine mode response is being validated
    When the refinement_applied array is at boundary <boundary_point>
    Then the validity should be <outcome>
    # ----------------------------------------------------------
    # refine_entry boundaries
    # ----------------------------------------------------------

    Examples: Boundary values
      | boundary_point                                                             | outcome   |
      | 1 refine entry, 1 refinement_applied entry (minimum valid pair)            | valid     |
      | 3 refine entries, 3 refinement_applied entries (multi-entry exact match)    | valid     |
      | 3 refine entries, 2 refinement_applied entries (fewer than expected)        | invalid   |
      | 1 refine entry, 2 refinement_applied entries (more than expected)           | invalid   |
      | 0 refinement_applied entries for N refine entries (empty array)             | invalid   |
      | status='applied' (ask fully fulfilled)                                     | valid     |
      | status='partial' (ask partially fulfilled)                                 | valid     |
      | status='unable' (ask could not be fulfilled)                               | valid     |
      | status missing from entry (required field absent)                          | invalid   |
      | status='rejected' (unknown enum value)                                     | invalid   |
      | scope echoed from refine entry (cross-validation present)                  | valid     |
      | scope omitted from entry (optional field, still valid)                     | valid     |
      | scope='campaign' (unknown enum value)                                      | invalid   |
      | refinement_applied present in refine mode response (SHOULD)                | valid     |
      | refinement_applied absent in refine mode response (SHOULD, not MUST — allowed) | valid |
      | refinement_applied absent in brief mode response (correct — not applicable)    | valid |
      | refinement_applied present in brief mode response (unexpected)             | invalid   |
      | status='partial' with notes (recommended practice)                         | valid     |
      | status='partial' without notes (allowed but not recommended)               | valid     |
      | status='unable' with notes (recommended practice)                          | valid     |
      | status='applied' without notes (typical — no explanation needed)           | valid     |

  @T-UC-001-boundary-refine-entry @boundary @refine_entry @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: refine_entry boundary validation - <boundary_point>
    Given a refine array entry is being validated
    When the entry is at boundary <boundary_point>
    Then the validation result should be <outcome>
    # ----------------------------------------------------------
    # delivery_type boundaries
    # ----------------------------------------------------------

    Examples: Boundary values
      | boundary_point                                                    | outcome   |
      | ask at minLength (1 char)                                         | valid     |
      | ask empty string (0 chars)                                        | invalid   |
      | id at minLength (1 char)                                          | valid     |
      | id empty string (0 chars)                                         | invalid   |
      | refine array with 1 entry (at minItems)                           | valid     |
      | refine array with 0 entries (below minItems)                      | invalid   |
      | request scope with only scope+ask (no extra fields)               | valid     |
      | request scope with scope+ask+id (extra field)                     | invalid   |
      | request scope with scope+ask+action (extra field)                 | invalid   |
      | product action=include                                            | valid     |
      | product action=omit                                               | valid     |
      | product action=more_like_this                                     | valid     |
      | product action=replace (unknown)                                  | invalid   |
      | proposal action=include                                           | valid     |
      | proposal action=omit                                              | valid     |
      | proposal action=more_like_this (not in proposal enum)             | invalid   |
      | scope=request                                                     | valid     |
      | scope=product                                                     | valid     |
      | scope=proposal                                                    | valid     |
      | scope=campaign (unknown)                                          | invalid   |
      | scope absent                                                      | invalid   |
      | omit action with ask present (ask ignored)                        | valid     |
      | ask absent on product include (optional field omitted)            | valid     |

  @T-UC-001-boundary-delivery-type @boundary @delivery_type @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: delivery_type boundary validation - <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with delivery_type at boundary <boundary_point>
    Then the filter result should be <outcome>
    # ----------------------------------------------------------
    # channels boundaries
    # ----------------------------------------------------------

    Examples: Boundary values
      | boundary_point                           | outcome   |
      | guaranteed (first enum value)            | valid     |
      | non_guaranteed (last enum value)         | valid     |
      | Not provided (no delivery type filter)   | valid     |
      | Unknown string not in enum               | invalid   |

  @T-UC-001-boundary-channels @boundary @channels @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario Outline: channels boundary validation - <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with channels at boundary <boundary_point>
    Then the filter result should be <outcome>

    Examples: Boundary values
      | boundary_point                           | outcome   |
      | display (first enum value)               | valid     |
      | product_placement (last enum value)      | valid     |
      | Not provided (no channel filter)         | valid     |
      | Unknown string not in enum               | invalid   |
      | Empty array                              | invalid   |

  @T-UC-001-nfr-001 @nfr @nfr-001 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: NFR-001 - Security hardening on product discovery
    Given the Seller Agent enforces security hardening
    When the Buyer Agent sends a get_products request
    Then the request should be validated against schema before processing
    And authentication should be checked before any data access
    And no internal system details should leak in error responses

  @T-UC-001-nfr-002 @nfr @nfr-002 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: NFR-002 - Prompt injection defense for brief and refine.ask
    Given the Seller Agent enforces prompt injection defense
    When the Buyer Agent sends a get_products request with a brief containing injection attempts
    Then the brief should be sanitized before passing to the LLM
    And the system should not execute injected instructions

  @T-UC-001-nfr-003 @nfr @nfr-003 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: NFR-003 - Audit logging for product discovery
    Given the Seller Agent has audit logging enabled
    When the Buyer Agent sends a get_products request
    Then the request should be logged with timestamp, principal_id, and request parameters
    And the response should be logged with status and product count
    And policy check results should be logged (operation: policy_check)

  @T-UC-001-nfr-004 @nfr @nfr-004 @analysis-2026-03-09 @schema-v3.0.0-rc.1
  Scenario: NFR-004 - Response latency SLA for product discovery
    Given the Seller Agent has latency SLA requirements
    When the Buyer Agent sends a get_products request
    Then the response should be returned within the configured SLA threshold
    And LLM calls (policy check, ranking) should have timeout guards
    And external calls (property list, signals agents) should have timeout guards

  @T-UC-001-sandbox-happy @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account receives simulated products with sandbox flag
    Given the Buyer is authenticated with a valid principal_id
    And the request targets a sandbox account
    When the Buyer Agent sends a get_products request with:
    | field       | value                            |
    | buying_mode | brief                            |
    | brief       | Display ads for tech audience Q4 |
    | brand       | {"domain": "acme.com"}           |
    Then the response status should be "completed"
    And the response should contain "products" array
    And the response should include sandbox equals true
    And no real ad platform API calls should have been made
    And no real billing records should have been created
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-2: real ad platform calls suppressed
    # BR-RULE-209 INV-3: real billing suppressed
    # BR-RULE-209 INV-4: response includes sandbox: true

  @T-UC-001-sandbox-production @invariant @br-rule-209 @sandbox
  Scenario: Production account response does not include sandbox flag
    Given the Buyer is authenticated with a valid principal_id
    And the request targets a production account
    When the Buyer Agent sends a get_products request with:
    | field       | value                            |
    | buying_mode | brief                            |
    | brief       | Display ads for tech audience Q4 |
    | brand       | {"domain": "acme.com"}           |
    Then the response status should be "completed"
    And the response should contain "products" array
    And the response should not include a sandbox field
    # BR-RULE-209 INV-5: production account -> sandbox absent

  @T-UC-001-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account with invalid input returns real validation error
    Given the Buyer is authenticated with a valid principal_id
    And the request targets a sandbox account
    When the Buyer Agent sends a get_products request with:
    | field       | value     |
    | buying_mode | brief     |
    Then the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

