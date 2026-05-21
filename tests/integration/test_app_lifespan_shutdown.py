"""Integration regression tests for the FastAPI lifespan shutdown registry.

PR #1264 fix #3 wired the ``ProtocolWebhookService.close()`` (which releases a
long-lived ``requests.Session`` connection pool — real OS file descriptors)
into ``src.app.app_lifespan``'s shutdown phase. salesagent-x2h.6 inverted the
dependency: the service self-registers ``close`` via
``src.core.lifecycle.register_shutdown`` at first construction, and
``app_lifespan`` only calls ``run_all_shutdown_callbacks()`` — it never names a
concrete service.

These are INTEGRATION tests: they drive the real ASGI lifespan protocol
(``asgi_lifespan.LifespanManager`` over ``FastAPI(lifespan=app_lifespan)``) and
exercise the genuine production ``app_lifespan`` — including the real
``_install_admin_mounts()`` startup hook — with a REAL
``ProtocolWebhookService`` instance registered through the REAL lifecycle
registry. They assert on the real ``requests.Session`` connection-pool state,
never on a mock.

No production symbol is patched. ``app_lifespan``'s startup legitimately
mutates the module-global ``src.app.app`` route table. The
``isolated_global_app_state`` fixture snapshots and restores that global, the
webhook-service singleton, AND the lifecycle shutdown-callback registry so
running the real startup/registration does not leak into sibling tests.

Mutation coverage (verified against real objects):
  (a) drop the ``await`` on ``run_all_shutdown_callbacks()``  -> pool not released -> FAIL
  (b) skip ``register_shutdown`` on construction              -> close never called -> FAIL
  (c) remove the per-callback try/except in the registry      -> close error escapes -> FAIL

The companion AST guard ``tests/unit/test_architecture_app_lifespan_lazy_import.py``
pins the service-agnostic contract so (b)-style regressions also fail fast.
"""

from __future__ import annotations

import pytest
import requests
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

import src.app as app_module
from src.app import app_lifespan
from src.core import lifecycle
from src.services import protocol_webhook_service
from src.services.protocol_webhook_service import ProtocolWebhookService, get_protocol_webhook_service

pytestmark = pytest.mark.integration


@pytest.fixture
def isolated_global_app_state():
    """Snapshot/restore the legitimate global side-effects of lifespan startup.

    - ``src.app.app.router.routes``: ``_install_admin_mounts()`` (run for real,
      not stubbed) rewrites this module-global list.
    - ``protocol_webhook_service._webhook_service``: the documented singleton
      slot; ``get_protocol_webhook_service()`` populates it and self-registers.
    - ``lifecycle._shutdown_callbacks``: the service-agnostic registry the
      shutdown hook drains; self-registration appends to it.

    Restoring these afterwards keeps the real startup from polluting sibling
    tests without patching any production code.
    """
    original_routes = list(app_module.app.router.routes)
    original_singleton = protocol_webhook_service._webhook_service
    original_callbacks = list(lifecycle._shutdown_callbacks)
    try:
        yield
    finally:
        app_module.app.router.routes = original_routes
        protocol_webhook_service._webhook_service = original_singleton
        lifecycle._shutdown_callbacks[:] = original_callbacks


def _prime_real_connection_pool(session: requests.Session) -> object:
    """Force the real session to cache a connection pool WITHOUT any network I/O.

    ``HTTPAdapter.poolmanager.connection_from_url`` lazily creates and caches an
    ``HTTPConnectionPool`` in ``poolmanager.pools``; no socket is opened until an
    actual request is made. ``requests.Session.close()`` -> ``HTTPAdapter.close()``
    -> ``poolmanager.clear()`` empties that cache. Returning the live poolmanager
    lets the test assert the real pre/post state of the real object.
    """
    adapter = session.get_adapter("http://localhost")
    adapter.poolmanager.connection_from_url("http://localhost")
    return adapter.poolmanager


async def test_lifespan_closes_real_webhook_session_pool(isolated_global_app_state):
    """The real lifespan shutdown must release the real requests.Session pool.

    Constructs the REAL service through ``get_protocol_webhook_service()`` so it
    self-registers with the REAL lifecycle registry. Primes a real connection
    pool, runs the genuine ASGI lifespan (real startup including
    ``_install_admin_mounts``, then shutdown), and asserts the real poolmanager
    was emptied by production's ``run_all_shutdown_callbacks()`` -> ``close()``.

    Fails under mutation (a) (no await -> coroutine never runs -> pool intact)
    and mutation (b) (no register_shutdown -> close never invoked).
    """
    protocol_webhook_service._webhook_service = None
    lifecycle._shutdown_callbacks.clear()
    service = get_protocol_webhook_service()  # self-registers close

    poolmanager = _prime_real_connection_pool(service._session)
    assert len(poolmanager.pools) >= 1, "precondition: a real connection pool must be cached before shutdown"

    app = FastAPI(lifespan=app_lifespan)
    async with LifespanManager(app):
        # Startup ran the real _install_admin_mounts(); pool intact mid-lifespan.
        assert len(poolmanager.pools) >= 1, "pool must survive until shutdown"
    # Exiting LifespanManager ran the real shutdown phase -> real close().

    assert len(poolmanager.pools) == 0, (
        "FastAPI lifespan shutdown did not release the ProtocolWebhookService "
        "requests.Session connection pool. PR #1264 fix #3 regression: the real "
        f"poolmanager still holds {len(poolmanager.pools)} pool(s). The shutdown "
        "hook either did not await run_all_shutdown_callbacks() or the service "
        "never self-registered its close callback."
    )


async def test_lifespan_safe_when_no_callbacks_registered(isolated_global_app_state):
    """An empty registry must not break lifespan startup/shutdown.

    With no service constructed (and thus nothing registered), the genuine ASGI
    lifespan must complete cleanly — ``run_all_shutdown_callbacks()`` is a no-op
    on an empty registry (``LifespanManager`` not raising is the contract).
    """
    protocol_webhook_service._webhook_service = None
    lifecycle._shutdown_callbacks.clear()

    app = FastAPI(lifespan=app_lifespan)
    async with LifespanManager(app):
        pass

    assert protocol_webhook_service._webhook_service is None


class _RaisingCloseSession(requests.Session):
    """A REAL ``requests.Session`` whose ``close()`` raises — not a mock.

    Subclassing the real ``Session`` keeps every other behaviour real while
    letting the test prove the registry's per-callback ``try/except`` actually
    swallows a failing ``close()``. ``super().close()`` still runs (real pool
    release) before the simulated failure.
    """

    close_calls = 0

    def close(self) -> None:  # type: ignore[override]
        type(self).close_calls += 1
        super().close()
        raise RuntimeError("simulated requests.Session.close() failure")


async def test_lifespan_swallows_webhook_close_errors(isolated_global_app_state):
    """A failing ``close()`` must be logged and swallowed, never escape the lifespan.

    Registers a real ``ProtocolWebhookService`` whose real ``Session`` subclass
    raises in ``close()``. The registry's per-callback ``try/except`` must
    contain it so ``LifespanManager`` exits normally. Fails under mutation (c)
    (try/except removed -> ``RuntimeError`` propagates out of ``LifespanManager``).
    """
    protocol_webhook_service._webhook_service = None
    lifecycle._shutdown_callbacks.clear()
    service = ProtocolWebhookService()
    service._session = _RaisingCloseSession()
    protocol_webhook_service._webhook_service = service
    lifecycle.register_shutdown(service.close)
    _RaisingCloseSession.close_calls = 0

    app = FastAPI(lifespan=app_lifespan)
    # Must NOT raise: the registry wraps each callback in try/except.
    async with LifespanManager(app):
        pass

    assert _RaisingCloseSession.close_calls == 1, (
        "production shutdown must have invoked the real session.close() exactly "
        f"once; got {_RaisingCloseSession.close_calls} — the close callback was "
        "not registered/awaited"
    )
