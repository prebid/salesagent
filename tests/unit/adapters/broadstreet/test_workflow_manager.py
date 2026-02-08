"""Unit tests for Broadstreet Workflow Manager."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.broadstreet.managers.workflow import BroadstreetWorkflowManager


class TestBroadstreetWorkflowManager:
    """Tests for BroadstreetWorkflowManager."""

    @pytest.fixture
    def manager(self):
        """Create a workflow manager with mocked dependencies."""
        principal = MagicMock()
        principal.principal_id = "principal_123"
        audit_logger = MagicMock()
        log_func = MagicMock()

        return BroadstreetWorkflowManager(
            tenant_id="test_tenant",
            principal=principal,
            audit_logger=audit_logger,
            log_func=log_func,
        )

    @pytest.fixture
    def sample_packages(self):
        """Create sample packages for testing."""
        pkg1 = MagicMock()
        pkg1.name = "Banner Package"
        pkg1.impressions = 100000
        pkg1.cpm = 5.0
        pkg1.targeting_overlay = None

        pkg2 = MagicMock()
        pkg2.name = "Sidebar Package"
        pkg2.impressions = 50000
        pkg2.cpm = 3.0
        pkg2.targeting_overlay = None

        return [pkg1, pkg2]

    def test_platform_attributes(self, manager):
        """Test platform-specific attributes."""
        assert manager.platform_name == "Broadstreet"
        assert manager.platform_url_base == "https://broadstreetads.com"

    @patch("src.adapters.base_workflow.get_db_session")
    @patch("src.adapters.base_workflow.get_tenant_config")
    def test_create_activation_workflow_step(self, mock_get_config, mock_db_session, manager, sample_packages):
        """Test creating activation workflow step."""
        mock_get_config.return_value = {}
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session

        step_id = manager.create_activation_workflow_step(
            media_buy_id="bs_12345",
            packages=sample_packages,
        )

        assert step_id is not None
        assert step_id.startswith("a")  # 'a' prefix for activation
        assert len(step_id) == 6

        # Verify database objects were created
        assert mock_session.add.call_count == 3  # Context, WorkflowStep, ObjectWorkflowMapping
        assert mock_session.commit.called

    @patch("src.adapters.base_workflow.get_db_session")
    @patch("src.adapters.base_workflow.get_tenant_config")
    def test_create_manual_campaign_workflow_step(self, mock_get_config, mock_db_session, manager, sample_packages):
        """Test creating manual campaign creation workflow step."""
        mock_get_config.return_value = {}
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session

        request = MagicMock()
        request.brand_manifest.brand_name = "Test Brand"
        request.po_number = "PO-12345"
        request.get_total_budget.return_value = 5000.0

        step_id = manager.create_manual_campaign_workflow_step(
            request=request,
            packages=sample_packages,
            start_time=datetime(2024, 1, 1),
            end_time=datetime(2024, 1, 31),
            media_buy_id="bs_pending_12345",
        )

        assert step_id is not None
        assert step_id.startswith("c")  # 'c' prefix for creation
        assert len(step_id) == 6

        # Verify database objects were created
        assert mock_session.add.call_count == 3
        assert mock_session.commit.called

    @patch("src.adapters.base_workflow.get_db_session")
    @patch("src.adapters.base_workflow.get_tenant_config")
    def test_create_creative_approval_workflow_step(self, mock_get_config, mock_db_session, manager):
        """Test creating creative approval workflow step."""
        mock_get_config.return_value = {}
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session

        step_id = manager.create_creative_approval_workflow_step(
            media_buy_id="bs_12345",
            creative_ids=["creative_1", "creative_2"],
        )

        assert step_id is not None
        assert step_id.startswith("p")  # 'p' prefix for approval
        assert len(step_id) == 6

        # Verify database objects were created
        assert mock_session.add.call_count == 3
        assert mock_session.commit.called

    @patch("src.adapters.base_workflow.get_db_session")
    @patch("src.adapters.base_workflow.get_tenant_config")
    @patch("requests.post")
    def test_slack_notification_sent(self, mock_post, mock_get_config, mock_db_session, manager, sample_packages):
        """Test that Slack notification is sent when configured."""
        mock_get_config.return_value = {"slack": {"webhook_url": "https://hooks.slack.com/test"}}
        mock_post.return_value.status_code = 200
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session

        manager.create_activation_workflow_step(
            media_buy_id="bs_12345",
            packages=sample_packages,
        )

        # Verify Slack was called
        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        assert "json" in call_kwargs
        assert "attachments" in call_kwargs["json"]

    @patch("src.adapters.base_workflow.get_db_session")
    @patch("src.adapters.base_workflow.get_tenant_config")
    def test_slack_notification_skipped_when_not_configured(
        self, mock_get_config, mock_db_session, manager, sample_packages
    ):
        """Test that Slack notification is skipped when not configured."""
        mock_get_config.return_value = {}  # No Slack config
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session

        # Should not raise
        step_id = manager.create_activation_workflow_step(
            media_buy_id="bs_12345",
            packages=sample_packages,
        )

        assert step_id is not None

    def test_get_notification_details_create_campaign(self, manager):
        """Test notification details for campaign creation."""
        details = manager._get_notification_details(
            "c12345",
            {
                "action_type": "create_broadstreet_campaign",
            },
        )

        assert "Broadstreet" in details["title"]
        assert "Manual" in details["title"]
        assert details["color"] == "#FF9500"

    def test_get_notification_details_activate_campaign(self, manager):
        """Test notification details for campaign activation."""
        details = manager._get_notification_details(
            "a12345",
            {
                "action_type": "activate_broadstreet_campaign",
            },
        )

        assert "Broadstreet" in details["title"]
        assert "Activation" in details["title"]
        assert details["color"] == "#FFD700"

    def test_get_notification_details_creative_approval(self, manager):
        """Test notification details for creative approval."""
        details = manager._get_notification_details(
            "p12345",
            {
                "action_type": "creative_approval",
            },
        )

        assert "Broadstreet" in details["title"]
        assert "Creative" in details["title"]
        assert details["color"] == "#9B59B6"

    def test_get_notification_details_unknown_type(self, manager):
        """Test notification details for unknown action type falls back to base."""
        details = manager._get_notification_details(
            "w12345",
            {
                "action_type": "unknown_action",
            },
        )

        # Should fall back to base class default
        assert "title" in details
        assert "description" in details
        assert "color" in details

    @patch("src.adapters.base_workflow.get_db_session")
    def test_workflow_step_returns_none_on_db_error(self, mock_db_session, manager, sample_packages):
        """Test that workflow step creation returns None on database error."""
        mock_db_session.return_value.__enter__.side_effect = Exception("Database error")

        step_id = manager.create_activation_workflow_step(
            media_buy_id="bs_12345",
            packages=sample_packages,
        )

        assert step_id is None
        assert manager.log.called  # Error was logged


class TestBaseWorkflowManagerIntegration:
    """Tests for BaseWorkflowManager methods used by Broadstreet."""

    @pytest.fixture
    def manager(self):
        """Create a workflow manager."""
        principal = MagicMock()
        principal.principal_id = "principal_123"

        return BroadstreetWorkflowManager(
            tenant_id="test_tenant",
            principal=principal,
        )

    def test_generate_step_id(self, manager):
        """Test step ID generation."""
        step_id = manager._generate_step_id("a")

        assert step_id.startswith("a")
        assert len(step_id) == 6

    def test_generate_step_id_unique(self, manager):
        """Test that generated step IDs are unique."""
        ids = [manager._generate_step_id("a") for _ in range(100)]
        assert len(set(ids)) == 100  # All unique

    def test_build_packages_summary(self, manager):
        """Test building packages summary."""
        pkg = MagicMock()
        pkg.name = "Test Package"
        pkg.impressions = 100000
        pkg.cpm = 5.0

        summary = manager.build_packages_summary([pkg])

        assert len(summary) == 1
        assert summary[0]["name"] == "Test Package"
        assert summary[0]["impressions"] == 100000
        assert summary[0]["cpm"] == 5.0
