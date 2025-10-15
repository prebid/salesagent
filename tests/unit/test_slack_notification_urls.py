"""
Tests for Slack notification URL generation.

Ensures all Slack notifications use proper tenant-specific URLs instead of hardcoded localhost.
"""

import os
from unittest.mock import patch

import pytest

from src.services.slack_notifier import SlackNotifier


class TestSlackNotificationUrls:
    """Test URL generation in Slack notifications."""

    @pytest.fixture
    def slack_notifier(self):
        """Create SlackNotifier instance for testing."""
        tenant_config = {"features": {"slack_webhook_url": "https://hooks.slack.com/services/test"}}
        return SlackNotifier(tenant_config=tenant_config)

    @pytest.fixture
    def mock_webhook_delivery(self):
        """Mock webhook delivery to capture payloads without actually sending."""
        with patch("src.core.webhook_delivery.deliver_webhook_with_retry") as mock:
            mock.return_value = (True, {"attempts": 1})
            yield mock

    def test_notify_new_task_with_tenant_id(self, slack_notifier, mock_webhook_delivery):
        """Test that notify_new_task uses tenant-specific URL when tenant_id provided."""
        with patch.dict(os.environ, {"ADMIN_UI_URL": "https://sales-agent.scope3.com"}):
            slack_notifier.notify_new_task(
                task_id="task_123",
                task_type="create_media_buy",
                principal_name="Test Advertiser",
                tenant_name="Test Publisher",
                tenant_id="tenant_abc",
            )

        # Get the payload that was sent
        call_args = mock_webhook_delivery.call_args
        delivery = call_args[0][0]
        payload = delivery.payload

        # Find the actions block with the URL
        actions_block = next((b for b in payload["blocks"] if b["type"] == "actions"), None)
        assert actions_block is not None, "Should have actions block"

        url = actions_block["elements"][0]["url"]
        assert url == "https://sales-agent.scope3.com/tenant/tenant_abc/workflows"
        assert "localhost" not in url, "Should not contain localhost"

    def test_notify_new_task_without_tenant_id(self, slack_notifier, mock_webhook_delivery):
        """Test that notify_new_task falls back to global workflows page without tenant_id."""
        with patch.dict(os.environ, {"ADMIN_UI_URL": "https://sales-agent.scope3.com"}):
            slack_notifier.notify_new_task(
                task_id="task_123",
                task_type="create_media_buy",
                principal_name="Test Advertiser",
                tenant_name="Test Publisher",
                tenant_id=None,  # No tenant ID
            )

        call_args = mock_webhook_delivery.call_args
        delivery = call_args[0][0]
        payload = delivery.payload

        actions_block = next((b for b in payload["blocks"] if b["type"] == "actions"), None)
        url = actions_block["elements"][0]["url"]

        # Should fall back to global workflows
        assert url == "https://sales-agent.scope3.com/workflows"
        assert "localhost" not in url

    def test_notify_media_buy_event_with_tenant_and_buy_id(self, slack_notifier, mock_webhook_delivery):
        """Test media buy event notification links to specific media buy."""
        with patch.dict(os.environ, {"ADMIN_UI_URL": "https://sales-agent.scope3.com"}):
            slack_notifier.notify_media_buy_event(
                event_type="created",
                media_buy_id="mb_123",
                principal_name="Test Advertiser",
                details={"total_budget": 5000.0},
                tenant_name="Test Publisher",
                tenant_id="tenant_abc",
            )

        call_args = mock_webhook_delivery.call_args
        delivery = call_args[0][0]
        payload = delivery.payload

        # Could be in blocks or attachments depending on event type
        if "blocks" in payload:
            actions_block = next((b for b in payload["blocks"] if b["type"] == "actions"), None)
        else:
            actions_block = next((b for b in payload["attachments"][0]["blocks"] if b["type"] == "actions"), None)

        url = actions_block["elements"][0]["url"]
        assert url == "https://sales-agent.scope3.com/tenant/tenant_abc/workflows#mb_123"
        assert "localhost" not in url

    def test_notify_media_buy_event_with_tenant_only(self, slack_notifier, mock_webhook_delivery):
        """Test media buy event without media_buy_id links to tenant workflows."""
        with patch.dict(os.environ, {"ADMIN_UI_URL": "https://sales-agent.scope3.com"}):
            slack_notifier.notify_media_buy_event(
                event_type="failed",
                media_buy_id=None,  # No media buy ID yet
                principal_name="Test Advertiser",
                details={},
                tenant_name="Test Publisher",
                tenant_id="tenant_abc",
                success=False,
            )

        call_args = mock_webhook_delivery.call_args
        delivery = call_args[0][0]
        payload = delivery.payload

        # Failed events use attachments
        actions_block = next((b for b in payload["attachments"][0]["blocks"] if b["type"] == "actions"), None)
        url = actions_block["elements"][0]["url"]

        assert url == "https://sales-agent.scope3.com/tenant/tenant_abc/workflows"
        assert "localhost" not in url

    def test_notify_media_buy_event_without_tenant_id(self, slack_notifier, mock_webhook_delivery):
        """Test media buy event falls back to global workflows without tenant_id."""
        with patch.dict(os.environ, {"ADMIN_UI_URL": "https://sales-agent.scope3.com"}):
            slack_notifier.notify_media_buy_event(
                event_type="created",
                media_buy_id="mb_123",
                principal_name="Test Advertiser",
                details={},
                tenant_name="Test Publisher",
                tenant_id=None,  # No tenant ID
            )

        call_args = mock_webhook_delivery.call_args
        delivery = call_args[0][0]
        payload = delivery.payload

        actions_block = next((b for b in payload["blocks"] if b["type"] == "actions"), None)
        url = actions_block["elements"][0]["url"]

        # Should fall back to global workflows
        assert url == "https://sales-agent.scope3.com/workflows"
        assert "localhost" not in url

    def test_notify_creative_pending_with_tenant_id(self, slack_notifier, mock_webhook_delivery):
        """Test creative notification uses tenant-specific URL."""
        with patch.dict(os.environ, {"ADMIN_UI_URL": "https://sales-agent.scope3.com"}):
            slack_notifier.notify_creative_pending(
                creative_id="creative_123",
                principal_name="Test Advertiser",
                format_type="display_300x250",
                tenant_id="tenant_abc",
            )

        call_args = mock_webhook_delivery.call_args
        delivery = call_args[0][0]
        payload = delivery.payload

        actions_block = next((b for b in payload["blocks"] if b["type"] == "actions"), None)
        url = actions_block["elements"][0]["url"]

        assert url == "https://sales-agent.scope3.com/tenant/tenant_abc/creatives/review#creative_123"
        assert "localhost" not in url

    def test_localhost_fallback_when_env_not_set(self, slack_notifier, mock_webhook_delivery):
        """Test that localhost is used as fallback when ADMIN_UI_URL not set (dev mode)."""
        # Don't set ADMIN_UI_URL
        with patch.dict(os.environ, {}, clear=True):
            slack_notifier.notify_new_task(
                task_id="task_123",
                task_type="create_media_buy",
                principal_name="Test Advertiser",
                tenant_id="tenant_abc",
            )

        call_args = mock_webhook_delivery.call_args
        delivery = call_args[0][0]
        payload = delivery.payload

        actions_block = next((b for b in payload["blocks"] if b["type"] == "actions"), None)
        url = actions_block["elements"][0]["url"]

        # Should use localhost fallback in dev mode
        assert url == "http://localhost:8001/tenant/tenant_abc/workflows"

    def test_all_event_types_use_tenant_urls(self, slack_notifier, mock_webhook_delivery):
        """Test that all event types properly handle tenant-specific URLs."""
        event_types = ["created", "approval_required", "config_approval_required", "failed", "activated"]

        with patch.dict(os.environ, {"ADMIN_UI_URL": "https://sales-agent.scope3.com"}):
            for event_type in event_types:
                mock_webhook_delivery.reset_mock()

                slack_notifier.notify_media_buy_event(
                    event_type=event_type,
                    media_buy_id="mb_123",
                    principal_name="Test Advertiser",
                    details={},
                    tenant_name="Test Publisher",
                    tenant_id="tenant_abc",
                )

                # Verify no localhost in any event type
                call_args = mock_webhook_delivery.call_args
                delivery = call_args[0][0]
                payload = delivery.payload

                # Check both possible locations for actions block
                actions_block = None
                if "blocks" in payload:
                    actions_block = next((b for b in payload["blocks"] if b["type"] == "actions"), None)
                elif "attachments" in payload:
                    actions_block = next(
                        (b for b in payload["attachments"][0]["blocks"] if b["type"] == "actions"), None
                    )

                assert actions_block is not None, f"Event type {event_type} should have actions block"
                url = actions_block["elements"][0]["url"]
                assert "localhost" not in url, f"Event type {event_type} should not use localhost"
                assert "tenant_abc" in url, f"Event type {event_type} should include tenant ID"
