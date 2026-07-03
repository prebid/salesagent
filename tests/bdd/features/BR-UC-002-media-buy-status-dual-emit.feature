# Hand-authored feature — not compiled from adcp-req (safe from compile_bdd.py --merge overwrite).
# Grounds AdCP 3.1 create-/update-media-buy-response status fields to the target GA behavior,
# asserted on the REAL wire. Graded by the 3.1.0-rc.12 storyboard pending_creatives_to_start.yaml
# (latest published compliance; no GA 3.1.0 dir exists yet). rc.12 grades `media_buy_status`
# `field_value` (REQUIRED, the PREFERRED DOMAIN status, MediaBuyStatus enum) and the top-level
# `status` `field_value` 'completed' (the PROTOCOL TaskStatus). This DIVERGES from the pinned
# SDK's beta.3 storyboard (which graded `status` field_value_or_absent MUST-equal media_buy_status,
# the deprecated "both identical" model, #4908). On the flattened wire envelope,
# TaskResultEnvelope._serialize sets top-level `status` to the protocol TaskStatus (e.g. completed/
# submitted) — a DIFFERENT namespace from the domain status; they are NOT identical. The domain
# status survives under `media_buy_status`. See docs/adcp-spec-version.md "Behavior target vs SDK pin".

@schema-v3.1 @media-buy-status-dual-emit
Feature: AdCP 3.1 media_buy_status on create/update responses
  As a Buyer (via Buyer Agent)
  I want create_media_buy and update_media_buy success responses to carry the domain
  media_buy_status (preferred) alongside the protocol-level status on the wire
  So that I can read the domain status from the stable media_buy_status field

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant exists with completed setup checklist
    And the Buyer is authenticated with a valid principal_id

  # @T-UC-002-ext-dual-emit routes through MediaBuyCreateEnv (dispatch_mode=create),
  # exercising the real _create_media_buy_impl flow on every transport (conftest _harness_env).
  @T-UC-002-ext-dual-emit @main-flow
  Scenario: create_media_buy carries domain media_buy_status and protocol status separately
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
    And the response carries the domain media_buy_status and the protocol status separately
    # Pin the exact DOMAIN value (not mere membership): a protocol value in the
    # MediaBuyStatus∩TaskStatus overlap {completed,canceled,rejected} leaked into
    # media_buy_status would pass a membership check. A fresh auto-approved buy with
    # no creatives is pending_creatives (rc.12 storyboard L147-149).
    And the wire media_buy_status should be "pending_creatives"

  # @T-UC-003-ext-dual-emit routes through MediaBuyDualEnv with a seeded existing
  # media buy, exercising the real _update_media_buy_impl flow (conftest _harness_env).
  @T-UC-003-ext-dual-emit @main-flow
  Scenario: update_media_buy carries domain media_buy_status and protocol status separately
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
    And the response carries the domain media_buy_status and the protocol status separately
    # Pin the exact DOMAIN value (not mere membership) — closes the overlap hole where
    # a protocol value {completed,canceled,rejected} leaked into media_buy_status would
    # still pass a MediaBuyStatus membership check.
    And the wire media_buy_status should be "pending_start"

  # salesagent-3ec1: a pre-flight 'scheduled' persisted status (set by admin approval)
  # is not in the AdCP vocabulary. The update response must normalize it to the domain
  # pending_start AND derive valid_actions from that normalized status — not emit a null
  # media_buy_status + empty valid_actions (which diverges from get_media_buys' date-aware
  # _compute_status, which reports pending_start).
  @T-UC-003-ext-scheduled-status @main-flow
  Scenario: update_media_buy on a scheduled buy normalizes status and reports valid_actions
    Given the Buyer owns an existing media buy with media_buy_id "mb_existing"
    And the media buy is in "scheduled" status
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
    And the wire media_buy_status should be "pending_start"
    And the wire valid_actions should include "update_budget"
    And the wire valid_actions should include "cancel"
