# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

@signals @BR-UC-008
Feature: BR-UC-008 Manage Audience Signals
  As a Buyer (via AI Agent or direct A2A)
  I want to discover and activate audience signals from the Seller Agent
  So that I can target my campaigns with relevant audience segments

  # Postconditions verified:
  #   POST-S1: Buyer knows which audience signals are available
  #   POST-S2: Buyer knows pricing options for each signal (CPM, percent_of_media, flat_fee)
  #   POST-S3: Buyer knows coverage percentage for each signal
  #   POST-S4: Buyer knows signal type and data provider
  #   POST-S5: Buyer knows deployment platforms
  #   POST-S6: Buyer receives signal_agent_segment_id for activation
  #   POST-S6a: Buyer knows value_type and categories/range
  #   POST-S6b: Sandbox mode indicated when active
  #   POST-S7: Signal activated/deactivated on specified platforms
  #   POST-S8: Buyer receives deployment results with activation keys
  #   POST-F1: System state unchanged on failure
  #   POST-F2: Buyer knows error code and message
  #   POST-F3: Application context echoed when possible

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And at least one signals agent is registered for the tenant


  @T-UC-008-main-mcp @main-flow @mcp
  Scenario: Signal discovery via MCP tool call
    Given the Buyer provides a valid signal_spec "audience segments for auto intenders"
    And destinations includes "dv360"
    And countries includes "US"
    When the Buyer Agent calls the get_signals MCP tool
    Then the response contains a non-empty signals array
    And each signal includes signal_agent_segment_id, name, description
    And each signal includes signal_type from [marketplace, custom, owned]
    And each signal includes data_provider name
    And each signal includes coverage_percentage between 0 and 100
    And each signal includes deployments array
    And each signal includes pricing_options array with at least 1 entry
    And each pricing_option includes pricing_option_id and a pricing model
    And each signal includes value_type from [binary, categorical, numeric]
    And the response is wrapped in MCP ToolResult content
    # POST-S1: Buyer knows which audience signals are available
    # POST-S2: Buyer knows pricing options (pricing_options array with pricing_option_id)
    # POST-S3: Buyer knows coverage percentage
    # POST-S4: Buyer knows signal type and data provider
    # POST-S5: Buyer knows deployment platforms
    # POST-S6: Buyer receives signal_agent_segment_id
    # POST-S6a: Buyer knows value_type and categories/range

  @T-UC-008-main-rest @main-flow @rest @a2a
  Scenario: Signal discovery via REST/A2A endpoint
    Given the Buyer provides a valid signal_spec "luxury travel"
    And destinations includes "trade-desk"
    And countries includes "GB"
    When the Buyer Agent sends a get_signals A2A task request
    Then the response contains a non-empty signals array
    And each signal includes pricing_options array with at least 1 entry
    And the response is returned directly (no ToolResult wrapper)
    # POST-S1, POST-S2, POST-S3, POST-S4, POST-S5, POST-S6

  @T-UC-008-main-sandbox @main-flow
  Scenario: Sandbox mode indicated in get_signals response
    Given the Buyer provides a valid signal_spec "audience"
    And sandbox mode is active
    When the Buyer Agent sends a get_signals request
    Then the response includes sandbox equals true
    # POST-S6b: Sandbox mode indicated when active

  @T-UC-008-main-value-type-categorical @main-flow
  Scenario: Categorical signal includes categories
    Given the Buyer provides a valid signal_spec "interest categories"
    When the Buyer Agent sends a get_signals request
    Then categorical signals include a categories array
    # POST-S6a: Buyer knows value_type and categories

  @T-UC-008-main-value-type-numeric @main-flow
  Scenario: Numeric signal includes range min/max
    Given the Buyer provides a valid signal_spec "income range"
    When the Buyer Agent sends a get_signals request
    Then numeric signals include a range object with min and max
    # POST-S6a: Buyer knows value_type and range

  @T-UC-008-main-context-echo @main-flow
  Scenario: Request context echoed in get_signals response
    Given the Buyer provides signal_spec "finance"
    And the request includes context {"trace_id": "abc-123"}
    When the Buyer Agent sends a get_signals request
    Then the response context equals {"trace_id": "abc-123"}
    # POST-F3: Application context echoed

  @T-UC-008-main-aggregation @main-flow @aggregation
  Scenario: Signals aggregated from multiple providers
    Given the tenant has 3 enabled signals agents
    And the Buyer Agent provides signal_spec "audience"
    When the Buyer Agent sends a get_signals request
    Then the response contains signals from multiple data providers
    # POST-S1, POST-S4

  @T-UC-008-main-filtered @main-flow @filtering
  Scenario: Signal discovery with all filters applied
    Given the Buyer provides signal_spec "audience"
    And filters include catalog_types ["marketplace"]
    And filters include data_providers ["Nielsen"]
    And filters include max_cpm 5.0
    And filters include max_percent 20
    And filters include min_coverage_percentage 75
    And max_results is 3
    When the Buyer Agent sends a get_signals request
    Then the response contains at most 3 signals
    And all returned signals have signal_type "marketplace"
    And all returned signals have data_provider "Nielsen"
    And all returned signals have a cpm pricing_option with cpm <= 5.0
    And all returned signals have coverage_percentage >= 75
    # POST-S1, POST-S2, POST-S3

  @T-UC-008-ext-a @extension @rest @a2a
  Scenario: No matching signals via REST -- empty result
    Given the Buyer provides signal_spec "nonexistent_category_xyz"
    And the request includes context {"trace_id": "empty-search"}
    When the Buyer Agent sends a get_signals A2A task request
    Then the response contains an empty signals array
    And the response does not contain errors
    And the response context equals {"trace_id": "empty-search"}
    # POST-F1: System state unchanged (read-only)
    # POST-F3: Context echoed

  @T-UC-008-ext-a-mcp @extension @mcp
  Scenario: No matching signals via MCP -- empty result
    Given the Buyer provides signal_spec "nonexistent_category_xyz"
    When the Buyer Agent calls the get_signals MCP tool
    Then the response contains an empty signals array
    And the response does not contain errors
    # POST-F1: System state unchanged (read-only)

  @T-UC-008-ext-b-rest @extension @rest @a2a @activation
  Scenario: Successful signal activation via REST
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "auto_intenders_q1_2025"
    And destinations include "dv360"
    When the Buyer Agent sends an activate_signal A2A task request
    Then the response contains deployments array (success variant)
    And the response does not contain errors
    And the activation includes a decisioning_platform_segment_id matching "seg_auto_intenders_q1_2025_*"
    And the activation status is "processing"
    And the activation includes estimated_activation_duration_minutes
    # POST-S7: Signal activated on specified platforms
    # POST-S8: Buyer receives deployment results with activation keys

  @T-UC-008-ext-b-mcp @extension @mcp @activation
  Scenario: Successful signal activation via MCP
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "sports_content"
    And destinations include "trade-desk"
    When the Buyer Agent calls the activate_signal MCP tool
    Then the response contains deployments array (success variant)
    And the response does not contain errors
    And the activation includes estimated_activation_duration_minutes of 15
    # POST-S7, POST-S8

  @T-UC-008-ext-b-deactivate @extension @activation
  Scenario: Signal deactivation removes segment from platforms
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "auto_intenders_q1_2025"
    And destinations include "dv360"
    And action is "deactivate"
    When the Buyer Agent sends an activate_signal request
    Then the response contains deployments array (success variant)
    And the response does not contain errors
    # POST-S7: Signal deactivated (action=deactivate)

  @T-UC-008-ext-b-pricing-option @extension @activation
  Scenario: Activation with pricing_option_id records pricing commitment
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "auto_intenders_q1_2025"
    And destinations include "dv360"
    And pricing_option_id is "opt-001"
    When the Buyer Agent sends an activate_signal request
    Then the response contains deployments array (success variant)
    And the pricing commitment is recorded for billing verification
    # POST-S7, POST-S8

  @T-UC-008-ext-b-sandbox @extension @activation
  Scenario: Sandbox mode indicated in activation response
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "auto_intenders_q1_2025"
    And destinations include "dv360"
    And sandbox mode is active
    When the Buyer Agent sends an activate_signal request
    Then the response includes sandbox equals true
    # POST-S6b analog for activation

  @T-UC-008-ext-b-context @extension @activation
  Scenario: Context echoed in successful activation response
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "auto_intenders_q1_2025"
    And destinations include "dv360"
    And the request includes context {"trace_id": "activate-trace"}
    When the Buyer Agent sends an activate_signal request
    Then the response context equals {"trace_id": "activate-trace"}
    And the response contains deployments array (success variant)
    # POST-F3: Context echoed

  @T-UC-008-ext-b-no-auth @extension @ext-b @activation @error
  Scenario: Activation rejected -- no authentication
    Given the Buyer has no authentication credentials
    And the Buyer Agent provides signal_agent_segment_id "auto_intenders_q1_2025"
    And destinations include "dv360"
    When the Buyer Agent sends an activate_signal request
    Then the system returns an error "Authentication required for signal activation"
    And the error should include "suggestion" field
    And the suggestion should contain "provide authentication credentials"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows error

  @T-UC-008-ext-c-rest @extension @ext-c @rest @a2a @activation @error
  Scenario: Premium signal requires approval via REST
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "premium_luxury_auto"
    And destinations include "dv360"
    And the request includes context {"trace_id": "premium-trace"}
    When the Buyer Agent sends an activate_signal A2A task request
    Then the response contains errors array (error variant)
    And the response does not contain deployments
    And the error code is "APPROVAL_REQUIRED"
    And the error message contains "requires manual approval"
    And the error should include "suggestion" field
    And the suggestion should contain "contact the Seller for approval"
    And the response context equals {"trace_id": "premium-trace"}
    # POST-F1: No activation occurred
    # POST-F2: Buyer knows error code (APPROVAL_REQUIRED)
    # POST-F3: Context echoed

  @T-UC-008-ext-c-mcp @extension @ext-c @mcp @activation @error
  Scenario: Premium signal requires approval via MCP
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "premium_exclusive_audience"
    And destinations include "dv360"
    When the Buyer Agent calls the activate_signal MCP tool
    Then the response contains errors array (error variant)
    And the response does not contain deployments
    And the error code is "APPROVAL_REQUIRED"
    And the error message contains "requires manual approval"
    And the error should include "suggestion" field
    And the suggestion should contain "contact the Seller for approval"
    # POST-F1: No activation occurred
    # POST-F2: Buyer knows error code
    # POST-F3: Context echoed

  @T-UC-008-ext-d-failed-rest @extension @ext-d @rest @a2a @activation @error
  Scenario: Activation fails -- provider unavailable via REST
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "unavailable_provider_signal"
    And the signal provider is unreachable
    And destinations include "dv360"
    And the request includes context {"trace_id": "fail-trace"}
    When the Buyer Agent sends an activate_signal A2A task request
    Then the response contains errors array (error variant)
    And the response does not contain deployments
    And the error code is "ACTIVATION_FAILED"
    And the error message contains "provider unavailable"
    And the error should include "suggestion" field
    And the suggestion should contain "retry later or contact support"
    And the response context equals {"trace_id": "fail-trace"}
    # POST-F1: No activation occurred
    # POST-F2: Buyer knows error code (ACTIVATION_FAILED)
    # POST-F3: Context echoed

  @T-UC-008-ext-d-failed-mcp @extension @ext-d @mcp @activation @error
  Scenario: Activation fails -- provider unavailable via MCP
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "unavailable_provider_signal"
    And the signal provider is unreachable
    And destinations include "dv360"
    When the Buyer Agent calls the activate_signal MCP tool
    Then the response contains errors array (error variant)
    And the error code is "ACTIVATION_FAILED"
    And the error should include "suggestion" field
    And the suggestion should contain "retry later or contact support"
    # POST-F1, POST-F2

  @T-UC-008-ext-d-error-rest @extension @ext-d @rest @a2a @activation @error
  Scenario: Activation fails -- unexpected error via REST
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "error_trigger_signal"
    And an unexpected error occurs during activation
    And destinations include "dv360"
    When the Buyer Agent sends an activate_signal A2A task request
    Then the response contains errors array (error variant)
    And the response does not contain deployments
    And the error code is "ACTIVATION_ERROR"
    And the error should include "suggestion" field
    And the suggestion should contain "retry or contact support"
    # POST-F1, POST-F2

  @T-UC-008-ext-d-error-mcp @extension @ext-d @mcp @activation @error
  Scenario: Activation fails -- unexpected error via MCP
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "error_trigger_signal"
    And an unexpected error occurs during activation
    And destinations include "dv360"
    When the Buyer Agent calls the activate_signal MCP tool
    Then the response contains errors array (error variant)
    And the error code is "ACTIVATION_ERROR"
    And the error should include "suggestion" field
    And the suggestion should contain "retry or contact support"
    # POST-F1, POST-F2

  @T-UC-008-ext-d-context @extension @ext-d @activation @error
  Scenario: Context echoed even on activation failure
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "premium_signal_xyz"
    And destinations include "dv360"
    And the request includes context {"trace_id": "fail-context"}
    When the Buyer Agent sends an activate_signal request
    Then the response context equals {"trace_id": "fail-context"}
    And the error code is "APPROVAL_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "contact the Seller for approval"
    # POST-F3: Context echoed

  @T-UC-008-graceful-degradation @main-flow @aggregation
  Scenario: Graceful degradation when one signals agent fails
    Given the tenant has 3 enabled signals agents
    And one signals agent returns an error
    And the Buyer Agent provides signal_spec "audience"
    When the Buyer Agent sends a get_signals request
    Then the response contains signals from the 2 healthy agents
    And the failing agent's error is logged but not returned to Buyer
    # POST-S1: Buyer still gets signals from healthy agents

  @T-UC-008-inv-047-1-holds @invariant @BR-RULE-047
  Scenario: INV-1 holds -- no filters returns all signals
    Given the Buyer provides signal_spec "audience"
    And no filters are specified
    When the Buyer Agent sends a get_signals request
    Then all available signals are returned

  @T-UC-008-inv-047-2-holds @invariant @BR-RULE-047
  Scenario: INV-2 holds -- multiple filters AND conjunction
    Given the Buyer provides signal_spec "audience"
    And filters include catalog_types ["marketplace"]
    And filters include max_cpm 5.0
    When the Buyer Agent sends a get_signals request
    Then all returned signals have signal_type "marketplace"
    And all returned signals have a cpm pricing_option with cpm <= 5.0

  @T-UC-008-inv-047-2-violated @invariant @BR-RULE-047
  Scenario: INV-2 violated -- signal fails one filter excluded even if passes others
    Given the Buyer provides signal_spec "audience"
    And filters include catalog_types ["marketplace"]
    And filters include max_cpm 1.0
    And a signal exists with signal_type "marketplace" and cpm 5.0
    When the Buyer Agent sends a get_signals request
    Then that signal is excluded because cpm exceeds max_cpm

  @T-UC-008-inv-047-3-holds @invariant @BR-RULE-047
  Scenario: INV-3 holds -- max_results limits result count
    Given the Buyer provides signal_spec "audience"
    And 10 signals match the criteria
    And max_results is 3
    When the Buyer Agent sends a get_signals request
    Then the response contains exactly 3 signals

  @T-UC-008-inv-047-3-violated @invariant @BR-RULE-047 @error
  Scenario: INV-3 violated -- max_results below minimum
    Given the Buyer provides signal_spec "audience"
    And max_results is 0
    When the Buyer Agent sends a get_signals request
    Then the request is rejected
    And the error should include "suggestion" field
    And the suggestion should contain "max_results must be >= 1"

  @T-UC-008-inv-048-1-holds @invariant @BR-RULE-048
  Scenario: INV-1 holds -- premium signal detected by prefix
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "premium_luxury_auto"
    And destinations include "dv360"
    When the Buyer Agent sends an activate_signal request
    Then the error code is "APPROVAL_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "contact the Seller for approval"

  @T-UC-008-inv-048-1-counter @invariant @BR-RULE-048
  Scenario: INV-1 counter -- non-premium signal activates normally
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "standard_auto_intenders"
    And destinations include "dv360"
    When the Buyer Agent sends an activate_signal request
    Then the response contains deployments array (success variant)
    And the response does not contain errors

  @T-UC-008-inv-048-2-holds @invariant @BR-RULE-048
  Scenario: INV-2 holds -- activation response atomic success
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "auto_intenders_q1_2025"
    And destinations include "dv360"
    When the Buyer Agent sends an activate_signal request
    Then the response contains deployments array (success variant)
    And the response does not contain errors

  @T-UC-008-inv-048-2-violated @invariant @BR-RULE-048 @error
  Scenario: INV-2 holds -- activation response atomic error
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "premium_luxury_auto"
    And destinations include "dv360"
    When the Buyer Agent sends an activate_signal request
    Then the response contains errors array (error variant)
    And the response does not contain deployments
    And the error should include "suggestion" field
    And the suggestion should contain "contact the Seller for approval"

  @T-UC-008-inv-050-1-holds @invariant @BR-RULE-050
  Scenario: INV-1 holds -- catalog_types OR within filter
    Given the Buyer provides signal_spec "audience"
    And filters include catalog_types ["marketplace", "custom"]
    And signals exist of types marketplace, custom, and owned
    When the Buyer Agent sends a get_signals request
    Then marketplace signals are returned
    And custom signals are returned
    And owned signals are excluded

  @T-UC-008-inv-050-1-counter @invariant @BR-RULE-050
  Scenario: INV-1 counter -- catalog_types omitted returns all types
    Given the Buyer provides signal_spec "audience"
    And no catalog_types filter is specified
    And signals exist of types marketplace, custom, and owned
    When the Buyer Agent sends a get_signals request
    Then signals of all types are returned

  @T-UC-008-inv-050-2-holds @invariant @BR-RULE-050
  Scenario: INV-2 holds -- data_providers OR within filter
    Given the Buyer provides signal_spec "audience"
    And filters include data_providers ["Nielsen", "LiveRamp"]
    And signals exist from Nielsen, LiveRamp, and Oracle
    When the Buyer Agent sends a get_signals request
    Then Nielsen signals are returned
    And LiveRamp signals are returned
    And Oracle signals are excluded

  @T-UC-008-inv-050-2-counter @invariant @BR-RULE-050
  Scenario: INV-2 counter -- data_providers omitted returns all providers
    Given the Buyer provides signal_spec "audience"
    And no data_providers filter is specified
    When the Buyer Agent sends a get_signals request
    Then signals from all providers are returned

  @T-UC-008-inv-050-3-holds @invariant @BR-RULE-050
  Scenario: INV-3 holds -- max_cpm excludes expensive CPM signals
    Given the Buyer provides signal_spec "audience"
    And filters include max_cpm 3.0
    And a signal has pricing_options with model="cpm" and cpm=5.0
    And another signal has pricing_options with model="cpm" and cpm=2.0
    When the Buyer Agent sends a get_signals request
    Then the signal with cpm 5.0 is excluded
    And the signal with cpm 2.0 is returned

  @T-UC-008-inv-050-3-cross @invariant @BR-RULE-050
  Scenario: INV-3 cross-model -- max_cpm does not affect percent_of_media signals
    Given the Buyer provides signal_spec "audience"
    And filters include max_cpm 1.0
    And a signal has only pricing_options with model="percent_of_media"
    When the Buyer Agent sends a get_signals request
    Then that percent_of_media signal is returned (not affected by max_cpm)

  @T-UC-008-inv-050-4-holds @invariant @BR-RULE-050
  Scenario: INV-4 holds -- min_coverage_percentage excludes low coverage
    Given the Buyer provides signal_spec "audience"
    And filters include min_coverage_percentage 80
    And a signal has coverage_percentage 50
    And another signal has coverage_percentage 90
    When the Buyer Agent sends a get_signals request
    Then the signal with coverage 50 is excluded
    And the signal with coverage 90 is returned

  @T-UC-008-inv-050-4-counter @invariant @BR-RULE-050
  Scenario: INV-4 counter -- min_coverage omitted returns all coverage levels
    Given the Buyer provides signal_spec "audience"
    And no min_coverage_percentage filter is specified
    And signals exist with coverage 10 and coverage 90
    When the Buyer Agent sends a get_signals request
    Then both signals are returned regardless of coverage

  @T-UC-008-inv-050-5-holds @invariant @BR-RULE-050
  Scenario: INV-5 holds -- signal_spec case-insensitive substring match
    Given the Buyer provides signal_spec "AUTO"
    And a signal exists with name "Auto Intenders Q1"
    When the Buyer Agent sends a get_signals request
    Then that signal is returned (case-insensitive match on name)

  @T-UC-008-inv-050-5-desc @invariant @BR-RULE-050
  Scenario: INV-5 holds -- signal_spec matches description field
    Given the Buyer provides signal_spec "luxury"
    And a signal exists with description "Luxury auto intenders 25-54"
    When the Buyer Agent sends a get_signals request
    Then that signal is returned (substring match on description)

  @T-UC-008-inv-050-6-holds @invariant @BR-RULE-050
  Scenario: INV-6 holds -- max_percent excludes high percent_of_media signals
    Given the Buyer provides signal_spec "audience"
    And filters include max_percent 15
    And a signal has pricing_options with model="percent_of_media" and percent=25
    And another signal has pricing_options with model="percent_of_media" and percent=10
    When the Buyer Agent sends a get_signals request
    Then the signal with percent 25 is excluded (ALL percent_of_media options exceed threshold)
    And the signal with percent 10 is returned

  @T-UC-008-inv-050-6-mixed @invariant @BR-RULE-050
  Scenario: INV-6 mixed -- signal with one percent option below threshold passes
    Given the Buyer provides signal_spec "audience"
    And filters include max_percent 15
    And a signal has two percent_of_media options: percent=10 and percent=25
    When the Buyer Agent sends a get_signals request
    Then that signal is returned (at least one option has percent <= max_percent)

  @T-UC-008-inv-050-6-cross @invariant @BR-RULE-050
  Scenario: INV-6 cross-model -- max_percent does not affect CPM or flat_fee signals
    Given the Buyer provides signal_spec "audience"
    And filters include max_percent 5
    And a signal has only pricing_options with model="cpm"
    And another signal has only pricing_options with model="flat_fee"
    When the Buyer Agent sends a get_signals request
    Then both signals are returned (not affected by max_percent)

  @T-UC-008-partition-signal-spec @partition @signal-spec
  Scenario Outline: Signal spec partition validation -- <partition>
    Given the Buyer provides signal_spec as <signal_spec_value>
    And signal_ids as <signal_ids_value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Valid partitions
      | partition                | signal_spec_value  | signal_ids_value | outcome                   |
      | spec_only                | "auto intenders"   | omitted          | signals returned          |
      | spec_and_ids             | "auto intenders"   | ["sig_001"]      | signals returned          |
      | spec_matches_name        | "auto"             | omitted          | matches by name substring |
      | spec_matches_description | "intend"           | omitted          | matches by description    |

    Examples: Invalid partitions
      | partition              | signal_spec_value    | signal_ids_value | outcome                  |
      | neither_spec_nor_ids   | omitted              | omitted          | validation error (anyOf) |
      | spec_no_match          | "zzz_nonexistent"    | omitted          | empty signals array      |

  @T-UC-008-partition-destinations @partition @destinations
  Scenario Outline: Destinations partition validation -- <partition>
    Given the Buyer provides signal_spec "audience"
    And destinations is <destinations_value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Valid partitions
      | partition             | destinations_value                                     | outcome          |
      | single_destination    | ["dv360"]                                              | signals returned |
      | multi_destination     | ["dv360", "trade-desk"]                                | signals returned |
      | platform_destination  | [{"type":"platform","platform":"dv360"}]               | signals returned |
      | agent_destination     | [{"type":"agent","agent_url":"https://sig.example.com"}] | signals returned |
      | omitted               | omitted                                                | signals returned |

    Examples: Invalid partitions
      | partition           | destinations_value | outcome          |
      | empty_destinations  | []                 | validation error |

  @T-UC-008-partition-countries @partition @countries
  Scenario Outline: Countries partition validation -- <partition>
    Given the Buyer provides signal_spec "audience"
    And countries is <countries_value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Valid partitions
      | partition       | countries_value | outcome          |
      | single_country  | ["US"]          | signals returned |
      | multi_country   | ["US", "GB"]    | signals returned |
      | omitted         | omitted         | signals returned |

    Examples: Invalid partitions
      | partition              | countries_value | outcome          |
      | empty_countries        | []              | validation error |
      | invalid_country_code   | ["us"]          | validation error |
      | lowercase_country_code | ["gb"]          | validation error |

  @T-UC-008-partition-catalog-types @partition @catalog-types
  Scenario Outline: Catalog types filter partition validation -- <partition>
    Given the Buyer provides signal_spec "audience"
    And catalog_types filter is <filter_value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Valid partitions
      | partition      | filter_value                     | outcome                       |
      | single_type    | ["marketplace"]                  | only marketplace signals      |
      | multiple_types | ["marketplace","custom"]         | marketplace or custom signals |
      | all_types      | ["marketplace","custom","owned"] | all signals                   |
      | omitted        | omitted                          | all catalog types returned    |

    Examples: Invalid partitions
      | partition    | filter_value | outcome                         |
      | invalid_type | ["premium"]  | validation error or empty result |

  @T-UC-008-partition-data-providers @partition @data-providers
  Scenario Outline: Data providers filter partition validation -- <partition>
    Given the Buyer provides signal_spec "audience"
    And data_providers filter is <filter_value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Valid partitions
      | partition            | filter_value           | outcome                     |
      | single_provider      | ["Nielsen"]            | only Nielsen signals        |
      | multiple_providers   | ["Nielsen","LiveRamp"] | Nielsen or LiveRamp signals |
      | omitted              | omitted                | all providers returned      |

    Examples: Invalid partitions
      | partition            | filter_value          | outcome             |
      | no_matching_provider | ["NonExistentCo"]     | empty signals array |

  @T-UC-008-partition-max-cpm @partition @max-cpm
  Scenario Outline: Max CPM filter partition validation -- <partition>
    Given the Buyer provides signal_spec "audience"
    And max_cpm filter is <max_cpm_value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Valid partitions
      | partition        | max_cpm_value | outcome                       |
      | cpm_filters_some | 3.0           | signals with cpm <= 3.0 only  |
      | cpm_zero         | 0             | only free signals             |
      | cpm_high         | 999.99        | all signals included          |
      | omitted          | omitted       | no price restriction          |

    Examples: Invalid partitions
      | partition    | max_cpm_value | outcome          |
      | cpm_negative | -1            | validation error |

  @T-UC-008-partition-max-percent @partition @max-percent
  Scenario Outline: Max percent filter partition validation -- <partition>
    Given the Buyer provides signal_spec "audience"
    And max_percent filter is <max_percent_value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Valid partitions
      | partition            | max_percent_value | outcome                                   |
      | percent_filters_some | 15                | excludes high percent_of_media signals     |
      | percent_zero         | 0                 | excludes all percent_of_media signals      |
      | percent_100          | 100               | all percent_of_media signals pass          |
      | omitted              | omitted           | no percent rate restriction                |

    Examples: Invalid partitions
      | partition         | max_percent_value | outcome          |
      | percent_negative  | -1                | validation error |
      | percent_over_100  | 101               | validation error |

  @T-UC-008-partition-min-coverage @partition @min-coverage
  Scenario Outline: Min coverage filter partition validation -- <partition>
    Given the Buyer provides signal_spec "audience"
    And min_coverage_percentage filter is <coverage_value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Valid partitions
      | partition           | coverage_value | outcome                           |
      | coverage_filters_some | 50           | signals with coverage >= 50% only |
      | coverage_zero       | 0              | all signals pass                  |
      | coverage_100        | 100            | only full coverage signals        |
      | omitted             | omitted        | no coverage restriction           |

    Examples: Invalid partitions
      | partition         | coverage_value | outcome          |
      | coverage_negative | -1             | validation error |
      | coverage_over_100 | 101            | validation error |

  @T-UC-008-partition-max-results @partition @max-results
  Scenario Outline: Max results partition validation -- <partition>
    Given the Buyer provides signal_spec "audience"
    And max_results is <max_results_value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Valid partitions
      | partition             | max_results_value | outcome                       |
      | limit_one             | 1                 | exactly 1 signal returned     |
      | limit_partial         | 2                 | at most 2 signals returned    |
      | limit_exceeds_matches | 1000              | all matching signals returned |
      | omitted               | omitted           | all matching signals returned |

    Examples: Invalid partitions
      | partition      | max_results_value | outcome          |
      | limit_zero     | 0                 | validation error |
      | limit_negative | -1                | validation error |

  @T-UC-008-partition-agent-segment-id @partition @agent-segment-id @activation
  Scenario Outline: Signal agent segment ID partition validation -- <partition>
    Given the Buyer authentication is <auth_state>
    And the Buyer Agent provides signal_agent_segment_id "<signal_id>"
    And destinations include "dv360"
    When the Buyer Agent sends an activate_signal request
    Then the outcome is <outcome>

    Examples: Valid partitions
      | partition          | auth_state    | signal_id                   | outcome                   |
      | standard_signal    | authenticated | auto_intenders_q1_2025      | success with deployments  |
      | premium_signal     | authenticated | premium_luxury_auto         | APPROVAL_REQUIRED error   |

    Examples: Invalid partitions
      | partition            | auth_state      | signal_id                   | outcome                   |
      | no_auth              | unauthenticated | auto_intenders_q1_2025      | authentication error      |
      | provider_unavailable | authenticated   | unavailable_provider_signal | ACTIVATION_FAILED error   |
      | unexpected_error     | authenticated   | error_trigger_signal        | ACTIVATION_ERROR error    |

  @T-UC-008-partition-pricing @partition @pricing
  Scenario Outline: Signal pricing model partition validation -- <partition>
    Given the Buyer provides signal_spec "audience"
    When the Buyer Agent sends a get_signals request
    Then signals with pricing model <model> include <expected_fields>

    Examples: Valid partitions
      | partition                   | model            | expected_fields                              |
      | cpm_model                   | cpm              | cpm >= 0 and currency (3-letter ISO)         |
      | percent_of_media_model      | percent_of_media | percent 0-100 and currency                   |
      | percent_of_media_with_cap   | percent_of_media | percent, currency, and max_cpm cap           |
      | flat_fee_monthly            | flat_fee         | amount >= 0, period=monthly, currency        |
      | flat_fee_quarterly          | flat_fee         | amount >= 0, period=quarterly, currency      |
      | flat_fee_annual             | flat_fee         | amount >= 0, period=annual, currency         |
      | flat_fee_campaign           | flat_fee         | amount >= 0, period=campaign, currency       |

    Examples: Invalid partitions
      | partition              | model   | expected_fields                      |
      | missing_model          | none    | schema violation (model required)    |
      | unknown_model          | unknown | schema violation (invalid model)     |
      | cpm_negative           | cpm     | cpm < 0 rejected                     |
      | percent_out_of_range   | percent_of_media | percent > 100 rejected        |
      | flat_fee_invalid_period | flat_fee | period not in enum rejected         |
      | missing_currency       | any     | currency required by all models      |

  @T-UC-008-partition-pricing-option @partition @pricing-option
  Scenario Outline: Pricing option partition validation -- <partition>
    Given the Buyer provides signal_spec "audience"
    When the Buyer Agent sends a get_signals request
    Then pricing options match <expected>

    Examples: Valid partitions
      | partition                | expected                                             |
      | cpm_option               | pricing_option_id + model=cpm pricing                |
      | percent_option           | pricing_option_id + model=percent_of_media pricing   |
      | flat_fee_option          | pricing_option_id + model=flat_fee pricing            |
      | multiple_options         | signal offers multiple pricing options                |

    Examples: Invalid partitions
      | partition                  | expected                                  |
      | missing_pricing_option_id  | pricing_option_id absent (schema error)   |
      | missing_model_fields       | model fields absent (schema error)        |

  @T-UC-008-boundary-signal-spec @boundary @signal-spec
  Scenario Outline: Signal spec boundary validation -- <boundary_point>
    Given signal_spec is <signal_spec_value>
    And signal_ids is <signal_ids_value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Boundary values
      | boundary_point                                  | signal_spec_value | signal_ids_value | outcome                  |
      | signal_spec provided alone (no signal_ids)      | "auto intenders"  | omitted          | valid request            |
      | both signal_spec and signal_ids provided        | "auto"            | ["sig_001"]      | valid request            |
      | neither signal_spec nor signal_ids provided     | omitted           | omitted          | validation error         |
      | signal_spec matches no signals                  | "zzz_nonexistent" | omitted          | empty signals (no error) |

  @T-UC-008-boundary-destinations @boundary @destinations
  Scenario Outline: Destinations boundary validation -- <boundary_point>
    Given the Buyer provides signal_spec "audience"
    And destinations is <destinations_value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Boundary values
      | boundary_point            | destinations_value                                       | outcome          |
      | 1 destination             | ["dv360"]                                                | valid request    |
      | multiple destinations     | ["dv360", "trade-desk"]                                  | valid request    |
      | destinations omitted      | omitted                                                  | valid request    |
      | 0 destinations (empty array) | []                                                    | validation error |

  @T-UC-008-boundary-countries @boundary @countries
  Scenario Outline: Countries boundary validation -- <boundary_point>
    Given the Buyer provides signal_spec "audience"
    And countries is <countries_value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Boundary values
      | boundary_point                              | countries_value | outcome          |
      | 1 country code (e.g., 'US')                 | ["US"]          | valid request    |
      | multiple country codes                      | ["US", "GB"]    | valid request    |
      | countries omitted                           | omitted         | valid request    |
      | 0 countries (empty array)                   | []              | validation error |
      | invalid country code (lowercase 'us')       | ["us"]          | validation error |
      | invalid country code (3 letters 'USA')      | ["USA"]         | validation error |

  @T-UC-008-boundary-catalog-types @boundary @catalog-types
  Scenario Outline: Catalog types filter boundary validation -- <boundary_point>
    Given the Buyer provides signal_spec "audience"
    And catalog_types filter is <filter_value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Boundary values
      | boundary_point                | filter_value                     | outcome                          |
      | single type 'marketplace'     | ["marketplace"]                  | marketplace signals only         |
      | all three types               | ["marketplace","custom","owned"] | all signals                      |
      | filter omitted                | omitted                          | all catalog types                |
      | invalid type value            | ["premium"]                      | validation error or empty result |

  @T-UC-008-boundary-data-providers @boundary @data-providers
  Scenario Outline: Data providers filter boundary validation -- <boundary_point>
    Given the Buyer provides signal_spec "audience"
    And data_providers filter is <filter_value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Boundary values
      | boundary_point              | filter_value           | outcome                  |
      | single provider name        | ["Nielsen"]            | only Nielsen signals     |
      | multiple provider names     | ["Nielsen","LiveRamp"] | signals from either      |
      | filter omitted              | omitted                | all providers            |
      | non-existent provider name  | ["NonExistentCo"]      | empty signals (no error) |

  @T-UC-008-boundary-max-cpm @boundary @max-cpm
  Scenario Outline: Max CPM filter boundary validation -- <boundary_point>
    Given the Buyer provides signal_spec "audience"
    And max_cpm filter is <value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Boundary values
      | boundary_point    | value   | outcome                |
      | max_cpm = 0       | 0       | only free signals      |
      | max_cpm = 0.01    | 0.01    | very low price ceiling |
      | max_cpm omitted   | omitted | no price restriction   |
      | max_cpm = -1      | -1      | validation error       |

  @T-UC-008-boundary-max-percent @boundary @max-percent
  Scenario Outline: Max percent filter boundary validation -- <boundary_point>
    Given the Buyer provides signal_spec "audience"
    And max_percent filter is <value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Boundary values
      | boundary_point       | value   | outcome                           |
      | max_percent = 0      | 0       | excludes all percent_of_media     |
      | max_percent = 15     | 15      | excludes high percent signals     |
      | max_percent = 100    | 100     | all percent_of_media signals pass |
      | max_percent omitted  | omitted | no percent rate restriction       |
      | max_percent = -1     | -1      | validation error                  |
      | max_percent = 101    | 101     | validation error                  |

  @T-UC-008-boundary-min-coverage @boundary @min-coverage
  Scenario Outline: Min coverage filter boundary validation -- <boundary_point>
    Given the Buyer provides signal_spec "audience"
    And min_coverage_percentage is <value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Boundary values
      | boundary_point           | value   | outcome            |
      | min_coverage = 0         | 0       | all signals pass   |
      | min_coverage = 100       | 100     | only full coverage |
      | min_coverage = 50        | 50      | medium threshold   |
      | min_coverage omitted     | omitted | no restriction     |
      | min_coverage = -1        | -1      | validation error   |
      | min_coverage = 101       | 101     | validation error   |

  @T-UC-008-boundary-max-results @boundary @max-results
  Scenario Outline: Max results boundary validation -- <boundary_point>
    Given the Buyer provides signal_spec "audience"
    And max_results is <value>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples: Boundary values
      | boundary_point                       | value   | outcome          |
      | max_results = 1                      | 1       | single signal    |
      | max_results = 1000 (exceeds matches) | 1000    | all signals      |
      | max_results omitted                  | omitted | all signals      |
      | max_results = 0                      | 0       | validation error |

  @T-UC-008-boundary-agent-segment-id @boundary @agent-segment-id @activation
  Scenario Outline: Activation boundary validation -- <boundary_point>
    Given the Buyer authentication is <auth_state>
    And the Buyer Agent provides signal_agent_segment_id "<signal_id>"
    And destinations include "dv360"
    When the Buyer Agent sends an activate_signal request
    Then the outcome is <outcome>

    Examples: Boundary values
      | boundary_point                             | auth_state      | signal_id                   | outcome                   |
      | standard signal ID with auth               | authenticated   | auto_intenders_q1_2025      | success with deployments  |
      | premium_ prefixed signal ID                | authenticated   | premium_luxury_auto         | APPROVAL_REQUIRED error   |
      | activation without authentication          | unauthenticated | auto_intenders_q1_2025      | authentication error      |
      | provider unavailable during activation     | authenticated   | unavailable_provider_signal | ACTIVATION_FAILED error   |

  @T-UC-008-boundary-pricing @boundary @pricing
  Scenario Outline: Signal pricing boundary validation -- <boundary_point>
    Given the Buyer provides signal_spec "audience"
    When the Buyer Agent sends a get_signals request
    Then pricing at boundary <boundary_point> yields <outcome>

    Examples: Boundary values
      | boundary_point                                                      | outcome          |
      | model='cpm', cpm=0, currency='USD'                                  | valid            |
      | model='percent_of_media', percent=0, currency='USD'                 | valid            |
      | model='percent_of_media', percent=100, currency='USD'               | valid            |
      | model='percent_of_media', percent=15, max_cpm=5.0, currency='USD'   | valid            |
      | model='flat_fee', amount=0, period='monthly', currency='USD'        | valid            |
      | model='flat_fee', amount=1000, period='campaign', currency='EUR'    | valid            |
      | model='cpm', cpm=-1, currency='USD'                                 | invalid          |
      | model='percent_of_media', percent=101, currency='USD'               | invalid          |

  @T-UC-008-boundary-pricing-option @boundary @pricing-option
  Scenario Outline: Pricing option boundary validation -- <boundary_point>
    Given the Buyer provides signal_spec "audience"
    When the Buyer Agent sends a get_signals request
    Then pricing option at boundary <boundary_point> yields <outcome>

    Examples: Boundary values
      | boundary_point                                                                    | outcome |
      | pricing_option_id='opt-001', model='cpm', cpm=2.50, currency='USD'                | valid   |
      | pricing_option_id='opt-002', model='percent_of_media', percent=15, currency='USD'  | valid   |
      | pricing_option_id='opt-003', model='flat_fee', amount=500, period='monthly', currency='EUR' | valid |
      | No pricing_option_id                                                               | invalid |

  @T-UC-008-gap-missing-segment-id @extension @ext-b @activation @error
  Scenario: Activation rejected -- missing signal_agent_segment_id
    Given the Buyer is authenticated with a valid principal_id
    And no signal_agent_segment_id is provided
    And destinations include "dv360"
    When the Buyer Agent sends an activate_signal request
    Then the request is rejected with a validation error
    And the error should include "suggestion" field
    And the suggestion should contain "provide signal_agent_segment_id"
    # PRE-B7 violation: signal_agent_segment_id required
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows error

  @T-UC-008-gap-empty-activate-destinations @extension @ext-b @activation @error
  Scenario: Activation rejected -- empty destinations array
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "auto_intenders_q1_2025"
    And destinations is an empty array
    When the Buyer Agent sends an activate_signal request
    Then the request is rejected with a validation error
    And the error should include "suggestion" field
    And the suggestion should contain "provide at least one destination"
    # PRE-B8 violation: destinations minItems: 1
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows error

  @T-UC-008-gap-invalid-action @extension @ext-b @activation @error
  Scenario: Activation rejected -- invalid action value
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "auto_intenders_q1_2025"
    And destinations include "dv360"
    And action is "invalid_action"
    When the Buyer Agent sends an activate_signal request
    Then the request is rejected with a validation error
    And the error should include "suggestion" field
    And the suggestion should contain "action must be activate or deactivate"
    # PRE-B8a violation: action must be "activate" or "deactivate"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows error

  @T-UC-008-gap-default-action @extension @activation
  Scenario: Activation with omitted action defaults to activate
    Given the Buyer is authenticated with a valid principal_id
    And the Buyer Agent provides signal_agent_segment_id "auto_intenders_q1_2025"
    And destinations include "dv360"
    And action is omitted
    When the Buyer Agent sends an activate_signal request
    Then the response contains deployments array (success variant)
    And the signal is activated (not deactivated)
    # PRE-B8a: action default is "activate"

  @T-UC-008-gap-flat-fee-max-cpm @invariant @BR-RULE-050
  Scenario: INV-3 cross-model -- max_cpm does not affect flat_fee signals
    Given the Buyer provides signal_spec "audience"
    And filters include max_cpm 1.0
    And a signal has only pricing_options with model="flat_fee"
    When the Buyer Agent sends a get_signals request
    Then that flat_fee signal is returned (not affected by max_cpm)

  @T-UC-008-dep-spec-ids @dependency
  Scenario Outline: signal_spec and signal_ids dependency -- <case>
    Given signal_spec is <spec>
    And signal_ids is <ids>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples:
      | case                | spec             | ids          | outcome                  |
      | spec_only           | "auto intenders" | omitted      | valid (spec search)      |
      | ids_only            | omitted          | ["sig_001"]  | valid (ID lookup)        |
      | both_provided       | "auto"           | ["sig_001"]  | valid (combined)         |
      | neither_provided    | omitted          | omitted      | validation error (anyOf) |

  @T-UC-008-dep-destinations-countries @dependency
  Scenario Outline: destinations and countries independence -- <case>
    Given the Buyer provides signal_spec "audience"
    And destinations is <destinations>
    And countries is <countries>
    When the Buyer Agent sends a get_signals request
    Then the outcome is <outcome>

    Examples:
      | case                 | destinations | countries | outcome                            |
      | both_provided        | ["dv360"]    | ["US"]    | signals filtered by both           |
      | destinations_only    | ["dv360"]    | omitted   | signals filtered by platform only  |
      | countries_only       | omitted      | ["US"]    | signals filtered by country only   |
      | neither_provided     | omitted      | omitted   | all signals returned               |

