# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

Feature: BR-UC-028 Manage Collection Lists
  As a Buyer
  I want to create, discover, retrieve, update, and delete curated collection lists
  So that I can reference them in targeting requests to filter inventory by content (shows, films, podcasts, genres)

  # Postconditions verified:
  #   POST-S1: Buyer has created a new collection list and received list_id and auth_token
  #   POST-S2: Buyer can retrieve full collection list configuration and optionally resolved collections by ID
  #   POST-S3: Buyer can discover all collection lists matching optional filters (account, name substring)
  #   POST-S4: Buyer has updated an existing collection list (full replacement semantics)
  #   POST-S5: Buyer has deleted a collection list that was not in active use
  #   POST-S6: Application context from the request is echoed unchanged in the response
  #   POST-S7: Auth token is returned only at creation time (one-shot secret)
  #   POST-S8: Subscribers receive collection_list_changed webhook on resolved-list changes
  #   POST-F1: System state is unchanged on failure
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Application context is still echoed when possible
  #   POST-F4: When a list is not found, the error references the provided list_id
  #
  # Extensions: A (create), B (get), C (update), D (delete), E (not found), F (access denied), G (in use), H (webhook)
  # Error codes (v3.1 authoritative vocabulary): REFERENCE_NOT_FOUND, LIST_ACCESS_DENIED, LIST_IN_USE,
  #   INVALID_REQUEST, BASE_COLLECTION_SELECTION_TYPE_INVALID,
  #   BASE_COLLECTION_IDENTIFIERS_EMPTY, BASE_COLLECTION_GENRE_TAXONOMY_REQUIRED,
  #   BASE_COLLECTION_UNKNOWN_FIELD, PUBLISHER_DOMAIN_INVALID_PATTERN,
  #   FILTER_ARRAY_EMPTY, FILTER_UNKNOWN_FIELD, VALIDATION_ERROR,
  #   PAGINATION_MAX_RESULTS_BELOW_MIN, PAGINATION_MAX_RESULTS_OVER_CAP,
  #   WEBHOOK_URL_NOT_ALLOWED_ON_CREATE, WEBHOOK_URL_INVALID_FORMAT, WEBHOOK_URL_SSRF_BLOCKED,
  #   WEBHOOK_PAYLOAD_REQUIRED_FIELD_MISSING, IDEMPOTENCY_KEY_TOO_SHORT, IDEMPOTENCY_KEY_TOO_LONG,
  #   IDEMPOTENCY_KEY_INVALID_CHARS, IDEMPOTENCY_CONFLICT, IDEMPOTENCY_IN_FLIGHT,
  #   IDEMPOTENCY_EXPIRED, COLLECTION_LIST_UNKNOWN_FIELD, ACCOUNT_REQUIRED

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And the Buyer is authenticated with a valid principal_id


  @T-UC-028-main-list-happy @list @happy-path @post-s3 @post-s6
  Scenario Outline: List collection lists via <transport> -- returns all tenant lists
    Given the tenant has 3 collection lists with different names
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent sends a list_collection_lists request
    Then the response contains a lists array with 3 items
    And each list includes list_id, name, and metadata
    And the request context is echoed in the response
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-028-main-list-no-filter @list @no-filter @post-s3 @boundary
  Scenario: List collection lists -- no filters returns all tenant lists
    Given the tenant has 5 collection lists
    When the Buyer Agent sends a list_collection_lists request with no filters
    Then the response contains a lists array with 5 items
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-main-list-by-account @list @filter @post-s3
  Scenario: List collection lists -- account filter returns only owned lists
    Given the tenant has a collection list owned by account "acct-alpha"
    And the tenant has a collection list owned by account "acct-beta"
    When the Buyer Agent filters list_collection_lists by account "acct-alpha"
    Then the response contains 1 collection list
    And the list is owned by account "acct-alpha"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-main-list-by-name @list @filter @post-s3 @boundary
  Scenario: List collection lists -- name_contains filter returns substring matches
    Given the tenant has a collection list named "Premium Drama Series"
    And the tenant has a collection list named "DRAMA Exclusion List"
    And the tenant has a collection list named "Sports Podcasts"
    When the Buyer Agent filters list_collection_lists by name_contains "drama"
    Then the response contains 2 collection lists
    And the Sports Podcasts list is not included
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-main-list-empty @list @post-s3 @boundary
  Scenario: List collection lists -- tenant with zero lists returns empty array
    Given the tenant has 0 collection lists
    When the Buyer Agent sends a list_collection_lists request with no filters
    Then the response contains an empty lists array
    And the response is not an error
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-main-list-tenant-isolation @list @tenant @post-s3
  Scenario: List collection lists -- only own tenant lists returned
    Given tenant-A has 3 collection lists
    And tenant-B has 2 collection lists
    When the Buyer Agent authenticated as tenant-A sends a list_collection_lists request
    Then the response contains a lists array with 3 items
    And no lists from tenant-B appear in the response

  @T-UC-028-ext-a-happy @create @happy-path @post-s1 @post-s7 @post-s6
  Scenario Outline: Create collection list via <transport> -- returns list_id and auth_token
    When the Buyer Agent creates a collection list via <transport> with:
    | name | Premium Drama Series |
    Then the response contains a generated list_id
    And the response contains an auth_token
    And the auth_token is a non-empty string
    And the cache_duration_hours defaults to 168
    And the request context is echoed in the response
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-028-ext-a-tenant-scope @create @tenant @post-s1
  Scenario: Create collection list -- list is scoped to creating tenant
    When the Buyer Agent authenticated as tenant-A creates a collection list with name "Tenant A List"
    Then the list is stored under tenant-A
    And a list_collection_lists request from tenant-B does not include this list
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-a-name-missing @create @validation @error @post-f1 @post-f2
  Scenario: Create collection list -- missing name is rejected
    When the Buyer Agent creates a collection list with no name field
    Then the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And no collection list is created
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-a-idempotency-missing @create @validation @error @post-f2
  Scenario: Create collection list -- missing idempotency_key is rejected
    When the Buyer Agent creates a collection list with name "X" and no idempotency_key
    Then the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-a-idempotency-format @create @validation @error @boundary @post-f2
  Scenario Outline: Create collection list -- idempotency_key <case> is rejected
    When the Buyer Agent creates a collection list with name "X" and idempotency_key <key_value>
    Then the error code should be "<error_code>"
    And the error should include "suggestion" field
    # boundary: 15-char key (below min), 256-char key (above max), invalid char
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | case        | key_value                                                                                                                                  | error_code                     |
      | too_short   | abc123                                                                                                                                     | IDEMPOTENCY_KEY_TOO_SHORT      |
      | too_long    | aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | IDEMPOTENCY_KEY_TOO_LONG       |
      | invalid_chr | abc 123 invalid spaces here xx                                                                                                             | IDEMPOTENCY_KEY_INVALID_CHARS  |

  @T-UC-028-ext-a-idempotency-replay @create @idempotency @post-s1
  Scenario: Create collection list -- replay of same idempotency_key returns cached response with replayed=true
    Given the Buyer Agent has previously created a collection list with idempotency_key "k-abc-1234567890abcd"
    When the Buyer Agent creates a collection list with the same idempotency_key "k-abc-1234567890abcd"
    Then the response returns the original list_id
    And the response includes replayed equal to true
    And no new collection list is created
    # --- Create: base_collections discriminated union validation ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-a-base-collections-valid @create @validation @boundary
  Scenario Outline: Create collection list -- base_collections <source_type> is valid
    When the Buyer Agent creates a collection list with name "Source Test" and base_collections <source_value>
    Then the response contains a generated list_id
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | source_type           | boundary_point                                              | source_value                                                                                                                                |
      | absent                | base_collections absent (entire database mode)              | (not provided)                                                                                                                              |
      | distribution_ids      | single distribution_ids entry with non-empty array          | [{"selection_type": "distribution_ids", "identifiers": [{"type": "imdb_id", "value": "tt0944947"}]}]                                        |
      | publisher_collections | single publisher_collections entry with non-empty ids       | [{"selection_type": "publisher_collections", "publisher_domain": "publisher.com", "collection_ids": ["coll-001"]}]                          |
      | publisher_genres      | single publisher_genres with required genre_taxonomy        | [{"selection_type": "publisher_genres", "publisher_domain": "publisher.com", "genres": ["drama"], "genre_taxonomy": "iab_content_3.0"}]                 |
      | mixed_sources         | distribution_ids plus publisher_collections                 | [{"selection_type": "distribution_ids", "identifiers": [{"type": "imdb_id", "value": "tt1"}]}, {"selection_type": "publisher_collections", "publisher_domain": "p.com", "collection_ids": ["c1"]}] |

  @T-UC-028-ext-a-base-collections-invalid @create @validation @error @boundary @post-f1 @post-f2
  Scenario Outline: Create collection list -- base_collections <source_type> is rejected
    When the Buyer Agent creates a collection list with name "Invalid Source" and base_collections <source_value>
    Then the error code should be "<error_code>"
    And the error should include "suggestion" field
    # --- Create: filters validation ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | source_type                | boundary_point                                                  | source_value                                                                                                | error_code                              |
      | unknown_selection_type     | unknown selection_type value                                    | [{"selection_type": "unknown", "publisher_domain": "p.com"}]                                                | BASE_COLLECTION_SELECTION_TYPE_INVALID  |
      | missing_selection_type     | missing selection_type discriminator                            | [{"publisher_domain": "publisher.com"}]                                                                     | BASE_COLLECTION_SELECTION_TYPE_INVALID  |
      | empty_identifiers          | distribution_ids with empty identifiers array (minItems=1)      | [{"selection_type": "distribution_ids", "identifiers": []}]                                                 | BASE_COLLECTION_IDENTIFIERS_EMPTY       |
      | empty_collection_ids       | publisher_collections with empty collection_ids (minItems=1)    | [{"selection_type": "publisher_collections", "publisher_domain": "p.com", "collection_ids": []}]            | BASE_COLLECTION_IDENTIFIERS_EMPTY       |
      | empty_genres               | publisher_genres with empty genres array (minItems=1)           | [{"selection_type": "publisher_genres", "publisher_domain": "p.com", "genres": [], "genre_taxonomy": "iab_content_3.0"}] | BASE_COLLECTION_IDENTIFIERS_EMPTY       |
      | missing_taxonomy           | publisher_genres missing required genre_taxonomy                | [{"selection_type": "publisher_genres", "publisher_domain": "p.com", "genres": ["drama"]}]                  | BASE_COLLECTION_GENRE_TAXONOMY_REQUIRED |
      | unknown_field              | variant carries field outside its sub-schema (additionalProps)  | [{"selection_type": "distribution_ids", "identifiers": [{"type": "imdb_id", "value": "tt1"}], "bogus": 1}]   | BASE_COLLECTION_UNKNOWN_FIELD           |
      | bad_publisher_domain       | publisher_collections publisher_domain fails domain pattern     | [{"selection_type": "publisher_collections", "publisher_domain": "not a domain", "collection_ids": ["c1"]}] | PUBLISHER_DOMAIN_INVALID_PATTERN        |

  @T-UC-028-ext-a-filters-valid @create @validation @boundary
  Scenario Outline: Create collection list -- filters <filter_type> is valid
    When the Buyer Agent creates a collection list with name "Filter Test" and filters <filter_value>
    Then the response contains a generated list_id
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | filter_type          | boundary_point                                       | filter_value                                                                  |
      | absent               | filters absent                                       | (not provided)                                                                |
      | kinds_only           | filters with single kind                             | {"kinds": ["series"]}                                                         |
      | genres_include_only  | filters with genres_include + taxonomy               | {"genres_include": ["drama"], "genre_taxonomy": "iab_content_3.0"}                        |
      | both_include_exclude | include and exclude on same dimension                | {"genres_include": ["drama"], "genres_exclude": ["children"], "genre_taxonomy": "iab_content_3.0"} |
      | production_quality   | filters with production_quality tier                 | {"production_quality": ["professional"]}                                      |
      | full_filters         | full mix of dimensions                               | {"kinds": ["series", "publication"], "content_ratings_exclude": [{"system": "mpaa", "rating": "R"}], "production_quality": ["professional"]} |

  @T-UC-028-ext-a-filters-invalid @create @validation @error @boundary @post-f1 @post-f2
  Scenario Outline: Create collection list -- filters <filter_type> is rejected
    When the Buyer Agent creates a collection list with name "Invalid Filter" and filters <filter_value>
    Then the error code should be "<error_code>"
    And the error should include "suggestion" field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | filter_type           | boundary_point                            | filter_value                          | error_code           |
      | kinds_empty_array     | kinds empty array (minItems=1)            | {"kinds": []}                         | FILTER_ARRAY_EMPTY   |
      | unknown_kind          | kinds contains unknown enum value         | {"kinds": ["unknown_kind"]}           | VALIDATION_ERROR     |
      | unknown_quality       | production_quality unknown enum value     | {"production_quality": ["bogus"]}     | VALIDATION_ERROR     |
      | unknown_filter_field  | field outside the closed filters schema   | {"bogus_dimension": ["x"]}            | FILTER_UNKNOWN_FIELD |

  @T-UC-028-ext-a-webhook-on-create @create @validation @error @post-f1 @post-f2
  Scenario: Create collection list -- webhook_url on create is rejected
    When the Buyer Agent creates a collection list with name "Webhook Test" and webhook_url "https://example.com/hook"
    Then the error code should be "FIELD_NOT_PERMITTED"
    And the error should include "suggestion" field
    # --- Create: idempotency conflict (same key, divergent payload) ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-a-idempotency-conflict @create @idempotency @error @post-f1 @post-f2
  Scenario: Create collection list -- same idempotency_key with a different payload is rejected
    Given the Buyer Agent has previously created a collection list with idempotency_key "k-conf-1234567890abcd" and name "Original"
    When the Buyer Agent creates a collection list with the same idempotency_key "k-conf-1234567890abcd" and name "Different"
    Then the error code should be "IDEMPOTENCY_CONFLICT"
    And the error should include "suggestion" field
    And no new collection list is created
    # --- Create: account-ref resolution (default vs ambiguous) ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-a-account-default @create @account-ref @post-s1
  Scenario: Create collection list -- account omitted defaults to the agent's sole account
    Given the Buyer Agent has access to exactly one account "acct-solo"
    When the Buyer Agent creates a collection list with name "Defaulted" and no account field
    Then the response contains a generated list_id
    And the list is owned by account "acct-solo"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-a-account-ambiguous @create @account-ref @error @post-f1 @post-f2
  Scenario: Create collection list -- account omitted is rejected when agent has multiple accounts
    Given the Buyer Agent has access to accounts "acct-one" and "acct-two"
    When the Buyer Agent creates a collection list with name "Ambiguous" and no account field
    Then the error code should be "ACCOUNT_REQUIRED"
    And the error should include "suggestion" field
    And no collection list is created

  @T-UC-028-ext-b-happy @get @happy-path @post-s2 @post-s6
  Scenario Outline: Get collection list via <transport> -- returns full configuration and resolved collections
    Given an existing collection list "list-abc" with base_collections and filters
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent sends a get_collection_list request for "list-abc"
    Then the response contains the full list metadata
    And the response contains resolved collections
    And the response includes resolved_at timestamp
    And the response includes cache_valid_until timestamp
    And the request context is echoed in the response
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-028-ext-b-resolve-false @get @post-s2 @boundary
  Scenario: Get collection list -- resolve=false returns metadata only
    Given an existing collection list "list-meta-only" with base_collections and filters
    When the Buyer Agent sends a get_collection_list request for "list-meta-only" with resolve false
    Then the response contains the full list metadata
    And the response does not contain a collections array
    And the response does not contain a resolved_at timestamp
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-b-auth-token-not-reexposed @get @post-s2 @post-s7 @boundary
  Scenario: Get collection list -- response never re-exposes the auth_token
    Given an existing collection list "list-secret" created with an auth_token
    When the Buyer Agent sends a get_collection_list request for "list-secret"
    Then the response contains the full list metadata
    And the response does not contain an auth_token field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-b-pagination-default @get @pagination @post-s2 @boundary
  Scenario: Get collection list -- pagination defaults max_results to 1000
    Given an existing collection list "list-big" resolving to 5000 collections
    When the Buyer Agent sends a get_collection_list request for "list-big" with no pagination
    Then the response collections array contains at most 1000 items
    And the response pagination includes a cursor
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-b-pagination-max @get @pagination @post-s2 @boundary
  Scenario: Get collection list -- pagination max_results 10000 is accepted
    Given an existing collection list "list-big" resolving to 15000 collections
    When the Buyer Agent sends a get_collection_list request for "list-big" with max_results 10000
    Then the response collections array contains at most 10000 items
    And the response pagination includes a cursor
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-b-pagination-invalid @get @pagination @validation @error @boundary @post-f2
  Scenario Outline: Get collection list -- pagination max_results <case> is rejected
    Given an existing collection list "list-x"
    When the Buyer Agent sends a get_collection_list request for "list-x" with max_results <value>
    Then the error code should be "<error_code>"
    And the error should include "suggestion" field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | case      | value | error_code                       |
      | zero      | 0     | PAGINATION_MAX_RESULTS_BELOW_MIN |
      | too_large | 10001 | PAGINATION_MAX_RESULTS_OVER_CAP  |

  @T-UC-028-ext-b-coverage-gaps @get @coverage-gaps @post-s2
  Scenario: Get collection list -- coverage_gaps reported for collections missing filter metadata
    Given an existing collection list "list-gaps" with genres_include filter
    And some resolved collections lack a genre attribute
    When the Buyer Agent sends a get_collection_list request for "list-gaps"
    Then the response includes coverage_gaps for the "genre" dimension
    And the coverage_gaps map dimension to arrays of distribution identifiers
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-b-not-found @get @ext-e @error @post-f1 @post-f2 @post-f4
  Scenario: Get collection list -- list_id not found returns REFERENCE_NOT_FOUND
    Given no collection list exists with list_id "list-missing"
    When the Buyer Agent sends a get_collection_list request for "list-missing"
    Then the error code should be "REFERENCE_NOT_FOUND"
    And the error message references list_id "list-missing"
    And the error should include "suggestion" field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-b-access-denied @get @ext-f @error @post-f1 @post-f2
  Scenario: Get collection list -- principal without permission returns LIST_ACCESS_DENIED
    Given a collection list "list-other-acct" owned by account "acct-other"
    And the Buyer Agent is authenticated with no access to account "acct-other"
    When the Buyer Agent sends a get_collection_list request for "list-other-acct"
    Then the error code should be "LIST_ACCESS_DENIED"
    And the error should include "suggestion" field

  @T-UC-028-ext-c-happy @update @happy-path @post-s4 @post-s6
  Scenario Outline: Update collection list via <transport> -- full replacement applied
    Given an existing collection list "list-upd" with name "Old Name"
    When the Buyer Agent updates "list-upd" via <transport> with name "New Name"
    Then the response contains the updated list with name "New Name"
    And the updated_at timestamp is more recent than created_at
    And the request context is echoed in the response
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-028-ext-c-full-replace-base @update @post-s4 @boundary
  Scenario: Update collection list -- base_collections is fully replaced (not patched)
    Given an existing collection list "list-rep" with two base_collections entries
    When the Buyer Agent updates "list-rep" with base_collections replaced by a single entry
    Then the updated list contains exactly one base_collections entry
    And the prior entries are not retained
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-c-webhook-set @update @webhook @post-s4
  Scenario: Update collection list -- webhook_url can be set
    Given an existing collection list "list-wh" without webhook_url
    When the Buyer Agent updates "list-wh" with webhook_url "https://example.com/cb"
    Then the response contains webhook_url "https://example.com/cb"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-c-webhook-remove @update @webhook @post-s4 @boundary
  Scenario: Update collection list -- empty webhook_url removes subscription
    Given an existing collection list "list-wh" with webhook_url "https://example.com/cb"
    When the Buyer Agent updates "list-wh" with webhook_url ""
    Then the response shows the webhook_url field cleared
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-c-webhook-ssrf @update @webhook @error @post-f1 @post-f2
  Scenario: Update collection list -- webhook_url targeting a blocked host is rejected
    Given an existing collection list "list-ssrf" without webhook_url
    When the Buyer Agent updates "list-ssrf" with webhook_url "http://169.254.169.254/latest/meta-data"
    Then the error code should be "WEBHOOK_URL_SSRF_BLOCKED"
    And the error should include "suggestion" field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-c-webhook-invalid-format @update @webhook @error @post-f1 @post-f2
  Scenario: Update collection list -- malformed webhook_url is rejected
    Given an existing collection list "list-badwh" without webhook_url
    When the Buyer Agent updates "list-badwh" with webhook_url "not-a-valid-url"
    Then the error code should be "WEBHOOK_URL_INVALID_FORMAT"
    And the error should include "suggestion" field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-c-idempotency-replay @update @idempotency @post-s4
  Scenario: Update collection list -- replay returns cached response with replayed=true
    Given the Buyer Agent has previously updated "list-r" with idempotency_key "k-upd-xxxxxxxxxxxxxxxx"
    When the Buyer Agent updates "list-r" again with the same idempotency_key "k-upd-xxxxxxxxxxxxxxxx"
    Then the response is the original update response
    And the response includes replayed equal to true
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-c-not-found @update @ext-e @error @post-f1 @post-f2 @post-f4
  Scenario: Update collection list -- list_id not found returns REFERENCE_NOT_FOUND
    Given no collection list exists with list_id "list-missing"
    When the Buyer Agent updates "list-missing" with name "Whatever"
    Then the error code should be "REFERENCE_NOT_FOUND"
    And the error message references list_id "list-missing"
    And the error should include "suggestion" field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-c-access-denied @update @ext-f @error @post-f1 @post-f2
  Scenario: Update collection list -- principal without permission returns LIST_ACCESS_DENIED
    Given a collection list "list-other-acct" owned by account "acct-other"
    And the Buyer Agent is authenticated with no access to account "acct-other"
    When the Buyer Agent updates "list-other-acct" with name "Hijack"
    Then the error code should be "LIST_ACCESS_DENIED"
    And the error should include "suggestion" field
    And the list "list-other-acct" name is unchanged

  @T-UC-028-ext-d-happy @delete @happy-path @post-s5 @post-s6
  Scenario: Delete collection list -- returns deleted=true
    Given an existing collection list "list-del" with no media buy references
    When the Buyer Agent deletes "list-del"
    Then the response contains deleted equal to true
    And the response echoes list_id "list-del"
    And a subsequent get_collection_list for "list-del" returns REFERENCE_NOT_FOUND
    And the auth_token for "list-del" is revoked
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-d-idempotency-replay @delete @idempotency @post-s5
  Scenario: Delete collection list -- replay returns cached deletion with replayed=true
    Given the Buyer Agent has previously deleted "list-d" with idempotency_key "k-del-yyyyyyyyyyyyyyyy"
    When the Buyer Agent deletes "list-d" with the same idempotency_key "k-del-yyyyyyyyyyyyyyyy"
    Then the response contains deleted equal to true
    And the response includes replayed equal to true
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-d-not-found @delete @ext-e @error @post-f1 @post-f2 @post-f4
  Scenario: Delete collection list -- list_id not found returns REFERENCE_NOT_FOUND
    Given no collection list exists with list_id "list-missing"
    When the Buyer Agent deletes "list-missing"
    Then the error code should be "REFERENCE_NOT_FOUND"
    And the error message references list_id "list-missing"
    And the error should include "suggestion" field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-d-access-denied @delete @ext-f @error @post-f1 @post-f2
  Scenario: Delete collection list -- principal without permission returns LIST_ACCESS_DENIED
    Given a collection list "list-other-acct" owned by account "acct-other"
    And the Buyer Agent is authenticated with no access to account "acct-other"
    When the Buyer Agent deletes "list-other-acct"
    Then the error code should be "LIST_ACCESS_DENIED"
    And the error should include "suggestion" field
    And the list "list-other-acct" still exists
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-d-in-use @delete @ext-g @error @post-f1 @post-f2 @post-f4
  Scenario: Delete collection list -- in use by active media buy returns LIST_IN_USE
    Given an existing collection list "list-in-use" referenced by an active media buy
    When the Buyer Agent deletes "list-in-use"
    Then the error code should be "LIST_IN_USE"
    And the error message references list_id "list-in-use"
    And the error should include "suggestion" field
    And the list "list-in-use" still exists

  @T-UC-028-ext-h-delivery @webhook @ext-h @post-s8 @happy-path
  Scenario: collection_list_changed -- webhook delivered when resolved set changes
    Given a collection list "list-wh" has webhook_url "https://example.com/cb" registered
    And the resolved collection set for "list-wh" changes
    When the governance agent posts the collection_list_changed webhook
    Then the payload event field equals "collection_list_changed"
    And the payload includes list_id "list-wh"
    And the payload includes a change_summary object
    And the payload includes resolved_at and cache_valid_until timestamps
    And the request carries X-ADCP-Signature and X-ADCP-Timestamp headers
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-h-dedupe @webhook @ext-h @idempotency @happy-path
  Scenario: collection_list_changed -- recipient dedupes by idempotency_key per sender
    Given a collection_list_changed webhook with idempotency_key "wh-1234567890abcdef-aa" is received and processed
    When the governance agent retries the same webhook with the same idempotency_key
    Then the recipient discards the retry as a duplicate
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-h-header-missing @webhook @ext-h @error @abstract-rejection
  Scenario: collection_list_changed -- recipient rejects a webhook missing signature headers
    Given a collection_list_changed webhook is received with no X-ADCP-Signature header
    When the recipient validates the request headers
    Then the recipient rejects the request as unauthorized
    And the error should include "suggestion" field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

  @T-UC-028-ext-h-payload-missing-field @webhook @ext-h @error
  Scenario: collection_list_changed -- payload omitting a required field is rejected
    Given a collection_list_changed webhook body omits the required "list_id" field
    When the recipient validates the webhook payload
    Then the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion" field

  @T-UC-028-bva-idempotency-format @boundary @bva @create @idempotency
  Scenario Outline: idempotency_key format boundary -- <boundary_point>
    Given a create_collection_list request is prepared for the idempotency_key boundary
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point           | expected                                |
      | 16-char alphanumeric key | accepted                                |
      | 15-char key              | rejected: IDEMPOTENCY_KEY_TOO_SHORT     |
      | 256-char key             | rejected: IDEMPOTENCY_KEY_TOO_LONG      |
      | key containing space     | rejected: IDEMPOTENCY_KEY_INVALID_CHARS |

  @T-UC-028-bva-idempotency-policy @boundary @bva @create @idempotency
  Scenario Outline: idempotency_key conflict/replay policy boundary -- <boundary_point>
    Given a state-mutating collection list request carrying an idempotency_key
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                                                                          | expected                                       |
      | key absent (field omitted)                                                              | rejected: INVALID_REQUEST                      |
      | key present, no prior record for (seller, account, key)                                 | accepted (first execution)                     |
      | key present, prior record exists, payload byte-identical                                | cached response replayed=true                  |
      | key present, prior record exists, payload has one field changed                         | rejected: IDEMPOTENCY_CONFLICT                 |
      | key present, prior record exists, payload has all fields changed                        | rejected: IDEMPOTENCY_CONFLICT                 |
      | key present, prior request still in flight (not yet committed)                          | rejected: IDEMPOTENCY_IN_FLIGHT                |
      | key present, prior record exists, replay arrives exactly at replay_ttl_seconds boundary | rejected: IDEMPOTENCY_EXPIRED                  |

  @T-UC-028-bva-replayed @boundary @bva @idempotency
  Scenario Outline: collection_list replay boundary -- <boundary_point>
    Given a collection list operation is retried under an idempotency_key
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                                              | expected                       |
      | Fresh create request with new idempotency_key               | created replayed=false         |
      | Retry of same create request body with same idempotency_key | cached response replayed=true  |
      | Replay of update with same idempotency_key after success    | cached response replayed=true  |
      | Replay of delete with same idempotency_key after success    | cached deletion replayed=true  |

  @T-UC-028-bva-required-fields @boundary @bva @create @validation
  Scenario Outline: collection_list required-fields boundary -- <boundary_point>
    Given a collection list model or request is constructed at the required-fields boundary
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                                          | expected                                         |
      | Model emitted with list_id + name only                  | valid                                            |
      | Model emitted with unknown field (e.g., legacy attribute) | rejected: COLLECTION_LIST_UNKNOWN_FIELD         |
      | Create request with only `name`                         | rejected: INVALID_REQUEST                       |

  @T-UC-028-bva-auth-token @boundary @bva @post-s7
  Scenario Outline: auth_token surface boundary -- <boundary_point>
    Given a collection list operation that touches the one-shot auth_token
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                                                  | expected |
      | create_collection_list response includes auth_token             | present  |
      | get_collection_list response omits auth_token                   | omitted  |
      | list_collection_lists response omits auth_token for every list  | omitted  |
      | update_collection_list response omits auth_token                | omitted  |
      | delete_collection_list revokes the token immediately            | revoked  |

  @T-UC-028-bva-base-collections @boundary @bva @create @validation
  Scenario Outline: base_collections source boundary -- <boundary_point>
    Given a create_collection_list request with a base_collections selection
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                                                              | expected                                        |
      | selection_type=publisher_collections with publisher_domain + collection_ids | accepted                                        |
      | selection_type=publisher_genres WITH genre_taxonomy present                 | accepted                                        |
      | selection_type=publisher_genres MISSING genre_taxonomy                      | rejected: BASE_COLLECTION_GENRE_TAXONOMY_REQUIRED |
      | identifiers array empty under distribution_ids                              | rejected: BASE_COLLECTION_IDENTIFIERS_EMPTY     |

  @T-UC-028-bva-filters @boundary @bva @create @validation
  Scenario Outline: collection_list filters boundary -- <boundary_point>
    Given a create_collection_list request carrying a filters object
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                                      | expected                   |
      | genres_include + genres_exclude both provided        | accepted                  |
      | kinds=[series] AND production_quality=[professional] | accepted                  |
      | content_ratings_exclude=[] (empty array)             | rejected: FILTER_ARRAY_EMPTY |

  @T-UC-028-bva-publisher-domain @boundary @bva @create @validation
  Scenario Outline: publisher_domain pattern boundary -- <boundary_point>
    Given a base_collections entry carrying a publisher_domain
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                            | expected                            |
      | uppercase character anywhere               | rejected: PUBLISHER_DOMAIN_INVALID_PATTERN |
      | hyphen as first/last char of a label       | rejected: PUBLISHER_DOMAIN_INVALID_PATTERN |

  @T-UC-028-bva-cache-duration @boundary @bva @create
  Scenario Outline: cache_duration_hours default boundary -- <boundary_point>
    Given a create_collection_list request without an explicit cache_duration_hours
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                          | expected          |
      | cache_duration_hours omitted → default 168 | defaulted to 168 |

  @T-UC-028-bva-isolation @boundary @bva @account-ref @tenant
  Scenario Outline: collection_list isolation/account-resolution boundary -- <boundary_point>
    Given an agent invokes a collection list operation under account-resolution rules
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                                                            | expected                          |
      | create with single-account agent, account omitted                          | valid (defaults to sole account) |
      | get from agent that owns list_id within its only accessible account        | valid                            |
      | get from agent with multi-account access; list_id unique → account omitted | valid                            |
      | get from agent with multi-account access; list_id NOT unique → account omitted | rejected: ACCOUNT_REQUIRED |
      | cross-tenant request (different authenticated tenant than owner)           | rejected: REFERENCE_NOT_FOUND         |

  @T-UC-028-bva-list-filters @boundary @bva @list
  Scenario Outline: list_collection_lists filters boundary -- <boundary_point>
    Given a list_collection_lists request at the filter boundary
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                       | expected                  |
      | Empty request body                    | all tenant lists returned |
      | name_contains supplied as a string    | substring filter applied  |
      | name_contains supplied as an integer  | rejected: VALIDATION_ERROR |

  @T-UC-028-bva-coverage-gaps @boundary @bva @get @coverage-gaps
  Scenario Outline: get_collection_list coverage_gaps boundary -- <boundary_point>
    Given a get_collection_list request that may report coverage gaps
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                                                       | expected                  |
      | resolve=true, filters set, all collections have the filtered metadata | no coverage_gaps reported |
      | resolve=false (no resolution)                                         | no coverage_gaps reported |

  @T-UC-028-bva-pagination @boundary @bva @get @pagination
  Scenario Outline: get_collection_list pagination boundary -- <boundary_point>
    Given a get_collection_list request with a pagination parameter
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point    | expected                              |
      | max_results=10000 | accepted (at cap)                     |
      | max_results=10001 | rejected: PAGINATION_MAX_RESULTS_OVER_CAP |
      | pagination omitted | defaulted to 1000                    |

  @T-UC-028-bva-not-found @boundary @bva @error
  Scenario Outline: collection_list not-found boundary -- <boundary_point>
    Given a collection list lookup against a non-resolvable list_id
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    And the error should include "suggestion" field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                                              | expected                         |
      | get with unknown list_id within tenant                       | rejected: REFERENCE_NOT_FOUND        |
      | update with cross-tenant list_id                             | rejected: REFERENCE_NOT_FOUND        |
      | delete with cross-account list_id (no prior existence knowledge) | rejected: REFERENCE_NOT_FOUND    |
      | list_collection_lists with name_contains that matches nothing | empty lists array (not an error) |

  @T-UC-028-bva-access-denied @boundary @bva @error
  Scenario Outline: collection_list access-denied boundary -- <boundary_point>
    Given a collection list request from a principal without access
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    And the error should include "suggestion" field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                                       | expected                     |
      | Unauthenticated cross-tenant request                  | rejected: LIST_ACCESS_DENIED |
      | Cross-account request with no prior existence knowledge | rejected: LIST_ACCESS_DENIED |

  @T-UC-028-bva-update-replacement @boundary @bva @update
  Scenario Outline: update full-replacement boundary -- <boundary_point>
    Given an existing collection list and an update request
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                                                       | expected                          |
      | update sends base_collections=[A]; existing list had base_collections=[B,C] | result base_collections=[A] |
      | update sends filters={kinds:[series]}; existing filters had genres_include   | result filters={kinds:[series]} |
      | update sends only name=X; base_collections omitted from request      | base_collections unchanged (retains prior value) |

  @T-UC-028-bva-webhook-url @boundary @bva @webhook
  Scenario Outline: webhook_url surface boundary -- <boundary_point>
    Given a create or update collection list request carrying webhook_url
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                                              | expected                              |
      | create request omits webhook_url                            | accepted                              |
      | update request sets webhook_url for first time              | accepted                              |
      | update request changes webhook_url to a different valid URI | accepted                              |
      | update request sets webhook_url="" (empty string)           | subscription removed                  |
      | create request includes webhook_url (per UC BR-12 surface rule) | rejected: WEBHOOK_URL_NOT_ALLOWED_ON_CREATE |
      | update request sets webhook_url="not-a-uri"                 | rejected: WEBHOOK_URL_INVALID_FORMAT  |

  @T-UC-028-bva-in-use @boundary @bva @delete
  Scenario Outline: collection_list in-use boundary -- <boundary_point>
    Given a collection list with media buy references
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                        | expected             |
      | Delete with one referencing active buy | rejected: LIST_IN_USE |
      | Delete with multiple referencing buys   | rejected: LIST_IN_USE |
      | Update on a referenced list             | allowed              |

  @T-UC-028-bva-delete-refint @boundary @bva @delete
  Scenario Outline: delete referential-integrity boundary -- <boundary_point>
    Given a delete or update request against a (possibly referenced) collection list
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                                      | expected             |
      | delete request, zero active references               | deleted              |
      | delete request, one referencing buy in 'active' state | rejected: LIST_IN_USE |
      | delete request, all referencing buys in 'ended' state | deleted              |
      | update request on referenced list                    | allowed              |

  @T-UC-028-bva-webhook-event @boundary @bva @webhook
  Scenario Outline: collection_list_changed event const boundary -- <boundary_point>
    Given a collection_list_changed webhook body with an event field
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                  | expected                          |
      | event="collection_list_changed" | valid                            |
      | event="collection_changed"      | rejected (invalid event const)   |
      | event=null or event=42          | rejected (invalid event type)    |

  @T-UC-028-bva-webhook-signature @boundary @bva @webhook
  Scenario Outline: collection_list_changed signature/freshness boundary -- <boundary_point>
    Given a collection_list_changed webhook received with signature and timestamp headers
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/collection/list-collection-lists-request.json

    Examples:
      | boundary_point                                       | expected                  |
      | Valid HMAC + ts within 300s + fresh idempotency_key   | accepted (processed)      |
      | Same idempotency_key replayed by same sender          | discarded as duplicate    |
      | ts older than now-300s                                | rejected as stale         |
      | Signature does not match recomputed HMAC              | rejected as unauthorized  |

  @T-UC-028-bva-webhook-required @boundary @bva @webhook
  Scenario Outline: collection_list_changed required-payload boundary -- <boundary_point>
    Given a collection_list_changed webhook body validated against its schema
    When the boundary case <boundary_point> is evaluated
    Then the outcome is <expected>

    Examples:
      | boundary_point                  | expected                                       |
      | event="other_event"              | rejected (invalid event const)                |
      | resolved_at missing              | rejected: WEBHOOK_PAYLOAD_REQUIRED_FIELD_MISSING |
      | Top-level field 'extra_data' added | rejected (additionalProperties false)        |
