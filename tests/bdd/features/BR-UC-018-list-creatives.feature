# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-018 List Creatives
  As a Buyer
  I want to query the Seller's creative library with filtering, sorting, pagination, and field projection
  So that I can discover, search, and evaluate creative assets for media buy decisions

  # Postconditions verified:
  #   POST-S1: Buyer knows which creatives match their filter criteria
  #   POST-S2: Buyer knows the total number of matching creatives and current page position
  #   POST-S3: Buyer knows the core attributes (ID, name, format, status, dates) for each returned creative
  #   POST-S4: Buyer knows which packages each creative is assigned to (when assignments requested)
  #   POST-S5: Buyer knows the aggregated performance metrics for each creative (when performance requested)
  #   POST-S6: Buyer knows the sub-assets for multi-format creatives (when sub-assets requested)
  #   POST-S7: Buyer knows which filters and sort order were applied to produce the results
  #   POST-F1: Buyer knows the operation failed
  #   POST-F2: Buyer knows what went wrong (error explains the failure)
  #   POST-F3: Buyer knows how to recover (suggestion for corrective action)
  #
  # Rules: BR-RULE-146 (defaults), BR-RULE-147 (pagination/sorting), BR-RULE-148 (filter semantics),
  #        BR-RULE-149 (field selector/error tolerance), BR-RULE-034 (cross-principal isolation)
  # Extensions: A (auth required), B (tenant unavailable), C (validation failure), D (invalid date)
  # Error codes: AUTHENTICATION_REQUIRED, TENANT_REQUIRED, VALIDATION_ERROR, DATE_INVALID_FORMAT

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And the Buyer is authenticated as principal "buyer-001"


  @T-UC-018-main @main-flow
  Scenario: List creatives -- default query returns non-archived creatives
    Given the authenticated principal has 5 creatives with statuses "approved", "processing", "rejected", "pending_review", "archived"
    When the Buyer Agent sends a list_creatives request with no parameters
    Then the response contains a creatives array with 4 items
    And the archived creative is not included in the results
    And each creative includes creative_id, name, format_id, status, created_date, updated_date
    And the query_summary shows total_matching as 4 and returned as 4
    And the query_summary shows sort_applied as "created_date desc"
    And the pagination shows has_more as false
    # BR-RULE-146 INV-1: No filters -> all non-archived creatives for principal
    # BR-RULE-147 INV-1: No pagination -> default page size 50
    # BR-RULE-147 INV-3: No sort -> created_date descending
    # POST-S1: Buyer knows which creatives match (4 non-archived)
    # POST-S2: Buyer knows total count and page position
    # POST-S3: Buyer knows core attributes
    # POST-S7: Buyer knows applied filters and sort

  @T-UC-018-main-enriched @main-flow
  Scenario: List creatives with assignments included by default
    Given the authenticated principal has 2 approved creatives with package assignments
    When the Buyer Agent sends a list_creatives request with no parameters
    Then the response contains a creatives array with 2 items
    And each creative includes assignment data
    # BR-RULE-149 INV-3: include_assignments defaults to true
    # POST-S4: Buyer knows package assignments (default included)

  @T-UC-018-main-performance @main-flow
  Scenario: List creatives with explicit performance data request
    Given the authenticated principal has 2 approved creatives with performance data
    When the Buyer Agent sends a list_creatives request with include_performance true
    Then each creative includes performance metrics
    # BR-RULE-149 INV-4: include_performance defaults to false, must explicitly request
    # POST-S5: Buyer knows performance metrics (when requested)

  @T-UC-018-main-subassets @main-flow
  Scenario: List creatives with explicit sub-assets request
    Given the authenticated principal has a multi-format creative with sub-assets
    When the Buyer Agent sends a list_creatives request with include_sub_assets true
    Then the creative includes sub_assets data
    # BR-RULE-149 INV-5: include_sub_assets defaults to false, must explicitly request
    # POST-S6: Buyer knows sub-assets (when requested)

  @T-UC-018-ext-a @extension @ext-a @error
  Scenario: Authentication required -- no credentials
    Given the Buyer has no authentication credentials
    When the Buyer Agent sends a list_creatives request
    Then the operation should fail with error code "AUTHENTICATION_REQUIRED"
    And the error message should contain "authentication"
    And the error should include a "suggestion" field
    And the suggestion should contain "valid authentication credentials"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains authentication is required
    # POST-F3: Suggestion advises providing valid credentials

  @T-UC-018-ext-b @extension @ext-b @error
  Scenario: Tenant unavailable -- identity has no tenant mapping
    Given no tenant can be resolved from the request context
    When the Buyer Agent sends a list_creatives request
    Then the operation should fail with error code "TENANT_REQUIRED"
    And the error message should contain "tenant"
    And the error should include a "suggestion" field
    And the suggestion should contain "valid tenant"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains tenant context could not be determined
    # POST-F3: Suggestion advises ensuring credentials map to a valid tenant

  @T-UC-018-ext-c @extension @ext-c @error
  Scenario Outline: Validation failure -- <description>
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a list_creatives request with <invalid_param>
    Then the operation should fail with error code "VALIDATION_ERROR"
    And the error message should contain "<error_detail>"
    And the error should include a "suggestion" field
    And the suggestion should contain "<suggestion_text>"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains which parameters are invalid
    # POST-F3: Suggestion provides valid parameter values

    Examples:
      | description                  | invalid_param                          | error_detail     | suggestion_text      |
      | invalid status enum          | statuses filter "unknown"              | status           | valid creative status |
      | empty statuses array         | statuses filter as empty array         | statuses         | at least 1           |
      | non-integer max_results      | max_results as "abc"                   | max_results      | integer              |
      | empty fields array           | fields as empty array                  | fields           | at least 1           |
      | unknown field enum           | fields containing "thumbnail"          | field            | valid field           |
      | empty tags array             | tags filter as empty array             | tags             | at least 1           |
      | creative_ids over max        | creative_ids with 101 items            | creative_ids     | at most 100          |
      | non-string field item        | fields containing integer 123          | field            | string               |

  @T-UC-018-ext-d @extension @ext-d @error
  Scenario Outline: Invalid date format -- <date_field> with value "<value>"
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a list_creatives request with <date_field> as "<value>"
    Then the operation should fail with error code "DATE_INVALID_FORMAT"
    And the error message should contain "<date_field>"
    And the error should include a "suggestion" field
    And the suggestion should contain "ISO 8601"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains which date field is invalid
    # POST-F3: Suggestion provides expected ISO 8601 format

    Examples:
      | date_field     | value         |
      | created_after  | not-a-date    |
      | created_after  | yesterday     |
      | created_before | 2024/01/15    |
      | created_before | Jan 15, 2024  |

  @T-UC-018-partition-default-query @partition @default-query-behavior
  Scenario Outline: Default query behavior -- <partition>
    Given the authenticated principal has creatives in statuses "approved", "processing", "archived", "pending_review", "rejected"
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Valid partitions
      | partition                   | request_params                                                                     | outcome                                                         |
      | empty_request               | no parameters                                                                      | 4 non-archived creatives are returned                            |
      | explicit_non_archived_status | statuses filter ["approved"]                                                       | only approved creatives are returned                             |
      | explicit_archived_status    | statuses filter ["archived"]                                                       | only archived creatives are returned                             |
      | mixed_statuses              | statuses filter ["approved", "archived"]                                           | both approved and archived creatives are returned                |
      | all_statuses_explicit       | statuses filter ["processing", "approved", "rejected", "pending_review", "archived"] | all 5 creatives including archived are returned                  |
      | filters_no_status           | name_contains filter "nike"                                                        | matching non-archived creatives are returned (archival exclusion applies) |

    Examples: Invalid partitions
      | partition             | request_params                      | outcome                                               |
      | invalid_status_enum   | statuses filter ["unknown"]         | error "VALIDATION_ERROR" with suggestion               |
      | empty_statuses_array  | statuses filter as empty array      | error "VALIDATION_ERROR" with suggestion               |

  @T-UC-018-boundary-default-query @boundary @default-query-behavior
  Scenario Outline: Default query behavior boundary -- <boundary_point>
    Given the authenticated principal has creatives in various statuses
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                        | request_params                                                                       | outcome                                                  |
      | Empty request (no parameters at all)                  | no parameters                                                                        | all non-archived creatives returned                       |
      | statuses=['archived'] — only archived creatives returned | statuses filter ["archived"]                                                         | only archived creatives returned                          |
      | statuses=[] — empty array violates minItems:1         | statuses filter as empty array                                                       | error "VALIDATION_ERROR" with suggestion                  |
      | filters={} — empty filters object, defaults apply     | empty filters object                                                                 | all non-archived creatives returned (defaults apply)      |
      | statuses=['unknown'] — invalid enum value             | statuses filter ["unknown"]                                                          | error "VALIDATION_ERROR" with suggestion                  |
      | All 5 statuses explicitly listed — includes archived  | statuses filter ["processing", "approved", "rejected", "pending_review", "archived"] | all 5 creatives including archived returned               |

  @T-UC-018-partition-pagination @partition @pagination-sorting
  Scenario Outline: Pagination and sorting -- <partition>
    Given the authenticated principal has 60 approved creatives
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Valid partitions
      | partition                      | request_params                               | outcome                                                       |
      | default_pagination             | no pagination params                         | 50 creatives returned (default page size)                      |
      | explicit_pagination            | max_results 20                               | 20 creatives returned                                          |
      | boundary_min_limit             | max_results 1                                | 1 creative returned                                            |
      | schema_max_limit               | max_results 100                              | 60 creatives returned (all available, below cap)                |
      | code_cap_limit                 | limit 1000                                   | 60 creatives returned (all available, below cap)                |
      | above_code_cap                 | limit 5000                                   | 60 creatives returned (capped to 1000, all available below cap) |
      | default_sort                   | no sort params                               | creatives sorted by created_date descending                     |
      | explicit_sort                  | sort_by "name" sort_order "asc"              | creatives sorted by name ascending                              |
      | invalid_sort_order_coercion    | sort_order "random"                          | creatives sorted by created_date descending (coerced)           |
      | invalid_sort_field_coercion    | sort_by "unknown_field"                      | creatives sorted by created_date descending (coerced)           |

    Examples: Invalid partitions
      | partition                | request_params                    | outcome                                     |
      | max_results_zero         | max_results 0                     | error "VALIDATION_ERROR" with suggestion     |
      | max_results_negative     | max_results -1                    | error "VALIDATION_ERROR" with suggestion     |
      | non_integer_max_results  | max_results "abc"                 | error "VALIDATION_ERROR" with suggestion     |

  @T-UC-018-boundary-pagination @boundary @pagination-sorting
  Scenario Outline: Pagination boundary -- <boundary_point>
    Given the authenticated principal has 60 approved creatives
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                    | request_params         | outcome                                                          |
      | max_results=0 (below schema min of 1)             | max_results 0          | error "VALIDATION_ERROR" with suggestion                          |
      | max_results=1 (schema minimum)                    | max_results 1          | 1 creative returned                                               |
      | max_results=100 (schema maximum)                  | max_results 100        | 60 creatives returned (all available)                              |
      | max_results=101 (above schema max, code allows)   | max_results 101        | 60 creatives returned (code allows beyond schema max)              |
      | limit=1000 (code cap)                             | limit 1000             | 60 creatives returned (all available)                              |
      | limit=1001 (above code cap, capped to 1000)       | limit 1001             | 60 creatives returned (capped to 1000, all available below cap)    |
      | sort_order='asc' (valid enum)                     | sort_order "asc"       | creatives sorted ascending                                        |
      | sort_order='desc' (valid enum, also the default)  | sort_order "desc"      | creatives sorted descending                                       |
      | sort_order='random' (invalid, coerced to desc)    | sort_order "random"    | creatives sorted descending (silently coerced)                     |
      | sort_by='performance_score' (valid enum boundary) | sort_by "performance_score" | creatives sorted by performance_score                         |
      | sort_by='unknown_field' (invalid, coerced to created_date) | sort_by "unknown_field" | creatives sorted by created_date (silently coerced)       |

  @T-UC-018-partition-filters @partition @filter-semantics
  Scenario Outline: Filter semantics -- <partition>
    Given the authenticated principal has creatives with various tags, statuses, and media buy associations
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Valid partitions
      | partition                        | request_params                                                                     | outcome                                                              |
      | no_filters                       | no filter parameters                                                               | all non-archived creatives returned                                   |
      | flat_only                        | flat status "approved" and format "video"                                          | only approved video creatives returned                                |
      | structured_only                  | structured filters with statuses ["approved"] and name_contains "nike"             | only approved creatives matching "nike" returned                      |
      | flat_and_structured_no_conflict  | flat tags ["q1"] and structured name_contains "nike"                               | creatives matching both tag "q1" AND name "nike" returned             |
      | flat_and_structured_conflict     | flat status "approved" and structured statuses ["rejected"]                        | approved creatives returned (flat param takes precedence)             |
      | singular_to_plural_merge         | singular media_buy_id "mb1" and plural media_buy_ids ["mb2"]                       | creatives for both mb1 and mb2 returned (merged, deduplicated)        |
      | tags_and_semantics               | tags filter ["q1", "brand"]                                                        | only creatives with BOTH q1 AND brand tags returned                   |
      | tags_or_semantics                | tags_any filter ["q1", "brand"]                                                    | creatives with EITHER q1 OR brand tag returned                        |
      | combined_date_range              | created_after "2024-01-01T00:00:00Z" and created_before "2024-06-30T23:59:59Z"    | only creatives within date range returned                             |

    Examples: Invalid partitions
      | partition                | request_params                             | outcome                                            |
      | invalid_date_format      | created_after "not-a-date"                 | error "DATE_INVALID_FORMAT" with suggestion          |
      | empty_tags_array         | tags filter as empty array                 | error "VALIDATION_ERROR" with suggestion             |
      | creative_ids_over_limit  | creative_ids with 101 items                | error "VALIDATION_ERROR" with suggestion             |

  @T-UC-018-boundary-filters @boundary @filter-semantics
  Scenario Outline: Filter semantics boundary -- <boundary_point>
    Given the authenticated principal has creatives with various tags, media buy associations, and creation dates
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                                       | request_params                                                               | outcome                                                               |
      | tags=['single_tag'] (minimum AND match)                              | tags filter ["single_tag"]                                                   | creatives with tag "single_tag" returned                               |
      | tags_any=['single_tag'] (minimum OR match)                           | tags_any filter ["single_tag"]                                               | creatives with tag "single_tag" returned                               |
      | creative_ids with 100 items (maxItems boundary)                      | creative_ids with exactly 100 items                                          | creatives matching those IDs returned                                  |
      | creative_ids with 101 items (above maxItems)                         | creative_ids with 101 items                                                  | error "VALIDATION_ERROR" with suggestion                               |
      | Flat status='approved' + structured statuses=['rejected'] (conflict) | flat status "approved" and structured statuses ["rejected"]                  | approved creatives returned (flat wins)                                |
      | media_buy_id='mb1' + media_buy_ids=['mb1'] (duplicate, deduplicated) | singular media_buy_id "mb1" and plural media_buy_ids ["mb1"]                 | creatives for mb1 returned (deduplicated, no duplicate results)        |
      | created_after='2024-01-01T00:00:00Z' (valid ISO 8601)               | created_after "2024-01-01T00:00:00Z"                                         | creatives created after the date returned                              |
      | created_after='yesterday' (invalid date format)                      | created_after "yesterday"                                                    | error "DATE_INVALID_FORMAT" with suggestion                            |

  @T-UC-018-partition-field-selector @partition @field-selector
  Scenario Outline: Field selector -- <partition>
    Given the authenticated principal has 3 approved creatives with full data
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Valid partitions
      | partition                    | request_params                                                                                      | outcome                                                        |
      | omitted                      | no fields parameter                                                                                 | all fields included in each creative object                     |
      | single_field                 | fields ["creative_id"]                                                                              | only creative_id field in each creative object                  |
      | minimal_set                  | fields ["creative_id", "name", "status"]                                                            | only creative_id, name, status in each creative object          |
      | all_fields                   | fields with all 10 enum values                                                                      | all 10 fields included in each creative object                  |
      | enrichment_fields            | fields ["creative_id", "assignments", "performance"] and include_performance true                   | creative_id, assignments, and performance data included          |
      | assignments_disabled         | include_assignments false                                                                           | assignment data excluded from creatives                          |
      | invalid_db_status_tolerance  | database has creative with unrecognized status value                                                | creative returned with status mapped to "pending_review"         |

    Examples: Invalid partitions
      | partition         | request_params                      | outcome                                         |
      | empty_array       | fields as empty array               | error "VALIDATION_ERROR" with suggestion         |
      | unknown_field     | fields ["creative_id", "thumbnail"] | error "VALIDATION_ERROR" with suggestion         |
      | non_string_item   | fields containing integer 123       | error "VALIDATION_ERROR" with suggestion         |

  @T-UC-018-boundary-field-selector @boundary @field-selector
  Scenario Outline: Field selector boundary -- <boundary_point>
    Given the authenticated principal has creatives with full data
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                            | request_params                                  | outcome                                                  |
      | ['creative_id'] (single field, minItems boundary)         | fields ["creative_id"]                          | only creative_id field in response                        |
      | All 10 enum values (max enum coverage)                    | fields with all 10 enum values                  | all 10 fields included                                    |
      | [] (empty array, violates minItems: 1)                    | fields as empty array                           | error "VALIDATION_ERROR" with suggestion                  |
      | ['creative_id', 'thumbnail'] (unknown enum value)         | fields ["creative_id", "thumbnail"]             | error "VALIDATION_ERROR" with suggestion                  |
      | fields omitted entirely (all fields returned)             | no fields parameter                             | all fields included in response                            |
      | include_assignments=false (overrides default true)         | include_assignments false                       | assignment data excluded                                   |
      | DB status='unknown_value' (mapped to pending_review)      | database has creative with unrecognized status  | status mapped to "pending_review" in response              |

  @T-UC-018-inv-146-1-holds @invariant @BR-RULE-146
  Scenario: BR-RULE-146 INV-1 holds -- no filters returns all non-archived creatives
    Given the authenticated principal has 3 approved and 1 archived creative
    When the Buyer Agent sends a list_creatives request with no filters
    Then the response contains 3 creatives
    And none of the returned creatives have status "archived"

  @T-UC-018-inv-146-2-holds @invariant @BR-RULE-146
  Scenario: BR-RULE-146 INV-2 holds -- explicit archived status includes archived creatives
    Given the authenticated principal has 3 approved and 2 archived creatives
    When the Buyer Agent sends a list_creatives request with statuses filter ["archived"]
    Then the response contains 2 creatives
    And all returned creatives have status "archived"

  @T-UC-018-inv-146-2-violated @invariant @BR-RULE-146
  Scenario: BR-RULE-146 INV-2 violated -- archived status NOT in filter excludes archived
    Given the authenticated principal has 3 approved and 2 archived creatives
    When the Buyer Agent sends a list_creatives request with statuses filter ["approved"]
    Then the response contains 3 creatives
    And none of the returned creatives have status "archived"
    # Counter-example: not specifying archived means archived are excluded

  @T-UC-018-inv-146-3-holds @invariant @BR-RULE-146
  Scenario: BR-RULE-146 INV-3 holds -- statuses filter without archived excludes archived
    Given the authenticated principal has 2 approved, 1 rejected, and 1 archived creative
    When the Buyer Agent sends a list_creatives request with statuses filter ["approved", "rejected"]
    Then the response contains 3 creatives
    And none of the returned creatives have status "archived"

  @T-UC-018-inv-147-1-holds @invariant @BR-RULE-147
  Scenario: BR-RULE-147 INV-1 holds -- no pagination uses default page size 50
    Given the authenticated principal has 60 approved creatives
    When the Buyer Agent sends a list_creatives request with no pagination params
    Then the response contains 50 creatives
    And pagination shows has_more as true

  @T-UC-018-inv-147-2-holds @invariant @BR-RULE-147
  Scenario: BR-RULE-147 INV-2 holds -- limit exceeding 1000 is capped
    Given the authenticated principal has 60 approved creatives
    When the Buyer Agent sends a list_creatives request with limit 5000
    Then the effective page size is at most 1000
    And the response does not contain more than 1000 creatives

  @T-UC-018-inv-147-3-holds @invariant @BR-RULE-147
  Scenario: BR-RULE-147 INV-3 holds -- no sort defaults to created_date descending
    Given the authenticated principal has creatives created on different dates
    When the Buyer Agent sends a list_creatives request with no sort params
    Then the creatives are ordered by created_date descending
    And the query_summary shows sort_applied as "created_date desc"

  @T-UC-018-inv-147-4-holds @invariant @BR-RULE-147
  Scenario: BR-RULE-147 INV-4 holds -- invalid sort_order coerced to desc
    Given the authenticated principal has creatives created on different dates
    When the Buyer Agent sends a list_creatives request with sort_order "random"
    Then the creatives are ordered descending (default coercion)
    And no error is returned

  @T-UC-018-inv-147-5-holds @invariant @BR-RULE-147
  Scenario: BR-RULE-147 INV-5 holds -- invalid sort_by coerced to created_date
    Given the authenticated principal has creatives created on different dates
    When the Buyer Agent sends a list_creatives request with sort_by "unknown_field"
    Then the creatives are ordered by created_date (default coercion)
    And no error is returned

  @T-UC-018-inv-148-1-holds @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-1 holds -- flat params take precedence over structured on conflict
    Given the authenticated principal has 3 approved and 2 rejected creatives
    When the Buyer Agent sends a list_creatives request with flat status "approved" and structured statuses ["rejected"]
    Then the response contains 3 creatives
    And all returned creatives have status "approved"

  @T-UC-018-inv-148-1-violated @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-1 context -- no conflict when only structured filters used
    Given the authenticated principal has 3 approved and 2 rejected creatives
    When the Buyer Agent sends a list_creatives request with structured statuses ["rejected"]
    Then the response contains 2 creatives
    And all returned creatives have status "rejected"
    # When there is no flat param conflict, structured filters are used as-is

  @T-UC-018-inv-148-2-holds @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-2 holds -- tags filter uses AND semantics
    Given the authenticated principal has a creative with tags ["q1", "brand"] and a creative with tags ["q1"]
    When the Buyer Agent sends a list_creatives request with tags filter ["q1", "brand"]
    Then the response contains 1 creative
    And the returned creative has both tags "q1" and "brand"

  @T-UC-018-inv-148-2-violated @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-2 counter -- creative with only one tag excluded by AND filter
    Given the authenticated principal has a creative with tags ["q1"] only
    When the Buyer Agent sends a list_creatives request with tags filter ["q1", "brand"]
    Then the creative with only tag "q1" is not returned

  @T-UC-018-inv-148-3-holds @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-3 holds -- tags_any filter uses OR semantics
    Given the authenticated principal has a creative with tag "q1" and a creative with tag "brand"
    When the Buyer Agent sends a list_creatives request with tags_any filter ["q1", "brand"]
    Then the response contains 2 creatives

  @T-UC-018-inv-148-4-holds @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-4 holds -- singular media_buy_id merged into plural array
    Given the authenticated principal has creatives associated with media buys "mb1" and "mb2"
    When the Buyer Agent sends a list_creatives request with media_buy_id "mb1" and media_buy_ids ["mb2"]
    Then the response contains creatives from both "mb1" and "mb2"

  @T-UC-018-inv-148-5-holds @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-5 holds -- singular buyer_ref merged into plural array
    Given the authenticated principal has creatives with buyer refs "ref1" and "ref2"
    When the Buyer Agent sends a list_creatives request with buyer_ref "ref1" and buyer_refs ["ref2"]
    Then the response contains creatives from both "ref1" and "ref2"

  @T-UC-018-inv-148-6-holds @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-6 holds -- invalid date format raises validation error
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a list_creatives request with created_after "not-a-date"
    Then the operation should fail with error code "DATE_INVALID_FORMAT"
    And the error should include a "suggestion" field
    And the suggestion should contain "ISO 8601"
    # POST-F3: Suggestion for recovery

  @T-UC-018-inv-148-6-violated @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-6 counter -- valid ISO 8601 date accepted
    Given the authenticated principal has creatives created in 2024
    When the Buyer Agent sends a list_creatives request with created_after "2024-01-01T00:00:00Z"
    Then the operation succeeds
    And the response contains creatives created after the specified date

  @T-UC-018-inv-149-1-holds @invariant @BR-RULE-149
  Scenario: BR-RULE-149 INV-1 holds -- fields array projects response
    Given the authenticated principal has 2 approved creatives with full data
    When the Buyer Agent sends a list_creatives request with fields ["creative_id", "name"]
    Then each creative in the response contains only "creative_id" and "name" fields

  @T-UC-018-inv-149-2-holds @invariant @BR-RULE-149
  Scenario: BR-RULE-149 INV-2 holds -- fields omitted returns all fields
    Given the authenticated principal has 2 approved creatives with full data
    When the Buyer Agent sends a list_creatives request with no fields parameter
    Then each creative in the response contains all available fields

  @T-UC-018-inv-149-3-holds @invariant @BR-RULE-149
  Scenario: BR-RULE-149 INV-3 holds -- include_assignments defaults to true
    Given the authenticated principal has an approved creative with package assignments
    When the Buyer Agent sends a list_creatives request without specifying include_assignments
    Then the creative in the response includes assignment data

  @T-UC-018-inv-149-4-holds @invariant @BR-RULE-149
  Scenario: BR-RULE-149 INV-4 holds -- include_performance defaults to false
    Given the authenticated principal has an approved creative with performance data
    When the Buyer Agent sends a list_creatives request without specifying include_performance
    Then the creative in the response does not include performance data

  @T-UC-018-inv-149-5-holds @invariant @BR-RULE-149
  Scenario: BR-RULE-149 INV-5 holds -- include_sub_assets defaults to false
    Given the authenticated principal has a multi-format creative with sub-assets
    When the Buyer Agent sends a list_creatives request without specifying include_sub_assets
    Then the creative in the response does not include sub_assets data

  @T-UC-018-inv-149-6-holds @invariant @BR-RULE-149
  Scenario: BR-RULE-149 INV-6 holds -- unrecognized DB status mapped to pending_review
    Given the authenticated principal has a creative with database status "draft" (not in protocol enum)
    When the Buyer Agent sends a list_creatives request
    Then the creative is returned with status "pending_review"
    And no error is raised

  @T-UC-018-inv-034-1-holds @invariant @BR-RULE-034
  Scenario: BR-RULE-034 INV-1 holds -- query always scoped by principal
    Given principal "buyer-001" has 3 creatives
    And principal "buyer-002" has 5 creatives in the same tenant
    When the Buyer Agent authenticated as "buyer-001" sends a list_creatives request
    Then the response contains exactly 3 creatives
    And all creatives belong to principal "buyer-001"

  @T-UC-018-inv-034-1-violated @invariant @BR-RULE-034
  Scenario: BR-RULE-034 INV-1 counter -- cross-principal creatives never visible
    Given principal "buyer-001" has 3 creatives
    And principal "buyer-002" has 5 creatives in the same tenant
    When the Buyer Agent authenticated as "buyer-001" sends a list_creatives request
    Then none of the returned creatives belong to principal "buyer-002"

  @T-UC-018-edge-empty-library @main-flow @edge-case
  Scenario: Empty creative library returns empty array not error
    Given the authenticated principal has no creatives
    When the Buyer Agent sends a list_creatives request
    Then the response contains a creatives array with 0 items
    And the query_summary shows total_matching as 0
    And the pagination shows has_more as false
    And the response is not an error
    # POST-S1: Buyer knows no creatives match (empty result)
    # POST-S2: Buyer knows total is 0

  @T-UC-018-edge-pagination-next @main-flow @edge-case
  Scenario: Pagination cursor traversal across pages
    Given the authenticated principal has 120 approved creatives
    When the Buyer Agent sends a list_creatives request with max_results 50
    Then the response contains 50 creatives
    And the pagination shows has_more as true
    And the pagination includes a cursor for the next page
    When the Buyer Agent sends a list_creatives request with the cursor from the previous response
    Then the response contains 50 creatives from the second page
    And the creatives do not overlap with the first page results

  @T-UC-018-edge-duplicate-dedup @invariant @BR-RULE-148 @edge-case
  Scenario: Singular media_buy_id duplicate in plural array is deduplicated
    Given the authenticated principal has a creative associated with media buy "mb1"
    When the Buyer Agent sends a list_creatives request with media_buy_id "mb1" and media_buy_ids ["mb1"]
    Then the filter resolves to media_buy_ids ["mb1"] (deduplicated)
    And the creative for "mb1" is returned exactly once

  @T-UC-018-edge-valid-date @main-flow @edge-case
  Scenario: Valid ISO 8601 date with timezone offset accepted
    Given the authenticated principal has creatives created in 2024
    When the Buyer Agent sends a list_creatives request with created_after "2024-01-15T00:00:00+05:00"
    Then the operation succeeds
    And the response contains creatives created after the specified timestamp

  @T-UC-018-partition-legacy-fields @partition @list-creatives-fields
  Scenario Outline: List creatives fields partition -- <partition>
    Given the authenticated principal has creatives with full data
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Valid partitions
      | partition          | request_params                                         | outcome                                         |
      | minimal_fields     | fields ["creative_id", "name", "status"]               | only creative_id, name, status in response       |
      | all_enum_values    | fields with all 10 enum values                         | all 10 fields included in response               |

  @T-UC-018-partition-sort-field @partition @creative-sort-field
  Scenario Outline: Creative sort field partition -- <partition>
    Given the authenticated principal has approved creatives
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Valid partitions
      | partition          | request_params                  | outcome                                    |
      | updated_date       | sort_by "updated_date"          | creatives sorted by updated_date           |
      | assignment_count   | sort_by "assignment_count"      | creatives sorted by assignment_count       |
      | performance_score  | sort_by "performance_score"     | creatives sorted by performance_score      |

    Examples: Invalid partitions
      | partition          | request_params                  | outcome                                          |
      | unknown_value      | sort_by "format"                | creatives sorted by created_date (coerced)        |

  @T-UC-018-boundary-legacy-fields @boundary @list-creatives-fields
  Scenario Outline: List creatives fields boundary -- <boundary_point>
    Given the authenticated principal has creatives with full data
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                                                                                                          | request_params                                | outcome                                   |
      | ["creative_id"] (single field, minimum valid)                                                                                           | fields ["creative_id"]                        | only creative_id returned                  |
      | ["creative_id", "name", "format", "status", "created_date", "updated_date", "tags", "assignments", "performance", "sub_assets"] (all 10 fields) | fields with all 10 enum values                | all 10 fields returned                     |
      | Not provided (all fields returned)                                                                                                      | no fields parameter                           | all fields returned                        |
      | ["creative_id", "thumbnail"] (unknown field in array)                                                                                   | fields ["creative_id", "thumbnail"]           | error "VALIDATION_ERROR" with suggestion   |
      | [] (empty array, violates minItems)                                                                                                     | fields as empty array                         | error "VALIDATION_ERROR" with suggestion   |

  @T-UC-018-boundary-sort-field @boundary @creative-sort-field
  Scenario Outline: Creative sort field boundary -- <boundary_point>
    Given the authenticated principal has approved creatives
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                              | request_params                  | outcome                                         |
      | created_date (first enum value, also default) | sort_by "created_date"          | creatives sorted by created_date                 |
      | performance_score (last enum value)         | sort_by "performance_score"     | creatives sorted by performance_score            |
      | Not provided (defaults to created_date)     | no sort params                  | creatives sorted by created_date (default)       |
      | format (not in enum)                        | sort_by "format"                | creatives sorted by created_date (coerced)       |

  @T-UC-018-sandbox-happy @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account list_creatives returns simulated results with sandbox flag
    Given the authenticated principal has creatives in a sandbox account
    And the request targets a sandbox account
    When the Buyer Agent sends a list_creatives request
    Then the response should contain "creatives" array
    And the response should include sandbox equals true
    And no real ad platform API calls should have been made
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-2: real ad platform calls suppressed
    # BR-RULE-209 INV-4: response includes sandbox: true

  @T-UC-018-sandbox-production @invariant @br-rule-209 @sandbox
  Scenario: Production account list_creatives response does not include sandbox flag
    Given the authenticated principal has creatives in a production account
    And the request targets a production account
    When the Buyer Agent sends a list_creatives request
    Then the response should contain "creatives" array
    And the response should not include a sandbox field
    # BR-RULE-209 INV-5: production account -> sandbox absent

  @T-UC-018-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account with invalid filter returns real validation error
    Given the request targets a sandbox account
    When the Buyer Agent sends a list_creatives request with invalid status filter
    Then the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

