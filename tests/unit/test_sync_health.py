"""Unit coverage for embedded sync-health derivation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.admin.services.sync_health import (
    SyncRunSnapshot,
    build_sync_health_changed_payload,
    classify_sync_error,
    derive_sync_health,
    normalize_sync_status,
    previous_runs_for_transition,
)

NOW = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)


def _run(**overrides) -> SyncRunSnapshot:
    defaults = {
        "sync_run_id": "sync_1",
        "sync_type": "inventory",
        "adapter_type": "google_ad_manager",
        "status": "completed",
        "started_at": NOW - timedelta(minutes=5),
        "completed_at": NOW - timedelta(minutes=1),
        "error_message": None,
    }
    defaults.update(overrides)
    return SyncRunSnapshot(**defaults)


def _health(runs: list[SyncRunSnapshot], *, sync_type: str = "inventory"):
    return derive_sync_health(
        runs,
        adapter_type="google_ad_manager",
        sync_type=sync_type,
        tenant_created_at=NOW - timedelta(hours=2),
        now=NOW,
    )


class TestNormalizeSyncStatus:
    def test_pending_and_queued_are_public_running(self):
        assert normalize_sync_status("pending") == "running"
        assert normalize_sync_status("queued") == "running"
        assert normalize_sync_status("in_progress") == "running"

    def test_completed_and_failed_statuses_are_normalized(self):
        assert normalize_sync_status("completed") == "success"
        assert normalize_sync_status("success") == "success"
        assert normalize_sync_status("error") == "failed"
        assert normalize_sync_status(None) == "never_run"


class TestClassifySyncError:
    @pytest.mark.parametrize(
        "message",
        [
            "Exception: Error running GAM report: [PermissionError.PERMISSION_DENIED @ ]",
            "Exception: Error running GAM report: [AuthenticationError.NO_NETWORKS_TO_ACCESS @ ]",
            "GAM authentication failed: [AuthenticationError.NO_NETWORKS_TO_ACCESS @ ]",
        ],
    )
    def test_gam_permission_reason_codes_are_auth_issues(self, message):
        assert classify_sync_error(message) == "auth"


class TestDeriveSyncHealth:
    def test_fresh_success_is_ok_with_no_issue(self):
        health = _health([_run()])

        assert health.status == "success"
        assert health.severity == "ok"
        assert health.issue is None
        assert health.last_success_at == NOW - timedelta(minutes=1)

    def test_fresh_transient_failure_with_fresh_baseline_is_warning(self):
        health = _health(
            [
                _run(
                    sync_run_id="sync_failed",
                    status="failed",
                    started_at=NOW - timedelta(minutes=2),
                    completed_at=NOW - timedelta(minutes=1),
                    error_message="Timeout while reading GAM inventory",
                ),
                _run(
                    sync_run_id="sync_success",
                    completed_at=NOW - timedelta(hours=1),
                    started_at=NOW - timedelta(hours=1, minutes=3),
                ),
            ]
        )

        assert health.status == "failed"
        assert health.severity == "warning"
        assert health.last_success_at == NOW - timedelta(hours=1)
        assert health.issue is not None
        assert health.issue.category == "transient"
        assert health.issue.action == "retry_sync"

    def test_stale_success_past_critical_threshold_is_critical(self):
        health = _health(
            [
                _run(
                    completed_at=NOW - timedelta(days=4),
                    started_at=NOW - timedelta(days=4, minutes=5),
                )
            ]
        )

        assert health.status == "success"
        assert health.severity == "critical"
        assert health.issue is not None
        assert health.issue.category == "stale"

    def test_auth_failure_is_critical_even_with_fresh_baseline(self):
        health = _health(
            [
                _run(
                    sync_run_id="sync_failed",
                    status="failed",
                    started_at=NOW - timedelta(minutes=2),
                    completed_at=NOW - timedelta(minutes=1),
                    error_message="Refresh token revoked",
                ),
                _run(
                    sync_run_id="sync_success",
                    completed_at=NOW - timedelta(hours=1),
                    started_at=NOW - timedelta(hours=1, minutes=3),
                ),
            ]
        )

        assert health.status == "failed"
        assert health.severity == "critical"
        assert health.issue is not None
        assert health.issue.category == "auth"
        assert health.issue.action == "reconnect_adapter"

    def test_queued_without_baseline_is_public_running_warning(self):
        health = _health(
            [
                _run(
                    status="queued",
                    started_at=NOW - timedelta(minutes=2),
                    completed_at=None,
                )
            ]
        )

        assert health.status == "running"
        assert health.severity == "warning"
        assert health.last_success_at is None
        assert health.issue is not None
        assert health.issue.action == "wait"

    def test_success_completed_after_later_started_failure_clears_health(self):
        """Completion time, not start time, owns terminal sync recency."""
        success_at = NOW - timedelta(minutes=1)
        health = _health(
            [
                _run(
                    sync_run_id="sync_z_failed",
                    status="failed",
                    started_at=NOW - timedelta(minutes=5),
                    completed_at=success_at,
                    error_message="Timeout while reading GAM custom targeting",
                ),
                _run(
                    sync_run_id="sync_a_success",
                    status="completed",
                    started_at=NOW - timedelta(minutes=10),
                    completed_at=success_at,
                ),
            ]
        )

        assert health.status == "success"
        assert health.severity == "ok"
        assert health.issue is None
        assert health.last_success_at == success_at
        assert health.related_sync_run_id == "sync_a_success"

    def test_success_completed_after_running_started_clears_retry(self):
        health = _health(
            [
                _run(
                    sync_run_id="sync_success",
                    status="completed",
                    started_at=NOW - timedelta(minutes=10),
                    completed_at=NOW - timedelta(minutes=1),
                ),
                _run(
                    sync_run_id="sync_running",
                    status="running",
                    started_at=NOW - timedelta(minutes=5),
                    completed_at=None,
                ),
            ]
        )

        assert health.status == "success"
        assert health.related_sync_run_id == "sync_success"

    def test_running_run_after_success_stays_visible_until_terminal(self):
        health = _health(
            [
                _run(
                    sync_run_id="sync_success",
                    status="completed",
                    started_at=NOW - timedelta(minutes=10),
                    completed_at=NOW - timedelta(minutes=6),
                ),
                _run(
                    sync_run_id="sync_running",
                    status="running",
                    started_at=NOW - timedelta(minutes=5),
                    completed_at=None,
                ),
            ]
        )

        assert health.status == "running"
        assert health.related_sync_run_id == "sync_running"

    def test_later_success_clears_older_running_retry(self):
        health = _health(
            [
                _run(
                    sync_run_id="sync_12271007_custom_targeting_retry",
                    sync_type="custom_targeting",
                    status="running",
                    started_at=NOW - timedelta(minutes=10),
                    completed_at=None,
                ),
                _run(
                    sync_run_id="sync_12271007_custom_targeting_success",
                    sync_type="custom_targeting",
                    status="completed",
                    started_at=NOW - timedelta(minutes=5),
                    completed_at=NOW - timedelta(minutes=1),
                ),
            ],
            sync_type="custom_targeting",
        )

        assert health.status == "success"
        assert health.severity == "ok"
        assert health.issue is None
        assert health.related_sync_run_id == "sync_12271007_custom_targeting_success"


class TestHealthChangedPayload:
    def test_previous_runs_for_transition_reconstructs_running_state(self):
        current_runs = [
            _run(
                sync_run_id="sync_failed",
                status="failed",
                completed_at=NOW - timedelta(minutes=1),
                error_message="Timeout",
            )
        ]

        previous = previous_runs_for_transition(
            current_runs,
            sync_run_id="sync_failed",
            previous_status="running",
            previous_completed_at=None,
            previous_error_message=None,
        )

        assert len(previous) == 1
        assert previous[0].status == "running"
        assert previous[0].completed_at is None
        assert previous[0].error_message is None

    def test_payload_is_compact_and_actionable(self):
        current = _health(
            [
                _run(
                    sync_run_id="sync_failed",
                    status="failed",
                    completed_at=NOW - timedelta(minutes=1),
                    error_message="Refresh token revoked",
                )
            ]
        )
        previous = _health([])

        payload = build_sync_health_changed_payload(
            current=current,
            previous=previous,
            sync_type="inventory",
            adapter_type="google_ad_manager",
        )

        assert payload["health"] == "critical"
        assert payload["previous_health"] == "critical"
        assert payload["reason"] == "auth"
        assert payload["action"] == "reconnect_adapter"
        assert payload["related_sync_run_id"] == "sync_failed"
