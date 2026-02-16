"""Test sync_creatives asyncio and variable scoping fixes.

This test file ensures the fixes for:
1. asyncio.run() in running event loop error
2. creative_id variable scoping error

Both issues occurred in production when sync_creatives was called via MCP.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from adcp.types.generated_poc.core.format_id import FormatId

from src.core.tools.creatives import _sync_creatives_impl
from src.core.validation_helpers import run_async_in_sync_context


class TestRunAsyncInSyncContext:
    """Test the run_async_in_sync_context helper function."""

    async def sample_async_function(self):
        """Sample async function for testing."""
        await asyncio.sleep(0.001)
        return "async_result"

    def test_run_async_outside_event_loop(self):
        """Test running async function when no event loop exists (sync context)."""
        result = run_async_in_sync_context(self.sample_async_function())
        assert result == "async_result"

    @pytest.mark.asyncio
    async def test_run_async_inside_event_loop(self):
        """Test running async function when already inside event loop (FastMCP context).

        This is the scenario that was failing before the fix:
        - FastMCP runs tools in an async context
        - sync_creatives was calling asyncio.run() directly
        - This caused: "asyncio.run() cannot be called from a running event loop"
        """
        result = run_async_in_sync_context(self.sample_async_function())
        assert result == "async_result"

    @pytest.mark.asyncio
    async def test_multiple_sequential_calls(self):
        """Test that multiple sequential calls work correctly."""
        result1 = run_async_in_sync_context(self.sample_async_function())
        result2 = run_async_in_sync_context(self.sample_async_function())
        result3 = run_async_in_sync_context(self.sample_async_function())

        assert result1 == "async_result"
        assert result2 == "async_result"
        assert result3 == "async_result"


class TestSyncCreativesErrorHandling:
    """Test sync_creatives error handling paths that use creative_id."""

    @pytest.mark.asyncio
    async def test_creative_id_defined_in_error_path(self):
        """Test that creative_id is available when validation fails (new creative path).

        Before the fix, this would raise:
        "cannot access local variable 'creative_id' where it is not associated with a value"

        This tests the new creative creation path where validation fails.
        """
        # Mock the database session and context
        mock_session = MagicMock()
        mock_context = MagicMock()
        mock_context.request_context = MagicMock()
        mock_context.request_context.meta = {"principal_id": "test_principal"}

        # Mock tenant context
        mock_tenant = {
            "tenant_id": "test_tenant",
            "approval_mode": "auto-approve",
        }

        # Create a creative that will fail validation (missing required fields)
        invalid_creative = {
            "creative_id": "test_creative_123",
            # Missing required fields like name, format_id
        }

        with patch("src.core.tools.creatives._sync.get_db_session") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_session
            mock_get_db.return_value.__exit__.return_value = None

            with patch("src.core.tools.creatives._sync.get_current_tenant", return_value=mock_tenant):
                with patch(
                    "src.core.helpers.context_helpers.get_principal_from_context",
                    return_value=("test_principal", mock_tenant),
                ):
                    # Mock the Creative schema to raise ValidationError
                    with patch("src.core.schemas.Creative") as mock_creative_class:
                        from pydantic import ValidationError

                        # Simulate validation error
                        mock_creative_class.side_effect = ValidationError.from_exception_data(
                            "Creative", [{"type": "missing", "loc": ("name",), "msg": "Field required"}]
                        )

                        # This should NOT raise "cannot access local variable 'creative_id'"
                        # Instead, it should handle the error gracefully
                        result = _sync_creatives_impl(
                            creatives=[invalid_creative],
                            context=None,
                            ctx=mock_context,
                        )

                        # Verify the error was captured with the correct creative_id
                        assert len(result.creatives) == 1
                        assert result.creatives[0].creative_id == "test_creative_123"
                        assert result.creatives[0].action == "failed"
                        assert len(result.creatives[0].errors) > 0

    @pytest.mark.asyncio
    async def test_creative_id_in_preview_failure_path(self):
        """Test that creative_id is available when creative agent preview fails.

        NOTE: This test was updated after fixing data preservation bugs.
        Creatives with valid media URLs in assets should SUCCEED even if preview fails,
        because preview is optional for static creatives with direct URLs.

        To test actual failure path, use creative WITHOUT any URL (no assets, no url field).
        """
        mock_session = MagicMock()
        mock_context = MagicMock()
        mock_context.request_context = MagicMock()
        mock_context.request_context.meta = {"principal_id": "test_principal"}

        mock_tenant = {
            "tenant_id": "test_tenant",
            "approval_mode": "auto-approve",
        }

        # Creative WITHOUT URL - this should fail when preview returns no previews
        creative = {
            "creative_id": "test_creative_456",
            "name": "Test Creative",
            "format_id": {"agent_url": "https://example.com", "id": "display_300x250"},
            # NO assets, NO url - preview is required
        }

        with patch("src.core.tools.creatives._sync.get_db_session") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_session
            mock_get_db.return_value.__exit__.return_value = None

            with patch("src.core.tools.creatives._sync.get_current_tenant", return_value=mock_tenant):
                with patch(
                    "src.core.helpers.context_helpers.get_principal_from_context",
                    return_value=("test_principal", mock_tenant),
                ):
                    # Mock the creative agent registry to return no previews
                    with patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry:
                        mock_reg_instance = MagicMock()
                        mock_registry.return_value = mock_reg_instance

                        _fmt_id = FormatId(agent_url="https://example.com", id="display_300x250")

                        # Mock get_format to return a valid format spec
                        async def mock_get_format(*args, **kwargs):
                            mock_format = MagicMock()
                            mock_format.format_id = _fmt_id
                            mock_format.agent_url = "https://example.com"
                            mock_format.output_format_ids = None  # Not generative
                            return mock_format

                        mock_reg_instance.get_format = mock_get_format

                        # Mock list_all_formats to return a matching format
                        async def mock_list_formats(*args, **kwargs):
                            mock_format = MagicMock()
                            mock_format.format_id = _fmt_id
                            mock_format.agent_url = "https://example.com"
                            mock_format.output_format_ids = None  # Not generative
                            return [mock_format]

                        mock_reg_instance.list_all_formats = mock_list_formats

                        # Mock preview_creative to return empty previews (failure case)
                        async def mock_preview(*args, **kwargs):
                            return {"previews": []}  # No previews = validation failure

                        mock_reg_instance.preview_creative = mock_preview

                        # Mock session.scalars().first() to return None (new creative)
                        mock_session.scalars.return_value.first.return_value = None

                        # This should handle the error gracefully with creative_id available
                        result = _sync_creatives_impl(
                            creatives=[creative],
                            context=None,
                            ctx=mock_context,
                        )

                        # Verify error was captured with correct creative_id
                        assert len(result.creatives) == 1
                        assert result.creatives[0].creative_id == "test_creative_456"
                        assert result.creatives[0].action == "failed"
                        assert any("preview" in err.lower() for err in result.creatives[0].errors)


class TestSyncCreativesAsyncScenario:
    """Integration test for sync_creatives in async context (simulates MCP call)."""

    @pytest.mark.asyncio
    async def test_sync_creatives_called_from_async_context(self):
        """Test that sync_creatives works when called from async context.

        This simulates the real-world scenario:
        - MCP tool is called (async context)
        - sync_creatives implementation is sync but calls async registry methods
        - Should NOT raise "asyncio.run() cannot be called from a running event loop"
        """
        mock_session = MagicMock()
        mock_context = MagicMock()
        mock_context.request_context = MagicMock()
        mock_context.request_context.meta = {"principal_id": "test_principal"}

        mock_tenant = {
            "tenant_id": "test_tenant",
            "approval_mode": "auto-approve",
        }

        creative = {
            "creative_id": "test_creative_789",
            "name": "Test Creative",
            "format_id": {"agent_url": "https://example.com", "id": "display_300x250"},
            "assets": {"banner_image": {"url": "https://example.com/image.png"}},
        }

        with patch("src.core.tools.creatives._sync.get_db_session") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_session
            mock_get_db.return_value.__exit__.return_value = None

            with patch("src.core.tools.creatives._sync.get_current_tenant", return_value=mock_tenant):
                with patch(
                    "src.core.helpers.context_helpers.get_principal_from_context",
                    return_value=("test_principal", mock_tenant),
                ):
                    with patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry:
                        mock_reg_instance = MagicMock()
                        mock_registry.return_value = mock_reg_instance

                        _fmt_id2 = FormatId(agent_url="https://example.com", id="display_300x250")

                        # Mock async methods
                        async def mock_get_format(*args, **kwargs):
                            # Simulate work
                            await asyncio.sleep(0.001)
                            mock_format = MagicMock()
                            mock_format.format_id = _fmt_id2
                            mock_format.agent_url = "https://example.com"
                            mock_format.output_format_ids = None
                            return mock_format

                        async def mock_list_formats(*args, **kwargs):
                            # Simulate work
                            await asyncio.sleep(0.001)
                            mock_format = MagicMock()
                            mock_format.format_id = _fmt_id2
                            mock_format.agent_url = "https://example.com"
                            mock_format.output_format_ids = None
                            return [mock_format]

                        async def mock_preview(*args, **kwargs):
                            await asyncio.sleep(0.001)
                            return {
                                "previews": [
                                    {
                                        "renders": [
                                            {
                                                "preview_url": "https://example.com/preview.png",
                                                "dimensions": {"width": 300, "height": 250},
                                            }
                                        ]
                                    }
                                ]
                            }

                        mock_reg_instance.get_format = mock_get_format
                        mock_reg_instance.list_all_formats = mock_list_formats
                        mock_reg_instance.preview_creative = mock_preview
                        mock_session.scalars.return_value.first.return_value = None

                        # Mock session.begin_nested for savepoint
                        mock_session.begin_nested.return_value.__enter__.return_value = None
                        mock_session.begin_nested.return_value.__exit__.return_value = None

                        # This is the critical test: calling from async context should work
                        # Before the fix, this would raise RuntimeError about asyncio.run()
                        result = _sync_creatives_impl(
                            creatives=[creative],
                            context=None,
                            ctx=mock_context,
                        )

                        # Verify it succeeded
                        assert result is not None
                        assert len(result.creatives) >= 1
                        # May succeed or fail depending on mocks, but should NOT crash with asyncio error
