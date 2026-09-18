"""Unit tests for ``ContextManager.audit_workflow_step_failure``.

Validates the two contracts the helper exists to enforce:

1. Webhook subscribers see the same wire shape as synchronous callers —
   ``response_data`` carries the full two-layer envelope (``adcp_error`` + ``errors[]``),
   not just an opaque ``error_message`` string. Without this, async push
   notifications fire with empty bodies (see ``_send_push_notifications`` at
   ``context_manager.py:715-726``).

2. A DB hiccup during the audit write must NOT shadow the original
   exception. The caller's bare ``raise`` (intended to re-raise the original
   AdCPSalesAgentError) would otherwise pick up the audit-failure exception and the
   buyer would see an unrelated DB error in place of the real validation
   failure.

Merge note (origin/main #1858 storyboard-conformance work). main graded the
helper's defensive wire-code sanitization branch — a source whose code fell
outside ``WIRE_STANDARD_CODES`` was rewritten to SERVICE_UNAVAILABLE — and
grew a test proving the rewrite took the PIN's recovery rather than a
hand-typed ``recovery="terminal"``. That branch no longer exists: the merged
``exceptions.py`` deletes ``WIRE_STANDARD_CODES``/``translate_error_code``
outright (see its "Reconciliation note", exceptions.py:74-102) because the pin
makes the AdCP code vocabulary OPEN and requires receivers to decode an unknown
code from ``recovery``. The obligation that test carried is not dropped, it is
relocated to where the merged architecture enforces it —
``TestWireCodeAndRecoveryCannotContradictThePin`` below — and main's
anti-self-agreement device (every caller STATES the (code, recovery) pair it
expects, so a derivation that silently moved reddens these tests instead of
quietly agreeing with itself) is carried into ``_expected_response_data``.
"""

from unittest.mock import MagicMock

import pytest

from src.core.context_manager import ContextManager
from src.core.errors.codes import CODE_TABLE, AppErrorCode, ErrorCode, Recovery
from src.core.errors.details import ValidationDetails
from src.core.exceptions import AdCPSalesAgentError, AdCPValidationError
from tests.helpers.envelope_assertions import envelope_for


def _new_ctx_manager_with_mocked_update() -> tuple[ContextManager, MagicMock]:
    """Build a ContextManager instance with ``update_workflow_step`` mocked.

    The helper under test calls ``self.update_workflow_step``; mocking that
    method directly isolates the helper's logic from DB plumbing while still
    exercising real envelope-builder integration.
    """
    cm = ContextManager.__new__(ContextManager)  # bypass __init__ DB setup
    cm.update_workflow_step = MagicMock()  # type: ignore[method-assign]
    return cm, cm.update_workflow_step


def _expected_response_data(exc: AdCPSalesAgentError, *, code: ErrorCode | AppErrorCode, recovery: Recovery) -> dict:
    """Build the two-layer wire-shape ``response_data`` the helper must emit.

    Built from the SAME exception the test raises, through the same
    ``AdcpErrorResponse.of`` + ``to_wire`` production calls (``envelope_for``) — so the
    assertion is on the dict ``update_workflow_step`` receives, with nothing reconstructed.

    Reconstructing an equivalent error here would not be equivalent: a NAMED-code
    ``AdCPSalesAgentError`` resolves recovery and suggestion from CODE_TABLE, while a class-coded
    subclass keeps its class defaults. Passing the real exception sidesteps that
    asymmetry instead of encoding it.

    ``code`` and ``recovery`` are REQUIRED, and are asserted against what the
    exception actually derives (origin/main #1858's device, kept). Building the
    expected envelope with production's own builder would otherwise let a moved
    derivation agree with itself; stating the pair here means every caller
    declares the classification it expects a subscriber to read.
    """
    assert (exc.error_code, exc.recovery) == (code, recovery), (
        f"expected {code} to derive recovery {recovery!r}, got "
        f"({exc.error_code!r}, {exc.recovery!r}) — the test's stated expectation and the pin disagree"
    )
    return envelope_for(exc)


def _normalized_for(exc: Exception) -> AdCPSalesAgentError:
    """The typed error production derives from an untyped one, via the same helper."""
    from src.core.exceptions import adcp_error_for

    return adcp_error_for(exc)


class TestFailWorkflowStepForExceptionWebhookPayload:
    """Webhook subscribers must receive the two-layer envelope, not just error_message."""

    def test_adcp_error_threads_envelope_into_response_data(self):
        cm, mock_update = _new_ctx_manager_with_mocked_update()
        exc = AdCPValidationError(
            field="packages[].budget",
            details=ValidationDetails(reasons=["below minimum"]),
        )

        cm.audit_workflow_step_failure("step_abc", exc)

        # Helper must call update_workflow_step with the exact wire-shape
        # payload subscribers will read off the webhook. Single
        # ``assert_called_once_with`` (no inspection) keeps the test atomic
        # and rejects any future drift in the helper's emitted shape.
        mock_update.assert_called_once_with(
            "step_abc",
            status="failed",
            error_message=exc.message,
            response_data=_expected_response_data(exc, code=ErrorCode.VALIDATION_ERROR, recovery=Recovery.CORRECTABLE),
        )

    def test_untyped_exception_wrapped_with_typed_error(self):
        """Bare exceptions get a typed AdCPSalesAgentError, so the payload is classified.

        The code is INTERNAL_ERROR — named on the base by ``adcp_error_for``'s
        final branch — and its pinned recovery is ``transient``. There is no
        rewrite to SERVICE_UNAVAILABLE any more: the merged exceptions module
        deletes the closed wire set that motivated it, because the pin says the
        vocabulary is open and a receiver decodes an unknown code from
        ``recovery`` (exceptions.py:74-102). The (code, recovery) pair a
        subscriber reads is stated below, not inferred.

        This ``response_data`` is a PERSISTED record: wire responses and
        persisted ``workflow_step.response_data`` share the same two-layer
        shape, both serialized from ``AdcpErrorResponse.of`` with ``to_wire``.
        It is read back by async webhook subscribers — so it gets the
        SAME provenance protection prkv.8 gives the live transports:
        ``adcp_error_for()``'s untyped-Exception fallback carries NO text from
        the exception, only the code's own table sentence, because the
        exception's ``str()`` has no guarantee of being safe to persist/replay
        to a subscriber (a DSN, a stack fragment, an upstream response body).
        """
        cm, mock_update = _new_ctx_manager_with_mocked_update()

        cm.audit_workflow_step_failure("step_abc", RuntimeError("postgres://admin:s3cr3t@10.0.0.5/prod"))

        # The raw DSN must not reach the payload: the helper normalizes to a typed error
        # whose text comes from the code, so the expected envelope is built the same way.
        expected = _normalized_for(RuntimeError("postgres://admin:s3cr3t@10.0.0.5/prod"))
        mock_update.assert_called_once_with(
            "step_abc",
            status="failed",
            error_message=expected.message,
            response_data=_expected_response_data(
                expected, code=AppErrorCode.INTERNAL_ERROR, recovery=Recovery.TRANSIENT
            ),
        )
        assert "s3cr3t" not in str(mock_update.call_args)

    def test_untyped_exception_with_no_text_is_indistinguishable(self):
        """A text-less untyped exception yields the same payload as any other.

        Its wording used to be the exception's type name; the sentence is now the
        code's, so an empty ``str(exc)`` is no longer a distinct case at all.
        """
        cm, mock_update = _new_ctx_manager_with_mocked_update()

        cm.audit_workflow_step_failure("step_abc", RuntimeError())

        expected = _normalized_for(RuntimeError())
        mock_update.assert_called_once_with(
            "step_abc",
            status="failed",
            error_message=expected.message,
            response_data=_expected_response_data(
                expected, code=AppErrorCode.INTERNAL_ERROR, recovery=Recovery.TRANSIENT
            ),
        )


class TestWireCodeAndRecoveryCannotContradictThePin:
    """origin/main #1858's sanitization obligation, at the layer that now enforces it.

    main's ``test_non_standard_wire_code_is_sanitized_with_the_pinned_recovery``
    reached the helper's rewrite branch by constructing an ``AdCPSalesAgentError`` and
    ASSIGNING ``error_code = "TOTALLY_NON_STANDARD_CODE"``, then proved the
    substituted SERVICE_UNAVAILABLE carried the pin's ``transient`` instead of a
    hand-typed ``terminal``. Neither the assignment nor the rewrite is
    expressible now, so the branch is not code the helper can reach — but the
    buyer-visible contradiction main was protecting against (a subscriber
    classifying by code sees "retry with backoff" while one classifying by
    recovery sees "stop") is exactly what these tests still forbid.
    """

    def test_a_code_the_table_does_not_classify_cannot_be_declared(self):
        """An out-of-table code is refused at CLASS CREATION, so no error can carry one.

        This is what replaced the sanitize-on-emit branch: rather than rewriting a
        bad code on its way to the subscriber, the error naming it never exists.

        It moved one step earlier and got stronger. The refusal used to fire on the first
        CONSTRUCTION of an error naming the code; with ``error_code=`` gone from the
        constructor, a code reaches an error only by being DECLARED on a class, and
        ``__init_subclass__`` refuses that at import — so a bad code cannot survive the
        module that introduces it, let alone reach a raise site.
        """
        with pytest.raises(TypeError, match="which CODE_TABLE does not"):

            class _Impossible(AdCPSalesAgentError):
                _code = "TOTALLY_NON_STANDARD_CODE"

    def test_a_constructed_error_cannot_be_re_coded_after_the_fact(self):
        """``error_code`` is read-only, closing main's route into the branch.

        Assignment was how a source acquired a non-standard code in the first
        place; with no setter, ``message``/``recovery``/``suggestion`` cannot drift
        away from the code they are derived from.
        """
        exc = AdCPValidationError()

        with pytest.raises(AttributeError, match="no setter"):
            exc.error_code = "TOTALLY_NON_STANDARD_CODE"  # type: ignore[misc]

    def test_both_envelope_layers_pair_the_code_with_the_pinned_recovery(self):
        """The payload a subscriber reads pairs each code with CODE_TABLE's recovery.

        Graded on the dict ``assert_called_once_with`` has just proven production
        emitted — envelope-level ``adcp_error`` and ``errors[0]`` alike, since the
        two layers disagreeing is the same buyer-visible contradiction by another
        route.
        """
        cm, mock_update = _new_ctx_manager_with_mocked_update()
        exc = AdCPValidationError(field="packages[].budget")

        cm.audit_workflow_step_failure("step_sanitize", exc)

        expected = _expected_response_data(exc, code=ErrorCode.VALIDATION_ERROR, recovery=Recovery.CORRECTABLE)
        mock_update.assert_called_once_with(
            "step_sanitize",
            status="failed",
            error_message=exc.message,
            response_data=expected,
        )
        for layer in (expected["adcp_error"], expected["errors"][0]):
            assert layer["recovery"] == CODE_TABLE[layer["code"]].recovery, (
                f"{layer['code']} was emitted with recovery {layer['recovery']!r}, "
                f"but the pin classifies it {CODE_TABLE[layer['code']].recovery!r}"
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
        original = AdCPValidationError()

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
        original = AdCPValidationError()

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
