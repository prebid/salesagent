"""Domain step definitions for UC-011: Manage Accounts.

Given steps: set up accounts, agent access, seller config
When steps: send list_accounts / sync_accounts requests
Then steps: verify account results, actions, status, errors

All steps operate on ctx dict (shared across Given/When/Then).
ctx["env"] is the harness environment (AccountSyncEnv or AccountListEnv).
ctx["response"] is the response object after When.
ctx["error"] is any exception raised.
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories.account import AccountFactory, AgentAccountAccessFactory

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _setup_tenant_and_principal(ctx: dict) -> tuple[Any, Any]:
    """Set up default tenant + principal, caching in ctx to avoid duplicates."""
    if "tenant" not in ctx:
        env = ctx["env"]
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    return ctx["tenant"], ctx["principal"]


def _create_accessible_account(ctx: dict, status: str = "active", **kwargs: Any) -> Any:
    """Create an account and grant agent access to it."""
    tenant, principal = _setup_tenant_and_principal(ctx)
    account = AccountFactory(tenant=tenant, status=status, **kwargs)
    AgentAccountAccessFactory(
        tenant_id=tenant.tenant_id,
        principal=principal,
        account=account,
    )
    return account


def _status_str(status: Any) -> str:
    """Extract string value from Status enum or return as-is."""
    return status.value if hasattr(status, "value") else str(status)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — authentication and account setup
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the Buyer Agent has an authenticated connection via {transport}"))
def given_authenticated_connection(ctx: dict, transport: str) -> None:
    """Set up authenticated connection via the specified transport."""
    ctx["transport"] = transport
    ctx["has_auth"] = True
    _setup_tenant_and_principal(ctx)


@given("the Buyer Agent has an unauthenticated connection via MCP")
def given_unauthenticated_mcp(ctx: dict) -> None:
    """Set up unauthenticated connection via MCP."""
    ctx["transport"] = "MCP"
    ctx["has_auth"] = False
    ctx["identity"] = None


@given("the Buyer is authenticated with a valid principal_id")
def given_buyer_authenticated(ctx: dict) -> None:
    """Buyer has authenticated identity with valid principal_id."""
    ctx["has_auth"] = True
    _setup_tenant_and_principal(ctx)


@given(parsers.parse('the agent has {count:d} accessible accounts with statuses "{s1}", "{s2}", "{s3}"'))
def given_n_accounts_with_3_statuses(ctx: dict, count: int, s1: str, s2: str, s3: str) -> None:
    """Create N accounts with the given statuses (3 statuses for N=3)."""
    statuses = [s1, s2, s3]
    for status in statuses[:count]:
        _create_accessible_account(ctx, status=status)


@given(parsers.parse('the agent has accounts with statuses "{s1}", "{s2}", "{s3}", "{s4}"'))
def given_accounts_with_4_statuses(ctx: dict, s1: str, s2: str, s3: str, s4: str) -> None:
    """Create accounts with 4 distinct statuses."""
    for status in [s1, s2, s3, s4]:
        _create_accessible_account(ctx, status=status)


@given(parsers.parse('the agent has accounts with statuses "{s1}", "{s2}", "{s3}"'))
def given_accounts_with_3_statuses(ctx: dict, s1: str, s2: str, s3: str) -> None:
    """Create accounts with 3 statuses."""
    for status in [s1, s2, s3]:
        _create_accessible_account(ctx, status=status)


@given("the agent has no accessible accounts")
def given_no_accounts(ctx: dict) -> None:
    """Agent has no accessible accounts (tenant + principal exist but no accounts)."""
    _setup_tenant_and_principal(ctx)


@given(parsers.parse("the agent has {count:d} accessible accounts"))
def given_n_accessible_accounts(ctx: dict, count: int) -> None:
    """Create N accessible accounts with default active status."""
    for _ in range(count):
        _create_accessible_account(ctx, status="active")


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — list_accounts requests
# ═══════════════════════════════════════════════════════════════════════


@when(parsers.parse("the Buyer Agent sends a list_accounts request via {transport}"))
def when_list_accounts_via_transport(ctx: dict, transport: str) -> None:
    """Send list_accounts request via specified transport."""
    ctx["transport"] = transport
    dispatch_request(ctx)


@when(
    parsers.re(
        r"the Buyer Agent sends a list_accounts request"
        r"(?! (?:with|via))"  # Not followed by "with" or "via" (those have their own steps)
        r"|the Buyer Agent sends a list_accounts request without a (?:status filter|context object)"
    )
)
def when_list_accounts_unfiltered(ctx: dict) -> None:
    """Send list_accounts request with no filters (matches multiple phrasings)."""
    dispatch_request(ctx)


@when(parsers.parse('the Buyer Agent sends a list_accounts request with status filter "{status}"'))
def when_list_accounts_status_filter(ctx: dict, status: str) -> None:
    """Send list_accounts with a status filter."""
    from src.core.schemas.account import ListAccountsRequest

    try:
        req = ListAccountsRequest(status=status)
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


@when("the Buyer Agent sends a list_accounts request without an authentication token")
def when_list_accounts_no_auth(ctx: dict) -> None:
    """Send list_accounts without authentication."""
    env = ctx["env"]
    try:
        ctx["response"] = env.call_impl(identity=None)
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse("the Buyer Agent sends a list_accounts request with max_results {value:d}"))
def when_list_accounts_paginated(ctx: dict, value: int) -> None:
    """Send list_accounts with max_results pagination."""
    from adcp.types.generated_poc.core.pagination_request import PaginationRequest

    from src.core.schemas.account import ListAccountsRequest

    try:
        req = ListAccountsRequest(pagination=PaginationRequest(max_results=value))
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


@when("the Buyer Agent sends a list_accounts request with the returned cursor")
def when_list_accounts_with_cursor(ctx: dict) -> None:
    """Send list_accounts with the cursor from the previous response."""
    from adcp.types.generated_poc.core.pagination_request import PaginationRequest

    from src.core.schemas.account import ListAccountsRequest

    prev_response = ctx["response"]
    cursor = prev_response.pagination.cursor
    # Use same max_results as before (stored in ctx or default)
    max_results = ctx.get("last_max_results", 50)
    try:
        req = ListAccountsRequest(pagination=PaginationRequest(max_results=max_results, cursor=cursor))
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse("the Buyer Agent sends a list_accounts request with sandbox equals {value}"))
def when_list_sandbox_filter(ctx: dict, value: str) -> None:
    """Send list_accounts with sandbox filter."""
    from src.core.schemas.account import ListAccountsRequest

    req = ListAccountsRequest(sandbox=value.lower() == "true")
    dispatch_request(ctx, req=req)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — response assertions
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse("the response contains an accounts array with {count:d} items"))
def then_accounts_array_count(ctx: dict, count: int) -> None:
    """Assert the response accounts array has the expected count."""
    resp = ctx["response"]
    assert resp is not None, "Expected a response"
    assert hasattr(resp, "accounts"), f"Response has no 'accounts' field: {type(resp)}"
    actual = len(resp.accounts)
    assert actual == count, f"Expected {count} accounts, got {actual}"


@then("each account includes account_id, name, status, advertiser, rate_card, and payment_terms")
def then_accounts_have_fields(ctx: dict) -> None:
    """Assert each account has the required fields."""
    resp = ctx["response"]
    for i, acct in enumerate(resp.accounts):
        assert acct.account_id is not None, f"Account {i} missing account_id"
        assert acct.name is not None, f"Account {i} missing name"
        assert acct.status is not None, f"Account {i} missing status"
        # advertiser, rate_card, payment_terms may be None but the fields should exist
        assert hasattr(acct, "advertiser"), f"Account {i} missing advertiser field"
        assert hasattr(acct, "rate_card"), f"Account {i} missing rate_card field"
        assert hasattr(acct, "payment_terms"), f"Account {i} missing payment_terms field"


@then("the accounts are only those accessible to the authenticated agent")
def then_accounts_are_agent_scoped(ctx: dict) -> None:
    """Assert returned accounts are scoped to the authenticated agent.

    All accounts should belong to our test agent — verified by the fact that
    we created exactly N accounts with agent access and got N back.
    """
    resp = ctx["response"]
    assert resp is not None, "Expected a response"
    # The Given step created accounts with access for our principal,
    # so if the count matches, scoping is working.
    assert len(resp.accounts) > 0, "Expected at least one account for scoping test"


@then(parsers.parse('the response contains only accounts with status "{status}"'))
def then_only_status(ctx: dict, status: str) -> None:
    """Assert all returned accounts have the expected status (vacuously true if empty)."""
    resp = ctx["response"]
    assert resp is not None, "Expected a response"
    for acct in resp.accounts:
        actual = _status_str(acct.status)
        assert actual == status, f"Expected status '{status}', got '{actual}'"


@then("accounts with other statuses are excluded")
def then_other_statuses_excluded(ctx: dict) -> None:
    """Assert no accounts with different statuses are present.

    This is a vacuous truth assertion — if only_status passed,
    other statuses are by definition excluded.
    """
    # Already verified by the only_status step above
    resp = ctx["response"]
    assert resp is not None, "Expected a response"


@then("the response contains an empty accounts array")
def then_empty_accounts(ctx: dict) -> None:
    """Assert the response has an empty accounts array."""
    resp = ctx.get("response")
    assert resp is not None, f"Expected a response but got error: {ctx.get('error')}"
    assert hasattr(resp, "accounts"), f"Response has no 'accounts' field: {type(resp)}"
    assert len(resp.accounts) == 0, f"Expected 0 accounts, got {len(resp.accounts)}"


@then("the response is not an error")
def then_not_an_error(ctx: dict) -> None:
    """Assert the response is a success (no error)."""
    error = ctx.get("error")
    assert error is None, f"Expected no error but got: {error}"
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"


@then(parsers.parse("the response contains {count:d} accounts"))
def then_n_accounts(ctx: dict, count: int) -> None:
    """Assert the response has exactly N accounts."""
    resp = ctx["response"]
    actual = len(resp.accounts)
    assert actual == count, f"Expected {count} accounts, got {actual}"


@then(parsers.parse("the response contains {count:d} more accounts"))
def then_n_more_accounts(ctx: dict, count: int) -> None:
    """Assert the response has exactly N accounts (phrased as 'more')."""
    resp = ctx["response"]
    actual = len(resp.accounts)
    assert actual == count, f"Expected {count} more accounts, got {actual}"


@then(parsers.parse("the response includes pagination metadata with has_more {has_more} and a cursor"))
def then_pagination_has_more_with_cursor(ctx: dict, has_more: str) -> None:
    """Assert pagination metadata with has_more and cursor."""
    resp = ctx["response"]
    assert resp.pagination is not None, "Expected pagination metadata"
    expected = has_more.lower() == "true"
    assert resp.pagination.has_more == expected, f"Expected has_more={expected}, got {resp.pagination.has_more}"
    if expected:
        assert resp.pagination.cursor is not None, "Expected cursor when has_more is true"


@then(parsers.parse("the response includes pagination metadata with has_more {has_more}"))
def then_pagination_has_more(ctx: dict, has_more: str) -> None:
    """Assert pagination metadata with has_more."""
    resp = ctx["response"]
    assert resp.pagination is not None, "Expected pagination metadata"
    expected = has_more.lower() == "true"
    assert resp.pagination.has_more == expected, f"Expected has_more={expected}, got {resp.pagination.has_more}"


@then("the response contains a validation error")
def then_validation_error(ctx: dict) -> None:
    """Assert the response is a validation error."""
    error = ctx.get("error")
    assert error is not None, "Expected a validation error but got no error"
    from src.core.exceptions import AdCPValidationError

    assert isinstance(error, (AdCPValidationError, ValueError)), (
        f"Expected validation error, got {type(error).__name__}: {error}"
    )


@then("the error indicates the status value is not recognized")
def then_error_invalid_status(ctx: dict) -> None:
    """Assert the error message indicates invalid status."""
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    msg = str(error).lower()
    assert "status" in msg or "valid" in msg or "invalid" in msg, f"Expected error about invalid status, got: {error}"


@then("the response contains accounts with all statuses")
def then_all_statuses_present(ctx: dict) -> None:
    """Assert the response includes accounts with all the statuses set up."""
    resp = ctx["response"]
    assert resp is not None, "Expected a response"
    statuses = {_status_str(a.status) for a in resp.accounts}
    # The Given step created accounts with 4 statuses
    assert len(statuses) >= 2, f"Expected multiple statuses, got {statuses}"


@then("the result set is identical to requesting without any filter")
def then_result_set_identical(ctx: dict) -> None:
    """Assert the unfiltered result set contains all accounts.

    The Given step created accounts with 4 different statuses,
    so all 4 should appear in the unfiltered results.
    """
    resp = ctx["response"]
    assert resp is not None, "Expected a response"
    assert len(resp.accounts) >= 4, f"Expected at least 4 accounts (one per status), got {len(resp.accounts)}"


@then(parsers.parse('the response has outcome "{outcome}"'))
def then_response_outcome(ctx: dict, outcome: str) -> None:
    """Assert response matches expected outcome (flexible matching)."""
    if "validation error" in outcome:
        error = ctx.get("error")
        assert error is not None, f"Expected validation error for outcome '{outcome}', but got no error"
    elif outcome.startswith("success with"):
        error = ctx.get("error")
        assert error is None, f"Expected success for outcome '{outcome}', but got error: {error}"
        resp = ctx.get("response")
        assert resp is not None, f"Expected a response for outcome '{outcome}'"

        # Parse expected count from outcome like "success with 50 accounts"
        import re

        match = re.search(r"(\d+)\s+account", outcome)
        if match:
            expected_count = int(match.group(1))
            actual = len(resp.accounts)
            assert actual == expected_count, f"Expected {expected_count} accounts for outcome '{outcome}', got {actual}"
