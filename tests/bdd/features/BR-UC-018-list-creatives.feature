# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)

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
    Then the response is compliant with the list_creatives spec
    And the response contains a creatives array with 4 items
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
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-main-enriched @main-flow
  Scenario: List creatives with assignments included by default
    Given the authenticated principal has 2 approved creatives with package assignments
    When the Buyer Agent sends a list_creatives request with no parameters
    Then the response is compliant with the list_creatives spec
    And the response contains a creatives array with 2 items
    And each creative includes assignment data
    # BR-RULE-149 INV-3: include_assignments defaults to true
    # POST-S4: Buyer knows package assignments (default included)
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-main-performance @main-flow
  Scenario: List creatives with explicit delivery snapshot request
    Given the authenticated principal has 2 approved creatives
    When the Buyer Agent sends a list_creatives request with include_snapshot true
    Then the response is compliant with the list_creatives spec
    And each creative includes a snapshot_unavailable_reason of "SNAPSHOT_UNSUPPORTED"
    # BR-RULE-149 INV-4: include_snapshot defaults to false, must explicitly request
    # POST-S5: Buyer knows the lightweight delivery snapshot, OR a machine-readable reason
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json
    #
    # Was "each creative includes a delivery snapshot", over a Given that promised
    # "creatives with delivery snapshot data". This seller has no such data to seed: no
    # column in this schema holds a creative's lifetime impressions or last-served date.
    # The pin does not require a seller to have them -- it requires a seller that does not
    # to SAY SO, through snapshot_unavailable_reason, whose SNAPSHOT_UNSUPPORTED member is
    # described as "The seller platform does not support delivery snapshots for this
    # entity" (enums/snapshot-unavailable-reason.json). That disclosure is what POST-S5
    # leaves the buyer knowing here, and it is what this scenario now grades.

  @T-UC-018-main-subassets @main-flow
  Scenario: List creatives with explicit items request
    Given the authenticated principal has a multi-asset creative with items
    When the Buyer Agent sends a list_creatives request with include_items true
    Then the response is compliant with the list_creatives spec
    And the creative includes items data
    # BR-RULE-149 INV-5: include_items defaults to false, must explicitly request
    # POST-S6: Buyer knows the items for multi-asset creatives (when requested)
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-main-variables @main-flow
  Scenario: List creatives with explicit variables request
    Given the authenticated principal has a creative with dynamic-content variables
    When the Buyer Agent sends a list_creatives request with include_variables true
    Then the response is compliant with the list_creatives spec
    And the creative includes variables data
    # BR-RULE-149 INV-7: include_variables defaults to false, must explicitly request
    # POST-S8: Buyer knows dynamic-content variables / DCO slots (when requested)

  @T-UC-018-ext-a @extension @ext-a @error
  Scenario: Authentication required -- no credentials
    Given the Buyer has no authentication credentials
    When the Buyer Agent sends a list_creatives request
    Then the error is compliant with the AdCP error spec
    And the operation should fail with error code "AUTH_MISSING"
    And the error code should be "AUTH_MISSING"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains authentication is required
    # POST-F3: Suggestion advises providing valid credentials

  @T-UC-018-ext-b @extension @ext-b @error
  Scenario: Tenant unavailable -- identity has no tenant mapping
    Given no tenant can be resolved from the request context
    When the Buyer Agent sends a list_creatives request
    Then the error is compliant with the AdCP error spec
    And the operation should fail with error code "TENANT_UNDEFINED"
    And the error code should be "TENANT_UNDEFINED"
    And the error recovery should be "terminal"
    And the error should include a "suggestion" field
    # Was AUTH_MISSING, and before that AUTH_REQUIRED, and before that TENANT_REQUIRED — a
    # code enums/error-code.json does not carry at 3.1.1. AUTH_MISSING was reached by
    # reasoning that with no pinned code for an unresolvable tenant, the seller should report
    # the authentication state it can see. It reports the state it is actually in instead.
    #
    # This Given sends NO HEADERS AT ALL, so the deployment cannot tell WHICH seller the
    # request addresses. It therefore cannot apply any seller's authentication policy, and
    # saying "you presented no credentials" asserts a check it never performed — for a
    # request that would be refused identically with a perfectly valid bearer.
    # CONFIGURATION_ERROR is pinned, its enumMetadata recovery is "terminal", and its
    # suggestion is the one production emits: a buyer cannot fix a seller-side deployment
    # that serves no seller at the host it was dialed on, and MUST NOT auto-retry.
    #
    # Two sibling scenarios on the SAME Given already grade exactly this, so the three now
    # agree rather than two agreeing and this one dissenting: BR-UC-010 @T-UC-010-ext-a
    # ("a request naming no seller is refused") and BR-UC-013 @T-UC-013-051 ("valid
    # credentials but unresolvable tenant"). Which seller a request names is outside the
    # spec's model, so what a deployment answers when it cannot tell is the seller's own
    # contract; mirror it upstream in adcp-req.
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains the seller could not be determined
    # POST-F3: Suggestion directs the report to a human at the seller

  @T-UC-018-ext-c @extension @ext-c @error
  Scenario Outline: Validation failure -- <description>
    Given the Buyer is authenticated
    When the Buyer Agent sends a list_creatives request with <invalid_param>
    Then the error is compliant with the AdCP error spec
    And the operation should fail with error code "INVALID_REQUEST"
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "<error_detail>"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains which parameters are invalid
    # POST-F3: Suggestion provides valid parameter values

    Examples:
      | description                  | invalid_param                          | error_detail     |
      | invalid status enum          | statuses filter "unknown"              | status           |
      | empty statuses array         | statuses filter as empty array         | statuses         |
      | non-integer max_results      | pagination max_results "abc"           | max_results      |
      | empty fields array           | fields as empty array                  | fields           |
      | unknown field enum           | fields containing "thumbnail"          | field            |
      | empty tags array             | tags filter as empty array             | tags             |
      | creative_ids over max        | creative_ids with 101 items            | creative_ids     |
      | non-string field item        | fields containing integer 123          | field            |

  @T-UC-018-ext-d @extension @ext-d @error
  Scenario Outline: Invalid date format -- <date_field> with value "<value>"
    Given the Buyer is authenticated
    When the Buyer Agent sends a list_creatives request with <date_field> as "<value>"
    Then the error is compliant with the AdCP error spec
    And the operation should fail with error code "INVALID_REQUEST"
    And the error code should be "INVALID_REQUEST"
    # The two lines above asked for two DIFFERENT codes for one refusal, so no
    # implementation could satisfy the scenario. Reconciled to INVALID_REQUEST: a value
    # that is not a date-time violates a SCHEMA CONSTRAINT on
    # core/creative-filters.json's created_after/created_before (format: date-time),
    # which pinned 3.1.1 assigns to INVALID_REQUEST ("violates schema constraints"),
    # not to VALIDATION_ERROR ("beyond schema validation").
    And the error field should contain "<date_field>"
    And the error should include a "suggestion" field
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
    Then the response is compliant with the list_creatives spec
    And <outcome>

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
      | invalid_status_enum   | statuses filter ["unknown"]         | error "INVALID_REQUEST" with suggestion               |
      | empty_statuses_array  | statuses filter as empty array      | error "INVALID_REQUEST" with suggestion               |

  @T-UC-018-boundary-default-query @boundary @default-query-behavior
  Scenario Outline: Default query behavior boundary -- <boundary_point>
    Given the authenticated principal has creatives in various statuses
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then the response is compliant with the list_creatives spec
    And <outcome>

    Examples: Boundary values
      | boundary_point                                        | request_params                                                                       | outcome                                                  |
      | Empty request (no parameters at all)                  | no parameters                                                                        | all non-archived creatives returned                       |
      | statuses=['archived'] — only archived creatives returned | statuses filter ["archived"]                                                         | only archived creatives returned                          |
      | statuses=[] — empty array violates minItems:1         | statuses filter as empty array                                                       | error "INVALID_REQUEST" with suggestion                  |
      | filters={} — empty filters object, defaults apply     | empty filters object                                                                 | all non-archived creatives returned (defaults apply)      |
      | statuses=['unknown'] — invalid enum value             | statuses filter ["unknown"]                                                          | error "INVALID_REQUEST" with suggestion                  |
      | All 5 statuses explicitly listed — includes archived  | statuses filter ["processing", "approved", "rejected", "pending_review", "archived"] | all 5 creatives including archived returned               |

  @T-UC-018-partition-pagination @partition @pagination-sorting
  # max_results is a member of the pagination OBJECT (core/pagination-request.json), and
  # sort is an object of field + direction; the rows below name them that way. The flat
  # max_results / sort_by / sort_order / limit spellings they used to carry are not in
  # AdCP 3.1.1 at all, so no row could have graded a spec obligation through them.
  Scenario Outline: Pagination and sorting -- <partition>
    Given the authenticated principal has 60 approved creatives
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then the response is compliant with the list_creatives spec
    And <outcome>

    Examples: Valid partitions
      | partition                      | request_params                               | outcome                                                       |
      | default_pagination             | no pagination params                         | 50 creatives returned (default page size)                      |
      | explicit_pagination            | pagination max_results 20                    | 20 creatives returned                                          |
      | boundary_min_limit             | pagination max_results 1                     | 1 creative returned                                            |
      | schema_max_limit               | pagination max_results 100                   | 60 creatives returned (all available, below cap)                |
      | default_sort                   | no sort params                               | creatives sorted by created_date descending                     |
      | explicit_sort                  | sort field "name" direction "asc"            | creatives sorted by name ascending                              |

    Examples: Invalid partitions
      | partition                | request_params                    | outcome                                     |
      | max_results_zero         | pagination max_results 0          | error "INVALID_REQUEST" with suggestion     |
      | max_results_negative     | pagination max_results -1         | error "INVALID_REQUEST" with suggestion     |
      | non_integer_max_results  | pagination max_results "abc"      | error "INVALID_REQUEST" with suggestion     |
      | max_results_above_max    | pagination max_results 101        | error "INVALID_REQUEST" with suggestion     |

  @T-UC-018-boundary-pagination @boundary @pagination-sorting
  Scenario Outline: Pagination boundary -- <boundary_point>
    Given the authenticated principal has 60 approved creatives
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then the response is compliant with the list_creatives spec
    And <outcome>

    Examples: Boundary values
      | boundary_point                                    | request_params         | outcome                                                          |
      | pagination.max_results=0 (below schema min of 1)   | pagination max_results 0    | error "INVALID_REQUEST" with suggestion                     |
      | pagination.max_results=1 (schema minimum)          | pagination max_results 1    | 1 creative returned                                         |
      | pagination.max_results=100 (schema maximum)        | pagination max_results 100  | 60 creatives returned (all available)                        |
      | pagination.max_results=101 (above schema maximum)  | pagination max_results 101  | error "INVALID_REQUEST" with suggestion                      |
      | sort.direction='asc' (valid enum)                  | sort direction "asc"        | creatives sorted ascending                                   |
      | sort.direction='desc' (valid enum, the default)    | sort direction "desc"       | creatives sorted descending                                  |
      | sort.direction='random' (not in the enum)          | sort direction "random"     | error "INVALID_REQUEST" with suggestion                      |
      | sort.field='assignment_count' (last enum member)   | sort field "assignment_count" | creatives sorted by assignment_count                      |
      | sort.field='unknown_field' (not in the enum)       | sort field "unknown_field"  | error "INVALID_REQUEST" with suggestion                      |

  @T-UC-018-partition-filters @partition @filter-semantics
  Scenario Outline: Filter semantics -- <partition>
    Given the authenticated principal has creatives with various tags, statuses, and media buy associations
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then the response is compliant with the list_creatives spec
    And <outcome>

    # The three rows this outline used to carry for FLAT filter params — flat_only,
    # flat_and_structured_no_conflict and flat_and_structured_conflict — are deleted, not
    # rewritten. AdCP 3.1.1 list-creatives-request.json declares no top-level status or
    # tags, so "the flat param takes precedence" is a precedence rule between a spec field
    # and a field that does not exist: there is no obligation for a row to grade, and the
    # DTO refuses the undeclared key rather than ranking it. The structured behaviour they
    # approximated is graded by structured_only and tags_and_semantics below.
    Examples: Valid partitions
      | partition                        | request_params                                                                     | outcome                                                              |
      | no_filters                       | no filter parameters                                                               | all non-archived creatives returned                                   |
      | structured_only                  | structured filters with statuses ["approved"] and name_contains "nike"             | only approved creatives matching "nike" returned                      |
      | media_buy_ids_multi              | structured filters with media_buy_ids ["mb1", "mb2"]                               | creatives for both mb1 and mb2 returned (deduplicated)                |
      | tags_and_semantics               | tags filter ["q1", "brand"]                                                        | only creatives with BOTH q1 AND brand tags returned                   |
      | tags_or_semantics                | tags_any filter ["q1", "brand"]                                                    | creatives with EITHER q1 OR brand tag returned                        |
      | combined_date_range              | created_after "2024-01-01T00:00:00Z" and created_before "2024-06-30T23:59:59Z"    | only creatives within date range returned                             |

    Examples: Invalid partitions
      | partition                | request_params                             | outcome                                            |
      | invalid_date_format      | created_after "not-a-date"                 | error "INVALID_REQUEST" with suggestion          |
      | empty_tags_array         | tags filter as empty array                 | error "INVALID_REQUEST" with suggestion             |
      | creative_ids_over_limit  | creative_ids with 101 items                | error "INVALID_REQUEST" with suggestion             |
      | has_served_unanswerable  | has_served true                            | error "UNSUPPORTED_FEATURE" with suggestion         |
    # has_served is a filter the pin declares and this seller cannot evaluate: nothing in
    # the schema records whether a creative has served an impression. Silently ignoring it
    # would answer a question the buyer did not ask, and core/creative-filters.json grants
    # an explicit "standalone creative agents SHOULD ignore this filter" licence to three
    # sales-agent-specific filters and not to this one. The pinned error table gives
    # UNSUPPORTED_FEATURE for "Requested feature not supported by this seller", recovery
    # correctable, so the buyer can drop the filter and retry. Moved here from the
    # boolean-filter outline, whose Given cannot establish the state it promised.

  @T-UC-018-boundary-filters @boundary @filter-semantics
  # The flat-versus-structured conflict row is deleted for the reason given on the
  # partition outline above, and the singular media_buy_id row is restated as a duplicate
  # MEMBER of filters.media_buy_ids: the plural array is the only media-buy filter
  # core/creative-filters.json declares, and a repeated member is the boundary the
  # deduplication claim is actually about.
  Scenario Outline: Filter semantics boundary -- <boundary_point>
    Given the authenticated principal has creatives with various tags, media buy associations, and creation dates
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then the response is compliant with the list_creatives spec
    And <outcome>

    Examples: Boundary values
      | boundary_point                                                       | request_params                                                               | outcome                                                               |
      | tags=['single_tag'] (minimum AND match)                              | tags filter ["single_tag"]                                                   | creatives with tag "single_tag" returned                               |
      | tags_any=['single_tag'] (minimum OR match)                           | tags_any filter ["single_tag"]                                               | creatives with tag "single_tag" returned                               |
      | creative_ids with 100 items (maxItems boundary)                      | creative_ids with exactly 100 items                                          | creatives matching those IDs returned                                  |
      | creative_ids with 101 items (above maxItems)                         | creative_ids with 101 items                                                  | error "INVALID_REQUEST" with suggestion                               |
      | media_buy_ids=['mb1','mb1'] (duplicate member, deduplicated)         | structured filters with media_buy_ids ["mb1", "mb1"]                         | creatives for mb1 returned (deduplicated, no duplicate results)        |
      | created_after='2024-01-01T00:00:00Z' (valid ISO 8601)               | created_after "2024-01-01T00:00:00Z"                                         | creatives created after the date returned                              |
      | created_after='yesterday' (invalid date format)                      | created_after "yesterday"                                                    | error "INVALID_REQUEST" with suggestion                            |

  @T-UC-018-partition-field-selector @partition @field-selector
  Scenario Outline: Field selector -- <partition>
    Given the authenticated principal has 3 approved creatives with full data
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then the response is compliant with the list_creatives spec
    And <outcome>

    # Every "only ..." outcome now names the same obligation, because it IS the same
    # obligation in each row and the row's own request_params cell says which fields were
    # asked for: the response carries the selected members and the six
    # list-creatives-response.json marks REQUIRED on every item, and nothing else. The
    # cells used to say "only creative_id field", which no conformant seller can produce —
    # `fields` cannot express keeping the required members, so a projection that dropped
    # them would answer a selection with a document violating the schema it was selected
    # from.
    #
    # The all-13 cell asks for every enum member, which is by definition what an omitted
    # `fields` returns, so it asserts the same thing the `omitted` row does. It is NOT
    # key-presence of 13: a creative legitimately omits an empty tag list, and this seller
    # omits the members it does not hold.
    #
    # invalid_db_status_tolerance is GONE from this outline: its request_params cell
    # ("database has creative with unrecognized status value") describes a database
    # fixture, not a request, so the row had nothing to dispatch. That obligation has its
    # own scenario, @T-UC-018-inv-149-6-holds, which grades it end to end.
    Examples: Valid partitions
      | partition                    | request_params                                                                                      | outcome                                                        |
      | omitted                      | no fields parameter                                                                                 | all fields included in each creative object                     |
      | single_field                 | fields ["creative_id"]                                                                              | only the selected and required fields in each creative object   |
      | minimal_set                  | fields ["creative_id", "name", "status"]                                                            | only the selected and required fields in each creative object   |
      | all_fields                   | fields with all 20 enum values                                                                      | all fields included in each creative object                     |
      | enrichment_fields            | fields ["creative_id", "assignments", "snapshot"] and include_snapshot true                         | only the selected and required fields in each creative object   |
      | assignments_disabled         | include_assignments false                                                                           | assignment data excluded from creatives                          |

    Examples: Invalid partitions
      | partition         | request_params                      | outcome                                         |
      | empty_array       | fields as empty array               | error "INVALID_REQUEST" with suggestion         |
      | unknown_field     | fields ["creative_id", "thumbnail"] | error "INVALID_REQUEST" with suggestion         |
      | non_string_item   | fields containing integer 123       | error "INVALID_REQUEST" with suggestion         |

  @T-UC-018-boundary-field-selector @boundary @field-selector
  Scenario Outline: Field selector boundary -- <boundary_point>
    Given the authenticated principal has creatives with full data
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then the response is compliant with the list_creatives spec
    And <outcome>

    # Same three reconciliations as the partition outline above: the "only ..." cells name
    # the obligation a conformant projection can meet, the all-13 cell equals an omitted
    # `fields`, and the database-fixture row is graded by @T-UC-018-inv-149-6-holds instead
    # of by a request that cannot express it.
    Examples: Boundary values
      | boundary_point                                            | request_params                                  | outcome                                                  |
      | ['creative_id'] (single field, minItems boundary)         | fields ["creative_id"]                          | only the selected and required fields in response         |
      | All 20 enum values (max enum coverage)                    | fields with all 20 enum values                  | all fields included in response                           |
      | [] (empty array, violates minItems: 1)                    | fields as empty array                           | error "INVALID_REQUEST" with suggestion                  |
      | ['creative_id', 'thumbnail'] (unknown enum value)         | fields ["creative_id", "thumbnail"]             | error "INVALID_REQUEST" with suggestion                  |
      | fields omitted entirely (all fields returned)             | no fields parameter                             | all fields included in response                            |
      | include_assignments=false (overrides default true)         | include_assignments false                       | assignment data excluded                                   |

  @T-UC-018-inv-146-1-holds @invariant @BR-RULE-146
  Scenario: BR-RULE-146 INV-1 holds -- no filters returns all non-archived creatives
    Given the authenticated principal has 3 approved and 1 archived creative
    When the Buyer Agent sends a list_creatives request with no filters
    Then the response is compliant with the list_creatives spec
    And the response contains 3 creatives
    And none of the returned creatives have status "archived"

  @T-UC-018-inv-146-2-holds @invariant @BR-RULE-146
  Scenario: BR-RULE-146 INV-2 holds -- explicit archived status includes archived creatives
    Given the authenticated principal has 3 approved and 2 archived creatives
    When the Buyer Agent sends a list_creatives request with statuses filter ["archived"]
    Then the response is compliant with the list_creatives spec
    And the response contains 2 creatives
    And all returned creatives have status "archived"

  @T-UC-018-inv-146-2-violated @invariant @BR-RULE-146
  Scenario: BR-RULE-146 INV-2 violated -- archived status NOT in filter excludes archived
    Given the authenticated principal has 3 approved and 2 archived creatives
    When the Buyer Agent sends a list_creatives request with statuses filter ["approved"]
    Then the response is compliant with the list_creatives spec
    And the response contains 3 creatives
    And none of the returned creatives have status "archived"
    # Counter-example: not specifying archived means archived are excluded

  @T-UC-018-inv-146-3-holds @invariant @BR-RULE-146
  Scenario: BR-RULE-146 INV-3 holds -- statuses filter without archived excludes archived
    Given the authenticated principal has 2 approved, 1 rejected, and 1 archived creative
    When the Buyer Agent sends a list_creatives request with statuses filter ["approved", "rejected"]
    Then the response is compliant with the list_creatives spec
    And the response contains 3 creatives
    And none of the returned creatives have status "archived"

  @T-UC-018-inv-147-1-holds @invariant @BR-RULE-147
  Scenario: BR-RULE-147 INV-1 holds -- no pagination uses default page size 50
    Given the authenticated principal has 60 approved creatives
    When the Buyer Agent sends a list_creatives request with no pagination params
    Then the response is compliant with the list_creatives spec
    And the response contains 50 creatives
    And pagination shows has_more as true

  @T-UC-018-inv-147-2-holds @invariant @BR-RULE-147
  Scenario: BR-RULE-147 INV-2 holds -- a page never exceeds the schema maximum of 100
    Given the authenticated principal has 60 approved creatives
    When the Buyer Agent sends a list_creatives request with pagination max_results 100
    Then the response is compliant with the list_creatives spec
    And 60 creatives returned (all available)
    And the pagination shows has_more as false
    # Was "limit exceeding 1000 is capped". There is no `limit` in AdCP 3.1.1 and no
    # 1000-item cap anywhere in it: core/pagination-request.json types max_results with
    # minimum 1 and MAXIMUM 100, so the page ceiling a buyer can ask for is 100 and a
    # larger value is refused (graded by the boundary outline's max_results=101 row).
    # This invariant now grades the ceiling that exists — a request at the maximum is
    # answered, and with fewer creatives available it returns all of them.

  @T-UC-018-inv-147-3-holds @invariant @BR-RULE-147
  Scenario: BR-RULE-147 INV-3 holds -- no sort defaults to created_date descending
    Given the authenticated principal has creatives created on different dates
    When the Buyer Agent sends a list_creatives request with no sort params
    Then the response is compliant with the list_creatives spec
    And the creatives are ordered by created_date descending
    And the query_summary shows sort_applied as "created_date desc"

  @T-UC-018-inv-147-4-holds @invariant @BR-RULE-147
  Scenario: BR-RULE-147 INV-4 holds -- a sort direction outside the enum is refused
    Given the authenticated principal has creatives created on different dates
    When the Buyer Agent sends a list_creatives request with sort direction "random"
    Then the error is compliant with the AdCP error spec
    And error "INVALID_REQUEST" with suggestion
    # Was "invalid sort_order coerced to desc". Two things in that are not in the pin:
    # the flat sort_order key, and the coercion. sort.direction $refs
    # enums/sort-direction.json, a CLOSED two-member enum, so "random" violates a schema
    # constraint and must be refused; silently ordering by desc would answer a question
    # the buyer did not ask. The seller cannot know which direction was meant.

  @T-UC-018-inv-147-5-holds @invariant @BR-RULE-147
  Scenario: BR-RULE-147 INV-5 holds -- a sort field outside the enum is refused
    Given the authenticated principal has creatives created on different dates
    When the Buyer Agent sends a list_creatives request with sort field "unknown_field"
    Then the error is compliant with the AdCP error spec
    And error "INVALID_REQUEST" with suggestion
    # Was "invalid sort_by coerced to created_date", and the same reconciliation applies:
    # sort.field $refs enums/creative-sort-field.json, a closed five-member enum.

  @T-UC-018-inv-148-1-holds @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-1 holds -- the statuses filter selects exactly the statuses it names
    Given the authenticated principal has 3 approved and 2 rejected creatives
    When the Buyer Agent sends a list_creatives request with structured statuses ["approved"]
    Then the response is compliant with the list_creatives spec
    And the response contains 3 creatives
    And all returned creatives have status "approved"
    # Was "flat params take precedence over structured on conflict". There are no flat
    # filter params in AdCP 3.1.1 list-creatives-request.json, so the conflict this
    # invariant ranked cannot arise: filters is the only place statuses is declared, and
    # an undeclared top-level status is refused rather than preferred. What remains of the
    # invariant is the half that IS a spec obligation — the named statuses are the ones
    # returned — with its counter-example in the sibling scenario below.

  @T-UC-018-inv-148-1-violated @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-1 context -- no conflict when only structured filters used
    Given the authenticated principal has 3 approved and 2 rejected creatives
    When the Buyer Agent sends a list_creatives request with structured statuses ["rejected"]
    Then the response is compliant with the list_creatives spec
    And the response contains 2 creatives
    And all returned creatives have status "rejected"
    # When there is no flat param conflict, structured filters are used as-is

  @T-UC-018-inv-148-2-holds @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-2 holds -- tags filter uses AND semantics
    Given the authenticated principal has a creative with tags ["q1", "brand"] and a creative with tags ["q1"]
    When the Buyer Agent sends a list_creatives request with tags filter ["q1", "brand"]
    Then the response is compliant with the list_creatives spec
    And the response contains 1 creative
    And the returned creative has both tags "q1" and "brand"

  @T-UC-018-inv-148-2-violated @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-2 counter -- creative with only one tag excluded by AND filter
    Given the authenticated principal has a creative with tags ["q1"] only
    When the Buyer Agent sends a list_creatives request with tags filter ["q1", "brand"]
    Then the response is compliant with the list_creatives spec
    And the creative with only tag "q1" is not returned

  @T-UC-018-inv-148-3-holds @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-3 holds -- tags_any filter uses OR semantics
    Given the authenticated principal has a creative with tag "q1" and a creative with tag "brand"
    When the Buyer Agent sends a list_creatives request with tags_any filter ["q1", "brand"]
    Then the response is compliant with the list_creatives spec
    And the response contains 2 creatives

  @T-UC-018-inv-148-4-holds @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-4 holds -- media_buy_ids returns the creatives of every buy it names
    Given the authenticated principal has creatives associated with media buys "mb1" and "mb2"
    When the Buyer Agent sends a list_creatives request with structured filters with media_buy_ids ["mb1", "mb2"]
    Then the response is compliant with the list_creatives spec
    And the response contains creatives from both "mb1" and "mb2"
    # Was "singular media_buy_id merged into plural array". core/creative-filters.json
    # declares media_buy_ids (an array) and no singular sibling, so there is no merge to
    # grade; the obligation is that every named buy's creatives come back, and only those
    # (the Given seeds a third creative on a buy the request does not name).

  @T-UC-018-inv-148-6-holds @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-6 holds -- invalid date format raises validation error
    Given the Buyer is authenticated
    When the Buyer Agent sends a list_creatives request with created_after "not-a-date"
    Then the error is compliant with the AdCP error spec
    And the operation should fail with error code "INVALID_REQUEST"
    And the error code should be "INVALID_REQUEST"
    And the error should include a "suggestion" field
    # POST-F3: Suggestion for recovery
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-inv-148-6-violated @invariant @BR-RULE-148
  Scenario: BR-RULE-148 INV-6 counter -- valid ISO 8601 date accepted
    Given the authenticated principal has creatives created in 2024
    When the Buyer Agent sends a list_creatives request with created_after "2024-01-01T00:00:00Z"
    Then the response is compliant with the list_creatives spec
    And the operation succeeds
    And the response contains creatives created after the specified date

  @T-UC-018-inv-149-1-holds @invariant @BR-RULE-149
  Scenario: BR-RULE-149 INV-1 holds -- fields array projects response
    Given the authenticated principal has 2 approved creatives with full data
    When the Buyer Agent sends a list_creatives request with fields ["creative_id", "name"]
    Then the response is compliant with the list_creatives spec
    And only the selected and required fields in each creative object
    # Was 'contains only "creative_id" and "name" fields'. The projection narrows the
    # OPTIONAL members: list-creatives-response.json marks creative_id, name, format_id,
    # status, created_date and updated_date REQUIRED on every item, and `fields` has no way
    # to express keeping them, so a response honouring this selection literally would
    # violate the schema the selection is made against. The step asserts the exact key set
    # the pin permits: the two selected members, the required ones, and nothing more.

  @T-UC-018-inv-149-2-holds @invariant @BR-RULE-149
  Scenario: BR-RULE-149 INV-2 holds -- fields omitted returns all fields
    Given the authenticated principal has 2 approved creatives with full data
    When the Buyer Agent sends a list_creatives request with no fields parameter
    Then the response is compliant with the list_creatives spec
    And each creative in the response contains all available fields

  @T-UC-018-inv-149-3-holds @invariant @BR-RULE-149
  Scenario: BR-RULE-149 INV-3 holds -- include_assignments defaults to true
    Given the authenticated principal has an approved creative with package assignments
    When the Buyer Agent sends a list_creatives request without specifying include_assignments
    Then the response is compliant with the list_creatives spec
    And the creative in the response includes assignment data

  @T-UC-018-inv-149-4-holds @invariant @BR-RULE-149
  Scenario: BR-RULE-149 INV-4 holds -- include_snapshot defaults to false
    # The Given said "with delivery snapshot data", which this seller cannot seed (see the
    # main-performance note). The invariant does not need it: the obligation is that an
    # unrequested snapshot is absent, and that is graded over any creative.
    Given the authenticated principal has an approved creative
    When the Buyer Agent sends a list_creatives request without specifying include_snapshot
    Then the response is compliant with the list_creatives spec
    And the creative in the response does not include a delivery snapshot
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-inv-149-5-holds @invariant @BR-RULE-149
  Scenario: BR-RULE-149 INV-5 holds -- include_items defaults to false
    Given the authenticated principal has a multi-asset creative with items
    When the Buyer Agent sends a list_creatives request without specifying include_items
    Then the response is compliant with the list_creatives spec
    And the creative in the response does not include items data
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-inv-149-7-holds @invariant @BR-RULE-149
  Scenario: BR-RULE-149 INV-7 holds -- include_variables defaults to false
    Given the authenticated principal has an approved creative with dynamic-content variables
    When the Buyer Agent sends a list_creatives request without specifying include_variables
    Then the response is compliant with the list_creatives spec
    And the creative in the response does not include variables data
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-inv-149-6-holds @invariant @BR-RULE-149
  Scenario: BR-RULE-149 INV-6 holds -- unrecognized DB status reported as processing
    Given the authenticated principal has a creative with database status "draft" (not in protocol enum)
    When the Buyer Agent sends a list_creatives request
    Then the response is compliant with the list_creatives spec
    And the creative is returned with status "processing"
    And no error is raised
    And exactly one entry in errors[] names that creative with code "CONFIGURATION_ERROR"
    # Was "mapped to pending_review". The pin does not choose a placeholder — status is
    # REQUIRED and $refs a closed enum with no "unknown" member — so the choice is the
    # seller's, and this seller's is `processing`: the only member that asserts no
    # completed evaluation and no seller obligation. pending_review is the one member that
    # would be a second untruth, claiming processing succeeded and a decision is owed.
    #
    # The substitution alone is not the whole obligation. list-creatives-response.json
    # carries errors[] on the SUCCESS path for exactly this case — the read succeeded and
    # one record is not fully representable — so the buyer is told WHICH creative could not
    # be described. CONFIGURATION_ERROR because the pin gives that code recovery "terminal"
    # and "surface to a human at the seller": a seller-side data defect is not something a
    # buyer can retry, and a code outside the wire-standard set collapses to
    # SERVICE_UNAVAILABLE, which tells the buyer to poll forever for a row only the seller
    # can repair.
    #
    # Exactly one entry, not merely one present: a per-row advisory emitted twice would
    # read as two defective records.
    #
    # The recovery value is deliberately not asserted. It is a read-only property over the
    # code table, so it is right whenever the code is right.
    # (src/core/tools/creatives/listing.py)

  @T-UC-018-inv-034-1-holds @invariant @BR-RULE-034
  Scenario: BR-RULE-034 INV-1 holds -- query always scoped by principal
    Given principal "buyer-001" has 3 creatives
    And principal "buyer-002" has 5 creatives in the same tenant
    When the Buyer Agent authenticated as "buyer-001" sends a list_creatives request
    Then the response is compliant with the list_creatives spec
    And the response contains exactly 3 creatives
    And all creatives belong to principal "buyer-001"

  @T-UC-018-inv-034-1-violated @invariant @BR-RULE-034
  Scenario: BR-RULE-034 INV-1 counter -- cross-principal creatives never visible
    Given principal "buyer-001" has 3 creatives
    And principal "buyer-002" has 5 creatives in the same tenant
    When the Buyer Agent authenticated as "buyer-001" sends a list_creatives request
    Then the response is compliant with the list_creatives spec
    And none of the returned creatives belong to principal "buyer-002"

  @T-UC-018-edge-empty-library @main-flow @edge-case
  Scenario: Empty creative library returns empty array not error
    Given the authenticated principal has no creatives
    When the Buyer Agent sends a list_creatives request
    Then the response is compliant with the list_creatives spec
    And the response contains a creatives array with 0 items
    And the query_summary shows total_matching as 0
    And the pagination shows has_more as false
    And the response is not an error
    # POST-S1: Buyer knows no creatives match (empty result)
    # POST-S2: Buyer knows total is 0
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-edge-pagination-next @main-flow @edge-case
  Scenario: Pagination cursor traversal across pages
    Given the authenticated principal has 120 approved creatives
    When the Buyer Agent sends a list_creatives request with pagination max_results 50
    Then the response is compliant with the list_creatives spec
    And the response contains 50 creatives
    And the pagination shows has_more as true
    And the pagination includes a cursor for the next page
    When the Buyer Agent sends a list_creatives request with the cursor from the previous response
    Then the response contains 50 creatives from the second page
    And the creatives do not overlap with the first page results
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-edge-duplicate-dedup @invariant @BR-RULE-148 @edge-case
  Scenario: A media_buy_id repeated inside media_buy_ids is deduplicated
    Given the authenticated principal has a creative associated with media buy "mb1"
    When the Buyer Agent sends a list_creatives request with structured filters with media_buy_ids ["mb1", "mb1"]
    Then the response is compliant with the list_creatives spec
    And the filter resolves to media_buy_ids ["mb1"] (deduplicated)
    And the creative for "mb1" is returned exactly once
    # The duplicate is a repeated MEMBER of the one array the spec declares, not a
    # singular key merged into it — core/creative-filters.json has no singular
    # media_buy_id. The two obligations are unchanged: the applied filter names the buy
    # once, and the assignment join does not multiply the creative's row.

  @T-UC-018-edge-valid-date @main-flow @edge-case
  Scenario: Valid ISO 8601 date with timezone offset accepted
    Given the authenticated principal has creatives created in 2024
    When the Buyer Agent sends a list_creatives request with created_after "2024-01-15T00:00:00+05:00"
    Then the response is compliant with the list_creatives spec
    And the operation succeeds
    And the response contains creatives created after the specified timestamp

  @T-UC-018-partition-legacy-fields @partition @list-creatives-fields
  Scenario Outline: List creatives fields partition -- <partition>
    Given the authenticated principal has creatives with full data
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then the response is compliant with the list_creatives spec
    And <outcome>

    # The same two reconciliations as the field-selector outlines above.
    Examples: Valid partitions
      | partition          | request_params                                         | outcome                                         |
      | minimal_fields     | fields ["creative_id", "name", "status"]               | only the selected and required fields in response |
      | all_enum_values    | fields with all 20 enum values                         | all fields included in response                  |

  @T-UC-018-partition-sort-field @partition @creative-sort-field
  # sort is an OBJECT of field + direction (list-creatives-request.json); the flat sort_by
  # these rows used to name is not in AdCP 3.1.1, and its enum is closed, so a value
  # outside it is refused rather than coerced to the default.
  Scenario Outline: Creative sort field partition -- <partition>
    Given the authenticated principal has approved creatives
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then the response is compliant with the list_creatives spec
    And <outcome>

    Examples: Valid partitions
      | partition          | request_params                  | outcome                                    |
      | updated_date       | sort field "updated_date"       | creatives sorted by updated_date           |
      | assignment_count   | sort field "assignment_count"   | creatives sorted by assignment_count       |

    Examples: Invalid partitions
      | partition          | request_params                  | outcome                                          |
      | unknown_value      | sort field "format"             | error "INVALID_REQUEST" with suggestion           |

  @T-UC-018-boundary-legacy-fields @boundary @list-creatives-fields
  Scenario Outline: List creatives fields boundary -- <boundary_point>
    Given the authenticated principal has creatives with full data
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then the response is compliant with the list_creatives spec
    And <outcome>

    # The same two reconciliations as the field-selector outlines above.
    Examples: Boundary values
      | boundary_point                                                                                                                          | request_params                                | outcome                                   |
      | ["creative_id"] (single field, minimum valid)                                                                                           | fields ["creative_id"]                        | only the selected and required fields in response |
      | ["creative_id", "name", "format_id", "format_kind", "format_option_ref", "assets", "status", "created_date", "updated_date", "tags", "rights", "rights_attestation_evaluations", "localization", "localization_unavailable", "assignments", "snapshot", "items", "variables", "concept", "pricing_options"] (all 20 fields) | fields with all 20 enum values                | all fields included in response           |
      | Not provided (all fields returned)                                                                                                      | no fields parameter                           | all fields returned                        |
      | ["creative_id", "thumbnail"] (unknown field in array)                                                                                   | fields ["creative_id", "thumbnail"]           | error "INVALID_REQUEST" with suggestion   |
      | [] (empty array, violates minItems)                                                                                                     | fields as empty array                         | error "INVALID_REQUEST" with suggestion   |

  @T-UC-018-boundary-sort-field @boundary @creative-sort-field
  # Same reconciliation as the partition outline above: sort.field, not flat sort_by, and
  # a value outside enums/creative-sort-field.json is refused.
  Scenario Outline: Creative sort field boundary -- <boundary_point>
    Given the authenticated principal has approved creatives
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then the response is compliant with the list_creatives spec
    And <outcome>

    Examples: Boundary values
      | boundary_point                              | request_params                  | outcome                                         |
      | created_date (first enum value, also default) | sort field "created_date"       | creatives sorted by created_date                 |
      | assignment_count (last enum value, v3.1 highest-index sort field) | sort field "assignment_count" | creatives sorted by assignment_count |
      | Not provided (defaults to created_date)     | no sort params                  | creatives sorted by created_date (default)       |
      | format (not in enum)                        | sort field "format"             | error "INVALID_REQUEST" with suggestion          |

  @T-UC-018-sandbox-happy @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account list_creatives returns simulated results with sandbox flag
    Given the authenticated principal has creatives in a sandbox account
    And the request targets a sandbox account
    When the Buyer Agent sends a list_creatives request
    Then the response is compliant with the list_creatives spec
    And the response should contain "creatives" array
    And the response should include sandbox equals true
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-2: real ad platform calls suppressed
    # BR-RULE-209 INV-4: response includes sandbox: true

  @T-UC-018-sandbox-production @invariant @br-rule-209 @sandbox
  Scenario: Production account list_creatives response does not include sandbox flag
    Given the authenticated principal has creatives in a production account
    And the request targets a production account
    When the Buyer Agent sends a list_creatives request
    Then the response is compliant with the list_creatives spec
    And the response should contain "creatives" array
    And the response should not include a sandbox field
    # BR-RULE-209 INV-5: production account -> sandbox absent

  @T-UC-018-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account with invalid filter returns real validation error
    Given the request targets a sandbox account
    When the Buyer Agent sends a list_creatives request with invalid status filter
    Then the error is compliant with the AdCP error spec
    And error "INVALID_REQUEST" with suggestion
    And the error field should contain "statuses"
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present
    #
    # The first two Then lines used to be "the response should indicate a validation error"
    # and "the error should be a real validation error, not simulated", both of which pin
    # the code VALIDATION_ERROR. A status outside enums/creative-status.json violates a
    # SCHEMA CONSTRAINT, and the pinned taxonomy assigns that to INVALID_REQUEST ("Request
    # is malformed or violates schema constraints"), so no conformant seller could satisfy
    # them here. INV-7's obligation — the refusal is real, not simulated for a sandbox
    # account — is graded on the wire instead, and more strictly: the code and its recovery
    # class, plus the FIELD the seller says it rejected, which a faked refusal does not know.

  @T-UC-018-inv-225-1-holds @invariant @BR-RULE-225 @error
  Scenario: BR-RULE-225 INV-1 holds -- include_pricing without account is rejected
    Given the Buyer is authenticated
    When the Buyer Agent sends a list_creatives request with include_pricing true and no account reference
    Then the error is compliant with the AdCP error spec
    And the operation should fail with error code "INVALID_REQUEST"
    And the error code should be "INVALID_REQUEST"
    And the error should include a "suggestion" field
    # The two code lines above named two different codes for one refusal, so nothing could
    # satisfy the scenario. Reconciled to INVALID_REQUEST: list-creatives-request.json
    # makes account conditionally REQUIRED in an allOf branch (if include_pricing is true,
    # then required: [account]), so the request violates a schema constraint — which
    # pinned 3.1.1 assigns to INVALID_REQUEST, not to VALIDATION_ERROR.
    # POST-F1, POST-F2, POST-F3
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-inv-225-2-holds @invariant @BR-RULE-225
  Scenario: BR-RULE-225 INV-2 holds -- include_pricing with an account is answered, and prices nothing
    Given the authenticated principal has 2 approved creatives
    And the request targets a production account
    When the Buyer Agent sends a list_creatives request with include_pricing true
    Then the response is compliant with the list_creatives spec
    And the operation succeeds
    And no pricing_options in any creative
    # POST-S9: Buyer knows the pricing options for using a creative -- of which this seller
    # has none to state.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json
    #
    # Was "each creative ... carries a pricing_options array with at least one option",
    # over a Given promising "an account reference resolvable to a rate card". What
    # list-creatives-response.json puts on a creative is core/vendor-pricing-option.json:
    # "A pricing option offered by a vendor agent (signals, creative, governance) ... Used
    # by ad servers and library agents", i.e. a charge for USING the creative. This seller
    # charges for inventory, not for creative serving: the account's rate_card column holds
    # an identifier with no options behind it, and the pricing models it does have belong to
    # products. minItems is 1, so an empty array would be invalid and inventing a price
    # would be worse than either -- the conformant answer is to omit the member. What the
    # gate half of BR-RULE-225 requires is still graded, here and in INV-1: naming an
    # account is what makes the request answerable at all, and INV-1 grades the refusal
    # when it is absent.

  @T-UC-018-inv-225-3-holds @invariant @BR-RULE-225
  Scenario: BR-RULE-225 INV-3 holds -- pricing_options absent when include_pricing omitted
    Given the authenticated principal has 2 approved creatives
    When the Buyer Agent sends a list_creatives request without specifying include_pricing
    Then the response is compliant with the list_creatives spec
    And no creative in the response includes a pricing_options field
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-partition-pricing-include @partition @pricing-include
  Scenario Outline: Pricing disclosure gate -- <partition>
    Given the authenticated principal has approved creatives
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then the response is compliant with the list_creatives spec
    And <outcome>
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json

    # The two account-carrying rows asked for pricing_options to be PRESENT. This seller
    # hosts no creative-serving pricing to put there (see the BR-RULE-225 INV-2 note), and
    # both rows also named an account no Given seeds, so neither could reach the gate they
    # exist to grade. They are restated as the one thing the account changes here: the
    # request is answered rather than refused, and no creative is priced. The account comes
    # from the shared account Given, so the reference resolves for real.
    Examples: Valid partitions
      | partition                        | request_params                                              | outcome                               |
      | pricing_omitted                  | no include_pricing parameter                                | no pricing_options in any creative    |
      | pricing_false                    | include_pricing false                                       | no pricing_options in any creative    |

    Examples: Invalid partitions
      | partition               | request_params                      | outcome                                    |
      | pricing_without_account | include_pricing true and no account | error "INVALID_REQUEST" with suggestion   |

  @T-UC-018-boundary-pricing-include @boundary @pricing-include
  Scenario Outline: Pricing disclosure gate boundary -- <boundary_point>
    Given the authenticated principal has approved creatives
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then the response is compliant with the list_creatives spec
    And <outcome>

    # The gate-satisfied row is graded by @T-UC-018-inv-225-2-holds, which carries the
    # account Given this outline has no room for: an account the seller can resolve has to
    # be seeded, and an outline's single Given is already spent on the creatives. What
    # remains here is the boundary itself -- the gate violated, and the two ways of not
    # asking for pricing at all.
    Examples: Boundary values
      | boundary_point                                              | request_params                                          | outcome                                  |
      | include_pricing=true + account absent (gate violated)       | include_pricing true and no account                     | error "INVALID_REQUEST" with suggestion |
      | include_pricing=false (no account needed)                   | include_pricing false                                   | no pricing_options in any creative       |
      | include_pricing omitted (defaults false, no account needed) | no include_pricing parameter                            | no pricing_options in any creative       |

  # BR-RULE-226 INV-1 IS DELETED. It asserted that a snapshot comes back "when available",
  # over a Given promising a creative "with available delivery snapshot data". No column in
  # this schema holds a per-creative lifetime impression count or last-served date, and the
  # pin does not oblige a seller to have one -- list-creatives-request.json makes
  # include_snapshot a request, and snapshot-unavailable-reason.json is how a seller without
  # the capability answers. There is therefore no state of this seller in which the
  # scenario's precondition holds, and the obligation that does bind it -- the disclosure --
  # is INV-2 below. Restoring INV-1 belongs with a delivery-snapshot source, not with a
  # test.

  @T-UC-018-inv-226-2-holds @invariant @BR-RULE-226
  Scenario Outline: BR-RULE-226 INV-2 holds -- snapshot unavailable surfaces machine-readable reason -- <reason>
    Given the authenticated principal has an approved creative whose snapshot is unavailable due to <condition>
    When the Buyer Agent sends a list_creatives request with include_snapshot true
    Then the response is compliant with the list_creatives spec
    And the creative in the response omits the snapshot
    And the creative includes a snapshot_unavailable_reason of "<reason>"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json
    #
    # One row, not three. The enum's other two members name conditions this seller cannot
    # be in, and the enum says so itself: SNAPSHOT_TEMPORARILY_UNAVAILABLE is "Snapshot data
    # exists but is temporarily unavailable (e.g., cache miss, pipeline lag)" -- there is no
    # snapshot data and no pipeline for it to lag -- and SNAPSHOT_PERMISSION_DENIED is "The
    # caller lacks permission to view snapshot data for this entity", which needs a
    # per-entity snapshot permission this seller does not model. A row whose precondition
    # cannot be established grades nothing; both belong with the capability that would make
    # them reachable.

    Examples:
      | condition                             | reason                            |
      | the platform never supports snapshots | SNAPSHOT_UNSUPPORTED              |

  @T-UC-018-inv-226-3-holds @invariant @BR-RULE-226
  Scenario: BR-RULE-226 INV-3 holds -- no snapshot fields when include_snapshot omitted
    Given the authenticated principal has an approved creative whose snapshot is unavailable
    When the Buyer Agent sends a list_creatives request without specifying include_snapshot
    Then the response is compliant with the list_creatives spec
    And the creative in the response includes neither a snapshot nor a snapshot_unavailable_reason
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-partition-snapshot-unavailable @partition @snapshot-unavailable
  Scenario Outline: Snapshot unavailability disclosure -- <partition>
    Given the authenticated principal has an approved creative
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then the response is compliant with the list_creatives spec
    And <outcome>
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json

    # The three rows this outline lost are the two unreachable enum members (see the
    # BR-RULE-226 INV-2 note) and reason_not_in_enum, whose request_params cell named a
    # RESPONSE field: a buyer cannot send snapshot_unavailable_reason, so there was no
    # request for the row to make. That the seller never emits a non-enum reason is graded
    # on every row here by the compliance Then, which validates the response against
    # list-creatives-response.json and its $ref to the closed enum.
    Examples: Valid partitions
      | partition              | request_params                              | outcome                                                                    |
      | reason_unsupported     | include_snapshot true                       | creative has snapshot_unavailable_reason "SNAPSHOT_UNSUPPORTED"             |
      | snapshot_not_requested | no include_snapshot parameter               | neither snapshot nor snapshot_unavailable_reason present                    |

  @T-UC-018-boundary-snapshot-unavailable @boundary @snapshot-unavailable
  Scenario Outline: Snapshot unavailability boundary -- <boundary_point>
    Given the authenticated principal has an approved creative
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then the response is compliant with the list_creatives spec
    And <outcome>

    # Same two deletions as the partition outline above, for the same two reasons: the
    # PERMISSION_DENIED member names a condition this seller cannot enter, and the
    # SNAPSHOT_BROKEN row put a response field in a request.
    Examples: Boundary values
      | boundary_point                                                       | request_params                                | outcome                                                  |
      | snapshot_unavailable_reason='SNAPSHOT_UNSUPPORTED' (valid enum)       | include_snapshot true                         | snapshot_unavailable_reason is "SNAPSHOT_UNSUPPORTED"     |
      | include_snapshot omitted (neither snapshot nor reason present)        | no include_snapshot parameter                 | neither snapshot nor snapshot_unavailable_reason present  |

  @T-UC-018-ext-e @extension @ext-e @degradation
  Scenario: Snapshot unavailable -- the listing still succeeds, with the reason on every creative
    Given the authenticated principal has approved creatives
    When the Buyer Agent sends a list_creatives request with include_snapshot true
    Then the response is compliant with the list_creatives spec
    And the operation succeeds and returns the full creatives array
    And each creative includes a snapshot_unavailable_reason of "SNAPSHOT_UNSUPPORTED"
    # POST-S5: degraded result is explained, not silently dropped
    #
    # Was an outline over the three enum members, asserting a MIXED response: one affected
    # creative carrying a reason while "creatives with available snapshots still include
    # their snapshot". This seller cannot produce that mixture, because it supports
    # snapshots for no creative at all (see the BR-RULE-226 INV-2 note), so the degradation
    # is uniform: every creative carries the reason, and the listing still succeeds with the
    # full array rather than failing or dropping rows. That is the half of POST-S5 this
    # seller can be held to, and it is the half the extension exists for.

  @T-UC-018-storyboard-list-all-creatives-after-sync @schema-v3.1 @v3-1 @list-after-sync
  Scenario: List creatives with no filters returns the library including recently synced creatives
    Given the buyer recently synced three creatives identified by canonical format kinds via sync_creatives
    When the Buyer Agent sends list_creatives with no filters for the same account
    Then the response is compliant with the list_creatives spec
    And the creatives array should include each of the synced creatives
    And each creative entry should expose creative_id, name, format_kind, and status, and no format_id
    # creative_lifecycle list_and_filter / list_all: after sync_creatives,
    # list_creatives without filters returns the library for the account
    # including the synced items. "Each creative includes: creative_id, name,
    # format_kind, status". The creative that step lists was synced by format_kind
    # (sync_multiple sends format_kind: image), and list-creatives-response.json makes the
    # two identifiers a oneOf -- format_kind is "Canonical 3.2 path ... Mutually exclusive
    # with deprecated format_id", and format_id is "DEPRECATED in 3.2. Legacy named-format
    # path. New listed creatives use format_kind" -- so a canonically-synced creative is
    # listed by its kind and carries no format_id. The graded step lives inside the
    # `creative` protocol baseline storyboard; capabilities.py declares
    # supported_protocols=[media_buy] only, so this behavior is schema-grounded for us,
    # not storyboard-graded (matches the sibling scenarios in this file already tagged
    # @schema-v3.1 for the same undeclared-protocol reason).
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/creative/index.yaml phase=list_and_filter step=list_all
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-response.json pointer=/properties/creatives/items/oneOf

  @T-UC-018-storyboard-filter-by-format-id-object @schema-v3.1 @v3-1 @list-filter @format-id-object
  Scenario: List creatives filtered by a format_id object returns only creatives matching that {agent_url, id}
    Given the buyer has synced creatives in formats including {agent_url, "display_300x250"} and {agent_url, "video_30s"}
    When the Buyer Agent sends list_creatives with filters.format_ids carrying one format_id object {agent_url, "display_300x250"}
    Then the response is compliant with the list_creatives spec
    And the creatives array should only include creatives whose format_id matches both agent_url and id
    And the creatives array should NOT include creatives whose format_id has a different id even on the same agent_url
    # core/creative-filters.json format_ids: an array of core/format-id.json objects
    # (agent_url + id), "Filter legacy named-format creatives. Use format_kinds for
    # canonical libraries." -- `deprecated: true`, so this is the LEGACY filter, kept for
    # 3.x buyers; T-UC-018-filter-by-format-kinds grades the canonical one. Only creatives
    # whose format_id matches both fields are returned, and a filter shaped as a bare
    # string id is not an object and so not this filter. The storyboard's list_filtered
    # step filters by format_kinds and grades nothing here.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/creative-filters.json pointer=/properties/format_ids

  @T-UC-018-format-id-filter-canonicalizes-stored-agent-url @schema-v3.2 @v3-1 @list-filter @format-id-object
  Scenario: A creative stored under a respelled agent_url is still returned by a format_id filter naming the canonical form
    Given the buyer synced a creative whose format_id agent_url differs from the canonical form only in host case and a default port
    When the Buyer Agent sends list_creatives with filters.format_ids carrying that format_id in canonical form
    Then the response is compliant with the list_creatives spec
    And the creatives array should include the creative stored under the respelled agent_url
    And the creatives array should NOT include the creative stored under a genuinely different agent_url
    # core/format-id.json, on agent_url: "Callers comparing two `format-id` values MUST
    # canonicalize `agent_url` per the AdCP URL canonicalization rules before treating two
    # formats as the same." The filter IS such a caller, and the two sides of the comparison
    # routinely disagree byte-for-byte while naming one agent: what is STORED is the buyer's
    # own spelling, because sync_creatives owes that spelling back verbatim on readback.
    #
    # Falsifiable in both directions by construction: one creative is stored under the
    # respelled spelling (host case + an explicit default port, both of which the
    # canonicalization normalizes away) and must come back, and one is stored under a
    # genuinely different agent that no canonicalization makes equal and must not.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format-id.json property=agent_url

  @T-UC-018-format-id-filter-matches-the-canonical-shape @schema-v3.2 @list-filter @format-id-object
  Scenario Outline: A format_id filter returns the creative whose legacy reference normalizes to the filtered one - <filter>
    Given the buyer's library holds creatives on legacy format_ids "<matching>" and "<other>"
    When the Buyer Agent sends list_creatives with filters.format_ids naming "<filter>" at the standard creative agent
    Then the response is compliant with the list_creatives spec
    And the creatives array should contain exactly the creative synced on "<matching>"

    Examples:
      | filter          | matching              | other                |
      | display_300x250 | display_300x250_image | display_728x90_image |
      | video_30s       | video_pre_roll_30s    | video_15s            |

    # docs/creative/template-format-ids.mdx (3.2.7) § Compatibility: legacy ids are normalized
    # "to canonical declarations immediately and do all matching on the canonical shape", and
    # canonical-formats.mdx § Format matching vs product satisfaction: a legacy format_id
    # "resolves through ... the canonical mapping registry to a canonical declaration ... After
    # that projection, implementations compare the canonical shape and parameters, not the raw
    # (agent_url, id) pair". No stored creative spells the filter's id, so a filter comparing
    # the raw key returns neither creative and fails the exact set; the other creative is the
    # same canonical kind at other parameters, so a filter comparing the kind alone returns
    # both and fails it too.
    #
    #   * display_300x250: the SDK's size upgrade projects it and display_300x250_image to an
    #     image at 300x250, and display_728x90_image to an image at 728x90.
    #   * video_30s: the SDK upgrades none of the three ids. Only the registry's projection
    #     states their durations (video_hosted at duration_ms_exact 30000, 30000 and 15000), so
    #     a filter reading the SDK's equivalence, which compares ids after the size upgrade,
    #     returns nothing.
    #
    # The relation is EQUIVALENCE, which is the seller's choice for this filter: the pin's
    # creative-filters.json says only "Filter legacy named-format creatives", and a creative
    # is a format, not a product accepting one. Equivalence leaves a parameter one side does
    # not state open, so a filter naming the display_image template returns every display
    # image creative. Only the reference agent's ids are projected through the registry, whose
    # namespace the registry is written in; another agent's id is compared by its canonical
    # (agent_url, id) pair and the parameters it states.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/template-format-ids.mdx
    # @source repo=adcp ref=v3.2.7 path=docs/creative/canonical-formats.mdx
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/creative-filters.json pointer=/properties/format_ids
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/registries/v1-canonical-mapping.json

  @T-UC-018-filter-by-format-kinds @schema-v3.2 @list-filter @format-kind
  Scenario Outline: List creatives filtered by filters.format_kinds "<wanted>" returns only the creatives of that canonical kind
    Given the buyer synced creatives on canonical format_kinds "video_hosted" and "image"
    When the Buyer Agent sends list_creatives with filters.format_kinds ["<wanted>"]
    Then the response is compliant with the list_creatives spec
    And the creatives array should contain exactly the "<wanted>" creative, carrying that format_kind
    And the creatives array should NOT include the "<other>" creative
    And query_summary.filters_applied should report format_kinds "<wanted>"

    Examples: each kind in the library is the filtered one in turn
      | wanted       | other        |
      | video_hosted | image        |
      | image        | video_hosted |

    # core/creative-filters.json declares format_kinds as an array of
    # core/canonical-format-kind.json (minItems 1, uniqueItems true): "Filter by canonical
    # format kinds. Returns creatives matching any supplied kind." The sibling format_ids
    # is `deprecated: true` in the same file and points HERE -- "Use `format_kinds` for
    # canonical libraries" -- so this is the filter a 3.2 buyer is told to send, and the
    # one the seller accepted and silently ignored: an unfiltered whole-library answer,
    # with filters_applied not even naming the filter it dropped. That file grants an
    # explicit "SHOULD ignore" licence to three sales-agent-specific filters
    # (assigned_to_packages, media_buy_ids, unassigned) and to this one it grants none.
    #
    # BOTH rows, swapped, because one row is satisfied by a seller that hardcoded the
    # kind it happened to be developed against -- and by a seller that returns the whole
    # library whenever the library has one creative per kind. Swapping which kind is
    # filtered makes a constant answer wrong in one direction or the other.
    #
    # The outcome names three things because any one of them passes vacuously alone. The
    # exact-set assertion alone would pass on an empty array; the absence assertion alone
    # would pass on a broken query returning nothing; and filters_applied is the half
    # that makes the behaviour OBSERVABLE to the buyer (POST-S7) rather than merely
    # correct by coincidence.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/creative-filters.json pointer=/properties/format_kinds property=format_kinds
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/canonical-format-kind.json

  @T-UC-018-format-kinds-filter-is-a-union @schema-v3.2 @list-filter @format-kind
  Scenario: A format_kinds filter naming two kinds returns the creatives of both
    Given the buyer synced creatives on canonical format_kinds "video_hosted" and "image"
    When the Buyer Agent sends list_creatives with filters.format_kinds ["video_hosted", "image"]
    Then the response is compliant with the list_creatives spec
    And the creatives array should contain both the "video_hosted" and the "image" creative
    And query_summary.filters_applied should report format_kinds "video_hosted,image"

    # The word is ANY: core/creative-filters.json says format_kinds "Returns creatives
    # matching any supplied kind", so a multi-kind filter is a UNION and not an
    # intersection. Graded separately from the single-kind rows above because they cannot
    # see the difference -- a seller that conjoined the kinds (one condition per kind,
    # ANDed) answers every single-kind request correctly and answers this one with an
    # empty array, since no creative carries two kinds. minItems is 1, so a multi-kind
    # array is in-contract traffic rather than an edge case.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/creative-filters.json pointer=/properties/format_kinds property=format_kinds

  @T-UC-018-format-kinds-filter-matches-the-projected-kind @schema-v3.2 @list-filter @format-kind
  Scenario: A creative synced by a legacy format_id is returned by a format_kinds filter naming the kind that reference projects to
    Given the buyer synced a creative on legacy format_id "display_300x250" and was refused a creative on "audio_60s", which no registry pattern projects
    When the Buyer Agent sends list_creatives with filters.format_kinds ["image"]
    Then the response is compliant with the list_creatives spec
    And the creatives array should contain exactly the creative synced on "display_300x250"
    And that creative should be listed by format_id, carrying no format_kind
    And the library holds no creative for the reference that projects to no canonical kind

    # The both-arms rule, and it is the one a reasonable reading gets backwards.
    # registries/v1-canonical-mapping.json is "authoritative for v1 -> v2 projection
    # only", and this seller runs that order on the way IN: a creative synced by
    # `display_300x250` stores the kind the reference PROJECTS to (`image`), so it matches
    # a format_kinds filter naming `image` -- even though its wire entry carries
    # `format_id` and no `format_kind`, because list-creatives-response.json's item schema
    # declares the two as a oneOf whose arms forbid each other and the state mirrors the
    # INPUT. The identity is what a filter keys on; the arm is a wire concern. A seller
    # that returned only canonically-synced rows here would be treating `format_kind` as
    # an arm marker instead of as the identity.
    #
    # The second reference is the fail-closed half, and the OUTCOME IT NAMES MOVED. It
    # used to be a seeded ROW carrying `format_kind` NULL, and the claim was that a
    # creative "not KNOWN to be any kind matches no kind". That row is now unrepresentable:
    # `creatives.format_kind` is NOT NULL and `creative_identity_of` refuses a reference
    # with no canonical projection, so the write is REFUSED instead of stored unresolved
    # (salesagent-jzhea). Asserting the old absence-from-a-filter would mean seeding a
    # state this seller cannot reach, which grades a seller no deployment runs.
    #
    # So the sixth normative step is graded where it now fires -- at the WRITE -- and the
    # read-side claim becomes the consequence: the reference never enters the library, so
    # no kind filter can return it. That is strictly more than the old pair said. It names
    # the refusal AND the absence, and a seller that quietly stored kind=NULL again would
    # fail the first half rather than passing the second by accident.
    # The refused reference is `audio_60s` at the reference agent, which passes no step of
    # the registry's resolution order: the reference agent does not serve it (so there is no
    # declaration whose `canonical` step 2 could read, nor a shape for step 4) and the
    # registry's only audio glob is `audio_15s`. It used to be `audio_30s`, which stopped
    # being unprojectable when the reference agent at the pinned release began serving it
    # with `canonical: {kind: audio_hosted}` -- step 2 now projects it, and the write accepts.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/registries/v1-canonical-mapping.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/creative-filters.json pointer=/properties/format_kinds property=format_kinds
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-response.json pointer=/properties/creatives/items property=format_kind

  @T-UC-018-parameterized-format-id-roundtrip @schema-v3.2 @v3-1 @format-id-object
  Scenario: A creative synced with a parameterized format_id is listed with every declared parameter intact
    Given the buyer synced a creative on a format_id parameterized by width, height and pixel_ratio
    When the Buyer Agent sends list_creatives with no filters for the same account
    Then the response is compliant with the list_creatives spec
    And the listed creative's format_id should carry every parameter the buyer sent, pixel_ratio included
    # core/format-id.json declares width, height, duration_ms and pixel_ratio on the
    # reference object itself, and creative/list-creatives-response.json requires format_id
    # on every listed creative — so a listed format_id that dropped a parameter the buyer
    # sent names a DIFFERENT format than the one synced.
    #
    # pixel_ratio is named in the outcome on purpose rather than left to "every parameter":
    # it is the one a by-name readback dropped, because the reference was reassembled from
    # partial columns and only three of the four were copied. An outcome that said only
    # "parameters are preserved" passed throughout that defect.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format-id.json

  @T-UC-018-canonical-format-option-ref-roundtrip @schema-v3.2 @format-option-ref
  Scenario Outline: A creative synced by format_kind with a <scope>-scope format_option_ref is listed with that reference intact
    Given the buyer synced a creative on format_kind "image" with a <scope>-scope format_option_ref
    When the Buyer Agent sends list_creatives with no filters for the same account
    Then the response is compliant with the list_creatives spec
    And the listed creative's format_option_ref should equal the reference the buyer sent, scope included
    And the listed creative's format_option_ref should be valid against core/format-option-ref.json

    Examples: the two scopes the reference discriminates between
      | scope     |
      | product   |
      | publisher |

    # creative/list-creatives-response.json declares format_option_ref on every listed
    # creative as a $ref to core/format-option-ref.json — "the concrete canonical format
    # option this creative targets". That file is a oneOf with
    # `discriminator: {propertyName: "scope"}`, and BOTH arms name `scope` in `required`; the
    # product arm additionally declares `"publisher_domain": false`, so a product-scope
    # reference may not carry the member at all, not even as null. A reference that reaches
    # the buyer without its discriminator names no arm of the union, which is to say it
    # names no option.
    #
    # Both scopes are rows because the discriminator is the whole point: a seller that
    # stored only `{publisher_domain, format_option_id}` loses product-vs-publisher scope,
    # and the two are DIFFERENT options even when they share a format_option_id (a
    # publisher-catalog option and a product-local one are namespaced differently). A
    # single-row scenario on either scope would pass on a seller that hard-coded the other.
    #
    # The second outcome is what stops the first from being satisfiable by an echo: a
    # seller that round-tripped its own malformed document byte-for-byte would satisfy
    # "equals what the buyer sent" while serving a reference the buyer's own schema check
    # refuses. Graded against the pinned file, so the pin is the authority and not this text.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format-option-ref.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-response.json pointer=/properties/creatives/items property=format_option_ref

  @T-UC-018-fields-projection-keeps-canonical-identity @schema-v3.2 @list-creatives-fields @format-kind
  Scenario: A fields selection naming no format identity still lists a canonical creative by its format_kind
    Given the buyer synced a creative on format_kind "image" with a product-scope format_option_ref
    When the Buyer Agent sends a list_creatives request with fields ["creative_id"]
    Then the response is compliant with the list_creatives spec
    And only the selected and required fields in response
    And the listed creative should carry format_kind "image" and no format_id

    # creative/list-creatives-response.json requires five members on every listed creative
    # (creative_id, name, status, created_date, updated_date) and a oneOf over the format
    # identity: the named-format arm requires format_id, the canonical arm requires
    # format_kind, and each forbids the other. A `fields` selection cannot drop either
    # arm's member, because the item would then match neither. The fields boundary rows
    # above seed legacy creatives only, so they cannot see a projection that keeps
    # format_id and drops format_kind -- which leaves a canonical creative with no format
    # identity at all. canonical-formats-migration.mdx's completion checklist names the
    # library path as one that must preserve format_kind and format_option_ref, and the
    # request schema's `fields` description says the same of localization, which
    # "automatically includes ... the selected format identity".
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-response.json pointer=/properties/creatives/items
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json pointer=/properties/fields
    # @source repo=adcp ref=v3.2.7 path=docs/creative/canonical-formats-migration.mdx section="Completion checklist"

  @T-UC-018-storyboard-filter-by-concept-id @schema-v3.1 @v3-1 @list-filter @concept-id
  Scenario: List creatives filtered by concept_ids returns only creatives in that concept carrying concept_id and concept_name
    Given the authenticated principal has creatives grouped under concept "concept_summer_2026" and other creatives under different concepts
    When the Buyer Agent sends list_creatives with filters.concept_ids ["concept_summer_2026"]
    Then the response is compliant with the list_creatives spec
    And the creatives array should only include creatives belonging to concept "concept_summer_2026"
    And each returned creative should carry concept_id "concept_summer_2026" and a concept_name
    # v3.1 ADDED filter filters.concept_ids (array of concept-id strings, minItems 1).
    # Concepts group related creatives across sizes and formats. Satisfies the
    # User Intent "List creatives in this concept with their DCO variables" and the
    # INT-001 "concept" filter dimension. Each returned creative exposes concept_id
    # and concept_name (list-creatives-response.json creatives[].concept_id/concept_name).
    # creative_lifecycle: concept_ids filter scopes results to one concept; concept_id/concept_name exposed
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-request.json

  @T-UC-018-filter-boolean-flags @list-filter @boolean-filter @v3-1
  Scenario Outline: v3.1 boolean filter <flag> partitions the library
    Given the authenticated principal has both creatives matching and not matching <flag>
    When the Buyer Agent sends a list_creatives request with <flag> <value>
    Then the response is compliant with the list_creatives spec
    And <outcome>
    # v3.1 ADDED boolean CreativeFilters has_variables (DCO vs static) and
    # has_served (has served >=1 impression vs never served). Each partitions
    # the principal's library into the matching subset.

    # has_served LEFT this outline for the filter-semantics one, as an invalid partition.
    # Its two rows shared this Given, which for has_served promises a library where some
    # creatives have served: no column in this schema records a creative's impressions, so
    # that state cannot be seeded and the filter cannot be evaluated. The seller refuses it
    # rather than answering a different question, and the refusal is graded where the other
    # unanswerable filters are.
    Examples: Boolean filter partitions
      | flag          | value | outcome                                                          |
      | has_variables | true  | only creatives with dynamic variables (DCO) are returned          |
      | has_variables | false | only static creatives (no dynamic variables) are returned         |

  @T-UC-018-boundary-creative-status @boundary @creative-status
  Scenario Outline: Creative status filter boundary -- <boundary_point>
    Given the authenticated principal has creatives in statuses "processing", "approved", "rejected", "pending_review", "archived"
    When the Buyer Agent sends a list_creatives request with <request_params>
    Then the response is compliant with the list_creatives spec
    And <outcome>

    Examples: Boundary values
      | boundary_point                                                                  | request_params                          | outcome                                                          |
      | processing (first enum value)                                                   | statuses filter ["processing"]          | only processing creatives are returned                            |
      | archived (last enum value)                                                      | statuses filter ["archived"]            | only archived creatives are returned                              |
      | ["approved", "rejected"] (multi-status array)                                   | statuses filter ["approved", "rejected"] | only approved and rejected creatives are returned                 |
      | Not provided (default excludes archived; or seller has no review lifecycle)     | no statuses filter                      | all non-archived creatives are returned (archived excluded by default) |
      | deleted (not in CreativeStatus enum)                                            | statuses filter ["deleted"]             | error "INVALID_REQUEST" with suggestion                          |

  @T-UC-018-boundary-sandbox-response @boundary @sandbox @br-rule-209
  Scenario Outline: Sandbox response semantics boundary -- <boundary_point>
    Given the authenticated principal has creatives
    And the request targets <account_kind>
    When the Buyer Agent sends a list_creatives request
    Then the response is compliant with the list_creatives spec
    And <outcome>

    Examples: Boundary values
      | boundary_point                                  | account_kind                          | outcome                                          |
      | sandbox: true in response (sandbox account)     | a sandbox account                     | the response should include sandbox equals true   |
      | sandbox absent in response (production account) | a production account                  | the response should not include a sandbox field   |
      | sandbox omitted for an explicitly non-sandbox account | a production account                | the response should not include a sandbox field   |
    # The third row asked for an explicit `sandbox: false`. The pinned response schema
    # defines only what TRUE means — "this response contains simulated data from sandbox
    # mode" — and attaches no obligation to the false case, while BR-RULE-209 INV-5 says a
    # production account's response omits the field. So on this seller's wire an
    # explicitly-non-sandbox account is indistinguishable from one that says nothing: both
    # omit the flag, which is what the row now asserts.
