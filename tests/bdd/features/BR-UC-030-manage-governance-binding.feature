# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

Feature: BR-UC-030 Manage Governance Binding
  As a Buyer
  I want to bind a single governance agent per account so that the Seller Agent can validate my planned media buys outbound during the campaign lifecycle
  So that governance approvals (purchase, modification, delivery) are routed through the agent I designate and lifecycle continuity is preserved via a signed governance_context

  # Postconditions verified:
  #   POST-S1: Buyer knows the seller persisted the governance agent binding per account
  #   POST-S2: Buyer receives the echoed governance agent URL per account (credentials NOT echoed)
  #   POST-S3: Idempotent replay of sync_governance returns the prior result
  #   POST-S4: adcp_version echoed on every response
  #   POST-S5: Subsequent media-buy lifecycle events route through the bound governance agent
  #   POST-S6: Seller obtains a governance decision (approved/conditions/denied) OR a transport/operation-level governance failure (not a status value)
  #   POST-S7: Seller persists and forwards governance_context (JWS) unchanged
  #   POST-S8: Seller knows expires_at on approved/conditions decisions
  #   POST-S9: On conditions, seller applies adjustments and re-checks
  #   POST-S10: On denied, seller surfaces findings and does not proceed
  #   POST-S11: next_check schedules ongoing delivery reporting
  #   POST-F1: System state unchanged on operation-level failure
  #   POST-F2: Buyer/seller knows what failed and the specific error code
  #   POST-F3: Application context echoed when possible
  #
  # Extensions: A (GOVERNANCE_DENIED), B (SYNC_PARTIAL_FAILURE)
  # Error codes: AUTH_REQUIRED, ACCOUNT_NOT_FOUND, SCOPE_INSUFFICIENT,
  #   PERMISSION_DENIED, GOVERNANCE_DENIED (semantic), GOVERNANCE_UNAVAILABLE,
  #   URL_NOT_HTTPS, CREDENTIALS_TOO_SHORT, GOVERNANCE_AGENTS_CARDINALITY,
  #   IDEMPOTENCY_KEY_REQUIRED, IDEMPOTENCY_KEY_INVALID, IDEMPOTENCY_CONFLICT,
  #   INVALID_REQUEST
  #
  # v3.1 BR-formalization coverage (BR-RULE-241..255): idempotency conflict,
  #   per-account PERMISSION_DENIED, check addressing (plan_id/caller),
  #   shape inference (budget-availability probe), governance-failure fail-closed
  #   (GOVERNANCE_UNAVAILABLE), JWS verification failure (PERMISSION_DENIED),
  #   opaque findings taxonomy, per-account replace scope.

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context


  @T-UC-030-sync-happy @sync @happy-path @post-s1 @post-s2 @post-s5
  Scenario Outline: Sync governance binding via <transport> (single account, fresh binding)
    Given the Buyer Agent has an authenticated connection
    And the agent has authority over account "acct-social-001"
    And no governance agent is currently bound to "acct-social-001"
    When the Buyer Agent sends a sync_governance request via <transport> with idempotency_key "uuid-v4-fresh-0000000000000001" and one account "acct-social-001" bound to governance agent "https://governance.pinnacle-media.com" with Bearer credentials of length 64
    Then the response variant is success and carries an accounts array with 1 item
    And the account "acct-social-001" has status "synced"
    And the response account "acct-social-001" echoes governance_agents[0].url "https://governance.pinnacle-media.com"
    And the response account "acct-social-001" does NOT echo governance_agents[0].authentication
    And the response carries an echoed adcp_version envelope
    # POST-S1, POST-S2, POST-S4, POST-S5
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-030-sync-replace @sync @replace-semantics @post-s1
  Scenario: Sync governance replaces a previously bound agent on the same account
    Given the Buyer Agent has an authenticated connection
    And the agent has authority over account "acct-social-001"
    And account "acct-social-001" is currently bound to governance agent "https://governance.old-buyer.com"
    When the Buyer Agent sends a sync_governance request via MCP with idempotency_key "uuid-v4-replace-000000000000002" and one account "acct-social-001" bound to governance agent "https://governance.new-buyer.com" with Bearer credentials of length 64
    Then the account "acct-social-001" has status "synced"
    And the persisted governance agent on "acct-social-001" is "https://governance.new-buyer.com"
    And the previous binding to "https://governance.old-buyer.com" is no longer present
    # Replace semantics, BR-1
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-sync-idempotent-replay @sync @idempotency @post-s3
  Scenario: Idempotent replay of sync_governance returns prior result without re-firing side effects
    Given the Buyer Agent has an authenticated connection
    And the agent has authority over account "acct-social-001"
    And the Buyer Agent previously sent a sync_governance request with idempotency_key "uuid-v4-replay-0000000000000003" that synced "acct-social-001" to "https://governance.pinnacle-media.com"
    When the Buyer Agent sends the same sync_governance request with idempotency_key "uuid-v4-replay-0000000000000003" again
    Then the response matches the prior sync result for "acct-social-001"
    And no additional audit events are emitted
    And no reapproval flows are triggered on existing plans
    # POST-S3, BR-4

  @T-UC-030-sync-idempotency-key-missing @sync @validation @partition @boundary
  Scenario: sync_governance without idempotency_key is rejected
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_governance request via MCP without an idempotency_key and one account "acct-social-001"
    Then the response variant is error
    And the error code indicates the missing idempotency_key
    # PRE-B1
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-sync-idempotency-key-too-short @sync @validation @partition @boundary
  Scenario Outline: sync_governance idempotency_key boundary <case>
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_governance request via MCP with idempotency_key "<key>" and one account "acct-social-001"
    Then the response outcome is "<outcome>"
    # PRE-B1 — pattern ^[A-Za-z0-9_.:-]{16,255}$
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

    Examples:
      | case                    | key                                                                                                                                                                                                                                                | outcome  |
      | min length 16 ok        | abcdef1234567890                                                                                                                                                                                                                                   | accepted |
      | one below min (15)      | abcdef123456789                                                                                                                                                                                                                                    | rejected |
      | max length 255 ok       | abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopq.0123456 | accepted |
      | invalid char (space)    | abcdef 1234567890                                                                                                                                                                                                                                  | rejected |

  @T-UC-030-sync-url-not-https @sync @validation @partition @boundary
  Scenario: sync_governance with non-https governance agent URL is rejected
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_governance request via MCP with idempotency_key "uuid-v4-httpscheck-00000000007" and account "acct-social-001" bound to governance agent "http://governance.pinnacle-media.com" with Bearer credentials of length 64
    Then the response variant is error
    And the error references the url field and indicates https is required
    # PRE-B5, BR-5
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-sync-credentials-too-short @sync @validation @partition @boundary
  Scenario: sync_governance with credentials below 32 chars is rejected
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_governance request via MCP with idempotency_key "uuid-v4-credlen-00000000000008" and account "acct-social-001" bound to governance agent "https://governance.pinnacle-media.com" with Bearer credentials "short-creds"
    Then the response variant is error
    And the error references the credentials field
    # PRE-B7, BR-6
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-sync-multiple-agents-rejected @sync @validation @partition @boundary @cardinality
  Scenario: sync_governance with more than one governance_agents entry per account is rejected (maxItems 1)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_governance request via MCP with idempotency_key "uuid-v4-maxitems-0000000000009" and account "acct-social-001" bound to TWO governance agents "https://governance.pinnacle-media.com" and "https://governance.acme-buyer.com"
    Then the response variant is error
    And the error references the governance_agents cardinality
    # PRE-B4, BR-2
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-sync-zero-agents-rejected @sync @validation @partition @boundary @cardinality
  Scenario: sync_governance with empty governance_agents array is rejected (minItems 1)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_governance request via MCP with idempotency_key "uuid-v4-minitems-0000000000010" and account "acct-social-001" with an empty governance_agents array
    Then the response variant is error
    And the error references the governance_agents cardinality
    # PRE-B4, BR-2
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-sync-accounts-max @sync @validation @partition @boundary
  Scenario: sync_governance with more than 100 accounts is rejected (maxItems 100)
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_governance request via MCP with idempotency_key "uuid-v4-acctsmax-0000000000011" and 101 accounts each bound to "https://governance.pinnacle-media.com"
    Then the response variant is error
    And the error references the accounts array size
    # PRE-B2
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-sync-implicit-account @sync @happy-path @account-ref @partition
  Scenario: Sync governance to an implicit account (brand + operator)
    Given the Buyer Agent has an authenticated connection
    And the agent has authority over the implicit account for brand "spark" on operator "pinnacle-media.com"
    When the Buyer Agent sends a sync_governance request via REST with idempotency_key "uuid-v4-implicit-0000000000012" and one account referenced by brand "spark" on operator "pinnacle-media.com" bound to governance agent "https://governance.pinnacle-media.com" with Bearer credentials of length 64
    Then the response variant is success
    And the account for brand "spark" on operator "pinnacle-media.com" has status "synced"
    # PRE-B3

  @T-UC-030-sync-unauth @sync @auth @partition
  Scenario: sync_governance without authentication returns AUTH_TOKEN_INVALID
    Given the Buyer Agent has an unauthenticated connection
    When the Buyer Agent sends a sync_governance request via MCP without an authentication token and one account "acct-social-001"
    Then the response variant is error
    And the error code is "AUTH_REQUIRED"
    # PRE-B8, POST-F1, POST-F2
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-sync-no-authority @sync @authority @partition
  Scenario: sync_governance fails per-account when the agent has no authority over the referenced account
    Given the Buyer Agent has an authenticated connection
    And the agent does NOT have authority over account "acct-not-mine"
    When the Buyer Agent sends a sync_governance request via MCP with idempotency_key "uuid-v4-noauthor-0000000000013" and one account "acct-not-mine" bound to governance agent "https://governance.pinnacle-media.com" with Bearer credentials of length 64
    Then the response variant is success
    And the account "acct-not-mine" has status "failed"
    And the per-account errors include a SCOPE_INSUFFICIENT or ACCOUNT_NOT_FOUND code
    # PRE-B8, BR-7

  @T-UC-030-sync-partial @sync @partial-failure @ext-b
  Scenario: sync_governance with mixed per-account outcomes returns success variant with synced and failed entries
    Given the Buyer Agent has an authenticated connection
    And the agent has authority over account "acct-social-001"
    And the agent does NOT have authority over account "acct-unknown"
    When the Buyer Agent sends a sync_governance request via MCP with idempotency_key "uuid-v4-partialf-0000000000014" and two accounts "acct-social-001" and "acct-unknown" both bound to governance agent "https://governance.pinnacle-media.com"
    Then the response variant is success
    And the response accounts array has 2 items
    And account "acct-social-001" has status "synced" and echoes the governance_agents URL
    And account "acct-unknown" has status "failed" and carries a per-account errors array
    And each per-account error should include a "suggestion" field guiding remediation
    And the response does NOT carry an operation-level errors array
    # POST-S1, POST-F2, POST-F3, BR-8, BR-9

  @T-UC-030-sync-idempotency-conflict @sync @idempotency @partition
  Scenario: sync_governance replay with the same idempotency_key but a different accounts payload is rejected with IDEMPOTENCY_CONFLICT
    Given the Buyer Agent has an authenticated connection
    And the agent has authority over account "acct-social-001"
    And the Buyer Agent previously sent a sync_governance request with idempotency_key "uuid-v4-conflict-000000000015" that synced "acct-social-001" to "https://governance.pinnacle-media.com"
    When the Buyer Agent sends a sync_governance request with idempotency_key "uuid-v4-conflict-000000000015" and a different accounts payload binding "acct-social-001" to "https://governance.acme-buyer.com"
    Then the response variant is error
    And the error code is "IDEMPOTENCY_CONFLICT"
    And no governance bindings are modified
    # BR-RULE-243 INV-5, POST-F1
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-sync-permission-denied @sync @authority @partition
  Scenario: sync_governance fails per-account with PERMISSION_DENIED when the agent's granted scope does not permit governance binding
    Given the Buyer Agent has an authenticated connection
    And the agent is known to account "acct-social-002" but its granted scope does not permit governance binding
    When the Buyer Agent sends a sync_governance request via MCP with idempotency_key "uuid-v4-permden-00000000000016" and one account "acct-social-002" bound to governance agent "https://governance.pinnacle-media.com" with Bearer credentials of length 64
    Then the response variant is success
    And the account "acct-social-002" has status "failed"
    And the per-account errors include a PERMISSION_DENIED code
    # BR-RULE-245 INV-4, BR-7
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-sync-absent-account-untouched @sync @replace-semantics @partition
  Scenario: sync_governance replace scope is per-account; an account not named in the request keeps its prior binding
    Given the Buyer Agent has an authenticated connection
    And the agent has authority over accounts "acct-social-001" and "acct-social-003"
    And account "acct-social-003" is currently bound to governance agent "https://governance.legacy-buyer.com"
    When the Buyer Agent sends a sync_governance request via MCP with idempotency_key "uuid-v4-perscope-00000000000017" naming only account "acct-social-001" bound to governance agent "https://governance.pinnacle-media.com" with Bearer credentials of length 64
    Then the account "acct-social-001" has status "synced"
    And the binding on account "acct-social-003" remains "https://governance.legacy-buyer.com" unchanged
    # BR-RULE-241 INV-3

  @T-UC-030-check-purchase-approved @check @outbound @purchase @approved @post-s6 @post-s7 @post-s8
  Scenario: Seller calls check_governance for an intent check on phase=purchase and receives approved
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com" with persisted Bearer credentials of length 64
    And the seller is preparing to honor a create_media_buy proposal on plan "plan-2026-q2-001"
    When the Seller Agent calls check_governance outbound with plan_id "plan-2026-q2-001", caller "https://seller.example.com", phase "purchase", purchase_type "media_buy", tool "create_media_buy", and a payload representing the proposed media buy
    Then the check_governance request carries no sibling account field
    And the governance agent returns status "approved" with check_id, plan_id echo, explanation, expires_at, and a fresh governance_context JWS
    And the Seller Agent verifies the governance_context JWS per the AdCP JWS profile
    And the Seller Agent persists the governance_context unchanged
    And the Seller Agent proceeds with create_media_buy before expires_at
    # POST-S6, POST-S7, POST-S8, BR-10, BR-12, BR-13, BR-14
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-check-execution-approved @check @outbound @execution @approved
  Scenario: Seller calls check_governance for an execution check (committed planned_delivery)
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com" with persisted credentials
    And the seller has a committed planned_delivery for plan "plan-2026-q2-001"
    When the Seller Agent calls check_governance outbound with plan_id "plan-2026-q2-001", caller "https://seller.example.com", phase "purchase", planned_delivery present, tool absent, and payload absent
    Then the governance agent infers an execution-check shape from the presence of planned_delivery
    And the governance agent returns status "approved" with expires_at and a fresh governance_context JWS
    And the Seller Agent persists the governance_context
    # PRE-B14, BR-11
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-check-delivery-phase @check @outbound @delivery @ongoing @post-s11
  Scenario: Seller calls check_governance on phase=delivery with delivery_metrics and schedules next_check
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com" with persisted credentials
    And the seller previously obtained an approved governance_context JWS for plan "plan-2026-q2-001"
    When the Seller Agent calls check_governance outbound with plan_id "plan-2026-q2-001", caller "https://seller.example.com", phase "delivery", governance_context threaded unchanged from the prior check, and delivery_metrics.reporting_period present
    Then the governance agent accepts the call as a delivery-phase report
    And the governance agent returns status "approved" with next_check populated for the next reporting window
    And the Seller Agent schedules the next outbound check_governance call at next_check
    # PRE-B12, POST-S11, BR-11
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-check-delivery-missing-reporting-period @check @validation @partition @boundary
  Scenario: check_governance on phase=delivery without delivery_metrics.reporting_period is rejected
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com"
    When the Seller Agent calls check_governance outbound with plan_id "plan-2026-q2-001", caller "https://seller.example.com", phase "delivery", and delivery_metrics that omit reporting_period
    Then the governance agent rejects the request as malformed
    And the error code is "INVALID_REQUEST"
    And the seller does not proceed with the delivery-phase reporting until reporting_period is supplied
    # PRE-B12
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-check-no-sibling-account @check @validation @partition @contract
  Scenario: check_governance request that carries a sibling account field is rejected as a contract violation
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com"
    When the Seller Agent constructs a check_governance request with both plan_id "plan-2026-q2-001" AND a sibling account field
    Then the governance agent rejects the request as a contract violation
    And the error code is "FIELD_NOT_PERMITTED"
    And the Seller Agent strips the account field before retrying (plan_id resolves the account internally)
    # PRE-B10, BR-10
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-check-conditions @check @outbound @conditions @post-s9
  Scenario: Governance agent returns status=conditions with required field-level adjustments
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com"
    When the Seller Agent calls check_governance outbound with plan_id "plan-2026-q2-001", caller "https://seller.example.com", phase "purchase", tool "create_media_buy", and a payload whose budget exceeds the plan's quarterly cap
    Then the governance agent returns status "conditions" with conditions array (minItems 1), expires_at, and a governance_context JWS
    And each condition carries field path, optional required_value, and reason
    And the Seller Agent applies the adjustments to the payload
    And the Seller Agent re-calls check_governance with the adjusted payload before proceeding
    # POST-S9, BR-14, BR-15
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-check-denied @check @outbound @denied @ext-a @post-s10
  Scenario: Governance agent returns status=denied with findings (Extension A)
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com"
    When the Seller Agent calls check_governance outbound with plan_id "plan-2026-q2-001", caller "https://seller.example.com", phase "purchase", tool "create_media_buy", and a payload that violates the governance plan
    Then the governance agent returns status "denied" with findings (minItems 1), explanation, and a governance_context JWS
    And each finding carries category_id, severity, and explanation
    And the Seller Agent persists the governance_context for audit
    And the Seller Agent does NOT proceed with create_media_buy
    And the Seller Agent surfaces the denial to the buyer with the findings attached
    And the surfaced denial error should include a "suggestion" field guiding buyer remediation
    # POST-S10, POST-F1, POST-F3, BR-14, BR-15, BR-17
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-check-jws-forwarded-unchanged @check @outbound @jws @lifecycle @post-s7
  Scenario: Seller forwards governance_context JWS unchanged on subsequent checks for the same governed action
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com"
    And the Seller Agent obtained governance_context JWS "JWS-token-aaaa.bbbb.cccc" from a prior check on plan "plan-2026-q2-001"
    When the Seller Agent calls check_governance outbound again on the same plan with phase "modification" and a modification_summary
    Then the request carries governance_context exactly "JWS-token-aaaa.bbbb.cccc" with no modification
    And the governance agent decodes its own token to resolve internal plan state
    # POST-S7, BR-12, BR-13
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-check-expired-approval @check @lifecycle @expires-at @post-s8
  Scenario: Seller MUST act before expires_at; a lapsed approval is no approval
    Given the Seller Agent received an approved decision on plan "plan-2026-q2-001" with expires_at in the past
    When the Seller Agent attempts to proceed with the governed action after expires_at
    Then the Seller Agent treats the approval as lapsed and does NOT proceed
    And the Seller Agent re-calls check_governance to obtain a fresh decision
    # POST-S8, BR-16
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-check-defaults @check @defaults @partition
  Scenario: check_governance applies defaults for omitted phase and purchase_type
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com"
    When the Seller Agent calls check_governance outbound with plan_id "plan-2026-q2-001" and caller "https://seller.example.com" but omits phase and purchase_type
    Then the governance agent evaluates the request with phase "purchase" and purchase_type "media_buy" by default
    # PRE-B17, BR-18

  @T-UC-030-check-missing-plan-id @check @validation @partition @contract
  Scenario: check_governance without plan_id is rejected and the seller does not proceed
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com"
    When the Seller Agent calls check_governance outbound with caller "https://seller.example.com" but omits plan_id
    Then the governance agent rejects the request as malformed
    And the error code is "INVALID_REQUEST"
    And the Seller Agent does NOT proceed with the governed action
    # BR-RULE-247 INV-1
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-check-invalid-caller @check @validation @partition @boundary
  Scenario Outline: check_governance with a missing or non-URI caller is rejected with INVALID_REQUEST
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com"
    When the Seller Agent calls check_governance outbound with plan_id "plan-2026-q2-001" and caller "<caller>"
    Then the governance agent rejects the request with error code "INVALID_REQUEST"
    And the error code is "INVALID_REQUEST"
    # BR-RULE-247 INV-2, INV-3 -- caller is required and format: uri
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

    Examples:
      | case            | caller    |
      | missing caller  |           |
      | not a valid uri | not-a-uri |

  @T-UC-030-check-budget-availability-probe @check @shape-inference @partition
  Scenario: check_governance with tool, payload, and planned_delivery all absent is inferred as a budget-availability probe
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com"
    When the Seller Agent calls check_governance outbound with plan_id "plan-2026-q2-001", caller "https://seller.example.com", and no tool, payload, or planned_delivery
    Then the governance agent infers a budget-availability probe shape with no specific action proposed
    And the governance agent returns a decision status
    # BR-RULE-248 INV-3
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-check-governance-unavailable @check @outbound @governance-failure @fail-closed @partition
  Scenario: check_governance where the governance agent is unreachable fails closed with GOVERNANCE_UNAVAILABLE
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com"
    And the bound governance agent is unreachable
    When the Seller Agent calls check_governance outbound with plan_id "plan-2026-q2-001", caller "https://seller.example.com", phase "purchase", tool "create_media_buy", and a payload representing the proposed media buy
    Then the Seller Agent obtains no governance decision
    And the Seller Agent treats the outcome as non-approval and does NOT proceed with the governed action
    And the Seller Agent surfaces the failure to the buyer with error code "GOVERNANCE_UNAVAILABLE"
    And no error value is introduced into the governance-decision status enum
    # BR-RULE-252 INV-1/2/3, BR-RULE-250 INV-4
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

  @T-UC-030-check-jws-verification-failure @check @outbound @jws @verification @v3-1 @partition
  Scenario Outline: A governance_context whose JWS fails verification blocks the governed action with PERMISSION_DENIED
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com"
    And the Seller Agent is a 3.1 seller that verifies the governance_context JWS per the AdCP JWS profile
    When the Seller Agent receives a governance_context that fails verification because <reason>
    Then the Seller Agent MUST NOT treat the governed request as governance-approved
    And the Seller Agent does NOT proceed with the governed action
    And the failure is surfaced as error code "PERMISSION_DENIED"
    And the failure is modeled outside the approved/conditions/denied decision enum
    # BR-RULE-250 INV-1/2/3/4
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

    Examples:
      | reason                                  |
      | the JWS signature is invalid            |
      | the aud claim is wrong or missing       |
      | the exp claim has expired               |
      | the jti has already been seen (replay)  |
      | the token has been revoked              |
      | the token is a non-JWS legacy plaintext |

  @T-UC-030-check-findings-opaque-taxonomy @check @findings @opaque-taxonomy @v3-1
  Scenario: Findings taxonomy ids are opaque; the seller acts on explanation and severity, not on category_id
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com"
    And the Seller Agent received a check_governance decision carrying findings with agent-internal category_id and policy_id values
    When the Seller Agent surfaces a finding to the buyer
    Then the Seller Agent MUST NOT pattern-match category_id or policy_id against a fixed list
    And the Seller Agent acts on the human-readable explanation and the severity value
    And the taxonomy ids are treated as opaque audit correlation labels only
    # BR-RULE-255 INV-1/2/3/4

  @T-UC-030-bva-purchase-type @check @validation @boundary @bva
  Scenario Outline: check_governance purchase_type boundary -- <boundary>
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com"
    When the Seller Agent issues a check_governance request exercising the purchase_type boundary case "<boundary>"
    Then the request verdict is "<verdict>"
    # purchase_type.yaml -- enum [media_buy, rights_license, signal_activation, creative_services], default media_buy
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

    Examples:
      | boundary                           | verdict |
      | purchase_type omitted              | valid   |
      | purchase_type = signal_activation  | valid   |
      | purchase_type = value outside enum | invalid |

  @T-UC-030-bva-phase @check @validation @boundary @bva
  Scenario Outline: check_governance phase boundary -- <boundary>
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com"
    When the Seller Agent issues a check_governance request exercising the phase boundary case "<boundary>"
    Then the request verdict is "<verdict>"
    # governance_phase.yaml -- phase defaults to purchase; delivery phase requires delivery_metrics.reporting_period
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

    Examples:
      | boundary                                   | verdict |
      | phase omitted                              | valid   |
      | phase = delivery + reporting_period present| valid   |
      | phase = delivery + delivery_metrics absent | invalid |
      | phase = value outside enum                 | invalid |

  @T-UC-030-bva-decision-status @check @outbound @boundary @bva
  Scenario Outline: governance decision status boundary -- <boundary>
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com"
    When the Seller Agent receives a governance decision exercising the status boundary case "<boundary>"
    Then the decision verdict is "<verdict>"
    # governance_decision.yaml -- status enum {approved, denied, conditions}; error is NOT an enum member (modeled as transport error)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

    Examples:
      | boundary                                                                          | verdict |
      | status in enum {approved,denied,conditions} with its conditional fields satisfied | valid   |
      | status = 'error' (not an enum member)                                             | invalid |
      | status = conditions, conditions[] empty                                           | invalid |
      | status = denied, findings[] absent                                                | invalid |
      | status = approved, expires_at absent                                              | invalid |

  @T-UC-030-bva-expires-at @check @lifecycle @boundary @bva
  Scenario Outline: governance expires_at lifecycle boundary -- <boundary>
    Given account "acct-social-001" is bound to governance agent "https://governance.pinnacle-media.com"
    When the Seller Agent exercises the expires_at boundary case "<boundary>"
    Then the lifecycle verdict is "<verdict>"
    # governance_expires_at.yaml -- approved/conditions decisions carry expires_at; acting after expires_at requires a fresh re-check
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

    Examples:
      | boundary                                                               | verdict |
      | status approved/conditions + expires_at present + acted before deadline| valid   |
      | status approved/conditions + expires_at absent                         | invalid |
      | acting on the governed action after expires_at without re-check        | invalid |

  @T-UC-030-bva-sync-account-status @sync @boundary @bva
  Scenario Outline: sync_governance per-account status boundary -- <boundary>
    Given the Buyer Agent has an authenticated connection
    When a sync_governance response exercises the per-account status boundary case "<boundary>"
    Then the response verdict is "<verdict>"
    # sync_governance_account_status.yaml -- per-account status is a two-member enum {synced, failed}
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

    Examples:
      | boundary                                          | verdict |
      | status=synced with echoed governance_agents URL   | valid   |
      | status=failed with per-account errors[]           | valid   |
      | status value outside the two-member enum          | invalid |

  @T-UC-030-bva-governance-agents-cardinality @sync @validation @boundary @bva @cardinality
  Scenario Outline: sync_governance governance_agents cardinality boundary -- <boundary>
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_governance request exercising the governance_agents boundary case "<boundary>"
    Then the request verdict is "<verdict>"
    # governance_agents_cardinality.yaml -- exactly one agent per account (minItems 1, maxItems 1)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

    Examples:
      | boundary                        | verdict |
      | governance_agents has 0 entries | invalid |
      | governance_agents has 2 entries | invalid |

  @T-UC-030-bva-accounts-cardinality @sync @validation @boundary @bva
  Scenario Outline: sync_governance accounts cardinality boundary -- <boundary>
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_governance request exercising the accounts boundary case "<boundary>"
    Then the request verdict is "<verdict>"
    # sync_governance_accounts_cardinality.yaml -- accounts minItems 1, maxItems 100
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

    Examples:
      | boundary                  | verdict |
      | accounts has 0 entries    | invalid |
      | accounts has 100 entries  | valid   |
      | accounts has 101 entries  | invalid |

  @T-UC-030-bva-credentials @sync @validation @boundary @bva
  Scenario Outline: sync_governance authentication.credentials boundary -- <boundary>
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_governance request exercising the credentials boundary case "<boundary>"
    Then the request verdict is "<verdict>"
    # governance_credentials.yaml -- credentials required on request (minLength 32), NEVER echoed on response
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

    Examples:
      | boundary                        | verdict |
      | credentials absent              | invalid |
      | credentials present on response | invalid |

  @T-UC-030-bva-auth-schemes @sync @validation @boundary @bva
  Scenario Outline: sync_governance authentication.schemes boundary -- <boundary>
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_governance request exercising the authentication.schemes boundary case "<boundary>"
    Then the request verdict is "<verdict>"
    # governance_auth_scheme.yaml -- exactly one scheme from the closed enum
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

    Examples:
      | boundary                  | verdict |
      | exactly one valid scheme  | valid   |
      | empty array (0 items)     | invalid |
      | two items                 | invalid |
      | single item outside enum  | invalid |
      | schemes absent            | invalid |

  @T-UC-030-bva-url @sync @validation @boundary @bva
  Scenario Outline: sync_governance governance agent url boundary -- <boundary>
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_governance request exercising the url boundary case "<boundary>"
    Then the request verdict is "<verdict>"
    # governance_agent_url.yaml -- format: uri, https scheme required (URL_NOT_HTTPS)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/sync-governance-request.json

    Examples:
      | boundary                | verdict |
      | https:// URL            | valid   |
      | http:// URL (plaintext) | invalid |
      | non-uri string          | invalid |
      | url absent              | invalid |

  @T-UC-030-bva-idempotency-key @sync @validation @boundary @bva @idempotency
  Scenario Outline: sync_governance idempotency_key boundary -- <boundary>
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_governance request exercising the idempotency_key boundary case "<boundary>"
    Then the request verdict is "<verdict>"
    # governance_idempotency_key.yaml -- required, pattern ^[A-Za-z0-9_.:-]{16,255}$, replay must match payload

    Examples:
      | boundary                                        | verdict |
      | absent (field not provided)                     | invalid |
      | valid length, disallowed character (e.g. space) | invalid |
      | replay: same key + identical payload            | valid   |
      | replay: same key + divergent payload            | invalid |

  @T-UC-030-storyboard-binding-used-during-create-media-buy @storyboard-v3.1 @v3-1 @binding @create-media-buy-integration
  Scenario: Governance agent bound via sync_governance is invoked by the seller during create_media_buy
    Given the buyer has registered governance agent "https://governance.pinnacle-agency.example" on the account via sync_governance
    When the Buyer Agent subsequently sends create_media_buy under the same account
    Then the Seller Agent should call check_governance against the previously registered governance agent URL
    And the seller MUST NOT skip the governance check when an agent is bound to the account
    And the seller MUST NOT call a governance agent URL other than the one registered via sync_governance for this account
    # governance/index.yaml and media-buy/index.yaml governance_setup phases:
    # the storyboard contract is two-step: (1) sync_governance stores the
    # governance agent URL on the account, and (2) the seller invokes that
    # agent during create_media_buy validation. UC-030 covers both halves
    # individually; this storyboard scenario anchors the integration -- the
    # specific governance URL persisted at sync time MUST be the URL the
    # seller calls at create_media_buy time.
    # governance binding integration: sync_governance URL is the only URL invoked at create_media_buy time

