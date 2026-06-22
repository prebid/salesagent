# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

Feature: BR-UC-020 Build Creative
  As a Buyer (Human or AI Agent)
  I want to request AI-powered generation or transformation of a creative manifest
  So that I can obtain a creative ready for preview or sync in my target format

  # Postconditions verified:
  #   POST-S1: Buyer knows the generated or transformed creative manifest is ready for preview or sync
  #   POST-S2: Buyer knows the output manifest's format_id matches the requested target_format_id
  #   POST-S3: Buyer knows the output manifest contains all required assets for the target format
  #   POST-S4: Buyer knows the provenance metadata on the output manifest reflects AI generation
  #   POST-F1: Buyer knows the operation failed
  #   POST-F2: Buyer knows what went wrong (error explains the failure)
  #   POST-F3: Buyer knows how to recover (suggestion for corrective action)

  Background:
    Given a Seller Agent is operational and accepting requests
    And at least one creative agent is registered and reachable


  @T-UC-020-main-rest @main-flow @rest @post-s1 @post-s2 @post-s3 @post-s4
  Scenario: Build creative via REST - pure generation with minimal request
    Given the Buyer has a target_format_id with agent_url "https://creative.example.com" and id "display_300x250"
    And the creative agent supports the requested format
    When the Buyer Agent sends a build_creative request via A2A with target_format_id and message "Create a display banner"
    Then the response should be a successful BuildCreativeResponse
    And the response should contain a creative_manifest
    And the output creative_manifest format_id should match the requested target_format_id
    And the output creative_manifest should contain all required assets for the target format
    And the output creative_manifest should include provenance metadata
    # POST-S1: Creative manifest ready for preview or sync
    # POST-S2: Output format_id matches target_format_id
    # POST-S3: All required assets present
    # POST-S4: Provenance metadata reflects AI generation

  @T-UC-020-main-mcp @main-flow @mcp @post-s1 @post-s2 @post-s3 @post-s4
  Scenario: Build creative via MCP - pure generation with minimal request
    Given the Buyer has a target_format_id with agent_url "https://creative.example.com" and id "display_300x250"
    And the creative agent supports the requested format
    When the Buyer Agent calls build_creative MCP tool with target_format_id and message "Create a display banner"
    Then the response should be a successful BuildCreativeResponse
    And the response should contain a creative_manifest
    And the output creative_manifest format_id should match the requested target_format_id
    And the output creative_manifest should contain all required assets for the target format
    And the output creative_manifest should include provenance metadata
    # POST-S1: Creative manifest ready for preview or sync
    # POST-S2: Output format_id matches target_format_id
    # POST-S3: All required assets present
    # POST-S4: Provenance metadata reflects AI generation

  @T-UC-020-main-transform @main-flow @rest @post-s1 @post-s2
  Scenario: Build creative via REST - transformation mode
    Given the Buyer has an existing creative_manifest for format "display_728x90"
    And the Buyer has a target_format_id with agent_url "https://creative.example.com" and id "display_300x250"
    And the creative agent supports the requested format
    When the Buyer Agent sends a build_creative request via A2A with the existing creative_manifest and target_format_id
    Then the response should be a successful BuildCreativeResponse
    And the output creative_manifest format_id should match the requested target_format_id
    # POST-S1: Transformed manifest ready
    # POST-S2: Output format matches requested target

  @T-UC-020-main-refine @main-flow @rest @post-s1
  Scenario: Build creative via REST - refinement mode
    Given the Buyer has a previously generated creative_manifest from build_creative
    And the Buyer has the same target_format_id used for the original generation
    And the creative agent supports the requested format
    When the Buyer Agent sends a build_creative request via A2A with the previous output as creative_manifest and message "Make the headline bolder"
    Then the response should be a successful BuildCreativeResponse
    And the output creative_manifest should reflect the refinement instructions
    # POST-S1: Refined manifest ready

  @T-UC-020-main-brand @main-flow @rest @post-s1
  Scenario: Build creative with brand context resolved
    Given the Buyer has a target_format_id for a supported format
    And the Buyer Agent provides a brand reference with domain "acme-corp.com"
    And the brand domain resolves to brand identity (colors, logos, tone)
    When the Buyer Agent sends a build_creative request via A2A with target_format_id and brand reference
    Then the response should be a successful BuildCreativeResponse
    And the output creative should reflect the resolved brand identity
    # POST-S1: Creative manifest generated with brand context

  @T-UC-020-main-precedence @main-flow @rest
  Scenario: Message takes precedence over brief asset on conflict
    Given the Buyer has a creative_manifest with a brief asset containing direction "use blue background"
    And the Buyer Agent provides message "use red background"
    And the Buyer has a target_format_id for a supported format
    When the Buyer Agent sends a build_creative request via A2A with both message and creative_manifest
    Then the response should be a successful BuildCreativeResponse
    And the message instruction should take precedence over the brief asset direction
    # BR-5: Message field takes precedence over brief asset on conflicts

  @T-UC-020-ext-a-rest @extension @ext-a @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Format not supported - unregistered agent URL via REST
    Given the Buyer has a target_format_id with agent_url "https://unknown-agent.example.com" and id "display_300x250"
    And no creative agent is registered for "https://unknown-agent.example.com"
    When the Buyer Agent sends a build_creative request via A2A with the target_format_id
    Then the operation should fail
    And the error code should be "FORMAT_NOT_SUPPORTED"
    And the error message should contain "not supported"
    And the error should include "suggestion" field
    And the suggestion should contain "list_creative_formats"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error explains format is not available
    # POST-F3: Suggestion advises list_creative_formats for discovery

  @T-UC-020-ext-a-mcp @extension @ext-a @error @mcp @post-f1 @post-f2 @post-f3
  Scenario: Format not supported - unregistered agent URL via MCP
    Given the Buyer has a target_format_id with agent_url "https://unknown-agent.example.com" and id "display_300x250"
    And no creative agent is registered for "https://unknown-agent.example.com"
    When the Buyer Agent calls build_creative MCP tool with the target_format_id
    Then the operation should fail
    And the error code should be "FORMAT_NOT_SUPPORTED"
    And the error message should contain "not supported"
    And the error should include "suggestion" field
    And the suggestion should contain "list_creative_formats"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error explains format is not available
    # POST-F3: Suggestion advises list_creative_formats for discovery

  @T-UC-020-ext-a-unknown-format @extension @ext-a @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Format not supported - agent registered but format id unknown
    Given the Buyer has a target_format_id with agent_url "https://creative.example.com" and id "nonexistent_format"
    And the creative agent at "https://creative.example.com" is registered but does not support format "nonexistent_format"
    When the Buyer Agent sends a build_creative request via A2A with the target_format_id
    Then the operation should fail
    And the error code should be "FORMAT_NOT_SUPPORTED"
    And the error message should contain "not supported"
    And the error should include "suggestion" field
    And the suggestion should contain "list_creative_formats"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error explains format id is not known by the agent
    # POST-F3: Suggestion advises list_creative_formats

  @T-UC-020-ext-b-rest @extension @ext-b @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Invalid manifest - malformed creative_manifest via REST
    Given the Buyer has a target_format_id for a supported format
    And the Buyer Agent provides a creative_manifest that is structurally malformed (missing required format_id within it)
    When the Buyer Agent sends a build_creative request via A2A with the invalid creative_manifest
    Then the operation should fail
    And the error code should be "INVALID_MANIFEST"
    And the error message should contain "manifest"
    And the error should include "field" pointing to the problematic path
    And the error should include "suggestion" field
    And the suggestion should contain "fix"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error explains what is wrong with the manifest
    # POST-F3: Suggestion advises how to fix the manifest

  @T-UC-020-ext-b-mcp @extension @ext-b @error @mcp @post-f1 @post-f2 @post-f3
  Scenario: Invalid manifest - malformed creative_manifest via MCP
    Given the Buyer has a target_format_id for a supported format
    And the Buyer Agent provides a creative_manifest that is structurally malformed (missing required format_id within it)
    When the Buyer Agent calls build_creative MCP tool with the invalid creative_manifest
    Then the operation should fail
    And the error code should be "INVALID_MANIFEST"
    And the error message should contain "manifest"
    And the error should include "field" pointing to the problematic path
    And the error should include "suggestion" field
    And the suggestion should contain "fix"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error explains what is wrong with the manifest
    # POST-F3: Suggestion advises how to fix the manifest

  @T-UC-020-ext-b-incompatible @extension @ext-b @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Invalid manifest - assets incompatible with target format
    Given the Buyer has a target_format_id for a display format
    And the Buyer Agent provides a creative_manifest with assets that do not match the target format's input requirements
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "INVALID_MANIFEST"
    And the error message should describe the asset incompatibility
    And the error should include "field" pointing to the incompatible asset path
    And the error should include "suggestion" field
    And the suggestion should contain "required assets"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error explains asset incompatibility
    # POST-F3: Suggestion advises providing compatible assets
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-ext-b-width-no-height @extension @ext-b @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Invalid manifest - width provided without height in format_id
    Given the Buyer has a target_format_id with width 300 but no height
    When the Buyer Agent sends a build_creative request via A2A with the target_format_id
    Then the operation should fail
    And the error code should be "FORMAT_ID_DIMENSION_INCOMPLETE"
    And the error message should contain "width and height must both be present"
    And the error should include "suggestion" field
    And the suggestion should contain "both width and height"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error explains dimension co-dependency violation
    # POST-F3: Suggestion advises providing both dimensions

  @T-UC-020-ext-c-rest @extension @ext-c @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Compliance unsatisfied - single disclosure unsatisfiable via REST
    Given the Buyer has a target_format_id for a format that does not support footer position
    And the Buyer Agent provides a creative_manifest with brief containing compliance.required_disclosures with position "footer"
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "CREATIVE_REJECTED"
    And the error message should identify the disclosure that cannot be rendered
    And the error should include "field" pointing to the unsatisfied disclosure path
    And the error should include "details" with disclosure_text and position
    And the error should include "suggestion" field
    And the suggestion should contain "format that supports"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error identifies unsatisfied disclosure
    # POST-F3: Suggestion advises compatible format or position change
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-ext-c-mcp @extension @ext-c @error @mcp @post-f1 @post-f2 @post-f3
  Scenario: Compliance unsatisfied - single disclosure unsatisfiable via MCP
    Given the Buyer has a target_format_id for a format that does not support footer position
    And the Buyer Agent provides a creative_manifest with brief containing compliance.required_disclosures with position "footer"
    When the Buyer Agent calls build_creative MCP tool with the request
    Then the operation should fail
    And the error code should be "CREATIVE_REJECTED"
    And the error message should identify the disclosure that cannot be rendered
    And the error should include "field" pointing to the unsatisfied disclosure path
    And the error should include "details" with disclosure_text and position
    And the error should include "suggestion" field
    And the suggestion should contain "format that supports"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error identifies unsatisfied disclosure
    # POST-F3: Suggestion advises compatible format or position change
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-ext-c-multiple @extension @ext-c @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Compliance unsatisfied - multiple disclosures with one unsatisfiable
    Given the Buyer has a target_format_id for a format that supports "prominent" but not "footer" position
    And the Buyer Agent provides a creative_manifest with brief containing two required_disclosures
    And one disclosure requires position "prominent" and another requires position "footer"
    When the Buyer Agent sends a build_creative request
    Then the operation should fail with the entire request rejected
    And the error code should be "CREATIVE_REJECTED"
    And the error message should identify the unsatisfied footer disclosure
    And the error should include "suggestion" field
    And the suggestion should contain "format that supports"
    And the error recovery should be "correctable"
    # POST-F1: Entire request fails (no partial success)
    # POST-F2: Error identifies the specific unsatisfied disclosure
    # POST-F3: Suggestion advises compatible format

  @T-UC-020-ext-d-rest @extension @ext-d @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Creative agent unavailable - connection refused via REST
    Given the Buyer has a target_format_id for a registered format
    And the creative agent at the agent_url is unreachable (connection refused)
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "SERVICE_UNAVAILABLE"
    And the error message should contain "unreachable" or "unavailable"
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    And the error should include "retry_after" field with a delay value
    And the error recovery should be "transient"
    # POST-F1: Operation failed
    # POST-F2: Error explains creative agent is temporarily unavailable
    # POST-F3: Suggestion advises retrying after delay
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-ext-d-mcp @extension @ext-d @error @mcp @post-f1 @post-f2 @post-f3
  Scenario: Creative agent unavailable - connection refused via MCP
    Given the Buyer has a target_format_id for a registered format
    And the creative agent at the agent_url is unreachable (connection refused)
    When the Buyer Agent calls build_creative MCP tool with the request
    Then the operation should fail
    And the error code should be "SERVICE_UNAVAILABLE"
    And the error message should contain "unreachable" or "unavailable"
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    And the error should include "retry_after" field with a delay value
    And the error recovery should be "transient"
    # POST-F1: Operation failed
    # POST-F2: Error explains creative agent is temporarily unavailable
    # POST-F3: Suggestion advises retrying after delay
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-ext-d-timeout @extension @ext-d @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Creative agent unavailable - timeout during delegation
    Given the Buyer has a target_format_id for a registered format
    And the creative agent at the agent_url times out (30s default)
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "SERVICE_UNAVAILABLE"
    And the error message should contain "timed out"
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    And the error should include "retry_after" field with a delay value
    And the error recovery should be "transient"
    # POST-F1: Operation failed
    # POST-F2: Error explains timeout
    # POST-F3: Suggestion advises retrying
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-ext-d-malformed @extension @ext-d @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Creative agent unavailable - non-parseable response from agent
    Given the Buyer has a target_format_id for a registered format
    And the creative agent returns a response that cannot be parsed as BuildCreativeResponse
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "SERVICE_UNAVAILABLE"
    And the error message should contain "response" or "parse"
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    And the error recovery should be "transient"
    # POST-F1: Operation failed
    # POST-F2: Error explains malformed response
    # POST-F3: Suggestion advises retrying

  @T-UC-020-ext-e-quota-exceeded @extension @ext-e @error @post-f1 @post-f2 @post-f3
  Scenario: Account quota exceeded - billable build rejected before delegation
    Given the Buyer sends a build_creative request with an account reference for a build the creative agent charges for
    And the account has exhausted its build quota
    When the Buyer Agent sends the build_creative request
    Then the operation should fail without delegating to the creative agent
    And the error code should be "QUOTA_EXCEEDED"
    And the error should include "field" pointing to "account"
    And the error should include "suggestion" field
    And the suggestion should contain "quota"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error explains the account quota constraint
    # POST-F3: Suggestion advises raising quota or omitting billable options
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-ext-e-entitlement-denied @extension @ext-e @error @post-f1 @post-f2 @post-f3
  Scenario: Account entitlement denied - no applicable pricing option
    Given the Buyer sends a build_creative request with an account reference for a build the creative agent charges for
    And no rate-card pricing option applies to the account
    When the Buyer Agent sends the build_creative request
    Then the operation should fail without delegating to the creative agent
    And the error code should be "ENTITLEMENT_DENIED"
    And the error should include "field" pointing to "account"
    And the error should include "suggestion" field
    And the suggestion should contain "entitlement"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error explains the account entitlement constraint
    # POST-F3: Suggestion advises enabling entitlement

  @T-UC-020-ext-f-atomic-failure @extension @ext-f @error @post-f1 @post-f2 @post-f3
  Scenario: Multi-format build fails atomically - no partial manifests
    Given the Buyer sends a build_creative request with target_format_ids listing multiple formats
    And at least one requested format cannot be produced
    When the Buyer Agent sends the build_creative request
    Then the operation should fail with a single error response
    And the response should not contain a creative_manifests array
    And no partial manifests should be returned
    And the error code should be "FORMAT_MISMATCH" or "BUILD_FAILED"
    And the error message should identify which requested format failed
    And the error should include "suggestion" field
    And the error recovery should be "correctable"
    # POST-F1: Operation failed atomically (no partial manifests)
    # POST-F2: Error identifies which requested format(s) could not be produced
    # POST-F3: Suggestion advises correcting or removing the failing format(s)

  @T-UC-020-ext-g-async-submitted @extension @ext-g @async @post-s5
  Scenario: Build queued asynchronously - submitted task envelope returned
    Given the build cannot be confirmed synchronously due to a slow generative pipeline
    When the Buyer Agent sends the build_creative request
    Then the response should have status "submitted" and a task_id
    And the response should not contain creative_manifest or creative_manifests inline
    # POST-S5: Buyer holds a task_id to poll tasks/get or receive a completion webhook
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-ext-g-async-working @extension @ext-g @async
  Scenario: Async build reports working progress while running
    Given a build_creative task was accepted with status "submitted" and a task_id
    When the Buyer polls tasks/get for the task_id during generation
    Then the task status should be "working"
    And the task should report progress with current_step and total_steps
    # SM-001: submitted -> working
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-ext-g-async-input-required @extension @ext-g @async
  Scenario: Async build pauses for required creative direction
    Given a build_creative task is in "working" status
    And the build needs creative direction or brand-guideline approval
    When the Buyer polls tasks/get for the task_id
    Then the task status should be "input-required"
    And the task should report a reason for the required input
    And the build should resume once the Buyer supplies input
    # SM-001: working -> input-required -> working

  @T-UC-020-ext-h-replay @extension @ext-h @happy-path @post-s1 @post-s6
  Scenario: Idempotency replay - retry with same key returns prior outcome
    Given a prior build_creative completed for an idempotency_key
    And the Buyer retries with the same idempotency_key and an unchanged payload
    When the Buyer Agent sends the build_creative request
    Then the seller should return the original outcome shape unchanged
    And no new creative should be generated
    And no duplicate vendor_cost should be charged
    # POST-S1: Buyer receives the prior creative manifest(s) or prior task handle
    # POST-S6: No duplicate charge on the retry
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-ext-h-conflict @extension @ext-h @error @BR-RULE-211
  Scenario: Idempotency conflict - same key, divergent payload
    Given a prior completed build_creative exists for an idempotency_key
    And the Buyer reuses the same idempotency_key with a divergent canonical payload
    When the Buyer Agent sends the build_creative request
    Then the operation should fail
    And the error code should be "IDEMPOTENCY_CONFLICT"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And no new creative should be generated
    # BR-RULE-211 INV-3: divergent payload under reused key -> IDEMPOTENCY_CONFLICT
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-ext-h-in-flight @extension @ext-h @error @BR-RULE-211
  Scenario: Idempotency in-flight - same key while first request still running
    Given a build_creative request under an idempotency_key is still in flight
    And the Buyer issues a second request with the same idempotency_key
    When the Buyer Agent sends the build_creative request
    Then the operation may fail with error code "IDEMPOTENCY_IN_FLIGHT"
    And the error should include "retry_after" field
    And the error should include "suggestion" field
    And the error recovery should be "transient"
    # BR-RULE-211 INV-4: in-flight first call -> IDEMPOTENCY_IN_FLIGHT (transient)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-ext-h-expired @extension @ext-h @error @BR-RULE-211
  Scenario: Idempotency expired - cached response evicted past replay TTL
    Given an idempotency_key was recorded previously
    And the cached response has expired past replay_ttl_seconds
    When the Buyer retries with the same idempotency_key
    Then the operation should fail
    And the error code should be "IDEMPOTENCY_EXPIRED"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # BR-RULE-211 INV-5: TTL-expired key -> IDEMPOTENCY_EXPIRED; buyer must natural-key check before fresh key

  @T-UC-020-inv-155-1-holds @invariant @BR-RULE-155
  Scenario: BR-RULE-155 INV-1 holds - target_format_id present
    Given the Buyer provides a valid target_format_id with agent_url and id
    When the Buyer Agent sends a build_creative request
    Then the request should pass validation
    # BR-RULE-155 INV-1: target_format_id is present

  @T-UC-020-inv-155-1-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-1 violated - neither target format selector present
    Given the Buyer omits both target_format_id and target_format_ids from the request
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "TARGET_FORMAT_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "target_format_id"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-1: neither target_format_id nor target_format_ids present -> rejected
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-inv-155-2-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-2 violated - agent_url absent in target_format_id
    Given the Buyer provides a target_format_id with id but no agent_url
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "AGENT_URL_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "agent_url"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-2: agent_url absent -> rejected

  @T-UC-020-inv-155-3-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-3 violated - id absent in target_format_id
    Given the Buyer provides a target_format_id with agent_url but no id
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "FORMAT_ID_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "format identifier"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-3: id absent -> rejected

  @T-UC-020-inv-155-4-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-4 violated - width without height
    Given the Buyer provides a target_format_id with width 300 but no height
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "FORMAT_ID_DIMENSION_INCOMPLETE"
    And the error message should contain "width and height must both be present"
    And the error should include "suggestion" field
    And the suggestion should contain "both width and height"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-4: width without height -> rejected

  @T-UC-020-inv-155-4b-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-4 violated - height without width
    Given the Buyer provides a target_format_id with height 250 but no width
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "FORMAT_ID_DIMENSION_INCOMPLETE"
    And the error message should contain "width and height must both be present"
    And the error should include "suggestion" field
    And the suggestion should contain "both width and height"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-4: height without width -> rejected

  @T-UC-020-inv-155-5-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-5 violated - id contains invalid characters
    Given the Buyer provides a target_format_id with id "display 300x250!"
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "FORMAT_ID_INVALID_FORMAT"
    And the error message should contain "invalid characters"
    And the error should include "suggestion" field
    And the suggestion should contain "letters, digits, underscores, and hyphens"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-5: id outside [a-zA-Z0-9_-] -> rejected

  @T-UC-020-inv-155-6-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-6 violated - dimension less than 1
    Given the Buyer provides a target_format_id with width 0 and height 250
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "FORMAT_ID_DIMENSION_INVALID"
    And the error message should contain "positive integers"
    And the error should include "suggestion" field
    And the suggestion should contain "integers >= 1"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-6: dimension < 1 -> rejected

  @T-UC-020-inv-155-1-both-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-1 violated - both target format selectors present
    Given the Buyer provides both a target_format_id and a target_format_ids array
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "TARGET_FORMAT_MUTUALLY_EXCLUSIVE"
    And the error should include "suggestion" field
    And the suggestion should contain "exactly one"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-1: target_format_id and target_format_ids are mutually exclusive -> rejected
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-inv-155-7-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-7 violated - idempotency_key absent
    Given the Buyer omits idempotency_key from the request
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "IDEMPOTENCY_KEY_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "idempotency_key"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-7: idempotency_key is the sole required top-level field -> rejected when absent
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-inv-155-8-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-8 violated - idempotency_key fails length or pattern
    Given the Buyer provides an idempotency_key of 15 characters
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "IDEMPOTENCY_KEY_INVALID_FORMAT"
    And the error message should contain "16"
    And the error should include "suggestion" field
    And the suggestion should contain "16"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-8: idempotency_key must match ^[A-Za-z0-9_.:-]{16,255}$ -> rejected
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-inv-155-9-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-9 violated - target_format_ids present but empty
    Given the Buyer provides a target_format_ids array with zero elements
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "TARGET_FORMAT_IDS_EMPTY"
    And the error should include "suggestion" field
    And the suggestion should contain "at least one"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-9: target_format_ids requires minItems 1 -> rejected when empty
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-inv-155-10-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-10 violated - retrieval by creative_id without concept_id
    Given the Buyer supplies a creative_id for retrieval mode
    And the creative agent cannot guarantee a globally-unique creative_id
    And the Buyer omits concept_id
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "CONCEPT_ID_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "concept_id"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-10: concept_id required to disambiguate a retrieved creative -> rejected
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-inv-155-11-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-11 violated - item_limit below minimum
    Given the Buyer provides an item_limit of 0
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "ITEM_LIMIT_INVALID"
    And the error should include "suggestion" field
    And the suggestion should contain "integer >= 1"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-11: item_limit must be an integer >= 1 -> rejected

  @T-UC-020-inv-156-1-holds @invariant @BR-RULE-156
  Scenario: BR-RULE-156 INV-1 holds - output format_id matches request
    Given the Buyer requests build_creative with target_format_id id "display_300x250" and agent_url "https://creative.example.com"
    And the creative agent successfully generates a creative
    When the build_creative response is received
    Then the output creative_manifest.format_id.id should equal "display_300x250"
    And the output creative_manifest.format_id.agent_url should equal "https://creative.example.com"
    # BR-RULE-156 INV-1: successful response -> format_id equals target_format_id

  @T-UC-020-inv-156-2-violated @invariant @BR-RULE-156 @error
  Scenario: BR-RULE-156 INV-2 violated - output format id differs from request
    Given the Buyer requests build_creative with target_format_id id "display_300x250"
    And the creative agent produces output with format_id id "display_728x90" (wrong format)
    When the system validates the creative agent response
    Then the operation should fail with a system-level error
    And the error code should be "FORMAT_MISMATCH"
    And the error message should contain "does not match"
    And the error should include "suggestion" field
    And the suggestion should contain "correct target format"
    # POST-F3: Suggestion for recovery
    # BR-RULE-156 INV-2: output id differs -> system-level error

  @T-UC-020-inv-156-3-violated @invariant @BR-RULE-156 @error
  Scenario: BR-RULE-156 INV-3 violated - output agent_url differs from request
    Given the Buyer requests build_creative with target_format_id agent_url "https://creative.example.com"
    And the creative agent produces output with format_id agent_url "https://other-agent.example.com" (wrong agent)
    When the system validates the creative agent response
    Then the operation should fail with a system-level error
    And the error code should be "FORMAT_MISMATCH"
    And the error message should contain "does not match"
    And the error should include "suggestion" field
    And the suggestion should contain "correct target format"
    # POST-F3: Suggestion for recovery
    # BR-RULE-156 INV-3: output agent_url differs -> system-level error

  @T-UC-020-inv-156-4-holds @invariant @BR-RULE-156
  Scenario: BR-RULE-156 INV-4 holds - multi-format response has one manifest per requested format
    Given the Buyer requests build_creative with target_format_ids ["display_300x250", "display_728x90", "native_feed"]
    And the creative agent successfully generates all requested formats
    When the build_creative response is received
    Then the response should contain a creative_manifests array with exactly 3 manifests
    And each requested format should have exactly one matching manifest with no duplicates or extras
    # BR-RULE-156 INV-4: multi-format -> exactly one manifest per requested format
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-inv-156-5-holds @invariant @BR-RULE-156
  Scenario: BR-RULE-156 INV-5 holds - multi-format response order matches request order
    Given the Buyer requests build_creative with target_format_ids ["display_300x250", "display_728x90"]
    And the creative agent successfully generates all requested formats
    When the build_creative response is received
    Then creative_manifests[0].format_id.id should equal "display_300x250"
    And creative_manifests[1].format_id.id should equal "display_728x90"
    # BR-RULE-156 INV-5: creative_manifests order corresponds to target_format_ids request order
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-inv-156-6-violated @invariant @BR-RULE-156 @error
  Scenario: BR-RULE-156 INV-6 violated - manifest format_id matches no requested target
    Given the Buyer requests build_creative with target_format_ids ["display_300x250", "display_728x90"]
    And the creative agent produces a manifest whose format_id matches no requested target
    When the system validates the creative agent response
    Then the operation should fail with a system-level error
    And the error code should be "FORMAT_MISMATCH"
    And the error message should contain "does not match"
    And the error should include "suggestion" field
    # POST-F3: Suggestion for recovery
    # BR-RULE-156 INV-6: each manifest format_id must match one of the requested target_format_ids

  @T-UC-020-inv-157-1-holds @invariant @BR-RULE-157
  Scenario: BR-RULE-157 INV-1 holds - all disclosures satisfiable
    Given the Buyer has a brief asset with required_disclosures requiring position "prominent"
    And the target format supports "prominent" position
    When the Buyer Agent sends a build_creative request
    Then the response should be a successful BuildCreativeResponse
    And the creative should include the required disclosures in the output
    # BR-RULE-157 INV-1: all disclosures satisfiable -> generation proceeds

  @T-UC-020-inv-157-2-violated @invariant @BR-RULE-157 @error
  Scenario: BR-RULE-157 INV-2 violated - single disclosure unsatisfiable
    Given the Buyer has a brief asset with required_disclosures requiring position "footer"
    And the target format does not support "footer" position
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "CREATIVE_REJECTED"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "format that supports"
    # POST-F3: Suggestion for recovery
    # BR-RULE-157 INV-2: any disclosure unsatisfiable -> entire request fails with CREATIVE_REJECTED (correctable)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-inv-157-3-holds @invariant @BR-RULE-157
  Scenario: BR-RULE-157 INV-3 holds - no brief asset, compliance check skipped
    Given the Buyer has a target_format_id for a supported format
    And the Buyer Agent does not provide a creative_manifest with a brief asset
    When the Buyer Agent sends a build_creative request with only target_format_id and message
    Then the response should be a successful BuildCreativeResponse
    # BR-RULE-157 INV-3: no brief -> compliance check not triggered

  @T-UC-020-inv-157-4-violated @invariant @BR-RULE-157 @error
  Scenario: BR-RULE-157 INV-4 - compliance failure error includes disclosure details
    Given the Buyer has a brief asset with required_disclosures requiring position "footer" with text "AI-generated content"
    And the target format does not support "footer" position
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "CREATIVE_REJECTED"
    And the error should include "details" with the disclosure_text, position, and reason
    And the error should include "field" pointing to the specific disclosure
    And the error should include "suggestion" field
    And the suggestion should contain "format that supports"
    # POST-F3: Suggestion for recovery
    # BR-RULE-157 INV-4: failure identifies specific unsatisfied disclosure (CREATIVE_REJECTED)

  @T-UC-020-inv-158-1-holds @invariant @BR-RULE-158
  Scenario: BR-RULE-158 INV-1 holds - brand domain resolvable
    Given the Buyer provides a brand reference with domain "acme-corp.com"
    And "acme-corp.com" hosts a valid /.well-known/brand.json
    When the Buyer Agent sends a build_creative request with the brand reference
    Then brand identity (colors, logos, tone) should be available to the creative agent
    And the response should be a successful BuildCreativeResponse
    # BR-RULE-158 INV-1: domain resolvable -> brand identity available

  @T-UC-020-inv-158-2 @invariant @BR-RULE-158
  Scenario: BR-RULE-158 INV-2 - brand domain not resolvable
    Given the Buyer provides a brand reference with domain "nonexistent-brand.example"
    And "nonexistent-brand.example" does not host /.well-known/brand.json
    When the Buyer Agent sends a build_creative request with the brand reference
    Then generation may proceed without brand context or fail depending on creative agent requirements
    # BR-RULE-158 INV-2: domain not resolvable -> behavior depends on agent

  @T-UC-020-inv-158-3-holds @invariant @BR-RULE-158
  Scenario: BR-RULE-158 INV-3 holds - brand omitted
    Given the Buyer does not provide a brand reference
    When the Buyer Agent sends a build_creative request with only target_format_id
    Then the response should be a successful BuildCreativeResponse
    And the creative should be generated without brand context
    # BR-RULE-158 INV-3: brand omitted -> proceeds without brand context

  @T-UC-020-inv-158-4-holds @invariant @BR-RULE-158
  Scenario: BR-RULE-158 INV-4 holds - house-of-brands resolution
    Given the Buyer provides a brand reference with domain "nova-brands.com" and brand_id "spark"
    And "nova-brands.com" hosts a brand portfolio including brand "spark"
    When the Buyer Agent sends a build_creative request with the brand reference
    Then the specific "spark" brand identity should be resolved from the portfolio
    And the response should be a successful BuildCreativeResponse
    # BR-RULE-158 INV-4: domain + brand_id -> specific brand resolved

  @T-UC-020-inv-159-1-holds @invariant @BR-RULE-159
  Scenario: BR-RULE-159 INV-1 holds - successful response includes provenance
    Given a successful build_creative response is produced
    When the output creative_manifest is examined
    Then the creative_manifest should include a provenance object
    # BR-RULE-159 INV-1: successful build -> provenance metadata present

  @T-UC-020-inv-159-1-violated @invariant @BR-RULE-159 @error
  Scenario: BR-RULE-159 INV-1 violated - provenance absent on AI-generated output
    Given the creative agent returns a successful response without provenance metadata
    When the system validates the creative agent response
    Then the operation should fail
    And the error code should be "PROVENANCE_REQUIRED"
    And the error message should contain "provenance"
    And the error should include "suggestion" field
    And the suggestion should contain "provenance"
    # POST-F3: Suggestion for recovery
    # BR-RULE-159 INV-1: provenance absent on AI output -> error

  @T-UC-020-inv-159-2-holds @invariant @BR-RULE-159
  Scenario: BR-RULE-159 INV-2 holds - provenance includes ai_tool with name
    Given a successful build_creative response includes provenance with ai_tool
    When the provenance is examined
    Then the ai_tool should have a name identifying the AI system
    # BR-RULE-159 INV-2: ai_tool.name identifies the AI system

  @T-UC-020-inv-159-2-violated @invariant @BR-RULE-159 @error
  Scenario: BR-RULE-159 INV-2 violated - ai_tool present but name missing
    Given the creative agent returns provenance with ai_tool object but no name field
    When the system validates the creative agent response
    Then the operation should fail
    And the error code should be "AI_TOOL_NAME_REQUIRED"
    And the error message should contain "ai_tool.name"
    And the error should include "suggestion" field
    And the suggestion should contain "name of the AI tool"
    # POST-F3: Suggestion for recovery
    # BR-RULE-159 INV-2: ai_tool without name -> error

  @T-UC-020-inv-159-3-holds @invariant @BR-RULE-159
  Scenario: BR-RULE-159 INV-3 holds - disclosure.required with jurisdictions
    Given a successful build_creative response includes provenance with disclosure.required = true
    When the provenance is examined
    Then the disclosure should include a jurisdictions array
    And the jurisdictions array should specify where AI disclosure obligations apply
    # BR-RULE-159 INV-3: disclosure.required=true -> jurisdictions present

  @T-UC-020-inv-159-4-holds @invariant @BR-RULE-159
  Scenario: BR-RULE-159 INV-4 holds - asset-level provenance replaces manifest-level
    Given a successful build_creative response has manifest-level provenance
    And an individual asset within the manifest has its own provenance
    When the asset's effective provenance is determined
    Then the asset-level provenance should entirely replace the manifest-level provenance
    And there should be no field-level merging between manifest and asset provenance
    # BR-RULE-159 INV-4: asset provenance replaces manifest provenance entirely

  @T-UC-020-inv-018-1-holds @invariant @BR-RULE-018
  Scenario: BR-RULE-018 INV-1 holds - successful response has no errors field
    Given the creative agent successfully generates a creative manifest
    When the build_creative response is received
    Then the response should contain creative_manifest
    And the response should not contain an errors field
    # BR-RULE-018 INV-1: success -> no errors field

  @T-UC-020-inv-018-3-violated @invariant @BR-RULE-018 @error
  Scenario: BR-RULE-018 INV-3 violated - response with both success and error is invalid
    Given the creative agent returns a response with both creative_manifest and errors
    When the system validates the BuildCreativeResponse against schema
    Then the response should be rejected as a schema violation
    And the error should include "suggestion" field
    And the suggestion should contain "exclusively success or error"
    # POST-F3: Suggestion for recovery
    # BR-RULE-018 INV-3: both success and error -> schema violation

  @T-UC-020-inv-018-9-holds @invariant @BR-RULE-018
  Scenario: BR-RULE-018 INV-9 holds - synchronous build_creative response is exactly one shape
    Given the creative agent returns a synchronous build_creative response
    When the BuildCreativeResponse is validated against schema
    Then the response should match exactly one of single-format success, multi-format success, or terminal failure
    And the terminal-failure shape should not carry creative_manifest, creative_manifests, or status "submitted"
    # BR-RULE-018 INV-9: synchronous build_creative -> exactly one of three shapes
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-inv-018-10-violated @invariant @BR-RULE-018 @error
  Scenario: BR-RULE-018 INV-10 violated - multi-format build is not atomic
    Given the Buyer requests build_creative with target_format_ids for multiple formats
    And one requested format cannot be produced
    When the build_creative response is received
    Then the response should be the terminal-error shape with an errors array
    And the response should not contain a creative_manifests array
    And no partial manifests should be returned
    And the error should include "suggestion" field
    # POST-F3: Suggestion for recovery
    # BR-RULE-018 INV-10: multi-format is all-or-none -> no partial creative_manifests
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-inv-018-11-holds @invariant @BR-RULE-018
  Scenario: BR-RULE-018 INV-11 holds - submitted envelope defers the manifest
    Given the build cannot be confirmed synchronously
    When the build_creative response is received
    Then the response should have status "submitted" and a task_id
    And the response should not contain creative_manifest or creative_manifests inline
    And advisory non-blocking errors may accompany the submitted envelope
    # BR-RULE-018 INV-11: submitted envelope -> manifest deferred to completion artifact

  @T-UC-020-inv-211-8-holds @invariant @BR-RULE-211
  Scenario: BR-RULE-211 INV-8 holds - identical-key replay returns cached outcome without re-charge
    Given a prior completed build_creative exists for (seller, account, idempotency_key)
    And the Buyer retries with the same idempotency_key and an identical canonical payload
    When the Buyer Agent sends the build_creative request
    Then the seller should return the prior creative_manifest(s) unchanged
    And no new creative should be generated
    And no additional vendor_cost should accrue
    # BR-RULE-211 INV-8: build_creative identical-key replay -> cached outcome, no re-generate, no re-charge

  @T-UC-020-cost-disclosure-present @post-s6 @cost
  Scenario: Cost disclosure present when the creative agent charges
    Given the creative agent charges for the build
    When the build_creative response is received
    Then the response should include vendor_cost and currency
    And the response should include pricing_option_id
    And the response should include a consumption breakdown
    # POST-S6: Buyer knows the cost applied (vendor_cost, currency, pricing_option_id)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-cost-disclosure-absent @post-s6 @cost
  Scenario: Cost disclosure omitted for a free build
    Given the creative agent does not charge for the build
    When the build_creative response is received
    Then the response should be a successful BuildCreativeResponse
    And the response should omit the cost-disclosure fields
    # POST-S6: free build -> economic fields absent
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-preview-supported @post-s7 @preview
  Scenario: Inline preview requested and supported - preview renders returned
    Given the Buyer sends a build_creative request with include_preview true
    And the creative agent supports inline preview for the target format
    When the build_creative response is received
    Then the response should include preview renders
    # POST-S7: Buyer receives preview renders without a separate round trip
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-preview-failed @post-s7 @preview
  Scenario: Inline preview requested but generation fails - preview_error explains why
    Given the Buyer sends a build_creative request with include_preview true
    And preview generation fails while the build itself succeeds
    When the build_creative response is received
    Then the response should include a creative_manifest
    And the response should include a preview_error explaining the preview failure
    # POST-S7: preview generation failure surfaces a preview_error, manifest still returned
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-preview-not-requested @post-s7 @preview
  Scenario: Inline preview not requested - no preview field
    Given the Buyer sends a build_creative request without include_preview
    When the build_creative response is received
    Then the response should be a successful BuildCreativeResponse
    And the response should not include a preview field
    # POST-S7: preview opt-out -> no preview field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-sandbox-happy @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account build_creative produces simulated output with sandbox flag
    Given the request targets a sandbox account
    And a valid target_format_id referencing an available creative format
    When the Buyer Agent sends a build_creative request
    Then the response should be a successful BuildCreativeResponse
    And the response should include sandbox equals true
    And no real AI generation API calls should have been billed
    And no real billing records should have been created
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-2: real ad platform calls suppressed
    # BR-RULE-209 INV-3: real billing suppressed
    # BR-RULE-209 INV-4: response includes sandbox: true
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-sandbox-production @invariant @br-rule-209 @sandbox
  Scenario: Production account build_creative response does not include sandbox flag
    Given the request targets a production account
    And a valid target_format_id referencing an available creative format
    When the Buyer Agent sends a build_creative request
    Then the response should be a successful BuildCreativeResponse
    And the response should not include a sandbox field
    # BR-RULE-209 INV-5: production account -> sandbox absent
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account with invalid format returns real validation error
    Given the request targets a sandbox account
    When the Buyer Agent sends a build_creative request with invalid target_format_id
    Then the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

  @T-UC-020-boundary-sandbox-response @boundary @sandbox @br-rule-209
  Scenario Outline: Sandbox response semantics boundary -- <boundary_point>
    Given the request targets <account_kind>
    And a valid target_format_id referencing an available creative format
    When the Buyer Agent sends a build_creative request
    Then <outcome>
    # v3.1: sandbox response-flag boundaries for build_creative (BR-RULE-209 INV-4/INV-5).
    #       Cross-operation sandbox boundaries (list_accounts filter, sync_accounts request item,
    #       capability declaration, media-buy budget) belong to UC-010/UC-011 features.

    Examples: Boundary values
      | boundary_point                                   | account_kind                            | outcome                                          |
      | sandbox: true in response (sandbox account)      | a sandbox account                       | the response should include sandbox equals true  |
      | sandbox absent in response (production account)  | a production account                    | the response should not include a sandbox field  |
      | sandbox: false in response (explicit production) | a production account with sandbox false | the response should include sandbox equals false |

  @T-UC-020-creative-variable-preserved @v3-1 @creative-variable
  Scenario: build_creative preserves declared CreativeVariable entries from source manifest
    Given the request targets a production account
    And a source creative_manifest declaring a CreativeVariable with variable_id "headline_text" and variable_type "text"
    And a valid target_format_id referencing an available creative format
    When the Buyer Agent sends a build_creative request
    Then the response should be a successful BuildCreativeResponse
    And the output creative_manifest should include a CreativeVariable with variable_id "headline_text" and variable_type "text"
    # POST-S1: output ready for serve-time DCO population
    # POST-S3: declared variables retained alongside required assets
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-creative-variable-required-flag @v3-1 @creative-variable
  Scenario: build_creative preserves required flag on CreativeVariable
    Given the request targets a production account
    And a source creative_manifest declaring a CreativeVariable with required true
    And a valid target_format_id referencing an available creative format
    When the Buyer Agent sends a build_creative request
    Then the response should be a successful BuildCreativeResponse
    And the output CreativeVariable should retain required equal to true
    # POST-S1: serve-time substitution contract preserved across build
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/build-creative-request.json

  @T-UC-020-creative-variable-invalid-type @v3-1 @creative-variable @ext-b
  Scenario: build_creative rejects CreativeVariable with invalid variable_type
    Given the request targets a production account
    And a source creative_manifest declaring a CreativeVariable with variable_type "geometry"
    And a valid target_format_id referencing an available creative format
    When the Buyer Agent sends a build_creative request
    Then the operation should fail with a schema validation error
    And the error code should be "CREATIVE_VALUE_NOT_ALLOWED"
    And the error should identify the invalid variable_type
    And the error should include "suggestion" field
    And the suggestion should contain "variable_type"
    # POST-F2: variable_type enum enforced (text|image|video|audio|url|number|boolean|color|date)
    # POST-F3: field path points at variable_type + recovery suggestion present

  @T-UC-020-creative-rejected-details @v3-1 @error-details @creative-rejected @ext-c
  Scenario: CREATIVE_REJECTED error returns policy reference and rejection reasons
    Given the request targets a production account
    And a creative_brief whose compliance.required_disclosures cannot be satisfied in the target format
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "CREATIVE_REJECTED"
    And the error details should include policy_id and a non-empty reasons array
    And the error details should include a policy_url where the full policy can be reviewed
    And the error should include "suggestion" field
    And the suggestion should contain "policy_url"
    # POST-F2: rejection rationale is structured, not free text
    # POST-F3: buyer knows what to revise via the suggestion + policy_url

  @T-UC-020-storyboard-build-vast-tag-from-synced-creative @storyboard-v3.1 @v3-1 @build-from-library @vast
  Scenario: Build a VAST-compatible serving tag from a synced video creative referenced by creative_id
    Given a video creative has been synced to the library with creative_id "video_30s_trail_pro"
    When the Buyer Agent sends build_creative referencing the creative_id and a target_format_id with id "vast_30s"
    Then the response should be schema-valid against build-creative-response.json
    And the response should carry a serving tag compatible with the VAST target_format_id
    And the response should reference the originating creative_id
    # creative_lifecycle build_video_tag: the buyer references an existing
    # creative_id from a prior sync_creatives call (not an inline creative_brief)
    # and asks the platform to build a serving tag in a target_format_id like
    # vast_30s. The platform produces a VAST-compatible tag the buyer can
    # traffic to ad servers. This anchors the post-sync build flow as distinct
    # from inline pure-generation scenarios.
    # build_video_tag: post-sync build by creative_id produces a target-format serving tag
