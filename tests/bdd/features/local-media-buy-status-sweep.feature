# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration). Ungraded by the AdCP storyboards:
# docs/building/verification/compliance-catalog.mdx at v3.2.7 defines status.monotonic as
# rejecting off-graph transitions. The sweep's two flight-date edges, pending_start ->
# active and active -> completed, are on the graph. A pending_start buy whose whole flight
# passed between ticks is written completed, an edge the graph does not have (#2508).
# universal/deterministic-testing.yaml at v3.2.7 declares requires: [controller], and
# universal/storyboard-schema.yaml (section "requires") gates that on the agent listing
# comply_test_controller in its get_adcp_capabilities tools. This seller does not, so the
# runner skips the storyboard with requirement_unmet (detail "controller"). Exposing the
# controller would not grade this sweep either: compliance/comply-test-controller-request.json
# at v3.2.7 forces statuses (force_*), simulates delivery and spend (simulate_*), and its
# advance_time operations move a catalog or reporting clock (catalog_item_availability_probe,
# reporting_core_lifecycle_probe). No operation moves a media buy's flight dates, which is
# what this time-driven sweep reads.
#
# SUBJECT. One raising media buy must not cost its siblings their status flip. The
# containment unit is one loop iteration and one SAVEPOINT. A persisted pending_start row
# reads pending_start from get_media_buys until this sweep writes the column
# (docs/media-buy/media-buys/lifecycle.mdx at v3.2.7).
#
# HOW. The tick runs in the test process. The transports cross on the get_media_buys read.
# The fault is a BEFORE UPDATE trigger on media_buys (tests/harness/database_faults.py),
# or, for the refused commit, a deferred constraint trigger that fires at COMMIT.
# The trigger is installed before the rows are inserted, and the rows are inserted in
# reverse id order, so the sweep visits them in id order only because it asks for that order.
Feature: A failing media buy does not take its siblings' status flips (local)
  As a publisher running one status sweep across tenants,
  I want one buy's failure recorded and the others still flipped,
  so that a single bad row is not a lost flight for everyone else.

  @T-SWEEP-sibling-durability
  Scenario Outline: One failing buy leaves its siblings' flips durable
    Given tenant "A" has three pending_start buys past their flight start
    And tenant "B" has one pending_start buy past its flight start
    And the database will fail the status write for tenant "A" buy <slot>
    When the status sweep runs
    Then tenant "A" buy <slot> reads "pending_start" from get_media_buys
    And the other tenant "A" buys and the tenant "B" buy read "active"
    And the isolation error count for tenant "A" rose by 1 with error_type "db_error"
    And the isolation error count for tenant "B" did not move
    And the sweep summary is the INFO "3 updated, 1 errors"

    Examples:
      | slot |
      | 0    |
      | 1    |
      | 2    |

  @T-SWEEP-dropped-connection
  Scenario: A dropped connection stops the sweep and persists nothing
    Given tenant "A" has three pending_start buys past their flight start
    And the database will drop the connection serving the status write for tenant "A" buy 2
    When the status sweep runs
    Then all three buys read "pending_start" from get_media_buys
    And the sweep logged no status flip
    And the isolation error count for tenant "A" did not move
    And the sweep summary is the WARNING "0 persisted (2 pending flips lost), 0 errors"

  @T-SWEEP-commit-refused
  Scenario: A refused commit persists nothing and claims no flip
    Given tenant "A" has three pending_start buys past their flight start
    And the database will refuse the sweep's commit for tenant "A"
    When the status sweep runs
    Then all three buys read "pending_start" from get_media_buys
    And the sweep logged no status flip
    And the isolation error count for tenant "A" did not move
    And the sweep summary is the WARNING "0 persisted (3 pending flips lost), 0 errors"

  @T-SWEEP-summary
  Scenario Outline: The sweep summary's severity follows what happened
    Given <buys>
    When the status sweep runs
    Then the sweep did not stop on an error
    And the sweep summary is <summary>

    Examples:
      | buys                                      | summary                           |
      | three buys whose status writes all fail   | the WARNING "0 updated, 3 errors" |
      | four mid-flight buys and one failing buy  | the INFO "0 updated, 1 errors"    |
      | one mid-flight buy with nothing to change | absent                            |
