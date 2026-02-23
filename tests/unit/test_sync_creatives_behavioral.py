"""Behavioral snapshot tests for sync_creatives: HIGH_RISK gaps.

Locks current behavior for migration safety (Flask -> FastAPI).
Tests organized by BR-RULE invariant, covering:
- BR-RULE-040: Media buy status transitions (5 invariants, zero prior coverage)
- BR-RULE-033 inv2/inv3: Strict/lenient assignment modes
- BR-RULE-037 inv6: Slack notification guard
- BR-RULE-033 inv4 / BR-RULE-038 inv4: ToolError propagation in strict mode

Reference: salesagent-1xsp design field.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock, patch

import pytest
from adcp.types.generated_poc.enums.creative_action import CreativeAction
from fastmcp.exceptions import ToolError

from src.core.schemas import SyncCreativeResult
from src.core.tools.creatives._assignments import _process_assignments
from src.core.tools.creatives._workflow import _send_creative_notifications

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant():
    """Standard tenant config for assignment tests."""
    return {
        "tenant_id": "tenant_test",
        "approval_mode": "auto-approve",
        "slack_webhook_url": None,
    }


@pytest.fixture
def _make_db_package():
    """Factory for mock DB package + media buy objects."""

    def _factory(
        package_id="pkg_1",
        media_buy_id="mb_1",
        mb_status="draft",
        mb_approved_at=None,
        product_id=None,
    ):
        db_package = Mock()
        db_package.package_id = package_id
        db_package.media_buy_id = media_buy_id
        db_package.package_config = {"product_id": product_id} if product_id else {}

        db_media_buy = Mock()
        db_media_buy.media_buy_id = media_buy_id
        db_media_buy.status = mb_status
        db_media_buy.approved_at = mb_approved_at

        return db_package, db_media_buy

    return _factory


# ========================================================================
# BR-RULE-040: Media buy status transitions (Priority 1)
# ========================================================================


class TestMediaBuyStatusTransitions:
    """BR-RULE-040: Media buy status transitions triggered by assignments.

    When a creative is assigned to a package, the parent media buy's status
    may transition from 'draft' to 'pending_creatives' if approved_at is set.
    """

    def _run_assignments(self, assignments, results, tenant, db_package, db_media_buy, db_creative=None):
        """Helper to run _process_assignments with mocked DB lookups."""
        with patch("src.core.tools.creatives._assignments.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            # Mock execute for package+media_buy join query
            mock_row = Mock()
            mock_row.__iter__ = Mock(return_value=iter([db_package, db_media_buy]))
            mock_row.__getitem__ = lambda self, i: [db_package, db_media_buy][i]
            mock_session.execute.return_value.first.return_value = mock_row

            # Mock scalars for creative lookup and assignment lookup
            # First call: existing assignment check, Second call: creative lookup
            if db_creative:
                mock_session.scalars.return_value.first.side_effect = [db_creative, None]
            else:
                mock_session.scalars.return_value.first.return_value = None

            return _process_assignments(
                assignments=assignments,
                results=results,
                tenant=tenant,
                validation_mode="strict",
            )

    def test_draft_with_approved_at_transitions_to_pending_creatives(self, tenant, _make_db_package):
        """rule-040-inv1: draft + approved_at -> pending_creatives."""
        db_package, db_media_buy = _make_db_package(
            mb_status="draft",
            mb_approved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        results = [SyncCreativeResult(creative_id="c1", action="created")]

        self._run_assignments(
            assignments={"c1": ["pkg_1"]},
            results=results,
            tenant=tenant,
            db_package=db_package,
            db_media_buy=db_media_buy,
        )

        assert db_media_buy.status == "pending_creatives"

    def test_draft_without_approved_at_stays_draft(self, tenant, _make_db_package):
        """rule-040-inv2: draft without approved_at stays draft."""
        db_package, db_media_buy = _make_db_package(
            mb_status="draft",
            mb_approved_at=None,
        )
        results = [SyncCreativeResult(creative_id="c1", action="created")]

        self._run_assignments(
            assignments={"c1": ["pkg_1"]},
            results=results,
            tenant=tenant,
            db_package=db_package,
            db_media_buy=db_media_buy,
        )

        assert db_media_buy.status == "draft"

    def test_non_draft_status_unchanged(self, tenant, _make_db_package):
        """rule-040-inv3: non-draft status is not changed by assignments."""
        db_package, db_media_buy = _make_db_package(
            mb_status="active",
            mb_approved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        results = [SyncCreativeResult(creative_id="c1", action="created")]

        self._run_assignments(
            assignments={"c1": ["pkg_1"]},
            results=results,
            tenant=tenant,
            db_package=db_package,
            db_media_buy=db_media_buy,
        )

        assert db_media_buy.status == "active"

    def test_dedup_transition_multiple_creatives_same_buy(self, tenant, _make_db_package):
        """rule-040-inv4: multiple creatives in same media buy trigger transition once."""
        db_package, db_media_buy = _make_db_package(
            mb_status="draft",
            mb_approved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        results = [
            SyncCreativeResult(creative_id="c1", action="created"),
            SyncCreativeResult(creative_id="c2", action="created"),
        ]

        with patch("src.core.tools.creatives._assignments.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            # Both creatives assigned to same package -> same media buy
            # Use a factory so __iter__ produces a fresh iterator each call
            def make_mock_row():
                row = Mock()
                row.__iter__ = lambda s: iter([db_package, db_media_buy])
                row.__getitem__ = lambda s, i: [db_package, db_media_buy][i]
                return row

            mock_session.execute.return_value.first.side_effect = lambda: make_mock_row()

            # No existing assignment, no creative found for format check
            mock_session.scalars.return_value.first.return_value = None

            _process_assignments(
                assignments={"c1": ["pkg_1"], "c2": ["pkg_1"]},
                results=results,
                tenant=tenant,
                validation_mode="strict",
            )

        # Status should be pending_creatives (transitioned once, not twice)
        assert db_media_buy.status == "pending_creatives"

    def test_both_created_and_updated_trigger_check(self, tenant, _make_db_package):
        """rule-040-inv5: both action=created and action=updated creatives trigger status check."""
        db_package, db_media_buy = _make_db_package(
            mb_status="draft",
            mb_approved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        results = [
            SyncCreativeResult(creative_id="c1", action="created"),
            SyncCreativeResult(creative_id="c2", action="updated", changes=["name"]),
        ]

        with patch("src.core.tools.creatives._assignments.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            def make_mock_row():
                row = Mock()
                row.__iter__ = lambda s: iter([db_package, db_media_buy])
                row.__getitem__ = lambda s, i: [db_package, db_media_buy][i]
                return row

            mock_session.execute.return_value.first.side_effect = lambda: make_mock_row()

            mock_session.scalars.return_value.first.return_value = None

            _process_assignments(
                assignments={"c1": ["pkg_1"], "c2": ["pkg_1"]},
                results=results,
                tenant=tenant,
                validation_mode="strict",
            )

        assert db_media_buy.status == "pending_creatives"


# ========================================================================
# BR-RULE-033 inv2: Strict assignment abort (Priority 2)
# ========================================================================


class TestStrictAssignmentAbort:
    """BR-RULE-033 inv2: strict mode raises ToolError on invalid package."""

    def test_strict_mode_invalid_package_raises_tool_error(self, tenant):
        """rule-033-inv2: When validation_mode=strict and package not found, ToolError raised."""
        results = [SyncCreativeResult(creative_id="c1", action="created")]

        with patch("src.core.tools.creatives._assignments.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            # Package not found
            mock_session.execute.return_value.first.return_value = None

            with pytest.raises(ToolError, match="Package not found"):
                _process_assignments(
                    assignments={"c1": ["nonexistent_pkg"]},
                    results=results,
                    tenant=tenant,
                    validation_mode="strict",
                )


# ========================================================================
# BR-RULE-033 inv3 / BR-RULE-038 inv3: Lenient assignment skip (Priority 3)
# ========================================================================


class TestLenientAssignmentSkip:
    """BR-RULE-033 inv3 / BR-RULE-038 inv3: lenient mode skips invalid assignments."""

    def test_lenient_mode_invalid_package_skips_with_warning(self, tenant):
        """rule-033-inv3 / rule-038-inv3: lenient mode skips missing package, creative still succeeds."""
        results = [SyncCreativeResult(creative_id="c1", action="created")]

        with patch("src.core.tools.creatives._assignments.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            # Package not found
            mock_session.execute.return_value.first.return_value = None

            assignment_list = _process_assignments(
                assignments={"c1": ["nonexistent_pkg"]},
                results=results,
                tenant=tenant,
                validation_mode="lenient",
            )

        # No ToolError raised, no assignments created
        assert len(assignment_list) == 0

        # Error recorded in result's assignment_errors
        assert results[0].assignment_errors is not None
        assert "nonexistent_pkg" in results[0].assignment_errors
        assert "Package not found" in results[0].assignment_errors["nonexistent_pkg"]

        # Creative itself still has action=created (not failed)
        assert results[0].action == CreativeAction.created

    def test_lenient_mode_format_mismatch_skips(self, tenant, _make_db_package):
        """rule-039-inv6: lenient mode skips format-mismatched assignment with error."""
        db_package, db_media_buy = _make_db_package(product_id="product_1")

        # Mock creative with format that doesn't match product
        db_creative = Mock()
        db_creative.agent_url = "https://agent.example.com"
        db_creative.format = "video_format"

        # Mock product with different formats
        mock_product = Mock()
        mock_product.name = "Display Product"
        mock_product.format_ids = [
            {"agent_url": "https://agent.example.com", "id": "display_300x250"},
        ]

        results = [SyncCreativeResult(creative_id="c1", action="created")]

        with patch("src.core.tools.creatives._assignments.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            # Package found
            mock_row = Mock()
            mock_row.__iter__ = Mock(return_value=iter([db_package, db_media_buy]))
            mock_row.__getitem__ = lambda self, i: [db_package, db_media_buy][i]
            mock_session.execute.return_value.first.return_value = mock_row

            # First scalars call: creative lookup, second: assignment lookup
            mock_session.scalars.return_value.first.side_effect = [db_creative, mock_product]

            assignment_list = _process_assignments(
                assignments={"c1": ["pkg_1"]},
                results=results,
                tenant=tenant,
                validation_mode="lenient",
            )

        assert len(assignment_list) == 0
        assert results[0].assignment_errors is not None
        assert "pkg_1" in results[0].assignment_errors
        assert "is not supported by product" in results[0].assignment_errors["pkg_1"]

    def test_lenient_mode_mixed_valid_invalid_assignments(self, tenant, _make_db_package):
        """rule-033-inv3 mixed: valid + invalid assignments in lenient mode."""
        db_package_valid, db_media_buy = _make_db_package(
            package_id="pkg_valid",
            mb_status="draft",
            mb_approved_at=None,
        )

        results = [SyncCreativeResult(creative_id="c1", action="created")]

        with patch("src.core.tools.creatives._assignments.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            # First package: found. Second package: not found.
            mock_row_valid = Mock()
            mock_row_valid.__iter__ = Mock(return_value=iter([db_package_valid, db_media_buy]))
            mock_row_valid.__getitem__ = lambda self, i: [db_package_valid, db_media_buy][i]

            # Return valid result first, then None for missing package
            mock_session.execute.return_value.first.side_effect = [mock_row_valid, None]

            # No existing assignment, no creative for format check
            mock_session.scalars.return_value.first.return_value = None

            assignment_list = _process_assignments(
                assignments={"c1": ["pkg_valid", "pkg_invalid"]},
                results=results,
                tenant=tenant,
                validation_mode="lenient",
            )

        # Valid assignment created
        assert len(assignment_list) == 1
        assert assignment_list[0].package_id == "pkg_valid"

        # Invalid assignment recorded in errors
        assert results[0].assignment_errors is not None
        assert "pkg_invalid" in results[0].assignment_errors

        # Valid assignment recorded in assigned_to
        assert results[0].assigned_to == ["pkg_valid"]


# ========================================================================
# BR-RULE-033 inv4 / BR-RULE-038 inv4: ToolError propagation in strict mode
# ========================================================================


class TestStrictModeToolErrorPropagation:
    """BR-RULE-033 inv4 / BR-RULE-038 inv4: strict mode ToolError prevents result population.

    In strict mode, when a package is not found, the error IS recorded in the
    local assignment_errors_by_creative dict *before* ToolError is raised.
    However, the ToolError propagates out of _process_assignments before the
    post-processing loop (lines 226-236) that writes assignment_errors to
    SyncCreativeResult. The BDD claim 'errors always recorded in response'
    does NOT hold in strict mode.
    """

    def test_strict_mode_error_not_written_to_result_on_toolerror(self, tenant):
        """rule-033-inv4 / rule-038-inv4: ToolError prevents assignment_errors from reaching result."""
        results = [SyncCreativeResult(creative_id="c1", action="created")]

        with patch("src.core.tools.creatives._assignments.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            # Package not found -> triggers ToolError in strict mode
            mock_session.execute.return_value.first.return_value = None

            with pytest.raises(ToolError):
                _process_assignments(
                    assignments={"c1": ["bad_pkg"]},
                    results=results,
                    tenant=tenant,
                    validation_mode="strict",
                )

        # After ToolError, the post-processing loop never ran,
        # so assignment_errors is NOT populated on the result
        assert results[0].assignment_errors is None

    def test_strict_mode_format_mismatch_error_not_written_to_result(self, tenant, _make_db_package):
        """rule-038-inv4: format mismatch ToolError also prevents result population."""
        db_package, db_media_buy = _make_db_package(product_id="product_1")

        db_creative = Mock()
        db_creative.agent_url = "https://agent.example.com"
        db_creative.format = "video_format"

        mock_product = Mock()
        mock_product.name = "Display Product"
        mock_product.format_ids = [
            {"agent_url": "https://agent.example.com", "id": "display_300x250"},
        ]

        results = [SyncCreativeResult(creative_id="c1", action="created")]

        with patch("src.core.tools.creatives._assignments.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            mock_row = Mock()
            mock_row.__iter__ = Mock(return_value=iter([db_package, db_media_buy]))
            mock_row.__getitem__ = lambda self, i: [db_package, db_media_buy][i]
            mock_session.execute.return_value.first.return_value = mock_row

            # creative lookup, then product lookup
            mock_session.scalars.return_value.first.side_effect = [db_creative, mock_product]

            with pytest.raises(ToolError, match="is not supported by product"):
                _process_assignments(
                    assignments={"c1": ["pkg_1"]},
                    results=results,
                    tenant=tenant,
                    validation_mode="strict",
                )

        # ToolError prevented post-processing — assignment_errors not written to result
        assert results[0].assignment_errors is None


# ========================================================================
# BR-RULE-037 inv6: Slack notification guard (Priority 4)
# ========================================================================


class TestSlackNotificationGuard:
    """BR-RULE-037 inv6: Slack notification sent only when conditions met.

    Triple guard: creatives_needing_approval AND slack_webhook_url AND
    approval_mode == 'require-human'.
    """

    def test_slack_notification_only_when_webhook_configured(self):
        """rule-037-inv6: Slack notification sent when webhook present + require-human mode."""
        tenant = {
            "tenant_id": "tenant_test",
            "slack_webhook_url": "https://hooks.slack.com/services/T00/B00/xxx",
        }
        creatives_needing_approval = [
            {"creative_id": "c1", "format": "display_300x250", "name": "Test Ad", "status": "pending_review"},
        ]

        with patch("src.services.slack_notifier.get_slack_notifier") as mock_notifier_fn:
            mock_notifier = Mock()
            mock_notifier_fn.return_value = mock_notifier

            _send_creative_notifications(
                creatives_needing_approval=creatives_needing_approval,
                tenant=tenant,
                approval_mode="require-human",
                principal_id="principal_1",
            )

            # Notification was sent
            mock_notifier.notify_creative_pending.assert_called_once()
            call_kwargs = mock_notifier.notify_creative_pending.call_args
            assert call_kwargs[1]["creative_id"] == "c1" or call_kwargs.kwargs.get("creative_id") == "c1"

    def test_slack_notification_skipped_when_no_webhook(self):
        """rule-037-inv6: No Slack notification when webhook URL absent."""
        tenant = {
            "tenant_id": "tenant_test",
            "slack_webhook_url": None,
        }
        creatives_needing_approval = [
            {"creative_id": "c1", "format": "display_300x250", "name": "Test Ad", "status": "pending_review"},
        ]

        with patch("src.services.slack_notifier.get_slack_notifier") as mock_notifier_fn:
            _send_creative_notifications(
                creatives_needing_approval=creatives_needing_approval,
                tenant=tenant,
                approval_mode="require-human",
                principal_id="principal_1",
            )

            # Notifier never instantiated
            mock_notifier_fn.assert_not_called()

    def test_slack_notification_skipped_for_auto_approve(self):
        """rule-037-inv6: No Slack notification for auto-approve mode even with webhook."""
        tenant = {
            "tenant_id": "tenant_test",
            "slack_webhook_url": "https://hooks.slack.com/services/T00/B00/xxx",
        }
        creatives_needing_approval = [
            {"creative_id": "c1", "format": "display_300x250", "name": "Test Ad", "status": "approved"},
        ]

        with patch("src.services.slack_notifier.get_slack_notifier") as mock_notifier_fn:
            _send_creative_notifications(
                creatives_needing_approval=creatives_needing_approval,
                tenant=tenant,
                approval_mode="auto-approve",
                principal_id="principal_1",
            )

            # Notifier never instantiated
            mock_notifier_fn.assert_not_called()

    def test_slack_notification_skipped_for_ai_powered(self):
        """rule-037-inv6: No Slack notification for ai-powered mode (sent after AI review)."""
        tenant = {
            "tenant_id": "tenant_test",
            "slack_webhook_url": "https://hooks.slack.com/services/T00/B00/xxx",
        }
        creatives_needing_approval = [
            {"creative_id": "c1", "format": "display_300x250", "name": "Test Ad", "status": "pending_review"},
        ]

        with patch("src.services.slack_notifier.get_slack_notifier") as mock_notifier_fn:
            _send_creative_notifications(
                creatives_needing_approval=creatives_needing_approval,
                tenant=tenant,
                approval_mode="ai-powered",
                principal_id="principal_1",
            )

            # Notifier never instantiated (ai-powered sends notification after review)
            mock_notifier_fn.assert_not_called()

    def test_slack_notification_skipped_when_no_creatives(self):
        """rule-037-inv6: No Slack notification when no creatives need approval."""
        tenant = {
            "tenant_id": "tenant_test",
            "slack_webhook_url": "https://hooks.slack.com/services/T00/B00/xxx",
        }

        with patch("src.services.slack_notifier.get_slack_notifier") as mock_notifier_fn:
            _send_creative_notifications(
                creatives_needing_approval=[],
                tenant=tenant,
                approval_mode="require-human",
                principal_id="principal_1",
            )

            mock_notifier_fn.assert_not_called()
