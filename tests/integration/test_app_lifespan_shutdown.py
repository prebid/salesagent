"""Integration regression tests for the FastAPI lifespan shutdown registry.

PR #1264 fix #3 wired the ``ProtocolWebhookService.close()`` (which releases a
long-lived ``httpx.AsyncClient`` connection pool — real OS file descriptors)
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
registry. They assert on the real ``httpx.AsyncClient`` state, never on a mock.

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

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from src.app import app_lifespan
from src.core import lifecycle
from src.services import protocol_webhook_service
from src.services.protocol_webhook_service import ProtocolWebhookService, get_protocol_webhook_service
from tests.helpers.app_state import preserved_global_app_state

pytestmark = pytest.mark.integration


@pytest.fixture
def isolated_global_app_state():
    """Snapshot/restore the legitimate global side-effects of lifespan startup.

    Which globals, and why each one leaks, is documented once in
    :mod:`tests.helpers.app_state` — every test that starts the real lifespan needs the
    same restore, and a second hand-rolled copy of the list drifts (it already did:
    ``test_signing_conformance_vectors.py`` had none, and its leaked route table broke
    the trust-root suite whenever ``--dist loadfile`` put them on one worker).
    """
    with preserved_global_app_state():
        yield


async def test_lifespan_closes_real_webhook_session_pool(isolated_global_app_state):
    """The real lifespan shutdown must release the real httpx.AsyncClient pool.

    Constructs the REAL service through ``get_protocol_webhook_service()`` so it
    self-registers with the REAL lifecycle registry, runs the genuine ASGI
    lifespan (real startup including ``_install_admin_mounts``, then shutdown),
    and asserts the real client was closed by production's
    ``run_all_shutdown_callbacks()`` -> ``close()``.

    ``is_closed`` is real state on the real client — ``aclose()`` sets it and
    nothing else does — so this fails under mutation (a) (no await -> coroutine
    never runs -> client still open) and mutation (b) (no register_shutdown ->
    close never invoked).
    """
    protocol_webhook_service._webhook_service = None
    lifecycle._shutdown_callbacks.clear()
    service = get_protocol_webhook_service()  # self-registers close

    assert isinstance(service._client, httpx.AsyncClient)
    assert not service._client.is_closed, "precondition: the real client must be open before shutdown"

    app = FastAPI(lifespan=app_lifespan)
    async with LifespanManager(app):
        # Startup ran the real _install_admin_mounts(); client intact mid-lifespan.
        assert not service._client.is_closed, "client must survive until shutdown"
    # Exiting LifespanManager ran the real shutdown phase -> real close().

    assert service._client.is_closed, (
        "FastAPI lifespan shutdown did not release the ProtocolWebhookService "
        "httpx.AsyncClient connection pool. PR #1264 fix #3 regression: the shutdown "
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


class _RaisingCloseClient(httpx.AsyncClient):
    """A REAL ``httpx.AsyncClient`` whose ``aclose()`` raises — not a mock.

    Subclassing the real client keeps every other behaviour real while letting
    the test prove the registry's per-callback ``try/except`` actually swallows a
    failing ``close()``. ``super().aclose()`` still runs (real pool release)
    before the simulated failure.
    """

    close_calls = 0

    async def aclose(self) -> None:  # type: ignore[override]
        type(self).close_calls += 1
        await super().aclose()
        raise RuntimeError("simulated httpx.AsyncClient.aclose() failure")


async def test_lifespan_swallows_webhook_close_errors(isolated_global_app_state):
    """A failing ``close()`` must be logged and swallowed, never escape the lifespan.

    Registers a real ``ProtocolWebhookService`` whose real ``AsyncClient``
    subclass raises in ``aclose()``. The registry's per-callback ``try/except`` must
    contain it so ``LifespanManager`` exits normally. Fails under mutation (c)
    (try/except removed -> ``RuntimeError`` propagates out of ``LifespanManager``).
    """
    protocol_webhook_service._webhook_service = None
    lifecycle._shutdown_callbacks.clear()
    service = ProtocolWebhookService()
    service._client = _RaisingCloseClient()
    protocol_webhook_service._webhook_service = service
    lifecycle.register_shutdown(service.close)
    _RaisingCloseClient.close_calls = 0

    app = FastAPI(lifespan=app_lifespan)
    # Must NOT raise: the registry wraps each callback in try/except.
    async with LifespanManager(app):
        pass

    assert _RaisingCloseClient.close_calls == 1, (
        "production shutdown must have invoked the real client.aclose() exactly "
        f"once; got {_RaisingCloseClient.close_calls} — the close callback was "
        "not registered/awaited"
    )
