# Hand-authored feature — account-access scoping for UC-002 natural-key resolution.
#
# salesagent-ym1c: natural-key account resolution (ambiguity detection + the
# disclosed ACCOUNT_AMBIGUOUS count) must be scoped to the requesting agent's
# accessible accounts (AgentAccountAccess), never the tenant-wide set — otherwise
# an agent learns of accounts it cannot access (info leak).
#
# @account routes through the wired UC-002 account-resolution harness branch
# (MediaBuyCreateEnv + full create), so these run on a2a/mcp/rest without
# additional conftest wiring.

@analysis-2026-06-25 @schema-v3.1
Feature: BR-UC-002 Account access scoping
  Natural-key account resolution is scoped to the requesting agent's accessible
  accounts so ambiguity and its disclosed count never reveal inaccessible accounts.

  @T-UC-002-ym1c-access-scope @account @error
  Scenario: Natural-key ambiguity is scoped to the agent's accessible accounts
    Given a valid create_media_buy request with account natural key brand "shared-brand.com" operator "shared-agency.com"
    And the natural key matches 2 accounts but the agent can access 1
    When the Buyer Agent sends the create_media_buy request
    Then the result should be success

  # salesagent-fb2l: an unauthenticated caller (tenant resolved, no principal) must be
  # rejected with AUTH_REQUIRED at the account-resolution boundary — it must never reach
  # natural-key resolution, which would disclose the tenant-wide match count (info leak).
  @T-UC-002-fb2l-unauth-no-disclosure @account @error
  Scenario: Unauthenticated natural-key resolution discloses no account information
    Given a valid create_media_buy request with account natural key brand "leak-brand.com" operator "leak-agency.com"
    And the natural key matches 2 accounts
    And the Buyer Agent's token resolves no principal
    When the Buyer Agent sends the create_media_buy request
    Then the result should be error "AUTH_REQUIRED"
