# Hand-authored feature — not compiled from adcp-req
# Admin UI: an expected refusal reaches the operator as its fixed text (#2571)

Feature: BR-ADMIN-OPERATOR-REFUSALS Expected refusals in the admin app
  As a super admin
  I want a value the seller refuses to be answered with what is wrong with it
  So that I can correct it, rather than see a server error

  # A refusal is an OperatorRefusal subclass (src/core/operator_errors.py). Its class
  # names its operator-facing text, MESSAGE; the exception's own text carries ids and the
  # refused value for the log. Routes let it escape, and the admin app's one error handler
  # (src/admin/utils/operator_errors.py) answers it with its class's STATUS and MESSAGE: JSON
  # for fetch and API callers, carrying any key the refusal names for them (its details, its
  # issues); for a browser, a flash on the page it came from, or the error page when it names
  # none. The status is the refusal's meaning, decided by the class it subclasses, never by
  # the route: a value the operator can change is 400, a record that does not exist 404, a
  # conflict with the current state 409, another system that failed 502, and a deployment
  # that lacks the configuration 503.
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
    And the answer does not echo the submitted "virtual_host"

  @T-ADMIN-REFUSAL-002 @create-tenant @virtual-host
  Scenario: With no page to go back to, the refusal is shown on the error page
    When the operator submits the create tenant form with no page to go back to, with:
      | field        | value                               |
      | name         | Refused Host Seller                 |
      | subdomain    | refused_host_seller                 |
      | virtual_host | https://refused-host.example/agent  |
    Then the page returns status 400
    And the page is the error page
    And the page shows the "InvalidVirtualHost" refusal
    And the answer does not echo the submitted "virtual_host"

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
    And the answer does not echo the submitted "virtual_host"

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

  @T-ADMIN-REFUSAL-008 @json @inventory-profile-form
  Scenario: A fetch() caller asking for a missing record gets the status as JSON
    # abort(404) and the other HTTP errors pass through the error handler, and a caller
    # that reads JSON gets them as JSON, so the page's script can show the error.
    When the inventory page deletes a profile that does not exist
    Then the page returns status 404
    And the JSON response has "success" as false
    And the JSON response has "error" as "Not Found"

  @T-ADMIN-REFUSAL-009 @json @inventory-sync
  Scenario: A sync started while one is running is refused with the running sync's id
    # The settings page's script follows the sync it is given, so the refusal names it.
    Given the tenant is onboarded to Google Ad Manager
    And the tenant's inventory sync "sync_bdd_already_running" is running
    When the settings page starts an inventory sync
    Then the page returns status 409
    And the JSON response has "success" as false
    And the JSON error is the "SyncAlreadyRunning" refusal
    And the JSON response has "sync_id" as "sync_bdd_already_running"

  # The answer's format is decided from the caller, never the route
  # (src/admin/utils/helpers.py caller_reads_json): only a browser asking for a page gets
  # HTML, so a script that sends curl's "Accept: */*", or no Accept at all, gets JSON.
  @T-ADMIN-REFUSAL-016 @json @inventory-sync
  Scenario Outline: A script that does not ask for a page gets a refusal as JSON
    Given another tenant of this seller, onboarded to Google Ad Manager
    And that tenant's inventory sync is running
    When a script starts that tenant's inventory sync with no body, sending Accept "<accept>"
    Then the page returns status 409
    And the JSON response has "success" as false
    And the JSON error is the "SyncAlreadyRunning" refusal
    And the JSON response names that tenant's running sync

    Examples:
      | accept |
      | */*    |
      | (none) |

  # A route under /api whose handler reads a JSON body: a script that posts none is refused by
  # Flask (415), and that refusal is JSON too.
  @T-ADMIN-REFUSAL-024 @json @adapter-config
  Scenario Outline: A script that does not ask for a page gets an HTTP error as JSON
    When a script saves the adapter config with no body, sending Accept "<accept>"
    Then the page returns status 415
    And the JSON response has "success" as false
    And the JSON response has "error" as "Unsupported Media Type"

    Examples:
      | accept |
      | */*    |
      | (none) |

  # A browser asks for a page in one of two ways, and either is enough for HTML: a current
  # browser sends Sec-Fetch-Mode: navigate, and an older one an Accept that prefers text/html.
  @T-ADMIN-REFUSAL-025 @browser @create-tenant @virtual-host
  Scenario Outline: A browser's form post gets the refusal as a flash on its page
    When <browser> submits the create tenant form from the create tenant page with:
      | field        | value                               |
      | name         | Refused Host Seller                 |
      | subdomain    | refused_host_seller                 |
      | virtual_host | https://refused-host.example/agent  |
    Then the page returns status 200
    And the page is the create tenant form
    And the page shows the "InvalidVirtualHost" refusal

    Examples:
      | browser                                   |
      | a browser that sends only Sec-Fetch-Mode  |
      | a browser that sends only Accept          |

  @T-ADMIN-REFUSAL-010 @json @service-account
  Scenario: A service account for a tenant with no ad server configuration is refused
    When the settings page creates a GAM service account
    Then the page returns status 409
    And the JSON response has "success" as false
    And the JSON error is the "TenantAdapterConfigMissing" refusal

  @T-ADMIN-REFUSAL-011 @json @service-account
  Scenario: A second service account for a tenant is refused
    Given the tenant is onboarded to Google Ad Manager with the service account "bdd-existing@sa.example"
    When the settings page creates a GAM service account
    Then the page returns status 409
    And the JSON response has "success" as false
    And the JSON error is the "ServiceAccountAlreadyExists" refusal

  # Neither test deployment sets GCP_PROJECT_ID. The tenant's own state permits a service
  # account, so what refuses it is the deployment, before any GCP call.
  @T-ADMIN-REFUSAL-017 @json @service-account
  Scenario: A service account on a deployment with no GCP project is refused
    Given another tenant of this seller, onboarded to Google Ad Manager
    When the settings page creates a GAM service account for that tenant
    Then the page returns status 503
    And the JSON response has "success" as false
    And the JSON error is the "GCPProjectNotConfigured" refusal

  # Neither test deployment sets GAM_OAUTH_CLIENT_ID, so the refusal comes before any call to Google.
  @T-ADMIN-REFUSAL-018 @json @gam-oauth
  Scenario: Detecting a GAM network on a deployment with no GAM OAuth client is refused
    Given another tenant of this seller
    When the settings page detects that tenant's GAM network with the refresh token "bdd-refresh-token"
    Then the page returns status 503
    And the JSON response has "success" as false
    And the JSON error is the "GAMOAuthNotConfigured" refusal

  @T-ADMIN-REFUSAL-019 @json @adapter
  Scenario: An adapter update that names no adapter, for a tenant that has chosen none, is refused
    Given another tenant of this seller, with no ad server chosen
    When the settings page saves that tenant's adapter without naming one
    Then the page returns status 409
    And the JSON response has "success" as false
    And the JSON error is the "NoAdapterConfigured" refusal

  # The connection test runs the tenant's real Gemini model against the scripted model origin
  # (tests/e2e/scripted_llm_origin_service.py), which answers with the row's status. A status
  # the operator can act on is answered with the refusal chosen on it (settings.ai_refusal_for).
  @T-ADMIN-REFUSAL-020 @json @ai-test
  Scenario Outline: An AI provider that refuses the connection test is answered by its status
    Given the AI provider answers the connection test with HTTP <status>
    When the settings page tests the AI connection with that key
    Then the page returns status <answer>
    And the JSON response has "success" as false
    And the JSON error is the "<refusal>" refusal

    Examples:
      | status | answer | refusal                  |
      | 401    | 400    | AIProviderKeyRefused     |
      | 403    | 400    | AIProviderKeyRefused     |
      | 404    | 400    | AIModelNotFound          |
      | 429    | 502    | AIProviderRateLimited    |
      | 400    | 400    | AIProviderRefusedRequest |

  @T-ADMIN-REFUSAL-022 @json @sync-api
  Scenario: A sync type the sync API does not run is refused for that field
    Given another tenant of this seller, onboarded to Google Ad Manager
    When the sync API is asked for a "bogus" sync of that tenant
    Then the page returns status 400
    And the JSON response has "success" as false
    And the JSON error is the "InvalidFormInput" refusal
    And the JSON issues are:
      | field     | text                                                      |
      | sync_type | sync_type must be one of full, inventory, targeting, selective |

  @T-ADMIN-REFUSAL-023 @json @sync-api
  Scenario: A selective sync that names no inventory types is refused for that field
    Given another tenant of this seller, onboarded to Google Ad Manager
    When the sync API is asked for a "selective" sync of that tenant
    Then the page returns status 400
    And the JSON response has "success" as false
    And the JSON error is the "InvalidFormInput" refusal
    And the JSON issues are:
      | field      | text                                        |
      | sync_types | sync_types is required for a selective sync |

  @T-ADMIN-REFUSAL-012 @json @inventory-profile-form
  Scenario: A profile a product is built from is not deleted from the list page
    Given the seller holds a verified property of publisher "pub-psatest.com"
    And a stored profile "in_use_by_fetch" declaring an image of 300x250
    And a product "built_from_in_use_by_fetch" built from the profile "in_use_by_fetch"
    When the inventory page deletes the profile "in_use_by_fetch"
    Then the page returns status 409
    And the JSON response has "success" as false
    And the JSON error is the "InventoryProfileInUse" refusal
    And the profile "in_use_by_fetch" stores an image of 300x250

  @T-ADMIN-REFUSAL-013 @browser @inventory-profile-form
  Scenario: A profile a product is built from is not deleted by a form post
    Given the seller holds a verified property of publisher "pub-psatest.com"
    And a stored profile "in_use_by_form" declaring an image of 300x250
    And a product "built_from_in_use_by_form" built from the profile "in_use_by_form"
    When a browser form on the profiles list deletes the profile "in_use_by_form"
    Then the page returns status 200
    And the page shows the "InventoryProfileInUse" refusal
    And the profile "in_use_by_form" stores an image of 300x250

  @T-ADMIN-REFUSAL-014 @json @adapter-config
  Scenario: An adapter config the adapter's schema refuses is answered with where, not with the value
    # pydantic's own text and error dicts echo the submitted value, which can be a credential.
    When the adapter settings save a "mock" config with "manual_approval_required" set to "sk-live-0123456789abcdef"
    Then the page returns status 400
    And the JSON response has "success" as false
    And the JSON error is the "InvalidFormInput" refusal
    And the JSON issues are:
      | field  | text                                                          |
      | config | config.manual_approval_required: This field is the wrong type. |
    And the answer does not echo the submitted "manual_approval_required"

  @T-ADMIN-REFUSAL-015 @browser @referrer
  Scenario Outline: A refusal from a page the browser reads as another site's is shown on the error page
    # Only a same-site path is gone back to. A browser reads //host and /\host as another
    # site, so a Referer whose path starts with either names no page here.
    When the operator submits the create tenant form from the page "<page>" with:
      | field        | value                               |
      | name         | Refused Host Seller                 |
      | subdomain    | refused_host_seller                 |
      | virtual_host | https://refused-host.example/agent  |
    Then the page returns status 400
    And the page is the error page
    And the page shows the "InvalidVirtualHost" refusal

    Examples:
      | page                                       |
      | https://elsewhere.example/create_tenant    |
      | //elsewhere.example/create_tenant          |
      | /\\elsewhere.example/create_tenant         |

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

  @T-ADMIN-REFUSAL-021 @product-form @pricing
  Scenario: A pricing option the product edit form refuses is named on the edit form
    # The edit form is answered by the error handler: back to the page it came from.
    Given another tenant of this seller
    And that tenant has a product
    When the operator submits that product's edit form from its page with:
      | field           | value     |
      | pricing_model_0 | cpm_fixed |
      | rate_0          |           |
    Then the page returns status 200
    And the page is the edit product form
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

  # A refusal whose class-level text cannot say what went wrong carries details: lines this
  # seller wrote, which a fetch() caller gets under "details" and shows beside MESSAGE.

  @T-ADMIN-REFUSAL-026 @json @connection-test
  Scenario: A connection test that fails is refused with what the test saw
    # Nothing answers at a .invalid host (RFC 6761), so the test message is not delivered.
    Given another tenant of this seller, with the Slack webhook "https://hooks.bdd-refusals.invalid/services/T0/B0/x"
    When the settings page sends that tenant's Slack test message
    Then the page returns status 502
    And the JSON response has "success" as false
    And the JSON error is the "ConnectionTestFailed" refusal
    And the JSON details are:
      | text                          |
      | Slack webhook delivery failed |

  @T-ADMIN-REFUSAL-027 @json @connection-test
  Scenario: A Slack test for a tenant with no webhook is refused
    Given another tenant of this seller
    When the settings page sends that tenant's Slack test message
    Then the page returns status 409
    And the JSON response has "success" as false
    And the JSON error is the "SlackWebhookNotConfigured" refusal

  # Neither test deployment sets APPROXIMATED_API_KEY.
  @T-ADMIN-REFUSAL-028 @json @approximated
  Scenario: A DNS widget token on a deployment with no Approximated key is refused
    Given another tenant of this seller
    When the settings page asks for that tenant's DNS widget token
    Then the page returns status 503
    And the JSON response has "success" as false
    And the JSON error is the "ApproximatedNotConfigured" refusal

  # Setup mode is disabled only from an SSO sign-in, so a tenant cannot lock itself out.
  @T-ADMIN-REFUSAL-029 @json @setup-mode
  Scenario: Disabling setup mode without an SSO sign-in is refused
    Given another tenant of this seller
    When the users page disables that tenant's setup mode
    Then the page returns status 409
    And the JSON response has "success" as false
    And the JSON error is the "SSOSignInRequired" refusal

  @T-ADMIN-REFUSAL-030 @json @setup-mode
  Scenario: Disabling setup mode for a tenant whose SSO is not enabled is refused
    Given another tenant of this seller
    And the operator signed in through SSO
    When the users page disables that tenant's setup mode
    Then the page returns status 409
    And the JSON response has "success" as false
    And the JSON error is the "SSONotEnabled" refusal

  # A stored AI configuration that names a provider and no key turns AI off for the tenant
  # (TenantAIConfig.from_tenant), whatever key the deployment itself holds.
  @T-ADMIN-REFUSAL-031 @json @ai-review
  Scenario: An AI review for a tenant whose AI cannot run is refused with why
    Given another tenant of this seller, whose AI configuration names "anthropic" with no key
    When the creative review page asks for an AI review of that tenant's creative "bdd_unreviewed_creative"
    Then the page returns status 409
    And the JSON response has "success" as false
    And the JSON error is the "AIReviewUnavailable" refusal
    And the JSON details are:
      | text              |
      | AI not configured |

  @T-ADMIN-REFUSAL-032 @json @inventory-sync
  Scenario: Resetting a stuck sync when no sync is running is refused
    Given another tenant of this seller
    When the settings page resets that tenant's stuck inventory sync
    Then the page returns status 409
    And the JSON response has "success" as false
    And the JSON error is the "NoRunningSyncToReset" refusal

  @T-ADMIN-REFUSAL-033 @json @accounts
  Scenario: A status change for an account that does not exist is refused as not found
    When the admin sends a status change request for an account that does not exist
    Then the page returns status 404
    And the JSON response has "success" as false
    And the JSON error is the "AccountNotFound" refusal

  @T-ADMIN-REFUSAL-036 @json @connection-test
  Scenario: A Slack test for a tenant that does not exist is refused as not found
    When the settings page sends the Slack test message of a tenant that does not exist
    Then the page returns status 404
    And the JSON response has "success" as false
    And the JSON error is the "TenantNotFound" refusal

  # Each kind of record has one RecordNotFound subclass, named by the repository for that kind
  # and raised by its require() when the lookup finds no row. Its MESSAGE is the answer.
  @T-ADMIN-REFUSAL-040 @json @record-not-found
  Scenario Outline: A request for a <record> that does not exist is refused as not found
    When a page's script asks for a <record> that does not exist
    Then the page returns status 404
    And the JSON response has "success" as false
    And the JSON error is the "<refusal>" refusal

    Examples:
      | record        | refusal              |
      | principal     | PrincipalNotFound    |
      | product       | ProductNotFound      |
      | creative      | CreativeNotFound     |
      | signals agent | SignalsAgentNotFound |
      | workflow step | WorkflowStepNotFound |
      | GAM order     | OrderNotFound        |
      | sync job      | SyncJobNotFound      |

  @T-ADMIN-REFUSAL-037 @json @authorized-domains
  Scenario: An authorized domain the tenant already authorizes is refused as a conflict
    Given another tenant of this seller, authorizing the domain "authorized.bdd-refusals.example"
    When the users page adds the authorized domain "authorized.bdd-refusals.example" to that tenant
    Then the page returns status 409
    And the JSON response has "success" as false
    And the JSON error is the "DomainAlreadyAuthorized" refusal

  # The deployment's SUPER_ADMIN_DOMAIN is the operator's own, and no tenant may authorize
  # it. Both test deployments name operator.bdd-refusals.example: docker-compose.e2e.yml on
  # the live stack, and the Given on the app in this process. The refusal compares the
  # domain as it would be stored (lower-cased, no trailing dot), so every spelling of it is
  # refused, on every page that authorizes a domain.
  @T-ADMIN-REFUSAL-038 @json @authorized-domains @super-admin-domain
  Scenario Outline: The users page refuses the deployment's super-admin domain, however it is spelled - <spelling>
    Given the deployment's super-admin domain is "operator.bdd-refusals.example"
    And another tenant of this seller, authorizing the domain "authorized.bdd-refusals.example"
    When the users page adds the authorized domain "<spelling>" to that tenant
    Then the page returns status 400
    And the JSON response has "success" as false
    And the JSON error is the "SuperAdminDomainRefused" refusal
    And that tenant authorizes only the domain "authorized.bdd-refusals.example"

    Examples:
      | spelling                       |
      | operator.bdd-refusals.example  |
      | Operator.BDD-Refusals.Example  |
      | operator.bdd-refusals.example. |

  @T-ADMIN-REFUSAL-039 @browser @authorized-domains @super-admin-domain
  Scenario Outline: The settings page refuses the deployment's super-admin domain, however it is spelled - <spelling>
    Given the deployment's super-admin domain is "operator.bdd-refusals.example"
    And another tenant of this seller, authorizing the domain "authorized.bdd-refusals.example"
    When the settings page's form adds the authorized domain "<spelling>" to that tenant, from no page
    Then the page returns status 400
    And the page is the error page
    And the page shows the "SuperAdminDomainRefused" refusal
    And that tenant authorizes only the domain "authorized.bdd-refusals.example"

    Examples:
      | spelling                       |
      | operator.bdd-refusals.example  |
      | Operator.BDD-Refusals.Example  |
      | operator.bdd-refusals.example. |

  @T-ADMIN-REFUSAL-034 @json @authorized-domains
  Scenario: An authorized domain that names no domain is refused for that field
    When the users page adds an authorized domain without naming one
    Then the page returns status 400
    And the JSON response has "success" as false
    And the JSON error is the "InvalidFormInput" refusal
    And the JSON issues are:
      | field  | text               |
      | domain | domain is required |

  @T-ADMIN-REFUSAL-035 @json @gam-config
  Scenario: A GAM configuration with a network code that is not a number is refused for that field
    Given another tenant of this seller
    When the settings page saves that tenant's GAM configuration with:
      | field         | value             |
      | auth_method   | oauth             |
      | network_code  | not-a-number      |
      | refresh_token | bdd-refresh-token |
    Then the page returns status 400
    And the JSON response has "success" as false
    And the JSON error is the "InvalidFormInput" refusal
    And the JSON issues are:
      | field        | text                         |
      | network_code | Network code must be numeric |

  # Approving a media buy books it at the ad server. When the approval stops on a typed error,
  # the operator gets a refusal whose details are that error's lines: its code and message,
  # the request field it names, and one line per problem. The class is decided by the error.
  # A refusal before booking leaves the buy awaiting approval, and the buy's own state is what
  # conflicts with booking it (409). An ad server that fails the booking is another system
  # that failed (502).

  @T-ADMIN-REFUSAL-042 @json @media-buy-approval
  Scenario: A media buy whose creative its product refuses is refused at approval with the creative's error lines
    Given another tenant of this seller, holding a media buy awaiting approval whose product accepts images of 728x90
    And that media buy's package is assigned the approved 300x250 image creative "cr_approval_misfit"
    When the workflow page approves that media buy's approval step
    Then the page returns status 409
    And the JSON response has "success" as false
    And the JSON error is the "MediaBuyApprovalRefused" refusal
    And the JSON details are the "CREATIVE_SIZE_MISMATCH" error's line, then:
      | text                                               |
      | field packages[0].creative_ids[0]                  |
      | creative cr_approval_misfit CREATIVE_SIZE_MISMATCH |
    And that media buy still awaits approval

  @T-ADMIN-REFUSAL-043 @json @media-buy-approval
  Scenario: A media buy the ad server fails to book is refused at approval as the ad server's failure
    Given another tenant of this seller, holding a media buy awaiting approval
    And that tenant's ad server fails every media buy it is asked to create
    When the workflow page approves that media buy's approval step
    Then the page returns status 502
    And the JSON response has "success" as false
    And the JSON error is the "MediaBuyBookingFailed" refusal
    And the JSON details are only the "SERVICE_UNAVAILABLE" error's line
