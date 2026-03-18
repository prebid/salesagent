# @contextgit id=T-UC-011 type=test upstream=[BR-UC-011,BR-RULE-054,BR-RULE-055,BR-RULE-056,BR-RULE-057,BR-RULE-058,BR-RULE-059,BR-RULE-060,BR-RULE-061,BR-RULE-062,BR-RULE-043]
Feature: BR-UC-011 Manage Accounts
  As a Buyer
  I want to query and provision billing accounts with the Seller Agent
  So that I can manage advertiser relationships and billing arrangements

  # Postconditions verified:
  #   POST-S1: Buyer knows which billing accounts are accessible to them
  #   POST-S2: Buyer knows each account's status
  #   POST-S3: Buyer knows the advertiser, billing proxy, rate card, and payment terms
  #   POST-S4: Buyer can paginate through accounts
  #   POST-S5: Buyer knows the seller-assigned account_id
  #   POST-S6: Buyer knows the action taken per account
  #   POST-S7: Buyer knows billing model for each account
  #   POST-S8: Buyer receives setup information for pending accounts
  #   POST-S9: Buyer knows which accounts were deactivated
  #   POST-S10: Buyer receives dry-run preview
  #   POST-F1: System state unchanged on failure
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Application context echoed when possible

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context

  # ============================================================
  # Group 1: List Accounts -- Main Flows (MCP + A2A)
  # Postconditions: POST-S1, POST-S2, POST-S3, POST-S4
  # Rules: BR-RULE-054 (access scoping), BR-RULE-055 (auth policy)
  # ============================================================

  # @contextgit id=T-UC-011-list-main type=test upstream=[BR-UC-011-main-list-mcp,BR-UC-011-main-list-a2a,BR-RULE-054,BR-RULE-055]
  @list @happy-path @post-s1 @post-s2 @post-s3 @partition @boundary
  Scenario Outline: List accounts via <transport> (authenticated_with_accounts)
    # @bva accounts (response): multiple accounts visible
    # @bva authentication (account operations): valid token on list
    Given the Buyer Agent has an authenticated connection via <transport>
    And the agent has 3 accessible accounts with statuses "active", "pending_approval", "suspended"
    When the Buyer Agent sends a list_accounts request via <transport>
    Then the response contains an accounts array with 3 items
    And each account includes account_id, name, status, advertiser, rate_card, and payment_terms
    And the accounts are only those accessible to the authenticated agent
    # POST-S1: Buyer knows which accounts are accessible
    # POST-S2: Buyer knows each account's status
    # POST-S3: Buyer knows advertiser, billing, rate card, payment terms

    Examples:
      | transport |
      | MCP       |
      | A2A       |

  # @contextgit id=T-UC-011-list-status-filter type=test upstream=[BR-UC-011-main-list-mcp,BR-RULE-054]
  @list @status-filter @partition @boundary
  Scenario Outline: List accounts filtered by status <status> (status_filter_match)
    # @bva status: active (first enum value), closed (last enum value)
    # @bva accounts (response): status filter = specific value with matches
    Given the Buyer Agent has an authenticated connection via MCP
    And the agent has accounts with statuses "active", "pending_approval", "suspended", "closed"
    When the Buyer Agent sends a list_accounts request with status filter "<status>"
    Then the response contains only accounts with status "<status>"
    And accounts with other statuses are excluded

    Examples:
      | status             |
      | active             |
      | pending_approval   |
      | payment_required   |
      | suspended          |
      | closed             |

  # @contextgit id=T-UC-011-list-no-accounts type=test upstream=[BR-RULE-054,BR-RULE-055]
  @list @empty-result @partition @boundary
  Scenario: List accounts returns empty when authenticated agent has no accounts (0 accounts visible)
    # @bva accounts (response): 0 accounts visible
    Given the Buyer Agent has an authenticated connection via MCP
    And the agent has no accessible accounts
    When the Buyer Agent sends a list_accounts request
    Then the response contains an empty accounts array
    And the response is not an error

  # @contextgit id=T-UC-011-list-unauth type=test upstream=[BR-RULE-055]
  @list @auth @partition @boundary
  Scenario: List accounts without authentication returns empty (no token on list)
    # @bva authentication (account operations): no token on list
    Given the Buyer Agent has an unauthenticated connection via MCP
    When the Buyer Agent sends a list_accounts request without an authentication token
    Then the response contains an empty accounts array
    And the response is not an error

  # @contextgit id=T-UC-011-list-pagination type=test upstream=[BR-UC-011-main-list-mcp,BR-UC-011-main-list-a2a]
  @list @pagination @post-s4
  Scenario: List accounts with pagination
    Given the Buyer Agent has an authenticated connection via MCP
    And the agent has 120 accessible accounts
    When the Buyer Agent sends a list_accounts request with max_results 50
    Then the response contains 50 accounts
    And the response includes pagination metadata with has_more true and a cursor
    When the Buyer Agent sends a list_accounts request with the returned cursor
    Then the response contains 50 more accounts
    And the response includes pagination metadata with has_more true
    # POST-S4: Buyer can paginate through accounts

  # @contextgit id=T-UC-011-list-status-filter-no-match type=test upstream=[BR-RULE-054]
  @list @status-filter @empty-result @partition @boundary
  Scenario: List accounts with status filter returns empty when no matches (status filter = specific value with no matches)
    # @bva accounts (response): status filter = specific value with no matches
    Given the Buyer Agent has an authenticated connection via MCP
    And the agent has accounts with statuses "active", "active", "active"
    When the Buyer Agent sends a list_accounts request with status filter "suspended"
    Then the response contains an empty accounts array
    And the response is not an error

  # @contextgit id=T-UC-011-list-invalid-status type=test upstream=[BR-UC-011]
  @list @validation @partition @boundary
  Scenario: List accounts with unknown status value not in enum
    # @bva status: Unknown string not in enum
    Given the Buyer Agent has an authenticated connection via MCP
    When the Buyer Agent sends a list_accounts request with status filter "unknown_status"
    Then the response contains a validation error
    And the error indicates the status value is not recognized

  # @contextgit id=T-UC-011-list-pagination-bva type=test upstream=[BR-UC-011]
  @list @pagination @bva @partition @boundary
  Scenario Outline: List accounts pagination boundary - max_results <value>
    Given the Buyer Agent has an authenticated connection via MCP
    And the agent has 200 accessible accounts
    When the Buyer Agent sends a list_accounts request with max_results <value>
    Then the response has outcome "<outcome>"

    Examples:
      | value | outcome                         |
      | 0     | validation error                |
      | 1     | success with 1 account          |
      | 50    | success with 50 accounts        |
      | 100   | success with 100 accounts       |
      | 101   | validation error                |

  # @contextgit id=T-UC-011-list-status-all type=test upstream=[BR-RULE-054]
  @list @status-filter @partition @boundary
  Scenario: List accounts with no status filter returns all statuses (status filter = 'all')
    # @bva accounts (response): status filter = 'all'
    Given the Buyer Agent has an authenticated connection via MCP
    And the agent has accounts with statuses "active", "pending_approval", "suspended", "closed"
    When the Buyer Agent sends a list_accounts request without a status filter
    Then the response contains accounts with all statuses
    And the result set is identical to requesting without any filter

  # ============================================================
  # Group 2: Sync Accounts -- Main Flow
  # Postconditions: POST-S5, POST-S6, POST-S7
  # Rules: BR-RULE-056 (upsert), BR-RULE-057 (atomic), BR-RULE-058 (brand identity), BR-RULE-059 (billing)
  # ============================================================

  # @contextgit id=T-UC-011-sync-create type=test upstream=[BR-UC-011-main-sync,BR-RULE-056,BR-RULE-058]
  @sync @happy-path @post-s5 @post-s6 @partition @boundary
  Scenario: Sync new account -- single_brand_domain, all_created (1 account, all same action)
    # @bva authentication (account operations): valid token on sync
    # @bva accounts (sync operation): 1 account (minimum)
    # @bva accounts (sync operation): all same action
    # @bva brand (brand-ref): single_brand_domain -- brand with domain only (single brand)
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator        | billing  |
      | acme-corp.com   | acme-corp.com   | operator |
    Then the response is a success variant with accounts array
    And the account for brand domain "acme-corp.com" has action "created"
    And the account has a seller-assigned account_id
    And the account has status "active"
    And the response includes brand domain "acme-corp.com" echoed from request
    # POST-S5: Buyer knows the seller-assigned account_id
    # POST-S6: Buyer knows the action taken per account

  # @contextgit id=T-UC-011-sync-multi-brand type=test upstream=[BR-RULE-058]
  @sync @brand-identity @partition @boundary
  Scenario: Sync multi_brand_domain with brand_id and operator (brand with domain + brand_id)
    # @bva brand (brand-ref): multi_brand_domain -- brand with domain + brand_id (multi brand)
    # @bva brand (brand-ref): brand with domain + brand_id + operator
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | brand.brand_id | operator        | billing  |
      | nova-brands.com | spark          | pinnacle-media.com | operator |
      | nova-brands.com | glow           | pinnacle-media.com | agent    |
    Then the response contains 2 account results
    And the account for brand domain "nova-brands.com" brand_id "spark" has action "created"
    And the account for brand domain "nova-brands.com" brand_id "glow" has action "created"
    And each account echoes brand domain and brand_id from the request

  # @contextgit id=T-UC-011-sync-brand-direct type=test upstream=[BR-RULE-058,BR-RULE-059]
  @sync @brand-identity @partition @boundary
  Scenario: Sync brand_direct -- brand operating own seat (operator is brand's domain)
    # @bva brand (brand-ref): brand_direct -- brand operating own seat
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator        | billing  |
      | acme-corp.com   | acme-corp.com   | operator |
    Then the account for brand domain "acme-corp.com" has action "created"
    And the account operator is "acme-corp.com"
    And the account billing is "operator"

  # @contextgit id=T-UC-011-sync-update type=test upstream=[BR-RULE-056]
  @sync @upsert @partition
  Scenario: Sync updates existing account -- all_updated
    Given the Buyer Agent has an authenticated connection via A2A
    And an account for brand domain "acme-corp.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator      | billing |
      | acme-corp.com   | acme-corp.com | agent   |
    Then the account for brand domain "acme-corp.com" has action "updated"
    And the account billing is "agent"

  # @contextgit id=T-UC-011-sync-unchanged type=test upstream=[BR-RULE-056]
  @sync @upsert @partition
  Scenario: Sync unchanged account is idempotent -- all_unchanged
    Given the Buyer Agent has an authenticated connection via A2A
    And an account for brand domain "acme-corp.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then the account for brand domain "acme-corp.com" has action "unchanged"

  # @contextgit id=T-UC-011-sync-billing-enum type=test upstream=[BR-RULE-059]
  @sync @billing @post-s7 @partition @boundary
  Scenario Outline: Sync with billing model <billing> -- <partition_name>
    # @bva billing: operator (first enum value), agent (last enum value)
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator      | billing   |
      | acme-corp.com   | acme-corp.com | <billing> |
    Then the account billing is "<billing>"
    # POST-S7: Buyer knows billing model for each account

    Examples:
      | billing  | partition_name   | boundary_point                |
      | operator | operator_honored | billing = operator            |
      | agent    | agent_honored    | billing = agent               |

  # @contextgit id=T-UC-011-sync-mixed type=test upstream=[BR-RULE-056]
  @sync @upsert @partition
  Scenario: Sync mixed_results -- created and updated in same request (all different actions)
    Given the Buyer Agent has an authenticated connection via A2A
    And an account for brand domain "existing-brand.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain        | operator            | billing  |
      | new-brand.com       | new-brand.com       | operator |
      | existing-brand.com  | existing-brand.com  | agent    |
    Then the account for brand domain "new-brand.com" has action "created"
    And the account for brand domain "existing-brand.com" has action "updated"

  # @contextgit id=T-UC-011-sync-brand-echo type=test upstream=[BR-RULE-056,BR-RULE-058]
  @sync @invariant @partition
  Scenario: Sync echoes brand from request in per-account result (brand echo)
    # BR-RULE-056 INV-4: Request includes brand for an account -> response echoes same brand value
    # BR-RULE-058 INV-3: Account is processed -> response echoes brand (brand-ref) from request
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | brand.brand_id | operator        | billing  |
      | nova-brands.com | spark          | pinnacle-media.com | operator |
    Then the per-account result echoes brand domain "nova-brands.com" and brand_id "spark"

  # @contextgit id=T-UC-011-sync-shortest-domain type=test upstream=[BR-RULE-058]
  @sync @brand-identity @partition @boundary
  Scenario: Sync with shortest valid domain (e.g., 'a.b')
    # @bva brand (brand-ref): shortest valid domain (e.g., 'a.b')
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain | operator | billing  |
      | a.b          | a.b      | operator |
    Then the account for brand domain "a.b" has action "created"

  # ============================================================
  # Extension A: AUTH_TOKEN_INVALID
  # Postconditions: POST-F1, POST-F2, POST-F3
  # Rule: BR-RULE-055 INV-1
  # ============================================================

  # @contextgit id=T-UC-011-ext-a-no-token type=test upstream=[BR-UC-011-ext-a,BR-RULE-055,BR-RULE-057]
  @sync @ext-a @auth @error @post-f1 @post-f2 @partition @boundary
  Scenario: Sync without authentication -- sync_no_token returns error_auth (no token on sync)
    # @bva authentication (account operations): no token on sync
    Given the Buyer Agent has an unauthenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then the response is an error variant with no accounts array
    And the error code is "AUTH_TOKEN_INVALID"
    And the error message describes the authentication requirement
    And the error should include "suggestion" field with remediation guidance
    And no accounts were modified on the seller
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows what failed

  # @contextgit id=T-UC-011-ext-a-expired type=test upstream=[BR-UC-011-ext-a,BR-RULE-055]
  @sync @ext-a @auth @error @partition @boundary
  Scenario: Sync with expired token -- sync_invalid_token returns AUTH_TOKEN_INVALID (invalid token on sync)
    # @bva authentication (account operations): invalid token on sync
    Given the Buyer Agent has an A2A connection with an expired token
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then the response is an error variant
    And the error code is "AUTH_TOKEN_INVALID"
    And the error should include "suggestion" field with remediation guidance

  # ============================================================
  # Extension B: SYNC_PARTIAL_FAILURE
  # Rule: BR-RULE-056 INV-3, BR-RULE-057 INV-1
  # ============================================================

  # @contextgit id=T-UC-011-ext-b-partial type=test upstream=[BR-UC-011-ext-b,BR-RULE-056,BR-RULE-057]
  @sync @partial-failure @invariant @partition @boundary
  Scenario: Sync partial_failure -- success_partial_failure with action=failed (action=failed with errors)
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain        | operator            | billing  |
      | acme-corp.com       | acme-corp.com       | operator |
      | invalid-brand.test  | invalid-brand.test  | operator |
    Then the response is a success variant with accounts array
    And the account for brand domain "acme-corp.com" has action "created"
    And the account for brand domain "invalid-brand.test" has action "failed"
    And the failed account includes a per-account errors array
    And the response does not contain an operation-level errors field

  # ============================================================
  # Extension C: BILLING_NOT_SUPPORTED
  # Rule: BR-RULE-059 INV-2
  # ============================================================

  # @contextgit id=T-UC-011-ext-c-rejected type=test upstream=[BR-UC-011-ext-c,BR-RULE-059]
  @sync @ext-c @billing @error @partition @boundary
  Scenario: Seller rejects unsupported billing -- billing_rejected (billing = unsupported value for seller)
    # @bva billing: billing = unsupported value for seller
    # BR-RULE-059 INV-2: Request includes billing model the seller does not support -> action=failed, status=rejected, BILLING_NOT_SUPPORTED
    Given the Buyer Agent has an authenticated connection via A2A
    And the seller does not support "operator" billing
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then the account for brand domain "acme-corp.com" has action "failed"
    And the account has status "rejected"
    And the per-account errors array contains an error with code "BILLING_NOT_SUPPORTED"
    And the error message explains the billing model is not available
    And the error should include "suggestion" field with remediation guidance
    # POST-F2: Buyer knows what failed and the specific error code

  # @contextgit id=T-UC-011-ext-c-mixed type=test upstream=[BR-UC-011-ext-c,BR-UC-011-ext-b,BR-RULE-059,BR-RULE-057]
  @sync @ext-c @billing @partial-failure @partition
  Scenario: Billing rejection is per-account -- other accounts still succeed
    # BR-RULE-059 INV-2 + BR-RULE-057 INV-1: rejected billing produces per-account failure within success variant
    Given the Buyer Agent has an authenticated connection via A2A
    And the seller supports "agent" billing but not "operator" billing
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain      | operator          | billing  |
      | good-brand.com    | good-brand.com    | agent    |
      | bad-brand.com     | bad-brand.com     | operator |
    Then the response is a success variant with accounts array
    And the account for brand domain "good-brand.com" has action "created"
    And the account for brand domain "bad-brand.com" has action "failed"
    And the failed account has status "rejected" with BILLING_NOT_SUPPORTED error
    And the error should include "suggestion" field with remediation guidance

  # @contextgit id=T-UC-011-ext-c-invalid-enum type=test upstream=[BR-RULE-059]
  @sync @billing @validation @partition @boundary
  Scenario: Billing value not in enum -- invalid_billing_value (billing = invalid string)
    # @bva billing: billing = invalid string
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | prepaid  |
    Then the account processing fails with a validation error for billing

  # ============================================================
  # Extension D: ACCOUNT_PENDING_APPROVAL
  # Rule: BR-RULE-060 INV-1, INV-2, INV-3
  # Postcondition: POST-S8
  # ============================================================

  # @contextgit id=T-UC-011-ext-d-pending-url type=test upstream=[BR-UC-011-ext-d,BR-RULE-060]
  @sync @approval @post-s8 @partition @boundary
  Scenario: Account pending_with_url -- setup with url + message + expires_at (status = pending_approval with setup)
    Given the Buyer Agent has an authenticated connection via A2A
    And the seller requires credit review for new accounts
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then the account has status "pending_approval"
    And the account has action "created"
    And the account includes a setup object
    And the setup object includes a message describing the required action
    And the setup object includes a URL for the human buyer
    And the setup object includes an expires_at timestamp
    # POST-S8: Buyer receives setup information

  # @contextgit id=T-UC-011-ext-d-pending-message type=test upstream=[BR-UC-011-ext-d,BR-RULE-060]
  @sync @approval @partition @boundary
  Scenario: Account pending_message_only -- setup with message only
    Given the Buyer Agent has an authenticated connection via A2A
    And the seller requires legal review for new accounts
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then the account has status "pending_approval"
    And the setup object includes a message
    And the setup object does not include a URL

  # @contextgit id=T-UC-011-ext-d-active type=test upstream=[BR-RULE-060]
  @sync @approval @partition @boundary
  Scenario: Account immediately active -- active_no_setup (status = active (no setup))
    Given the Buyer Agent has an authenticated connection via A2A
    And the seller auto-approves new accounts
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then the account has status "active"
    And the account does not include a setup object

  # @contextgit id=T-UC-011-ext-d-push type=test upstream=[BR-UC-011-ext-d,BR-RULE-060]
  @sync @push-notification @partition
  Scenario: Push notification for async status changes -- with_push_notification
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    And the request includes a push_notification_config with url "https://agent.com/webhooks"
    Then the system registers the webhook for async account status notifications
    And when the account transitions from "pending_approval" to "active"
    Then a push notification is sent to "https://agent.com/webhooks"

  # ============================================================
  # Extension E: DRY_RUN
  # Rule: BR-RULE-062 INV-1, INV-2, INV-3
  # Postcondition: POST-S10
  # ============================================================

  # @contextgit id=T-UC-011-ext-e-preview type=test upstream=[BR-UC-011-ext-e,BR-RULE-062]
  @sync @dry-run @post-s10 @partition @boundary
  Scenario: dry_run_true returns preview -- success_dry_run (dry_run = true)
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with dry_run true and:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then the response is a success variant
    And the response includes dry_run true
    And the account for brand domain "acme-corp.com" shows action "created"
    And no accounts were actually created or modified on the seller
    # POST-S10: Buyer receives dry-run preview

  # @contextgit id=T-UC-011-ext-e-normal type=test upstream=[BR-RULE-062]
  @sync @dry-run @partition @boundary
  Scenario: dry_run_false -- normal sync applies changes (dry_run = false)
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with dry_run false and:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then the response does not include a dry_run field
    And the account was actually created on the seller

  # @contextgit id=T-UC-011-ext-e-omitted type=test upstream=[BR-RULE-062]
  @sync @dry-run @partition @boundary
  Scenario: dry_run_omitted -- default behavior applies changes (dry_run omitted)
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then the response does not include a dry_run field
    And the account was actually created on the seller

  # ============================================================
  # Extension F: DELETE_MISSING
  # Rule: BR-RULE-061 INV-1, INV-2, INV-3
  # Postcondition: POST-S9
  # ============================================================

  # @contextgit id=T-UC-011-ext-f-deactivate type=test upstream=[BR-UC-011-ext-f,BR-RULE-061]
  @sync @delete-missing @post-s9 @partition @boundary
  Scenario: delete_missing_true deactivates absent accounts (delete_missing = true with absent accounts)
    Given the Buyer Agent has an authenticated connection via A2A
    And the agent previously synced accounts for brand domain "acme-corp.com" and "old-brand.com"
    When the Buyer Agent sends a sync_accounts request with delete_missing true and:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then the response includes a result for brand domain "old-brand.com" showing deactivation
    And the account for brand domain "acme-corp.com" has action "unchanged" or "updated"
    # POST-S9: Buyer knows which accounts were deactivated

  # @contextgit id=T-UC-011-ext-f-scoped type=test upstream=[BR-UC-011-ext-f,BR-RULE-061]
  @sync @delete-missing @agent-scoped
  Scenario: Delete missing scoped to authenticated agent only
    Given the Buyer Agent has an authenticated connection via A2A
    And agent A previously synced accounts for brand domain "brand-a.com"
    And agent B previously synced accounts for brand domain "brand-b.com"
    When agent A sends a sync_accounts request with delete_missing true and:
      | brand.domain    | operator      | billing  |
      | brand-a.com     | brand-a.com   | operator |
    Then agent B's account for brand domain "brand-b.com" is not affected
    And only agent A's absent accounts are deactivated

  # @contextgit id=T-UC-011-ext-f-false type=test upstream=[BR-RULE-061]
  @sync @delete-missing @partition @boundary
  Scenario: delete_missing_false preserves absent accounts (delete_missing = false with absent accounts)
    Given the Buyer Agent has an authenticated connection via A2A
    And the agent previously synced accounts for brand domain "acme-corp.com" and "old-brand.com"
    When the Buyer Agent sends a sync_accounts request with delete_missing false and:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then brand domain "old-brand.com" remains in its current state
    And only the included accounts are processed

  # @contextgit id=T-UC-011-ext-f-none-absent type=test upstream=[BR-RULE-061]
  @sync @delete-missing @partition @boundary
  Scenario: delete_missing_none_absent -- true with no absent accounts (delete_missing = true with no absent accounts)
    Given the Buyer Agent has an authenticated connection via A2A
    And the agent previously synced accounts for brand domain "acme-corp.com" only
    When the Buyer Agent sends a sync_accounts request with delete_missing true and:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then no accounts are deactivated
    And the account for brand domain "acme-corp.com" is processed normally

  # @contextgit id=T-UC-011-ext-f-omitted type=test upstream=[BR-RULE-061]
  @sync @delete-missing @partition @boundary
  Scenario: delete_missing_omitted -- default preserves accounts (delete_missing omitted)
    Given the Buyer Agent has an authenticated connection via A2A
    And the agent previously synced accounts for brand domain "acme-corp.com" and "old-brand.com"
    When the Buyer Agent sends a sync_accounts request without delete_missing and:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then brand domain "old-brand.com" remains in its current state
    And only the included accounts are processed

  # ============================================================
  # Extension G: CONTEXT_ECHO
  # Rule: BR-RULE-043 INV-1, INV-2
  # Postcondition: POST-F3
  # ============================================================

  # @contextgit id=T-UC-011-ext-g-echo type=test upstream=[BR-UC-011-ext-g,BR-RULE-043]
  @context-echo @post-f3 @partition @boundary
  Scenario Outline: context_provided -- context echoed in <operation> response (context with properties)
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends a <operation> request with context {"session_id": "abc-123", "trace": "xyz-789"}
    Then the response includes context {"session_id": "abc-123", "trace": "xyz-789"}
    And the context is identical to what was sent
    # POST-F3: Application context echoed when possible

    Examples:
      | operation      |
      | list_accounts  |
      | sync_accounts  |

  # @contextgit id=T-UC-011-ext-g-echo-error type=test upstream=[BR-UC-011-ext-g,BR-RULE-043]
  @context-echo @error @post-f3
  Scenario: Context echoed in sync error response
    Given the Buyer Agent has an unauthenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with context {"trace": "err-001"}
    Then the response is an error variant with AUTH_TOKEN_INVALID
    And the response includes context {"trace": "err-001"}
    And the error should include "suggestion" field with remediation guidance
    # POST-F3: Context echoed even on error path

  # @contextgit id=T-UC-011-ext-g-absent type=test upstream=[BR-RULE-043]
  @context-echo @partition @boundary
  Scenario: context_absent -- context omitted from response (context absent)
    Given the Buyer Agent has an authenticated connection via MCP
    When the Buyer Agent sends a list_accounts request without a context object
    Then the response does not include a context field

  # @contextgit id=T-UC-011-ext-g-empty type=test upstream=[BR-RULE-043]
  @context-echo @partition @boundary
  Scenario: context_empty_object -- empty context echoed unchanged (context = {})
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with context {}
    Then the response includes context {}

  # @contextgit id=T-UC-011-ext-g-nested type=test upstream=[BR-RULE-043]
  @context-echo @partition @boundary
  Scenario: context_nested -- deeply nested context echoed unchanged (context with properties)
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with context {"deep": {"nested": {"level": 3}}, "array": [1, 2, 3]}
    Then the response includes context {"deep": {"nested": {"level": 3}}, "array": [1, 2, 3]}
    And the context is identical to what was sent

  # ============================================================
  # Sync Validation Scenarios (from gap analysis)
  # Preconditions: PRE-B3, PRE-B4, PRE-B5, PRE-B6, PRE-B7, PRE-B8, PRE-B9
  # ============================================================

  # @contextgit id=T-UC-011-sync-empty-accounts type=test upstream=[BR-UC-011]
  @sync @validation @partition @boundary
  Scenario: Sync with empty_accounts array rejected (0 accounts)
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with an empty accounts array
    Then the response is an error variant
    And the error indicates accounts array must not be empty

  # @contextgit id=T-UC-011-sync-missing-brand type=test upstream=[BR-UC-011,BR-RULE-058]
  @sync @validation @partition @boundary
  Scenario: Sync account with no_domain -- missing brand domain rejected
    # @bva brand (brand-ref): missing domain in brand-ref
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with an account that has no brand domain field
    Then the account has action "failed"
    And the per-account error indicates brand domain is required

  # @contextgit id=T-UC-011-sync-missing-operator type=test upstream=[BR-UC-011,BR-RULE-058]
  @sync @validation @partition @boundary
  Scenario: Sync account with missing operator -- operator is required
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with an account that has no operator field
    Then the account has action "failed"
    And the per-account error indicates operator is required

  # @contextgit id=T-UC-011-sync-missing-billing type=test upstream=[BR-UC-011,BR-RULE-059]
  @sync @validation @partition @boundary
  Scenario: Sync account with missing billing -- billing is required
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with an account that has no billing field
    Then the account processing fails with a validation error for billing

  # @contextgit id=T-UC-011-sync-invalid-patterns type=test upstream=[BR-UC-011,BR-RULE-058]
  @sync @validation @patterns @partition @boundary
  Scenario Outline: Sync with invalid pattern -- <field> "<value>" (<partition_name>)
    # @bva brand (brand-ref): invalid patterns -- uppercase domain, invalid brand_id_pattern
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with <field> set to "<value>"
    Then the account processing fails with a validation error for <field>

    Examples:
      | field          | value         | partition_name             |
      | brand.domain   | ACME.COM      | invalid_domain_pattern     |
      | brand.domain   | acme corp.com | invalid_domain_pattern     |
      | brand.brand_id | Dove!         | invalid_brand_id_pattern   |
      | brand.brand_id | UPPERCASE     | invalid_brand_id_pattern   |
      | operator       | NOT A DOMAIN  | invalid_domain_pattern     |

  # @contextgit id=T-UC-011-sync-accounts-bva type=test upstream=[BR-UC-011]
  @sync @validation @bva @partition @boundary
  Scenario Outline: Sync accounts array boundary -- <count> accounts (<boundary_desc>)
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with <count> accounts
    Then the response has outcome "<outcome>"

    Examples:
      | count | outcome                              | boundary_desc                      |
      | 1     | success with per-account results      | 1 account (minimum)                |
      | 1000  | success with per-account results      | 1000 accounts (maximum)            |
      | 1001  | validation error for exceeding limit  | 1001 accounts (exceeds maxItems)   |

  # ============================================================
  # Atomic Response Structure
  # Rule: BR-RULE-057 INV-1, INV-2, INV-3
  # ============================================================

  # @contextgit id=T-UC-011-atomic-success type=test upstream=[BR-RULE-057]
  @sync @atomic @partition @boundary
  Scenario: success_all_ok -- accounts present, no operation-level errors (success with 0 per-account failures)
    Given the Buyer Agent has an authenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then the response contains an accounts array
    And the response does not contain an operation-level errors array
    And the response is the success variant of oneOf

  # @contextgit id=T-UC-011-atomic-all-failed type=test upstream=[BR-RULE-057]
  @sync @atomic @partition @boundary
  Scenario: success with all per-account failures -- still success variant (success with all per-account failures)
    Given the Buyer Agent has an authenticated connection via A2A
    And the seller does not support any of the requested billing models
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then the response is a success variant with accounts array
    And all accounts have action "failed"
    And the response does not contain an operation-level errors array

  # @contextgit id=T-UC-011-atomic-error type=test upstream=[BR-RULE-057]
  @sync @atomic @error @partition @boundary
  Scenario: Error variant -- errors present, no accounts or dry_run (error with exactly 1 error)
    Given the Buyer Agent has an unauthenticated connection via A2A
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then the response contains an errors array with at least 1 error
    And the response does not contain an accounts array
    And the response does not contain a dry_run field
    And the response is the error variant of oneOf
    And the error should include "suggestion" field with remediation guidance

  # @contextgit id=T-UC-011-atomic-service-error type=test upstream=[BR-RULE-057]
  @sync @atomic @error @partition @boundary
  Scenario: error_service -- service-level failure (error with multiple errors)
    Given the Buyer Agent has an authenticated connection via A2A
    And the seller system is experiencing an internal failure
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain    | operator      | billing  |
      | acme-corp.com   | acme-corp.com | operator |
    Then the response is an error variant
    And the errors array may contain multiple errors
    And each error includes code and message
    And the error should include "suggestion" field with remediation guidance

  # @contextgit id=T-UC-011-atomic-both type=test upstream=[BR-RULE-057]
  @sync @atomic @partition @boundary
  Scenario: Schema prohibits both_present -- accounts and errors never coexist (both accounts and errors present)
    Given the sync_accounts response schema uses oneOf
    Then a response with both accounts and errors arrays is invalid
    And a response with neither_present is also invalid (neither accounts nor errors present)

  # ==========================================================
  # Cross-Cutting: Sandbox Mode Response Semantics (BR-RULE-209)
  # ==========================================================

  # @contextgit id=T-UC-011-sandbox-provision type=test upstream=[BR-UC-011-main-sync,BR-RULE-209]
  @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account provisioned via sync_accounts with sandbox flag
    Given the Buyer Agent has an authenticated connection via A2A
    And the seller declares features.sandbox equals true in capabilities
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain  | operator      | billing  | sandbox |
      | acme-corp.com | acme-corp.com | operator | true    |
    Then the response is a success variant with accounts array
    And the provisioned account should have sandbox equals true
    And the account should have a seller-assigned account_id
    And no real ad platform account should have been created
    # BR-RULE-209 INV-6: seller with features.sandbox: true supports sandbox provisioning
    # BR-RULE-209 INV-2: real ad platform calls suppressed for sandbox account

  # @contextgit id=T-UC-011-sandbox-list-filter type=test upstream=[BR-UC-011-main-list-mcp,BR-RULE-209]
  @invariant @br-rule-209 @sandbox
  Scenario: List accounts with sandbox filter returns only sandbox accounts
    Given the Buyer Agent has an authenticated connection via MCP
    And both sandbox and production accounts exist for the Buyer
    When the Buyer Agent sends a list_accounts request with sandbox equals true
    Then the response should contain "accounts" array
    And all returned accounts should have sandbox equals true
    And the response should not include production accounts
    # BR-RULE-209 INV-4: sandbox accounts identifiable via sandbox: true

  # @contextgit id=T-UC-011-sandbox-validation type=test upstream=[BR-UC-011-main-sync,BR-RULE-209]
  @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account provisioning with invalid billing returns real validation error
    Given the Buyer Agent has an authenticated connection via A2A
    And the seller declares features.sandbox equals true in capabilities
    When the Buyer Agent sends a sync_accounts request with:
      | brand.domain  | operator      | billing       | sandbox |
      | acme-corp.com | acme-corp.com | unsupported   | true    |
    Then the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-1: sandbox inputs validated same as production
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present
