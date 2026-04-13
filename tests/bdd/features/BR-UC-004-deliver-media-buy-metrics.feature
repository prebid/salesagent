# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-004 Deliver Media Buy Metrics
  As a Buyer (Human or AI Agent)
  I want to retrieve delivery performance metrics for my media buys
  So that I can monitor campaign performance and make optimization decisions

  # Postconditions verified:
  #   POST-S1: Buyer knows the delivery performance of each requested media buy
  #   POST-S2: Buyer can see package-level breakdowns
  #   POST-S3: Buyer knows the reporting period covered
  #   POST-S4: Buyer can see aggregated totals across media buys
  #   POST-S5: Buyer knows the current status of each media buy
  #   POST-S6: Buyer receives an unambiguous success confirmation
  #   POST-S7: Buyer's endpoint receives periodic delivery reports
  #   POST-S8: Buyer can verify report authenticity via HMAC signature
  #   POST-S9: Buyer knows the notification type
  #   POST-S10: Buyer knows the sequence number
  #   POST-F1: System state is unchanged on failure
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Buyer knows how to fix the issue and retry

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant has completed setup checklist
    And an authenticated Buyer with principal_id "buyer-001"
    And the principal "buyer-001" exists in the tenant database


  @T-UC-004-main @main-flow @polling
  Scenario: Polling delivery metrics for a single media buy
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response status should be "completed"
    And the response should include delivery data for "mb-001"
    And the delivery data should include impressions, spend, and clicks
    And the delivery data should include package-level breakdowns
    And the response should include the reporting period start and end dates
    And the response should include the media buy status "active"
    # POST-S1: Buyer knows delivery performance
    # POST-S2: Package-level breakdowns present
    # POST-S3: Reporting period present
    # POST-S5: Media buy status present
    # POST-S6: Unambiguous success (status=completed)

  @T-UC-004-main-multi @main-flow @polling @post-s4
  Scenario: Polling delivery metrics for multiple media buys with aggregated totals
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And a media buy "mb-002" owned by "buyer-001" with status "active"
    And the ad server adapter has delivery data for both media buys
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001", "mb-002"]
    Then the response should include delivery data for "mb-001" and "mb-002"
    And the response should include aggregated totals across both media buys
    And the aggregated impressions should equal the sum of individual impressions
    And the aggregated spend should equal the sum of individual spend
    # POST-S1: Per-media-buy delivery data
    # POST-S4: Aggregated totals across media buys

  @T-UC-004-identify-mode @invariant @BR-RULE-030 @identification
  Scenario Outline: Identification mode resolution - <mode>
    Given a media buy "mb-001" owned by "buyer-001" with buyer_ref "ref-001"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics with <request_params>
    Then the response should include delivery data for "mb-001"
    # BR-RULE-030: <invariant>

    Examples: Identification priority
      | mode | request_params | invariant |
      | media_buy_ids only | media_buy_ids=["mb-001"] | INV-1: resolves by publisher IDs |
      | buyer_refs only | buyer_refs=["ref-001"] | INV-2: resolves by buyer refs |
      | both provided | media_buy_ids=["mb-001"] buyer_refs=["ref-001"] | INV-3: media_buy_ids wins, buyer_refs ignored |

  @T-UC-004-identify-fallback @invariant @BR-RULE-030 @identification
  Scenario: Neither identifiers provided - returns all principal's media buys
    Given a media buy "mb-001" owned by "buyer-001"
    And a media buy "mb-002" owned by "buyer-001"
    And the ad server adapter has delivery data for both media buys
    When the Buyer Agent requests delivery metrics without media_buy_ids or buyer_refs
    Then the response should include delivery data for "mb-001" and "mb-002"
    # BR-RULE-030 INV-4: neither provided -> all principal's buys

  @T-UC-004-identify-partial @invariant @BR-RULE-030 @identification
  Scenario: Partial resolution - some IDs valid, some invalid
    Given a media buy "mb-001" owned by "buyer-001"
    And no media buy exists with id "mb-999"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001", "mb-999"]
    Then the response should include delivery data for "mb-001" only
    And the response should not include an error for "mb-999"
    # BR-RULE-030 INV-5: partial resolution, missing silently omitted

  @T-UC-004-identify-zero @invariant @BR-RULE-030 @identification
  Scenario: Zero resolution - all IDs invalid returns empty array
    Given no media buy exists with id "mb-999" or "mb-998"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-999", "mb-998"]
    Then the response should have an empty media_buy_deliveries array
    And the response status should be "completed"
    # BR-RULE-030 INV-6: zero resolution -> empty array, no error
    # NOTE: Tension with ext-c which says error. BR-030 (code-derived) takes precedence.

  @T-UC-004-identify-fallback-empty @invariant @BR-RULE-030 @identification
  Scenario: Neither identifiers AND no media buys for principal - empty array
    Given the principal "buyer-001" has no media buys
    When the Buyer Agent requests delivery metrics without media_buy_ids or buyer_refs
    Then the response should have an empty media_buy_deliveries array
    And the response status should be "completed"
    # BR-RULE-030 INV-4 counter-example: neither provided, no buys -> empty

  @T-UC-004-identify-batch-ownership @invariant @ownership @BR-RULE-030 @identification
  Scenario: Batch request with mixed ownership - non-owned silently omitted
    Given a media buy "mb-001" owned by "buyer-001"
    And a media buy "mb-other" owned by "other-buyer"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001", "mb-other"]
    Then the response should include delivery data for "mb-001" only
    And the response should NOT include delivery data for "mb-other"
    And no error should be returned for "mb-other"
    # PRE-BIZ3 (ownership) + BR-RULE-030 INV-5: non-owned treated as not-found, partial results

  @T-UC-004-identify-empty @invariant @BR-RULE-030 @error @boundary
  Scenario: Empty array provided - schema rejects request
    When the Buyer Agent requests delivery metrics with media_buy_ids []
    Then the operation should fail
    And the error code should be "validation_error"
    And the error message should contain "minItems"
    And the error should include "suggestion" field
    And the suggestion should contain "at least one identifier"
    # Traces to BR-RULE-030 INV-1/INV-2 (schema minItems constraint on identification arrays)
    # POST-F2: Error explains what failed
    # POST-F3: Suggestion for recovery

  @T-UC-004-identify-buyer-refs-empty @invariant @BR-RULE-030 @error @boundary
  Scenario: Empty buyer_refs array - schema rejects request
    When the Buyer Agent requests delivery metrics with buyer_refs []
    Then the operation should fail
    And the error code should be "validation_error"
    And the error message should contain "minItems"
    And the error should include "suggestion" field
    # Traces to BR-RULE-030 INV-1/INV-2 (schema minItems constraint on identification arrays)

  @T-UC-004-filter @alternative @status-filter
  Scenario Outline: Status filter - <filter_value>
    Given a media buy "mb-active" owned by "buyer-001" with status "active"
    And a media buy "mb-completed" owned by "buyer-001" with status "completed"
    And a media buy "mb-paused" owned by "buyer-001" with status "paused"
    And the ad server adapter has delivery data for all media buys
    When the Buyer Agent requests delivery metrics with status_filter "<filter_value>"
    Then the response should include only media buys with status "<filter_value>"

    Examples: Valid status values
      | filter_value |
      | pending_activation |
      | active |
      | paused |
      | completed |
      | rejected |
      | canceled |

  @T-UC-004-filter-empty @alternative @status-filter
  Scenario: Status filter - no matches returns empty success
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    When the Buyer Agent requests delivery metrics with status_filter "completed"
    Then the response should have an empty media_buy_deliveries array
    And the response status should be "completed"

  @T-UC-004-filter-invalid @alternative @status-filter @error
  Scenario: Invalid status filter value - rejected
    Given a media buy "mb-001" owned by "buyer-001"
    When the Buyer Agent requests delivery metrics with status_filter "nonexistent_status"
    Then the operation should fail
    And the error code should be "validation_error"
    And the error message should contain "status_filter"
    And the error should include "suggestion" field
    And the suggestion should contain "valid status values"
    # PRE-BIZ5: status_filter must be a valid value
    # POST-F2: Error explains invalid filter
    # POST-F3: Suggestion lists valid values

  @T-UC-004-filter-default @alternative @status-filter
  Scenario: Default status filter is "active" when not specified
    Given a media buy "mb-active" owned by "buyer-001" with status "active"
    And a media buy "mb-completed" owned by "buyer-001" with status "completed"
    When the Buyer Agent requests delivery metrics without status_filter
    Then the response should include delivery data for "mb-active" only
    # Constraint YAML: default "active"

  @T-UC-004-filter-array @alternative @status-filter
  Scenario: Status filter with array of multiple statuses
    Given a media buy "mb-active" owned by "buyer-001" with status "active"
    And a media buy "mb-paused" owned by "buyer-001" with status "paused"
    And a media buy "mb-completed" owned by "buyer-001" with status "completed"
    And the ad server adapter has delivery data for all media buys
    When the Buyer Agent requests delivery metrics with status_filter ["active", "paused"]
    Then the response should include delivery data for "mb-active" and "mb-paused"
    And the response should not include delivery data for "mb-completed"

  @T-UC-004-daterange @alternative @date-range
  Scenario: Custom date range used as reporting period
    Given a media buy "mb-001" owned by "buyer-001"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics with start_date "2026-01-01" and end_date "2026-01-31"
    Then the response reporting_period start should be "2026-01-01"
    And the response reporting_period end should be "2026-01-31"
    # POST-S3: Buyer knows the exact reporting period

  @T-UC-004-daterange-start-only @alternative @date-range
  Scenario: Only start_date provided - end defaults to current date
    Given a media buy "mb-001" owned by "buyer-001"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics with start_date "2026-01-01" and no end_date
    Then the response reporting_period end should be today's date

  @T-UC-004-daterange-end-only @alternative @date-range
  Scenario: Only end_date provided - start defaults to media buy creation date
    Given a media buy "mb-001" owned by "buyer-001" created on "2025-12-01"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics with end_date "2026-01-31" and no start_date
    Then the response reporting_period start should be "2025-12-01"
    # NOTE: Schema says creation date default, code says 30 days ago (Gap G40)

  @T-UC-004-daterange-invalid @extension @ext-e @error @invariant @BR-RULE-013 @date-range
  Scenario: Invalid date range - start after end
    Given a media buy "mb-001" owned by "buyer-001"
    When the Buyer Agent requests delivery metrics with start_date "2026-02-01" and end_date "2026-01-01"
    Then the operation should fail
    And the error code should be "invalid_date_range"
    And the error message should contain "start_date must be before end_date"
    And the error should include "suggestion" field
    And the suggestion should contain "ensure start_date is before end_date"
    # POST-F1: System state unchanged
    # POST-F2: Error explains invalid date range
    # POST-F3: Suggestion for recovery
    # BR-RULE-013 INV-3: end <= start -> rejected

  @T-UC-004-daterange-equal @extension @ext-e @error @invariant @BR-RULE-013 @date-range @boundary
  Scenario: Invalid date range - start equals end (zero-length period)
    Given a media buy "mb-001" owned by "buyer-001"
    When the Buyer Agent requests delivery metrics with start_date "2026-01-15" and end_date "2026-01-15"
    Then the operation should fail
    And the error code should be "invalid_date_range"
    And the error should include "suggestion" field
    # BR-RULE-013 INV-3: end <= start -> rejected (boundary: equal dates)

  @T-UC-004-webhook-scheduled @alternative @webhook @post-s7
  Scenario: Scheduled webhook delivery at configured frequency
    Given a media buy "mb-001" with an active reporting_webhook configured
    And the reporting_frequency is "daily"
    And the ad server adapter has delivery data for "mb-001"
    When the webhook scheduler fires for "mb-001"
    Then the system should POST a delivery report to the configured webhook URL
    And the payload should include delivery metrics for "mb-001"
    And the payload should include the reporting_period
    # POST-S7: Buyer's endpoint receives periodic delivery reports

  @T-UC-004-webhook-hmac @alternative @webhook @invariant @BR-RULE-029 @post-s8 @nfr @nfr-005
  Scenario: HMAC-SHA256 signed webhook payload
    Given a media buy "mb-001" with webhook authentication scheme "HMAC-SHA256"
    And the shared secret is a valid 32+ character string
    When the system delivers a webhook report for "mb-001"
    Then the request should include header "X-ADCP-Signature" with hex-encoded HMAC
    And the request should include header "X-ADCP-Timestamp" with ISO timestamp
    And the HMAC should be computed over "timestamp.payload" concatenation
    # POST-S8: Buyer can verify report authenticity
    # BR-RULE-029 INV-1: monotonically increasing sequence (signing is precondition)
    # Webhook auth: traces to SR-NFR-005

  @T-UC-004-webhook-bearer @alternative @webhook @invariant @BR-RULE-029
  Scenario: Bearer token webhook authentication
    Given a media buy "mb-001" with webhook authentication scheme "Bearer"
    And the bearer token is a valid 32+ character string
    When the system delivers a webhook report for "mb-001"
    Then the request should include header "Authorization" with the bearer token
    # Webhook auth: traces to SR-NFR-005

  @T-UC-004-webhook-notification-type @alternative @webhook @invariant @BR-RULE-029 @post-s9
  Scenario Outline: Webhook notification type - <type>
    Given a media buy "mb-001" with an active reporting_webhook
    When the system delivers a "<type>" webhook report for "mb-001"
    Then the payload notification_type should be "<type>"
    And the payload <next_expected> include next_expected_at
    # POST-S9: Buyer knows the notification type
    # BR-RULE-029 INV-2: final -> no next_expected_at

    Examples: Notification types and next_expected_at presence
      | type | next_expected |
      | scheduled | should |
      | final | should not |
      | delayed | should |
      | adjusted | should |

  @T-UC-004-webhook-sequence @alternative @webhook @invariant @BR-RULE-029 @post-s10
  Scenario: Webhook sequence numbers are monotonically increasing
    Given a media buy "mb-001" with an active reporting_webhook
    When the system delivers three consecutive webhook reports for "mb-001"
    Then each report should have a higher sequence_number than the previous
    And the first sequence_number should be >= 1
    # POST-S10: Buyer knows the sequence number for ordering
    # BR-RULE-029 INV-1: monotonically increasing per media buy stream

  @T-UC-004-webhook-no-aggregated @alternative @webhook
  Scenario: Webhook payload does not include aggregated totals
    Given a media buy "mb-001" with an active reporting_webhook
    When the system delivers a webhook report for "mb-001"
    Then the payload should not include "aggregated_totals" field
    # UC-004 note: aggregated totals are polling-only (not webhook)

  @T-UC-004-webhook-retry-5xx @async @extension @ext-g @webhook-reliability @invariant @BR-RULE-029 @nfr @nfr-005
  Scenario: Webhook delivery retries on 5xx response
    Given a media buy "mb-001" with an active reporting_webhook
    And the webhook endpoint returns 500 Internal Server Error
    When the system attempts to deliver a webhook report
    Then the system should retry up to 3 times
    And retries should use exponential backoff (1s, 2s, 4s + jitter)
    # BR-RULE-029 INV-3: 5xx -> retry with exponential backoff
    # POST-F2: System knows the failure mode

  @T-UC-004-webhook-retry-network @async @extension @ext-g @webhook-reliability @invariant @BR-RULE-029
  Scenario: Webhook delivery retries on network error
    Given a media buy "mb-001" with an active reporting_webhook
    And the webhook endpoint is unreachable (connection timeout)
    When the system attempts to deliver a webhook report
    Then the system should retry up to 3 times with exponential backoff
    # BR-RULE-029 INV-3: network error -> retry

  @T-UC-004-webhook-no-retry-4xx @async @extension @ext-g @webhook-reliability @invariant @BR-RULE-029
  Scenario: Webhook delivery does not retry on 4xx response
    Given a media buy "mb-001" with an active reporting_webhook
    And the webhook endpoint returns 401 Unauthorized
    When the system attempts to deliver a webhook report
    Then the system should not retry the delivery
    And the system should log the authentication rejection
    And the webhook should be marked as failed
    # BR-RULE-029 INV-4: 4xx -> no retry (client error)

  @T-UC-004-webhook-circuit-open @async @extension @ext-g @webhook-reliability @nfr @nfr-005
  Scenario: Persistent webhook failures open circuit breaker
    Given a media buy "mb-001" with an active reporting_webhook
    And the webhook endpoint has failed 5 consecutive delivery attempts
    When the system evaluates the circuit breaker state
    Then the circuit breaker should be in "OPEN" state
    And subsequent scheduled deliveries should be suppressed
    # POST-F2: System knows the webhook is persistently failing

  @T-UC-004-webhook-circuit-halfopen @async @extension @ext-g @webhook-reliability
  Scenario: Circuit breaker half-open probe attempts recovery
    Given a media buy "mb-001" with circuit breaker in "OPEN" state
    And the circuit breaker timeout (60s) has elapsed
    When the system evaluates the circuit breaker state
    Then the circuit breaker should transition to "HALF_OPEN"
    And the system should attempt a single probe delivery

  @T-UC-004-webhook-circuit-recovery @async @extension @ext-g @webhook-reliability
  Scenario: Circuit breaker closes after successful recovery probes
    Given a media buy "mb-001" with circuit breaker in "HALF_OPEN" state
    And the webhook endpoint has recovered and returns 200
    When the system delivers 2 successful probe reports
    Then the circuit breaker should transition to "CLOSED"
    And normal scheduled deliveries should resume
    # POST-F3: System has recovery path

  @T-UC-004-webhook-retry-success @async @extension @ext-g @webhook-reliability
  Scenario: Successful retry records delivery
    Given a media buy "mb-001" with an active reporting_webhook
    And the webhook endpoint fails on first attempt but succeeds on second
    When the system delivers a webhook report with retry
    Then the delivery should be recorded as successful
    And the circuit breaker state should remain healthy
    # POST-F3: System has recovery path (retry for transient)

  @T-UC-004-webhook-creds-short @invariant @BR-RULE-029 @webhook @boundary @error
  Scenario: Webhook credentials too short - rejected at configuration
    Given a media buy webhook configuration with credentials of 31 characters
    When the system validates the webhook configuration
    Then the configuration should be rejected
    And the error should indicate minimum credential length is 32 characters
    And the error should include "suggestion" field
    And the suggestion should contain "credentials must be at least 32 characters"
    # Boundary: 31 chars (min-1)
    # POST-F3: Suggestion for recovery

  @T-UC-004-webhook-creds-valid @invariant @BR-RULE-029 @webhook @boundary
  Scenario: Webhook credentials at minimum length - accepted
    Given a media buy webhook configuration with credentials of 32 characters
    When the system validates the webhook configuration
    Then the configuration should be accepted
    # Boundary: 32 chars (min)

  @T-UC-004-ext-a @extension @ext-a @error @nfr @nfr-001
  Scenario: Authentication error - missing principal
    When the Buyer Agent sends a delivery metrics request without authentication
    Then the operation should fail
    And the error code should be "principal_id_missing"
    And the error message should contain "authentication"
    And the error should include "suggestion" field
    And the suggestion should contain "provide valid credentials"
    # POST-F1: System state unchanged
    # POST-F2: Error explains authentication required
    # POST-F3: Suggestion to provide credentials

  @T-UC-004-ext-b @extension @ext-b @error
  Scenario: Principal not found in tenant database
    Given an authenticated request with principal_id "unknown-buyer"
    And no principal "unknown-buyer" exists in the tenant database
    When the Buyer Agent requests delivery metrics
    Then the operation should fail
    And the error code should be "principal_not_found"
    And the error message should contain "principal"
    And the error should include "suggestion" field
    And the suggestion should contain "verify account"
    # POST-F1: System state unchanged
    # POST-F2: Error explains principal not found
    # POST-F3: Suggestion to verify account

  @T-UC-004-ext-c @extension @ext-c @error @tension
  Scenario: Media buy not found - nonexistent identifier
    Given no media buy exists with id "mb-nonexistent"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-nonexistent"]
    Then the operation should fail
    And the error code should be "media_buy_not_found"
    And the error message should contain "media buy"
    And the error should include "suggestion" field
    And the suggestion should contain "verify the identifier"
    # POST-F1: System state unchanged
    # POST-F2: Error explains media buy not found
    # POST-F3: Suggestion to verify identifiers
    # NOTE: Tension with BR-030 INV-6 (zero resolution -> empty, no error).
    #   ext-c triggers when single ID requested and not found.
    #   BR-030 describes batch behavior. See Pass 2 gap analysis.

  @T-UC-004-ext-d @extension @ext-d @error @invariant @ownership @nfr @nfr-001
  Scenario: Ownership mismatch - returns media_buy_not_found for security
    Given a media buy "mb-other" owned by "other-buyer"
    And an authenticated Buyer with principal_id "buyer-001"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-other"]
    Then the operation should fail
    And the error code should be "media_buy_not_found"
    And the error should NOT reveal that the media buy exists
    And the error should include "suggestion" field
    And the suggestion should contain "verify the identifier"
    # POST-F1: System state unchanged
    # POST-F2: Error does not reveal existence (security)
    # POST-F3: Suggestion to verify identifier
    # PRE-BIZ3: non-owner -> rejection masked as not_found

  @T-UC-004-ext-f @extension @ext-f @error
  Scenario: Adapter error - ad server unavailable
    Given a media buy "mb-001" owned by "buyer-001"
    And the ad server adapter is unavailable
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the operation should fail
    And the error code should be "adapter_error"
    And the error message should contain "delivery data"
    And the error should include "suggestion" field
    And the suggestion should contain "retry later"
    # POST-F1: System state unchanged
    # POST-F2: Error explains adapter failure
    # POST-F3: Suggestion to retry

  @T-UC-004-adapter-partial @edge-case
  Scenario: Adapter partial failure - some media buys return data, others fail
    Given a media buy "mb-001" owned by "buyer-001"
    And a media buy "mb-002" owned by "buyer-001"
    And the ad server adapter returns data for "mb-001" but errors for "mb-002"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001", "mb-002"]
    Then the response should include delivery data for "mb-001"
    And the response should indicate "mb-002" has partial_data or delayed metrics
    # Gap analysis: adapter fails for subset of media buys -- partial data indicator

  @T-UC-004-empty-period @edge-case
  Scenario: Media buy exists but no delivery data for requested period
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the ad server adapter has no delivery data for "mb-001" in the requested period
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response should include "mb-001" with zero impressions and zero spend
    And the response status should be "completed"
    # Gap analysis: valid media buy with no data -> success with empty/zero metrics

  @T-UC-004-webhook-no-config @alternative @webhook @edge-case
  Scenario: Webhook fires for media buy without webhook configuration
    Given a media buy "mb-001" without a reporting_webhook configured
    When the webhook scheduler evaluates "mb-001"
    Then the system should skip "mb-001" (no webhook to deliver to)
    And no delivery attempt should be made
    # PRE-BIZ6: webhook URL must be configured -- not configured -> skip

  @T-UC-004-response-success @invariant @BR-RULE-018 @response
  Scenario: Success response contains delivery data without errors field
    Given a media buy "mb-001" owned by "buyer-001"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response should contain "media_buy_deliveries" field
    And the response should not contain "errors" field
    # BR-RULE-018 INV-1: success has data, no errors

  @T-UC-004-response-error @invariant @BR-RULE-018 @response @error
  Scenario: Error response contains errors array without delivery data
    When the Buyer Agent sends a delivery metrics request without authentication
    Then the response should contain "errors" field
    And the response should not contain "media_buy_deliveries" field
    And the error should include "suggestion" field
    And the suggestion should contain "provide valid authentication"
    # BR-RULE-018 INV-2: failure has errors, no data
    # POST-F3: Suggestion for recovery

  @T-UC-004-dim-supported @invariant @BR-RULE-091 @reporting-dimensions
  Scenario: Buyer requests supported dimension - seller returns breakdown
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the seller supports reporting dimension "device_type"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics for "mb-001" with reporting_dimensions {"device_type": {}}
    Then the response packages should include "by_device_type" breakdown arrays
    # BR-RULE-091 INV-1: buyer includes dimension key -> seller returns corresponding by_* array

  @T-UC-004-dim-unsupported @invariant @BR-RULE-091 @reporting-dimensions
  Scenario: Buyer requests unsupported dimension - silently omitted
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the seller does NOT support reporting dimension "audience"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics for "mb-001" with reporting_dimensions {"audience": {}}
    Then the response packages should NOT include "by_audience" breakdown arrays
    And no error should be returned
    # BR-RULE-091 INV-2: unsupported dimension silently omitted (no error, no empty array)

  @T-UC-004-dim-truncated @invariant @BR-RULE-091 @reporting-dimensions
  Scenario: Breakdown truncated by limit - truncation flag set true
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the seller supports reporting dimension "geo"
    And there are more geo breakdown entries than the requested limit
    When the Buyer Agent requests delivery metrics for "mb-001" with reporting_dimensions {"geo": {"geo_level": "country", "limit": 5}}
    Then the response packages should include "by_geo" with at most 5 entries
    And "by_geo_truncated" should be true
    # BR-RULE-091 INV-3: truncated by limit -> by_*_truncated = true

  @T-UC-004-dim-complete @invariant @BR-RULE-091 @reporting-dimensions
  Scenario: Breakdown complete (not truncated) - truncation flag set false
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the seller supports reporting dimension "device_type"
    And the device_type breakdown has fewer entries than any limit
    When the Buyer Agent requests delivery metrics for "mb-001" with reporting_dimensions {"device_type": {}}
    Then the response packages should include "by_device_type"
    And "by_device_type_truncated" should be false
    # BR-RULE-091 INV-4: complete (not truncated) -> by_*_truncated = false

  @T-UC-004-dim-geo-system @invariant @BR-RULE-091 @reporting-dimensions
  Scenario: Geo with metro level includes classification system
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the seller supports reporting dimension "geo"
    When the Buyer Agent requests delivery metrics for "mb-001" with reporting_dimensions {"geo": {"geo_level": "metro", "system": "nielsen_dma"}}
    Then the response geo breakdown should use classification system "nielsen_dma"
    # BR-RULE-091 INV-5: geo_level=metro/postal_area -> system field specifies classification

  @T-UC-004-dim-geo-postal @invariant @BR-RULE-091 @reporting-dimensions
  Scenario: Geo with postal_area level includes classification system
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the seller supports reporting dimension "geo"
    When the Buyer Agent requests delivery metrics for "mb-001" with reporting_dimensions {"geo": {"geo_level": "postal_area", "system": "us_zip"}}
    Then the response geo breakdown should use classification system "us_zip"
    # BR-RULE-091 INV-5: geo_level=postal_area -> system specifies classification

  @T-UC-004-dim-sortby-fallback @invariant @BR-RULE-091 @reporting-dimensions
  Scenario: sort_by metric not available - seller falls back to spend
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the seller supports reporting dimension "placement"
    And the seller does NOT report metric "conversions"
    When the Buyer Agent requests delivery metrics for "mb-001" with reporting_dimensions {"placement": {"sort_by": "conversions"}}
    Then the response placement breakdown should be sorted by "spend" (fallback)
    # BR-RULE-091 INV-6: sort_by metric not reported -> falls back to 'spend'

  @T-UC-004-dim-sortby-valid @invariant @BR-RULE-091 @reporting-dimensions
  Scenario: sort_by metric available - seller uses requested metric
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the seller supports reporting dimension "placement"
    And the seller reports metric "clicks"
    When the Buyer Agent requests delivery metrics for "mb-001" with reporting_dimensions {"placement": {"sort_by": "clicks"}}
    Then the response placement breakdown should be sorted by "clicks"
    # BR-RULE-091 INV-6 counter-example: sort_by metric reported -> uses requested metric

  @T-UC-004-dim-multi @invariant @BR-RULE-091 @reporting-dimensions
  Scenario: Multiple dimensions requested simultaneously
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the seller supports reporting dimensions "geo" and "device_type"
    And the seller does NOT support "audience"
    When the Buyer Agent requests delivery metrics for "mb-001" with reporting_dimensions {"geo": {"geo_level": "country"}, "device_type": {}, "audience": {}}
    Then the response packages should include "by_geo" and "by_device_type" breakdowns
    And the response packages should NOT include "by_audience"
    # BR-RULE-091 INV-1 + INV-2: supported returned, unsupported silently omitted

  @T-UC-004-attr-supported @invariant @BR-RULE-092 @attribution
  Scenario: Buyer requests custom attribution - seller applies and echoes
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the seller supports configurable attribution windows
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics for "mb-001" with attribution_window {"post_click": {"interval": 7, "unit": "days"}, "model": "last_touch"}
    Then the response should include attribution_window with model "last_touch"
    And the attribution_window should echo the applied post_click window
    # BR-RULE-092 INV-1: buyer provides -> seller applies requested lookback
    # BR-RULE-092 INV-3: response echoes applied attribution_window

  @T-UC-004-attr-unsupported @invariant @BR-RULE-092 @attribution
  Scenario: Seller ignores attribution request - returns platform default
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the seller does NOT support configurable attribution windows
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics for "mb-001" with attribution_window {"post_click": {"interval": 30, "unit": "days"}}
    Then the response should include attribution_window with the seller's platform default
    And no error should be returned
    # BR-RULE-092 INV-2: seller ignores request, returns platform default

  @T-UC-004-attr-echo @invariant @BR-RULE-092 @attribution
  Scenario: Response always echoes applied attribution window with model
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics for "mb-001"
    Then the response attribution_window should include "model" field (required)
    # BR-RULE-092 INV-3: response MUST echo attribution_window with model

  @T-UC-004-attr-omitted @invariant @BR-RULE-092 @attribution
  Scenario: Buyer omits attribution window - seller uses and echoes default
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics for "mb-001" without attribution_window
    Then the response should include attribution_window with the seller's platform default model
    # BR-RULE-092 INV-4: buyer omits -> seller uses and echoes platform default

  @T-UC-004-attr-campaign-valid @invariant @BR-RULE-092 @attribution
  Scenario: Campaign unit with interval 1 - valid
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the seller supports configurable attribution windows
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics for "mb-001" with attribution_window {"post_click": {"interval": 1, "unit": "campaign"}}
    Then the response should include attribution_window reflecting campaign-length window
    # BR-RULE-092 INV-5: unit=campaign, interval=1 -> valid (spans full campaign flight)

  @T-UC-004-attr-campaign-invalid @invariant @BR-RULE-092 @attribution @error
  Scenario: Campaign unit with interval != 1 - rejected
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    When the Buyer Agent requests delivery metrics for "mb-001" with attribution_window {"post_click": {"interval": 2, "unit": "campaign"}}
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error message should contain "interval must be 1"
    And the error should include "suggestion" field
    And the suggestion should contain "interval must be 1"
    # BR-RULE-092 INV-5 violated: unit=campaign + interval!=1 -> rejected
    # POST-F2: Error explains constraint
    # POST-F3: Suggestion for recovery

  @T-UC-004-partition-reporting-dims @partition @reporting_dimensions @BR-RULE-091
  Scenario Outline: Reporting dimensions partition - <partition>
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics with reporting_dimensions <value>
    Then the reporting_dimensions validation should result in <expected>

    Examples: Valid partitions
      | partition | value | expected |
      | omitted | (field absent) | valid |
      | empty_object | {} | valid |
      | single_dimension_defaults | {"device_type": {}} | valid |
      | multi_dimension | {"geo": {"geo_level": "country"}, "device_type": {}, "audience": {}} | valid |
      | geo_with_system | {"geo": {"geo_level": "metro", "system": "nielsen_dma", "limit": 10}} | valid |
      | custom_sort_and_limit | {"placement": {"limit": 50, "sort_by": "clicks"}} | valid |
      | all_dimensions | {"geo": {"geo_level": "country"}, "device_type": {}, "device_platform": {}, "audience": {}, "placement": {}} | valid |
      | unsupported_dimension_only | {"audience": {}} | valid |

    Examples: Invalid partitions
      | partition | value | expected |
      | geo_missing_geo_level | {"geo": {"limit": 10}} | error "INVALID_REQUEST" with suggestion |
      | geo_metro_missing_system | {"geo": {"geo_level": "metro"}} | error "INVALID_REQUEST" with suggestion |
      | limit_zero | {"geo": {"geo_level": "country", "limit": 0}} | error "INVALID_REQUEST" with suggestion |
      | limit_negative | {"device_type": {"limit": -1}} | error "INVALID_REQUEST" with suggestion |

  @T-UC-004-boundary-reporting-dims @boundary @reporting_dimensions @BR-RULE-091
  Scenario Outline: Reporting dimensions boundary - <boundary_point>
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics at reporting_dimensions boundary <value>
    Then the reporting_dimensions handling should be <expected>

    Examples: Boundary values
      | boundary_point | value | expected |
      | omitted (no reporting_dimensions field) | (field absent) | valid |
      | empty object {} | {} | valid |
      | single dimension {device_type: {}} | {"device_type": {}} | valid |
      | all 5 dimensions at once | {"geo": {"geo_level": "country"}, "device_type": {}, "device_platform": {}, "audience": {}, "placement": {}} | valid |
      | geo with geo_level=country (no system needed) | {"geo": {"geo_level": "country"}} | valid |
      | geo with geo_level=metro + system=nielsen_dma | {"geo": {"geo_level": "metro", "system": "nielsen_dma"}} | valid |
      | geo with geo_level=postal_area + system=us_zip | {"geo": {"geo_level": "postal_area", "system": "us_zip"}} | valid |
      | geo without geo_level (required field missing) | {"geo": {"limit": 10}} | invalid |
      | geo with geo_level=metro but no system (behavioral gap) | {"geo": {"geo_level": "metro"}} | invalid |
      | limit=1 (minimum boundary) | {"geo": {"geo_level": "country", "limit": 1}} | valid |
      | limit=0 (below minimum) | {"geo": {"geo_level": "country", "limit": 0}} | invalid |
      | unsupported dimension only (seller lacks capability) | {"audience": {}} | valid |
      | sort_by=unsupported_metric (seller falls back to spend) | {"placement": {"sort_by": "conversions"}} | valid |
      | limit negative | {"device_type": {"limit": -1}} | invalid |

  @T-UC-004-partition-attribution @partition @attribution_window @BR-RULE-092
  Scenario Outline: Attribution window partition - <partition>
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics with attribution_window <value>
    Then the attribution_window validation should result in <expected>

    Examples: Valid partitions
      | partition | value | expected |
      | omitted | (field absent) | valid |
      | empty_object | {} | valid |
      | post_click_only | {"post_click": {"interval": 7, "unit": "days"}} | valid |
      | post_view_only | {"post_view": {"interval": 1, "unit": "days"}} | valid |
      | both_windows | {"post_click": {"interval": 14, "unit": "days"}, "post_view": {"interval": 1, "unit": "days"}, "model": "last_touch"} | valid |
      | campaign_unit | {"post_click": {"interval": 1, "unit": "campaign"}} | valid |
      | model_only | {"model": "data_driven"} | valid |
      | seller_ignores | {"post_click": {"interval": 30, "unit": "days"}} | valid |

    Examples: Invalid partitions
      | partition | value | expected |
      | interval_zero | {"post_click": {"interval": 0, "unit": "days"}} | error "INVALID_REQUEST" with suggestion |
      | interval_negative | {"post_click": {"interval": -1, "unit": "days"}} | error "INVALID_REQUEST" with suggestion |
      | invalid_unit | {"post_click": {"interval": 1, "unit": "weeks"}} | error "INVALID_REQUEST" with suggestion |
      | invalid_model | {"model": "last_click"} | error "INVALID_REQUEST" with suggestion |
      | campaign_interval_not_one | {"post_click": {"interval": 2, "unit": "campaign"}} | error "INVALID_REQUEST" with suggestion |

  @T-UC-004-boundary-attribution @boundary @attribution_window @BR-RULE-092
  Scenario Outline: Attribution window boundary - <boundary_point>
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics at attribution_window boundary <value>
    Then the attribution_window handling should be <expected>

    Examples: Boundary values
      | boundary_point | value | expected |
      | omitted (no attribution_window field) | (field absent) | valid |
      | empty object {} | {} | valid |
      | post_click only with 7-day window | {"post_click": {"interval": 7, "unit": "days"}} | valid |
      | both windows with model=last_touch | {"post_click": {"interval": 14, "unit": "days"}, "post_view": {"interval": 1, "unit": "days"}, "model": "last_touch"} | valid |
      | model only (data_driven) | {"model": "data_driven"} | valid |
      | unit=campaign with interval=1 | {"post_click": {"interval": 1, "unit": "campaign"}} | valid |
      | unit=campaign with interval=2 (desc says must be 1) | {"post_click": {"interval": 2, "unit": "campaign"}} | invalid |
      | interval=0 (below minimum) | {"post_click": {"interval": 0, "unit": "days"}} | invalid |
      | interval=1 (minimum boundary) | {"post_click": {"interval": 1, "unit": "days"}} | valid |
      | unit=weeks (not in enum) | {"post_click": {"interval": 1, "unit": "weeks"}} | invalid |
      | model=last_click (not in enum) | {"model": "last_click"} | invalid |
      | seller ignores field (no configurable window support) | {"post_click": {"interval": 30, "unit": "days"}} | valid |

  @T-UC-004-partition-daily-breakdown @partition @include_package_daily_breakdown
  Scenario Outline: Include package daily breakdown partition - <partition>
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics with include_package_daily_breakdown <value>
    Then the daily breakdown handling should result in <expected>

    Examples: Valid partitions
      | partition | value | expected |
      | omitted | (field absent) | valid |
      | explicit_false | false | valid |
      | explicit_true | true | valid |

    Examples: Invalid partitions
      | partition | value | expected |
      | non_boolean | "yes" | error "INVALID_REQUEST" with suggestion |

  @T-UC-004-boundary-daily-breakdown @boundary @include_package_daily_breakdown
  Scenario Outline: Include package daily breakdown boundary - <boundary_point>
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics at daily breakdown boundary <value>
    Then the daily breakdown handling should be <expected>

    Examples: Boundary values
      | boundary_point | value | expected |
      | omitted (absent from request) | (field absent) | valid |
      | false (explicit) | false | valid |
      | true (explicit) | true | valid |
      | string 'true' (non-boolean type) | "true" | invalid |

  @T-UC-004-partition-account @partition @delivery_account
  Scenario Outline: Delivery account partition - <partition>
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics with account <value>
    Then the account validation should result in <expected>

    Examples: Valid partitions
      | partition | value | expected |
      | omitted | (field absent) | valid |
      | explicit_account_id | {"account_id": "acc_acme_001"} | valid |
      | natural_key | {"brand": {"domain": "acme-corp.com"}, "operator": "acme-corp.com"} | valid |

    Examples: Invalid partitions
      | partition | value | expected |
      | invalid_oneOf_both | {"account_id": "acc_001", "brand": {"domain": "x.com"}, "operator": "x.com"} | error "INVALID_REQUEST" with suggestion |
      | account_not_found | {"account_id": "acc_nonexistent"} | error "ACCOUNT_NOT_FOUND" with suggestion |
      | empty_object | {} | error "INVALID_REQUEST" with suggestion |

  @T-UC-004-boundary-account @boundary @delivery_account
  Scenario Outline: Delivery account boundary - <boundary_point>
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics at account boundary <value>
    Then the account handling should be <expected>

    Examples: Boundary values
      | boundary_point | value | expected |
      | omitted (no account field) | (field absent) | valid |
      | account_id present + account exists | {"account_id": "acc_acme_001"} | valid |
      | brand + operator present + single match | {"brand": {"domain": "acme-corp.com"}, "operator": "acme-corp.com"} | valid |
      | both account_id and brand/operator present | {"account_id": "acc_001", "brand": {"domain": "x.com"}, "operator": "x.com"} | invalid |
      | account_id present + not found | {"account_id": "acc_nonexistent"} | invalid |
      | empty object {} | {} | invalid |

  @T-UC-004-partition-status-filter @partition @status_filter
  Scenario Outline: Status filter partition - <partition>
    Given multiple media buys owned by "buyer-001" in various statuses
    When the Buyer Agent requests delivery metrics with status_filter "<partition_value>"
    Then the filter should result in <expected>

    Examples: Valid partitions
      | partition | partition_value | expected |
      | omitted | (field absent) | valid |
      | single_active | active | valid |
      | single_pending | pending_activation | valid |
      | single_paused | paused | valid |
      | single_completed | completed | valid |
      | single_rejected | rejected | valid |
      | single_canceled | canceled | valid |
      | status_array | ["active", "paused"] | valid |
      | all_statuses_array | ["pending_activation", "active", "paused", "completed", "rejected", "canceled"] | valid |

    Examples: Invalid partitions
      | partition | partition_value | expected |
      | unknown_value | failed | error "INVALID_REQUEST" with suggestion |
      | empty_array | [] | error "INVALID_REQUEST" with suggestion |

  @T-UC-004-boundary-status-filter @boundary @status_filter
  Scenario Outline: Status filter boundary - <boundary_point>
    Given multiple media buys owned by "buyer-001" in various statuses
    When the Buyer Agent requests delivery metrics at status_filter boundary "<boundary_value>"
    Then the status handling should be <expected>

    Examples: Boundary values
      | boundary_point | boundary_value | expected |
      | omitted (defaults to active) | (field absent) | valid |
      | pending_activation (first enum value) | pending_activation | valid |
      | canceled (last enum value) | canceled | valid |
      | rejected (new enum value) | rejected | valid |
      | ["active", "paused"] (multi-status array) | ["active", "paused"] | valid |
      | all 6 statuses in array | ["pending_activation", "active", "paused", "completed", "rejected", "canceled"] | valid |
      | failed (not in AdCP enum, only internal) | failed | invalid |
      | [] (empty array, violates minItems) | [] | invalid |

  @T-UC-004-partition-date-range @partition @delivery_date_range @BR-RULE-013
  Scenario Outline: Delivery date range partition - <partition>
    Given a media buy "mb-001" owned by "buyer-001"
    When the Buyer Agent requests delivery metrics with date range "<partition>"
    Then the date range validation should result in <expected>

    Examples:
      | partition          | expected |
      | start_before_end   | valid    |
      | dates_omitted      | valid    |
      | start_equals_end   | invalid  |
      | start_after_end    | invalid  |

  @T-UC-004-boundary-date-range @boundary @delivery_date_range @BR-RULE-013
  Scenario Outline: Delivery date range boundary - <boundary_point>
    Given a media buy "mb-001" owned by "buyer-001"
    When the Buyer Agent requests delivery metrics at date boundary "<boundary_point>"
    Then the date handling should be <expected>

    Examples:
      | boundary_point                       | expected |
      | start_date before end_date           | valid    |
      | dates omitted (full range)           | valid    |
      | start_date equals end_date           | invalid  |
      | start_date after end_date            | invalid  |

  @T-UC-004-partition-credentials @partition @reporting_webhook @BR-RULE-029
  Scenario Outline: Webhook credentials partition - <partition>
    Given a media buy "mb-001" with webhook delivery configured
    When the webhook is configured with credentials "<partition>"
    Then the credentials validation should result in <expected>

    Examples:
      | partition               | expected |
      | hmac_sha256             | valid    |
      | bearer_auth             | valid    |
      | credentials_at_minimum  | valid    |
      | credentials_too_short   | invalid  |
      | unknown_scheme          | invalid  |

  @T-UC-004-boundary-credentials @boundary @reporting_webhook @BR-RULE-029
  Scenario Outline: Webhook credentials boundary - <boundary_point>
    Given a media buy "mb-001" with webhook delivery configured
    When the webhook credentials are at boundary "<boundary_point>"
    Then the credentials check should be <expected>

    Examples:
      | boundary_point                              | expected |
      | HMAC-SHA256 scheme                          | valid    |
      | Bearer scheme                               | valid    |
      | credentials = 32 chars (minimum)            | valid    |
      | credentials = 31 chars (rejected)           | invalid  |
      | Unknown auth scheme not in enum             | invalid  |

  @T-UC-004-partition-resolution @partition @media_buy_resolution @BR-RULE-030
  Scenario Outline: Media buy resolution partition - <partition>
    Given media buys owned by "buyer-001"
    When the Buyer Agent requests delivery metrics with resolution "<partition>"
    Then the resolution should result in <expected>

    Examples:
      | partition            | expected |
      | media_buy_ids_only   | valid    |
      | buyer_refs_only      | valid    |
      | both_provided        | valid    |
      | neither_provided     | valid    |
      | partial_resolution   | valid    |
      | zero_resolution      | valid    |
      | empty_array          | invalid  |

  @T-UC-004-boundary-resolution @boundary @media_buy_resolution @BR-RULE-030
  Scenario Outline: Media buy resolution boundary - <boundary_point>
    Given media buys owned by "buyer-001"
    When the Buyer Agent requests delivery metrics at resolution boundary "<boundary_point>"
    Then the resolution should be <expected>

    Examples:
      | boundary_point                                | expected |
      | media_buy_ids only (primary)                  | valid    |
      | buyer_refs only (fallback)                    | valid    |
      | both provided (priority rule)                 | valid    |
      | neither provided (all buys)                   | valid    |
      | empty array (schema reject)                   | invalid  |
      | partial resolution (some missing)             | valid    |
      | zero resolution (empty result)                | valid    |

  @T-UC-004-partition-ownership @partition @ownership
  Scenario Outline: Principal ownership partition - <partition>
    Given a media buy "mb-001" with a known owner
    When the Buyer Agent requests delivery metrics with principal "<partition>"
    Then the ownership check should result in <expected>

    Examples:
      | partition       | expected |
      | owner_matches   | valid    |
      | owner_mismatch  | invalid  |

  @T-UC-004-boundary-ownership @boundary @ownership
  Scenario Outline: Principal ownership boundary - <boundary_point>
    Given a media buy "mb-001" with a known owner
    When the Buyer Agent requests delivery metrics at ownership boundary "<boundary_point>"
    Then the ownership should be <expected>

    Examples:
      | boundary_point                        | expected |
      | principal matches owner               | valid    |
      | principal differs from owner          | invalid  |

  @T-UC-004-partition-sampling @partition @sampling_method
  Scenario Outline: Sampling method partition - <partition>
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    When the Buyer Agent queries delivery artifacts with sampling method "<partition_value>"
    Then the sampling method handling should result in <expected>

    Examples: Valid partitions
      | partition | partition_value | expected |
      | random | random | valid |
      | stratified | stratified | valid |
      | recent | recent | valid |
      | failures_only | failures_only | valid |
      | not_provided | (omitted) | valid |

    Examples: Invalid partitions
      | partition | partition_value | expected |
      | unknown_value | systematic | error "INVALID_REQUEST" with suggestion |

  @T-UC-004-boundary-sampling @boundary @sampling_method
  Scenario Outline: Sampling method boundary - <boundary_point>
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    When the Buyer Agent queries delivery artifacts at sampling boundary "<boundary_value>"
    Then the sampling handling should be <expected>

    Examples: Boundary values
      | boundary_point | boundary_value | expected |
      | random (first enum value) | random | valid |
      | failures_only (last enum value) | failures_only | valid |
      | Not provided (server default) | (omitted) | valid |
      | Unknown string not in enum | systematic | invalid |

  @T-UC-004-sandbox-happy @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account receives simulated delivery metrics with sandbox flag
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the request targets a sandbox account
    When the Buyer Agent queries delivery metrics for media buy "mb-001"
    Then the response status should be "completed"
    And the response should include sandbox equals true
    And no real ad platform API calls should have been made
    And no real billing records should have been created
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-2: real ad platform calls suppressed
    # BR-RULE-209 INV-3: real billing suppressed
    # BR-RULE-209 INV-4: response includes sandbox: true

  @T-UC-004-sandbox-production @invariant @br-rule-209 @sandbox
  Scenario: Production account delivery metrics response does not include sandbox flag
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the request targets a production account
    When the Buyer Agent queries delivery metrics for media buy "mb-001"
    Then the response status should be "completed"
    And the response should not include a sandbox field
    # BR-RULE-209 INV-5: production account -> sandbox absent

  @T-UC-004-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account with invalid media buy ID returns real validation error
    Given the request targets a sandbox account
    When the Buyer Agent queries delivery metrics for a non-existent media buy
    Then the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

