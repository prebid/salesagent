# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-017 Account Financials & Usage Reporting
  As a Buyer
  I want to query the financial status of an operator-billed account and report vendor service consumption
  So that I can make informed spend decisions and ensure vendor billing is accurate

  # Postconditions verified:
  #   POST-S1: Buyer knows the account's currency and the actual billing period covered
  #   POST-S2: Buyer knows the seller's billing timezone for interpreting date boundaries
  #   POST-S3: Buyer knows the total spend for the period (and optionally the active media buy count)
  #   POST-S4: Buyer knows the credit position (credit limit, available credit, utilization) for credit-based accounts
  #   POST-S5: Buyer knows the prepay balance (available funds, last top-up) for prepay accounts
  #   POST-S6: Buyer knows the overall payment status (current, past_due, or suspended)
  #   POST-S7: Buyer knows the payment terms in effect (e.g., net_30, prepay)
  #   POST-S8: Buyer can review recent invoices with their status, amounts, and due dates
  #   POST-S9: Seller knows how many usage records were accepted and stored
  #   POST-S10: Seller can track earned revenue for each vendor service consumed
  #   POST-S11: Seller can verify that the correct pricing option and rate were applied
  #   POST-S12: Buyer knows how many records were accepted (accepted count)
  #   POST-S13: Buyer knows which individual records failed and why (per-record errors with field paths)
  #   POST-S14: Buyer knows whether the account is a sandbox (no billing occurred)
  #   POST-F1: System state is unchanged on failure (get_account_financials is read-only; report_usage rejects invalid records)
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Application context is still echoed when possible
  #
  # Rules: BR-RULE-132..145, BR-RULE-043 (15 rules, ~47 invariants)
  # Extensions: A (UNSUPPORTED_FEATURE), B (ACCOUNT_NOT_FOUND), C (INVALID_USAGE_DATA),
  #   D (INVALID_PRICING_OPTION), E (DUPLICATE_REQUEST), F (INVALID_REQUEST), G (Partial Acceptance)
  # Error codes: UNSUPPORTED_FEATURE, ACCOUNT_NOT_FOUND, INVALID_USAGE_DATA,
  #   INVALID_PRICING_OPTION, DUPLICATE_REQUEST, INVALID_REQUEST

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And the Buyer Agent has an authenticated connection via MCP


  @T-UC-017-main-fin @main-flow @get-financials @happy-path @post-s1 @post-s2 @post-s3 @post-s6 @post-s7
  Scenario: Get account financials -- operator-billed account with full financial data
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_acme_001" exists
    When the Buyer Agent invokes get_account_financials with account "acct_acme_001"
    Then the response contains the success variant with account, currency, period, and timezone
    And the response includes spend summary with total_spend and active_buy_count
    And the response includes payment_status
    And the response includes payment_terms
    # POST-S1: Buyer knows currency and billing period
    # POST-S2: Buyer knows billing timezone
    # POST-S3: Buyer knows spend for the period
    # POST-S6: Buyer knows payment status
    # POST-S7: Buyer knows payment terms

  @T-UC-017-main-fin-credit @main-flow @get-financials @happy-path @post-s4
  Scenario: Get account financials -- credit-based account includes credit section
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_credit_001" exists with credit-based payment terms (net_30)
    When the Buyer Agent invokes get_account_financials with account "acct_credit_001"
    Then the response contains the success variant
    And the response includes credit section with credit_limit and available_credit
    # POST-S4: Buyer knows credit position

  @T-UC-017-main-fin-prepay @main-flow @get-financials @happy-path @post-s5
  Scenario: Get account financials -- prepay account includes balance section
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_prepay_001" exists with prepay payment terms
    When the Buyer Agent invokes get_account_financials with account "acct_prepay_001"
    Then the response contains the success variant
    And the response includes balance section with available funds
    # POST-S5: Buyer knows prepay balance

  @T-UC-017-main-fin-invoices @main-flow @get-financials @happy-path @post-s8
  Scenario: Get account financials -- response includes invoice history
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_invoice_001" exists with recent invoices
    When the Buyer Agent invokes get_account_financials with account "acct_invoice_001"
    Then the response contains the success variant
    And the response includes invoices array with status, amounts, and due dates
    # POST-S8: Buyer can review recent invoices

  @T-UC-017-main-usage @main-flow @report-usage @happy-path @post-s9 @post-s10 @post-s11 @post-s12
  Scenario: Report usage -- all records accepted with valid data
    Given an operator-billed account "acct_001" exists
    And a valid reporting period from "2026-02-01T00:00:00Z" to "2026-02-28T23:59:59Z"
    When the Buyer Agent invokes report_usage with 3 valid usage records for account "acct_001"
    Then the response contains accepted count of 3
    And the response does not contain an errors array
    And the request context is echoed in the response
    # POST-S9: Seller knows 3 records accepted and stored
    # POST-S10: Seller can track earned revenue
    # POST-S11: Seller can verify pricing options
    # POST-S12: Buyer knows accepted count

  @T-UC-017-main-usage-pricing @main-flow @report-usage @happy-path @post-s11
  Scenario: Report usage -- record with valid pricing_option_id accepted
    Given an operator-billed account "acct_001" exists
    And a pricing option "po_lux_auto_cpm" is known for account "acct_001"
    When the Buyer Agent submits a usage record with pricing_option_id "po_lux_auto_cpm"
    Then the response contains accepted count of 1
    # POST-S11: Seller can verify correct pricing option and rate

  @T-UC-017-main-usage-sandbox @main-flow @report-usage @happy-path @post-s14
  Scenario: Report usage -- sandbox account flags no billing
    Given a sandbox account "acct_sandbox_001" exists
    And a valid reporting period
    When the Buyer Agent invokes report_usage with 2 valid usage records for account "acct_sandbox_001"
    Then the response contains accepted count of 2
    And the response contains sandbox flag set to true
    # POST-S14: Buyer knows this is a sandbox (no billing occurred)

  @T-UC-017-ext-a-cap @extension @ext-a @error @get-financials @post-f1 @post-f2 @post-f3
  Scenario: Get account financials -- capability not enabled returns UNSUPPORTED_FEATURE
    Given the seller does not declare account_financials capability (false or absent)
    And an operator-billed account "acct_001" exists
    When the Buyer Agent invokes get_account_financials with account "acct_001"
    Then the operation should fail with the error variant
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error should include "suggestion" field
    And the suggestion should contain "check get_adcp_capabilities"
    And the request context is echoed in the response
    # POST-F1: System state unchanged (read-only)
    # POST-F2: Buyer knows error code UNSUPPORTED_FEATURE
    # POST-F3: Context echoed

  @T-UC-017-ext-a-billing @extension @ext-a @error @get-financials @post-f1 @post-f2 @post-f3
  Scenario: Get account financials -- agent-billed account returns UNSUPPORTED_FEATURE
    Given the seller declares account_financials capability as true
    And an agent-billed account "acct_agent_001" exists
    When the Buyer Agent invokes get_account_financials with account "acct_agent_001"
    Then the operation should fail with the error variant
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error should include "suggestion" field
    And the suggestion should contain "agent billing system"
    And the request context is echoed in the response
    # POST-F1: System state unchanged (read-only)
    # POST-F2: Buyer knows error code UNSUPPORTED_FEATURE
    # POST-F3: Context echoed

  @T-UC-017-ext-b-fin @extension @ext-b @error @get-financials @post-f1 @post-f2 @post-f3
  Scenario: Get account financials -- unresolvable account returns ACCOUNT_NOT_FOUND
    Given the seller declares account_financials capability as true
    And no account with ID "acct_nonexistent" exists
    When the Buyer Agent invokes get_account_financials with account "acct_nonexistent"
    Then the operation should fail with the error variant
    And the error code should be "ACCOUNT_NOT_FOUND"
    And the error should include "suggestion" field
    And the suggestion should contain "verify account via list_accounts"
    And the request context is echoed in the response
    # POST-F1: System state unchanged (read-only)
    # POST-F2: Buyer knows error code ACCOUNT_NOT_FOUND
    # POST-F3: Context echoed

  @T-UC-017-ext-b-usage @extension @ext-b @error @report-usage @post-f2 @post-f3
  Scenario: Report usage -- unresolvable account in usage record returns per-record ACCOUNT_NOT_FOUND
    Given a valid reporting period
    And no account with ID "acct_nonexistent" exists
    When the Buyer Agent submits a usage record with account "acct_nonexistent"
    Then the response contains accepted count of 0
    And the response contains an errors array with 1 entry
    And the error code should be "ACCOUNT_NOT_FOUND"
    And the error field path should reference "usage[0].account"
    And the error should include "suggestion" field
    And the suggestion should contain "verify account"
    # POST-F2: Buyer knows error code ACCOUNT_NOT_FOUND
    # POST-F3: Suggestion provided

  @T-UC-017-ext-c-missing @extension @ext-c @error @report-usage @post-f2 @post-s13
  Scenario: Report usage -- missing required field returns INVALID_USAGE_DATA
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits a usage record missing vendor_cost field
    Then the response contains accepted count of 0
    And the response contains an errors array
    And the error code should be "INVALID_USAGE_DATA"
    And the error field path should reference "usage[0].vendor_cost"
    And the error should include "suggestion" field
    And the suggestion should contain "required fields"
    # POST-F2: Buyer knows specific error
    # POST-S13: Buyer knows which record failed and why

  @T-UC-017-ext-c-neg-cost @extension @ext-c @error @report-usage @post-f2 @post-s13
  Scenario: Report usage -- negative vendor_cost returns INVALID_USAGE_DATA
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits a usage record with vendor_cost of -50
    Then the response contains accepted count of 0
    And the response contains an errors array
    And the error code should be "INVALID_USAGE_DATA"
    And the error field path should reference "usage[0].vendor_cost"
    And the error should include "suggestion" field
    And the suggestion should contain "vendor_cost must be >= 0"
    # POST-F2: Buyer knows specific error
    # POST-S13: Buyer knows which record failed and why

  @T-UC-017-ext-c-currency @extension @ext-c @error @report-usage @post-f2 @post-s13
  Scenario: Report usage -- invalid currency format returns INVALID_USAGE_DATA
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits a usage record with currency "usd" (lowercase)
    Then the response contains accepted count of 0
    And the response contains an errors array
    And the error code should be "INVALID_USAGE_DATA"
    And the error field path should reference "usage[0].currency"
    And the error should include "suggestion" field
    And the suggestion should contain "ISO 4217"
    # POST-F2: Buyer knows specific error
    # POST-S13: Buyer knows which record failed and why

  @T-UC-017-ext-d @extension @ext-d @error @report-usage @post-f2 @post-s13
  Scenario: Report usage -- unknown pricing_option_id returns INVALID_PRICING_OPTION
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    And no pricing option "po_unknown" exists for account "acct_001"
    When the Buyer Agent submits a usage record with pricing_option_id "po_unknown"
    Then the response contains an errors array
    And the error code should be "INVALID_PRICING_OPTION"
    And the error field path should reference "usage[0].pricing_option_id"
    And the error should include "suggestion" field
    And the suggestion should contain "verify pricing_option_id from vendor discovery"
    # POST-F2: Buyer knows specific error
    # POST-S13: Buyer knows which record failed and why

  @T-UC-017-ext-e @extension @ext-e @report-usage @post-f1 @post-s12
  Scenario: Report usage -- duplicate idempotency_key returns original response
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    And a previous report_usage request with idempotency_key "550e8400-e29b-41d4-a716-446655440000" was accepted with 2 records
    When the Buyer Agent submits report_usage with the same idempotency_key "550e8400-e29b-41d4-a716-446655440000"
    Then the response returns the original accepted count of 2
    And no records are re-processed or double-counted
    And the error should include "suggestion" field
    And the suggestion should contain "safe to ignore on retry"
    # POST-F1: No state change (records already stored)
    # POST-S12: Buyer knows accepted count from original

  @T-UC-017-ext-f-fin @extension @ext-f @error @get-financials @post-f1 @post-f2 @post-f3
  Scenario: Get account financials -- missing required account field returns INVALID_REQUEST
    Given the seller declares account_financials capability as true
    When the Buyer Agent invokes get_account_financials without the required account field
    Then the operation should fail with the error variant
    And the error code should be "INVALID_REQUEST"
    And the error field path should reference "account"
    And the error should include "suggestion" field
    And the suggestion should contain "account is required"
    And the request context is echoed in the response
    # POST-F1: No state change
    # POST-F2: Buyer knows specific error
    # POST-F3: Context echoed

  @T-UC-017-ext-f-usage-period @extension @ext-f @error @report-usage @post-f1 @post-f2 @post-f3
  Scenario: Report usage -- missing reporting_period returns INVALID_REQUEST
    When the Buyer Agent invokes report_usage without the required reporting_period field
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field path should reference "reporting_period"
    And the error should include "suggestion" field
    And the suggestion should contain "reporting_period is required"
    # POST-F1: No state change
    # POST-F2: Buyer knows specific error
    # POST-F3: Context echoed

  @T-UC-017-ext-f-usage-empty @extension @ext-f @error @report-usage @post-f1 @post-f2 @post-f3
  Scenario: Report usage -- empty usage array returns INVALID_REQUEST
    Given a valid reporting period
    When the Buyer Agent invokes report_usage with an empty usage array
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error field path should reference "usage"
    And the error should include "suggestion" field
    And the suggestion should contain "at least one usage record"
    # POST-F1: No state change
    # POST-F2: Buyer knows specific error
    # POST-F3: Context echoed

  @T-UC-017-ext-g @extension @ext-g @report-usage @post-s9 @post-s12 @post-s13
  Scenario: Report usage -- partial acceptance stores valid records and reports errors
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    And a pricing option "po_valid" is known for account "acct_001"
    When the Buyer Agent submits 3 usage records where record 0 and 2 are valid and record 1 has invalid pricing_option_id "po_bad"
    Then the response contains accepted count of 2
    And the response contains an errors array with 1 entry
    And the error for record 1 has code "INVALID_PRICING_OPTION"
    And the error field path references "usage[1].pricing_option_id"
    And the error should include "suggestion" field
    And the suggestion should contain "verify pricing_option_id"
    # POST-S9: Seller knows 2 records accepted
    # POST-S12: Buyer knows accepted count is 2
    # POST-S13: Buyer knows record 1 failed with reason

  @T-UC-017-ext-g-all-fail @extension @ext-g @report-usage @post-s12 @post-s13
  Scenario: Report usage -- all records invalid returns accepted 0 with errors
    Given a valid reporting period
    When the Buyer Agent submits 2 usage records both with missing vendor_cost
    Then the response contains accepted count of 0
    And the response contains an errors array with 2 entries
    And each error has code "INVALID_USAGE_DATA"
    And each error should include "suggestion" field
    # POST-S12: Buyer knows accepted count is 0
    # POST-S13: Buyer knows each record failed and why

  @T-UC-017-r132-inv1 @invariant @br-rule-132 @get-financials
  Scenario: BR-RULE-132 INV-1 holds -- capability declared true enables get_account_financials
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_001" exists
    When the Buyer Agent invokes get_account_financials with account "acct_001"
    Then the response contains the success variant
    # INV-1: Capability flag true -> task is available

  @T-UC-017-r132-inv2 @invariant @br-rule-132 @get-financials @error
  Scenario: BR-RULE-132 INV-2 violated -- capability false returns UNSUPPORTED_FEATURE
    Given the seller declares account_financials capability as false
    When the Buyer Agent invokes get_account_financials with account "acct_001"
    Then the error code should be "UNSUPPORTED_FEATURE"
    And the error should include "suggestion" field
    # INV-2: Capability flag false -> UNSUPPORTED_FEATURE

  @T-UC-017-r132-inv3 @invariant @br-rule-132 @get-financials @error
  Scenario: BR-RULE-132 INV-3 holds -- UNSUPPORTED_FEATURE has correctable recovery
    Given the seller declares account_financials capability as false
    When the Buyer Agent invokes get_account_financials with account "acct_001"
    Then the error code should be "UNSUPPORTED_FEATURE"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "capabilities"
    # INV-3: UNSUPPORTED_FEATURE recovery is correctable

  @T-UC-017-r133-inv1 @invariant @br-rule-133 @get-financials
  Scenario: BR-RULE-133 INV-1 holds -- operator-billed account returns financial data
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_operator" exists
    When the Buyer Agent invokes get_account_financials with account "acct_operator"
    Then the response contains the success variant with financial data
    # INV-1: Operator billing -> financial data returned

  @T-UC-017-r133-inv2 @invariant @br-rule-133 @get-financials @error
  Scenario: BR-RULE-133 INV-2 violated -- agent-billed account returns UNSUPPORTED_FEATURE
    Given the seller declares account_financials capability as true
    And an agent-billed account "acct_agent" exists
    When the Buyer Agent invokes get_account_financials with account "acct_agent"
    Then the error code should be "UNSUPPORTED_FEATURE"
    And the error should include "suggestion" field
    And the suggestion should contain "billing system"
    # INV-2: Agent billing -> UNSUPPORTED_FEATURE

  @T-UC-017-r133-inv3 @invariant @br-rule-133 @get-financials @error
  Scenario: BR-RULE-133 INV-3 holds -- billing model mismatch returns correctable recovery
    Given the seller declares account_financials capability as true
    And an agent-billed account "acct_agent" exists
    When the Buyer Agent invokes get_account_financials with account "acct_agent"
    Then the error code should be "UNSUPPORTED_FEATURE"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "billing system"
    # INV-3: Recovery is correctable for billing model

  @T-UC-017-r134-inv1 @invariant @br-rule-134 @get-financials
  Scenario: BR-RULE-134 INV-1 holds -- success response has financial fields, no errors
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_001" exists
    When the Buyer Agent invokes get_account_financials with account "acct_001"
    Then the response contains account, currency, period, and timezone
    And the response does not contain an errors field
    # INV-1: Success -> financial fields present, errors absent

  @T-UC-017-r134-inv2 @invariant @br-rule-134 @get-financials @error
  Scenario: BR-RULE-134 INV-2 holds -- error response has errors array, no financial fields
    Given the seller declares account_financials capability as false
    When the Buyer Agent invokes get_account_financials with account "acct_001"
    Then the response contains an errors array with at least 1 entry
    And the response does not contain account, currency, period, or timezone fields
    And the error should include "suggestion" field
    # INV-2: Error -> errors present, financial fields absent

  @T-UC-017-r134-inv3 @invariant @br-rule-134 @get-financials
  Scenario: BR-RULE-134 INV-3 holds -- response is exactly one of success or error
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_001" exists
    When the Buyer Agent invokes get_account_financials with account "acct_001"
    Then the response matches exactly one branch of the discriminated union
    # INV-3: Exactly one of success or error, never both, never neither

  @T-UC-017-r135-inv1 @invariant @br-rule-135 @get-financials
  Scenario: BR-RULE-135 INV-1 holds -- omitted period defaults to current billing cycle
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_001" exists
    When the Buyer Agent invokes get_account_financials without a period parameter
    Then the response period covers the current billing cycle
    # INV-1: Omitted period -> current billing cycle

  @T-UC-017-r135-inv2 @invariant @br-rule-135 @get-financials
  Scenario: BR-RULE-135 INV-2 holds -- specified period may be adjusted to billing boundaries
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_001" exists with monthly billing cycles
    When the Buyer Agent invokes get_account_financials with period "2026-01-15" to "2026-02-15"
    Then the response period may differ from the requested period
    And the response period reflects the seller's billing cycle boundaries
    # INV-2: Specified period -> adjusted to billing cycle overlap

  @T-UC-017-r135-inv3 @invariant @br-rule-135 @get-financials
  Scenario: BR-RULE-135 INV-3 holds -- success response always includes period
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_001" exists
    When the Buyer Agent invokes get_account_financials with account "acct_001"
    Then the response contains a period field with start and end dates
    # INV-3: Period always present on success

  @T-UC-017-r136-inv1 @invariant @br-rule-136 @get-financials
  Scenario: BR-RULE-136 INV-1 holds -- success response guarantees four fields
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_001" exists
    When the Buyer Agent invokes get_account_financials with account "acct_001"
    Then the response contains account field
    And the response contains currency field
    And the response contains period field
    And the response contains timezone field
    # INV-1: account, currency, period, timezone always present on success

  @T-UC-017-r136-inv2 @invariant @br-rule-136 @get-financials
  Scenario: BR-RULE-136 INV-2 holds -- optional fields may be absent on success
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_minimal" exists with a seller providing minimal data
    When the Buyer Agent invokes get_account_financials with account "acct_minimal"
    Then the response contains the four guaranteed fields
    And the response may omit spend, credit, balance, payment_status, payment_terms, and invoices
    # INV-2: Optional fields may be absent

  @T-UC-017-r137-inv1 @invariant @br-rule-137 @get-financials
  Scenario: BR-RULE-137 INV-1 holds -- credit section present for credit-based account
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_credit" with net_30 payment terms
    When the Buyer Agent invokes get_account_financials with account "acct_credit"
    Then the response may include credit section with credit_limit and available_credit
    # INV-1: Credit-based terms -> credit section may be present

  @T-UC-017-r137-inv2 @invariant @br-rule-137 @get-financials
  Scenario: BR-RULE-137 INV-2 holds -- credit section absent for prepay account
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_prepay" with prepay payment terms
    When the Buyer Agent invokes get_account_financials with account "acct_prepay"
    Then the response does not contain a credit section
    # INV-2: Non-credit terms -> credit section absent

  @T-UC-017-r137-inv3 @invariant @br-rule-137 @get-financials
  Scenario: BR-RULE-137 INV-3 holds -- credit section when present has required sub-fields
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_credit" with net_30 payment terms and seller provides credit data
    When the Buyer Agent invokes get_account_financials with account "acct_credit"
    Then the credit section contains credit_limit with value >= 0
    And the credit section contains available_credit
    # INV-3: When present, credit_limit and available_credit required

  @T-UC-017-r138-inv1 @invariant @br-rule-138 @get-financials
  Scenario: BR-RULE-138 INV-1 holds -- balance section present for prepay account
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_prepay" with prepay payment terms
    When the Buyer Agent invokes get_account_financials with account "acct_prepay"
    Then the response may include balance section with available field
    # INV-1: Prepay terms -> balance section may be present

  @T-UC-017-r138-inv2 @invariant @br-rule-138 @get-financials
  Scenario: BR-RULE-138 INV-2 holds -- balance section absent for credit-based account
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_credit" with net_30 payment terms
    When the Buyer Agent invokes get_account_financials with account "acct_credit"
    Then the response does not contain a balance section
    # INV-2: Non-prepay terms -> balance section absent

  @T-UC-017-r138-inv3 @invariant @br-rule-138 @get-financials
  Scenario: BR-RULE-138 INV-3 holds -- balance section when present has available field
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_prepay" with prepay payment terms and seller provides balance data
    When the Buyer Agent invokes get_account_financials with account "acct_prepay"
    Then the balance section contains available with value >= 0
    # INV-3: When present, available required

  @T-UC-017-r139-inv1 @invariant @br-rule-139 @report-usage
  Scenario: BR-RULE-139 INV-1 holds -- mixed valid/invalid records partially accepted
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits 3 records where 2 are valid and 1 has negative vendor_cost
    Then the response contains accepted count of 2
    And the response contains an errors array with 1 entry
    # INV-1: Mix of valid/invalid -> valid stored, invalid rejected

  @T-UC-017-r139-inv2 @invariant @br-rule-139 @report-usage
  Scenario: BR-RULE-139 INV-2 holds -- all records valid means all accepted
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits 3 valid usage records
    Then the response contains accepted count of 3
    And the response does not contain an errors array
    # INV-2: All valid -> accepted = total, errors absent

  @T-UC-017-r139-inv3 @invariant @br-rule-139 @report-usage
  Scenario: BR-RULE-139 INV-3 holds -- all records invalid means accepted is 0
    Given a valid reporting period
    When the Buyer Agent submits 2 usage records both with missing account
    Then the response contains accepted count of 0
    And the response contains an errors array with 2 entries
    # INV-3: All invalid -> accepted = 0, errors for each

  @T-UC-017-r139-inv4 @invariant @br-rule-139 @report-usage
  Scenario: BR-RULE-139 INV-4 holds -- accepted count always present in response
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits 1 valid usage record
    Then the response contains accepted count field with value >= 0
    # INV-4: accepted count always present

  @T-UC-017-r140-inv1 @invariant @br-rule-140 @report-usage
  Scenario: BR-RULE-140 INV-1 holds -- duplicate key returns original response
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    And a previous report_usage with idempotency_key "key-abc-123" accepted 2 records
    When the Buyer Agent submits report_usage with idempotency_key "key-abc-123"
    Then the response returns accepted count of 2 (original response)
    And no records are re-processed
    # INV-1: Matching key -> original response without re-processing

  @T-UC-017-r140-inv2 @invariant @br-rule-140 @report-usage
  Scenario: BR-RULE-140 INV-2 holds -- new key processed normally
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits report_usage with a new idempotency_key "key-new-456"
    Then the request is processed normally
    And the response contains accepted count reflecting processing result
    And the idempotency_key is stored for future deduplication
    # INV-2: New key -> processed normally, key stored

  @T-UC-017-r140-inv3 @invariant @br-rule-140 @report-usage
  Scenario: BR-RULE-140 INV-3 holds -- absent key means not idempotent
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits report_usage without an idempotency_key
    Then the request is processed normally
    And resubmission would create duplicate records
    # INV-3: No key -> not idempotent

  @T-UC-017-r141-inv1 @invariant @br-rule-141 @report-usage
  Scenario: BR-RULE-141 INV-1 holds -- each record carries its own account
    Given operator-billed accounts "acct_001" and "acct_002" exist
    And a valid reporting period
    When the Buyer Agent submits 2 usage records each with their own account reference
    Then both records are accepted with correct account attribution
    # INV-1: Each record must include its own account reference

  @T-UC-017-r141-inv2 @invariant @br-rule-141 @report-usage
  Scenario: BR-RULE-141 INV-2 holds -- single request spans multiple accounts
    Given operator-billed accounts "acct_001" and "acct_002" exist
    And a valid reporting period
    When the Buyer Agent submits records: one for "acct_001" and one for "acct_002"
    Then the response contains accepted count of 2
    # INV-2: Single request may contain records for different accounts

  @T-UC-017-r141-inv3 @invariant @br-rule-141 @report-usage
  Scenario: BR-RULE-141 INV-3 holds -- single request spans multiple campaigns
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits 2 records with buyer_campaign_ref "camp_A" and "camp_B"
    Then the response contains accepted count of 2
    # INV-3: Single request may contain records for different campaigns

  @T-UC-017-r142-inv1 @invariant @br-rule-142 @report-usage
  Scenario: BR-RULE-142 INV-1 holds -- record with all three required fields accepted
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits a usage record with account, vendor_cost 100.00, and currency "USD"
    Then the response contains accepted count of 1
    # INV-1: account, vendor_cost, currency present -> accepted

  @T-UC-017-r142-inv4 @invariant @br-rule-142 @report-usage @error
  Scenario: BR-RULE-142 INV-4 violated -- missing required field rejected with INVALID_USAGE_DATA
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits a usage record missing the currency field
    Then the response contains an errors array
    And the error code should be "INVALID_USAGE_DATA"
    And the error field path should reference the missing field
    And the error should include "suggestion" field
    And the suggestion should contain "required fields"
    # INV-4: Required field missing -> INVALID_USAGE_DATA

  @T-UC-017-r143-inv1 @invariant @br-rule-143 @report-usage
  Scenario: BR-RULE-143 INV-1 holds -- valid pricing_option_id accepted
    Given an operator-billed account "acct_001" exists
    And a pricing option "po_lux_cpm" is known for account "acct_001"
    And a valid reporting period
    When the Buyer Agent submits a usage record with pricing_option_id "po_lux_cpm"
    Then the response contains accepted count of 1
    # INV-1: Valid pricing_option_id -> accepted

  @T-UC-017-r143-inv2 @invariant @br-rule-143 @report-usage @error
  Scenario: BR-RULE-143 INV-2 violated -- unknown pricing_option_id rejected
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits a usage record with pricing_option_id "po_does_not_exist"
    Then the error code should be "INVALID_PRICING_OPTION"
    And the error should include "suggestion" field
    # INV-2: Unknown pricing_option_id -> INVALID_PRICING_OPTION

  @T-UC-017-r143-inv3 @invariant @br-rule-143 @report-usage
  Scenario: BR-RULE-143 INV-3 holds -- omitted pricing_option_id accepted without verification
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits a usage record without pricing_option_id
    Then the response contains accepted count of 1
    # INV-3: Omitted -> accepted without rate verification

  @T-UC-017-r144-inv1 @invariant @br-rule-144 @report-usage
  Scenario: BR-RULE-144 INV-1 holds -- percent_of_media pricing with media_spend provided
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    And a pricing option "po_pct_media" with percent_of_media pricing model
    When the Buyer Agent submits a usage record with pricing_option_id "po_pct_media" and media_spend 21000.00
    Then the response contains accepted count of 1
    # INV-1: percent_of_media + media_spend -> accepted

  @T-UC-017-r144-inv1-violated @invariant @br-rule-144 @report-usage @error
  Scenario: BR-RULE-144 INV-1 violated -- percent_of_media pricing without media_spend rejected
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    And a pricing option "po_pct_media" with percent_of_media pricing model
    When the Buyer Agent submits a usage record with pricing_option_id "po_pct_media" but no media_spend
    Then the error code should be "INVALID_USAGE_DATA"
    And the error should include "suggestion" field
    And the suggestion should contain "media_spend required for percent_of_media"
    # INV-1 violated: percent_of_media without media_spend

  @T-UC-017-r144-inv2 @invariant @br-rule-144 @report-usage
  Scenario: BR-RULE-144 INV-2 holds -- non-percent pricing without media_spend accepted
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    And a pricing option "po_cpm" with CPM pricing model
    When the Buyer Agent submits a usage record with pricing_option_id "po_cpm" and no media_spend
    Then the response contains accepted count of 1
    # INV-2: Non-percent model + no media_spend -> accepted

  @T-UC-017-r145-inv1 @invariant @br-rule-145 @report-usage
  Scenario: BR-RULE-145 INV-1 holds -- sandbox account returns sandbox true, no billing
    Given a sandbox account "acct_sandbox" exists
    And a valid reporting period
    When the Buyer Agent submits valid usage records for account "acct_sandbox"
    Then the response contains sandbox flag set to true
    And no billing occurs
    # INV-1: Sandbox account -> sandbox: true, no billing

  @T-UC-017-r145-inv2 @invariant @br-rule-145 @report-usage
  Scenario: BR-RULE-145 INV-2 holds -- production account has no sandbox flag
    Given a production account "acct_prod" exists
    And a valid reporting period
    When the Buyer Agent submits valid usage records for account "acct_prod"
    Then the response does not contain a sandbox flag
    # INV-2: Production account -> sandbox flag absent

  @T-UC-017-r145-inv3 @invariant @br-rule-145 @report-usage
  Scenario: BR-RULE-145 INV-3 holds -- sandbox records still validated normally
    Given a sandbox account "acct_sandbox" exists
    And a valid reporting period
    When the Buyer Agent submits a usage record with negative vendor_cost for sandbox account
    Then the response contains accepted count of 0
    And the response contains errors for the invalid record
    And the response contains sandbox flag set to true
    # INV-3: Sandbox still validates records per normal rules

  @T-UC-017-r043-inv1-fin @invariant @br-rule-043 @get-financials @context-echo
  Scenario: BR-RULE-043 INV-1 holds -- context echoed on get_account_financials success
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_001" exists
    When the Buyer Agent invokes get_account_financials with context {"trace_id": "fin-001"}
    Then the response contains context with trace_id "fin-001"
    # INV-1: Context sent -> context echoed in success

  @T-UC-017-r043-inv1-usage @invariant @br-rule-043 @report-usage @context-echo
  Scenario: BR-RULE-043 INV-1 holds -- context echoed on report_usage success
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent invokes report_usage with valid records and context {"trace_id": "usage-001"}
    Then the response contains context with trace_id "usage-001"
    # INV-1: Context sent -> context echoed in success

  @T-UC-017-r043-inv1-error @invariant @br-rule-043 @get-financials @context-echo @error
  Scenario: BR-RULE-043 INV-1 holds -- context echoed on get_account_financials error
    Given the seller declares account_financials capability as false
    When the Buyer Agent invokes get_account_financials with context {"trace_id": "err-001"}
    Then the response contains context with trace_id "err-001"
    And the error should include "suggestion" field
    # INV-1: Context echoed even on error (POST-F3)

  @T-UC-017-r043-inv2-fin @invariant @br-rule-043 @get-financials @context-echo
  Scenario: BR-RULE-043 INV-2 holds -- no context sent, no context in financials response
    Given the seller declares account_financials capability as true
    And an operator-billed account "acct_001" exists
    When the Buyer Agent invokes get_account_financials without context
    Then the response does not contain a context field
    # INV-2: No context sent -> no context in response

  @T-UC-017-r043-inv2-usage @invariant @br-rule-043 @report-usage @context-echo
  Scenario: BR-RULE-043 INV-2 holds -- no context sent, no context in usage response
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent invokes report_usage with valid records and no context
    Then the response does not contain a context field
    # INV-2: No context sent -> no context in response

  @T-UC-017-r142-inv2 @invariant @br-rule-142 @report-usage
  Scenario: BR-RULE-142 INV-2 holds -- vendor_cost >= 0 accepted
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits a usage record with vendor_cost 0 and currency "USD"
    Then the response contains accepted count of 1
    # INV-2: vendor_cost >= 0 -> accepted

  @T-UC-017-r142-inv2-violated @invariant @br-rule-142 @report-usage @error
  Scenario: BR-RULE-142 INV-2 violated -- vendor_cost negative rejected
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits a usage record with vendor_cost -0.01 and currency "USD"
    Then the error code should be "INVALID_USAGE_DATA"
    And the error should include "suggestion" field
    # INV-2 violated: vendor_cost < 0 -> rejected

  @T-UC-017-r142-inv3 @invariant @br-rule-142 @report-usage
  Scenario: BR-RULE-142 INV-3 holds -- valid ISO 4217 currency accepted
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits a usage record with vendor_cost 100 and currency "EUR"
    Then the response contains accepted count of 1
    # INV-3: Currency matches ^[A-Z]{3}$ -> accepted

  @T-UC-017-r142-inv3-violated @invariant @br-rule-142 @report-usage @error
  Scenario: BR-RULE-142 INV-3 violated -- invalid currency format rejected
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits a usage record with vendor_cost 100 and currency "Us1"
    Then the error code should be "INVALID_USAGE_DATA"
    And the error should include "suggestion" field
    And the suggestion should contain "ISO 4217"
    # INV-3 violated: Currency doesn't match ^[A-Z]{3}$ -> rejected

  @T-UC-017-r144-inv3 @invariant @br-rule-144 @report-usage
  Scenario: BR-RULE-144 INV-3 holds -- media_spend >= 0 accepted
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits a usage record with media_spend 0 and vendor_cost 100 and currency "USD"
    Then the response contains accepted count of 1
    # INV-3: media_spend >= 0 -> accepted

  @T-UC-017-r144-inv3-violated @invariant @br-rule-144 @report-usage @error
  Scenario: BR-RULE-144 INV-3 violated -- negative media_spend rejected
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits a usage record with media_spend -100 and vendor_cost 100
    Then the error code should be "INVALID_USAGE_DATA"
    And the error should include "suggestion" field
    And the suggestion should contain "media_spend must be >= 0"
    # INV-3 violated: media_spend < 0 -> rejected

  @T-UC-017-gap-impressions @extension @ext-c @error @report-usage @gap-fill
  Scenario: Report usage -- negative impressions returns INVALID_USAGE_DATA
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    When the Buyer Agent submits a usage record with impressions of -1
    Then the response contains an errors array
    And the error code should be "INVALID_USAGE_DATA"
    And the error field path should reference "usage[0].impressions"
    And the error should include "suggestion" field
    And the suggestion should contain "impressions must be >= 0"
    # PRE-B11 violation: impressions must be integer >= 0

  @T-UC-017-gap-dup-error @extension @ext-e @report-usage @error @gap-fill
  Scenario: Report usage -- duplicate key with different payload returns DUPLICATE_REQUEST
    Given an operator-billed account "acct_001" exists
    And a valid reporting period
    And a previous report_usage with idempotency_key "key-dup-001" was processed
    When the Buyer Agent submits report_usage with idempotency_key "key-dup-001" but different records
    Then the error code should be "DUPLICATE_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "use a new idempotency_key"
    # Gap fill: DUPLICATE_REQUEST error code needs explicit assertion

  @T-UC-017-part-cap-gate @partition @cap-gate @get-financials
  Scenario Outline: Capability gate partition -- <partition>
    Given an operator-billed account exists
    And the seller capability account_financials is <cap_state>
    When the Buyer Agent invokes get_account_financials
    Then <outcome>

    Examples: Valid partitions
      | partition          | cap_state | outcome                                      |
      | capability_true    | true      | the response contains the success variant     |

    Examples: Invalid partitions
      | partition          | cap_state | outcome                                                   |
      | capability_false   | false     | the error code is "UNSUPPORTED_FEATURE" with suggestion   |
      | capability_absent  | absent    | the error code is "UNSUPPORTED_FEATURE" with suggestion   |

  @T-UC-017-bound-cap-gate @boundary @cap-gate @get-financials
  Scenario Outline: Capability gate boundary -- <boundary_point>
    Given an operator-billed account exists
    And the seller capability account_financials matches the boundary condition
    When the Buyer Agent invokes get_account_financials
    Then <outcome>

    Examples: Boundary values
      | boundary_point      | outcome                                                  |
      | capability=true     | the response contains the success variant                |
      | capability=false    | the error code is "UNSUPPORTED_FEATURE" with suggestion  |
      | capability absent   | the error code is "UNSUPPORTED_FEATURE" with suggestion  |

  @T-UC-017-part-operator @partition @operator-billed @get-financials
  Scenario Outline: Operator billing gate partition -- <partition>
    Given the seller declares account_financials capability as true
    And an account exists with billing type "<billing_type>"
    When the Buyer Agent invokes get_account_financials for that account
    Then <outcome>

    Examples: Valid partitions
      | partition                | billing_type | outcome                                     |
      | operator_billed_account  | operator     | the response contains the success variant    |

    Examples: Invalid partitions
      | partition             | billing_type | outcome                                                        |
      | agent_billed_account  | agent        | the error code is "UNSUPPORTED_FEATURE" with suggestion        |

  @T-UC-017-part-union @partition @financials-union @get-financials
  Scenario Outline: Financials response union partition -- <partition>
    Given the seller declares account_financials capability as true
    And a response scenario matching "<partition>"
    Then <outcome>

    Examples: Valid partitions
      | partition  | outcome                                                      |
      | success    | the response has account, currency, period, timezone; no errors |
      | error      | the response has errors array; no account/currency/period/timezone |

    Examples: Invalid partitions
      | partition        | outcome                                                    |
      | both_present     | schema validation fails (both financial data and errors)   |
      | neither_present  | schema validation fails (neither financial data nor errors) |

  @T-UC-017-part-period @partition @billing-period @get-financials
  Scenario Outline: Billing period partition -- <partition>
    Given the seller declares account_financials capability as true
    And an operator-billed account exists
    When the Buyer Agent invokes get_account_financials with <period_input>
    Then <outcome>

    Examples: Valid partitions
      | partition        | period_input                                     | outcome                                         |
      | explicit_period  | period start "2026-01-01" end "2026-01-31"       | the response period covers the requested range   |
      | omitted_period   | no period parameter                              | the response period defaults to current cycle    |
      | adjusted_period  | period start "2026-01-15" end "2026-02-15"       | the response period may differ from request      |

    Examples: Invalid partitions
      | partition       | period_input                  | outcome                                     |
      | partial_period  | period with start but no end  | the error code is "VALIDATION_ERROR" or "INVALID_REQUEST" |

  @T-UC-017-part-guaranteed @partition @guaranteed-fields @get-financials
  Scenario Outline: Guaranteed fields partition -- <partition>
    Given the seller declares account_financials capability as true
    And an operator-billed account exists with seller providing "<detail_level>"
    When the Buyer Agent invokes get_account_financials
    Then <outcome>

    Examples: Valid partitions
      | partition         | detail_level | outcome                                                |
      | minimal_response  | minimal      | the response has only account, currency, period, timezone |
      | full_response     | full         | the response has all guaranteed fields plus all optional fields |
      | partial_response  | partial      | the response has guaranteed fields plus spend only     |

    Examples: Invalid partitions
      | partition         | detail_level    | outcome                                          |
      | missing_account   | no_account      | schema validation fails (account absent)         |
      | missing_currency  | no_currency     | schema validation fails (currency absent)        |

  @T-UC-017-part-credit @partition @credit-conditionality @get-financials
  Scenario Outline: Credit conditionality partition -- <partition>
    Given the seller declares account_financials capability as true
    And an operator-billed account with "<payment_type>" payment terms
    When the Buyer Agent invokes get_account_financials
    Then <outcome>

    Examples: Valid partitions
      | partition              | payment_type | outcome                                                           |
      | credit_account_full    | net_30       | the credit section has credit_limit, available_credit, utilization_percent |
      | credit_account_minimal | net_30       | the credit section has credit_limit and available_credit only     |
      | non_credit_account     | prepay       | the credit section is absent                                     |

    Examples: Invalid partitions
      | partition              | payment_type | outcome                                                    |
      | credit_missing_limit   | net_30       | schema validation fails (credit section without credit_limit) |

  @T-UC-017-part-balance @partition @balance-conditionality @get-financials
  Scenario Outline: Balance conditionality partition -- <partition>
    Given the seller declares account_financials capability as true
    And an operator-billed account with "<payment_type>" payment terms
    When the Buyer Agent invokes get_account_financials
    Then <outcome>

    Examples: Valid partitions
      | partition            | payment_type | outcome                                                  |
      | prepay_with_topup    | prepay       | the balance section has available and last_top_up         |
      | prepay_minimal       | prepay       | the balance section has available only                    |
      | non_prepay_account   | net_30       | the balance section is absent                             |

    Examples: Invalid partitions
      | partition                | payment_type | outcome                                                     |
      | balance_missing_available | prepay       | schema validation fails (balance section without available)  |

  @T-UC-017-part-partial @partition @partial-acceptance @report-usage
  Scenario Outline: Partial acceptance partition -- <partition>
    Given a valid reporting period
    And operator-billed accounts exist for usage records
    When the Buyer Agent submits usage records matching "<partition>" scenario
    Then <outcome>

    Examples: Valid partitions
      | partition      | outcome                                                |
      | all_accepted   | the response has accepted = total, no errors           |
      | partial        | the response has accepted > 0 and errors array present |
      | all_rejected   | the response has accepted = 0 and errors for each record |

    Examples: Invalid partitions
      | partition         | outcome                                             |
      | missing_accepted  | schema validation fails (no accepted count)          |

  @T-UC-017-part-idempotency @partition @usage-idempotency @report-usage
  Scenario Outline: Idempotency partition -- <partition>
    Given a valid reporting period and operator-billed account exists
    When the Buyer Agent submits report_usage with <key_scenario>
    Then <outcome>

    Examples: Valid partitions
      | partition   | key_scenario                           | outcome                                     |
      | new_key     | a new idempotency_key                  | request processed normally                   |
      | no_key      | no idempotency_key                     | request processed (not idempotent)           |
      | replay_key  | a previously used idempotency_key      | original response returned without re-processing |

    Examples: Invalid partitions
      | partition                    | key_scenario                           | outcome                                         |
      | reused_key_different_payload | same key with different request body   | DUPLICATE_REQUEST error with suggestion          |

  @T-UC-017-part-self-contained @partition @self-contained-records @report-usage
  Scenario Outline: Self-contained records partition -- <partition>
    Given a valid reporting period
    When the Buyer Agent submits usage records matching "<partition>"
    Then <outcome>

    Examples: Valid partitions
      | partition       | outcome                                     |
      | single_account  | all records accepted for same account        |
      | multi_account   | records accepted spanning multiple accounts  |
      | multi_campaign  | records accepted spanning multiple campaigns |

    Examples: Invalid partitions
      | partition        | outcome                                          |
      | missing_account  | schema validation error (record without account)  |

  @T-UC-017-part-required @partition @required-fields @report-usage
  Scenario Outline: Usage record required fields partition -- <partition>
    Given an operator-billed account exists
    And a valid reporting period
    When the Buyer Agent submits a usage record matching "<partition>"
    Then <outcome>

    Examples: Valid partitions
      | partition       | outcome                       |
      | minimal_record  | accepted (3 required fields)  |
      | full_record     | accepted (all fields)         |

    Examples: Invalid partitions
      | partition              | outcome                                          |
      | missing_vendor_cost    | INVALID_USAGE_DATA error with suggestion          |
      | missing_currency       | INVALID_USAGE_DATA error with suggestion          |
      | invalid_currency_format | INVALID_USAGE_DATA error with suggestion          |
      | negative_vendor_cost   | INVALID_USAGE_DATA error with suggestion          |

  @T-UC-017-part-pricing @partition @pricing-verification @report-usage
  Scenario Outline: Pricing option verification partition -- <partition>
    Given an operator-billed account exists
    And a valid reporting period
    When the Buyer Agent submits a usage record matching "<partition>"
    Then <outcome>

    Examples: Valid partitions
      | partition            | outcome                                         |
      | valid_pricing_option | accepted (pricing option verified)               |
      | omitted              | accepted (no rate verification)                  |

    Examples: Invalid partitions
      | partition       | outcome                                          |
      | unknown_option  | INVALID_PRICING_OPTION error with suggestion      |

  @T-UC-017-part-media-spend @partition @media-spend-percent @report-usage
  Scenario Outline: Media spend conditional requirement partition -- <partition>
    Given an operator-billed account exists
    And a valid reporting period
    When the Buyer Agent submits a usage record matching "<partition>"
    Then <outcome>

    Examples: Valid partitions
      | partition                    | outcome                                         |
      | percent_model_with_spend     | accepted (media_spend verified)                  |
      | non_percent_model_omitted    | accepted (media_spend not required)              |
      | non_percent_model_provided   | accepted (media_spend optional but provided)     |

    Examples: Invalid partitions
      | partition                      | outcome                                          |
      | percent_model_without_spend    | INVALID_USAGE_DATA error with suggestion          |
      | negative_media_spend           | INVALID_USAGE_DATA error with suggestion          |

  @T-UC-017-part-sandbox @partition @sandbox-usage @report-usage
  Scenario Outline: Sandbox mode partition -- <partition>
    Given a valid reporting period
    And an account matching "<partition>" scenario
    When the Buyer Agent submits valid usage records
    Then <outcome>

    Examples: Valid partitions
      | partition           | outcome                                     |
      | sandbox_account     | accepted with sandbox: true, no billing      |
      | production_account  | accepted without sandbox flag                |
      | sandbox_with_errors | partial acceptance with sandbox: true         |

  @T-UC-017-bound-operator @boundary @operator-billed @get-financials
  Scenario Outline: Operator billing gate boundary -- <boundary_point>
    Given the seller declares account_financials capability as true
    And an account matching the boundary condition
    When the Buyer Agent invokes get_account_financials
    Then <outcome>

    Examples: Boundary values
      | boundary_point           | outcome                                     |
      | operator-billed account  | the response contains the success variant    |
      | agent-billed account     | the error code is "UNSUPPORTED_FEATURE" with suggestion |

  @T-UC-017-bound-union @boundary @financials-union @get-financials
  Scenario Outline: Financials union boundary -- <boundary_point>
    Given a response scenario matching the boundary condition
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                           | outcome                                                      |
      | account+currency+period+timezone present, no errors      | valid success response                                       |
      | errors present, no account/currency/period/timezone      | valid error response                                         |
      | both account and errors present                          | schema validation fails                                      |
      | neither account/currency/period/timezone nor errors      | schema validation fails                                      |

  @T-UC-017-bound-period @boundary @billing-period @get-financials
  Scenario Outline: Billing period boundary -- <boundary_point>
    Given the seller declares account_financials capability as true
    And an operator-billed account exists
    When the Buyer Agent invokes get_account_financials with the boundary condition
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                  | outcome                                     |
      | period omitted (default to current cycle)       | valid response with current billing cycle    |
      | period with valid start and end                 | valid response with actual period            |
      | response period adjusted to billing boundaries  | valid response with adjusted period          |
      | period with only start (missing end)            | validation error                             |

  @T-UC-017-bound-guaranteed @boundary @guaranteed-fields @get-financials
  Scenario Outline: Guaranteed fields boundary -- <boundary_point>
    Given the seller declares account_financials capability as true
    And a response scenario matching the boundary condition
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                    | outcome                                         |
      | all four guaranteed fields present, no optional   | valid minimal response                           |
      | all guaranteed + all optional fields              | valid full response                              |
      | guaranteed + spend only                           | valid partial response                           |
      | missing account (one guaranteed absent)           | schema validation fails                          |
      | missing currency (one guaranteed absent)          | schema validation fails                          |

  @T-UC-017-bound-credit @boundary @credit-conditionality @get-financials
  Scenario Outline: Credit conditionality boundary -- <boundary_point>
    Given the seller declares account_financials capability as true
    And a response scenario matching the boundary condition
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                    | outcome                                     |
      | credit section present with all fields            | valid (credit_limit, available_credit, utilization_percent) |
      | credit section present without utilization_percent | valid (utilization_percent is optional)       |
      | credit section absent (prepay account)            | valid (no credit for prepay)                 |
      | credit section present but missing credit_limit   | schema validation fails                      |
      | utilization_percent at boundary 0                 | valid (minimum utilization)                  |
      | utilization_percent at boundary 100               | valid (maximum utilization)                  |

  @T-UC-017-bound-balance @boundary @balance-conditionality @get-financials
  Scenario Outline: Balance conditionality boundary -- <boundary_point>
    Given the seller declares account_financials capability as true
    And a response scenario matching the boundary condition
    Then <outcome>

    Examples: Boundary values
      | boundary_point                             | outcome                                     |
      | balance present with available and last_top_up | valid (full balance section)                |
      | balance present with only available         | valid (last_top_up optional)                 |
      | balance section absent (credit account)     | valid (no balance for credit)                |
      | balance present but missing available       | schema validation fails                      |
      | available at boundary 0                     | valid (zero remaining balance)               |

  @T-UC-017-bound-partial @boundary @partial-acceptance @report-usage
  Scenario Outline: Partial acceptance boundary -- <boundary_point>
    Given a valid reporting period
    And a response scenario matching the boundary condition
    Then <outcome>

    Examples: Boundary values
      | boundary_point                         | outcome                                     |
      | accepted = total records, no errors    | valid (all accepted)                         |
      | accepted > 0 and errors present        | valid (partial acceptance)                   |
      | accepted = 0, errors present           | valid (all rejected)                         |
      | accepted field missing                 | schema validation fails                      |

  @T-UC-017-bound-idempotency @boundary @usage-idempotency @report-usage
  Scenario Outline: Idempotency boundary -- <boundary_point>
    Given a valid reporting period and operator-billed account exists
    When the Buyer Agent submits report_usage matching the boundary condition
    Then <outcome>

    Examples: Boundary values
      | boundary_point                         | outcome                                     |
      | new idempotency key (first submission) | processed normally                           |
      | no idempotency key provided            | processed (not idempotent)                   |
      | same key same payload (retry)          | original response returned                   |
      | same key different payload             | DUPLICATE_REQUEST error with suggestion       |

  @T-UC-017-bound-self-contained @boundary @self-contained-records @report-usage
  Scenario Outline: Self-contained records boundary -- <boundary_point>
    Given a valid reporting period
    When the Buyer Agent submits usage records matching the boundary condition
    Then <outcome>

    Examples: Boundary values
      | boundary_point                       | outcome                                     |
      | all records same account             | all accepted for same account                |
      | records spanning two accounts        | all accepted across accounts                 |
      | records spanning two campaigns       | all accepted across campaigns                |
      | record missing account field         | validation error (account required per record) |

  @T-UC-017-bound-required @boundary @required-fields @report-usage
  Scenario Outline: Usage record required fields boundary -- <boundary_point>
    Given an operator-billed account exists
    And a valid reporting period
    When the Buyer Agent submits a usage record matching the boundary condition
    Then <outcome>

    Examples: Boundary values
      | boundary_point                             | outcome                                          |
      | vendor_cost = 0 (boundary minimum)         | accepted (zero cost is valid)                    |
      | vendor_cost = -1 (below minimum)           | INVALID_USAGE_DATA error with suggestion          |
      | currency = 'USD' (valid 3-letter uppercase) | accepted                                          |
      | currency = 'usd' (lowercase)               | INVALID_USAGE_DATA error with suggestion          |
      | currency = 'US' (2 letters)                | INVALID_USAGE_DATA error with suggestion          |
      | all three required fields present          | accepted                                          |
      | vendor_cost missing                        | INVALID_USAGE_DATA error with suggestion          |

  @T-UC-017-bound-pricing @boundary @pricing-verification @report-usage
  Scenario Outline: Pricing option verification boundary -- <boundary_point>
    Given an operator-billed account exists
    And a valid reporting period
    When the Buyer Agent submits a usage record matching the boundary condition
    Then <outcome>

    Examples: Boundary values
      | boundary_point                              | outcome                                         |
      | valid pricing_option_id (known to vendor)   | accepted                                         |
      | pricing_option_id omitted                   | accepted (no rate verification)                  |
      | pricing_option_id not found on account      | INVALID_PRICING_OPTION error with suggestion      |

  @T-UC-017-bound-media-spend @boundary @media-spend-percent @report-usage
  Scenario Outline: Media spend boundary -- <boundary_point>
    Given an operator-billed account exists
    And a valid reporting period
    When the Buyer Agent submits a usage record matching the boundary condition
    Then <outcome>

    Examples: Boundary values
      | boundary_point                             | outcome                                         |
      | media_spend = 0 (boundary minimum)         | accepted (zero media spend is valid)             |
      | media_spend = -1 (below minimum)           | INVALID_USAGE_DATA error with suggestion          |
      | percent_of_media with media_spend present  | accepted                                          |
      | percent_of_media without media_spend       | INVALID_USAGE_DATA error with suggestion          |
      | CPM model without media_spend              | accepted (media_spend not required)               |

  @T-UC-017-bound-sandbox @boundary @sandbox-usage @report-usage
  Scenario Outline: Sandbox mode boundary -- <boundary_point>
    Given a valid reporting period
    And an account matching the boundary condition
    When the Buyer Agent submits valid usage records
    Then <outcome>

    Examples: Boundary values
      | boundary_point                     | outcome                                     |
      | sandbox: true (sandbox account)    | accepted with sandbox: true, no billing      |
      | sandbox field absent (production)  | accepted without sandbox flag                |
      | sandbox: true with errors          | partial acceptance with sandbox: true         |

