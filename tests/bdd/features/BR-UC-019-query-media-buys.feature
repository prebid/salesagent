# Generated from adcp-req @ render on 2026-06-04T09:53:12Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

Feature: BR-UC-019 Query Media Buys
  As a Buyer (Human or AI Agent)
  I want to query the current state of my media buys
  So that I can monitor campaign status, check creative approvals, and assess delivery pacing

  # Postconditions verified:
  #   POST-S1: Buyer knows the current status of each matching media buy (v3.1 MediaBuyStatus: pending_creatives, pending_start, active, paused, completed, rejected, canceled)
  #   POST-S2: Buyer knows the package-level details for each media buy (budget, bid_price, product, flight dates, paused state)
  #   POST-S3: Buyer knows the creative approval state for each package (pending_review, approved, rejected with reason)
  #   POST-S4: Buyer knows the near-real-time delivery metrics per package when snapshots were requested and available
  #   POST-S5: Buyer knows why a snapshot is unavailable when requested but not returned
  #   POST-S6: Buyer can correlate results via v3.1 lifecycle handles (revision, valid_actions, history, cancellation, echoed account/invoice_recipient)
  #   POST-F1: Buyer knows the operation failed
  #   POST-F2: Buyer knows what went wrong (error explains the failure)
  #   POST-F3: Buyer knows how to recover (error includes recovery classification)

  Background:
    Given a Seller Agent is operational and accepting requests
    And an authenticated Buyer with principal_id "buyer-001"
    And the principal "buyer-001" exists in the tenant database



  @T-UC-019-main @main-flow
  Scenario: Query media buys with default filters
    Given the principal "buyer-001" owns media buy "mb-001" with start_date "2026-03-01" and end_date "2026-03-31"
    And today is "2026-03-15"
    When the Buyer Agent sends a get_media_buys request with no filters
    Then the response should include media buy "mb-001" with status "active"
    And each media buy should include package-level details with budget, bid_price, product_id, flight dates, and paused state
    And each package should include creative approval state when creatives are assigned
    And each media buy should include buyer_ref and buyer_campaign_ref for correlation
    # POST-S1: Status computed from flight dates
    # POST-S2: Package-level details present
    # POST-S3: Creative approval state present
    # POST-S6: Buyer refs present for correlation

  @T-UC-019-main-filter-ids @main-flow @filtering
  Scenario: Query media buys by specific media_buy_ids
    Given the principal "buyer-001" owns media buys "mb-001", "mb-002", and "mb-003"
    When the Buyer Agent sends a get_media_buys request with media_buy_ids ["mb-001", "mb-003"]
    Then the response should include media buys "mb-001" and "mb-003"
    And the response should not include media buy "mb-002"
    # POST-S1: Status present for each requested buy

  @T-UC-019-main-snapshot @main-flow @snapshot
  Scenario: Query media buys with delivery snapshots requested and available
    Given the principal "buyer-001" owns media buy "mb-001" with an active package "pkg-001"
    And the ad platform adapter supports realtime reporting
    And snapshot data is available for package "pkg-001"
    When the Buyer Agent sends a get_media_buys request with include_snapshot true
    Then the response package "pkg-001" should include a snapshot
    And the snapshot should include as_of, staleness_seconds, impressions, and spend
    # POST-S4: Near-real-time delivery metrics present per package

  @T-UC-019-main-no-results @main-flow
  Scenario: Query returns empty results when principal has no matching media buys
    Given the principal "buyer-001" owns no media buys
    When the Buyer Agent sends a get_media_buys request with no filters
    Then the response should include an empty media_buys array
    And no error should be present in the response
    # POST-S1: Empty result is valid (no matching buys)

  @T-UC-019-ext-a @extension @ext-a @error
  Scenario: Authentication required - identity missing from request
    Given the Buyer has no authentication credentials
    When the Buyer Agent sends a get_media_buys request without authentication
    Then the operation should fail with error code "AUTH_REQUIRED"
    And the error message should indicate that identity is required
    And the error should include a "recovery" field indicating terminal failure
    And the error should include a "suggestion" field
    And the suggestion should contain "authentication" or "credentials"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains authentication is missing
    # POST-F3: Recovery classification indicates how to fix

  @T-UC-019-ext-b @extension @ext-b @error
  Scenario: Principal ID missing - identity resolved but principal_id absent
    Given an authenticated identity with no principal_id
    When the Buyer Agent sends a get_media_buys request
    Then the response should include an empty media_buys array
    And the response errors array should include error code "principal_id_missing"
    And the error message should contain "Principal ID not found"
    And the error should include a "suggestion" field
    And the suggestion should contain "re-authenticate" or "credentials"
    # POST-F1: Buyer knows no results were returned
    # POST-F2: Error explains principal_id is missing
    # POST-F3: Buyer can infer corrective action

  @T-UC-019-ext-c @extension @ext-c @error
  Scenario: Principal not found - principal_id not in registry
    Given an authenticated Buyer with principal_id "buyer-unknown"
    And the principal "buyer-unknown" does not exist in the tenant database
    When the Buyer Agent sends a get_media_buys request
    Then the response should include an empty media_buys array
    And the response errors array should include error code "principal_not_found"
    And the error message should contain "not found"
    And the error should include a "suggestion" field
    And the suggestion should contain "register" or "verify"
    # POST-F1: Buyer knows no results were returned
    # POST-F2: Error explains principal was not found
    # POST-F3: Buyer can infer corrective action

  @T-UC-019-ext-d @extension @ext-d @error
  Scenario: Request validation failed - invalid parameter values
    Given an authenticated Buyer with principal_id "buyer-001"
    When the Buyer Agent sends a get_media_buys request with invalid parameter types
    Then the operation should fail with error code "VALIDATION_ERROR"
    And the error message should include field-level validation details
    And the error should include a "recovery" field indicating correctable failure
    And the error should include a "suggestion" field
    And the suggestion should contain "fix" or "correct" or "valid"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error identifies which fields failed validation
    # POST-F3: Per-field details enable targeted correction

  @T-UC-019-ext-e @extension @ext-e @error
  Scenario: Account filter not supported - account_id provided but not implemented
    Given an authenticated Buyer with principal_id "buyer-001"
    When the Buyer Agent sends a get_media_buys request with account_id "acc-001"
    Then the operation should fail with error code "ACCOUNT_FILTER_NOT_SUPPORTED"
    And the error message should contain "account_id filtering is not yet supported"
    And the error should include a "recovery" field indicating correctable failure
    And the error should include a "suggestion" field
    And the suggestion should contain "remove account_id" or "omit account_id"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains account filtering is not supported
    # POST-F3: Recovery is correctable -- omit account_id and retry

  @T-UC-019-partition-status @partition @status
  Scenario Outline: Status computation from flight dates - <partition>
    Given the principal "buyer-001" owns media buy "mb-001" with start_date "<start>" and end_date "<end>"
    And today is "<today>"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" should have status "<expected_status>"
    # BR-RULE-150: Status computed from relationship between today and flight dates

    Examples: Valid partitions
      | partition                 | today      | start      | end        | expected_status |
      | active_refined_pending_start | 2026-03-01 | 2026-03-15 | 2026-03-31 | pending_start   |
      | active_refined_in_flight  | 2026-03-15 | 2026-03-01 | 2026-03-31 | active          |
      | active_refined_completed  | 2026-04-01 | 2026-03-01 | 2026-03-31 | completed       |
      | single_day_flight         | 2026-03-15 | 2026-03-15 | 2026-03-15 | active          |

  @T-UC-019-partition-status-invalid @partition @status @error
  Scenario Outline: Status computation with missing dates - <partition>
    Given the principal "buyer-001" owns media buy "mb-001" with <date_condition>
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" status computation should handle the missing date gracefully
    And the error should include a "suggestion" field
    And the suggestion should contain "start_date" or "end_date" or "flight dates"
    # BR-RULE-150: Missing dates prevent status computation

    Examples: Invalid partitions
      | partition          | date_condition                          |
      | missing_start_date | no start_time and no start_date         |
      | missing_end_date   | no end_time and no end_date             |

  @T-UC-019-boundary-status @boundary @status
  Scenario Outline: Status computation boundary - <boundary_point>
    Given the principal "buyer-001" owns media buy "mb-001" with start_date "<start>" and end_date "<end>"
    And today is "<today>"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" should have status "<expected_status>"
    # BR-RULE-150: Boundary test at flight date transition points

    Examples: Boundary values
      | boundary_point                                            | today      | start      | end        | expected_status |
      | persisted='active', day before start                      | 2026-03-14 | 2026-03-15 | 2026-03-31 | pending_start   |
      | persisted='active', start day itself                      | 2026-03-15 | 2026-03-15 | 2026-03-31 | active          |
      | persisted='active', end day itself                        | 2026-03-31 | 2026-03-15 | 2026-03-31 | active          |
      | persisted='active', day after end                         | 2026-04-01 | 2026-03-15 | 2026-03-31 | completed       |
      | persisted='active', start==end==today                     | 2026-03-15 | 2026-03-15 | 2026-03-15 | active          |
      | persisted='active', start==end, day before                | 2026-03-14 | 2026-03-15 | 2026-03-15 | pending_start   |
      | persisted='active', start==end, day after                 | 2026-03-16 | 2026-03-15 | 2026-03-15 | completed       |

  @T-UC-019-inv-150-4 @invariant @BR-RULE-150
  Scenario: INV-4 holds - start_time takes precedence over start_date
    Given the principal "buyer-001" owns media buy "mb-001" with start_date "2026-03-15" and start_time "2026-03-10T00:00:00Z" and end_date "2026-03-31"
    And today is "2026-03-12"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" should have status "active"
    # BR-RULE-150 INV-4: start_time.date() (2026-03-10) used instead of start_date (2026-03-15)

  @T-UC-019-inv-150-5 @invariant @BR-RULE-150
  Scenario: INV-5 holds - end_time takes precedence over end_date
    Given the principal "buyer-001" owns media buy "mb-001" with start_date "2026-03-01" and end_date "2026-03-31" and end_time "2026-03-25T23:59:59Z"
    And today is "2026-03-28"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" should have status "completed"
    # BR-RULE-150 INV-5: end_time.date() (2026-03-25) used instead of end_date (2026-03-31)

  @T-UC-019-partition-status-filter @partition @status_filter
  Scenario Outline: Default status filter behavior - <partition>
    Given the principal "buyer-001" owns media buys in various statuses
    # Pin the clock: the "various statuses" seed builds each buy's flight window
    # around this date, so the query MUST evaluate status against it too (else all
    # windows are in the past under the real clock and every buy reads completed).
    And today is "2026-03-15"
    When the Buyer Agent sends a get_media_buys request with <filter_config>
    Then <expected_behavior>
    # BR-RULE-151: Status filter defaults and validation

    Examples: Valid partitions
      | partition           | filter_config                                                                                                  | expected_behavior                                                |
      | null_default_no_ids | no status_filter and no media_buy_ids                                                                          | only media buys with status "active" are returned                |
      | single_status       | status_filter "completed"                                                                                      | only media buys with status "completed" are returned             |
      | multiple_statuses   | status_filter ["active", "pending_start"]                                                                      | media buys with either status are returned                       |
      | all_statuses        | all seven v3.1 status values in status_filter                                                                  | media buys in any status are returned                            |

  @T-UC-019-partition-status-filter-invalid @partition @status_filter @error
  Scenario Outline: Invalid status filter values - <partition>
    Given an authenticated Buyer with principal_id "buyer-001"
    When the Buyer Agent sends a get_media_buys request with <invalid_filter>
    Then the operation should fail with error code "<error_code>"
    And the error should include a "suggestion" field
    And the suggestion should contain "<suggestion_fragment>"
    # BR-RULE-151: Invalid status filter rejected

    Examples: Invalid partitions
      | partition              | invalid_filter                      | error_code                   | suggestion_fragment                    |
      | unknown_status_value   | status_filter "expired"             | STATUS_FILTER_INVALID_VALUE  | pending_activation, active             |
      | empty_array            | status_filter as empty array []     | STATUS_FILTER_EMPTY          | at least one status value              |

  @T-UC-019-boundary-status-filter @boundary @status_filter
  Scenario Outline: Status filter boundary - <boundary_point>
    Given the principal "buyer-001" owns media buys in various statuses
    # Pin the clock so the seed's flight windows and the query's status
    # computation agree (see the partition scenario above).
    And today is "2026-03-15"
    When the Buyer Agent sends a get_media_buys request with <filter_config>
    Then <expected_behavior>
    # BR-RULE-151: Boundary test for status filter

    Examples: Boundary values
      | boundary_point                              | filter_config                                          | expected_behavior                                               |
      | status_filter omitted, media_buy_ids omitted| no status_filter                                       | only media buys with status "active" are returned               |
      | status_filter omitted, media_buy_ids non-empty | no status_filter and media_buy_ids ["mb-active","mb-completed"]   | every matching buy returned regardless of status                |
      | single valid enum value                     | status_filter "active"                                 | only media buys with status "active" are returned               |
      | array with one valid value                  | status_filter ["completed"]                            | only media buys with status "completed" are returned            |
      | array with all seven enum values            | all seven v3.1 status values in status_filter          | media buys in any status are returned                           |
      | empty array                                 | status_filter as empty array []                        | error "STATUS_FILTER_EMPTY" with suggestion                     |
      | removed enum value `pending_activation`     | status_filter "pending_activation"                     | error "STATUS_FILTER_INVALID_VALUE" with suggestion             |
      | array with mix of valid and unknown values  | status_filter ["active", "expired"]                    | error "STATUS_FILTER_INVALID_VALUE" with suggestion             |

  @T-UC-019-partition-approval @partition @approval_status
  Scenario Outline: Creative approval status mapping - <partition>
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And package "pkg-001" has a creative assignment with creative_id "<creative_id>"
    And the creative "<creative_id>" has internal status "<internal_status>" <extra_condition>
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the creative approval for "<creative_id>" should have approval_status "<expected_approval>"
    And <rejection_check>
    # BR-RULE-152: Internal creative status mapped to AdCP protocol enum

    Examples: Valid partitions
      | partition                | creative_id | internal_status | extra_condition                              | expected_approval | rejection_check                                     |
      | approved_creative        | cr-001      | approved        |                                              | approved          | rejection_reason should be absent                   |
      | rejected_with_reason     | cr-002      | rejected        | and rejection_reason "Image too dark"        | rejected          | rejection_reason should be "Image too dark"         |
      | rejected_without_reason  | cr-003      | rejected        | and no rejection_reason in data              | rejected          | rejection_reason should be null or absent           |
      | pending_review_explicit  | cr-004      | submitted       |                                              | pending_review    | rejection_reason should be absent                   |
      | pending_review_catchall  | cr-005      | processing      |                                              | pending_review    | rejection_reason should be absent                   |

  @T-UC-019-partition-approval-invalid @partition @approval_status
  Scenario: Creative approval mapping - no_creative_found (silent skip)
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And package "pkg-001" has a creative assignment referencing creative_id "cr-999"
    And no creative with id "cr-999" exists in the tenant
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the creative approvals for package "pkg-001" should not include an entry for "cr-999"
    And no error should be raised for the missing creative
    # BR-RULE-152 INV-4: Nonexistent creative silently omitted from approvals

  @T-UC-019-boundary-approval @boundary @approval_status
  Scenario Outline: Creative approval status boundary - <boundary_point>
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And package "pkg-001" has a creative assignment with creative_id "cr-001"
    And the creative "cr-001" has <creative_condition>
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then <expected_outcome>
    # BR-RULE-152: Boundary test for approval status mapping

    Examples: Boundary values
      | boundary_point                                    | creative_condition                             | expected_outcome                                                             |
      | creative.status exactly 'approved'                | internal status "approved"                     | approval_status should be "approved"                                         |
      | creative.status exactly 'rejected'                | internal status "rejected" with rejection data | approval_status should be "rejected" with rejection_reason                   |
      | creative.status exactly 'rejected' with no data   | internal status "rejected" and empty data       | approval_status should be "rejected" with null rejection_reason              |
      | creative.status is empty string                   | internal status ""                             | approval_status should be "pending_review"                                   |
      | creative.status is null                           | internal status null                           | approval_status should be "pending_review"                                   |
      | creative.status is 'APPROVED' (case mismatch)    | internal status "APPROVED"                     | approval_status should be "pending_review"                                   |
      | creative not found for assignment                 | a nonexistent creative_id                      | the approval entry should be silently omitted                                |

  @T-UC-019-inv-152-5 @invariant @BR-RULE-152
  Scenario: INV-5 holds - rejection_reason absent when not rejected
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And package "pkg-001" has a creative with internal status "approved"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the creative approval should have approval_status "approved"
    And rejection_reason should not be present in the approval entry
    # BR-RULE-152 INV-5: rejection_reason is absent when approval_status is not rejected

  @T-UC-019-partition-snapshot @partition @include_snapshot
  Scenario Outline: Snapshot availability - <partition>
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And <adapter_condition>
    When the Buyer Agent sends a get_media_buys request with <snapshot_request>
    Then <expected_outcome>
    # BR-RULE-153: Snapshot availability depends on adapter capability

    Examples: Valid partitions
      | partition                          | adapter_condition                                                | snapshot_request           | expected_outcome                                                                                |
      | snapshot_not_requested             | the ad platform adapter exists                                   | include_snapshot false      | no snapshot or snapshot_unavailable_reason on any package                                        |
      | snapshot_supported_and_available   | the adapter supports realtime reporting and data is available    | include_snapshot true       | package "pkg-001" should include a snapshot with as_of and impressions                           |
      | snapshot_supported_but_unavailable | the adapter supports realtime reporting but no data for pkg-001  | include_snapshot true       | package "pkg-001" should have snapshot_unavailable_reason "SNAPSHOT_TEMPORARILY_UNAVAILABLE"     |
      | snapshot_unsupported               | the adapter does not support realtime reporting                  | include_snapshot true       | package "pkg-001" should have snapshot_unavailable_reason "SNAPSHOT_UNSUPPORTED"                 |

  @T-UC-019-boundary-snapshot @boundary @include_snapshot
  Scenario Outline: Snapshot availability boundary - <boundary_point>
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And <adapter_condition>
    When the Buyer Agent sends a get_media_buys request with <snapshot_request>
    Then <expected_outcome>
    # BR-RULE-153: Boundary test for snapshot behavior

    Examples: Boundary values
      | boundary_point                                                           | adapter_condition                                                | snapshot_request           | expected_outcome                                                                             |
      | include_snapshot omitted (defaults to false)                             | the ad platform adapter exists                                   | no include_snapshot param  | no snapshot or snapshot_unavailable_reason on any package                                     |
      | include_snapshot explicitly false                                        | the ad platform adapter exists                                   | include_snapshot false      | no snapshot or snapshot_unavailable_reason on any package                                     |
      | include_snapshot true, adapter supports, snapshot returned               | the adapter supports realtime reporting and data is available    | include_snapshot true       | package "pkg-001" should include a snapshot                                                  |
      | include_snapshot true, adapter supports, snapshot null for a package     | the adapter supports realtime reporting but no data for pkg-001  | include_snapshot true       | snapshot_unavailable_reason "SNAPSHOT_TEMPORARILY_UNAVAILABLE"                                |
      | include_snapshot true, adapter does not support realtime                 | the adapter does not support realtime reporting                  | include_snapshot true       | snapshot_unavailable_reason "SNAPSHOT_UNSUPPORTED"                                            |
      | include_snapshot true, all packages have snapshot                        | the adapter supports realtime reporting and data for all pkgs    | include_snapshot true       | all packages should include snapshots                                                        |
      | include_snapshot true, mixed — some packages have snapshot, some do not  | the adapter supports reporting, data for pkg-001 but not pkg-002 | include_snapshot true       | pkg-001 has snapshot, pkg-002 has SNAPSHOT_TEMPORARILY_UNAVAILABLE                           |

  @T-UC-019-inv-153-5 @invariant @BR-RULE-153
  Scenario: INV-5 holds - snapshot includes required fields when returned
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And the ad platform adapter supports realtime reporting
    And snapshot data is available for package "pkg-001"
    When the Buyer Agent sends a get_media_buys request with include_snapshot true
    Then the snapshot for package "pkg-001" should include "as_of" timestamp
    And the snapshot should include "staleness_seconds" integer
    And the snapshot should include "impressions" count
    And the snapshot should include "spend" amount
    # BR-RULE-153 INV-5: Required snapshot fields verified

  @T-UC-019-partition-principal @partition @principal_id
  Scenario Outline: Principal scoping - <partition>
    Given <principal_setup>
    When the Buyer Agent sends a get_media_buys request
    Then <expected_outcome>
    # BR-RULE-154: Principal scoping and tenant isolation

    Examples: Valid partitions
      | partition                      | principal_setup                                                                    | expected_outcome                                            |
      | principal_with_media_buys      | an authenticated principal "buyer-001" who owns 3 media buys                       | the response should include 3 media buys                    |
      | principal_with_no_media_buys   | an authenticated principal "buyer-002" who owns no media buys                      | the response should include an empty media_buys array       |

  @T-UC-019-boundary-principal @boundary @principal_id
  Scenario Outline: Principal scoping boundary - <boundary_point>
    Given <principal_setup>
    When the Buyer Agent sends a get_media_buys request
    Then <expected_outcome>
    # BR-RULE-154: Boundary test for principal resolution

    Examples: Boundary values
      | boundary_point                          | principal_setup                                                       | expected_outcome                                                                                                          |
      | valid principal with multiple media buys | an authenticated principal "buyer-001" who owns 5 media buys         | the response should include 5 media buys scoped to buyer-001                                                              |
      | valid principal with zero media buys    | an authenticated principal "buyer-002" who owns no media buys         | the response should include an empty media_buys array                                                                     |
      | principal_id is null                    | an authenticated identity with principal_id null                      | empty media_buys with soft error code "AUTH_REQUIRED" message "Principal ID not found in context"                         |
      | principal_id is empty string            | an authenticated identity with principal_id ""                        | empty media_buys with soft error code "AUTH_REQUIRED" message "Principal ID not found in context"                         |
      | principal_id not in registry            | an authenticated principal "buyer-ghost" not in registry              | empty media_buys with soft error code "AUTH_REQUIRED" message "Principal buyer-ghost not found"                           |
      | identity not resolved (no auth)         | no authentication context                                             | hard error code "AUTH_REQUIRED" raised before any DB access                                                               |

  @T-UC-019-inv-154-tenant @invariant @BR-RULE-154
  Scenario: INV-1 and INV-5 hold - two-layer isolation prevents cross-principal access
    Given an authenticated principal "buyer-001" who owns media buy "mb-001"
    And an authenticated principal "buyer-002" who owns media buy "mb-002"
    When "buyer-001" sends a get_media_buys request
    Then the response should include media buy "mb-001"
    And the response should not include media buy "mb-002"
    # BR-RULE-154 INV-1: Database scoped to tenant
    # BR-RULE-154 INV-5: Results filtered to principal only

  @T-UC-019-inv-150-1 @invariant @BR-RULE-150
  Scenario: INV-1 holds - pre-flight media buy has pending_start status
    Given the principal "buyer-001" owns media buy "mb-001" with start_date "2026-04-01" and end_date "2026-04-30"
    And today is "2026-03-15"
    When the Buyer Agent sends a get_media_buys request with status_filter "pending_start"
    Then the response should include media buy "mb-001" with status "pending_start"
    # BR-RULE-150 INV-1: today < start_date yields pending_start.
    # CORRECTED to AdCP 3.1 enums/media-buy-status.json @ v3.1-04f59d2d5: pre-flight is pending_start
    # ("ready to serve, waiting for its flight date"); "pending_activation" is not a 3.1 wire value.

  @T-UC-019-inv-150-2 @invariant @BR-RULE-150
  Scenario: INV-2 holds - in-flight media buy has active status
    Given the principal "buyer-001" owns media buy "mb-001" with start_date "2026-03-01" and end_date "2026-03-31"
    And today is "2026-03-15"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the response should include media buy "mb-001" with status "active"
    # BR-RULE-150 INV-2: start_date <= today <= end_date yields active

  @T-UC-019-inv-150-3 @invariant @BR-RULE-150
  Scenario: INV-3 holds - post-flight media buy has completed status
    Given the principal "buyer-001" owns media buy "mb-001" with start_date "2026-02-01" and end_date "2026-02-28"
    And today is "2026-03-15"
    When the Buyer Agent sends a get_media_buys request with status_filter "completed"
    Then the response should include media buy "mb-001" with status "completed"
    # BR-RULE-150 INV-3: today > end_date yields completed

  @T-UC-019-inv-151-1 @invariant @BR-RULE-151
  Scenario: INV-1 holds - default filter returns only active media buys
    Given the principal "buyer-001" owns active media buy "mb-001" and completed media buy "mb-002"
    # Pin the clock: the seed builds mb-001/mb-002 flight windows around this same
    # "today" (mock_today), so the query MUST evaluate status against it too, else
    # mb-001's window is in the past under the real clock and it reads as completed.
    And today is "2026-03-15"
    When the Buyer Agent sends a get_media_buys request with no status_filter
    Then the response should include media buy "mb-001"
    And the response should not include media buy "mb-002"
    # BR-RULE-151 INV-1: null status_filter defaults to {active}

  @T-UC-019-inv-151-4 @invariant @BR-RULE-151 @error
  Scenario: INV-4 violated - unknown status value rejected
    Given an authenticated Buyer with principal_id "buyer-001"
    When the Buyer Agent sends a get_media_buys request with status_filter "expired"
    Then the operation should fail with error code "STATUS_FILTER_INVALID_VALUE"
    And the error message should indicate "expired" is not a valid MediaBuyStatus
    And the error should include a "suggestion" field
    And the suggestion should contain "pending_activation, active"
    # BR-RULE-151 INV-4: Unknown status value rejected with suggestion

  @T-UC-019-inv-152-1 @invariant @BR-RULE-152
  Scenario: INV-1 holds - approved creative maps to approved
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And package "pkg-001" has a creative with internal status "approved"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the creative approval should have approval_status "approved"
    # BR-RULE-152 INV-1: approved maps to approved

  @T-UC-019-inv-152-2 @invariant @BR-RULE-152
  Scenario: INV-2 holds - rejected creative maps to rejected with reason
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And package "pkg-001" has a creative with internal status "rejected" and rejection_reason "Text overlaps safe zone"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the creative approval should have approval_status "rejected"
    And the rejection_reason should be "Text overlaps safe zone"
    # BR-RULE-152 INV-2: rejected maps to rejected with reason

  @T-UC-019-inv-152-3 @invariant @BR-RULE-152
  Scenario: INV-3 holds - unrecognized internal status maps to pending_review
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And package "pkg-001" has a creative with internal status "in_review"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the creative approval should have approval_status "pending_review"
    # BR-RULE-152 INV-3: Catch-all maps unknown status to pending_review

  @T-UC-019-inv-153-1 @invariant @BR-RULE-153
  Scenario: INV-1 holds - no snapshot fields when not requested
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    When the Buyer Agent sends a get_media_buys request with include_snapshot false
    Then package "pkg-001" should not have a snapshot field
    And package "pkg-001" should not have a snapshot_unavailable_reason field
    # BR-RULE-153 INV-1: Snapshot not requested means no snapshot fields

  @T-UC-019-inv-153-3 @invariant @BR-RULE-153 @error
  Scenario: INV-3 holds - unsupported adapter sets SNAPSHOT_UNSUPPORTED for all packages
    Given the principal "buyer-001" owns media buy "mb-001" with packages "pkg-001" and "pkg-002"
    And the ad platform adapter does not support realtime reporting
    When the Buyer Agent sends a get_media_buys request with include_snapshot true
    Then package "pkg-001" should have snapshot_unavailable_reason "SNAPSHOT_UNSUPPORTED"
    And package "pkg-002" should have snapshot_unavailable_reason "SNAPSHOT_UNSUPPORTED"
    And the error should include a "suggestion" field
    And the suggestion should contain "adapter" or "realtime" or "reporting"
    # BR-RULE-153 INV-3: All packages get SNAPSHOT_UNSUPPORTED

  @T-UC-019-inv-153-4 @invariant @BR-RULE-153
  Scenario: INV-4 holds - temporarily unavailable when adapter supports but no data
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And the ad platform adapter supports realtime reporting
    And no snapshot data is available for package "pkg-001"
    When the Buyer Agent sends a get_media_buys request with include_snapshot true
    Then package "pkg-001" should have snapshot_unavailable_reason "SNAPSHOT_TEMPORARILY_UNAVAILABLE"
    # BR-RULE-153 INV-4: Package without data gets TEMPORARILY_UNAVAILABLE

  @T-UC-019-inv-150-6 @invariant @BR-RULE-150 @schema-v3.1
  Scenario: INV-6 holds - persisted active with is_paused true returns paused (override)
    Given the principal "buyer-001" owns media buy "mb-001" with persisted status "active" and is_paused true
    And media buy "mb-001" has start_date "2026-03-01" and end_date "2026-03-31"
    And today is "2026-03-15"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" should have status "paused"
    # BR-RULE-150 INV-6: is_paused=true overrides the flight-window refinement to paused
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-150-7 @invariant @BR-RULE-150 @schema-v3.1
  Scenario Outline: INV-7 holds - terminal persisted status passes through unchanged
    Given the principal "buyer-001" owns media buy "mb-001" with persisted status "<persisted>"
    And media buy "mb-001" has start_date "2026-03-01" and end_date "2026-03-31"
    And today is "2026-03-15"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" should have status "<expected>"
    # BR-RULE-150 INV-7: terminal lifecycle states pass through; no flight-window refinement
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples:
      | persisted | expected  |
      | paused    | paused    |
      | completed | completed |
      | rejected  | rejected  |
      | canceled  | canceled  |

  @T-UC-019-inv-150-8 @invariant @BR-RULE-150 @schema-v3.1
  Scenario Outline: INV-8 holds - pre-serving persisted states map to their pending status
    Given the principal "buyer-001" owns media buy "mb-001" with persisted status "<persisted>"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" should have status "<expected>"
    # BR-RULE-150 INV-8: pre-serving persisted states map to their pending status; no flight refinement.
    # CORRECTED to AdCP 3.1 enums/media-buy-status.json @ v3.1-04f59d2d5: a draft buy has no creatives
    # assigned, so it is pending_creatives ("approved but has no creatives"), NOT pending_start
    # ("ready to serve, waiting for its flight date"). pending / pending_approval are pre-serving
    # ready states -> pending_start.
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples:
      | persisted        | expected          |
      | draft            | pending_creatives |
      | pending          | pending_start     |
      | pending_approval | pending_start     |

  @T-UC-019-inv-150-9 @invariant @BR-RULE-150 @schema-v3.1
  Scenario: INV-9 holds - persisted failed maps to rejected
    Given the principal "buyer-001" owns media buy "mb-001" with persisted status "failed"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" should have status "rejected"
    # BR-RULE-150 INV-9: persisted 'failed' has no v3.1 wire equivalent; maps to closest terminal 'rejected'
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-150-10 @invariant @BR-RULE-150 @schema-v3.1
  Scenario Outline: INV-10 holds - pending_creatives and pending_start pass through unchanged
    Given the principal "buyer-001" owns media buy "mb-001" with persisted status "<persisted>"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" should have status "<persisted>"
    # BR-RULE-150 INV-10: pending_creatives/pending_start pass through; no flight refinement
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples:
      | persisted         |
      | pending_creatives |
      | pending_start     |

  @T-UC-019-inv-150-11 @invariant @BR-RULE-150 @schema-v3.1
  Scenario: INV-11 holds - unknown persisted status defaults to active then flight-refines
    # Unmapped status must fit the status column (varchar(20)); the exact
    # string is irrelevant — any value absent from PERSISTED_STATUS_TO_CANONICAL
    # exercises the defensive default-to-active path.
    Given the principal "buyer-001" owns media buy "mb-001" with persisted status "unmapped_state" and is_paused false
    And media buy "mb-001" has start_date "2026-03-01" and end_date "2026-03-31"
    And today is "2026-03-15"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" should have status "active"
    # BR-RULE-150 INV-11: unknown persisted strings default to active and run flight-window refinement (defensive)

  @T-UC-019-inv-151-5 @invariant @BR-RULE-151 @schema-v3.1
  Scenario: INV-5 holds - status_filter omitted AND media_buy_ids supplied applies no implicit filter
    Given the principal "buyer-001" owns media buy "mb-001" with status "active"
    And the principal "buyer-001" owns media buy "mb-002" with status "completed"
    And the principal "buyer-001" owns media buy "mb-003" with status "canceled"
    When the Buyer Agent sends a get_media_buys request with no status_filter and media_buy_ids ["mb-001","mb-002","mb-003"]
    Then the response should include media buy "mb-001"
    And the response should include media buy "mb-002"
    And the response should include media buy "mb-003"
    # BR-RULE-151 INV-5: explicit media_buy_ids suppresses the implicit {active} default

  @T-UC-019-partition-history @partition @include_history @schema-v3.1
  Scenario Outline: include_history partitions - <partition>
    Given the principal "buyer-001" owns media buy "mb-001" with <history_state>
    When the Buyer Agent sends a get_media_buys request with <request_form>
    Then <expected_outcome>
    # BR-RULE-289: include_history bounded 0..1000; default 0; history[] absent unless > 0
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples: Valid partitions
      | partition                       | history_state                                 | request_form                          | expected_outcome                                                                                |
      | omitted_default                 | 3 history entries                             | no include_history parameter         | each media buy entry should omit the history field                                              |
      | zero_explicit                   | 3 history entries                             | include_history 0                    | each media buy entry should omit the history field                                              |
      | monitoring_range                | 8 history entries                             | include_history 5                    | the history array should contain 5 entries most recent first                                    |
      | max_boundary                    | 12 history entries                            | include_history 1000                 | the history array should contain 12 entries most recent first                                   |
      | more_requested_than_available   | 12 history entries                            | include_history 50                   | the history array should contain 12 entries most recent first                                   |

  @T-UC-019-boundary-history @boundary @include_history @schema-v3.1
  Scenario Outline: include_history boundary - <boundary_point>
    Given the principal "buyer-001" owns media buy "mb-001" with 10 history entries
    When the Buyer Agent sends a get_media_buys request with <request_form>
    Then <expected_outcome>
    # BR-RULE-289: range [0, 1000] integer; below or above is VALIDATION_ERROR
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples: Boundary values
      | boundary_point                            | request_form                  | expected_outcome                                                                          |
      | include_history omitted                   | no include_history parameter | the history field should be absent on each media buy entry                                |
      | include_history = 0 (lower bound inclusive) | include_history 0           | the history field should be absent on each media buy entry                                |
      | include_history = 1                       | include_history 1            | the history array should contain 1 entry                                                  |
      | include_history = 1000 (upper bound inclusive) | include_history 1000     | the history array should contain 10 entries (min of include_history and available)        |
      | include_history = 1001                    | include_history 1001         | error code "VALIDATION_ERROR" with suggestion mentioning range "[0, 1000]"                |
      | include_history = -1                      | include_history -1           | error code "VALIDATION_ERROR" with suggestion mentioning range "[0, 1000]"                |
      | include_history = 5.5 (non-integer)       | include_history 5.5          | error code "VALIDATION_ERROR" with suggestion mentioning integer                          |

  @T-UC-019-inv-289-3 @invariant @BR-RULE-289 @schema-v3.1
  Scenario: INV-3 holds - response returns min(include_history, available) most recent entries
    Given the principal "buyer-001" owns media buy "mb-001" with 12 history entries created over time
    When the Buyer Agent sends a get_media_buys request with include_history 5
    Then the response media buy "mb-001" history array should contain 5 entries
    And the history entries should be ordered most recent first
    # BR-RULE-289 INV-3: min(include_history, available) most recent first
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-289-5 @invariant @BR-RULE-289 @schema-v3.1
  Scenario: INV-5 holds - history entries are append-only and byte-identical across queries
    Given the principal "buyer-001" owns media buy "mb-001" with a history entry at revision 3
    When the Buyer Agent sends two get_media_buys requests with include_history 10 at times t1 and t2
    Then the entry at revision 3 from the t1 response should be byte-identical to the entry at revision 3 from the t2 response
    # BR-RULE-289 INV-5: sellers MUST NOT modify or delete previously emitted entries
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-289-6 @invariant @BR-RULE-289 @schema-v3.1
  Scenario: INV-6 holds - history entry actor is derived from authentication context
    Given the principal "buyer-001" owns media buy "mb-001"
    And buyer "buyer-001" performs an update that creates a history entry
    When the Buyer Agent sends a get_media_buys request with include_history 5
    Then the new history entry actor should reflect the authenticated identity "buyer-001"
    And the actor value should not be derived from any caller-supplied field
    # BR-RULE-289 INV-6: actor derived from auth context; never caller-provided
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-289-9 @invariant @BR-RULE-289 @schema-v3.1
  Scenario: INV-9 holds - package-targeted history entries carry package_id
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And a history entry was created for action "package_paused" targeting package "pkg-001"
    And a separate history entry was created for action "updated_budget" at the media-buy level
    When the Buyer Agent sends a get_media_buys request with include_history 5
    Then the package_paused entry should have package_id "pkg-001"
    And the updated_budget entry should omit package_id
    # BR-RULE-289 INV-9: package-targeted entries carry package_id; buy-level entries omit it

  @T-UC-019-partition-valid-actions @partition @valid_actions @schema-v3.1
  Scenario Outline: valid_actions per status - <partition>
    Given the principal "buyer-001" owns media buy "mb-001" with returned wire status "<status>"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" valid_actions should equal <expected_actions>
    # BR-RULE-290: valid_actions deterministically derived from wire status via MEDIA_BUY_STATE_MACHINE
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples: Valid partitions
      | partition                  | status            | expected_actions                                                                          |
      | pending_creatives_actions  | pending_creatives | [cancel, update_budget, update_dates, update_packages, add_packages, sync_creatives]      |
      | pending_start_actions      | pending_start     | [cancel, update_budget, update_dates, update_packages, add_packages]                      |
      | active_actions             | active            | [pause, cancel, update_budget, update_dates, update_packages, add_packages]               |
      | paused_actions             | paused            | [resume, cancel, update_budget, update_dates]                                             |
      | terminal_empty             | completed         | []                                                                                        |

  @T-UC-019-boundary-valid-actions @boundary @valid_actions @schema-v3.1
  Scenario Outline: valid_actions boundary - status=<status>
    Given the principal "buyer-001" owns media buy "mb-001" with returned wire status "<status>"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then every value in media buy "mb-001" valid_actions should be drawn from the media-buy-valid-action enum
    And the action set should match the state-machine table for status "<status>"
    # BR-RULE-290: closed enum {pause, resume, cancel, update_budget, update_dates, update_packages, add_packages, sync_creatives}
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples: Boundary values
      | status            |
      | pending_creatives |
      | pending_start     |
      | active            |
      | paused            |
      | completed         |
      | rejected          |
      | canceled          |

  @T-UC-019-inv-290-3 @invariant @BR-RULE-290 @schema-v3.1
  Scenario: INV-3 holds - active status includes pause and excludes resume
    Given the principal "buyer-001" owns media buy "mb-001" with returned wire status "active"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" valid_actions should include "pause"
    And the media buy "mb-001" valid_actions should not include "resume"
    # BR-RULE-290 INV-3: active -> pause yes, resume no
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-290-4 @invariant @BR-RULE-290 @schema-v3.1
  Scenario: INV-4 holds - paused status includes resume and excludes pause/update_packages/add_packages
    Given the principal "buyer-001" owns media buy "mb-001" with returned wire status "paused"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" valid_actions should include "resume"
    And the media buy "mb-001" valid_actions should not include "pause"
    And the media buy "mb-001" valid_actions should not include "update_packages"
    And the media buy "mb-001" valid_actions should not include "add_packages"
    # BR-RULE-290 INV-4: paused -> resume yes; pause/update_packages/add_packages no
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-290-5 @invariant @BR-RULE-290 @schema-v3.1
  Scenario Outline: INV-5 holds - terminal statuses emit empty valid_actions array (not omitted)
    Given the principal "buyer-001" owns media buy "mb-001" with returned wire status "<terminal_status>"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" should include a valid_actions field
    And the media buy "mb-001" valid_actions should be an empty array
    # BR-RULE-290 INV-5: terminal statuses -> valid_actions = [] (positive end-of-lifecycle signal)

    Examples:
      | terminal_status |
      | completed       |
      | rejected        |
      | canceled        |

  @T-UC-019-partition-revision @partition @revision @schema-v3.1
  Scenario Outline: revision partitions - <partition>
    Given the principal "buyer-001" owns media buy "mb-001" with <revision_state>
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" revision should be <expected>
    # BR-RULE-291: revision >= 1, per-buy monotonic counter
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples: Valid partitions
      | partition         | revision_state                                                       | expected               |
      | just_created      | persisted revision 1 and no subsequent writes                        | 1                      |
      | after_writes      | persisted revision 5 after four state-changing writes                | 5                      |
      | idempotent_reads  | persisted revision 7 and no intervening writes between two reads     | 7 on both reads        |

  @T-UC-019-boundary-revision @boundary @revision @schema-v3.1
  Scenario Outline: revision boundary - <boundary_point>
    Given the principal "buyer-001" owns media buy "mb-001" with <revision_state>
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then <expected_outcome>
    # BR-RULE-291: schema minimum 1; 0/negative/missing -> SCHEMA_VIOLATION
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples: Boundary values
      | boundary_point                   | revision_state                                  | expected_outcome                                                              |
      | revision = 1 (minimum inclusive) | persisted revision 1                            | the media buy "mb-001" revision should be 1                                   |
      | revision = 0                     | persisted revision 0 (defective seller)         | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION" |
      | revision = -1                    | persisted revision -1 (defective seller)        | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION" |
      | revision absent                  | persisted store missing revision (defective seller) | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION" |

  @T-UC-019-inv-291-1 @invariant @BR-RULE-291 @schema-v3.1
  Scenario: INV-1 holds - every returned media buy has revision integer >= 1
    Given the principal "buyer-001" owns 3 media buys
    When the Buyer Agent sends a get_media_buys request
    Then every returned media buy should include an integer revision field
    And every revision should be >= 1
    # BR-RULE-291 INV-1: revision is always present, integer, minimum 1
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-291-4 @invariant @BR-RULE-291 @schema-v3.1
  Scenario: INV-4 holds - two reads with no intervening write return the same revision
    Given the principal "buyer-001" owns media buy "mb-001" with persisted revision 4
    And no state-changing writes occur between two reads
    When the Buyer Agent sends a get_media_buys request at time t1
    And the Buyer Agent sends a get_media_buys request at time t2 (t1 < t2)
    Then the revision at t1 should equal the revision at t2
    # BR-RULE-291 INV-4: no intervening write -> revision unchanged across reads
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-291-5 @invariant @BR-RULE-291 @schema-v3.1
  Scenario: INV-5 holds - intervening successful write monotonically increases revision
    Given the principal "buyer-001" owns media buy "mb-001" with persisted revision 4
    When the Buyer Agent sends a get_media_buys request at time t1
    And one successful update_media_buy lands between t1 and t2
    And the Buyer Agent sends a get_media_buys request at time t2
    Then the revision at t2 should be strictly greater than the revision at t1
    # BR-RULE-291 INV-5: every successful state-changing write increments revision by at least 1

  @T-UC-019-inv-confirmed-at-present @invariant @confirmed_at @schema-v3.1
  Scenario: confirmed_at present - every returned media buy carries an ISO 8601 timestamp set at creation
    Given the principal "buyer-001" owns media buy "mb-001" that was successfully created at "2026-05-01T12:00:00Z"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" should include a confirmed_at field
    And the confirmed_at value should be the ISO 8601 timestamp "2026-05-01T12:00:00Z"
    # POST-S6 / INT-006: confirmed_at is set when the buy transitions out of pre-create state and is exposed on every read
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-confirmed-at-stable @invariant @confirmed_at @schema-v3.1
  Scenario: confirmed_at stable across subsequent reads - timestamp does not drift with later writes
    Given the principal "buyer-001" owns media buy "mb-001" with confirmed_at "2026-05-01T12:00:00Z"
    When the Buyer Agent sends a get_media_buys request at time t1
    And one successful update_media_buy lands between t1 and t2
    And the Buyer Agent sends a get_media_buys request at time t2
    Then the confirmed_at at t1 should equal "2026-05-01T12:00:00Z"
    And the confirmed_at at t2 should equal "2026-05-01T12:00:00Z"
    # POST-S6 / INT-006: confirmed_at reflects the original confirmation moment; revision updates do not rewrite it
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-lifecycle-approval @invariant @confirmed_at @BR-RULE-291 @schema-v3.1
  Scenario: Manual approval lifecycle - approval advances revision and stamps confirmed_at at the approval instant
    Given the tenant requires manual approval for media buys
    And the Buyer Agent has created media buy "mb-pending" awaiting seller approval
    When the seller approves media buy "mb-pending"
    And the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-pending"]
    Then the media buy "mb-pending" revision should be greater than its revision at creation
    And the media buy "mb-pending" confirmed_at should equal the approval instant
    And the media buy "mb-pending" confirmed_at should not equal its created_at
    # BR-RULE-291 / spec MUST: revision increments on every state change — seller approval included
    # POST-S6 / INT-006: confirmed_at is the seller's confirmation instant (approval moment on the
    # deferred path), NOT the buyer's create-request time (created_at)
    # @source repo=adcp ref=3.1.0-beta.3 path=docs/media-buy/specification.mdx (revision MUST
    #         increment on every state change; confirmed_at stamped at IO-signing per the
    #         sales-guaranteed conformance storyboard)
    # NOTE: hand-authored obligation (neighbors are generated from adcp-req with
    #       ref=v3.1-<sha>). Reconcile upstream into adcp-req so this is generated
    #       and its @source aligns automatically — tracked in #1565.

  @T-UC-019-partition-confirmed-at @partition @confirmed_at @schema-v3.1
  Scenario Outline: confirmed_at - <partition>
    Given the principal "buyer-001" owns media buy "mb-001" with <buy_state>
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then <expected_outcome>
    # POST-S6 / INT-006: confirmed_at presence and ISO 8601 shape

    Examples: Valid partitions
      | partition                       | buy_state                                                  | expected_outcome                                                                                  |
      | confirmed_buy_carries_timestamp | a successful create stamping confirmed_at "2026-05-01T12:00:00Z" | the media buy "mb-001" confirmed_at should equal "2026-05-01T12:00:00Z"                          |
      | confirmed_at_includes_timezone  | confirmed_at "2026-05-01T12:00:00+00:00"                   | the media buy "mb-001" confirmed_at should be an ISO 8601 string with a timezone designator       |

    Examples: Invalid partitions
      | partition                       | buy_state                                                  | expected_outcome                                                                                  |
      | confirmed_at_missing_on_buy     | persisted store missing confirmed_at (defective seller)    | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"        |
      | confirmed_at_not_iso8601        | persisted confirmed_at "2026-05-01 12:00:00" (no T, no TZ) | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"        |

  @T-UC-019-partition-package-creative-deadline @partition @creative_deadline @schema-v3.1
  Scenario Outline: package creative_deadline - <partition>
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And media buy "mb-001" carries <buy_state>
    And package "pkg-001" carries <package_state>
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then <expected_outcome>
    # POST-S2 / INT-002: package-level creative_deadline; when absent, the buy-level creative_deadline applies
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples: Valid partitions
      | partition                                  | buy_state                                            | package_state                                            | expected_outcome                                                                                                                                                |
      | package_level_creative_deadline_set        | buy creative_deadline "2026-04-15T00:00:00Z"        | package creative_deadline "2026-04-10T00:00:00Z"        | the package "pkg-001" creative_deadline should equal "2026-04-10T00:00:00Z" and override the buy-level value                                                    |
      | package_level_absent_inherits_from_buy     | buy creative_deadline "2026-04-15T00:00:00Z"        | no package creative_deadline                              | the package "pkg-001" creative_deadline should be omitted and the effective deadline should be the buy-level "2026-04-15T00:00:00Z"                              |
      | both_levels_absent_is_legal                | no buy creative_deadline                              | no package creative_deadline                              | the media buy "mb-001" should omit creative_deadline and the package "pkg-001" should omit creative_deadline (no error)                                          |
      | package_level_set_buy_level_absent         | no buy creative_deadline                              | package creative_deadline "2026-04-10T00:00:00Z"        | the package "pkg-001" creative_deadline should equal "2026-04-10T00:00:00Z" and the buy-level field should be omitted                                            |

    Examples: Invalid partitions
      | partition                                  | buy_state                                            | package_state                                            | expected_outcome                                                                                                                                                |
      | package_creative_deadline_not_iso8601     | buy creative_deadline "2026-04-15T00:00:00Z"        | package creative_deadline "next Friday"                  | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"                                                                      |
      | package_creative_deadline_wrong_type       | no buy creative_deadline                              | package creative_deadline 1747094400 (integer not string)| the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"                                                                      |

  @T-UC-019-inv-package-creative-deadline-inheritance @invariant @creative_deadline @schema-v3.1
  Scenario: package creative_deadline overrides buy-level when set, otherwise inherits
    Given the principal "buyer-001" owns media buy "mb-001" with creative_deadline "2026-04-15T00:00:00Z"
    And media buy "mb-001" has package "pkg-001" with creative_deadline "2026-04-10T00:00:00Z"
    And media buy "mb-001" has package "pkg-002" with no creative_deadline
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the package "pkg-001" creative_deadline should equal "2026-04-10T00:00:00Z"
    And the package "pkg-002" creative_deadline should be omitted
    And the effective deadline for "pkg-002" should be the buy-level "2026-04-15T00:00:00Z"
    # POST-S2 / INT-002: package-level value overrides; absence inherits from buy

  @T-UC-019-partition-cancellation @partition @cancellation @schema-v3.1
  Scenario Outline: cancellation block - <partition>
    Given the principal "buyer-001" owns media buy "mb-001" with <buy_state>
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then <expected_outcome>
    # BR-RULE-292: block presence iff terminal flag true; required {canceled_at, canceled_by}; optional reason<=500
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples: Valid partitions
      | partition                     | buy_state                                                                                              | expected_outcome                                                                                                  |
      | buy_canceled_minimal          | status canceled and cancellation {canceled_at:'2026-05-01T12:00:00Z', canceled_by:'buyer'}            | the media buy "mb-001" should include a cancellation block with canceled_by "buyer"                                |
      | buy_canceled_with_reason      | status canceled and cancellation {canceled_at:'2026-05-01T12:00:00Z', canceled_by:'seller', reason:'Policy violation'} | the media buy "mb-001" cancellation should include reason "Policy violation"                                 |
      | package_canceled_individually | status active and packages[2].canceled true with package cancellation {canceled_at:..., canceled_by:'buyer'} | the media buy "mb-001" should omit cancellation at buy level and packages[2] should carry a cancellation block |
      | buy_canceled_packages_silent  | status canceled and per-package cancellation blocks omitted                                            | the media buy "mb-001" buy-level cancellation should be present and per-package cancellation blocks should be absent |
      | not_canceled                  | status active                                                                                          | the media buy "mb-001" should not carry a cancellation block                                                       |

    Examples: Invalid partitions
      | partition                       | buy_state                                                                                                  | expected_outcome                                                                                            |
      | present_without_terminal_flag   | status active but seller emits cancellation block                                                          | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"                  |
      | missing_required_field          | status canceled and cancellation {canceled_at:'2026-05-01T12:00:00Z'} (canceled_by missing)                | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"                  |
      | invalid_canceled_by             | status canceled and cancellation {canceled_at:'2026-05-01T12:00:00Z', canceled_by:'operator'}              | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"                  |
      | additional_property             | status canceled and cancellation {canceled_at:..., canceled_by:'buyer', refund_status:'pending'}           | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"                  |

  @T-UC-019-boundary-cancellation @boundary @cancellation @schema-v3.1
  Scenario Outline: cancellation boundary - <boundary_point>
    Given the principal "buyer-001" owns media buy "mb-001" with <buy_state>
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then <expected_outcome>
    # BR-RULE-292: boundary - reason length, terminal-flag toggle, additionalProperties
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples: Boundary values
      | boundary_point                                         | buy_state                                                                       | expected_outcome                                                                            |
      | status=canceled, block present with required fields    | status canceled and cancellation {canceled_at:'2026-05-01T12:00:00Z', canceled_by:'buyer'} | the media buy "mb-001" cancellation block with required fields should be accepted          |
      | status=canceled, block present with reason at 500 chars | status canceled and cancellation reason exactly 500 characters                  | the media buy "mb-001" cancellation reason should be accepted                              |
      | status=canceled, block present with reason at 501 chars | status canceled and cancellation reason exactly 501 characters                  | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"  |
      | status=active, no block                                 | status active                                                                  | the media buy "mb-001" should not carry a cancellation block                                |
      | status=active, block present                            | status active but seller emits cancellation block                              | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"  |
      | package canceled=true, block present                    | status active and packages[1].canceled true with cancellation                  | the packages[1] should carry a cancellation block                                          |
      | package canceled=false, block present                   | status active and packages[1].canceled false but emits cancellation             | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"  |
      | canceled_by='operator'                                  | status canceled and cancellation canceled_by 'operator' (not in enum)           | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"  |

  @T-UC-019-inv-292-1 @invariant @BR-RULE-292 @schema-v3.1
  Scenario: INV-1 holds - canceled status MUST carry cancellation block
    Given the principal "buyer-001" owns media buy "mb-001" with status "canceled" and persisted cancellation metadata
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" should include a cancellation block
    And the cancellation block should include canceled_at and canceled_by
    # BR-RULE-292 INV-1: status==canceled -> cancellation block MUST be present
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-292-2 @invariant @BR-RULE-292 @schema-v3.1
  Scenario: INV-2 holds - non-canceled status MUST NOT carry cancellation block
    Given the principal "buyer-001" owns media buy "mb-001" with status "active"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" should not include a cancellation block
    # BR-RULE-292 INV-2: status!=canceled -> cancellation block MUST be absent
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-292-7 @invariant @BR-RULE-292 @schema-v3.1
  Scenario: INV-7 holds - additional properties in cancellation block are forbidden
    Given the principal "buyer-001" owns media buy "mb-001" with status "canceled"
    And the seller emits cancellation block with an extra field "refund_status"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"
    And the validation message should reference cancellation.additionalProperties
    # BR-RULE-292 INV-7: additionalProperties:false on cancellation; ride-along metadata belongs in ext

  @T-UC-019-inv-293-2 @invariant @BR-RULE-293 @error @ext-e @schema-v3.1
  Scenario: INV-2 holds - v3.x account (AccountReference) triggers ACCOUNT_FILTER_NOT_SUPPORTED before any DB read
    Given an authenticated Buyer with principal_id "buyer-001"
    When the Buyer Agent sends a get_media_buys request with account {brand:"brand-x", operator:"op-y"}
    Then the operation should fail with error code "ACCOUNT_FILTER_NOT_SUPPORTED"
    And the error code should be "ACCOUNT_FILTER_NOT_SUPPORTED"
    And the error recovery classification should be "correctable"
    And no database query should have been executed
    And the error should include a "suggestion" field
    And the suggestion should contain "remove" or "omit" or "without the `account` filter"
    # BR-RULE-293 INV-2: v3.x AccountReference -> AdCPValidationError before DB read
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-293-5 @invariant @BR-RULE-293 @error @schema-v3.1
  Scenario: INV-5 holds - account-filter validation failure yields empty media_buys with no DB query
    Given an authenticated Buyer with principal_id "buyer-001"
    When the Buyer Agent sends a get_media_buys request with account_id "acc-001"
    Then the response media_buys array should be empty
    And no database query should have been executed
    And the error should include a "suggestion" field
    And the suggestion should contain "omit" or "without the `account` filter"
    # BR-RULE-293 INV-5: validation fails -> no DB query; no partial result leak

  @T-UC-019-partition-targeting-rehydration @partition @targeting_overlay @schema-v3.1
  Scenario Outline: targeting_overlay rehydration - <partition>
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And package "pkg-001" persisted package_config has <persisted_state>
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then <expected_outcome>
    # BR-RULE-294: per-package fail-soft; TypeError caught narrowly; ValidationError NOT caught
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples: Valid partitions
      | partition                            | persisted_state                                       | expected_outcome                                                                                                                                                |
      | no_targeting_persisted               | no targeting_overlay and no legacy targeting          | the package "pkg-001" targeting_overlay should be null and no error should appear in response.errors[] for "pkg-001"                                            |
      | targeting_rehydrates_cleanly         | targeting_overlay {geo:['US']}                        | the package "pkg-001" targeting_overlay should be a Targeting object with geo ["US"]                                                                            |
      | legacy_targeting_key                 | no targeting_overlay but legacy targeting {geo:['US']} | the package "pkg-001" targeting_overlay should be a Targeting object with geo ["US"]                                                                            |
      | rehydration_typeerror_partial_success | targeting_overlay set to the string 'not a dict'      | the package "pkg-001" targeting_overlay should be null and response.errors[] should include an INTERNAL_ERROR entry with message starting "TARGETING_REHYDRATION_FAILED:" |

  @T-UC-019-inv-294-3 @invariant @BR-RULE-294 @error @schema-v3.1
  Scenario: INV-3 holds - TypeError during Targeting instantiation yields non-fatal INTERNAL_ERROR + null overlay
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And package "pkg-001" persisted targeting_overlay is a string (will raise TypeError on Targeting(**str))
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then a warning should be logged with media_buy_id "mb-001" and package_id "pkg-001"
    And response.errors[] should include an entry with code "INTERNAL_ERROR"
    And that errors[] entry message should start with "TARGETING_REHYDRATION_FAILED:"
    And that errors[] entry field selector should be "media_buys[].packages[pkg-001].targeting_overlay"
    And the package "pkg-001" targeting_overlay should be null
    And the error should include a "suggestion" field
    And the suggestion should contain "package_config" or "rehydrated"
    # BR-RULE-294 INV-3: narrow TypeError catch -> warn + non-fatal Error + null overlay
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-294-5 @invariant @BR-RULE-294 @schema-v3.1
  Scenario: INV-5 holds - one corrupted package does not break sibling packages in the same buy
    Given the principal "buyer-001" owns media buy "mb-001" with packages "pkg-001" and "pkg-002"
    And package "pkg-001" persisted targeting_overlay is corrupted (will raise TypeError)
    And package "pkg-002" persisted targeting_overlay is a valid dict {geo:["US"]}
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the package "pkg-001" targeting_overlay should be null
    And the package "pkg-002" targeting_overlay should be a Targeting object with geo ["US"]
    And response.errors[] should include exactly one TARGETING_REHYDRATION_FAILED entry for ("mb-001", "pkg-001")
    # BR-RULE-294 INV-5: per-package failure isolation
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-294-6 @invariant @BR-RULE-294 @schema-v3.1
  Scenario: INV-6 holds - one buy with a corrupted package does not break sibling buys
    Given the principal "buyer-001" owns media buys "mb-001" and "mb-002"
    And media buy "mb-001" package "pkg-001" has corrupted targeting_overlay (will raise TypeError)
    And media buy "mb-002" has valid persisted state
    When the Buyer Agent sends a get_media_buys request
    Then the response should include media buy "mb-001" with package "pkg-001" targeting_overlay null
    And the response should include media buy "mb-002" rendered normally
    And response.errors[] should include exactly one TARGETING_REHYDRATION_FAILED entry for ("mb-001", "pkg-001")
    # BR-RULE-294 INV-6: per-buy failure isolation across the response
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-294-8 @invariant @BR-RULE-294 @schema-v3.1
  Scenario: INV-8 holds - legacy targeting key consulted only when modern key absent
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And package "pkg-001" persisted package_config has no targeting_overlay key but has legacy targeting {geo:["US"]}
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the package "pkg-001" targeting_overlay should be a Targeting object with geo ["US"]
    # BR-RULE-294 INV-8: pre-rename data compatibility through legacy `targeting` key fallback

  @T-UC-019-partition-delivery-status @partition @delivery_status @snapshot @schema-v3.1
  Scenario Outline: snapshot.delivery_status partitions - <partition>
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And package "pkg-001" activation occurred such that <elapsed_state>
    And the snapshot reports staleness_seconds <staleness_seconds>
    And the package <delivery_observation>
    When the Buyer Agent sends a get_media_buys request with include_snapshot true
    Then the package "pkg-001" snapshot delivery_status should be <expected>
    # BR-RULE-295: not_delivering forbidden until elapsed >= staleness_seconds since activation
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples: Valid partitions
      | partition                       | elapsed_state | staleness_seconds | delivery_observation              | expected                                            |
      | within_staleness_window_delivering | elapsed 300s since activation | 900 | has zero impressions so far       | "delivering"                                        |
      | within_staleness_window_omitted    | elapsed 300s since activation | 900 | has zero impressions so far       | absent                                              |
      | post_staleness_zero_delivery       | elapsed 1800s since activation | 900 | has zero impressions so far       | "not_delivering"                                    |
      | actively_serving                   | elapsed 300s since activation | 900 | is producing impressions          | "delivering"                                        |
      | terminal_completed                 | irrelevant timing             | 900 | has completed flight              | "completed"                                         |
      | terminal_budget_exhausted          | irrelevant timing             | 900 | exhausted its budget              | "budget_exhausted"                                  |
      | terminal_flight_ended              | irrelevant timing             | 900 | reached end of flight             | "flight_ended"                                      |
      | terminal_goal_met                  | irrelevant timing             | 900 | reached optimization goal         | "goal_met"                                          |

    Examples: Invalid partitions
      | partition              | elapsed_state               | staleness_seconds | delivery_observation        | expected                                                                    |
      | not_delivering_too_early | elapsed 300s since activation | 900             | has zero impressions so far | flagged with code "STALENESS_GATE_VIOLATION" because not_delivering forbidden before window elapsed |
      | value_outside_enum       | elapsed 1800s since activation | 900            | has zero impressions so far | flagged with code "SCHEMA_VIOLATION" because the seller emitted "unknown" outside the enum         |

  @T-UC-019-boundary-delivery-status @boundary @delivery_status @snapshot @schema-v3.1
  Scenario Outline: snapshot.delivery_status boundary - <boundary_point>
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And package "pkg-001" has snapshot.staleness_seconds 900 and elapsed time <elapsed> since activation
    And impressions remain 0
    And the seller's candidate delivery_status value is <candidate>
    When the Buyer Agent sends a get_media_buys request with include_snapshot true
    Then the result for "pkg-001" delivery_status should be <expected_outcome>
    # BR-RULE-295: boundary at elapsed=staleness_seconds inclusive
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples: Boundary values
      | boundary_point                                        | elapsed | candidate         | expected_outcome                                                       |
      | elapsed = staleness_seconds-1, candidate=not_delivering | 899     | not_delivering    | flagged with code "STALENESS_GATE_VIOLATION"                          |
      | elapsed = staleness_seconds  (boundary), candidate=not_delivering | 900 | not_delivering    | accepted as "not_delivering"                                          |
      | elapsed = staleness_seconds+1, candidate=not_delivering | 901     | not_delivering    | accepted as "not_delivering"                                          |
      | elapsed < staleness_seconds, candidate=delivering       | 300     | delivering        | accepted as "delivering"                                              |
      | elapsed < staleness_seconds, candidate omitted          | 300     | (omitted)         | the delivery_status field should be absent                            |
      | candidate=completed at any time                          | 50      | completed         | accepted as "completed"                                               |
      | candidate='paused' (not in enum)                         | 1800    | paused            | flagged with code "SCHEMA_VIOLATION"                                  |
      | elapsed < staleness_seconds, delivery_status=delivering | 300     | delivering        | accepted as "delivering"                                              |
      | elapsed < staleness_seconds, delivery_status omitted    | 300     | (omitted)         | the delivery_status field should be absent                            |
      | delivery_status=completed (any time)                     | 50      | completed         | accepted as "completed"                                               |
      | delivery_status='paused' (not in enum)                   | 1800    | paused            | flagged with code "SCHEMA_VIOLATION"                                  |

  @T-UC-019-inv-295-1 @invariant @BR-RULE-295 @schema-v3.1
  Scenario: INV-1 holds - not_delivering forbidden until elapsed >= staleness_seconds
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And package "pkg-001" has snapshot.staleness_seconds 900 and elapsed 600s since activation
    And impressions for "pkg-001" remain 0
    When the Buyer Agent sends a get_media_buys request with include_snapshot true
    Then the snapshot for "pkg-001" should NOT report delivery_status "not_delivering"
    And the snapshot should report delivery_status "delivering" or omit delivery_status
    # BR-RULE-295 INV-1: anti-flapping gate during staleness window
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-295-2 @invariant @BR-RULE-295 @schema-v3.1
  Scenario: INV-2 holds - not_delivering permitted once elapsed >= staleness_seconds AND impressions=0
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And package "pkg-001" has snapshot.staleness_seconds 900 and elapsed 1800s since activation
    And impressions for "pkg-001" remain 0
    When the Buyer Agent sends a get_media_buys request with include_snapshot true
    Then the snapshot for "pkg-001" MAY report delivery_status "not_delivering"
    # BR-RULE-295 INV-2: post-window + zero impressions -> not_delivering legal

  @T-UC-019-partition-invoice-recipient @partition @invoice_recipient @schema-v3.1
  Scenario Outline: invoice_recipient + account echo - <partition>
    Given the principal "buyer-001" owns media buy "mb-001" with <persisted_billing>
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then <expected_outcome>
    # BR-RULE-296: account+invoice_recipient echo with bank_details writeOnly redaction
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples: Valid partitions
      | partition                 | persisted_billing                                                                                                                | expected_outcome                                                                                                                                              |
      | override_echoed_no_bank   | explicit invoice_recipient override {legal_name:'Acme', vat_id:'DE123', address:{...}, bank_details:{iban:'...'}}                | the media buy "mb-001" invoice_recipient should include legal_name "Acme" and should omit bank_details                                                       |
      | no_override_omitted       | no invoice_recipient override (inherits account default)                                                                          | the media buy "mb-001" should not include an invoice_recipient field                                                                                         |
      | override_with_contacts    | explicit invoice_recipient override {legal_name:'Acme', contacts:[{role:'billing', name:'A. Person'}], address:{...}}            | the media buy "mb-001" invoice_recipient should include legal_name and a contacts array                                                                      |
      | account_echoed            | persisted account binding {account_id:'acct_123'}                                                                                | the media buy "mb-001" should include an account snapshot with account_id "acct_123"                                                                         |

    Examples: Invalid partitions
      | partition              | persisted_billing                                                                                            | expected_outcome                                                                                                                              |
      | bank_details_echoed    | seller defectively echoes bank_details on invoice_recipient                                                  | the response should be flagged as schema-invalid for "mb-001" with code "WRITE_ONLY_FIELD_LEAKED" because bank_details appeared in the response   |
      | empty_echo             | seller emits invoice_recipient with no persisted content                                                     | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION" because invoice_recipient must be omitted when empty   |

  @T-UC-019-inv-296-1 @invariant @BR-RULE-296 @schema-v3.1
  Scenario: INV-1 holds - buy bound to account MAY echo the account snapshot
    Given the principal "buyer-001" owns media buy "mb-001" bound to account "acct_123"
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" account field if present should equal the current billing account "acct_123"
    # BR-RULE-296 INV-1: account snapshot reflects current billing target (not creation-time copy)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-296-2 @invariant @BR-RULE-296 @schema-v3.1
  Scenario: INV-2 holds - explicit invoice_recipient override at create echoes the persisted entity
    Given the principal "buyer-001" owns media buy "mb-001" created with explicit invoice_recipient override
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" should include an invoice_recipient field reflecting the persisted (post-transform) value
    # BR-RULE-296 INV-2: echo is descriptive; reflects what seller stored
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-296-3 @invariant @BR-RULE-296 @schema-v3.1
  Scenario: INV-3 holds - no override at create -> invoice_recipient absent on the entry
    Given the principal "buyer-001" owns media buy "mb-001" created without an invoice_recipient override
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" should not include an invoice_recipient field
    # BR-RULE-296 INV-3: account-default inheritance -> field omitted
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

  @T-UC-019-inv-296-4 @invariant @BR-RULE-296 @schema-v3.1
  Scenario: INV-4 holds - bank_details MUST NOT appear in any echoed invoice_recipient (writeOnly redaction)
    Given the principal "buyer-001" owns media buy "mb-001" with persisted invoice_recipient that includes bank_details
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then the media buy "mb-001" invoice_recipient should be present
    And the invoice_recipient should not include a bank_details sub-field
    # BR-RULE-296 INV-4: bank_details is writeOnly per schema; seller stores it but never echoes it

  @T-UC-019-partition-currency-budget @partition @currency @schema-v3.1
  Scenario Outline: media_buy currency + total_budget partitions - <partition>
    Given the principal "buyer-001" owns media buy "mb-001" with <buy_state>
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then <expected_outcome>
    # uc019_currency_and_budget: pure structural constraint (no BR-RULE); validated against v3.1 schema
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buys-response.json

    Examples: Valid partitions
      | partition       | buy_state                                              | expected_outcome                                                          |
      | usd_typical     | currency "USD" and total_budget 10000                  | the media buy "mb-001" should expose currency "USD" and total_budget 10000 |
      | eur_currency    | currency "EUR" and total_budget 5000.50                | the media buy "mb-001" should expose currency "EUR" and total_budget 5000.5 |
      | zero_budget     | currency "USD" and total_budget 0                      | the media buy "mb-001" should expose currency "USD" and total_budget 0    |

    Examples: Invalid partitions
      | partition              | buy_state                                          | expected_outcome                                                                            |
      | lowercase_currency     | currency "usd" and total_budget 10000              | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"   |
      | wrong_length_currency  | currency "USDX" and total_budget 10000             | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"   |
      | negative_budget        | currency "USD" and total_budget -1                 | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"   |
      | missing_currency       | no currency field and total_budget 1000            | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"   |
      | missing_budget         | currency "USD" and no total_budget field           | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"   |

  @T-UC-019-boundary-currency-budget @boundary @currency @schema-v3.1
  Scenario Outline: media_buy currency + total_budget boundary - <boundary_point>
    Given the principal "buyer-001" owns media buy "mb-001" with <buy_state>
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then <expected_outcome>
    # uc019_currency_and_budget: boundary values from constraint YAML

    Examples: Boundary values
      | boundary_point                              | buy_state                                       | expected_outcome                                                                            |
      | currency='USD', total_budget=0 (lower bound) | currency "USD" and total_budget 0              | the media buy "mb-001" should expose currency "USD" and total_budget 0                       |
      | currency='USD', total_budget=-0.01           | currency "USD" and total_budget -0.01          | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"   |
      | currency='US' (2 chars)                      | currency "US" and total_budget 100             | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"   |
      | currency='USDD' (4 chars)                    | currency "USDD" and total_budget 100           | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"   |
      | currency='USD' (3 uppercase chars)           | currency "USD" and total_budget 100            | the media buy "mb-001" should expose currency "USD" and total_budget 100                     |
      | currency='Usd' (mixed case)                  | currency "Usd" and total_budget 100            | the response should be flagged as schema-invalid for "mb-001" with code "SCHEMA_VIOLATION"   |

  @T-UC-019-sandbox-happy @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account get_media_buys returns simulated results with sandbox flag
    Given the principal "buyer-001" owns media buy "mb-001"
    And the request targets a sandbox account
    When the Buyer Agent sends a get_media_buys request
    Then the response should contain "media_buys" array
    And the response should include sandbox equals true
    And no real ad platform API calls should have been made
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-2: real ad platform calls suppressed
    # BR-RULE-209 INV-4: response includes sandbox: true

  @T-UC-019-sandbox-production @invariant @br-rule-209 @sandbox
  Scenario: Production account get_media_buys response does not include sandbox flag
    Given the principal "buyer-001" owns media buy "mb-001"
    And the request targets a production account
    When the Buyer Agent sends a get_media_buys request
    Then the response should contain "media_buys" array
    And the response should not include a sandbox field
    # BR-RULE-209 INV-5: production account -> sandbox absent

  @T-UC-019-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account with invalid request returns real validation error
    Given the request targets a sandbox account
    When the Buyer Agent sends a get_media_buys request with invalid status filter
    Then the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

  @T-UC-019-boundary-sandbox @boundary @sandbox @br-rule-209
  Scenario Outline: sandbox response semantics boundary - <boundary_point>
    Given the principal "buyer-001" owns media buy "mb-001"
    And the request <account_kind>
    When the Buyer Agent sends a get_media_buys request
    Then the response should contain "media_buys" array
    And the response <sandbox_assertion>
    # BR-RULE-209 BVA: canonical sandbox echo placements from sandbox_response_semantics.yaml

    Examples: Boundary values
      | boundary_point                                          | account_kind                            | sandbox_assertion                          |
      | sandbox: true in response (sandbox account)             | targets a sandbox account               | should include sandbox equals true         |
      | sandbox absent in response (production account)         | targets a production account            | should not include a sandbox field         |
      | sandbox: false in response (explicit production)        | targets an explicit production account  | should include sandbox equals false        |

  @T-UC-019-boundary-targeting-overlay @boundary @targeting_overlay @br-rule-294 @schema-v3.1
  Scenario Outline: targeting_overlay rehydration boundary - <boundary_point>
    Given the principal "buyer-001" owns media buy "mb-001" with package "pkg-001"
    And package "pkg-001" persisted package_config has <persisted_state>
    When the Buyer Agent sends a get_media_buys request for media_buy_ids ["mb-001"]
    Then <expected_outcome>
    # BR-RULE-294 BVA: per-package fail-soft on TypeError; clean rehydration otherwise

    Examples: Boundary values
      | boundary_point                                                                 | persisted_state                                              | expected_outcome                                                                                                                                                                                                                       |
      | targeting_overlay key absent, targeting key absent                             | no targeting_overlay and no legacy targeting                  | the package "pkg-001" targeting_overlay should be null and no error should appear in response.errors[] for "pkg-001"                                                                                                                    |
      | targeting_overlay key present and parseable                                    | targeting_overlay {geo:['US']}                                | the package "pkg-001" targeting_overlay should be a Targeting object with geo ["US"]                                                                                                                                                    |
      | targeting_overlay key absent, targeting key present                            | no targeting_overlay but legacy targeting {geo:['US']}        | the package "pkg-001" targeting_overlay should be a Targeting object with geo ["US"]                                                                                                                                                    |
      | targeting_overlay is a string (TypeError on Targeting(**str))                  | targeting_overlay set to the string 'not a dict'              | the package "pkg-001" targeting_overlay should be null and response.errors[] should include an INTERNAL_ERROR entry with message starting "TARGETING_REHYDRATION_FAILED:" and a "suggestion" field referencing "package_config"          |
      | targeting_overlay is a list (TypeError on Targeting(**list))                   | targeting_overlay set to the list ['not','a','dict']          | the package "pkg-001" targeting_overlay should be null and response.errors[] should include an INTERNAL_ERROR entry with message starting "TARGETING_REHYDRATION_FAILED:" and a "suggestion" field referencing "package_config"          |
      | two packages in same buy both raise TypeError                                  | two packages "pkg-001" and "pkg-002" both with corrupted targeting_overlay strings | both packages' targeting_overlay should be null and response.errors[] should include two INTERNAL_ERROR entries (one per package) each with a "suggestion" field referencing "package_config"                              |
      | one of N buys has one bad package                                              | one of two buys "mb-001"/"mb-002" has package "pkg-001" with corrupted targeting_overlay and the other buy is clean | the corrupted package's targeting_overlay should be null, sibling buys should render normally, and response.errors[] should include exactly one INTERNAL_ERROR entry with a "suggestion" field referencing "package_config"             |

  @T-UC-019-storyboard-post-create-status-poll @storyboard-v3.1 @v3-1 @post-create-poll
  Scenario: get_media_buys called immediately after create_media_buy resolves the freshly-created buy by media_buy_id
    Given the buyer captured a media_buy_id from a successful create_media_buy response
    When the Buyer Agent calls get_media_buys with that media_buy_id under the same account
    Then the response should be schema-valid against get-media-buys-response.json
    And the media_buys array should include the freshly-created buy
    And the included entry should expose the same media_buy_id and current status
    # media-buy/index.yaml create_buy / check_buy_status step: after the buyer
    # captures media_buy_id from create_media_buy, the buyer calls get_media_buys
    # to confirm the buy is queryable and observe its initial status. This anchors
    # the synchronous post-create poll pattern -- the buy MUST be findable by
    # media_buy_id immediately on the same account; sellers that have eventual
    # consistency without a documented retry contract fail.
    # check_buy_status: post-create get_media_buys must resolve the freshly-created buy synchronously
