# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

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
  #   POST-S11: Buyer knows the seller-level reporting capabilities
  #   POST-S12: Buyer knows the audience targeting capabilities when feature enabled
  #   POST-S13: Buyer knows the conversion tracking capabilities when feature enabled
  #   POST-S14: Buyer knows the creative protocol capabilities when creative protocol supported
  #   POST-F1: System state is unchanged on failure (read-only operation)
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Application context is still echoed when possible

  Background:
    Given a Seller Agent is operational and accepting requests


  @T-UC-010-main-mcp @main-flow @mcp @post-s1 @post-s2 @post-s3 @post-s4 @post-s5 @post-s6 @post-s7 @post-s8 @post-s10 @post-s11 @partition @boundary
  Scenario: not_provided — Not provided (no protocol filter), discover complete capabilities via MCP
    Given a tenant is resolvable from the request context
    And the tenant has an adapter with channels "display, social, ctv"
    And the tenant has registered publisher partnerships with domains "news.com", "sports.com"
    And the adapter provides targeting capabilities including geo and device
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then the response should include adcp.major_versions containing 3
    And the response should include supported_protocols containing "media_buy"
    And the response should include account section with account_resolution and billing models
    And the response should include media_buy.features section with all 7 flags
    And the response should include media_buy.supported_pricing_models
    And the response should include media_buy.reporting section
    And the response should include media_buy.execution section with targeting
    And the response should include media_buy.portfolio with publisher_domains "news.com", "sports.com"
    And the response should include media_buy.portfolio with primary_channels "display", "social", "ctv"
    And the response should include last_updated as a valid timestamp
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
    # POST-S11: Buyer knows reporting capabilities

  @T-UC-010-main-rest @main-flow @a2a @post-s1 @post-s2 @post-s3 @post-s4 @post-s5 @post-s6 @post-s7 @post-s8 @post-s10 @post-s11
  Scenario: Discover complete capabilities via A2A
    Given a tenant is resolvable from the request context
    And the tenant has an adapter with channels "display, social, ctv"
    And the tenant has registered publisher partnerships with domains "news.com", "sports.com"
    And the adapter provides targeting capabilities including geo and device
    And the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a get_adcp_capabilities skill request
    Then the response should include adcp.major_versions containing 3
    And the response should include supported_protocols containing "media_buy"
    And the response should include account section with account_resolution and billing models
    And the response should include media_buy.features section with all 7 flags
    And the response should include media_buy.supported_pricing_models
    And the response should include media_buy.reporting section
    And the response should include media_buy.execution section with targeting
    And the response should include media_buy.portfolio with publisher_domains "news.com", "sports.com"
    And the response should include media_buy.portfolio with primary_channels "display", "social", "ctv"
    And the response should include last_updated as a valid timestamp
    # POST-S1 through POST-S8, POST-S10, POST-S11 verified (same as MCP path)

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

  @T-UC-010-reporting @main-flow @post-s11
  Scenario: Capabilities response includes reporting capabilities
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities MCP tool
    Then media_buy.reporting should include date_range_support as a boolean
    And media_buy.reporting should include daily_breakdown as a boolean
    And media_buy.reporting should include webhooks as a boolean
    And media_buy.reporting should include available_dimensions as an array
    And available_dimensions may include "geo", "device_type", "device_platform", "audience", "placement", "creative", "keyword", "catalog_item"
    # POST-S11: Buyer knows seller-level reporting capabilities
    # POST-S11: Buyer knows reporting capabilities

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
    And media_buy.audience_targeting should include matching_latency_description as a string
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
    And media_buy.conversion_tracking should include supports_hashed_identifiers as a boolean
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
      | invalid_token_a2a invalid token (A2A) | invalid     | A2A     | AUTH_TOKEN_INVALID |

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
    And the response should include supported_protocols containing "media_buy"
    And the response should NOT include media_buy details
    And the response should NOT include account section
    And the response should NOT include media_buy.reporting section
    And the response should NOT include media_buy.audience_targeting section
    And the response should NOT include media_buy.conversion_tracking section
    # BR-RULE-052 INV-1: No tenant -> minimal response
    # @bva capabilities_degradation: tenant absent
    # POST-S1: Buyer knows AdCP v3 (minimal)
    # POST-S2: Buyer knows media_buy protocol (minimal)

  @T-UC-010-ext-a-a2a @extension @ext-a @degradation @a2a @partition
  Scenario: no_tenant — minimal capabilities via A2A
    Given no tenant can be resolved from the request context
    When the Buyer Agent sends a get_adcp_capabilities skill request via A2A without token
    Then the response should include adcp.major_versions containing 3
    And the response should include supported_protocols containing "media_buy"
    And the response should NOT include media_buy details
    # BR-RULE-052 INV-1: No tenant -> minimal response
    # POST-S1, POST-S2 (minimal)

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
      | reporting_absent adapter fails → reporting section absent                                                    | unavailable   | conversion_tracking | true       | reporting            | absent        |
      | full_response adapter succeeds → reporting section present                                                   | available     | conversion_tracking | true       | reporting            | present       |
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
    And the error code should be "AUTH_TOKEN_INVALID"
    And the error message should reference authentication or token validation
    And the error should include "suggestion" field when possible
    # BR-RULE-041 INV-5: A2A requires valid token if one is provided
    # Error code: AUTH_TOKEN_INVALID
    # POST-F2: Buyer knows what failed and the error code
    # POST-F1: No state change (read-only)
    # POST-F3: Suggestion included when possible (A2A ServerError may not support it)

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
    And media_buy.execution.targeting should include device_platform flag
    And media_buy.execution.targeting should include device_type flag
    And media_buy.execution.targeting should include language flag
    And media_buy.execution.targeting should include audience_include flag
    And media_buy.execution.targeting should include audience_exclude flag
    And media_buy.execution.targeting should include keyword_targets section
    And media_buy.execution.targeting should include negative_keywords section
    And media_buy.execution.targeting should include geo_proximity section
    # POST-S5: Buyer knows execution capabilities
    # POST-S5: Targeting capabilities verified (all dimensions)

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
      | partial_dimensions                                                                | geo_countries=true, geo_regions=false, device_platform=true                                                      | partial dimensions as reported                         |
      | nested_populated geo_metros threshold: any sub-property true -> object present     | geo_metros.nielsen_dma=true, geo_postal_areas.us_zip=true                                                        | nested objects present                                 |
      | nested_absent geo_metros threshold: all sub-properties false -> object absent      | no nested sub-properties true                                                                                    | nested objects absent                                  |
      | age_restriction_supported age_restriction with verification methods               | age_restriction.supported=true, verification_methods=[self_declared]                                             | age_restriction section present                        |
      | device_type_supported device_type declared (include + exclude)                     | device_type=true                                                                                                 | device_type targeting available                         |
      | audience_dimensions audience_include declared (requires features.audience_targeting) | audience_include=true, audience_exclude=true                                                                     | audience include/exclude targeting available            |
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
      | device_platform declared                                                  |
      | audience_exclude declared (requires features.audience_targeting)           |
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
      | no_tenant                                           | no tenant can be resolved from the request context                                  | minimal: adcp v3 + protocols=[media_buy] only                       |
      | adapter_fail                                        | a tenant is resolvable but adapter is unavailable                                   | channels=[display], default targeting, no reporting/audience/conversion |
      | db_fail                                             | a tenant is resolvable but database query fails                                     | placeholder domain, other sections unaffected                        |
      | adapter_and_db_fail                                 | a tenant is resolvable but both adapter and DB fail                                 | combined defaults, adapter-dependent sections absent                 |
      | no_principal                                        | a tenant is resolvable but no auth principal available                              | similar to adapter_fail (no principal -> no adapter)                 |
      | account_degraded                                    | a tenant is resolvable with partial account config                                  | account present but partially populated                              |
      | reporting_absent                                    | a tenant is resolvable but adapter unavailable                                      | reporting section absent                                             |
      | audience_targeting_absent                           | a tenant is resolvable but adapter unavailable or audience_targeting=false           | audience_targeting section absent                                    |
      | conversion_tracking_absent                          | a tenant is resolvable but adapter unavailable or conversion_tracking=false          | conversion_tracking section absent                                   |
      | creative_absent                                     | a tenant is resolvable but creative not in supported_protocols                      | creative section absent                                              |

