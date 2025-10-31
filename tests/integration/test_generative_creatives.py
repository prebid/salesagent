"""Integration tests for generative creative support.

Tests the flow where sync_creatives detects generative formats (those with output_format_ids)
and calls build_creative instead of preview_creative, using mocked Gemini API.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from src.core.database.models import MediaBuy, Principal
from src.core.schema_adapters import SyncCreativesResponse
from tests.utils.database_helpers import create_tenant_with_timestamps

# Tests now working - using structured format_id objects to bypass cache validation
pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class MockContext:
    """Mock FastMCP Context for testing."""

    def __init__(self, auth_token="test-token-123"):
        self.meta = {"headers": {"x-adcp-auth": auth_token}}


class FormatIdMatcher:
    """Helper class to match format_id comparisons in tests.

    The code at line 794 checks: if fmt.format_id == creative_format
    where creative_format is a dict {"agent_url": "...", "id": "..."}
    This class implements __eq__ to match when compared to dicts or strings.
    """

    def __init__(self, format_id_dict):
        self.format_id_dict = format_id_dict
        self.format_id = format_id_dict["id"] if isinstance(format_id_dict, dict) else format_id_dict

    def __eq__(self, other):
        if isinstance(other, dict) and "id" in other:
            return other["id"] == self.format_id
        return str(other) == self.format_id


class TestGenerativeCreatives:
    """Integration tests for generative creative functionality."""

    def _import_sync_creatives(self):
        """Import sync_creatives MCP tool."""
        from src.core.tools.creatives import sync_creatives_raw

        return sync_creatives_raw

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
                access_token="test-token-123",
                platform_mappings={"mock": {"advertiser_id": "test-advertiser"}},
            )
            session.add(principal)

            # Create media buy
            media_buy = MediaBuy(
                media_buy_id="mb-gen-001",
                tenant_id=tenant.tenant_id,
                principal_id=principal.principal_id,
                buyer_ref="buyer-gen-001",
                order_name="Test Order",  # Required field
                advertiser_name="Test Advertiser",  # Required field
                status="pending",
                start_date=datetime.now(UTC),
                end_date=datetime.now(UTC),
                raw_request={},  # Required field
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(media_buy)
            session.commit()

            self.tenant_id = tenant.tenant_id
            self.principal_id = principal.principal_id
            self.media_buy_id = media_buy.media_buy_id

    @patch("src.core.creative_agent_registry.get_creative_agent_registry")
    @patch("src.core.config.get_config")
    def test_generative_format_detection_calls_build_creative(self, mock_get_config, mock_get_registry):
        """Test that generative formats (with output_format_ids) call build_creative."""
        # Setup mocks
        mock_config = MagicMock()
        mock_config.gemini_api_key = "test-gemini-key"
        mock_get_config.return_value = mock_config

        # Mock format with output_format_ids (generative)
        mock_format = MagicMock()
        format_dict = {"agent_url": "https://test-agent.example.com", "id": "display_300x250_generative"}
        mock_format.format_id = FormatIdMatcher(format_dict)
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
            context=context,
            creatives=[
                {
                    "creative_id": "gen-creative-001",
                    "name": "Test Generative Creative",
                    "format_id": {
                        "agent_url": "https://test-agent.example.com",
                        "id": "display_300x250_generative",
                    },
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
        # format_id is passed as str(dict) because dict doesn't have .id attribute
        # In production, this would be a FormatId object with .id attribute
        format_id_param = call_args[1]["format_id"]
        assert "display_300x250_generative" in str(format_id_param)
        assert call_args[1]["message"] == "Create a banner ad for eco-friendly products"
        assert call_args[1]["gemini_api_key"] == "test-gemini-key"

        # Verify result
        assert isinstance(result, SyncCreativesResponse)
        assert len(result.creatives) == 1
        assert result.creatives[0].action == "created"

        # Verify creative was stored with generative data
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(creative_id="gen-creative-001")
            creative = session.scalars(stmt).first()
            assert creative is not None
            assert creative.data.get("generative_status") == "draft"
            assert creative.data.get("generative_context_id") == "ctx-123"
            assert creative.data.get("url") == "https://example.com/generated.html"

    @patch("src.core.creative_agent_registry.get_creative_agent_registry")
    @patch("src.core.config.get_config")
    def test_static_format_calls_preview_creative(self, mock_get_config, mock_get_registry):
        """Test that static formats (without output_format_ids) call preview_creative."""
        # Mock format without output_format_ids (static)
        mock_format = MagicMock()
        format_dict = {"agent_url": "https://test-agent.example.com", "id": "display_300x250"}
        mock_format.format_id = FormatIdMatcher(format_dict)
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
            context=context,
            creatives=[
                {
                    "creative_id": "static-creative-001",
                    "name": "Test Static Creative",
                    "format_id": {
                        "agent_url": "https://test-agent.example.com",
                        "id": "display_300x250",
                    },
                    "assets": {"image": {"url": "https://example.com/banner.png"}},
                }
            ],
        )

        # Verify preview_creative was called (not build_creative)
        assert mock_registry.preview_creative.called
        assert not hasattr(mock_registry, "build_creative") or not mock_registry.build_creative.called

        # Verify result
        assert isinstance(result, SyncCreativesResponse)
        assert len(result.creatives) == 1
        assert result.creatives[0].action == "created"

    @patch("src.core.creative_agent_registry.get_creative_agent_registry")
    @patch("src.core.config.get_config")
    def test_missing_gemini_api_key_raises_error(self, mock_get_config, mock_get_registry):
        """Test that missing GEMINI_API_KEY raises clear error for generative formats."""
        # Setup mocks - no API key
        mock_config = MagicMock()
        mock_config.gemini_api_key = None
        mock_get_config.return_value = mock_config

        # Mock generative format
        mock_format = MagicMock()
        format_dict = {"agent_url": "https://test-agent.example.com", "id": "display_300x250_generative"}
        mock_format.format_id = FormatIdMatcher(format_dict)
        mock_format.agent_url = "https://test-agent.example.com"
        mock_format.output_format_ids = ["display_300x250"]

        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=[mock_format])
        mock_get_registry.return_value = mock_registry

        # Call sync_creatives - should fail the creative (not raise exception)
        sync_fn = self._import_sync_creatives()
        context = MockContext()

        result = sync_fn(
            context=context,
            creatives=[
                {
                    "creative_id": "gen-creative-002",
                    "name": "Test Generative Creative",
                    "format_id": {
                        "agent_url": "https://test-agent.example.com",
                        "id": "display_300x250_generative",
                    },
                    "assets": {"message": {"content": "Test message"}},
                }
            ],
        )

        # Verify creative failed with appropriate error message
        assert isinstance(result, SyncCreativesResponse)
        assert len(result.creatives) == 1
        assert result.creatives[0].action == "failed"
        assert result.creatives[0].errors
        assert any("GEMINI_API_KEY" in str(err) for err in result.creatives[0].errors)

    @patch("src.core.creative_agent_registry.get_creative_agent_registry")
    @patch("src.core.config.get_config")
    def test_message_extraction_from_assets(self, mock_get_config, mock_get_registry):
        """Test that message is correctly extracted from various asset roles."""
        mock_config = MagicMock()
        mock_config.gemini_api_key = "test-key"
        mock_get_config.return_value = mock_config

        mock_format = MagicMock()
        format_dict = {"agent_url": "https://test-agent.example.com", "id": "display_300x250_generative"}
        mock_format.format_id = FormatIdMatcher(format_dict)
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
            context=context,
            creatives=[
                {
                    "creative_id": "gen-creative-003",
                    "name": "Test",
                    "format_id": {
                        "agent_url": "https://test-agent.example.com",
                        "id": "display_300x250_generative",
                    },
                    "assets": {"brief": {"content": "Message from brief"}},
                }
            ],
        )

        call_args = mock_registry.build_creative.call_args
        assert call_args[1]["message"] == "Message from brief"

    @patch("src.core.creative_agent_registry.get_creative_agent_registry")
    @patch("src.core.config.get_config")
    def test_message_fallback_to_creative_name(self, mock_get_config, mock_get_registry):
        """Test that creative name is used as fallback when no message provided."""
        mock_config = MagicMock()
        mock_config.gemini_api_key = "test-key"
        mock_get_config.return_value = mock_config

        mock_format = MagicMock()
        format_dict = {"agent_url": "https://test-agent.example.com", "id": "display_300x250_generative"}
        mock_format.format_id = FormatIdMatcher(format_dict)
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
            context=context,
            creatives=[
                {
                    "creative_id": "gen-creative-004",
                    "name": "Eco-Friendly Products Banner",
                    "format_id": {
                        "agent_url": "https://test-agent.example.com",
                        "id": "display_300x250_generative",
                    },
                    "assets": {},
                }
            ],
        )

        call_args = mock_registry.build_creative.call_args
        assert call_args[1]["message"] == "Create a creative for: Eco-Friendly Products Banner"

    @patch("src.core.creative_agent_registry.get_creative_agent_registry")
    @patch("src.core.config.get_config")
    def test_context_id_reuse_for_refinement(self, mock_get_config, mock_get_registry):
        """Test that context_id is reused for iterative refinement."""
        mock_config = MagicMock()
        mock_config.gemini_api_key = "test-key"
        mock_get_config.return_value = mock_config

        mock_format = MagicMock()
        format_dict = {"agent_url": "https://test-agent.example.com", "id": "display_300x250_generative"}
        mock_format.format_id = FormatIdMatcher(format_dict)
        mock_format.agent_url = "https://test-agent.example.com"
        mock_format.output_format_ids = ["display_300x250"]

        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=[mock_format])
        mock_registry.build_creative = AsyncMock(
            return_value={
                "status": "draft",
                "context_id": "ctx-original",
                "creative_output": {
                    "output_format": {"url": "https://example.com/generated-initial.html"},
                },
            }
        )
        mock_get_registry.return_value = mock_registry

        sync_fn = self._import_sync_creatives()
        context = MockContext()

        # Create initial creative
        sync_fn(
            context=context,
            creatives=[
                {
                    "creative_id": "gen-creative-005",
                    "name": "Test",
                    "format_id": {
                        "agent_url": "https://test-agent.example.com",
                        "id": "display_300x250_generative",
                    },
                    "assets": {"message": {"content": "Initial message"}},
                }
            ],
        )

        # Update with refinement - context_id should be reused
        mock_registry.build_creative = AsyncMock(
            return_value={
                "status": "draft",
                "context_id": "ctx-original",  # Same context
                "creative_output": {
                    "output_format": {"url": "https://example.com/generated-refined.html"},
                },
            }
        )

        sync_fn(
            context=context,
            creatives=[
                {
                    "creative_id": "gen-creative-005",  # Same ID
                    "name": "Test",
                    "format_id": {
                        "agent_url": "https://test-agent.example.com",
                        "id": "display_300x250_generative",
                    },
                    "assets": {"message": {"content": "Refined message"}},
                }
            ],
        )

        # Verify context_id was passed in the update
        call_args = mock_registry.build_creative.call_args
        assert call_args[1]["context_id"] == "ctx-original"
        assert call_args[1]["message"] == "Refined message"

    @patch("src.core.creative_agent_registry.get_creative_agent_registry")
    @patch("src.core.config.get_config")
    def test_promoted_offerings_extraction(self, mock_get_config, mock_get_registry):
        """Test that promoted_offerings are extracted from assets."""
        mock_config = MagicMock()
        mock_config.gemini_api_key = "test-key"
        mock_get_config.return_value = mock_config

        mock_format = MagicMock()
        format_dict = {"agent_url": "https://test-agent.example.com", "id": "display_300x250_generative"}
        mock_format.format_id = FormatIdMatcher(format_dict)
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
            context=context,
            creatives=[
                {
                    "creative_id": "gen-creative-006",
                    "name": "Test",
                    "format_id": {
                        "agent_url": "https://test-agent.example.com",
                        "id": "display_300x250_generative",
                    },
                    "assets": {
                        "message": {"content": "Test message"},
                        "promoted_offerings": promoted_offerings_data,
                    },
                }
            ],
        )

        call_args = mock_registry.build_creative.call_args
        assert call_args[1]["promoted_offerings"] == promoted_offerings_data
