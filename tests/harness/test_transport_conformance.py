"""Strict error.json conformance contract for ``extract_wire_suggestion``.

The AdCP error object has ONE defined shape (error.json): ``suggestion`` is a
top-level sibling of code/message/field/retry_after/recovery. ``details`` is a
free-form dict — a suggestion buried there is NOT at the protocol position and
must not satisfy a conformance assertion. These tests pin the strict contract
so the harness red-flags every emitter that buries (or omits) the suggestion
instead of masking the drift (#1417).
"""

import pytest

from tests.harness.transport import TransportResult, extract_wire_suggestion
from tests.harness.wire_fixtures import wire_error_result


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


def _mirrored_envelope(*, errors_suggestion: str | None, adcp_suggestion: str | None) -> dict:
    """A two-layer AUTH_REQUIRED envelope with a suggestion on the named layer(s) only."""
    payload_error: dict = {"code": "AUTH_REQUIRED", "message": "token required", "recovery": "correctable"}
    envelope_error: dict = dict(payload_error)
    if errors_suggestion is not None:
        payload_error["suggestion"] = errors_suggestion
    if adcp_suggestion is not None:
        envelope_error["suggestion"] = adcp_suggestion
    return {"adcp_error": envelope_error, "errors": [payload_error]}


def _result(envelope: dict) -> TransportResult:
    # has_wire=True: every caller hands in a captured two-layer envelope — the
    # state a real >= 400 wire body leaves behind. These tests grade what
    # ``assert_wire_error`` does with bytes that WERE received; declaring no
    # wire would model a dispatch that never reached a boundary at all.
    return wire_error_result(envelope)


class TestRequireSuggestionDemandsBothMirroredLayers:
    """``require_suggestion=True`` grades BOTH mirrored layers, by name (#1547 item 3).

    ``AdcpErrorResponse.of`` (src/core/schemas/_base.py) carries the error
    object twice — once as ``errors[0]``, once mirrored to the envelope-level
    ``adcp_error``. A buyer parsing either layer must find the same
    ``suggestion``. An either-layer check (``errors[0].get(...) or
    adcp_error.get(...)``) lets a one-layer emitter satisfy EVERY call site in
    the suite, so the assertion cannot catch the mirror breaking — which is the
    only defect it exists to catch.

    Presence, not equality: error.json defines ``suggestion`` as free-form text
    with no enumMetadata tie (unlike its sibling ``recovery``, whose enum
    relationship the schema does spell out), so the contract is that each layer
    carries a non-empty one — not that the harness pins the wording.
    """

    def test_both_layers_carrying_a_suggestion_passes(self):
        result = _result(_mirrored_envelope(errors_suggestion="provide a token", adcp_suggestion="provide a token"))
        result.assert_wire_error("AUTH_REQUIRED", recovery="correctable", require_suggestion=True)

    def test_suggestion_only_on_errors0_fails_naming_the_envelope_layer(self):
        """The mirror dropped it: ``adcp_error`` carries no suggestion."""
        result = _result(_mirrored_envelope(errors_suggestion="provide a token", adcp_suggestion=None))
        with pytest.raises(AssertionError, match="adcp_error"):
            result.assert_wire_error("AUTH_REQUIRED", recovery="correctable", require_suggestion=True)

    def test_suggestion_only_on_adcp_error_fails_naming_the_payload_layer(self):
        """The inverse drop: ``errors[0]`` — the layer the AdCP payload defines — carries none."""
        result = _result(_mirrored_envelope(errors_suggestion=None, adcp_suggestion="provide a token"))
        with pytest.raises(AssertionError, match=r"errors\[0\]"):
            result.assert_wire_error("AUTH_REQUIRED", recovery="correctable", require_suggestion=True)

    def test_empty_string_on_one_layer_fails(self):
        """A present-but-empty suggestion is an omission, not a value."""
        result = _result(_mirrored_envelope(errors_suggestion="provide a token", adcp_suggestion=""))
        with pytest.raises(AssertionError, match="adcp_error"):
            result.assert_wire_error("AUTH_REQUIRED", recovery="correctable", require_suggestion=True)

    def test_neither_layer_fails(self):
        result = _result(_mirrored_envelope(errors_suggestion=None, adcp_suggestion=None))
        with pytest.raises(AssertionError):
            result.assert_wire_error("AUTH_REQUIRED", recovery="correctable", require_suggestion=True)

    def test_omitting_require_suggestion_leaves_the_assertion_unchanged(self):
        """A one-layer envelope is only a defect when the caller asked for the suggestion."""
        result = _result(_mirrored_envelope(errors_suggestion="provide a token", adcp_suggestion=None))
        result.assert_wire_error("AUTH_REQUIRED", recovery="correctable")
