# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
# Upstream gap: BR-UC-010 carries @T-UC-010-v31-experimental-features, but its
# Given ("the tenant implements experimental surfaces [...]") names surfaces this
# seller does not implement (brand.rights_lifecycle), so it grades a shape rather
# than this agent's declaration. The obligation below is the one this codebase
# actually creates by emitting experimental_features for the first time.
#
# Authority: AdCP 3.1.1 reference/experimental-status.mdx — "A seller
# implementing any experimental surface MUST list it in experimental_features.
# Sellers that do not list an experimental surface MUST NOT implement it — there
# is no 'silently experimental' mode." Omission is therefore a positive claim,
# which is why the negative scenarios below are obligations and not preferences.
#
# Ungraded upstream: no conformance storyboard step covers a seller's own
# experimental declaration, so these scenarios are the local grading.
# Reconcile upstream in adcp-req, then retire this file (#1197 review).
Feature: TMP capability declaration — the agent declares the surface it implements (local)

  @T-TMP-CAPS-declared @trusted_match @experimental
  Scenario: a tenant with a syncable provider declares the trusted match surface
    Given the tenant's only TMP provider has status "active"
    When the Buyer Agent asks for the seller's capabilities
    Then experimental_features includes "trusted_match.core"

  @T-TMP-CAPS-draining @trusted_match @experimental
  Scenario: a draining provider still counts as implemented
    Given the tenant's only TMP provider has status "draining"
    When the Buyer Agent asks for the seller's capabilities
    Then experimental_features includes "trusted_match.core"

  @T-TMP-CAPS-inactive @trusted_match @experimental
  Scenario: a tenant whose only provider is inactive does not declare the surface
    Given the tenant's only TMP provider has status "inactive"
    When the Buyer Agent asks for the seller's capabilities
    Then experimental_features does not include "trusted_match.core"

  @T-TMP-CAPS-none @trusted_match @experimental
  Scenario: a tenant with no provider does not declare the surface
    Given the tenant has no TMP provider registered
    When the Buyer Agent asks for the seller's capabilities
    Then experimental_features does not include "trusted_match.core"
