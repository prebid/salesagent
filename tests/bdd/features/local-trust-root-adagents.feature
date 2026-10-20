# adagents.json is served at the tenant's own host only when that host owns a property.
#
# A tenant is a sales agent. Its own host is not a publisher, unless the tenant also
# holds an authorized property on it. The adagents.json served at that host speaks for
# the properties on that host and no others: a property the tenant sells on another
# publisher's domain is authorized by THAT publisher's adagents.json, which a buyer
# fetches from that domain.
#
# PINNED AUTHORITY (adcp==6.6.0, AdCP 3.1.1 — docs/adcp-spec-version.md).
#   dist/schemas/3.1.1/adagents.json oneOf[1].allOf[0]: "a file with neither sales
#   authorization nor non-empty catalog content is rejected". So a host with no property
#   has no valid document to serve, and answers 404.
#   docs/brand-protocol/seller-setup.mdx "Who publishes what": a sales representative
#   publishes adagents.json "Usually no, unless it also owns properties".
#   docs/governance/property/authorized-properties.mdx "Missing adagents.json: Treat as
#   unauthorized (fail closed)", which is what the 404 tells a buyer.
#   publisher_domain's pattern admits no colon, so the properties on a host served on a
#   port are found by its hostname.
#   Ungraded by storyboards: no 3.1.1 compliance yaml fetches the seller's adagents.json.
#
# WHY LOCAL. Which host serves which document is this deployment's own routing; the spec
# fixes only what a valid document contains. adagents.json is not a tool, so it cannot go
# through `serve`; the env owns the fetch (`fetch_agent_card`, which takes any root path).

@adagents
Feature: adagents.json is published only by a host that owns a property

  Background:
    Given a Seller Agent is operational and accepting requests
    And the tenant is reachable at its own virtual host

  @T-ADAGENTS-no-property-at-host
  Scenario: A host that owns no property publishes no adagents.json
    Given the tenant holds an authorized property on "other-publisher.example" with verification status "verified"
    When the buyer fetches adagents.json naming the seller by Host
    Then no adagents.json is published

  @T-ADAGENTS-own-host-property
  Scenario: A host served on a port publishes a valid adagents.json for its own properties only
    Given the tenant is served on port 8443 of its host
    And the tenant holds an authorized property on its own host with verification status "pending"
    And the tenant holds an authorized property on "other-publisher.example" with verification status "verified"
    When the buyer fetches adagents.json naming the seller by Host
    Then the adagents.json validates against the pinned schema
    And the adagents.json claims only the property on the tenant's own host
    # The own-host property is "pending" on purpose. Verifying a property fetches the
    # adagents.json at its domain, so for a property on this host the verification reads
    # this very document; requiring "verified" first would mean it never becomes verified
    # (AuthorizedPropertyRepository.list_for_publisher_domain).
