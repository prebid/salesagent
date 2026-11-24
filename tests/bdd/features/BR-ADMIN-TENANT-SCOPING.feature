# Hand-authored feature — not compiled from adcp-req
# Admin UI BDD scenarios for tenant scoping of admin routes (prebid/salesagent#2203, #2204)

Feature: BR-ADMIN-TENANT-SCOPING Tenant-scoped admin routes prove membership
  As a publisher operating one tenant
  I want every admin route that names my tenant in its URL to check that the caller belongs to it and holds the role the route asks for
  So that a user of another tenant cannot read my revenue, catalogue, policy pages or GAM reports or start a sign-in test on my tenant, and a member of my tenant needs the admin role to start one

  # A route that takes <tenant_id> from the URL must prove the caller belongs to
  # that tenant. Four admin routes carried only require_auth(), which checks that
  # somebody is logged in, and the OIDC test route carried no check at all (#2203).
  # The six GAM reporting routes carried a private copy of require_auth and a private
  # tenant check that answered 500 to every ordinary login (#2204). The one
  # component that proves membership is require_tenant_access
  # (src/admin/utils/helpers.py); every outcome below is that decorator's contract:
  #   - anonymous        -> JSON 401 for callers asking for JSON, redirect to the
  #                         tenant login page for callers navigating to a page
  #   - non-member       -> 403 (JSON {"error": "Access denied"} on the API routes)
  #   - inactive member  -> 403, same shape
  #   - active member below the role the route asks for
  #                      -> 403 (JSON {"error": "Your role does not allow this"} when
  #                         the caller asks for JSON)
  #   - active member holding that role
  #                      -> today's behaviour, same status and body
  #   - any rejection    -> no state change
  #
  # The caller's session is always held against a tenant that is NOT the target,
  # so the target tenant's membership row is the only thing that decides.
  #
  # The caller's active row in the target tenant holds the manager role unless its
  # scenario names another. Manager is the least role that may send POST (role_permits
  # in src/admin/utils/helpers.py), so the role check decides no outcome on the four
  # routes of #2203 or on the six GAM reporting routes of #2204, which serve GET only.
  # The OIDC test page is a POST and asks for the admin role: the
  # callback of the flow it starts turns the tenant's SSO on, as the admin-only
  # oidc.enable does. T-ADMIN-SCOPE-011 seeds an admin there, and T-ADMIN-SCOPE-012 a
  # manager and a viewer.
  #
  # A row that must not let the caller in holds the admin role: the other member of the
  # target tenant, the caller's row in another tenant, and the caller's inactive row.
  # Admin passes the role check of every route here, so only the membership lookup can
  # refuse on those rows, and a lookup that matched one of them would let the caller in.
  #
  # tests/integration/test_admin_role_enforcement.py grades the role on the routes of
  # its ADMIN_ONLY_ROUTES table, which the OIDC test page is not in.
  #
  # Transports (both run here, chosen at collection like BR-ADMIN-ACCOUNTS):
  #   - integration: Flask test_client (in-process, no Docker)
  #   - e2e: requests.Session against Docker stack (full deployment)

  Background:
    Given a target tenant with one catalogue product, one active media buy and an active member

  @T-ADMIN-SCOPE-001 @auth @edge-case
  Scenario Outline: Anonymous callers of the tenant APIs receive JSON 401
    Given the caller is not authenticated
    When the caller sends GET to the <route> of the target tenant
    Then the page returns status 401
    And the JSON response has "error" as "Authentication required"
    And the target tenant's stored data is unchanged

    Examples:
      | route                       |
      | revenue chart API           |
      | products API                |
      | product suggestions API     |
      | GAM reporting API           |
      | GAM advertiser summary API  |
      | GAM principal reporting API |
      | GAM country breakdown API   |
      | GAM ad unit breakdown API   |
      | GAM principal summary API   |

  @T-ADMIN-SCOPE-002 @auth @edge-case
  Scenario Outline: Anonymous callers of the tenant pages are sent to the tenant login
    Given the caller is not authenticated
    When the caller sends <method> to the <route> of the target tenant
    Then the page redirects to the login page of the target tenant
    And the target tenant's stored data is unchanged

    Examples:
      | method | route             |
      | GET    | policy rules page |
      | POST   | policy rules page |
      | POST   | OIDC test page    |

  @T-ADMIN-SCOPE-003 @auth @edge-case
  Scenario Outline: A member of another tenant receives JSON 403 from the tenant APIs
    Given the caller is an active member of a different tenant
    When the caller sends GET to the <route> of the target tenant
    Then the page returns status 403
    And the JSON response has "error" as "Access denied"
    And the target tenant's stored data is unchanged

    Examples:
      | route                       |
      | revenue chart API           |
      | products API                |
      | product suggestions API     |
      | GAM reporting API           |
      | GAM advertiser summary API  |
      | GAM principal reporting API |
      | GAM country breakdown API   |
      | GAM ad unit breakdown API   |
      | GAM principal summary API   |

  @T-ADMIN-SCOPE-004 @auth @edge-case
  Scenario Outline: A member of another tenant receives 403 from the tenant pages
    Given the caller is an active member of a different tenant
    When the caller sends <method> to the <route> of the target tenant
    Then the page returns status 403
    And the target tenant's stored data is unchanged

    Examples:
      | method | route             |
      | GET    | policy rules page |
      | POST   | policy rules page |
      | POST   | OIDC test page    |

  @T-ADMIN-SCOPE-005 @auth @edge-case
  Scenario Outline: An inactive membership in the target tenant receives JSON 403 from the tenant APIs
    Given the caller's membership in the target tenant is inactive
    When the caller sends GET to the <route> of the target tenant
    Then the page returns status 403
    And the JSON response has "error" as "Access denied"
    And the target tenant's stored data is unchanged

    Examples:
      | route                       |
      | revenue chart API           |
      | products API                |
      | product suggestions API     |
      | GAM reporting API           |
      | GAM advertiser summary API  |
      | GAM principal reporting API |
      | GAM country breakdown API   |
      | GAM ad unit breakdown API   |
      | GAM principal summary API   |

  @T-ADMIN-SCOPE-006 @auth @edge-case
  Scenario Outline: An inactive membership in the target tenant receives 403 from the tenant pages
    Given the caller's membership in the target tenant is inactive
    When the caller sends <method> to the <route> of the target tenant
    Then the page returns status 403
    And the target tenant's stored data is unchanged

    Examples:
      | method | route             |
      | GET    | policy rules page |
      | POST   | policy rules page |
      | POST   | OIDC test page    |

  @T-ADMIN-SCOPE-007 @auth @main-flow
  Scenario: An active member reads the tenant's revenue chart
    Given the caller is an active member of the target tenant
    When the caller sends GET to the revenue chart API of the target tenant
    Then the page returns status 200
    And the revenue chart lists the target tenant's active media buy

  @T-ADMIN-SCOPE-008 @auth @main-flow
  Scenario: An active member reads the tenant's product catalogue
    Given the caller is an active member of the target tenant
    When the caller sends GET to the products API of the target tenant
    Then the page returns status 200
    And the product list is the target tenant's catalogue

  @T-ADMIN-SCOPE-009 @auth @main-flow
  Scenario: An active member reads the product suggestions
    Given the caller is an active member of the target tenant
    When the caller sends GET to the product suggestions API of the target tenant
    Then the page returns status 200
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

  # The seeded tenant has no OIDC configuration, so what the route answers a caller it
  # admits is its "not configured" redirect to the tenant settings.
  @T-ADMIN-SCOPE-011 @auth @main-flow
  Scenario: An admin of the target tenant is redirected from the OIDC test page to the tenant settings
    Given the caller is an active member of the target tenant with the admin role
    When the caller sends POST to the OIDC test page of the target tenant
    Then the page redirects to the settings page of the target tenant

  # The 403 page carries no reason, so this caller asks for JSON: the answer then names
  # the role check, where the membership check answers "Access denied". The last step
  # counts the tenant's rows, tenant_auth_configs included, so a refusal that wrote one fails.
  @T-ADMIN-SCOPE-012 @auth @edge-case
  Scenario Outline: A member below the admin role is refused the OIDC test page
    Given the caller is an active member of the target tenant with the <role> role
    When the caller sends POST to the OIDC test page of the target tenant, asking for JSON
    Then the page returns status 403
    And the JSON response has "error" as "Your role does not allow this"
    And the target tenant's stored data is unchanged

    Examples:
      | role    |
      | manager |
      | viewer  |

  @T-ADMIN-SCOPE-013 @auth @main-flow
  Scenario Outline: An active member reaches the GAM reporting handlers
    # The target runs the mock adapter, so each handler's first answer is its own refusal
    # to report: an answer only a caller the guard let through can see.
    Given the caller is an active member of the target tenant
    When the caller sends GET to the <route> of the target tenant
    Then the page returns status 400
    And the JSON response has "error" as "<error>"

    Examples:
      | route                       | error                                                               |
      | GAM reporting API           | GAM reporting is only available for tenants using Google Ad Manager |
      | GAM advertiser summary API  | GAM reporting is only available for tenants using Google Ad Manager |
      | GAM principal reporting API | Principal does not have a GAM advertiser ID configured              |
      | GAM country breakdown API   | GAM reporting is only available for tenants using Google Ad Manager |
      | GAM ad unit breakdown API   | GAM reporting is only available for tenants using Google Ad Manager |
      | GAM principal summary API   | Principal does not have a GAM advertiser ID configured              |

  @T-ADMIN-SCOPE-014 @auth @main-flow
  Scenario Outline: A super admin of another tenant reaches the GAM reporting handlers
    # docs/security.md: super admins have full access to all tenants. The maintainer amended
    # #2204's AC4 to keep that access and pin it with a test
    # (https://github.com/prebid/salesagent/issues/2204#issuecomment-5712126259).
    # T-ADMIN-SCOPE-003 refuses the same caller without the grant.
    Given the caller is a super admin of a different tenant with no membership in the target tenant
    When the caller sends GET to the <route> of the target tenant
    Then the page returns status 400
    And the JSON response has "error" as "<error>"

    Examples:
      | route                       | error                                                               |
      | GAM reporting API           | GAM reporting is only available for tenants using Google Ad Manager |
      | GAM advertiser summary API  | GAM reporting is only available for tenants using Google Ad Manager |
      | GAM principal reporting API | Principal does not have a GAM advertiser ID configured              |
      | GAM country breakdown API   | GAM reporting is only available for tenants using Google Ad Manager |
      | GAM ad unit breakdown API   | GAM reporting is only available for tenants using Google Ad Manager |
      | GAM principal summary API   | Principal does not have a GAM advertiser ID configured              |

  # The media buys page names the products each buy's packages name. The buyer chose those
  # ids, so the page looks them up in the target tenant's catalogue alone
  # (tenants.media_buys_list): an id another tenant uses must not put that tenant's product
  # name on this page. The seeded buy names a product id the target's catalogue lacks. The
  # name holds no character HTML escapes, so its absence from the page is not an escape.
  @T-ADMIN-SCOPE-015 @auth @edge-case
  Scenario: The media buys page names no product of another tenant
    Given another tenant holds a product named "Product of another tenant" under the product id the target tenant's media buy names
    And the caller is an active member of the target tenant
    When the caller sends GET to the media buys page of the target tenant
    Then the page returns status 200
    And the media buys page lists the target tenant's media buy with no product named "Product of another tenant"
