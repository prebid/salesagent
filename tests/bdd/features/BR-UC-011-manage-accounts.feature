# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

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


  @T-UC-011-list-main @list @happy-path @post-s1 @post-s2 @post-s3 @partition @boundary
  Scenario: List accounts (authenticated_with_accounts)
    Given the Buyer Agent has an authenticated connection
    And the agent has 3 accessible accounts with statuses "active", "pending_approval", "suspended"
    When the Buyer Agent sends a list_accounts request
    Then the response contains an accounts array with 3 items
    And each account includes account_id, name, status, advertiser, rate_card, and payment_terms
    And the accounts are only those accessible to the authenticated agent
    # @bva accounts (response): multiple accounts visible
    # @bva authentication (account operations): valid token on list
    # POST-S1: Buyer knows which accounts are accessible
    # POST-S2: Buyer knows each account's status
    # POST-S3: Buyer knows advertiser, billing, rate card, payment terms

  @T-UC-011-list-status-filter @list @status-filter @partition @boundary
  Scenario Outline: List accounts filtered by status <status> (status_filter_match)
    Given the Buyer Agent has an authenticated connection
    And the agent has accounts with statuses "active", "pending_approval", "suspended", "closed"
    When the Buyer Agent sends a list_accounts request with status filter "<status>"
    Then the response contains only accounts with status "<status>"
    And accounts with other statuses are excluded
    # @bva status: active (first enum value), closed (last enum value)
    # @bva accounts (response): status filter = specific value with matches

    Examples:
      | status             |
      | active             |
      | pending_approval   |
      | payment_required   |
      | suspended          |
      | closed             |

  @T-UC-011-list-no-accounts @list @empty-result @partition @boundary
  Scenario: List accounts returns empty when authenticated agent has no accounts (0 accounts visible)
    Given the Buyer Agent has an authenticated connection
    And the agent has no accessible accounts
    When the Buyer Agent sends a list_accounts request
    Then the response contains an empty accounts array
    And the response is not an error
    # @bva accounts (response): 0 accounts visible

  @T-UC-011-list-unauth @list @auth @partition @boundary
  Scenario: List accounts without authentication returns auth error (no token on list)
    Given the Buyer Agent has an unauthenticated connection
    When the Buyer Agent sends a list_accounts request without an authentication token
    Then the response is an error variant with no accounts array
    And the error code is "AUTH_TOKEN_INVALID"
    And the error message describes the authentication requirement
    # @bva authentication (account operations): no token on list

  # ── Hand-authored: authorization boundary scenarios (PR #1170 review) ──

  @T-UC-011-list-cross-agent @list @auth @security @hand-authored
  Scenario: List accounts returns only the authenticated agent's accounts
    Given agent "agent-A" has an authenticated connection with 2 accessible accounts
    And agent "agent-B" has 3 accessible accounts in the same tenant
    When agent "agent-A" sends a list_accounts request
    Then the response contains an accounts array with 2 items
    And none of the returned accounts belong to agent "agent-B"
    # Security: cross-agent isolation — agent A must not see agent B's accounts

  @T-UC-011-list-no-principal @list @auth @security @hand-authored
  Scenario: List accounts with valid tenant but missing principal_id returns auth error
    Given the Buyer Agent has a connection with tenant resolved but no principal_id
    When the Buyer Agent sends a list_accounts request with no principal_id
    Then the response is an error variant with no accounts array
    And the error code is "AUTH_TOKEN_INVALID"
    # Security: identity with tenant_id but missing principal_id must be rejected

  @T-UC-011-sync-cross-agent @sync @auth @security @hand-authored
  Scenario: Sync accounts are scoped to the authenticated agent
    Given agent "agent-A" has an authenticated connection
    And agent "agent-A" previously synced account for brand domain "a-brand.com"
    And agent "agent-B" previously synced account for brand domain "b-brand.com"
    When agent "agent-A" sends a list_accounts request
    Then none of the returned accounts have brand domain "b-brand.com"
    # Security: agent A cannot see agent B's accounts via list_accounts

  @T-UC-011-list-pagination @list @pagination @post-s4
  Scenario: List accounts with pagination
    Given the Buyer Agent has an authenticated connection
    And the agent has 120 accessible accounts
    When the Buyer Agent sends a list_accounts request with max_results 50
    Then the response contains 50 accounts
    And the response includes pagination metadata with has_more true and a cursor
    When the Buyer Agent sends a list_accounts request with the returned cursor
    Then the response contains 50 more accounts
    And the response includes pagination metadata with has_more true
    # POST-S4: Buyer can paginate through accounts

  @T-UC-011-list-status-filter-no-match @list @status-filter @empty-result @partition @boundary
  Scenario: List accounts with status filter returns empty when no matches (status filter = specific value with no matches)
    Given the Buyer Agent has an authenticated connection
    And the agent has accounts with statuses "active", "active", "active"
    When the Buyer Agent sends a list_accounts request with status filter "suspended"
    Then the response contains an empty accounts array
    And the response is not an error
    # @bva accounts (response): status filter = specific value with no matches

  @T-UC-011-list-invalid-status @list @validation @partition @boundary
  Scenario: List accounts with unknown status value not in enum
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a list_accounts request with status filter "unknown_status"
    Then the response contains a validation error
    And the error indicates the status value is not recognized
    # @bva status: Unknown string not in enum

  @T-UC-011-list-pagination-bva @list @pagination @bva @partition @boundary
  Scenario Outline: List accounts pagination boundary - max_results <value>
    Given the Buyer Agent has an authenticated connection
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

  @T-UC-011-list-malformed-cursor @list @pagination @boundary
  Scenario: List accounts with malformed pagination cursor returns first page
    Given the Buyer Agent has an authenticated connection
    And the agent has 5 accessible accounts
    When the Buyer Agent sends a list_accounts request with cursor "not-valid-base64!"
    Then the response returns accounts starting from the first page
    And the response contains 5 accounts
    # @bva pagination cursor: malformed base64 string falls back to offset 0

  @T-UC-011-list-service-error @list @error @hand-authored
  Scenario: List accounts returns error on service failure
    Given the Buyer Agent has an authenticated connection
    And the database is experiencing a transient failure
    When the Buyer Agent sends a list_accounts request
    Then the response is an error variant
    # Edge case: service-level DB failure propagates as error, not empty result

  @T-UC-011-list-status-all @list @status-filter @partition @boundary
  Scenario: List accounts with no status filter returns all statuses (status filter = 'all')
    Given the Buyer Agent has an authenticated connection
    And the agent has accounts with statuses "active", "pending_approval", "suspended", "closed"
    When the Buyer Agent sends a list_accounts request without a status filter
    Then the response contains accounts with all statuses
    And the result set is identical to requesting without any filter
    # @bva accounts (response): status filter = 'all'

  @T-UC-011-sync-create @sync @happy-path @post-s5 @post-s6 @partition @boundary
  Scenario: Sync new account -- single_brand_domain, all_created (1 account, all same action)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator        | billing  |
    | acme-corp.com   | acme-corp.com   | operator |
    Then the response is a success variant with accounts array
    And the account for brand domain "acme-corp.com" has action "created"
    And the account has a seller-assigned account_id
    And the account has status "active"
    And the response includes brand domain "acme-corp.com" echoed from request
    # @bva authentication (account operations): valid token on sync
    # @bva accounts (sync operation): 1 account (minimum)
    # @bva accounts (sync operation): all same action
    # @bva brand (brand-ref): single_brand_domain -- brand with domain only (single brand)
    # POST-S5: Buyer knows the seller-assigned account_id
    # POST-S6: Buyer knows the action taken per account

  @T-UC-011-sync-multi-brand @sync @brand-identity @partition @boundary
  Scenario: Sync multi_brand_domain with brand_id and operator (brand with domain + brand_id)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | brand.brand_id | operator        | billing  |
    | nova-brands.com | spark          | pinnacle-media.com | operator |
    | nova-brands.com | glow           | pinnacle-media.com | agent    |
    Then the response contains 2 account results
    And the account for brand domain "nova-brands.com" brand_id "spark" has action "created"
    And the account for brand domain "nova-brands.com" brand_id "glow" has action "created"
    And each account echoes brand domain and brand_id from the request
    # @bva brand (brand-ref): multi_brand_domain -- brand with domain + brand_id (multi brand)
    # @bva brand (brand-ref): brand with domain + brand_id + operator

  @T-UC-011-sync-brand-direct @sync @brand-identity @partition @boundary
  Scenario: Sync brand_direct -- brand operating own seat (operator is brand's domain)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator        | billing  |
    | acme-corp.com   | acme-corp.com   | operator |
    Then the account for brand domain "acme-corp.com" has action "created"
    And the account operator is "acme-corp.com"
    And the account billing is "operator"
    # @bva brand (brand-ref): brand_direct -- brand operating own seat

  @T-UC-011-sync-update @sync @upsert @partition
  Scenario: Sync updates existing account -- all_updated
    Given the Buyer Agent has an authenticated connection
    And an account for brand domain "acme-corp.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing |
    | acme-corp.com   | acme-corp.com | agent   |
    Then the account for brand domain "acme-corp.com" has action "updated"
    And the account billing is "agent"

  @T-UC-011-sync-unchanged @sync @upsert @partition
  Scenario: Sync unchanged account is idempotent -- all_unchanged
    Given the Buyer Agent has an authenticated connection
    And an account for brand domain "acme-corp.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the account for brand domain "acme-corp.com" has action "unchanged"

  @T-UC-011-sync-billing-enum @sync @billing @post-s7 @partition @boundary
  Scenario Outline: Sync with billing model <billing> -- <partition_name>
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing   |
    | acme-corp.com   | acme-corp.com | <billing> |
    Then the account billing is "<billing>"
    # @bva billing: operator (first enum value), agent (last enum value)
    # POST-S7: Buyer knows billing model for each account

    Examples:
      | billing  | partition_name   | boundary_point                |
      | operator | operator_honored | billing = operator            |
      | agent    | agent_honored    | billing = agent               |

  @T-UC-011-sync-mixed @sync @upsert @partition
  Scenario: Sync mixed_results -- created and updated in same request (all different actions)
    Given the Buyer Agent has an authenticated connection
    And an account for brand domain "existing-brand.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain        | operator            | billing  |
    | new-brand.com       | new-brand.com       | operator |
    | existing-brand.com  | existing-brand.com  | agent    |
    Then the account for brand domain "new-brand.com" has action "created"
    And the account for brand domain "existing-brand.com" has action "updated"

  @T-UC-011-sync-brand-echo @sync @invariant @partition
  Scenario: Sync echoes brand from request in per-account result (brand echo)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | brand.brand_id | operator        | billing  |
    | nova-brands.com | spark          | pinnacle-media.com | operator |
    Then the per-account result echoes brand domain "nova-brands.com" and brand_id "spark"
    # BR-RULE-056 INV-4: Request includes brand for an account -> response echoes same brand value
    # BR-RULE-058 INV-3: Account is processed -> response echoes brand (brand-ref) from request

  @T-UC-011-sync-shortest-domain @sync @brand-identity @partition @boundary
  Scenario: Sync with shortest valid domain (e.g., 'a.b')
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain | operator | billing  |
    | a.b          | a.b      | operator |
    Then the account for brand domain "a.b" has action "created"
    # @bva brand (brand-ref): shortest valid domain (e.g., 'a.b')

  @T-UC-011-ext-a-no-token @sync @ext-a @auth @error @post-f1 @post-f2 @partition @boundary
  Scenario: Sync without authentication -- sync_no_token returns error_auth (no token on sync)
    Given the Buyer Agent has an unauthenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is an error variant with no accounts array
    And the error code is "AUTH_TOKEN_INVALID"
    And the error message describes the authentication requirement
    And the error should include "suggestion" field with remediation guidance
    And no accounts were modified on the seller
    # @bva authentication (account operations): no token on sync
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows what failed

  @T-UC-011-ext-a-expired @sync @ext-a @auth @error @partition @boundary
  Scenario: Sync with expired token -- sync_invalid_token returns AUTH_TOKEN_INVALID (invalid token on sync)
    Given the Buyer Agent has an A2A connection with an expired token
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is an error variant
    And the error code is "AUTH_TOKEN_INVALID"
    And the error should include "suggestion" field with remediation guidance
    # @bva authentication (account operations): invalid token on sync

  @T-UC-011-sync-no-principal @sync @auth @security @hand-authored
  Scenario: Sync accounts with valid tenant but missing principal_id returns auth error
    Given the Buyer Agent has a connection with tenant resolved but no principal_id
    When the Buyer Agent sends a sync_accounts request with no principal_id and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is an error variant with no accounts array
    And the error code is "AUTH_TOKEN_INVALID"
    # Security: parity with list_accounts no-principal guard

  @T-UC-011-list-expired @list @auth @hand-authored
  Scenario: List accounts with expired token returns auth error
    Given the Buyer Agent has an A2A connection with an expired token
    When the Buyer Agent sends a list_accounts request without an authentication token
    Then the response is an error variant with no accounts array
    And the error code is "AUTH_TOKEN_INVALID"
    # Auth parity: list mirrors sync expired-token behavior

  @T-UC-011-ext-b-partial @sync @partial-failure @invariant @partition @boundary
  Scenario: Sync partial_failure -- success_partial_failure with action=failed (action=failed with errors)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain        | operator            | billing  |
    | acme-corp.com       | acme-corp.com       | operator |
    | invalid-brand.test  | invalid-brand.test  | operator |
    Then the response is a success variant with accounts array
    And the account for brand domain "acme-corp.com" has action "created"
    And the account for brand domain "invalid-brand.test" has action "failed"
    And the failed account includes a per-account errors array
    And the response does not contain an operation-level errors field

  @T-UC-011-ext-c-rejected @sync @ext-c @billing @error @partition @boundary
  Scenario: Seller rejects unsupported billing -- billing_rejected (billing = unsupported value for seller)
    Given the Buyer Agent has an authenticated connection
    And the seller does not support "operator" billing
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the account for brand domain "acme-corp.com" has action "failed"
    And the account has status "rejected"
    And the per-account errors array contains an error with code "BILLING_NOT_SUPPORTED"
    And the error message explains the billing model is not available
    And the error should include "suggestion" field with remediation guidance
    # @bva billing: billing = unsupported value for seller
    # BR-RULE-059 INV-2: Request includes billing model the seller does not support -> action=failed, status=rejected, BILLING_NOT_SUPPORTED
    # POST-F2: Buyer knows what failed and the specific error code

  @T-UC-011-ext-c-mixed @sync @ext-c @billing @partial-failure @partition
  Scenario: Billing rejection is per-account -- other accounts still succeed
    Given the Buyer Agent has an authenticated connection
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
    # BR-RULE-059 INV-2 + BR-RULE-057 INV-1: rejected billing produces per-account failure within success variant

  @T-UC-011-ext-c-invalid-enum @sync @billing @validation @partition @boundary
  Scenario: Billing value not in enum -- invalid_billing_value (billing = invalid string)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | prepaid  |
    Then the account processing fails with a validation error for billing
    # @bva billing: billing = invalid string

  @T-UC-011-ext-d-pending-url @sync @approval @post-s8 @partition @boundary
  Scenario: Account pending_with_url -- setup with url + message + expires_at (status = pending_approval with setup)
    Given the Buyer Agent has an authenticated connection
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

  @T-UC-011-ext-d-pending-message @sync @approval @partition @boundary
  Scenario: Account pending_message_only -- setup with message only
    Given the Buyer Agent has an authenticated connection
    And the seller requires legal review for new accounts
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the account has status "pending_approval"
    And the setup object includes a message
    And the setup object does not include a URL

  @T-UC-011-ext-d-active @sync @approval @partition @boundary
  Scenario: Account immediately active -- active_no_setup (status = active (no setup))
    Given the Buyer Agent has an authenticated connection
    And the seller auto-approves new accounts
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the account has status "active"
    And the account does not include a setup object

  @T-UC-011-ext-d-push @sync @push-notification @partition
  Scenario: Push notification for async status changes -- with_push_notification
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    And the request includes a push_notification_config with url "https://agent.com/webhooks"
    Then the system registers the webhook for async account status notifications
    And when the account transitions from "pending_approval" to "active"
    Then a push notification is sent to "https://agent.com/webhooks"

  @T-UC-011-ext-e-preview @sync @dry-run @post-s10 @partition @boundary
  Scenario: dry_run_true returns preview -- success_dry_run (dry_run = true)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with dry_run true and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is a success variant
    And the response includes dry_run true
    And the account for brand domain "acme-corp.com" shows action "created"
    And no accounts were actually created or modified on the seller
    # POST-S10: Buyer receives dry-run preview

  @T-UC-011-ext-e-normal @sync @dry-run @partition @boundary
  Scenario: dry_run_false -- normal sync applies changes (dry_run = false)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with dry_run false and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response does not include a dry_run field
    And the account was actually created on the seller

  @T-UC-011-ext-e-omitted @sync @dry-run @partition @boundary
  Scenario: dry_run_omitted -- default behavior applies changes (dry_run omitted)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response does not include a dry_run field
    And the account was actually created on the seller

  @T-UC-011-ext-f-deactivate @sync @delete-missing @post-s9 @partition @boundary
  Scenario: delete_missing_true deactivates absent accounts (delete_missing = true with absent accounts)
    Given the Buyer Agent has an authenticated connection
    And the agent previously synced accounts for brand domain "acme-corp.com" and "old-brand.com"
    When the Buyer Agent sends a sync_accounts request with delete_missing true and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response includes a result for brand domain "old-brand.com" showing deactivation
    And the account for brand domain "acme-corp.com" has action "unchanged" or "updated"
    # POST-S9: Buyer knows which accounts were deactivated

  @T-UC-011-ext-f-scoped @sync @delete-missing @agent-scoped
  Scenario: Delete missing scoped to authenticated agent only
    Given the Buyer Agent has an authenticated connection
    And agent A previously synced accounts for brand domain "brand-a.com"
    And agent B previously synced accounts for brand domain "brand-b.com"
    When agent A sends a sync_accounts request with delete_missing true and:
    | brand.domain    | operator      | billing  |
    | brand-a.com     | brand-a.com   | operator |
    Then agent B's account for brand domain "brand-b.com" is not affected
    And only agent A's absent accounts are deactivated

  @T-UC-011-ext-f-false @sync @delete-missing @partition @boundary
  Scenario: delete_missing_false preserves absent accounts (delete_missing = false with absent accounts)
    Given the Buyer Agent has an authenticated connection
    And the agent previously synced accounts for brand domain "acme-corp.com" and "old-brand.com"
    When the Buyer Agent sends a sync_accounts request with delete_missing false and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then brand domain "old-brand.com" remains in its current state
    And only the included accounts are processed

  @T-UC-011-ext-f-none-absent @sync @delete-missing @partition @boundary
  Scenario: delete_missing_none_absent -- true with no absent accounts (delete_missing = true with no absent accounts)
    Given the Buyer Agent has an authenticated connection
    And the agent previously synced accounts for brand domain "acme-corp.com" only
    When the Buyer Agent sends a sync_accounts request with delete_missing true and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then no accounts are deactivated
    And the account for brand domain "acme-corp.com" is processed normally

  @T-UC-011-ext-f-omitted @sync @delete-missing @partition @boundary
  Scenario: delete_missing_omitted -- default preserves accounts (delete_missing omitted)
    Given the Buyer Agent has an authenticated connection
    And the agent previously synced accounts for brand domain "acme-corp.com" and "old-brand.com"
    When the Buyer Agent sends a sync_accounts request without delete_missing and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then brand domain "old-brand.com" remains in its current state
    And only the included accounts are processed

  # ── Hand-authored: delete_missing semantics (coverage gap analysis) ──

  @T-UC-011-dryrun-delete-missing @sync @dry-run @delete-missing @hand-authored
  Scenario: dry_run=true suppresses delete_missing — no deactivation preview
    Given the Buyer Agent has an authenticated connection
    And the agent previously synced accounts for brand domain "acme-corp.com" and "old-brand.com"
    When the Buyer Agent sends a sync_accounts request with dry_run true and delete_missing true and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response includes dry_run true
    And the response does not include a result for brand domain "old-brand.com"
    # Documents: dry_run suppresses delete_missing entirely — no preview of closures

  @T-UC-011-delete-missing-granted-access @sync @delete-missing @security @hand-authored
  Scenario: delete_missing does not close accounts the agent was granted access to
    Given agent "agent-A" has an authenticated connection
    And agent "agent-B" created account for brand domain "b-brand.com"
    And agent "agent-A" was granted access to the account for brand domain "b-brand.com"
    And agent "agent-A" previously synced account for brand domain "a-brand.com"
    When agent "agent-A" sends a sync_accounts request with delete_missing true and:
    | brand.domain  | operator      | billing  |
    | a-brand.com   | a-brand.com   | operator |
    Then agent B's account for brand domain "b-brand.com" is not affected
    # Documents: delete_missing scopes by creator (principal_id), not by access grant

  @T-UC-011-delete-missing-own-only @sync @delete-missing @hand-authored
  Scenario: delete_missing only closes accounts the agent created
    Given the Buyer Agent has an authenticated connection
    And the agent previously synced accounts for brand domain "keep.com" and "drop.com"
    When the Buyer Agent sends a sync_accounts request with delete_missing true and:
    | brand.domain | operator   | billing  |
    | keep.com     | keep.com   | operator |
    Then the response includes a result for brand domain "drop.com" showing deactivation
    And the account for brand domain "keep.com" has action "unchanged" or "updated"

  @T-UC-011-ext-g-echo @context-echo @post-f3 @partition @boundary
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

  @T-UC-011-ext-g-echo-error @context-echo @error @post-f3
  Scenario: Context echoed in sync error response
    Given the Buyer Agent has an unauthenticated connection
    When the Buyer Agent sends a sync_accounts request with context {"trace": "err-001"}
    Then the response is an error variant with AUTH_TOKEN_INVALID
    And the response includes context {"trace": "err-001"}
    And the error should include "suggestion" field with remediation guidance
    # POST-F3: Context echoed even on error path

  @T-UC-011-ext-g-absent @context-echo @partition @boundary
  Scenario: context_absent -- context omitted from response (context absent)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a list_accounts request without a context object
    Then the response does not include a context field

  @T-UC-011-ext-g-empty @context-echo @partition @boundary
  Scenario: context_empty_object -- empty context echoed unchanged (context = {})
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with context {}
    Then the response includes context {}

  @T-UC-011-ext-g-nested @context-echo @partition @boundary
  Scenario: context_nested -- deeply nested context echoed unchanged (context with properties)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with context {"deep": {"nested": {"level": 3}}, "array": [1, 2, 3]}
    Then the response includes context {"deep": {"nested": {"level": 3}}, "array": [1, 2, 3]}
    And the context is identical to what was sent

  @T-UC-011-sync-empty-accounts @sync @validation @partition @boundary
  Scenario: Sync with empty_accounts array rejected (0 accounts)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with an empty accounts array
    Then the response is an error variant
    And the error indicates accounts array must not be empty

  @T-UC-011-sync-missing-brand @sync @validation @partition @boundary
  Scenario: Sync account with no_domain -- missing brand domain rejected
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with an account that has no brand domain field
    Then the account has action "failed"
    And the per-account error indicates brand domain is required
    # @bva brand (brand-ref): missing domain in brand-ref

  @T-UC-011-sync-missing-operator @sync @validation @partition @boundary
  Scenario: Sync account with missing operator -- operator is required
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with an account that has no operator field
    Then the account has action "failed"
    And the per-account error indicates operator is required

  @T-UC-011-sync-missing-billing @sync @validation @partition @boundary
  Scenario: Sync account with missing billing -- billing is required
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with an account that has no billing field
    Then the account processing fails with a validation error for billing

  @T-UC-011-sync-invalid-patterns @sync @validation @patterns @partition @boundary
  Scenario Outline: Sync with invalid pattern -- <field> "<value>" (<partition_name>)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with <field> set to "<value>"
    Then the account processing fails with a validation error for <field>
    # @bva brand (brand-ref): invalid patterns -- uppercase domain, invalid brand_id_pattern

    Examples:
      | field          | value         | partition_name             |
      | brand.domain   | ACME.COM      | invalid_domain_pattern     |
      | brand.domain   | acme corp.com | invalid_domain_pattern     |
      | brand.brand_id | Dove!         | invalid_brand_id_pattern   |
      | brand.brand_id | UPPERCASE     | invalid_brand_id_pattern   |
      | operator       | NOT A DOMAIN  | invalid_domain_pattern     |

  @T-UC-011-sync-accounts-bva @sync @validation @bva @partition @boundary
  Scenario Outline: Sync accounts array boundary -- <count> accounts (<boundary_desc>)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with <count> accounts
    Then the response has outcome "<outcome>"

    Examples:
      | count | outcome                              | boundary_desc                      |
      | 1     | success with per-account results      | 1 account (minimum)                |
      | 1000  | success with per-account results      | 1000 accounts (maximum)            |
      | 1001  | validation error for exceeding limit  | 1001 accounts (exceeds maxItems)   |

  @T-UC-011-atomic-success @sync @atomic @partition @boundary
  Scenario: success_all_ok -- accounts present, no operation-level errors (success with 0 per-account failures)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response contains an accounts array
    And the response does not contain an operation-level errors array
    And the response is the success variant of oneOf

  @T-UC-011-atomic-all-failed @sync @atomic @partition @boundary
  Scenario: success with all per-account failures -- still success variant (success with all per-account failures)
    Given the Buyer Agent has an authenticated connection
    And the seller does not support any of the requested billing models
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is a success variant with accounts array
    And all accounts have action "failed"
    And the response does not contain an operation-level errors array

  @T-UC-011-atomic-error @sync @atomic @error @partition @boundary
  Scenario: Error variant -- errors present, no accounts or dry_run (error with exactly 1 error)
    Given the Buyer Agent has an unauthenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response contains an errors array with at least 1 error
    And the response does not contain an accounts array
    And the response does not contain a dry_run field
    And the response is the error variant of oneOf
    And the error should include "suggestion" field with remediation guidance

  @T-UC-011-atomic-service-error @sync @atomic @error @partition @boundary
  Scenario: error_service -- service-level failure (error with multiple errors)
    Given the Buyer Agent has an authenticated connection
    And the seller system is experiencing an internal failure
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is an error variant
    And the errors array may contain multiple errors
    And each error includes code and message
    And the error should include "suggestion" field with remediation guidance

  @T-UC-011-atomic-both @sync @atomic @partition @boundary
  Scenario: Schema prohibits both_present -- accounts and errors never coexist (both accounts and errors present)
    Given the sync_accounts response schema uses oneOf
    Then a response with both accounts and errors arrays is invalid
    And a response with neither_present is also invalid (neither accounts nor errors present)

  @T-UC-011-sandbox-provision @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account provisioned via sync_accounts with sandbox flag
    Given the Buyer Agent has an authenticated connection
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

  @T-UC-011-sandbox-list-filter @invariant @br-rule-209 @sandbox
  Scenario: List accounts with sandbox filter returns only sandbox accounts
    Given the Buyer Agent has an authenticated connection
    And both sandbox and production accounts exist for the Buyer
    When the Buyer Agent sends a list_accounts request with sandbox equals true
    Then the response should contain "accounts" array
    And all returned accounts should have sandbox equals true
    And the response should not include production accounts
    # BR-RULE-209 INV-4: sandbox accounts identifiable via sandbox: true

  @T-UC-011-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account provisioning with invalid billing returns real validation error
    Given the Buyer Agent has an authenticated connection
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

  @T-UC-011-sync-update-billing @sync @upsert @partition
  Scenario: Sync update billing on existing account
    Given the Buyer Agent has an authenticated connection
    And an account for brand domain "acme-corp.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing |
    | acme-corp.com   | acme-corp.com | agent   |
    Then the account for brand domain "acme-corp.com" has action "updated"
    And the account billing is "agent"

  @T-UC-011-sync-update-payment-terms @sync @upsert @partition
  Scenario: Sync update payment_terms on existing account
    Given the Buyer Agent has an authenticated connection
    And an account for brand domain "acme-corp.com" already exists with payment_terms "net_30"
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  | payment_terms |
    | acme-corp.com   | acme-corp.com | operator | net_60        |
    Then the account for brand domain "acme-corp.com" has action "updated"

  @T-UC-011-sync-governance @sync @governance @partition
  Scenario: Sync new account with governance_agents stores data correctly
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with governance_agents for brand "governed.com"
    Then the account for brand domain "governed.com" has action "created"
    And the governance_agents are stored for brand domain "governed.com"

  # ── Hand-authored: implementation fidelity (PR #1170 review) ──

  @T-UC-011-sync-governance-unchanged @sync @governance @idempotent @hand-authored
  Scenario: Sync unchanged governance_agents is idempotent
    Given the Buyer Agent has an authenticated connection
    And an account for brand domain "governed.com" already exists with governance_agents
    When the Buyer Agent re-syncs with identical governance_agents for brand "governed.com"
    Then the account for brand domain "governed.com" has action "unchanged"
    # Regression: catches model-vs-dict comparison bug in change detection

  @T-UC-011-sync-governance-update @sync @governance @hand-authored
  Scenario: Sync with modified governance_agents detects the change
    Given the Buyer Agent has an authenticated connection
    And an account for brand domain "governed.com" already exists with governance_agents
    When the Buyer Agent sends a sync with different governance_agents for brand "governed.com"
    Then the account for brand domain "governed.com" has action "updated"

  @T-UC-011-sync-unchanged-all-fields @sync @idempotent @hand-authored
  Scenario: Sync with all fields identical reports unchanged (full idempotency)
    Given the Buyer Agent has an authenticated connection
    And an account for brand domain "full.com" exists with billing "agent", payment_terms "net_30", and governance_agents
    When the Buyer Agent re-syncs with identical billing, payment_terms, and governance_agents for brand "full.com"
    Then the account for brand domain "full.com" has action "unchanged"
    # Regression: change detection must work across ALL field types

  @T-UC-011-sync-unchanged-full @sync @upsert @partition
  Scenario: Sync existing account with identical values is unchanged
    Given the Buyer Agent has an authenticated connection
    And an account for brand domain "stable.com" already exists with billing "agent" and payment_terms "net_30"
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain | operator  | billing | payment_terms |
    | stable.com   | stable.com | agent   | net_30        |
    Then the account for brand domain "stable.com" has action "unchanged"

  # ── Hand-authored: field preservation + access persistence invariants ──

  @T-UC-011-sync-immutable-preserved @sync @upsert @invariant @hand-authored
  Scenario: Sync update preserves immutable fields (name, advertiser, rate_card)
    Given the Buyer Agent has an authenticated connection
    And an account for brand domain "acme-corp.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing |
    | acme-corp.com   | acme-corp.com | agent   |
    Then the account for brand domain "acme-corp.com" has action "updated"
    And the account name in the database is unchanged from the original
    And the account rate_card in the database is unchanged from the original

  @T-UC-011-sync-no-dup-access @sync @invariant @hand-authored
  Scenario: Re-syncing an existing account does not duplicate access grants
    Given the Buyer Agent has an authenticated connection
    And an account for brand domain "resync.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain | operator    | billing |
    | resync.com   | resync.com  | agent   |
    Then the account for brand domain "resync.com" has action "updated"
    And the agent has exactly one access grant for brand domain "resync.com"

  @T-UC-011-sync-then-list @sync @list @invariant @hand-authored
  Scenario: Newly synced account appears in list_accounts
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator        | billing  |
    | new-brand.com   | new-brand.com   | operator |
    Then the account for brand domain "new-brand.com" has action "created"
    When the Buyer Agent sends a list_accounts request
    Then the list includes an account with brand domain "new-brand.com"

  @T-UC-011-dryrun-update @sync @dry-run @upsert @partition
  Scenario: Dry-run detects billing change on existing account
    Given the Buyer Agent has an authenticated connection
    And an account for brand domain "preview.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with dry_run true and:
    | brand.domain | operator    | billing |
    | preview.com  | preview.com | agent   |
    Then the response is a success variant
    And the response includes dry_run true
    And the account for brand domain "preview.com" has action "updated"
    And no accounts were actually modified for brand domain "preview.com"

  @T-UC-011-dryrun-unchanged @sync @dry-run @upsert @partition
  Scenario: Dry-run with no changes reports unchanged
    Given the Buyer Agent has an authenticated connection
    And an account for brand domain "steady.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with dry_run true and:
    | brand.domain | operator   | billing  |
    | steady.com   | steady.com | operator |
    Then the response is a success variant
    And the response includes dry_run true
    And the account for brand domain "steady.com" has action "unchanged"

