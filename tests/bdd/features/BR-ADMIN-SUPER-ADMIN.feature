# Hand-authored feature — not compiled from adcp-req
# Admin UI: the all-tenants index decides through the one super-admin check

Feature: BR-ADMIN-SUPER-ADMIN The all-tenants index asks whether its caller is still a super admin
  As the operator of a seller deployment
  I want a super admin I remove from the configured lists to stop seeing every tenant
  So that a session signed in before the removal does not keep the deployment-wide index

  # A sign-in caches its super-admin answer in the session, and is_super_admin
  # (src/admin/utils/helpers.py) trusts that answer for SUPER_ADMIN_RECHECK_SECONDS, then
  # reads the configured lists again. The admin home page lists every tenant to a caller
  # that check finds a super admin, and sends anyone else to the tenant their session names.
  #
  # The caller's address is on no configured list in either deployment, so the age of the
  # cached answer is the only thing that separates the two scenarios.
  #
  # Transports:
  #   - integration: Flask test_client (in-process, no Docker)
  #   - e2e: requests.Session against Docker stack (full deployment)

  Background:
    Given the deployment serves many tenants

  @T-ADMIN-SUPER-001
  Scenario: A super-admin answer cached within the recheck interval is trusted
    Given the operator is signed in as "former-admin@bdd-super-admin.example", found a super admin just now
    When the operator opens the admin home page
    Then the page returns status 200
    And the page contains "<h2>Accounts</h2>"

  @T-ADMIN-SUPER-002
  Scenario: A super-admin answer cached longer than the recheck interval ago is asked again
    Given the operator is signed in as "former-admin@bdd-super-admin.example", found a super admin longer than the recheck interval ago
    When the operator opens the admin home page
    Then the page returns status 302

  @T-ADMIN-SUPER-003
  Scenario: A super-admin answer cached with no recorded time is asked again
    # A session signed in before the recheck shipped carries the cached answer and no time.
    Given the operator is signed in as "former-admin@bdd-super-admin.example", found a super admin with no recorded time
    When the operator opens the admin home page
    Then the page returns status 302
