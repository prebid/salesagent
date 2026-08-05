"""Characterization tests for salesagent-chit (MED-01/MED-05/CON-05/LR-01).

Guard the behavior of the generic ``then_boundary_handling_result`` step on the
DELIVERY domain path so that relocating that logic out of the generic
``then_payload`` module into ``uc004_delivery`` (via a boundary-handler registry)
preserves behavior exactly.

Importing ``uc004_delivery`` ensures its registered delivery handler is active
after the refactor (no-op before it). These call the real generic step with a
crafted ctx; they pass on the pre-refactor code and must keep passing after.

Updated for the wire-discipline migration: the delivery handler's "valid" branch now reads
``wire_dict(ctx)`` instead of ``getattr(response, "media_buy_deliveries")``, so
the crafted ctx carries a wire-shaped
``ctx["wire_response"]`` dict, not a typed ``SimpleNamespace``. The domain-routing
sniff (``hasattr(resp, "media_buy_deliveries")``) stays typed deliberately (see
``_delivery_boundary_handler``'s own comment — it is routing, not grading), so
``ctx["response"]`` is still a ``SimpleNamespace`` for that purpose. The "invalid"
branch now asserts a genuine wire-grounded client rejection (``_assert_wire_rejection``)
rather than a bare ``isinstance(error, AdCPError)`` check, so the crafted error must
carry a real non-``INTERNAL_ERROR`` code — ``AdCPValidationError`` (VALIDATION_ERROR),
matching what production actually raises for a rejected boundary value.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.exceptions import AdCPValidationError

# Importing the domain module registers its boundary handler post-refactor.
from tests.bdd.steps.domain import uc004_delivery  # noqa: F401
from tests.bdd.steps.generic.then_payload import then_boundary_handling_result

DELIVERY_FIELD = "reporting_dimensions"  # a delivery-domain boundary field


def _delivery_ctx(deliveries):
    """Typed response for the routing sniff + wire body for the grading read."""
    return {
        "response": SimpleNamespace(media_buy_deliveries=deliveries),
        "wire_response": {"media_buy_deliveries": deliveries},
    }


def test_valid_delivery_boundary_with_deliveries_passes():
    ctx = _delivery_ctx([{"media_buy_id": "mb1"}])
    then_boundary_handling_result(ctx, DELIVERY_FIELD, "valid")  # no raise


def test_valid_delivery_boundary_empty_deliveries_raises():
    ctx = _delivery_ctx([])
    with pytest.raises(AssertionError):
        then_boundary_handling_result(ctx, DELIVERY_FIELD, "valid")


def test_invalid_delivery_boundary_with_error_passes():
    ctx = {"error": AdCPValidationError("bad reporting_dimensions")}
    then_boundary_handling_result(ctx, DELIVERY_FIELD, "invalid")  # no raise


def test_invalid_delivery_boundary_without_error_raises():
    ctx = _delivery_ctx([{"media_buy_id": "mb1"}])
    with pytest.raises(AssertionError):
        then_boundary_handling_result(ctx, DELIVERY_FIELD, "invalid")
