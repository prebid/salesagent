# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-019 Query Media Buys
  As a Buyer (Human or AI Agent)
  I want to query the current state of my media buys
  So that I can monitor campaign status, check creative approvals, and assess delivery pacing

  # Postconditions verified:
  #   POST-S1: Buyer knows the current status of each matching media buy (pending_activation, active, completed)
  #   POST-S2: Buyer knows the package-level details for each media buy (budget, bid_price, product, flight dates, paused state)
  #   POST-S3: Buyer knows the creative approval state for each package (pending_review, approved, rejected with reason)
  #   POST-S4: Buyer knows the near-real-time delivery metrics per package when snapshots were requested and available
  #   POST-S5: Buyer knows why a snapshot is unavailable when requested but not returned
  #   POST-S6: Buyer can correlate results to their own references via buyer_ref and buyer_campaign_ref
  #   POST-F1: Buyer knows the operation failed
  #   POST-F2: Buyer knows what went wrong (error explains the failure)
  #   POST-F3: Buyer knows how to recover (error includes recovery classification)

  Background:
    Given a Seller Agent is operational and accepting requests
    And an authenticated Buyer with principal_id "buyer-001"
    And the principal "buyer-001" exists in the tenant database


  @T-UC-019-main-rest @main-flow @rest
  Scenario: Query media buys via REST with default filters
    Given the principal "buyer-001" owns media buy "mb-001" with start_date "2026-03-01" and end_date "2026-03-31"
    And today is "2026-03-15"
    When the Buyer Agent sends a get_media_buys request via A2A with no filters
    Then the response should include media buy "mb-001" with status "active"
    And each media buy should include package-level details with budget, bid_price, product_id, flight dates, and paused state
    And each package should include creative approval state when creatives are assigned
    And each media buy should include buyer_ref and buyer_campaign_ref for correlation
    # POST-S1: Status computed from flight dates
    # POST-S2: Package-level details present
    # POST-S3: Creative approval state present
    # POST-S6: Buyer refs present for correlation

  @T-UC-019-main-mcp @main-flow @mcp
  Scenario: Query media buys via MCP with default filters
    Given the principal "buyer-001" owns media buy "mb-001" with start_date "2026-03-01" and end_date "2026-03-31"
    And today is "2026-03-15"
    When the Buyer Agent invokes the get_media_buys MCP tool with no filters
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

  @T-UC-019-main-filter-refs @main-flow @filtering
  Scenario: Query media buys by buyer_refs
    Given the principal "buyer-001" owns media buy "mb-001" with buyer_ref "campaign-alpha"
    And the principal "buyer-001" owns media buy "mb-002" with buyer_ref "campaign-beta"
    When the Buyer Agent sends a get_media_buys request with buyer_refs ["campaign-alpha"]
    Then the response should include media buy "mb-001"
    And the response should not include media buy "mb-002"
    # POST-S6: buyer_ref used for filtering and correlation

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
      | partition          | today      | start      | end        | expected_status      |
      | pre_flight         | 2026-03-01 | 2026-03-15 | 2026-03-31 | pending_activation   |
      | in_flight          | 2026-03-15 | 2026-03-01 | 2026-03-31 | active               |
      | post_flight        | 2026-04-01 | 2026-03-01 | 2026-03-31 | completed            |
      | single_day_flight  | 2026-03-15 | 2026-03-15 | 2026-03-15 | active               |

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
      | boundary_point                                            | today      | start      | end        | expected_status    |
      | day before start_date                                     | 2026-03-14 | 2026-03-15 | 2026-03-31 | pending_activation |
      | start_date itself                                         | 2026-03-15 | 2026-03-15 | 2026-03-31 | active             |
      | end_date itself                                           | 2026-03-31 | 2026-03-15 | 2026-03-31 | active             |
      | day after end_date                                        | 2026-04-01 | 2026-03-15 | 2026-03-31 | completed          |
      | start_date equals end_date and today equals that date     | 2026-03-15 | 2026-03-15 | 2026-03-15 | active             |
      | start_date equals end_date and today is day before        | 2026-03-14 | 2026-03-15 | 2026-03-15 | pending_activation |
      | start_date equals end_date and today is day after         | 2026-03-16 | 2026-03-15 | 2026-03-15 | completed          |

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
    When the Buyer Agent sends a get_media_buys request with <filter_config>
    Then <expected_behavior>
    # BR-RULE-151: Status filter defaults and validation

    Examples: Valid partitions
      | partition          | filter_config                                             | expected_behavior                                                |
      | null_default       | no status_filter                                          | only media buys with status "active" are returned                |
      | single_status      | status_filter "completed"                                 | only media buys with status "completed" are returned             |
      | multiple_statuses  | status_filter ["active", "pending_activation"]            | media buys with either status are returned                       |
      | all_statuses       | all six status values in status_filter                    | media buys in any status are returned                            |

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
    When the Buyer Agent sends a get_media_buys request with <filter_config>
    Then <expected_behavior>
    # BR-RULE-151: Boundary test for status filter

    Examples: Boundary values
      | boundary_point                              | filter_config                                          | expected_behavior                                               |
      | status_filter omitted (null)                | no status_filter                                       | only media buys with status "active" are returned               |
      | single valid enum value                     | status_filter "active"                                 | only media buys with status "active" are returned               |
      | array with one valid value                  | status_filter ["completed"]                            | only media buys with status "completed" are returned            |
      | array with all six enum values              | all six status values in status_filter                 | media buys in any status are returned                           |
      | empty array                                 | status_filter as empty array []                        | error "STATUS_FILTER_EMPTY" with suggestion                     |
      | unknown enum value as string                | status_filter "expired"                                | error "STATUS_FILTER_INVALID_VALUE" with suggestion             |
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

  @T-UC-019-partition-principal-invalid @partition @principal_id @error
  Scenario Outline: Principal scoping failures - <partition>
    Given <principal_setup>
    When the Buyer Agent sends a get_media_buys request
    Then <expected_outcome>
    And the error should include a "suggestion" field
    And the suggestion should contain "<suggestion_fragment>"
    # BR-RULE-154: Principal resolution failures

    Examples: Invalid partitions
      | partition              | principal_setup                                       | expected_outcome                                                                 | suggestion_fragment       |
      | missing_principal_id   | an authenticated identity with no principal_id        | the response should include an empty media_buys array with error "principal_id_missing" | re-authenticate           |
      | principal_not_found    | an authenticated principal "buyer-unknown" not in registry | the response should include an empty media_buys array with error "principal_not_found"  | register                  |
      | identity_missing       | no authentication context                             | the operation should fail with error code "AUTH_REQUIRED"                         | authentication            |

  @T-UC-019-boundary-principal @boundary @principal_id
  Scenario Outline: Principal scoping boundary - <boundary_point>
    Given <principal_setup>
    When the Buyer Agent sends a get_media_buys request
    Then <expected_outcome>
    # BR-RULE-154: Boundary test for principal resolution

    Examples: Boundary values
      | boundary_point                          | principal_setup                                                       | expected_outcome                                                                 |
      | valid principal with multiple media buys | an authenticated principal "buyer-001" who owns 5 media buys         | the response should include 5 media buys scoped to buyer-001                     |
      | valid principal with zero media buys    | an authenticated principal "buyer-002" who owns no media buys         | the response should include an empty media_buys array                            |
      | principal_id is null                    | an authenticated identity with principal_id null                      | empty media_buys with error "principal_id_missing"                               |
      | principal_id is empty string            | an authenticated identity with principal_id ""                        | empty media_buys with error "principal_id_missing"                               |
      | principal_id not in registry            | an authenticated principal "buyer-ghost" not in registry              | empty media_buys with error "principal_not_found"                                |
      | identity not resolved (no auth)         | no authentication context                                             | error "AUTH_REQUIRED" with suggestion                                            |

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
  Scenario: INV-1 holds - pre-flight media buy has pending_activation status
    Given the principal "buyer-001" owns media buy "mb-001" with start_date "2026-04-01" and end_date "2026-04-30"
    And today is "2026-03-15"
    When the Buyer Agent sends a get_media_buys request with status_filter "pending_activation"
    Then the response should include media buy "mb-001" with status "pending_activation"
    # BR-RULE-150 INV-1: today < start_date yields pending_activation

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
    And the request targets a sandbox account
    When the Buyer Agent sends a get_media_buys request with invalid status filter
    Then the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

