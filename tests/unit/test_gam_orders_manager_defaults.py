"""Pin the safety-budget defaults on ``GAMOrdersManager.approve_order``.

The synchronous ``approve_order`` is called from the admin handoff path
in ``execute_approved_media_buy`` with ``max_retries=1``. The defaults
are the **fallback** for callers that don't override them. They were
deliberately lowered from the legacy ``max_retries=40, poll_interval=15``
(a 10-minute block) to ``max_retries=12, poll_interval=10`` (a 2-minute
budget that hands off to background polling on failure).

Reverting any of these defaults silently passes CI today — there is no
direct test that calls ``approve_order()`` with default args. This file
adds an introspection-based pin so the defaults cannot drift without a
visible test failure.

If you legitimately change a default (e.g. tune the safety budget),
update the expected values here in the same commit. The pin is the
forcing function for that explicit decision.
"""

from __future__ import annotations

import inspect

import pytest


@pytest.fixture
def approve_order_method():
    """Bound to the production method to limit import side-effects to one place."""
    from src.adapters.gam.managers.orders import GAMOrdersManager

    return GAMOrdersManager.approve_order


class TestApproveOrderDefaultsArePinned:
    """The ``approve_order`` defaults form a runtime budget contract.

    A buyer who hits ``execute_approved_media_buy`` blocks the admin
    request thread for at most ``max_retries * poll_interval`` seconds
    on the synchronous path (with the ``max_retries=1`` override).
    The ``@timeout`` decorator's ``seconds`` argument is the hard ceiling
    that fires if the GAM client wedges.

    Pin the three values together so any one of them moving is visible.
    """

    def test_max_retries_default_is_pinned(self, approve_order_method):
        """``approve_order(order_id)`` defaults to ``max_retries=12``.

        Reverting to the legacy ``40`` would extend the **fallback** budget
        from 2 minutes to 10 minutes for any caller that forgets the
        ``max_retries=1`` override. The admin handoff in
        ``execute_approved_media_buy`` would still pass ``1``, but any
        future caller leaning on defaults would block 5x longer.
        """
        sig = inspect.signature(approve_order_method)
        assert sig.parameters["max_retries"].default == 12, (
            f"approve_order(max_retries=…) default changed to "
            f"{sig.parameters['max_retries'].default}; expected 12 "
            "(the 2-minute fallback budget). Reverting to 40 would block the "
            "admin request 5x longer on any caller that doesn't override."
        )

    def test_poll_interval_default_is_pinned(self, approve_order_method):
        """``approve_order(order_id)`` defaults to ``poll_interval=10``.

        Reverting to ``15`` would similarly extend the per-retry wait window.
        Pin both ``max_retries`` and ``poll_interval`` together — the product
        determines the wall-clock budget.
        """
        sig = inspect.signature(approve_order_method)
        assert sig.parameters["poll_interval"].default == 10, (
            f"approve_order(poll_interval=…) default changed to "
            f"{sig.parameters['poll_interval'].default}; expected 10 seconds. "
            "Mutating this raises the per-retry sleep and extends the wall-clock budget."
        )

    def test_timeout_decorator_ceiling_is_pinned(self, approve_order_method):
        """The ``@timeout(seconds=130)`` ceiling on ``approve_order`` is pinned.

        ``@timeout`` is implemented in ``src/adapters/utils/timeout.py``: the
        decorator returns a wrapper whose closure captures ``seconds``. We
        extract that value from the wrapper's closure cells so future
        decorator changes don't silently re-roll the ceiling.

        The expected value (130s) is ``max_retries * poll_interval`` (120s)
        plus a small head-room buffer for GAM client overhead. Reverting to
        the legacy ``620s`` would let a wedged GAM client block the admin
        request for 10 minutes before the timeout fires.
        """
        closure_seconds = self._extract_timeout_seconds(approve_order_method)
        assert closure_seconds == 130, (
            f"@timeout(seconds=…) on approve_order changed to {closure_seconds}; "
            "expected 130 (max_retries * poll_interval + headroom). "
            "Reverting to 620 would let a wedged GAM client block the admin "
            "request for 10 minutes before the hard ceiling fires."
        )

    @staticmethod
    def _extract_timeout_seconds(decorated_method) -> int:
        """Pull the ``seconds`` value out of the ``@timeout`` decorator's closure.

        Defensive against decorator changes: scans all closure cells for the
        first ``int`` value, which is what ``timeout(seconds=…)`` captures.
        Raises ``AssertionError`` with diagnostic info if the decorator no
        longer carries an int in its closure — that surfaces a structural
        change to the decorator itself, not a default-value drift.
        """
        closure = getattr(decorated_method, "__closure__", None)
        assert closure is not None, (
            "approve_order is no longer decorated with @timeout, or the "
            "decorator no longer uses a closure. The runtime ceiling pin "
            "is structurally broken — verify the decorator implementation."
        )

        int_cells = []
        for cell in closure:
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if isinstance(value, int) and not isinstance(value, bool):
                int_cells.append(value)

        assert len(int_cells) == 1, (
            f"Expected exactly one int in @timeout closure (the seconds value), "
            f"found {int_cells}. The decorator's closure shape may have changed; "
            "update this pin alongside the decorator change."
        )
        return int_cells[0]
