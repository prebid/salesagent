# Hand-authored feature — not compiled from adcp-req
# Admin UI BDD scenarios for the workflow step review page (GHSA-2968-2px9-6rjm)

Feature: BR-ADMIN-WORKFLOW-STEP-REVIEW The step review page never shows a buyer's webhook secrets
  As a publisher operating one tenant
  I want the workflow step review page to show the stored request and response without the buyer's webhook secrets
  So that a user of my tenant, a viewer included, cannot read the credential the seller signs the buyer's webhooks with

  # A workflow step stores the buyer's request as sent: create_media_buy and
  # update_media_buy dump the whole request into workflow_steps.request_data, an
  # update awaiting approval also copies it into response_data["request_data"], and
  # sync_creatives stores its push_notification_config. Webhook delivery reads the
  # registration back from the stored step, so the secrets stay in storage and the
  # review page removes them when it renders the step.
  #
  # Every webhook registration a buyer sends carries two secrets, in the pinned
  # schemas (adcp/_schemas/3.2, spec version from adcp.get_adcp_spec_version()):
  #   - token: core/push-notification-config.json, core/reporting-webhook.json and
  #     media-buy/create-media-buy-request.json artifact_webhook. The seller echoes it
  #     in every webhook payload so the buyer can tell the call came from the seller.
  #   - authentication.credentials: the same three objects. "For Bearer: token sent in
  #     Authorization header. For HMAC-SHA256: shared secret used to generate signature."
  # core/notification-config.json states the rule for the account-level registration:
  # "Credentials and shared secrets in `authentication.credentials` are write-only".
  # The review page is an admin UI page, not an AdCP task, so no storyboard grades it.
  #
  # The caller is a tenant member with the viewer role, the least role the page
  # admits (require_tenant_access() defaults to viewer). Each step also stores a name
  # and the registration's url, and the page must show both: a page that rendered no
  # payload at all would contain neither secret either.
  #
  # Transports (both run here, chosen at collection like BR-ADMIN-ACCOUNTS):
  #   - integration: Flask test_client (in-process, no Docker)
  #   - e2e: requests.Session against Docker stack (full deployment)

  Background:
    Given a target tenant with one catalogue product, one active media buy and an active member
    And the caller is an active member of the target tenant with the viewer role

  @T-ADMIN-STEPREVIEW-001 @security @main-flow
  Scenario Outline: A viewer sees a step's stored payload without the buyer's webhook secrets
    Given the target tenant has a <tool> workflow step whose stored <document> holds a <registration> with a token and a credential
    When the caller opens the review page of that workflow step
    Then the page returns status 200
    And the page shows the name and the <registration> url the step stores
    And the page shows neither the token nor the credential of the <registration>

    Examples:
      | tool             | document | registration             |
      | create_media_buy | request  | push_notification_config |
      | create_media_buy | request  | reporting_webhook        |
      | create_media_buy | request  | artifact_webhook         |
      | update_media_buy | response | push_notification_config |
      | sync_creatives   | request  | push_notification_config |
