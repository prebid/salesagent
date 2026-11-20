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

  # A form the seller cannot save is refused with InvalidFormInput: its MESSAGE says the
  # form was refused, and each issue names a refused field in text the validator wrote,
  # never another exception's text. A browser is shown MESSAGE and every issue.

  @T-ADMIN-REFUSAL-006 @product-form @pricing
  Scenario: A pricing option the product form refuses is named on the form
    # The add form renders itself again rather than going back, so the GAM form can keep
    # what the operator typed.
    When the operator submits the add product form from its page with:
      | field           | value                 |
      | name            | Refused Pricing Probe |
      | pricing_model_0 | cpm_fixed             |
      | rate_0          |                       |
    Then the page returns status 200
    And the page is the add product form
    And the page shows the "InvalidFormInput" refusal
    And the page contains "Rate is required for fixed pricing (pricing option 0)"

  @T-ADMIN-REFUSAL-007 @inventory-profile-form @format-options
  Scenario: Format options that are not JSON are refused with where the parse stopped
    # The edit form is answered by the error handler: back to the page it came from.
    Given the seller holds a verified property of publisher "pub-psatest.com"
    And a stored profile "unparsed_options" declaring an image of 300x250
    When the operator saves the edit form of profile "unparsed_options" from its page with format options "[{"
    Then the page returns status 200
    And the page shows the "InvalidFormInput" refusal
    And the page contains "format_options is not valid JSON at line 1, column 3"
    And the answer does not carry the JSON parser's text
    And the profile "unparsed_options" stores an image of 300x250
