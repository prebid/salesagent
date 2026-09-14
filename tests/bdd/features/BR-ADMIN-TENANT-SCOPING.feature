# Hand-authored feature — not compiled from adcp-req
# Admin UI BDD scenarios for tenant scoping of admin routes (prebid/salesagent#2203)

Feature: BR-ADMIN-TENANT-SCOPING Tenant-scoped admin routes prove membership
  As a publisher operating one tenant
  I want every admin route that names my tenant in its URL to check that the caller belongs to it
  So that a logged-in user of another tenant cannot read my revenue, catalogue or policy pages

  # A route that takes <tenant_id> from the URL must prove the caller belongs to
  # that tenant. Four admin routes carried only require_auth(), which checks that
  # somebody is logged in. The one component that proves membership is require_tenant_access
  # (src/admin/utils/helpers.py); every outcome below is that decorator's contract:
  #   - anonymous        -> JSON 401 on the three api_mode routes, redirect to the
  #                         tenant login page on the HTML route
  #   - non-member       -> 403 (JSON {"error": "Access denied"} on the API routes)
  #   - inactive member  -> 403, same shape
  #   - active member    -> today's behaviour, same status and body
  #   - any rejection    -> no state change
  #
  # The caller's session is always held against a tenant that is NOT the target,
  # so the target tenant's membership row is the only thing that decides.
  #
  # Transports:
  #   - integration: Flask test_client (in-process, no Docker) — tests/bdd
  #   - e2e: requests.Session against Docker stack (full deployment) —
  #     tests/e2e/test_admin_tenant_scoping_e2e.py drives the same harness

  Background:
    Given a target tenant with one catalogue product and one active media buy

  @T-ADMIN-SCOPE-001 @auth @edge-case
  Scenario Outline: Anonymous callers of the tenant APIs receive JSON 401
    Given the caller is not authenticated
    When the caller sends GET to the <route> of the target tenant
    Then the JSON response returns status 401
    And the JSON response has "error" as "Authentication required"
    And the target tenant's stored data is unchanged

    Examples:
      | route                   |
      | revenue chart API       |
      | products API            |
      | product suggestions API |

  @T-ADMIN-SCOPE-002 @auth @edge-case
  Scenario Outline: Anonymous callers of the policy rules page are sent to the tenant login
    Given the caller is not authenticated
    When the caller sends <method> to the policy rules page of the target tenant
    Then the page redirects to the login page of the target tenant
    And the target tenant's stored data is unchanged

    Examples:
      | method |
      | GET    |
      | POST   |

  @T-ADMIN-SCOPE-003 @auth @edge-case
  Scenario Outline: A member of another tenant receives JSON 403 from the tenant APIs
    Given the caller is an active member of a different tenant
    When the caller sends GET to the <route> of the target tenant
    Then the JSON response returns status 403
    And the JSON response has "error" as "Access denied"
    And the target tenant's stored data is unchanged

    Examples:
      | route                   |
      | revenue chart API       |
      | products API            |
      | product suggestions API |

  @T-ADMIN-SCOPE-004 @auth @edge-case
  Scenario Outline: A member of another tenant receives 403 from the policy rules page
    Given the caller is an active member of a different tenant
    When the caller sends <method> to the policy rules page of the target tenant
    Then the page returns status 403
    And the target tenant's stored data is unchanged

    Examples:
      | method |
      | GET    |
      | POST   |

  @T-ADMIN-SCOPE-005 @auth @edge-case
  Scenario Outline: An inactive membership in the target tenant receives JSON 403 from the tenant APIs
    Given the caller's membership in the target tenant is inactive
    When the caller sends GET to the <route> of the target tenant
    Then the JSON response returns status 403
    And the JSON response has "error" as "Access denied"
    And the target tenant's stored data is unchanged

    Examples:
      | route                   |
      | revenue chart API       |
      | products API            |
      | product suggestions API |

  @T-ADMIN-SCOPE-006 @auth @edge-case
  Scenario Outline: An inactive membership in the target tenant receives 403 from the policy rules page
    Given the caller's membership in the target tenant is inactive
    When the caller sends <method> to the policy rules page of the target tenant
    Then the page returns status 403
    And the target tenant's stored data is unchanged

    Examples:
      | method |
      | GET    |
      | POST   |

  @T-ADMIN-SCOPE-007 @auth @main-flow
  Scenario: An active member reads the tenant's revenue chart
    Given the caller is an active member of the target tenant
    When the caller sends GET to the revenue chart API of the target tenant
    Then the JSON response returns status 200
    And the revenue chart lists the target tenant's active media buy

  @T-ADMIN-SCOPE-008 @auth @main-flow
  Scenario: An active member reads the tenant's product catalogue
    Given the caller is an active member of the target tenant
    When the caller sends GET to the products API of the target tenant
    Then the JSON response returns status 200
    And the product list is the target tenant's catalogue

  @T-ADMIN-SCOPE-009 @auth @main-flow
  Scenario: An active member reads the product suggestions
    Given the caller is an active member of the target tenant
    When the caller sends GET to the product suggestions API of the target tenant
    Then the JSON response returns status 200
    And the suggestions list the default catalogue

  @T-ADMIN-SCOPE-010 @auth @main-flow
  Scenario Outline: An active member is redirected from the policy rules page to the policy page
    Given the caller is an active member of the target tenant
    When the caller sends <method> to the policy rules page of the target tenant
    Then the page redirects to the policy page of the target tenant

    Examples:
      | method |
      | GET    |
      | POST   |
