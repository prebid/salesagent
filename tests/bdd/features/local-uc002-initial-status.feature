# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# What it grades: the status a buy starts in when create_media_buy assigns it creatives that
# are already approved. AdCP 3.1.1 enums/media-buy-status.json: `pending_creatives` is a buy
# that "has no creatives assigned — the buyer must attach creatives via `sync_creatives`
# before the buy can serve"; `pending_start` is "ready to serve and waiting for its flight
# date to begin"; `active` is "currently running". docs/media-buy/media-buys/lifecycle.mdx
# line 146: "`create_media_buy` accepts inline `creative_assignments` or `creatives` per
# package. If you supply them at creation time and the flight date has passed, the buy
# enters `active` directly ... If the flight date is in the future, it enters
# `pending_start`". The buyer observes the status on the create response and on
# get_media_buys.
#
# The state-machine storyboard's create_buy step grades the same transition, but it requires
# media_buy.creative_approval_mode auto_approve, which this seller does not declare, and it
# assigns the creative through creative_assignments, which create does not read (#2392). These
# scenarios are the in-process grading of that obligation, on a tenant that auto-approves
# creatives, with the creative uploaded inline on the package.
#
# The status is decided per package: the same section says "Delivery cannot begin until the
# buyer supplies at least one creative per package", so a buy with an approved creative on
# only some of its packages stays `pending_creatives`. A creative is the buyer's own: another
# principal's creative under the same id does not clear it. A buy whose own creative is still
# under review keeps `pending_creatives`; whether it should is #2399, and that scenario asserts
# today's answer only to show the other principal's approved creative does not change it.
#
# The generated outline @T-UC-002-v31-success-status-values (BR-UC-002-create-media-buy.feature)
# states the `pending_start` and `active` rows too, and does not execute: three of its steps
# have no definition, and it reads the media buy status from the top-level `status`, which
# media-buy/create-media-buy-response.json reserves for the task status and deprecates in
# favour of `media_buy_status` (#2415). When that outline executes and reads
# `media_buy_status`, the outline below is reduced to what it adds: the get_media_buys
# read-back.
#
# Not graded here: a library creative attached to a package by id. The pinned request attaches
# one through `packages[].creative_assignments`, which create does not read (#2392); the
# `creative_ids` field create does read is not in the 3.1.1 package-request schema.
#
# Transports: a2a, mcp, rest and e2e_rest. On each of them the create runs the seller's own
# adapter, the real Mock adapter (the read-back route hands the harness's adapter patch to
# production's get_adapter, which is how the live server builds it). So the scenarios on an
# auto-approve tenant also grade the Mock adapter's creative upload: it reads each creative's
# id under `creative_id`, the key the create path sends (#2420).
#
# @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/media-buy-status.json
# @source repo=adcp ref=v3.1.1 path=docs/media-buy/media-buys/lifecycle.mdx
# @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/domains/media-buy/state-machine.yaml pointer=/phases/setup/steps/create_buy
Feature: A media buy created with approved creatives starts in its flight state (local)

  @T-UC-002-local-initial-status-approved-creatives @extension
  Scenario Outline: A buy whose package carries an approved creative is <status>, not pending_creatives
    Given a valid create_media_buy request with:
      | field      | value              |
      | start_time | <start>            |
      | end_time   | {30 days from now} |
    And the account exists and is active
    And the tenant has approval_mode "auto-approve"
    And the product "prod-1" supports format_ids ["display_300x250_image"]
    And the request's package carries an inline image creative in its product's format
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the wire media_buy_status should be "<status>"
    When the Buyer Agent calls get_media_buys for the media buy it just created
    Then the included entry should expose the same media_buy_id and status "<status>"

    Examples:
      | start             | status        |
      | asap              | active        |
      | {7 days from now} | pending_start |

  @T-UC-002-local-initial-status-partial-creatives @extension
  Scenario: A buy whose approved creative covers only one of its two packages stays pending_creatives
    Given a valid create_media_buy request with:
      | field      | value              |
      | start_time | asap               |
      | end_time   | {30 days from now} |
    And the account exists and is active
    And the tenant has approval_mode "auto-approve"
    And the product "prod-1" supports format_ids ["display_300x250_image"]
    And the request includes 2 packages with valid product_ids
    And the request's first package carries an inline image creative in its product's format
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the wire media_buy_status should be "pending_creatives"

  @T-UC-002-local-initial-status-other-principal-creative @extension
  Scenario: Another principal's approved creative under the same id does not clear the buyer's creative under review
    Given a valid create_media_buy request with:
      | field      | value              |
      | start_time | asap               |
      | end_time   | {30 days from now} |
    And the account exists and is active
    And the tenant has approval_mode "require-human"
    And the product "prod-1" supports format_ids ["display_300x250_image"]
    And the request's package carries an inline image creative in its product's format
    And another principal of the tenant holds an approved creative under the id of the request's inline creative
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the wire media_buy_status should be "pending_creatives"
