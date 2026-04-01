# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-006 Sync Creative Assets
  As a Buyer (AI Agent or Human User)
  I want to sync creative assets to the Seller's creative library
  So that creatives are validated, approved, and ready for media buy execution

  # Postconditions verified:
  #   POST-S1: Buyer knows which creatives were successfully created, updated, or unchanged
  #   POST-S2: Buyer knows the per-creative action taken (created, updated, unchanged, failed, deleted)
  #   POST-S3: Buyer knows which packages each creative was assigned to
  #   POST-S4: Buyer knows about any per-creative warnings or assignment errors
  #   POST-S5: Creatives requiring approval are routed to configured workflow
  #   POST-F1: Buyer knows the operation failed
  #   POST-F2: Buyer knows what went wrong
  #   POST-F3: Buyer knows how to recover

  Background:
    Given a Seller Agent is operational and accepting requests
    And a valid tenant context exists


  @T-UC-006-main-rest @main-flow @rest
  Scenario: Sync creatives via REST — successful create
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "Summer Banner" and a known format_id
    And the creative does not exist in the Seller's library
    When the Buyer Agent syncs the creative
    Then the response should include the creative with action "created"
    And the creative should have a status reflecting the approval workflow
    # POST-S1: Buyer knows creative was successfully created
    # POST-S2: Buyer knows action = created

  @T-UC-006-main-rest-update @main-flow @rest
  Scenario: Sync creatives via REST — successful update
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "Summer Banner" and a known format_id
    And the creative already exists in the Seller's library for this principal
    When the Buyer Agent syncs the creative
    Then the response should include the creative with action "updated"
    # POST-S1: Buyer knows creative was updated
    # POST-S2: Buyer knows action = updated

  @T-UC-006-main-rest-unchanged @main-flow @rest
  Scenario: Sync creatives via REST — creative unchanged
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "Summer Banner" and a known format_id
    And the creative already exists with identical data
    When the Buyer Agent syncs the creative
    Then the response should include the creative with action "unchanged"
    # POST-S2: Buyer knows action = unchanged

  @T-UC-006-main-rest-assign @main-flow @rest
  Scenario: Sync creatives via REST — with package assignments
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And assignments mapping the creative to valid package_ids
    When the Buyer Agent syncs the creative
    Then the response should include the creative with assignment results
    And the assignment results should list the assigned packages
    # POST-S3: Buyer knows which packages each creative was assigned to

  @T-UC-006-main-rest-warnings @main-flow @rest
  Scenario: Sync creatives via REST — partial success with warnings
    Given the Buyer is authenticated with a valid principal_id
    And two creatives: one valid and one with an empty name
    When the Buyer Agent syncs both creatives
    Then the response should include one creative with action "created"
    And the response should include one creative with action "failed"
    # POST-S4: Buyer knows about per-creative warnings

  @T-UC-006-main-rest-approval @main-flow @rest
  Scenario: Sync creatives via REST — approval workflow routing
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And the tenant has approval_mode set to "require-human"
    When the Buyer Agent syncs the creative
    Then the creative status should be "pending_review"
    And a workflow step should be created for the Seller
    # POST-S5: Creative routed to approval workflow

  @T-UC-006-main-rest-lenient-warnings @main-flow @rest
  Scenario: Sync creatives — lenient mode with mixed assignment results
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And assignments to three packages: two valid, one non-existent
    And validation_mode is "lenient"
    When the Buyer Agent syncs the creative
    Then the creative should have action "created"
    And two assignments should be created successfully
    And the response should include assignment_errors for the non-existent package
    # POST-S3: Buyer knows successful assignments
    # POST-S4: Buyer knows about assignment errors

  @T-UC-006-main-rest-provenance-warning @main-flow @rest
  Scenario: Sync creatives via REST — provenance warning when policy requires it
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has a product with creative_policy.provenance_required = true
    And a creative with a known format_id but no provenance metadata
    When the Buyer Agent syncs the creative
    Then the creative should have action "created"
    And the response should include a warning about missing provenance
    And the creative should be flagged for review
    # POST-S4: Buyer knows about provenance warning

  @T-UC-006-main-rest-weight @main-flow @rest
  Scenario: Sync creatives via REST — assignment with explicit weight
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And an assignment with package_id "pkg-1" and weight 50
    When the Buyer Agent syncs the creative
    Then the assignment should be created with the specified weight
    # POST-S3: Buyer knows assignment details including weight
    # --- Main Flow: MCP ---

  @T-UC-006-main-mcp @main-flow @mcp
  Scenario: Sync creatives via MCP — successful create
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "Summer Banner" and a known format_id
    And the creative does not exist in the Seller's library
    When the Buyer Agent syncs the creative
    Then the response should include the creative with action "created"
    And the creative should have a status reflecting the approval workflow
    # POST-S1: Buyer knows creative was successfully created
    # POST-S2: Buyer knows action = created

  @T-UC-006-main-mcp-update @main-flow @mcp
  Scenario: Sync creatives via MCP — successful update
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "Summer Banner" and a known format_id
    And the creative already exists in the Seller's library for this principal
    When the Buyer Agent syncs the creative
    Then the response should include the creative with action "updated"
    # POST-S1: Buyer knows creative was updated

  @T-UC-006-ext-a-rest @extension @ext-a @error @rest
  Scenario: Authentication required — missing principal_id (REST)
    Given the Buyer has no authentication credentials
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the operation should fail
    And the error code should be "AUTH_REQUIRED"
    And the error message should contain "authentication"
    And the error should include a "suggestion" field
    And the suggestion should contain "authentication credentials"
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains missing authentication
    # POST-F3: Suggestion for recovery

  @T-UC-006-ext-a-mcp @extension @ext-a @error @mcp
  Scenario: Authentication required — missing principal_id (MCP)
    Given the Buyer has no authentication credentials
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the operation should fail
    And the error code should be "AUTH_REQUIRED"
    And the error should include a "suggestion" field
    And the suggestion should contain "authentication credentials"
    # POST-F1, POST-F2, POST-F3

  @T-UC-006-ext-a-empty @extension @ext-a @error
  Scenario: Authentication required — empty principal_id
    Given the Buyer has an empty principal_id in the authentication context
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the operation should fail
    And the error code should be "AUTH_REQUIRED"
    And the error should include a "suggestion" field
    # POST-F1, POST-F2, POST-F3
    # --- ext-b: TENANT_NOT_FOUND ---

  @T-UC-006-ext-b-rest @extension @ext-b @error @rest
  Scenario: Tenant not found — principal has no tenant (REST)
    Given the Buyer is authenticated with a valid principal_id
    But the principal has no associated tenant
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the operation should fail
    And the error code should be "TENANT_NOT_FOUND"
    And the error message should contain "tenant"
    And the error should include a "suggestion" field
    And the suggestion should contain "tenant"
    # POST-F1, POST-F2, POST-F3

  @T-UC-006-ext-b-mcp @extension @ext-b @error @mcp
  Scenario: Tenant not found — principal has no tenant (MCP)
    Given the Buyer is authenticated with a valid principal_id
    But the principal has no associated tenant
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the operation should fail
    And the error code should be "TENANT_NOT_FOUND"
    And the error should include a "suggestion" field
    # POST-F1, POST-F2, POST-F3
    # --- ext-c: CREATIVE_VALIDATION_FAILED ---

  @T-UC-006-ext-c-rest @extension @ext-c @error @rest
  Scenario: Creative validation failed — schema violation (REST)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with invalid schema structure
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_VALIDATION_FAILED"
    And the error message should contain "validation"
    And the error should include a "suggestion" field
    And the suggestion should contain "CreativeAsset schema"
    # POST-F2: Error explains validation failure
    # POST-F3: Suggestion for corrective action

  @T-UC-006-ext-c-mcp @extension @ext-c @error @mcp
  Scenario: Creative validation failed — schema violation (MCP)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with invalid schema structure
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_VALIDATION_FAILED"
    And the error should include a "suggestion" field
    And the suggestion should contain "CreativeAsset schema"
    # POST-F2, POST-F3
    # --- ext-d: CREATIVE_NAME_EMPTY ---

  @T-UC-006-ext-d-rest @extension @ext-d @error @rest
  Scenario: Creative name empty (REST)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "" and a known format_id
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_NAME_EMPTY"
    And the error message should contain "name"
    And the error should include a "suggestion" field
    And the suggestion should contain "non-empty name"
    # POST-F2, POST-F3

  @T-UC-006-ext-d-mcp @extension @ext-d @error @mcp
  Scenario: Creative name empty (MCP)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "" and a known format_id
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_NAME_EMPTY"
    And the error should include a "suggestion" field
    # POST-F2, POST-F3

  @T-UC-006-ext-d-whitespace @extension @ext-d @error
  Scenario: Creative name whitespace-only
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "   " and a known format_id
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_NAME_EMPTY"
    And the error should include a "suggestion" field
    # POST-F2, POST-F3
    # --- ext-e: CREATIVE_FORMAT_REQUIRED ---

  @T-UC-006-ext-e-rest @extension @ext-e @error @rest
  Scenario: Creative format required — missing format_id (REST)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "Banner" but no format_id
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_FORMAT_REQUIRED"
    And the error message should contain "format"
    And the error should include a "suggestion" field
    And the suggestion should contain "format_id"
    # POST-F2, POST-F3

  @T-UC-006-ext-e-mcp @extension @ext-e @error @mcp
  Scenario: Creative format required — missing format_id (MCP)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "Banner" but no format_id
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_FORMAT_REQUIRED"
    And the error should include a "suggestion" field
    # POST-F2, POST-F3
    # --- ext-f: CREATIVE_FORMAT_UNKNOWN ---

  @T-UC-006-ext-f-rest @extension @ext-f @error @rest
  Scenario: Creative format unknown — not in agent registry (REST)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a format_id that does not exist in any agent registry
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_FORMAT_UNKNOWN"
    And the error message should contain "unknown format"
    And the error should include a "suggestion" field
    And the suggestion should contain "list_creative_formats"
    # POST-F2, POST-F3

  @T-UC-006-ext-f-mcp @extension @ext-f @error @mcp
  Scenario: Creative format unknown — not in agent registry (MCP)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a format_id that does not exist in any agent registry
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_FORMAT_UNKNOWN"
    And the error should include a "suggestion" field
    And the suggestion should contain "list_creative_formats"
    # POST-F2, POST-F3
    # --- ext-g: CREATIVE_AGENT_UNREACHABLE ---

  @T-UC-006-ext-g-rest @extension @ext-g @error @rest
  Scenario: Creative agent unreachable (REST)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a format_id whose agent_url is unreachable
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_AGENT_UNREACHABLE"
    And the error message should contain "unreachable"
    And the error should include a "suggestion" field
    And the suggestion should contain "try again"
    # POST-F2, POST-F3

  @T-UC-006-ext-g-mcp @extension @ext-g @error @mcp
  Scenario: Creative agent unreachable (MCP)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a format_id whose agent_url is unreachable
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_AGENT_UNREACHABLE"
    And the error should include a "suggestion" field
    And the suggestion should contain "try again"
    # POST-F2, POST-F3
    # --- ext-h: CREATIVE_PREVIEW_FAILED ---

  @T-UC-006-ext-h-rest @extension @ext-h @error @rest
  Scenario: Creative preview failed — no previews generated (REST)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id but no media_url
    And the creative agent returns no preview URLs
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_PREVIEW_FAILED"
    And the error message should contain "preview"
    And the error should include a "suggestion" field
    And the suggestion should contain "media_url"
    # POST-F2, POST-F3

  @T-UC-006-ext-h-mcp @extension @ext-h @error @mcp
  Scenario: Creative preview failed — no previews generated (MCP)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id but no media_url
    And the creative agent returns no preview URLs
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_PREVIEW_FAILED"
    And the error should include a "suggestion" field
    # POST-F2, POST-F3
    # --- ext-i: CREATIVE_GEMINI_KEY_MISSING ---

  @T-UC-006-ext-i-rest @extension @ext-i @error @rest
  Scenario: Gemini key missing — generative creative without config (REST)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a generative format (output_format_ids present)
    And the Seller Agent does not have GEMINI_API_KEY configured
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_GEMINI_KEY_MISSING"
    And the error message should contain "GEMINI_API_KEY"
    And the error should include a "suggestion" field
    And the suggestion should contain "seller"
    # POST-F2, POST-F3

  @T-UC-006-ext-i-mcp @extension @ext-i @error @mcp
  Scenario: Gemini key missing — generative creative without config (MCP)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a generative format (output_format_ids present)
    And the Seller Agent does not have GEMINI_API_KEY configured
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_GEMINI_KEY_MISSING"
    And the error should include a "suggestion" field
    And the suggestion should contain "seller"
    # POST-F2, POST-F3
    # --- ext-j: PACKAGE_NOT_FOUND (strict) ---

  @T-UC-006-ext-j-rest @extension @ext-j @error @rest
  Scenario: Package not found — strict mode aborts (REST)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And assignments referencing a non-existent package_id
    And validation_mode is "strict"
    When the Buyer Agent syncs the creative
    Then the operation should fail with an assignment error
    And the error code should be "PACKAGE_NOT_FOUND"
    And the error message should contain "package"
    And the error should include a "suggestion" field
    And the suggestion should contain "media buys"
    # POST-F2, POST-F3

  @T-UC-006-ext-j-mcp @extension @ext-j @error @mcp
  Scenario: Package not found — strict mode aborts (MCP)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And assignments referencing a non-existent package_id
    And validation_mode is "strict"
    When the Buyer Agent syncs the creative
    Then the operation should fail with an assignment error
    And the error code should be "PACKAGE_NOT_FOUND"
    And the error should include a "suggestion" field
    # POST-F2, POST-F3
    # --- ext-k: FORMAT_MISMATCH (strict) ---

  @T-UC-006-ext-k-rest @extension @ext-k @error @rest
  Scenario: Format mismatch — creative format incompatible with product (REST)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with format_id "agent1/banner-300x250"
    And assignments to a package whose product only accepts "agent1/video-pre-roll"
    And validation_mode is "strict"
    When the Buyer Agent syncs the creative
    Then the operation should fail with an assignment error
    And the error code should be "FORMAT_MISMATCH"
    And the error message should contain "not supported by product"
    And the error should include a "suggestion" field
    And the suggestion should contain "list_creative_formats"
    # POST-F2, POST-F3

  @T-UC-006-ext-k-mcp @extension @ext-k @error @mcp
  Scenario: Format mismatch — creative format incompatible with product (MCP)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with format_id "agent1/banner-300x250"
    And assignments to a package whose product only accepts "agent1/video-pre-roll"
    And validation_mode is "strict"
    When the Buyer Agent syncs the creative
    Then the operation should fail with an assignment error
    And the error code should be "FORMAT_MISMATCH"
    And the error should include a "suggestion" field
    And the suggestion should contain "list_creative_formats"
    # POST-F2, POST-F3

  @T-UC-006-rule-033-inv1 @invariant @BR-RULE-033
  Scenario: INV-1 — per-creative failure does not abort other creatives
    Given the Buyer is authenticated with a valid principal_id
    And two creatives: one valid and one with an empty name
    When the Buyer Agent syncs both creatives
    Then the valid creative should have action "created"
    And the invalid creative should have action "failed"
    And the valid creative should not be affected by the invalid one

  @T-UC-006-rule-033-inv2 @invariant @BR-RULE-033 @error
  Scenario: INV-2 — assignment error in strict mode aborts all assignments
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And assignments to two packages: one valid and one non-existent
    And validation_mode is "strict"
    When the Buyer Agent syncs the creative
    Then the assignment processing should abort with an error
    And no assignments should be created
    And the error should include a "suggestion" field
    # POST-F3: Suggestion for recovery

  @T-UC-006-rule-033-inv3 @invariant @BR-RULE-033
  Scenario: INV-3 — assignment error in lenient mode skips and continues
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And assignments to two packages: one valid and one non-existent
    And validation_mode is "lenient"
    When the Buyer Agent syncs the creative
    Then the valid assignment should be created
    And the non-existent package should be reported as a warning
    And processing should continue normally

  @T-UC-006-rule-033-inv4 @invariant @BR-RULE-033
  Scenario: INV-4 — assignment errors always recorded in response
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And assignments to a non-existent package
    And validation_mode is "lenient"
    When the Buyer Agent syncs the creative
    Then the response should include assignment_errors
    And the assignment_errors should contain the package_id

  @T-UC-006-rule-033-inv5 @invariant @BR-RULE-033
  Scenario: INV-5 — default validation_mode is strict
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And assignments to a non-existent package
    And no validation_mode is specified
    When the Buyer Agent syncs the creative
    Then the assignment processing should abort with an error
    And the behavior should match strict mode
    # --- BR-RULE-034: Cross-Principal Isolation ---

  @T-UC-006-rule-034-inv1 @invariant @BR-RULE-034
  Scenario: INV-1 — creative lookup uses triple key
    Given the Buyer is authenticated as principal "buyer-A"
    And a creative "creative-1" exists for principal "buyer-A" in the tenant
    When the Buyer Agent syncs creative "creative-1"
    Then the existing creative should be updated (matched by triple key)

  @T-UC-006-rule-034-inv2 @invariant @BR-RULE-034
  Scenario: INV-2 — cross-principal creative creates new silently
    Given the Buyer is authenticated as principal "buyer-B"
    And a creative "creative-1" exists for principal "buyer-A" in the same tenant
    When the Buyer Agent syncs creative "creative-1" as principal "buyer-B"
    Then a new creative should be created for principal "buyer-B"
    And the existing creative for principal "buyer-A" should remain unchanged

  @T-UC-006-rule-034-inv3 @invariant @BR-RULE-034
  Scenario: INV-3 — new creative stamped with authenticated principal
    Given the Buyer is authenticated as principal "buyer-A"
    And a creative that does not exist in the library
    When the Buyer Agent syncs the creative
    Then the created creative should be associated with principal "buyer-A"
    # --- BR-RULE-035: Creative Format Validation ---

  @T-UC-006-rule-035-static @invariant @BR-RULE-035
  Scenario: Static creative validated by creative agent
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known HTTP-based format_id
    And the creative agent is reachable
    When the Buyer Agent syncs the creative
    Then the creative should be validated by the creative agent
    And preview URLs should be generated
    And the creative should have action "created"

  @T-UC-006-rule-035-inv2 @invariant @BR-RULE-035
  Scenario: INV-2 — adapter format skips external validation
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a non-HTTP adapter format_id
    When the Buyer Agent syncs the creative
    Then the creative should be processed without external agent validation
    And the creative should have action "created" or "updated"
    # --- BR-RULE-036: Generative Creative Build ---

  @T-UC-006-rule-036-inv1 @invariant @BR-RULE-036
  Scenario: INV-1 — generative detection via output_format_ids
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a format that has output_format_ids defined
    And GEMINI_API_KEY is configured
    When the Buyer Agent syncs the creative
    Then the creative should be processed as generative
    And the creative should have generated content

  @T-UC-006-rule-036-inv2 @invariant @BR-RULE-036
  Scenario: INV-2 — prompt from assets (message role)
    Given the Buyer is authenticated with a valid principal_id
    And a generative creative with an asset of role "message" containing "Create summer vibes"
    And GEMINI_API_KEY is configured
    When the Buyer Agent syncs the creative
    Then the generative build should use "Create summer vibes" as the prompt

  @T-UC-006-rule-036-inv3 @invariant @BR-RULE-036
  Scenario: INV-3 — prompt fallback to inputs context_description
    Given the Buyer is authenticated with a valid principal_id
    And a generative creative with no prompt assets but inputs[0].context_description = "Holiday theme"
    And GEMINI_API_KEY is configured
    When the Buyer Agent syncs the creative
    Then the generative build should use "Holiday theme" as the prompt

  @T-UC-006-rule-036-inv4 @invariant @BR-RULE-036
  Scenario: INV-4 — create fallback to creative name as prompt
    Given the Buyer is authenticated with a valid principal_id
    And a generative creative named "Summer Sale Banner" with no prompt assets or inputs
    And GEMINI_API_KEY is configured
    When the Buyer Agent creates the creative
    Then the generative build should use "Create a creative for: Summer Sale Banner" as the prompt

  @T-UC-006-rule-036-inv5 @invariant @BR-RULE-036
  Scenario: INV-5 — update without prompt preserves existing data
    Given the Buyer is authenticated with a valid principal_id
    And a generative creative that already exists with generated content
    And the update has no prompt assets or inputs
    And GEMINI_API_KEY is configured
    When the Buyer Agent updates the creative
    Then the generative build should be skipped
    And the existing creative data should be preserved

  @T-UC-006-rule-036-inv6 @invariant @BR-RULE-036
  Scenario: INV-6 — user assets take priority over generative output
    Given the Buyer is authenticated with a valid principal_id
    And a generative creative with both user-provided assets and generative prompt
    And GEMINI_API_KEY is configured
    When the Buyer Agent syncs the creative
    Then the user-provided assets should be preserved
    And user assets should take priority over any generated content
    # --- BR-RULE-037: Approval Workflow ---

  @T-UC-006-rule-037-inv1 @invariant @BR-RULE-037
  Scenario: INV-1 — default approval mode is require-human
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has no approval_mode configured
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the creative status should be "pending_review"
    And a workflow step should be created

  @T-UC-006-rule-037-inv2 @invariant @BR-RULE-037
  Scenario: INV-2 — auto-approve sets status directly
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has approval_mode "auto-approve"
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the creative status should be "approved"
    And no workflow steps should be created
    And no Slack notification should be sent

  @T-UC-006-rule-037-inv3 @invariant @BR-RULE-037
  Scenario: INV-3 — require-human creates workflow and sends Slack
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has approval_mode "require-human"
    And the tenant has a slack_webhook_url configured
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the creative status should be "pending_review"
    And a workflow step should be created with type "creative_approval"
    And a Slack notification should be sent immediately

  @T-UC-006-rule-037-inv4 @invariant @BR-RULE-037
  Scenario: INV-4 — ai-powered creates workflow and submits AI review
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has approval_mode "ai-powered"
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the creative status should be "pending_review"
    And a workflow step should be created
    And a background AI review task should be submitted
    And Slack notification should be deferred until AI review completes

  @T-UC-006-rule-037-inv5 @invariant @BR-RULE-037
  Scenario: INV-5 — workflow step attributes
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has approval_mode "require-human"
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the workflow step should have step_type "creative_approval"
    And the workflow step should have owner "publisher"
    And the workflow step should have status "requires_approval"

  @T-UC-006-rule-037-inv6 @invariant @BR-RULE-037
  Scenario: INV-6 — Slack only sent when webhook configured and creatives need approval
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has approval_mode "require-human"
    And the tenant has no slack_webhook_url configured
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the creative status should be "pending_review"
    But no Slack notification should be sent
    # --- BR-RULE-038: Assignment Package Validation ---

  @T-UC-006-rule-038-inv1 @invariant @BR-RULE-038
  Scenario: INV-1 — package lookup is tenant-scoped
    Given the Buyer is authenticated with a valid principal_id
    And a package exists in a different tenant
    And assignments referencing that package_id
    And validation_mode is "strict"
    When the Buyer Agent syncs the creative
    Then the assignment should fail with "PACKAGE_NOT_FOUND"
    And the cross-tenant package should not be accessible

  @T-UC-006-rule-038-inv3 @invariant @BR-RULE-038
  Scenario: INV-3 — idempotent assignment upsert
    Given the Buyer is authenticated with a valid principal_id
    And a creative already assigned to a package
    And assignments referencing the same package_id
    When the Buyer Agent syncs the creative
    Then the existing assignment should be updated (not duplicated)

  @T-UC-006-rule-038-inv4 @invariant @BR-RULE-038
  Scenario: INV-4 — draft media buy with approved_at transitions to pending_creatives
    Given the Buyer is authenticated with a valid principal_id
    And a media buy with status "draft" and approved_at set
    And a creative with a known format_id
    And assignments to a package in that media buy
    When the Buyer Agent syncs the creative with assignments
    Then the media buy status should transition to "pending_creatives"

  @T-UC-006-rule-038-inv4-violated @invariant @BR-RULE-038
  Scenario: INV-4 violated — draft media buy without approved_at does not transition
    Given the Buyer is authenticated with a valid principal_id
    And a media buy with status "draft" and approved_at null
    And a creative with a known format_id
    And assignments to a package in that media buy
    When the Buyer Agent syncs the creative with assignments
    Then the media buy status should remain "draft"

  @T-UC-006-rule-038-inv5 @invariant @BR-RULE-038
  Scenario: INV-5 — non-draft media buy does not transition
    Given the Buyer is authenticated with a valid principal_id
    And a media buy with status "active" (non-draft)
    And a creative with a known format_id
    And assignments to a package in that media buy
    When the Buyer Agent syncs the creative with assignments
    Then the media buy status should remain "active"
    # --- BR-RULE-039: Assignment Format Compatibility ---

  @T-UC-006-rule-039-inv1 @invariant @BR-RULE-039
  Scenario: INV-1 — URL normalization strips trailing slash and /mcp
    Given the Buyer is authenticated with a valid principal_id
    And a creative with format agent_url "https://agent.example.com/mcp/"
    And a product with format agent_url "https://agent.example.com"
    And matching format_id strings
    When format compatibility is checked
    Then the formats should match after URL normalization

  @T-UC-006-rule-039-inv2 @invariant @BR-RULE-039 @error
  Scenario: INV-2 — match requires both normalized agent_url AND exact format_id
    Given the Buyer is authenticated with a valid principal_id
    And a creative with format agent_url "https://agent.example.com" and format_id "banner-300x250"
    And a product with format agent_url "https://agent.example.com" and format_id "video-pre-roll"
    And validation_mode is "strict"
    When the Buyer Agent syncs the creative with assignments
    Then the assignment should fail with "FORMAT_MISMATCH"
    And the error should include a "suggestion" field
    # Agent URL matches but format_id differs — partial match is not sufficient

  @T-UC-006-rule-039-inv3 @invariant @BR-RULE-039
  Scenario: INV-3 — empty product format_ids allows all formats
    Given the Buyer is authenticated with a valid principal_id
    And a creative with any format_id
    And assignments to a package whose product has empty format_ids
    When the Buyer Agent syncs the creative
    Then the format compatibility check should pass
    And the assignment should be created successfully

  @T-UC-006-rule-039-inv4 @invariant @BR-RULE-039
  Scenario: INV-4 — product format_ids accepts both id and format_id keys
    Given the Buyer is authenticated with a valid principal_id
    And a product with format_ids using "format_id" key
    And a creative with a matching format
    When format compatibility is checked
    Then the formats should match using the "format_id" key

  @T-UC-006-rule-039-inv6 @invariant @BR-RULE-039
  Scenario: INV-6 — no product_id on package skips format check
    Given the Buyer is authenticated with a valid principal_id
    And a creative with any format_id
    And assignments to a package that has no product_id
    When the Buyer Agent syncs the creative
    Then the format compatibility check should be skipped
    And the assignment should be created successfully

  @T-UC-006-rule-039-inv5-lenient @invariant @BR-RULE-039
  Scenario: INV-5 — format mismatch in lenient mode skips assignment
    Given the Buyer is authenticated with a valid principal_id
    And a creative with format_id "agent/banner-300x250"
    And assignments to two packages: one with compatible format and one incompatible
    And validation_mode is "lenient"
    When the Buyer Agent syncs the creative
    Then the compatible package assignment should be created
    And the incompatible package should be reported in assignment_errors
    And processing should continue without aborting
    # --- BR-RULE-040: Media Buy Status Transition ---

  @T-UC-006-rule-040-inv1 @invariant @BR-RULE-040
  Scenario: INV-1 — draft with approved_at transitions to pending_creatives
    Given the Buyer is authenticated with a valid principal_id
    And a media buy with status "draft" and approved_at set
    And assignments to a package in that media buy
    When the Buyer Agent syncs the creative with assignments
    Then the media buy status should transition to "pending_creatives"

  @T-UC-006-rule-040-inv2 @invariant @BR-RULE-040
  Scenario: INV-2 — draft without approved_at stays draft
    Given the Buyer is authenticated with a valid principal_id
    And a media buy with status "draft" and approved_at null
    And assignments to a package in that media buy
    When the Buyer Agent syncs the creative with assignments
    Then the media buy status should remain "draft"

  @T-UC-006-rule-040-inv3 @invariant @BR-RULE-040
  Scenario: INV-3 — non-draft status unchanged
    Given the Buyer is authenticated with a valid principal_id
    And a media buy with status "active" (non-draft)
    And assignments to a package in that media buy
    When the Buyer Agent syncs the creative with assignments
    Then the media buy status should remain "active"

  @T-UC-006-rule-040-inv4 @invariant @BR-RULE-040
  Scenario: INV-4 — both new and updated assignments trigger transition check
    Given the Buyer is authenticated with a valid principal_id
    And a media buy with status "draft" and approved_at set
    And an existing assignment to a package in that media buy
    And a new assignment to another package in the same media buy
    When the Buyer Agent syncs the creative with assignments
    Then the media buy status should transition to "pending_creatives"
    # --- BR-RULE-093: Assignment Weight and Delivery Semantics ---

  @T-UC-006-rule-093-inv1 @invariant @BR-RULE-093
  Scenario: INV-1 — weight 0 means paused (assigned but no delivery)
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And an assignment with package_id "pkg-1" and weight 0
    When the Buyer Agent syncs the creative
    Then the assignment should be created with weight 0
    And the creative should be assigned but paused (no delivery)

  @T-UC-006-rule-093-inv2 @invariant @BR-RULE-093
  Scenario: INV-2 — weight omitted means equal rotation
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And an assignment with package_id "pkg-1" and no weight specified
    When the Buyer Agent syncs the creative
    Then the assignment should be created
    And the creative should receive equal rotation with other unweighted creatives

  @T-UC-006-rule-093-inv3 @invariant @BR-RULE-093
  Scenario: INV-3 — proportional delivery with different weights
    Given the Buyer is authenticated with a valid principal_id
    And creative "creative-A" assigned to "pkg-1" with weight 80
    And creative "creative-B" assigned to "pkg-1" with weight 20
    When the Buyer Agent syncs the creatives
    Then creative-A should receive proportionally more delivery than creative-B
    And the delivery ratio should reflect the weight ratio (80:20)
    # --- BR-RULE-094: Creative Provenance Policy Enforcement ---

  @T-UC-006-rule-094-inv1 @invariant @BR-RULE-094
  Scenario: INV-1 — provenance absent when required triggers warning
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has a product with creative_policy.provenance_required = true
    And a creative with a known format_id but no provenance metadata
    When the Buyer Agent syncs the creative
    Then the creative should be processed (not rejected)
    And a warning should be appended about missing provenance
    And the creative should be flagged for review

  @T-UC-006-rule-094-inv2 @invariant @BR-RULE-094
  Scenario: INV-2 — provenance present when required passes normally
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has a product with creative_policy.provenance_required = true
    And a creative with a known format_id and valid provenance metadata
    When the Buyer Agent syncs the creative
    Then the creative should be processed normally
    And no provenance warning should be generated

  @T-UC-006-rule-094-inv3 @invariant @BR-RULE-094
  Scenario: INV-3 — no provenance policy means check skipped
    Given the Buyer is authenticated with a valid principal_id
    And no product in the tenant has provenance_required set
    And a creative with no provenance metadata
    When the Buyer Agent syncs the creative
    Then the creative should be processed normally
    And no provenance warning should be generated

  @T-UC-006-rule-094-inv4 @invariant @BR-RULE-094
  Scenario: INV-4 — creative_policy null on product means check skipped
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has a product with creative_policy = null
    And a creative with no provenance metadata
    When the Buyer Agent syncs the creative
    Then the creative should be processed normally
    And no provenance warning should be generated

  @T-UC-006-rule-094-inv5 @invariant @BR-RULE-094
  Scenario: INV-5 — asset-level provenance replaces creative-level entirely
    Given the Buyer is authenticated with a valid principal_id
    And a creative with provenance declaring digital_source_type "digital_capture"
    And an asset within the creative declaring digital_source_type "trained_algorithmic_media"
    When the Buyer Agent syncs the creative
    Then the asset should have provenance "trained_algorithmic_media" (not inherited "digital_capture")
    And no field-level merging should occur

  @T-UC-006-partition-validation-mode @partition @validation-mode
  Scenario Outline: Validation mode behavior — <partition>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And assignments to a non-existent package
    And validation_mode is "<mode>"
    When the Buyer Agent syncs the creative
    Then the assignment result should be "<outcome>"
    # --- approval_mode partitions ---

    Examples: Valid modes
      | partition    | mode    | outcome                             |
      | strict       | strict  | operation aborts with error          |
      | lenient      | lenient | warning logged, processing continues |

    Examples: Invalid modes
      | partition      | mode     | outcome                              |
      | unknown_value  | partial  | rejected with VALIDATION_ERROR       |

  @T-UC-006-partition-approval-mode @partition @approval-mode
  Scenario Outline: Approval mode routing — <partition>
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has approval_mode "<mode>"
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the creative status should be "<status>"
    And workflow steps created should be "<workflow>"
    # --- creative_scope partitions ---

    Examples: Approval modes
      | partition      | mode           | status         | workflow  |
      | auto_approve   | auto-approve   | approved       | none      |
      | require_human  | require-human  | pending_review | yes       |
      | ai_powered     | ai-powered     | pending_review | yes       |
      | not_set        |                | pending_review | yes       |

  @T-UC-006-partition-creative-scope @partition @creative-scope
  Scenario Outline: Creative scope resolution — <partition>
    Given the Buyer is authenticated as principal "<principal>"
    And creative "<creative_id>" <existence>
    When the Buyer Agent syncs the creative
    Then the action should be "<action>"
    # --- format_id partitions ---

    Examples: Scope resolution
      | partition         | principal | creative_id | existence                                | action   |
      | new_creative      | buyer-A   | c-1         | does not exist for this principal         | created  |
      | existing_creative | buyer-A   | c-1         | exists for principal buyer-A              | updated  |
      | cross_principal   | buyer-B   | c-1         | exists for principal buyer-A only         | created  |

  @T-UC-006-partition-format-id @partition @format-id
  Scenario Outline: Format validation — <partition>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with <format_setup>
    When the Buyer Agent syncs the creative
    Then the result should be "<outcome>"
    # --- generative_build partitions ---

    Examples: Format partitions
      | partition          | format_setup                             | outcome                      |
      | known_http_format  | a known HTTP-based format_id             | success                      |
      | adapter_format     | a non-HTTP adapter format_id             | success (no agent validation)|
      | missing_format_id  | no format_id                             | CREATIVE_FORMAT_REQUIRED     |
      | unknown_format     | a format_id unknown to all agents        | CREATIVE_FORMAT_UNKNOWN      |
      | agent_unreachable  | a format_id whose agent is unreachable   | CREATIVE_AGENT_UNREACHABLE   |
      | empty_name         | an empty name and a known format_id      | CREATIVE_NAME_EMPTY          |

  @T-UC-006-partition-generative @partition @generative
  Scenario Outline: Generative build detection — <partition>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with <format_type>
    And <prompt_source>
    When the Buyer Agent syncs the creative
    Then the result should be "<outcome>"
    # --- assignment_package partitions ---

    Examples: Generative partitions
      | partition                       | format_type                        | prompt_source                          | outcome                      |
      | static_creative                 | no output_format_ids               | any assets                             | standard processing          |
      | generative_with_prompt          | output_format_ids present          | message asset with prompt text         | generative build with prompt |
      | generative_create_name_fallback | output_format_ids present (create) | no prompt assets or inputs             | generative build with name   |
      | generative_no_gemini_key        | output_format_ids present          | message asset but no GEMINI_API_KEY    | CREATIVE_GEMINI_KEY_MISSING  |

  @T-UC-006-partition-assignment-pkg @partition @assignment-package
  Scenario Outline: Assignment package validation — <partition>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And <package_setup>
    And validation_mode is "strict"
    When the Buyer Agent syncs the creative
    Then the result should be "<outcome>"
    # --- assignment_format partitions ---

    Examples: Package partitions
      | partition           | package_setup                              | outcome             |
      | existing_package    | assignments to an existing package          | assignment created  |
      | existing_assignment | the creative is already assigned to package | assignment updated  |
      | package_not_found   | assignments to a non-existent package       | PACKAGE_NOT_FOUND   |

  @T-UC-006-partition-assignment-fmt @partition @assignment-format
  Scenario Outline: Assignment format compatibility — <partition>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with format_id "<creative_format>"
    And assignments to a package with <product_setup>
    And validation_mode is "strict"
    When the Buyer Agent syncs the creative
    Then the result should be "<outcome>"
    # --- media_buy_status partitions ---

    Examples: Format compatibility partitions
      | partition       | creative_format       | product_setup                            | outcome            |
      | format_matches  | agent/banner-300x250  | product accepting agent/banner-300x250   | assignment created |
      | no_restrictions | agent/banner-300x250  | product with empty format_ids            | assignment created |
      | no_product_id   | agent/banner-300x250  | package with no product_id               | assignment created |
      | format_mismatch | agent/banner-300x250  | product accepting only agent/video-30s   | FORMAT_MISMATCH    |

  @T-UC-006-partition-mb-status @partition @media-buy-status
  Scenario Outline: Media buy status transition on assignment — <partition>
    Given the Buyer is authenticated with a valid principal_id
    And a media buy with status "<mb_status>" and approved_at <approved_at>
    And assignments to a package in that media buy
    When the Buyer Agent syncs the creative
    Then the media buy status should be "<final_status>"
    # --- provenance partitions ---

    Examples: Status transition partitions
      | partition          | mb_status | approved_at | final_status      |
      | draft_approved     | draft     | set         | pending_creatives |
      | draft_not_approved | draft     | null        | draft             |
      | non_draft          | active    | set         | active            |

  @T-UC-006-partition-provenance @partition @provenance
  Scenario Outline: Provenance policy enforcement — <partition>
    Given the Buyer is authenticated with a valid principal_id
    And <provenance_setup>
    And <policy_setup>
    When the Buyer Agent syncs the creative
    Then <outcome>
    # --- assignments_structure partitions ---

    Examples: Provenance partitions
      | partition                        | provenance_setup                        | policy_setup                                              | outcome                                          |
      | provenance_present_required      | a creative with provenance metadata     | a product with creative_policy.provenance_required = true | the creative should be processed without warning |
      | provenance_present_not_required  | a creative with provenance metadata     | no product with provenance_required                       | the creative should be processed without warning |
      | provenance_absent_not_required   | a creative without provenance metadata  | no product with provenance_required                       | the creative should be processed without warning |
      | provenance_absent_when_required  | a creative without provenance metadata  | a product with creative_policy.provenance_required = true | the creative should have a provenance warning    |

  @T-UC-006-partition-assignments-structure @partition @assignments-structure
  Scenario Outline: Assignments array structure — <partition>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And <assignment_setup>
    When the Buyer Agent syncs the creative
    Then <outcome>
    # --- assignment_weight partitions ---

    Examples: Valid assignment structures
      | partition                | assignment_setup                                                       | outcome                                                       |
      | single_assignment        | an assignment with creative_id "c1" and package_id "p1"               | the assignment should be created successfully                 |
      | multi_assignment         | assignments mapping creative "c1" to packages "p1" and "p2"           | both assignments should be created                            |
      | with_weight              | an assignment with creative_id "c1", package_id "p1", and weight 50   | the assignment should be created with weight 50               |
      | with_placement_targeting | an assignment with creative_id "c1", package_id "p1", and placement_ids ["slot_a"] | the assignment should be created with placement targeting |
      | absent                   | no assignments field                                                   | no assignment processing should occur                         |

    Examples: Invalid assignment structures
      | partition            | assignment_setup                                | outcome                                                              |
      | empty_array          | an empty assignments array                      | the error should be ASSIGNMENTS_EMPTY with suggestion                |
      | missing_creative_id  | an assignment entry missing creative_id         | the error should be ASSIGNMENT_CREATIVE_ID_REQUIRED with suggestion  |
      | missing_package_id   | an assignment entry missing package_id          | the error should be ASSIGNMENT_PACKAGE_ID_REQUIRED with suggestion   |

  @T-UC-006-partition-assignment-weight @partition @assignment-weight
  Scenario Outline: Assignment weight validation — <partition>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And an assignment with package_id "pkg-1" and weight <weight>
    When the Buyer Agent syncs the creative
    Then <outcome>
    # --- authentication partitions ---

    Examples: Valid weights
      | partition            | weight | outcome                                          |
      | weight_absent        |        | the assignment should use equal rotation          |
      | weight_typical       | 50     | the assignment should be created with weight 50   |
      | weight_boundary_min  | 0      | the assignment should be created as paused        |
      | weight_boundary_max  | 100    | the assignment should be created with weight 100  |

    Examples: Invalid weights
      | partition          | weight | outcome                                                                     |
      | weight_below_min   | -1     | the error should be ASSIGNMENT_WEIGHT_BELOW_MINIMUM with suggestion         |
      | weight_above_max   | 101    | the error should be ASSIGNMENT_WEIGHT_ABOVE_MAXIMUM with suggestion         |

  @T-UC-006-partition-auth @partition @authentication
  Scenario Outline: Authentication partition - <partition>
    Given <auth_state>
    And a creative with name "Banner" and a known format_id
    When the Buyer Agent syncs the creative
    Then <expected>
    # --- account partitions ---

    Examples:
      | partition | auth_state                                          | expected                                           |
      | typical   | the Buyer is authenticated with a valid principal_id | the creative should be processed successfully      |
      | missing   | the Buyer has no authentication credentials             | the request should be rejected with AUTH_REQUIRED   |
      | empty     | the request has an empty principal_id                | the request should be rejected with AUTH_REQUIRED   |

  @T-UC-006-partition-account @partition @account
  Scenario Outline: Account resolution — <partition>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And account is <account_setup>
    When the Buyer Agent syncs the creative
    Then <outcome>
    # --- idempotency_key partitions ---

    Examples: Valid accounts
      | partition                  | account_setup                                                  | outcome                                           |
      | explicit_account_id        | {"account_id": "acc_acme_001"}                                | the request should proceed with resolved account  |
      | natural_key_unambiguous    | {"brand": {"domain": "acme-corp.com"}, "operator": "acme.com"} | the request should proceed with resolved account  |

    Examples: Invalid accounts
      | partition                  | account_setup                                                               | outcome                                                       |
      | missing_account            | not provided                                                                | the error should be INVALID_REQUEST with suggestion           |
      | invalid_oneOf_both         | {"account_id": "acc_001", "brand": {"domain": "x.com"}, "operator": "x"}   | the error should be INVALID_REQUEST with suggestion           |
      | explicit_not_found         | {"account_id": "acc_nonexistent"}                                           | the error should be ACCOUNT_NOT_FOUND with suggestion         |
      | natural_key_not_found      | {"brand": {"domain": "unknown.com"}, "operator": "unknown.com"}            | the error should be ACCOUNT_NOT_FOUND with suggestion         |
      | natural_key_ambiguous      | {"brand": {"domain": "multi.com"}, "operator": "agency.com"}               | the error should be ACCOUNT_AMBIGUOUS with suggestion         |
      | account_setup_required     | {"account_id": "acc_new_unconfigured"}                                      | the error should be ACCOUNT_SETUP_REQUIRED with suggestion    |
      | account_payment_required   | {"account_id": "acc_overdue"}                                               | the error should be ACCOUNT_PAYMENT_REQUIRED with suggestion  |
      | account_suspended          | {"account_id": "acc_suspended"}                                             | the error should be ACCOUNT_SUSPENDED with suggestion         |
      | access_denied_id           | {"account_id": "acc_other_agent"}                                           | the error should be AUTHORIZATION_ERROR with suggestion       |
      | access_denied_natural_key  | {"brand": {"domain": "other-agent.com"}, "operator": "other-agent.com"}    | the error should be AUTHORIZATION_ERROR with suggestion       |

  @T-UC-006-partition-idempotency-key @partition @idempotency-key
  Scenario Outline: Idempotency key validation — <partition>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And idempotency_key is <key_value>
    When the Buyer Agent syncs the creative
    Then <expected>

    Examples: Valid keys
      | partition      | key_value                                | expected                                              |
      | absent         |                                          | the request should proceed without idempotency check  |
      | typical_valid  | "abc12345-retry-001"                     | the request should proceed normally                   |
      | boundary_min   | "12345678"                               | the request should proceed normally                   |
      | uuid_format    | "550e8400-e29b-41d4-a716-446655440000"   | the request should proceed normally                   |

    Examples: Invalid keys
      | partition      | key_value  | expected                                                      |
      | empty_string   | ""         | the error should be IDEMPOTENCY_KEY_TOO_SHORT with suggestion |
      | too_short      | "abc1234"  | the error should be IDEMPOTENCY_KEY_TOO_SHORT with suggestion |
      | too_long       | "a]x256"   | the error should be IDEMPOTENCY_KEY_TOO_LONG with suggestion  |

  @T-UC-006-boundary-approval @boundary @approval-mode
  Scenario Outline: Approval mode boundary — <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "Banner" and a known format_id
    And the tenant approval mode is <mode>
    When the Buyer Agent syncs the creative
    Then <expected>
    # --- validation_mode boundaries ---

    Examples:
      | boundary_point   | mode             | expected                                                   |
      | not set (null)   | not configured   | the creative should use require-human as default           |
      | auto-approve     | "auto-approve"   | the creative status should be set to approved immediately  |
      | require-human    | "require-human"  | a review workflow should be created with Slack notification |
      | ai-powered       | "ai-powered"     | a review workflow should be created with AI review         |

  @T-UC-006-boundary-validation-mode @boundary @validation-mode
  Scenario Outline: Validation mode boundary — <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "Banner" and a known format_id
    And validation_mode is <mode>
    And an assignment with a package that does not exist
    When the Buyer Agent syncs the creative
    Then <expected>
    # --- format_id boundaries ---

    Examples:
      | boundary_point           | mode       | expected                                                 |
      | not set (default strict) | not set    | the operation should abort with PACKAGE_NOT_FOUND        |
      | strict                   | "strict"   | the operation should abort with PACKAGE_NOT_FOUND        |
      | lenient                  | "lenient"  | the assignment should be skipped with a warning          |
      | unknown value            | "partial"  | the system should reject with VALIDATION_ERROR           |

  @T-UC-006-boundary-format-id @boundary @format-id
  Scenario Outline: Format validation boundary — <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    And <creative_state>
    When the Buyer Agent syncs the creative
    Then <expected>
    # --- generative_build boundaries ---

    Examples:
      | boundary_point           | creative_state                                              | expected                                                  |
      | missing format_id (null) | a creative with name "Banner" but no format_id              | the error should include "suggestion" field               |
      | known HTTP format        | a creative with a known HTTP-registered format_id           | the creative should be processed successfully             |
      | adapter format (non-HTTP)| a creative with an adapter (non-HTTP) format_id             | the creative should skip external format validation       |
      | unknown format           | a creative with an unknown format_id                        | the error should include "suggestion" field               |
      | agent unreachable        | a creative with a format_id whose agent is unreachable      | the error should include "suggestion" field               |
      | empty name               | a creative with format_id but an empty name                 | the error should include "suggestion" field               |

  @T-UC-006-boundary-generative @boundary @generative
  Scenario Outline: Generative build boundary — <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    And <creative_state>
    When the Buyer Agent syncs the creative
    Then <expected>
    # --- creative_scope boundaries ---

    Examples:
      | boundary_point                         | creative_state                                                        | expected                                                          |
      | static creative (no output_format_ids) | a creative with a static format (no output_format_ids)                | the creative should be processed without generative build         |
      | generative with prompt from assets     | a creative with a generative format and prompt in assets              | the system should invoke generative build with the asset prompt   |
      | generative create, name fallback       | a new creative with a generative format and no prompt but a name      | the system should use the creative name as prompt fallback        |
      | generative, no GEMINI_API_KEY          | a creative with a generative format but GEMINI_API_KEY not configured | the error should include "suggestion" field                       |

  @T-UC-006-boundary-creative-scope @boundary @creative-scope
  Scenario Outline: Creative scope boundary — <boundary_point>
    Given the Buyer is authenticated as principal "<principal>"
    And <creative_state>
    When the Buyer Agent syncs the creative
    Then <expected>
    # --- media_buy_status boundaries ---

    Examples:
      | boundary_point                          | principal     | creative_state                                          | expected                                               |
      | all three keys match (update)           | buyer-abc     | creative "C1" already exists for principal "buyer-abc"  | the existing creative should be updated                |
      | new creative_id (create)                | buyer-abc     | creative "C-new" does not exist for this principal      | a new creative should be created                       |
      | same creative_id, different principal    | buyer-xyz     | creative "C1" exists for principal "buyer-abc"          | a new creative should be created for "buyer-xyz"       |

  @T-UC-006-boundary-media-buy @boundary @media-buy-status
  Scenario Outline: Media buy status transition boundary — <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "Banner" and a known format_id
    And an assignment to a package in a media buy with <buy_state>
    When the Buyer Agent syncs the creative
    Then <expected>
    # --- assignment_package boundaries ---

    Examples:
      | boundary_point                          | buy_state                        | expected                                                   |
      | draft + approved_at (transitions)       | status=draft and approved_at set | the media buy should transition to pending_creatives       |
      | draft + no approved_at (stays draft)    | status=draft and no approved_at  | the media buy should remain in draft status                |
      | non-draft status (no transition)        | status=active                    | the media buy status should not change                     |

  @T-UC-006-boundary-assignment-package @boundary @assignment-package
  Scenario Outline: Assignment package boundary — <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "Banner" and a known format_id
    And <assignment_state>
    When the Buyer Agent syncs the creative
    Then <expected>
    # --- assignment_format boundaries ---

    Examples:
      | boundary_point                    | assignment_state                                       | expected                                             |
      | existing package                  | an assignment to a package that exists in the tenant   | the assignment should be created successfully        |
      | existing assignment (idempotent)  | an assignment that already exists for this creative    | the existing assignment should be updated            |
      | package not found                 | an assignment to a package that does not exist         | the error should include "suggestion" field          |

  @T-UC-006-boundary-assignment-format @boundary @assignment-format
  Scenario Outline: Assignment format compatibility boundary — <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And <assignment_state>
    When the Buyer Agent syncs the creative
    Then <expected>
    # --- authentication boundaries ---

    Examples:
      | boundary_point                              | assignment_state                                                     | expected                                              |
      | format matches (exact)                      | an assignment to a package whose product accepts this format         | the assignment should be created successfully         |
      | format matches after URL normalization      | an assignment to a package whose product format has trailing slash   | the assignment should match after URL normalization   |
      | no product format restrictions              | an assignment to a package whose product has empty format_ids        | the assignment should be created (all formats allowed)|
      | no product_id on package                    | an assignment to a package with no product_id                        | the format check should be skipped entirely           |
      | format mismatch                             | an assignment to a package whose product does not accept this format | the error should include "suggestion" field           |

  @T-UC-006-boundary-principal @boundary @authentication
  Scenario Outline: Authentication boundary — <boundary_point>
    Given <auth_state>
    And a creative with name "Banner" and a known format_id
    When the Buyer Agent syncs the creative
    Then <expected>
    # --- provenance boundaries ---

    Examples:
      | boundary_point        | auth_state                                          | expected                                             |
      | typical principal_id  | the Buyer is authenticated with a valid principal_id | the creative should be processed successfully       |
      | missing (null)        | the Buyer has no authentication credentials             | the request should be rejected with AUTH_REQUIRED    |
      | empty string          | the request has an empty principal_id                | the request should be rejected with AUTH_REQUIRED    |

  @T-UC-006-boundary-provenance @boundary @provenance
  Scenario Outline: Provenance policy boundary — <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    And <provenance_state>
    And <policy_state>
    When the Buyer Agent syncs the creative
    Then <expected>
    # --- assignments_structure boundaries ---

    Examples:
      | boundary_point                                                 | provenance_state                       | policy_state                                               | expected                                          |
      | provenance present + policy requires provenance                | a creative with provenance metadata    | a product with creative_policy.provenance_required = true  | the creative should be processed without warning  |
      | provenance absent + policy requires provenance                 | a creative without provenance metadata | a product with creative_policy.provenance_required = true  | a provenance warning should be generated          |
      | provenance present + no provenance policy                      | a creative with provenance metadata    | no product with provenance_required                        | the creative should be processed without warning  |
      | provenance absent + no provenance policy                       | a creative without provenance metadata | no product with provenance_required                        | the creative should be processed without warning  |
      | provenance absent + creative_policy is null                    | a creative without provenance metadata | a product with creative_policy = null                      | the creative should be processed without warning  |
      | provenance absent + creative_policy exists but provenance_required=false | a creative without provenance metadata | a product with creative_policy.provenance_required = false | the creative should be processed without warning  |

  @T-UC-006-boundary-assignments-structure @boundary @assignments-structure
  Scenario Outline: Assignments structure boundary — <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And <assignment_setup>
    When the Buyer Agent syncs the creative
    Then <expected>
    # --- assignment_weight boundaries ---

    Examples:
      | boundary_point                           | assignment_setup                                                 | expected                                                       |
      | assignments absent                       | no assignments field                                             | no assignment processing should occur                          |
      | empty array []                           | an empty assignments array                                       | the error should be ASSIGNMENTS_EMPTY with suggestion          |
      | single entry (minItems boundary)         | an assignment with creative_id "c1" and package_id "p1"         | the assignment should be created successfully                  |
      | entry missing creative_id                | an assignment entry with only package_id                         | the error should be ASSIGNMENT_CREATIVE_ID_REQUIRED            |
      | entry missing package_id                 | an assignment entry with only creative_id                        | the error should be ASSIGNMENT_PACKAGE_ID_REQUIRED             |
      | entry with weight = 0 (paused)           | an assignment with weight 0                                      | the assignment should be created as paused                     |
      | entry with placement_ids                 | an assignment with placement_ids ["slot_a"]                      | the assignment should include placement targeting              |
      | duplicate (creative_id, package_id) pair | two assignment entries with same creative_id and package_id      | the second should be an idempotent upsert                      |

  @T-UC-006-boundary-assignment-weight @boundary @assignment-weight
  Scenario Outline: Assignment weight boundary — <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And an assignment with package_id "pkg-1" and weight <weight_value>
    When the Buyer Agent syncs the creative
    Then <expected>
    # --- account boundaries ---

    Examples:
      | boundary_point                     | weight_value | expected                                                                     |
      | weight absent (field omitted)      |              | the assignment should use equal rotation                                     |
      | weight = -1 (min - 1)              | -1           | the error should be ASSIGNMENT_WEIGHT_BELOW_MINIMUM with suggestion          |
      | weight = 0 (min, inclusive — paused)| 0            | the assignment should be created as paused (no delivery)                     |
      | weight = 1 (min + 1)               | 1            | the assignment should be created with weight 1                               |
      | weight = 50 (typical)              | 50           | the assignment should be created with weight 50                              |
      | weight = 99 (max - 1)              | 99           | the assignment should be created with weight 99                              |
      | weight = 100 (max, inclusive)       | 100          | the assignment should be created with weight 100                             |
      | weight = 101 (max + 1)             | 101          | the error should be ASSIGNMENT_WEIGHT_ABOVE_MAXIMUM with suggestion          |

  @T-UC-006-boundary-account @boundary @account
  Scenario Outline: Account resolution boundary — <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And account is <account_setup>
    When the Buyer Agent syncs the creative
    Then <expected>
    # --- idempotency_key boundaries ---

    Examples:
      | boundary_point                                  | account_setup                                                               | expected                                                      |
      | account_id present + account exists + active    | {"account_id": "acc_acme_001"}                                             | the request should proceed with resolved account              |
      | account_id present + not found                  | {"account_id": "acc_nonexistent"}                                          | the error should be ACCOUNT_NOT_FOUND with suggestion         |
      | brand + operator present + single match + active | {"brand": {"domain": "acme.com"}, "operator": "acme.com"}                 | the request should proceed with resolved account              |
      | brand + operator present + no match             | {"brand": {"domain": "unknown.com"}, "operator": "unknown.com"}           | the error should be ACCOUNT_NOT_FOUND with suggestion         |
      | brand + operator present + multiple matches     | {"brand": {"domain": "multi.com"}, "operator": "agency.com"}              | the error should be ACCOUNT_AMBIGUOUS with suggestion         |
      | account resolved + setup incomplete             | {"account_id": "acc_new_unconfigured"}                                     | the error should be ACCOUNT_SETUP_REQUIRED with suggestion    |
      | account resolved + payment due                  | {"account_id": "acc_overdue"}                                              | the error should be ACCOUNT_PAYMENT_REQUIRED with suggestion  |
      | account resolved + suspended                    | {"account_id": "acc_suspended"}                                            | the error should be ACCOUNT_SUSPENDED with suggestion         |
      | account field absent                            | not provided                                                                | the error should be INVALID_REQUEST with suggestion           |
      | both account_id and brand/operator present      | {"account_id": "acc_001", "brand": {"domain": "x.com"}, "operator": "x"}  | the error should be INVALID_REQUEST with suggestion           |

  @T-UC-006-boundary-idempotency-key @boundary @idempotency-key
  Scenario Outline: Idempotency key boundary — <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And idempotency_key is <key_value>
    When the Buyer Agent syncs the creative
    Then <expected>

    Examples:
      | boundary_point               | key_value                              | expected                                                      |
      | absent (field not provided)  |                                        | the request should proceed without idempotency check          |
      | empty string (length 0)      | ""                                     | the error should be IDEMPOTENCY_KEY_TOO_SHORT with suggestion |
      | length 7 (min - 1)           | "abc1234"                              | the error should be IDEMPOTENCY_KEY_TOO_SHORT with suggestion |
      | length 8 (min, inclusive)     | "12345678"                             | the request should proceed normally                           |
      | length 9 (min + 1)           | "123456789"                            | the request should proceed normally                           |
      | length 254 (max - 1)         | "a]x254"                               | the request should proceed normally                           |
      | length 255 (max, inclusive)   | "a]x255"                               | the request should proceed normally                           |
      | length 256 (max + 1)         | "a]x256"                               | the error should be IDEMPOTENCY_KEY_TOO_LONG with suggestion  |

  @T-UC-006-sandbox-happy @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account sync creatives produces simulated results with sandbox flag
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And the request targets a sandbox account
    When the Buyer Agent sends a sync_creatives request
    Then the response status should be "completed"
    And the response should include sandbox equals true
    And no real ad platform creative uploads should have been made
    And no real billing records should have been created
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-2: real ad platform calls suppressed
    # BR-RULE-209 INV-3: real billing suppressed
    # BR-RULE-209 INV-4: response includes sandbox: true

  @T-UC-006-sandbox-production @invariant @br-rule-209 @sandbox
  Scenario: Production account sync creatives response does not include sandbox flag
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And the request targets a production account
    When the Buyer Agent sends a sync_creatives request
    Then the response status should be "completed"
    And the response should not include a sandbox field
    # BR-RULE-209 INV-5: production account -> sandbox absent

  @T-UC-006-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account with invalid creative returns real validation error
    Given the Buyer is authenticated with a valid principal_id
    And a creative with an invalid format_id
    And the request targets a sandbox account
    When the Buyer Agent sends a sync_creatives request
    Then the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

