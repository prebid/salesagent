"""Transport-aware realization of env mock-setup intents (#1418).

BDD step definitions call ``env.set_adapter_response(...)`` /
``env.set_registry_formats(...)`` once. In-process transports inject a
``MagicMock``; e2e transports must realize the same intent on the real server
surface (a DB row, a fixture-subset validation, ...).

This module provides the single dispatch seam so that each setup method has
exactly one in-process branch and one e2e branch — no per-method ``if/elif``
chains and no copy-pasted dispatch logic.

Usage::

    class DeliveryPollMixin:
        @realize_e2e(_persist_simulation_config)
        def _realize_adapter_response(self, resp):
            self._adapter_responses[resp.media_buy_id] = resp  # in-process

        @realize_e2e(e2e_unsupported("no server fault-injection surface"))
        def set_adapter_error(self, exception):
            self.mock["adapter"].return_value.get_media_buy_delivery.side_effect = exception

``env.is_e2e`` selects the branch at call time.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from tests.harness._base import BaseTestEnv

F = TypeVar("F", bound="Callable[..., Any]")


class E2EUnsupportedSetup(Exception):
    """A setup intent that has no realization on the live server surface.

    Raised by an env mock-setup method when the test asks for a configuration
    the real stack cannot be coerced into (adapter fault injection, an empty
    creative catalog, a format the agent does not serve). This IS the
    "impl-only setup" declaration — the reason lives at the env method, not in
    a nodeid ledger. The BDD ``pytest_runtest_makereport`` hook translates it
    into a non-strict xfail carrying ``reason``.

    Attributes:
        method_name: The env method that could not realize the intent.
        reason: Human-readable explanation, surfaced in the xfail report.
    """

    def __init__(self, reason: str, method_name: str | None = None) -> None:
        self.reason = reason
        self.method_name = method_name
        suffix = f" [{method_name}]" if method_name else ""
        super().__init__(f"{reason}{suffix}")


def realize_e2e(e2e_impl: Callable[..., Any]) -> Callable[[F], F]:
    """Decorate an in-process setup method with an e2e realization.

    The wrapped method keeps the in-process behavior (MagicMock injection).
    ``e2e_impl`` is invoked instead — with the same ``(self, *args, **kwargs)``
    — when ``self.is_e2e`` is true. One decorator, no branching at each call
    site; both branches consume the SAME normalized arguments the caller
    passed.

    Args:
        e2e_impl: ``(self, *args, **kwargs) -> Any`` realizing the intent on
            the server surface. Use :func:`e2e_unsupported` for intents with no
            surface.
    """

    def _decorate(in_process_impl: F) -> F:
        @functools.wraps(in_process_impl)
        def _dispatch(self: BaseTestEnv, *args: Any, **kwargs: Any) -> Any:
            if not self.is_e2e:
                return in_process_impl(self, *args, **kwargs)
            try:
                return e2e_impl(self, *args, **kwargs)
            except E2EUnsupportedSetup as exc:
                # Name the env method that could not realize the intent, so the
                # xfail report points at the call site, not the inner helper.
                if exc.method_name is None:
                    exc.method_name = in_process_impl.__name__
                raise

        return _dispatch  # type: ignore[return-value]

    return _decorate


def e2e_unsupported(reason: str) -> Callable[..., Any]:
    """Return an ``e2e_impl`` that declares the intent unrealizable over e2e.

    The returned callable raises :class:`E2EUnsupportedSetup` carrying
    ``reason``. :func:`realize_e2e` fills in the decorated method's name. Used
    as the ``e2e_impl`` argument to :func:`realize_e2e`.
    """

    def _raise(self: BaseTestEnv, *args: Any, **kwargs: Any) -> Any:
        raise E2EUnsupportedSetup(reason)

    return _raise
