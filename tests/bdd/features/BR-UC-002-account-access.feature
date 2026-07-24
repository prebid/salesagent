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
    And the resolved account is the one the agent can access

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

  # A present-but-invalid token is rejected with a GENERIC message: the tenant id
  # is resolved from the request headers BEFORE the token is validated, so echoing
  # it back hands an unauthenticated caller an internal identifier (the tenant
  # UUID in a host-routed deploy). Non-disclosure is a contract about what the
  # BUYER receives, so it is graded on the real wire, not on an envelope rebuilt
  # in-process from a caught exception.
  #
  # ONE transport-agnostic scenario, swept across a2a/mcp/rest (+e2e_rest) by
  # pytest_generate_tests. Each transport reaches the same production
  # reject_invalid_token raise; what it can carry back differs, and the single
  # Then step grades accordingly:
  #   - a2a / rest return a real two-layer envelope -> the Then asserts AUTH_REQUIRED
  #     on the real wire (require_real_wire) plus non-disclosure on the envelope.
  #     (REST authenticates in-process by dependency override, which would skip the
  #     raise; the harness routes the bad token through the REAL dep as headers so
  #     REST reaches it — see _dispatch_full_create / _run_rest_request.)
  #   - mcp raises a bare ToolError with no two-layer envelope -> the Then asserts
  #     the message equals INVALID_TOKEN_MESSAGE plus non-disclosure on the message.
  #     That missing MCP auth envelope is a separate production gap, tracked in #1704.
  @T-UC-002-invalid-token-no-disclosure @account @error
  Scenario: An invalid token is rejected without disclosing the tenant id
    Given a valid create_media_buy request with account natural key brand "leak-brand.com" operator "leak-agency.com"
    And the Buyer Agent presents an invalid authentication token
    When the Buyer Agent sends the create_media_buy request
    Then the invalid token is rejected without disclosing the tenant id
