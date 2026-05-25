"""Unit tests for ``ContextManager.fail_workflow_step_for_exception``.

Validates the two contracts the helper exists to enforce:

1. Webhook subscribers see the same wire shape as synchronous callers —
   ``response_data`` carries the two-layer envelope's ``errors[]`` payload,
   not just an opaque ``error_message`` string. Without this, async push
   notifications fire with empty bodies (see ``_send_push_notifications`` at
   ``context_manager.py:715-726``).

2. A DB hiccup during the audit write must NOT shadow the original
   exception. The caller's bare ``raise`` (intended to re-raise the original
   AdCPError) would otherwise pick up the audit-failure exception and the
   buyer would see an unrelated DB error in place of the real validation
   failure.
"""

from unittest.mock import MagicMock

import pytest

from src.core.context_manager import ContextManager
from src.core.exceptions import AdCPValidationError


def _new_ctx_manager_with_mocked_update() -> tuple[ContextManager, MagicMock]:
    """Build a ContextManager instance with ``update_workflow_step`` mocked.

    The helper under test calls ``self.update_workflow_step``; mocking that
    method directly isolates the helper's logic from DB plumbing while still
    exercising real envelope-builder integration.
    """
    cm = ContextManager.__new__(ContextManager)  # bypass __init__ DB setup
    cm.update_workflow_step = MagicMock()  # type: ignore[method-assign]
    return cm, cm.update_workflow_step


def _expected_response_data(
    code: str, message: str, *, recovery: str, field: str | None = None, details: dict | None = None
) -> dict:
    """Build the wire-shape ``response_data`` the helper must emit.

    Uses the same SDK helper the production code uses, so the test asserts on
    EXACTLY the dict shape ``update_workflow_step`` will receive — no
    inspection or partial matching needed (per the
    ``test_architecture_weak_mock_assertions`` guard).
    """
    from adcp.server.helpers import adcp_error as sdk_adcp_error

    payload = sdk_adcp_error(code, message, recovery=recovery, field=field, details=details)
    return {"errors": payload["errors"]}


class TestFailWorkflowStepForExceptionWebhookPayload:
    """Webhook subscribers must receive the two-layer envelope, not just error_message."""

    def test_adcp_error_threads_envelope_into_response_data(self):
        cm, mock_update = _new_ctx_manager_with_mocked_update()
        exc = AdCPValidationError(
            "bad budget",
            field="packages[].budget",
            details={"violations": ["below minimum"]},
        )

        cm.fail_workflow_step_for_exception("step_abc", exc)

        # Helper must call update_workflow_step with the exact wire-shape
        # payload subscribers will read off the webhook. Single
        # ``assert_called_once_with`` (no inspection) keeps the test atomic
        # and rejects any future drift in the helper's emitted shape.
        mock_update.assert_called_once_with(
            "step_abc",
            status="failed",
            error_message="bad budget",
            response_data=_expected_response_data(
                "VALIDATION_ERROR",
                "bad budget",
                recovery="correctable",
                field="packages[].budget",
                details={"violations": ["below minimum"]},
            ),
        )

    def test_untyped_exception_wrapped_with_wire_safe_code(self):
        """Bare exceptions get a synthetic AdCPError so the wire code stays standard.

        ``AdCPError`` defaults to ``INTERNAL_ERROR`` which is in
        ``INTERNAL_CODES``; the helper's defensive wire-code enforcement
        falls back to ``SERVICE_UNAVAILABLE`` so async subscribers only see
        codes from ``STANDARD_ERROR_CODES`` even when the source was untyped.
        """
        cm, mock_update = _new_ctx_manager_with_mocked_update()

        cm.fail_workflow_step_for_exception("step_abc", RuntimeError("kaboom"))

        mock_update.assert_called_once_with(
            "step_abc",
            status="failed",
            error_message="kaboom",
            response_data=_expected_response_data("SERVICE_UNAVAILABLE", "kaboom", recovery="terminal"),
        )

    def test_empty_exception_message_falls_back_to_type_name(self):
        cm, mock_update = _new_ctx_manager_with_mocked_update()

        cm.fail_workflow_step_for_exception("step_abc", RuntimeError())

        # Empty message is replaced with type name so the wire envelope and
        # error_message never carry blank strings.
        mock_update.assert_called_once_with(
            "step_abc",
            status="failed",
            error_message="RuntimeError",
            response_data=_expected_response_data("SERVICE_UNAVAILABLE", "RuntimeError", recovery="terminal"),
        )


class TestFailWorkflowStepForExceptionAuditFailureNonFatal:
    """A DB hiccup during the audit write must NOT shadow the original exception."""

    def test_update_workflow_step_raise_is_swallowed(self, caplog):
        """If ``update_workflow_step`` raises, the helper logs and returns normally.

        The caller's bare ``raise`` after this helper returns must propagate
        the original exception. Python's exception chaining would otherwise
        replace it with the audit-failure exception, hiding the real error
        from the buyer.
        """
        cm = ContextManager.__new__(ContextManager)
        cm.update_workflow_step = MagicMock(side_effect=RuntimeError("DB went away"))  # type: ignore[method-assign]
        original = AdCPValidationError("real error the buyer should see")

        # Helper must return normally so the caller's `raise` propagates `original`.
        cm.fail_workflow_step_for_exception("step_abc", original)

        # Audit failure must be logged so SREs can correlate, but the caller
        # never knows it happened — original exception will be re-raised.
        assert any("Failed to audit workflow_step" in record.message for record in caplog.records)

    def test_caller_can_safely_re_raise_after_audit_failure(self):
        """End-to-end: simulate the caller's ``raise`` pattern and verify
        the ORIGINAL exception reaches the test boundary, not the audit one.
        """
        cm = ContextManager.__new__(ContextManager)
        cm.update_workflow_step = MagicMock(side_effect=RuntimeError("DB went away"))  # type: ignore[method-assign]
        original = AdCPValidationError("real error")

        def caller_pattern():
            try:
                # Simulate the body raising
                raise original
            except AdCPValidationError as e:
                cm.fail_workflow_step_for_exception("step_abc", e)
                raise

        with pytest.raises(AdCPValidationError) as excinfo:
            caller_pattern()
        # The buyer sees the real error, not the audit failure.
        assert excinfo.value is original
        assert "real error" in excinfo.value.message
