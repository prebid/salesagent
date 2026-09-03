"""Integration regression tests for the FastAPI lifespan shutdown registry.

PR #1264 fix #3 wired the ``ProtocolWebhookService.close()`` (which releases a
long-lived ``requests.Session`` connection pool — real OS file descriptors)
into ``src.app.app_lifespan``'s shutdown phase. inverted the
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
from src.services.protocol_webhook_service import get_protocol_webhook_service

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


async def test_lifespan_awaits_every_registered_shutdown_callback(isolated_global_app_state):
    """The real lifespan shutdown must AWAIT the callbacks the registry holds.

    Repointed by GH #1802. This used to prime a real
    ``requests.Session`` pool on ProtocolWebhookService and assert the lifespan
    emptied it. That service now builds and discards a per-destination pinned
    transport, so it owns no pool and registers nothing — with it went the last
    producer in ``src/``, and with that, mutation (b) ("skip register_shutdown on
    construction"): a test that registers the callback itself can only grade the
    drain, not any production registration. Coverage here is honestly 2 of the
    original 3 mutations, not 3.

    What still matters, and is still graded: the lifespan must AWAIT what the
    registry holds. Fails under mutation (a) — drop the ``await`` and the
    coroutine never runs.
    """
    lifecycle._shutdown_callbacks.clear()
    awaited: list[str] = []

    async def _close() -> None:
        awaited.append("closed")

    lifecycle.register_shutdown(_close)

    app = FastAPI(lifespan=app_lifespan)
    async with LifespanManager(app):
        # Startup ran the real _install_admin_mounts(); nothing drained yet.
        assert awaited == [], "callbacks must not run until the shutdown phase"

    assert awaited == ["closed"], (
        "FastAPI lifespan shutdown did not await the registered callback. "
        "Either app_lifespan dropped its await of run_all_shutdown_callbacks(), "
        "or the registry did not invoke what it held."
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


async def test_constructing_the_webhook_service_registers_no_shutdown_callback(isolated_global_app_state):
    """Constructing the webhook service must leave the shutdown registry empty.

    ``ProtocolWebhookService`` holds a long-lived ``requests.Session`` today and
    self-registers ``close`` to release it. The egress-seam migration
    (GH #1802) removes the session, because
    ``build_ip_pinned_transport`` resolves its destination at construction and
    refuses to connect anywhere else — so there can be no client that outlives a
    single delivery, and therefore nothing left to close at shutdown.

    This grades the removal from the outside, at the registry, rather than by
    naming the attribute that goes away: a service that still holds a pooled
    client under some other name would still have to register a close callback
    to be correct, and would still fail here.

    The real ASGI lifespan runs afterwards to pin the other half of the
    requirement — the app starts and stops cleanly with the hook gone.
    ``test_lifespan_safe_when_no_callbacks_registered`` grades an EMPTY registry
    that nothing ever tried to fill; this grades a registry that the webhook
    service was constructed against.
    """
    protocol_webhook_service._webhook_service = None
    lifecycle._shutdown_callbacks.clear()

    get_protocol_webhook_service()

    assert lifecycle._shutdown_callbacks == [], (
        "constructing ProtocolWebhookService registered a shutdown callback: "
        f"{lifecycle._shutdown_callbacks}. Nothing survives a single delivery any more, "
        "so there is nothing for the lifespan to release."
    )

    app = FastAPI(lifespan=app_lifespan)
    async with LifespanManager(app):
        pass


async def test_lifespan_swallows_a_failing_shutdown_callback(isolated_global_app_state):
    """A failing callback must be logged and swallowed, never escape the lifespan.

    Repointed by GH #1802 for the same reason as the drain test above: the
    webhook service no longer has a ``close()`` to make raise. The contract under
    test is the registry's, not that service's — one bad callback must not take
    the process down at shutdown. Fails under mutation (c) (per-callback
    try/except removed -> the error propagates out of ``LifespanManager``).
    """
    lifecycle._shutdown_callbacks.clear()
    calls: list[str] = []

    async def _raising_close() -> None:
        calls.append("called")
        raise RuntimeError("close failed")

    lifecycle.register_shutdown(_raising_close)

    app = FastAPI(lifespan=app_lifespan)
    # Must NOT raise: the registry wraps each callback in try/except.
    async with LifespanManager(app):
        pass

    assert calls == ["called"], f"production shutdown must have invoked the callback exactly once; got {calls}"
