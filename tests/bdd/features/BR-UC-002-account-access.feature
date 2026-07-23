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
  # BUYER receives, so it is graded on the real wire envelope here, not on an
  # envelope rebuilt in-process from a caught exception.
  #
  # Graded on A2A and MCP, not REST. Each claim below was checked against the
  # harness, not assumed:
  #   - A2A carries the full auth chain in-process
  #     (_get_auth_token -> _resolve_a2a_identity -> resolve_identity) and returns
  #     a real two-layer wire envelope, so the A2A scenario grades the ENVELOPE.
  #   - MCP rejects the invalid token too, but as a bare `ToolError` with no
  #     two-layer envelope (wire_error_envelope is None), because the rejection is
  #     raised outside the tool boundary that builds the envelope. There is no
  #     envelope to grade, but the ToolError MESSAGE is what the buyer receives, so
  #     the MCP scenario grades the MESSAGE — a deliberately weaker pin for the
  #     same non-disclosure contract. That MCP auth rejection reaching the buyer
  #     without a structured envelope is a separate production gap, tracked in
  #     issue #1704.
  #   - REST: the in-process harness overrides `_require_auth_dep` with the
  #     injected identity (tests/harness/_base.py::_configure_rest_auth_override),
  #     so the invalid-token raise site is never reached — the request proceeds as
  #     authenticated. A REST variant would grade nothing, so there is none.
  @T-UC-002-invalid-token-no-disclosure @account @error @a2a
  Scenario: An invalid token is rejected without disclosing the tenant id
    Given a valid create_media_buy request with account natural key brand "leak-brand.com" operator "leak-agency.com"
    And the Buyer Agent presents an invalid authentication token
    When the Buyer Agent sends the create_media_buy request via A2A
    Then the rejection reaches the buyer as a real "AUTH_REQUIRED" wire envelope
    And the error discloses no tenant id

  # MCP grades the same non-disclosure contract on the ToolError message, since
  # MCP rejects without a two-layer envelope (see the note above). Weaker than the
  # A2A envelope pin, but it covers the transport A2A does not.
  @T-UC-002-invalid-token-no-disclosure-mcp @account @error @mcp
  Scenario: An invalid token is rejected over MCP without disclosing the tenant id
    Given a valid create_media_buy request with account natural key brand "leak-brand.com" operator "leak-agency.com"
    And the Buyer Agent presents an invalid authentication token
    When the Buyer Agent sends the create_media_buy request via MCP
    Then the error message discloses no tenant id
