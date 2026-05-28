"""Background health-check scheduler for TMP providers.

Polls each active/draining TMP provider's ``/health`` endpoint on a fixed
cadence and writes the result (``healthy``, ``unhealthy``, ``error``) to the
``health_status`` / ``last_health_checked_at`` columns.  The admin UI reads
from these columns instead of making a live HTTP call in the request cycle,
which avoids blocking workers for up to 5 s per provider.

The scheduler extends :class:`src.services._scheduler_base.IntervalScheduler`
which provides the identical ``__init__`` / ``start`` / ``stop`` /
``_run_scheduler`` scaffold shared by all three interval schedulers.

Design principles (matching tmp_provider_sync.py):
- HTTP calls are made **after** the DB session is closed — no open transaction
  during network I/O.
- Provider metadata is read into memory, the session is closed, probes run
  concurrently via ``httpx.AsyncClient``, then a short session writes the results.
- ``asyncio.gather(..., return_exceptions=True)`` ensures one bad endpoint
  cannot cancel the remaining probes.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from src.core.database.database_session import get_db_session
from src.core.database.repositories.tmp_provider import TMPProviderRepository
from src.services._scheduler_base import IntervalScheduler

logger = logging.getLogger(__name__)

# Configurable via env var — default 60 seconds.
# Wrapped in try/except so a bad value (e.g. "sixty") doesn't crash the
# process at import time before lifespan startup can report the error.
try:
    HEALTH_CHECK_INTERVAL_SECONDS: int = int(os.getenv("TMP_HEALTH_CHECK_INTERVAL") or "60")
except (ValueError, TypeError):
    logger.warning("TMP_HEALTH_CHECK_INTERVAL is not a valid integer — defaulting to 60s")
    HEALTH_CHECK_INTERVAL_SECONDS = 60

# Per-provider HTTP timeout.  Shorter than the old inline 5 s because
# the scheduler can afford to mark a slow provider as unhealthy and
# retry on the next cycle.
HEALTH_CHECK_TIMEOUT_SECONDS = 5


async def _check_provider_health(endpoint: str) -> str:
    """Probe a single provider's /health endpoint (async, no thread pool).

    Returns one of: ``"healthy"``, ``"unhealthy"``, ``"error"``.

    Uses ``follow_redirects=False`` to prevent SSRF via open-redirect even
    though the base URL was validated at registration time.

    Any exception that escapes ``httpx.HTTPError`` (e.g. ``socket.gaierror``,
    ``UnicodeError`` on a malformed hostname) is caught here and mapped to
    ``"error"`` so the caller's ``gather(return_exceptions=True)`` loop never
    sees a raw exception from this coroutine.
    """
    health_url = endpoint.rstrip("/") + "/health"
    try:
        async with httpx.AsyncClient(
            timeout=HEALTH_CHECK_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            resp = await client.get(health_url)
        return "healthy" if resp.status_code == 200 else "unhealthy"
    except Exception:
        return "error"


class TMPHealthScheduler(IntervalScheduler):
    """Background scheduler that polls TMP provider health endpoints."""

    def __init__(self) -> None:
        super().__init__(
            interval_seconds=HEALTH_CHECK_INTERVAL_SECONDS,
            name="TMP health",
        )

    async def tick(self) -> None:
        """Poll every active/draining provider and persist the result.

        Follows the same pattern as tmp_provider_sync.py:
        1. Read provider metadata into memory (short DB session).
        2. Close the session — no open transaction during network I/O.
        3. Run health probes concurrently via httpx.AsyncClient.
        4. Reopen a short session to write the results.
        """
        # --- Step 1: read provider metadata, then close the session ---
        with get_db_session() as session:
            providers = TMPProviderRepository.get_all_syncable(session)
            if not providers:
                return
            # Materialise into plain tuples so we don't need detached ORM objects
            provider_info = [(p.provider_id, p.tenant_id, p.endpoint) for p in providers]

        # --- Step 2: probe all providers concurrently (no DB session held) ---
        # return_exceptions=True: one bad endpoint (DNS failure, UnicodeError, etc.)
        # cannot cancel the remaining probes or skip the persist step.
        raw_results = await asyncio.gather(
            *[_check_provider_health(endpoint) for _, _, endpoint in provider_info],
            return_exceptions=True,
        )

        # Coerce any leaked exception to "error" (defensive — _check_provider_health
        # already catches everything, but belt-and-suspenders for future changes).
        statuses = [r if isinstance(r, str) else "error" for r in raw_results]

        # --- Step 3: write results in a short session ---
        with get_db_session() as session:
            for (provider_id, tenant_id, _endpoint), status in zip(provider_info, statuses, strict=True):
                repo = TMPProviderRepository(session, tenant_id)
                repo.update_health_status(provider_id, status)
            session.commit()

        logger.debug(
            "TMP health check complete: %d provider(s) checked",
            len(provider_info),
        )


# ---------------------------------------------------------------------------
# Global singleton (same pattern as delivery_webhook_scheduler)
# ---------------------------------------------------------------------------

_scheduler: TMPHealthScheduler | None = None


def get_tmp_health_scheduler() -> TMPHealthScheduler:
    """Get or create the global TMP health scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = TMPHealthScheduler()
    return _scheduler


async def start_tmp_health_scheduler() -> None:
    """Start the global TMP health scheduler."""
    scheduler = get_tmp_health_scheduler()
    await scheduler.start()


async def stop_tmp_health_scheduler() -> None:
    """Stop the global TMP health scheduler."""
    scheduler = get_tmp_health_scheduler()
    await scheduler.stop()
