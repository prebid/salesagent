"""The code-free ``TransportResult`` wire graders actually fail when they should.

``assert_wire_recovery`` and ``assert_wire_is_adcp_envelope`` exist because their
callers previously read a value OUT of the envelope under test and fed it back IN
as the expectation (``assert_wire_error(_wire_code(ctx), ...)``), which makes the
code arm unfailable. A grader that claims to be non-vacuous has to be shown
reddening, not asserted in a docstring — so every arm below is mutation-driven:
each failure case takes a well-formed envelope and breaks exactly one thing.

Unit-level on purpose: ``TransportResult`` is a plain frozen dataclass, so the
graders are gradeable offline without the 4-transport BDD harness that exercises
them in situ.
"""

from __future__ import annotations

import inspect

import pytest

from tests.harness.transport import TransportResult

# Pinned-enum facts these cases rest on (tests/fixtures/adcp_schemas_pinned/enums/error-code.json):
_CORRECTABLE_CODE = "PACKAGE_NOT_FOUND"  # recovery: correctable
_TRANSIENT_CODE = "SERVICE_UNAVAILABLE"  # recovery: transient
_NON_CANONICAL_CODE = "NOT_A_REAL_CODE"  # absent from the pinned enum


def _result(
    *,
    code: str = _CORRECTABLE_CODE,
    recovery: str = "correctable",
    errors_code: str | None = None,
    errors_recovery: str | None = None,
) -> TransportResult:
    """A result carrying a two-layer wire envelope, one layer independently settable.

    Defaults are well-formed and spec-consistent; each test overrides exactly the
    field it means to break, so the failure is attributable to that field alone.
    """
    return TransportResult(
        wire_error_envelope={
            "adcp_error": {"code": code, "message": "boom", "recovery": recovery},
            "errors": [
                {
                    "code": errors_code if errors_code is not None else code,
                    "message": "boom",
                    "recovery": errors_recovery if errors_recovery is not None else recovery,
                }
            ],
        }
    )


class TestGradersTakeNoCode:
    """The signature IS the fix — a code parameter would reopen the tautology."""

    @pytest.mark.parametrize("grader", ["assert_wire_recovery", "assert_wire_is_adcp_envelope"])
    def test_grader_accepts_no_error_code_argument(self, grader):
        params = set(inspect.signature(getattr(TransportResult, grader)).parameters) - {"self"}
        assert params <= {"recovery"}, (
            f"{grader} grew parameter(s) {sorted(params - {'recovery'})}. These graders are code-free by "
            "design: a caller with no code expectation of its own can only satisfy a code parameter by "
            "reading one out of the envelope being graded, which is the unfailable arm this API removed."
        )


class TestAssertWireRecovery:
    def test_passes_when_both_layers_carry_the_expected_recovery(self):
        _result(recovery="correctable").assert_wire_recovery("correctable")

    def test_fails_when_the_recovery_differs(self):
        with pytest.raises(AssertionError, match="recovery"):
            _result(recovery="terminal").assert_wire_recovery("correctable")

    def test_fails_when_only_the_envelope_layer_matches(self):
        """Layer drift is the regression the two-layer invariant exists to catch."""
        with pytest.raises(AssertionError, match="errors\\[0\\].recovery"):
            _result(recovery="correctable", errors_recovery="terminal").assert_wire_recovery("correctable")

    def test_fails_when_the_layers_disagree_on_the_code(self):
        with pytest.raises(AssertionError, match="errors\\[0\\].code"):
            _result(errors_code="MEDIA_BUY_NOT_FOUND").assert_wire_recovery("correctable")

    def test_fails_when_no_wire_envelope_was_captured(self):
        with pytest.raises(AssertionError, match="no wire_error_envelope was captured"):
            TransportResult().assert_wire_recovery("correctable")


class TestAssertWireIsAdcpEnvelope:
    def test_passes_when_the_envelope_matches_the_pinned_classification(self):
        _result(code=_CORRECTABLE_CODE, recovery="correctable").assert_wire_is_adcp_envelope()

    def test_passes_for_a_non_correctable_code_too(self):
        """The expectation comes from the pinned enum per code, not a fixed literal."""
        _result(code=_TRANSIENT_CODE, recovery="transient").assert_wire_is_adcp_envelope()

    def test_fails_when_recovery_drifts_from_the_pinned_classification(self):
        with pytest.raises(AssertionError, match="recovery"):
            _result(code=_TRANSIENT_CODE, recovery="correctable").assert_wire_is_adcp_envelope()

    def test_fails_when_the_layers_disagree_on_the_code(self):
        with pytest.raises(AssertionError, match="errors\\[0\\].code"):
            _result(errors_code="MEDIA_BUY_NOT_FOUND").assert_wire_is_adcp_envelope()

    def test_fails_when_the_code_is_not_canonical(self):
        with pytest.raises(AssertionError, match="not a canonical AdCP error code"):
            _result(code=_NON_CANONICAL_CODE).assert_wire_is_adcp_envelope()

    def test_fails_when_the_envelope_layer_has_no_code(self):
        result = TransportResult(wire_error_envelope={"adcp_error": {"message": "boom"}, "errors": []})
        with pytest.raises(AssertionError, match="no envelope-level adcp_error.code"):
            result.assert_wire_is_adcp_envelope()

    def test_fails_on_a_500_or_non_adcp_body(self):
        """No envelope captured is exactly the shape this step is meant to reject."""
        with pytest.raises(AssertionError, match="no wire_error_envelope was captured"):
            TransportResult().assert_wire_is_adcp_envelope()
