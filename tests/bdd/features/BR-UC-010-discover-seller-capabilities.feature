# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

Feature: BR-UC-010 Discover Seller Capabilities
  As a Buyer (Human or AI Agent)
  I want to discover what protocols, features, and targeting dimensions a Seller Agent supports
  So that I can evaluate the seller's capabilities before creating media buys or activating signals

  # Postconditions verified:
  #   POST-S1: Buyer knows which AdCP major versions the seller supports
  #   POST-S2: Buyer knows which domain protocols the seller supports
  #   POST-S3: Buyer knows the account requirements
  #   POST-S4: Buyer knows the media-buy feature flags (7 flags)
  #   POST-S5: Buyer knows the execution capabilities (targeting, creative specs)
  #   POST-S6: Buyer knows the seller's portfolio (publisher domains, channels, countries, policies)
  #   POST-S7: Buyer knows when capabilities were last updated
  #   POST-S8: Buyer knows which extension namespaces the seller supports
  #   POST-S9: Application context from the request is echoed unchanged in the response
  #   POST-S10: Buyer knows the supported pricing models across the seller's product portfolio
  #   POST-S11: DEPRECATED in v3.1 (media_buy.reporting subtree removed) — superseded by POST-S18
  #   POST-S12: Buyer knows the audience targeting capabilities when feature enabled
  #   POST-S13: Buyer knows the conversion tracking capabilities when feature enabled
  #   POST-S14: Buyer knows the creative protocol capabilities when creative protocol supported
  #   POST-S15: Buyer knows the adcp.idempotency capabilities
  #   POST-S16: Buyer knows the supported_versions
  #   POST-S17: Buyer knows the build_version
  #   POST-S18: Buyer knows the reporting_delivery_methods, offline_delivery_protocols, supports_proposals
  #   POST-S19: Buyer knows the content_standards capabilities
  #   POST-S20: Buyer knows the trusted_match capabilities
  #   POST-S21: Buyer knows the creative_specs capabilities
  #   POST-S22: Buyer knows the brand capabilities
  #   POST-S23: Buyer knows the request_signing capabilities
  #   POST-S24: Buyer knows the webhook_signing capabilities
  #   POST-S25: Buyer knows the identity capabilities
  #   POST-S26: Buyer knows the measurement capabilities
  #   POST-S27: Buyer knows the compliance_testing capabilities
  #   POST-S28: Buyer knows the specialisms and experimental_features
  #   POST-S29: Buyer knows advisory errors when present
  #   POST-F1: System state is unchanged on failure (read-only operation)
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Application context is still echoed when possible
  #   POST-F4: On unsupported version, seller returns VERSION_UNSUPPORTED carrying authoritative supported_versions[] for re-pin

  Background:
    Given a Seller Agent is operational and accepting requests


  @T-UC-010-main-mcp @main-flow @mcp @post-s1 @post-s2 @post-s3 @post-s4 @post-s5 @post-s6 @post-s7 @post-s8 @post-s10 @post-s18 @partition @boundary
  Scenario: not_provided — Not provided (no protocol filter), discover complete capabilities via MCP
    Given a tenant is resolvable from the request context
    And the tenant has an adapter with channels "display, social, ctv"
    And the tenant has registered publisher partnerships with domains "news.com", "sports.com"
    And the adapter provides targeting capabilities including geo and device
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the response should include adcp.major_versions containing 3
    And the response should include supported_protocols containing "media_buy"
    # RECONCILED with pinned spec v3.1.1 (salesagent-f2p3, mirror upstream in adcp-req):
    # core/media-buy-features.json declares exactly 4 properties — ALL OPTIONAL
    # ("Optional media-buy protocol features", no required array), so a seller omits
    # flags it does not declare. 'all 7 flags' came from the older v3.1-04f59d2d5 ref;
    # 'all 4 flags' over-specified presence the pin never mandates (caught the moment
    # the assert first executed — committed_metrics_supported is legitimately absent).
    And the response should include media_buy.features section with the declared feature flags
    And the response should include media_buy.execution section with targeting
    And the response should include media_buy.portfolio with publisher_domains "news.com", "sports.com"
    And the response should include media_buy.portfolio with primary_channels "display", "social", "ctv"
    And the response should include last_updated as a valid timestamp
    # RECONCILED ORDER (salesagent-f2p3, local edit — mirror upstream in adcp-req):
    # the three production-gap asserts are grouped LAST so the eight asserts above
    # run green today (pytest-bdd stops at the first failing step). All three are
    # the strict-xfail graduation triggers: supported_pricing_models and
    # reporting_delivery_methods are spec-optional sections production does not
    # emit yet (first executions of these asserts caught this — they were dormant
    # behind the account gap); account is the salesagent-oj0 gap.
    And the response should include media_buy.supported_pricing_models
    And the response should include media_buy.reporting_delivery_methods section
    And the response should include account section with sandbox flag and billing models
    # @bva protocols: Not provided (no protocol filter)
    # POST-S1: Buyer knows AdCP major versions
    # POST-S2: Buyer knows supported protocols
    # POST-S3: Buyer knows account requirements
    # POST-S4: Buyer knows media-buy feature flags
    # POST-S5: Buyer knows execution capabilities
    # POST-S6: Buyer knows portfolio
    # POST-S7: Buyer knows last_updated
    # POST-S8: Buyer knows extension namespaces
    # POST-S10: Buyer knows pricing models
    # POST-S18: Buyer knows reporting delivery methods
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-main-rest @main-flow @a2a @post-s1 @post-s2 @post-s3 @post-s4 @post-s5 @post-s6 @post-s7 @post-s8 @post-s10 @post-s18
  Scenario: Discover complete capabilities via A2A
    Given a tenant is resolvable from the request context
    And the tenant has an adapter with channels "display, social, ctv"
    And the tenant has registered publisher partnerships with domains "news.com", "sports.com"
    And the adapter provides targeting capabilities including geo and device
    And the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_adcp_capabilities skill request
    Then the response should include adcp.major_versions containing 3
    And the response should include supported_protocols containing "media_buy"
    # RECONCILED with pinned spec v3.1.1 (salesagent-f2p3, mirror upstream in adcp-req):
    # core/media-buy-features.json declares exactly 4 properties — ALL OPTIONAL
    # ("Optional media-buy protocol features", no required array), so a seller omits
    # flags it does not declare. 'all 7 flags' came from the older v3.1-04f59d2d5 ref;
    # 'all 4 flags' over-specified presence the pin never mandates (caught the moment
    # the assert first executed — committed_metrics_supported is legitimately absent).
    And the response should include media_buy.features section with the declared feature flags
    And the response should include media_buy.execution section with targeting
    And the response should include media_buy.portfolio with publisher_domains "news.com", "sports.com"
    And the response should include media_buy.portfolio with primary_channels "display", "social", "ctv"
    And the response should include last_updated as a valid timestamp
    # RECONCILED ORDER (salesagent-f2p3, local edit — mirror upstream in adcp-req):
    # the three production-gap asserts are grouped LAST so the eight asserts above
    # run green today (pytest-bdd stops at the first failing step). All three are
    # the strict-xfail graduation triggers: supported_pricing_models and
    # reporting_delivery_methods are spec-optional sections production does not
    # emit yet (first executions of these asserts caught this — they were dormant
    # behind the account gap); account is the salesagent-oj0 gap.
    And the response should include media_buy.supported_pricing_models
    And the response should include media_buy.reporting_delivery_methods section
    And the response should include account section with sandbox flag and billing models
    # POST-S1 through POST-S8, POST-S10, POST-S18 verified (same as MCP path)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-main-readonly @main-flow @post-f1
  Scenario: Capabilities discovery is read-only — no state change
    Given a tenant is resolvable from the request context
    And the system has known state before the request
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the system state should be unchanged after the response
    # POST-F1: System state is unchanged (read-only operation)

  @T-UC-010-main-timestamp @main-flow @post-s7
  Scenario: Capabilities response includes last_updated for cache invalidation
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the response should include last_updated as a valid ISO 8601 timestamp
    # POST-S7: Buyer knows when capabilities were last updated

  @T-UC-010-pricing @main-flow @post-s10
  Scenario: Capabilities response includes supported pricing models
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then media_buy.supported_pricing_models should be a non-empty array
    And each pricing model should be a valid pricing-model enum value
    # POST-S10: Buyer knows supported pricing models across seller's portfolio
    # POST-S10: Buyer knows the supported pricing models

  @T-UC-010-audience-caps @main-flow @post-s12
  Scenario: Capabilities response includes audience targeting capabilities when feature enabled
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    And features.audience_targeting is true
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then media_buy.audience_targeting should include supported_identifier_types as an array
    And media_buy.audience_targeting should include supported_uid_types as an array of uid-type enum values
    And media_buy.audience_targeting should include supports_platform_customer_id as a boolean
    And media_buy.audience_targeting should include minimum_audience_size as an integer
    And media_buy.audience_targeting should include matching_latency_hours as an object with min and max as integers
    # POST-S12: Buyer knows audience targeting capabilities
    # POST-S12: Buyer knows audience targeting capabilities

  @T-UC-010-conversion-caps @main-flow @post-s13
  Scenario: Capabilities response includes conversion tracking capabilities when feature enabled
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    And features.conversion_tracking is true
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then media_buy.conversion_tracking should include supported_event_types as an array of event-type enum values
    And media_buy.conversion_tracking should include supported_uid_types as an array of uid-type enum values
    And media_buy.conversion_tracking should include supported_hashed_identifiers as an array of [hashed_email, hashed_phone]
    And media_buy.conversion_tracking should include supported_action_sources as an array of action-source enum values
    And media_buy.conversion_tracking should include attribution_windows as an array of duration objects
    And media_buy.conversion_tracking should include multi_source_event_dedup as a boolean
    # POST-S13: Buyer knows conversion tracking capabilities
    # POST-S13: Buyer knows conversion tracking capabilities

  @T-UC-010-creative-caps @main-flow @post-s14
  Scenario: Capabilities response includes creative protocol when supported
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    And "creative" is in supported_protocols
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the response should include creative section
    And creative.supports_compliance should be a boolean
    # POST-S14: Buyer knows creative protocol capabilities
    # POST-S14: Buyer knows creative protocol capabilities

  @T-UC-010-auth @auth @invariant @partition @boundary @post-s1 @post-s2
  Scenario Outline: Authentication policy for capabilities discovery
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    And the Buyer has <token_state> authentication
    When the Buyer Agent invokes get_adcp_capabilities via <channel>
    Then the response should be <outcome>
    # BR-RULE-041: INV-1 (no token -> full data), INV-2 (valid token -> full data),
    # INV-3 (invalid MCP -> treated absent), INV-4 (auth irrelevant), INV-5 (invalid A2A -> error)

    Examples:
      | partition_boundary                    | token_state | channel | outcome            |
      | no_token no token                     | no          | MCP     | success            |
      | no_token no token                     | no          | A2A     | success            |
      | valid_token valid token               | valid       | MCP     | success            |
      | valid_token valid token               | valid       | A2A     | success            |
      | invalid_token_mcp invalid token (MCP) | invalid     | MCP     | success            |
      | invalid_token_a2a invalid token (A2A) | invalid     | A2A     | AUTH_REQUIRED      |

  @T-UC-010-auth-data-identity @auth @invariant
  Scenario: Authentication state does not affect response data content
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities MCP tool without authentication
    And the Buyer is authenticated with a valid principal_id
    Then both responses should contain identical capabilities data
    # BR-RULE-041 INV-4: Unauthenticated and authenticated callers receive identical data
    # INV-4: Response data identical regardless of auth state

  @T-UC-010-ext-a-mcp @extension @ext-a @degradation @mcp @partition @boundary
  Scenario: no_tenant — tenant absent, minimal capabilities via MCP
    Given no tenant can be resolved from the request context
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the response should include adcp.major_versions containing 3
    And the response should include adcp.supported_versions as a non-empty array
    And the response should include supported_protocols containing "media_buy"
    And the response should NOT include media_buy details
    And the response should NOT include account section
    And the response should NOT include media_buy.audience_targeting section
    And the response should NOT include media_buy.conversion_tracking section
    # BR-RULE-052 INV-1: No tenant -> minimal response
    # @bva capabilities_degradation: tenant absent
    # POST-S1: Buyer knows AdCP v3.1 version envelope (supported_versions + deprecated major_versions) (minimal)
    # POST-S2: Buyer knows media_buy protocol (minimal)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-ext-a-a2a @extension @ext-a @degradation @a2a @partition
  Scenario: no_tenant — minimal capabilities via A2A
    Given no tenant can be resolved from the request context
    When the Buyer Agent sends a get_adcp_capabilities skill request via A2A without token
    Then the response should include adcp.major_versions containing 3
    And the response should include adcp.supported_versions as a non-empty array
    And the response should include supported_protocols containing "media_buy"
    And the response should NOT include media_buy details
    # BR-RULE-052 INV-1: No tenant -> minimal response
    # POST-S1, POST-S2 (minimal; v3.1 version envelope = supported_versions + deprecated major_versions)

  @T-UC-010-ext-b-degradation @extension @ext-b @degradation @invariant @partition @boundary
  Scenario Outline: Graceful degradation when dependencies fail
    Given a tenant is resolvable from the request context
    And the adapter is in <adapter_state> state
    And the database is in <db_state> state
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the response should be a valid capabilities response
    And primary_channels should be <expected_channels>
    And publisher_domains should match <expected_domain>
    # BR-RULE-052: INV-1 through INV-5
    # @bva capabilities_degradation: tenant resolved, tenant absent, adapter succeeds,
    # adapter fails, DB succeeds, DB fails, adapter AND DB fail

    Examples:
      | partition_boundary                                         | adapter_state | db_state  | expected_channels | expected_domain  |
      | full_response tenant resolved adapter succeeds DB succeeds | available     | available | from_adapter      | from_db          |
      | adapter_fail adapter fails                                 | unavailable   | available | [display]         | from_db          |
      | db_fail DB fails                                           | available     | failure   | from_adapter      | placeholder      |
      | adapter_and_db_fail adapter AND DB fail                    | unavailable   | failure   | [display]         | placeholder      |
      | no_principal adapter fails                                 | no_principal  | available | [display]         | from_db          |
      | adapter_fail adapter fails DB succeeds                     | unavailable   | empty     | [display]         | from_db_or_empty |

  @T-UC-010-ext-b-schema-valid @extension @ext-b @degradation @invariant
  Scenario: Degraded response is always schema-valid
    Given a tenant is resolvable from the request context
    And the adapter is unavailable
    And the database query fails
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the response should pass schema validation for get-adcp-capabilities-response
    And no error should be propagated to the caller
    And degradation warnings should be logged internally
    # BR-RULE-052 INV-5: Response remains schema-valid; failure logged, not propagated

  @T-UC-010-degradation-account @extension @degradation @partition @boundary @post-s3
  Scenario Outline: Account section presence depends on tenant resolution
    Given <tenant_condition>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the account section should be <account_state>
    # BR-RULE-052: account section absent when tenant unavailable, present when tenant resolved

    Examples:
      | partition_boundary                                          | tenant_condition                                   | account_state |
      | no_tenant no tenant → account section absent                | no tenant can be resolved from the request context | absent        |
      | full_response tenant resolved → account section present     | a tenant is resolvable from the request context    | present       |
      | account_degraded                                            | a tenant is resolvable with partial account config | partial       |

  @T-UC-010-degradation-sections @extension @degradation @partition @boundary
  Scenario Outline: Adapter-dependent sections absent when adapter fails
    Given a tenant is resolvable from the request context
    And the adapter is in <adapter_state> state
    And features.<feature_flag> is <flag_value>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then media_buy.<section> should be <section_state>
    # BR-RULE-052: reporting, audience_targeting, conversion_tracking sections are adapter-dependent

    Examples:
      | partition_boundary                                                                                          | adapter_state | feature_flag       | flag_value | section              | section_state |
      | audience_targeting_absent adapter fails → audience_targeting section absent                                   | unavailable   | audience_targeting  | true       | audience_targeting   | absent        |
      | full_response features.audience_targeting=true AND adapter succeeds → audience_targeting section present      | available     | audience_targeting  | true       | audience_targeting   | present       |
      | audience_targeting_absent features.audience_targeting=false → audience_targeting section absent               | available     | audience_targeting  | false      | audience_targeting   | absent        |
      | conversion_tracking_absent adapter fails → conversion_tracking section absent                                | unavailable   | conversion_tracking | true       | conversion_tracking  | absent        |
      | full_response features.conversion_tracking=true AND adapter succeeds → conversion_tracking section present   | available     | conversion_tracking | true       | conversion_tracking  | present       |
      | conversion_tracking_absent features.conversion_tracking=false → conversion_tracking section absent           | available     | conversion_tracking | false      | conversion_tracking  | absent        |

  @T-UC-010-degradation-creative @extension @degradation @partition @boundary @post-s14
  Scenario Outline: Creative section presence depends on protocol support
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    And <creative_condition>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the creative section should be <creative_state>
    # BR-RULE-052: creative section absent when creative not in supported_protocols

    Examples:
      | partition_boundary                                                             | creative_condition                        | creative_state |
      | creative_absent creative not in supported_protocols → creative section absent  | "creative" is NOT in supported_protocols  | absent         |
      | full_response creative in supported_protocols → creative section present       | "creative" is in supported_protocols      | present        |

  @T-UC-010-ext-c-a2a @extension @ext-c @error @a2a @post-f1 @post-f2 @post-f3
  Scenario: A2A request with invalid auth token — error returned
    Given a tenant is resolvable from the request context
    And the Buyer has an invalid authentication token
    When the Buyer Agent sends a get_adcp_capabilities skill request via A2A with the token
    Then the response should be an authentication error
    And the error code should be "AUTH_REQUIRED"
    And the error message should reference authentication or token validation
    And the error should include "suggestion" field when possible
    # BR-RULE-041 INV-5: A2A requires valid token if one is provided
    # Error code: AUTH_REQUIRED
    # POST-F2: Buyer knows what failed and the error code
    # POST-F1: No state change (read-only)
    # POST-F3: Suggestion included when possible (A2A ServerError may not support it)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-ext-c-mcp @extension @ext-c @auth @mcp @degradation
  Scenario: MCP request with invalid auth token — silently ignored
    Given a tenant is resolvable from the request context
    And the Buyer has an invalid authentication token
    When the Buyer Agent calls get_adcp_capabilities MCP tool with the token
    Then the response should be a success with capabilities
    And the request should proceed without principal context
    # BR-RULE-041 INV-3: MCP treats invalid token as absent
    # Degraded capabilities may result (no principal -> no adapter)

  @T-UC-010-ext-d-filter @extension @ext-d @known-gap @boundary @partition
  Scenario: media_buy (first enum value) — protocol filter provided but ignored
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities MCP tool with protocols filter ["media_buy"]
    Then all protocol sections should be returned regardless of filter
    And no error should be raised about the filter
    # ext-d: Implementation creates GetAdcpCapabilitiesRequest() ignoring params
    # @bva protocols: media_buy (first enum value)
    # @known-gap: Schema defines protocols filter but implementation ignores it

  @T-UC-010-ext-d-all-protocols @extension @ext-d @known-gap @boundary @partition
  Scenario: signals governance sponsored_intelligence creative (last enum value) — all protocols in filter, still ignored
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities MCP tool with protocols filter ["media_buy", "signals", "governance", "sponsored_intelligence", "creative"]
    Then all protocol sections should be returned
    # @bva protocols: creative (last enum value)
    # @known-gap: Filter with all values behaves same as no filter

  @T-UC-010-ext-d-invalid-value @extension @ext-d @known-gap @boundary @partition
  Scenario: unknown_protocol — filter with invalid enum value, Unknown string not in enum
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities MCP tool with protocols filter ["marketing"]
    Then all protocol sections should be returned
    And no error should be raised about the invalid protocol value
    # Implementation ignores all params including invalid ones
    # @bva protocols: Unknown string not in enum
    # @known-gap: Invalid filter value silently ignored

  @T-UC-010-ext-d-empty @extension @ext-d @known-gap @boundary @partition
  Scenario: empty_array — protocol filter with empty array, silently accepted
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities MCP tool with protocols filter []
    Then all protocol sections should be returned
    # Schema requires minItems: 1 but implementation ignores params entirely
    # @bva protocols: Empty array
    # @known-gap: Empty array violates schema minItems but implementation ignores filter

  @T-UC-010-ext-e-mcp @context @mcp @post-s9 @invariant @partition @boundary
  Scenario: context_provided — context echoed in capabilities response via MCP
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities MCP tool with context {"session_id": "abc-123", "trace": "xyz-789"}
    Then the response context should equal {"session_id": "abc-123", "trace": "xyz-789"}
    # BR-RULE-043 INV-1: Request includes context -> response includes identical context
    # POST-S9: Application context echoed unchanged

  @T-UC-010-ext-e-a2a @context @a2a @post-s9 @invariant @partition @boundary
  Scenario: context_provided — context echoed via A2A, context with properties
    Given a tenant is resolvable from the request context
    And the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_adcp_capabilities skill request via A2A with context {"session_id": "abc-123"}
    Then the response context should equal {"session_id": "abc-123"}
    # BR-RULE-043 INV-1: Request includes context -> response includes identical context
    # POST-S9: Application context echoed unchanged

  @T-UC-010-ext-e-absent @context @invariant @partition @boundary
  Scenario: context_absent — context absent, no context in request means no context in response
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities MCP tool without context
    Then the response should not contain a context field
    # BR-RULE-043 INV-2: Request omits context -> response omits context
    # INV-2: Absence echoed as absence

  @T-UC-010-ext-e-nested @context @invariant @boundary @partition
  Scenario: context_nested — deeply nested context object echoed unchanged
    Given a tenant is resolvable from the request context
    When the Buyer Agent calls get_adcp_capabilities MCP tool with context {"deep": {"nested": {"level": 3, "data": true}}}
    Then the response context should equal {"deep": {"nested": {"level": 3, "data": true}}}
    # BR-RULE-043 INV-1: Context is opaque — echoed regardless of structure
    # Context is opaque — never parsed, modified, or validated

  @T-UC-010-ext-e-empty @context @invariant @boundary @partition
  Scenario: context_empty_object — empty context echoed, context = {}
    Given a tenant is resolvable from the request context
    When the Buyer Agent calls get_adcp_capabilities MCP tool with context {}
    Then the response context should equal {}
    # BR-RULE-043 INV-1: Even empty object is echoed
    # Empty context is a valid context — echoed as empty object

  @T-UC-010-ext-e-gap @context @known-gap
  Scenario: Context echo implementation gap in capabilities endpoint
    Given a tenant is resolvable from the request context
    When the Buyer Agent calls get_adcp_capabilities MCP tool with context {"tracking": "id-456"}
    Then context SHOULD be echoed per schema specification
    # BR-RULE-043 INV-1 — KNOWN GAP
    # capabilities.py line 271: req = GetAdcpCapabilitiesRequest() ignores request params
    # Schema supports context echo but implementation does not copy it
    # @known-gap: Implementation does not echo context (unlike properties.py and performance.py)

  @T-UC-010-channel-mapping @channel @invariant @partition @boundary
  Scenario Outline: Channel name resolution from adapter to MediaChannel enum
    Given a tenant is resolvable from the request context
    And the adapter reports channels <adapter_channels>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then primary_channels should be <expected_result>
    # BR-RULE-053: INV-1 (canonical), INV-2 (alias), INV-3 (unrecognized dropped)
    # @bva primary_channels: 0 channels from adapter, 1 recognized channel,
    # 1 unrecognized channel, alias 'video', alias 'audio'

    Examples:
      | partition_boundary                           | adapter_channels       | expected_result      |
      | canonical_channels                           | display, social, ctv   | display, social, ctv |
      | aliased_channels alias 'video' alias 'audio' | video, audio           | olv, streaming_audio |
      | mixed_channels                               | display, video, social | display, olv, social |
      | single_channel 1 recognized channel          | ctv                    | ctv                  |
      | unrecognized_only 1 unrecognized channel     | hologram               | display              |
      | mixed_valid_invalid                          | display, hologram      | display              |
      | canonical_channels 0 channels from adapter   |                        | display              |

  @T-UC-010-channel-all-canonical @channel @boundary
  Scenario: All 18 canonical MediaChannel values are valid
    Given a tenant is resolvable from the request context
    And the adapter reports all 18 MediaChannel enum values
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then primary_channels should contain all 18 canonical values
    # BR-RULE-053 INV-1: All canonical names included
    # Canonical values: display, olv, social, search, ctv, linear_tv, radio,
    # streaming_audio, podcast, dooh, ooh, print, cinema, email, gaming,
    # retail_media, influencer, affiliate, product_placement

  @T-UC-010-features @validation @post-s4
  Scenario: Capabilities response includes all 7 media-buy feature flags
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then media_buy.features should include inline_creative_management as a boolean
    And media_buy.features should include property_list_filtering as a boolean
    And media_buy.features should include content_standards as a boolean
    And media_buy.features should include conversion_tracking as a boolean
    And media_buy.features should include audience_targeting as a boolean
    And media_buy.features should include catalog_management as a boolean
    And media_buy.features should include sandbox as a boolean
    # POST-S4: Buyer knows feature flags (7 total)
    # POST-S4: All 7 feature flags verified

  @T-UC-010-features-partitions @partition @boundary @features @post-s4
  Scenario Outline: Feature flag configurations - <partition>
    Given a tenant is resolvable from the request context
    And the tenant features are configured as <feature_config>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then media_buy.features should match <expected_behavior>
    # capabilities_features.yaml partitions and boundaries

    Examples:
      | partition                                                    | feature_config                                                                                                                         | expected_behavior                                  |
      | mixed_typical feature flag true (declaration-means-honor)    | inline_creative_management=true, content_standards=false, property_list_filtering=false, audience_targeting=false, catalog_management=false, sandbox=false | some true some false                               |
      | all_true all 7 named flags present                           | all 7 flags true                                                                                                                       | all true, all dependent sections present            |
      | all_false feature flag false (must not expose)               | all 7 flags false                                                                                                                      | all false, no dependent sections                    |
      | with_additional_properties additional boolean property included | inline_creative_management=true, custom_reporting=true                                                                                 | named flags plus custom flag accepted               |
      | conversion_tracking_enabled conversion_tracking=true (triggers conversion section) | conversion_tracking=true                                                                                                               | conversion_tracking section populated in response   |
      | audience_targeting_enabled audience_targeting=true (triggers audience_targeting section) | audience_targeting=true                                                                                                                | audience_targeting section populated in response    |
      | catalog_management_enabled catalog_management=true (enables sync_catalogs) | catalog_management=true                                                                                                                | catalog_management flag true                        |
      | sandbox_enabled sandbox=true (sandbox mode available)        | sandbox=true                                                                                                                           | sandbox flag true                                   |

  @T-UC-010-features-boundaries @boundary @features @post-s4
  Scenario Outline: Feature flag boundary - <boundary_point>
    Given a tenant is resolvable from the request context
    And the tenant features are configured for <boundary_point>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the response should reflect the <expected> boundary condition
    # capabilities_features.yaml boundary conditions

    Examples:
      | boundary_point                                         | expected |
      | conversion_tracking=false (no conversion section)      | valid    |
      | audience_targeting=false (no audience_targeting section) | valid    |
      | catalog_management=false (sync_catalogs unavailable)   | valid    |
      | sandbox=false (no sandbox mode)                        | valid    |

  @T-UC-010-targeting @validation @post-s5
  Scenario: Capabilities response includes all targeting dimensions
    Given a tenant is resolvable from the request context
    And the adapter provides full targeting capabilities
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then media_buy.execution.targeting should include geo section
    And targeting.geo should include countries, regions, metros, postal_areas flags
    And media_buy.execution.targeting should include age_restriction section
    And media_buy.execution.targeting should include language flag
    And media_buy.execution.targeting should include keyword_targets section
    And media_buy.execution.targeting should include negative_keywords section
    And media_buy.execution.targeting should include geo_proximity section
    # POST-S5: Buyer knows execution capabilities
    # POST-S5: Targeting capabilities verified (v3.1 removed device_platform, device_type, audience_include, audience_exclude)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-targeting-partitions @partition @boundary @targeting @post-s5
  Scenario Outline: Targeting capability configurations - <partition>
    Given a tenant is resolvable from the request context
    And the adapter provides targeting as <targeting_config>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then media_buy.execution.targeting should reflect <expected_targeting>
    # capabilities_targeting.yaml partitions and boundaries

    Examples:
      | partition                                                                         | targeting_config                                                                                                 | expected_targeting                                    |
      | full_adapter adapter available with full capabilities                              | all dimensions reported                                                                                          | all targeting dimensions present                      |
      | adapter_unavailable_defaults adapter unavailable (defaults apply)                  | adapter unavailable                                                                                              | geo_countries=true, geo_regions=true only              |
      | partial_dimensions                                                                | geo_countries=true, geo_regions=false, age_restriction.supported=true                                            | partial dimensions as reported                         |
      | nested_populated geo_metros threshold: any sub-property true -> object present     | geo_metros.nielsen_dma=true, geo_postal_areas.us_zip=true                                                        | nested objects present                                 |
      | nested_absent geo_metros threshold: all sub-properties false -> object absent      | no nested sub-properties true                                                                                    | nested objects absent                                  |
      | age_restriction_supported age_restriction with verification methods               | age_restriction.supported=true, verification_methods=[id_document]                                             | age_restriction section present                        |
      | keyword_targeting keyword_targets with supported_match_types [broad, phrase, exact] | keyword_targets.supported_match_types=[broad,phrase,exact], negative_keywords.supported_match_types=[exact]       | keyword targeting with match types                     |
      | geo_proximity_supported geo_proximity.radius=true (simple circle targeting)        | geo_proximity.radius=true, travel_time=true, geometry=false, transport_modes=[driving,walking]                   | proximity targeting available                          |
      | postal_areas_extended geo_postal_areas with ch_plz (Swiss 4-digit)                 | geo_postal_areas.de_plz=true, ch_plz=true, at_plz=true                                                          | extended postal areas including ch_plz and at_plz      |

  @T-UC-010-targeting-boundaries @boundary @targeting @post-s5
  Scenario Outline: Targeting dimension boundary - <boundary_point>
    Given a tenant is resolvable from the request context
    And the adapter provides targeting for <boundary_point>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then targeting should reflect the expected boundary behavior
    # capabilities_targeting.yaml boundary conditions

    Examples:
      | boundary_point                                                            |
      | geo_postal_areas threshold: any sub-property true -> object present       |
      | geo_postal_areas threshold: all sub-properties false -> object absent     |
      | geo_postal_areas with at_plz (Austrian 4-digit)                           |
      | negative_keywords with supported_match_types [broad, phrase, exact]        |
      | geo_proximity.travel_time=true with transport_modes                       |
      | geo_proximity.geometry=true (buyer-provided GeoJSON)                      |

  @T-UC-010-degradation-partitions @partition @boundary @degradation
  Scenario Outline: Degradation path - <partition>
    Given <precondition>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the response should reflect <expected_degradation>
    # capabilities_degradation.yaml partitions and boundaries

    Examples:
      | partition                                           | precondition                                                                       | expected_degradation                                                |
      | full_response                                       | a tenant is resolvable and adapter and DB are available with all features           | complete capabilities including all sections                         |
      | no_tenant                                           | no tenant can be resolved from the request context                                  | minimal: adcp v3.1 version envelope (supported_versions + deprecated major_versions), protocols=[media_buy] only |
      | adapter_fail                                        | a tenant is resolvable but adapter is unavailable                                   | channels=[display], default targeting, no reporting/audience/conversion |
      | db_fail                                             | a tenant is resolvable but database query fails                                     | placeholder domain, other sections unaffected                        |
      | adapter_and_db_fail                                 | a tenant is resolvable but both adapter and DB fail                                 | combined defaults, adapter-dependent sections absent                 |
      | no_principal                                        | a tenant is resolvable but no auth principal available                              | similar to adapter_fail (no principal -> no adapter)                 |
      | account_degraded                                    | a tenant is resolvable with partial account config                                  | account present but partially populated                              |
      | audience_targeting_absent                           | a tenant is resolvable but adapter unavailable or audience_targeting=false           | audience_targeting section absent                                    |
      | conversion_tracking_absent                          | a tenant is resolvable but adapter unavailable or conversion_tracking=false          | conversion_tracking section absent                                   |
      | creative_absent                                     | a tenant is resolvable but creative not in supported_protocols                      | creative section absent                                              |

  @T-UC-010-v31-supported-versions @v31 @main-flow @post-s16 @partition @boundary
  Scenario: supported-versions — release-precision supported_versions emitted alongside deprecated major_versions
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the response should include adcp.major_versions containing 3
    And the response should include adcp.supported_versions as a non-empty array
    And each value in adcp.supported_versions should match pattern "^\d+\.\d+(-[a-zA-Z0-9.-]+)?$"
    # v3.1: adcp.supported_versions (release-precision strings like "3.0", "3.1", "3.1-beta")
    # v3.1: adcp.major_versions remains for 3.x backwards compatibility (removed in 4.0)
    # POST-S16: Buyer knows release-precision AdCP versions for pinning
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-build-version @v31 @main-flow @post-s17 @boundary
  Scenario: build-version — optional advisory build_version is semver and not used for negotiation
    Given a tenant is resolvable from the request context
    And the tenant declares adcp.build_version "3.1.2+scope3.deploy.4821"
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then adcp.build_version should match pattern "^\d+\.\d+\.\d+(-[a-zA-Z0-9.-]+)?(\+[a-zA-Z0-9.-]+)?$"
    And buyers should not use adcp.build_version for version negotiation
    # v3.1: adcp.build_version is advisory only; buyers MUST NOT use for negotiation
    # POST-S17: build_version surfaced for incident triage
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-idempotency-supported @v31 @main-flow @post-s15 @partition @boundary
  Scenario Outline: idempotency-supported — IdempotencySupported discriminated union shape
    Given a tenant is resolvable from the request context
    And the tenant declares idempotency posture <posture>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then adcp.idempotency.supported should equal <supported>
    And the response should include the discriminator-required fields for <posture>
    # v3.1: adcp.idempotency is REQUIRED (no default; sellers without it are non-compliant)
    # @bva idempotency: supported=true / supported=false / replay_ttl_seconds boundary
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

    Examples:
      | partition_boundary                                                                | posture                                            | supported |
      | idempotency_supported supported=true with minimum replay_ttl_seconds=3600         | supported=true replay_ttl_seconds=3600             | true      |
      | idempotency_supported supported=true with maximum replay_ttl_seconds=604800       | supported=true replay_ttl_seconds=604800           | true      |
      | idempotency_supported supported=true with in_flight_max_seconds and opaque ids    | supported=true replay_ttl_seconds=86400 in_flight_max_seconds=300 account_id_is_opaque=true | true      |
      | idempotency_unsupported supported=false (no replay_ttl_seconds, no in_flight_max) | supported=false                                    | false     |

  @T-UC-010-v31-idempotency-required @v31 @invariant @post-s15
  Scenario: idempotency-required — adcp.idempotency is REQUIRED in v3.1 response
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then adcp.idempotency should be present in the response
    And adcp.idempotency.supported should be a boolean discriminator
    # v3.1: Sellers without idempotency declaration are non-compliant and unsafe for retries
    # POST-S15: Buyer knows the idempotency posture (required for safe retry on mutating requests)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-idempotency-in-flight-bound @v31 @invariant @boundary
  Scenario: idempotency-in-flight-bound — in_flight_max_seconds must not exceed replay_ttl_seconds
    Given a tenant is resolvable from the request context
    And the tenant declares idempotency posture supported=true replay_ttl_seconds=86400 in_flight_max_seconds=86400
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then adcp.idempotency.in_flight_max_seconds should be less than or equal to adcp.idempotency.replay_ttl_seconds
    # v3.1 PRE-BIZ7: in_flight_max_seconds <= replay_ttl_seconds (validators enforce cross-field)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-supported-protocols-extended @v31 @main-flow @post-s2 @partition @boundary
  Scenario: supported-protocols-extended — v3.1 enum extends supported_protocols to seven values
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then each value in supported_protocols should be one of "media_buy", "signals", "governance", "sponsored_intelligence", "creative", "brand", "measurement"
    # v3.1: enum adds "brand" and "measurement"
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-supports-proposals @v31 @main-flow @post-s18 @partition @boundary
  Scenario Outline: supports-proposals — proposal lifecycle commitment flag (v3.1)
    Given a tenant is resolvable from the request context
    And the tenant declares <supports_proposals_state>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then media_buy.supports_proposals should equal <expected>
    # v3.1: media_buy.supports_proposals — when true, seller is graded against proposal-lifecycle storyboards
    # POST-S18: Buyer knows proposal-lifecycle support + reporting_delivery_methods / offline_delivery_protocols
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

    Examples:
      | partition_boundary                                          | supports_proposals_state            | expected |
      | proposals_supported guaranteed-deal seller declares true    | media_buy.supports_proposals=true   | true     |
      | proposals_unsupported auction-based PG declares false       | media_buy.supports_proposals=false  | false    |
      | proposals_default omitted (default false)                   | media_buy.supports_proposals omitted | false    |

  @T-UC-010-v31-reporting-delivery-methods @v31 @main-flow @post-s18 @partition @boundary
  Scenario Outline: reporting-delivery-methods — push-based delivery beyond baseline polling
    Given a tenant is resolvable from the request context
    And the tenant declares reporting delivery methods <methods> with offline protocols <protocols>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then media_buy.reporting_delivery_methods should equal <methods>
    And media_buy.offline_delivery_protocols should reflect <protocols>
    # v3.1: media_buy.reporting_delivery_methods enum [webhook, offline]; offline_delivery_protocols only meaningful when "offline" present
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

    Examples:
      | partition_boundary                                            | methods              | protocols             |
      | polling_only methods omitted (baseline polling only)          | omitted              | omitted               |
      | webhook_only methods=[webhook]                                 | [webhook]            | omitted               |
      | offline_only methods=[offline] with s3 protocol                | [offline]            | [s3]                  |
      | mixed_delivery methods=[webhook, offline] with gcs protocol   | [webhook, offline]   | [gcs]                 |

  @T-UC-010-v31-content-standards-block @v31 @main-flow @post-s19
  Scenario: content-standards-block — media_buy.content_standards capability declares evaluation surface
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    And features.content_standards is true
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then media_buy.content_standards should include supports_local_evaluation as a boolean
    And media_buy.content_standards should include supported_channels as an array of channels enum values
    And media_buy.content_standards should include supports_webhook_delivery as a boolean
    # v3.1: media_buy.content_standards block (supports_local_evaluation, supported_channels, supports_webhook_delivery)
    # POST-S19: Buyer knows content_standards capability surface
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-trusted-match-surfaces @v31 @main-flow @post-s20 @partition @boundary
  Scenario Outline: trusted-match-surfaces — TMP surfaces declaration (v3.1)
    Given a tenant is resolvable from the request context
    And the tenant declares trusted_match surfaces <surfaces>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then media_buy.execution.trusted_match.surfaces should equal <surfaces>
    And each surface should be one of "website", "mobile_app", "ctv_app", "desktop_app", "dooh", "podcast", "radio", "streaming_audio", "ai_assistant"
    # v3.1: media_buy.execution.trusted_match.surfaces (experimental); axe_integrations DEPRECATED
    # POST-S20: Buyer knows the trusted_match TMP surfaces (and deprecated axe_integrations)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

    Examples:
      | partition_boundary                                      | surfaces                                       |
      | tmp_web_only website surface only                       | [website]                                      |
      | tmp_app_surfaces mobile_app and ctv_app                 | [mobile_app, ctv_app]                          |
      | tmp_audio podcast and streaming_audio surfaces          | [podcast, streaming_audio]                     |
      | tmp_ai_assistant ai_assistant surface                   | [ai_assistant]                                 |
      | tmp_full_coverage all nine TMP surfaces                  | [website, mobile_app, ctv_app, desktop_app, dooh, podcast, radio, streaming_audio, ai_assistant] |

  @T-UC-010-v31-axe-integrations-deprecated @v31 @main-flow @known-gap @post-s20
  Scenario: axe-integrations-deprecated — legacy axe_integrations retained for backwards compatibility
    Given a tenant is resolvable from the request context
    And the tenant has legacy axe_integrations declared
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then media_buy.execution.axe_integrations should be an array of URIs
    And media_buy.execution.axe_integrations is treated as deprecated in v3.1
    # v3.1 BR-15: axe_integrations is DEPRECATED; sellers SHOULD declare TMP via trusted_match.surfaces instead
    # @known-gap: axe_integrations remains schema-valid through 3.x; new integrations use trusted_match
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-creative-specs @v31 @main-flow @post-s21
  Scenario: creative-specs — VAST/MRAID/VPAID/SIMID creative specification support
    Given a tenant is resolvable from the request context
    And the tenant declares creative_specs vast_versions=["4.2"] mraid_versions=["3.0"] vpaid=true simid=true
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then media_buy.execution.creative_specs.vast_versions should match pattern "^[0-9]+\.[0-9]+$"
    And media_buy.execution.creative_specs.mraid_versions should match pattern "^[0-9]+\.[0-9]+$"
    And media_buy.execution.creative_specs.vpaid should be a boolean
    And media_buy.execution.creative_specs.simid should be a boolean
    # v3.1: media_buy.execution.creative_specs (vast_versions, mraid_versions, vpaid, simid)
    # POST-S21: Buyer knows creative specification capabilities
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-brand-block @v31 @main-flow @post-s22 @partition @boundary
  Scenario: brand-block — brand protocol capabilities (experimental in v3.1)
    Given a tenant is resolvable from the request context
    And "brand" is in supported_protocols
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the response should include brand section
    And brand.rights should be a boolean
    And brand.right_types should be an array of right-type enum values
    And brand.available_uses should be an array of right-use enum values
    And brand.generation_providers should be an array of provider names
    # v3.1: brand top-level block (rights, right_types, available_uses, generation_providers, description)
    # POST-S22: Buyer knows brand protocol capabilities
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-request-signing-posture @v31 @main-flow @post-s23 @partition @boundary
  Scenario Outline: request-signing-posture — RFC 9421 request signing declaration
    Given a tenant is resolvable from the request context
    And the tenant declares request_signing posture <posture>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then request_signing.supported should equal <supported>
    And request_signing.covers_content_digest should be one of "required", "forbidden", "either"
    # v3.1: request_signing top-level block (supported, covers_content_digest, required_for, warn_for, supported_for, protocol_methods_*)
    # POST-S23: Buyer knows the request_signing posture (RFC 9421 covers_content_digest + tool/method namespaces)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

    Examples:
      | partition_boundary                                                                  | posture                                                       | supported |
      | signing_supported_either supported=true covers_content_digest=either                | supported=true covers_content_digest=either                   | true      |
      | signing_required_covers_digest supported=true covers_content_digest=required        | supported=true covers_content_digest=required                 | true      |
      | signing_forbidden_digest supported=true covers_content_digest=forbidden              | supported=true covers_content_digest=forbidden                | true      |
      | signing_unsupported supported=false (no required_for, no protocol_methods_*)        | supported=false                                               | false     |

  @T-UC-010-v31-request-signing-namespace-split @v31 @invariant @boundary
  Scenario: request-signing-namespace-split — AdCP tool names vs JSON-RPC method names live in separate buckets
    Given a tenant is resolvable from the request context
    And the tenant declares request_signing.required_for=["create_media_buy"] and request_signing.protocol_methods_required_for=["tasks/cancel"]
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then request_signing.required_for should contain only AdCP tool names without "/"
    And request_signing.protocol_methods_required_for should match pattern "^[a-z][a-z0-9_]*/[a-z][a-z0-9_]*$"
    # v3.1 BR-12: required_for / warn_for / supported_for carry AdCP tool names only;
    # JSON-RPC method names (containing "/") belong in protocol_methods_*
    # BR-12: Namespaces MUST NOT be conflated
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-request-signing-subset @v31 @invariant @boundary
  Scenario: request-signing-subset — required_for and warn_for must be subset of supported_for
    Given a tenant is resolvable from the request context
    And the tenant declares request_signing.supported_for=["create_media_buy", "update_media_buy"] required_for=["create_media_buy"] warn_for=["update_media_buy"]
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then request_signing.required_for should be a subset of request_signing.supported_for
    And request_signing.warn_for should be a subset of request_signing.supported_for
    And request_signing.warn_for should be disjoint from request_signing.required_for
    # v3.1 PRE-BIZ6: x-adcp-validation enforces required_for ⊆ supported_for AND warn_for ⊆ supported_for AND warn_for ∩ required_for = ∅
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-webhook-signing @v31 @main-flow @post-s24 @partition @boundary
  Scenario Outline: webhook-signing — RFC 9421 webhook signing posture
    Given a tenant is resolvable from the request context
    And the tenant declares webhook_signing posture <posture>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then webhook_signing.supported should equal <supported>
    And webhook_signing.profile should be "adcp/webhook-signing/v1" when present
    And each algorithm should be one of "ed25519", "ecdsa-p256-sha256"
    # v3.1: webhook_signing top-level block (supported, profile, algorithms, legacy_hmac_fallback)
    # POST-S24: Buyer knows the webhook_signing posture for outbound webhook signature verification
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

    Examples:
      | partition_boundary                                                                | posture                                                                       | supported |
      | webhook_signing_ed25519 supported=true ed25519                                     | supported=true profile=adcp/webhook-signing/v1 algorithms=[ed25519]            | true      |
      | webhook_signing_ecdsa supported=true ecdsa-p256-sha256                             | supported=true profile=adcp/webhook-signing/v1 algorithms=[ecdsa-p256-sha256]  | true      |
      | webhook_signing_legacy_fallback supported=true with legacy_hmac_fallback           | supported=true profile=adcp/webhook-signing/v1 algorithms=[ed25519] legacy_hmac_fallback=true | true |
      | webhook_signing_unsupported supported=false (no profile, no algorithms)            | supported=false                                                                | false     |

  @T-UC-010-v31-webhook-signing-required-when @v31 @invariant @boundary
  Scenario Outline: webhook-signing-required-when — mutating-webhook emission requires signed webhooks
    Given a tenant is resolvable from the request context
    And the tenant declares <emission_state>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then webhook_signing.supported should equal <required>
    # v3.1 PRE-BIZ4: webhook_signing.supported MUST be true when reporting_delivery_methods contains "webhook"
    # OR content_standards.supports_webhook_delivery is true
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

    Examples:
      | partition_boundary                                                       | emission_state                                                                    | required |
      | reporting_webhook_emission reporting_delivery_methods contains "webhook" | media_buy.reporting_delivery_methods=[webhook]                                    | true     |
      | content_standards_webhook supports_webhook_delivery=true                  | media_buy.content_standards.supports_webhook_delivery=true                        | true     |
      | no_mutating_webhooks neither emission path declared                       | no mutating-webhook emission                                                      | false    |

  @T-UC-010-v31-identity-brand-json-url @v31 @main-flow @post-s25 @partition @boundary
  Scenario: identity-brand-json-url — trust-root pointer required when any signing posture declared
    Given a tenant is resolvable from the request context
    And the tenant declares request_signing.supported_for=["create_media_buy"]
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then identity.brand_json_url should be present
    And identity.brand_json_url should match pattern "^https://"
    And identity.brand_json_url should be distinct from sponsored_intelligence.brand_url
    # v3.1 PRE-BIZ5: identity.brand_json_url REQUIRED when any signing posture declared (storyboard 3.x; schema-required 4.0)
    # v3.1 BR-13: identity.brand_json_url is DISTINCT from sponsored_intelligence.brand_url (rendering pointer)
    # POST-S25: Trust-root pointer for signing-key discovery
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-identity-key-origins @v31 @main-flow @post-s25 @partition
  Scenario Outline: identity-key-origins — JWKS origin separation per signing purpose
    Given a tenant is resolvable from the request context
    And the tenant declares identity.key_origins for <purpose> at <origin>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then identity.key_origins.<purpose> should equal <origin>
    And the purpose should have a corresponding signing posture declared elsewhere
    # v3.1: identity.key_origins maps purpose → origin (governance_signing, request_signing, webhook_signing, tmp_signing)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

    Examples:
      | partition_boundary                                                       | purpose             | origin                              |
      | key_origin_governance governance_signing origin                          | governance_signing  | https://governance.seller.example   |
      | key_origin_request request_signing origin                                | request_signing     | https://signing.seller.example      |
      | key_origin_webhook webhook_signing origin                                | webhook_signing     | https://webhooks.seller.example     |
      | key_origin_tmp tmp_signing origin (TMP participant only)                 | tmp_signing         | https://tmp.seller.example          |

  @T-UC-010-v31-identity-compromise-notification @v31 @main-flow @post-s25
  Scenario: identity-compromise-notification — emits/accepts flags for compromise webhook
    Given a tenant is resolvable from the request context
    And the tenant declares identity.compromise_notification.emits=true accepts=true
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then identity.compromise_notification.emits should be a boolean
    And identity.compromise_notification.accepts should be a boolean
    # v3.1: identity.compromise_notification {emits, accepts} for identity.compromise_notification webhook subscription
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-identity-required-when-signing @v31 @invariant @boundary @post-s25
  Scenario Outline: identity-required-when-signing — signing posture without brand_json_url is rejected
    Given a tenant is resolvable from the request context
    And the tenant declares <signing_posture> with identity block <identity_state>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the response should be <verdict>
    # BR-RULE-234 INV-5: signing posture declared but identity absent or empty {} → response rejected as missing brand_json_url
    # BR-RULE-234 INV-2: no signing posture → identity MAY be absent
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

    Examples:
      | partition_boundary                                                  | signing_posture                                  | identity_state | verdict                                     |
      | posture_declared_identity_absent signing declared, identity missing | request_signing.supported_for=[create_media_buy] | absent         | rejected as missing identity.brand_json_url |
      | posture_declared_identity_empty signing declared, identity={}       | request_signing.supported_for=[create_media_buy] | empty object   | rejected as missing identity.brand_json_url |
      | no_posture_identity_absent no signing posture, identity absent      | no signing posture                               | absent         | a valid capabilities response               |

  @T-UC-010-v31-measurement-catalog @v31 @main-flow @post-s26
  Scenario: measurement-catalog — measurement vendor metrics catalog (v3.1)
    Given a tenant is resolvable from the request context
    And "measurement" is in supported_protocols
    And the tenant declares measurement.metrics with metric_id "viewable_impressions"
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the response should include measurement section
    And measurement.metrics should be a non-empty array
    And each entry should include metric_id as a vendor-metric-id
    And each entry may include standard_reference as a URI
    And each entry may include accreditations as an array of accrediting_body entries
    And each entry may include methodology_version as a version string
    # v3.1: measurement.metrics[] (metric_id, standard_reference, accreditations, unit, methodology_url, methodology_version)
    # POST-S26: Buyer knows measurement vendor metric catalog
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-measurement-accreditations @v31 @main-flow @post-s26 @partition @boundary
  Scenario Outline: measurement-accreditations — third-party accreditation entries on metrics
    Given a tenant is resolvable from the request context
    And the tenant declares a measurement metric with accreditation <accreditation>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the metric accreditation should include accrediting_body
    And the accreditation may include certification_id, valid_until, and evidence_url
    # v3.1: measurement.metrics[].accreditations[] has accrediting_body (required), certification_id, valid_until, evidence_url
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

    Examples:
      | partition_boundary                                              | accreditation                                                                                          |
      | accreditation_mrc MRC accreditation with certification_id       | accrediting_body=MRC certification_id=MRC-2026-001                                                     |
      | accreditation_arf ARF accreditation                              | accrediting_body=ARF                                                                                   |
      | accreditation_full body certification valid_until evidence_url  | accrediting_body=ABC certification_id=ABC-42 valid_until=2027-01-01 evidence_url=https://abc.org/listing |

  @T-UC-010-v31-compliance-testing @v31 @main-flow @post-s27 @partition @boundary
  Scenario: compliance-testing — compliance_testing.scenarios declares comply_test_controller support
    Given a tenant is resolvable from the request context
    And the tenant supports comply_test_controller with all six scenarios
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then compliance_testing.scenarios should be a non-empty array
    And each scenario should be one of "force_creative_status", "force_account_status", "force_media_buy_status", "force_session_status", "simulate_delivery", "simulate_budget_spend"
    # v3.1: compliance_testing.scenarios enum (force_creative_status, force_account_status, force_media_buy_status, force_session_status, simulate_delivery, simulate_budget_spend)
    # POST-S27: Buyer knows compliance testing scenarios
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-specialisms @v31 @main-flow @post-s28
  Scenario: specialisms — kebab-case specialism claims graded by AAO compliance runner
    Given a tenant is resolvable from the request context
    And the tenant claims specialisms ["creative-generative", "sales-non-guaranteed"]
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then specialisms should be a unique array of kebab-case enum IDs
    And each specialism should roll up to a protocol in supported_protocols
    # v3.1: specialisms[] (kebab-case enum IDs; each must map to a parent protocol in supported_protocols)
    # POST-S28: Buyer knows the seller's specialisms and experimental_features conformance surface
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-experimental-features @v31 @main-flow @post-s28
  Scenario: experimental-features — dot-separated ids of implemented experimental surfaces
    Given a tenant is resolvable from the request context
    And the tenant implements experimental surfaces ["brand.rights_lifecycle", "trusted_match.core"]
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then experimental_features should be a unique array of dot-separated ids
    And each id should match pattern "^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"
    # v3.1: experimental_features[] (pattern: ^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-advisory-errors @v31 @main-flow @post-s29
  Scenario: advisory-errors — top-level errors[] is advisory and does not fail discovery
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    And the seller surfaces an advisory warning during discovery
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the response should include errors as an array of error objects
    And the response should still be a valid capabilities response
    # v3.1: errors top-level (advisory; does not signify discovery failure)
    # POST-S29: Advisory errors do not fail capabilities discovery
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-creative-extended @v31 @main-flow @post-s14
  Scenario: creative-extended — creative protocol exposes library / generation / transformation in v3.1
    Given a tenant is resolvable from the request context
    And "creative" is in supported_protocols
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then creative.supports_compliance should be a boolean
    And creative.has_creative_library should be a boolean
    And creative.supports_generation should be a boolean
    And creative.supports_transformation should be a boolean
    # v3.1: creative.has_creative_library, supports_generation, supports_transformation (in addition to supports_compliance)
    # POST-S14 (v3.1 extension): Buyer knows full creative protocol capabilities

  @T-UC-010-v31-version-unsupported @v31 @extension @ext-f @error @post-f2 @post-f4 @partition
  Scenario: version-unsupported — VERSION_UNSUPPORTED error carries authoritative supported_versions
    Given a tenant is resolvable from the request context
    And the seller speaks adcp release-precision versions "3.0", "3.1"
    When the Buyer Agent calls get_adcp_capabilities MCP tool with adcp_version "4.0"
    Then the response should be a VERSION_UNSUPPORTED error
    And the error code should be "VERSION_UNSUPPORTED"
    And the error details should include supported_versions as a non-empty array
    And each supported_versions entry should match pattern "^\\d+\\.\\d+(-[a-zA-Z0-9.-]+)?$"
    And the error details may include supported_majors as a deprecated array of integers
    And the error details may include build_version as an advisory semver string
    And the Buyer Agent may re-pin to a value from supported_versions and retry without a second discovery round-trip
    And the error should include "suggestion" field advising the Buyer to re-pin to a supported_versions entry
    # v3.1 Phase 1.5 wave A: error-details/version-unsupported.json
    # PRE-BIZ11 / BR-19: supported_versions REQUIRED with minItems:1; supported_majors DEPRECATED; build_version advisory only
    # POST-F2: Buyer knows the specific error code
    # POST-F4: Buyer can re-pin and retry without another discovery round-trip
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-version-unsupported-major-fallback @v31 @extension @ext-f @error @post-f4 @partition
  Scenario: version-unsupported-major-fallback — major-version negotiation falls back to supported_versions
    Given a tenant is resolvable from the request context
    And the seller speaks adcp release-precision versions "3.0", "3.1"
    When the Buyer Agent calls get_adcp_capabilities MCP tool with adcp_major_version 4
    Then the response should be a VERSION_UNSUPPORTED error
    And the error details should include supported_versions containing "3.0" and "3.1"
    And the error details may include supported_majors containing 3
    And the error should include "suggestion" field advising re-pin to a supported_versions entry
    # v3.1 Phase 1.5 wave A: when buyer pins adcp_major_version, details still carries supported_versions
    # POST-F4: supported_versions is authoritative even for major-version pins
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

  @T-UC-010-v31-version-unsupported-build-version-advisory @v31 @extension @ext-f @error @post-f4 @boundary
  Scenario: version-unsupported-build-version-advisory — build_version is advisory and MUST NOT drive negotiation
    Given a tenant is resolvable from the request context
    And the seller speaks adcp release-precision versions "3.0", "3.1"
    And the seller's build_version is "3.1.2+scope3.deploy.4821"
    When the Buyer Agent calls get_adcp_capabilities MCP tool with adcp_version "4.0"
    Then the response should be a VERSION_UNSUPPORTED error
    And the error details should include build_version equal to "3.1.2+scope3.deploy.4821"
    And the Buyer Agent must select the next adcp_version from supported_versions
    And the Buyer Agent must not use build_version to choose a retry version
    And the error should include "suggestion" field advising the Buyer to select a supported_versions entry
    # v3.1 Phase 1.5 wave A: build_version is full semver and is advisory triage only
    # BR-19: build_version MUST NOT be used for negotiation
    # BR-19: build_version is advisory triage only

  @T-UC-010-v31-request-signing-monotonicity @v31 @invariant @boundary @partition
  Scenario Outline: request-signing posture sets boundary - <boundary_point>
    Given a tenant is resolvable from the request context
    And the tenant declares request_signing posture sets for <boundary_point>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the request_signing posture sets should be <expected> against the monotonicity invariant
    # signing_posture_monotonicity.yaml: required_for/warn_for ⊆ supported_for; warn_for ∩ required_for = ∅;
    # protocol_methods_required_for ⊆ protocol_methods_supported_for (namespace-split buckets)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

    Examples:
      | boundary_point                                                                      | expected |
      | required_for = supported_for (full subset, equal sets)                              | valid    |
      | required_for adds one operation not in supported_for                                | invalid  |
      | warn_for and required_for share zero operations                                     | valid    |
      | warn_for and required_for share exactly one operation                               | invalid  |
      | protocol_methods_required_for ⊆ protocol_methods_supported_for, equal sets          | valid    |
      | protocol_methods_required_for adds one method not in protocol_methods_supported_for | invalid  |

  @T-UC-010-v31-idempotency-ttl-bounds @v31 @boundary @partition @post-s15
  Scenario Outline: adcp.idempotency replay-ttl boundary - <boundary_point>
    Given a tenant is resolvable from the request context
    And the tenant declares idempotency posture at <boundary_point>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the idempotency posture should be <expected> against the replay-ttl bounds
    # capabilities_idempotency_posture.yaml: replay_ttl_seconds in [3600, 604800];
    # in_flight_max_seconds <= replay_ttl_seconds (cross-field PRE-BIZ7)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

    Examples:
      | boundary_point                              | expected |
      | replay_ttl_seconds = 3599 (below min)       | invalid  |
      | replay_ttl_seconds = 604800 (max, 7d)       | valid    |
      | replay_ttl_seconds = 604801 (above max)     | invalid  |
      | in_flight_max_seconds == replay_ttl_seconds | valid    |

  @T-UC-010-v31-account-sandbox @v31 @boundary @partition @post-s3
  Scenario Outline: sandbox flag boundary - <boundary_point>
    Given a tenant is resolvable from the request context
    And the tenant account is configured for <boundary_point>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the capabilities response should be <expected> for the sandbox flag
    # sandbox_response_semantics.yaml: account.sandbox on get_adcp_capabilities response
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

    Examples:
      | boundary_point                                          | expected |
      | sandbox: true in response (sandbox account)             | valid    |
      | sandbox absent in response (production account)         | valid    |
      | sandbox: false in response (explicit production)        | valid    |
      | capability not declared, sandbox provisioning requested | invalid  |

  @T-UC-010-v31-version-unsupported-details-bounds @v31 @boundary @partition
  Scenario Outline: details (VERSION_UNSUPPORTED error) boundary - <boundary_point>
    Given a tenant is resolvable from the request context
    And a VERSION_UNSUPPORTED error is produced with details at <boundary_point>
    When the Buyer Agent inspects the error details
    Then the VERSION_UNSUPPORTED error details should be <expected>
    # version_unsupported_details.yaml: supported_versions REQUIRED minItems:1; build_version advisory only
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

    Examples:
      | boundary_point                          | expected |
      | supported_versions empty array          | invalid  |
      | supported_versions omitted              | invalid  |
      | build_version used as negotiation input | invalid  |

  @T-UC-010-v31-identity-brand-json-url-bounds @v31 @boundary @partition @post-s25
  Scenario Outline: identity.brand_json_url boundary - <boundary_point>
    Given a tenant is resolvable from the request context
    And the tenant identity and signing posture are configured for <boundary_point>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the capabilities response should be <expected> for the trust-root pointer
    # signing_trust_root.yaml: brand_json_url REQUIRED when any signing posture signal is declared
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/protocol/get-adcp-capabilities-response.json

    Examples:
      | boundary_point                                                              | expected |
      | no signing posture, no brand_json_url                                       | valid    |
      | any one signing-posture signal true/non-empty, brand_json_url present       | valid    |
      | any one signing-posture signal true/non-empty, brand_json_url absent        | invalid  |
      | signing posture declared with identity: {}                                  | invalid  |

  @T-UC-010-v31-webhook-signing-bounds @v31 @boundary @partition @post-s24
  Scenario Outline: webhook_signing boundary - <boundary_point>
    Given a tenant is resolvable from the request context
    And the tenant declares webhook_signing posture described as <boundary_point>
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the webhook_signing posture should be <expected>
    # webhook_signing_posture.yaml: supported MUST be true when mutating webhooks emitted;
    # algorithms ⊆ {ed25519, ecdsa-p256-sha256}
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/agent-signing-key.json

    Examples:
      | boundary_point                                          | expected |
      | reporting_delivery_methods=['webhook'], supported=true  | valid    |
      | supports_webhook_delivery=true, supported=true          | valid    |
      | reporting_delivery_methods=['webhook'], supported=false | invalid  |
      | supports_webhook_delivery=true, supported absent        | invalid  |
      | algorithms=['ed25519']                                  | valid    |
      | algorithms=['ecdsa-p256-sha256']                        | valid    |
      | algorithms=['rsa-pss-sha512']                           | invalid  |

  @T-UC-010-v31-agent-signing-key-bounds @v31 @boundary @partition
  Scenario Outline: agent_signing_keys[] entry boundary - <boundary_point>
    Given a tenant is resolvable from the request context
    And the tenant publishes an agent_signing_keys[] entry described as <boundary_point>
    When the Buyer Agent calls get_adcp_capabilities MCP tool and the verifier evaluates the entry
    Then the signing key entry should be <expected> for signature verification
    # agent_signing_key.yaml (BR-RULE-235): each entry MUST carry kid + kty; trust anchor is the
    # adagents.json resolved via identity.brand_json_url (never the agent domain); when revoked_at
    # is present, a signature is verifiable only if its signing epoch is strictly before revoked_at.
    # Verification is performed by the buyer-side verifier (external system) — no seller-emitted error code.
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/agent-encryption-key.json

    Examples:
      | boundary_point                                                      | expected |
      | entry with kid and kty only                                         | valid    |
      | entry missing kid                                                   | invalid  |
      | entry missing kty                                                   | invalid  |
      | signing epoch one instant before revoked_at                         | valid    |
      | signing epoch exactly at revoked_at                                 | invalid  |
      | signing epoch after revoked_at                                      | invalid  |
      | revoked key observed by a cache within the TTL grace window (~5 min) | valid    |

  @T-UC-010-v31-agent-encryption-key-bounds @v31 @boundary @partition
  Scenario Outline: agent_encryption_keys[] entry boundary - <boundary_point>
    Given a tenant is resolvable from the request context
    And the tenant publishes an agent_encryption_keys[] entry described as <boundary_point>
    When the Buyer Agent calls get_adcp_capabilities MCP tool and inspects the encryption key entry
    Then the encryption key entry should be <expected> for HPKE TMPX encryption
    # agent_encryption_key.yaml (BR-RULE-236): X25519 HPKE public key (kty=OKP, crv=X25519, use=enc);
    # x is the base64url-encoded 32-byte public key; kid is opaque with maxLength 8 and MUST NOT encode
    # geographic or deployment information (no-leak privacy policy, INT-008). additionalProperties: false.

    Examples:
      | boundary_point                                       | expected |
      | kid length 8                                         | valid    |
      | kid length 9                                         | invalid  |
      | x decodes to 32 bytes                                | valid    |
      | x decodes to 31 or 33 bytes                          | invalid  |
      | kid = opaque token (e.g. 'k1')                       | valid    |
      | kid = geographic/deployment token (e.g. 'us-east1')  | invalid  |
