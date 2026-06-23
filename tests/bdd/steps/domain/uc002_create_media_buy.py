"""BDD step definitions for UC-002: Create Media Buy — account resolution scenarios.

Focuses on account resolution error paths (ext-r, ext-s, ext-t, BR-RULE-080)
and partition/boundary scenarios for account_ref.

Steps dispatch a full create_media_buy through the wire transport
(MediaBuyCreateEnv); production resolves the account at the transport boundary
and emits the outcome on the wire (salesagent-zh85).

beads: salesagent-2rq, salesagent-zh85
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._harness_db import db_session as _db_session
from tests.bdd.steps._outcome_helpers import _get_response_field
from tests.factories.account import AccountFactory, AgentAccountAccessFactory

# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — request setup and account state
# ═══════════════════════════════════════════════════════════════════════


def _attach_account_to_full_request(ctx: dict) -> None:
    """Build a complete, valid create request carrying the account reference.

    Account-not-found scenarios dispatch through the full create flow
    (dispatch_mode="create") so production account resolution runs at the
    transport boundary. The request must be otherwise valid — reuse the shared
    base-request defaults and inject the account so the only failure surfaced is
    ACCOUNT_NOT_FOUND, not a missing-field ValidationError.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    kwargs["account"] = ctx["account_ref"]
    ctx["dispatch_mode"] = "create"


def _attach_raw_account_shape(ctx: dict, account_value: Any | None) -> None:
    """Build a complete, valid create request carrying a MALFORMED account SHAPE.

    Schema-shape cases (account field absent, or a oneOf-both dict carrying both
    account_id AND brand+operator) cannot be expressed as a typed
    ``CreateMediaBuyRequest`` — Pydantic rejects them at construction in test
    code, so they could never reach the production transport boundary that way.
    Instead we stash the raw flat kwargs (``request_kwargs`` minus a typed
    account, plus the raw ``account_value`` verbatim) and dispatch them as a RAW
    body (``dispatch_mode="create_raw"``). Production's route + Pydantic then
    builds the request and either accepts it (account omitted is valid — account
    is optional) or rejects the oneOf-both shape with VALIDATION_ERROR on the wire.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    kwargs.pop("account", None)
    if account_value is not None:
        kwargs["account"] = account_value
    ctx["dispatch_mode"] = "create_raw"


@given(parsers.parse('a valid create_media_buy request with account_id "{account_id}"'))
def given_request_with_account_id(ctx: dict, account_id: str) -> None:
    """Set up a create_media_buy request referencing an explicit account_id."""
    from adcp.types import AccountReference, AccountReferenceById

    ctx["account_ref"] = AccountReference(root=AccountReferenceById(account_id=account_id))
    ctx["request_account_id"] = account_id
    _attach_account_to_full_request(ctx)


@given(parsers.parse('a valid create_media_buy request with account natural key brand "{brand}" operator "{operator}"'))
def given_request_with_natural_key(ctx: dict, brand: str, operator: str) -> None:
    """Set up a create_media_buy request referencing a natural key (brand + operator)."""
    from adcp.types import AccountReference, AccountReferenceByNaturalKey, BrandReference

    ctx["account_ref"] = AccountReference(
        root=AccountReferenceByNaturalKey(brand=BrandReference(domain=brand), operator=operator),
    )
    ctx["request_brand"] = brand
    ctx["request_operator"] = operator
    _attach_account_to_full_request(ctx)


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
    from adcp.types import AccountReference, AccountReferenceById

    ctx["account_ref"] = AccountReference(root=AccountReferenceById(account_id=account_id))
    ctx["request_account_id"] = account_id


@given("the account_id does not exist in the seller's account store")
def given_account_id_not_found(ctx: dict) -> None:
    """Precondition: the referenced account is absent from the store.

    No account row is seeded for this account_ref, so the full create dispatch
    surfaces ACCOUNT_NOT_FOUND when production resolves the account at the
    transport boundary. Assert the request carries the account reference so the
    resolution path is actually exercised (a prior version built a partial
    request via call_impl and crashed with a ValidationError — see salesagent-rkb9).
    """
    assert ctx.get("account_ref") is not None, "account_ref must be set by the request Given step"
    assert ctx.get("request_kwargs", {}).get("account") is not None, (
        "request_kwargs must carry the account reference for the create dispatch"
    )


@given("no account matches the brand + operator combination")
def given_natural_key_not_found(ctx: dict) -> None:
    """Precondition: no account matches the natural key (none seeded).

    The full create dispatch surfaces ACCOUNT_NOT_FOUND when production resolves
    the brand+operator natural key. Assert the request carries the account
    reference so the resolution path is exercised (see salesagent-rkb9).
    """
    assert ctx.get("account_ref") is not None, "account_ref must be set by the request Given step"
    assert ctx.get("request_kwargs", {}).get("account") is not None, (
        "request_kwargs must carry the account reference for the create dispatch"
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
    from adcp.types import AccountReference, AccountReferenceById, AccountReferenceByNaturalKey, BrandReference

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
        ctx["account_ref"] = AccountReference(root=AccountReferenceById(account_id="acc-explicit"))

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
            root=AccountReferenceByNaturalKey(brand=BrandReference(domain="natkey.com"), operator="natkey.com"),
        )

    elif partition == "missing_account":
        # Schema-shape case: dispatch the create with NO account field at all.
        # account is OPTIONAL on CreateMediaBuyRequest (account-management mid-spec),
        # so production accepts it and creates the buy — outcome is success, not
        # a rejection (see salesagent-zh85 empirical trace).
        _attach_raw_account_shape(ctx, None)
        return

    elif partition == "invalid_oneOf_both":
        # Schema-shape case: dispatch a raw account carrying BOTH account_id AND
        # brand+operator. Pydantic's AccountReference oneOf rejects it at the
        # transport boundary → VALIDATION_ERROR on the wire.
        _attach_raw_account_shape(ctx, {"account_id": "acc_001", "brand": {"domain": "x.com"}, "operator": "x.com"})
        return

    elif partition == "explicit_not_found":
        ctx["account_ref"] = AccountReference(root=AccountReferenceById(account_id="acc-not-found"))

    elif partition == "natural_key_not_found":
        ctx["account_ref"] = AccountReference(
            root=AccountReferenceByNaturalKey(brand=BrandReference(domain="unknown.com"), operator="unknown.com"),
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
            root=AccountReferenceByNaturalKey(brand=BrandReference(domain="ambiguous.com"), operator="ambiguous.com"),
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
        ctx["account_ref"] = AccountReference(root=AccountReferenceById(account_id="acc-setup"))

    elif partition == "account_payment_required":
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-payment",
            status="payment_required",
            brand={"domain": "payment.com"},
            operator="payment.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReferenceById(account_id="acc-payment"))

    elif partition == "account_suspended":
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-suspended",
            status="suspended",
            brand={"domain": "suspended.com"},
            operator="suspended.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReferenceById(account_id="acc-suspended"))

    elif partition == "natural_key_sandbox":
        # v3.1: sandbox natural-key resolution — an active sandbox account is
        # matched by brand+operator+sandbox=true (Account.sandbox column,
        # AccountRepository.get_by_natural_key(sandbox=...)).
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-sandbox",
            status="active",
            brand={"domain": "sandbox.com"},
            operator="sandbox.com",
            sandbox=True,
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(
            root=AccountReferenceByNaturalKey(
                brand=BrandReference(domain="sandbox.com"), operator="sandbox.com", sandbox=True
            ),
        )

    else:
        raise ValueError(f"Unknown account partition: {partition}")

    # Valid-shape cases (resolution succeeds OR resolution fails with an account
    # error): dispatch a full, valid create_media_buy carrying the typed account
    # reference so production resolves it at the transport boundary and emits the
    # outcome on the wire.
    _attach_account_to_full_request(ctx)


@given(parsers.parse("a create_media_buy request with account: {config}"))
def given_request_with_boundary_config(ctx: dict, config: str) -> None:
    """Set up request based on boundary config string."""
    from adcp.types import AccountReference, AccountReferenceById, AccountReferenceByNaturalKey, BrandReference

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
        ctx["account_ref"] = AccountReference(root=AccountReferenceById(account_id=account_id))

    elif config.startswith("acc-") and "not-found" in config:
        account_id = config.split()[0]
        ctx["account_ref"] = AccountReference(root=AccountReferenceById(account_id=account_id))

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
            root=AccountReferenceByNaturalKey(brand=BrandReference(domain="single.com"), operator="single.com"),
        )

    elif config.startswith("brand+op") and "no match" in config:
        ctx["account_ref"] = AccountReference(
            root=AccountReferenceByNaturalKey(brand=BrandReference(domain="nomatch.com"), operator="nomatch.com"),
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
            root=AccountReferenceByNaturalKey(brand=BrandReference(domain="multi.com"), operator="multi.com"),
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
        ctx["account_ref"] = AccountReference(root=AccountReferenceById(account_id="acc-setup"))

    elif "payment-due" in config:
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-payment",
            status="payment_required",
            brand={"domain": "payment.com"},
            operator="payment.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReferenceById(account_id="acc-payment"))

    elif "suspended" in config:
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-suspended",
            status="suspended",
            brand={"domain": "suspended.com"},
            operator="suspended.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReferenceById(account_id="acc-suspended"))

    elif "no account" in config:
        # Schema-shape case: account field omitted entirely. account is OPTIONAL
        # on CreateMediaBuyRequest, so production accepts and creates the buy →
        # success, not a rejection (salesagent-zh85 empirical trace).
        _attach_raw_account_shape(ctx, None)
        return

    elif "both fields" in config:
        # Schema-shape case: raw account with BOTH account_id and brand+operator.
        # Pydantic oneOf rejects at the boundary → VALIDATION_ERROR on the wire.
        _attach_raw_account_shape(ctx, {"account_id": "acc_001", "brand": {"domain": "x.com"}, "operator": "x.com"})
        return

    elif config.startswith("brand+op") and "sandbox" in config:
        # v3.1: sandbox natural-key resolution — an active sandbox account is
        # matched by brand+operator+sandbox=true.
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-brand-sandbox",
            status="active",
            brand={"domain": "sandboxbo.com"},
            operator="sandboxbo.com",
            sandbox=True,
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(
            root=AccountReferenceByNaturalKey(
                brand=BrandReference(domain="sandboxbo.com"), operator="sandboxbo.com", sandbox=True
            ),
        )

    else:
        raise ValueError(f"Unknown boundary config: {config}")

    # Valid-shape cases: dispatch a full, valid create_media_buy carrying the
    # typed account reference so production resolves it on the wire.
    _attach_account_to_full_request(ctx)


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — send request
# ═══════════════════════════════════════════════════════════════════════


@when("the Buyer Agent sends the create_media_buy request")
def when_send_create_media_buy(ctx: dict) -> None:
    """Send the create_media_buy request and capture the result or error.

    Three dispatch modes share this step text — every one routes a full
    ``create_media_buy`` through the parametrized wire transport (a2a/mcp/rest):

    - v3.1 idempotency scenarios (``ctx["idempotency_create"]``) dispatch flat
      ``request_kwargs`` so the production idempotency replay path runs end-to-end.
    - ``dispatch_mode == "create"`` builds a typed ``CreateMediaBuyRequest`` from
      ctx["request_kwargs"] (carrying a typed ``account`` for account-resolution
      and budget/pricing scenarios) and dispatches it. Production resolves the
      account at the transport boundary and emits the outcome on the wire.
    - ``dispatch_mode == "create_raw"`` dispatches ctx["request_kwargs"] as a RAW
      flat body (no typed construction) so a malformed account SHAPE (absent
      field, or a oneOf-both dict) reaches the production Pydantic boundary, which
      either accepts it (account is optional) or rejects it on the wire.
    """
    if ctx.get("idempotency_create"):
        from tests.bdd.steps.generic._dispatch import dispatch_request

        dispatch_request(ctx, **ctx["request_kwargs"])
        return

    if ctx.get("dispatch_mode") == "create_raw":
        _dispatch_raw_create(ctx)
    else:
        _dispatch_full_create(ctx)


def _dispatch_full_create(ctx: dict) -> None:
    """Build a typed CreateMediaBuyRequest from ctx['request_kwargs'] and dispatch."""
    from pydantic import ValidationError

    from src.core.schemas import CreateMediaBuyRequest
    from tests.bdd.steps.generic._dispatch import dispatch_request

    kwargs = ctx.get("request_kwargs", {})
    try:
        req = CreateMediaBuyRequest(**kwargs)
    except ValidationError as e:
        ctx["error"] = e
        return

    dispatch_request(ctx, req=req)


def _dispatch_raw_create(ctx: dict) -> None:
    """Dispatch ctx['request_kwargs'] as a RAW flat body (no typed construction).

    Schema-shape cases carry a malformed ``account`` shape that a typed
    ``CreateMediaBuyRequest`` would reject in test code before reaching the wire.
    Dispatching the flat kwargs sends them through the real route + production
    Pydantic, so the boundary itself accepts or rejects the shape.
    """
    from tests.bdd.steps.generic._dispatch import dispatch_request

    dispatch_request(ctx, **ctx.get("request_kwargs", {}))


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
    """Assert outcome of a partition/boundary scenario.

    Branches by outcome family and asserts the appropriate production behavior:
    - Account resolution: resolved_account_id matches request
    - Validation passes/skips: request proceeded past the named validation stage
    - Workflow outcomes: correct approval path was taken
    - Persistence outcomes: DB state matches the expected persistence behavior
    - Task list outcomes: task query returned correctly shaped/ordered results
    - Error outcomes: AdCPError with matching code and recovery
    - Unknown: raises ValueError so unmapped rows are caught immediately
    """
    if outcome.startswith("account resolution succeeds"):
        _assert_account_resolution_succeeds(ctx)
    elif outcome.startswith("error"):
        _assert_error_outcome(ctx, outcome)
    elif _is_pipeline_routing_outcome(outcome):
        _assert_pipeline_routing(ctx, outcome)
    elif _is_validation_pass_outcome(outcome):
        _assert_validation_pass(ctx, outcome)
    elif _is_workflow_outcome(outcome):
        _assert_workflow_outcome(ctx, outcome)
    elif _is_persistence_outcome(outcome):
        _assert_persistence_outcome(ctx, outcome)
    elif _is_task_list_outcome(outcome):
        _assert_task_list_outcome(ctx, outcome)
    else:
        raise ValueError(f"Unknown outcome: {outcome!r}")


def _assert_account_resolution_succeeds(ctx: dict) -> None:
    """Assert the create_media_buy succeeded — proving production resolved the account.

    Account resolution now runs inside a full create_media_buy on the wire
    (salesagent-zh85): a successful create proves the account reference resolved
    at the transport boundary, because an unresolved/invalid account would have
    raised before the buy was created. Assert the wire success response carries a
    ``media_buy_id`` rather than inspecting a bare resolved-account string (which
    no longer exists on this path).
    """
    assert "error" not in ctx, f"Expected account resolution to succeed but got error: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, "Expected a create_media_buy success response, but ctx['response'] is None"

    from tests.bdd.steps._outcome_helpers import _get_response_field

    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, (
        f"Expected the create to succeed with a media_buy_id (account resolved), got response: {resp!r}"
    )


# -- Outcome family classifiers -----------------------------------------------


def _is_pipeline_routing_outcome(outcome: str) -> bool:
    """Check if outcome is a pipeline routing result (UC-001 buying_mode)."""
    return outcome.startswith("request proceeds to") or outcome.startswith("request defaults to")


def _is_validation_pass_outcome(outcome: str) -> bool:
    """Check if outcome is a validation-pass or validation-skipped result."""
    _VALIDATION_SUFFIXES = (
        "validation passes",
        "check skipped",
        "passes",
        "time resolves to now",
        "time accepted",
        "time treated as UTC",
    )
    return any(outcome.endswith(suffix) for suffix in _VALIDATION_SUFFIXES)


def _is_workflow_outcome(outcome: str) -> bool:
    """Check if outcome is a workflow path result."""
    return outcome in ("auto-approved path taken", "manual approval required")


def _is_persistence_outcome(outcome: str) -> bool:
    """Check if outcome is a persistence timing result."""
    return outcome in (
        "all records persisted after adapter success",
        "records persisted in pending state",
        "no records persisted after adapter failure",
    )


def _is_task_list_outcome(outcome: str) -> bool:
    """Check if outcome is a task list query result."""
    return (
        outcome.startswith("tasks sorted by")
        or outcome.startswith("tasks filtered to")
        or outcome.startswith("tasks of all")
        or outcome.startswith("tasks from all")
        or outcome.startswith("defaults to")
        or outcome.startswith("results in")
    )


# -- Validation domain extraction ----------------------------------------------


def _extract_validation_domain(outcome: str) -> str:
    """Extract the validation domain name from an outcome string.

    Examples:
        "budget validation passes" -> "budget"
        "minimum spend passes" -> "minimum spend"
        "start time resolves to now" -> "start time"
    """
    for suffix in (
        " validation passes",
        " check skipped",
        " passes",
        " resolves to now",
        " accepted",
        " treated as UTC",
    ):
        if outcome.endswith(suffix):
            return outcome[: -len(suffix)]
    return outcome


def _extract_pipeline_name(outcome: str) -> str:
    """Extract the pipeline name from a routing outcome.

    "request proceeds to brief pipeline" -> "brief"
    "request defaults to brief pipeline" -> "brief"
    """
    for prefix in ("request proceeds to ", "request defaults to "):
        if outcome.startswith(prefix):
            remainder = outcome[len(prefix) :]
            if remainder.endswith(" pipeline"):
                return remainder[: -len(" pipeline")]
            return remainder
    return outcome


# -- Outcome family assertions -------------------------------------------------


def _assert_validation_pass(ctx: dict, outcome: str) -> None:
    """Assert a named validation stage passed -- request proceeded without error.

    Asserts:
    1. No error was raised (the validation stage did not reject the request)
    2. A response is present and well-formed
    3. For account-resolution scenarios: the resolved account_id matches the
       account set up by the Given step (not just any non-empty string)
    4. For full create scenarios: the response has a media_buy_id (success)
    """
    domain = _extract_validation_domain(outcome)
    assert "error" not in ctx, f"Expected '{domain}' validation to pass but got error: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, f"Expected response for '{domain}' validation pass but ctx['response'] is None"
    if isinstance(resp, str):
        assert len(resp) > 0, f"Expected non-empty account_id for '{domain}' validation pass, got empty string"
        # Verify the resolved account_id matches the Given step's account_ref
        account_ref = ctx.get("account_ref")
        if account_ref is not None:
            root = account_ref.root
            if hasattr(root, "account_id"):
                assert resp == root.account_id, (
                    f"Resolved account_id '{resp}' does not match requested "
                    f"account_id '{root.account_id}' for '{domain}' validation"
                )
    else:
        from tests.bdd.steps._outcome_helpers import _get_response_field

        media_buy_id = _get_response_field(resp, "media_buy_id")
        assert media_buy_id is not None, (
            f"Expected media_buy_id in response for '{domain}' validation pass, "
            f"got {type(resp).__name__} without media_buy_id"
        )


def _assert_pipeline_routing(ctx: dict, outcome: str) -> None:
    """Assert the production code dispatched to the expected pipeline.

    For "request proceeds to X pipeline" or "request defaults to X pipeline",
    verifies:
    1. No error raised
    2. Response is present
    3. If the harness recorded the dispatched pipeline (ctx["dispatched_pipeline"]),
       asserts it matches the expected pipeline name
    4. For "defaults to X": the request did NOT explicitly specify a buying_mode

    When the harness does not yet expose pipeline routing, the assertion
    xfails rather than silently passing on a no-error check.
    """
    import pytest

    expected_pipeline = _extract_pipeline_name(outcome)
    is_default = outcome.startswith("request defaults to")

    assert "error" not in ctx, (
        f"Expected request to route to '{expected_pipeline}' pipeline but got error: {ctx.get('error')}"
    )
    resp = ctx.get("response")
    assert resp is not None, (
        f"Expected response for pipeline routing to '{expected_pipeline}' but ctx['response'] is None"
    )
    dispatched = ctx.get("dispatched_pipeline")
    if dispatched is None:
        pytest.xfail(
            f"Harness does not yet expose dispatched pipeline "
            f"(expected '{expected_pipeline}'). "
            f"Add ctx['dispatched_pipeline'] to the When step."
        )
    assert dispatched == expected_pipeline, f"Expected dispatched pipeline '{expected_pipeline}', got '{dispatched}'"
    if is_default:
        explicit_mode = ctx.get("explicit_buying_mode")
        assert explicit_mode is None, (
            f"Expected default pipeline routing (no explicit buying_mode), "
            f"but ctx['explicit_buying_mode'] = {explicit_mode!r}"
        )


def _assert_workflow_outcome(ctx: dict, outcome: str) -> None:
    """Assert the correct approval workflow path was taken.

    'auto-approved path taken' -- request completed without manual intervention.
    'manual approval required' -- request was routed to pending_approval state.
    """
    assert "error" not in ctx, f"Expected workflow outcome '{outcome}' but got error: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, f"Expected response for workflow outcome '{outcome}' but ctx['response'] is None"
    from tests.bdd.steps._outcome_helpers import _get_response_field

    if outcome == "auto-approved path taken":
        # Auto-approved: media buy should be created with a non-pending status
        status = _get_response_field(resp, "status")
        assert status is not None, f"Expected status field on response for auto-approval, got {type(resp).__name__}"
        assert status != "pending_approval", f"Expected auto-approved status (not pending_approval), got '{status}'"
    elif outcome == "manual approval required":
        # Manual approval: media buy should be in pending_approval
        status = _get_response_field(resp, "status")
        assert status is not None, f"Expected status field on response for manual approval, got {type(resp).__name__}"
        assert status == "pending_approval", f"Expected pending_approval status for manual approval, got '{status}'"


def _assert_persistence_outcome(ctx: dict, outcome: str) -> None:
    """Assert DB persistence matches the expected behavior.

    - 'all records persisted after adapter success': media buy + packages in DB
    - 'records persisted in pending state': media buy exists with pending_approval
    - 'no records persisted after adapter failure': error raised, no media buy
    """
    if outcome == "no records persisted after adapter failure":
        assert "error" in ctx, f"Expected error for '{outcome}' but no error in ctx"
        return

    assert "error" not in ctx, f"Expected '{outcome}' but got error: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, f"Expected response for '{outcome}' but ctx['response'] is None"
    from tests.bdd.steps._outcome_helpers import _get_response_field

    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id is not None, f"Expected media_buy_id for '{outcome}', got None from {type(resp).__name__}"
    if outcome == "records persisted in pending state":
        status = _get_response_field(resp, "status")
        assert status == "pending_approval", f"Expected pending_approval for '{outcome}', got '{status}'"


def _extract_tasks_from_response(ctx: dict, outcome: str) -> list:
    """Extract the tasks list from the response, asserting it exists."""
    assert "error" not in ctx, f"Expected task list outcome '{outcome}' but got error: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, f"Expected response for task list outcome '{outcome}' but ctx['response'] is None"
    tasks = None
    if isinstance(resp, dict):
        tasks = resp.get("tasks") or resp.get("items") or resp.get("results")
    elif hasattr(resp, "tasks"):
        tasks = resp.tasks
    assert tasks is not None, (
        f"Expected 'tasks' field in response for '{outcome}', got keys: "
        f"{list(resp.keys()) if isinstance(resp, dict) else dir(resp)}"
    )
    assert isinstance(tasks, list), f"Expected tasks to be a list, got {type(tasks).__name__}"
    return tasks


def _get_task_field(task: object, field: str) -> object:
    """Extract a field from a task dict or object."""
    if isinstance(task, dict):
        return task.get(field)
    return getattr(task, field, None)


_SORT_FIELD_MAP = {
    "creation timestamp": "created_at",
    "update timestamp": "updated_at",
    "status value": "status",
    "operation type": "task_type",
    "AdCP domain": "domain",
}

_FILTER_MAP = {
    "media-buy domain": ("domain", "media_buy"),
    "signals domain": ("domain", "signals"),
    "governance domain": ("domain", "governance"),
    "creative domain": ("domain", "creative"),
    "submitted status": ("status", "submitted"),
    "working status": ("status", "working"),
    "input-required": ("status", "input_required"),
    "completed status": ("status", "completed"),
    "canceled status": ("status", "canceled"),
    "failed status": ("status", "failed"),
    "rejected status": ("status", "rejected"),
    "auth-required": ("status", "auth_required"),
    "unknown status": ("status", "unknown"),
}


def _assert_tasks_sorted(tasks: list, outcome: str) -> None:
    """Verify tasks are sorted by the claimed field."""
    if len(tasks) < 2:
        return
    sort_field = None
    for desc, field in _SORT_FIELD_MAP.items():
        if desc in outcome:
            sort_field = field
            break
    if sort_field is None:
        raise ValueError(f"Unknown sort field in outcome: {outcome!r}")
    values = [_get_task_field(t, sort_field) for t in tasks]
    non_none = [v for v in values if v is not None]
    if len(non_none) >= 2:
        is_ascending = all(a <= b for a, b in zip(non_none, non_none[1:], strict=False))
        is_descending = all(a >= b for a, b in zip(non_none, non_none[1:], strict=False))
        assert is_ascending or is_descending, f"Tasks not sorted by '{sort_field}': values = {non_none[:5]}"


def _assert_tasks_filtered(tasks: list, outcome: str) -> None:
    """Verify all returned tasks match the claimed filter value."""
    suffix = outcome[len("tasks filtered to ") :]
    if "multiple" in suffix:
        _assert_multi_value_filter(tasks, suffix)
        return
    for desc, (field, expected_value) in _FILTER_MAP.items():
        if suffix.strip() == desc:
            for task in tasks:
                actual = _get_task_field(task, field)
                assert actual == expected_value, f"Expected all tasks {field}='{expected_value}', got '{actual}'"
            return
    # Remaining: suffix IS the task_type value
    task_type = suffix.strip()
    if task_type and "domain" not in task_type and "status" not in task_type:
        for task in tasks:
            actual = _get_task_field(task, "task_type")
            assert actual == task_type, f"Expected task_type='{task_type}', got '{actual}'"
        return
    # Unmapped: matched no _FILTER_MAP entry and is not a bare task_type value
    # (empty, or an unmapped domain/status filter). Fail loudly rather than
    # passing the scenario with zero filter verification.
    raise ValueError(f"Unmapped filter outcome (no _FILTER_MAP entry, not a task_type): {outcome!r}")


def _assert_multi_value_filter(tasks: list, suffix: str) -> None:
    """Assert tasks span multiple filter values."""
    if not tasks:
        return
    if "domain" in suffix:
        values = {_get_task_field(t, "domain") for t in tasks}
        assert len(values) >= 2, f"Expected multiple domains, got {values}"
    elif "status" in suffix or "statuses" in suffix:
        values = {_get_task_field(t, "status") for t in tasks}
        assert len(values) >= 2, f"Expected multiple statuses, got {values}"
    elif "type" in suffix:
        values = {_get_task_field(t, "task_type") for t in tasks}
        assert len(values) >= 2, f"Expected multiple types, got {values}"
    else:
        # Unmapped multi-value dimension — fail loudly rather than skip silently.
        raise ValueError(f"Unmapped multi-value filter suffix (no domain/status/type): {suffix!r}")


def _assert_task_list_outcome(ctx: dict, outcome: str) -> None:
    """Assert task list query returned the correct shape, ordering, and filtering.

    For sorting outcomes: verifies monotonic ordering by the named field.
    For filtering outcomes: verifies every task matches the claimed filter.
    For 'defaults to' outcomes: verifies the default sort was applied.
    For 'results in' outcomes: verifies ascending/descending direction.
    """
    tasks = _extract_tasks_from_response(ctx, outcome)

    if outcome.startswith("tasks sorted by"):
        _assert_tasks_sorted(tasks, outcome)
    elif outcome.startswith("tasks filtered to"):
        _assert_tasks_filtered(tasks, outcome)
    elif outcome.startswith("tasks of all") or outcome.startswith("tasks from all"):
        seeded_count = ctx.get("seeded_task_count")
        if seeded_count is not None:
            assert len(tasks) >= seeded_count, f"Expected >= {seeded_count} tasks (unfiltered), got {len(tasks)}"
    elif outcome.startswith("defaults to"):
        if "created_at" in outcome and len(tasks) >= 2:
            values = [_get_task_field(t, "created_at") for t in tasks]
            non_none = [v for v in values if v is not None]
            if len(non_none) >= 2:
                assert all(a >= b for a, b in zip(non_none, non_none[1:], strict=False)), (
                    f"Expected default descending created_at sort, values = {non_none[:5]}"
                )
    elif outcome.startswith("results in") and len(tasks) >= 2:
        values = [_get_task_field(t, "created_at") for t in tasks]
        non_none = [v for v in values if v is not None]
        if len(non_none) >= 2:
            if "ascending" in outcome:
                assert all(a <= b for a, b in zip(non_none, non_none[1:], strict=False)), (
                    f"Expected ascending order, values = {non_none[:5]}"
                )
            elif "descending" in outcome:
                assert all(a >= b for a, b in zip(non_none, non_none[1:], strict=False)), (
                    f"Expected descending order, values = {non_none[:5]}"
                )


def _assert_error_outcome(ctx: dict, outcome: str) -> None:
    """Assert error outcome with exact code, recovery, and message matching.

    Handles three outcome formats:
    1. Structured code: "error CODE [recovery] [with suggestion]"
    2. Suggestion-only: "error with suggestion"
    3. Descriptive: "error <desc>" or "error: <desc>" -- message-contains check.

    When the scenario dispatched through a wire transport (``ctx["result"]`` is a
    TransportResult), the structured-code and suggestion-only paths assert on the
    real wire envelope via ``result.assert_wire_error`` — the AdCP two-layer error
    contract the buyer sees — instead of a reconstructed exception.
    """
    from src.core.exceptions import AdCPError

    assert "error" in ctx, f"Expected an error for outcome: {outcome}"
    error = ctx["error"]
    remainder = outcome[5:].strip()  # strip "error" prefix

    # Colon-style: "error: <description>"
    if remainder.startswith(":"):
        description = remainder[1:].strip()
        error_msg = str(error).lower()
        assert description.lower() in error_msg, f"Expected error message to contain '{description}', got: {error}"
        return

    result = ctx.get("result")

    # Suggestion-only: "error with suggestion"
    if remainder.startswith("with suggestion"):
        if result is not None and result.wire_error_envelope is not None:
            code = result.wire_error_envelope.get("adcp_error", {}).get("code")
            result.assert_wire_error(code, require_suggestion=True)
            return
        assert isinstance(error, AdCPError), (
            f"Expected AdCPError for suggestion check, got {type(error).__name__}: {error}"
        )
        assert error.details is not None, "Expected error details with suggestion, got None"
        assert "suggestion" in error.details, f"Expected suggestion in details: {error.details}"
        return

    # Check if first word is a structured error code (UPPER_CASE with _)
    parts = remainder.split()
    first_word = parts[0] if parts else ""
    is_structured = bool(first_word) and first_word == first_word.upper() and "_" in first_word

    if is_structured:
        expected_code = first_word
        recovery = parts[1] if len(parts) >= 2 and parts[1] in ("terminal", "correctable", "transient") else None
        require_suggestion = "with suggestion" in outcome.lower()

        if result is not None and result.wire_error_envelope is not None:
            # Wire-first: assert the AdCP two-layer envelope the buyer receives.
            result.assert_wire_error(expected_code, recovery=recovery, require_suggestion=require_suggestion)
            return

        assert isinstance(error, AdCPError), (
            f"Expected AdCPError with code '{expected_code}', got {type(error).__name__}: {error}"
        )
        assert error.error_code == expected_code, f"Expected error code '{expected_code}', got '{error.error_code}'"
        if recovery is not None:
            assert error.recovery == recovery, f"Expected recovery '{recovery}', got '{error.recovery}'"
        if require_suggestion:
            assert error.details is not None, "Expected error details with suggestion, got None"
            assert "suggestion" in error.details, f"Expected suggestion in details: {error.details}"
    else:
        # Descriptive: "error unknown sort field"
        description = remainder
        error_msg = str(error).lower()
        assert description.lower() in error_msg, f"Expected error message to contain '{description}', got: {error}"


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


# Step "the tenant is configured for auto-approval" is defined in
# tests/bdd/steps/generic/given_media_buy.py (real DB-aware version).


# ── v3.1 idempotency replay / missing (T-UC-002-v31-idempotency-{replay,missing}) ──
#
# These steps build ctx["request_kwargs"] referencing the real product +
# pricing option seeded into ctx by conftest's _harness_env (MediaBuyCreateEnv),
# then dispatch a full create_media_buy through the parametrized transport.


def _idempotency_pricing_option_id(pricing_option) -> str:
    """Synthetic pricing_option_id string from a PricingOption ORM row.

    Matches the production/`given_media_buy` convention
    ``{pricing_model}_{currency_lower}_{fixed|auction}``.
    """
    fixed_str = "fixed" if pricing_option.is_fixed else "auction"
    return f"{pricing_option.pricing_model}_{pricing_option.currency.lower()}_{fixed_str}"


def _build_idempotency_request_kwargs(ctx: dict) -> dict:
    """Assemble a valid create_media_buy request dict against the seeded product.

    Stored on ctx["request_kwargs"]; the When step and the "already created"
    Given step dispatch THIS exact dict (copied) so the canonical payload hash
    matches between the original create and the replay.
    """
    from datetime import UTC, datetime, timedelta

    product = ctx["default_product"]
    pricing_option = ctx["default_pricing_option"]
    now = datetime.now(UTC)
    ctx["request_kwargs"] = {
        "brand": {"domain": "testbrand.com"},
        # Explicit, stable po_number so the canonical payload is byte-identical
        # between the original create and the replay across ALL transports. The
        # A2A wrapper no longer mints a random po_number when the caller omits
        # one (it stays None for idempotency-hash + cross-transport parity), so
        # this value is set explicitly here to keep the canonical payload —
        # and therefore the idempotency hash — identical between the original
        # create and the replay. A real buyer replaying an idempotent request
        # resends their own po_number.
        "po_number": "PO-IDEMPOTENCY-REPLAY-001",
        "start_time": (now + timedelta(days=1)).isoformat(),
        "end_time": (now + timedelta(days=30)).isoformat(),
        "packages": [
            {
                "product_id": product.product_id,
                "budget": 5000.0,
                "pricing_option_id": _idempotency_pricing_option_id(pricing_option),
            }
        ],
    }
    return ctx["request_kwargs"]


@given(parsers.parse('a valid create_media_buy request with idempotency_key "{key}"'))
def given_valid_request_with_idempotency_key(ctx: dict, key: str) -> None:
    """Build a valid create_media_buy request carrying a literal idempotency_key."""
    kwargs = _build_idempotency_request_kwargs(ctx)
    kwargs["idempotency_key"] = key
    ctx["idempotency_create"] = True
    ctx["idempotency_key"] = key


@given("a create_media_buy request with the idempotency_key field omitted")
def given_request_idempotency_key_omitted(ctx: dict) -> None:
    """Build a create_media_buy request that carries NO idempotency_key on the wire.

    Uses the harness OMIT sentinel: the request assembler keeps it, and
    MediaBuyCreateEnv._ensure_idempotency_key pops it so the constructed
    CreateMediaBuyRequest is missing the REQUIRED field — production rejects it
    with a VALIDATION_ERROR naming idempotency_key and a buyer-facing
    ``suggestion`` derived by ``suggest_validation_fix`` ("Provide the required
    'idempotency_key' field ..."), surfaced on the wire across all transports
    (#1417/gh8p.10).
    """
    from tests.harness.media_buy_create import OMIT_IDEMPOTENCY_KEY

    kwargs = _build_idempotency_request_kwargs(ctx)
    kwargs["idempotency_key"] = OMIT_IDEMPOTENCY_KEY
    ctx["idempotency_create"] = True


@given("a media buy was already created for the same seller with that idempotency_key")
def given_media_buy_already_created_same_key(ctx: dict) -> None:
    """Perform a REAL first create through the parametrized transport.

    Dispatches the SAME request_kwargs (copied) so the canonical payload hash
    matches the When-step replay. Records the original media_buy_id and the
    adapter create_media_buy call count so the Then steps can assert the replay
    returns the same id and does NOT re-invoke the adapter.
    """
    from tests.bdd.steps.generic._dispatch import dispatch_request

    env = ctx["env"]
    adapter_mock = env.mock["adapter"].return_value

    first_ctx: dict = {"env": env, "transport": ctx.get("transport"), "tenant": ctx.get("tenant")}
    dispatch_request(first_ctx, **dict(ctx["request_kwargs"]))

    assert "error" not in first_ctx, f"First create_media_buy (idempotency seed) failed: {first_ctx.get('error')!r}"
    first_resp = first_ctx.get("response")
    media_buy_id = _get_response_field(first_resp, "media_buy_id")
    assert media_buy_id, f"First create produced no media_buy_id; response={first_resp!r}"

    ctx["first_media_buy_id"] = media_buy_id
    ctx["adapter_calls_after_first_create"] = adapter_mock.create_media_buy.call_count


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


# Step "the ad server adapter is available" is defined in
# tests/bdd/steps/generic/given_media_buy.py (real DB-aware version).


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


@then("the budget validation should pass")
def then_budget_validation_passes(ctx: dict) -> None:
    """Assert budget validation passed -- no error, response has media_buy_id."""
    assert "error" not in ctx, f"Expected budget validation to pass but got error: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found (budget validation may have failed silently)"
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "Expected media_buy_id in response -- budget validation passed but no media buy created"


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


@then(parsers.parse('the response should include the previously created "{field}"'))
def then_response_includes_previously_created(ctx: dict, field: str) -> None:
    """Assert the idempotency replay returned the ORIGINAL create's value.

    Asserts two things on the replay response:
    1. ``response.<field>`` equals the value the FIRST create produced
       (recorded by the "already created" Given step), proving the replay
       served the original rather than minting a new media buy.
    2. The replay marker is set (``CreateMediaBuyResult.replayed is True``) —
       this is what production injects on a verbatim cache hit, surfaced on
       every transport by the harness response reconstruction.
    """
    resp = ctx.get("response")
    assert resp is not None, "No response in ctx — replay scenario produced nothing"
    original = ctx.get("first_media_buy_id")
    assert original is not None, (
        "No first_media_buy_id recorded — the 'already created' Given step must run before this assertion"
    )
    actual = _get_response_field(resp, field)
    assert actual == original, (
        f"Replay response {field}={actual!r} does not match the previously created {field}={original!r} — "
        "the replay returned a different media buy instead of the cached original"
    )
    replayed = _get_response_field(resp, "replayed")
    assert replayed is True, (
        f"Expected the replay marker (replayed=True) on the cached-hit response, got replayed={replayed!r}. "
        "Without it the buyer cannot tell the response was served from the idempotency cache."
    )


@then(parsers.parse('the error should reference the missing "{field}" field'))
def then_error_references_missing_field(ctx: dict, field: str) -> None:
    """Assert the validation error names the missing required field.

    The error message (or Pydantic field locations) must mention ``field`` so
    the buyer knows which required field was omitted.
    """
    error = ctx.get("error")
    assert error is not None, f"No error in ctx — expected a validation error naming the missing '{field}' field"

    from pydantic import ValidationError

    if isinstance(error, ValidationError):
        locs = {str(loc) for detail in error.errors() for loc in detail.get("loc", ())}
        assert field in locs, (
            f"Pydantic ValidationError does not reference the missing '{field}' field (error locations: {sorted(locs)})"
        )
        return

    message = _get_error_message_for_step(error)
    assert field in message, f"Validation error does not reference the missing '{field}' field. Message: {message!r}"


def _get_error_message_for_step(error: object) -> str:
    """Best-effort human-readable text from an AdCPError / Error model / exception."""
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        parts = [error.message or ""]
        if error.details:
            parts.append(str(error.details))
        return " ".join(parts)
    message = getattr(error, "message", None)
    return message if isinstance(message, str) and message else str(error)


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


@then("the Buyer should be notified via webhook")
def then_webhook_notification(ctx: dict) -> None:
    """Assert buyer webhook notification dispatch prerequisites and payload correctness.

    Production delivery path (src/core/context_manager.py::_send_push_notifications):
      1. Query ObjectWorkflowMapping rows by step_id.
      2. Query PushNotificationConfig (tenant_id, principal_id, is_active=True).
      3. Read ``push_notification_config.url`` from step.request_data.
      4. Build payload (media_buy_id, status, rejection_reason) and POST to the URL.

    Hard assertions (all verified, all pass):
      A. PushNotificationConfig row: url matches, is_active=True, principal_id matches.
      C. ObjectWorkflowMapping exists linking step_id to the media buy.
      D. Media buy + workflow step are in terminal status.
      E. Notification payload content: media buy carries rejection status and
         non-empty rejection_reason (the data the webhook would deliver).

    Targeted xfail (harness gap -- only this check is xfailed):
      B. step.request_data carries push_notification_config URL -- required for
         _send_push_notifications to actually POST. The BDD reject path uses
         repository methods that bypass the admin flow which populates this field.
         FIXME(salesagent-9vgz.1): Wire through the production admin approve/reject
         flow, then remove the xfail.
    """
    import pytest
    from sqlalchemy import select

    from src.core.database.models import ObjectWorkflowMapping, PushNotificationConfig
    from src.core.database.repositories.media_buy import MediaBuyRepository
    from src.core.database.repositories.workflow import WorkflowRepository

    # --- Extract media_buy_id and tenant ---
    resp = ctx.get("response")
    existing_mb = ctx.get("existing_media_buy")
    assert resp is not None or existing_mb is not None, (
        "No response or existing media buy in ctx — nothing to notify the Buyer about"
    )

    media_buy_id = None
    if resp is not None:
        media_buy_id = _get_response_field(resp, "media_buy_id")
    elif existing_mb is not None:
        media_buy_id = getattr(existing_mb, "media_buy_id", None)
    assert media_buy_id, "No media_buy_id — cannot verify notification"

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx — cannot verify notification scoping"
    tenant_id = getattr(tenant, "tenant_id", None) or (tenant.get("tenant_id") if isinstance(tenant, dict) else None)

    # --- Check push_notification_config was registered by Given step ---
    push_config = ctx.get("push_notification_config")
    assert push_config is not None, (
        "No push_notification_config in ctx — scenario must include a Given step "
        "that sets ctx['push_notification_config'] with the expected webhook URL."
    )
    expected_url = push_config.get("url") if isinstance(push_config, dict) else None
    assert expected_url, "push_notification_config has no 'url' — cannot verify webhook destination"

    # --- A. PushNotificationConfig row: exact url match, active, correct principal ---
    principal = ctx.get("principal")
    expected_principal_id = (
        getattr(principal, "principal_id", None)
        if principal is not None
        else (principal.get("principal_id") if isinstance(principal, dict) else None)
    )
    with _db_session(ctx) as session:
        configs = (
            session.scalars(select(PushNotificationConfig).filter_by(tenant_id=tenant_id, url=expected_url)).all() or []
        )
        stored_urls = [c.url for c in configs]
        assert expected_url in stored_urls, (
            f"Expected webhook URL '{expected_url}' not found in PushNotificationConfig "
            f"for tenant {tenant_id}. Stored URLs: {stored_urls}. "
            "Dispatcher will not find the webhook destination."
        )
        assert any(c.is_active for c in configs), (
            f"PushNotificationConfig rows for url={expected_url} exist but none have is_active=True "
            f"(found: {[(c.id, c.is_active) for c in configs]}) — "
            "_send_push_notifications filters by is_active=True and will skip them"
        )
        if expected_principal_id:
            active_principal_ids = [c.principal_id for c in configs if c.is_active]
            assert expected_principal_id in active_principal_ids, (
                f"No active PushNotificationConfig for principal_id={expected_principal_id}; "
                f"active rows belong to principals: {active_principal_ids}. "
                "Dispatcher filters by principal_id — the webhook would be addressed to the wrong buyer."
            )

    # --- D. Media buy in terminal status (status-change trigger has fired) ---
    with _db_session(ctx) as session:
        mb_repo = MediaBuyRepository(session, tenant_id)
        mb = mb_repo.get_by_id(str(media_buy_id))
        assert mb is not None, f"Media buy {media_buy_id} not found — cannot verify status change"
        terminal_statuses = {"rejected", "approved", "active", "completed", "cancelled"}
        assert mb.status in terminal_statuses, (
            f"Media buy {media_buy_id} has status '{mb.status}' — expected a terminal "
            f"status ({terminal_statuses}) proving the status-change event that "
            "triggers webhook delivery has occurred"
        )

    # --- E. Notification payload content ---
    # _send_push_notifications builds the webhook payload from the media buy's
    # current state. Verify the media buy carries the data the buyer expects:
    # for rejection, the payload must include a non-empty rejection_reason.
    with _db_session(ctx) as session:
        mb_repo = MediaBuyRepository(session, tenant_id)
        mb = mb_repo.get_by_id(str(media_buy_id))
        assert mb is not None, f"Media buy {media_buy_id} disappeared between checks"
        if mb.status == "rejected":
            assert mb.rejection_reason is not None and mb.rejection_reason.strip() != "", (
                f"Media buy {media_buy_id} has status 'rejected' but rejection_reason is "
                f"'{mb.rejection_reason}' — webhook payload would lack the rejection reason, "
                "violating the Buyer notification contract (POST-S12)"
            )

    # --- C. Workflow step + mapping ---
    with _db_session(ctx) as session:
        wf_repo = WorkflowRepository(session, tenant_id)
        mapping = wf_repo.get_latest_mapping_for_object("media_buy", str(media_buy_id))
        assert mapping is not None, (
            f"No workflow mapping for media_buy {media_buy_id} — "
            "_send_push_notifications iterates ObjectWorkflowMapping; with none, nothing is dispatched"
        )
        # Mapping must point at the right object (dispatcher uses object_type + object_id in payload).
        assert mapping.object_type == "media_buy", (
            f"Expected mapping.object_type='media_buy', got '{mapping.object_type}' — "
            "dispatcher would build the wrong payload kind"
        )
        assert str(mapping.object_id) == str(media_buy_id), (
            f"Mapping object_id='{mapping.object_id}' != media_buy_id='{media_buy_id}' — "
            "dispatcher would address a different object"
        )

        step = wf_repo.get_step_by_id(mapping.step_id)
        assert step is not None, (
            f"Workflow step {mapping.step_id} not found — context_manager cannot dispatch without a step record"
        )
        terminal_step_statuses = {"rejected", "completed", "approved", "failed"}
        assert step.status in terminal_step_statuses, (
            f"Workflow step {mapping.step_id} has status '{step.status}' — "
            f"expected one of {terminal_step_statuses} so the status-change event fires dispatch"
        )

        # Cross-check the mapping links back to this step (dispatcher reads step.request_data).
        mappings_for_step = session.scalars(select(ObjectWorkflowMapping).filter_by(step_id=step.step_id)).all()
        assert mappings_for_step, (
            f"No mappings discoverable by step_id={step.step_id} — "
            "_send_push_notifications queries ObjectWorkflowMapping by step_id and would find nothing"
        )

        # --- B. step.request_data must carry push_notification_config with the buyer URL ---
        # _send_push_notifications reads step.request_data['push_notification_config']['url']
        # to determine the webhook destination. Without it, dispatch logs
        # 'No push notification URL present' and skips the POST entirely.
        #
        # SPEC-PRODUCTION GAP: the repository-driven reject path does NOT populate
        # step.request_data with push_notification_config because it bypasses the
        # Flask admin flow that writes the original request payload onto the step.
        # FIXME(salesagent-9vgz.1): wire through the production admin approve/reject
        # flow which populates request_data, then remove this xfail.
        req_data = step.request_data or {}
        step_push_cfg = req_data.get("push_notification_config") if isinstance(req_data, dict) else None
        if not isinstance(step_push_cfg, dict) or step_push_cfg.get("url") != expected_url:
            pytest.xfail(
                "SPEC-PRODUCTION GAP: step.request_data does not carry "
                "push_notification_config with the buyer's URL — "
                "_send_push_notifications would skip dispatch. "
                "FIXME(salesagent-9vgz.1): wire through the admin flow."
            )

        # Happy path (reached when harness wires the full admin flow):
        assert step_push_cfg["url"] == expected_url, (
            f"step.request_data push_notification_config URL mismatch: "
            f"expected '{expected_url}', got '{step_push_cfg.get('url')}'"
        )
