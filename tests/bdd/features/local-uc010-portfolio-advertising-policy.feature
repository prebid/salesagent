# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# Upstream gap: v3.1.1 defines media_buy.portfolio.advertising_policies, and this seller
# emits it from the tenant's advertising_policy column, but no BR-UC-010 storyboard step
# grades it. The obligation was graded only by mocked unit tests that patched the unit of
# work and asserted against a response they had half-built, so it did not reach the wire on
# any transport. Those tests are deleted; this is where the obligation lives.
#
# Reconcile upstream in adcp-req (a "seller publishes its advertising policy" scenario),
# then retire this file in favor of the regenerated one.
#
# The field is type "string" with no null arm, and portfolio requires only
# publisher_domains, so a seller that declares no policy OMITS the member rather than
# sending null — the omit-don't-null contract this repo grades everywhere else.
#
# @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/portfolio/properties/advertising_policies
# @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/portfolio/required
Feature: UC-010 get_adcp_capabilities — the seller publishes its advertising policy (local)

  @T-UC-010-local-advertising-policy-declared @main-flow @partition @boundary
  Scenario: a declared advertising policy reaches the buyer verbatim
    Given a tenant is resolvable from the request context
    And the tenant declares an advertising policy described as "No adult content allowed"
    When the Buyer Agent calls get_adcp_capabilities
    Then the response is compliant with the get_adcp_capabilities spec
    And media_buy.portfolio.advertising_policies should equal "No adult content allowed"

  @T-UC-010-local-advertising-policy-absent @main-flow @partition @boundary @invariant
  Scenario: a seller declaring no advertising policy omits the member
    Given a tenant is resolvable from the request context
    And the tenant declares no advertising policy
    When the Buyer Agent calls get_adcp_capabilities
    Then the response is compliant with the get_adcp_capabilities spec
    And media_buy.portfolio.advertising_policies should be omitted

  @T-UC-010-local-advertising-policy-empty-description @main-flow @partition @boundary
  Scenario: a policy carrying no description omits the member rather than sending an empty string
    Given a tenant is resolvable from the request context
    And the tenant declares an advertising policy with no description
    When the Buyer Agent calls get_adcp_capabilities
    Then the response is compliant with the get_adcp_capabilities spec
    And media_buy.portfolio.advertising_policies should be omitted
    # The column holds a document, and only its "description" member is publishable. A
    # policy row that carries other keys but no description has nothing to say on the
    # wire, and an empty string would be a published policy of "" rather than none.
