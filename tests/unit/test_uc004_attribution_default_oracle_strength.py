"""Regression test: the BDD attribution-model oracle for the seller's platform
default is self-referential -- it imports the SAME constant production uses,
so it cannot catch a wrong default.

``_expected_attribution_model`` (tests/bdd/steps/domain/uc004_delivery.py)
returns ``requested_model or PLATFORM_DEFAULT_ATTRIBUTION_MODEL.value``,
importing the constant from ``src.core.tools.media_buy_delivery`` -- the
module under test. For the ``omitted``/``empty_object`` rows (the buyer
sends no model), this makes the "expected" side and the production output
the SAME expression: change the constant, and the oracle's expectation
moves in lockstep. Nothing external pins what the seller's platform default
value SHOULD be, so no BDD scenario can ever redden if the default silently
changes.
"""

from __future__ import annotations

from unittest.mock import patch

from adcp.types.generated_poc.enums.attribution_model import AttributionModel

from src.core.schemas import GetMediaBuyDeliveryRequest
from tests.bdd.steps.domain.uc004_delivery import _expected_attribution_model


def _ctx_with_omitted_attribution_window() -> dict:
    """A ctx as if the When step dispatched a request with no attribution_window."""
    req = GetMediaBuyDeliveryRequest()
    assert req.attribution_window is None
    return {"dispatched_request": req}


def test_expected_default_model_moves_lockstep_with_the_mutated_constant() -> None:
    """The oracle is self-referential: mutating the seller's platform default
    changes the 'expected' side identically, so a wrong default is invisible.

    The ``omitted``/``empty_object`` BDD rows would stay green even if the
    platform default silently changed to the wrong model, because the
    expected value is derived from the SUT's own constant, not an
    independent anchor.
    """
    ctx = _ctx_with_omitted_attribution_window()
    with patch(
        "src.core.tools.media_buy_delivery.PLATFORM_DEFAULT_ATTRIBUTION_MODEL",
        AttributionModel.data_driven,
    ):
        # The oracle happily reports "data_driven" as the expected value for a
        # request that never asked for one -- it has no independent anchor.
        assert _expected_attribution_model(ctx) == "data_driven"


def test_platform_default_attribution_model_is_pinned_to_last_touch() -> None:
    """An anchor independent of ``_expected_attribution_model`` itself: pins the
    seller's platform default with a deliberate assertion so a change of seller
    default reddens exactly this one line, instead of silently re-basing every
    omitted/empty_object attribution row with it.
    """
    from src.core.tools.media_buy_delivery import PLATFORM_DEFAULT_ATTRIBUTION_MODEL

    assert PLATFORM_DEFAULT_ATTRIBUTION_MODEL.value == "last_touch", (
        "PLATFORM_DEFAULT_ATTRIBUTION_MODEL changed — this is a deliberate seller-default "
        "decision, not incidental drift. If intentional, update this pin AND verify the "
        "BR-UC-004 attribution omitted/empty_object BDD rows still describe the new default "
        "correctly (they derive their expected value from this same constant and have no "
        "other anchor)."
    )
