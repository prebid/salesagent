# Manual feature: what a stored canonical format declaration looks like when a buyer
# discovers it. Sibling of BR-UC-GET-PRODUCTS-pricing-options.feature and built the same
# way — every scenario stores a REAL products row and reads the REAL wire.

Feature: Canonical format options a seller stores are announced to buyers on the wire
  As a Buyer Agent
  I want each of the seller's canonical format declarations to reach me whole
  So that I can tell which canonical shapes a product accepts without a named-format lookup

  # core/product.json: "Products MUST carry format_ids, format_options, or BOTH" — a root
  # anyOf. This seller carried only the deprecated half; format_options had zero references
  # under src/, so a product could describe itself to a 3.x peer and not at all to a
  # canonical one. These scenarios grade the half that was unrepresentable.
  #
  # The field that makes the whole arrangement work is v1_format_ref. The projection between
  # the two identities runs ONE WAY — registries/v1-canonical-mapping.json is "authoritative
  # for v1 -> v2 projection only" and a seller MUST NOT synthesize a v1 format_id by
  # inverting a structural match — so a canonical declaration that wants a legacy projection
  # has to ASSERT one. That turns serving a legacy peer into a lookup instead of a synthesis,
  # and it is only worth anything if the assertion survives to the buyer, which is what the
  # first scenario grades.
  #
  # The deprecated arm is DERIVED from those declarations and from nothing else.
  # canonical-formats.mdx § "Deprecated named-format compatibility projection (normative)",
  # Producer rules: "Canonical format_options are the source of truth. Compatibility
  # format_ids MUST be derived from them rather than maintained as a second authored
  # catalog." So a product with declarations is announced with format_ids equal to the
  # v1_format_ref values they assert, and with no format_ids key when none asserts one; a
  # stored format_ids column beside the declarations is never served. A product with no
  # declarations at all (the deprecated "Named-format product" branch the 3.x window still
  # admits) serves the arm it authored.
  #
  # A derived arm can still disagree with its declarations when a declaration asserts a ref
  # its params do not narrow. core/product-format-declaration.json on v1_format_ref: "The v2
  # declaration's `params` MUST narrow (be compatible with) each referenced v1 format's
  # `requirements`". Every fixture below therefore asserts a ref its params narrow (a
  # 728x90 declaration for display_728x90_image), except the two not-narrowed scenarios,
  # which store the divergence on purpose: the ref is omitted from format_ids
  # (canonical-formats.mdx Producer rules, "A producer that cannot guarantee agreement MUST
  # omit the legacy compatibility projection") and FORMAT_DECLARATION_DIVERGENT is advised
  # on errors[] with source "producer". Emitting the advisory is the seller's choice: the
  # enum says "a SELLER may self-detect on emit"; this seller does, because the declaration
  # still carries the ref on the wire.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/FORMAT_DECLARATION_DIVERGENT
  #
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product-format-declaration.json
  # @source repo=adcp ref=v3.2.7 path=docs/creative/canonical-formats.mdx
  # @source repo=adcp ref=v3.2.7 path=docs/creative/canonical-formats-migration.mdx
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/registries/v1-canonical-mapping.json
  # @source repo=adcp ref=v3.2.7 path=dist/compliance/3.2.7/protocols/media-buy/scenarios/canonical_formats.yaml

  Background:
    Given a tenant is configured for product discovery

  @T-UC-GET-PRODUCTS-format-options-v1-ref @format_option_announcement @requires_db
  Scenario: a stored declaration reaches the buyer with its v1_format_ref intact
    Given the seller stores a canonical format option for "image" with a v1_format_ref to "display_300x250_image"
    When the buyer requests products
    Then the announced format option declares format_kind "image" and params the seller stored
    And the announced format option carries v1_format_ref naming "display_300x250_image"
    And the announced product's format_ids name exactly "display_300x250_image"
    And the response carries no format-declaration advisory
    # Both declaration halves are asserted because either alone is satisfiable without the
    # other. A declaration that arrived with its format_kind and params but no v1_format_ref
    # would pass the first outcome while having lost the one field the legacy projection is
    # derived from.
    #
    # The fixture is DUAL-authored (the factory's legacy column plus the declaration), and
    # the two name the same format, so this is the case where an authored arm and a derived
    # one look identical on the wire. The scenarios below separate them.

  @T-UC-GET-PRODUCTS-format-options-ref-projected @format_option_announcement @requires_db
  Scenario: a canonically-authored product's asserted v1_format_ref is projected to format_ids
    Given the seller stores a canonically-authored product for "image" asserting a v1_format_ref to "display_300x250_image"
    When the buyer requests products
    Then the announced format option carries v1_format_ref naming "display_300x250_image"
    And the announced product's format_ids name exactly "display_300x250_image"
    And the response carries no format-declaration advisory
    # The product stores NO legacy column, so the format_ids the buyer receives can only
    # have come from the declaration: core/product.json on format_ids, "sellers MAY
    # additionally project those declarations to `format_ids` for legacy buyers", and the
    # projection runs from the seller-asserted v1_format_ref only
    # (registries/v1-canonical-mapping.json: v2 -> v1 "MUST rely on `v1_format_ref`").

  @T-UC-GET-PRODUCTS-format-options-no-ref-not-served @format_option_announcement @requires_db
  Scenario: a dual-authored product whose declarations assert no v1_format_ref is announced with no format_ids
    Given the seller stores a dual-authored product whose format_ids names "display_300x250_image" and whose declaration asserts no v1_format_ref
    When the buyer requests products
    Then the announced format option declares format_kind "image" and params the seller stored
    And the announced product carries no format_ids key at all
    And the response carries no format-declaration advisory
    # The stored legacy column is a second authored catalog, which the producer rules
    # forbid serving: "Compatibility format_ids MUST be derived from them rather than
    # maintained as a second authored catalog." Derived from a declaration that asserts no
    # ref, the projection is empty, so the key is absent: the projection runs from
    # v1_format_ref only, and registries/v1-canonical-mapping.json forbids synthesizing a v1
    # format_id by inverting a structural match.

  @T-UC-GET-PRODUCTS-format-options-undeterminable @format_option_announcement @requires_db
  Scenario: a custom declaration the strict format type refuses is served, not turned into an outage
    Given the seller stores a dual-authored product whose declaration is a custom format with no format_shape
    When the buyer requests products
    Then the announced format option declares format_kind "custom" and params the seller stored
    And the announced product carries no format_ids key at all
    And the response carries no format-declaration advisory
    # THE REGRESSION TEST for salesagent-1d5z4: `format_kind: "custom"` with no
    # `format_shape` is accepted by the sixteen-arm ProductFormatDeclaration union the DTO
    # declares and refused by adcp.types.canonical_creative.Format. Reading the declaration
    # through the stricter type once answered INTERNAL_ERROR for every product of the tenant.
    #
    # DUAL-authored, so the read path has a stored legacy column it could fall back to.
    # It must not: the declaration is the source of truth and asserts no v1_format_ref, so
    # there is no legacy projection (canonical-formats.mdx, Producer rules: for custom
    # declarations producers "MUST NOT synthesize a v1 `format_id`"). The first outcome is
    # what makes the scenario falsifiable against an outage: a seller that raised returns no
    # products at all.

  @T-UC-GET-PRODUCTS-format-options-profile-linked @format_option_announcement @requires_db
  Scenario: a canonical product linked to a legacy-only profile is announced from its own declarations
    Given the seller stores a canonically-authored product linked to an inventory profile whose format_ids names "display_300x250_image", asserting a v1_format_ref to "display_728x90_image"
    When the buyer requests products
    Then the announced format option carries v1_format_ref naming "display_728x90_image"
    And the announced product's format_ids name exactly "display_728x90_image"
    And the response carries no format-declaration advisory
    # The profile carries only a legacy catalog and the product carries canonical
    # declarations. The declarations are the source of truth, so the legacy arm is derived
    # from them and the profile's independently authored format_ids is not served beside
    # them: canonical-formats-migration.mdx § Compatibility projection, "Never merge
    # independently authored legacy constraints back into the canonical catalog. That
    # creates two sources of truth". Serving the profile's arm here, with an advisory naming
    # the profile, was salesagent-ym571's resolution of a disagreement the seller authored.
    #
    # The declaration is a 728x90 image, the params that narrow display_728x90_image. It
    # used to store 300x250 for that ref, a divergent dual emission this scenario graded as
    # correct; the outcomes are unchanged because the declaration now agrees with its ref.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product-format-declaration.json pointer=/properties/v1_format_ref

  @T-UC-GET-PRODUCTS-format-options-profile-canonical @format_option_announcement @requires_db
  Scenario: a product linked to a profile with canonical declarations is announced from the profile
    Given the seller stores a product linked to an inventory profile whose canonical declaration asserts a v1_format_ref to "display_728x90_image"
    When the buyer requests products
    Then the announced format option carries v1_format_ref naming "display_728x90_image"
    And the announced product's format_ids name exactly "display_728x90_image"
    And the response carries no format-declaration advisory
    # A linked profile supplies the product's format contract, as it supplies its publisher
    # properties. The product's own columns hold a different declaration and a different
    # legacy arm (both "display_300x250_image"), so a seller that read the product's columns,
    # or served a stored column beside the profile's declarations, fails an outcome. Each
    # declaration's params narrow the ref it asserts (728x90 on the profile, 300x250 on the
    # product), so no divergence is stored.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product-format-declaration.json pointer=/properties/v1_format_ref

  @T-UC-GET-PRODUCTS-format-options-authored-arm-not-served @format_option_announcement @requires_db
  Scenario Outline: a stored format_ids column the declarations do not assert is not served - <case>
    Given the seller stores a dual-authored product whose format_ids names "<authored>" and whose declaration asserts "<asserted>"
    When the buyer requests products
    Then the announced format option declares format_kind "image" and params the seller stored
    And the announced product's format_ids name exactly "<asserted>"
    And the response carries no format-declaration advisory
    # A row authored before the canonical arm existed and then given declarations (the
    # admin edit form writes format_options on such a row) holds two catalogs. Only one is
    # the source of truth: "Compatibility format_ids MUST be derived from them rather than
    # maintained as a second authored catalog" (canonical-formats.mdx, Producer rules). The
    # announced arm is exactly what the declaration asserts, whatever the column holds.
    #
    # The superset row is the case a one-way containment check passed: every asserted ref is
    # in the column, so the column used to be served whole, extra entry included. In the
    # disagreeing row the COLUMN disagrees with the declaration, which is a 728x90 image
    # narrowing the display_728x90_image it asserts: the column is not served, the derived
    # arm agrees with the declaration, and there is nothing to advise. The declaration used
    # to store 300x250 for that ref, which is the divergence the not-narrowed scenarios
    # below grade, not this one.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product-format-declaration.json pointer=/properties/v1_format_ref

    Examples:
      | case        | authored                                    | asserted             |
      | superset    | display_300x250_image, display_728x90_image | display_300x250_image |
      | disagreeing | display_300x250_image                       | display_728x90_image  |

  @T-UC-GET-PRODUCTS-format-options-not-narrowed @format_option_announcement @requires_db
  Scenario: a v1_format_ref its declaration does not narrow is left out of format_ids and advised
    Given the seller stores a canonically-authored product whose "image" declaration of 300x250 asserts v1_format_refs to "display_728x90_image"
    When the buyer requests products
    Then the announced format option declares format_kind "image" and params the seller stored
    And the announced format option carries v1_format_ref naming "display_728x90_image"
    And the announced product carries no format_ids key at all
    And the response carries one FORMAT_DECLARATION_DIVERGENT advisory from the producer naming "products[0]"
    And the advisory details name the product and the unnarrowed "display_728x90_image" of "display_728x90_image"
    # A 300x250 image asserting display_728x90_image breaks the MUST on v1_format_ref ("The
    # v2 declaration's `params` MUST narrow ... each referenced v1 format's
    # `requirements`"): the ref normalizes to an image of 728x90, and canonical-formats.mdx
    # § "Narrows" calls a v2 value outside the v1 constraint "a conflict, not narrowing".
    # Projected, the legacy arm would name a format the declaration is not, so it is
    # omitted ("A producer that cannot guarantee agreement MUST omit the legacy
    # compatibility projection"). The product is still served, whole, on a populated
    # success: "Hard-failing the entire get_products response is discouraged". The
    # declaration is served as stored, ref included, so the advisory is what tells a buyer
    # why the two shapes differ; error-code.json makes errors[] its single surface, field
    # the offending product, and details { product_id, format_ids, format_options_summary,
    # divergence_reason }. errors[0] is asserted because the pin's
    # get_seeded_divergent_product step reads errors[0].code and errors[0].source; that
    # storyboard requires the comply controller, so it grades nothing here and this
    # scenario is the grader.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product-format-declaration.json pointer=/properties/v1_format_ref
    # @source repo=adcp ref=v3.2.7 path=docs/creative/canonical-formats.mdx section="Producer rules"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/FORMAT_DECLARATION_DIVERGENT

  @T-UC-GET-PRODUCTS-format-options-not-narrowed-partial @format_option_announcement @requires_db
  Scenario: only the v1_format_ref a declaration does not narrow is left out of format_ids
    Given the seller stores a canonically-authored product whose "image" declaration of 300x250 asserts v1_format_refs to "display_300x250_image, display_728x90_image"
    When the buyer requests products
    Then the announced format option carries v1_format_ref naming "display_300x250_image, display_728x90_image"
    And the announced product's format_ids name exactly "display_300x250_image"
    And the response carries one FORMAT_DECLARATION_DIVERGENT advisory from the producer naming "products[0]"
    And the advisory details name the product and the unnarrowed "display_728x90_image" of "display_300x250_image, display_728x90_image"
    # SELLER CHOICE, recorded: the omission is per reference. The pin's MUST omits "the
    # legacy compatibility projection" a producer cannot guarantee agreement for, and the
    # ref the declaration narrows still names the same underlying format, so it is
    # projected; only display_728x90_image, which it does not narrow, is dropped. A seller
    # that dropped the whole arm fails the second outcome, and one that kept the divergent
    # ref fails it too.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/canonical-formats.mdx section="Producer rules"

  @T-UC-GET-PRODUCTS-format-options-absent @format_option_announcement @requires_db
  Scenario: a product declaring no format options omits the key and keeps its authored format_ids
    Given the seller stores a product with no canonical format options
    When the buyer requests products
    Then the announced product carries no format_options key at all
    And the announced product's format_ids name exactly "display_300x250_image"
    # The deprecated "Named-format product" branch of core/product.json's anyOf, which the
    # 3.x window still admits for a pre-existing row. Its format_ids is its only catalog,
    # not a projection beside a canonical one, so it is served as authored. It must not be
    # answered with format_options: null — an explicitly null member says "declared, and
    # empty", which is a different claim from "not declared".

  @T-UC-GET-PRODUCTS-format-options-canonical-only @format_option_announcement @requires_db
  Scenario: a canonically-authored product is announced with its declarations and no format_ids
    Given the seller stores a canonically-authored product for "image" with no format_ids
    When the buyer requests products
    Then the announced format option declares format_kind "image" and params the seller stored
    And the announced product carries no format_ids key at all
    # The exact MIRROR of the scenario above, and the shape 3.2 tells new products to use:
    # core/product.json's root anyOf has a "Canonical-format product" branch, format_ids is
    # "Deprecated in AdCP 3.2; removed in AdCP 4.0", and it is absent from the root
    # `required` list. So a product declaring only format_options is legal and must not be
    # answered with format_ids at all.
    #
    # Omission is the whole point, and an EMPTY array would be worse than absence: the
    # deprecated anyOf branch requires only the KEY, so `format_ids: []` satisfies that
    # branch by presence while declaring no format whatever. That is the bug this grades.
    #
    # The first outcome is what stops it passing vacuously. A response that dropped BOTH
    # halves would satisfy "no format_ids key" and leave the product undescribed -- the
    # same trap the sibling scenario above guards with its second outcome, in the other
    # direction.
    #
    # Two things had to change for this to be representable at all: products.format_ids
    # was NOT NULL until migration 08cd415b3d91, and product_conversion REFUSED a product
    # whose legacy arm was absent -- so the canonical shape was unstorable and then
    # unservable.

  @T-UC-GET-PRODUCTS-format-options-operator-authored @format_option_announcement @requires_db
  Scenario: a product the operator creates through the admin form carries its contract in format_options
    Given the operator creates a product through the admin product form declaring a canonical "image" format option
    When the buyer requests products
    Then the announced format option declares format_kind "image" and params the seller stored
    And the announced product carries no format_ids key at all
    # canonical-formats-migration.mdx § Seller migration, step 1 (v3.2.7): "Move each
    # product's creative contract into format_options[]". core/product.json format_ids
    # (v3.2.7): "New products MUST author canonical format_options[] ... Do not author a new
    # product from format_ids alone."
    #
    # Every other scenario here seeds its row through ProductFactory, so they grade what
    # the seller SERVES from a row, never where the seller's own authoring PUTS the
    # contract. This Given posts the admin product-create form, on the scenario's own
    # transport: the in-process admin app for a2a, mcp and rest, and the live server's
    # admin for e2e_rest, so the product the buyer discovers is the one the operator made.
    #
    #   * a create path that wrote format_ids and dropped the declaration fails the first
    #     outcome, because nothing would be announced in format_options;
    #   * a create path that synthesized a format_id from the kind fails the second, since
    #     the declaration asserts no v1_format_ref and so projects no format_ids;
    #   * a form that refused the canonical-only product fails in the Given, which asserts
    #     the create redirected.
    #
    # The seller's other authoring paths (CREATE_SAMPLE_DATA seeding, the mock adapter's
    # product config, inventory profiles, the setup scripts) are not graded here.

  # ── The operator authors a multi-publisher product without placements ────────────────
  #
  # core/product.json format_options (v3.2.7), addressed to the product's author: "If a
  # product spans multiple publishers but omits placements[], there is no public routing key
  # for per-placement creatives: its format_options MUST therefore be the common intersection
  # accepted across every selected publisher/property scope. A seller that needs a union of
  # publisher-specific formats MUST publish placements[] with publisher-scoped identities and
  # narrowing." An option scoped to one publisher (core/product-format-declaration.json
  # publisher_domain) is accepted only in that publisher's scope (formats.mdx § Matching and
  # satisfaction: "publisher-scoped options only to their publisher"), so it is outside the
  # intersection, and the seller refuses to author it. The buyer-visible effect is the
  # product's absence from get_products.
  #
  # Both scenarios post the same admin form on the scenario's own transport: two property
  # tags of two different publishers, no placements, a product-local 300x250 option and a
  # 728x90 option. They differ in one thing, whether the 728x90 is scoped to the first
  # publisher. A seller that did not hold its author to the intersection stores and announces
  # the refused product; one that refused every multi-publisher product reddens the accept.
  #
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/format_options/description
  # @source repo=adcp ref=v3.2.7 path=docs/creative/formats.mdx

  @T-UC-GET-PRODUCTS-format-options-operator-common-intersection-accepted @format_option_announcement @requires_db
  Scenario: the operator's multi-publisher product whose options every publisher accepts is announced
    Given the operator submits the admin product form for a product sold across two publishers declaring a "product-local" leaderboard option beside a product-local one
    When the buyer requests products
    Then the buyer is announced the operator's product with both options

  @T-UC-GET-PRODUCTS-format-options-operator-common-intersection-refused @format_option_announcement @requires_db
  Scenario: the operator's multi-publisher product with an option scoped to one publisher is refused
    Given the operator submits the admin product form for a product sold across two publishers declaring a "publisher-scoped" leaderboard option beside a product-local one
    When the buyer requests products
    Then the admin form refuses the operator's product and the buyer is not announced it

  @T-UC-GET-PRODUCTS-format-options-several @format_option_announcement @requires_db
  Scenario: a product declaring several format options announces every one of them
    Given the seller stores a canonically-authored product declaring three format options
    When the buyer requests products
    Then the announced format options are exactly the three declarations the seller stored
    And the announced product's format_ids name exactly "display_300x250_image, display_728x90_image"
    And the response carries no format-declaration advisory
    # canonical-formats-migration.mdx § Seller migration, step 4 (v3.2.7): "Return the
    # applicable options from get_products". core/product.json format_options: "one or more
    # inline format declarations the product accepts". Every other scenario here stores ONE
    # declaration, so a seller that announced only the first, or collapsed declarations of
    # one kind into one, passed them all.
    #
    # The three options are product-local, share format_kind "image" and differ in
    # format_option_id and size:
    #
    #   * announcing only the first option fails the count;
    #   * collapsing same-kind options into one fails the count;
    #   * dropping format_option_id fails the identity comparison, and it is the member a
    #     buyer names in format_option_refs to select an option;
    #   * deriving format_ids from the first declaration only fails the second outcome,
    #     which needs refs from two declarations and none from the third, which asserts
    #     no v1_format_ref.
    #
    # The pin gives format_options no order, so options are compared by format_option_id.
    # The derived format_ids are compared in declared order. That order is this seller's
    # choice (src/core/format_resolver.py, CanonicalFormats.format_ids); the pin does not
    # order format_ids either.

  @T-UC-GET-PRODUCTS-format-options-unannounceable @format_option_announcement @requires_db
  Scenario Outline: a stored declaration the seller cannot announce is refused, not half-announced
    # core/product-format-declaration.json puts format_kind and params in `required`. A
    # stored row missing either cannot become a valid declaration, and no database
    # constraint covers the contents of this JSON column, so the seller really can hold one.
    #
    # INTERNAL_ERROR, on the same reasoning the sibling pricing scenarios use: it is the
    # seller's own data that is wrong and there is nothing the buyer can correct. Emitting a
    # declaration that would fail the buyer's own schema check is the alternative, and it is
    # worse.
    Given the seller stores a canonical format option <missing a required member>
    When the buyer requests products
    Then the response contains error code INTERNAL_ERROR

    Examples: stored declarations that cannot be announced
      | missing a required member |
      | with no params            |
      | with no format_kind       |

  @T-UC-GET-PRODUCTS-format-options-no-arm @format_option_announcement @requires_db
  Scenario: a product declaring neither format arm is refused, not served undescribed
    # core/product.json's root anyOf: "Products MUST carry `format_ids`, `format_options`,
    # or both during the 3.x migration window". Each half is individually optional, so the
    # one state the MUST forbids is the CONJUNCTION of their absence -- which is why it is
    # the easiest clause in this feature to leave ungraded. The two scenarios above store a
    # product missing one arm in each direction and both are legal; neither can see this.
    #
    # THE SAME CHECK USED TO FIRE ON AN ABSENT format_ids ALONE, which made the canonical
    # shape unservable -- the defect the canonical-only scenario above exists to prevent
    # regressing. The two are a pair: that one asserts ONE arm is enough, this one asserts
    # ZERO arms is not, and a seller satisfying either alone is wrong in a way the other
    # scenario cannot report.
    #
    # INTERNAL_ERROR on the same reasoning as the outline above: it is the seller's own row
    # that is wrong and there is nothing the buyer can correct.
    #
    # WHAT THIS GRADES IS THE OBLIGATION, NOT ONE REFUSAL, and that distinction was measured
    # rather than reasoned. The anyOf is enforced TWICE, in order: product_conversion's own
    # check fires first (its message names both arms and the inventory profile, which is the
    # operator-facing value it adds), and the PINNED MODEL backstops it -- disabling the
    # application check leaves the row refused by the generated Product itself, verbatim
    # "Product requires at least one of these field groups: Named-format product |
    # Canonical-format product [type=anyOf]". So this scenario cannot attribute the refusal
    # to either layer, and it does not try to; what it asserts is that a product declaring
    # neither arm never reaches a buyer.
    #
    # FIRE-PROBED at the producer of the value the Then actually reads -- the error code,
    # minted where products.py turns a conversion failure into AdCPInternalError. Making
    # that branch skip the product instead reddens this row on all three in-process
    # transports AND the six rows of the outline above, which share that producer, while the
    # other 21 stay green. Probing the application's anyOf check instead reddens NOTHING,
    # because of the backstop above -- recorded because that is the probe a reader would
    # reach for first, and it proves the wrong thing.
    #
    # The fixture seeds a pricing option deliberately: a product with none is refused by the
    # same conversion with the same envelope, and no raise site authors buyer-visible text,
    # so the wire cannot tell those causes apart either. That cause is excluded BY
    # CONSTRUCTION rather than by the Then.
    Given the seller stores a product declaring neither format arm
    When the buyer requests products
    Then the response contains error code INTERNAL_ERROR
