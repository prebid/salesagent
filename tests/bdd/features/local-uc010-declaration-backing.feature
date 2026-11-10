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
# The generated `T-UC-010-v31-specialisms` scenario grades only the accepted path: it
# declares the two specialisms this seller backs, so it cannot reach the rejections below.
#
# Reconcile upstream in adcp-req (a "seller rejects an unbacked capability
# declaration" scenario), then retire this file in favor of the regenerated one.
#
# @source repo=adcp ref=v3.2.7 path=static/schemas/source/protocol/get-adcp-capabilities-response.json pointer=/properties/specialisms
# @source repo=adcp ref=v3.2.7 path=dist/compliance/3.2.7/specialisms/signal-owned/index.yaml pointer=/required_tools
# @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/signals/index.yaml pointer=/required_tools
Feature: UC-010 get_adcp_capabilities — capability declarations must be implementation-backed (local)

  @T-UC-010-local-backed-specialism @v31 @invariant
  Scenario: a backed specialism and its parent protocol are echoed alongside the defaults
    Given a tenant is resolvable from the request context
    And the tenant declares specialisms ["signal-owned"] with supported_protocols ["signals"]
    When the Buyer Agent calls get_adcp_capabilities
    Then the response is compliant with the get_adcp_capabilities spec
    And specialisms should equal ["sales-non-guaranteed", "signal-owned"]
    And supported_protocols should equal ["media_buy", "signals"]
    # UNION, never replacement: the declaration adds to the baseline rather than
    # replacing it. Replacing would drop media_buy and orphan the unconditionally
    # emitted `sales-non-guaranteed`, whose parent protocol is media-buy — the
    # self-inconsistent wire /properties/specialisms says the runner rejects.
    # signal-owned is backed: its bundle requires only get_signals, which this
    # deployment implements, and it requires zero storyboard scenarios.

  @T-UC-010-local-unbacked-specialism @v31 @invariant @error-path
  Scenario: declaring a specialism this deployment does not implement is rejected when capabilities are requested
    Given a tenant is resolvable from the request context
    And the tenant declares specialisms ["creative-generative"] with supported_protocols ["media_buy"]
    When the Buyer Agent calls get_adcp_capabilities
    Then the error is compliant with the AdCP error spec
    And the capability declaration should be rejected as terminal misconfiguration
    And the rejection should name capability "specialisms" and rejected value "creative-generative"
    # Nothing implements generative creative, and the AAO compliance runner grades
    # the claim, so echoing the declaration would be a false conformance claim.
    # Tracked by #1724.
    # The operator must be told WHICH claim was refused: a bare CONFIGURATION_ERROR
    # makes them bisect their own config.

  @T-UC-010-local-orphaned-specialism @v31 @invariant @error-path @boundary
  Scenario: a backed specialism without its parent protocol is rejected
    Given a tenant is resolvable from the request context
    And the tenant declares specialisms ["signal-owned"] without declaring its parent protocol
    When the Buyer Agent calls get_adcp_capabilities
    Then the error is compliant with the AdCP error spec
    And the capability declaration should be rejected as terminal misconfiguration
    And the rejection should name capability "specialisms" and rejected value "signal-owned (needs signals)"
    # Roll-up coherence: /properties/specialisms — "the runner rejects a specialism
    # claim whose parent protocol is missing". Boundary case for the backing rule:
    # the specialism IS backed, so only the roll-up check can catch this.

  @T-UC-010-local-unbacked-protocol @v31 @invariant @error-path
  Scenario: declaring a protocol this deployment does not serve is rejected when capabilities are requested
    Given a tenant is resolvable from the request context
    And the tenant declares supported_protocols ["creative"]
    When the Buyer Agent calls get_adcp_capabilities
    Then the error is compliant with the AdCP error spec
    And the capability declaration should be rejected as terminal misconfiguration
    And the rejection should name capability "supported_protocols" and rejected value "creative"
    # A protocol claim commits the seller to that domain's required tool surface
    # (protocols/<p>/index.yaml#required_tools). Generative creative is unimplemented
    # here, so advertising `creative` would route buyer traffic to a domain that
    # cannot answer. Tracked by #1724.

  @T-UC-010-local-inline-creative-management @v31 @invariant
  Scenario: the seller declares inline_creative_management, which its create and update paths honor
    Given a tenant is resolvable from the request context
    When the Buyer Agent calls get_adcp_capabilities
    Then the response is compliant with the get_adcp_capabilities spec
    And media_buy.features.inline_creative_management should equal true
    # sales-agent-creative-capabilities.mdx § Creative management modes:
    # "media_buy.inline_creative_management: true | Package-scoped creative bodies are
    # accepted on create/update media-buy operations". media-buy-features.json: "If a
    # seller declares a feature as true, they MUST honor requests using that feature."
    # This seller accepts packages[].creatives on create_media_buy and update_media_buy,
    # so the flag's VALUE is the obligation, not only its boolean shape (which
    # @T-UC-010-main grades). The acceptance half is graded on all four transports by
    # @T-UC-002-alt-creatives (the create answers packages[].creative_assignments for the
    # inline creatives, and get_media_buys reads them back as the package's
    # creative_approvals) and @T-UC-002-ext-canonical-product-slots-inline (an inline
    # creative is uploaded to the library and assigned to the package the create made); the
    # update half by
    # @T-UC-003-local-inline-creatives-replace and @T-UC-003-local-inline-creatives-keep-library.
    # At the pin the flag is `deprecated: true` with `x-removed-in: 4.0.0` ("Deprecated 3.x
    # compatibility capability for creatives provided inline in create_media_buy and
    # update_media_buy package payloads"). Deprecated is not removed: a 3.x seller that
    # accepts inline creatives still declares it, and the MUST-honor rule still binds.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/sales-agent-creative-capabilities.mdx section="Creative management modes"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/media-buy-features.json pointer=/properties/inline_creative_management
