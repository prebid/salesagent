# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

Feature: BR-UC-009 Update Performance Index
  As a Buyer (via AI Agent)
  I want to provide performance feedback for my active media buys
  So that the Seller can optimize future delivery, adjust targeting, and refine pricing

  # Postconditions verified:
  #   POST-S1: Performance feedback is accepted and forwarded to the ad server adapter
  #   POST-S2: Buyer knows the feedback was successfully processed (status confirmation)
  #   POST-S3: Adapter has received the performance index mapped to the relevant package(s)
  #   POST-S4: Operation is audit-logged with principal, media buy, product count, avg index
  #   POST-F1: System state is unchanged on failure (no partial performance updates)
  #   POST-F2: Buyer knows what failed and the specific error information
  #   POST-F3: Application context is echoed in the response when provided

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And the Buyer is authenticated with a valid principal_id


  # RECONCILED (#1442, local edit — mirror upstream in adcp-req): transport
  # tag dropped so this success scenario parametrizes across ALL wire transports
  # (impl/a2a/mcp/rest); no Then step here is transport-shaped. The storyboard
  # narrates MCP, but the graded contract is transport-uniform.
  @T-UC-009-main-mcp @main-flow
  Scenario: Submit performance feedback via MCP - success
    Given the Buyer owns media buy "mb_perf_001"
    And the media buy has products ["pkg_display_001"]
    When the Buyer Agent calls update_performance_index MCP tool with:
    | media_buy_id | performance_data                                              |
    | mb_perf_001  | [{"product_id": "pkg_display_001", "performance_index": 1.2}] |
    Then the operation should succeed
    And the response success field should be true
    # POST-S1: Feedback accepted and forwarded to adapter
    # POST-S2: Buyer receives success: true confirmation (v3.1 Success branch)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-main-mcp-adapter @main-flow @mcp @adapter
  Scenario: MCP feedback is forwarded to adapter with mapped packages
    Given the Buyer owns media buy "mb_perf_002"
    And the media buy has products ["pkg_social_001", "pkg_video_001"]
    When the Buyer Agent calls update_performance_index MCP tool with:
    | media_buy_id | performance_data                                                                                                       |
    | mb_perf_002  | [{"product_id": "pkg_social_001", "performance_index": 0.9}, {"product_id": "pkg_video_001", "performance_index": 1.5}] |
    Then the adapter should receive PackagePerformance entries for:
    | package_id     | performance_index |
    | pkg_social_001 | 0.9               |
    | pkg_video_001  | 1.5               |
    # POST-S3: Adapter receives performance index mapped to packages
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-main-mcp-audit @main-flow @mcp @audit
  Scenario: MCP performance feedback is audit-logged
    Given the Buyer owns media buy "mb_perf_003"
    And the media buy has products ["pkg_001"]
    When the Buyer Agent calls update_performance_index MCP tool with:
    | media_buy_id | performance_data                                        |
    | mb_perf_003  | [{"product_id": "pkg_001", "performance_index": 0.85}] |
    Then the operation should succeed
    And the audit log should contain an entry with:
    | field           | value       |
    | media_buy_id    | mb_perf_003 |
    | product_count   | 1           |
    | avg_performance | 0.85        |
    # POST-S4: Operation audit-logged with principal, media buy, count, avg

  # RECONCILED (#1442, local edit — mirror upstream in adcp-req): transport
  # tags dropped so this success scenario parametrizes across ALL wire transports —
  # previously @rest @a2a suppressed parametrization and the step pinned A2A, so
  # Transport.REST executed for ZERO UC-009 scenarios.
  @T-UC-009-main-rest @main-flow
  Scenario: Submit performance feedback via A2A - success
    Given the Buyer owns media buy "mb_perf_004"
    And the media buy has products ["pkg_display_002"]
    When the Buyer Agent sends update_performance_index A2A skill request with:
    | media_buy_id | performance_data                                              |
    | mb_perf_004  | [{"product_id": "pkg_display_002", "performance_index": 1.0}] |
    Then the operation should succeed
    And the response success field should be true
    # POST-S1: Feedback accepted and forwarded
    # POST-S2: Buyer receives success: true confirmation (v3.1 Success branch)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-main-rest-adapter @main-flow @rest @adapter
  Scenario: A2A feedback is forwarded to adapter with mapped packages
    Given the Buyer owns media buy "mb_perf_005"
    And the media buy has products ["pkg_audio_001"]
    When the Buyer Agent sends update_performance_index A2A skill request with:
    | media_buy_id | performance_data                                            |
    | mb_perf_005  | [{"product_id": "pkg_audio_001", "performance_index": 0.6}] |
    Then the adapter should receive PackagePerformance entries for:
    | package_id    | performance_index |
    | pkg_audio_001 | 0.6               |
    # POST-S3: Adapter receives mapped packages

  @T-UC-009-batch @main-flow @batch
  Scenario: Batch performance feedback for multiple products
    Given the Buyer owns media buy "mb_batch_001"
    And the media buy has products ["pkg_a", "pkg_b", "pkg_c"]
    When the Buyer Agent submits performance feedback for multiple products:
    | product_id | performance_index |
    | pkg_a      | 1.2               |
    | pkg_b      | 0.5               |
    | pkg_c      | 0.8               |
    Then the operation should succeed
    And all 3 products should be forwarded to the adapter
    And the audit log should show product_count 3
    And the audit log should show avg_performance 0.833
    # POST-S1, POST-S3: All products forwarded
    # POST-S4: Audit with correct count and average
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-single @main-flow @batch
  Scenario: Single product performance feedback
    Given the Buyer owns media buy "mb_single_001"
    And the media buy has products ["pkg_solo"]
    When the Buyer Agent submits performance feedback:
    | product_id | performance_index |
    | pkg_solo   | 0.9               |
    Then the operation should succeed
    And 1 product should be forwarded to the adapter
    # POST-S1: Single product forwarded
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-idempotent @main-flow @idempotent @extension @ext-e @BR-RULE-081
  Scenario: Duplicate performance feedback submission succeeds
    Given the Buyer owns media buy "mb_idempotent_001"
    And the Buyer has already submitted performance feedback for "mb_idempotent_001"
    When the Buyer Agent submits the same performance feedback again for "mb_idempotent_001"
    Then the operation should succeed
    And the latest feedback should be applied
    # Update operation is idempotent — latest value wins

  @T-UC-009-inv-018-1 @invariant @BR-RULE-018
  Scenario: INV-1 holds - successful response contains only success fields
    Given the Buyer owns media buy "mb_atomic_001"
    When the Buyer Agent submits valid performance feedback for "mb_atomic_001"
    Then the response success field should be true
    And the response should NOT contain an errors array
    # BR-RULE-018 INV-1: Successful operation -> success: true (const), no errors[]
    # v3.1 wire: Success branch carries {success: true, optional sandbox, context, ext} only — no status/detail fields
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-inv-018-2 @invariant @BR-RULE-018 @error
  Scenario: INV-2 holds - error response contains only error fields
    Given the Buyer owns media buy "mb_atomic_002"
    When the Buyer Agent submits performance feedback with invalid data for "mb_atomic_002"
    Then the response should contain error information
    And the response should NOT contain a success field
    And the error should include "suggestion" field
    And the suggestion should contain "correct format"
    # BR-RULE-018 INV-2: Validation failure -> errors[] populated, no success/sandbox fields
    # v3.1 wire: Error branch carries {errors[], optional context, ext} only — no success/sandbox/status fields

  @T-UC-009-inv-021-1 @invariant @BR-RULE-021
  Scenario: INV-1 holds - media_buy_id resolves target
    Given the Buyer owns media buy "mb_xor_001"
    When the Buyer Agent submits performance feedback with media_buy_id "mb_xor_001"
    Then the operation should succeed
    # BR-RULE-021 INV-1: Exactly one identifier -> resolves

  @T-UC-009-inv-021-3 @invariant @BR-RULE-021 @error
  Scenario: INV-3 violated - media_buy_id missing
    When the Buyer Agent submits performance feedback without media_buy_id
    Then the operation should fail with a validation error
    And the error code should be "INVALID_REQUEST"
    And the error should indicate that media_buy_id is required
    And the error should include "suggestion" field
    And the suggestion should contain "provide media_buy_id"
    # BR-RULE-021 INV-3: media_buy_id missing -> schema rejects (v3.1 required field)

  @T-UC-009-inv-043-1 @invariant @BR-RULE-043 @context
  Scenario: INV-1 holds - context provided is echoed in success response
    Given the Buyer owns media buy "mb_ctx_001"
    When the Buyer Agent submits performance feedback for "mb_ctx_001" with context:
    | session_id | trace_id    |
    | sess_abc   | trace_12345 |
    Then the operation should succeed
    And the response context should contain session_id "sess_abc"
    And the response context should contain trace_id "trace_12345"
    # BR-RULE-043 INV-1: Context provided -> echoed unchanged
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-inv-043-2 @invariant @BR-RULE-043 @context
  Scenario: INV-2 holds - context omitted means no context in response
    Given the Buyer owns media buy "mb_ctx_002"
    When the Buyer Agent submits performance feedback for "mb_ctx_002" without context
    Then the operation should succeed
    And the response should not contain a context field
    # BR-RULE-043 INV-2: Context omitted -> omitted from response

  @T-UC-009-inv-043-err @invariant @BR-RULE-043 @context @error
  Scenario: INV-1 holds on failure - context echoed in error response
    Given the Buyer owns media buy "mb_ctx_003"
    And the adapter will fail to process performance updates
    When the Buyer Agent submits performance feedback for "mb_ctx_003" with context:
    | session_id |
    | sess_fail  |
    Then the operation should fail
    And the error code should be "SERVICE_UNAVAILABLE"
    And the response context should contain session_id "sess_fail"
    And the response should include suggestion to retry
    # BR-RULE-043 INV-1: Context echoed even on failure path
    # POST-F3: Application context echoed in response

  @T-UC-009-perf-index @validation @performance-index @partition @boundary @invariant @BR-RULE-051
  Scenario Outline: Performance index validation - <partition>
    Given the Buyer owns media buy "mb_idx_val"
    When the Buyer Agent submits performance feedback with performance_index <value>
    Then the outcome should be <outcome>
    And <assertion>
    # Valid partitions (DR-3)
    # Invalid partitions (INV-2)
    # Boundary values (DR-4)

    Examples: Valid - no optimization
      | partition        | value | outcome | assertion                            |
      | zero_value       | 0.0   | success | no optimization flag is set           |
      | at_threshold     | 0.8   | success | no optimization flag is set           |
      | above_threshold  | 0.85  | success | no optimization flag is set           |
      | at_baseline      | 1.0   | success | performance is treated as baseline    |
      | above_baseline   | 1.5   | success | performance is above expected         |

    Examples: Valid - triggers optimization (INV-3)
      | partition        | value | outcome | assertion                            |
      | low_performance  | 0.3   | success | system flags low performance          |
      | below_baseline   | 0.5   | success | system flags low performance          |

    Examples: Invalid - below minimum
      | partition       | value | outcome          | assertion                    |
      | negative_value  | -0.5  | validation error | schema rejects negative index |

    Examples: Boundaries
      | partition                       | value | outcome          | assertion                            |
      | -0.01 (below minimum)           | -0.01 | validation error | schema rejects negative index         |
      | 0.0 (minimum boundary)          | 0.0   | success          | minimum boundary accepted              |
      | 0.79 (just below optimization threshold) | 0.79 | success | system flags low performance           |
      | 0.8 (optimization threshold)    | 0.8   | success          | no optimization flag is set            |
      | 1.0 (baseline)                  | 1.0   | success          | performance is treated as baseline     |
      | 1.01 (above baseline)           | 1.01  | success          | performance is above expected          |

  @T-UC-009-meas-period @validation @measurement-period @partition @boundary @BR-RULE-281
  Scenario Outline: Measurement period validation - <partition>
    Given the Buyer owns media buy "mb_period_val"
    When the Buyer Agent submits performance feedback with measurement_period <start> to <end>
    Then the outcome should be <outcome>

    Examples: Valid
      | partition      | start                | end                      | outcome |
      | normal_period  | 2024-01-01T00:00:00Z | 2024-01-31T23:59:59Z     | success |

    Examples: Invalid
      | partition       | start                | end                  | outcome          |
      | same_instant    | 2024-06-15T12:00:00Z | 2024-06-15T12:00:00Z | validation error |
      | end_before_start| 2024-06-30T00:00:00Z | 2024-06-01T00:00:00Z | validation error |
      | missing_start   |                      | 2024-01-31T23:59:59Z | validation error |
      | missing_end     | 2024-01-01T00:00:00Z |                      | validation error |
      | invalid_format  | not-a-date           | 2024-01-31T23:59:59Z | validation error |

    Examples: Boundaries
      | partition                                                                       | start                | end                  | outcome          |
      | Valid period (start=2024-01-01T00:00:00Z, end=2024-01-31T23:59:59Z)             | 2024-01-01T00:00:00Z | 2024-01-31T23:59:59Z | success          |
      | Same instant (start == end) [BR-RULE-281 INV-5: now rejected in v3.1]           | 2024-06-15T12:00:00Z | 2024-06-15T12:00:00Z | validation error |
      | end before start [BR-RULE-281 INV-5]                                            | 2024-06-30T00:00:00Z | 2024-06-01T00:00:00Z | validation error |
      | Missing start field                                                             |                      | 2024-01-31T23:59:59Z | validation error |
      | Missing end field                                                               | 2024-01-01T00:00:00Z |                      | validation error |
      | Non-datetime string for start                                                   | not-a-date           | 2024-01-31T23:59:59Z | validation error |

  @T-UC-009-metric-type @validation @metric-type @partition @boundary @BR-RULE-282
  Scenario Outline: Metric type validation - <partition>
    Given the Buyer owns media buy "mb_metric_val"
    When the Buyer Agent submits performance feedback with metric_type "<value>"
    Then the outcome should be <outcome>

    Examples: Valid enum values
      | partition             | value               | outcome |
      | overall_performance   | overall_performance | success |
      | conversion_rate (legacy v3.0 enum) | conversion_rate     | success |
      | brand_lift            | brand_lift          | success |
      | click_through_rate    | click_through_rate  | success |
      | completion_rate       | completion_rate     | success |
      | viewability           | viewability         | success |
      | brand_safety          | brand_safety        | success |
      | cost_efficiency       | cost_efficiency     | success |
      | not_provided          |                     | success |

    Examples: Invalid
      | partition    | value           | outcome          |
      | unknown_type | engagement_rate | validation error |

    Examples: Boundaries
      | partition                         | value               | outcome          |
      | overall_performance (default)     | overall_performance | success          |
      | cost_efficiency (last enum value) | cost_efficiency     | success          |
      | Not provided (uses default)       |                     | success          |
      | Unknown string not in enum        | engagement_rate     | validation error |

  @T-UC-009-fb-source @validation @feedback-source @partition @boundary
  Scenario Outline: Feedback source validation - <partition>
    Given the Buyer owns media buy "mb_source_val"
    When the Buyer Agent submits performance feedback with feedback_source "<value>"
    Then the outcome should be <outcome>

    Examples: Valid enum values
      | partition               | value                   | outcome |
      | buyer_attribution       | buyer_attribution       | success |
      | third_party_measurement | third_party_measurement | success |
      | platform_analytics      | platform_analytics      | success |
      | verification_partner    | verification_partner    | success |
      | not_provided            |                         | success |

    Examples: Invalid
      | partition      | value        | outcome          |
      | unknown_source | manual_entry | validation error |

    Examples: Boundaries
      | partition                              | value                   | outcome          |
      | buyer_attribution (default)            | buyer_attribution       | success          |
      | verification_partner (last enum value) | verification_partner    | success          |
      | Not provided (uses default)            |                         | success          |
      | Unknown string not in enum             | manual_entry            | validation error |

  @T-UC-009-pkg-id @validation @package-id @partition @boundary
  Scenario Outline: Package ID targeting - <partition>
    Given the Buyer owns media buy "mb_pkg_val"
    When the Buyer Agent submits performance feedback with package_id "<value>"
    Then the outcome should be <outcome>

    Examples: Valid
      | partition     | value          | outcome |
      | omitted       |                | success |
      | valid_package | pkg_social_001 | success |
      | single_char   | p              | success |

    Examples: Invalid
      | partition    | value | outcome          |
      | empty_string |       | validation error |

    Examples: Boundaries
      | partition                          | value          | outcome          |
      | Omitted (media buy level feedback) |                | success          |
      | Single-character package ID        | p              | success          |
      | Empty string                       |                | validation error |

  @T-UC-009-creative-id @validation @creative-id @partition @boundary
  Scenario Outline: Creative ID targeting - <partition>
    Given the Buyer owns media buy "mb_cr_val"
    When the Buyer Agent submits performance feedback with creative_id "<value>"
    Then the outcome should be <outcome>

    Examples: Valid
      | partition      | value            | outcome |
      | omitted        |                  | success |
      | valid_creative | cr_video_30s_001 | success |
      | single_char    | c                | success |

    Examples: Invalid
      | partition    | value | outcome          |
      | empty_string |       | validation error |

    Examples: Boundaries
      | partition                                | value            | outcome          |
      | Omitted (package/media buy level feedback) |                | success          |
      | Single-character creative ID             | c                | success          |
      | Empty string                             |                  | validation error |

  @T-UC-009-mb-ident @validation @media-buy-identification @partition @boundary @BR-RULE-021
  Scenario Outline: Media buy identification - <partition>
    When the Buyer Agent submits performance feedback with:
    | media_buy_id   |
    | <media_buy_id> |
    Then the outcome should be <outcome>

    Examples: Valid
      | partition         | media_buy_id | outcome |
      | media_buy_id_only | mb_ident_001 | success |

    Examples: Invalid
      | partition        | media_buy_id | outcome          |
      | neither_provided |              | validation error |

    Examples: Boundaries
      | partition                        | media_buy_id | outcome          |
      | media_buy_id only (primary path) | mb_ident_001 | success          |
      | neither identifier (missing)     |              | validation error |

  @T-UC-009-granularity @dependency @granularity
  Scenario Outline: Feedback granularity targeting - <level>
    Given the Buyer owns media buy "mb_gran_001"
    And the media buy has packages ["pkg_001"] with creatives ["cr_001"]
    When the Buyer Agent submits performance feedback targeting <level>:
    | media_buy_id | package_id   | creative_id   | performance_index |
    | mb_gran_001  | <package_id> | <creative_id> | 1.0               |
    Then the feedback should target the <target_description>

    Examples: Granularity levels
      | level     | package_id | creative_id | target_description     |
      | media_buy |            |             | overall media buy      |
      | package   | pkg_001    |             | specific package       |
      | creative  | pkg_001    | cr_001      | specific creative asset |

  @T-UC-009-ext-a-mcp @extension @ext-a @error @mcp
  Scenario: Media buy not found - MCP path
    Given the Buyer is authenticated with a valid principal_id
    And media buy "mb_nonexistent" does not exist
    When the Buyer Agent calls update_performance_index MCP tool with media_buy_id "mb_nonexistent"
    Then the operation should fail
    And the error code should be "MEDIA_BUY_NOT_FOUND"
    And the error should indicate the media buy was not found
    And the error should include "suggestion" field
    And the suggestion should contain "verify the media buy identifier"
    # POST-F1: No performance data written
    # POST-F2: Buyer knows the identifier was invalid
    # POST-F3: Context echoed if provided
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-ext-a-rest @extension @ext-a @error @rest
  Scenario: Media buy not found - A2A path
    Given the Buyer is authenticated with a valid principal_id
    And media buy "mb_nonexistent_2" does not exist
    When the Buyer Agent sends update_performance_index A2A request with media_buy_id "mb_nonexistent_2"
    Then the operation should fail
    And the error code should be "MEDIA_BUY_NOT_FOUND"
    And the error should indicate the media buy was not found
    And the error should include "suggestion" field
    And the suggestion should contain "verify the media buy identifier"
    # POST-F1: No data written
    # POST-F2: Buyer knows what failed

  @T-UC-009-ext-b-mcp @extension @ext-b @error @mcp @validation
  Scenario: Validation error - MCP - invalid performance_data structure
    Given the Buyer owns media buy "mb_val_001"
    When the Buyer Agent calls update_performance_index MCP tool with invalid performance_data:
    | media_buy_id | performance_data                |
    | mb_val_001   | [{"invalid_field": "no_index"}] |
    Then the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error should contain validation details
    And the error should include "suggestion" field
    And the suggestion should contain "correct parameter format"
    # POST-F1: No performance data written
    # POST-F2: Buyer knows which parameters were invalid
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-ext-b-mcp-empty @extension @ext-b @error @mcp @validation
  Scenario: Validation error - MCP - empty performance_data list
    Given the Buyer owns media buy "mb_val_002"
    When the Buyer Agent calls update_performance_index MCP tool with:
    | media_buy_id | performance_data |
    | mb_val_002   | []               |
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should indicate performance data is required
    And the error should include "suggestion" field
    And the suggestion should contain "provide at least one product entry"
    # POST-F1: No data written
    # POST-F2: Buyer knows data was empty
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-ext-b-rest @extension @ext-b @error @rest @validation
  Scenario: Validation error - A2A - missing required parameters
    When the Buyer Agent sends update_performance_index A2A request without required parameters
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should indicate which parameters are required
    And the response should include required_parameters hint
    And the error should include "suggestion" field
    And the suggestion should contain "required fields"
    # POST-F1: No data written
    # POST-F2: Buyer knows what is required (A2A-specific required_parameters)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-ext-b-rest-pydantic @extension @ext-b @error @rest @validation
  Scenario: Validation error - A2A - Pydantic model validation failure
    When the Buyer Agent sends update_performance_index A2A request with:
    | media_buy_id | performance_data |
    | mb_val_003   | "not_a_list"     |
    Then the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error should contain model validation details
    And the error should include "suggestion" field
    And the suggestion should contain "correct data format"
    # POST-F2: Buyer knows specific field failures
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-ext-b-type @extension @ext-b @error @validation
  Scenario: Validation error - performance_index wrong type
    Given the Buyer owns media buy "mb_type_001"
    When the Buyer Agent submits performance feedback with performance_index as string "high"
    Then the operation should fail with a validation error
    And the error code should be "VALIDATION_ERROR"
    And the error should indicate performance_index must be numeric
    And the error should include "suggestion" field
    And the suggestion should contain "correct data type"
    # POST-F1: No data written
    # POST-F2: Buyer knows type mismatch

  @T-UC-009-ext-c-noauth @extension @ext-c @error @auth @mcp @BR-RULE-285
  Scenario: No authentication context - MCP path
    Given the Buyer has no authentication credentials
    When the Buyer Agent calls update_performance_index MCP tool with media_buy_id "mb_auth_001"
    Then the operation should fail
    And the error code should be "AUTH_REQUIRED"
    And the error should indicate "Context is required for update_performance_index"
    And the error should include "suggestion" field
    And the suggestion should contain "provide authentication credentials"
    # BR-RULE-285 INV-1: no resolvable principal -> AUTH_REQUIRED
    # POST-F1: No data written
    # POST-F2: Buyer knows authentication is required
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-ext-c-ownership @extension @ext-c @error @auth @ownership @BR-RULE-285
  Scenario: Principal does not own media buy
    Given the Buyer is authenticated as principal "buyer_A"
    And media buy "mb_auth_002" is owned by principal "buyer_B"
    When the Buyer Agent calls update_performance_index with media_buy_id "mb_auth_002"
    Then the operation should fail
    And the error code should be "PERMISSION_DENIED"
    And the error should indicate the caller is not authorized for this media buy
    And the error should include "suggestion" field
    And the suggestion should contain "verify media buy ownership"
    # BR-RULE-285 INV-2: principal does not own media buy -> PERMISSION_DENIED
    # POST-F1: No data written
    # POST-F2: Buyer knows ownership check failed
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-ext-c-principal @extension @ext-c @error @auth
  Scenario: Principal object not found
    Given the Buyer is authenticated with a principal that has no principal object
    When the Buyer Agent calls update_performance_index with media_buy_id "mb_auth_003"
    Then the operation should fail
    And the error code should be "AUTH_REQUIRED"
    And the error should indicate the principal was not found
    And the error should include "suggestion" field
    And the suggestion should contain "verify principal configuration"
    # POST-F1: No data written
    # POST-F2: Buyer knows principal lookup failed
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-ext-c-a2a-auth @extension @ext-c @error @auth @rest @BR-RULE-285
  Scenario: Authentication failure - A2A path - invalid auth token
    Given the Buyer sends an A2A request with an invalid or expired auth token
    When the Buyer Agent sends update_performance_index A2A request for "mb_auth_a2a"
    Then the operation should fail
    And the error code should be "AUTH_REQUIRED"
    And the error should indicate authentication failure
    And the error should include "suggestion" field
    And the suggestion should contain "provide valid credentials"
    # BR-RULE-285 INV-1: no resolvable principal credential -> AUTH_REQUIRED
    # POST-F1: No data written
    # POST-F2: Buyer knows auth failed on A2A path

  @T-UC-009-ext-d-false @extension @ext-d @error @adapter
  Scenario: Adapter returns failure - response carries errors[]
    Given the Buyer owns media buy "mb_adapter_001"
    And the adapter will return failure for performance updates
    When the Buyer Agent submits valid performance feedback for "mb_adapter_001" with context:
    | trace_id |
    | trace_d1 |
    Then the response should contain error information
    And the response should NOT contain a success field
    And the response should NOT contain a sandbox field
    And the error should include "suggestion" field
    And the suggestion should contain "retry or contact support"
    And the response context should contain trace_id "trace_d1"
    # POST-F1: No performance data permanently applied
    # POST-F2: Buyer knows adapter failed via populated errors[] (v3.1 Error branch; no status field)
    # POST-F3: Context echoed in constructed response
    # BR-RULE-018 INV-2: Error branch carries errors[] (minItems 1), success/sandbox absent
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-ext-d-exception @extension @ext-d @error @adapter @rest
  Scenario: Adapter raises exception - A2A path
    Given the Buyer owns media buy "mb_adapter_002"
    And the adapter will raise an exception during performance update
    When the Buyer Agent sends update_performance_index A2A request for "mb_adapter_002"
    Then the operation should fail with a server error
    And the error code should be "SERVICE_UNAVAILABLE"
    And the error should indicate "Unable to update performance index"
    And the error should include "suggestion" field
    And the suggestion should contain "retry or contact support"
    # POST-F1: No data applied
    # POST-F2: Buyer knows server error occurred

  @T-UC-009-sandbox-happy @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account performance feedback produces simulated result with sandbox flag
    Given the Buyer owns media buy "mb_sandbox_001"
    And the request targets a sandbox account
    When the Buyer Agent submits valid performance feedback for "mb_sandbox_001" with context:
    | trace_id     |
    | trace_sbx_01 |
    Then the response success field should be true
    And the response should include sandbox equals true
    And no real ad platform optimization adjustments should have been made
    And no real billing records should have been created
    # v3.1 wire: Success branch carries {success: true, sandbox: true, context, optional ext}
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-2: real ad platform calls suppressed
    # BR-RULE-209 INV-3: real billing suppressed
    # BR-RULE-209 INV-4: response includes sandbox: true
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-sandbox-production @invariant @br-rule-209 @sandbox
  Scenario: Production account performance feedback response does not include sandbox flag
    Given the Buyer owns media buy "mb_prod_001"
    And the request targets a production account
    When the Buyer Agent submits valid performance feedback for "mb_prod_001" with context:
    | trace_id      |
    | trace_prod_01 |
    Then the response success field should be true
    And the response should not include a sandbox field
    # BR-RULE-209 INV-5: production account -> sandbox absent
    # v3.1 wire: Success branch carries {success: true, context, optional ext} — no status field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account with invalid performance index returns real validation error
    Given the Buyer owns media buy "mb_sandbox_002"
    And the request targets a sandbox account
    When the Buyer Agent submits performance feedback with a negative performance_index
    Then the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

  @T-UC-009-inv-281-2 @invariant @BR-RULE-281 @error @measurement-period
  Scenario: INV-2 violated - measurement_period entirely omitted
    Given the Buyer owns media buy "mb_period_002"
    When the Buyer Agent submits performance feedback omitting the measurement_period field for "mb_period_002"
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-281 INV-2: missing required envelope field -> INVALID_REQUEST
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-inv-281-5 @invariant @BR-RULE-281 @error @measurement-period @partition
  Scenario Outline: INV-5 violated - start >= end is rejected (<partition>)
    Given the Buyer owns media buy "mb_period_005"
    When the Buyer Agent submits performance feedback with measurement_period <start> to <end>
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-281 INV-5: strict start < end; zero-duration or inverted -> rejection
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

    Examples:
      | partition        | start                | end                  |
      | same_instant     | 2024-06-15T12:00:00Z | 2024-06-15T12:00:00Z |
      | end_before_start | 2024-06-30T00:00:00Z | 2024-06-01T00:00:00Z |

  @T-UC-009-inv-281-6 @invariant @BR-RULE-281 @error @atomicity
  Scenario: INV-6 holds - invalid measurement_period rejection is atomic (no adapter call, no partial state)
    Given the Buyer owns media buy "mb_period_006"
    When the Buyer Agent submits performance feedback with measurement_period 2024-07-10T00:00:00Z to 2024-07-01T00:00:00Z for "mb_period_006"
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the adapter should NOT be invoked
    And no PackagePerformance should be constructed
    # BR-RULE-281 INV-6 + BR-RULE-018: rejection is atomic — no partial state

  @T-UC-009-inv-282-3 @invariant @BR-RULE-282 @deprecation
  Scenario: INV-3 holds - metric_type is surfaced as DEPRECATED in v3.1
    Given the Buyer owns media buy "mb_metric_dep_003"
    When the Buyer Agent submits performance feedback with metric_type "click_through_rate" for "mb_metric_dep_003"
    Then the operation should succeed
    And the consumer-visible schema MUST mark metric_type as DEPRECATED
    # BR-RULE-282 INV-3: one-minor BC deprecation surface
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-inv-282-4 @invariant @BR-RULE-282 @BR-RULE-283 @dispatch
  Scenario: INV-4 holds - when both metric and legacy metric_type are present, dispatch is on metric
    Given the Buyer owns media buy "mb_metric_dispatch_004"
    When the Buyer Agent submits performance feedback for "mb_metric_dispatch_004" with metric_type "brand_lift" and metric:
    | scope     | metric_id |
    | standard  | ctr       |
    Then the operation should succeed
    And the seller dispatches on metric.metric_id "ctr"
    And metric_type "brand_lift" MUST NOT override metric dispatch
    # BR-RULE-282 INV-4 (also covers BR-RULE-283 INV-7): metric is authoritative; metric_type is best-effort BC

  @T-UC-009-inv-283-1 @invariant @BR-RULE-283
  Scenario: INV-1 holds - no metric field is accepted as holistic feedback
    Given the Buyer owns media buy "mb_metric_holistic_001"
    When the Buyer Agent submits performance feedback for "mb_metric_holistic_001" without a metric field
    Then the operation should succeed
    And validation MUST NOT reject for missing metric
    # BR-RULE-283 INV-1: missing metric -> holistic, accepted
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-inv-283-2 @invariant @BR-RULE-283 @error
  Scenario: INV-2 violated - metric.scope outside closed set is rejected
    Given the Buyer owns media buy "mb_metric_scope_002"
    When the Buyer Agent submits performance feedback for "mb_metric_scope_002" with metric scope "unknown_scope"
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-283 INV-2: scope must be {standard, vendor}
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-inv-283-3 @invariant @BR-RULE-283 @error
  Scenario: INV-3 violated - metric.scope=standard with out-of-enum metric_id is rejected
    Given the Buyer owns media buy "mb_metric_std_003"
    When the Buyer Agent submits performance feedback for "mb_metric_std_003" with metric:
    | scope    | metric_id          |
    | standard | not_a_real_metric  |
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-283 INV-3: scope=standard -> metric_id MUST be available-metric enum member
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-inv-283-4 @invariant @BR-RULE-283 @error @partition
  Scenario Outline: INV-4 violated - metric.scope=vendor missing or malformed (<partition>)
    Given the Buyer owns media buy "mb_metric_vendor_004"
    When the Buyer Agent submits performance feedback for "mb_metric_vendor_004" with metric scope "vendor" and <field_present>
    Then the operation should fail
    And the error code should be "<code>"
    And the error should include "suggestion" field
    # BR-RULE-283 INV-4: scope=vendor needs both vendor and metric_id; format violation -> VALIDATION_ERROR
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

    Examples:
      | partition         | field_present                                                       | code             |
      | missing_vendor    | metric_id "vmetric_a"                                               | INVALID_REQUEST  |
      | missing_metric_id | vendor domain "measure.example.com"                                 | INVALID_REQUEST  |
      | bad_vendor_format | vendor domain "INVALID DOMAIN" and metric_id "vmetric_a"            | VALIDATION_ERROR |

  @T-UC-009-inv-283-5 @invariant @BR-RULE-283 @error
  Scenario: INV-5 violated - metric.qualifier with unknown key is rejected
    Given the Buyer owns media buy "mb_metric_qual_005"
    When the Buyer Agent submits performance feedback for "mb_metric_qual_005" with metric qualifier containing unknown key "nonexistent_qualifier"
    Then the operation should fail
    And the error code should be "FIELD_NOT_PERMITTED"
    And the error should include "suggestion" field
    # BR-RULE-283 INV-5: qualifier has additionalProperties:false
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-inv-283-6 @invariant @BR-RULE-283 @error
  Scenario: INV-6 violated - metric object with unknown top-level key is rejected
    Given the Buyer owns media buy "mb_metric_extrakey_006"
    When the Buyer Agent submits performance feedback for "mb_metric_extrakey_006" with metric containing unknown top-level key "garbage"
    Then the operation should fail
    And the error code should be "FIELD_NOT_PERMITTED"
    And the error should include "suggestion" field
    # BR-RULE-283 INV-6: additionalProperties:false on every metric oneOf branch

  @T-UC-009-inv-284-1 @invariant @BR-RULE-284 @partition
  Scenario Outline: INV-1 holds - vendor omitted is always conformant (<feedback_source>)
    Given the Buyer owns media buy "mb_vendor_omitted_001"
    When the Buyer Agent submits performance feedback for "mb_vendor_omitted_001" with feedback_source "<feedback_source>" and vendor omitted
    Then the operation should succeed
    # BR-RULE-284 INV-1: vendor is OPTIONAL at schema level
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

    Examples:
      | feedback_source         |
      | buyer_attribution       |
      | third_party_measurement |
      | platform_analytics      |
      | verification_partner    |

  @T-UC-009-inv-284-2 @invariant @BR-RULE-284 @error
  Scenario: INV-2 violated - vendor with non-conforming brand-ref shape is rejected
    Given the Buyer owns media buy "mb_vendor_shape_002"
    When the Buyer Agent submits performance feedback for "mb_vendor_shape_002" with vendor object containing invalid domain "NOT A VALID DOMAIN!"
    Then the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion" field
    # BR-RULE-284 INV-2: vendor must satisfy brand-ref schema
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-inv-284-3 @invariant @BR-RULE-284 @advisory @partition
  Scenario Outline: INV-3 holds - third-party/verification source with vendor absent yields advisory (not rejection) (<feedback_source>)
    Given the Buyer owns media buy "mb_vendor_advisory_003"
    When the Buyer Agent submits performance feedback for "mb_vendor_advisory_003" with feedback_source "<feedback_source>" and vendor omitted
    Then the operation should succeed
    And the response MAY include an advisory warning "VENDOR_ATTRIBUTION_RECOMMENDED"
    And the request MUST NOT be rejected
    # BR-RULE-284 INV-3: SHOULD-populate guidance -> advisory only
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

    Examples:
      | feedback_source         |
      | third_party_measurement |
      | verification_partner    |

  @T-UC-009-inv-284-4 @invariant @BR-RULE-284 @advisory
  Scenario: INV-4 holds - blended feedback with vendor populated yields advisory (not rejection)
    Given the Buyer owns media buy "mb_vendor_blended_004"
    When the Buyer Agent submits performance feedback for "mb_vendor_blended_004" with feedback_source "buyer_attribution" derived from blended MMM and vendor domain "measure.example.com"
    Then the operation should succeed
    And the response MAY include an advisory warning "VENDOR_ATTRIBUTION_AMBIGUOUS"
    And the request MUST NOT be rejected
    # BR-RULE-284 INV-4: SHOULD-omit guidance -> advisory only

  @T-UC-009-inv-285-3 @invariant @BR-RULE-285 @ownership
  Scenario: INV-3 holds - ownership is re-evaluated per call (no cached decision survives)
    Given the Buyer owns media buy "mb_owner_realtime_003"
    And the Buyer Agent has previously submitted feedback for "mb_owner_realtime_003" successfully
    And ownership of "mb_owner_realtime_003" is transferred to a different principal
    When the Buyer Agent submits performance feedback for "mb_owner_realtime_003" again
    Then the operation should fail
    And the error code should be "PERMISSION_DENIED"
    # BR-RULE-285 INV-3: ownership re-evaluated per call against current tenant/principal state
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

  @T-UC-009-inv-285-4 @invariant @BR-RULE-285 @auth @ordering
  Scenario: INV-4 holds - authentication is checked before ownership
    Given the Buyer has no authentication credentials
    When the Buyer Agent submits performance feedback for media_buy_id "mb_owner_order_004"
    Then the operation should fail
    And the error code should be "AUTH_REQUIRED"
    And ownership verification MUST NOT have been attempted
    # BR-RULE-285 INV-4: auth-before-ownership ordering — auth failure precludes ownership check

  @T-UC-009-bva-metric @bva @partition @boundary @BR-RULE-283 @metric
  Scenario Outline: BVA - metric discriminated row shape boundary - <partition>
    Given the Buyer owns media buy "mb_bva_metric"
    When the Buyer Agent submits performance feedback exercising the metric boundary
    Then the outcome should be <outcome>
    # All "validation error" outcomes above assert the canonical INVALID_REQUEST/VALIDATION_ERROR/FIELD_NOT_PERMITTED rejection with a recovery suggestion (see BR-RULE-283 invariant scenarios T-UC-009-inv-283-2..6 for assertion details).
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

    Examples: Valid metric branches
      | partition                                                              | outcome          |
      | metric field omitted (holistic feedback)                               | success          |
      | scope=standard, minimum required keys                                  | success          |
      | scope=standard with one qualifier key                                  | success          |
      | scope=standard with multi-key qualifier (methodology + window)         | success          |
      | scope=vendor with full (vendor, metric_id) tuple                       | success          |

    Examples: Invalid metric branches (atomic rejection with suggestion)
      | partition                                                              | outcome          |
      | scope value outside {standard, vendor}                                 | validation error |
      | scope field absent on present metric object                            | validation error |
      | standard metric_id outside available-metric enum                       | validation error |
      | vendor branch missing vendor brand-ref                                 | validation error |
      | vendor branch missing metric_id                                        | validation error |
      | vendor metric_id with uppercase/hyphen (pattern violation)             | validation error |
      | unknown top-level key on metric branch                                 | validation error |

  @T-UC-009-bva-context @bva @partition @boundary @BR-RULE-043 @context
  Scenario Outline: BVA - context echo partition - <partition>
    Given the Buyer owns media buy "mb_bva_context"
    When the Buyer Agent submits performance feedback with the context boundary
    Then the outcome should be success
    And the response context echo behavior matches the partition
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

    Examples:
      | partition              |
      | context absent         |
      | context = {}           |
      | context with properties |

  @T-UC-009-bva-sandbox @bva @partition @boundary @BR-RULE-209 @sandbox
  Scenario Outline: BVA - sandbox response semantics - <partition>
    Given the Buyer owns media buy "mb_bva_sandbox"
    When the Buyer Agent submits valid performance feedback against the account class
    Then the response carries the expected sandbox semantics
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

    Examples: Response shape boundaries
      | partition                                          |
      | sandbox: true in response (sandbox account)        |
      | sandbox absent in response (production account)    |
      | sandbox: false in response (explicit production)   |

  @T-UC-009-bva-measurement-period @bva @partition @boundary @BR-RULE-281 @measurement-period
  Scenario Outline: BVA - measurement_period window validity - <partition>
    Given the Buyer owns media buy "mb_bva_period"
    When the Buyer Agent submits performance feedback exercising the measurement_period boundary
    Then the outcome should be <outcome>
    # All "validation error" outcomes above assert INVALID_REQUEST with a recovery suggestion (see invariant scenarios T-UC-009-inv-281-2 / T-UC-009-inv-281-5 / T-UC-009-inv-281-6).
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

    Examples: Invalid windows (atomic rejection, INVALID_REQUEST + suggestion)
      | partition                                                | outcome          |
      | measurement_period absent from request                   | validation error |
      | Missing start field inside measurement_period            | validation error |
      | Missing end field inside measurement_period              | validation error |
      | Non-datetime string for start (e.g., 'not-a-date')       | validation error |
      | start == end (same instant)                              | validation error |
      | start > end (inverted)                                   | validation error |

  @T-UC-009-bva-metric-type @bva @partition @boundary @BR-RULE-282 @metric-type
  Scenario Outline: BVA - metric_type legacy enum boundary - <partition>
    Given the Buyer owns media buy "mb_bva_metric_type"
    When the Buyer Agent submits performance feedback exercising the metric_type boundary
    Then the outcome should be success
    # Dispatch-precedence row (metric+metric_type both present) is success: consumers MUST use `metric` for dispatch per BR-RULE-282 INV-4 (covered fully by T-UC-009-inv-282-4); legacy metric_type retained for one-minor BC.
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

    Examples: Default / omitted / dispatch precedence
      | partition                                                              |
      | overall_performance (default value)                                    |
      | Not provided (envelope default applied)                                |
      | Both `metric` and `metric_type` populated on the same row              |

  @T-UC-009-bva-idempotency-key @bva @partition @boundary @BR-RULE-081 @idempotency-key
  Scenario Outline: BVA - idempotency_key format boundary - <partition>
    Given the Buyer owns media buy "mb_bva_idemp"
    When the Buyer Agent submits performance feedback exercising the idempotency_key boundary
    Then the outcome should be <outcome>
    # The "validation error" outcomes assert VALIDATION_ERROR / IDEMPOTENCY_KEY_INVALID_FORMAT with a recovery suggestion (use a fresh UUID v4 of length 16-255 with allowed character set).
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

    Examples: Length and pattern boundaries
      | partition                                          | outcome          |
      | length 16 (min, inclusive)                         | success          |
      | length 255 (max, inclusive)                        | success          |
      | length 15 (below min)                              | validation error |
      | absent (field not provided)                        | validation error |
      | valid length, disallowed character (e.g. space)    | validation error |

  @T-UC-009-bva-media-buy-identification @bva @partition @boundary @BR-RULE-021 @media-buy-identification
  Scenario Outline: BVA - media_buy_id required (v3.1) - <partition>
    When the Buyer Agent submits performance feedback exercising the media_buy_identification boundary
    Then the outcome should be <outcome>
    # "media_buy_id absent (missing)" asserts INVALID_REQUEST with a recovery suggestion (provide a seller-issued media_buy_id; buyer_ref XOR was retired in v3.1).
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/provide-performance-feedback-request.json

    Examples:
      | partition                          | outcome          |
      | media_buy_id present (required)    | success          |
      | media_buy_id absent (missing)      | validation error |
      | media_buy_id empty string ("")     | validation error |

  @T-UC-009-bva-vendor @bva @partition @boundary @BR-RULE-284 @vendor
  Scenario Outline: BVA - vendor attribution provenance - <partition>
    Given the Buyer owns media buy "mb_bva_vendor"
    When the Buyer Agent submits performance feedback exercising the vendor boundary
    Then the outcome should be <outcome>
    # All "validation error" rows above assert VALIDATION_ERROR with a recovery suggestion (vendor MUST be an object conforming to brand-ref: required domain matching lowercase-DNS pattern, optional brand_id, additionalProperties:false) — see T-UC-009-inv-284-2 for the canonical assertion.

    Examples: Vendor SHOULD-omit / SHOULD-populate / shape-valid (conformant)
      | partition                                                                                                                | outcome |
      | Omitted entirely (any feedback_source)                                                                                   | success |
      | Omitted with feedback_source=third_party_measurement + MMM narrative (SHOULD-OMIT satisfied)                             | success |
      | Omitted with feedback_source=verification_partner + clean-room narrative (SHOULD-OMIT satisfied)                         | success |
      | Present with valid brand-ref, single attesting vendor, feedback_source=third_party_measurement (SHOULD-POPULATE satisfied) | success |
      | Present with valid brand-ref, single attesting vendor, feedback_source=verification_partner (SHOULD-POPULATE satisfied)   | success |
      | Present with brand_id for house-of-brands                                                                                | success |

    Examples: Vendor shape rejections (atomic, VALIDATION_ERROR + suggestion)
      | partition                                       | outcome          |
      | Present without required domain                 | validation error |
      | Present with uppercase domain                   | validation error |
      | Present with unknown additional property        | validation error |
      | Sent as a bare string                           | validation error |
