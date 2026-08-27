# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge
# Locally reconciled to AdCP 3.1.1 on 2026-07-13 per #1592 (P0 audit salesagent-y2vz).
# Local edits survive semantic merge (project policy); true divergences to be mirrored upstream.
# Spec authority: adcp tag v3.1.1 — dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json
# (JSON pointers below), dist/docs prose, dist/compliance/3.1.1/capability-discovery.yaml.
#
# Retired 2026-07-13 (P0.4 reconcile):
#   - @T-UC-010-ext-e-gap: pinned non-echo as expected while its siblings assert the spec-true
#     echo; context echo is a MUST at 3.1.1 (core/protocol-envelope.json "MUST preserve
#     byte-for-byte") and machine-graded by capability-discovery.yaml — the gap is documented
#     by XFAIL-EXPECTED comments on the echo scenarios instead.
#   - @T-UC-010-features-boundaries: vacuous outline (no observable expected values); its
#     negative boundaries are concrete rows in @T-UC-010-features-partitions now.
#   - @T-UC-010-v31-agent-signing-key-bounds and @T-UC-010-v31-agent-encryption-key-bounds:
#     adagents.json scope, not capabilities-response scope — signing_keys[]/encryption_keys[]
#     live on authorized_agents[*] per core/authorized-agent-base.json and are never returned
#     by get_adcp_capabilities; re-scope to an adagents.json-publication suite if needed.
#   - @T-UC-010-v31-account-sandbox row 4 ("capability not declared, sandbox provisioning
#     requested"): sandbox provisioning happens at sync_accounts time — rescoped to UC-011
#     (the UC-011 feature owner is adding the sync_accounts-side scenario).
#
# Transport policy (#1592): every scenario runs on all 4 transports (impl, a2a, mcp, rest)
# via harness parametrization — When steps are transport-neutral. The only deliberately
# transport-specific scenarios are the auth-policy rows (@T-UC-010-auth channel column) and
# the invalid-token pair (@T-UC-010-ext-c-a2a / @T-UC-010-ext-c-mcp), where the per-channel
# auth behavior IS the subject (spec-silent on auth for this task — capability-discovery.yaml
# "No prerequisites"; production authoritative per project policy).

Feature: BR-UC-010 Discover Seller Capabilities
  As a Buyer (Human or AI Agent)
  I want to discover what protocols, features, and targeting dimensions a Seller Agent supports
  So that I can evaluate the seller's capabilities before creating media buys or activating signals

  # Postconditions verified:
  #   POST-S1: Buyer knows which AdCP major versions the seller supports
  #   POST-S2: Buyer knows which domain protocols the seller supports
  #   POST-S3: Buyer knows the account requirements
  #   POST-S4: Buyer knows the media-buy feature flags (4 named 3.1.1 flags: inline_creative_management,
  #            property_list_filtering, catalog_management, committed_metrics_supported) plus the
  #            presence-object capabilities (audience_targeting, conversion_tracking, content_standards);
  #            sandbox moved to account.sandbox at 3.x
  #   POST-S5: Buyer knows the execution capabilities (targeting, creative specs)
  #   POST-S6: Buyer knows the seller's portfolio (publisher domains, channels, countries, policies)
  #   POST-S7: Buyer knows when capabilities were last updated
  #   POST-S8: Buyer knows which extension namespaces the seller supports
  #   POST-S9: Application context from the request is echoed unchanged in the response
  #   POST-S10: Buyer knows the supported pricing models across the seller's product portfolio
  #   POST-S11: DEPRECATED in v3.1 (media_buy.reporting subtree removed) — superseded by POST-S18
  #   POST-S12: Buyer knows the audience targeting capabilities when supported (presence of
  #             media_buy.audience_targeting object indicates support at 3.1.1)
  #   POST-S13: Buyer knows the conversion tracking capabilities when supported (presence of
  #             media_buy.conversion_tracking object indicates support at 3.1.1)
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
  #   POST-S30: Buyer knows the account establishment surface (require_operator_auth,
  #             authorization_endpoint, required_for_products, supported_billing, account_financials)
  #   POST-S31: Buyer knows the 3.1.1 pre-call discriminators (creative.multiplicity,
  #             creative.supports_* fan-out/refinement flags, media_buy.creative_approval_mode,
  #             media_buy.governance_aware, media_buy.vendor_metric_optimization)
  #   POST-F1: System state is unchanged on failure (read-only operation)
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Application context is still echoed when possible
  #   POST-F4: On unsupported version, seller returns VERSION_UNSUPPORTED carrying authoritative supported_versions[] for re-pin

  Background:
    Given a Seller Agent is operational and accepting requests


  @T-UC-010-main @main-flow @post-s1 @post-s2 @post-s3 @post-s4 @post-s5 @post-s6 @post-s7 @post-s8 @post-s10 @post-s15 @post-s18 @partition @boundary
  Scenario: not_provided — Not provided (no protocol filter), discover complete capabilities
    Given a tenant is resolvable from the request context
    And the tenant has an adapter with channels "display, social, ctv"
    And the tenant has registered publisher partnerships with domains "news.com", "sports.com"
    And the adapter provides targeting capabilities including geo
    And the tenant billing policy is configured as operator, agent
    And the tenant account is configured for sandbox: false in response (explicit production)
    When the Buyer Agent calls get_adcp_capabilities
    Then the response should include adcp.major_versions containing 3
    And adcp.idempotency.supported should equal true
    And adcp.idempotency.replay_ttl_seconds should be an integer between 3600 and 604800
    And the response should include supported_protocols containing "media_buy"
    And account.supported_billing should equal [operator, agent]
    And account.sandbox should equal false
    And media_buy.features should have boolean flags inline_creative_management, property_list_filtering, catalog_management and committed_metrics_supported
    And media_buy.supported_pricing_models should be a non-empty unique array of pricing-model enum values
    And media_buy.reporting_delivery_methods should be a non-empty unique subset of ["webhook", "offline"]
    And media_buy.execution.targeting.geo_countries should equal true
    And media_buy.execution.targeting.geo_regions should equal true
    And the response should include media_buy.portfolio with publisher_domains "news.com", "sports.com"
    And the response should include media_buy.portfolio with primary_channels "display", "social", "ctv"
    And last_updated should be an RFC 3339 date-time string
    # Former @T-UC-010-main-mcp / @T-UC-010-main-rest twins merged 2026-07-13: after de-pinning
    # the transport wording the two scenarios were word-identical (the extra "authenticated"
    # Given was auth-policy territory, owned by @T-UC-010-auth); the 4-way transport
    # parametrization covers mcp, a2a, rest and impl.
    # 3.1.1 fixes: "all 7 flags" -> 4-flag features model (core/media-buy-features.json);
    # "device" targeting removed (docs get_adcp_capabilities.mdx L185: implied by media_buy
    # support); sandbox lives at account.sandbox; presence-only asserts upgraded to
    # config-derived value asserts.
    # @bva protocols: Not provided (no protocol filter)
    # POST-S1..S8, POST-S10, POST-S15, POST-S18 verified
    # Vague Thens spec-pinned (salesagent-f5fs): idempotency split into supported=true +
    # replay_ttl_seconds range (oneOf IdempotencySupported); billing/sandbox/features/pricing/
    # reporting/targeting-geo pinned to exact values or spec-grounded shape. Exact
    # supported_pricing_models / reporting_delivery_methods SETS and per-flag feature VALUES are
    # config-derived (spec-silent on value) — the enum/shape is graded; the set is a production
    # config surface (#1592, reported not improvised).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/required
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/adcp/properties/idempotency/oneOf
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/media-buy-features.json pointer=/properties
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/pricing-model.json pointer=/enum
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/billing-party.json pointer=/enum
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/reporting_delivery_methods
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/account/required

  @T-UC-010-main-readonly @main-flow @post-f1
  Scenario: Capabilities discovery is read-only — no state change
    Given a tenant is resolvable from the request context
    And the system has known state before the request
    When the Buyer Agent calls get_adcp_capabilities
    Then the row counts of tenants, principals, publisher_partners and media_buys should equal their pre-request values
    # POST-F1: System state is unchanged (read-only operation)
    # Observable set pinned (was "system state should be unchanged"): the storyboard
    # defines the read-only obligation (stateful: false); the exact tables snapshot is
    # the local observable — the four mutable tables a capabilities call could touch.
    # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/capability-discovery.yaml pointer=/steps (stateful: false on both storyboard steps)
    # Step definition must pin a concrete state snapshot (row counts / updated-at) — spec
    # defines the read-only obligation, the observable set is a local choice.

  @T-UC-010-main-timestamp @main-flow @post-s7
  Scenario: Capabilities response includes last_updated for cache invalidation
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities
    Then last_updated should parse as an RFC 3339 date-time value
    # POST-S7: Buyer knows when capabilities were last updated
    # Format pinned to the schema keyword (was "valid ISO 8601 timestamp"): the value
    # must parse as a JSON-Schema date-time (RFC 3339), e.g. "2025-10-14T14:25:30Z".
    # NOT-IN-SPEC presence: last_updated is optional at 3.1.1 — always emitting it is a local
    # guarantee, not a spec mandate. The format contract (date-time) is the spec part.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/last_updated

  @T-UC-010-pricing @main-flow @post-s10
  Scenario: Capabilities response includes supported pricing models
    Given a tenant is resolvable from the request context
    And the tenant uses the mock adapter with full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities
    Then media_buy.supported_pricing_models should be a non-empty unique array of pricing-model enum values
    And each pricing model should be one of "cpm", "vcpm", "cpc", "cpcv", "cpv", "cpp", "cpa", "flat_rate", "time"
    And media_buy.supported_pricing_models should contain no duplicates
    # POST-S10: Buyer knows supported pricing models across seller's portfolio
    # 3.1.1 enum is 9 values (adds cpa, time vs the pre-release snapshot); minItems 1, uniqueItems.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/supported_pricing_models
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/pricing-model.json pointer=/enum

  @T-UC-010-audience-caps @main-flow @post-s12
  Scenario: Capabilities response includes audience targeting capabilities when supported
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    And the tenant supports audience targeting
    When the Buyer Agent calls get_adcp_capabilities
    Then media_buy.audience_targeting.supported_identifier_types should equal ["hashed_email", "hashed_phone"]
    And media_buy.audience_targeting.minimum_audience_size should equal 1000
    And media_buy.audience_targeting.supported_uid_types should equal ["uid2", "rampid"]
    And media_buy.audience_targeting.supports_platform_customer_id should equal true
    And media_buy.audience_targeting.matching_latency_hours should equal {"min": 24, "max": 72}
    # POST-S12: Buyer knows audience targeting capabilities
    # 3.1.1: the features.audience_targeting flag was removed in 3.0 — PRESENCE of the
    # media_buy.audience_targeting object indicates support. Required members within the
    # block: supported_identifier_types, minimum_audience_size; the rest are optional and
    # asserted only when the tenant declares them.
    # Fixture-pinned values (was "matching the tenant config" with no declared fixture):
    # supported_identifier_types items enum [hashed_email, hashed_phone]; minimum_audience_size
    # integer minimum 1 (1000 used); supported_uid_types items from uid-type enum (uid2, rampid
    # are enum members); supports_platform_customer_id boolean; matching_latency_hours object of
    # integer min/max (both minimum 0). Production does not emit audience_targeting yet (#1855).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/audience_targeting/required
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/uid-type.json pointer=/enum
    # @source repo=adcp ref=v3.1.1 path=docs/protocol/get_adcp_capabilities.mdx (L183: flag replaced by object presence)

  @T-UC-010-conversion-caps @main-flow @post-s13
  Scenario: Capabilities response includes conversion tracking capabilities when supported
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    And the tenant supports conversion tracking
    When the Buyer Agent calls get_adcp_capabilities
    Then media_buy.conversion_tracking should be present
    And media_buy.conversion_tracking.supported_event_types should equal ["purchase", "page_view"]
    And media_buy.conversion_tracking.supported_uid_types should equal ["uid2", "rampid"]
    And media_buy.conversion_tracking.supported_hashed_identifiers should equal ["hashed_email"]
    And media_buy.conversion_tracking.supported_action_sources should equal ["website", "app"]
    And media_buy.conversion_tracking.attribution_windows should equal [{"post_click": [{"interval": 7, "unit": "days"}]}]
    And media_buy.conversion_tracking.multi_source_event_dedup should equal true
    # POST-S13: Buyer knows conversion tracking capabilities
    # 3.1.1: features.conversion_tracking flag removed in 3.0 — object PRESENCE indicates
    # support; no required members inside the block. Every subfield asserts an exact
    # fixture value (salesagent-ytq6, was "when declared ... matching the tenant config"
    # with no declared fixture): supported_event_types/uid_types/action_sources items are
    # drawn from their pinned enums; supported_hashed_identifiers ∈ [hashed_email,
    # hashed_phone]; attribution_windows items are window objects whose post_click is a
    # REQUIRED array of duration objects (interval integer >=1 + unit enum), NOT bare
    # durations. The exact per-seller SET is config-derived (spec-silent on value) — a
    # production config surface; production does not emit conversion_tracking at all yet
    # (#1855), so the scenario executes and fails at "should be present" (strict xfail).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/conversion_tracking
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/event-type.json pointer=/enum
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/uid-type.json pointer=/enum
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/action-source.json pointer=/enum
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/duration.json pointer=/required
    # @source repo=adcp ref=v3.1.1 path=docs/protocol/get_adcp_capabilities.mdx (L184: flag replaced by object presence)

  @T-UC-010-creative-caps @main-flow @post-s14
  Scenario: Capabilities response includes creative protocol when supported
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    And "creative" is in supported_protocols
    And the tenant declares creative supports_compliance true
    When the Buyer Agent calls get_adcp_capabilities
    Then the response should include the creative section
    And creative.supports_compliance should equal true
    # POST-S14: Buyer knows creative protocol capabilities
    # NOT-IN-SPEC direction note: schema says the block is "Only present if creative is in
    # supported_protocols" (necessary condition); emitting it whenever the protocol is
    # declared is the local choice. supports_compliance is an optional boolean — pinned to
    # the declared fixture value (salesagent-ytq6, was "equal the tenant-configured value"
    # with no declared fixture; mirrors the concrete sibling row). Production emits only the
    # media_buy protocol (creative not in supported_protocols), so the scenario executes and
    # fails at "should include the creative section" (strict xfail, #1724).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/creative/properties/supports_compliance

  # Deliberately transport-specific: auth policy genuinely differs per channel and the
  # channel column is the subject of the outline. Spec is silent on auth for this task
  # (capability-discovery.yaml: "No prerequisites ... the very first call a buyer makes"),
  # so the per-channel policy is production-authoritative; the ERROR CODE once rejecting a
  # presented credential is spec-governed: AUTH_INVALID, recovery terminal
  # (enums/error-code.json: AUTH_REQUIRED is a deprecated 3.x alias).
  @T-UC-010-auth @auth @invariant @partition @boundary @post-s1 @post-s2
  Scenario Outline: Authentication policy for capabilities discovery
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    And the Buyer has <token_state> authentication
    When the Buyer Agent invokes get_adcp_capabilities via <channel>
    Then the response should be <outcome>
    And a success outcome should carry adcp.major_versions, adcp.idempotency, supported_protocols and the media_buy section
    # INV-1 (no token -> full data), INV-2 (valid token -> full data),
    # INV-3 (invalid MCP/REST -> treated absent; local contract — this project's token rides
    # x-adcp-auth, not Authorization, and discovery is the no-prerequisite first call),
    # INV-4 (auth irrelevant to data), INV-5 (invalid A2A -> AUTH_INVALID error)
    # A success <outcome> is graded as a non-error completed discovery envelope: no wire
    # error envelope was produced and the spec-required top-level blocks (adcp,
    # supported_protocols) are present (salesagent-ytq6, was existence-only "response is not
    # None"). The invalid-A2A error row is wire-pinned to AUTH_INVALID / recovery terminal
    # (canonical code; graduated salesagent-7moz — A2A now validates a presented token and
    # rejects invalid ones with AUTH_INVALID, so the row passes; the former AUTH_REQUIRED
    # production gap is closed).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumDescriptions/AUTH_INVALID
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/required
    # REST rows pin the same treat-as-absent local contract as MCP (spec-silent -> production
    # authoritative; confirm on wiring).

    Examples:
      | partition_boundary                      | token_state | channel | outcome            |
      | no_token no token                       | no          | MCP     | success            |
      | no_token no token                       | no          | A2A     | success            |
      | no_token no token                       | no          | REST    | success            |
      | valid_token valid token                 | valid       | MCP     | success            |
      | valid_token valid token                 | valid       | A2A     | success            |
      | valid_token valid token                 | valid       | REST    | success            |
      | invalid_token_mcp invalid token (MCP)   | invalid     | MCP     | success            |
      | invalid_token_rest invalid token (REST) | invalid     | REST    | success            |
      | invalid_token_a2a invalid token (A2A)   | invalid     | A2A     | AUTH_INVALID       |

  @T-UC-010-auth-data-identity @auth @invariant
  Scenario: Authentication state does not affect response data content
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities without authentication
    And the Buyer Agent calls get_adcp_capabilities authenticated with a valid principal_id
    Then both responses should contain identical capabilities data ignoring last_updated and context
    # INV-4: Unauthenticated and authenticated callers receive identical data — the response
    # is the seller's surface, not caller-scoped (get_adcp_capabilities.mdx L23).
    # Comparison excludes volatile fields (last_updated, context echo) to avoid flake.
    # @source repo=adcp ref=v3.1.1 path=docs/protocol/get_adcp_capabilities.mdx (L23)

  @T-UC-010-ext-a @extension @ext-a @degradation @partition @boundary
  Scenario: no_tenant — tenant absent, minimal capabilities
    Given no tenant can be resolved from the request context
    When the Buyer Agent calls get_adcp_capabilities
    Then the response should include adcp.major_versions containing 3
    And the response should include adcp.supported_versions as a non-empty array
    And each value in adcp.supported_versions should match pattern "^\d+\.\d+(-[a-zA-Z0-9.-]+)?$"
    And adcp.idempotency.supported should be exactly true or false, and when false replay_ttl_seconds and in_flight_max_seconds should be absent
    And the response should include supported_protocols containing "media_buy"
    And the wire response should not contain a media_buy key
    And the response should NOT include account section
    # Former @T-UC-010-ext-a-mcp / @T-UC-010-ext-a-a2a twins merged 2026-07-13 (assertions
    # aligned; transport covered by 4-way parametrization).
    # NOT-IN-SPEC: minimal-on-no-tenant is a production contract (spec has no tenant concept);
    # the minimal shape IS schema-checked: top-level required is [adcp, supported_protocols],
    # and adcp.required is [major_versions, idempotency] — a minimal response WITHOUT
    # adcp.idempotency is schema-invalid, hence the idempotency assert.
    # Hardened (salesagent-ytq6): supported_versions entries pinned to the schema `pattern`
    # (was non-empty-only); idempotency pinned to the oneOf discriminator invariant — supported
    # is exactly true/false and, on the IdempotencyUnsupported branch (supported=false), the
    # `not.anyOf` MUST omit replay_ttl_seconds/in_flight_max_seconds (was "boolean supported
    # discriminator", a schema-role phrase asserting only the type); media_buy absence asserted
    # as wire-key absence (was "NOT include media_buy details" — there is no `media_buy.details`
    # wire key). Graduated: _build_adcp_block() now always emits adcp.supported_versions
    # (derived from SUPPORTED_ADCP_VERSIONS) on both the no-tenant and tenant-resolved paths.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/adcp/properties/supported_versions
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/adcp/properties/idempotency/oneOf/1
    # Design tension (flagged, production decision): advertising "media_buy" in
    # supported_protocols while omitting the media_buy block is schema-valid but against the
    # storyboard's spirit ("Expected when media_buy is in supported_protocols").
    # @bva capabilities_degradation: tenant absent
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/adcp/required

  @T-UC-010-ext-b-degradation @extension @ext-b @degradation @invariant @partition @boundary
  Scenario Outline: Graceful degradation when dependencies fail
    Given a tenant is resolvable from the request context
    And the adapter is in <adapter_state> state
    And the database is in <db_state> state
    When the Buyer Agent calls get_adcp_capabilities
    Then the response should pass schema validation for get-adcp-capabilities-response
    And primary_channels should be <expected_channels>
    And publisher_domains should be <expected_domains>
    # NOT-IN-SPEC: degradation policy is spec-silent — production authoritative; the one hard
    # spec invariant is that every response still validates against the response schema.
    # Symbolic expectations concretized 2026-07-13: fixture seeds channels
    # "display, social, ctv" on the adapter and domains "news.com, sports.com" in the DB;
    # placeholder domain is "example.com" (pin to production's actual placeholder on wiring).
    # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/capability-discovery.yaml pointer=/steps/0/validations (response_schema)

    Examples:
      | partition_boundary                                         | adapter_state | db_state  | expected_channels    | expected_domains       |
      | full_response tenant resolved adapter succeeds DB succeeds | available     | available | display, social, ctv | news.com, sports.com   |
      | adapter_fail adapter fails                                 | unavailable   | available | display              | news.com, sports.com   |
      | db_fail DB fails                                           | available     | failure   | display, social, ctv | example.com            |
      | adapter_and_db_fail adapter AND DB fail                    | unavailable   | failure   | display              | example.com            |
      | no_principal no auth principal available                   | no_principal  | available | display              | news.com, sports.com   |
      | db_empty adapter fails DB has no partnerships              | unavailable   | empty     | display              | example.com            |

  @T-UC-010-ext-b-schema-valid @extension @ext-b @degradation @invariant
  Scenario: Degraded response is always schema-valid
    Given a tenant is resolvable from the request context
    And the adapter is unavailable
    And the database query fails
    When the Buyer Agent calls get_adcp_capabilities
    Then the response should pass schema validation for get-adcp-capabilities-response
    And the wire response should not contain an adcp_error field
    And media_buy.portfolio should be omitted, never a fabricated publisher domain
    # INV-5 (local): degrade-don't-error; the schema-validity half is the spec-hard invariant
    # (storyboard validation check: response_schema). Rewritten (salesagent-ytq6): the two
    # vague Thens ("no error should be propagated", "degradation warnings should be logged
    # internally") were replaced with wire-observable assertions — the envelope MUST NOT carry
    # adcp_error for a non-failure (protocol-envelope). "degradation warnings logged internally"
    # was intentionally NOT re-added: internal logs are not on the wire.
    # Corrected (salesagent-piyo, 2026-08-04): this row combines adapter-unavailable AND
    # database-query-fails, so no real publisher_domain data was read at all. Was pinned to
    # "primary_channels falls back to [display]" via a fabricated placeholder portfolio, which
    # was salesagent-piyo's bug -- portfolio.publisher_domains is REQUIRED+minItems:1 (pinned
    # v3.1.1 schema) whenever portfolio is present, and media_buy has no required fields, so the
    # honest, schema-legal response with no real domain is to OMIT portfolio entirely (and
    # primary_channels along with it, since it lives inside portfolio). Same correction as the
    # adapter_and_db_fail row of the sibling ext-b-degradation outline.
    # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/universal/capability-discovery.yaml pointer=/phases/0/steps/0/validations (check: response_schema)
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/bundled/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/portfolio/properties/publisher_domains (required, minItems 1)
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/protocol-envelope.json pointer=/properties/adcp_error (envelope error-signal for fatal failures — absent on a successful degraded response)

  @T-UC-010-degradation-account @extension @degradation @partition @boundary @post-s3
  Scenario Outline: Account section presence depends on tenant resolution
    Given <tenant_condition>
    When the Buyer Agent calls get_adcp_capabilities
    Then the account section should be <account_state>
    And a present account section should include supported_billing as a non-empty array of billing-party enum values
    # NOT-IN-SPEC: account presence is an mdx SHOULD ("All sellers should declare this
    # section"); mapping presence to tenant resolution is production choice. The account
    # block is ALL-OR-NOTHING per schema: whenever present it MUST carry supported_billing
    # (minItems 1) — there is no schema-legal "partially populated without supported_billing".
    # The former "partial" row now pins the schema-legal degraded shape: block present with
    # supported_billing only, all optional fields omitted.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/account/required

    Examples:
      | partition_boundary                                            | tenant_condition                                   | account_state                                             |
      | no_tenant no tenant → account section absent                  | no tenant can be resolved from the request context | absent                                                    |
      | full_response tenant resolved → account section present       | a tenant is resolvable from the request context    | present                                                   |
      | account_degraded partial config → supported_billing-only block | a tenant is resolvable with partial account config | present with supported_billing only and no optional fields |

  @T-UC-010-degradation-sections @extension @degradation @partition @boundary
  Scenario Outline: Adapter-dependent sections absent when adapter fails
    Given a tenant is resolvable from the request context
    And the adapter is in <adapter_state> state
    And the tenant has <capability> configured as <capability_state>
    When the Buyer Agent calls get_adcp_capabilities
    Then the media_buy.<section> section should be <section_state>
    And a present audience_targeting section should include supported_identifier_types and minimum_audience_size
    # 3.1.1 presence-indicates-support model: the wire flags features.audience_targeting /
    # features.conversion_tracking were REMOVED in 3.0 (mdx L183-184) — the Given now names
    # tenant-config capabilities, not the excised wire fields. When audience_targeting is
    # present the schema REQUIRES supported_identifier_types + minimum_audience_size;
    # conversion_tracking has no required subfields.
    # NOT-IN-SPEC: adapter-dependence of the sections is production choice.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/audience_targeting/required

    Examples:
      | partition_boundary                                                                       | adapter_state | capability          | capability_state | section              | section_state |
      | audience_targeting_absent adapter fails → audience_targeting section absent               | unavailable   | audience targeting  | enabled          | audience_targeting   | absent        |
      | full_response audience targeting enabled AND adapter succeeds → section present           | available     | audience targeting  | enabled          | audience_targeting   | present       |
      | audience_targeting_absent audience targeting disabled → section absent                    | available     | audience targeting  | disabled         | audience_targeting   | absent        |
      | conversion_tracking_absent adapter fails → conversion_tracking section absent             | unavailable   | conversion tracking | enabled          | conversion_tracking  | absent        |
      | full_response conversion tracking enabled AND adapter succeeds → section present          | available     | conversion tracking | enabled          | conversion_tracking  | present       |
      | conversion_tracking_absent conversion tracking disabled → section absent                  | available     | conversion tracking | disabled         | conversion_tracking  | absent        |

  @T-UC-010-degradation-creative @extension @degradation @partition @boundary @post-s14
  Scenario Outline: Creative section presence depends on protocol support
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    And <creative_condition>
    When the Buyer Agent calls get_adcp_capabilities
    Then the creative section should be <creative_state>
    # Row 1 is spec-true (schema: creative "Only present if creative is in supported_protocols").
    # Row 2 (present-when-declared) is NOT-IN-SPEC strictly — "only present if" is a necessary
    # condition, not an emission mandate; presence-when-declared is the sensible local choice.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/creative

    Examples:
      | partition_boundary                                                             | creative_condition                        | creative_state |
      | creative_absent creative not in supported_protocols → creative section absent  | "creative" is NOT in supported_protocols  | absent         |
      | full_response creative in supported_protocols → creative section present       | "creative" is in supported_protocols      | present        |

  # Deliberately A2A-specific: this scenario IS the A2A auth policy (per-channel behavior is
  # the subject; REST/MCP invalid-token policy is pinned by @T-UC-010-auth rows).
  @T-UC-010-ext-c-a2a @extension @ext-c @error @a2a @post-f1 @post-f2 @post-f3
  Scenario: A2A request with invalid auth token — error returned
    Given a tenant is resolvable from the request context
    And the Buyer has an invalid authentication token
    When the Buyer Agent sends a get_adcp_capabilities skill request via A2A with the token
    Then the wire error envelope should carry code "AUTH_INVALID" with recovery "terminal"
    And the wire error message should contain "token" and "invalid"
    # Graduated (salesagent-7moz): A2A now always validates a presented token
    # regardless of the requested skill's own auth requirement.
    # POST-F2: Buyer knows what failed and the error code
    # POST-F1: No state change (read-only)
    # The message substring is pinned to production's ACTUAL AUTH_INVALID wording
    # (core/error.json message is a free string, so the spec cannot pin content):
    # resolved_identity.py raises "Authentication token is invalid for tenant '...'"
    # and adcp_a2a_server.py emits "Authentication token is invalid or expired." —
    # both contain "token" and "invalid", and neither is the AUTH_REQUIRED
    # missing-credential wording ("authentication required"), the deprecated 3.x alias.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumDescriptions/AUTH_INVALID
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/error.json pointer=/properties/message (free string — content pinned to production)
    # @source repo=adcp ref=v3.1.1 path=docs/protocol/get_adcp_capabilities.mdx (error table L1083-1084)

  # Deliberately MCP-specific: this scenario IS the MCP auth policy (treat-invalid-as-absent).
  @T-UC-010-ext-c-mcp @extension @ext-c @auth @mcp @degradation
  Scenario: MCP request with invalid auth token — silently ignored
    Given a tenant is resolvable from the request context
    And the tenant has registered publisher partnerships with domains "verified-partner.com"
    And the Buyer has an invalid authentication token
    When the Buyer Agent calls get_adcp_capabilities via MCP with the token
    Then the response should be a success carrying adcp.major_versions, adcp.idempotency and supported_protocols
    And the response should carry the tenant's normal capabilities, not gated on the invalid token
    # A real publisher partnership is seeded (salesagent-piyo) so media_buy.portfolio is
    # populated -- portfolio is omitted entirely, never fabricated, when no real publisher
    # domain exists, and this scenario's concern is the invalid-token/channels interaction,
    # not publisher domain resolution.
    # LOCAL CONTRACT (documented reading): treat-invalid-as-absent sits in tension with the
    # AUTH_INVALID seller-MUST, which governs an Authorization header; this project's MCP
    # token rides x-adcp-auth and discovery is the spec's no-prerequisite first call —
    # production is authoritative on the policy (spec-silent), the reading is pinned here.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumDescriptions/AUTH_INVALID
    # INV-4: capabilities describe the seller, not the caller — the invalid
    # token must not degrade adapter-derived data (channels). Graduated: MCP ToolResult now
    # pre-serializes via model_dump(mode="json"), so audience_targeting is correctly omitted
    # instead of serialized as null.
    # @source repo=adcp ref=v3.1.1 path=docs/protocol/get_adcp_capabilities.mdx (L23)

  @T-UC-010-ext-d-filter @extension @ext-d @boundary @partition
  Scenario: media_buy (first enum value) — protocol filter honored, response filtered to requested domain
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities with protocols filter ["media_buy"]
    Then the response should include the media_buy section
    And the response should include adcp, supported_protocols and account as protocol-invariant blocks
    And the response should NOT include the signals, governance, sponsored_intelligence or creative sections
    # Graduated: the POST /api/v1/capabilities route carries protocols/context/adcp_version
    # on all 3 transports; the response is filtered to the requested protocol domain.
    # Inverted 2026-07-13 from a @known-gap pin ("filter ignored") to the spec-true assertion:
    # the 3.1.1 storyboard step get_capabilities_filtered sends protocols=["media_buy"] and
    # expects only the requested domain details (filtering is storyboard-prose expected;
    # machine validations grade response_schema + context echo — note: partially graded).
    # @bva protocols: media_buy (first enum value)
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-request.json pointer=/properties/protocols
    # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/capability-discovery.yaml pointer=/steps (get_capabilities_filtered)

  @T-UC-010-ext-d-all-protocols @extension @ext-d @boundary @partition
  Scenario: signals governance sponsored_intelligence creative (last enum value) — filter naming all protocols returns full response
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities with protocols filter ["media_buy", "signals", "governance", "sponsored_intelligence", "creative"]
    Then the response should include the media_buy section
    And the response should include the signals section
    And the response should include the governance section
    And the response should include the sponsored_intelligence section
    And the response should include the creative section
    # Spec-true boundary (not a gap pin): a filter equal to the full request enum yields the
    # same sections whether the filter is honored or ignored. Note the request/response enum
    # asymmetry: the response supported_protocols enum adds "brand" and "measurement", which
    # the request filter cannot select.
    # @bva protocols: creative (last enum value)
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-request.json pointer=/properties/protocols/items/enum

  @T-UC-010-ext-d-invalid-value @extension @ext-d @error @boundary @partition
  Scenario: unknown_protocol — filter with invalid enum value is rejected
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities with protocols filter ["marketing"]
    Then the wire error envelope should carry code "VALIDATION_ERROR" with recovery "correctable"
    # Graduated: build_get_adcp_capabilities_request now constructs a real typed
    # GetAdcpCapabilitiesRequest — Pydantic enforces the protocols enum, so "marketing"
    # (outside the closed 5-value enum) is rejected.
    # Inverted 2026-07-13 from a @known-gap pin. Transport note: MCP may reject at the
    # FastMCP validation layer before _impl — assert the wire envelope on each transport.
    # @bva protocols: Unknown string not in enum
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-request.json pointer=/properties/protocols/items/enum

  @T-UC-010-ext-d-empty @extension @ext-d @error @boundary @partition
  Scenario: empty_array — protocol filter with empty array is rejected
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities with protocols filter []
    Then the wire error envelope should carry code "VALIDATION_ERROR" with recovery "correctable"
    # Graduated: build_get_adcp_capabilities_request now constructs a real typed
    # GetAdcpCapabilitiesRequest — Pydantic enforces minItems:1, so an empty protocols
    # array is rejected. Inverted 2026-07-13 from a @known-gap pin.
    # @bva protocols: Empty array
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-request.json pointer=/properties/protocols (minItems 1)

  @T-UC-010-ext-e-echo @context @post-s9 @invariant @partition @boundary
  Scenario: context_provided — context echoed unchanged in capabilities response
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities with context {"session_id": "abc-123", "trace": "xyz-789"}
    Then the response context should equal {"session_id": "abc-123", "trace": "xyz-789"}
    # Former @T-UC-010-ext-e-mcp / @T-UC-010-ext-e-a2a twins merged 2026-07-13: echo is
    # transport-invariant; the 4-way parametrization covers all transports.
    # Graduated: _get_adcp_capabilities_impl echoes req.context verbatim onto the response
    # on every transport — echo is a MUST at 3.1.1 ("MUST preserve byte-for-byte") and is
    # machine-graded by capability-discovery.yaml on both storyboard steps.
    # POST-S9: Application context echoed unchanged
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/protocol-envelope.json pointer=/properties/context
    # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/capability-discovery.yaml pointer=/steps/0/validations (field_value context.correlation_id)

  @T-UC-010-ext-e-absent @context @invariant @partition @boundary
  Scenario: context_absent — no context in request means no context in response
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities without context
    Then the wire response should not contain a context field
    # Implied by echo semantics (context is defined purely as caller-supplied echo; optional
    # in the protocol envelope) — no explicit MUST-omit in the spec; storyboard does not
    # grade absence. Assert on the wire response, not the typed payload.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/context.json pointer=/description

  @T-UC-010-ext-e-nested @context @invariant @boundary @partition
  Scenario: context_nested — deeply nested context object echoed unchanged
    Given a tenant is resolvable from the request context
    When the Buyer Agent calls get_adcp_capabilities with context {"deep": {"nested": {"level": 3, "data": true}}}
    Then the response context should equal {"deep": {"nested": {"level": 3, "data": true}}}
    # Graduated: _get_adcp_capabilities_impl echoes req.context verbatim.
    # Context is opaque — never parsed, modified, or validated; arbitrary nesting is valid
    # (core/context.json: object, additionalProperties true) and preserved byte-for-byte.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/protocol-envelope.json pointer=/properties/context

  @T-UC-010-ext-e-empty @context @invariant @boundary @partition
  Scenario: context_empty_object — empty context echoed, context = {}
    Given a tenant is resolvable from the request context
    When the Buyer Agent calls get_adcp_capabilities with context {}
    Then the wire response context should equal {}
    # Graduated: _get_adcp_capabilities_impl echoes req.context verbatim.
    # Empty object validates against core/context.json; echo-unchanged applies. Assert on the
    # wire (typed payloads may coerce empty-object/None ambiguously).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/context.json

  @T-UC-010-channel-mapping @channel @invariant @partition @boundary
  Scenario Outline: Channel name resolution from adapter to channels enum
    Given a tenant is resolvable from the request context
    And the adapter reports channels <adapter_channels>
    When the Buyer Agent calls get_adcp_capabilities
    Then primary_channels should be <expected_result>
    # NOT-IN-SPEC / production mapping: 3.1.1 defines only the channels enum value set — the
    # aliasing table (video->olv, audio->streaming_audio), the unrecognized-dropped rule and
    # the display fallback are production choices this outline pins (spec-silent ->
    # production authoritative). All OUTPUT values are valid 3.1.1 enum members. Note
    # portfolio.primary_channels has NO minItems — an empty array would also be schema-valid;
    # the display fallback is a product choice.
    # @bva primary_channels: 0 channels from adapter, 1 recognized channel,
    # 1 unrecognized channel, alias 'video', alias 'audio'
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/portfolio/properties/primary_channels

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
  Scenario: All 20 canonical channels enum values are valid
    Given a tenant is resolvable from the request context
    And the tenant has registered publisher partnerships with domains "verified-partner.com"
    And the adapter reports all 20 channels enum values
    When the Buyer Agent calls get_adcp_capabilities
    Then primary_channels should equal the channels enum's 20 canonical values
    # A real publisher partnership is seeded (salesagent-piyo) so media_buy.portfolio is
    # populated -- portfolio is omitted entirely, never fabricated, when no real publisher
    # domain exists, and this scenario's concern is the channel enum, not domain resolution.
    # 3.1.1 enum has 20 values: display, olv, social, search, ctv, linear_tv, radio,
    # streaming_audio, podcast, dooh, ooh, print, cinema, email, gaming, retail_media,
    # influencer, affiliate, product_placement, sponsored_intelligence
    # (count corrected 2026-07-13: scenario said 18, comment listed 19, spec has 20 — the
    # pre-release snapshot lacked sponsored_intelligence)
    # PRODUCTION GAP (strict xfail): CHANNEL_MAPPING (src/core/tools/capabilities.py)
    # maps 19 canonical values + 2 aliases but OMITS sponsored_intelligence, so an
    # adapter reporting all 20 enum values yields only 19 on the wire —
    # sponsored_intelligence is dropped as "unrecognized" (MediaChannel.sponsored_intelligence
    # exists in the adcp enum; only the production mapping lags). Executes and fails at
    # the 20-value equality until the mapping gains the 20th channel.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/channels.json pointer=/enum

  @T-UC-010-features @validation @post-s4
  Scenario: Capabilities response reflects the 3.1.1 media-buy feature model
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities
    Then media_buy.features should have boolean flags inline_creative_management, property_list_filtering, catalog_management and committed_metrics_supported
    And media_buy.content_standards should be present
    And media_buy.conversion_tracking should be present
    And media_buy.audience_targeting should be present
    And a present audience_targeting section should include supported_identifier_types and minimum_audience_size
    And account.sandbox should equal false
    # POST-S4: 3.1.1 feature model — 4 named boolean flags in media_buy.features
    # (core/media-buy-features.json, additionalProperties boolean); content_standards /
    # conversion_tracking / audience_targeting are presence-objects directly under media_buy
    # (flags removed in 3.0); sandbox moved to account.sandbox.
    # Spec-pinned Thens (salesagent-tmpd): the four tautological "presence should indicate
    # support" lines and the two type-only lines are pinned to observables — the 4-flag
    # boolean SHAPE (media-buy-features.json: 4 named flags, additionalProperties boolean,
    # none required; per-flag VALUE is config-derived / spec-silent so the shape is graded
    # and the value reported), each presence-object asserted PRESENT (its presence IS the
    # 3.1.1 support signal, replacing the removed flags), audience_targeting's two required
    # members, and account.sandbox pinned to false (schema default). PRODUCTION GAP
    # (strict xfail): the capabilities builder emits media_buy.features but NOT the
    # content_standards / conversion_tracking / audience_targeting presence-objects nor the
    # account block (account.sandbox) — the scenario executes, grades the features shape,
    # then fails at the first missing presence-object (#1855).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/media-buy-features.json pointer=/properties
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/audience_targeting/required
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/account/properties/sandbox

  @T-UC-010-features-partitions @partition @boundary @features @post-s4
  Scenario Outline: Feature capability configurations - <partition>
    Given a tenant is resolvable from the request context
    And the tenant capabilities are configured as <capability_config>
    When the Buyer Agent calls get_adcp_capabilities
    Then the response should satisfy <expected_assertion>
    # Rebuilt 2026-07-13 around the 3.1.1 shape (4 named flags + additionalProperties
    # boolean; presence-object partitions for conversion_tracking / audience_targeting /
    # content_standards incl. audience_targeting's required members; account.sandbox
    # partition). The former @T-UC-010-features-boundaries outline's negative boundaries are
    # the *_absent / *_false rows here.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/media-buy-features.json
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/audience_targeting/required

    Examples:
      | partition                                                        | capability_config                                                                                        | expected_assertion                                                                                                  |
      | mixed_typical named flags mixed true/false                      | inline_creative_management=true, property_list_filtering=false, catalog_management=false, committed_metrics_supported=false | media_buy.features equals {inline_creative_management: true, property_list_filtering: false, catalog_management: false, committed_metrics_supported: false} |
      | all_true all 4 named flags true                                  | all 4 named feature flags true                                                                            | media_buy.features has all 4 named flags true                                                                        |
      | all_false all 4 named flags false                                | all 4 named feature flags false                                                                           | media_buy.features has all 4 named flags false                                                                       |
      | with_additional_properties custom boolean flag accepted          | inline_creative_management=true, custom_reporting=true                                                    | media_buy.features contains inline_creative_management true and custom_reporting true                               |
      | conversion_tracking_enabled object presence signals support      | conversion tracking enabled                                                                               | media_buy.conversion_tracking is present                                                                             |
      | conversion_tracking_absent no conversion tracking configured     | conversion tracking disabled                                                                              | media_buy.conversion_tracking is absent                                                                              |
      | audience_targeting_enabled object presence with required members | audience targeting enabled                                                                                | media_buy.audience_targeting is present with supported_identifier_types and minimum_audience_size                    |
      | audience_targeting_absent no audience targeting configured       | audience targeting disabled                                                                               | media_buy.audience_targeting is absent                                                                               |
      | content_standards_absent no content standards configured         | content standards disabled                                                                                | media_buy.content_standards is absent                                                                                |
      | catalog_management_enabled catalog_management=true               | catalog_management=true                                                                                   | media_buy.features.catalog_management is true                                                                        |
      | catalog_management_disabled catalog_management=false             | catalog_management=false                                                                                  | media_buy.features.catalog_management is false                                                                       |
      | sandbox_enabled account.sandbox=true                             | sandbox=true                                                                                              | account.sandbox is true                                                                                              |
      | sandbox_disabled account.sandbox=false                           | sandbox=false                                                                                             | account.sandbox is false                                                                                             |

  @T-UC-010-targeting @validation @post-s5
  Scenario: Capabilities response includes all targeting dimensions
    Given a tenant is resolvable from the request context
    And the adapter provides full targeting capabilities
    When the Buyer Agent calls get_adcp_capabilities
    Then media_buy.execution.targeting.geo_countries should equal true
    And media_buy.execution.targeting.geo_regions should equal true
    And media_buy.execution.targeting.geo_metros should equal {"nielsen_dma": true, "uk_itl1": true, "uk_itl2": true, "eurostat_nuts2": true}
    And media_buy.execution.targeting.geo_postal_areas should be a country-keyed map where US contains "zip"
    And media_buy.execution.targeting.age_restriction.supported should equal true
    And media_buy.execution.targeting.language should equal true
    And media_buy.execution.targeting.keyword_targets.supported_match_types should equal ["broad", "phrase", "exact"]
    And media_buy.execution.targeting.negative_keywords.supported_match_types should equal ["broad", "phrase", "exact"]
    And media_buy.execution.targeting.geo_proximity should equal {"radius": true, "travel_time": true, "geometry": true, "transport_modes": ["driving", "walking", "cycling", "public_transport"]}
    And media_buy.execution.targeting should not contain device_platform, device_type, audience_include or audience_exclude
    # POST-S5: Buyer knows execution capabilities
    # Spec-pinned Thens (salesagent-tmpd): under the full-caps fixture every dimension value is
    # knowable, so the type-only / subset lines are pinned to exact values — geo_countries /
    # geo_regions / language / age_restriction.supported = true; geo_metros = the 4-key all-true
    # object (additionalProperties: false); keyword_targets / negative_keywords match types =
    # the full [broad, phrase, exact] enum (minItems 1); geo_proximity = the full member object;
    # geo_postal_areas = the native country-keyed map. US is enum [zip, zip_plus_four] exactly
    # (postal-area-support.json's named US property) -- the [postal_code, custom] enum is a
    # DIFFERENT arm of the same schema (additionalProperties, for countries with no named
    # property), not what US uses. PRODUCTION GAP (strict xfail): _build_geo_postal_areas
    # builds the native country-keyed map correctly -- the real remaining gap is
    # age_restriction, language, keyword_targets, negative_keywords and geo_proximity, never
    # built. The scenario executes, grades geo_countries/regions/metros/postal_areas, then
    # fails at the first missing non-geo dimension (#1857).
    # 3.1.1: targeting is FLAT — there is NO nested geo object; geo_countries/geo_regions are
    # boolean siblings of the geo_metros/geo_postal_areas objects. geo_postal_areas is the
    # native country-keyed map of core/postal-area-support.json (legacy country-fused boolean
    # aliases like us_zip are deprecated: true). device_platform/device_type removed
    # ("implied by media_buy support"); audience_include/exclude moved to the presence of
    # media_buy.audience_targeting.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/execution/properties/targeting/properties
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/postal-area-support.json

  @T-UC-010-targeting-partitions @partition @boundary @targeting @post-s5
  Scenario Outline: Targeting capability configurations - <partition>
    Given a tenant is resolvable from the request context
    And the adapter provides targeting as <targeting_config>
    When the Buyer Agent calls get_adcp_capabilities
    Then media_buy.execution.targeting should satisfy <expected_targeting>
    # Concretized 2026-07-13: each row's expected column is an exact implementable assertion.
    # Postal rows re-authored to the native country-keyed map (postal-area-support.json);
    # one legacy-alias migration row retained (deprecated-compat). Emission-threshold rows
    # (nested_absent, adapter_unavailable_defaults) are NOT-IN-SPEC production contract
    # (spec-silent -> production authoritative).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/execution/properties/targeting
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/postal-area-support.json pointer=/properties/us_zip (deprecated: true)

    Examples:
      | partition                                                                            | targeting_config                                                                                             | expected_targeting                                                                                    |
      | full_adapter adapter available with full capabilities                                | all dimensions reported                                                                                      | targeting has exactly the keys geo_countries, geo_regions, geo_metros, geo_postal_areas, age_restriction, language, keyword_targets, negative_keywords and geo_proximity with geo_countries true and geo_regions true |
      | adapter_unavailable_defaults adapter unavailable (production defaults apply)         | adapter unavailable                                                                                          | targeting equals exactly {geo_countries: true, geo_regions: true}                                     |
      | partial_dimensions                                                                   | geo_countries=true, geo_regions=false, age_restriction.supported=true                                        | geo_countries true, geo_regions false, age_restriction.supported true, no other geo keys              |
      | nested_populated native postal map and metros                                        | geo_metros.nielsen_dma=true, geo_postal_areas US=["zip"]                                                     | geo_metros equals {nielsen_dma: true} and geo_postal_areas has US containing "zip"                    |
      | nested_absent no nested sub-properties declared                                      | no nested sub-properties true                                                                                | geo_metros and geo_postal_areas absent from targeting                                                 |
      | age_restriction_supported age_restriction with verification methods                  | age_restriction.supported=true, verification_methods=[id_document]                                          | age_restriction equals {supported: true, verification_methods: [id_document]}                         |
      | keyword_targeting keyword_targets with supported_match_types [broad, phrase, exact]  | keyword_targets.supported_match_types=[broad,phrase,exact], negative_keywords.supported_match_types=[exact]  | keyword_targets match types equal [broad, phrase, exact] and negative_keywords match types equal [exact] |
      | geo_proximity_supported radius and travel_time with transport modes                  | geo_proximity.radius=true, travel_time=true, geometry=false, transport_modes=[driving,walking]               | geo_proximity equals {radius: true, travel_time: true, geometry: false, transport_modes: [driving, walking]} |
      | postal_areas_native DE/CH/AT via native country-keyed map                            | geo_postal_areas DE=["plz"] CH=["plz"] AT=["plz"]                                                            | geo_postal_areas equals {DE: [plz], CH: [plz], AT: [plz]}                                             |
      | postal_areas_legacy_alias deprecated boolean aliases co-exist during migration       | geo_postal_areas.de_plz=true alongside native DE=["plz"]                                                     | geo_postal_areas has native DE containing "plz" and MAY carry the deprecated de_plz alias             |

  @T-UC-010-targeting-boundaries @boundary @targeting @post-s5
  Scenario Outline: Targeting dimension boundary - <boundary_point>
    Given a tenant is resolvable from the request context
    And the adapter provides targeting for <boundary_point>
    When the Buyer Agent calls get_adcp_capabilities
    Then media_buy.execution.targeting should satisfy <expected>
    # Concretized 2026-07-13 (the outline previously had NO expected column at all). The
    # former postal-threshold and at_plz rows duplicated targeting-partitions rows 4/5/9/10
    # and were dropped (scenario-level DRY); the surviving rows are unique coverage.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/execution/properties/targeting/properties/geo_proximity

    Examples:
      | boundary_point                                                       | expected                                                                          |
      | negative_keywords with supported_match_types [broad, phrase, exact]  | negative_keywords.supported_match_types equals [broad, phrase, exact]             |
      | geo_proximity.travel_time=true with transport_modes [cycling, public_transport] | geo_proximity.travel_time true and transport_modes equals [cycling, public_transport] |
      | geo_proximity.geometry=true (buyer-provided GeoJSON)                 | geo_proximity.geometry is true                                                    |

  @T-UC-010-degradation-partitions @partition @boundary @degradation
  Scenario Outline: Degradation path - <partition>
    Given <precondition>
    When the Buyer Agent calls get_adcp_capabilities
    Then the response should pass schema validation for get-adcp-capabilities-response
    And the response should satisfy <expected_degradation>
    # NOT-IN-SPEC / ungraded: degradation paths are production contract (the 3.1.1 storyboard
    # grades only the happy path). Every degraded response must still validate against the
    # response schema — hence the unconditional schema-validation Then. Fixes 2026-07-13:
    # no_tenant expected now includes adcp.idempotency (schema-required inside adcp — the
    # previous "minimal" shape was schema-INVALID); account_degraded re-authored to the
    # schema-legal partition (supported_billing always present and non-empty when the block
    # is emitted); adapter_fail expected reworded to portfolio.primary_channels ("channels"
    # is not a field of the 3.1.1 response).
    # Vague expected columns pinned (salesagent-tmpd): full_response → the concrete top-level
    # key-set + account.supported_billing non-empty + adcp.idempotency present; adapter_fail /
    # no_principal "default targeting" → the exact production default {geo_countries: true,
    # geo_regions: true} (spec-silent on degradation — production authoritative; the same
    # concrete value is pinned by @T-UC-010-targeting-partitions' adapter_unavailable_defaults
    # row). PRODUCTION GAPS (strict per-row xfail via conftest _SELECTIVE_XFAIL): no_tenant's
    # top-level response carries extra keys beyond the minimal contract; no_principal does not
    # degrade to [display] (INV-4 keeps the adapter principal-free, so a missing principal does
    # NOT degrade adapter-derived channels); account_degraded's account block always emits
    # require_operator_auth/sandbox as real values instead of a billing-only shape (#1856) —
    # those rows execute and fail; the rows that DO hold (adapter_fail, db_fail,
    # adapter_and_db_fail, *_absent) pass on the wire. The former "account block and
    # adcp.supported_versions are not emitted" gaps have graduated — both are now emitted.
    # db_fail/adapter_and_db_fail corrected (salesagent-piyo, 2026-08-04): production used to
    # fabricate a "<subdomain>.example.com" placeholder domain when no real PublisherPartner
    # row existed; portfolio.publisher_domains is REQUIRED+minItems:1 (pinned v3.1.1
    # get-adcp-capabilities-response.json) whenever portfolio is present, and media_buy has no
    # required fields, so the honest, schema-legal response when a DB failure means no real
    # domain data was read is to OMIT media_buy.portfolio entirely -- never a fabricated
    # domain, and (since primary_channels lives inside portfolio too) primary_channels is not
    # separately observable in that case either. adapter_fail's Given now seeds a real
    # publisher partner (unrelated to the DB-failure row) so this partition's own concern
    # (adapter channel degradation) stays testable independent of domain resolution. This
    # supersedes the earlier db_fail reading ("other sections unaffected" → placeholder
    # publisher_domains + intact [display, social, ctv] channels): there is no placeholder
    # domain any more, so neither observable exists on that row.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/adcp/required
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/account/required
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/bundled/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/portfolio/properties/publisher_domains (required, minItems 1)

    Examples:
      | partition                  | precondition                                                                | expected_degradation                                                                                                      |
      | full_response              | a tenant is resolvable and adapter and DB are available with all features   | top-level keys include adcp, supported_protocols, account, media_buy and last_updated with account.supported_billing non-empty and adcp.idempotency present |
      | no_tenant                  | no tenant can be resolved from the request context                          | only adcp and supported_protocols at top level, with adcp carrying major_versions, supported_versions and idempotency     |
      | adapter_fail               | a tenant is resolvable but adapter is unavailable                           | primary_channels equals [display] and targeting equals exactly {geo_countries: true, geo_regions: true} with no reporting_delivery_methods, audience_targeting or conversion_tracking |
      | db_fail                    | a tenant is resolvable but database query fails                             | media_buy.portfolio is omitted (no real publisher domain, never fabricated)                                                |
      | adapter_and_db_fail        | a tenant is resolvable but both adapter and DB fail                         | media_buy.portfolio is omitted (no real publisher domain, never fabricated), adapter-dependent sections absent            |
      | no_principal               | a tenant is resolvable but no auth principal available                      | primary_channels equals [display] and targeting equals exactly {geo_countries: true, geo_regions: true} with no reporting_delivery_methods, audience_targeting or conversion_tracking |
      | account_degraded           | a tenant is resolvable with partial account config                          | account present with non-empty supported_billing and no optional account fields                                           |
      | audience_targeting_absent  | a tenant is resolvable but adapter unavailable or audience targeting disabled | media_buy.audience_targeting absent                                                                                       |
      | conversion_tracking_absent | a tenant is resolvable but adapter unavailable or conversion tracking disabled | media_buy.conversion_tracking absent                                                                                      |
      | creative_absent            | a tenant is resolvable but creative not in supported_protocols              | creative section absent                                                                                                   |

  @T-UC-010-v31-supported-versions @v31 @main-flow @post-s16 @partition @boundary
  Scenario: supported-versions — release-precision supported_versions emitted alongside deprecated major_versions
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities
    Then the response should include adcp.major_versions containing 3
    And the response should include adcp.supported_versions as a non-empty array
    And each value in adcp.supported_versions should match pattern "^\d+\.\d+(-[a-zA-Z0-9.-]+)?$"
    # adcp.supported_versions (release-precision strings like "3.0", "3.1"); optional in
    # schema — presence is a seller-quality contract for a 3.1-speaking seller.
    # adcp.major_versions remains REQUIRED and MUST be emitted through 3.x (removed in 4.0).
    # POST-S16: Buyer knows release-precision AdCP versions for pinning
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/adcp/properties/supported_versions
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/adcp/properties/major_versions

  @T-UC-010-v31-build-version @v31 @main-flow @post-s17 @boundary
  Scenario: build-version — optional advisory build_version is semver and not used for negotiation
    Given a tenant is resolvable from the request context
    And the tenant declares adcp.build_version "3.1.2+scope3.deploy.4821"
    When the Buyer Agent calls get_adcp_capabilities
    Then adcp.build_version should equal "3.1.2+scope3.deploy.4821"
    And adcp.build_version should match pattern "^\d+\.\d+\.\d+(-[a-zA-Z0-9.-]+)?(\+[a-zA-Z0-9.-]+)?$"
    # adcp.build_version is advisory only; buyers MUST NOT use it for negotiation (buyer-side
    # obligation — kept as a comment, not a Then: negotiation outcome is a function of
    # supported_versions only, invariant under build_version changes).
    # POST-S17: build_version surfaced for incident triage
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/adcp/properties/build_version

  @T-UC-010-v31-idempotency-supported @v31 @main-flow @post-s15 @partition @boundary
  Scenario Outline: idempotency-supported — IdempotencySupported discriminated union shape
    Given a tenant is resolvable from the request context
    And the tenant declares idempotency posture <posture>
    When the Buyer Agent calls get_adcp_capabilities
    Then adcp.idempotency.supported should equal <supported>
    And adcp.idempotency should satisfy <expected_fields>
    # adcp.idempotency is REQUIRED (no default; sellers without it are non-compliant).
    # IdempotencySupported: replay_ttl_seconds required, integer 3600..604800 (rows 1-2 sit
    # exactly on the bounds); in_flight_max_seconds optional in 3.1 (required in 4.0);
    # account_id_is_opaque boolean default false. IdempotencyUnsupported: supported const
    # false AND replay_ttl_seconds/in_flight_max_seconds MUST be absent (not.anyOf).
    # @bva idempotency: supported=true / supported=false / replay_ttl_seconds boundary
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/adcp/properties/idempotency

    Examples:
      | partition_boundary                                                                | posture                                                                                     | supported | expected_fields                                                                       |
      | idempotency_supported supported=true with minimum replay_ttl_seconds=3600         | supported=true replay_ttl_seconds=3600                                                      | true      | replay_ttl_seconds equals 3600                                                        |
      | idempotency_supported supported=true with maximum replay_ttl_seconds=604800       | supported=true replay_ttl_seconds=604800                                                    | true      | replay_ttl_seconds equals 604800                                                      |
      | idempotency_supported supported=true with in_flight_max_seconds and opaque ids    | supported=true replay_ttl_seconds=86400 in_flight_max_seconds=300 account_id_is_opaque=true | true      | replay_ttl_seconds equals 86400, in_flight_max_seconds equals 300, account_id_is_opaque true |
      | idempotency_unsupported supported=false (no replay_ttl_seconds, no in_flight_max) | supported=false                                                                             | false     | replay_ttl_seconds absent and in_flight_max_seconds absent                            |

  @T-UC-010-v31-idempotency-required @v31 @invariant @post-s15
  Scenario: idempotency-required — adcp.idempotency is REQUIRED in v3.1 response
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities
    Then adcp.idempotency should be present in the response
    And adcp.idempotency.supported should be exactly true or false, and when false replay_ttl_seconds and in_flight_max_seconds should be absent
    # Sellers without idempotency declaration are non-compliant and unsafe for retries
    # ("Clients MUST NOT assume a default").
    # POST-S15: Buyer knows the idempotency posture (required for safe retry on mutating requests)
    # Spec-pinned Then (salesagent-tmpd): "should be a boolean discriminator" described the
    # schema ROLE, not a checkable value — replaced with the oneOf invariant: supported is
    # exactly true or false, and on the IdempotencyUnsupported branch (supported=false) the
    # schema's not.anyOf forbids replay_ttl_seconds and in_flight_max_seconds. Production
    # emits Idempotency(supported=true, replay_ttl_seconds=DEFAULT_REPLAY_TTL), so this
    # scenario passes on the wire (adcp.idempotency is REQUIRED and present).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/adcp/required
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/adcp/properties/idempotency/oneOf

  @T-UC-010-v31-idempotency-in-flight-bound @v31 @invariant @boundary
  Scenario: idempotency-in-flight-bound — in_flight_max_seconds must not exceed replay_ttl_seconds
    Given a tenant is resolvable from the request context
    And the tenant declares idempotency posture supported=true replay_ttl_seconds=86400 in_flight_max_seconds=86400
    When the Buyer Agent calls get_adcp_capabilities
    Then adcp.idempotency.in_flight_max_seconds should be less than or equal to adcp.idempotency.replay_ttl_seconds
    # Cross-field rule ("MUST be no greater than replay_ttl_seconds ... validators MUST
    # enforce this cross-field constraint at the test layer since JSON Schema cannot express
    # field-relative bounds"). Equality is allowed — this boundary row is valid.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/adcp/properties/idempotency/oneOf/0/properties/in_flight_max_seconds

  @T-UC-010-v31-supported-protocols-extended @v31 @main-flow @post-s2 @partition @boundary
  Scenario: supported-protocols-extended — v3.1 enum extends supported_protocols to seven values
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities
    Then each value in supported_protocols should be one of "media_buy", "signals", "governance", "sponsored_intelligence", "creative", "brand", "measurement"
    And if supported_protocols contains "measurement" then experimental_features should contain "measurement.core"
    # v3.1: enum adds "brand" and "measurement"; measurement is experimental in 3.1 — agents
    # implementing it MUST also list measurement.core in experimental_features.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/supported_protocols/items/enum

  @T-UC-010-v31-supports-proposals @v31 @main-flow @post-s18 @partition @boundary
  Scenario Outline: supports-proposals — proposal lifecycle commitment flag (v3.1)
    Given a tenant is resolvable from the request context
    And the tenant declares <supports_proposals_state>
    When the Buyer Agent calls get_adcp_capabilities
    Then media_buy.supports_proposals should be <expected>
    # media_buy.supports_proposals — when true, seller is graded against proposal-lifecycle
    # storyboards. Row 3 fixed 2026-07-13: the schema default documents buyer/runner
    # interpretation — a seller that omits the field emits NO key; the spec does not obligate
    # materializing false ("When false or absent, conformance runners skip proposal-lifecycle
    # storyboards"), so the omitted row asserts absent-or-false, not a forced false.
    # POST-S18: Buyer knows proposal-lifecycle support
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/supports_proposals

    Examples:
      | partition_boundary                                          | supports_proposals_state             | expected        |
      | proposals_supported guaranteed-deal seller declares true    | media_buy.supports_proposals=true    | equal to true   |
      | proposals_unsupported auction-based PG declares false       | media_buy.supports_proposals=false   | equal to false  |
      | proposals_default omitted (buyer treats as false)           | media_buy.supports_proposals omitted | absent or false |

  @T-UC-010-v31-reporting-delivery-methods @v31 @main-flow @post-s18 @partition @boundary
  Scenario Outline: reporting-delivery-methods — push-based delivery beyond baseline polling
    Given a tenant is resolvable from the request context
    And the tenant declares reporting delivery methods <methods> with offline protocols <protocols>
    When the Buyer Agent calls get_adcp_capabilities
    Then media_buy.reporting_delivery_methods should be <expected_methods>
    And media_buy.offline_delivery_protocols should be <expected_protocols>
    And webhook_signing.supported should be <webhook_signing_supported>
    # media_buy.reporting_delivery_methods enum [webhook, offline], minItems 1, uniqueItems;
    # "when absent, only polling is available". offline_delivery_protocols items from
    # enums/cloud-storage-protocol.json [s3, gcs, azure_blob], only meaningful with offline.
    # 3.1.1 cross-field (added 2026-07-13): webhook_signing.supported MUST be true when
    # reporting_delivery_methods contains "webhook" (x-adcp-validation must_equal_when).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/reporting_delivery_methods
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/webhook_signing/properties/supported/x-adcp-validation

    Examples:
      | partition_boundary                                          | methods            | protocols | expected_methods            | expected_protocols | webhook_signing_supported  |
      | polling_only methods omitted (baseline polling only)        | omitted            | omitted   | absent                      | absent             | true, false, or absent     |
      | webhook_only methods=[webhook]                              | [webhook]          | omitted   | equal to [webhook]          | absent             | equal to true              |
      | offline_only methods=[offline] with s3 protocol             | [offline]          | [s3]      | equal to [offline]          | equal to [s3]      | true, false, or absent     |
      | mixed_delivery methods=[webhook, offline] with gcs protocol | [webhook, offline] | [gcs]     | equal to [webhook, offline] | equal to [gcs]     | equal to true              |

  @T-UC-010-v31-content-standards-block @v31 @main-flow @post-s19
  Scenario: content-standards-block — media_buy.content_standards capability declares evaluation surface
    Given a tenant is resolvable from the request context
    And the tenant declares media_buy.content_standards with supports_local_evaluation=true supported_channels=["display","social"] supports_webhook_delivery=true
    When the Buyer Agent calls get_adcp_capabilities
    Then media_buy.content_standards.supports_local_evaluation should equal true
    And media_buy.content_standards.supported_channels should equal ["display", "social"]
    And media_buy.content_standards.supports_webhook_delivery should equal true
    And webhook_signing.supported should equal true
    # Given fixed 2026-07-13: the former "features.content_standards is true" flag does not
    # exist at 3.1.1 — presence of the media_buy.content_standards object IS the capability
    # signal. supports_webhook_delivery=true triggers the webhook_signing.supported MUST-true
    # cross-field (x-adcp-validation must_equal_when).
    # POST-S19: Buyer knows content_standards capability surface
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/content_standards
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/webhook_signing/properties/supported/x-adcp-validation

  @T-UC-010-v31-trusted-match-surfaces @v31 @main-flow @post-s20 @partition @boundary
  Scenario Outline: trusted-match-surfaces — TMP surfaces declaration (v3.1)
    Given a tenant is resolvable from the request context
    And the tenant declares trusted_match surfaces <surfaces>
    When the Buyer Agent calls get_adcp_capabilities
    Then media_buy.execution.trusted_match.surfaces should equal <surfaces>
    And each surface should be one of "website", "mobile_app", "ctv_app", "desktop_app", "dooh", "podcast", "radio", "streaming_audio", "ai_assistant"
    # media_buy.execution.trusted_match.surfaces (x-status experimental); presence of the
    # object indicates deployed TMP infrastructure; axe_integrations DEPRECATED.
    # POST-S20: Buyer knows the trusted_match TMP surfaces
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/execution/properties/trusted_match

    Examples:
      | partition_boundary                                      | surfaces                                       |
      | tmp_web_only website surface only                       | [website]                                      |
      | tmp_app_surfaces mobile_app and ctv_app                 | [mobile_app, ctv_app]                          |
      | tmp_audio podcast and streaming_audio surfaces          | [podcast, streaming_audio]                     |
      | tmp_ai_assistant ai_assistant surface                   | [ai_assistant]                                 |
      | tmp_full_coverage all nine TMP surfaces                  | [website, mobile_app, ctv_app, desktop_app, dooh, podcast, radio, streaming_audio, ai_assistant] |

  @T-UC-010-v31-axe-integrations-deprecated @v31 @main-flow @post-s20
  Scenario: axe-integrations-deprecated — legacy axe_integrations retained for backwards compatibility
    Given a tenant is resolvable from the request context
    And the tenant has legacy axe_integrations ["https://axe.example.com/integration"] declared
    When the Buyer Agent calls get_adcp_capabilities
    Then media_buy.execution.axe_integrations should equal ["https://axe.example.com/integration"]
    And each axe_integrations entry should be a URI
    # 3.1.1: deprecation is description-prose ("Deprecated. Legacy AXE integrations. Use
    # trusted_match for new integrations.") — no deprecated keyword; the testable seller
    # contract is exact echo of the configured legacy URIs through 3.x (the former "is
    # treated as deprecated" Then graded documentation, not the response — demoted here).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/execution/properties/axe_integrations

  @T-UC-010-v31-creative-specs @v31 @main-flow @post-s21
  Scenario: creative-specs — VAST/MRAID/VPAID/SIMID creative specification support
    Given a tenant is resolvable from the request context
    And the tenant declares creative_specs vast_versions=["4.2"] mraid_versions=["3.0"] vpaid=true simid=true
    When the Buyer Agent calls get_adcp_capabilities
    Then media_buy.execution.creative_specs.vast_versions should equal ["4.2"]
    And media_buy.execution.creative_specs.mraid_versions should equal ["3.0"]
    And media_buy.execution.creative_specs.vpaid should equal true
    And media_buy.execution.creative_specs.simid should equal true
    # Strengthened 2026-07-13 from pattern/type-only checks to exact echo of the declared
    # fixture values (item pattern ^[0-9]+\.[0-9]+$ is schema-enforced on both version arrays).
    # POST-S21: Buyer knows creative specification capabilities
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/execution/properties/creative_specs

  @T-UC-010-v31-brand-block @v31 @main-flow @post-s22 @partition @boundary
  Scenario: brand-block — brand protocol capabilities (experimental in v3.1)
    Given a tenant is resolvable from the request context
    And "brand" is in supported_protocols
    And the tenant declares brand.rights=true right_types=[talent, music] available_uses=[likeness, sync] generation_providers=["openai"]
    When the Buyer Agent calls get_adcp_capabilities
    Then the response should include brand section
    And brand.rights should equal true
    And brand.right_types should equal ["talent", "music"]
    And brand.available_uses should equal ["likeness", "sync"]
    And brand.generation_providers should equal ["openai"]
    # brand top-level block ("Only present if brand is in supported_protocols"); experimental
    # is per-field (x-status on rights/right_types/available_uses/generation_providers).
    # Strengthened (salesagent-scgh): the Given now declares a concrete brand posture and every
    # Then exact-echoes the declared fixture (was type-only/enum-membership-only). right_types
    # items ∈ enums/right-type.json enum [talent, character, brand_ip, music, stock_media];
    # available_uses items ∈ enums/right-use.json enum [likeness, voice, name, endorsement,
    # motion_capture, signature, catchphrase, sync, background_music, editorial, commercial,
    # ai_generated_image]; generation_providers is an array of open strings.
    # POST-S22: Buyer knows brand protocol capabilities
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/brand
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/right-type.json pointer=/enum
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/right-use.json pointer=/enum

  @T-UC-010-v31-request-signing-posture @v31 @main-flow @post-s23 @partition @boundary
  Scenario Outline: request-signing-posture — RFC 9421 request signing declaration
    Given a tenant is resolvable from the request context
    And the tenant declares request_signing posture <posture>
    When the Buyer Agent calls get_adcp_capabilities
    Then request_signing.supported should equal <supported>
    And request_signing.covers_content_digest should be <expected_digest>
    # request_signing.required = [supported]; covers_content_digest enum [required,
    # forbidden, either], default "either", optional — a supported=false seller typically
    # omits it (the default is buyer interpretation, not a seller emission obligation), so
    # the row-4 assert is presence-conditional; rows 1-3 exact-echo the declared value.
    # POST-S23: Buyer knows the request_signing posture
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/request_signing

    Examples:
      | partition_boundary                                                            | posture                                        | supported | expected_digest                                          |
      | signing_supported_either supported=true covers_content_digest=either          | supported=true covers_content_digest=either    | true      | equal to "either"                                        |
      | signing_required_covers_digest supported=true covers_content_digest=required  | supported=true covers_content_digest=required  | true      | equal to "required"                                      |
      | signing_forbidden_digest supported=true covers_content_digest=forbidden       | supported=true covers_content_digest=forbidden | true      | equal to "forbidden"                                     |
      | signing_unsupported supported=false (no required_for, no protocol_methods_*)  | supported=false                                | false     | absent or one of "required", "forbidden", "either"       |

  @T-UC-010-v31-request-signing-namespace-split @v31 @invariant @boundary
  Scenario: request-signing-namespace-split — AdCP tool names vs JSON-RPC method names live in separate buckets
    Given a tenant is resolvable from the request context
    And the tenant declares request_signing.supported_for=["create_media_buy"] required_for=["create_media_buy"] protocol_methods_supported_for=["tasks/cancel"] protocol_methods_required_for=["tasks/cancel"]
    When the Buyer Agent calls get_adcp_capabilities
    Then request_signing.required_for should contain only AdCP tool names without "/"
    And request_signing.protocol_methods_required_for should match pattern "^[a-z][a-z0-9_]*/[a-z][a-z0-9_]*$"
    And request_signing.required_for should be a subset of request_signing.supported_for
    And request_signing.protocol_methods_required_for should be a subset of request_signing.protocol_methods_supported_for
    # Given fixed 2026-07-13: the former fixture declared required_for/protocol_methods_
    # required_for WITHOUT their supersets — spec-invalid under the x-adcp-validation subset
    # rules; a conformant seller must never emit that posture. The no-slash rule on
    # required_for is description prose (test-layer encoding is fine); protocol_methods_*
    # items carry the schema pattern.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/request_signing/properties/required_for/x-adcp-validation
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/request_signing/properties/protocol_methods_required_for

  @T-UC-010-v31-request-signing-subset @v31 @invariant @boundary
  Scenario: request-signing-subset — required_for and warn_for must be subset of supported_for
    Given a tenant is resolvable from the request context
    And the tenant declares request_signing.supported_for=["create_media_buy", "update_media_buy"] required_for=["create_media_buy"] warn_for=["update_media_buy"]
    When the Buyer Agent calls get_adcp_capabilities
    Then request_signing.required_for should be a subset of request_signing.supported_for
    And request_signing.warn_for should be a subset of request_signing.supported_for
    And request_signing.warn_for should be disjoint from request_signing.required_for
    # The three x-adcp-validation relations (test-layer constraints — JSON Schema cannot
    # express them; a BDD assertion is precisely where the spec says enforcement lives).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/request_signing/properties/required_for/x-adcp-validation
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/request_signing/properties/warn_for/x-adcp-validation

  @T-UC-010-v31-webhook-signing @v31 @main-flow @post-s24 @partition @boundary
  Scenario Outline: webhook-signing — RFC 9421 webhook signing posture
    Given a tenant is resolvable from the request context
    And the tenant declares webhook_signing posture <posture>
    When the Buyer Agent calls get_adcp_capabilities
    Then webhook_signing.supported should equal <supported>
    And webhook_signing should satisfy <expected_extras>
    # webhook_signing: required=[supported]; profile closed enum ["adcp/webhook-signing/v1"];
    # algorithms items in {ed25519, ecdsa-p256-sha256}, minItems 1, uniqueItems;
    # legacy_hmac_fallback boolean default false. Strengthened 2026-07-13: rows 1-3 assert
    # profile equality unconditionally, row 3 asserts legacy_hmac_fallback, row 4 asserts
    # profile/algorithms ABSENT.
    # POST-S24: Buyer knows the webhook_signing posture
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/webhook_signing

    Examples:
      | partition_boundary                                                       | posture                                                                                       | supported | expected_extras                                                                    |
      | webhook_signing_ed25519 supported=true ed25519                           | supported=true profile=adcp/webhook-signing/v1 algorithms=[ed25519]                           | true      | profile equals "adcp/webhook-signing/v1" and algorithms equals [ed25519]           |
      | webhook_signing_ecdsa supported=true ecdsa-p256-sha256                   | supported=true profile=adcp/webhook-signing/v1 algorithms=[ecdsa-p256-sha256]                 | true      | profile equals "adcp/webhook-signing/v1" and algorithms equals [ecdsa-p256-sha256] |
      | webhook_signing_legacy_fallback supported=true with legacy_hmac_fallback | supported=true profile=adcp/webhook-signing/v1 algorithms=[ed25519] legacy_hmac_fallback=true | true      | legacy_hmac_fallback equals true                                                   |
      | webhook_signing_unsupported supported=false (no profile, no algorithms)  | supported=false                                                                               | false     | profile absent and algorithms absent                                               |

  @T-UC-010-v31-webhook-signing-required-when @v31 @invariant @boundary
  Scenario Outline: webhook-signing-required-when — mutating-webhook emission requires signed webhooks
    Given a tenant is resolvable from the request context
    And the tenant declares <emission_state>
    When the Buyer Agent calls get_adcp_capabilities
    Then webhook_signing.supported should be <expected>
    # One-directional rule (fixed 2026-07-13): "When the seller advertises mutating-webhook
    # emission (reporting_delivery_methods includes webhook, content_standards.
    # supports_webhook_delivery true, OR wholesale_feed_webhooks.supported true), this MUST
    # be true" — a non-emitting seller MAY still declare supported=true, so the negative row
    # asserts schema-validity for either value instead of forcing false; the third trigger
    # (wholesale_feed_webhooks) row added.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/webhook_signing/properties/supported/x-adcp-validation

    Examples:
      | partition_boundary                                                        | emission_state                                              | expected                             |
      | reporting_webhook_emission reporting_delivery_methods contains "webhook"  | media_buy.reporting_delivery_methods=[webhook]              | equal to true                        |
      | content_standards_webhook supports_webhook_delivery=true                  | media_buy.content_standards.supports_webhook_delivery=true  | equal to true                        |
      | wholesale_feed_webhook wholesale_feed_webhooks.supported=true             | wholesale_feed_webhooks.supported=true                      | equal to true                        |
      | no_mutating_webhooks neither emission path declared                       | no mutating-webhook emission                                | true or false (both schema-valid)    |

  @T-UC-010-v31-identity-brand-json-url @v31 @main-flow @post-s25 @partition @boundary
  Scenario: identity-brand-json-url — trust-root pointer required when any signing posture declared
    Given a tenant is resolvable from the request context
    And the tenant declares request_signing.supported_for=["create_media_buy"]
    And the tenant declares sponsored_intelligence.brand_url "https://brand.seller.example/render"
    When the Buyer Agent calls get_adcp_capabilities
    Then identity.brand_json_url should be present
    And identity.brand_json_url should match pattern "^https://"
    And identity.brand_json_url should be distinct from sponsored_intelligence.brand_url
    # identity.brand_json_url: format uri, pattern ^https://; required_when.any_of includes
    # request_signing.supported_for non-empty (this fixture); distinct_from
    # sponsored_intelligence.brand_url ("verifiers MUST use this field for key discovery and
    # MUST NOT fall back to sponsored_intelligence.brand_url") — the Given now declares an SI
    # brand_url so the distinctness comparison is non-vacuous. Storyboard-enforced in 3.x;
    # schema-required under 4.x supported_versions.
    # POST-S25: Trust-root pointer for signing-key discovery
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/identity/properties/brand_json_url/x-adcp-validation

  @T-UC-010-v31-identity-key-origins @v31 @main-flow @post-s25 @partition
  Scenario Outline: identity-key-origins — JWKS origin separation per signing purpose
    Given a tenant is resolvable from the request context
    And the tenant declares identity.key_origins for <purpose> at <origin>
    When the Buyer Agent calls get_adcp_capabilities
    Then identity.key_origins.<purpose> should equal <origin>
    And the same response should declare <posture_anchor>
    # identity.key_origins has exactly four purposes (additionalProperties false); purpose
    # anchoring is normative via x-adcp-validation.verifier_constraints.purpose_anchoring —
    # the vague "corresponding posture declared elsewhere" step is concretized per row.
    # Nuance: webhooks are signed with request-signing keys; the webhook_signing origin names
    # the delivery surface, not a distinct live key purpose.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/identity/properties/key_origins

    Examples:
      | partition_boundary                                        | purpose            | origin                            | posture_anchor                                   |
      | key_origin_governance governance_signing origin           | governance_signing | https://governance.seller.example | "governance" in supported_protocols              |
      | key_origin_request request_signing origin                 | request_signing    | https://signing.seller.example    | a non-empty request_signing.supported_for        |
      | key_origin_webhook webhook_signing origin                 | webhook_signing    | https://webhooks.seller.example   | webhook_signing.supported equal to true          |
      | key_origin_tmp tmp_signing origin (TMP participant only)  | tmp_signing        | https://tmp.seller.example        | non-empty trusted_match.surfaces                 |

  @T-UC-010-v31-identity-compromise-notification @v31 @main-flow @post-s25
  Scenario: identity-compromise-notification — emits/accepts flags for compromise webhook
    Given a tenant is resolvable from the request context
    And the tenant declares identity.compromise_notification.emits=true accepts=true
    When the Buyer Agent calls get_adcp_capabilities
    Then identity.compromise_notification.emits should equal true
    And identity.compromise_notification.accepts should equal true
    # Strengthened 2026-07-13 from type-only to value equality (type checks passed even with
    # inverted values). Schema: {emits, accepts} booleans default false, additionalProperties false.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/identity/properties/compromise_notification

  @T-UC-010-v31-identity-required-when-signing @v31 @invariant @boundary @post-s25
  Scenario Outline: identity-required-when-signing — signing posture without brand_json_url is rejected
    Given a tenant is resolvable from the request context
    And the tenant declares <signing_posture> with identity block <identity_state>
    When the Buyer Agent calls get_adcp_capabilities
    Then the seller capabilities builder should treat the configuration as <verdict>
    # Identity block description: "an agent declaring a signing posture elsewhere in the
    # response with an empty identity MUST be rejected by storyboard runners as missing
    # brand_json_url"; identity: {} alone is schema-valid but advisory-neutral. Enforcement
    # is storyboard/verifier-level in 3.x (schema-required only under 4.x versions) — the
    # seller-gradable behavior is refusing to emit (config-validation) the invalid posture.
    # Observable pinned (salesagent-scgh): "rejected" = the builder does NOT return a success
    # response for the invalid posture — it surfaces a CONFIGURATION_ERROR (a seller-side
    # deployment/config fault the buyer cannot fix) with recovery "terminal", naming
    # brand_json_url; "a valid capabilities response" = a success carrying adcp +
    # supported_protocols and no adcp_error. CONFIGURATION_ERROR is enums/error-code.json with
    # enumMetadata.recovery = "terminal".
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/identity
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/identity/properties/brand_json_url/x-adcp-validation/required_when
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumMetadata/CONFIGURATION_ERROR

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
    When the Buyer Agent calls get_adcp_capabilities
    Then the response should include measurement section
    And measurement.metrics should be a non-empty array
    And each metrics entry's metric_id should match pattern "^[a-z][a-z0-9_]*$" with length 1..64
    And a metrics entry should carry metric_id "viewable_impressions"
    And experimental_features should contain "measurement.core"
    # measurement (x-status experimental) requires metrics (minItems 1); items require
    # metric_id. Agents implementing measurement MUST list measurement.core in
    # experimental_features (both the measurement block and supported_protocols descriptions).
    # Strengthened (salesagent-scgh): "as a vendor-metric-id" named a schema, not a contract —
    # replaced by the schema's own item constraints (vendor-metric-id.json: pattern
    # ^[a-z][a-z0-9_]*$, minLength 1, maxLength 64; examples attention_units, demographic_reach).
    # POST-S26: Buyer knows measurement vendor metric catalog
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/measurement
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/vendor-metric-id.json pointer=/pattern

  @T-UC-010-v31-measurement-accreditations @v31 @main-flow @post-s26 @partition @boundary
  Scenario Outline: measurement-accreditations — third-party accreditation entries on metrics
    Given a tenant is resolvable from the request context
    And the tenant declares a measurement metric with accreditation <accreditation>
    When the Buyer Agent calls get_adcp_capabilities
    Then the metric accreditation should equal the declared <accreditation> fields exactly
    # accreditations[] items: accrediting_body required (open string), optional
    # certification_id, valid_until (date), evidence_url (uri); additionalProperties false.
    # The former "may include" pseudo-assert replaced by per-row exact-field equality.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/measurement/properties/metrics/items/properties/accreditations

    Examples:
      | partition_boundary                                              | accreditation                                                                                            |
      | accreditation_mrc MRC accreditation with certification_id       | accrediting_body=MRC certification_id=MRC-2026-001                                                       |
      | accreditation_arf ARF accreditation                              | accrediting_body=ARF                                                                                     |
      | accreditation_full body certification valid_until evidence_url  | accrediting_body=ABC certification_id=ABC-42 valid_until=2027-01-01 evidence_url=https://abc.org/listing |

  @T-UC-010-v31-compliance-testing @v31 @main-flow @post-s27 @partition @boundary
  Scenario: compliance-testing — compliance_testing.scenarios declares comply_test_controller support
    Given a tenant is resolvable from the request context
    And the tenant declares the compliance-testing scenarios it implements
    When the Buyer Agent calls get_adcp_capabilities
    Then compliance_testing.scenarios should be a non-empty array of strings
    And compliance_testing.scenarios should NOT contain "list_scenarios"
    And compliance_testing.scenarios should be a subset of the scenario ids returned by the seller's comply_test_controller list_scenarios call
    # Fixed 2026-07-13: at 3.1.1 scenarios items are OPEN strings — there is NO enum ("Values
    # MAY also include implementation-specific scenarios"); the former six-value closed-enum
    # assert was a pre-release snapshot artifact. Constraints: minItems 1; list_scenarios
    # excluded per description; the meaningful check is consistency with the controller's
    # runtime list_scenarios.
    # Hardened 2026-07-24 (salesagent-e4ad, triage :1118): the vague "should match a scenario
    # the ... list_scenarios returns" is restated as the precise SUBSET relation. The runtime
    # reference is the seller's comply_test_controller (schema description: "the runtime source
    # of truth remains comply_test_controller with scenario: 'list_scenarios'"). This seller
    # exposes no comply_test_controller in production (#1724) — the scenario strict-xfails on
    # the unemitted compliance_testing block.
    # POST-S27: Buyer knows compliance testing scenarios
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/compliance_testing

  @T-UC-010-v31-specialisms @v31 @main-flow @post-s28
  Scenario: specialisms — kebab-case specialism claims graded by AAO compliance runner
    Given a tenant is resolvable from the request context
    And "creative" and "media_buy" are in supported_protocols
    And the tenant claims specialisms ["creative-generative", "sales-non-guaranteed"]
    When the Buyer Agent calls get_adcp_capabilities
    Then specialisms should equal ["creative-generative", "sales-non-guaranteed"]
    And each specialism should be a member of the 3.1.1 specialism enum
    And specialism "creative-generative" should roll up to "creative" in supported_protocols
    And specialism "sales-non-guaranteed" should roll up to "media_buy" in supported_protocols
    # specialisms items $ref enums/specialism.json (closed 22-value kebab-case enum;
    # uniqueItems); roll-up rule normative in the field description ("the runner rejects a
    # specialism claim whose parent protocol is missing") — concrete mapping asserted for the
    # fixture values, whose parent protocols the Given declares.
    # POST-S28: Buyer knows the seller's specialisms
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/specialisms
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/specialism.json pointer=/enum

  @T-UC-010-v31-experimental-features @v31 @main-flow @post-s28
  Scenario: experimental-features — dot-separated ids of implemented experimental surfaces
    Given a tenant is resolvable from the request context
    And the tenant implements experimental surfaces ["brand.rights_lifecycle", "trusted_match.core"]
    When the Buyer Agent calls get_adcp_capabilities
    Then experimental_features should equal ["brand.rights_lifecycle", "trusted_match.core"]
    And each id should match pattern "^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"
    # experimental_features[] pattern-enforced, uniqueItems; both fixture ids appear in the
    # schema's own examples.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/experimental_features

  @T-UC-010-v31-advisory-errors @v31 @main-flow @post-s29
  Scenario: advisory-errors — top-level errors[] is advisory and does not fail discovery
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    And the seller surfaces an advisory warning during discovery
    When the Buyer Agent calls get_adcp_capabilities
    Then the response should include errors as an array of error objects
    And each errors entry should carry code and message
    And the response envelope status should equal "completed" and the envelope should not carry adcp_error
    # errors is a top-level optional array of core/error.json ("Task-specific errors and
    # warnings"); advisory semantics are prose-implicit (success envelope + errors-as-
    # warnings) and ungraded by the storyboard — noted as prose-implicit.
    # Hardened 2026-07-24 (salesagent-e4ad, triage :1164): "a success envelope status" is
    # restated to the pinned envelope contract — status MUST equal "completed" for this
    # synchronous read-only metadata call, and the envelope MUST NOT carry adcp_error for a
    # non-failure (non-fatal warnings populate ONLY payload.errors[] with severity warning).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/protocol-envelope.json pointer=/properties/status
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/protocol-envelope.json pointer=/properties/adcp_error
    # POST-S29: Advisory errors do not fail capabilities discovery
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/errors
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/error.json pointer=/required

  @T-UC-010-v31-creative-extended @v31 @main-flow @post-s14
  Scenario: creative-extended — creative protocol exposes library / generation / transformation in v3.1
    Given a tenant is resolvable from the request context
    And "creative" is in supported_protocols
    And the tenant declares creative posture supports_compliance=true has_creative_library=true supports_generation=false supports_transformation=false
    When the Buyer Agent calls get_adcp_capabilities
    Then creative.supports_compliance should equal true
    And creative.has_creative_library should equal true
    And creative.supports_generation should be absent or false
    And creative.supports_transformation should be absent or false
    # Fixed 2026-07-13: none of the four fields is required — "should be a boolean" failed
    # against a valid response that omits them; the Given now declares the posture and the
    # Thens assert declared==returned (absent-or-false for the false-valued defaults). The
    # 3.1.1 creative block additionally carries supports_transformers, supports_refinement,
    # supports_spend_controls, supports_evaluator, refinable_retention_seconds, multiplicity,
    # supported_formats, bills_through_adcp, canonical_catalog_version — covered by the
    # @T-UC-010-v31-creative-multiplicity / @T-UC-010-v31-creative-agentic-flags scenarios.
    # POST-S14 (v3.1 extension)
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/creative/properties

  @T-UC-010-v31-version-unsupported @v31 @extension @ext-f @error @post-f2 @post-f4 @partition
  Scenario: version-unsupported — VERSION_UNSUPPORTED error carries authoritative supported_versions
    Given a tenant is resolvable from the request context
    And the seller speaks adcp release-precision versions "3.0", "3.1"
    When the Buyer Agent calls get_adcp_capabilities with adcp_version "4.0"
    Then the wire error envelope should carry code "VERSION_UNSUPPORTED" with recovery "correctable"
    And the error details should include supported_versions as a non-empty array
    And each supported_versions entry should match pattern "^\\d+\\.\\d+(-[a-zA-Z0-9.-]+)?$"
    # Graduated: version negotiation now implemented (src/core/version_negotiation.py) — a bad
    # adcp_version/adcp_major_version pin raises AdCPVersionUnsupportedError -> VERSION_UNSUPPORTED.
    # Details shape (error-details/version-unsupported.json, "Recommended"): supported_versions
    # REQUIRED minItems 1 when the details block is emitted; supported_majors DEPRECATED
    # integer array (SHOULD-emit through 3.x — assertable when the fixture declares it);
    # build_version advisory. "suggestion" is OPTIONAL on core/error.json — the former
    # suggestion-required Then over-asserted and was dropped. Buyer-side conduct ("may re-pin
    # and retry without a second discovery round-trip") is a comment, not a Then.
    # Recovery pinned 2026-07-24 (salesagent-e4ad, triage :1197): VERSION_UNSUPPORTED is
    # "Recovery: correctable (re-pin to a release in supported_versions and retry ...)".
    # POST-F2, POST-F4
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/error-details/version-unsupported.json pointer=/required
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/error.json pointer=/required
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumDescriptions/VERSION_UNSUPPORTED

  @T-UC-010-v31-version-unsupported-major-fallback @v31 @extension @ext-f @error @post-f4 @partition
  Scenario: version-unsupported-major-fallback — major-version negotiation falls back to supported_versions
    Given a tenant is resolvable from the request context
    And the seller speaks adcp release-precision versions "3.0", "3.1"
    When the Buyer Agent calls get_adcp_capabilities with adcp_major_version 4
    Then the wire error envelope should carry code "VERSION_UNSUPPORTED" with recovery "correctable"
    And the error details should include supported_versions containing "3.0" and "3.1"
    # Graduated: version negotiation now implemented (src/core/version_negotiation.py).
    # adcp_major_version pin honored through 3.x ("Servers MUST continue to honor this field
    # through 3.x"); details still carries supported_versions. supported_majors is a
    # SHOULD-level emission (assert when the fixture emits it); "suggestion" is optional —
    # required-Then dropped 2026-07-13.
    # Recovery pinned 2026-07-24 (salesagent-e4ad, triage :1217): VERSION_UNSUPPORTED recovery
    # is correctable (re-pin to a supported release and retry).
    # POST-F4: supported_versions is authoritative even for major-version pins
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/error-details/version-unsupported.json
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumDescriptions/VERSION_UNSUPPORTED

  @T-UC-010-v31-version-unsupported-build-version-advisory @v31 @extension @ext-f @error @post-f4 @boundary
  Scenario: version-unsupported-build-version-advisory — build_version is advisory triage only
    Given a tenant is resolvable from the request context
    And the seller speaks adcp release-precision versions "3.0", "3.1"
    And the seller's build_version is "3.1.2+scope3.deploy.4821"
    When the Buyer Agent calls get_adcp_capabilities with adcp_version "4.0"
    Then the wire error envelope should carry code "VERSION_UNSUPPORTED" with recovery "correctable"
    And the error details should include build_version equal to "3.1.2+scope3.deploy.4821"
    And the error details should include supported_versions as a non-empty array
    And each supported_versions entry should match pattern "^\\d+\\.\\d+(-[a-zA-Z0-9.-]+)?$"
    # Graduated: version negotiation now implemented (src/core/version_negotiation.py).
    # Trimmed 2026-07-13 to seller-observable assertions: "Buyer Agent must select from
    # supported_versions" and "must not use build_version for negotiation" are BUYER conduct
    # a seller suite cannot grade (kept here as the spec context: "Buyers MUST NOT use this
    # field for negotiation").
    # Hardened 2026-07-24 (salesagent-jd6a, triage :1197): pinned recovery "correctable" on the
    # VERSION_UNSUPPORTED envelope (enumMetadata.recovery = correctable — "re-pin to a release
    # in supported_versions and retry") to match the sibling version-unsupported Thens, and
    # tightened the "non-empty array" duty with the release-precision pattern each entry must
    # match (build_version stays the exact advisory value; buyers MUST NOT use it for negotiation).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/error-details/version-unsupported.json pointer=/properties/build_version
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/error-details/version-unsupported.json pointer=/properties/supported_versions
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumMetadata/VERSION_UNSUPPORTED

  @T-UC-010-v31-request-signing-monotonicity @v31 @invariant @boundary @partition
  Scenario Outline: request-signing posture sets boundary - <boundary_point>
    Given a tenant is resolvable from the request context
    And the tenant declares request_signing posture sets for <boundary_point>
    When the Buyer Agent calls get_adcp_capabilities
    Then request_signing should hold the subset and disjoint relations for a <expected> posture
    # x-adcp-validation relations on request_signing: required_for subset_of supported_for;
    # warn_for disjoint_with required_for and subset_of supported_for;
    # protocol_methods_required_for subset_of protocol_methods_supported_for. These are
    # test-layer/verifier constraints — the seller-gradable behavior for invalid rows is that
    # the capabilities builder rejects/never emits the violating posture (response_schema
    # grading alone would NOT catch it).
    # Hardened 2026-07-24 (salesagent-jd6a, triage :1248): the former single Then crammed a
    # wire assertion and an unobservable "refuse to emit" into valid/invalid column words. Now
    # the <expected> column drives the graded observable: valid rows → a schema-valid success
    # whose emitted request_signing satisfies every relation (required_for ⊆ supported_for;
    # warn_for ∩ required_for = ∅ and warn_for ⊆ supported_for; protocol_methods_required_for ⊆
    # protocol_methods_supported_for); invalid rows → the builder rejects the relation-violating
    # config with CONFIGURATION_ERROR (seller-side deployment fault, recovery terminal) rather
    # than emitting the violating posture. The capabilities builder emits no request_signing
    # block today (#1291), so every row strict-xfails.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/request_signing/properties/required_for/x-adcp-validation
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/request_signing/properties/warn_for/x-adcp-validation
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/request_signing/properties/protocol_methods_required_for/x-adcp-validation
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumMetadata/CONFIGURATION_ERROR

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
    When the Buyer Agent calls get_adcp_capabilities
    Then adcp.idempotency should echo a <expected> posture within the replay_ttl_seconds bounds
    # replay_ttl_seconds integer minimum 3600 maximum 604800 (schema-enforced); cross-field
    # in_flight_max_seconds <= replay_ttl_seconds is test-layer-enforced ("validators MUST
    # enforce this cross-field constraint at the test layer") — the failing side of the
    # cross-field rule added 2026-07-13. Invalid rows grade the seller's config validation:
    # reject or normalize, never emit a non-conformant response.
    # Hardened 2026-07-24 (salesagent-jd6a, triage :1272): "valid"/"invalid" pinned nothing.
    # Now valid rows → adcp.idempotency echoes the declared posture exactly and passes schema
    # validation (replay_ttl_seconds within 3600..604800; when in_flight_max_seconds is declared
    # it is present and ≤ replay_ttl_seconds); invalid rows → the builder rejects the
    # out-of-bounds / cross-field-violating posture (CONFIGURATION_ERROR, recovery terminal).
    # Graduated: get_idempotency_posture() now returns a typed IdempotencyPosture whose
    # check_bounds() enforces the replay_ttl_seconds/in_flight_max_seconds schema bounds,
    # raising CONFIGURATION_ERROR (terminal) on the invalid rows.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/adcp/properties/idempotency/oneOf/0
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumMetadata/CONFIGURATION_ERROR

    Examples:
      | boundary_point                              | expected |
      | replay_ttl_seconds = 3599 (below min)       | invalid  |
      | replay_ttl_seconds = 604800 (max, 7d)       | valid    |
      | replay_ttl_seconds = 604801 (above max)     | invalid  |
      | in_flight_max_seconds == replay_ttl_seconds | valid    |
      | in_flight_max_seconds > replay_ttl_seconds  | invalid  |

  @T-UC-010-v31-account-sandbox @v31 @boundary @partition @post-s3
  Scenario Outline: sandbox flag boundary - <boundary_point>
    Given a tenant is resolvable from the request context
    And the tenant account is configured for <boundary_point>
    When the Buyer Agent calls get_adcp_capabilities
    Then the capabilities response should be schema-valid and account.sandbox should be <expected_value>
    # account.sandbox boolean, default false — absent means buyers treat as false. The former
    # row 4 ("capability not declared, sandbox provisioning requested → invalid") was a
    # sync_accounts obligation, not a get_adcp_capabilities behavior — rescoped to UC-011
    # 2026-07-13 (see file header).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/account/properties/sandbox

    Examples:
      | boundary_point                                          | expected_value            |
      | sandbox: true in response (sandbox account)             | equal to true             |
      | sandbox absent in response (production account)         | absent (buyer-default false) |
      | sandbox: false in response (explicit production)        | equal to false            |

  @T-UC-010-v31-version-unsupported-details-bounds @v31 @boundary @partition @error
  Scenario Outline: details (VERSION_UNSUPPORTED error) boundary - <boundary_point>
    Given a tenant is resolvable from the request context
    And the seller speaks adcp release-precision versions "3.0", "3.1"
    And the seller's error-details builder is configured for <boundary_point>
    When the Buyer Agent calls get_adcp_capabilities with adcp_version "4.0"
    Then the emitted VERSION_UNSUPPORTED details must carry supported_versions equal to ["3.0", "3.1"], never empty or omitted
    # Graduated: version negotiation now implemented (src/core/version_negotiation.py), emitting
    # a non-empty, release-precision supported_versions in VERSION_UNSUPPORTED details.
    # Rebuilt 2026-07-13: the former When ("Buyer Agent inspects the error details") was not
    # a tool call — the error is now produced via a real 4.0 pin like the scenario-family
    # above. supported_versions REQUIRED minItems 1 whenever the (Recommended) details block
    # is emitted. Former row 3 ("build_version used as negotiation input → invalid") was
    # BUYER conduct a seller suite cannot grade — dropped.
    # Hardened 2026-07-24 (salesagent-jd6a, triage :1312): the former <expected> column was
    # uniformly "invalid" and pinned nothing. Both rows are invalid-side (the error-details
    # builder is configured to emit a malformed details block), so the Then asserts the concrete
    # conformance duty a spec-correct seller owes: when the details block is emitted it MUST carry
    # a non-empty supported_versions (required, minItems 1) — here exactly the release-precision
    # versions the seller speaks, ["3.0", "3.1"] — never an empty array and never omitted.
    # Graduated: version negotiation now implemented (src/core/version_negotiation.py), emitting
    # a non-empty, release-precision supported_versions in VERSION_UNSUPPORTED details.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/error-details/version-unsupported.json pointer=/required
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/error-details/version-unsupported.json pointer=/properties/supported_versions

    Examples:
      | boundary_point                 |
      | supported_versions empty array |
      | supported_versions omitted     |

  @T-UC-010-v31-identity-brand-json-url-bounds @v31 @boundary @partition @post-s25
  Scenario Outline: identity.brand_json_url boundary - <boundary_point>
    Given a tenant is resolvable from the request context
    And the tenant identity and signing posture are configured for <boundary_point>
    When the Buyer Agent calls get_adcp_capabilities
    Then identity.brand_json_url should be graded <expected> against its required_when rule
    # Mirrors identity.brand_json_url x-adcp-validation.required_when.any_of (six signing-
    # posture signals) plus the empty-identity-with-posture rejection from the identity block
    # description. Enforcement is storyboard-level in 3.x (schema-required only under 4.x
    # supported_versions) — "invalid" = seller-side refusal to emit / validator rejection.
    # Row 2's signal is pinned to request_signing.supported_for non-empty (one of the six).
    # Hardened 2026-07-24 (salesagent-jd6a, triage :1331): the garbled single Then ("emit the
    # configuration when valid and refuse to emit it when invalid") pinned nothing. Now the
    # <expected> column drives the graded observable: valid rows → a schema-valid success and,
    # when identity.brand_json_url is emitted, it matches format uri / pattern "^https://";
    # invalid rows → the builder rejects the signing-posture-without-brand_json_url config with
    # CONFIGURATION_ERROR (recovery terminal) naming brand_json_url — the same observable decision
    # the identity-required-when-signing sibling grades. The builder never builds identity/the
    # signing posture (#1291), so the invalid rows strict-xfail (selective) while the valid rows
    # pass on the degraded-but-schema-valid baseline response.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/identity/properties/brand_json_url
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/identity/properties/brand_json_url/x-adcp-validation/required_when
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumMetadata/CONFIGURATION_ERROR

    Examples:
      | boundary_point                                                              | expected |
      | no_posture no signing posture, no brand_json_url                            | valid    |
      | posture_url_present request_signing.supported_for non-empty, url present    | valid    |
      | posture_url_absent request_signing.supported_for non-empty, url absent      | invalid  |
      | posture_identity_empty signing posture declared, identity: {}               | invalid  |

  @T-UC-010-v31-webhook-signing-bounds @v31 @boundary @partition @post-s24
  Scenario Outline: webhook_signing boundary - <boundary_point>
    Given a tenant is resolvable from the request context
    And the tenant declares webhook_signing posture described as <boundary_point>
    When the Buyer Agent calls get_adcp_capabilities
    Then webhook_signing should be graded <expected> against its must_equal_when and algorithm-enum rules
    # webhook_signing.supported must_equal_when (triggers: reporting_delivery_methods
    # contains webhook; content_standards.supports_webhook_delivery true;
    # wholesale_feed_webhooks.supported true — third trigger row added 2026-07-13);
    # algorithms closed enum {ed25519, ecdsa-p256-sha256} ("other values are reserved ...
    # MUST NOT be emitted under adcp/webhook-signing/v1"). Invalid rows grade the seller's
    # config validation / schema rejection. (@source corrected 2026-07-13: the former
    # citation pointed at core/agent-signing-key.json — wrong file.)
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/webhook_signing/properties/supported/x-adcp-validation
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/webhook_signing/properties/algorithms

    Examples:
      | boundary_point                                          | expected |
      | reporting_delivery_methods=['webhook'], supported=true  | valid    |
      | supports_webhook_delivery=true, supported=true          | valid    |
      | wholesale_feed_webhooks.supported=true, supported=true  | valid    |
      | reporting_delivery_methods=['webhook'], supported=false | invalid  |
      | supports_webhook_delivery=true, supported absent        | invalid  |
      | algorithms=['ed25519']                                  | valid    |
      | algorithms=['ecdsa-p256-sha256']                        | valid    |
      | algorithms=['rsa-pss-sha512']                           | invalid  |

  # ── New scenarios (2026-07-13, #1592 P0.2 gap closure): account block per-field ──────────
  # The account block core (supported_billing, require_operator_auth, sandbox) is emitted
  # since #1721 C2; the per-field CONFIG surface (operator-auth true, authorization_endpoint,
  # required_for_products, tenant-set sandbox) is tracked by #1856 — those rows xfail there.

  @T-UC-010-account-require-operator-auth @v31 @account @post-s3 @post-s30 @partition
  Scenario Outline: account-require-operator-auth — who must authenticate is declared up front
    Given a tenant is resolvable from the request context
    And the tenant is configured with require_operator_auth <configured>
    When the Buyer Agent calls get_adcp_capabilities
    Then account.require_operator_auth should be <expected>
    # XFAIL-EXPECTED: production gap — #1856 (account block fields beyond the legacy shape
    # are not emitted)
    # 3.1.1 semantics (major rewrite vs beta.3): the flag declares WHO must authenticate —
    # not whether OAuth/list_accounts/sync modes exist. When true, account-scoped calls use
    # seller-assigned account_id values; if a credential may access more than one account the
    # seller MUST expose list_accounts and buyers MUST resolve an explicit account_id before
    # the first account-scoped request; a singleton-credential seller SHOULD expose a
    # singleton list_accounts (MAY omit only with another declared account_id path). The
    # list_accounts exposure duty itself is graded in UC-011 — this scenario grades the
    # declaration. When false (default): buyer-declared accounts via sync_accounts natural keys.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/account/properties/require_operator_auth

    Examples:
      | partition_boundary                                       | configured | expected        |
      | operator_auth_required seller-owned account namespace    | true       | equal to true   |
      | operator_auth_not_required buyer-declared accounts       | false      | equal to false  |
      | operator_auth_default omitted (buyer treats as false)    | omitted    | absent or false |

  @T-UC-010-account-authorization-endpoint @v31 @account @post-s3 @post-s30 @partition
  Scenario Outline: account-authorization-endpoint — OAuth endpoint for operator credentials
    Given a tenant is resolvable from the request context
    And the tenant is configured with require_operator_auth true and OAuth support <oauth_state>
    When the Buyer Agent calls get_adcp_capabilities
    Then account.authorization_endpoint should be <expected>
    # XFAIL-EXPECTED: production gap — #1856 (account.authorization_endpoint not emitted)
    # Present (format uri) when the seller supports OAuth for operator authentication; if
    # absent while require_operator_auth is true, operators obtain credentials out-of-band
    # (seller portal, API key).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/account/properties/authorization_endpoint

    Examples:
      | partition_boundary                                   | oauth_state                                        | expected                                                  |
      | oauth_supported endpoint declared                    | enabled at https://auth.seller.example/authorize   | equal to "https://auth.seller.example/authorize" as a URI |
      | oauth_absent out-of-band credential onboarding       | disabled                                           | absent                                                    |

  @T-UC-010-account-required-for-products @v31 @account @post-s3 @post-s30 @partition
  Scenario Outline: account-required-for-products — whether an account gates get_products
    Given a tenant is resolvable from the request context
    And the tenant is configured with required_for_products <configured>
    When the Buyer Agent calls get_adcp_capabilities
    Then account.required_for_products should be <expected>
    # XFAIL-EXPECTED: production gap — #1856 (account.required_for_products not emitted)
    # default false: buyer can browse products without an account (price comparison and
    # discovery before committing); true requires establishing an account before get_products.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/account/properties/required_for_products

    Examples:
      | partition_boundary                                        | configured | expected        |
      | products_gated account required before browsing           | true       | equal to true   |
      | products_open browse without account                      | false      | equal to false  |
      | products_default omitted (buyer treats as false)          | omitted    | absent or false |

  @T-UC-010-account-supported-billing @v31 @account @post-s3 @post-s30 @partition @boundary
  Scenario Outline: account-supported-billing — required billing-party declaration derived from tenant config
    Given a tenant is resolvable from the request context
    And the tenant billing policy is configured as <billing_config>
    When the Buyer Agent calls get_adcp_capabilities
    Then account.supported_billing should equal <expected_set>
    And account.supported_billing should be a non-empty array
    And each supported_billing value should be one of "operator", "agent", "advertiser"
    # Graduated: account.supported_billing now derives from resolve_supported_billing(tenant)
    # on the capabilities response.
    # supported_billing is the ONLY required member of the account block (minItems 1); items
    # from enums/billing-party.json [operator, agent, advertiser]. Derivation source must be
    # the same tenant billing config _check_billing_policy reads — the value the buyer "must
    # pass in sync_accounts" has to match what billing-policy enforcement accepts (#1521
    # class drift otherwise). Note the advertiser member is exercised here (scenario-side
    # twin of #1521).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/account/properties/supported_billing
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/billing-party.json pointer=/enum

    Examples:
      | partition_boundary                                      | billing_config                        | expected_set                     |
      | billing_operator_only operator invoicing only           | operator                              | [operator]                       |
      | billing_agent_consolidated agent consolidates billing   | operator, agent                       | [operator, agent]                |
      | billing_advertiser_direct advertiser invoiced directly  | advertiser                            | [advertiser]                     |
      | billing_full_set all three billing parties              | operator, agent, advertiser           | [operator, agent, advertiser]    |

  @T-UC-010-account-financials-declaration @v31 @account @post-s3 @post-s30
  Scenario: account-financials-declaration — pre-call discriminator for get_account_financials
    Given a tenant is resolvable from the request context
    And the tenant does not expose the get_account_financials task
    When the Buyer Agent calls get_adcp_capabilities
    Then account.account_financials should be absent or false
    # account.account_financials (default false) is the pre-call discriminator: "buyers MUST
    # consult this field before issuing get_account_financials; when false (or absent),
    # sellers MAY reject the call with UNSUPPORTED_FEATURE / OPERATION_NOT_SUPPORTED". The
    # gating behavior side is graded in BR-UC-017; this scenario grades the declaration side
    # on the capabilities response: false/omitted until get_account_financials is implemented.
    # Only applicable to operator-billed accounts.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/account/properties/account_financials

  @T-UC-010-account-block-presence @v31 @account @post-s3 @post-s30 @invariant
  Scenario: account-block-presence — sellers declaring media_buy should declare the account block
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured
    When the Buyer Agent calls get_adcp_capabilities
    Then supported_protocols should contain "media_buy"
    And the account section should be present with a non-empty supported_billing
    And account.supported_billing should equal [operator, agent, advertiser]
    # Graduated: the account block is now emitted on the tenant-resolved path.
    # SHOULD-level relationship grounded in the SCHEMA — the 3.1.1 mdx docs are not part of
    # the v3.1.1 tag (schemas + compliance yaml only), so the former docs @source did not
    # resolve. media_buy.description pins it: "Expected when media_buy is in
    # supported_protocols. Sellers declaring media_buy should also include account with
    # supported_billing." account is NOT top-level-required (response #/required =
    # [adcp, supported_protocols]); the block is all-or-nothing (account/required =
    # [supported_billing], minItems 1). Under "full capabilities" the seller supports every
    # billing party, so supported_billing echoes the complete billing-party enum.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/description
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/account/required
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/billing-party.json pointer=/enum

  # ── New scenarios (2026-07-13, P0.2 gap closure): fields new at 3.1.1 ───────────────

  @T-UC-010-v31-creative-multiplicity @v31 @main-flow @post-s31 @partition
  Scenario: creative-multiplicity — build_creative fan-out pre-call discriminators
    Given a tenant is resolvable from the request context
    And "creative" is in supported_protocols
    And the tenant declares creative.multiplicity supports_catalog_fanout=true max_creatives_limit=50 supports_variants=true max_variants_limit=8 variant_dimensions=["voice","theme"]
    When the Buyer Agent calls get_adcp_capabilities
    Then creative.multiplicity.supports_catalog_fanout should equal true
    And creative.multiplicity.max_creatives_limit should equal 50
    And creative.multiplicity.supports_variants should equal true
    And creative.multiplicity.max_variants_limit should equal 8
    And creative.multiplicity.variant_dimensions should equal ["voice", "theme"]
    And each variant_dimensions value should be one of "voice", "theme", "best_of_n", "transformer_config", "custom"
    # DORMANT: no Given/Then step definitions exist for this scenario yet, so it
    # never runs — it does not xfail on a graded production gap.
    # NEW at 3.1.1: pre-call discriminators so a buyer knows BEFORE sending max_creatives /
    # max_variants whether fan-out is supported and the ceilings (over-limit requests are
    # CLAMPED, not rejected). Absent means no fan-out. Experimental members
    # (supports_signal_fanout, max_signal_conditions_limit, selection_strategies) require
    # creative.signal_fanout in experimental_features when declared — not in this fixture.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/creative/properties/multiplicity

  @T-UC-010-v31-creative-agentic-flags @v31 @main-flow @post-s31 @partition
  Scenario: creative-agentic-flags — 3.1.1 creative refinement/transformer/spend-control/evaluator discriminators
    Given a tenant is resolvable from the request context
    And "creative" is in supported_protocols
    And the tenant declares creative posture supports_refinement=true refinable_retention_seconds=86400 supports_transformers=true supports_spend_controls=true supports_evaluator=true
    When the Buyer Agent calls get_adcp_capabilities
    Then creative.supports_refinement should equal true
    And creative.refinable_retention_seconds should equal 86400
    And creative.supports_transformers should equal true
    And creative.supports_spend_controls should equal true
    And creative.supports_evaluator should equal true
    And experimental_features should contain "creative.evaluator"
    # DORMANT: no Given/Then step definitions exist for this scenario yet, so it
    # never runs — it does not xfail on a graded production gap.
    # NEW at 3.1.1, all default false: supports_refinement (refine_from_build_variant_id;
    # false/absent -> UNSUPPORTED_FEATURE), refinable_retention_seconds (guaranteed-MINIMUM
    # refinability window, integer >= 0, only meaningful with supports_refinement),
    # supports_transformers (list_transformers surface), supports_spend_controls (max_spend
    # ceiling + mode:estimate; meaningful only alongside bills_through_adcp true),
    # supports_evaluator (experimental — MUST also list creative.evaluator in
    # experimental_features, hence the coupling assert).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/creative/properties/supports_refinement
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/creative/properties/supports_evaluator

  @T-UC-010-v31-creative-approval-mode @v31 @main-flow @post-s31 @partition
  Scenario Outline: creative-approval-mode — tenant-wide creative approval applicability signal
    Given a tenant is resolvable from the request context
    And the tenant creative approval mode is configured as <configured>
    When the Buyer Agent calls get_adcp_capabilities
    Then media_buy.creative_approval_mode should be <expected>
    # Partially graded: production emits the constant require_human (shipped #1721 C5) — that row
    # passes; the <configured>=auto_approve row is strict-excluded (#1724: never claim
    # auto_approve without auto-approval behavior) and the tenant-config surface is #1856.
    # NEW at 3.1.1: closed enum [auto_approve, require_human] — not a workflow, an
    # applicability signal; require_human is a worst-case ceiling across the portfolio; when
    # ABSENT approval behavior is legacy-unspecified and runners SHOULD NOT treat omission as
    # an affirmative auto-approval claim (hence the omitted row asserts absence, not a default).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/creative_approval_mode

    Examples:
      | partition_boundary                                              | configured    | expected                  |
      | approval_auto auto_approve declared                             | auto_approve  | equal to "auto_approve"   |
      | approval_human require_human worst-case ceiling                 | require_human | equal to "require_human"  |
      | approval_unspecified omitted (legacy-unspecified, no default)   | omitted       | absent                    |

  @T-UC-010-v31-governance-aware @v31 @main-flow @post-s31 @partition
  Scenario Outline: governance-aware — opt-in to governance-denial grading
    Given a tenant is resolvable from the request context
    And the tenant governance consultation is configured as <configured>
    When the Buyer Agent calls get_adcp_capabilities
    Then media_buy.governance_aware should be <expected>
    # DORMANT: no Given/Then step definitions exist for this scenario yet, so it
    # never runs — it does not xfail on a graded production gap.
    # NEW at 3.1.1 (default false): conformance declaration that the seller consults a
    # registered governance agent (sync_governance + outbound check_governance) before
    # committing a media buy and surfaces GOVERNANCE_DENIED. true opts into governance-denial
    # grading; false/absent -> runners skip those storyboards. Independent of baseline
    # sync_governance registration.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/governance_aware

    Examples:
      | partition_boundary                                     | configured | expected        |
      | governance_consulted outbound consultation implemented | enabled    | equal to true   |
      | governance_not_consulted no outbound consultation      | disabled   | absent or false |

  @T-UC-010-v31-vendor-metric-optimization @v31 @main-flow @post-s31
  Scenario: vendor-metric-optimization — seller-level rollup of vendor-metric goal support
    Given a tenant is resolvable from the request context
    And at least one tenant product declares vendor_metric_optimization with supported targets ["cost_per"]
    When the Buyer Agent calls get_adcp_capabilities
    Then media_buy.vendor_metric_optimization should be present
    And media_buy.vendor_metric_optimization.supported_targets should equal ["cost_per"]
    And each supported_targets value should be one of "cost_per", "threshold_rate"
    # DORMANT: no Given/Then step definitions exist for this scenario yet, so it
    # never runs — it does not xfail on a graded production gap.
    # NEW at 3.1.1: seller-level rollup so buyers/runners can discover vendor_metric goal
    # scope before walking the catalog; product-level declarations remain authoritative and
    # "Sellers MUST keep this in sync with product-level vendor_metric_optimization
    # declarations". supported_targets: enum [cost_per, threshold_rate], minItems 1,
    # uniqueItems; a target-less vendor_metric goal needs no target-kind declaration.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/vendor_metric_optimization
