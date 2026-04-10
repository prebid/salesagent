# Hand-authored feature — manual overrides for auto-generated UC-002 scenarios.
#
# Some auto-generated scenarios are missing Given steps that are needed for
# the Then assertions to work (e.g., webhook config). These manual overrides
# add the missing setup while preserving the original scenario intent.
#
# The original scenarios are xfailed in conftest.py in favor of these.

@analysis-2026-03-09 @schema-v3.0.0-rc.1
Feature: BR-UC-002 Manual Overrides
  Corrected scenarios from BR-UC-002-create-media-buy.feature with
  missing Given steps added.

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant exists with completed setup checklist
    And the Buyer is authenticated with a valid principal_id

  # Replaces T-UC-002-alt-manual-reject from auto-generated feature.
  # Original scenario has no Given step for push_notification_config,
  # but Then asserts "Buyer should be notified via webhook" which
  # requires a webhook URL to verify against.
  @T-UC-002-alt-manual-reject-override @alternative @alt-manual @post-s12
  Scenario: Seller rejects a pending media buy (with webhook)
    Given a media buy exists in "pending_approval" state
    And the buyer has configured a webhook for notifications
    When the Seller rejects the media buy with reason "Budget too low for Q1 campaign"
    Then the media buy status should be "rejected"
    And the response should include "rejection_reason" containing "Budget too low"
    And the Buyer should be notified via webhook
