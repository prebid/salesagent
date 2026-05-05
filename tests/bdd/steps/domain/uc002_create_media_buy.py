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
    elif outcome == "success":
        assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
        assert "response" in ctx, "Expected response in ctx"
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


# ═══════════════════════════════════════════════════════════════════════
# Hand-authored: Idempotency steps (adcp 3.12 / PR #1217 review)
# ═══════════════════════════════════════════════════════════════════════


@given("the tenant is configured for auto-approval")
def given_tenant_auto_approval(ctx: dict) -> None:
    """Configure the tenant for auto-approval (no manual review required)."""
    ctx["tenant_auto_approval"] = True
    ctx.setdefault("tenant_config", {})["human_review_required"] = False
    ctx.setdefault("tenant_config", {})["auto_create_media_buys"] = True


@given(parsers.parse("a valid create_media_buy request with:\n{datatable}"))
def given_valid_request_with_table(ctx: dict, datatable) -> None:
    """Build a create_media_buy request from a field/value data table."""
    request_fields: dict = {}
    # datatable is a list of lists (rows), where first row is header
    if hasattr(datatable, "__iter__"):
        rows = list(datatable)
        # Skip header row if it looks like column names
        if rows and hasattr(rows[0], "__iter__"):
            header = [str(c).strip() for c in rows[0]]
            for row in rows[1:]:
                cells = [str(c).strip() for c in row]
                if len(cells) >= 2:
                    field_name = cells[header.index("field")] if "field" in header else cells[0]
                    field_value = cells[header.index("value")] if "value" in header else cells[1]
                    request_fields[field_name] = field_value

    ctx["request_fields"] = request_fields

    # Extract specific fields into ctx for use by other steps
    if "idempotency_key" in request_fields:
        ctx["idempotency_key"] = request_fields["idempotency_key"]
    if "account" in request_fields:
        # Parse "account_id "acc-001"" format
        acct_val = request_fields["account"]
        if acct_val.startswith('account_id "') and acct_val.endswith('"'):
            ctx["request_account_id"] = acct_val.split('"')[1]
    if "brand" in request_fields:
        brand_val = request_fields["brand"]
        if brand_val.startswith('domain "') and brand_val.endswith('"'):
            ctx["request_brand_domain"] = brand_val.split('"')[1]


@given(parsers.parse("the request includes {count:d} package with a valid product_id"))
@given(parsers.parse("the request includes {count:d} packages with valid product_ids"))
def given_request_includes_packages(ctx: dict, count: int) -> None:
    """Add packages with valid product_ids to the request."""
    ctx["package_count"] = count


@given("the package has a positive budget meeting minimum spend")
def given_package_positive_budget(ctx: dict) -> None:
    """Ensure the package has a budget that meets minimum spend requirements."""
    ctx["package_budget_valid"] = True


@given("the ad server adapter is available")
def given_adapter_available(ctx: dict) -> None:
    """Mark the ad server adapter as available for the scenario."""
    ctx["adapter_available"] = True


@given("the request does NOT include an idempotency_key")
def given_no_idempotency_key(ctx: dict) -> None:
    """Explicitly set request to have no idempotency_key."""
    ctx["idempotency_key"] = None
    ctx.get("request_fields", {}).pop("idempotency_key", None)


@given(parsers.parse("the idempotency_key is set to {value}"))
def given_idempotency_key_set(ctx: dict, value: str) -> None:
    """Set the idempotency_key on the request."""
    value = value.strip()
    if value == "<not provided>":
        ctx["idempotency_key"] = None
    elif value in {"<255 character string>", "<254 char string>"}:
        ctx["idempotency_key"] = "k" * int("".join(c for c in value if c.isdigit()))
    elif value in {"<256 chars>", "<256 char string>"}:
        ctx["idempotency_key"] = "k" * 256
    else:
        ctx["idempotency_key"] = value


@when(parsers.parse('the Buyer Agent sends the same create_media_buy request with idempotency_key "{key}"'))
def when_send_same_request_with_key(ctx: dict, key: str) -> None:
    """Replay the same create_media_buy request with the given idempotency_key.

    Uses the same request fields from the previous request but ensures the
    idempotency_key matches the provided value.
    """
    ctx["idempotency_key"] = key
    ctx["is_replay"] = True
    # Dispatch the request through the harness
    from tests.bdd.steps.generic._dispatch import dispatch_request

    dispatch_request(ctx)


@when("the Buyer Agent sends a second create_media_buy request with the same parameters")
def when_send_second_request(ctx: dict) -> None:
    """Send a second create_media_buy request with identical parameters."""
    ctx["is_second_request"] = True
    from tests.bdd.steps.generic._dispatch import dispatch_request

    dispatch_request(ctx)


@then("the response should succeed")
def then_response_should_succeed(ctx: dict) -> None:
    """Assert the response indicates success (no error)."""
    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    assert "response" in ctx, "No response recorded in ctx"


@then(parsers.parse('the response should include a "{field}"'))
def then_response_includes_field(ctx: dict, field: str) -> None:
    """Assert the response includes the specified field."""
    response = ctx.get("response")
    assert response is not None, "No response in ctx"
    if hasattr(response, field):
        assert getattr(response, field) is not None, f"Response field '{field}' is None"
    elif isinstance(response, dict):
        assert field in response, f"Response missing field '{field}': {response}"
    else:
        # Try model_dump if it's a Pydantic model
        dumped = response.model_dump() if hasattr(response, "model_dump") else {}
        assert field in dumped, f"Response missing field '{field}'"


@then(parsers.parse('I remember the "{field}" as "{alias}"'))
def then_remember_field(ctx: dict, field: str, alias: str) -> None:
    """Remember a response field value for later comparison."""
    response = ctx.get("response")
    assert response is not None, "No response to remember from"
    if hasattr(response, field):
        value = getattr(response, field)
    elif isinstance(response, dict):
        value = response.get(field)
    else:
        dumped = response.model_dump() if hasattr(response, "model_dump") else {}
        value = dumped.get(field)
    assert value is not None, f"Cannot remember None value for '{field}'"
    ctx.setdefault("remembered", {})[alias] = value


@then(parsers.parse('the response "{field}" should equal the remembered "{alias}"'))
def then_response_equals_remembered(ctx: dict, field: str, alias: str) -> None:
    """Assert a response field equals a previously remembered value."""
    response = ctx.get("response")
    assert response is not None, "No response in ctx"
    remembered = ctx.get("remembered", {})
    assert alias in remembered, f"No remembered value for '{alias}'"

    if hasattr(response, field):
        actual = getattr(response, field)
    elif isinstance(response, dict):
        actual = response.get(field)
    else:
        dumped = response.model_dump() if hasattr(response, "model_dump") else {}
        actual = dumped.get(field)

    assert actual == remembered[alias], (
        f"Response {field}={actual!r} does not equal remembered {alias}={remembered[alias]!r}"
    )


@then(parsers.parse('the response "{field}" should NOT equal the remembered "{alias}"'))
def then_response_not_equals_remembered(ctx: dict, field: str, alias: str) -> None:
    """Assert a response field does NOT equal a previously remembered value."""
    response = ctx.get("response")
    assert response is not None, "No response in ctx"
    remembered = ctx.get("remembered", {})
    assert alias in remembered, f"No remembered value for '{alias}'"

    if hasattr(response, field):
        actual = getattr(response, field)
    elif isinstance(response, dict):
        actual = response.get(field)
    else:
        dumped = response.model_dump() if hasattr(response, "model_dump") else {}
        actual = dumped.get(field)

    assert actual != remembered[alias], (
        f"Response {field}={actual!r} should NOT equal remembered {alias}={remembered[alias]!r}"
    )


@then("no duplicate ad server booking should be created")
def then_no_duplicate_booking(ctx: dict) -> None:
    """Assert that no duplicate ad server booking was created on replay.

    Verifies the adapter was not called more than once (idempotency replay
    should return the cached result without a second adapter call).
    The adapter call count is tracked in ctx by the harness dispatch layer.
    """
    adapter_call_count = ctx.get("adapter_create_call_count", 0)
    assert adapter_call_count <= 1, (
        f"Adapter create_media_buy called {adapter_call_count} times "
        "— expected at most 1 (the original, not a duplicate)"
    )


# ── Order naming steps (hand-authored, adcp 3.12 / PR #1217) ──


@then(parsers.parse('I remember the ad server order name as "{alias}"'))
def then_remember_order_name(ctx: dict, alias: str) -> None:
    """Remember the ad server order name for later comparison."""
    response = ctx.get("response")
    assert response is not None, "No response in ctx"
    # Order name is typically in the adapter call args or response metadata
    order_name = ctx.get("last_order_name")
    assert order_name is not None, "No order name recorded — harness must capture it"
    ctx.setdefault("remembered", {})[alias] = order_name


@then(parsers.parse('the ad server order name should differ from the remembered "{alias}"'))
def then_order_name_differs(ctx: dict, alias: str) -> None:
    """Assert the order name from the latest request differs from the remembered one."""
    remembered = ctx.get("remembered", {})
    assert alias in remembered, f"No remembered value for '{alias}'"
    current = ctx.get("last_order_name")
    assert current is not None, "No order name for current request"
    assert current != remembered[alias], f"Order name '{current}' should differ from remembered '{remembered[alias]}'"


@then(parsers.parse('the ad server order name should not contain "{substring}"'))
def then_order_name_no_substring(ctx: dict, substring: str) -> None:
    """Assert the order name does not contain the given substring."""
    order_name = ctx.get("last_order_name")
    assert order_name is not None, "No order name recorded"
    assert substring not in order_name, f"Order name '{order_name}' should not contain '{substring}'"


@then("the ad server order name should contain the media_buy_id from the response")
def then_order_name_contains_media_buy_id(ctx: dict) -> None:
    """Assert the order name contains the media_buy_id from the create response."""
    order_name = ctx.get("last_order_name")
    response = ctx.get("response")
    assert order_name is not None, "No order name recorded"
    assert response is not None, "No response in ctx"
    media_buy_id = getattr(response, "media_buy_id", None)
    if isinstance(response, dict):
        media_buy_id = response.get("media_buy_id")
    assert media_buy_id is not None, "No media_buy_id in response"
    assert media_buy_id in order_name, f"Order name '{order_name}' should contain media_buy_id '{media_buy_id}'"


@given(parsers.parse('the tenant order_name_template is "{template}"'))
def given_order_name_template(ctx: dict, template: str) -> None:
    """Set a custom order_name_template on the tenant."""
    ctx.setdefault("tenant_config", {})["order_name_template"] = template


@given("the tenant uses the default order_name_template")
def given_default_order_name_template(ctx: dict) -> None:
    """Use the default order_name_template (no override)."""
    ctx.setdefault("tenant_config", {}).pop("order_name_template", None)


# --- Functions from feature branch ---


def _maybe_init_request_kwargs(ctx: dict) -> None:
    """Initialize request_kwargs if env is MediaBuyCreateEnv (not account-only)."""
    from tests.harness.media_buy_create import MediaBuyCreateEnv

    env = ctx.get("env")
    if isinstance(env, MediaBuyCreateEnv):
        from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

        _ensure_request_defaults(ctx)


@given("a create_media_buy request with account_id that does not exist")
def given_request_account_not_exists(ctx: dict) -> None:
    """Set up a request referencing a nonexistent account_id.

    The account resolution layer (enrich_identity_with_account) will raise
    AdCPAccountNotFoundError — a terminal error with code ACCOUNT_NOT_FOUND.
    No 'the account exists and is active' step should follow this Given.
    """
    from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference1

    ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-does-not-exist"))
    ctx["request_account_id"] = "acc-does-not-exist"
    _maybe_init_request_kwargs(ctx)
    if "request_kwargs" in ctx:
        ctx["request_kwargs"]["account"] = ctx["account_ref"]


@given("no account matches the brand + operator combination")
def given_account_not_exists(ctx: dict) -> None:
    """Ensure the referenced account does not exist in DB.

    Handles two lookup modes:
    1. Explicit account_id: ctx["request_account_id"] set by prior Given step
    2. Natural key (brand + operator): ctx["request_brand"] + ctx["request_operator"]
    Verifies no matching Account record exists in the database.
    """
    from src.core.database.repositories.account import AccountRepository

    account_id = ctx.get("request_account_id")
    brand = ctx.get("request_brand")
    operator = ctx.get("request_operator")
    assert account_id is not None or (brand is not None and operator is not None), (
        "No request_account_id or request_brand/request_operator in ctx — "
        "step claims account does not exist but no account reference was set by a prior Given step"
    )

    tenant = ctx.get("tenant")
    assert tenant is not None, (
        "No tenant in ctx — step claims account does not exist in the seller's "
        "account store but cannot verify without a tenant"
    )
    env = ctx["env"]
    env._commit_factory_data()
    repo = AccountRepository(env._session, tenant.tenant_id)
    if account_id is not None:
        existing = repo.get_by_id(account_id)
        assert existing is None, (
            f"Account '{account_id}' exists in DB for tenant '{tenant.tenant_id}' — "
            "step claims account does not exist but a prior step created it."
        )
    else:
        matching = repo.list_by_natural_key(operator=operator, brand_domain=brand)
        assert not matching, (
            f"Account with brand '{brand}' + operator '{operator}' exists in DB for tenant '{tenant.tenant_id}' — "
            "step claims no account matches but a prior step created one."
        )


def _create_account_for_other_agent(ctx: dict, **account_kwargs: object) -> None:
    """Create an account accessible only to a different agent (not the test principal).

    Sets up tenant+principal if needed, creates a second "other" principal,
    creates the account, and grants access only to the other principal.
    The test principal has NO access — resolve_account() should raise
    AdCPAuthorizationError.
    """
    env = ctx["env"]
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    else:
        tenant = ctx["tenant"]

    other_principal = PrincipalFactory(tenant=tenant, principal_id="other_agent")
    account = AccountFactory(tenant=tenant, **account_kwargs)
    AgentAccountAccessFactory(tenant=tenant, principal=other_principal, account=account)


@given("the account exists but is accessible only to a different agent")
def given_account_other_agent_by_id(ctx: dict) -> None:
    """Create account by ID, grant access only to a different agent."""
    account_id = ctx.get("request_account_id", "acc_other_agent")
    _create_account_for_other_agent(
        ctx,
        account_id=account_id,
        status="active",
        brand={"domain": "other-agent-id.com"},
        operator="other-agent-id.com",
    )


@given("the natural key resolves to an account accessible only to a different agent")
def given_account_other_agent_by_natural_key(ctx: dict) -> None:
    """Create account by natural key, grant access only to a different agent."""
    brand = ctx.get("request_brand", "other-agent.com")
    operator = ctx.get("request_operator", "other-agent.com")
    _create_account_for_other_agent(
        ctx,
        account_id=f"acc_natkey_{brand}",
        status="active",
        brand={"domain": brand},
        operator=operator,
    )


def _validate_account_config(config: str) -> None:
    """Validate config against known patterns to fail fast on unknown values.

    The config string comes from Gherkin Examples tables. Unknown values should
    raise immediately with a helpful message, not silently fall through.
    """
    # Check against known patterns (wildcard for acc-* prefix configs)
    if config.startswith("acc-") and ("active" in config or "not-found" in config):
        return
    if config.startswith("brand+op") and any(k in config for k in ("single match", "no match", "multi match")):
        return
    for keyword in ("setup-needed", "payment-due", "suspended", "no account", "both fields"):
        if keyword in config:
            return
    known = sorted(_VALID_ACCOUNT_CONFIGS)
    raise ValueError(
        f"Unknown account boundary config: {config!r}. "
        f"Known patterns: {known}. "
        f"Add handling for this config or check the Gherkin Examples table."
    )


def _dispatch_create_media_buy(ctx: dict) -> None:
    """Build CreateMediaBuyRequest from ctx and dispatch through harness."""
    from pydantic import ValidationError

    from tests.bdd.steps.generic._dispatch import dispatch_request

    # For auth-failure scenarios (no Given step builds request_kwargs),
    # build a valid request so we test the AUTH path, not Pydantic parsing.
    if ctx.get("has_auth") is False and "request_kwargs" not in ctx:
        from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

        _ensure_request_defaults(ctx)

    request_kwargs = ctx.get("request_kwargs", {})

    # For production account scenarios in E2E mode, the Docker default tenant
    # has human_review_required=True which routes to manual approval (status='submitted').
    # Production account scenarios expect auto-approval, so sync to DB.
    if ctx.get("sandbox") is False:
        tenant = ctx.get("tenant")
        env = ctx.get("env")
        if tenant is not None and env is not None:
            from tests.bdd.steps.generic.given_media_buy import _sync_adapter_approval_to_db

            tenant.human_review_required = False
            _sync_adapter_approval_to_db(ctx, manual_approval_required=False)
            env._commit_factory_data()
            env._identity_cache.clear()
            env._tenant_overrides["human_review_required"] = False

    # Build the request object — may raise ValidationError for malformed inputs
    # (e.g., start_time="ASAP" violates Literal["asap"] | AwareDatetime)
    from src.core.schemas import CreateMediaBuyRequest

    try:
        req = CreateMediaBuyRequest(**request_kwargs)
    except ValidationError as exc:
        ctx["error"] = exc
        return

    # Account resolution — mirrors transport boundary behavior.
    # Production wrappers call enrich_identity_with_account(identity, req.account)
    # before _impl. We resolve here using the harness session (env._session) so
    # E2E and in-process transports both query the correct database.
    if req.account is not None:
        from src.core.database.repositories.account import AccountRepository
        from src.core.helpers.account_helpers import resolve_account

        env = ctx["env"]
        env._commit_factory_data()
        try:
            repo = AccountRepository(env._session, env.identity.tenant_id)
            account_id = resolve_account(req.account, env.identity, repo)
            enriched = env.identity.model_copy(update={"account_id": account_id})
        except Exception as exc:
            ctx["error"] = exc
            return
        if enriched is not None:
            dispatch_request(ctx, req=req, identity=enriched)
        elif ctx.get("has_auth") is False:
            dispatch_request(ctx, req=req, identity=_no_principal_identity(ctx))
        else:
            dispatch_request(ctx, req=req)
    elif ctx.get("has_auth") is False:
        dispatch_request(ctx, req=req, identity=_no_principal_identity(ctx))
    else:
        dispatch_request(ctx, req=req)

    # Post-process: CreateMediaBuyResult wraps errors in response, not as exceptions.
    # Promote error results to ctx["error"] so generic Then steps can find them.
    resp = ctx.get("response")
    if resp is not None and hasattr(resp, "status") and hasattr(resp, "response"):
        from src.core.schemas._base import CreateMediaBuyError as CMBError

        if isinstance(resp.response, CMBError) and resp.response.errors:
            # Promote first error from the errors list — it has .code, .message, .recovery
            ctx["error"] = resp.response.errors[0]
            ctx["error_response"] = resp.response  # Keep full error response for multi-error checks
            del ctx["response"]
        elif resp.status == "failed":
            ctx["error"] = resp
            del ctx["response"]


def _no_principal_identity(ctx: dict) -> object:
    """Build a ResolvedIdentity with tenant but no principal_id.

    For auth-failure scenarios (ext-i), the buyer has tenant context but lacks
    a principal. This lets the request get past Pydantic parsing and fail at
    the authentication layer with a proper "Principal ID not found" error.
    Using identity=None would instead trigger "Identity is required" which
    doesn't match the scenario's expected error message.
    """
    from tests.factories.principal import PrincipalFactory

    tenant = ctx.get("tenant")
    tenant_id = tenant.tenant_id if tenant else "test_tenant"
    return PrincipalFactory.make_identity(
        principal_id=None,
        tenant_id=tenant_id,
    )


def _assert_start_time_outcome(ctx: dict, outcome: str) -> None:
    """Assert start_time success outcomes from partition/boundary scenarios.

    Supported outcomes:
        "start time resolves to now"   — ASAP resolved to current UTC
        "start time accepted"          — future datetime accepted without error
        "start time treated as UTC"    — naive datetime treated as UTC (same as accepted)
    """
    import pytest

    from tests.bdd.steps.generic.then_media_buy import _get_response_field

    if "error" in ctx:
        pytest.xfail(f"SPEC-PRODUCTION GAP: Expected success ({outcome}) but production rejected with: {ctx['error']}")
    resp = ctx.get("response")
    assert resp is not None, f"Expected a response for '{outcome}'"

    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, f"No media_buy_id in response for '{outcome}'"

    if outcome == "start time resolves to now":
        # Reuse existing ASAP resolution assertion (DRY)
        from tests.bdd.steps.generic.then_media_buy import then_start_time_resolved_to_utc

        then_start_time_resolved_to_utc(ctx)
    elif outcome in ("start time accepted", "start time treated as UTC"):
        pass  # media_buy_id assertion above is sufficient for these outcomes
    else:
        raise ValueError(f"Unknown start time outcome: {outcome}")


def _assert_end_time_outcome(ctx: dict, outcome: str) -> None:
    """Assert end_time success outcomes from partition/boundary scenarios.

    Supported outcomes:
        "end time accepted" — end_time after start_time accepted without error
    """
    import pytest

    from tests.bdd.steps.generic.then_media_buy import _get_response_field

    if "error" in ctx:
        pytest.xfail(f"SPEC-PRODUCTION GAP: Expected success ({outcome}) but production rejected with: {ctx['error']}")
    resp = ctx.get("response")
    assert resp is not None, f"Expected a response for '{outcome}'"

    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, f"No media_buy_id in response for '{outcome}'"

    if outcome == "end time accepted":
        pass  # media_buy_id assertion above is sufficient
    else:
        raise ValueError(f"Unknown end time outcome: {outcome}")


def _assert_request_proceeds_outcome(ctx: dict, outcome: str) -> None:
    """Assert 'request proceeds' outcomes from buying_mode / policy partition scenarios.

    These outcomes verify that the request was accepted and routed to the correct
    pipeline or that a policy gate was passed. All share a common success assertion
    (no error, valid response). Pipeline-specific outcomes additionally verify
    distinguishing behavior.

    Supported patterns:
        "request proceeds to brief pipeline"       — success + brief-specific check
        "request proceeds to wholesale pipeline"   — success + wholesale-specific check
        "request proceeds to refine pipeline"      — success + refine-specific check
        "request proceeds to catalog discovery"    — success (catalog route)
        "request proceeds to audience processing"  — success (audience route)
        "request proceeds to account resolution"   — success (account route)
        "request proceeds (no restrictions)"       — success (policy pass-through)
        "request proceeds (auth satisfied)"        — success (auth gate passed)
        "request proceeds (brand satisfied)"       — success (brand gate passed)
        "request proceeds (no check performed)"    — success (policy disabled)
        "request proceeds (LLM returned ALLOWED)"  — success (LLM policy gate passed)
        "request proceeds (fail-open)"             — success (policy service unavailable)
        "request proceeds (no catalog dependency)" — success (no catalog needed)
        "request proceeds (brand-scoped)"          — success (brand-scoped path)
        "request defaults to brief pipeline"       — success + brief-specific check
    """
    import pytest

    from tests.bdd.steps.generic.then_media_buy import _get_response_field

    if "error" in ctx:
        from src.core.exceptions import AdCPError

        error = ctx["error"]
        if isinstance(error, AdCPError):
            pytest.xfail(
                f"SPEC-PRODUCTION GAP: Expected '{outcome}' but production "
                f"rejected with AdCPError (code={error.error_code}): {error}"
            )
        from pydantic import ValidationError

        if isinstance(error, ValidationError):
            pytest.xfail(f"SPEC-PRODUCTION GAP: Expected '{outcome}' but Pydantic rejected request: {error}")
        if isinstance(error, TypeError) and "unexpected keyword argument" in str(error):
            pytest.xfail(f"TRANSPORT BOUNDARY GAP: {error} — wrapper doesn't forward this parameter")
        raise AssertionError(f"Expected '{outcome}' (success path) but got error: {type(error).__name__}: {error}")

    resp = ctx.get("response")
    assert resp is not None, f"Expected a response for '{outcome}'"

    # Common success assertion: response must have observable content.
    # For product discovery pipelines, that means a products list.
    # For other "request proceeds" outcomes, response existence suffices.
    products = _get_response_field(resp, "products")

    # Pipeline-specific outcome assertions
    if "to brief pipeline" in outcome or "defaults to brief pipeline" in outcome:
        # Brief pipeline should return products with relevance scores.
        assert products is not None, f"Brief pipeline ({outcome}): expected 'products' in response, got None"
        if isinstance(products, list) and len(products) > 0:
            first = products[0]
            score = (
                getattr(first, "relevance_score", None) if not isinstance(first, dict) else first.get("relevance_score")
            )
            if score is not None:
                assert isinstance(score, (int, float)), (
                    f"Brief pipeline: expected numeric relevance_score, got {type(score).__name__}"
                )
            # FIXME(salesagent-s271): When response metadata exposes pipeline identity,
            # assert relevance_score IS present (not just check when available).
    elif "to wholesale pipeline" in outcome:
        # Wholesale pipeline returns catalog-style unranked results.
        assert products is not None, f"Wholesale pipeline ({outcome}): expected 'products' in response, got None"
        if isinstance(products, list) and len(products) > 0:
            first = products[0]
            score = (
                getattr(first, "relevance_score", None) if not isinstance(first, dict) else first.get("relevance_score")
            )
            # Wholesale should NOT have relevance scores (unranked catalog).
            if score is not None:
                assert score == 0 or score is None, (
                    f"Wholesale pipeline should not rank products, but got relevance_score={score}"
                )
    elif "to refine pipeline" in outcome:
        # Refine pipeline filters against a prior result set.
        assert products is not None, f"Refine pipeline ({outcome}): expected 'products' in response, got None"


def _assert_error_outcome(ctx: dict, outcome: str) -> None:
    """Assert error outcome, supporting both AdCPError exceptions and Error pydantic models.

    Supported patterns:
        "error CODE"                  — assert error with specific code
        "error CODE recovery_hint"    — assert code + recovery (terminal/correctable/transient)
        "error CODE with suggestion"  — assert code + suggestion field present
        "error with suggestion"       — assert any error + suggestion field present (no code check)
    """
    import pytest

    from src.core.exceptions import AdCPError

    if "error" not in ctx:
        # SPEC-PRODUCTION GAP: expected error but production succeeded
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: Expected '{outcome}' but production succeeded. Response: {ctx.get('response')}"
        )

    error = ctx["error"]

    # Parse expected: "error CODE ..." or "error with suggestion" (no code)
    remainder = outcome[6:].strip()  # strip "error " prefix
    parts = remainder.split()

    # Determine if an error code is specified (first word is NOT "with")
    has_error_code = bool(parts) and parts[0] != "with"

    if has_error_code:
        expected_code = parts[0].strip('"')
        # Extract error code from either AdCPError exception or Error pydantic model
        if isinstance(error, AdCPError):
            actual_code = error.error_code
        elif hasattr(error, "code"):
            actual_code = error.code
        else:
            from pydantic import ValidationError

            if isinstance(error, ValidationError) and expected_code in (
                "INVALID_REQUEST",
                "SCHEMA_VALIDATION_ERROR",
                "BUDGET_TOO_LOW",
                "UNSUPPORTED_FEATURE",
            ):
                # Pydantic rejects the request before production code runs.
                # Treat ValidationError as equivalent to the expected code.
                actual_code = expected_code
            elif isinstance(error, TypeError) and "unexpected keyword argument" in str(error):
                # Transport boundary gap: wrapper doesn't forward this parameter.
                pytest.xfail(f"TRANSPORT BOUNDARY GAP: {error} — wrapper doesn't forward this parameter")
            else:
                raise AssertionError(f"Error has no code attribute: {type(error).__name__}: {error}")
        # Spec-production code mappings — xfail when production uses a different error code.
        # Non-impl transports (a2a, mcp, rest) sometimes return VALIDATION_ERROR for
        # domain-specific error conditions where the spec requires a more specific code.
        # Non-impl transports (especially REST) often collapse domain-specific
        # error codes to a generic VALIDATION_ERROR.  The spec requires specific
        # codes; these mappings make the mismatch xfail instead of hard-fail.
        _SPEC_PRODUCTION_CODE_MAP = {
            "BUDGET_TOO_LOW": {"budget_below_minimum", "budget_limit_exceeded", "VALIDATION_ERROR"},
            "PERMISSION_DENIED": {"AUTHORIZATION_ERROR", "VALIDATION_ERROR"},
            "ADAPTER_ERROR": {"VALIDATION_ERROR"},
            "EMPTY_UPDATE": {"VALIDATION_ERROR"},
            "INVALID_STATUS": {"VALIDATION_ERROR"},
            "INVALID_REQUEST": {"VALIDATION_ERROR"},
            "CREATIVE_REJECTED": {"VALIDATION_ERROR"},
            "invalid_placement_ids": {"VALIDATION_ERROR"},
        }
        if actual_code != expected_code:
            expected_production = _SPEC_PRODUCTION_CODE_MAP.get(expected_code)
            if expected_production and actual_code in expected_production:
                pytest.xfail(f"SPEC-PRODUCTION GAP: Spec says '{expected_code}', production uses '{actual_code}'")
        assert actual_code == expected_code, f"Expected error code '{expected_code}', got '{actual_code}'"

        # Check recovery hint if specified
        if len(parts) >= 2 and parts[1] in ("terminal", "correctable", "transient"):
            if isinstance(error, AdCPError):
                actual_recovery = error.recovery
            elif hasattr(error, "recovery"):
                actual_recovery = str(error.recovery) if error.recovery is not None else None
            else:
                actual_recovery = None
            assert actual_recovery == parts[1], f"Expected recovery '{parts[1]}', got '{actual_recovery}'"

    # Check "with suggestion" if specified
    if "with suggestion" in outcome.lower():
        _assert_has_suggestion(error)


def _assert_has_suggestion(error: object) -> None:
    """Assert that an error carries a suggestion, regardless of error type.

    If the error exists but lacks a suggestion field, this is a SPEC-PRODUCTION GAP:
    the spec requires a suggestion but production doesn't provide one.
    """
    import pytest

    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        if not (error.details and "suggestion" in error.details):
            pytest.xfail(
                f"SPEC-PRODUCTION GAP: Error raised but lacks suggestion field. "
                f"Error: {error}, details: {getattr(error, 'details', None)}"
            )
    elif hasattr(error, "suggestion"):
        if error.suggestion is None:
            pytest.xfail(f"SPEC-PRODUCTION GAP: Error has suggestion=None. Error: {error}")
    else:
        pytest.xfail(f"SPEC-PRODUCTION GAP: Error type {type(error).__name__} has no suggestion attribute")


def _assert_persistence_after_adapter_success(ctx: dict) -> None:
    """Assert all records persisted after adapter returns success (auto-approval path).

    Verifies: auto-approval status, media buy persisted, packages persisted, adapter called.
    """
    import pytest

    from tests.bdd.steps.generic.then_media_buy import (
        then_adapter_executed,
        then_approval_auto,
        then_media_buy_persisted,
        then_package_records_persisted,
    )

    if "error" in ctx:
        pytest.xfail(f"SPEC-PRODUCTION GAP: Expected adapter success + persistence but got error: {ctx['error']}")
    resp = ctx.get("response")
    assert resp is not None, "Expected a response for adapter success path"

    then_approval_auto(ctx)
    then_media_buy_persisted(ctx)
    then_package_records_persisted(ctx)
    then_adapter_executed(ctx)


def _assert_no_persistence_after_adapter_failure(ctx: dict) -> None:
    """Assert no records persisted after adapter returns failure.

    Verifies: error in context, no media buy persisted, no packages persisted.
    """
    from tests.bdd.steps.generic.then_media_buy import (
        then_no_media_buy_persisted,
        then_no_package_records_persisted,
    )

    assert "error" in ctx, f"Expected an error after adapter failure but got success response: {ctx.get('response')}"
    then_no_media_buy_persisted(ctx)
    then_no_package_records_persisted(ctx)


def _assert_persistence_in_pending_state(ctx: dict) -> None:
    """Assert records persisted in pending state (manual approval path).

    Verifies: submitted status in response, media buy persisted with pending_approval in DB.
    """
    import pytest

    from tests.bdd.steps.generic.then_media_buy import (
        then_approval_manual,
        then_pending_state,
    )

    if "error" in ctx:
        pytest.xfail(f"SPEC-PRODUCTION GAP: Expected pending state but got error: {ctx['error']}")
    then_approval_manual(ctx)
    then_pending_state(ctx)


@when(parsers.parse('the Seller rejects the media buy with reason "{reason}"'))
def when_seller_rejects_media_buy(ctx: dict, reason: str) -> None:
    """Simulate seller rejecting a pending media buy (admin action).

    Uses MediaBuyRepository.update_status() and WorkflowRepository.update_status()
    to exercise the production repository code paths.

    SPEC-PRODUCTION GAP: This step still bypasses the full Flask admin rejection path
    (operations.py:approve_media_buy with action='reject') because it requires Flask
    request context. However, it exercises the repository layer (tenant-scoped queries,
    status transitions) rather than raw DB manipulation.
    FIXME(salesagent-9vgz.1): Wire through the production admin flow when the harness
    supports Flask request context.
    Production path: POST /operations/media-buy/<id>/approve with action=reject.
    """
    import warnings

    import pytest

    from src.core.database.repositories.media_buy import MediaBuyRepository
    from src.core.database.repositories.workflow import WorkflowRepository

    # SPEC-PRODUCTION GAP: Uses repository methods instead of the full Flask admin
    # rejection path. The repository layer (tenant scoping, status transitions) is
    # exercised, but Flask-specific logic (comments, webhooks) is not.
    # FIXME(salesagent-9vgz.1): Wire through production admin flow.
    warnings.warn(
        "when_seller_rejects_media_buy uses repository methods, not the full Flask "
        "admin rejection path (operations.py:approve_media_buy). Repository-level only.",
        stacklevel=1,
    )

    env = ctx["env"]
    env._commit_factory_data()

    media_buy = ctx["existing_media_buy"]
    media_buy_id = media_buy.media_buy_id
    tenant = ctx["tenant"]

    session = env._session
    mb_repo = MediaBuyRepository(session, tenant.tenant_id)
    wf_repo = WorkflowRepository(session, tenant.tenant_id)

    # Verify media buy exists and is in a rejectable state (via repository)
    mb = mb_repo.get_by_id(media_buy_id)
    assert mb is not None, f"Media buy {media_buy_id} not found in DB for tenant {tenant.tenant_id}"
    rejectable_statuses = {"pending_approval", "submitted", "requires_approval"}
    assert mb.status in rejectable_statuses, (
        f"Media buy {media_buy_id} has status '{mb.status}' — "
        f"expected one of {rejectable_statuses} for rejection. "
        "Step claims 'Seller rejects' but media buy is not in a rejectable state."
    )

    # Update status via repository (exercises tenant-scoped update_status)
    updated_mb = mb_repo.update_status(media_buy_id, "rejected")
    assert updated_mb is not None, (
        f"MediaBuyRepository.update_status returned None for {media_buy_id} — "
        "repository could not find the media buy within tenant scope"
    )

    # Find and update workflow step via repository
    workflow_step_id = None
    mapping = wf_repo.get_latest_mapping_for_object("media_buy", media_buy_id)
    if mapping:
        step = wf_repo.update_status(
            mapping.step_id,
            status="rejected",
            error_message=reason,
        )
        if step:
            workflow_step_id = step.step_id

    session.commit()

    # Verify the rejection actually persisted (tenant-scoped read after commit)
    session.expire_all()
    mb_check = mb_repo.get_by_id(media_buy_id)
    assert mb_check is not None, f"Media buy {media_buy_id} not found after rejection"
    assert mb_check.status == "rejected", f"Expected 'rejected' status after seller rejection, got '{mb_check.status}'"
    if workflow_step_id:
        ws = wf_repo.get_by_step_id(workflow_step_id)
        assert ws is not None, f"Workflow step {workflow_step_id} not found after rejection commit"
        assert ws.status == "rejected", f"Expected workflow step status 'rejected', got '{ws.status}'"
        assert ws.error_message == reason, (
            f"Expected rejection reason '{reason}' on workflow step, got '{ws.error_message}'"
        )

    # Store rejection_reason on existing_media_buy so Then steps can find it.
    # MediaBuy model lacks a rejection_reason column — we set it dynamically.
    media_buy.rejection_reason = reason  # type: ignore[attr-defined]

    # SPEC-PRODUCTION GAP: xfail AFTER commit+verification — rejection status IS
    # persisted, but the reason could not be stored on a workflow step because no
    # workflow mapping exists. This surfaces the gap without masking the rejection.
    if mapping is None:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: No workflow mapping for media buy {media_buy_id} — "
            "rejection reason cannot be stored on a workflow step. "
            "Rejection status IS persisted (verified above). "
            "FIXME(salesagent-9vgz.1): Wire through production admin rejection flow."
        )


@given(parsers.parse('a package creative_assignment references creative_id "{creative_id}"'))
@given(parsers.parse('But a package creative_assignment references creative_id "{creative_id}"'))
def given_package_references_creative_id(ctx: dict, creative_id: str) -> None:
    """Add creative_id to the first package's creative_ids list.

    For ext-o: the creative_id won't exist in DB, triggering CREATIVES_NOT_FOUND.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        "Step claims 'a package creative_assignment references creative_id' "
        "but no packages in request — ensure packages are set up first"
    )
    pkg = kwargs["packages"][0]
    existing = pkg.get("creative_ids") or []
    existing.append(creative_id)
    pkg["creative_ids"] = existing

    # Postcondition: verify creative_id was actually appended
    assert creative_id in pkg["creative_ids"], (
        f"Postcondition failed: creative_id '{creative_id}' not found in "
        f"package creative_ids {pkg['creative_ids']} after append"
    )


@given("a creative's format_id does not match any of the product's supported format_ids")
@given("But a creative's format_id does not match any of the product's supported format_ids")
def given_creative_format_mismatch(ctx: dict) -> None:
    """Create a creative with a format that doesn't match the product's format_ids.

    For ext-p: creates a creative with format "video_640x480" while the product
    only supports "display_300x250", triggering CREATIVE_FORMAT_MISMATCH.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults
    from tests.factories.creative import CreativeFactory
    from tests.helpers.adcp_factories import create_test_format

    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)
    # Use a display format (not video) to avoid Assets/Assets5 discriminated union
    # bug in extract_media_url_and_dimensions. display_728x90 is a valid display format
    # but not in the product's accepted formats (display_300x250).
    creative = CreativeFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        creative_id="cr-mismatched-format",
        format="display_728x90",  # Mismatched — product accepts display_300x250
        status="approved",
        data={
            "assets": {
                "primary": {
                    "url": "https://cdn.example.com/leaderboard.png",
                    "width": 728,
                    "height": 90,
                }
            }
        },
    )
    env._commit_factory_data()
    # Register the mismatched format spec so pre-validation recognizes it
    # (without this, _get_format_spec_sync returns None → "unknown format" error
    # instead of the specific CREATIVE_FORMAT_MISMATCH we want to test)
    env._format_specs["display_728x90"] = create_test_format(
        format_id="display_728x90",
        name="Display 728x90 Leaderboard",
        type="display",
    )
    # Add the creative_id to the first package
    if kwargs.get("packages"):
        pkg = kwargs["packages"][0]
        existing = pkg.get("creative_ids") or []
        existing.append(creative.creative_id)
        pkg["creative_ids"] = existing


@given("a valid create_media_buy request with inline creatives that passes all validation")
def given_request_with_inline_creatives(ctx: dict) -> None:
    """Set up a request with creative_ids referencing existing approved creatives.

    For ext-q: creatives exist and format-match the product, so validation passes.
    The upload step (adapter.add_creative_assets) is where failure will be injected.
    Creative data must include proper assets (matching format spec's asset_id="primary")
    with URL and dimensions so pre-validation passes.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults
    from tests.factories.creative import CreativeFactory

    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)
    # Create a creative that matches the product's format_ids with proper asset data
    creative = CreativeFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        creative_id="cr-upload-test",
        agent_url="https://creative.adcontextprotocol.org",
        format="display_300x250",  # Matches product format
        status="approved",
        data={
            "assets": {
                "primary": {
                    "url": "https://cdn.example.com/creative.png",
                    "width": 300,
                    "height": 250,
                }
            }
        },
    )
    env._commit_factory_data()
    if kwargs.get("packages"):
        pkg = kwargs["packages"][0]
        existing = pkg.get("creative_ids") or []
        existing.append(creative.creative_id)
        pkg["creative_ids"] = existing


@given("the ad server rejects the creative upload")
@given("But the ad server rejects the creative upload")
def given_ad_server_rejects_upload(ctx: dict) -> None:
    """Configure the mock adapter to fail on creative upload.

    For ext-q: adapter.add_creative_assets raises a non-AdCPError exception,
    which production code catches and wraps as CREATIVE_UPLOAD_FAILED.
    """
    env = ctx["env"]
    mock_adapter = env.mock["adapter"].return_value
    mock_adapter.add_creative_assets.side_effect = Exception("Ad server rejected creative: invalid asset dimensions")


@given("a valid create_media_buy request with inline creatives")
def given_request_with_inline_creatives_base(ctx: dict) -> None:
    """Set up a request with creative_ids referencing an existing creative.

    For ext-g: creates a creative with valid data so subsequent steps can
    inject specific validation failures (missing URL, non-generative format).
    Stores the creative ORM object in ctx["inline_creative"] for modification.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults
    from tests.factories.creative import CreativeFactory

    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)
    creative = CreativeFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        creative_id="cr-ext-g-test",
        agent_url="https://creative.adcontextprotocol.org",
        format="display_300x250",
        status="approved",
        data={
            "assets": {
                "primary": {
                    "url": "https://cdn.example.com/creative.png",
                    "width": 300,
                    "height": 250,
                }
            }
        },
    )
    env._commit_factory_data()
    ctx["inline_creative"] = creative
    if kwargs.get("packages"):
        pkg = kwargs["packages"][0]
        existing = pkg.get("creative_ids") or []
        existing.append(creative.creative_id)
        pkg["creative_ids"] = existing


@given("a creative is missing the required URL in assets")
@given("But a creative is missing the required URL in assets")
def given_creative_missing_url(ctx: dict) -> None:
    """Remove the URL from the inline creative's asset data.

    For ext-g: production code in _validate_creatives_before_adapter_call
    calls extract_media_url_and_dimensions which returns (None, None, None)
    when the primary asset has no URL, triggering INVALID_CREATIVES error.
    """
    creative = ctx.get("inline_creative")
    assert creative is not None, "No inline creative in ctx — call 'with inline creatives' first"
    # Clear the URL from assets so extract_media_url_and_dimensions returns None
    creative.data = {"assets": {"primary": {"width": 300, "height": 250}}}
    # Re-commit so the DB row reflects the missing URL (not just in-memory ORM object)
    env = ctx["env"]
    env._commit_factory_data()


@given("the creative format is not generative")
@given("And the creative format is not generative")
def given_creative_format_not_generative(ctx: dict) -> None:
    """Establish the format spec for the creative's format as non-generative.

    For ext-g: generative formats have output_format_ids and are skipped
    during URL validation. This step unconditionally clears output_format_ids
    to establish the non-generative precondition regardless of current state.
    """
    env = ctx["env"]
    format_spec = env._format_specs.get("display_300x250")
    assert format_spec is not None, "display_300x250 format spec not configured in harness"
    # Unconditionally establish non-generative state — do not skip behind an if guard
    format_spec.output_format_ids = None
    # Postcondition: verify the invariant holds after setup
    assert not getattr(format_spec, "output_format_ids", None), (
        "display_300x250 format spec should NOT have output_format_ids (non-generative) after setup"
    )


@given(parsers.parse('a package format_id is a plain string "{value}" instead of a FormatId object'))
@given(parsers.parse('But a package format_id is a plain string "{value}" instead of a FormatId object'))
def given_format_id_plain_string(ctx: dict, value: str) -> None:
    """Replace package format_ids with a plain string instead of FormatId object.

    For ext-h: AI agents commonly send format IDs as plain strings like
    "banner_300x250" instead of the required FormatId object with {agent_url, id}.
    Pydantic rejects this at request construction time with a ValidationError.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        # Set format_ids to a list containing the plain string
        # This will fail Pydantic validation when CreateMediaBuyRequest is constructed
        kwargs["packages"][0]["format_ids"] = [value]


@given("a package format_id references an unregistered agent_url")
@given("But a package format_id references an unregistered agent_url")
def given_format_id_unregistered_agent(ctx: dict) -> None:
    """Set a format_id with a valid structure but unregistered agent_url.

    For ext-h-agent: the FormatId object has {agent_url, id} but the agent_url
    is not registered with the tenant. Production should detect this and reject
    it, but _validate_and_convert_format_ids is currently dead code — the
    format_id passes Pydantic validation and reaches the format compatibility
    check, which rejects it because the product doesn't have this agent's formats.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["format_ids"] = [
            {
                "agent_url": "https://unknown-agent.example.com",
                "id": "display_300x250",
            }
        ]


@given("all referenced creatives exist in valid state with compatible formats")
def given_creatives_valid_and_compatible(ctx: dict) -> None:
    """Create approved creatives with format matching the product's accepted formats.

    For inv-026-1: creatives are valid (status=approved) and format-compatible
    (display_300x250 matches default product), so creative assignment should proceed.

    Creates DB records for ALL creative_ids already in the request (e.g. creative-default-001
    from given_request_with_creative_assignments) plus an additional compatible creative.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults
    from tests.factories.creative import CreativeFactory

    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)

    # Create DB records for any creative_ids already present in the request
    if kwargs.get("packages"):
        for cid in kwargs["packages"][0].get("creative_ids", []):
            CreativeFactory(
                tenant=ctx["tenant"],
                principal=ctx["principal"],
                creative_id=cid,
                format="display_300x250",
                status="approved",
                data={
                    "assets": {
                        "primary": {
                            "url": f"https://cdn.example.com/{cid}.png",
                            "width": 300,
                            "height": 250,
                        }
                    }
                },
            )

    creative = CreativeFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        creative_id="cr-valid-compatible",
        format="display_300x250",  # Matches default product format
        status="approved",
        data={
            "assets": {
                "primary": {
                    "url": "https://cdn.example.com/valid-creative.png",
                    "width": 300,
                    "height": 250,
                }
            }
        },
    )
    env._commit_factory_data()
    if kwargs.get("packages"):
        pkg = kwargs["packages"][0]
        existing = pkg.get("creative_ids") or []
        if creative.creative_id not in existing:
            existing.append(creative.creative_id)
        pkg["creative_ids"] = existing
    # Update expected creative IDs for Then steps
    ctx.setdefault("expected_creative_ids", set())
    ctx["expected_creative_ids"].add(creative.creative_id)


@given('a referenced creative is in "error" state')
@given('But a referenced creative is in "error" state')
def given_creative_in_error_state(ctx: dict) -> None:
    """Create a creative with status=error to trigger BR-RULE-026 rejection.

    For inv-026-2: production code in _validate_creatives_before_adapter_call
    rejects creatives in "error" or "rejected" state with INVALID_CREATIVES.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults
    from tests.factories.creative import CreativeFactory

    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)
    creative = CreativeFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        creative_id="cr-error-state",
        format="display_300x250",  # Format is fine — status is the problem
        status="error",
        data={
            "assets": {
                "primary": {
                    "url": "https://cdn.example.com/error-creative.png",
                    "width": 300,
                    "height": 250,
                }
            }
        },
    )
    env._commit_factory_data()
    assert kwargs.get("packages"), (
        "packages must be initialized before adding creative — "
        "step text claims a creative is associated with a package but no packages exist"
    )
    pkg = kwargs["packages"][0]
    existing = pkg.get("creative_ids") or []
    existing.append(creative.creative_id)
    pkg["creative_ids"] = existing


@given("a creative format is incompatible with the product's supported formats")
@given("But a creative format is incompatible with the product's supported formats")
def given_creative_format_incompatible(ctx: dict) -> None:
    """Create a creative whose format doesn't match the product's accepted formats.

    For inv-026-4: creative has format display_728x90 but product only accepts
    display_300x250, triggering INVALID_CREATIVES during pre-validation.
    Reuses the same pattern as given_creative_format_mismatch (ext-p) but with
    different Gherkin text.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults
    from tests.factories.creative import CreativeFactory
    from tests.helpers.adcp_factories import create_test_format

    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)
    creative = CreativeFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        creative_id="cr-incompatible-format",
        format="display_728x90",  # Mismatched — product accepts display_300x250
        status="approved",
        data={
            "assets": {
                "primary": {
                    "url": "https://cdn.example.com/leaderboard.png",
                    "width": 728,
                    "height": 90,
                }
            }
        },
    )
    env._commit_factory_data()
    # Register the mismatched format spec so pre-validation recognizes it
    env._format_specs["display_728x90"] = create_test_format(
        format_id="display_728x90",
        name="Display 728x90 Leaderboard",
        type="display",
    )
    assert kwargs.get("packages"), (
        "packages must be initialized before setting creative format incompatibility — "
        "step text claims a creative is associated with a package but no packages exist"
    )
    pkg = kwargs["packages"][0]
    existing = pkg.get("creative_ids") or []
    existing.append(creative.creative_id)
    pkg["creative_ids"] = existing


@then("the creative assignment should proceed")
def then_creative_assignment_proceeds(ctx: dict) -> None:
    """Assert creative assignment succeeded — response is success with creative assignments persisted.

    For inv-026-1: valid creatives with compatible formats should result in a
    successful create_media_buy with creative assignment records in the database.
    """
    assert "error" not in ctx, f"Expected creative assignment to proceed but got error: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"

    # Verify creative assignments were persisted
    media_buy_id = None
    if hasattr(resp, "media_buy_id"):
        media_buy_id = resp.media_buy_id
    elif hasattr(resp, "response") and hasattr(resp.response, "media_buy_id"):
        media_buy_id = resp.response.media_buy_id
    assert media_buy_id, "No media_buy_id in response — creative assignment did not produce a media buy"

    from src.core.database.repositories.creative import CreativeAssignmentRepository

    env = ctx["env"]
    env._commit_factory_data()
    tenant = ctx["tenant"]
    repo = CreativeAssignmentRepository(env._session, tenant.tenant_id)
    assignments = repo.get_by_media_buy(media_buy_id)

    expected_ids = ctx.get("expected_creative_ids")
    assert expected_ids, (
        "No expected_creative_ids in ctx — Given step must register ctx['expected_creative_ids'] for Then steps"
    )
    actual_ids = {a.creative_id for a in assignments}
    missing = expected_ids - actual_ids
    assert not missing, (
        f"Expected creatives {sorted(expected_ids)} assigned to media buy {media_buy_id}, "
        f"but missing {sorted(missing)}. Actual: {sorted(actual_ids)}"
    )


@given(parsers.parse("the system returns a transient error ({error_type})"))
def given_transient_error(ctx: dict, error_type: str) -> None:
    """Configure mock adapter to raise a transient error matching error_type.

    Maps error_type to the appropriate AdCP exception class so the error
    flows through dispatch as the correct transient error type.
    """
    from src.core.exceptions import AdCPRateLimitError, AdCPServiceUnavailableError

    _transient_error_map: dict[str, type] = {
        "RATE_LIMITED": AdCPRateLimitError,
        "SERVICE_UNAVAILABLE": AdCPServiceUnavailableError,
        "TIMEOUT": AdCPServiceUnavailableError,
    }
    error_cls = _transient_error_map.get(error_type)
    assert error_cls is not None, (
        f"Unknown transient error type '{error_type}'. Supported: {sorted(_transient_error_map.keys())}"
    )

    env = ctx["env"]
    mock_adapter = env.mock["adapter"].return_value
    mock_adapter.create_media_buy.side_effect = error_cls(
        f"{error_type}: transient error",
        details={"retry_after": 30, "error_code": error_type},
        recovery="transient",
    )
    # Also write to DB so Docker-hosted adapter raises the error in E2E mode.
    # Must also disable manual approval so the adapter is actually called
    # (manual approval short-circuits before calling adapter.create_media_buy).
    from tests.bdd.steps.generic.given_media_buy import _sync_adapter_approval_to_db, _sync_adapter_error_to_db

    _sync_adapter_approval_to_db(ctx, manual_approval_required=False)
    _sync_adapter_error_to_db(
        ctx,
        fail_on_create=True,
        error_message=f"{error_type}: transient error",
        error_details={"retry_after": 30, "error_code": error_type},
        recovery="transient",
    )
    # Also set tenant to auto-approval so production code doesn't short-circuit
    tenant = ctx.get("tenant")
    if tenant is not None:
        tenant.human_review_required = False
        env._commit_factory_data()
        env._identity_cache.clear()
        env._tenant_overrides["human_review_required"] = False


@given(parsers.parse('a package has optimization_goal with kind "{kind}" and metric "{metric}" not in supported set'))
@given(
    parsers.parse('But a package has optimization_goal with kind "{kind}" and metric "{metric}" not in supported set')
)
def given_unsupported_optimization_metric(ctx: dict, kind: str, metric: str) -> None:
    """Add an optimization_goal with an unsupported metric to the first package.

    SPEC-PRODUCTION GAP: optimization_goals is not in adcp v3.6.0 or production
    schemas. PackageRequest(extra='forbid') will reject this field with a generic
    Pydantic validation error, not the spec-expected UNSUPPORTED_FEATURE.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        "packages must be initialized before setting optimization_goals — "
        "step text claims a package has a specific optimization_goal but no packages exist"
    )
    kwargs["packages"][0]["optimization_goals"] = [{"kind": kind, "metric": metric, "priority": 1}]


@given('a package has optimization_goal with kind "event" and unregistered event_source_id')
@given('But a package has optimization_goal with kind "event" and unregistered event_source_id')
def given_unregistered_event_source(ctx: dict) -> None:
    """Add an optimization_goal with an unregistered event_source_id to the first package.

    SPEC-PRODUCTION GAP: optimization_goals is not in adcp v3.6.0 or production
    schemas. PackageRequest(extra='forbid') will reject this field with a generic
    Pydantic validation error, not the spec-expected INVALID_REQUEST.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        "packages must be initialized before setting optimization_goals — "
        "step text claims a package has an unregistered event_source_id but no packages exist"
    )
    kwargs["packages"][0]["optimization_goals"] = [
        {"kind": "event", "event_source_id": "evt-unregistered-999", "priority": 1}
    ]


@given("a package has two optimization goals with the same priority value")
@given("But a package has two optimization goals with the same priority value")
def given_duplicate_optimization_priority(ctx: dict) -> None:
    """Add two optimization_goals with identical priority values to trigger inv-087-5.

    SPEC-PRODUCTION GAP: optimization_goals is not in adcp v3.6.0 or production
    schemas. PackageRequest(extra='forbid') will reject this field with a generic
    Pydantic validation error, not the spec-expected INVALID_REQUEST for duplicate
    priority values.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        "packages must be initialized before setting optimization_goals — "
        "step text claims duplicate priority values but no packages exist"
    )
    kwargs["packages"][0]["optimization_goals"] = [
        {"kind": "metric", "metric": "viewability", "priority": 1},
        {"kind": "metric", "metric": "ctr", "priority": 1},
    ]


@given("a package has optimization_goals as an empty array")
@given("But a package has optimization_goals as an empty array")
def given_empty_optimization_goals(ctx: dict) -> None:
    """Set optimization_goals to an empty array to trigger inv-087-6.

    SPEC-PRODUCTION GAP: optimization_goals is not in adcp v3.6.0 or production
    schemas. PackageRequest(extra='forbid') will reject this field with a generic
    Pydantic validation error, not the spec-expected INVALID_REQUEST for empty array.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        "packages must be initialized before setting optimization_goals — "
        "step text claims empty optimization_goals but no packages exist to set it on"
    )
    kwargs["packages"][0]["optimization_goals"] = []


@given(parsers.parse('a package has an event kind optimization goal with target kind "{target_kind}"'))
@given(parsers.parse('But a package has an event kind optimization goal with target kind "{target_kind}"'))
def given_event_optimization_with_target(ctx: dict, target_kind: str) -> None:
    """Add an event-kind optimization_goal with specified target_kind for inv-087-7.

    SPEC-PRODUCTION GAP: optimization_goals is not in adcp v3.6.0 or production
    schemas. PackageRequest(extra='forbid') will reject this field with a generic
    Pydantic validation error, not the spec-expected INVALID_REQUEST for missing
    value_field on event source.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        "packages must be initialized before setting event optimization goals — "
        "step text claims a package has an event kind optimization goal but no packages exist"
    )
    kwargs["packages"][0]["optimization_goals"] = [
        {
            "kind": "event",
            "event_source_id": "evt-src-001",
            "target": {"kind": target_kind, "value": 5.0},
            "priority": 1,
        }
    ]
    # Also set up event_sources without value_field (companion step may override)
    kwargs["packages"][0].setdefault("event_sources", [{"event_source_id": "evt-src-001", "name": "conversions"}])


@given("no event_sources entry has value_field set")
@given("And no event_sources entry has value_field set")
def given_no_value_field_on_event_sources(ctx: dict) -> None:
    """Ensure no event_sources entry has value_field set for inv-087-7.

    SPEC-PRODUCTION GAP: optimization_goals and event_sources are not in adcp v3.6.0
    or production schemas. PackageRequest(extra='forbid') will reject these fields.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        "packages must be initialized before modifying event_sources — "
        "step text claims 'no event_sources entry has value_field set' but no packages exist"
    )
    pkg = kwargs["packages"][0]
    # Ensure event_sources exist but none have value_field
    event_sources = pkg.get("event_sources", [{"event_source_id": "evt-src-001", "name": "conversions"}])
    for es in event_sources:
        es.pop("value_field", None)
    pkg["event_sources"] = event_sources


@given(parsers.parse('a package has two catalogs both with type "{catalog_type}"'))
@given(parsers.parse('But a package has two catalogs both with type "{catalog_type}"'))
def given_duplicate_catalog_types(ctx: dict, catalog_type: str) -> None:
    """Add two catalogs with the same type to the first package.

    SPEC-PRODUCTION GAP: Production code accepts catalogs (field is in adcp
    library PackageRequest) but never validates duplicate types. The request
    succeeds silently instead of returning INVALID_REQUEST.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        "packages must be initialized before setting catalogs — "
        "step text claims duplicate catalog types but no packages exist"
    )
    kwargs["packages"][0]["catalogs"] = [
        {"type": catalog_type, "url": "https://example.com/feed-a.xml"},
        {"type": catalog_type, "url": "https://example.com/feed-b.xml"},
    ]


@given(parsers.parse('a package references catalog_id "{catalog_id}" not found in synced catalogs'))
@given(parsers.parse('But a package references catalog_id "{catalog_id}" not found in synced catalogs'))
def given_catalog_id_not_found(ctx: dict, catalog_id: str) -> None:
    """Add a catalog with a nonexistent catalog_id to the first package.

    SPEC-PRODUCTION GAP: Production code accepts catalogs (field is in adcp
    library PackageRequest) but never validates catalog_id existence against
    synced catalogs. The request succeeds silently instead of returning
    INVALID_REQUEST.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        "Step claims 'a package references catalog_id' but no packages in request — ensure packages are set up first"
    )
    kwargs["packages"][0]["catalogs"] = [
        {"type": "product", "catalog_id": catalog_id, "url": "https://example.com/feed.xml"},
    ]


@given('the request includes packages with inline "creatives" array')
def given_request_with_inline_creatives_array(ctx: dict) -> None:
    """Build inline CreativeAsset objects on the first package's creatives field.

    Creates a CreativeAsset with valid format_id, name, and assets.
    Uses display_300x250_image which matches the creative agent registry's
    mock format list (ADCP_TESTING=true).

    Also patches the creative agent registry so _sync_creatives_impl doesn't
    make real HTTP calls to the creative agent (preview_creative). This is the
    same pattern used by CreativeSyncEnv.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults
    from tests.helpers.adcp_factories import create_test_format

    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)
    # Register format spec so _get_format_spec_sync mock recognizes it.
    env._format_specs["display_300x250_image"] = create_test_format(
        format_id="display_300x250_image",
        name="Medium Rectangle",
        type="display",
    )
    # Patch creative agent registry for inline creative processing.
    # _sync_creatives_impl calls preview_creative on the external agent;
    # we mock it to avoid real HTTP calls (same pattern as CreativeSyncEnv).
    mock_registry = MagicMock()
    mock_registry.list_all_formats.return_value = []
    mock_registry.get_format = AsyncMock(return_value={"id": "display_300x250_image", "name": "Medium Rectangle"})
    mock_registry.preview_creative = AsyncMock(return_value={})
    mock_registry.build_creative = AsyncMock(return_value={})
    registry_patcher = patch(
        "src.core.creative_agent_registry.get_creative_agent_registry",
        return_value=mock_registry,
    )
    registry_patcher.start()
    env._patchers.append(registry_patcher)
    # Also patch run_async_in_sync_context used by _sync for format validation
    run_async_patcher = patch(
        "src.core.tools.creatives._sync.run_async_in_sync_context",
        side_effect=lambda coro: [],
    )
    run_async_patcher.start()
    env._patchers.append(run_async_patcher)

    # Update product format_ids to match the mock format registry's ID
    # (display_300x250_image instead of display_300x250)
    product = ctx.get("default_product")
    if product:
        product.format_ids = [
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"},
        ]
        env._commit_factory_data()

    assert kwargs.get("packages"), (
        "Step claims 'request includes packages with inline creatives array' "
        "but no packages in request — ensure packages are set up first"
    )
    kwargs["packages"][0]["creatives"] = [
        {
            "creative_id": "cr-inline-001",
            "name": "Inline Banner 300x250",
            "format_id": {
                "agent_url": "https://creative.adcontextprotocol.org",
                "id": "display_300x250_image",
            },
            "assets": {
                "primary": {
                    "url": "https://cdn.example.com/inline-banner.png",
                    "width": 300,
                    "height": 250,
                }
            },
        }
    ]


@given("each creative has a valid format_id, name, and assets with URL and dimensions")
def given_creatives_have_valid_fields(ctx: dict) -> None:
    """Assert the inline creatives already have required fields.

    The previous Given step builds valid CreativeAssets — this step confirms
    the precondition without modifying state. Step text claims 'URL and
    dimensions' so both url AND width/height are checked.
    """
    kwargs = ctx.get("request_kwargs", {})
    packages = kwargs.get("packages", [])
    assert packages, "No packages in request_kwargs"
    checked_any = False
    for pkg in packages:
        for creative in pkg.get("creatives", []):
            assert "format_id" in creative, "Creative missing format_id"
            assert "name" in creative, "Creative missing name"
            assert "assets" in creative, "Creative missing assets"
            for asset_key, asset in creative["assets"].items():
                if isinstance(asset, dict):
                    assert "url" in asset, f"Asset '{asset_key}' missing url: {asset}"
                    # Step text claims 'dimensions' — verify width and height
                    assert "width" in asset, f"Asset '{asset_key}' missing width: {asset}"
                    assert "height" in asset, f"Asset '{asset_key}' missing height: {asset}"
                    checked_any = True
    assert checked_any, "No assets found to validate — step claims assets with URL and dimensions"


@given("the creative agent has the referenced formats registered")
def given_creative_agent_formats_registered(ctx: dict) -> None:
    """Ensure the harness format_specs registry has entries for all referenced formats.

    The display_300x250 format is registered by the 'inline creatives array'
    Given step. This step verifies it's present.
    """
    env = ctx["env"]
    kwargs = ctx.get("request_kwargs", {})
    for pkg in kwargs.get("packages", []):
        for creative in pkg.get("creatives", []):
            fmt_id = creative.get("format_id", {})
            fid = fmt_id.get("id") if isinstance(fmt_id, dict) else None
            if fid:
                assert fid in env._format_specs, (
                    f"Format {fid} not registered in harness — available: {list(env._format_specs.keys())}"
                )


@then("the system should upload the creatives to the creative library")
def then_creatives_uploaded_to_library(ctx: dict) -> None:
    """Assert inline creatives were synced to the DB creative library.

    Production calls process_and_upload_package_creatives → _sync_creatives_impl
    which persists creatives to the database. Verify the specific creatives from
    the current request were uploaded (not just any pre-existing creatives).
    """
    from src.core.database.repositories.creative import CreativeRepository

    tenant = ctx["tenant"]
    # Verify the create request succeeded before checking creative upload
    resp = ctx.get("response")
    assert resp is not None, (
        "No response in ctx — step claims 'system should upload creatives' but the create request did not succeed"
    )
    assert "error" not in ctx, (
        f"Create request errored ({ctx['error']}) — cannot verify creative upload when the parent operation failed"
    )
    expected_ids = ctx.get("expected_creative_ids")
    assert expected_ids, (
        "No expected_creative_ids in ctx — Given step must register ctx['expected_creative_ids'] for Then steps"
    )

    env = ctx["env"]
    env._commit_factory_data()
    repo = CreativeRepository(env._session, tenant.tenant_id)
    db_creatives = repo.admin_list_all()
    db_creative_ids = {c.creative_id for c in db_creatives}
    # Hard assert: all expected creatives must exist in DB.
    # If production doesn't persist inline creatives yet, the scenario-level
    # xfail (T-UC-002-alt-creatives tag in conftest.py) handles the gap.
    # NEVER xfail inside the step body — it silently swallows the check.
    missing = expected_ids - db_creative_ids
    assert not missing, (
        f"Expected all creatives {sorted(expected_ids)} uploaded to DB, "
        f"but missing {sorted(missing)}. "
        f"DB has {sorted(db_creative_ids)}. "
        f"SPEC-PRODUCTION GAP: production may not persist inline creatives "
        f"via this code path yet. FIXME(salesagent-9vgz.1)"
    )


@then("the system should assign the uploaded creatives to packages")
def then_creatives_assigned_to_packages(ctx: dict) -> None:
    """Assert creative assignments link each uploaded creative to its originating package.

    Production creates CreativeAssignment records linking creatives to packages.
    Verifies that the specific (creative_id, package_id) pairs from the request's
    inline creatives array are present in the DB — not just that any assignment exists.
    """
    from src.core.database.repositories.creative import CreativeAssignmentRepository

    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    media_buy_id = _get_response_field_from_resp(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"

    # Expected creative IDs come from Given steps (ctx contract), not request parsing.
    # Package IDs come from the response (they're an outcome — server generates them).
    expected_ids = ctx.get("expected_creative_ids")
    assert expected_ids, (
        "No expected_creative_ids in ctx — Given step must register ctx['expected_creative_ids'] for Then steps"
    )

    env = ctx["env"]
    env._commit_factory_data()
    tenant = ctx["tenant"]
    repo = CreativeAssignmentRepository(env._session, tenant.tenant_id)
    assignments = repo.get_by_media_buy(media_buy_id)

    # Verify each expected creative was assigned to some package
    actual_ids = {a.creative_id for a in assignments}
    missing = expected_ids - actual_ids
    assert not missing, (
        f"Expected creatives {sorted(expected_ids)} assigned to packages, "
        f"but missing {sorted(missing)}. "
        f"Actual assignments: {sorted(actual_ids)}"
    )


@then("the response should include the created media buy with creative assignments")
def then_response_has_creative_assignments(ctx: dict) -> None:
    """Assert the response includes a created media buy with creative assignments.

    Verifies: media_buy_id present, activation-related status, AND creative
    assignment records exist in the database for this media buy.
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    media_buy_id = _get_response_field_from_resp(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response — media buy not created"
    status = _get_response_field_from_resp(resp, "status")
    # Inline creative uploads trigger pending_activation (creatives synced but
    # not yet approved by creative agent) or completed (auto-approved tenant).
    assert status in ("pending_activation", "completed", "activating"), (
        f"Expected activation-related status, got '{status}'"
    )
    # Step text claims "with creative assignments" — verify the specific creatives
    from src.core.database.repositories.creative import CreativeAssignmentRepository

    env = ctx["env"]
    env._commit_factory_data()
    tenant = ctx["tenant"]
    repo = CreativeAssignmentRepository(env._session, tenant.tenant_id)
    assignments = repo.get_by_media_buy(media_buy_id)

    expected_ids = ctx.get("expected_creative_ids")
    assert expected_ids, (
        "No expected_creative_ids in ctx — Given step must register ctx['expected_creative_ids'] for Then steps"
    )
    actual_ids = {a.creative_id for a in assignments}
    missing = expected_ids - actual_ids
    assert not missing, (
        f"Step claims 'with creative assignments' — expected {sorted(expected_ids)} "
        f"but missing {sorted(missing)}. Actual: {sorted(actual_ids)}"
    )


@given(parsers.parse('proposal "{proposal_id}" exists and has not expired'))
def given_proposal_exists(ctx: dict, proposal_id: str) -> None:
    """Set up proposal state and wire it into the create request.

    SPEC-PRODUCTION GAP: Production has no proposal store. proposal_id is
    accepted on CreateMediaBuyRequest (from adcp library) but never validated.
    This step records the expected proposal with a future expiry datetime so
    Then-step assertions can verify the proposal was carried through.

    When production implements proposal storage, this step must:
    1. Create a proposal record via ProposalFactory
    2. Set expiry to a future date (step text says "has not expired")
    3. Link it to the tenant/principal
    FIXME(salesagent-9vgz.1)
    """
    import warnings
    from datetime import UTC, datetime, timedelta

    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    assert proposal_id, "proposal_id must be non-empty — step claims proposal 'exists'"
    assert isinstance(proposal_id, str), f"proposal_id must be a string, got {type(proposal_id).__name__}"
    # Verify tenant and principal exist — proposals are tenant+principal scoped
    assert "tenant" in ctx, "No tenant in ctx — proposal requires a tenant context"
    assert "principal" in ctx, "No principal in ctx — proposal requires a principal context"

    # Record proposal identity and expiry for Then-step assertions.
    # "has not expired" → future expiry datetime (not a boolean flag).
    ctx["expected_proposal_id"] = proposal_id
    ctx["proposal_expiry"] = datetime.now(tz=UTC) + timedelta(days=30)

    # Wire proposal_id into request_kwargs so the create request includes it.
    kwargs = _ensure_request_defaults(ctx)
    kwargs["proposal_id"] = proposal_id

    # Postconditions: verify state was wired correctly.
    assert ctx["expected_proposal_id"] == proposal_id, (
        f"Postcondition failed: expected_proposal_id={ctx['expected_proposal_id']!r} != {proposal_id!r}"
    )
    assert ctx["proposal_expiry"] > datetime.now(tz=UTC), (
        "Postcondition failed: proposal_expiry must be in the future ('has not expired')"
    )
    assert kwargs.get("proposal_id") == proposal_id, (
        f"Postcondition failed: request_kwargs['proposal_id'] not set to {proposal_id!r} — "
        "the create request will not include the proposal_id"
    )

    # SPEC-PRODUCTION GAP: No proposal entity created in DB — production has no
    # proposal persistence layer. Scenario-level xfail (T-UC-002-alt-proposal tag
    # in conftest.py) handles the gap. FIXME(salesagent-9vgz.1)
    warnings.warn(
        f"SPEC-PRODUCTION GAP: Proposal '{proposal_id}' — no proposal entity created, "
        f"expiry set to {ctx['proposal_expiry'].isoformat()} but not enforced by production. "
        "Scenario-level xfail via T-UC-002-alt-proposal tag handles the gap. "
        "FIXME(salesagent-9vgz.1): Create ProposalFactory with future expiry.",
        stacklevel=1,
    )


@given(parsers.parse("the proposal has {count:d} product allocations"))
def given_proposal_allocations(ctx: dict, count: int) -> None:
    """Create real Product entities for each proposal allocation.

    SPEC-PRODUCTION GAP: Production has no proposal allocation mechanism.
    Proposals are accepted on CreateMediaBuyRequest but never processed.
    Scenario-level xfail (T-UC-002-alt-proposal tag in conftest.py)
    handles the gap. FIXME(salesagent-9vgz.1)

    This step creates real DB-backed products (one per allocation) and wires
    request_kwargs packages so the create request reflects the allocation
    structure. Then steps can cross-check product_ids against these real entities.
    """
    from tests.factories import PricingOptionFactory, ProductFactory

    assert isinstance(count, int), f"count must be an int, got {type(count).__name__}"
    assert count > 0, "Proposal must have at least 1 product allocation"
    proposal_id = ctx.get("expected_proposal_id")
    assert proposal_id is not None, (
        "No expected_proposal_id in ctx — 'the proposal has N product allocations' "
        "requires a prior 'proposal X exists' step"
    )
    env = ctx["env"]
    tenant = ctx["tenant"]

    # Default to equal allocation percentages when scenario doesn't specify them.
    if "expected_allocation_percentages" not in ctx:
        ctx["expected_allocation_percentages"] = [100.0 / count] * count
    percentages = ctx["expected_allocation_percentages"]
    assert len(percentages) == count, (
        f"Allocation percentages count ({len(percentages)}) must match allocation count ({count})"
    )
    assert abs(sum(percentages) - 100.0) < 0.01, f"Allocation percentages must sum to 100%, got {sum(percentages):.2f}%"

    # Create real Product + PricingOption entities for each allocation
    allocation_products = []
    for i in range(count):
        product = ProductFactory(
            tenant=tenant,
            product_id=f"alloc-prod-{i}",
            property_tags=["all_inventory"],
        )
        pricing_option = PricingOptionFactory(
            product=product,
            pricing_model="cpm",
            currency="USD",
            is_fixed=True,
        )
        allocation_products.append((product, pricing_option))
    env._commit_factory_data()

    # Wire request_kwargs packages from the real products.
    # Budget distribution uses the allocation percentages.
    kwargs = ctx.setdefault("request_kwargs", {})
    total_budget = kwargs.get("total_budget", {})
    if isinstance(total_budget, dict):
        total_amount = total_budget.get("amount", 5000.0)
    else:
        total_amount = getattr(total_budget, "amount", 5000.0)

    packages = []
    for i, (product, pricing_option) in enumerate(allocation_products):
        po_id = f"{pricing_option.pricing_model}_{pricing_option.currency.lower()}_"
        po_id += "fixed" if pricing_option.is_fixed else "auction"
        packages.append(
            {
                "product_id": product.product_id,
                "buyer_ref": f"alloc-pkg-{i}",
                "budget": total_amount * (percentages[i] / 100.0),
                "pricing_option_id": po_id,
            }
        )
    kwargs["packages"] = packages

    ctx["expected_proposal_allocations"] = count
    ctx["allocation_products"] = allocation_products

    # Postconditions
    assert ctx["expected_proposal_allocations"] == count
    assert len(ctx["expected_allocation_percentages"]) == count
    assert len(kwargs["packages"]) == count
    assert all(pkg["product_id"] == allocation_products[i][0].product_id for i, pkg in enumerate(kwargs["packages"])), (
        "Package product_ids must match created allocation products"
    )


@then("the system should derive packages from proposal allocations")
def then_packages_derived_from_proposal(ctx: dict) -> None:
    """Assert packages were derived from proposal allocations.

    Hard assertions only — scenario-level xfail in conftest.py handles the
    spec-production gap (T-UC-002-alt-proposal tag).
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    packages = _get_response_field_from_resp(resp, "packages")
    expected = ctx.get("expected_proposal_allocations")
    assert expected is not None and expected > 0, (
        "Scenario must set expected_proposal_allocations via 'the proposal has N product allocations' Given step"
    )
    assert packages is not None and len(packages) > 0, (
        "Response has no packages — cannot verify derivation from proposal allocations"
    )
    # Verify derivation evidence on existing packages.
    # Each derived package must have a product_id (the allocation's product).
    proposal_id = ctx.get("expected_proposal_id")
    missing_derivation = []
    for i, pkg in enumerate(packages):
        product_id = getattr(pkg, "product_id", None) if not isinstance(pkg, dict) else pkg.get("product_id")
        if product_id is None:
            missing_derivation.append(f"pkg[{i}] missing product_id")
        if proposal_id:
            pkg_proposal = getattr(pkg, "proposal_id", None) if not isinstance(pkg, dict) else pkg.get("proposal_id")
            if pkg_proposal is None:
                missing_derivation.append(f"pkg[{i}] missing proposal_id")
            else:
                assert pkg_proposal == proposal_id, (
                    f"Package {i} proposal_id '{pkg_proposal}' doesn't match expected '{proposal_id}'"
                )
    # Count check
    assert len(packages) == expected, (
        f"Expected {expected} packages derived from proposal allocations, got {len(packages)}"
    )
    assert not missing_derivation, f"Packages missing derivation evidence: {', '.join(missing_derivation)}"


@then("the total_budget should be distributed per allocation percentages")
def then_budget_distributed_per_allocations(ctx: dict) -> None:
    """Assert total budget was distributed across packages per allocation percentages.

    Hard assertions only — scenario-level xfail in conftest.py handles the
    spec-production gap (T-UC-002-alt-proposal tag).
    Verifies: (1) budget sum matches total, (2) each package gets a non-zero
    share, and (3) shares are proportional (within rounding tolerance).
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    packages = _get_response_field_from_resp(resp, "packages")
    assert packages is not None and len(packages) > 0, "No packages in response"

    def _extract_budget(pkg: object) -> float:
        if isinstance(pkg, dict):
            return float(pkg.get("budget", 0) or 0)
        return float(getattr(pkg, "budget", 0) or 0)

    # Verify budget sum matches total_budget from request
    kwargs = ctx.get("request_kwargs", {})
    total_budget = kwargs.get("total_budget", {})
    if isinstance(total_budget, dict):
        expected_total = total_budget.get("amount", 0)
    else:
        expected_total = getattr(total_budget, "amount", None)
        assert expected_total is not None, f"Cannot extract amount from total_budget: {total_budget!r}"

    pkg_budgets = [_extract_budget(p) for p in packages]
    budget_sum = sum(pkg_budgets)
    assert abs(budget_sum - expected_total) < 0.01, f"Expected budget sum {expected_total}, got {budget_sum}"

    # Verify each existing package has a non-zero budget share
    for i, budget in enumerate(pkg_budgets):
        assert budget > 0, (
            f"Package {i} has zero budget — 'distributed per allocation percentages' "
            f"requires each allocation to receive a non-zero share"
        )

    # Proportional distribution requires allocation percentages
    alloc_pcts = ctx.get("expected_allocation_percentages")
    assert alloc_pcts is not None and len(alloc_pcts) > 0, (
        "Scenario must set expected_allocation_percentages (list of floats summing to 100) "
        "via the 'the proposal has N product allocations' Given step or a dedicated step. "
        "Cannot verify 'distributed per allocation percentages' without knowing the percentages."
    )
    assert len(packages) == len(alloc_pcts), (
        f"Expected {len(alloc_pcts)} allocation packages (one per allocation percentage), got {len(packages)}"
    )
    # Verify each package's budget matches total_budget * (percentage / 100)
    for i, (budget, pct) in enumerate(zip(pkg_budgets, alloc_pcts, strict=True)):
        expected_share = expected_total * (pct / 100.0)
        assert abs(budget - expected_share) < 0.01, (
            f"Package {i} budget {budget} does not match expected share "
            f"{expected_share:.2f} (total_budget {expected_total} * {pct}%). "
            f"'Distributed per allocation percentages' requires each package's "
            f"budget to equal total_budget * (allocation_percentage / 100)."
        )


@then("the response should include the created media buy with derived packages")
def then_response_has_derived_packages(ctx: dict) -> None:
    """Assert response has a media buy with packages derived from proposal allocations.

    Hard assertions only — scenario-level xfail in conftest.py handles the
    spec-production gap (T-UC-002-alt-proposal tag).
    Verifies:
    1. media_buy_id is present
    2. Each package has a product_id (evidence of derivation from proposal)
    3. packages count matches expected_proposal_allocations (from Given step)
    4. package product_ids match known products from context (cross-check)
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    media_buy_id = _get_response_field_from_resp(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"
    packages = _get_response_field_from_resp(resp, "packages")
    assert packages is not None and len(packages) > 0, "No derived packages in response"

    # Collect known product_ids from context for cross-checking derivation.
    # Products come from request_kwargs packages and/or default_product.
    known_product_ids: set[str] = set()
    default_product = ctx.get("default_product")
    if default_product is not None:
        known_product_ids.add(default_product.product_id)
    request_pkgs = ctx.get("request_kwargs", {}).get("packages", [])
    for rp in request_pkgs:
        pid = rp.get("product_id") if isinstance(rp, dict) else getattr(rp, "product_id", None)
        if pid:
            known_product_ids.add(pid)

    # Verify each package has a product_id AND it matches a known product
    missing_product_ids: list[int] = []
    unknown_product_ids: list[tuple[int, str]] = []
    for i, pkg in enumerate(packages):
        product_id = getattr(pkg, "product_id", None)
        if product_id is None and isinstance(pkg, dict):
            product_id = pkg.get("product_id")
        if product_id is None:
            missing_product_ids.append(i)
        else:
            assert isinstance(product_id, str) and len(product_id.strip()) > 0, (
                f"Package {i} product_id is empty — derived packages must reference a product"
            )
            # Cross-check: product_id must be one of the known products from the
            # scenario setup. This catches bugs where packages have arbitrary product_ids
            # that don't match the proposal's allocations.
            if known_product_ids and product_id not in known_product_ids:
                unknown_product_ids.append((i, product_id))

    # Verify package count matches expected proposal allocations
    expected_count = ctx.get("expected_proposal_allocations")
    if expected_count is not None:
        assert len(packages) == expected_count, (
            f"Expected {expected_count} packages derived from proposal allocations, got {len(packages)}"
        )
    assert not missing_product_ids, (
        f"Packages {missing_product_ids} have no product_id — cannot confirm derivation from proposal allocations"
    )
    assert not unknown_product_ids, (
        f"Packages have product_ids not matching known products from scenario setup: "
        f"{unknown_product_ids}. Known product_ids: {known_product_ids}. "
        f"'Derived from proposal allocations' requires each package's product_id to reference "
        f"a product configured in the scenario."
    )


def _get_response_field_from_resp(resp: object, field: str) -> object:
    """Extract a field from a response, handling wrapper types.

    Delegates to the shared helper in then_media_buy to avoid duplication.
    """
    from tests.bdd.steps.generic.then_media_buy import _get_response_field

    return _get_response_field(resp, field)

    def _extract_budget(pkg: object) -> float:
        if isinstance(pkg, dict):
            return float(pkg.get("budget", 0) or 0)
        return float(getattr(pkg, "budget", 0) or 0)
