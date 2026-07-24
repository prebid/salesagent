"""Strict error.json conformance contract for ``extract_wire_suggestion``.

The AdCP error object has ONE defined shape (error.json): ``suggestion`` is a
top-level sibling of code/message/field/retry_after/recovery. ``details`` is a
free-form dict — a suggestion buried there is NOT at the protocol position and
must not satisfy a conformance assertion. These tests pin the strict contract
so the harness red-flags every emitter that buries (or omits) the suggestion
instead of masking the drift (#1417).
"""

from tests.harness.transport import extract_wire_suggestion


class TestExtractWireSuggestionStrict:
    """extract_wire_suggestion reads the top-level protocol position ONLY."""

    def test_top_level_suggestion_on_errors0_is_extracted(self):
        envelope = {"errors": [{"code": "AUTH_REQUIRED", "message": "x", "suggestion": "provide a token"}]}
        assert extract_wire_suggestion(envelope) == "provide a token"

    def test_top_level_suggestion_on_adcp_error_is_extracted(self):
        envelope = {"adcp_error": {"code": "AUTH_REQUIRED", "message": "x", "suggestion": "provide a token"}}
        assert extract_wire_suggestion(envelope) == "provide a token"

    def test_no_envelope_returns_none(self):
        assert extract_wire_suggestion(None) is None

    def test_suggestion_buried_in_errors0_details_is_not_extracted(self):
        """A suggestion hidden in the free-form details dict is non-conformant.

        error.json places ``suggestion`` at the top level of the error object;
        ``details.suggestion`` is a hand-placed copy at the wrong position and
        must NOT satisfy the conformance lookup.
        """
        envelope = {
            "errors": [{"code": "AUTH_REQUIRED", "message": "x", "details": {"suggestion": "buried — wrong position"}}]
        }
        assert extract_wire_suggestion(envelope) is None

    def test_suggestion_buried_in_adcp_error_details_is_not_extracted(self):
        """Same strictness for the envelope-level ``adcp_error`` layer."""
        envelope = {
            "adcp_error": {
                "code": "AUTH_REQUIRED",
                "message": "x",
                "details": {"suggestion": "buried — wrong position"},
            }
        }
        assert extract_wire_suggestion(envelope) is None


class TestAssertWireErrorRequiresRealWire:
    """``require_real_wire`` refuses an envelope the A2A dispatcher rebuilt.

    The A2A dispatcher falls back to ``build_two_layer_error_envelope`` when the
    transport raised with no envelope attached, and that rebuild lands on
    ``wire_error_envelope`` looking exactly like real wire bytes. Tests whose
    point is what the buyer RECEIVES must be able to refuse it, so the flag has
    to actually bite — otherwise it is decoration.
    """

    ENVELOPE = {
        "adcp_error": {"code": "AUTH_REQUIRED", "message": "x", "recovery": "correctable"},
        "errors": [{"code": "AUTH_REQUIRED", "message": "x", "recovery": "correctable"}],
    }

    def _result(self, *, synthesized: bool):
        from tests.harness.transport import TransportResult

        return TransportResult(
            error=RuntimeError("rejected"),
            wire_error_envelope=self.ENVELOPE,
            wire_error_envelope_synthesized=synthesized,
        )

    def test_real_wire_envelope_passes(self):
        self._result(synthesized=False).assert_wire_error("AUTH_REQUIRED", require_real_wire=True)

    def test_rebuilt_envelope_is_rejected(self):
        import pytest

        with pytest.raises(AssertionError, match="rebuilt from the reconstructed"):
            self._result(synthesized=True).assert_wire_error("AUTH_REQUIRED", require_real_wire=True)

    def test_rebuilt_envelope_still_passes_without_the_flag(self):
        """Default stays permissive: existing A2A error tests are unaffected."""
        self._result(synthesized=True).assert_wire_error("AUTH_REQUIRED")


class TestA2ADispatcherDerivesSynthesizedFlag:
    """The synthesized flag comes from the REAL ``_a2a_wire_envelope_was_synthesized``.

    The class above pins how ``require_real_wire`` reacts to the flag, but sets
    the flag by hand — mutate the derivation to ``return False`` and those tests
    stay green. These drive the actual derivation through ``A2ADispatcher``
    against the two exception shapes the A2A reconstruction produces, so the
    detector-never-fires direction (the one that matters for a security guard)
    is caught.
    """

    class _RaisingEnv:
        """Minimal env: ``call_a2a`` raises the pre-reconstructed exception."""

        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def call_a2a(self, **kwargs):
            raise self._exc

    def _dispatch_reconstructed(self, a2a_exc: Exception):
        """Reconstruct *a2a_exc* exactly as the harness does, then dispatch.

        ``_unwrap_a2a_server_error`` is the production-reconstruction seam
        ``_run_a2a_handler`` routes every raised ``A2AError`` through; feeding
        its output to ``A2ADispatcher.dispatch`` runs the real
        ``_a2a_wire_envelope_was_synthesized`` derivation — nothing is hand-set.
        """
        from tests.harness._base import _unwrap_a2a_server_error
        from tests.harness.dispatchers import A2ADispatcher

        reconstructed = _unwrap_a2a_server_error(a2a_exc)
        return A2ADispatcher().dispatch(self._RaisingEnv(reconstructed))  # type: ignore[arg-type]

    def test_bare_a2a_error_no_data_derives_synthesized_true(self):
        """A bare ``A2AError`` (no ``data``) — the buyer got NO AdCP envelope.

        The dispatcher's fallback rebuilds one anyway, so the derivation must
        flag it and ``require_real_wire=True`` must refuse it.
        """
        import pytest
        from a2a.types import InvalidRequestError

        from src.core.exceptions import INVALID_TOKEN_MESSAGE

        result = self._dispatch_reconstructed(InvalidRequestError(message=INVALID_TOKEN_MESSAGE))

        assert result.is_error
        assert result.wire_error_envelope is not None, (
            "The fallback rebuild is expected here — that masquerade is exactly what the flag exposes"
        )
        assert result.wire_error_envelope_synthesized is True
        with pytest.raises(AssertionError, match="rebuilt from the reconstructed"):
            result.assert_wire_error("AUTH_REQUIRED", require_real_wire=True)

    def test_a2a_error_with_data_envelope_derives_synthesized_false(self):
        """An ``A2AError`` carrying the envelope in ``data`` IS real wire bytes.

        The derivation must stay quiet and ``require_real_wire=True`` must pass
        — the detector-fires-when-it-shouldn't direction.
        """
        from a2a.types import InvalidRequestError

        from src.core.exceptions import AdCPAuthenticationError, build_two_layer_error_envelope

        envelope = build_two_layer_error_envelope(AdCPAuthenticationError("Invalid authentication token"))
        result = self._dispatch_reconstructed(
            InvalidRequestError(message="Invalid authentication token", data=envelope)
        )

        assert result.is_error
        assert result.wire_error_envelope_synthesized is False
        result.assert_wire_error("AUTH_REQUIRED", require_suggestion=True, require_real_wire=True)
