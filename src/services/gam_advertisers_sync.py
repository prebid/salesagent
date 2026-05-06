"""Sprint 5 piece D — GAM advertisers cache sync worker.

Pulls the publisher's GAM advertisers
(``CompanyService.getCompaniesByStatement WHERE type = 'ADVERTISER'``)
into the ``gam_advertisers`` cache table. The Buyer Routing UI picker
serves out of this cache; round-tripping to GAM on every keystroke is
prohibitively slow on 10k+ advertiser networks.

Soft-delete on disappearance: advertisers that drop out of GAM are
flagged ``status='inactive'`` rather than hard-deleted, because routing
rules might still reference them. The picker hides inactive rows by
default.

Wire-up: ``POST /tenants/{tid}/refresh`` creates a pending SyncJob row
with ``sync_type='advertisers'``. The cron picker (sprint follow-up) or
an explicit admin-button click will call :func:`sync_advertisers`,
which reads the pending job, marks it running, executes the GAM read +
upsert, and marks it completed.

For tests + unit-style invocation, :func:`sync_advertisers` accepts a
``client_factory`` parameter so callers can inject a mocked GAM
client without touching real credentials.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, GamAdvertiser, SyncJob

logger = logging.getLogger(__name__)


# Tunable: GAM enforces a max 500 rows per page; smaller pages help us
# stream progress updates and recover from transient timeouts without
# refetching from offset zero.
_GAM_PAGE_SIZE = 500


class GamClientUnavailable(RuntimeError):
    """Tenant has no working GAM auth — the worker cannot run."""


class _EmptyAdvertiserResult(RuntimeError):
    """GAM returned zero advertisers. Skip the soft-delete sweep so a
    transient API hiccup doesn't silently empty the cache.
    """


def _build_gam_client_for_tenant(tenant_id: str) -> Any:
    """Build a real GAM ad_manager.AdManagerClient for ``tenant_id``.

    Default ``client_factory`` for :func:`sync_advertisers`. Routes through
    the canonical builder so auth-method detection stays consistent with
    every other GAM client construction site (connection test, inventory
    sync, custom-targeting sync). Service-account JSON wins over refresh
    token when both are present — see build_gam_config_from_adapter.
    """
    from src.adapters.gam import GAMClientManager, build_gam_config_from_adapter

    with get_db_session() as session:
        adapter = session.scalars(
            select(AdapterConfig).filter_by(tenant_id=tenant_id, adapter_type="google_ad_manager")
        ).first()
        if adapter is None or not adapter.gam_network_code:
            raise GamClientUnavailable(f"Tenant {tenant_id!r} has no GAM adapter configured")

        config = build_gam_config_from_adapter(adapter)
        if "service_account_json" not in config and "refresh_token" not in config:
            raise GamClientUnavailable(f"Tenant {tenant_id!r} has no GAM credentials")

        return GAMClientManager(config, network_code=adapter.gam_network_code).get_client()


def _iter_advertisers_from_gam(client: Any) -> Iterable[dict[str, Any]]:
    """Yield ``{id, name, status, currency_code}`` dicts from GAM.

    Pages through ``CompanyService.getCompaniesByStatement WHERE
    type = 'ADVERTISER'`` until the totalResultSetSize is exhausted.
    """
    from googleads import ad_manager

    company_service = client.GetService("CompanyService")
    statement_builder = ad_manager.StatementBuilder()
    statement_builder.Where("type = :type")
    statement_builder.WithBindVariable("type", "ADVERTISER")
    statement_builder.Limit(_GAM_PAGE_SIZE)

    total = None
    fetched = 0
    saw_any_page = False
    while True:
        result = company_service.getCompaniesByStatement(statement_builder.ToStatement())
        results = getattr(result, "results", None) if result else None
        if total is None and result is not None:
            total = int(getattr(result, "totalResultSetSize", 0))
        if not results:
            # No rows on this page. If we've never seen results AND the
            # total reported by GAM is zero, signal "intentionally empty"
            # via raising EmptyAdvertiserResult so the caller can skip the
            # soft-delete sweep — a transient API blip that returns
            # zero rows must not silently empty the whole cache.
            if not saw_any_page and (total is None or total == 0):
                raise _EmptyAdvertiserResult("GAM returned no advertisers")
            break
        saw_any_page = True
        for company in result.results:
            yield {
                "id": str(company.id),
                "name": company.name,
                "status": (getattr(company, "creditStatus", None) or "active"),
                # GAM Company has no per-advertiser currency; currency is
                # network-level. Left None until we surface it from
                # NetworkService or LineItem state if/when a need arises.
                "currency_code": None,
            }
        fetched += len(result.results)
        if total is None or fetched >= total:
            break
        statement_builder.offset += len(result.results)


def _upsert_advertisers(
    tenant_id: str,
    advertisers: list[dict[str, Any]],
    sync_time: datetime,
) -> tuple[int, int]:
    """Upsert advertisers + soft-delete missing ones.

    Returns ``(upserted_count, soft_deleted_count)``.
    """
    upserted = 0
    if advertisers:
        with get_db_session() as session:
            seen_ids = {a["id"] for a in advertisers}
            payload = [
                {
                    "tenant_id": tenant_id,
                    "advertiser_id": a["id"],
                    "name": a["name"],
                    "currency_code": a.get("currency_code"),
                    "status": a.get("status") or "active",
                    "synced_at": sync_time,
                }
                for a in advertisers
            ]
            stmt = pg_insert(GamAdvertiser).values(payload)
            stmt = stmt.on_conflict_do_update(
                index_elements=["tenant_id", "advertiser_id"],
                set_={
                    "name": stmt.excluded.name,
                    "currency_code": stmt.excluded.currency_code,
                    "status": stmt.excluded.status,
                    "synced_at": stmt.excluded.synced_at,
                },
            )
            session.execute(stmt)
            upserted = len(payload)
            session.commit()
    else:
        seen_ids = set()

    # Soft-delete: rows in cache but missing from this sync get
    # status='inactive'. We DO NOT hard-delete because a routing rule
    # might still reference them — surfacing the inactive flag in the
    # UI is the correct user-facing signal.
    soft_deleted = 0
    with get_db_session() as session:
        # FIXME(embedded-mode-sprint-5-piece-D): GamAdvertiserRepository TBD —
        # raw select() until the repository class lands.
        stale = session.scalars(
            select(GamAdvertiser).filter_by(tenant_id=tenant_id).where(GamAdvertiser.status != "inactive")
        ).all()
        for row in stale:
            if row.advertiser_id in seen_ids:
                continue
            row.status = "inactive"
            row.synced_at = sync_time
            soft_deleted += 1
        if soft_deleted:
            session.commit()

    return upserted, soft_deleted


def sync_advertisers(
    tenant_id: str,
    *,
    sync_id: str | None = None,
    client_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Run the advertisers sync for ``tenant_id`` start-to-finish.

    If ``sync_id`` is provided, the matching SyncJob row is mutated:
    pending → running on entry, completed/failed on exit. If omitted,
    the worker creates a fresh SyncJob row first (admin-button or
    direct-call path).

    ``client_factory`` is the GAM-client constructor; defaults to
    :func:`_build_gam_client_for_tenant`. Tests inject a mocked
    factory to avoid touching real GAM.

    Returns a summary dict suitable for storing in
    ``SyncJob.summary``.
    """
    factory = client_factory or _build_gam_client_for_tenant
    started_at = datetime.now(UTC)
    sync_time = started_at

    with get_db_session() as session:
        if sync_id is None:
            # Microsecond precision in the suffix so back-to-back syncs in
            # the same second don't collide on the sync_jobs primary key.
            sync_id = f"sync_{tenant_id}_advertisers_{int(started_at.timestamp() * 1_000_000)}"
            job = SyncJob(
                sync_id=sync_id,
                tenant_id=tenant_id,
                adapter_type="google_ad_manager",
                sync_type="advertisers",
                status="running",
                started_at=started_at,
                triggered_by="worker",
                triggered_by_id="sync_advertisers",
            )
            session.add(job)
        else:
            job = session.scalars(select(SyncJob).filter_by(sync_id=sync_id)).first()  # type: ignore[assignment]
            if job is None:
                raise ValueError(f"SyncJob {sync_id!r} not found")
            job.status = "running"
        session.commit()

    try:
        client = factory(tenant_id)
        try:
            advertisers = list(_iter_advertisers_from_gam(client))
        except _EmptyAdvertiserResult:
            # GAM returned zero advertisers and zero totalResultSetSize.
            # Skip the upsert (no rows) AND skip the soft-delete sweep —
            # marking every cached row inactive on a transient empty
            # response would silently empty the Buyer Routing picker.
            logger.warning(
                "[%s] GAM returned zero advertisers; preserving cache (soft-delete sweep skipped)",
                sync_id,
            )
            upserted, soft_deleted = 0, 0
            advertisers = []
        else:
            upserted, soft_deleted = _upsert_advertisers(tenant_id, advertisers, sync_time)
    except Exception as exc:  # pragma: no cover - error-path tested separately
        logger.error("[%s] advertisers sync failed: %s", sync_id, exc, exc_info=True)
        with get_db_session() as session:
            job = session.scalars(select(SyncJob).filter_by(sync_id=sync_id)).first()  # type: ignore[assignment]
            if job is not None:
                job.status = "failed"
                job.completed_at = datetime.now(UTC)
                job.error_message = str(exc)
                session.commit()
        raise

    summary: dict[str, Any] = {
        "tenant_id": tenant_id,
        "sync_time": sync_time.isoformat(),
        "upserted": upserted,
        "soft_deleted": soft_deleted,
        "total_seen": len(advertisers),
    }
    with get_db_session() as session:
        job = session.scalars(select(SyncJob).filter_by(sync_id=sync_id)).first()  # type: ignore[assignment]
        if job is not None:
            job.status = "completed"
            job.completed_at = datetime.now(UTC)
            job.summary = json.dumps(summary)
            session.commit()
    logger.info("[%s] advertisers sync complete: %s", sync_id, summary)
    return summary


def sync_advertisers_pending_jobs(tenant_id: str | None = None) -> list[str]:
    """Pick up pending ``advertisers`` SyncJobs and run them.

    Cron-style picker for the rows that ``POST /tenants/{tid}/refresh``
    fans out (sprint 1.8 §8). Filters to ``tenant_id`` when provided so
    a per-tenant admin button can drive a single sync without scanning
    every tenant.

    Returns the list of sync_ids processed.
    """
    with get_db_session() as session:
        stmt = select(SyncJob).where(SyncJob.sync_type == "advertisers", SyncJob.status == "pending")
        if tenant_id is not None:
            stmt = stmt.where(SyncJob.tenant_id == tenant_id)
        pending = list(session.scalars(stmt).all())

    processed: list[str] = []
    for job in pending:
        try:
            sync_advertisers(job.tenant_id, sync_id=job.sync_id)
        except Exception as exc:
            # Already marked failed inside sync_advertisers; log and
            # keep going so one bad tenant doesn't poison the whole run.
            logger.warning(
                "advertisers sync failed for tenant=%s sync_id=%s: %s",
                job.tenant_id,
                job.sync_id,
                exc,
                exc_info=True,
            )
        processed.append(job.sync_id)
    return processed
