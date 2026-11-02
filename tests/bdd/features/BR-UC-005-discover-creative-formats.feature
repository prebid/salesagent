# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)

Feature: BR-UC-005 Discover Creative Formats
  As a Buyer (Human or AI Agent)
  I want to discover what creative formats a seller accepts
  So that I can prepare compliant creative assets before creating media buys

  # Postconditions verified:
  #   POST-S1: Buyer knows the complete catalog of creative formats available from this seller
  #   POST-S2: Buyer knows the asset requirements (type, dimensions, required/optional) for each format
  #   POST-S3: Buyer knows which formats match their filter criteria (when filters applied)
  #   POST-S4: Buyer knows about additional creative agents they can query for more formats
  #   POST-S5: Buyer knows each format's pricing options (surfaced pass-through from the registry; the media-buy request carries no include_pricing gate)
  #   POST-F1: Buyer knows the operation failed
  #   POST-F2: Buyer knows what went wrong (error explains the failure)
  #   POST-F3: Buyer knows how to recover (suggestion for corrective action)

  Background:
    Given a Seller Agent is operational and accepting requests
    And at least one creative agent is registered with format definitions



  @T-UC-005-main @main-flow @post-s1 @post-s2
  Scenario: Discover full format catalog
    Given the creative agent registry has formats across multiple categories
    When the Buyer Agent requests all formats with no filters
    Then the response is compliant with the list_creative_formats spec
    And the response should include all registered formats
    And each format should include a format_id with agent_url and id
    And each format should include a name
    And each format should include asset requirements with type and dimensions
    # POST-S1: Complete catalog returned
    # POST-S2: Asset requirements included per format
    # Two Thens were dropped here, both grading `type`, which adcp 3.12 removed from
    # Format: "a name and type category" and "sorted by format type then name". Sorting is
    # graded on its own by @T-UC-005-inv-031-2-holds, so it is no longer hostage to this
    # scenario's remaining gap.

  @T-UC-005-main-filtered @UC-005-MAIN-MCP-05 @main-flow @post-s3
  Scenario: Discover filtered format catalog
    Given the creative agent registry has formats of types "display" and "video"
    When the Buyer Agent requests formats with type filter "display"
    Then the response is compliant with the list_creative_formats spec
    And the response should include only display formats
    And no video formats should be present in the results
    # POST-S3: Only matching formats returned when filters applied

  @T-UC-005-main-referrals @UC-005-MAIN-MCP-13 @main-flow @post-s4
  Scenario: Creative agent referrals included in response
    Given the seller has additional creative agents beyond the default
    When the Buyer Agent requests the format catalog
    Then the response should include creative_agents referrals
    And each referral should include the agent URL and supported capabilities
    # POST-S4: Creative agent referrals present when available
    #
    # THE COMPLIANCE THEN IS DELIBERATELY ABSENT (local divergence -- mirror upstream). It
    # read "Then the response is compliant with the list_creative_formats spec" and was
    # REDUNDANT on every transport, while being the only thing that kept POST-S4 --
    # this scenario's whole subject -- ungraded in-network:
    #
    #   * Over e2e_rest it validated a document byte-identical to the one
    #     @T-UC-005-main's copy of the same line validates: the live server answers with
    #     the whole reference catalog whichever formats a Given names (set_registry_formats
    #     is a no-op there for reference ids), and 45 of the catalog's 57 formats carry a
    #     pixel_tracker asset the pinned Format.assets union does not admit (adcp#7338).
    #     That node is already on tests/bdd/e2e_rest_known_failures.txt with the evidence.
    #   * In process the format half is @T-UC-005-main's SUBJECT, graded there against the
    #     same pixel_tracker-free reference picks, on a2a/mcp/rest.
    #   * The creative_agents half cannot be made to fail from any scenario input, which
    #     was measured rather than assumed: production builds the block out of the pinned
    #     AdcpCreativeAgent, so it conforms by construction, and a drift (an advertised
    #     capability outside enums/creative-agent-capability.json) raises INSIDE
    #     creative_formats.py's own `except Exception` and degrades to an empty array --
    #     caught by "should include creative_agents referrals" below, not by a schema check.
    #     Probe: adding a bogus capability to ADVERTISED_CREATIVE_AGENT_CAPABILITIES fails
    #     this scenario with "got empty list" on all three in-process transports.
    #
    # 16 of this feature's 85 scenarios already carry no compliance Then, and no guard
    # requires one (the only ordering guard is UC-004-scoped and self-declared disposable).
    # Restore the line only if a reason appears that the three bullets above do not cover.

  @T-UC-005-main-pricing @main-flow @post-s5
  Scenario: Per-format pricing options surfaced pass-through by the media-buy aggregator
    Given the creative agent registry has formats with vendor pricing options
    When the Buyer Agent requests the format catalog
    Then the response is compliant with the list_creative_formats spec
    And the response should include all registered formats
    And each format offering vendor pricing should include its pricing_options
    # POST-S5: per-format pricing surfaced pass-through. The media-buy request [S1] carries
    # no `include_pricing` flag (that field exists only on the creative-variant request), and
    # the reference impl applies no include_pricing gate — pricing_options pass through from
    # the registry unconditionally.
    # OPEN G-UC005-2: behaviour when a format has no pricing_options (omitted vs empty) unresolved
    # OUT OF SCOPE: the v3.1 creative-variant request's `include_pricing` / `account` mechanism
    # (and its "account required when include_pricing=true" constraint) is not part of the
    # media-buy aggregator contract under analysis (see overview scope notes).
    # @source repo=adcp ref=v3.1.1 commit=467fd93d7 path=static/schemas/source/media-buy/list-creative-formats-request.json

  @T-UC-005-main-no-pricing @main-flow @post-s5
  Scenario: Formats without vendor pricing carry no pricing_options
    Given the creative agent registry has formats without vendor pricing options
    When the Buyer Agent requests the format catalog
    Then the response is compliant with the list_creative_formats spec
    And no format should include pricing_options
    # POST-S5: pricing_options presence reflects registry data, not a request flag. The
    # media-buy request carries no `include_pricing` gate, so the aggregator never strips
    # pricing_options — formats lack them only when the registry supplies none.

  @T-UC-005-inv-031-1-holds @UC-005-MAIN-MCP-16 @invariant @BR-RULE-031
  Scenario: BR-RULE-031 INV-1 holds - Multiple filters combine as AND
    Given the registry has format "display-banner" of type "display" with asset type "image"
    And the registry has format "video-banner" of type "display" with asset type "video"
    And the registry has format "pre-roll" of type "video" with asset type "video"
    When the Buyer Agent requests formats with type "display" and asset_types ["video"]
    Then the response is compliant with the list_creative_formats spec
    And only "video-banner" should be returned
    # BR-RULE-031 INV-1: both filters must match (type=display AND asset=video)

  @T-UC-005-inv-031-1-violated @UC-005-MAIN-MCP-16 @invariant @BR-RULE-031
  Scenario: BR-RULE-031 INV-1 violated - AND combination excludes partial matches
    Given the registry has format "pre-roll" of type "video" with asset type "video"
    When the Buyer Agent requests formats with type "display" and asset_types ["video"]
    Then the response is compliant with the list_creative_formats spec
    And no formats should be returned
    # BR-RULE-031 INV-1: type=display excludes video-type format despite matching asset_types

  @T-UC-005-inv-031-2-holds @UC-005-MAIN-MCP-04 @invariant @BR-RULE-031
  Scenario: BR-RULE-031 INV-2 holds - Results sorted by name
    Given the registry has formats:
    | name            |
    | Zebra Banner    |
    | Alpha Banner    |
    | Pre-Roll        |
    | Audio Spot      |
    When the Buyer Agent requests all formats with no filters
    Then the response is compliant with the list_creative_formats spec
    And the results should be ordered:
    | name            |
    | Alpha Banner    |
    | Audio Spot      |
    | Pre-Roll        |
    | Zebra Banner    |
    # BR-RULE-031 INV-2: sorted by name.
    # Was "sorted by type value then name". adcp 3.12 removed `type` from Format
    # (Format.model_fields has no `type`; `category` is standard|custom|generative, a
    # different concept), and production sorts on name alone --
    # src/core/tools/creative_formats.py:386 `formats.sort(key=lambda f: f.name or "")`.
    # The scenario graded a field the spec deleted, so it was xfailed as a gap; it was
    # obsolete, not failing.

  @T-UC-005-inv-049-2-holds @UC-005-MAIN-MCP-06 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-2 holds - Format IDs filter matches on the (agent_url, id) pair
    Given the registry has format "leaderboard" with format_id id "fmt-001"
    And the registry has format "pre-roll" with format_id id "fmt-002"
    When the Buyer Agent requests formats with format_ids filter ["fmt-001"]
    Then the response is compliant with the list_creative_formats spec
    And only "leaderboard" should be returned
    # BR-RULE-049 INV-2: format_ids matches on the (agent_url, id) federation pair
    # (core/format-id.json requires [agent_url, id]; list_formats step match_keys [agent_url, id])

  @T-UC-005-inv-049-2-violated @UC-005-MAIN-MCP-06 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-2 violated - Non-matching format IDs silently excluded
    Given the registry has format "leaderboard" with format_id id "fmt-001"
    When the Buyer Agent requests formats with format_ids filter ["fmt-999", "fmt-001"]
    Then the response is compliant with the list_creative_formats spec
    And only "leaderboard" should be returned
    And no error should be raised for "fmt-999"
    # BR-RULE-049 INV-2: references that match no (agent_url, id) pair are silently excluded
    # --- INV-3: asset_types OR semantics ---

  @T-UC-005-inv-049-3-holds @UC-005-MAIN-MCP-07 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-3 holds - Asset types filter with OR semantics
    Given the registry has format "banner" with assets of type "image"
    And the registry has format "video-ad" with assets of type "video"
    And the registry has format "rich-media" with assets of types "image" and "html"
    When the Buyer Agent requests formats with asset_types filter ["image", "video"]
    Then the response is compliant with the list_creative_formats spec
    And "banner", "video-ad", and "rich-media" should all be returned
    # BR-RULE-049 INV-3: at least one matching asset type -> format included (OR semantics)

  @T-UC-005-inv-049-3-violated @UC-005-MAIN-MCP-07 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-3 violated - No matching asset type excludes format
    Given the registry has format "text-only" with assets of type "text"
    When the Buyer Agent requests formats with asset_types filter ["video"]
    Then the response is compliant with the list_creative_formats spec
    And "text-only" should not be returned
    # BR-RULE-049 INV-3: no matching asset type -> format excluded

  @T-UC-005-inv-049-3-group @UC-005-MAIN-MCP-07 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-3 edge - Group assets checked in addition to individual assets
    Given the registry has format "rich-media" with a repeatable asset group containing "image" and "text"
    When the Buyer Agent requests formats with asset_types filter ["text"]
    Then the response is compliant with the list_creative_formats spec
    And "rich-media" should be returned
    # BR-RULE-049 INV-3: both individual and group assets checked
    # --- INV-4: dimension ANY render match ---

  @T-UC-005-inv-049-4-holds @UC-005-MAIN-MCP-08 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-4 holds - Dimension filter matches any render
    Given the registry has format "companion-ad" with renders:
    | width | height |
    | 300   | 250    |
    | 728   | 90     |
    When the Buyer Agent requests formats with min_width 700
    Then the response is compliant with the list_creative_formats spec
    And "companion-ad" should be returned
    # BR-RULE-049 INV-4: ANY render satisfies constraint (728 >= 700)

  @T-UC-005-inv-049-4-violated @UC-005-MAIN-MCP-09 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-4 violated - No render fits dimension filter
    Given the registry has format "small-banner" with renders:
    | width | height |
    | 300   | 250    |
    | 320   | 50     |
    When the Buyer Agent requests formats with min_width 700
    Then the response is compliant with the list_creative_formats spec
    And "small-banner" should not be returned
    # BR-RULE-049 INV-4: no render satisfies min_width 700

  @T-UC-005-inv-049-4-nodim @UC-005-MAIN-MCP-09 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-4 edge - Formats without dimensions excluded by dimension filter
    Given the registry has format "audio-spot" with no render dimensions
    When the Buyer Agent requests formats with min_width 100
    Then the response is compliant with the list_creative_formats spec
    And "audio-spot" should not be returned
    # BR-RULE-049 INV-4: formats without dimension info excluded
    # --- INV-5: is_responsive=true ---

  @T-UC-005-inv-049-5-holds @UC-005-MAIN-MCP-10 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-5 holds - Responsive filter returns only responsive formats
    Given the registry has format "responsive-banner" with responsive render dimensions
    And the registry has format "fixed-banner" with non-responsive render dimensions
    When the Buyer Agent requests formats with is_responsive true
    Then the response is compliant with the list_creative_formats spec
    And only "responsive-banner" should be returned
    # BR-RULE-049 INV-5: is_responsive=true -> only formats with responsive render dimension
    # --- INV-6: is_responsive=false ---

  @T-UC-005-inv-049-6-holds @UC-005-MAIN-MCP-10 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-6 holds - Non-responsive filter returns only non-responsive formats
    Given the registry has format "responsive-banner" with responsive render dimensions
    And the registry has format "fixed-banner" with non-responsive render dimensions
    When the Buyer Agent requests formats with is_responsive false
    Then the response is compliant with the list_creative_formats spec
    And only "fixed-banner" should be returned
    # BR-RULE-049 INV-6: is_responsive=false -> only formats with no responsive dimensions
    # --- INV-7: name_search case-insensitive substring ---

  @T-UC-005-inv-049-7-holds @UC-005-MAIN-MCP-11 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-7 holds - Name search case-insensitive substring match
    Given the registry has format named "Premium Leaderboard"
    And the registry has format named "Standard Banner"
    When the Buyer Agent requests formats with name_search "leader"
    Then the response is compliant with the list_creative_formats spec
    And only "Premium Leaderboard" should be returned
    # BR-RULE-049 INV-7: case-insensitive substring match

  @T-UC-005-inv-049-7-violated @UC-005-MAIN-MCP-11 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-7 violated - Name search no match excluded
    Given the registry has format named "Standard Banner"
    When the Buyer Agent requests formats with name_search "video"
    Then the response is compliant with the list_creative_formats spec
    And no formats should be returned
    # BR-RULE-049 INV-7: no substring match -> excluded
    # --- INV-8: disclosure_positions AND-match (NEW) ---

  @T-UC-005-inv-049-8-holds @UC-005-MAIN-MCP-18 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-8 holds - Disclosure positions AND-match filter
    Given the registry has format "video-ad" with supported_disclosure_positions ["prominent", "footer", "overlay"]
    And the registry has format "audio-ad" with supported_disclosure_positions ["prominent", "audio"]
    When the Buyer Agent requests formats with disclosure_positions filter ["prominent", "footer"]
    Then the response is compliant with the list_creative_formats spec
    And only "video-ad" should be returned
    # BR-RULE-049 INV-8: ALL requested positions must be supported (AND semantics)

  @T-UC-005-inv-049-8-violated @UC-005-MAIN-MCP-18 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-8 violated - Disclosure positions partial match excluded
    Given the registry has format "audio-ad" with supported_disclosure_positions ["prominent", "audio"]
    And the registry has format "exact-ad" with supported_disclosure_positions ["prominent", "footer"]
    When the Buyer Agent requests formats with disclosure_positions filter ["prominent", "footer"]
    Then the response is compliant with the list_creative_formats spec
    And "audio-ad" should not be returned
    And "exact-ad" should be returned
    # BR-RULE-049 INV-8: format only supports "prominent" not "footer" -> excluded
    #
    # "exact-ad" is a POSITIVE CONTROL, added when this row was graduated. The scenario
    # asserted an exclusion and nothing else, so it passed on ANY empty result -- which is
    # how it XPASSED for a whole release while production applied no disclosure filter at
    # all: the seller returned an empty catalog for an unrelated reason (the UC-005 route
    # seeded no tenant) and "audio-ad is absent" held vacuously. The control makes the
    # Then discriminate: the filter must REMOVE the partial match and KEEP the exact one,
    # so neither an empty catalog nor an unfiltered one can satisfy it. Distinct from
    # @T-UC-005-inv-049-8-holds, which grades a SUPERSET match
    # (["prominent","footer","overlay"]); this row grades an EXACT match against a partial
    # one.
    # @source repo=adcp ref=v3.1.1 path=static/schemas/source/media-buy/list-creative-formats-request.json

  @T-UC-005-inv-049-8-nofield @UC-005-MAIN-MCP-18 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-8 edge - Format without disclosure positions excluded
    Given the registry has format "basic-banner" with no supported_disclosure_positions field
    And the registry has format "disclosing-banner" with supported_disclosure_positions ["prominent"]
    When the Buyer Agent requests formats with disclosure_positions filter ["prominent"]
    Then the response is compliant with the list_creative_formats spec
    And "basic-banner" should not be returned
    And "disclosing-banner" should be returned
    # BR-RULE-049 INV-8: formats without supported_disclosure_positions excluded
    #
    # "disclosing-banner" is a POSITIVE CONTROL, added when this row was graduated, for
    # the same reason as the sibling -violated row: an exclusion-only Then passes on any
    # empty result, and this row XPASSED vacuously on exactly that. The control pins that
    # an UNDECLARED format is dropped while a DECLARED one survives the same request --
    # the pinned obligation is a discrimination between the two, not an absence.
    #
    # The obligation is core/format.json on supported_disclosure_positions: "When omitted,
    # the format makes no disclosure rendering guarantees -- creative agents SHOULD treat
    # this as incompatible with briefs that require specific disclosure positions."
    # @source repo=adcp ref=v3.1.1 path=static/schemas/source/core/format.json
    # --- INV-9: output_format_ids OR-match (NEW) ---

  @T-UC-005-inv-049-8-capabilities @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-8 holds - Disclosure positions matched via disclosure_capabilities
    Given the registry has format "structured-ad" with disclosure_capabilities positions ["prominent", "footer"]
    And "structured-ad" has no supported_disclosure_positions field
    When the Buyer Agent requests formats with disclosure_positions filter ["prominent", "footer"]
    Then the response is compliant with the list_creative_formats spec
    And only "structured-ad" should be returned
    # BR-RULE-049 INV-8 (v3.1): match against disclosure_capabilities[].position when present
    # --- INV-9: output_format_ids OR-match (NEW) ---
    # @source repo=adcp ref=v3.1.1 commit=467fd93d7 path=static/schemas/source/media-buy/list-creative-formats-request.json

  @T-UC-005-inv-049-9-holds @UC-005-MAIN-MCP-19 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-9 holds - Output format IDs OR-match filter
    Given the registry has format "universal-builder" with output_format_ids:
    | agent_url                                    | id             |
    | https://creatives.adcontextprotocol.org      | display_static |
    | https://creatives.adcontextprotocol.org      | video_hosted   |
    And the registry has format "audio-builder" with output_format_ids:
    | agent_url                                    | id             |
    | https://creatives.adcontextprotocol.org      | audio_ad       |
    When the Buyer Agent requests formats with output_format_ids filter [{"agent_url": "https://creatives.adcontextprotocol.org", "id": "display_static"}]
    Then the response is compliant with the list_creative_formats spec
    And only "universal-builder" should be returned
    # BR-RULE-049 INV-9: ANY requested ID matches -> format included (OR semantics)

  @T-UC-005-inv-049-9-violated @UC-005-MAIN-MCP-19 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-9 violated - Output format IDs no match excluded
    Given the registry has format "audio-builder" with output_format_ids:
    | agent_url                                    | id        |
    | https://creatives.adcontextprotocol.org      | audio_ad  |
    When the Buyer Agent requests formats with output_format_ids filter [{"agent_url": "https://creatives.adcontextprotocol.org", "id": "display_static"}]
    Then the response is compliant with the list_creative_formats spec
    And "audio-builder" should not be returned
    # BR-RULE-049 INV-9: no matching output ID -> excluded

  @T-UC-005-inv-049-9-nofield @UC-005-MAIN-MCP-19 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-9 edge - Format without output_format_ids excluded
    Given the registry has format "simple-banner" with no output_format_ids field
    When the Buyer Agent requests formats with output_format_ids filter [{"agent_url": "https://creatives.adcontextprotocol.org", "id": "display_static"}]
    Then the response is compliant with the list_creative_formats spec
    And "simple-banner" should not be returned
    # BR-RULE-049 INV-9: formats without output_format_ids excluded
    # --- INV-10: input_format_ids OR-match (NEW) ---

  @T-UC-005-inv-049-10-holds @UC-005-MAIN-MCP-20 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-10 holds - Input format IDs OR-match filter
    Given the registry has format "resizer" with input_format_ids:
    | agent_url                                    | id             |
    | https://creatives.adcontextprotocol.org      | display_static |
    | https://creatives.adcontextprotocol.org      | display_animated |
    And the registry has format "transcoder" with input_format_ids:
    | agent_url                                    | id             |
    | https://creatives.adcontextprotocol.org      | video_hosted   |
    When the Buyer Agent requests formats with input_format_ids filter [{"agent_url": "https://creatives.adcontextprotocol.org", "id": "display_static"}]
    Then the response is compliant with the list_creative_formats spec
    And only "resizer" should be returned
    # BR-RULE-049 INV-10: ANY requested ID matches -> format included (OR semantics)

  @T-UC-005-inv-049-10-violated @UC-005-MAIN-MCP-20 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-10 violated - Input format IDs no match excluded
    Given the registry has format "transcoder" with input_format_ids:
    | agent_url                                    | id           |
    | https://creatives.adcontextprotocol.org      | video_hosted |
    When the Buyer Agent requests formats with input_format_ids filter [{"agent_url": "https://creatives.adcontextprotocol.org", "id": "display_static"}]
    Then the response is compliant with the list_creative_formats spec
    And "transcoder" should not be returned
    # BR-RULE-049 INV-10: no matching input ID -> excluded

  @T-UC-005-inv-049-10-nofield @UC-005-MAIN-MCP-20 @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-10 edge - Format without input_format_ids excluded
    Given the registry has format "basic-display" with no input_format_ids field
    When the Buyer Agent requests formats with input_format_ids filter [{"agent_url": "https://creatives.adcontextprotocol.org", "id": "display_static"}]
    Then the response is compliant with the list_creative_formats spec
    And "basic-display" should not be returned
    # BR-RULE-049 INV-10: formats without input_format_ids excluded (works from raw assets)

  @T-UC-005-inv-049-11-holds @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-11 holds - Persistence modes satisfied across positions
    Given the registry has format "eu-compliant" with disclosure_capabilities:
    | position  | persistence |
    | prominent | continuous  |
    | footer    | initial     |
    When the Buyer Agent requests formats with disclosure_persistence filter ["continuous", "initial"]
    Then the response is compliant with the list_creative_formats spec
    And only "eu-compliant" should be returned
    # BR-RULE-049 INV-11: each requested mode satisfied by >=1 position (AND across modes, existential across positions)
    # @source repo=adcp ref=v3.1.1 commit=467fd93d7 path=static/schemas/source/media-buy/list-creative-formats-request.json

  @T-UC-005-inv-049-11-violated @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-11 violated - Unsatisfiable persistence mode excludes format
    Given the registry has format "flex-only" with disclosure_capabilities:
    | position | persistence |
    | footer   | flexible    |
    When the Buyer Agent requests formats with disclosure_persistence filter ["continuous"]
    Then the response is compliant with the list_creative_formats spec
    And "flex-only" should not be returned
    # BR-RULE-049 INV-11: no position supports "continuous" -> excluded
    # @source repo=adcp ref=v3.1.1 commit=467fd93d7 path=static/schemas/source/media-buy/list-creative-formats-request.json

  @T-UC-005-inv-049-11-nofield @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-11 edge - Format without disclosure_capabilities excluded
    Given the registry has format "legacy-banner" with no disclosure_capabilities field
    When the Buyer Agent requests formats with disclosure_persistence filter ["initial"]
    Then the response is compliant with the list_creative_formats spec
    And "legacy-banner" should not be returned
    # BR-RULE-049 INV-11: formats without disclosure_capabilities cannot declare persistence -> excluded

  @T-UC-005-empty-catalog @UC-005-MAIN-MCP-01 @edge-case
  Scenario: Empty catalog when no agents have formats
    Given no creative agents have any registered formats
    When the Buyer Agent requests the format catalog
    Then the response is compliant with the list_creative_formats spec
    And no formats should be returned
    And no error should be returned
    # Edge case: PRE-B1 boundary — no formats available

  @T-UC-005-dim-boundary @UC-005-MAIN-MCP-08 @boundary @BR-RULE-049
  Scenario: Dimension boundary - inclusive range at threshold
    Given the registry has format "exact-fit" with render width 728 and height 90
    When the Buyer Agent requests formats with min_width 728 and max_width 728
    Then the response is compliant with the list_creative_formats spec
    And "exact-fit" should be returned
    # BR-RULE-049 INV-4: dimension range is inclusive (width == min_width == max_width)

  @T-UC-005-ext-a @extension @ext-a @error @post-f1 @post-f2 @post-f3
  Scenario: No tenant context
    Given the Buyer has no authentication credentials
    And no hostname-based tenant resolution is possible
    When the Buyer Agent requests the format catalog
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "AUTH_MISSING"
    And the error recovery classification should be "correctable"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: The buyer is told WHICH condition failed by the CODE. TENANT_REQUIRED
    #   was a minted code no raise site can emit; production's require_tenant
    #   (auth.py:362) raises on the credential-presented axis, so "no credentials
    #   at all" is AUTH_MISSING -- a published member with the same correctable
    #   recovery. The old "message should indicate tenant context could not be
    #   determined" assertion cannot hold once the sentence is derived from the
    #   code, and it graded a copy of CODE_TABLE's own text anyway
    #   (#1753).
    # POST-F3: Suggestion advises providing auth -- AUTH_MISSING's table entry is
    #   "provide credentials via the auth header and retry".
    # --- ext-b: Invalid Request Parameters ---

  @T-UC-005-ext-b @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Invalid request parameters
    Given a tenant is resolvable from the request context
    When the Buyer Agent requests formats with type "not_a_category"
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "type"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains which parameters are invalid and why
    # POST-F3: Suggestion provides valid values or format guidance
    # --- ext-b: Disclosure Positions Validation Errors (NEW) ---

  @T-UC-005-ext-b-disclosure-invalid @UC-005-EXT-B-10 @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Invalid disclosure position value
    Given a tenant is resolvable from the request context
    When the Buyer Agent requests formats with disclosure_positions filter ["sidebar"]
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "disclosure_positions"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains invalid value
    # POST-F3: Suggestion lists valid enum values

  @T-UC-005-ext-b-disclosure-empty @UC-005-EXT-B-11 @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Empty disclosure positions array
    Given a tenant is resolvable from the request context
    When the Buyer Agent requests formats with disclosure_positions filter []
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "disclosure_positions"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains minItems violation
    # POST-F3: Suggestion for recovery

  @T-UC-005-ext-b-disclosure-dupes @UC-005-EXT-B-12 @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Duplicate disclosure positions
    Given a tenant is resolvable from the request context
    When the Buyer Agent requests formats with disclosure_positions filter ["prominent", "prominent"]
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "disclosure_positions"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains uniqueItems violation
    # POST-F3: Suggestion for recovery
    # --- ext-b: Output Format IDs Validation Errors (NEW) ---

  @T-UC-005-ext-b-persistence-invalid @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Invalid disclosure persistence value
    Given a tenant is resolvable from the request context
    When the Buyer Agent requests formats with disclosure_persistence filter ["permanent"]
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "disclosure_persistence"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains invalid enum value
    # POST-F3: Suggestion lists valid enum values
    # @source repo=adcp ref=v3.1.1 commit=467fd93d7 path=static/schemas/source/media-buy/list-creative-formats-request.json

  @T-UC-005-ext-b-persistence-empty @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Empty disclosure persistence array
    Given a tenant is resolvable from the request context
    When the Buyer Agent requests formats with disclosure_persistence filter []
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "disclosure_persistence"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains minItems violation
    # POST-F3: Suggestion for recovery
    # @source repo=adcp ref=v3.1.1 commit=467fd93d7 path=static/schemas/source/media-buy/list-creative-formats-request.json

  @T-UC-005-ext-b-persistence-dupes @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Duplicate disclosure persistence modes
    Given a tenant is resolvable from the request context
    When the Buyer Agent requests formats with disclosure_persistence filter ["continuous", "continuous"]
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "disclosure_persistence"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains uniqueItems violation
    # POST-F3: Suggestion for recovery
    # --- ext-b: Output Format IDs Validation Errors (NEW) ---
    # @source repo=adcp ref=v3.1.1 commit=467fd93d7 path=static/schemas/source/media-buy/list-creative-formats-request.json

  @T-UC-005-ext-b-output-empty @UC-005-EXT-B-13 @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Empty output format IDs array
    Given a tenant is resolvable from the request context
    When the Buyer Agent requests formats with output_format_ids filter []
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "output_format_ids"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains minItems violation
    # POST-F3: Suggestion for recovery

  @T-UC-005-ext-b-output-invalid @UC-005-EXT-B-14 @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Invalid output format ID structure - missing agent_url
    Given a tenant is resolvable from the request context
    When the Buyer Agent requests formats with output_format_ids filter [{"id": "display_static"}]
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "output_format_ids"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains invalid structure
    # POST-F3: Suggestion for correct FormatId structure

  @T-UC-005-ext-b-output-noid @UC-005-EXT-B-14 @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Invalid output format ID structure - missing id
    Given a tenant is resolvable from the request context
    When the Buyer Agent requests formats with output_format_ids filter [{"agent_url": "https://example.com"}]
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "output_format_ids"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains invalid structure
    # POST-F3: Suggestion for correct FormatId structure
    # --- ext-b: Input Format IDs Validation Errors (NEW) ---

  @T-UC-005-ext-b-input-empty @UC-005-EXT-B-15 @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Empty input format IDs array
    Given a tenant is resolvable from the request context
    When the Buyer Agent requests formats with input_format_ids filter []
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "input_format_ids"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains minItems violation
    # POST-F3: Suggestion for recovery

  @T-UC-005-ext-b-input-invalid @UC-005-EXT-B-16 @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Invalid input format ID structure - missing agent_url
    Given a tenant is resolvable from the request context
    When the Buyer Agent requests formats with input_format_ids filter [{"id": "display_static"}]
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "input_format_ids"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains invalid structure
    # POST-F3: Suggestion for correct FormatId structure

  @T-UC-005-ext-b-input-noid @UC-005-EXT-B-16 @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Invalid input format ID structure - missing id
    Given a tenant is resolvable from the request context
    When the Buyer Agent requests formats with input_format_ids filter [{"agent_url": "https://example.com"}]
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "input_format_ids"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: Error explains invalid structure
    # POST-F3: Suggestion for correct FormatId structure

  # ── Rejection outcomes name their AdCP error code ──────────────────────────
  # The outcome column of every "Invalid partitions" / boundary-rejection table below
  # carries the CODE the seller must return, not a bare "invalid". A bare marker could
  # be satisfied by any failure anywhere -- including a pydantic error raised in the
  # TEST process before the payload ever crossed a transport, which is what these rows
  # actually graded until salesagent-prkv.65 made the When steps dispatch raw.
  #
  # INVALID_REQUEST is the code for all of them because each invalid row violates a
  # SCHEMA constraint (a value outside a pinned enum, an array below minItems=1, a
  # FormatId missing a required member) rather than a business rule:
  #   INVALID_REQUEST  "Request is malformed, missing required fields, or violates
  #                     schema constraints. Recovery: correctable."
  #   VALIDATION_ERROR "Request contains invalid field values or violates business
  #                     rules BEYOND schema validation."
  # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/INVALID_REQUEST
  # (pinned locally at tests/fixtures/adcp_schemas_pinned/enums/error-code.json:102)
  # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/media-buy/list-creative-formats-request.json
  #   pointer=/properties/asset_types/minItems  (minItems=1 also on format_ids,
  #   disclosure_positions, output_format_ids, input_format_ids)
  # This matches the codes the ext-b error scenarios above already assert for the same
  # conditions, so one feature no longer names two codes for one rejection.

  @T-UC-005-partition-type-filter @partition @format_type_filter
  Scenario Outline: Format type filter partition - <partition>
    Given a seller with formats of various types
    When the Buyer Agent requests creative formats with type filter "<partition>"
    Then the response is compliant with the list_creative_formats spec
    And the type filtering should result in <expected>

    Examples: Valid partitions
      | partition     | expected |
      | display       | valid    |
      | video         | valid    |
      | audio         | valid    |
      | dooh          | valid    |
      | omitted       | valid    |

    Examples: Invalid partitions
      | partition     | expected |
      | invalid_type  | INVALID_REQUEST  |

  @T-UC-005-partition-format-ids @UC-005-MAIN-MCP-06 @partition @format_ids_filter
  Scenario Outline: Format IDs filter partition - <partition>
    Given a seller with known format IDs in the catalog
    When the Buyer Agent requests creative formats with format_ids "<partition>"
    Then the response is compliant with the list_creative_formats spec
    And the format_ids filtering should result in <expected>

    Examples: Valid partitions
      | partition       | expected |
      | all_ids_match   | valid    |
      | partial_match   | valid    |
      | no_match        | valid    |
      | omitted         | valid    |

  @T-UC-005-partition-asset-types @UC-005-MAIN-MCP-07 @partition @asset_types_filter
  Scenario Outline: Asset types filter partition - <partition>
    Given a seller with formats containing various asset types
    When the Buyer Agent requests creative formats with asset_types "<partition>"
    Then the response is compliant with the list_creative_formats spec
    And the asset_types filtering should result in <expected>

    Examples: Valid partitions
      | partition            | expected |
      | single_type_match    | valid    |
      | multiple_types_or    | valid    |
      | omitted              | valid    |

    Examples: Invalid partitions
      | partition                   | expected |
      | no_matching_formats         | valid    |
      | unknown_asset_type          | INVALID_REQUEST  |
      | removed_promoted_offerings  | INVALID_REQUEST  |

  @T-UC-005-partition-dimension @UC-005-MAIN-MCP-08 @partition @dimension_filter
  Scenario Outline: Dimension filter partition - <partition>
    Given a seller with formats of various render dimensions
    When the Buyer Agent requests creative formats with dimension filter "<partition>"
    Then the response is compliant with the list_creative_formats spec
    And the dimension filtering should result in <expected>

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

  @T-UC-005-partition-responsive @UC-005-MAIN-MCP-10 @partition @is_responsive_filter
  Scenario Outline: Responsive filter partition - <partition>
    Given a seller with both responsive and fixed-dimension formats
    When the Buyer Agent requests creative formats with is_responsive "<partition>"
    Then the response is compliant with the list_creative_formats spec
    And the responsive filtering should result in <expected>

    Examples: Valid partitions
      | partition         | expected |
      | responsive_true   | valid    |
      | responsive_false  | valid    |
      | omitted           | valid    |

  @T-UC-005-partition-name-search @UC-005-MAIN-MCP-11 @partition @name_search_filter
  Scenario Outline: Name search filter partition - <partition>
    Given a seller with formats named "Standard Banner", "Video Interstitial", "Native Card"
    When the Buyer Agent requests creative formats with name_search "<partition>"
    Then the response is compliant with the list_creative_formats spec
    And the name search filtering should result in <expected>

    Examples: Valid partitions
      | partition         | expected |
      | exact_name        | valid    |
      | partial_match     | valid    |
      | case_insensitive  | valid    |
      | omitted           | valid    |

    Examples: Invalid partitions
      | partition         | expected |
      | no_match          | valid    |

  @T-UC-005-partition-wcag @UC-005-MAIN-MCP-12 @partition @wcag_level
  Scenario Outline: WCAG level filter partition - <partition>
    Given a seller with formats at various accessibility conformance levels
    When the Buyer Agent requests creative formats with wcag_level "<partition>"
    Then the response is compliant with the list_creative_formats spec
    And the wcag filtering should result in <expected>

    Examples: Valid partitions
      | partition     | expected |
      | level_a       | valid    |
      | level_aa      | valid    |
      | level_aaa     | valid    |
      | not_provided  | valid    |

    Examples: Invalid partitions
      | partition      | expected |
      | unknown_value  | INVALID_REQUEST  |

  @T-UC-005-partition-disclosure @UC-005-MAIN-MCP-18 @partition @disclosure_positions
  Scenario Outline: Disclosure positions filter partition - <partition>
    Given a seller with formats supporting various disclosure positions
    When the Buyer Agent requests creative formats with disclosure_positions "<partition>"
    Then the response is compliant with the list_creative_formats spec
    And the disclosure_positions filtering should result in <expected>

    Examples: Valid partitions
      | partition                      | expected |
      | single_position                | valid    |
      | multiple_positions_all_match   | valid    |
      | all_positions                  | valid    |
      | omitted                        | valid    |
      | no_matching_formats            | valid    |

    Examples: Invalid partitions
      | partition            | expected |
      | unknown_position     | INVALID_REQUEST  |
      | empty_array          | INVALID_REQUEST  |
      | duplicate_positions  | INVALID_REQUEST  |

  @T-UC-005-partition-disclosure-persistence @partition @disclosure_persistence
  Scenario Outline: Disclosure persistence filter partition - <partition>
    Given a seller with formats declaring various disclosure persistence capabilities
    When the Buyer Agent requests creative formats with disclosure_persistence "<partition>"
    Then the response is compliant with the list_creative_formats spec
    And the disclosure_persistence filtering should result in <expected>

    Examples: Valid partitions
      | partition                                  | expected |
      | single_mode                                | valid    |
      | multiple_modes_satisfied_across_positions  | valid    |
      | all_modes                                  | valid    |
      | omitted                                    | valid    |
      | no_matching_formats                        | valid    |
      | format_without_disclosure_capabilities     | valid    |

    Examples: Invalid partitions
      | partition       | expected |
      | unknown_mode    | INVALID_REQUEST  |
      | empty_array     | INVALID_REQUEST  |
      | duplicate_modes | INVALID_REQUEST  |

  @T-UC-005-partition-output-fmtids @UC-005-MAIN-MCP-19 @partition @output_format_ids
  Scenario Outline: Output format IDs filter partition - <partition>
    Given a seller with formats that produce various output formats
    When the Buyer Agent requests creative formats with output_format_ids "<partition>"
    Then the response is compliant with the list_creative_formats spec
    And the output_format_ids filtering should result in <expected>

    Examples: Valid partitions
      | partition                    | expected |
      | single_format_id             | valid    |
      | multiple_ids_any_match       | valid    |
      | omitted                      | valid    |
      | no_matching_formats          | valid    |
      | format_without_output_ids    | valid    |

    Examples: Invalid partitions
      | partition                           | expected |
      | empty_array                         | INVALID_REQUEST  |
      | invalid_format_id_missing_agent_url | INVALID_REQUEST  |
      | invalid_format_id_missing_id        | INVALID_REQUEST  |

  @T-UC-005-partition-input-fmtids @UC-005-MAIN-MCP-20 @partition @input_format_ids
  Scenario Outline: Input format IDs filter partition - <partition>
    Given a seller with formats that accept various input formats
    When the Buyer Agent requests creative formats with input_format_ids "<partition>"
    Then the response is compliant with the list_creative_formats spec
    And the input_format_ids filtering should result in <expected>

    Examples: Valid partitions
      | partition                    | expected |
      | single_format_id             | valid    |
      | multiple_ids_any_match       | valid    |
      | omitted                      | valid    |
      | no_matching_formats          | valid    |
      | format_without_input_ids     | valid    |

    Examples: Invalid partitions
      | partition                           | expected |
      | empty_array                         | INVALID_REQUEST  |
      | invalid_format_id_missing_agent_url | INVALID_REQUEST  |
      | invalid_format_id_missing_id        | INVALID_REQUEST  |

  @T-UC-005-boundary-type-filter @boundary @format_type_filter
  Scenario Outline: Format type filter boundary - <boundary_point>
    Given a seller with formats of various types
    When the Buyer Agent requests creative formats at type boundary "<boundary_point>"
    Then the response is compliant with the list_creative_formats spec
    And the type handling should be <expected>

    Examples:
      | boundary_point              | expected |
      | display (valid enum)        | valid    |
      | video (valid enum)          | valid    |
      | audio (valid enum)          | valid    |
      | dooh (valid enum)           | valid    |
      | omitted (no filter)         | valid    |
      | invalid type (rejected)     | INVALID_REQUEST  |

  @T-UC-005-boundary-format-ids @UC-005-MAIN-MCP-06 @boundary @format_ids_filter
  Scenario Outline: Format IDs filter boundary - <boundary_point>
    Given a seller with known format IDs in the catalog
    When the Buyer Agent requests creative formats at format_ids boundary "<boundary_point>"
    Then the response is compliant with the list_creative_formats spec
    And the format_ids handling should be <expected>

    Examples:
      | boundary_point                      | expected |
      | all IDs match                       | valid    |
      | partial match (some excluded)       | valid    |
      | no IDs match (empty result)         | valid    |
      | omitted (no filter)                 | valid    |

  @T-UC-005-boundary-asset-types @UC-005-MAIN-MCP-07 @boundary @asset_types_filter
  Scenario Outline: Asset types filter boundary - <boundary_point>
    Given a seller with formats containing various asset types
    When the Buyer Agent requests creative formats at asset_types boundary "<boundary_point>"
    Then the response is compliant with the list_creative_formats spec
    And the asset_types handling should be <expected>

    Examples:
      | boundary_point                                    | expected |
      | single asset type match                           | valid    |
      | multiple types OR semantics                       | valid    |
      | omitted (no filter)                               | valid    |
      | brief (new asset type for generative formats)     | valid    |
      | catalog (new asset type for catalog-based formats) | valid    |
      | no formats match (empty result)                   | valid    |
      | Unknown string not in enum                        | INVALID_REQUEST  |
      | promoted_offerings (removed from enum)            | INVALID_REQUEST  |

  @T-UC-005-boundary-dimension @UC-005-MAIN-MCP-08 @boundary @dimension_filter
  Scenario Outline: Dimension filter boundary - <boundary_point>
    Given a seller with formats of various render dimensions
    When the Buyer Agent requests creative formats at dimension boundary "<boundary_point>"
    Then the response is compliant with the list_creative_formats spec
    And the dimension handling should be <expected>

    Examples:
      | boundary_point                    | expected |
      | width filter only                 | valid    |
      | height filter only                | valid    |
      | width and height combined         | valid    |
      | omitted (no dimension filter)     | valid    |
      | no render matches constraints     | valid    |

  @T-UC-005-boundary-responsive @UC-005-MAIN-MCP-10 @boundary @is_responsive_filter
  Scenario Outline: Responsive filter boundary - <boundary_point>
    Given a seller with both responsive and fixed-dimension formats
    When the Buyer Agent requests creative formats at responsive boundary "<boundary_point>"
    Then the response is compliant with the list_creative_formats spec
    And the responsive handling should be <expected>

    Examples:
      | boundary_point            | expected |
      | is_responsive = true      | valid    |
      | is_responsive = false     | valid    |
      | is_responsive omitted     | valid    |

  @T-UC-005-boundary-name-search @UC-005-MAIN-MCP-11 @boundary @name_search_filter
  Scenario Outline: Name search filter boundary - <boundary_point>
    Given a seller with formats named "Standard Banner", "Video Interstitial", "Native Card"
    When the Buyer Agent requests creative formats at name_search boundary "<boundary_point>"
    Then the response is compliant with the list_creative_formats spec
    And the name search handling should be <expected>

    Examples:
      | boundary_point              | expected |
      | exact name match            | valid    |
      | partial substring match     | valid    |
      | case-insensitive match      | valid    |
      | omitted (no filter)         | valid    |
      | no match (empty result)     | valid    |

  @T-UC-005-boundary-wcag @UC-005-MAIN-MCP-12 @boundary @wcag_level
  Scenario Outline: WCAG level filter boundary - <boundary_point>
    Given a seller with formats at various accessibility conformance levels
    When the Buyer Agent requests creative formats at wcag_level boundary "<boundary_point>"
    Then the response is compliant with the list_creative_formats spec
    And the wcag handling should be <expected>

    Examples:
      | boundary_point                                   | expected |
      | A (first enum value — minimum conformance)       | valid    |
      | AAA (last enum value — highest conformance)      | valid    |
      | Not provided (no filter)                         | valid    |
      | Unknown string not in enum                       | INVALID_REQUEST  |

  @T-UC-005-boundary-disclosure @UC-005-MAIN-MCP-18 @boundary @disclosure_positions
  Scenario Outline: Disclosure positions filter boundary - <boundary_point>
    Given a seller with formats supporting various disclosure positions
    When the Buyer Agent requests creative formats at disclosure boundary "<boundary_point>"
    Then the response is compliant with the list_creative_formats spec
    And the disclosure handling should be <expected>

    Examples:
      | boundary_point                                          | expected |
      | single position ['prominent'] (min array size)          | valid    |
      | all 8 positions (max meaningful array)                  | valid    |
      | omitted (no filter)                                     | valid    |
      | format has no supported_disclosure_positions (excluded)  | valid    |
      | empty array []                                          | INVALID_REQUEST  |
      | unknown position string 'sidebar'                       | INVALID_REQUEST  |
      | duplicate positions ['prominent','prominent']           | INVALID_REQUEST  |

  @T-UC-005-boundary-disclosure-persistence @boundary @disclosure_persistence
  Scenario Outline: Disclosure persistence filter boundary - <boundary_point>
    Given a seller with formats declaring various disclosure persistence capabilities
    When the Buyer Agent requests creative formats at persistence boundary "<boundary_point>"
    Then the response is compliant with the list_creative_formats spec
    And the persistence handling should be <expected>

    Examples:
      | boundary_point                                    | expected |
      | single mode ['continuous'] (min array size)        | valid    |
      | all 3 modes (max array size)                       | valid    |
      | two modes satisfied by different positions         | valid    |
      | omitted (no filter)                                | valid    |
      | format has no disclosure_capabilities (excluded)   | valid    |
      | no format covers all requested modes               | valid    |
      | empty array []                                     | INVALID_REQUEST  |
      | unknown mode string 'permanent'                    | INVALID_REQUEST  |
      | duplicate modes ['continuous','continuous']        | INVALID_REQUEST  |

  @T-UC-005-boundary-output-fmtids @UC-005-MAIN-MCP-19 @boundary @output_format_ids
  Scenario Outline: Output format IDs filter boundary - <boundary_point>
    Given a seller with formats that produce various output formats
    When the Buyer Agent requests creative formats at output_format_ids boundary "<boundary_point>"
    Then the response is compliant with the list_creative_formats spec
    And the output_format_ids handling should be <expected>

    Examples:
      | boundary_point                                   | expected |
      | single FormatId (min array size)                 | valid    |
      | multiple FormatIds, one matches (ANY semantics)  | valid    |
      | omitted (no filter)                              | valid    |
      | format has no output_format_ids (excluded)       | valid    |
      | no formats match requested output IDs            | valid    |
      | empty array []                                   | INVALID_REQUEST  |
      | FormatId missing agent_url                       | INVALID_REQUEST  |
      | FormatId missing id                              | INVALID_REQUEST  |

  @T-UC-005-boundary-input-fmtids @UC-005-MAIN-MCP-20 @boundary @input_format_ids
  Scenario Outline: Input format IDs filter boundary - <boundary_point>
    Given a seller with formats that accept various input formats
    When the Buyer Agent requests creative formats at input_format_ids boundary "<boundary_point>"
    Then the response is compliant with the list_creative_formats spec
    And the input_format_ids handling should be <expected>

    Examples:
      | boundary_point                                   | expected |
      | single FormatId (min array size)                 | valid    |
      | multiple FormatIds, one matches (ANY semantics)  | valid    |
      | omitted (no filter)                              | valid    |
      | format has no input_format_ids (excluded)        | valid    |
      | no formats match requested input IDs             | valid    |
      | empty array []                                   | INVALID_REQUEST  |
      | FormatId missing agent_url                       | INVALID_REQUEST  |
      | FormatId missing id                              | INVALID_REQUEST  |

  @T-UC-005-partition-agent-type @UC-005-MAIN-MCP-21 @partition @creative_agent_format_type
  Scenario Outline: Creative agent format type partition - <partition>
    Given a seller with creative agent formats of various types
    When the Buyer Agent queries creative agent formats with type "<partition>"
    Then the response is compliant with the list_creative_formats spec
    And the creative agent type filtering should result in <expected>

    Examples: Valid partitions
      | partition     | expected |
      | audio         | valid    |
      | video         | valid    |
      | display       | valid    |
      | dooh          | valid    |
      | not_provided  | valid    |

    # Type filter REMOVED in adcp 3.12 — ListCreativeFormatsRequest has no
    # 'type' field, so every value dispatches unfiltered and production returns
    # the full catalog. The former 'unknown_value -> invalid' rejection no longer
    # exists; reconciled to valid (success).
    Examples: Formerly-invalid partitions (filter removed in 3.12, now valid)
      | partition      | expected |
      | unknown_value  | valid    |

  @T-UC-005-partition-agent-asset @UC-005-MAIN-MCP-22 @partition @creative_agent_asset_type
  Scenario Outline: Creative agent asset type partition - <partition>
    Given a seller with creative agent formats containing various asset types
    When the Buyer Agent queries creative agent formats with asset_types "<partition>"
    Then the response is compliant with the list_creative_formats spec
    And the creative agent asset type filtering should result in <expected>

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
      | unknown_value  | INVALID_REQUEST  |
      | empty_array    | INVALID_REQUEST  |

  @T-UC-005-boundary-agent-type @UC-005-MAIN-MCP-21 @boundary @creative_agent_format_type
  Scenario Outline: Creative agent format type boundary - <boundary_point>
    Given a seller with creative agent formats of various types
    When the Buyer Agent queries creative agent formats at type boundary "<boundary_point>"
    Then the response is compliant with the list_creative_formats spec
    And the creative agent type handling should be <expected>

    # Type filter REMOVED in adcp 3.12 — no 'type' field on the request, so
    # 'native' dispatches unfiltered like every other value; production no longer
    # rejects it. Reconciled to valid (success).
    Examples:
      | boundary_point                                                  | expected |
      | audio (first enum value)                                        | valid    |
      | dooh (last enum value)                                          | valid    |
      | Not provided (no filter)                                        | valid    |
      | native (valid in media-buy variant but not in creative agent)   | valid    |

  @T-UC-005-boundary-agent-asset @UC-005-MAIN-MCP-22 @boundary @creative_agent_asset_type
  Scenario Outline: Creative agent asset type boundary - <boundary_point>
    Given a seller with creative agent formats containing various asset types
    When the Buyer Agent queries creative agent formats at asset_types boundary "<boundary_point>"
    Then the response is compliant with the list_creative_formats spec
    And the creative agent asset type handling should be <expected>

    Examples:
      | boundary_point                                                  | expected |
      | image (first enum value)                                        | valid    |
      | url (last enum value)                                           | valid    |
      | Not provided (no filter)                                        | valid    |
      | vast (valid in media-buy variant but not in creative agent)     | INVALID_REQUEST  |
      | Empty array                                                     | INVALID_REQUEST  |

  @T-UC-005-sandbox-happy @UC-005-MAIN-MCP-23 @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account receives simulated creative formats with sandbox flag
    Given the Buyer is authenticated
    And the request targets a sandbox account
    When the Buyer Agent sends a list_creative_formats request
    Then the response is compliant with the list_creative_formats spec
    And the response status should be "completed"
    And the response should contain "formats" array
    And the response should include sandbox equals true
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-2: real ad platform calls suppressed
    # BR-RULE-209 INV-4: response includes sandbox: true

  @T-UC-005-sandbox-production @UC-005-MAIN-MCP-23 @invariant @br-rule-209 @sandbox
  Scenario: Production account creative formats response does not include sandbox flag
    Given the Buyer is authenticated
    And the request targets a production account
    When the Buyer Agent sends a list_creative_formats request
    Then the response is compliant with the list_creative_formats spec
    And the response status should be "completed"
    And the response should contain "formats" array
    And the response should not include a sandbox field
    # BR-RULE-209 INV-5: production account -> sandbox absent

  @T-UC-005-sandbox-validation @UC-005-MAIN-MCP-23 @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account with invalid filter returns real validation error
    Given the Buyer is authenticated
    And the request targets a sandbox account
    When the Buyer Agent sends a list_creative_formats request with invalid dimension filters
    Then the error is compliant with the AdCP error spec
    And the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

  @T-UC-005-boundary-sandbox @boundary @sandbox @br-rule-209
  Scenario Outline: Sandbox response flag boundary - <boundary_point>
    Given a seller handling a list_creative_formats request for the given account context
    When the Buyer Agent inspects the sandbox response flag at boundary "<boundary_point>"
    Then the response is compliant with the list_creative_formats spec
    And the sandbox response handling should be <expected>
    # BR-RULE-209 INV-4: sandbox account -> sandbox: true in response
    # BR-RULE-209 INV-5: production account -> sandbox absent in response
    # BR-RULE-209: explicit production may carry sandbox: false (still a non-simulated response)

    Examples:
      | boundary_point                                   | expected |
      | sandbox: true in response (sandbox account)      | valid    |
      | sandbox absent in response (production account)  | valid    |
      | sandbox: false in response (explicit production) | valid    |

  @T-UC-005-v31-asset-registry-discriminators @main-flow @v3-1 @asset-registry
  Scenario Outline: asset_types filter accepts registry discriminator values
    Given the seller catalog has at least one format containing an "<asset_type>" asset
    When the Buyer Agent requests creative formats with asset_types filter ["<asset_type>"]
    Then the response is compliant with the list_creative_formats spec
    And the response status should be "completed"
    And each returned format should contain at least one asset of type "<asset_type>"
    # v3.1: the asset_types FILTER enum is asset-content-type.json (14 values).
    # vast_tracker / daast_tracker are manifest-payload discriminators in
    # asset-types/index.json (16 keys) — NOT valid filter inputs.
    # @source repo=adcp ref=v3.1.1 commit=467fd93d7 path=static/schemas/source/media-buy/list-creative-formats-request.json

    Examples: 14 asset-content-type filter enum values
      | asset_type     |
      | image          |
      | video          |
      | audio          |
      | vast           |
      | daast          |
      | text           |
      | markdown       |
      | url            |
      | html           |
      | css            |
      | webhook        |
      | javascript     |
      | brief          |
      | catalog        |

  @T-UC-005-v31-asset-registry-no-variant-siblings @main-flow @v3-1 @asset-registry @validation
  Scenario: asset_types filter rejects nested-variant discriminators (no sibling registry entries)
    Given the Buyer is authenticated
    And the request targets a production account
    When the Buyer Agent sends a list_creative_formats request with asset_types ["vast_url"]
    Then the error is compliant with the AdCP error spec
    And the response should indicate a validation error
    And the error should indicate "asset_types" must use outer registry discriminators only
    # v3.1: registry forbids sibling entries like vast_url/vast_inline; use vast with inner delivery_type
    # @source repo=adcp ref=v3.1.1 commit=467fd93d7 path=static/schemas/source/media-buy/list-creative-formats-request.json

  @T-UC-005-v31-payload-vs-requirements-separation @main-flow @v3-1 @asset-registry
  Scenario: Format response carries constraints under requirements, not on the asset schema
    Given the seller catalog has a format with a required image asset capped at 2 MB
    When the Buyer Agent requests creative formats for that format
    Then the response is compliant with the list_creative_formats spec
    And the format's "assets" entry should expose "asset_type" with value "image"
    And the same entry should expose constraints under "requirements" (e.g., max_file_size)
    And the asset_type payload shape should NOT carry "max_file_size" or "required"
    # v3.1: registry mandates payload-vs-requirements separation

  @T-UC-005-storyboard-format-id-roundtrip-from-products @storyboard-v3.1 @v3-1 @format-id-roundtrip
  Scenario: Format ID roundtrip -- list_creative_formats returns the same format object that get_products advertised
    Given the Buyer Agent captured a format_id object {agent_url, id} from a prior get_products response
    When the Buyer Agent sends list_creative_formats with format_ids [{captured agent_url, captured id}]
    Then the response is compliant with the list_creative_formats spec
    And the formats array should contain at least one entry
    And formats[0].format_id should roundtrip verbatim with the captured {agent_url, id}
    And an empty formats[] would indicate a stale catalog reference and is a compliance failure
    # media-buy/index.yaml product_discovery / list_formats_integrity step: the buyer
    # captures products[0].format_ids[0] from a get_products response and asks
    # list_creative_formats to resolve it. The sales agent MUST return the format
    # it advertised on its own product -- whether it hosts that format directly or
    # proxies to the creative agent named in format_ids[0].agent_url. An empty
    # formats[] means the catalog references a stale or typo'd format that would
    # have failed silently at sync_creatives after the buy was already committed.
    # list_formats_integrity: format_ids advertised on products MUST resolve through list_creative_formats
    # @source repo=adcp ref=v3.1.1 path=static/compliance/source/protocols/media-buy/index.yaml phase=product_discovery step=list_formats_integrity

  @T-UC-005-storyboard-format-id-third-party-agent-out-of-scope @storyboard-v3.1 @v3-1 @format-id-roundtrip @third-party-agent
  Scenario: Format ID with agent_url pointing at a third-party creative agent is reported as observation, not failure
    Given a product advertises a format_id whose agent_url points at a third-party creative agent
    And the seller has no local copy of that format in its own catalog
    When the Buyer Agent sends list_creative_formats with that third-party format_id
    Then the response is compliant with the list_creative_formats spec
    And the seller should NOT fabricate a local format entry to satisfy the third-party reference
    And the verification result should be reported as an observation rather than a graded failure
    # media-buy/index.yaml list_formats_integrity: when products[].format_ids[].agent_url
    # points at a creative agent different from this seller's agent, the seller cannot
    # verify it without calling that agent. The runner reports such references as
    # observations rather than grading failures (scope.equals=$agent_url with
    # on_out_of_scope=warn). The seller MUST NOT fabricate a local format entry to
    # cover a third-party reference.
    # list_formats_integrity: third-party format_ids are unverifiable locally and out of scope for graded failure
    # @source repo=adcp ref=v3.1.1 path=static/compliance/source/protocols/media-buy/index.yaml phase=creative_sync step=list_formats

  @T-UC-005-storyboard-baseline-format-id-object-shape @storyboard-v3.1 @v3-1 @format-id-shape @baseline-conformance
  Scenario: Baseline list_creative_formats response carries format_id objects with agent_url and id
    Given the Buyer Agent calls list_creative_formats without filters
    When the response returns a non-empty formats array
    Then the response is compliant with the list_creative_formats spec
    And every entry's format_id should be an object carrying both agent_url and id
    And no entry's format_id should be a bare string
    # creative/index.yaml discover_formats phase: every format_id returned must be an
    # object with both agent_url (the creative agent's URL) and id (the format's
    # unique identifier within that agent). Sellers returning bare-string format IDs
    # break the v3.1 federation contract.
    # discover_formats: format_id object shape is the federation contract
    # The strict "every entry / never a bare string" form is mandated by the schema
    # (core/format-id.json: required [agent_url, id]); the storyboard grades field_present on formats[0].
    # @source repo=adcp ref=v3.1.1 path=static/schemas/source/core/format-id.json
