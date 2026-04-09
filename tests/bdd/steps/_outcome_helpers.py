"""Outcome-based assertion helpers for E2E transport compatibility.

These helpers verify outcomes (DB state, response fields) instead of
interactions (mock.call_count), making assertions work across all transports
including E2E where mocks live in the test process but the adapter runs in Docker.

Usage in Then steps:
    from tests.bdd.steps._outcome_helpers import assert_adapter_executed, is_e2e

    @then('the media buy should proceed to adapter execution')
    def then_adapter_executed(ctx):
        assert_adapter_executed(ctx)
        if not is_e2e(ctx):
            # bonus mock check for in-process transports
            ...
"""

from __future__ import annotations

from tests.bdd.steps._harness_db import db_session


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
    """Verify media buy exists in DB -- proves adapter executed.

    Returns the MediaBuy ORM instance for further assertions.
    """
    from sqlalchemy import select

    from src.core.database.models import MediaBuy

    if media_buy_id is None:
        resp = ctx.get("response")
        if resp is not None:
            media_buy_id = _get_response_field(resp, "media_buy_id")

    assert media_buy_id is not None, "No media_buy_id available to verify creation"

    with db_session(ctx) as session:
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not in DB -- adapter may not have executed"
        return mb


def assert_adapter_executed(ctx: dict) -> object:
    """Verify adapter ran by checking DB state (not mock call count).

    A media buy that reaches a non-draft status proves the adapter was invoked.
    Returns the MediaBuy ORM instance for further assertions.
    """
    mb = assert_media_buy_created(ctx)
    executed_statuses = ("active", "completed", "pending_approval", "pending_activation", "submitted")
    assert mb.status in executed_statuses, (
        f"Media buy status '{mb.status}' does not confirm adapter execution. Expected one of {executed_statuses}."
    )
    return mb


def assert_audit_logged(ctx: dict, *, operation_substring: str = "create_media_buy") -> None:
    """Verify audit logging occurred -- transport-aware.

    In-process: asserts on mock audit logger calls (fast, precise).
    E2E: queries audit_logs table through harness DB session.
    """
    if is_e2e(ctx):
        _assert_audit_logged_e2e(ctx, operation_substring)
    else:
        _assert_audit_logged_mock(ctx, operation_substring)


def _assert_audit_logged_mock(ctx: dict, operation_substring: str) -> list:
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
    return mock_audit.log_operation.call_args_list


def _assert_audit_logged_e2e(ctx: dict, operation_substring: str) -> None:
    """Assert audit_logs table has entries with the operation (E2E mode)."""
    from sqlalchemy import select

    from src.core.database.models import AuditLog

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx for E2E audit assertion"

    with db_session(ctx) as session:
        logs = session.scalars(select(AuditLog).filter_by(tenant_id=tenant.tenant_id)).all()
        matching = [log for log in logs if operation_substring in (log.operation or "")]
        assert matching, (
            f"Expected audit_logs entry containing '{operation_substring}' for tenant "
            f"{tenant.tenant_id}, found {len(logs)} total audit entries with operations: "
            f"{[log.operation for log in logs]}"
        )


def assert_audit_approval_logged(ctx: dict) -> None:
    """Verify approval decision was logged -- transport-aware.

    In-process: checks mock for approval-specific content.
    E2E: checks audit_logs for approval-specific entries.
    """
    if is_e2e(ctx):
        _assert_audit_approval_e2e(ctx)
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
        f"Expected audit log entry with approval-specific content: either "
        f"operation='create_media_buy_pending_approval', or "
        f"operation='create_media_buy' with success=True and details.media_buy_id. "
        f"Got calls: {[c.kwargs for c in mock_audit.log_operation.call_args_list]}"
    )


def _assert_audit_approval_e2e(ctx: dict) -> None:
    """Assert approval-specific audit log exists in DB (E2E mode)."""
    from sqlalchemy import select

    from src.core.database.models import AuditLog

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx for E2E audit assertion"

    with db_session(ctx) as session:
        logs = session.scalars(select(AuditLog).filter_by(tenant_id=tenant.tenant_id)).all()
        for log in logs:
            if log.operation == "create_media_buy_pending_approval":
                return
            if log.operation == "create_media_buy" and log.success is True:
                details = log.details or {}
                if "media_buy_id" in details:
                    return
        raise AssertionError(
            f"Expected audit_logs entry with approval-specific content for tenant "
            f"{tenant.tenant_id}, found operations: {[(log.operation, log.success) for log in logs]}"
        )


def assert_audit_adapter_logged(ctx: dict) -> None:
    """Verify adapter execution was logged -- transport-aware.

    In-process: checks mock for success=True with details.
    E2E: checks audit_logs for success entries with details.
    """
    if is_e2e(ctx):
        _assert_audit_adapter_e2e(ctx)
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


def _assert_audit_adapter_e2e(ctx: dict) -> None:
    """Assert adapter execution audit log exists in DB (E2E mode)."""
    from sqlalchemy import select

    from src.core.database.models import AuditLog

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx for E2E audit assertion"

    with db_session(ctx) as session:
        logs = session.scalars(select(AuditLog).filter_by(tenant_id=tenant.tenant_id)).all()
        for log in logs:
            if log.operation == "create_media_buy" and log.success is True and log.details is not None:
                return
        raise AssertionError(
            f"Expected audit_logs entry for adapter execution "
            f"(operation='create_media_buy', success=True, with details) for tenant "
            f"{tenant.tenant_id}, found: {[(log.operation, log.success) for log in logs]}"
        )
