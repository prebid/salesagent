# Hand-authored feature — not compiled from adcp-req
# Admin UI BDD scenario for the operator's media buy page (prebid/salesagent#2647)

Feature: BR-ADMIN-MEDIA-BUY-PAGE The operator's media buy page shows what the seller recorded
  As a publisher operating one tenant
  I want the media buy page to show each package's budget as the seller records it
  So that the page agrees with what get_media_buys tells the buyer after an update

  # A package's budget has one record: the media_packages.budget column. update_media_buy
  # writes it (src/core/tools/media_buy_update.py) and get_media_buys reads it
  # (src/core/tools/media_buy_list.py). package_config keeps the budget the package was
  # created with, which an update does not change. The admin UI is not AdCP protocol, so
  # no spec section applies; the column being the seller's one budget record is the rule.
  #
  # The admin harness drives admin pages and cannot call update_media_buy, so the Given
  # stores the row an updated package holds: the column at the new budget and
  # package_config at the creation budget.

  Background:
    Given an admin user is authenticated for tenant "test-tenant"

  @T-ADMIN-MBPAGE-001 @main-flow
  Scenario: The page shows a package's budget after an update changed it
    Given the buy "mb_updated" holds package "pkg_001" with a budget of 7000, created with 5000
    When the operator opens the page of the buy "mb_updated"
    Then the page shows the package "pkg_001" with the budget "$7000.00"
