# Hand-authored feature — not compiled from adcp-req
# Admin UI: the two listings of every tenant a deployment holds

Feature: BR-ADMIN-TENANT-LISTINGS The deployment's tenant listings show the tenants their filters keep
  As the operator of a seller deployment
  I want the all-tenants index and the tenant management API to list the tenants the deployment holds
  So that I can find the tenants that name no ad server or have sold nothing, and see which have an adapter configured

  # The all-tenants index (render_super_admin_index in src/admin/blueprints/core.py) lists the
  # active tenants by name to a super admin, through the two filters its form sends
  # (templates/index.html). The Configuration filter keeps the tenants that name an ad server
  # ("configured") or that name none or an empty one ("not-configured"); the Activity filter
  # keeps the tenants that hold a media buy ("has-activity") or hold none ("no-activity"); "all"
  # keeps every tenant. The page says how many tenants it shows, of how many the filter keeps.
  #
  # The tenant management API's list (GET /api/v1/tenant-management/tenants) names every
  # tenant, active or not, newest first, with whether it has an adapter configuration.
  #
  # The deployment holds the Background's tenants and no other: this env stores no tenant of
  # its own. Each filter keeps two of the four active tenants, so a total that ignored the
  # filter would say 4. The inactive tenant names an ad server and holds a media buy, so an
  # index that listed inactive tenants would show it under "configured" and "has-activity".
  #
  # Transports (both run here, chosen at collection like BR-ADMIN-ACCOUNTS):
  #   - integration: Flask test_client (in-process, no Docker)
  #   - e2e: requests.Session against Docker stack (full deployment)

  Background:
    Given the deployment serves many tenants
    And the deployment holds the tenants:
      | name                 | ad server         | media buy | active | adapter config | created                   |
      | Alpha Wired Busy     | mock              | yes       | yes    | yes            | 2026-01-05T09:00:00+00:00 |
      | Bravo Wired Idle     | google_ad_manager | no        | yes    | no             | 2026-01-04T09:00:00+00:00 |
      | Charlie Unwired Busy | (none)            | yes       | yes    | no             | 2026-01-03T09:00:00+00:00 |
      | Delta Unwired Idle   | (empty)           | no        | yes    | no             | 2026-01-02T09:00:00+00:00 |
      | Echo Closed Busy     | mock              | yes       | no     | no             | 2026-01-01T09:00:00+00:00 |

  @T-ADMIN-LIST-001 @main-flow
  Scenario Outline: The all-tenants index shows the active tenants its filter keeps, and how many it keeps
    Given the operator is signed in as "listing-admin@bdd-super-admin.example", found a super admin just now
    When the operator opens the admin home page with "<filter>" set to "<value>"
    Then the page returns status 200
    And the index lists the tenants "<names>"
    And the index says it shows <shown> of <total> accounts

    Examples:
      | filter     | value          | names                                                                       | shown | total |
      | configured | all            | Alpha Wired Busy, Bravo Wired Idle, Charlie Unwired Busy, Delta Unwired Idle | 4     | 4     |
      | configured | configured     | Alpha Wired Busy, Bravo Wired Idle                                          | 2     | 2     |
      | configured | not-configured | Charlie Unwired Busy, Delta Unwired Idle                                    | 2     | 2     |
      | activity   | has-activity   | Alpha Wired Busy, Charlie Unwired Busy                                      | 2     | 2     |
      | activity   | no-activity    | Bravo Wired Idle, Delta Unwired Idle                                        | 2     | 2     |

  @T-ADMIN-LIST-002 @main-flow
  Scenario: The tenant management API lists every tenant, newest first, with whether it has an adapter configuration
    When the tenant management API is asked for its list of tenants
    Then the page returns status 200
    And the tenant list is "Alpha Wired Busy, Bravo Wired Idle, Charlie Unwired Busy, Delta Unwired Idle, Echo Closed Busy", each as stored, with whether it has an adapter configuration
