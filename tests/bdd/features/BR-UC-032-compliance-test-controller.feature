# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

Feature: BR-UC-032 Compliance Test Controller (Sandbox-Only)
  As a conformance Runner driving compliance tests against a seller's sandbox
  I want to invoke comply_test_controller scenarios to force state transitions, simulate delivery, seed fixtures, and inspect upstream traffic
  So that I can validate the seller's behavior deterministically without orchestrating long happy-path setups — AND verify the seller refuses production-account invocations

  # SANDBOX-ONLY: per schema description, "Sellers MUST NOT expose this tool in production."
  # The defining business rule is the sandbox-only gate enforced by Extension A (FORBIDDEN).

  # Postconditions verified:
  #   POST-S1: Sandbox-only gate passed — targeted account is sandbox-flagged in persisted records
  #   POST-S2: list_scenarios returns seller's advertised scenario set
  #   POST-S3: force_* state scenarios transition entity; response carries previous_state and current_state
  #   POST-S4: force_create_media_buy_arm registers single-shot directive
  #   POST-S5: force_task_completion marks task complete with verbatim result delivery
  #   POST-S6: simulate_* applies simulated values; cumulative carries running totals (simulate_delivery)
  #   POST-S7: seed_* pre-populates fixture with stable ID
  #   POST-S8: query_upstream_traffic returns per-principal-scoped recorded_calls with secrets redacted
  #   POST-S9: Request context echoed unchanged
  #   POST-S10: Naturally idempotent — replays converge to same observable state
  #   POST-F1: System state unchanged on failure
  #   POST-F2: Runner receives structured error (success: false, error enum)
  #   POST-F3: Context echoed on failure when possible
  #   POST-F4: Production exposure rejected — targeting non-sandbox account returns FORBIDDEN
  #
  # Rules: BR-1 (SANDBOX-ONLY gate), BR-2 (caller declaration const: true), BR-3 (seller persisted-record verification),
  #        BR-4 (per-scenario if/then params), BR-5 (idempotent), BR-6 (open-for-extension),
  #        BR-7 (per-principal scoping), BR-8 (secret redaction), BR-9 (synthetic-vectors-only),
  #        BR-10 (force_task_completion verbatim delivery), BR-11 (force_create_media_buy_arm single-shot),
  #        BR-12 (structured error envelope), BR-13 (context echo)
  # Extensions: A (FORBIDDEN — production exposure rejected), B (scenario-level errors)
  # Error codes: FORBIDDEN, INVALID_TRANSITION, INVALID_STATE, NOT_FOUND, UNKNOWN_SCENARIO, INVALID_PARAMS, INTERNAL_ERROR

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And the Runner has an authenticated connection to the Seller Agent


  @T-UC-032-ext-a-prod-rejected @extension-a @sandbox-gate @forbidden @post-f1 @post-f2 @post-f4 @critical
  Scenario: Production account rejected — seller verifies persisted record and returns FORBIDDEN
    Given an account "acct-prod-001" exists in the seller's persisted records with sandbox flag false
    When the Runner invokes comply_test_controller with scenario "force_creative_status" targeting an entity owned by account "acct-prod-001"
    Then the response has success false
    And the response has error "FORBIDDEN"
    And the response has error_detail mentioning that the account is not a sandbox account
    And the persisted state of the targeted entity is unchanged
    # POST-F4: Production exposure rejected (the defining gate)
    # POST-F1: System state unchanged
    # POST-F2: Structured error returned
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-ext-a-prod-rejected-no-account-declaration @extension-a @sandbox-gate @forbidden @post-f4 @critical
  Scenario: Production account rejected even when caller omits the account declaration
    Given an account "acct-prod-002" exists in the seller's persisted records with sandbox flag false
    When the Runner invokes comply_test_controller with scenario "force_account_status" targeting account "acct-prod-002" and omits the account.sandbox declaration
    Then the response has success false
    And the response has error "FORBIDDEN"
    And the seller verified the persisted record rather than trusting the caller's omitted declaration
    # POST-F4: Seller-side persisted-record verification is the primary gate
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-main-sandbox-gate-pass @main-flow @sandbox-gate @happy-path @post-s1
  Scenario: Sandbox account accepted — gate passes, scenario handler dispatched
    Given an account "acct-sandbox-001" exists in the seller's persisted records with sandbox flag true
    When the Runner invokes comply_test_controller with scenario "list_scenarios" and account.sandbox true targeting account "acct-sandbox-001"
    Then the sandbox-only gate passes
    And the response has success true
    # POST-S1: Gate passed — sandbox-flagged account verified

  @T-UC-032-list-scenarios @main-flow @list-scenarios @happy-path @post-s2 @post-s9
  Scenario: list_scenarios returns the seller's advertised scenario set
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "list_scenarios"
    Then the response has success true
    And the response contains a scenarios array
    And each scenarios entry is a string from the documented scenario enum or a forward-compatible unknown string
    And the request context is echoed unchanged in the response
    # POST-S2: Runner receives advertised scenario set
    # POST-S9: Context echoed
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-list-scenarios-includes-upstream-traffic @main-flow @list-scenarios @upstream-traffic-opt-in @post-s2
  Scenario: Adopter advertising query_upstream_traffic opts into the upstream-traffic conformance contract
    Given the Runner targets a sandbox-flagged account
    And the seller has implemented query_upstream_traffic
    When the Runner invokes comply_test_controller with scenario "list_scenarios"
    Then the response scenarios array includes "query_upstream_traffic"
    And storyboards declaring check: upstream_traffic against this seller will be graded (not graded not_applicable)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-list-scenarios-omits-upstream-traffic @main-flow @list-scenarios @upstream-traffic-opt-out @post-s2
  Scenario: Adopter not advertising query_upstream_traffic causes upstream_traffic storyboards to grade not_applicable
    Given the Runner targets a sandbox-flagged account
    And the seller has NOT implemented query_upstream_traffic
    When the Runner invokes comply_test_controller with scenario "list_scenarios"
    Then the response scenarios array does NOT include "query_upstream_traffic"
    And storyboards declaring check: upstream_traffic against this seller grade not_applicable

  @T-UC-032-force-creative-status @main-flow @force-creative-status @state-transition @post-s3
  Scenario: force_creative_status transitions creative to rejected
    Given the Runner targets a sandbox-flagged account
    And a creative "cr-123" exists in state "processing" in the sandbox
    When the Runner invokes comply_test_controller with scenario "force_creative_status" params creative_id "cr-123" status "rejected" rejection_reason "Brand safety policy violation"
    Then the response has success true
    And the response has previous_state "processing"
    And the response has current_state "rejected"
    And the response may include a message string describing the transition
    # POST-S3: Entity transitioned; previous_state and current_state present
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-force-account-status @main-flow @force-account-status @state-transition @post-s3
  Scenario: force_account_status transitions account to suspended
    Given the Runner targets a sandbox-flagged account
    And an account "acct-456" exists in state "active" in the sandbox
    When the Runner invokes comply_test_controller with scenario "force_account_status" params account_id "acct-456" status "suspended"
    Then the response has success true
    And the response has previous_state "active"
    And the response has current_state "suspended"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-force-create-media-buy-arm-submitted @main-flow @force-create-media-buy-arm @directive @post-s4 @single-shot
  Scenario: force_create_media_buy_arm with submitted arm registers single-shot directive
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "force_create_media_buy_arm" params arm "submitted" task_id "task_async_signed_io_q2" message "Awaiting IO signature"
    Then the response has success true
    And the response has forced.arm "submitted"
    And the response has forced.task_id "task_async_signed_io_q2"
    And the next create_media_buy call from the same sandbox account returns the submitted arm with task_id "task_async_signed_io_q2"
    And the directive is consumed after the next create_media_buy call (single-shot)
    # POST-S4: Single-shot directive registered
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-force-task-completion-verbatim-delivery @main-flow @force-task-completion @verbatim-delivery @post-s5
  Scenario: force_task_completion delivers result payload verbatim to push_notification_config.url
    Given the Runner targets a sandbox-flagged account
    And a task "task_async_signed_io_q2" is registered in state "submitted"
    And the buyer has registered push_notification_config.url "https://buyer.example/webhook"
    When the Runner invokes comply_test_controller with scenario "force_task_completion" params task_id "task_async_signed_io_q2" and a result payload "{media_buy_id: mb-async, status: active, packages: [...]}"
    Then the response has success true
    And the task transitions to status "completed"
    And the seller delivers the result payload to "https://buyer.example/webhook"
    And the delivered payload preserves all caller-supplied fields verbatim
    And the seller MAY augment with seller-controlled fields like created_at or dsp_* IDs
    And the seller MUST NOT overwrite caller-supplied values
    # POST-S5: Verbatim delivery to push_notification_config.url with caller-supplied fields preserved
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-force-session-status-terminated @main-flow @force-session-status @state-transition @post-s3
  Scenario: force_session_status terminates a session with termination_reason
    Given the Runner targets a sandbox-flagged account
    And a session "sess-abc" exists in state "active"
    When the Runner invokes comply_test_controller with scenario "force_session_status" params session_id "sess-abc" status "terminated" termination_reason "session_timeout"
    Then the response has success true
    And the response has previous_state "active"
    And the response has current_state "terminated"

  @T-UC-032-simulate-delivery @main-flow @simulate-delivery @post-s6
  Scenario: simulate_delivery injects metrics and returns cumulative totals
    Given the Runner targets a sandbox-flagged account
    And a media buy "mb-789" exists in the sandbox
    When the Runner invokes comply_test_controller with scenario "simulate_delivery" params media_buy_id "mb-789" impressions 10000 clicks 150 reported_spend amount 150 currency "USD"
    Then the response has success true
    And the response has simulated with impressions 10000, clicks 150, reported_spend amount 150
    And the response has cumulative with running totals across all simulation calls for this media buy
    # POST-S6: Simulated values applied; cumulative carries running totals
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-simulate-budget-spend @main-flow @simulate-budget-spend @post-s6
  Scenario: simulate_budget_spend consumes budget to specified percentage
    Given the Runner targets a sandbox-flagged account
    And a media buy "mb-789" has a budget of amount 1000 currency "USD"
    When the Runner invokes comply_test_controller with scenario "simulate_budget_spend" params media_buy_id "mb-789" spend_percentage 95
    Then the response has success true
    And the response has simulated.spend_percentage 95
    And the response has simulated.computed_spend amount 950
    And the response has simulated.budget amount 1000

  @T-UC-032-seed-product @main-flow @seed-product @post-s7
  Scenario: seed_product pre-populates a product fixture with stable ID
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "seed_product" params product_id "test-product" fixture delivery_type "non_guaranteed" channels ["display"]
    Then the response has success true
    And the product "test-product" is available for reference by ID in subsequent storyboard steps
    # POST-S7: Fixture seeded with stable ID
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-seed-creative @main-flow @seed-creative @post-s7
  Scenario: seed_creative pre-populates an approved creative fixture
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "seed_creative" params creative_id "campaign_hero_video" fixture status "approved" format_id id "video_30s"
    Then the response has success true
    And the creative "campaign_hero_video" is available for reference by ID

  @T-UC-032-upstream-traffic-raw @main-flow @query-upstream-traffic @raw-mode @post-s8
  Scenario: query_upstream_traffic returns recorded_calls in raw mode (default)
    Given the Runner targets a sandbox-flagged account
    And the seller advertised query_upstream_traffic in list_scenarios
    And the agent has made an outbound POST to a synthetic audience-upload endpoint
    When the Runner invokes comply_test_controller with scenario "query_upstream_traffic" params since_timestamp "2026-05-02T14:30:00Z" attestation_mode "raw"
    Then the response has success true
    And the response carries total_count as a required integer at least 0
    And the response carries since_timestamp as a required ISO 8601 string echoing the request since_timestamp "2026-05-02T14:30:00Z"
    And the response contains recorded_calls ordered by timestamp ascending
    And each recorded_call has attestation_mode "raw" and carries the full payload
    And each recorded_call carries payload_length matching the post-redaction canonical byte length
    # POST-S8: per-principal-scoped recorded_calls returned
    # v3.1 schema: UpstreamTrafficSuccess.required = [success, recorded_calls, total_count, since_timestamp]
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-upstream-traffic-per-principal-scoping @main-flow @query-upstream-traffic @per-principal-scoping @post-s8 @critical
  Scenario: query_upstream_traffic MUST scope to requesting principal — cross-caller traffic excluded
    Given two principals "principal-A" and "principal-B" share the same sandbox process
    And principal-A has caused outbound HTTP calls
    And principal-B has caused different outbound HTTP calls
    When principal-A invokes comply_test_controller with scenario "query_upstream_traffic"
    Then the response recorded_calls contains only calls caused by principal-A
    And no call caused by principal-B appears in the response regardless of since_timestamp
    # BR-7: per-principal scoping is normative
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-upstream-traffic-secret-redaction @main-flow @query-upstream-traffic @secret-redaction @critical
  Scenario: Controller-side secret redaction strips authorization/token/api_key/cookie values before emission
    Given the Runner targets a sandbox-flagged account
    And the agent has made an outbound call carrying header keys "Authorization" and "X-API-Key" and body keys "access_token" "refresh_token" "cookie"
    When the Runner invokes comply_test_controller with scenario "query_upstream_traffic"
    Then each recorded_call payload has values at keys matching the secret pattern replaced with the literal "[redacted]"
    And the redaction is applied recursively at any depth in the payload
    And the redaction is case-insensitive against the pattern "^(authorization|credentials?|token|api[_-]?key|password|secret|client[_-]secret|refresh[_-]token|access[_-]token|bearer|session[_-]token|offering[_-]token|cookie|set[_-]cookie)$"
    # BR-8: secret redaction is normative on the controller
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-upstream-traffic-digest-mode @main-flow @query-upstream-traffic @digest-mode @post-s8
  Scenario: query_upstream_traffic in digest mode returns payload_digest_sha256 instead of raw payload
    Given the Runner targets a sandbox-flagged account
    And the agent has made an outbound POST with a JSON body
    When the Runner invokes comply_test_controller with scenario "query_upstream_traffic" params attestation_mode "digest" identifier_value_digests ["1f0c1bda935708f24218ee06d62f2a91dffaadb4cd5b7f9d33fcad66b66d97c4"]
    Then each recorded_call has attestation_mode "digest"
    And each recorded_call has payload_digest_sha256 (64-char lowercase hex)
    And each recorded_call has payload_length (byte length of post-redaction canonical body)
    And each recorded_call has identifier_match_proofs with one entry per requested digest
    And no recorded_call carries a raw payload field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-idempotent-replay @main-flow @idempotent @post-s10
  Scenario: Replaying a force_*_status request converges to the same observable state without idempotency_key
    Given the Runner targets a sandbox-flagged account
    And a creative "cr-123" exists in state "processing"
    When the Runner invokes comply_test_controller with scenario "force_creative_status" params creative_id "cr-123" status "rejected"
    And the Runner replays the same request a second time without any idempotency_key
    Then both responses report current_state "rejected"
    And the second invocation does not raise INVALID_TRANSITION for the already-rejected entity (or, if it does, the observable persisted state is unchanged from the first call)
    # POST-S10: Naturally idempotent — replays converge to same observable state

  @T-UC-032-ext-b-invalid-transition @extension-b @invalid-transition @post-f1 @post-f2
  Scenario: INVALID_TRANSITION — cannot transition from a terminal state
    Given the Runner targets a sandbox-flagged account
    And an entity is in a terminal state "archived"
    When the Runner invokes comply_test_controller with scenario "force_creative_status" attempting to transition the archived entity to "processing"
    Then the response has success false
    And the response has error "INVALID_TRANSITION"
    And the response has current_state "archived"
    And the response error_detail explains "Cannot transition from archived to processing — archived is terminal"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-ext-b-not-found @extension-b @not-found @post-f1 @post-f2
  Scenario: NOT_FOUND — referenced entity does not exist
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "force_creative_status" params creative_id "cr-unknown" status "approved"
    Then the response has success false
    And the response has error "NOT_FOUND"
    And the response has current_state null
    And the response error_detail mentions "cr-unknown"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-ext-b-unknown-scenario @extension-b @unknown-scenario @post-f1 @post-f2
  Scenario: UNKNOWN_SCENARIO — seller cannot decode the scenario string
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "force_quantum_state" which the seller has not implemented
    Then the response has success false
    And the response has error "UNKNOWN_SCENARIO"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-ext-b-invalid-params-missing-task-id @extension-b @invalid-params @post-f1 @post-f2
  Scenario: INVALID_PARAMS — force_create_media_buy_arm with arm "submitted" missing task_id is rejected
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "force_create_media_buy_arm" params arm "submitted" but omits task_id
    Then the response has success false
    And the response has error "INVALID_PARAMS" (or the request is schema-rejected by the if/then branch before reaching the seller)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-ext-b-invalid-params-result-payload @extension-b @invalid-params @post-f1 @post-f2
  Scenario: INVALID_PARAMS — force_task_completion result payload does not validate against async-response-data union branch
    Given the Runner targets a sandbox-flagged account
    And a task "task-x" is registered as a create_media_buy task
    When the Runner invokes comply_test_controller with scenario "force_task_completion" params task_id "task-x" result a malformed payload that does not validate as CreateMediaBuyResponse
    Then the response has success false
    And the response has error "INVALID_PARAMS"
    And the response error_detail explains that the payload did not validate against the response branch for the task's original method
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-ext-b-internal-error @extension-b @internal-error @post-f1 @post-f2 @post-f3
  Scenario: INTERNAL_ERROR — unexpected seller-side failure during scenario dispatch
    Given the Runner targets a sandbox-flagged account
    And the seller's scenario handler raises an unexpected internal failure during dispatch
    When the Runner invokes comply_test_controller with scenario "force_creative_status" params creative_id "cr-int-001" status "approved" and request context "{request_id: req-int-001}"
    Then the response has success false
    And the response has error "INTERNAL_ERROR"
    And the response has error_detail with a human-readable explanation
    And the persisted state of the targeted entity is unchanged
    And the response context echoes "{request_id: req-int-001}"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

  @T-UC-032-ext-b-context-echoed-on-failure @extension-b @context-echo @post-f3
  Scenario: Context is still echoed on failure when possible
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "force_creative_status" params creative_id "cr-unknown" status "approved" and request context "{request_id: req-001}"
    Then the response has success false
    And the response has error "NOT_FOUND"
    And the response context echoes "{request_id: req-001}"
    # POST-F3: Context echoed on failure when possible

  @T-UC-032-bva-recorded-calls-items @bva @boundary @recorded-calls @sandbox-only
  Scenario Outline: BVA — recorded_calls.items field bounds (<boundary>)
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "query_upstream_traffic"
    Then the recorded_calls item satisfies the boundary case "<boundary>"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

    Examples:
      | boundary |
      | method='GET' (first enum value) |
      | method='OPTIONS' (last enum value) |
      | method='CONNECT' (not in 7-value enum) |
      | status_code=100 (lower bound) |
      | status_code=599 (upper bound) |
      | status_code=99 (below minimum) |
      | status_code=600 (above maximum) |
      | status_code omitted (optional) |
      | payload exactly 65536 bytes (at maxLength boundary) |
      | payload > 65536 bytes without truncation marker |
      | payload > 64 KiB truncated with trailing `[…truncated]` marker (length still ≤ 65536) |
      | payload_length=0 (zero-length body, e.g., GET) |
      | payload_length=-1 (below minimum) |
      | purpose='platform_primary' (first enum value) |
      | purpose='other' (last enum value; also default-for-filtering) |
      | purpose omitted (treated as 'other' for purpose_filter matching) |
      | purpose='marketing' (not in 6-value enum) |
      | identifier_match_proofs=[] (empty array — valid in digest mode w/o identifier digests, or non-JSON content type) |
      | identifier_match_proofs with exactly 64 items (at maxItems boundary) |
      | identifier_match_proofs with 65 items (above maxItems cap) |
      | SHA-256 hex 64-char lowercase (valid pattern) |
      | SHA-256 hex with uppercase chars (violates lowercase pattern) |
      | SHA-256 hex shorter than 64 chars |
      | timestamp ISO 8601 date-time (valid) |
      | timestamp not ISO 8601 (e.g., '13/05/2026 14:30') |
      | item carries a field outside the schema property set |
      | item omits `method` (required) |
      | item omits `attestation_mode` (required + discriminator) |
      | item omits `payload_length` (required in both raw and digest modes) |
      | item omits `timestamp` (required) |

  @T-UC-032-bva-recorded-calls-attestation-mode @bva @boundary @recorded-calls @attestation-mode @sandbox-only
  Scenario Outline: BVA — recorded_calls.items.attestation_mode discriminator (<boundary>)
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "query_upstream_traffic"
    Then the recorded_calls item attestation_mode discriminator satisfies the boundary case "<boundary>"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

    Examples:
      | boundary |
      | attestation_mode=raw on a recorded_calls item with payload populated |
      | attestation_mode=digest on a recorded_calls item with payload_digest_sha256 populated and no identifier_match_proofs |
      | attestation_mode=digest with payload_digest_sha256 and identifier_match_proofs[] populated |
      | recorded_calls array with both raw and digest items (mixed-mode) |
      | request asked raw, adopter returned digest for one call (unilateral downgrade) |
      | attestation_mode field missing on an item |
      | attestation_mode='summary' (outside enum) |
      | attestation_mode=raw, payload absent |
      | attestation_mode=raw, both payload and payload_digest_sha256 present |
      | attestation_mode=raw, identifier_match_proofs present |
      | attestation_mode=digest, payload_digest_sha256 absent |
      | attestation_mode=digest, payload also present |

  @T-UC-032-bva-recorded-calls-timestamp @bva @boundary @recorded-calls @timestamp @sandbox-only
  Scenario Outline: BVA — recorded_calls.items.timestamp monotonicity (<boundary>)
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "query_upstream_traffic"
    Then the recorded_calls timestamp ordering satisfies the boundary case "<boundary>"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

    Examples:
      | boundary |
      | Two items with strictly increasing timestamps (canonical ascending case) |
      | Two items with the same timestamp (sub-millisecond tie, parallel fan-out) |
      | Single-item recorded_calls (monotonicity vacuously holds) |
      | Empty recorded_calls (monotonicity vacuously holds) |
      | UTC 'Z'-suffix form (recommended cross-timezone-unambiguous representation) |
      | Explicit UTC offset form (still RFC 3339 date-time) |
      | Raw item followed by digest item, ascending timestamps (mixed-mode monotonic) |
      | Item missing the timestamp field |
      | timestamp value 'today' or '2026-05-02' or a Unix-epoch integer (not RFC 3339 date-time) |
      | items[2].timestamp earlier than items[1].timestamp (monotonicity inversion) |
      | timestamp is the log-flush instant rather than the send instant |
      | timestamp is the response-receipt instant rather than the send instant |
      | All items in a fan-out batch share the dispatcher's group-time rather than each call's individual send-time |
      | Individual timestamps are well-formed but the array is not returned in ascending order |

  @T-UC-032-bva-upstream-traffic-success-required-fields @bva @boundary @query-upstream-traffic @upstream-traffic-success @sandbox-only
  Scenario Outline: BVA — UpstreamTrafficSuccess top-level required fields (<boundary>)
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "query_upstream_traffic"
    Then the UpstreamTrafficSuccess required-field-set satisfies the boundary case "<boundary>"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

    Examples:
      | boundary |
      | response carries success=true, recorded_calls=[], total_count=0, since_timestamp (all four required keys present) |
      | response carries success=true, recorded_calls=[item], total_count=1, since_timestamp, truncated=false (all required + optional truncated) |
      | response omits total_count (REQUIRED per v3.1 schema — schema-rejects) |
      | response omits since_timestamp (REQUIRED per v3.1 schema — schema-rejects) |
      | response omits truncated (optional — valid) |
      | total_count present but negative (below integer minimum 0) |
      | total_count present as string (wrong type) |
      | since_timestamp present but not RFC 3339 date-time |
      | since_timestamp value equals request `since_timestamp` (echo case) |
      | request omitted `since_timestamp`; response substitutes session-start ISO 8601 instant |

  @T-UC-032-bva-force-task-completion-verbatim @bva @boundary @force-task-completion @verbatim-delivery @sandbox-only
  Scenario Outline: BVA — force_task_completion_verbatim_delivery (<boundary>)
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "force_task_completion"
    Then the verbatim-delivery contract satisfies the boundary case "<boundary>"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

    Examples:
      | boundary |
      | force_task_completion succeeded; webhook registered; caller-supplied result has 3 fields; seller adds 1 seller-controlled field with disjoint key |
      | force_task_completion succeeded; webhook NOT registered (force_task_completion still returns success) |
      | force_task_completion succeeded; webhook registered; seller mutates one caller-supplied field |
      | force_task_completion succeeded; webhook registered; seller drops one caller-supplied field |
      | force_task_completion succeeded; seller posts webhook to a URL other than the task-registered push_notification_config.url |
      | force_task_completion succeeded; seller's augment field key collides with caller key; seller writes its own value instead of dropping |

  @T-UC-032-bva-force-task-completion-result @bva @boundary @force-task-completion @params-result @sandbox-only
  Scenario Outline: BVA — params.result for force_task_completion (<boundary>)
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "force_task_completion"
    Then the params.result validation satisfies the boundary case "<boundary>"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

    Examples:
      | boundary |
      | valid result matching the task's original-method response branch |
      | params.task_id absent |
      | params.result absent |
      | result shape mismatches the original method's branch |
      | result payload size == 262143 bytes (just under 256 KB) |
      | result payload size == 262145 bytes (just over 256 KB), seller enforces MAY |
      | task_id refers to unknown task in caller's sandbox account |

  @T-UC-032-bva-force-create-media-buy-arm @bva @boundary @force-create-media-buy-arm @single-shot @sandbox-only
  Scenario Outline: BVA — force_create_media_buy_arm_directive (<boundary>)
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "force_create_media_buy_arm"
    Then the single-shot directive satisfies the boundary case "<boundary>"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

    Examples:
      | boundary |
      | params.arm = 'submitted' with params.task_id present (<=128 chars) |
      | params.arm = 'submitted' with params.task_id absent |
      | params.arm = 'input-required' |
      | params.arm = 'completed' (excluded arm; not in enum) |
      | params.arm = 'working' (excluded arm; not in enum) |
      | params.task_id length = 128 |
      | params.task_id length = 129 |
      | params.message length = 2000 |
      | params.message length = 2001 |
      | second create_media_buy from same principal (no re-arm) after directive consumption |
      | create_media_buy from a different principal after Principal A's registration |
      | two force_create_media_buy_arm calls from same principal before any create_media_buy |

  @T-UC-032-bva-simulate-numeric @bva @boundary @simulate @sandbox-only
  Scenario Outline: BVA — simulate_numeric_constraints (<boundary>)
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "simulate_delivery" or "simulate_budget_spend"
    Then the numeric constraint satisfies the boundary case "<boundary>"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

    Examples:
      | boundary |
      | impressions = 0 (lower bound, valid) |
      | impressions = -1 (below lower bound) |
      | impressions = 1.5 (non-integer) |
      | clicks = 0 (lower bound, valid) |
      | clicks = -5 (below lower bound) |
      | conversions = 0 (lower bound, valid) |
      | conversions = -2 (below lower bound) |
      | spend_percentage = 0 (lower inclusive bound) |
      | spend_percentage = 100 (upper inclusive bound) |
      | spend_percentage = -0.01 (just below 0) |
      | spend_percentage = 100.01 (just above 100) |
      | spend_percentage = 150 (far above 100) |
      | reported_spend.amount = 0 (lower bound, valid) |
      | reported_spend.amount = -1 (below lower bound) |
      | reported_spend.currency = 'USD' (matches ^[A-Z]{3}$) |
      | reported_spend.currency = 'usd' (lowercase, violates regex) |
      | reported_spend.currency = 'US' (2 chars, violates regex) |
      | reported_spend.currency = 'USDX' (4 chars, violates regex) |
      | reported_spend.currency = '840' (numeric ISO code, violates regex) |
      | reported_spend = {amount: 150, currency: 'USD'} (both required keys present) |
      | reported_spend = {currency: 'USD'} (amount missing, violates required) |
      | reported_spend = {amount: 150} (currency missing, violates required) |

  @T-UC-032-bva-endpoint-pattern @bva @boundary @query-upstream-traffic @endpoint-pattern @sandbox-only
  Scenario Outline: BVA — params.endpoint_pattern grammar (<boundary>)
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "query_upstream_traffic"
    Then the endpoint_pattern parameter satisfies the boundary case "<boundary>"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

    Examples:
      | boundary |
      | pattern '*' (matches every recorded call) |
      | pattern '<METHOD> *' (matches every URL for a given method) |
      | pattern '' (empty) |
      | pattern 'POST */audience/upload' (cross-segment `*` in URL) |
      | pattern 'POST /audience/upload' (no wildcards, exact literal) |
      | pattern contains `?` and implementation treats it as wildcard |
      | field omitted from params |

  @T-UC-032-bva-sandbox-only-gate @bva @boundary @sandbox-gate @sandbox-only
  Scenario Outline: BVA — sandbox_only_gate (<boundary>)
    Given the seller has persisted records of accounts
    When the Runner invokes comply_test_controller
    Then the sandbox-only gate evaluates the boundary case "<boundary>"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

    Examples:
      | boundary |
      | account object present with sandbox: true; targeted account is sandbox in persisted records |
      | account object omitted; targeted account is sandbox in persisted records |
      | account object present with sandbox: false |
      | account object omitted; targeted account is production in persisted records |
      | account object present with sandbox: true; targeted account is production in persisted records |
      | tool deployed on a production-facing endpoint (deployment-time) |

  @T-UC-032-bva-caller-sandbox-declaration @bva @boundary @sandbox-gate @account-sandbox @sandbox-only
  Scenario Outline: BVA — account.sandbox caller-side declaration (<boundary>)
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with the caller-side account declaration
    Then the schema validation satisfies the boundary case "<boundary>"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

    Examples:
      | boundary |
      | account.sandbox = true (only accepted value) |
      | account object omitted entirely (optional in this version) |
      | account.sandbox = false (violates const: true) |
      | account present but sandbox absent (violates account.required) |
      | account.sandbox = "true" (string, violates type: boolean) |

  @T-UC-032-bva-force-session-status @bva @boundary @force-session-status @params-status @sandbox-only
  Scenario Outline: BVA — params.status for force_session_status (<boundary>)
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "force_session_status"
    Then the params.status enum and required-key gating satisfy the boundary case "<boundary>"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

    Examples:
      | boundary |
      | complete (first enum value) |
      | terminated (second enum value) |
      | terminated with termination_reason='session_timeout' |
      | active (not in narrowed enum, even if valid in broader session-status) |
      | status omitted with scenario=force_session_status |
      | session_id omitted with scenario=force_session_status and status present |

  @T-UC-032-bva-query-upstream-traffic-principal-scoping @bva @boundary @query-upstream-traffic @per-principal-scoping @sandbox-only
  Scenario Outline: BVA — query_upstream_traffic_principal_scoping (<boundary>)
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller with scenario "query_upstream_traffic"
    Then the per-principal scoping invariant satisfies the boundary case "<boundary>"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/compliance/comply-test-controller-request.json

    Examples:
      | boundary |
      | Caller P with own outbound calls in window → response contains only those calls |
      | Caller P with NO outbound calls in window → UpstreamTrafficSuccess with recorded_calls: [] |
      | P1 and P2 both query against shared sandbox account → disjoint views |
      | Caller P supplies since_timestamp before any of P's call timestamps → only P's calls returned (never another principal's) |
      | Post-re-auth identity P' queries → only P''s post-re-auth calls, pre-re-auth P traffic absent |
      | Caller P's response contains a call caused by another principal Q |
      | Implementation uses process-global recording buffer (no per-principal keying) |
      | Buffer keyed on sandbox account only; two principals on same account see merged view |
      | since_timestamp parameter pulls another principal's calls into caller's response |
      | Empty per-principal buffer returns NOT_FOUND / INTERNAL_ERROR / FORBIDDEN instead of empty-array success |
      | Pre-re-auth traffic from principal P appears in post-re-auth identity P''s response |

  @T-UC-032-bva-context-echo @bva @boundary @context-echo @sandbox-only
  Scenario Outline: BVA — context echo-unchanged invariant (<boundary>)
    Given the Runner targets a sandbox-flagged account
    When the Runner invokes comply_test_controller and observes the response context field
    Then the context-echo invariant satisfies the boundary case "<boundary>"

    Examples:
      | boundary |
      | request omits context; response omits context (POST-S9 happy path; POST-F3 failure path) |
      | request carries context; ListScenariosSuccess echoes it byte-for-byte |
      | request carries context; ControllerError echoes it byte-for-byte (POST-F3) |
      | request carries nested context; success branch echoes whole tree verbatim |
      | request omits context; response synthesizes a seller-generated context |
      | request carries context; response mutates a value (case-fold, trim, normalize) |
      | request carries context with multiple keys; response drops one key |
      | request carries context; response augments with seller-derived correlation field |
      | request carries context; success response omits context entirely (controller bug) |
      | request carries context; ControllerError omits context entirely (controller bug) |
