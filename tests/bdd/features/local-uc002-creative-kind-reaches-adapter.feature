# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# What it grades: what the seller hands its ad server for each creative kind a buyer can
# send, read at the adapter boundary. AdCP 3.2.7 docs/creative/channels/display.mdx:
# "`display_tag` describes a third-party-served representation. Keep them separate because
# validation, hosting, security, and measurement differ. Inline third-party markup is never
# an `html5` bundle." Its three delivery types: `tag_url` is "One `url` asset with
# `url_type: "ad_request"`, or a `display_tag` asset with `delivery_type: "tag_url"`";
# `inline_markup` is "One `display_tag` asset with byte-preserved markup and its markup type";
# `paired_redirect` is "One `display_tag` asset containing both `ad_request_url` and
# `clickthrough_url`". core/assets/display-tag-asset.json: of `markup`, "Receivers MUST
# preserve its bytes"; of `clickthrough_url`, "The pair MUST NOT be split".
# core/assets/html-asset.json requires only `asset_type` and `content` (#2390).
# formats/canonical/html5.json: "Interactive HTML5 banner delivered as a zip archive. Slot:
# `html5_bundle` (zip asset)" (#2545). A creative agent's preview page is not serving
# content: preview_creative.mdx says "Every render's `preview_url` returns an untrusted HTML
# page for cross-origin iframe embedding".
#
# The ad server receives each creative as one typed value under the asset's `creative` key:
# its canonical kind, the representation the buyer sent, and the sizes its format option
# declares (#2547). The seller hands it over on three paths, and each is graded here: the
# synchronous create (adapter.add_creative_assets), the seller's approval of a held buy, and
# the retroactive push of a creative approved after its buy went live (both
# adapter.creatives_manager.add_creative_assets).
#
# The retroactive push withholds a misfit creative and pushes the others. The pin permits a
# per-creative failure inside a successful sync: creative/sync-creatives-response.json, "Items
# with action='failed' indicate per-item validation/processing failures, not operation-level
# failures", and sync_creatives.mdx, "`creatives` - Results for each creative processed
# (includes both successful and failed items)". That response is not this push's, though: the
# push graded here follows the seller's approval of a creative in the Admin UI, a request no
# buyer makes and no buyer response answers, and no buyer readback carries a push's outcome
# (the assignment row stays, so the buyer's re-send is pushed again). A buyer's sync assigning
# the same creative is refused at its own assignment validation, before any push, by the
# validator the push asks. So the push is graded at the ad server's creative manager, and the
# seller who approved the creative is told in the Admin UI route's answer which creative was
# not pushed and the refusal's code.
#
# Ungraded by the storyboard runner: the ad server's input is not observable on the buyer's
# wire. Transports: a2a, mcp, rest and e2e_rest. In process the ad server is the env's
# adapter stand-in; on e2e_rest the create path's upload is read off the receipt the live
# server's Mock adapter writes to its audit trail, and the two operator paths run in the test
# process with the stand-in on every transport (MediaBuyCreateEnv.creative_manager_assets).
#
# @source repo=adcp ref=v3.2.7 path=docs/creative/channels/display.mdx
# @source repo=adcp ref=v3.2.7 path=dist/schemas/3.2.7/core/assets/display-tag-asset.json
# @source repo=adcp ref=v3.2.7 path=dist/schemas/3.2.7/formats/canonical/display_tag.json pointer=/properties/sizes
# @source repo=adcp ref=v3.2.7 path=dist/schemas/3.2.7/core/assets/html-asset.json pointer=/required
# @source repo=adcp ref=v3.2.7 path=dist/schemas/3.2.7/core/assets/zip-asset.json
# @source repo=adcp ref=v3.2.7 path=dist/schemas/3.2.7/formats/canonical/html5.json pointer=/properties/slots
# @source repo=adcp ref=v3.2.7 path=docs/creative/task-reference/preview_creative.mdx
# @source repo=adcp ref=v3.2.7 path=docs/creative/task-reference/build_creative.mdx
# @source repo=adcp ref=v3.2.7 path=dist/schemas/3.2.7/enums/error-code.json
# @source repo=adcp ref=v3.2.7 path=dist/schemas/3.2.7/media-buy/package-request.json pointer=/properties/format_option_refs
# @source repo=adcp ref=v3.2.7 path=dist/schemas/3.2.7/creative/sync-creatives-response.json
# @source repo=adcp ref=v3.2.7 path=docs/creative/task-reference/sync_creatives.mdx
# @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/assets/url-asset.json
# @source repo=adcp ref=v3.2.7 path=docs/creative/asset-types.mdx
# @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/display_tag.json pointer=/properties/supported_delivery_types
# @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/representation-rejection.json
Feature: The ad server receives each creative as its canonical kind and the representation the buyer sent (local)

  @T-UC-002-local-creative-kind-inline-markup @extension @creative
  Scenario: Inline third-party markup reaches the ad server byte for byte, and the creative agent's preview page does not
    Given a valid create_media_buy request
    And the account exists and is active
    And the product publishes a display_tag format option for inline_markup delivery fixed at 300x250
    And the package references the buyer's approved display_tag creative "cr-inline" carrying its byte-sensitive inline iframe_javascript markup, recorded with the creative agent's preview page "https://creative.adcontextprotocol.org/preview/cr-inline/desktop.html" as its url at 300x250
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the ad server receives creative "cr-inline" of kind "display_tag" as inline iframe_javascript markup, the buyer's markup byte for byte
    And the creative agent's preview page appears nowhere in what the ad server receives for creative "cr-inline"

  @T-UC-002-local-creative-kind-tag-url @extension @creative
  Scenario: A display_tag asset delivered as a tag URL reaches the ad server as that ad request
    Given a valid create_media_buy request
    And the account exists and is active
    And the product publishes a display_tag format option for tag_url delivery fixed at 300x250
    And the package references the buyer's approved display_tag creative "cr-tag" carrying the tag URL "https://ads.example-adserver.com/tag?slot=42&cb=%%CACHEBUSTER%%"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the ad server receives creative "cr-tag" of kind "display_tag" as the ad tag "https://ads.example-adserver.com/tag?slot=42&cb=%%CACHEBUSTER%%"

  # The backward-compatible default: the kind's `tag_url` slot, a url asset carrying its own
  # url_type (formats/canonical/display_tag.json: "The backward-compatible default slot remains
  # `tag_url` (`url_type: ad_request`)").
  @T-UC-002-local-creative-kind-legacy-tag-url @extension @creative
  Scenario: A url asset in the display_tag kind's tag_url slot, declaring url_type ad_request, reaches the ad server as an ad request, not an image
    Given a valid create_media_buy request
    And the account exists and is active
    And the product publishes a display_tag format option on the kind's default slots fixed at 300x250
    And the package references the buyer's approved display_tag creative "cr-legacy-tag" whose tag_url slot carries "https://ads.example-adserver.com/legacy?cb=%%CACHEBUSTER%%" with url_type "ad_request"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the ad server receives creative "cr-legacy-tag" of kind "display_tag" as the ad tag "https://ads.example-adserver.com/legacy?cb=%%CACHEBUSTER%%"

  # F8(b) (PR #2582 review): the same url asset with no url_type is not invoked as an ad request
  # for sitting in the kind's tag_url slot. The pin rules that fallback out. AdCP
  # 3.2.7 core/assets/url-asset.json: "third_party_tracker and display ad requests have no safe
  # fallback. When neither `url_type` nor a format-side role is available, receivers MUST NOT
  # silently pick a mechanism." docs/creative/asset-types.mdx:203: "Display ad-request URLs also
  # have no role-based fallback. Declare `url_type: "ad_request"` explicitly." The display_tag
  # slot's "MUST use `url_type: ad_request`" obliges the sender. So the creative carries nothing
  # this seller can serve, which the create gate answers VALIDATION_ERROR before the ad server
  # is handed the buy, as it does for @T-UC-002-local-creative-kind-generative-unbuilt.
  @T-UC-002-local-creative-kind-legacy-tag-url-no-url-type @extension @creative
  Scenario: A url asset in the display_tag kind's tag_url slot with no url_type is refused before the ad server is handed the buy
    Given a valid create_media_buy request
    And the account exists and is active
    And the product publishes a display_tag format option on the kind's default slots fixed at 300x250
    And the package references the buyer's approved display_tag creative "cr-legacy-tag" whose tag_url slot carries "https://ads.example-adserver.com/legacy?cb=%%CACHEBUSTER%%" with no url_type
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail with error code "VALIDATION_ERROR"
    And the ad server is handed no creative
    And the ad server adapter should never be invoked

  # F8(a) (PR #2582 review): nothing grades an option's delivery types, so inline markup is
  # trafficked on an option that traffics only paired redirects. AdCP 3.2.7
  # formats/canonical/display_tag.json `supported_delivery_types`: "Closed set of delivery types
  # this format option can traffic." The pin's one structured code for this,
  # core/representation-rejection.json `unsupported_delivery_type`, is a "Structured
  # incompatibility reason for one candidate in a failed CreativeRepresentationSet
  # resolution", and this creative is one manifest, not a representation set. So it is a
  # manifest shortfall graded where the option's params are, and with no more specific code it
  # takes enums/error-code.json CREATIVE_VALIDATION_FAILED_GENERIC: "The creative failed
  # buyer-correctable validation, but the producer cannot classify the failure with a more
  # specific standard code."
  @T-UC-002-local-creative-kind-unsupported-delivery-type @extension @creative
  Scenario: Inline markup on an option whose only delivery type is paired_redirect is refused before the ad server is handed the buy
    Given a valid create_media_buy request
    And the account exists and is active
    And the product publishes a display_tag format option for paired_redirect delivery fixed at 300x250
    And the package references the buyer's approved display_tag creative "cr-inline-misfit" carrying its byte-sensitive inline iframe_javascript markup
    When the Buyer Agent sends the create_media_buy request
    Then the creative is refused CREATIVE_VALIDATION_FAILED_GENERIC
    And the refusal names "assets.serving_tag.delivery_type" stating "inline_markup" where "paired_redirect" is accepted
    And the ad server is handed no creative
    And the ad server adapter should never be invoked

  @T-UC-002-local-creative-kind-paired-redirect @extension @creative
  Scenario: A paired redirect reaches the ad server as one pair, both URLs intact
    Given a valid create_media_buy request
    And the account exists and is active
    And the product publishes a display_tag format option for paired_redirect delivery fixed at 300x250
    And the package references the buyer's approved display_tag creative "cr-paired" carrying the paired redirect "https://ads.example-adserver.com/ad?slot=42&cb=%%CACHEBUSTER%%" with click-through "https://click.example-adserver.com/redirect?dest=%%DEST_URL_ESC%%"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the ad server receives creative "cr-paired" of kind "display_tag" as one redirect pair of ad request "https://ads.example-adserver.com/ad?slot=42&cb=%%CACHEBUSTER%%" and clickthrough "https://click.example-adserver.com/redirect?dest=%%DEST_URL_ESC%%"

  # #2390. The reference creative agent declares display_300x250_html as kind html5 while its
  # one required asset is inline html, which carries no url and no dimensions.
  @T-UC-002-local-creative-kind-html @extension @creative
  Scenario: An html creative with no url or dimensions is accepted and reaches the ad server as its markup, at its format's size
    Given a valid create_media_buy request
    And the account exists and is active
    And the package references the buyer's approved "display_300x250_html" creative "cr-html" whose only asset is html "<div>Example ad</div>"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the ad server receives creative "cr-html" of kind "html5" as inline html "<div>Example ad</div>"
    And the ad server receives creative "cr-html" sized "300x250"

  # #2545
  @T-UC-002-local-creative-kind-html5-bundle @extension @creative
  Scenario: An html5 creative reaches the ad server as its zip bundle, not as its backup image
    Given a valid create_media_buy request
    And the account exists and is active
    And the product publishes an html5 format option fixed at 300x250
    And the package references the buyer's approved html5 creative "cr-bundle" bundled at "https://cdn.example.com/banner-300x250.zip", with backup image "https://cdn.example.com/banner-backup.png"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the ad server receives creative "cr-bundle" of kind "html5" as the html5 bundle "https://cdn.example.com/banner-300x250.zip" with backup image "https://cdn.example.com/banner-backup.png"

  # F7 (PR #2582 review): a zip asset missing its `url` must not raise inside the gate and
  # answer the buyer an internal error. AdCP 3.2.7
  # core/assets/zip-asset.json requires `url`; a creative whose bundle names none carries
  # nothing an ad server can serve, which the create gate answers VALIDATION_ERROR (enums/
  # error-code.json: "Request contains invalid field values or violates business rules beyond
  # schema validation. Recovery: correctable"), as for
  # @T-UC-002-local-creative-kind-generative-unbuilt.
  @T-UC-002-local-creative-kind-html5-bundle-no-url @extension @creative
  Scenario: An html5 creative whose zip bundle names no url is refused before the ad server is handed the buy
    Given a valid create_media_buy request
    And the account exists and is active
    And the product publishes an html5 format option fixed at 300x250
    And the package references the buyer's approved html5 creative "cr-bundle-no-url" bundled with no url, with backup image "https://cdn.example.com/banner-backup.png"
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail with error code "VALIDATION_ERROR"
    And the ad server is handed no creative
    And the ad server adapter should never be invoked

  @T-UC-002-local-creative-kind-multi-size @extension @creative
  Scenario: A display_tag creative for a multi-size option reaches the ad server with every size the option declares
    Given a valid create_media_buy request
    And the account exists and is active
    And the product publishes a display_tag format option for tag_url delivery accepting sizes "300x250, 728x90"
    And the package references the buyer's approved display_tag creative "cr-multi" carrying the tag URL "https://ads.example-adserver.com/tag?slot=7"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the ad server receives creative "cr-multi" of kind "display_tag" as the ad tag "https://ads.example-adserver.com/tag?slot=7"
    And the ad server receives creative "cr-multi" sized "300x250, 728x90"

  # A responsive option states bounds and no size (formats/canonical/display_tag.json,
  # `min_width`: "Minimum accepted width for responsive third-party tags").
  @T-UC-002-local-creative-kind-responsive @extension @creative
  Scenario: A display_tag creative for a responsive option reaches the ad server with the option's size range and no fixed size
    Given a valid create_media_buy request
    And the account exists and is active
    And the product publishes a display_tag format option for tag_url delivery accepting widths 300 to 970 and heights 90 to 250
    And the package references the buyer's approved display_tag creative "cr-responsive" carrying the tag URL "https://ads.example-adserver.com/tag?slot=9"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the ad server receives creative "cr-responsive" of kind "display_tag" as the ad tag "https://ads.example-adserver.com/tag?slot=9"
    And the ad server receives creative "cr-responsive" with no fixed size, sized within widths 300 to 970 and heights 90 to 250

  # The build output is what build_creative produced to serve, not a preview. The stored row's
  # root url is the creative agent's preview page (F10, PR #2582 review): a reader that fell
  # back to it would hand the ad server the preview.
  @T-UC-002-local-creative-kind-generative-built @extension @creative
  Scenario: A generative creative reaches the ad server as the file its build produced, and the creative agent's preview page does not
    Given a valid create_media_buy request
    And the account exists and is active
    And the package references the buyer's approved generative creative "cr-built" in "display_300x250_generative", built to "https://cdn.example.com/generated/cr-built.png"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the ad server receives creative "cr-built" of kind "image" as image media "https://cdn.example.com/generated/cr-built.png"
    And the creative agent's preview page appears nowhere in what the ad server receives for creative "cr-built"

  # F10 (PR #2582 review): the change that stopped sync copying a preview onto the creative
  # (#2390) was graded only in process. Here the creative arrives through sync: create_media_buy
  # syncs a package's `creatives` through the creative-sync service before it books, against
  # the tenant's own creative agent, which previews it as a page with a size.
  # preview_creative.mdx: "Every render's `preview_url` returns an untrusted HTML page for
  # cross-origin iframe embedding"; core/assets/html-asset.json: the markup is `content`.
  @T-UC-002-local-creative-kind-synced-preview-kept-out @extension @creative
  Scenario: An html creative synced against a previewing creative agent reaches the ad server as its markup, and the preview page does not
    Given a valid create_media_buy request
    And the account exists and is active
    And the tenant has approval_mode "auto-approve"
    And the tenant's own creative agent publishes "display_300x250_html" and previews every creative as its page "https://creative.adcontextprotocol.org/preview/cr-synced/desktop.html", sized 300x250
    And the request's package carries the buyer's "display_300x250_html" creative "cr-synced" whose only asset is html "<div>Synced ad</div>", in that agent's format
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the tenant's own creative agent was asked to preview the html "<div>Synced ad</div>"
    And the ad server receives creative "cr-synced" of kind "html5" as inline html "<div>Synced ad</div>"
    And the creative agent's preview page "https://creative.adcontextprotocol.org/preview/cr-synced/desktop.html" appears nowhere in what the ad server receives for creative "cr-synced"

  @T-UC-002-local-creative-kind-generative-unbuilt @extension @creative
  Scenario: A generative creative with no build output is refused before the ad server is handed the buy
    Given a valid create_media_buy request
    And the account exists and is active
    And the package references the buyer's approved generative creative "cr-unbuilt" in "display_300x250_generative", not built yet
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail with error code "VALIDATION_ERROR"
    And the ad server adapter should never be invoked

  # The two operator paths hand over the same typed creative the create path does, sized by
  # the option the creative resolved to on the buy's product. The multi-size scenario above
  # grades those sizes on the create path. The package names its option because the seller's
  # approval reconstructs each package from the format contract the create recorded.
  @T-UC-002-local-creative-kind-approval-replay @extension @creative
  Scenario: The seller's approval of a held buy hands the ad server the creative as its kind, representation and declared sizes
    Given a valid create_media_buy request
    And the account exists and is active
    And the product publishes a display_tag format option for tag_url delivery accepting sizes "300x250, 728x90"
    And the package references the buyer's approved display_tag creative "cr-held-buy" carrying the tag URL "https://ads.example-adserver.com/tag?slot=11"
    And the package selects that format option by its format_option_ref
    And the tenant requires manual approval
    When the Buyer Agent sends the create_media_buy request
    Then the response status should be "submitted"
    When the seller approves the media buy
    Then the seller's approval books the media buy
    And the ad server's creative manager receives creative "cr-held-buy" of kind "display_tag" as the ad tag "https://ads.example-adserver.com/tag?slot=11"
    And the ad server's creative manager receives creative "cr-held-buy" sized "300x250, 728x90"

  # A package that names no format option records no format contract, and on a product
  # publishing only canonical options (no format_ids) create places it on those options
  # (core/product.json: "New products MUST author canonical format_options[]"). The seller's
  # approval reconstructs the package from the same product, so it books the buy as create
  # would have, and the creative carries the sizes of the option it resolved to.
  @T-UC-002-local-creative-kind-approval-canonical-only @extension @creative
  Scenario: The seller's approval books a held buy whose package names no format option, on a product publishing only canonical options
    Given a valid create_media_buy request
    And the account exists and is active
    And the product publishes no format_ids
    And the product publishes a display_tag format option for tag_url delivery accepting sizes "300x250, 728x90"
    And the package references the buyer's approved display_tag creative "cr-held-canonical" carrying the tag URL "https://ads.example-adserver.com/tag?slot=13"
    And the tenant requires manual approval
    When the Buyer Agent sends the create_media_buy request
    Then the response status should be "submitted"
    When the seller approves the media buy
    Then the seller's approval books the media buy
    And the ad server's creative manager receives creative "cr-held-canonical" of kind "display_tag" as the ad tag "https://ads.example-adserver.com/tag?slot=13"
    And the ad server's creative manager receives creative "cr-held-canonical" sized "300x250, 728x90"

  @T-UC-002-local-creative-kind-retroactive-push @extension @creative
  Scenario: A creative the seller approves after its buy went live reaches the ad server as its kind, representation and declared sizes
    Given the product publishes a display_tag format option for tag_url delivery accepting sizes "300x250, 728x90"
    And the buyer holds a live media buy booked at the ad server as order "order-live" with package "pkg-live"
    And the buyer's creative "cr-late", under review, a display_tag carrying the tag URL "https://ads.example-adserver.com/tag?slot=12", is assigned to package "pkg-live" of the live media buy
    When the seller approves creative "cr-late"
    Then the ad server's creative manager receives creative "cr-late" of kind "display_tag" as the ad tag "https://ads.example-adserver.com/tag?slot=12"
    And the ad server's creative manager receives creative "cr-late" sized "300x250, 728x90"

  # A creative whose size the option does not accept is refused on every path that pushes it
  # (core/product.json format_options, and formats/canonical/image.json `sizes`, a closed set).
  # Create refuses the buy before the ad server is reached, with enums/error-code.json
  # CREATIVE_SIZE_MISMATCH: "The submitted creative dimensions do not match any size accepted
  # by the selected packages. Sellers SHOULD identify the offending creative in error.field
  # ... Recovery: correctable". The request names the creative at packages[0].creative_ids[0].
  # The seller's approval of a held buy books the buy as create does, so it is refused the same
  # way: no order is booked and no creative is handed over. The retroactive push is per
  # creative, so it withholds the misfit and pushes the other; it is given a fitting creative
  # too, so a path that never reached the upload cannot pass.
  @T-UC-002-local-creative-kind-size-refused-create @extension @creative
  Scenario: A 300x250 image is refused at create against an option accepting only 728x90, before the ad server
    Given a valid create_media_buy request
    And the account exists and is active
    And the product publishes an image format option accepting sizes "728x90"
    And the package references the buyer's approved 300x250 image creative "cr-misfit" showing "https://cdn.example.com/misfit.png"
    When the Buyer Agent sends the create_media_buy request
    Then the request is refused on the wire with code "CREATIVE_SIZE_MISMATCH" recovery "correctable" naming field "packages[0].creative_ids[0]"
    And the ad server is handed no creative
    And the ad server adapter should never be invoked

  # Multi-error (PR #2582 review, epic invariant): the create gate raises on the first creative
  # its option refuses, so a buy with two misfits names one and the buyer fixes, resends and
  # learns of the second. One refusal carries every refused creative in details.problems, each
  # an ErrorProblem (src/core/errors/details.py) naming its creative, its code from the code
  # table and the manifest path it is about, the shape the gate already gives the first
  # creative's problems; the error's field names the first creative's request position.
  # enums/error-code.json CREATIVE_SIZE_MISMATCH: "Sellers SHOULD identify the offending
  # creative in error.field and MAY include the submitted and accepted dimensions in
  # buyer-safe error.details".
  @T-UC-002-local-creative-kind-size-refused-every-creative @extension @creative
  Scenario: Two images an option's sizes refuse are both named in one refusal at create, before the ad server
    Given a valid create_media_buy request
    And the account exists and is active
    And the product publishes an image format option accepting sizes "728x90"
    And the package references the buyer's approved 300x250 image creative "cr-misfit-a" showing "https://cdn.example.com/misfit-a.png"
    And the package references the buyer's approved 160x600 image creative "cr-misfit-b" showing "https://cdn.example.com/misfit-b.png"
    When the Buyer Agent sends the create_media_buy request
    Then the request is refused on the wire with code "CREATIVE_SIZE_MISMATCH" recovery "correctable" naming field "packages[0].creative_ids[0]"
    And the CREATIVE_SIZE_MISMATCH refusal's problems are exactly, one per creative:
      | subject_id  | field                   | code                   |
      | cr-misfit-a | assets.image_main.width | CREATIVE_SIZE_MISMATCH |
      | cr-misfit-b | assets.image_main.width | CREATIVE_SIZE_MISMATCH |
    And the ad server is handed no creative
    And the ad server adapter should never be invoked

  @T-UC-002-local-creative-kind-size-refused-approval-replay @extension @creative
  Scenario: The seller's approval of a held buy holding a creative its option's sizes refuse is refused before the ad server
    Given a valid create_media_buy request
    And the account exists and is active
    And the product publishes an image format option accepting sizes "728x90"
    And the package references the buyer's approved 728x90 image creative "cr-fits" showing "https://cdn.example.com/fits.png"
    And the package selects that format option by its format_option_ref
    And the tenant requires manual approval
    When the Buyer Agent sends the create_media_buy request
    Then the response status should be "submitted"
    Given the buyer's creative "cr-misfit", approved, a 300x250 image showing "https://cdn.example.com/misfit.png", is assigned to the media buy awaiting approval
    When the seller approves the media buy
    Then the seller's approval fails with error code "CREATIVE_SIZE_MISMATCH"
    And the media buy still awaits approval
    And the ad server's creative manager is handed no creative

  @T-UC-002-local-creative-kind-size-refused-retroactive-push @extension @creative
  Scenario: Approving a creative its option's sizes refuse does not push it to the live buy
    Given the product publishes an image format option accepting sizes "728x90"
    And the buyer holds a live media buy booked at the ad server as order "order-live" with package "pkg-live"
    And the buyer's creative "cr-fits-late", under review, a 728x90 image showing "https://cdn.example.com/fits-late.png", is assigned to package "pkg-live" of the live media buy
    And the buyer's creative "cr-misfit-late", under review, a 300x250 image showing "https://cdn.example.com/misfit-late.png", is assigned to package "pkg-live" of the live media buy
    When the seller approves creative "cr-fits-late"
    And the seller approves creative "cr-misfit-late"
    Then the ad server's creative manager is handed creative "cr-fits-late" once and not creative "cr-misfit-late"
    And the seller's approval of creative "cr-misfit-late" tells the seller it was not pushed to the live media buy, with code "CREATIVE_SIZE_MISMATCH", and the approval of creative "cr-fits-late" reports nothing

  # F1 (PR #2582 review): a canonical creative states its size only through its product's
  # declaration, so on a package whose product resolves none it has no size to be trafficked
  # at; it is refused on every path that pushes it ("no product declaration resolved"),
  # answered VALIDATION_ERROR, even when another of its packages resolves. Here the second
  # package was booked on a product the seller has since removed. The push is per creative, so
  # it withholds this one and pushes the fitting one, once, which proves the path reached the
  # upload; the refused creative's approval reports one refusal, not one per package.
  @T-UC-002-local-creative-kind-unresolved-product-retroactive-push @extension @creative
  Scenario: Approving a canonical creative assigned to a package whose product resolves no declaration does not push it to the live buy
    Given the product publishes a display_tag format option for tag_url delivery accepting sizes "300x250"
    And the buyer holds a live media buy booked at the ad server as order "order-live" with package "pkg-live", and package "pkg-gone" on product "prod-retired", which the seller no longer has
    And the buyer's creative "cr-fits-late", under review, a display_tag carrying the tag URL "https://ads.example-adserver.com/tag?slot=14", is assigned to package "pkg-live" of the live media buy
    And the buyer's creative "cr-unresolved-late", under review, a display_tag carrying the tag URL "https://ads.example-adserver.com/tag?slot=15", is assigned to packages "pkg-live, pkg-gone" of the live media buy
    When the seller approves creative "cr-fits-late"
    And the seller approves creative "cr-unresolved-late"
    Then the ad server's creative manager is handed creative "cr-fits-late" once and not creative "cr-unresolved-late"
    And the seller's approval of creative "cr-unresolved-late" tells the seller it was not pushed to the live media buy, with code "VALIDATION_ERROR", and the approval of creative "cr-fits-late" reports nothing

  # One format resolution on every path (PR #2582): the retroactive push resolves a creative's
  # legacy reference the way the create gate does, by one fetch from the agent the reference
  # names. A reference that agent does not serve, and the canonical mapping registry does not
  # project, has no canonical declaration to normalize to, so the push withholds the creative
  # with VALIDATION_ERROR, the code the create gate answers the same problem with. The push
  # used to look the reference up a second time and, when that missed too, traffic the
  # creative from its raw data. The fitting creative is pushed once, which proves the path
  # reached the upload. docs/creative/template-format-ids.mdx § Compatibility: "Normalize them
  # to canonical declarations immediately and do all matching on the canonical shape. Do not
  # author new template IDs or treat a deprecated `list_creative_formats` response as a source
  # of truth."
  # @source repo=adcp ref=v3.2.7 path=docs/creative/template-format-ids.mdx
  @T-UC-002-local-creative-kind-unserved-format-retroactive-push @extension @creative
  Scenario: Approving a creative in a reference format its creative agent does not serve does not push it to the live buy
    Given the product publishes an image format option accepting sizes "300x250"
    And the buyer holds a live media buy booked at the ad server as order "order-live" with package "pkg-live"
    And the buyer's creative "cr-fits-late", under review, a 300x250 image showing "https://cdn.example.com/fits-late.png", is assigned to package "pkg-live" of the live media buy
    And the buyer's creative "cr-unserved-late", under review, a 300x250 image showing "https://cdn.example.com/unserved-late.png" in the reference format "retired_banner_v1", which the creative agent does not serve, is assigned to package "pkg-live" of the live media buy
    When the seller approves creative "cr-fits-late"
    And the seller approves creative "cr-unserved-late"
    Then the ad server's creative manager is handed creative "cr-fits-late" once and not creative "cr-unserved-late"
    And the seller's approval of creative "cr-unserved-late" tells the seller it was not pushed to the live media buy, with code "VALIDATION_ERROR", and the approval of creative "cr-fits-late" reports nothing

  # A creative on two packages of one buy is one creative at the ad server, associated with
  # each package's line item. The seller's approval of it pushes it to that buy once, carrying
  # both packages, each with the creative as its own package's product reads it.
  @T-UC-002-local-creative-kind-retroactive-push-two-packages @extension @creative
  Scenario: Approving a creative assigned to two packages of a live buy pushes it once, with both packages
    Given the product publishes a display_tag format option for tag_url delivery accepting sizes "300x250"
    And the buyer holds a live media buy booked at the ad server as order "order-live" with packages "pkg-live, pkg-live-2"
    And the buyer's creative "cr-two-packages", under review, a display_tag carrying the tag URL "https://ads.example-adserver.com/tag?slot=16", is assigned to packages "pkg-live, pkg-live-2" of the live media buy
    When the seller approves creative "cr-two-packages"
    Then the ad server's creative manager is handed creative "cr-two-packages" once, assigned to packages "pkg-live, pkg-live-2", sized "300x250" on each
