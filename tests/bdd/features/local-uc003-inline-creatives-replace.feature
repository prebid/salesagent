# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# Upstream gap: BR-UC-003's only positive inline-creatives scenario
# (@T-UC-003-alt-creatives-inline) grades the response status and affected_packages,
# which a seller that ADDS the inline bodies beside the package's previous creatives
# passes as well as one that replaces them. The pin's inline_creatives_without_sync
# storyboard grades the replacement, but it requires comply_test_controller, which this
# seller does not include. These scenarios grade the replacement, in the storyboard's
# shape (an inline creative, then an inline update, then a get_media_buys readback), and
# its scope: the inline array replaces the inline creatives, never the library ones.
#
# Reconcile upstream in adcp-req (a "the inline creatives replace the package's inline
# creatives" Then on the alt-creatives-inline flow), then retire this file.
#
# @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-update.json pointer=/properties/creatives
# @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/update_media_buy.mdx section="Package Update Object"
# @source repo=adcp ref=v3.2.7 path=dist/compliance/3.2.7/protocols/media-buy/scenarios/inline_creatives_without_sync.yaml
# @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/media-buy-features.json pointer=/properties/inline_creative_management
Feature: UC-003 update_media_buy — inline creatives replace the package's inline creatives (local)

  # BR-UC-003's Background, unchanged: these scenarios run in that flow, and its last Given
  # is what maps the label "mb_existing" to the seeded buy. Without it the update names the
  # literal id and the seller answers MEDIA_BUY_NOT_FOUND before reaching the package.
  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant exists with completed setup checklist
    And the Buyer is authenticated
    And the Buyer owns an existing media buy with media_buy_id "mb_existing"
    And the media buy is in "active" status

  @T-UC-003-local-inline-creatives-replace @creatives-inline @post-s1 @post-s2
  Scenario: inline creatives on a package update replace the inline creatives assigned before
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package "pkg_001" exists in the media buy
    And an earlier update gave package "pkg_001" the inline creative "cr_inline_before"
    And the package update includes inline creatives with valid content
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
    And the response should contain affected_packages including "pkg_001"
    When the Buyer Agent reads media buy "mb_existing" back with get_media_buys
    Then the package "pkg_001" should report creative approvals for exactly ["inline-cr-001"]
    # package-update.json /properties/creatives: "Replace this package's inline creative
    # assets". The seller declares media_buy.features.inline_creative_management true
    # (graded by @T-UC-010-local-inline-creative-management), and media-buy-features.json
    # says a seller declaring a feature "MUST honor requests using that feature". So the
    # inline creative the earlier update sent is no longer assigned, and the one this
    # update sent is: the record holds the replacement, not the union.

  @T-UC-003-local-inline-creatives-keep-library @creatives-inline @post-s1 @post-s2
  Scenario: inline creatives on a package update leave the package's library creatives assigned
    Given the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    And the package "pkg_001" exists in the media buy
    And an earlier update assigned library creative "cr_library_before" to package "pkg_001"
    And the package update includes inline creatives with valid content
    When the Buyer Agent sends the update_media_buy request
    Then the response is compliant with the update_media_buy success spec
    And the response status should be "completed"
    And the response should contain affected_packages including "pkg_001"
    When the Buyer Agent reads media buy "mb_existing" back with get_media_buys
    Then the package "pkg_001" should report creative approvals for exactly ["cr_library_before", "inline-cr-001"]
    # update_media_buy.mdx's package field table: creative_assignments "Replace assigned
    # library creatives", creatives "Replace assigned inline creative bodies for this
    # package"; the same page says a seller "must never silently delete an assignment".
    # package-update.json /properties/creatives: "Use creative_assignments instead for
    # existing library creatives". So the inline array replaces nothing the library
    # array assigned.
