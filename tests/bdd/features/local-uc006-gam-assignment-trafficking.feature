# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# What it grades: a creative assigned to a package of a buy booked in Google Ad Manager
# reaches the line item that package was booked as, through sync_creatives or the seller's
# approval of the creative, once; and an assignment GAM cannot take is reported as an
# assignment error. local-gam-seller.feature grades a buy booked at create.
#
# AdCP 3.1.1:
# - creative/sync-creatives-response.json: assigned_to lists "Package IDs this creative was
#   successfully assigned to"; assignment_errors holds "Assignment errors by package ID".
# - docs/creative/task-reference/sync_creatives.mdx: "Assignments are at the request level,
#   mapping creative IDs to package IDs. Standalone creative agents that do not manage media
#   buys ignore this field" (a sales agent acts on them); dry_run previews changes "without
#   applying them".
# - core/assets/html-asset.json: "Inline HTML content asset", the markup in `content`. GAM
#   serves markup as a ThirdPartyCreative's `snippet`.
# Ungraded by the storyboard runner.
#
# Transports: a2a, mcp and rest with the real GoogleAdManager adapter over a stand-in SOAP
# client. The live server has no stand-in GAM network, so the setup declares itself
# unrealizable on e2e (GamSellerMixin.sell_through_gam).
#
# @source repo=adcp ref=v3.1.1 path=docs/creative/task-reference/sync_creatives.mdx
# @source repo=adcp ref=v3.1.1 path=static/schemas/source/creative/sync-creatives-response.json
Feature: UC-006 a Google Ad Manager seller traffics assigned creatives onto its booked line items (local)

  Background:
    Given the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001"
    And the creative agent serves the reference creative formats
    And the tenant has approval_mode "auto-approve"
    And the buyer holds a buy booked in Google Ad Manager as order "6000001" whose package "pkg-banner-a" is line item "5000001" accepting 300x250

  @T-UC-006-local-gam-assignment-traffics
  Scenario: A synced creative assigned to a booked package is created in GAM and placed on its line item
    Given the buyer syncs a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-banner-a"
    When the Buyer Agent syncs the creative with assignments
    Then creative "cr-banner" is assigned to package "pkg-banner-a"
    And Google Ad Manager created one image creative for advertiser "3000001" showing "https://example.com/banner.png"
    And Google Ad Manager associated that creative with line items "5000001"

  # sync_creatives records the creative agent's preview link as an HTML creative's URL. The
  # markup is what GAM serves; the preview page is not sent to GAM as an image.
  @T-UC-006-local-gam-html-creative
  Scenario: An HTML creative is created in GAM as a third-party creative whose snippet is its markup
    Given the product "prod-banner" supports format_ids ["display_300x250_image", "display_300x250_html"]
    And the buyer holds an approved 300x250 HTML creative "cr-html" with markup "<div>Example ad</div>", recorded with the creative agent's preview link "https://preview.example.com/cr-html" as its URL
    When the Buyer Agent references library creative "cr-html", without resending it, assigned to package "pkg-banner-a"
    Then creative "cr-html" is assigned to package "pkg-banner-a"
    And Google Ad Manager created one third-party creative for advertiser "3000001" whose snippet is "<div>Example ad</div>" and which names no image URL
    And Google Ad Manager associated that creative with line items "5000001"

  @T-UC-006-local-gam-weighted-assignment
  Scenario: A weighted assignment puts its line item on manual rotation and carries the weight
    Given the buyer syncs a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-banner-a" with weight 40
    When the Buyer Agent syncs the creative with assignments
    Then creative "cr-banner" is assigned to package "pkg-banner-a"
    And Google Ad Manager set line item "5000001" to manual creative rotation
    And Google Ad Manager associated that creative with line item "5000001" at weight 40

  @T-UC-006-local-gam-assignment-refused
  Scenario: A package whose line item cannot take the creative's size is reported in assignment_errors
    Given the buyer holds a buy booked in Google Ad Manager as order "6000003" whose package "pkg-leader" is line item "5000003" accepting 728x90
    And the buyer syncs a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-leader"
    When the Buyer Agent syncs the creative with assignments
    Then creative "cr-banner" reports package "pkg-leader" in assignment_errors and not in assigned_to
    And Google Ad Manager created no creative

  # GAM is asked again when the assignment is sent again: that it holds the creative from
  # another package does not place the creative on this one.
  @T-UC-006-local-gam-refused-assignment-resent
  Scenario: A refused assignment of a creative GAM already holds is refused again when re-sent
    Given the buyer holds a buy booked in Google Ad Manager as order "6000003" whose package "pkg-leader" is line item "5000003" accepting 728x90
    And the buyer synced a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-banner-a"
    When the Buyer Agent references library creative "cr-banner", without resending it, assigned to package "pkg-leader"
    Then creative "cr-banner" reports package "pkg-leader" in assignment_errors and not in assigned_to
    When the Buyer Agent references library creative "cr-banner", without resending it, assigned to package "pkg-leader"
    Then creative "cr-banner" reports package "pkg-leader" in assignment_errors and not in assigned_to
    And Google Ad Manager associated that creative with line items "5000001"

  # A creative id is the buyer's own choice, unique only among that buyer's creatives.
  @T-UC-006-local-gam-other-buyers-creative-id
  Scenario: Another buyer's creative under the same id is not the one sent to GAM
    Given another buyer of the seller holds an approved 300x250 image creative "cr-banner" showing "https://example.com/other-buyer.png"
    And the buyer syncs a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-banner-a"
    When the Buyer Agent syncs the creative with assignments
    Then creative "cr-banner" is assigned to package "pkg-banner-a"
    And Google Ad Manager created one image creative for advertiser "3000001" showing "https://example.com/banner.png"
    And Google Ad Manager associated that creative with line items "5000001"

  # sync_creatives accepts an assignment to another buyer's package and reports it assigned
  # (#2417). Nothing of it reaches that buyer's line item.
  @T-UC-006-local-gam-other-buyers-package
  Scenario: An assignment to another buyer's booked package sends nothing to GAM
    Given another buyer of the seller holds a buy booked in Google Ad Manager as order "6000004" whose package "pkg-other-buyer" is line item "5000004" accepting 300x250
    And the buyer syncs a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-other-buyer" with weight 40
    When the Buyer Agent syncs the creative with assignments
    Then creative "cr-banner" is assigned to package "pkg-other-buyer"
    And Google Ad Manager created no creative
    And Google Ad Manager changed no line item

  # The seller's approval traffics a creative onto a buy as that buy's owner. The assignment
  # row sync_creatives wrote on another buyer's live buy (#2417) is not that owner's.
  @T-UC-006-local-gam-approval-other-buyers-buy
  Scenario: Approving a creative assigned to another buyer's live buy sends nothing to GAM
    Given the tenant has approval_mode "require-human"
    And another buyer of the seller holds a buy booked in Google Ad Manager as order "6000006" in status "active" whose package "pkg-other-live" is line item "5000006" accepting 300x250
    And the buyer synced a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-other-live" with weight 40
    When the seller approves creative "cr-banner"
    Then Google Ad Manager created no creative
    And Google Ad Manager changed no line item

  # A buy the seller has not approved has no line item yet. Its creatives are sent when the
  # seller approves the buy.
  @T-UC-006-local-gam-unbooked-package
  Scenario: An assignment to a package not yet booked in GAM is assigned and sends nothing to GAM
    Given the buyer holds a buy awaiting the seller's approval, not booked in Google Ad Manager, with package "pkg-unbooked"
    And the buyer syncs a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-unbooked"
    When the Buyer Agent syncs the creative with assignments
    Then creative "cr-banner" is assigned to package "pkg-unbooked"
    And Google Ad Manager created no creative

  # The seller approved the buy while its creative was under review, so nothing of it is in
  # GAM. Approving the creative books the buy, and the creative goes onto the line item GAM
  # created for its package (#2378).
  @T-UC-006-local-gam-approval-books-held-buy
  Scenario: Approving the creative an unbooked buy waits for books the buy and places the creative on its line item
    Given the tenant has approval_mode "require-human"
    And the buyer holds a buy the seller approved and holds for its creatives, not booked in Google Ad Manager, with package "pkg-unbooked"
    And the buyer synced a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-unbooked"
    When the seller approves creative "cr-banner"
    Then Google Ad Manager created one image creative for advertiser "3000001" showing "https://example.com/banner.png"
    And Google Ad Manager associated each creative it created with the line item it created

  # A record written before GAM's id had its own key holds the buyer's id as the ad server's
  # (#2247). That id is not one GAM has, so the creative is created.
  @T-UC-006-local-gam-buyer-id-not-read-as-gams
  Scenario: A creative recorded with the buyer's own id as the ad server's is created in GAM
    Given the buyer holds an approved 300x250 image creative "cr-banner" showing "https://example.com/banner.png", recorded with its own id as the ad server's
    When the Buyer Agent references library creative "cr-banner", without resending it, assigned to package "pkg-banner-a"
    Then creative "cr-banner" is assigned to package "pkg-banner-a"
    And Google Ad Manager created one image creative for advertiser "3000001" showing "https://example.com/banner.png"
    And Google Ad Manager associated that creative with line items "5000001"

  # Broadstreet stores a line item id on each package too, and takes no creatives from the seller.
  @T-UC-006-local-gam-other-ad-server-takes-no-creatives
  Scenario: An ad server that takes no creatives from the seller leaves the assignment as reported
    Given the seller's ad server is now "broadstreet", which takes no creatives from the seller
    And the buyer syncs a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-banner-a"
    When the Buyer Agent syncs the creative with assignments
    Then creative "cr-banner" is assigned to package "pkg-banner-a"

  @T-UC-006-local-gam-creative-reused
  Scenario: A creative already in GAM is placed on a second package's line item, not created again
    Given the buyer holds a buy booked in Google Ad Manager as order "6000002" whose package "pkg-banner-b" is line item "5000002" accepting 300x250
    And the buyer synced a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-banner-a"
    When the Buyer Agent references library creative "cr-banner", without resending it, assigned to package "pkg-banner-b"
    Then creative "cr-banner" is assigned to package "pkg-banner-b"
    And Google Ad Manager created one image creative for advertiser "3000001" showing "https://example.com/banner.png"
    And Google Ad Manager associated that creative with line items "5000001, 5000002"

  @T-UC-006-local-gam-dry-run-stays-local
  Scenario: A dry-run sync of an assignment sends nothing to GAM
    Given the buyer syncs a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-banner-a" as a dry run
    When the Buyer Agent syncs the creative with assignments
    Then creative "cr-banner" is assigned to package "pkg-banner-a"
    And Google Ad Manager created no creative

  @T-UC-006-local-gam-pending-creative-held
  Scenario: A creative awaiting the seller's review is assigned but not sent to GAM
    Given the tenant has approval_mode "require-human"
    And the buyer syncs a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-banner-a"
    When the Buyer Agent syncs the creative with assignments
    Then creative "cr-banner" is assigned to package "pkg-banner-a"
    And Google Ad Manager created no creative

  # Approving it a second time finds it on the line item and sends no second association.
  # The id the approval records is GAM's own: the later assignment places that creative on
  # the second line item instead of creating it again.
  @T-UC-006-local-gam-approval-on-live-buy
  Scenario: Approving a creative held back from a live buy places it on the buy's line item and records GAM's id
    Given the tenant has approval_mode "require-human"
    And the buyer holds a buy booked in Google Ad Manager as order "6000005" in status "active" whose package "pkg-live" is line item "5000005" accepting 300x250
    And the buyer synced a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-live"
    When the seller approves creative "cr-banner"
    Then Google Ad Manager created one image creative for advertiser "3000001" showing "https://example.com/banner.png"
    And Google Ad Manager associated that creative with line items "5000005"
    When the seller approves creative "cr-banner"
    Then Google Ad Manager associated that creative with line items "5000005"
    When the Buyer Agent references library creative "cr-banner", without resending it, assigned to package "pkg-banner-a"
    Then creative "cr-banner" is assigned to package "pkg-banner-a"
    And Google Ad Manager created one image creative for advertiser "3000001" showing "https://example.com/banner.png"
    And Google Ad Manager associated that creative with line items "5000005, 5000001"

  # The buyer re-sends the creative it already synced, with the same assignment. GAM is asked
  # and already holds the creative on that line item, so no creative and no association is
  # created again.
  @T-UC-006-local-gam-resend-no-second-creative
  Scenario: Re-sending a creative GAM already holds, with the same assignment, creates nothing in GAM
    Given the buyer synced a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-banner-a"
    And the buyer syncs a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-banner-a"
    When the Buyer Agent syncs the creative with assignments
    Then creative "cr-banner" is assigned to package "pkg-banner-a"
    And Google Ad Manager created one image creative for advertiser "3000001" showing "https://example.com/banner.png"
    And Google Ad Manager associated that creative with line items "5000001"

  # The assignment was stored while the creative was under review, so nothing was sent for it.
  # GAM holds the creative from another package; that does not place it on this one.
  @T-UC-006-local-gam-resend-unplaced-assignment
  Scenario: Re-sending an assignment GAM does not hold places the creative on that line item
    Given the buyer holds a buy booked in Google Ad Manager as order "6000002" whose package "pkg-banner-b" is line item "5000002" accepting 300x250
    And the buyer synced a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-banner-a"
    And the tenant has approval_mode "require-human"
    And the buyer synced a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-banner-b"
    And the tenant has approval_mode "auto-approve"
    And the buyer syncs a 300x250 image creative "cr-banner" showing "https://example.com/banner.png" assigned to package "pkg-banner-b"
    When the Buyer Agent syncs the creative with assignments
    Then creative "cr-banner" is assigned to package "pkg-banner-b"
    And Google Ad Manager created one image creative for advertiser "3000001" showing "https://example.com/banner.png"
    And Google Ad Manager associated that creative with line items "5000001, 5000002"
