"""BDD step definitions for UC-002: Create Media Buy — account resolution scenarios.

Focuses on account resolution error paths (ext-r, ext-s, ext-t, BR-RULE-080)
and partition/boundary scenarios for account_ref.

Steps delegate to MediaBuyAccountEnv which calls resolve_account() with real DB.

beads: salesagent-2rq
"""

from __future__ import annotations

from pytest_bdd import given, parsers, then, when

from tests.factories.account import AccountFactory, AgentAccountAccessFactory

# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — request setup and account state
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('a valid create_media_buy request with account_id "{account_id}"'))
def given_request_with_account_id(ctx: dict, account_id: str) -> None:
    """Set up a create_media_buy request referencing an explicit account_id."""
    from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference1

    ctx["account_ref"] = AccountReference(root=AccountReference1(account_id=account_id))
    ctx["request_account_id"] = account_id


@given(parsers.parse('a valid create_media_buy request with account natural key brand "{brand}" operator "{operator}"'))
def given_request_with_natural_key(ctx: dict, brand: str, operator: str) -> None:
    """Set up a create_media_buy request referencing a natural key (brand + operator)."""
    from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference2
    from adcp.types.generated_poc.core.brand_ref import BrandReference

    ctx["account_ref"] = AccountReference(
        root=AccountReference2(brand=BrandReference(domain=brand), operator=operator),
    )
    ctx["request_brand"] = brand
    ctx["request_operator"] = operator


@given("a create_media_buy request without account field")
def given_request_without_account(ctx: dict) -> None:
    """Set up a create_media_buy request with no account field."""
    ctx["account_ref"] = None
    ctx["account_absent"] = True


@given("a valid create_media_buy request with creative assignments")
def given_request_with_creative_assignments(ctx: dict) -> None:
    """Set up a create_media_buy request with creative assignments (account is implicit)."""
    ctx.setdefault("account_ref", None)


@given("a valid create_media_buy request")
def given_valid_request(ctx: dict) -> None:
    """Set up a generic valid create_media_buy request (account populated separately)."""
    ctx.setdefault("account_ref", None)


@given(parsers.parse('a valid create_media_buy request with account "{account_id}"'))
def given_request_with_account(ctx: dict, account_id: str) -> None:
    """Set up a create_media_buy request with account (short form)."""
    from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference1

    ctx["account_ref"] = AccountReference(root=AccountReference1(account_id=account_id))
    ctx["request_account_id"] = account_id


@given("the account_id does not exist in the seller's account store")
def given_account_id_not_found(ctx: dict) -> None:
    """Verify the account_id from the request does not exist via production resolve_account."""
    from src.core.exceptions import AdCPAccountNotFoundError

    env = ctx["env"]
    try:
        # TRANSPORT-BYPASS: Given step verifies precondition state, not request dispatch
        env.call_impl(account_ref=ctx["account_ref"])
        raise AssertionError("Expected account not found, but resolve_account succeeded")
    except AdCPAccountNotFoundError:
        pass  # Correct — account doesn't exist


@given("no account matches the brand + operator combination")
def given_natural_key_not_found(ctx: dict) -> None:
    """Verify no account matches the natural key via production resolve_account."""
    from src.core.exceptions import AdCPAccountNotFoundError

    env = ctx["env"]
    try:
        # TRANSPORT-BYPASS: Given step verifies precondition state, not request dispatch
        env.call_impl(account_ref=ctx["account_ref"])
        raise AssertionError("Expected account not found, but resolve_account succeeded")
    except AdCPAccountNotFoundError:
        pass  # Correct — no matching account


@given(parsers.parse('the account "{account_id}" exists but requires setup (billing not configured)'))
def given_account_needs_setup(ctx: dict, account_id: str) -> None:
    """Create account with pending_approval status (setup not complete)."""
    env = ctx["env"]
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    tenant, principal = ctx["tenant"], ctx["principal"]
    account = AccountFactory(
        tenant=tenant,
        account_id=account_id,
        status="pending_approval",
        brand={"domain": "setup-needed.com"},
        operator="setup-needed.com",
    )
    AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)


@given(parsers.parse("the natural key matches {count:d} accounts"))
def given_multiple_matches(ctx: dict, count: int) -> None:
    """Create multiple accounts matching the same natural key."""
    env = ctx["env"]
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    else:
        tenant = ctx["tenant"]
        principal = ctx["principal"]

    brand = ctx.get("request_brand", "multi-brand.com")
    operator = ctx.get("request_operator", "agency.com")

    for i in range(count):
        account = AccountFactory(
            tenant=tenant,
            account_id=f"acc-multi-{i}",
            brand={"domain": brand},
            operator=operator,
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)


@given(parsers.parse('the account "{account_id}" exists and is active'))
def given_account_exists_active(ctx: dict, account_id: str) -> None:
    """Create an active account with agent access."""
    env = ctx["env"]
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    else:
        tenant = ctx["tenant"]
        principal = ctx["principal"]

    account = AccountFactory(
        tenant=tenant,
        account_id=account_id,
        status="active",
        brand={"domain": f"{account_id}.com"},
        operator=f"{account_id}.com",
    )
    AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)


@given("the account exists and is active")
def given_account_active(ctx: dict) -> None:
    """Create an active account for the current request context."""
    env = ctx["env"]
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    else:
        tenant = ctx["tenant"]
        principal = ctx["principal"]

    account_id = ctx.get("request_account_id", "acc-001")
    account = AccountFactory(
        tenant=tenant,
        account_id=account_id,
        status="active",
        brand={"domain": f"{account_id}.com"},
        operator=f"{account_id}.com",
    )
    AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)


@given(parsers.parse("a create_media_buy request with account configuration {partition}"))
def given_request_with_partition(ctx: dict, partition: str) -> None:
    """Set up request based on partition name (for Scenario Outline tables)."""
    from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference1, AccountReference2
    from adcp.types.generated_poc.core.brand_ref import BrandReference

    env = ctx["env"]
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    else:
        tenant = ctx["tenant"]
        principal = ctx["principal"]

    if partition == "explicit_account_id":
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-explicit",
            status="active",
            brand={"domain": "explicit.com"},
            operator="explicit.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-explicit"))

    elif partition == "natural_key_unambiguous":
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-natkey",
            status="active",
            brand={"domain": "natkey.com"},
            operator="natkey.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(
            root=AccountReference2(brand=BrandReference(domain="natkey.com"), operator="natkey.com"),
        )

    elif partition == "missing_account":
        ctx["account_ref"] = None
        ctx["account_absent"] = True

    elif partition == "invalid_oneOf_both":
        ctx["account_ref"] = None
        ctx["account_invalid_both"] = True

    elif partition == "explicit_not_found":
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-not-found"))

    elif partition == "natural_key_not_found":
        ctx["account_ref"] = AccountReference(
            root=AccountReference2(brand=BrandReference(domain="unknown.com"), operator="unknown.com"),
        )

    elif partition == "natural_key_ambiguous":
        for i in range(3):
            AccountFactory(
                tenant=tenant,
                account_id=f"acc-amb-{i}",
                status="active",
                brand={"domain": "ambiguous.com"},
                operator="ambiguous.com",
            )
        ctx["account_ref"] = AccountReference(
            root=AccountReference2(brand=BrandReference(domain="ambiguous.com"), operator="ambiguous.com"),
        )

    elif partition == "account_setup_required":
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-setup",
            status="pending_approval",
            brand={"domain": "setup.com"},
            operator="setup.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-setup"))

    elif partition == "account_payment_required":
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-payment",
            status="payment_required",
            brand={"domain": "payment.com"},
            operator="payment.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-payment"))

    elif partition == "account_suspended":
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-suspended",
            status="suspended",
            brand={"domain": "suspended.com"},
            operator="suspended.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-suspended"))

    else:
        raise ValueError(f"Unknown account partition: {partition}")


@given(parsers.parse("a create_media_buy request with account: {config}"))
def given_request_with_boundary_config(ctx: dict, config: str) -> None:
    """Set up request based on boundary config string."""
    from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference1, AccountReference2
    from adcp.types.generated_poc.core.brand_ref import BrandReference

    env = ctx["env"]
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    else:
        tenant = ctx["tenant"]
        principal = ctx["principal"]

    if config.startswith("acc-") and "active" in config:
        account_id = config.split()[0]
        account = AccountFactory(
            tenant=tenant,
            account_id=account_id,
            status="active",
            brand={"domain": f"{account_id}.com"},
            operator=f"{account_id}.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id=account_id))

    elif config.startswith("acc-") and "not-found" in config:
        account_id = config.split()[0]
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id=account_id))

    elif config.startswith("brand+op") and "single match" in config:
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-brand-single",
            status="active",
            brand={"domain": "single.com"},
            operator="single.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(
            root=AccountReference2(brand=BrandReference(domain="single.com"), operator="single.com"),
        )

    elif config.startswith("brand+op") and "no match" in config:
        ctx["account_ref"] = AccountReference(
            root=AccountReference2(brand=BrandReference(domain="nomatch.com"), operator="nomatch.com"),
        )

    elif config.startswith("brand+op") and "multi match" in config:
        for i in range(2):
            AccountFactory(
                tenant=tenant,
                account_id=f"acc-multi-{i}",
                status="active",
                brand={"domain": "multi.com"},
                operator="multi.com",
            )
        ctx["account_ref"] = AccountReference(
            root=AccountReference2(brand=BrandReference(domain="multi.com"), operator="multi.com"),
        )

    elif "setup-needed" in config:
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-setup",
            status="pending_approval",
            brand={"domain": "setup.com"},
            operator="setup.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-setup"))

    elif "payment-due" in config:
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-payment",
            status="payment_required",
            brand={"domain": "payment.com"},
            operator="payment.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-payment"))

    elif "suspended" in config:
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-suspended",
            status="suspended",
            brand={"domain": "suspended.com"},
            operator="suspended.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-suspended"))

    elif "no account" in config:
        ctx["account_ref"] = None
        ctx["account_absent"] = True

    elif "both fields" in config:
        ctx["account_ref"] = None
        ctx["account_invalid_both"] = True

    else:
        raise ValueError(f"Unknown boundary config: {config}")


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — send request
# ═══════════════════════════════════════════════════════════════════════


@when("the Buyer Agent sends the create_media_buy request")
def when_send_create_media_buy(ctx: dict) -> None:
    """Send the create_media_buy request and capture the result or error."""
    from tests.bdd.steps.generic._account_resolution import resolve_account_or_error

    resolve_account_or_error(ctx)


def _ensure_tenant_principal(ctx: dict, env: object) -> None:
    """Create tenant + principal if not already created by a Given step."""
    from tests.bdd.steps.generic._account_resolution import ensure_tenant_principal

    ensure_tenant_principal(ctx, env)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — account-specific assertions
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('the error should include "details" with setup instructions'))
def then_error_has_setup_details(ctx: dict) -> None:
    """Assert error details include setup instructions."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        assert error.details, f"Expected details on error: {error}"
        details_str = str(error.details).lower()
        assert "setup" in details_str or "billing" in details_str or "configure" in details_str, (
            f"Expected setup instructions in details: {error.details}"
        )
    else:
        raise AssertionError(f"Cannot check details on non-AdCPError: {type(error).__name__}")


@then(parsers.parse('the error message should contain "{count} accounts"'))
def then_error_contains_count(ctx: dict, count: str) -> None:
    """Assert error message mentions the specific number of matching accounts."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = str(error)
    assert f"{count} account" in msg.lower() or f"{count}" in msg, f"Expected '{count} accounts' in error: {msg}"


@then(parsers.parse("the result should be {outcome}"))
def then_result_should_be(ctx: dict, outcome: str) -> None:
    """Assert outcome of a partition/boundary scenario."""
    if outcome.startswith("account resolution succeeds"):
        assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
        assert "resolved_account_id" in ctx, "Expected resolved_account_id in ctx"
    elif outcome.startswith("error "):
        assert "error" in ctx, f"Expected an error for outcome: {outcome}"
        from src.core.exceptions import AdCPError

        error = ctx["error"]
        # Parse expected: "error CODE recovery_hint" or "error CODE with suggestion"
        parts = outcome[6:].strip().split()
        expected_code = parts[0]
        if isinstance(error, AdCPError):
            assert error.error_code == expected_code, f"Expected error code '{expected_code}', got '{error.error_code}'"
        # Check recovery hint if specified
        if len(parts) >= 2 and parts[1] in ("terminal", "correctable", "transient"):
            if isinstance(error, AdCPError):
                assert error.recovery == parts[1], f"Expected recovery '{parts[1]}', got '{error.recovery}'"
        # Check "with suggestion" if specified
        if "with suggestion" in outcome.lower() or "with" in parts:
            if isinstance(error, AdCPError) and error.details:
                assert "suggestion" in error.details, f"Expected suggestion in details: {error.details}"
    else:
        raise ValueError(f"Unknown outcome: {outcome}")


# ═══════════════════════════════════════════════════════════════════════
# Hand-authored: Authorization boundary steps (PR #1170 review)
# ═══════════════════════════════════════════════════════════════════════


@given("the account exists but is accessible only to a different agent")
def given_account_other_agent(ctx: dict) -> None:
    """Create an account with access granted to a different principal."""
    from tests.factories.principal import PrincipalFactory

    env = ctx["env"]
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    else:
        tenant = ctx["tenant"]

    account_id = ctx.get("request_account_id", "acc_other_agent")
    # Create account
    account = AccountFactory(
        tenant=tenant,
        account_id=account_id,
        status="active",
        brand={"domain": "other-agent-denied.com"},
        operator="other-agent-denied.com",
    )
    # Grant access to a DIFFERENT principal — not the requesting agent
    other_principal = PrincipalFactory(tenant=tenant)
    AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=other_principal, account=account)


@given("the natural key resolves to an account accessible only to a different agent")
def given_natural_key_other_agent(ctx: dict) -> None:
    """Create an account matching the natural key with access to a different principal."""
    from tests.factories.principal import PrincipalFactory

    env = ctx["env"]
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    else:
        tenant = ctx["tenant"]

    account = AccountFactory(
        tenant=tenant,
        status="active",
        brand={"domain": "other-agent.com"},
        operator="other-agent.com",
    )
    other_principal = PrincipalFactory(tenant=tenant)
    AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=other_principal, account=account)


@given("the sandbox account exists but is accessible only to a different agent")
def given_sandbox_account_other_agent(ctx: dict) -> None:
    """Create a sandbox account with access to a different principal."""
    from tests.factories.principal import PrincipalFactory

    env = ctx["env"]
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    else:
        tenant = ctx["tenant"]

    account_id = ctx.get("request_account_id", "acc_sandbox_other")
    account = AccountFactory(
        tenant=tenant,
        account_id=account_id,
        status="active",
        sandbox=True,
        brand={"domain": "sandbox-denied.com"},
        operator="sandbox-denied.com",
    )
    other_principal = PrincipalFactory(tenant=tenant)
    AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=other_principal, account=account)
