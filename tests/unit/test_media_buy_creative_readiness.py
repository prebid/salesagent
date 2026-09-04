"""Unit tests for shared creative finalize-readiness predicate (#1696)."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import ANY, MagicMock, patch

from src.core.database.models import PersistedMediaBuyStatus
from src.core.schemas.creative import FINALIZE_READY_CREATIVE_STATUSES, CreativeStatusEnum
from src.services.media_buy_creative_readiness import (
    CreativeFinalizeReadiness,
    _coerce_flight_boundary,
    apply_creative_finalize_hold,
    apply_creative_finalize_ready,
    compute_media_buy_status_from_flight_dates,
    evaluate_creative_finalize_readiness,
    evaluate_creative_finalize_readiness_for_session,
    finalize_media_buy_after_creative_approval,
    log_creative_finalize_hold,
    mark_media_buy_adapter_failed,
    stamp_media_buy_approval,
)


def _assignment(creative_id: str, principal_id: str = "p1") -> MagicMock:
    a = MagicMock()
    a.creative_id = creative_id
    a.principal_id = principal_id
    return a


def _creative(creative_id: str, status: str) -> MagicMock:
    c = MagicMock()
    c.creative_id = creative_id
    c.status = status
    return c


def _repos(assignments: list, creatives_by_call: list[list] | None = None):
    """Mock assignment + creative repositories for the shared predicate."""
    assignments_repo = MagicMock()
    creatives_repo = MagicMock()
    assignments_repo.get_by_media_buy.return_value = assignments
    if creatives_by_call is None:
        creatives_repo.get_by_ids.return_value = []
    elif len(creatives_by_call) == 1:
        creatives_repo.get_by_ids.return_value = creatives_by_call[0]
    else:
        creatives_repo.get_by_ids.side_effect = creatives_by_call
    return assignments_repo, creatives_repo


class TestEvaluateCreativeFinalizeReadiness:
    def test_zero_assignments_not_ready_no_assignments(self):
        assignments_repo, creatives_repo = _repos([])
        result = evaluate_creative_finalize_readiness(assignments_repo, creatives_repo, media_buy_id="mb_1")
        assert result.ready is False
        assert result.unapproved_creative_ids == []
        assert result.hold_reason == "no_assignments"
        assert result.hold_message is not None
        assert "assigned" in result.hold_message
        creatives_repo.get_by_ids.assert_not_called()

    def test_all_approved_ready(self):
        assignments_repo, creatives_repo = _repos(
            [_assignment("c1"), _assignment("c2")],
            [[_creative("c1", "approved"), _creative("c2", "approved")]],
        )
        result = evaluate_creative_finalize_readiness(assignments_repo, creatives_repo, media_buy_id="mb_1")
        assert result.ready is True
        assert result.unapproved_creative_ids == []
        assert result.hold_reason is None
        assert result.hold_message is None
        creatives_repo.get_by_ids.assert_called_once_with(["c1", "c2"], "p1")

    def test_active_status_counts_as_ready(self):
        """Legacy ``active`` remains in the shared allowlist; pin against enum + sole legacy."""
        enum_values = {m.value for m in CreativeStatusEnum}
        assert FINALIZE_READY_CREATIVE_STATUSES - {"active"} <= enum_values
        assert FINALIZE_READY_CREATIVE_STATUSES - enum_values == {"active"}
        assignments_repo, creatives_repo = _repos(
            [_assignment("c1")],
            [[_creative("c1", "active")]],
        )
        result = evaluate_creative_finalize_readiness(assignments_repo, creatives_repo, media_buy_id="mb_1")
        assert result.ready is True
        assert result.hold_reason is None

    def test_pending_creative_not_ready(self):
        assignments_repo, creatives_repo = _repos(
            [_assignment("c1"), _assignment("c2")],
            [[_creative("c1", "approved"), _creative("c2", "pending_review")]],
        )
        result = evaluate_creative_finalize_readiness(assignments_repo, creatives_repo, media_buy_id="mb_1")
        assert result.ready is False
        assert result.unapproved_creative_ids == ["c2"]
        assert result.hold_reason == "unapproved_creatives"
        assert "1 creative" in (result.hold_message or "")

    def test_rejected_creative_not_ready(self):
        assignments_repo, creatives_repo = _repos(
            [_assignment("c1")],
            [[_creative("c1", "rejected")]],
        )
        result = evaluate_creative_finalize_readiness(assignments_repo, creatives_repo, media_buy_id="mb_1")
        assert result.ready is False
        assert result.unapproved_creative_ids == ["c1"]
        assert result.hold_reason == "unapproved_creatives"

    def test_missing_creative_row_counts_as_unapproved(self):
        assignments_repo, creatives_repo = _repos(
            [_assignment("c1"), _assignment("c_missing")],
            [[_creative("c1", "approved")]],
        )
        result = evaluate_creative_finalize_readiness(assignments_repo, creatives_repo, media_buy_id="mb_1")
        assert result.ready is False
        assert result.hold_reason == "unapproved_creatives"
        assert "c_missing" in result.unapproved_creative_ids

    def test_loads_creatives_per_principal(self):
        assignments_repo, creatives_repo = _repos(
            [_assignment("c1", "p1"), _assignment("c2", "p2")],
            [[_creative("c1", "approved")], [_creative("c2", "approved")]],
        )
        result = evaluate_creative_finalize_readiness(assignments_repo, creatives_repo, media_buy_id="mb_x")
        assert result.ready is True
        assert creatives_repo.get_by_ids.call_count == 2
        creatives_repo.get_by_ids.assert_any_call(["c1"], "p1")
        creatives_repo.get_by_ids.assert_any_call(["c2"], "p2")
        assignments_repo.get_by_media_buy.assert_called_once_with("mb_x")


class TestEvaluateCreativeFinalizeReadinessForSession:
    def test_builds_repos_and_delegates(self):
        session = MagicMock()
        readiness = CreativeFinalizeReadiness(
            ready=False,
            unapproved_creative_ids=[],
            hold_reason="no_assignments",
            hold_message="held",
        )
        with (
            patch(
                "src.services.media_buy_creative_readiness.CreativeAssignmentRepository",
            ) as mock_assign_cls,
            patch(
                "src.services.media_buy_creative_readiness.CreativeRepository",
            ) as mock_creative_cls,
            patch(
                "src.services.media_buy_creative_readiness.evaluate_creative_finalize_readiness",
                return_value=readiness,
            ) as mock_eval,
        ):
            result = evaluate_creative_finalize_readiness_for_session(session, "tenant_1", media_buy_id="mb_1")

        assert result is readiness
        mock_assign_cls.assert_called_once_with(session, "tenant_1")
        mock_creative_cls.assert_called_once_with(session, "tenant_1")
        mock_eval.assert_called_once_with(
            mock_assign_cls.return_value,
            mock_creative_cls.return_value,
            media_buy_id="mb_1",
        )


class TestApplyCreativeFinalizeHold:
    def test_sets_status_provenance_and_logs_hold_reason(self, caplog):
        media_buy = MagicMock()
        media_buy.media_buy_id = "mb_hold"
        readiness = CreativeFinalizeReadiness(
            ready=False,
            unapproved_creative_ids=["c1"],
            hold_reason="unapproved_creatives",
            hold_message="waiting",
        )
        with caplog.at_level("INFO", logger="src.services.media_buy_creative_readiness"):
            apply_creative_finalize_hold(media_buy, readiness, approved_by="op@example.com")

        assert media_buy.status == "pending_creatives"
        assert media_buy.approved_by == "op@example.com"
        assert isinstance(media_buy.approved_at, datetime)
        assert any("hold_reason=unapproved_creatives" in r.message for r in caplog.records)
        assert any("[APPROVAL]" in r.message for r in caplog.records)
        assert any("event=creative_finalize_hold" in r.message for r in caplog.records)
        assert not any(" reason=" in r.message for r in caplog.records)


class TestApplyCreativeFinalizeReady:
    def test_stamps_provenance_and_flight_status(self):
        media_buy = MagicMock()
        media_buy.media_buy_id = "mb_ready"
        media_buy.start_time = datetime.now(UTC) + timedelta(days=7)
        media_buy.end_time = datetime.now(UTC) + timedelta(days=37)
        media_buy.start_date = None
        media_buy.end_date = None
        apply_creative_finalize_ready(media_buy, approved_by="op@example.com")
        assert media_buy.approved_by == "op@example.com"
        assert isinstance(media_buy.approved_at, datetime)
        assert media_buy.status == "pending_start"


class TestStampMediaBuyApproval:
    def test_stamps_provenance(self):
        media_buy = MagicMock()
        stamp_media_buy_approval(media_buy, approved_by="op@example.com")
        assert media_buy.approved_by == "op@example.com"
        assert isinstance(media_buy.approved_at, datetime)


class TestMarkMediaBuyAdapterFailed:
    def test_logs_approval_error_then_updates_status(self, caplog):
        mock_repo = MagicMock()
        mock_repo.update_status.return_value = True
        mock_session = MagicMock()
        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_session)
        mock_uow.__exit__ = MagicMock(return_value=None)

        with (
            patch("src.services.media_buy_creative_readiness.get_db_session", return_value=mock_uow),
            patch(
                "src.services.media_buy_creative_readiness.MediaBuyRepository",
                return_value=mock_repo,
            ),
            caplog.at_level("ERROR", logger="src.services.media_buy_creative_readiness"),
        ):
            mark_media_buy_adapter_failed("mb_x", "tenant_1", error_msg="adapter boom")

        mock_repo.update_status.assert_called_once_with("mb_x", PersistedMediaBuyStatus.FAILED)
        mock_session.commit.assert_called_once_with()
        assert any("[APPROVAL] Adapter creation failed for mb_x: adapter boom" in r.message for r in caplog.records)

    def test_custom_status_for_creatives_recoverable_path(self):
        mock_repo = MagicMock()
        mock_repo.update_status.return_value = True
        mock_session = MagicMock()
        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_session)
        mock_uow.__exit__ = MagicMock(return_value=None)

        with (
            patch("src.services.media_buy_creative_readiness.get_db_session", return_value=mock_uow),
            patch(
                "src.services.media_buy_creative_readiness.MediaBuyRepository",
                return_value=mock_repo,
            ),
        ):
            mark_media_buy_adapter_failed(
                "mb_x",
                "tenant_1",
                error_msg="adapter boom",
                status=PersistedMediaBuyStatus.PENDING_CREATIVES,
            )

        mock_repo.update_status.assert_called_once_with("mb_x", PersistedMediaBuyStatus.PENDING_CREATIVES)


def _approval_executed(**kwargs):
    from src.core.tools.media_buy_create import ApprovalOutcome, ApprovalResult

    return ApprovalResult(outcome=ApprovalOutcome.EXECUTED, **kwargs)


def _approval_failed(error_msg: str):
    from src.core.tools.media_buy_create import ApprovalResult

    return ApprovalResult.failed(error_msg)


class TestFinalizeMediaBuyApproval:
    def test_held_applies_hold_and_commits_without_execute(self):
        from src.services.media_buy_creative_readiness import finalize_media_buy_approval

        session = MagicMock()
        media_buy = MagicMock()
        media_buy.media_buy_id = "mb_hold"
        readiness = CreativeFinalizeReadiness(
            ready=False,
            unapproved_creative_ids=[],
            hold_reason="no_assignments",
            hold_message="held msg",
        )
        mock_repo = MagicMock()

        with (
            patch(
                "src.services.media_buy_creative_readiness.evaluate_creative_finalize_readiness_for_session",
                return_value=readiness,
            ) as mock_eval,
            patch(
                "src.services.media_buy_creative_readiness.MediaBuyRepository",
                return_value=mock_repo,
            ) as mock_repo_cls,
            patch("src.services.media_buy_creative_readiness.log_creative_finalize_hold") as mock_log,
            patch("src.services.media_buy_creative_readiness.apply_creative_finalize_ready") as mock_ready,
            patch(
                "src.core.tools.media_buy_create.execute_approved_media_buy",
            ) as mock_execute,
        ):
            outcome = finalize_media_buy_approval(session, "t1", media_buy, approved_by="op@example.com")

        mock_eval.assert_called_once_with(session, "t1", media_buy_id="mb_hold")
        mock_repo_cls.assert_called_once_with(session, "t1")
        mock_repo.update_status.assert_called_once_with(
            "mb_hold",
            PersistedMediaBuyStatus.PENDING_CREATIVES,
            approved_at=ANY,
            approved_by="op@example.com",
        )
        mock_log.assert_called_once_with("mb_hold", readiness)
        session.commit.assert_called_once_with()
        mock_ready.assert_not_called()
        mock_execute.assert_not_called()
        assert outcome.kind == "held"
        assert outcome.hold_message == "held msg"
        assert outcome.hold_reason == "no_assignments"

    def test_finalized_delegates_to_sole_writer_without_pre_stamp(self):
        from src.services.media_buy_creative_readiness import finalize_media_buy_approval

        session = MagicMock()
        media_buy = MagicMock()
        media_buy.media_buy_id = "mb_ok"
        readiness = CreativeFinalizeReadiness(
            ready=True,
            unapproved_creative_ids=[],
            hold_reason=None,
            hold_message=None,
        )

        with (
            patch(
                "src.services.media_buy_creative_readiness.evaluate_creative_finalize_readiness_for_session",
                return_value=readiness,
            ),
            patch("src.services.media_buy_creative_readiness.apply_creative_finalize_ready") as mock_ready,
            patch(
                "src.services.media_buy_creative_readiness.MediaBuyRepository",
            ) as mock_repo_cls,
            patch(
                "src.core.media_buy_status.resolve_canonical_status",
                return_value="pending_start",
            ) as mock_resolve,
            patch(
                "src.core.tools.media_buy_create.execute_approved_media_buy",
                return_value=_approval_executed(
                    status=PersistedMediaBuyStatus.SCHEDULED,
                    confirmed_at=datetime(2026, 1, 1, tzinfo=UTC),
                    revision=2,
                ),
            ) as mock_execute,
            patch("src.services.media_buy_creative_readiness.mark_media_buy_adapter_failed") as mock_fail,
        ):
            fresh = MagicMock()
            mock_repo_cls.return_value.get_by_id.return_value = fresh
            outcome = finalize_media_buy_approval(session, "t1", media_buy, approved_by="op@example.com")

        mock_ready.assert_not_called()
        session.commit.assert_not_called()
        session.expire_all.assert_called_once_with()
        mock_repo_cls.assert_called_once_with(session, "t1")
        mock_repo_cls.return_value.get_by_id.assert_called_once_with("mb_ok")
        mock_resolve.assert_called_once_with(fresh, ANY)
        mock_execute.assert_called_once_with(
            "mb_ok",
            "t1",
            approved_by="op@example.com",
            approved_at=ANY,
        )
        mock_fail.assert_not_called()
        assert outcome.kind == "finalized"
        assert outcome.webhook_media_buy_status == "pending_start"
        assert outcome.revision == 2

    def test_adapter_failed_rolls_back_via_shared_applier(self):
        from src.services.media_buy_creative_readiness import finalize_media_buy_approval

        session = MagicMock()
        media_buy = MagicMock()
        media_buy.media_buy_id = "mb_fail"
        readiness = CreativeFinalizeReadiness(
            ready=True,
            unapproved_creative_ids=[],
            hold_reason=None,
            hold_message=None,
        )

        with (
            patch(
                "src.services.media_buy_creative_readiness.evaluate_creative_finalize_readiness_for_session",
                return_value=readiness,
            ),
            patch("src.services.media_buy_creative_readiness.apply_creative_finalize_ready"),
            patch(
                "src.core.media_buy_status.resolve_canonical_status",
                return_value="pending_start",
            ),
            patch(
                "src.core.tools.media_buy_create.execute_approved_media_buy",
                return_value=_approval_failed("boom"),
            ),
            patch("src.services.media_buy_creative_readiness.mark_media_buy_adapter_failed") as mock_fail,
        ):
            outcome = finalize_media_buy_approval(session, "t1", media_buy, approved_by="op@example.com")

        mock_fail.assert_called_once_with(
            "mb_fail",
            "t1",
            error_msg="boom",
            status=PersistedMediaBuyStatus.FAILED,
            approved_by="op@example.com",
            approved_at=ANY,
        )
        assert outcome.kind == "adapter_failed"
        assert outcome.error_msg == "boom"


class TestFinalizeMediaBuyAfterCreativeApproval:
    def test_success_delegates_to_sole_writer_without_extra_stamp(self):
        with (
            patch(
                "src.core.tools.media_buy_create.execute_approved_media_buy",
                return_value=_approval_executed(
                    status=PersistedMediaBuyStatus.ACTIVE,
                    confirmed_at=datetime(2026, 1, 1, tzinfo=UTC),
                    revision=3,
                ),
            ) as mock_execute,
            patch(
                "src.services.media_buy_creative_readiness.apply_creative_finalize_ready",
            ) as mock_ready,
            patch(
                "src.services.media_buy_creative_readiness.mark_media_buy_adapter_failed",
            ) as mock_fail,
        ):
            outcome = finalize_media_buy_after_creative_approval(
                "mb_ok",
                "t1",
                approved_by="op@example.com",
            )

        mock_execute.assert_called_once_with(
            "mb_ok",
            "t1",
            approved_by="op@example.com",
            approved_at=ANY,
        )
        mock_ready.assert_not_called()
        mock_fail.assert_not_called()
        assert outcome.kind == "finalized"
        assert outcome.revision == 3

    def test_failure_keeps_recoverable_pending_creatives_without_stamping(self):
        with (
            patch(
                "src.core.tools.media_buy_create.execute_approved_media_buy",
                return_value=_approval_failed("boom"),
            ) as mock_execute,
            patch(
                "src.services.media_buy_creative_readiness.apply_creative_finalize_ready",
            ) as mock_ready,
            patch(
                "src.services.media_buy_creative_readiness.mark_media_buy_adapter_failed",
            ) as mock_fail,
        ):
            outcome = finalize_media_buy_after_creative_approval(
                "mb_fail",
                "t1",
                approved_by="op@example.com",
            )

        mock_execute.assert_called_once_with(
            "mb_fail",
            "t1",
            approved_by="op@example.com",
            approved_at=ANY,
        )
        mock_fail.assert_called_once_with(
            "mb_fail",
            "t1",
            error_msg="boom",
            status=PersistedMediaBuyStatus.PENDING_CREATIVES,
        )
        mock_ready.assert_not_called()
        assert outcome.kind == "adapter_failed"
        assert outcome.error_msg == "boom"


class TestLogCreativeFinalizeHold:
    def test_uses_hold_reason_key(self, caplog):
        readiness = CreativeFinalizeReadiness(
            ready=False,
            unapproved_creative_ids=[],
            hold_reason="no_assignments",
            hold_message="held",
        )
        with caplog.at_level("INFO", logger="src.services.media_buy_creative_readiness"):
            log_creative_finalize_hold("mb_x", readiness, context_tag="[CREATIVE APPROVAL]")
        assert any("hold_reason=no_assignments" in r.message for r in caplog.records)
        assert any("[CREATIVE APPROVAL]" in r.message for r in caplog.records)
        assert any("event=creative_finalize_hold" in r.message for r in caplog.records)


class TestCoerceFlightBoundary:
    def test_aware_datetime_passthrough(self):
        dt = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        assert _coerce_flight_boundary(dt, None, end_of_day=False) == dt

    def test_naive_datetime_assumes_utc(self):
        dt = datetime(2026, 1, 1, 12, 0)
        result = _coerce_flight_boundary(dt, None, end_of_day=False)
        assert result == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def test_date_start_of_day(self):
        result = _coerce_flight_boundary(None, date(2026, 3, 1), end_of_day=False)
        assert result == datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

    def test_date_end_of_day(self):
        result = _coerce_flight_boundary(None, date(2026, 3, 1), end_of_day=True)
        assert result is not None
        assert result.date() == date(2026, 3, 1)
        assert result.hour == 23

    def test_none_when_both_missing(self):
        assert _coerce_flight_boundary(None, None, end_of_day=False) is None


class TestComputeMediaBuyStatusFromFlightDates:
    def test_active_when_past_end(self):
        """Past end stays active; wire refine to completed via resolve_canonical_status."""
        mb = MagicMock()
        mb.start_time = datetime.now(UTC) - timedelta(days=10)
        mb.end_time = datetime.now(UTC) - timedelta(days=1)
        mb.start_date = None
        mb.end_date = None
        assert compute_media_buy_status_from_flight_dates(mb) == "active"

    def test_pending_start_when_before_start(self):
        mb = MagicMock()
        mb.start_time = datetime.now(UTC) + timedelta(days=2)
        mb.end_time = datetime.now(UTC) + timedelta(days=10)
        mb.start_date = None
        mb.end_date = None
        assert compute_media_buy_status_from_flight_dates(mb) == "pending_start"

    def test_date_columns_via_coerce(self):
        mb = MagicMock()
        mb.start_time = None
        mb.end_time = None
        mb.start_date = (datetime.now(UTC) + timedelta(days=2)).date()
        mb.end_date = (datetime.now(UTC) + timedelta(days=10)).date()
        assert compute_media_buy_status_from_flight_dates(mb) == "pending_start"

    def test_active_when_in_window(self):
        mb = MagicMock()
        mb.start_time = datetime.now(UTC) - timedelta(days=1)
        mb.end_time = datetime.now(UTC) + timedelta(days=10)
        mb.start_date = None
        mb.end_date = None
        assert compute_media_buy_status_from_flight_dates(mb) == "active"

    def test_active_when_only_start_in_past(self):
        """Single-boundary fallback: past start_time alone → active."""
        mb = MagicMock()
        mb.start_time = datetime.now(UTC) - timedelta(days=1)
        mb.end_time = None
        mb.start_date = None
        mb.end_date = None
        assert compute_media_buy_status_from_flight_dates(mb) == "active"

    def test_pending_start_when_only_start_in_future(self):
        """Single-boundary: future start_time alone → pending_start."""
        mb = MagicMock()
        mb.start_time = datetime.now(UTC) + timedelta(days=2)
        mb.end_time = None
        mb.start_date = None
        mb.end_date = None
        assert compute_media_buy_status_from_flight_dates(mb) == "pending_start"
