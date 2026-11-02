"""Guard: the attribution-echo Then grades the REQUEST, not a hardcoded constant.

``then_attribution_echo`` (uc004_delivery.py) claimed in its docstring to verify "the
echoed values match the request — not merely that they are non-None", and did the
opposite: it read ``ctx["request_attribution"]`` with fallbacks ``interval=7`` /
``unit="days"``, and NO step wrote that key. So it compared the response against two
constants which happened to equal @T-UC-004-attr-supported's Examples. A seller that
ignored the buyer's requested window entirely and always answered 7 days passed
(salesagent-b9hi1.1).

THIS FILE EXISTS BECAUSE THE SCENARIO CANNOT GRADE THE REPAIR TODAY.
@T-UC-004-attr-supported is red upstream on a production INTERNAL_ERROR from
/api/v1/media-buys/delivery (salesagent-ioxoc), red identically before and after the
repair, so execution never reaches this Then. These three cases drive the step directly
through the ``self_dispatched_response`` seam ``require_payload`` already honours. When
ioxoc lands the scenario grades it end to end and this file is still the cheaper place
to see WHICH discrimination is being made.

The middle case is the one that matters: a response echoing 7 days against a request for
30. The old code passed it — the key was absent, the default was 7, and 7 == 7.
"""

from __future__ import annotations

import types

import pytest

from tests.bdd.steps.domain.uc004_delivery import then_attribution_echo


def _response(interval: int, unit: str) -> types.SimpleNamespace:
    """The shape the step reads: deliveries present, attribution_window.post_click set."""
    return types.SimpleNamespace(
        media_buy_deliveries=[types.SimpleNamespace(media_buy_id="mb-001")],
        attribution_window=types.SimpleNamespace(
            post_click=types.SimpleNamespace(interval=interval, unit=unit),
        ),
    )


def _ctx(*, response, request_attribution=...) -> dict:
    ctx: dict = {"self_dispatched_response": response}
    if request_attribution is not ...:
        ctx["request_attribution"] = request_attribution
    return ctx


def test_an_echo_matching_the_request_passes():
    ctx = _ctx(
        response=_response(30, "days"),
        request_attribution={"post_click": {"interval": 30, "unit": "days"}},
    )
    then_attribution_echo(ctx)


def test_a_seller_that_ignores_the_request_and_answers_the_old_default_now_fails():
    """THE REGRESSION. Buyer asked for 30 days, seller answered 7 — the value the
    deleted fallback hardcoded. This is the case that used to pass."""
    ctx = _ctx(
        response=_response(7, "days"),
        request_attribution={"post_click": {"interval": 30, "unit": "days"}},
    )
    with pytest.raises(AssertionError, match="should echo the requested 30"):
        then_attribution_echo(ctx)


def test_a_unit_the_buyer_did_not_ask_for_fails():
    ctx = _ctx(
        response=_response(30, "days"),
        request_attribution={"post_click": {"interval": 30, "unit": "hours"}},
    )
    with pytest.raises(AssertionError, match="should echo the requested 'hours'"):
        then_attribution_echo(ctx)


def test_an_unrecorded_request_fails_rather_than_defaulting():
    """The absent-key branch is the defect itself: defaulting there is what turned an
    echo assertion into a comparison against a constant."""
    ctx = _ctx(response=_response(7, "days"))
    with pytest.raises(AssertionError, match="No request_attribution recorded"):
        then_attribution_echo(ctx)
