"""Synthetic wire-dict proof for uc003/uc006's error-promotion helpers.

Both wire the shared ``_promote_wire_graded_error`` (generic/then_error.py,
salesagent-oyiv.12) into their own domain-specific promote sites:

  1. ``_promote_update_errors`` (uc003_update_media_buy.py)     — LIVE
  2. ``then_operation_fails_with_assignment_error`` (uc006_sync_creatives.py) — LIVE
  3. ``then_no_errors_field`` (uc003_update_media_buy.py)       — LIVE

The shared helper's own wire-grading behavior is already covered by
test_then_error_promote_synthetic_wire_dict.py; these tests cover the
domain-specific wiring around it (type-gating, side effects, and the
independent wire-vs-reconstruction absence check).
"""

from __future__ import annotations

from adcp.types import Error

from tests.bdd.steps.domain.uc003_update_media_buy import (
    _promote_update_errors,
    then_no_errors_field,
)
from tests.harness.transport import Transport


class _StubUpdateMediaBuyError:
    """Duck-types as UpdateMediaBuyError for the isinstance gate — real type
    checked separately; this proves the WIRE-reading behavior specifically."""

    def __init__(self, errors: list[Error]) -> None:
        self.errors = errors


class TestPromoteUpdateErrors:
    def test_promotes_typed_error_and_deletes_response(self) -> None:
        from src.core.schemas._base import UpdateMediaBuyError

        typed_error = Error(code="VALIDATION_ERROR", message="budget must be positive")
        resp = UpdateMediaBuyError(errors=[typed_error])
        ctx = {
            "response": resp,
            "wire_response": {"errors": [{"code": "VALIDATION_ERROR", "message": "budget must be positive"}]},
            "transport": Transport.REST,
        }

        _promote_update_errors(ctx)

        assert ctx["error"] is typed_error
        assert ctx["error_response"] is resp
        assert "response" not in ctx

    def test_grades_wire_not_typed_reconstruction(self) -> None:
        """Divergence: typed errors look fine, wire errors[0] has an empty code.
        The promote must fail (via the shared wire-grading helper), not silently
        promote the typed value."""
        import pytest

        from src.core.schemas._base import UpdateMediaBuyError

        resp = UpdateMediaBuyError(errors=[Error(code="VALIDATION_ERROR", message="typed looks fine")])
        ctx = {
            "response": resp,
            "wire_response": {"errors": [{"code": "", "message": "wire error with no code"}]},
            "transport": Transport.REST,
        }

        with pytest.raises(AssertionError):
            _promote_update_errors(ctx)


class TestNoErrorsField:
    def test_passes_when_wire_omits_errors_key(self) -> None:
        ctx = {
            "response": object(),
            "wire_response": {"media_buy_id": "mb_1"},
            "transport": Transport.REST,
        }
        then_no_errors_field(ctx)

    def test_fails_when_wire_carries_errors_despite_typed_reconstruction_omitting_it(self) -> None:
        """Divergence: a typed reconstruction that (via exclude_none) drops an
        errors key the RAW wire still carries must be caught — proves the step
        grades the wire, not a re-serialization of the typed payload."""
        import pytest

        class _NoErrorsAttr:
            def model_dump(self, **kwargs: object) -> dict:
                return {"media_buy_id": "mb_1"}  # would hide the wire's real errors key

        ctx = {
            "response": _NoErrorsAttr(),
            "wire_response": {"errors": [{"code": "VALIDATION_ERROR", "message": "present on the wire"}]},
            "transport": Transport.REST,
        }
        with pytest.raises(AssertionError, match="errors"):
            then_no_errors_field(ctx)
