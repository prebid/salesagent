# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-020 Build Creative
  As a Buyer (Human or AI Agent)
  I want to request AI-powered generation or transformation of a creative manifest
  So that I can obtain a creative ready for preview or sync in my target format

  # Postconditions verified:
  #   POST-S1: Buyer knows the generated or transformed creative manifest is ready for preview or sync
  #   POST-S2: Buyer knows the output manifest's format_id matches the requested target_format_id
  #   POST-S3: Buyer knows the output manifest contains all required assets for the target format
  #   POST-S4: Buyer knows the provenance metadata on the output manifest reflects AI generation
  #   POST-F1: Buyer knows the operation failed
  #   POST-F2: Buyer knows what went wrong (error explains the failure)
  #   POST-F3: Buyer knows how to recover (suggestion for corrective action)

  Background:
    Given a Seller Agent is operational and accepting requests
    And at least one creative agent is registered and reachable


  @T-UC-020-main-rest @main-flow @rest @post-s1 @post-s2 @post-s3 @post-s4
  Scenario: Build creative via REST - pure generation with minimal request
    Given the Buyer has a target_format_id with agent_url "https://creative.example.com" and id "display_300x250"
    And the creative agent supports the requested format
    When the Buyer Agent sends a build_creative request via A2A with target_format_id and message "Create a display banner"
    Then the response should be a successful BuildCreativeResponse
    And the response should contain a creative_manifest
    And the output creative_manifest format_id should match the requested target_format_id
    And the output creative_manifest should contain all required assets for the target format
    And the output creative_manifest should include provenance metadata
    # POST-S1: Creative manifest ready for preview or sync
    # POST-S2: Output format_id matches target_format_id
    # POST-S3: All required assets present
    # POST-S4: Provenance metadata reflects AI generation

  @T-UC-020-main-mcp @main-flow @mcp @post-s1 @post-s2 @post-s3 @post-s4
  Scenario: Build creative via MCP - pure generation with minimal request
    Given the Buyer has a target_format_id with agent_url "https://creative.example.com" and id "display_300x250"
    And the creative agent supports the requested format
    When the Buyer Agent calls build_creative MCP tool with target_format_id and message "Create a display banner"
    Then the response should be a successful BuildCreativeResponse
    And the response should contain a creative_manifest
    And the output creative_manifest format_id should match the requested target_format_id
    And the output creative_manifest should contain all required assets for the target format
    And the output creative_manifest should include provenance metadata
    # POST-S1: Creative manifest ready for preview or sync
    # POST-S2: Output format_id matches target_format_id
    # POST-S3: All required assets present
    # POST-S4: Provenance metadata reflects AI generation

  @T-UC-020-main-transform @main-flow @rest @post-s1 @post-s2
  Scenario: Build creative via REST - transformation mode
    Given the Buyer has an existing creative_manifest for format "display_728x90"
    And the Buyer has a target_format_id with agent_url "https://creative.example.com" and id "display_300x250"
    And the creative agent supports the requested format
    When the Buyer Agent sends a build_creative request via A2A with the existing creative_manifest and target_format_id
    Then the response should be a successful BuildCreativeResponse
    And the output creative_manifest format_id should match the requested target_format_id
    # POST-S1: Transformed manifest ready
    # POST-S2: Output format matches requested target

  @T-UC-020-main-refine @main-flow @rest @post-s1
  Scenario: Build creative via REST - refinement mode
    Given the Buyer has a previously generated creative_manifest from build_creative
    And the Buyer has the same target_format_id used for the original generation
    And the creative agent supports the requested format
    When the Buyer Agent sends a build_creative request via A2A with the previous output as creative_manifest and message "Make the headline bolder"
    Then the response should be a successful BuildCreativeResponse
    And the output creative_manifest should reflect the refinement instructions
    # POST-S1: Refined manifest ready

  @T-UC-020-main-brand @main-flow @rest @post-s1
  Scenario: Build creative with brand context resolved
    Given the Buyer has a target_format_id for a supported format
    And the Buyer Agent provides a brand reference with domain "acme-corp.com"
    And the brand domain resolves to brand identity (colors, logos, tone)
    When the Buyer Agent sends a build_creative request via A2A with target_format_id and brand reference
    Then the response should be a successful BuildCreativeResponse
    And the output creative should reflect the resolved brand identity
    # POST-S1: Creative manifest generated with brand context

  @T-UC-020-main-precedence @main-flow @rest
  Scenario: Message takes precedence over brief asset on conflict
    Given the Buyer has a creative_manifest with a brief asset containing direction "use blue background"
    And the Buyer Agent provides message "use red background"
    And the Buyer has a target_format_id for a supported format
    When the Buyer Agent sends a build_creative request via A2A with both message and creative_manifest
    Then the response should be a successful BuildCreativeResponse
    And the message instruction should take precedence over the brief asset direction
    # BR-5: Message field takes precedence over brief asset on conflicts

  @T-UC-020-ext-a-rest @extension @ext-a @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Format not supported - unregistered agent URL via REST
    Given the Buyer has a target_format_id with agent_url "https://unknown-agent.example.com" and id "display_300x250"
    And no creative agent is registered for "https://unknown-agent.example.com"
    When the Buyer Agent sends a build_creative request via A2A with the target_format_id
    Then the operation should fail
    And the error code should be "FORMAT_NOT_SUPPORTED"
    And the error message should contain "not supported"
    And the error should include "suggestion" field
    And the suggestion should contain "list_creative_formats"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error explains format is not available
    # POST-F3: Suggestion advises list_creative_formats for discovery

  @T-UC-020-ext-a-mcp @extension @ext-a @error @mcp @post-f1 @post-f2 @post-f3
  Scenario: Format not supported - unregistered agent URL via MCP
    Given the Buyer has a target_format_id with agent_url "https://unknown-agent.example.com" and id "display_300x250"
    And no creative agent is registered for "https://unknown-agent.example.com"
    When the Buyer Agent calls build_creative MCP tool with the target_format_id
    Then the operation should fail
    And the error code should be "FORMAT_NOT_SUPPORTED"
    And the error message should contain "not supported"
    And the error should include "suggestion" field
    And the suggestion should contain "list_creative_formats"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error explains format is not available
    # POST-F3: Suggestion advises list_creative_formats for discovery

  @T-UC-020-ext-a-unknown-format @extension @ext-a @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Format not supported - agent registered but format id unknown
    Given the Buyer has a target_format_id with agent_url "https://creative.example.com" and id "nonexistent_format"
    And the creative agent at "https://creative.example.com" is registered but does not support format "nonexistent_format"
    When the Buyer Agent sends a build_creative request via A2A with the target_format_id
    Then the operation should fail
    And the error code should be "FORMAT_NOT_SUPPORTED"
    And the error message should contain "not supported"
    And the error should include "suggestion" field
    And the suggestion should contain "list_creative_formats"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error explains format id is not known by the agent
    # POST-F3: Suggestion advises list_creative_formats

  @T-UC-020-ext-b-rest @extension @ext-b @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Invalid manifest - malformed creative_manifest via REST
    Given the Buyer has a target_format_id for a supported format
    And the Buyer Agent provides a creative_manifest that is structurally malformed (missing required format_id within it)
    When the Buyer Agent sends a build_creative request via A2A with the invalid creative_manifest
    Then the operation should fail
    And the error code should be "INVALID_MANIFEST"
    And the error message should contain "manifest"
    And the error should include "field" pointing to the problematic path
    And the error should include "suggestion" field
    And the suggestion should contain "fix"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error explains what is wrong with the manifest
    # POST-F3: Suggestion advises how to fix the manifest

  @T-UC-020-ext-b-mcp @extension @ext-b @error @mcp @post-f1 @post-f2 @post-f3
  Scenario: Invalid manifest - malformed creative_manifest via MCP
    Given the Buyer has a target_format_id for a supported format
    And the Buyer Agent provides a creative_manifest that is structurally malformed (missing required format_id within it)
    When the Buyer Agent calls build_creative MCP tool with the invalid creative_manifest
    Then the operation should fail
    And the error code should be "INVALID_MANIFEST"
    And the error message should contain "manifest"
    And the error should include "field" pointing to the problematic path
    And the error should include "suggestion" field
    And the suggestion should contain "fix"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error explains what is wrong with the manifest
    # POST-F3: Suggestion advises how to fix the manifest

  @T-UC-020-ext-b-incompatible @extension @ext-b @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Invalid manifest - assets incompatible with target format
    Given the Buyer has a target_format_id for a display format
    And the Buyer Agent provides a creative_manifest with assets that do not match the target format's input requirements
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "INVALID_MANIFEST"
    And the error message should describe the asset incompatibility
    And the error should include "field" pointing to the incompatible asset path
    And the error should include "suggestion" field
    And the suggestion should contain "required assets"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error explains asset incompatibility
    # POST-F3: Suggestion advises providing compatible assets

  @T-UC-020-ext-b-width-no-height @extension @ext-b @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Invalid manifest - width provided without height in format_id
    Given the Buyer has a target_format_id with width 300 but no height
    When the Buyer Agent sends a build_creative request via A2A with the target_format_id
    Then the operation should fail
    And the error code should be "FORMAT_ID_DIMENSION_INCOMPLETE"
    And the error message should contain "width and height must both be present"
    And the error should include "suggestion" field
    And the suggestion should contain "both width and height"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error explains dimension co-dependency violation
    # POST-F3: Suggestion advises providing both dimensions

  @T-UC-020-ext-c-rest @extension @ext-c @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Compliance unsatisfied - single disclosure unsatisfiable via REST
    Given the Buyer has a target_format_id for a format that does not support footer position
    And the Buyer Agent provides a creative_manifest with brief containing compliance.required_disclosures with position "footer"
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "COMPLIANCE_UNSATISFIED"
    And the error message should identify the disclosure that cannot be rendered
    And the error should include "field" pointing to the unsatisfied disclosure path
    And the error should include "details" with disclosure_text and position
    And the error should include "suggestion" field
    And the suggestion should contain "format that supports"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error identifies unsatisfied disclosure
    # POST-F3: Suggestion advises compatible format or position change

  @T-UC-020-ext-c-mcp @extension @ext-c @error @mcp @post-f1 @post-f2 @post-f3
  Scenario: Compliance unsatisfied - single disclosure unsatisfiable via MCP
    Given the Buyer has a target_format_id for a format that does not support footer position
    And the Buyer Agent provides a creative_manifest with brief containing compliance.required_disclosures with position "footer"
    When the Buyer Agent calls build_creative MCP tool with the request
    Then the operation should fail
    And the error code should be "COMPLIANCE_UNSATISFIED"
    And the error message should identify the disclosure that cannot be rendered
    And the error should include "field" pointing to the unsatisfied disclosure path
    And the error should include "details" with disclosure_text and position
    And the error should include "suggestion" field
    And the suggestion should contain "format that supports"
    And the error recovery should be "correctable"
    # POST-F1: Operation failed
    # POST-F2: Error identifies unsatisfied disclosure
    # POST-F3: Suggestion advises compatible format or position change

  @T-UC-020-ext-c-multiple @extension @ext-c @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Compliance unsatisfied - multiple disclosures with one unsatisfiable
    Given the Buyer has a target_format_id for a format that supports "prominent" but not "footer" position
    And the Buyer Agent provides a creative_manifest with brief containing two required_disclosures
    And one disclosure requires position "prominent" and another requires position "footer"
    When the Buyer Agent sends a build_creative request
    Then the operation should fail with the entire request rejected
    And the error code should be "COMPLIANCE_UNSATISFIED"
    And the error message should identify the unsatisfied footer disclosure
    And the error should include "suggestion" field
    And the suggestion should contain "format that supports"
    And the error recovery should be "correctable"
    # POST-F1: Entire request fails (no partial success)
    # POST-F2: Error identifies the specific unsatisfied disclosure
    # POST-F3: Suggestion advises compatible format

  @T-UC-020-ext-d-rest @extension @ext-d @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Creative agent unavailable - connection refused via REST
    Given the Buyer has a target_format_id for a registered format
    And the creative agent at the agent_url is unreachable (connection refused)
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "CREATIVE_AGENT_UNAVAILABLE"
    And the error message should contain "unreachable" or "unavailable"
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    And the error should include "retry_after" field with a delay value
    And the error recovery should be "transient"
    # POST-F1: Operation failed
    # POST-F2: Error explains creative agent is temporarily unavailable
    # POST-F3: Suggestion advises retrying after delay

  @T-UC-020-ext-d-mcp @extension @ext-d @error @mcp @post-f1 @post-f2 @post-f3
  Scenario: Creative agent unavailable - connection refused via MCP
    Given the Buyer has a target_format_id for a registered format
    And the creative agent at the agent_url is unreachable (connection refused)
    When the Buyer Agent calls build_creative MCP tool with the request
    Then the operation should fail
    And the error code should be "CREATIVE_AGENT_UNAVAILABLE"
    And the error message should contain "unreachable" or "unavailable"
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    And the error should include "retry_after" field with a delay value
    And the error recovery should be "transient"
    # POST-F1: Operation failed
    # POST-F2: Error explains creative agent is temporarily unavailable
    # POST-F3: Suggestion advises retrying after delay

  @T-UC-020-ext-d-timeout @extension @ext-d @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Creative agent unavailable - timeout during delegation
    Given the Buyer has a target_format_id for a registered format
    And the creative agent at the agent_url times out (30s default)
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "CREATIVE_AGENT_UNAVAILABLE"
    And the error message should contain "timed out"
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    And the error should include "retry_after" field with a delay value
    And the error recovery should be "transient"
    # POST-F1: Operation failed
    # POST-F2: Error explains timeout
    # POST-F3: Suggestion advises retrying

  @T-UC-020-ext-d-malformed @extension @ext-d @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Creative agent unavailable - non-parseable response from agent
    Given the Buyer has a target_format_id for a registered format
    And the creative agent returns a response that cannot be parsed as BuildCreativeResponse
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "CREATIVE_AGENT_UNAVAILABLE"
    And the error message should contain "response" or "parse"
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    And the error recovery should be "transient"
    # POST-F1: Operation failed
    # POST-F2: Error explains malformed response
    # POST-F3: Suggestion advises retrying

  @T-UC-020-inv-155-1-holds @invariant @BR-RULE-155
  Scenario: BR-RULE-155 INV-1 holds - target_format_id present
    Given the Buyer provides a valid target_format_id with agent_url and id
    When the Buyer Agent sends a build_creative request
    Then the request should pass validation
    # BR-RULE-155 INV-1: target_format_id is present

  @T-UC-020-inv-155-1-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-1 violated - target_format_id absent
    Given the Buyer omits target_format_id from the request
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "TARGET_FORMAT_ID_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "target_format_id"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-1: target_format_id is absent -> rejected

  @T-UC-020-inv-155-2-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-2 violated - agent_url absent in target_format_id
    Given the Buyer provides a target_format_id with id but no agent_url
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "AGENT_URL_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "agent_url"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-2: agent_url absent -> rejected

  @T-UC-020-inv-155-3-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-3 violated - id absent in target_format_id
    Given the Buyer provides a target_format_id with agent_url but no id
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "FORMAT_ID_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "format identifier"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-3: id absent -> rejected

  @T-UC-020-inv-155-4-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-4 violated - width without height
    Given the Buyer provides a target_format_id with width 300 but no height
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "FORMAT_ID_DIMENSION_INCOMPLETE"
    And the error message should contain "width and height must both be present"
    And the error should include "suggestion" field
    And the suggestion should contain "both width and height"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-4: width without height -> rejected

  @T-UC-020-inv-155-4b-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-4 violated - height without width
    Given the Buyer provides a target_format_id with height 250 but no width
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "FORMAT_ID_DIMENSION_INCOMPLETE"
    And the error message should contain "width and height must both be present"
    And the error should include "suggestion" field
    And the suggestion should contain "both width and height"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-4: height without width -> rejected

  @T-UC-020-inv-155-5-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-5 violated - id contains invalid characters
    Given the Buyer provides a target_format_id with id "display 300x250!"
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "FORMAT_ID_INVALID_FORMAT"
    And the error message should contain "invalid characters"
    And the error should include "suggestion" field
    And the suggestion should contain "letters, digits, underscores, and hyphens"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-5: id outside [a-zA-Z0-9_-] -> rejected

  @T-UC-020-inv-155-6-violated @invariant @BR-RULE-155 @error
  Scenario: BR-RULE-155 INV-6 violated - dimension less than 1
    Given the Buyer provides a target_format_id with width 0 and height 250
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "FORMAT_ID_DIMENSION_INVALID"
    And the error message should contain "positive integers"
    And the error should include "suggestion" field
    And the suggestion should contain "integers >= 1"
    # POST-F3: Suggestion for recovery
    # BR-RULE-155 INV-6: dimension < 1 -> rejected

  @T-UC-020-inv-156-1-holds @invariant @BR-RULE-156
  Scenario: BR-RULE-156 INV-1 holds - output format_id matches request
    Given the Buyer requests build_creative with target_format_id id "display_300x250" and agent_url "https://creative.example.com"
    And the creative agent successfully generates a creative
    When the build_creative response is received
    Then the output creative_manifest.format_id.id should equal "display_300x250"
    And the output creative_manifest.format_id.agent_url should equal "https://creative.example.com"
    # BR-RULE-156 INV-1: successful response -> format_id equals target_format_id

  @T-UC-020-inv-156-2-violated @invariant @BR-RULE-156 @error
  Scenario: BR-RULE-156 INV-2 violated - output format id differs from request
    Given the Buyer requests build_creative with target_format_id id "display_300x250"
    And the creative agent produces output with format_id id "display_728x90" (wrong format)
    When the system validates the creative agent response
    Then the operation should fail with a system-level error
    And the error code should be "FORMAT_MISMATCH"
    And the error message should contain "does not match"
    And the error should include "suggestion" field
    And the suggestion should contain "correct target format"
    # POST-F3: Suggestion for recovery
    # BR-RULE-156 INV-2: output id differs -> system-level error

  @T-UC-020-inv-156-3-violated @invariant @BR-RULE-156 @error
  Scenario: BR-RULE-156 INV-3 violated - output agent_url differs from request
    Given the Buyer requests build_creative with target_format_id agent_url "https://creative.example.com"
    And the creative agent produces output with format_id agent_url "https://other-agent.example.com" (wrong agent)
    When the system validates the creative agent response
    Then the operation should fail with a system-level error
    And the error code should be "FORMAT_MISMATCH"
    And the error message should contain "does not match"
    And the error should include "suggestion" field
    And the suggestion should contain "correct target format"
    # POST-F3: Suggestion for recovery
    # BR-RULE-156 INV-3: output agent_url differs -> system-level error

  @T-UC-020-inv-157-1-holds @invariant @BR-RULE-157
  Scenario: BR-RULE-157 INV-1 holds - all disclosures satisfiable
    Given the Buyer has a brief asset with required_disclosures requiring position "prominent"
    And the target format supports "prominent" position
    When the Buyer Agent sends a build_creative request
    Then the response should be a successful BuildCreativeResponse
    And the creative should include the required disclosures in the output
    # BR-RULE-157 INV-1: all disclosures satisfiable -> generation proceeds

  @T-UC-020-inv-157-2-violated @invariant @BR-RULE-157 @error
  Scenario: BR-RULE-157 INV-2 violated - single disclosure unsatisfiable
    Given the Buyer has a brief asset with required_disclosures requiring position "footer"
    And the target format does not support "footer" position
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "COMPLIANCE_UNSATISFIED"
    And the error should include "suggestion" field
    And the suggestion should contain "format that supports"
    # POST-F3: Suggestion for recovery
    # BR-RULE-157 INV-2: any disclosure unsatisfiable -> entire request fails

  @T-UC-020-inv-157-3-holds @invariant @BR-RULE-157
  Scenario: BR-RULE-157 INV-3 holds - no brief asset, compliance check skipped
    Given the Buyer has a target_format_id for a supported format
    And the Buyer Agent does not provide a creative_manifest with a brief asset
    When the Buyer Agent sends a build_creative request with only target_format_id and message
    Then the response should be a successful BuildCreativeResponse
    # BR-RULE-157 INV-3: no brief -> compliance check not triggered

  @T-UC-020-inv-157-4-violated @invariant @BR-RULE-157 @error
  Scenario: BR-RULE-157 INV-4 - compliance failure error includes disclosure details
    Given the Buyer has a brief asset with required_disclosures requiring position "footer" with text "AI-generated content"
    And the target format does not support "footer" position
    When the Buyer Agent sends a build_creative request
    Then the operation should fail
    And the error code should be "COMPLIANCE_UNSATISFIED"
    And the error should include "details" with the disclosure_text, position, and reason
    And the error should include "field" pointing to the specific disclosure
    And the error should include "suggestion" field
    And the suggestion should contain "format that supports"
    # POST-F3: Suggestion for recovery
    # BR-RULE-157 INV-4: failure identifies specific unsatisfied disclosure

  @T-UC-020-inv-158-1-holds @invariant @BR-RULE-158
  Scenario: BR-RULE-158 INV-1 holds - brand domain resolvable
    Given the Buyer provides a brand reference with domain "acme-corp.com"
    And "acme-corp.com" hosts a valid /.well-known/brand.json
    When the Buyer Agent sends a build_creative request with the brand reference
    Then brand identity (colors, logos, tone) should be available to the creative agent
    And the response should be a successful BuildCreativeResponse
    # BR-RULE-158 INV-1: domain resolvable -> brand identity available

  @T-UC-020-inv-158-2 @invariant @BR-RULE-158
  Scenario: BR-RULE-158 INV-2 - brand domain not resolvable
    Given the Buyer provides a brand reference with domain "nonexistent-brand.example"
    And "nonexistent-brand.example" does not host /.well-known/brand.json
    When the Buyer Agent sends a build_creative request with the brand reference
    Then generation may proceed without brand context or fail depending on creative agent requirements
    # BR-RULE-158 INV-2: domain not resolvable -> behavior depends on agent

  @T-UC-020-inv-158-3-holds @invariant @BR-RULE-158
  Scenario: BR-RULE-158 INV-3 holds - brand omitted
    Given the Buyer does not provide a brand reference
    When the Buyer Agent sends a build_creative request with only target_format_id
    Then the response should be a successful BuildCreativeResponse
    And the creative should be generated without brand context
    # BR-RULE-158 INV-3: brand omitted -> proceeds without brand context

  @T-UC-020-inv-158-4-holds @invariant @BR-RULE-158
  Scenario: BR-RULE-158 INV-4 holds - house-of-brands resolution
    Given the Buyer provides a brand reference with domain "nova-brands.com" and brand_id "spark"
    And "nova-brands.com" hosts a brand portfolio including brand "spark"
    When the Buyer Agent sends a build_creative request with the brand reference
    Then the specific "spark" brand identity should be resolved from the portfolio
    And the response should be a successful BuildCreativeResponse
    # BR-RULE-158 INV-4: domain + brand_id -> specific brand resolved

  @T-UC-020-inv-159-1-holds @invariant @BR-RULE-159
  Scenario: BR-RULE-159 INV-1 holds - successful response includes provenance
    Given a successful build_creative response is produced
    When the output creative_manifest is examined
    Then the creative_manifest should include a provenance object
    # BR-RULE-159 INV-1: successful build -> provenance metadata present

  @T-UC-020-inv-159-1-violated @invariant @BR-RULE-159 @error
  Scenario: BR-RULE-159 INV-1 violated - provenance absent on AI-generated output
    Given the creative agent returns a successful response without provenance metadata
    When the system validates the creative agent response
    Then the operation should fail
    And the error code should be "PROVENANCE_REQUIRED"
    And the error message should contain "provenance"
    And the error should include "suggestion" field
    And the suggestion should contain "provenance"
    # POST-F3: Suggestion for recovery
    # BR-RULE-159 INV-1: provenance absent on AI output -> error

  @T-UC-020-inv-159-2-holds @invariant @BR-RULE-159
  Scenario: BR-RULE-159 INV-2 holds - provenance includes ai_tool with name
    Given a successful build_creative response includes provenance with ai_tool
    When the provenance is examined
    Then the ai_tool should have a name identifying the AI system
    # BR-RULE-159 INV-2: ai_tool.name identifies the AI system

  @T-UC-020-inv-159-2-violated @invariant @BR-RULE-159 @error
  Scenario: BR-RULE-159 INV-2 violated - ai_tool present but name missing
    Given the creative agent returns provenance with ai_tool object but no name field
    When the system validates the creative agent response
    Then the operation should fail
    And the error code should be "AI_TOOL_NAME_REQUIRED"
    And the error message should contain "ai_tool.name"
    And the error should include "suggestion" field
    And the suggestion should contain "name of the AI tool"
    # POST-F3: Suggestion for recovery
    # BR-RULE-159 INV-2: ai_tool without name -> error

  @T-UC-020-inv-159-3-holds @invariant @BR-RULE-159
  Scenario: BR-RULE-159 INV-3 holds - disclosure.required with jurisdictions
    Given a successful build_creative response includes provenance with disclosure.required = true
    When the provenance is examined
    Then the disclosure should include a jurisdictions array
    And the jurisdictions array should specify where AI disclosure obligations apply
    # BR-RULE-159 INV-3: disclosure.required=true -> jurisdictions present

  @T-UC-020-inv-159-4-holds @invariant @BR-RULE-159
  Scenario: BR-RULE-159 INV-4 holds - asset-level provenance replaces manifest-level
    Given a successful build_creative response has manifest-level provenance
    And an individual asset within the manifest has its own provenance
    When the asset's effective provenance is determined
    Then the asset-level provenance should entirely replace the manifest-level provenance
    And there should be no field-level merging between manifest and asset provenance
    # BR-RULE-159 INV-4: asset provenance replaces manifest provenance entirely

  @T-UC-020-inv-018-1-holds @invariant @BR-RULE-018
  Scenario: BR-RULE-018 INV-1 holds - successful response has no errors field
    Given the creative agent successfully generates a creative manifest
    When the build_creative response is received
    Then the response should contain creative_manifest
    And the response should not contain an errors field
    # BR-RULE-018 INV-1: success -> no errors field

  @T-UC-020-inv-018-2-holds @invariant @BR-RULE-018
  Scenario: BR-RULE-018 INV-2 holds - error response has no success fields
    Given the build_creative request fails validation
    When the build_creative response is received
    Then the response should contain an errors array with at least one entry
    And the response should not contain a creative_manifest field
    # BR-RULE-018 INV-2: failure -> errors array, no success fields

  @T-UC-020-inv-018-3-violated @invariant @BR-RULE-018 @error
  Scenario: BR-RULE-018 INV-3 violated - response with both success and error is invalid
    Given the creative agent returns a response with both creative_manifest and errors
    When the system validates the BuildCreativeResponse against schema
    Then the response should be rejected as a schema violation
    And the error should include "suggestion" field
    And the suggestion should contain "exclusively success or error"
    # POST-F3: Suggestion for recovery
    # BR-RULE-018 INV-3: both success and error -> schema violation

  @T-UC-020-partition-request @partition @build_creative_request_structure
  Scenario Outline: Request structure validation - <partition>
    Given the Buyer prepares a build_creative request matching partition "<partition>"
    When the Buyer Agent sends the build_creative request
    Then the <outcome>

    Examples: Valid partitions
      | partition                  | outcome                                                    |
      | minimal_request            | request should pass validation and produce a creative       |
      | full_request               | request should pass validation and produce a creative       |
      | with_dimensions            | request should pass validation and produce a creative       |
      | with_duration              | request should pass validation and produce a creative       |
      | with_brand_and_brand_id    | request should pass validation and produce a creative       |
      | generation_mode            | request should pass validation and produce a creative       |

    Examples: Invalid partitions
      | partition                  | outcome                                                                            |
      | missing_target_format_id   | operation should fail with error "TARGET_FORMAT_ID_REQUIRED" and suggestion         |
      | width_without_height       | operation should fail with error "FORMAT_ID_DIMENSION_INCOMPLETE" and suggestion    |
      | height_without_width       | operation should fail with error "FORMAT_ID_DIMENSION_INCOMPLETE" and suggestion    |
      | invalid_id_pattern         | operation should fail with error "FORMAT_ID_INVALID_FORMAT" and suggestion          |
      | zero_width                 | operation should fail with error "FORMAT_ID_DIMENSION_INVALID" and suggestion       |
      | missing_agent_url          | operation should fail with error "AGENT_URL_REQUIRED" and suggestion                |
      | missing_format_id          | operation should fail with error "FORMAT_ID_REQUIRED" and suggestion                |

  @T-UC-020-boundary-request @boundary @build_creative_request_structure
  Scenario Outline: Request structure boundary - <boundary_point>
    Given the Buyer prepares a build_creative request at boundary "<boundary_point>"
    When the Buyer Agent sends the build_creative request
    Then the <outcome>

    Examples: Boundary values
      | boundary_point                                           | outcome                                                                            |
      | target_format_id present with agent_url + id             | request should pass validation and produce a creative                               |
      | target_format_id absent (null)                           | operation should fail with error "TARGET_FORMAT_ID_REQUIRED" and suggestion         |
      | width=1 and height=1 (minimum valid dimensions)          | request should pass validation and produce a creative                               |
      | width=0 and height=250 (below minimum)                   | operation should fail with error "FORMAT_ID_DIMENSION_INVALID" and suggestion       |
      | width=300 and height absent (co-dependency violated)     | operation should fail with error "FORMAT_ID_DIMENSION_INCOMPLETE" and suggestion    |
      | height=250 and width absent (co-dependency violated)     | operation should fail with error "FORMAT_ID_DIMENSION_INCOMPLETE" and suggestion    |
      | id='display_300x250' (valid alphanumeric)                | request should pass validation and produce a creative                               |
      | id='display 300x250!' (invalid pattern)                  | operation should fail with error "FORMAT_ID_INVALID_FORMAT" and suggestion          |
      | agent_url absent in target_format_id                     | operation should fail with error "AGENT_URL_REQUIRED" and suggestion                |
      | duration_ms=1 (minimum valid duration)                   | request should pass validation and produce a creative                               |

  @T-UC-020-partition-output-format @partition @output_format_matching
  Scenario Outline: Output format matching validation - <partition>
    Given the Buyer sends a build_creative request with target_format_id
    And the creative agent produces a response matching partition "<partition>"
    When the system validates the response
    Then the <outcome>

    Examples: Valid partitions
      | partition                    | outcome                                                            |
      | exact_match                  | response should be accepted as valid                               |
      | exact_match_with_dimensions  | response should be accepted as valid                               |

    Examples: Invalid partitions
      | partition             | outcome                                                                    |
      | id_mismatch           | operation should fail with error "FORMAT_MISMATCH" and suggestion          |
      | agent_url_mismatch    | operation should fail with error "FORMAT_MISMATCH" and suggestion          |
      | dimension_mismatch    | operation should fail with error "FORMAT_MISMATCH" and suggestion          |

  @T-UC-020-boundary-output-format @boundary @output_format_matching
  Scenario Outline: Output format matching boundary - <boundary_point>
    Given the Buyer sends a build_creative request
    And the creative agent produces a response at boundary "<boundary_point>"
    When the system validates the response
    Then the <outcome>

    Examples: Boundary values
      | boundary_point                                                 | outcome                                                                   |
      | output format_id exactly equals request target_format_id       | response should be accepted as valid                                      |
      | output format_id.id differs from request target_format_id.id   | operation should fail with error "FORMAT_MISMATCH" and suggestion         |
      | output format_id.agent_url differs from request                | operation should fail with error "FORMAT_MISMATCH" and suggestion         |
      | output format_id has different dimensions than requested       | operation should fail with error "FORMAT_MISMATCH" and suggestion         |

  @T-UC-020-partition-compliance @partition @compliance_hard_fail
  Scenario Outline: Compliance hard-fail validation - <partition>
    Given the Buyer prepares a build_creative request matching compliance partition "<partition>"
    When the Buyer Agent sends the build_creative request
    Then the <outcome>

    Examples: Valid partitions
      | partition                     | outcome                                                   |
      | no_disclosures                | request should succeed without compliance check            |
      | all_disclosures_satisfied     | request should succeed with all disclosures rendered       |
      | no_brief_asset                | request should succeed without compliance check            |

    Examples: Invalid partitions
      | partition                              | outcome                                                                         |
      | single_disclosure_unsatisfied          | operation should fail with error "COMPLIANCE_UNSATISFIED" and suggestion         |
      | multiple_disclosures_some_unsatisfied  | operation should fail with error "COMPLIANCE_UNSATISFIED" and suggestion         |
      | position_unsupported_by_format         | operation should fail with error "COMPLIANCE_UNSATISFIED" and suggestion         |

  @T-UC-020-boundary-compliance @boundary @compliance_hard_fail
  Scenario Outline: Compliance hard-fail boundary - <boundary_point>
    Given the Buyer prepares a build_creative request at compliance boundary "<boundary_point>"
    When the Buyer Agent sends the build_creative request
    Then the <outcome>

    Examples: Boundary values
      | boundary_point                                        | outcome                                                                         |
      | no brief asset present                                | request should succeed without compliance check                                 |
      | brief with no compliance section                      | request should succeed without compliance check                                 |
      | brief with empty required_disclosures array           | operation should fail with error "COMPLIANCE_UNSATISFIED" and suggestion         |
      | single disclosure that is satisfiable                 | request should succeed with disclosure rendered                                 |
      | single disclosure that is unsatisfiable               | operation should fail with error "COMPLIANCE_UNSATISFIED" and suggestion         |
      | multiple disclosures, all satisfiable                 | request should succeed with all disclosures rendered                             |
      | multiple disclosures, one unsatisfiable               | operation should fail with error "COMPLIANCE_UNSATISFIED" and suggestion         |

  @T-UC-020-partition-brand @partition @brand_resolution
  Scenario Outline: Brand resolution validation - <partition>
    Given the Buyer prepares a build_creative request matching brand partition "<partition>"
    When the Buyer Agent sends the build_creative request
    Then the <outcome>

    Examples: Valid partitions
      | partition               | outcome                                                                  |
      | no_brand                | request should succeed without brand context                             |
      | single_brand_domain     | request should succeed with brand identity resolved                      |
      | house_of_brands         | request should succeed with specific brand from portfolio resolved       |

    Examples: Invalid partitions
      | partition                  | outcome                                                                             |
      | invalid_domain_pattern     | operation should fail with error "BRAND_DOMAIN_INVALID_FORMAT" and suggestion        |
      | domain_not_resolvable      | operation should fail with error "BRAND_NOT_FOUND" and suggestion                   |

  @T-UC-020-boundary-brand @boundary @brand_resolution
  Scenario Outline: Brand resolution boundary - <boundary_point>
    Given the Buyer prepares a build_creative request at brand boundary "<boundary_point>"
    When the Buyer Agent sends the build_creative request
    Then the <outcome>

    Examples: Boundary values
      | boundary_point                                             | outcome                                                                             |
      | brand absent (null/omitted)                                | request should succeed without brand context                                        |
      | brand with valid single-brand domain                       | request should succeed with brand identity resolved                                 |
      | brand with valid domain + brand_id                         | request should succeed with specific brand resolved                                 |
      | brand with domain containing uppercase letters             | operation should fail with error "BRAND_DOMAIN_INVALID_FORMAT" and suggestion        |
      | brand with domain that has no /.well-known/brand.json      | operation should fail with error "BRAND_NOT_FOUND" and suggestion                   |

  @T-UC-020-partition-provenance @partition @ai_provenance
  Scenario Outline: AI provenance validation - <partition>
    Given a build_creative response is produced matching provenance partition "<partition>"
    When the system validates the response
    Then the <outcome>

    Examples: Valid partitions
      | partition               | outcome                                                    |
      | full_provenance         | response should be accepted with complete provenance       |
      | minimal_provenance      | response should be accepted with minimal provenance        |
      | provenance_with_c2pa    | response should be accepted with C2PA credentials          |

    Examples: Invalid partitions
      | partition                | outcome                                                                         |
      | provenance_absent        | operation should fail with error "PROVENANCE_REQUIRED" and suggestion            |
      | ai_tool_missing_name     | operation should fail with error "AI_TOOL_NAME_REQUIRED" and suggestion          |

  @T-UC-020-boundary-provenance @boundary @ai_provenance
  Scenario Outline: AI provenance boundary - <boundary_point>
    Given a build_creative response is produced at provenance boundary "<boundary_point>"
    When the system validates the response
    Then the <outcome>

    Examples: Boundary values
      | boundary_point                                           | outcome                                                                         |
      | provenance present with digital_source_type and ai_tool  | response should be accepted as valid                                            |
      | provenance present with only digital_source_type         | response should be accepted as valid                                            |
      | provenance absent on AI-generated output                 | operation should fail with error "PROVENANCE_REQUIRED" and suggestion            |
      | ai_tool present but name missing                         | operation should fail with error "AI_TOOL_NAME_REQUIRED" and suggestion          |

  @T-UC-020-sandbox-happy @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account build_creative produces simulated output with sandbox flag
    Given the request targets a sandbox account
    And a valid target_format_id referencing an available creative format
    When the Buyer Agent sends a build_creative request
    Then the response status should be "completed"
    And the response should include sandbox equals true
    And no real AI generation API calls should have been billed
    And no real billing records should have been created
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-2: real ad platform calls suppressed
    # BR-RULE-209 INV-3: real billing suppressed
    # BR-RULE-209 INV-4: response includes sandbox: true

  @T-UC-020-sandbox-production @invariant @br-rule-209 @sandbox
  Scenario: Production account build_creative response does not include sandbox flag
    Given the request targets a production account
    And a valid target_format_id referencing an available creative format
    When the Buyer Agent sends a build_creative request
    Then the response status should be "completed"
    And the response should not include a sandbox field
    # BR-RULE-209 INV-5: production account -> sandbox absent

  @T-UC-020-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account with invalid format returns real validation error
    Given the request targets a sandbox account
    When the Buyer Agent sends a build_creative request with invalid target_format_id
    Then the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

