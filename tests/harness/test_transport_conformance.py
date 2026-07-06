"""Strict error.json conformance contract for ``extract_wire_suggestion``.

The AdCP error object has ONE defined shape (error.json): ``suggestion`` is a
top-level sibling of code/message/field/retry_after/recovery. ``details`` is a
free-form dict — a suggestion buried there is NOT at the protocol position and
must not satisfy a conformance assertion. These tests pin the strict contract
so the harness red-flags every emitter that buries (or omits) the suggestion
instead of masking the drift (salesagent-9val).
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
