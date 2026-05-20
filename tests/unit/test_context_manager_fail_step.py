"""Unit tests for ContextManager.fail_step().

This method is the architecture's single ingress for failed-workflow persistence:
``_impl`` functions raise an ``AdCPError`` subclass; the boundary translator
caller invokes ``ctx_manager.fail_step(step_id, exc=exc)`` to persist the spec
two-layer envelope into ``workflow_step.response_data`` AND fire webhooks
through ``update_workflow_step`` → ``_send_push_notifications``.

Critical invariant: the persisted ``response_data`` is the SAME envelope the
wire returns. ``build_two_layer_error_envelope()`` is the single source of
truth for that shape; both call sites (transport translator + fail_step) must
call it. This test verifies the dispatch.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.core.context_manager import ContextManager
from src.core.exceptions import AdCPMediaBuyNotFoundError, AdCPValidationError


class TestFailStepDispatch:
    """fail_step builds the envelope and routes it through update_workflow_step."""

    def test_fail_step_persists_envelope_as_response_data(self):
        from src.core.exceptions import build_two_layer_error_envelope

        cm = ContextManager.__new__(ContextManager)  # bypass __init__ for unit isolation
        cm.update_workflow_step = MagicMock()  # type: ignore[method-assign]
        exc = AdCPMediaBuyNotFoundError("buy_xyz missing", field="media_buy_id")
        expected_envelope = build_two_layer_error_envelope(exc)

        cm.fail_step("step_42", exc=exc)

        # Single strong assertion: status, response_data envelope, error_message all pinned at once.
        cm.update_workflow_step.assert_called_once_with(
            "step_42",
            status="failed",
            response_data=expected_envelope,
            error_message="buy_xyz missing",
        )

    def test_fail_step_explicit_error_message_wins(self):
        cm = ContextManager.__new__(ContextManager)
        cm.update_workflow_step = MagicMock()  # type: ignore[method-assign]
        exc = AdCPValidationError("internal: budget validation failed")

        cm.fail_step("step_99", exc=exc, error_message="user-facing: please fix your budget")

        kwargs = cm.update_workflow_step.call_args.kwargs
        assert kwargs["error_message"] == "user-facing: please fix your budget"

    def test_fail_step_response_data_matches_boundary_envelope(self):
        """Single source of truth: fail_step's envelope is byte-identical to the boundary envelope.

        ``workflow_step.response_data`` is read by ``get_task`` and by webhook
        delivery. The wire response from the boundary translator is what the
        immediate caller sees. They MUST match — otherwise polling vs immediate
        responses diverge.
        """
        from src.core.exceptions import build_two_layer_error_envelope

        cm = ContextManager.__new__(ContextManager)
        cm.update_workflow_step = MagicMock()  # type: ignore[method-assign]
        exc = AdCPMediaBuyNotFoundError("buy_x missing", suggestion="check the id")

        cm.fail_step("s1", exc=exc)
        persisted = cm.update_workflow_step.call_args.kwargs["response_data"]
        wire = build_two_layer_error_envelope(exc)

        assert persisted == wire

    def test_fail_step_propagates_context(self):
        """exc.context is preserved in the persisted envelope."""
        from adcp.types import ContextObject

        cm = ContextManager.__new__(ContextManager)
        cm.update_workflow_step = MagicMock()  # type: ignore[method-assign]
        ctx = ContextObject(correlation_id="trace-abc")
        exc = AdCPMediaBuyNotFoundError("buy_x missing", context=ctx)

        cm.fail_step("s1", exc=exc)
        envelope = cm.update_workflow_step.call_args.kwargs["response_data"]
        assert envelope["context"] == {"correlation_id": "trace-abc"}
