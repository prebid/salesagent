# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration). Ungraded by the AdCP storyboards:
# this is deployment infrastructure, so the spec says nothing about it. The authority is
# the contract of the proxy that asks. Caddy's on_demand_tls `ask` sends ?domain=<name>
# (modules/caddytls/ondemand.go:131 at v2.11.6) and issues nothing on any non-2xx (:164), and
# docs/deployment/multi-tenant.md states which hosts the seller answers yes for.
#
# SUBJECT. GET /tls/ask decides whether anyone who points a domain at this deployment
# makes it request a certificate, so a wrong yes is a certificate for a name this seller
# does not serve and a wrong no is a tenant with no TLS.
#
# WHY THE GATE IS NOT A TOOL. It answers before any AdCP exchange, carries no envelope and
# is not a registry row, so it is not dispatched through `serve`. The env owns the GET
# (`ask_tls_gate`): on the in-process transports it comes off the composed `src.app` app,
# on e2e it is real HTTP against the live stack, and on both the app was composed with
# TLS_ASK_ENABLED=true, so a composition that stops mounting the route answers 404 here.
#
# HOW A MALFORMED HOST IS SHOWN TO COST NO QUERY. The database is made to refuse connections
# (tests/harness/database_faults.py; nothing in the seller is patched). A well-formed host
# then answers 500 because its lookup failed, so a malformed host answering 403 did not
# attempt one.
#
# NOT HERE: how a differently SPELLED SALES_AGENT_DOMAIN or ADMIN_DOMAIN is read. A live stack
# has one spelling per server for its whole run, so tests/integration/test_tls_ask_endpoint.py
# grades that matrix through injected settings.

@tlsask
Feature: The on-demand TLS gate allows a certificate only for a host this deployment serves

  Background:
    Given a Seller Agent is operational and accepting requests
    And the deployment answers on the domain "sales-agent.example.com"

  @T-TLSASK-deployment-domains
  Scenario Outline: The deployment's own domains get a certificate
    When the proxy asks about "<host>"
    Then the gate allows the certificate

    Examples:
      | host                          |
      | sales-agent.example.com       |
      | admin.sales-agent.example.com |

  @T-TLSASK-tenant-host
  Scenario Outline: An active tenant's declared host gets a certificate however the proxy spells it
    Given an active tenant declares a host
    When the proxy asks about that host <spelling>
    Then the gate allows the certificate

    Examples:
      | spelling            |
      | as declared         |
      | in upper case       |
      | with a trailing dot |

  @T-TLSASK-host-limits
  Scenario Outline: A declared host at the edge of what DNS allows gets a certificate
    Given an active tenant declares a host starting with the label "<label>"
    When the proxy asks about that host as declared
    Then the gate allows the certificate

    Examples:
      | label                                                           |
      | xn--bcher-kva                                                   |
      | aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa |

  # A certificate names only the host, so the port a tenant stores says nothing about it.
  @T-TLSASK-stored-port
  Scenario: A tenant that stores its host with a port gets a certificate for the host
    Given an active tenant declares a host with the port 8443
    When the proxy asks about that host without the port
    Then the gate allows the certificate

  @T-TLSASK-inactive-tenant
  Scenario: An inactive tenant's host is refused
    Given an inactive tenant declares a host
    When the proxy asks about that host as declared
    Then the gate refuses the certificate

  # Nothing is derived from a subdomain: a tenant is served at the host it declares and
  # nowhere else, so its subdomain under the deployment's domain is refused like any other name.
  @T-TLSASK-undeclared-name
  Scenario Outline: A name no tenant declares is refused
    Given an active tenant declares a host
    When the proxy asks about that host <relation>
    Then the gate refuses the certificate

    Examples:
      | relation                              |
      | as its subdomain under the deployment |
      | with a label in front of it           |
      | under another domain                  |
      | with a port                           |

  @T-TLSASK-unknown-name
  Scenario: A well-formed name nobody mentioned is refused
    When the proxy asks about "nobody-serves-this.invalid"
    Then the gate refuses the certificate

  # The Kelvin sign row is U+212A, which str.lower folds to an ASCII "k".
  @T-TLSASK-malformed
  Scenario Outline: A malformed host is refused
    When the proxy asks about "<host>"
    Then the gate refuses the certificate

    Examples:
      | host                                                                     |
      |                                                                          |
      | .                                                                        |
      | a..b                                                                     |
      | a b.example                                                              |
      | evil.example/path                                                        |
      | *.sales-agent.example.com                                                |
      | -lead.example                                                            |
      | trail-.example                                                           |
      | under_score.example                                                      |
      | K.example                                                                |
      | aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.example |

  @T-TLSASK-too-long
  Scenario: A host over the 253 character limit is refused
    When the proxy asks about a host of 254 characters
    Then the gate refuses the certificate

  @T-TLSASK-no-host
  Scenario: A request naming no host is refused
    When the proxy asks without naming a host
    Then the gate refuses the certificate

  # The gate identifies a tenant; it does not load one. A stored secret that no longer decrypts
  # breaks that tenant's landing page, and must not cost the tenant its certificate as well.
  @T-TLSASK-undecryptable-secret
  Scenario: A tenant whose stored Gemini key does not decrypt still gets a certificate
    Given an active tenant declares a host and stores a Gemini key that does not decrypt
    When the proxy asks about that host as declared
    Then the gate allows the certificate

  @T-TLSASK-database-down
  Scenario: A well-formed host is refused with a server error while the database is down
    Given the database refuses new connections
    When the proxy asks about "nobody-serves-this.invalid"
    Then the gate fails without allowing the certificate

  @T-TLSASK-malformed-costs-no-query
  Scenario Outline: A malformed host is refused without asking the database
    Given the database refuses new connections
    When the proxy asks about "<host>"
    Then the gate refuses the certificate

    Examples:
      | host        |
      |             |
      | *.x         |
      | a b.x       |
      | host.x:443  |
