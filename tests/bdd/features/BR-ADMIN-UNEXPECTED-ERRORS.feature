# Hand-authored feature — not compiled from adcp-req
# Admin UI: an unexpected failure reaches the operator as fixed text and a reference (#2571)

Feature: BR-ADMIN-UNEXPECTED-ERRORS Unexpected failures in the admin app
  As a seller's operator
  I want a failure the seller did not expect to tell me what failed and where it was logged
  So that no internal detail (a connection string, a driver dump, tenant data) reaches my screen

  # A route lets an unexpected exception escape, and the admin app's one error handler
  # (src/admin/utils/operator_errors.py) logs it once, with its traceback, under a short
  # reference, and answers 500 with fixed text and that reference: JSON for a fetch caller;
  # for a browser, a flash on the page it came from, or the error page when it names none.
  # A database error is answered with DB_ERROR_OPERATOR_MESSAGE (src/core/operator_errors.py).
  #
  # The fault is the database's own. The harness installs a trigger that refuses one row's
  # write with a message carrying a marker, so the route's real write fails on both
  # transports and nothing in src/ knows a test is running.
  #
  # Transports:
  #   - integration: Flask test_client (in-process, no Docker)
  #   - e2e: requests.Session against Docker stack (full deployment)

  Background:
    Given an admin user is authenticated for tenant "bdd_admin_tenant"

  @T-ADMIN-FAULT-001 @browser
  Scenario: A form post that fails is answered with a flash on the page it came from
    Given the database refuses to store an account named "Fault Probe Created"
    When the operator submits the create account form from the create account page with:
      | field        | value                        |
      | name         | Fault Probe Created          |
      | brand_domain | fault-probe-created.example  |
    Then the page returns status 200
    And the page is the create account form
    And the page shows a database error with a reference
    And the answer does not carry the database fault's text
    And the database refused the write once
    And the database contains no account named "Fault Probe Created"

  @T-ADMIN-FAULT-002 @browser
  Scenario: With no page to go back to, the failure is shown on the error page
    Given the database refuses to store an account named "Fault Probe Unanchored"
    When the operator submits the create account form with no page to go back to, with:
      | field        | value                           |
      | name         | Fault Probe Unanchored          |
      | brand_domain | fault-probe-unanchored.example  |
    Then the page returns status 500
    And the page is the error page
    And the page shows a database error with a reference
    And the answer does not carry the database fault's text
    And the database refused the write once
    And the database contains no account named "Fault Probe Unanchored"

  @T-ADMIN-FAULT-003 @json
  Scenario: A fetch() caller gets the failure as JSON
    Given the tenant has an account "Fault Probe Status" with status "active"
    And the database refuses to change account "Fault Probe Status"
    When the admin sends a status change request for "Fault Probe Status" to "suspended"
    Then the page returns status 500
    And the JSON response has "success" as false
    And the JSON error is a database error with a reference
    And the answer does not carry the database fault's text
    And the database refused the write once
    And the database shows account "Fault Probe Status" with status "active"
