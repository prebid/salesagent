# Hand-authored feature — not compiled from adcp-req
# Admin UI: an expected refusal reaches the operator as its fixed text (#2571)

Feature: BR-ADMIN-OPERATOR-REFUSALS Expected refusals in the admin app
  As a super admin
  I want a value the seller refuses to be answered with what is wrong with it
  So that I can correct it, rather than see a server error

  # A refusal is an OperatorRefusal subclass (src/core/operator_errors.py). Its class
  # names its operator-facing text, MESSAGE; the exception's own text carries ids and the
  # refused value for the log. Routes let it escape, and the admin app's one error handler
  # (src/admin/utils/operator_errors.py) answers it with 400 and MESSAGE: JSON for fetch and
  # API callers, carrying any key the refusal names for them; for a browser, a flash on the
  # page it came from, or the error page when it names none.
  #
  # Transports:
  #   - integration: Flask test_client (in-process, no Docker)
  #   - e2e: requests.Session against Docker stack (full deployment)

  Background:
    Given an admin user is authenticated for tenant "bdd_admin_tenant"

  @T-ADMIN-REFUSAL-001 @create-tenant @virtual-host
  Scenario: A virtual host the column refuses is shown on the create tenant form
    When the operator submits the create tenant form from the create tenant page with:
      | field        | value                               |
      | name         | Refused Host Seller                 |
      | subdomain    | refused_host_seller                 |
      | virtual_host | https://refused-host.example/agent  |
    Then the page returns status 200
    And the page is the create tenant form
    And the page shows the "InvalidVirtualHost" refusal
    And the answer does not echo the submitted virtual host

  @T-ADMIN-REFUSAL-002 @create-tenant @virtual-host
  Scenario: With no page to go back to, the refusal is shown on the error page
    When the operator submits the create tenant form with no page to go back to, with:
      | field        | value                               |
      | name         | Refused Host Seller                 |
      | subdomain    | refused_host_seller                 |
      | virtual_host | https://refused-host.example/agent  |
    Then the page returns status 400
    And the page shows the "InvalidVirtualHost" refusal
    And the answer does not echo the submitted virtual host

  @T-ADMIN-REFUSAL-003 @json
  Scenario: A fetch() caller gets the refusal as JSON
    When the operator saves an OIDC configuration with:
      | field         | value     |
      | provider      | custom    |
      | client_id     | client-1  |
      | client_secret | secret-1  |
    Then the page returns status 400
    And the JSON response has "success" as false
    And the JSON error is the "DiscoveryUrlRequired" refusal

  @T-ADMIN-REFUSAL-004 @json @virtual-host
  Scenario: An API caller is told which field was refused
    When the tenant management API is asked to create a tenant with:
      | field        | value                               |
      | name         | Refused Host Seller                 |
      | subdomain    | refused_host_seller                 |
      | ad_server    | mock                                |
      | virtual_host | https://refused-host.example/agent  |
    Then the page returns status 400
    And the JSON response has "success" as false
    And the JSON error is the "InvalidVirtualHost" refusal
    And the JSON response has "field" as "virtual_host"
    And the answer does not echo the submitted virtual host

  @T-ADMIN-REFUSAL-005 @json @tenant-user
  Scenario: A tenant's own admin gets the refusal, not a server error
    # Not a super admin: the access check reads the tenant's users row, and the route
    # runs after it, so what the route raises still reaches the error handler.
    Given the operator is signed in as "refusal-admin@bdd-refusals.example", an admin of the tenant
    When the operator saves an OIDC configuration with:
      | field         | value     |
      | provider      | custom    |
      | client_id     | client-1  |
      | client_secret | secret-1  |
    Then the page returns status 400
    And the JSON response has "success" as false
    And the JSON error is the "DiscoveryUrlRequired" refusal
