# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# What it grades: the Google Ad Manager creative a GAM seller creates for each creative a buyer
# names on create_media_buy, read off the representation the buyer sent and never off a URL's
# shape or a preview page, and the creative's size, read off the product's format option and
# matched against the line item's creative placeholders with no default.
#
# AdCP 3.2.7:
# - formats/canonical/display_tag.json: "a single ad-request URL, byte-preserved inline markup,
#   or an atomic paired redirect (`ad_request_url` plus `clickthrough_url`)"; the default slot
#   is `tag_url` (`url_type: ad_request`); `sizes`: "The buyer's third-party tag must render at
#   one of the listed sizes; the seller picks which size to request at impression time."
#   A tag URL creative here is a `display_tag` asset with `delivery_type: "tag_url"`, under the
#   `serving_tag` slot its option declares ("Format options accepting inline or paired delivery
#   override `slots` with a `display_tag` asset and declare `supported_delivery_types`").
# - core/assets/display-tag-asset.json: `markup` "Receivers MUST preserve its bytes";
#   `clickthrough_url` "The pair MUST NOT be split, mixed, or revised independently."
# - formats/canonical/html5.json: "Interactive HTML5 banner delivered as a zip archive ...
#   Receivers unpack the zip, validate internal structure, and serve from CDN."
# - core/assets/html-asset.json: "Inline HTML content asset", the markup in `content`.
# - enums/error-code.json: CREATIVE_SIZE_MISMATCH "The submitted creative dimensions do not
#   match any size accepted by the selected packages"; UNSUPPORTED_FEATURE "A requested feature
#   or field is not supported by this seller".
# Ungraded by the storyboard runner: what a seller sends its ad server is seller-internal.
#
# GAM facts are Ad Manager API v202602 (googleads 49.0.0 calls it by default):
# ThirdPartyCreative.snippet; ImageRedirectCreative.imageUrl and destinationUrl; Html5Creative
# requires html5Asset, whose assetByteArray "must be a zip bundle" and fileName "must have a zip
# extension"; LineItemCreativeAssociation.sizes "Overrides the value set for Creative.size".
#
# Transports: a2a, mcp and rest with the real GoogleAdManager adapter over a stand-in SOAP
# client. On e2e the tenant
# sells on the GAM network the environment configures, and each GAM Then reads it back
# (tests/harness/gam_network.py); a Given the configured network cannot be made into
# declares that with its own reason (GamSellerMixin).
#
# @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/display_tag.json
# @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/assets/display-tag-asset.json
# @source repo=adcp ref=v3.2.7 path=static/schemas/source/formats/canonical/html5.json
# @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/assets/html-asset.json
# @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json
Feature: A Google Ad Manager seller creates each creative as the GAM creative its representation is (local)

  Background:
    Given a valid create_media_buy request
    And the account exists and is active

  # The markup reaches GAM byte for byte: the AdCP macro in it is not rewritten to GAM's.
  @T-UC-002-local-gam-creative-inline-markup @extension
  Scenario: A display_tag creative's inline markup becomes one third-party creative whose snippet is the markup's bytes
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_inline_728x90", "params": {"width": 728, "height": 90, "supported_delivery_types": ["inline_markup"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}'
    And the package references the buyer's approved display_tag creative "cr-inline" carrying the inline markup "<script src='https://ads.example.com/serve.js?slot=1&cb={CACHEBUSTER}'></script>" of type "javascript"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one third-party creative for advertiser "3000001" whose snippet is "<script src='https://ads.example.com/serve.js?slot=1&cb={CACHEBUSTER}'></script>" and which names no image URL
    And Google Ad Manager created that creative at size 728x90
    And Google Ad Manager associated that creative with the line item it created, at its own size

  # A tag URL is an ad request made at impression time, so GAM gets a tag that calls it, not an
  # image whose source is the tag. The URL is an attribute value, so its ampersand is escaped.
  @T-UC-002-local-gam-creative-tag-url @extension
  Scenario: A display_tag creative's tag URL becomes one third-party creative whose snippet is a script tag calling it
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_url_728x90", "params": {"width": 728, "height": 90, "supported_delivery_types": ["tag_url"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}'
    And the package references the buyer's approved display_tag creative "cr-tag" carrying the tag URL "https://ads.example.com/tag.js?slot=1&size=728x90"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one third-party creative for advertiser "3000001" whose snippet is '<script src="https://ads.example.com/tag.js?slot=1&amp;size=728x90"></script>' and which names no image URL
    And Google Ad Manager created that creative at size 728x90
    And Google Ad Manager associated that creative with the line item it created, at its own size

  @T-UC-002-local-gam-creative-paired-redirect @extension
  Scenario: A display_tag creative's paired redirect becomes one image redirect creative over exactly that pair
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_paired_300x250", "params": {"width": 300, "height": 250, "supported_delivery_types": ["paired_redirect"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}'
    And the package references the buyer's approved display_tag creative "cr-paired" carrying the paired redirect "https://prebid.org/ad?slot=42&cb={CACHEBUSTER}" with click-through "https://prebid.org/redirect?id=42"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one image redirect creative for advertiser "3000001" showing "https://prebid.org/ad?slot=42&cb={CACHEBUSTER}" and clicking through to "https://prebid.org/redirect?id=42"
    And Google Ad Manager created that creative at size 300x250
    And Google Ad Manager associated that creative with the line item it created, at its own size

  # AdCP macros (docs/creative/universal-macros.mdx at adcp@8d1d3244b, :241 "| `{CACHEBUSTER}` |
  # Random number to prevent caching |" and :243 "| `{CLICK_URL}` | Publisher's click tracking
  # URL |") reach GAM two ways, and both are graded on exact bytes:
  # - translated, in a tracking URL or a landing page: :694, the sales agent "Translates platform
  #   macros to their ad server's syntax: `{CACHEBUSTER}` → `%%CACHEBUSTER%%` (GAM)". GAM's
  #   spelling is src/adapters/gam/utils/macros.py:34 (`%%CACHEBUSTER%%`) and :36
  #   (`%%CLICK_URL_ESC%%`). The impression tracker of every row, and the image's landing page.
  # - preserved, inside a display_tag representation: core/assets/display-tag-asset.json:17
  #   "Tokens remain byte-preserved until the resolver named by each declaration substitutes
  #   them", and :119 for the pair, "The pair MUST NOT be split, mixed, or revised independently".
  #   The tag URL's snippet and the paired redirect's two URLs.
  @T-UC-002-local-gam-creative-macros @extension
  Scenario Outline: A creative's AdCP macros reach GAM in GAM's spelling in its tracker and landing page, and byte for byte inside a display tag
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '<product_formats>'
    And the package references the buyer's approved <creative>, with impression tracker "https://prebid.org/imp?cb={CACHEBUSTER}"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one <gam_creative>
    And Google Ad Manager tracks that creative's impressions at exactly "https://prebid.org/imp?cb=%%CACHEBUSTER%%"

    Examples:
      | representation  | product_formats | creative | gam_creative |
      | tag_url         | {"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_url_300x250", "params": {"width": 300, "height": 250, "supported_delivery_types": ["tag_url"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]} | display_tag creative "cr-macro-tag" carrying the tag URL "https://ads.example.com/tag/{CACHEBUSTER}.js?click={CLICK_URL}" | third-party creative for advertiser "3000001" whose snippet is '<script src="https://ads.example.com/tag/{CACHEBUSTER}.js?click={CLICK_URL}"></script>' and which names no image URL |
      | paired_redirect | {"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_paired_300x250", "params": {"width": 300, "height": 250, "supported_delivery_types": ["paired_redirect"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]} | display_tag creative "cr-macro-paired" carrying the paired redirect "https://ads.example.com/ad?cb={CACHEBUSTER}" with click-through "https://prebid.org/redirect?c={CLICK_URL}" | image redirect creative for advertiser "3000001" showing "https://ads.example.com/ad?cb={CACHEBUSTER}" and clicking through to "https://prebid.org/redirect?c={CLICK_URL}" |
      | image           | {"format_options": [{"format_kind": "image", "format_option_id": "image_300x250", "params": {"width": 300, "height": 250}}]} | 300x250 image creative "cr-macro-image" showing "https://cdn.example.com/banner-300x250.png", with click-through "https://prebid.org/landing?c={CLICK_URL}" | image redirect creative for advertiser "3000001" showing "https://cdn.example.com/banner-300x250.png" and clicking through to "https://prebid.org/landing?c=%%CLICK_URL_ESC%%" |

  # The creative is stored as the buyer sent it: its markup, with no URL and no size of its own.
  # The size comes from the format.
  @T-UC-002-local-gam-creative-html @extension
  Scenario: An HTML creative stored without a URL becomes one third-party creative whose snippet is its markup
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_html"}]}'
    And the package references the buyer's approved 300x250 HTML creative "cr-html" with markup "<div>Example ad</div>", stored without a URL
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one third-party creative for advertiser "3000001" whose snippet is "<div>Example ad</div>" and which names no image URL
    And Google Ad Manager created that creative at size 300x250
    And Google Ad Manager associated that creative with the line item it created, at its own size

  # GAM receives the bundle itself, fetched from the buyer's host; the backup image is not the
  # creative.
  @T-UC-002-local-gam-creative-html5-bundle @extension
  Scenario: An html5 creative's zip bundle becomes one HTML5 creative carrying the bundle, not an image of its backup
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "html5", "format_option_id": "html5_300x250", "params": {"width": 300, "height": 250}}]}'
    And the package references the buyer's approved html5 creative "cr-bundle" bundled by the buyer's bundle host, with backup image "https://cdn.example.com/backup.png"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one HTML5 creative for advertiser "3000001" whose html5 asset is the buyer's zip bundle, in a file with a zip extension, and which names no image URL
    And Google Ad Manager created that creative at size 300x250
    And Google Ad Manager associated that creative with the line item it created, at its own size

  # GAM never fetches the bundle; the seller does, and hands GAM its bytes. So a bundle the
  # seller cannot fetch is the buyer's to fix, found before anything is booked, never after the
  # order and line items are written. AdCP 3.2.7 enums/error-code.json
  # CREATIVE_VALIDATION_FAILED_GENERIC: "The creative failed buyer-correctable validation, but
  # the producer cannot classify the failure with a more specific standard code ... Recovery:
  # correctable (correct the buyer-visible validation problem and resubmit the creative)." No
  # code names a creative asset the seller cannot fetch: CREATIVE_INACCESSIBLE is a creative
  # governance agent's failure to retrieve assets. The request names the creative at
  # packages[0].creative_ids[0].
  @T-UC-002-local-gam-creative-html5-bundle-unreachable @extension
  Scenario: An html5 creative whose bundle URL does not answer is refused before booking, and GAM holds no order
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "html5", "format_option_id": "html5_300x250", "params": {"width": 300, "height": 250}}]}'
    And the package references the buyer's approved html5 creative "cr-bundle-silent" bundled by a bundle host that does not answer, with backup image "https://cdn.example.com/backup.png"
    When the Buyer Agent sends the create_media_buy request
    Then the request is refused on the wire with code "CREATIVE_VALIDATION_FAILED_GENERIC" recovery "correctable" naming field "packages[0].creative_ids[0]"
    And no media buy is persisted for the tenant
    And Google Ad Manager received no order and no line item
    And Google Ad Manager created no creative

  # A GAM creative has one size for life. The first size the option declares that a line item
  # placeholder takes is the creative's; the association carries every declared size a
  # placeholder takes, the creative's own among them, because its sizes "override" Creative.size.
  # The association's sizes are a set: a real network does not keep the order they were sent in.
  # The 160x600 placeholder is no size the option declares, so it is not one.
  @T-UC-002-local-gam-creative-multi-size @extension
  Scenario: A multi-size display_tag creative is created at its first declared size, and its association carries both
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_inline_multi", "params": {"sizes": [{"width": 728, "height": 90}, {"width": 300, "height": 250}], "supported_delivery_types": ["inline_markup"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}', with product settings '{"creative_placeholders": [{"width": 160, "height": 600}, {"width": 300, "height": 250}, {"width": 728, "height": 90}]}'
    And the package references the buyer's approved display_tag creative "cr-multi" carrying the inline markup "<div>Example tag</div>" of type "standard"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one third-party creative for advertiser "3000001" whose snippet is "<div>Example tag</div>" and which names no image URL
    And Google Ad Manager created that creative at size 728x90
    And Google Ad Manager associated that creative with the line item it created, overriding its size with "728x90,300x250"

  # A responsive option states bounds, not sizes ("Minimum accepted width for responsive
  # third-party tags. Pair with `max_width`"). Every placeholder inside them, each bound
  # inclusive, is a size the tag accepts: 728x90 sits on the width and height bounds, 300x250 on
  # the others, and 160x600 is outside. They are taken smallest width first, then smallest
  # height, never in the line item's order: GAM does not answer a line item's placeholders in
  # the order they were written (R9's e2e run 025d613f wrote [300x600, 728x90] and read
  # [728x90, 300x600]), so the order written cannot survive the round trip, and the seller's
  # check before booking and its upload after must pick the same size. The association carries
  # both, the creative's own size among them.
  @T-UC-002-local-gam-creative-responsive-size @extension
  Scenario: A responsive display_tag creative is created at the smallest-width placeholder inside its bounds, and its association carries every one
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_url_responsive", "params": {"min_width": 300, "max_width": 728, "min_height": 90, "max_height": 250, "supported_delivery_types": ["tag_url"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}', with product settings '{"creative_placeholders": [{"width": 160, "height": 600}, {"width": 728, "height": 90}, {"width": 300, "height": 250}]}'
    And the package references the buyer's approved display_tag creative "cr-responsive" carrying the tag URL "https://ads.example.com/responsive.js"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one third-party creative for advertiser "3000001" whose snippet is '<script src="https://ads.example.com/responsive.js"></script>' and which names no image URL
    And Google Ad Manager created that creative at size 300x250
    And Google Ad Manager associated that creative with the line item it created, overriding its size with "300x250,728x90"

  # An option stating no size mode "accepts any dimensions": every placeholder of the line item
  # is a size the tag accepts. They are taken smallest width first, then smallest height, for
  # the reason the responsive scenario above gives (GAM reorders a line item's placeholders,
  # run 025d613f); the first is the creative's own, and the association carries them all.
  @T-UC-002-local-gam-creative-any-size @extension
  Scenario: A display_tag creative whose option states no size is created at the line item's smallest-width placeholder, and its association carries every placeholder
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_url_any", "params": {"supported_delivery_types": ["tag_url"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}', with product settings '{"creative_placeholders": [{"width": 300, "height": 600}, {"width": 728, "height": 90}]}'
    And the package references the buyer's approved display_tag creative "cr-any" carrying the tag URL "https://ads.example.com/any.js"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one third-party creative for advertiser "3000001" whose snippet is '<script src="https://ads.example.com/any.js"></script>' and which names no image URL
    And Google Ad Manager created that creative at size 300x600
    And Google Ad Manager associated that creative with the line item it created, overriding its size with "300x600,728x90"

  # The option declares 300x250 and the seller's line items take 728x90 only: there is no size
  # to create the creative at, and none is invented. The refusal comes before GAM is written to,
  # and before the seller stores a buy, as the core creative gate's refusal on create does.
  #
  # F2 (PR #2582 review): the option's sizes and the line items' placeholders cannot overlap,
  # so no creative the option accepts can serve here; the buyer has no lever, and today the GAM
  # layer answers CREATIVE_SIZE_MISMATCH, telling the buyer to resize. AdCP 3.2.7
  # enums/error-code.json: CONFIGURATION_ERROR "The seller's deployment is misconfigured in a
  # way that prevents handling the request — the buyer cannot fix it, retrying will not help,
  # and reporting to the seller's operator is the only remediation. ... Recovery: terminal";
  # CREATIVE_SIZE_MISMATCH "Recovery: correctable (resize or replace the creative with a size
  # accepted by every selected package that may serve it)", which no size can satisfy here.
  # The refusal still names the creative where the request named it, as the create gate's
  # creative refusals do (packages[0].creative_ids[0]).
  @T-UC-002-local-gam-creative-size-mismatch @extension
  Scenario: A creative whose option's sizes overlap no line item placeholder is refused as the seller's configuration, and nothing is created in GAM
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_url_300x250", "params": {"width": 300, "height": 250, "supported_delivery_types": ["tag_url"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}', with product settings '{"creative_placeholders": [{"width": 728, "height": 90}]}'
    And the package references the buyer's approved display_tag creative "cr-tag" carrying the tag URL "https://ads.example.com/tag.js?slot=1"
    When the Buyer Agent sends the create_media_buy request
    Then the request is refused on the wire with code "CONFIGURATION_ERROR" recovery "terminal" naming field "packages[0].creative_ids[0]"
    And no media buy is persisted for the tenant
    And Google Ad Manager received no order and no line item
    And Google Ad Manager created no creative

  # A native creative is a GAM native style over a creative template (PR #2445); this seller
  # does not create one, and says so before GAM is written to.
  @T-UC-002-local-gam-creative-native-refused @extension
  Scenario: A native_in_feed creative is refused as unsupported, and nothing is created in GAM
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "native_in_feed", "format_option_id": "native_feed", "params": {}}]}'
    And the package references the buyer's approved native_in_feed creative "cr-native" titled "Example headline" by "Example Advertiser", landing on "https://example.com/landing", with main image "https://example.com/native.png"
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail with error code "UNSUPPORTED_FEATURE"
    And the error recovery should be "correctable"
    And no media buy is persisted for the tenant
    And Google Ad Manager received no order and no line item
    And Google Ad Manager created no creative

  # A 1x1 placeholder is a size like any other: a creative whose option declares 300x250 is
  # created at 300x250 on a line item that also takes a 1x1 placeholder, and its association
  # states no size. The 1x1 placeholder neither accepts every creative nor becomes one of
  # this creative's sizes.
  @T-UC-002-local-gam-creative-one-by-one-placeholder @extension
  Scenario: A declared 300x250 creative on a line item that also takes a 1x1 placeholder is created at 300x250
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_url_300x250", "params": {"width": 300, "height": 250, "supported_delivery_types": ["tag_url"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}', with product settings '{"creative_placeholders": [{"width": 1, "height": 1}, {"width": 300, "height": 250}]}'
    And the package references the buyer's approved display_tag creative "cr-tag" carrying the tag URL "https://ads.example.com/tag.js?slot=1"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one third-party creative for advertiser "3000001" whose snippet is '<script src="https://ads.example.com/tag.js?slot=1"></script>' and which names no image URL
    And Google Ad Manager created that creative at size 300x250
    And Google Ad Manager associated that creative with the line item it created, at its own size

  # The seller's approval is what books a held buy's order, so the refusal comes before it:
  # the approval fails, GAM is not written to, and the buy still awaits approval, as it does
  # when the seller's own creative check refuses it: nothing was booked, so there is nothing
  # that failed, and the seller can retry once the creative is fixed.
  #
  # F2 (PR #2582 review): the same non-overlap as @T-UC-002-local-gam-creative-size-mismatch,
  # so the same code, CONFIGURATION_ERROR, quoted there. The seller's approval carries the
  # typed error a create answers with (ApprovalResult.error), so it names the creative where
  # the held buy names it, packages[0].creative_ids[0], as the create scenario does.
  @T-UC-002-local-gam-creative-size-mismatch-on-approval @extension
  Scenario: A held buy whose creative's option sizes overlap no line item placeholder is refused at the seller's approval as the seller's configuration, and nothing is created in GAM
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_url_300x250", "params": {"width": 300, "height": 250, "supported_delivery_types": ["tag_url"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}', with product settings '{"creative_placeholders": [{"width": 728, "height": 90}]}'
    And the tenant requires manual approval
    And the package references the buyer's approved display_tag creative "cr-tag" carrying the tag URL "https://ads.example.com/tag.js?slot=1"
    When the Buyer Agent sends the create_media_buy request
    Then the response status should be "submitted"
    When the seller approves the media buy
    Then the seller's approval fails with error code "CONFIGURATION_ERROR" naming field "packages[0].creative_ids[0]"
    And the media buy still awaits approval
    And Google Ad Manager received no order and no line item
    And Google Ad Manager created no creative

  # The same rule when GAM's check refuses the package rather than the creative: the product's
  # GAM settings exclude the display family the selected display_tag option declares, so GAM's
  # placeholder computation refuses it as a capability the product does not offer, before the
  # order is written. Nothing was booked, so the buy still awaits approval. adcp/_schemas/3.2/
  # enums/error-code.json:149 UNSUPPORTED_FEATURE: "A requested feature or field is not
  # supported by this seller". That the buy stays pending is the seller's own approval rule,
  # ungraded by the storyboard runner.
  @T-UC-002-local-gam-family-excluded-on-approval @extension
  Scenario: A held buy selecting a display_tag option whose family the product's GAM settings exclude is refused at the seller's approval, and the buy still awaits approval
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_url_728x90", "params": {"width": 728, "height": 90, "supported_delivery_types": ["tag_url"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}', with product settings '{"supported_format_types": ["video"]}'
    And the package references the buyer's approved display_tag creative "cr-tag" carrying the tag URL "https://ads.example.com/tag.js?slot=1"
    And the package selects that format option by its format_option_ref
    And the tenant requires manual approval
    When the Buyer Agent sends the create_media_buy request
    Then the response status should be "submitted"
    When the seller approves the media buy
    Then the seller's approval fails with error code "UNSUPPORTED_FEATURE"
    And the media buy still awaits approval
    And Google Ad Manager received no order and no line item
    And Google Ad Manager created no creative

  # F2 (PR #2582 review): the GAM-layer size refusal carries no error.field. Here the refusal is
  # the buyer's to fix: the option accepts 728x90 as well, and the line items take it, so a
  # 728x90 image would serve. AdCP 3.2.7 enums/error-code.json CREATIVE_SIZE_MISMATCH: "The
  # submitted creative dimensions do not match any size accepted by the selected packages.
  # Sellers SHOULD identify the offending creative in error.field and MAY include the submitted
  # and accepted dimensions in buyer-safe error.details. Recovery: correctable (resize or
  # replace the creative with a size accepted by every selected package that may serve it)."
  # The request names the creative at packages[0].creative_ids[0], as the core gate's own size
  # refusal points (@T-UC-002-local-creative-kind-size-refused-create).
  @T-UC-002-local-gam-creative-size-mismatch-names-creative @extension
  Scenario: A 300x250 image on an option also accepting 728x90, for line items taking only 728x90, is refused naming the creative, and nothing is created in GAM
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "image", "format_option_id": "image_multi", "params": {"sizes": [{"width": 300, "height": 250}, {"width": 728, "height": 90}]}}]}', with product settings '{"creative_placeholders": [{"width": 728, "height": 90}]}'
    And the package references the buyer's approved 300x250 image creative "cr-image" showing "https://cdn.example.com/banner-300x250.png"
    When the Buyer Agent sends the create_media_buy request
    Then the request is refused on the wire with code "CREATIVE_SIZE_MISMATCH" recovery "correctable" naming field "packages[0].creative_ids[0]"
    And no media buy is persisted for the tenant
    And Google Ad Manager received no order and no line item
    And Google Ad Manager created no creative

  # A GAM image creative needs a click-through: the configured test network refused, on
  # 2026-11-20, every 300x250 image creative sent without one (destinationUrlType NONE, no
  # destinationUrl, CLICK_TO_WEB with no URL, an empty destinationUrl). AdCP 3.2.7
  # formats/canonical/image.json makes `landing_page_url` optional (`"required": false`), so the
  # requirement is this seller's, and enums/error-code.json names it: CREATIVE_MISSING_CLICK_URL
  # "A submitted creative that requires a destination URL does not provide one ... Recovery:
  # correctable (add the required destination URL and resubmit the creative)". The refusal comes
  # before GAM is written to, pointing at the creative where the request named it.
  @T-UC-002-local-gam-creative-image-no-clickthrough @extension
  Scenario: An image creative with no click-through is refused as one GAM cannot serve, and nothing is created in GAM
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "image", "format_option_id": "image_300x250", "params": {"width": 300, "height": 250}}]}'
    And the package references the buyer's approved 300x250 image creative "cr-no-click" showing "https://cdn.example.com/banner-300x250.png"
    When the Buyer Agent sends the create_media_buy request
    Then the request is refused on the wire with code "CREATIVE_MISSING_CLICK_URL" recovery "correctable" naming field "packages[0].creative_ids[0]"
    And no media buy is persisted for the tenant
    And Google Ad Manager received no order and no line item
    And Google Ad Manager created no creative

  # The creative is the image its option requires, whatever else its manifest carries. AdCP 3.2.7
  # formats/canonical/image.json `slots`: "Products MAY override the default", so an image option
  # may also take an optional video. The served file is the one in the `image_main` slot, and its
  # type is that asset's own, not the type of the first media asset in the manifest (Codex review
  # on PR #2582: an image labelled as video was refused as UNSUPPORTED_FEATURE).
  @T-UC-002-local-gam-creative-image-beside-video @extension
  Scenario: An image creative that also carries a video becomes one image redirect creative of its image
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "image", "format_option_id": "image_300x250_with_video", "params": {"width": 300, "height": 250, "slots": [{"asset_group_id": "image_main", "asset_type": "image", "required": true}, {"asset_group_id": "video", "asset_type": "video", "required": false}, {"asset_group_id": "landing_page_url", "asset_type": "url", "required": false}]}}]}'
    And the package references the buyer's approved 300x250 image creative "cr-image-video" showing "https://cdn.example.com/banner-300x250.png", with click-through "https://prebid.org/landing", beside the video "https://cdn.example.com/clip.mp4" in its "video" slot
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one image redirect creative for advertiser "3000001" showing "https://cdn.example.com/banner-300x250.png" and clicking through to "https://prebid.org/landing"
    And Google Ad Manager created that creative at size 300x250

  # F1 (PR #2582 review): the seller's approval reads the creative once for the union of its
  # packages, so GAM plans it against every line item's placeholders together; the "prod_a"
  # line item then takes the creative at 728x90 too, a size only prod_b's option accepts.
  # Each package's product states its own sizes (core/product.json format_options), and
  # enums/error-code.json CREATIVE_SIZE_MISMATCH asks for "a size accepted by every selected
  # package that may serve it". LineItemCreativeAssociation.sizes "Overrides the value set for
  # Creative.size", so what a line item serves is its association's sizes, else the creative's.
  #
  # The create books the buy on an auto-approving seller, and the seller's approval books it
  # otherwise. Both push the creative to GAM, so both are graded: on create the creative was
  # pushed once per package, and the second package's line item served it at the first
  # package's size (BG-01, PR #2582 review).
  @T-UC-002-local-gam-creative-two-packages-own-sizes @extension
  Scenario Outline: A creative on two packages whose products accept different sizes serves on each package's line item only at the sizes that package's product accepts - <approval>
    Given the seller's guaranteed products have pricing options:
      | product_id | pricing_option_id | pricing_model | fixed_price | floor_price |
      | prod_a     | cpm_usd_a         | cpm           | 12.00       |             |
      | prod_b     | cpm_usd_b         | cpm           | 20.00       |             |
    And the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001"
    And Google Ad Manager sells product "prod_a" with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_url_300x250", "params": {"width": 300, "height": 250, "supported_delivery_types": ["tag_url"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}', with product settings '{"creative_placeholders": [{"width": 300, "height": 250}, {"width": 728, "height": 90}]}'
    And Google Ad Manager sells product "prod_b" with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_url_728x90", "params": {"width": 728, "height": 90, "supported_delivery_types": ["tag_url"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}', with product settings '{"creative_placeholders": [{"width": 728, "height": 90}]}'
    And the tenant approval mode is <approval>
    And the buyer's packages, over a 14-day flight, are:
      | product_id | pricing_option_id | budget | bid_price |
      | prod_a     | cpm_usd_a         | 600    |           |
      | prod_b     | cpm_usd_b         | 420    |           |
    And the package references the buyer's approved display_tag creative "cr-both" carrying the tag URL "https://ads.example.com/tag.js?slot=5"
    And creative "cr-both" is named on every package of the request
    When the Buyer Agent sends the create_media_buy request, which the seller books <booked>
    Then the response status should be "<status>"
    And Google Ad Manager created one third-party creative for advertiser "3000001" whose snippet is '<script src="https://ads.example.com/tag.js?slot=5"></script>' and which names no image URL
    And Google Ad Manager serves that creative on the "prod_a" package's line item at sizes "300x250" only
    And Google Ad Manager serves that creative on the "prod_b" package's line item at sizes "728x90" only

    Examples:
      | approval      | booked          | status    |
      | manual        | on approving it | submitted |
      | auto-approval | at once         | completed |

  # F1 (PR #2582 review): the seller's approval of a held buy pushes through a second reader and
  # a second GAM plan, and only its refusal was graded on GAM. A display_tag creative, and an
  # html5 creative, are created as on create: the GAM creative its representation is, at its
  # option's size, on the line item the approval booked.
  @T-UC-002-local-gam-creative-approval-tag-url @extension
  Scenario: The seller's approval of a held buy creates its display_tag creative as one third-party creative calling the tag
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_url_728x90", "params": {"width": 728, "height": 90, "supported_delivery_types": ["tag_url"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}'
    And the tenant requires manual approval
    And the package references the buyer's approved display_tag creative "cr-held-tag" carrying the tag URL "https://ads.example.com/tag.js?slot=2"
    When the Buyer Agent sends the create_media_buy request
    Then the response status should be "submitted"
    When the seller approves the media buy
    Then the seller's approval books the media buy
    And Google Ad Manager created one third-party creative for advertiser "3000001" whose snippet is '<script src="https://ads.example.com/tag.js?slot=2"></script>' and which names no image URL
    And Google Ad Manager created that creative at size 728x90
    And Google Ad Manager associated that creative with the line item it created, at its own size

  @T-UC-002-local-gam-creative-approval-html5-bundle @extension
  Scenario: The seller's approval of a held buy creates its html5 creative as one HTML5 creative carrying the bundle
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "html5", "format_option_id": "html5_300x250", "params": {"width": 300, "height": 250}}]}'
    And the tenant requires manual approval
    And the package references the buyer's approved html5 creative "cr-held-bundle" bundled by the buyer's bundle host, with backup image "https://cdn.example.com/backup.png"
    When the Buyer Agent sends the create_media_buy request
    Then the response status should be "submitted"
    When the seller approves the media buy
    Then the seller's approval books the media buy
    And Google Ad Manager created one HTML5 creative for advertiser "3000001" whose html5 asset is the buyer's zip bundle, in a file with a zip extension, and which names no image URL
    And Google Ad Manager created that creative at size 300x250
    And Google Ad Manager associated that creative with the line item it created, at its own size

  # F1 (PR #2582 review): the retroactive push, after the seller approves a creative assigned to
  # a buy GAM already holds, has the same second reader and plan, and was graded on GAM nowhere.
  @T-UC-002-local-gam-creative-retroactive-push-tag-url @extension
  Scenario: A display_tag creative the seller approves after its buy was booked is created as one third-party creative on the booked line item
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_url_728x90", "params": {"width": 728, "height": 90, "supported_delivery_types": ["tag_url"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}'
    And the buyer holds a buy booked in Google Ad Manager as order "8000001" in status "active" whose package "pkg-live" is line item "9000001" accepting 728x90
    And the buyer's creative "cr-late-tag", under review, a display_tag carrying the tag URL "https://ads.example.com/tag.js?slot=3", is assigned to package "pkg-live" of the live media buy
    When the seller approves creative "cr-late-tag"
    Then Google Ad Manager created one third-party creative for advertiser "3000001" whose snippet is '<script src="https://ads.example.com/tag.js?slot=3"></script>' and which names no image URL
    And Google Ad Manager created that creative at size 728x90
    And Google Ad Manager associated that creative with line items "9000001"

  @T-UC-002-local-gam-creative-retroactive-push-html5-bundle @extension
  Scenario: An html5 creative the seller approves after its buy was booked is created as one HTML5 creative on the booked line item
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "html5", "format_option_id": "html5_300x250", "params": {"width": 300, "height": 250}}]}'
    And the buyer holds a buy booked in Google Ad Manager as order "8000002" in status "active" whose package "pkg-live" is line item "9000002" accepting 300x250
    And the buyer's creative "cr-late-bundle", under review, an html5 creative bundled by the buyer's bundle host, with backup image "https://cdn.example.com/backup.png", is assigned to package "pkg-live" of the live media buy
    When the seller approves creative "cr-late-bundle"
    Then Google Ad Manager created one HTML5 creative for advertiser "3000001" whose html5 asset is the buyer's zip bundle, in a file with a zip extension, and which names no image URL
    And Google Ad Manager created that creative at size 300x250
    And Google Ad Manager associated that creative with line items "9000002"

  # F5 (PR #2582 review): the line item's placeholders are narrowed by a second reader of the
  # stored row, which takes the first sized asset; for html5 that is the backup image, 320x50, a
  # size the option does not declare (formats/canonical/html5.json: the bundle is what is
  # served; `backup_image` is optional). The creative is the bundle, at the option's 300x250.
  @T-UC-002-local-gam-creative-html5-backup-size @extension
  Scenario: An html5 creative whose backup image is a size its option does not declare is created at its option's size
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "html5", "format_option_id": "html5_300x250", "params": {"width": 300, "height": 250}}]}', with product settings '{"creative_placeholders": [{"width": 300, "height": 250}, {"width": 320, "height": 50}]}'
    And the package references the buyer's approved html5 creative "cr-bundle" bundled by the buyer's bundle host, with backup image "https://cdn.example.com/backup-320x50.png", sized 320x50
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one HTML5 creative for advertiser "3000001" whose html5 asset is the buyer's zip bundle, in a file with a zip extension, and which names no image URL
    And Google Ad Manager created that creative at size 300x250
    And Google Ad Manager associated that creative with the line item it created, at its own size

  # F5 (PR #2582 review): a row synced before sync stopped copying the creative agent's preview
  # onto the creative still carries the preview page as its url and the preview's 300x250 as
  # its size, and nothing migrates it. The second row reader narrows the line item to that
  # 300x250, so the creative is created at 300x250 with no override, not as
  # @T-UC-002-local-gam-creative-multi-size requires. preview_creative.mdx: "Every render's
  # `preview_url` returns an untrusted HTML page for cross-origin iframe embedding".
  @T-UC-002-local-gam-creative-multi-size-preview-backfill @extension
  Scenario: A multi-size display_tag creative recorded with the preview back-fill is created at its first declared size, and its association carries both
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_inline_multi", "params": {"sizes": [{"width": 728, "height": 90}, {"width": 300, "height": 250}], "supported_delivery_types": ["inline_markup"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}', with product settings '{"creative_placeholders": [{"width": 160, "height": 600}, {"width": 300, "height": 250}, {"width": 728, "height": 90}]}'
    And the package references the buyer's approved display_tag creative "cr-multi" carrying the inline markup "<div>Example tag</div>" of type "standard", recorded with the creative agent's preview page "https://creative.adcontextprotocol.org/preview/cr-multi/desktop.html" as its url at 300x250
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one third-party creative for advertiser "3000001" whose snippet is "<div>Example tag</div>" and which names no image URL
    And Google Ad Manager created that creative at size 728x90
    And Google Ad Manager associated that creative with the line item it created, overriding its size with "728x90,300x250"
