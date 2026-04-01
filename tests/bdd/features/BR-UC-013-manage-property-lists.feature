# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-013 Manage Property Lists
  As a Buyer
  I want to create, discover, retrieve, update, and delete curated property lists
  So that I can reference them in targeting requests to filter inventory by publisher properties

  # Postconditions verified:
  #   POST-S1: Buyer has created a new property list and received list_id and auth_token
  #   POST-S2: Buyer can retrieve full property list configuration and optionally resolved identifiers by ID
  #   POST-S3: Buyer can discover all property lists matching optional filters (principal, name substring)
  #   POST-S4: Buyer has updated an existing property list (full replacement semantics)
  #   POST-S5: Buyer has deleted a property list that was not in active use
  #   POST-S6: Application context from the request is echoed unchanged in the response
  #   POST-S7: Auth token is returned only at creation time (one-shot secret)
  #   POST-F1: System state is unchanged on failure
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Application context is still echoed when possible
  #   POST-F4: When a list is not found, the error references the provided list_id
  #
  # Rules: BR-RULE-070..078, BR-RULE-043 (10 rules, 33 invariants)
  # Extensions: A (create), B (get), C (update), D (delete), E (not found), F (access denied), G (in use)
  # Error codes: LIST_NOT_FOUND, LIST_ACCESS_DENIED, LIST_IN_USE, TENANT_ERROR,
  #   NAME_REQUIRED, BASE_PROPERTIES_INVALID_SOURCE_TYPE, BASE_PROPERTIES_INVALID_SOURCE,
  #   FILTERS_COUNTRIES_REQUIRED, FILTERS_CHANNELS_REQUIRED, FILTERS_INVALID_COUNTRY_CODE,
  #   FILTERS_INVALID_CHANNEL, PAGINATION_MAX_RESULTS_INVALID, PAGINATION_MAX_RESULTS_EXCEEDED,
  #   PAGINATION_INVALID_CURSOR, WEBHOOK_URL_NOT_ALLOWED_ON_CREATE, WEBHOOK_URL_INVALID_FORMAT,
  #   RESOLVE_INVALID_TYPE, AUTH_TOKEN_MISSING

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And the Buyer is authenticated with a valid principal_id


  @T-UC-013-001 @list @happy-path @post-s3 @post-s6 @br-rule-078
  Scenario Outline: List property lists via <transport> -- returns all tenant lists
    Given the tenant has 3 property lists with different names
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent sends a list_property_lists request
    Then the response contains a lists array with 3 items
    And each list includes list_id, name, and metadata
    And the request context is echoed in the response
    # POST-S3: Buyer discovers all property lists matching optional filters
    # POST-S6: Application context echoed unchanged

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-013-002 @list @no-filter @post-s3 @partition @boundary @br-rule-078
  Scenario: List property lists -- no filters returns all tenant lists
    Given the tenant has 5 property lists
    When the Buyer Agent sends a list_property_lists request with no filters
    Then the response contains a lists array with 5 items
    # BR-RULE-078 INV-1: No filter parameters -> all property lists for tenant returned
    # @bva boundary: name_contains filter with empty string (equivalent to no filter)

  @T-UC-013-003 @list @filter @post-s3 @partition @br-rule-078
  Scenario: List property lists -- principal filter returns only owned lists
    Given the tenant has a property list owned by principal "agent-alpha"
    And the tenant has a property list owned by principal "agent-beta"
    When the Buyer Agent filters by principal "agent-alpha"
    Then the response contains 1 property list
    And the list is owned by principal "agent-alpha"
    # BR-RULE-078 INV-2: principal filter -> exact match on owner

  @T-UC-013-004 @list @filter @post-s3 @partition @br-rule-078
  Scenario: List property lists -- name_contains filter returns substring matches
    Given the tenant has a property list named "Q4 Travel Inclusion"
    And the tenant has a property list named "TRAVEL Exclusion List"
    And the tenant has a property list named "Sports Campaign"
    When the Buyer Agent filters by name_contains "travel"
    Then the response contains 2 property lists
    And the Sports Campaign list is not included
    # BR-RULE-078 INV-3: name_contains -> case-insensitive substring match
    # @bva boundary: name_contains filter with substring

  @T-UC-013-005 @list @filter @post-s3 @partition @br-rule-078
  Scenario: List property lists -- combined principal and name_contains filters
    Given the tenant has a property list "Travel A" owned by "agent-alpha"
    And the tenant has a property list "Travel B" owned by "agent-beta"
    And the tenant has a property list "Sports C" owned by "agent-alpha"
    When the Buyer Agent filters by principal "agent-alpha" and name_contains "Travel"
    Then the response contains 1 property list
    And the list is "Travel A"
    # DR-5: principal AND name_contains filters applied together

  @T-UC-013-006 @list @filter @post-s3 @partition @br-rule-078
  Scenario Outline: List property lists -- <filter_type> with no match returns empty
    Given the tenant has 3 property lists owned by principal "agent-alpha" with names containing "Sports"
    When the Buyer Agent filters by <filter_field> "<filter_value>"
    Then the response contains an empty lists array
    And the response is not an error
    # BR-RULE-078: Filters with no matching results return empty (not error)

    Examples:
      | filter_type        | filter_field  | filter_value  |
      | principal_no_match | principal     | agent-unknown |
      | name_no_match      | name_contains | Travel        |

  @T-UC-013-007 @list @post-s3 @boundary @br-rule-078
  Scenario: List property lists -- tenant with zero lists returns empty array
    Given the tenant has 0 property lists
    When the Buyer Agent sends a list_property_lists request with no filters
    Then the response contains an empty lists array
    And the response is not an error
    # Boundary: empty state for list operation

  @T-UC-013-008 @list @tenant @post-s3 @br-rule-071
  Scenario: List property lists -- only own tenant lists returned
    Given tenant-A has 3 property lists
    And tenant-B has 2 property lists
    When the Buyer Agent authenticated as tenant-A sends a list_property_lists request
    Then the response contains a lists array with 3 items
    And no lists from tenant-B appear in the response
    # BR-RULE-071 INV-1: All operations scoped to authenticated tenant

  @T-UC-013-009 @create @happy-path @post-s1 @post-s7 @post-s6 @br-rule-074
  Scenario Outline: Create property list via <transport> -- returns list_id and auth_token
    When the Buyer Agent creates a property list via <transport> with:
    | name | My Programmatic TV List |
    Then the response contains a generated list_id
    And the response contains an auth_token
    And the auth_token is a non-empty string
    And the request context is echoed in the response
    # @bva boundary: create response contains auth_token
    # @bva boundary: webhook_url absent from create request
    # POST-S1: list_id assigned; POST-S7: auth_token one-shot; POST-S6: context echoed

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-013-010 @create @tenant @post-s1 @br-rule-071
  Scenario: Create property list -- list is scoped to creating tenant
    When the Buyer Agent authenticated as tenant-A creates a property list with name "Tenant A List"
    Then the list is stored under tenant-A
    And a list_property_lists request from tenant-B does not include this list
    # BR-RULE-071 INV-3: Created list associated with creating principal's tenant

  @T-UC-013-011 @create @post-s1
  Scenario: Create property list -- duplicate name is allowed
    Given the tenant has a property list with name "My Travel List"
    When the Buyer Agent creates a property list with name "My Travel List"
    Then the response contains a generated list_id
    And the new list_id is different from the existing list
    # Names are not unique constraints; list_id is the unique identifier
    # --- Create: name field validation ---

  @create @validation @partition @boundary @br-rule-072
  Scenario Outline: Create property list -- name <name_type> is valid
    When the Buyer Agent creates a property list with name <name_value>
    Then the response contains a generated list_id

    Examples:
      | name_type        | boundary_point                     | name_value                      |
      | typical_name     | name present with non-empty value  | My Programmatic TV Campaigns    |
      | minimal_name     | name present as single character   | A                               |
      | name_with_spaces | name with spaces and punctuation   | Q4 2026 - Travel Exclusion List |

  @create @ext-a @validation @error @partition @boundary @br-rule-072 @post-f2
  Scenario Outline: Create property list -- name <name_type> is rejected
    When the Buyer Agent creates a property list with name <name_value>
    Then the error code should be "NAME_REQUIRED"
    And the error should include "suggestion" field
    # --- Create: base_properties field validation ---

    Examples:
      | name_type    | boundary_point                   | name_value |
      | missing_name | name absent from create request  | (absent)   |
      | empty_name   | name present as empty string     |            |

  @create @validation @partition @boundary @br-rule-072
  Scenario Outline: Create property list -- base_properties <source_type> is valid
    When the Buyer Agent creates a property list with name "Source Test" and base_properties <source_value>
    Then the response contains a generated list_id
    # BR-RULE-072 INV-1..4: Discriminated union source types

    Examples:
      | source_type    | boundary_point                                     | source_value                                                                                                                                                                  |
      | absent         | base_properties absent (entire database mode)      | (not provided)                                                                                                                                                                |
      | publisher_tags | single publisher_tags entry with non-empty tags    | [{"selection_type": "publisher_tags", "publisher_domain": "raptive.com", "tags": ["sports"]}]                                                                                 |
      | publisher_ids  | single publisher_ids entry with non-empty ids      | [{"selection_type": "publisher_ids", "publisher_domain": "raptive.com", "property_ids": ["prop-001"]}]                                                                        |
      | identifiers    | single identifiers entry with non-empty array      | [{"selection_type": "identifiers", "identifiers": [{"type": "domain", "value": "example.com"}]}]                                                                              |
      | mixed_sources  | multiple mixed source types                        | [{"selection_type": "publisher_tags", "publisher_domain": "a.com", "tags": ["t1"]}, {"selection_type": "identifiers", "identifiers": [{"type": "domain", "value": "b.com"}]}] |

  @T-UC-013-014 @create @ext-a @validation @error @partition @boundary @br-rule-072 @post-f1 @post-f2
  Scenario Outline: Create property list -- base_properties <source_type> is rejected
    When the Buyer Agent creates a property list with name "Invalid Source" and base_properties <source_value>
    Then the error code should be "<error_code>"
    And the error should include "suggestion" field
    # --- Create: filters field validation ---

    Examples:
      | source_type            | boundary_point                                                    | source_value                                                                           | error_code                          |
      | unknown_selection_type | unknown selection_type value                                      | [{"selection_type": "unknown", "publisher_domain": "a.com"}]                           | BASE_PROPERTIES_INVALID_SOURCE_TYPE |
      | missing_selection_type | missing selection_type discriminator                              | [{"publisher_domain": "raptive.com"}]                                                  | BASE_PROPERTIES_INVALID_SOURCE_TYPE |
      | empty_tags             | publisher_tags with empty tags array (minItems=1 violation)       | [{"selection_type": "publisher_tags", "publisher_domain": "a.com", "tags": []}]        | BASE_PROPERTIES_INVALID_SOURCE      |
      | empty_identifiers      | identifiers with empty identifiers array (minItems=1 violation)   | [{"selection_type": "identifiers", "identifiers": []}]                                 | BASE_PROPERTIES_INVALID_SOURCE      |
      | missing_domain         | missing publisher_domain in publisher_tags entry                  | [{"selection_type": "publisher_ids", "property_ids": ["prop-001"]}]                    | BASE_PROPERTIES_INVALID_SOURCE      |

  @create @validation @partition @boundary @br-rule-073
  Scenario Outline: Create property list -- filters <filter_type> is valid
    When the Buyer Agent creates a property list with name "Filter Test" and filters <filter_value>
    Then the response contains a generated list_id
    # BR-RULE-073 INV-1,4: countries_all and channels_any are optional; filters object itself is optional

    Examples:
      | filter_type               | boundary_point                                                         | filter_value                                                                                        |
      | absent                    | filters absent                                                         | (not provided)                                                                                      |
      | minimal_valid_filters     | filters with only property_types (no countries_all or channels_any)    | {"property_types": ["website"]}                                                                     |
      | filters_without_countries | filters with channels_any only (no countries_all)                      | {"channels_any": ["display"]}                                                                       |
      | filters_without_channels  | filters with countries_all only (no channels_any)                      | {"countries_all": ["US"]}                                                                           |
      | filters_with_both         | filters with countries_all [1 country] and channels_any [1 channel]    | {"countries_all": ["US"], "channels_any": ["display"]}                                              |
      | full_filters              | filters with multiple countries and channels                           | {"countries_all": ["US", "CA"], "channels_any": ["display", "video"], "property_types": ["website"]} |

  @create @ext-a @validation @error @partition @boundary @br-rule-073 @post-f1 @post-f2
  Scenario Outline: Create property list -- filters <filter_type> is rejected
    When the Buyer Agent creates a property list with name "Invalid Filter" and filters <filter_value>
    Then the error code should be "<error_code>"
    And the error should include "suggestion" field
    # Note: countries_all and channels_any are OPTIONAL (omission is valid).
    # These errors only trigger for minItems=1 violation (empty array) or invalid values.

    Examples:
      | filter_type           | boundary_point                                  | filter_value                                                   | error_code                   |
      | countries_empty_array | countries_all empty array                       | {"countries_all": [], "channels_any": ["display"]}             | FILTERS_COUNTRIES_REQUIRED   |
      | channels_empty_array  | channels_any empty array                        | {"countries_all": ["US"], "channels_any": []}                  | FILTERS_CHANNELS_REQUIRED    |
      | invalid_country_code  | country code in lowercase (not ISO format)      | {"countries_all": ["usa"], "channels_any": ["display"]}        | FILTERS_INVALID_COUNTRY_CODE |
      | country_1char         | country code 1 character (too short)            | {"countries_all": ["U"], "channels_any": ["display"]}          | FILTERS_INVALID_COUNTRY_CODE |
      | invalid_channel       | unknown channel value                           | {"countries_all": ["US"], "channels_any": ["unknown_channel"]} | FILTERS_INVALID_CHANNEL      |

  @T-UC-013-017 @create @ext-a @validation @error @boundary @br-rule-075 @post-f1 @post-f2
  Scenario: Create property list -- webhook_url on create is rejected
    When the Buyer Agent creates a property list with name "Webhook Test" and webhook_url "https://example.com/hook"
    Then the error code should be "WEBHOOK_URL_NOT_ALLOWED_ON_CREATE"
    And the error should include "suggestion" field
    # BR-RULE-075 INV-3: webhook_url in create request -> rejected
    # @bva boundary: webhook_url provided in create request (schema violation)

  @T-UC-013-018 @get @happy-path @partition @post-s2 @post-s6
  Scenario Outline: Get property list via <transport> -- returns full configuration and resolved identifiers
    Given an existing property list "list-abc" with base_properties and filters
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent sends a get_property_list request for "list-abc"
    Then the response contains the full list metadata
    And the response contains resolved identifiers
    And the response includes resolved_at timestamp
    And the response includes cache_valid_until timestamp
    And the request context is echoed in the response
    # @bva boundary: list_id of existing list owned by requesting tenant
    # POST-S2: full configuration with resolved identifiers
    # --- Get: resolve field ---

    Examples:
      | transport | list_id_partition |
      | MCP       | existing_own_list |
      | REST      | existing_own_list |

  @T-UC-013-019 @get @resolution @partition @boundary @br-rule-077
  Scenario Outline: Get property list -- resolve <resolve_state> triggers identifier resolution
    Given an existing property list "list-abc" with base_properties matching 50 identifiers
    When the Buyer Agent sends a get_property_list request for "list-abc" <resolve_param>
    Then the response contains an identifiers array
    And the response includes resolved_at timestamp
    # BR-RULE-077 INV-1: resolve=true or omitted -> filters applied, identifiers returned

    Examples:
      | resolve_state          | boundary_point                        | resolve_param              |
      | resolve_true_explicit  | resolve=true (explicit)               | with resolve=true          |
      | omitted_default        | resolve absent (defaults to true)     | without specifying resolve |

  @T-UC-013-021 @get @resolution @partition @boundary @br-rule-077
  Scenario: Get property list -- resolve=false returns metadata only
    Given an existing property list "list-abc"
    When the Buyer Agent sends a get_property_list request for "list-abc" with resolve=false
    Then the response contains the full list metadata
    And the response does not contain an identifiers array
    # BR-RULE-077 INV-2: resolve=false -> no identifiers returned
    # @bva boundary: resolve=false (metadata-only)

  @T-UC-013-022 @get @ext-b @resolution @error @partition @boundary @br-rule-077 @post-f2
  Scenario: Get property list -- resolve as non-boolean is rejected
    Given an existing property list "list-abc"
    When the Buyer Agent sends a get_property_list request for "list-abc" with resolve="yes"
    Then the error code should be "RESOLVE_INVALID_TYPE"
    And the error should include "suggestion" field
    # @bva boundary: resolve as string 'true' (type mismatch)

  @T-UC-013-023 @get @resolution @pagination @br-rule-077
  Scenario: Get property list -- pagination with resolve=false has no effect
    Given an existing property list "list-abc"
    When the Buyer Agent sends a get_property_list request for "list-abc" with resolve=false and pagination {"max_results": 100}
    Then the response contains the full list metadata
    And the response does not contain an identifiers array
    And no error is returned
    # DR-5: resolve=false makes pagination parameters accepted but no-op
    # @bva boundary: pagination with resolve=false (no-op)
    # --- Get: resolution filter semantics ---

  @T-UC-013-024 @get @resolution @br-rule-073
  Scenario: Get property list -- resolution applies AND on countries_all, OR on channels_any
    Given an existing property list with filters {"countries_all": ["US", "CA"], "channels_any": ["display", "video"]}
    And the property catalog has property P1 with data for US and CA supporting display
    And the property catalog has property P2 with data for US only supporting video
    And the property catalog has property P3 with data for US and CA supporting social only
    When the Buyer Agent sends a get_property_list request with resolve=true
    Then the resolved identifiers include P1
    And the resolved identifiers do not include P2 or P3
    # BR-RULE-073 INV-2+3: countries_all = AND logic, channels_any = OR logic
    # P2 fails countries_all (missing CA); P3 fails channels_any (social not in [display,video])

  @T-UC-013-025 @get @resolution @br-rule-072
  Scenario: Get property list -- base_properties narrows resolution scope
    Given an existing property list with base_properties [{"selection_type": "publisher_tags", "publisher_domain": "a.com", "tags": ["sports"]}] and no filters
    And the property catalog has properties from "a.com" tagged "sports" (P1, P2)
    And the property catalog has properties from "b.com" tagged "sports" (P3)
    When the Buyer Agent sends a get_property_list request with resolve=true
    Then the resolved identifiers include P1 and P2
    And the resolved identifiers do not include P3
    # BR-RULE-072 INV-1: base_properties present -> only specified sources; absent -> entire catalog

  @T-UC-013-026 @get @resolution @br-rule-072 @br-rule-073
  Scenario: Get property list -- absent base_properties and filters means unconstrained resolution
    Given an existing property list with no base_properties and no filters
    And the property catalog has 100 properties across various countries and channels
    When the Buyer Agent sends a get_property_list request with resolve=true
    Then the resolved identifiers include properties from the entire catalog
    # BR-RULE-072 INV-1: Absent base_properties -> entire catalog
    # BR-RULE-073 INV-4: Absent filters -> no country/channel constraints

  @T-UC-013-028 @get @resolution @boundary @br-rule-077
  Scenario: Get property list -- resolution returns empty when no properties match
    Given an existing property list with filters {"countries_all": ["ZZ"], "channels_any": ["display"]}
    And no properties in the catalog have data for country "ZZ"
    When the Buyer Agent sends a get_property_list request with resolve=true
    Then the response contains an empty identifiers array
    And the response is not an error
    And the response includes resolved_at timestamp
    # Boundary: filters match nothing in catalog
    # --- Get: pagination ---

  @get @pagination @partition @boundary @br-rule-077
  Scenario Outline: Get property list -- pagination <pagination_type> is valid
    Given an existing property list "list-abc" with base_properties matching 5000 identifiers
    When the Buyer Agent sends a get_property_list request for "list-abc" with pagination <pagination_value>
    Then the response contains resolved identifiers
    And the number of identifiers is at most <expected_max>
    # BR-RULE-077 INV-3: max_results boundaries (min=1, default=1000, max=10000)

    Examples:
      | pagination_type     | boundary_point                                             | pagination_value       | expected_max |
      | absent              | pagination absent (resolve=true, default 1000 identifiers) | (not provided)         | 1000         |
      | max_results_min     | max_results=1 (minimum)                                    | {"max_results": 1}     | 1            |
      | max_results_default | max_results=1000 (default)                                 | {"max_results": 1000}  | 1000         |
      | max_results_max     | max_results=10000 (maximum)                                | {"max_results": 10000} | 10000        |

  @T-UC-013-030 @get @pagination @partition @boundary @post-s2 @br-rule-077
  Scenario Outline: Get property list -- <pagination_state> retrieves next page of identifiers
    Given an existing property list "list-abc" with base_properties matching 2500 identifiers
    When the Buyer Agent sends a get_property_list request for "list-abc" with pagination {"max_results": 1000}
    Then the response contains 1000 identifiers
    And the response includes a pagination cursor
    When the Buyer Agent sends a get_property_list request for "list-abc" with the cursor from the previous response
    Then the response contains 1000 identifiers
    And the identifiers are distinct from the first page
    # BR-RULE-077 INV-4: Response cursor -> next request retrieves next page

    Examples:
      | pagination_state | boundary_point                |
      | cursor_provided  | cursor from previous response |

  @get @ext-b @pagination @error @partition @boundary @br-rule-077 @post-f2
  Scenario Outline: Get property list -- pagination <pagination_type> is rejected
    Given an existing property list "list-abc"
    When the Buyer Agent sends a get_property_list request for "list-abc" with pagination <pagination_value>
    Then the error code should be "<error_code>"
    And the error should include "suggestion" field

    Examples:
      | pagination_type      | boundary_point                   | pagination_value                   | error_code                      |
      | max_results_zero     | max_results=0 (below minimum)    | {"max_results": 0}                 | PAGINATION_MAX_RESULTS_INVALID  |
      | max_results_negative | max_results=-1 (negative)        | {"max_results": -1}                | PAGINATION_MAX_RESULTS_INVALID  |
      | max_results_exceeded | max_results=10001 (above maximum) | {"max_results": 10001}            | PAGINATION_MAX_RESULTS_EXCEEDED |
      | invalid_cursor       | invalid cursor value             | {"cursor": "invalid-cursor-value"} | PAGINATION_INVALID_CURSOR       |

  @T-UC-013-032 @update @happy-path @post-s4 @post-s6
  Scenario Outline: Update property list via <transport> -- returns updated list
    Given an existing property list "list-abc" with name "Original Name"
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent updates property list "list-abc" via <transport> with name "Updated Name"
    Then the response contains the updated list object
    And the list name is "Updated Name"
    And the request context is echoed in the response
    # POST-S4: full replacement semantics; POST-S6: context echoed

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-013-033 @update @post-s4 @br-rule-075
  Scenario: Update property list -- provided field completely replaces existing value
    Given an existing property list "list-abc" with base_properties [{"selection_type": "publisher_tags", "publisher_domain": "old.com", "tags": ["old"]}]
    When the Buyer Agent updates property list "list-abc" with base_properties [{"selection_type": "identifiers", "identifiers": [{"type": "domain", "value": "new.com"}]}]
    Then the list base_properties is entirely [{"selection_type": "identifiers", "identifiers": [{"type": "domain", "value": "new.com"}]}]
    And no trace of the old publisher_tags entry remains
    # BR-RULE-075 INV-1: Full replacement semantics

  @T-UC-013-034 @update @post-s4 @br-rule-075
  Scenario: Update property list -- omitted fields retain existing values (including no-op)
    Given an existing property list "list-abc" with name "Original" and description "Keep this"
    When the Buyer Agent updates property list "list-abc" with name "Changed" and no description field
    Then the list name is "Changed"
    And the list description is still "Keep this"
    When the Buyer Agent updates property list "list-abc" with no fields provided
    Then the list name is still "Changed"
    And the list description is still "Keep this"
    # BR-RULE-075 INV-2: Omitted fields are not cleared; empty update is a no-op
    # Verify no-op case: update with no fields at all
    # --- Update: webhook_url ---

  @update @validation @partition @boundary @br-rule-075
  Scenario Outline: Update property list -- webhook_url <webhook_type> is valid
    Given an existing property list "list-abc" with webhook_url <initial_webhook>
    When the Buyer Agent updates property list "list-abc" with webhook_url <new_webhook>
    Then the list webhook_url is <expected_webhook>

    Examples:
      | webhook_type    | boundary_point                                                  | initial_webhook | new_webhook                  | expected_webhook             |
      | set_first_time  | webhook_url set for first time via update (valid URI)            | (not set)       | https://example.com/webhook  | https://example.com/webhook  |
      | change_url      | webhook_url changed to a different valid URI via update          | https://old.com | https://new.example.com/hook | https://new.example.com/hook |
      | clear_via_empty | webhook_url set to empty string in update (removes webhook)     | https://old.com |                              | (not set)                    |

  @T-UC-013-037 @update @ext-c @validation @error @boundary @br-rule-075 @post-f2
  Scenario: Update property list -- webhook_url with invalid URI format is rejected
    Given an existing property list "list-abc"
    When the Buyer Agent updates property list "list-abc" with webhook_url "not-a-uri"
    Then the error code should be "WEBHOOK_URL_INVALID_FORMAT"
    And the error should include "suggestion" field
    # @bva boundary: webhook_url set to non-URI string in update
    # --- Update: validation (same rules as create; representative samples only) ---

  @update @ext-c @validation @error @partition @br-rule-072 @br-rule-073 @post-f1 @post-f2
  Scenario Outline: Update property list -- <field> <error_type> is rejected
    Given an existing property list "list-abc"
    When the Buyer Agent updates property list "list-abc" with <field> <invalid_value>
    Then the error code should be "<error_code>"
    And the error should include "suggestion" field

    Examples:
      | field           | error_type             | invalid_value                                           | error_code                          |
      | name            | empty_string           |                                                         | NAME_REQUIRED                       |
      | base_properties | unknown_selection_type | [{"selection_type": "unknown"}]                         | BASE_PROPERTIES_INVALID_SOURCE_TYPE |
      | filters         | countries_empty_array  | {"countries_all": [], "channels_any": ["display"]}      | FILTERS_COUNTRIES_REQUIRED          |

  @T-UC-013-039 @delete @happy-path @post-s5 @post-s6
  Scenario Outline: Delete property list via <transport> -- confirms deletion
    Given an existing property list "list-abc" not referenced by any media buy
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent deletes property list "list-abc"
    Then the response contains deleted=true
    And the response echoes the list_id "list-abc"
    And the request context is echoed in the response
    # POST-S5: list deleted; POST-S6: context echoed

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-013-040 @delete @ext-d @post-s5 @br-rule-076
  Scenario: Delete property list -- list is no longer retrievable; repeat delete returns LIST_NOT_FOUND
    Given an existing property list "list-abc" not referenced by any media buy
    When the Buyer Agent deletes property list "list-abc"
    Then the response contains deleted=true
    When the Buyer Agent sends a get_property_list request for "list-abc"
    Then the error code should be "LIST_NOT_FOUND"
    When the Buyer Agent deletes property list "list-abc" again
    Then the error code should be "LIST_NOT_FOUND"
    And the error references list_id "list-abc"
    And the error should include "suggestion" field
    # Verify retrieval fails
    # Verify idempotency: second delete also returns LIST_NOT_FOUND

  @T-UC-013-042 @error @ext-e @partition @post-f1 @post-f2 @post-f4 @br-rule-076
  Scenario Outline: <operation> property list -- LIST_NOT_FOUND when list_id does not exist
    When the Buyer Agent sends a <operation> request for nonexistent list_id "list-does-not-exist"
    Then the error code should be "LIST_NOT_FOUND"
    And the error references list_id "list-does-not-exist"
    And the error should include "suggestion" field
    # BR-RULE-076 INV-2: list_id not matching any tenant list -> LIST_NOT_FOUND
    # @bva boundary: list_id that does not exist at all
    # Representative: get (read) + delete (mutating). Update shares same check.
    # POST-F4: Error references the provided list_id

    Examples:
      | operation | list_id_partition   |
      | get       | nonexistent_list_id |
      | delete    | nonexistent_list_id |

  @T-UC-013-043 @error @ext-e @tenant @post-f1 @post-f4 @br-rule-071
  Scenario: Get property list -- cross-tenant list_id appears as LIST_NOT_FOUND
    Given tenant-B has a property list "list-tenant-b"
    When the Buyer Agent authenticated as tenant-A sends a get request for "list-tenant-b"
    Then the error code should be "LIST_NOT_FOUND"
    And the error references list_id "list-tenant-b"
    And the error should include "suggestion" field
    # BR-RULE-071 INV-2: Cross-tenant access returns NOT_FOUND (not ACCESS_DENIED)
    # @bva boundary: list_id of a list in another tenant (same ID, different tenant)

  @T-UC-013-044 @error @ext-e @context-echo @post-f3 @br-rule-043
  Scenario: Get property list -- LIST_NOT_FOUND still echoes context
    When the Buyer Agent sends a get_property_list request for "list-nonexistent" with context {"trace_id": "abc123"}
    Then the error code should be "LIST_NOT_FOUND"
    And the error should include "suggestion" field
    And the response context is {"trace_id": "abc123"}
    # POST-F3: Application context echoed even on error
    # --- Extension F: LIST_ACCESS_DENIED ---

  @T-UC-013-045 @error @ext-f @post-f1 @post-f2 @br-rule-070
  Scenario Outline: <operation> property list -- LIST_ACCESS_DENIED when principal lacks permission
    Given an existing property list "list-restricted" with restricted access
    When an unauthorized principal sends a <operation> request for "list-restricted"
    Then the error code should be "LIST_ACCESS_DENIED"
    And the error should include "suggestion" field
    # --- Extension G: LIST_IN_USE ---

    Examples:
      | operation |
      | get       |
      | update    |

  @T-UC-013-047 @error @ext-g @delete @post-f1 @post-f2 @br-rule-076
  Scenario: Delete property list -- LIST_IN_USE when referenced by active media buy
    Given an existing property list "list-active" referenced by an active media buy
    When the Buyer Agent deletes property list "list-active"
    Then the error code should be "LIST_IN_USE"
    And the error should include "suggestion" field
    When the Buyer Agent sends a get_property_list request for "list-active"
    Then the response contains the full list metadata
    # BR-RULE-076 INV-3: Delete blocked when list is in active use
    # @bva boundary: valid list_id in delete request when list has active media buy references
    # POST-F1: list NOT deleted -- verify it persists
    # Note: POST-F3 context echo on error is tested by T-UC-013-044 (LIST_NOT_FOUND).
    # Same echo behavior applies to LIST_ACCESS_DENIED and LIST_IN_USE per BR-RULE-043.

  @T-UC-013-050 @auth @error @post-f1 @post-f2 @br-rule-070
  Scenario Outline: <operation> property list -- unauthenticated request rejected
    When an unauthenticated Buyer Agent sends a <operation> property list request
    Then the error code should be "LIST_ACCESS_DENIED"
    And the error should include "suggestion" field
    # BR-RULE-070 INV-1: No valid credentials -> LIST_ACCESS_DENIED
    # Representative sample: create (mutating), list (read-all), get (read-one)

    Examples:
      | operation |
      | create    |
      | list      |
      | get       |

  @T-UC-013-051 @auth @error @post-f1 @post-f2 @br-rule-070
  Scenario Outline: <operation> property list -- valid credentials but unresolvable tenant
    Given no tenant can be resolved from the request context
    When the Buyer Agent sends a <operation> property list request
    Then the error code should be "TENANT_ERROR"
    And the error should include "suggestion" field
    # BR-RULE-070 INV-2: Credentials valid but tenant unresolvable -> TENANT_ERROR
    # Representative sample: create (mutating), list (read-all), delete (mutating + needs list_id)
    # --- Auth token for seller resolution ---

    Examples:
      | operation |
      | create    |
      | list      |
      | delete    |

  @T-UC-013-052 @auth @br-rule-070
  Scenario Outline: Reference property list -- auth_token <token_state> for seller resolution
    Given a property list "list-abc" with auth_token "correct-token"
    When a seller references "list-abc" with auth_token <provided_token>
    Then <expected_outcome>
    # BR-RULE-070 INV-4: auth_token match/mismatch/missing determines seller access

    Examples:
      | token_state | provided_token  | expected_outcome                                |
      | correct     | "correct-token" | the seller can fetch the list for resolution    |
      | mismatch    | "wrong-token"   | the seller cannot fetch the list for resolution |

  @T-UC-013-053 @auth @error @br-rule-070 @post-f2
  Scenario: Reference property list -- auth_token missing when seller tries to resolve
    Given a property list "list-abc" exists and requires an auth_token for seller resolution
    When a seller references "list-abc" without providing an auth_token
    Then the error code should be "AUTH_TOKEN_MISSING"
    And the error should include "suggestion" field

  @T-UC-013-054 @auth @post-s7 @br-rule-074
  Scenario Outline: <operation> property list -- auth_token absent from non-create response
    Given an existing property list "list-abc"
    When the Buyer Agent sends a <operation>_property_list request for "list-abc"
    Then the response does not contain an auth_token field
    # BR-RULE-074 INV-2: Only create returns auth_token
    # @bva boundary: get response does not contain auth_token
    # @bva boundary: update response does not contain auth_token

    Examples:
      | operation |
      | get       |
      | update    |
      | delete    |

  @T-UC-013-055 @list @auth @post-s7 @br-rule-074
  Scenario: List property lists -- auth_token absent from list response items
    Given the tenant has 3 property lists
    When the Buyer Agent sends a list_property_lists request
    Then no list object in the response contains an auth_token field
    # @bva boundary: list response does not contain auth_token for any list
    # Note: BR-RULE-074 INV-3 (lost auth_token -> no recovery) is a documented constraint,
    # not a testable scenario. The auth_token absence from get/update/delete is verified by T-UC-013-054.
    # Lost token requires list recreation -- this is a protocol-level documentation point.

  @T-UC-013-057 @context-echo @post-s6 @br-rule-043
  Scenario Outline: <operation> property list -- context present is echoed unchanged
    When the Buyer Agent sends a <operation> property list request with context {"trace_id": "t-001", "campaign": "q4"}
    Then the response context is {"trace_id": "t-001", "campaign": "q4"}
    # BR-RULE-043 INV-1: Request includes context -> response includes identical context

    Examples:
      | operation |
      | create    |
      | list      |
      | get       |

  @T-UC-013-057b @context-echo @post-s6 @br-rule-043
  Scenario: Create property list -- context absent means context omitted from response
    When the Buyer Agent sends a create property list request without context
    Then the response does not contain a context field
    # BR-RULE-043 INV-2: No context in request -> no context in response
    # Note: Error-path context echo covered in Group 6 (T-UC-013-044).

  @T-UC-013-058 @create @error @post-f1
  Scenario: Create property list -- failed create does not persist any list
    When the Buyer Agent creates a property list with name (absent)
    Then the error code should be "NAME_REQUIRED"
    And the error should include "suggestion" field
    When the Buyer Agent sends a list_property_lists request
    Then no new list was added

  @T-UC-013-059 @update @error @post-f1
  Scenario: Update property list -- failed update does not modify list state
    Given an existing property list "list-abc" with name "Original"
    When the Buyer Agent updates property list "list-abc" with filters {"countries_all": [], "channels_any": ["display"]}
    Then the error code should be "FILTERS_COUNTRIES_REQUIRED"
    And the error should include "suggestion" field
    When the Buyer Agent sends a get_property_list request for "list-abc"
    Then the list name is still "Original"
    And no fields were modified
    # Note: POST-F1 for delete covered by T-UC-013-047 (list persists after LIST_IN_USE)

  @T-UC-013-060 @precondition
  Scenario: Any operation -- Seller Agent unavailable returns connection error
    Given the Seller Agent is not operational
    When the Buyer Agent sends a list_property_lists request
    Then the request fails with a connection or service unavailable error
    # PRE-C1 violation

  @T-UC-013-061 @precondition
  Scenario Outline: <transport> operation -- without established connection fails
    Given a Seller Agent is operational and accepting requests
    But the Buyer Agent has not established a <transport> connection
    When the Buyer Agent attempts to call list_property_lists
    Then the request fails with a transport error
    # PRE-MCP1/PRE-A2A1 violation

    Examples:
      | transport |
      | MCP       |
      | A2A       |

  @T-UC-013-062 @create @get @update @delete @lifecycle
  Scenario: Property list CRUD lifecycle -- create, get, update, get, delete
    When the Buyer Agent creates a property list with name "Lifecycle Test" and base_properties [{"selection_type": "identifiers", "identifiers": [{"type": "domain", "value": "example.com"}]}]
    Then the response contains a generated list_id
    And the response contains an auth_token
    When the Buyer Agent sends a get_property_list request for the created list_id
    Then the list name is "Lifecycle Test"
    And the response does not contain an auth_token field
    When the Buyer Agent updates the list with name "Lifecycle Updated" and webhook_url "https://example.com/hook"
    Then the list name is "Lifecycle Updated"
    And the list webhook_url is "https://example.com/hook"
    When the Buyer Agent sends a get_property_list request for the same list_id
    Then the list name is "Lifecycle Updated"
    And the list base_properties is unchanged from creation
    When the Buyer Agent deletes the list
    Then the response contains deleted=true
    When the Buyer Agent sends a get_property_list request for the same list_id
    Then the error code should be "LIST_NOT_FOUND"
    # DR-5: Full operation dependency chain
    # Step 1: Create
    # Step 2: Get (verify creation)
    # Step 3: Update
    # Step 4: Get (verify omitted fields retained)
    # Step 5: Delete
    # Step 6: Verify deletion

