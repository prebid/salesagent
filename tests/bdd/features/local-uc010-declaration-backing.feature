# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# Upstream gap: the BR-UC-010 storyboard grades what a CONFORMANT seller emits, but
# has no scenario for an operator declaring a capability this deployment does not
# back. Under the STRICT policy (owner, 2026-07-27) that rejection is the whole point
# of the declaration store — a tenant must not be able to make the wire promise
# behavior production lacks — so the backing rules would otherwise ship ungraded.
#
# The generated `T-UC-010-v31-specialisms` scenario cannot grade them: it declares
# `creative-generative` AND requires the `creative` protocol, both unbacked, so it
# stays xfailed against #1724 rather than being weakened to pass.
#
# Reconcile upstream in adcp-req (a "seller rejects an unbacked capability
# declaration" scenario), then retire this file in favor of the regenerated one.
#
# @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/specialisms
# @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/specialisms/signal-owned/index.yaml pointer=/required_tools
# @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/protocols/signals/index.yaml pointer=/required_tools
Feature: UC-010 get_adcp_capabilities — capability declarations must be implementation-backed (local)

  @T-UC-010-local-backed-specialism @v31 @invariant
  Scenario: a backed specialism and its parent protocol are echoed alongside the defaults
    Given a tenant is resolvable from the request context
    And the tenant declares specialisms ["signal-owned"] with supported_protocols ["signals"]
    When the Buyer Agent calls get_adcp_capabilities
    Then specialisms should equal ["sales-non-guaranteed", "signal-owned"]
    And supported_protocols should equal ["media_buy", "signals"]
    # UNION, never replacement: the declaration adds to the baseline rather than
    # replacing it. Replacing would drop media_buy and orphan the unconditionally
    # emitted `sales-non-guaranteed`, whose parent protocol is media-buy — the
    # self-inconsistent wire /properties/specialisms says the runner rejects.
    # signal-owned is backed: its bundle requires only get_signals, which this
    # deployment implements, and it requires zero storyboard scenarios.

  @T-UC-010-local-unbacked-specialism @v31 @invariant @error-path
  Scenario: declaring a specialism this deployment does not implement is rejected at config time
    Given a tenant is resolvable from the request context
    And the tenant declares specialisms ["creative-generative"] with supported_protocols ["media_buy"]
    When the Buyer Agent calls get_adcp_capabilities
    Then the capability declaration should be rejected as terminal misconfiguration
    And the rejection should name "creative-generative"
    # Nothing implements generative creative, and the AAO compliance runner grades
    # the claim, so echoing the declaration would be a false conformance claim.
    # Tracked by #1724.

  @T-UC-010-local-orphaned-specialism @v31 @invariant @error-path @boundary
  Scenario: a backed specialism without its parent protocol is rejected
    Given a tenant is resolvable from the request context
    And the tenant declares specialisms ["signal-owned"] without declaring its parent protocol
    When the Buyer Agent calls get_adcp_capabilities
    Then the capability declaration should be rejected as terminal misconfiguration
    And the rejection should name "signal-owned"
    # Roll-up coherence: /properties/specialisms — "the runner rejects a specialism
    # claim whose parent protocol is missing". Boundary case for the backing rule:
    # the specialism IS backed, so only the roll-up check can catch this.

  @T-UC-010-local-unbacked-protocol @v31 @invariant @error-path
  Scenario: declaring a protocol this deployment does not serve is rejected at config time
    Given a tenant is resolvable from the request context
    And the tenant declares supported_protocols ["creative"]
    When the Buyer Agent calls get_adcp_capabilities
    Then the capability declaration should be rejected as terminal misconfiguration
    And the rejection should name "creative"
    # A protocol claim commits the seller to that domain's required tool surface
    # (protocols/<p>/index.yaml#required_tools). Generative creative is unimplemented
    # here, so advertising `creative` would route buyer traffic to a domain that
    # cannot answer. Tracked by #1724.
