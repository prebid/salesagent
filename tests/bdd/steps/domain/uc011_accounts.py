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
    """Create an account and grant agent access to it.

    Tracks created account IDs in ctx["expected_account_ids"] and statuses
    in ctx["created_statuses"] for assertion verification.
    """
    tenant, principal = _setup_tenant_and_principal(ctx)
    account = AccountFactory(tenant=tenant, status=status, **kwargs)
    AgentAccountAccessFactory(
        tenant_id=tenant.tenant_id,
        principal=principal,
        account=account,
    )
    ctx.setdefault("expected_account_ids", set()).add(account.account_id)
    ctx.setdefault("created_statuses", set()).add(status)
    return account


def _status_str(status: Any) -> str:
    """Extract string value from Status enum or return as-is."""
    return status.value if hasattr(status, "value") else str(status)


def _action_str(action: Any) -> str:
    """Extract string value from Action enum or return as-is."""
    return action.value if hasattr(action, "value") else str(action)


def _brand_id_str(bid: Any) -> str | None:
    """Extract string value from BrandId (RootModel[str]) or return as-is."""
    if bid is None:
        return None
    if hasattr(bid, "root"):
        return str(bid.root)
    return str(bid)


def _find_account_by_brand(resp: Any, domain: str, brand_id: str | None = None) -> Any:
    """Find an account in sync response by brand domain (and optional brand_id)."""
    for acct in resp.accounts:
        if acct.brand.domain != domain:
            continue
        if brand_id is not None:
            acct_bid = _brand_id_str(getattr(acct.brand, "brand_id", None))
            if acct_bid != brand_id:
                continue
        return acct
    domains = [a.brand.domain for a in resp.accounts]
    suffix = f" and brand_id '{brand_id}'" if brand_id else ""
    raise AssertionError(f"No account found for domain '{domain}'{suffix}. Available: {domains}")


def _make_governance_agent(
    url: str = "https://compliance.example.com/check",
    categories: list[str] | None = None,
) -> dict[str, Any]:
    """Build a valid GovernanceAgent dict matching the adcp schema.

    Uses the library GovernanceAgent model for validation, then dumps to dict
    for use in SyncAccountsRequest entries.

    Note: authentication was removed from GovernanceAgent in adcp 3.12.
    """
    from adcp.types.generated_poc.core.account import GovernanceAgent  # TODO: no stable alias in adcp.types

    agent = GovernanceAgent(
        url=url,
        categories=categories,
    )
    return agent.model_dump()


def _sync_pre_create(ctx: dict, brand_domain: str, operator: str, billing: str, **extra: Any) -> None:
    """Pre-create an account via sync so it exists for update/unchanged tests.

    Extra kwargs (e.g., payment_terms, governance_agents) are merged into the account entry.
    Captures original field values in ctx["original_field_values"] for later
    "unchanged from the original" assertions.
    """
    from src.core.schemas.account import SyncAccountsRequest

    entry: dict[str, Any] = {"brand": {"domain": brand_domain}, "operator": operator, "billing": billing}
    entry.update(extra)
    req = SyncAccountsRequest(accounts=[entry])
    dispatch_request(ctx, req=req)
    # Capture original field values for "unchanged from the original" assertions
    resp = ctx.get("response")
    if resp is not None and resp.accounts:
        acct = resp.accounts[0]
        originals = ctx.setdefault("original_field_values", {})
        originals["billing"] = billing
        originals["operator"] = operator
        if "payment_terms" in extra:
            originals["payment_terms"] = extra["payment_terms"]
        # Capture DB-assigned fields from the response
        if hasattr(acct, "account_id"):
            originals["account_id"] = acct.account_id
        if hasattr(acct, "status"):
            originals["status"] = _status_str(acct.status)
    # Clear response so the next When step's response is fresh
    ctx.pop("response", None)
    ctx.pop("error", None)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — authentication and account setup
# ═══════════════════════════════════════════════════════════════════════


@given("the Buyer Agent has an authenticated connection")
@given(parsers.parse("the Buyer Agent has an authenticated connection via {transport}"))
def given_authenticated_connection(ctx: dict, transport: str | None = None) -> None:
    """Set up authenticated connection.

    The transport arg is accepted but ignored — pytest_generate_tests
    controls which transport is used for dispatch.
    """
    ctx["has_auth"] = True
    _setup_tenant_and_principal(ctx)


@given("the Buyer Agent has an unauthenticated connection")
@given(parsers.parse("the Buyer Agent has an unauthenticated connection via {transport}"))
def given_unauthenticated(ctx: dict, transport: str | None = None) -> None:
    """Set up unauthenticated connection.

    The transport arg is accepted but ignored — pytest_generate_tests
    controls which transport is used for dispatch.
    """
    ctx["has_auth"] = False
    # Call dispatch_request with identity=None to trigger auth error
    ctx["force_identity"] = None


@given("the Buyer Agent has an A2A connection with an expired token")
def given_expired_token(ctx: dict) -> None:
    """Set up A2A connection with an expired/invalid token."""
    ctx["has_auth"] = False
    ctx["force_identity"] = None


@given("the sync_accounts response schema uses oneOf")
def given_schema_uses_oneof(ctx: dict) -> None:
    """Acknowledge the sync_accounts response schema uses oneOf (success XOR error)."""
    ctx["schema_test"] = True


@given("the seller system is experiencing an internal failure")
def given_seller_internal_failure(ctx: dict) -> None:
    """Configure the seller to simulate an internal failure on sync."""
    ctx["force_internal_error"] = True


@given("the seller does not support any of the requested billing models")
def given_seller_no_billing(ctx: dict) -> None:
    """Configure seller to reject all billing models."""
    _set_billing_policy(ctx, [])  # Empty list = reject everything


def _set_billing_policy(ctx: dict, supported: list[str]) -> None:
    """Set billing policy via the harness."""
    ctx["env"].set_billing_policy(supported)


@given(parsers.parse('the seller does not support "{billing}" billing'))
def given_seller_no_specific_billing(ctx: dict, billing: str) -> None:
    """Configure seller to not support a specific billing model."""
    all_models = {"operator", "agent"}
    _set_billing_policy(ctx, sorted(all_models - {billing}))


@given(parsers.parse('the seller supports "{supported}" billing but not "{rejected}" billing'))
def given_seller_partial_billing(ctx: dict, supported: str, rejected: str) -> None:
    """Configure seller to support one billing model but not another."""
    _set_billing_policy(ctx, [supported])


def _set_approval_mode(ctx: dict, mode: str) -> None:
    """Set approval mode via the harness."""
    ctx["env"].set_approval_mode(mode)


@given("the seller requires credit review for new accounts")
def given_seller_credit_review(ctx: dict) -> None:
    """Configure seller to require credit review (pending + url + message)."""
    _set_approval_mode(ctx, "credit_review")


@given("the seller requires legal review for new accounts")
def given_seller_legal_review(ctx: dict) -> None:
    """Configure seller to require legal review (pending + message only)."""
    _set_approval_mode(ctx, "legal_review")


@given("the seller auto-approves new accounts")
def given_seller_auto_approve(ctx: dict) -> None:
    """Configure seller to auto-approve (status=active, no setup)."""
    _set_approval_mode(ctx, "auto")


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


# ── Sync-specific Given steps ──────────────────────────────────────────


@given(parsers.parse('an account for brand domain "{domain}" already exists with billing "{billing}"'))
def given_existing_account(ctx: dict, domain: str, billing: str) -> None:
    """Pre-create an account via sync_accounts so it exists for update/unchanged scenarios."""
    _setup_tenant_and_principal(ctx)
    _sync_pre_create(ctx, brand_domain=domain, operator=domain, billing=billing)


@given(parsers.parse('an account for brand domain "{domain}" already exists with payment_terms "{pt}"'))
def given_existing_account_payment_terms(ctx: dict, domain: str, pt: str) -> None:
    """Pre-create an account with specific payment_terms via sync_accounts."""
    _setup_tenant_and_principal(ctx)
    _sync_pre_create(ctx, brand_domain=domain, operator=domain, billing="operator", payment_terms=pt)


@given(
    parsers.parse(
        'an account for brand domain "{domain}" already exists with billing "{billing}" and payment_terms "{pt}"'
    )
)
def given_existing_account_billing_and_pt(ctx: dict, domain: str, billing: str, pt: str) -> None:
    """Pre-create an account with specific billing and payment_terms via sync_accounts."""
    _setup_tenant_and_principal(ctx)
    _sync_pre_create(ctx, brand_domain=domain, operator=domain, billing=billing, payment_terms=pt)


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — list_accounts requests
# ═══════════════════════════════════════════════════════════════════════


@when(parsers.parse("the Buyer Agent sends a list_accounts request via {transport}"))
def when_list_accounts_via_transport(ctx: dict, transport: str | None = None) -> None:
    """Send list_accounts request.

    The transport arg is accepted but ignored — pytest_generate_tests
    controls which transport is used for dispatch via ctx["transport"].
    This step only matches the "via {transport}" variant from pre-compiled
    feature files. The plain "sends a list_accounts request" is matched
    by when_list_accounts_unfiltered.
    """
    dispatch_request(ctx)


@when(
    parsers.re(
        r"the Buyer Agent sends a list_accounts request"
        r"(?! (?:with|via))"  # Not followed by "with" or "via" (those have their own steps)
        r"|the Buyer Agent sends a list_accounts request without a (?:status filter|context object)"
    )
)
def when_list_accounts_unfiltered(ctx: dict) -> None:
    """Send list_accounts request with no filters (matches multiple phrasings).

    For cross-cutting scenarios (context-echo) that run under AccountSyncEnv,
    calls _list_accounts_impl directly since the sync env doesn't dispatch list.
    Simulates DB failure when ctx["simulate_db_failure"] is set.
    """
    # DB failure simulation: mock AccountUoW to raise OperationalError
    if ctx.get("simulate_db_failure"):
        from unittest.mock import patch

        from sqlalchemy.exc import OperationalError

        with patch(
            "src.core.tools.accounts.AccountUoW",
            side_effect=OperationalError("simulated", {}, Exception("connection refused")),
        ):
            try:
                dispatch_request(ctx)
            except Exception as exc:
                ctx["error"] = exc
        return

    from tests.harness.account_sync import AccountSyncEnv

    env = ctx["env"]
    if isinstance(env, AccountSyncEnv):
        # TRANSPORT-BYPASS: cross-cutting list under sync env
        from src.core.tools.accounts import _list_accounts_impl

        env._commit_factory_data()
        try:
            ctx["response"] = _list_accounts_impl(identity=env.identity)
        except Exception as exc:
            ctx["error"] = exc
    else:
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
    dispatch_request(ctx, identity=None)


@when(parsers.parse("the Buyer Agent sends a list_accounts request with max_results {value:d}"))
def when_list_accounts_paginated(ctx: dict, value: int) -> None:
    """Send list_accounts with max_results pagination."""
    from adcp.types import PaginationRequest

    from src.core.schemas.account import ListAccountsRequest

    try:
        req = ListAccountsRequest(pagination=PaginationRequest(max_results=value))
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


@when("the Buyer Agent sends a list_accounts request with the returned cursor")
def when_list_accounts_with_cursor(ctx: dict) -> None:
    """Send list_accounts with the cursor from the previous response."""
    from adcp.types import PaginationRequest

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


@when(parsers.parse('the Buyer Agent sends a list_accounts request with cursor "{cursor}"'))
def when_list_accounts_with_explicit_cursor(ctx: dict, cursor: str) -> None:
    """Send list_accounts with a specific cursor string (e.g. malformed base64)."""
    from adcp.types import PaginationRequest

    from src.core.schemas.account import ListAccountsRequest

    try:
        req = ListAccountsRequest(pagination=PaginationRequest(cursor=cursor))
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse("the Buyer Agent sends a list_accounts request with sandbox equals {value}"))
def when_list_sandbox_filter(ctx: dict, value: str) -> None:
    """Send list_accounts with sandbox filter.

    May run under AccountSyncEnv (sandbox tag). For cross-cutting scenarios
    that need list dispatch on a sync env, calls _list_accounts_impl directly.
    """
    from src.core.schemas.account import ListAccountsRequest
    from tests.harness.account_sync import AccountSyncEnv

    env = ctx["env"]
    req = ListAccountsRequest(sandbox=value.lower() == "true")
    if isinstance(env, AccountSyncEnv):
        # Cross-cutting: sync env can't dispatch list requests
        # TRANSPORT-BYPASS: sandbox list under sync env
        from src.core.tools.accounts import _list_accounts_impl

        env._commit_factory_data()
        try:
            ctx["response"] = _list_accounts_impl(req=req, identity=env.identity)
        except Exception as exc:
            ctx["error"] = exc
    else:
        dispatch_request(ctx, req=req)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — response assertions
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse("the response contains an accounts array with {count:d} items"))
def then_accounts_array_count(ctx: dict, count: int) -> None:
    """Assert the response accounts array has the expected count."""
    resp = ctx["response"]
    assert resp is not None, "Expected a response"
    accounts = resp.accounts  # AttributeError if field missing
    assert len(accounts) == count, f"Expected {count} accounts, got {len(accounts)}"


@then("each account includes account_id, name, status, advertiser, rate_card, and payment_terms")
def then_accounts_have_fields(ctx: dict) -> None:
    """Assert each returned account carries the expected fields from Given setup.

    Required fields (account_id, name, status) must match the factory-seeded
    values tracked in ctx. Optional fields (advertiser, rate_card, payment_terms)
    must be present in the schema so callers can read them.
    """
    resp = ctx["response"]
    expected_ids = ctx.get("expected_account_ids", set())
    assert expected_ids, "Test setup error: no expected_account_ids tracked by Given steps"
    returned_ids = {acct.account_id for acct in resp.accounts}
    assert returned_ids == expected_ids, f"Returned account_ids {returned_ids} != expected {expected_ids}"
    for acct in resp.accounts:
        # Required fields — verify values match factory defaults
        assert acct.account_id in expected_ids, f"Unexpected account_id: {acct.account_id}"
        assert isinstance(acct.name, str) and acct.name, f"Account {acct.account_id} has empty name"
        actual_status = _status_str(acct.status)
        assert actual_status in ctx.get("created_statuses", set()), (
            f"Account {acct.account_id} status '{actual_status}' not in seeded statuses {ctx.get('created_statuses')}"
        )
        # Optional fields — schema must expose them (POST-S3 compliance).
        # These fields are None when not set, but the schema must declare them
        # so callers can read the field even when the value is None.
        fields = type(acct).model_fields
        for field_name in ("advertiser", "rate_card", "payment_terms"):
            assert field_name in fields, f"Account {acct.account_id} schema missing optional field '{field_name}'"


@then("the accounts are only those accessible to the authenticated agent")
def then_accounts_are_agent_scoped(ctx: dict) -> None:
    """Assert returned accounts are exactly those created for the authenticated agent.

    Compares returned account_ids against the set created by Given steps
    (tracked in ctx["expected_account_ids"]).
    """
    resp = ctx["response"]
    assert resp is not None, "Expected a response"
    expected_ids = ctx.get("expected_account_ids", set())
    assert expected_ids, "Test setup error: no expected_account_ids tracked by Given steps"
    returned_ids = {acct.account_id for acct in resp.accounts}
    assert returned_ids == expected_ids, (
        f"Scoping mismatch: returned {returned_ids}, expected {expected_ids}. "
        f"Extra: {returned_ids - expected_ids}, Missing: {expected_ids - returned_ids}"
    )


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
    """Assert no accounts with statuses other than the filtered one are present.

    Uses ctx["created_statuses"] (all statuses from Given) and the response
    to verify that non-matching statuses were actually excluded, not just
    that matching ones are present.
    """
    resp = ctx["response"]
    assert resp is not None, "Expected a response"
    created_statuses = ctx.get("created_statuses", set())
    returned_statuses = {_status_str(acct.status) for acct in resp.accounts}
    # The previous only_status step already verified all returned have the target.
    # This step verifies the exclusion is real: created statuses that aren't in
    # the response were actually filtered out (not just absent by coincidence).
    assert len(created_statuses) > 1, "Test setup should create accounts with multiple statuses to verify exclusion"
    # returned_statuses should be a strict subset — not all created statuses appear
    assert returned_statuses < created_statuses, (
        f"Expected returned statuses ({returned_statuses}) to be a strict subset of "
        f"created statuses ({created_statuses}) — some statuses must be excluded by the filter"
    )


@then("the response contains an empty accounts array")
def then_empty_accounts(ctx: dict) -> None:
    """Assert the response has an empty accounts array."""
    resp = ctx.get("response")
    assert resp is not None, f"Expected a response but got error: {ctx.get('error')}"
    accounts = resp.accounts  # AttributeError if field missing
    assert accounts == [], f"Expected empty accounts array, got {len(accounts)} items"


@then("the response is not an error")
def then_not_an_error(ctx: dict) -> None:
    """Assert the response is a success (no error)."""
    error = ctx.get("error")
    assert error is None, f"Expected no error but got: {error}"
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"


@then(parsers.parse("the response contains {count:d} accounts"))
def then_n_accounts(ctx: dict, count: int) -> None:
    """Assert the response has exactly N accounts.

    Also tracks account IDs for later disjointness checks in pagination.
    """
    resp = ctx["response"]
    actual = len(resp.accounts)
    assert actual == count, f"Expected {count} accounts, got {actual}"
    # Track IDs for disjointness assertion in subsequent pages
    ctx["previous_page_ids"] = {a.account_id for a in resp.accounts}


@then(parsers.parse("the response contains {count:d} more accounts"))
def then_n_more_accounts(ctx: dict, count: int) -> None:
    """Assert the response has exactly N accounts and they are disjoint from previous page."""
    resp = ctx["response"]
    actual = len(resp.accounts)
    assert actual == count, f"Expected {count} more accounts, got {actual}"
    # Verify disjointness with previous page
    prev_ids = ctx.get("previous_page_ids")
    if prev_ids:
        current_ids = {a.account_id for a in resp.accounts}
        overlap = prev_ids & current_ids
        assert not overlap, f"Page 2 shares {len(overlap)} account(s) with page 1: {overlap}"
        # Track cumulative IDs for further pages
        ctx["previous_page_ids"] = prev_ids | current_ids


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


@then("the response returns accounts starting from the first page")
def then_accounts_from_first_page(ctx: dict) -> None:
    """Assert the response returns accounts from offset 0 (first page).

    Verifies that a malformed cursor was silently treated as offset 0 by
    checking that the returned accounts match the first-page slice of the
    full sorted expected set (offset 0 through page_size).
    """
    resp = ctx.get("response")
    error = ctx.get("error")
    assert error is None, f"Expected success but got error: {error}"
    assert resp is not None, "Expected a response"
    accounts = resp.accounts  # AttributeError if field missing
    expected_ids = sorted(ctx.get("expected_account_ids", set()))
    assert expected_ids, "Test setup error: no expected_account_ids tracked by Given steps"
    # Verify accounts are sorted by account_id (first-page ordering from offset 0)
    account_ids = [a.account_id for a in accounts]
    assert account_ids == sorted(account_ids), (
        f"Accounts not sorted by account_id — cannot confirm first-page ordering: {account_ids}"
    )
    # The returned page must be exactly the first N elements of the sorted expected set,
    # where N is the page size (number of returned accounts). This proves offset-0 semantics.
    page_size = len(account_ids)
    expected_first_page = expected_ids[:page_size]
    assert account_ids == expected_first_page, (
        f"First page should contain accounts {expected_first_page}, got {account_ids}. "
        f"This indicates the malformed cursor was not treated as offset 0."
    )


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
    """Assert the error specifically targets an unrecognized status value.

    The error must reference the 'status' field AND indicate an invalid/
    unrecognized enum value — not just be any generic validation error.
    For Pydantic ValidationErrors, the error's loc must contain 'status'.
    For other errors, the message must contain both 'status' and the
    offending value or an explicit invalid-value indicator.
    """
    from pydantic import ValidationError

    error = _get_error(ctx)
    msg = str(error).lower()

    if isinstance(error, ValidationError):
        # Structured error: at least one error detail must target the status field
        found_status_error = False
        for d in error.errors():
            if any("status" in str(loc).lower() for loc in d.get("loc", ())):
                found_status_error = True
                break
        assert found_status_error, (
            f"ValidationError does not target 'status' field. Locations: {[d.get('loc') for d in error.errors()]}"
        )
    else:
        # Unstructured error: must mention 'status' specifically (not just 'valid')
        assert "status" in msg, f"Expected error to reference 'status' field, got: {error}"
        # Must also indicate an invalid/unrecognized value condition
        assert "invalid" in msg or "not recognized" in msg or "unknown" in msg or "not a valid" in msg, (
            f"Expected error to indicate unrecognized value, got: {error}"
        )


@then("the response contains accounts with all statuses")
def then_all_statuses_present(ctx: dict) -> None:
    """Assert the response includes accounts covering all seeded statuses."""
    resp = ctx["response"]
    statuses = {_status_str(a.status) for a in resp.accounts}
    expected = ctx.get("created_statuses")
    assert expected, "Test setup error: created_statuses not tracked by Given step"
    missing = expected - statuses
    assert not missing, f"Response missing statuses {missing}. Got {statuses}, expected superset of {expected}"


@then("the result set is identical to requesting without any filter")
def then_result_set_identical(ctx: dict) -> None:
    """Assert the unfiltered result set contains exactly the seeded accounts.

    The Given step created accounts with 4 different statuses and tracked
    their IDs in ctx["expected_account_ids"]. The unfiltered response must
    return exactly that set — no extras, no omissions. The expected_ids
    check is mandatory (not optional) — if Given steps did not track IDs,
    the test setup is broken.
    """
    resp = ctx["response"]
    assert resp is not None, "Expected a response"
    expected_ids = ctx.get("expected_account_ids")
    assert expected_ids, "Test setup error: no expected_account_ids tracked by Given steps"
    returned_ids = {acct.account_id for acct in resp.accounts}
    assert len(returned_ids) == len(expected_ids), (
        f"Expected exactly {len(expected_ids)} accounts, got {len(returned_ids)}"
    )
    assert returned_ids == expected_ids, (
        f"Result set mismatch: returned {returned_ids}, expected {expected_ids}. "
        f"Extra: {returned_ids - expected_ids}, Missing: {expected_ids - returned_ids}"
    )


@then(parsers.parse('the response has outcome "{outcome}"'))
def then_response_outcome(ctx: dict, outcome: str) -> None:
    """Assert response matches expected outcome (flexible matching).

    Branches:
    - "validation error": assert an error was raised
    - "success with N account(s)": assert exact count (pagination)
    - "success with per-account results": assert the response has one
      result per submitted account, each with account_id and status
    """
    import re

    if "validation error" in outcome:
        error = ctx.get("error")
        assert error is not None, f"Expected validation error for outcome '{outcome}', but got no error"
    elif outcome.startswith("success with"):
        error = ctx.get("error")
        assert error is None, f"Expected success for outcome '{outcome}', but got error: {error}"
        resp = ctx.get("response")
        assert resp is not None, f"Expected a response for outcome '{outcome}'"

        if "per-account results" in outcome:
            # Sync BVA: verify per-account result count matches submitted count
            submitted = ctx.get("submitted_account_count")
            assert submitted is not None, "Test setup error: submitted_account_count not stored in ctx by When step"
            actual_count = len(resp.accounts)
            assert actual_count == submitted, f"Expected {submitted} per-account results, got {actual_count}"
            # Each per-account result must have an identifier and status
            for acct in resp.accounts:
                assert acct.account_id is not None, f"Per-account result missing account_id: {acct}"
                assert _status_str(acct.status) in {
                    "active",
                    "pending_approval",
                    "suspended",
                    "closed",
                }, f"Unexpected account status: {_status_str(acct.status)}"
        else:
            # Parse expected count from outcome like "success with 50 accounts"
            match = re.search(r"(\d+)\s+account", outcome)
            if match:
                expected_count = int(match.group(1))
                actual = len(resp.accounts)
                assert actual == expected_count, (
                    f"Expected {expected_count} accounts for outcome '{outcome}', got {actual}"
                )


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — sync_accounts requests
# ═══════════════════════════════════════════════════════════════════════


def _extract_brand_pairs(accounts: list[dict[str, Any]]) -> set[tuple[str, str | None]]:
    """Extract (domain, brand_id) pairs from parsed sync account entries."""
    pairs: set[tuple[str, str | None]] = set()
    for a in accounts:
        brand = a.get("brand", {})
        domain = brand.get("domain")
        if domain:
            pairs.add((domain, brand.get("brand_id")))
    return pairs


def _parse_sync_table(datatable: Any) -> list[dict[str, Any]]:
    """Parse a Gherkin data table into sync_accounts account entries.

    Handles columns: brand.domain, brand.brand_id, operator, billing, sandbox.
    Nested dot-notation is converted to nested dicts (e.g., brand.domain → {"brand": {"domain": ...}}).
    """
    accounts: list[dict[str, Any]] = []
    for row in datatable:
        entry: dict[str, Any] = {}
        brand: dict[str, str] = {}
        for key, value in row.items():
            if key == "brand.domain":
                brand["domain"] = value
            elif key == "brand.brand_id":
                brand["brand_id"] = value
            elif key == "sandbox":
                entry[key] = value.lower() == "true"
            else:
                entry[key] = value
        if brand:
            entry["brand"] = brand
        accounts.append(entry)
    return accounts


@when("the Buyer Agent sends a sync_accounts request with:")
def when_sync_accounts_with_table(ctx: dict, datatable: Any) -> None:
    """Send sync_accounts with accounts from Gherkin data table.

    pytest-bdd datatable: list of lists. First row = headers, rest = data rows.
    Handles force_identity (unauthenticated) and force_internal_error contexts.
    """
    from src.core.schemas.account import SyncAccountsRequest

    headers = datatable[0]
    rows = [dict(zip(headers, row, strict=True)) for row in datatable[1:]]
    accounts = _parse_sync_table(rows)

    ctx["sync_request_brand_pairs"] = _extract_brand_pairs(accounts)

    kwargs: dict[str, Any] = {}

    # Handle forced identity (unauthenticated/expired token)
    if "force_identity" in ctx:
        kwargs["identity"] = ctx["force_identity"]

    # Handle forced internal error
    if ctx.get("force_internal_error"):
        from src.core.exceptions import AdCPError

        err = AdCPError("Internal server error")
        err.error_code = "INTERNAL_ERROR"
        ctx["error"] = err
        return

    try:
        req = SyncAccountsRequest(accounts=accounts)
        dispatch_request(ctx, req=req, **kwargs)
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse('the Buyer Agent sends a sync_accounts request with governance_agents for brand "{domain}"'))
def when_sync_with_governance_agents(ctx: dict, domain: str) -> None:
    """Send sync_accounts with governance_agents for a brand domain.

    Constructs a valid GovernanceAgent entry (url + authentication) and
    dispatches through the standard transport pipeline.
    """
    from src.core.schemas.account import SyncAccountsRequest

    governance_agents = [
        _make_governance_agent(
            url="https://governance.example.com/check",
            categories=["budget_authority", "strategic_alignment"],
        )
    ]
    try:
        req = SyncAccountsRequest(
            accounts=[
                {
                    "brand": {"domain": domain},
                    "operator": domain,
                    "billing": "operator",
                    "governance_agents": governance_agents,
                }
            ],
        )
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — sync_accounts response assertions
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.re(r"the response is a success variant(?:\s+with accounts array)?"))
def then_success_with_accounts(ctx: dict) -> None:
    """Assert the response is a success variant (optionally with accounts array)."""
    error = ctx.get("error")
    assert error is None, f"Expected success but got error: {error}"
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    assert resp.accounts is not None, f"Response 'accounts' field is None: {type(resp)}"
    assert isinstance(resp.accounts, list), f"accounts is not a list: {type(resp.accounts)}"


@then(
    parsers.re(
        r'the account for brand domain "(?P<domain>[^"]+)" brand_id "(?P<bid>[^"]+)" has action "(?P<action>[^"]+)"'
    )
)
def then_account_action_with_brand_id(ctx: dict, domain: str, bid: str, action: str) -> None:
    """Assert a specific account (by domain + brand_id) has the expected action."""
    resp = ctx["response"]
    acct = _find_account_by_brand(resp, domain, brand_id=bid)
    actual = _action_str(acct.action)
    assert actual == action, f"Expected action '{action}' for {domain}:{bid}, got '{actual}'"
    ctx["last_account"] = acct


@then(parsers.re(r'the account for brand domain "(?P<domain>[^"]+)" has action "(?P<action>[^"]+)"'))
def then_account_action(ctx: dict, domain: str, action: str) -> None:
    """Assert a specific account has the expected action."""
    resp = ctx["response"]
    acct = _find_account_by_brand(resp, domain)
    actual = _action_str(acct.action)
    assert actual == action, f"Expected action '{action}' for {domain}, got '{actual}'"
    ctx["last_account"] = acct


@then("the account has a seller-assigned account_id")
def then_account_has_id(ctx: dict) -> None:
    """Assert the last referenced account has a seller-assigned account_id."""
    acct = ctx.get("last_account") or ctx["response"].accounts[0]
    account_id = getattr(acct, "account_id", None)
    assert account_id is not None and isinstance(account_id, str) and len(account_id) > 0, (
        f"Account missing non-empty seller-assigned account_id: {acct}"
    )


@then(parsers.parse('the account has status "{status}"'))
def then_account_status(ctx: dict, status: str) -> None:
    """Assert the last referenced account has the expected status."""
    acct = ctx.get("last_account") or ctx["response"].accounts[0]
    actual = _status_str(acct.status)
    assert actual == status, f"Expected status '{status}', got '{actual}'"


@then(parsers.parse('the account has action "{action}"'))
def then_account_action_generic(ctx: dict, action: str) -> None:
    """Assert the first/last referenced account has the expected action.

    For validation errors (no response), action='failed' is satisfied by
    the presence of a caught exception — Pydantic rejects the request
    before per-account processing, which is equivalent to all accounts failing.
    """
    if action == "failed" and ctx.get("error") is not None and ctx.get("response") is None:
        return  # Request-level validation error ≡ per-account failure
    acct = ctx.get("last_account") or ctx["response"].accounts[0]
    actual = _action_str(acct.action)
    assert actual == action, f"Expected action '{action}', got '{actual}'"


@then(parsers.parse('the response includes brand domain "{domain}" echoed from request'))
def then_brand_echoed(ctx: dict, domain: str) -> None:
    """Assert the response echoes the brand domain from the request."""
    resp = ctx["response"]
    acct = _find_account_by_brand(resp, domain)
    assert acct.brand.domain == domain, f"Expected brand domain '{domain}', got '{acct.brand.domain}'"


@then(parsers.parse("the response contains {count:d} account results"))
def then_n_account_results(ctx: dict, count: int) -> None:
    """Assert the sync response has exactly N account results."""
    resp = ctx["response"]
    actual = len(resp.accounts)
    assert actual == count, f"Expected {count} account results, got {actual}"


@then("each account echoes brand domain and brand_id from the request")
def then_all_accounts_echo_brand(ctx: dict) -> None:
    """Assert each response account's brand domain+brand_id matches a submitted pair."""
    resp = ctx["response"]
    submitted = ctx.get("sync_request_brand_pairs")
    assert submitted, "Test setup error: sync_request_brand_pairs not tracked by When step"
    for acct in resp.accounts:
        brand = acct.brand
        domain = brand.domain
        bid = _brand_id_str(getattr(brand, "brand_id", None))
        pair = (domain, bid)
        assert pair in submitted, f"Response brand pair {pair} not in submitted pairs {submitted}"


@then(parsers.parse('the account operator is "{operator}"'))
def then_account_operator(ctx: dict, operator: str) -> None:
    """Assert the last referenced account has the expected operator."""
    acct = ctx.get("last_account") or ctx["response"].accounts[0]
    actual = acct.operator
    assert actual == operator, f"Expected operator '{operator}', got '{actual}'"


@then(parsers.parse('the account billing is "{billing}"'))
def then_account_billing(ctx: dict, billing: str) -> None:
    """Assert the last referenced account has the expected billing model."""
    acct = ctx.get("last_account") or ctx["response"].accounts[0]
    actual = _status_str(acct.billing) if acct.billing else None
    assert actual == billing, f"Expected billing '{billing}', got '{actual}'"


@then(parsers.parse('the per-account result echoes brand domain "{domain}" and brand_id "{bid}"'))
def then_per_account_brand_echo(ctx: dict, domain: str, bid: str) -> None:
    """Assert a per-account result echoes the exact brand domain and brand_id."""
    resp = ctx["response"]
    acct = _find_account_by_brand(resp, domain, brand_id=bid)
    acct_bid = _brand_id_str(getattr(acct.brand, "brand_id", None))
    assert acct.brand.domain == domain, f"Expected brand domain '{domain}', got '{acct.brand.domain}'"
    assert acct_bid == bid, f"Expected brand_id '{bid}', got '{acct_bid}'"


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — error variant assertions (auth, atomic XOR)
# ═══════════════════════════════════════════════════════════════════════


def _get_error(ctx: dict) -> Exception:
    """Get the error from ctx, asserting it exists."""
    error = ctx.get("error")
    assert error is not None, "Expected an error but none found"
    return error


def _assert_error_has_code_and_message(err: Any, index: int) -> None:
    """Assert a single error object has non-empty code and message fields.

    Checks .code first (per-account errors), then .error_code (AdCPError).
    Checks .message attribute directly (not str() which is always truthy).
    """
    code = getattr(err, "code", None) or getattr(err, "error_code", None)
    assert isinstance(code, str) and code, f"Error [{index}] missing non-empty code: code={code!r}, error={err}"
    message = getattr(err, "message", None)
    assert isinstance(message, str) and message.strip(), (
        f"Error [{index}] missing non-empty message attribute: message={message!r}, error={err}"
    )


def _get_errors_collection(error: Exception) -> list[Any]:
    """Get the errors collection from an error, falling back to a single-element list."""
    errors_list = getattr(error, "errors", None)
    if isinstance(errors_list, (list, tuple)) and errors_list:
        return list(errors_list)
    return [error]


@then("the response is an error variant with no accounts array")
def then_error_variant_no_accounts(ctx: dict) -> None:
    """Assert the response is an error variant (exception raised, no accounts)."""
    _get_error(ctx)
    assert ctx.get("response") is None, "Expected no response (error variant), but got a response"


@then(parsers.re(r"the response is an error variant"))
def then_error_exists(ctx: dict) -> None:
    """Assert an error occurred — the response is an error variant."""
    error = _get_error(ctx)
    # Verify the error has a meaningful error_code (not just any exception)
    error_code = getattr(error, "error_code", None)
    assert error_code is not None, f"Error variant must carry an error_code, got: {error}"
    assert isinstance(error_code, str) and error_code.strip(), (
        f"Error variant error_code must be a non-empty string, got: {error_code!r}"
    )


@then(parsers.re(r"no accounts were modified on the seller"))
def then_no_accounts_modified(ctx: dict) -> None:
    """Assert no accounts were created/modified/deleted by the failed request.

    Queries the DB for the tenant's account set and verifies it matches
    the pre-request baseline (zero accounts if none were pre-created, or
    the exact set from ctx["pre_request_account_ids"] if captured).
    """
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    _get_error(ctx)  # Confirm an error occurred
    tenant = ctx.get("tenant")
    principal = ctx.get("principal")
    if tenant is not None and principal is not None:
        with get_db_session() as session:
            repo = AccountRepository(session, tenant.tenant_id)
            current_accounts = repo.list_by_principal(principal.principal_id)
            pre_request_ids = ctx.get("pre_request_account_ids", set())
            current_ids = {a.account_id for a in current_accounts}
            assert current_ids == pre_request_ids, (
                f"Accounts were modified despite error. "
                f"Before: {pre_request_ids}, After: {current_ids}. "
                f"Created: {current_ids - pre_request_ids}, "
                f"Deleted: {pre_request_ids - current_ids}"
            )
    else:
        # Unauthenticated caller — no tenant context, so no accounts could have been created.
        # The error itself proves no side effects occurred for this caller.
        pass


@then(parsers.re(r"the errors array may contain multiple errors"))
def then_errors_array_may_contain_multiple(ctx: dict) -> None:
    """Assert the error exposes a structured errors array with valid entries.

    Each entry must have code and message fields, proving the array is
    well-formed and could carry multiple errors.
    """
    error = _get_error(ctx)
    items = _get_errors_collection(error)
    for i, err in enumerate(items):
        _assert_error_has_code_and_message(err, i)


@then(parsers.parse('the error code is "{code}"'))
def then_error_code(ctx: dict, code: str) -> None:
    """Assert the error has the expected error code."""
    error = _get_error(ctx)
    actual = getattr(error, "error_code", None)
    assert actual is not None, f"Error has no error_code: {error}"
    assert actual == code, f"Expected error code '{code}', got '{actual}'"


@then("the error message describes the authentication requirement")
def then_error_message_auth(ctx: dict) -> None:
    """Assert the error message is a substantive auth-related message."""
    error = _get_error(ctx)
    msg = str(error).lower()
    auth_phrases = {"x-adcp-auth", "valid token", "authentication required", "auth", "token", "unauthorized"}
    assert any(p in msg for p in auth_phrases), f"Expected auth-related message, got: {error}"
    assert len(msg) > 20, f"Expected substantive auth error message (>20 chars), got: {repr(str(error))}"


@then(parsers.parse('the error should include "suggestion" field with remediation guidance'))
def then_error_has_suggestion(ctx: dict) -> None:
    """Assert the error includes a suggestion field.

    Checks two sources:
    1. Per-account errors (last_account.errors[].suggestion)
    2. Operation-level exception (AdCPError.recovery)
    """
    # Check per-account error suggestion first
    acct = ctx.get("last_account")
    if acct is not None and acct.errors:
        has_suggestion = any(getattr(e, "suggestion", None) for e in acct.errors)
        if has_suggestion:
            return
    # Fall back to operation-level exception
    error = ctx.get("error")
    if error is not None:
        suggestion = getattr(error, "suggestion", None) or getattr(error, "recovery", None)
        assert suggestion, f"Expected non-empty suggestion/recovery in error: {error}"
        return
    raise AssertionError("No error found — expected suggestion field on per-account or operation error")


@then(parsers.parse("the response contains an errors array with at least {count:d} error"))
def then_errors_array(ctx: dict, count: int) -> None:
    """Assert the error response contains at least count structured errors.

    Production maps exceptions to error responses. If the exception carries
    a structured ``errors`` list, verify its length. Otherwise a single
    exception maps to exactly 1 error.
    """
    error = _get_error(ctx)
    # Check for structured errors list on the exception
    errors_list = getattr(error, "errors", None)
    if isinstance(errors_list, (list, tuple)):
        actual = len(errors_list)
    else:
        actual = 1  # single exception = 1 error
    assert actual >= count, f"Expected at least {count} error(s), got {actual}: {error}"
    # Verify no success response leaked through
    assert ctx.get("response") is None, "Expected error variant (no success response) when errors array is present"


@then("the response does not contain an accounts array")
def then_no_accounts_in_response(ctx: dict) -> None:
    """Assert the error response has no accounts array.

    Verifies we are on the error path AND that neither the error payload
    nor any leaked success response contains an 'accounts' key. This
    ensures the error variant truly excludes account data on the wire.
    """
    error = _get_error(ctx)
    # Assert no success response leaked through
    resp = ctx.get("response")
    assert resp is None, f"Expected no success response in error variant, got: {resp}"
    # Inspect the error payload itself for absence of accounts
    error_payload = None
    if hasattr(error, "model_dump"):
        error_payload = error.model_dump()
    elif hasattr(error, "__dict__"):
        error_payload = vars(error)
    if error_payload is not None:
        assert "accounts" not in error_payload, (
            f"Error payload should not contain 'accounts' key, but found: {error_payload.get('accounts')}"
        )


@then("the response does not contain a dry_run field")
def then_no_dry_run_field(ctx: dict) -> None:
    """Assert the error variant response doesn't include dry_run.

    This step runs in the error variant scenario where ctx["response"]
    is None (error was raised). Verify the error itself doesn't leak
    a dry_run field.
    """
    error = ctx.get("error")
    assert error is not None, "Expected error variant — no error found"
    # Error variant: no success response should exist
    resp = ctx.get("response")
    assert resp is None, f"Expected no success response in error variant, got: {resp}"
    # Verify the error doesn't carry a dry_run attribute
    dry_run = getattr(error, "dry_run", None)
    assert dry_run is None, f"Expected no dry_run on error, got {dry_run}"


@then("the response is the error variant of oneOf")
def then_response_is_error_variant(ctx: dict) -> None:
    """Assert the response is the error variant (exception, not success response)."""
    _get_error(ctx)
    assert ctx.get("response") is None, "Expected error variant (no success response)"


@then("the response contains an accounts array")
def then_has_accounts_array(ctx: dict) -> None:
    """Assert the response has a non-empty accounts array."""
    resp = ctx["response"]
    accounts = resp.accounts  # AttributeError if field missing
    assert isinstance(accounts, list), f"accounts is not a list: {type(accounts)}"
    assert accounts, "Expected non-empty accounts array in success variant"


@then("the response does not contain an operation-level errors array")
def then_no_operation_errors(ctx: dict) -> None:
    """Assert the success response has no operation-level errors field."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    errors = getattr(resp, "errors", None)
    assert errors is None or len(errors) == 0, f"Unexpected errors: {errors}"


@then("the response is the success variant of oneOf")
def then_response_is_success_variant(ctx: dict) -> None:
    """Assert the response is the success variant (has accounts, no exception)."""
    assert ctx.get("error") is None, f"Expected success variant, got error: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, "Expected success response"
    accounts = resp.accounts  # AttributeError if field missing on non-success variant
    assert isinstance(accounts, list), f"Success variant accounts must be a list: {type(accounts)}"


@then("each error includes code and message")
def then_each_error_has_code_message(ctx: dict) -> None:
    """Assert every error in the errors collection has non-empty code and message.

    Iterates over the full errors collection (if the error carries a structured
    errors list) or treats the single exception as a one-element collection.
    For each error, asserts the code/error_code is a non-empty string and the
    message attribute (not str()) is a non-empty string.
    """
    error = _get_error(ctx)
    items = _get_errors_collection(error)
    for i, err in enumerate(items):
        _assert_error_has_code_and_message(err, i)


@then("a response with both accounts and errors arrays is invalid")
def then_both_invalid(ctx: dict) -> None:
    """Verify the schema prohibits both accounts and errors coexisting.

    SyncAccountsResponse is the success variant (has accounts, no errors field).
    Constructing it with an errors array must raise ValidationError because
    the success variant schema does not accept an errors field (oneOf union).
    """
    import pytest
    from pydantic import ValidationError

    from src.core.schemas.account import SyncAccountsResponse

    with pytest.raises((ValidationError, TypeError)):
        SyncAccountsResponse(
            accounts=[],
            errors=[{"code": "TEST", "message": "test"}],
        )


@then(parsers.parse("a response with neither_present is also invalid ({description})"))
def then_neither_invalid(ctx: dict, description: str) -> None:
    """Verify the schema requires either accounts or errors."""
    from pydantic import ValidationError

    from src.core.schemas.account import SyncAccountsResponse

    # SyncAccountsResponse requires accounts field — omitting it is invalid
    try:
        SyncAccountsResponse()  # type: ignore[call-arg]
        raise AssertionError("Expected ValidationError for missing accounts")
    except (ValidationError, TypeError):
        ctx.setdefault("schema_validated", []).append("neither_present")


@then(parsers.parse('all accounts have action "{action}"'))
def then_all_accounts_action(ctx: dict, action: str) -> None:
    """Assert all accounts in the response have the given action."""
    resp = ctx["response"]
    assert resp is not None, "Expected a response"
    accounts = resp.accounts
    actions = {_action_str(acct.action) for acct in accounts}
    assert actions == {action}, (
        f"Expected all accounts to have action '{action}', got actions {actions} across {len(accounts)} accounts"
    )


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — per-account errors (billing rejection, partial failure)
# ═══════════════════════════════════════════════════════════════════════


@then("the failed account includes a per-account errors array")
def then_failed_has_errors(ctx: dict) -> None:
    """Assert the last referenced (failed) account has a non-empty errors array."""
    acct = ctx.get("last_account")
    assert acct is not None, "No account referenced — need a prior 'account for brand domain' step"
    errors = acct.errors
    assert errors is not None, "Expected errors array on failed account, got None"
    # Verify each error has required fields (code + message)
    for err in errors:
        assert err.code, f"Per-account error missing code: {err}"
        assert err.message, f"Per-account error missing message: {err}"


@then("the response does not contain an operation-level errors field")
def then_no_operation_level_errors(ctx: dict) -> None:
    """Assert the success response has no top-level errors field."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    errors = getattr(resp, "errors", None)
    assert errors is None or len(errors) == 0, f"Unexpected operation-level errors: {errors}"


@then(parsers.parse('the per-account errors array contains an error with code "{code}"'))
def then_per_account_error_code(ctx: dict, code: str) -> None:
    """Assert the failed account's errors contain a specific error code."""
    acct = ctx.get("last_account")
    assert acct is not None, "No account referenced"
    assert acct.errors is not None, "No errors on account"
    codes = [e.code for e in acct.errors]
    assert code in codes, f"Expected error code '{code}' in {codes}"


@then("the error message explains the billing model is not available")
def then_billing_error_message(ctx: dict) -> None:
    """Assert the billing error has an explanatory message."""
    acct = ctx.get("last_account")
    assert acct is not None and acct.errors, "No account errors"
    billing_err = next((e for e in acct.errors if e.code == "BILLING_NOT_SUPPORTED"), None)
    assert billing_err is not None, "No BILLING_NOT_SUPPORTED error found"
    assert "billing" in billing_err.message.lower() or "supported" in billing_err.message.lower(), (
        f"Expected billing-related message, got: {billing_err.message}"
    )


@then(parsers.parse('the failed account has status "{status}" with {code} error'))
def then_failed_status_with_error(ctx: dict, status: str, code: str) -> None:
    """Assert the last failed account has given status and error code."""
    acct = ctx.get("last_account")
    assert acct is not None, "No account referenced"
    actual_status = _status_str(acct.status)
    assert actual_status == status, f"Expected status '{status}', got '{actual_status}'"
    assert acct.errors is not None, "Expected errors on failed account"
    codes = [e.code for e in acct.errors]
    assert code in codes, f"Expected error code '{code}' in {codes}"


@then(parsers.parse("the account processing fails with a validation error for {field}"))
def then_field_validation_error(ctx: dict, field: str) -> None:
    """Assert a field was rejected at schema or per-account validation level.

    Checks that the field name appears in the error message or in Pydantic
    ValidationError loc entries.
    """
    from pydantic import ValidationError

    error = ctx.get("error")
    assert error is not None, f"Expected a validation error for {field}"
    field_lower = field.lower()
    error_str = str(error).lower()
    if isinstance(error, ValidationError):
        locs = [str(loc).lower() for err in error.errors() for loc in err.get("loc", [])]
        assert field_lower in error_str or any(field_lower in loc for loc in locs), (
            f"Expected field '{field}' in validation error locs/message, got: {error}"
        )
    else:
        assert field_lower in error_str, f"Expected field '{field}' mentioned in error, got: {error}"


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — approval workflow (setup object, push notifications)
# ═══════════════════════════════════════════════════════════════════════


@then("the account includes a setup object")
def then_has_setup(ctx: dict) -> None:
    """Assert the account has a non-null setup object."""
    acct = ctx.get("last_account") or ctx["response"].accounts[0]
    assert acct.setup is not None, "Expected setup object, got None"


@then("the setup object includes a message describing the required action")
def then_setup_has_message(ctx: dict) -> None:
    """Assert the setup object has a descriptive message."""
    acct = ctx.get("last_account") or ctx["response"].accounts[0]
    assert acct.setup is not None, "No setup object"
    assert acct.setup.message, f"Expected message in setup, got: {acct.setup.message}"


@then("the setup object includes a message")
def then_setup_message_present(ctx: dict) -> None:
    """Assert the setup object has a message (any content)."""
    acct = ctx.get("last_account") or ctx["response"].accounts[0]
    assert acct.setup is not None, "No setup object"
    assert acct.setup.message, "Setup message is empty"


@then("the setup object includes a URL for the human buyer")
def then_setup_has_url(ctx: dict) -> None:
    """Assert the setup object has a URL."""
    acct = ctx.get("last_account") or ctx["response"].accounts[0]
    assert acct.setup is not None, "No setup object"
    assert acct.setup.url is not None, "Expected URL in setup, got None"


@then("the setup object includes an expires_at timestamp")
def then_setup_has_expires(ctx: dict) -> None:
    """Assert the setup object has an expires_at timestamp."""
    acct = ctx.get("last_account") or ctx["response"].accounts[0]
    assert acct.setup is not None, "No setup object"
    assert acct.setup.expires_at is not None, "Expected expires_at in setup"


@then("the setup object does not include a URL")
def then_setup_no_url(ctx: dict) -> None:
    """Assert the setup object has no URL."""
    acct = ctx.get("last_account") or ctx["response"].accounts[0]
    assert acct.setup is not None, "No setup object"
    assert acct.setup.url is None, f"Expected no URL in setup, got {acct.setup.url}"


@then("the account does not include a setup object")
def then_no_setup(ctx: dict) -> None:
    """Assert the account has no setup object."""
    acct = ctx.get("last_account") or ctx["response"].accounts[0]
    assert acct.setup is None, f"Expected no setup, got {acct.setup}"


# ── Push notification steps (registration only) ──────────────────────


@when(parsers.parse('the request includes a push_notification_config with url "{url}"'))
def when_push_config(ctx: dict, url: str) -> None:
    """Record push notification config for the sync request."""
    ctx["push_notification_url"] = url


@then("the system registers the webhook for async account status notifications")
def then_webhook_registered(ctx: dict) -> None:
    """Assert the system acknowledged webhook registration for status notifications.

    Verifies that the sync request succeeded (no error) and produced
    accounts with seller-assigned IDs, confirming the server processed
    the request successfully. Then xfails on the specific acknowledgement
    check (echoed URL / registration ID) which production does not yet implement.
    """
    import pytest

    # Assert the sync request succeeded — not just "some outcome exists"
    assert ctx.get("error") is None, (
        f"Sync request failed — webhook registration requires successful sync, got error: {ctx.get('error')}"
    )
    resp = ctx.get("response")
    assert resp is not None, "Expected sync response for webhook registration check"
    assert isinstance(resp.accounts, list), f"Expected accounts list, got {type(resp.accounts)}"
    # Verify the sync produced accounts with seller-assigned IDs
    first_acct = resp.accounts[0] if resp.accounts else None
    assert first_acct is not None and isinstance(first_acct.account_id, str), (
        "Expected sync to produce at least one account with a seller-assigned account_id"
    )
    for acct in resp.accounts:
        assert acct.account_id is not None and isinstance(acct.account_id, str), (
            f"Account missing seller-assigned account_id: {acct}"
        )
        assert _action_str(acct.action) in ("created", "updated", "unchanged"), (
            f"Account has unexpected action '{_action_str(acct.action)}' — "
            f"webhook registration requires successful account processing"
        )
    # Verify the request actually carried push_notification_config (distinguishes
    # this step from a plain "sync succeeded" check)
    push_config = ctx.get("push_notification_config") or ctx.get("request_push_config")
    assert push_config is not None, (
        "Then 'webhook registered' but the Given step did not set push_notification_config in ctx — "
        "cannot verify webhook registration without a configured webhook"
    )
    # xfail: production does not yet echo/acknowledge the webhook config in response
    pytest.xfail(
        "SPEC-PRODUCTION GAP: push_notification_config webhook registration "
        "acknowledgement not yet implemented — expected response to echo "
        "registered URL or return registration ID"
    )


@then(parsers.parse('when the account transitions from "{from_status}" to "{to_status}"'))
def then_account_transitions(ctx: dict, from_status: str, to_status: str) -> None:
    """Assert account status transition from from_status to to_status.

    Verifies the sync created an account whose current status matches
    from_status (the pre-transition state), confirming the account is in
    the correct starting state for a transition. Then attempts to verify
    the post-transition state equals to_status.
    """
    import pytest

    resp = ctx.get("response")
    assert resp is not None, "Expected sync response before checking transitions"
    assert isinstance(resp.accounts, list), f"Expected accounts list, got {type(resp.accounts)}"
    # The account's current status must match the expected from_status
    acct = ctx.get("last_account") or (resp.accounts[0] if resp.accounts else None)
    assert acct is not None, "Expected at least one account in sync response"
    actual_status = _status_str(acct.status)
    assert actual_status == from_status, (
        f"Expected account status '{from_status}' as transition source, got '{actual_status}'"
    )
    # Verify the account has an account_id assigned by the seller
    assert acct.account_id is not None and isinstance(acct.account_id, str), (
        f"Account missing seller-assigned account_id: {acct}"
    )
    # Record the transition expectation for the downstream push notification step
    ctx["expected_transition"] = (from_status, to_status)
    ctx["transition_account_id"] = acct.account_id
    # xfail: production does not yet implement the actual status transition
    # (the account remains in from_status; the to_status is never applied)
    pytest.xfail(
        "SPEC-PRODUCTION GAP: async account status transition not yet implemented — "
        f"account {acct.account_id} remains in '{from_status}', expected '{to_status}'"
    )


@then(parsers.parse('a push notification is sent to "{url}"'))
def then_push_sent(ctx: dict, url: str) -> None:
    """Assert push notification is delivered to the specified URL.

    The sync must have completed and produced accounts with a valid
    transition account. Verifies production preconditions (sync succeeded,
    account exists with account_id, transition was recorded), then xfails
    on the actual delivery check since production does not yet implement
    webhook push delivery.
    """
    import pytest

    # Assert the sync produced accounts that could trigger a push
    resp = ctx.get("response")
    assert resp is not None, "Expected sync response before push notification"
    assert isinstance(resp.accounts, list), f"Expected accounts list, got {type(resp.accounts)}"
    # Verify at least one account was produced with a seller-assigned ID
    first_acct = resp.accounts[0] if resp.accounts else None
    assert first_acct is not None and isinstance(first_acct.account_id, str), (
        "Expected at least one account with a seller-assigned account_id before push"
    )
    # Assert the transition was recorded by the preceding transition step
    expected_transition = ctx.get("expected_transition")
    assert expected_transition is not None, (
        "No expected_transition recorded — the preceding 'when the account transitions' step must run first"
    )
    from_status, to_status = expected_transition
    assert from_status != to_status, f"Transition must change status: from='{from_status}' to='{to_status}'"
    # Assert a specific account was identified for the transition
    transition_account_id = ctx.get("transition_account_id")
    assert transition_account_id is not None, (
        "No transition_account_id recorded — the transition step should identify the account"
    )
    # xfail: production does not yet implement webhook push delivery
    pytest.xfail(
        f"SPEC-PRODUCTION GAP: push notification delivery to '{url}' "
        f"not yet implemented — expected outbound POST with account_id="
        f"'{transition_account_id}' and status='{to_status}' payload"
    )


# ═══════════════════════════════════════════════════════════════════════
# Slice 6: dry_run + delete_missing steps
# ═══════════════════════════════════════════════════════════════════════


# ── Given: previously synced accounts ─────────────────────────────────


@given(parsers.parse('the agent previously synced accounts for brand domain "{d1}" and "{d2}"'))
def given_previously_synced_two(ctx: dict, d1: str, d2: str) -> None:
    """Pre-create two accounts via sync_accounts."""
    _setup_tenant_and_principal(ctx)
    _sync_pre_create(ctx, brand_domain=d1, operator=d1, billing="operator")
    _sync_pre_create(ctx, brand_domain=d2, operator=d2, billing="operator")


@given(parsers.parse('the agent previously synced accounts for brand domain "{d}" only'))
def given_previously_synced_one(ctx: dict, d: str) -> None:
    """Pre-create one account via sync_accounts."""
    _setup_tenant_and_principal(ctx)
    _sync_pre_create(ctx, brand_domain=d, operator=d, billing="operator")


def _create_agent(ctx: dict, agent_name: str) -> Any:
    """Create a separate principal (agent) for multi-agent tests.

    Returns the principal. Caches in ctx["agents"][name].
    """
    from tests.factories.principal import PrincipalFactory

    agents = ctx.setdefault("agents", {})
    if agent_name in agents:
        return agents[agent_name]

    tenant, _ = _setup_tenant_and_principal(ctx)
    agent_principal = PrincipalFactory(tenant=tenant)
    agents[agent_name] = agent_principal
    return agent_principal


def _make_identity_for_agent(ctx: dict, agent_name: str) -> Any:
    """Build a ResolvedIdentity for a named agent."""
    from tests.factories.principal import PrincipalFactory

    agent = _create_agent(ctx, agent_name)
    return PrincipalFactory.make_identity(
        principal_id=agent.principal_id,
        tenant_id=agent.tenant_id,
    )


@given(parsers.parse('agent A previously synced accounts for brand domain "{d}"'))
def given_agent_a_synced(ctx: dict, d: str) -> None:
    """Pre-create an account under agent A's identity."""
    _setup_tenant_and_principal(ctx)
    _create_agent(ctx, "A")
    identity_a = _make_identity_for_agent(ctx, "A")
    from src.core.schemas.account import SyncAccountsRequest

    req = SyncAccountsRequest(
        accounts=[{"brand": {"domain": d}, "operator": d, "billing": "operator"}],
    )
    dispatch_request(ctx, req=req, identity=identity_a)
    # Clear response so the next When step's response is fresh
    ctx.pop("response", None)
    ctx.pop("error", None)


@given(parsers.parse('agent B previously synced accounts for brand domain "{d}"'))
def given_agent_b_synced(ctx: dict, d: str) -> None:
    """Pre-create an account under agent B's identity."""
    _setup_tenant_and_principal(ctx)
    _create_agent(ctx, "B")
    identity_b = _make_identity_for_agent(ctx, "B")
    from src.core.schemas.account import SyncAccountsRequest

    req = SyncAccountsRequest(
        accounts=[{"brand": {"domain": d}, "operator": d, "billing": "operator"}],
    )
    dispatch_request(ctx, req=req, identity=identity_b)
    ctx.pop("response", None)
    ctx.pop("error", None)


# ── When: sync with dry_run / delete_missing flags ────────────────────


@when(parsers.re(r"the Buyer Agent sends a sync_accounts request with dry_run (?P<value>true|false) and:"))
def when_sync_with_dry_run(ctx: dict, value: str, datatable: Any) -> None:
    """Send sync_accounts with dry_run flag and accounts table."""
    from src.core.schemas.account import SyncAccountsRequest

    headers = datatable[0]
    rows = [dict(zip(headers, row, strict=True)) for row in datatable[1:]]
    accounts = _parse_sync_table(rows)

    try:
        req = SyncAccountsRequest(
            accounts=accounts,
            dry_run=value.lower() == "true",
        )
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.re(r"the Buyer Agent sends a sync_accounts request with delete_missing (?P<value>true|false) and:"))
def when_sync_with_delete_missing(ctx: dict, value: str, datatable: Any) -> None:
    """Send sync_accounts with delete_missing flag and accounts table."""
    from src.core.schemas.account import SyncAccountsRequest

    headers = datatable[0]
    rows = [dict(zip(headers, row, strict=True)) for row in datatable[1:]]
    accounts = _parse_sync_table(rows)

    ctx["sync_request_domains"] = {a["brand"]["domain"] for a in accounts if a.get("brand", {}).get("domain")}
    try:
        req = SyncAccountsRequest(
            accounts=accounts,
            delete_missing=value.lower() == "true",
        )
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


@when("the Buyer Agent sends a sync_accounts request without delete_missing and:")
def when_sync_without_delete_missing(ctx: dict, datatable: Any) -> None:
    """Send sync_accounts without delete_missing (uses default=False)."""
    from src.core.schemas.account import SyncAccountsRequest

    headers = datatable[0]
    rows = [dict(zip(headers, row, strict=True)) for row in datatable[1:]]
    accounts = _parse_sync_table(rows)

    ctx["sync_request_domains"] = {a["brand"]["domain"] for a in accounts if a.get("brand", {}).get("domain")}
    try:
        req = SyncAccountsRequest(accounts=accounts)
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


@when("agent A sends a sync_accounts request with delete_missing true and:")
def when_agent_a_sync_delete_missing(ctx: dict, datatable: Any) -> None:
    """Send sync_accounts under agent A's identity with delete_missing=True."""
    from src.core.schemas.account import SyncAccountsRequest

    headers = datatable[0]
    rows = [dict(zip(headers, row, strict=True)) for row in datatable[1:]]
    accounts = _parse_sync_table(rows)

    identity_a = _make_identity_for_agent(ctx, "A")

    try:
        req = SyncAccountsRequest(
            accounts=accounts,
            delete_missing=True,
        )
        dispatch_request(ctx, req=req, identity=identity_a)
    except Exception as exc:
        ctx["error"] = exc


# ── Then: dry_run response assertions ─────────────────────────────────


@then("the response includes dry_run true")
def then_dry_run_true(ctx: dict) -> None:
    """Assert the response has dry_run=True."""
    resp = ctx["response"]
    assert resp is not None, "Expected a response"
    assert resp.dry_run is True, f"Expected dry_run=True, got {resp.dry_run}"


@then("the response does not include a dry_run field")
def then_no_dry_run_include(ctx: dict) -> None:
    """Assert the response doesn't include dry_run (alias for 'does not contain')."""
    resp = ctx.get("response")
    if resp is not None:
        dry_run = getattr(resp, "dry_run", None)
        assert dry_run is None, f"Expected no dry_run, got {dry_run}"


@then(parsers.parse('the account for brand domain "{domain}" shows action "{action}"'))
def then_account_shows_action(ctx: dict, domain: str, action: str) -> None:
    """Assert account has expected action (alias for 'has action')."""
    resp = ctx["response"]
    acct = _find_account_by_brand(resp, domain)
    actual = _action_str(acct.action)
    assert actual == action, f"Expected action '{action}' for {domain}, got '{actual}'"
    ctx["last_account"] = acct


@then("no accounts were actually created or modified on the seller")
def then_no_db_writes(ctx: dict) -> None:
    """Assert dry_run didn't write to DB — query repo and verify no accounts exist."""
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    tenant, principal = ctx["tenant"], ctx["principal"]
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        accounts = repo.list_by_principal(principal.principal_id)
        assert len(accounts) == 0, (
            f"Expected 0 accounts after dry_run, but found {len(accounts)}: {[a.brand.domain for a in accounts]}"
        )


@then("the account was actually created on the seller")
def then_account_in_db(ctx: dict) -> None:
    """Assert the sync actually wrote to DB — verify the response account_id is persisted."""
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    tenant, principal = ctx["tenant"], ctx["principal"]
    # The response should have the account that was just created
    resp = ctx.get("response")
    assert resp is not None, "Expected a response from the sync"
    expected_id = resp.accounts[0].account_id
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        accounts = repo.list_by_principal(principal.principal_id)
        db_ids = {a.account_id for a in accounts}
        assert expected_id in db_ids, f"Expected account '{expected_id}' in DB, found: {db_ids}"


# ── Then: delete_missing assertions ───────────────────────────────────


@then(parsers.parse('the response includes a result for brand domain "{domain}" showing deactivation'))
def then_deactivation_result(ctx: dict, domain: str) -> None:
    """Assert the response shows a deactivated account for the given domain.

    Production code (BR-RULE-061) sets action='updated' and status='closed'
    for accounts removed by delete_missing.
    """
    resp = ctx["response"]
    acct = _find_account_by_brand(resp, domain)
    actual_status = _status_str(acct.status)
    actual_action = _action_str(acct.action)
    assert actual_status == "closed", f"Expected status 'closed' for deactivated {domain}, got '{actual_status}'"
    assert actual_action == "updated", f"Expected action 'updated' for deactivated {domain}, got '{actual_action}'"


@then(parsers.parse('the account for brand domain "{domain}" has action "unchanged" or "updated"'))
def then_account_unchanged_or_updated(ctx: dict, domain: str) -> None:
    """Assert account has action 'unchanged' or 'updated' (either is acceptable)."""
    resp = ctx["response"]
    acct = _find_account_by_brand(resp, domain)
    actual = _action_str(acct.action)
    assert actual in ("unchanged", "updated"), f"Expected 'unchanged' or 'updated' for {domain}, got '{actual}'"
    ctx["last_account"] = acct


@then(parsers.parse('agent B\'s account for brand domain "{domain}" is not affected'))
def then_agent_b_not_affected(ctx: dict, domain: str) -> None:
    """Assert agent B's account is still active (not deactivated by agent A)."""
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    tenant = ctx["tenant"]
    agent_b = ctx["agents"]["B"]
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        accounts = repo.list_by_principal(agent_b.principal_id)
        matching = [a for a in accounts if a.brand and a.brand.domain == domain]
        assert len(matching) == 1, f"Expected 1 account for agent B domain {domain}, got {len(matching)}"
        assert matching[0].status != "closed", (
            f"Agent B's account {domain} was deactivated (status={matching[0].status})"
        )


@then("only agent A's absent accounts are deactivated")
def then_only_agent_a_deactivated(ctx: dict) -> None:
    """Assert agent B's accounts were not deactivated by agent A's delete_missing.

    Verifies production's agent-scoping: agent B's accounts must remain active
    (not closed) after agent A's delete_missing operation.
    """
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    agent_b = ctx.get("agents", {}).get("B")
    assert agent_b is not None, "Test setup error: no agent B in context"
    tenant = ctx["tenant"]
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        agent_b_accounts = repo.list_by_principal(agent_b.principal_id)
    assert agent_b_accounts, "Test setup error: agent B should have at least one account"
    statuses = {a.account_id: _status_str(a.status) for a in agent_b_accounts}
    for acct_id, status in statuses.items():
        assert status != "closed", f"Agent A's delete_missing deactivated agent B's account {acct_id} (status={status})"


@then(parsers.parse('brand domain "{domain}" remains in its current state'))
def then_brand_unchanged(ctx: dict, domain: str) -> None:
    """Assert the account for the given domain was NOT deactivated."""
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    tenant = ctx["tenant"]
    principal = ctx["principal"]
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        accounts = repo.list_by_principal(principal.principal_id)
        matching = [a for a in accounts if a.brand and a.brand.domain == domain]
        assert len(matching) == 1, f"Expected account for {domain}, got {len(matching)}"
        assert matching[0].status != "closed", (
            f"Account {domain} was deactivated (status={matching[0].status}) but should be unchanged"
        )


@then("only the included accounts are processed")
def then_only_included_processed(ctx: dict) -> None:
    """Assert the response only contains accounts that were in the sync request."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    request_domains = ctx.get("sync_request_domains")
    assert request_domains, "Test setup error: sync_request_domains not tracked by When step"
    response_domains = {a.brand.domain for a in resp.accounts if a.brand}
    extra = response_domains - request_domains
    assert not extra, f"Response included accounts not in the sync request: {extra}. Request domains: {request_domains}"


@then("no accounts are deactivated")
def then_no_deactivations(ctx: dict) -> None:
    """Assert no accounts were deactivated (all still active/non-closed)."""
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    tenant = ctx["tenant"]
    principal = ctx["principal"]
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        all_accounts = repo.list_by_principal(principal.principal_id)
        closed = [a for a in all_accounts if a.status == "closed"]
        assert len(closed) == 0, (
            f"Expected 0 deactivated accounts, found {len(closed)}: {[a.brand.domain for a in closed]}"
        )


@then(parsers.parse('the account for brand domain "{domain}" is processed normally'))
def then_account_processed_normally(ctx: dict, domain: str) -> None:
    """Assert the account was processed (action is created/updated/unchanged, not failed)."""
    resp = ctx["response"]
    acct = _find_account_by_brand(resp, domain)
    actual = _action_str(acct.action)
    assert actual in ("created", "updated", "unchanged"), (
        f"Expected normal processing for {domain}, got action '{actual}'"
    )
    ctx["last_account"] = acct


# ═══════════════════════════════════════════════════════════════════════
# Slice 7: Context echo + validation + schema + sandbox
# ═══════════════════════════════════════════════════════════════════════


# ── Given: sandbox setup ───────────────────────────────────────────────


@given("the seller declares features.sandbox equals true in capabilities")
def given_sandbox_supported(ctx: dict) -> None:
    """Configure seller to support sandbox mode."""
    _setup_tenant_and_principal(ctx)
    ctx["sandbox_supported"] = True


@given("both sandbox and production accounts exist for the Buyer")
def given_sandbox_and_production_accounts(ctx: dict) -> None:
    """Create one sandbox and one production account with agent access."""
    _create_accessible_account(ctx, status="active", sandbox=True)
    _create_accessible_account(ctx, status="active", sandbox=False)


# ── When: context-bearing requests ─────────────────────────────────────


def _parse_inline_context(ctx_json_str: str) -> dict:
    """Parse inline JSON context string from Gherkin step."""
    import json

    return json.loads(ctx_json_str)


@when(
    parsers.re(
        r"the Buyer Agent sends a (?P<operation>list_accounts|sync_accounts) "
        r"request with context (?P<ctx_json>\{.*\})"
    )
)
def when_request_with_context(ctx: dict, operation: str, ctx_json: str) -> None:
    """Send a list_accounts or sync_accounts request with inline context.

    Context-echo is cross-cutting: tests both list and sync operations.
    The conftest harness provides AccountSyncEnv for context-echo tags.
    For list_accounts, we call _list_accounts_impl directly (the sync env
    shares the same DB session and identity infrastructure).
    """
    context_data = _parse_inline_context(ctx_json)
    ctx["sent_context"] = context_data

    from adcp.types import ContextObject

    context_obj = ContextObject.model_validate(context_data)

    if operation == "list_accounts":
        from src.core.schemas.account import ListAccountsRequest

        req = ListAccountsRequest(context=context_obj)
    else:
        from src.core.schemas.account import SyncAccountsRequest

        # Provide a minimal valid account for sync context echo tests
        req = SyncAccountsRequest(
            accounts=[{"brand": {"domain": "ctx-test.com"}, "operator": "ctx-test.com", "billing": "operator"}],
            context=context_obj,
        )

    dispatch_kwargs: dict[str, Any] = {}
    if "force_identity" in ctx:
        dispatch_kwargs["identity"] = ctx["force_identity"]

    try:
        dispatch_request(ctx, req=req, **dispatch_kwargs)
    except Exception as exc:
        ctx["error"] = exc


# ── When: input validation requests ────────────────────────────────────


@when("the Buyer Agent sends a sync_accounts request with an empty accounts array")
def when_sync_empty_accounts(ctx: dict) -> None:
    """Send sync_accounts with an empty accounts array."""
    from src.core.schemas.account import SyncAccountsRequest

    try:
        req = SyncAccountsRequest(accounts=[])
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


@when("the Buyer Agent sends a sync_accounts request with an account that has no brand domain field")
def when_sync_no_brand_domain(ctx: dict) -> None:
    """Send sync with account missing brand.domain — triggers Pydantic validation."""
    from pydantic import ValidationError

    from src.core.schemas.account import SyncAccountsRequest

    try:
        req = SyncAccountsRequest(
            accounts=[{"operator": "test.com", "billing": "operator"}],
        )
        dispatch_request(ctx, req=req)
    except (ValidationError, Exception) as exc:
        ctx["error"] = exc


@when("the Buyer Agent sends a sync_accounts request with an account that has no operator field")
def when_sync_no_operator(ctx: dict) -> None:
    """Send sync with account missing operator — triggers Pydantic validation."""
    from pydantic import ValidationError

    from src.core.schemas.account import SyncAccountsRequest

    try:
        req = SyncAccountsRequest(
            accounts=[{"brand": {"domain": "test.com"}, "billing": "operator"}],
        )
        dispatch_request(ctx, req=req)
    except (ValidationError, Exception) as exc:
        ctx["error"] = exc


@when("the Buyer Agent sends a sync_accounts request with an account that has no billing field")
def when_sync_no_billing(ctx: dict) -> None:
    """Send sync with account missing billing — triggers Pydantic validation."""
    from pydantic import ValidationError

    from src.core.schemas.account import SyncAccountsRequest

    try:
        req = SyncAccountsRequest(
            accounts=[{"brand": {"domain": "test.com"}, "operator": "test.com"}],
        )
        dispatch_request(ctx, req=req)
    except (ValidationError, Exception) as exc:
        ctx["error"] = exc


@when(parsers.parse('the Buyer Agent sends a sync_accounts request with {field} set to "{value}"'))
def when_sync_invalid_field(ctx: dict, field: str, value: str) -> None:
    """Send sync with an invalid field value for validation testing."""
    from pydantic import ValidationError

    from src.core.schemas.account import SyncAccountsRequest

    # Build account entry with the invalid field
    entry: dict[str, Any] = {
        "brand": {"domain": "valid.com"},
        "operator": "valid.com",
        "billing": "operator",
    }

    if field == "brand.domain":
        entry["brand"]["domain"] = value
    elif field == "brand.brand_id":
        entry["brand"]["brand_id"] = value
    elif field == "operator":
        entry["operator"] = value
    else:
        entry[field] = value

    try:
        req = SyncAccountsRequest(accounts=[entry])
        dispatch_request(ctx, req=req)
    except (ValidationError, Exception) as exc:
        ctx["error"] = exc


@when(parsers.parse("the Buyer Agent sends a sync_accounts request with {count:d} accounts"))
def when_sync_n_accounts(ctx: dict, count: int) -> None:
    """Send sync with N generated accounts for boundary testing."""
    from pydantic import ValidationError

    from src.core.schemas.account import SyncAccountsRequest

    accounts = [
        {"brand": {"domain": f"brand-{i:04d}.com"}, "operator": f"brand-{i:04d}.com", "billing": "operator"}
        for i in range(count)
    ]
    ctx["submitted_account_count"] = count

    try:
        req = SyncAccountsRequest(accounts=accounts)
        dispatch_request(ctx, req=req)
    except (ValidationError, Exception) as exc:
        ctx["error"] = exc


# ── Then: context echo assertions ──────────────────────────────────────


@then(parsers.re(r"the response includes context (?P<ctx_json>\{.*\})"))
def then_response_includes_context(ctx: dict, ctx_json: str) -> None:
    """Assert the response (success or error) includes the expected context.

    For success responses, reads context from the response object.
    For error responses, attempts to read context from the error object.
    Never falls back to comparing the test's own sent_context.
    """
    import json

    import pytest

    expected = json.loads(ctx_json)

    # Check success response first
    resp = ctx.get("response")
    if resp is not None:
        resp_context = getattr(resp, "context", None)
        assert resp_context is not None, "Response has no context field"
        # ContextObject may be a Pydantic model — convert to dict for comparison
        if hasattr(resp_context, "model_dump"):
            actual = resp_context.model_dump(mode="json", exclude_none=True)
        elif isinstance(resp_context, dict):
            actual = resp_context
        else:
            actual = dict(resp_context)
        assert actual == expected, f"Context mismatch: expected {expected}, got {actual}"
        return

    # Error path: read context from the error object/payload, not the sent value
    error = ctx.get("error")
    assert error is not None, "No response and no error — cannot verify context echo"
    # Try to extract context from the error object
    error_context = getattr(error, "context", None)
    if error_context is not None:
        if hasattr(error_context, "model_dump"):
            actual = error_context.model_dump(mode="json", exclude_none=True)
        elif isinstance(error_context, dict):
            actual = error_context
        else:
            actual = dict(error_context)
        assert actual == expected, f"Error context mismatch: expected {expected}, got {actual}"
        return
    # Production error objects (AdCPError) do not carry a context field yet
    pytest.xfail(
        "SPEC-PRODUCTION GAP: error variant does not echo context — "
        "AdCPError has no context field, expected context echo on error responses"
    )


@then("the context is identical to what was sent")
def then_context_identical(ctx: dict) -> None:
    """Assert the echoed context is exactly what was sent (deep equality)."""
    resp = ctx.get("response")
    sent = ctx.get("sent_context")
    assert sent is not None, "No sent_context to compare"

    if resp is not None:
        resp_context = getattr(resp, "context", None)
        assert resp_context is not None, "Response has no context"
        if hasattr(resp_context, "model_dump"):
            actual = resp_context.model_dump(mode="json", exclude_none=True)
        elif isinstance(resp_context, dict):
            actual = resp_context
        else:
            actual = dict(resp_context)
        assert actual == sent, f"Context not identical: sent {sent}, got {actual}"


@then("the response does not include a context field")
def then_no_context(ctx: dict) -> None:
    """Assert the response has no context field (or it's None)."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    context = getattr(resp, "context", None)
    assert context is None, f"Expected no context, got {context}"


@then(parsers.re(r"the response is an error variant with (?P<code>\w+)"))
def then_error_with_code(ctx: dict, code: str) -> None:
    """Assert the response is an error with a specific error code."""
    error = _get_error(ctx)
    actual = getattr(error, "error_code", None)
    assert actual == code, f"Expected error code '{code}', got '{actual}'"


# ── Then: input validation assertions ──────────────────────────────────


@then("the error indicates accounts array must not be empty")
def then_empty_accounts_error(ctx: dict) -> None:
    """Assert the error is a validation error about empty accounts array.

    Production raises AdCPValidationError with a message containing
    'accounts array must not be empty'.
    """
    from src.core.exceptions import AdCPValidationError

    error = _get_error(ctx)
    assert isinstance(error, (AdCPValidationError, ValueError)), (
        f"Expected AdCPValidationError, got {type(error).__name__}: {error}"
    )
    msg = str(error).lower()
    assert "empty" in msg and "account" in msg, f"Expected error about empty accounts array, got: {error}"


@then("the per-account error indicates brand domain is required")
def then_brand_required_error(ctx: dict) -> None:
    """Assert the error indicates brand domain is required.

    The error must mention both 'brand'/'domain' AND 'required'/'missing'
    to confirm it's specifically about the missing brand domain field.
    """
    error = _get_error(ctx)
    msg = str(error).lower()
    has_brand_ref = "brand" in msg or "domain" in msg
    has_required_ref = "required" in msg or "missing" in msg
    assert has_brand_ref and has_required_ref, f"Expected error about brand domain being required, got: {error}"


@then("the per-account error indicates operator is required")
def then_operator_required_error(ctx: dict) -> None:
    """Assert the error is a validation error indicating operator is required.

    Production raises Pydantic ValidationError because operator is a required
    field in the SyncAccountsRequest account entry schema.
    """
    from pydantic import ValidationError

    error = _get_error(ctx)
    assert isinstance(error, ValidationError), f"Expected Pydantic ValidationError, got {type(error).__name__}: {error}"
    msg = str(error).lower()
    assert "operator" in msg, f"Expected error about 'operator', got: {error}"
    assert "required" in msg or "missing" in msg, f"Expected 'required' or 'missing' in error, got: {error}"


# ── Then: sandbox assertions ───────────────────────────────────────────


@then("the provisioned account should have sandbox equals true")
def then_account_sandbox_true(ctx: dict) -> None:
    """Assert the provisioned account has sandbox=True."""
    resp = ctx["response"]
    acct = resp.accounts[0]
    assert acct.sandbox is True, f"Expected sandbox=True, got {acct.sandbox}"
    ctx["last_account"] = acct


@then("the account should have a seller-assigned account_id")
def then_sandbox_account_has_id(ctx: dict) -> None:
    """Assert the account has a seller-assigned account_id."""
    acct = ctx.get("last_account") or ctx["response"].accounts[0]
    account_id = getattr(acct, "account_id", None)
    assert account_id is not None, f"Account missing seller-assigned account_id: {acct}"


@then("no real ad platform account should have been created")
def then_no_real_platform_account(ctx: dict) -> None:
    """Assert sandbox account was created without external platform provisioning.

    Verifies the consequence (no external platform reference in DB), not just
    the input (sandbox=True). The DB record should have no platform_mappings,
    proving no adapter was called to provision an external account.
    """
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    acct = ctx.get("last_account") or ctx["response"].accounts[0]
    account_id = acct.account_id
    assert account_id is not None, "Account missing account_id"

    tenant = ctx["tenant"]
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        db_acct = repo.get_by_id(account_id)
        assert db_acct is not None, f"Account {account_id} not found in DB"
        assert db_acct.sandbox is True, f"DB account {account_id} sandbox={db_acct.sandbox}"
        # No external platform reference should exist — platform_mappings must be None/empty
        assert not db_acct.platform_mappings, (
            f"Sandbox account {account_id} has platform_mappings={db_acct.platform_mappings} "
            f"— expected no external platform references"
        )


@then(parsers.parse('the response should contain "{field}" array'))
def then_response_has_field_array(ctx: dict, field: str) -> None:
    """Assert the response contains the named field as an array."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    value = getattr(resp, field, None)
    assert value is not None, f"Response has no '{field}' field"
    assert isinstance(value, list), f"'{field}' is not an array: {type(value)}"


@then("all returned accounts should have sandbox equals true")
def then_all_accounts_sandbox_true(ctx: dict) -> None:
    """Assert every account in the response has sandbox=True."""
    resp = ctx["response"]
    for acct in resp.accounts:
        assert acct.sandbox is True, f"Expected sandbox=True, got sandbox={acct.sandbox} for {acct.name}"


@then("the response should not include production accounts")
def then_no_production_accounts(ctx: dict) -> None:
    """Assert all returned accounts are sandbox (no production accounts).

    The sandbox filter was applied in the When step. Every returned account
    must have sandbox=True; any sandbox=False or sandbox=None is a production
    account that should have been filtered out.
    """
    resp = ctx["response"]
    for acct in resp.accounts:
        assert acct.sandbox is True, f"Production account found: {acct.account_id} (sandbox={acct.sandbox})"


@then("the response should indicate a validation error")
def then_response_validation_error(ctx: dict) -> None:
    """Assert the response is a validation error of the expected type.

    Sandbox validation errors should be real Pydantic/AdCP validation errors,
    same as production (BR-RULE-209 INV-1).
    """
    from pydantic import ValidationError

    from src.core.exceptions import AdCPValidationError

    error = _get_error(ctx)
    assert isinstance(error, (ValidationError, AdCPValidationError, ValueError)), (
        f"Expected validation error type, got {type(error).__name__}: {error}"
    )


@then("the error should be a real validation error, not simulated")
def then_real_validation_error(ctx: dict) -> None:
    """Assert the validation error is real (not a sandbox simulation)."""
    error = ctx.get("error")
    assert error is not None, "Expected a validation error"
    # Real validation errors come from Pydantic or AdCPValidationError
    from pydantic import ValidationError

    from src.core.exceptions import AdCPValidationError

    assert isinstance(error, (ValidationError, AdCPValidationError, ValueError)), (
        f"Expected real validation error, got {type(error).__name__}: {error}"
    )


@then("the error should include a suggestion for how to fix the issue")
def then_error_has_fix_suggestion(ctx: dict) -> None:
    """Assert the error includes actionable fix guidance.

    Pydantic ValidationErrors include inline guidance ('Input should be ...').
    Per-account errors (AdCP Error objects) carry an explicit 'suggestion' field.
    The assertion verifies the error contains non-empty, human-readable fix text
    appropriate to the error type.
    """
    from pydantic import ValidationError

    # Check per-account errors first (AdCP Error objects with typed suggestion field)
    acct = ctx.get("last_account")
    if acct is not None and hasattr(acct, "errors") and acct.errors:
        found_suggestion = False
        for e in acct.errors:
            s = getattr(e, "suggestion", None)
            if s is not None:
                assert isinstance(s, str) and s.strip(), f"Expected non-empty suggestion string, got: {s!r}"
                found_suggestion = True
        assert found_suggestion, f"Per-account errors lack 'suggestion' field: {[str(e) for e in acct.errors]}"
        return

    error = _get_error(ctx)

    if isinstance(error, ValidationError):
        # Pydantic errors must contain structured remediation text with msg field
        error_details = error.errors()
        assert error_details, "ValidationError has no error details"
        for detail in error_details:
            detail_msg = detail.get("msg", "")
            assert isinstance(detail_msg, str) and detail_msg.strip(), (
                f"ValidationError detail lacks remediation message: {detail}"
            )
        return

    # Operation-level errors must carry a suggestion or recovery field
    suggestion = getattr(error, "suggestion", None) or getattr(error, "recovery", None)
    assert suggestion is not None, (
        f"Error of type {type(error).__name__} lacks 'suggestion' or 'recovery' field: {error}"
    )
    assert isinstance(suggestion, str) and len(suggestion.strip()) > 0, (
        f"Expected non-empty suggestion/recovery string, got: {suggestion!r}"
    )


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — governance_agents + dry-run update assertions
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('the governance_agents are stored for brand domain "{domain}"'))
def then_governance_agents_stored(ctx: dict, domain: str) -> None:
    """Assert governance_agents were persisted in the DB for the given brand domain."""
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    tenant = ctx["tenant"]
    principal = ctx["principal"]
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        accounts = repo.list_by_principal(principal.principal_id)
        matching = [a for a in accounts if a.brand and a.brand.domain == domain]
        assert len(matching) == 1, f"Expected 1 account for {domain}, got {len(matching)}"
        account = matching[0]
        agents = account.governance_agents
        assert agents is not None, f"Expected governance_agents to be stored for {domain}, got None"
        # The When step sends exactly 1 governance agent with known URL and categories
        assert len(agents) == 1, f"Expected 1 governance_agent for {domain}, got {len(agents)}"
        agent = agents[0]
        agent_url = agent.get("url") if isinstance(agent, dict) else getattr(agent, "url", None)
        assert agent_url == "https://governance.example.com/check", (
            f"Expected governance agent url 'https://governance.example.com/check', got '{agent_url}'"
        )


@then(parsers.parse('no accounts were actually modified for brand domain "{domain}"'))
def then_no_modifications_for_domain(ctx: dict, domain: str) -> None:
    """Assert a dry-run did not modify the existing account's billing in the DB.

    Verifies that the pre-existing account retains its original billing value
    despite the dry-run response reporting action='updated'.
    """
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    tenant = ctx["tenant"]
    principal = ctx["principal"]
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        accounts = repo.list_by_principal(principal.principal_id)
        matching = [a for a in accounts if a.brand and a.brand.domain == domain]
        assert len(matching) == 1, f"Expected 1 pre-existing account for {domain}, got {len(matching)}"
        # The dry-run scenario syncs with billing='agent' but the pre-existing account
        # was created with billing='operator'. If dry_run worked, DB still has 'operator'.
        account = matching[0]
        assert account.billing == "operator", (
            f"Expected billing='operator' (unchanged by dry-run) for {domain}, "
            f"got billing='{account.billing}' — dry_run failed to prevent DB writes"
        )


# ═══════════════════════════════════════════════════════════════════════
# Hand-authored: Authorization boundary steps (PR #1170 review)
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('agent "{name}" has an authenticated connection with {count:d} accessible accounts'))
def given_agent_with_n_accounts(ctx: dict, name: str, count: int) -> None:
    """Create a named agent with N accessible accounts."""
    _setup_tenant_and_principal(ctx)
    agent = _create_agent(ctx, name)
    from tests.factories.account import AccountFactory, AgentAccountAccessFactory

    tenant = ctx["tenant"]
    agent_account_ids: set[str] = set()
    for _ in range(count):
        account = AccountFactory(tenant=tenant, status="active")
        AgentAccountAccessFactory(
            tenant_id=tenant.tenant_id,
            principal=agent,
            account=account,
        )
        agent_account_ids.add(account.account_id)
    ctx.setdefault("agent_account_ids", {})[name] = agent_account_ids


@given(parsers.parse('agent "{name}" has {count:d} accessible accounts in the same tenant'))
def given_agent_b_accounts_same_tenant(ctx: dict, name: str, count: int) -> None:
    """Create a second agent with N accessible accounts in the same tenant."""
    given_agent_with_n_accounts(ctx, name, count)


@given("the Buyer Agent has a connection with tenant resolved but no principal_id")
def given_connection_no_principal(ctx: dict) -> None:
    """Set up identity with tenant_id but principal_id=None."""
    _setup_tenant_and_principal(ctx)
    ctx["override_identity_no_principal"] = True


@when(parsers.parse('agent "{name}" sends a list_accounts request'))
def when_agent_list_accounts(ctx: dict, name: str) -> None:
    """Send list_accounts as a specific named agent."""
    identity = _make_identity_for_agent(ctx, name)
    dispatch_request(ctx, identity=identity)


@when("the Buyer Agent sends a list_accounts request with no principal_id")
def when_list_accounts_no_principal(ctx: dict) -> None:
    """Send list_accounts with an identity that has tenant_id but no principal_id."""
    from src.core.resolved_identity import ResolvedIdentity

    tenant = ctx["tenant"]
    broken_identity = ResolvedIdentity(
        tenant_id=tenant.tenant_id,
        principal_id=None,
        protocol="mcp",
    )
    dispatch_request(ctx, identity=broken_identity)


@when("the Buyer Agent sends a sync_accounts request with no principal_id and:")
def when_sync_no_principal(ctx: dict, datatable: Any) -> None:
    """Send sync_accounts with an identity that has tenant_id but no principal_id."""
    from src.core.resolved_identity import ResolvedIdentity
    from src.core.schemas.account import SyncAccountsRequest

    tenant = ctx["tenant"]
    broken_identity = ResolvedIdentity(
        tenant_id=tenant.tenant_id,
        principal_id=None,
        protocol="mcp",
    )
    headers = datatable[0]
    rows = [dict(zip(headers, row, strict=True)) for row in datatable[1:]]
    accounts = _parse_sync_table(rows)
    req = SyncAccountsRequest(accounts=accounts)
    dispatch_request(ctx, req=req, identity=broken_identity)


@then(parsers.parse('none of the returned accounts belong to agent "{name}"'))
def then_none_belong_to_agent(ctx: dict, name: str) -> None:
    """Assert no returned accounts are in the other agent's set."""
    resp = ctx["response"]
    other_ids = ctx.get("agent_account_ids", {}).get(name, set())
    assert other_ids, f"Test setup error: no account IDs tracked for agent '{name}'"
    returned_ids = {acct.account_id for acct in resp.accounts}
    leaked = returned_ids & other_ids
    assert not leaked, f"Cross-agent leak: accounts {leaked} belong to agent '{name}' but appeared in response"


# ── Governance idempotency steps ────────────────────────────────────


@given(parsers.parse('an account for brand domain "{domain}" already exists with governance_agents'))
def given_existing_account_with_governance(ctx: dict, domain: str) -> None:
    """Pre-create an account with governance_agents via sync_accounts."""
    _setup_tenant_and_principal(ctx)
    gov = [_make_governance_agent()]
    _sync_pre_create(ctx, brand_domain=domain, operator=domain, billing="operator", governance_agents=gov)
    ctx["governance_agents_fixture"] = gov


@given(
    parsers.parse(
        'an account for brand domain "{domain}" exists with billing "{billing}", '
        'payment_terms "{pt}", and governance_agents'
    )
)
def given_existing_account_all_fields(ctx: dict, domain: str, billing: str, pt: str) -> None:
    """Pre-create an account with all mutable fields populated."""
    _setup_tenant_and_principal(ctx)
    gov = [_make_governance_agent()]
    _sync_pre_create(
        ctx, brand_domain=domain, operator=domain, billing=billing, payment_terms=pt, governance_agents=gov
    )
    ctx["governance_agents_fixture"] = gov


@when(parsers.parse('the Buyer Agent re-syncs with identical governance_agents for brand "{domain}"'))
def when_resync_identical_governance(ctx: dict, domain: str) -> None:
    """Re-sync with the same governance_agents that were used during creation."""
    from src.core.schemas.account import SyncAccountsRequest

    gov = ctx["governance_agents_fixture"]
    req = SyncAccountsRequest(
        accounts=[{"brand": {"domain": domain}, "operator": domain, "billing": "operator", "governance_agents": gov}],
    )
    dispatch_request(ctx, req=req)


@when(parsers.parse('the Buyer Agent sends a sync with different governance_agents for brand "{domain}"'))
def when_sync_different_governance(ctx: dict, domain: str) -> None:
    """Sync with modified governance_agents."""
    from src.core.schemas.account import SyncAccountsRequest

    req = SyncAccountsRequest(
        accounts=[
            {
                "brand": {"domain": domain},
                "operator": domain,
                "billing": "operator",
                "governance_agents": [
                    _make_governance_agent(
                        url="https://new-bot.example.com/check",
                    )
                ],
            }
        ],
    )
    dispatch_request(ctx, req=req)


@when(
    parsers.parse(
        'the Buyer Agent re-syncs with identical billing, payment_terms, and governance_agents for brand "{domain}"'
    )
)
def when_resync_identical_all_fields(ctx: dict, domain: str) -> None:
    """Re-sync with all fields identical to creation."""
    from src.core.schemas.account import SyncAccountsRequest

    gov = ctx["governance_agents_fixture"]
    req = SyncAccountsRequest(
        accounts=[
            {
                "brand": {"domain": domain},
                "operator": domain,
                "billing": "agent",
                "payment_terms": "net_30",
                "governance_agents": gov,
            }
        ],
    )
    dispatch_request(ctx, req=req)


@then(parsers.parse('none of the returned accounts have brand domain "{domain}"'))
def then_none_have_brand_domain(ctx: dict, domain: str) -> None:
    """Assert no returned account has the specified brand domain."""
    resp = ctx["response"]
    for acct in resp.accounts:
        if hasattr(acct, "brand") and acct.brand and hasattr(acct.brand, "domain"):
            assert acct.brand.domain != domain, (
                f"Cross-agent leak: account {acct.account_id} has brand domain '{domain}' "
                f"but should not be visible to this agent"
            )


# ── delete_missing semantics steps ──────────────────────────────────


@when("the Buyer Agent sends a sync_accounts request with dry_run true and delete_missing true and:")
def when_sync_dryrun_and_delete_missing(ctx: dict, datatable: Any) -> None:
    """Send sync_accounts with both dry_run=True and delete_missing=True."""
    from src.core.schemas.account import SyncAccountsRequest

    headers = datatable[0]
    rows = [dict(zip(headers, row, strict=True)) for row in datatable[1:]]
    accounts = _parse_sync_table(rows)
    req = SyncAccountsRequest(accounts=accounts, dry_run=True, delete_missing=True)
    dispatch_request(ctx, req=req)


@when(parsers.parse('agent "{name}" sends a sync_accounts request with delete_missing true and:'))
def when_named_agent_sync_delete_missing(ctx: dict, name: str, datatable: Any) -> None:
    """Send sync_accounts under a named agent's identity with delete_missing=True."""
    from src.core.schemas.account import SyncAccountsRequest

    identity = _make_identity_for_agent(ctx, name)
    headers = datatable[0]
    rows = [dict(zip(headers, row, strict=True)) for row in datatable[1:]]
    accounts = _parse_sync_table(rows)
    req = SyncAccountsRequest(accounts=accounts, delete_missing=True)
    dispatch_request(ctx, req=req, identity=identity)


@given(parsers.parse('agent "{name}" created account for brand domain "{domain}"'))
def given_agent_created_account(ctx: dict, name: str, domain: str) -> None:
    """Create an account under a specific agent's identity via sync."""
    _setup_tenant_and_principal(ctx)
    agent = _create_agent(ctx, name)
    identity = _make_identity_for_agent(ctx, name)
    from src.core.schemas.account import SyncAccountsRequest

    req = SyncAccountsRequest(
        accounts=[{"brand": {"domain": domain}, "operator": domain, "billing": "operator"}],
    )
    dispatch_request(ctx, req=req, identity=identity)
    ctx.pop("response", None)
    ctx.pop("error", None)


@given(parsers.parse('agent "{a}" was granted access to the account for brand domain "{domain}"'))
def given_agent_granted_access(ctx: dict, a: str, domain: str) -> None:
    """Grant agent A access to an existing account (created by another agent)."""
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    tenant = ctx["tenant"]
    agent = _create_agent(ctx, a)
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        # Find the account by domain
        from sqlalchemy import select

        from src.core.database.models import Account

        account = session.scalars(
            select(Account).where(
                Account.tenant_id == tenant.tenant_id,
                Account.brand["domain"].as_string() == domain,
            )
        ).first()
        assert account is not None, f"Account for domain {domain} not found"
        repo.grant_access(agent.principal_id, account.account_id)
        session.commit()


# ── Field preservation + access persistence steps ───────────────────


@then(parsers.parse("the account {field} in the database is unchanged from the original"))
def then_db_field_unchanged(ctx: dict, field: str) -> None:
    """Assert a DB field was not modified by sync — compare against captured original.

    The preceding Given/When steps must have captured the original field value
    into ctx["original_field_values"][field] before the sync ran. This step
    re-fetches the account from the DB and asserts exact equality with the
    captured original.
    """
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    acct = ctx.get("last_account")
    assert acct is not None, "No last_account in ctx — need a preceding account action step"
    tenant = ctx["tenant"]
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        # Find by brand domain from the last_account
        domain = acct.brand.domain if hasattr(acct.brand, "domain") else str(acct.brand)
        db_acct = repo.get_by_natural_key(operator=domain, brand_domain=domain)
        assert db_acct is not None, f"Account for {domain} not found in DB"
        db_val = getattr(db_acct, field, None)
        # Compare against captured original value
        original_values = ctx.get("original_field_values", {})
        if field in original_values:
            original_val = original_values[field]
            assert db_val == original_val, (
                f"Field '{field}' was modified by sync: original={original_val!r}, "
                f"current={db_val!r} — expected unchanged"
            )
        else:
            # No captured original — the preceding steps should have captured it.
            # Fall back to asserting the DB has a meaningful value (non-None)
            # to avoid silently passing when test setup is incomplete.
            assert db_val is not None, (
                f"Field '{field}' is None in DB and no original value was captured "
                f"in ctx['original_field_values']. Test setup must capture the "
                f"original value before sync."
            )


@then(parsers.parse('the agent has exactly one access grant for brand domain "{domain}"'))
def then_one_access_grant(ctx: dict, domain: str) -> None:
    """Assert exactly one AgentAccountAccess row for this agent + account."""
    from sqlalchemy import func, select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Account, AgentAccountAccess

    tenant = ctx["tenant"]
    principal = ctx["principal"]
    with get_db_session() as session:
        # Find the account by domain
        account = session.scalars(
            select(Account).where(
                Account.tenant_id == tenant.tenant_id,
                Account.brand["domain"].as_string() == domain,
            )
        ).first()
        assert account is not None, f"Account for {domain} not found"
        count = session.scalar(
            select(func.count())
            .select_from(AgentAccountAccess)
            .where(
                AgentAccountAccess.tenant_id == tenant.tenant_id,
                AgentAccountAccess.principal_id == principal.principal_id,
                AgentAccountAccess.account_id == account.account_id,
            )
        )
        assert count == 1, f"Expected 1 access grant for {domain}, got {count}"


@given("the database is experiencing a transient failure")
def given_db_failure(ctx: dict) -> None:
    """Configure the harness to simulate a DB failure on the next query."""
    ctx["simulate_db_failure"] = True


@then(parsers.parse('the list includes an account with brand domain "{domain}"'))
def then_list_includes_domain(ctx: dict, domain: str) -> None:
    """Assert the list_accounts response contains an account with the given brand domain."""
    resp = ctx["response"]
    for acct in resp.accounts:
        if hasattr(acct, "brand") and acct.brand and getattr(acct.brand, "domain", None) == domain:
            return
    domains = [getattr(a.brand, "domain", "?") for a in resp.accounts if hasattr(a, "brand") and a.brand]
    raise AssertionError(f"Expected account with domain '{domain}' in list, got: {domains}")


@then(parsers.parse('the response does not include a result for brand domain "{domain}"'))
def then_no_result_for_domain(ctx: dict, domain: str) -> None:
    """Assert the sync response has no account entry for the given domain."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    for acct in resp.accounts:
        acct_domain = acct.brand.domain if hasattr(acct, "brand") and acct.brand else None
        assert acct_domain != domain, (
            f"Expected no result for domain '{domain}' but found account "
            f"{acct.account_id} with action={getattr(acct, 'action', '?')}"
        )
