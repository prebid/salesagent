# The agent card publishes WHERE THIS SELLER IS REACHED, read from the tenant's own row.
#
# WHY A FEATURE AND NOT ONLY INTEGRATION TESTS. The card is the first thing a buyer
# fetches and the only place it learns the agent's URL, so a card naming a host nothing
# serves is a seller that cannot be reached at all — and the failure is silent, because
# every other card assertion passes when the published origin is an echo of the
# requesting Host (they send the stored host AS the Host, so an echo and a read produce
# the same string). The scenario that separates them names a DIFFERENT host than the one
# the tenant stores.
#
# PINNED AUTHORITY (adcp==6.6.0 -> _schemas/3.1, AdCP 3.1.1 — docs/adcp-spec-version.md).
#   The spec does not say how a deployment maps a host to a tenant; a request is
#   addressed to an agent's URL and the mapping is the seller's own concern. So these
#   scenarios are LOCAL rather than a BR-* storyboard. What the A2A specification does
#   fix is the canonical discovery path, /.well-known/agent-card.json (A2A §8.2, §14.3);
#   /.well-known/agent.json is the path AdCP's own guide names (a2a-guide.mdx:782).
#
# WHY THE CARD IS NOT A TOOL. It answers before any AdCP exchange, carries no envelope
# and is not a registry row, so it cannot be dispatched through `serve`. The env owns
# the fetch (`fetch_agent_card`), which is where the per-transport HOW belongs.

@agentcard
Feature: The agent card publishes the host its tenant declares

  Background:
    Given a Seller Agent is operational and accepting requests
    And the tenant is reachable at its own virtual host

  @T-AGENTCARD-stored-origin
  Scenario: The card publishes the tenant's stored origin
    When the buyer fetches the agent card naming the seller by Host
    Then the card publishes the origin the tenant stores
    And the card's interface is one an A2A client selects

  @T-AGENTCARD-not-an-echo
  Scenario: The card publishes the stored origin, not the Host it was asked on
    When the buyer fetches the agent card naming the seller by tenant header from another host
    Then the card publishes the origin the tenant stores
    And the card does not publish the host the request named

  @T-AGENTCARD-unserved-host
  Scenario: A card fetch naming no tenant this deployment serves is refused
    When the buyer fetches the agent card naming a seller nobody serves
    Then no agent card is published
    And the refusal names a seller-side misconfiguration
