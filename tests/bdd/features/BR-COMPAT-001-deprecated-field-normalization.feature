# Generated from adcp-req: backward-compat normalization (manual)
@compat
Feature: Deprecated field normalization across transports
  As a buyer agent using older AdCP field names,
  I want the seller agent to translate deprecated fields to current equivalents,
  so that I can interact with a v3 seller without updating my client immediately.

  Background:
    Given a tenant with products configured

  @T-COMPAT-001-brand-manifest
  Scenario: brand_manifest URL is translated to brand BrandReference
    When get_products is called with brand_manifest "https://acme.com/.well-known/brand.json" and brief "test ads"
    Then the request succeeds
    And the brand was resolved with domain "acme.com"

  @T-COMPAT-001-campaign-ref
  Scenario: campaign_ref is renamed to buyer_campaign_ref
    When the normalizer translates campaign_ref "camp-123" for create_media_buy
    Then the result contains buyer_campaign_ref "camp-123"
    And the result does not contain campaign_ref

  @T-COMPAT-001-account-id
  Scenario: account_id string is wrapped in account object
    When the normalizer translates account_id "acc-456" for get_products
    Then the result contains account with account_id "acc-456"
    And the result does not contain account_id

  @T-COMPAT-001-precedence
  Scenario: Current field takes precedence over deprecated
    When get_products is called with both brand and brand_manifest
    Then the request succeeds
    And the brand domain is "current.com" not "old.com"
