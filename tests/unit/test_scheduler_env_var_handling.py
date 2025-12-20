"""Tests for scheduler environment variable handling.

These tests ensure that scheduler modules handle edge cases in environment
variable parsing, particularly empty strings which can cause startup crashes.
"""

import os
from unittest.mock import patch


class TestDeliveryWebhookSchedulerEnvVar:
    """Test DELIVERY_WEBHOOK_INTERVAL environment variable handling."""

    def test_default_value_when_env_not_set(self):
        """Test that default value (3600) is used when env var is not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove the env var if it exists
            os.environ.pop("DELIVERY_WEBHOOK_INTERVAL", None)

            # Re-import to get fresh module-level constant
            import importlib

            import src.services.delivery_webhook_scheduler as module

            importlib.reload(module)

            assert module.SLEEP_INTERVAL_SECONDS == 3600

    def test_default_value_when_env_is_empty_string(self):
        """Test that default value is used when env var is empty string.

        This is a regression test for a production crash where docker-compose
        set DELIVERY_WEBHOOK_INTERVAL="" which caused int('') to raise ValueError.
        """
        with patch.dict(os.environ, {"DELIVERY_WEBHOOK_INTERVAL": ""}, clear=False):
            import importlib

            import src.services.delivery_webhook_scheduler as module

            importlib.reload(module)

            # Should use default 3600, not crash with ValueError
            assert module.SLEEP_INTERVAL_SECONDS == 3600

    def test_custom_value_when_env_is_set(self):
        """Test that custom value is used when env var is set to valid integer."""
        with patch.dict(os.environ, {"DELIVERY_WEBHOOK_INTERVAL": "1800"}, clear=False):
            import importlib

            import src.services.delivery_webhook_scheduler as module

            importlib.reload(module)

            assert module.SLEEP_INTERVAL_SECONDS == 1800


class TestMediaBuyStatusSchedulerEnvVar:
    """Test MEDIA_BUY_STATUS_CHECK_INTERVAL environment variable handling."""

    def test_default_value_when_env_not_set(self):
        """Test that default value (60) is used when env var is not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("MEDIA_BUY_STATUS_CHECK_INTERVAL", None)

            import importlib

            import src.services.media_buy_status_scheduler as module

            importlib.reload(module)

            assert module.STATUS_CHECK_INTERVAL_SECONDS == 60

    def test_default_value_when_env_is_empty_string(self):
        """Test that default value is used when env var is empty string.

        This is a regression test - same pattern as DELIVERY_WEBHOOK_INTERVAL.
        """
        with patch.dict(os.environ, {"MEDIA_BUY_STATUS_CHECK_INTERVAL": ""}, clear=False):
            import importlib

            import src.services.media_buy_status_scheduler as module

            importlib.reload(module)

            # Should use default 60, not crash with ValueError
            assert module.STATUS_CHECK_INTERVAL_SECONDS == 60

    def test_custom_value_when_env_is_set(self):
        """Test that custom value is used when env var is set to valid integer."""
        with patch.dict(os.environ, {"MEDIA_BUY_STATUS_CHECK_INTERVAL": "120"}, clear=False):
            import importlib

            import src.services.media_buy_status_scheduler as module

            importlib.reload(module)

            assert module.STATUS_CHECK_INTERVAL_SECONDS == 120
