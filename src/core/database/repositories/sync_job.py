"""SyncJob repository — tenant-scoped reads of the sync history.

Sprint 3 of [embedded-mode](../../../../docs/design/embedded-mode-sprint-3.md):
``GET /tenants/{tid}/sync-history`` reads from ``sync_jobs``. Existing sync
infrastructure (provision + ``/refresh``) writes rows directly via
``session.add(SyncJob(...))`` because that path is performance-critical and
predates the repository layer; this repository covers the read drill-downs
the management API needs.

Stage 4 of #382 adds :class:`SyncJobAdminRepository` for the cross-tenant
``/admin/scheduling`` view — same table, no tenant filter, super-admin only.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select, tuple_
from sqlalchemy.orm import Session

from src.core.database.models import SyncJob


class SyncJobRepository:
    """Tenant-scoped reads against the ``sync_jobs`` table.

    Args:
        session: Active SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def list_history(
        self,
        *,
        sync_type: str | None = None,
        status: str | None = None,
        cursor_started_at: datetime | None = None,
        cursor_id: str | None = None,
        limit: int = 20,
    ) -> list[SyncJob]:
        """List sync runs for the tenant, ordered by ``started_at desc, sync_id desc``.

        Cursor pagination uses ``(started_at, sync_id)`` so concurrent inserts
        with the same timestamp can't skip or duplicate rows.
        """
        stmt = select(SyncJob).where(SyncJob.tenant_id == self._tenant_id)

        if sync_type:
            stmt = stmt.where(SyncJob.sync_type == sync_type)
        if status:
            stmt = stmt.where(SyncJob.status == status)

        if cursor_started_at is not None and cursor_id is not None:
            stmt = stmt.where(
                or_(
                    SyncJob.started_at < cursor_started_at,
                    and_(
                        SyncJob.started_at == cursor_started_at,
                        SyncJob.sync_id < cursor_id,
                    ),
                )
            )

        stmt = stmt.order_by(SyncJob.started_at.desc(), SyncJob.sync_id.desc()).limit(limit)
        return list(self._session.scalars(stmt).all())

    def find_by_sync_id(self, sync_id: str) -> SyncJob | None:
        """Lookup a single SyncJob row for this tenant by sync_id.

        Returns ``None`` when the row doesn't exist OR belongs to another
        tenant — the tenant_id filter is enforced so the
        ``enqueue_adapter_sync`` async path can't accidentally transition
        another tenant's queued row to ``running``.
        """
        stmt = select(SyncJob).filter_by(sync_id=sync_id, tenant_id=self._tenant_id)
        return self._session.scalars(stmt).first()

    def mark_pending_as_failed(self, sync_ids: list[str], error_message: str) -> int:
        """Transition any of the given pending SyncJob rows to ``failed``.

        Used by the provision / refresh paths when a worker spawn raises:
        without this, the row sits ``pending`` forever and the publisher
        sees "never run" with no error surfaced. Only ``pending`` rows are
        touched — a worker that already started running owns the row's
        lifecycle from that point on.

        Returns the count of rows transitioned.
        """
        if not sync_ids:
            return 0
        rows = self._session.scalars(
            select(SyncJob).where(
                SyncJob.tenant_id == self._tenant_id,
                SyncJob.sync_id.in_(sync_ids),
                SyncJob.status == "pending",
            )
        ).all()
        now = datetime.now(UTC)
        for row in rows:
            row.status = "failed"
            row.completed_at = now
            row.error_message = error_message
        return len(rows)

    def latest_completed_at(self, *, adapter_type: str, sync_type: str) -> datetime | None:
        """Return ``completed_at`` of the most-recent ``status=completed``
        sync row for this tenant + adapter + kind, or ``None``.

        Powers the freshness accessors on :class:`AdServerAdapter` —
        callers don't need full rows, just the timestamp.
        """
        stmt = (
            select(SyncJob.completed_at)
            .where(
                SyncJob.tenant_id == self._tenant_id,
                SyncJob.adapter_type == adapter_type,
                SyncJob.sync_type == sync_type,
                SyncJob.status == "completed",
            )
            .order_by(SyncJob.completed_at.desc())
            .limit(1)
        )
        return self._session.scalar(stmt)


class SyncJobAdminRepository:
    """Cross-tenant reads against ``sync_jobs`` for the super-admin
    ``/admin/scheduling`` view (Stage 4 of #382).

    Deliberately separate from :class:`SyncJobRepository` so the tenant
    isolation invariant on the tenant-scoped repo stays intact — this one
    skips that filter on purpose, and the only callers are super-admin
    endpoints gated by ``@require_auth(admin_only=True)``.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def latest_per_kind(self) -> dict[tuple[str, str, str], SyncJob]:
        """Return the most-recent SyncJob row per
        ``(tenant_id, adapter_type, sync_type)`` triple.

        One row per triple — the scheduling page's "Last run" column wants
        the freshest record only, not full history. Uses a correlated
        subquery on ``MAX(started_at)`` so it stays a single round-trip even
        as the table grows.
        """
        latest_started = (
            select(
                SyncJob.tenant_id.label("t"),
                SyncJob.adapter_type.label("a"),
                SyncJob.sync_type.label("k"),
                func.max(SyncJob.started_at).label("max_started"),
            )
            .group_by(SyncJob.tenant_id, SyncJob.adapter_type, SyncJob.sync_type)
            .subquery()
        )

        stmt = select(SyncJob).join(
            latest_started,
            and_(
                SyncJob.tenant_id == latest_started.c.t,
                SyncJob.adapter_type == latest_started.c.a,
                SyncJob.sync_type == latest_started.c.k,
                SyncJob.started_at == latest_started.c.max_started,
            ),
        )

        rows = self._session.scalars(stmt).all()
        # Same (tenant, adapter, kind, started_at) can have >1 row if two
        # jobs began in the same microsecond — pick deterministically by
        # sync_id so the UI doesn't flicker between equal candidates.
        out: dict[tuple[str, str, str], SyncJob] = {}
        for row in rows:
            key = (row.tenant_id, row.adapter_type, row.sync_type)
            existing = out.get(key)
            if existing is None or row.sync_id > existing.sync_id:
                out[key] = row
        return out

    def list_recent(self, *, limit: int = 100) -> list[SyncJob]:
        """Return the N most recent SyncJob rows across all tenants.

        Powers the "Recent runs" log on the scheduling page — flat list,
        no grouping. ``started_at desc, sync_id desc`` for determinism.
        """
        stmt = select(SyncJob).order_by(SyncJob.started_at.desc(), SyncJob.sync_id.desc()).limit(limit)
        return list(self._session.scalars(stmt).all())

    def latest_for_triples(self, triples: list[tuple[str, str, str]]) -> dict[tuple[str, str, str], SyncJob]:
        """Same as :meth:`latest_per_kind` but restricted to a set of
        ``(tenant_id, adapter_type, sync_type)`` triples.

        Used when the caller already knows the expected matrix from
        :class:`AdapterConfig` × :class:`AdapterCapabilities` and only
        wants existing rows for those slots — cheaper than scanning all
        history when most adapters never ran a given sync_kind.
        """
        if not triples:
            return {}

        latest_started = (
            select(
                SyncJob.tenant_id.label("t"),
                SyncJob.adapter_type.label("a"),
                SyncJob.sync_type.label("k"),
                func.max(SyncJob.started_at).label("max_started"),
            )
            .where(tuple_(SyncJob.tenant_id, SyncJob.adapter_type, SyncJob.sync_type).in_(triples))
            .group_by(SyncJob.tenant_id, SyncJob.adapter_type, SyncJob.sync_type)
            .subquery()
        )

        stmt = select(SyncJob).join(
            latest_started,
            and_(
                SyncJob.tenant_id == latest_started.c.t,
                SyncJob.adapter_type == latest_started.c.a,
                SyncJob.sync_type == latest_started.c.k,
                SyncJob.started_at == latest_started.c.max_started,
            ),
        )

        out: dict[tuple[str, str, str], SyncJob] = {}
        for row in self._session.scalars(stmt).all():
            key = (row.tenant_id, row.adapter_type, row.sync_type)
            existing = out.get(key)
            if existing is None or row.sync_id > existing.sync_id:
                out[key] = row
        return out
