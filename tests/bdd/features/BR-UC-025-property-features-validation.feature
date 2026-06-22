# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

Feature: BR-UC-025 Validate Property Delivery Compliance
  As a Buyer
  I want to validate delivery records against a property list and resolve per-record compliance and supply-path authorization
  So that I can determine campaign compliance and drive fix-and-retry from per-feature detail

  # v3.1: get_property_features removed; the single live operation of UC-025 is
  # validate_property_delivery. Former discovery scenarios and INV-189/INV-190
  # invariants are retained as @deprecated for binding stability.
  #
  # Postconditions verified:
  #   POST-S1: [DEPRECATED v3.1] superseded by POST-S5 (per-record features[] breakdown)
  #   POST-S2: [DEPRECATED v3.1] no live discovery operation in v3.1
  #   POST-S3: Buyer knows per-record compliance (compliant / non_compliant / not_covered / unidentified) and aggregate summary
  #   POST-S4: Buyer knows supply-path authorization (authorized / unauthorized / unknown) and authorization_summary
  #   POST-S5: Buyer knows per-feature breakdown via features[] (status from feature-check-status enum) with requirement echoed when buyer-authored
  #   POST-S6: Buyer knows validated_at (required) and list_resolved_at (optional)
  #   POST-S7: Buyer knows root-level compliant flag (true iff summary.non_compliant_records == 0)
  #   POST-F1: System state is unchanged on failure (read-only)
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Application context is still echoed when possible
  #
  # Rules: BR-RULE-189 [DEPRECATED v3.1], BR-RULE-190 [DEPRECATED v3.1],
  #        BR-RULE-191 (Delivery Validation Request), BR-RULE-192 (Delivery Validation Response)
  # Extensions (active v3.1): E (REFERENCE_NOT_FOUND), F (LIST_ACCESS_DENIED), G (RECORDS_REQUIRED),
  #   H (RECORDS_LIMIT_EXCEEDED), K (partial coverage not_covered/unidentified),
  #   L (per-feature failed/warning), M (supply-path authorization unauthorized/unknown),
  #   N (ACCOUNT_REQUIRED multi-account)
  # Extensions (deprecated v3.1): A, B (transport-split validate; consolidated into BR-UC-025-main),
  #   C (PROPERTY_NOT_FOUND), D (PROPERTY_NOT_MONITORED), I (PROPERTY_SELECTION_REQUIRED),
  #   J (PROPERTIES_LIMIT_EXCEEDED)

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And the Buyer is authenticated with a valid principal_id


  @T-UC-025-delivery-mcp @main-flow @mcp @post-s3 @post-s5 @post-s6
  Scenario: Delivery validation via MCP -- validate records against property list
    Given the Buyer Agent has an authenticated connection
    And property list "pl-valid" exists and is accessible by the buyer
    And the list contains properties "good.com" and "approved.com"
    When the Buyer Agent invokes validate_property_delivery with list_id "pl-valid" and records containing deliveries to "good.com" (1000 impressions) and "bad.com" (500 impressions)
    Then the response contains list_id "pl-valid"
    And the summary shows total_records 2 with compliant_records 1 and non_compliant_records 1
    And the results array contains the non-compliant record for "bad.com"
    And the non-compliant record has a features[] entry with feature_id "record:list_membership" and status "failed"
    And the response includes validated_at timestamp
    # POST-S3: Buyer knows compliance status
    # POST-S5: Buyer knows per-feature breakdown via features[]
    # POST-S6: Buyer knows validation timestamp
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

  @T-UC-025-delivery-rest @main-flow @rest @post-s3 @post-s6
  Scenario: Delivery validation via REST/A2A -- same semantics as MCP
    Given the Buyer Agent has an authenticated connection
    And property list "pl-valid" exists and is accessible by the buyer
    When the Buyer Agent sends validate_property_delivery via A2A with list_id "pl-valid" and 10 delivery records
    Then the response contains list_id "pl-valid"
    And the summary shows all 10 counters correctly
    And the response includes validated_at timestamp
    # POST-S3: Compliance status via REST
    # POST-S6: Validation timestamp and list resolution
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

  @T-UC-025-delivery-transport @main-flow @post-s3
  Scenario Outline: Delivery validation via <transport> -- equivalent results
    Given the Buyer Agent has an authenticated connection
    And property list "pl-valid" exists with property "example.com"
    When the Buyer Agent sends validate_property_delivery via <transport> with list_id "pl-valid" and 1 record for "example.com" with 1000 impressions
    Then the summary shows total_records 1 and compliant_records 1
    # POST-S3: Equivalent compliance results regardless of transport

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-025-validation-statuses @main-flow @invariant @BR-RULE-192 @post-s3
  Scenario Outline: Validation status classification -- <status>
    Given property list "pl-valid" exists and is accessible
    And the delivery record for "<property>" resolves to status "<status>"
    When the Buyer Agent validates delivery with list_id "pl-valid" and 1 record for "<property>"
    Then the result has status "<status>"
    And <features_state>
    # BR-RULE-192 INV-1: exactly one of four statuses
    # BR-RULE-192 INV-13: not_covered (recognized, no data) vs unidentified (type not resolvable) distinct from non_compliant
    # DEPRECATED: upstream removed -- violations[] removed in v3.1; per-failure detail now surfaced via features[].status="failed" (see T-UC-025-feature-failed-detail, T-UC-025-inv192-reserved-namespace)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

    Examples:
      | status         | property         | features_state                                                    |
      | compliant      | good.com         | no failed features[] entries are present                          |
      | non_compliant  | bad.com          | at least one features[] entry has status "failed"                  |
      | not_covered    | no-data.com      | features[] may be empty or absent                                  |
      | unidentified   | custom-id-xyz    | features[] may be empty or absent                                  |

  @T-UC-025-violation-codes @deprecated @main-flow @ext-a @post-s5
  Scenario Outline: Violation code detail -- <violation_code>
    Given property list "pl-valid" exists and is accessible
    And the delivery record triggers violation "<violation_code>"
    When the Buyer Agent validates delivery with that record
    Then the result has status "non_compliant"
    And the violations array contains an entry with code "<violation_code>"
    And the violation entry includes a message
    # POST-S5: Buyer knows specific violations
    # BR-RULE-192 INV-2: non_compliant has violations
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

    Examples:
      | violation_code    |
      | not_in_list       |
      | excluded          |
      | country_mismatch  |
      | channel_mismatch  |
      | feature_failed    |

  @T-UC-025-feature-failed-detail @main-flow @extension @ext-l @post-s5
  Scenario: Per-feature failure detail -- features[] entry includes feature_id, status, and echoed requirement
    Given property list "pl-valid" with feature requirement carbon_score min_value 50
    And the delivery record is for property "low-carbon.com" which has carbon_score 30
    When the Buyer Agent validates delivery with that record
    Then the result has status "non_compliant"
    And the result features[] contains an entry with feature_id "carbon_score" and status "failed"
    And the features[] entry echoes the buyer-authored requirement min_value 50
    # POST-S5: per-feature failure breakdown with requirement echoed (BR-RULE-192 INV-10)

  @T-UC-025-auth-check @main-flow @post-s4
  Scenario: Supply path authorization -- records with sales_agent_url trigger auth check
    Given property list "pl-valid" exists and is accessible
    And the delivery record for "example.com" includes sales_agent_url "https://agent.example.com/.well-known/adcp"
    And the publisher's adagents.json lists that agent as authorized
    When the Buyer Agent validates delivery with that record
    Then the result includes authorization with status "authorized"
    And the response includes authorization_summary
    And authorization_summary records_checked equals 1
    # POST-S4: Buyer knows authorization status
    # BR-RULE-191 INV-6: sales_agent_url triggers auth check
    # BR-RULE-192 INV-3: authorization_summary present when auth checks performed
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

  @T-UC-025-auth-statuses @main-flow @invariant @BR-RULE-192 @post-s4
  Scenario Outline: Authorization status classification -- <auth_status>
    Given property list "pl-valid" exists and is accessible
    And the delivery record includes sales_agent_url and authorization resolves to "<auth_status>"
    When the Buyer Agent validates delivery with that record
    Then the result authorization status is "<auth_status>"
    # BR-RULE-192 INV-3: auth status is three-valued enum [authorized / unauthorized / unknown]
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

    Examples:
      | auth_status  |
      | authorized   |
      | unauthorized |
      | unknown      |

  @T-UC-025-auth-no-url @main-flow @invariant @BR-RULE-192 @post-s4
  Scenario: No authorization check -- records without sales_agent_url skip auth
    Given property list "pl-valid" exists and is accessible
    And the delivery records do not include sales_agent_url
    When the Buyer Agent validates delivery with those records
    Then no authorization field is present on any result
    And authorization_summary is absent from the response
    # BR-RULE-191 INV-7: no auth check without sales_agent_url
    # BR-RULE-192 INV-3: authorization_summary absent when no auth records
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

  @T-UC-025-auth-mixed @main-flow @post-s4
  Scenario: Mixed authorization -- some records with sales_agent_url, some without
    Given property list "pl-valid" exists and is accessible
    And record for "a.com" includes sales_agent_url
    And record for "b.com" does not include sales_agent_url
    When the Buyer Agent validates delivery with both records
    Then the result for "a.com" includes authorization status
    And the result for "b.com" does not include authorization
    And authorization_summary is present with records_checked 1
    # BR-RULE-191 INV-6/7: per-record opt-in authorization
    # BR-RULE-192 INV-3: summary present because at least one record had URL
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

  @T-UC-025-auth-independence @main-flow @invariant @BR-RULE-192 @post-s3 @post-s4
  Scenario: Authorization independent of compliance -- compliant but unauthorized
    Given property list "pl-valid" with property "legit.com"
    And the delivery record for "legit.com" includes sales_agent_url for an unauthorized agent
    When the Buyer Agent validates delivery with that record
    Then the result has status "compliant" (property is in list)
    And the result has authorization status "unauthorized" (agent not in adagents.json)
    # BR-RULE-192: compliance and authorization are independent checks

  @T-UC-025-summary-consistency @main-flow @invariant @BR-RULE-192 @post-s3
  Scenario: Summary counters are internally consistent
    Given property list "pl-valid" exists and is accessible
    And 100 delivery records resolve to: 60 compliant, 25 non_compliant, 10 not_covered, 5 unidentified
    When the Buyer Agent validates delivery with those 100 records
    Then summary total_records equals 100
    And total_records equals compliant_records + non_compliant_records + not_covered_records + unidentified_records
    And total_impressions equals compliant_impressions + non_compliant_impressions + not_covered_impressions + unidentified_impressions
    # BR-RULE-192 INV-4: total_records consistency
    # BR-RULE-192 INV-5: total_impressions consistency
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

  @T-UC-025-auth-summary-consistency @main-flow @invariant @BR-RULE-192 @post-s4
  Scenario: Authorization summary counters are internally consistent
    Given property list "pl-valid" exists and is accessible
    And 50 delivery records include sales_agent_url: 40 authorized, 7 unauthorized, 3 unknown
    When the Buyer Agent validates delivery with those records
    Then authorization_summary records_checked equals 50
    And records_checked equals authorized_records + unauthorized_records + unknown_records
    # BR-RULE-192 INV-6: authorization summary consistency

  @T-UC-025-include-compliant @main-flow @invariant @BR-RULE-192 @post-s3
  Scenario Outline: Include compliant flag -- <flag_state>
    Given property list "pl-valid" with 10 records: 8 compliant, 2 non_compliant
    When the Buyer Agent validates delivery with include_compliant <flag_state>
    Then the results array contains <expected_count> records
    # BR-RULE-191 INV-5: include_compliant default behavior
    # BR-RULE-192 INV-7: results filtering
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

    Examples:
      | flag_state          | expected_count |
      | omitted (default)   | 2              |
      | set to false        | 2              |
      | set to true         | 10             |

  @T-UC-025-all-compliant-default @main-flow @post-s3
  Scenario: All compliant records with default filter -- results array empty
    Given property list "pl-valid" with all records compliant
    When the Buyer Agent validates delivery with include_compliant omitted
    Then the results array is empty
    And the summary shows all records as compliant
    # BR-RULE-192 INV-7: all compliant + default filter = empty results

  @T-UC-025-ext-e @extension @ext-e @error @post-f1 @post-f2 @post-f3
  Scenario: REFERENCE_NOT_FOUND -- property list does not exist
    Given list_id "pl-nonexistent" does not reference any existing property list
    When the Buyer Agent sends validate_property_delivery with list_id "pl-nonexistent"
    Then the operation should fail
    And the error code should be "REFERENCE_NOT_FOUND"
    And the error message should contain "not found"
    And the error should include "suggestion" field
    And the suggestion should contain "verify the list_id"
    # POST-F1: System state unchanged (read-only)
    # POST-F2: Error code REFERENCE_NOT_FOUND
    # POST-F3: Application context echoed

  @T-UC-025-ext-f @extension @ext-f @error @post-f1 @post-f2 @post-f3
  Scenario: LIST_ACCESS_DENIED -- buyer lacks permission to access property list
    Given list_id "pl-other-tenant" exists but belongs to a different tenant
    When the Buyer Agent sends validate_property_delivery with list_id "pl-other-tenant"
    Then the operation should fail
    And the error code should be "LIST_ACCESS_DENIED"
    And the error message should contain "access denied"
    And the error should include "suggestion" field
    And the suggestion should contain "permission"
    # POST-F1: System state unchanged (read-only)
    # POST-F2: Error code LIST_ACCESS_DENIED
    # POST-F3: Application context echoed

  @T-UC-025-ext-g @extension @ext-g @error @post-f1 @post-f2 @post-f3
  Scenario Outline: RECORDS_REQUIRED -- records array <condition>
    Given property list "pl-valid" exists and is accessible
    When the Buyer Agent sends validate_property_delivery with list_id "pl-valid" and <records_state>
    Then the operation should fail
    And the error code should be "RECORDS_REQUIRED"
    And the error message should contain "records"
    And the error should include "suggestion" field
    And the suggestion should contain "at least one delivery record"
    # POST-F1: System state unchanged
    # POST-F2: Error code RECORDS_REQUIRED
    # POST-F3: Application context echoed

    Examples:
      | condition       | records_state          |
      | missing         | no records field       |
      | empty array     | records as empty array |

  @T-UC-025-ext-h @extension @ext-h @error @post-f1 @post-f2 @post-f3
  Scenario: RECORDS_LIMIT_EXCEEDED -- records array exceeds 10,000 maximum
    Given property list "pl-valid" exists and is accessible
    When the Buyer Agent sends validate_property_delivery with list_id "pl-valid" and 10001 records
    Then the operation should fail
    And the error code should be "RECORDS_LIMIT_EXCEEDED"
    And the error message should contain "10,000"
    And the error should include "suggestion" field
    And the suggestion should contain "batch"
    # POST-F1: System state unchanged
    # POST-F2: Error code RECORDS_LIMIT_EXCEEDED
    # POST-F3: Application context echoed

  @T-UC-025-partition-delivery-request @partition @delivery-request
  Scenario Outline: Delivery request partition validation -- <partition>
    When the Buyer Agent sends validate_property_delivery with <setup>
    Then <outcome>

    Examples: Valid partitions
      | partition             | setup                                                                              | outcome                                             |
      | minimal_valid         | list_id "pl-123" and 1 record with identifier and 1000 impressions                 | success with summary and results                    |
      | typical_batch         | list_id "pl-123" and 2 records with record_ids                                     | success with 2 results                              |
      | max_batch             | list_id "pl-123" and 10000 records                                                 | success with summary for all records                |
      | with_authorization    | list_id "pl-123" and record with sales_agent_url                                   | success with authorization check                    |
      | include_compliant_true | list_id "pl-123" with include_compliant true                                      | success with all records in results                 |
      | zero_impressions      | list_id "pl-123" and record with 0 impressions                                     | success with 0 impressions counted                  |
      | mixed_auth_no_auth    | list_id "pl-123" with mixed sales_agent_url presence                               | success with partial authorization summary          |
      | account_single_omitted | list_id "pl-123" with account omitted by single-account agent                     | success (account conditional only when multi-account) |
      | account_multi_provided | list_id "pl-123" with account provided by multi-account agent                     | success with multi-account disambiguation           |

    Examples: Invalid partitions
      | partition             | setup                                                  | outcome                                             |
      | missing_list_id       | no list_id field                                       | error "LIST_ID_REQUIRED" with suggestion             |
      | missing_records       | list_id "pl-123" with no records field                 | error "RECORDS_REQUIRED" with suggestion             |
      | empty_records         | list_id "pl-123" with records as empty array           | error "RECORDS_REQUIRED" with suggestion             |
      | records_over_limit    | list_id "pl-123" with 10001 records                    | error "RECORDS_LIMIT_EXCEEDED" with suggestion       |
      | negative_impressions  | list_id "pl-123" with record having -1 impressions     | error "IMPRESSIONS_INVALID" with suggestion          |
      | list_not_found        | list_id "pl-nonexistent" with valid records            | error "REFERENCE_NOT_FOUND" with suggestion               |
      | list_access_denied    | list_id "pl-other-buyers" with valid records           | error "LIST_ACCESS_DENIED" with suggestion           |
      | account_multi_omitted | list_id "pl-123" with account omitted by multi-account agent | error "ACCOUNT_REQUIRED" with suggestion        |

  @T-UC-025-boundary-delivery-request @boundary @delivery-request
  Scenario Outline: Delivery request boundary validation -- <boundary_point>
    When the Buyer Agent sends validate_property_delivery with <setup>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                            | setup                                                | outcome                                              |
      | 0 records (empty array)                   | list_id "pl-123" with records []                     | error "RECORDS_REQUIRED" with suggestion              |
      | 1 record (minimum)                        | list_id "pl-123" with 1 record                       | success                                              |
      | 10000 records (maximum)                   | list_id "pl-123" with 10000 records                  | success                                              |
      | 10001 records (over limit)                | list_id "pl-123" with 10001 records                  | error "RECORDS_LIMIT_EXCEEDED" with suggestion        |
      | impressions = 0 (minimum)                 | record with impressions 0                            | success                                              |
      | impressions = -1 (below minimum)          | record with impressions -1                           | error "IMPRESSIONS_INVALID" with suggestion           |
      | include_compliant = false (default)       | include_compliant set to false                       | success with compliant records excluded              |
      | include_compliant = true                  | include_compliant set to true                        | success with all records included                    |
      | include_compliant omitted (defaults to false) | include_compliant not specified                  | success with compliant records excluded              |
      | list_id present and valid                 | list_id "pl-123" that exists                         | success                                              |
      | list_id absent                            | no list_id field                                     | error "LIST_ID_REQUIRED" with suggestion              |
      | list_id references nonexistent list       | list_id "pl-nonexistent"                             | error "REFERENCE_NOT_FOUND" with suggestion                |
      | list_id references inaccessible list      | list_id "pl-other-tenant"                            | error "LIST_ACCESS_DENIED" with suggestion            |
      | records absent                            | no records field                                     | error "RECORDS_REQUIRED" with suggestion              |
      | sales_agent_url present on some records   | mixed records with and without sales_agent_url       | success with partial authorization summary           |
      | sales_agent_url absent from all records   | no sales_agent_url on any record                     | success without authorization summary                |
      | account omitted (single-account agent)    | request without account; agent has 1 account         | success                                              |
      | account omitted (multi-account agent)     | request without account; agent has >1 accounts       | error "ACCOUNT_REQUIRED" with suggestion              |
      | account present with account_id (multi-account agent) | request with account_id; agent has >1 accounts | success                                          |
      | account present with brand+operator (multi-account agent) | request with brand+operator; agent has >1 accounts | success                                  |

  @T-UC-025-partition-delivery-response @partition @delivery-response
  Scenario Outline: Delivery response partition validation -- <partition>
    Given property list "pl-valid" exists with appropriate test data
    When the Buyer Agent validates delivery triggering <condition>
    Then <outcome>

    Examples: Valid partitions
      | partition                  | condition                                            | outcome                                                          |
      | all_compliant_default      | all records compliant with default filter             | results array empty, summary shows all compliant                 |
      | all_compliant_included     | all records compliant with include_compliant true     | results array contains all records with status compliant         |
      | mixed_compliance           | mix of compliant and non-compliant records            | summary reflects correct category counts                         |
      | with_failed_feature        | non-compliant record with features[] entry status failed | result includes features[] entry with feature_id and status     |
      | with_warning_feature       | record with features[] entry status warning           | result includes features[] entry with status "warning"           |
      | with_unevaluated_feature   | record with features[] entry status unevaluated       | result includes features[] entry with status "unevaluated"       |
      | with_authorization         | records with sales_agent_url                          | authorization_summary present with per-record auth results       |
      | with_aggregate             | agent provides optional aggregate scoring             | aggregate object present with score, grade, label                |
      | with_requirement_echoed    | buyer-authored failed feature requirement             | features[] entry echoes the unmet requirement                    |
      | reserved_record_namespace  | record:list_membership status failed                  | features[] entry with reserved record: feature_id namespace      |
      | reserved_delivery_namespace | delivery:seller_authorization status failed          | features[] entry with reserved delivery: feature_id namespace    |
      | unidentified_records       | record with unresolvable identifier type              | result has status unidentified                                   |
      | not_covered_records        | record with recognized identifier but no data         | result has status not_covered                                    |
      | root_compliant_true        | summary.non_compliant_records is 0                    | response includes root-level compliant true                      |
      | root_compliant_false       | summary.non_compliant_records > 0                     | response includes root-level compliant false                     |
      | root_compliant_omitted     | partial response (e.g., include_compliant false)      | root-level compliant may be omitted; consumers use summary counts |
      | confidence_in_range        | features[] entry with confidence 0.5                  | confidence in [0, 1] accepted                                    |
      | list_resolved_at_present   | response with optional list_resolved_at timestamp     | list_resolved_at present as ISO 8601                             |
      | list_resolved_at_absent    | response without list_resolved_at                     | only validated_at is required                                    |

    Examples: Invalid partitions
      | partition                           | condition                                     | outcome                                                       |
      | missing_summary                     | response missing summary object               | error "SUMMARY_REQUIRED" with suggestion                      |
      | summary_missing_counter             | summary missing one of the ten required counters | error "SUMMARY_REQUIRED" with suggestion                   |
      | inconsistent_counters               | summary total != sum of four buckets          | error "SUMMARY_INCONSISTENT" with suggestion                  |
      | unknown_validation_status           | result with unknown status value              | error "VALIDATION_STATUS_INVALID" with suggestion             |
      | unknown_feature_check_status        | features[] entry with status not in [passed/failed/warning/unevaluated] | error "FEATURE_STATUS_INVALID" with suggestion |
      | unknown_authorization_status        | authorization with unknown status             | error "AUTHORIZATION_STATUS_INVALID" with suggestion          |
      | authorization_summary_without_auth_records | auth summary present without auth records | error "AUTHORIZATION_SUMMARY_UNEXPECTED" with suggestion     |
      | reserved_namespace_misuse           | data feature uses record: or delivery: prefix | error "FEATURE_ID_RESERVED_NAMESPACE" with suggestion         |
      | confidence_below_zero               | features[] entry with confidence -0.1         | error "CONFIDENCE_OUT_OF_RANGE" with suggestion               |
      | confidence_above_one                | features[] entry with confidence 1.1          | error "CONFIDENCE_OUT_OF_RANGE" with suggestion               |
      | root_compliant_inconsistent         | compliant=true while summary.non_compliant_records > 0 | error "ROOT_COMPLIANT_INCONSISTENT" with suggestion  |

  @T-UC-025-boundary-delivery-response @boundary @delivery-response
  Scenario Outline: Delivery response boundary validation -- <boundary_point>
    Given property list "pl-valid" exists with appropriate test data
    When the Buyer Agent validates delivery with <condition>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                         | condition                                          | outcome                                                     |
      | validation status = compliant                          | compliant record                                   | valid status accepted                                       |
      | validation status = non_compliant                      | non-compliant record                               | valid status with at least one failed features[] entry      |
      | validation status = not_covered                        | not-covered record                                 | valid status accepted                                       |
      | validation status = unidentified                       | unidentified record                                | valid status accepted                                       |
      | validation status = unknown value                      | unknown status value                               | error "VALIDATION_STATUS_INVALID" with suggestion            |
      | feature-check-status = passed                          | features[] entry status passed                     | valid status accepted                                       |
      | feature-check-status = failed                          | features[] entry status failed                     | valid status with optional requirement echoed                |
      | feature-check-status = warning                         | features[] entry status warning                    | valid status accepted                                       |
      | feature-check-status = unevaluated                     | features[] entry status unevaluated                | valid status accepted                                       |
      | feature-check-status = unknown value                   | unknown feature-check-status value                 | error "FEATURE_STATUS_INVALID" with suggestion               |
      | authorization status = authorized                      | authorized agent                                   | valid authorization status                                  |
      | authorization status = unauthorized                    | unauthorized agent                                 | valid authorization status                                  |
      | authorization status = unknown                         | adagents.json unavailable                          | valid authorization status                                  |
      | authorization status = unknown value                   | unknown auth status value                          | error "AUTHORIZATION_STATUS_INVALID" with suggestion         |
      | authorization_summary present (records had sales_agent_url) | records with sales_agent_url                 | authorization_summary present                               |
      | authorization_summary absent (no auth records)         | no records with sales_agent_url                    | authorization_summary absent                                |
      | results empty (all compliant, default filter)          | all compliant with default filter                  | results array empty                                         |
      | results populated (include_compliant=true)             | all compliant with include_compliant true          | results array populated                                     |
      | summary counters total = sum of four buckets           | mixed status records                               | counters internally consistent                              |
      | summary counters total != sum of four buckets          | inconsistent summary                               | error "SUMMARY_INCONSISTENT" with suggestion                 |
      | summary missing one of ten counters                    | summary lacks not_covered_impressions              | error "SUMMARY_REQUIRED" with suggestion                     |
      | features[] entry has feature_id and status             | well-formed features[] entry                       | accepted                                                    |
      | features[] entry missing feature_id                    | features[] entry without feature_id                | error "FEATURE_ID_REQUIRED" with suggestion                  |
      | features[] entry missing status                        | features[] entry without status                    | error "FEATURE_STATUS_REQUIRED" with suggestion              |
      | features[] entry uses reserved record: namespace       | feature_id "record:list_membership"                | accepted (reserved for structural checks)                   |
      | features[] entry uses reserved delivery: namespace     | feature_id "delivery:seller_authorization"         | accepted (reserved for structural checks)                   |
      | data feature misuses reserved record: prefix           | data feature_id "record:custom"                    | error "FEATURE_ID_RESERVED_NAMESPACE" with suggestion        |
      | features[] entry confidence = 0.0 (min)                | confidence 0.0                                     | accepted                                                    |
      | features[] entry confidence = 1.0 (max)                | confidence 1.0                                     | accepted                                                    |
      | features[] entry confidence = -0.01 (below min)        | confidence -0.01                                   | error "CONFIDENCE_OUT_OF_RANGE" with suggestion              |
      | features[] entry confidence = 1.01 (above max)         | confidence 1.01                                    | error "CONFIDENCE_OUT_OF_RANGE" with suggestion              |
      | root compliant = true when non_compliant_records = 0   | summary.non_compliant_records = 0                  | root compliant true                                         |
      | root compliant = false when non_compliant_records > 0  | summary.non_compliant_records > 0                  | root compliant false                                        |
      | root compliant omitted (partial response)              | include_compliant false partial response           | root compliant omitted; consumers use summary counts        |
      | aggregate present with score/grade/label               | agent provides aggregate                           | aggregate object in response                                |
      | aggregate absent                                       | agent does not provide aggregate                   | no aggregate in response                                    |
      | validated_at present (required)                        | response includes validated_at                     | accepted                                                    |
      | validated_at absent (missing required)                 | response lacks validated_at                        | error "VALIDATED_AT_REQUIRED" with suggestion                |
      | list_resolved_at present (optional)                    | response includes list_resolved_at                 | accepted                                                    |
      | list_resolved_at absent (optional)                     | response lacks list_resolved_at                    | accepted                                                    |

  @T-UC-025-inv191-include-compliant-default @invariant @BR-RULE-191
  Scenario: BR-RULE-191 INV-5 holds -- include_compliant defaults to false
    Given property list "pl-valid" with all records compliant
    When the Buyer Agent validates delivery without include_compliant parameter
    Then compliant records are excluded from the results array
    # BR-RULE-191 INV-5: default false excludes compliant records

  @T-UC-025-inv191-auth-triggered @invariant @BR-RULE-191
  Scenario: BR-RULE-191 INV-6 holds -- sales_agent_url triggers authorization check
    Given property list "pl-valid" exists and is accessible
    And the delivery record includes sales_agent_url "https://agent.example.com"
    When the Buyer Agent validates delivery with that record
    Then the result includes an authorization field
    # BR-RULE-191 INV-6: authorization check triggered by URL presence

  @T-UC-025-inv191-no-auth @invariant @BR-RULE-191
  Scenario: BR-RULE-191 INV-7 holds -- no sales_agent_url means no authorization summary
    Given property list "pl-valid" exists and is accessible
    And no delivery records include sales_agent_url
    When the Buyer Agent validates delivery with those records
    Then authorization_summary is absent from the response
    # BR-RULE-191 INV-7: no auth URLs means no auth summary

  @T-UC-025-inv191-account-required @invariant @BR-RULE-191 @error
  Scenario: BR-RULE-191 INV-8 violated -- multi-account agent omits account
    Given the authenticated agent has access to multiple accounts
    And the request omits the account reference
    When the Buyer Agent sends validate_property_delivery with list_id "pl-valid"
    Then the operation should fail
    And the error code should be "ACCOUNT_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "account"
    # BR-RULE-191 INV-8: multi-account agent must disambiguate
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

  @T-UC-025-inv191-account-single @invariant @BR-RULE-191
  Scenario: BR-RULE-191 INV-8 counter -- single-account agent omits account
    Given the authenticated agent has access to exactly one account
    And the request omits the account reference
    When the Buyer Agent sends validate_property_delivery with list_id "pl-valid"
    Then the request succeeds
    # BR-RULE-191 INV-8: account conditional only when multi-account

  @T-UC-025-inv192-exclusive-status @invariant @BR-RULE-192
  Scenario: BR-RULE-192 INV-1 holds -- each record has exactly one validation status
    Given property list "pl-valid" with 4 records resolving to different statuses
    When the Buyer Agent validates delivery with those records
    Then each result has exactly one status from: compliant, non_compliant, not_covered, unidentified
    And no result has more than one status
    # BR-RULE-192 INV-1: mutually exclusive status

  @T-UC-025-inv192-violations-required @deprecated @invariant @BR-RULE-192
  Scenario: BR-RULE-192 INV-2 holds -- non_compliant records have violations
    Given property list "pl-valid" with a record that resolves to non_compliant
    When the Buyer Agent validates delivery with that record
    Then the result has status "non_compliant"
    And the violations array is present with at least one entry
    # BR-RULE-192 INV-2: violations required for non_compliant
    # DEPRECATED: upstream removed -- BR-RULE-192 INV-2 (violations[]) removed in v3.1
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

  @T-UC-025-inv192-violations-absent-compliant @deprecated @invariant @BR-RULE-192
  Scenario: BR-RULE-192 INV-2 counter-example -- compliant records have no violations
    Given property list "pl-valid" with a record that resolves to compliant
    When the Buyer Agent validates delivery with include_compliant true
    Then the result has status "compliant"
    And no violations field is present on the result
    # BR-RULE-192 INV-2 counter: compliant = no violations
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

  @T-UC-025-inv192-auth-summary-present @invariant @BR-RULE-192
  Scenario: BR-RULE-192 INV-3 holds -- authorization_summary present when auth checks performed
    Given property list "pl-valid" exists
    And at least one delivery record includes sales_agent_url
    When the Buyer Agent validates delivery
    Then authorization_summary is present in the response
    # BR-RULE-192 INV-3: auth summary present

  @T-UC-025-inv192-auth-summary-absent @invariant @BR-RULE-192
  Scenario: BR-RULE-192 INV-3 counter -- authorization_summary absent without auth records
    Given property list "pl-valid" exists
    And no delivery records include sales_agent_url
    When the Buyer Agent validates delivery
    Then authorization_summary is absent from the response
    # BR-RULE-192 INV-3 counter: no auth checks = no summary

  @T-UC-025-inv192-counter-records @invariant @BR-RULE-192
  Scenario: BR-RULE-192 INV-4 holds -- total_records consistency
    Given property list "pl-valid" with mixed compliance records
    When the Buyer Agent validates delivery
    Then summary.total_records equals compliant_records + non_compliant_records + not_covered_records + unidentified_records
    # BR-RULE-192 INV-4: counter arithmetic

  @T-UC-025-inv192-counter-impressions @invariant @BR-RULE-192
  Scenario: BR-RULE-192 INV-5 holds -- total_impressions consistency
    Given property list "pl-valid" with mixed compliance records and varying impression counts
    When the Buyer Agent validates delivery
    Then summary.total_impressions equals compliant_impressions + non_compliant_impressions + not_covered_impressions + unidentified_impressions
    # BR-RULE-192 INV-5: impression counter arithmetic

  @T-UC-025-inv192-auth-counter @invariant @BR-RULE-192
  Scenario: BR-RULE-192 INV-6 holds -- authorization summary counter consistency
    Given property list "pl-valid" with records that include sales_agent_url
    When the Buyer Agent validates delivery
    Then authorization_summary.records_checked equals authorized_records + unauthorized_records + unknown_records
    # BR-RULE-192 INV-6: auth counter arithmetic

  @T-UC-025-inv192-filter-default @invariant @BR-RULE-192
  Scenario: BR-RULE-192 INV-7 holds -- default filter excludes compliant records
    Given property list "pl-valid" with 5 compliant and 3 non-compliant records
    When the Buyer Agent validates delivery with include_compliant omitted
    Then the results array contains only 3 non-compliant records
    # BR-RULE-192 INV-7: default filter behavior

  @T-UC-025-inv192-filter-true @invariant @BR-RULE-192
  Scenario: BR-RULE-192 INV-7 holds -- include_compliant=true returns all records
    Given property list "pl-valid" with 5 compliant and 3 non-compliant records
    When the Buyer Agent validates delivery with include_compliant true
    Then the results array contains all 8 records
    # BR-RULE-192 INV-7: include_compliant=true returns everything

  @T-UC-025-inv192-feature-status-enum @invariant @BR-RULE-192 @v3-1
  Scenario Outline: BR-RULE-192 INV-8 holds -- features[] status drawn from feature-check-status enum -- <status>
    Given property list "pl-valid" exists and is accessible
    And the delivery record yields a features[] entry with status "<status>"
    When the Buyer Agent validates delivery with that record
    Then the result features[] entry has status "<status>"
    # BR-RULE-192 INV-8: feature-check-status enum is [passed / failed / warning / unevaluated]
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

    Examples:
      | status      |
      | passed      |
      | failed      |
      | warning     |
      | unevaluated |

  @T-UC-025-inv192-reserved-namespace @invariant @BR-RULE-192 @v3-1
  Scenario Outline: BR-RULE-192 INV-9 holds -- reserved feature_id namespaces accepted for structural checks -- <feature_id>
    Given property list "pl-valid" exists and is accessible
    And the structural check yields a features[] entry with feature_id "<feature_id>"
    When the Buyer Agent validates delivery
    Then the features[] entry feature_id is "<feature_id>"
    And the reserved namespace prefix is accepted for structural checks
    # BR-RULE-192 INV-9: record: and delivery: prefixes reserved for structural checks; MUST NOT be used for data features
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

    Examples:
      | feature_id                       |
      | record:list_membership           |
      | record:excluded                  |
      | delivery:seller_authorization    |
      | delivery:click_url_presence      |

  @T-UC-025-inv192-requirement-echoed @invariant @BR-RULE-192 @v3-1
  Scenario: BR-RULE-192 INV-10 holds -- buyer-authored failed feature echoes requirement
    Given property list "pl-valid" with buyer-authored feature_requirements: carbon_score min_value 50
    And the delivery record fails the carbon_score requirement
    When the Buyer Agent validates delivery with that record
    Then the features[] entry for "carbon_score" has status "failed"
    And the features[] entry requirement min_value equals 50
    # BR-RULE-192 INV-10: enables fix-and-retry loop without re-fetching list definition
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

  @T-UC-025-inv192-feature-confidence-range @invariant @BR-RULE-192 @v3-1 @boundary
  Scenario Outline: BR-RULE-192 INV-11 -- features[] confidence range [0, 1] -- <case>
    Given property list "pl-valid" exists and is accessible
    And the delivery record yields a features[] entry with confidence <value>
    When the Buyer Agent validates delivery with that record
    Then the result is <outcome>
    # BR-RULE-192 INV-11: confidence bounded [0, 1] (migrated from BR-RULE-190 INV-4)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

    Examples:
      | case             | value | outcome                                              |
      | minimum boundary | 0.0   | accepted                                             |
      | midpoint         | 0.5   | accepted                                             |
      | maximum boundary | 1.0   | accepted                                             |
      | below minimum    | -0.01 | rejected with error code "CONFIDENCE_OUT_OF_RANGE"   |
      | above maximum    | 1.01  | rejected with error code "CONFIDENCE_OUT_OF_RANGE"   |

  @T-UC-025-inv192-root-compliant-derivation @invariant @BR-RULE-192 @v3-1 @post-s7
  Scenario Outline: BR-RULE-192 INV-12 holds -- root compliant flag derivation -- <case>
    Given property list "pl-valid" exists and is accessible
    When the Buyer Agent validates delivery yielding summary.non_compliant_records = <non_compliant>
    Then the response root compliant flag is <root_compliant>
    # POST-S7 / BR-RULE-192 INV-12: compliant=true iff non_compliant_records===0
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

    Examples:
      | case                          | non_compliant | root_compliant                                                                |
      | all compliant                 | 0             | true                                                                          |
      | at least one non_compliant    | 1             | false                                                                         |
      | many non_compliant            | 17            | false                                                                         |
      | partial response (include_compliant false) | 0  | may be omitted; consumers fall back to summary counts                         |

  @T-UC-025-inv192-not-covered-vs-unidentified @invariant @BR-RULE-192 @v3-1
  Scenario Outline: BR-RULE-192 INV-13 holds -- not_covered vs unidentified distinct -- <case>
    Given property list "pl-valid" exists and is accessible
    And the delivery record identifier is <identifier_state>
    When the Buyer Agent validates delivery with that record
    Then the result has status "<status>"
    And the status is distinct from non_compliant
    # BR-RULE-192 INV-13: not_covered = identifier recognized but no data; unidentified = identifier type not resolvable
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

    Examples:
      | case                                | identifier_state                                              | status        |
      | recognized identifier, no data      | type "url" with value example.com but no compliance data       | not_covered   |
      | identifier type not resolvable      | type "custom:unknown_taxonomy" with value xyz                  | unidentified  |

  @T-UC-025-inv192-ten-counters @invariant @BR-RULE-192 @v3-1
  Scenario: BR-RULE-192 INV-14 holds -- summary includes all ten required counters
    Given property list "pl-valid" exists and is accessible
    When the Buyer Agent validates delivery with at least one record
    Then the summary object contains total_records and total_impressions
    And the summary object contains compliant_records and compliant_impressions
    And the summary object contains non_compliant_records and non_compliant_impressions
    And the summary object contains not_covered_records and not_covered_impressions
    And the summary object contains unidentified_records and unidentified_impressions
    # BR-RULE-192 INV-14: all ten counters are required
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/property/validate-property-delivery-request.json

  @T-UC-025-features-absent-allowed @main-flow @v3-1 @post-s5
  Scenario: features[] is optional -- record may omit features[] when no features evaluated
    Given property list "pl-valid" exists and is accessible
    And the delivery record has no applicable features for evaluation
    When the Buyer Agent validates delivery with that record
    Then the result is well-formed
    And features[] is either omitted or empty for that record
    # POST-S5: per-feature breakdown is optional; absence permitted when no features evaluated

  @T-UC-025-ext-k @main-flow @extension @ext-k @v3-1 @post-s3
  Scenario Outline: Extension K: Partial coverage classification -- <status>
    Given property list "pl-valid" exists and is accessible
    And the delivery record identifier <identifier_state>
    When the Buyer Agent validates delivery with that record
    Then the result has status "<status>"
    And the summary counters <counter_state>
    And the record is included in results regardless of include_compliant
    # POST-S3: partial-coverage statuses distinguished from non_compliant
    # BR-RULE-192 INV-13: not_covered vs unidentified

    Examples:
      | status        | identifier_state                                | counter_state                                                       |
      | not_covered   | is recognized but has no compliance data         | not_covered_records and not_covered_impressions incremented         |
      | unidentified  | type is not resolvable by the governance agent   | unidentified_records and unidentified_impressions incremented       |

  @T-UC-025-ext-l @main-flow @extension @ext-l @v3-1 @post-s5
  Scenario Outline: Extension L: Per-feature failure detail -- <status>
    Given property list "pl-valid" with feature requirements
    And the delivery record yields a features[] entry with status "<status>"
    When the Buyer Agent validates delivery with that record
    Then the features[] entry has feature_id and status "<status>"
    And the features[] entry MAY include policy_id, explanation, and confidence
    And <requirement_clause>
    # POST-S5: per-feature failure breakdown enables fix-and-retry
    # BR-RULE-192 INV-8: feature-check-status enum membership

    Examples:
      | status   | requirement_clause                                                                  |
      | failed   | when the buyer authored the requirement, the unmet requirement is echoed on the entry |
      | warning  | the requirement field MAY be absent for a warning entry                              |

  @T-UC-025-ext-m @main-flow @extension @ext-m @v3-1 @post-s4
  Scenario Outline: Extension M: Supply-path authorization failure -- <auth_status>
    Given property list "pl-valid" exists and is accessible
    And the delivery record includes sales_agent_url "https://agent.example.com/.well-known/adcp"
    And the publisher's adagents.json <adagents_state>
    When the Buyer Agent validates delivery with that record
    Then the result authorization status is "<auth_status>"
    And <violation_clause>
    And the authorization_summary <summary_clause>
    # POST-S4: authorization status independent of property compliance
    # POST-F3: context echoed (sales_agent_url and adagents.json provenance)

    Examples:
      | auth_status   | adagents_state                                  | violation_clause                                              | summary_clause                            |
      | unauthorized  | does not list the sales agent                    | the authorization.violation field is present with code and message | unauthorized_records counter is incremented |
      | unknown       | could not be fetched or parsed                   | the authorization status reflects inability to determine        | unknown_records counter is incremented      |

  @T-UC-025-ext-n @extension @ext-n @v3-1 @error @post-f1 @post-f2 @post-f3
  Scenario: Extension N: ACCOUNT_REQUIRED -- multi-account agent omits account
    Given the authenticated agent has access to multiple accounts
    And the request omits the account reference required to disambiguate list ownership
    When the Buyer Agent sends validate_property_delivery with list_id "pl-valid" and 1 record
    Then the operation should fail
    And the error code should be "ACCOUNT_REQUIRED"
    And the error message should indicate that an account reference must be supplied
    And the error should include "suggestion" field
    And the suggestion should contain "account"
    # POST-F1: System state unchanged (read-only)
    # POST-F2: Error code ACCOUNT_REQUIRED
    # POST-F3: Application context echoed

  @T-UC-025-timestamps @main-flow @post-s6
  Scenario: Delivery validation response includes timestamps
    Given property list "pl-valid" exists and was resolved at a known timestamp
    When the Buyer Agent validates delivery against "pl-valid"
    Then the response includes validated_at as an ISO 8601 timestamp
    And the response may include list_resolved_at showing when the list was last resolved
    # POST-S6: validated_at required; list_resolved_at optional
