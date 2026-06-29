# Hand-authored feature — not compiled from adcp-req (safe from compile_bdd.py --merge overwrite).
# Grounds AdCP 3.1 (3.1.0-beta.3) create-/update-media-buy-response dual-emit:
# `media_buy_status` is the PREFERRED domain-status field, deprecating the body-level
# `status` (removed in 3.2). During the deprecation window the seller emits BOTH with
# IDENTICAL MediaBuyStatus values. Library: adcp==5.7.0 CreateMediaBuySuccessResponse /
# UpdateMediaBuySuccessResponse — both fields typed `MediaBuyStatus | None`.

@schema-v3.1 @media-buy-status-dual-emit
Feature: AdCP 3.1 media_buy_status dual-emit on create/update responses
  As a Buyer (via Buyer Agent)
  I want create_media_buy and update_media_buy success responses to carry both
  the preferred media_buy_status and the deprecated status with identical values
  So that I can migrate to the AdCP 3.1 preferred field without losing the status

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant exists with completed setup checklist
    And the Buyer is authenticated with a valid principal_id

  # @T-UC-002-ext-dual-emit routes through MediaBuyCreateEnv (dispatch_mode=create),
  # exercising the real _create_media_buy_impl flow on every transport (conftest _harness_env).
  @T-UC-002-ext-dual-emit @main-flow
  Scenario: create_media_buy emits status and media_buy_status identically
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request with:
    | field          | value                |
    | account        | account_id "acc-001" |
    | brand          | domain "acme.com"    |
    | start_time     | {1 day from now}     |
    | end_time       | {30 days from now}   |
    And the request includes 2 packages with valid product_ids
    And each package has a positive budget meeting minimum spend
    And all packages use the same currency "USD"
    And each package has a valid pricing_option_id
    And the account "acc-001" exists and is active
    And the ad server adapter is available
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the response should include both status and media_buy_status with identical values

  # @T-UC-003-ext-dual-emit routes through MediaBuyDualEnv with a seeded existing
  # media buy, exercising the real _update_media_buy_impl flow (conftest _harness_env).
  @T-UC-003-ext-dual-emit @main-flow
  Scenario: update_media_buy emits status and media_buy_status identically
    Given the Buyer owns an existing media buy with media_buy_id "mb_existing"
    And the tenant is configured for auto-approval
    And a valid update_media_buy request with:
    | field        | value       |
    | media_buy_id | mb_existing |
    And the request includes 1 package update with:
    | field      | value   |
    | package_id | pkg_001 |
    | budget     | 5000    |
    And the package "pkg_001" exists in the media buy
    And the updated daily spend does not exceed max_daily_package_spend
    When the Buyer Agent sends the update_media_buy request
    Then the response should succeed
    And the response should include both status and media_buy_status with identical values
