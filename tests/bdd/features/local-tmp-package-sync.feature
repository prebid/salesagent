# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
# Upstream gap: the adcp-req storyboards have no scenario for seller-side TMP
# Package Sync — AdCP 3.1.1 trusted-match specification, "Package Sync": package
# metadata is synced from seller agents to TMP providers at media buy creation
# time and whenever the media buy materially changes. The obligation is
# transport-blind buyer-triggered behavior, so it belongs to a scenario rather
# than to a per-tier test that invents its own observable (#1197 review).
# Reconcile upstream in adcp-req, then retire this file for the regenerated
# scenario.
#
# Ungraded upstream: no conformance storyboard step covers Package Sync (the
# task is experimental in 3.1.1), so these scenarios are the local grading.
Feature: TMP package sync — registered providers receive package data (local)

  @T-TMP-SYNC-create @trusted_match @experimental
  Scenario: creating a media buy delivers its packages to the provider
    Given a TMP provider is registered for the tenant
    When the Buyer Agent creates a media buy
    Then the provider receives the packages for that media buy

  @T-TMP-SYNC-update @trusted_match @experimental
  Scenario: updating a media buy re-delivers its packages to the provider
    Given a TMP provider is registered for the tenant
    And the Buyer Agent created a media buy whose packages were delivered
    When the Buyer Agent updates that media buy
    Then the provider receives the packages for that media buy a second time

  @T-TMP-SYNC-no-credential @trusted_match @experimental
  Scenario: a provider registered without a credential receives no Authorization header
    Given a TMP provider is registered for the tenant
    When the Buyer Agent creates a media buy
    Then the provider receives the packages for that media buy
    And the delivery carries no credential

  @T-TMP-SYNC-credential @trusted_match @experimental
  Scenario: a credentialed provider receives the credential as a Bearer header
    Given a TMP provider with a credential is registered for the tenant
    When the Buyer Agent creates a media buy
    Then the provider receives the packages for that media buy
    And the delivery carries the provider's credential

  @T-TMP-SYNC-draining @trusted_match @experimental
  Scenario: a draining provider still receives the packages
    Given a TMP provider is registered for the tenant with status "draining"
    When the Buyer Agent creates a media buy
    Then the provider receives the packages for that media buy

  @T-TMP-SYNC-inactive @trusted_match @experimental
  Scenario: an inactive provider receives nothing
    Given a TMP provider is registered for the tenant with status "inactive"
    When the Buyer Agent creates a media buy
    Then the provider receives nothing
