# AdCP 3.1.1 docs/media-buy/advanced-topics/sandbox.mdx, Seller implementation:
# normal validation and simulated execution. Vendor effects are locally graded.
# Submitted envelope: create_media_buy_async.yaml submitted_arm_response.create_media_buy_submitted.
# Account ownership: update_media_buy.mdx; invalid_transitions.yaml unknown_media_buy.update_unknown_media_buy.
@sandbox-lifecycle
Feature: Account-safe sandbox lifecycle

  @T-SANDBOX-LIFECYCLE-applied-updates
  Scenario Outline: Applied package and campaign updates survive readback
    Given a configured seller with automatic approval
    And a "<account>" buy ready for the "<action>" update
    When that account applies the "<action>" update
    Then the wire response has status "completed"
    And the persisted update has budget 6000 and package paused "<package_paused>" and campaign paused "<campaign_paused>"
    When that account lists the updated buy
    Then the listed package has budget 6000 and paused "<package_paused>"

    Examples:
      | account | action          | package_paused | campaign_paused |
      | sandbox | budget          | false          | false           |
      | sandbox | pause           | true           | false           |
      | sandbox | resume          | false          | false           |
      | sandbox | pause_targeting | true           | false           |
      | sandbox | resume_targeting | false         | false           |
      | sandbox | budget_targeting | true          | false           |
      | sandbox | campaign_pause  | false          | true            |
      | sandbox | campaign_resume | false          | false           |
      | live    | campaign_pause  | false          | true            |
      | live    | campaign_resume | false          | false           |

  @T-SANDBOX-LIFECYCLE-live-package-outcomes
  Scenario Outline: Live mock retains its package update outcomes
    Given a configured seller with automatic approval
    And a "live" buy ready for the "<action>" update
    When that account applies the "<action>" update
    Then the wire response has status "completed"

    Examples:
      | action |
      | budget |
      | pause  |
      | resume |

  @T-SANDBOX-LIFECYCLE-pending-update
  Scenario: Package updates wait for normal approval
    Given a configured seller requiring human approval
    And a "sandbox" buy ready for the "budget" update
    When that account applies the "budget" update
    Then the wire response has status "submitted" and a task identifier
    And the stored buy and package are unchanged

  @T-SANDBOX-LIFECYCLE-mock-targeting-refusals
  Scenario Outline: Mock targeting refusals apply to live and sandbox execution
    Given a configured seller with automatic approval
    And the requesting account is "<account>"
    When that account requests targeting "<field>" with value "<value>"
    Then the wire error is "UNSUPPORTED_FEATURE" with recovery "correctable"
    And the targeting refusal names capability "<field>"

    Examples:
      | account | field              | value   |
      | live    | device_platform    | ios     |
      | sandbox | device_platform    | ios     |
      | live    | device_type_any_of | mobile  |
      | sandbox | device_type_any_of | mobile  |
      | live    | os_any_of          | ios     |
      | sandbox | os_any_of          | ios     |
      | live    | browser_any_of     | chrome  |
      | sandbox | browser_any_of     | chrome  |
      | live    | content_cat_any_of | IAB1    |
      | sandbox | content_cat_any_of | IAB1    |
      | live    | keywords_any_of    | sports  |
      | sandbox | keywords_any_of    | sports  |

  @T-SANDBOX-LIFECYCLE-configured-targeting
  Scenario Outline: Configured targeting accepts supported values
    Given a seller configured for "<adapter>" with automatic approval
    And the requesting account is "<account>"
    When that account requests targeting "<field>" with value "<value>"
    Then the wire response has status "completed"

    Examples:
      | adapter            | account | field           | value |
      | mock               | live    | geo_countries   | US    |
      | mock               | sandbox | geo_countries   | US    |
      | google_ad_manager | sandbox | media_type_any_of | display |
      | kevel             | sandbox | media_type_any_of | display |
      | triton            | sandbox | media_type_any_of | audio   |

  @T-SANDBOX-LIFECYCLE-gam-targeting-refusal
  Scenario Outline: Sandbox rejects targeting that the configured adapter cannot execute
    Given a seller configured for "<adapter>" with automatic approval
    And the requesting account is "sandbox"
    When that account requests targeting "<field>" with value "<value>"
    Then the wire error is "UNSUPPORTED_FEATURE" with recovery "correctable"

    Examples:
      | adapter           | field              | value  |
      | google_ad_manager | media_type_any_of  | audio  |
      | google_ad_manager | device_platform    | ios    |
      | google_ad_manager | device_type_any_of | mobile |
      | google_ad_manager | os_any_of          | ios    |
      | google_ad_manager | browser_any_of     | chrome |
      | google_ad_manager | content_cat_any_of | IAB1   |
      | google_ad_manager | keywords_any_of    | sports |
      | kevel             | media_type_any_of  | video  |
      | kevel             | device_type_any_of | ctv    |
      | triton            | media_type_any_of  | display |
      | triton            | device_type_any_of | tablet |
      | triton            | browser_any_of     | chrome |
      | triton            | content_cat_any_of | IAB1   |

  @T-SANDBOX-LIFECYCLE-pending-create
  Scenario: Sandbox creation preserves normal approval
    Given a configured seller requiring human approval
    And an accessible sandbox account
    When that account requests a valid media buy
    Then the wire response has status "submitted" and a task identifier
    And it has no confirmed media buy identifier or packages

  @T-SANDBOX-LIFECYCLE-account-isolation
  Scenario Outline: A different account cannot update an existing buy
    Given a configured seller with automatic approval
    And a buy owned by another accessible "<account>" account of the same principal
    When the sandbox account requests an update to that buy
    Then the wire error is "MEDIA_BUY_NOT_FOUND" with recovery "correctable"
    And the stored buy is unchanged

    Examples:
      | account |
      | live    |
      | sibling |

  @T-SANDBOX-LIFECYCLE-gam-validation
  Scenario: Sandbox validates configured GAM products
    Given a sandbox seller configured for GAM
    And a product with invalid GAM configuration
    When that account requests a valid media buy
    Then the wire error is "VALIDATION_ERROR" with recovery "correctable"

  @T-SANDBOX-LIFECYCLE-gam-shared-policy
  Scenario: Sandbox defaults do not change shared GAM product configuration
    Given a sandbox seller configured for GAM
    And a product without GAM configuration
    When that account requests a valid media buy
    Then the wire response has status "completed"
    And the shared product still has no GAM configuration

  @T-SANDBOX-LIFECYCLE-reporting-scope
  Scenario Outline: A supplied account filters reporting
    Given a configured seller with automatic approval
    And stored buys belonging to sandbox and live accounts
    When the buyer requests "<tool>" for the sandbox account
    Then only the sandbox buy appears in "<field>"

    Examples:
      | tool                   | field                |
      | get_media_buys         | media_buys           |
      | get_media_buy_delivery | media_buy_deliveries |

  @T-SANDBOX-LIFECYCLE-snapshot-selection
  Scenario: Unscoped snapshots select each stored account
    Given a configured seller with automatic approval
    And a stored sandbox buy and a live buy created through the seller
    When the buyer requests snapshots across accounts
    Then the sandbox buy has a zero delivery snapshot

  @T-SANDBOX-LIFECYCLE-delivery-selection
  Scenario: Unscoped delivery selects each stored account
    Given a configured seller with automatic approval
    And stored buys belonging to sandbox and live accounts
    When the buyer requests delivery across accounts
    Then the sandbox buy has zero delivered impressions and spend

  @T-SANDBOX-LIFECYCLE-approval-policy
  Scenario Outline: Normal approval policy applies to each account
    Given a seller requiring approval through "<gate>"
    And the requesting account is "<account>"
    When that account requests a valid media buy
    Then the wire response has status "submitted" and a task identifier
    And it has no confirmed media buy identifier or packages

    Examples:
      | gate    | account |
      | tenant  | live    |
      | tenant  | sandbox |
      | adapter | live    |
      | adapter | sandbox |
      | product | live    |
      | product | sandbox |

  @T-SANDBOX-LIFECYCLE-purchase-order-isolation
  Scenario: Repeated purchase order text cannot alias sandbox accounts
    Given a configured seller with automatic approval
    When sandbox accounts create buys with the same purchase order text
    Then the two simulated buys have distinct identifiers and stored accounts

  @T-SANDBOX-LIFECYCLE-shared-creative
  Scenario: Immediate sandbox creative upload preserves shared live references
    Given a sandbox seller configured for GAM
    And an approved creative already assigned to a live buy
    When the sandbox account creates a buy with that creative
    Then the wire response has status "completed"
    And that buy has its own simulated creative reference
    And the shared live creative reference is unchanged

  @T-SANDBOX-LIFECYCLE-unresolved-account
  # AdCP 3.1.1 enums/error-code.json, CONFIGURATION_ERROR: transport failure
  # markers are mandatory. This stored-account mapping is locally graded.
  Scenario Outline: An unresolved stored account cannot select live execution
    Given a sandbox seller configured for GAM
    And a stored buy whose account is "<condition>"
    When the buyer requests "<tool>" for the broken stored buy
    Then the wire error is "CONFIGURATION_ERROR" with recovery "terminal"

    Examples:
      | condition | tool                   |
      | absent    | get_media_buys         |
      | absent    | get_media_buy_delivery |

  @T-SANDBOX-LIFECYCLE-account-refusals
  # AdCP 3.1.1 enums/error-code.json: preserve account refusal codes/recovery.
  # Per-buy reporting advisory placement is a local policy, ungraded by storyboards.
  Scenario Outline: Stored account refusals retain their specific reporting advisory
    Given a sandbox seller configured for GAM
    And a stored buy whose account is "<condition>"
    When the buyer requests "<tool>" for the broken stored buy
    Then the reporting advisory is "<code>" with recovery "<recovery>"

    Examples:
      | condition        | tool                   | code                     | recovery    |
      | suspended        | get_media_buys         | ACCOUNT_SUSPENDED         | terminal    |
      | suspended        | get_media_buy_delivery | ACCOUNT_SUSPENDED         | terminal    |
      | pending_approval | get_media_buys         | ACCOUNT_SETUP_REQUIRED    | correctable |
      | pending_approval | get_media_buy_delivery | ACCOUNT_SETUP_REQUIRED    | correctable |
      | payment_required | get_media_buys         | ACCOUNT_PAYMENT_REQUIRED  | terminal    |
      | payment_required | get_media_buy_delivery | ACCOUNT_PAYMENT_REQUIRED  | terminal    |
      | revoked          | get_media_buys         | PERMISSION_DENIED         | correctable |
      | revoked          | get_media_buy_delivery | PERMISSION_DENIED         | correctable |

  @T-SANDBOX-LIFECYCLE-misconfigured-adapter
  Scenario Outline: Stored live buys require valid adapter configuration for reporting
    Given a sandbox seller configured for GAM
    And a live stored buy whose GAM credentials are absent
    When the buyer requests "<tool>" for the broken stored buy
    Then the wire error is "CONFIGURATION_ERROR" with recovery "terminal"

    Examples:
      | tool                   |
      | get_media_buys          |
      | get_media_buy_delivery |

  @T-SANDBOX-LIFECYCLE-no-snapshot-execution
  Scenario: Listing without snapshots needs no stored account execution
    Given a sandbox seller configured for GAM
    And a stored buy whose account is "absent"
    When the buyer lists the broken stored buy without snapshots
    Then the listing includes the buy without a configuration advisory
