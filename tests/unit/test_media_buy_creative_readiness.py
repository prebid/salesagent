"""Unit tests for shared creative finalize-readiness predicate (#1696)."""

from unittest.mock import MagicMock, patch

from src.admin.services.media_buy_creative_readiness import (
    FINALIZE_READY_CREATIVE_STATUSES,
    CreativeFinalizeReadiness,
    evaluate_creative_finalize_readiness,
    should_hold_media_buy_for_creatives,
)


def _assignment(creative_id: str) -> MagicMock:
    a = MagicMock()
    a.creative_id = creative_id
    return a


def _creative(creative_id: str, status: str) -> MagicMock:
    c = MagicMock()
    c.creative_id = creative_id
    c.status = status
    return c


def _session_returning(assignments: list, creatives: list | None = None) -> MagicMock:
    """Mock session.scalars().all() for assignment then creative queries."""
    session = MagicMock()
    call_results = [assignments]
    if creatives is not None:
        call_results.append(creatives)

    results_iter = iter(call_results)

    def _scalars(_stmt):
        mock_result = MagicMock()
        mock_result.all.return_value = next(results_iter)
        return mock_result

    session.scalars.side_effect = _scalars
    return session


class TestEvaluateCreativeFinalizeReadiness:
    def test_zero_assignments_not_ready_no_assignments(self):
        session = _session_returning([])
        result = evaluate_creative_finalize_readiness(session, tenant_id="t1", media_buy_id="mb_1")
        assert result.ready is False
        assert result.assignment_count == 0
        assert result.unapproved_creative_ids == []
        assert result.hold_reason == "no_assignments"
        hold_session = _session_returning([])
        assert should_hold_media_buy_for_creatives(hold_session, tenant_id="t1", media_buy_id="mb_1") is True

    def test_all_approved_ready(self):
        session = _session_returning(
            [_assignment("c1"), _assignment("c2")],
            [_creative("c1", "approved"), _creative("c2", "approved")],
        )
        result = evaluate_creative_finalize_readiness(session, tenant_id="t1", media_buy_id="mb_1")
        assert result.ready is True
        assert result.assignment_count == 2
        assert result.unapproved_creative_ids == []
        assert result.hold_reason is None
        ready_session = _session_returning(
            [_assignment("c1"), _assignment("c2")],
            [_creative("c1", "approved"), _creative("c2", "approved")],
        )
        assert should_hold_media_buy_for_creatives(ready_session, tenant_id="t1", media_buy_id="mb_1") is False

    def test_active_status_counts_as_ready(self):
        """Legacy ``active`` remains in the shared allowlist."""
        assert "active" in FINALIZE_READY_CREATIVE_STATUSES
        session = _session_returning(
            [_assignment("c1")],
            [_creative("c1", "active")],
        )
        result = evaluate_creative_finalize_readiness(session, tenant_id="t1", media_buy_id="mb_1")
        assert result.ready is True
        assert result.hold_reason is None

    def test_pending_creative_not_ready(self):
        session = _session_returning(
            [_assignment("c1"), _assignment("c2")],
            [_creative("c1", "approved"), _creative("c2", "pending_review")],
        )
        result = evaluate_creative_finalize_readiness(session, tenant_id="t1", media_buy_id="mb_1")
        assert result.ready is False
        assert result.assignment_count == 2
        assert result.unapproved_creative_ids == ["c2"]
        assert result.hold_reason == "unapproved_creatives"

    def test_rejected_creative_not_ready(self):
        session = _session_returning(
            [_assignment("c1")],
            [_creative("c1", "rejected")],
        )
        result = evaluate_creative_finalize_readiness(session, tenant_id="t1", media_buy_id="mb_1")
        assert result.ready is False
        assert result.unapproved_creative_ids == ["c1"]
        assert result.hold_reason == "unapproved_creatives"

    def test_tenant_scoped_assignment_query(self):
        session = _session_returning([])
        evaluate_creative_finalize_readiness(session, tenant_id="tenant_a", media_buy_id="mb_x")
        stmt = session.scalars.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "tenant_a" in compiled
        assert "mb_x" in compiled


def _unwrap(fn):
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class TestApproveRoutesHoldBehavior:
    """Hold arm: pending_creatives + execute_approved_media_buy must not run."""

    def test_approve_workflow_step_zero_assignments_holds_without_execute(self):
        from src.admin.app import create_app
        from src.admin.blueprints import workflows

        app = create_app()
        media_buy = MagicMock()
        media_buy.status = "pending_approval"

        step = MagicMock()
        mapping = MagicMock()
        mapping.object_type = "media_buy"
        mapping.object_id = "mb_hold"

        db = MagicMock()
        db_cm = MagicMock()
        db_cm.__enter__ = MagicMock(return_value=db)
        db_cm.__exit__ = MagicMock(return_value=False)

        hold = CreativeFinalizeReadiness(
            ready=False,
            assignment_count=0,
            unapproved_creative_ids=[],
            hold_reason="no_assignments",
        )

        approve = _unwrap(workflows.approve_workflow_step)
        with (
            app.test_request_context(
                "/tenant/t1/workflows/wf1/steps/s1/approve",
                method="POST",
            ),
            patch("src.admin.blueprints.workflows.get_db_session", return_value=db_cm),
            patch("src.admin.blueprints.workflows.WorkflowRepository") as mock_wf_repo_cls,
            patch("src.admin.blueprints.workflows.MediaBuyRepository") as mock_mb_repo_cls,
            patch(
                "src.admin.services.media_buy_creative_readiness.evaluate_creative_finalize_readiness",
                return_value=hold,
            ) as mock_eval,
            patch(
                "src.core.tools.media_buy_create.execute_approved_media_buy",
            ) as mock_execute,
            patch("src.admin.blueprints.workflows.flash"),
            patch("src.admin.blueprints.workflows.session", {"user": {"email": "op@example.com"}}),
        ):
            wf_repo = mock_wf_repo_cls.return_value
            wf_repo.update_status.return_value = step
            wf_repo.get_mappings_for_step.return_value = [mapping]
            mock_mb_repo_cls.return_value.get_by_id.return_value = media_buy

            response, status = approve("t1", "wf1", "s1")

        assert status == 200
        assert response.get_json()["success"] is True
        assert media_buy.status == "pending_creatives"
        mock_eval.assert_called_once_with(db, tenant_id="t1", media_buy_id="mb_hold")
        mock_execute.assert_not_called()
        assert db.commit.called

    def test_approve_media_buy_zero_assignments_holds_without_execute(self):
        from src.admin.app import create_app
        from src.admin.blueprints import operations

        app = create_app()
        media_buy = MagicMock()
        media_buy.status = "pending_approval"
        media_buy.start_time = None
        media_buy.end_time = None
        media_buy.principal_id = "p1"

        step = MagicMock()
        step.step_id = "step_1"
        step.context_id = "ctx_1"
        step.tool_name = "create_media_buy"
        step.request_data = {}
        step.comments = []

        db = MagicMock()
        db_cm = MagicMock()
        db_cm.__enter__ = MagicMock(return_value=db)
        db_cm.__exit__ = MagicMock(return_value=False)
        db.scalars.return_value.first.return_value = step

        hold = CreativeFinalizeReadiness(
            ready=False,
            assignment_count=0,
            unapproved_creative_ids=[],
            hold_reason="no_assignments",
        )

        approve = _unwrap(operations.approve_media_buy)
        with (
            app.test_request_context(
                "/tenant/t1/media-buy/mb_hold/approve",
                method="POST",
                data={"action": "approve"},
            ),
            patch("src.core.database.database_session.get_db_session", return_value=db_cm),
            patch("src.admin.blueprints.operations.MediaBuyRepository") as mock_mb_repo_cls,
            patch(
                "src.admin.services.media_buy_creative_readiness.evaluate_creative_finalize_readiness",
                return_value=hold,
            ) as mock_eval,
            patch(
                "src.core.tools.media_buy_create.execute_approved_media_buy",
            ) as mock_execute,
            patch("flask.flash"),
            patch("flask.redirect", return_value="redirected") as mock_redirect,
            patch("flask.url_for", return_value="/detail"),
            patch("flask.session", {"user": {"email": "op@example.com"}}),
        ):
            mock_mb_repo_cls.return_value.get_by_id.return_value = media_buy
            result = approve("t1", "mb_hold")

        assert result == "redirected"
        assert media_buy.status == "pending_creatives"
        mock_eval.assert_called_once_with(db, tenant_id="t1", media_buy_id="mb_hold")
        mock_execute.assert_not_called()
        assert db.commit.called
        mock_redirect.assert_called_once_with("/detail")
