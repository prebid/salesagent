"""Test sync_creatives asyncio and variable scoping fixes.

This test file ensures the fixes for:
1. asyncio.run() in running event loop error
2. creative_id variable scoping error

Both issues occurred in production when sync_creatives was called via MCP.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from adcp.types import FormatId

from src.core.tools.creatives._sync import _sync_creatives_impl
from src.core.validation_helpers import run_async_in_sync_context
from tests.factories.creative_asset import build_assets, image_spec, text_spec
from tests.harness import make_mock_uow
from tests.helpers.creative_test_helpers import sync_creatives_request
from tests.helpers.unit_identity import fabricated_account_identity


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

    @pytest.mark.asyncio
    async def test_coroutine_raised_runtimeerror_propagates_in_async_context(self):
        """A RuntimeError raised BY the coroutine must propagate unmangled.

        : the helper wraps BOTH the get_running_loop() probe AND
        the thread-pool execution in one try. When a running loop exists, a
        coroutine-raised RuntimeError (e.g. httpx/anyio 'Event loop is closed')
        re-raises out of future.result() INSIDE that try, is misread as
        "no running loop", and the already-CONSUMED coroutine is re-run on a
        fresh loop — surfacing as 'cannot reuse already awaited coroutine'
        instead of the real error (which downstream degrades to a terminal
        CREATIVE_REJECTED).
        """

        async def boom():
            raise RuntimeError("Event loop is closed")

        with pytest.raises(RuntimeError, match="Event loop is closed"):
            run_async_in_sync_context(boom())

    def test_coroutine_raised_runtimeerror_propagates_in_sync_context(self):
        """Same contract without a running loop (guards both branches)."""

        async def boom():
            raise RuntimeError("Event loop is closed")

        with pytest.raises(RuntimeError, match="Event loop is closed"):
            run_async_in_sync_context(boom())


def _make_creative_uow():
    """Create a mock CreativeUoW with creative_repo returning sensible defaults."""
    mock_creative_repo = MagicMock()
    mock_creative_repo.get_provenance_policies.return_value = []
    mock_creative_repo.get_by_id.return_value = None
    _, mock_uow = make_mock_uow(
        repos={
            "creatives": mock_creative_repo,
            "assignments": MagicMock(),
        }
    )
    return mock_uow, mock_creative_repo


class TestSyncCreativesErrorHandling:
    """Test sync_creatives error handling paths that use creative_id.

    ``test_creative_id_defined_in_error_path`` used to live here. Its premise was that a
    creative dict missing ``name`` and ``format_id`` reaches the per-creative loop and fails
    THERE with ``creative_id`` bound. That premise is now inverted: every caller builds a
    SyncCreativesRequest first, and core/creative-asset.json makes both fields required, so
    such an item is refused at the request boundary and never reaches the loop. The branch it
    graded -- the loop's ``except (ValidationError, ValueError)`` branch, and that it names the
    offending creative_id -- is still reached by a spec-legal item that fails
    ``_validate_creative_input``, and is graded by
    test_sync_creatives_format_validation.py::test_format_validation_unknown_format and
    test_creative.py::TestExtensionGaps::test_ext_c_validation_failure_strict_others_processed.
    """

    @pytest.mark.asyncio
    async def test_creative_id_in_preview_failure_path(self):
        """Test that creative_id is available when creative agent preview fails.

        NOTE: This test was updated after fixing data preservation bugs.
        Creatives with valid media URLs in assets should SUCCEED even if preview fails,
        because preview is optional for static creatives with direct URLs.

        To test actual failure path, use creative WITHOUT any URL (no assets, no url field).
        """
        mock_uow, mock_creative_repo = _make_creative_uow()

        # Mock the per-creative savepoint
        mock_creative_repo.savepoint.return_value.__enter__.return_value = None
        mock_creative_repo.savepoint.return_value.__exit__.return_value = None

        identity = fabricated_account_identity(approval_mode="auto-approve")

        # Creative with NO URL anywhere - this should fail when preview returns no previews.
        # ``assets`` is spec-REQUIRED (core/creative-asset.json), so the slot map cannot
        # simply be omitted the way it was when this payload went straight into _impl; a TEXT
        # asset satisfies the schema while still carrying no URL, which is what the preview
        # path here needs.
        creative = {
            "creative_id": "test_creative_456",
            "name": "Test Creative",
            "format_id": {"agent_url": "https://example.com", "id": "display_300x250"},
            "assets": build_assets(text_spec("message", content="No URL anywhere")),
        }

        with patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls:
            mock_uow_cls.return_value.__enter__.return_value = mock_uow

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

                # This should handle the error gracefully with creative_id available
                result = _sync_creatives_impl(req=sync_creatives_request(creatives=[creative]), identity=identity)

                # Verify error was captured with correct creative_id
                assert len(result.creatives) == 1
                assert result.creatives[0].creative_id == "test_creative_456"
                assert result.creatives[0].action == "failed"


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
        mock_uow, mock_creative_repo = _make_creative_uow()

        # Mock the per-creative savepoint
        mock_creative_repo.savepoint.return_value.__enter__.return_value = None
        mock_creative_repo.savepoint.return_value.__exit__.return_value = None

        identity = fabricated_account_identity(approval_mode="auto-approve")

        creative = {
            "creative_id": "test_creative_789",
            "name": "Test Creative",
            "format_id": {"agent_url": "https://example.com", "id": "display_300x250"},
            "assets": build_assets(
                image_spec("banner_image", url="https://example.com/image.png", width=300, height=250)
            ),
        }

        with patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls:
            mock_uow_cls.return_value.__enter__.return_value = mock_uow

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

                # This is the critical test: calling from async context should work
                # Before the fix, this would raise RuntimeError about asyncio.run()
                result = _sync_creatives_impl(
                    req=sync_creatives_request(creatives=[creative], context=None), identity=identity
                )

                # Verify it succeeded
                assert result is not None
                assert len(result.creatives) >= 1
                # May succeed or fail depending on mocks, but should NOT crash with asyncio error
