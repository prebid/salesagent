"""Strict error.json conformance contract for ``extract_wire_suggestion``.

The AdCP error object has ONE defined shape (error.json): ``suggestion`` is a
top-level sibling of code/message/field/retry_after/recovery. ``details`` is a
free-form dict — a suggestion buried there is NOT at the protocol position and
must not satisfy a conformance assertion. These tests pin the strict contract
so the harness red-flags every emitter that buries (or omits) the suggestion
instead of masking the drift (#1417).
"""

import pytest

from tests.harness.transport import Transport, TransportResult, extract_wire_suggestion


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


class TestRequireWireGuardsWireAbsence:
    """TransportResult.require_wire() narrows the optional wire and names the transport.

    The raising branch is the success-path analogue of assert_wire_error's
    no-envelope check. It fires on exactly the paths whose dispatcher passes no
    ``envelope`` (the error branches), so it reads the ``transport`` field that
    ``call_via`` stamps on every result — not ``envelope["transport"]``, which is
    absent there and used to print "unknown transport". Constructed directly (no
    dispatch) to exercise the branch in isolation.
    """

    def test_wire_absent_raises_naming_the_transport(self):
        result = TransportResult(transport=Transport.A2A, error=RuntimeError("boom"))
        with pytest.raises(AssertionError, match="^a2a:"):
            result.require_wire()

    def test_wire_absent_without_stamp_falls_back_to_unknown(self):
        # A TransportResult built directly (never through call_via) has no transport
        # stamp and no envelope — the fallback keeps the message non-crashing.
        result = TransportResult(wire_response=None)
        with pytest.raises(AssertionError, match="unknown transport"):
            result.require_wire()

    def test_wire_present_returns_the_wire_dict(self):
        wire = {"creatives": []}
        result = TransportResult(transport=Transport.REST, wire_response=wire)
        assert result.require_wire() is wire
