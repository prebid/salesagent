# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration). Ungraded by the AdCP storyboards:
# the spec says nothing about how a seller shares one database across tenants, so the
# authority is the seller's own contract in src/core/database/database_session.py and the
# defect it fixes (#2328).
#
# SUBJECT. get_db_session's fail-fast refuses EVERY session in the process for 10 seconds
# once it trips, whichever tenant the request is for. It exists for a database that cannot
# be reached, and only a connect the server never answered trips it:
#
# - a statement the server cancels on a live connection (a deadlock victim, a
#   statement_timeout, a lock timeout) fails that one request;
# - a connection the server drops (an admin terminate, idle_session_timeout, a pooler
#   closing a client) fails that one request, and the pool reconnects for the next;
# - a connect the server refuses before answering any SQL trips the fail-fast, so the
#   next request is refused even after the database is back.
#
# The credential lookup every request makes first runs under execute_with_retry, which reads
# the same signal: a dropped connection is retried on a new one, and a statement the server
# cancelled is not re-run.
#
# HOW: the env's database-fault methods (tests/harness/database_faults.py) make Postgres do
# each of those to the seller; nothing in the seller is patched. Two tenants, because the
# claim is about the tenant whose request did nothing wrong.
Feature: One failed statement does not take the seller down (local)
  As a publisher sharing one process across tenants,
  I want a failure the database answered to fail that request alone,
  so that one buyer's bad luck is not every buyer's outage.

  Background:
    Given two tenants each own a product the other does not

  @T-DBFAILFAST-cancelled-statement
  Scenario: A statement the database cancels fails that request and no other
    Given the database will cancel the seller's next statement
    When the buyer requests products with tenant "A" credentials
    Then the response contains error code INTERNAL_ERROR
    When the buyer requests products with tenant "B" credentials
    Then the response contains tenant "B" products

  @T-DBFAILFAST-dropped-connection
  Scenario: A connection the database drops fails that request and no other
    Given the database will drop the connection serving the seller's next statement
    When the buyer requests products with tenant "A" credentials
    Then the response contains error code INTERNAL_ERROR
    When the buyer requests products with tenant "B" credentials
    Then the response contains tenant "B" products

  # The refusal of tenant B is the fail-fast and nothing else: the database accepts
  # connections again before B asks, so without the trip B would be served.
  @T-DBFAILFAST-unanswered-connect
  Scenario: A connect the database never answers makes the next request fail fast
    Given the database refuses new connections
    When the buyer requests products with tenant "A" credentials
    Then the response contains error code INTERNAL_ERROR
    When the database accepts new connections again
    And the buyer requests products with tenant "B" credentials
    Then the response contains error code INTERNAL_ERROR

  # Attempt 2 runs on a new connection after the lock is gone, so it is served.
  @T-DBFAILFAST-lookup-dropped-connection
  Scenario: A credential lookup whose connection the database drops is retried
    Given the database will drop the connection serving the seller's credential lookup for tenant "A"
    When the buyer requests products with tenant "A" credentials
    Then the response contains tenant "A" products

  # The lock is gone after the cancel, so a retry would be served: the error is the
  # proof that the cancelled lookup ran once.
  @T-DBFAILFAST-lookup-cancelled-statement
  Scenario: A credential lookup the database cancels is not retried
    Given the database will cancel the seller's credential lookup for tenant "A"
    When the buyer requests products with tenant "A" credentials
    Then the response contains error code INTERNAL_ERROR
