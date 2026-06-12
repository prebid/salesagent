"""Derived sync-health contract for embedded tenant surfaces.

Raw ``SyncJob`` rows are operator diagnostics. Storefronts need a smaller
contract: normalized run status, freshness severity, and one recommended
action. This module owns that derivation so ``GET /status`` and webhooks use
the same rules.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from src.services.adapter_sync_orchestration import KIND_INVENTORY, KIND_REPORTING
from src.services.sync_scheduling_view import freshness_thresholds_for

SyncPublicStatus = Literal["running", "success", "failed", "never_run"]
SyncSeverity = Literal["ok", "warning", "critical"]
SyncIssueCategory = Literal["auth", "transient", "permanent", "stale", "unknown"]
SyncIssueAction = Literal["reconnect_adapter", "retry_sync", "wait", "contact_support"]

RUNNING_STATUSES = frozenset({"pending", "queued", "running", "in_progress"})
SUCCESS_STATUSES = frozenset({"completed", "success"})
FAILED_STATUSES = frozenset({"failed", "error"})

NEVER_RUN_GRACE = timedelta(minutes=30)
MAX_PUBLIC_ERROR_LEN = 200


@dataclass(frozen=True)
class SyncRunSnapshot:
    """Minimal immutable projection of a ``SyncJob`` row."""

    sync_run_id: str
    sync_type: str
    adapter_type: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    progress: dict[str, Any] | None = None


@dataclass(frozen=True)
class SyncHealthIssue:
    code: str
    category: SyncIssueCategory
    message: str
    retryable: bool
    action: SyncIssueAction

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
            "retryable": self.retryable,
            "action": self.action,
        }


@dataclass(frozen=True)
class SyncHealth:
    status: SyncPublicStatus
    severity: SyncSeverity
    last_success_at: datetime | None = None
    issue: SyncHealthIssue | None = None
    last_run_at: datetime | None = None
    last_failure_at: datetime | None = None
    next_retry_at: datetime | None = None
    related_sync_run_id: str | None = None


def sync_run_snapshot_from_job(job: Any) -> SyncRunSnapshot:
    """Project a SyncJob-like object onto the derivation input shape."""

    return SyncRunSnapshot(
        sync_run_id=job.sync_id,
        sync_type=job.sync_type,
        adapter_type=job.adapter_type,
        status=job.status,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error_message=job.error_message,
        progress=job.progress,
    )


def normalize_sync_status(raw_status: str | None) -> SyncPublicStatus:
    if raw_status is None:
        return "never_run"
    lowered = raw_status.lower()
    if lowered in RUNNING_STATUSES:
        return "running"
    if lowered in SUCCESS_STATUSES:
        return "success"
    if lowered in FAILED_STATUSES:
        return "failed"
    return "never_run"


def classify_sync_error(message: str | None) -> SyncIssueCategory:
    """Bucket operator-facing errors into storefront-safe categories."""

    if not message:
        return "unknown"
    lowered = message.lower()
    if any(
        fp in lowered
        for fp in (
            "refresh token",
            "invalid_grant",
            "unauthoriz",
            "permission denied",
            "permissionerror.permission_denied",
            "permission_denied",
            "insufficient permissions",
            "no_networks_to_access",
            "authenticationerror.no_networks_to_access",
            "not_allowed",
            "403",
            "401",
        )
    ):
        return "auth"
    if any(
        fp in lowered
        for fp in (
            "timeout",
            "timed out",
            "connection reset",
            "connection refused",
            "rate limit",
            "throttle",
            "5xx",
            "503",
            "502",
            "500",
            "temporarily unavailable",
        )
    ):
        return "transient"
    return "permanent"


def public_sync_error_message(raw: str | None) -> str | None:
    """Scrub a raw ``SyncJob.error_message`` for public payloads."""

    if not raw:
        return None
    first_line = raw.splitlines()[0].strip()
    return first_line[:MAX_PUBLIC_ERROR_LEN]


def derive_sync_health(
    runs: Sequence[SyncRunSnapshot],
    *,
    adapter_type: str,
    sync_type: str,
    tenant_created_at: datetime | None,
    now: datetime | None = None,
) -> SyncHealth:
    """Derive the public health snapshot for one tenant sync stream."""

    current_time = now or datetime.now(UTC)
    latest = _latest_run(runs)
    last_success = _latest_success(runs)
    last_success_at = last_success.completed_at if last_success else None

    if latest is None:
        return _never_run_health(tenant_created_at=tenant_created_at, now=current_time)

    status = normalize_sync_status(latest.status)
    last_run_at = latest.completed_at or latest.started_at
    last_failure_at = latest.completed_at if status == "failed" else None
    related_sync_run_id = latest.sync_run_id
    warning_after, critical_after = _thresholds(adapter_type, sync_type)

    if status == "success":
        return _success_health(
            completed_at=latest.completed_at,
            warning_after=warning_after,
            critical_after=critical_after,
            now=current_time,
            last_run_at=last_run_at,
            related_sync_run_id=related_sync_run_id,
        )

    if status == "running":
        return _running_health(
            latest=latest,
            last_success_at=last_success_at,
            warning_after=warning_after,
            critical_after=critical_after,
            now=current_time,
            related_sync_run_id=related_sync_run_id,
        )

    if status == "failed":
        return _failed_health(
            latest=latest,
            last_success_at=last_success_at,
            warning_after=warning_after,
            critical_after=critical_after,
            now=current_time,
            last_run_at=last_run_at,
            last_failure_at=last_failure_at,
        )

    return _never_run_health(tenant_created_at=tenant_created_at, now=current_time)


def previous_runs_for_transition(
    runs: Sequence[SyncRunSnapshot],
    *,
    sync_run_id: str,
    previous_status: str | None,
    previous_completed_at: datetime | None,
    previous_error_message: str | None,
) -> list[SyncRunSnapshot]:
    """Return a run list representing state before a terminal transition."""

    out: list[SyncRunSnapshot] = []
    for run in runs:
        if run.sync_run_id != sync_run_id:
            out.append(run)
            continue
        if previous_status is None:
            continue
        out.append(
            replace(
                run,
                status=previous_status,
                completed_at=previous_completed_at,
                error_message=previous_error_message,
            )
        )
    return out


def build_sync_health_changed_payload(
    *,
    current: SyncHealth,
    previous: SyncHealth,
    sync_type: str,
    adapter_type: str,
) -> dict[str, Any]:
    """Construct the ``sync_health.changed`` webhook data block."""

    issue = current.issue
    return {
        "sync_type": sync_type,
        "adapter_type": adapter_type,
        "health": current.severity,
        "previous_health": previous.severity,
        "reason": issue.category if issue else None,
        "message": issue.message if issue else None,
        "action": issue.action if issue else None,
        "last_success_at": _iso(current.last_success_at),
        "last_failure_at": _iso(current.last_failure_at),
        "next_retry_at": _iso(current.next_retry_at),
        "related_sync_run_id": current.related_sync_run_id,
    }


def _latest_run(runs: Sequence[SyncRunSnapshot]) -> SyncRunSnapshot | None:
    if not runs:
        return None

    terminal_runs = [run for run in runs if normalize_sync_status(run.status) in {"success", "failed"}]
    running_runs = [run for run in runs if normalize_sync_status(run.status) == "running"]
    latest_terminal = max(terminal_runs, key=_terminal_run_recency_key) if terminal_runs else None
    latest_running = max(running_runs, key=_started_run_recency_key) if running_runs else None

    if latest_running is not None and _is_running_newer_than_terminal(latest_running, latest_terminal):
        return latest_running
    if latest_terminal is not None:
        return latest_terminal
    return max(runs, key=_started_run_recency_key)


def _is_running_newer_than_terminal(
    running: SyncRunSnapshot,
    terminal: SyncRunSnapshot | None,
) -> bool:
    if terminal is None:
        return True
    terminal_at = terminal.completed_at or terminal.started_at or datetime.min.replace(tzinfo=UTC)
    running_started_at = running.started_at or datetime.min.replace(tzinfo=UTC)
    return running_started_at > terminal_at


def _terminal_run_recency_key(run: SyncRunSnapshot) -> tuple[datetime, int, str]:
    public_status = normalize_sync_status(run.status)
    status_rank = {"never_run": 0, "failed": 1, "running": 2, "success": 3}[public_status]
    return (run.completed_at or run.started_at or datetime.min.replace(tzinfo=UTC), status_rank, run.sync_run_id)


def _started_run_recency_key(run: SyncRunSnapshot) -> tuple[datetime, str]:
    return (run.started_at or datetime.min.replace(tzinfo=UTC), run.sync_run_id)


def _latest_success(runs: Sequence[SyncRunSnapshot]) -> SyncRunSnapshot | None:
    successes = [run for run in runs if normalize_sync_status(run.status) == "success" and run.completed_at is not None]
    if not successes:
        return None
    return max(successes, key=lambda run: (run.completed_at or datetime.min.replace(tzinfo=UTC), run.sync_run_id))


def _never_run_health(*, tenant_created_at: datetime | None, now: datetime) -> SyncHealth:
    issue = SyncHealthIssue(
        code="sync_never_run",
        category="stale",
        message="Initial sync has not completed yet.",
        retryable=True,
        action="wait",
    )
    if tenant_created_at is not None and now - tenant_created_at > NEVER_RUN_GRACE:
        issue = SyncHealthIssue(
            code="sync_never_run",
            category="stale",
            message="Run the first sync before showing inventory-dependent controls.",
            retryable=True,
            action="retry_sync",
        )
        return SyncHealth(status="never_run", severity="critical", issue=issue)
    return SyncHealth(status="never_run", severity="warning", issue=issue)


def _success_health(
    *,
    completed_at: datetime | None,
    warning_after: timedelta,
    critical_after: timedelta,
    now: datetime,
    last_run_at: datetime | None,
    related_sync_run_id: str,
) -> SyncHealth:
    if completed_at is None:
        return SyncHealth(
            status="success",
            severity="critical",
            issue=_stale_issue("Sync completed without a completion timestamp.", critical=True),
            last_run_at=last_run_at,
            related_sync_run_id=related_sync_run_id,
        )
    age = now - completed_at
    if age <= warning_after:
        return SyncHealth(
            status="success",
            severity="ok",
            last_success_at=completed_at,
            last_run_at=last_run_at,
            related_sync_run_id=related_sync_run_id,
        )
    critical = age > critical_after
    return SyncHealth(
        status="success",
        severity="critical" if critical else "warning",
        last_success_at=completed_at,
        issue=_stale_issue(_stale_message(critical=critical), critical=critical),
        last_run_at=last_run_at,
        related_sync_run_id=related_sync_run_id,
    )


def _running_health(
    *,
    latest: SyncRunSnapshot,
    last_success_at: datetime | None,
    warning_after: timedelta,
    critical_after: timedelta,
    now: datetime,
    related_sync_run_id: str,
) -> SyncHealth:
    last_run_at = latest.completed_at or latest.started_at
    started_at = latest.started_at or now
    running_age = now - started_at
    baseline = _baseline_freshness(last_success_at, warning_after=warning_after, critical_after=critical_after, now=now)

    if baseline == "ok" and running_age <= warning_after:
        return SyncHealth(
            status="running",
            severity="ok",
            last_success_at=last_success_at,
            last_run_at=last_run_at,
            related_sync_run_id=related_sync_run_id,
        )

    if baseline == "critical" and last_success_at is None and running_age > critical_after:
        severity: SyncSeverity = "critical"
    else:
        severity = "warning"
    return SyncHealth(
        status="running",
        severity=severity,
        last_success_at=last_success_at,
        issue=SyncHealthIssue(
            code="sync_running",
            category="stale",
            message="Sync is still running.",
            retryable=False,
            action="wait",
        ),
        last_run_at=last_run_at,
        related_sync_run_id=related_sync_run_id,
    )


def _failed_health(
    *,
    latest: SyncRunSnapshot,
    last_success_at: datetime | None,
    warning_after: timedelta,
    critical_after: timedelta,
    now: datetime,
    last_run_at: datetime | None,
    last_failure_at: datetime | None,
) -> SyncHealth:
    category = classify_sync_error(latest.error_message)
    baseline = _baseline_freshness(last_success_at, warning_after=warning_after, critical_after=critical_after, now=now)
    critical = category in {"auth", "permanent"} or baseline == "critical" or last_success_at is None
    issue = _failure_issue(category, latest.error_message)
    return SyncHealth(
        status="failed",
        severity="critical" if critical else "warning",
        last_success_at=last_success_at,
        issue=issue,
        last_run_at=last_run_at,
        last_failure_at=last_failure_at,
        related_sync_run_id=latest.sync_run_id,
    )


def _baseline_freshness(
    last_success_at: datetime | None,
    *,
    warning_after: timedelta,
    critical_after: timedelta,
    now: datetime,
) -> SyncSeverity:
    if last_success_at is None:
        return "critical"
    age = now - last_success_at
    if age <= warning_after:
        return "ok"
    if age <= critical_after:
        return "warning"
    return "critical"


def _failure_issue(category: SyncIssueCategory, raw_message: str | None) -> SyncHealthIssue:
    if category == "auth":
        return SyncHealthIssue(
            code="adapter_auth_failed",
            category="auth",
            message="Reconnect the ad server adapter.",
            retryable=False,
            action="reconnect_adapter",
        )
    if category == "transient":
        return SyncHealthIssue(
            code="sync_transient_failure",
            category="transient",
            message=public_sync_error_message(raw_message) or "Sync failed temporarily. Retry the sync.",
            retryable=True,
            action="retry_sync",
        )
    if category == "unknown":
        return SyncHealthIssue(
            code="sync_failed",
            category="unknown",
            message="Sync failed. Check Sales Agent admin details.",
            retryable=False,
            action="contact_support",
        )
    return SyncHealthIssue(
        code="sync_permanent_failure",
        category="permanent",
        message="Sync failed. Check Sales Agent admin details.",
        retryable=False,
        action="contact_support",
    )


def _stale_issue(message: str, *, critical: bool) -> SyncHealthIssue:
    return SyncHealthIssue(
        code="sync_stale",
        category="stale",
        message=message,
        retryable=True,
        action="retry_sync" if critical else "wait",
    )


def _stale_message(*, critical: bool) -> str:
    if critical:
        return "Synced data is too stale to rely on."
    return "Synced data is getting stale."


def _thresholds(adapter_type: str, sync_type: str) -> tuple[timedelta, timedelta]:
    scheduling_kind = KIND_REPORTING if sync_type == KIND_REPORTING else KIND_INVENTORY
    return freshness_thresholds_for(adapter_type, scheduling_kind)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
