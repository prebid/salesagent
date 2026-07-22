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
