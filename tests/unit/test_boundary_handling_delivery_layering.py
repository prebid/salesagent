"""Characterization tests for (MED-01/MED-05/CON-05/LR-01).

Guard the behavior of the generic ``then_boundary_handling_result`` step on the
DELIVERY domain path so that relocating that logic out of the generic
``then_payload`` module into ``uc004_delivery`` (via a boundary-handler registry)
preserves behavior exactly.

Importing ``uc004_delivery`` ensures its registered delivery handler is active
after the refactor (no-op before it). These call the real generic step with a
crafted ctx; they pass on the pre-refactor code and must keep passing after.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.exceptions import AdCPValidationError

# Importing the domain module registers its boundary handler post-refactor.
from tests.bdd.steps.domain import uc004_delivery  # noqa: F401
from tests.bdd.steps.generic.then_payload import then_boundary_handling_result
from tests.harness.wire_fixtures import wire_error_result
from tests.helpers.envelope_assertions import envelope_for

DELIVERY_FIELD = "reporting_dimensions"  # a delivery-domain boundary field


# ctx key for a payload produced WITHOUT dispatching, which is what these
# unit tests do: they drive a Then directly to exercise its assertion logic.
def _delivery_response(deliveries):
    return SimpleNamespace(media_buy_deliveries=deliveries)


def test_valid_delivery_boundary_with_deliveries_passes():
    ctx = {"self_dispatched_response": _delivery_response([SimpleNamespace(media_buy_id="mb1")])}
    then_boundary_handling_result(ctx, DELIVERY_FIELD, "valid")  # no raise


def test_valid_delivery_boundary_empty_deliveries_raises():
    ctx = {"self_dispatched_response": _delivery_response([])}
    with pytest.raises(AssertionError):
        then_boundary_handling_result(ctx, DELIVERY_FIELD, "valid")


def test_invalid_delivery_boundary_with_wire_rejection_passes():
    """The step now grades the WIRE, so the fixture must supply one.

    It used to hand-build ctx["error"] = AdCPSalesAgentError(INTERNAL_ERROR) -- fabricating
    the expected error, which is the antipattern
    test_architecture_bdd_wire_discipline bans in step definitions, and which also
    meant this characterization test asserted the step accepted a
    server-crash code as a "client rejection". After salesagent-3dawm.18 the step
    requires a real envelope, so the fixture builds one from a real rejection.
    """
    envelope = envelope_for(AdCPValidationError(field=DELIVERY_FIELD))
    # has_wire=True: the fixture stands in for a dispatch whose rejection was
    # CAPTURED off the wire, which is the state the step must read from. The
    # envelope is serialized from a production AdcpErrorResponse only so the
    # shape is genuine.
    ctx = {"result": wire_error_result(envelope)}
    then_boundary_handling_result(ctx, DELIVERY_FIELD, "invalid")  # no raise


def test_invalid_delivery_boundary_with_build_failure_passes():
    """The one no-wire outcome that is still acceptable: the request never built."""
    from pydantic import BaseModel
    from pydantic import ValidationError as PydanticValidationError

    class _Probe(BaseModel):
        n: int

    with pytest.raises(PydanticValidationError) as exc_info:
        _Probe(n="not-an-int")  # type: ignore[arg-type]

    ctx = {"error": exc_info.value}
    then_boundary_handling_result(ctx, DELIVERY_FIELD, "invalid")  # no raise


def test_invalid_delivery_boundary_without_error_raises():
    ctx = {"self_dispatched_response": _delivery_response([SimpleNamespace(media_buy_id="mb1")])}
    with pytest.raises(AssertionError):
        then_boundary_handling_result(ctx, DELIVERY_FIELD, "invalid")
