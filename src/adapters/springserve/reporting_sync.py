"""SpringServe Reporting API sync -- orchestrator.

Pulls per-demand-tag delivery metrics into the ``springserve_demand_tag_stats``
cache that backs ``SpringServeAdapter.get_packages_snapshot`` and
``SpringServeAdapter.get_media_buy_delivery``.

Orchestration:

  1. Build a :class:`JobSpec` covering the requested demand tags (or all
     in the tenant if ``demand_tag_ids`` is None).
  2. Submit -- sync for small one-day windows, async for larger ones.
  3. Poll until DONE (async only).
  4. Parse rows via the configured :class:`ColumnMap`.
  5. Bulk-upsert into ``springserve_demand_tag_stats`` via
     :class:`SpringServeDemandTagStatsRepository`.

When scope isn't granted yet the underlying call surfaces a
:class:`SpringServeForbiddenError`; we trap it once at the top of
:meth:`run` and raise :class:`ReportingScopeNotGranted` so the shared
scheduler gets a clean signal rather than a raw 403.

Today (pre-scope): calling :meth:`run` raises immediately because the
first POST returns 403. The read paths (``get_packages_snapshot``,
``get_media_buy_delivery``) tolerate an empty cache by raising
``DeliveryDataUnavailable`` (matches the FreeWheel adapter contract).

Day-of-scope: re-verify the response shape and column names; tune
:data:`DEFAULT_COLUMN_MAP` in ``_reporting.py`` to match SpringServe's
real schema if needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from src.adapters.springserve._reporting import (
    JobSpec,
    ReportingError,
    SpringServeReportingClient,
)
from src.adapters.springserve._transport import SpringServeForbiddenError
from src.adapters.springserve.client import SpringServeClient
from src.core.database.repositories.springserve_demand_tag_stats import (
    SpringServeDemandTagStatsRepository,
)

logger = logging.getLogger(__name__)


class ReportingScopeNotGranted(RuntimeError):
    """Raised when reporting sync runs before SpringServe Reporting scope is granted.

    See ``docs/adapters/springserve/README.md`` -- the scope ask is
    bundled with the Stage 2 write-scope grant request to SpringServe
    support.
    """

    def __init__(self) -> None:
        super().__init__(
            "SpringServe Reporting API scope not granted on this account. "
            "POST /report returns 403; ask SpringServe support to enable "
            "Reporting access on the API user. Read paths return empty "
            "results gracefully until sync is wired."
        )


@dataclass
class ReportingSyncResult:
    """Summary of one reporting-sync run."""

    rows_updated: int
    report_id: str | None
    error: str | None = None


class SpringServeReportingSync:
    """Reporting-sync orchestrator.

    Construct once per run with the SpringServe client, the tenant id,
    and a DB session. Call :meth:`run` to refresh the cache.
    """

    # Async threshold -- windows longer than 1 day go through the async
    # report-jobs path to stay under the 10 req/min sync limit.
    SYNC_MAX_DAYS: int = 1

    def __init__(self, *, client: SpringServeClient, tenant_id: str, session: Session):
        self._client = client
        self._reporting = SpringServeReportingClient(client._transport)
        self._tenant_id = tenant_id
        self._session = session

    def run(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        demand_tag_ids: list[str] | None = None,
    ) -> ReportingSyncResult:
        """Refresh stats for ``demand_tag_ids`` (required, non-empty) over
        ``[start_date, end_date]``. Defaults: today.

        The SpringServe Reporting API does not echo the demand-tag id back
        in rows and sums across multi-id filters, so per-tag stats require
        one job per tag. We loop here and aggregate.
        """
        today = datetime.now(UTC).date()
        start = start_date or today
        end = end_date or today
        if not demand_tag_ids:
            # The Reporting API doesn't tag rows with their source demand_tag_id
            # and sums across multi-id filters, so per-tag stats require one job
            # per tag. The scheduler may call us before any packages have been
            # pushed -- treat this as a successful no-op, not a sync failure.
            logger.info("SpringServe reporting sync skipped: no demand_tag_ids supplied")
            return ReportingSyncResult(rows_updated=0, report_id=None, error=None)
        use_async = (end - start).days > self.SYNC_MAX_DAYS

        all_rows = []
        last_report_id: str | None = None
        try:
            for tag_id in demand_tag_ids:
                spec = JobSpec(
                    start_date=start,
                    end_date=end,
                    demand_tag_id=tag_id,
                    use_async=use_async,
                )
                if use_async:
                    report_id = self._reporting.submit_async(spec)
                    self._reporting.poll_until_done(report_id)
                    rows = self._reporting.fetch_rows(report_id, demand_tag_id=tag_id)
                    last_report_id = report_id
                else:
                    rows = self._reporting.submit_sync(spec)
                all_rows.extend(rows)
        except SpringServeForbiddenError as exc:
            logger.info("SpringServe reporting scope not granted: %s", exc)
            raise ReportingScopeNotGranted() from exc
        except ReportingError as exc:
            logger.warning("SpringServe reporting job failed: %s", exc)
            return ReportingSyncResult(rows_updated=0, report_id=last_report_id, error=str(exc))

        now = datetime.now(UTC)
        payloads = [
            {
                "demand_tag_id": row.demand_tag_id,
                "campaign_id": None,
                "impressions": row.impressions,
                "completed_views": row.completed_views,
                "clicks": row.clicks,
                "spend_micros": row.spend_micros,
                "currency": None,
                "as_of": now,
                "last_synced_at": now,
            }
            for row in all_rows
        ]
        repo = SpringServeDemandTagStatsRepository(self._session, self._tenant_id)
        touched = repo.bulk_upsert(payloads)
        self._session.commit()
        logger.info(
            "SpringServe reporting sync: tenant=%s window=%s..%s tags=%d rows=%d touched=%d report_id=%s",
            self._tenant_id,
            start,
            end,
            len(demand_tag_ids),
            len(all_rows),
            touched,
            last_report_id,
        )
        return ReportingSyncResult(rows_updated=touched, report_id=last_report_id, error=None)


__all__ = ["ReportingScopeNotGranted", "ReportingSyncResult", "SpringServeReportingSync"]
