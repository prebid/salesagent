# Hand-authored feature — not compiled from adcp-req
# Admin UI BDD scenarios for account management (salesagent-oj0.1.1)

Feature: BR-ADMIN-ACCOUNTS Admin Account Management
  As a Tenant Admin
  I want to manage accounts through the admin web interface
  So that I can list, create, edit, and control account status for my tenant

  # This feature covers the admin UI account management blueprint.
  # Unlike BR-UC-011 (API-level account management for buyer agents),
  # this feature tests human admin interactions through HTML forms.
  #
  # Transports:
  #   - integration: Flask test_client (in-process, no Docker)
  #   - e2e: requests.Session against Docker stack (full deployment)

  Background:
    Given an admin user is authenticated for tenant "test-tenant"
    And the tenant "test-tenant" exists in the database

  @T-ADMIN-ACCT-001 @list @main-flow
  Scenario: List accounts for tenant
    Given the tenant has the following accounts:
      | name       | status    | brand_domain  |
      | Acme Corp  | active    | acme-corp.com |
      | Beta Inc   | suspended | beta.com      |
    When the admin navigates to the accounts list page
    Then the page returns status 200
    And the page contains "Accounts"
    And the page shows 2 accounts
    And the page shows account "Acme Corp" with status "active"
    And the page shows account "Beta Inc" with status "suspended"

  @T-ADMIN-ACCT-002 @create @main-flow
  Scenario: Create a new account
    When the admin navigates to the create account page
    Then the page returns status 200
    And the page contains "Create New Account"
    When the admin submits the create account form with:
      | field         | value         |
      | name          | New Corp      |
      | brand_domain  | new-corp.com  |
      | operator      | example-media |
      | billing       | operator      |
      | payment_terms | net_30        |
    Then the admin is redirected to the accounts list
    And the database contains an account named "New Corp"
    And the account "New Corp" has brand domain "new-corp.com"

  @T-ADMIN-ACCT-003 @detail @main-flow
  Scenario: View account detail page
    Given the tenant has an account "Acme Corp" with status "active"
    When the admin navigates to the account detail page for "Acme Corp"
    Then the page returns status 200
    And the page contains "Acme Corp"
    And the page shows the account status as "active"
    And the page shows action buttons for "Suspended" and "Closed"
    And the page does not show action button for "Active"

  @T-ADMIN-ACCT-004 @edit @main-flow
  Scenario: Edit account mutable fields
    Given the tenant has an account "Acme Corp" with status "active"
    When the admin navigates to the edit page for "Acme Corp"
    Then the page returns status 200
    And the page contains "Edit Account"
    When the admin submits the edit form with:
      | field         | value             |
      | name          | Acme Corp Updated |
      | billing       | agent             |
      | payment_terms | net_60            |
    Then the admin is redirected to the account detail page
    And the database shows account "Acme Corp Updated" with billing "agent"

  @T-ADMIN-ACCT-005 @status @main-flow
  Scenario: Change account status via AJAX API
    Given the tenant has an account "Acme Corp" with status "active"
    When the admin sends a status change request for "Acme Corp" to "suspended"
    Then the JSON response has "success" as true
    And the JSON response has "status" as "suspended"
    And the database shows account "Acme Corp" with status "suspended"

  @T-ADMIN-ACCT-006 @status @validation @edge-case
  Scenario: Reject invalid status transition
    Given the tenant has an account "Acme Corp" with status "active"
    When the admin sends a status change request for "Acme Corp" to "rejected"
    Then the JSON response returns status 400
    And the JSON response has "success" as false
    And the JSON response has "error" containing "Cannot transition"
    And the database shows account "Acme Corp" with status "active"

  @T-ADMIN-ACCT-007 @filter @alternative
  Scenario: Filter accounts by status
    Given the tenant has the following accounts:
      | name       | status           | brand_domain |
      | Active Co  | active           | active.com   |
      | Pending Co | pending_approval | pending.com  |
      | Closed Co  | closed           | closed.com   |
    When the admin navigates to the accounts list page with status filter "active"
    Then the page returns status 200
    And the page shows 1 accounts
    And the page shows account "Active Co" with status "active"
    And the page does not show account "Pending Co"

  @T-ADMIN-ACCT-008 @validation @create @edge-case
  Scenario: Reject account creation with missing name
    When the admin submits the create account form with:
      | field        | value       |
      | name         |             |
      | brand_domain | unnamed.com |
    Then the admin is redirected back to the create page
    And the database does not contain an account with brand domain "unnamed.com"

  @T-ADMIN-ACCT-009 @auth @edge-case
  Scenario: Unauthenticated access is denied
    Given the admin user is not authenticated
    When the admin navigates to the accounts list page
    Then the page returns a redirect to the login page

  @T-ADMIN-ACCT-010 @status @detail @edge-case
  Scenario: Terminal status shows no transition buttons
    Given the tenant has an account "Closed Corp" with status "closed"
    When the admin navigates to the account detail page for "Closed Corp"
    Then the page returns status 200
    And the page shows the account status as "closed"
    And the page does not show any status action buttons
