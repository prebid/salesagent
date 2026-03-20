# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-015 Track Conversions
  As a Buyer
  I want to configure event sources and log conversion events for attribution
  So that I can track purchases, leads, and other conversions for campaign optimization

  # Postconditions verified:
  #   POST-S1: Buyer has configured event sources and received per-source action results with setup instructions
  #   POST-S2: Buyer can discover all event sources on an account without modification
  #   POST-S3: Buyer has sent conversion events and received confirmation with events_received and events_processed counts
  #   POST-S4: Buyer knows the match quality score for the submitted event batch
  #   POST-S5: Buyer is informed of any partial failures with per-event error details
  #   POST-S6: Buyer is informed of any warnings
  #   POST-S7: Buyer can validate integration using test events without affecting production data
  #   POST-S8: Buyer-managed event sources not in a clean-sync request are removed when delete_missing is true
  #   POST-S9: Application context from the request is echoed unchanged in the response
  #   POST-F1: System state is unchanged on failure
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Application context is still echoed when possible
  #   POST-F4: When an event source is not found, the error references the provided event_source_id
  #
  # Rules: BR-RULE-105..112 (8 rules, 38 invariants)
  # Extensions: A (sync create/update), B (log events), C (test events), D (clean sync),
  #   E (EVENT_SOURCE_NOT_FOUND), F (ACCOUNT_NOT_FOUND), G (INVALID_EVENT_TYPE),
  #   H (INVALID_EVENT_TIME), I (BATCH_TOO_LARGE), J (MISSING_USER_MATCH),
  #   K (DUPLICATE_EVENT_SOURCE_ID), L (RATE_LIMITED)
  # Error codes: ACCOUNT_REQUIRED, ACCOUNT_NOT_FOUND, ACCOUNT_INVALID_FORMAT,
  #   EVENT_SOURCE_NOT_FOUND, EVENT_SOURCES_REQUIRED, EVENT_SOURCE_ID_REQUIRED,
  #   EVENT_SOURCE_ID_EXISTS, EVENT_ID_REQUIRED, EVENT_ID_TOO_SHORT, EVENT_ID_TOO_LONG,
  #   EVENT_TYPE_REQUIRED, EVENT_TYPE_INVALID_FORMAT, EVENT_TIME_REQUIRED,
  #   EVENT_TIME_INVALID_FORMAT, CUSTOM_EVENT_NAME_REQUIRED, EVENT_SOURCE_URL_REQUIRED,
  #   USER_MATCH_REQUIRED, HASHED_EMAIL_INVALID_FORMAT, HASHED_PHONE_INVALID_FORMAT,
  #   EVENTS_REQUIRED, BATCH_TOO_LARGE, INVALID_EVENT_TYPE, INVALID_EVENT_TIME,
  #   DUPLICATE_EVENT_SOURCE_ID, RATE_LIMITED

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And the Buyer is authenticated with a valid principal_id


  @T-UC-015-001 @main-flow @happy-path @post-s2 @post-s9 @br-rule-105 @br-rule-106
  Scenario Outline: Discover event sources via <transport> -- returns all sources on account
    Given an account "acc_acme_001" has 2 buyer-managed and 1 seller-managed event sources
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent sends a sync_event_sources request via <transport> with account "acc_acme_001" and no event_sources
    Then the response contains an event_sources array with 3 items
    And each source includes event_source_id, managed_by, and action "unchanged"
    And seller-managed sources have managed_by "seller"
    And buyer-managed sources have managed_by "buyer"
    And the request context is echoed in the response
    # POST-S2: Buyer can discover all event sources without modification
    # POST-S9: Application context echoed unchanged
    # BR-RULE-105 INV-5: Response contains both buyer-managed and seller-managed sources
    # BR-RULE-106 INV-1: discovery-only mode (event_sources omitted)

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-015-002 @main-flow @post-s2 @br-rule-105
  Scenario: Discover event sources -- account with no sources returns empty array
    Given an account "acc_new_001" has 0 event sources
    When the Buyer Agent sends a sync_event_sources request with account "acc_new_001" and no event_sources
    Then the response contains an empty event_sources array
    And the response is not an error

  @T-UC-015-003 @main-flow @post-s2 @br-rule-105
  Scenario: Discover event sources -- seller-managed sources are read-only
    Given an account has a seller-managed event source "platform_pixel"
    When the Buyer Agent discovers event sources on the account
    Then the source "platform_pixel" has managed_by "seller"
    And the source "platform_pixel" has action "unchanged"
    # BR-RULE-105 INV-6: Seller-managed sources cannot be updated or deleted by buyer

  @T-UC-015-ext-a-001 @extension @happy-path @post-s1 @post-s9 @br-rule-106
  Scenario Outline: Sync event sources via <transport> -- upsert creates and updates sources
    Given an account "acc_acme_001" has a buyer-managed source "src_existing" with name "Old Pixel"
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent sends a sync_event_sources request via <transport> with account "acc_acme_001" and event_sources:
    | event_source_id | name        |
    | src_existing    | New Pixel   |
    | src_new         | App SDK     |
    Then the response contains event_sources with source "src_existing" action "updated"
    And the response contains event_sources with source "src_new" action "created"
    And newly created sources include setup instructions
    And the request context is echoed in the response
    # POST-S1: Buyer configured event sources with per-source action results
    # POST-S9: Context echoed
    # BR-RULE-106 INV-2: upsert semantics

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-015-ext-a-002 @extension @post-s1 @br-rule-106
  Scenario: Sync event sources -- unchanged source retains action "unchanged"
    Given an account has a buyer-managed source "src_1" with name "Pixel" and event_types ["purchase"]
    When the Buyer Agent syncs event_sources with identical configuration for "src_1"
    Then the response contains event_sources with source "src_1" action "unchanged"

  @T-UC-015-ext-a-003 @extension @post-s1 @br-rule-105
  Scenario: Sync event sources -- seller-managed sources not modified by buyer sync
    Given an account has a seller-managed source "platform_attribution" and a buyer-managed source "src_pixel"
    When the Buyer Agent syncs event_sources including only "src_pixel"
    Then "platform_attribution" appears in response with managed_by "seller" and action "unchanged"
    And "src_pixel" appears with managed_by "buyer"
    # BR-RULE-105 INV-6: Seller-managed sources are immutable by buyer

  @T-UC-015-ext-b-001 @extension @happy-path @post-s3 @post-s4 @post-s9 @br-rule-112
  Scenario Outline: Log conversion events via <transport> -- all events processed successfully
    Given an event source "src_web" is configured on the account
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent logs 5 valid purchase events to "src_web"
    Then the response contains events_received 5
    And the response contains events_processed 5
    And the response does not contain partial_failures
    And the response contains match_quality between 0.0 and 1.0
    And the request context is echoed in the response
    # POST-S3: Confirmation with counts
    # POST-S4: Match quality score
    # POST-S9: Context echoed
    # BR-RULE-112 INV-1: Success branch
    # BR-RULE-112 INV-4: All events pass, no partial_failures

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-015-ext-b-002 @extension @post-s5 @br-rule-112
  Scenario: Log events -- partial failures reported per-event within success response
    Given an event source "src_web" is configured
    When the Buyer Agent logs 3 events where 1 has invalid event_type
    Then the response contains events_received 3
    And the response contains events_processed 2
    And the response contains partial_failures with 1 entry
    And the partial failure entry includes event_id, code "INVALID_EVENT_TYPE", and message
    # BR-RULE-112 INV-3: Partial failures are per-event within success branch
    # BR-RULE-112 INV-5: events_received = events_processed + partial_failures count
    # POST-S5: Buyer informed of partial failures

  @T-UC-015-ext-b-003 @extension @post-s5 @br-rule-112
  Scenario: Log events -- all events fail per-event validation
    Given an event source "src_web" is configured
    When the Buyer Agent logs 3 events all with invalid event_type
    Then the response contains events_received 3
    And the response contains events_processed 0
    And the response contains partial_failures with 3 entries
    And the response does NOT contain errors array
    # BR-RULE-112 INV-3: All per-event failures still uses success branch

  @T-UC-015-ext-b-004 @extension @post-s6 @br-rule-112
  Scenario: Log events -- warnings included in success response
    Given an event source "src_web" is configured
    When the Buyer Agent logs events without user_match identifiers
    Then the response includes warnings about low match quality
    And the match_quality score is low
    # POST-S6: Buyer informed of warnings (low match quality, missing recommended fields)

  @T-UC-015-ext-b-005 @extension @br-rule-107
  Scenario: Log events -- duplicate events silently deduplicated
    Given an event source "src_web" is configured
    And an event with event_id "evt_001" type "purchase" was previously sent to "src_web"
    When the Buyer Agent logs the same event (evt_001, purchase, src_web) again
    Then the event is counted as received but not double-processed
    And events_received reflects the count
    And events_processed does not double-count
    # BR-RULE-107 INV-1: Duplicate triple is silently deduplicated

  @T-UC-015-ext-b-006 @extension @br-rule-107
  Scenario: Log events -- same event_id with different event_type is distinct
    Given an event source "src_web" is configured
    When the Buyer Agent logs event_id "evt_001" with event_type "add_to_cart" to "src_web"
    And the Buyer Agent logs event_id "evt_001" with event_type "purchase" to "src_web"
    Then both events are processed as distinct conversions
    # BR-RULE-107 INV-2: Different event_type = distinct event

  @T-UC-015-ext-b-007 @extension @br-rule-107
  Scenario: Log events -- same event_id to different event_source_ids is distinct
    Given event sources "src_web" and "src_app" are configured on the account
    When the Buyer Agent logs event_id "evt_001" type "purchase" to "src_web"
    And the Buyer Agent logs event_id "evt_001" type "purchase" to "src_app"
    Then both events are processed as distinct conversions
    # BR-RULE-107 INV-3: Different event_source_id = distinct event

  @T-UC-015-ext-b-008 @extension @br-rule-112
  Scenario: Log events -- match_quality score boundaries
    Given an event source "src_web" is configured
    When the Buyer Agent logs events with full user_match identifiers
    Then the match_quality value is between 0.0 and 1.0 inclusive
    # BR-RULE-112 INV-6: match_quality between 0.0 and 1.0

  @T-UC-015-ext-c-001 @extension @happy-path @post-s7 @post-s3 @post-s9 @br-rule-111
  Scenario Outline: Log test events via <transport> -- processed but isolated from production
    Given an event source "src_web" is configured
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent logs events to "src_web" via <transport> with test_event_code "TEST_SESSION_001"
    Then the response contains events_received and events_processed counts
    And test events are validated using the same pipeline as production events
    And test events do NOT affect production attribution or reporting
    And the request context is echoed in the response
    # BR-RULE-111 INV-1: test_event_code present -> test events
    # BR-RULE-111 INV-2: same validation pipeline
    # BR-RULE-111 INV-3: no production impact
    # POST-S7: Integration validation without production impact
    # POST-S3: Confirmation counts
    # POST-S9: Context echoed

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-015-ext-c-002 @extension @br-rule-111
  Scenario: Log events without test_event_code -- treated as production
    Given an event source "src_web" is configured
    When the Buyer Agent logs events to "src_web" without test_event_code
    Then the events are treated as production events
    And the events affect attribution and reporting
    # BR-RULE-111 INV-4: test_event_code absent = production events

  @T-UC-015-ext-d-001 @extension @happy-path @post-s8 @post-s1 @post-s9 @br-rule-106
  Scenario Outline: Clean sync via <transport> -- unlisted buyer sources deleted
    Given an account has buyer-managed sources "src_keep" and "src_remove"
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent syncs event_sources via <transport> with delete_missing true and only "src_keep"
    Then the response shows "src_keep" with action "unchanged" or "updated"
    And the response shows "src_remove" with action "deleted"
    And the request context is echoed in the response
    # BR-RULE-106 INV-3: Clean-sync mode
    # POST-S8: Unlisted buyer-managed sources removed
    # POST-S1: Per-source action results
    # POST-S9: Context echoed

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-015-ext-d-002 @extension @br-rule-106
  Scenario: Clean sync -- seller-managed sources never deleted
    Given an account has buyer-managed source "src_buyer" and seller-managed source "src_seller"
    When the Buyer Agent syncs event_sources with delete_missing true and only "src_buyer"
    Then "src_seller" appears in response with managed_by "seller" and action "unchanged"
    And "src_seller" is NOT deleted
    # BR-RULE-106 INV-4: Seller-managed source immune to delete_missing

  @T-UC-015-ext-d-003 @extension @br-rule-106
  Scenario: Clean sync with empty account -- delete_missing removes all buyer sources
    Given an account has buyer-managed sources "src_a" and "src_b" and seller-managed source "src_platform"
    When the Buyer Agent syncs event_sources with delete_missing true and event_sources containing only a new source "src_new"
    Then "src_a" and "src_b" have action "deleted"
    And "src_new" has action "created"
    And "src_platform" has action "unchanged" and managed_by "seller"

  @T-UC-015-020 @extension @ext-e @error @post-f1 @post-f2 @post-f3 @post-f4
  Scenario Outline: Log events -- event source not found via <transport>
    Given no event source with id "nonexistent_src" is configured on the account
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent logs events to event_source_id "nonexistent_src"
    Then the operation should fail
    And the error code should be "EVENT_SOURCE_NOT_FOUND"
    And the error message should reference "nonexistent_src"
    And the error should include "suggestion" field
    And the suggestion should contain "sync_event_sources"
    And the request context is echoed in the response
    # POST-F1: System state unchanged
    # POST-F2: Error code EVENT_SOURCE_NOT_FOUND
    # POST-F3: Context echoed
    # POST-F4: Error references the provided event_source_id
    # --- Extension F: ACCOUNT_NOT_FOUND ---

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-015-021 @extension @ext-f @error @post-f1 @post-f2 @post-f3
  Scenario Outline: Sync event sources -- account not found via <transport>
    Given no account with id "nonexistent_acc" exists on the seller platform
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent sends a sync_event_sources request via <transport> with account "nonexistent_acc"
    Then the operation should fail
    And the error code should be "ACCOUNT_NOT_FOUND"
    And the error message should contain "nonexistent_acc"
    And the error should include "suggestion" field
    And the suggestion should contain "account_id"
    And the request context is echoed in the response
    # POST-F1: System state unchanged
    # POST-F2: Error code ACCOUNT_NOT_FOUND
    # POST-F3: Context echoed
    # --- Extension G: INVALID_EVENT_TYPE ---

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-015-022 @extension @ext-g @error @post-f2 @post-f3
  Scenario: Sync event sources -- invalid event type in event_types config
    Given an account "acc_1" exists
    When the Buyer Agent syncs event_sources with an event_types entry "nonstandard_type"
    Then the operation should fail
    And the error code should be "INVALID_EVENT_TYPE"
    And the error should include "suggestion" field
    And the suggestion should contain "supported_event_types"
    # INVALID_EVENT_TYPE at sync level (operation-level error)

  @T-UC-015-023 @extension @ext-g @error @post-f2 @post-f3
  Scenario: Log events -- invalid event_type in event is partial failure
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with event_type "nonstandard_type"
    Then the event appears in partial_failures with code "INVALID_EVENT_TYPE"
    And the partial failure includes a message
    And the error should include "suggestion" field
    And the suggestion should contain "supported_event_types"
    # INVALID_EVENT_TYPE at log level (partial failure)
    # --- Extension H: INVALID_EVENT_TIME ---

  @T-UC-015-024 @extension @ext-h @error @post-f2 @post-f3 @post-s5
  Scenario: Log events -- event_time outside attribution window is partial failure
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with event_time far in the past (outside attribution window)
    Then the event appears in partial_failures with code "INVALID_EVENT_TIME"
    And the partial failure includes a message indicating the acceptable range
    And the error should include "suggestion" field
    And the suggestion should contain "attribution window"
    # POST-S5: Partial failures reported per-event

  @T-UC-015-025 @extension @ext-h @error @post-f2 @post-f3
  Scenario: Log events -- event_time in the future is partial failure
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with event_time far in the future
    Then the event appears in partial_failures with code "INVALID_EVENT_TIME"
    And the error should include "suggestion" field
    And the suggestion should contain "attribution window"
    # --- Extension I: BATCH_TOO_LARGE ---

  @T-UC-015-026 @extension @ext-i @error @post-f1 @post-f2 @post-f3
  Scenario Outline: Log events -- batch too large via <transport>
    Given an event source "src_web" is configured
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent logs 10001 events to "src_web"
    Then the operation should fail
    And the error code should be "BATCH_TOO_LARGE"
    And the error message should contain "10,000"
    And the error should include "suggestion" field
    And the suggestion should contain "split"
    And no events are processed
    And the request context is echoed in the response
    # POST-F1: System state unchanged
    # POST-F2: Error code BATCH_TOO_LARGE
    # POST-F3: Context echoed
    # BR-RULE-110 INV-3: Over max rejected
    # --- Extension J: MISSING_USER_MATCH ---

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-015-027 @extension @ext-j @error @post-f2 @post-f3 @post-s5
  Scenario: Log events -- empty user_match is partial failure
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with user_match as empty object {}
    Then the event appears in partial_failures with code "MISSING_USER_MATCH"
    And the error should include "suggestion" field
    And the suggestion should contain "uids, hashed_email, hashed_phone, click_id"
    # BR-RULE-109 INV-1: user_match present but no valid identifier group
    # POST-S5: Partial failure reported
    # --- Extension K: DUPLICATE_EVENT_SOURCE_ID ---

  @T-UC-015-028 @extension @ext-k @error @post-f1 @post-f2 @post-f3
  Scenario Outline: Sync event sources -- duplicate event_source_id via <transport>
    Given an account "acc_1" exists
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent syncs event_sources via <transport> with two entries both having event_source_id "src_dup"
    Then the operation should fail
    And the error code should be "DUPLICATE_EVENT_SOURCE_ID"
    And the error message should reference "src_dup"
    And the error should include "suggestion" field
    And the suggestion should contain "unique"
    And no sources are modified
    And the request context is echoed in the response
    # BR-RULE-106 INV-6: Duplicate IDs rejected
    # POST-F1: System state unchanged
    # POST-F2: Error code DUPLICATE_EVENT_SOURCE_ID
    # POST-F3: Context echoed
    # --- Extension L: RATE_LIMITED ---

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-015-029 @extension @ext-l @error @post-f1 @post-f2 @post-f3
  Scenario Outline: Conversion tracking -- rate limited via <transport>
    Given the Buyer Agent has exceeded the rate limit for conversion tracking
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent sends a <operation> request
    Then the operation should fail
    And the error code should be "RATE_LIMITED"
    And the response contains retry_after field
    And the error should include "suggestion" field
    And the suggestion should contain "backoff"
    And the request context is echoed in the response
    # POST-F1: System state unchanged
    # POST-F2: Error code RATE_LIMITED
    # POST-F3: Context echoed

    Examples:
      | transport | operation          |
      | MCP       | sync_event_sources |
      | MCP       | log_event          |
      | REST      | sync_event_sources |
      | REST      | log_event          |

  @T-UC-015-030 @partition @event_source_scoping @br-rule-105
  Scenario Outline: Account reference partition validation - <partition>
    When the Buyer Agent sends a sync_event_sources request with account <account_value>
    Then <outcome>

    Examples: Valid partitions
      | partition             | account_value                                            | outcome                                     |
      | account_id_lookup     | {"account_id": "acc_acme_001"}                           | the response contains event_sources          |
      | brand_operator_lookup | {"brand": {"domain": "acme-corp.com"}, "operator": "acme-corp.com"} | the response contains event_sources |

    Examples: Invalid partitions
      | partition             | account_value                                              | outcome                                                  |
      | account_missing       | (absent)                                                   | error "ACCOUNT_REQUIRED" with suggestion                 |
      | account_not_found     | {"account_id": "nonexistent_123"}                          | error "ACCOUNT_NOT_FOUND" with suggestion                |
      | account_invalid_ref   | {"account_id": "acc_1", "brand": {"domain": "x.com"}, "operator": "x.com"} | error "ACCOUNT_INVALID_FORMAT" with suggestion |

  @T-UC-015-031 @boundary @event_source_scoping @br-rule-105
  Scenario Outline: Account reference boundary validation - <boundary_point>
    When the Buyer Agent sends a sync_event_sources request with account <account_value>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                               | account_value                                             | outcome                                                  |
      | account_id present alone                     | {"account_id": "acc_acme_001"}                            | the response contains event_sources                      |
      | brand+operator present alone                 | {"brand": {"domain": "acme-corp.com"}, "operator": "acme-corp.com"} | the response contains event_sources            |
      | account field omitted entirely               | (absent)                                                  | error "ACCOUNT_REQUIRED" with suggestion                 |
      | account_id references nonexistent account    | {"account_id": "nonexistent_123"}                         | error "ACCOUNT_NOT_FOUND" with suggestion                |
      | brand+operator pair with no matching account | {"brand": {"domain": "unknown.com"}, "operator": "unknown.com"} | error "ACCOUNT_NOT_FOUND" with suggestion          |
      | both account_id and brand+operator supplied  | {"account_id": "acc_1", "brand": {"domain": "x.com"}, "operator": "x.com"} | error "ACCOUNT_INVALID_FORMAT" with suggestion |
      | empty object for account                     | {}                                                        | error "ACCOUNT_INVALID_FORMAT" with suggestion           |

  @T-UC-015-032 @partition @event_source_sync @br-rule-106
  Scenario Outline: Event source sync mode partition validation - <partition>
    Given an account "acc_1" exists with buyer-managed source "src_existing"
    When the Buyer Agent sends a sync_event_sources request with <sync_config>
    Then <outcome>

    Examples: Valid partitions
      | partition           | sync_config                                                                            | outcome                                          |
      | discovery_only      | account only, no event_sources                                                         | response lists all sources with action "unchanged" |
      | upsert_mode         | account and event_sources [{"event_source_id": "src_1"}], delete_missing omitted       | sources created or updated                        |
      | clean_sync_mode     | account and event_sources [{"event_source_id": "src_1"}], delete_missing true          | unlisted buyer sources deleted                    |

    Examples: Invalid partitions
      | partition                          | sync_config                                                     | outcome                                           |
      | empty_event_sources_array          | account and event_sources []                                    | error "EVENT_SOURCES_REQUIRED" with suggestion     |
      | missing_event_source_id            | account and event_sources [{"name": "Pixel"}]                   | error "EVENT_SOURCE_ID_REQUIRED" with suggestion   |
      | duplicate_event_source_id_in_request | account and event_sources with two "src_1" entries             | error "EVENT_SOURCE_ID_EXISTS" with suggestion     |

  @T-UC-015-033 @boundary @event_source_sync @br-rule-106
  Scenario Outline: Event source sync boundary validation - <boundary_point>
    Given an account "acc_1" exists
    When the Buyer Agent sends a sync_event_sources request with <sync_config>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                                            | sync_config                                                                  | outcome                                          |
      | event_sources omitted (discovery-only mode)                               | account only, no event_sources                                               | response lists all sources with action "unchanged" |
      | event_sources with 1 item, delete_missing omitted                         | account and event_sources [{"event_source_id": "src_1"}]                     | source created or updated                         |
      | event_sources with 1 item, delete_missing=true                            | account and event_sources [{"event_source_id": "src_1"}], delete_missing true | unlisted buyer sources deleted                   |
      | event_sources with 1 item, delete_missing=false                           | account and event_sources [{"event_source_id": "src_1"}], delete_missing false | source created or updated, nothing deleted       |
      | event_sources is empty array []                                           | account and event_sources []                                                 | error "EVENT_SOURCES_REQUIRED" with suggestion    |
      | event source item missing event_source_id                                 | account and event_sources [{"name": "Pixel"}]                                | error "EVENT_SOURCE_ID_REQUIRED" with suggestion  |
      | two items with same event_source_id                                       | account and event_sources with two "src_dup" entries                         | error "EVENT_SOURCE_ID_EXISTS" with suggestion    |
      | delete_missing=true but event_sources omitted (discovery-only; delete_missing ignored) | account only, delete_missing true, no event_sources                 | response lists all sources (discovery-only)       |

  @T-UC-015-034 @partition @event_dedup @br-rule-107
  Scenario Outline: Event dedup partition validation - <partition>
    Given an event source "src_web" is configured
    When the Buyer Agent logs events with <event_config>
    Then <outcome>

    Examples: Valid partitions
      | partition                | event_config                                                                      | outcome                                   |
      | unique_triple            | event_id "evt_001", event_type "purchase", event_source_id "src_web"              | event processed successfully               |
      | same_id_different_type   | event_id "evt_001" with event_type "add_to_cart" and "purchase" to same source    | both events processed as distinct          |
      | same_id_different_source | event_id "evt_001" type "purchase" to "src_web" and "src_app" separately          | both events processed as distinct          |
      | duplicate_event          | event_id "evt_001" type "purchase" to "src_web" sent twice                        | second event silently deduplicated         |

    Examples: Invalid partitions
      | partition         | event_config                                              | outcome                                          |
      | missing_event_id  | event without event_id field                              | error "EVENT_ID_REQUIRED" with suggestion         |
      | empty_event_id    | event with event_id ""                                    | error "EVENT_ID_TOO_SHORT" with suggestion        |
      | event_id_too_long | event with event_id of 257 characters                     | error "EVENT_ID_TOO_LONG" with suggestion         |

  @T-UC-015-035 @boundary @event_dedup @br-rule-107
  Scenario Outline: Event dedup boundary validation - <boundary_point>
    Given an event source "src_web" is configured
    When the Buyer Agent logs events with <event_config>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                               | event_config                                                               | outcome                                      |
      | event_id length = 1 (minimum)                                | event with event_id "a"                                                    | event processed successfully                  |
      | event_id length = 256 (maximum)                              | event with event_id of exactly 256 characters                              | event processed successfully                  |
      | event_id length = 0 (empty string)                           | event with event_id ""                                                     | error "EVENT_ID_TOO_SHORT" with suggestion    |
      | event_id length = 257 (over max)                             | event with event_id of 257 characters                                      | error "EVENT_ID_TOO_LONG" with suggestion     |
      | event_id omitted                                             | event without event_id field                                               | error "EVENT_ID_REQUIRED" with suggestion     |
      | same event_id + same event_type + same event_source_id (duplicate) | event_id "evt_001" type "purchase" to "src_web" sent twice            | second event silently deduplicated            |
      | same event_id + different event_type + same event_source_id  | event_id "evt_001" with types "add_to_cart" and "purchase"                 | both events processed as distinct             |
      | same event_id + same event_type + different event_source_id  | event_id "evt_001" type "purchase" to "src_web" and "src_app"              | both events processed as distinct             |

  @T-UC-015-036 @partition @event_structure @br-rule-108
  Scenario Outline: Event structure partition validation - <partition>
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with <event_config>
    Then <outcome>

    Examples: Valid partitions
      | partition                  | event_config                                                                                                    | outcome                 |
      | standard_event             | event_id "evt_1", event_type "purchase", event_time "2026-01-15T14:30:00Z"                                      | event processed          |
      | custom_event_with_name     | event_id "evt_2", event_type "custom", event_time "2026-01-15T14:30:00Z", custom_event_name "product_demo"      | event processed          |
      | website_event_with_url     | event_id "evt_3", event_type "purchase", event_time "2026-01-15T14:30:00Z", action_source "website", event_source_url "https://shop.example.com" | event processed |
      | full_event                 | event with all optional fields populated                                                                         | event processed          |

    Examples: Invalid partitions
      | partition                  | event_config                                                                                                    | outcome                                              |
      | missing_event_type         | event_id "evt_1", event_time "2026-01-15T14:30:00Z", no event_type                                              | error "EVENT_TYPE_REQUIRED" with suggestion           |
      | missing_event_time         | event_id "evt_1", event_type "purchase", no event_time                                                           | error "EVENT_TIME_REQUIRED" with suggestion           |
      | invalid_event_type         | event_id "evt_1", event_type "conversion", event_time "2026-01-15T14:30:00Z"                                     | error "EVENT_TYPE_INVALID_FORMAT" with suggestion     |
      | invalid_event_time_format  | event_id "evt_1", event_type "purchase", event_time "yesterday"                                                  | error "EVENT_TIME_INVALID_FORMAT" with suggestion     |
      | custom_type_no_name        | event_id "evt_1", event_type "custom", event_time "2026-01-15T14:30:00Z", no custom_event_name                   | error "CUSTOM_EVENT_NAME_REQUIRED" with suggestion    |
      | website_source_no_url      | event_id "evt_1", event_type "purchase", event_time "2026-01-15T14:30:00Z", action_source "website", no URL      | error "EVENT_SOURCE_URL_REQUIRED" with suggestion     |

  @T-UC-015-037 @boundary @event_structure @br-rule-108
  Scenario Outline: Event structure boundary validation - <boundary_point>
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with <event_config>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                         | event_config                                                                                                    | outcome                                              |
      | event with only required fields (event_id, event_type, event_time) | event_id "evt_1", event_type "purchase", event_time "2026-01-15T14:30:00Z"                          | event processed                                       |
      | event_type = 'purchase' (standard type)                | event_id "evt_1", event_type "purchase", event_time "2026-01-15T14:30:00Z"                                      | event processed                                       |
      | event_type = 'custom' with custom_event_name           | event_id "evt_1", event_type "custom", event_time "2026-01-15T14:30:00Z", custom_event_name "demo"              | event processed                                       |
      | event_type = 'custom' without custom_event_name        | event_id "evt_1", event_type "custom", event_time "2026-01-15T14:30:00Z", no custom_event_name                  | error "CUSTOM_EVENT_NAME_REQUIRED" with suggestion    |
      | action_source = 'website' with event_source_url        | event_id "evt_1", event_type "purchase", event_time "2026-01-15T14:30:00Z", action_source "website", event_source_url "https://shop.example.com" | event processed |
      | action_source = 'website' without event_source_url     | event_id "evt_1", event_type "purchase", event_time "2026-01-15T14:30:00Z", action_source "website", no URL     | error "EVENT_SOURCE_URL_REQUIRED" with suggestion     |
      | action_source = 'app' without event_source_url         | event_id "evt_1", event_type "purchase", event_time "2026-01-15T14:30:00Z", action_source "app"                 | event processed                                       |
      | event_type not in enum (e.g. 'conversion')             | event_id "evt_1", event_type "conversion", event_time "2026-01-15T14:30:00Z"                                    | error "EVENT_TYPE_INVALID_FORMAT" with suggestion     |
      | event_time is not ISO 8601                             | event_id "evt_1", event_type "purchase", event_time "yesterday"                                                 | error "EVENT_TIME_INVALID_FORMAT" with suggestion     |
      | event_type omitted                                     | event_id "evt_1", event_time "2026-01-15T14:30:00Z"                                                             | error "EVENT_TYPE_REQUIRED" with suggestion           |
      | event_time omitted                                     | event_id "evt_1", event_type "purchase"                                                                          | error "EVENT_TIME_REQUIRED" with suggestion           |

  @T-UC-015-037b @boundary @event_type @br-rule-108
  Scenario Outline: Event type enum boundary validation - <boundary_point>
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with event_type <event_type_value> and required fields
    Then <outcome>

    Examples: Boundary values
      | boundary_point                    | event_type_value | outcome                                            |
      | page_view (first enum value)      | page_view        | event processed                                     |
      | custom (last enum value)          | custom           | event processed (with custom_event_name provided)   |
      | Unknown string not in enum        | click            | error "EVENT_TYPE_INVALID_FORMAT" with suggestion   |

  @T-UC-015-038 @partition @user_match_id @br-rule-109
  Scenario Outline: User match identifier partition validation - <partition>
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with user_match <user_match_value>
    Then <outcome>

    Examples: Valid partitions
      | partition              | user_match_value                                                                         | outcome                |
      | uid_only               | {"uids": [{"type": "rampid", "value": "abc123"}]}                                        | event processed         |
      | hashed_email_only      | {"hashed_email": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"}    | event processed         |
      | hashed_phone_only      | {"hashed_phone": "f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5"}    | event processed         |
      | click_id_only          | {"click_id": "CjwKCAjw...", "click_id_type": "gclid"}                                    | event processed         |
      | ip_ua_pair             | {"client_ip": "203.0.113.50", "client_user_agent": "Mozilla/5.0..."}                     | event processed         |
      | multiple_identifiers   | {"hashed_email": "a1b2...", "click_id": "CjwK...", "client_ip": "203.0.113.50", "client_user_agent": "Mozilla/5.0..."} | event processed |

    Examples: Invalid partitions
      | partition                       | user_match_value                    | outcome                                                  |
      | empty_user_match                | {}                                  | error "USER_MATCH_REQUIRED" with suggestion               |
      | invalid_hashed_email_format     | {"hashed_email": "user@example.com"} | error "HASHED_EMAIL_INVALID_FORMAT" with suggestion      |
      | invalid_hashed_phone_format     | {"hashed_phone": "+12065551234"}     | error "HASHED_PHONE_INVALID_FORMAT" with suggestion      |
      | ip_without_ua                   | {"client_ip": "203.0.113.50"}        | error "USER_MATCH_REQUIRED" with suggestion               |
      | ua_without_ip                   | {"client_user_agent": "Mozilla/5.0..."} | error "USER_MATCH_REQUIRED" with suggestion            |
      | uids_empty_array                | {"uids": []}                         | error "USER_MATCH_REQUIRED" with suggestion               |

  @T-UC-015-039 @boundary @user_match_id @br-rule-109
  Scenario Outline: User match identifier boundary validation - <boundary_point>
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with user_match <user_match_value>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                          | user_match_value                                                                         | outcome                                                  |
      | user_match with hashed_email only (valid SHA-256)       | {"hashed_email": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"}    | event processed                                           |
      | user_match with hashed_phone only (valid SHA-256)       | {"hashed_phone": "f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5"}    | event processed                                           |
      | user_match with click_id only                           | {"click_id": "CjwKCAjw..."}                                                              | event processed                                           |
      | user_match with uids containing 1 item                  | {"uids": [{"type": "rampid", "value": "abc"}]}                                           | event processed                                           |
      | user_match with client_ip + client_user_agent           | {"client_ip": "203.0.113.50", "client_user_agent": "Mozilla/5.0..."}                     | event processed                                           |
      | user_match with client_ip only (no client_user_agent)   | {"client_ip": "203.0.113.50"}                                                             | error "USER_MATCH_REQUIRED" with suggestion               |
      | user_match with client_user_agent only (no client_ip)   | {"client_user_agent": "Mozilla/5.0..."}                                                   | error "USER_MATCH_REQUIRED" with suggestion               |
      | user_match empty object (no identifiers)                | {}                                                                                        | error "USER_MATCH_REQUIRED" with suggestion               |
      | hashed_email with uppercase hex (e.g. 'A1B2...')        | {"hashed_email": "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2"}    | error "HASHED_EMAIL_INVALID_FORMAT" with suggestion       |
      | hashed_email with 63 characters (too short)             | {"hashed_email": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b"}     | error "HASHED_EMAIL_INVALID_FORMAT" with suggestion       |
      | hashed_email with 65 characters (too long)              | {"hashed_email": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c"}   | error "HASHED_EMAIL_INVALID_FORMAT" with suggestion       |
      | hashed_email with non-hex characters                    | {"hashed_email": "g1h2i3j4k5l6g1h2i3j4k5l6g1h2i3j4k5l6g1h2i3j4k5l6g1h2i3j4k5l6g1h2"}   | error "HASHED_EMAIL_INVALID_FORMAT" with suggestion       |
      | uids array empty                                        | {"uids": []}                                                                              | error "USER_MATCH_REQUIRED" with suggestion               |
      | user_match omitted from event                           | (omitted)                                                                                 | event processed (no attribution)                          |

  @T-UC-015-040 @partition @batch_size @br-rule-110
  Scenario Outline: Batch size partition validation - <partition>
    Given an event source "src_web" is configured
    When the Buyer Agent logs <batch_config> to "src_web"
    Then <outcome>

    Examples: Valid partitions
      | partition       | batch_config              | outcome                 |
      | single_event    | a batch with 1 event      | events processed         |
      | typical_batch   | a batch with 50 events    | events processed         |
      | max_batch       | a batch with 10000 events | events processed         |

    Examples: Invalid partitions
      | partition       | batch_config                | outcome                                       |
      | empty_events    | a batch with 0 events       | error "EVENTS_REQUIRED" with suggestion        |
      | missing_events  | a request with events omitted | error "EVENTS_REQUIRED" with suggestion      |
      | batch_too_large | a batch with 10001 events   | error "BATCH_TOO_LARGE" with suggestion        |

  @T-UC-015-041 @boundary @batch_size @br-rule-110
  Scenario Outline: Batch size boundary validation - <boundary_point>
    Given an event source "src_web" is configured
    When the Buyer Agent logs <batch_config> to "src_web"
    Then <outcome>

    Examples: Boundary values
      | boundary_point                          | batch_config                  | outcome                                       |
      | events array with 1 item (minimum)      | a batch with 1 event          | events processed                               |
      | events array with 10,000 items (maximum) | a batch with 10000 events    | events processed                               |
      | events array with 0 items (empty)       | a batch with 0 events         | error "EVENTS_REQUIRED" with suggestion        |
      | events array with 10,001 items (over max) | a batch with 10001 events  | error "BATCH_TOO_LARGE" with suggestion        |
      | events field omitted                    | a request with events omitted | error "EVENTS_REQUIRED" with suggestion        |

  @T-UC-015-042 @partition @test_isolation @br-rule-111
  Scenario Outline: Test event isolation partition validation - <partition>
    Given an event source "src_web" is configured
    When the Buyer Agent logs events to "src_web" with <test_config>
    Then <outcome>

    Examples: Valid partitions
      | partition        | test_config                        | outcome                                        |
      | production_mode  | test_event_code omitted            | events treated as production                    |
      | test_mode        | test_event_code "TEST_20260315"    | events isolated from production attribution     |

  @T-UC-015-043 @boundary @test_isolation @br-rule-111
  Scenario Outline: Test event isolation boundary validation - <boundary_point>
    Given an event source "src_web" is configured
    When the Buyer Agent logs events to "src_web" with <test_config>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                      | test_config                     | outcome                                        |
      | test_event_code omitted (production mode)           | test_event_code omitted         | events treated as production                    |
      | test_event_code present with non-empty string       | test_event_code "TEST_SESSION"  | events isolated from production attribution     |
      | test_event_code present with short string (e.g. 'T') | test_event_code "T"           | events isolated from production attribution     |

  @T-UC-015-044 @partition @partial_failure @br-rule-112
  Scenario Outline: Partial failure response partition validation - <partition>
    Given an event source "src_web" is configured
    When the Buyer Agent logs events with <event_config>
    Then <outcome>

    Examples: Valid partitions
      | partition                    | event_config                                       | outcome                                                                |
      | all_events_processed         | 10 valid events                                    | events_received 10, events_processed 10, no partial_failures            |
      | partial_success              | 10 events where 2 have invalid event_type          | events_received 10, events_processed 8, partial_failures with 2 entries |
      | all_events_failed_validation | 3 events all with invalid structure                | events_received 3, events_processed 0, partial_failures with 3 entries  |
      | operation_level_error        | events sent to nonexistent event_source             | errors array present, no events_received field                          |

    Examples: Invalid partitions
      | partition                       | event_config                                                  | outcome                                          |
      | mixed_success_and_errors        | (server-side constraint: response cannot mix success+errors)   | error "VALIDATION_ERROR" with suggestion          |

  @T-UC-015-045 @boundary @partial_failure @br-rule-112
  Scenario Outline: Partial failure response boundary validation - <boundary_point>
    Given an event source "src_web" is configured
    When the Buyer Agent logs events with <event_config>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                                       | event_config                                         | outcome                                                                |
      | events_received = events_processed (all succeeded, no partial_failures) | 10 valid events                                    | events_received 10, events_processed 10, no partial_failures            |
      | events_received > events_processed (some partial_failures)           | 10 events where 3 have invalid type                  | events_received 10, events_processed 7, partial_failures with 3 entries |
      | events_processed = 0 with partial_failures (all per-event failures)  | 3 events all invalid                                 | events_received 3, events_processed 0, partial_failures with 3 entries  |
      | operation-level error (errors array present, no success fields)      | events to nonexistent source                         | errors array present, no events_received field                          |
      | both events_received and errors present in response                  | (server-side constraint: cannot mix)                 | error "VALIDATION_ERROR" with suggestion                                |
      | partial_failures array present but empty                             | 5 valid events with empty partial_failures            | events_received 5, events_processed 5                                   |
      | match_quality = 0.0 (no matches)                                    | events without user_match                             | match_quality 0.0                                                       |
      | match_quality = 1.0 (all matched)                                   | events with complete user_match                       | match_quality 1.0                                                       |

  @T-UC-015-046 @context-echo @post-s9 @post-f3
  Scenario: Context echo -- sync_event_sources success includes context
    Given an account "acc_1" exists
    When the Buyer Agent sends a sync_event_sources request with context {"trace_id": "abc-123"}
    Then the response echoes context {"trace_id": "abc-123"} unchanged

  @T-UC-015-047 @context-echo @post-s9 @post-f3
  Scenario: Context echo -- log_event success includes context
    Given an event source "src_web" is configured
    When the Buyer Agent logs events with context {"correlation_id": "xyz-789"}
    Then the response echoes context {"correlation_id": "xyz-789"} unchanged

  @T-UC-015-048 @context-echo @post-f3
  Scenario: Context echo -- error response includes context when possible
    When the Buyer Agent sends a log_event request with invalid event_source_id and context {"trace_id": "err-001"}
    Then the error response echoes context {"trace_id": "err-001"}

  @T-UC-015-049 @invariant @br-rule-105
  Scenario: BR-RULE-105 INV-3 holds -- account_id resolves account
    Given an account with account_id "acc_acme_001" exists
    When the Buyer Agent sends sync_event_sources with account {"account_id": "acc_acme_001"}
    Then the account is resolved and event sources are returned

  @T-UC-015-050 @invariant @br-rule-105
  Scenario: BR-RULE-105 INV-4 holds -- brand+operator resolves account
    Given an account with brand "acme-corp.com" and operator "acme-corp.com" exists
    When the Buyer Agent sends sync_event_sources with account {"brand": {"domain": "acme-corp.com"}, "operator": "acme-corp.com"}
    Then the account is resolved and event sources are returned

  @T-UC-015-051 @invariant @br-rule-105 @error
  Scenario: BR-RULE-105 INV-1 violated -- account omitted
    When the Buyer Agent sends sync_event_sources without account field
    Then the error code should be "ACCOUNT_REQUIRED"
    And the error should include "suggestion" field

  @T-UC-015-052 @invariant @br-rule-105 @error
  Scenario: BR-RULE-105 INV-2 violated -- account does not resolve
    When the Buyer Agent sends sync_event_sources with account {"account_id": "nonexistent"}
    Then the error code should be "ACCOUNT_NOT_FOUND"
    And the error should include "suggestion" field

  @T-UC-015-053 @invariant @br-rule-106
  Scenario: BR-RULE-106 INV-1 holds -- discovery-only when event_sources omitted
    Given an account "acc_1" has 2 buyer-managed event sources
    When the Buyer Agent sends sync_event_sources with account "acc_1" and no event_sources
    Then no sources are created, updated, or deleted
    And all sources have action "unchanged"

  @T-UC-015-054 @invariant @br-rule-106
  Scenario: BR-RULE-106 INV-2 holds -- upsert creates and updates
    Given an account "acc_1" has buyer-managed source "src_existing"
    When the Buyer Agent syncs event_sources with [{"event_source_id": "src_existing"}, {"event_source_id": "src_new"}] and delete_missing false
    Then "src_existing" has action "updated" or "unchanged"
    And "src_new" has action "created"
    And no sources are deleted

  @T-UC-015-055 @invariant @br-rule-106
  Scenario: BR-RULE-106 INV-3 holds -- clean sync deletes unlisted buyer sources
    Given an account "acc_1" has buyer-managed sources "src_keep" and "src_old"
    When the Buyer Agent syncs event_sources with [{"event_source_id": "src_keep"}] and delete_missing true
    Then "src_keep" has action "updated" or "unchanged"
    And "src_old" has action "deleted"

  @T-UC-015-056 @invariant @br-rule-106 @error
  Scenario: BR-RULE-106 INV-5 violated -- empty event_sources array
    When the Buyer Agent syncs event_sources with account "acc_1" and event_sources []
    Then the error code should be "EVENT_SOURCES_REQUIRED"
    And the error should include "suggestion" field

  @T-UC-015-057 @invariant @br-rule-106 @error
  Scenario: BR-RULE-106 INV-6 violated -- duplicate event_source_id
    When the Buyer Agent syncs event_sources with two entries having event_source_id "src_dup"
    Then the error code should be "DUPLICATE_EVENT_SOURCE_ID"
    And the error should include "suggestion" field

  @T-UC-015-058 @invariant @br-rule-107 @error
  Scenario: BR-RULE-107 INV-4 violated -- event_id missing
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event without event_id
    Then the event appears in partial_failures with code "EVENT_ID_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "event_id"

  @T-UC-015-059 @invariant @br-rule-107 @error
  Scenario: BR-RULE-107 INV-5 violated -- event_id outside length bounds
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with event_id of 257 characters
    Then the event appears in partial_failures with code "EVENT_ID_TOO_LONG"
    And the error should include "suggestion" field
    And the suggestion should contain "256 characters"

  @T-UC-015-060 @invariant @br-rule-108 @error
  Scenario: BR-RULE-108 INV-1 violated -- missing required event fields
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event missing event_type
    Then the event appears in partial_failures with code "EVENT_TYPE_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "event_type"

  @T-UC-015-061 @invariant @br-rule-108 @error
  Scenario: BR-RULE-108 INV-2 violated -- invalid event_type
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with event_type "nonstandard"
    Then the event appears in partial_failures with code "EVENT_TYPE_INVALID_FORMAT"
    And the error should include "suggestion" field
    And the suggestion should contain "standard types"

  @T-UC-015-062 @invariant @br-rule-108 @error
  Scenario: BR-RULE-108 INV-3 violated -- invalid event_time format
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with event_time "not-a-date"
    Then the event appears in partial_failures with code "EVENT_TIME_INVALID_FORMAT"
    And the error should include "suggestion" field
    And the suggestion should contain "ISO 8601"

  @T-UC-015-063 @invariant @br-rule-108 @error
  Scenario: BR-RULE-108 INV-4 violated -- custom type without custom_event_name
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with event_type "custom" and no custom_event_name
    Then the event appears in partial_failures with code "CUSTOM_EVENT_NAME_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "custom_event_name"

  @T-UC-015-064 @invariant @br-rule-108 @error
  Scenario: BR-RULE-108 INV-5 violated -- website source without URL
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with action_source "website" and no event_source_url
    Then the event appears in partial_failures with code "EVENT_SOURCE_URL_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "event_source_url"

  @T-UC-015-065 @invariant @br-rule-108
  Scenario: BR-RULE-108 INV-6 holds -- custom event with name accepted
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with event_type "custom" and custom_event_name "product_demo"
    Then the event is processed successfully with custom classification

  @T-UC-015-066 @invariant @br-rule-108
  Scenario: BR-RULE-108 INV-7 holds -- non-website source without URL accepted
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with action_source "app" and no event_source_url
    Then the event is processed successfully

  @T-UC-015-067 @invariant @br-rule-109 @error
  Scenario: BR-RULE-109 INV-2 violated -- invalid hashed_email format
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with hashed_email "not-a-sha256-hash"
    Then the event appears in partial_failures with code "HASHED_EMAIL_INVALID_FORMAT"
    And the error should include "suggestion" field
    And the suggestion should contain "SHA-256"

  @T-UC-015-068 @invariant @br-rule-109 @error
  Scenario: BR-RULE-109 INV-3 violated -- invalid hashed_phone format
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with hashed_phone "not-a-sha256-hash"
    Then the event appears in partial_failures with code "HASHED_PHONE_INVALID_FORMAT"
    And the error should include "suggestion" field
    And the suggestion should contain "E.164"

  @T-UC-015-069 @invariant @br-rule-109 @error
  Scenario: BR-RULE-109 INV-4 violated -- client_ip without client_user_agent
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with user_match {"client_ip": "203.0.113.50"}
    Then the event appears in partial_failures with code "USER_MATCH_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "client_ip"

  @T-UC-015-070 @invariant @br-rule-109
  Scenario: BR-RULE-109 INV-5 holds -- user_match omitted, event accepted
    Given an event source "src_web" is configured
    When the Buyer Agent logs a valid event without user_match
    Then the event is processed successfully
    And the match_quality may be reduced

  @T-UC-015-071 @invariant @br-rule-109
  Scenario: BR-RULE-109 INV-6 holds -- multiple identifiers improve match quality
    Given an event source "src_web" is configured
    When the Buyer Agent logs an event with hashed_email, click_id, and client_ip+client_user_agent
    Then the event is processed successfully
    And the match_quality is higher than single-identifier events

  @T-UC-015-072 @invariant @br-rule-110
  Scenario: BR-RULE-110 INV-1 holds -- batch of 1 to 10000 accepted
    Given an event source "src_web" is configured
    When the Buyer Agent logs a batch of 100 valid events
    Then the response contains events_received 100

  @T-UC-015-073 @invariant @br-rule-110 @error
  Scenario: BR-RULE-110 INV-2 violated -- empty or omitted events
    Given an event source "src_web" is configured
    When the Buyer Agent sends a log_event request with empty events array
    Then the error code should be "EVENTS_REQUIRED"
    And the error should include "suggestion" field

  @T-UC-015-074 @invariant @br-rule-110 @error
  Scenario: BR-RULE-110 INV-3 violated -- batch exceeds 10000
    Given an event source "src_web" is configured
    When the Buyer Agent logs a batch of 10001 events
    Then the error code should be "BATCH_TOO_LARGE"
    And the error should include "suggestion" field

  @T-UC-015-075 @invariant @br-rule-111
  Scenario: BR-RULE-111 INV-2 holds -- test events validated identically
    Given an event source "src_web" is configured
    When the Buyer Agent logs a test event with invalid event_type and test_event_code "TEST_001"
    Then the event appears in partial_failures with code "INVALID_EVENT_TYPE"
    And the validation is identical to production events

  @T-UC-015-076 @invariant @br-rule-111
  Scenario: BR-RULE-111 INV-3 holds -- test events do not affect production
    Given an event source "src_web" is configured
    When the Buyer Agent logs valid events with test_event_code "TEST_001"
    Then the events are processed
    And production attribution is not affected
    And production reporting is not affected
    And production optimization signals are not affected

  @T-UC-015-077 @invariant @br-rule-112
  Scenario: BR-RULE-112 INV-1 holds -- success branch structure
    Given an event source "src_web" is configured
    When the Buyer Agent logs valid events
    Then the response contains events_received and events_processed
    And the response does NOT contain errors array

  @T-UC-015-078 @invariant @br-rule-112
  Scenario: BR-RULE-112 INV-2 holds -- error branch structure
    When the Buyer Agent logs events to nonexistent event_source_id
    Then the response contains errors array
    And the response does NOT contain events_received or events_processed

  @T-UC-015-079 @invariant @br-rule-112
  Scenario: BR-RULE-112 INV-5 holds -- events_received = events_processed + partial_failures
    Given an event source "src_web" is configured
    When the Buyer Agent logs 10 events where 3 have invalid structure
    Then events_received is 10
    And events_processed is 7
    And partial_failures has 3 entries
    And events_received equals events_processed plus partial_failures count

  @T-UC-015-sandbox-happy @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account log_event produces simulated processing with sandbox flag
    Given an event source "src_web" is configured
    And the request targets a sandbox account
    When the Buyer Agent logs valid events
    Then the response should include sandbox equals true
    And no real attribution or reporting pipelines should have been triggered
    And no real billing records should have been created
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-2: real ad platform calls suppressed
    # BR-RULE-209 INV-3: real billing suppressed
    # BR-RULE-209 INV-4: response includes sandbox: true

  @T-UC-015-sandbox-production @invariant @br-rule-209 @sandbox
  Scenario: Production account log_event response does not include sandbox flag
    Given an event source "src_web" is configured
    And the request targets a production account
    When the Buyer Agent logs valid events
    Then the response should not include a sandbox field
    # BR-RULE-209 INV-5: production account -> sandbox absent

  @T-UC-015-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account with invalid event returns real validation error
    And the request targets a sandbox account
    When the Buyer Agent logs events with missing required event_type
    Then the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

