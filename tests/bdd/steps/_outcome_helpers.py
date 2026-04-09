"""Outcome-based assertion helpers for E2E transport compatibility.

These helpers verify outcomes through the harness (which uses repositories
and the correctly-bound DB session), making assertions work across all
transports including E2E.

No raw session access. No db_session(ctx). The harness owns the session,
the repository owns the query, the helper owns the assertion.
"""

from __future__ import annotations


def _get_response_field(resp: object, field: str) -> object:
    """Extract a field from a response, handling wrapper types."""
    if hasattr(resp, field):
        return getattr(resp, field)
    inner = getattr(resp, "response", None)
    if inner is not None and hasattr(inner, field):
        return getattr(inner, field)
    if isinstance(resp, dict):
        return resp.get(field)
    return None


def is_e2e(ctx: dict) -> bool:
    """Check if the current transport is E2E (Docker-based)."""
    transport = ctx.get("transport")
    return transport is not None and hasattr(transport, "value") and str(transport.value).startswith("e2e_")


def assert_media_buy_created(ctx: dict, media_buy_id: str | None = None) -> object:
    """Verify media buy exists in DB through the harness.

    Returns the MediaBuy ORM instance for further assertions.
    """
    env = ctx["env"]

    if media_buy_id is None:
        resp = ctx.get("response")
        if resp is not None:
            media_buy_id = _get_response_field(resp, "media_buy_id")

    assert media_buy_id is not None, "No media_buy_id available to verify creation"

    mb = env.get_media_buy(media_buy_id)
    return mb


def assert_adapter_executed(ctx: dict) -> object:
    """Verify adapter ran by checking DB state through the harness.

    A media buy that reaches a non-draft status proves the adapter was invoked.
    """
    mb = assert_media_buy_created(ctx)
    executed_statuses = ("active", "completed", "pending_approval", "pending_activation", "submitted")
    assert mb.status in executed_statuses, (
        f"Media buy status '{mb.status}' does not confirm adapter execution. Expected one of {executed_statuses}."
    )
    return mb


def assert_audit_logged(ctx: dict, *, operation_substring: str = "create_media_buy") -> None:
    """Verify audit logging occurred — transport-aware.

    In-process: asserts on mock audit logger calls (fast, precise).
    E2E: queries audit_logs through the harness.
    """
    if is_e2e(ctx):
        env = ctx["env"]
        logs = env.get_audit_logs(operation_substring)
        assert logs, (
            f"Expected audit_logs entry containing '{operation_substring}' "
            f"for tenant {env._tenant_id}"
        )
    else:
        _assert_audit_logged_mock(ctx, operation_substring)


def _assert_audit_logged_mock(ctx: dict, operation_substring: str) -> None:
    """Assert audit logger mock was called with the operation (in-process mode)."""
    env = ctx["env"]
    mock_audit = env.mock["audit"].return_value
    assert mock_audit.log_operation.called, (
        f"Expected audit_logger.log_operation to be called with '{operation_substring}', but it was never called"
    )
    operations = [
        call.kwargs.get("operation") or (call.args[0] if call.args else None)
        for call in mock_audit.log_operation.call_args_list
    ]
    matching = [op for op in operations if op and operation_substring in op]
    assert matching, (
        f"Expected at least one log_operation call containing '{operation_substring}', got operations: {operations}"
    )


def assert_audit_approval_logged(ctx: dict) -> None:
    """Verify approval decision was logged — transport-aware."""
    if is_e2e(ctx):
        env = ctx["env"]
        logs = env.get_audit_logs()
        found = any("pending_approval" in (log.operation or "") for log in logs) or any(
            "create_media_buy" in (log.operation or "") and log.success is True
            for log in logs
        )
        assert found, (
            f"Expected audit entry for approval decision, "
            f"found: {[(log.operation, log.success) for log in logs]}"
        )
    else:
        _assert_audit_approval_mock(ctx)


def _assert_audit_approval_mock(ctx: dict) -> None:
    """Assert approval-specific audit log call exists (in-process mode)."""
    env = ctx["env"]
    mock_audit = env.mock["audit"].return_value
    assert mock_audit.log_operation.called, (
        "Expected audit_logger.log_operation to be called for approval decision logging"
    )
    for call in mock_audit.log_operation.call_args_list:
        op = call.kwargs.get("operation") or (call.args[0] if call.args else None)
        if op == "create_media_buy_pending_approval":
            return
        if op == "create_media_buy":
            success = call.kwargs.get("success")
            details = call.kwargs.get("details") or {}
            if success is True and "media_buy_id" in details:
                return
    raise AssertionError(
        f"Expected audit log entry with approval-specific content, "
        f"got calls: {[c.kwargs for c in mock_audit.log_operation.call_args_list]}"
    )


def assert_audit_adapter_logged(ctx: dict) -> None:
    """Verify adapter execution was logged — transport-aware.

    If the media buy went to pending_approval, the adapter was not called —
    that's correct behavior (no adapter audit log expected).
    """
    if is_e2e(ctx):
        env = ctx["env"]
        logs = env.get_audit_logs()
        for log in logs:
            op = log.operation or ""
            if "create_media_buy" in op and log.success is True and log.details is not None:
                return
            if "pending_approval" in op:
                return
        raise AssertionError(
            f"Expected audit entry for adapter execution or pending_approval, "
            f"found: {[(log.operation, log.success) for log in logs]}"
        )
    else:
        _assert_audit_adapter_mock(ctx)


def _assert_audit_adapter_mock(ctx: dict) -> None:
    """Assert adapter execution audit log call exists (in-process mode)."""
    env = ctx["env"]
    mock_audit = env.mock["audit"].return_value
    assert mock_audit.log_operation.called, (
        "Expected audit_logger.log_operation to be called for adapter execution logging"
    )
    for call in mock_audit.log_operation.call_args_list:
        op = call.kwargs.get("operation") or (call.args[0] if call.args else None)
        success = call.kwargs.get("success")
        details = call.kwargs.get("details")
        if op == "create_media_buy" and success is True and details is not None:
            return
    raise AssertionError(
        f"Expected audit log entry for adapter execution "
        f"(operation='create_media_buy', success=True, with details), "
        f"got: {[c.kwargs for c in mock_audit.log_operation.call_args_list]}"
    )
