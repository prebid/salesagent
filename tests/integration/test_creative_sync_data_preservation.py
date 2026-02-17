"""Integration tests for creative sync data preservation.

These tests verify that user-provided data is never silently overwritten by
system-generated data (previews, generative outputs, etc.).

Context:
--------
During security audit, we discovered 6 critical bugs where system data was
unconditionally overwriting user data. These tests ensure that pattern never
returns.

Pattern Under Test:
------------------
if system_data and not user_data:
    use_system_data()
else:
    preserve_user_data()

Critical Bugs This Prevents:
----------------------------
1. Preview URL overwriting user-provided URL from assets
2. Preview dimensions overwriting user-provided dimensions
3. Generative creative output replacing user-provided assets
4. Generative creative URL replacing user-provided URL
5. Platform creative IDs being lost on re-upload
6. Tracking URLs being replaced instead of merged

Test Strategy:
-------------
- Use real database (integration_db fixture)
- Mock only external APIs (creative agent, Gemini)
- Assert exact values, not just presence
- Test both create and update paths
- Test both static and generative formats
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from src.core.database.models import MediaBuy, Principal
from tests.utils.database_helpers import create_tenant_with_timestamps

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class MockContext:
    """Mock FastMCP Context for testing."""

    def __init__(self, auth_token="test-token-preserve"):
        self.meta = {"headers": {"x-adcp-auth": auth_token}}


def _make_format_id(agent_url: str, format_id: str) -> "LibraryFormatId":
    """Create a proper FormatId model for test mocks."""
    from adcp.types.generated_poc.core.format_id import FormatId as LibraryFormatId

    return LibraryFormatId(agent_url=agent_url, id=format_id)


class TestCreativeSyncDataPreservation:
    """Test that sync_creatives preserves user data over system data."""

    def _import_sync_creatives(self):
        """Import sync_creatives raw function."""
        from src.core.tools.creatives import sync_creatives_raw

        return sync_creatives_raw

    @pytest.fixture(autouse=True)
    def setup_test_data(self, integration_db):
        """Create test tenant, principal, and media buy."""
        with get_db_session() as session:
            # Create test tenant
            tenant = create_tenant_with_timestamps(
                tenant_id="test-tenant-preserve",
                name="Test Tenant Data Preservation",
                subdomain="test-preserve",
            )
            session.add(tenant)

            # Create principal
            principal = Principal(
                principal_id="test-principal-preserve",
                tenant_id=tenant.tenant_id,
                name="Test Principal Preserve",
                access_token="test-token-preserve",
                platform_mappings={"mock": {"advertiser_id": "test-advertiser"}},
            )
            session.add(principal)

            # Create media buy
            media_buy = MediaBuy(
                media_buy_id="mb-preserve-001",
                tenant_id=tenant.tenant_id,
                principal_id=principal.principal_id,
                buyer_ref="buyer-preserve-001",
                order_name="Test Order",
                advertiser_name="Test Advertiser",
                status="pending",
                start_date=datetime.now(UTC),
                end_date=datetime.now(UTC),
                raw_request={},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(media_buy)
            session.commit()

            self.tenant_id = tenant.tenant_id
            self.principal_id = principal.principal_id
            self.media_buy_id = media_buy.media_buy_id

    @patch("src.core.creative_agent_registry.get_creative_agent_registry")
    def test_sync_preserves_user_url_when_preview_available(self, mock_get_registry):
        """Test that user-provided URL from assets is NOT overwritten by preview URL.

        Bug Context:
        -----------
        Line 587 (update) and line 945 (create) unconditionally set:
            data["url"] = first_render["preview_url"]

        This caused user URLs like "https://via.placeholder.com/300x250/blue/white"
        to be replaced with system placeholder "https://placeholder.example.com/missing.jpg"

        Test Verifies:
        -------------
        When user provides assets.banner_image.url = "USER_URL"
        And preview_creative returns preview_url = "SYSTEM_URL"
        Then database stores url = "USER_URL" (user data preserved)
        """
        # Setup mock format (static, has preview)
        mock_format = MagicMock()
        mock_format.format_id = _make_format_id("https://test-agent.example.com", "display_300x250_image")
        mock_format.agent_url = "https://test-agent.example.com"
        mock_format.output_format_ids = None  # Static format

        # Mock preview_creative to return a different URL
        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=[mock_format])
        mock_registry.get_format = AsyncMock(return_value=mock_format)
        mock_registry.preview_creative = AsyncMock(
            return_value={
                "previews": [
                    {
                        "renders": [
                            {
                                "preview_url": "https://system-preview.example.com/placeholder.png",
                                "dimensions": {"width": 300, "height": 250},
                            }
                        ]
                    }
                ]
            }
        )
        mock_get_registry.return_value = mock_registry

        # User-provided creative with URL in assets
        user_url = "https://user-provided.example.com/actual-creative.png"
        sync_fn = self._import_sync_creatives()
        context = MockContext()

        result = sync_fn(
            ctx=context,
            creatives=[
                {
                    "creative_id": "preserve-url-001",
                    "name": "User Creative with URL",
                    "format_id": {"agent_url": "https://test-agent.example.com", "id": "display_300x250_image"},
                    "assets": {"banner_image": {"url": user_url, "width": 300, "height": 250}},
                }
            ],
        )

        # Verify creative was created
        assert len(result.creatives) == 1
        assert result.creatives[0].action == "created"

        # Verify URL in database matches USER URL, not system preview URL
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(creative_id="preserve-url-001")
            creative = session.scalars(stmt).first()
            assert creative is not None
            assert creative.data.get("url") == user_url, (
                f"Expected user URL '{user_url}' but got '{creative.data.get('url')}'. "
                f"User data was overwritten by system preview URL!"
            )

    @patch("src.core.creative_agent_registry.get_creative_agent_registry")
    def test_sync_preserves_dimensions_when_preview_has_different_size(self, mock_get_registry):
        """Test that user-provided dimensions are NOT overwritten by preview dimensions.

        Bug Context:
        -----------
        Lines 953-958 (create) and similar in update path unconditionally set:
            data["width"] = dimensions["width"]
            data["height"] = dimensions["height"]

        This caused user-specified sizes to be lost when preview returned different dimensions.

        Test Verifies:
        -------------
        When user provides width=728, height=90
        And preview returns width=300, height=250
        Then database stores width=728, height=90 (user data preserved)
        """
        # Setup mock format
        mock_format = MagicMock()
        mock_format.format_id = _make_format_id("https://test-agent.example.com", "display_728x90_image")
        mock_format.agent_url = "https://test-agent.example.com"
        mock_format.output_format_ids = None

        # Mock preview returns DIFFERENT dimensions
        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=[mock_format])
        mock_registry.get_format = AsyncMock(return_value=mock_format)
        mock_registry.preview_creative = AsyncMock(
            return_value={
                "previews": [
                    {
                        "renders": [
                            {
                                "preview_url": "https://system-preview.example.com/placeholder.png",
                                "dimensions": {"width": 300, "height": 250},  # Different from user!
                            }
                        ]
                    }
                ]
            }
        )
        mock_get_registry.return_value = mock_registry

        # User-provided dimensions
        user_width, user_height = 728, 90
        sync_fn = self._import_sync_creatives()
        context = MockContext()

        result = sync_fn(
            ctx=context,
            creatives=[
                {
                    "creative_id": "preserve-dims-001",
                    "name": "User Creative with Dimensions",
                    "format_id": {"agent_url": "https://test-agent.example.com", "id": "display_728x90_image"},
                    "width": user_width,
                    "height": user_height,
                    "url": "https://user.example.com/banner.png",
                }
            ],
        )

        # Verify creative was created
        assert len(result.creatives) == 1
        assert result.creatives[0].action == "created"

        # Verify dimensions in database match USER dimensions, not preview
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(creative_id="preserve-dims-001")
            creative = session.scalars(stmt).first()
            assert creative is not None
            assert creative.data.get("width") == user_width, (
                f"Expected user width {user_width} but got {creative.data.get('width')}. "
                f"User dimensions were overwritten by preview!"
            )
            assert creative.data.get("height") == user_height, (
                f"Expected user height {user_height} but got {creative.data.get('height')}. "
                f"User dimensions were overwritten by preview!"
            )

    @patch("src.core.creative_agent_registry.get_creative_agent_registry")
    @patch("src.core.config.get_config")
    def test_generative_output_preserves_user_assets(self, mock_get_config, mock_get_registry):
        """Test that user-provided assets are NOT replaced by generative output.

        Bug Context:
        -----------
        Lines 495, 866 unconditionally set:
            data["assets"] = creative_output["assets"]

        This caused user's carefully crafted AdCP-compliant asset structures
        to be completely replaced by AI-generated output.

        Test Verifies:
        -------------
        When user provides assets = {"banner_image": {"url": "USER_ASSET"}}
        And build_creative returns creative_output.assets = {"generated": {...}}
        Then database stores assets = {"banner_image": {"url": "USER_ASSET"}} (preserved)
        """
        # Setup mocks
        mock_config = MagicMock()
        mock_config.gemini_api_key = "test-gemini-key"
        mock_get_config.return_value = mock_config

        # Mock generative format
        mock_format = MagicMock()
        mock_format.format_id = _make_format_id("https://test-agent.example.com", "display_300x250_generative")
        mock_format.agent_url = "https://test-agent.example.com"
        mock_format.output_format_ids = ["display_300x250"]  # Generative

        # Mock build_creative returns DIFFERENT assets
        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=[mock_format])
        mock_registry.get_format = AsyncMock(return_value=mock_format)
        mock_registry.build_creative = AsyncMock(
            return_value={
                "status": "draft",
                "context_id": "ctx-123",
                "creative_output": {
                    "assets": {
                        "generated_headline": {"text": "AI Generated Headline"},
                        "generated_image": {"url": "https://ai-generated.example.com/output.png"},
                    },
                    "output_format": {"url": "https://ai-generated.example.com/creative.html"},
                },
            }
        )
        mock_get_registry.return_value = mock_registry

        # User-provided assets (ImageAsset adds alt_text/format defaults after parsing)
        user_assets = {
            "banner_image": {
                "url": "https://user-creative.example.com/banner.png",
                "width": 300,
                "height": 250,
                "alt_text": None,
                "format": None,
            }
        }

        sync_fn = self._import_sync_creatives()
        context = MockContext()

        result = sync_fn(
            ctx=context,
            creatives=[
                {
                    "creative_id": "preserve-assets-001",
                    "name": "User Creative with Assets",
                    "format_id": {"agent_url": "https://test-agent.example.com", "id": "display_300x250_generative"},
                    "assets": {
                        "banner_image": {
                            "url": "https://user-creative.example.com/banner.png",
                            "width": 300,
                            "height": 250,
                        }
                    },
                }
            ],
        )

        # Verify creative was created
        assert len(result.creatives) == 1
        assert result.creatives[0].action == "created"

        # Verify assets in database match USER assets, not replaced by AI-generated
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(creative_id="preserve-assets-001")
            creative = session.scalars(stmt).first()
            assert creative is not None
            assert creative.data.get("assets") == user_assets, (
                f"Expected user assets {user_assets} but got {creative.data.get('assets')}. "
                f"User assets were replaced by generative output!"
            )

    @patch("src.core.creative_agent_registry.get_creative_agent_registry")
    @patch("src.core.config.get_config")
    def test_generative_output_preserves_user_url(self, mock_get_config, mock_get_registry):
        """Test that user-provided URL is NOT replaced by generative output URL.

        Bug Context:
        -----------
        Lines 506, 873 unconditionally set:
            data["url"] = output_format["url"]

        When user provided URL via assets, generative output would overwrite it.

        Test Verifies:
        -------------
        When user provides assets with URL
        And build_creative returns output_format.url = "AI_URL"
        Then database stores url = "USER_URL" (preserved)
        """
        # Setup mocks
        mock_config = MagicMock()
        mock_config.gemini_api_key = "test-gemini-key"
        mock_get_config.return_value = mock_config

        # Mock generative format
        mock_format = MagicMock()
        mock_format.format_id = _make_format_id("https://test-agent.example.com", "video_generative")
        mock_format.agent_url = "https://test-agent.example.com"
        mock_format.output_format_ids = ["video_mp4"]

        # Mock build_creative returns DIFFERENT URL
        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=[mock_format])
        mock_registry.get_format = AsyncMock(return_value=mock_format)
        mock_registry.build_creative = AsyncMock(
            return_value={
                "status": "draft",
                "context_id": "ctx-456",
                "creative_output": {
                    "assets": {"video": {"url": "https://ai-generated.example.com/video.mp4"}},
                    "output_format": {"url": "https://ai-generated.example.com/video-final.mp4"},  # Different from user
                },
            }
        )
        mock_get_registry.return_value = mock_registry

        # User-provided URL via assets
        user_url = "https://user-video.example.com/campaign-video.mp4"
        sync_fn = self._import_sync_creatives()
        context = MockContext()

        result = sync_fn(
            ctx=context,
            creatives=[
                {
                    "creative_id": "preserve-gen-url-001",
                    "name": "User Video Creative",
                    "format_id": {"agent_url": "https://test-agent.example.com", "id": "video_generative"},
                    "assets": {"video": {"url": user_url, "duration": 30}},
                }
            ],
        )

        # Verify creative was created
        assert len(result.creatives) == 1
        assert result.creatives[0].action == "created"

        # Verify URL in database matches USER URL, not AI-generated
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(creative_id="preserve-gen-url-001")
            creative = session.scalars(stmt).first()
            assert creative is not None
            assert creative.data.get("url") == user_url, (
                f"Expected user URL '{user_url}' but got '{creative.data.get('url')}'. "
                f"User URL was replaced by generative output!"
            )

    @patch("src.core.creative_agent_registry.get_creative_agent_registry")
    def test_update_preserves_user_url_when_preview_changes(self, mock_get_registry):
        """Test that UPDATE path also preserves user URL over preview URL.

        Bug Context:
        -----------
        Same bug (line 587) affected UPDATE path. User updates were losing data.

        Test Verifies:
        -------------
        When updating existing creative with new user URL
        And preview returns different URL
        Then database stores new user URL (not preview)
        """
        # Setup mock format
        mock_format = MagicMock()
        mock_format.format_id = _make_format_id("https://test-agent.example.com", "display_300x250_image")
        mock_format.agent_url = "https://test-agent.example.com"
        mock_format.output_format_ids = None

        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=[mock_format])
        mock_registry.get_format = AsyncMock(return_value=mock_format)
        mock_registry.preview_creative = AsyncMock(
            return_value={
                "previews": [
                    {
                        "renders": [
                            {
                                "preview_url": "https://system-preview-v2.example.com/placeholder.png",
                                "dimensions": {"width": 300, "height": 250},
                            }
                        ]
                    }
                ]
            }
        )
        mock_get_registry.return_value = mock_registry

        sync_fn = self._import_sync_creatives()
        context = MockContext()

        # First create the creative
        original_url = "https://user-v1.example.com/banner.png"
        sync_fn(
            ctx=context,
            creatives=[
                {
                    "creative_id": "update-preserve-001",
                    "name": "Creative to Update",
                    "format_id": {"agent_url": "https://test-agent.example.com", "id": "display_300x250_image"},
                    "assets": {"banner_image": {"url": original_url, "width": 300, "height": 250}},
                }
            ],
        )

        # Now update with new user URL
        new_user_url = "https://user-v2.example.com/banner-updated.png"
        result = sync_fn(
            ctx=context,
            creatives=[
                {
                    "creative_id": "update-preserve-001",
                    "name": "Creative to Update",
                    "format_id": {"agent_url": "https://test-agent.example.com", "id": "display_300x250_image"},
                    "assets": {"banner_image": {"url": new_user_url, "width": 300, "height": 250}},
                }
            ],
        )

        # Verify creative was updated
        assert len(result.creatives) == 1
        assert result.creatives[0].action in ["updated", "unchanged"]

        # Verify URL in database matches NEW user URL, not preview
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(creative_id="update-preserve-001")
            creative = session.scalars(stmt).first()
            assert creative is not None
            assert creative.data.get("url") == new_user_url, (
                f"Expected updated user URL '{new_user_url}' but got '{creative.data.get('url')}'. "
                f"User update was overwritten by preview!"
            )
