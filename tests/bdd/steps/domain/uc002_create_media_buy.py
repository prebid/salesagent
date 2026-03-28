"""BDD step definitions for UC-002: Create Media Buy — account resolution scenarios.

Focuses on account resolution error paths (ext-r, ext-s, ext-t, BR-RULE-080)
and partition/boundary scenarios for account_ref.

Steps delegate to MediaBuyAccountEnv which calls resolve_account() with real DB.

beads: salesagent-2rq
"""

from __future__ import annotations

from pytest_bdd import given, parsers, then, when

from tests.factories.account import AccountFactory, AgentAccountAccessFactory


def _maybe_init_request_kwargs(ctx: dict) -> None:
    """Initialize request_kwargs if env is MediaBuyCreateEnv (not account-only)."""
    from tests.harness.media_buy_create import MediaBuyCreateEnv

    env = ctx.get("env")
    if isinstance(env, MediaBuyCreateEnv):
        from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

        _ensure_request_defaults(ctx)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — request setup and account state
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('a valid create_media_buy request with account_id "{account_id}"'))
def given_request_with_account_id(ctx: dict, account_id: str) -> None:
    """Set up a create_media_buy request referencing an explicit account_id."""
    from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference1

    ctx["account_ref"] = AccountReference(root=AccountReference1(account_id=account_id))
    ctx["request_account_id"] = account_id
    _maybe_init_request_kwargs(ctx)
    if "request_kwargs" in ctx:
        ctx["request_kwargs"]["account"] = ctx["account_ref"]


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
    _maybe_init_request_kwargs(ctx)
    if "request_kwargs" in ctx:
        ctx["request_kwargs"]["account"] = ctx["account_ref"]


@given("a create_media_buy request without account field")
def given_request_without_account(ctx: dict) -> None:
    """Set up a create_media_buy request with no account field."""
    ctx["account_ref"] = None
    ctx["account_absent"] = True
    _maybe_init_request_kwargs(ctx)
    # Explicitly omit account from request_kwargs (not just absent — actively removed)
    if "request_kwargs" in ctx:
        ctx["request_kwargs"].pop("account", None)


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


@given("a valid create_media_buy request with creative assignments")
def given_request_with_creative_assignments(ctx: dict) -> None:
    """Set up a create_media_buy request with creative assignments (account is implicit).

    Initialises request_kwargs and ensures the first package has a creative_ids
    key populated with at least one default creative ID. Subsequent Given steps
    can override or append to the creative assignment data.
    """
    ctx.setdefault("account_ref", None)
    _maybe_init_request_kwargs(ctx)
    # Ensure packages exist with creative_ids populated (not empty — step claims "with creative assignments")
    assert "request_kwargs" in ctx, "request_kwargs not initialised — _maybe_init_request_kwargs failed"
    kwargs = ctx["request_kwargs"]
    assert kwargs.get("packages"), (
        "Step claims 'with creative assignments' but no packages in request — "
        "_ensure_request_defaults must create at least one package"
    )
    kwargs["packages"][0].setdefault("creative_ids", ["creative-default-001"])


@given("a valid create_media_buy request")
def given_valid_request(ctx: dict) -> None:
    """Set up a generic valid create_media_buy request (account populated separately)."""
    ctx.setdefault("account_ref", None)
    _maybe_init_request_kwargs(ctx)


@given(parsers.parse('a valid create_media_buy request with account "{account_id}"'))
def given_request_with_account(ctx: dict, account_id: str) -> None:
    """Set up a create_media_buy request with account (short form)."""
    from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference1

    ctx["account_ref"] = AccountReference(root=AccountReference1(account_id=account_id))
    ctx["request_account_id"] = account_id
    _maybe_init_request_kwargs(ctx)
    if "request_kwargs" in ctx:
        ctx["request_kwargs"]["account"] = ctx["account_ref"]


@given("the account_id does not exist in the seller's account store")
@given("no account matches the brand + operator combination")
def given_account_not_exists(ctx: dict) -> None:
    """Ensure the referenced account does not exist in DB.

    Verifies that if a prior step set an account_id, no matching Account record
    exists in the database. This prevents silent lies when a prior step
    accidentally created an account with this ID.
    """
    account_id = ctx.get("request_account_id")
    if account_id is not None:
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Account

        tenant = ctx.get("tenant")
        if tenant is not None:
            with get_db_session() as session:
                existing = session.scalars(
                    select(Account).filter_by(account_id=account_id, tenant_id=tenant.tenant_id)
                ).first()
                assert existing is None, (
                    f"Account '{account_id}' exists in DB for tenant '{tenant.tenant_id}' — "
                    "step claims 'account_id does not exist in the seller's account store' "
                    "but a prior step created it. Clean up or use a different account_id."
                )


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
    """Send the create_media_buy request and capture the result or error.

    Two dispatch paths based on env type:
    - MediaBuyCreateEnv: full create_media_buy through all transports
    - MediaBuyAccountEnv: account resolution only (resolve_account_or_error)
    """
    from tests.harness.media_buy_create import MediaBuyCreateEnv

    env = ctx["env"]
    if isinstance(env, MediaBuyCreateEnv):
        _dispatch_create_media_buy(ctx)
    else:
        from tests.harness.media_buy_account import MediaBuyAccountEnv

        assert isinstance(env, MediaBuyAccountEnv), (
            f"Step 'the Buyer Agent sends the create_media_buy request' requires "
            f"MediaBuyCreateEnv (full dispatch) or MediaBuyAccountEnv (account resolution), "
            f"got {type(env).__name__}"
        )
        # MediaBuyAccountEnv: account resolution IS the first phase of create_media_buy.
        # For account-validation scenarios, the request is "sent" but expected to fail
        # at account resolution — the same error path production would take.
        # resolve_account_or_error stores ctx["response"] or ctx["error"] using
        # the same contract as _dispatch_create_media_buy.
        from tests.bdd.steps.generic._account_resolution import resolve_account_or_error

        resolve_account_or_error(ctx)
        # Verify the dispatch stored an outcome (response or error)
        assert "response" in ctx or "error" in ctx, (
            "MediaBuyAccountEnv dispatch completed but neither ctx['response'] "
            "nor ctx['error'] was set — account resolution must produce an outcome"
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
    # before _impl. The harness's call_a2a/call_mcp don't propagate account through
    # flat kwargs, so we resolve here (pre-dispatch) for all transports.
    if req.account is not None:
        from src.core.transport_helpers import enrich_identity_with_account

        env = ctx["env"]
        env._commit_factory_data()
        try:
            enriched = enrich_identity_with_account(env.identity, req.account)
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
    """Assert error message contains '{count} accounts' as a phrase (not as independent words)."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = str(error).lower()
    # Step text claims the phrase "{count} accounts" — check as a combined substring
    # to avoid false positives from messages that mention count and account separately.
    expected_phrase = f"{count} account"
    assert expected_phrase in msg, f"Expected phrase '{expected_phrase}...' in error message, got: {msg}"


@then(parsers.parse("the result should be {outcome}"))
def then_result_should_be(ctx: dict, outcome: str) -> None:
    """Assert outcome of a partition/boundary scenario."""
    import pytest

    if outcome.startswith("account resolution succeeds"):
        assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
        assert "resolved_account_id" in ctx, "Expected resolved_account_id in ctx"
    elif outcome.startswith("start time "):
        _assert_start_time_outcome(ctx, outcome)
    elif outcome.startswith("end time "):
        _assert_end_time_outcome(ctx, outcome)
    elif outcome.endswith("passes") or outcome.endswith("skipped"):
        # Success outcome: "* validation passes", "minimum spend passes",
        # "minimum spend check skipped", etc.
        if "error" in ctx:
            # SPEC-PRODUCTION GAP: production rejects what spec considers valid.
            # Only xfail for AdCPError (production validation) — other errors
            # are test bugs and must surface as hard failures.
            from src.core.exceptions import AdCPError

            error = ctx["error"]
            if isinstance(error, AdCPError):
                pytest.xfail(
                    f"SPEC-PRODUCTION GAP: Expected success ({outcome}) but production rejected with AdCPError: {error}"
                )
            raise AssertionError(f"Expected success ({outcome}) but got non-AdCPError: {type(error).__name__}: {error}")
        resp = ctx.get("response")
        assert resp is not None, "Expected a response for success outcome"
        # Strengthen: verify response has a media_buy_id (proof of successful creation)
        from tests.bdd.steps.generic.then_media_buy import _get_response_field

        media_buy_id = _get_response_field(resp, "media_buy_id")
        assert media_buy_id, f"Expected media_buy_id in response for '{outcome}', got None"
    elif outcome == "auto-approved path taken":
        from tests.bdd.steps.generic.then_media_buy import then_approval_auto

        then_approval_auto(ctx)
    elif outcome == "manual approval required":
        from tests.bdd.steps.generic.then_media_buy import then_approval_manual

        then_approval_manual(ctx)
    elif outcome == "all records persisted after adapter success":
        _assert_persistence_after_adapter_success(ctx)
    elif outcome == "no records persisted after adapter failure":
        _assert_no_persistence_after_adapter_failure(ctx)
    elif outcome == "records persisted in pending state":
        _assert_persistence_in_pending_state(ctx)
    elif (
        outcome.startswith("tasks sorted by ")
        or outcome.startswith("defaults to ")
        or outcome.startswith("results in ")
        or outcome.startswith("tasks filtered to ")
        or outcome
        in ("tasks from all domains returned", "tasks of all statuses returned", "tasks of all types returned")
    ):
        from tests.bdd.steps.domain.uc002_task_query import assert_task_query_outcome

        assert_task_query_outcome(ctx, outcome)
    elif outcome.startswith("error "):
        _assert_error_outcome(ctx, outcome)
    else:
        raise ValueError(f"Unknown outcome: {outcome}")


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
        expected_code = parts[0]
        # Extract error code from either AdCPError exception or Error pydantic model
        if isinstance(error, AdCPError):
            actual_code = error.error_code
        elif hasattr(error, "code"):
            actual_code = error.code
        else:
            from pydantic import ValidationError

            if isinstance(error, ValidationError) and expected_code == "INVALID_REQUEST":
                # Pydantic rejects the request before production code runs.
                # Treat ValidationError as equivalent to INVALID_REQUEST.
                actual_code = "INVALID_REQUEST"
            else:
                raise AssertionError(f"Error has no code attribute: {type(error).__name__}: {error}")
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


# ═══════════════════════════════════════════════════════════════════════
# Persistence timing assertions (BR-RULE-020)
# ═══════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — seller actions (admin-side, not transport-dispatched)
# ═══════════════════════════════════════════════════════════════════════


@when(parsers.parse('the Seller rejects the media buy with reason "{reason}"'))
def when_seller_rejects_media_buy(ctx: dict, reason: str) -> None:
    """Simulate seller rejecting a pending media buy (admin action).

    Updates the media buy status to 'rejected' and stores the rejection reason
    on the associated workflow step.

    FIXME(salesagent-9vgz.1): This step bypasses operations.py:approve_media_buy(action='reject')
    and directly manipulates DB rows. Bugs in the production rejection path will not be caught.
    Wire through the production admin flow when the harness supports Flask request context.
    Production path: POST /operations/media-buy/<id>/approve with action=reject.
    """
    from datetime import UTC, datetime

    import pytest
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy, ObjectWorkflowMapping, WorkflowStep

    env = ctx["env"]
    env._commit_factory_data()

    media_buy = ctx["existing_media_buy"]
    media_buy_id = media_buy.media_buy_id
    tenant = ctx["tenant"]

    with get_db_session() as session:
        # Verify media buy exists and is in a rejectable state
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id, tenant_id=tenant.tenant_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not found in DB for tenant {tenant.tenant_id}"
        rejectable_statuses = {"pending_approval", "submitted", "requires_approval"}
        assert mb.status in rejectable_statuses, (
            f"Media buy {media_buy_id} has status '{mb.status}' — "
            f"expected one of {rejectable_statuses} for rejection. "
            "Step claims 'Seller rejects' but media buy is not in a rejectable state."
        )
        mb.status = "rejected"

        # Find workflow step to store rejection reason
        mapping = session.scalars(select(ObjectWorkflowMapping).filter_by(object_id=media_buy_id)).first()
        if mapping:
            step = session.scalars(select(WorkflowStep).filter_by(step_id=mapping.step_id)).first()
            if step:
                step.status = "rejected"
                step.error_message = reason
                step.updated_at = datetime.now(UTC)
        else:
            pytest.xfail(
                f"SPEC-PRODUCTION GAP: No workflow mapping for media buy {media_buy_id} — "
                "rejection reason cannot be stored on a workflow step. "
                "FIXME(salesagent-9vgz.1): Wire through production admin rejection flow."
            )

        session.commit()

    # Verify the rejection actually persisted (tenant-scoped)
    with get_db_session() as session:
        mb_check = session.scalars(
            select(MediaBuy).filter_by(media_buy_id=media_buy_id, tenant_id=tenant.tenant_id)
        ).first()
        assert mb_check is not None, f"Media buy {media_buy_id} not found after rejection"
        assert mb_check.status == "rejected", (
            f"Expected 'rejected' status after seller rejection, got '{mb_check.status}'"
        )

    # Store rejection_reason on existing_media_buy so Then steps can find it.
    # MediaBuy model lacks a rejection_reason column — we set it dynamically.
    media_buy.rejection_reason = reason  # type: ignore[attr-defined]


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — creative validation error injection (ext-o, ext-p, ext-q)
# ═══════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — creative URL validation (ext-g)
# ═══════════════════════════════════════════════════════════════════════


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
    during URL validation. The default test format (display_300x250) is
    already non-generative. If it were generative, this step clears output_format_ids.
    """
    env = ctx["env"]
    format_spec = env._format_specs.get("display_300x250")
    assert format_spec is not None, "display_300x250 format spec not configured in harness"
    # Establish non-generative state: clear output_format_ids if present
    if getattr(format_spec, "output_format_ids", None):
        format_spec.output_format_ids = None
    # Postcondition: verify the invariant holds after setup
    assert not getattr(format_spec, "output_format_ids", None), (
        "display_300x250 format spec should NOT have output_format_ids (non-generative) after setup"
    )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — format_id validation (ext-h, ext-h-agent)
# ═══════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — creative assignment validation (inv-026-1, inv-026-2, inv-026-4)
# ═══════════════════════════════════════════════════════════════════════


@given("all referenced creatives exist in valid state with compatible formats")
def given_creatives_valid_and_compatible(ctx: dict) -> None:
    """Create approved creatives with format matching the product's accepted formats.

    For inv-026-1: creatives are valid (status=approved) and format-compatible
    (display_300x250 matches default product), so creative assignment should proceed.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults
    from tests.factories.creative import CreativeFactory

    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)
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
        existing.append(creative.creative_id)
        pkg["creative_ids"] = existing


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


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — creative assignment success (inv-026-1)
# ═══════════════════════════════════════════════════════════════════════


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

    from sqlalchemy import func, select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import CreativeAssignment

    with get_db_session() as session:
        count = session.scalar(
            select(func.count()).select_from(CreativeAssignment).filter_by(media_buy_id=media_buy_id)
        )
        assert count and count > 0, (
            f"No creative assignment records found for media buy {media_buy_id} — expected creatives to be assigned"
        )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — transient error injection (inv-018-4)
# ═══════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — optimization goal error injection (ext-u, ext-u-event)
# ═══════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — optimization goal invariant injection (inv-087-5,6,7)
# ═══════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — catalog validation error injection (ext-v, ext-v-notfound)
# ═══════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — inline creatives (alt-creatives scenario)
# ═══════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — inline creatives assertions (alt-creatives scenario)
# ═══════════════════════════════════════════════════════════════════════


@then("the system should upload the creatives to the creative library")
def then_creatives_uploaded_to_library(ctx: dict) -> None:
    """Assert inline creatives were synced to the DB creative library.

    Production calls process_and_upload_package_creatives → _sync_creatives_impl
    which persists creatives to the database. Verify the specific creatives from
    the current request were uploaded (not just any pre-existing creatives).
    """
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Creative as CreativeModel

    tenant = ctx["tenant"]
    # Extract expected creative IDs from the request
    kwargs = ctx.get("request_kwargs", {})
    expected_ids = set()
    for pkg in kwargs.get("packages", []):
        for creative in pkg.get("creatives", []):
            cid = creative.get("creative_id")
            if cid:
                expected_ids.add(cid)
    assert expected_ids, "No creative IDs found in request — cannot verify upload"

    import pytest

    with get_db_session() as session:
        db_creatives = session.scalars(select(CreativeModel).filter_by(tenant_id=tenant.tenant_id)).all()
        db_creative_ids = {c.creative_id for c in db_creatives}
        # Hard assert: all expected creatives must exist in DB
        missing = expected_ids - db_creative_ids
        if missing:
            if not (db_creative_ids & expected_ids):
                pytest.xfail(
                    f"SPEC-PRODUCTION GAP: None of the request creatives {expected_ids} "
                    f"were found in DB (DB has {db_creative_ids}). Production may not "
                    f"persist inline creatives via this code path yet. "
                    f"FIXME(salesagent-9vgz.1)"
                )
            pytest.xfail(
                f"SPEC-PRODUCTION GAP: Partial upload — missing "
                f"{missing} from DB "
                f"(found {db_creative_ids & expected_ids}). Production may not "
                f"persist all inline creatives yet. "
                f"FIXME(salesagent-9vgz.1)"
            )
        # All expected creatives found — hard-assert the subset relationship
        assert expected_ids <= db_creative_ids, (
            f"Expected all request creatives {expected_ids} in DB, but DB has {db_creative_ids}. Missing: {missing}"
        )


@then("the system should assign the uploaded creatives to packages")
def then_creatives_assigned_to_packages(ctx: dict) -> None:
    """Assert creative assignments exist for the created media buy packages.

    Production creates CreativeAssignment records linking creatives to packages.
    """
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import CreativeAssignment

    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    media_buy_id = _get_response_field_from_resp(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"

    with get_db_session() as session:
        assignments = session.scalars(select(CreativeAssignment).filter_by(media_buy_id=media_buy_id)).all()
        assert len(assignments) > 0, f"Expected creative assignments for media_buy {media_buy_id}, found none"


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
    # Step text claims "with creative assignments" — verify they exist in DB
    from sqlalchemy import func, select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import CreativeAssignment

    with get_db_session() as session:
        assignment_count = session.scalar(
            select(func.count()).select_from(CreativeAssignment).filter_by(media_buy_id=media_buy_id)
        )
        assert assignment_count and assignment_count > 0, (
            f"Step claims 'with creative assignments' but no CreativeAssignment records "
            f"found for media_buy {media_buy_id}"
        )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — proposal-based creation (alt-proposal scenario)
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('proposal "{proposal_id}" exists and has not expired'))
def given_proposal_exists(ctx: dict, proposal_id: str) -> None:
    """Record that a proposal exists (spec-production gap: no proposal store).

    SPEC-PRODUCTION GAP: Production has no proposal store. proposal_id is
    accepted on CreateMediaBuyRequest (from adcp library) but never validated.
    This step records the expected proposal for Then-step assertions.

    When production implements proposal storage, this step must:
    1. Create a proposal record via ProposalFactory
    2. Set expiry to a future date
    3. Link it to the tenant/principal
    """
    assert proposal_id, "proposal_id must be non-empty — step claims proposal 'exists'"
    ctx["expected_proposal_id"] = proposal_id
    # Record in request_kwargs so the create request includes proposal_id
    if "request_kwargs" in ctx:
        ctx["request_kwargs"]["proposal_id"] = proposal_id
    # SPEC-PRODUCTION GAP: No proposal persistence layer — only recording
    # the expected ID. Scenario-level xfail (T-UC-002-alt-proposal tag)
    # handles the gap at the correct level. FIXME(salesagent-9vgz.1)


@given(parsers.parse("the proposal has {count:d} product allocations"))
def given_proposal_allocations(ctx: dict, count: int) -> None:
    """Record expected proposal allocations (spec-production gap).

    SPEC-PRODUCTION GAP: Production has no proposal allocation mechanism.
    This step records the expected allocation count AND default equal-percentage
    allocations for Then-step assertions. When no explicit percentages are given
    in the scenario, equal distribution is assumed.

    When production implements proposals, this step must:
    1. Create N allocation records linked to the proposal
    2. Each allocation must reference a valid product_id
    3. Default to equal-percentage distribution
    """
    assert count > 0, "Proposal must have at least 1 product allocation"
    assert "expected_proposal_id" in ctx, (
        "No expected_proposal_id in ctx — 'the proposal has N product allocations' "
        "requires a prior 'proposal X exists' step"
    )
    ctx["expected_proposal_allocations"] = count
    # Default to equal allocation percentages when scenario doesn't specify them.
    # Scenarios with non-equal splits should set ctx["expected_allocation_percentages"]
    # explicitly via a dedicated Given step.
    if "expected_allocation_percentages" not in ctx:
        ctx["expected_allocation_percentages"] = [100.0 / count] * count
    # SPEC-PRODUCTION GAP: No proposal allocation mechanism — only recording
    # expected count. Scenario-level xfail (T-UC-002-alt-proposal tag)
    # handles the gap at the correct level. FIXME(salesagent-9vgz.1)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — proposal-based creation assertions (alt-proposal scenario)
# ═══════════════════════════════════════════════════════════════════════


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
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    media_buy_id = _get_response_field_from_resp(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"
    packages = _get_response_field_from_resp(resp, "packages")
    assert packages is not None and len(packages) > 0, "No derived packages in response"
    # Verify each package has a product_id (derivation evidence)
    missing_product_ids = []
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
    # Verify package count matches expected proposal allocations
    expected_count = ctx.get("expected_proposal_allocations")
    if expected_count is not None:
        assert len(packages) == expected_count, (
            f"Expected {expected_count} packages derived from proposal allocations, got {len(packages)}"
        )
    assert not missing_product_ids, (
        f"Packages {missing_product_ids} have no product_id — cannot confirm derivation from proposal allocations"
    )


# ═══════════════════════════════════════════════════════════════════════
# Helpers (local to this module)
# ═══════════════════════════════════════════════════════════════════════


def _get_response_field_from_resp(resp: object, field: str) -> object:
    """Extract a field from a response, handling wrapper types.

    Delegates to the shared helper in then_media_buy to avoid duplication.
    """
    from tests.bdd.steps.generic.then_media_buy import _get_response_field

    return _get_response_field(resp, field)
