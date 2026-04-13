# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-012 Manage Content Standards
  As a Buyer
  I want to define, discover, and maintain brand safety and suitability policies
  So that I can control what publisher content is acceptable adjacent to my advertising

  # Postconditions verified:
  #   POST-S1: Buyer has created a new content standard and knows its standards_id
  #   POST-S2: Buyer can retrieve the full content standard configuration by ID
  #   POST-S3: Buyer can discover all content standards matching scope filters
  #   POST-S4: Buyer has updated an existing content standard (new version created)
  #   POST-S5: Buyer has deleted an obsolete content standard not in use
  #   POST-S6: Application context echoed unchanged in response
  #   POST-S7: Scope conflict detected and reported with conflicting_standards_id
  #   POST-F1: System state unchanged on failure
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Application context echoed when possible
  #   POST-F4: Conflicting standards_id returned on scope conflict

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And the Buyer is authenticated with a valid principal_id


  @T-UC-012-list-main @list @happy-path @post-s3 @post-s6
  Scenario Outline: List content standards via <transport> - returns matching standards
    Given the tenant has 3 content standards with different scopes
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent sends a list_content_standards request
    Then the response contains a standards array with 3 items
    And each standard includes standards_id, scope, and policy
    And the request context is echoed in the response
    # POST-S3: Buyer discovers all content standards matching scope filters
    # POST-S6: Application context echoed unchanged

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @list @no-filter @post-s3 @partition @boundary
  Scenario: List content standards - no filters (empty request) returns all tenant standards
    Given the tenant has 5 content standards
    When the Buyer Agent sends a list_content_standards request with no filters
    Then the response contains a standards array with 5 items
    # BR-RULE-068 INV-5: No filter parameters → all standards returned

  @list @filter @channels @post-s3 @partition
  Scenario: List with channels filter uses OR logic within dimension
    Given the tenant has a standard scoped to "display" channel
    And the tenant has a standard scoped to "social" channel
    And the tenant has a standard scoped to "podcast" channel
    When the Buyer Agent filters by channels ["display", "social"]
    Then the response contains 2 standards (display and social)
    And the podcast standard is not included
    # BR-RULE-068 INV-1: Multiple channels in filter → match ANY

  @list @filter @languages @post-s3 @partition
  Scenario: List with languages filter uses OR logic within dimension
    Given the tenant has a standard scoped to language "en"
    And the tenant has a standard scoped to language "de"
    And the tenant has a standard scoped to language "fr"
    When the Buyer Agent filters by languages ["en", "de"]
    Then the response contains 2 standards (en and de)
    # BR-RULE-068 INV-2: Multiple languages → match ANY

  @list @filter @countries @post-s3 @partition
  Scenario: List with countries filter uses OR logic within dimension
    Given the tenant has a standard scoped to countries ["US", "GB"]
    And the tenant has a standard scoped to countries ["DE"]
    When the Buyer Agent filters by countries ["US"]
    Then the response includes the US+GB standard
    And the DE-only standard is not included
    # BR-RULE-068 INV-3: Multiple countries → match ANY

  @list @filter @cross-dimension @post-s3 @partition
  Scenario: List with multiple filter dimensions uses AND logic between dimensions
    Given the tenant has a standard scoped to channel "display" and language "en"
    And the tenant has a standard scoped to channel "social" and language "de"
    When the Buyer Agent filters by channels ["display"] and languages ["de"]
    Then the response contains 0 standards
    # BR-RULE-068 INV-4: Multiple dimensions → must match ALL
    # Neither standard matches both dimensions

  @T-UC-012-list-empty-result @list @empty-result @partition
  Scenario: List with non-matching filters returns empty array
    Given the tenant has a standard scoped to channel "display"
    When the Buyer Agent filters by channels ["podcast"]
    Then the response contains an empty standards array
    And the response is not an error

  @list @filter @partition @boundary
  Scenario Outline: List content standards - filter combination <combination>
    Given the tenant has content standards with various scopes
    When the Buyer Agent sends a list_content_standards request with <filter_params>
    Then the response contains matching standards based on <combination> logic

    Examples:
      | combination      | filter_params                                           | boundary_point                             |
      | channels_only    | channels=["display","olv"]                              | --                                         |
      | languages_only   | languages=["en","de"]                                   | --                                         |
      | countries_only   | countries=["US"]                                        | countries with 1 value                     |
      | two_filters      | channels=["social"],languages=["en"]                    | channels + languages (no countries)        |
      | all_filters      | channels=["display"],languages=["en"],countries=["US"]  | all three filters present                  |
      | single_channel   | channels=["podcast"]                                    | channels with 1 value                      |
      | single_language  | languages=["en"]                                        | languages with 1 value                     |
      | multi_channel    | channels=["display","olv","social","search","ctv","linear_tv","radio","streaming_audio","podcast","dooh","ooh","print","cinema","email","gaming","retail_media","influencer","affiliate"] | channels with all 18 enum values |

  @T-UC-012-ext-a-create @create @happy-path @post-s1 @post-s6
  Scenario Outline: Create content standard via <transport> - returns standards_id
    Given no existing content standard for this scope
    When the Buyer Agent creates a content standard via <transport> with:
    | scope                    | {"languages_any": ["en"], "channels_any": ["display"]} |
    | policy                   | No ads adjacent to violence or hate speech              |
    Then the response contains a generated standards_id
    And the request context is echoed in the response
    # POST-S1: Buyer has created standard and knows standards_id
    # POST-S6: Application context echoed

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @create @scope @partition @boundary
  Scenario Outline: Create content standard - scope <scope_type> is valid
    Given no existing content standard for this scope
    When the Buyer Agent creates a content standard with scope <scope_value> and policy "Safe content only"
    Then the response contains a generated standards_id
    # BR-RULE-064 INV-1..4: Scope dimension semantics

    Examples:
      | scope_type     | scope_value                                                                          | boundary_point                                    |
      | minimal_scope  | {"languages_any": ["en"]}                                                            | languages_any with 1 item (minimum valid)         |
      | full_scope     | {"countries_all": ["US","GB"], "channels_any": ["display","olv"], "languages_any": ["en","de"]} | languages_any with many items                     |
      | no_countries   | {"channels_any": ["social"], "languages_any": ["en"]}                                | countries_all absent (no country filter), channels_any with valid enum value |
      | no_channels    | {"countries_all": ["DE"], "languages_any": ["de"]}                                   | countries_all with 1 ISO code                     |
      | all_channels   | {"channels_any": ["display","olv","social","search","ctv","linear_tv","radio","streaming_audio","podcast","dooh","ooh","print","cinema","email","gaming","retail_media","influencer","affiliate"], "languages_any": ["en"]} | channels_any with all 18 enum values |

  @create @scope @error @partition @boundary @post-f2 @post-f3
  Scenario Outline: Create content standard - scope <scope_type> is rejected
    When the Buyer Agent creates a content standard with scope <scope_value> and policy "Safe content only"
    Then the error code should be "<error_code>"
    And the error should include "suggestion"
    # BR-RULE-064 INV-1,5: languages_any required with minItems:1
    # POST-F2: Buyer knows what failed
    # POST-F3: Suggestion present

    Examples:
      | scope_type         | scope_value                      | error_code         | boundary_point                              |
      | empty_languages    | {"languages_any": []}            | LANGUAGES_REQUIRED | languages_any with 0 items (empty array)    |
      | missing_languages  | {"countries_all": ["US"]}        | LANGUAGES_REQUIRED | scope without languages_any key             |
      | invalid_channel    | {"channels_any": ["not_a_channel"], "languages_any": ["en"]} | CHANNEL_INVALID | channels_any with unknown enum value |

  @create @policy @partition @boundary @post-f2 @post-f3
  Scenario Outline: Create content standard - policy <policy_type>
    Given no existing content standard for this scope
    When the Buyer Agent creates a content standard with scope {"languages_any": ["en"]} and policy <policy_value>
    Then <expected_outcome>

    Examples:
      | policy_type      | policy_value                                                     | expected_outcome                             | boundary_point                            |
      | typical_policy   | No ads adjacent to violence, hate speech, or explicit material   | the response contains a generated standards_id | policy present with content on create     |
      | minimal_policy   | No adult content                                                 | the response contains a generated standards_id | --                                        |
      | missing_on_create| (absent)                                                         | the error code should be "POLICY_REQUIRED"   | policy absent on create                   |
      | empty_string     |                                                                  | the error code should be "POLICY_REQUIRED"   | policy empty string on create             |

  @create @calibration @partition @boundary
  Scenario Outline: Create content standard - calibration exemplars <exemplar_type>
    Given no existing content standard for this scope
    When the Buyer Agent creates a content standard with calibration_exemplars <exemplar_value>
    Then <expected_outcome>
    # BR-RULE-069 INV-1..4: Polymorphic exemplar format

    Examples:
      | exemplar_type       | exemplar_value                                                                     | expected_outcome                               | boundary_point                                    |
      | url_refs_only       | {"pass": [{"type": "url", "value": "https://example.com/safe"}]}                   | the response contains a generated standards_id | pass array with 1 URL ref (minimal)               |
      | artifacts_only      | {"pass": [{"property_id": "pub1", "artifact_id": "art1", "assets": [{"type": "text"}]}]} | the response contains a generated standards_id | pass array with 1 artifact (minimal)              |
      | mixed_formats       | {"pass": [{"type": "url", "value": "https://safe.com"}, {"property_id": "p1", "artifact_id": "a1", "assets": []}]} | the response contains a generated standards_id | pass array with mixed URL ref + artifact |
      | pass_only           | {"pass": [{"type": "url", "value": "https://example.com/safe"}]}                   | the response contains a generated standards_id | --                                                |
      | fail_only           | {"fail": [{"type": "url", "value": "https://example.com/unsafe"}]}                 | the response contains a generated standards_id | fail array only (no pass)                         |
      | absent              | (not provided)                                                                      | the response contains a generated standards_id | calibration_exemplars absent                      |
      | empty_object        | {}                                                                                  | the response contains a generated standards_id | calibration_exemplars empty object {}             |

  @create @calibration @error @partition @boundary @post-f2 @post-f3
  Scenario Outline: Create content standard - invalid calibration exemplar <exemplar_type>
    When the Buyer Agent creates a content standard with calibration_exemplars <exemplar_value>
    Then the error code should be "EXEMPLAR_INVALID_FORMAT"
    And the error should include "suggestion"
    # BR-RULE-069 INV-1,2: Format validation
    # POST-F2: Buyer knows what failed
    # POST-F3: Suggestion present

    Examples:
      | exemplar_type           | exemplar_value                                         | boundary_point                    |
      | url_missing_value       | {"pass": [{"type": "url"}]}                            | URL ref missing value field       |
      | url_invalid_uri         | {"pass": [{"type": "url", "value": "not-a-url"}]}     | URL ref with non-URI value        |
      | artifact_missing_assets | {"pass": [{"property_id": "p1", "artifact_id": "a1"}]} | artifact missing required assets  |

  @T-UC-012-ext-b-get @get @happy-path @post-s2 @post-s6
  Scenario: Get content standard by ID - returns full configuration
    Given an existing content standard with standards_id "std_abc123"
    When the Buyer Agent sends a get_content_standards request for "std_abc123"
    Then the response contains the full content standard including standards_id, scope, policy, and calibration_exemplars
    And the request context is echoed in the response
    # POST-S2: Buyer retrieves full content standard by ID

  @get @standards-id @partition @boundary @post-f2 @post-f3
  Scenario Outline: Get content standard - standards_id <id_type>
    When the Buyer Agent sends a get_content_standards request for standards_id <id_value>
    Then <expected_outcome>

    Examples:
      | id_type          | id_value              | expected_outcome                                      | boundary_point                              |
      | existing_standard| std_abc123            | the response contains the full content standard       | valid standards_id for existing standard    |
      | system_generated | std_sys_001           | the response contains the full content standard       | --                                          |
      | not_found        | nonexistent_id        | the error code should be "STANDARDS_NOT_FOUND"        | standards_id not found in tenant            |
      | wrong_tenant     | other_tenant_standard | the error code should be "STANDARDS_NOT_FOUND"        | standards_id exists in different tenant     |

  @T-UC-012-get-pricing-options @get @pricing-options @partition @boundary
  Scenario: Get content standard - response includes pricing_options when seller provides them
    Given an existing content standard with standards_id "std_priced" that has pricing_options
    When the Buyer Agent sends a get_content_standards request for "std_priced"
    Then the response contains a pricing_options array with at least 1 item
    And each pricing option includes pricing_option_id and pricing_model
    # pricing_options is seller-supplied on the content standard model

  @T-UC-012-ext-c-update @update @happy-path @post-s4 @post-s6
  Scenario Outline: Update content standard via <transport> - success branch (success: true, standards_id)
    Given an existing content standard with standards_id "std_abc123"
    When the Buyer Agent updates the content standard via <transport> with new policy "Updated brand safety policy"
    Then the response success field is true
    And the response contains standards_id "std_abc123"
    And a new version of the standard is created
    And the request context is echoed in the response
    # BR-RULE-066 INV-1: Update creates new version
    # Response uses oneOf success branch: {"success": true, "standards_id": "..."}
    # POST-S4: Buyer updated content standard (new version)
    # POST-S6: Context echoed

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @update @partial @post-s4 @partition @boundary
  Scenario: Update content standard - policy present on update (replaces existing), partial update preserves unchanged fields
    Given an existing content standard with scope {"languages_any": ["en"], "channels_any": ["display"]} and policy "Original policy"
    When the Buyer Agent updates only the policy to "Updated policy"
    Then the response success field is true
    And the scope remains {"languages_any": ["en"], "channels_any": ["display"]}
    And the standards_id is unchanged
    # BR-RULE-066 INV-2: Only provided fields are changed
    # BR-RULE-066 INV-3: standards_id stable across versions

  @T-UC-012-update-scope-only @update @partial @post-s4 @partition @boundary
  Scenario: Update content standard - update_omitted: policy omitted on update, scope change preserves policy
    Given an existing content standard with policy "Keep this policy"
    When the Buyer Agent updates the scope to {"languages_any": ["en", "de"]}
    Then the response success field is true
    And the policy remains "Keep this policy"
    # BR-RULE-066 INV-2: Unchanged fields carried forward

  @T-UC-012-update-no-changes @update @edge-case @partition
  Scenario: Update content standard - no fields changed still returns success branch
    Given an existing content standard with standards_id "std_abc123"
    When the Buyer Agent sends an update request with only standards_id and no other fields
    Then the response success field is true
    And the response contains standards_id "std_abc123"

  @T-UC-012-update-scope-conflict @update @scope-conflict @error @post-s7 @post-f1 @post-f2 @post-f3 @post-f4
  Scenario: Update content standard - scope change triggers SCOPE_CONFLICT via error branch (success: false)
    Given an existing content standard "std_001" with scope {"languages_any": ["en"]}
    And another existing content standard "std_002" with scope {"languages_any": ["de"]}
    When the Buyer Agent updates "std_001" scope to {"languages_any": ["de"]}
    Then the response success field is false
    And the response contains errors array with at least 1 item
    And the errors array includes error code "SCOPE_CONFLICT"
    And the response includes conflicting_standards_id "std_002"
    And the error should include "suggestion"
    # BR-RULE-065 INV-2: Update scope overlap → SCOPE_CONFLICT via oneOf error branch
    # POST-S7: Scope conflict detected
    # POST-F1: System state unchanged
    # POST-F4: conflicting_standards_id returned

  @T-UC-012-update-scope-valid @update @scope @error @partition @boundary @post-f2 @post-f3
  Scenario: Update content standard - scope languages_any validation on update
    Given an existing content standard with standards_id "std_abc123"
    When the Buyer Agent updates the scope to {"languages_any": []}
    Then the response success field is false
    And the response contains errors array with at least 1 item
    And the errors array includes error code "LANGUAGES_REQUIRED"
    And the error should include "suggestion"
    # BR-RULE-064 INV-5: Update languages_any must satisfy minItems:1

  @T-UC-012-update-not-found @update @error @post-f2 @post-f3
  Scenario: Update content standard - standards_id not found
    When the Buyer Agent sends an update for non-existent standards_id "nonexistent_id"
    Then the error code should be "STANDARDS_NOT_FOUND"
    And the error should include "suggestion"
    # POST-F2: Buyer knows what failed
    # POST-F3: Suggestion present

  @T-UC-012-update-exemplars @update @calibration @partition
  Scenario: Update content standard - add calibration exemplars to existing standard
    Given an existing content standard without calibration exemplars
    When the Buyer Agent updates with calibration_exemplars {"pass": [{"type": "url", "value": "https://example.com/safe"}]}
    Then the response success field is true
    And a new version of the standard is created
    # BR-RULE-069: Exemplar polymorphism applies to update too

  @T-UC-012-update-success-branch @update @oneOf @post-s4
  Scenario: Update response success branch requires success:true and standards_id
    Given an existing content standard with standards_id "std_xyz"
    When the Buyer Agent updates the policy to "New policy text"
    Then the response success field is true
    And the response contains standards_id "std_xyz"
    And the response conforms to the success branch of update-content-standards-response oneOf
    # Response schema: oneOf success branch requires ["success", "standards_id"]
    # BR-RULE-066 INV-3: success returns same standards_id

  @T-UC-012-update-error-branch @update @oneOf @error @post-f1 @post-f2
  Scenario: Update response error branch requires success:false and errors array (minItems:1)
    Given an existing content standard "std_err" with scope {"languages_any": ["en"]}
    And another existing content standard "std_conflict" with scope {"languages_any": ["fr"]}
    When the Buyer Agent updates "std_err" scope to {"languages_any": ["fr"]}
    Then the response success field is false
    And the response contains errors array with at least 1 item
    And the error should include "suggestion"
    And the response conforms to the error branch of update-content-standards-response oneOf
    # Response schema: oneOf error branch requires ["success", "errors"] with errors minItems:1
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows what failed

  @T-UC-012-ext-d-delete @delete @happy-path @post-s5 @post-s6
  Scenario: Delete content standard - unreferenced standard removed
    Given an existing content standard "std_obsolete" with no active media buy references
    When the Buyer Agent deletes "std_obsolete"
    Then the deletion succeeds
    And all versions and calibration exemplars are removed
    And the request context is echoed in the response
    # BR-RULE-067 INV-2: Unreferenced → deleted with versions and exemplars
    # POST-S5: Buyer deleted obsolete standard
    # POST-S6: Context echoed

  @T-UC-012-delete-in-use @delete @in-use @error @ext-g @post-f1 @post-f2 @post-f3
  Scenario: Delete content standard - blocked when referenced by active media buy
    Given an existing content standard "std_active" referenced by 2 active media buys
    When the Buyer Agent attempts to delete "std_active"
    Then the error code should be "STANDARDS_IN_USE"
    And the error should include "suggestion"
    And the content standard "std_active" still exists
    # BR-RULE-067 INV-1: Active media buy references → STANDARDS_IN_USE
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows what failed
    # POST-F3: Suggestion present

  @T-UC-012-delete-inactive-refs @delete @inactive-refs @partition
  Scenario: Delete content standard - allowed when only inactive buys reference it
    Given an existing content standard "std_old" referenced only by completed media buys
    When the Buyer Agent deletes "std_old"
    Then the deletion succeeds
    # BR-RULE-067 INV-3: Only ACTIVE buys block deletion

  @T-UC-012-delete-not-found @delete @error @post-f2 @post-f3
  Scenario: Delete content standard - standards_id not found
    When the Buyer Agent attempts to delete non-existent standards_id "nonexistent_id"
    Then the error code should be "STANDARDS_NOT_FOUND"
    And the error should include "suggestion"

  @T-UC-012-delete-unchanged @delete @error @post-f1
  Scenario: Failed delete does not modify system state
    Given an existing content standard "std_active" referenced by active media buys
    When the Buyer Agent attempts to delete "std_active"
    Then the error code should be "STANDARDS_IN_USE"
    And the error should include "suggestion"
    And the content standard "std_active" still exists with unchanged data
    # POST-F1: System state unchanged on failure

  @T-UC-012-not-found-operations @not-found @error @ext-e @post-f1 @post-f2 @post-f3
  Scenario Outline: STANDARDS_NOT_FOUND on <operation> with non-existent ID
    When the Buyer Agent sends a <operation> request for standards_id "nonexistent_id"
    Then the error code should be "STANDARDS_NOT_FOUND"
    And the error message references "nonexistent_id"
    And the error should include "suggestion"
    And the system state is unchanged
    # POST-F1: Unchanged
    # POST-F2: Error code
    # POST-F3: Suggestion

    Examples:
      | operation              |
      | get_content_standards  |
      | update_content_standards |
      | delete_content_standards |

  @T-UC-012-not-found-wrong-tenant @not-found @tenant-isolation @partition @post-f2
  Scenario: STANDARDS_NOT_FOUND when standards_id belongs to different tenant
    Given a content standard "std_other" exists in tenant "other_tenant"
    When the Buyer Agent in tenant "my_tenant" requests get_content_standards for "std_other"
    Then the error code should be "STANDARDS_NOT_FOUND"
    # Tenant isolation: cannot see other tenant's standards

  @scope-conflict @create @error @ext-f @partition @post-s7 @post-f1 @post-f2 @post-f3 @post-f4
  Scenario: Create content standard - scope_overlap triggers SCOPE_CONFLICT with error branch (success: false)
    Given an existing content standard "std_existing" with scope {"languages_any": ["en"], "channels_any": ["display"]}
    When the Buyer Agent creates a content standard with the same scope
    Then the response success field is false
    And the response contains errors array with at least 1 item
    And the errors array includes error code "SCOPE_CONFLICT"
    And the response includes conflicting_standards_id "std_existing"
    And the error should include "suggestion"
    And no new content standard is created
    # BR-RULE-065 INV-1: Create overlap → SCOPE_CONFLICT via oneOf error branch
    # POST-S7: Scope conflict detected and reported
    # POST-F1: System state unchanged
    # POST-F4: conflicting_standards_id returned

  @T-UC-012-scope-no-conflict @scope-conflict @create @partition
  Scenario: Create content standard - non-overlapping scope proceeds normally (success: true)
    Given an existing content standard with scope {"languages_any": ["en"]}
    When the Buyer Agent creates a content standard with scope {"languages_any": ["de"]}
    Then the response contains a generated standards_id
    # BR-RULE-065 INV-3: No overlap → proceeds via success branch

  @T-UC-012-scope-conflict-update @scope-conflict @update @error @ext-f @post-s7 @post-f1 @post-f2 @post-f3 @post-f4
  Scenario: Update content standard - scope change triggers SCOPE_CONFLICT with error branch (success: false)
    Given an existing content standard "std_a" with scope {"languages_any": ["en"]}
    And another existing content standard "std_b" with scope {"languages_any": ["de"]}
    When the Buyer Agent updates "std_a" scope to {"languages_any": ["de"]}
    Then the response success field is false
    And the response contains errors array with at least 1 item
    And the errors array includes error code "SCOPE_CONFLICT"
    And the response includes conflicting_standards_id "std_b"
    And the error should include "suggestion"
    # BR-RULE-065 INV-2: Update scope overlap → error branch
    # POST-S7: Scope conflict detected
    # POST-F1: System state unchanged
    # POST-F4: conflicting_standards_id returned

  @T-UC-012-scope-no-conflict-update @scope-conflict @update @partition
  Scenario: Update content standard - non-overlapping scope change proceeds (success: true)
    Given an existing content standard "std_c" with scope {"languages_any": ["en"]}
    And another existing content standard "std_d" with scope {"languages_any": ["de"]}
    When the Buyer Agent updates "std_c" scope to {"languages_any": ["fr"]}
    Then the response success field is true
    And the response contains standards_id "std_c"
    # BR-RULE-065 INV-3: No overlap → success branch

  @T-UC-012-auth-required @auth @error @post-f2
  Scenario Outline: Authentication required for <operation>
    Given the Buyer has no authentication credentials
    When the Buyer Agent sends a <operation> request
    Then the request is rejected with an authentication error
    And the error should include "suggestion"
    # BR-RULE-063 INV-2,3: No token or invalid token → rejected
    # BR-RULE-063 INV-4: All five operations enforce identical auth
    # POST-F2: Buyer knows what failed

    Examples:
      | operation                |
      | list_content_standards   |
      | create_content_standards |
      | get_content_standards    |
      | update_content_standards |
      | delete_content_standards |

  @T-UC-012-auth-invalid @auth @error @post-f2
  Scenario: Authentication - expired token rejected
    Given the Buyer Agent has an expired authentication token
    When the Buyer Agent sends a list_content_standards request
    Then the request is rejected with an authentication error
    And the error should include "suggestion"
    # BR-RULE-063 INV-3: Invalid/expired token → rejected

  @T-UC-012-context-echo-success @context-echo @post-s6
  Scenario Outline: Context echoed in <operation> success response
    Given a valid request with context {"trace_id": "abc-123", "session": "s1"}
    When the Buyer Agent sends a successful <operation> request
    Then the response includes context {"trace_id": "abc-123", "session": "s1"}
    # BR-RULE-043 INV-1: Request includes context → response includes identical context
    # POST-S6: Application context echoed unchanged

    Examples:
      | operation                |
      | list_content_standards   |
      | create_content_standards |
      | get_content_standards    |
      | update_content_standards |
      | delete_content_standards |

  @T-UC-012-context-echo-error @context-echo @error @post-f3
  Scenario Outline: Context echoed in <error_type> error response
    Given a request with context {"trace_id": "err-456"}
    When the request triggers <error_type> error
    Then the error response includes context {"trace_id": "err-456"}
    And the error should include "suggestion"
    # BR-RULE-043 INV-1 + POST-F3: Context echoed on error paths
    # POST-F3: Application context echoed when possible

    Examples:
      | error_type         |
      | STANDARDS_NOT_FOUND |
      | SCOPE_CONFLICT      |
      | STANDARDS_IN_USE    |

  @T-UC-012-context-omitted @context-echo @partition
  Scenario: Context omitted in request - response omits context
    When the Buyer Agent sends a list_content_standards request without context
    Then the response does not include a context field
    # BR-RULE-043 INV-2: No context → no context in response

  @T-UC-012-scope-countries-and @scope @countries-and @boundary
  Scenario: Scope countries_all uses AND logic - standard applies in ALL listed countries
    Given a content standard with countries_all ["US", "GB"]
    Then the standard applies when the evaluation context is US
    And the standard applies when the evaluation context is GB
    And the standard applies in ALL listed countries simultaneously
    # BR-RULE-064 INV-2: countries_all → AND

  @T-UC-012-list-channel-invalid @list @filter @error @boundary @post-f2 @post-f3
  Scenario: List content standards - channels with unknown enum value in filter
    When the Buyer Agent filters by channels ["fake_channel"]
    Then the error code should be "CHANNEL_INVALID"
    And the error should include "suggestion"

  @pricing-options @partition @boundary
  Scenario Outline: Content standard pricing_options - <partition>
    Given an existing content standard with pricing_options <pricing_value>
    When the Buyer Agent retrieves the content standard
    Then <expected_outcome>
    # pricing_options is seller-supplied on the content standard model (read-only for buyer)

    Examples:
      | partition         | pricing_value                                                                                           | expected_outcome                                                      | boundary_point                                       |
      | absent            | (field omitted)                                                                                         | the response does not include pricing_options                         | pricing_options absent (no billing for this standard)|
      | single_option     | [{"pricing_option_id": "cs_cpm_usd", "pricing_model": "cpm", "currency": "USD", "fixed_price": 0.50}]  | the response contains pricing_options with 1 item                     | pricing_options with 1 item (minimum valid)          |
      | multiple_options  | [{"pricing_option_id": "cs_cpm_usd", "pricing_model": "cpm"}, {"pricing_option_id": "cs_flat_usd", "pricing_model": "flat_rate"}] | the response contains pricing_options with 2 items    | pricing_options with multiple models                 |
      | multi_currency    | [{"pricing_option_id": "cs_cpm_usd", "pricing_model": "cpm", "currency": "USD"}, {"pricing_option_id": "cs_cpm_eur", "pricing_model": "cpm", "currency": "EUR"}] | the response contains pricing_options with 2 items | --                                                   |

  @pricing-options @error @partition @boundary @post-f2
  Scenario: Content standard pricing_options - empty_array violates minItems:1 — pricing_options with 0 items (empty array)
    Given a content standard where pricing_options is set to an empty array []
    Then the pricing_options is invalid per schema (minItems: 1 violated)
    And the error code should be "PRICING_OPTIONS_EMPTY"
    And the error should include "suggestion"
    # pricing_options when present must have minItems: 1
    # POST-F2: Buyer knows what failed

  @T-UC-012-pricing-options-list @pricing-options @list @partition
  Scenario: List content standards - response includes pricing_options on standards that have them
    Given the tenant has a standard with pricing_options and a standard without pricing_options
    When the Buyer Agent sends a list_content_standards request
    Then the priced standard includes pricing_options in the response
    And the unpriced standard does not include pricing_options in the response

