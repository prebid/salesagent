# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

Feature: BR-UC-001 Discover Available Inventory
  As a Buyer (via Buyer Agent)
  I want to discover what advertising inventory matches my campaign requirements
  So that I can evaluate products and proceed to purchasing

  # Postconditions verified:
  #   POST-S1: Buyer knows what inventory matches their request (may be empty)
  #   POST-S2: Buyer can evaluate each product's pricing, formats, reporting_capabilities (delivery_measurement optional), and catalog match data
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



  @T-UC-001-main @main-flow @analysis-2026-03-09 @schema-v3.1
  Scenario: Main flow - brief mode discovery via MCP
    Given the Buyer is authenticated with a valid principal_id
    And the tenant brand_manifest_policy is "require_auth"
    And the tenant has an advertising_policy configured
    And the product catalog contains products with valid schema (format_ids, publisher_properties, pricing_options, reporting_capabilities)
    When the Buyer Agent sends a get_products request with:
    | field        | value                               |
    | buying_mode  | brief                               |
    | brief        | Display ads for tech audience Q4     |
    | brand        | {"domain": "acme.com"}              |
    Then the response status should be "completed"
    And the response should contain "products" array
    And each product should have product_id, name, format_ids, publisher_properties, pricing_options, and reporting_capabilities
    And the products should be ordered by relevance_score descending
    And each product should include brief_relevance explanation
    # POST-S1: Buyer knows what inventory matches their brief
    # POST-S2: Buyer can evaluate each product's pricing, formats, reporting_capabilities
    # POST-S3: Products ordered by relevance to brief
    # POST-S4: Only authorized products visible
    # POST-S5: Status is completed

  @T-UC-001-alt-wholesale @alternative @alt-wholesale @analysis-2026-03-09 @schema-v3.1
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
    # POST-S2: Buyer can evaluate pricing, formats, reporting_capabilities (delivery_measurement optional)
    # POST-S4: Only authorized products visible
    # POST-S5: Status is completed

  @T-UC-001-alt-refine @alternative @alt-refine @analysis-2026-03-09 @schema-v3.1
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

  @T-UC-001-alt-anonymous @alternative @alt-anonymous @analysis-2026-03-09 @schema-v3.1
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

  @T-UC-001-alt-empty @alternative @alt-empty @analysis-2026-03-09 @schema-v3.1
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

  @T-UC-001-incomplete @partial-completion @schema-v3.1 @analysis-2026-05-26
  Scenario: Partial completion - a scope exceeds time_budget and is reported via incomplete[]
    Given the Buyer is authenticated with a valid principal_id
    And resolving the forecast scope cannot complete within the buyer's time_budget
    When the Buyer Agent sends a get_products request with:
    | field       | value                                  |
    | buying_mode | brief                                  |
    | brief       | Display ads for tech audience Q4       |
    | time_budget | {"interval": 5, "unit": "seconds"}     |
    Then the response status should be "completed"
    And the response should contain "products" array
    And the response should contain a non-empty "incomplete" array
    And each incomplete entry should have a "scope" in ["products", "pricing", "forecast", "proposals"]
    And each incomplete entry should have a "description"
    And an incomplete entry may include an "estimated_wait" duration
    And the response should not be reported as an error
    # POST-S1: Buyer knows what inventory matches (partial)
    # POST-S5: Status is completed
    # POST-S11: Unfinished scopes signaled via incomplete[], not an error
    # INT-006 (MINIMAL guarantee) / INT-013 (SUCCESS): partial completion signaled
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-preferred-delivery-types @alternative @schema-v3.1 @analysis-2026-05-26
  Scenario: preferred_delivery_types orders curation preference without hard-excluding other delivery types
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with:
    | field                   | value                       |
    | buying_mode             | brief                       |
    | brief                   | Video ads for US market     |
    | preferred_delivery_types | ["guaranteed"]             |
    Then the response status should be "completed"
    And the response should contain "products" array
    And products matching the preferred delivery type should be ordered ahead of others
    And products with other delivery types should not be excluded from the response
    # Contrast with filters.delivery_type which hard-excludes; preferred_delivery_types is a soft curation preference
    # INT-002: scope via mode/filters/catalog/property_list/preferred_delivery_types
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-required-policies @main-flow @schema-v3.1 @analysis-2026-05-26
  Scenario: required_policies filters products to those complying with every requested registry policy
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with:
    | field            | value                              |
    | buying_mode      | brief                              |
    | brief            | Display ads for tech audience Q4   |
    | required_policies | ["no_political", "coppa_safe"]    |
    Then the response status should be "completed"
    And the response should contain "products" array
    And every returned product should comply with each requested registry policy id
    # INT-007: policy-compliant inventory only (incl. buyer required_policies)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-filter-diagnostics @alternative @alt-empty @schema-v3.1 @analysis-2026-05-26
  Scenario: filter_diagnostics distinguishes filter-excluded from no-inventory on an empty result
    Given the Buyer is authenticated with a valid principal_id
    And inventory exists but every candidate is excluded by a filter
    When the Buyer Agent sends a get_products request with:
    | field       | value                                              |
    | buying_mode | brief                                              |
    | brief       | Video ads for US market                            |
    | filters     | {"delivery_type": "guaranteed", "countries": ["US"]} |
    Then the response status should be "completed"
    And the response "products" array should be empty
    And the response may include a "filter_diagnostics" block
    And the filter_diagnostics should report "total_candidates"
    And the filter_diagnostics should report per-filter "excluded_by" counts
    And the filter_diagnostics "semantics" should be one of ["only", "any", "approximate"]
    # Observability-only and counts-only; buyer must inspect semantics before arithmetic on counts
    # POST-S1: empty is a valid success; diagnostics disambiguate filter-excluded vs no-inventory

  @T-UC-001-alt-filtered @alternative @alt-filtered @analysis-2026-03-09 @schema-v3.1
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

  @T-UC-001-alt-paginated @alternative @alt-paginated @analysis-2026-03-09 @schema-v3.1
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
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-alt-paginated-next @alternative @alt-paginated @analysis-2026-03-09 @schema-v3.1
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
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-alt-paginated-last @alternative @alt-paginated @analysis-2026-03-09 @schema-v3.1
  Scenario: Paginated discovery - last page with no more results
    Given the Buyer is authenticated with a valid principal_id
    And a previous get_products response indicated more results
    When the Buyer Agent sends a get_products request with the cursor for the last page
    Then the response status should be "completed"
    And the response pagination should have "has_more" as false
    And the response pagination should NOT include a "cursor" value
    # POST-S7: Buyer knows this is the last page

  @T-UC-001-alt-proposal @alternative @alt-proposal @analysis-2026-03-09 @schema-v3.1
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

  @T-UC-001-alt-catalog @alternative @alt-catalog @analysis-2026-03-09 @schema-v3.1
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

  @T-UC-001-alt-sparse @alternative @alt-sparse @analysis-2026-03-09 @schema-v3.1
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

  @T-UC-001-ext-a @extension @ext-a @error @analysis-2026-03-09 @schema-v3.1
  Scenario: Extension *a - brief blocked by advertising policy
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has an advertising_policy configured and enabled
    And the brief content violates the advertising policy (LLM returns BLOCKED)
    When the Buyer Agent sends a get_products request with:
    | field        | value                               |
    | buying_mode  | brief                               |
    | brief        | Tobacco advertising for teens        |
    Then the operation should fail with error code "POLICY_VIOLATION"
    And the error code should be "POLICY_VIOLATION"
    And the error message should contain the LLM-provided reason
    And the error should include "suggestion" field
    And the suggestion should contain "revise" or "comply"
    # POST-F1: System state is unchanged
    # POST-F2: Buyer knows brief violated policy
    # POST-F3: Buyer knows to revise brief
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-ext-a-restricted @extension @ext-a @error @analysis-2026-03-09 @schema-v3.1
  Scenario: Extension *a - brief restricted with manual review required
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has an advertising_policy with require_manual_review enabled
    And the brief content is flagged as RESTRICTED by the LLM
    When the Buyer Agent sends a get_products request with:
    | field        | value                               |
    | buying_mode  | brief                               |
    | brief        | Alcohol advertising campaign         |
    Then the operation should fail with error code "POLICY_VIOLATION"
    And the error code should be "POLICY_VIOLATION"
    And the error message should include restrictions details
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged
    # POST-F2: Buyer knows brief was restricted
    # POST-F3: Buyer knows how to revise
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-ext-a-failopen @extension @ext-a @degradation @analysis-2026-03-09 @schema-v3.1
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

  @T-UC-001-ext-b @extension @ext-b @error @analysis-2026-03-09 @schema-v3.1
  Scenario: Extension *b - authentication required but caller is anonymous
    Given the Buyer has no authentication credentials
    And the tenant brand_manifest_policy is "require_auth"
    When the Buyer Agent sends a get_products request with:
    | field        | value        |
    | buying_mode  | brief        |
    | brief        | Display ads  |
    Then the operation should fail with error code "authentication_error"
    And the error code should be "authentication_error"
    And the error message should contain "Authentication required"
    And the error should include "suggestion" field
    And the suggestion should contain "credentials" or "authenticate"
    # POST-F1: System state is unchanged
    # POST-F2: Buyer knows authentication is required
    # POST-F3: Buyer knows to obtain credentials

  @T-UC-001-ext-c @extension @ext-c @error @analysis-2026-03-09 @schema-v3.1
  Scenario: Extension *c - brand required but not provided
    Given the Buyer is authenticated with a valid principal_id
    And the tenant brand_manifest_policy is "require_brand"
    When the Buyer Agent sends a get_products request with:
    | field        | value                               |
    | buying_mode  | brief                               |
    | brief        | Display ads for tech audience        |
    Then the operation should fail with error code "validation_error"
    And the error code should be "validation_error"
    And the error message should contain "Brand required"
    And the error should include "suggestion" field
    And the suggestion should contain "brand" or "domain"
    # POST-F1: System state is unchanged
    # POST-F2: Buyer knows brand is required
    # POST-F3: Buyer knows to provide brand reference

  @T-UC-001-ext-d @extension @ext-d @error @analysis-2026-03-09 @schema-v3.1
  Scenario Outline: Extension *d - buying mode constraint violation - <violation>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with <invalid_fields>
    Then the operation should fail with error code "validation_error"
    And the error code should be "validation_error"
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

  @T-UC-001-inv-001 @invariant @BR-RULE-001 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-001 INV-1 holds - require_auth policy with authenticated caller
    Given the tenant brand_manifest_policy is "require_auth"
    And the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a valid get_products request
    Then the request should proceed to product discovery
    # INV-1 holds: policy is require_auth and request is authenticated
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-001-v @invariant @BR-RULE-001 @error @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-001 INV-1 violated - require_auth policy with unauthenticated caller
    Given the tenant brand_manifest_policy is "require_auth"
    And the Buyer has no authentication credentials
    When the Buyer Agent sends a get_products request
    Then the operation should fail
    And the error code should be "authentication_error"
    And the error should indicate authentication is required
    And the error should include "suggestion" field
    # INV-1 violated: policy is require_auth and request is unauthenticated
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-001-2 @invariant @BR-RULE-001 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-001 INV-2 holds - require_brand policy with brand provided
    Given the tenant brand_manifest_policy is "require_brand"
    And the Buyer is authenticated with a valid principal_id
    And the request includes brand {"domain": "acme.com"}
    When the Buyer Agent sends a valid get_products request
    Then the request should proceed to product discovery
    # INV-2 holds: policy is require_brand and brand is provided
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-001-2v @invariant @BR-RULE-001 @error @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-001 INV-2 violated - require_brand policy without brand
    Given the tenant brand_manifest_policy is "require_brand"
    And the Buyer is authenticated with a valid principal_id
    And the request does NOT include a brand field
    When the Buyer Agent sends a get_products request
    Then the operation should fail
    And the error code should be "validation_error"
    And the error should indicate brand is required
    And the error should include "suggestion" field
    # INV-2 violated: policy is require_brand and no brand provided
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-001-3 @invariant @BR-RULE-001 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-001 INV-3 holds - public policy allows any caller
    Given the tenant brand_manifest_policy is "public"
    And the Buyer has no authentication credentials
    When the Buyer Agent sends a get_products request
    Then the request should proceed to product discovery
    # INV-3 holds: policy is public, request proceeds regardless

  @T-UC-001-inv-002 @invariant @BR-RULE-002 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-002 INV-1 violated - policy enabled and brief BLOCKED
    Given the tenant has advertising_policy enabled
    And the LLM evaluates the brief as BLOCKED
    When the Buyer Agent sends a get_products request with a non-compliant brief
    Then the operation should fail with error code "POLICY_VIOLATION"
    And the error code should be "POLICY_VIOLATION"
    # INV-1 violated: policy enabled and brief content is BLOCKED
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-002-2 @invariant @BR-RULE-002 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-002 INV-2 violated - RESTRICTED with manual review required
    Given the tenant has advertising_policy enabled with require_manual_review
    And the LLM evaluates the brief as RESTRICTED
    When the Buyer Agent sends a get_products request
    Then the operation should fail
    And the error code should be "POLICY_VIOLATION"
    # INV-2 violated: policy enabled, RESTRICTED with manual review required
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-002-3 @invariant @BR-RULE-002 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-002 INV-3 holds - policy disabled, check skipped
    Given the tenant has advertising_policy disabled
    When the Buyer Agent sends a get_products request with any brief content
    Then the request should proceed to product discovery without policy check
    # INV-3 holds: policy disabled, check skipped
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-002-4 @invariant @BR-RULE-002 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-002 INV-4 holds - policy service unavailable, fail-open
    Given the tenant has advertising_policy enabled
    And the LLM policy service is unavailable
    When the Buyer Agent sends a get_products request
    Then the request should proceed (fail-open behavior)
    # INV-4 holds: policy service unavailable, fail-open
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-002-5 @invariant @BR-RULE-002 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-002 INV-5 holds - policy evaluation returns ALLOWED
    Given the tenant has advertising_policy enabled
    And the LLM evaluates the brief as ALLOWED
    When the Buyer Agent sends a get_products request with a compliant brief
    Then the request should proceed with full product catalog
    # INV-5 holds: policy evaluation returns ALLOWED
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-002-6 @invariant @BR-RULE-002 @analysis-2026-05-25 @schema-v3.1
  Scenario: BR-RULE-002 INV-6 holds - policy requires human review, plan escalated
    Given the tenant has advertising_policy enabled
    And the resolved policy has "requires_human_review" set to true
    When the Buyer Agent sends a get_products request
    Then the plan should be escalated for human review regardless of the enforcement level
    # INV-6 holds: requires_human_review escalates regardless of must/should/may (v3.1 governance)

  @T-UC-001-inv-003-2v @invariant @BR-RULE-003 @analysis-2026-03-09 @schema-v3.1 @implementation-only
  Scenario: BR-RULE-003 INV-2 violated - principal NOT in allowed list
    Given a product has allowed_principal_ids ["buyer-456"]
    And the Buyer is authenticated as principal "buyer-123"
    When the Buyer Agent sends a get_products request
    Then the product should NOT be visible in results (silently filtered)
    # INV-2 violated: principal is NOT in allow-list (no error, product just hidden)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-003-3 @invariant @BR-RULE-003 @analysis-2026-03-09 @schema-v3.1 @implementation-only
  Scenario: BR-RULE-003 INV-3 holds - no allowed_principal_ids restriction
    Given a product has allowed_principal_ids as null
    When the Buyer Agent sends a get_products request
    Then the product should be visible to all principals
    # INV-3 holds: no restriction, visible to all
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-003-4v @invariant @BR-RULE-003 @analysis-2026-03-09 @schema-v3.1 @implementation-only
  Scenario: BR-RULE-003 INV-4 violated - anonymous request with restricted product
    Given a product has allowed_principal_ids ["buyer-123"]
    And the Buyer has no authentication credentials
    And the tenant brand_manifest_policy is "public"
    When the Buyer Agent sends a get_products request
    Then the product should NOT be visible in results (silently filtered)
    # INV-4 violated: anonymous request and product has allowed_principal_ids (no error, product just hidden)

  @T-UC-001-inv-004 @invariant @BR-RULE-004 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-004 INV-1 holds - anonymous request, pricing suppressed
    Given the Buyer has no authentication credentials
    And the tenant brand_manifest_policy is "public"
    When the Buyer Agent sends a get_products request
    Then every product should have pricing_options as an empty array
    # INV-1 holds: anonymous request, pricing stripped
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-004-2 @invariant @BR-RULE-004 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-004 INV-2 holds - authenticated request, full pricing retained
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request
    Then every product should retain its full pricing_options
    # INV-2 holds: authenticated request, full pricing

  @T-UC-001-inv-005 @invariant @BR-RULE-005 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-005 INV-1 violated - product below 0.1 threshold excluded
    Given the Buyer is authenticated with a valid principal_id
    And ranking is applied (brief provided, ranking prompt configured)
    And a product has relevance_score 0.05
    When the system applies AI ranking
    Then the product should be excluded from results
    # INV-1 violated: ranking applied and product scores < 0.1
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-005-2 @invariant @BR-RULE-005 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-005 INV-2 holds - product at or above 0.1 threshold included
    Given the Buyer is authenticated with a valid principal_id
    And ranking is applied (brief provided, ranking prompt configured)
    And a product has relevance_score 0.15
    When the system applies AI ranking
    Then the product should be included in results sorted by score descending
    # INV-2 holds: ranking applied and product scores >= 0.1
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-005-3 @invariant @BR-RULE-005 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-005 INV-3 holds - no brief provided, no threshold applied
    Given the Buyer is authenticated with a valid principal_id
    And no brief is provided (wholesale mode)
    When the Buyer Agent sends a get_products request
    Then all products should be returned without ranking or threshold filtering
    # INV-3 holds: no brief, no threshold applied
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-005-4 @invariant @BR-RULE-005 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-005 INV-4 holds - ranking service fails, products returned unranked
    Given the Buyer is authenticated with a valid principal_id
    And ranking is configured but the AI ranking service fails
    When the Buyer Agent sends a get_products request with a brief
    Then products should be returned unranked with no threshold applied
    # INV-4 holds: ranking service fails, products returned unranked

  @T-UC-001-inv-006 @invariant @BR-RULE-006 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-006 INV-1 holds - fixed_price set, floor_price null (fixed pricing)
    Given a product has a pricing_option with fixed_price set and floor_price null
    When the system validates the pricing option
    Then the pricing option is valid (fixed pricing model)
    # INV-1 holds: fixed_price set and floor_price null
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-006-2 @invariant @BR-RULE-006 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-006 INV-2 holds - floor_price set, fixed_price null (auction pricing)
    Given a product has a pricing_option with floor_price set and fixed_price null
    When the system validates the pricing option
    Then the pricing option is valid (auction pricing model)
    # INV-2 holds: floor_price set and fixed_price null
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-006-3v @invariant @BR-RULE-006 @error @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-006 INV-3 violated - both fixed_price and floor_price set
    Given a product has a pricing_option with both fixed_price and floor_price set
    When the system validates the pricing option
    Then the pricing option is invalid (ambiguous pricing model)
    And the error should include "suggestion" field
    # INV-3 violated: both set
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-006-4v @invariant @BR-RULE-006 @error @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-006 INV-4 violated - neither fixed_price nor floor_price set
    Given a product has a pricing_option with neither fixed_price nor floor_price set
    When the system validates the pricing option
    Then the pricing option is invalid (undefined pricing)
    And the error should include "suggestion" field
    # INV-4 violated: neither set
    # NOTE: this scenario captures the salesagent CODE-enforcement facet — code still
    #       rejects neither-set (schema-vs-code gap). The v3.1 SCHEMA-layer facet, where
    #       neither-set is valid, is verified by T-UC-001-inv-006-4s below.
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-006-4s @invariant @BR-RULE-006 @analysis-2026-05-25 @schema-v3.1
  Scenario: BR-RULE-006 INV-4 holds at schema layer - neither set is v3.1 schema-valid auction
    Given a product has a pricing_option with neither fixed_price nor floor_price set
    When the v3.1 schema validates the pricing option
    Then the pricing option is valid (auction-based, no explicit floor)
    # INV-4 schema layer: v3.1 relaxed neither-set to valid (auction without explicit floor);
    #   salesagent code still rejects it — see T-UC-001-inv-006-4v for the code-enforcement gap
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-006-5 @invariant @BR-RULE-006 @analysis-2026-05-25 @schema-v3.1
  Scenario: BR-RULE-006 INV-5 holds - max_bid=true on bid-based auction model
    Given a product has a bid-based auction pricing_option (cpm/cpc/cpcv/cpv/vcpm) with max_bid=true
    When the system validates the pricing option
    Then the pricing option is valid and bid_price is interpreted as the buyer ceiling
    # INV-5 holds: max_bid=true means bid_price is the buyer's ceiling, not an exact price

  @T-UC-001-inv-007 @invariant @BR-RULE-007 @analysis-2026-05-25 @schema-v3.1
  Scenario: BR-RULE-007 INV-1 holds - product has all required fields
    Given a product has >= 1 format_id, >= 1 publisher_property, >= 1 pricing_option, a delivery_measurement object, and a reporting_capabilities object
    When the system converts the product to AdCP schema
    Then the conversion should succeed
    # INV-1 holds: all required fields present (v3.1 adds reporting_capabilities; delivery_measurement no longer schema-required)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-007-6 @invariant @BR-RULE-007 @analysis-2026-05-25 @schema-v3.1
  Scenario: BR-RULE-007 INV-6 holds - missing reporting_capabilities, minimal default applied
    Given a product has no reporting_capabilities in the database
    When the system converts the product to AdCP schema
    Then the conversion should succeed with a minimal default reporting_capabilities
    # INV-6 holds: minimal default for reporting_capabilities (parallels INV-5 delivery_measurement fallback)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-007-5 @invariant @BR-RULE-007 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-007 INV-5 holds - missing delivery_measurement, adapter provides default
    Given a product has no delivery_measurement in the database
    When the system converts the product to AdCP schema
    Then the conversion should succeed with adapter-specific default delivery_measurement
    # INV-5 holds: adapter fallback for delivery_measurement

  @T-UC-001-inv-079 @invariant @BR-RULE-079 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-079 INV-6 holds - pre-v3 client without buying_mode defaults to brief
    Given a pre-v3 client sends a get_products request without buying_mode
    And the request includes a brief
    When the system processes the request
    Then the system should default buying_mode to "brief"
    And the request should proceed through the brief-mode pipeline
    # INV-6 holds: pre-v3 backward compatibility

  @T-UC-001-inv-084 @invariant @BR-RULE-084 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-084 INV-1 holds - catalog with brand present
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with catalog and brand
    Then the request should proceed to catalog-driven discovery
    # INV-1 holds: catalog present and brand present
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-084-2v @invariant @BR-RULE-084 @error @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-084 INV-2 violated - catalog without brand
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with catalog but no brand
    Then the operation should fail with validation error
    And the error code should be "BRAND_REQUIRED"
    And the error should indicate brand is required when catalog is provided
    And the error should include "suggestion" field
    # INV-2 violated: catalog present and brand absent
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-084-3 @invariant @BR-RULE-084 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-084 INV-3 holds - no catalog, no dependency constraint
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request without catalog
    Then the catalog-brand dependency should not apply
    And the request should proceed normally
    # INV-3 holds: no catalog, no constraint

  @T-UC-001-inv-085 @invariant @BR-RULE-085 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-085 INV-1 holds - refinement_applied length matches refine array
    Given the Buyer sends a refine request with 3 entries
    When the system returns refinement_applied
    Then refinement_applied should have exactly 3 entries
    # INV-1 holds: length(refinement_applied) = length(request.refine)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-085-2 @invariant @BR-RULE-085 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-085 INV-2 holds - positional correspondence maintained
    Given the Buyer sends a refine request with [request-scope, product-scope, proposal-scope] entries
    When the system returns refinement_applied
    Then refinement_applied[0] should correspond to the request-scope entry
    And refinement_applied[1] should correspond to the product-scope entry
    And refinement_applied[2] should correspond to the proposal-scope entry
    # INV-2 holds: positional correspondence
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-085-3 @invariant @BR-RULE-085 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-085 INV-3 holds - each entry has required status field
    Given the Buyer sends a refine request
    When the system returns refinement_applied
    Then each entry in refinement_applied should have a "status" field with value "applied", "partial", or "unable"
    # INV-3 holds: entry MUST include status
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-085-6 @invariant @BR-RULE-085 @analysis-2026-05-25 @schema-v3.1
  Scenario: BR-RULE-085 INV-6 holds - scoped entry echoes its id field
    Given the Buyer sends a refine request with a product-scoped entry and a proposal-scoped entry
    When the system returns refinement_applied
    Then the product-scoped refinement_applied entry should echo the corresponding product_id
    And the proposal-scoped refinement_applied entry should echo the corresponding proposal_id
    # INV-6 holds: scoped entry MUST echo product_id/proposal_id (v3.1 strengthened to MUST)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-085-7 @invariant @BR-RULE-085 @analysis-2026-05-25 @schema-v3.1
  Scenario: BR-RULE-085 INV-7 holds - each entry includes scope discriminator
    Given the Buyer sends a refine request
    When the system returns refinement_applied
    Then each entry in refinement_applied should include a "scope" discriminator echoing the corresponding refine entry's scope
    # INV-7 holds: entry MUST include scope discriminator (request, product, or proposal)

  @T-UC-001-inv-086 @invariant @BR-RULE-086 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-086 INV-1 holds - valid request-scoped entry
    Given a refine entry with scope "request" and ask "more video options"
    When the system validates the refine entry
    Then the entry should be accepted as valid request-scoped refinement
    # INV-1 holds: scope=request, ask present, no id/action
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-086-8v @invariant @BR-RULE-086 @error @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-086 INV-8 violated - proposal scope with more_like_this
    Given a refine entry with scope "proposal", proposal_id "prop-456", action "more_like_this"
    When the system validates the refine entry
    Then the entry should be rejected: more_like_this not valid for proposal scope
    And the error should include "suggestion" field
    # INV-8 violated: proposal scope with more_like_this
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-086-10 @invariant @BR-RULE-086 @analysis-2026-03-09 @schema-v3.1
  Scenario: BR-RULE-086 INV-10 holds - omit action with ask provided (ask ignored)
    Given a refine entry with scope "product", product_id "prod-123", action "omit", ask "not relevant"
    When the system validates the refine entry
    Then the entry should be accepted as valid (ask is ignored for omit action)
    # INV-10 holds: ask ignored when action is omit
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-086-11 @invariant @BR-RULE-086 @analysis-2026-05-25 @schema-v3.1
  Scenario: BR-RULE-086 INV-11 holds - product/proposal action omitted defaults to include
    Given a refine entry with scope "product" and product_id "prod-123" and no action
    When the system validates the refine entry
    Then the entry should be accepted as valid with action defaulting to "include"
    # INV-11 holds: action optional for product/proposal scope, defaults to include (v3.1)

  @T-UC-001-partition-buying-mode @partition @buying_mode @analysis-2026-03-09 @schema-v3.1
  Scenario Outline: buying_mode partition validation - <partition>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with buying_mode configuration <partition>
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # brand_manifest_policy partitions
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

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

  @T-UC-001-partition-brand-policy @partition @brand_manifest_policy @analysis-2026-03-09 @schema-v3.1
  Scenario Outline: brand_manifest_policy partition validation - <partition>
    Given the tenant brand_manifest_policy is configured
    When the Buyer Agent sends a get_products request under <partition> conditions
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # brief_policy partitions
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Valid partitions
      | partition                      | outcome                                  |
      | public_policy                  | request proceeds (no restrictions)       |
      | require_auth_authenticated     | request proceeds (auth satisfied)        |
      | require_brand_with_brand       | request proceeds (brand satisfied)       |

    Examples: Invalid partitions
      | partition                      | outcome                                  |
      | require_auth_anonymous         | error: authentication required            |
      | require_brand_no_brand         | error: brand required by policy           |

  @T-UC-001-partition-brief-policy @partition @brief_policy @analysis-2026-05-25 @schema-v3.1
  Scenario Outline: brief_policy partition validation - <partition>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request under <partition> conditions
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # allowed_principal_ids partitions
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Valid partitions
      | partition                      | outcome                                  |
      | policy_disabled                | request proceeds (no check performed)    |
      | policy_allowed                 | request proceeds (LLM returned ALLOWED)  |
      | policy_service_unavailable     | request proceeds (fail-open)             |
      | policy_requires_human_review   | plan escalated for human review (regardless of enforcement level) |

    Examples: Invalid partitions
      | partition                   | outcome                                  |
      | policy_blocked              | error: POLICY_VIOLATION                  |
      | policy_restricted_review    | error: POLICY_VIOLATION (restricted)     |

  @T-UC-001-partition-principal @partition @allowed_principal_ids @analysis-2026-03-09 @schema-v3.1 @implementation-only
  Scenario Outline: allowed_principal_ids partition validation - <partition>
    Given a product with allowed_principal_ids configuration
    When the Buyer Agent sends a get_products request under <partition> conditions
    Then the product visibility should be <outcome>
    # ----------------------------------------------------------
    # anonymous_pricing partitions
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Valid partitions
      | partition             | outcome              |
      | unrestricted_null     | visible to all       |
      | unrestricted_empty    | visible to all       |
      | principal_in_list     | visible to caller    |

    Examples: Invalid partitions
      | partition               | outcome                |
      | principal_not_in_list   | product suppressed     |
      | anonymous_restricted    | product suppressed     |

  @T-UC-001-partition-anon-pricing @partition @anonymous_pricing @analysis-2026-03-09 @schema-v3.1
  Scenario Outline: anonymous_pricing partition validation - <partition>
    Given pricing suppression logic is applied
    When the Buyer Agent sends a get_products request under <partition> conditions
    Then the pricing result should be <outcome>
    # ----------------------------------------------------------
    # relevance_score partitions
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Valid partitions
      | partition                    | outcome                          |
      | authenticated_full_pricing   | full pricing options returned    |
      | anonymous_suppressed         | pricing_options set to []        |

  @T-UC-001-partition-relevance @partition @relevance_score @analysis-2026-03-09 @schema-v3.1
  Scenario Outline: relevance_score partition validation - <partition>
    Given AI ranking is applied to products
    When a product has relevance in the <partition> range
    Then the product should be <outcome>
    # ----------------------------------------------------------
    # pricing_option_xor partitions
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Valid partitions
      | partition              | outcome                     |
      | above_threshold        | included in results         |
      | ranking_not_applied    | included (no ranking)       |

    Examples: Invalid partitions
      | partition              | outcome                     |
      | below_threshold        | excluded from results       |

  @T-UC-001-partition-pricing-xor @partition @pricing_option_xor @analysis-2026-03-09 @schema-v3.1
  Scenario Outline: pricing_option_xor partition validation - <partition>
    Given a product has pricing_option configuration
    When the system validates the pricing option XOR constraint
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # product_required_fields partitions
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Valid partitions
      | partition           | outcome                |
      | fixed_pricing       | valid (fixed price)    |
      | auction_pricing     | valid (auction price)  |
      | cpa_model           | valid (CPA always fixed) |
      | auction_no_floor    | valid (v3.1 auction, no explicit floor) |
      | max_bid_ceiling     | valid (max_bid ceiling semantics) |

    Examples: Invalid partitions
      | partition           | outcome                |
      | both_set            | invalid (ambiguous)    |
      | neither_set         | invalid (undefined)    |

  @T-UC-001-partition-product-fields @partition @product_required_fields @analysis-2026-05-25 @schema-v3.1
  Scenario Outline: product_required_fields partition validation - <partition>
    Given a product in the database with specific field configuration
    When the system converts the product to AdCP schema
    Then the conversion result should be <outcome>
    # NOTE: v3.1 — delivery_measurement is no longer schema-required and `provider` is deprecated
    #       (replaced by structured `vendors`); the former missing_delivery_measurement_provider
    #       invalid partition is now valid and is covered by delivery_measurement_from_adapter.
    # ----------------------------------------------------------
    # catalog_brand_dependency partitions
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Valid partitions
      | partition                          | outcome                              |
      | all_required_present               | conversion succeeds                  |
      | delivery_measurement_from_adapter  | conversion succeeds (adapter default)|
      | reporting_capabilities_from_default | conversion succeeds (minimal default)|
      | format_ids_from_profile            | conversion succeeds (profile resolved)|

    Examples: Invalid partitions
      | partition                              | outcome                            |
      | empty_format_ids                       | conversion fails (ValueError)      |
      | empty_publisher_properties             | conversion fails (ValueError)      |
      | empty_pricing_options                  | conversion fails (ValueError)      |

  @T-UC-001-partition-catalog-brand @partition @catalog @analysis-2026-03-09 @schema-v3.1
  Scenario Outline: catalog brand dependency partition validation - <partition>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with <partition> field combination
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # refinement_applied partitions
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Valid partitions
      | partition               | outcome                                 |
      | catalog_with_brand      | request proceeds to catalog discovery   |
      | no_catalog_no_brand     | request proceeds (no catalog dependency)|
      | no_catalog_with_brand   | request proceeds (brand-scoped)         |

    Examples: Invalid partitions
      | partition               | outcome                                    |
      | catalog_without_brand   | error: brand required when catalog provided |

  @T-UC-001-partition-refinement @partition @refinement_applied @analysis-2026-05-25 @schema-v3.1
  Scenario Outline: refinement_applied partition validation - <partition>
    Given a refine mode response is being assembled
    When the refinement_applied array is in the <partition> state
    Then the response validity should be <outcome>
    # ----------------------------------------------------------
    # refine_entry partitions
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Valid partitions
      | partition                       | outcome                                  |
      | exact_match_all_applied         | valid (all entries applied)               |
      | exact_match_mixed_status        | valid (mixed statuses)                   |
      | exact_match_all_unable          | valid (all unable with notes)            |
      | single_entry                    | valid (minimum 1:1 match)                |
      | status_only_minimal             | valid (status + required scope)          |
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
      | missing_scope                   | invalid: scope discriminator required    |
      | missing_scoped_id               | invalid: product_id/proposal_id required for scoped entry |

  @T-UC-001-partition-refine-entry @partition @refine_entry @analysis-2026-05-25 @schema-v3.1
  Scenario Outline: refine_entry partition validation - <partition>
    Given a refine array entry is being validated
    When the entry matches the <partition> configuration
    Then the validation result should be <outcome>
    # v3.1: product_scope_missing_action is deprecated (action now optional, defaults to include) -
    #       covered by valid product_scope_action_defaulted above.
    # ----------------------------------------------------------
    # delivery_type partitions
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Valid partitions
      | partition                          | outcome                               |
      | request_scope_typical              | valid (request-scoped with ask)       |
      | product_scope_include              | valid (product include)               |
      | product_scope_include_with_ask     | valid (product include with ask)      |
      | product_scope_omit                 | valid (product omit)                  |
      | product_scope_more_like_this       | valid (product more_like_this)        |
      | product_scope_mlt_with_ask         | valid (product MLT with ask)          |
      | product_scope_action_defaulted     | valid (action omitted, defaults to include) |
      | proposal_scope_include             | valid (proposal include)              |
      | proposal_scope_include_with_ask    | valid (proposal include with ask)     |
      | proposal_scope_omit               | valid (proposal omit)                 |
      | proposal_scope_action_defaulted    | valid (action omitted, defaults to include) |
      | proposal_scope_finalize            | valid (proposal finalize - firm pricing + hold) |
      | omit_with_ask_ignored              | valid (ask ignored for omit)          |
      | mixed_scope_array                  | valid (array with mixed scopes)       |

    Examples: Invalid partitions
      | partition                          | outcome                                     |
      | request_scope_with_product_id      | invalid: product_id forbidden for request scope |
      | request_scope_with_action          | invalid: action forbidden for request scope  |
      | request_scope_missing_ask          | invalid: ask required for request scope      |
      | request_scope_empty_ask            | invalid: ask minLength 1 violated            |
      | product_scope_missing_product_id   | invalid: product_id required for product scope |
      | product_scope_empty_product_id     | invalid: product_id minLength 1 violated     |
      | product_scope_invalid_action       | invalid: action not in product enum          |
      | proposal_scope_more_like_this      | invalid: more_like_this not for proposal     |
      | proposal_scope_missing_proposal_id | invalid: proposal_id required for proposal scope |
      | missing_scope                      | invalid: scope required                      |
      | invalid_scope                      | invalid: scope not in enum                   |
      | empty_array                        | invalid: refine array minItems 1 violated    |
      | id_not_found                       | invalid: referenced ID not found             |

  @T-UC-001-partition-delivery-type @partition @delivery_type @analysis-2026-03-09 @schema-v3.1
  Scenario Outline: delivery_type partition validation - <partition>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with delivery_type filter <partition>
    Then the filter result should be <outcome>
    # ----------------------------------------------------------
    # channels partitions
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Valid partitions
      | partition          | outcome                              |
      | guaranteed         | only guaranteed products returned    |
      | non_guaranteed     | only non-guaranteed products returned|
      | not_provided       | all delivery types returned          |

    Examples: Invalid partitions
      | partition          | outcome                              |
      | unknown_value      | error: unknown delivery_type value   |

  @T-UC-001-partition-channels @partition @channels @analysis-2026-03-09 @schema-v3.1
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

  @T-UC-001-boundary-buying-mode @boundary @buying_mode @analysis-2026-03-09 @schema-v3.1
  Scenario Outline: buying_mode boundary validation - <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request at boundary <boundary_point>
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # brand_manifest_policy boundaries
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

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

  @T-UC-001-boundary-brand-policy @boundary @brand_manifest_policy @analysis-2026-03-09 @schema-v3.1
  Scenario Outline: brand_manifest_policy boundary validation - <boundary_point>
    Given the tenant brand_manifest_policy is configured
    When the Buyer Agent sends a get_products request at boundary <boundary_point>
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # brief_policy boundaries
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Boundary values
      | boundary_point                   | outcome   |
      | public policy (no restrictions)  | valid     |
      | require_auth + authenticated     | valid     |
      | require_auth + anonymous         | invalid   |
      | require_brand + brand present    | valid     |
      | require_brand + no brand         | invalid   |

  @T-UC-001-boundary-brief-policy @boundary @brief_policy @analysis-2026-05-25 @schema-v3.1
  Scenario Outline: brief_policy boundary validation - <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request at boundary <boundary_point>
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # allowed_principal_ids boundaries
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Boundary values
      | boundary_point                               | outcome   |
      | policy disabled                              | valid     |
      | evaluation passes                            | valid     |
      | enforcement 'must' violated                  | invalid   |
      | LLM service unavailable (fail-open)          | valid     |
      | policy requires_human_review: true           | valid     |

  @T-UC-001-boundary-principal @boundary @allowed_principal_ids @analysis-2026-03-09 @schema-v3.1 @implementation-only
  Scenario Outline: allowed_principal_ids boundary validation - <boundary_point>
    Given a product with specific allowed_principal_ids configuration
    When the Buyer Agent sends a get_products request at boundary <boundary_point>
    Then the visibility result should be <outcome>
    # ----------------------------------------------------------
    # anonymous_pricing boundaries
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Boundary values
      | boundary_point                     | outcome   |
      | allowed_principal_ids null         | valid     |
      | allowed_principal_ids empty array  | valid     |
      | principal in allow-list            | valid     |
      | principal not in allow-list        | invalid   |
      | anonymous + restricted product     | invalid   |

  @T-UC-001-boundary-anon-pricing @boundary @anonymous_pricing @analysis-2026-03-09 @schema-v3.1
  Scenario Outline: anonymous_pricing boundary validation - <boundary_point>
    Given pricing suppression logic is applied
    When the Buyer Agent sends a get_products request at boundary <boundary_point>
    Then the pricing result should be <outcome>
    # ----------------------------------------------------------
    # relevance_score boundaries
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Boundary values
      | boundary_point                     | outcome   |
      | authenticated (full pricing)       | valid     |
      | anonymous (pricing suppressed)     | valid     |

  @T-UC-001-boundary-relevance @boundary @relevance_score @analysis-2026-03-09 @schema-v3.1
  Scenario Outline: relevance_score boundary validation - <boundary_point>
    Given AI ranking is applied to products
    When a product has relevance score at boundary <boundary_point>
    Then the product should be <outcome>
    # ----------------------------------------------------------
    # pricing_option_xor boundaries
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Boundary values
      | boundary_point                               | outcome   |
      | score = 0.1 (threshold, included)            | valid     |
      | score = 0.09 (just below threshold)          | invalid   |
      | score = 0.0 (minimum)                        | invalid   |
      | ranking not applied (no brief)               | valid     |

  @T-UC-001-boundary-pricing-xor @boundary @pricing_option_xor @analysis-2026-03-09 @schema-v3.1
  Scenario Outline: pricing_option_xor boundary validation - <boundary_point>
    Given a product has pricing_option configuration
    When the system validates the pricing option at boundary <boundary_point>
    Then the result should be <outcome>
    # v3.1 (BR-RULE-006): "neither present" split into two boundaries — schema-valid
    #   (auction without explicit floor) vs salesagent code enforcement (still rejects, gap).
    #   max_bid=true on bid-based models (cpm/cpc/cpcv/cpv/vcpm): bid_price = buyer ceiling.
    # ----------------------------------------------------------
    # product_required_fields boundaries
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Boundary values
      | boundary_point                                   | outcome   |
      | fixed_price only (valid fixed)                   | valid     |
      | floor_price only (valid auction)                 | valid     |
      | both present (mutually exclusive)                | invalid   |
      | neither present, v3.1 schema (auction, no floor) | valid     |
      | neither present, salesagent code enforcement     | invalid   |
      | max_bid=true on bid-based auction model          | valid     |

  @T-UC-001-boundary-product-fields @boundary @product_required_fields @analysis-2026-05-25 @schema-v3.1
  Scenario Outline: product_required_fields boundary validation - <boundary_point>
    Given a product in the database with specific field configuration
    When the system converts the product at boundary <boundary_point>
    Then the conversion result should be <outcome>
    # v3.1: boundary "delivery_measurement without provider field" is deprecated
    #       (provider optional/deprecated → now schema-valid); omitted from active rows.
    # ----------------------------------------------------------
    # catalog_brand_dependency boundaries
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Boundary values
      | boundary_point                                                    | outcome   |
      | all arrays with 1 item + delivery_measurement + reporting_capabilities present | valid |
      | format_ids empty (0 items)                                        | invalid   |
      | publisher_properties empty (0 items)                              | invalid   |
      | pricing_options empty (0 items)                                   | invalid   |
      | delivery_measurement absent from DB, adapter provides default     | valid     |
      | reporting_capabilities absent from DB, code provides minimal default | valid  |

  @T-UC-001-boundary-catalog-brand @boundary @catalog @analysis-2026-03-09 @schema-v3.1
  Scenario Outline: catalog brand dependency boundary validation - <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request at boundary <boundary_point>
    Then the result should be <outcome>
    # ----------------------------------------------------------
    # refinement_applied boundaries
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Boundary values
      | boundary_point                                                       | outcome   |
      | catalog present + brand present (both provided)                      | valid     |
      | catalog absent + brand absent (neither provided)                     | valid     |
      | catalog absent + brand present (brand alone)                         | valid     |
      | catalog present + brand absent (dependency violation)                | invalid   |
      | catalog present + brand with domain only (minimal brand-ref)         | valid     |
      | catalog present + brand with domain and brand_id (full brand-ref)    | valid     |

  @T-UC-001-boundary-refinement @boundary @refinement_applied @analysis-2026-05-25 @schema-v3.1
  Scenario Outline: refinement_applied boundary validation - <boundary_point>
    Given a refine mode response is being validated
    When the refinement_applied array is at boundary <boundary_point>
    Then the validity should be <outcome>
    # ----------------------------------------------------------
    # refine_entry boundaries
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

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
      | scope + product_id echoed from product refine entry (cross-validation present) | valid  |
      | scope omitted from entry (v3.1: required discriminator — invalid)           | invalid   |
      | product-scoped entry omits product_id (v3.1: required when scope='product') | invalid   |
      | scope='campaign' (unknown enum value)                                      | invalid   |
      | refinement_applied present in refine mode response (SHOULD)                | valid     |
      | refinement_applied absent in refine mode response (SHOULD, not MUST — allowed) | valid |
      | refinement_applied absent in brief mode response (correct — not applicable)    | valid |
      | refinement_applied present in brief mode response (unexpected)             | invalid   |
      | status='partial' with notes (recommended practice)                         | valid     |
      | status='partial' without notes (allowed but not recommended)               | valid     |
      | status='unable' with notes (recommended practice)                          | valid     |
      | status='applied' without notes (typical — no explanation needed)           | valid     |

  @T-UC-001-boundary-refine-entry @boundary @refine_entry @analysis-2026-05-25 @schema-v3.1
  Scenario Outline: refine_entry boundary validation - <boundary_point>
    Given a refine array entry is being validated
    When the entry is at boundary <boundary_point>
    Then the validation result should be <outcome>
    # ----------------------------------------------------------
    # delivery_type boundaries
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Boundary values
      | boundary_point                                                    | outcome   |
      | ask at minLength (1 char)                                         | valid     |
      | ask empty string (0 chars)                                        | invalid   |
      | product_id at minLength (1 char)                                  | valid     |
      | product_id empty string (0 chars)                                 | invalid   |
      | refine array with 1 entry (at minItems)                           | valid     |
      | refine array with 0 entries (below minItems)                      | invalid   |
      | request scope with only scope+ask (no extra fields)               | valid     |
      | request scope with scope+ask+product_id (extra field)             | invalid   |
      | request scope with scope+ask+action (extra field)                 | invalid   |
      | product action=include                                            | valid     |
      | product action=omit                                               | valid     |
      | product action=more_like_this                                     | valid     |
      | product action=replace (unknown)                                  | invalid   |
      | proposal action=include                                           | valid     |
      | proposal action=omit                                              | valid     |
      | proposal action=finalize                                          | valid     |
      | proposal action=more_like_this (not in proposal enum)             | invalid   |
      | product action absent (defaults to include)                       | valid     |
      | proposal action absent (defaults to include)                      | valid     |
      | scope=request                                                     | valid     |
      | scope=product                                                     | valid     |
      | scope=proposal                                                    | valid     |
      | scope=campaign (unknown)                                          | invalid   |
      | scope absent                                                      | invalid   |
      | omit action with ask present (ask ignored)                        | valid     |
      | ask absent on product include (optional field omitted)            | valid     |

  @T-UC-001-boundary-delivery-type @boundary @delivery_type @analysis-2026-03-09 @schema-v3.1
  Scenario Outline: delivery_type boundary validation - <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_products request with delivery_type at boundary <boundary_point>
    Then the filter result should be <outcome>
    # ----------------------------------------------------------
    # channels boundaries
    # ----------------------------------------------------------
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Boundary values
      | boundary_point                           | outcome   |
      | guaranteed (first enum value)            | valid     |
      | non_guaranteed (last enum value)         | valid     |
      | Not provided (no delivery type filter)   | valid     |
      | Unknown string not in enum               | invalid   |

  @T-UC-001-boundary-channels @boundary @channels @analysis-2026-03-09 @schema-v3.1
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

  @T-UC-001-nfr-001 @nfr @nfr-001 @analysis-2026-03-09 @schema-v3.1
  Scenario: NFR-001 - Security hardening on product discovery
    Given the Seller Agent enforces security hardening
    When the Buyer Agent sends a get_products request
    Then the request should be validated against schema before processing
    And authentication should be checked before any data access
    And no internal system details should leak in error responses
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-nfr-002 @nfr @nfr-002 @analysis-2026-03-09 @schema-v3.1
  Scenario: NFR-002 - Prompt injection defense for brief and refine.ask
    Given the Seller Agent enforces prompt injection defense
    When the Buyer Agent sends a get_products request with a brief containing injection attempts
    Then the brief should be sanitized before passing to the LLM
    And the system should not execute injected instructions
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-nfr-003 @nfr @nfr-003 @analysis-2026-03-09 @schema-v3.1
  Scenario: NFR-003 - Audit logging for product discovery
    Given the Seller Agent has audit logging enabled
    When the Buyer Agent sends a get_products request
    Then the request should be logged with timestamp, principal_id, and request parameters
    And the response should be logged with status and product count
    And policy check results should be logged (operation: policy_check)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-nfr-004 @nfr @nfr-004 @analysis-2026-03-09 @schema-v3.1
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
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

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
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

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
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-boundary-sandbox-response @boundary @sandbox @schema-v3.1
  Scenario Outline: sandbox response flag boundary validation - <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    When the get_products response is assembled at boundary <boundary_point>
    Then the sandbox-flag result should be <outcome>
    # v3.1: sandbox response-flag boundaries for get_products. Cross-operation sandbox boundaries
    #       (list_accounts filter, sync_accounts request item, capability declaration, media-buy
    #       budget) belong to UC-010 (get_adcp_capabilities) and UC-011 (list/sync_accounts) features.

    Examples: Boundary values
      | boundary_point                                     | outcome |
      | sandbox: true in response (sandbox account)        | valid   |
      | sandbox absent in response (production account)    | valid   |
      | sandbox: false in response (explicit production)   | valid   |

  @T-UC-001-v31-collection-product @v3.1 @collection @schema-v3.1
  Scenario: Product references a publisher-declared collection with kind and cadence
    Given the Buyer is authenticated with a valid principal_id
    And the publisher's adagents.json declares a collection with collection_id "ny-times-daily" and kind "publication" and cadence "daily"
    And a product references this collection via a collection selector
    When the Buyer Agent sends a get_products request with:
    | field       | value                            |
    | buying_mode | brief                            |
    | brief       | Display ads for tech audience Q4 |
    | brand       | {"domain": "acme.com"}           |
    Then the response status should be "completed"
    And the response should contain "products" array
    And the matched product should include a collection reference resolvable to collection_id "ny-times-daily"
    And the resolved collection should have kind "publication"
    And the resolved collection should have cadence "daily"
    # POST-S1, POST-S2: Buyer can evaluate inventory in the context of its recurring container
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-v31-collection-status-ended @v3.1 @collection @collection-status
  Scenario: Collection with status "ended" is surfaced with status indicator
    Given the Buyer is authenticated with a valid principal_id
    And the publisher declares a collection with status "ended"
    And a product references this ended collection
    When the Buyer Agent sends a get_products request with:
    | field       | value                            |
    | buying_mode | wholesale                        |
    | brand       | {"domain": "acme.com"}           |
    Then the response status should be "completed"
    And any product referencing the ended collection should expose collection.status equal to "ended"
    # G36: ended collection visibility behavior is observable to the buyer
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-v31-limited-series @v3.1 @collection @limited-series
  Scenario: Limited-series collection declares total_installments and bounded run
    Given the Buyer is authenticated with a valid principal_id
    And the publisher declares a collection with a limited_series block specifying total_installments 8 starts "2026-09-01T00:00:00Z" and ends "2026-10-27T00:00:00Z"
    And a product references this limited-series collection
    When the Buyer Agent sends a get_products request with:
    | field       | value                            |
    | buying_mode | brief                            |
    | brief       | Tentpole sponsorship for Q4 limited series |
    | brand       | {"domain": "acme.com"}           |
    Then the response status should be "completed"
    And the matched product's collection should include limited_series with total_installments 8
    And the limited_series.starts should be "2026-09-01T00:00:00Z"
    And the limited_series.ends should be "2026-10-27T00:00:00Z"
    # POST-S1, POST-S2: Buyer can evaluate bounded inventory windows
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-v31-collection-distribution-cross-platform @v3.1 @collection @collection-distribution
  Scenario: Collection distribution carries platform-specific identifiers across publishers
    Given the Buyer is authenticated with a valid principal_id
    And the publisher declares a collection with a distribution entry for publisher_domain "youtube.com" with identifier type "youtube_channel_id" and value "UCabc123"
    And the same collection declares a distribution entry for publisher_domain "spotify.com" with identifier type "spotify_collection_id" and value "spfy_xyz"
    When the Buyer Agent sends a get_products request with:
    | field       | value                            |
    | buying_mode | wholesale                        |
    | brand       | {"domain": "acme.com"}           |
    Then the response status should be "completed"
    And the matched product's collection.distribution should include both publisher_domain entries
    And each distribution entry should carry at least one identifier with a typed value
    # POST-S1, POST-S8: Buyer can correlate the same logical collection across sellers
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-v31-deadline-policy-applied @v3.1 @collection @deadline-policy
  Scenario: Collection deadline policy applies default lead times to installments
    Given the Buyer is authenticated with a valid principal_id
    And a collection declares a deadline_policy with booking_lead_days 7 and cancellation_lead_days 3 and material_stages "draft" lead_days 14 and "final" lead_days 7
    And a product references this collection and exposes upcoming installments without explicit deadlines
    When the Buyer Agent sends a get_products request with:
    | field       | value                            |
    | buying_mode | brief                            |
    | brief       | Pre-roll for upcoming episodes   |
    | brand       | {"domain": "acme.com"}           |
    Then the response status should be "completed"
    And each installment should derive its booking_deadline from scheduled_at minus 7 days
    And each installment should derive its draft_material_deadline from scheduled_at minus 14 days
    And booking_lead_days should be greater than or equal to cancellation_lead_days
    # POST-S1, POST-S2: Buyer can evaluate when material is due relative to airing
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-v31-deadline-policy-business-days @v3.1 @collection @deadline-policy
  Scenario: Deadline policy with business_days_only counts business days only
    Given the Buyer is authenticated with a valid principal_id
    And a collection declares a deadline_policy with booking_lead_days 5 and business_days_only true
    And the installment scheduled_at falls on a Monday
    When the Buyer Agent sends a get_products request with:
    | field       | value                            |
    | buying_mode | brief                            |
    | brief       | Newsletter sponsorship           |
    | brand       | {"domain": "acme.com"}           |
    Then the booking_deadline should be calculated using business days (Monday-Friday) only
    And weekends should be excluded from the lead-time computation
    # G41: business-day calendar semantics are exercised
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-v31-installment-override-deadline @v3.1 @collection @deadline-policy
  Scenario: Installment with explicit deadlines overrides the collection deadline policy
    Given the Buyer is authenticated with a valid principal_id
    And a collection declares a deadline_policy with booking_lead_days 7
    And one installment of that collection declares an explicit booking_deadline
    When the Buyer Agent sends a get_products request with:
    | field       | value                            |
    | buying_mode | wholesale                        |
    | brand       | {"domain": "acme.com"}           |
    Then the override installment should use its explicit booking_deadline value
    And other installments without explicit deadlines should fall back to the collection deadline_policy
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-v31-placement-definition-reuse @v3.1 @placement-definition
  Scenario: Product reuses a placement_id registered in adagents.json placement_definitions
    Given the Buyer is authenticated with a valid principal_id
    And the publisher's adagents.json declares a placement_definition with placement_id "homepage-banner" scoped to property_tags including "premium"
    And a product references placement_id "homepage-banner"
    When the Buyer Agent sends a get_products request with:
    | field       | value                            |
    | buying_mode | brief                            |
    | brief       | Display ads for tech audience Q4 |
    | brand       | {"domain": "acme.com"}           |
    Then the response status should be "completed"
    And the matched product's placement_id "homepage-banner" should resolve to the registered placement_definition
    And the resolved placement_definition should expose its registered property_tags and format_ids
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-v31-placement-definition-anyof @v3.1 @placement-definition @schema-anyof
  Scenario Outline: placement-definition anyOf(property_ids, property_tags) - <case>
    Given the publisher's adagents.json declares a placement_definition with placement_id "p1"
    And the placement_definition has <scoping_present>
    When the seller validates the adagents.json placement_definition
    Then the placement_definition should be <validity>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples:
      | case               | scoping_present                       | validity |
      | property_ids only  | property_ids ["prop-1"]               | valid    |
      | property_tags only | property_tags ["premium"]             | valid    |
      | both present       | property_ids ["prop-1"] and property_tags ["premium"] | valid    |
      | neither present    | no property_ids and no property_tags  | invalid  |

  @T-UC-001-v31-seller-agent-ref @v3.1 @seller-agent-ref @federation
  Scenario: Federated product exposes a seller_agent_ref with agent_url
    Given the Buyer is authenticated with a valid principal_id
    And a product is served by a federated seller agent with agent_url "https://agent.acme.example"
    And the property publisher's adagents.json lists "https://agent.acme.example" in authorized_agents
    When the Buyer Agent sends a get_products request with:
    | field       | value                            |
    | buying_mode | wholesale                        |
    | brand       | {"domain": "acme.com"}           |
    Then the response status should be "completed"
    And the federated product should expose a seller_agent_ref with agent_url "https://agent.acme.example"
    And the agent_url should use the https scheme
    And the seller_agent_ref should not populate the reserved id slot
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-v31-seller-agent-ref-not-authorized @v3.1 @seller-agent-ref @federation
  Scenario: Seller agent reference not in authorized_agents is rejected as seller_not_authorized
    Given the Buyer is authenticated with a valid principal_id
    And a product declares seller_agent_ref.agent_url "https://rogue.example"
    And the property publisher's adagents.json does NOT list "https://rogue.example" in authorized_agents
    When the seller validates the product
    Then the seller should reject the seller_agent_ref with error code "seller_not_authorized"
    And the URL comparison should use AdCP canonicalization rules rather than byte equality
    # G40: URL canonicalization and seller_not_authorized error shape are exercised
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-v31-genre-taxonomy-iab @v3.1 @collection @genre-taxonomy
  Scenario: Collection declares iab_content_3.0 genre_taxonomy with taxonomy IDs
    Given the Buyer is authenticated with a valid principal_id
    And a collection declares genre_taxonomy "iab_content_3.0" and genre values "IAB1-1" and "IAB1-2"
    When the Buyer Agent sends a get_products request with:
    | field       | value                            |
    | buying_mode | brief                            |
    | brief       | News content sponsorship          |
    | brand       | {"domain": "acme.com"}           |
    Then the response status should be "completed"
    And the resolved collection's genre_taxonomy should be "iab_content_3.0"
    And each genre value should be a valid identifier within the declared taxonomy
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-v31-genre-taxonomy-custom @v3.1 @collection @genre-taxonomy
  Scenario: Collection with custom genre_taxonomy uses free-form values
    Given the Buyer is authenticated with a valid principal_id
    And a collection declares genre_taxonomy "custom" and genre values "publisher-pop-culture" and "publisher-deep-dive"
    When the Buyer Agent sends a get_products request with:
    | field       | value                            |
    | buying_mode | wholesale                        |
    | brand       | {"domain": "acme.com"}           |
    Then the response status should be "completed"
    And the resolved collection's genre_taxonomy should be "custom"
    And genre values should be passed through verbatim without taxonomy validation
    # G38: taxonomy validation is conditional on the declared taxonomy
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-v31-production-quality-tiers @v3.1 @collection @production-quality
  Scenario Outline: Collection production_quality maps to OpenRTB content.prodq - <quality>
    Given a collection declares production_quality "<quality>"
    When a buyer requests products referencing that collection
    Then the resolved collection.production_quality should equal "<quality>"
    And the OpenRTB content.prodq value should equal <openrtb_prodq>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples:
      | quality      | openrtb_prodq |
      | professional | 1             |
      | prosumer     | 2             |
      | ugc          | 3             |

  @T-UC-001-v31-collection-relationship @v3.1 @collection @collection-relationship
  Scenario: Related collections expose typed relationships
    Given a collection "after-show" declares a related_collections entry with collection_id "main-show" and relationship "spinoff"
    When the Buyer Agent sends a get_products request with:
    | field       | value                            |
    | buying_mode | wholesale                        |
    | brand       | {"domain": "acme.com"}           |
    Then the resolved collection.related_collections should include collection_id "main-show" with relationship "spinoff"
    And the relationship value should be one of "spinoff", "companion", "sequel", "prequel", or "crossover"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-v31-collection-cadence-partition @v3.1 @collection @partition
  Scenario Outline: collection-cadence partition validation - <cadence>
    Given a collection declares cadence "<cadence>"
    When the seller validates the collection schema
    Then the cadence value should be <validity>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples:
      | cadence    | validity |
      | daily      | valid    |
      | weekly     | valid    |
      | monthly    | valid    |
      | seasonal   | valid    |
      | event      | valid    |
      | irregular  | valid    |
      | hourly     | invalid  |
      | quarterly  | invalid  |

  @T-UC-001-v31-distribution-identifier-types @v3.1 @collection @collection-distribution @partition
  Scenario Outline: distribution-identifier-type partition validation - <id_type>
    Given a collection_distribution entry uses identifier type "<id_type>"
    When the seller validates the collection-distribution schema
    Then the identifier type should be <validity>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples:
      | id_type               | validity |
      | apple_podcast_id      | valid    |
      | spotify_collection_id | valid    |
      | youtube_channel_id    | valid    |
      | imdb_id               | valid    |
      | gracenote_id          | valid    |
      | eidr_id               | valid    |
      | domain                | valid    |
      | facebook_page_id      | invalid  |
      | x_handle              | invalid  |

  @T-UC-001-v31-publisher-identifier-types @v3.1 @publisher-identifier @partition
  Scenario Outline: publisher-identifier-types partition validation - <id_type>
    Given a publisher identity declares identifier type "<id_type>"
    When the seller validates the publisher identifier
    Then the identifier type should be <validity>

    Examples:
      | id_type   | validity |
      | tag_id    | valid    |
      | duns      | valid    |
      | lei       | valid    |
      | seller_id | valid    |
      | gln       | valid    |
      | iso6166   | invalid  |
      | ein       | invalid  |

  @T-UC-001-storyboard-proposal-finalize-action @storyboard-v3.1 @v3.1 @proposal @refine @finalize-action
  Scenario: Proposal finalize action transitions proposal_status from draft to committed
    Given a previous get_products response returned a proposal with proposal_status "draft"
    And the buyer holds the proposal_id captured from that response
    When the Buyer Agent sends get_products with buying_mode "refine" and a refine entry with scope "proposal", proposal_id, and action "finalize"
    Then the response should contain "proposals" array
    And the finalized proposal's proposal_status should be "committed"
    And the finalized proposal should carry an "expires_at" timestamp for the inventory hold window
    And the finalized proposal should carry firm pricing rather than indicative pricing
    And the finalized proposal may carry an insertion_order with an io_id for downstream create_media_buy acceptance
    # proposal_finalize storyboard: full proposal lifecycle through get_products
    #   brief -> proposals (draft, indicative pricing)
    #   refine (scope=proposal, ask=...) -> updated proposal (still draft)
    #   refine (scope=proposal, action=finalize) -> committed (firm pricing, expires_at)
    # The finalize action is the protocol's commitment trigger -- before this, pricing
    # is indicative; after, the seller holds inventory until expires_at.
    # proposal_finalize: action=finalize is the commitment trigger; pricing transitions from indicative to firm
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/proposal_finalize.yaml

  @T-UC-001-storyboard-finalize-uses-refine-vocabulary @storyboard-v3.1 @v3.1 @proposal @refine @finalize-action
  Scenario: Finalize action is encoded as a refine entry with action "finalize" (vocabulary lock)
    Given a refine entry with scope "proposal", a proposal_id, and action "finalize"
    When the system validates the refine entry
    Then the entry should be accepted as valid (ask is ignored for finalize action)
    # The protocol expresses proposal finalization through the existing refine vocabulary
    # rather than introducing a separate finalize endpoint. The refine entry uses
    # scope=proposal, the captured proposal_id, and action=finalize; ask is ignored
    # for the finalize action (mirrors the existing INV-10 pattern for omit action).
    # proposal_finalize: vocabulary lock -- finalize is expressed via the existing refine grammar

  @T-UC-001-inv-210 @invariant @BR-RULE-210 @v3.1 @federation @schema-v3.1
  Scenario: BR-RULE-210 INV-3 holds - canonical agent_url matches an authorized agent
    Given a get_products response references a product sold by a different seller via seller-agent-ref
    And the reference's agent_url canonicalizes to an entry in the publisher's adagents.json authorized_agents[].url
    When the system resolves the seller agent reference
    Then the seller agent reference should be accepted as authorized
    # INV-3 holds: canonical agent_url matches authorized_agents entry
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

  @T-UC-001-inv-210-reject @invariant @BR-RULE-210 @error @v3.1 @federation @schema-v3.1
  Scenario Outline: BR-RULE-210 seller-agent-ref rejected - <condition>
    Given a get_products response references a product via seller-agent-ref
    When the seller agent reference is in the <condition> state
    Then the federation reference should be rejected with "seller_not_authorized"
    And the error should include a "suggestion" field
    # INV-1 (uncanonicalizable), INV-2 (no match), INV-4 (non-https), INV-5 (missing url), INV-6 (id slot) -> seller_not_authorized
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-products-request.json

    Examples: Rejection conditions
      | condition                                                             |
      | agent_url not canonicalizable (malformed authority / raw non-ToASCII host) |
      | canonical agent_url matches no authorized_agents[].url entry          |
      | agent_url uses http:// scheme against an https:// authorized entry    |
      | required agent_url absent from the reference                          |
      | reserved id slot populated by the sender (agent_url is sole authority)|

  @T-UC-001-boundary-agent-url @boundary @agent_url @v3.1 @federation @schema-v3.1
  Scenario Outline: agent_url canonicalization boundary validation - <boundary_point>
    Given a get_products response references a product via seller-agent-ref
    When the agent_url is at boundary <boundary_point>
    Then the federation authorization result should be <outcome>

    Examples: Boundary values
      | boundary_point                                                       | outcome   |
      | agent_url canonicalizes to a registered authorized_agents[].url      | valid     |
      | uppercase scheme + explicit default port canonicalizes to registered form | valid |
      | correctly ToASCII-normalized IDN host matches registered A-label     | valid     |
      | canonical form present in registry but for a different property      | invalid   |
      | http:// caller against https:// registered entry                    | invalid   |
      | scheme other than https://                                          | invalid   |
      | malformed authority / non-ToASCII host / IPv6 zone id                | invalid   |
      | reserved id slot populated                                          | invalid   |
      | agent_url absent                                                    | invalid   |
