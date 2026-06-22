# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

Feature: BR-UC-018 List Creatives
  As a Buyer
  I want to query the Seller's creative library with filtering, sorting, pagination, and field projection
  So that I can discover, search, and evaluate creative assets for media buy decisions

  # Postconditions verified:
  #   POST-S1: Buyer knows which creatives match their filter criteria
  #   POST-S2: Buyer knows the total number of matching creatives and current page position
  #   POST-S3: Buyer knows the core attributes (ID, name, format_id, status, dates) for each returned creative
  #   POST-S4: Buyer knows which packages each creative is assigned to (when assignments requested)
  #   POST-S5: Buyer knows the lightweight delivery snapshot for each creative (when include_snapshot requested), or a snapshot_unavailable_reason
  #   POST-S6: Buyer knows the items for multi-asset creatives (when include_items requested)
  #   POST-S7: Buyer knows which filters and sort order were applied to produce the results
  #   POST-S8: Buyer knows the dynamic-content variables / DCO slots for each creative (when include_variables requested)
  #   POST-S9: Buyer knows the pricing options for each creative (when include_pricing requested and an account is provided)
  #   POST-F1: Buyer knows the operation failed
  #   POST-F2: Buyer knows what went wrong (error explains the failure)
  #   POST-F3: Buyer knows how to recover (suggestion for corrective action)
  #
  # Rules: BR-RULE-146 (defaults), BR-RULE-147 (pagination/sorting), BR-RULE-148 (filter semantics),
  #        BR-RULE-149 (field selector/error tolerance), BR-RULE-034 (cross-principal isolation),
  #        BR-RULE-209 (sandbox semantics), BR-RULE-225 (pricing disclosure gate), BR-RULE-226 (snapshot unavailability)
  # Extensions: A (auth required), B (tenant unavailable), C (validation failure), D (invalid date), E (snapshot unavailable)
  # Error codes: AUTHENTICATION_REQUIRED, TENANT_REQUIRED, VALIDATION_ERROR, DATE_INVALID_FORMAT, ACCOUNT_REQUIRED

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
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-main-enriched @main-flow
  Scenario: List creatives with assignments included by default
    Given the authenticated principal has 2 approved creatives with package assignments
    When the Buyer Agent sends a list_creatives request with no parameters
    Then the response contains a creatives array with 2 items
    And each creative includes assignment data
    # BR-RULE-149 INV-3: include_assignments defaults to true
    # POST-S4: Buyer knows package assignments (default included)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-main-performance @main-flow
  Scenario: List creatives with explicit delivery snapshot request
    Given the authenticated principal has 2 approved creatives with delivery snapshot data
    When the Buyer Agent sends a list_creatives request with include_snapshot true
    Then each creative includes a delivery snapshot
    # BR-RULE-149 INV-4: include_snapshot defaults to false, must explicitly request
    # POST-S5: Buyer knows the lightweight delivery snapshot (when requested)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-main-subassets @main-flow
  Scenario: List creatives with explicit items request
    Given the authenticated principal has a multi-asset creative with items
    When the Buyer Agent sends a list_creatives request with include_items true
    Then the creative includes items data
    # BR-RULE-149 INV-5: include_items defaults to false, must explicitly request
    # POST-S6: Buyer knows the items for multi-asset creatives (when requested)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-main-variables @main-flow
  Scenario: List creatives with explicit variables request
    Given the authenticated principal has a creative with dynamic-content variables
    When the Buyer Agent sends a list_creatives request with include_variables true
    Then the creative includes variables data
    # BR-RULE-149 INV-7: include_variables defaults to false, must explicitly request
    # POST-S8: Buyer knows dynamic-content variables / DCO slots (when requested)

  @T-UC-018-ext-a @extension @ext-a @error
  Scenario: Authentication required -- no credentials
    Given the Buyer has no authentication credentials
    When the Buyer Agent sends a list_creatives request
    Then the operation should fail with error code "AUTH_REQUIRED"
    And the error code should be "AUTH_REQUIRED"
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
    Then the operation should fail with error code "AUTH_REQUIRED"
    And the error code should be "AUTH_REQUIRED"
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
    And the error code should be "VALIDATION_ERROR"
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
    Then the operation should fail with error code "VALIDATION_ERROR"
    And the error code should be "VALIDATION_ERROR"
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
      | sort_by='assignment_count' (valid enum boundary; v3.1 highest-index sort field) | sort_by "assignment_count" | creatives sorted by assignment_count                 |
      | sort_by='unknown_field' (invalid, coerced to created_date) | sort_by "unknown_field" | creatives sorted by created_date (silently coerced)       |

  @T-UC-018-partition-filters @partition @filter-semantics
  Scenario Outline: Filter semantics -- <partition>
    Given the authenticated principal has creatives with various tags, statuses, and media buy associations
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Valid partitions
      | partition                        | request_params                                                                     | outcome                                                              |
      | no_filters                       | no filter parameters                                                               | all non-archived creatives returned                                   |
      | flat_only                        | flat status "approved"                                                             | only approved creatives returned                                      |
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
      | all_fields                   | fields with all 13 enum values                                                                      | all 13 fields included in each creative object                  |
      | enrichment_fields            | fields ["creative_id", "assignments", "snapshot"] and include_snapshot true                         | creative_id, assignments, and delivery snapshot included         |
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
      | All 13 enum values (max enum coverage)                    | fields with all 13 enum values                  | all 13 fields included                                    |
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

  @T-UC-018-inv-148-6-holds @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-6 holds -- invalid date format raises validation error
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a list_creatives request with created_after "not-a-date"
    Then the operation should fail with error code "VALIDATION_ERROR"
    And the error code should be "VALIDATION_ERROR"
    And the error should include a "suggestion" field
    And the suggestion should contain "ISO 8601"
    # POST-F3: Suggestion for recovery
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

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
  Scenario: BR-RULE-149 INV-4 holds -- include_snapshot defaults to false
    Given the authenticated principal has an approved creative with delivery snapshot data
    When the Buyer Agent sends a list_creatives request without specifying include_snapshot
    Then the creative in the response does not include a delivery snapshot
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-inv-149-5-holds @invariant @BR-RULE-149
  Scenario: BR-RULE-149 INV-5 holds -- include_items defaults to false
    Given the authenticated principal has a multi-asset creative with items
    When the Buyer Agent sends a list_creatives request without specifying include_items
    Then the creative in the response does not include items data
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-inv-149-7-holds @invariant @BR-RULE-149
  Scenario: BR-RULE-149 INV-7 holds -- include_variables defaults to false
    Given the authenticated principal has an approved creative with dynamic-content variables
    When the Buyer Agent sends a list_creatives request without specifying include_variables
    Then the creative in the response does not include variables data
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

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
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

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
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

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
      | all_enum_values    | fields with all 13 enum values                         | all 13 fields included in response               |

  @T-UC-018-partition-sort-field @partition @creative-sort-field
  Scenario Outline: Creative sort field partition -- <partition>
    Given the authenticated principal has approved creatives
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Valid partitions
      | partition          | request_params                  | outcome                                    |
      | updated_date       | sort_by "updated_date"          | creatives sorted by updated_date           |
      | assignment_count   | sort_by "assignment_count"      | creatives sorted by assignment_count       |

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
      | ["creative_id", "name", "format_id", "status", "created_date", "updated_date", "tags", "assignments", "snapshot", "items", "variables", "concept", "pricing_options"] (all 13 fields) | fields with all 13 enum values                | all 13 fields returned                     |
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
      | assignment_count (last enum value, v3.1 highest-index sort field) | sort_by "assignment_count" | creatives sorted by assignment_count   |
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

  @T-UC-018-inv-225-1-holds @invariant @BR-RULE-225 @error
  Scenario: BR-RULE-225 INV-1 holds -- include_pricing without account is rejected
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a list_creatives request with include_pricing true and no account reference
    Then the operation should fail with error code "VALIDATION_ERROR"
    And the error code should be "VALIDATION_ERROR"
    And the error should include a "suggestion" field
    And the suggestion should contain "account"
    # POST-F1, POST-F2, POST-F3
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-inv-225-2-holds @invariant @BR-RULE-225
  Scenario: BR-RULE-225 INV-2 holds -- include_pricing with account returns pricing_options
    Given the authenticated principal has 2 approved creatives
    And the request supplies an account reference resolvable to a rate card
    When the Buyer Agent sends a list_creatives request with include_pricing true and the account reference
    Then each creative in the response carries a pricing_options array with at least one option
    # POST-S9: Buyer knows pricing options (when include_pricing and account provided)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-inv-225-3-holds @invariant @BR-RULE-225
  Scenario: BR-RULE-225 INV-3 holds -- pricing_options absent when include_pricing omitted
    Given the authenticated principal has 2 approved creatives
    When the Buyer Agent sends a list_creatives request without specifying include_pricing
    Then no creative in the response includes a pricing_options field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-partition-pricing-include @partition @pricing-include
  Scenario Outline: Pricing disclosure gate -- <partition>
    Given the authenticated principal has approved creatives
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

    Examples: Valid partitions
      | partition                        | request_params                                              | outcome                               |
      | pricing_omitted                  | no include_pricing parameter                                | no pricing_options in any creative    |
      | pricing_false                    | include_pricing false                                       | no pricing_options in any creative    |
      | pricing_with_account             | include_pricing true and account account_id "acct_acme"     | each creative carries pricing_options |
      | pricing_with_natural_key_account | include_pricing true and account brand+operator natural key | each creative carries pricing_options |

    Examples: Invalid partitions
      | partition               | request_params                      | outcome                                    |
      | pricing_without_account | include_pricing true and no account | error "ACCOUNT_REQUIRED" with suggestion   |

  @T-UC-018-boundary-pricing-include @boundary @pricing-include
  Scenario Outline: Pricing disclosure gate boundary -- <boundary_point>
    Given the authenticated principal has approved creatives
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                              | request_params                                          | outcome                                  |
      | include_pricing=true + account present (gate satisfied)     | include_pricing true and account account_id "acct_acme" | each creative carries pricing_options    |
      | include_pricing=true + account absent (gate violated)       | include_pricing true and no account                     | error "ACCOUNT_REQUIRED" with suggestion |
      | include_pricing=false (no account needed)                   | include_pricing false                                   | no pricing_options in any creative       |
      | include_pricing omitted (defaults false, no account needed) | no include_pricing parameter                            | no pricing_options in any creative       |

  @T-UC-018-inv-226-1-holds @invariant @BR-RULE-226
  Scenario: BR-RULE-226 INV-1 holds -- snapshot returned when available
    Given the authenticated principal has an approved creative with available delivery snapshot data
    When the Buyer Agent sends a list_creatives request with include_snapshot true
    Then the creative in the response includes a delivery snapshot
    And the creative does not include a snapshot_unavailable_reason
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-inv-226-2-holds @invariant @BR-RULE-226
  Scenario Outline: BR-RULE-226 INV-2 holds -- snapshot unavailable surfaces machine-readable reason -- <reason>
    Given the authenticated principal has an approved creative whose snapshot is unavailable due to <condition>
    When the Buyer Agent sends a list_creatives request with include_snapshot true
    Then the creative in the response omits the snapshot
    And the creative includes a snapshot_unavailable_reason of "<reason>"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

    Examples:
      | condition                             | reason                            |
      | the platform never supports snapshots | SNAPSHOT_UNSUPPORTED              |
      | a transient data gap                  | SNAPSHOT_TEMPORARILY_UNAVAILABLE  |
      | an access restriction                 | SNAPSHOT_PERMISSION_DENIED        |

  @T-UC-018-inv-226-3-holds @invariant @BR-RULE-226
  Scenario: BR-RULE-226 INV-3 holds -- no snapshot fields when include_snapshot omitted
    Given the authenticated principal has an approved creative whose snapshot is unavailable
    When the Buyer Agent sends a list_creatives request without specifying include_snapshot
    Then the creative in the response includes neither a snapshot nor a snapshot_unavailable_reason
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-partition-snapshot-unavailable @partition @snapshot-unavailable
  Scenario Outline: Snapshot unavailability disclosure -- <partition>
    Given the authenticated principal has an approved creative
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

    Examples: Valid partitions
      | partition              | request_params                              | outcome                                                                    |
      | snapshot_returned      | include_snapshot true, snapshot available   | creative includes snapshot, no snapshot_unavailable_reason                  |
      | reason_unsupported     | include_snapshot true, platform unsupported | creative has snapshot_unavailable_reason "SNAPSHOT_UNSUPPORTED"             |
      | reason_temporary       | include_snapshot true, transient gap        | creative has snapshot_unavailable_reason "SNAPSHOT_TEMPORARILY_UNAVAILABLE" |
      | reason_permission      | include_snapshot true, no permission        | creative has snapshot_unavailable_reason "SNAPSHOT_PERMISSION_DENIED"       |
      | snapshot_not_requested | no include_snapshot parameter               | neither snapshot nor snapshot_unavailable_reason present                    |

    Examples: Invalid partitions
      | partition          | request_params                                         | outcome                                  |
      | reason_not_in_enum | snapshot_unavailable_reason "SNAPSHOT_BROKEN" returned  | error "VALIDATION_ERROR" with suggestion |

  @T-UC-018-boundary-snapshot-unavailable @boundary @snapshot-unavailable
  Scenario Outline: Snapshot unavailability boundary -- <boundary_point>
    Given the authenticated principal has an approved creative
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                                       | request_params                                | outcome                                                  |
      | snapshot_unavailable_reason='SNAPSHOT_UNSUPPORTED' (valid enum)       | include_snapshot true, platform unsupported   | snapshot_unavailable_reason is "SNAPSHOT_UNSUPPORTED"     |
      | snapshot_unavailable_reason='SNAPSHOT_PERMISSION_DENIED' (valid enum) | include_snapshot true, no permission          | snapshot_unavailable_reason is "SNAPSHOT_PERMISSION_DENIED" |
      | snapshot_unavailable_reason='SNAPSHOT_BROKEN' (not in enum)           | snapshot_unavailable_reason "SNAPSHOT_BROKEN"  | error "VALIDATION_ERROR" with suggestion                 |
      | include_snapshot omitted (neither snapshot nor reason present)        | no include_snapshot parameter                 | neither snapshot nor snapshot_unavailable_reason present  |

  @T-UC-018-ext-e @extension @ext-e @degradation
  Scenario Outline: Snapshot unavailable -- listing still succeeds with reason -- <reason>
    Given the authenticated principal has approved creatives
    And the delivery snapshot is unavailable for one creative due to <condition>
    When the Buyer Agent sends a list_creatives request with include_snapshot true
    Then the operation succeeds and returns the full creatives array
    And the affected creative carries a snapshot_unavailable_reason of "<reason>"
    And creatives with available snapshots still include their snapshot
    # POST-S5: degraded result is explained, not silently dropped

    Examples:
      | condition                             | reason                            |
      | the platform never supports snapshots | SNAPSHOT_UNSUPPORTED              |
      | a transient data gap                  | SNAPSHOT_TEMPORARILY_UNAVAILABLE  |
      | an access restriction                 | SNAPSHOT_PERMISSION_DENIED        |

  @T-UC-018-storyboard-list-all-creatives-after-sync @storyboard-v3.1 @v3-1 @list-after-sync
  Scenario: List creatives with no filters returns the library including recently synced creatives
    Given the buyer recently synced three creatives in three different formats via sync_creatives
    When the Buyer Agent sends list_creatives with no filters for the same account
    Then the response should be schema-valid against list-creatives-response.json
    And the creatives array should include each of the synced creatives
    And each creative entry should expose creative_id, name, format_id, and status
    # creative_lifecycle list_and_filter / list_all: after sync_creatives,
    # list_creatives without filters returns the library for the account
    # including the synced items. Each entry exposes creative_id, name,
    # format_id, status.
    # creative_lifecycle: list_creatives reflects recent sync_creatives state
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/creative/index.yaml

  @T-UC-018-storyboard-filter-by-format-id-object @storyboard-v3.1 @v3-1 @list-filter @format-id-object
  Scenario: List creatives filtered by a format_id object returns only creatives matching that {agent_url, id}
    Given the buyer has synced creatives in formats including {agent_url, "display_300x250"} and {agent_url, "video_30s"}
    When the Buyer Agent sends list_creatives with filters.format_ids carrying one format_id object {agent_url, "display_300x250"}
    Then the response should be schema-valid against list-creatives-response.json
    And the creatives array should only include creatives whose format_id matches both agent_url and id
    And the creatives array should NOT include creatives whose format_id has a different id even on the same agent_url
    # creative_lifecycle list_filtered: the buyer filters by a format_id object
    # (agent_url + id). Only creatives whose format_id matches both fields are
    # returned. A filter shaped as a bare string id (not an object) is not part
    # of the v3.1 contract; format_ids are objects, period.
    # creative_lifecycle: format_id object filter exact-matches both (agent_url, id)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/creative/index.yaml

  @T-UC-018-storyboard-filter-by-concept-id @storyboard-v3.1 @v3-1 @list-filter @concept-id
  Scenario: List creatives filtered by concept_ids returns only creatives in that concept carrying concept_id and concept_name
    Given the authenticated principal has creatives grouped under concept "concept_summer_2026" and other creatives under different concepts
    When the Buyer Agent sends list_creatives with filters.concept_ids ["concept_summer_2026"]
    Then the response should be schema-valid against list-creatives-response.json
    And the creatives array should only include creatives belonging to concept "concept_summer_2026"
    And each returned creative should carry concept_id "concept_summer_2026" and a concept_name
    # v3.1 ADDED filter filters.concept_ids (array of concept-id strings, minItems 1).
    # Concepts group related creatives across sizes and formats. Satisfies the
    # User Intent "List creatives in this concept with their DCO variables" and the
    # INT-001 "concept" filter dimension. Each returned creative exposes concept_id
    # and concept_name (list-creatives-response.json creatives[].concept_id/concept_name).
    # creative_lifecycle: concept_ids filter scopes results to one concept; concept_id/concept_name exposed
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-filter-boolean-flags @list-filter @boolean-filter @v3-1
  Scenario Outline: v3.1 boolean filter <flag> partitions the library
    Given the authenticated principal has both creatives matching and not matching <flag>
    When the Buyer Agent sends a list_creatives request with <flag> <value>
    Then <outcome>
    # v3.1 ADDED boolean CreativeFilters has_variables (DCO vs static) and
    # has_served (has served >=1 impression vs never served). Each partitions
    # the principal's library into the matching subset.

    Examples: Boolean filter partitions
      | flag          | value | outcome                                                          |
      | has_variables | true  | only creatives with dynamic variables (DCO) are returned          |
      | has_variables | false | only static creatives (no dynamic variables) are returned         |
      | has_served    | true  | only creatives that have served at least one impression are returned |
      | has_served    | false | only creatives that have never served are returned                |

  @T-UC-018-boundary-creative-status @boundary @creative-status
  Scenario Outline: Creative status filter boundary -- <boundary_point>
    Given the authenticated principal has creatives in statuses "processing", "approved", "rejected", "pending_review", "archived"
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                                                  | request_params                          | outcome                                                          |
      | processing (first enum value)                                                   | statuses filter ["processing"]          | only processing creatives are returned                            |
      | archived (last enum value)                                                      | statuses filter ["archived"]            | only archived creatives are returned                              |
      | ["approved", "rejected"] (multi-status array)                                   | statuses filter ["approved", "rejected"] | only approved and rejected creatives are returned                 |
      | Not provided (default excludes archived; or seller has no review lifecycle)     | no statuses filter                      | all non-archived creatives are returned (archived excluded by default) |
      | deleted (not in CreativeStatus enum)                                            | statuses filter ["deleted"]             | error "VALIDATION_ERROR" with suggestion                          |

  @T-UC-018-boundary-sandbox-response @boundary @sandbox @br-rule-209
  Scenario Outline: Sandbox response semantics boundary -- <boundary_point>
    Given the authenticated principal has creatives
    And the request targets <account_kind>
    When the Buyer Agent sends a list_creatives request
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                  | account_kind                          | outcome                                          |
      | sandbox: true in response (sandbox account)     | a sandbox account                     | the response should include sandbox equals true   |
      | sandbox absent in response (production account) | a production account                  | the response should not include a sandbox field   |
      | sandbox: false in response (explicit production) | a production account with sandbox false | the response should include sandbox equals false  |
