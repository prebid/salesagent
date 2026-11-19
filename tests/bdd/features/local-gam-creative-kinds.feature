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
# client. The live server has no stand-in GAM network, so the setup declares itself
# unrealizable on e2e (GamSellerMixin.sell_through_gam).
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
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_url_728x90", "params": {"width": 728, "height": 90}}]}'
    And the package references the buyer's approved display_tag creative "cr-tag" carrying the tag URL "https://ads.example.com/tag.js?slot=1&size=728x90"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one third-party creative for advertiser "3000001" whose snippet is '<script src="https://ads.example.com/tag.js?slot=1&amp;size=728x90"></script>' and which names no image URL
    And Google Ad Manager created that creative at size 728x90
    And Google Ad Manager associated that creative with the line item it created, at its own size

  @T-UC-002-local-gam-creative-paired-redirect @extension
  Scenario: A display_tag creative's paired redirect becomes one image redirect creative over exactly that pair
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_paired_300x250", "params": {"width": 300, "height": 250, "supported_delivery_types": ["paired_redirect"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}'
    And the package references the buyer's approved display_tag creative "cr-paired" carrying the paired redirect "https://ads.example.com/ad?slot=42&cb={CACHEBUSTER}" with click-through "https://click.example.com/redirect?id=42"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one image redirect creative for advertiser "3000001" showing "https://ads.example.com/ad?slot=42&cb={CACHEBUSTER}" and clicking through to "https://click.example.com/redirect?id=42"
    And Google Ad Manager created that creative at size 300x250
    And Google Ad Manager associated that creative with the line item it created, at its own size

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
    And the package references the buyer's approved html5 creative "cr-bundle" carrying a zip bundle from the buyer's bundle host and the backup image "https://cdn.example.com/backup.png"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one HTML5 creative for advertiser "3000001" whose html5 asset is the buyer's zip bundle, in a file with a zip extension, and which names no image URL
    And Google Ad Manager created that creative at size 300x250
    And Google Ad Manager associated that creative with the line item it created, at its own size

  # A GAM creative has one size for life. The first size the option declares that a line item
  # placeholder takes is the creative's; the other declared sizes a placeholder takes ride the
  # association. The 160x600 placeholder is no size the option declares, so it is not one.
  @T-UC-002-local-gam-creative-multi-size @extension
  Scenario: A multi-size display_tag creative is created at its first declared size, and its association carries the other
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_inline_multi", "params": {"sizes": [{"width": 728, "height": 90}, {"width": 300, "height": 250}], "supported_delivery_types": ["inline_markup"], "slots": [{"asset_group_id": "serving_tag", "asset_type": "display_tag", "required": true}]}}]}', with product settings '{"creative_placeholders": [{"width": 160, "height": 600}, {"width": 300, "height": 250}, {"width": 728, "height": 90}]}'
    And the package references the buyer's approved display_tag creative "cr-multi" carrying the inline markup "<div>Example tag</div>" of type "standard"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one third-party creative for advertiser "3000001" whose snippet is "<div>Example tag</div>" and which names no image URL
    And Google Ad Manager created that creative at size 728x90
    And Google Ad Manager associated that creative with the line item it created, overriding its size with "300x250"

  # A responsive option states bounds, not sizes ("Minimum accepted width for responsive
  # third-party tags. Pair with `max_width`"). Every placeholder inside them, each bound
  # inclusive, is a size the tag accepts, in the line item's order: 728x90 sits on the width and
  # height bounds, 300x250 on the others, and 160x600 is outside.
  @T-UC-002-local-gam-creative-responsive-size @extension
  Scenario: A responsive display_tag creative is created at the first placeholder inside its bounds, and its association carries the rest
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_url_responsive", "params": {"min_width": 300, "max_width": 728, "min_height": 90, "max_height": 250}}]}', with product settings '{"creative_placeholders": [{"width": 160, "height": 600}, {"width": 728, "height": 90}, {"width": 300, "height": 250}]}'
    And the package references the buyer's approved display_tag creative "cr-responsive" carrying the tag URL "https://ads.example.com/responsive.js"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one third-party creative for advertiser "3000001" whose snippet is '<script src="https://ads.example.com/responsive.js"></script>' and which names no image URL
    And Google Ad Manager created that creative at size 728x90
    And Google Ad Manager associated that creative with the line item it created, overriding its size with "300x250"

  # An option stating no size mode "accepts any dimensions": every placeholder of the line item
  # is a size the tag accepts, the first one the creative's own.
  @T-UC-002-local-gam-creative-any-size @extension
  Scenario: A display_tag creative whose option states no size is created at the line item's first placeholder, and its association carries the rest
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_url_any", "params": {}}]}', with product settings '{"creative_placeholders": [{"width": 300, "height": 600}, {"width": 728, "height": 90}]}'
    And the package references the buyer's approved display_tag creative "cr-any" carrying the tag URL "https://ads.example.com/any.js"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager created one third-party creative for advertiser "3000001" whose snippet is '<script src="https://ads.example.com/any.js"></script>' and which names no image URL
    And Google Ad Manager created that creative at size 300x600
    And Google Ad Manager associated that creative with the line item it created, overriding its size with "728x90"

  # The option declares 300x250 and the seller's line items take 728x90 only: there is no size
  # to create the creative at, and none is invented. The refusal comes before GAM is written to.
  @T-UC-002-local-gam-creative-size-mismatch @extension
  Scenario: A creative whose declared size matches no line item placeholder is refused, and nothing is created in GAM
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product formats '{"format_options": [{"format_kind": "display_tag", "format_option_id": "tag_url_300x250", "params": {"width": 300, "height": 250}}]}', with product settings '{"creative_placeholders": [{"width": 728, "height": 90}]}'
    And the package references the buyer's approved display_tag creative "cr-tag" carrying the tag URL "https://ads.example.com/tag.js?slot=1"
    When the Buyer Agent sends the create_media_buy request
    Then the operation should fail with error code "CREATIVE_SIZE_MISMATCH"
    And the error recovery should be "correctable"
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
    And Google Ad Manager received no order and no line item
    And Google Ad Manager created no creative
