# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# Upstream gap: no BR-UC-003 scenario sends more than one change in a request — two package
# budgets, or a package budget beside the campaign's paused — and reads the outcome back, and
# none refuses a request after part of it has reached the ad server. The pin's
# total_budget_redistribution storyboard reads two package budgets back through
# get_media_buys, but it drives a media-buy-level total_budget, which this seller does not
# implement. These scenarios grade the transaction boundary itself: a request that changes
# several things is stored as a whole, and a request refused part-way stores none of it.
#
# The ad server is told about each change one call at a time (one per package change, then
# the campaign pause), so the refusal scenario fails the second call. Any write the ad server
# made for the first call may stand there; what the seller records and serves back must not.
#
# Reconcile upstream in adcp-req (multi-change and refused-midway rows on the BR-UC-003
# transaction boundary), then retire this file. prebid/salesagent#2647.
#
# @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/update_media_buy.mdx section="Compatibility transaction boundary"
# @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/update_media_buy.mdx line=274
# @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/get-media-buys-response.json pointer=/properties/media_buys/items/properties/revision
Feature: UC-003 update_media_buy — one request is one atomic mutation (local)

  # BR-UC-003's Background, unchanged: its last Given maps the label "mb_existing" to the
  # seeded buy.
  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant exists with completed setup checklist
    And the Buyer is authenticated
    And the Buyer owns an existing media buy with media_buy_id "mb_existing"
    And the media buy is in "active" status

  # update_media_buy.mdx @ v3.2.7 line 25: "One `update_media_buy` request is one atomic
  # mutation." Line 274: "Every mutating update increments `revision` and returns the new
  # value in the success response." The pin does not say by how much one request moves it.
  @T-UC-003-local-atomic-two-package-budgets @post-s1 @post-s2
  Scenario: one update that changes two packages' budgets stores both, and get_media_buys serves them
    Given the tenant is configured for auto-approval
    And the media buy "mb_existing" is in "active" status with packages "pkg_001" and "pkg_002"
    And package "pkg_001" holds a budget of 5000 and is not paused
    And package "pkg_002" holds a budget of 5000 and is not paused
    And the media buy "mb_existing" is at revision 7
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request updates these packages, in order:
    | package_id | field  | value |
    | pkg_001    | budget | 6000  |
    | pkg_002    | budget | 7000  |
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the response should include "revision" with an integer value of at least 8
    When the Buyer Agent reads media buy "mb_existing" back with get_media_buys
    Then get_media_buys reports package "pkg_001" with budget 6000
    And get_media_buys reports package "pkg_002" with budget 7000
    And get_media_buys reports media buy "mb_existing" at a revision greater than 7

  # The same boundary across the two levels a payload can carry: package budget and campaign
  # pause ("budget, targeting, catalog, pause, and billing changes in the same payload",
  # update_media_buy.mdx @ v3.2.7). The campaign pause is applied last.
  @T-UC-003-local-atomic-budget-and-campaign-pause @post-s1 @post-s2
  Scenario: one update that changes a package budget and the campaign's paused stores the budget, and get_media_buys serves it
    Given the tenant is configured for auto-approval
    And package "pkg_001" holds a budget of 5000 and is not paused
    And the media buy "mb_existing" is at revision 7
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    | paused       | true        |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 6000    |
    When the Buyer Agent sends the update_media_buy request
    Then the response status should be "completed"
    And the response should include "revision" with an integer value of at least 8
    When the Buyer Agent reads media buy "mb_existing" back with get_media_buys
    Then get_media_buys reports package "pkg_001" with budget 6000
    And get_media_buys reports media buy "mb_existing" at a revision greater than 7

  # update_media_buy.mdx @ v3.2.7, Compatibility transaction boundary: "The seller validates
  # the complete post-update state before applying any field. [...] No package, assignment,
  # library creative, or MediaBuy field from the rejected update may change." pkg_001's pause
  # reaches the ad server first; pkg_002's budget is refused there. The refusal is the ad
  # server's transient failure, SERVICE_UNAVAILABLE (enums/error-code.json), and the seller
  # stores nothing of the request: the pause, the budget and the revision all read back as
  # they were.
  @T-UC-003-local-atomic-refused-midway @post-f1 @post-f2 @post-f3
  Scenario: an update the ad server refuses after its first change stores none of the request
    Given the tenant is configured for auto-approval
    And the media buy "mb_existing" is in "active" status with packages "pkg_001" and "pkg_002"
    And package "pkg_001" holds a budget of 5000 and is not paused
    And package "pkg_002" holds a budget of 5000 and is not paused
    And the media buy "mb_existing" is at revision 7
    And the ad server refuses the "update_package_budget" update
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request updates these packages, in order:
    | package_id | field  | value |
    | pkg_001    | paused | true  |
    | pkg_002    | budget | 7000  |
    When the Buyer Agent sends the update_media_buy request
    Then the operation should fail with error code "SERVICE_UNAVAILABLE"
    And the ad server received the updates "pause_package, update_package_budget"
    When the Buyer Agent reads media buy "mb_existing" back with get_media_buys
    Then get_media_buys reports package "pkg_001" with paused false
    And get_media_buys reports package "pkg_002" with budget 5000
    And the media buy "mb_existing" revision should be 7
