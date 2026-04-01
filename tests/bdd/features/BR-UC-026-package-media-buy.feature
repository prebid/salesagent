# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

@analysis-2026-03-10 @schema-v3.0.0-rc.1
Feature: BR-UC-026 Package Media Buy
  As a Buyer (via Buyer Agent)
  I want to configure packages within a media buy
  So that I can allocate budget, select pricing, assign creatives, and apply targeting to specific products

  # Postconditions verified:
  #   POST-S1: Buyer knows the package_id assigned by the seller to each created package
  #   POST-S2: Buyer knows the complete package state including budget, pricing, targeting, and creative assignments
  #   POST-S3: Buyer knows the format_ids active for the package (echoed from request or defaulted)
  #   POST-S4: Buyer knows the paused state of each package
  #   POST-S5: Buyer knows which format_ids still need creative assets (format_ids_to_provide)
  #   POST-S6: Buyer knows that deduplication was applied when the same buyer_ref was resubmitted
  #   POST-F1: Buyer knows the operation failed
  #   POST-F2: Buyer knows what went wrong (error code and message)
  #   POST-F3: Buyer knows how to recover (suggestion and recovery classification)

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant exists with completed setup checklist
    And the Buyer is authenticated with a valid principal_id
    And the seller has a product "prod-1" in inventory with pricing_options ["cpm-standard", "cpm-auction"]
    And the product "prod-1" supports format_ids ["banner-300x250", "banner-728x90"]


  @T-UC-026-main-mcp @main-flow @mcp @post-s1 @post-s2 @post-s3 @post-s4 @post-s5
  Scenario: Create package via MCP -- all required fields provided
    Given a valid create_media_buy MCP tool request with packages array containing:
    | field              | value         |
    | buyer_ref          | pkg-001       |
    | product_id         | prod-1        |
    | budget             | 5000          |
    | pricing_option_id  | cpm-standard  |
    When the Buyer Agent invokes the create_media_buy MCP tool
    Then the response should contain a package with a seller-assigned package_id
    And the package should contain buyer_ref "pkg-001"
    And the package should contain budget 5000
    And the package should contain pricing_option_id "cpm-standard"
    And the package should contain format_ids defaulting to all product formats
    And the package should contain paused as false
    And the package should contain format_ids_to_provide listing formats needing creative assets
    # POST-S1: Buyer knows seller-assigned package_id
    # POST-S2: Complete package state returned (budget, pricing, targeting)
    # POST-S3: format_ids echoed (defaulted to all product formats)
    # POST-S4: paused state returned (defaults to false)
    # POST-S5: format_ids_to_provide lists formats needing creatives

  @T-UC-026-main-rest @main-flow @rest @post-s1 @post-s2 @post-s3 @post-s4 @post-s5
  Scenario: Create package via REST -- all required fields provided
    Given a valid create_media_buy A2A task request with packages array containing:
    | field              | value         |
    | buyer_ref          | pkg-002       |
    | product_id         | prod-1        |
    | budget             | 10000         |
    | pricing_option_id  | cpm-standard  |
    When the Buyer Agent sends the create_media_buy A2A task
    Then the response should contain a package with a seller-assigned package_id
    And the package should contain buyer_ref "pkg-002"
    And the package should contain budget 10000
    And the package should contain pricing_option_id "cpm-standard"
    And the package should contain format_ids defaulting to all product formats
    And the package should contain paused as false
    And the package should contain format_ids_to_provide listing formats needing creative assets
    # POST-S1: Buyer knows seller-assigned package_id
    # POST-S2: Complete package state returned
    # POST-S3: format_ids echoed (defaulted to all product formats)
    # POST-S4: paused state returned (defaults to false)
    # POST-S5: format_ids_to_provide lists formats needing creatives

  @T-UC-026-main-explicit-formats @main-flow @post-s3 @post-s5
  Scenario: Create package with explicit format_ids
    Given a valid create_media_buy request with a package containing:
    | field              | value                                |
    | buyer_ref          | pkg-fmt-1                            |
    | product_id         | prod-1                               |
    | budget             | 3000                                 |
    | pricing_option_id  | cpm-standard                         |
    | format_ids         | [banner-300x250]                     |
    When the Buyer Agent sends the create_media_buy request
    Then the response should contain a package with format_ids ["banner-300x250"]
    And the package should contain format_ids_to_provide based on assigned creatives
    # POST-S3: format_ids echoed from request (explicit subset)
    # POST-S5: format_ids_to_provide shows outstanding creative needs

  @T-UC-026-main-full-config @main-flow @post-s2
  Scenario: Create package with all optional fields populated
    Given a valid create_media_buy request with a package containing:
    | field                 | value                                |
    | buyer_ref             | pkg-full                             |
    | product_id            | prod-1                               |
    | budget                | 8000                                 |
    | pricing_option_id     | cpm-auction                          |
    | bid_price             | 2.50                                 |
    | pacing                | even                                 |
    | impressions           | 100000                               |
    | format_ids            | [banner-300x250, banner-728x90]      |
    | paused                | false                                |
    | catalogs              | [{"type": "product", "catalog_id": "cat-1"}] |
    | optimization_goals    | [{"metric": "clicks", "priority": 1}] |
    | creative_assignments  | [{"creative_id": "cr-1", "weight": 1.0}] |
    | targeting_overlay     | {"audiences": [{"audience_id": "aud-1"}]} |
    When the Buyer Agent sends the create_media_buy request
    Then the response should contain a package with all provided fields echoed
    And the package should contain the seller-assigned package_id
    # POST-S2: Complete package state returned with all fields

  @T-UC-026-alt-update @alt-flow @update @post-s2 @post-s4
  Scenario: Update package budget via package_id
    Given the Buyer owns a media buy with a package "pkg-001" having budget 5000
    And a valid update_media_buy request with package update:
    | field      | value   |
    | package_id | pkg-001 |
    | budget     | 7500    |
    When the Buyer Agent sends the update_media_buy request
    Then the response should contain the updated package with budget 7500
    And the package paused state should be unchanged
    # POST-S2: Updated package state reflects new budget
    # POST-S4: Paused state unchanged

  @T-UC-026-alt-update-buyer-ref @alt-flow @update @post-s2
  Scenario: Update package budget via buyer_ref
    Given the Buyer owns a media buy with a package identified by buyer_ref "pkg-my-ref"
    And a valid update_media_buy request with package update:
    | field      | value       |
    | buyer_ref  | pkg-my-ref  |
    | budget     | 6000        |
    When the Buyer Agent sends the update_media_buy request
    Then the response should contain the updated package with budget 6000
    # POST-S2: Updated state reflects change

  @T-UC-026-alt-pause @alt-flow @pause @post-s4
  Scenario: Pause a running package
    Given the Buyer owns a media buy with an active package "pkg-001" (paused=false)
    And a valid update_media_buy request with package update:
    | field      | value   |
    | package_id | pkg-001 |
    | paused     | true    |
    When the Buyer Agent sends the update_media_buy request
    Then the response should contain the package with paused=true
    And the package should not deliver impressions
    # POST-S4: Paused state updated to true

  @T-UC-026-alt-resume @alt-flow @resume @post-s4
  Scenario: Resume a paused package
    Given the Buyer owns a media buy with a paused package "pkg-001" (paused=true)
    And a valid update_media_buy request with package update:
    | field      | value   |
    | package_id | pkg-001 |
    | paused     | false   |
    When the Buyer Agent sends the update_media_buy request
    Then the response should contain the package with paused=false
    And the package should resume delivering impressions
    # POST-S4: Paused state updated to false

  @T-UC-026-alt-keyword-add @alt-flow @keyword @post-s2
  Scenario: Add new keyword targets via keyword_targets_add
    Given the Buyer owns a media buy with a package "pkg-001" having no keyword targets
    And a valid update_media_buy request with package update:
    | field               | value                                              |
    | package_id          | pkg-001                                            |
    | keyword_targets_add | [{"keyword": "shoes", "match_type": "broad", "bid_price": 2.50}] |
    When the Buyer Agent sends the update_media_buy request
    Then the response should contain the package with keyword "shoes" in targeting_overlay
    # POST-S2: Updated targeting state reflects keyword addition

  @T-UC-026-alt-keyword-upsert @alt-flow @keyword @invariant @BR-RULE-199
  Scenario: Upsert existing keyword target -- bid_price updated (INV-2 holds)
    Given the Buyer owns a media buy with a package "pkg-001" having keyword target ("shoes", "broad", bid_price=2.50)
    And a valid update_media_buy request with package update:
    | field               | value                                              |
    | package_id          | pkg-001                                            |
    | keyword_targets_add | [{"keyword": "shoes", "match_type": "broad", "bid_price": 3.50}] |
    When the Buyer Agent sends the update_media_buy request
    Then the response should contain keyword "shoes" with match_type "broad" and updated bid_price 3.50
    # BR-RULE-199 INV-2: Existing (keyword, match_type) pair gets bid_price updated

  @T-UC-026-alt-keyword-remove @alt-flow @keyword @invariant @BR-RULE-202
  Scenario: Remove existing keyword target (INV-1 holds)
    Given the Buyer owns a media buy with a package "pkg-001" having keyword target ("shoes", "broad")
    And a valid update_media_buy request with package update:
    | field                  | value                                       |
    | package_id             | pkg-001                                     |
    | keyword_targets_remove | [{"keyword": "shoes", "match_type": "broad"}] |
    When the Buyer Agent sends the update_media_buy request
    Then the response should not contain keyword "shoes" with match_type "broad" in targeting_overlay
    # BR-RULE-202 INV-1: Matching pair removed

  @T-UC-026-alt-keyword-remove-noop @alt-flow @keyword @invariant @BR-RULE-202
  Scenario: Remove non-existent keyword target -- no-op (INV-2 holds)
    Given the Buyer owns a media buy with a package "pkg-001" having no keyword target ("nonexistent", "exact")
    And a valid update_media_buy request with package update:
    | field                  | value                                              |
    | package_id             | pkg-001                                            |
    | keyword_targets_remove | [{"keyword": "nonexistent", "match_type": "exact"}] |
    When the Buyer Agent sends the update_media_buy request
    Then the response should succeed with package targeting unchanged
    # BR-RULE-202 INV-2: Non-matching pair treated as no-op

  @T-UC-026-alt-negative-keyword-add @alt-flow @keyword @negative-keyword
  Scenario: Add negative keywords via negative_keywords_add
    Given the Buyer owns a media buy with a package "pkg-001"
    And a valid update_media_buy request with package update:
    | field                 | value                                              |
    | package_id            | pkg-001                                            |
    | negative_keywords_add | [{"keyword": "free", "match_type": "exact"}]       |
    When the Buyer Agent sends the update_media_buy request
    Then the response should contain negative keyword "free" in targeting_overlay

  @T-UC-026-alt-negative-keyword-remove-noop @alt-flow @keyword @negative-keyword @invariant @BR-RULE-202
  Scenario: Remove non-existent negative keyword -- no-op (INV-4 holds)
    Given the Buyer owns a media buy with a package "pkg-001" having no negative keyword ("absent", "broad")
    And a valid update_media_buy request with package update:
    | field                    | value                                         |
    | package_id               | pkg-001                                       |
    | negative_keywords_remove | [{"keyword": "absent", "match_type": "broad"}] |
    When the Buyer Agent sends the update_media_buy request
    Then the response should succeed with package negative keywords unchanged
    # BR-RULE-202 INV-4: Non-matching negative keyword pair treated as no-op

  @T-UC-026-alt-dedup @alt-flow @dedup @post-s1 @post-s6
  Scenario: Duplicate buyer_ref returns existing package
    Given the Buyer owns a media buy with a package having buyer_ref "pkg-001" and package_id "existing-pkg"
    And a valid create_media_buy request with a package containing:
    | field              | value        |
    | buyer_ref          | pkg-001      |
    | product_id         | prod-1       |
    | budget             | 5000         |
    | pricing_option_id  | cpm-standard |
    When the Buyer Agent sends the create_media_buy request
    Then the response should contain the existing package with package_id "existing-pkg"
    And no duplicate package should be created
    # POST-S1: Buyer receives existing package_id
    # POST-S6: Deduplication applied

  @T-UC-026-alt-dedup-crossbuy @alt-flow @dedup @invariant @BR-RULE-194
  Scenario: Same buyer_ref in different media buy -- not a duplicate (INV-3 holds)
    Given the Buyer owns media buy "mb-A" with a package having buyer_ref "pkg-001"
    And the Buyer is creating a new media buy "mb-B"
    And the create_media_buy request for "mb-B" includes a package with buyer_ref "pkg-001"
    When the Buyer Agent sends the create_media_buy request for "mb-B"
    Then a new package should be created in "mb-B" with a new package_id
    # BR-RULE-194 INV-3: No cross-buy deduplication

  @T-UC-026-ext-a @extension @ext-a @error @post-f1 @post-f2 @post-f3
  Scenario: Package references unknown product_id -- PRODUCT_NOT_FOUND
    Given a valid create_media_buy request with a package containing:
    | field              | value             |
    | buyer_ref          | pkg-bad-prod      |
    | product_id         | nonexistent-prod  |
    | budget             | 5000              |
    | pricing_option_id  | cpm-standard      |
    And the product "nonexistent-prod" does not exist in seller inventory
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "PRODUCT_NOT_FOUND"
    And the error message should contain "product"
    And the error should include "suggestion" field
    And the suggestion should contain "get_products"
    And the error recovery should be "correctable"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error identifies unknown product_id
    # POST-F3: Suggestion advises re-discovering products

  @T-UC-026-ext-b @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario Outline: Missing required package field -- INVALID_REQUEST (<missing_field>)
    Given a valid create_media_buy request with a package missing <missing_field>
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error message should contain "<missing_field>"
    And the error should include "suggestion" field
    And the suggestion should contain "buyer_ref, product_id, budget, pricing_option_id"
    And the error recovery should be "correctable"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error identifies the specific missing field
    # POST-F3: Suggestion lists all required fields

    Examples: Missing individual required fields
      | missing_field      |
      | buyer_ref          |
      | product_id         |
      | budget             |
      | pricing_option_id  |

  @T-UC-026-ext-c @extension @ext-c @error @post-f1 @post-f2 @post-f3
  Scenario: Pricing option not found in product -- INVALID_REQUEST
    Given a valid create_media_buy request with a package containing:
    | field              | value                |
    | buyer_ref          | pkg-bad-price        |
    | product_id         | prod-1               |
    | budget             | 5000                 |
    | pricing_option_id  | nonexistent-option   |
    And the pricing_option_id "nonexistent-option" is not in product "prod-1" pricing_options
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error message should contain "pricing_option_id"
    And the error should include "suggestion" field
    And the suggestion should contain "pricing_options"
    And the error recovery should be "correctable"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error identifies invalid pricing_option_id
    # POST-F3: Suggestion advises checking product's available pricing options

  @T-UC-026-ext-d @extension @ext-d @error @post-f1 @post-f2 @post-f3
  Scenario: Package budget below product minimum -- BUDGET_TOO_LOW
    Given the product "prod-1" has a minimum spend requirement of 1000
    And a valid create_media_buy request with a package containing:
    | field              | value        |
    | buyer_ref          | pkg-low-bud  |
    | product_id         | prod-1       |
    | budget             | 500          |
    | pricing_option_id  | cpm-standard |
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "BUDGET_TOO_LOW"
    And the error message should contain "budget"
    And the error should include "suggestion" field
    And the suggestion should contain "increase budget"
    And the error recovery should be "correctable"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error indicates budget is below product minimum
    # POST-F3: Suggestion advises increasing budget

  @T-UC-026-ext-e @extension @ext-e @error @post-f1 @post-f2 @post-f3
  Scenario: Format_ids not supported by product -- INVALID_REQUEST
    Given a valid create_media_buy request with a package containing:
    | field              | value                 |
    | buyer_ref          | pkg-bad-fmt           |
    | product_id         | prod-1                |
    | budget             | 5000                  |
    | pricing_option_id  | cpm-standard          |
    | format_ids         | [video-unsupported]   |
    And the format_id "video-unsupported" is not supported by product "prod-1"
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error message should contain "format"
    And the error should include "suggestion" field
    And the suggestion should contain "format_ids"
    And the error recovery should be "correctable"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error identifies unsupported format_ids
    # POST-F3: Suggestion advises checking product's supported formats

  @T-UC-026-ext-f @extension @ext-f @error @post-f1 @post-f2 @post-f3
  Scenario: Duplicate catalog types within package -- INVALID_REQUEST
    Given a valid create_media_buy request with a package containing:
    | field              | value                                                             |
    | buyer_ref          | pkg-dup-cat                                                       |
    | product_id         | prod-1                                                            |
    | budget             | 5000                                                              |
    | pricing_option_id  | cpm-standard                                                      |
    | catalogs           | [{"type": "product", "catalog_id": "c1"}, {"type": "product", "catalog_id": "c2"}] |
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error message should contain "catalog"
    And the error should include "suggestion" field
    And the suggestion should contain "distinct type"
    And the error recovery should be "correctable"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error identifies duplicate catalog types
    # POST-F3: Suggestion advises using distinct catalog types

  @T-UC-026-ext-g-product @extension @ext-g @error @post-f1 @post-f2 @post-f3 @invariant @BR-RULE-198
  Scenario: Update attempts to change product_id -- INVALID_REQUEST (INV-1 violated)
    Given the Buyer owns a media buy with a package "pkg-001" with product_id "prod-1"
    And a valid update_media_buy request with package update:
    | field      | value       |
    | package_id | pkg-001     |
    | product_id | prod-2      |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error message should contain "product_id"
    And the error message should contain "cannot be changed"
    And the error should include "suggestion" field
    And the suggestion should contain "new package"
    And the error recovery should be "correctable"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error identifies immutable product_id field
    # POST-F3: Suggestion advises creating a new package

  @T-UC-026-ext-g-format @extension @ext-g @error @post-f1 @post-f2 @post-f3 @invariant @BR-RULE-198
  Scenario: Update attempts to change format_ids -- INVALID_REQUEST (INV-2 violated)
    Given the Buyer owns a media buy with a package "pkg-001" with format_ids ["banner-300x250"]
    And a valid update_media_buy request with package update:
    | field      | value              |
    | package_id | pkg-001            |
    | format_ids | [banner-728x90]    |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error message should contain "format_ids"
    And the error message should contain "cannot be changed"
    And the error should include "suggestion" field
    And the suggestion should contain "new package"
    And the error recovery should be "correctable"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error identifies immutable format_ids field
    # POST-F3: Suggestion advises creating a new package

  @T-UC-026-ext-g-pricing @extension @ext-g @error @post-f1 @post-f2 @post-f3 @invariant @BR-RULE-198
  Scenario: Update attempts to change pricing_option_id -- INVALID_REQUEST (INV-3 violated)
    Given the Buyer owns a media buy with a package "pkg-001" with pricing_option_id "cpm-standard"
    And a valid update_media_buy request with package update:
    | field              | value       |
    | package_id         | pkg-001     |
    | pricing_option_id  | cpm-auction |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error message should contain "pricing_option_id"
    And the error message should contain "cannot be changed"
    And the error should include "suggestion" field
    And the suggestion should contain "new package"
    And the error recovery should be "correctable"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error identifies immutable pricing_option_id field
    # POST-F3: Suggestion advises creating a new package

  @T-UC-026-ext-h-keyword @extension @ext-h @error @post-f1 @post-f2 @post-f3 @invariant @BR-RULE-083
  Scenario: Conflicting keyword_targets_add with targeting_overlay.keyword_targets -- INVALID_REQUEST (INV-1 violated)
    Given the Buyer owns a media buy with a package "pkg-001"
    And a valid update_media_buy request with package update:
    | field                             | value                                            |
    | package_id                        | pkg-001                                          |
    | keyword_targets_add               | [{"keyword": "shoes", "match_type": "broad"}]    |
    | targeting_overlay.keyword_targets  | [{"keyword": "hats", "match_type": "exact"}]     |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error message should contain "keyword"
    And the error message should contain "mutually exclusive"
    And the error should include "suggestion" field
    And the suggestion should contain "targeting_overlay.keyword_targets"
    And the error recovery should be "correctable"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains conflict between replacement and incremental modes
    # POST-F3: Suggestion advises choosing one mode

  @T-UC-026-ext-h-negative @extension @ext-h @error @post-f1 @post-f2 @post-f3 @invariant @BR-RULE-083
  Scenario: Conflicting negative_keywords_add with targeting_overlay.negative_keywords -- INVALID_REQUEST (INV-2 violated)
    Given the Buyer owns a media buy with a package "pkg-001"
    And a valid update_media_buy request with package update:
    | field                               | value                                            |
    | package_id                          | pkg-001                                          |
    | negative_keywords_add               | [{"keyword": "free", "match_type": "exact"}]     |
    | targeting_overlay.negative_keywords  | [{"keyword": "cheap", "match_type": "broad"}]    |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error message should contain "negative_keywords"
    And the error message should contain "mutually exclusive"
    And the error should include "suggestion" field
    And the suggestion should contain "targeting_overlay.negative_keywords"
    And the error recovery should be "correctable"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains conflict between replacement and incremental modes
    # POST-F3: Suggestion advises choosing one mode

  @T-UC-026-ext-h-cross-ok @extension @ext-h @invariant @BR-RULE-083
  Scenario: Cross-dimension mixing allowed -- keyword_targets_add with targeting_overlay.negative_keywords (INV-3 holds)
    Given the Buyer owns a media buy with a package "pkg-001"
    And a valid update_media_buy request with package update:
    | field                               | value                                            |
    | package_id                          | pkg-001                                          |
    | keyword_targets_add                 | [{"keyword": "shoes", "match_type": "broad"}]    |
    | targeting_overlay.negative_keywords  | [{"keyword": "cheap", "match_type": "broad"}]    |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should succeed
    And the response should contain updated keyword targets and negative keywords
    # BR-RULE-083 INV-3: Cross-dimension mixing is valid

  @T-UC-026-ext-h-cross-reverse @extension @ext-h @invariant @BR-RULE-083
  Scenario: Cross-dimension mixing allowed -- negative_keywords_add with targeting_overlay.keyword_targets (INV-4 holds)
    Given the Buyer owns a media buy with a package "pkg-001"
    And a valid update_media_buy request with package update:
    | field                             | value                                            |
    | package_id                        | pkg-001                                          |
    | negative_keywords_add             | [{"keyword": "free", "match_type": "exact"}]     |
    | targeting_overlay.keyword_targets  | [{"keyword": "shoes", "match_type": "broad"}]    |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should succeed
    And the response should contain updated keyword targets and negative keywords
    # BR-RULE-083 INV-4: Cross-dimension reverse mixing is valid

  @T-UC-026-ext-i @extension @ext-i @error @post-f1 @post-f2 @post-f3 @invariant @BR-RULE-021
  Scenario: Package update with neither package_id nor buyer_ref -- INVALID_REQUEST (INV-3 at package level)
    Given a valid update_media_buy request with package update:
    | field  | value |
    | budget | 7000  |
    And the package update contains neither package_id nor buyer_ref
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error message should contain "package_id"
    And the error message should contain "buyer_ref"
    And the error should include "suggestion" field
    And the suggestion should contain "package_id or buyer_ref"
    And the error recovery should be "correctable"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains that a package identifier is required
    # POST-F3: Suggestion advises providing package_id or buyer_ref

  @T-UC-026-inv-194-1 @invariant @BR-RULE-194
  Scenario: INV-1 holds -- new buyer_ref creates new package
    Given the Buyer is creating a media buy with no existing packages
    And a valid create_media_buy request with a package containing buyer_ref "brand-new-ref"
    When the Buyer Agent sends the create_media_buy request
    Then a new package should be created with a seller-assigned package_id
    # BR-RULE-194 INV-1: New buyer_ref creates new package

  @T-UC-026-inv-194-2 @invariant @BR-RULE-194
  Scenario: INV-2 holds -- duplicate buyer_ref returns existing package
    Given the Buyer owns a media buy with a package having buyer_ref "pkg-001"
    And a valid create_media_buy request resubmits buyer_ref "pkg-001" in the same media buy
    When the Buyer Agent sends the create_media_buy request
    Then the existing package should be returned without creating a duplicate
    # BR-RULE-194 INV-2: Duplicate buyer_ref returns existing package

  @T-UC-026-inv-195-1 @invariant @BR-RULE-195
  Scenario: INV-1 holds -- valid pricing_option_id resolves successfully
    Given the product "prod-1" has pricing_option "cpm-standard" in its pricing_options array
    And a valid create_media_buy request with a package containing pricing_option_id "cpm-standard"
    When the Buyer Agent sends the create_media_buy request
    Then the package should be created with pricing_option_id "cpm-standard"
    # BR-RULE-195 INV-1: pricing_option_id matches entry in product

  @T-UC-026-inv-195-2 @invariant @BR-RULE-195 @error
  Scenario: INV-2 violated -- pricing_option_id not in product
    Given the product "prod-1" does not have pricing_option "nonexistent-option"
    And a valid create_media_buy request with a package containing pricing_option_id "nonexistent-option"
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "pricing_options"
    # BR-RULE-195 INV-2: pricing_option_id not found -> INVALID_REQUEST

  @T-UC-026-inv-195-3 @invariant @BR-RULE-195
  Scenario: INV-3 holds -- max_bid=true means bid_price is ceiling
    Given the product "prod-1" has pricing_option "cpm-auction" with max_bid=true
    And a valid create_media_buy request with a package containing:
    | field              | value       |
    | pricing_option_id  | cpm-auction |
    | bid_price          | 5.00        |
    When the Buyer Agent sends the create_media_buy request
    Then the package should be created with bid_price 5.00 interpreted as ceiling
    # BR-RULE-195 INV-3: max_bid=true, bid_price is ceiling

  @T-UC-026-inv-195-4 @invariant @BR-RULE-195
  Scenario: INV-4 holds -- max_bid=false means bid_price is exact
    Given the product "prod-1" has pricing_option "cpm-standard" with max_bid=false
    And a valid create_media_buy request with a package containing:
    | field              | value        |
    | pricing_option_id  | cpm-standard |
    | bid_price          | 2.50         |
    When the Buyer Agent sends the create_media_buy request
    Then the package should be created with bid_price 2.50 interpreted as exact bid
    # BR-RULE-195 INV-4: max_bid=false, bid_price is exact

  @T-UC-026-inv-196-3 @invariant @BR-RULE-196
  Scenario: INV-3 holds -- bid_price omitted uses pricing option defaults
    Given a valid create_media_buy request with a package containing no bid_price
    When the Buyer Agent sends the create_media_buy request
    Then the package should be created without a bid_price
    And pricing should be determined by pricing option defaults
    # BR-RULE-196 INV-3: No bid semantics when bid_price omitted

  @T-UC-026-inv-197-3 @invariant @BR-RULE-197
  Scenario: INV-3 holds -- format_ids omitted defaults to all product formats
    Given the product "prod-1" supports format_ids ["banner-300x250", "banner-728x90"]
    And a valid create_media_buy request with a package containing no format_ids
    When the Buyer Agent sends the create_media_buy request
    Then the package should be created with format_ids ["banner-300x250", "banner-728x90"]
    # BR-RULE-197 INV-3: format_ids omitted defaults to all product formats

  @T-UC-026-inv-197-4 @invariant @BR-RULE-197 @error
  Scenario: INV-4 violated -- empty format_ids array rejected
    Given a valid create_media_buy request with a package containing format_ids as empty array []
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "format_ids"
    # BR-RULE-197 INV-4: Empty array violates minItems: 1

  @T-UC-026-inv-198-4 @invariant @BR-RULE-198
  Scenario: INV-4 holds -- update only mutable fields succeeds
    Given the Buyer owns a media buy with a package "pkg-001"
    And a valid update_media_buy request with package update:
    | field      | value   |
    | package_id | pkg-001 |
    | budget     | 9000    |
    | pacing     | even    |
    When the Buyer Agent sends the update_media_buy request
    Then the response should contain the updated package with budget 9000 and pacing "even"
    # BR-RULE-198 INV-4: Mutable-only update processed normally

  @T-UC-026-inv-199-3 @invariant @BR-RULE-199
  Scenario: INV-3 holds -- same keyword with different match_types treated independently
    Given the Buyer owns a media buy with a package "pkg-001"
    And a valid update_media_buy request with package update:
    | field               | value                                                                            |
    | package_id          | pkg-001                                                                          |
    | keyword_targets_add | [{"keyword": "shoes", "match_type": "broad"}, {"keyword": "shoes", "match_type": "exact"}] |
    When the Buyer Agent sends the update_media_buy request
    Then the response should contain keyword "shoes" with match_type "broad"
    And the response should contain keyword "shoes" with match_type "exact"
    # BR-RULE-199 INV-3: Each (keyword, match_type) pair treated independently

  @T-UC-026-inv-199-4 @invariant @BR-RULE-199
  Scenario: INV-4 holds -- per-keyword bid_price inherits max_bid semantics
    Given the Buyer owns a media buy with a package "pkg-001" using pricing_option with max_bid=true
    And a valid update_media_buy request with package update:
    | field               | value                                                       |
    | package_id          | pkg-001                                                     |
    | keyword_targets_add | [{"keyword": "shoes", "match_type": "broad", "bid_price": 4.00}] |
    When the Buyer Agent sends the update_media_buy request
    Then the keyword bid_price 4.00 should be interpreted as ceiling (max_bid=true)
    # BR-RULE-199 INV-4: Per-keyword bid inherits max_bid semantics

  @T-UC-026-inv-200-1 @invariant @BR-RULE-200
  Scenario: INV-1 holds -- paused omitted on create defaults to false
    Given a valid create_media_buy request with a package containing no paused field
    When the Buyer Agent sends the create_media_buy request
    Then the package should be created with paused=false
    And the package should deliver impressions
    # BR-RULE-200 INV-1: Defaults to active when omitted

  @T-UC-026-inv-200-2 @invariant @BR-RULE-200
  Scenario: INV-2 holds -- paused=true on create means no delivery
    Given a valid create_media_buy request with a package containing paused=true
    When the Buyer Agent sends the create_media_buy request
    Then the package should be created with paused=true
    And the package should not deliver impressions
    # BR-RULE-200 INV-2: paused=true means no delivery

  @T-UC-026-inv-201-1 @invariant @BR-RULE-201
  Scenario: INV-1 holds -- catalogs provided replaces existing list
    Given the Buyer owns a media buy with a package "pkg-001" having catalogs [{"type": "product", "catalog_id": "cat-1"}]
    And a valid update_media_buy request with package update:
    | field      | value                                          |
    | package_id | pkg-001                                        |
    | catalogs   | [{"type": "store", "catalog_id": "cat-2"}]     |
    When the Buyer Agent sends the update_media_buy request
    Then the package catalogs should be [{"type": "store", "catalog_id": "cat-2"}]
    And the old catalog "cat-1" should not be present
    # BR-RULE-201 INV-1: Catalogs replaced, not merged

  @T-UC-026-inv-201-2 @invariant @BR-RULE-201
  Scenario: INV-2 holds -- optimization_goals provided replaces existing
    Given the Buyer owns a media buy with a package "pkg-001" having optimization_goals [{"metric": "impressions"}]
    And a valid update_media_buy request with package update:
    | field              | value                                    |
    | package_id         | pkg-001                                  |
    | optimization_goals | [{"metric": "clicks", "priority": 1}]   |
    When the Buyer Agent sends the update_media_buy request
    Then the package optimization_goals should be [{"metric": "clicks", "priority": 1}]
    # BR-RULE-201 INV-2: optimization_goals replaced

  @T-UC-026-inv-201-3 @invariant @BR-RULE-201
  Scenario: INV-3 holds -- creative_assignments provided replaces existing
    Given the Buyer owns a media buy with a package "pkg-001" having creative_assignments [{"creative_id": "cr-1"}]
    And a valid update_media_buy request with package update:
    | field                | value                                    |
    | package_id           | pkg-001                                  |
    | creative_assignments | [{"creative_id": "cr-2", "weight": 0.5}] |
    When the Buyer Agent sends the update_media_buy request
    Then the package creative_assignments should be [{"creative_id": "cr-2", "weight": 0.5}]
    # BR-RULE-201 INV-3: creative_assignments replaced

  @T-UC-026-inv-201-4 @invariant @BR-RULE-201
  Scenario: INV-4 holds -- targeting_overlay provided replaces existing
    Given the Buyer owns a media buy with a package "pkg-001" having targeting_overlay with audiences ["aud-1"]
    And a valid update_media_buy request with package update:
    | field             | value                                                |
    | package_id        | pkg-001                                              |
    | targeting_overlay | {"audiences": [{"audience_id": "aud-2"}]}            |
    When the Buyer Agent sends the update_media_buy request
    Then the package targeting_overlay should contain only audience "aud-2"
    And the old audience "aud-1" should not be present
    # BR-RULE-201 INV-4: targeting_overlay replaced

  @T-UC-026-inv-201-5 @invariant @BR-RULE-201
  Scenario: INV-5 holds -- omitted array fields preserved
    Given the Buyer owns a media buy with a package "pkg-001" having catalogs and optimization_goals
    And a valid update_media_buy request with package update:
    | field      | value   |
    | package_id | pkg-001 |
    | budget     | 8000    |
    When the Buyer Agent sends the update_media_buy request
    Then the package budget should be 8000
    And the package catalogs should be unchanged
    And the package optimization_goals should be unchanged
    # BR-RULE-201 INV-5: Omitted fields preserved (patch semantics)

  @T-UC-026-inv-089-2 @invariant @BR-RULE-089
  Scenario: INV-2 holds -- distinct catalog types accepted
    Given a valid create_media_buy request with a package containing:
    | field    | value                                                                        |
    | catalogs | [{"type": "product", "catalog_id": "c1"}, {"type": "store", "catalog_id": "c2"}] |
    When the Buyer Agent sends the create_media_buy request
    Then the package should be created with both catalogs
    # BR-RULE-089 INV-2: All distinct types -> accepted

  @T-UC-026-inv-089-3 @invariant @BR-RULE-089
  Scenario: INV-3 holds -- no catalogs means non-catalog-driven package
    Given a valid create_media_buy request with a package containing no catalogs field
    When the Buyer Agent sends the create_media_buy request
    Then the package should be created without catalogs
    # BR-RULE-089 INV-3: No catalogs, no constraint applies

  @T-UC-026-partition-required-fields @partition @package_required_fields
  Scenario Outline: Package required fields partition validation -- <partition>
    Given a create_media_buy request with package fields per <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the outcome should be <outcome>

    Examples: Valid partitions
      | partition          | outcome                          |
      | all_four_present   | success with new package created |
      | budget_zero        | success with budget 0            |

    Examples: Invalid partitions
      | partition               | outcome                                                    |
      | missing_buyer_ref       | error "INVALID_REQUEST" with suggestion                    |
      | missing_product_id      | error "INVALID_REQUEST" with suggestion                    |
      | missing_budget          | error "INVALID_REQUEST" with suggestion                    |
      | missing_pricing_option_id | error "INVALID_REQUEST" with suggestion                  |
      | negative_budget         | error "INVALID_REQUEST" with suggestion                    |

  @T-UC-026-boundary-required-fields @boundary @package_required_fields
  Scenario Outline: Package required fields boundary validation -- <boundary_point>
    Given a create_media_buy request per boundary <boundary_point>
    When the Buyer Agent sends the create_media_buy request
    Then the outcome should be <outcome>

    Examples: Boundary values
      | boundary_point                                        | outcome                                                    |
      | all four required fields present with valid values    | success                                                    |
      | budget = 0 (minimum boundary)                         | success                                                    |
      | budget = -0.01 (below minimum)                        | error "INVALID_REQUEST" with suggestion                    |
      | buyer_ref missing                                     | error "INVALID_REQUEST" with suggestion                    |
      | product_id missing                                    | error "INVALID_REQUEST" with suggestion                    |
      | budget missing                                        | error "INVALID_REQUEST" with suggestion                    |
      | pricing_option_id missing                             | error "INVALID_REQUEST" with suggestion                    |

  @T-UC-026-partition-bid-price @partition @bid_price
  Scenario Outline: Bid price partition validation -- <partition>
    Given a create_media_buy request with package bid_price per <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the outcome should be <outcome>

    Examples: Valid partitions
      | partition     | outcome                                    |
      | exact_bid     | success with bid_price as exact bid         |
      | ceiling_bid   | success with bid_price as ceiling           |
      | zero_bid      | success with bid_price 0                   |
      | bid_absent    | success without bid_price                  |

    Examples: Invalid partitions
      | partition      | outcome                                    |
      | negative_bid   | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-boundary-bid-price @boundary @bid_price
  Scenario Outline: Bid price boundary validation -- <boundary_point>
    Given a create_media_buy request with package bid_price per boundary <boundary_point>
    When the Buyer Agent sends the create_media_buy request
    Then the outcome should be <outcome>

    Examples: Boundary values
      | boundary_point                                  | outcome                                    |
      | bid_price = 0 (minimum boundary)                | success                                    |
      | bid_price = 0.01 (just above minimum)           | success                                    |
      | bid_price = -0.01 (below minimum)               | error "INVALID_REQUEST" with suggestion    |
      | bid_price absent (optional)                     | success                                    |
      | bid_price with max_bid=true pricing option      | success                                    |
      | bid_price with max_bid=false pricing option     | success                                    |

  @T-UC-026-partition-buyer-ref @partition @buyer_ref
  Scenario Outline: Buyer_ref deduplication partition validation -- <partition>
    Given a create_media_buy request with buyer_ref per <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the outcome should be <outcome>

    Examples: Valid partitions
      | partition               | outcome                                    |
      | new_buyer_ref           | success with new package created            |
      | duplicate_buyer_ref     | success with existing package returned      |
      | same_ref_different_buy  | success with new package in different buy   |

    Examples: Invalid partitions
      | partition           | outcome                                    |
      | buyer_ref_missing   | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-boundary-buyer-ref @boundary @buyer_ref
  Scenario Outline: Buyer_ref deduplication boundary validation -- <boundary_point>
    Given a create_media_buy request with buyer_ref per boundary <boundary_point>
    When the Buyer Agent sends the create_media_buy request
    Then the outcome should be <outcome>

    Examples: Boundary values
      | boundary_point                                      | outcome                                    |
      | first submission of buyer_ref in media buy          | success                                    |
      | second submission of same buyer_ref in same media buy | success (existing returned)               |
      | same buyer_ref in different media buy               | success (new package)                      |
      | buyer_ref absent on create                          | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-partition-format-ids @partition @format_ids
  Scenario Outline: Format_ids partition validation -- <partition>
    Given a create_media_buy request with format_ids per <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the outcome should be <outcome>

    Examples: Valid partitions
      | partition                  | outcome                                         |
      | subset_of_product_formats  | success with specified formats                   |
      | all_product_formats        | success with all formats                         |
      | format_ids_omitted         | success with all product formats defaulted       |
      | single_format              | success with one format                          |

    Examples: Invalid partitions
      | partition           | outcome                                    |
      | unsupported_format  | error "INVALID_REQUEST" with suggestion    |
      | empty_array         | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-boundary-format-ids @boundary @format_ids
  Scenario Outline: Format_ids boundary validation -- <boundary_point>
    Given a create_media_buy request with format_ids per boundary <boundary_point>
    When the Buyer Agent sends the create_media_buy request
    Then the outcome should be <outcome>

    Examples: Boundary values
      | boundary_point                                    | outcome                                    |
      | format_ids omitted (defaults to all product formats) | success                                 |
      | single format_id matching product format          | success                                    |
      | all product formats explicitly listed             | success                                    |
      | one unsupported format_id among valid ones        | error "INVALID_REQUEST" with suggestion    |
      | empty array []                                    | error "INVALID_REQUEST" with suggestion    |
      | format_id from different product                  | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-partition-pricing-option @partition @pricing_option_id
  Scenario Outline: Pricing option partition validation -- <partition>
    Given a create_media_buy request with pricing_option_id per <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the outcome should be <outcome>

    Examples: Valid partitions
      | partition              | outcome                                    |
      | valid_pricing_option   | success with pricing resolved               |
      | valid_with_max_bid     | success with ceiling semantics              |

    Examples: Invalid partitions
      | partition                    | outcome                                    |
      | pricing_option_not_found     | error "INVALID_REQUEST" with suggestion    |
      | pricing_option_wrong_product | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-boundary-pricing-option @boundary @pricing_option_id
  Scenario Outline: Pricing option boundary validation -- <boundary_point>
    Given a create_media_buy request with pricing_option_id per boundary <boundary_point>
    When the Buyer Agent sends the create_media_buy request
    Then the outcome should be <outcome>

    Examples: Boundary values
      | boundary_point                                      | outcome                                    |
      | pricing_option_id matches first entry in pricing_options | success                               |
      | pricing_option_id matches last entry in pricing_options  | success                               |
      | pricing_option_id matches entry with max_bid=true   | success                                    |
      | pricing_option_id not in product's pricing_options  | error "INVALID_REQUEST" with suggestion    |
      | pricing_option_id from different product            | error "INVALID_REQUEST" with suggestion    |
      | empty string pricing_option_id                      | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-partition-immutable @partition @package_immutable_fields
  Scenario Outline: Immutable fields partition validation -- <partition>
    Given a package update request per <partition>
    When the Buyer Agent sends the update_media_buy request
    Then the outcome should be <outcome>

    Examples: Valid partitions
      | partition                     | outcome                                    |
      | update_mutable_only           | success with updated fields                |
      | no_immutable_fields_present   | success with update applied                |

    Examples: Invalid partitions
      | partition                  | outcome                                    |
      | product_id_change          | error "INVALID_REQUEST" with suggestion    |
      | format_ids_change          | error "INVALID_REQUEST" with suggestion    |
      | pricing_option_id_change   | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-boundary-immutable @boundary @package_immutable_fields
  Scenario Outline: Immutable fields boundary validation -- <boundary_point>
    Given a package update request per boundary <boundary_point>
    When the Buyer Agent sends the update_media_buy request
    Then the outcome should be <outcome>

    Examples: Boundary values
      | boundary_point                                | outcome                                    |
      | update with only mutable fields               | success                                    |
      | update includes product_id                    | error "INVALID_REQUEST" with suggestion    |
      | update includes format_ids                    | error "INVALID_REQUEST" with suggestion    |
      | update includes pricing_option_id             | error "INVALID_REQUEST" with suggestion    |
      | update includes all three immutable fields    | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-partition-keyword-add @partition @keyword_targets_add
  Scenario Outline: Keyword targets add partition validation -- <partition>
    Given a package update request with keyword_targets_add per <partition>
    When the Buyer Agent sends the update_media_buy request
    Then the outcome should be <outcome>

    Examples: Valid partitions
      | partition                    | outcome                                    |
      | new_keyword                  | success with keyword added                 |
      | existing_keyword_update_bid  | success with bid_price updated             |
      | mixed_new_and_update         | success with mixed operations              |
      | same_keyword_different_match | success with distinct entries              |

    Examples: Invalid partitions
      | partition           | outcome                                    |
      | empty_keyword       | error "INVALID_REQUEST" with suggestion    |
      | invalid_match_type  | error "INVALID_REQUEST" with suggestion    |
      | negative_bid_price  | error "INVALID_REQUEST" with suggestion    |
      | empty_array         | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-boundary-keyword-add @boundary @keyword_targets_add
  Scenario Outline: Keyword targets add boundary validation -- <boundary_point>
    Given a package update request with keyword_targets_add per boundary <boundary_point>
    When the Buyer Agent sends the update_media_buy request
    Then the outcome should be <outcome>

    Examples: Boundary values
      | boundary_point                                             | outcome                                    |
      | single new keyword target with bid_price                   | success                                    |
      | existing (keyword, match_type) pair with updated bid_price | success                                    |
      | same keyword with broad and exact match_type (distinct)    | success                                    |
      | empty keyword string                                       | error "INVALID_REQUEST" with suggestion    |
      | unknown match_type value                                   | error "INVALID_REQUEST" with suggestion    |
      | bid_price = 0 (minimum boundary)                           | success                                    |
      | bid_price = -0.01 (below minimum)                          | error "INVALID_REQUEST" with suggestion    |
      | empty array []                                             | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-partition-keyword-remove @partition @keyword_targets_remove
  Scenario Outline: Keyword targets remove partition validation -- <partition>
    Given a package update request with keyword_targets_remove per <partition>
    When the Buyer Agent sends the update_media_buy request
    Then the outcome should be <outcome>

    Examples: Valid partitions
      | partition                       | outcome                                    |
      | remove_existing_pair            | success with pair removed                  |
      | remove_nonexistent_pair         | success (no-op)                            |
      | mixed_existing_and_nonexistent  | success with partial removal               |
      | remove_all_keywords             | success with all keywords removed          |

    Examples: Invalid partitions
      | partition           | outcome                                    |
      | empty_keyword       | error "INVALID_REQUEST" with suggestion    |
      | invalid_match_type  | error "INVALID_REQUEST" with suggestion    |
      | empty_array         | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-boundary-keyword-remove @boundary @keyword_targets_remove
  Scenario Outline: Keyword targets remove boundary validation -- <boundary_point>
    Given a package update request with keyword_targets_remove per boundary <boundary_point>
    When the Buyer Agent sends the update_media_buy request
    Then the outcome should be <outcome>

    Examples: Boundary values
      | boundary_point                                 | outcome                                    |
      | remove single existing (keyword, match_type) pair | success                                |
      | remove non-existent pair (no-op)               | success                                    |
      | mix of existing and non-existent pairs         | success                                    |
      | remove all keyword targets                     | success                                    |
      | empty keyword string                           | error "INVALID_REQUEST" with suggestion    |
      | unknown match_type                             | error "INVALID_REQUEST" with suggestion    |
      | empty array []                                 | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-partition-kw-add-shared @partition @keyword_targets_add
  Scenario Outline: Keyword targets add shared partition validation -- <partition>
    Given a package update request with keyword_targets_add per shared <partition>
    When the Buyer Agent sends the update_media_buy request
    Then the outcome should be <outcome>

    Examples: Valid partitions
      | partition                | outcome                                    |
      | typical_add              | success with keywords added                |
      | boundary_min_array       | success with single entry                  |
      | boundary_min_keyword     | success with single-char keyword            |
      | add_with_bid_price       | success with bid_price set                 |
      | add_without_bid_price    | success inheriting package bid              |
      | upsert_existing          | success with bid_price updated             |
      | zero_bid_price           | success with bid_price 0                   |
      | all_match_types          | success with three distinct entries         |
      | cross_dimension_valid    | success with cross-dimension mix            |

    Examples: Invalid partitions
      | partition              | outcome                                    |
      | missing_keyword        | error "INVALID_REQUEST" with suggestion    |
      | missing_match_type     | error "INVALID_REQUEST" with suggestion    |
      | conflict_with_overlay  | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-partition-kw-remove-shared @partition @keyword_targets_remove
  Scenario Outline: Keyword targets remove shared partition validation -- <partition>
    Given a package update request with keyword_targets_remove per shared <partition>
    When the Buyer Agent sends the update_media_buy request
    Then the outcome should be <outcome>

    Examples: Valid partitions
      | partition                | outcome                                    |
      | typical_remove           | success with pairs removed                 |
      | boundary_min_array       | success with single entry                  |
      | boundary_min_keyword     | success with single-char keyword            |
      | remove_nonexistent       | success (no-op)                            |
      | all_match_types          | success with three match types removed     |
      | cross_dimension_valid    | success with cross-dimension mix            |

    Examples: Invalid partitions
      | partition              | outcome                                    |
      | missing_keyword        | error "INVALID_REQUEST" with suggestion    |
      | missing_match_type     | error "INVALID_REQUEST" with suggestion    |
      | conflict_with_overlay  | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-partition-neg-kw-add @partition @negative_keywords_add
  Scenario Outline: Negative keywords add shared partition validation -- <partition>
    Given a package update request with negative_keywords_add per shared <partition>
    When the Buyer Agent sends the update_media_buy request
    Then the outcome should be <outcome>

    Examples: Valid partitions
      | partition                | outcome                                    |
      | typical_add              | success with negative keywords added       |
      | boundary_min_array       | success with single entry                  |
      | boundary_min_keyword     | success with single-char keyword            |
      | add_duplicate            | success (no-op for duplicate)              |
      | all_match_types          | success with three distinct entries         |
      | cross_dimension_valid    | success with cross-dimension mix            |

    Examples: Invalid partitions
      | partition              | outcome                                    |
      | missing_keyword        | error "INVALID_REQUEST" with suggestion    |
      | missing_match_type     | error "INVALID_REQUEST" with suggestion    |
      | conflict_with_overlay  | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-partition-neg-kw-remove @partition @negative_keywords_remove
  Scenario Outline: Negative keywords remove shared partition validation -- <partition>
    Given a package update request with negative_keywords_remove per shared <partition>
    When the Buyer Agent sends the update_media_buy request
    Then the outcome should be <outcome>

    Examples: Valid partitions
      | partition                | outcome                                    |
      | typical_remove           | success with negative keywords removed     |
      | boundary_min_array       | success with single entry                  |
      | boundary_min_keyword     | success with single-char keyword            |
      | remove_nonexistent       | success (no-op)                            |
      | all_match_types          | success with three match types removed     |
      | cross_dimension_valid    | success with cross-dimension mix            |

    Examples: Invalid partitions
      | partition              | outcome                                    |
      | missing_keyword        | error "INVALID_REQUEST" with suggestion    |
      | missing_match_type     | error "INVALID_REQUEST" with suggestion    |
      | conflict_with_overlay  | error "INVALID_REQUEST" with suggestion    |

  @T-UC-026-boundary-kw-add-shared @boundary @keyword_targets_add
  Scenario Outline: Keyword targets add shared boundary validation -- <boundary_point>
    Given a package update request with keyword_targets_add per boundary <boundary_point>
    When the Buyer Agent sends the update_media_buy request
    Then the outcome should be <outcome>

    Examples: Boundary values
      | boundary_point                                                       | outcome                                    |
      | array length 0 (empty)                                               | error "INVALID_REQUEST" with suggestion    |
      | array length 1 (minimum valid)                                       | success                                    |
      | keyword length 0 (empty string)                                      | error "INVALID_REQUEST" with suggestion    |
      | keyword length 1 (single char)                                       | success                                    |
      | match_type = 'broad'                                                 | success                                    |
      | match_type = 'phrase'                                                | success                                    |
      | match_type = 'exact'                                                 | success                                    |
      | match_type = 'unknown'                                               | error "INVALID_REQUEST" with suggestion    |
      | keyword_targets_add WITH targeting_overlay.keyword_targets           | error "INVALID_REQUEST" with suggestion    |
      | keyword_targets_add WITHOUT targeting_overlay.keyword_targets        | success                                    |
      | keyword_targets_add WITH targeting_overlay.negative_keywords (cross-dimension) | success                          |

  @T-UC-026-boundary-kw-remove-shared @boundary @keyword_targets_remove
  Scenario Outline: Keyword targets remove shared boundary validation -- <boundary_point>
    Given a package update request with keyword_targets_remove per boundary <boundary_point>
    When the Buyer Agent sends the update_media_buy request
    Then the outcome should be <outcome>

    Examples: Boundary values
      | boundary_point                                                        | outcome                                    |
      | array length 0 (empty)                                                | error "INVALID_REQUEST" with suggestion    |
      | array length 1 (minimum valid)                                        | success                                    |
      | keyword length 0 (empty string)                                       | error "INVALID_REQUEST" with suggestion    |
      | keyword length 1 (single char)                                        | success                                    |
      | match_type = 'broad'                                                  | success                                    |
      | match_type = 'phrase'                                                 | success                                    |
      | match_type = 'exact'                                                  | success                                    |
      | match_type = 'unknown'                                                | error "INVALID_REQUEST" with suggestion    |
      | keyword_targets_remove WITH targeting_overlay.keyword_targets         | error "INVALID_REQUEST" with suggestion    |
      | keyword_targets_remove WITHOUT targeting_overlay.keyword_targets      | success                                    |
      | remove pair that exists in current list                               | success                                    |
      | remove pair that does NOT exist in current list (no-op)              | success                                    |

  @T-UC-026-boundary-neg-kw-add @boundary @negative_keywords_add
  Scenario Outline: Negative keywords add boundary validation -- <boundary_point>
    Given a package update request with negative_keywords_add per boundary <boundary_point>
    When the Buyer Agent sends the update_media_buy request
    Then the outcome should be <outcome>

    Examples: Boundary values
      | boundary_point                                                             | outcome                                    |
      | array length 0 (empty)                                                     | error "INVALID_REQUEST" with suggestion    |
      | array length 1 (minimum valid)                                             | success                                    |
      | keyword length 0 (empty string)                                            | error "INVALID_REQUEST" with suggestion    |
      | keyword length 1 (single char)                                             | success                                    |
      | match_type = 'broad'                                                       | success                                    |
      | match_type = 'phrase'                                                      | success                                    |
      | match_type = 'exact'                                                       | success                                    |
      | match_type = 'unknown'                                                     | error "INVALID_REQUEST" with suggestion    |
      | negative_keywords_add WITH targeting_overlay.negative_keywords              | error "INVALID_REQUEST" with suggestion    |
      | negative_keywords_add WITHOUT targeting_overlay.negative_keywords           | success                                    |
      | add pair that already exists in list (duplicate no-op)                     | success                                    |
      | negative_keywords_add WITH targeting_overlay.keyword_targets (cross-dimension) | success                                |

  @T-UC-026-boundary-neg-kw-remove @boundary @negative_keywords_remove
  Scenario Outline: Negative keywords remove boundary validation -- <boundary_point>
    Given a package update request with negative_keywords_remove per boundary <boundary_point>
    When the Buyer Agent sends the update_media_buy request
    Then the outcome should be <outcome>

    Examples: Boundary values
      | boundary_point                                                              | outcome                                    |
      | array length 0 (empty)                                                      | error "INVALID_REQUEST" with suggestion    |
      | array length 1 (minimum valid)                                              | success                                    |
      | keyword length 0 (empty string)                                             | error "INVALID_REQUEST" with suggestion    |
      | keyword length 1 (single char)                                              | success                                    |
      | match_type = 'broad'                                                        | success                                    |
      | match_type = 'phrase'                                                       | success                                    |
      | match_type = 'exact'                                                        | success                                    |
      | match_type = 'unknown'                                                      | error "INVALID_REQUEST" with suggestion    |
      | negative_keywords_remove WITH targeting_overlay.negative_keywords            | error "INVALID_REQUEST" with suggestion    |
      | negative_keywords_remove WITHOUT targeting_overlay.negative_keywords         | success                                    |
      | remove pair that exists in current list                                     | success                                    |
      | remove pair that does NOT exist in current list (no-op)                     | success                                    |

  @T-UC-026-partition-paused @partition @paused
  Scenario Outline: Paused behavior partition validation -- <partition>
    Given a package request with paused per <partition>
    When the Buyer Agent sends the request
    Then the outcome should be <outcome>

    Examples: Valid partitions
      | partition          | outcome                                    |
      | active_default     | success with paused=false (default)        |
      | explicitly_active  | success with paused=false                  |
      | explicitly_paused  | success with paused=true                   |
      | pause_on_update    | success with delivery suspended             |
      | resume_on_update   | success with delivery resumed               |

  @T-UC-026-boundary-paused @boundary @paused
  Scenario Outline: Paused behavior boundary validation -- <boundary_point>
    Given a package request with paused per boundary <boundary_point>
    When the Buyer Agent sends the request
    Then the outcome should be <outcome>

    Examples: Boundary values
      | boundary_point                                       | outcome                                    |
      | paused omitted on create (defaults to false)         | success                                    |
      | paused=false on create (explicitly active)           | success                                    |
      | paused=true on create (created paused)               | success                                    |
      | paused=true on update (pause running package)        | success                                    |
      | paused=false on update (resume paused package)       | success                                    |
      | paused=true on already-paused package (idempotent)   | success                                    |

  @T-UC-026-partition-replacement @partition @package_update_array_fields
  Scenario Outline: Update replacement semantics partition validation -- <partition>
    Given a package update request per replacement semantics <partition>
    When the Buyer Agent sends the update_media_buy request
    Then the outcome should be <outcome>

    Examples: Valid partitions
      | partition                      | outcome                                    |
      | replace_catalogs               | success with catalogs replaced             |
      | replace_optimization_goals     | success with goals replaced                |
      | replace_creative_assignments   | success with assignments replaced          |
      | omit_array_fields              | success with existing values preserved     |
      | replace_targeting_overlay      | success with overlay replaced              |

  @T-UC-026-boundary-replacement @boundary @package_update_array_fields
  Scenario Outline: Update replacement semantics boundary validation -- <boundary_point>
    Given a package update request per replacement boundary <boundary_point>
    When the Buyer Agent sends the update_media_buy request
    Then the outcome should be <outcome>

    Examples: Boundary values
      | boundary_point                                       | outcome                                    |
      | catalogs provided with one entry (replaces existing) | success                                    |
      | optimization_goals provided (replaces existing)      | success                                    |
      | creative_assignments provided (replaces existing)    | success                                    |
      | all array fields omitted (existing preserved)        | success                                    |
      | only scalar fields updated (patch semantics)         | success                                    |
      | targeting_overlay replacement (full swap)            | success                                    |

