"""Guard: the non-disclosure Then runs BOTH halves, including the id-echo half.

``then_error_no_reveal`` (uc004_delivery.py) grades @T-UC-004-ext-d's security
obligation: a non-owner asking about someone else's media buy must get
``media_buy_not_found``, worded so it cannot be told apart from a buy that never
existed. It has two halves — no leaking phrase in the message, and no repeated echo of
the requested id.

The second half never ran. It was gated on
``ctx.get("target_media_buy_id") or ctx.get("media_buy_id")``, and NO step in the tree
writes either key, so the id was always ``""`` and the ``if mb_id:`` skipped it
(salesagent-b9hi1.1). The step now reads the request as sent and fails when it is
absent rather than skipping.

THIS FILE EXISTS BECAUSE THE SCENARIO CANNOT GRADE THE REPAIR TODAY.
@T-UC-004-ext-d is a DELIBERATE xfail — "partial-success Error model needs suggestion
field — production enhancement" — so it is a spec-production-gap ledger entry that must
not be weakened to make it green, and its outcome cannot show whether this half runs.
These cases drive the step directly.
"""

from __future__ import annotations

import pytest

from tests.bdd.steps.domain.uc004_delivery import then_error_no_reveal


class _Error:
    """The shape ``_get_error_message`` reads: a ``message`` attribute."""

    def __init__(self, message: str) -> None:
        self.message = message

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"_Error({self.message!r})"


#: The id the buyer sent. A sentinel rather than a mutable default (B006), and NOT
#: ``None``: ``None`` already means "the step recorded nothing", which is the case the
#: unrecorded-request test drives, so the two cannot share a spelling.
_SENT_ID = "mb-other"


def _ctx(message: str, *, requested: list[str] | None | object = _SENT_ID) -> dict:
    ctx: dict = {"error": _Error(message)}
    if requested is _SENT_ID:
        ctx["requested_media_buy_ids"] = [_SENT_ID]
    elif requested is not None:
        ctx["requested_media_buy_ids"] = requested
    return ctx


def test_a_refusal_echoing_the_id_once_is_allowed():
    """Repeating what the buyer sent reveals nothing — the bound is on repetition."""
    then_error_no_reveal(_ctx("No media buy found for mb-other"))


def test_a_refusal_repeating_the_id_fails():
    """THE HALF THAT NEVER RAN. Two echoes start to read like confirmation."""
    with pytest.raises(AssertionError, match="repeatedly echoes media_buy_id"):
        then_error_no_reveal(_ctx("mb-other not found; mb-other is not accessible"))


def test_a_leaking_phrase_still_fails():
    """The half that did run must keep running."""
    with pytest.raises(AssertionError, match="leaks existence info"):
        then_error_no_reveal(_ctx("Media buy is owned by another principal"))


def test_an_unrecorded_request_fails_rather_than_skipping():
    """The defect itself: with nothing recorded the step used to skip the id half and
    pass. Skipping a security assertion silently is the outcome to prevent."""
    with pytest.raises(AssertionError, match="No requested_media_buy_ids recorded"):
        then_error_no_reveal(_ctx("mb-other not found; mb-other is not accessible", requested=None))
