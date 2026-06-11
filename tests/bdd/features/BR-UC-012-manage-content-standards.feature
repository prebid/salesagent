# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

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
    And each standard includes standards_id, scope, and policies
    And the request context is echoed in the response
    # POST-S3: Buyer discovers all content standards matching scope filters
    # POST-S6: Application context echoed unchanged
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/content-standards/list-content-standards-request.json

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-012-list-empty-result @list @empty-result @partition
  Scenario: List with non-matching filters returns empty array
    Given the tenant has a standard scoped to channel "display"
    When the Buyer Agent filters by channels ["podcast"]
    Then the response contains an empty standards array
    And the response is not an error

  @T-UC-012-ext-a-create @create @happy-path @post-s1 @post-s6
  Scenario Outline: Create content standard via <transport> - returns standards_id
    Given no existing content standard for this scope
    When the Buyer Agent creates a content standard via <transport> with:
    | idempotency_key | cs-create-key-0001-abcd                                                                          |
    | scope           | {"languages_any": ["en"], "channels_any": ["display"]}                                           |
    | policies        | [{"policy_id": "no_violence", "enforcement": "must", "policy": "No ads adjacent to violence or hate speech"}] |
    Then the response contains a generated standards_id
    And the request context is echoed in the response
    # POST-S1: Buyer has created standard and knows standards_id
    # POST-S6: Application context echoed
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/content-standards/list-content-standards-request.json

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-012-governance-channels-combine @create @governance @partition
  Scenario: Create content standard - bespoke policies[] and registry_policy_ids[] combine (both evaluated)
    Given no existing content standard for this scope
    When the Buyer Agent creates a content standard with policies [{"policy_id": "no_violence", "enforcement": "must", "policy": "No violence"}] and registry_policy_ids ["iab:hate-speech"]
    Then the response contains a generated standards_id
    And both the bespoke policy "no_violence" and the registry policy "iab:hate-speech" are recorded as governance for the standard
    # BR-RULE-256 INV-5: both channels present -> both evaluated, neither overrides
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/content-standards/list-content-standards-request.json

  @T-UC-012-legacy-policy-rejected @create @governance @error @partition @post-f2 @post-f3
  Scenario: Create content standard - legacy singular policy string is not recognized as governance in v3.1
    Given no existing content standard for this scope
    When the Buyer Agent creates a content standard with scope {"languages_any": ["en"]} and only a legacy singular policy string "No adult content"
    Then the legacy policy string does not establish governance
    And the request is rejected with error code "VALIDATION_ERROR"
    And the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion"
    # BR-RULE-256 INV-7: v3.0 singular `policy` string no longer recognized; governance must use policies[]/registry_policy_ids[]
    # With no policies[]/registry_policy_ids[], the v3.1 create-request anyOf (INV-1) is unsatisfied → rejection
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/content-standards/list-content-standards-request.json

  @T-UC-012-ext-b-get @get @happy-path @post-s2 @post-s6
  Scenario: Get content standard by ID - returns full configuration
    Given an existing content standard with standards_id "std_abc123"
    When the Buyer Agent sends a get_content_standards request for "std_abc123"
    Then the response contains the full content standard including standards_id, name, scope, policies, and calibration_exemplars
    And the request context is echoed in the response
    # POST-S2: Buyer retrieves full content standard by ID
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/content-standards/list-content-standards-request.json

  @T-UC-012-get-pricing-options @get @pricing-options @partition @boundary
  Scenario: Get content standard - response includes pricing_options when seller provides them
    Given an existing content standard with standards_id "std_priced" that has pricing_options
    When the Buyer Agent sends a get_content_standards request for "std_priced"
    Then the response contains a pricing_options array with at least 1 item
    And each pricing option includes pricing_option_id and model
    # pricing_options is seller-supplied on the content standard model

  @T-UC-012-ext-c-update @update @happy-path @post-s4 @post-s6
  Scenario Outline: Update content standard via <transport> - success branch (success: true, standards_id)
    Given an existing content standard with standards_id "std_abc123"
    When the Buyer Agent updates the content standard via <transport> with a valid idempotency_key and new policies [{"policy_id": "updated_safety", "enforcement": "must", "policy": "Updated brand safety policy"}]
    Then the response success field is true
    And the response contains standards_id "std_abc123"
    And a new version of the standard is created
    And the request context is echoed in the response
    # BR-RULE-066 INV-1: Update creates new version
    # Response uses oneOf success branch: {"success": true, "standards_id": "..."}
    # POST-S4: Buyer updated content standard (new version)
    # POST-S6: Context echoed
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/content-standards/list-content-standards-request.json

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-012-update-scope-only @update @partial @post-s4 @partition @boundary
  Scenario: Update content standard - update_omitted: policies omitted on update, scope change preserves policies
    Given an existing content standard with policies [{"policy_id": "keep", "enforcement": "must", "policy": "Keep this policy"}]
    When the Buyer Agent updates the scope to {"languages_any": ["en", "de"]}
    Then the response success field is true
    And the policies remain [{"policy_id": "keep", "enforcement": "must", "policy": "Keep this policy"}]
    # BR-RULE-066 INV-2: Unchanged fields carried forward
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/content-standards/list-content-standards-request.json

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
    When the Buyer Agent updates the policies to [{"policy_id": "new", "enforcement": "must", "policy": "New policy text"}]
    Then the response success field is true
    And the response contains standards_id "std_xyz"
    And the response conforms to the success branch of update-content-standards-response oneOf
    # Response schema: oneOf success branch requires ["success", "standards_id"]
    # BR-RULE-066 INV-3: success returns same standards_id
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/content-standards/list-content-standards-request.json

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

  @T-UC-012-update-idempotency-required @update @idempotency @error @post-f2 @post-f3
  Scenario: Update content standard - idempotency_key omitted is rejected (REQUIRED in v3.1)
    Given an existing content standard with standards_id "std_abc123"
    When the Buyer Agent sends an update for "std_abc123" without an idempotency_key
    Then the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion"
    # BR-RULE-081 INV-6: idempotency_key REQUIRED on content-standards update
    # POST-F2: Buyer knows what failed
    # POST-F3: Suggestion present

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
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/content-standards/list-content-standards-request.json

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
    And the error code should be "AUTH_REQUIRED"
    And the error should include "suggestion"
    # BR-RULE-063 INV-2,3: No token or invalid token → rejected
    # BR-RULE-063 INV-4: All five operations enforce identical auth
    # POST-F2: Buyer knows what failed
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/content-standards/list-content-standards-request.json

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
    And the error code should be "AUTH_REQUIRED"
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
    Then the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion"

  @T-UC-012-pricing-options-list @pricing-options @list @partition
  Scenario: List content standards - response includes pricing_options on standards that have them
    Given the tenant has a standard with pricing_options and a standard without pricing_options
    When the Buyer Agent sends a list_content_standards request
    Then the priced standard includes pricing_options in the response
    And the unpriced standard does not include pricing_options in the response

  @T-UC-012-policy-violation-details-shape @policy-violation @error-details @schema @post-f2
  Scenario: POLICY_VIOLATION error carries policy-violation details shape (policy_id, violated_rules)
    Given the Buyer Agent submits a create_content_standards request whose policy text violates the seller's governance policy
    When the Seller Agent rejects the request with error code "POLICY_VIOLATION"
    Then the error.details object conforms to /schemas/error-details/policy-violation.json
    And the error code should be "POLICY_VIOLATION"
    And error.details.policy_id is present and is a string
    And error.details.violated_rules is a non-empty array of strings
    And error.details.policy_url, when present, is an absolute URI
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/content-standards/list-content-standards-request.json

  @T-UC-012-conflict-details-shape @conflict @error-details @schema @post-f4
  Scenario: CONFLICT error on update carries conflict details (resource_id, expected_version, current_version)
    Given the Buyer Agent submits an update_content_standards request with a stale version token
    When the Seller Agent rejects the request with error code "CONFLICT"
    Then the error.details object conforms to /schemas/error-details/conflict.json
    And the error code should be "CONFLICT"
    And error.details.resource_id equals the requested standards_id
    And error.details.expected_version reflects the version the client sent
    And error.details.current_version reflects the server's current version
    # Buyer can re-read the resource at current_version and retry
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/content-standards/list-content-standards-request.json

  @T-UC-012-escalation-severity-enum @escalation @enum @governance @partition
  Scenario Outline: Governance escalation severity must be one of info|warning|critical
    Given a governance escalation is attached to a content-standards SCOPE_CONFLICT event
    When the escalation is emitted with severity "<severity>"
    Then the severity value is <validity> per /schemas/enums/escalation-severity.json

    Examples:
      | severity | validity |
      | info     | valid    |
      | warning  | valid    |
      | critical | valid    |
      | urgent   | invalid  |
      | INFO     | invalid  |
