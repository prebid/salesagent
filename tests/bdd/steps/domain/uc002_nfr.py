"""BDD step definitions for UC-002: Create Media Buy — NFR scenarios.

Covers non-functional requirements:
- nfr-001: Security hardening (auth, rate limiting, payload size)
- nfr-003: Audit logging (protocol, approval, adapter)
- nfr-004: Response latency SLA
- nfr-006: Minimum order size enforcement

beads: salesagent-9vgz.92
"""

from __future__ import annotations

import uuid

from pytest_bdd import given, then

from tests.bdd.steps.generic._dispatch import dispatch_request

# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — NFR preconditions
# ═══════════════════════════════════════════════════════════════════════


@given("the tenant has minimum order size requirements")
def given_tenant_has_min_order(ctx: dict) -> None:
    """Assert the tenant's CurrencyLimit has min_package_budget configured.

    TenantFactory auto-creates CurrencyLimit with min_package_budget=100.00,
    so this step verifies the precondition rather than creating new data.
    """
    env = ctx["env"]
    env._commit_factory_data()

    cl = env.get_currency_limit("USD")
    assert cl is not None, f"No CurrencyLimit(USD) for tenant {env._tenant_id} — TenantFactory should auto-create one"
    assert cl.min_package_budget is not None and cl.min_package_budget > 0, (
        f"CurrencyLimit.min_package_budget is {cl.min_package_budget} — expected a positive minimum order size"
    )
    ctx["min_package_budget"] = cl.min_package_budget


@given("the package budget is below the minimum")
@given("But the package budget is below the minimum")
def given_budget_below_minimum(ctx: dict) -> None:
    """Set each package budget to 1 cent below min_package_budget.

    Requires 'the tenant has minimum order size requirements' to run first
    (sets ctx["min_package_budget"]).
    """
    from decimal import Decimal

    min_budget = ctx.get("min_package_budget")
    assert min_budget is not None, (
        "min_package_budget not in ctx — 'the tenant has minimum order size requirements' Given step must run first"
    )
    below_min = float(Decimal(str(min_budget)) - Decimal("0.01"))
    kwargs = ctx.get("request_kwargs", {})
    for pkg in kwargs.get("packages", []):
        pkg["budget"] = below_min


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — NFR enforcement (restructured scenarios)
# ═══════════════════════════════════════════════════════════════════════


@then("the operation should fail with authentication error")
def then_fail_with_auth_error(ctx: dict) -> None:
    """Assert the operation failed with an authentication error.

    Accepts AdCPAuthenticationError (in-process auth rejection) or
    TypeError (E2E: null auth token can't be serialized to HTTP header).
    Both prove the request never reached business logic.
    """
    from src.core.exceptions import AdCPAuthenticationError

    error = ctx.get("error")
    assert error is not None, (
        "Expected an authentication error but no error was recorded — the request succeeded despite invalid credentials"
    )
    is_auth_error = isinstance(error, AdCPAuthenticationError)
    is_null_token_error = isinstance(error, TypeError) and "Header value" in str(error)
    assert is_auth_error or is_null_token_error, (
        f"Expected AdCPAuthenticationError (or null-token TypeError in E2E), got {type(error).__name__}: {error}"
    )


@then("no adapter calls should have been made")
def then_no_adapter_calls(ctx: dict) -> None:
    """Assert the adapter was never called — proving auth blocked before business logic."""
    env = ctx["env"]
    mock_adapter = env.mock["adapter"].return_value
    assert not mock_adapter.create_media_buy.called, (
        "Adapter.create_media_buy was called despite auth failure — business logic ran before authentication"
    )


@then("the error should indicate minimum spend requirement")
def then_error_minimum_spend(ctx: dict) -> None:
    """Assert the error message mentions minimum spend enforcement."""
    error = ctx.get("error")
    resp = ctx.get("response")

    error_str = ""
    if error is not None:
        error_str = str(error).lower()
    elif resp is not None:
        # Some transports wrap errors in the response
        from tests.bdd.steps.generic.then_media_buy import _get_response_field

        msg = _get_response_field(resp, "message") or _get_response_field(resp, "error") or ""
        error_str = str(msg).lower()

    assert "minimum" in error_str or "min" in error_str or "spend" in error_str, (
        f"Expected error to indicate minimum spend requirement, got: {error or resp}"
    )


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — NFR-001: Security hardening (legacy — dispatch-in-Then)
# ═══════════════════════════════════════════════════════════════════════


@then("the system should validate authentication before any business logic")
def then_auth_before_business_logic(ctx: dict) -> None:
    """Assert authentication is the first gate before any business logic.

    Sends a SECOND request with invalid credentials (no principal_id) THROUGH
    THE WIRE (the parametrized transport) and verifies: (1) the wire envelope
    carries the AUTH_REQUIRED error code, and (2) no adapter calls were made —
    proving auth blocks before business logic side effects.

    Per the Error Verification Policy (tests/CLAUDE.md), this asserts on the
    wire envelope, not a reconstructed exception. The auth error code is
    AUTH_REQUIRED on the wire (a recent reversal flipped AUTH_TOKEN_INVALID ->
    AUTH_REQUIRED); the "Principal ID not found" message still holds.
    """
    from src.core.exceptions import AdCPAuthenticationError
    from src.core.schemas import CreateMediaBuyRequest
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
    request_kwargs = ctx.get("request_kwargs", {})
    req = CreateMediaBuyRequest(**request_kwargs)

    # Dispatch through the wire with the invalid identity — the parametrized
    # transport (a2a/mcp/rest) actually exercises the auth gate on the wire.
    auth_ctx: dict = {k: ctx[k] for k in ("env", "transport", "e2e_config") if k in ctx}
    dispatch_request(auth_ctx, req=req, identity=invalid_identity)

    result = auth_ctx.get("result")
    assert result is not None, "dispatch_request did not produce a TransportResult for the invalid-identity request"
    # recovery omitted -> defaults to the pinned AUTH_REQUIRED enum (correctable). Do not
    # pass an explicit recovery= that shadows the pinned enum (#1417: superseded
    # the earlier terminal override; the pinned enum is the single source of truth).
    result.assert_wire_error("AUTH_REQUIRED", message_substr="Principal ID not found")

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
    from copy import deepcopy

    from src.core.schemas import CreateMediaBuyRequest

    # The original request already succeeded (from the When step).
    resp = ctx.get("response")
    assert resp is not None, "Expected a successful response from the original request"

    # Make a rapid follow-up call THROUGH THE WIRE to trigger rate limiting.
    # The follow-up needs a FRESH idempotency_key — reusing the original's
    # would replay the cached success instead of exercising a second real
    # request. Dispatching through the parametrized transport means a
    # rate-limit gate (when implemented) would surface as a RATE_LIMITED wire
    # envelope on a2a/mcp/rest.
    request_kwargs = deepcopy(ctx.get("request_kwargs", {}))
    request_kwargs["idempotency_key"] = f"bdd-key-{uuid.uuid4().hex}"
    req = CreateMediaBuyRequest(**request_kwargs)

    rate_ctx: dict = {k: ctx[k] for k in ("env", "transport", "e2e_config") if k in ctx}
    dispatch_request(rate_ctx, req=req)

    # Gap preserved: production never emits RATE_LIMITED, so the follow-up
    # succeeds (or fails for an unrelated reason) and no RATE_LIMITED wire
    # envelope is produced. This assertion fails — proving the gap — exactly
    # as the call_impl version did.
    result = rate_ctx.get("result")
    envelope = result.wire_error_envelope if result is not None else None
    rate_limit_hit = bool(envelope) and envelope.get("errors", [{}])[0].get("code") == "RATE_LIMITED"

    assert rate_limit_hit, (
        "SPEC-PRODUCTION GAP: Rate limiting not implemented. "
        "Sent a rapid follow-up request through the wire — not rejected with a RATE_LIMITED envelope. "
        "AdCPRateLimitError class exists but is never raised. "
        "FIXME(salesagent-9vgz.92)"
    )


@then("the system should validate payload size limits")
def then_payload_size_limits(ctx: dict) -> None:
    """Assert payload size limits are enforced.

    Sends a request with an oversized order_name payload and asserts it is
    rejected with a payload-too-large error. Production has no ASGI middleware
    that checks content-length or rejects oversized request bodies.

    FIXME(salesagent-9vgz.92): Implement payload size validation middleware.

    Note: PAYLOAD_TOO_LARGE is not a canonical AdCP error code in the pinned
    enum, so assert_wire_error() cannot be used here — the assertion inspects
    the raw wire envelope (and any dispatch error) for a payload-size rejection.
    """
    import uuid
    from copy import deepcopy

    from src.core.schemas import CreateMediaBuyRequest

    # Build a request with an oversized field to trigger payload validation.
    # A 1 MB order_name string simulates an oversized body. Dispatching through
    # the parametrized transport means a content-length / payload-size gate
    # (when implemented) would surface as a wire rejection on a2a/mcp/rest.
    request_kwargs = deepcopy(ctx.get("request_kwargs", {}))
    request_kwargs["order_name"] = f"oversize-{uuid.uuid4().hex[:8]}-{'X' * (1024 * 1024)}"
    req = CreateMediaBuyRequest(**request_kwargs)

    payload_ctx: dict = {k: ctx[k] for k in ("env", "transport", "e2e_config") if k in ctx}
    dispatch_request(payload_ctx, req=req)

    # Gap preserved: no ASGI middleware checks content-length, so the oversized
    # body is accepted and no payload-size rejection appears on the wire (nor in
    # any dispatch error). Inspect both the wire envelope and a transport error.
    payload_rejected = False
    result = payload_ctx.get("result")
    envelope = result.wire_error_envelope if result is not None else None
    if envelope:
        code = envelope.get("errors", [{}])[0].get("code", "")
        msg = (envelope.get("errors", [{}])[0].get("message") or "").lower()
        if code == "PAYLOAD_TOO_LARGE" or "payload" in msg or "too large" in msg or "content-length" in msg:
            payload_rejected = True
    dispatch_error = payload_ctx.get("error")
    if dispatch_error is not None:
        error_str = str(dispatch_error).lower()
        error_code = getattr(dispatch_error, "error_code", "")
        if (
            "payload" in error_str
            or "too large" in error_str
            or "content-length" in error_str
            or error_code == "PAYLOAD_TOO_LARGE"
        ):
            payload_rejected = True

    assert payload_rejected, (
        "SPEC-PRODUCTION GAP: Payload size validation not implemented. "
        "Sent a request with a 1 MB order_name through the wire — not rejected for payload size. "
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
    at line 3603 of media_buy_create.py. Transport-aware: in-process checks
    mock, E2E checks audit_logs table.
    """
    from tests.bdd.steps._outcome_helpers import assert_audit_logged

    assert_audit_logged(ctx, operation_substring="create_media_buy")


@then("the approval decision should be logged")
def then_approval_logged(ctx: dict) -> None:
    """Assert the approval decision was logged with approval-specific content.

    Transport-aware: in-process checks mock for approval-specific content,
    E2E checks audit_logs table.
    """
    from tests.bdd.steps._outcome_helpers import assert_audit_approval_logged

    assert_audit_approval_logged(ctx)


@then("the adapter execution should be logged")
def then_adapter_execution_logged(ctx: dict) -> None:
    """Assert the adapter execution was logged via audit_logger.

    Transport-aware: in-process checks mock for success=True with details,
    E2E checks audit_logs table.
    """
    from tests.bdd.steps._outcome_helpers import assert_audit_adapter_logged

    assert_audit_adapter_logged(ctx)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — NFR-004: Response latency
# ═══════════════════════════════════════════════════════════════════════


@then("the response should be returned within 15 seconds (p95)")
def then_response_within_sla(ctx: dict) -> None:
    """Assert the request-path architecture supports the 15s p95 SLA.

    A single-call BDD harness cannot measure p95 latency. The controllable
    latency risk is synchronous adapter I/O (GAM SOAP, etc.) blocking the
    request thread. This step asserts:
    1. The request completed successfully (full pipeline ran).
    2. The adapter was not called synchronously on the request thread
       (the actual SLA risk). Currently xfailed because production DOES
       call adapter I/O in-line.

    True p95 enforcement belongs in production monitoring/alerting.

    FIXME(salesagent-9vgz.92): Move adapter I/O to background workers
    so latency SLA is enforceable at the application layer.
    """
    import pytest

    from src.core.schemas._base import CreateMediaBuySuccess

    env = ctx["env"]

    # --- Part 1: Verify the original request completed successfully ---
    error = ctx.get("error")
    assert error is None, f"Expected a successful response to verify SLA, got error: {error}"
    result = ctx.get("response")
    assert result is not None, "No response recorded — the request did not complete"
    assert result.status == "success", f"Expected status='success' (full pipeline completed), got '{result.status}'"
    assert isinstance(result.response, CreateMediaBuySuccess), (
        f"Expected CreateMediaBuySuccess, got {type(result.response).__name__}"
    )

    # Production-computed: media_buy_id proves the pipeline completed end-to-end
    assert result.response.media_buy_id, (
        "media_buy_id is empty — pipeline did not complete (fast error is not SLA compliance)"
    )
    # Production-computed: packages prove adapter + persistence completed
    assert result.response.packages, "packages list is empty — adapter/persistence did not complete"

    # --- Part 2: Assert no synchronous adapter I/O on request thread ---
    # The controllable latency risk for p95 SLA is synchronous external
    # calls (GAM SOAP, Kevel API) blocking the request thread. When
    # adapters run in background workers, the adapter mock should NOT be
    # called during the request cycle.
    mock_adapter = env.mock["adapter"].return_value
    adapter_called_sync = mock_adapter.create_media_buy.called

    if adapter_called_sync:
        pytest.xfail(
            "SPEC-PRODUCTION GAP: Adapter I/O runs synchronously on the "
            "request thread. Architecture direction is to move adapter calls "
            "to background workers and return 201 pending. Until then, p95 "
            "SLA is not enforceable at the application layer. "
            "FIXME(salesagent-9vgz.92)"
        )

    assert not adapter_called_sync, (
        "Adapter.create_media_buy was called on the request thread — "
        "synchronous adapter I/O is the primary latency risk for SLA compliance"
    )


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
    2. Makes a SECOND call with budget below min_package_budget THROUGH THE WIRE.
    3. Asserts the wire rejection contains "minimum spend" (or code
       BUDGET_EXCEEDED) — proving the enforcement mechanism actually fires.

    Dispatches the low-budget request through the parametrized transport
    (a2a/mcp/rest), so budget enforcement is verified on the wire — not on a
    reconstructed exception (tests/CLAUDE.md Error Verification Policy).
    """
    from copy import deepcopy
    from decimal import Decimal

    from src.core.schemas import CreateMediaBuyRequest

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

    # Step 2: Build a second request with budget below minimum to test enforcement
    request_kwargs = deepcopy(ctx.get("request_kwargs", {}))
    below_min = float(Decimal(str(min_budget)) - Decimal("0.01"))
    if "packages" in request_kwargs:
        for pkg in request_kwargs["packages"]:
            pkg["budget"] = below_min

    low_budget_req = CreateMediaBuyRequest(**request_kwargs)

    # Dispatch the second request through the wire (transport-independent).
    low_budget_ctx: dict = {k: ctx[k] for k in ("env", "transport", "e2e_config") if k in ctx}
    dispatch_request(low_budget_ctx, req=low_budget_req)

    # Step 3: Assert the specific minimum spend rejection on the wire envelope.
    result = low_budget_ctx.get("result")
    assert result is not None, "dispatch_request did not produce a TransportResult for the low-budget request"
    envelope = result.wire_error_envelope
    assert envelope is not None, (
        f"Expected a wire rejection for budget {below_min} below min_package_budget {min_budget}, "
        f"but no wire_error_envelope was captured (is_error={result.is_error}, payload={result.payload!r})"
    )
    first_error = envelope.get("errors", [{}])[0]
    error_code = first_error.get("code", "")
    error_msg = (first_error.get("message") or "").lower()
    assert "minimum spend" in error_msg or error_code == "BUDGET_EXCEEDED", (
        f"Expected minimum spend rejection (wire message containing 'minimum spend' "
        f"or code 'BUDGET_EXCEEDED'), got: code={error_code!r}, message={first_error.get('message')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════
# nfr-highvalue: >$10k high-value Seller alert (#1417)
# ═══════════════════════════════════════════════════════════════════════


@given("the Seller observes high-value audit alerts")
def given_observe_high_value_alerts(ctx: dict) -> None:
    """Make the >$10k high-value audit alert observable at the Seller boundary.

    The high-value alert fires inside the REAL AuditLogger (notify_audit_log),
    which the create harness no-ops by default. This swaps in the real audit
    logger so the budget gate actually runs, and spies its external Slack
    boundary (the same Slack-boundary idiom as 'a Slack notification should be
    sent to the Seller', which asserts notify_media_buy_event). The spy is
    registered on the env's patcher list so it is torn down on env __exit__.

    In-process only: e2e_rest dispatches create in the Docker process, out of
    this spy's reach — the Then step branches on is_e2e.
    """
    from tests.bdd.steps._outcome_helpers import is_e2e

    if is_e2e(ctx):
        return

    from unittest import mock

    from src.core.audit_logger import get_audit_logger

    env = ctx["env"]
    # Real audit logger -> the high-value notification gate executes for real.
    env.mock["audit"].side_effect = get_audit_logger
    # Spy the audit logger's external Slack boundary; tie teardown to env lifecycle.
    patcher = mock.patch("src.services.slack_notifier.get_slack_notifier")
    notifier_factory = patcher.start()
    env._patchers.append(patcher)
    notifier = mock.MagicMock()
    notifier_factory.return_value = notifier
    env.mock["audit_slack"] = notifier_factory
    ctx["high_value_alert_notifier"] = notifier


@then("a high-value alert should be sent to the Seller")
def then_high_value_alert_sent(ctx: dict) -> None:
    """Assert the >$10k pending-approval high-value alert fired to the Seller.

    Filtering by the create_media_buy_pending_approval operation isolates the
    high-value budget gate from audit_logger's sensitive_ops allowlist (the
    auto-approve op create_media_buy would notify regardless) and confirms the
    create took the pending path.
    """
    import pytest

    from tests.bdd.steps._outcome_helpers import is_e2e

    if is_e2e(ctx):
        # The high-value alert is a seller-internal side-effect observed via the
        # in-process Slack spy. On e2e_rest the create runs in the Docker app
        # (out of the spy's reach) and MediaBuyCreateEnv cannot query its
        # audit_logs, so this behavior is not observable through the e2e wire.
        pytest.skip("high-value Slack alert is observed in-process; not observable on e2e_rest")

    notifier = ctx.get("high_value_alert_notifier")
    assert notifier is not None, (
        "high-value alert observation not armed — the "
        "'the Seller observes high-value audit alerts' Given must run before this Then"
    )
    high_value_calls = [
        call
        for call in notifier.notify_audit_log.call_args_list
        if call.kwargs.get("operation") == "create_media_buy_pending_approval"
    ]
    assert len(high_value_calls) == 1, (
        "the >$10k pending-approval high-value Seller alert did not fire "
        f"(notify_audit_log calls: {notifier.notify_audit_log.call_args_list})"
    )
