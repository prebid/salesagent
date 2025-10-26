"""Integration tests for generative creative support.

Tests the flow where sync_creatives detects generative formats (those with output_format_ids)
and calls build_creative instead of preview_creative, using mocked Gemini API.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, Principal
from tests.utils.database_helpers import create_tenant_with_timestamps

# TODO: Fix generative creative tests - complex mock setup needs debugging
pytestmark = [pytest.mark.integration, pytest.mark.requires_db, pytest.mark.skip_ci]


class MockContext:
    """Mock FastMCP Context for testing."""

    def __init__(self, auth_token="test-token-123"):
        self.meta = {"headers": {"x-adcp-auth": auth_token}}


class TestGenerativeCreatives:
    """Integration tests for generative creative functionality."""

    def _import_sync_creatives(self):
        """Import sync_creatives MCP tool."""
        from src.core.main import sync_creatives as core_sync_creatives_tool

        sync_fn = core_sync_creatives_tool.fn if hasattr(core_sync_creatives_tool, "fn") else core_sync_creatives_tool
        return sync_fn

    @pytest.fixture(autouse=True)
    def setup_test_data(self, integration_db):
        """Create test tenant, principal, and media buy."""
        with get_db_session() as session:
            # Create test tenant
            tenant = create_tenant_with_timestamps(
                tenant_id="test-tenant-gen",
                name="Test Tenant Generative",
                subdomain="test-gen",
            )
            session.add(tenant)

            # Create principal
            principal = Principal(
                principal_id="test-principal-gen",
                tenant_id=tenant.tenant_id,
                name="Test Principal Gen",
                token="test-token-123",
            )
            session.add(principal)

            # Create media buy
            media_buy = MediaBuy(
                media_buy_id="mb-gen-001",
                tenant_id=tenant.tenant_id,
                principal_id=principal.principal_id,
                buyer_ref="buyer-gen-001",
                status="pending",
                start_date=datetime.now(UTC),
                end_date=datetime.now(UTC),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(media_buy)
            session.commit()

            self.tenant_id = tenant.tenant_id
            self.principal_id = principal.principal_id
            self.media_buy_id = media_buy.media_buy_id

    @patch("src.core.main.get_creative_agent_registry")
    @patch("src.core.main.get_config")
    def test_generative_format_detection_calls_build_creative(self, mock_get_config, mock_get_registry):
        """Test that generative formats (with output_format_ids) call build_creative."""
        # Setup mocks
        mock_config = MagicMock()
        mock_config.gemini_api_key = "test-gemini-key"
        mock_get_config.return_value = mock_config

        # Mock format with output_format_ids (generative)
        mock_format = MagicMock()
        mock_format.format_id = "display_300x250_generative"
        mock_format.agent_url = "https://test-agent.example.com"
        mock_format.output_format_ids = ["display_300x250"]  # This makes it generative

        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=[mock_format])
        mock_registry.build_creative = AsyncMock(
            return_value={
                "status": "draft",
                "context_id": "ctx-123",
                "creative_output": {
                    "assets": {"headline": {"text": "Generated headline"}},
                    "output_format": {"url": "https://example.com/generated.html"},
                },
            }
        )
        mock_get_registry.return_value = mock_registry

        # Call sync_creatives with generative format
        sync_fn = self._import_sync_creatives()
        context = MockContext()

        result = sync_fn(
            ctx=context,
            media_buy_id=self.media_buy_id,
            creatives=[
                {
                    "creative_id": "gen-creative-001",
                    "name": "Test Generative Creative",
                    "format_id": "display_300x250_generative",
                    "assets": {"message": {"content": "Create a banner ad for eco-friendly products"}},
                }
            ],
        )

        # Verify build_creative was called (not preview_creative)
        assert mock_registry.build_creative.called
        assert not hasattr(mock_registry, "preview_creative") or not mock_registry.preview_creative.called

        # Verify build_creative was called with correct parameters
        call_args = mock_registry.build_creative.call_args
        assert call_args[1]["agent_url"] == "https://test-agent.example.com"
        assert call_args[1]["format_id"] == "display_300x250_generative"
        assert call_args[1]["message"] == "Create a banner ad for eco-friendly products"
        assert call_args[1]["gemini_api_key"] == "test-gemini-key"

        # Verify result
        assert isinstance(result, SyncCreativesResponse)
        assert result.created_count == 1

        # Verify creative was stored with generative data
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(creative_id="gen-creative-001")
            creative = session.scalars(stmt).first()
            assert creative is not None
            assert creative.data.get("generative_status") == "draft"
            assert creative.data.get("generative_context_id") == "ctx-123"
            assert creative.data.get("url") == "https://example.com/generated.html"

    @patch("src.core.main.get_creative_agent_registry")
    @patch("src.core.main.get_config")
    def test_static_format_calls_preview_creative(self, mock_get_config, mock_get_registry):
        """Test that static formats (without output_format_ids) call preview_creative."""
        # Mock format without output_format_ids (static)
        mock_format = MagicMock()
        mock_format.format_id = "display_300x250"
        mock_format.agent_url = "https://test-agent.example.com"
        mock_format.output_format_ids = None  # No output_format_ids = static

        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=[mock_format])
        mock_registry.preview_creative = AsyncMock(
            return_value={
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
        )
        mock_get_registry.return_value = mock_registry

        # Call sync_creatives with static format
        sync_fn = self._import_sync_creatives()
        context = MockContext()

        result = sync_fn(
            ctx=context,
            media_buy_id=self.media_buy_id,
            creatives=[
                {
                    "creative_id": "static-creative-001",
                    "name": "Test Static Creative",
                    "format_id": "display_300x250",
                    "assets": {"image": {"url": "https://example.com/banner.png"}},
                }
            ],
        )

        # Verify preview_creative was called (not build_creative)
        assert mock_registry.preview_creative.called
        assert not hasattr(mock_registry, "build_creative") or not mock_registry.build_creative.called

        # Verify result
        assert isinstance(result, SyncCreativesResponse)
        assert result.created_count == 1

    @patch("src.core.main.get_creative_agent_registry")
    @patch("src.core.main.get_config")
    def test_missing_gemini_api_key_raises_error(self, mock_get_config, mock_get_registry):
        """Test that missing GEMINI_API_KEY raises clear error for generative formats."""
        # Setup mocks - no API key
        mock_config = MagicMock()
        mock_config.gemini_api_key = None
        mock_get_config.return_value = mock_config

        # Mock generative format
        mock_format = MagicMock()
        mock_format.format_id = "display_300x250_generative"
        mock_format.agent_url = "https://test-agent.example.com"
        mock_format.output_format_ids = ["display_300x250"]

        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=[mock_format])
        mock_get_registry.return_value = mock_registry

        # Call sync_creatives - should raise error
        sync_fn = self._import_sync_creatives()
        context = MockContext()

        with pytest.raises(ValueError, match="GEMINI_API_KEY not configured"):
            sync_fn(
                ctx=context,
                media_buy_id=self.media_buy_id,
                creatives=[
                    {
                        "creative_id": "gen-creative-002",
                        "name": "Test Generative Creative",
                        "format_id": "display_300x250_generative",
                        "assets": {"message": {"content": "Test message"}},
                    }
                ],
            )

    @patch("src.core.main.get_creative_agent_registry")
    @patch("src.core.main.get_config")
    def test_message_extraction_from_assets(self, mock_get_config, mock_get_registry):
        """Test that message is correctly extracted from various asset roles."""
        mock_config = MagicMock()
        mock_config.gemini_api_key = "test-key"
        mock_get_config.return_value = mock_config

        mock_format = MagicMock()
        mock_format.format_id = "display_300x250_generative"
        mock_format.agent_url = "https://test-agent.example.com"
        mock_format.output_format_ids = ["display_300x250"]

        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=[mock_format])
        mock_registry.build_creative = AsyncMock(
            return_value={
                "status": "draft",
                "context_id": "ctx-456",
                "creative_output": {},
            }
        )
        mock_get_registry.return_value = mock_registry

        sync_fn = self._import_sync_creatives()
        context = MockContext()

        # Test with "brief" role
        sync_fn(
            ctx=context,
            media_buy_id=self.media_buy_id,
            creatives=[
                {
                    "creative_id": "gen-creative-003",
                    "name": "Test",
                    "format_id": "display_300x250_generative",
                    "assets": {"brief": {"content": "Message from brief"}},
                }
            ],
        )

        call_args = mock_registry.build_creative.call_args
        assert call_args[1]["message"] == "Message from brief"

    @patch("src.core.main.get_creative_agent_registry")
    @patch("src.core.main.get_config")
    def test_message_fallback_to_creative_name(self, mock_get_config, mock_get_registry):
        """Test that creative name is used as fallback when no message provided."""
        mock_config = MagicMock()
        mock_config.gemini_api_key = "test-key"
        mock_get_config.return_value = mock_config

        mock_format = MagicMock()
        mock_format.format_id = "display_300x250_generative"
        mock_format.agent_url = "https://test-agent.example.com"
        mock_format.output_format_ids = ["display_300x250"]

        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=[mock_format])
        mock_registry.build_creative = AsyncMock(
            return_value={
                "status": "draft",
                "context_id": "ctx-789",
                "creative_output": {},
            }
        )
        mock_get_registry.return_value = mock_registry

        sync_fn = self._import_sync_creatives()
        context = MockContext()

        # No message in assets
        sync_fn(
            ctx=context,
            media_buy_id=self.media_buy_id,
            creatives=[
                {
                    "creative_id": "gen-creative-004",
                    "name": "Eco-Friendly Products Banner",
                    "format_id": "display_300x250_generative",
                    "assets": {},
                }
            ],
        )

        call_args = mock_registry.build_creative.call_args
        assert call_args[1]["message"] == "Create a creative for: Eco-Friendly Products Banner"

    @patch("src.core.main.get_creative_agent_registry")
    @patch("src.core.main.get_config")
    def test_context_id_reuse_for_refinement(self, mock_get_config, mock_get_registry):
        """Test that context_id is reused for iterative refinement."""
        mock_config = MagicMock()
        mock_config.gemini_api_key = "test-key"
        mock_get_config.return_value = mock_config

        mock_format = MagicMock()
        mock_format.format_id = "display_300x250_generative"
        mock_format.agent_url = "https://test-agent.example.com"
        mock_format.output_format_ids = ["display_300x250"]

        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=[mock_format])
        mock_registry.build_creative = AsyncMock(
            return_value={
                "status": "draft",
                "context_id": "ctx-original",
                "creative_output": {},
            }
        )
        mock_get_registry.return_value = mock_registry

        sync_fn = self._import_sync_creatives()
        context = MockContext()

        # Create initial creative
        sync_fn(
            ctx=context,
            media_buy_id=self.media_buy_id,
            creatives=[
                {
                    "creative_id": "gen-creative-005",
                    "name": "Test",
                    "format_id": "display_300x250_generative",
                    "assets": {"message": {"content": "Initial message"}},
                }
            ],
        )

        # Update with refinement - context_id should be reused
        mock_registry.build_creative = AsyncMock(
            return_value={
                "status": "draft",
                "context_id": "ctx-original",  # Same context
                "creative_output": {},
            }
        )

        sync_fn(
            ctx=context,
            media_buy_id=self.media_buy_id,
            creatives=[
                {
                    "creative_id": "gen-creative-005",  # Same ID
                    "name": "Test",
                    "format_id": "display_300x250_generative",
                    "assets": {"message": {"content": "Refined message"}},
                }
            ],
        )

        # Verify context_id was passed in the update
        call_args = mock_registry.build_creative.call_args
        assert call_args[1]["context_id"] == "ctx-original"
        assert call_args[1]["message"] == "Refined message"

    @patch("src.core.main.get_creative_agent_registry")
    @patch("src.core.main.get_config")
    def test_promoted_offerings_extraction(self, mock_get_config, mock_get_registry):
        """Test that promoted_offerings are extracted from assets."""
        mock_config = MagicMock()
        mock_config.gemini_api_key = "test-key"
        mock_get_config.return_value = mock_config

        mock_format = MagicMock()
        mock_format.format_id = "display_300x250_generative"
        mock_format.agent_url = "https://test-agent.example.com"
        mock_format.output_format_ids = ["display_300x250"]

        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=[mock_format])
        mock_registry.build_creative = AsyncMock(
            return_value={
                "status": "draft",
                "context_id": "ctx-999",
                "creative_output": {},
            }
        )
        mock_get_registry.return_value = mock_registry

        sync_fn = self._import_sync_creatives()
        context = MockContext()

        promoted_offerings_data = {
            "name": "Eco Water Bottle",
            "description": "Sustainable water bottle",
        }

        sync_fn(
            ctx=context,
            media_buy_id=self.media_buy_id,
            creatives=[
                {
                    "creative_id": "gen-creative-006",
                    "name": "Test",
                    "format_id": "display_300x250_generative",
                    "assets": {
                        "message": {"content": "Test message"},
                        "promoted_offerings": promoted_offerings_data,
                    },
                }
            ],
        )

        call_args = mock_registry.build_creative.call_args
        assert call_args[1]["promoted_offerings"] == promoted_offerings_data
