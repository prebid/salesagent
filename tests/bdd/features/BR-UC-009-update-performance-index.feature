# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

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


  @T-UC-009-main-mcp @main-flow @mcp
  Scenario: Submit performance feedback via MCP - success
    Given the Buyer owns media buy "mb_perf_001"
    And the media buy has products ["pkg_display_001"]
    When the Buyer Agent calls update_performance_index MCP tool with:
    | media_buy_id | performance_data                                              |
    | mb_perf_001  | [{"product_id": "pkg_display_001", "performance_index": 1.2}] |
    Then the operation should succeed
    And the response status should be "success"
    And the response should include a detail message
    # POST-S1: Feedback accepted and forwarded to adapter
    # POST-S2: Buyer receives status confirmation

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

  @T-UC-009-main-rest @main-flow @rest @a2a
  Scenario: Submit performance feedback via A2A - success
    Given the Buyer owns media buy "mb_perf_004"
    And the media buy has products ["pkg_display_002"]
    When the Buyer Agent sends update_performance_index A2A skill request with:
    | media_buy_id | performance_data                                              |
    | mb_perf_004  | [{"product_id": "pkg_display_002", "performance_index": 1.0}] |
    Then the operation should succeed
    And the response status should be "success"
    And the response should include a detail message
    # POST-S1: Feedback accepted and forwarded
    # POST-S2: Buyer receives confirmation

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

  @T-UC-009-idempotent @main-flow @idempotent
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
    Then the response should contain status "success"
    And the response should contain a detail message
    And the response should NOT contain an errors array
    # BR-RULE-018 INV-1: Successful operation -> success fields, no errors

  @T-UC-009-inv-018-2 @invariant @BR-RULE-018 @error
  Scenario: INV-2 holds - error response contains only error fields
    Given the Buyer owns media buy "mb_atomic_002"
    When the Buyer Agent submits performance feedback with invalid data for "mb_atomic_002"
    Then the response should contain error information
    And the response should NOT contain status "success"
    And the error should include "suggestion" field
    And the suggestion should contain "correct format"
    # BR-RULE-018 INV-2: Validation failure -> errors, no success fields

  @T-UC-009-inv-021-1 @invariant @BR-RULE-021
  Scenario: INV-1 holds - media_buy_id resolves target
    Given the Buyer owns media buy "mb_xor_001"
    When the Buyer Agent submits performance feedback with media_buy_id "mb_xor_001"
    Then the operation should succeed
    # BR-RULE-021 INV-1: Exactly one identifier -> resolves

  @T-UC-009-inv-021-1-buyerref @invariant @BR-RULE-021
  Scenario: INV-1 holds - buyer_ref resolves target (protocol)
    Given the Buyer owns a media buy with buyer_ref "my-campaign-2024"
    When the Buyer Agent submits performance feedback with buyer_ref "my-campaign-2024"
    Then the operation should succeed
    # BR-RULE-021 INV-1: buyer_ref resolves the target media buy

  @T-UC-009-inv-021-2 @invariant @BR-RULE-021 @error
  Scenario: INV-2 violated - both identifiers provided
    When the Buyer Agent submits performance feedback with:
    | media_buy_id | buyer_ref        |
    | mb_xor_002   | my-campaign-2024 |
    Then the operation should fail with a validation error
    And the error should indicate that only one identifier is allowed
    And the error should include "suggestion" field
    And the suggestion should contain "provide exactly one identifier"
    # BR-RULE-021 INV-2: Both provided -> schema rejects

  @T-UC-009-inv-021-3 @invariant @BR-RULE-021 @error
  Scenario: INV-3 violated - neither identifier provided
    When the Buyer Agent submits performance feedback without media_buy_id or buyer_ref
    Then the operation should fail with a validation error
    And the error should indicate that an identifier is required
    And the error should include "suggestion" field
    And the suggestion should contain "provide media_buy_id or buyer_ref"
    # BR-RULE-021 INV-3: Neither provided -> schema rejects

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

  @T-UC-009-meas-period @validation @measurement-period @partition @boundary
  Scenario Outline: Measurement period validation - <partition>
    Given the Buyer owns media buy "mb_period_val"
    When the Buyer Agent submits performance feedback with measurement_period <start> to <end>
    Then the outcome should be <outcome>

    Examples: Valid
      | partition      | start                | end                      | outcome |
      | normal_period  | 2024-01-01T00:00:00Z | 2024-01-31T23:59:59Z     | success |
      | same_instant   | 2024-06-15T12:00:00Z | 2024-06-15T12:00:00Z     | success |

    Examples: Invalid
      | partition       | start                | end                  | outcome          |
      | missing_start   |                      | 2024-01-31T23:59:59Z | validation error |
      | missing_end     | 2024-01-01T00:00:00Z |                      | validation error |
      | invalid_format  | not-a-date           | 2024-01-31T23:59:59Z | validation error |

    Examples: Boundaries
      | partition                                                                       | start                | end                  | outcome          |
      | Valid period (start=2024-01-01T00:00:00Z, end=2024-01-31T23:59:59Z)             | 2024-01-01T00:00:00Z | 2024-01-31T23:59:59Z | success          |
      | Same instant (start == end)                                                     | 2024-06-15T12:00:00Z | 2024-06-15T12:00:00Z | success          |
      | Missing start field                                                             |                      | 2024-01-31T23:59:59Z | validation error |
      | Missing end field                                                               | 2024-01-01T00:00:00Z |                      | validation error |
      | Non-datetime string for start                                                   | not-a-date           | 2024-01-31T23:59:59Z | validation error |

  @T-UC-009-metric-type @validation @metric-type @partition @boundary
  Scenario Outline: Metric type validation - <partition>
    Given the Buyer owns media buy "mb_metric_val"
    When the Buyer Agent submits performance feedback with metric_type "<value>"
    Then the outcome should be <outcome>

    Examples: Valid enum values
      | partition             | value               | outcome |
      | overall_performance   | overall_performance | success |
      | conversion_rate       | conversion_rate     | success |
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
    | media_buy_id   | buyer_ref   |
    | <media_buy_id> | <buyer_ref> |
    Then the outcome should be <outcome>

    Examples: Valid
      | partition        | media_buy_id | buyer_ref        | outcome |
      | media_buy_id_only | mb_ident_001 |                  | success |
      | buyer_ref_only   |              | my-campaign-2024 | success |

    Examples: Invalid
      | partition         | media_buy_id | buyer_ref        | outcome          |
      | both_provided     | mb_ident_001 | my-campaign-2024 | validation error |
      | neither_provided  |              |                  | validation error |

    Examples: Boundaries
      | partition                          | media_buy_id | buyer_ref        | outcome          |
      | media_buy_id only (primary path)   | mb_ident_001 |                  | success          |
      | buyer_ref only (fallback path)     |              | my-campaign-2024 | success          |
      | both identifiers (ambiguous)       | mb_ident_001 | my-campaign-2024 | validation error |
      | neither identifier (missing)       |              |                  | validation error |

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
    And the error should indicate the media buy was not found
    And the error should include "suggestion" field
    And the suggestion should contain "verify the media buy identifier"
    # POST-F1: No performance data written
    # POST-F2: Buyer knows the identifier was invalid
    # POST-F3: Context echoed if provided

  @T-UC-009-ext-a-rest @extension @ext-a @error @rest
  Scenario: Media buy not found - A2A path
    Given the Buyer is authenticated with a valid principal_id
    And media buy "mb_nonexistent_2" does not exist
    When the Buyer Agent sends update_performance_index A2A request with media_buy_id "mb_nonexistent_2"
    Then the operation should fail
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
    And the error should contain validation details
    And the error should include "suggestion" field
    And the suggestion should contain "correct parameter format"
    # POST-F1: No performance data written
    # POST-F2: Buyer knows which parameters were invalid

  @T-UC-009-ext-b-mcp-empty @extension @ext-b @error @mcp @validation
  Scenario: Validation error - MCP - empty performance_data list
    Given the Buyer owns media buy "mb_val_002"
    When the Buyer Agent calls update_performance_index MCP tool with:
    | media_buy_id | performance_data |
    | mb_val_002   | []               |
    Then the operation should fail
    And the error should indicate performance data is required
    And the error should include "suggestion" field
    And the suggestion should contain "provide at least one product entry"
    # POST-F1: No data written
    # POST-F2: Buyer knows data was empty

  @T-UC-009-ext-b-rest @extension @ext-b @error @rest @validation
  Scenario: Validation error - A2A - missing required parameters
    When the Buyer Agent sends update_performance_index A2A request without required parameters
    Then the operation should fail
    And the error should indicate which parameters are required
    And the response should include required_parameters hint
    And the error should include "suggestion" field
    And the suggestion should contain "required fields"
    # POST-F1: No data written
    # POST-F2: Buyer knows what is required (A2A-specific required_parameters)

  @T-UC-009-ext-b-rest-pydantic @extension @ext-b @error @rest @validation
  Scenario: Validation error - A2A - Pydantic model validation failure
    When the Buyer Agent sends update_performance_index A2A request with:
    | media_buy_id | performance_data |
    | mb_val_003   | "not_a_list"     |
    Then the operation should fail
    And the error should contain model validation details
    And the error should include "suggestion" field
    And the suggestion should contain "correct data format"
    # POST-F2: Buyer knows specific field failures

  @T-UC-009-ext-b-type @extension @ext-b @error @validation
  Scenario: Validation error - performance_index wrong type
    Given the Buyer owns media buy "mb_type_001"
    When the Buyer Agent submits performance feedback with performance_index as string "high"
    Then the operation should fail with a validation error
    And the error should indicate performance_index must be numeric
    And the error should include "suggestion" field
    And the suggestion should contain "correct data type"
    # POST-F1: No data written
    # POST-F2: Buyer knows type mismatch

  @T-UC-009-ext-c-noauth @extension @ext-c @error @auth @mcp
  Scenario: No authentication context - MCP path
    Given the Buyer has no authentication credentials
    When the Buyer Agent calls update_performance_index MCP tool with media_buy_id "mb_auth_001"
    Then the operation should fail
    And the error should indicate "Context is required for update_performance_index"
    And the error should include "suggestion" field
    And the suggestion should contain "provide authentication credentials"
    # POST-F1: No data written
    # POST-F2: Buyer knows authentication is required

  @T-UC-009-ext-c-ownership @extension @ext-c @error @auth
  Scenario: Principal does not own media buy
    Given the Buyer is authenticated as principal "buyer_A"
    And media buy "mb_auth_002" is owned by principal "buyer_B"
    When the Buyer Agent calls update_performance_index with media_buy_id "mb_auth_002"
    Then the operation should fail
    And the error should indicate the caller is not authorized for this media buy
    And the error should include "suggestion" field
    And the suggestion should contain "verify media buy ownership"
    # POST-F1: No data written
    # POST-F2: Buyer knows ownership check failed

  @T-UC-009-ext-c-principal @extension @ext-c @error @auth
  Scenario: Principal object not found
    Given the Buyer is authenticated with a principal that has no principal object
    When the Buyer Agent calls update_performance_index with media_buy_id "mb_auth_003"
    Then the operation should fail
    And the error should indicate the principal was not found
    And the error should include "suggestion" field
    And the suggestion should contain "verify principal configuration"
    # POST-F1: No data written
    # POST-F2: Buyer knows principal lookup failed

  @T-UC-009-ext-c-a2a-auth @extension @ext-c @error @auth @rest
  Scenario: Authentication failure - A2A path - invalid auth token
    Given the Buyer sends an A2A request with an invalid or expired auth token
    When the Buyer Agent sends update_performance_index A2A request for "mb_auth_a2a"
    Then the operation should fail
    And the error should indicate authentication failure
    And the error should include "suggestion" field
    And the suggestion should contain "provide valid credentials"
    # POST-F1: No data written
    # POST-F2: Buyer knows auth failed on A2A path

  @T-UC-009-ext-d-false @extension @ext-d @error @adapter
  Scenario: Adapter returns failure (status=failed)
    Given the Buyer owns media buy "mb_adapter_001"
    And the adapter will return failure for performance updates
    When the Buyer Agent submits valid performance feedback for "mb_adapter_001" with context:
    | trace_id |
    | trace_d1 |
    Then the response status should be "failed"
    And the response should include a failure detail message
    And the response should include suggestion to retry or contact support
    And the response context should contain trace_id "trace_d1"
    # POST-F1: No performance data permanently applied
    # POST-F2: Buyer knows adapter failed
    # POST-F3: Context echoed in constructed response

  @T-UC-009-ext-d-exception @extension @ext-d @error @adapter @rest
  Scenario: Adapter raises exception - A2A path
    Given the Buyer owns media buy "mb_adapter_002"
    And the adapter will raise an exception during performance update
    When the Buyer Agent sends update_performance_index A2A request for "mb_adapter_002"
    Then the operation should fail with a server error
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
    Then the response status should be "completed"
    And the response should include sandbox equals true
    And no real ad platform optimization adjustments should have been made
    And no real billing records should have been created
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-2: real ad platform calls suppressed
    # BR-RULE-209 INV-3: real billing suppressed
    # BR-RULE-209 INV-4: response includes sandbox: true

  @T-UC-009-sandbox-production @invariant @br-rule-209 @sandbox
  Scenario: Production account performance feedback response does not include sandbox flag
    Given the Buyer owns media buy "mb_prod_001"
    And the request targets a production account
    When the Buyer Agent submits valid performance feedback for "mb_prod_001" with context:
    | trace_id      |
    | trace_prod_01 |
    Then the response status should be "completed"
    And the response should not include a sandbox field
    # BR-RULE-209 INV-5: production account -> sandbox absent

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

