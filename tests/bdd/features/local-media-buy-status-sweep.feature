# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration). Ungraded by the AdCP storyboards:
# docs/building/verification/compliance-catalog.mdx at v3.1.1 defines status.monotonic as
# rejecting off-graph transitions, and both edges this sweep writes are on the graph.
# comply_test_controller has no src/ references, so universal/deterministic-testing.yaml
# skips with missing_test_controller. Exposing it would grade forced transitions and would
# still not run this time-driven sweep.
#
# SUBJECT. One raising media buy must not cost its siblings their status flip. The
# containment unit is one loop iteration and one SAVEPOINT. A persisted pending_start row
# reads pending_start from get_media_buys until this sweep writes the column
# (docs/media-buy/media-buys/lifecycle.mdx at v3.1.1).
#
# HOW. The tick runs in the test process. The transports cross on the get_media_buys read.
# The fault is a BEFORE UPDATE trigger on media_buys (tests/harness/database_faults.py).
# The trigger is installed before the rows are inserted.
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
    And the database will drop the connection serving the status write for tenant "A" buy 1
    When the status sweep runs
    Then all three buys read "pending_start" from get_media_buys
    And the isolation error count for tenant "A" did not move
    And the sweep summary is the WARNING "0 persisted (1 pending flips lost), 0 errors"

  @T-SWEEP-summary
  Scenario Outline: The sweep summary's severity follows what happened
    Given <buys>
    When the status sweep runs
    Then the sweep summary is <summary>

    Examples:
      | buys                                      | summary                           |
      | three buys whose status writes all fail   | the WARNING "0 updated, 3 errors" |
      | four mid-flight buys and one failing buy  | the INFO "0 updated, 1 errors"    |
      | one mid-flight buy with nothing to change | absent                            |
