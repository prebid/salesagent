"""Process-wide idempotency store for the greenfield core/ platforms.

Wraps :class:`adcp.server.idempotency.IdempotencyStore` with a
:class:`PgBackend` that survives across workers — required for any
deployment running ≥2 processes (which managed-mode and the legacy
gunicorn topology both do).

The pool + store are built lazily on first access, so module import
remains side-effect-free for tooling that pulls in core.platforms.* for
introspection (OpenAPI export, alembic autogen, etc.) without a live
DATABASE_URL.

Test-only: set ``CORE_IDEMPOTENCY_BACKEND=memory`` to fall back to
:class:`MemoryBackend`. Used by storyboard tests where spinning up a Pg
pool for every assertion is overkill.

**Atomicity caveat (SDK #555).** :meth:`PgBackend.put` commits on a
fresh pool connection — separate from the handler's business
transaction. Handlers that mutate state without a unique constraint on
their ``idempotency_key`` (e.g. ``_create_media_buy_impl`` inserting
into ``media_buys``) may double-execute on a crash between handler
success and cache commit. Tracked separately; PgBackend is still the
right default — it's a strict improvement over MemoryBackend's
single-process scope.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from adcp.decisioning import AdcpError
from adcp.exceptions import IdempotencyConflictError
from adcp.server.idempotency import IdempotencyStore, MemoryBackend, PgBackend

logger = logging.getLogger(__name__)


def translate_idempotency_conflict[F: Callable[..., Awaitable[Any]]](handler: F) -> F:
    """Decorator: translate framework :class:`IdempotencyConflictError` to a
    wire-shaped :class:`AdcpError` with code ``IDEMPOTENCY_CONFLICT`` and
    ``recovery="correctable"``.

    The framework's :class:`IdempotencyStore.wrap` raises
    :class:`IdempotencyConflictError` when the same idempotency_key is reused
    with a materially different (post-JCS-canonicalization) payload — a
    spec-defined buyer mistake, not a server failure. Without translation the
    untyped exception bubbles through the dispatcher's generic catch-all and
    surfaces as ``INTERNAL_ERROR`` (terminal), discarding the spec's
    correctable classification and breaking buyer-side retry-with-fresh-key
    recovery.

    Spec invariant — do NOT special-case correctable-rejection caching: the
    framework caches by ``(caller_identity, key, JCS(payload))``, and AdCP
    treats the idempotency_key as identifying an *attempt*, not an *intent*.
    After any non-success response (including correctable rejections like
    ``TERMS_REJECTED``), the spec answer is for the buyer to mint a fresh
    ``uuid.uuid4()`` and retry — not for the seller to invalidate cache
    entries based on the response classification. Trying to "fix" that
    breaks the JCS-payload-equality invariant.

    Apply this decorator OUTSIDE the ``@_IDEMPOTENCY.wrap`` decorator on every
    platform method that uses idempotency caching::

        @translate_idempotency_conflict
        @_IDEMPOTENCY.wrap
        async def create_media_buy(self, req, ctx) -> dict[str, Any]:
            ...

    Decorator stacking order matters: ``@_IDEMPOTENCY.wrap`` must wrap the raw
    method first (innermost), then ``@translate_idempotency_conflict`` wraps
    the wrap (outermost) so it sees the conflict exception when the framework
    invokes the platform method.
    """

    @functools.wraps(handler)
    async def _wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await handler(*args, **kwargs)
        except IdempotencyConflictError as exc:
            # AdCP L1/security idempotency rule: the IDEMPOTENCY_CONFLICT body
            # MUST NOT include a ``field`` json-pointer (even a generic one)
            # to preserve oracle resistance — a pointer reveals schema shape
            # and gives attackers a probing signal. Omit ``field`` here so the
            # wire envelope carries only code + message + recovery.
            raise AdcpError(
                "IDEMPOTENCY_CONFLICT",
                message=(
                    "idempotency_key reused with a different payload — either "
                    "resend the exact original payload or mint a fresh "
                    "uuid.uuid4() key and retry"
                ),
                recovery="correctable",
            ) from exc

    # The framework's boot-time ``validate_idempotency_wiring`` calls
    # ``is_wrapped(fn)`` on each platform method to confirm idempotency is
    # actually wired. ``is_wrapped`` checks membership in the framework's
    # private ``_WRAPPED_FUNCTIONS`` WeakSet — and that check does not walk
    # ``__wrapped__``, so our outer translator hides the inner wrap from the
    # validator unless we also register here. Registering is correct: the
    # outer function preserves all idempotency semantics (it only intercepts
    # the conflict-raise path, which is the framework's own exception).
    try:
        from adcp.server.idempotency.store import _WRAPPED_FUNCTIONS

        _WRAPPED_FUNCTIONS.add(_wrapper)
    except (ImportError, AttributeError):  # pragma: no cover — defensive
        # Framework refactored the registry — fall back to leaving the
        # validator to fail loudly so the discrepancy is caught at boot.
        pass

    return _wrapper  # type: ignore[return-value]


# Lock guards lazy initialization. The store is process-singleton; once
# constructed we never rebuild it.
#
# We deliberately do NOT detect ``DATABASE_URL`` changes and rebuild the pool.
# Production never mutates ``DATABASE_URL`` mid-process — the URL is set once
# at startup. The only place the URL changes between uses is the integration
# test suite (per-test databases via ``tests/fixtures/integration_db.py``),
# which calls :func:`reset_for_tests` to drop the singletons explicitly. Adding
# defensive URL-comparison logic to a hot path to handle a test-only
# concern would push test plumbing into production code.
_LOCK = threading.Lock()
_STORE: IdempotencyStore | None = None
_POOL = None  # AsyncConnectionPool, kept around so the GC doesn't close it


def _build_pool():
    """Build the psycopg3 pool from ``DATABASE_URL``.

    The salesagent's primary URL is keyed for psycopg2 (`postgresql://...`).
    psycopg3 accepts the same prefix; no rewriting needed. Pool sizes are
    conservative — idempotency reads + writes are short, and oversized
    pools waste idle connections under multi-worker fanout.
    """
    from psycopg_pool import AsyncConnectionPool

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL must be set to use PgBackend for idempotency. "
            "Set CORE_IDEMPOTENCY_BACKEND=memory for single-process tests."
        )
    # ``check=AsyncConnectionPool.check_connection`` runs a tiny round-trip
    # (an empty query under autocommit) before handing a pooled connection to
    # the caller. Without it, a PgBouncer ``client_idle_timeout`` eviction
    # silently closes the underlying socket and the next acquire returns a
    # dead connection — surfaced as ``ProtocolViolation`` mid-operation. The
    # check costs one round-trip per acquire but keeps the pool resilient to
    # idle eviction.
    return AsyncConnectionPool(
        url,
        min_size=1,
        max_size=4,
        check=AsyncConnectionPool.check_connection,
        open=False,  # opened on first use; create_schema() triggers it
    )


def get_idempotency_store() -> IdempotencyStore:
    """Return the process-wide :class:`IdempotencyStore`.

    Lazy + thread-safe. First call constructs the backend; subsequent
    calls return the cached instance.
    """
    global _STORE, _POOL
    if _STORE is not None:
        return _STORE

    with _LOCK:
        if _STORE is not None:  # double-checked
            return _STORE

        backend_name = os.environ.get("CORE_IDEMPOTENCY_BACKEND", "auto").lower()

        # Explicit memory request, or no DATABASE_URL = fall back to
        # MemoryBackend. Lets module imports succeed in tooling contexts
        # (OpenAPI export, alembic autogen, unit-test collection) without
        # a live Postgres. Production sets DATABASE_URL via compose / env.
        if backend_name == "memory" or (backend_name == "auto" and not os.environ.get("DATABASE_URL")):
            logger.info(
                "Idempotency: using MemoryBackend (no DATABASE_URL or "
                "CORE_IDEMPOTENCY_BACKEND=memory). Multi-worker deployments "
                "MUST set DATABASE_URL so PgBackend takes over."
            )
            _STORE = IdempotencyStore(backend=MemoryBackend(), ttl_seconds=86400)
            return _STORE

        # PgBackend path. The pool MUST open on the same event loop that
        # later acquires connections, because AsyncConnectionPool's worker
        # tasks are bound to whichever loop ran open(). Bootstrapping in a
        # transient asyncio.run() loop here would leave the pool's workers
        # tied to a closed loop once serve() takes over.
        #
        # Defer pool.open() + create_schema() to the first async call via
        # _LazyBootstrapPgBackend, which runs them on whatever loop is live
        # at that moment.
        _POOL = _build_pool()
        backend = _LazyBootstrapPgBackend(pool=_POOL)

        logger.info("Idempotency: PgBackend constructed (pool will open on first async use)")
        _STORE = IdempotencyStore(backend=backend, ttl_seconds=86400)
        return _STORE


class _LazyBootstrapPgBackend(PgBackend):
    """PgBackend variant that opens its pool + creates the schema on the
    first async call, on whatever event loop is running at that point.

    Why: the previous bootstrap path called ``asyncio.run(_bootstrap())``
    inside :func:`get_idempotency_store`. That spawns a one-shot loop;
    :class:`AsyncConnectionPool` registers its worker tasks against that
    loop and they die as soon as ``asyncio.run`` returns. The pool then
    can't talk to the production loop that ``serve()`` later starts.

    With this wrapper the pool opens lazily on the live loop and stays
    open for the lifetime of the process.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # asyncio.Lock binds to a loop at construction; defer creation
        # until first use so we attach to the live loop.
        self._bootstrap_lock: asyncio.Lock | None = None
        self._bootstrapped = False

    async def _ensure_bootstrap(self) -> None:
        if self._bootstrapped:
            return
        if self._bootstrap_lock is None:
            self._bootstrap_lock = asyncio.Lock()
        async with self._bootstrap_lock:
            if self._bootstrapped:
                return
            await self._pool.open()
            await self.create_schema()
            self._bootstrapped = True
            logger.info("Idempotency: PgBackend pool opened, adcp_idempotency table ensured")

    async def get(self, *args: Any, **kwargs: Any):
        await self._ensure_bootstrap()
        return await super().get(*args, **kwargs)

    async def put(self, *args: Any, **kwargs: Any):
        await self._ensure_bootstrap()
        return await super().put(*args, **kwargs)

    async def delete_expired(self, *args: Any, **kwargs: Any):
        await self._ensure_bootstrap()
        return await super().delete_expired(*args, **kwargs)


def reset_for_tests() -> None:
    """Drop the cached store + pool — for test isolation only.

    Tests that flip ``CORE_IDEMPOTENCY_BACKEND`` between cases need this
    to force re-init. Production code never calls it.
    """
    global _STORE, _POOL
    with _LOCK:
        _STORE = None
        # We deliberately don't close the pool here — its workers are bound
        # to the asyncio loop that opened it (which is already gone in the
        # test process), so close() raises CancelledError. The test process
        # exits at session end and the OS reclaims the connections. This is
        # test-only; production never resets the singleton.
        _POOL = None
