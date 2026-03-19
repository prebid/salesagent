# Generated from adcp-req @ 3b4c503bac3a4878809197ceefb212e4fc0e746d on 2026-03-11T13:05:19Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-005 Discover Creative Formats
  As a Buyer (Human or AI Agent)
  I want to discover what creative formats a seller accepts
  So that I can prepare compliant creative assets before creating media buys

  # Postconditions verified:
  #   POST-S1: Buyer knows the complete catalog of creative formats available from this seller
  #   POST-S2: Buyer knows the asset requirements (type, dimensions, required/optional) for each format
  #   POST-S3: Buyer knows which formats match their filter criteria (when filters applied)
  #   POST-S4: Buyer knows about additional creative agents they can query for more formats
  #   POST-F1: Buyer knows the operation failed
  #   POST-F2: Buyer knows what went wrong (error explains the failure)
  #   POST-F3: Buyer knows how to recover (suggestion for corrective action)

  Background:
    Given a Seller Agent is operational and accepting requests
    And at least one creative agent is registered with format definitions


  @T-UC-005-main-rest @main-flow @rest @post-s1 @post-s2
  Scenario: Discover full format catalog via REST
    Given the creative agent registry has formats across multiple categories
    When the Buyer Agent sends a list_creative_formats task via A2A with no filters
    Then the response should include all registered formats
    And each format should include a format_id with agent_url and id
    And each format should include a name and type category
    And each format should include asset requirements with type and dimensions
    And the results should be sorted by format type then name
    # POST-S1: Complete catalog returned
    # POST-S2: Asset requirements included per format

  @T-UC-005-main-mcp @main-flow @mcp @post-s1 @post-s2
  Scenario: Discover full format catalog via MCP
    Given the creative agent registry has formats across multiple categories
    When the Buyer Agent calls list_creative_formats MCP tool with no filters
    Then the response should include all registered formats
    And each format should include a format_id with agent_url and id
    And each format should include a name and type category
    And each format should include asset requirements with type and dimensions
    And the results should be sorted by format type then name
    # POST-S1: Complete catalog returned
    # POST-S2: Asset requirements included per format

  @T-UC-005-main-filtered @main-flow @rest @post-s3
  Scenario: Discover filtered format catalog via REST
    Given the creative agent registry has formats of types "display" and "video"
    When the Buyer Agent sends a list_creative_formats task via A2A with type filter "display"
    Then the response should include only display formats
    And no video formats should be present in the results
    # POST-S3: Only matching formats returned when filters applied

  @T-UC-005-main-referrals @main-flow @post-s4
  Scenario: Creative agent referrals included in response
    Given the seller has additional creative agents beyond the default
    When the Buyer Agent requests the format catalog
    Then the response should include creative_agents referrals
    And each referral should include the agent URL and supported capabilities
    # POST-S4: Creative agent referrals present when available

  @T-UC-005-inv-031-1-holds @invariant @BR-RULE-031
  Scenario: BR-RULE-031 INV-1 holds - Multiple filters combine as AND
    Given the registry has format "display-banner" of type "display" with asset type "image"
    And the registry has format "video-banner" of type "display" with asset type "video"
    And the registry has format "pre-roll" of type "video" with asset type "video"
    When the Buyer Agent requests formats with type "display" and asset_types ["video"]
    Then only "video-banner" should be returned
    # BR-RULE-031 INV-1: both filters must match (type=display AND asset=video)

  @T-UC-005-inv-031-1-violated @invariant @BR-RULE-031
  Scenario: BR-RULE-031 INV-1 violated - AND combination excludes partial matches
    Given the registry has format "pre-roll" of type "video" with asset type "video"
    When the Buyer Agent requests formats with type "display" and asset_types ["video"]
    Then no formats should be returned
    # BR-RULE-031 INV-1: type=display excludes video-type format despite matching asset_types

  @T-UC-005-inv-031-2-holds @invariant @BR-RULE-031
  Scenario: BR-RULE-031 INV-2 holds - Results sorted by type then name
    Given the registry has formats:
    | name            | type    |
    | Zebra Banner    | display |
    | Alpha Banner    | display |
    | Pre-Roll        | video   |
    | Audio Spot      | audio   |
    When the Buyer Agent requests all formats with no filters
    Then the results should be ordered:
    | name            | type    |
    | Audio Spot      | audio   |
    | Alpha Banner    | display |
    | Zebra Banner    | display |
    | Pre-Roll        | video   |
    # BR-RULE-031 INV-2: sorted by type value then name

  @T-UC-005-inv-049-1-holds @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-1 holds - Type filter matches exact category
    Given the registry has formats: "leaderboard" (display), "pre-roll" (video), "podcast-ad" (audio)
    When the Buyer Agent requests formats with type filter "display"
    Then only "leaderboard" should be returned
    And the returned format type should be "display"
    # BR-RULE-049 INV-1: type filter -> only formats with exactly matching FormatCategory

  @T-UC-005-inv-049-1-violated @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-1 violated - Type filter excludes non-matching formats
    Given the registry has formats: "leaderboard" (display), "pre-roll" (video)
    When the Buyer Agent requests formats with type filter "audio"
    Then no formats should be returned
    # BR-RULE-049 INV-1: non-matching FormatCategory excluded
    # --- INV-2: format_ids id match with silent exclusion ---

  @T-UC-005-inv-049-2-holds @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-2 holds - Format IDs filter matches on id field
    Given the registry has format "leaderboard" with format_id id "fmt-001"
    And the registry has format "pre-roll" with format_id id "fmt-002"
    When the Buyer Agent requests formats with format_ids filter ["fmt-001"]
    Then only "leaderboard" should be returned
    # BR-RULE-049 INV-2: format_ids matches on id field only

  @T-UC-005-inv-049-2-violated @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-2 violated - Non-matching format IDs silently excluded
    Given the registry has format "leaderboard" with format_id id "fmt-001"
    When the Buyer Agent requests formats with format_ids filter ["fmt-999", "fmt-001"]
    Then only "leaderboard" should be returned
    And no error should be raised for "fmt-999"
    # BR-RULE-049 INV-2: non-matching IDs silently excluded
    # --- INV-3: asset_types OR semantics ---

  @T-UC-005-inv-049-3-holds @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-3 holds - Asset types filter with OR semantics
    Given the registry has format "banner" with assets of type "image"
    And the registry has format "video-ad" with assets of type "video"
    And the registry has format "rich-media" with assets of types "image" and "html"
    When the Buyer Agent requests formats with asset_types filter ["image", "video"]
    Then "banner", "video-ad", and "rich-media" should all be returned
    # BR-RULE-049 INV-3: at least one matching asset type -> format included (OR semantics)

  @T-UC-005-inv-049-3-violated @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-3 violated - No matching asset type excludes format
    Given the registry has format "text-only" with assets of type "text"
    When the Buyer Agent requests formats with asset_types filter ["video"]
    Then "text-only" should not be returned
    # BR-RULE-049 INV-3: no matching asset type -> format excluded

  @T-UC-005-inv-049-3-group @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-3 edge - Group assets checked in addition to individual assets
    Given the registry has format "rich-media" with a repeatable asset group containing "image" and "text"
    When the Buyer Agent requests formats with asset_types filter ["text"]
    Then "rich-media" should be returned
    # BR-RULE-049 INV-3: both individual and group assets checked
    # --- INV-4: dimension ANY render match ---

  @T-UC-005-inv-049-4-holds @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-4 holds - Dimension filter matches any render
    Given the registry has format "companion-ad" with renders:
    | width | height |
    | 300   | 250    |
    | 728   | 90     |
    When the Buyer Agent requests formats with min_width 700
    Then "companion-ad" should be returned
    # BR-RULE-049 INV-4: ANY render satisfies constraint (728 >= 700)

  @T-UC-005-inv-049-4-violated @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-4 violated - No render fits dimension filter
    Given the registry has format "small-banner" with renders:
    | width | height |
    | 300   | 250    |
    | 320   | 50     |
    When the Buyer Agent requests formats with min_width 700
    Then "small-banner" should not be returned
    # BR-RULE-049 INV-4: no render satisfies min_width 700

  @T-UC-005-inv-049-4-nodim @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-4 edge - Formats without dimensions excluded by dimension filter
    Given the registry has format "audio-spot" with no render dimensions
    When the Buyer Agent requests formats with min_width 100
    Then "audio-spot" should not be returned
    # BR-RULE-049 INV-4: formats without dimension info excluded
    # --- INV-5: is_responsive=true ---

  @T-UC-005-inv-049-5-holds @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-5 holds - Responsive filter returns only responsive formats
    Given the registry has format "responsive-banner" with responsive render dimensions
    And the registry has format "fixed-banner" with non-responsive render dimensions
    When the Buyer Agent requests formats with is_responsive true
    Then only "responsive-banner" should be returned
    # BR-RULE-049 INV-5: is_responsive=true -> only formats with responsive render dimension
    # --- INV-6: is_responsive=false ---

  @T-UC-005-inv-049-6-holds @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-6 holds - Non-responsive filter returns only non-responsive formats
    Given the registry has format "responsive-banner" with responsive render dimensions
    And the registry has format "fixed-banner" with non-responsive render dimensions
    When the Buyer Agent requests formats with is_responsive false
    Then only "fixed-banner" should be returned
    # BR-RULE-049 INV-6: is_responsive=false -> only formats with no responsive dimensions
    # --- INV-7: name_search case-insensitive substring ---

  @T-UC-005-inv-049-7-holds @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-7 holds - Name search case-insensitive substring match
    Given the registry has format named "Premium Leaderboard"
    And the registry has format named "Standard Banner"
    When the Buyer Agent requests formats with name_search "leader"
    Then only "Premium Leaderboard" should be returned
    # BR-RULE-049 INV-7: case-insensitive substring match

  @T-UC-005-inv-049-7-violated @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-7 violated - Name search no match excluded
    Given the registry has format named "Standard Banner"
    When the Buyer Agent requests formats with name_search "video"
    Then no formats should be returned
    # BR-RULE-049 INV-7: no substring match -> excluded
    # --- INV-8: disclosure_positions AND-match (NEW) ---

  @T-UC-005-inv-049-8-holds @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-8 holds - Disclosure positions AND-match filter
    Given the registry has format "video-ad" with supported_disclosure_positions ["prominent", "footer", "overlay"]
    And the registry has format "audio-ad" with supported_disclosure_positions ["prominent", "audio"]
    When the Buyer Agent requests formats with disclosure_positions filter ["prominent", "footer"]
    Then only "video-ad" should be returned
    # BR-RULE-049 INV-8: ALL requested positions must be supported (AND semantics)

  @T-UC-005-inv-049-8-violated @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-8 violated - Disclosure positions partial match excluded
    Given the registry has format "audio-ad" with supported_disclosure_positions ["prominent", "audio"]
    When the Buyer Agent requests formats with disclosure_positions filter ["prominent", "footer"]
    Then "audio-ad" should not be returned
    # BR-RULE-049 INV-8: format only supports "prominent" not "footer" -> excluded

  @T-UC-005-inv-049-8-nofield @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-8 edge - Format without disclosure positions excluded
    Given the registry has format "basic-banner" with no supported_disclosure_positions field
    When the Buyer Agent requests formats with disclosure_positions filter ["prominent"]
    Then "basic-banner" should not be returned
    # BR-RULE-049 INV-8: formats without supported_disclosure_positions excluded
    # --- INV-9: output_format_ids OR-match (NEW) ---

  @T-UC-005-inv-049-9-holds @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-9 holds - Output format IDs OR-match filter
    Given the registry has format "universal-builder" with output_format_ids:
    | agent_url                                    | id             |
    | https://creatives.adcontextprotocol.org      | display_static |
    | https://creatives.adcontextprotocol.org      | video_hosted   |
    And the registry has format "audio-builder" with output_format_ids:
    | agent_url                                    | id             |
    | https://creatives.adcontextprotocol.org      | audio_ad       |
    When the Buyer Agent requests formats with output_format_ids filter [{"agent_url": "https://creatives.adcontextprotocol.org", "id": "display_static"}]
    Then only "universal-builder" should be returned
    # BR-RULE-049 INV-9: ANY requested ID matches -> format included (OR semantics)

  @T-UC-005-inv-049-9-violated @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-9 violated - Output format IDs no match excluded
    Given the registry has format "audio-builder" with output_format_ids:
    | agent_url                                    | id        |
    | https://creatives.adcontextprotocol.org      | audio_ad  |
    When the Buyer Agent requests formats with output_format_ids filter [{"agent_url": "https://creatives.adcontextprotocol.org", "id": "display_static"}]
    Then "audio-builder" should not be returned
    # BR-RULE-049 INV-9: no matching output ID -> excluded

  @T-UC-005-inv-049-9-nofield @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-9 edge - Format without output_format_ids excluded
    Given the registry has format "simple-banner" with no output_format_ids field
    When the Buyer Agent requests formats with output_format_ids filter [{"agent_url": "https://creatives.adcontextprotocol.org", "id": "display_static"}]
    Then "simple-banner" should not be returned
    # BR-RULE-049 INV-9: formats without output_format_ids excluded
    # --- INV-10: input_format_ids OR-match (NEW) ---

  @T-UC-005-inv-049-10-holds @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-10 holds - Input format IDs OR-match filter
    Given the registry has format "resizer" with input_format_ids:
    | agent_url                                    | id             |
    | https://creatives.adcontextprotocol.org      | display_static |
    | https://creatives.adcontextprotocol.org      | display_animated |
    And the registry has format "transcoder" with input_format_ids:
    | agent_url                                    | id             |
    | https://creatives.adcontextprotocol.org      | video_hosted   |
    When the Buyer Agent requests formats with input_format_ids filter [{"agent_url": "https://creatives.adcontextprotocol.org", "id": "display_static"}]
    Then only "resizer" should be returned
    # BR-RULE-049 INV-10: ANY requested ID matches -> format included (OR semantics)

  @T-UC-005-inv-049-10-violated @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-10 violated - Input format IDs no match excluded
    Given the registry has format "transcoder" with input_format_ids:
    | agent_url                                    | id           |
    | https://creatives.adcontextprotocol.org      | video_hosted |
    When the Buyer Agent requests formats with input_format_ids filter [{"agent_url": "https://creatives.adcontextprotocol.org", "id": "display_static"}]
    Then "transcoder" should not be returned
    # BR-RULE-049 INV-10: no matching input ID -> excluded

  @T-UC-005-inv-049-10-nofield @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-10 edge - Format without input_format_ids excluded
    Given the registry has format "basic-display" with no input_format_ids field
    When the Buyer Agent requests formats with input_format_ids filter [{"agent_url": "https://creatives.adcontextprotocol.org", "id": "display_static"}]
    Then "basic-display" should not be returned
    # BR-RULE-049 INV-10: formats without input_format_ids excluded (works from raw assets)

  @T-UC-005-empty-catalog @edge-case
  Scenario: Empty catalog when no agents have formats
    Given no creative agents have any registered formats
    When the Buyer Agent requests the format catalog
    Then the response should include an empty formats array
    And no error should be raised
    # Edge case: PRE-B1 boundary — no formats available

  @T-UC-005-dim-boundary @boundary @BR-RULE-049
  Scenario: Dimension boundary - inclusive range at threshold
    Given the registry has format "exact-fit" with render width 728 and height 90
    When the Buyer Agent requests formats with min_width 728 and max_width 728
    Then "exact-fit" should be returned
    # BR-RULE-049 INV-4: dimension range is inclusive (width == min_width == max_width)

  @T-UC-005-ext-a-rest @extension @ext-a @error @rest @post-f1 @post-f2 @post-f3
  Scenario: No tenant context - REST
    Given the Buyer has no authentication credentials
    And no hostname-based tenant resolution is possible
    When the Buyer Agent sends a list_creative_formats task via A2A
    Then the operation should fail
    And the error code should be "TENANT_REQUIRED"
    And the error message should indicate tenant context could not be determined
    And the error should include a "suggestion" field
    And the suggestion should advise providing authentication credentials
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains tenant context is missing
    # POST-F3: Suggestion advises providing auth or tenant identification

  @T-UC-005-ext-a-mcp @extension @ext-a @error @mcp @post-f1 @post-f2 @post-f3
  Scenario: No tenant context - MCP
    Given no tenant can be resolved from the request context
    When the Buyer Agent calls list_creative_formats MCP tool
    Then the operation should fail
    And the error code should be "TENANT_REQUIRED"
    And the error message should indicate tenant context could not be determined
    And the error should include a "suggestion" field
    And the suggestion should advise providing authentication credentials
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains tenant context is missing
    # POST-F3: Suggestion advises providing auth or tenant identification
    # --- ext-b: Invalid Request Parameters ---

  @T-UC-005-ext-b-rest @extension @ext-b @error @rest @post-f1 @post-f2 @post-f3
  Scenario: Invalid request parameters - REST
    Given the Buyer has tenant context
    When the Buyer Agent sends a list_creative_formats task via A2A with type "not_a_category"
    Then the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error message should indicate which parameters are invalid
    And the error should include a "suggestion" field
    And the suggestion should provide valid parameter values
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains which parameters are invalid and why
    # POST-F3: Suggestion provides valid values or format guidance

  @T-UC-005-ext-b-mcp @extension @ext-b @error @mcp @post-f1 @post-f2 @post-f3
  Scenario: Invalid request parameters - MCP
    Given the Buyer has tenant context via MCP session
    When the Buyer Agent calls list_creative_formats MCP tool with type "not_a_category"
    Then the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error message should indicate which parameters are invalid
    And the error should include a "suggestion" field
    And the suggestion should provide valid parameter values
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains which parameters are invalid and why
    # POST-F3: Suggestion provides valid values or format guidance
    # --- ext-b: Disclosure Positions Validation Errors (NEW) ---

  @T-UC-005-ext-b-disclosure-invalid @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Invalid disclosure position value
    Given the Buyer has tenant context
    When the Buyer Agent requests formats with disclosure_positions filter ["sidebar"]
    Then the operation should fail
    And the error code should be "DISCLOSURE_POSITIONS_INVALID_VALUE"
    And the error message should indicate "sidebar" is not a valid disclosure position
    And the error should include a "suggestion" field
    And the suggestion should advise using valid DisclosurePosition enum values
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains invalid value
    # POST-F3: Suggestion lists valid enum values

  @T-UC-005-ext-b-disclosure-empty @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Empty disclosure positions array
    Given the Buyer has tenant context
    When the Buyer Agent requests formats with disclosure_positions filter []
    Then the operation should fail
    And the error code should be "DISCLOSURE_POSITIONS_EMPTY"
    And the error message should indicate at least 1 item is required
    And the error should include a "suggestion" field
    And the suggestion should advise providing at least one position or omitting the filter
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains minItems violation
    # POST-F3: Suggestion for recovery

  @T-UC-005-ext-b-disclosure-dupes @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Duplicate disclosure positions
    Given the Buyer has tenant context
    When the Buyer Agent requests formats with disclosure_positions filter ["prominent", "prominent"]
    Then the operation should fail
    And the error code should be "DISCLOSURE_POSITIONS_DUPLICATES"
    And the error message should indicate duplicate values are not allowed
    And the error should include a "suggestion" field
    And the suggestion should advise removing duplicate positions
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains uniqueItems violation
    # POST-F3: Suggestion for recovery
    # --- ext-b: Output Format IDs Validation Errors (NEW) ---

  @T-UC-005-ext-b-output-empty @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Empty output format IDs array
    Given the Buyer has tenant context
    When the Buyer Agent requests formats with output_format_ids filter []
    Then the operation should fail
    And the error code should be "OUTPUT_FORMAT_IDS_EMPTY"
    And the error message should indicate at least 1 item is required
    And the error should include a "suggestion" field
    And the suggestion should advise providing at least one FormatId or omitting the filter
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains minItems violation
    # POST-F3: Suggestion for recovery

  @T-UC-005-ext-b-output-invalid @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Invalid output format ID structure - missing agent_url
    Given the Buyer has tenant context
    When the Buyer Agent requests formats with output_format_ids filter [{"id": "display_static"}]
    Then the operation should fail
    And the error code should be "OUTPUT_FORMAT_IDS_INVALID_STRUCTURE"
    And the error message should indicate FormatId must include agent_url and id
    And the error should include a "suggestion" field
    And the suggestion should advise including agent_url (URI) and id fields
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains invalid structure
    # POST-F3: Suggestion for correct FormatId structure

  @T-UC-005-ext-b-output-noid @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Invalid output format ID structure - missing id
    Given the Buyer has tenant context
    When the Buyer Agent requests formats with output_format_ids filter [{"agent_url": "https://example.com"}]
    Then the operation should fail
    And the error code should be "OUTPUT_FORMAT_IDS_INVALID_STRUCTURE"
    And the error message should indicate FormatId must include agent_url and id
    And the error should include a "suggestion" field
    And the suggestion should advise including agent_url (URI) and id fields
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains invalid structure
    # POST-F3: Suggestion for correct FormatId structure
    # --- ext-b: Input Format IDs Validation Errors (NEW) ---

  @T-UC-005-ext-b-input-empty @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Empty input format IDs array
    Given the Buyer has tenant context
    When the Buyer Agent requests formats with input_format_ids filter []
    Then the operation should fail
    And the error code should be "INPUT_FORMAT_IDS_EMPTY"
    And the error message should indicate at least 1 item is required
    And the error should include a "suggestion" field
    And the suggestion should advise providing at least one FormatId or omitting the filter
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains minItems violation
    # POST-F3: Suggestion for recovery

  @T-UC-005-ext-b-input-invalid @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Invalid input format ID structure - missing agent_url
    Given the Buyer has tenant context
    When the Buyer Agent requests formats with input_format_ids filter [{"id": "display_static"}]
    Then the operation should fail
    And the error code should be "INPUT_FORMAT_IDS_INVALID_STRUCTURE"
    And the error message should indicate FormatId must include agent_url and id
    And the error should include a "suggestion" field
    And the suggestion should advise including agent_url (URI) and id fields
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains invalid structure
    # POST-F3: Suggestion for correct FormatId structure

  @T-UC-005-ext-b-input-noid @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Invalid input format ID structure - missing id
    Given the Buyer has tenant context
    When the Buyer Agent requests formats with input_format_ids filter [{"agent_url": "https://example.com"}]
    Then the operation should fail
    And the error code should be "INPUT_FORMAT_IDS_INVALID_STRUCTURE"
    And the error message should indicate FormatId must include agent_url and id
    And the error should include a "suggestion" field
    And the suggestion should advise including agent_url (URI) and id fields
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains invalid structure
    # POST-F3: Suggestion for correct FormatId structure

  @T-UC-005-partition-type-filter @partition @format_type_filter
  Scenario Outline: Format type filter partition - <partition>
    Given a seller with formats of various types
    When the Buyer Agent requests creative formats with type filter "<partition>"
    Then the type filtering should result in <expected>

    Examples: Valid partitions
      | partition     | expected |
      | display       | valid    |
      | video         | valid    |
      | native        | valid    |
      | omitted       | valid    |

    Examples: Invalid partitions
      | partition     | expected |
      | invalid_type  | invalid  |

  @T-UC-005-partition-format-ids @partition @format_ids_filter
  Scenario Outline: Format IDs filter partition - <partition>
    Given a seller with known format IDs in the catalog
    When the Buyer Agent requests creative formats with format_ids "<partition>"
    Then the format_ids filtering should result in <expected>

    Examples: Valid partitions
      | partition       | expected |
      | all_ids_match   | valid    |
      | partial_match   | valid    |
      | no_match        | valid    |
      | omitted         | valid    |

  @T-UC-005-partition-asset-types @partition @asset_types_filter
  Scenario Outline: Asset types filter partition - <partition>
    Given a seller with formats containing various asset types
    When the Buyer Agent requests creative formats with asset_types "<partition>"
    Then the asset_types filtering should result in <expected>

    Examples: Valid partitions
      | partition            | expected |
      | single_type_match    | valid    |
      | multiple_types_or    | valid    |
      | omitted              | valid    |

    Examples: Invalid partitions
      | partition                   | expected |
      | no_matching_formats         | valid    |
      | unknown_asset_type          | invalid  |
      | removed_promoted_offerings  | invalid  |

  @T-UC-005-partition-dimension @partition @dimension_filter
  Scenario Outline: Dimension filter partition - <partition>
    Given a seller with formats of various render dimensions
    When the Buyer Agent requests creative formats with dimension filter "<partition>"
    Then the dimension filtering should result in <expected>

    Examples: Valid partitions
      | partition           | expected |
      | width_only          | valid    |
      | height_only         | valid    |
      | width_and_height    | valid    |
      | omitted             | valid    |

    Examples: Invalid partitions
      | partition           | expected |
      | no_render_match     | valid    |
      | no_dimension_info   | valid    |

  @T-UC-005-partition-responsive @partition @is_responsive_filter
  Scenario Outline: Responsive filter partition - <partition>
    Given a seller with both responsive and fixed-dimension formats
    When the Buyer Agent requests creative formats with is_responsive "<partition>"
    Then the responsive filtering should result in <expected>

    Examples: Valid partitions
      | partition         | expected |
      | responsive_true   | valid    |
      | responsive_false  | valid    |
      | omitted           | valid    |

  @T-UC-005-partition-name-search @partition @name_search_filter
  Scenario Outline: Name search filter partition - <partition>
    Given a seller with formats named "Standard Banner", "Video Interstitial", "Native Card"
    When the Buyer Agent requests creative formats with name_search "<partition>"
    Then the name search filtering should result in <expected>

    Examples: Valid partitions
      | partition         | expected |
      | exact_name        | valid    |
      | partial_match     | valid    |
      | case_insensitive  | valid    |
      | omitted           | valid    |

    Examples: Invalid partitions
      | partition         | expected |
      | no_match          | valid    |

  @T-UC-005-partition-wcag @partition @wcag_level
  Scenario Outline: WCAG level filter partition - <partition>
    Given a seller with formats at various accessibility conformance levels
    When the Buyer Agent requests creative formats with wcag_level "<partition>"
    Then the wcag filtering should result in <expected>

    Examples: Valid partitions
      | partition     | expected |
      | level_a       | valid    |
      | level_aa      | valid    |
      | level_aaa     | valid    |
      | not_provided  | valid    |

    Examples: Invalid partitions
      | partition      | expected |
      | unknown_value  | invalid  |

  @T-UC-005-partition-disclosure @partition @disclosure_positions
  Scenario Outline: Disclosure positions filter partition - <partition>
    Given a seller with formats supporting various disclosure positions
    When the Buyer Agent requests creative formats with disclosure_positions "<partition>"
    Then the disclosure_positions filtering should result in <expected>

    Examples: Valid partitions
      | partition                      | expected |
      | single_position                | valid    |
      | multiple_positions_all_match   | valid    |
      | all_positions                  | valid    |
      | omitted                        | valid    |
      | no_matching_formats            | valid    |

    Examples: Invalid partitions
      | partition            | expected |
      | unknown_position     | invalid  |
      | empty_array          | invalid  |
      | duplicate_positions  | invalid  |

  @T-UC-005-partition-output-fmtids @partition @output_format_ids
  Scenario Outline: Output format IDs filter partition - <partition>
    Given a seller with formats that produce various output formats
    When the Buyer Agent requests creative formats with output_format_ids "<partition>"
    Then the output_format_ids filtering should result in <expected>

    Examples: Valid partitions
      | partition                    | expected |
      | single_format_id             | valid    |
      | multiple_ids_any_match       | valid    |
      | omitted                      | valid    |
      | no_matching_formats          | valid    |
      | format_without_output_ids    | valid    |

    Examples: Invalid partitions
      | partition                           | expected |
      | empty_array                         | invalid  |
      | invalid_format_id_missing_agent_url | invalid  |
      | invalid_format_id_missing_id        | invalid  |

  @T-UC-005-partition-input-fmtids @partition @input_format_ids
  Scenario Outline: Input format IDs filter partition - <partition>
    Given a seller with formats that accept various input formats
    When the Buyer Agent requests creative formats with input_format_ids "<partition>"
    Then the input_format_ids filtering should result in <expected>

    Examples: Valid partitions
      | partition                    | expected |
      | single_format_id             | valid    |
      | multiple_ids_any_match       | valid    |
      | omitted                      | valid    |
      | no_matching_formats          | valid    |
      | format_without_input_ids     | valid    |

    Examples: Invalid partitions
      | partition                           | expected |
      | empty_array                         | invalid  |
      | invalid_format_id_missing_agent_url | invalid  |
      | invalid_format_id_missing_id        | invalid  |

  @T-UC-005-boundary-type-filter @boundary @format_type_filter
  Scenario Outline: Format type filter boundary - <boundary_point>
    Given a seller with formats of various types
    When the Buyer Agent requests creative formats at type boundary "<boundary_point>"
    Then the type handling should be <expected>

    Examples:
      | boundary_point              | expected |
      | display (valid enum)        | valid    |
      | video (valid enum)          | valid    |
      | omitted (no filter)         | valid    |
      | invalid type (rejected)     | invalid  |

  @T-UC-005-boundary-format-ids @boundary @format_ids_filter
  Scenario Outline: Format IDs filter boundary - <boundary_point>
    Given a seller with known format IDs in the catalog
    When the Buyer Agent requests creative formats at format_ids boundary "<boundary_point>"
    Then the format_ids handling should be <expected>

    Examples:
      | boundary_point                      | expected |
      | all IDs match                       | valid    |
      | partial match (some excluded)       | valid    |
      | no IDs match (empty result)         | valid    |
      | omitted (no filter)                 | valid    |

  @T-UC-005-boundary-asset-types @boundary @asset_types_filter
  Scenario Outline: Asset types filter boundary - <boundary_point>
    Given a seller with formats containing various asset types
    When the Buyer Agent requests creative formats at asset_types boundary "<boundary_point>"
    Then the asset_types handling should be <expected>

    Examples:
      | boundary_point                                    | expected |
      | single asset type match                           | valid    |
      | multiple types OR semantics                       | valid    |
      | omitted (no filter)                               | valid    |
      | brief (new asset type for generative formats)     | valid    |
      | catalog (new asset type for catalog-based formats) | valid    |
      | no formats match (empty result)                   | valid    |
      | Unknown string not in enum                        | invalid  |
      | promoted_offerings (removed from enum)            | invalid  |

  @T-UC-005-boundary-dimension @boundary @dimension_filter
  Scenario Outline: Dimension filter boundary - <boundary_point>
    Given a seller with formats of various render dimensions
    When the Buyer Agent requests creative formats at dimension boundary "<boundary_point>"
    Then the dimension handling should be <expected>

    Examples:
      | boundary_point                    | expected |
      | width filter only                 | valid    |
      | height filter only                | valid    |
      | width and height combined         | valid    |
      | omitted (no dimension filter)     | valid    |
      | no render matches constraints     | valid    |

  @T-UC-005-boundary-responsive @boundary @is_responsive_filter
  Scenario Outline: Responsive filter boundary - <boundary_point>
    Given a seller with both responsive and fixed-dimension formats
    When the Buyer Agent requests creative formats at responsive boundary "<boundary_point>"
    Then the responsive handling should be <expected>

    Examples:
      | boundary_point            | expected |
      | is_responsive = true      | valid    |
      | is_responsive = false     | valid    |
      | is_responsive omitted     | valid    |

  @T-UC-005-boundary-name-search @boundary @name_search_filter
  Scenario Outline: Name search filter boundary - <boundary_point>
    Given a seller with formats named "Standard Banner", "Video Interstitial", "Native Card"
    When the Buyer Agent requests creative formats at name_search boundary "<boundary_point>"
    Then the name search handling should be <expected>

    Examples:
      | boundary_point              | expected |
      | exact name match            | valid    |
      | partial substring match     | valid    |
      | case-insensitive match      | valid    |
      | omitted (no filter)         | valid    |
      | no match (empty result)     | valid    |

  @T-UC-005-boundary-wcag @boundary @wcag_level
  Scenario Outline: WCAG level filter boundary - <boundary_point>
    Given a seller with formats at various accessibility conformance levels
    When the Buyer Agent requests creative formats at wcag_level boundary "<boundary_point>"
    Then the wcag handling should be <expected>

    Examples:
      | boundary_point                                   | expected |
      | A (first enum value — minimum conformance)       | valid    |
      | AAA (last enum value — highest conformance)      | valid    |
      | Not provided (no filter)                         | valid    |
      | Unknown string not in enum                       | invalid  |

  @T-UC-005-boundary-disclosure @boundary @disclosure_positions
  Scenario Outline: Disclosure positions filter boundary - <boundary_point>
    Given a seller with formats supporting various disclosure positions
    When the Buyer Agent requests creative formats at disclosure boundary "<boundary_point>"
    Then the disclosure handling should be <expected>

    Examples:
      | boundary_point                                          | expected |
      | single position ['prominent'] (min array size)          | valid    |
      | all 8 positions (max meaningful array)                  | valid    |
      | omitted (no filter)                                     | valid    |
      | format has no supported_disclosure_positions (excluded)  | valid    |
      | empty array []                                          | invalid  |
      | unknown position string 'sidebar'                       | invalid  |
      | duplicate positions ['prominent','prominent']           | invalid  |

  @T-UC-005-boundary-output-fmtids @boundary @output_format_ids
  Scenario Outline: Output format IDs filter boundary - <boundary_point>
    Given a seller with formats that produce various output formats
    When the Buyer Agent requests creative formats at output_format_ids boundary "<boundary_point>"
    Then the output_format_ids handling should be <expected>

    Examples:
      | boundary_point                                   | expected |
      | single FormatId (min array size)                 | valid    |
      | multiple FormatIds, one matches (ANY semantics)  | valid    |
      | omitted (no filter)                              | valid    |
      | format has no output_format_ids (excluded)       | valid    |
      | no formats match requested output IDs            | valid    |
      | empty array []                                   | invalid  |
      | FormatId missing agent_url                       | invalid  |
      | FormatId missing id                              | invalid  |

  @T-UC-005-boundary-input-fmtids @boundary @input_format_ids
  Scenario Outline: Input format IDs filter boundary - <boundary_point>
    Given a seller with formats that accept various input formats
    When the Buyer Agent requests creative formats at input_format_ids boundary "<boundary_point>"
    Then the input_format_ids handling should be <expected>

    Examples:
      | boundary_point                                   | expected |
      | single FormatId (min array size)                 | valid    |
      | multiple FormatIds, one matches (ANY semantics)  | valid    |
      | omitted (no filter)                              | valid    |
      | format has no input_format_ids (excluded)        | valid    |
      | no formats match requested input IDs             | valid    |
      | empty array []                                   | invalid  |
      | FormatId missing agent_url                       | invalid  |
      | FormatId missing id                              | invalid  |

  @T-UC-005-partition-agent-type @partition @creative_agent_format_type
  Scenario Outline: Creative agent format type partition - <partition>
    Given a seller with creative agent formats of various types
    When the Buyer Agent queries creative agent formats with type "<partition>"
    Then the creative agent type filtering should result in <expected>

    Examples: Valid partitions
      | partition     | expected |
      | audio         | valid    |
      | video         | valid    |
      | display       | valid    |
      | dooh          | valid    |
      | not_provided  | valid    |

    Examples: Invalid partitions
      | partition      | expected |
      | unknown_value  | invalid  |

  @T-UC-005-partition-agent-asset @partition @creative_agent_asset_type
  Scenario Outline: Creative agent asset type partition - <partition>
    Given a seller with creative agent formats containing various asset types
    When the Buyer Agent queries creative agent formats with asset_types "<partition>"
    Then the creative agent asset type filtering should result in <expected>

    Examples: Valid partitions
      | partition     | expected |
      | image         | valid    |
      | video         | valid    |
      | audio         | valid    |
      | text          | valid    |
      | html          | valid    |
      | javascript    | valid    |
      | url           | valid    |
      | not_provided  | valid    |

    Examples: Invalid partitions
      | partition      | expected |
      | unknown_value  | invalid  |
      | empty_array    | invalid  |

  @T-UC-005-boundary-agent-type @boundary @creative_agent_format_type
  Scenario Outline: Creative agent format type boundary - <boundary_point>
    Given a seller with creative agent formats of various types
    When the Buyer Agent queries creative agent formats at type boundary "<boundary_point>"
    Then the creative agent type handling should be <expected>

    Examples:
      | boundary_point                                                  | expected |
      | audio (first enum value)                                        | valid    |
      | dooh (last enum value)                                          | valid    |
      | Not provided (no filter)                                        | valid    |
      | native (valid in media-buy variant but not in creative agent)   | invalid  |

  @T-UC-005-boundary-agent-asset @boundary @creative_agent_asset_type
  Scenario Outline: Creative agent asset type boundary - <boundary_point>
    Given a seller with creative agent formats containing various asset types
    When the Buyer Agent queries creative agent formats at asset_types boundary "<boundary_point>"
    Then the creative agent asset type handling should be <expected>

    Examples:
      | boundary_point                                                  | expected |
      | image (first enum value)                                        | valid    |
      | url (last enum value)                                           | valid    |
      | Not provided (no filter)                                        | valid    |
      | vast (valid in media-buy variant but not in creative agent)     | invalid  |
      | Empty array                                                     | invalid  |

  @T-UC-005-sandbox-happy @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account receives simulated creative formats with sandbox flag
    Given the request targets a sandbox account
    When the Buyer Agent sends a list_creative_formats request
    Then the response status should be "completed"
    And the response should contain "formats" array
    And the response should include sandbox equals true
    And no real ad platform API calls should have been made
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-2: real ad platform calls suppressed
    # BR-RULE-209 INV-4: response includes sandbox: true

  @T-UC-005-sandbox-production @invariant @br-rule-209 @sandbox
  Scenario: Production account creative formats response does not include sandbox flag
    Given the request targets a production account
    When the Buyer Agent sends a list_creative_formats request
    Then the response status should be "completed"
    And the response should contain "formats" array
    And the response should not include a sandbox field
    # BR-RULE-209 INV-5: production account -> sandbox absent

  @T-UC-005-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account with invalid filter returns real validation error
    Given the request targets a sandbox account
    When the Buyer Agent sends a list_creative_formats request with invalid dimension filters
    Then the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

