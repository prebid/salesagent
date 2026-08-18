"""Assertion helpers for tests that inspect a patched logger's recorded calls."""

from __future__ import annotations

from unittest.mock import Mock


def rendered_log_calls(mock_method: Mock) -> list[str]:
    """Render a patched logger method's recorded calls to their final messages.

    Lazy-logging call sites pass the format args separately
    (``logger.warning("dropping %s", name)``) rather than pre-formatting, so the recorded
    ``call.args`` are ``(fmt, *args)``. This substitutes them (``fmt % args``) so a test can
    assert on the exact text an operator would see.

    The ``%`` substitution is guarded on the presence of format args — exactly stdlib
    ``LogRecord.getMessage``'s ``if self.args`` — so a legal zero/one-arg call carrying a
    literal ``%`` (e.g. ``logger.warning("scanned 100% of rows")``) cannot raise a
    ``TypeError`` that would abort the caller and mask the real assertion.

    Pass the bound method, e.g. ``rendered_log_calls(mock_logger.warning)``.
    """
    return [
        (call.args[0] % call.args[1:]) if len(call.args) > 1 else call.args[0] for call in mock_method.call_args_list
    ]
