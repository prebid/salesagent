# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)

Feature: BR-UC-005 Discover Creative Formats
  As a Buyer (Human or AI Agent)
  I want to discover what creative formats a seller accepts
  So that I can prepare compliant creative assets before creating media buys

  # Postconditions verified:
  #   POST-S1: Buyer knows the formats this seller's deprecated 3.x compatibility list serves
  #
  # Sales-agent list_creative_formats is DEPRECATED at the pin (media-buy/
  # list-creative-formats-request.json "deprecated": true, x-deprecated-in 3.2.0). The pin
  # lets an existing 3.x agent keep serving it, but the list is not this seller's catalogue
  # and not a deliverability promise: "Sales agents MUST NOT use an agent-global format list
  # as a purchasability promise. Product format_options[] is the closed sellable set"
  # (creative/task-reference/list_creative_formats.mdx). What a buyer can deliver on a
  # product is graded by BR-UC-GET-PRODUCTS-format-options and BR-UC-FORMAT-SATISFACTION.
  # @source repo=adcp ref=v3.2.7 path=docs/creative/task-reference/list_creative_formats.mdx
  # @source repo=adcp ref=v3.2.7 path=docs/media-buy/specification.mdx section="Canonical creative-format discovery"
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
    # POST-S1: The compatibility list returned, whole
    # POST-S2: Asset requirements included per format
    # Two Thens were dropped here, both grading `type`, which adcp 3.12 removed from
    # Format: "a name and type category" and "sorted by format type then name". Sorting is
    # graded on its own by @T-UC-005-inv-031-2-holds, so it is no longer hostage to this
    # scenario's remaining gap.

  @T-UC-005-local-product-projection @hand-edited @main-flow @post-s1
  Scenario: the compatibility list names only the formats the seller's products declare
    Given the registry has formats:
      | name             |
      | declared-banner  |
      | undeclared-video |
    But the seller's products declare only format "declared-banner" and one format no creative agent publishes
    When the Buyer Agent requests all formats with no filters
    Then the response is compliant with the list_creative_formats spec
    And the formats on the wire should be exactly "declared-banner"
    # HAND-EDITED: authored locally, not rendered from adcp-req.
    #
    # The list is a projection of the seller's products, never a catalogue of its own.
    # media-buy/specification.mdx § Canonical creative-format discovery: "Compatibility
    # list_creative_formats responses MAY be projected from canonical declarations for older
    # 3.x peers, but MUST NOT be independently authored"; creative/sales-agent-creative-
    # capabilities.mdx § Compatibility: "The projection must be derived; it must not become a
    # second authored catalog." So the creative agent publishing "undeclared-video" does not
    # put it on the list, and the declared format no agent publishes has no definition to
    # project, so it is omitted rather than invented. Each half fails a different seller: one
    # that lists its agents' catalogues, and one that mints an entry from a bare reference.
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/specification.mdx section="Canonical creative-format discovery"
    # @source repo=adcp ref=v3.2.7 path=docs/creative/sales-agent-creative-capabilities.mdx section="Compatibility"
    # @source repo=adcp ref=v3.2.7 path=docs/creative/task-reference/list_creative_formats.mdx section="Compatibility behavior"

  @T-UC-005-main-filtered @UC-005-MAIN-MCP-05 @main-flow @post-s3
  Scenario: Discover filtered format catalog
    # Filters on asset_types, which the media-buy request declares. `type` is NOT a
    # parameter of this seller's request: it was removed from
    # media-buy/list-creative-formats-request.json at the 3.0 major (2.5 had it) and
    # survives only on the "(Creative Agent)" variant under creative/, which this
    # seller does not serve.
    #
    # The outcome is asserted on `name`, not on the format's category. `type` is not a
    # field of core/format.json either, so a Format carries one only as pydantic
    # model_extra that the test factory injected -- measured: FormatFactory.build(
    # type="display") puts it in model_extra, and Format.model_fields has no "type".
    # An assertion on it grades a value no buyer ever receives. `name` is REQUIRED on
    # the pinned Format, and `only "..." should be returned` also pins the count at 1,
    # so an empty result cannot satisfy it.
    Given the registry has format "display-banner" of type "display" with asset type "image"
    And the registry has format "pre-roll" of type "video" with asset type "video"
    When the Buyer Agent requests formats with asset_types filter ["image"]
    Then the response is compliant with the list_creative_formats spec
    And only "display-banner" should be returned
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

  # DELETED: @T-UC-005-main-pricing ("Per-format pricing options surfaced pass-through by
  # the media-buy aggregator"). It asserted that a format offering vendor pricing carries
  # its pricing_options in this response, and the pin gives that no basis. core/format.json
  # on pricing_options: "DEPRECATED in 3.1. Removed at 4.0." ... "Legacy behavior, retained
  # for 3.1-3.x backward compatibility: pricing options for this format ... present when
  # the request included include_pricing=true and account." ... "New code SHOULD NOT emit
  # this field." The media-buy request this seller serves declares no include_pricing, so
  # the one condition under which the field is present can never hold here. The surviving
  # half of POST-S5 -- no pricing_options when the registry supplies none -- is
  # @T-UC-005-main-no-pricing below.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format.json pointer=/properties/pricing_options

  @T-UC-005-main-no-pricing @main-flow @post-s5
  Scenario: Formats without vendor pricing carry no pricing_options
    Given the creative agent registry has formats without vendor pricing options
    When the Buyer Agent requests the format catalog
    Then the response is compliant with the list_creative_formats spec
    And no format should include pricing_options
    # POST-S5: the seller adds no pricing_options of its own. core/format.json makes the
    # field legacy and conditional ("present when the request included include_pricing=true
    # and account", "New code SHOULD NOT emit this field"), and the media-buy request
    # carries no include_pricing, so a format the registry supplies without pricing reaches
    # the buyer without it.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format.json pointer=/properties/pricing_options

  # BR-RULE-031 INV-1 is AND semantics across multiple filters. It was stated with
  # `type` + `asset_types`; `type` is not a parameter of this seller's request (removed
  # from the media-buy variant at 3.0, present only on the "(Creative Agent)" variant),
  # so the pair graded nothing. Restated over two filters the request DOES declare --
  # asset_types and name_search -- which production applies as sequential narrowings of
  # one list (creative_formats.py:365 and :360) and therefore ANDs. The Givens and the
  # Thens are unchanged: the same registry and the same expected survivor now follow
  # from filters that exist.
  @T-UC-005-inv-031-1-holds @UC-005-MAIN-MCP-16 @invariant @BR-RULE-031
  Scenario: BR-RULE-031 INV-1 holds - Multiple filters combine as AND
    Given the registry has format "display-banner" of type "display" with asset type "image"
    And the registry has format "video-banner" of type "display" with asset type "video"
    And the registry has format "pre-roll" of type "video" with asset type "video"
    When the Buyer Agent requests formats with asset_types ["video"] and name_search "banner"
    Then the response is compliant with the list_creative_formats spec
    And only "video-banner" should be returned
    # BR-RULE-031 INV-1: both filters must match (asset=video AND name contains "banner").
    # asset_types alone would also return "pre-roll"; name_search alone would also
    # return "display-banner". Only the AND leaves "video-banner".

  @T-UC-005-inv-031-1-violated @UC-005-MAIN-MCP-16 @invariant @BR-RULE-031
  Scenario: BR-RULE-031 INV-1 violated - AND combination excludes partial matches
    Given the registry has format "pre-roll" of type "video" with asset type "video"
    When the Buyer Agent requests formats with asset_types ["video"] and name_search "banner"
    Then the response is compliant with the list_creative_formats spec
    And no formats should be returned
    # BR-RULE-031 INV-1: "pre-roll" matches asset_types but not name_search, so the AND
    # excludes it. A seller that ORed the filters would return it.

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
    And the registry has format "foreign-leaderboard" with format_id id "fmt-001" declared by agent "https://creative.other-agent.example"
    When the Buyer Agent requests formats with format_ids filter ["fmt-001"]
    Then the response is compliant with the list_creative_formats spec
    And only "leaderboard" should be returned
    # BR-RULE-049 INV-2: format_ids matches on the (agent_url, id) federation pair
    # (core/format-id.json @ 3.2.7 identifies a format "by its declaring agent and local
    # slug"; list_formats step match_keys [agent_url, id]). "foreign-leaderboard" shares
    # the requested id under another agent, so a seller matching on id alone returns two
    # formats and fails "only".

  @T-UC-005-inv-049-2-respelled-agent @UC-005-MAIN-MCP-06 @invariant @BR-RULE-049
  Scenario Outline: BR-RULE-049 INV-2 holds - a format_ids filter naming the declaring agent in another spelling matches - <spelling>
    Given the registry has format "spelled-banner" with format_id id "fmt-201" declared by agent "<declared>"
    And the registry has format "sibling-banner" with format_id id "fmt-201" declared by agent "<other>"
    When the Buyer Agent requests formats filtered by format_id "fmt-201" declared by agent "<requested>"
    Then the response is compliant with the list_creative_formats spec
    And only "spelled-banner" should be returned

    Examples:
      | spelling                         | declared                          | other                               | requested                              |
      | host case and default port       | https://formats.agent.example/api | https://formats.agent.example/other | https://FORMATS.Agent.Example:443/api  |
      | trailing root-label dot          | https://formats.agent.example/api | https://formats.agent.example/other | https://formats.agent.example./api     |
      | removable dot segment            | https://formats.agent.example/api | https://formats.agent.example/other | https://formats.agent.example/x/../api |
      | percent-encoded unreserved octet | https://formats.agent.example/api | https://formats.agent.example/other | https://formats.agent.example/%61pi    |
      | root-label dot on an IDN host    | https://xn--bcher-kva.example/api | https://xn--bcher-kva.example/other | https://xn--bcher-kva.example./api     |

    # core/format-id.json (3.2.7), on agent_url: "Callers comparing two format-id values MUST
    # canonicalize agent_url per the AdCP URL canonicalization rules before treating two
    # formats as the same", and docs/reference/url-canonicalization.mdx: "Implementations MUST
    # apply every step". Each row respells the declaring agent by one step -- host case and
    # the default port (2, 4), the root-label dot (2), a dot segment (5), a percent-encoded
    # unreserved octet (6), and the root-label dot on an IDN host's A-label, which step 2 says
    # is removed "identically to an input U-label and its equivalent A-label". The sibling is
    # the same id on the same host under another path, a different agent ("Canonicalization
    # removes only dot segments, never real path segments"), so a filter that dropped the
    # agent returns two formats and fails "only".
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format-id.json pointer=/properties/agent_url/description
    # @source repo=adcp ref=v3.2.7 path=docs/reference/url-canonicalization.mdx

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

  @T-UC-005-inv-049-3-group-violated @UC-005-MAIN-MCP-07 @invariant @BR-RULE-049 @hand-edited
  Scenario: BR-RULE-049 INV-3 edge - A group whose assets match no requested type excludes the format
    Given the registry has format "text-carousel" with a repeatable asset group containing "text" and "html"
    And the registry has format "video-ad" with assets of type "video"
    When the Buyer Agent requests formats with asset_types filter ["video"]
    Then the response is compliant with the list_creative_formats spec
    And only "video-ad" should be returned
    # HAND-EDITED: authored locally, not rendered from adcp-req (mirror upstream).
    # The asset_types filter keeps "formats that include these asset types". The assets
    # nested in a repeatable group are assets the format includes, so they are read
    # (@T-UC-005-inv-049-3-group); reading them must not admit a format whose group holds
    # none of the requested types. "video-ad" is the control: the same request returns it,
    # so the exclusion is the filter's and not an empty listing. The outcome is the same
    # whether or not group members count, which the pin leaves unstated (adcp#8630).
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json pointer=/properties/asset_types
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format.json pointer=/properties/assets

  @T-UC-005-inv-049-3-mixed @UC-005-MAIN-MCP-07 @invariant @BR-RULE-049 @hand-edited
  Scenario Outline: BR-RULE-049 INV-3 edge - Individual and group assets of one format both count - asset_types ["<requested>"]
    Given the registry has format "mixed-media" with an individual "video" asset and a repeatable asset group containing "image" and "text"
    And the registry has format "html-banner" with assets of type "html"
    When the Buyer Agent requests formats with asset_types filter ["<requested>"]
    Then the response is compliant with the list_creative_formats spec
    And only "<returned>" should be returned

    Examples:
      | requested | returned    |
      | image     | mixed-media |
      | video     | mixed-media |
      | html      | html-banner |
    # HAND-EDITED: authored locally, not rendered from adcp-req (mirror upstream).
    # One format carrying a video slot beside a repeatable group of image and text includes
    # all three types, so it is kept by a request for a type held only in the group (image)
    # and by one for the type held only in the individual slot (video), and dropped by a
    # request for a type it holds nowhere (html), which keeps "html-banner" and only it.
    # Every row requests ONE type, so none of them depends on whether several requested
    # types combine as any-of or all-of. The image row reads a type held only inside the
    # group as one the format "includes"; the pin does not say so in words, which
    # adcp#8630 asks it to, and core/format.json lists the group among the "array of all
    # assets supported for this format". That is also the reading
    # @T-UC-005-inv-049-3-group grades.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json pointer=/properties/asset_types
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format.json pointer=/properties/assets
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
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json

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
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format.json
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
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json

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
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json

  @T-UC-005-inv-049-11-violated @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-11 violated - Unsatisfiable persistence mode excludes format
    Given the registry has format "flex-only" with disclosure_capabilities:
    | position | persistence |
    | footer   | flexible    |
    And the registry has format "continuous-ad" with disclosure_capabilities:
    | position  | persistence |
    | prominent | continuous  |
    When the Buyer Agent requests formats with disclosure_persistence filter ["continuous"]
    Then the response is compliant with the list_creative_formats spec
    And "flex-only" should not be returned
    And "continuous-ad" should be returned
    # BR-RULE-049 INV-11: no position supports "continuous" -> excluded
    # "continuous-ad" is a POSITIVE CONTROL, for the reason @T-UC-005-inv-049-8-violated
    # carries one: an exclusion-only Then passes on any empty result, so the filter must
    # drop the unsatisfying format AND keep the satisfying one.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json pointer=/properties/disclosure_persistence

  @T-UC-005-inv-049-11-nofield @invariant @BR-RULE-049
  Scenario: BR-RULE-049 INV-11 edge - Format without disclosure_capabilities excluded
    Given the registry has format "legacy-banner" with no disclosure_capabilities field
    And the registry has format "structured-banner" with disclosure_capabilities:
    | position | persistence |
    | footer   | initial     |
    When the Buyer Agent requests formats with disclosure_persistence filter ["initial"]
    Then the response is compliant with the list_creative_formats spec
    And "legacy-banner" should not be returned
    And "structured-banner" should be returned
    # BR-RULE-049 INV-11: formats without disclosure_capabilities cannot declare persistence -> excluded
    # "structured-banner" is the positive control: the request schema matches persistence
    # "by at least one position in disclosure_capabilities", so a format declaring the mode
    # there survives the same request that drops the undeclared one.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json pointer=/properties/disclosure_persistence

  @T-UC-005-empty-catalog @UC-005-MAIN-MCP-01 @edge-case
  Scenario: Empty catalog when no agents have formats
    Given no creative agents have any registered formats
    When the Buyer Agent requests the format catalog
    Then the response is compliant with the list_creative_formats spec
    And no formats should be returned
    And no error should be returned
    And the response should NOT have an "errors" field
    # Edge case: PRE-B1 boundary — no formats available

  # A creative agent's catalog is a peer's document, parsed by this seller. One entry the
  # pinned schema cannot validate costs that entry and is reported; it does not cost the
  # buyer the agent's conforming formats, and it is never answered as an empty catalog.
  # An enum member the pin does not model is not a defect: the pin makes enums additive
  # and tells a reader to tolerate an unknown value, and the seller passes it on as the
  # agent published it. accepts_parameters must survive verbatim, because an empty list
  # reads as "a concrete format with fixed parameters".
  # @source repo=adcp ref=v3.2.7 path=docs/reference/versioning.mdx
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format.json pointer=/properties/accepts_parameters
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-response.json pointer=/properties/errors
  @T-UC-005-peer-catalog-partial-discard @UC-005-MAIN-MCP-01 @edge-case
  Scenario: A creative agent's catalog entry that does not validate costs that entry, not the catalog
    Given a creative agent whose catalog includes one entry the pinned schema cannot validate
    When the Buyer Agent requests the format catalog
    Then only the creative agent's format "acme_peer_banner" should be returned
    And the creative agent's format should carry accepts_parameters "future_parameter"
    And the creative agent's format should carry reported_metrics "impressions, future_metric"
    And the discarded entries should be reported as "AGENT_FORMATS_DISCARDED" on "formats" with recovery "terminal"

  # The two below share a When and differ in one Given, so "the agent genuinely has no
  # formats" and "the seller discarded every format the agent published" cannot converge
  # on the same answer again: the first carries no errors, the second reports the discard.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-response.json pointer=/properties/errors
  @T-UC-005-peer-catalog-empty @UC-005-MAIN-MCP-01 @edge-case
  Scenario: A creative agent with a genuinely empty catalog contributes no formats and no error
    Given a creative agent whose catalog has no entries
    When the Buyer Agent requests the format catalog
    Then no formats from the creative agent should be returned
    And the response should NOT have an "errors" field

  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-response.json pointer=/properties/errors
  @T-UC-005-peer-catalog-all-discarded @UC-005-MAIN-MCP-01 @edge-case
  Scenario: A creative agent whose every catalog entry was discarded is reported, not answered as empty
    Given a creative agent whose catalog has only entries the pinned schema cannot validate
    When the Buyer Agent requests the format catalog
    Then no formats from the creative agent should be returned
    And the discarded entries should be reported as "AGENT_FORMATS_DISCARDED" on "formats" with recovery "terminal"

  # A configured creative agent that does not answer costs the buyer that agent's formats
  # and nothing else: the listing is still a successful response, the format the standard
  # creative agent publishes for the seller's product is listed, and the failure rides the
  # response's errors[] as one advisory naming the section it degrades. AGENT_UNREACHABLE
  # is a platform-specific code, which the pin permits ("senders MAY emit codes outside
  # that set"); a buyer decodes it by its recovery, transient, since the agent may answer
  # a retry. Storyboard: ungraded.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/error.json pointer=/properties/code
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-response.json pointer=/properties/errors
  @T-UC-005-peer-catalog-unreachable @UC-005-MAIN-MCP-01 @edge-case @hand-edited
  Scenario: A configured creative agent that does not answer is reported, and the other agents' formats are still listed
    Given the seller's products declare the standard creative agent's format "video_vast"
    And a creative agent the seller configured does not answer
    When the Buyer Agent requests the format catalog
    Then the response is compliant with the list_creative_formats spec
    And the formats on the wire should be exactly "VAST Video"
    And the unreachable creative agent should be reported as "AGENT_UNREACHABLE" on "formats" with recovery "transient"

  # Which creative agents a seller federates is the seller's choice: the pinned response
  # requires only formats, and creative_agents is an optional referral list. A seller that
  # runs its own creative agent and turns the standard one off lists that agent's formats
  # and nothing else, and refers buyers to that agent alone. Storyboard: ungraded.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-response.json pointer=/properties/creative_agents
  @T-UC-005-own-agents-only @UC-005-MAIN-MCP-13 @edge-case @post-s4
  Scenario: A seller that does not federate the standard creative agent lists only its own agents
    Given the seller does not federate the standard creative agent and its own creative agent serves format "acme-own-banner"
    When the Buyer Agent requests the format catalog
    Then only "acme-own-banner" should be returned
    And the creative_agents referrals should name only the seller's own creative agent

  @T-UC-005-dim-boundary @UC-005-MAIN-MCP-08 @boundary @BR-RULE-049
  Scenario: Dimension boundary - inclusive range at threshold
    Given the registry has format "exact-fit" with render width 728 and height 90
    When the Buyer Agent requests formats with min_width 728 and max_width 728
    Then the response is compliant with the list_creative_formats spec
    And "exact-fit" should be returned
    # BR-RULE-049 INV-4: dimension range is inclusive (width == min_width == max_width)

  @T-UC-005-ext-a @extension @ext-a @error @post-f1 @post-f2 @post-f3
  Scenario: No tenant context
    Given no tenant can be resolved from the request context
    When the Buyer Agent requests the format catalog
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "TENANT_UNDEFINED"
    And the error recovery should be "terminal"
    And the error should include a "suggestion" field
    # POST-F1: Buyer knows the operation failed
    # POST-F2: The CODE says the seller could not be determined. This scenario used to
    #   read "the Buyer has no authentication credentials / no hostname-based tenant
    #   resolution is possible" and expect AUTH_MISSING. Neither Given removed the
    #   tenant: the first presents no token but still addresses the env's seller, and the
    #   second only checks that pairing. list_creative_formats is a public task, so that
    #   request was served -- the scenario's own title was never realized. The Given now
    #   sends no headers at all, so no seller is named, which is the state the title
    #   describes.
    #
    #   AUTH_MISSING would be wrong for that state even on a protected task:
    #   enums/error-code.json says "No credentials were presented", and a deployment
    #   that cannot tell which seller was addressed applies no seller's credential policy.
    #   The pin has no tenant concept, so what a deployment answers for a request naming
    #   no seller is this seller's own contract; three sibling scenarios on the same Given
    #   already grade it: BR-UC-010 @T-UC-010-ext-a, BR-UC-013 @T-UC-013-051 and BR-UC-018
    #   @T-UC-018-ext-b, all TENANT_UNDEFINED with terminal recovery.
    # POST-F3: Suggestion directs the buyer to check the agent URL it dialled.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/AUTH_MISSING
    # --- ext-b: Invalid Request Parameters ---

  @T-UC-005-ext-b @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Invalid request parameters
    # Stated as an invalid `type` value, which this seller's request does not declare,
    # so no rejection was reachable. asset_types is declared and enum-typed
    # (asset-content-type.json), so an unknown member is a real INVALID_REQUEST naming
    # a real parameter.
    Given a tenant is resolvable from the request context
    When the Buyer Agent requests formats with asset_types filter ["not_a_category"]
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "asset_types"
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
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json

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
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json

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
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json

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
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/INVALID_REQUEST
  # (pinned locally at tests/fixtures/adcp_schemas_pinned/enums/error-code.json:102)
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json pointer=/properties/asset_types/minItems
  #   pointer=/properties/asset_types/minItems  (minItems=1 also on format_ids,
  #   disclosure_positions, output_format_ids, input_format_ids)
  # This matches the codes the ext-b error scenarios above already assert for the same
  # conditions, so one feature no longer names two codes for one rejection.

  # DELETED: @T-UC-005-partition-type-filter ("Format type filter partition").
  # `type` is not a parameter of this seller's request, so every row's When dispatched
  # an UNFILTERED request and the Then asserted that no filtering had occurred -- five
  # rows reading green for asserting the absence of a behaviour nobody implements, and
  # a sixth xfailed for a rejection that cannot happen. The pin's declared filters are
  # graded by @T-UC-005-partition-asset-types and @T-UC-005-partition-format-ids below;
  # restating this outline over either would duplicate one of them.
  #
  # ON RECOMPILE: upstream still authors both outlines, so `compile_bdd --merge` will
  # bring them back. That is LOUD, not silent: their step definitions are deleted too,
  # and neither tag is in tests/bdd/dormant_scenarios.txt, so each resurrected row
  # fails as "NEW DORMANT SCENARIO". Delete them again -- do not add a baseline line,
  # and do not re-add the step. (The note at scripts/compile_bdd.py:1180 says such a
  # scenario "goes quiet rather than red"; that predates the dormancy baseline and is
  # no longer true for a tag the baseline does not list.)
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
      | invalid_format_id_width_only        | INVALID_REQUEST  |

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
      | invalid_format_id_width_only        | INVALID_REQUEST  |
  # invalid_format_id_width_only, both outlines: a format-id with width and no height. The
  # schema's dependencies make width require height, so the request is schema-invalid.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format-id.json pointer=/dependencies/width

  # DELETED: @T-UC-005-boundary-type-filter ("Format type filter boundary"), for the
  # same reason as the partition outline above -- five valid rows dispatched unfiltered
  # and asserted that nothing was filtered. Boundary coverage for the pin's declared
  # filters is @T-UC-005-boundary-asset-types and @T-UC-005-boundary-format-ids.
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

  @T-UC-005-boundary-dimension-one-past @UC-005-MAIN-MCP-08 @boundary @dimension_filter @hand-edited
  Scenario Outline: Dimension filter boundary - a render one pixel past <filter> 300 is excluded
    Given the registry has format "at-bound" with render width 300 and height 250
    And the registry has format "one-pixel-past" with render width <past_width> and height 250
    When the Buyer Agent requests formats with <filter> 300
    Then the response is compliant with the list_creative_formats spec
    And only "at-bound" should be returned

    Examples:
      | filter    | past_width |
      | min_width | 299        |
      | max_width | 301        |
    # HAND-EDITED: authored locally, not rendered from adcp-req (mirror upstream).
    # Both width bounds are inclusive: min_width returns formats "where ANY render has
    # width >= this value", max_width those "where ANY render has width <= this value".
    # So the 300px render is kept at the bound and the render one pixel outside it is
    # dropped, in both directions; each row grades the inclusive and the exclusive side of
    # the same comparison at once. @T-UC-005-dim-boundary grades the bound alone.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json pointer=/properties/min_width
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json pointer=/properties/max_width

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

  @T-UC-005-boundary-name-search-empty @UC-005-MAIN-MCP-11 @boundary @name_search_filter @hand-edited
  Scenario: Name search filter boundary - an empty string filters nothing
    Given a seller with formats named "Standard Banner", "Video Interstitial", "Native Card"
    When the Buyer Agent requests creative formats at name_search boundary "empty string (filters nothing)"
    Then the response is compliant with the list_creative_formats spec
    And the response should include all registered formats
    # HAND-EDITED: authored locally, not rendered from adcp-req (mirror upstream).
    # name_search is a "case-insensitive partial match" and the request schema puts no
    # minLength on it, so "" is a valid request whose term is contained in every name: it
    # keeps every format, and is neither refused nor answered with an empty list.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json pointer=/properties/name_search

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
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "min_width"
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real. sandbox.mdx: the seller "MUST
    # validate inputs the same way as production", and "Sandbox errors are real validation
    # errors" -- so the refusal is the one a production request gets for the same filter.
    # The invalid filter is a non-integer min_width: the request schema declares it
    # {"type": "integer"} with no minimum, so a negative width (what this used to send) is
    # a valid request. A type violation is a schema constraint, which error-code.json codes
    # INVALID_REQUEST ("violates schema constraints"); VALIDATION_ERROR, which the two
    # replaced Thens asserted, is for rules "BEYOND schema validation".
    # POST-F3: suggestion field present
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/advanced-topics/sandbox.mdx
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json pointer=/properties/min_width
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/INVALID_REQUEST

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
    When the Buyer Agent requests formats with asset_types filter ["<asset_type>"]
    Then the response is compliant with the list_creative_formats spec
    And the response status should be "completed"
    And each returned format should contain at least one asset of type "<asset_type>"
    # The asset_types FILTER's items are enums/asset-content-type.json, and one row per
    # member of that enum AS PINNED -- 16 of them, zip and published_post included (the
    # request schema itself says "For published-post reference formats, search for
    # 'published_post'"). The count is the pin's, not this comment's: a row missing here is
    # a member the scenario does not grade.
    # vast_tracker / daast_tracker are manifest-payload discriminators in
    # asset-types/index.json — NOT valid filter inputs.
    # The Given seeds exactly one format carrying the row's asset type, served by the
    # seller's own peer creative agent on every transport (CreativeFormatsEnv
    # .set_registry_formats), so every member is realized over e2e_rest too -- not only the
    # 8 the reference catalog happens to carry.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json pointer=/properties/asset_types
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/asset-content-type.json

    Examples: every asset-content-type filter enum value
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
      | zip            |
      | published_post |

  @T-UC-005-v31-asset-registry-no-variant-siblings @main-flow @v3-1 @asset-registry @validation
  Scenario: asset_types filter rejects nested-variant discriminators (no sibling registry entries)
    Given the Buyer is authenticated
    And the request targets a production account
    When the Buyer Agent sends a list_creative_formats request with asset_types ["vast_url"]
    Then the error is compliant with the AdCP error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field should contain "asset_types"
    # v3.1: registry forbids sibling entries like vast_url/vast_inline; use vast with inner delivery_type
    # asset_types items are enums/asset-content-type.json, which carries "vast" and no
    # "vast_url", so the request violates a schema enum: INVALID_REQUEST ("violates schema
    # constraints"), naming the parameter. The replaced Thens asserted VALIDATION_ERROR,
    # which error-code.json reserves for rules "BEYOND schema validation", and a message
    # about registry discriminators the code table words for itself.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-request.json pointer=/properties/asset_types
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/asset-content-type.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/INVALID_REQUEST

  @T-UC-005-v31-payload-vs-requirements-separation @main-flow @v3-1 @asset-registry
  Scenario: Format response carries constraints under requirements, not on the asset schema
    Given the seller catalog has a format with a required image asset capped at 2 MB
    When the Buyer Agent requests creative formats for that format
    Then the response is compliant with the list_creative_formats spec
    And the format's "assets" entry should expose "asset_type" with value "image"
    And the same entry should expose "required" as true
    And the same entry should expose "max_file_size_kb" with value 2048 under "requirements"
    And the same entry should not expose "max_file_size_kb" outside "requirements"
    # v3.1: registry mandates payload-vs-requirements separation
    # The Given seeds the capped format through the seller's own peer creative agent, so it
    # is realized on every transport, e2e_rest included; the reference catalog declares no
    # image cap the pin names, which is why it is seeded rather than drawn from there.
    # Corrected against the pin. The cap is max_file_size_kb, the member
    # core/requirements/image-asset-requirements.json declares (there is no max_file_size,
    # and no MB unit), so the 2 MB cap reads as the 2048 KB the buyer sees. And `required`
    # belongs ON the asset entry: core/format.json's baseIndividualAsset declares it there
    # ("Whether this asset is required (true) or optional (false)"), so the old Then that
    # the entry must NOT carry "required" contradicted the pin; the separation the pin
    # draws is constraints under `requirements`, never beside asset_type.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format.json pointer=/$defs/baseIndividualAsset/properties/required
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/requirements/image-asset-requirements.json pointer=/properties/max_file_size_kb

  @T-UC-005-storyboard-format-id-third-party-agent-out-of-scope @v3-1 @format-id-roundtrip @third-party-agent
  Scenario: A format_id filter naming a third-party creative agent's format is not answered with the seller's own same-id format
    Given a product advertises a format_id whose agent_url points at a third-party creative agent
    And the seller has no local copy of that format in its own catalog
    When the Buyer Agent sends list_creative_formats with that third-party format_id
    Then the response is compliant with the list_creative_formats spec
    And the seller should NOT fabricate a local format entry to satisfy the third-party reference
    # A format_id names a format by BOTH halves: core/format-id.json says "Callers comparing
    # two format-id values MUST canonicalize agent_url ... before treating two formats as
    # the same", so a seller holding a same-id format under its OWN agent_url does not hold
    # the third-party one, and answering the filter with it attributes a format to an agent
    # that does not host it. list_creative_formats.mdx makes the compatibility side
    # explicit: a compatibility implementation SHOULD "Never return publisher-declared
    # formats as if every format were deliverable by the responding sales agent".
    # The pin's list_creative_formats is deprecated -- agents MAY serve it -- and no
    # storyboard step at the pin grades it; the creative_sync/list_formats step this
    # scenario used to cite does not exist at the pin, and the runner's "report as an
    # observation" wording it carried is grading behaviour, not a seller obligation, so
    # that Then is gone.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format-id.json pointer=/properties/agent_url
    # @source repo=adcp ref=v3.2.7 path=docs/creative/task-reference/list_creative_formats.mdx

  @T-UC-005-storyboard-baseline-format-id-object-shape @storyboard-v3.1 @v3-1 @format-id-shape @baseline-conformance
  Scenario: Baseline list_creative_formats response carries format_id objects with agent_url and id
    Given the creative agent registry has formats across multiple categories
    And the Buyer Agent calls list_creative_formats without filters
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
    # LOCAL CORRECTION: the first Given is added. The list projects the seller's product
    # declarations (@T-UC-005-local-product-projection), so a seller with no product lists
    # nothing and the non-empty precondition had no state to stand on.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format-id.json
