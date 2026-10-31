# Manual feature: whether a buyer's format selector satisfies what the product declares.
# Sibling of BR-UC-GET-PRODUCTS-format-options.feature — that one grades that a canonical
# declaration REACHES the buyer; this one grades that the seller then HOLDS the buyer to it.

Feature: A format selector must satisfy the canonical declaration the product published
  As a Buyer Agent
  I want a package whose format selector under-specifies the product's declaration refused
  So that I am told before the buy exists, rather than sold inventory no creative can fill

  # THE AUTHORITY is docs/creative/formats.mdx § Matching and satisfaction (at the pin),
  # which states the relation as five rules:
  #
  #   - Exact dimensions and durations must match.
  #   - A requested range must be contained within the accepted range; overlap is
  #     insufficient.
  #   - Required slots must be present with compatible asset types.
  #   - An exact publisher claim must match both publisher_domain and format_option_id.
  #   - Equal format_kind values alone do not prove compatibility.
  #
  # WHICH CONSTRAINTS A SELECTOR MUST STATE is named, not left to the shape of the
  # constraint. docs/creative/canonical-formats.mdx § Format matching vs product
  # satisfaction: "When the product declares a fixed width, height, duration_ms_exact, or
  # duration_ms_range, the requested creative or package selector MUST declare and match
  # that constraint." Any other constraint a selector is silent about is INHERITED -- the
  # same document's normative "Narrows" definition: "when v2.params omits a parameter that
  # v1 specified, v2 inherits v1's value (no narrowing constraint added)" -- and a
  # constraint it does state must still be met. The pin's storyboard grades exactly that
  # split: create_media_buy_with_equivalent_direct_legacy_emission sends a direct route of
  # {width, height} against a declaration that also fixes max_file_size_kb and
  # image_formats, and expects it ACCEPTED with every route graded independently. The
  # direct-selector step that restates those two (create_media_buy_with_direct_canonical_
  # selector) restates them because an earlier runner refused their omission; the changesets
  # that added them say so, and the runner has since been corrected to inherit.
  #
  # and core/product-format-declaration.json's seller_preference, which is normative that
  # format_options[] "IS the closed set of accepted formats; anything outside the list is
  # rejected at create_media_buy regardless of preference". The refusal is
  # UNSUPPORTED_FEATURE: the pinned error-code enum classifies it `correctable` ("check
  # get_adcp_capabilities and remove unsupported fields"), which is exactly the buyer's
  # lever here — name a format this product publishes.
  #
  # WHAT WAS WRONG BEFORE. Four separate gates asked this question and all four read only
  # the DEPRECATED format_ids arm, so a product declaring format_options[] imposed no
  # format restriction anywhere: a buyer naming a bare `image` against a product that
  # fixes 300x250 was accepted. The two canonical selector arms the pin added
  # (format_option_refs, and format_kind + params) were parsed and then ignored. The four
  # gates did not even agree on their shared blind spot — the create gate SKIPPED a
  # creative carrying no legacy assertion while the update and assignment gates REFUSED
  # one, so one buyer input had two answers depending on which tool received it.
  #
  # NEARLY EVERY SCENARIO HERE SEEDS A DUAL-ARM PRODUCT — format_ids AND format_options,
  # which is the shape the conformance fixture canonical_formats_mrec_display uses. Not
  # incidental: a product carrying both arms leaves every selector route reachable, so a
  # refusal is attributable to the satisfaction rule the scenario names rather than to an
  # arm the product never declared.
  #
  # THE ONE EXCEPTION is the purchasability scenario at the end, which seeds a
  # CANONICAL-ONLY product. That was deferred while stage 4 (work item C3) had not yet
  # resolved a canonical-only product's legacy selector route — it now has, so the
  # deferral is spent, and the scenario it was blocking is the one stage-1 guarantee
  # nothing graded.
  #
  # @source repo=adcp ref=v3.2.7 path=docs/creative/formats.mdx
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product-format-declaration.json
  # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/canonical_formats.yaml
  # @source repo=adcp ref=v3.2.7 path=docs/creative/canonical-formats.mdx
  # @source repo=adcp ref=v3.2.7 path=docs/creative/template-format-ids.mdx
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format-id.json pointer=/properties/agent_url/description
  # @source repo=adcp ref=v3.2.7 path=docs/reference/url-canonicalization.mdx

  Background:
    Given a tenant is configured for media buy creation

  @T-UC-FORMAT-SATISFACTION-accepted @format_satisfaction @requires_db
  Scenario Outline: a selector that satisfies the declaration is accepted - <selector>
    Given the seller declares a canonical format option "<declaration>"
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy

    Examples:
      | declaration          | selector                       |
      | fixed_300x250        | exact_300x250                  |
      | fixed_300x250        | complete_300x250               |
      | text_limited_300x250 | exact_300x250                  |
      | duration_range       | exact_duration_in_range        |
      | exact_duration       | exact_duration_in_range        |
      | exact_over_range     | exact_duration_in_range        |
      | fixed_300x250        | product_option_ref             |
      | fixed_300x250        | legacy_display_300x250         |
      | duration_range       | contained_range                |
      | required_slots       | covering_slots                 |
      | publisher_300        | matching_publisher_claim       |
      | fixed_300x250        | option_plus_projectable_legacy |
      | fixed_300x250        | direct_plus_legacy             |
      | two_image_options    | exact_300x250                  |
      | two_image_options    | mismatched_728x90              |
      | fixed_300x250        | legacy_display_300x250_image        |
      | fixed_300x250        | option_plus_equivalent_legacy_image |
      | two_image_options    | both_option_refs                    |
      | any_audio            | legacy_audio_standard_30s           |
      | house_ref_300x250    | legacy_house_ref_only               |

    # legacy_house_ref_only is the ONE row a package's legacy reference resolves through the
    # seller's own v1_format_ref and nothing else. registries/v1-canonical-mapping.json (3.2.7)
    # "Resolution order", step 1: the "Authoritative v2->v1 link" -- a declaration's
    # v1_format_ref naming the id. The id is a house slug: no glob names it, it encodes no size
    # for the SDK's upgrade, and no creative agent serves it, so steps 2 to 4 have nothing to
    # read. A seller that skips step 1 finds no projection and no kind and refuses it
    # UNSUPPORTED_FEATURE (package-request.json: "a legacy format_id that cannot be normalized
    # through the canonical mapping path MUST be rejected"); every other legacy row here also
    # resolves through the registry, so none of them could tell.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/description

    # legacy_audio_standard_30s is the ONE row a package's legacy reference resolves through
    # the creative agent's own declaration and nothing else. registries/v1-canonical-mapping.json
    # (3.2.7) "Resolution order", step 2: "if the v1 format declaration carries an explicit
    # canonical field, use it"; canonical-formats.mdx § Format matching vs product satisfaction:
    # "Sellers and SDKs MUST normalize legacy named formats ... through the explicit canonical
    # annotation, v1_format_ref, or the canonical mapping registry". No glob names
    # audio_standard_30s and the any_audio declaration carries no v1_format_ref, so a package
    # route that skips step 2 refuses it UNSUPPORTED_FEATURE while sync_creatives accepts it.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/registries/v1-canonical-mapping.json pointer=/description

    # both_option_refs names BOTH of two_image_options' options on one format_option_refs
    # arm. That arm is one route selecting a set (package-request.json: "Each reference
    # matches one target product format_options[] entry"), so the two refs agree with each
    # other by construction; they are not two routes to be equivalence-checked.

    # THE TWO ROWS BEFORE IT GRADE NORMALIZATION RATHER THAN SPELLING. Every other legacy row here
    # sends display_300x250, which is literally the product's deprecated entry AND its
    # declaration's v1_format_ref, so a seller that exact-matched raw (agent_url, id) pairs
    # passed them unchanged. display_300x250_image is spelled by neither and reaches the
    # 300x250 declaration only through the registry's upgrade (both ids become display_image
    # 300x250). docs/creative/template-format-ids.mdx § Compatibility: "Normalize them to
    # canonical declarations immediately and do all matching on the canonical shape", and
    # core/product-format-declaration.json, "Format matching vs satisfaction (normative)":
    # "do not exact-match raw (agent_url, id) pairs once a format_id has been projected".
    # The second row is the storyboard's create_media_buy_with_equivalent_dual_emission. Both
    # were refused VALIDATION_ERROR by a second, raw-key gate that ran after satisfaction had
    # accepted them (salesagent-ol033.20.26).

    # `exact_300x250` against `fixed_300x250` states the fixed size and omits the size cap
    # and the file-format set the declaration also fixes; `complete_300x250` restates them.
    # Both are accepted, and their refusal mirrors below (`oversized_file_300x250`,
    # `unaccepted_image_format_300x250`) state one of those constraints and break it -- so a
    # seller that refused every omission reddens here and one that ignored every
    # non-dimension constraint reddens there. `text_limited_300x250` is the same pair over a
    # numeric limit whose name carries no bound prefix, which is the case that separates a
    # rule keyed on the pin's list from one keyed on the constraint's shape.
    #
    # THE LAST TWO ROWS grade a PACKAGE SELECTOR against two same-kind declarations, which
    # media-buy/package-request.json lets resolve by directional satisfaction. They do NOT
    # grade the MANIFEST rule (core/creative-asset.json format_option_ref: "Required when
    # the target product has multiple format_options entries sharing the same
    # format_kind"); no scenario grades that one yet. They are the same two selectors that appear
    # in the refusal outline below against `fixed_300x250` — which is what makes them
    # informative. A product publishing TWO image declarations, 300x250 and 728x90, accepts
    # both; a product publishing only the first accepts one and refuses the other. So a
    # seller that graded a bare-kind selector against the FIRST same-kind declaration
    # instead of the one the selector satisfies reddens `two_image_options |
    # mismatched_728x90` and nothing else — which is exactly what it did
    # (salesagent-ky23p). core/product-format-declaration.json is why a product may be
    # shaped that way: format_option_id is "REQUIRED when a product contains multiple
    # declarations with the same format_kind", so several per kind is the case the pin
    # names, not an exotic one.

    # These rows are what stops the refusal outline below from passing vacuously. Every
    # pair (declaration, selector) here differs from a row there by ONE thing — the
    # selector — against the SAME declaration, so a gate that refused everything would
    # redden here and a gate that refused nothing would redden there. Measured: before the
    # satisfaction function existed, every row in BOTH outlines was accepted.
    #
    # `product_option_ref` and `legacy_display_300x250` are the two EXACT routes: each
    # names one declaration outright, so a selector stating no params is complete and must
    # be accepted. That is the distinction between
    # create_media_buy_with_canonical_format_option (an option ref, no params, ACCEPT) and
    # reject_bare_image_selector_for_fixed_mrec (a kind, no params, REJECT) — without it
    # they are the same input.

  @T-UC-FORMAT-SATISFACTION-refused @format_satisfaction @requires_db
  Scenario Outline: a selector that does not satisfy the declaration is refused - <selector>
    Given the seller declares a canonical format option "<declaration>"
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the "<arm>" selector

    Examples:
      | declaration          | selector                         | arm                |
      | fixed_300x250        | bare_image_kind                  | format_kind        |
      | fixed_300x250        | mismatched_728x90                | format_kind        |
      | duration_range       | overlapping_range                | format_kind        |
      | required_slots       | slot_missing                     | format_kind        |
      | publisher_300        | wrong_publisher_domain           | format_option_refs |
      | fixed_300x250        | option_plus_unprojectable_legacy | format_ids         |
      | fixed_300x250        | unpublished_option_plus_exact_300x250 | format_option_refs |
      | fixed_300x250        | legacy_display_728x90            | format_ids         |
      | two_image_options    | mismatched_160x600               | format_kind        |
      | fixed_300x250        | oversized_file_300x250           | format_kind        |
      | fixed_300x250        | unaccepted_image_format_300x250  | format_kind        |
      | text_limited_300x250 | headline_over_limit_300x250      | format_kind        |
      | duration_range       | duration_omitted                 | format_kind        |
      | duration_range       | exact_duration_outside_range     | format_kind        |
      | fixed_300x250        | legacy_unsized_template          | format_ids         |
      | fixed_300x250        | foreign_agent_display_300x250    | format_ids         |
      | fixed_300x250        | malformed_agent_display_300x250  | format_ids         |
      | duration_range       | range_over_upper_bound           | format_kind        |
      | exact_duration       | exact_duration_mismatched        | format_kind        |
      | exact_duration       | contained_range                  | format_kind        |
      | exact_duration       | duration_omitted                 | format_kind        |
      | exact_over_range     | exact_duration_mismatched        | format_kind        |
      | required_slots       | slot_wrong_asset_type            | format_kind        |
      | publisher_300        | wrong_publisher_option_id        | format_option_refs |

    # legacy_unsized_template is a legacy reference that normalizes to the kind and nothing
    # else: the unsized display_image template states no fixed size, so it is the legacy
    # spelling of bare_image_kind, and core/product-format-declaration.json refuses it the
    # same way: "A broad request with no dimensions or duration does not satisfy a
    # fixed-size or fixed-duration product". foreign_agent_display_300x250 is display_300x250
    # at an agent that serves no declaration for it: the registry maps a legacy id only at
    # the reference agent's origin, so it projects to nothing and is refused on the same
    # arm. Until salesagent-ol033.20.26 both were ACCEPTED by satisfaction and then refused
    # VALIDATION_ERROR by a raw (agent_url, id) gate -- the comparison the same document
    # forbids -- so removing that gate needed the satisfaction rule to refuse them for the
    # reason the pin gives.
    #
    # malformed_agent_display_300x250 names the creative agent's host with two trailing dots.
    # docs/reference/url-canonicalization step 2: "More than one trailing dot creates an empty
    # DNS label rather than a root-label spelling and MUST be rejected". It names no agent, so
    # the registry's size upgrade does not apply and it is the third kind-only reference,
    # refused for the same reason. It used to raise out of the comparison as INTERNAL_ERROR.

    # bare_image_kind IS the storyboard's reject_bare_image_selector_for_fixed_mrec, and
    # mismatched_728x90 is formats.mdx's fifth rule stated as an input: the two selectors
    # name the SAME format_kind the product publishes and differ only in their params, so a
    # seller that treated an equal kind as proof of compatibility would accept both.
    #
    # The field is asserted, not just the code: core/error.json defines `field` as the
    # JSONPath-lite pointer naming WHICH request field was rejected, and the two canonical
    # arms are different fields — a refusal that named the wrong one would send the buyer
    # to edit a selector they did not write. The buyer-facing MESSAGE is deliberately not
    # asserted anywhere here: it is a function of the code through CODE_TABLE, so pinning
    # both would compare the table to itself.
    #
    # legacy_display_728x90 is a SINGLE legacy route that normalizes and then selects
    # nothing: measured on the pinned wheel, display_728x90 upgrades to display_image +
    # 728x90 and canonical_kind_for answers `image`, so against a product fixing 300x250 it
    # resolves and satisfies no declaration. The pin puts that in this bucket by itself —
    # "A single resolved route that does not satisfy the product is rejected with
    # UNSUPPORTED_FEATURE" — and the SAME legacy id beside a resolving option ref is
    # CONFLICTING_SELECTORS in the outline below. The two rows differ in the presence of the
    # second route and in nothing else, which is what grades the distinction rather than
    # asserting it: before this change this row was ACCEPTED, because an unmatched legacy
    # route fell through to the bare-kind route whose params grading a legacy route skips
    # (salesagent-t0uoo).
    #
    # duration_omitted is the duration half of the named list: a range is one of the four
    # constraints a selector MUST state, so a bare video_hosted is under-specified for a
    # product that bounds its duration -- canonical-formats.mdx's own example row,
    # "video_hosted with no duration ... Reject for this product". exact_duration_outside_range
    # pairs with exact_duration_in_range above: the same key, inside and outside the range.
    #
    # THE FIVE ROWS AFTER exact_duration_outside_range each grade one half of a rule that a
    # sibling row grades only together with another half, so a seller checking one half
    # passes the sibling and reddens here:
    #   - range_over_upper_bound mirrors overlapping_range on the HIGH endpoint ([10000,
    #     90000] against [5000, 60000]); containment tests both ends.
    #   - exact_duration_mismatched is "Exact ... durations must match" against a
    #     duration_ms_exact declaration, paired with exact_duration | exact_duration_in_range
    #     in the accept outline (30000 against 30000 accepted, 15000 refused).
    #   - exact_duration | contained_range: a RANGE claim does not satisfy an exact duration,
    #     because a 10s creative meets the claim and not the product. duration_ms_exact is
    #     one of the four constraints a selector MUST state (canonical-formats.mdx).
    #   - slot_wrong_asset_type offers the required group video_main as an image. slot_missing
    #     differs in the group AND the type, so only this row grades "with compatible asset
    #     types" by itself.
    #   - wrong_publisher_option_id names the RIGHT publisher_domain and an option id it does
    #     not publish; wrong_publisher_domain is the other half of "must match both
    #     publisher_domain and format_option_id".
    #   - exact_duration | duration_omitted is the EXACT half of duration_range |
    #     duration_omitted: silence about a fixed duration is refused as silence about a range is.
    #   - exact_over_range declares both duration keys, and core/product-format-declaration.json
    #     orders them: "Duration precedence is duration_ms_exact > duration_ms_range".
    #     exact_duration_mismatched (15000) lies inside the range but is not the exact 30000, so
    #     it is refused here, and exact_duration_in_range (30000) is accepted in the accept
    #     outline. A seller that graded the range instead of the exact value accepts both.
    #
    # mismatched_160x600 is the two-image-options refusal half: a selector that satisfies
    # NEITHER image declaration the product publishes. Without it, the two accept rows above are
    # satisfied by a seller that accepts any image selector whatever its params say.
    #
    # unpublished_option_plus_exact_300x250 is the step (1) ordering obligation on the
    # canonical arm, the sibling of option_plus_unprojectable_legacy on the legacy one: the
    # option ref fails to resolve and the direct 300x250 route beside it resolves and
    # selects. package-request.json's format_option_refs description: sellers "reject
    # unresolved options with UNSUPPORTED_FEATURE and a field path to the failing entry
    # before comparing co-present routes". Comparing first would answer
    # CONFLICTING_SELECTORS, the code the pin forbids for a resolution failure.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/properties/format_option_refs

  # ── The closed set: a format the product does not publish is refused ──────────────────
  #
  # core/product-format-declaration.json (v3.2.7), "Closed-set semantics (normative)":
  # "format_options[] is the closed set of accepted formats for this product. Sellers MUST
  # reject create_media_buy requests targeting any format_kind (or format option reference)
  # not present in this list -- typically with UNSUPPORTED_FEATURE or a seller-specific code;
  # the rejection is structural, not negotiable."
  #
  # The two outlines below are the same rule on the two product shapes the 3.x window
  # admits with declarations: dual-arm (format_ids beside format_options) and canonical-only
  # (format_options alone, the shape 3.2 tells new products to author). A canonical-only
  # product has no deprecated arm a seller could fall back to, so a closed set read from
  # format_ids would accept everything there and refuse correctly on the dual-arm product.
  #
  # THE DETAILS ARE ASSERTED, not only the code and field. An unpublished kind and an
  # unsatisfied params selector both answer UNSUPPORTED_FEATURE at packages[0].format_kind,
  # so code and field alone cannot say which rule refused. details.capability names it, and
  # core/error.json names rejected_value + accepted_values as the canonical rejection-set
  # keys. accepted_values is this seller's choice of what to name back: the product's kinds
  # for a kind refusal (the SDK's FormatKindNotInClosedSetError.accepted_kinds), and each
  # declaration's format_option_id for an option refusal, since that is the value a buyer
  # puts in format_option_refs. The pin requires only the rejection.
  #
  # The accept outline above is the control for both shapes: fixed_300x250 accepts
  # exact_300x250 and product_option_ref, and so does the canonical-only purchasable outline
  # further down, so a gate that refused everything reddens there.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product-format-declaration.json pointer=/description
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/error.json

  @T-UC-FORMAT-SATISFACTION-closed-set @format_satisfaction @requires_db
  Scenario Outline: a selector naming a format the product does not publish is refused - <selector>
    Given the seller declares a canonical format option "fixed_300x250"
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the "<arm>" selector
    And the refusal names the "<capability>" rule, rejecting "<rejected>" and accepting only "<accepted>"

    Examples:
      | selector               | arm                | capability           | rejected       | accepted                |
      | unpublished_video_kind | format_kind        | kind_not_published   | video_hosted   | image                   |
      | unpublished_option_ref | format_option_refs | option_not_published | no_such_option | satisfaction_image_mrec |

  @T-UC-FORMAT-SATISFACTION-closed-set-canonical-only @format_satisfaction @requires_db
  Scenario Outline: a canonical-only product refuses a format it does not publish - <selector>
    Given the seller declares a canonical format option "fixed_300x250" and no format_ids
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the "<arm>" selector
    And the refusal names the "<capability>" rule, rejecting "<rejected>" and accepting only "<accepted>"

    Examples:
      | selector               | arm                | capability           | rejected       | accepted                |
      | unpublished_video_kind | format_kind        | kind_not_published   | video_hosted   | image                   |
      | unpublished_option_ref | format_option_refs | option_not_published | no_such_option | satisfaction_image_mrec |

  @T-UC-FORMAT-SATISFACTION-conflicting @format_satisfaction @requires_db
  Scenario Outline: co-present selector routes that select different declarations are refused - <selector>
    Given the seller declares a canonical format option "fixed_300x250"
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused CONFLICTING_SELECTORS naming the package
    And the conflict summarizes the "format_option_refs" route as selecting "satisfaction_image_mrec"
    And the conflict summarizes the "<other_route>" route as selecting nothing

    Examples:
      | selector                       | other_route |
      | option_plus_conflicting_legacy | format_ids  |
      | option_plus_conflicting_direct | format_kind |
      | bare_kind_beside_option_ref    | format_kind |
      | option_plus_broad_legacy       | format_ids  |

    # option_plus_broad_legacy is bare_kind_beside_option_ref with the broad route spelled
    # as the legacy template display_image, which states no size. package-request.json's
    # format_kind: broad image "does not satisfy a fixed-size product declaration", and a
    # legacy reference that normalizes to the kind alone is graded the same way, so it
    # selects nothing beside an option ref that selects the 300x250 (salesagent-ol033.20.22).
    #
    # THE SUMMARIES are per ROUTE, and a route is an arm: enums/error-code.json asks the
    # details to "identify the supplied selector routes and their normalized canonical
    # summaries", and each summary carries the option SET its route selected, the thing step
    # (2) compares. The second Then lines name those sets, so a seller that answered the code
    # without resolving cannot pass them.

    # THE STORYBOARD'S TWO CONFLICT STEPS, as inputs.
    # option_plus_conflicting_legacy is reject_conflicting_dual_emission ("The canonical
    # option selects the product's 300x250 image declaration while the registry-backed
    # legacy display_728x90 selector normalizes to a 728x90 image. Canonical precedence must
    # not hide the dimensional conflict."), and option_plus_conflicting_direct is
    # reject_conflicting_canonical_routes ("The product-local option selects the fixed
    # 300x250 declaration while the co-present direct selector describes a 728x90 image.").
    #
    # WHY AN EMPTY SELECTED SET IS A CONFLICT AND NOT AN UNSUPPORTED_FEATURE. The product
    # publishes ONE image option, so the 728x90 route selects no option at all — and
    # reject_conflicting_canonical_routes grades that exact fixture as
    # CONFLICTING_SELECTORS. There is no other mechanism by which that step produces that
    # code, so "selects nothing" is a differing selected option set under
    # media-buy/package-request.json step (2) ("require the selected option sets to match").
    # bare_kind_beside_option_ref is the same shape with the params OMITTED rather than
    # wrong, so it takes the same code; it was graded UNSUPPORTED_FEATURE until this change,
    # when the comparison did not exist yet (salesagent-iql3e).
    #
    # THE PAIRING THAT MAKES THIS OUTLINE INFORMATIVE is in the accept outline above:
    # option_plus_projectable_legacy and direct_plus_legacy are these same two shapes with
    # the second route AGREEING, against the same declaration. So an always-conflict seller
    # reddens there and an always-accept seller reddens here, and neither outline can be
    # satisfied by a seller that reads only one route.
    #
    # The field is the PACKAGE and not an arm, which is this refusal's own shape: no single
    # arm is at fault, the set of them is, and enums/error-code.json says so for this code —
    # "error.field SHOULD point at the conflicting package". The sibling row
    # option_plus_unprojectable_legacy above stays UNSUPPORTED_FEATURE naming format_ids,
    # because an unnormalizable legacy id fails step (1) and the pin forbids relabelling it:
    # "Resolution failure is reported before selector equivalence and must not be
    # mislabeled CONFLICTING_SELECTORS."

  @T-UC-FORMAT-SATISFACTION-conflicting-option-sets @format_satisfaction @requires_db
  Scenario Outline: co-present routes whose selected option sets differ are refused - <declaration> / <selector>
    Given the seller declares a canonical format option "<declaration>"
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused CONFLICTING_SELECTORS naming the package
    And the conflict summarizes the "format_option_refs" route as selecting "<option_set>"
    And the conflict summarizes the "format_ids" route as selecting <legacy_set>

    Examples:
      | declaration       | selector                                | option_set                                             | legacy_set                                               |
      | two_mrec_options  | option_plus_projectable_legacy          | satisfaction_image_mrec                                | "satisfaction_image_mrec,satisfaction_image_mrec_gif"    |
      | two_mrec_options  | second_mrec_option_plus_legacy          | satisfaction_image_mrec_gif                            | "satisfaction_image_mrec,satisfaction_image_mrec_gif"    |
      | fixed_15s_video   | option_plus_conflicting_duration_legacy | satisfaction_video_15s                                 | nothing                                                  |
      | two_image_options | both_option_refs_plus_legacy            | satisfaction_image_mrec,satisfaction_image_leaderboard | "satisfaction_image_mrec"                                |

    # both_option_refs_plus_legacy grades format_option_refs as ONE route selecting a SET
    # (package-request.json: "Each reference matches one target product format_options[]
    # entry"; receivers "require each route to select the same product option set"). Its two
    # refs select both of two_image_options' entries and do not conflict with each other --
    # both_option_refs alone is accepted in the accept outline -- while display_300x250
    # beside them selects only the 300x250 its projection narrows to, so the arms differ.

    # media-buy/package-request.json step (2): "derive the product format_options[] entries
    # selected by each route using directional product satisfaction, and require the
    # selected option sets to match"; step (3) rejects "different ... selected option sets"
    # with CONFLICTING_SELECTORS; format_option_refs: "require each route to select the same
    # product option set". The legacy route's set is derived under the "v2 narrows v1"
    # relation the same step names (canonical-formats.mdx § "Narrows" -- formal definition).
    #
    # THE TWO two_mrec_options ROWS. The product publishes two 300x250 image options that
    # differ only in image_formats. display_300x250 projects to image 300x250 and states no
    # file format, so both options narrow it and the legacy route selects BOTH; an option
    # ref selects one. The sets differ whichever option the ref names, so both rows are
    # refused -- the verdict does not depend on declaration order. option_plus_projectable_legacy
    # is accepted against fixed_300x250 above, where the product publishes one option: the
    # rows differ in the product and in nothing else. legacy_set states the legacy route's
    # set exactly, because "the refs arm names its refs" and "the sets differ" also admit an
    # empty or one-entry legacy set -- the answer of a seller that declines to choose between
    # two narrowing declarations, or keeps the first.
    #
    # THE DURATION ROW. A legacy reference states duration as duration_ms
    # (core/format-id.json); the declaration states duration_ms_exact. The legacy route's
    # 30000 ms is graded against the option's 15000 ms, selects nothing, and the option ref
    # selects the option, so the sets differ. With the key left untranslated the duration
    # was never compared and the pair was accepted.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json
    # @source repo=adcp ref=v3.2.7 path=docs/creative/canonical-formats.mdx
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format-id.json

  @T-UC-FORMAT-SATISFACTION-legacy-only-accepted @format_satisfaction @requires_db
  Scenario: a legacy selector against a product publishing only deprecated format_ids is accepted
    Given the seller publishes only the deprecated format_ids
    And the buyer's package selects "legacy_display_300x250"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy

  @T-UC-FORMAT-SATISFACTION-legacy-only-option-ref @format_satisfaction @requires_db
  Scenario Outline: a format option ref against a product publishing no format_options is refused - <selector>
    Given the seller publishes only the deprecated format_ids
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the "format_option_refs" selector

    Examples:
      | selector                       |
      | product_option_ref             |
      | option_plus_projectable_legacy |

    # format_option_refs: "Each reference matches one target product format_options[]
    # entry", and step (1) says "an unresolved format_option_ref ... MUST be rejected with
    # UNSUPPORTED_FEATURE". A product that publishes no format_options[] has no entry to
    # match, and its deprecated format_ids name legacy ids, not options. Both rows were
    # ACCEPTED before: the deprecated lane graded only the legacy route and read a claim
    # with no legacy id as "no claim". core/product.json keeps such a product legal at the
    # pin ("Products MUST carry format_ids, format_options, or both during the 3.x migration
    # window").
    #
    # THE PAIR. product_option_ref is accepted against fixed_300x250 above and refused
    # here; the only difference is the product's canonical arm. option_plus_projectable_legacy
    # adds a legacy id the product DOES carry, which the accept scenario just above takes on
    # its own, so the refusal is the option ref's and not the legacy id's.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/properties/format_option_refs
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json

  # ── Direct and co-present routes on a product publishing ONLY format_ids ──────────────
  #
  # media-buy/package-request.json's selector contract binds every receiver, whichever arm
  # the product publishes: "(1) independently resolve each present route to canonical
  # declarations ... (2) derive the product format_options[] entries selected by each route
  # using directional product satisfaction, and require the selected option sets to match;
  # ... (3) reject different format shapes, selected option sets, or incompatible
  # dimensions with CONFLICTING_SELECTORS". A product publishing only the deprecated arm
  # has no format_options[] of its own, so the seller projects each deprecated entry to its
  # canonical declaration through the SDK (adcp.canonical_formats.projection, the
  # registries/v1-canonical-mapping.json order, failing closed) and resolves every route
  # against that. display_300x250 projects to image 300x250. Before this, a format_kind
  # route on such a product was never graded: a bare image, a 728x90 and a video route
  # beside display_300x250 were all accepted.
  #
  # The direct route alone is graded like any direct route: exact_300x250 states the size
  # the projection fixes and is accepted; bare_image_kind states none and is refused, which
  # is package-request.json's format_kind: broad image "does not satisfy a fixed-size
  # product declaration". legacy_unsized_template is the same broad shape on the legacy arm
  # and is refused as well.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/properties/format_kind

  @T-UC-FORMAT-SATISFACTION-legacy-only-direct-accepted @format_satisfaction @requires_db
  Scenario Outline: a direct route satisfying a legacy-only product's projected format is accepted - <selector>
    Given the seller publishes only the deprecated format_ids
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy

    Examples:
      | selector           |
      | exact_300x250      |
      | direct_plus_legacy |

  @T-UC-FORMAT-SATISFACTION-legacy-only-direct-refused @format_satisfaction @requires_db
  Scenario Outline: a broad route against a legacy-only product's fixed-size format is refused - <selector>
    Given the seller publishes only the deprecated format_ids
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the "<arm>" selector

    Examples:
      | selector                | arm         |
      | bare_image_kind         | format_kind |
      | legacy_unsized_template | format_ids  |

  @T-UC-FORMAT-SATISFACTION-legacy-only-conflicting @format_satisfaction @requires_db
  Scenario Outline: co-present routes selecting different formats of a legacy-only product are refused - <selector>
    Given the seller publishes only the deprecated format_ids
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused CONFLICTING_SELECTORS naming the package
    And the conflict summarizes the "format_kind" route as selecting nothing

    Examples:
      | selector                |
      | kind_728x90_plus_legacy |
      | video_kind_plus_legacy  |

    # The first row is incompatible dimensions, the second a different format shape: step
    # (3) names both. Their accept control is direct_plus_legacy above, the same two routes
    # agreeing on 300x250. The format_ids route selects the projection of display_300x250 --
    # the Then on the CONFLICTING_SELECTORS line asserts the two summarized sets differ, so it
    # cannot be empty -- and the format_kind route selects nothing.

  # ── Coverage: the selected format must cover every INCLUDED placement ─────────────────
  #
  # docs/creative/formats.mdx § Matching and satisfaction: "For a product with multiple
  # included placements, the selected format set must cover every placement. A seller must
  # reject or refine a package with an uncovered placement; it must not silently remove
  # inventory." core/product.json's format_options description restates it ("uncovered
  # inventory MUST be rejected or refined, never silently omitted") and says how a
  # placement's accepted set is computed: the intersection of the product format_options
  # and the placement's inline format_options, matched by format_option_id. This seller
  # rejects, with the satisfaction refusal: UNSUPPORTED_FEATURE naming the arm whose
  # selection left the placement uncovered.
  #
  # Every row stores two_image_options (image 300x250 and image 728x90) and differs only in
  # the stored placements and the selector. `split` has two INCLUDED placements, one
  # accepting each option, so neither option covers both. `split_leaderboard_targetable`
  # is `split` with the 728x90 placement TARGETABLE: core/placement.json makes an included
  # placement "fixed product inventory" that every package buys, while a targetable one is
  # bought only through placement_selection, so that row grades the mode and nothing else.
  # `shared` has both included placements accepting 300x250.
  #
  # A package with NO selector is accepted against `split`: media-buy/package-request.json,
  # "omitting both selects all formats supported by the product", and the product set
  # covers both placements.
  #
  # @source repo=adcp ref=v3.2.7 path=docs/creative/formats.mdx
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/format_options/description
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/placement.json pointer=/properties/mode/description
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/placement.json pointer=/properties/format_options/description
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/properties/format_option_refs/description

  @T-UC-FORMAT-SATISFACTION-placements-covered @format_satisfaction @requires_db
  Scenario Outline: a selected format that covers every included placement is accepted - <declaration> <placements> <selector>
    Given the seller declares canonical format options "<declaration>" across placements "<placements>"
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy

    Examples:
      | declaration       | placements                   | selector           |
      | two_image_options | shared                       | product_option_ref |
      | two_image_options | split_leaderboard_targetable | product_option_ref |
      | two_image_options | split                        | no_selector        |
      | two_image_options | split                        | both_option_refs   |
      | two_image_options | shared_inline                | product_option_ref |
      | duration_range    | narrowed_inline              | contained_range    |
      | duration_range    | widened_inline               | contained_range    |
      | two_image_options | unnarrowed                   | product_option_ref |
      | fixed_300x250     | narrowed_file_size           | product_option_ref |
      | two_image_options | publisher_ref_targetable     | product_option_ref |
      | two_image_options | publisher_ref_targetable     | no_selector        |

    # both_option_refs is the spec-valid way to cover `split` explicitly: two
    # format_option_refs on ONE arm select a SET (package-request.json: "require each route
    # to select the same product option set"), and core/product.json lets that set be a
    # union: "Distinct per-placement creatives MAY use the union, but the selected creative
    # set MUST cover every included placement". It pairs with split | product_option_ref
    # below, which differs only in the second ref.
    #
    # The two *_inline rows state a placement's inline declaration WITHOUT a
    # format_option_id, so it is matched by the third rule of core/product.json: "match
    # declarations with the same format_kind whose narrower parameters satisfy the broader
    # declaration". The placement is the narrower side (core/placement.json: its
    # declarations "MUST NOT introduce a format outside that product upper bound").
    # narrowed_inline narrows the product's duration_ms_range [5000, 60000] to [5000,
    # 30000]: a seller that grades the product declaration against the placement's refuses
    # it. shared_inline restates 300x250 and pairs with split_inline below, which states
    # 728x90 instead. widened_inline is the other direction: the placement's range [1000,
    # 120000] is BROADER than the product's, so the selected option is the narrower side and
    # satisfies the placement; a seller that only grades the placement against the option
    # refuses it.
    #
    # unnarrowed names two included placements and narrows neither: "An omitted optional
    # layer is unconstrained", so each inherits the product's set and one option covers both.
    # narrowed_file_size narrows only max_file_size_kb under fixed_300x250, which fixes width
    # and height; the placement states no size, so it inherits the option's 300x250
    # (canonical-formats.mdx § "Narrows": "v2 inherits v1's value") and the package is
    # accepted.

  @T-UC-FORMAT-SATISFACTION-placements-uncovered @format_satisfaction @requires_db
  Scenario Outline: a selected format that leaves an included placement uncovered is refused - <declaration> <placements> <selector>
    Given the seller declares canonical format options "<declaration>" across placements "<placements>"
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the "<arm>" selector for an uncovered placement of "<selected>"

    Examples:
      | declaration       | placements                   | selector                | arm                | selected                       |
      | two_image_options | split                        | product_option_ref      | format_option_refs | satisfaction_image_mrec        |
      | two_image_options | split                        | exact_300x250           | format_kind        | satisfaction_image_mrec        |
      | two_image_options | split_inline                 | product_option_ref      | format_option_refs | satisfaction_image_mrec        |
      | two_image_options | shared                       | leaderboard_option_ref  | format_option_refs | satisfaction_image_leaderboard |
      | two_image_options | split_leaderboard_targetable | leaderboard_option_ref  | format_option_refs | satisfaction_image_leaderboard |
      | exact_duration    | short_range_inline           | exact_duration_in_range | format_kind        | satisfaction_video_exact       |

    # Each refusal row has an accept row that differs in ONE thing: `shared` (the second
    # placement also accepts 300x250), `split_leaderboard_targetable` (the second placement
    # is not included), split | both_option_refs (the selection adds the 728x90 option) and
    # shared_inline (the id-less inline declaration states 300x250, not 728x90). A seller
    # that ignored placements accepts every refusal row; one that refused every
    # multi-placement product reddens the accept rows. split_inline is the refusal half of
    # the format_kind-plus-params match: a seller matching an id-less declaration by kind
    # alone accepts it. The first two selectors select the same 300x250 declaration by
    # different routes, so the refusal is about the selected declaration and not the route
    # that named it.
    #
    # shared | leaderboard_option_ref keeps the placements of an accept row and changes only
    # the selector: the 728x90 leaves the 300x250-only placement uncovered, so a seller that
    # refused every product with placements is not the only one it catches.
    # split_leaderboard_targetable | leaderboard_option_ref is refused where the same
    # placements with product_option_ref are accepted: the included placement still
    # constrains when a targetable one is present. exact_duration | short_range_inline is a
    # placement stating only a duration_ms_range [5000, 20000] under an option fixed at an
    # exact 30000 ms: the placement does not inherit the exact value it excludes, so a seller
    # that grades only the params the placement states accepts it.
    #
    # The Then reads details.capability and details.rejected_value as well as the code and
    # the field, because UNSUPPORTED_FEATURE naming format_option_refs is also what an
    # unpublished option ref gets. The capability names the rule that refused, and the
    # rejected value is the declaration the package selected.

  @T-UC-FORMAT-SATISFACTION-placements-unresolved @format_satisfaction @requires_db
  Scenario Outline: an included publisher-referenced placement fails closed for a selecting package - <selector>
    Given the seller declares canonical format options "two_image_options" across placements "publisher_ref_included"
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the "<arm>" selector for the unresolved publisher placement "satisfaction_publisher_slot"

    Examples:
      | selector           | arm                |
      | product_option_ref | format_option_refs |
      | exact_300x250      | format_kind        |

    # core/product.json format_options: a placement's effective set intersects "(3) for kind
    # publisher_ref, the named publisher's adagents.json catalog narrowing", and "an
    # unresolved publisher placement or format-option reference MUST fail closed". This
    # seller has no adagents.json resolver on the buy path, so an INCLUDED publisher_ref
    # placement cannot be shown to accept anything a package selects, and the package is
    # refused until a resolver exists. The seller-inline placement beside it accepts the
    # 300x250 both rows select, so the refusal is the publisher placement's. The accept
    # controls are in the covered outline: the same placement TARGETABLE (not bought), with
    # each of these selectors and with none.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/format_options/description
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/placement.json pointer=/properties/format_options/description

  @T-UC-FORMAT-SATISFACTION-placements-unresolved-no-selector @format_satisfaction @requires_db
  Scenario: an included publisher-referenced placement fails closed for a package naming no selector
    Given the seller declares canonical format options "two_image_options" across placements "publisher_ref_included"
    And the buyer's package selects "no_selector"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the package's product for the unresolved publisher placement "satisfaction_publisher_slot"

    # A package naming no selector "selects all formats supported by the product"
    # (media-buy/package-request.json), and that set is still graded against an included
    # placement's EFFECTIVE set, which for a publisher_ref placement includes layer (3), the
    # publisher's adagents.json narrowing. This seller cannot resolve that layer, so the
    # product's whole set cannot be shown to cover the placement either, and core/product.json
    # leaves no exemption for the selector-less package: "an unresolved publisher placement or
    # format-option reference MUST fail closed". It is refused exactly as a selecting package
    # is, with the same capability and rejected value; the pointer names the product, because
    # the buyer selected nothing to name. The accept control is publisher_ref_targetable |
    # no_selector in the covered outline, which differs only in the placement's mode.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/format_options/description
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/description

  # ── Coverage of a multi-publisher product WITHOUT placements: publisher slices ─────────
  #
  # docs/creative/formats.mdx § Matching and satisfaction: "If a multi-publisher product
  # omits placements[], each publisher_properties[] selector is an eligibility slice:
  # product-local options apply to every slice, publisher-scoped options only to their
  # publisher". canonical-formats.mdx says it again: "distinct creatives must cover every
  # slice". core/product.json format_options addresses the same product to its AUTHOR: its
  # format_options "MUST therefore be the common intersection accepted across every selected
  # publisher/property scope". The texts agree: a publisher-scoped option is outside that
  # intersection, and a package that selects only it, or creatives that satisfy only it,
  # leave the other publisher's slice uncovered. This seller rejects (the pin allows reject or
  # refine), with the placement-coverage refusal: details.capability placement_uncovered.
  #
  # local_and_publisher_options is two_image_options with the 728x90 option scoped to
  # shorts.streamhaus.example; the 300x250 stays product-local. streamhaus_and_other sells
  # that publisher and other.example, streamhaus_only the first alone.
  #
  # Seller choice, recorded: a slice no declaration applies to is an empty set and fails
  # closed for every selection, a package naming no selector included
  # (publisher-slices-empty-uncovered). The pin's "an indeterminate slice fails closed" has no other
  # instance here, because every declaration this seller stores is inline and resolves
  # without the publisher's adagents.json.
  #
  # @source repo=adcp ref=v3.2.7 path=docs/creative/formats.mdx
  # @source repo=adcp ref=v3.2.7 path=docs/creative/canonical-formats.mdx
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/format_options/description
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product-format-declaration.json pointer=/properties/publisher_domain/description

  @T-UC-FORMAT-SATISFACTION-publisher-slices-covered @format_satisfaction @requires_db
  Scenario Outline: a selection that covers every publisher's slice is accepted - <publishers> <selector>
    Given the seller declares canonical format options "local_and_publisher_options" across publishers "<publishers>"
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy

    Examples:
      | publishers           | selector                  |
      | streamhaus_and_other | product_option_ref        |
      | streamhaus_and_other | local_and_publisher_refs  |
      | streamhaus_and_other | no_selector               |
      | streamhaus_only      | publisher_leaderboard_ref |

  @T-UC-FORMAT-SATISFACTION-publisher-slices-uncovered @format_satisfaction @requires_db
  Scenario: a selection of one publisher's scoped option leaves another publisher's slice uncovered
    Given the seller declares canonical format options "local_and_publisher_options" across publishers "streamhaus_and_other"
    And the buyer's package selects "publisher_leaderboard_ref"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the "format_option_refs" selector for an uncovered publisher slice of "satisfaction_image_leaderboard"

    # Each accept row differs from the refusal in one thing: streamhaus_only drops the
    # publisher the scoped option does not apply to, local_and_publisher_refs adds the
    # product-local option, and product_option_ref selects it instead. A seller that graded a
    # multi-publisher product on its whole option set (the reading before this rule) accepts
    # the refusal; one that refused every publisher-scoped selection reddens streamhaus_only.
    # no_selector "selects all formats supported by the product" (package-request.json),
    # which include the product-local option every slice accepts.

  @T-UC-FORMAT-SATISFACTION-publisher-slices-empty-covered @format_satisfaction @requires_db
  Scenario: a package naming no selector is accepted when every option is scoped to its one publisher
    Given the seller declares canonical format options "publisher_only_options" across publishers "streamhaus_only"
    And the buyer's package selects "no_selector"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy

  @T-UC-FORMAT-SATISFACTION-publisher-slices-empty-uncovered @format_satisfaction @requires_db
  Scenario: a package naming no selector is refused when a publisher's slice is empty
    Given the seller declares canonical format options "publisher_only_options" across publishers "streamhaus_and_other"
    And the buyer's package selects "no_selector"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the package's product for the uncovered publisher slice "other.example"

    # The recorded seller choice above, graded: publisher_only_options scopes both options to
    # shorts.streamhaus.example, so other.example's slice is empty. The package names no
    # selector and so "selects all formats supported by the product" (package-request.json),
    # yet none of them applies to that slice; accepting it would buy other.example's
    # inventory with no format that can serve it, which formats.mdx forbids ("it must not
    # silently remove inventory"). The accept control differs in one thing: streamhaus_only
    # drops other.example. A seller treating a selector-less package as covered by
    # construction accepts the refusal; one refusing every publisher-scoped product reddens
    # the control. The buyer selected nothing, so the refusal points at
    # packages[0].product_id and its rejected_value is the empty slice's publisher_domain.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/formats.mdx
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/description

  @T-UC-FORMAT-SATISFACTION-publisher-slices-creatives-covered @format_satisfaction @requires_db
  Scenario Outline: creatives that cover every publisher's slice are accepted - <publishers> <manifests>
    Given the seller declares canonical format options "local_and_publisher_options" across publishers "<publishers>"
    And the buyer assigns canonical "image" creatives carrying the "<manifests>" manifests naming product options "<options>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy

    Examples:
      | publishers           | manifests                  | options                                                |
      | streamhaus_and_other | image_300x250              | satisfaction_image_mrec                                |
      | streamhaus_and_other | image_300x250,image_728x90 | satisfaction_image_mrec,satisfaction_image_leaderboard |
      | streamhaus_only      | image_728x90               | satisfaction_image_leaderboard                         |

  @T-UC-FORMAT-SATISFACTION-publisher-slices-creatives-uncovered @format_satisfaction @requires_db
  Scenario: creatives satisfying only one publisher's scoped option leave another publisher's slice uncovered
    Given the seller declares canonical format options "local_and_publisher_options" across publishers "streamhaus_and_other"
    And the buyer assigns canonical "image" creatives carrying the "image_728x90" manifests naming product options "satisfaction_image_leaderboard"
    When the Buyer Agent sends the create_media_buy request
    Then the creatives are refused CREATIVE_VALIDATION_FAILED_GENERIC at "packages" for leaving the publisher slice "other.example" uncovered

    # The 728x90 creative satisfies the product on its own (the scoped option), so a seller
    # grading creatives one at a time accepts it. The accept rows differ in one thing:
    # streamhaus_only drops other.example, and image_300x250,image_728x90 adds the creative
    # that fills its slice. The package names no selector, so the refusal is the creative
    # set's, and it names the slice by its publisher_domain. local_and_publisher_options
    # declares two image options, so each creative names its option: format_option_ref is
    # "REQUIRED on the manifest when the target product's format_options contains two or
    # more declarations sharing the same format_kind" (canonical-formats.mdx;
    # @T-UC-FORMAT-SATISFACTION-manifest-ref-required). The 728x90 option is scoped to
    # shorts.streamhaus.example, so its ref is the publisher arm.

  # ── Coverage through the DEPRECATED arm: a legacy-only product's placements ───────────
  #
  # core/placement.json format_ids at the pin: "When present on a product placement, this
  # field narrows the product-level format_ids contract and MUST NOT introduce formats the
  # product does not accept." A legacy-only product's placements narrow through that arm and
  # no other, so the coverage rule reads it, projected through the SDK exactly as the
  # product's own deprecated arm is. legacy_split is `split` spelled in the deprecated arm:
  # the product sells display_300x250 and display_728x90, and each included placement
  # narrows to one of them.
  #
  # The refusal names the reference the buyer sent: a legacy-only product published no
  # option ids, and the ones the SDK's projection mints would name options the buyer was
  # never shown.
  #
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/placement.json pointer=/properties/format_ids/description
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/format_options/description

  @T-UC-FORMAT-SATISFACTION-legacy-placements-covered @format_satisfaction @requires_db
  Scenario Outline: a deprecated selection that covers every legacy-narrowed placement is accepted - <selector> <manifests>
    Given the seller publishes only the deprecated 300x250 and 728x90 formats across placements "legacy_split"
    And the buyer's package selects "<selector>"
    And the buyer assigns canonical "image" creatives carrying the "<manifests>" manifests
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy

    Examples:
      | selector          | manifests                  |
      | no_selector       | image_300x250,image_728x90 |
      | legacy_both_sizes | image_300x250,image_728x90 |

  @T-UC-FORMAT-SATISFACTION-legacy-placements-uncovered @format_satisfaction @requires_db
  Scenario Outline: a deprecated selection that leaves a legacy-narrowed placement uncovered is refused - <selector>
    Given the seller publishes only the deprecated 300x250 and 728x90 formats across placements "legacy_split"
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the "format_ids" selector for an uncovered placement of "<selected>"

    Examples:
      | selector               | selected                                               |
      | legacy_display_300x250 | https://creative.adcontextprotocol.org/display_300x250 |
      | legacy_display_728x90  | https://creative.adcontextprotocol.org/display_728x90  |

    # Each row's accept control is legacy_both_sizes above: the same arm naming both sizes.
    # A seller that skipped a placement stating only format_ids accepts both rows, which is
    # what this seller did before the deprecated arm was read as a narrowing.

  # ── Coverage by the package's CREATIVES ───────────────────────────────────────────────
  #
  # The same rule asked of the creatives rather than the selector. core/product.json
  # format_options at the pin: "For a product or package containing multiple included
  # placements, a single creative intended for every placement MUST lie in the intersection
  # of every selected placement's effective set. Distinct per-placement creatives MAY use
  # the union, but the selected creative set MUST cover every included placement; uncovered
  # inventory MUST be rejected or refined, never silently omitted." This seller rejects.
  #
  # A creative covers a placement when its assignment routes it there and its manifest
  # satisfies the placement's effective set. A create package routes no creative, so each is
  # "eligible for all placements" (sync-creatives-request.json placement_ids). The packages
  # here name no selector, so the selector rule accepts them and the refusal is the creative
  # set's. The code is CREATIVE_VALIDATION_FAILED_GENERIC, the pinned enum's code for a
  # buyer-correctable creative failure no more specific code names, and the refusal names
  # the uncovered placement.
  #
  # Every canonical creative here names its product option: two_image_options publishes two
  # image declarations, and canonical-formats.mdx makes format_option_ref "REQUIRED on the
  # manifest when the target product's format_options contains two or more declarations
  # sharing the same format_kind" (@T-UC-FORMAT-SATISFACTION-manifest-ref-required). The
  # options column is aligned with the manifests.
  #
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/format_options/description
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/sync-creatives-request.json pointer=/properties/assignments/items/properties/placement_ids/description
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json

  @T-UC-FORMAT-SATISFACTION-creatives-covered @format_satisfaction @requires_db
  Scenario Outline: a package whose creatives cover every included placement is accepted - <placements> <manifests>
    Given the seller declares canonical format options "two_image_options" across placements "<placements>"
    And the buyer assigns canonical "image" creatives carrying the "<manifests>" manifests naming product options "<options>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy

    Examples:
      | placements                   | manifests                  | options                                                |
      | split                        | image_300x250,image_728x90 | satisfaction_image_mrec,satisfaction_image_leaderboard |
      | shared                       | image_300x250              | satisfaction_image_mrec                                |
      | split_leaderboard_targetable | image_300x250              | satisfaction_image_mrec                                |
      | unnarrowed                   | image_300x250              | satisfaction_image_mrec                                |

  @T-UC-FORMAT-SATISFACTION-creatives-uncovered @format_satisfaction @requires_db
  Scenario Outline: a package whose creatives leave an included placement uncovered is refused - <placements> <manifests>
    Given the seller declares canonical format options "two_image_options" across placements "<placements>"
    And the buyer assigns canonical "image" creatives carrying the "<manifests>" manifests naming product options "<options>"
    When the Buyer Agent sends the create_media_buy request
    Then the creatives are refused CREATIVE_VALIDATION_FAILED_GENERIC at "packages" for leaving placement "<placement>" uncovered

    Examples:
      | placements | manifests     | options                        | placement                     |
      | split      | image_300x250 | satisfaction_image_mrec        | satisfaction_leaderboard_slot |
      | split      | image_728x90  | satisfaction_image_leaderboard | satisfaction_mrec_slot        |
      | shared     | image_728x90  | satisfaction_image_leaderboard | satisfaction_mrec_slot        |

    # Every creative here satisfies the PRODUCT on its own -- two_image_options publishes
    # both sizes -- so a seller grading creatives one at a time accepts every row. Each row
    # has an accept control differing in one thing: split | image_300x250,image_728x90 adds
    # the creative for the other placement, shared | image_300x250 swaps the size for the
    # one both placements take, and split_leaderboard_targetable | image_300x250 makes the
    # uncovered placement targetable, which a package does not buy. unnarrowed is the
    # control for a seller that refused every product with included placements.

  @T-UC-FORMAT-SATISFACTION-creatives-cover-placement-narrowing-one-option-twice @format_satisfaction @requires_db
  Scenario Outline: a creative naming no option covers a placement that narrows the product's one option into two entries - <manifests>
    Given the seller declares canonical format options "plain_300x250" across placements "narrowed_twice"
    And the buyer assigns canonical "image" creatives carrying the "<manifests>" manifests
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy

    Examples:
      | manifests           |
      | image_300x250       |
      | image_300x250_120kb |

  @T-UC-FORMAT-SATISFACTION-creatives-miss-placement-narrowing-one-option-twice @format_satisfaction @requires_db
  Scenario: a creative fitting neither entry of a placement that narrows the product's one option twice leaves it uncovered
    Given the seller declares canonical format options "plain_300x250" across placements "narrowed_twice"
    And the buyer assigns canonical "image" creatives carrying the "image_300x250_200kb" manifests
    When the Buyer Agent sends the create_media_buy request
    Then the creatives are refused CREATIVE_VALIDATION_FAILED_GENERIC at "packages" for leaving placement "satisfaction_capped_mrec_slot" uncovered

    # THE REF IS REQUIRED BY THE PRODUCT, NOT BY A PLACEMENT. canonical-formats.mdx makes
    # format_option_ref "REQUIRED on the manifest when the target product's format_options
    # contains two or more declarations sharing the same format_kind" and "OPTIONAL when each
    # format_kind in the product's format_options is unique". plain_300x250 publishes image
    # once, so these creatives name no option. narrowed_twice is one included placement whose
    # format_options narrow that option into two image entries, capped at 100 KB and 150 KB
    # (core/placement.json: they "are intersected with the product-level format_options"), so
    # the placement's effective set holds two same-kind entries. A creative lies in that set
    # when it satisfies either entry: the 51,200-byte image fits both, the 120,000-byte one
    # only the 150 KB entry. A seller that applies the ref rule to the placement's entries
    # finds both creatives ambiguous there and refuses them as uncovering the placement.
    #
    # The refusal differs from the accept rows only in the creative's size: 200,000 bytes is
    # over both caps (1 KB = 1,000 bytes) and inside the product's uncapped option, so the
    # product accepts it and only the placement does not. A seller ignoring the placement's
    # narrowing accepts it.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/canonical-formats.mdx section="Worked example — multi-format product (third-party html5 OR internal display_tag)"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/placement.json pointer=/properties/format_options/description
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/format_options/description

  @T-UC-FORMAT-SATISFACTION-legacy-placements-creatives-uncovered @format_satisfaction @requires_db
  Scenario Outline: creatives that leave a legacy-narrowed placement uncovered are refused - <manifests>
    Given the seller publishes only the deprecated 300x250 and 728x90 formats across placements "legacy_split"
    And the buyer assigns canonical "image" creatives carrying the "<manifests>" manifests
    When the Buyer Agent sends the create_media_buy request
    Then the creatives are refused CREATIVE_VALIDATION_FAILED_GENERIC at "packages" for leaving placement "<placement>" uncovered

    Examples:
      | manifests     | placement                     |
      | image_300x250 | satisfaction_leaderboard_slot |
      | image_160x600 | satisfaction_mrec_slot        |

    # The accept control is legacy-placements-covered | no_selector, the same product with
    # both sizes assigned. image_160x600 is a canonical image a legacy-only product accepts
    # on its own -- the product publishes no canonical declaration to hold it to a size --
    # and it fits neither placement, so the first included placement is the one reported.

  # ── Coverage on a legacy-only product the SDK cannot project ──────────────────────────
  #
  # core/product.json format_options at the pin: "An omitted optional layer is
  # unconstrained". An included placement that narrows nothing therefore admits exactly what
  # the product admits, and a legacy-only product admits what its deprecated arm accepts,
  # whether or not the SDK can project that arm. The product here sells
  # display_300x250_image_2x and display_728x90_image_2x, two ids the pinned agent serves and
  # the SDK projects to nothing. A coverage rule built from the projection alone has an empty
  # set for every placement and refuses every creative the product accepts.
  #
  # The deprecated arm narrows by reference: core/placement.json format_ids "narrows the
  # product-level format_ids contract". A creative claiming a reference is compared with the
  # placement's references the way the product's own arm compares it.
  #
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/format_options/description
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/placement.json pointer=/properties/format_ids/description

  @T-UC-FORMAT-SATISFACTION-unprojectable-placements-creatives-covered @format_satisfaction @requires_db
  Scenario Outline: creatives a legacy-only product accepts cover its placements that narrow nothing - <placements>
    Given the seller publishes only deprecated formats the SDK cannot project across placements "<placements>"
    And the buyer assigns canonical "image" creatives carrying the "image_300x250" manifests
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy

    Examples:
      | placements |
      | none       |
      | unnarrowed |

    # `none` is the control: the same product and creative with no included placement. A
    # seller grading an unconstrained placement against the projection refuses `unnarrowed`
    # CREATIVE_VALIDATION_FAILED_GENERIC and accepts `none`.

  @T-UC-FORMAT-SATISFACTION-update-creatives-covered @format_satisfaction @requires_db
  Scenario Outline: update_media_buy accepts creative assignments that cover every included placement - <routing>
    Given the seller declares canonical format options "two_image_options" across placements "split"
    And the buyer already holds a media buy for that product
    And canonical "image" creatives carrying the "<manifests>" manifests naming product options "<options>" exist
    When the Buyer Agent assigns those creatives through update_media_buy routed to "<routing>"
    Then the update is accepted

    Examples:
      | manifests                  | options                                                | routing                                              |
      | image_300x250,image_728x90 | satisfaction_image_mrec,satisfaction_image_leaderboard | satisfaction_mrec_slot,satisfaction_leaderboard_slot |
      | image_300x250,image_728x90 | satisfaction_image_mrec,satisfaction_image_leaderboard | any,any                                              |

  @T-UC-FORMAT-SATISFACTION-update-creatives-uncovered @format_satisfaction @requires_db
  Scenario Outline: update_media_buy refuses creative assignments that leave an included placement uncovered - <routing>
    Given the seller declares canonical format options "two_image_options" across placements "split"
    And the buyer already holds a media buy for that product
    And canonical "image" creatives carrying the "<manifests>" manifests naming product options "<options>" exist
    When the Buyer Agent assigns those creatives through update_media_buy routed to "<routing>"
    Then the creatives are refused CREATIVE_VALIDATION_FAILED_GENERIC at "packages[0].creative_assignments" for leaving placement "<placement>" uncovered

    Examples:
      | manifests                  | options                                                | routing                                              | placement                     |
      | image_300x250,image_728x90 | satisfaction_image_mrec,satisfaction_image_leaderboard | satisfaction_leaderboard_slot,satisfaction_mrec_slot | satisfaction_mrec_slot        |
      | image_300x250              | satisfaction_image_mrec                                | any                                                  | satisfaction_leaderboard_slot |

    # The ROUTING is what the first row grades: the same two creatives as the accept rows,
    # each routed by placement_ids to the placement it does not fit. A seller that pooled the
    # creatives across placements accepts it. The second row is the single creative meant
    # for every placement, which "MUST lie in the intersection of every selected placement's
    # effective set" -- and split's two placements share nothing.

  # ── Coverage after an INLINE replacement on update_media_buy ──────────────────────────
  #
  # package-update.json creatives: "Replace this package's inline creative assets", and
  # update_media_buy.mdx scopes that array to the inline bodies alone, so the library
  # creatives assigned through creative_ids / creative_assignments stay. The replaced bodies
  # are dropped AFTER the new ones are synced, so a set that covered every included
  # placement while both were assigned can stop covering one once the old bodies go. The
  # rule of the section above applies to the set the replacement leaves: core/product.json
  # format_options "the selected creative set MUST cover every included placement;
  # uncovered inventory MUST be rejected or refined, never silently omitted".
  #
  # The refusal is the whole request's: update_media_buy.mdx § Compatibility transaction
  # boundary, "One update_media_buy request is one atomic mutation ... No package,
  # assignment, library creative, or MediaBuy field from the rejected update may change".
  # The new bodies are written before the drop the refusal grades, and the wire envelope
  # reads the same whether or not they stayed, so the refusal rows read the package and the
  # creative library back as well.
  #
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/creatives/description
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/format_options/description
  # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/update_media_buy.mdx

  @T-UC-FORMAT-SATISFACTION-update-inline-creatives-covered @format_satisfaction @requires_db
  Scenario Outline: update_media_buy accepts an inline replacement that leaves every included placement covered - <inline> <library> -> <uploaded>
    Given the seller declares canonical format options "two_image_options" across placements "split"
    And the buyer already holds a media buy for that product
    And the package already carries inline canonical "image" creatives carrying the "<inline>" manifests
    And the package already carries library canonical "image" creatives carrying the "<library>" manifests
    When the Buyer Agent replaces the package's inline creatives through update_media_buy with canonical "image" creatives carrying the "<uploaded>" manifests naming product options "<options>"
    Then the update is accepted

    Examples:
      | inline                     | library       | uploaded                   | options                                                |
      | image_300x250,image_728x90 | image_160x600 | image_300x250,image_728x90 | satisfaction_image_mrec,satisfaction_image_leaderboard |
      | image_300x250              | image_728x90  | image_300x250              | satisfaction_image_mrec                                |

  @T-UC-FORMAT-SATISFACTION-update-inline-creatives-uncovered @format_satisfaction @requires_db
  Scenario Outline: update_media_buy refuses an inline replacement that leaves an included placement uncovered - <uploaded>
    Given the seller declares canonical format options "two_image_options" across placements "split"
    And the buyer already holds a media buy for that product
    And the package already carries inline canonical "image" creatives carrying the "image_300x250,image_728x90" manifests
    And the package already carries library canonical "image" creatives carrying the "image_160x600" manifests
    When the Buyer Agent replaces the package's inline creatives through update_media_buy with canonical "image" creatives carrying the "<uploaded>" manifests naming product options "<options>"
    Then the creatives are refused CREATIVE_VALIDATION_FAILED_GENERIC at "packages[0].creatives" for leaving placement "<placement>" uncovered
    And the package keeps the creatives it held and none of the uploaded creatives is stored

    Examples:
      | uploaded      | options                        | placement                     |
      | image_300x250 | satisfaction_image_mrec        | satisfaction_leaderboard_slot |
      | image_728x90  | satisfaction_image_leaderboard | satisfaction_mrec_slot        |

    # Every uploaded creative is one the product takes, and the new set together with the
    # held bodies covers both placements, so a seller that asks coverage only before the drop
    # accepts both rows. The first accept row uploads both sizes and differs from the refusal
    # rows only in what it uploads. The second holds the 728x90 as a LIBRARY creative, which
    # the inline replacement keeps, so the same upload as the first refusal row is accepted;
    # a seller that dropped library creatives with the inline ones refuses it. The library
    # image_160x600 fits neither placement, so it covers nothing in any row: a seller that
    # counted a held creative as covering without grading it accepts the refusal rows.

  @T-UC-FORMAT-SATISFACTION-sync-creatives-covered @format_satisfaction_sync @requires_db
  Scenario: sync_creatives accepts an assignment whose creative covers every included placement
    Given a packaged product whose placements "shared" narrow canonical format options "two_image_options"
    And the buyer syncs a canonical "image" creative carrying the "image_300x250" manifest and naming product option "satisfaction_image_mrec"
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error

  @T-UC-FORMAT-SATISFACTION-sync-creatives-uncovered @format_satisfaction_sync @requires_db
  Scenario: sync_creatives refuses an assignment that leaves an included placement uncovered
    Given a packaged product whose placements "split" narrow canonical format options "two_image_options"
    And the buyer syncs a canonical "image" creative carrying the "image_300x250" manifest and naming product option "satisfaction_image_mrec"
    When the Buyer Agent assigns it to that package
    Then the creatives are refused CREATIVE_VALIDATION_FAILED_GENERIC at "assignments" for leaving placement "satisfaction_leaderboard_slot" uncovered

    # The pair differs only in the stored placements. The refusal is raised whatever the
    # validation mode: lenient skips a creative the product will not take, and here every
    # creative is one the product takes -- what fails is the package's set.

  @T-UC-FORMAT-SATISFACTION-sync-unprojectable-placements-covered @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives accepts a deprecated creative the SDK cannot project on placements that admit it - <placements>
    Given a packaged product selling only deprecated formats the SDK cannot project across placements "<placements>"
    And the buyer syncs a creative identified by format_id "display_300x250_image_2x"
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error

    Examples:
      | placements         |
      | none               |
      | unnarrowed         |
      | unprojectable_mrec |

  @T-UC-FORMAT-SATISFACTION-sync-unprojectable-placements-uncovered @format_satisfaction_sync @requires_db
  Scenario: sync_creatives refuses a deprecated creative the SDK cannot project that leaves a placement uncovered
    Given a packaged product selling only deprecated formats the SDK cannot project across placements "unprojectable_split"
    And the buyer syncs a creative identified by format_id "display_300x250_image_2x"
    When the Buyer Agent assigns it to that package
    Then the creatives are refused CREATIVE_VALIDATION_FAILED_GENERIC at "assignments" for leaving placement "satisfaction_leaderboard_slot" uncovered

    # The rule of the create-path outline above, asked of a creative CLAIMING a deprecated
    # reference, which reaches the seller only through sync. unprojectable_mrec narrows to the
    # creative's own reference; unprojectable_split adds a placement narrowing to the 728x90
    # one, which the creative does not fill, and the refusal names that placement. A seller
    # grading the placements through the projection refuses the unnarrowed and
    # unprojectable_mrec rows and names satisfaction_mrec_slot in the refusal; one that
    # ignored a narrowing it cannot project accepts the refusal row. `none` is the control.

  # ── The CREATIVE side of the same function ────────────────────────────────────────────
  #
  # The two scenarios above grade a PACKAGE SELECTOR. The ones below grade a stored CREATIVE
  # through the same function, on the create path's pre-adapter gate -- one of the four
  # gates the satisfaction function replaced. A creative seeded by canonical kind asserts
  # NO legacy reference, which is precisely the input those four gates disagreed about: the
  # create gate skipped it, the update and assignment gates refused it, and NONE of them
  # looked at format_options[] at all. So before this change a canonical creative was
  # accepted onto any product whatever its closed set said.
  #
  # A creative states no canonical params -- the stored identity is a kind and an option --
  # and its dimensions live on its ASSETS. So the first two scenarios separate the CLOSED-SET
  # step: a kind the product does not publish is UNSUPPORTED_FEATURE before any asset is
  # read. A creative whose kind IS published is then graded on its manifest against the
  # declaration it selects (creative-manifests.mdx § Validation), which is the manifest
  # section further down; the third scenario here is the first of those refusals.

  @T-UC-FORMAT-SATISFACTION-creative-accepted @format_satisfaction @requires_db
  Scenario: a creative whose canonical kind the product publishes is accepted
    Given the seller declares a canonical format option "fixed_300x250"
    And the buyer assigns a creative identified by canonical kind "image" with its media asset keyed "image_main"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy

  @T-UC-FORMAT-SATISFACTION-creative-refused @format_satisfaction @requires_db
  Scenario: a creative whose canonical kind the product does not publish is refused
    Given the seller declares a canonical format option "fixed_300x250"
    And the buyer assigns a creative identified by canonical kind "video_hosted" with its media asset keyed "image_main"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the packages field

    # The pair is what makes either one informative. Both creatives are seeded the same way
    # and differ ONLY in the canonical kind they carry, against the SAME image-only product
    # -- so a gate that refused every canonical creative would redden the row above and a
    # gate that refused none would redden this one. Measured before the change: both were
    # accepted.
    #
    # The asset key is stated on both rows and is the SAME on both, which is what keeps the
    # kind the only difference. On this row it is also inert: the closed-set refusal is
    # raised before the asset gate's collected errors are answered, so this creative is
    # refused for its kind whatever its assets are keyed.

  # ── Whether the seller can READ a canonical creative's assets at all ──────────────────
  #
  # The accept row above is only reachable if the seller can find the creative's media
  # asset, and for a canonical creative that is a separate question from the closed set.
  # core/product-format-declaration.json's params.slots[] carries
  # {asset_group_id, asset_type, required}, and each canonical schema states its own list as
  # the slots DEFAULT -- formats/canonical/image.json defaults to image_main (image) plus
  # headline/body_text/primary_text/cta (text) and landing_page_url (url). So the
  # declaration ALREADY SAYS what a canonical creative's assets are called.
  #
  # WHAT WAS WRONG BEFORE. extract_media_url_and_dimensions found a creative's media asset
  # either by asking the LEGACY format spec for its asset_id, or by matching a hardcoded
  # 9-member MEDIA_ASSET_FALLBACK_IDS allowlist of naming conventions. A creative identified
  # by its canonical kind asserts no legacy reference, so no spec resolves and the allowlist
  # was the only path left -- meaning a canonical creative's assets were readable only by
  # COINCIDENCE of asset-id naming. Measured: the same creative keyed image_main (what the
  # pin declares) was refused "missing required URL field in assets", and keyed banner_image
  # (what this seller happened to guess) was accepted. That is salesagent-ol033.16, and it is
  # why the accept row above used to seed banner_image with a comment apologising for it.
  #
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/image.json pointer=/properties/slots/default

  @T-UC-FORMAT-SATISFACTION-creative-asset-unnamed @format_satisfaction @requires_db
  Scenario: a canonical creative whose media asset no slot names is refused
    Given the seller declares a canonical format option "fixed_300x250"
    And the buyer assigns a creative identified by canonical kind "image" with its media asset keyed "banner"
    When the Buyer Agent sends the create_media_buy request
    Then the creative is refused CREATIVE_VALIDATION_FAILED_GENERIC naming the packages field
    And the refusal names "assets.image_main" missing where "image" is accepted

    # THE CODE MOVED, from VALIDATION_ERROR to CREATIVE_VALIDATION_FAILED_GENERIC, and the
    # condition did not. creative-manifests.mdx § Validation step 2, "Confirm all required
    # slots are present", is now asked of the declaration the creative selects, and
    # image_main is the image kind's required slot (formats/canonical/image.json's slots
    # default). The pinned enum has a creative code for a buyer-correctable creative failure
    # that no more specific code names, and VALIDATION_ERROR is the generic business-rule
    # code it is more specific than. The asset gate that answered VALIDATION_ERROR still
    # runs; the manifest refusal is raised first because it is the creative's own failure.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/creative-manifests.mdx section="Validation"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json

    # THE CONTROL FOR THE ACCEPT ROW, and the reason that row is not satisfied by a gate
    # that simply stopped checking. This creative and the accepted one are the same kind,
    # the same asset, the same product, and differ in ONE character sequence: the key its
    # media asset is stored under. `banner` is CreativeFactory's own default and is named by
    # nothing -- not a slot of any of the sixteen canonical kinds, and not a member of the
    # fallback allowlist either -- so the seller genuinely cannot find a URL or dimensions
    # for it, and refusing is correct. A fix that made the gate read the declaration would
    # redden the accept row if it failed and redden this one if it over-reached.
    #
    # The CODE differs from its sibling refusal above, and that is graded rather than
    # incidental: a creative outside the published closed set is UNSUPPORTED_FEATURE, while
    # a creative the product does publish whose manifest does not fill the declaration is a
    # creative code. The two have different buyer recoveries -- name another format, versus
    # re-sync this creative -- so a gate that collapsed them would send the buyer to the
    # wrong fix.
    #
    # NOT GRADED HERE, and filed rather than assumed: a canonical audio creative carries no
    # dimensions at all and this same gate demands them of every creative
    # (salesagent-bcqev), and a canonical creative's clickthrough slot landing_page_url is
    # in no allowlist so the click URL is dropped silently (salesagent-7xi64). Both block
    # stage 6.

  @T-UC-FORMAT-SATISFACTION-schema-invalid @format_satisfaction @requires_db
  Scenario: an atomically-incomplete dimension is a SCHEMA refusal, not a satisfaction one
    Given the seller declares a canonical format option "fixed_300x250"
    And the buyer's package selects "width_without_height"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused INVALID_REQUEST

    # The storyboard's reject_image_width_without_height, and it is in THIS phase, so it is
    # accounted for here rather than assumed to be someone else's. Its negative_path is
    # `schema_invalid` while every other refusal in the phase is `payload_well_formed`, and
    # the error bucket differs with it: INVALID_REQUEST, not UNSUPPORTED_FEATURE.
    #
    # MEASURED, not assumed: the pinned PackageRequest refuses `params: {width: 300}` with
    # no height on construction -- "violates a constraint its schema states outside its
    # properties (media-buy/package-request.json, root conjunct 3) over format_kind, height,
    # params" -- so fixed-size dimension atomicity is enforced by the REQUEST BOUNDARY and
    # the selector never reaches the satisfaction function. The owner is the pinned model,
    # which is the right owner; what needed grading is that the two buckets stay apart,
    # because a seller that let this through to satisfaction would answer the buyer
    # UNSUPPORTED_FEATURE ("this product cannot do that") for a request that is simply
    # malformed ("you did not finish the dimensions"). Those are different recoveries.

  # ── The same function on the UPDATE and ASSIGNMENT paths ──────────────────────────────
  #
  # The bead's grading clause names three paths -- "BDD scenarios run against CREATE, UPDATE
  # and ASSIGNMENT paths" -- and everything above dispatches create_media_buy. These grade
  # the other two. All four gates call ONE function, so the canonical lane is live on all of
  # them by construction; what was ungraded is whether each path actually REACHES it and
  # answers on the wire with the same code.
  #
  # This is also where the skip-vs-refuse divergence lived: a creative carrying no legacy
  # assertion was SKIPPED by the create gate and REFUSED by these two, so one buyer input
  # had two answers depending on which tool received it. The accept rows here and the
  # creative accept row above are the same input through three tools, which is what makes
  # the convergence observable rather than asserted.

  @T-UC-FORMAT-SATISFACTION-update-accepted @format_satisfaction @requires_db
  Scenario: update_media_buy accepts a creative whose canonical kind the product publishes
    Given the seller declares a canonical format option "fixed_300x250"
    And the buyer already holds a media buy for that product
    And a creative identified by canonical kind "image" exists
    When the Buyer Agent assigns that creative through update_media_buy
    Then the update is accepted

  @T-UC-FORMAT-SATISFACTION-update-refused @format_satisfaction @requires_db
  Scenario: update_media_buy refuses a creative whose canonical kind the product does not publish
    Given the seller declares a canonical format option "fixed_300x250"
    And the buyer already holds a media buy for that product
    And a creative identified by canonical kind "video_hosted" exists
    When the Buyer Agent assigns that creative through update_media_buy
    Then the buyer is refused UNSUPPORTED_FEATURE naming the "creative_ids" array

    # The field is asserted here too, and it is a DIFFERENT pointer from the create path's:
    # core/error.json wants it to name the array the buyer actually sent, and this path has
    # two of them (creative_ids / creative_assignments). A canonical refusal used to travel
    # with field=None while the legacy refusals on the same path carried one -- the only
    # refusal here that did not say which array it was about.

  @T-UC-FORMAT-SATISFACTION-sync-accepted @format_satisfaction_sync @requires_db
  Scenario: sync_creatives accepts an assignment whose canonical kind the product publishes
    Given a packaged product declaring a canonical image option fixed at 300x250
    And the buyer syncs a creative identified by canonical kind "image"
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error

  @T-UC-FORMAT-SATISFACTION-sync-refused @format_satisfaction_sync @requires_db
  Scenario: sync_creatives refuses an assignment whose canonical kind the product does not publish
    Given a packaged product declaring a canonical image option fixed at 300x250
    And the buyer syncs a creative identified by canonical kind "video_hosted"
    When the Buyer Agent assigns it to that package
    Then the buyer is refused UNSUPPORTED_FEATURE

  @T-UC-FORMAT-SATISFACTION-sync-lenient-canonical @format_satisfaction_sync @requires_db
  Scenario: lenient validation does not soften a closed-set refusal
    Given a packaged product declaring a canonical image option fixed at 300x250
    And the buyer syncs a creative identified by canonical kind "video_hosted"
    And the buyer asks for lenient validation
    When the Buyer Agent assigns it to that package
    Then the buyer is refused UNSUPPORTED_FEATURE

    # THE ASYMMETRY, graded deliberately because it reads like a bug to anyone who meets it
    # without this scenario. ``validation_mode: "lenient"`` means "skip an assignment this
    # product will not take and record the reason", and the LEGACY lane still does exactly
    # that -- the row below proves it. A CANONICAL refusal does not soften, because the
    # product published a closed set and this creative is outside it: there is no partial
    # assignment to fall back to, and core/product-format-declaration.json calls the
    # closed-set rejection "structural, not negotiable". Without these two rows side by
    # side the next person to read the raise site sees one branch ignoring validation_mode
    # and "fixes" it.

  @T-UC-FORMAT-SATISFACTION-sync-lenient-legacy @format_satisfaction_sync @requires_db
  Scenario: lenient validation does soften a deprecated-arm refusal
    Given a packaged product declaring only the deprecated format_ids
    And the buyer syncs a creative identified by format_id "video_1280x720"
    And the buyer asks for lenient validation
    When the Buyer Agent assigns it to that package
    Then the sync succeeds and records an assignment error for that package

  # ── The LIBRARY UPSERT path: a creative whose format resolves to no canonical kind ────
  #
  # Everything else in this module reaches the satisfaction function through a gate that
  # already exists. These two reach the path that has NONE: sync_creatives writing a
  # creative into the library with no assignment at all.
  #
  # MEASURED: `satisfies` is called once in creatives/_assignments.py and ZERO times in
  # creatives/_processing.py, the upsert path. format_satisfaction.py is the only place that
  # refuses kind=None, and it is not on this path -- so a legacy format_id with no canonical
  # projection was STORED with format_kind NULL rather than refused.
  #
  # The user's ruling is "gate at writing, not write if we can't resolve it", so these grade
  # the refusal AND the absence of the row: a wire envelope cannot tell a seller that refused
  # before writing from one that wrote and then errored, and the second leaves the NULL
  # behind for every later reader.
  #
  # THE REFUSAL NEEDS A FORMAT NO STEP OF THE ORDER PROJECTS, and the reference capture has
  # none. registries/v1-canonical-mapping.json (3.2.7) "Resolution order" projects a v1
  # format through (2) its own canonical, (3) a format_id_glob, (4) a structural entry --
  # "Set of asset_type values that must appear in the format's slots (in any order, any
  # count)" -- and only then (6) fails closed. Every one of the 76 captured formats reaches
  # one of those: 69 declare canonical, and the 7 card formats that do not all carry a `url`
  # asset, which the structural `url` entry maps to display_tag. `product_card_standard`
  # used to stand here as "unprojectable"; that was true only of a seller that stopped
  # after step 3. So the refused format is a publisher's own, served by the tenant's peer
  # agent: image and text slots, no canonical, an id no glob names -- no step reaches it.
  #
  # Every row below is SERVED, so none can pass or fail on resolvability over the wire,
  # which is what makes the projection the only variable. `display_300x250_image` projects
  # to `image` through the capture's canonical.
  #
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/registries/v1-canonical-mapping.json

  @T-UC-FORMAT-SATISFACTION-upsert-unprojectable @creative_library_upsert @requires_db
  Scenario: a creative whose format resolves to no canonical kind is refused, not stored
    Given a packaged product declaring only the deprecated format_ids
    And the seller's creative agent serves "publisher_bespoke_takeover_unit" with assets "image, text" declaring no canonical kind
    And the buyer syncs a creative identified by that served format
    When the Buyer Agent syncs it into the library
    Then the buyer is refused UNSUPPORTED_FEATURE and nothing is stored

  # STEP 4, graded on its own: a publisher format that declares no canonical and that no
  # glob names, whose one slot is a hosted video. The registry's structural entry
  # {"asset_types": ["video"]} -> video_hosted is the only step that reaches it, so a seller
  # whose order stops at the glob refuses it and the row reddens.
  @T-UC-FORMAT-SATISFACTION-upsert-structural @creative_library_upsert @requires_db
  Scenario: a creative whose format only a registry structural entry matches is stored with that family's kind
    Given a packaged product declaring only the deprecated format_ids
    And the seller's creative agent serves "publisher_hosted_spot" with assets "video" declaring no canonical kind
    And the buyer syncs a creative identified by that served format
    When the Buyer Agent syncs it into the library
    Then the creative is stored carrying canonical kind "video_hosted"

  # STEP 4 READS THE SLOT'S VERSION, NOT ONLY ITS ASSET TYPE. The registry matches a
  # structural entry "against the format's slot shape, asset types, and version
  # constraints", and its one `vast` entry for VAST 4 is {"asset_types": ["vast"],
  # "vast_versions": [">=4.0"]}. A publisher format with one VAST slot stating
  # vast_version 4.2 and no canonical therefore projects to video_vast. A seller that
  # matches the asset types alone finds no unversioned `vast` entry and refuses the
  # creative, which reddens this row.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/registries/v1-canonical-mapping.json
  @T-UC-FORMAT-SATISFACTION-upsert-structural-versioned @creative_library_upsert @requires_db
  Scenario: a creative whose format only a versioned structural entry matches is stored with that family's kind
    Given a packaged product declaring only the deprecated format_ids
    And the seller's creative agent serves "publisher_preroll" with one "vast" slot requiring version "4.2" and no canonical kind
    And the buyer syncs a creative identified by that served format
    When the Buyer Agent syncs it into the library
    Then the creative is stored carrying canonical kind "video_vast"

  # STEP 2 AT A PEER WHOSE ID A GLOB NAMES. The publisher calls its HTML5 banner
  # `display_300x250`, an id the registry's literal glob maps to image at the reference
  # agent, and its own declaration says html5. Step 2 -- "if the v1 format declaration
  # carries an explicit canonical field, use it" -- is what projects a peer's id, so the
  # stored kind is html5. A seller consulting the glob first stores image, and a seller
  # ignoring the declaration finds no structural entry for an `html` slot and refuses it:
  # either reddens this row.
  @T-UC-FORMAT-SATISFACTION-upsert-declared-outranks-glob @creative_library_upsert @requires_db
  Scenario: a format's own canonical projection outranks a registry glob naming its id
    Given a packaged product declaring only the deprecated format_ids
    And the seller's creative agent serves "display_300x250" with assets "html" declaring canonical kind "html5"
    And the buyer syncs a creative identified by that served format
    When the Buyer Agent syncs it into the library
    Then the creative is stored carrying canonical kind "html5"

  # A PEER'S ID IS NOT LOOKED UP IN THE GLOBS, EVEN WHEN ITS DECLARATION CARRIES NO
  # CANONICAL. The publisher serves `video_30s` with a single `url` slot and no canonical.
  # At the reference agent the registry's literal glob maps that id to video_hosted at
  # duration_ms_exact 30000 (step 3). The globs are written in the reference agent's id
  # namespace, and this seller maps a legacy id by glob only at the reference agent's origin
  # (the owner's ruling, 2026-10-30): at the tenant's peer the id is the peer's own, step 3
  # does not apply, and the registry's structural `url` entry -- "the PRIMARY fallback for
  # v1 wire traffic", which "catches custom v1 formats ... without enumerating every
  # possible v1 name" -- maps the slot shape to display_tag (step 4). A seller applying the
  # glob at any agent, as the SDK does, stores video_hosted; a seller that drops step 4 at a
  # peer along with step 3 refuses the creative. Either reddens the row. The pin does not
  # say which agent's ids step 3 applies to: adcontextprotocol/adcp#8626 asks for that to be
  # written down.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/registries/v1-canonical-mapping.json pointer=/description
  @T-UC-FORMAT-SATISFACTION-upsert-peer-id-skips-glob @creative_library_upsert @requires_db
  Scenario: a peer agent's legacy id is projected by its slots, not by a registry glob naming the id
    Given a packaged product declaring only the deprecated format_ids
    And the seller's creative agent serves "video_30s" with assets "url" declaring no canonical kind
    And the buyer syncs a creative identified by that served format
    When the Buyer Agent syncs it into the library
    Then the creative is stored carrying canonical kind "display_tag"

  @T-UC-FORMAT-SATISFACTION-upsert-projectable @creative_library_upsert @requires_db
  Scenario: a creative whose format does resolve is stored with its canonical kind
    Given a packaged product declaring only the deprecated format_ids
    And the buyer syncs a creative identified by format_id "display_300x250_image"
    When the Buyer Agent syncs it into the library
    Then the creative is stored carrying canonical kind "image"

    # THE CONTROL, and without it the row above is satisfied by a seller that refuses every
    # library upsert. Same tool, same When, same product, an id the same agent serves -- and
    # it differs only in having a registry projection. It also pins the POSITIVE half of the
    # write gate: the kind actually reaches the column, which is the thing a NOT NULL
    # constraint alone would not show (a seller could satisfy NOT NULL by writing a wrong
    # kind, which is what the fire-probe for this change perturbs).

  # THE AGENT'S CATALOGUE IS NOT THE SOURCE OF TRUTH. docs/creative/template-format-ids.mdx
  # (3.2.7) § Compatibility: "Normalize them to canonical declarations immediately ... Do not
  # ... treat a deprecated list_creative_formats response as a source of truth". The standard
  # creative agent's catalogue does not list display_468x60_image, and the canonical mapping
  # registry -- "authoritative for v1 -> v2 projection" -- upgrades it to display_image
  # 468x60, kind image. So the creative is a creative in a real format and is stored with that
  # kind; a seller asking the catalogue refuses it REFERENCE_NOT_FOUND on every row.
  #
  # THE SPELLINGS grade core/format-id.json's agent_url MUST ("canonicalize agent_url per the
  # AdCP URL canonicalization rules before treating two formats as the same"): the registry's
  # upgrade applies only in the standard agent's namespace, and docs/reference/
  # url-canonicalization.mdx (3.2.7) makes the trailing root-label dot (step 2) and the
  # removable dot segment (step 5) the same agent. The upper-cased host is the control: the
  # SDK's own partial comparison already equates it, so it separates the catalogue rule from
  # the canonicalization rule.
  #
  # Which agent's ids the registry projects is a SELLER CHOICE the pin leaves open: the
  # registry's globs carry no agent_url, and this seller projects them only for references
  # naming the standard creative agent, by origin (the owner's ruling: an agent's identity is
  # its origin, and legacy v1 ids map to a kind only at the reference agent's origin). The
  # mounted-path row grades that choice: the standard agent served at /mcp is the same origin,
  # and a seller that recognized the agent by origin but handed the SDK's size upgrade the
  # full URL (whose comparison keeps the path) found no projection and refused it.
  # @source repo=adcp ref=v3.2.7 path=docs/creative/template-format-ids.mdx
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/registries/v1-canonical-mapping.json pointer=/description
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format-id.json pointer=/properties/agent_url/description
  # @source repo=adcp ref=v3.2.7 path=docs/reference/url-canonicalization.mdx
  @T-UC-FORMAT-SATISFACTION-upsert-registry-projects-unlisted @creative_library_upsert @requires_db
  Scenario Outline: a legacy id the creative agent does not list is stored with the kind the mapping registry gives it - <variant>
    Given a packaged product declaring only the deprecated format_ids
    And the buyer syncs a creative identified by format_id "display_468x60_image" at the standard creative agent spelled with <variant>
    When the Buyer Agent syncs it into the library
    Then the creative is stored carrying canonical kind "image"

    Examples:
      | variant                   |
      | an upper-cased host       |
      | a trailing root-label dot |
      | a removable dot segment   |
      | a mounted path            |

  # THE SAME RULE ON THE CREATE PATH. create_media_buy re-checks every library creative a
  # package names before the ad server is called, and asked the agent's deprecated catalogue
  # whether the creative's legacy id is a format. The library creative here is on
  # display_468x60_image at the standard creative agent: the catalogue does not list it and
  # the canonical mapping registry projects it to an image at 468x60 (template-format-ids.mdx
  # § Compatibility: "Do not ... treat a deprecated list_creative_formats response as a source
  # of truth"). The product sells exactly that id, so nothing but the catalogue question can
  # refuse the buy; a seller that asks it refuses VALIDATION_ERROR with "unknown format".
  # @source repo=adcp ref=v3.2.7 path=docs/creative/template-format-ids.mdx
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/registries/v1-canonical-mapping.json pointer=/description
  @T-UC-FORMAT-SATISFACTION-create-registry-projects-unlisted @format_satisfaction @requires_db
  Scenario: create_media_buy accepts a library creative on a legacy id the creative agent does not list
    Given the seller publishes only the deprecated format "display_468x60_image" at the standard creative agent
    And the buyer assigns a stored creative identified by format_id "display_468x60_image" sized 468x60 at the standard creative agent
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy

  # A FORMAT IS ITS AGENT'S. core/format-id.json identifies a format by its declaring agent
  # and its id, and requires canonicalizing agent_url before two references are treated as the
  # same. The seller's creative agent lists a format under ANOTHER agent's URL, and the buyer
  # names that id at the seller's agent: no format the seller's agent publishes is named, so
  # the reference does not resolve -- REFERENCE_NOT_FOUND, enums/error-code.json's "referenced
  # identifier ... that does not exist" -- and nothing is stored. A seller that matched the
  # agent's catalogue on the id alone took the entry as the named format and refused the
  # creative UNSUPPORTED_FEATURE for a projection it should never have attempted.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format-id.json pointer=/properties/agent_url/description
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/REFERENCE_NOT_FOUND
  @T-UC-FORMAT-SATISFACTION-upsert-relisted-format @creative_library_upsert @requires_db
  Scenario: a format the seller's creative agent lists under another agent's URL is not that agent's format
    Given a packaged product declaring only the deprecated format_ids
    And the seller's creative agent lists "publisher_relisted_banner" with assets "image" under another agent's URL
    And the buyer syncs a creative naming that format at the seller's creative agent
    When the Buyer Agent syncs it into the library
    Then the creative is reported failed with REFERENCE_NOT_FOUND and nothing is stored

    # The OTHER half of the asymmetry, and the control for the row above: same tool, same
    # lenient mode, same "this product will not take this creative" condition -- and the
    # legacy lane skips with a recorded error instead of refusing. Both rows are needed:
    # either one alone is satisfied by a gate that treats every mode the same.

  @T-UC-FORMAT-SATISFACTION-sync-sized-conforming @format_satisfaction_sync @requires_db
  Scenario: a creative sized as the deprecated arm requires is accepted
    Given a packaged product selling only a deprecated format sized 300x250
    And the buyer syncs a creative identified by format_id "display_image" sized 300x250
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error

  @T-UC-FORMAT-SATISFACTION-sync-sized-mismatched @format_satisfaction_sync @requires_db
  Scenario: a creative sized differently from the deprecated arm is refused
    Given a packaged product selling only a deprecated format sized 300x250
    And the buyer syncs a creative identified by format_id "display_image" sized 728x90
    When the Buyer Agent assigns it to that package
    Then the creative is refused CREATIVE_SIZE_MISMATCH
    And the refusal names "format_id.width" stating "728" where "300" is accepted
    And the refusal names "format_id.height" stating "90" where "250" is accepted

    # THE PAIR IS ONE INSTRUMENT and neither row works alone: the accept row alone is
    # satisfied by a seller that takes everything, which is the defect, and the refuse row
    # alone is satisfied by a seller that takes nothing. They differ in exactly the
    # creative's declared dimensions, so what they grade is the comparison and not the id.
    #
    # core/product-format-declaration.json states it under its own "Format matching vs
    # satisfaction (normative)" heading: "when this declaration specifies fixed constraints
    # such as width, height ... a buyer request or creative manifest MUST declare and
    # satisfy those constraints", and it forbids the comparison that was being made --
    # "do not exact-match raw (agent_url, id) pairs". The deprecated arm's accepted set was
    # built from template keys, so a product restricting to 300x250 and one restricting to
    # nothing were the same set and a 728x90 creative was accepted (salesagent-4764o).
    #
    # A TEMPLATE id carrying parameters, because a name-encoded one cannot express this:
    # upgrade_legacy_format_id rewrites the parameters from the name, so
    # display_300x250_image sized 728x90 becomes display_image at 300x250 and matches.
    #
    # THE CODE is the one the canonical lane gives the same condition (the fixed_300x250 /
    # image_728x90 row of @T-UC-FORMAT-SATISFACTION-manifest-sync-refused).
    # enums/error-code.json, CREATIVE_SIZE_MISMATCH: "The submitted creative dimensions do
    # not match any size accepted by the selected packages". The product takes the
    # creative's format, display_image, at another size. A format it does not take at all
    # is still VALIDATION_ERROR (@T-UC-FORMAT-SATISFACTION-sync-other-agent-path).
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/CREATIVE_SIZE_MISMATCH

  @T-UC-FORMAT-SATISFACTION-sync-density-mismatched @format_satisfaction_sync @requires_db
  Scenario: a creative at the wrong asset density is refused
    Given a packaged product selling only a deprecated format sized 300x250 at 2x density
    And the buyer syncs a creative identified by format_id "display_image" sized 300x250 at 1x density
    When the Buyer Agent assigns it to that package
    Then the creative is refused CREATIVE_SIZE_MISMATCH
    And the refusal names "format_id.pixel_ratio" stating "1" where "2" is accepted

  @T-UC-FORMAT-SATISFACTION-sync-density-silent @format_satisfaction_sync @requires_db
  Scenario: a creative silent about asset density is refused by a product that fixes one
    Given a packaged product selling only a deprecated format sized 300x250 at 2x density
    And the buyer syncs a creative identified by format_id "display_image" sized 300x250
    When the Buyer Agent assigns it to that package
    Then the creative is refused CREATIVE_SIZE_MISMATCH
    And the refusal names "format_id.pixel_ratio" missing where "2" is accepted

    # THE FOURTH PARAMETER, and it needs BOTH rows because the two ways to fail it are
    # different: a creative CONTRADICTING the density, and a creative SILENT about it. The
    # pin requires the claim to "declare and satisfy" a fixed constraint, so silence fails
    # it just as a wrong value does -- and core/format-id.json calls pixel_ratio "Required
    # intrinsic-pixel density for a parameterized visual format".
    #
    # Silence is refused because the field's own text gives it a value: "Omit for the
    # backward-compatible 1x variant", and 1x is not the 2x the product fixes. adcp's
    # format_is_supported reads the field that way and answers both rows; an earlier SDK
    # compared only ("width", "height", "duration_ms") and accepted both, which is what these
    # rows were written to catch.
    #
    # A density is a size here, as on the canonical lane (the plain_300x250 /
    # image_600x500_2x row of @T-UC-FORMAT-SATISFACTION-manifest-sync-refused is
    # CREATIVE_SIZE_MISMATCH too): core/format-id.json's {width: 300, height: 250,
    # pixel_ratio: 2} "identifies a 300x250 logical render supplied by a 600x500 image", so
    # a 1x creative is a 300x250 image where the product takes a 600x500 one.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format-id.json pointer=/properties/pixel_ratio

    # wrong_publisher_domain is the second half of formats.mdx's fourth rule. It carries
    # the SAME format_option_id the product publishes under a DIFFERENT publisher_domain,
    # which the pin makes a different option outright: the two arms of
    # core/format-option-ref.json declare publisher_domain required on one and forbidden on
    # the other, so the domain's presence is the discriminator and matching the id alone
    # would answer a publisher-catalogue reference with a product-local option.
    #
    # THE LAST TWO ROWS ARE THE ORDERING OBLIGATION, and they are the reason every route is
    # resolved rather than the first that succeeds. Both carry a format_option_ref that
    # RESOLVES, so a seller that stopped at the first satisfied route would accept both.
    # core/product-format-declaration.json's step-15 step states it: "Resolution failure is
    # reported before selector equivalence and must not be mislabeled
    # CONFLICTING_SELECTORS" -- and step 13 says "canonical precedence MUST NOT hide the
    # dimensional conflict", so precedence is not a short circuit. The `arm` column is what
    # grades it: the refusal must name format_ids / format_kind, the route that actually
    # failed, and NOT the format_option_refs that succeeded. Their mirrors in the accept
    # outline (option_plus_projectable_legacy, direct_plus_legacy) are the same two shapes
    # with the second route RESOLVING, so neither outline can pass by refusing or by
    # accepting everything that carries two routes.

  # ── What the seller EMITS and RECORDS once the selector is accepted ───────────────────
  #
  # Everything above grades a refusal or an acceptance. These grade what an acceptance
  # LEAVES BEHIND, which is a separate obligation and an unobserved one:
  # core/package.json calls formats_to_provide the package's "immutable canonical creative
  # contracts ... Each entry is a PackageFormatSnapshot of the selected effective Product
  # format declaration", and requires that "the live Product is never substituted for the
  # package snapshot". The seller emitted the field on no package at all, and emitted its
  # deprecated projection format_ids_to_provide as an ECHO of the buyer's own request arm.
  #
  # BOTH HALVES ARE ASSERTED — the response AND the stored row — and the bead says why: a
  # response-only assertion is echo-satisfiable, which is precisely how
  # T-UC-026-partition-paused passed while the money bug was live. A seller that assembled
  # the snapshot for the reply and stored nothing would pass a response-only scenario and
  # then answer a later read from the live product, which is the substitution the pin names.
  #
  # NEITHER HALF IS GRADED BY THE STORYBOARD: grep over dist/compliance/3.2.3 for
  # formats_to_provide, formats_pending, format_ids_to_provide and format_ids_pending
  # returns ZERO occurrences in any scenario. So these scenarios are the only grading these
  # obligations have, which is the reason they are mandatory rather than nice to have.

  @T-UC-FORMAT-SATISFACTION-snapshot-emitted @format_satisfaction @requires_db
  Scenario: the selected declaration is emitted as a snapshot and persisted on the package
    Given the seller declares a canonical format option "fixed_300x250"
    And the buyer's package selects "product_option_ref"
    When the Buyer Agent sends the create_media_buy request
    Then the package snapshots the "satisfaction_image_mrec" declaration fixed at 300x250
    And the stored package snapshot names the same declaration
    And the package's deprecated format_ids_to_provide names "display_300x250"

    # THE BUYER SENT NO format_ids AT ALL, which is what makes the last Then a derivation
    # rather than an echo: display_300x250 reaches the response only because the SELECTED
    # declaration asserts it in v1_format_ref. Before this change the field was
    # `format_ids_to_provide=package.format_ids`, so this request received nothing on a
    # field whose absence the pin gives a defined meaning to (core/package.json on
    # format_ids_pending: "Absence means legacy readiness was not reported and MUST NOT be
    # interpreted as full coverage").
    #
    # v1_format_ref is the ONLY legal source. registries/v1-canonical-mapping.json:
    # "v2 → v1 projection MUST rely on v1_format_ref on the v2 ProductFormatDeclaration —
    # sellers assert the v1 pairing explicitly. SDKs MUST NOT synthesize a v1 format_id
    # from the registry by inverting structural matches."

  # THE RESOLUTION ORDER, which a single-route row cannot show. registries/v1-canonical-mapping.json
  # (3.2.7) "Resolution order" is normative and step 1 is "Highest priority": "if any v2
  # ProductFormatDeclaration on the same product carries v1_format_ref pointing at this v1
  # format_id, use that v2 declaration". Step 3 is the registry glob. The product publishes two
  # 300x250 images, the first asserting a house id and the second asserting display_300x250_image.
  # The package selects display_300x250_image. Step 1 selects the second declaration alone. The
  # registry's projection (an image at 300x250) narrows to BOTH, so a seller that skipped step 1
  # or ran the projection first selects both, records the first of the selection, and
  # snapshots the house option. The Then reads the one snapshot on the wire and in the row, so
  # either error fails it.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/registries/v1-canonical-mapping.json pointer=/description
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/package.json pointer=/properties/formats_to_provide
  @T-UC-FORMAT-SATISFACTION-legacy-order-step-1-first @format_satisfaction @requires_db
  Scenario: a legacy selector a declaration's v1_format_ref names selects that declaration, not every one its projection narrows
    Given the seller declares a canonical format option "house_mrec_then_asserted_mrec"
    And the buyer's package selects "legacy_display_300x250_image"
    When the Buyer Agent sends the create_media_buy request
    Then the package snapshots the "satisfaction_image_mrec" declaration fixed at 300x250
    And the stored package snapshot names the same declaration

  @T-UC-FORMAT-SATISFACTION-snapshot-no-legacy-ref @format_satisfaction @requires_db
  Scenario: a declaration asserting no legacy reference projects to no deprecated array
    Given the seller declares a canonical format option "fixed_300x250_no_ref"
    And the buyer's package selects "product_option_ref"
    When the Buyer Agent sends the create_media_buy request
    Then the package snapshots the "satisfaction_image_mrec" declaration fixed at 300x250
    And the package carries no deprecated format_ids_to_provide

    # THE PAIRED POSITIVE FOR THE ABSENCE, and it is the first Then of this scenario rather
    # than a separate row: an assertion that format_ids_to_provide is ABSENT passes for free
    # under any perturbation that drops the whole package, so it is only informative beside
    # a positive assertion on the same response. The two scenarios differ in ONE thing — the
    # declaration asserts a v1_format_ref or it does not — so a seller that synthesized a
    # legacy reference from the registry reddens here, and a seller that emitted none at all
    # reddens the row above.

  # ── THE MANUAL-APPROVAL PATH, which none of the above reaches ─────────────────────────
  #
  # Every scenario above runs on the AUTO path, and that is not a choice any of them made:
  # MediaBuyCreateEnv seeds human_review_required=False deliberately (its own comment
  # explains why — so the live e2e server's gate state matches the other transports), and
  # its mocked adapter sets manual_approval_operations=[]. So no satisfaction question in
  # this feature has ever been asked with that tenant column flipped.
  #
  # MEASURED on d5c1b3507, through the real _create_media_buy_impl: it is not asked
  # because production cannot answer it. One product declaring one image option fixed at
  # 300x250, one package selecting a bare `image`, and the ONLY difference the tenant
  # column:
  #
  #     human_review_required=False  ->  REFUSED  (UNSUPPORTED_FEATURE)
  #     human_review_required=True   ->  ACCEPTED, submitted
  #
  # The cause is ordering and it is structural, not conditional: the manual-approval branch
  # opens at media_buy_create.py:2828 and its only non-exceptional exit is the return at
  # :3147, while the canonical selector gate is at :3337 — 190 lines later. A second early
  # return at :3267 (product-level configuration approval) bypasses it identically. So
  # everything the gate owns is skipped on this path: the closed set, the params rules, M2,
  # the resolution/selection split and CONFLICTING_SELECTORS.
  #
  # core/product-format-declaration.json leaves no room for an approval-mode carve-out —
  # format_options[] "IS the closed set of accepted formats; anything outside the list is
  # rejected at create_media_buy REGARDLESS OF PREFERENCE" — and
  # media-buy/package-request.json states its four receiver steps with no mode condition
  # either.
  #
  # The ordering has since been fixed: the selector gate (_resolve_package_selectors) runs
  # ABOVE both approval branches, so these scenarios grade that it stays there. They were
  # landed red first, as the instrument for salesagent-gz86t (the gate did not run),
  # salesagent-1dcfo (so no snapshot was recorded) and salesagent-zoo82 (so C8 had nothing
  # to read).
  #
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/description
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/package.json pointer=/properties/formats_to_provide/description

  @T-UC-FORMAT-SATISFACTION-approval-gate-refuses @format_satisfaction @requires_db
  Scenario: a human-review tenant still refuses a selector the product does not accept
    Given the seller declares a canonical format option "fixed_300x250"
    And the tenant requires manual approval
    And the buyer's package selects "bare_image_kind"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the "format_kind" selector

  @T-UC-FORMAT-SATISFACTION-approval-gate-accepts @format_satisfaction @requires_db
  Scenario: a human-review tenant parks a buy whose selector the product does accept
    Given the seller declares a canonical format option "fixed_300x250"
    And the tenant requires manual approval
    And the buyer's package selects "product_option_ref"
    When the Buyer Agent sends the create_media_buy request
    Then the buy is parked for a human decision

    # THE PAIR, and it is the pair that makes the refusal informative rather than a
    # restatement of "manual approval refuses things". Same product, same tenant column,
    # same tool — the selectors differ in exactly whether the product accepts them, which
    # is the dimension being judged. A seller that refused every selector on the approval
    # path would redden this row; one that accepts every selector reddens the row above.

  @T-UC-FORMAT-SATISFACTION-approval-snapshot-recorded @format_satisfaction @requires_db
  Scenario: a parked buy records the declaration its selector selected
    Given the seller declares a canonical format option "fixed_300x250"
    And the tenant requires manual approval
    And the buyer's package selects "product_option_ref"
    When the Buyer Agent sends the create_media_buy request
    Then the parked package snapshots the "satisfaction_image_mrec" declaration fixed at 300x250
    And the parked package's deprecated format_ids_to_provide names "display_300x250"

    # WHY THE PARKED BUY IS THE CASE THAT MATTERS MOST, not an edge of the auto path:
    # core/package.json requires a package to "retain and return this checklist" and says
    # "the live Product is never substituted for the package snapshot". A buy waiting for a
    # human has by construction the LONGEST window between create and execution, so it is
    # where the live product is most likely to have moved on — and measured, it is the one
    # case with no snapshot at all. Its package_config carries the buyer's request arm
    # (`format_ids`) and neither canonical field, so a canonical-only buyer's parked buy
    # records NOTHING about what creative it needs.
    #
    # Read off the stored row rather than the reply, and here that is forced rather than
    # preferred: the submitted envelope carries no media_buy_id at all — correct, and the
    # reason the buy is located by tenant through the repository.

  @T-UC-FORMAT-SATISFACTION-approval-executes-from-snapshot @format_satisfaction @requires_db
  Scenario: approving a parked buy executes the recorded snapshot, not the product as it is now
    Given the seller declares a canonical format option "fixed_300x250"
    And the tenant requires manual approval
    And the buyer's package selects "product_option_ref"
    And the Buyer Agent sent the create_media_buy request
    And the seller then republishes that option fixed at 728x90
    When the publisher approves the parked buy
    Then the ad server is asked for a 300x250 package

    # THE FIXTURE DIFFERS IN THE JUDGED DIMENSION, which is the whole design of this
    # scenario: the product is EDITED between create and approval, so "read the snapshot"
    # and "read the live product" give different answers — 300x250 against 728x90. Without
    # the edit both answers agree and the scenario grades nothing, which is how an approval
    # test passes while reading the live product.
    #
    # The option id is unchanged and only its params move, so this is not a different
    # option being selected: it is the SAME contract the buyer bought, republished with
    # different dimensions. That is exactly what
    # core/package-format-snapshot.json's immutability rule is about — "changing selected
    # format, placement set, execution version, or tracker contract requires a replacement
    # package_id; never mutate this snapshot in place".
    #
    # The observable is what the AD SERVER was asked for, read off the adapter boundary
    # the approval actually calls (_execute_adapter_media_buy_creation ->
    # adapter.create_media_buy), not off a response field. An approval's whole product is
    # the ad-server call; its reply says almost nothing.
    #
    # ONE TRANSPORT NOTE, recorded because it reads like a gap otherwise.
    # execute_approved_media_buy has no registry row and no transport — the admin UI calls
    # it — so this scenario is transport-parametrized on its CREATE half with one identical
    # approval execution. That is still four-transport grading of the half that is a
    # transport, and it is the load-bearing half here: the snapshot is persisted BY the
    # create path, so a transport that persisted a malformed one would be caught. It is not
    # four approval executions, and nothing here pretends otherwise.

  @T-UC-FORMAT-SATISFACTION-approval-unsnapshotted-canonical-only @format_satisfaction @requires_db
  Scenario: approving a parked buy with no recorded snapshot places a canonical-only product on its declarations
    Given the seller declares a canonical format option "fixed_300x250" and no format_ids
    And the tenant requires manual approval
    And the buyer's package selects "no_selector"
    And the Buyer Agent sent the create_media_buy request
    When the publisher approves the parked buy
    Then the approval executes the order
    And the ad server is asked for exactly the format "display_300x250"

    # A package whose request named no selector records no snapshot (media-buy/
    # package-request.json: "omitting both selects all formats supported by the product"),
    # so the approval places it on the product's contract. core/product.json (v3.2.7):
    # "New products MUST author canonical format_options[]"; canonical-formats.mdx producer
    # rules: compatibility format_ids "MUST be derived from them". The product stores no
    # format_ids, so the only source of display_300x250 is the declaration's v1_format_ref:
    # a reader of the product's own format_ids column fails the approval with "no valid
    # formats".

  @T-UC-FORMAT-SATISFACTION-approval-unsnapshotted-profile-linked @format_satisfaction @requires_db
  Scenario: approving a parked buy with no recorded snapshot places a profile-linked product on its profile's contract
    Given the seller links a product declaring "fixed_300x250" to an inventory profile declaring "leaderboard_728x90"
    And the tenant requires manual approval
    And the buyer's package selects "no_selector"
    And the Buyer Agent sent the create_media_buy request
    When the publisher approves the parked buy
    Then the approval executes the order
    And the ad server is asked for exactly the format "display_728x90"

    # A linked inventory profile supplies the product's format contract, as get_products
    # announces it. The product's own arms -- its declaration and its legacy column -- both
    # name display_300x250, so a reader of either asks the ad server for the wrong format;
    # only the profile's declaration names display_728x90.

  @T-UC-FORMAT-SATISFACTION-canonical-only-purchasable @format_satisfaction @requires_db
  Scenario Outline: a product declaring only canonical format options is purchasable - <selector>
    Given the seller declares a canonical format option "fixed_300x250" and no format_ids
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy
    And the package snapshots the "satisfaction_image_mrec" declaration fixed at 300x250
    And the package's deprecated format_ids_to_provide names "display_300x250"

    Examples: canonical selector routes against a product with no deprecated arm
      | selector           |
      | exact_300x250      |
      | product_option_ref |

    # THE SHAPE 3.2 TELLS NEW PRODUCTS TO AUTHOR, graded on the buy path rather than the
    # discovery path. core/product.json's root anyOf has a "Canonical-format product"
    # branch, format_ids is "Deprecated in AdCP 3.2; removed in AdCP 4.0" and is absent
    # from the root `required` list — so a product declaring only format_options is legal,
    # and the sibling BR-UC-GET-PRODUCTS-format-options feature already grades that such a
    # product is ANNOUNCED. Whether it can then be BOUGHT was graded by nothing: every
    # other scenario in this file, and every create scenario elsewhere, seeds a product
    # that still carries the deprecated arm. Discoverable-but-unpurchasable is a coherent
    # failure and it had no detector.
    #
    # NO CREATIVE IS ASSIGNED, deliberately. A creative's claimed format reference is
    # RESOLVED against a live catalog, whereas a product's declaration is compared as an
    # identity — so a creative would add a second, independent reason for this scenario to
    # fail (an id the pinned agent does not serve: salesagent-yfu33) while saying nothing
    # about the product's arms, which is its subject. create_media_buy requires no
    # creative; assigning one is BR-UC-FORMAT-SATISFACTION's own creative scenarios' job.
    #
    # THE THIRD OUTCOME SAYS THE PROJECTION STILL HAPPENS WITH NO ARM TO PROJECT FROM.
    # registries/v1-canonical-mapping.json is normative that v2 -> v1 projection "MUST rely
    # on v1_format_ref on the v2 ProductFormatDeclaration", and here the product declares no
    # format_ids whatever — so display_300x250 reaching the package proves the seller read
    # the selected declaration's assertion, and that a canonical-only product still reports
    # legacy readiness rather than leaving a 3.x peer with a field whose absence the pin
    # gives a defined meaning to (format_ids_pending: "Absence means legacy readiness was
    # not reported and MUST NOT be interpreted as full coverage").
    #
    # IT DOES NOT PROVE MORE THAN THAT, and an earlier draft of this comment claimed it
    # did — that a dual-arm fixture cannot tell a projection sourced from the snapshot from
    # one sourced from the product's own arm, because both name display_300x250. Measured,
    # that ambiguity does not exist: _legacy_formats_to_provide reads the snapshot when
    # there is one and the BUYER'S REQUEST arm when there is not, and the product's
    # format_ids column is not a candidate source at either branch. The sibling scenario
    # above already separates the two real sources by sending no request arm. Left here
    # because the claim is the kind that reads plausibly and would have been inherited.
    #
    # WHICH OUTCOME CARRIES THE GRADING, measured by perturbation rather than assumed.
    # Dropping the product's declarations before the selector is graded does NOT refuse the
    # buy — a selector that selects nothing "imposes no format restriction", so the buy
    # succeeds and the package simply carries no snapshot. So the SNAPSHOT outcome is this
    # scenario's discriminating assertion, and the acceptance outcome is its precondition:
    # it is live (grading every canonical claim as under-specified refuses the buy and fails
    # it) but it cannot catch a declarations-plumbing regression on its own. The pairing is
    # what makes the scenario non-vacuous in both directions: the acceptance outcome alone
    # would pass with no format contract at all, and the snapshot outcome alone has no
    # package to read when the buy is refused.
    #
    # BOTH CANONICAL ROUTES, because they resolve independently — a direct format_kind +
    # params selector and a product-scoped format_option_ref reach the declaration by
    # different code, and one working says nothing about the other. The legacy selector
    # route against a canonical-only product is a different question (it resolves through
    # the declaration's asserted ref, or through the registry's upgrade) and is the outline
    # below.

  # ── Legacy selectors are matched on the canonical shape, not on their spelling ─────────
  #
  # docs/creative/template-format-ids.mdx § Compatibility, at the pin: "Implementations may
  # still parse legacy template IDs for older 3.x peers. Normalize them to canonical
  # declarations immediately and do all matching on the canonical shape." The normative
  # restatement is core/product-format-declaration.json's "Format matching vs satisfaction":
  # "Legacy named formats MUST be normalized to canonical declarations before comparison; do
  # not exact-match raw (agent_url, id) pairs once a format_id has been projected".
  #
  # Each accept row below was REFUSED before salesagent-ol033.20.26: after satisfaction had
  # accepted the package, a second gate compared the request's raw (agent_url, id) keys with
  # the product's deprecated format_ids and refused the difference with VALIDATION_ERROR. A
  # canonical-only product has no deprecated arm, so EVERY legacy selector was a difference.
  # Each outline is paired with a refusal on the same product, so a seller that accepted
  # every legacy selector reddens there.

  @T-UC-FORMAT-SATISFACTION-canonical-only-legacy-selector @format_satisfaction @requires_db
  Scenario Outline: a legacy selector normalizing onto a canonical-only product's declaration is accepted - <selector>
    Given the seller declares a canonical format option "fixed_300x250" and no format_ids
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy
    And the package snapshots the "satisfaction_image_mrec" declaration fixed at 300x250

    Examples: one legacy route by the declaration's own assertion, one by the registry
      | selector                     |
      | legacy_display_300x250       |
      | legacy_display_300x250_image |

    # display_300x250 is the declaration's own v1_format_ref, so it resolves by lookup;
    # display_300x250_image resolves only through the registry's upgrade. The snapshot Then
    # is what shows the route SELECTED the declaration rather than imposing nothing: a
    # selector that selects nothing leaves the package with no snapshot.

  @T-UC-FORMAT-SATISFACTION-canonical-only-legacy-refused @format_satisfaction @requires_db
  Scenario: a legacy selector normalizing to another size is refused by a canonical-only product
    Given the seller declares a canonical format option "fixed_300x250" and no format_ids
    And the buyer's package selects "legacy_display_728x90"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the "format_ids" selector

  @T-UC-FORMAT-SATISFACTION-legacy-only-normalized @format_satisfaction @requires_db
  Scenario Outline: a legacy-only product accepts a legacy selector of the same canonical shape - <selector>
    Given the seller publishes only a deprecated format sized 300x250
    And the buyer's package selects "<selector>"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy

    Examples:
      | selector                     |
      | legacy_display_300x250       |
      | legacy_display_300x250_image |

    # The product's one deprecated entry is display_300x250 sized 300x250. The first row
    # names it byte for byte and is the control; the second names it only through the
    # registry's upgrade. The deprecated lane grades both as directional satisfaction over
    # upgraded references -- the clause's "normalized to canonical declarations before
    # comparison" -- so they are the same input to it.

  @T-UC-FORMAT-SATISFACTION-legacy-only-normalized-refused @format_satisfaction @requires_db
  Scenario: a legacy-only product refuses a legacy selector normalizing to another size
    Given the seller publishes only a deprecated format sized 300x250
    And the buyer's package selects "legacy_display_728x90"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the "format_ids" selector and the 300x250 it accepts

  # The deprecated lane's half of malformed_agent_display_300x250 in the refusal outline above:
  # the same id the product publishes, at the creative agent's host spelled with two trailing
  # dots, which docs/reference/url-canonicalization step 2 says "MUST be rejected". The
  # comparison fails closed and the lane's own refusal answers, where the canonicalizer's
  # exception used to escape it as INTERNAL_ERROR.
  @T-UC-FORMAT-SATISFACTION-legacy-only-malformed-agent @format_satisfaction @requires_db
  Scenario: a legacy-only product refuses a legacy selector whose agent_url cannot be canonicalized
    Given the seller publishes only a deprecated format sized 300x250
    And the buyer's package selects "malformed_agent_display_300x250"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the "format_ids" selector and the 300x250 it accepts

  # ── The same agent, spelled differently, is the same format ───────────────────────────
  #
  # core/format-id.json, properties.agent_url: "Callers comparing two format-id values MUST
  # canonicalize agent_url per the AdCP URL canonicalization rules before treating two
  # formats as the same." docs/reference/url-canonicalization: "Implementations MUST apply
  # every step and compare the result byte-for-byte." Only the PRODUCT's deprecated entry is
  # respelled; the creative or selector names the agent as every other row here does.
  #
  # Each spelling is one step of the algorithm: step 2 lowercases the host and removes "a
  # single trailing dot" from a DNS hostname, step 5 applies remove_dot_segments to the path.
  # The host-case row is the CONTROL -- the comparison already applied that step. The other
  # two were refused on the assignment path (salesagent-ol033.20.23): the deprecated lane
  # compared through the SDK's own canonicalizer, which applies four of the eight steps and
  # neither of those. The refusal scenarios hold the other side: "canonicalization removes
  # only dot segments, never real path segments", so another path is another agent.

  @T-UC-FORMAT-SATISFACTION-sync-respelled-agent @format_satisfaction_sync @requires_db
  Scenario Outline: a creative is accepted by a deprecated entry naming its agent in another spelling - <variant>
    Given a packaged product selling only a deprecated format sized 300x250 at the creative agent's url with <variant>
    And the buyer syncs a creative identified by format_id "display_image" sized 300x250
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error

    Examples:
      | variant                   |
      | an upper-cased host       |
      | a trailing root-label dot |
      | a removable dot segment   |

  @T-UC-FORMAT-SATISFACTION-sync-other-agent-path @format_satisfaction_sync @requires_db
  Scenario: a creative is refused by a deprecated entry naming another path on its agent's host
    Given a packaged product selling only a deprecated format sized 300x250 at the creative agent's url with a different path
    And the buyer syncs a creative identified by format_id "display_image" sized 300x250
    When the Buyer Agent assigns it to that package
    Then the buyer is refused VALIDATION_ERROR naming the 300x250 it accepts

  @T-UC-FORMAT-SATISFACTION-respelled-agent @format_satisfaction @requires_db
  Scenario Outline: a legacy selector is accepted by a deprecated entry naming its agent in another spelling - <variant>
    Given the seller publishes only a deprecated format sized 300x250 at the creative agent's url with <variant>
    And the buyer's package selects "legacy_display_300x250"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy

    Examples:
      | variant                   |
      | an upper-cased host       |
      | a trailing root-label dot |
      | a removable dot segment   |

  @T-UC-FORMAT-SATISFACTION-other-agent-path @format_satisfaction @requires_db
  Scenario: a legacy selector is refused by a deprecated entry naming another path on its agent's host
    Given the seller publishes only a deprecated format sized 300x250 at the creative agent's url with a different path
    And the buyer's package selects "legacy_display_300x250"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused UNSUPPORTED_FEATURE naming the "format_ids" selector and the 300x250 it accepts

  # THE BUYER'S SIDE OF THE SAME RULE. The two outlines above respell the agent on the
  # PRODUCT's entry; this one respells it on the package selector. display_300x250_image
  # reaches the fixed_300x250 declaration only through the registry's upgrade, which applies
  # in the standard agent's namespace alone, so a seller that recognized the agent by a partial
  # canonicalization skipped the upgrade on the root-label dot and dot-segment rows, found no
  # projected size, and refused the package (core/format-id.json agent_url MUST;
  # docs/reference/url-canonicalization.mdx steps 2 and 5).
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format-id.json pointer=/properties/agent_url/description
  # @source repo=adcp ref=v3.2.7 path=docs/reference/url-canonicalization.mdx
  @T-UC-FORMAT-SATISFACTION-respelled-selector-agent @format_satisfaction @requires_db
  Scenario Outline: a legacy selector naming the creative agent in another spelling is normalized and accepted - <variant>
    Given the seller declares a canonical format option "fixed_300x250"
    And the buyer's package selects format_id "display_300x250_image" at the creative agent's url with <variant>
    When the Buyer Agent sends the create_media_buy request
    Then the buyer receives the media buy

    Examples:
      | variant                   |
      | an upper-cased host       |
      | a trailing root-label dot |
      | a removable dot segment   |

  # ── MANIFEST IDENTITY: which arm a creative names, and what the seller does with it ───
  #
  # docs/creative/creative-manifests.mdx (at the pin) gives a creative ONE identity:
  # "format_kind names the canonical creative shape ... Compound format_id references are
  # deprecated in 3.2. New manifests use the canonical branch", narrowed by
  # format_option_ref "when a product accepts multiple declarations of the same kind".
  # Its § Deprecated compatibility branch adds the ban these first three scenarios grade:
  # a 3.2 implementation "must not ... mix format_id and format_kind in one manifest".
  # core/creative-asset.json states that ban as its root oneOf, each arm carrying a `not`
  # on the other's identifier, and the seller receives creative-asset items in three
  # places: sync_creatives creatives[], and packages[].creatives[] on create_media_buy and
  # on update_media_buy. A request carrying a mixed item is schema-invalid, so it is
  # refused whole with INVALID_REQUEST, and issues[] says which rule (oneOf) and which item
  # (the pointer addresses the buyer's own array, not a model name).
  #
  # The control for all three is the option-ref scenario below them: the SAME tool, a
  # creative carrying format_kind alone (with its option ref), accepted and stored. A
  # seller refusing every canonical creative reddens it; one accepting the mix reddens
  # these.
  #
  # @source repo=adcp ref=v3.2.7 path=docs/creative/creative-manifests.mdx
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/creative-asset.json pointer=/oneOf

  @T-UC-FORMAT-SATISFACTION-manifest-mixed-sync @format_satisfaction_sync @requires_db
  Scenario: sync_creatives refuses a creative naming both format_id and format_kind
    Given the buyer syncs a creative identified by both format_id "display_300x250_image" and canonical kind "image"
    When the Buyer Agent syncs it into the library
    Then the buyer is refused INVALID_REQUEST
    And the response error issues include keyword oneOf for field creatives.0

  @T-UC-FORMAT-SATISFACTION-manifest-null-legacy-sync @format_satisfaction_sync @requires_db
  Scenario: sync_creatives refuses a creative naming format_kind beside a null format_id
    Given the buyer syncs a creative identified by canonical kind "image" beside a null format_id
    When the Buyer Agent syncs it into the library
    Then the buyer is refused INVALID_REQUEST
    And the response error issues include keyword oneOf for field creatives.0

    # REQUIRED COUNTS PRESENCE. core/creative-asset.json's root oneOf arms are
    # {required: [format_id], not: {required: [format_kind]}} and its mirror, and JSON
    # Schema's `required` is met by a present key whatever its value -- so a null format_id
    # still names the legacy identifier, and beside format_kind the item matches neither arm.
    # The row above sends both identifiers valued; this one differs in the legacy value
    # being null, which a seller counting `is not None` accepted.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/creative-asset.json pointer=/oneOf

  @T-UC-FORMAT-SATISFACTION-manifest-mixed-create @format_satisfaction @requires_db
  Scenario: create_media_buy refuses an uploaded creative naming both format_id and format_kind
    Given the seller declares a canonical format option "fixed_300x250"
    And the buyer's package uploads a creative identified by both format_id "display_300x250_image" and canonical kind "image"
    When the Buyer Agent sends the create_media_buy request
    Then the buyer is refused INVALID_REQUEST
    And the response error issues include keyword oneOf for field packages.0.creatives.0

  @T-UC-FORMAT-SATISFACTION-manifest-mixed-update @format_satisfaction @requires_db
  Scenario: update_media_buy refuses an uploaded creative naming both format_id and format_kind
    Given the seller declares a canonical format option "fixed_300x250"
    And the buyer already holds a media buy for that product
    And the buyer's package uploads a creative identified by both format_id "display_300x250_image" and canonical kind "image"
    When the Buyer Agent uploads the package's creatives through update_media_buy
    Then the buyer is refused INVALID_REQUEST
    And the response error issues include keyword oneOf for field packages.0.creatives.0

    # The update row grades WHERE the refusal happens. The SDK's own creative type checks
    # only "at least one" identifier, so a seller that kept it on package updates refuses a
    # mixed item later, inside the per-package loop, after earlier package changes may
    # already have reached the ad server, and with the pointer /creatives/0, which names
    # nothing in an update request. Refused at the boundary, the pointer is the buyer's own
    # packages[0].creatives[0].

  # ── The canonical arm, sent over the wire and stored whole ────────────────────────────
  #
  # The UC-018 round-trip scenarios read a format_option_ref back through list_creatives,
  # but their creatives are SEEDED, so no scenario sent one through sync_creatives. These
  # two do, against the packaged product that publishes "satisfaction_image_mrec". The
  # option ref is product-scoped, and "A product-scoped reference resolves only with the
  # enclosing product/package context" (creative-manifests.mdx § Exact option routing), so
  # the sync ASSIGNS the creative: the assignment is the product context the ref resolves
  # in. The refusal row is the same input naming an option the product does not publish,
  # which § Validation step 5 makes a refusal ("Reject unknown or uncovered selections").
  #
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/creative-asset.json pointer=/properties/format_option_ref

  @T-UC-FORMAT-SATISFACTION-manifest-option-ref-stored @format_satisfaction_sync @requires_db
  Scenario: a creative synced by format_kind with a product option ref is assigned and stored with both
    Given a packaged product declaring a canonical image option fixed at 300x250
    And the buyer syncs a creative whose format_option_ref names product option "satisfaction_image_mrec" of canonical kind "image"
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error
    And the creative is stored carrying canonical kind "image"
    And the creative is stored with its format_option_ref naming product option "satisfaction_image_mrec"

  @T-UC-FORMAT-SATISFACTION-manifest-option-ref-unpublished @format_satisfaction_sync @requires_db
  Scenario: an assignment whose format_option_ref names an option the product does not publish is refused
    Given a packaged product declaring a canonical image option fixed at 300x250
    And the buyer syncs a creative whose format_option_ref names product option "no_such_option" of canonical kind "image"
    When the Buyer Agent assigns it to that package
    Then the buyer is refused UNSUPPORTED_FEATURE

  # ── Review applies to a canonical creative on UPDATE, not only on create ──────────────
  #
  # Approval is seller-specific and applies to every manifest (creative-manifests.mdx
  # § Libraries and trafficking: "A valid manifest can still enter review"). The update
  # path applied the tenant's approval mode only when the item carried format_id, so a
  # creative identified by format_kind kept its old status on re-sync under every mode.
  # Each row seeds the library row in the status the mode must MOVE it away from, so a
  # seller that leaves the status alone reddens both rows.
  #
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/sync-creatives-response.json pointer=/oneOf/0/properties/creatives/items/properties/status

  @T-UC-FORMAT-SATISFACTION-manifest-update-review @format_satisfaction_sync @requires_db
  Scenario Outline: re-syncing a creative identified by format_kind applies the approval mode — <mode>
    Given the tenant has approval_mode "<mode>"
    And a creative identified by canonical kind "image" is already in the library as "<prior>"
    And the buyer syncs a creative identified by canonical kind "image"
    When the Buyer Agent syncs it into the library
    Then the per-creative result should carry advisory status "<status>"

    Examples:
      | mode          | prior          | status         |
      | auto-approve  | pending_review | approved       |
      | require-human | approved       | pending_review |

  # ── format_option_ref on the deprecated arm ───────────────────────────────────────────
  #
  # core/creative-asset.json declares format_option_ref at the item's top level, beside the
  # root oneOf on the identifier, and neither arm forbids it: "Structured format option
  # reference matching one of the target product's format_options[] declarations". So a v1
  # item naming a format_id may route to an exact option too. The seller used to read the
  # ref on the canonical arm only and drop it silently on this one. The pair differs only in
  # whether the option exists; before the fix both were ACCEPTED, because the dropped ref
  # left display_300x250_image to match the 300x250 declaration on its own.
  #
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/creative-asset.json pointer=/properties/format_option_ref

  @T-UC-FORMAT-SATISFACTION-manifest-legacy-option-ref-stored @format_satisfaction_sync @requires_db
  Scenario: a creative synced by format_id with a product option ref is assigned and keeps the ref
    Given a packaged product declaring a canonical image option fixed at 300x250
    And the buyer syncs a creative whose format_option_ref names product option "satisfaction_image_mrec" beside format_id "display_300x250_image"
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error
    And the creative is stored with its format_option_ref naming product option "satisfaction_image_mrec"

  @T-UC-FORMAT-SATISFACTION-manifest-legacy-option-ref-unpublished @format_satisfaction_sync @requires_db
  Scenario: a creative synced by format_id with an option ref the product does not publish is refused
    Given a packaged product declaring a canonical image option fixed at 300x250
    And the buyer syncs a creative whose format_option_ref names product option "no_such_option" beside format_id "display_300x250_image"
    When the Buyer Agent assigns it to that package
    Then the buyer is refused UNSUPPORTED_FEATURE

  @T-UC-FORMAT-SATISFACTION-manifest-legacy-option-ref-size-conflict @format_satisfaction_sync @requires_db
  Scenario: a creative synced by format_id naming an option its format_id does not fit is refused
    Given a packaged product declaring a canonical image option fixed at 300x250
    And the buyer syncs a creative whose format_option_ref names product option "satisfaction_image_mrec" beside format_id "display_728x90_image"
    When the Buyer Agent assigns it to that package
    Then the buyer is refused UNSUPPORTED_FEATURE

    # THE REF DOES NOT REPLACE THE format_id. Against the stored row above this differs only
    # in the named format: display_728x90_image normalizes to image at 728x90, which the
    # 300x250 declaration the ref names does not accept. Routed by the ref alone it was
    # ACCEPTED, because the option route never graded the reference and a legacy creative
    # carries no manifest to grade -- a 728x90 creative trafficked on a fixed 300x250
    # product. formats.mdx: "Equal format_kind values alone do not prove compatibility";
    # creative-manifests.mdx § Validation step 5: "do not silently relax the target
    # contract". The reference resolves and selects nothing on this product, "a single
    # resolved route that does not satisfy" (media-buy/package-request.json), so the code is
    # UNSUPPORTED_FEATURE. CONFLICTING_SELECTORS is defined for "A 3.x package request", not
    # a creative.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/formats.mdx section="Matching and satisfaction"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json

  @T-UC-FORMAT-SATISFACTION-manifest-legacy-option-ref-agrees @format_satisfaction_sync @requires_db
  Scenario: a creative synced by format_id whose option ref names the declaration its format_id selects is assigned
    Given a packaged product declaring canonical format options "two_image_options"
    And the buyer syncs a creative whose format_option_ref names product option "satisfaction_image_leaderboard" beside format_id "display_728x90_image"
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error

  @T-UC-FORMAT-SATISFACTION-manifest-legacy-option-ref-disagrees @format_satisfaction_sync @requires_db
  Scenario: a creative synced by format_id whose option ref names another declaration is refused
    Given a packaged product declaring canonical format options "two_image_options"
    And the buyer syncs a creative whose format_option_ref names product option "satisfaction_image_mrec" beside format_id "display_728x90_image"
    When the Buyer Agent assigns it to that package
    Then the buyer is refused UNSUPPORTED_FEATURE

    # BOTH HALVES RESOLVE HERE, and still only the first row is assigned. two_image_options
    # publishes the 300x250 and the 728x90; display_728x90_image selects the 728x90 and each
    # ref selects one of the two. The two scenarios differ only in which option the ref
    # names, so a seller that let the ref decide alone accepts both, and one that ignored the
    # ref accepts both too -- only a seller holding each half to the other refuses the second.

  # ── The MANIFEST against the declaration it selects ───────────────────────────────────
  #
  # docs/creative/creative-manifests.mdx § Validation, at the pin:
  #
  #   Validate a manifest against the selected product or publisher declaration:
  #   1. Resolve format_option_ref when present; otherwise select the unambiguous
  #      compatible format_kind declaration.
  #   2. Confirm all required slots are present.
  #   3. Validate each asset against its declared type and technical constraints.
  #   4. Check fixed dimensions, durations, codecs, file-size limits, and required
  #      disclosures.
  #   5. Reject unknown or uncovered selections; do not silently relax the target contract.
  #
  #   ... Sellers perform final acceptance during sync_creatives ...
  #
  # and docs/creative/formats.mdx § Matching and satisfaction: "Equal format_kind values
  # alone do not prove compatibility." Every gate that assigns a creative -- create_media_buy,
  # update_media_buy and the sync_creatives assignment -- grades the creative's manifest
  # against the declaration it selects. A sync that assigns nothing selects no declaration,
  # and the manifest is held to its kind's slots.
  #
  # THE CODES are the pinned enum's: CREATIVE_SIZE_MISMATCH when only dimensions fail ("The
  # submitted creative dimensions do not match any size accepted by the selected
  # packages"), CREATIVE_VALIDATION_FAILED_GENERIC for every other buyer-correctable
  # failure (a missing or mistyped slot, a file size, a file format, an ambiguous
  # selection). details.problems[] names each failed manifest path with the value the
  # asset states and the values the declaration accepts.
  #
  # EVERY REFUSAL ROW HAS AN ACCEPT ROW differing in one asset member against the same
  # product, so a seller that refused every manifest reddens the accept rows and one that
  # graded kinds alone reddens the refusals -- which is what every refusal row below was
  # before this change: same-kind creatives were accepted whatever their assets said.
  #
  # @source repo=adcp ref=v3.2.7 path=docs/creative/creative-manifests.mdx section="Validation"
  # @source repo=adcp ref=v3.2.7 path=docs/creative/formats.mdx section="Matching and satisfaction"
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json

  @T-UC-FORMAT-SATISFACTION-manifest-create-size-mismatch @format_satisfaction @requires_db
  Scenario: create_media_buy refuses a same-kind creative whose size the declaration does not accept
    Given the seller declares a canonical format option "fixed_300x250"
    And the buyer assigns a canonical "image" creative carrying the "image_728x90" manifest
    When the Buyer Agent sends the create_media_buy request
    Then the creative is refused CREATIVE_SIZE_MISMATCH naming the packages field
    And the refusal names "assets.image_main.width" stating "728" where "300" is accepted
    And the refusal names "assets.image_main.height" stating "90" where "250" is accepted

    # The accept row is @T-UC-FORMAT-SATISFACTION-creative-accepted: the same product, the
    # same kind, image_main at 300x250.

  @T-UC-FORMAT-SATISFACTION-manifest-update-size-mismatch @format_satisfaction @requires_db
  Scenario: update_media_buy refuses a same-kind creative whose size the declaration does not accept
    Given the seller declares a canonical format option "fixed_300x250"
    And the buyer already holds a media buy for that product
    And a canonical "image" creative carrying the "image_728x90" manifest exists
    When the Buyer Agent assigns that creative through update_media_buy
    Then the creative is refused CREATIVE_SIZE_MISMATCH naming the "creative_ids" array
    And the refusal names "assets.image_main.width" stating "728" where "300" is accepted

    # The accept row is @T-UC-FORMAT-SATISFACTION-update-accepted: its creative is
    # image_main at 300x250.

  @T-UC-FORMAT-SATISFACTION-manifest-sync-accepted @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives accepts an assignment whose manifest satisfies the selected declaration - <declaration> <manifest>
    Given a packaged product declaring canonical format options "<declaration>"
    And the buyer syncs a canonical "image" creative carrying the "<manifest>" manifest
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error

    Examples:
      | declaration          | manifest                        |
      | fixed_300x250        | image_300x250                   |
      | fixed_300x250        | image_300x250_200kb             |
      | fixed_300x250        | image_300x250_png               |
      | retina_300x250       | image_600x500_2x                |
      | retina_300x250       | image_600x500                   |
      | retina_300x250       | image_300x250                   |
      | retina_rendition_set | image_renditions_1x_2x          |
      | retina_rendition_set | image_renditions_1x_2x_inferred |
      | retina_rendition_set | image_renditions_1x_1_5x_2x     |
      | retina_300x250       | image_renditions_2x             |

  @T-UC-FORMAT-SATISFACTION-manifest-sync-refused @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives refuses an assignment whose manifest fails the selected declaration - <declaration> <manifest>
    Given a packaged product declaring canonical format options "<declaration>"
    And the buyer syncs a canonical "image" creative carrying the "<manifest>" manifest
    When the Buyer Agent assigns it to that package
    Then the creative is refused <code>
    And the refusal names "<path>" stating "<stated>" where "<accepted>" is accepted

    Examples:
      | declaration          | manifest                  | code                               | path                              | stated | accepted |
      | fixed_300x250        | image_728x90              | CREATIVE_SIZE_MISMATCH             | assets.image_main.width           | 728    | 300      |
      | fixed_300x250        | image_300x250_500kb       | CREATIVE_VALIDATION_FAILED_GENERIC | assets.image_main.file_size_bytes | 512000 | 200      |
      | fixed_300x250        | image_300x250_200_001b    | CREATIVE_VALIDATION_FAILED_GENERIC | assets.image_main.file_size_bytes | 200001 | 200      |
      | fixed_300x250        | image_300x250_200kib      | CREATIVE_VALIDATION_FAILED_GENERIC | assets.image_main.file_size_bytes | 204800 | 200      |
      | fixed_300x250        | image_300x250_gif         | CREATIVE_VALIDATION_FAILED_GENERIC | assets.image_main.format          | gif    | jpg, png |
      | fixed_300x250        | image_main_as_text        | CREATIVE_VALIDATION_FAILED_GENERIC | assets.image_main.asset_type      | text   | image    |
      | two_image_options    | image_160x600             | CREATIVE_SIZE_MISMATCH             | assets.image_main.width           | 160    | 300      |
      | plain_300x250        | image_600x500_2x          | CREATIVE_SIZE_MISMATCH             | assets.image_main.pixel_ratio     | 2      | 1        |
      | plain_300x250        | image_600x500             | CREATIVE_SIZE_MISMATCH             | assets.image_main.width           | 600    | 300      |
      | retina_300x250       | image_450x375_1_5x        | CREATIVE_SIZE_MISMATCH             | assets.image_main.pixel_ratio     | 1.5    | 1, 2     |
      | retina_rendition_set | image_renditions_1x_1x_2x | CREATIVE_VALIDATION_FAILED_GENERIC | assets.image_main[1].pixel_ratio  | 1      | 1, 2     |

    # THE PAIRS. Against fixed_300x250 (image 300x250, max_file_size_kb 200, image_formats
    # jpg/png): 728x90 against 300x250 is the creative half of formats.mdx's fifth rule; 500
    # binary KB, 200,001 bytes and 204,800 bytes against 200 KB exactly (200,000 bytes);
    # gif against png; the image_main slot holding a text asset against holding an image
    # (step 3, "against its declared type"). The file-size and
    # format rows are not size failures, so they take the generic code -- the code split is
    # graded, not only the refusal. Against two_image_options (300x250 and 728x90), 160x600
    # fits neither, so the refusal is its size against the first declaration's dimensions:
    # whichever option the buyer names, the assets fail it. A manifest that fits one of them
    # and names neither is refused for the missing ref instead
    # (@T-UC-FORMAT-SATISFACTION-manifest-ref-required).
    #
    # DENSITY IS ITS OWN AXIS. A declaration's width and height are LOGICAL; an image asset's
    # are intrinsic. formats/canonical/image.json: "an accepted asset's intrinsic width is
    # width x pixel_ratio", pixel_ratios "[1, 2] accepts both standard and Retina
    # renditions", "Absence means [1]", and "SDKs determine the effective ratio from
    # asset.pixel_ratio when supplied, otherwise infer it from intrinsic asset dimensions
    # divided by the matched logical size". So a 600x500 image -- its ratio stated as 2, or
    # left to inference -- is accepted by retina_300x250 above, and refused by
    # plain_300x250, which differs from it only in stating no pixel_ratios: stated, the
    # ratio 2 is outside [1]; inferred, the only ratio left is 1 and the image is 600 wide.
    # A density outside the accepted set is an intrinsic size the declaration does not
    # accept, so it is CREATIVE_SIZE_MISMATCH. The 300x250 row above is the control that a
    # [1, 2] declaration still takes a standard rendition.
    #
    # [1, 2] IS A SET OF TWO DENSITIES, not the interval between them: the 450x375 image at
    # 1.5x is refused by retina_300x250, which accepts the 300x250 at 1x and the 600x500 at 2x
    # above. A seller reading a two-number list as a range accepts it.
    #
    # A RENDITION SET IS GRADED RENDITION BY RENDITION. retina_rendition_set is the pinned
    # acme_retina_mrec declaration: image_main accepts 1x, 1.5x and 2x and requires the 1x and
    # the 2x. The duplicate row sends the 1x twice beside the 2x and is refused at the second
    # copy, image_main[1]: "exactly one matching image rendition for each required ratio".
    # Its accept rows above send each required rendition once -- the ratios stated, or left
    # to inference from 300x250 and 600x500 -- and once more with the optional 1.5x, which
    # "Other accepted ratios remain optional" permits.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/image.json pointer=/properties/pixel_ratios
    #
    # A KILOBYTE IS 1,000 BYTES. canonical-formats.mdx: "a field ending in _kb uses 1 KB =
    # 1,000 bytes ... Implementations MUST NOT substitute binary KiB/MiB units for these
    # fields". The 200,001-byte and 204,800-byte images are over 200 KB and under 200 binary
    # kilobytes, so a seller dividing by 1024 accepts both; the 200,000-byte accept row is the
    # boundary.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/assets/image-asset.json pointer=/properties/pixel_ratio
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/_base.json pointer=/properties/slots/items/properties/required_pixel_ratios
    # @source repo=adcp ref=v3.2.7 path=static/examples/products/canonical/acme_retina_mrec.json
    # @source repo=adcp ref=v3.2.7 path=docs/creative/canonical-formats.mdx section="Technical completeness and file-size units"

  @T-UC-FORMAT-SATISFACTION-manifest-sync-rendition-missing @format_satisfaction_sync @requires_db
  Scenario: sync_creatives refuses an assignment whose rendition set lacks a required pixel ratio
    Given a packaged product declaring canonical format options "retina_rendition_set"
    And the buyer syncs a canonical "image" creative carrying the "image_renditions_2x" manifest
    When the Buyer Agent assigns it to that package
    Then the creative is refused CREATIVE_VALIDATION_FAILED_GENERIC
    And the refusal names "assets.image_main.pixel_ratio" missing where "1" is accepted

    # THE REQUIRED RATIO, NOT THE ACCEPTED SET, DEMANDS THE RENDITION. formats/canonical/
    # image.json pixel_ratios: "This is an acceptance set, not a requirement to submit every
    # rendition: one image_main asset satisfying any listed ratio is sufficient unless the
    # effective image_main slot declares required_pixel_ratios". So the same retina-only
    # set is accepted by retina_300x250 in the outline above, which accepts 2x and requires
    # nothing, and refused here, where the slot requires 1x and 2x. The accept row with the
    # same declaration is image_renditions_1x_2x, which differs only in also sending the 1x.
    #
    # THE CODE IS THE GENERIC ONE. CREATIVE_SIZE_MISMATCH is "The submitted creative
    # dimensions do not match any size accepted by the selected packages" (enums/error-code.json),
    # and the 600x500 at 2x matches an accepted size; what fails is the set's coverage, which
    # no specific code names.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/_base.json pointer=/properties/slots/items/properties/required_pixel_ratios
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json
    # @source repo=adcp ref=v3.2.7 path=static/test-vectors/canonical-image-pixel-ratio.json

  @T-UC-FORMAT-SATISFACTION-manifest-sync-file-size-unstated @format_satisfaction_sync @requires_db
  Scenario: sync_creatives refuses an assignment whose manifest is silent about a capped file size
    Given a packaged product declaring canonical format options "fixed_300x250"
    And the buyer syncs a canonical "image" creative carrying the "image_300x250_unsized" manifest
    When the Buyer Agent assigns it to that package
    Then the creative is refused CREATIVE_VALIDATION_FAILED_GENERIC
    And the refusal names "assets.image_main.file_size_bytes" missing where "200" is accepted

    # A CAP THE MANIFEST DOES NOT ANSWER CANNOT BE CHECKED. core/assets/image-asset.json:
    # file_size_bytes is "Required by agents that advertise a max_file_size_kb constraint",
    # and fixed_300x250 advertises 200. The accept row is image_300x250 in the outline above,
    # which differs from this one only in stating its file size; accepting the silent one
    # would "silently relax the target contract" (creative-manifests.mdx § Validation step 5).
    # A package SELECTOR silent about the cap still inherits it -- the canonical_formats
    # storyboard's equivalent-direct-legacy step grades that -- because a selector names a
    # format and a manifest is the creative.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/assets/image-asset.json pointer=/properties/file_size_bytes

  @T-UC-FORMAT-SATISFACTION-manifest-sync-media-accepted @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives accepts an assignment whose video or audio manifest satisfies the selected declaration - <declaration> <manifest>
    Given a packaged product declaring canonical format options "<declaration>"
    And the buyer syncs a canonical "<kind>" creative carrying the "<manifest>" manifest
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error

    Examples:
      | declaration         | kind         | manifest       |
      | exact_duration      | video_hosted | video_30s      |
      | duration_range      | video_hosted | video_30s      |
      | mp4_h264_10mb_video | video_hosted | video_30s      |
      | mp4_h264_10mb_video | video_hosted | video_30s_10mb     |
      | mp4_h264_10mb_video | video_hosted | video_30s_8000kbps |
      | mp3_30s_audio       | audio_hosted | audio_30s          |
      | mp3_30s_audio       | audio_hosted | audio_30s_48khz    |

  @T-UC-FORMAT-SATISFACTION-manifest-sync-media-refused @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives refuses an assignment whose video or audio manifest fails the selected declaration - <declaration> <manifest>
    Given a packaged product declaring canonical format options "<declaration>"
    And the buyer syncs a canonical "<kind>" creative carrying the "<manifest>" manifest
    When the Buyer Agent assigns it to that package
    Then the creative is refused CREATIVE_VALIDATION_FAILED_GENERIC
    And the refusal names "<path>" stating "<stated>" where "<accepted>" is accepted

    Examples:
      | declaration         | kind         | manifest            | path                                 | stated   | accepted     |
      | exact_duration      | video_hosted | video_15s           | assets.video_main.duration_ms        | 15000    | 30000        |
      | duration_range      | video_hosted | video_90s           | assets.video_main.duration_ms        | 90000    | 5000, 60000  |
      | mp4_h264_10mb_video | video_hosted | video_30s_vp9       | assets.video_main.video_codec        | vp9      | h264         |
      | mp4_h264_10mb_video | video_hosted | video_30s_webm      | assets.video_main.container_format   | webm     | mp4          |
      | mp4_h264_10mb_video | video_hosted | video_30s_10_4mb    | assets.video_main.file_size_bytes    | 10400000 | 10           |
      | mp4_h264_10mb_video | video_hosted | video_30s_12000kbps | assets.video_main.video_bitrate_kbps | 12000    | 8000         |
      | mp4_h264_10mb_video | video_hosted | video_30s_60fps     | assets.video_main.frame_rate         | 60       | 25, 30       |
      | mp3_30s_audio       | audio_hosted | audio_15s           | assets.audio_main.duration_ms        | 15000    | 30000        |
      | mp3_30s_audio       | audio_hosted | audio_30s_aac       | assets.audio_main.codec              | aac      | mp3          |
      | mp3_30s_audio       | audio_hosted | audio_30s_1_04mb    | assets.audio_main.file_size_bytes    | 1040000  | 1            |
      | mp3_30s_audio       | audio_hosted | audio_30s_22khz     | assets.audio_main.sampling_rate_hz   | 22050    | 44100, 48000 |
      | mp3_30s_audio       | audio_hosted | audio_30s_mono      | assets.audio_main.channels           | mono     | stereo       |

    # STEPS 3 AND 4 ON THE TWO OTHER MEDIA KINDS: "Validate each asset against its declared
    # type and technical constraints" and "Check fixed dimensions, durations, codecs,
    # file-size limits". Each refusal row has an accept row against the same declaration that
    # differs in the one member under test: a 30-second clip against exact_duration (30000)
    # and duration_range ([5000, 60000]); h264 in mp4 at 8 MB, 5,000 kbps and 30 fps, at
    # exactly 10 MB and at exactly 8,000 kbps against mp4_h264_10mb_video; and a 30-second
    # stereo mp3 of 900 KB at 44.1 kHz and at 48 kHz against mp3_30s_audio. The asset
    # members are the pinned asset schemas' own (core/assets/video-asset.json duration_ms,
    # video_codec, container_format, file_size_bytes, video_bitrate_kbps, frame_rate;
    # audio-asset.json codec, sampling_rate_hz, channels), graded against the declaration
    # params that constrain them (duration_ms_exact, duration_ms_range, video_codecs,
    # containers, max_file_size_mb, max_bitrate_kbps, frame_rates, audio_codecs,
    # audio_sample_rates, audio_channels). video-asset.json types frame_rate as a string "to
    # preserve precision", so "30" is read as the 30 frame_rates lists.
    #
    # A MEGABYTE IS 1,000,000 BYTES: video_hosted.json says "1 MB is exactly 1,000,000 bytes"
    # and audio_hosted.json "decimal megabytes". The 10,400,000-byte clip and the
    # 1,040,000-byte spot are over the cap in decimal megabytes and under it in binary ones,
    # so a seller reading the cap as binary accepts both refusal rows; the exactly-10 MB
    # accept row is the boundary.
    #
    # None of these is a size, so the code is the generic one: CREATIVE_SIZE_MISMATCH is for
    # "creative dimensions", and no specific code names a duration, codec or file size.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/creative-manifests.mdx section="Validation"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/video_hosted.json pointer=/properties/max_file_size_mb
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/audio_hosted.json pointer=/properties/max_file_size_mb
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/assets/video-asset.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/assets/audio-asset.json

  @T-UC-FORMAT-SATISFACTION-manifest-sync-slot-limits-accepted @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives accepts an assignment whose every slot meets its own limits - <manifest>
    Given a packaged product declaring canonical format options "slot_limited_300x250"
    And the buyer syncs a canonical "image" creative carrying the "<manifest>" manifest
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error

    Examples:
      | manifest                 |
      | image_headlined          |
      | image_headline_25_chars  |
      | image_two_headlines      |
      | image_body_text_90_chars |
      | image_logo_50kb          |
      | image_logo_2x            |

  @T-UC-FORMAT-SATISFACTION-manifest-sync-slot-limits-refused @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives refuses an assignment whose slot other than the main media fails its own limit - <manifest>
    Given a packaged product declaring canonical format options "slot_limited_300x250"
    And the buyer syncs a canonical "image" creative carrying the "<manifest>" manifest
    When the Buyer Agent assigns it to that package
    Then the creative is refused <code>
    And the refusal names "<path>" stating "<stated>" where "<accepted>" is accepted

    Examples:
      | manifest                 | code                               | path                        | stated | accepted |
      | image_headline_26_chars  | CREATIVE_VALIDATION_FAILED_GENERIC | assets.headline.content     | 26     | 25       |
      | image_three_headlines    | CREATIVE_VALIDATION_FAILED_GENERIC | assets.headline             | 3      | 2        |
      | image_body_text_91_chars | CREATIVE_VALIDATION_FAILED_GENERIC | assets.body_text.content    | 91     | 90       |
      | image_logo_51kb          | CREATIVE_VALIDATION_FAILED_GENERIC | assets.logo.file_size_bytes | 51000  | 50       |
      | image_logo_3x            | CREATIVE_SIZE_MISMATCH             | assets.logo.pixel_ratio     | 3      | 1, 2     |

  @T-UC-FORMAT-SATISFACTION-manifest-sync-slot-size-unstated @format_satisfaction_sync @requires_db
  Scenario: sync_creatives refuses an assignment whose capped slot is silent about its file size
    Given a packaged product declaring canonical format options "slot_limited_300x250"
    And the buyer syncs a canonical "image" creative carrying the "image_logo_unsized" manifest
    When the Buyer Agent assigns it to that package
    Then the creative is refused CREATIVE_VALIDATION_FAILED_GENERIC
    And the refusal names "assets.logo.file_size_bytes" missing where "50" is accepted

    # EVERY SLOT, NOT ONLY THE MAIN MEDIA. formats/canonical/_base.json: "per-slot
    # constraints (a specific slot's max_chars or max_size_kb) live on the slot entry", and
    # "Format-level narrowing parameters that apply across all slots (e.g., flat
    # headline_max_chars ...) may also live on the format declaration". slot_limited_300x250
    # puts four of them on the slots beside image_main: the headline's max_chars 25 and max 2
    # ("Maximum count for repeatable / pool slots"), the flat body_text_max_chars 90, and the
    # logo's max_size_kb 50 and pixel_ratios [1, 2]. Every refusal row has an accept row
    # differing in that one slot: the limit met exactly, or the logo at 2x.
    #
    # THE KILOBYTE HERE IS 1,000 BYTES: max_size_kb is "exact kilobytes, where 1 KB = 1,000
    # bytes". The 51,000-byte logo is 51 decimal kilobytes and 49.8 binary ones, so a seller
    # dividing by 1024 accepts it. Silence about a capped slot's size fails as silence about
    # the declaration's own cap does (the file-size-unstated scenario above): a cap the
    # manifest does not answer cannot be checked.
    #
    # The logo's density is a size, so that row alone is CREATIVE_SIZE_MISMATCH; the image
    # canonical's pixel_ratios: "an accepted asset's intrinsic width is width x pixel_ratio".
    # A character count, a count of assets and a file size are not sizes, so the others take
    # the generic code.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/_base.json pointer=/properties/slots
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/image.json pointer=/properties/pixel_ratios
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/assets/text-asset.json pointer=/properties/content

  @T-UC-FORMAT-SATISFACTION-manifest-sync-flat-slot-params-accepted @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives accepts an assignment whose slots meet the limits the declaration states flat - <declaration> <manifest>
    Given a packaged product declaring canonical format options "<declaration>"
    And the buyer syncs a canonical "<kind>" creative carrying the "<manifest>" manifest
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error

    Examples:
      | declaration                 | kind                | manifest                        |
      | responsive_headlines        | responsive_creative | responsive_headline_30_chars    |
      | responsive_headlines        | responsive_creative | responsive_two_headlines        |
      | carousel_card_headlines     | image_carousel      | carousel_card_headline_25_chars |
      | audio_with_companion        | audio_hosted        | audio_mastered_with_companion   |
      | captioned_video_with_banner | video_hosted        | video_captioned_with_banner     |
      | carousel_card_media         | image_carousel      | carousel_three_cards            |
      | carousel_card_media         | image_carousel      | carousel_four_cards             |
      | carousel_card_media         | image_carousel      | carousel_video_card             |
      | responsive_pool_limits      | responsive_creative | responsive_video_6s             |
      | responsive_pool_limits      | responsive_creative | responsive_video_30s            |
      | responsive_pool_limits      | responsive_creative | responsive_logo_4_1             |
      | responsive_pool_limits      | responsive_creative | responsive_logo_150kb           |
      | responsive_pool_limits      | responsive_creative | responsive_image_150kb          |
      | native_image_cap            | native_in_feed      | native_main_image_100kb         |
      | native_image_cap            | native_in_feed      | native_icon_100kb               |

  @T-UC-FORMAT-SATISFACTION-manifest-sync-flat-slot-params-refused @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives refuses an assignment whose slot fails a limit the declaration states flat - <declaration> <manifest>
    Given a packaged product declaring canonical format options "<declaration>"
    And the buyer syncs a canonical "<kind>" creative carrying the "<manifest>" manifest
    When the Buyer Agent assigns it to that package
    Then the creative is refused <code>
    And the refusal names "<path>" stating "<stated>" where "<accepted>" is accepted

    Examples:
      | declaration                 | kind                | manifest                         | code                               | path                                    | stated  | accepted |
      | responsive_headlines        | responsive_creative | responsive_headline_31_chars     | CREATIVE_VALIDATION_FAILED_GENERIC | assets.headlines[0].content                | 31       | 30       |
      | responsive_headlines        | responsive_creative | responsive_three_headlines       | CREATIVE_VALIDATION_FAILED_GENERIC | assets.headlines                           | 3        | 2        |
      | carousel_card_headlines     | image_carousel      | carousel_card_headline_26_chars  | CREATIVE_VALIDATION_FAILED_GENERIC | assets.cards[0].headline                   | 26       | 25       |
      | audio_with_companion        | audio_hosted        | audio_too_loud_with_companion    | CREATIVE_VALIDATION_FAILED_GENERIC | assets.audio_main.loudness_lufs            | -14.5    | -17, -15 |
      | audio_with_companion        | audio_hosted        | audio_peaking_with_companion     | CREATIVE_VALIDATION_FAILED_GENERIC | assets.audio_main.true_peak_dbfs           | -1       | -2       |
      | audio_with_companion        | audio_hosted        | audio_with_wide_companion        | CREATIVE_SIZE_MISMATCH             | assets.companion_image                     | 300:250  | 1:1      |
      | audio_with_companion        | audio_hosted        | audio_with_heavy_companion       | CREATIVE_VALIDATION_FAILED_GENERIC | assets.companion_image.file_size_bytes     | 100001   | 100      |
      | captioned_video_with_banner | video_hosted        | video_captioned_with_leaderboard | CREATIVE_SIZE_MISMATCH             | assets.companion_banner.width              | 728      | 300      |
      | carousel_card_media         | image_carousel      | carousel_two_cards               | CREATIVE_VALIDATION_FAILED_GENERIC | assets.cards                               | 2        | 3        |
      | carousel_card_media         | image_carousel      | carousel_five_cards              | CREATIVE_VALIDATION_FAILED_GENERIC | assets.cards                               | 5        | 4        |
      | carousel_card_media         | image_carousel      | carousel_wide_card               | CREATIVE_SIZE_MISMATCH             | assets.cards[0].media                      | 1200:628 | 1:1      |
      | carousel_card_media         | image_carousel      | carousel_heavy_image_card        | CREATIVE_VALIDATION_FAILED_GENERIC | assets.cards[0].media.file_size_bytes      | 100001   | 100      |
      | carousel_card_media         | image_carousel      | carousel_heavy_video_card        | CREATIVE_VALIDATION_FAILED_GENERIC | assets.cards[0].media.file_size_bytes      | 1000001  | 1000     |
      | carousel_card_media         | image_carousel      | carousel_long_video_card         | CREATIVE_VALIDATION_FAILED_GENERIC | assets.cards[0].media.duration_ms          | 15001    | 15000    |
      | carousel_card_headlines     | image_carousel      | carousel_video_card              | CREATIVE_VALIDATION_FAILED_GENERIC | assets.cards[0].media.asset_type           | video    | image    |
      | responsive_pool_limits      | responsive_creative | responsive_video_5999ms          | CREATIVE_VALIDATION_FAILED_GENERIC | assets.video[0].duration_ms                | 5999     | 6000     |
      | responsive_pool_limits      | responsive_creative | responsive_video_30001ms         | CREATIVE_VALIDATION_FAILED_GENERIC | assets.video[0].duration_ms                | 30001    | 30000    |
      | responsive_pool_limits      | responsive_creative | responsive_logo_2_1              | CREATIVE_SIZE_MISMATCH             | assets.logo[0]                             | 200:100  | 1:1, 4:1 |
      | responsive_pool_limits      | responsive_creative | responsive_logo_150_001b         | CREATIVE_VALIDATION_FAILED_GENERIC | assets.logo[0].file_size_bytes             | 150001   | 150      |
      | responsive_pool_limits      | responsive_creative | responsive_image_150_001b        | CREATIVE_VALIDATION_FAILED_GENERIC | assets.images_landscape[0].file_size_bytes | 150001   | 150      |
      | native_image_cap            | native_in_feed      | native_main_image_100_001b       | CREATIVE_VALIDATION_FAILED_GENERIC | assets.main_image.file_size_bytes          | 100001   | 100      |
      | native_image_cap            | native_in_feed      | native_icon_100_001b             | CREATIVE_VALIDATION_FAILED_GENERIC | assets.icon.file_size_bytes                | 100001   | 100      |
      | native_image_cap            | native_in_feed      | native_main_image_102_400b       | CREATIVE_VALIDATION_FAILED_GENERIC | assets.main_image.file_size_bytes          | 102400   | 100      |

  @T-UC-FORMAT-SATISFACTION-manifest-sync-flat-slot-params-missing @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives refuses an assignment missing what the declaration requires flat - <declaration> <manifest>
    Given a packaged product declaring canonical format options "<declaration>"
    And the buyer syncs a canonical "<kind>" creative carrying the "<manifest>" manifest
    When the Buyer Agent assigns it to that package
    Then the creative is refused CREATIVE_VALIDATION_FAILED_GENERIC
    And the refusal names "<path>" missing where "<accepted>" is accepted

    Examples:
      | declaration                 | kind         | manifest                      | path                           | accepted |
      | audio_with_companion        | audio_hosted | audio_without_companion       | assets.companion_image         | image    |
      | captioned_video_with_banner | video_hosted | video_uncaptioned_with_banner | assets.video_main.captions_url | required |

    # FLAT PER-SLOT PARAMS. formats/canonical/_base.json slots: "Format-level narrowing
    # parameters that apply across all slots (e.g., flat headline_max_chars on
    # responsive_creative) may also live on the format declaration". A flat param names its
    # slot by role, in either number: responsive_creative.json's headlines pool is limited by
    # headline_max_chars and headlines_max ("per-slot count/length narrowing lives in flat
    # parameters"); image_carousel.json's card_headline_max_chars "Governs the headline field
    # on each card-asset in the cards slot"; audio_hosted.json's companion_image_* params
    # constrain the companion_image slot, and video_hosted.json's companion_banner_widths and
    # companion_banner_heights ("Permitted companion banner widths") the companion_banner
    # slot. A seller building the param name from the slot id reads headlines_max_chars,
    # which no schema declares, and grades nothing.
    #
    # Every refusal row has an accept row against the same declaration that differs in the
    # one fact under test: a headline of 30 characters, two headlines, a first card headline
    # of 25, the spot at -16.5 LUFS and -2 dBFS with a 250x250 companion of 100,000 bytes
    # ("1 KB = 1,000 bytes"), the clip with a caption track and a 300x250 banner. The missing
    # rows differ from their accept rows in leaving out the companion image that
    # companion_image_required requires, and the caption track that captions: required
    # requires (video-asset.json captions_url).
    #
    # SELLER CHOICES the pin leaves open, recorded here: audio_hosted.json gives
    # loudness_tolerance_db ("Permitted deviation from loudness_lufs in dB") no default, and
    # this seller reads an absent tolerance as zero, the reading that does not relax the
    # target; true_peak_dbfs is "Maximum true-peak level", so a peak at the ceiling is
    # accepted. A loudness or peak the asset does not state is not graded: audio-asset.json
    # makes both members optional, and creative-manifests.mdx names no silence rule for them.
    # The companion's ratio and the banner's size are sizes, so those rows are
    # CREATIVE_SIZE_MISMATCH; the others are not, and take the generic code.
    #
    # THE CAROUSEL'S CARDS AND THEIR MEDIA. image_carousel.json's min_cards and max_cards
    # ("Minimum card count", "Maximum card count") narrow the cards slot's own 2 to 10 to 3 to
    # 4, and the rest of its flat params constrain each card's media, the image or video a
    # card-asset carries (core/assets/card-asset.json): card_aspect_ratio ("Aspect ratio
    # shared across all cards"), card_image_max_file_size_kb and card_video_max_file_size_kb,
    # card_video_max_duration_ms, and allowed_card_media_asset_types ("Asset types each card's
    # media field may carry. Default: ['image']"). carousel_card_media allows both types;
    # carousel_card_headlines states none, so the default refuses the video card the other
    # accepts. The 100,001-byte image and the 1,000,001-byte video are over the cap in decimal
    # kilobytes and under it in binary ones (canonical-formats.mdx: "1 KB = 1,000 bytes").
    #
    # THE RESPONSIVE CREATIVE'S POOLS. responsive_creative.json's video_min_duration_ms and
    # video_max_duration_ms bound each video the video pool holds, inclusively as every min_
    # and max_ bound is; logo_aspect_ratios is a SET of ratios, so a 400x100 logo (4:1) is
    # accepted and a 200x100 one (2:1) refused; and asset_image_max_file_size_kb caps every
    # image the pool holds, whichever slot it fills, so the logo and the landscape image are
    # both held to 150 KB. That last reading is this seller's: the pin describes none of the
    # three, and "asset_image" names no slot, so the asset type is what it names. A logo's
    # shape is a size, so that row is CREATIVE_SIZE_MISMATCH; a count, a duration, a file
    # size and a media type are not.
    #
    # THE NATIVE UNIT'S IMAGES. native_in_feed.json's max_image_file_size_kb is "Maximum file
    # size in kilobytes for main_image and icon", the two image slots the format declares, so
    # each is held to it in decimal kilobytes: 100,000 bytes is accepted, 100,001 is refused,
    # and so is 102,400, which is 100 binary kilobytes. It is the same cap
    # asset_image_max_file_size_kb states on the responsive creative, spelled the other way.
    #
    # SELLER CHOICES the pin leaves open, recorded here and asked upstream in
    # adcontextprotocol/adcp#8629: max_cards is a "narrowing" parameter (_base.json:
    # "Format-level narrowing parameters"), so with the slot's own max of 10 the stricter of
    # the two holds and a declaration stating max_cards above 10 still caps at 10 unless it
    # restates the slot; and a declaration silent about allowed_card_media_asset_types takes
    # the parameter's own "Default: ['image']", although the format description lists image
    # and video as allowed.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/image_carousel.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/assets/card-asset.json pointer=/properties/media
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/native_in_feed.json pointer=/properties/max_image_file_size_kb
    # @source repo=adcp ref=v3.2.7 path=docs/creative/canonical-formats.mdx section="Technical completeness and file-size units"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/_base.json pointer=/properties/slots/description
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/responsive_creative.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/image_carousel.json pointer=/properties/card_headline_max_chars
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/assets/card-asset.json pointer=/properties/headline
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/audio_hosted.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/assets/audio-asset.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/video_hosted.json pointer=/properties/captions
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/assets/video-asset.json pointer=/properties/captions_url

  @T-UC-FORMAT-SATISFACTION-manifest-sync-aspect-ratio @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives refuses a manifest whose shape disagrees with the declaration's aspect ratio - <declaration> <manifest>
    Given a packaged product declaring canonical format options "<declaration>"
    And the buyer syncs a canonical "<kind>" creative carrying the "<manifest>" manifest
    When the Buyer Agent assigns it to that package
    Then the creative is refused CREATIVE_SIZE_MISMATCH
    And the refusal names "<path>" stating "<stated>" where "<ratio>" is accepted

    Examples:
      | declaration     | kind         | manifest        | path              | stated    | ratio  |
      | responsive_1_91 | image        | image_1200x1200 | assets.image_main | 1200:1200 | 1.91:1 |
      | vertical_video  | video_hosted | video_30s       | assets.video_main | 640:360   | 9:16   |

  @T-UC-FORMAT-SATISFACTION-manifest-sync-aspect-ratio-accepted @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives accepts a manifest at the declaration's aspect ratio - <declaration> <manifest>
    Given a packaged product declaring canonical format options "<declaration>"
    And the buyer syncs a canonical "<kind>" creative carrying the "<manifest>" manifest
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error

    Examples:
      | declaration     | kind         | manifest            |
      | responsive_1_91 | image        | image_1200x628      |
      | vertical_video  | video_hosted | video_30s_1080x1920 |

    # STEP 4's "fixed dimensions" include the declared shape. formats/canonical/image.json
    # aspect_ratio: "When provided alongside width/height, must agree. When used with sizes[]
    # or responsive ranges, narrows accepted entries to those matching the aspect ratio."
    # responsive_1_91 accepts 600 to 1200 wide at 1.91:1, so 1200x1200 is inside the width
    # range and refused for its shape, and 1200x628 differs from it only in height. A ratio
    # mismatch is a size the declaration does not accept, so it is CREATIVE_SIZE_MISMATCH.
    #
    # A VIDEO'S ORIENTATION NAMES ITS RATIO. video_hosted.json: orientation "Vertical = 9:16
    # (Reels, Stories, Shorts). Horizontal = 16:9 (instream, CTV). Square = 1:1", and
    # aspect_ratio is "Inferred from orientation if omitted". vertical_video states only the
    # orientation, so the 640x360 clip is refused and the same clip at 1080x1920 accepted.
    #
    # The pin states the ratio and no tolerance. This seller compares at the ratio's own
    # precision: exactly for whole terms (9:16 takes 1080x1920), and to the decimals the ratio
    # is written in otherwise, so 1200x628 (1.9108) is 1.91:1.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/image.json pointer=/properties/aspect_ratio
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/video_hosted.json pointer=/properties/orientation
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json

  @T-UC-FORMAT-SATISFACTION-manifest-sync-cta-accepted @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives accepts an assignment whose call to action is one the declaration permits - <declaration> <manifest>
    Given a packaged product declaring canonical format options "<declaration>"
    And the buyer syncs a canonical "<kind>" creative carrying the "<manifest>" manifest
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error

    Examples:
      | declaration         | kind           | manifest                   |
      | image_cta_300x250   | image          | image_cta_shop_now         |
      | image_cta_300x250   | image          | image_300x250              |
      | native_cta          | native_in_feed | native_cta_sign_up         |
      | video_cta           | video_hosted   | video_cta_watch_more       |
      | carousel_card_media | image_carousel | carousel_card_cta_shop_now |

  @T-UC-FORMAT-SATISFACTION-manifest-sync-cta-refused @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives refuses an assignment whose call to action is outside the declared values - <declaration> <manifest>
    Given a packaged product declaring canonical format options "<declaration>"
    And the buyer syncs a canonical "<kind>" creative carrying the "<manifest>" manifest
    When the Buyer Agent assigns it to that package
    Then the creative is refused CREATIVE_VALUE_NOT_ALLOWED at "<path>" allowing "<allowed>"
    And the refusal names "<path>" stating "<stated>" where "<allowed>" is accepted

    Examples:
      | declaration         | kind           | manifest                  | path                | stated       | allowed              |
      | image_cta_300x250   | image          | image_cta_explore_more    | assets.cta.content  | EXPLORE_MORE | LEARN_MORE, SHOP_NOW |
      | image_cta_300x250   | image          | image_cta_lowercase       | assets.cta.content  | shop_now     | LEARN_MORE, SHOP_NOW |
      | native_cta          | native_in_feed | native_cta_download       | assets.cta.content  | DOWNLOAD     | LEARN_MORE, SIGN_UP  |
      | video_cta           | video_hosted   | video_cta_learn_more      | assets.cta.content  | LEARN_MORE   | WATCH_MORE, SHOP_NOW |
      | carousel_card_media | image_carousel | carousel_card_cta_buy_now | assets.cards[0].cta | BUY_NOW      | SHOP_NOW, LEARN_MORE |

    # A CLOSED SET OF CALLS TO ACTION. image.json and video_hosted.json declare cta_values,
    # "Permitted CTA values for this product", and native_in_feed.json adds "When set, narrows
    # the cta slot to a closed enum"; core/assets/card-asset.json holds each card to it: "When
    # the parent format declares cta_values (allowed CTA labels), the per-card cta MUST be one
    # of those values". The value is a text asset's content (core/assets/text-asset.json).
    #
    # THE CODE IS ITS OWN. enums/error-code.json CREATIVE_VALUE_NOT_ALLOWED: "A submitted
    # text-asset value is not in the format's declared allowed_values list ... a closed-set
    # constraint violation that the buyer can resolve mechanically", and "The seller MUST set
    # error.field to the offending asset's path within the manifest ... and SHOULD include the
    # format's allowed_values array in error.details.allowed_values". So the refusal points at
    # the manifest path, unlike the sync assignment's other creative refusals, and carries the
    # declared set. The pinned native_in_feed storyboard's reject_cta_not_in_enum step grades
    # this code for a CTA outside cta_values, which this seller cannot run (it requires the
    # controller), so these rows grade it here.
    #
    # Every refusal row has an accept row against the same declaration differing only in the
    # value; image_300x250 is the control that a declaration closing the cta slot does not
    # require one (the slot stays optional). The values are matched exactly, so shop_now is
    # not SHOP_NOW: "any value outside that list is rejected by definition".
    #
    # The pin reads two ways on the details key (the enum entry's allowed_values against
    # core/error.json's canonical accepted_values) and on whether a card's cta, a string
    # member rather than a text asset, takes this code. This seller follows the code's own
    # entry for both: allowed_values at the top of details, and accepted_values on each
    # problems[] entry as on every other creative refusal. Asked upstream in
    # adcontextprotocol/adcp#8628.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/image.json pointer=/properties/cta_values
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/native_in_feed.json pointer=/properties/cta_values
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/assets/card-asset.json pointer=/properties/cta
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json
    # @source repo=adcp ref=v3.2.7 path=dist/compliance/3.2.7/protocols/creative/scenarios/native_in_feed.yaml step=reject_cta_not_in_enum

  @T-UC-FORMAT-SATISFACTION-manifest-ambiguous @format_satisfaction_sync @requires_db
  Scenario: an assignment whose manifest fits two same-kind declarations and names no option ref is refused
    Given a packaged product declaring canonical format options "two_mrec_options"
    And the buyer syncs a canonical "image" creative carrying the "image_300x250" manifest
    When the Buyer Agent assigns it to that package
    Then the creative is refused CREATIVE_VALIDATION_FAILED_GENERIC
    And the refusal names the compatible options "satisfaction_image_mrec, satisfaction_image_mrec_gif"

  @T-UC-FORMAT-SATISFACTION-manifest-ambiguous-resolved-by-ref @format_satisfaction_sync @requires_db
  Scenario: the same manifest naming one of the two options is assigned to it
    Given a packaged product declaring canonical format options "two_mrec_options"
    And the buyer syncs a canonical "image" creative carrying the "image_300x250" manifest and naming product option "satisfaction_image_mrec_gif"
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error
    And the creative is stored with its format_option_ref naming product option "satisfaction_image_mrec_gif"

    # two_mrec_options publishes two 300x250 images differing in image_formats; a 300x250
    # manifest stating no file format is compatible with both, so the refusal names both
    # options, the values the ref can take. The scenario just above resolves it by the ref.
    # The rule these grade is stated, with its sources, on the outline below.

  @T-UC-FORMAT-SATISFACTION-manifest-ref-required @format_satisfaction_sync @requires_db
  Scenario Outline: an assignment whose manifest fits one of several same-kind declarations and names no option ref is refused - <declaration> <manifest>
    Given a packaged product declaring canonical format options "<declaration>"
    And the buyer syncs a canonical "image" creative carrying the "<manifest>" manifest
    When the Buyer Agent assigns it to that package
    Then the creative is refused CREATIVE_VALIDATION_FAILED_GENERIC
    And the refusal names the compatible options "<option>"

    Examples:
      | declaration       | manifest          | option                         |
      | two_image_options | image_300x250     | satisfaction_image_mrec        |
      | two_image_options | image_728x90      | satisfaction_image_leaderboard |
      | two_mrec_options  | image_300x250_gif | satisfaction_image_mrec_gif    |

  @T-UC-FORMAT-SATISFACTION-manifest-ref-required-resolved @format_satisfaction_sync @requires_db
  Scenario Outline: the same manifest naming the option it fits is assigned to it - <declaration> <manifest>
    Given a packaged product declaring canonical format options "<declaration>"
    And the buyer syncs a canonical "image" creative carrying the "<manifest>" manifest and naming product option "<option>"
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error
    And the creative is stored with its format_option_ref naming product option "<option>"

    Examples:
      | declaration       | manifest          | option                         |
      | two_image_options | image_300x250     | satisfaction_image_mrec        |
      | two_image_options | image_728x90      | satisfaction_image_leaderboard |
      | two_mrec_options  | image_300x250_gif | satisfaction_image_mrec_gif    |

    # format_option_ref IS REQUIRED WHEN THE PRODUCT PUBLISHES A KIND MORE THAN ONCE.
    # canonical-formats.mdx, "Routing rule for multi-element format_options (normative)":
    # "format_option_ref is **REQUIRED** on the manifest when the target product's
    # format_options contains two or more declarations sharing the same format_kind — without
    # it, the seller can't disambiguate which option the buyer is shipping against", and
    # "**OPTIONAL** when each format_kind in the product's format_options is unique".
    # core/creative-asset.json and core/creative-manifest.json say the same of the field, and
    # sync_creatives.mdx: "Required when the target product has multiple options with the
    # same format_kind". creative-manifests.mdx § Exact option routing: "When a product
    # accepts multiple declarations of the same kind, or when the creative must target an
    # exact publisher format, add format_option_ref". § Validation step 1, "otherwise select
    # the unambiguous compatible format_kind declaration", is the optional case read with
    # them: the kind alone names one declaration, which must be compatible. Fitting one of
    # two options does not stand in for naming it, and step 5 is "do not silently relax the
    # target contract". Step 1 read alone can be taken to count only the declarations the
    # assets fit, which would accept every refusal row below; the normative rule counts the
    # product's declarations, and adcontextprotocol/adcp#8625 asks for step 1 to say so.
    #
    # Each refusal row has the accept row below it differing only in the ref: each manifest
    # fits exactly one option, the 300x250 and the 728x90 one size each and the gif the gif
    # option, so a seller that picked the one compatible declaration accepts all three
    # refusal rows. The refusal names the option the manifest fits, the ref the buyer can send
    # and be accepted. A manifest fitting none is refused for its assets (the 160x600 row of
    # @T-UC-FORMAT-SATISFACTION-manifest-sync-refused): it fails whichever option is named.
    #
    # The pin names no code for a missing ref. CREATIVE_REPRESENTATION_UNRESOLVED is about a
    # CreativeRepresentationSet and its details MUST carry one rejection per representation
    # id, which a plain manifest has none of; the generic creative code is the one the enum
    # reserves for a buyer-correctable creative failure no specific code names.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/creative-asset.json pointer=/properties/format_option_ref
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/creative-manifest.json pointer=/properties/format_option_ref
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product-format-declaration.json pointer=/properties/format_option_id
    # @source repo=adcp ref=v3.2.7 path=docs/creative/canonical-formats.mdx section="Worked example — multi-format product (third-party html5 OR internal display_tag)"
    # @source repo=adcp ref=v3.2.7 path=docs/creative/creative-manifests.mdx section="Exact option routing"
    # @source repo=adcp ref=v3.2.7 path=docs/creative/creative-manifests.mdx section="Validation"
    # @source repo=adcp ref=v3.2.7 path=docs/creative/task-reference/sync_creatives.mdx
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/error-details/creative-representation-unresolved.json

  @T-UC-FORMAT-SATISFACTION-manifest-library-accepted @format_satisfaction_sync @requires_db
  Scenario: sync_creatives stores a library creative that fills the slots its kind requires
    Given the buyer syncs a canonical "image" creative carrying the "image_300x250" manifest
    When the Buyer Agent syncs it into the library
    Then the creative is stored carrying canonical kind "image"

  @T-UC-FORMAT-SATISFACTION-manifest-library-slot-missing @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives refuses a library creative that does not fill its kind's slots - <manifest>
    Given the buyer syncs a canonical "image" creative carrying the "<manifest>" manifest
    When the Buyer Agent syncs it into the library
    Then the creative is reported failed with CREATIVE_VALIDATION_FAILED_GENERIC and nothing is stored

    Examples:
      | manifest           |
      | image_unnamed      |
      | image_main_as_text |

    # A SYNC THAT ASSIGNS NOTHING SELECTS NO DECLARATION, so the manifest is held to the
    # canonical format its format_kind names: formats/canonical/image.json's slots default
    # requires image_main, an image. image_unnamed keys its image as `banner`, which no slot
    # names, and image_main_as_text fills the slot with a text asset. The accept row above
    # differs from both in that one asset. The refusal is per creative -- the batch completes
    # and the entry fails -- and nothing is written.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/image.json pointer=/properties/slots/default

  @T-UC-FORMAT-SATISFACTION-manifest-library-kind-unknown @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives refuses a creative whose format_kind is outside the pinned vocabulary - <kind>
    Given the buyer syncs a canonical "<kind>" creative carrying the "image_300x250" manifest
    When the Buyer Agent syncs it into the library
    Then the creative is reported failed with INVALID_REQUEST at "format_kind" and nothing is stored

    Examples:
      | kind          |
      | display_image |
      | video         |

    # "REJECT UNKNOWN SELECTIONS" (creative-manifests.mdx § Validation step 5) starts at the
    # kind. core/creative-asset.json types format_kind as core/canonical-format-kind.json, an
    # enum the pin calls "closed for producer validation: sellers do not mint ad-hoc values",
    # and "SDKs may retain unknown future values in tolerant in-memory parsers, but that does
    # not change versioned JSON Schema validation". display_image is the v1 registry's
    # canonical name for a sized display image and video is an asset type; neither is one of
    # the sixteen kinds. The accept row is @T-UC-FORMAT-SATISFACTION-manifest-library-accepted:
    # the same manifest under kind image, stored.
    #
    # THE CODE is INVALID_REQUEST, which enums/error-code.json gives to a request that
    # "violates schema constraints"; VALIDATION_ERROR is for violations "beyond schema
    # validation", and an out-of-enum value is a schema violation. The refusal is per item:
    # the batch completes, the entry fails naming format_kind, and nothing is written.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/canonical-format-kind.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/creative-asset.json pointer=/properties/format_kind
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json

  @T-UC-FORMAT-SATISFACTION-legacy-manifest-library-accepted @format_satisfaction_sync @requires_db
  Scenario: sync_creatives stores a legacy creative carrying every asset its named format requires
    Given the buyer syncs a creative identified by format_id "display_300x250_image" carrying the "complete" manifest
    When the Buyer Agent syncs it into the library
    Then the creative is stored carrying canonical kind "image"

  @T-UC-FORMAT-SATISFACTION-legacy-manifest-library-missing @format_satisfaction_sync @requires_db
  Scenario Outline: sync_creatives refuses a legacy creative missing an asset its named format requires - <manifest>
    Given the buyer syncs a creative identified by format_id "display_300x250_image" carrying the "<manifest>" manifest
    When the Buyer Agent syncs it into the library
    Then the creative is reported failed with <code> at "creatives[0]" and nothing is stored
    And the entry names "<path>" missing where "<accepted>" is accepted

    Examples:
      | manifest             | code                               | path                | accepted |
      | banner_image_missing | CREATIVE_VALIDATION_FAILED_GENERIC | assets.banner_image | image    |
      | click_url_missing    | CREATIVE_MISSING_CLICK_URL         | assets.click_url    | url      |

  @T-UC-FORMAT-SATISFACTION-legacy-manifest-library-wrong-type @format_satisfaction_sync @requires_db
  Scenario: sync_creatives refuses a legacy creative filling a required asset with another asset type
    Given the buyer syncs a creative identified by format_id "display_300x250_image" carrying the "banner_image_as_text" manifest
    When the Buyer Agent syncs it into the library
    Then the creative is reported failed with CREATIVE_VALIDATION_FAILED_GENERIC at "creatives[0]" and nothing is stored
    And the entry names "assets.banner_image.asset_type" stating "text" where "image" is accepted

    # THE DEPRECATED BRANCH IS HELD TO ITS NAMED FORMAT. A creative naming a format_id keys its
    # assets by that format's own asset ids, and core/format.json says each asset_id "must be
    # used as the key in creative manifests" and that "Required assets must be provided for a
    # valid creative". creative-manifests.mdx § Validation steps 2 and 3, "Confirm all required
    # slots are present" and "Validate each asset against its declared type", apply to it at
    # sync_creatives, where "Sellers perform final acceptance". The declaration is the format
    # the agent serves: display_300x250_image declares banner_image (image) and click_url (url),
    # both required. Every refusal row differs from the accept row in one asset, so a seller
    # that does not read the named format's assets accepts all three. The refusal is per
    # creative -- the batch completes and the entry fails -- and nothing is written.
    #
    # THE CODE is the most specific one enums/error-code.json gives the failure, because
    # CREATIVE_VALIDATION_FAILED_GENERIC's own text says "Producers SHOULD prefer a specific
    # creative code whenever one applies". click_url is the format's click destination -- the
    # vocabulary registry (core/asset-group-vocabulary.json) lists it as an alias of
    # landing_page_url, "Click-through destination URL for the ad" -- so leaving it out is
    # CREATIVE_MISSING_CLICK_URL: "A submitted creative that requires a destination URL does
    # not provide one". A missing image and a mistyped asset have no specific code and take the
    # generic one, as a canonical manifest that does not fill its kind's slots does
    # (@T-UC-FORMAT-SATISFACTION-manifest-library-slot-missing).
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format.json pointer=/$defs/baseIndividualAsset/properties/asset_id
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format.json pointer=/$defs/baseIndividualAsset/properties/required
    # @source repo=adcp ref=v3.2.7 path=docs/creative/creative-manifests.mdx section="Validation"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json property=CREATIVE_VALIDATION_FAILED_GENERIC
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json property=CREATIVE_MISSING_CLICK_URL
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/asset-group-vocabulary.json pointer=/vocabulary/landing_page_url

  @T-UC-FORMAT-SATISFACTION-manifest-legacy-only-product-accepted @format_satisfaction_sync @requires_db
  Scenario: a canonical creative filling its kind's slots is assigned to a product publishing no declaration
    Given a packaged product declaring only the deprecated format_ids
    And the buyer syncs a canonical "image" creative carrying the "image_300x250" manifest
    When the Buyer Agent assigns it to that package
    Then the assignment is recorded with no assignment error

  @T-UC-FORMAT-SATISFACTION-manifest-legacy-only-product-refused @format_satisfaction_sync @requires_db
  Scenario: a canonical creative missing its kind's slot is refused by a product publishing no declaration
    Given a packaged product declaring only the deprecated format_ids
    And the buyer syncs a canonical "image" creative carrying the "image_unnamed" manifest
    When the Buyer Agent assigns it to that package
    Then the creative is refused CREATIVE_VALIDATION_FAILED_GENERIC
    And the refusal names "assets.image_main" missing where "image" is accepted

    # AN ASSIGNMENT THAT SELECTS NO DECLARATION STILL GRADES THE MANIFEST. A product
    # publishing only the deprecated format_ids has no format_options[] entry for the
    # canonical creative to select, so there is no declaration to hold it to -- and before
    # this pair the manifest was graded nowhere: the same `banner`-keyed image the library
    # sync refuses was assigned. It is held to its kind, as a sync that assigns nothing is:
    # formats/canonical/image.json's slots default requires image_main. The two scenarios
    # differ only in that one asset.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/creative-manifests.mdx section="Validation"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/image.json pointer=/properties/slots/default
