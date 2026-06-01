"""Service-agnostic shutdown-callback registry.

PR #1264 fix #3 hardcoded the webhook-service shutdown in ``app_lifespan``.
That guaranteed ``app_lifespan`` would grow one try/except per closable
service — an inverted dependency where a transport-layer file (``src/app.py``)
must be edited every time a service needs teardown.

This module inverts the dependency: each service self-registers an async
shutdown callback at first construction via :func:`register_shutdown`, and
``app_lifespan`` only calls :func:`run_all_shutdown_callbacks` — it never
references a specific service.

Pinned contract (kept deliberately simple — the process lifespan fires once):
  - Callbacks MUST be async (``Callable[[], Awaitable[None]]``). Sync
    callbacks are not supported.
  - Callbacks run in registration order.
  - An exception in one callback is logged (via ``logger.exception``) and
    swallowed so subsequent callbacks still run.
  - :func:`run_all_shutdown_callbacks` is NOT idempotent — it does not clear
    the registry, so calling it again re-runs every callback. Lifespan only
    fires once per process, so this is acceptable and avoids surprising
    "callbacks vanished" behavior in tests/reload scenarios.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

_shutdown_callbacks: list[Callable[[], Awaitable[None]]] = []


def register_shutdown(callback: Callable[[], Awaitable[None]]) -> None:
    """Register an async callback to run on FastAPI lifespan shutdown.

    Callbacks run in registration order. Exceptions in one callback are
    logged but do not prevent other callbacks from running. Safe to call at
    module import time or inside lazy initializers (e.g. a service's
    get-or-create singleton accessor).
    """
    _shutdown_callbacks.append(callback)


async def run_all_shutdown_callbacks() -> None:
    """Run every registered shutdown callback, swallowing per-callback errors.

    Not idempotent: the registry is not cleared, so a second call re-runs
    all callbacks (see module docstring for rationale).
    """
    for cb in _shutdown_callbacks:
        try:
            await cb()
        except Exception:
            logger.exception("Shutdown callback failed", extra={"cb": getattr(cb, "__name__", repr(cb))})
