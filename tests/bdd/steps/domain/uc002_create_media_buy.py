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
