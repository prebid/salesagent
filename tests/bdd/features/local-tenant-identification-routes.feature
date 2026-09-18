# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# SUBJECT. A request names the seller it is addressing in one of TWO ways, and
# both resolve the same tenant. The rest of the suite now sends a `Host`
# (tests/harness/_base.py `credential`), because that is what a deployment does:
# the proxy passes the Host through untouched and the app resolves the tenant
# from it (config/nginx/nginx-multi-tenant.conf). These scenarios exist so making
# the host the default does not quietly drop the other route — each one is pinned
# here, once, by the route it is named for.
#
# WHY IT IS WORTH PINNING AT ALL. Before the host became the default, NO factory,
# step or env ever set a `virtual_host`, so the virtual-host branch of tenant
# detection had never executed in any test. It was reachable in production the
# whole time, and what it hid was a real defect: a tenant host carrying a port
# reached `publisher_properties[].publisher_domain`, which the pinned schema
# constrains to a pattern admitting no colon, and `get_products` answered
# INTERNAL_ERROR for that tenant's entire catalogue. An unexercised branch is
# not a small gap; it is an ungraded one.
#
# PINNED AUTHORITY (adcp==6.6.0 -> _schemas/3.1, AdCP 3.1.1 — docs/adcp-spec-version.md).
#   The spec does NOT specify how a buyer selects a seller: a request is
#   addressed to an agent's URL, and how that deployment maps a host to a tenant
#   is the seller's own concern. So these scenarios grade THIS SELLER's
#   documented resolution order (src/core/resolved_identity._detect_tenant:
#   Host -> x-adcp-tenant), not a protocol obligation, and they are deliberately
#   local rather than a BR-* storyboard.
#   What IS pinned is the consequence: `get_adcp_capabilities` describes the
#   tenant that was resolved, so resolving the wrong one — or none — is
#   buyer-visible.
#
# WHY get_adcp_capabilities IS THE PROBE. It is public (`PublicIdentity`), so a
# scenario can grade tenant resolution without a credential confusing the
# question, and its response is derived per tenant, so "which tenant answered"
# is observable rather than assumed.

@tenantid
Feature: A request identifies its seller by host or by header

  Background:
    Given a Seller Agent is operational and accepting requests

  @T-TENANTID-host
  Scenario: The tenant is identified by the Host it is served at
    Given the tenant is reachable at its own virtual host
    When the buyer requests capabilities naming the seller by Host
    Then the response describes that tenant

  @T-TENANTID-host-with-port
  Scenario: A Host carrying a port identifies the same tenant
    Given the tenant is reachable at its own virtual host
    When the buyer requests capabilities naming the seller by Host with a port
    Then the response describes that tenant

  @T-TENANTID-header
  Scenario: The tenant is identified by the x-adcp-tenant header
    Given the tenant is reachable at its own virtual host
    When the buyer requests capabilities naming the seller by tenant header
    Then the response describes that tenant

  @T-TENANTID-unserved-host
  Scenario: A Host no tenant claims is refused as a misconfiguration
    Given the tenant is reachable at its own virtual host
    When the buyer requests capabilities naming a seller nobody serves
    Then the response contains error code CONFIGURATION_ERROR
    And the error recovery should be "terminal"
    And the refusal names the host the request used
