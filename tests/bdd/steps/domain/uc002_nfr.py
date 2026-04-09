"""BDD step definitions for UC-002: Create Media Buy — NFR scenarios.

Covers non-functional requirements:
- nfr-001: Security hardening (auth, rate limiting, payload size)
- nfr-003: Audit logging (protocol, approval, adapter)
- nfr-004: Response latency SLA
- nfr-006: Minimum order size enforcement

beads: salesagent-9vgz.92
"""

from __future__ import annotations

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

    from src.core.database.models import CurrencyLimit
    from tests.bdd.steps._harness_db import db_session

    env = ctx["env"]
    env._commit_factory_data()

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx — 'the account exists and is active' must run first"

    with db_session(ctx) as session:
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
    """Assert authentication is the first gate before any business logic.

    Sends a SECOND request with invalid credentials (no principal_id) and
    verifies: (1) AdCPAuthenticationError is raised, and (2) no adapter
    calls were made — proving auth blocks before business logic side effects.
    """
    from src.core.exceptions import AdCPAuthenticationError
    from tests.factories.principal import PrincipalFactory

    env = ctx["env"]

    # First, verify the original request (with valid creds) succeeded
    resp = ctx.get("response")
    error = ctx.get("error")
    if error is not None:
        assert not isinstance(error, AdCPAuthenticationError), (
            f"Authentication failed despite valid credentials: {error}"
        )
    else:
        assert resp is not None, "Expected either a response or an error"

    # Now make a SECOND call with invalid credentials to prove ordering.
    # Build an identity with no principal_id — auth should reject before
    # any business logic (adapter, DB writes) executes.
    invalid_identity = PrincipalFactory.make_identity(
        principal_id=None,
        tenant_id=env._tenant_id,
    )

    # Reset adapter mock call count to detect any side effects
    mock_adapter = env.mock["adapter"].return_value
    mock_adapter.create_media_buy.reset_mock()

    # Build a valid request so we test auth, not Pydantic parsing
    from src.core.schemas import CreateMediaBuyRequest

    request_kwargs = ctx.get("request_kwargs", {})
    req = CreateMediaBuyRequest(**request_kwargs)

    # Call impl with invalid identity — expect auth error
    auth_error = None
    try:
        env.call_impl(req=req, identity=invalid_identity)
    except AdCPAuthenticationError as exc:
        auth_error = exc

    assert auth_error is not None, (
        "Expected AdCPAuthenticationError with no principal_id, but the request succeeded — auth is not the first gate"
    )
    assert "Principal ID not found" in str(auth_error), f"Expected 'Principal ID not found' error, got: {auth_error}"

    # Verify no business logic side effects occurred
    assert not mock_adapter.create_media_buy.called, (
        "Adapter.create_media_buy was called despite auth failure — business logic ran before authentication"
    )


@then("the system should enforce rate limiting on the endpoint")
def then_rate_limiting_enforced(ctx: dict) -> None:
    """Assert rate limiting is enforced on create_media_buy.

    Sends a rapid follow-up request and asserts it is rejected with
    AdCPRateLimitError. Production should reject when the threshold is
    exceeded, but no rate-limiting middleware exists yet.

    FIXME(salesagent-9vgz.92): Implement rate limiting middleware for create_media_buy.
    """
    import uuid
    from copy import deepcopy

    from src.core.exceptions import AdCPRateLimitError
    from src.core.schemas import CreateMediaBuyRequest

    # The original request already succeeded (from the When step).
    env = ctx["env"]
    resp = ctx.get("response")
    assert resp is not None, "Expected a successful response from the original request"

    # Make a rapid follow-up call to trigger rate limiting.
    rate_limit_hit = False
    request_kwargs = deepcopy(ctx.get("request_kwargs", {}))
    request_kwargs["buyer_ref"] = f"rate-limit-{uuid.uuid4().hex[:8]}"
    req = CreateMediaBuyRequest(**request_kwargs)
    try:
        env.call_impl(req=req)
    except AdCPRateLimitError:
        rate_limit_hit = True
    except Exception:
        # Other errors (duplicate key, etc.) are not rate limiting
        pass

    assert rate_limit_hit, (
        "SPEC-PRODUCTION GAP: Rate limiting not implemented. "
        "Sent a rapid follow-up request — not rejected with AdCPRateLimitError. "
        "AdCPRateLimitError class exists but is never raised. "
        "FIXME(salesagent-9vgz.92)"
    )


@then("the system should validate payload size limits")
def then_payload_size_limits(ctx: dict) -> None:
    """Assert payload size limits are enforced.

    Sends a request with an oversized buyer_ref payload and asserts it is
    rejected with a payload-too-large error. Production has no ASGI middleware
    that checks content-length or rejects oversized request bodies.

    FIXME(salesagent-9vgz.92): Implement payload size validation middleware.
    """
    import uuid
    from copy import deepcopy

    from src.core.schemas import CreateMediaBuyRequest

    env = ctx["env"]

    # Build a request with an oversized field to trigger payload validation.
    # A 1 MB buyer_ref string simulates an oversized body.
    request_kwargs = deepcopy(ctx.get("request_kwargs", {}))
    request_kwargs["buyer_ref"] = f"oversize-{uuid.uuid4().hex[:8]}-{'X' * (1024 * 1024)}"

    payload_rejected = False
    try:
        req = CreateMediaBuyRequest(**request_kwargs)
        env.call_impl(req=req)
    except Exception as exc:
        error_str = str(exc).lower()
        error_code = getattr(exc, "error_code", "")
        # Accept any payload-size-related rejection
        if (
            "payload" in error_str
            or "too large" in error_str
            or "content-length" in error_str
            or error_code == "PAYLOAD_TOO_LARGE"
        ):
            payload_rejected = True

    assert payload_rejected, (
        "SPEC-PRODUCTION GAP: Payload size validation not implemented. "
        "Sent a request with a 1 MB buyer_ref — not rejected for payload size. "
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
    """Assert the approval decision was logged with approval-specific content.

    Production logs approval decisions at two points:
    - Auto-approved (success path): log_operation(operation='create_media_buy',
      success=True, details={media_buy_id, total_budget, ...}) at line 3706.
    - Pending approval: log_operation(operation='create_media_buy_pending_approval',
      success=True, details={media_buy_id, workflow_step_id, ...}) at line 2212.

    This step verifies approval-SPECIFIC content that distinguishes it from the
    protocol audit entry (which checks only for operation name presence). The
    approval log must contain either:
    - 'create_media_buy_pending_approval' operation (explicitly approval), OR
    - 'create_media_buy' with success=True AND details containing 'media_buy_id'
      (the post-approval business activity entry, distinct from protocol activity).
    """
    env = ctx["env"]
    mock_audit = env.mock["audit"].return_value

    assert mock_audit.log_operation.called, (
        "Expected audit_logger.log_operation to be called for approval decision logging"
    )

    # Find a call with approval-specific content (not just operation name)
    approval_call = None
    for call in mock_audit.log_operation.call_args_list:
        op = call.kwargs.get("operation") or (call.args[0] if call.args else None)

        # Pending approval path: operation name is explicitly approval-related
        if op == "create_media_buy_pending_approval":
            approval_call = call
            break

        # Auto-approved path: success=True with details containing media_buy_id
        # This is the post-adapter audit entry at line 3706, NOT the protocol entry.
        if op == "create_media_buy":
            success = call.kwargs.get("success")
            details = call.kwargs.get("details") or {}
            if success is True and "media_buy_id" in details:
                approval_call = call
                break

    assert approval_call is not None, (
        f"Expected audit log entry with approval-specific content: either "
        f"operation='create_media_buy_pending_approval', or "
        f"operation='create_media_buy' with success=True and details.media_buy_id. "
        f"Got calls: {[c.kwargs for c in mock_audit.log_operation.call_args_list]}"
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

    Measures elapsed time of a call through the harness and asserts it
    completes within 15 seconds. In test harness (mock adapter), this
    always passes. The real SLA enforcement gap is tracked separately.

    FIXME(salesagent-9vgz.92): Implement SLA enforcement or monitoring alert.
    """
    import time
    import uuid
    from copy import deepcopy

    from src.core.schemas import CreateMediaBuyRequest

    env = ctx["env"]

    # Verify the original request succeeded
    resp = ctx.get("response")
    error = ctx.get("error")
    assert resp is not None and error is None, f"Expected a successful response to measure latency, got error: {error}"

    # Time a follow-up call to measure actual latency through the harness
    request_kwargs = deepcopy(ctx.get("request_kwargs", {}))
    request_kwargs["buyer_ref"] = f"sla-check-{uuid.uuid4().hex[:8]}"
    req = CreateMediaBuyRequest(**request_kwargs)

    start = time.monotonic()
    try:
        env.call_impl(req=req)
    except Exception:
        pass  # Even if the call fails, we measured latency
    elapsed = time.monotonic() - start

    sla_seconds = 15.0
    assert elapsed < sla_seconds, f"Response latency {elapsed:.2f}s exceeds {sla_seconds}s p95 SLA"


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — NFR-006: Minimum order size
# ═══════════════════════════════════════════════════════════════════════


@then("the system should validate budget against minimum order requirements")
def then_budget_validated_against_min_order(ctx: dict) -> None:
    """Assert budget enforcement works by testing the rejection path.

    The happy path (budget >= minimum, original request succeeded) is
    tautological — it only proves the budget was already adequate.
    True enforcement verification requires a budget BELOW the minimum
    triggering a specific rejection.

    This step:
    1. Verifies the original request succeeded (budget was adequate).
    2. Makes a SECOND call with budget below min_package_budget.
    3. Asserts the rejection contains "minimum spend" — proving the
       enforcement mechanism actually fires.
    """
    from copy import deepcopy
    from decimal import Decimal

    min_budget = ctx.get("min_package_budget")
    assert min_budget is not None, (
        "min_package_budget not in ctx — 'the tenant has minimum order size requirements' Given step must run first"
    )

    # Step 1: Original request should have succeeded (budget >= min)
    resp = ctx.get("response")
    error = ctx.get("error")
    assert resp is not None and error is None, (
        f"Expected the original request to succeed (budget >= min_package_budget), but got error: {error}"
    )

    # Step 2: Make a second call with budget below minimum to test enforcement
    import uuid

    from src.core.schemas import CreateMediaBuyRequest

    env = ctx["env"]
    request_kwargs = deepcopy(ctx.get("request_kwargs", {}))

    # Generate a unique buyer_ref to avoid duplicate-key rejection
    request_kwargs["buyer_ref"] = f"nfr-budget-{uuid.uuid4().hex[:8]}"

    # Set each package budget to 1 cent below the minimum
    below_min = float(Decimal(str(min_budget)) - Decimal("0.01"))
    if "packages" in request_kwargs:
        for pkg in request_kwargs["packages"]:
            pkg["budget"] = below_min

    low_budget_req = CreateMediaBuyRequest(**request_kwargs)

    low_budget_error = None
    try:
        result = env.call_impl(req=low_budget_req)
        # Check if the result wraps an error response
        if hasattr(result, "response") and hasattr(result.response, "errors") and result.response.errors:
            low_budget_error = result.response.errors[0]
    except Exception as exc:
        low_budget_error = exc

    # Step 3: Assert the specific minimum spend rejection
    assert low_budget_error is not None, (
        f"Expected rejection for budget {below_min} below min_package_budget {min_budget}, but the request succeeded"
    )
    error_str = str(low_budget_error)
    error_code = getattr(low_budget_error, "code", "")
    assert "minimum spend" in error_str.lower() or error_code == "BUDGET_TOO_LOW", (
        f"Expected minimum spend rejection (message containing 'minimum spend' "
        f"or code 'BUDGET_TOO_LOW'), got: code={error_code!r}, message={error_str!r}"
    )
