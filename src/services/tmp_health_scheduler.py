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
  cannot cancel the remaining probes, and the write phase is isolated per tenant
  for the same reason — both phases fail per item, never per batch.

Singleton pattern (same as delivery_webhook_scheduler and media_buy_status_scheduler):
the module-level ``get_*()`` / ``start_*()`` / ``stop_*()`` trio is bound from
``_scheduler_base.make_singleton()`` rather than hand-rolled, so all three
scheduler modules share one implementation.  Each module still owns its own
cached instance, and tests can construct a fresh ``TMPHealthScheduler()``
directly without touching it.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from src.core.database.database_session import get_db_session
from src.core.database.repositories.tmp_provider import TMPProviderRepository
from src.core.database.repositories.uow import TMPProviderUoW
from src.core.logging_config import log_safe
from src.core.security.outbound_http import OperatorEndpoint, OutboundDeliveryFailed, asend
from src.services._provider_http import provider_url
from src.services._scheduler_base import IntervalScheduler, make_singleton, parse_interval_env

logger = logging.getLogger(__name__)

# Configurable via env var — default 60 seconds.
HEALTH_CHECK_INTERVAL_SECONDS: int = parse_interval_env("TMP_HEALTH_CHECK_INTERVAL", 60)

# Per-provider HTTP timeout.  The scheduler can afford to mark a slow
# provider as unhealthy and retry on the next cycle, so this stays short.
HEALTH_CHECK_TIMEOUT_SECONDS = 5


async def _check_provider_health(endpoint: str) -> str:
    """Probe a single provider's /health endpoint (async, no thread pool).

    Returns one of: ``"healthy"``, ``"unhealthy"``, ``"error"``.

    Probed through the outbound egress seam (``outbound_http.asend``), which owns
    address policy, TLS and redirect refusal — the ``follow_redirects=False``
    this used to set itself is httpx's default inside the seam (#1802).

    ``max_attempts=1``: a health probe must report what one request saw. The
    seam's default of 3 would turn a single scheduled probe into three requests
    per provider per tick and report the last one, which is a different
    measurement from the one this scheduler has always taken.

    The three-way answer survives the seam raising on non-2xx.
    ``OutboundDeliveryFailed`` carries ``http_status``, so "the provider answered
    and it was not 200" (unhealthy) stays distinguishable from "no answer
    reached us at all" (error) — the distinction the persisted status exists to
    make. Anything else (a refusal, a malformed hostname) is ``"error"`` so the
    caller's ``gather(return_exceptions=True)`` loop never sees a raw exception
    from this coroutine.
    """
    health_url = provider_url(endpoint, "/health")
    try:
        result = await asend(
            health_url,
            method="GET",
            timeout=HEALTH_CHECK_TIMEOUT_SECONDS,
            max_attempts=1,
            provenance=OperatorEndpoint("the TMP provider"),
        )
    except OutboundDeliveryFailed as failed:
        if failed.http_status is not None:
            logger.debug("[TMP health] Provider %s answered %d", log_safe(endpoint), failed.http_status)
            return "unhealthy"
        logger.exception("[TMP health] Health probe failed for %s", log_safe(endpoint))
        return "error"
    except Exception:
        logger.exception("[TMP health] Health probe failed for %s", log_safe(endpoint))
        return "error"
    return "healthy" if result.http_status == 200 else "unhealthy"


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
        4. Write results — one UoW per tenant group so the commit boundary
           is owned by the UoW, not a raw session.commit() call.
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

        # --- Step 3: write results — group by tenant so each UoW owns its commit ---
        # Build a per-tenant list of (provider_id, status) pairs first so we can
        # open exactly one UoW per tenant rather than one raw session for all tenants.
        by_tenant: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for (provider_id, tenant_id, _endpoint), status in zip(provider_info, statuses, strict=True):
            by_tenant[tenant_id].append((provider_id, status))

        # Isolated per tenant, for the same reason the probe phase passes
        # return_exceptions=True: the unit of work is one tenant, so one
        # tenant's UoW failure (a lock timeout, a row deleted mid-cycle) must not
        # skip every tenant later in the iteration order — which is what an
        # unguarded loop did, silently, on the write half only (#1197 review).
        written = 0
        for tenant_id, updates in by_tenant.items():
            try:
                with TMPProviderUoW(tenant_id) as uow:
                    for provider_id, status in updates:
                        uow.tmp_providers.update_health_status(provider_id, status)
            except Exception:
                logger.exception(
                    "[TMP health] Failed to persist %d health result(s) for tenant=%s — "
                    "continuing with the remaining tenants",
                    len(updates),
                    tenant_id,
                )
                continue
            written += 1

        logger.debug(
            "[TMP health] Check complete: %d provider(s) checked across %d tenant(s) (%d persisted)",
            len(provider_info),
            len(by_tenant),
            written,
        )


# ---------------------------------------------------------------------------
# Global singleton — derived from the shared factory, not hand-rolled.
# make_singleton also registers the start/stop pair under the display name
# below, which is what the app entry point iterates (#1197 review).
# ---------------------------------------------------------------------------

(
    get_tmp_health_scheduler,
    start_tmp_health_scheduler,
    stop_tmp_health_scheduler,
) = make_singleton(TMPHealthScheduler, name="TMP health")
