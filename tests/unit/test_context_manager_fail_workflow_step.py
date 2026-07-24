"""Unit tests for ``ContextManager.audit_workflow_step_failure``.

Validates the two contracts the helper exists to enforce:

1. Webhook subscribers see the same wire shape as synchronous callers —
   ``response_data`` carries the full two-layer envelope (``adcp_error`` + ``errors[]``),
   not just an opaque ``error_message`` string. Without this, async push
   notifications fire with empty bodies (see ``_send_push_notifications`` at
   ``context_manager.py:715-726``).

2. A DB hiccup during the audit write must NOT shadow the original
   exception. The caller's bare ``raise`` (intended to re-raise the original
   AdCPError) would otherwise pick up the audit-failure exception and the
   buyer would see an unrelated DB error in place of the real validation
   failure.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.core.context_manager import ContextManager
from src.core.exceptions import (
    _SANITIZED_INTERNAL_MESSAGE,
    AdCPConfigurationError,
    AdCPValidationError,
    build_two_layer_error_envelope,
    safe_adcp_error,
)
from tests.helpers.secret_scrub import SECRET_BEARING_MESSAGE, assert_no_secret_leak


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
    code: str, message: str, *, recovery: str, field: str | None = None, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build the two-layer wire-shape ``response_data`` the helper must emit.

    Constructs a temporary ``AdCPError`` and calls
    ``build_two_layer_error_envelope`` — the same function the production code
    now uses — so the test asserts on EXACTLY the dict shape
    ``update_workflow_step`` will receive.
    """
    from src.core.exceptions import AdCPError

    exc = AdCPError(message, field=field, details=details, recovery=recovery)
    exc.error_code = code
    return build_two_layer_error_envelope(exc)


def _scrubbed_response_data(exc: Exception) -> dict[str, Any]:
    """The scrubbed two-layer ``response_data`` the audit helper must emit for an INTERNAL
    error — built through the exact production policy (``safe_adcp_error`` →
    ``build_two_layer_error_envelope``, on the ORIGINAL exception, NOT pre-normalized). If the
    helper stops scrubbing, the raw message survives and no longer matches this expected shape
    (the mutation reddens). The independent secret-absence tests below don't route through this
    helper — they assert the literal secret is nowhere in the payload — so a shared regression in
    both helper and production can't hide the leak.
    """
    return build_two_layer_error_envelope(safe_adcp_error(exc))


class TestFailWorkflowStepForExceptionWebhookPayload:
    """Webhook subscribers must receive the two-layer envelope, not just error_message."""

    def test_adcp_error_threads_envelope_into_response_data(self):
        cm, mock_update = _new_ctx_manager_with_mocked_update()
        exc = AdCPValidationError(
            "bad budget",
            field="packages[].budget",
            details={"violations": ["below minimum"]},
        )

        cm.audit_workflow_step_failure("step_abc", exc)

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

    def test_untyped_internal_error_scrubs_secret_from_webhook_payload(self):
        """An untyped/internal exception's raw message — which may embed a connection string —
        is SCRUBBED to the sanitized generic message before it reaches the webhook subscriber.

        The async webhook path is the twin of the synchronous re-raise scrub; both route
        internal/infra errors through ``safe_adcp_error`` so ``str(exc)`` never reaches the
        buyer. The wire code stays standard (SERVICE_UNAVAILABLE) and recovery transient.
        """
        cm, mock_update = _new_ctx_manager_with_mocked_update()
        secret = SECRET_BEARING_MESSAGE

        cm.audit_workflow_step_failure("step_abc", RuntimeError(secret))

        # error_message + response_data carry the sanitized message, NOT the secret.
        mock_update.assert_called_once_with(
            "step_abc",
            status="failed",
            error_message=_SANITIZED_INTERNAL_MESSAGE,
            response_data=_scrubbed_response_data(RuntimeError(secret)),
        )

    def test_config_error_scrubs_secret_from_webhook_payload(self):
        """``AdCPConfigurationError`` (wire code CONFIGURATION_ERROR) is terminal-internal and
        also scrubbed — its decryption-failure raise sites can interpolate a secret. Before the
        shared-policy fix this leg passed through unscrubbed."""
        cm, mock_update = _new_ctx_manager_with_mocked_update()
        secret = SECRET_BEARING_MESSAGE

        cm.audit_workflow_step_failure("step_abc", AdCPConfigurationError(f"decrypt failed: {secret}"))

        mock_update.assert_called_once_with(
            "step_abc",
            status="failed",
            error_message=_SANITIZED_INTERNAL_MESSAGE,
            response_data=_scrubbed_response_data(AdCPConfigurationError(f"decrypt failed: {secret}")),
        )

    @pytest.mark.parametrize(
        ("exc_factory", "expected_code", "msg_keyword"),
        [
            pytest.param(lambda s: ValueError(s), "VALIDATION_ERROR", "validate", id="ValueError"),
            pytest.param(lambda s: PermissionError(s), "AUTH_REQUIRED", "credential", id="PermissionError"),
        ],
    )
    def test_untyped_valueerror_permissionerror_scrub_secret_from_webhook_payload(
        self, exc_factory, expected_code, msg_keyword
    ):
        """Untyped ``ValueError``/``PermissionError`` on the webhook path: message scrubbed, but the
        SEMANTIC code matches what the synchronous boundary emits (webhook↔sync parity).

        Two properties, both from the reviewer's finding:
        1. Message trust — the raw ``str(exc)`` (a connection string / token / SQL) is scrubbed,
           because these are raw built-ins (untrusted provenance).
        2. Semantic parity — the persisted wire code is VALIDATION_ERROR / AUTH_REQUIRED (the code
           the synchronous transport also emits for the same exception via
           ``normalize_to_adcp_error``), NOT a divergent SERVICE_UNAVAILABLE. Otherwise a buyer
           watching both channels sees two different codes for one failure.

        Authored INDEPENDENTLY of ``safe_adcp_error``: secret-absence is asserted against the literal
        payload, and the parity code is checked against ``normalize_to_adcp_error`` (the shared
        semantic mapping), not reconstructed through the sanitizer under test.
        """
        from src.core.exceptions import normalize_to_adcp_error

        # Record the persisted payload via a plain callable (not a Mock) so the assertions can do
        # substring-absence checks on the ACTUAL emitted values without the assert_called_once() +
        # call_args split-assertion pattern the weak-mock guard forbids.
        calls: list[dict] = []
        cm = ContextManager.__new__(ContextManager)
        cm.update_workflow_step = lambda step_id, **kw: calls.append({"step_id": step_id, **kw})  # type: ignore[method-assign]
        secret = SECRET_BEARING_MESSAGE

        cm.audit_workflow_step_failure("step_abc", exc_factory(secret))

        assert len(calls) == 1, "audit must persist exactly one failed-step update"
        payload = calls[0]
        # The message is category-appropriate, NOT the generic "internal error" (which would
        # contradict a client-correctable code), and secret-free. The shared oracle IS the
        # definition of a leak (incl. the bearer-token shape) — assert both webhook sinks.
        assert msg_keyword in payload["error_message"].lower()
        assert "internal error" not in payload["error_message"].lower()
        assert_no_secret_leak(payload["response_data"], context="webhook response_data")
        assert_no_secret_leak(payload["error_message"], context="error_message")
        # Webhook↔sync semantic parity: the persisted code equals the synchronous boundary's code.
        persisted_code = payload["response_data"]["adcp_error"]["code"]
        assert persisted_code == expected_code
        assert persisted_code == normalize_to_adcp_error(exc_factory(secret)).wire_error_code


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
        cm.audit_workflow_step_failure("step_abc", original)

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
                cm.audit_workflow_step_failure("step_abc", e)
                raise

        with pytest.raises(AdCPValidationError) as excinfo:
            caller_pattern()
        # The buyer sees the real error, not the audit failure.
        assert excinfo.value is original
        assert "real error" in excinfo.value.message
