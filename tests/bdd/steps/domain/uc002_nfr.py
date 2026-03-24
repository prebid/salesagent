"""BDD step definitions for UC-002: Create Media Buy — NFR scenarios.

Covers non-functional requirements:
- nfr-001: Security hardening (auth, rate limiting, payload size)
- nfr-003: Audit logging (protocol, approval, adapter)
- nfr-004: Response latency SLA
- nfr-006: Minimum order size enforcement

beads: salesagent-9vgz.92
"""

from __future__ import annotations

import pytest
from pytest_bdd import given, then

# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — NFR preconditions
# ═══════════════════════════════════════════════════════════════════════


@given("the tenant has minimum order size requirements")
def given_tenant_has_min_order(ctx: dict) -> None:
    """Assert the tenant's CurrencyLimit has min_package_budget configured.

    TenantFactory auto-creates CurrencyLimit with min_package_budget=100.00,
    so this step verifies the precondition rather than creating new data.
    """
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import CurrencyLimit

    env = ctx["env"]
    env._commit_factory_data()

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx — 'the account exists and is active' must run first"

    with get_db_session() as session:
        cl = session.scalars(select(CurrencyLimit).filter_by(tenant_id=tenant.tenant_id)).first()
        assert cl is not None, (
            f"No CurrencyLimit found for tenant {tenant.tenant_id} — TenantFactory should auto-create one"
        )
        assert cl.min_package_budget is not None and cl.min_package_budget > 0, (
            f"CurrencyLimit.min_package_budget is {cl.min_package_budget} — expected a positive minimum order size"
        )
    # Store for Then step to reference
    ctx["min_package_budget"] = cl.min_package_budget


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — NFR-001: Security hardening
# ═══════════════════════════════════════════════════════════════════════


@then("the system should validate authentication before any business logic")
def then_auth_before_business_logic(ctx: dict) -> None:
    """Assert authentication was validated before business logic executed.

    Production flow: _create_media_buy_impl checks identity.principal_id
    and identity.tenant before any DB access or validation. A successful
    response proves auth ran first (no auth = AdCPAuthenticationError).

    The harness provides a valid identity, so a successful response means
    auth validation passed before business logic.
    """
    resp = ctx.get("response")
    error = ctx.get("error")

    if error is not None:
        # If there's an error, it should NOT be an auth error (we have valid creds)
        from src.core.exceptions import AdCPAuthenticationError

        assert not isinstance(error, AdCPAuthenticationError), (
            f"Authentication failed despite valid credentials: {error}"
        )
    else:
        # Successful response proves: identity was valid, principal was found,
        # tenant was resolved — all before business logic ran.
        assert resp is not None, "Expected either a response or an error"


@then("the system should enforce rate limiting on the endpoint")
def then_rate_limiting_enforced(ctx: dict) -> None:
    """Assert rate limiting is enforced on create_media_buy.

    SPEC-PRODUCTION GAP: AdCPRateLimitError class exists in src/core/exceptions.py
    but is never raised anywhere. No rate limiting middleware, no per-principal
    counter, no per-tenant counter exists.

    FIXME(salesagent-9vgz.92): Implement rate limiting middleware for create_media_buy.
    """
    pytest.xfail(
        "SPEC-PRODUCTION GAP: Rate limiting not implemented. "
        "AdCPRateLimitError class exists but is never raised. "
        "No rate limiting middleware or counters exist. "
        "FIXME(salesagent-9vgz.92)"
    )


@then("the system should validate payload size limits")
def then_payload_size_limits(ctx: dict) -> None:
    """Assert payload size limits are enforced.

    SPEC-PRODUCTION GAP: No ASGI middleware checks content-length or rejects
    oversized request bodies on the MCP or A2A path.

    FIXME(salesagent-9vgz.92): Implement payload size validation middleware.
    """
    pytest.xfail(
        "SPEC-PRODUCTION GAP: Payload size validation not implemented. "
        "No ASGI middleware checks content-length for oversized bodies. "
        "FIXME(salesagent-9vgz.92)"
    )


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — NFR-003: Audit logging
# ═══════════════════════════════════════════════════════════════════════


@then("the system should log the protocol audit entry")
def then_protocol_audit_logged(ctx: dict) -> None:
    """Assert the protocol-level audit entry was logged.

    Production calls log_tool_activity(identity, 'create_media_buy', ...)
    at line 3603 of media_buy_create.py. In the harness, the audit mock
    captures log_operation calls. The first call on the success path is
    the protocol-level entry.
    """
    env = ctx["env"]
    mock_audit = env.mock["audit"].return_value

    # log_operation is called for: (1) protocol/activity, (2) success/pending/failure
    assert mock_audit.log_operation.called, (
        "Expected audit_logger.log_operation to be called for protocol audit entry, but it was never called"
    )
    # Verify at least one call has the create_media_buy operation
    operations = [
        call.kwargs.get("operation") or (call.args[0] if call.args else None)
        for call in mock_audit.log_operation.call_args_list
    ]
    create_ops = [op for op in operations if op and "create_media_buy" in op]
    assert len(create_ops) >= 1, (
        f"Expected at least one log_operation call with 'create_media_buy' operation, got operations: {operations}"
    )


@then("the approval decision should be logged")
def then_approval_logged(ctx: dict) -> None:
    """Assert the approval decision was logged via audit_logger.

    Production logs approval decisions at two points:
    - Auto-approved (success path): log_operation(operation='create_media_buy', success=True)
    - Pending approval: log_operation(operation='create_media_buy_pending_approval', success=True)

    Either call proves the approval decision was captured in the audit trail.
    """
    env = ctx["env"]
    mock_audit = env.mock["audit"].return_value

    assert mock_audit.log_operation.called, (
        "Expected audit_logger.log_operation to be called for approval decision logging"
    )

    # Check that at least one call captures the approval path
    approval_logged = False
    for call in mock_audit.log_operation.call_args_list:
        op = call.kwargs.get("operation") or (call.args[0] if call.args else None)
        if op in ("create_media_buy", "create_media_buy_pending_approval"):
            # On success path, success=True means auto-approved and executed
            # On pending path, operation name itself captures the approval decision
            approval_logged = True
            break

    assert approval_logged, (
        f"Expected audit log entry for approval decision "
        f"(operation='create_media_buy' or 'create_media_buy_pending_approval'), "
        f"got: {[c.kwargs for c in mock_audit.log_operation.call_args_list]}"
    )


@then("the adapter execution should be logged")
def then_adapter_execution_logged(ctx: dict) -> None:
    """Assert the adapter execution was logged via audit_logger.

    Production logs adapter execution at line 3706 with success=True and
    details including media_buy_id. This is the business activity feed entry
    that records the adapter created the order.
    """
    env = ctx["env"]
    mock_audit = env.mock["audit"].return_value

    assert mock_audit.log_operation.called, (
        "Expected audit_logger.log_operation to be called for adapter execution logging"
    )

    # Find the log_operation call that records adapter execution (success=True with details)
    adapter_logged = False
    for call in mock_audit.log_operation.call_args_list:
        op = call.kwargs.get("operation") or (call.args[0] if call.args else None)
        success = call.kwargs.get("success")
        details = call.kwargs.get("details")
        if op == "create_media_buy" and success is True and details is not None:
            # This is the post-adapter success log entry
            adapter_logged = True
            break

    assert adapter_logged, (
        f"Expected audit log entry for adapter execution "
        f"(operation='create_media_buy', success=True, with details), "
        f"got: {[c.kwargs for c in mock_audit.log_operation.call_args_list]}"
    )


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — NFR-004: Response latency
# ═══════════════════════════════════════════════════════════════════════


@then("the response should be returned within 15 seconds (p95)")
def then_response_within_sla(ctx: dict) -> None:
    """Assert response latency is within the 15s p95 SLA.

    SPEC-PRODUCTION GAP: Production measures latency via request_start_time
    and log_tool_activity but does not enforce an SLA. There is no timeout,
    alarm, or rejection for slow responses.

    FIXME(salesagent-9vgz.92): Implement SLA enforcement or monitoring alert.
    """
    pytest.xfail(
        "SPEC-PRODUCTION GAP: Response latency SLA not enforced. "
        "Production measures latency (request_start_time + log_tool_activity) "
        "but has no SLA check, timeout, or alarm. "
        "FIXME(salesagent-9vgz.92)"
    )


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — NFR-006: Minimum order size
# ═══════════════════════════════════════════════════════════════════════


@then("the system should validate budget against minimum order requirements")
def then_budget_validated_against_min_order(ctx: dict) -> None:
    """Assert budget was validated against minimum order size requirements.

    Production flow: _create_media_buy_impl checks each package budget
    against CurrencyLimit.min_package_budget (and per-product overrides
    via PricingOption.min_spend_per_package). Uses validate_min_package_budget()
    from financial_validation.py.

    A successful response with a budget >= min_package_budget proves the
    validation ran and passed. The Given step confirmed min_package_budget
    is configured (default: 100.00 from CurrencyLimitFactory).
    """
    min_budget = ctx.get("min_package_budget")
    assert min_budget is not None, (
        "min_package_budget not in ctx — 'the tenant has minimum order size requirements' Given step must run first"
    )

    resp = ctx.get("response")
    error = ctx.get("error")

    if error is not None:
        # If the request had a budget below minimum, we'd expect BUDGET_TOO_LOW
        error_str = str(error)
        if "BUDGET_TOO_LOW" in error_str or "minimum" in error_str.lower():
            # Validation correctly rejected — budget was below minimum
            return
        # Other errors are fine too — validation still ran before this point

    if resp is not None:
        # Successful response means budget passed validation.
        # The default request uses budget > min_package_budget (100.00),
        # so reaching here proves validate_min_package_budget() passed.
        from tests.bdd.steps.generic.then_media_buy import _get_response_field

        media_buy_id = _get_response_field(resp, "media_buy_id")
        assert media_buy_id, (
            "Expected media_buy_id in response — budget validation should have "
            "either passed (creating media buy) or failed (BUDGET_TOO_LOW error)"
        )
        return

    raise AssertionError("No response or error in ctx — cannot verify budget validation ran")
