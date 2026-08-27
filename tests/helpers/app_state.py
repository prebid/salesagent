"""Snapshot/restore the process-global state that starting the app lifespan mutates.

WHY THIS EXISTS — a measured cross-file failure, not a hypothetical
------------------------------------------------------------------
``src.app.app`` is a module-level singleton, and its startup hook
``_install_admin_hooks``-style side effects are NOT undone by the shutdown hook.
The one that bites is the route table: ``src.app._install_admin_mounts()`` runs at
lifespan STARTUP and appends the Flask WSGI catch-all ``Mount("")``, which matches
EVERY path. Measured against the real app:

===================  ==============================  ==============================
``GET``              before any lifespan started     after one lifespan started
===================  ==============================  ==============================
``/mcp``             ``307`` -> ``/mcp/``            ``404`` text/html (Flask admin)
``/a2a``             ``405`` (route exists, POST)    ``404`` text/html (Flask admin)
===================  ==============================  ==============================

Both flips have the same cause: ``Mount("")`` returns ``Match.FULL``, so Starlette's
router never reaches its ``redirect_slashes`` fallback (which is what issued the
``/mcp`` -> ``/mcp/`` ``307``) and never falls back to its remembered ``PARTIAL``
match (which is what issued the ``/a2a`` ``405``).

Since ``tox`` runs the integration suite with ``--dist loadfile``, WHICH files share a
worker process varies between runs — so any test that starts the lifespan silently
re-shapes the app for every later file on that worker. That is how a green suite turned
red with no source change (``salesagent-66a1``): the trust-root suite asks the running
app which path a counterparty lands on, and a leaked catch-all changes the answer.

A test that starts the real lifespan therefore MUST restore. Use this context manager
rather than re-deriving the snapshot list per call site: the set of globals startup
touches is one fact, and two copies of it drift.

THE SECOND START (salesagent-n78j0.1.1)
---------------------------------------
``src.app.mcp_app`` is built ONCE at import, so the ``StreamableHTTPSessionManager``
behind the ``/mcp`` mount is a process singleton — and its ``run()`` deliberately
refuses a second call ("can only be called once per instance"). Left alone, the FIRST
test in a process that starts the lifespan silently makes the app un-startable for
every later one: measured as a ``RuntimeError`` on the second signed-MCP dispatch,
not as anything resembling its cause. Restarting is exactly the "restore what startup
mutated" contract this module already owns, so the one-shot latch is cleared here
rather than at each call site.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def preserved_global_app_state() -> Iterator[None]:
    """Restore the app globals a real lifespan startup/shutdown mutates.

    * ``src.app.app.router.routes`` — ``_install_admin_mounts()`` rewrites this list
      at startup (see the module docstring for the routing consequences).
    * ``protocol_webhook_service._webhook_service`` — the documented singleton slot;
      ``get_protocol_webhook_service()`` populates it and self-registers a shutdown
      callback. Left alone, a lifespan SHUTDOWN closes that service's connection pool
      and hands the next file on the worker a closed client.
    * ``lifecycle._shutdown_callbacks`` — the service-agnostic registry the shutdown
      hook drains.
    * the ``/mcp`` session manager's one-shot ``run()`` latch — see the module
      docstring's "second start" section.

    Restores by rebinding/slicing rather than by re-running production setup, so it
    stays correct whether or not the body actually reached startup.
    """
    import src.app as app_module
    from src.core import lifecycle
    from src.services import protocol_webhook_service

    original_routes = list(app_module.app.router.routes)
    original_singleton = protocol_webhook_service._webhook_service
    original_callbacks = list(lifecycle._shutdown_callbacks)
    try:
        yield
    finally:
        app_module.app.router.routes = original_routes
        protocol_webhook_service._webhook_service = original_singleton
        lifecycle._shutdown_callbacks[:] = original_callbacks
        _rearm_mcp_session_manager()


def _rearm_mcp_session_manager() -> None:
    """Clear the ``/mcp`` session manager's "already ran" latch.

    ``StreamableHTTPSessionManager.run()`` unwinds everything else itself on exit
    (task group cancelled, ``_task_group`` cleared, session and owner maps
    emptied); the only thing it deliberately does NOT reset is the flag that makes
    a second ``run()`` raise, because upstream expects a fresh instance per run and
    a long-lived server never needs one. We cannot supply a fresh instance — the
    lifespan closure in ``fastmcp.server.http`` captured this one, and the app is
    built at import — so the latch is cleared instead. Nothing else is touched.

    A no-op when the mount's shape changes: the manager is located through the
    route rather than assumed, so this degrades to doing nothing rather than to
    an AttributeError far from any call site.
    """
    import src.app as app_module

    for route in getattr(app_module.mcp_app, "routes", []):
        manager = getattr(getattr(route, "endpoint", None), "session_manager", None)
        if manager is not None:
            manager._has_started = False
