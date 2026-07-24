# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

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
    Given a media buy "mb-001" owned by "buyer-001"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics with <request_params>
    Then the response should include delivery data for "mb-001"
    # BR-RULE-030: <invariant>

    Examples: Identification priority
      | mode | request_params | invariant |
      | media_buy_ids only | media_buy_ids=["mb-001"] | INV-1: resolves by publisher IDs |

  @T-UC-004-identify-fallback @invariant @BR-RULE-030 @identification
  Scenario: Neither identifiers provided - returns all principal's media buys
    Given a media buy "mb-001" owned by "buyer-001"
    And a media buy "mb-002" owned by "buyer-001"
    And the ad server adapter has delivery data for both media buys
    When the Buyer Agent requests delivery metrics without media_buy_ids
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
    When the Buyer Agent requests delivery metrics without media_buy_ids
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

  @T-UC-004-filter @alternative @status-filter
  Scenario Outline: Status filter - <filter_value>
    # Seed one buy per canonical status (active, completed, paused, rejected,
    # canceled, pending_creatives, pending_start) so EVERY Examples row has a
    # matching buy to return. Seeding only active/completed/paused made the
    # pending_*/rejected/canceled rows return empty, so the Then's loop never ran
    # and the row passed vacuously (#1545 review).
    Given multiple media buys owned by "buyer-001" in various statuses
    And the ad server adapter has delivery data for all media buys
    When the Buyer Agent requests delivery metrics with status_filter "<filter_value>"
    Then the response should include only media buys with status "<filter_value>"

    Examples: Valid status values
      | filter_value |
      | pending_creatives |
      | pending_start |
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

  # HAND-EDITED: salesagent-local scenario (not in adcp-req). The @hand-edited
  # marker makes compile_bdd.py --merge classify this LEGACY-PRESERVE, and its
  # bdd-traceability.yaml entry survives the rederive prune (id kept in
  # all_scenario_ids). Added for the #1570 review (grade the real scheduler path).
  # NOTE: this comment MUST stay ABOVE the tag line — a comment between the tag
  # line and Scenario: makes the compiler parse tags=[]/id=None -> LEGACY-DELETE.
  @T-UC-004-webhook-scheduler-derivation @alternative @polling @invariant @BR-RULE-029 @hand-edited
  Scenario Outline: Delivery webhook scheduler derives <type> from the buy's real delivery status
    # Drives the REAL DeliveryWebhookScheduler — not the WebhookDeliveryService
    # is_final/is_adjusted flags: notification_type comes from
    # derive_notification_type() over the buy's resolved delivery status, and
    # sequence_number from the success-only WebhookDeliveryLog counter (#1570).
    # NOT tagged @webhook so the polling harness (DeliveryPollEnv) is active — it
    # exposes send_delivery_webhook / set_adapter_response.
    Given a media buy "mb-100" with a reporting_webhook and a "<flight>" flight
    When the delivery webhook scheduler sends a report for "mb-100"
    Then the scheduler webhook payload notification_type should be "<type>"
    And the scheduler webhook payload sequence_number should be 1
    And the scheduler webhook payload <next_expected> include next_expected_at
    And the scheduler webhook payload should omit unavailable_count while partial_data is false

    # A completed (flight-ended) buy resolves to "completed" -> derive_notification_type
    # returns "final", which must OMIT next_expected_at; an in-flight buy -> "scheduled".
    Examples: notification_type derived from the buy's resolved delivery status
      | flight    | type      | next_expected |
      | live      | scheduled | should        |
      | completed | final     | should not    |

  # HAND-EDITED: salesagent-local scenario (not in adcp-req). See the
  # @hand-edited note on the scheduler-derivation scenario above (comment stays
  # ABOVE the tag line so the compiler classifies it LEGACY-PRESERVE).
  @T-UC-004-poll-omits-webhook-fields @main-flow @polling @v3-1 @invariant @BR-RULE-029 @hand-edited
  Scenario: Synchronous poll omits the webhook-only fields
    # #1570: the webhook-only fields (notification_type, sequence_number,
    # next_expected_at, partial_data, unavailable_count) are "only present in
    # webhook deliveries" — the synchronous poll must omit them all, on every
    # transport. The step enforces the full WEBHOOK_ONLY_FIELDS set.
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response omits the webhook-only fields

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
    # Boundary: 31 chars (min-1)
    # Production rejects the short credential at the create_media_buy Pydantic
    # boundary (Authentication.credentials MinLen=32) with VALIDATION_ERROR; the
    # 32-char minimum is carried in the error MESSAGE. The RequestValidationError
    # envelope emits no suggestion for this path.

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
    # HAND-EDITED (salesagent-rlgl.2): Then asserts the buyer-facing WIRE envelope
    # via the existing wire-assertion path (attribution_window in _WIRE_ASSERTED_FIELDS
    # -> _assert_error_outcome -> assert_envelope_shape on ctx["wire_error_envelope"]),
    # not the lossy reconstructed ctx["error"] generic then_error.py steps.
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    When the Buyer Agent requests delivery metrics for "mb-001" with attribution_window {"post_click": {"interval": 2, "unit": "campaign"}}
    Then the attribution_window validation should result in error "VALIDATION_ERROR" with suggestion
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

  @T-UC-004-partition-attribution @partition @attribution_window @BR-RULE-092 @schema-v3.1
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
      | interval_zero | {"post_click": {"interval": 0, "unit": "days"}} | error "VALIDATION_ERROR" with suggestion |
      | interval_negative | {"post_click": {"interval": -1, "unit": "days"}} | error "VALIDATION_ERROR" with suggestion |
      | invalid_unit | {"post_click": {"interval": 1, "unit": "weeks"}} | error "VALIDATION_ERROR" with suggestion |
      | invalid_model | {"model": "last_click"} | error "VALIDATION_ERROR" with suggestion |
      | campaign_interval_not_one | {"post_click": {"interval": 2, "unit": "campaign"}} | error "VALIDATION_ERROR" with suggestion |

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
      | unit=campaign with interval=2 (desc says must be 1) | {"post_click": {"interval": 2, "unit": "campaign"}} | error "VALIDATION_ERROR" |
      | interval=0 (below minimum) | {"post_click": {"interval": 0, "unit": "days"}} | error "VALIDATION_ERROR" |
      | interval=1 (minimum boundary) | {"post_click": {"interval": 1, "unit": "days"}} | valid |
      | unit=weeks (not in enum) | {"post_click": {"interval": 1, "unit": "weeks"}} | error "VALIDATION_ERROR" |
      | model=last_click (not in enum) | {"model": "last_click"} | error "VALIDATION_ERROR" |
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
      | brand + operator + sandbox:true present + sandbox account exists | {"brand": {"domain": "acme-corp.com"}, "operator": "acme-corp.com", "sandbox": true} | valid |

  @T-UC-004-partition-status-filter @partition @status_filter
  Scenario Outline: Status filter partition - <partition>
    Given multiple media buys owned by "buyer-001" in various statuses
    When the Buyer Agent requests delivery metrics with status_filter "<partition_value>"
    Then the filter should result in <expected>

    Examples: Valid partitions
      | partition | partition_value | expected |
      | omitted | (field absent) | valid |
      | single_active | active | valid |
      | single_pending_creatives | pending_creatives | valid |
      | single_pending_start | pending_start | valid |
      | single_paused | paused | valid |
      | single_completed | completed | valid |
      | single_rejected | rejected | valid |
      | single_canceled | canceled | valid |
      | status_array | ["active", "paused"] | valid |
      | all_statuses_array | ["pending_creatives", "pending_start", "active", "paused", "completed", "rejected", "canceled"] | valid |

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
      | pending_creatives (first enum value) | pending_creatives | valid |
      | canceled (last enum value) | canceled | valid |
      | rejected (new enum value) | rejected | valid |
      | ["active", "paused"] (multi-status array) | ["active", "paused"] | valid |
      | all 7 statuses in array | ["pending_creatives", "pending_start", "active", "paused", "completed", "rejected", "canceled"] | valid |
      | failed (not in AdCP enum, only internal) | failed | invalid |
      | [] (empty array, violates minItems) | [] | invalid |

  @T-UC-004-partition-date-range @partition @delivery_date_range @BR-RULE-013 @schema-v3.1
  Scenario Outline: Delivery date range partition - <partition>
    Given a media buy "mb-001" owned by "buyer-001"
    When the Buyer Agent requests delivery metrics with date range "<partition>"
    Then the date range validation should result in <expected>

    Examples:
      | partition          | expected                                    |
      | start_before_end   | valid                                       |
      | dates_omitted      | valid                                       |
      | start_equals_end   | error "VALIDATION_ERROR" with suggestion    |
      | start_after_end    | error "VALIDATION_ERROR" with suggestion    |

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

  @T-UC-004-partition-resolution @partition @media_buy_resolution @BR-RULE-030 @schema-v3.1
  Scenario Outline: Media buy resolution partition - <partition>
    Given media buys owned by "buyer-001"
    When the Buyer Agent requests delivery metrics with resolution "<partition>"
    Then the resolution should result in <expected>

    Examples:
      | partition            | expected                                 |
      | media_buy_ids_only   | valid                                    |
      | both_provided        | valid                                    |
      | neither_provided     | valid                                    |
      | partial_resolution   | valid                                    |
      | zero_resolution      | valid                                    |
      | empty_array          | error "VALIDATION_ERROR" with suggestion |

  @T-UC-004-boundary-resolution @boundary @media_buy_resolution @BR-RULE-030
  Scenario Outline: Media buy resolution boundary - <boundary_point>
    Given media buys owned by "buyer-001"
    When the Buyer Agent requests delivery metrics at resolution boundary "<boundary_point>"
    Then the resolution should be <expected>

    Examples:
      | boundary_point                                | expected |
      | media_buy_ids only (primary)                  | valid    |
      | media_buy_ids provided                        | valid    |
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

  @T-UC-004-webhook-window-update @alternative @webhook @invariant @BR-RULE-029 @BR-RULE-221 @measurement-window @v3-1 @post-s9
  Scenario: Webhook notification_type window_update supersedes a prior measurement window
    Given a media buy "mb-001" with an active reporting_webhook
    And a prior webhook report for "mb-001" used measurement_window "live"
    When the system delivers a "window_update" webhook report for "mb-001"
    Then the payload notification_type should be "window_update"
    And the payload supersedes_window should be "live"
    And the payload measurement_window should be "c3"
    And buyers should replace stored data for the superseded window
    # POST-S9: Buyer knows the notification type (window_update added in v3.1)
    # v3.1: supersedes_window enables broadcast c3/c7 progression
    # BR-RULE-221 INV-6: window_update + supersedes_window -> buyer REPLACES superseded window's data
    # BR-RULE-221 INV-4: measurement_window references a declared window_id
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-webhook-partial-data @alternative @webhook @v3-1 @invariant @BR-RULE-222 @partial-data
  Scenario: Webhook payload signals partial_data with unavailable_count when adapter data is delayed
    Given a webhook batch covering media buys "mb-001" and "mb-002"
    And the ad server adapter has delivery data for "mb-001"
    And the ad server adapter returns reporting_delayed for "mb-002"
    When the system delivers a "scheduled" webhook report for that batch
    Then the payload partial_data should be true
    And the payload unavailable_count should equal 1
    And the entry for "mb-002" should have status "reporting_delayed"
    And the entry for "mb-002" should include expected_availability timestamp
    # v3.1: partial_data + unavailable_count + reporting_delayed + expected_availability
    # BR-RULE-222 INV-1: webhook contains a reporting_delayed/failed buy -> partial_data true
    # BR-RULE-222 INV-2/INV-3: partial_data true -> unavailable_count present and equal to delayed/failed count
    # BR-RULE-222 INV-5: temporarily unavailable data -> status reporting_delayed
    # BR-RULE-222 INV-6: reporting_delayed + known availability -> expected_availability present
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-webhook-adjusted-resend @alternative @webhook @v3-1 @invariant @BR-RULE-221 @measurement-window
  Scenario: Webhook notification_type adjusted resends a prior period with is_adjusted true
    Given a media buy "mb-001" with an active reporting_webhook
    And a prior webhook report for "mb-001" covered reporting_period "2026-05-10" to "2026-05-10"
    When the system delivers an "adjusted" webhook report for "mb-001" covering the same period with corrected totals
    Then the payload notification_type should be "adjusted"
    And the entry for "mb-001" should include is_adjusted equals true
    And buyers should replace previous period data with the resent totals
    # v3.1: is_adjusted disambiguates resends from forward-only scheduled reports
    # BR-RULE-221 INV-8: adjusted / is_adjusted -> same-window corrected resend, DISTINCT from window_update supersession

  @T-UC-004-package-is-final-true @main-flow @polling @v3-1 @invariant @BR-RULE-221 @measurement-window
  Scenario: Polling response marks a closed broadcast package is_final true
    Given a media buy "mb-001" owned by "buyer-001" with status "completed"
    And package "pkg-1" delivers under measurement_window "c7"
    And the seller considers the c7 data closed
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response should include delivery data for "mb-001"
    And the response packages should include is_final equals true for "pkg-1"
    And the response packages should include measurement_window "c7" for "pkg-1"
    # v3.1: is_final + measurement_window declare provisional vs closed data
    # BR-RULE-221 INV-2: is_final true -> seller considers data closed, no further updates expected
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-package-is-final-false @main-flow @polling @v3-1 @invariant @BR-RULE-221 @measurement-window
  Scenario: Polling response marks a live broadcast package is_final false with measurement_window live
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And package "pkg-1" delivers under measurement_window "live"
    And the seller expects the c3 window to supersede the live data later
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response should include delivery data for "mb-001"
    And the response packages should include is_final equals false for "pkg-1"
    And the response packages should include measurement_window "live" for "pkg-1"
    # v3.1: live window is provisional; c3/c7 will arrive via window_update
    # BR-RULE-221 INV-1: is_final false -> data provisional, a later report may supersede it

  @T-UC-004-metric-aggregates-viewable-rate-by-standard @main-flow @polling @v3-1 @invariant @BR-RULE-220 @metric-aggregates
  Scenario: aggregated_totals.metric_aggregates emits separate rows per viewability_standard
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And package "pkg-1" reports viewable_rate under viewability_standard "mrc"
    And package "pkg-1" also reports viewable_rate under viewability_standard "groupm"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response aggregated_totals should include a metric_aggregates row with scope "standard" and metric_id "viewable_rate" and qualifier viewability_standard "mrc"
    And the response aggregated_totals should include a metric_aggregates row with scope "standard" and metric_id "viewable_rate" and qualifier viewability_standard "groupm"
    And each viewable_rate row should include measurable_impressions and viewable_impressions
    And no top-level "viewable_rate" scalar should be present in aggregated_totals
    # v3.1: metric_aggregates mutual exclusion MUST with top-level scalars
    # BR-RULE-220 INV-1: qualified metric in metric_aggregates -> top-level scalar omitted
    # BR-RULE-220 INV-5: viewable_rate row carries measurable_impressions + viewable_impressions
    # BR-RULE-220 INV-11: same metric_id, different qualifier -> separate rows, never summed
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-metric-aggregates-vendor-scope @main-flow @polling @v3-1 @invariant @BR-RULE-220 @metric-aggregates
  Scenario: aggregated_totals.metric_aggregates supports vendor scope anchored on BrandRef
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the seller reports vendor metric "attention_units" from vendor domain "attentionvendor.example"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response aggregated_totals should include a metric_aggregates row with scope "vendor"
    And the vendor row should reference vendor domain "attentionvendor.example"
    And the vendor row should include metric_id "attention_units"
    And the vendor row should include measurable_impressions as coverage denominator
    # v3.1: vendor scope uses (vendor BrandRef, metric_id) tuple
    # BR-RULE-220 INV-4: scope=vendor -> requires [scope, vendor, metric_id, value], keyed by (vendor, metric_id)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-metric-aggregates-completion-source @main-flow @polling @v3-1 @invariant @BR-RULE-220 @metric-aggregates
  Scenario: aggregated_totals.metric_aggregates partitions completion_rate by completion_source qualifier
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And package "pkg-1" reports completion_rate under completion_source "seller_attested"
    And package "pkg-1" also reports completion_rate under completion_source "vendor_attested"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response aggregated_totals should include a metric_aggregates row with metric_id "completion_rate" and qualifier completion_source "seller_attested"
    And the response aggregated_totals should include a metric_aggregates row with metric_id "completion_rate" and qualifier completion_source "vendor_attested"
    And each completion_rate row should include impressions and completed_views
    # v3.1: completion_source qualifier prevents cross-source summation
    # BR-RULE-220 INV-6: completion_rate row carries impressions + completed_views
    # BR-RULE-220 INV-11: same metric_id, different qualifier -> separate rows, never summed

  @T-UC-004-missing-metrics-flagged @main-flow @polling @v3-1 @invariant @BR-RULE-223 @missing-metrics @accountability
  Scenario: by_package.missing_metrics flags a committed metric the seller did not populate
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And package "pkg-1" committed to deliver metric "completed_views"
    And the ad server adapter did not return completed_views for the reporting period
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response packages should include a missing_metrics entry for "pkg-1"
    And the missing_metrics entry should have scope "standard" and metric_id "completed_views"
    # v3.1: missing_metrics surfaces accountability breaches symmetric with committed_metrics
    # BR-RULE-223 INV-1: contract metric not populated (and measurable) -> appears in missing_metrics
    # BR-RULE-223 INV-5: standard scope entry carries scope + metric_id (from available-metric enum)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-missing-metrics-clean @main-flow @polling @v3-1 @invariant @BR-RULE-223 @missing-metrics @accountability
  Scenario: by_package.missing_metrics is empty or absent when all committed metrics were delivered
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And package "pkg-1" committed to deliver metric "completed_views"
    And the ad server adapter returned completed_views for the reporting period
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response packages should have an empty or absent missing_metrics array for "pkg-1"
    # v3.1: empty / absent missing_metrics indicates clean delivery against contract
    # BR-RULE-223 INV-9: every committed metric due populated -> missing_metrics empty/absent (clean delivery)

  @T-UC-004-package-commercial-fields @main-flow @polling @v3-1
  Scenario: Polling response includes per-package pricing_model, rate, currency, and effective_rate
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And package "pkg-1" uses pricing_model "cpm" with rate 12.50 and currency "USD"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response packages should include pricing_model "cpm" for "pkg-1"
    And the response packages should include rate 12.50 for "pkg-1"
    And the response packages should include currency "USD" for "pkg-1"
    And the response packages should include effective_rate for "pkg-1"
    # v3.1: per-package pricing_model, rate, currency, effective_rate are required
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-package-pacing-index @main-flow @polling @v3-1
  Scenario Outline: Polling response includes per-package pacing_index reflecting delivery pace
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And package "pkg-1" is <pace_state> with pacing_index <pacing_index>
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response packages should include pacing_index <pacing_index> for "pkg-1"
    # v3.1: pacing_index 1.0 = on-track, <1 behind, >1 ahead
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

    Examples: pacing_index states
      | pace_state | pacing_index |
      | on track   | 1.00         |
      | behind     | 0.82         |
      | ahead      | 1.18         |

  @T-UC-004-package-delivery-status @main-flow @polling @v3-1
  Scenario Outline: Polling response reports per-package delivery_status enum
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And package "pkg-1" has delivery_status "<delivery_status>"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response packages should include delivery_status "<delivery_status>" for "pkg-1"
    # v3.1: delivery_status is operational state independent of buyer pause

    Examples: delivery_status enum
      | delivery_status   |
      | delivering        |
      | completed         |
      | budget_exhausted  |
      | flight_ended      |
      | goal_met          |

  @T-UC-004-aggregated-roas-and-cpa @main-flow @polling @v3-1 @invariant @BR-RULE-220 @metric-aggregates
  Scenario: aggregated_totals scalar fields include roas and cost_per_acquisition
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And a media buy "mb-002" owned by "buyer-001" with status "active"
    And the ad server adapter has delivery data for both media buys
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001", "mb-002"]
    Then the response should include aggregated totals across both media buys
    And the aggregated_totals should include "roas" as total conversion_value over total spend
    And the aggregated_totals should include "cost_per_acquisition" as total spend over total conversions
    And the aggregated_totals should include "media_buy_count" equal to 2
    # v3.1: aggregated_totals scalars expanded beyond impressions/spend/clicks
    # BR-RULE-220 INV-2: unqualified metrics (roas, cost_per_acquisition, media_buy_count) -> top-level scalars, not metric_aggregates rows
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-aggregated-reach-homogeneous @main-flow @polling @v3-1 @invariant @BR-RULE-224 @reach
  Scenario: aggregated_totals.reach is present when all buys share the same reach_unit
    Given a media buy "mb-001" owned by "buyer-001" with status "active" and reach_unit "individuals"
    And a media buy "mb-002" owned by "buyer-001" with status "active" and reach_unit "individuals"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001", "mb-002"]
    Then the aggregated_totals should include "reach" deduplicated across both media buys
    And the aggregated_totals should include "reach_unit" equal to "individuals"
    And the aggregated_totals should include "frequency" as impressions over reach
    # v3.1: aggregate reach only when reach_unit is homogeneous
    # BR-RULE-224 INV-1/INV-3: reach present -> homogeneous reach_unit; reach_unit names the single shared unit
    # BR-RULE-224 INV-4: seller can dedup -> reach is deduplicated across buys
    # BR-RULE-224 INV-6: frequency present -> reach present
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-aggregated-reach-heterogeneous-omitted @main-flow @polling @v3-1 @invariant @BR-RULE-224 @reach
  Scenario: aggregated_totals.reach is omitted when reach_unit is heterogeneous across buys
    Given a media buy "mb-001" owned by "buyer-001" with status "active" and reach_unit "individuals"
    And a media buy "mb-002" owned by "buyer-001" with status "active" and reach_unit "households"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001", "mb-002"]
    Then the aggregated_totals should not include "reach" field
    And the aggregated_totals should not include "reach_unit" field
    And buyers should use per-media-buy reach values instead
    # v3.1: heterogeneous reach_unit -> aggregate reach omitted (not zeroed)
    # BR-RULE-224 INV-2: heterogeneous reach units -> reach AND reach_unit omitted, buyer uses per-buy reach

  @T-UC-004-status-reporting-delayed @alternative @webhook @v3-1 @invariant @BR-RULE-222 @partial-data
  Scenario: Webhook entry for media buy with delayed adapter data uses status reporting_delayed
    Given a media buy "mb-001" with an active reporting_webhook
    And the ad server adapter cannot return data for "mb-001" yet
    When the system delivers a "delayed" webhook report for "mb-001"
    Then the entry for "mb-001" should have status "reporting_delayed"
    And the entry for "mb-001" should include expected_availability timestamp
    # v3.1: reporting_delayed is webhook-context status for missing/delayed data
    # BR-RULE-222 INV-5: temporarily unavailable data -> status reporting_delayed
    # BR-RULE-222 INV-6: reporting_delayed + known availability -> expected_availability present
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-status-pending-legacy-alias @main-flow @polling @v3-1
  Scenario: Polling response accepts legacy status "pending" as alias for "pending_start"
    Given a media buy "mb-001" owned by "buyer-001" with status "pending_start"
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-001"]
    Then the response should include the media buy status "pending_start"
    And buyers MAY treat the legacy alias "pending" as equivalent to "pending_start"
    # v3.1: pending_start replaces pending; pending retained as legacy alias

  @T-UC-004-package-daily-breakdown-on @main-flow @polling @v3-1
  Scenario: include_package_daily_breakdown true returns per-package daily_breakdown arrays
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics for "mb-001" with include_package_daily_breakdown true
    Then the response packages should include "daily_breakdown" arrays for each package
    And each daily_breakdown entry should include date, impressions, and spend
    # v3.1: include_package_daily_breakdown=true gates per-package daily arrays
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-package-daily-breakdown-off @main-flow @polling @v3-1
  Scenario: include_package_daily_breakdown omitted defaults to false and packages have no daily arrays
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the ad server adapter has delivery data for "mb-001"
    When the Buyer Agent requests delivery metrics for "mb-001" without include_package_daily_breakdown
    Then the response packages should NOT include "daily_breakdown" arrays
    # v3.1: default false bounds payload size for multi-package long-flight buys

  @T-UC-004-v31-metric-scope-standard @main-flow @v3-1 @metric-scope
  Scenario: committed_metrics entry with scope standard keys by metric_id alone
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the seller committed to deliver "impressions" as a standard metric
    When the Buyer Agent requests delivery metrics for "mb-001"
    Then the response committed_metrics should include an entry with scope "standard"
    And that entry should include "metric_id" with value "impressions"
    And that entry should NOT include "vendor"
    # v3.1: standard scope -> metric_id alone, MUST resolve in available-metric.json
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-v31-metric-scope-vendor @main-flow @v3-1 @metric-scope
  Scenario: committed_metrics entry with scope vendor keys by (vendor, metric_id) tuple
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    And the seller committed to deliver vendor metric "viewable_impressions" from brand "moat"
    When the Buyer Agent requests delivery metrics for "mb-001"
    Then the response committed_metrics should include an entry with scope "vendor"
    And that entry should include "metric_id" with value "viewable_impressions"
    And that entry should include "vendor" referencing brand "moat"
    # v3.1: vendor scope -> (vendor, metric_id) tuple; vendor anchored on brand.json
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-v31-forecastable-metric-vocab @main-flow @v3-1 @forecastable-metric
  Scenario Outline: Forecast metrics map accepts forecastable-metric vocabulary
    Given a forecast point for media buy "mb-001"
    When the seller publishes the forecast with metric key "<metric>"
    Then the metric key should be a recognized forecastable-metric value
    And the consumer should treat the value as numeric
    # v3.1: forecastable-metric mirrors available-metric except for audience_size + measured_impressions deltas

    Examples: shared with available-metric (delivery-equivalent forecast keys)
      | metric            |
      | impressions       |
      | clicks            |
      | spend             |
      | completed_views   |
      | engagements       |

    Examples: forecast-only deltas
      | metric                |
      | audience_size         |
      | measured_impressions  |

  @T-UC-004-storyboard-controller-driven-delivery-schema-compliance @storyboard-v3.1 @v3-1 @schema-compliance @controller-driven
  Scenario: Delivery reporting -- controller-injected impressions and spend produce schema-compliant get_media_buy_delivery response
    Given a comply_test_controller has injected simulated delivery with impressions 5000, clicks 150, and spend 250.00 USD into a media buy
    When the Buyer Agent calls get_media_buy_delivery for the media buy with include_package_daily_breakdown true
    Then the response should be schema-valid against get-media-buy-delivery-response.json
    And the response should carry a media_buy_deliveries array with at least one entry
    And the per-package breakdown should reflect the injected impressions and spend
    # delivery_reporting storyboard: the test controller injects delivery metrics
    # via the simulate_delivery scenario, then the buyer calls
    # get_media_buy_delivery. Seller MUST return schema-compliant data with
    # per-package metrics (impressions, spend, pacing). Without this anchor,
    # sellers can return arbitrary formats and still pass smoke tests; the
    # schema check is the protocol-level contract.
    # delivery_reporting: schema compliance after controller-driven delivery
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/measurement_accountability.yaml

  @T-UC-004-storyboard-required-metrics-end-to-end-accountability @storyboard-v3.1 @v3-1 @missing-metrics @accountability
  Scenario: Measurement accountability -- required_metrics declared at discovery surfaces missing_metrics in delivery
    Given the buyer declared filters.required_metrics including "completed_views" at get_products time
    And the seller filtered products to those whose reporting_capabilities.available_metrics is a superset of required_metrics
    And the buyer created a media buy from one of those products
    And controller-driven simulated delivery emitted impressions but did not emit "completed_views"
    When the Buyer Agent calls get_media_buy_delivery for the media buy
    Then the response should be schema-valid against get-media-buy-delivery-response.json
    And the per-package missing_metrics entry should include scope "standard" and metric_id "completed_views"
    And missing_metrics should be empty or absent when all product-declared metrics were emitted
    # measurement_accountability storyboard: end-to-end contract in three pieces:
    #   1. filters.required_metrics on get_products: seller MUST exclude products
    #      whose available_metrics is not a superset (filter-not-fail; not an error).
    #   2. Product's available_metrics carries forward as the reporting contract.
    #   3. by_package[].missing_metrics on get_media_buy_delivery: seller lists
    #      any product-declared metrics NOT populated in this report.
    # Derived ratio metrics (e.g. completion_rate) are satisfied by their
    # underlying scalars in available_metrics. Existing UC-004 missing_metrics
    # scenarios target single missing entries; this storyboard anchor asserts
    # the full lifecycle (discovery -> reporting contract -> emission).
    # measurement_accountability: required_metrics at discovery -> missing_metrics in delivery
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/vendor_metric_accountability.yaml

  @T-UC-004-storyboard-vendor-metric-end-to-end @storyboard-v3.1 @v3-1 @vendor-metric @accountability
  Scenario: Vendor metric accountability -- declaration on product, filter at discovery, emission in delivery
    Given the seller's product declared reporting_capabilities.vendor_metrics for vendor "attentionvendor.example" and metric_id "attention_units"
    And the buyer declared filters.required_vendor_metrics matching that pointer at get_products time
    And the buyer created a media buy from the matching product
    And controller-driven simulated delivery emitted vendor_metric_values for that vendor metric
    When the Buyer Agent calls get_media_buy_delivery for the media buy
    Then the response should be schema-valid against get-media-buy-delivery-response.json
    And the per-package vendor_metric_values should carry one row per (vendor.domain, vendor.brand_id, metric_id)
    And the row for vendor "attentionvendor.example" should include metric_id "attention_units"
    And the seller should NOT emit duplicate rows for the same (vendor.domain, vendor.brand_id, metric_id) within a single reporting period
    # vendor_metric_accountability storyboard: vendor-defined metrics (attention
    # scores, emissions, panel demographics) flow through the lifecycle:
    #   1. reporting_capabilities.vendor_metrics on the product -- pointer
    #      {vendor, metric_id} into vendor's catalog (category, methodology,
    #      documentation at vendor's brand.json).
    #   2. filters.required_vendor_metrics on get_products -- buyer declares;
    #      seller silently excludes products that don't match (filter-not-fail).
    #      At least one of vendor or metric_id must be pinned per entry.
    #   3. by_package[].vendor_metric_values on get_media_buy_delivery -- one row
    #      per (vendor.domain, vendor.brand_id, metric_id) per reporting period;
    #      seller MUST de-duplicate before emission.
    # vendor_metric_accountability: declaration -> filter -> emission contract

  @T-UC-004-aggr-scope-standard @invariant @BR-RULE-220 @metric-aggregates
  Scenario: Standard-scope aggregate row draws metric_id from the closed enum
    Given delivery data for two media buys owned by "buyer-001"
    And a metric_aggregates row with scope "standard"
    When the Buyer Agent requests delivery metrics for the media buys
    Then the row's metric_id should be a member of the available-metric enum
    And the row should carry "scope", "metric_id", and "value"
    # BR-RULE-220 INV-3: scope=standard -> metric_id from available-metric.json enum, requires [scope, metric_id, value]
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-aggr-components @invariant @BR-RULE-220 @metric-aggregates
  Scenario Outline: Standard rate or cost metric carries its required component fields
    Given delivery data for two media buys owned by "buyer-001"
    And a metric_aggregates row with scope "standard" and metric_id "<metric_id>"
    When the Buyer Agent requests delivery metrics for the media buys
    Then the row MUST carry component fields "<component_a>" and "<component_b>"
    # BR-RULE-220 INV-5/INV-6/INV-7/INV-8: rate/cost metric rows require their component fields
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

    Examples:
      | metric_id            | component_a            | component_b          |
      | viewability          | measurable_impressions | viewable_impressions |
      | completion_rate      | impressions            | completed_views      |
      | cost_per_acquisition | spend                  | conversions          |
      | roas                 | spend                  | conversion_value     |

  @T-UC-004-aggr-brand-lift-qualifier @invariant @BR-RULE-220 @metric-aggregates
  Scenario: Brand-lift aggregate row requires a lift_dimension qualifier
    Given delivery data for two media buys owned by "buyer-001"
    And a metric_aggregates row with scope "standard" and metric_id "brand_lift"
    When the Buyer Agent requests delivery metrics for the media buys
    Then the row's qualifier MUST include "lift_dimension"
    # BR-RULE-220 INV-9: brand_lift -> qualifier must include lift_dimension
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-aggr-qualifier-closed @invariant @BR-RULE-220 @metric-aggregates
  Scenario: Qualifier keys are confined to the closed vocabulary
    Given delivery data for two media buys owned by "buyer-001"
    And a metric_aggregates row carrying a qualifier object
    When the Buyer Agent requests delivery metrics for the media buys
    Then each qualifier key MUST be one of "viewability_standard", "completion_source", "attribution_methodology", "attribution_window", or "lift_dimension"
    And no other qualifier key should be present
    # BR-RULE-220 INV-10: qualifier keys confined to closed vocab (additionalProperties: false)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-aggr-finest-granularity @invariant @BR-RULE-220 @metric-aggregates
  Scenario: Qualified metric is emitted at the finest available granularity
    Given delivery data for two media buys owned by "buyer-001"
    And the seller reports a qualified metric at the finest granularity it can provide
    When the Buyer Agent requests delivery metrics for the media buys
    Then the response should emit one row per (metric_id, full qualifier set)
    # BR-RULE-220 INV-12: one row per (metric_id, full qualifier set) at finest granularity; buyer re-aggregates up
    # --- Measurement Window Supersession (BR-RULE-221) ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-window-isfinal-absent @invariant @BR-RULE-221 @measurement-window
  Scenario: Absent is_final means the seller draws no provisional/final distinction
    Given delivery data for media buy "mb-001" owned by "buyer-001"
    And a package report with no "is_final" flag
    When the Buyer Agent requests delivery metrics for "mb-001"
    Then the buyer should treat the data as not distinguished provisional from final
    # BR-RULE-221 INV-3: is_final absent -> seller does not distinguish provisional from final
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-window-absent @invariant @BR-RULE-221 @measurement-window
  Scenario: Absent measurement_window means data is not windowed
    Given delivery data for media buy "mb-001" owned by "buyer-001"
    And a package report with no "measurement_window"
    When the Buyer Agent requests delivery metrics for "mb-001"
    Then the data should be treated as not windowed (final on first delivery)
    # BR-RULE-221 INV-5: measurement_window absent -> not windowed (standard digital, final on first delivery)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-window-first-report @alternative @webhook @invariant @BR-RULE-221 @measurement-window
  Scenario: First report for a period carries no supersedes_window
    Given no prior report exists for media buy "mb-001" for the reporting period
    When the seller delivers the first report for the period
    Then "supersedes_window" should be absent
    # BR-RULE-221 INV-7: first report for a period -> supersedes_window absent (no prior window to replace)
    # --- Delayed Data Signaling (BR-RULE-222) ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-delayed-count-nonnegative @boundary @BR-RULE-222 @partial-data @webhook
  Scenario: unavailable_count is a non-negative integer
    Given a webhook delivery covering media buys for "buyer-001"
    And "unavailable_count" is present
    When the seller delivers the webhook notification
    Then "unavailable_count" MUST be an integer greater than or equal to 0
    # BR-RULE-222 INV-4: unavailable_count >= 0 (integer)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-delayed-no-false-complete @invariant @BR-RULE-222 @partial-data @truthfulness
  Scenario: Delayed data is flagged, never presented as complete
    Given a webhook delivery covering media buys for "buyer-001"
    And one media buy has missing or delayed data
    When the seller delivers the webhook notification
    Then the seller MUST NOT present that data as complete (zeroed or final)
    And the buy MUST be flagged via "reporting_delayed" / "partial_data"
    # BR-RULE-222 INV-7: missing/delayed data must be flagged, never reported as complete
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-delayed-sync-absent @invariant @BR-RULE-222 @partial-data
  Scenario: Synchronous API response omits webhook-only partial-data signals
    Given a media buy "mb-001" owned by "buyer-001"
    When the Buyer Agent requests delivery metrics for "mb-001" via the synchronous API
    Then "partial_data" should be absent
    And "unavailable_count" should be absent
    # BR-RULE-222 INV-8: synchronous response -> partial_data and unavailable_count absent (webhook-context fields)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-delayed-all-available @invariant @BR-RULE-222 @partial-data @webhook
  Scenario: Webhook with all media buys available does not flag partial_data
    Given a webhook delivery covering three media buys for "buyer-001"
    And every media buy has available delivery data (no reporting_delayed or failed)
    When the seller delivers the webhook notification
    Then "partial_data" should be false or absent
    And "unavailable_count" should be 0 or absent
    # BR-RULE-222 INV-1 (counter): no delayed/failed buy -> partial_data not forced true
    # --- Missing Metrics Contract Accountability (BR-RULE-223) ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-missing-committed-source @invariant @BR-RULE-223 @missing-metrics @accountability
  Scenario: Missing set is computed against committed_metrics committed before period end
    Given a package for media buy "mb-001" with committed_metrics present
    And a committed metric whose committed_at precedes reporting_period.end
    When the Buyer Agent requests delivery metrics for "mb-001"
    Then the missing set MUST be computed against committed_metrics entries with committed_at before reporting_period.end
    # BR-RULE-223 INV-2: committed_metrics present -> missing set computed against entries committed_at < reporting_period.end
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-missing-late-commit @invariant @BR-RULE-223 @missing-metrics @accountability
  Scenario: A metric committed at or after period end is not flagged for the earlier period
    Given a package for media buy "mb-001" with committed_metrics present
    And a committed metric whose committed_at is at or after reporting_period.end
    When the Buyer Agent requests delivery metrics for "mb-001"
    Then that metric MUST NOT be flagged missing for the earlier period
    # BR-RULE-223 INV-3: committed_at >= reporting_period.end -> not flagged missing for that period
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-missing-fallback @invariant @BR-RULE-223 @missing-metrics @accountability
  Scenario: Without committed_metrics the missing set falls back to current available_metrics
    Given a package for media buy "mb-001" with committed_metrics absent
    When the Buyer Agent requests delivery metrics for "mb-001"
    Then the missing set MUST fall back to the product's current reporting_capabilities.available_metrics with no timestamp filter
    # BR-RULE-223 INV-4: committed_metrics absent -> fall back to current available_metrics, no timestamp filter
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-missing-scope @invariant @BR-RULE-223 @missing-metrics
  Scenario Outline: Missing entry carries the required fields for its scope
    Given a package for media buy "mb-001" with a missing_metrics entry of scope "<scope>"
    When the Buyer Agent requests delivery metrics for "mb-001"
    Then the missing entry MUST carry "<required_fields>"
    # BR-RULE-223 INV-5/INV-6: standard requires scope+metric_id (from enum); vendor requires scope+vendor+metric_id
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

    Examples:
      | scope    | required_fields          |
      | standard | scope, metric_id         |
      | vendor   | scope, vendor, metric_id |

  @T-UC-004-missing-qualifier-match @invariant @BR-RULE-223 @missing-metrics
  Scenario: Missing entry for a qualified committed metric mirrors its qualifier exactly
    Given a package for media buy "mb-001" with a qualified committed metric not populated
    When the Buyer Agent requests delivery metrics for "mb-001"
    Then the missing_metrics entry's qualifier MUST equal the qualifier on that committed_metrics entry
    # BR-RULE-223 INV-7: standard missing entry for a qualified committed metric -> qualifier deep-equals committed qualifier
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-missing-window-exclusion @invariant @BR-RULE-223 @missing-metrics @measurement-window
  Scenario: A metric not yet measurable for the current window is excluded from missing_metrics
    Given a package for media buy "mb-001" with a committed metric not yet measurable for the current measurement_window
    When the Buyer Agent requests delivery metrics for "mb-001"
    Then that metric MUST be excluded from "missing_metrics"
    And it MAY surface when a wider window supersedes via supersedes_window
    # BR-RULE-223 INV-8: not measurable for current window -> excluded from missing_metrics (surfaces on wider window)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-missing-dropped-metric @invariant @BR-RULE-223 @missing-metrics @accountability
  Scenario: A metric the product later dropped is still flagged if it was committed before period end
    Given a package for media buy "mb-001" with a metric committed before period end
    And the product later dropped that metric from its available_metrics
    And the report did not produce that metric
    When the Buyer Agent requests delivery metrics for "mb-001"
    Then that metric MUST still be flagged in "missing_metrics"
    # BR-RULE-223 INV-10: reconciliation independent of subsequent product mutations
    # --- Aggregated Reach Deduplication (BR-RULE-224) ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-reach-dedup @invariant @BR-RULE-224 @reach
  Scenario Outline: Reach value is deduplicated or summed per seller capability
    Given delivery data for media buys owned by "buyer-001" sharing one reach_unit
    And the seller "<dedup_capability>" cross-buy deduplicate
    When the Buyer Agent requests delivery metrics for the media buys
    Then "aggregated_totals.reach" should be the "<reach_value>"
    # BR-RULE-224 INV-4/INV-5: can dedup -> deduplicated; cannot -> sum of per-buy reach
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

    Examples:
      | dedup_capability | reach_value                    |
      | can              | deduplicated reach across buys |
      | cannot           | sum of per-buy reach           |

  @T-UC-004-reach-frequency-gate @invariant @BR-RULE-224 @reach
  Scenario Outline: frequency is reported only when reach is present
    Given delivery data for media buys owned by "buyer-001" where reach is "<reach_state>"
    When the Buyer Agent requests delivery metrics for the media buys
    Then "aggregated_totals.frequency" should be "<frequency_state>"
    # BR-RULE-224 INV-6/INV-7: frequency present -> reach present; reach omitted -> frequency omitted
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

    Examples:
      | reach_state | frequency_state |
      | present     | present         |
      | omitted     | omitted         |

  @T-UC-004-reach-nonnegative @boundary @BR-RULE-224 @reach
  Scenario: Reach and frequency values are non-negative
    Given delivery data for media buys owned by "buyer-001" with aggregated reach present
    When the Buyer Agent requests delivery metrics for the media buys
    Then "aggregated_totals.reach" should be greater than or equal to 0
    And "aggregated_totals.frequency" should be greater than or equal to 0
    # BR-RULE-224 INV-8: reach/frequency value >= 0
    # --- New / changed individual invariants (BR-RULE-030, 209, 018) ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-identify-account-scope @invariant @BR-RULE-030 @identification
  Scenario: Account filter scopes resolution to the referenced account
    Given media buys for "buyer-001" spanning accounts "acct-A" and "acct-B"
    When the Buyer Agent requests delivery metrics with account "acct-A"
    Then the response should include only media buys belonging to account "acct-A"
    # BR-RULE-030 INV-7: account filter provided -> resolution scoped to that account (omitted -> all accessible accounts)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-sandbox-natural-key @invariant @BR-RULE-209 @sandbox
  Scenario: Natural-key sandbox reference resolves without prior provisioning
    Given a sandbox account referenced by natural key brand "acme" and operator "gam" with sandbox true
    When the Buyer Agent requests delivery metrics referencing that sandbox account
    Then the seller MUST resolve the reference to the sandbox account for that brand/operator pair
    And no prior provisioning should be required
    # BR-RULE-209 INV-8: natural-key (brand+operator) + sandbox:true -> resolves to sandbox account without prior provisioning
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

  @T-UC-004-envelope-two-layer @invariant @BR-RULE-018 @response @error @webhook
  Scenario Outline: Two-layer error envelope distinguishes fatal failures from warnings
    Given an asynchronous delivery task for "buyer-001"
    And the task emits a "<severity>" condition
    When the seller returns the task result
    Then the protocol envelope adcp_error field should be "<envelope_state>"
    And the payload errors array should be "<payload_state>"
    And each populated payload error should include a "suggestion" field for how to resolve or proceed
    And the envelope MUST NOT emit legacy "task_status" or "response_status" fields
    # BR-RULE-018 INV-8: fatal -> adcp_error + payload.errors[]; warning -> payload.errors[] only (severity:warning), no envelope adcp_error; no legacy task_status/response_status

    Examples:
      | severity | envelope_state | payload_state                        |
      | fatal    | populated      | populated                            |
      | warning  | absent         | populated with severity warning only |

  @T-UC-004-boundary-metric-aggregates @boundary @BR-RULE-220 @metric-aggregates
  Scenario Outline: metric_aggregates boundary - <boundary_point>
    Given delivery data for two media buys owned by "buyer-001"
    When the seller assembles aggregated_totals at metric_aggregates boundary "<boundary_point>"
    Then the metric_aggregates handling should be <expected>
    # BR-RULE-220 INV-1..INV-12: metric_aggregates mutual-exclusion, scope row-shape, component fields, qualifier vocab
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

    Examples: Mutual exclusion with top-level scalars
      | boundary_point                                                       | expected |
      | metric_id in metric_aggregates AND top-level scalar omitted          | valid    |
      | metric_id in metric_aggregates AND top-level scalar present (or zeroed) | invalid  |
      | unqualified metric (impressions/spend/media_buy_count) at top level only | valid    |
      | unqualified metric placed in metric_aggregates                       | invalid  |
      | metric_aggregates empty array / omitted                              | valid    |

    Examples: Row shape per scope
      | boundary_point                                          | expected |
      | scope=standard, metric_id in available-metric enum      | valid    |
      | scope=standard, metric_id outside available-metric enum | invalid  |
      | scope=standard with [scope, metric_id, value] present   | valid    |
      | scope=standard missing value                            | invalid  |
      | scope=vendor with [scope, vendor, metric_id, value] present | valid    |
      | scope=vendor missing vendor                             | invalid  |

    Examples: Rate/cost metric component fields
      | boundary_point                                            | expected |
      | viewable_rate row with measurable_impressions+viewable_impressions | valid    |
      | viewable_rate row without component fields                | invalid  |
      | completion_rate row with impressions+completed_views      | valid    |
      | completion_rate row without component fields              | invalid  |
      | cost_per_acquisition row with spend+conversions           | valid    |
      | cost_per_acquisition row without component fields         | invalid  |
      | roas row with spend+conversion_value                      | valid    |
      | roas row without component fields                         | invalid  |
      | brand_lift row with lift_dimension in qualifier           | valid    |
      | brand_lift row without lift_dimension                     | invalid  |

    Examples: Qualifier vocabulary and partitioning
      | boundary_point                                                      | expected |
      | qualifier with only closed-vocab keys                               | valid    |
      | qualifier with a key outside closed vocabulary                      | invalid  |
      | two rows for same metric_id under different qualifier values, kept separate | valid    |
      | one row blending values across different qualifier values           | invalid  |

  @T-UC-004-boundary-missing-metrics @boundary @BR-RULE-223 @missing-metrics
  Scenario Outline: missing_metrics boundary - <boundary_point>
    Given a package for media buy "mb-001" with committed_metrics present
    When the seller computes by_package.missing_metrics at boundary "<boundary_point>"
    Then the missing_metrics handling should be <expected>
    # BR-RULE-223 INV-1..INV-10: missing-set computed against committed_metrics; scope/qualifier discipline; window and temporal exclusion
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

    Examples: Clean delivery and flagging
      | boundary_point                                                  | expected |
      | empty array (clean delivery)                                    | valid    |
      | field absent (clean delivery)                                   | valid    |
      | one standard metric missing                                     | valid    |
      | qualified standard metric missing, qualifier matches committed entry | valid    |
      | one vendor metric missing                                       | valid    |

    Examples: Temporal exclusion
      | boundary_point                                                  | expected |
      | metric committed at-or-after period end, excluded from earlier period | valid    |
      | metric not yet measurable for current window, excluded          | valid    |

    Examples: Malformed or premature entries
      | boundary_point                                                  | expected |
      | entry without scope discriminator                               | invalid  |
      | vendor entry without vendor brand-ref                           | invalid  |
      | standard metric_id outside the enum                             | invalid  |
      | qualifier differs from the committed entry                      | invalid  |
      | window-excluded metric flagged prematurely                      | invalid  |
      | metric flagged for a period that closed before its commitment   | invalid  |

  @T-UC-004-boundary-media-buy-status @boundary @media-buy-status
  Scenario Outline: media_buy status boundary - <boundary_point>
    Given a media buy "mb-001" owned by "buyer-001"
    When the response reports media buy status at boundary "<boundary_point>"
    Then the status handling should be <expected>
    # uc004_media_buy_status: base lifecycle enum + legacy pending alias + webhook-context reporting_delayed/failed; unknown values rejected
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

    Examples: Base lifecycle enum
      | boundary_point                          | expected |
      | pending_creatives (base lifecycle)      | valid    |
      | pending_start (base lifecycle)          | valid    |
      | active (base lifecycle)                 | valid    |
      | paused (base lifecycle)                 | valid    |
      | completed (base lifecycle)              | valid    |
      | rejected (base lifecycle)               | valid    |
      | canceled (base lifecycle)               | valid    |

    Examples: Aliases and webhook-context statuses
      | boundary_point                          | expected |
      | pending (legacy alias → pending_start)  | valid    |
      | reporting_delayed (webhook context)     | valid    |
      | failed (webhook context)                | valid    |
      | value outside the enum (e.g. 'running') | invalid  |

  @T-UC-004-boundary-measurement-window @boundary @BR-RULE-221 @measurement-window
  Scenario Outline: measurement_window boundary - <boundary_point>
    Given delivery data for media buy "mb-001" owned by "buyer-001"
    When the seller emits a report at measurement_window boundary "<boundary_point>"
    Then the window handling should be <expected>
    # BR-RULE-221 INV-1..INV-8: measurement_window references a declared window_id (1..50 chars); supersedes_window pairs with window_update; is_adjusted distinct from supersession
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

    Examples: Provisional vs windowed
      | boundary_point                                            | expected |
      | measurement_window absent (not windowed)                  | valid    |
      | measurement_window='live', is_final=false (provisional)   | valid    |
      | first report for period, supersedes_window null           | valid    |
      | adjusted/same-window corrected resend (is_adjusted=true)  | valid    |

    Examples: Identifier and supersession constraints
      | boundary_point                                                       | expected |
      | window_id not declared in reporting_capabilities                     | invalid  |
      | 51-character window identifier                                       | invalid  |
      | supersedes_window present without notification_type='window_update'  | invalid  |
      | notification_type='window_update' with no supersedes_window          | invalid  |

  @T-UC-004-boundary-aggregated-reach @boundary @BR-RULE-224 @reach
  Scenario Outline: aggregated_totals reach boundary - <boundary_point>
    Given delivery data for media buys owned by "buyer-001"
    When the seller assembles aggregated_totals reach fields at boundary "<boundary_point>"
    Then the reach handling should be <expected>
    # BR-RULE-224 INV-1..INV-8: reach present only when reach_unit homogeneous; reach_unit names the shared unit; frequency requires reach
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

    Examples: Homogeneity and field presence
      | boundary_point                                        | expected |
      | all three fields omitted (heterogeneous units)        | valid    |
      | reach + reach_unit + frequency, homogeneous deduplicated | valid    |
      | reach + reach_unit present, frequency omitted         | valid    |
      | reach_unit = custom (homogeneous)                     | valid    |

    Examples: Invalid combinations
      | boundary_point                                        | expected |
      | reach present, reach_unit absent                      | invalid  |
      | frequency present, reach absent                       | invalid  |
      | reach present while per-buy units are heterogeneous   | invalid  |
      | reach_unit = impressions (not in enum)                | invalid  |

  @T-UC-004-boundary-delayed-data @boundary @BR-RULE-222 @partial-data @webhook
  Scenario Outline: delayed data signaling boundary - <boundary_point>
    Given a webhook delivery covering media buys for "buyer-001"
    When the seller assembles partial-data signals at boundary "<boundary_point>"
    Then the delayed-data handling should be <expected>
    # BR-RULE-222 INV-1..INV-8: delayed/failed buy -> partial_data true + unavailable_count = count; reporting_delayed carries expected_availability; never report delayed data as complete
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

    Examples: Correct signaling
      | boundary_point                                                   | expected |
      | no delayed/failed buys, partial_data false/absent                | valid    |
      | partial_data true, unavailable_count = exact delayed/failed count | valid    |
      | reporting_delayed item with expected_availability present        | valid    |
      | reporting_delayed item with expected_availability omitted (ETA unknown) | valid    |

    Examples: Inconsistent or untruthful signaling
      | boundary_point                                                   | expected |
      | delayed/failed buy present but partial_data not true             | invalid  |
      | partial_data true but unavailable_count missing                  | invalid  |
      | unavailable_count != count of reporting_delayed/failed items     | invalid  |
      | delayed data reported as zero/final rather than flagged          | invalid  |

  @T-UC-004-boundary-sandbox @boundary @sandbox @BR-RULE-209
  Scenario Outline: sandbox response flag boundary - <boundary_point>
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    When the response is assembled at sandbox boundary "<boundary_point>"
    Then the sandbox flag handling should be <expected>
    # BR-RULE-209 INV-4/INV-5: sandbox account -> sandbox:true echoed; production account -> sandbox absent (or explicit false)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/get-media-buy-delivery-request.json

    Examples: Response flag semantics
      | boundary_point                                  | expected |
      | sandbox: true in response (sandbox account)     | valid    |
      | sandbox absent in response (production account) | valid    |
      | sandbox: false in response (explicit production) | valid    |

  @T-UC-004-boundary-package-commercial @boundary @package-commercial
  Scenario Outline: package commercial enum boundary - <boundary_point>
    Given a media buy "mb-001" owned by "buyer-001" with status "active"
    When the response reports per-package commercial fields at boundary "<boundary_point>"
    Then the commercial field handling should be <expected>
    # package_commercial_accountability: pricing_model and delivery_status draw from their closed enums (first/last members exercised)

    Examples: Enum membership
      | boundary_point                              | expected |
      | pricing_model = 'cpm' (in enum)             | valid    |
      | pricing_model = 'time' (last enum value)    | valid    |
      | delivery_status = 'goal_met' (last enum value) | valid    |
