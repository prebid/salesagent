# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

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



  @T-UC-006-main @main-flow
  Scenario: Sync creatives — successful create
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "Summer Banner" and a known format_id
    And the creative does not exist in the Seller's library
    When the Buyer Agent syncs the creative
    Then the response should include the creative with action "created"
    And the creative should have a status reflecting the approval workflow
    # POST-S1: Buyer knows creative was successfully created
    # POST-S2: Buyer knows action = created

  @T-UC-006-main-update @main-flow
  Scenario: Sync creatives — successful update
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "Summer Banner" and a known format_id
    And the creative already exists in the Seller's library for this principal
    When the Buyer Agent syncs the creative
    Then the response should include the creative with action "updated"
    # POST-S1: Buyer knows creative was updated
    # POST-S2: Buyer knows action = updated

  @T-UC-006-main-unchanged @main-flow
  Scenario: Sync creatives — creative unchanged
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "Summer Banner" and a known format_id
    And the creative already exists with identical data
    When the Buyer Agent syncs the creative
    Then the response should include the creative with action "unchanged"
    # POST-S2: Buyer knows action = unchanged

  @T-UC-006-main-assign @main-flow
  Scenario: Sync creatives — with package assignments
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And assignments mapping the creative to valid package_ids
    When the Buyer Agent syncs the creative
    Then the response should include the creative with assignment results
    And the assignment results should list the assigned packages
    # POST-S3: Buyer knows which packages each creative was assigned to

  @T-UC-006-main-warnings @main-flow
  Scenario: Sync creatives — partial success with warnings
    Given the Buyer is authenticated with a valid principal_id
    And two creatives: one valid and one with an empty name
    When the Buyer Agent syncs both creatives
    Then the response should include one creative with action "created"
    And the response should include one creative with action "failed"
    # POST-S4: Buyer knows about per-creative warnings

  @T-UC-006-main-approval @main-flow
  Scenario: Sync creatives — approval workflow routing
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And the tenant has approval_mode set to "require-human"
    When the Buyer Agent syncs the creative
    Then the creative status should be "pending_review"
    And a workflow step should be created for the Seller
    # POST-S5: Creative routed to approval workflow

  @T-UC-006-main-lenient-warnings @main-flow
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

  @T-UC-006-main-provenance-warning @main-flow
  Scenario: Sync creatives — provenance warning when policy requires it
    Given the Buyer is authenticated with a valid principal_id
    And the tenant has a product with creative_policy.provenance_required = true
    And a creative with a known format_id but no provenance metadata
    When the Buyer Agent syncs the creative
    Then the creative should have action "created"
    And the response should include a warning about missing provenance
    And the creative should be flagged for review
    # POST-S4: Buyer knows about provenance warning

  @T-UC-006-main-weight @main-flow
  Scenario: Sync creatives — assignment with explicit weight
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And an assignment with package_id "pkg-1" and weight 50
    When the Buyer Agent syncs the creative
    Then the assignment should be created with the specified weight
    # POST-S3: Buyer knows assignment details including weight

  @T-UC-006-main-async-submitted @main-flow @async
  Scenario: Sync creatives — async submitted task envelope
    Given the Buyer is authenticated with a valid principal_id
    And a batch sync that the Seller cannot confirm within the request window
    When the Buyer Agent syncs the creatives
    Then the response should have status "submitted" with a task_id
    And the response should not include a creatives array
    And the Buyer can poll tasks/get with the task_id to retrieve per-item results
    # POST-S1/S2: per-item results land on the task completion artifact, not this envelope
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/sync-creatives-request.json

  @T-UC-006-main-delete-missing-conflict @main-flow @error
  Scenario: Sync creatives — delete_missing rejected when creative_ids filter provided
    Given the Buyer is authenticated with a valid principal_id
    And a sync request with both creative_ids filter and delete_missing set to true
    When the Buyer Agent syncs the creatives
    Then the operation should fail with INVALID_REQUEST
    And the error code should be "INVALID_REQUEST"
    And the error should explain that delete_missing applies to the entire library scope, not a filtered subset
    # POST-F1/F2/F3: BR-12 — delete_missing + creative_ids are mutually exclusive

  @T-UC-006-ext-a @extension @ext-a @error
  Scenario: Authentication required — missing principal_id
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

  @T-UC-006-ext-a-empty @extension @ext-a @error
  Scenario: Authentication required — empty principal_id
    Given the Buyer has an empty principal_id in the authentication context
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the operation should fail
    And the error code should be "AUTH_REQUIRED"
    And the error should include a "suggestion" field

  @T-UC-006-ext-webhook-ssrf @extension @ext-webhook-ssrf @webhook-ssrf @error @post-f1 @post-f2 @post-f3
  Scenario: Push notification webhook URL targeting a blocked host is rejected
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "Summer Banner" and a known format_id
    And the request includes a push_notification_config with url "http://169.254.169.254/latest/meta-data/"
    When the Buyer Agent syncs the creative
    Then the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # Repo-local SSRF policy (ungraded extension): reuses AdCP 3.1.1
    # VALIDATION_ERROR / recovery=correctable enum values + suggestion on
    # MCP/REST/A2A tool transports. Schema is silent on SSRF. A2A-native
    # push-config endpoints map the same gate to InvalidParamsError with the
    # AdCP VALIDATION_ERROR envelope in data= — unit-pinned, not this scenario.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json (error recovery enum)
    # POST-F1, POST-F2, POST-F3
    # --- ext-b: TENANT_NOT_FOUND ---

  @T-UC-006-ext-b @extension @ext-b @error
  Scenario: Tenant not found — principal has no tenant
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
    # --- ext-c: CREATIVE_VALIDATION_FAILED ---

  @T-UC-006-ext-c @extension @ext-c @error
  Scenario: Creative validation failed — schema violation
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
    # --- ext-d: CREATIVE_NAME_EMPTY ---

  @T-UC-006-ext-d @extension @ext-d @error
  Scenario: Creative name empty
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "" and a known format_id
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_NAME_EMPTY"
    And the error message should contain "name"
    And the error should include a "suggestion" field
    And the suggestion should contain "non-empty name"
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

  @T-UC-006-ext-e @extension @ext-e @error
  Scenario: Creative format required — missing format_id
    Given the Buyer is authenticated with a valid principal_id
    And a creative with name "Banner" but no format_id
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_FORMAT_REQUIRED"
    And the error message should contain "format"
    And the error should include a "suggestion" field
    And the suggestion should contain "format_id"
    # POST-F2, POST-F3
    # --- ext-f: CREATIVE_FORMAT_UNKNOWN ---

  @T-UC-006-ext-f @extension @ext-f @error
  Scenario: Creative format unknown — not in agent registry
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a format_id that does not exist in any agent registry
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_FORMAT_UNKNOWN"
    And the error message should contain "unknown format"
    And the error should include a "suggestion" field
    And the suggestion should contain "list_creative_formats"
    # POST-F2, POST-F3
    # --- ext-g: CREATIVE_AGENT_UNREACHABLE ---

  @T-UC-006-ext-g @extension @ext-g @error
  Scenario: Creative agent unreachable
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a format_id whose agent_url is unreachable
    When the Buyer Agent syncs the creative
    Then the creative should have action "failed"
    And the error code should be "CREATIVE_AGENT_UNREACHABLE"
    And the error message should contain "unreachable"
    And the error should include a "suggestion" field
    And the suggestion should contain "try again"
    # POST-F2, POST-F3
    # --- ext-h: CREATIVE_PREVIEW_FAILED ---

  @T-UC-006-ext-h @extension @ext-h @error
  Scenario: Creative preview failed — no previews generated
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
    # --- ext-i: CREATIVE_GEMINI_KEY_MISSING ---

  @T-UC-006-ext-i @extension @ext-i @error
  Scenario: Gemini key missing — generative creative without config
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
    # --- ext-j: PACKAGE_NOT_FOUND (strict) ---

  @T-UC-006-ext-j @extension @ext-j @error
  Scenario: Package not found — strict mode aborts
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
    # --- ext-k: FORMAT_MISMATCH (strict) ---

  @T-UC-006-ext-k @extension @ext-k @error
  Scenario: Format mismatch — creative format incompatible with product
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

  @T-UC-006-boundary-delete-missing @boundary @delete-missing
  Scenario Outline: delete_missing scope boundary — <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    And a sync request whose scope is <scope_setup>
    When the Buyer Agent syncs the creatives
    Then <expected>
    # --- per-creative advisory status value boundaries (response shape) ---
    # NOTE: sync_creatives is an upsert-WRITE operation; it has NO creative_status
    # request filter (that is a list_creatives / UC-018 retrieval concept). The only
    # status surface here is the advisory CreativeStatus on each per-creative RESULT
    # of the SyncCreativesSuccess shape. These boundaries fix the enum membership of
    # that response value.
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/sync-creatives-request.json

    Examples: Valid scope combinations
      | boundary_point                          | scope_setup                                              | expected                                                       |
      | delete_missing=true, creative_ids absent | delete_missing true and no creative_ids filter           | the request should proceed as a full-library replace           |
      | delete_missing=false, creative_ids absent | delete_missing false and no creative_ids filter         | the request should proceed and leave absent creatives unchanged |
      | delete_missing omitted                  | neither delete_missing nor creative_ids provided         | the request should proceed and leave absent creatives unchanged |
      | creative_ids present, delete_missing absent | a creative_ids filter and no delete_missing flag      | the request should proceed scoped to the filtered subset       |

    Examples: Invalid scope combination
      | boundary_point                          | scope_setup                                              | expected                                            |
      | delete_missing=true AND creative_ids present | delete_missing true together with a creative_ids filter | the error should be INVALID_REQUEST with suggestion |

  @T-UC-006-boundary-creative-status @boundary @creative-status @response-shape @v3-1
  Scenario Outline: Per-creative advisory status value boundary — <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    And a creative whose sync resolves to a non-terminal per-creative action "<action>" carrying advisory status <status_value>
    When the Buyer Agent syncs the creative
    Then <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/sync-creatives-request.json

    Examples: Valid CreativeStatus members
      | boundary_point                          | action    | status_value     | expected                                                           |
      | processing (first enum member)          | created   | "processing"     | the per-creative result should carry advisory status "processing"  |
      | archived (last enum member)             | updated   | "archived"       | the per-creative result should carry advisory status "archived"    |
      | approved (review-lifecycle member)      | unchanged | "approved"       | the per-creative result should carry advisory status "approved"    |

    Examples: Invalid status value (not a CreativeStatus member)
      | boundary_point                              | action  | status_value | expected                                                        |
      | deleted (a CreativeAction, not a status)    | created | "deleted"    | the per-creative result should be rejected as schema-invalid    |

  @T-UC-006-boundary-creative-status-response @boundary @creative-status @response-shape @v3-1
  Scenario Outline: Per-creative status omission on terminal action — <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    And a creative whose sync resolves to <result_shape>
    When the Buyer Agent syncs the creative
    Then <expected>

    Examples: Valid response shape
      | boundary_point                                                 | result_shape                            | expected                                                            |
      | action='deleted' with status omitted (sync-creatives-response) | per-creative action "deleted" with no status field | the per-creative result should be accepted with the status omitted |

    Examples: Invalid response shape
      | boundary_point                                                           | result_shape                                       | expected                                                |
      | action='failed' with status='rejected' present (sync-creatives-response) | per-creative action "failed" carrying status "rejected" | the per-creative result shape should be rejected as schema-invalid |

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

  @T-UC-006-sandbox-submitted-no-flag @invariant @br-rule-209 @sandbox @async @v3-1
  Scenario: Sandbox account async submitted sync_creatives envelope omits the sandbox flag
    Given the Buyer is authenticated with a valid principal_id
    And the request targets a sandbox account
    And a batch sync that the Seller cannot confirm within the request window
    When the Buyer Agent sends a sync_creatives request
    Then the response should have status "submitted" with a task_id
    And the response should not include a sandbox field
    # BR-RULE-209 INV-11: sandbox permitted only on the synchronous success shape;
    # forbidden on the async submitted envelope (no sandbox property) — a queued
    # sandbox sync has produced no simulated result yet to flag
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/sync-creatives-request.json

  @T-UC-006-sandbox-errors-no-flag @invariant @br-rule-209 @sandbox @error @v3-1
  Scenario: Sandbox account terminal-failure sync_creatives response omits the sandbox flag
    Given the Buyer is authenticated with a valid principal_id
    And the request targets a sandbox account
    And a sync request that fails operation-level validation
    When the Buyer Agent sends a sync_creatives request
    Then the response should indicate a validation error
    And the response should not include a sandbox field
    And the error should include a "suggestion" field
    # BR-RULE-209 INV-11: sandbox forbidden on the terminal-failure errors shape
    # (not.anyOf required:[sandbox]) — the failure carries the real error only
    # POST-F3: suggestion field present even on the sandbox error shape
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/sync-creatives-request.json

  @T-UC-006-boundary-sandbox @boundary @sandbox @v3-1
  Scenario Outline: Sandbox flag response-shape boundary — <boundary_point>
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And <account_kind>
    When the Buyer Agent sends a <response_shape>
    Then <expected>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/sync-creatives-request.json

    Examples: Synchronous success shape (sandbox permitted)
      | boundary_point                                            | account_kind                                       | response_shape                                    | expected                                                |
      | sandbox: true in response (sandbox account)               | the request targets a sandbox account              | sync_creatives request that completes synchronously | the response should include sandbox equals true       |
      | sandbox: true on sync_creatives synchronous success shape | the request targets a sandbox account              | sync_creatives request that completes synchronously | the response should include sandbox equals true       |
      | sandbox absent in response (production account)           | the request targets a production account           | sync_creatives request that completes synchronously | the response should not include a sandbox field       |
      | sandbox: false in response (explicit production)          | the request targets an explicit production account | sync_creatives request that completes synchronously | the response sandbox field, if present, should be false |

    Examples: Non-success shapes (sandbox forbidden)
      | boundary_point                                                    | account_kind                          | response_shape                                       | expected                                        |
      | sandbox present on sync_creatives submitted task envelope         | the request targets a sandbox account | sync_creatives request that is queued as submitted   | the response should not include a sandbox field |
      | sandbox present on sync_creatives terminal-failure (errors) shape | the request targets a sandbox account | sync_creatives request that fails operation validation | the response should not include a sandbox field |

  @T-UC-006-partition-creative-status-terminal @partition @creative-status @v3-1
  Scenario Outline: Per-creative result omits advisory status on a terminal action — <partition>
    Given the Buyer is authenticated with a valid principal_id
    And a creative whose sync resolves to per-creative action "<action>"
    When the Buyer Agent syncs the creative
    Then the per-creative result should report action "<action>"
    And the per-creative result should omit the status field
    # creative_status (v3.1): status is advisory review-lifecycle (not a spend gate)
    # and MUST be omitted when per-creative action ∈ {failed, deleted}
    # (schema allOf if/then). BR-RULE-037 governs status/approval routing.

    Examples: Terminal actions
      | partition                     | action  |
      | omitted_on_failed_or_deleted  | failed  |
      | omitted_on_failed_or_deleted  | deleted |

  @T-UC-006-creative-item-multi-asset @v3-1 @creative-item
  Scenario: Sync creative with multi-asset composition (carousel) succeeds
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id that requires a multi-asset composition
    And the creative's assets include CreativeItems with asset_kind "media" and asset_kind "text"
    And each CreativeItem carries asset_type, asset_id, and the discriminator-required content field
    When the Buyer Agent syncs the creative
    Then the response should include the creative with action "created"
    And every CreativeItem should be persisted under the parent creative
    # POST-S1: multi-asset composite sync succeeds
    # POST-S2: action = created
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/sync-creatives-request.json

  @T-UC-006-creative-item-text-array @v3-1 @creative-item
  Scenario: CreativeItem text content accepts array for A/B variants
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And a CreativeItem with asset_kind "text" whose content is an array of strings
    When the Buyer Agent syncs the creative
    Then the response should include the creative with action "created"
    And all text variants should be retained on the CreativeItem
    # POST-S2: array-shaped text content preserved (A/B variant support)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/sync-creatives-request.json

  @T-UC-006-creative-item-missing-content @v3-1 @creative-item @ext-c
  Scenario: CreativeItem missing discriminator-required field is rejected
    Given the Buyer is authenticated with a valid principal_id
    And a creative whose assets contain a CreativeItem with asset_kind "media" but no content_uri
    When the Buyer Agent syncs the creative
    Then the per-creative result should report action "failed"
    And the error should be a schema validation error
    And the error should identify the missing content_uri field on the CreativeItem
    And the error should include a "suggestion" field
    # POST-F2: discriminator violation surfaced
    # POST-F3: field path points the buyer at the offending CreativeItem

  @T-UC-006-creative-variable-declared @v3-1 @creative-variable @dco
  Scenario: Sync creative that declares DCO variables persists every variable slot
    Given the Buyer is authenticated with a valid principal_id
    And a creative with a known format_id
    And the creative declares CreativeVariables with variable_id, name, and variable_type set
    When the Buyer Agent syncs the creative
    Then the response should include the creative with action "created"
    And every declared CreativeVariable should be persisted on the creative
    # POST-S1: DCO-aware creative sync succeeds
    # POST-S2: variables retained for serve-time substitution
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/sync-creatives-request.json

  @T-UC-006-creative-variable-required-flag @v3-1 @creative-variable @dco
  Scenario: CreativeVariable required flag is preserved on persisted creative
    Given the Buyer is authenticated with a valid principal_id
    And a creative that declares a CreativeVariable with required true and a default_value
    When the Buyer Agent syncs the creative
    Then the response should include the creative with action "created"
    And the persisted CreativeVariable should retain its required flag and default_value
    # POST-S2: serve-time semantics (required, default_value) preserved
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/sync-creatives-request.json

  @T-UC-006-creative-variable-invalid-type @v3-1 @creative-variable @dco @ext-c
  Scenario: CreativeVariable with unsupported variable_type is rejected
    Given the Buyer is authenticated with a valid principal_id
    And a creative that declares a CreativeVariable whose variable_type is not in the v3.1 enum
    When the Buyer Agent syncs the creative
    Then the per-creative result should report action "failed"
    And the error should be a schema validation error
    And the error should identify the offending variable_type value
    And the error should include a "suggestion" field
    # POST-F2: enum violation surfaced
    # POST-F3: field path points at variable_type

  @T-UC-006-vast-tracker-asset @v3-1 @vast-tracker
  Scenario: Sync video creative with decomposed VAST trackers succeeds
    Given the Buyer is authenticated with a valid principal_id
    And a video creative with a known format_id
    And the creative's assets include a VAST tracker with vast_event "start" and a tracker URL
    And the creative's assets include a VAST tracker with vast_event "complete" and a tracker URL
    When the Buyer Agent syncs the creative
    Then the response should include the creative with action "created"
    And every VAST tracker asset should be persisted with its vast_event and url
    # POST-S1: decomposed VAST trackers accepted
    # POST-S2: trackers retained for serve-time TrackingEvents assembly
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/sync-creatives-request.json

  @T-UC-006-vast-tracker-progress-requires-offset @v3-1 @vast-tracker @ext-c
  Scenario: VAST tracker with vast_event "progress" without offset is rejected
    Given the Buyer is authenticated with a valid principal_id
    And a video creative with a known format_id
    And a VAST tracker asset with vast_event "progress" but no offset field
    When the Buyer Agent syncs the creative
    Then the per-creative result should report action "failed"
    And the error should be a schema validation error
    And the error should identify the missing offset field
    And the error should include a "suggestion" field
    # POST-F2: conditional-required violation surfaced
    # POST-F3: field path points at offset
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/sync-creatives-request.json

  @T-UC-006-vast-tracker-forbidden-event @v3-1 @vast-tracker @ext-c
  Scenario: VAST tracker with forbidden vast_event "impression" is rejected
    Given the Buyer is authenticated with a valid principal_id
    And a video creative with a known format_id
    And a VAST tracker asset whose vast_event is "impression"
    When the Buyer Agent syncs the creative
    Then the per-creative result should report action "failed"
    And the error should be a schema validation error
    And the error should explain that impression URLs belong on a url asset with url_type "tracker_pixel"
    And the error should include a "suggestion" field
    # POST-F2: VAST modeling rule enforced (impression -> url asset, not vast_tracker)
    # POST-F3: suggestion points buyer at the correct asset type
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/sync-creatives-request.json

  @T-UC-006-daast-tracker-asset @v3-1 @daast-tracker
  Scenario: Sync audio creative with decomposed DAAST trackers succeeds
    Given the Buyer is authenticated with a valid principal_id
    And an audio creative with a known format_id
    And the creative's assets include a DAAST tracker with daast_event "start" and a tracker URL
    And the creative's assets include a DAAST tracker with daast_event "complete" and a tracker URL
    When the Buyer Agent syncs the creative
    Then the response should include the creative with action "created"
    And every DAAST tracker asset should be persisted with its daast_event and url
    # POST-S1: decomposed DAAST trackers accepted
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/sync-creatives-request.json

  @T-UC-006-daast-tracker-no-non-linear-target @v3-1 @daast-tracker @ext-c
  Scenario: DAAST tracker with target "non_linear" is rejected
    Given the Buyer is authenticated with a valid principal_id
    And an audio creative with a known format_id
    And a DAAST tracker asset whose target is "non_linear"
    When the Buyer Agent syncs the creative
    Then the per-creative result should report action "failed"
    And the error should be a schema validation error
    And the error should explain that DAAST has no non_linear element
    And the error should include a "suggestion" field
    # POST-F2: DAAST target enum (linear|companion) enforced
    # POST-F3: suggestion points buyer at the valid DAAST target set

  @T-UC-006-error-details-conflict @v3-1 @error-details @conflict
  Scenario: CONFLICT error returns version details so buyer can re-read and retry
    Given the Buyer is authenticated with a valid principal_id
    And a creative whose creative_id collides with a concurrently-updated server-side creative
    When the Buyer Agent syncs the creative
    Then the per-creative result should report action "failed"
    And the error code should be "CONFLICT"
    And the error details should include resource_id, expected_version, and current_version
    And the error should include a suggestion to re-read the resource and retry
    # POST-F2: machine-readable version info returned
    # POST-F3: recovery path is explicit (re-read + retry)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/sync-creatives-request.json

  @T-UC-006-error-details-policy-violation @v3-1 @error-details @policy-violation
  Scenario: POLICY_VIOLATION error returns policy reference and violated rules
    Given the Buyer is authenticated with a valid principal_id
    And a creative whose content breaches a referenced governance policy
    When the Buyer Agent syncs the creative
    Then the per-creative result should report action "failed"
    And the error code should be "POLICY_VIOLATION"
    And the error details should include policy_id and a non-empty violated_rules array
    And the error details should include a policy_url where the full policy can be reviewed
    # POST-F2: policy reference is structured, not free text
    # POST-F3: buyer can fetch policy text and revise
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/creative/sync-creatives-request.json

  @T-UC-006-error-details-creative-rejected @v3-1 @error-details @creative-rejected
  Scenario: CREATIVE_REJECTED error returns policy reference and rejection reasons
    Given the Buyer is authenticated with a valid principal_id
    And a creative that is rejected by the Seller's review workflow
    When the Buyer Agent syncs the creative
    Then the per-creative result should report action "failed"
    And the error code should be "CREATIVE_REJECTED"
    And the error details should include policy_id and a non-empty reasons array
    And the error details should include a policy_url where the full policy can be reviewed
    # POST-F2: rejection rationale is structured
    # POST-F3: buyer knows what to revise

  @T-UC-006-storyboard-provenance-required-rejection @storyboard-v3.1 @v3-1 @provenance @rejection
  Scenario: PROVENANCE_REQUIRED -- provenance object absent on creative under a policy that requires it
    Given the tenant has a product with creative_policy.provenance_required = true
    And the Buyer Agent submits a creative whose manifest carries no provenance object at all
    When the Buyer Agent sends sync_creatives
    Then the response envelope should be schema-valid against sync-creatives-response.json
    And the per-creative result should report action "failed"
    And the per-creative errors[0].code should be "PROVENANCE_REQUIRED"
    # provenance_enforcement Phase 2: cheapest buyer mistake -- no provenance attached.
    # Seller accepts envelope but per-creative action=failed with PROVENANCE_REQUIRED.
    # provenance_enforcement: provenance entirely absent under provenance_required policy
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/provenance_enforcement.yaml

  @T-UC-006-storyboard-provenance-digital-source-type-missing @storyboard-v3.1 @v3-1 @provenance @rejection
  Scenario: PROVENANCE_DIGITAL_SOURCE_TYPE_MISSING -- provenance present but digital_source_type omitted
    Given the tenant has a product with creative_policy.provenance_requirements.require_digital_source_type = true
    And the Buyer Agent submits a creative whose provenance object omits digital_source_type
    When the Buyer Agent sends sync_creatives
    Then the per-creative result should report action "failed"
    And the per-creative errors[0].code should be "PROVENANCE_DIGITAL_SOURCE_TYPE_MISSING"
    # provenance_enforcement Phase 3: provenance attached but missing digital_source_type
    # under a policy with require_digital_source_type=true. Distinct from
    # PROVENANCE_REQUIRED because provenance IS present.
    # provenance_enforcement: digital_source_type missing under require_digital_source_type policy
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/provenance_enforcement.yaml

  @T-UC-006-storyboard-provenance-disclosure-missing @storyboard-v3.1 @v3-1 @provenance @rejection
  Scenario: PROVENANCE_DISCLOSURE_MISSING -- provenance present but disclosure block omitted under require_disclosure_metadata
    Given the tenant has a product with creative_policy.provenance_requirements.require_disclosure_metadata = true
    And the Buyer Agent submits a creative whose provenance object lacks a disclosure block
    When the Buyer Agent sends sync_creatives
    Then the per-creative result should report action "failed"
    And the per-creative errors[0].code should be "PROVENANCE_DISCLOSURE_MISSING"
    # provenance_enforcement Phase 5: structural disclosure check. Seller inspects the
    # submitted manifest against creative_policy.provenance_requirements.require_disclosure_metadata
    # without calling any verifier. error.field points at the missing disclosure path.
    # provenance_enforcement: disclosure block missing under require_disclosure_metadata policy
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/provenance_enforcement.yaml

  @T-UC-006-storyboard-provenance-corrected-acceptance @storyboard-v3.1 @v3-1 @provenance @acceptance
  Scenario: Corrected resubmission with disclosure block and on-list verifier is accepted
    Given a creative submission that previously failed with provenance rejection codes
    And the Buyer Agent resubmits with a complete disclosure block and an on-list verify_agent from the seller's accepted_verifiers
    When the Buyer Agent sends sync_creatives with the corrected manifest
    Then the per-creative result should report action "created" or "updated"
    And the per-creative result should NOT report action "failed"
    # provenance_enforcement Phase 6: the structural-rejection contract terminates in a
    # corrected acceptance. Buyer reads the rejection error codes from prior phases,
    # attaches a complete disclosure block, and represents an on-list verify_agent
    # drawn from creative_policy.accepted_verifiers. Per-creative action transitions
    # to created/updated (not failed); the creative enters the seller's review lifecycle.
    # provenance_enforcement: corrected resubmission terminates the rejection cascade
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/provenance_truth_of_claim.yaml

  @T-UC-006-storyboard-provenance-claim-contradicted @storyboard-v3.1 @v3-1 @provenance @rejection @truth-of-claim
  Scenario: PROVENANCE_CLAIM_CONTRADICTED -- on-list verifier refutes buyer's digital_source_type claim
    Given the Buyer Agent submits a creative claiming digital_source_type "digital_capture"
    And the on-list verifier responds with ai_generated true at confidence at least 0.9
    When the seller invokes the verifier against the creative manifest
    Then the per-creative result should report action "failed"
    And the per-creative errors[0].code should be "PROVENANCE_CLAIM_CONTRADICTED"
    And the error details should include agent_url, feature_id, claimed_value, observed_value, and confidence
    And the error details should NOT carry detail_url or verifier extension fields
    # provenance_truth_of_claim: buyer claims digital_source_type=digital_capture (non-AI)
    # but the asset URL drives the seller's on-list verifier to return ai_generated:true.
    # Seller invokes the verifier (via get_creative_features on accepted_verifiers entry),
    # observes the contradiction, and rejects with PROVENANCE_CLAIM_CONTRADICTED.
    # error.details carries only the audit-safe allowlist (agent_url, feature_id,
    # claimed_value, observed_value, confidence) -- no detail_url, no verifier extension
    # fields. This is the cross-tenant trust boundary.
    # provenance_truth_of_claim: verifier contradicts buyer claim; details bounded to audit-safe allowlist
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/creative/index.yaml

  @T-UC-006-storyboard-multi-format-sync @storyboard-v3.1 @v3-1 @bulk-sync @multi-format
  Scenario: Bulk sync of three creatives in three different formats returns per-creative action and status
    Given the Buyer Agent submits three creatives in three different formats in a single sync_creatives call
    When the Buyer Agent sends sync_creatives
    Then the response envelope should be schema-valid against sync-creatives-response.json
    And the creatives array should carry one result per submitted creative
    And every per-creative result should expose action and status fields
    And every action value should be "created", "updated", or "failed"
    And every status value should be drawn from the creative-status enum
    # creative/index.yaml sync_multiple: a single sync_creatives call carries three
    # creatives (display 300x250, video 30s, native_content). The seller validates each
    # against its format spec independently and returns per-creative action plus status.
    # Per-creative status is from creative-status (approved, pending_review, rejected).
    # sync_multiple: bulk multi-format validation returns per-creative action+status
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/creative/index.yaml

  @T-UC-006-storyboard-format-id-roundtrip-on-sync @storyboard-v3.1 @v3-1 @format-id-roundtrip
  Scenario: Sync creative with the same format_id object returned by get_products -- seller MUST accept its own format_id
    Given the Buyer Agent captured a format_id {agent_url, id} from a prior get_products response
    When the Buyer Agent sends sync_creatives carrying a creative whose format_id matches the captured object
    Then the per-creative result should NOT report action "failed" due to format_id rejection
    And the seller's own format_id object should roundtrip through sync_creatives without modification
    # media-buy/index.yaml creative_sync (format_id roundtrip): the buyer submits a
    # creative whose format_id is the EXACT object returned by get_products
    # (products[0].format_ids[0]). If the seller's validation rejects a format_id
    # that it returned in products, its catalog does not roundtrip and a buy
    # would silently fail at sync_creatives after commit.
    # format_id roundtrip: seller MUST accept its own format_ids on sync_creatives
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/compliance/source/protocols/media-buy/scenarios/creative_reception.yaml

  @T-UC-006-storyboard-creative-reception-stateful-render @storyboard-v3.1 @v3-1 @stateful-push @creative-reception
  Scenario: Stateful sales agent accepts pushed creatives and exposes them via per-creative status transitions
    Given the Buyer Agent pushes creative assets to a stateful sales agent
    When the Buyer Agent sends sync_creatives
    Then the seller should validate the creatives against its format specifications
    And the per-creative result should carry a status drawn from creative-status enum
    And the per-creative status may be "approved", "pending_review", or "rejected"
    And platform-assigned IDs should be returned when applicable
    # creative_reception storyboard: a sales agent (publisher, retail media network)
    # accepts pushed creative assets, validates them against format specs, stores them,
    # and exposes per-creative status (approved, pending_review, rejected). Distinct
    # from creative-platform (Innovid/Flashtalking) which carries a full lifecycle;
    # the sales-agent reception is the minimum viable creative-handling contract.
    # creative_reception: stateful sales agent minimum-viable creative reception contract
