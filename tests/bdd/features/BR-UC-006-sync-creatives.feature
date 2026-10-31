# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)

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
    And a tenant is resolvable from the request context



  @T-UC-006-main @main-flow
  Scenario: Sync creatives — successful create
    Given the Buyer is authenticated
    And a creative with name "Summer Banner" and a known format_id
    And the creative does not exist in the Seller's library
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response should include the creative with action "created"
    And the creative should have a status reflecting the approval workflow
    # POST-S1: Buyer knows creative was successfully created
    # POST-S2: Buyer knows action = created

  @T-UC-006-main-update @main-flow
  Scenario: Sync creatives — successful update
    Given the Buyer is authenticated
    And a creative with name "Summer Banner" and a known format_id
    And the creative already exists in the Seller's library for this principal
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response should include the creative with action "updated"
    # POST-S1: Buyer knows creative was updated
    # POST-S2: Buyer knows action = updated

  @T-UC-006-main-unchanged @main-flow
  Scenario: Sync creatives — creative unchanged
    Given the Buyer is authenticated
    And a creative with name "Summer Banner" and a known format_id
    And the creative already exists with identical data
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response should include the creative with action "unchanged"
    # POST-S2: Buyer knows action = unchanged

  # core/format-id.json -- byte-identical at the 3.1 and 3.2 pins -- makes this a MUST:
  # "Callers comparing two `format-id` values MUST canonicalize `agent_url` per the AdCP
  # URL canonicalization rules before treating two formats as the same."
  # So a buyer that respells the SAME agent is not changing the format, and POST-S2 owes
  # "unchanged". Each row below is one canonicalization axis the algorithm normalizes:
  # step 1 (scheme case), step 2 (host case), step 3 (userinfo stripped).
  # The spelling is varied ACROSS the two syncs, not within one request: the stored value
  # and the resent value have to disagree for the comparison to be exercised at all.
  # The agent is the one the Seller already knows, respelled -- not a different host --
  # so a cell that fails is reporting on the comparison and not on agent resolution.
  # Every axis touches only the scheme, the host or the userinfo, never the path or the
  # port. That is deliberate: the agent URL's shape differs by transport (in-process
  # serves a bare authority, e2e_rest one with a port AND a path), so an axis that moved
  # the path or the port would name a DIFFERENT agent on one of them. Two such axes --
  # appending "/" and appending ":443" -- shipped here and failed over e2e_rest for
  # exactly that reason: production was right and the rows were wrong.
  @T-UC-006-unchanged-respelled @main-flow
  Scenario Outline: Sync creatives — respelling agent_url is not a format change (<axis>)
    Given the Buyer is authenticated
    And a creative with name "Summer Banner" and a known format_id
    And the creative already exists with identical data
    And the resync respells the same agent_url by <axis>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response should include the creative with action "unchanged"

    Examples:
      | axis                     |
      | upper-casing the scheme  |
      | upper-casing the host    |
      | adding ignored userinfo  |

  @T-UC-006-main-assign @main-flow
  Scenario: Sync creatives — with package assignments
    Given the Buyer is authenticated
    And a creative with a known format_id
    And assignments mapping the creative to valid package_ids
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response should include the creative with assignment results
    And the assignment results should list the assigned packages
    # POST-S3: Buyer knows which packages each creative was assigned to

  @T-UC-006-main-warnings @main-flow
  Scenario: Sync creatives — partial success with warnings
    Given the Buyer is authenticated
    And two creatives: one valid and one with an empty name
    When the Buyer Agent syncs both creatives
    Then the response is compliant with the sync_creatives success spec
    And the response should include one creative with action "created"
    And the response should include one creative with action "failed"
    # POST-S4: Buyer knows about per-creative warnings

  @T-UC-006-main-approval @main-flow
  Scenario: Sync creatives — approval workflow routing
    Given the Buyer is authenticated
    And a creative with a known format_id
    And the tenant has approval_mode "require-human"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative status should be "pending_review"
    And a workflow step should be created for the Seller
    # POST-S5: Creative routed to approval workflow

  @T-UC-006-main-lenient-warnings @main-flow
  Scenario: Sync creatives — lenient mode with mixed assignment results
    Given the Buyer is authenticated
    And a creative with a known format_id
    And assignments to three packages: two valid, one non-existent
    And validation_mode is "lenient"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative should have action "created"
    And two assignments should be created successfully
    And the response should include assignment_errors for the non-existent package
    # POST-S3: Buyer knows successful assignments
    # POST-S4: Buyer knows about assignment errors

  # main-provenance-warning (POST-S4) said a creative missing provenance under a policy
  # that requires it is created with a warning. The pin says otherwise: enums/error-code.json
  # defines PROVENANCE_REQUIRED for exactly that submission and core/creative-policy.json
  # makes the refusal a MUST, so the definition is
  # @T-UC-006-storyboard-provenance-required-rejection and the "provenance absent + policy
  # requires provenance" rows of the provenance boundary and partition outlines.

  # main-weight (assignment with an explicit weight, POST-S3) is the "weight = 50" row of
  # @T-UC-006-boundary-assignment-weight, which is the one definition of assignments[].weight.

  # main-async-submitted (the submitted task envelope) is not graded: sync-creatives-
  # response.json makes it one of three shapes a seller MAY answer with, and this seller
  # processes every sync synchronously -- the shape is never produced, so no request can
  # drive it. See the note on @T-UC-006-boundary-sandbox.

  @T-UC-006-main-delete-missing-conflict @main-flow @error
  Scenario: Sync creatives — delete_missing rejected when creative_ids filter provided
    Given the Buyer is authenticated
    And a sync request with both creative_ids filter and delete_missing set to true
    When the Buyer Agent syncs the creatives
    Then the response is compliant with the sync_creatives error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the response error field is delete_missing
    # POST-F1/F2/F3: BR-12 — delete_missing + creative_ids are mutually exclusive

  @T-UC-006-ext-a @extension @ext-a @error
  Scenario: Authentication required — missing principal_id
    Given the Buyer has no authentication credentials
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives error spec
    And the operation should fail
    And the error code should be "AUTH_MISSING"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains missing authentication
    # POST-F3: Suggestion for recovery

  @T-UC-006-ext-a-empty @extension @ext-a @error
  Scenario: Authentication required — empty principal_id
    Given the request has an empty principal_id
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives error spec
    And the operation should fail
    And the error code should be "AUTH_MISSING"
    And the error should include a "suggestion" field

  @T-UC-006-ext-webhook-ssrf @extension @ext-webhook-ssrf @webhook-ssrf @error @post-f1 @post-f2 @post-f3
  Scenario: Push notification webhook URL targeting a blocked host is rejected
    Given the Buyer is authenticated
    And a creative with name "Summer Banner" and a known format_id
    And the request includes a push_notification_config with url "http://169.254.169.254/latest/meta-data/"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives error spec
    And the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # NOT a repo-local extension: the pinned spec designates VALIDATION_ERROR for this
    # exact vector. dist/docs/3.1.0/learning/specialist/security.mdx:84 registers
    # https://169.254.169.254/latest/meta-data/ and expects the agent to "refuse it
    # synchronously with a VALIDATION_ERROR on notification_configs[].url". A refused
    # schema-valid URL is a business rule beyond schema, which is what the published
    # VALIDATION_ERROR enumDescription covers; the buyer discriminates on error.field.
    # Suggestion on
    # MCP/REST/A2A tool transports. Schema is silent on SSRF. A2A-native
    # push-config endpoints map the same gate to InvalidParamsError with the
    # AdCP VALIDATION_ERROR envelope in data= — unit-pinned, not this scenario.
    # recovery=correctable comes from error-code.json's enumMetadata.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/INVALID_REQUEST
    # POST-F1, POST-F2, POST-F3
    # --- ext-b: AUTH_INVALID ---

  # ext-b ("Tenant not found — principal has no tenant") is DELETED. A principal
  # row carries its tenant as a foreign key, so a credential that resolves to a
  # principal always resolves to that principal's tenant; the state the scenario
  # named cannot be reached from any wire, and the only way its Given could express
  # it was a hand-built identity naming a tenant that does not exist -- which the
  # three transports then answered three different ways (AUTH_MISSING, AUTH_MISSING,
  # ACCOUNT_NOT_FOUND), none of them the AUTH_INVALID the scenario asserted, because
  # none of them was ever the seller answering a real request. A missing or invalid
  # credential is graded by the authentication partition and boundary outlines below.
    # --- ext-c: INVALID_REQUEST ---

  @T-UC-006-ext-c @extension @ext-c @error
  # A schema violation is refused for the WHOLE request: enums/error-code.json puts
  # "malformed, missing required fields, or violates schema constraints" under
  # INVALID_REQUEST, and the transports validate the request before any creative is
  # processed. This used to expect a per-item action "failed" inside a success
  # response, which no transport can produce for a payload the schema rejects.
  Scenario: Creative validation failed — schema violation
    Given the Buyer is authenticated
    And a creative with invalid schema structure
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives error spec
    And the error code should be "INVALID_REQUEST"
    And the error should include a "suggestion" field
    # POST-F2: Error explains validation failure
    # POST-F3: Suggestion for corrective action
    # --- ext-d: INVALID_REQUEST ---

  @T-UC-006-ext-d @extension @ext-d @error
  # core/creative-asset.json requires ``name`` but sets no minLength, so an empty
  # string is a request the schema ADMITS; refusing it is the seller's own rule, which
  # enums/error-code.json codes VALIDATION_ERROR ("business rules beyond schema
  # validation"), on the creative's own entry. INVALID_REQUEST would claim a schema
  # violation that is not there.
  Scenario: Creative name empty
    Given the Buyer is authenticated
    And a creative with name "" and a known format_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative should have action "failed"
    And the creatives entry carries error code "VALIDATION_ERROR"
    And the error should include a "suggestion" field
    # POST-F2, POST-F3

  @T-UC-006-ext-d-whitespace @extension @ext-d @error
  Scenario: Creative name whitespace-only
    Given the Buyer is authenticated
    And a creative with name "   " and a known format_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative should have action "failed"
    And the creatives entry carries error code "VALIDATION_ERROR"
    And the error should include a "suggestion" field
    # POST-F2, POST-F3
    # --- ext-e: INVALID_REQUEST ---

  @T-UC-006-ext-e @extension @ext-e @error
  # core/creative-asset.json's root oneOf requires exactly one of format_id (the
  # deprecated arm) and format_kind (the canonical arm). A creative naming neither -- here
  # format_id null and no format_kind -- matches no arm, a schema violation, so the whole
  # request is refused with INVALID_REQUEST at the transport, before any creative is
  # processed -- not a per-item failure. format_id alone is not in /required.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/creative-asset.json pointer=/oneOf
  Scenario: Creative format required — missing format_id
    Given the Buyer is authenticated
    And a creative with name "Banner" but no format_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives error spec
    And the error code should be "INVALID_REQUEST"
    And the error should include a "suggestion" field
    # POST-F2, POST-F3
    # --- ext-f: REFERENCE_NOT_FOUND ---

  @T-UC-006-ext-f @extension @ext-f @error
  # A well-formed format_id that no agent serves is a reference that does not resolve:
  # enums/error-code.json routes it to REFERENCE_NOT_FOUND ("Generic fallback for a
  # referenced identifier ... that does not exist ... Use when no resource-specific
  # not-found code applies"). It fails the creative, not the request.
  Scenario: Creative format unknown — not in agent registry
    Given the Buyer is authenticated
    And a creative with a format_id that does not exist in any agent registry
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative should have action "failed"
    And the creatives entry carries error code "REFERENCE_NOT_FOUND"
    And the error should include a "suggestion" field
    # POST-F2, POST-F3
    # --- ext-g: AGENT_UNREACHABLE ---

  @T-UC-006-ext-g @extension @ext-g @error
  # AGENT_UNREACHABLE is not in the pinned enum. What the enum has for "a service the
  # seller depends on did not answer" is SERVICE_UNAVAILABLE, recovery transient, and
  # the egress seam types every undelivered request to the creative agent that way
  # (OutboundDeliveryFailed IS an AdCPServiceUnavailableError), so the refusal reaches
  # the buyer as the request's answer with a retry hint rather than as a per-item
  # failure that reads terminal.
  Scenario: Creative agent unreachable
    Given the Buyer is authenticated
    And a creative with a format_id whose agent_url is unreachable
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives error spec
    And the error code should be "SERVICE_UNAVAILABLE"
    And the error recovery should be "transient"
    # POST-F2, POST-F3
    # --- ext-h: INVALID_REQUEST ---

  # ext-h ("Creative preview failed — no previews generated") is DELETED. The only
  # way to reach the per-item failure it described is a static creative with no
  # media url, and core/creative-asset.json makes the asset's url required -- so that
  # payload is refused as INVALID_REQUEST at the transport before any creative is
  # processed. With a valid url, an agent that returns no previews is a warning on a
  # created creative, not a failure. The scenario graded a branch no wire reaches.
    # POST-F2, POST-F3
    # --- ext-i: CONFIGURATION_ERROR ---

  @T-UC-006-ext-i @extension @ext-i @error
  Scenario: Gemini key missing — generative creative without config
    Given the Buyer is authenticated
    And a creative with a generative format (output_format_ids present)
    And the Seller Agent does not have GEMINI_API_KEY configured
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative should have action "failed"
    And the creatives entry carries error code "CONFIGURATION_ERROR"
    And the error should include a "suggestion" field
    # POST-F2, POST-F3
    # --- ext-j: PACKAGE_NOT_FOUND (strict) ---

  @T-UC-006-ext-j @extension @ext-j @error
  Scenario: Package not found — strict mode aborts
    Given the Buyer is authenticated
    And a creative with a known format_id
    And assignments to a non-existent package
    And validation_mode is "strict"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives error spec
    And the operation should fail with an assignment error
    And the error code should be "PACKAGE_NOT_FOUND"
    And the error should include a "suggestion" field
    # POST-F2, POST-F3
    # --- ext-k: VALIDATION_ERROR (strict) ---
    # ext-k (creative format incompatible with the product, strict) is the
    # format_mismatch row of @T-UC-006-partition-assignment-fmt -- one definition of
    # the rule, and the pin reasoning for VALIDATION_ERROR lives there.

  @T-UC-006-rule-033-inv1 @invariant @BR-RULE-033
  Scenario: INV-1 — per-creative failure does not abort other creatives
    Given the Buyer is authenticated
    And two creatives: one valid and one with an empty name
    When the Buyer Agent syncs both creatives
    Then the response is compliant with the sync_creatives success spec
    And the valid creative should have action "created"
    And the invalid creative should have action "failed"
    And the valid creative should not be affected by the invalid one

  @T-UC-006-rule-033-inv2 @invariant @BR-RULE-033 @error
  Scenario: INV-2 — assignment error in strict mode aborts all assignments
    Given the Buyer is authenticated
    And a creative with a known format_id
    And assignments to two packages: one valid and one non-existent
    And validation_mode is "strict"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives error spec
    And the assignment processing should abort with an error
    And no assignments should be created
    And the error should include a "suggestion" field
    # POST-F3: Suggestion for recovery

  @T-UC-006-rule-033-inv3 @invariant @BR-RULE-033
  Scenario: INV-3 — assignment error in lenient mode skips and continues
    Given the Buyer is authenticated
    And a creative with a known format_id
    And assignments to two packages: one valid and one non-existent
    And validation_mode is "lenient"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the valid assignment should be created
    And the non-existent package should be reported as a warning
    And processing should continue normally

  @T-UC-006-rule-033-inv4 @invariant @BR-RULE-033
  Scenario: INV-4 — assignment errors always recorded in response
    Given the Buyer is authenticated
    And a creative with a known format_id
    And assignments to a non-existent package
    And validation_mode is "lenient"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response should include assignment_errors
    And the assignment_errors should contain the package_id

  @T-UC-006-rule-033-inv5 @invariant @BR-RULE-033
  Scenario: INV-5 — default validation_mode is strict
    Given the Buyer is authenticated
    And a creative with a known format_id
    And assignments to a non-existent package
    And validation_mode is not set
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives error spec
    And the assignment processing should abort with an error
    And the behavior should match strict mode
    # --- BR-RULE-034: Cross-Principal Isolation ---

  @T-UC-006-rule-034-inv1 @invariant @BR-RULE-034
  Scenario: INV-1 — creative lookup uses triple key
    Given the Buyer is authenticated as principal "buyer-A"
    And a creative "creative-1" exists for principal "buyer-A" in the tenant
    When the Buyer Agent syncs creative "creative-1"
    Then the response is compliant with the sync_creatives success spec
    And the existing creative should be updated (matched by triple key)

  @T-UC-006-rule-034-inv2 @invariant @BR-RULE-034
  Scenario: INV-2 — cross-principal creative creates new silently
    Given the Buyer is authenticated as principal "buyer-B"
    And a creative "creative-1" exists for principal "buyer-A" in the same tenant
    When the Buyer Agent syncs creative "creative-1" as principal "buyer-B"
    Then the response is compliant with the sync_creatives success spec
    And a new creative should be created for principal "buyer-B"
    And the existing creative for principal "buyer-A" should remain unchanged

  @T-UC-006-rule-034-inv3 @invariant @BR-RULE-034
  Scenario: INV-3 — new creative stamped with authenticated principal
    Given the Buyer is authenticated as principal "buyer-A"
    And a creative that does not exist in the library
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the created creative should be associated with principal "buyer-A"
    # --- BR-RULE-034 / BR-RULE-033: assignment references graded on the wire ---
    # These four replaced integration tests that asserted _impl's returned DTO or a
    # pytest.raises class -- neither of which can see a wrong wire code. The pinned 3.1 enums/error-code.json defines
    # CREATIVE_NOT_FOUND ("Referenced creative does not exist in the agent's creative
    # library ... Sellers MUST return this code uniformly for any creative_id not owned
    # by the calling account", recovery correctable) and PACKAGE_NOT_FOUND; the response
    # schema puts per-item failures on creatives[] with action "failed" and a
    # core/error.json errors[] entry, so the same code reaches the buyer on both arms:
    # strict aborts the request, lenient records it on the synthesized entry.

  @T-UC-006-local-assignment-unknown-creative @invariant @BR-RULE-034 @error-details
  Scenario Outline: Assignment naming a creative the library does not hold — <mode>
    Given the Buyer is authenticated
    And a creative with a known format_id
    And an assignment referencing the unknown creative "c_never_synced" to a package that exists in the tenant
    And validation_mode is "<mode>"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives <branch> spec
    And <expected>

    Examples:
      | mode    | branch  | expected                                                                    |
      | strict  | error   | the error code should be "CREATIVE_NOT_FOUND"                               |
      | lenient | success | the creatives entry for "c_never_synced" carries error code "CREATIVE_NOT_FOUND" |

  @T-UC-006-local-assignment-only-missing-package @invariant @BR-RULE-033 @error-details
  Scenario: Assignment-only reference whose package does not exist carries PACKAGE_NOT_FOUND on its entry
    Given the Buyer is authenticated
    And a creative with a known format_id
    And an assignment referencing the library creative "c_exists_in_library" to the package "pkg_does_not_exist"
    And validation_mode is "lenient"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creatives entry for "c_exists_in_library" has action "failed"
    And the creatives entry for "c_exists_in_library" carries error code "PACKAGE_NOT_FOUND"

  @T-UC-006-local-assignment-only-existing-creative @invariant @BR-RULE-033
  Scenario: Assignment-only reference to a creative already in the library surfaces assigned_to
    Given the Buyer is authenticated
    And a creative with a known format_id
    And an assignment referencing the library creative "c_preexisting" to a package that exists in the tenant
    And validation_mode is "lenient"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creatives entry for "c_preexisting" has action "unchanged"
    And the creatives entry for "c_preexisting" is assigned to the package

  # GH #1418: a creative that fails per-item validation is never persisted, so its
  # assignment must be reported on its entry rather than attempted (the attempt was a
  # foreign-key violation surfacing as a 500). An empty name is schema-conformant
  # (core/creative-asset.json sets no minLength) and fails the seller's own per-item
  # validation -- the same mechanism BR-RULE-033 INV-1 grades live.
  @T-UC-006-local-failed-creative-assignment @invariant @BR-RULE-033
  Scenario: A creative that fails validation keeps its assignment out of the library and reports it
    Given the Buyer is authenticated
    And a creative with a known format_id
    And the creative has an empty name
    And an assignment to a package that exists in the tenant
    And validation_mode is "lenient"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creatives entry for "creative-known-fmt-001" has action "failed"
    And the creatives entry for "creative-known-fmt-001" carries error code "VALIDATION_ERROR"
    And the creatives entry for "creative-known-fmt-001" reports the package as an assignment error

    # --- BR-RULE-035: Creative Format Validation ---

  @T-UC-006-rule-035-static @invariant @BR-RULE-035
  Scenario: Static creative validated by creative agent
    Given the Buyer is authenticated
    And a creative with a known HTTP-based format_id
    And the creative agent is reachable
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative should be validated by the creative agent
    And the creative should have action "created"
    # No preview_url line: sync-creatives-response.json defines preview_url as
    # "Preview URL for generative creatives (only present for generative formats)",
    # and this creative is static. The line that stood here asserted a field the
    # pin says must be absent.

  @T-UC-006-rule-035-inv2 @invariant @BR-RULE-035
  Scenario: INV-2 — adapter format skips external validation
    Given the Buyer is authenticated
    And a creative with a non-HTTP adapter format_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative should be processed without external agent validation
    And the creative should have action "created" or "updated"
    # --- BR-RULE-036: Generative Creative Build ---

  @T-UC-006-rule-036-inv1 @invariant @BR-RULE-036
  Scenario: INV-1 — generative detection via output_format_ids
    Given the Buyer is authenticated
    And a creative with a format that has output_format_ids defined
    And GEMINI_API_KEY is configured
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative should be processed as generative
    And the creative should have generated content

  @T-UC-006-rule-036-inv2 @invariant @BR-RULE-036
  Scenario: INV-2 — the build's prompt is the generation_prompt its format declares, ahead of any other prompt role
    Given the Buyer is authenticated
    And a generative creative whose "message" asset carrying "Make it look wintry" comes before its generation_prompt carrying "Create summer vibes"
    And GEMINI_API_KEY is configured
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the generative build should use "Create summer vibes" as the prompt

  # THE PROMPT IS THE ASSET THE FORMAT DECLARES FOR IT. display_300x250_generative, as the
  # reference agent serves it, requires one asset, generation_prompt (text), and
  # core/canonical-projection-ref.json names "a generative entry whose input is
  # generation_prompt: text". So a conformant generative creative always carries it, and a
  # message asset beside it is not the input. The message comes FIRST in the payload and says
  # something else, so a seller that reads the first prompt role in the buyer's key order builds
  # from "Make it look wintry" and the Then reddens.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/canonical-projection-ref.json
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format.json pointer=/$defs/baseIndividualAsset/properties/required

  @T-UC-006-rule-036-inv3 @invariant @BR-RULE-036
  Scenario: INV-3 — a generative creative carrying no prompt asset is refused, inputs notwithstanding
    Given the Buyer is authenticated
    And a generative creative with no prompt assets but inputs[0].context_description = "Holiday theme"
    And GEMINI_API_KEY is configured
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative is reported failed with CREATIVE_VALIDATION_FAILED_GENERIC at "creatives[0]" and nothing is stored

  @T-UC-006-rule-036-inv4 @invariant @BR-RULE-036
  Scenario: INV-4 — a new generative creative carrying no prompt asset is refused, not built from its name
    Given the Buyer is authenticated
    And a generative creative named "Summer Sale Banner" with no prompt assets or inputs
    And GEMINI_API_KEY is configured
    When the Buyer Agent creates the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative is reported failed with CREATIVE_VALIDATION_FAILED_GENERIC at "creatives[0]" and nothing is stored

  @T-UC-006-rule-036-inv5 @invariant @BR-RULE-036
  Scenario: INV-5 — an update carrying no prompt asset is refused and the existing data kept
    Given the Buyer is authenticated
    And a generative creative that already exists with generated content
    And no prompt assets or inputs
    And GEMINI_API_KEY is configured
    When the Buyer Agent updates the creative
    Then the response is compliant with the sync_creatives success spec
    And the creatives entry for "creative-gen-inv5-001" carries error code "CREATIVE_VALIDATION_FAILED_GENERIC"
    And the existing creative data should be preserved

  # INV-3 TO INV-5 USED TO GRADE A PROMPT FALLBACK (inputs, then the creative's name, then a
  # skipped build on update) for a generative creative carrying no prompt asset. The format
  # every one of them names, the reference agent's display_300x250_generative, declares its
  # prompt as a REQUIRED asset (generation_prompt, text), and core/format.json says each
  # asset's asset_id "must be used as the key in creative manifests" and that "Required
  # assets must be provided for a valid creative". creative-manifests.mdx § Validation puts
  # final acceptance at sync_creatives: "Confirm all required slots are present". So the
  # creative is refused per item, before any fallback, and an update leaves the stored
  # creative as it was.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format.json pointer=/$defs/baseIndividualAsset/properties/required
  # @source repo=adcp ref=v3.2.7 path=docs/creative/creative-manifests.mdx section="Validation"

  @T-UC-006-rule-036-inv6 @invariant @BR-RULE-036
  Scenario: INV-6 — user assets take priority over generative output
    Given the Buyer is authenticated
    And a generative creative with both user-provided assets and generative prompt
    And GEMINI_API_KEY is configured
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the user-provided assets should be preserved
    And user assets should take priority over any generated content
    # --- BR-RULE-037: Approval Workflow ---

  @T-UC-006-rule-037-inv1 @invariant @BR-RULE-037
  Scenario: INV-1 — default approval mode is require-human
    Given the Buyer is authenticated
    And the tenant has no approval_mode configured
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative status should be "pending_review"
    And a workflow step should be created

  @T-UC-006-rule-037-inv2 @invariant @BR-RULE-037
  Scenario: INV-2 — auto-approve sets status directly
    Given the Buyer is authenticated
    And the tenant has approval_mode "auto-approve"
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative status should be "approved"
    And no workflow steps should be created
    And no Slack notification should be sent

  @T-UC-006-rule-037-inv3 @invariant @BR-RULE-037
  Scenario: INV-3 — require-human creates workflow and sends Slack
    Given the Buyer is authenticated
    And the tenant has approval_mode "require-human"
    And the tenant has a slack_webhook_url configured
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative status should be "pending_review"
    And a workflow step should be created with type "creative_approval"
    And a Slack notification should be sent immediately

  @T-UC-006-rule-037-inv4 @invariant @BR-RULE-037
  Scenario: INV-4 — ai-powered creates workflow and submits AI review
    Given the Buyer is authenticated
    And the tenant has approval_mode "ai-powered"
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative status should be "pending_review"
    And a workflow step should be created
    And a background AI review task should be submitted
    And Slack notification should be deferred until AI review completes

  @T-UC-006-rule-037-inv5 @invariant @BR-RULE-037
  Scenario: INV-5 — workflow step attributes
    Given the Buyer is authenticated
    And the tenant has approval_mode "require-human"
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the workflow step should have step_type "creative_approval"
    And the workflow step should have owner "publisher"
    And the workflow step should have status "requires_approval"

  @T-UC-006-rule-037-inv6 @invariant @BR-RULE-037
  Scenario: INV-6 — Slack only sent when webhook configured and creatives need approval
    Given the Buyer is authenticated
    And the tenant has approval_mode "require-human"
    And the tenant has no slack_webhook_url configured
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative status should be "pending_review"
    But no Slack notification should be sent
    # --- BR-RULE-038: Assignment Package Validation ---

  @T-UC-006-rule-038-inv1 @invariant @BR-RULE-038
  Scenario: INV-1 — package lookup is tenant-scoped
    Given the Buyer is authenticated
    And a package exists in a different tenant
    And assignments referencing that package_id
    And validation_mode is "strict"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives error spec
    And the assignment should fail with "PACKAGE_NOT_FOUND"
    And the cross-tenant package should not be accessible

  @T-UC-006-rule-038-inv3 @invariant @BR-RULE-038
  Scenario: INV-3 — idempotent assignment upsert
    Given the Buyer is authenticated
    And a creative already assigned to a package
    And assignments referencing the same package_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the existing assignment should be updated (not duplicated)

  @T-UC-006-rule-038-inv4 @invariant @BR-RULE-038
  Scenario: INV-4 — draft media buy with approved_at transitions to pending_creatives
    Given the Buyer is authenticated
    And a media buy with status "draft" and approved_at set
    And a creative with a known format_id
    And assignments to a package in that media buy
    When the Buyer Agent syncs the creative with assignments
    Then the response is compliant with the sync_creatives success spec
    And the media buy status should transition to "pending_creatives"

  @T-UC-006-rule-038-inv4-violated @invariant @BR-RULE-038
  Scenario: INV-4 violated — draft media buy without approved_at does not transition
    Given the Buyer is authenticated
    And a media buy with status "draft" and approved_at null
    And a creative with a known format_id
    And assignments to a package in that media buy
    When the Buyer Agent syncs the creative with assignments
    Then the response is compliant with the sync_creatives success spec
    And the media buy status should remain "draft"

  @T-UC-006-rule-038-inv5 @invariant @BR-RULE-038
  Scenario: INV-5 — non-draft media buy does not transition
    Given the Buyer is authenticated
    And a media buy with status "active" (non-draft)
    And a creative with a known format_id
    And assignments to a package in that media buy
    When the Buyer Agent syncs the creative with assignments
    Then the response is compliant with the sync_creatives success spec
    And the media buy status should remain "active"
    # --- BR-RULE-039: Assignment Format Compatibility ---

  # The agent is the one the harness actually seeds, spelled two ways. It used to be the
  # fictional "https://agent.example.com", which could never resolve against the live
  # registry on e2e_rest -- so the scenario could not succeed there in principle, and
  # in-process it only appeared to because the registry is mocked. The rule is real and
  # wire-observable: if canonicalization stopped equating the two spellings, the seller would
  # not resolve the product's format and no assignment would be created.
  #
  # The axis is HOST CASE, not a trailing slash. core/format-id.json's algorithm
  # (docs/reference/url-canonicalization) collapses host case at step 2 for ANY URL,
  # whereas a trailing slash is collapsed only on an EMPTY path (step 5) -- so a
  # trailing-slash scenario asserts something true of the in-process seed
  # ("https://creative.test.example.com") and FALSE of the e2e one
  # (".../api/creative-agent"), where the spec makes ".../creative-agent" and
  # ".../creative-agent/" two different agents. It passed on e2e only because
  # canonical_agent_url used to rstrip("/"), an "additional transformation before
  # comparison" the algorithm forbids. Host case is spec-true on every transport.
  @T-UC-006-rule-039-inv1 @invariant @BR-RULE-039
  Scenario: INV-1 — agent_url host case does not split format identity
    Given the Buyer is authenticated
    And a creative with a known format_id
    And a product whose format agent_url is the same agent with the host upper-cased
    When the Buyer Agent syncs the creative with assignments
    Then the response is compliant with the sync_creatives success spec
    And the assignment should be created

  # The negative half INV-1 never had. Canonicalization that collapsed too much would
  # pass the scenario above and fail this one, and only the pair pins the rule: the
  # spec preserves the PATH, so an agent serving MCP at /mcp and A2A at /a2a on one
  # host is two agents, not one.
  @T-UC-006-rule-039-inv1b @invariant @BR-RULE-039 @error
  Scenario: INV-1b — a different path on the same host IS a different agent
    Given the Buyer is authenticated
    And a creative with a known format_id
    And a product whose format agent_url is the same host at a different path
    And validation_mode is "strict"
    When the Buyer Agent syncs the creative with assignments
    Then the response is compliant with the sync_creatives error spec
    And the assignment should fail with "VALIDATION_ERROR"

  # BR-RULE-039 INV-2 (same agent_url, different format_id is NOT a match) is the
  # format_mismatch row of @T-UC-006-partition-assignment-fmt: the product there declares
  # a different id at the creative's own agent. The scenario that stood here carried
  # literal example.com URLs, which the Docker egress gate refuses at DNS, so on e2e_rest
  # the creative failed on its agent_url before the rule was ever exercised.

  @T-UC-006-rule-039-inv3 @invariant @BR-RULE-039
  Scenario: INV-3 — empty product format_ids allows all formats
    Given the Buyer is authenticated
    And a creative with any format_id
    And assignments to a package whose product has empty format_ids
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the format compatibility check should pass
    And the assignment should be created successfully

  # INV-4 ("product format_ids accepts both id and format_id keys") is DELETED. A
  # product's format_ids entries are core/format-id.json objects, whose key is
  # ``id``; a ``format_id`` key is a shape the pin does not define, so the
  # invariant asserted tolerance for something no conformant buyer sends.

  @T-UC-006-rule-039-inv6 @invariant @BR-RULE-039
  Scenario: INV-6 — no product_id on package skips format check
    Given the Buyer is authenticated
    And a creative with any format_id
    And assignments to a package that has no product_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the format compatibility check should be skipped
    And the assignment should be created successfully

  @T-UC-006-rule-039-inv5-lenient @invariant @BR-RULE-039
  Scenario: INV-5 — format mismatch in lenient mode skips assignment
    Given the Buyer is authenticated
    And a creative with a known format_id
    And assignments to two packages: one with compatible format and one incompatible
    And validation_mode is "lenient"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the compatible package assignment should be created
    And the incompatible package should be reported in assignment_errors
    And processing should continue without aborting
    # --- BR-RULE-040: Media Buy Status Transition ---

  @T-UC-006-rule-040-inv1 @invariant @BR-RULE-040
  Scenario: INV-1 — draft with approved_at transitions to pending_creatives
    Given the Buyer is authenticated
    And a media buy with status "draft" and approved_at set
    And assignments to a package in that media buy
    When the Buyer Agent syncs the creative with assignments
    Then the response is compliant with the sync_creatives success spec
    And the media buy status should transition to "pending_creatives"

  @T-UC-006-rule-040-inv2 @invariant @BR-RULE-040
  Scenario: INV-2 — draft without approved_at stays draft
    Given the Buyer is authenticated
    And a media buy with status "draft" and approved_at null
    And assignments to a package in that media buy
    When the Buyer Agent syncs the creative with assignments
    Then the response is compliant with the sync_creatives success spec
    And the media buy status should remain "draft"

  @T-UC-006-rule-040-inv3 @invariant @BR-RULE-040
  Scenario: INV-3 — non-draft status unchanged
    Given the Buyer is authenticated
    And a media buy with status "active" (non-draft)
    And assignments to a package in that media buy
    When the Buyer Agent syncs the creative with assignments
    Then the response is compliant with the sync_creatives success spec
    And the media buy status should remain "active"

  @T-UC-006-rule-040-inv4 @invariant @BR-RULE-040
  Scenario: INV-4 — both new and updated assignments trigger transition check
    Given the Buyer is authenticated
    And a media buy with status "draft" and approved_at set
    And an existing assignment to a package in that media buy
    And a new assignment to another package in the same media buy
    When the Buyer Agent syncs the creative with assignments
    Then the response is compliant with the sync_creatives success spec
    And the media buy status should transition to "pending_creatives"
    # --- BR-RULE-093: Assignment Weight and Delivery Semantics ---

  # BR-RULE-093 INV-1 (weight 0 is assigned but paused) and INV-2 (weight omitted is equal
  # rotation) are the "weight = 0" and "weight absent" rows of
  # @T-UC-006-boundary-assignment-weight -- one definition of assignments[].weight.

  @T-UC-006-rule-093-inv3 @invariant @BR-RULE-093
  Scenario: INV-3 — each assignment keeps its own weight for proportional delivery
    Given the Buyer is authenticated
    And creative "creative-A" assigned to "pkg-1" with weight 80
    And creative "creative-B" assigned to "pkg-1" with weight 20
    When the Buyer Agent syncs the creatives
    Then the response is compliant with the sync_creatives success spec
    And the assignment of "creative-A" to "pkg-1" should carry weight 80
    And the assignment of "creative-B" to "pkg-1" should carry weight 20
    # assignments[].weight: "When multiple creatives are assigned to the same package,
    # weights determine impression distribution proportionally" (sync-creatives-request.json).
    # The persisted weights are what the ad server rotates on; delivery itself is not
    # observable on this tool, so the invariant is graded on the weights each entry keeps.
    # --- BR-RULE-094: Creative Provenance Policy Enforcement ---

  # BR-RULE-094 INV-1 read "provenance absent when required triggers a warning". The pin
  # outranks the rule: PROVENANCE_REQUIRED (enums/error-code.json) is the code for a
  # creative with "no provenance object on the manifest, on the creative-asset, or on any
  # individual asset" under a policy with provenance_required, and core/creative-policy.json
  # makes the refusal a MUST. The invariant is
  # @T-UC-006-storyboard-provenance-required-rejection and the matching boundary and
  # partition rows.

  @T-UC-006-rule-094-inv2 @invariant @BR-RULE-094
  Scenario: INV-2 — provenance present when required passes normally
    Given the Buyer is authenticated
    And a product with creative_policy.provenance_required = true
    And a creative with provenance metadata
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative should be processed normally
    And no provenance warning should be generated

  @T-UC-006-rule-094-inv3 @invariant @BR-RULE-094
  Scenario: INV-3 — no provenance policy means check skipped
    Given the Buyer is authenticated
    And no product with provenance_required
    And a creative with no provenance metadata
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative should be processed normally
    And no provenance warning should be generated

  @T-UC-006-rule-094-inv4 @invariant @BR-RULE-094
  Scenario: INV-4 — creative_policy null on product means check skipped
    Given the Buyer is authenticated
    And a product with creative_policy = null
    And a creative with no provenance metadata
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creative should be processed normally
    And no provenance warning should be generated

  @T-UC-006-rule-094-inv5 @invariant @BR-RULE-094
  Scenario: INV-5 — asset-level provenance replaces creative-level entirely
    Given the Buyer is authenticated
    And a creative with provenance declaring digital_source_type "digital_capture"
    And an asset within the creative declaring digital_source_type "trained_algorithmic_media"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the asset should have provenance "trained_algorithmic_media" (not inherited "digital_capture")
    And no field-level merging should occur

  # The validation_mode partition (strict / lenient / an unknown value) is the row set of
  # @T-UC-006-boundary-validation-mode, which also grades the default; one outline.
    # --- approval_mode partitions ---

  @T-UC-006-partition-approval-mode @partition @approval-mode
  Scenario Outline: Approval mode routing — <partition>
    Given the Buyer is authenticated
    And the tenant has approval_mode "<mode>"
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And the creative status should be "<status>"
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
    Then the response is compliant with the sync_creatives spec
    And the action should be "<action>"
    # --- format_id partitions ---

    Examples: Scope resolution
      | partition         | principal | creative_id | existence                                | action   |
      | new_creative      | buyer-A   | c-1         | does not exist for this principal         | created  |
      | existing_creative | buyer-A   | c-1         | exists for principal buyer-A              | updated  |
      | cross_principal   | buyer-B   | c-1         | exists for principal buyer-A only         | created  |

  @T-UC-006-partition-format-id @partition @format-id
  Scenario Outline: Format validation — <partition>
    Given the Buyer is authenticated
    And a creative with <format_setup>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- generative_build partitions ---

    # Each row names the exact wire outcome the pin gives it: a missing format_id is a
    # schema violation and refuses the request (INVALID_REQUEST); an unknown format is a
    # reference that does not resolve, on the creative's entry (REFERENCE_NOT_FOUND); an
    # unreachable agent is the seller's dependency not answering, transient, on the
    # request (SERVICE_UNAVAILABLE -- AGENT_UNREACHABLE is not in the enum); an empty
    # name is schema-conformant and fails the seller's own rule on the entry
    # (VALIDATION_ERROR -- name has no minLength, so INVALID_REQUEST was wrong).
    Examples: Format partitions
      | partition          | format_setup                             | expected                                                     |
      | known_http_format  | a known HTTP-based format_id             | the creative should be processed successfully                |
      | adapter_format     | a non-HTTP adapter format_id             | the creative should skip external format validation          |
      | missing_format_id  | no format_id                             | the error code should be "INVALID_REQUEST"                   |
      | unknown_format     | a format_id unknown to all agents        | the creatives entry carries error code "REFERENCE_NOT_FOUND" |
      | agent_unreachable  | a format_id whose agent is unreachable   | the error code should be "SERVICE_UNAVAILABLE"               |
      | empty_name         | format_id but an empty name              | the creatives entry carries error code "VALIDATION_ERROR"    |

  @T-UC-006-partition-generative @partition @generative
  Scenario Outline: Generative build detection — <partition>
    Given the Buyer is authenticated
    And a creative with <format_type>
    And <prompt_source>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- assignment_package partitions ---

    Examples: Generative partitions
      | partition                       | format_type                        | prompt_source                          | expected                                                          |
      | static_creative                 | no output_format_ids               | any assets                             | the creative should be processed without generative build         |
      | generative_with_prompt          | output_format_ids present          | a message asset ahead of the generation_prompt | the generative build should use the generation_prompt asset as the prompt |
      | generative_without_prompt_asset | output_format_ids present (create) | no prompt assets or inputs             | the creatives entry carries error code "CREATIVE_VALIDATION_FAILED_GENERIC" |
      | generative_no_gemini_key        | output_format_ids present          | message asset but no GEMINI_API_KEY    | the creatives entry carries error code "CONFIGURATION_ERROR"      |

  @T-UC-006-partition-assignment-pkg @partition @assignment-package
  Scenario Outline: Assignment package validation — <partition>
    Given the Buyer is authenticated
    And a creative with a known format_id
    And <package_setup>
    And validation_mode is "strict"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- assignment_format partitions ---

    Examples: Package partitions
      | partition           | package_setup                              | expected                                        |
      | existing_package    | assignments to an existing package          | the assignment should be created successfully   |
      | existing_assignment | the creative is already assigned to package | the existing assignment should be updated       |
      | package_not_found   | assignments to a non-existent package       | the error code should be "PACKAGE_NOT_FOUND"    |

  @T-UC-006-partition-assignment-fmt @partition @assignment-format
  Scenario Outline: Assignment format compatibility — <partition>
    Given the Buyer is authenticated
    And a creative with a known format_id
    And assignments to a package with <product_setup>
    And validation_mode is "strict"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- media_buy_status partitions ---

    # The creative carries the format its transport's agent serves; only the PRODUCT's
    # declared set varies. The rows used to name a literal creative format, which the
    # in-process mock accepted and the real e2e agent did not, so the "matches" rows
    # failed the creative before any assignment ran.
    #
    # A mismatch is VALIDATION_ERROR: 3.1.1 enums/error-code.json defines it as
    # "violates business rules beyond schema validation", and a format outside the
    # product's declared set is exactly that. CREATIVE_REJECTED is "Creative failed
    # content policy review" with {policy_id, policy_url, reasons} details -- a policy
    # outcome this path never reaches; the creative is fine, the ASSIGNMENT is what the
    # product does not permit. Which format and product are incompatible travels
    # structurally, not in the sentence (ADR-010).
    #
    # format_mismatch is also BR-RULE-039 INV-2: identity is the (canonical agent_url,
    # id) PAIR (core/format-id.json), so a different id at the same agent is no match.
    Examples: Format compatibility partitions
      | partition       | product_setup                              | expected                                       |
      | format_matches  | product accepting the creative's format    | the assignment should be created successfully  |
      | no_restrictions | product with empty format_ids              | the assignment should be created successfully  |
      | no_product_id   | package with no product_id                 | the assignment should be created successfully  |
      | format_mismatch | product accepting only a different format  | the error code should be "VALIDATION_ERROR"    |

  @T-UC-006-partition-mb-status @partition @media-buy-status
  Scenario Outline: Media buy status transition on assignment — <partition>
    Given the Buyer is authenticated
    And a media buy with status "<mb_status>" and approved_at <approved_at>
    And assignments to a package in that media buy
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And the media buy status should be "<final_status>"
    # --- provenance partitions ---

    Examples: Status transition partitions
      | partition          | mb_status | approved_at | final_status      |
      | draft_approved     | draft     | set         | pending_creatives |
      | draft_not_approved | draft     | null        | draft             |
      | non_draft          | active    | set         | active            |

  @T-UC-006-partition-provenance @partition @provenance
  Scenario Outline: Provenance policy enforcement — <partition>
    Given the Buyer is authenticated
    And <provenance_setup>
    And <policy_setup>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <outcome>
    # --- assignments_structure partitions ---

    Examples: Provenance partitions
      | partition                        | provenance_setup                        | policy_setup                                              | outcome                                          |
      | provenance_present_required      | a creative with provenance metadata     | a product with creative_policy.provenance_required = true | the creative should be processed without warning |
      | provenance_present_not_required  | a creative with provenance metadata     | no product with provenance_required                       | the creative should be processed without warning |
      | provenance_absent_not_required   | a creative without provenance metadata  | no product with provenance_required                       | the creative should be processed without warning |
      | provenance_absent_when_required  | a creative without provenance metadata  | a product with creative_policy.provenance_required = true | the creatives entry carries error code "PROVENANCE_REQUIRED" |
      | provenance_absent_not_required_explicitly | a creative without provenance metadata | a product with creative_policy.provenance_required = false | the creative should be processed without warning |

  @T-UC-006-partition-assignments-structure @partition @assignments-structure
  Scenario Outline: Assignments array structure — <partition>
    Given the Buyer is authenticated
    And a creative with a known format_id
    And <assignment_setup>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <outcome>
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
      | empty_array          | an empty assignments array                      | the error should be INVALID_REQUEST with suggestion                |
      | missing_creative_id  | an assignment entry missing creative_id         | the error should be INVALID_REQUEST with suggestion  |
      | missing_package_id   | an assignment entry missing package_id          | the error should be INVALID_REQUEST with suggestion   |

  # The assignment-weight partition (absent / 50 / 0 / 100 / -1 / 101) is a subset of the
  # rows of @T-UC-006-boundary-assignment-weight; one outline grades the field.
    # --- authentication partitions ---

  @T-UC-006-partition-auth @partition @authentication
  Scenario Outline: Authentication partition - <partition>
    Given <auth_state>
    And a creative with name "Banner" and a known format_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- account partitions ---

    Examples:
      | partition | auth_state                                          | expected                                           |
      | typical   | the Buyer is authenticated                          | the creative should be processed successfully      |
      | missing   | the Buyer has no authentication credentials             | the request should be rejected with AUTH_MISSING    |
      | empty     | the request has an empty principal_id                | the request should be rejected with AUTH_MISSING    |

  @T-UC-006-partition-account @partition @account
  Scenario Outline: Account resolution — <partition>
    Given the Buyer is authenticated
    And a creative with a known format_id
    And account is <account_setup>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <outcome>
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
    Given the Buyer is authenticated
    And a creative with a known format_id
    And idempotency_key is <key_value>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>

    # AdCP 3.1.1 creative/sync-creatives-request.json: idempotency_key is in /required and
    # declares minLength 16, maxLength 255, pattern ^[A-Za-z0-9_.:-]{16,255}$. So an ABSENT
    # key is a rejected request, not a "proceed without check" path (the key is
    # client-generated precisely so a retry carries the same one -- there is nothing for
    # the seller to proceed without), and the minimum boundary is 16 characters, not 8.
    Examples: Valid keys
      | partition      | key_value                                | expected                                              |
      | typical_valid  | "abc12345-retry-001"                     | the request should proceed normally                   |
      | boundary_min   | "1234567890123456"                       | the request should proceed normally                   |
      | uuid_format    | "550e8400-e29b-41d4-a716-446655440000"   | the request should proceed normally                   |

    Examples: Invalid keys
      | partition          | key_value          | expected                                                      |
      | absent             |                    | the error should be INVALID_REQUEST with suggestion |
      | empty_string       | ""                 | the error should be INVALID_REQUEST with suggestion |
      | too_short          | "abc1234"          | the error should be INVALID_REQUEST with suggestion |
      | boundary_below_min | "123456789012345"  | the error should be INVALID_REQUEST with suggestion |
      | too_long       | "a]x256"   | the error should be INVALID_REQUEST with suggestion  |

    # --- idempotency_key BEHAVIOR (the partitions above grade the key's SHAPE only) ---
    # AdCP 3.1.1 dist/compliance/3.1.1/universal/idempotency.yaml, narrative:
    #   "Every mutating request in AdCP carries an idempotency_key so buyers can safely
    #    retry after network errors without double-booking."
    #   2. "Replay with the same key and an equivalent payload returns the cached response
    #       without re-executing resource mutations."
    #   3. "Replay with the same key but a materially different payload is rejected with
    #       IDEMPOTENCY_CONFLICT."
    #   "Sellers that do not support create_media_buy SHOULD still pass idempotency
    #    compliance on whichever mutating task they do implement."
    # sync_creatives' pinned request schema marks idempotency_key REQUIRED, but the
    # universal storyboard has NO `task: sync_creatives` step at 3.1.1 — the obligation
    # is mandated and UNGRADED, so these two scenarios are its only grading for this tool.
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/universal/idempotency.yaml

  @T-UC-006-idempotency-replay @uc006-idempotency @idempotency-key @happy-path
  Scenario: Retrying a sync with the same idempotency_key does not re-execute the write
    Given the Buyer is authenticated
    And a creative with a known format_id
    And that creative was already synced with idempotency_key "sync-retry-0001-abcd"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And every creative result has action "created"
    And the per-creative result should carry no changes list
    And no additional creative approval workflow step should have been created

  @T-UC-006-idempotency-conflict @uc006-idempotency @idempotency-key @error-details
  Scenario: Reusing an idempotency_key with a materially different payload conflicts
    Given the Buyer is authenticated
    And a creative with a known format_id
    And that creative was already synced with idempotency_key "sync-conflict-01-abcd"
    And the creative name is changed to "Materially Different Creative"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives error spec
    And the operation should fail
    And the error code should be "IDEMPOTENCY_CONFLICT"
    And the error recovery should be "correctable"

  @T-UC-006-boundary-approval @boundary @approval-mode
  Scenario Outline: Approval mode boundary — <boundary_point>
    Given the Buyer is authenticated
    And a creative with name "Banner" and a known format_id
    And the tenant approval mode is <mode>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    And the per-creative result should carry advisory status "<status>"
    # --- validation_mode boundaries ---

    # The status column is sync-creatives-response.json's per-creative advisory
    # status -- "sellers with async review return processing or pending_review; sellers
    # with synchronous review MAY return a terminal value (approved, rejected)" -- as
    # this seller's review lifecycle produces it for each approval mode. The former
    # per-creative-status boundary outline named values no sync request can drive
    # (archived; a CreativeAction in the status slot), so its live rows live here.
    Examples:
      | boundary_point   | mode             | expected                                                   | status         |
      | not set (null)   | not configured   | the creative should use require-human as default           | pending_review |
      | auto-approve     | "auto-approve"   | the creative status should be set to approved immediately  | approved       |
      | require-human    | "require-human"  | a review workflow should be created with Slack notification | pending_review |
      | ai-powered       | "ai-powered"     | a review workflow should be created with AI review         | pending_review |

  @T-UC-006-boundary-validation-mode @boundary @validation-mode
  Scenario Outline: Validation mode boundary — <boundary_point>
    Given the Buyer is authenticated
    And a creative with name "Banner" and a known format_id
    And validation_mode is <mode>
    And assignments to a non-existent package
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- format_id boundaries ---

    Examples:
      | boundary_point           | mode       | expected                                                 |
      | not set (default strict) | not set    | the operation should abort with PACKAGE_NOT_FOUND        |
      | strict                   | "strict"   | the operation should abort with PACKAGE_NOT_FOUND        |
      | lenient                  | "lenient"  | the assignment should be skipped with a warning          |
      | unknown value            | "partial"  | the error code should be "INVALID_REQUEST"               |

  @T-UC-006-boundary-format-id @boundary @format-id
  Scenario Outline: Format validation boundary — <boundary_point>
    Given the Buyer is authenticated
    And <creative_state>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- generative_build boundaries ---

    Examples:
      | boundary_point           | creative_state                                              | expected                                                     |
      | missing format_id (null) | a creative with name "Banner" but no format_id              | the error code should be "INVALID_REQUEST"                   |
      | known HTTP format        | a creative with a known HTTP-registered format_id           | the creative should be processed successfully                |
      | adapter format (non-HTTP)| a creative with an adapter (non-HTTP) format_id             | the creative should skip external format validation          |
      | unknown format           | a creative with an unknown format_id                        | the creatives entry carries error code "REFERENCE_NOT_FOUND" |
      | agent unreachable        | a creative with a format_id whose agent is unreachable      | the error code should be "SERVICE_UNAVAILABLE"               |
      | empty name               | a creative with format_id but an empty name                 | the creatives entry carries error code "VALIDATION_ERROR"    |

  @T-UC-006-boundary-generative @boundary @generative
  Scenario Outline: Generative build boundary — <boundary_point>
    Given the Buyer is authenticated
    And <creative_state>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- creative_scope boundaries ---

    Examples:
      | boundary_point                         | creative_state                                                        | expected                                                          |
      | static creative (no output_format_ids) | a creative with a static format (no output_format_ids)                | the creative should be processed without generative build         |
      | generative with prompt from assets     | a creative with a generative format and prompt in assets              | the system should invoke generative build with the asset prompt   |
      | generative create, no prompt asset     | a new creative with a generative format and no prompt but a name      | the creatives entry carries error code "CREATIVE_VALIDATION_FAILED_GENERIC" |
      | generative, no GEMINI_API_KEY          | a creative with a generative format but GEMINI_API_KEY not configured | the creatives entry carries error code "CONFIGURATION_ERROR"      |

  @T-UC-006-boundary-creative-scope @boundary @creative-scope
  Scenario Outline: Creative scope boundary — <boundary_point>
    Given the Buyer is authenticated as principal "<principal>"
    And <creative_state>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- media_buy_status boundaries ---

    Examples:
      | boundary_point                          | principal     | creative_state                                          | expected                                               |
      | all three keys match (update)           | buyer-abc     | creative "C1" already exists for principal "buyer-abc"  | the existing creative should be updated                |
      | new creative_id (create)                | buyer-abc     | creative "C-new" does not exist for this principal      | a new creative should be created                       |
      | same creative_id, different principal    | buyer-xyz     | creative "C1" exists for principal "buyer-abc"          | a new creative should be created for "buyer-xyz"       |

  @T-UC-006-boundary-media-buy @boundary @media-buy-status
  Scenario Outline: Media buy status transition boundary — <boundary_point>
    Given the Buyer is authenticated
    And a creative with name "Banner" and a known format_id
    And an assignment to a package in a media buy with <buy_state>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- assignment_package boundaries ---

    Examples:
      | boundary_point                          | buy_state                        | expected                                                   |
      | draft + approved_at (transitions)       | status=draft and approved_at set | the media buy should transition to pending_creatives       |
      | draft + no approved_at (stays draft)    | status=draft and no approved_at  | the media buy should remain in draft status                |
      | non-draft status (no transition)        | status=active                    | the media buy status should not change                     |

  @T-UC-006-boundary-assignment-package @boundary @assignment-package
  Scenario Outline: Assignment package boundary — <boundary_point>
    Given the Buyer is authenticated
    And a creative with name "Banner" and a known format_id
    And <assignment_state>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- assignment_format boundaries ---

    Examples:
      | boundary_point                    | assignment_state                                       | expected                                             |
      | existing package                  | an assignment to a package that exists in the tenant   | the assignment should be created successfully        |
      | existing assignment (idempotent)  | an assignment that already exists for this creative    | the existing assignment should be updated            |
      | package not found                 | an assignment to a package that does not exist         | the error should include "suggestion" field          |

  @T-UC-006-boundary-assignment-format @boundary @assignment-format
  Scenario Outline: Assignment format compatibility boundary — <boundary_point>
    Given the Buyer is authenticated
    And a creative with a known format_id
    And <assignment_state>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- authentication boundaries ---

    # The last row's wire-observable outcome is the assignment itself: a package with no
    # product_id has no product to check the format against, and the only thing the
    # buyer can see of "the check was skipped" is that the assignment was created. The
    # cell used to say "the format check should be skipped entirely", bound to a step
    # whose body was byte-identical to the created-successfully one.
    #
    # The "format matches after URL normalization" row (product agent_url with a trailing
    # slash) is gone: core/format-id.json's canonicalization collapses a trailing slash
    # only on an EMPTY path (step 5) and preserves it on any other, so the row held on the
    # in-process root-path agent and failed on the real e2e agent, whose path is not
    # empty. The axis the algorithm does collapse, host case, is BR-RULE-039 INV-1.
    Examples:
      | boundary_point                              | assignment_state                                                     | expected                                              |
      | format matches (exact)                      | an assignment to a package whose product accepts this format         | the assignment should be created successfully         |
      | no product format restrictions              | assignments to a package whose product has empty format_ids        | the assignment should be created (all formats allowed)|
      | no product_id on package                    | an assignment to a package with no product_id                        | the assignment should be created successfully         |
      | format mismatch                             | an assignment to a package whose product does not accept this format | the error should include "suggestion" field           |

  @T-UC-006-boundary-principal @boundary @authentication
  Scenario Outline: Authentication boundary — <boundary_point>
    Given <auth_state>
    And a creative with name "Banner" and a known format_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- provenance boundaries ---

    Examples:
      | boundary_point        | auth_state                                          | expected                                             |
      | typical principal_id  | the Buyer is authenticated                          | the creative should be processed successfully       |
      | missing (null)        | the Buyer has no authentication credentials             | the request should be rejected with AUTH_MISSING     |
      | empty string          | the request has an empty principal_id                | the request should be rejected with AUTH_MISSING     |

  @T-UC-006-boundary-provenance @boundary @provenance
  Scenario Outline: Provenance policy boundary — <boundary_point>
    Given the Buyer is authenticated
    And <provenance_state>
    And <policy_state>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- assignments_structure boundaries ---

    Examples:
      | boundary_point                                                 | provenance_state                       | policy_state                                               | expected                                          |
      | provenance present + policy requires provenance                | a creative with provenance metadata    | a product with creative_policy.provenance_required = true  | the creative should be processed without warning  |
      | provenance absent + policy requires provenance                 | a creative without provenance metadata | a product with creative_policy.provenance_required = true  | the creatives entry carries error code "PROVENANCE_REQUIRED" |
      | provenance present + no provenance policy                      | a creative with provenance metadata    | no product with provenance_required                        | the creative should be processed without warning  |
      | provenance absent + no provenance policy                       | a creative without provenance metadata | no product with provenance_required                        | the creative should be processed without warning  |
      | provenance absent + creative_policy is null                    | a creative without provenance metadata | a product with creative_policy = null                      | the creative should be processed without warning  |
      | provenance absent + creative_policy exists but provenance_required=false | a creative without provenance metadata | a product with creative_policy.provenance_required = false | the creative should be processed without warning  |

  @T-UC-006-boundary-assignments-structure @boundary @assignments-structure
  Scenario Outline: Assignments structure boundary — <boundary_point>
    Given the Buyer is authenticated
    And a creative with a known format_id
    And <assignment_setup>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- assignment_weight boundaries ---

    Examples:
      | boundary_point                           | assignment_setup                                                 | expected                                                       |
      | assignments absent                       | no assignments field                                             | no assignment processing should occur                          |
      | empty array []                           | an empty assignments array                                       | the error should be INVALID_REQUEST with suggestion          |
      | single entry (minItems boundary)         | an assignment with creative_id "c1" and package_id "p1"         | the assignment should be created successfully                  |
      | entry missing creative_id                | an assignment entry with only package_id                         | the error should be INVALID_REQUEST with suggestion |
      | entry missing package_id                 | an assignment entry with only creative_id                        | the error should be INVALID_REQUEST with suggestion |
      | entry with weight = 0 (paused)           | an assignment with weight 0                                      | the assignment should be created as paused                     |
      | entry with placement_ids                 | an assignment with placement_ids ["slot_a"]                      | the assignment should include placement targeting              |
      | duplicate (creative_id, package_id) pair | two assignment entries with same creative_id and package_id      | the second should be an idempotent upsert                      |

  @T-UC-006-boundary-assignment-weight @boundary @assignment-weight
  Scenario Outline: Assignment weight boundary — <boundary_point>
    Given the Buyer is authenticated
    And a creative with a known format_id
    And an assignment with package_id "pkg-1" and weight <weight_value>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- account boundaries ---

    # The one definition of assignments[].weight (sync-creatives-request.json): "Relative
    # delivery weight (0-100) ... When omitted, the creative receives equal rotation with
    # other unweighted creatives. A weight of 0 means the creative is assigned but paused
    # (receives no delivery)." Out-of-range values violate the schema's minimum/maximum, so
    # they are INVALID_REQUEST at the request. The former partition outline, main-weight and
    # BR-RULE-093 INV-1/INV-2 were these rows written again.
    Examples:
      | boundary_point                     | weight_value | expected                                                                     |
      | weight absent (field omitted)      |              | the assignment should use equal rotation                                     |
      | weight = -1 (min - 1)              | -1           | the error should be INVALID_REQUEST with suggestion          |
      | weight = 0 (min, inclusive — paused)| 0            | the assignment should be created as paused                                   |
      | weight = 1 (min + 1)               | 1            | the assignment should be created with weight 1                               |
      | weight = 50 (typical)              | 50           | the assignment should be created with weight 50                              |
      | weight = 99 (max - 1)              | 99           | the assignment should be created with weight 99                              |
      | weight = 100 (max, inclusive)       | 100          | the assignment should be created with weight 100                             |
      | weight = 101 (max + 1)             | 101          | the error should be INVALID_REQUEST with suggestion          |

  @T-UC-006-boundary-account @boundary @account
  Scenario Outline: Account resolution boundary — <boundary_point>
    Given the Buyer is authenticated
    And a creative with a known format_id
    And account is <account_setup>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
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
    Given the Buyer is authenticated
    And a sync request whose scope is <scope_setup>
    When the Buyer Agent syncs the creatives
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # --- per-creative advisory status value boundaries (response shape) ---
    # NOTE: sync_creatives is an upsert-WRITE operation; it has NO creative_status
    # request filter (that is a list_creatives / UC-018 retrieval concept). The only
    # status surface here is the advisory CreativeStatus on each per-creative RESULT
    # of the SyncCreativesSuccess shape. These boundaries fix the enum membership of
    # that response value.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/sync-creatives-request.json

    Examples: Valid scope combinations
      | boundary_point                          | scope_setup                                              | expected                                                       |
      | delete_missing=true, creative_ids absent | delete_missing true and no creative_ids filter           | the request should proceed as a full-library replace           |
      | delete_missing=false, creative_ids absent | delete_missing false and no creative_ids filter         | the request should proceed and leave absent creatives unchanged |
      | delete_missing omitted                  | neither delete_missing nor creative_ids provided         | the request should proceed and leave absent creatives unchanged |
      | creative_ids present, delete_missing absent | a creative_ids filter and no delete_missing flag      | the request should proceed scoped to the filtered subset       |

    Examples: Invalid scope combination
      | boundary_point                          | scope_setup                                              | expected                                            |
      | delete_missing=true AND creative_ids present | delete_missing true together with a creative_ids filter | the error should be INVALID_REQUEST with suggestion |

  # The per-creative advisory status is graded where a request can drive it: the status
  # column of @T-UC-006-boundary-approval (which review state each approval mode reports)
  # and @T-UC-006-partition-creative-status-terminal (omitted on failed/deleted). Two outlines
  # that stood here asked the seller to emit values no sync request reaches -- "archived"
  # (sync_creatives has no archive input in the pin) and a CreativeAction in the status slot
  # -- or graded the seller's own response against the schema, which "the response is
  # compliant with the sync_creatives spec" already does on every scenario.

  @T-UC-006-boundary-sandbox @boundary @sandbox @v3-1
  Scenario Outline: Sandbox flag response-shape boundary — <boundary_point>
    Given the Buyer is authenticated
    And <creative>
    And <account_kind>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # sync-creatives-response.json: SyncCreativesSuccess carries ``sandbox`` ("When true,
    # this response contains simulated data from sandbox mode"); SyncCreativesError
    # forbids it (not.anyOf required:[sandbox]). core/account.json: a sandbox account
    # means "no real platform calls, no real spend". BR-RULE-209 INV-1 (inputs validated
    # the same as production), INV-4 (sandbox: true on the success shape), INV-5 (absent
    # for a production account), INV-7 (a sandbox account's invalid input is a REAL
    # rejection), INV-11 (forbidden on the errors shape). The one definition: the
    # sandbox-happy / -production / -validation / -errors-no-flag scenarios were these
    # rows written four times. The submitted-envelope rows (and the -submitted-no-flag
    # and main-async-submitted scenarios) are gone: this seller processes every sync
    # synchronously, so the pin's third shape is never produced and cannot be graded.
    # sync_creatives makes no ad-platform call and bills nothing on any account, so
    # INV-2/INV-3 have no observable here beyond the flag itself.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/sync-creatives-request.json

    Examples: Synchronous success shape (sandbox permitted)
      | boundary_point                      | creative                          | account_kind                             | expected                                        |
      | sandbox: true (sandbox account)     | a creative with a known format_id | the request targets a sandbox account    | the response should include sandbox equals true |
      | sandbox absent (production account) | a creative with a known format_id | the request targets a production account | the response should not include a sandbox field |

    Examples: Terminal-failure shape (sandbox forbidden)
      | boundary_point                                    | creative                             | account_kind                          | expected                                   |
      | real rejection, no sandbox flag (sandbox account) | a creative with an invalid format_id | the request targets a sandbox account | the error code should be "INVALID_REQUEST" |

  @T-UC-006-partition-creative-status-terminal @partition @creative-status @v3-1
  Scenario Outline: Per-creative result omits advisory status on a terminal action — <action>
    Given the Buyer is authenticated
    And <setup>
    When the Buyer Agent syncs the creatives
    Then the response is compliant with the sync_creatives spec
    And the creatives entry for "<creative_id>" has action "<action>"
    And the creatives entry for "<creative_id>" omits the "status" field
    # sync-creatives-response.json: status "MUST be omitted when action is failed or deleted
    # (the creative has no meaningful review state -- failure details belong in the errors
    # array; deleted creatives are gone from the library)", enforced by the per-item allOf
    # if/then. BR-RULE-037 governs status/approval routing. A failed action is reached
    # through a format no agent serves; a deleted one through a full-library replace.

    Examples: Terminal actions
      | action  | setup                                                                        | creative_id                      |
      | failed  | a creative with an unknown format_id                                         | creative-unknown-fmt-001         |
      | deleted | a sync request whose scope is delete_missing true and no creative_ids filter | creative-absent-from-request-001 |

  # The six CreativeItem / CreativeVariable scenarios that stood here graded a shape the
  # sync request does not carry: at 3.1.1, core/creative-item.json and
  # core/creative-variable.json are referenced only by creative/list-creatives-response.json
  # (the library READ), never by core/creative-asset.json, whose ``assets`` slots take the
  # assets/asset-union members. Nothing a sync_creatives request can send reaches them.

  @T-UC-006-partition-tracker-assets @partition @v3-1 @vast-tracker @daast-tracker
  Scenario Outline: Decomposed VAST and DAAST tracker assets — <partition>
    Given the Buyer is authenticated
    And a creative with a known format_id whose assets carry <tracker_assets>
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives spec
    And <expected>
    # core/assets/vast-tracker-asset.json and daast-tracker-asset.json (asset-union members
    # the request's ``assets`` slots accept): one tracker URL per TrackingEvents event. The
    # event MUST NOT be impression ("model as a url asset with url_type tracker_pixel"),
    # clickTracking, customClick, error or a ViewableImpression child; ``offset`` is
    # "Required when vast_event is progress"; DAAST ``target`` is linear or companion
    # ("DAAST has no NonLinearAds element"). A refused tracker is a schema violation,
    # so the whole request is INVALID_REQUEST (as ext-c). Accepted trackers are stored
    # as sent, for the sales agent to assemble into TrackingEvents at serve time.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/sync-creatives-request.json

    Examples: Accepted trackers, persisted as sent
      | partition            | tracker_assets                                               | expected                                                      |
      | vast_start_complete  | a VAST tracker for "start" and a VAST tracker for "complete"   | the creative should be created with its tracker assets stored |
      | daast_start_complete | a DAAST tracker for "start" and a DAAST tracker for "complete" | the creative should be created with its tracker assets stored |
      | vast_progress_offset | a VAST tracker for "progress" at offset "00:00:05"             | the creative should be created with its tracker assets stored |

    Examples: Refused trackers
      | partition               | tracker_assets                                     | expected                                   |
      | vast_impression         | a VAST tracker for "impression"                    | the error code should be "INVALID_REQUEST" |
      | vast_progress_no_offset | a VAST tracker for "progress" without an offset    | the error code should be "INVALID_REQUEST" |
      | daast_non_linear_target | a DAAST tracker for "start" targeting "non_linear" | the error code should be "INVALID_REQUEST" |

  @T-UC-006-error-details-creative-rejected @v3-1 @error-details @creative-rejected
  Scenario: CREATIVE_REJECTED carries the rejection reasons the buyer can act on
    Given the Buyer is authenticated
    And a creative with a known format_id whose agent returns no preview and that carries no media url
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the creatives entry for "creative-no-preview-001" has action "failed"
    And the creatives entry for "creative-no-preview-001" carries error code "CREATIVE_REJECTED"
    And the creatives entry for "creative-no-preview-001" carries a non-empty "reasons" error detail
    # error-details/creative-rejected.json: the RECOMMENDED details shape for
    # CREATIVE_REJECTED is {policy_id, policy_url, reasons}, ``reasons`` being "Specific
    # reasons the creative was rejected". This seller's synchronous rejection is the
    # creative agent answering with no preview for a creative that has no media_url to
    # fall back on -- a technical rejection, not a policy one -- so it carries reasons and
    # no policy reference. The CONFLICT and POLICY_VIOLATION scenarios that stood beside
    # this one named paths sync_creatives does not have (no creative versioning; content
    # policy is applied to media buys), so no request could drive them.

  @T-UC-006-storyboard-provenance-required-rejection @uc006-storyboard-routing @storyboard-v3.1 @v3-1 @provenance @rejection
  Scenario: PROVENANCE_REQUIRED -- provenance object absent on creative under a policy that requires it
    Given a product with creative_policy.provenance_required = true
    And the Buyer Agent submits a creative whose manifest carries no provenance object at all
    When the Buyer Agent sends sync_creatives
    Then the response is compliant with the sync_creatives success spec
    And the per-creative result should report action "failed"
    And the creatives entry carries error code "PROVENANCE_REQUIRED"
    # provenance_enforcement Phase 2: cheapest buyer mistake -- no provenance attached.
    # Seller accepts envelope but per-creative action=failed with PROVENANCE_REQUIRED.
    # provenance_enforcement: provenance entirely absent under provenance_required policy
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/provenance_enforcement.yaml phase=reject_no_provenance step=sync_creatives_no_provenance

  @T-UC-006-storyboard-provenance-digital-source-type-missing @uc006-storyboard-routing @storyboard-v3.1 @v3-1 @provenance @rejection
  Scenario: PROVENANCE_DIGITAL_SOURCE_TYPE_MISSING -- provenance present but digital_source_type omitted
    Given the tenant has a product with creative_policy.provenance_requirements.require_digital_source_type = true
    And the Buyer Agent submits a creative whose provenance object omits digital_source_type
    When the Buyer Agent sends sync_creatives
    Then the response is compliant with the sync_creatives success spec
    And the per-creative result should report action "failed"
    And the creatives entry carries error code "PROVENANCE_DIGITAL_SOURCE_TYPE_MISSING"
    # provenance_enforcement Phase 3: provenance attached but missing digital_source_type
    # under a policy with require_digital_source_type=true. Distinct from
    # PROVENANCE_REQUIRED because provenance IS present.
    # provenance_enforcement: digital_source_type missing under require_digital_source_type policy
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/provenance_enforcement.yaml phase=reject_missing_digital_source_type step=sync_creatives_no_digital_source_type

  @T-UC-006-storyboard-provenance-disclosure-missing @uc006-storyboard-routing @storyboard-v3.1 @v3-1 @provenance @rejection
  Scenario: PROVENANCE_DISCLOSURE_MISSING -- provenance present but disclosure block omitted under require_disclosure_metadata
    Given the tenant has a product with creative_policy.provenance_requirements.require_disclosure_metadata = true
    And the Buyer Agent submits a creative whose provenance object lacks a disclosure block
    When the Buyer Agent sends sync_creatives
    Then the response is compliant with the sync_creatives success spec
    And the per-creative result should report action "failed"
    And the creatives entry carries error code "PROVENANCE_DISCLOSURE_MISSING"
    # provenance_enforcement Phase 5: structural disclosure check. Seller inspects the
    # submitted manifest against creative_policy.provenance_requirements.require_disclosure_metadata
    # without calling any verifier. error.field points at the missing disclosure path.
    # provenance_enforcement: disclosure block missing under require_disclosure_metadata policy
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/provenance_enforcement.yaml phase=reject_missing_disclosure step=sync_creatives_missing_disclosure

  @T-UC-006-storyboard-provenance-corrected-acceptance @uc006-storyboard-routing @storyboard-v3.1 @v3-1 @provenance @acceptance
  Scenario: Corrected resubmission with disclosure block and on-list verifier is accepted
    Given a creative submission that previously failed with provenance rejection codes
    And the Buyer Agent resubmits with a complete disclosure block and an on-list verify_agent from the seller's accepted_verifiers
    When the Buyer Agent sends sync_creatives with the corrected manifest
    Then the response is compliant with the sync_creatives success spec
    And the per-creative result's action should be "created" or "updated"
    # provenance_enforcement Phase 6: the structural-rejection contract terminates in a
    # corrected acceptance. Buyer reads the rejection error codes from prior phases,
    # attaches a complete disclosure block, and represents an on-list verify_agent
    # drawn from creative_policy.accepted_verifiers. Per-creative action transitions
    # to created/updated (not failed); the creative enters the seller's review lifecycle.
    # BOTH values are conformant: the step's validation is `allowed_values: ["created",
    # "updated"]`, and phases 5 and 6 submit the same creative_id, so a seller that kept
    # the failed phase-5 entry answers "updated" for the resubmission.
    # provenance_enforcement: corrected resubmission terminates the rejection cascade
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/provenance_enforcement.yaml phase=accept_with_disclosure step=sync_creatives_with_disclosure

  @T-UC-006-storyboard-provenance-claim-contradicted @uc006-storyboard-routing @schema-v3.1 @v3-1 @provenance @rejection @truth-of-claim
  Scenario: PROVENANCE_CLAIM_CONTRADICTED -- on-list verifier refutes buyer's digital_source_type claim
    Given the Buyer Agent submits a creative claiming digital_source_type "digital_capture"
    And the on-list verifier responds with ai_generated true at confidence at least 0.9
    When the seller invokes the verifier against the creative manifest
    Then the response is compliant with the sync_creatives success spec
    And the per-creative result should report action "failed"
    And the creatives entry carries error code "PROVENANCE_CLAIM_CONTRADICTED"
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
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/provenance_truth_of_claim.yaml phase=reject_contradicted_claim step=sync_creatives_contradicted
    # NOT IMPLEMENTED, deliberately: provenance verification is deferred feature work,
    # tracked at https://github.com/prebid/salesagent/issues/1074 (accepted_verifiers on the
    # creative policy, the outbound get_creative_features call, the
    # PROVENANCE_CLAIM_CONTRADICTED refusal and its details class, and a verifier the e2e
    # stack can reach). It stays expected-to-fail until that lands.

  # STORYBOARD CLAIM WITHDRAWN at the AdCP 3.2.1 pin — RESTORABLE BY DECLARING.
    # Gated upstream by the `creative` PROTOCOL, which AdCP 3.2 split creatives into and which this seller does not declare, so the claim that an AdCP storyboard grades this scenario is FALSE at 3.2.1
    # and feeding a guard a false claim is the guard grading a fiction. Declaring it
    # restores the claim and the coverage with it; that decision is salesagent-o1wxm,
    # because declaring a capability is a get_adcp_capabilities behaviour change and this
    # repo declares a posture only where the implementation backs it.
    # The `@storyboard-v3.1` TAG is gone and nothing else is: every Given, When and Then
    # below still runs on every transport.
  @T-UC-006-storyboard-multi-format-sync @uc006-storyboard-routing @v3-1 @bulk-sync @multi-format
  Scenario: Bulk sync of three creatives in three different formats returns per-creative action
    Given the Buyer Agent submits three creatives in three different formats in a single sync_creatives call
    When the Buyer Agent sends sync_creatives
    Then the response is compliant with the sync_creatives success spec
    And the creatives array should carry one result per submitted creative
    And every per-creative result should expose an action field
    And every action value should be "created", "updated", or "failed"
    # sync-creatives-response.json: creatives[] is "Results for each creative processed",
    # one entry per submitted creative, each carrying an action from creative-action.json
    # (created, updated, unchanged, failed, deleted). The asserted set leaves out deleted,
    # which only delete_missing produces, and unchanged, which only a byte-identical
    # resubmission produces; this call carries neither. The storyboard's
    # creative/index.yaml sync_multiple step syncs ONE 300x250 image creative; the bulk
    # shape is the schema's, not that step's.
    # This half grades the ACTION obligations; the per-creative STATUS obligations are
    # graded by the sibling scenario below, split out because their known production gap
    # aborted this scenario and left these assertions dead (#1858).
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/sync-creatives-response.json pointer=/oneOf/0/properties/creatives

  # STORYBOARD CLAIM WITHDRAWN at the AdCP 3.2.1 pin — RESTORABLE BY DECLARING.
    # Gated upstream by the `creative` PROTOCOL, which AdCP 3.2 split creatives into and which this seller does not declare, so the claim that an AdCP storyboard grades this scenario is FALSE at 3.2.1
    # and feeding a guard a false claim is the guard grading a fiction. Declaring it
    # restores the claim and the coverage with it; that decision is salesagent-o1wxm,
    # because declaring a capability is a get_adcp_capabilities behaviour change and this
    # repo declares a posture only where the implementation backs it.
    # The `@storyboard-v3.1` TAG is gone and nothing else is: every Given, When and Then
    # below still runs on every transport.
  @T-UC-006-storyboard-multi-format-sync-status @uc006-storyboard-routing @v3-1 @bulk-sync @multi-format
  Scenario: Bulk sync of three creatives in three different formats returns per-creative status
    Given the Buyer Agent submits three creatives in three different formats in a single sync_creatives call
    When the Buyer Agent sends sync_creatives
    Then the response is compliant with the sync_creatives success spec
    And every per-creative result should carry a status exactly when its action is neither failed nor deleted
    And every status value should be drawn from the creative-status enum
    # The STATUS half of the sibling scenario above. sync-creatives-response.json, on the
    # per-creative status: "Values come from CreativeStatus only (processing,
    # pending_review, approved, suspended, rejected, archived)", and it "MUST be omitted
    # when action is failed or deleted ... Omit entirely when the seller has no review
    # lifecycle at all." This seller has a review lifecycle, so every entry that was not
    # failed or deleted carries one, and an entry that was carries none.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/sync-creatives-response.json pointer=/oneOf/0/properties/creatives/items/properties/status

  # STORYBOARD CLAIM WITHDRAWN at the AdCP 3.2.1 pin — RESTORABLE BY DECLARING.
    # Gated upstream by the `creative` PROTOCOL, which AdCP 3.2 split creatives into and which this seller does not declare, so the claim that an AdCP storyboard grades this scenario is FALSE at 3.2.1
    # and feeding a guard a false claim is the guard grading a fiction. Declaring it
    # restores the claim and the coverage with it; that decision is salesagent-o1wxm,
    # because declaring a capability is a get_adcp_capabilities behaviour change and this
    # repo declares a posture only where the implementation backs it.
    # The `@storyboard-v3.1` TAG is gone and nothing else is: every Given, When and Then
    # below still runs on every transport.
  @T-UC-006-storyboard-format-id-roundtrip-on-sync @uc006-storyboard-routing @v3-1 @format-id-roundtrip
  Scenario: Sync creative with the same format_id object returned by get_products -- seller MUST accept its own format_id
    Given the Buyer Agent captured a format_id {agent_url, id} from a prior get_products response
    When the Buyer Agent sends sync_creatives carrying a creative whose format_id matches the captured object
    Then the response is compliant with the sync_creatives success spec
    And the per-creative result should NOT report action "failed" due to format_id rejection
    And the seller's own format_id object should roundtrip through sync_creatives without modification
    # universal/schema-validation.yaml, phase format_option_reconciliation: "Identifiers
    # returned by get_products must be accepted back by the same agent in subsequent
    # calls." The buyer submits a creative whose format_id is the EXACT object returned by
    # get_products (products[0].format_ids[0]). The pin's own worked example of the
    # principle is the canonical one -- the selected format_kind and option reference
    # echoed into sync_creatives -- and format_id is the deprecated 3.x branch
    # (creative-asset.json: "DEPRECATED in 3.2"); a product that still emits a format_id
    # is still handing the buyer an identifier, so the principle covers it. The phase's
    # pointer to a media_buy_seller creative_sync phase names nothing at the pin.
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/universal/schema-validation.yaml phase=format_option_reconciliation

  @T-UC-006-canonical-identity-roundtrip-on-sync @schema-v3.2 @format-kind @format-option-ref
  Scenario Outline: A creative synced by format_kind with a <scope>-scope format_option_ref is listed back with that identity intact
    Given the Buyer is authenticated
    And the buyer syncs a creative identified by canonical kind "image" with a <scope>-scope format_option_ref
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response should include the creative with action "created"
    And listing the library returns the creative on format_kind "image" with the format_option_ref the buyer last sent, scope included

    Examples: the two scopes the reference discriminates between
      | scope     |
      | product   |
      | publisher |

    # canonical-formats-migration.mdx's completion checklist: "Preview, sync, library, and
    # delivery paths preserve `format_kind` and `format_option_ref`." The checklist item
    # carries no RFC 2119 keyword; the force comes from the schemas it points at. The sync
    # request's item (core/creative-asset.json) carries the canonical arm, and
    # list-creatives-response.json declares format_kind and format_option_ref on every
    # listed creative. sync-creatives-response.json carries no format identity per
    # creative, so a following list_creatives is the only place the buyer can observe that
    # the sync kept it. That makes this sync-then-list scenario the only way the sync half
    # can be graded. The library-side roundtrip (@T-UC-018-canonical-format-option-ref-roundtrip)
    # seeds its creative with a factory and never drives the sync write path.
    #
    # Both scopes are rows because core/format-option-ref.json discriminates on `scope`,
    # and a product-scope and a publisher-scope option with the same format_option_id are
    # different options. A single row would pass on a seller that hard-coded the other.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/canonical-formats-migration.mdx section="Completion checklist"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format-option-ref.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-response.json pointer=/properties/creatives/items property=format_option_ref

  @T-UC-006-canonical-identity-resync-replaces-option @schema-v3.2 @format-kind @format-option-ref
  Scenario: Re-syncing a canonical creative onto a different format_option_ref stores and lists the new option
    Given the Buyer is authenticated
    And the buyer syncs a creative identified by canonical kind "image" with a product-scope format_option_ref
    And the creative already exists with identical data
    And the resync narrows the same creative to a publisher-scope format_option_ref
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response should include the creative with action "updated"
    And listing the library returns the creative on format_kind "image" with the format_option_ref the buyer last sent, scope included

    # The update half of the same checklist item. The first sync goes through the
    # transport (the "identical data" Given), so the stored row is exactly what the write
    # path left. The resync then names a different option on the same kind. A different
    # option is a different format identity, so the update is a real change, answered
    # "updated" (enums/creative-action.json), and the listing must now carry the new
    # reference. A seller that kept the first-stored option on update would list the
    # product-scope reference and fail the readback.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/canonical-formats-migration.mdx section="Completion checklist"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/creative-action.json

  @T-UC-006-canonical-identity-through-preview @schema-v3.2 @format-kind @format-option-ref
  Scenario: A creative synced by format_kind is previewed by the operator's creative agent with that identity intact
    Given the Buyer is authenticated
    And the buyer syncs a creative carrying only a tag_url, identified by canonical kind "display_tag" with a publisher-scope format_option_ref
    And the operator's creative agent previews that creative's canonical identity
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response should include the creative with action "created"
    And the stored creative carries the preview the operator's creative agent returned
    And listing the library returns the creative on format_kind "display_tag" with the format_option_ref the buyer last sent, scope included

    # The PREVIEW half of the completion checklist ("Preview, sync, library, and delivery
    # paths preserve `format_kind` and `format_option_ref`"). A creative identified by
    # format_kind names no creative agent, so the seller previews it with its own -- the
    # operator's -- and the request carries the identity where
    # creative/preview-creative-request.json puts it: no `format_id` (deprecated in 3.2) and
    # `creative_manifest.format_kind` plus `creative_manifest.format_option_ref`.
    #
    # display_tag because the operator's agent declares exactly one format of that kind.
    # The same schema lets a request omit `target_capability_id` only when exactly one
    # advertised capability matches; this seller does not read the agent's capabilities,
    # so that is the case it can route, and on e2e the pinned reference agent resolves it.
    # The creative carries only the slot formats/canonical/display_tag.json requires.
    #
    # What is graded is what the seller STORED and RETURNED, not the agent's rendering: the
    # sync answers "created", the stored row holds a preview with a render, and the listing
    # returns the identity whole. A seller that never previews a format_kind creative stores
    # no preview; one that previews it without the canonical identity is not rendered.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/canonical-formats-migration.mdx section="Completion checklist"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/preview-creative-request.json pointer=/properties/format_id
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/preview-creative-request.json pointer=/properties/target_capability_id
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/display_tag.json pointer=/properties/slots

  @T-UC-006-canonical-unchanged-resync-keeps-review @schema-v3.2 @format-kind @format-option-ref
  Scenario: An unchanged resync of an approved creative identified by format_kind keeps its approval
    Given the Buyer is authenticated
    And the tenant has approval_mode "auto-approve"
    And the buyer syncs a creative identified by canonical kind "image" with a product-scope format_option_ref
    And the creative already exists with identical data
    And the tenant has approval_mode "require-human"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response should include the creative with action "unchanged"
    And the per-creative result should carry advisory status "approved"

  @T-UC-006-canonical-changed-resync-enters-review @schema-v3.2 @format-kind @format-option-ref
  Scenario: A resync that changes the format_option_ref of an approved creative sends it back to review
    Given the Buyer is authenticated
    And the tenant has approval_mode "auto-approve"
    And the buyer syncs a creative identified by canonical kind "image" with a product-scope format_option_ref
    And the creative already exists with identical data
    And the tenant has approval_mode "require-human"
    And the resync narrows the same creative to a publisher-scope format_option_ref
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response should include the creative with action "updated"
    And the per-creative result should carry advisory status "pending_review"

    # Review on RESYNC, for a creative identified by format_kind. Both scenarios approve the
    # creative on its first sync (auto-approve) and switch the tenant to require-human
    # before the second, so the mode now in force would move the status if it were applied.
    #
    # Changed content enters review. docs/creative/specification.mdx § Creative revision
    # identity: "Prior approval does not transfer: an accepted content-bearing unversioned
    # update enters the ordinary review lifecycle", and format identity is revision-bearing
    # content in the same section. A different format_option_ref is a different identity,
    # so the second scenario answers "updated" and goes back to pending_review.
    #
    # An unchanged resync is not an update and keeps its review state. § Creative Status
    # Lifecycle allows approved -> pending_review for a buyer-initiated revision update, or as a
    # seller-initiated re-review that MUST fire creative.status_changed. A resync that
    # changes nothing is neither, so the first scenario answers "unchanged" and stays
    # approved; a seller that re-applies the mode on every resync answers pending_review.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/specification.mdx section="Creative revision identity"
    # @source repo=adcp ref=v3.2.7 path=docs/creative/specification.mdx section="Creative Status Lifecycle"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/sync-creatives-response.json pointer=/oneOf/0/properties/creatives/items/properties/status

  @T-UC-006-canonical-renamed-resync-keeps-review @schema-v3.2 @format-kind @format-option-ref
  Scenario: A resync that only renames an approved creative identified by format_kind is updated and keeps its approval
    Given the Buyer is authenticated
    And the tenant has approval_mode "auto-approve"
    And the buyer syncs a creative identified by canonical kind "image" with a product-scope format_option_ref
    And the creative already exists with identical data
    And the tenant has approval_mode "require-human"
    And the creative name is changed to "Renamed Canonical Creative"
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response should include the creative with action "updated"
    And the per-creative result should carry advisory status "approved"

    # A rename is metadata, not revision content. docs/creative/specification.mdx
    # § Creative revision identity excludes "the explicit operational/metadata fields
    # `name`, `tags`, `status`, `weight`, `placement_refs`, and `placement_ids`" from
    # revision content, and says "a metadata-only mutation may still return `updated`"
    # while the current revision, and so its review state, is kept. The resync therefore
    # answers "updated" (the name did change) and stays "approved" under the require-human
    # mode now in force. § Creative Status Lifecycle allows approved -> pending_review for a
    # buyer-initiated revision update or a seller-initiated re-review that MUST fire
    # creative.status_changed; a rename is neither, so a seller that re-reviews on it
    # answers pending_review here.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/specification.mdx section="Creative revision identity"
    # @source repo=adcp ref=v3.2.7 path=docs/creative/specification.mdx section="Creative Status Lifecycle"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/creative-action.json

  @T-UC-006-representation-selection-readback @schema-v3.2 @format-kind @representation-selection
  Scenario: A creative synced with its representation_selection is listed back with that exact lineage
    Given the Buyer is authenticated
    And the buyer syncs a creative identified by canonical kind "image" selected from a representation set
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response should include the creative with action "created"
    And listing the library returns the creative carrying the representation_selection the buyer last sent

    # creative-manifests.mdx § Libraries and trafficking: "When a manifest was resolved from
    # a set of equivalent trafficking representations, it carries representation_selection.
    # That object preserves the durable creative_id, complete source revision_id plus digest,
    # selected representation_id, applied deterministic strategy, and derived-output digest
    # used for review." The seller's half of "carries" is the readback:
    # list-creatives-response.json representation_lineage_readback is
    # "required_when_current_creative_was_resolved_from_representation_set" and
    # "must_equal": "last_accepted_current_representation_selection". The sync response
    # carries no lineage, so the listing is where the buyer observes it.
    #
    # The item's selected_output_digest is computed test-side by the pin's projection rule
    # (remove $schema, representation_selection, creative_id, revision_id, name, tags,
    # status, weight, placement_refs, placement_ids and inputs; RFC 8785 JCS; SHA-256), and
    # that oracle is first held to the pin's worked example, so the accepted row is accepted
    # for the bytes it carries and not for agreeing with the seller's own arithmetic.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/creative-manifests.mdx section="Libraries and trafficking"
    # @source repo=adcp ref=v3.2.7 path=docs/creative/representation-sets.mdx section="Sync, review, and readback"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/list-creatives-response.json pointer=/properties/creatives/items/properties/representation_selection

  @T-UC-006-representation-selection-refused @schema-v3.2 @format-kind @representation-selection
  Scenario Outline: A representation_selection that does not describe its item is refused and nothing is stored - <member>
    Given the Buyer is authenticated
    And the buyer syncs a creative identified by canonical kind "image" selected from a representation set
    And the creative's representation_selection disagrees with the item on <member>
    When the Buyer Agent syncs the creative
    Then the creative is reported failed with CREATIVE_VALIDATION_FAILED_GENERIC at "creatives[0].representation_selection.<member>" and nothing is stored

    Examples:
      | member                 |
      | creative_id            |
      | revision_id            |
      | selected_output_digest |

    # sync_creatives.mdx: "When an item carries representation_selection, its creative_id and
    # revision_id must equal the selection lineage", and "The seller separately verifies
    # selected_output_digest with the same manifest projection used by build_creative".
    # sync-creatives-request.json representation_set_projection states the same as
    # identity_match and selected_output_verification. One row per lineage member, each
    # perturbing that member alone, so a seller that checks two of the three passes two rows.
    #
    # The code is CREATIVE_VALIDATION_FAILED_GENERIC: the pin names no specific code for a
    # lineage that does not describe its item, and the generic one is "buyer-correctable
    # validation ... the producer cannot classify ... with a more specific standard code".
    # CREATIVE_REPRESENTATION_UNRESOLVED is the RESOLVER's refusal when no candidate in a
    # CreativeRepresentationSet is compatible, and its details require one rejection per
    # candidate -- a seller receiving only the selected output has no candidates to name.
    # The refusal is per entry (sync_creatives is a batch tool) and is taken before the
    # write, so the creative is absent from the library.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/task-reference/sync_creatives.mdx section="Creative revisions"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/sync-creatives-request.json pointer=/properties/creatives/items/allOf/1/x-adcp-validation
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json property=CREATIVE_VALIDATION_FAILED_GENERIC

  @T-UC-006-representation-reselection-enters-review @schema-v3.2 @format-kind @representation-selection
  Scenario: A resync selecting another representation of the same revision is updated and sent back to review
    Given the Buyer is authenticated
    And the tenant has approval_mode "auto-approve"
    And the buyer syncs a creative identified by canonical kind "image" selected from a representation set
    And the creative already exists with identical data
    And the tenant has approval_mode "require-human"
    And the resync selects another representation of the same source revision
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response should include the creative with action "updated"
    And the per-creative result should carry advisory status "pending_review"
    And listing the library returns the creative carrying the representation_selection the buyer last sent

  @T-UC-006-representation-new-revision-keeps-review @schema-v3.2 @format-kind @representation-selection
  Scenario: A resync carrying a new source revision that selects the same output is updated and keeps its approval
    Given the Buyer is authenticated
    And the tenant has approval_mode "auto-approve"
    And the buyer syncs a creative identified by canonical kind "image" selected from a representation set
    And the creative already exists with identical data
    And the tenant has approval_mode "require-human"
    And the resync carries a new source revision that selects the same output
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response should include the creative with action "updated"
    And the per-creative result should carry advisory status "approved"
    And listing the library returns the creative carrying the representation_selection the buyer last sent

    # The two review invariants of representation-sets.mdx § Sync, review, and readback, one
    # scenario each, and they differ in which half of the lineage moves. The execution is
    # (selected_representation_id, selected_output_digest, execution_vast_version,
    # execution_daast_version): "Changing only the selected execution does not mint a buyer
    # revision, but it is an updated creative and follows the ordinary re-review lifecycle;
    # approval never transfers silently to different execution bytes". The first scenario
    # moves only selected_representation_id, with the same output bytes, so a seller that
    # decides review on the assets alone keeps it approved and reddens the row.
    #
    # The source revision is the other half. core/representation-selection.json
    # review_identity tracks the execution "separately from the source revision", and
    # sync-creatives-request.json gives "different_revision_same_current_content" as
    # "accept_as_updated_make_current_preserve_review_state". The second scenario moves only
    # revision_id and its digest: the lineage changed, so the creative is "updated" and lists
    # the new lineage, and the execution did not, so it stays approved. A seller that
    # re-reviews on any lineage change reddens it; one that ignores the lineage answers
    # "unchanged" and lists the first revision.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/representation-sets.mdx section="Sync, review, and readback"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/representation-selection.json pointer=/x-adcp-validation/verifier_constraints/derived_output/review_identity
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/sync-creatives-request.json pointer=/properties/creatives/items/allOf/1/x-adcp-validation

  @T-UC-006-storyboard-creative-reception-stateful-render @uc006-storyboard-routing @schema-v3.1 @v3-1 @stateful-push @creative-reception
  Scenario: Stateful sales agent accepts pushed creatives and exposes them via per-creative status transitions
    Given the Buyer is authenticated
    And a creative with a known format_id
    When the Buyer Agent sends sync_creatives
    Then the response is compliant with the sync_creatives success spec
    And every per-creative result should expose a status field
    And every status value should be drawn from the creative-status enum
    And the per-creative result should carry advisory status "pending_review"
    # A pushed creative is accepted into the library and its per-creative ``status`` is
    # where it sits in review (sync-creatives-response.json). This seller reviews
    # asynchronously by default (require-human), so the status after the sync is
    # pending_review; the synchronous-review value is the auto-approve row of
    # @T-UC-006-boundary-approval. Validation against the format specification is
    # graded by the format-validation partition (an unserved format is a failed entry),
    # and platform_id is "only present when applicable" -- this seller assigns none at
    # sync time, so there is nothing to grade on it here.
    # creative_reception storyboard: a sales agent (publisher, retail media network)
    # accepts pushed creative assets, validates them against format specs, stores them,
    # and exposes per-creative status (approved, pending_review, rejected). Distinct
    # from creative-platform (Innovid/Flashtalking) which carries a full lifecycle;
    # the sales-agent reception is the minimum viable creative-handling contract.
    # creative_reception: stateful sales agent minimum-viable creative reception contract
