# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-025 Discover Property Features & Validate Delivery Compliance
  As a Buyer
  I want to discover property-level feature values and validate delivery records against property lists
  So that I can assess property governance compliance and supply path authorization

  # Postconditions verified:
  #   POST-S1: Buyer knows feature values for requested properties with coverage status and measurement metadata
  #   POST-S2: Buyer knows which properties could not be evaluated and the specific error reason
  #   POST-S3: Buyer knows compliance status of delivery records with aggregate statistics
  #   POST-S4: Buyer knows supply path authorization status for records with sales_agent_url
  #   POST-S5: Buyer knows specific violations for non-compliant records
  #   POST-S6: Buyer knows when validation was performed and which property list resolution was used
  #   POST-F1: System state is unchanged on failure (both operations are read-only)
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Application context is still echoed when possible
  #
  # Rules: BR-RULE-189 (Feature Discovery Request), BR-RULE-190 (Feature Value Semantics),
  #        BR-RULE-191 (Delivery Validation Request), BR-RULE-192 (Delivery Validation Response)
  # Extensions: A (Validate Delivery MCP), B (Validate Delivery REST), C (PROPERTY_NOT_FOUND),
  #   D (PROPERTY_NOT_MONITORED), E (LIST_NOT_FOUND), F (LIST_ACCESS_DENIED),
  #   G (RECORDS_REQUIRED), H (RECORDS_LIMIT_EXCEEDED), I (PROPERTY_SELECTION_REQUIRED),
  #   J (PROPERTIES_LIMIT_EXCEEDED)

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And the Buyer is authenticated with a valid principal_id


  @T-UC-025-main-mcp @main-flow @mcp @post-s1
  Scenario: Feature discovery via MCP -- explicit properties mode
    Given the Buyer Agent has an authenticated connection
    And properties "example.com" and "news.example.com" are known and monitored
    And the governance agent has feature data for consent_quality and carbon_score
    When the Buyer Agent invokes get_property_features with properties ["example.com", "news.example.com"]
    Then the response contains a results array with 2 property feature results
    And each result includes a coverage_status of "covered"
    And each result includes a features map with typed values
    And the request context is echoed in the response
    # POST-S1: Buyer knows feature values with coverage status and metadata

  @T-UC-025-main-rest @main-flow @rest @post-s1
  Scenario: Feature discovery via REST/A2A -- publisher domain mode
    Given the Buyer Agent has an authenticated connection
    And publisher "publisher.example.com" has 5 monitored properties
    When the Buyer Agent sends get_property_features via A2A with publisher_domain "publisher.example.com"
    Then the response contains a results array with 5 property feature results
    And each result includes a coverage_status
    And the request context is echoed in the response
    # POST-S1: Buyer knows feature values for publisher's properties

  @T-UC-025-main-transport @main-flow @post-s1
  Scenario Outline: Feature discovery via <transport> -- both transports produce equivalent results
    Given the Buyer Agent has an authenticated connection
    And property "example.com" is known with feature consent_quality (binary, value true, confidence 0.95)
    When the Buyer Agent sends get_property_features via <transport> with properties ["example.com"]
    Then the response contains 1 property feature result for "example.com"
    And the result has coverage_status "covered"
    And the features map contains consent_quality with boolean value true
    And the consent_quality confidence is 0.95
    # POST-S1: Equivalent results regardless of transport

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-025-main-publisher-filters @main-flow @post-s1
  Scenario: Feature discovery -- publisher domain mode with type and tag filters
    Given publisher "media.example.com" has properties of types website, mobile_app, and ctv_app
    And some properties are tagged "premium"
    When the Buyer Agent sends get_property_features with publisher_domain "media.example.com" and property_types ["website"] and property_tags ["premium"]
    Then the response contains results only for website properties tagged premium
    # BR-RULE-189 INV-6: type/tag filters apply in publisher_domain mode

  @T-UC-025-main-feature-filter @main-flow @post-s1
  Scenario: Feature discovery -- optional feature_ids filter returns subset of features
    Given property "example.com" is known with features consent_quality, carbon_score, and coppa_certified
    When the Buyer Agent sends get_property_features with properties ["example.com"] and feature_ids ["carbon_score"]
    Then the response result for "example.com" contains only carbon_score in the features map
    # BR-RULE-189 INV-5: feature_ids filter narrows returned features

  @T-UC-025-main-all-features @main-flow @post-s1
  Scenario: Feature discovery -- feature_ids omitted returns all available features
    Given property "example.com" is known with features consent_quality, carbon_score, and coppa_certified
    When the Buyer Agent sends get_property_features with properties ["example.com"] without feature_ids
    Then the response result for "example.com" contains all 3 features in the features map
    # BR-RULE-189 INV-5: omitting feature_ids returns all features

  @T-UC-025-feature-types @main-flow @invariant @BR-RULE-190 @post-s1
  Scenario Outline: Feature value type matching -- <feature_type> feature returns <value_type> value
    Given property "example.com" has feature "<feature_id>" defined as <feature_type>
    And the feature value is <value>
    When the Buyer Agent sends get_property_features with properties ["example.com"]
    Then the features map for "example.com" contains "<feature_id>" with <value_type> value <value>
    # BR-RULE-190 INV-1/2/3: Value type matches feature definition type

    Examples:
      | feature_type  | feature_id       | value_type | value     |
      | binary        | coppa_certified  | boolean    | true      |
      | quantitative  | carbon_score     | number     | 42.5      |
      | categorical   | consent_tier     | string     | "tier_1"  |

  @T-UC-025-coverage-status @main-flow @invariant @BR-RULE-190 @post-s1
  Scenario Outline: Coverage status classification -- <coverage_status>
    Given property "<property>" has coverage status "<coverage_status>"
    When the Buyer Agent sends get_property_features with properties ["<property>"]
    Then the result for "<property>" has coverage_status "<coverage_status>"
    And the features map is <features_state>
    # BR-RULE-190 INV-7: not_covered/pending may have empty features

    Examples:
      | property         | coverage_status | features_state                   |
      | monitored.com    | covered         | populated with feature values    |
      | unmeasured.com   | not_covered     | empty or absent                  |
      | measuring.com    | pending         | empty or absent                  |

  @T-UC-025-partial-success @main-flow @post-s1 @post-s2
  Scenario: Partial success -- some properties succeed while others fail
    Given property "good.com" is known and monitored
    And property "unknown.com" is not recognized by the governance agent
    And property "not-monitored.com" is recognized but not monitored
    When the Buyer Agent sends get_property_features with properties ["good.com", "unknown.com", "not-monitored.com"]
    Then the results array contains 1 result for "good.com" with coverage_status "covered"
    And the errors array contains 2 entries
    And one error has code "PROPERTY_NOT_FOUND" for "unknown.com"
    And one error has code "PROPERTY_NOT_MONITORED" for "not-monitored.com"
    # POST-S1: Buyer gets results for successful properties
    # POST-S2: Buyer knows which properties failed and why
    # BR-RULE-190 INV-5, INV-6: error entries for unresolvable properties

  @T-UC-025-delivery-mcp @main-flow @ext-a @mcp @post-s3 @post-s5 @post-s6
  Scenario: Delivery validation via MCP -- validate records against property list
    Given the Buyer Agent has an authenticated connection
    And property list "pl-valid" exists and is accessible by the buyer
    And the list contains properties "good.com" and "approved.com"
    When the Buyer Agent invokes validate_property_delivery with list_id "pl-valid" and records containing deliveries to "good.com" (1000 impressions) and "bad.com" (500 impressions)
    Then the response contains list_id "pl-valid"
    And the summary shows total_records 2 with compliant_records 1 and non_compliant_records 1
    And the results array contains the non-compliant record for "bad.com"
    And the non-compliant record has violations with code "not_in_list"
    And the response includes validated_at timestamp
    # POST-S3: Buyer knows compliance status
    # POST-S5: Buyer knows violation details
    # POST-S6: Buyer knows validation timestamp

  @T-UC-025-delivery-rest @main-flow @ext-b @rest @post-s3 @post-s6
  Scenario: Delivery validation via REST/A2A -- same semantics as MCP
    Given the Buyer Agent has an authenticated connection
    And property list "pl-valid" exists and is accessible by the buyer
    When the Buyer Agent sends validate_property_delivery via A2A with list_id "pl-valid" and 10 delivery records
    Then the response contains list_id "pl-valid"
    And the summary shows all 10 counters correctly
    And the response includes validated_at timestamp
    # POST-S3: Compliance status via REST
    # POST-S6: Validation timestamp and list resolution

  @T-UC-025-delivery-transport @main-flow @ext-a @ext-b @post-s3
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

  @T-UC-025-validation-statuses @main-flow @ext-a @invariant @BR-RULE-192 @post-s3
  Scenario Outline: Validation status classification -- <status>
    Given property list "pl-valid" exists and is accessible
    And the delivery record for "<property>" resolves to status "<status>"
    When the Buyer Agent validates delivery with list_id "pl-valid" and 1 record for "<property>"
    Then the result has status "<status>"
    And <violation_state>
    # BR-RULE-192 INV-1: exactly one of four statuses
    # BR-RULE-192 INV-2: non_compliant records have violations

    Examples:
      | status         | property         | violation_state                                 |
      | compliant      | good.com         | no violations present                           |
      | non_compliant  | bad.com          | violations array present with at least one entry |
      | not_covered    | no-data.com      | no violations present                           |
      | unidentified   | custom-id-xyz    | no violations present                           |

  @T-UC-025-violation-codes @main-flow @ext-a @post-s5
  Scenario Outline: Violation code detail -- <violation_code>
    Given property list "pl-valid" exists and is accessible
    And the delivery record triggers violation "<violation_code>"
    When the Buyer Agent validates delivery with that record
    Then the result has status "non_compliant"
    And the violations array contains an entry with code "<violation_code>"
    And the violation entry includes a message
    # POST-S5: Buyer knows specific violations
    # BR-RULE-192 INV-2: non_compliant has violations

    Examples:
      | violation_code    |
      | not_in_list       |
      | excluded          |
      | country_mismatch  |
      | channel_mismatch  |
      | feature_failed    |

  @T-UC-025-feature-failed-detail @main-flow @ext-a @post-s5
  Scenario: Violation detail -- feature_failed includes feature_id and requirement
    Given property list "pl-valid" with feature requirement carbon_score min_value 50
    And the delivery record is for property "low-carbon.com" which has carbon_score 30
    When the Buyer Agent validates delivery with that record
    Then the result has status "non_compliant"
    And the violations array contains code "feature_failed" with feature_id "carbon_score"
    And the violation includes the requirement that was not met
    # POST-S5: Detailed violation with feature context

  @T-UC-025-auth-check @main-flow @ext-a @post-s4
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

  @T-UC-025-auth-statuses @main-flow @ext-a @invariant @BR-RULE-192 @post-s4
  Scenario Outline: Authorization status classification -- <auth_status>
    Given property list "pl-valid" exists and is accessible
    And the delivery record includes sales_agent_url and authorization resolves to "<auth_status>"
    When the Buyer Agent validates delivery with that record
    Then the result authorization status is "<auth_status>"
    # BR-RULE-192 INV-3: auth status is three-valued enum

    Examples:
      | auth_status  |
      | authorized   |
      | unauthorized |
      | unknown      |

  @T-UC-025-auth-no-url @main-flow @ext-a @invariant @BR-RULE-192 @post-s4
  Scenario: No authorization check -- records without sales_agent_url skip auth
    Given property list "pl-valid" exists and is accessible
    And the delivery records do not include sales_agent_url
    When the Buyer Agent validates delivery with those records
    Then no authorization field is present on any result
    And authorization_summary is absent from the response
    # BR-RULE-191 INV-7: no auth check without sales_agent_url
    # BR-RULE-192 INV-3: authorization_summary absent when no auth records

  @T-UC-025-auth-mixed @main-flow @ext-a @post-s4
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

  @T-UC-025-auth-independence @main-flow @ext-a @invariant @BR-RULE-192 @post-s3 @post-s4
  Scenario: Authorization independent of compliance -- compliant but unauthorized
    Given property list "pl-valid" with property "legit.com"
    And the delivery record for "legit.com" includes sales_agent_url for an unauthorized agent
    When the Buyer Agent validates delivery with that record
    Then the result has status "compliant" (property is in list)
    And the result has authorization status "unauthorized" (agent not in adagents.json)
    # BR-RULE-192: compliance and authorization are independent checks

  @T-UC-025-summary-consistency @main-flow @ext-a @invariant @BR-RULE-192 @post-s3
  Scenario: Summary counters are internally consistent
    Given property list "pl-valid" exists and is accessible
    And 100 delivery records resolve to: 60 compliant, 25 non_compliant, 10 not_covered, 5 unidentified
    When the Buyer Agent validates delivery with those 100 records
    Then summary total_records equals 100
    And total_records equals compliant_records + non_compliant_records + not_covered_records + unidentified_records
    And total_impressions equals compliant_impressions + non_compliant_impressions + not_covered_impressions + unidentified_impressions
    # BR-RULE-192 INV-4: total_records consistency
    # BR-RULE-192 INV-5: total_impressions consistency

  @T-UC-025-auth-summary-consistency @main-flow @ext-a @invariant @BR-RULE-192 @post-s4
  Scenario: Authorization summary counters are internally consistent
    Given property list "pl-valid" exists and is accessible
    And 50 delivery records include sales_agent_url: 40 authorized, 7 unauthorized, 3 unknown
    When the Buyer Agent validates delivery with those records
    Then authorization_summary records_checked equals 50
    And records_checked equals authorized_records + unauthorized_records + unknown_records
    # BR-RULE-192 INV-6: authorization summary consistency

  @T-UC-025-include-compliant @main-flow @ext-a @invariant @BR-RULE-192 @post-s3
  Scenario Outline: Include compliant flag -- <flag_state>
    Given property list "pl-valid" with 10 records: 8 compliant, 2 non_compliant
    When the Buyer Agent validates delivery with include_compliant <flag_state>
    Then the results array contains <expected_count> records
    # BR-RULE-191 INV-5: include_compliant default behavior
    # BR-RULE-192 INV-7: results filtering

    Examples:
      | flag_state          | expected_count |
      | omitted (default)   | 2              |
      | set to false        | 2              |
      | set to true         | 10             |

  @T-UC-025-all-compliant-default @main-flow @ext-a @post-s3
  Scenario: All compliant records with default filter -- results array empty
    Given property list "pl-valid" with all records compliant
    When the Buyer Agent validates delivery with include_compliant omitted
    Then the results array is empty
    And the summary shows all records as compliant
    # BR-RULE-192 INV-7: all compliant + default filter = empty results

  @T-UC-025-ext-c @extension @ext-c @error @post-s2
  Scenario: PROPERTY_NOT_FOUND -- unrecognized property in feature discovery
    Given property "unknown-domain.com" is not recognized by the governance agent
    And property "known.com" is known and monitored
    When the Buyer Agent sends get_property_features with properties ["unknown-domain.com", "known.com"]
    Then the results array contains the result for "known.com"
    And the errors array contains an entry with code "PROPERTY_NOT_FOUND" for "unknown-domain.com"
    And the error entry includes a diagnostic message
    And the error should include "suggestion" field
    And the suggestion should contain "verify the property identifier"
    # POST-S2: Buyer knows which property was not found
    # POST-S1: Other properties still return results (partial success)
    # POST-F3: Context echoed

  @T-UC-025-ext-d @extension @ext-d @error @post-s2
  Scenario: PROPERTY_NOT_MONITORED -- recognized but unmonitored property
    Given property "known-unmonitored.com" is recognized but not monitored by the governance agent
    And property "monitored.com" is known and monitored
    When the Buyer Agent sends get_property_features with properties ["known-unmonitored.com", "monitored.com"]
    Then the results array contains the result for "monitored.com"
    And the errors array contains an entry with code "PROPERTY_NOT_MONITORED" for "known-unmonitored.com"
    And the error entry includes a diagnostic message
    And the error should include "suggestion" field
    And the suggestion should contain "property is not within monitoring coverage"
    # POST-S2: Buyer knows which property is not monitored
    # POST-S1: Other properties still return results (partial success)
    # POST-F3: Context echoed

  @T-UC-025-ext-e @extension @ext-e @error @post-f1 @post-f2 @post-f3
  Scenario: LIST_NOT_FOUND -- property list does not exist
    Given list_id "pl-nonexistent" does not reference any existing property list
    When the Buyer Agent sends validate_property_delivery with list_id "pl-nonexistent"
    Then the operation should fail
    And the error code should be "LIST_NOT_FOUND"
    And the error message should contain "not found"
    And the error should include "suggestion" field
    And the suggestion should contain "verify the list_id"
    # POST-F1: System state unchanged (read-only)
    # POST-F2: Error code LIST_NOT_FOUND
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

  @T-UC-025-ext-i @extension @ext-i @error @post-f1 @post-f2 @post-f3
  Scenario Outline: PROPERTY_SELECTION_REQUIRED -- <condition>
    When the Buyer Agent sends get_property_features with <request_state>
    Then the operation should fail
    And the error code should be "PROPERTY_SELECTION_REQUIRED"
    And the error message should contain "properties"
    And the error should include "suggestion" field
    And the suggestion should contain "properties" or "publisher_domain"
    # POST-F1: System state unchanged
    # POST-F2: Error code PROPERTY_SELECTION_REQUIRED
    # POST-F3: Application context echoed
    # BR-RULE-189 INV-1/INV-2: oneOf constraint

    Examples:
      | condition                     | request_state                                                 |
      | neither mode provided         | neither properties nor publisher_domain                       |
      | both modes provided           | both properties ["x.com"] and publisher_domain "x.com"        |

  @T-UC-025-ext-j @extension @ext-j @error @post-f1 @post-f2 @post-f3
  Scenario: PROPERTIES_LIMIT_EXCEEDED -- properties array exceeds 100 maximum
    When the Buyer Agent sends get_property_features with 101 properties
    Then the operation should fail
    And the error code should be "PROPERTIES_LIMIT_EXCEEDED"
    And the error message should contain "100"
    And the error should include "suggestion" field
    And the suggestion should contain "publisher_domain" or "batch"
    # POST-F1: System state unchanged
    # POST-F2: Error code PROPERTIES_LIMIT_EXCEEDED
    # POST-F3: Application context echoed

  @T-UC-025-partition-discovery @partition @discovery-request
  Scenario Outline: Discovery request partition validation -- <partition>
    When the Buyer Agent sends get_property_features with <setup>
    Then <outcome>

    Examples: Valid partitions
      | partition                      | setup                                                                                     | outcome                                        |
      | explicit_properties_typical    | properties ["example.com", "news.example.com"]                                            | success with results for both properties       |
      | explicit_properties_min        | properties ["example.com"] (1 item)                                                       | success with result for 1 property             |
      | explicit_properties_max        | properties with exactly 100 items                                                         | success with results for 100 properties        |
      | publisher_domain_only          | publisher_domain "example.com" only                                                       | success with results for publisher properties  |
      | publisher_domain_with_filters  | publisher_domain "example.com" with property_types ["website"] and property_tags ["premium"] | success with filtered results                |
      | either_mode_with_feature_filter | publisher_domain "example.com" with feature_ids ["carbon_score"]                          | success with filtered features                 |

    Examples: Invalid partitions
      | partition                 | setup                                                              | outcome                                                       |
      | neither_mode              | neither properties nor publisher_domain                            | error "PROPERTY_SELECTION_REQUIRED" with suggestion            |
      | both_modes                | both properties and publisher_domain                               | error "PROPERTY_SELECTION_CONFLICT" with suggestion            |
      | properties_empty_array    | properties as empty array []                                       | error "PROPERTIES_REQUIRED" with suggestion                    |
      | properties_over_limit     | properties with 101 items                                          | error "PROPERTIES_LIMIT_EXCEEDED" with suggestion              |
      | property_type_unknown     | publisher_domain with property_types ["hologram"]                  | error "PROPERTY_TYPE_INVALID" with suggestion                  |
      | property_tag_invalid_pattern | publisher_domain with property_tags ["Premium!"]                | error "PROPERTY_TAG_INVALID_FORMAT" with suggestion            |
      | publisher_domain_empty    | publisher_domain as empty string ""                                | error "PUBLISHER_DOMAIN_REQUIRED" with suggestion              |
      | feature_ids_empty_array   | properties with feature_ids as empty array []                      | error "FEATURE_IDS_EMPTY" with suggestion                      |

  @T-UC-025-boundary-discovery @boundary @discovery-request
  Scenario Outline: Discovery request boundary validation -- <boundary_point>
    When the Buyer Agent sends get_property_features with <setup>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                   | setup                                                                    | outcome                                             |
      | 0 properties (empty array)                       | properties as empty array                                                | error "PROPERTIES_REQUIRED" with suggestion          |
      | 1 property (minimum)                             | properties with exactly 1 item                                           | success                                             |
      | 100 properties (maximum)                         | properties with exactly 100 items                                        | success                                             |
      | 101 properties (over limit)                      | properties with 101 items                                                | error "PROPERTIES_LIMIT_EXCEEDED" with suggestion    |
      | neither properties nor publisher_domain          | no selection mode fields                                                 | error "PROPERTY_SELECTION_REQUIRED" with suggestion  |
      | both properties and publisher_domain             | both fields provided                                                     | error "PROPERTY_SELECTION_CONFLICT" with suggestion  |
      | publisher_domain only                            | publisher_domain "example.com" only                                      | success                                             |
      | properties only                                  | properties ["example.com"] only                                          | success                                             |
      | each property_type enum value: website           | publisher_domain with property_types ["website"]                         | success                                             |
      | each property_type enum value: streaming_audio   | publisher_domain with property_types ["streaming_audio"]                 | success                                             |
      | unknown property_type value                      | publisher_domain with property_types ["hologram"]                        | error "PROPERTY_TYPE_INVALID" with suggestion        |
      | property_tag matching pattern: premium_ctv       | publisher_domain with property_tags ["premium_ctv"]                      | success                                             |
      | property_tag violating pattern: Premium!         | publisher_domain with property_tags ["Premium!"]                         | error "PROPERTY_TAG_INVALID_FORMAT" with suggestion  |
      | feature_ids present with 1 item                  | properties with feature_ids ["carbon_score"]                             | success                                             |
      | feature_ids omitted (returns all)                | properties without feature_ids                                           | success                                             |
      | feature_ids empty array                          | properties with feature_ids as empty array                               | error "FEATURE_IDS_EMPTY" with suggestion            |
      | empty publisher_domain string                    | publisher_domain as ""                                                   | error "PUBLISHER_DOMAIN_REQUIRED" with suggestion    |

  @T-UC-025-partition-feature-value @partition @feature-value
  Scenario Outline: Feature value partition validation -- <partition>
    Given property "example.com" is configured as described
    When the Buyer Agent sends get_property_features with properties ["example.com"]
    Then <outcome>

    Examples: Valid partitions
      | partition                     | outcome                                                              |
      | binary_feature_covered        | feature value is boolean true with confidence 1.0                    |
      | quantitative_feature_covered  | feature value is number 42.5 with unit and confidence 0.85           |
      | categorical_feature_covered   | feature value is string "tier_1" with confidence 0.9                 |
      | coverage_not_covered          | coverage_status is "not_covered" and features map empty or absent    |
      | coverage_pending              | coverage_status is "pending" and features map empty or absent        |
      | confidence_zero               | feature value present with confidence 0.0                            |
      | confidence_max                | feature value present with confidence 1.0                            |
      | minimal_value_only            | feature value is boolean true with no optional metadata              |

    Examples: Invalid partitions
      | partition                      | outcome                                                         |
      | value_type_mismatch_binary     | error "FEATURE_VALUE_TYPE_MISMATCH" with suggestion             |
      | value_type_mismatch_quantitative | error "FEATURE_VALUE_TYPE_MISMATCH" with suggestion           |
      | confidence_below_zero          | error "CONFIDENCE_OUT_OF_RANGE" with suggestion                 |
      | confidence_above_one           | error "CONFIDENCE_OUT_OF_RANGE" with suggestion                 |
      | coverage_status_unknown        | error "COVERAGE_STATUS_INVALID" with suggestion                 |

  @T-UC-025-boundary-feature-value @boundary @feature-value
  Scenario Outline: Feature value boundary validation -- <boundary_point>
    Given property "example.com" is configured with boundary condition
    When the Buyer Agent sends get_property_features with properties ["example.com"]
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                       | outcome                                             |
      | confidence = 0.0 (minimum)                           | valid confidence accepted                           |
      | confidence = 0.5 (midpoint)                          | valid confidence accepted                           |
      | confidence = 1.0 (maximum)                           | valid confidence accepted                           |
      | confidence = -0.1 (below minimum)                    | error "CONFIDENCE_OUT_OF_RANGE" with suggestion      |
      | confidence = 1.1 (above maximum)                     | error "CONFIDENCE_OUT_OF_RANGE" with suggestion      |
      | coverage_status = covered                            | valid status with features populated                |
      | coverage_status = not_covered                        | valid status with features empty or absent          |
      | coverage_status = pending                            | valid status with features empty or absent          |
      | coverage_status = unknown value                      | error "COVERAGE_STATUS_INVALID" with suggestion      |
      | boolean value for binary feature                     | valid type match                                    |
      | number value for quantitative feature                | valid type match                                    |
      | string value for categorical feature                 | valid type match                                    |
      | string value for binary feature (type mismatch)      | error "FEATURE_VALUE_TYPE_MISMATCH" with suggestion  |
      | boolean value for quantitative feature (type mismatch) | error "FEATURE_VALUE_TYPE_MISMATCH" with suggestion |
      | value absent (missing required field)                | error "FEATURE_VALUE_TYPE_MISMATCH" with suggestion  |
      | features map with 1 feature entry                    | valid response                                      |
      | features map empty when covered                      | valid response                                      |
      | confidence omitted (optional)                        | valid response without confidence                   |

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

    Examples: Invalid partitions
      | partition             | setup                                                  | outcome                                             |
      | missing_list_id       | no list_id field                                       | error "LIST_ID_REQUIRED" with suggestion             |
      | missing_records       | list_id "pl-123" with no records field                 | error "RECORDS_REQUIRED" with suggestion             |
      | empty_records         | list_id "pl-123" with records as empty array           | error "RECORDS_REQUIRED" with suggestion             |
      | records_over_limit    | list_id "pl-123" with 10001 records                    | error "RECORDS_LIMIT_EXCEEDED" with suggestion       |
      | negative_impressions  | list_id "pl-123" with record having -1 impressions     | error "IMPRESSIONS_INVALID" with suggestion          |
      | list_not_found        | list_id "pl-nonexistent" with valid records            | error "LIST_NOT_FOUND" with suggestion               |
      | list_access_denied    | list_id "pl-other-buyers" with valid records           | error "LIST_ACCESS_DENIED" with suggestion           |

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
      | list_id references nonexistent list       | list_id "pl-nonexistent"                             | error "LIST_NOT_FOUND" with suggestion                |
      | list_id references inaccessible list      | list_id "pl-other-tenant"                            | error "LIST_ACCESS_DENIED" with suggestion            |
      | records absent                            | no records field                                     | error "RECORDS_REQUIRED" with suggestion              |
      | sales_agent_url present on some records   | mixed records with and without sales_agent_url       | success with partial authorization summary           |
      | sales_agent_url absent from all records   | no sales_agent_url on any record                     | success without authorization summary                |

  @T-UC-025-partition-delivery-response @partition @delivery-response
  Scenario Outline: Delivery response partition validation -- <partition>
    Given property list "pl-valid" exists with appropriate test data
    When the Buyer Agent validates delivery triggering <condition>
    Then <outcome>

    Examples: Valid partitions
      | partition                  | condition                                            | outcome                                                        |
      | all_compliant_default      | all records compliant with default filter             | results array empty, summary shows all compliant               |
      | all_compliant_included     | all records compliant with include_compliant true     | results array contains all records with status compliant       |
      | mixed_compliance           | mix of compliant and non-compliant records            | summary reflects correct category counts                      |
      | with_violations            | non-compliant record with not_in_list violation       | result includes violations array with code and message         |
      | with_authorization         | records with sales_agent_url                          | authorization_summary present with per-record auth results     |
      | with_aggregate             | agent provides optional aggregate scoring             | aggregate object present with score, grade, label              |
      | feature_failed_violation   | record fails feature requirement                      | violation has code feature_failed with feature_id              |
      | unidentified_records       | record with unresolvable identifier type              | result has status unidentified                                 |

    Examples: Invalid partitions
      | partition                           | condition                                     | outcome                                                       |
      | missing_summary                     | response missing summary object               | error "SUMMARY_REQUIRED" with suggestion                      |
      | inconsistent_counters               | summary total != sum of categories            | error "SUMMARY_INCONSISTENT" with suggestion                  |
      | unknown_validation_status           | result with unknown status value              | error "VALIDATION_STATUS_INVALID" with suggestion             |
      | unknown_authorization_status        | authorization with unknown status             | error "AUTHORIZATION_STATUS_INVALID" with suggestion          |
      | authorization_summary_without_auth_records | auth summary present without auth records | error "AUTHORIZATION_SUMMARY_UNEXPECTED" with suggestion     |

  @T-UC-025-boundary-delivery-response @boundary @delivery-response
  Scenario Outline: Delivery response boundary validation -- <boundary_point>
    Given property list "pl-valid" exists with appropriate test data
    When the Buyer Agent validates delivery with <condition>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                         | condition                                          | outcome                                                     |
      | validation status = compliant                          | compliant record                                   | valid status accepted                                       |
      | validation status = non_compliant                      | non-compliant record                               | valid status with violations                                |
      | validation status = not_covered                        | not-covered record                                 | valid status accepted                                       |
      | validation status = unidentified                       | unidentified record                                | valid status accepted                                       |
      | validation status = unknown value                      | unknown status value                               | error "VALIDATION_STATUS_INVALID" with suggestion            |
      | authorization status = authorized                      | authorized agent                                   | valid authorization status                                  |
      | authorization status = unauthorized                    | unauthorized agent                                 | valid authorization status                                  |
      | authorization status = unknown                         | adagents.json unavailable                          | valid authorization status                                  |
      | authorization status = unknown value                   | unknown auth status value                          | error "AUTHORIZATION_STATUS_INVALID" with suggestion         |
      | authorization_summary present (records had sales_agent_url) | records with sales_agent_url                 | authorization_summary present                               |
      | authorization_summary absent (no auth records)         | no records with sales_agent_url                    | authorization_summary absent                                |
      | results empty (all compliant, default filter)          | all compliant with default filter                  | results array empty                                         |
      | results populated (include_compliant=true)             | all compliant with include_compliant true          | results array populated                                     |
      | summary counters total = sum of categories             | mixed status records                               | counters internally consistent                              |
      | summary counters total != sum of categories            | inconsistent summary                               | error "SUMMARY_INCONSISTENT" with suggestion                 |
      | violations present on non_compliant record             | non-compliant record                               | violations array present                                    |
      | violations absent on compliant record                  | compliant record                                   | no violations                                               |
      | violation code = feature_failed with feature_id and requirement | feature requirement failure              | violation includes feature context                          |
      | aggregate present with score/grade/label               | agent provides aggregate                           | aggregate object in response                                |
      | aggregate absent                                       | agent does not provide aggregate                   | no aggregate in response                                    |

  @T-UC-025-inv189-filters-ignored @invariant @BR-RULE-189
  Scenario: BR-RULE-189 INV-6 holds -- type/tag filters ignored in explicit properties mode
    Given property "example.com" is of type "website"
    When the Buyer Agent sends get_property_features with properties ["example.com"] and property_types ["mobile_app"]
    Then the response still contains results for "example.com"
    And the property_types filter has no effect in explicit properties mode
    # BR-RULE-189 INV-6: filters ignored in properties mode

  @T-UC-025-inv189-empty-properties @invariant @BR-RULE-189 @error
  Scenario: BR-RULE-189 INV-3 violated -- properties array empty
    When the Buyer Agent sends get_property_features with properties []
    Then the operation should fail
    And the error code should be "PROPERTIES_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "at least one property"
    # BR-RULE-189 INV-3: minItems=1 enforced

  @T-UC-025-inv190-type-mismatch @invariant @BR-RULE-190 @error
  Scenario Outline: BR-RULE-190 INV-1/2/3 violated -- value type mismatch <case>
    Given property "example.com" has feature "<feature_id>" defined as <definition_type>
    But the stored value is <wrong_value> (type <wrong_type>)
    When the Buyer Agent sends get_property_features with properties ["example.com"]
    Then the feature value is flagged as type mismatch
    And the error should include "suggestion" field
    And the suggestion should contain "binary->boolean, quantitative->number, categorical->string"
    # BR-RULE-190 INV-1/2/3: type safety enforcement

    Examples:
      | case                        | feature_id   | definition_type | wrong_value | wrong_type |
      | string for binary feature   | coppa_cert   | binary          | "yes"       | string     |
      | boolean for quantitative    | carbon_score | quantitative    | true        | boolean    |

  @T-UC-025-inv190-confidence @invariant @BR-RULE-190 @error
  Scenario Outline: BR-RULE-190 INV-4 violated -- confidence <case>
    Given property "example.com" has a feature with confidence <confidence_value>
    When the Buyer Agent sends get_property_features with properties ["example.com"]
    Then the confidence value is rejected
    And the error should include "suggestion" field
    And the suggestion should contain "0.0, 1.0"
    # BR-RULE-190 INV-4: confidence bounded [0, 1]

    Examples:
      | case           | confidence_value |
      | below zero     | -0.1             |
      | above one      | 1.5              |

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

  @T-UC-025-inv192-exclusive-status @invariant @BR-RULE-192
  Scenario: BR-RULE-192 INV-1 holds -- each record has exactly one validation status
    Given property list "pl-valid" with 4 records resolving to different statuses
    When the Buyer Agent validates delivery with those records
    Then each result has exactly one status from: compliant, non_compliant, not_covered, unidentified
    And no result has more than one status
    # BR-RULE-192 INV-1: mutually exclusive status

  @T-UC-025-inv192-violations-required @invariant @BR-RULE-192
  Scenario: BR-RULE-192 INV-2 holds -- non_compliant records have violations
    Given property list "pl-valid" with a record that resolves to non_compliant
    When the Buyer Agent validates delivery with that record
    Then the result has status "non_compliant"
    And the violations array is present with at least one entry
    # BR-RULE-192 INV-2: violations required for non_compliant

  @T-UC-025-inv192-violations-absent-compliant @invariant @BR-RULE-192
  Scenario: BR-RULE-192 INV-2 counter-example -- compliant records have no violations
    Given property list "pl-valid" with a record that resolves to compliant
    When the Buyer Agent validates delivery with include_compliant true
    Then the result has status "compliant"
    And no violations field is present on the result
    # BR-RULE-192 INV-2 counter: compliant = no violations

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

  @T-UC-025-timestamps @main-flow @ext-a @post-s6
  Scenario: Delivery validation response includes timestamps
    Given property list "pl-valid" exists and was resolved at a known timestamp
    When the Buyer Agent validates delivery against "pl-valid"
    Then the response includes validated_at as an ISO 8601 timestamp
    And the response may include list_resolved_at showing when the list was last resolved
    # POST-S6: Buyer knows when validation was performed

