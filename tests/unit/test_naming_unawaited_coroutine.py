"""Tests for naming.py async context handling.

Bug fix: salesagent-5shl
generate_auto_name() previously used asyncio.run() which fails inside a running
event loop. Now uses run_async_in_sync_context() which handles both sync and async
calling contexts correctly.

Note on AsyncMock: unittest.mock.patch auto-creates AsyncMock for async functions.
AsyncMock with side_effect=lambda that returns a coroutine does NOT await the inner
coroutine (lambda is not async def, so iscoroutinefunction is False). Use
return_value instead, which AsyncMock wraps in a proper awaitable coroutine.
"""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


class TestNamingAsyncContext:
    """Verify generate_auto_name works from async contexts after fix."""

    @pytest.mark.asyncio
    async def test_generate_auto_name_returns_ai_name_in_async_context(self):
        """generate_auto_name should return AI-generated name even from async context."""
        from src.core.utils.naming import generate_auto_name

        loop = asyncio.get_running_loop()
        assert loop is not None, "Test must run inside an async event loop"

        request = MagicMock()
        request.buyer_ref = "ACME-001"
        request.brand_manifest = MagicMock()
        request.brand_manifest.name = "Test Brand"
        request.brand_manifest.campaign_objectives = None
        request.get_total_budget.return_value = 5000.0
        request.packages = [MagicMock(currency="USD", product_id="prod_1")]

        start_time = datetime(2025, 6, 1)
        end_time = datetime(2025, 6, 30)

        mock_factory = MagicMock()
        mock_factory.is_ai_enabled.return_value = True
        mock_factory.create_model.return_value = "google-gla:gemini-2.0-flash"

        with (
            patch("src.services.ai.AIServiceFactory", return_value=mock_factory),
            patch(
                "src.services.ai.agents.naming_agent.create_naming_agent",
                return_value=MagicMock(),
            ),
            patch(
                "src.services.ai.agents.naming_agent.generate_name_async",
                return_value="AI Generated Campaign Name",
            ),
        ):
            result = generate_auto_name(
                request=request,
                packages=request.packages,
                start_time=start_time,
                end_time=end_time,
                tenant_ai_config={"provider": "gemini", "api_key": "fake-key"},
            )

            assert result == "AI Generated Campaign Name", (
                f"Expected AI-generated name but got '{result}'. "
                "run_async_in_sync_context should handle nested event loops."
            )

    def test_generate_auto_name_works_in_sync_context(self):
        """generate_auto_name should also work from pure sync context."""
        from src.core.utils.naming import generate_auto_name

        request = MagicMock()
        request.buyer_ref = "ACME-001"
        request.brand_manifest = MagicMock()
        request.brand_manifest.name = "Test Brand"
        request.brand_manifest.campaign_objectives = None
        request.get_total_budget.return_value = 5000.0
        request.packages = [MagicMock(currency="USD", product_id="prod_1")]

        start_time = datetime(2025, 6, 1)
        end_time = datetime(2025, 6, 30)

        mock_factory = MagicMock()
        mock_factory.is_ai_enabled.return_value = True
        mock_factory.create_model.return_value = "google-gla:gemini-2.0-flash"

        with (
            patch("src.services.ai.AIServiceFactory", return_value=mock_factory),
            patch(
                "src.services.ai.agents.naming_agent.create_naming_agent",
                return_value=MagicMock(),
            ),
            patch(
                "src.services.ai.agents.naming_agent.generate_name_async",
                return_value="Sync Context AI Name",
            ),
        ):
            result = generate_auto_name(
                request=request,
                packages=request.packages,
                start_time=start_time,
                end_time=end_time,
                tenant_ai_config={"provider": "gemini", "api_key": "fake-key"},
            )

            assert result == "Sync Context AI Name", f"Expected AI name from sync context but got '{result}'"
