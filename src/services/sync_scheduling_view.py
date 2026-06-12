"""Cross-tenant scheduling view (#382 Stage 4).

Assembles the data behind ``/admin/scheduling``: one row per
``(tenant_id, adapter_type, sync_kind)`` where the adapter declares
support for that kind, paired with the most recent SyncJob row (if any)
and a three-state freshness verdict (``ok`` / ``warning`` / ``critical``).

Reads only — the Run Now action goes through
``src.services.adapter_sync_orchestration.enqueue_adapter_sync``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from src.adapters import ADAPTER_REGISTRY
from src.adapters.base import AdapterCapabilities
from src.core.database.models import SyncJob
from src.core.database.repositories.adapter_config import AdapterConfigAdminRepository
from src.core.database.repositories.sync_job import SyncJobAdminRepository
from src.services.adapter_sync_orchestration import (
    KIND_AVAILABILITY_GUIDANCE,
    KIND_INVENTORY,
    KIND_PRICE_GUIDANCE,
    KIND_SIGNAL_COVERAGE,
    SUPPORTED_SYNC_KINDS,
    adapter_supports_sync_kind,
)

# Fallback thresholds when an adapter doesn't override on its capabilities
# block. Used for unknown adapter types and as backstops; production
# adapters set their own values on :class:`AdapterCapabilities`.
DEFAULT_INVENTORY_WARNING = timedelta(hours=24)
DEFAULT_INVENTORY_CRITICAL = timedelta(hours=72)
DEFAULT_REPORTING_WARNING = timedelta(hours=2)
DEFAULT_REPORTING_CRITICAL = timedelta(hours=6)
DEFAULT_GUIDANCE_WARNING = timedelta(hours=24)
DEFAULT_GUIDANCE_CRITICAL = timedelta(hours=72)

# Back-compat aliases — the Stage 5 reporting scheduler uses these to
# decide "is this row fresh enough to skip?". Keeping them at the
# Stage-4-default value is intentional: the scheduler doesn't care
# which adapter is which, it just needs a single threshold.
REPORTING_STALE_AFTER = DEFAULT_REPORTING_WARNING
INVENTORY_STALE_AFTER = DEFAULT_INVENTORY_WARNING
GUIDANCE_STALE_AFTER = DEFAULT_GUIDANCE_WARNING

GUIDANCE_SYNC_KINDS = (KIND_PRICE_GUIDANCE, KIND_AVAILABILITY_GUIDANCE, KIND_SIGNAL_COVERAGE)
_SYNC_KINDS = tuple(sorted(SUPPORTED_SYNC_KINDS))
IN_FLIGHT_STATUSES = frozenset({"pending", "queued", "running", "in_progress"})
LONG_RUNNING_WARNING_AFTER = timedelta(minutes=30)
LONG_RUNNING_CRITICAL_AFTER = timedelta(hours=1)

# Freshness levels, in increasing severity. The HTML template renders
# each as a distinct badge color (green / amber / red).
FRESHNESS_OK = "ok"
FRESHNESS_WARNING = "warning"
FRESHNESS_CRITICAL = "critical"


@dataclass
class SchedulingRow:
    """One row in the scheduling matrix."""

    tenant_id: str
    tenant_name: str
    adapter_type: str
    sync_kind: str
    supported: bool  # adapter.capabilities.supports_<kind>_sync
    last_status: str | None  # "queued" | "running" | "completed" | "failed" | None
    last_started_at: datetime | None
    last_completed_at: datetime | None
    last_sync_id: str | None
    last_error_message: str | None
    freshness: str  # FRESHNESS_OK / FRESHNESS_WARNING / FRESHNESS_CRITICAL
    never_run: bool  # True when no SyncJob row exists at all for this triple
    last_run_age_seconds: int | None = None
    last_run_age_label: str | None = None
    long_running: bool = False
    long_running_threshold_seconds: int | None = None
    notes: str | None = None  # Human-readable hint (e.g. "reporting bundled with inventory")

    @property
    def stale(self) -> bool:
        """Back-compat: ``stale=True`` for anything not ``ok``.

        Original Stage 4 surfaced a binary stale/fresh badge. Three-state
        rendering replaces it, but JS / templates that look at ``stale``
        still get a sensible value."""
        return self.freshness != FRESHNESS_OK

    @property
    def freshness_age_seconds(self) -> int | None:
        if self.last_completed_at is None:
            return None
        # SyncJob.completed_at is timezone-aware (DateTime(timezone=True))
        return int((datetime.now(UTC) - self.last_completed_at).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "tenant_name": self.tenant_name,
            "adapter_type": self.adapter_type,
            "sync_kind": self.sync_kind,
            "supported": self.supported,
            "last_status": self.last_status,
            "last_started_at": self.last_started_at.isoformat() if self.last_started_at else None,
            "last_completed_at": self.last_completed_at.isoformat() if self.last_completed_at else None,
            "last_sync_id": self.last_sync_id,
            "last_error_message": self.last_error_message,
            "freshness": self.freshness,
            "stale": self.stale,
            "never_run": self.never_run,
            "freshness_age_seconds": self.freshness_age_seconds,
            "last_run_age_seconds": self.last_run_age_seconds,
            "last_run_age_label": self.last_run_age_label,
            "long_running": self.long_running,
            "long_running_threshold_seconds": self.long_running_threshold_seconds,
            "notes": self.notes,
        }


def _capabilities_for(adapter_type: str) -> AdapterCapabilities | None:
    adapter_class = ADAPTER_REGISTRY.get(adapter_type.lower())
    if adapter_class is None:
        return None
    return getattr(adapter_class, "capabilities", None)


def _capability_flag(adapter_type: str, sync_kind: str) -> bool:
    """Return the adapter class's declared support for the given kind.

    Looks at :class:`AdapterCapabilities` directly rather than instantiating
    the adapter — the scheduling page must render for tenants whose
    AdapterConfig is incomplete or whose credentials are missing.
    """
    caps = _capabilities_for(adapter_type)
    if caps is None:
        return False
    return adapter_supports_sync_kind(caps, sync_kind)


def _freshness_thresholds(
    adapter_type: str,
    sync_kind: str,
    *,
    sync_cadence_minutes: int | None = None,
) -> tuple[timedelta, timedelta]:
    """Return ``(warning_after, critical_after)`` for this triple.

    Reads from the adapter's :class:`AdapterCapabilities` so each adapter
    can declare its own cadence (FW reporting flips warning at 2h; GAM
    inventory at 24h). Falls back to module defaults for unknown adapters.
    """
    if sync_kind == KIND_INVENTORY and sync_cadence_minutes is not None:
        warning = timedelta(minutes=sync_cadence_minutes)
        return warning, warning * 3

    caps = _capabilities_for(adapter_type)
    if caps is None:
        if sync_kind == KIND_INVENTORY:
            warning = timedelta(hours=6)
            return warning, warning * 3
        if sync_kind in GUIDANCE_SYNC_KINDS:
            return DEFAULT_GUIDANCE_WARNING, DEFAULT_GUIDANCE_CRITICAL
        return DEFAULT_REPORTING_WARNING, DEFAULT_REPORTING_CRITICAL

    if sync_kind == KIND_INVENTORY:
        return caps.inventory_freshness_warning, caps.inventory_freshness_critical
    if sync_kind in GUIDANCE_SYNC_KINDS:
        return caps.guidance_freshness_warning, caps.guidance_freshness_critical
    return caps.reporting_freshness_warning, caps.reporting_freshness_critical


def freshness_thresholds_for(adapter_type: str, sync_kind: str) -> tuple[timedelta, timedelta]:
    """Public wrapper for the scheduling freshness thresholds.

    Storefront sync-health derives severity from the same adapter-declared
    windows the admin scheduling page renders.
    """
    return _freshness_thresholds(adapter_type, sync_kind)


def _classify_freshness(
    *,
    job: SyncJob | None,
    now: datetime,
    warning_after: timedelta,
    critical_after: timedelta,
) -> str:
    """Three-state classification.

    Rules:
      * No row, or no completed run → ``critical`` (action needed).
      * Last completed run within ``warning_after`` → ``ok``.
      * Last completed run within ``critical_after`` → ``warning``.
      * Older than ``critical_after`` → ``critical``.
      * In-flight (``running``/``queued``) → freshness based on the
        previous completed_at, OR ``warning`` if we have no completed
        run to compare against (still soft-stale, not red).
    """
    if job is None:
        return FRESHNESS_CRITICAL
    if job.status == "completed" and job.completed_at is not None:
        age = now - job.completed_at
        if age <= warning_after:
            return FRESHNESS_OK
        if age <= critical_after:
            return FRESHNESS_WARNING
        return FRESHNESS_CRITICAL
    if job.status in IN_FLIGHT_STATUSES:
        started_at = job.started_at
        if started_at is not None and now - started_at > LONG_RUNNING_CRITICAL_AFTER:
            return FRESHNESS_CRITICAL
        # A recent in-flight row is soft-stale, not red. The matrix only
        # carries the latest row per stream, so it cannot inspect an older
        # successful baseline here.
        return FRESHNESS_WARNING
    # Failed (no completed_at on this row, but the prior completed run
    # isn't joined here). Treat as critical — the failure means the
    # cache wasn't refreshed.
    return FRESHNESS_CRITICAL


def _notes_for(adapter_type: str, sync_kind: str) -> str | None:
    """Operator-facing hint shown beside the row.

    Currently only used for GAM's bundled-reporting case so admins
    don't see "no reporting row" and worry data is missing.
    """
    caps = _capabilities_for(adapter_type)
    if caps is None:
        return None
    if sync_kind == KIND_INVENTORY and getattr(caps, "reporting_bundled_with_inventory", False):
        return "reporting bundled with inventory sync"
    return None


def _last_run_age_seconds(job: SyncJob | None, now: datetime) -> int | None:
    if job is None or job.started_at is None:
        return None
    return int((now - job.started_at).total_seconds())


def _format_age_seconds(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    total = max(0, seconds)
    if total < 60:
        return f"{total}s"
    minutes = total // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    if hours < 48:
        if remaining_minutes:
            return f"{hours}h {remaining_minutes}m"
        return f"{hours}h"
    days = hours // 24
    remaining_hours = hours % 24
    if remaining_hours:
        return f"{days}d {remaining_hours}h"
    return f"{days}d"


def _is_long_running(job: SyncJob | None, now: datetime) -> bool:
    if job is None or job.status not in IN_FLIGHT_STATUSES or job.started_at is None:
        return False
    return now - job.started_at > LONG_RUNNING_WARNING_AFTER


def _long_running_note(notes: str | None, job: SyncJob | None, now: datetime) -> str | None:
    if not _is_long_running(job, now):
        return notes
    warning = "sync has been in flight longer than expected"
    if notes:
        return f"{notes}; {warning}"
    return warning


def _build_row(
    *,
    tenant_id: str,
    tenant_name: str,
    adapter_type: str,
    sync_kind: str,
    job: SyncJob | None,
    now: datetime,
    sync_cadence_minutes: int | None = None,
) -> SchedulingRow:
    supported = _capability_flag(adapter_type, sync_kind)
    warning_after, critical_after = _freshness_thresholds(
        adapter_type,
        sync_kind,
        sync_cadence_minutes=sync_cadence_minutes,
    )
    freshness = _classify_freshness(job=job, now=now, warning_after=warning_after, critical_after=critical_after)
    notes = _long_running_note(_notes_for(adapter_type, sync_kind), job, now)
    last_run_age_seconds = _last_run_age_seconds(job, now)
    last_run_age_label = _format_age_seconds(last_run_age_seconds)
    long_running = _is_long_running(job, now)
    long_running_threshold_seconds = int(LONG_RUNNING_WARNING_AFTER.total_seconds())

    if job is None:
        return SchedulingRow(
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            adapter_type=adapter_type,
            sync_kind=sync_kind,
            supported=supported,
            last_status=None,
            last_started_at=None,
            last_completed_at=None,
            last_sync_id=None,
            last_error_message=None,
            freshness=freshness,
            never_run=True,
            last_run_age_seconds=None,
            last_run_age_label=None,
            long_running=False,
            long_running_threshold_seconds=long_running_threshold_seconds,
            notes=notes,
        )

    return SchedulingRow(
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        adapter_type=adapter_type,
        sync_kind=sync_kind,
        supported=supported,
        last_status=job.status,
        last_started_at=job.started_at,
        last_completed_at=job.completed_at,
        last_sync_id=job.sync_id,
        last_error_message=job.error_message,
        freshness=freshness,
        never_run=False,
        last_run_age_seconds=last_run_age_seconds,
        last_run_age_label=last_run_age_label,
        long_running=long_running,
        long_running_threshold_seconds=long_running_threshold_seconds,
        notes=notes,
    )


def build_scheduling_matrix(session: Session) -> list[SchedulingRow]:
    """Return the full ``/admin/scheduling`` matrix.

    Strategy:
      1. List every tenant that has an AdapterConfig row — those are the
         tenants the scheduler can act on.
      2. Fan out into ``(inventory, reporting)`` per (tenant, adapter_type).
      3. Skip rows where the adapter doesn't declare the capability —
         showing them would imply runnability that doesn't exist.
      4. Pull the most-recent SyncJob row for the remaining triples in
         one cross-tenant query.

    Returns rows sorted by (tenant_name, adapter_type, sync_kind) so the
    HTML table is stable across requests.
    """
    pairs = AdapterConfigAdminRepository(session).list_all()
    if not pairs:
        return []

    expected_triples: list[tuple[str, str, str]] = [
        (p.tenant_id, p.adapter_type, kind)
        for p in pairs
        for kind in _SYNC_KINDS
        if _capability_flag(p.adapter_type, kind)
    ]

    latest = SyncJobAdminRepository(session).latest_for_triples(expected_triples)

    now = datetime.now(UTC)
    rows: list[SchedulingRow] = []
    for pair in pairs:
        for kind in _SYNC_KINDS:
            if not _capability_flag(pair.adapter_type, kind):
                continue
            rows.append(
                _build_row(
                    tenant_id=pair.tenant_id,
                    tenant_name=pair.tenant_name,
                    adapter_type=pair.adapter_type,
                    sync_kind=kind,
                    job=latest.get((pair.tenant_id, pair.adapter_type, kind)),
                    now=now,
                    sync_cadence_minutes=pair.sync_cadence_minutes,
                )
            )

    rows.sort(key=lambda r: (r.tenant_name.lower(), r.adapter_type, r.sync_kind))
    return rows
