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

  # A JSON API blueprint answers JSON whatever the caller's Accept says: curl and most HTTP
  # libraries send "Accept: */*", which a page-or-JSON guess from headers answers with HTML.
  @T-ADMIN-FAULT-004 @json
  Scenario: A script that accepts any format gets an API failure as JSON
    Given another tenant of this seller
    And the database refuses to change that tenant
    When a script deletes that tenant through the tenant management API, accepting any format
    Then the page returns status 500
    And the JSON response has "success" as false
    And the JSON error is a database error with a reference
    And the answer does not carry the database fault's text
    And the database refused the write once

  # The settings page posts the business-rules form as FormData through fetch(): the route
  # answers it in the format the handler would, so success, refusal and failure all reach
  # the page's script as JSON.
  @T-ADMIN-FAULT-005 @json
  Scenario: The business-rules form posted by fetch() gets a failure as JSON
    Given the database refuses to store the order name template "BDD fault {campaign_name}"
    When the settings page posts the business rules form by fetch with:
      | field               | value                     |
      | order_name_template | BDD fault {campaign_name} |
    Then the page returns status 500
    And the JSON response has "success" as false
    And the JSON error is a database error with a reference
    And the answer does not carry the database fault's text
    And the database refused the write once

  @T-ADMIN-FAULT-006 @json
  Scenario: The business-rules form posted by fetch() gets a refusal as JSON
    When the settings page posts the business rules form by fetch with:
      | field               | value           |
      | order_name_template | {campaign_name  |
    Then the page returns status 400
    And the JSON response has "success" as false
    And the JSON error is the "InvalidFormInput" refusal
    And the JSON issues are:
      | field               | text                                               |
      | order_name_template | order_name_template: Template has unbalanced braces |

  # A sync's failure is written in a unit of work of its own (src/services/sync_jobs.py):
  # the session its work used holds a transaction the database aborted, and committing on
  # it left the job running, which refused every later sync that day.
  @T-ADMIN-FAULT-007 @json
  Scenario: A sync whose database write fails is recorded as failed, with the reference it answered
    Given another tenant of this seller, onboarded to Google Ad Manager
    And the database refuses to mark that tenant's sync jobs running
    When the sync API is asked to sync that tenant's inventory
    Then the page returns status 500
    And the JSON response has "status" as "failed"
    And the JSON error is that tenant's inventory sync failure with a reference
    And the answer does not carry the database fault's text
    And the database refused the write once
    And the sync API reports the answered sync job as failed, with the answered error

  # A body that is JSON but not an object is the caller's to fix: refused for "body"
  # (src/admin/form_validation.py json_object_body), never a crash in the route's ".get".
  @T-ADMIN-FAULT-008 @json
  Scenario: An AI connection test whose body is not a JSON object is refused for its body
    When the settings page tests the AI connection with the JSON body "[1]"
    Then the page returns status 400
    And the JSON response has "success" as false
    And the JSON error is the "InvalidFormInput" refusal
    And the JSON issues are:
      | field | text                       |
      | body  | body must be a JSON object |

  # The answer's format is decided from the caller, never the route
  # (src/admin/utils/helpers.py caller_reads_json): only a browser asking for a page gets
  # HTML, so a script that sends curl's "Accept: */*", or no Accept at all, gets JSON.
  @T-ADMIN-FAULT-009 @json
  Scenario Outline: A script that does not ask for a page gets a failure as JSON
    Given another tenant of this seller, onboarded to Google Ad Manager
    And the database refuses to mark that tenant's sync jobs running
    When a script starts that tenant's inventory sync with no body, sending Accept "<accept>"
    Then the page returns status 500
    And the JSON response has "success" as false
    And the JSON error is a database error with a reference
    And the answer does not carry the database fault's text
    And the database refused the write once

    Examples:
      | accept |
      | */*    |
      | (none) |

  # The connection test runs the tenant's real Gemini model against the scripted model origin
  # (tests/e2e/scripted_llm_origin_service.py). A provider that fails the call itself, rather
  # than refusing the operator's key or model, is not the operator's to fix.
  @T-ADMIN-FAULT-010 @json @ai-test
  Scenario: An AI provider that fails the connection test is answered with a reference
    Given the AI provider answers the connection test with HTTP 503
    When the settings page tests the AI connection with that key
    Then the page returns status 500
    And the JSON response has "success" as false
    And the JSON error is an unexpected error with a reference

  # A decorated route that raises is recorded as failed in the audit log, and the activity
  # feed shows that row. The row carries fixed text (src/admin/utils/audit_decorator.py),
  # never the exception's.
  @T-ADMIN-FAULT-011 @browser @audit
  Scenario: A failed action is shown in the activity feed without the exception's text
    Given the database refuses to store an account named "Fault Probe Audited"
    When the operator submits the create account form from the create account page with:
      | field        | value                        |
      | name         | Fault Probe Audited          |
      | brand_domain | fault-probe-audited.example  |
    And the operator reads the tenant's recent activity
    Then the activity feed shows the create account action as failed with the fixed audit text
    And the activity feed does not carry the database fault's text
    And the database refused the write once
