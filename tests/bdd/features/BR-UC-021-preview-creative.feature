# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-021 Preview Creative
  As a Buyer (or Buyer Agent)
  I want to generate visual previews of creative manifests in single, batch, or variant mode
  So that I can verify how ads will render before committing them to a media buy

  # Postconditions verified:
  #   POST-S1: Buyer has received preview renders for a single creative
  #   POST-S2: Buyer has received batch results with one result per request in order
  #   POST-S3: Buyer has received a post-flight variant preview showing what was served
  #   POST-S4: Buyer knows when preview URLs will expire via expires_at
  #   POST-S5: Buyer has received embedding metadata for secure iframe integration
  #   POST-S6: Buyer is informed of which input parameters were applied to each preview variant
  #   POST-S7: Buyer can access an interactive testing page via interactive_url when provided
  #   POST-S8: Response type discriminator matches the request type discriminator
  #   POST-S9: Application context from the request is echoed unchanged in the response
  #   POST-F1: System state is unchanged on failure
  #   POST-F2: Buyer knows what failed and the specific error code with recovery classification
  #   POST-F3: In batch mode, individual item failures do not prevent other items from succeeding
  #   POST-F4: Application context is still echoed when possible
  #
  # Rules: BR-RULE-043, BR-RULE-160..168 (10 rules, 44 invariants)
  # Extensions: A (batch), B (variant), C (input variants), D (CREATIVE_MANIFEST_REQUIRED),
  #   E (FORMAT_NOT_FOUND), F (MANIFEST_VALIDATION_ERROR), G (BATCH_LIMIT_EXCEEDED),
  #   H (VARIANT_NOT_FOUND), I (OUTPUT_FORMAT_INVALID), J (CREATIVE_AGENT_UNAVAILABLE)
  # Error codes: CREATIVE_MANIFEST_REQUIRED, FORMAT_NOT_FOUND, MANIFEST_VALIDATION_ERROR,
  #   BATCH_LIMIT_EXCEEDED, VARIANT_NOT_FOUND, OUTPUT_FORMAT_INVALID, CREATIVE_AGENT_UNAVAILABLE,
  #   BATCH_EMPTY, BATCH_REQUESTS_REQUIRED, MANIFEST_FORMAT_ID_REQUIRED, MANIFEST_ASSETS_REQUIRED,
  #   MANIFEST_ASSET_KEY_INVALID, MANIFEST_AGENT_URL_REQUIRED, MANIFEST_DIMENSIONS_INCOMPLETE,
  #   INPUTS_EMPTY, INPUT_NAME_REQUIRED, VARIANT_ID_REQUIRED, REQUEST_TYPE_REQUIRED,
  #   REQUEST_TYPE_INVALID

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And the Buyer is authenticated with a valid principal_id


  @T-UC-021-main-mcp @main-flow @single @mcp @post-s1 @post-s4 @post-s5 @post-s7 @post-s8 @post-s9
  Scenario: Single preview via MCP -- returns preview renders with expiration
    Given a creative manifest with a valid format_id and assets
    And the creative agent at format_id.agent_url is reachable
    And the Buyer Agent has an authenticated connection via MCP
    When the Buyer Agent invokes preview_creative via MCP with request_type "single" and a creative_manifest
    Then the response has response_type "single"
    And the response contains a previews array with at least 1 preview
    And each preview has a preview_id and a renders array with at least 1 render
    And each render has a render_id, output_format, role, and preview_url
    And the response includes an expires_at timestamp in ISO 8601 format
    And the response may include an interactive_url for testing
    And the response may include embedding metadata with sandbox policy and CSP
    And the request context is echoed in the response
    # POST-S1: Buyer received preview renders for a single creative
    # POST-S4: expires_at timestamp present
    # POST-S5: Embedding metadata available
    # POST-S7: interactive_url provided when available
    # POST-S8: response_type = request_type = "single"
    # POST-S9: Context echoed unchanged

  @T-UC-021-main-rest @main-flow @single @rest @post-s1 @post-s4 @post-s8 @post-s9
  Scenario: Single preview via REST/A2A -- returns preview renders with expiration
    Given a creative manifest with a valid format_id and assets
    And the creative agent at format_id.agent_url is reachable
    And the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a POST preview_creative request via REST with request_type "single" and a creative_manifest
    Then the response should succeed
    And the response has response_type "single"
    And the response contains a previews array with at least 1 preview
    And each preview has a preview_id and a renders array
    And the response includes an expires_at timestamp
    And the request context is echoed in the response
    # POST-S1: Buyer received preview renders
    # POST-S4: expires_at present
    # POST-S8: response_type mirrors request_type
    # POST-S9: Context echoed

  @T-UC-021-ext-a-batch @happy-path @ext-a @batch @post-s2 @post-s8 @post-s9
  Scenario: Batch preview -- returns positionally-ordered results
    Given 3 creative manifests with valid format_ids and assets
    And the creative agents are reachable
    When the Buyer Agent invokes preview_creative with request_type "batch" and a requests array of 3 items
    Then the response has response_type "batch"
    And the response contains a results array with exactly 3 items
    And results[0] corresponds to requests[0]
    And results[1] corresponds to requests[1]
    And results[2] corresponds to requests[2]
    And each successful result has success true with a response containing previews and expires_at
    And the request context is echoed in the response
    # POST-S2: Batch results with one result per request in order
    # POST-S8: response_type = "batch" mirrors request_type
    # POST-S9: Context echoed unchanged

  @T-UC-021-ext-a-partial @happy-path @ext-a @batch @partial-failure @post-f3 @post-s9
  Scenario: Batch preview with partial failure -- successful items are not blocked
    Given a batch of 3 creative manifests where item 2 has an invalid format_id
    And the creative agents for items 1 and 3 are reachable
    When the Buyer Agent invokes preview_creative with request_type "batch" and a requests array of 3 items
    Then the response has response_type "batch"
    And the results array has 3 items
    And results[0] has success true with a response
    And results[1] has success false with an errors array
    And results[2] has success true with a response
    And the request context is echoed in the response
    # POST-F3: Individual item failure does not prevent other items from succeeding
    # POST-S9: Context echoed

  @T-UC-021-ext-a-override @happy-path @ext-a @batch @output-override @br-rule-162 @br-rule-164
  Scenario: Batch preview with per-item output_format override
    Given a batch of 2 creative manifests
    And the batch-level output_format is "url"
    And the first item specifies output_format "html"
    When the Buyer Agent invokes preview_creative with request_type "batch"
    Then results[0] renders contain preview_html (overridden to html)
    And results[1] renders contain preview_url (batch default url)
    # BR-RULE-162 INV-6: Batch output_format is default; per-item overrides it
    # BR-RULE-164 INV-5: Per-item output_format takes precedence

  @T-UC-021-ext-b-variant @happy-path @ext-b @variant @post-s3 @post-s8 @post-s9
  Scenario: Variant preview -- returns post-flight variant with rendered manifest
    Given a variant_id "v-served-123" that references an existing delivered creative variant
    When the Buyer Agent invokes preview_creative with request_type "variant" and variant_id "v-served-123"
    Then the response has response_type "variant"
    And the response includes variant_id "v-served-123"
    And the response includes a previews array with renders showing what was served
    And the response includes a manifest object representing the actual served creative
    And the request context is echoed in the response
    # POST-S3: Buyer received post-flight variant preview with rendered manifest
    # POST-S8: response_type = "variant" mirrors request_type
    # POST-S9: Context echoed

  @T-UC-021-ext-b-expires @happy-path @ext-b @variant @expiration @br-rule-165
  Scenario: Variant preview -- expires_at is optional
    Given a variant_id that references an existing delivered creative variant
    When the Buyer Agent invokes preview_creative with request_type "variant"
    Then the response may or may not include an expires_at timestamp
    And the variant preview is still valid regardless of expires_at presence
    # BR-RULE-165 INV-3: Variant mode response has optional expires_at

  @T-UC-021-ext-b-creative-id @happy-path @ext-b @variant @br-rule-163
  Scenario: Variant preview with creative_id context
    Given a variant_id "v-abc" that references a variant for creative "c-parent-123"
    When the Buyer Agent invokes preview_creative with request_type "variant", variant_id "v-abc", and creative_id "c-parent-123"
    Then the response includes variant_id "v-abc"
    And the response includes creative_id "c-parent-123"
    And the response includes the rendered manifest
    # BR-RULE-163 INV-2: Variant exists -> response includes variant_id, previews, and manifest

  @T-UC-021-ext-c-inputs @happy-path @ext-c @inputs @post-s1 @post-s6
  Scenario: Input variant previews -- one preview per input set with echo
    Given a creative manifest with a valid format_id and assets
    And an inputs array with 3 items named "Desktop", "Mobile", and "Tablet"
    And the "Desktop" input has macros {"device": "desktop"} and context_description "User browsing on desktop"
    When the Buyer Agent invokes preview_creative with request_type "single", creative_manifest, and inputs
    Then the response contains exactly 3 previews
    And the first preview echoes input name "Desktop" with macros {"device": "desktop"} and context_description
    And the second preview echoes input name "Mobile"
    And the third preview echoes input name "Tablet"
    # BR-RULE-166 INV-3: inputs with N items -> response contains exactly N previews
    # BR-RULE-166 INV-5: macros/context_description echoed in response input object
    # POST-S1: Buyer received one preview per input variant
    # POST-S6: Buyer knows which input parameters were applied to each preview

  @T-UC-021-ext-c-default @happy-path @ext-c @inputs @no-inputs @br-rule-166
  Scenario: No inputs provided -- creative agent generates single default preview
    Given a creative manifest with a valid format_id and assets
    And no inputs array is provided
    When the Buyer Agent invokes preview_creative with request_type "single" and creative_manifest only
    Then the response contains exactly 1 preview
    And the preview does not include an input echo object
    # BR-RULE-166 INV-4: inputs omitted -> creative agent produces one default preview

  @T-UC-021-ext-d @extension @ext-d @error @post-f1 @post-f2
  Scenario: Single mode without creative_manifest -- CREATIVE_MANIFEST_REQUIRED
    Given request_type is "single" but creative_manifest is absent
    When the Buyer Agent invokes preview_creative
    Then the operation should fail
    And the error code should be "CREATIVE_MANIFEST_REQUIRED"
    And the error field should be "creative_manifest"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "creative_manifest"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows creative_manifest is required
    # POST-F3: Suggestion for recovery

  @T-UC-021-ext-d-batch @extension @ext-d @error @batch @post-f3
  Scenario: Batch mode with missing creative_manifest in one item -- per-item error
    Given a batch of 2 items where item 1 is valid and item 2 is missing creative_manifest
    When the Buyer Agent invokes preview_creative with request_type "batch"
    Then results[0] has success true
    And results[1] has success false
    And results[1] errors include code "CREATIVE_MANIFEST_REQUIRED"
    And results[1] errors include field "requests[1].creative_manifest"
    And the error should include "suggestion" field
    And the suggestion should contain "creative_manifest"
    # POST-F3: Item 1 succeeds despite item 2 failure

  @T-UC-021-ext-e @extension @ext-e @error @post-f1 @post-f2
  Scenario: Format not found in creative agent registry -- FORMAT_NOT_FOUND
    Given a creative manifest with format_id.id "nonexistent_format" and a valid agent_url
    And the creative agent does not support the format "nonexistent_format"
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the operation should fail
    And the error code should be "FORMAT_NOT_FOUND"
    And the error message should contain "nonexistent_format"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "format"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows which format was not found
    # POST-F3: Suggestion for recovery

  @T-UC-021-ext-f-format-id @extension @ext-f @error @post-f1 @post-f2 @br-rule-161
  Scenario: Manifest missing format_id -- MANIFEST_VALIDATION_ERROR
    Given a creative manifest without a format_id field
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the operation should fail
    And the error code should be "MANIFEST_VALIDATION_ERROR"
    And the error field should contain "creative_manifest.format_id"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "format_id"
    # BR-RULE-161 INV-1: manifest must contain format_id and assets
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows what failed in the manifest
    # POST-F3: Suggestion for recovery

  @T-UC-021-ext-f-assets @extension @ext-f @error @post-f1 @post-f2 @br-rule-161
  Scenario: Manifest missing assets -- MANIFEST_VALIDATION_ERROR
    Given a creative manifest with format_id but without assets
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the operation should fail
    And the error code should be "MANIFEST_VALIDATION_ERROR"
    And the error field should contain "creative_manifest.assets"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "assets"
    # BR-RULE-161 INV-1: manifest must contain format_id and assets
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows assets are required
    # POST-F3: Suggestion for recovery

  @T-UC-021-ext-f-agent-url @extension @ext-f @error @br-rule-161
  Scenario: Manifest format_id missing agent_url -- MANIFEST_VALIDATION_ERROR
    Given a creative manifest with format_id containing id but no agent_url
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the operation should fail
    And the error code should be "MANIFEST_VALIDATION_ERROR"
    And the error field should contain "creative_manifest.format_id.agent_url"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "agent_url"
    # BR-RULE-161 INV-2: format_id must contain agent_url and id
    # POST-F3: Suggestion for recovery

  @T-UC-021-ext-f-dimensions @extension @ext-f @error @br-rule-161
  Scenario: Manifest format_id with width but no height -- MANIFEST_VALIDATION_ERROR
    Given a creative manifest with format_id containing width 300 but no height
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the operation should fail
    And the error code should be "MANIFEST_VALIDATION_ERROR"
    And the error message should contain "width" and "height"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "both width and height"
    # BR-RULE-161 INV-3: width and height are co-dependent (both or neither)
    # POST-F3: Suggestion for recovery

  @T-UC-021-ext-f-asset-key @extension @ext-f @error @br-rule-161
  Scenario: Manifest with invalid asset key pattern -- MANIFEST_VALIDATION_ERROR
    Given a creative manifest with asset key "Banner-Image" containing uppercase and hyphen
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the operation should fail
    And the error code should be "MANIFEST_VALIDATION_ERROR"
    And the error message should contain "Banner-Image"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "lowercase"
    # BR-RULE-161 INV-4: asset keys must match ^[a-z0-9_]+$
    # POST-F3: Suggestion for recovery

  @T-UC-021-ext-g @extension @ext-g @error @batch @post-f1 @post-f2
  Scenario: Batch with more than 50 items -- BATCH_LIMIT_EXCEEDED
    Given a batch preview request with 51 items in the requests array
    When the Buyer Agent invokes preview_creative with request_type "batch"
    Then the operation should fail
    And the error code should be "BATCH_LIMIT_EXCEEDED"
    And the error message should contain "50"
    And the error field should be "requests"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "split"
    # POST-F1: System state unchanged -- entire batch rejected before processing
    # POST-F2: Buyer knows the batch limit and actual count
    # POST-F3: Suggestion for recovery

  @T-UC-021-ext-h @extension @ext-h @error @variant @post-f1 @post-f2
  Scenario: Variant not found -- VARIANT_NOT_FOUND
    Given a variant_id "v-nonexistent" that does not exist in the system
    When the Buyer Agent invokes preview_creative with request_type "variant" and variant_id "v-nonexistent"
    Then the operation should fail
    And the error code should be "VARIANT_NOT_FOUND"
    And the error message should contain "v-nonexistent"
    And the error field should be "variant_id"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "get_creative_delivery"
    # BR-RULE-163 INV-3: variant does not exist -> VARIANT_NOT_FOUND
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows the variant was not found
    # POST-F3: Suggestion for recovery

  @T-UC-021-ext-h-expired @extension @ext-h @error @variant @br-rule-163
  Scenario: Variant expired -- VARIANT_NOT_FOUND
    Given a variant_id "v-expired" that references a variant whose data has expired
    When the Buyer Agent invokes preview_creative with request_type "variant" and variant_id "v-expired"
    Then the operation should fail
    And the error code should be "VARIANT_NOT_FOUND"
    And the error message should contain "expired"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "variant"
    # BR-RULE-163 INV-3: variant expired -> VARIANT_NOT_FOUND
    # POST-F3: Suggestion for recovery

  @T-UC-021-ext-i @extension @ext-i @error @post-f1 @post-f2
  Scenario: Invalid output format -- OUTPUT_FORMAT_INVALID
    Given a valid creative manifest and output_format "pdf"
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the operation should fail
    And the error code should be "OUTPUT_FORMAT_INVALID"
    And the error message should contain "url" and "html"
    And the error field should be "output_format"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "url"
    # BR-RULE-164 INV-4: output_format not in [url, html] -> rejected
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows the allowed values
    # POST-F3: Suggestion for recovery

  @T-UC-021-ext-j @extension @ext-j @error @post-f1 @post-f2
  Scenario: Creative agent unreachable -- CREATIVE_AGENT_UNAVAILABLE
    Given a creative manifest with format_id pointing to an unreachable creative agent
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the operation should fail
    And the error code should be "CREATIVE_AGENT_UNAVAILABLE"
    And the error message should contain the agent URL
    And the error recovery should be "transient"
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    # BR-RULE-168 INV-3: agent unreachable/timeout/error -> CREATIVE_AGENT_UNAVAILABLE
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows the agent is unavailable and receives retry guidance
    # POST-F3: Suggestion for recovery

  @T-UC-021-ext-j-batch @extension @ext-j @error @batch @br-rule-168
  Scenario: Creative agent unavailable in batch -- per-item error with other items succeeding
    Given a batch of 2 items where item 1 uses a reachable agent and item 2 uses an unreachable agent
    When the Buyer Agent invokes preview_creative with request_type "batch"
    Then results[0] has success true with a response
    And results[1] has success false
    And results[1] errors include code "CREATIVE_AGENT_UNAVAILABLE"
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    # BR-RULE-168 INV-3 in batch context
    # POST-F3: Item 1 succeeds despite item 2 agent failure

  @T-UC-021-ext-j-timeout @extension @ext-j @error @timeout @br-rule-168
  Scenario: Creative agent timeout -- CREATIVE_AGENT_UNAVAILABLE after 30s
    Given a creative manifest with format_id pointing to a creative agent that responds slowly
    And the creative agent does not respond within 30 seconds
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the operation should fail
    And the error code should be "CREATIVE_AGENT_UNAVAILABLE"
    And the error recovery should be "transient"
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    # BR-RULE-168 INV-3: timeout -> CREATIVE_AGENT_UNAVAILABLE with retry
    # POST-F3: Suggestion for recovery

  @T-UC-021-discriminator @invariant @BR-RULE-160 @discriminator
  Scenario Outline: Request type discriminator selects correct mode -- <request_type>
    Given a valid preview_creative request with request_type "<request_type>"
    And the required fields for <request_type> mode are present
    When the Buyer Agent invokes preview_creative
    Then the response has response_type "<request_type>"
    And the response contains the expected structure for <request_type> mode
    # BR-RULE-160 INV-1/2/3: Each request_type activates different required fields
    # BR-RULE-160 INV-4: response_type = request_type

    Examples:
      | request_type |
      | single       |
      | batch        |
      | variant      |

  @T-UC-021-discriminator-invalid @invariant @BR-RULE-160 @error @discriminator
  Scenario: Unknown request type -- rejected before processing
    Given a preview_creative request with request_type "unknown_type"
    When the Buyer Agent invokes preview_creative
    Then the operation should fail
    And the error should include "suggestion" field
    And the suggestion should contain "single" or "batch" or "variant"
    # BR-RULE-160 INV-5: request_type unknown -> rejected
    # POST-F3: Suggestion for recovery

  @T-UC-021-discriminator-missing @invariant @BR-RULE-160 @error @discriminator
  Scenario: Missing request type -- rejected before processing
    Given a preview_creative request without request_type field
    When the Buyer Agent invokes preview_creative
    Then the operation should fail
    And the error should include "suggestion" field
    And the suggestion should contain "request_type"
    # BR-RULE-160 INV-5: request_type missing -> rejected
    # POST-F3: Suggestion for recovery

  @T-UC-021-output-url @invariant @BR-RULE-164 @output-format
  Scenario: Output format url -- renders include preview_url
    Given a valid single preview request with output_format "url"
    When the Buyer Agent invokes preview_creative
    Then each render has output_format "url" and includes preview_url
    And each render does not include preview_html
    # BR-RULE-164 INV-2: output_format = url -> renders include preview_url
    # BR-RULE-167 INV-3: render output_format = url -> preview_url required

  @T-UC-021-output-html @invariant @BR-RULE-164 @output-format
  Scenario: Output format html -- renders include preview_html
    Given a valid single preview request with output_format "html"
    When the Buyer Agent invokes preview_creative
    Then each render has output_format "html" and includes preview_html
    And each render does not include preview_url
    # BR-RULE-164 INV-3: output_format = html -> renders include preview_html
    # BR-RULE-167 INV-4: render output_format = html -> preview_html required

  @T-UC-021-output-default @invariant @BR-RULE-164 @output-format
  Scenario: Output format omitted -- defaults to url
    Given a valid single preview request with no output_format specified
    When the Buyer Agent invokes preview_creative
    Then each render has output_format "url" and includes preview_url
    # BR-RULE-164 INV-1: output_format omitted -> defaults to url

  @T-UC-021-output-both @invariant @BR-RULE-167 @output-format
  Scenario: Render-level output_format both -- includes url and html
    Given a creative agent that provides both url and html for a format
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the render has output_format "both"
    And the render includes both preview_url and preview_html
    # BR-RULE-167 INV-5: render output_format = both -> both preview_url and preview_html

  @T-UC-021-multi-render @invariant @BR-RULE-167 @multi-render
  Scenario: Multi-render format -- companion ad produces multiple renders with roles
    Given a creative manifest for a companion ad format (video + banner)
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the preview contains a renders array with at least 2 items
    And one render has role "primary" (e.g., video)
    And another render has role "companion" (e.g., banner)
    And each render has a unique render_id
    # BR-RULE-167 INV-1: renders array has >= 1 item
    # BR-RULE-167 INV-2: each render has unique render_id and semantic role

  @T-UC-021-render-dimensions @invariant @BR-RULE-167 @multi-render
  Scenario: Render with dimensions -- both width and height present
    Given a creative agent that returns renders with dimension information
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then each render with dimensions has both width and height values
    # BR-RULE-167 INV-6: render includes dimensions -> both width and height must be present

  @T-UC-021-render-custom-role @invariant @BR-RULE-167 @multi-render
  Scenario: Render with custom role string -- accepted as valid role
    Given a creative agent that returns a render with a custom role "sidebar"
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the render has role "sidebar"
    And the render has a unique render_id
    # BR-RULE-167 INV-2: each render has a semantic role (no enum restriction)

  @T-UC-021-expiration-single @invariant @BR-RULE-165 @expiration
  Scenario: Single mode -- expires_at is required
    Given a valid single preview request
    When the Buyer Agent invokes preview_creative
    Then the response includes a required expires_at timestamp in ISO 8601 format
    # BR-RULE-165 INV-1: single mode response requires expires_at

  @T-UC-021-expiration-batch @invariant @BR-RULE-165 @expiration @batch
  Scenario: Batch mode -- each successful result includes expires_at
    Given a batch of 2 valid creative manifests
    When the Buyer Agent invokes preview_creative with request_type "batch"
    Then each successful result in the results array includes expires_at
    # BR-RULE-165 INV-2: batch per-result response requires expires_at

  @T-UC-021-expiration-variant @invariant @BR-RULE-165 @expiration @variant
  Scenario: Variant mode -- expires_at is optional
    Given a valid variant preview request
    When the Buyer Agent invokes preview_creative with request_type "variant"
    Then the response may or may not include an expires_at timestamp
    # BR-RULE-165 INV-3: variant mode -> expires_at is optional

  @T-UC-021-delegation @invariant @BR-RULE-168 @delegation
  Scenario: Agent delegation -- resolves from format_id.agent_url
    Given a creative manifest with format_id.agent_url "https://creative.example.com"
    And the creative agent at "https://creative.example.com" is registered and reachable
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the system delegates rendering to the creative agent at "https://creative.example.com"
    And the response contains preview renders from that agent
    # BR-RULE-168 INV-1: preview request -> resolve creative agent from format_id.agent_url

  @T-UC-021-delegation-tenant @invariant @BR-RULE-168 @delegation
  Scenario: Tenant-specific agent -- custom agents sorted by priority
    Given the tenant has a custom creative agent configured with higher priority
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the system uses the tenant's custom agent over the default agent
    # BR-RULE-168 INV-4: tenant has custom agents -> loaded from database, sorted by priority

  @T-UC-021-delegation-default @invariant @BR-RULE-168 @delegation
  Scenario: Default agent fallback -- no custom agents for tenant
    Given the tenant has no custom creative agents configured
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the system uses the default creative agent
    # BR-RULE-168 INV-5: no custom agents -> default creative agent used

  @T-UC-021-delegation-error-response @invariant @BR-RULE-168 @error @delegation
  Scenario: Agent returns error response -- CREATIVE_AGENT_UNAVAILABLE
    Given a creative agent that is reachable but returns an error status
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the operation should fail
    And the error code should be "CREATIVE_AGENT_UNAVAILABLE"
    And the error recovery should be "transient"
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    # BR-RULE-168 INV-3: agent returns error -> CREATIVE_AGENT_UNAVAILABLE
    # POST-F3: Suggestion for recovery

  @T-UC-021-context-echo @invariant @BR-RULE-043 @context-echo
  Scenario: Context provided -- echoed unchanged in response
    Given a valid single preview request with context {"session_id": "abc-123", "trace": true}
    When the Buyer Agent invokes preview_creative
    Then the response context is {"session_id": "abc-123", "trace": true}
    # BR-RULE-043 INV-1: request includes context -> response includes identical context

  @T-UC-021-context-omit @invariant @BR-RULE-043 @context-echo
  Scenario: Context omitted -- response also omits context
    Given a valid single preview request without a context field
    When the Buyer Agent invokes preview_creative
    Then the response does not include a context field
    # BR-RULE-043 INV-2: request omits context -> response omits context

  @T-UC-021-context-error @invariant @BR-RULE-043 @error @context-echo
  Scenario: Context echoed on error response when possible
    Given a preview request with context {"trace": "err-test"} but missing creative_manifest
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the operation should fail
    And the error code should be "CREATIVE_MANIFEST_REQUIRED"
    And the response context is {"trace": "err-test"} when possible
    And the error should include "suggestion" field
    And the suggestion should contain "creative_manifest"
    # POST-F4: Application context is still echoed when possible
    # POST-F4: Context echoed on error path
    # POST-F3: Suggestion for recovery

  @T-UC-021-partition-batch @partition @batch_constraints
  Scenario Outline: Batch constraints partition validation -- <partition>
    Given a preview_creative request with request_type "batch"
    And the batch is configured as <partition>
    When the Buyer Agent invokes preview_creative
    Then the <outcome>

    Examples: Valid partitions
      | partition          | outcome                                                  |
      | single_item_batch  | response contains a results array with 1 item            |
      | typical_batch      | response contains a results array matching request count  |
      | max_batch          | response contains a results array with 50 items           |
      | partial_failure    | results include both success and error items              |

    Examples: Invalid partitions
      | partition          | outcome                                                              |
      | empty_batch        | error "BATCH_EMPTY" with suggestion "Add at least one"               |
      | over_limit         | error "BATCH_LIMIT_EXCEEDED" with suggestion "split"                 |
      | missing_requests   | error "BATCH_REQUESTS_REQUIRED" with suggestion "Include a requests" |

  @T-UC-021-boundary-batch @boundary @batch_constraints
  Scenario Outline: Batch constraints boundary validation -- <boundary_point>
    Given a preview_creative request with request_type "batch"
    And the batch is configured for boundary <boundary_point>
    When the Buyer Agent invokes preview_creative
    Then the <outcome>

    Examples: Boundary values
      | boundary_point                                         | outcome                                                              |
      | requests array with 1 item (minItems)                  | response contains a results array with 1 item                       |
      | requests array with 50 items (maxItems)                | response contains a results array with 50 items                      |
      | requests array with 0 items                            | error "BATCH_EMPTY" with suggestion "Add at least one"               |
      | requests array with 51 items                           | error "BATCH_LIMIT_EXCEEDED" with suggestion "split"                 |
      | requests array absent in batch mode                    | error "BATCH_REQUESTS_REQUIRED" with suggestion "Include a requests" |
      | results[0] maps to requests[0] (positional check)      | results maintain positional correspondence                           |
      | result with success=true and response present          | result contains response with previews and expires_at                |
      | result with success=false and errors present           | result contains errors array with at least one error                 |

  @T-UC-021-partition-manifest @partition @manifest_validity
  Scenario Outline: Manifest validity partition validation -- <partition>
    Given a creative manifest configured as <partition>
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the <outcome>

    Examples: Valid partitions
      | partition                | outcome                                                    |
      | complete_manifest        | preview renders are returned successfully                  |
      | manifest_with_dimensions | preview renders are returned with dimension context        |
      | manifest_with_provenance | preview renders are returned with provenance metadata      |

    Examples: Invalid partitions
      | partition            | outcome                                                                    |
      | missing_format_id    | error "MANIFEST_VALIDATION_ERROR" with suggestion "format_id"              |
      | missing_assets       | error "MANIFEST_VALIDATION_ERROR" with suggestion "assets"                 |
      | invalid_asset_key    | error "MANIFEST_VALIDATION_ERROR" with suggestion "lowercase"              |
      | missing_agent_url    | error "MANIFEST_VALIDATION_ERROR" with suggestion "agent_url"              |
      | width_without_height | error "MANIFEST_VALIDATION_ERROR" with suggestion "both width and height"  |

  @T-UC-021-boundary-manifest @boundary @manifest_validity
  Scenario Outline: Manifest validity boundary validation -- <boundary_point>
    Given a creative manifest configured for boundary <boundary_point>
    When the Buyer Agent invokes preview_creative with request_type "single"
    Then the <outcome>

    Examples: Boundary values
      | boundary_point                                    | outcome                                                                    |
      | manifest with format_id + assets (minimal valid)  | preview renders are returned successfully                                  |
      | manifest missing format_id                        | error "MANIFEST_VALIDATION_ERROR" with suggestion "format_id"              |
      | manifest missing assets                           | error "MANIFEST_VALIDATION_ERROR" with suggestion "assets"                 |
      | manifest with empty assets object {}              | preview renders are returned (empty assets is valid)                       |
      | asset key 'a' (minimal valid pattern)             | preview renders are returned successfully                                  |
      | asset key 'Banner-Image' (uppercase + hyphen)     | error "MANIFEST_VALIDATION_ERROR" with suggestion "lowercase"              |
      | format_id with width=1, height=1 (minimum dimensions) | preview renders are returned with dimensions                           |
      | format_id with width=0 (below minimum)            | error "MANIFEST_VALIDATION_ERROR" with suggestion                          |
      | format_id with width but no height                | error "MANIFEST_VALIDATION_ERROR" with suggestion "both width and height"  |

  @T-UC-021-partition-output @partition @output_format
  Scenario Outline: Output format partition validation -- <partition>
    Given a valid preview_creative request with output_format configured as <partition>
    When the Buyer Agent invokes preview_creative
    Then the <outcome>

    Examples: Valid partitions
      | partition       | outcome                                          |
      | url_format      | renders include preview_url                      |
      | html_format     | renders include preview_html                     |
      | omitted_default | renders include preview_url (default)            |
      | batch_override  | item overrides batch-level default               |

    Examples: Invalid partitions
      | partition       | outcome                                                                  |
      | unknown_format  | error "OUTPUT_FORMAT_INVALID" with suggestion "url"                      |
      | empty_format    | error "OUTPUT_FORMAT_INVALID" with suggestion "url"                      |

  @T-UC-021-boundary-output @boundary @output_format
  Scenario Outline: Output format boundary validation -- <boundary_point>
    Given a valid preview_creative request with output_format at boundary <boundary_point>
    When the Buyer Agent invokes preview_creative
    Then the <outcome>

    Examples: Boundary values
      | boundary_point                                                    | outcome                                          |
      | output_format = 'url'                                             | renders include preview_url                      |
      | output_format = 'html'                                            | renders include preview_html                     |
      | output_format omitted                                             | renders include preview_url (default)            |
      | output_format = 'pdf' (unknown)                                   | error "OUTPUT_FORMAT_INVALID" with suggestion    |
      | output_format = '' (empty string)                                 | error "OUTPUT_FORMAT_INVALID" with suggestion    |
      | batch output_format = 'url', item output_format = 'html' (override) | item renders include preview_html             |
      | render output_format = 'both' (render-level)                      | render includes both preview_url and preview_html |

  @T-UC-021-partition-input @partition @input_variant
  Scenario Outline: Input variant partition validation -- <partition>
    Given a single-mode preview request with inputs configured as <partition>
    When the Buyer Agent invokes preview_creative
    Then the <outcome>

    Examples: Valid partitions
      | partition       | outcome                                          |
      | single_input    | response contains exactly 1 preview              |
      | multiple_inputs | response contains one preview per input           |
      | with_context    | response echoes context_description in input      |
      | inputs_omitted  | response contains 1 default preview              |

    Examples: Invalid partitions
      | partition     | outcome                                                                  |
      | empty_inputs  | error "INPUTS_EMPTY" with suggestion "at least one"                      |
      | missing_name  | error "INPUT_NAME_REQUIRED" with suggestion "name"                       |

  @T-UC-021-boundary-input @boundary @input_variant
  Scenario Outline: Input variant boundary validation -- <boundary_point>
    Given a single-mode preview request with inputs at boundary <boundary_point>
    When the Buyer Agent invokes preview_creative
    Then the <outcome>

    Examples: Boundary values
      | boundary_point                              | outcome                                                      |
      | inputs array with 1 item (minItems)         | response contains exactly 1 preview                          |
      | inputs array with 0 items                   | error "INPUTS_EMPTY" with suggestion "at least one"          |
      | inputs omitted entirely                     | response contains 1 default preview                          |
      | input with name only (minimal)              | response contains preview with echoed input name             |
      | input without name                          | error "INPUT_NAME_REQUIRED" with suggestion "name"           |
      | response input echoes request input name    | preview input object contains the original name              |
      | response input echoes request macros        | preview input object contains the original macros            |

  @T-UC-021-partition-variant @partition @variant_constraints
  Scenario Outline: Variant constraints partition validation -- <partition>
    Given a variant-mode preview request configured as <partition>
    When the Buyer Agent invokes preview_creative
    Then the <outcome>

    Examples: Valid partitions
      | partition             | outcome                                                      |
      | variant_with_creative | response includes variant_id and creative_id and manifest    |
      | variant_minimal       | response includes variant_id and manifest                    |

    Examples: Invalid partitions
      | partition          | outcome                                                                      |
      | missing_variant_id | error "VARIANT_ID_REQUIRED" with suggestion "variant_id"                     |
      | variant_not_found  | error "VARIANT_NOT_FOUND" with suggestion "get_creative_delivery"            |

  @T-UC-021-boundary-variant @boundary @variant_constraints
  Scenario Outline: Variant constraints boundary validation -- <boundary_point>
    Given a variant-mode preview request at boundary <boundary_point>
    When the Buyer Agent invokes preview_creative
    Then the <outcome>

    Examples: Boundary values
      | boundary_point                              | outcome                                                                  |
      | variant_id present and valid                | response includes variant_id, previews, and manifest                     |
      | variant_id present, creative_id omitted     | response includes variant_id and manifest without creative_id            |
      | variant_id missing in variant mode          | error "VARIANT_ID_REQUIRED" with suggestion "variant_id"                 |
      | variant_id references non-existent variant  | error "VARIANT_NOT_FOUND" with suggestion "get_creative_delivery"        |
      | variant_id references expired variant       | error "VARIANT_NOT_FOUND" with suggestion "get_creative_delivery"        |

  @T-UC-021-partition-discriminator @partition @type_discriminator
  Scenario Outline: Type discriminator partition validation -- <partition>
    Given a preview_creative request configured as <partition>
    When the Buyer Agent invokes preview_creative
    Then the <outcome>

    Examples: Valid partitions
      | partition    | outcome                                          |
      | single_mode  | response has response_type "single" with previews |
      | batch_mode   | response has response_type "batch" with results   |
      | variant_mode | response has response_type "variant" with manifest |

    Examples: Invalid partitions
      | partition              | outcome                                                              |
      | missing_discriminator  | error "REQUEST_TYPE_REQUIRED" with suggestion "request_type"         |
      | unknown_value          | error "REQUEST_TYPE_INVALID" with suggestion "single"                |

  @T-UC-021-boundary-discriminator @boundary @type_discriminator
  Scenario Outline: Type discriminator boundary validation -- <boundary_point>
    Given a preview_creative request at boundary <boundary_point>
    When the Buyer Agent invokes preview_creative
    Then the <outcome>

    Examples: Boundary values
      | boundary_point                                         | outcome                                                              |
      | request_type = 'single'                                | response has response_type "single"                                  |
      | request_type = 'batch'                                 | response has response_type "batch"                                   |
      | request_type = 'variant'                               | response has response_type "variant"                                 |
      | request_type missing                                   | error "REQUEST_TYPE_REQUIRED" with suggestion "request_type"         |
      | request_type = 'unknown_value'                         | error "REQUEST_TYPE_INVALID" with suggestion "single"                |
      | response_type matches request_type (single->single)    | response_type equals request_type                                    |
      | response_type mismatches request_type                  | protocol violation -- response_type must mirror request_type         |

  @T-UC-021-partition-expiration @partition @expiration
  Scenario Outline: Expiration partition validation -- <partition>
    Given a preview response with expiration configured as <partition>
    When the Buyer Agent receives the preview_creative response
    Then the <outcome>

    Examples: Valid partitions
      | partition         | outcome                                                  |
      | future_expiration | expires_at is a future ISO 8601 timestamp                |
      | short_ttl         | expires_at is a few minutes in the future                |
      | long_ttl          | expires_at is days in the future                         |

    Examples: Invalid partitions
      | partition            | outcome                                                              |
      | missing_in_single    | server-side error: expires_at required in single response            |
      | malformed_timestamp  | server-side error: expires_at must be valid ISO 8601                 |

  @T-UC-021-boundary-expiration @boundary @expiration
  Scenario Outline: Expiration boundary validation -- <boundary_point>
    Given a preview response with expiration at boundary <boundary_point>
    When the Buyer Agent receives the preview_creative response
    Then the <outcome>

    Examples: Boundary values
      | boundary_point                                       | outcome                                                              |
      | expires_at set to future ISO 8601 timestamp          | valid expiration -- preview URLs usable until expires_at             |
      | expires_at missing from single mode response         | server-side error: expires_at required in single mode               |
      | expires_at missing from variant mode response        | valid -- expires_at optional in variant mode                        |
      | expires_at with non-ISO format                       | server-side error: invalid timestamp format                         |
      | expires_at in past (already expired at response time) | valid response but preview URLs already invalid on receipt          |

  @T-UC-021-partition-multi-render @partition @multi_render
  Scenario Outline: Multi-render partition validation -- <partition>
    Given a preview response with renders configured as <partition>
    When the Buyer Agent receives the preview_creative response
    Then the <outcome>

    Examples: Valid partitions
      | partition                 | outcome                                          |
      | single_render             | renders array has 1 item with role and render_id  |
      | multi_render_companion    | renders array has 2+ items with distinct roles    |
      | render_with_dimensions    | render includes width and height                  |
      | render_with_embedding     | render includes embedding metadata                |

    Examples: Invalid partitions
      | partition          | outcome                                                  |
      | empty_renders      | server-side error: renders must have at least 1 item     |
      | missing_render_id  | server-side error: each render must have render_id       |
      | missing_role       | server-side error: each render must have role            |

  @T-UC-021-boundary-multi-render @boundary @multi_render
  Scenario Outline: Multi-render boundary validation -- <boundary_point>
    Given a preview response with renders at boundary <boundary_point>
    When the Buyer Agent receives the preview_creative response
    Then the <outcome>

    Examples: Boundary values
      | boundary_point                                      | outcome                                                      |
      | renders array with 1 item (minItems)                | valid: single render with render_id and role                 |
      | renders array with 0 items                          | server-side error: renders array empty                       |
      | renders with 2+ items (multi-render)                | valid: multiple renders with distinct render_ids and roles   |
      | render with role = 'primary'                        | valid: primary render role                                   |
      | render with role = 'companion'                      | valid: companion render role                                 |
      | render with custom role string                      | valid: custom role accepted                                  |
      | render output_format = 'url' with preview_url       | valid: url render has preview_url                            |
      | render output_format = 'html' with preview_html     | valid: html render has preview_html                          |
      | render output_format = 'both' with both fields      | valid: both render has preview_url and preview_html          |
      | dimensions with width=0 (below minimum)             | server-side error: width must be >= 1                        |

  @T-UC-021-partition-delegation @partition @agent_delegation
  Scenario Outline: Agent delegation partition validation -- <partition>
    Given a preview request with agent delegation configured as <partition>
    When the Buyer Agent invokes preview_creative
    Then the <outcome>

    Examples: Valid partitions
      | partition              | outcome                                          |
      | default_agent          | renders returned from default creative agent      |
      | custom_agent           | renders returned from tenant custom agent         |
      | agent_returns_previews | response contains preview renders                 |

    Examples: Invalid partitions
      | partition          | outcome                                                                      |
      | agent_unreachable  | error "CREATIVE_AGENT_UNAVAILABLE" with suggestion "Retry"                   |
      | agent_timeout      | error "CREATIVE_AGENT_UNAVAILABLE" with suggestion "Retry"                   |
      | agent_error        | error "CREATIVE_AGENT_UNAVAILABLE" with suggestion "Retry"                   |

  @T-UC-021-boundary-delegation @boundary @agent_delegation
  Scenario Outline: Agent delegation boundary validation -- <boundary_point>
    Given a preview request with agent delegation at boundary <boundary_point>
    When the Buyer Agent invokes preview_creative
    Then the <outcome>

    Examples: Boundary values
      | boundary_point                                       | outcome                                                              |
      | agent_url = default creative agent (always reachable) | renders returned from default agent                                 |
      | agent_url = tenant-specific agent (reachable)        | renders returned from tenant agent                                   |
      | agent_url = unreachable endpoint                     | error "CREATIVE_AGENT_UNAVAILABLE" with suggestion "Retry"           |
      | agent responds but exceeds 30-second timeout         | error "CREATIVE_AGENT_UNAVAILABLE" with suggestion "Retry"           |
      | agent returns error response                         | error "CREATIVE_AGENT_UNAVAILABLE" with suggestion "Retry"           |
      | agent returns empty previews (no renders)            | server-side error: renders must have at least 1 item                 |

