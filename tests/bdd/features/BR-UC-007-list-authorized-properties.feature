# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-007 Discover Publisher Properties
  As a Buyer (Human or AI Agent)
  I want to discover which publisher properties a seller is authorized to represent
  So that I can evaluate the seller's inventory scope before committing to a relationship

  # Postconditions verified:
  #   POST-S1: Buyer knows which publisher domains the seller agent is authorized to represent
  #   POST-S2: Buyer knows the primary advertising channels available in the portfolio (if provided)
  #   POST-S3: Buyer knows the primary geographic markets covered by the portfolio (if provided)
  #   POST-S4: Buyer can read a natural-language description of the portfolio (if provided)
  #   POST-S5: Buyer understands the seller's advertising content policies and restrictions (if provided)
  #   POST-S6: Buyer knows when the publisher authorization list was last updated (if provided)
  #   POST-S7: Application context from the request is echoed unchanged in the response
  #   POST-F1: System state is unchanged on failure (read-only operation)
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Application context is still echoed when possible

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context


  @T-UC-007-main-rest @main-flow @rest @post-s1 @post-s2 @post-s3 @post-s4 @post-s6
  Scenario: Discover full publisher portfolio via REST
    Given the tenant has registered publisher partnerships with domains "cnn.com", "nytimes.com", "washingtonpost.com"
    And the tenant has primary_channels configured as "display, video"
    And the tenant has primary_countries configured as "US, UK"
    And the tenant has a portfolio_description configured
    And the tenant has a last_updated timestamp
    When the Buyer Agent sends a list_authorized_properties task via A2A with no filters
    Then the response should include publisher_domains "cnn.com", "nytimes.com", "washingtonpost.com"
    And the response should include primary_channels
    And the response should include primary_countries
    And the response should include portfolio_description
    And the response should include last_updated
    # POST-S1: Buyer knows authorized publisher domains
    # POST-S2: Buyer knows primary advertising channels
    # POST-S3: Buyer knows primary geographic markets
    # POST-S4: Buyer reads portfolio description
    # POST-S6: Buyer knows when list was last updated

  @T-UC-007-main-mcp @main-flow @mcp @post-s1 @post-s2 @post-s3 @post-s4 @post-s6
  Scenario: Discover full publisher portfolio via MCP
    Given the tenant has registered publisher partnerships with domains "cnn.com", "nytimes.com", "washingtonpost.com"
    And the tenant has primary_channels configured as "display, video"
    And the tenant has primary_countries configured as "US, UK"
    And the tenant has a portfolio_description configured
    And the tenant has a last_updated timestamp
    When the Buyer Agent calls list_authorized_properties MCP tool with no filters
    Then the response should include publisher_domains "cnn.com", "nytimes.com", "washingtonpost.com"
    And the response should include primary_channels
    And the response should include primary_countries
    And the response should include portfolio_description
    And the response should include last_updated
    # POST-S1: Buyer knows authorized publisher domains
    # POST-S2: Buyer knows primary advertising channels
    # POST-S3: Buyer knows primary geographic markets
    # POST-S4: Buyer reads portfolio description
    # POST-S6: Buyer knows when list was last updated

  @T-UC-007-main-policy @main-flow @post-s5
  Scenario: Portfolio includes advertising content policies when enabled
    Given the tenant has advertising_policy enabled with prohibited categories and tactics
    And the tenant has registered publisher partnerships
    When the Buyer Agent requests list_authorized_properties
    Then the response should include advertising_policies text
    And the advertising_policies should describe prohibited content categories
    And the advertising_policies should describe prohibited tactics
    And the advertising_policies should include a policy enforcement footer
    # POST-S5: Buyer understands content policies and restrictions

  @T-UC-007-main-context @main-flow @post-s7
  Scenario: Request context is echoed in successful response
    Given the tenant has registered publisher partnerships
    And the request includes a context object with session_id "buyer-session-42"
    When the Buyer Agent requests list_authorized_properties
    Then the response should include the context object unchanged
    And the context session_id should be "buyer-session-42"
    # POST-S7: Application context echoed unchanged

  @T-UC-007-audit-log @main-flow @audit
  Scenario: Audit event logged after successful property discovery
    Given the tenant has registered publisher partnerships with domains "a.com", "b.com"
    When the Buyer Agent requests list_authorized_properties
    Then an audit event should be logged
    And the audit event should include publisher_count of 2
    And the audit event should include the publisher_domains list
    # UC flow step 8: Audit event logged with operation details

  @T-UC-007-ext-a-rest @extension @ext-a @error @rest @post-f1 @post-f2
  Scenario: TENANT_ERROR via REST - tenant resolution fails
    Given no tenant can be resolved from the request context
    When the Buyer Agent sends a list_authorized_properties task
    Then the operation should fail
    And the error code should be "TENANT_ERROR"
    And the error message should contain "tenant"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged (read-only)
    # POST-F2: Error code TENANT_ERROR explains what failed

  @T-UC-007-ext-a-mcp @extension @ext-a @error @mcp @post-f1 @post-f2
  Scenario: TENANT_ERROR via MCP - tenant resolution fails
    Given no tenant can be resolved from the request context
    When the Buyer Agent calls list_authorized_properties MCP tool
    Then the operation should fail
    And the error code should be "TENANT_ERROR"
    And the error message should contain "tenant"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged (read-only)
    # POST-F2: Error code TENANT_ERROR explains what failed

  @T-UC-007-ext-b-rest @extension @ext-b @error @rest @post-f1 @post-f2
  Scenario: PROPERTIES_ERROR via REST - database query fails
    Given a tenant is resolvable from the request context
    But the publisher partnership database query fails
    When the Buyer Agent sends a list_authorized_properties task
    Then the operation should fail
    And the error code should be "PROPERTIES_ERROR"
    And the error message should contain "error"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged (read-only)
    # POST-F2: Error code PROPERTIES_ERROR explains what failed

  @T-UC-007-ext-b-mcp @extension @ext-b @error @mcp @post-f1 @post-f2
  Scenario: PROPERTIES_ERROR via MCP - database query fails
    Given a tenant is resolvable from the request context
    But the publisher partnership database query fails
    When the Buyer Agent calls list_authorized_properties MCP tool
    Then the operation should fail
    And the error code should be "PROPERTIES_ERROR"
    And the error message should contain "error"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged (read-only)
    # POST-F2: Error code PROPERTIES_ERROR explains what failed

  @T-UC-007-audit-log-error @extension @ext-b @audit
  Scenario: Audit event logged on PROPERTIES_ERROR failure
    Given a tenant is resolvable from the request context
    But the publisher partnership database query fails
    When the Buyer Agent requests list_authorized_properties
    Then the operation should fail
    And the error should include "suggestion" field
    And an audit event should be logged recording the failure
    # UC ext-b step 4c: Audit event recorded on failure

  @T-UC-007-ext-c-rest @extension @ext-c @error @rest @post-f1 @post-f2
  Scenario: DOMAIN_INVALID_FORMAT via REST - invalid domain pattern in filter
    Given a tenant is resolvable from the request context
    When the Buyer Agent sends a list_authorized_properties task via A2A with publisher_domains filter containing "INVALID DOMAIN!"
    Then the operation should fail
    And the error code should be "DOMAIN_INVALID_FORMAT"
    And the error message should contain "domain"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: Error code DOMAIN_INVALID_FORMAT explains what failed

  @T-UC-007-ext-c-mcp @extension @ext-c @error @mcp @post-f1 @post-f2
  Scenario: DOMAIN_INVALID_FORMAT via MCP - invalid domain pattern in filter
    Given a tenant is resolvable from the request context
    When the Buyer Agent calls list_authorized_properties MCP tool with publisher_domains filter containing "INVALID DOMAIN!"
    Then the operation should fail
    And the error code should be "DOMAIN_INVALID_FORMAT"
    And the error message should contain "domain"
    And the error should include "suggestion" field
    # POST-F1: System state unchanged
    # POST-F2: Error code DOMAIN_INVALID_FORMAT explains what failed

  @T-UC-007-error-context-echo @extension @error @context-echo @post-f3
  Scenario: Context is echoed in error response when possible
    Given no tenant can be resolved from the request context
    And the request includes context {"trace_id": "err-trace-001"}
    When the Buyer Agent requests list_authorized_properties
    Then the operation should fail
    And the error code should be "TENANT_ERROR"
    And the error should include "suggestion" field
    And the response should include context with trace_id "err-trace-001"
    # POST-F3: Application context echoed even on error

  @T-UC-007-r041-inv1-holds @invariant @BR-RULE-041 @authentication
  Scenario: R041 INV-1 holds - no auth token returns full portfolio
    Given the tenant has registered publisher partnerships with domains "example.com", "test.org"
    And the Buyer has no authentication credentials
    When the Buyer Agent requests list_authorized_properties
    Then the response should include publisher_domains "example.com", "test.org"
    And the principal should be logged as "anonymous"
    # BR-RULE-041 INV-1: No token -> full portfolio, logged as anonymous

  @T-UC-007-r041-inv2-holds @invariant @BR-RULE-041 @authentication
  Scenario: R041 INV-2 holds - valid auth token returns full portfolio
    Given the tenant has registered publisher partnerships with domains "example.com", "test.org"
    And the request has a valid authentication token for user "buyer-123"
    When the Buyer Agent requests list_authorized_properties
    Then the response should include publisher_domains "example.com", "test.org"
    And the principal should be logged as "buyer-123"
    # BR-RULE-041 INV-2: Valid token -> full portfolio, logged by identity

  @T-UC-007-r041-inv3-holds @invariant @BR-RULE-041 @authentication @mcp
  Scenario: R041 INV-3 holds - invalid auth token on MCP path treated as missing
    Given the tenant has registered publisher partnerships with domains "example.com", "test.org"
    And the request has an expired authentication token
    When the Buyer Agent calls list_authorized_properties MCP tool
    Then the response should include publisher_domains "example.com", "test.org"
    And no authentication error should be raised
    # BR-RULE-041 INV-3: Invalid/expired token on MCP path treated as missing, no error

  @T-UC-007-r041-inv5-holds @invariant @BR-RULE-041 @authentication @rest
  Scenario: R041 INV-5 holds - invalid auth token on A2A path rejected
    Given the tenant has registered publisher partnerships with domains "example.com", "test.org"
    And the request has an expired authentication token
    When the Buyer Agent sends a list_authorized_properties task
    Then the operation should fail
    And the error code should indicate an authentication failure
    And the error should include "suggestion" field
    # BR-RULE-041 INV-5: Invalid/expired token on A2A path → authentication error

  @T-UC-007-r041-inv4-holds @invariant @BR-RULE-041 @authentication @mcp
  Scenario: R041 INV-4 holds - portfolio data identical regardless of auth state (MCP)
    Given the tenant has registered publisher partnerships with domains "alpha.com", "beta.com"
    When the Buyer Agent calls list_authorized_properties MCP tool with no auth token
    And the Buyer Agent calls list_authorized_properties MCP tool with a valid auth token
    And the Buyer Agent calls list_authorized_properties MCP tool with an expired auth token
    Then all three responses should contain identical publisher_domains
    And all three responses should contain identical portfolio metadata
    # BR-RULE-041 INV-4: Response data unchanged across auth states (MCP path where all succeed)

  @T-UC-007-r042-inv1-holds @invariant @BR-RULE-042 @portfolio
  Scenario: R042 INV-1 holds - all partnerships included regardless of verification
    Given the tenant has publisher partnerships:
    | domain       | is_verified |
    | verified.com | true        |
    | pending.com  | false       |
    | new.com      | false       |
    When the Buyer Agent requests list_authorized_properties
    Then the response should include publisher_domains "new.com", "pending.com", "verified.com"
    # BR-RULE-042 INV-1: All partnerships included regardless of verification status
    # Traces to BR-RULE-042 INV-1 (include regardless of verification status — is_verified=false case)
    # Traces to BR-RULE-042 INV-1 (include regardless of verification status — is_verified=true case)

  @T-UC-007-r042-inv2-holds @invariant @BR-RULE-042 @portfolio
  Scenario: R042 INV-2 holds - domains sorted alphabetically
    Given the tenant has publisher partnerships with domains "zebra.com", "alpha.com", "middle.com"
    When the Buyer Agent requests list_authorized_properties
    Then the publisher_domains should be ordered as "alpha.com", "middle.com", "zebra.com"
    # BR-RULE-042 INV-2: Domains sorted alphabetically ascending

  @T-UC-007-r042-inv3-holds @invariant @BR-RULE-042 @portfolio
  Scenario: R042 INV-3 holds - empty portfolio returns descriptive message
    Given the tenant has no registered publisher partnerships
    When the Buyer Agent requests list_authorized_properties
    Then the response should include an empty publisher_domains array
    And the response should include a portfolio_description explaining no partnerships are configured
    # BR-RULE-042 INV-3: Empty portfolio -> empty array + descriptive message

  @T-UC-007-r043-inv1-holds @invariant @BR-RULE-043 @context-echo
  Scenario: R043 INV-1 holds - context echoed in response
    Given the tenant has registered publisher partnerships
    And the request includes context {"session_id": "s-001", "trace_id": "t-abc"}
    When the Buyer Agent requests list_authorized_properties
    Then the response context should be {"session_id": "s-001", "trace_id": "t-abc"}
    # BR-RULE-043 INV-1: Request context echoed unchanged

  @T-UC-007-r043-inv2-holds @invariant @BR-RULE-043 @context-echo
  Scenario: R043 INV-2 holds - no context means no context in response
    Given the tenant has registered publisher partnerships
    And the request does not include a context object
    When the Buyer Agent requests list_authorized_properties
    Then the response should not include a context field
    # BR-RULE-043 INV-2: No request context -> no response context

  @invariant @BR-RULE-043 @context-echo
  Scenario: R043 INV-3 holds - context echoed even on empty portfolio
    Given the tenant has no registered publisher partnerships
    And the request includes context {"campaign": "eval-2024"}
    When the Buyer Agent requests list_authorized_properties
    Then the response should include an empty publisher_domains array
    And the response context should be {"campaign": "eval-2024"}
    # BR-RULE-043 INV-3: Context echoed in empty portfolio response

  @invariant @BR-RULE-043 @context-echo
  Scenario: R043 INV-4 holds - complex nested context preserved exactly
    Given the tenant has registered publisher partnerships
    And the request includes context {"meta": {"nested": {"deep": true}}, "tags": ["a", "b"]}
    When the Buyer Agent requests list_authorized_properties
    Then the response context should preserve the exact nested structure
    And the context "meta.nested.deep" should be true
    And the context "tags" should be ["a", "b"]
    # BR-RULE-043 INV-4: Complex/nested context preserved exactly

  @T-UC-007-r044-inv1-holds @invariant @BR-RULE-044 @advertising-policy
  Scenario: R044 INV-1 holds - policy disclosed when enabled with content
    Given the tenant has advertising_policy enabled
    And the tenant has prohibited_categories: "adult, gambling"
    And the tenant has prohibited_tactics: "clickbait, auto-redirect"
    And the tenant has registered publisher partnerships
    When the Buyer Agent requests list_authorized_properties
    Then the response should include advertising_policies text
    And the advertising_policies should mention "adult"
    And the advertising_policies should mention "gambling"
    And the advertising_policies should mention "clickbait"
    And the advertising_policies should mention "auto-redirect"
    And the advertising_policies should include a policy enforcement footer
    # BR-RULE-044 INV-1: Policy enabled + non-empty arrays -> disclosure included

  @T-UC-007-r044-inv2-holds @invariant @BR-RULE-044 @advertising-policy
  Scenario: R044 INV-2 holds - policy omitted when not enabled
    Given the tenant has advertising_policy disabled
    And the tenant has registered publisher partnerships
    When the Buyer Agent requests list_authorized_properties
    Then the response should not include an advertising_policies field
    # BR-RULE-044 INV-2: Policy disabled -> field omitted

  @T-UC-007-r044-inv3-holds @invariant @BR-RULE-044 @advertising-policy
  Scenario: R044 INV-3 holds - policy omitted when enabled but all arrays empty
    Given the tenant has advertising_policy enabled
    And the tenant has empty prohibited_categories, prohibited_tactics, and blocked_advertisers
    And the tenant has registered publisher partnerships
    When the Buyer Agent requests list_authorized_properties
    Then the response should not include an advertising_policies field
    # BR-RULE-044 INV-3: Policy enabled but all empty -> field omitted

  @T-UC-007-r044-inv4-holds @invariant @BR-RULE-044 @advertising-policy
  Scenario: R044 INV-4 holds - policy text includes enforcement footer
    Given the tenant has advertising_policy enabled with prohibited_categories: "tobacco"
    And the tenant has registered publisher partnerships
    When the Buyer Agent requests list_authorized_properties
    Then the response should include advertising_policies text
    And the advertising_policies text should end with a policy enforcement footer
    # BR-RULE-044 INV-4: When present, text includes enforcement footer

  @T-UC-007-r044-partial-categories @invariant @BR-RULE-044 @advertising-policy
  Scenario: R044 partial - only prohibited categories configured
    Given the tenant has advertising_policy enabled
    And the tenant has prohibited_categories: "adult, gambling"
    And the tenant has empty prohibited_tactics and blocked_advertisers
    And the tenant has registered publisher partnerships
    When the Buyer Agent requests list_authorized_properties
    Then the response should include advertising_policies text
    And the advertising_policies should mention "adult"
    And the advertising_policies should not contain a tactics section
    And the advertising_policies should include a policy enforcement footer
    # BR-RULE-044: Only categories section included when others are empty

  @T-UC-007-r044-blocked-advertisers @invariant @BR-RULE-044 @advertising-policy
  Scenario: R044 partial - blocked advertisers included in policy text
    Given the tenant has advertising_policy enabled
    And the tenant has blocked_advertisers: "competitor-corp, spam-inc"
    And the tenant has empty prohibited_categories and prohibited_tactics
    And the tenant has registered publisher partnerships
    When the Buyer Agent requests list_authorized_properties
    Then the response should include advertising_policies text
    And the advertising_policies should mention "competitor-corp"
    And the advertising_policies should mention "spam-inc"
    And the advertising_policies should include a policy enforcement footer
    # BR-RULE-044: Blocked advertisers section present when configured

  @T-UC-007-r045-inv1-holds @invariant @BR-RULE-045 @domain-filter
  Scenario: R045 INV-1 holds - no filter returns all publishers
    Given the tenant has publisher partnerships with domains "a.com", "b.com", "c.com"
    When the Buyer Agent requests list_authorized_properties with no publisher_domains filter
    Then the response should include publisher_domains "a.com", "b.com", "c.com"
    # BR-RULE-045 INV-1: Absent filter -> all publishers returned

  @invariant @BR-RULE-045 @domain-filter @error
  Scenario: R045 INV-3 violated - empty filter array rejected
    Given a tenant is resolvable from the request context
    When the Buyer Agent requests list_authorized_properties with publisher_domains filter as empty array
    Then the operation should fail
    And the error should indicate minItems violation
    And the error should include "suggestion" field
    # Schema validation (minItems): traces to constraint YAML publisher_domains_filter.yaml via BR-RULE-045

  @T-UC-007-r045-inv4-holds @invariant @BR-RULE-045 @domain-filter
  Scenario: R045 INV-4 holds - valid domain not matching any publisher returns empty
    Given the tenant has publisher partnerships with domains "cnn.com", "bbc.com"
    When the Buyer Agent requests list_authorized_properties with publisher_domains filter ["nonexistent.com"]
    Then the response should succeed
    And the response should include an empty publisher_domains array
    # BR-RULE-045 INV-4 (renumbered to INV-3): Valid format, no match -> success with empty result

  @T-UC-007-domain-format @validation @domain-filter @partition
  Scenario Outline: Publisher domain filter format validation - <partition>
    Given a tenant is resolvable from the request context
    When the Buyer Agent requests list_authorized_properties with publisher_domains filter ["<domain>"]
    Then the outcome should be <expected>
    # Valid partitions (aligned with publisher_domains_filter.yaml partition names)
    # Invalid partitions (covers BR-RULE-045 INV-2)

    Examples: Valid - single domain
      | partition       | domain           | expected |
      | single_domain   | example.com      | success  |

    Examples: Valid - single domain (subdomain variant)
      | partition       | domain           | expected |
      | single_domain   | news.example.com | success  |

    Examples: Valid - single domain (hyphenated variant)
      | partition       | domain           | expected |
      | single_domain   | my-site.com      | success  |

    Examples: Valid - single domain (single label variant)
      | partition       | domain           | expected |
      | single_domain   | localhost        | success  |

    Examples: Valid - minimal domain
      | partition        | domain           | expected |
      | minimal_domain   | a                | success  |

    Examples: Invalid - uppercase domain
      | partition         | domain           | expected                      |
      | uppercase_domain  | Example.COM      | error "DOMAIN_INVALID_FORMAT"  |

    Examples: Invalid - domain with spaces
      | partition           | domain           | expected                      |
      | domain_with_spaces  | my domain.com    | error "DOMAIN_INVALID_FORMAT"  |

    Examples: Invalid - domain starts hyphen
      | partition              | domain           | expected                      |
      | domain_starts_hyphen   | -example.com     | error "DOMAIN_INVALID_FORMAT"  |

    Examples: Invalid - domain starts hyphen (trailing variant)
      | partition              | domain           | expected                      |
      | domain_starts_hyphen   | example-.com     | error "DOMAIN_INVALID_FORMAT"  |

  @T-UC-007-partition-context @partition @context-echo
  Scenario Outline: Context echo partition - <partition>
    Given the tenant has registered publisher partnerships with domains "example.com"
    And the request includes context <context_value>
    When the Buyer Agent requests list_authorized_properties
    Then the response context should be <expected_context>

    Examples:
      | partition         | context_value                              | expected_context                           |
      | context_provided  | {"session_id": "s-1", "trace_id": "t-1"}  | {"session_id": "s-1", "trace_id": "t-1"}  |
      | context_absent    |                                            |                                            |
      | context_empty_object | {}                                      | {}                                         |
      | context_nested    | {"deep": {"nested": true}}                | {"deep": {"nested": true}}                |

  @T-UC-007-partition-portfolio @partition @portfolio
  Scenario Outline: Publisher portfolio partition - <partition>
    Given <precondition>
    When the Buyer Agent requests list_authorized_properties
    Then <expected>

    Examples:
      | partition            | precondition                                                        | expected                                           |
      | populated_portfolio  | the tenant has publisher partnerships with domains "abc.com", "xyz.com" | the response should include publisher_domains "abc.com", "xyz.com" |
      | single_publisher     | the tenant has a single publisher partnership with domain "only.com" and is_verified=true | the response should include publisher_domains "only.com" |
      | empty_portfolio      | the tenant has no registered publisher partnerships                  | the response should include an empty publisher_domains array |
      | mixed_verification   | the tenant has publisher partnerships with domains "verified.com", "pending.com" | the response should include publisher_domains "pending.com", "verified.com" |

  @T-UC-007-partition-policy @partition @advertising-policy
  Scenario Outline: Advertising policy partition - <partition>
    Given the tenant has registered publisher partnerships
    And <policy_state>
    When the Buyer Agent requests list_authorized_properties
    Then <expected>

    Examples:
      | partition                      | policy_state                                                    | expected                                                      |
      | policy_enabled_with_content    | the tenant has advertising_policy enabled with prohibited_categories "adult" | the response should include advertising_policies text         |
      | policy_disabled                | the tenant has advertising_policy disabled                      | the response should not include an advertising_policies field |
      | policy_all_sections            | the tenant has advertising_policy enabled with all sections populated | the response should include advertising_policies text         |
      | policy_enabled_empty_arrays    | the tenant has advertising_policy enabled with all arrays empty | the response should not include an advertising_policies field |

  @T-UC-007-partition-auth @partition @authentication
  Scenario Outline: Authentication partition - <partition>
    Given the tenant has registered publisher partnerships
    And <auth_state>
    When the Buyer Agent calls list_authorized_properties MCP tool
    Then the response should include the full property portfolio

    Examples:
      | partition                | auth_state                                          |
      | no_auth_header           | the Buyer has no authentication credentials             |
      | valid_auth_token         | the request has a valid Bearer authentication token  |
      | invalid_auth_token_mcp   | the request has an expired Bearer authentication token |

  @T-UC-007-partition-auth-a2a @partition @authentication @rest
  Scenario: Authentication partition - invalid_auth_token_a2a rejected
    Given the tenant has registered publisher partnerships
    And the request has an expired Bearer authentication token
    When the Buyer Agent sends a list_authorized_properties task
    Then the operation should fail
    And the error code should indicate an authentication failure
    # BR-RULE-041 INV-5: A2A path rejects invalid tokens

  @T-UC-007-partition-filter-extra @partition @domain-filter
  Scenario: Domain filter partition - filter_absent
    Given the tenant has publisher partnerships with domains "cnn.com", "bbc.com"
    When the Buyer Agent requests list_authorized_properties without publisher_domains filter
    Then the response should include all publisher_domains
    # Partition: filter_absent -> all publishers returned

  @T-UC-007-partition-filter-multi @partition @domain-filter
  Scenario: Domain filter partition - multi_domain
    Given the tenant has publisher partnerships with domains "cnn.com", "bbc.com", "nytimes.com"
    When the Buyer Agent requests list_authorized_properties with publisher_domains filter ["cnn.com", "bbc.com"]
    Then the response should include publisher_domains "bbc.com", "cnn.com"
    And the response should not include "nytimes.com"
    # Partition: multi_domain -> multiple domains in filter

  @T-UC-007-partition-filter-empty @partition @domain-filter
  Scenario: Domain filter partition - empty_array
    When the Buyer Agent requests list_authorized_properties with publisher_domains filter []
    Then the request should be rejected with schema validation error
    # Partition: empty_array -> minItems:1 violated

  @T-UC-007-filter-match @domain-filter @filtering
  Scenario: Filter returns only matching publishers
    Given the tenant has publisher partnerships with domains "cnn.com", "bbc.com", "nytimes.com"
    When the Buyer Agent requests list_authorized_properties with publisher_domains filter ["cnn.com", "bbc.com"]
    Then the response should include publisher_domains "bbc.com", "cnn.com"
    And the response should not include "nytimes.com"
    # Filtering returns subset of matching publishers, sorted alphabetically

  @T-UC-007-filter-partial @domain-filter @filtering
  Scenario: Filter with mix of matching and non-matching domains
    Given the tenant has publisher partnerships with domains "cnn.com", "bbc.com"
    When the Buyer Agent requests list_authorized_properties with publisher_domains filter ["cnn.com", "nonexistent.com"]
    Then the response should include publisher_domains "cnn.com"
    And the response should not include "nonexistent.com"
    # Partial match returns only matched domains; non-matching silently excluded

  @T-UC-007-boundary-context @boundary @context-echo
  Scenario Outline: Context object boundary - <boundary>
    Given the tenant has registered publisher partnerships with domains "example.com"
    And the request includes context <context_value>
    When the Buyer Agent requests list_authorized_properties
    Then the response context should be <expected_context>
    # Boundary points: context absent, context = {}, context with properties

    Examples:
      | boundary                | context_value                              | expected_context                           |
      | context absent          |                                            |                                            |
      | context = {}            | {}                                         | {}                                         |
      | context with properties | {"session_id": "s-1", "trace_id": "t-1"}  | {"session_id": "s-1", "trace_id": "t-1"}  |

  @T-UC-007-boundary-publishers @boundary @portfolio
  Scenario: Publisher domains boundary - 0 publishers
    Given the tenant has no registered publisher partnerships
    When the Buyer Agent requests list_authorized_properties
    Then the response should include an empty publisher_domains array
    And the response should include a portfolio_description
    # Boundary: 0 publishers -> empty array with descriptive message

  @T-UC-007-boundary-publishers-1 @boundary @portfolio
  Scenario: Publisher domains boundary - 1 publisher
    Given the tenant has a single publisher partnership with domain "only.com" and is_verified=true
    When the Buyer Agent requests list_authorized_properties
    Then the response should include publisher_domains "only.com"
    # Boundary: 1 publisher -> single-element array

  @T-UC-007-boundary-publishers-large @boundary @portfolio
  Scenario: Publisher domains boundary - N publishers (large set)
    Given the tenant has 50 registered publisher partnerships
    When the Buyer Agent requests list_authorized_properties
    Then the response should include 50 publisher_domains
    And the publisher_domains should be sorted alphabetically
    # Boundary: N publishers (large set) -> all returned, sorted

  @T-UC-007-boundary-publishers-mixed @boundary @portfolio
  Scenario: Publisher domains boundary - mix of verified/unverified
    Given the tenant has publisher partnerships:
    | domain        | is_verified |
    | verified.com  | true        |
    | pending.com   | false       |
    When the Buyer Agent requests list_authorized_properties
    Then the response should include publisher_domains "pending.com", "verified.com"
    # Boundary: mix of verified/unverified -> all included regardless

  @T-UC-007-boundary-policy-disabled @boundary @advertising-policy
  Scenario: Advertising policy boundary - policy disabled
    Given the tenant has advertising_policy disabled
    And the tenant has registered publisher partnerships
    When the Buyer Agent requests list_authorized_properties
    Then the response should not include an advertising_policies field
    # Boundary: policy disabled -> field omitted entirely

  @T-UC-007-boundary-policy @boundary @advertising-policy
  Scenario Outline: Advertising policy boundary - <boundary>
    Given the tenant has advertising_policy enabled
    And the tenant has prohibited_categories: <categories>
    And the tenant has prohibited_tactics: <tactics>
    And the tenant has blocked_advertisers: <advertisers>
    And the tenant has registered publisher partnerships
    When the Buyer Agent requests list_authorized_properties
    Then <expected>
    # Boundary points: policy enabled, all arrays empty / policy enabled, one category / policy enabled, all sections

    Examples:
      | boundary                         | categories        | tactics     | advertisers | expected                                                      |
      | policy enabled, all arrays empty | ""                | ""          | ""          | the response should not include an advertising_policies field |
      | policy enabled, one category     | "adult"           | ""          | ""          | the response should include advertising_policies text         |
      | policy enabled, all sections     | "adult, gambling" | "clickbait" | "spam-inc"  | the response should include advertising_policies text         |

  @T-UC-007-boundary-domain-filter @boundary @domain-filter
  Scenario: Publisher domain filter boundary - filter absent
    Given the tenant has publisher partnerships with domains "cnn.com", "bbc.com"
    When the Buyer Agent requests list_authorized_properties without publisher_domains filter
    Then the response should include all publisher_domains
    # Boundary: filter absent -> all publishers returned

  @T-UC-007-boundary-domain-filter-one @boundary @domain-filter
  Scenario: Publisher domain filter boundary - 1 valid domain
    Given the tenant has publisher partnerships with domains "cnn.com", "bbc.com"
    When the Buyer Agent requests list_authorized_properties with publisher_domains filter ["cnn.com"]
    Then the response should include publisher_domains "cnn.com"
    And the response should not include "bbc.com"
    # Boundary: 1 valid domain -> single match returned

  @T-UC-007-boundary-domain-filter-empty @boundary @domain-filter
  Scenario: Publisher domain filter boundary - 0 domains (empty array)
    When the Buyer Agent requests list_authorized_properties with publisher_domains filter []
    Then the request should be rejected with schema validation error
    # Boundary: 0 domains (empty array) -> minItems:1 violated

  @T-UC-007-boundary-domain-filter-minimal @boundary @domain-filter
  Scenario: Publisher domain filter boundary - domain 'a'
    Given the tenant has publisher partnerships with domains "a"
    When the Buyer Agent requests list_authorized_properties with publisher_domains filter ["a"]
    Then the response should include publisher_domains "a"
    # Boundary: domain 'a' -> minimal valid domain accepted

  @T-UC-007-boundary-domain-filter-uppercase @boundary @domain-filter
  Scenario: Publisher domain filter boundary - domain 'A'
    When the Buyer Agent requests list_authorized_properties with publisher_domains filter ["A"]
    Then the request should be rejected with error "DOMAIN_INVALID_FORMAT"
    # Boundary: domain 'A' -> uppercase rejected by pattern

  @T-UC-007-boundary-domain-filter-hyphen @boundary @domain-filter
  Scenario: Publisher domain filter boundary - domain '-abc.com'
    When the Buyer Agent requests list_authorized_properties with publisher_domains filter ["-abc.com"]
    Then the request should be rejected with error "DOMAIN_INVALID_FORMAT"
    # Boundary: domain '-abc.com' -> leading hyphen rejected by pattern

  @T-UC-007-boundary-auth @boundary @authentication
  Scenario: Authentication boundary - no auth header
    Given the tenant has registered publisher partnerships
    And the Buyer has no authentication credentials
    When the Buyer Agent requests list_authorized_properties
    Then the response should include the full property portfolio
    # Boundary: no auth header -> anonymous access succeeds (discovery is public)

  @T-UC-007-boundary-auth-valid @boundary @authentication
  Scenario: Authentication boundary - valid Bearer token
    Given the tenant has registered publisher partnerships
    And the request has a valid Bearer authentication token
    When the Buyer Agent requests list_authorized_properties
    Then the response should include the full property portfolio
    # Boundary: valid Bearer token -> authenticated access identical to anonymous

  @T-UC-007-boundary-auth-expired @boundary @authentication @mcp
  Scenario: Authentication boundary - expired Bearer token on MCP path
    Given the tenant has registered publisher partnerships
    And the request has an expired Bearer authentication token
    When the Buyer Agent calls list_authorized_properties MCP tool
    Then the response should include the full property portfolio
    # Boundary: expired Bearer token on MCP -> degrades to anonymous, no error (INV-3)

  @T-UC-007-boundary-auth-malformed @boundary @authentication @mcp
  Scenario: Authentication boundary - malformed auth header (not Bearer format) via MCP
    Given the tenant has registered publisher partnerships
    And the request has a malformed authentication header "Basic dXNlcjpwYXNz"
    When the Buyer Agent calls list_authorized_properties MCP tool
    Then the response should include the full property portfolio
    # Boundary: malformed auth header on MCP -> degrades to anonymous, no error (INV-3)

  @T-UC-007-boundary-auth-a2a-invalid @boundary @authentication @rest
  Scenario: Authentication boundary - expired Bearer token on A2A path rejected
    Given the tenant has registered publisher partnerships
    And the request has an expired Bearer authentication token
    When the Buyer Agent sends a list_authorized_properties task
    Then the operation should fail
    And the error code should indicate an authentication failure
    And the error should include "suggestion" field
    # Boundary: expired Bearer token on A2A -> authentication error (INV-5)

  @T-UC-007-enum-creatives-fields @validation @list-creatives-fields @partition @boundary
  Scenario Outline: List creatives fields boundary validation - <boundary_point>
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent lists creatives with fields "<fields_value>"
    Then the response should indicate <outcome>

    Examples: Partitions
      | boundary_point  | transport | fields_value                                                                                                         | outcome          |
      | minimal_fields  | MCP     | ["creative_id","name","status"]                                                                                       | success          |
      | all_enum_values | MCP     | ["creative_id","name","format","status","created_date","updated_date","tags","assignments","performance","sub_assets"] | success          |
      | single_field    | MCP     | ["creative_id"]                                                                                                       | success          |
      | omitted         | MCP     |                                                                                                                       | success          |
      | unknown_field   | MCP     | ["creative_id","thumbnail"]                                                                                           | validation error |
      | empty_array     | MCP     | []                                                                                                                    | validation error |

    Examples: Boundaries
      | boundary_point                                                                                                                          | transport | fields_value                                                                                                         | outcome          |
      | ["creative_id"] (single field, minimum valid)                                                                                           | MCP     | ["creative_id"]                                                                                                       | success          |
      | ["creative_id", "name", "format", "status", "created_date", "updated_date", "tags", "assignments", "performance", "sub_assets"] (all 10 fields) | MCP     | ["creative_id","name","format","status","created_date","updated_date","tags","assignments","performance","sub_assets"] | success          |
      | Not provided (all fields returned)                                                                                                       | MCP     |                                                                                                                       | success          |
      | ["creative_id", "thumbnail"] (unknown field in array)                                                                                    | MCP     | ["creative_id","thumbnail"]                                                                                           | validation error |
      | [] (empty array, violates minItems)                                                                                                      | MCP     | []                                                                                                                    | validation error |

  @T-UC-007-enum-sort-field @validation @creative-sort-field @partition @boundary
  Scenario Outline: Creative sort field boundary validation - <boundary_point>
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent lists creatives sorted by "<sort_field>"
    Then the response should indicate <outcome>

    Examples: Partitions
      | boundary_point   | transport | sort_field        | outcome          |
      | created_date     | MCP     | created_date      | success          |
      | updated_date     | MCP     | updated_date      | success          |
      | name             | MCP     | name              | success          |
      | status           | MCP     | status            | success          |
      | assignment_count | MCP     | assignment_count  | success          |
      | performance_score| MCP     | performance_score | success          |
      | omitted          | MCP     |                   | success          |
      | unknown_value    | MCP     | format            | validation error |

    Examples: Boundaries
      | boundary_point                                  | transport | sort_field        | outcome          |
      | created_date (first enum value, also default)   | MCP     | created_date      | success          |
      | performance_score (last enum value)             | MCP     | performance_score | success          |
      | Not provided (defaults to created_date)         | MCP     |                   | success          |
      | format (not in enum)                            | MCP     | format            | validation error |

  @T-UC-007-enum-preview-format @validation @preview-output-format @partition @boundary
  Scenario Outline: Preview output format boundary validation - <boundary_point>
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent requests a creative preview with output format "<format>"
    Then the response should indicate <outcome>

    Examples: Partitions
      | boundary_point | transport | format | outcome          |
      | url            | MCP     | url    | success          |
      | html           | MCP     | html   | success          |
      | omitted        | MCP     |        | success          |
      | unknown_value  | MCP     | json   | validation error |

    Examples: Boundaries
      | boundary_point                          | transport | format | outcome          |
      | url (first enum value, also default)    | MCP     | url    | success          |
      | html (last enum value)                  | MCP     | html   | success          |
      | Not provided (defaults to url)          | MCP     |        | success          |
      | json (not in enum)                      | MCP     | json   | validation error |

  @T-UC-007-boundary-property-type @boundary @property-type @partition
  Scenario Outline: Property type boundary validation - <boundary_point>
    Given the tenant has registered publisher partnerships
    And the publisher partnership includes a property with type "<property_type>"
    When the Buyer Agent requests list_authorized_properties
    Then the property type "<property_type>" should be <outcome>

    Examples: Boundaries
      | boundary_point                       | property_type   | outcome          |
      | website (first enum value)           | website         | accepted         |
      | streaming_audio (last enum value)    | streaming_audio | accepted         |
      | Unknown string not in enum           | hologram        | validation error |

