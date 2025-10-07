"""Integration tests for creative lifecycle MCP tools.

Tests sync_creatives and list_creatives MCP tools with real database operations.
These tests verify the integration between FastMCP tool definitions and database persistence,
without mocking the core business logic or database operations.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from src.core.database.models import CreativeAssignment, MediaBuy, Principal
from src.core.schemas import ListCreativesResponse, SyncCreativesResponse
from tests.utils.database_helpers import create_tenant_with_timestamps, get_utc_now


class MockContext:
    """Mock FastMCP Context for testing."""

    def __init__(self, auth_token="test-token-123"):
        if auth_token is None:
            self.meta = {"headers": {}}  # No auth header for testing optional auth
        else:
            self.meta = {"headers": {"x-adcp-auth": auth_token}}


class TestCreativeLifecycleMCP:
    """Integration tests for creative lifecycle MCP tools."""

    def _import_mcp_tools(self):
        """Import MCP tools to avoid module-level database initialization."""
        from src.core.main import list_creatives as core_list_creatives_tool
        from src.core.main import sync_creatives as core_sync_creatives_tool

        # Extract the actual functions from FunctionTool objects if needed
        sync_fn = core_sync_creatives_tool.fn if hasattr(core_sync_creatives_tool, "fn") else core_sync_creatives_tool
        list_fn = core_list_creatives_tool.fn if hasattr(core_list_creatives_tool, "fn") else core_list_creatives_tool

        return sync_fn, list_fn

    @pytest.fixture(autouse=True)
    def setup_test_data(self, integration_db):
        """Create test tenant, principal, and media buy for creative tests."""
        with get_db_session() as session:
            # Create test tenant
            tenant = create_tenant_with_timestamps(
                tenant_id="creative_test",
                name="Creative Test Tenant",
                subdomain="creative-test",
                is_active=True,
                ad_server="mock",
                max_daily_budget=10000,
                enable_axe_signals=True,
                authorized_emails=[],
                authorized_domains=[],
                auto_approve_formats=["display_300x250", "display_728x90"],
                human_review_required=False,
            )
            session.add(tenant)

            # Create test principal
            principal = Principal(
                tenant_id="creative_test",
                principal_id="test_advertiser",
                name="Test Advertiser",
                access_token="test-token-123",
                platform_mappings={"mock": {"id": "test_advertiser"}},
            )
            session.add(principal)

            # Create test media buy with packages in raw_request
            media_buy = MediaBuy(
                tenant_id="creative_test",
                media_buy_id="test_media_buy_1",
                principal_id="test_advertiser",
                order_name="Test Order",
                advertiser_name="Test Advertiser",
                status="active",
                budget=5000.0,
                start_date=get_utc_now().date(),
                end_date=(get_utc_now() + timedelta(days=30)).date(),
                buyer_ref="buyer_ref_123",
                raw_request={
                    "test": True,
                    "packages": [
                        {"package_id": "package_1", "status": "active"},
                        {"package_id": "package_2", "status": "active"},
                        {"package_id": "package_buyer_ref", "status": "active"},
                    ],
                },
            )
            session.add(media_buy)

            session.commit()

        # Store test data for easy access
        self.test_tenant_id = "creative_test"
        self.test_principal_id = "test_advertiser"
        self.test_media_buy_id = "test_media_buy_1"
        self.test_buyer_ref = "buyer_ref_123"

    @pytest.fixture
    def mock_context(self):
        """Create mock FastMCP context."""
        return MockContext()

    @pytest.fixture
    def sample_creatives(self):
        """Sample creative data for testing."""
        return [
            {
                "creative_id": "creative_display_1",
                "name": "Banner Ad 300x250",
                "format": "display_300x250",
                "url": "https://example.com/banner.jpg",
                "click_url": "https://advertiser.com/landing",
                "width": 300,
                "height": 250,
            },
            {
                "creative_id": "creative_video_1",
                "name": "Video Ad 30sec",
                "format": "video_pre_roll",
                "url": "https://example.com/video.mp4",
                "click_url": "https://advertiser.com/video-landing",
                "width": 640,
                "height": 360,
                "duration": 30.0,
            },
            {
                "creative_id": "creative_native_1",
                "name": "Native Ad with Snippet",
                "format": "native_content",
                "snippet": "<script>window.adTag = 'native';</script>",
                "snippet_type": "javascript",
                "template_variables": {"headline": "Amazing Product!", "cta": "Learn More"},
                "click_url": "https://advertiser.com/native-landing",
            },
        ]

    def test_sync_creatives_create_new_creatives(self, mock_context, sample_creatives):
        """Test sync_creatives creates new creatives successfully."""
        core_sync_creatives_tool, _ = self._import_mcp_tools()

        with (
            patch("src.core.main._get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.main.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):

            # Call sync_creatives tool (uses default patch=False for full upsert)
            response = core_sync_creatives_tool(creatives=sample_creatives, context=mock_context)

            # Verify response structure
            assert isinstance(response, SyncCreativesResponse)
            assert len(response.synced_creatives) == 3
            assert len(response.failed_creatives) == 0
            assert len(response.assignments) == 0
            assert "Synced 3 creatives" in response.message

            # Verify database persistence
            with get_db_session() as session:
                db_creatives = session.scalars(select(DBCreative).filter_by(tenant_id=self.test_tenant_id)).all()
                assert len(db_creatives) == 3

                # Verify display creative
                display_creative = next((c for c in db_creatives if c.format == "display_300x250"), None)
                assert display_creative is not None
                assert display_creative.name == "Banner Ad 300x250"
                assert display_creative.data.get("url") == "https://example.com/banner.jpg"
                assert display_creative.data.get("width") == 300
                assert display_creative.data.get("height") == 250
                assert display_creative.status == "pending"

                # Verify video creative
                video_creative = next((c for c in db_creatives if c.format == "video_pre_roll"), None)
                assert video_creative is not None
                assert video_creative.data.get("duration") == 30.0

                # Verify native creative with snippet
                native_creative = next((c for c in db_creatives if c.format == "native_content"), None)
                assert native_creative is not None
                assert native_creative.data.get("snippet") == "<script>window.adTag = 'native';</script>"
                assert native_creative.data.get("snippet_type") == "javascript"
                assert native_creative.data.get("template_variables") == {
                    "headline": "Amazing Product!",
                    "cta": "Learn More",
                }

    def test_sync_creatives_upsert_existing_creative(self, mock_context):
        """Test sync_creatives updates existing creative (default patch=False behavior)."""
        core_sync_creatives_tool, _ = self._import_mcp_tools()
        # First, create an existing creative
        with get_db_session() as session:
            existing_creative = DBCreative(
                tenant_id=self.test_tenant_id,
                creative_id="creative_update_test",
                principal_id=self.test_principal_id,
                name="Old Creative Name",
                format="display_300x250",
                status="pending",
                data={
                    "url": "https://example.com/old.jpg",
                    "width": 300,
                    "height": 250,
                },
            )
            session.add(existing_creative)
            session.commit()

        # Now sync with updated data
        updated_creative_data = [
            {
                "creative_id": "creative_update_test",
                "name": "Updated Creative Name",
                "format": "display_300x250",
                "url": "https://example.com/updated.jpg",
                "click_url": "https://advertiser.com/updated-landing",
                "width": 300,
                "height": 250,
            }
        ]

        with (
            patch("src.core.main._get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.main.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):

            # Upsert with patch=False (default): full replacement
            response = core_sync_creatives_tool(creatives=updated_creative_data, context=mock_context)

            # Verify response
            assert len(response.synced_creatives) == 1
            assert len(response.failed_creatives) == 0

            # Verify database update
            with get_db_session() as session:
                updated_creative = session.scalars(
                    select(DBCreative).filter_by(tenant_id=self.test_tenant_id, creative_id="creative_update_test")
                ).first()

                assert updated_creative.name == "Updated Creative Name"
                assert updated_creative.data.get("url") == "https://example.com/updated.jpg"
                assert updated_creative.data.get("click_url") == "https://advertiser.com/updated-landing"
                assert updated_creative.updated_at is not None

    def test_sync_creatives_with_package_assignments(self, mock_context, sample_creatives):
        """Test sync_creatives assigns creatives to packages using spec-compliant assignments dict."""
        core_sync_creatives_tool, _ = self._import_mcp_tools()

        # Get the creative_id from the first sample creative
        creative_data = sample_creatives[:1]
        creative_id = creative_data[0]["creative_id"]

        with (
            patch("src.core.main._get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.main.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):

            # Use spec-compliant assignments dict: creative_id → package_ids
            response = core_sync_creatives_tool(
                creatives=creative_data,
                assignments={creative_id: ["package_1", "package_2"]},
                context=mock_context,
            )

            # Verify assignments created
            assert len(response.assignments) == 2
            assert "2 assignments created" in response.message

            # Verify database assignments
            with get_db_session() as session:
                assignments = session.scalars(
                    select(CreativeAssignment).filter_by(
                        tenant_id=self.test_tenant_id, media_buy_id=self.test_media_buy_id
                    )
                ).all()

                assert len(assignments) == 2
                package_ids = [a.package_id for a in assignments]
                assert "package_1" in package_ids
                assert "package_2" in package_ids

    def test_sync_creatives_with_assignments_lookup(self, mock_context, sample_creatives):
        """Test sync_creatives with assignments dict (spec-compliant approach)."""
        core_sync_creatives_tool, _ = self._import_mcp_tools()

        # Get the creative_id from the first sample creative
        creative_data = sample_creatives[:1]
        creative_id = creative_data[0]["creative_id"]

        with (
            patch("src.core.main._get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.main.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):

            # Use spec-compliant assignments dict
            response = core_sync_creatives_tool(
                creatives=creative_data,
                assignments={creative_id: ["package_buyer_ref"]},
                context=mock_context,
            )

            # Verify assignment created
            assert len(response.assignments) == 1
            assert response.assignments[0].media_buy_id == self.test_media_buy_id

    def test_sync_creatives_validation_failures(self, mock_context):
        """Test sync_creatives handles validation failures gracefully."""
        core_sync_creatives_tool, _ = self._import_mcp_tools()
        invalid_creatives = [
            {
                "creative_id": "valid_creative",
                "name": "Valid Creative",
                "format": "display_300x250",
                "url": "https://example.com/valid.jpg",
            },
            {
                "creative_id": "invalid_creative",
                "name": "",  # Invalid: empty name
                "format": "display_300x250",
            },
        ]

        with (
            patch("src.core.main._get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.main.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):

            response = core_sync_creatives_tool(creatives=invalid_creatives, context=mock_context)

            # Should sync valid creative but fail on invalid one
            assert len(response.synced_creatives) == 1
            assert len(response.failed_creatives) == 1
            assert "1 failed" in response.message

            # Verify only valid creative was persisted
            with get_db_session() as session:
                db_creatives = session.scalars(select(DBCreative).filter_by(tenant_id=self.test_tenant_id)).all()
                creative_ids = [c.creative_id for c in db_creatives]
                assert "valid_creative" in creative_ids
                assert "invalid_creative" not in creative_ids

    def test_list_creatives_no_filters(self, mock_context):
        """Test list_creatives returns all creatives when no filters applied."""
        _, core_list_creatives_tool = self._import_mcp_tools()
        _, core_list_creatives_tool = self._import_mcp_tools()
        # Create test creatives in database
        with get_db_session() as session:
            creatives = [
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id=f"list_test_{i}",
                    principal_id=self.test_principal_id,
                    name=f"Test Creative {i}",
                    format="display_300x250",
                    status="approved" if i % 2 == 0 else "pending",
                    data={
                        "url": f"https://example.com/creative_{i}.jpg",
                        "width": 300,
                        "height": 250,
                    },
                )
                for i in range(5)
            ]
            session.add_all(creatives)
            session.commit()

        with (
            patch("src.core.main._get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.main.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):

            response = core_list_creatives_tool(context=mock_context)

            # Verify response structure
            assert isinstance(response, ListCreativesResponse)
            assert len(response.creatives) == 5
            assert response.total_count == 5
            assert response.has_more is False

            # Verify creatives are sorted by created_date desc by default
            creative_names = [c.name for c in response.creatives]
            assert creative_names[0] == "Test Creative 0"  # Most recent
            assert creative_names[-1] == "Test Creative 4"  # Oldest

    def test_list_creatives_with_status_filter(self, mock_context):
        """Test list_creatives filters by status correctly."""
        _, core_list_creatives_tool = self._import_mcp_tools()
        # Create creatives with different statuses
        with get_db_session() as session:
            creatives = [
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id=f"status_test_approved_{i}",
                    principal_id=self.test_principal_id,
                    name=f"Approved Creative {i}",
                    format="display_300x250",
                    status="approved",
                )
                for i in range(3)
            ] + [
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id=f"status_test_pending_{i}",
                    principal_id=self.test_principal_id,
                    name=f"Pending Creative {i}",
                    format="display_728x90",
                    status="pending",
                )
                for i in range(2)
            ]
            session.add_all(creatives)
            session.commit()

        with (
            patch("src.core.main._get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.main.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):

            # Test approved filter
            response = core_list_creatives_tool(status="approved", context=mock_context)
            assert len(response.creatives) == 3
            assert all(c.status == "approved" for c in response.creatives)

            # Test pending filter
            response = core_list_creatives_tool(status="pending", context=mock_context)
            assert len(response.creatives) == 2
            assert all(c.status == "pending" for c in response.creatives)

    def test_list_creatives_with_format_filter(self, mock_context):
        """Test list_creatives filters by format correctly."""
        _, core_list_creatives_tool = self._import_mcp_tools()
        # Create creatives with different formats
        with get_db_session() as session:
            creatives = [
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id=f"format_test_300x250_{i}",
                    principal_id=self.test_principal_id,
                    name=f"Banner {i}",
                    format="display_300x250",
                    status="approved",
                )
                for i in range(2)
            ] + [
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id=f"format_test_video_{i}",
                    principal_id=self.test_principal_id,
                    name=f"Video {i}",
                    format="video_pre_roll",
                    status="approved",
                    data={"duration": 15.0},
                )
                for i in range(3)
            ]
            session.add_all(creatives)
            session.commit()

        with (
            patch("src.core.main._get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.main.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):

            # Test display format filter
            response = core_list_creatives_tool(format="display_300x250", context=mock_context)
            assert len(response.creatives) == 2
            assert all(c.format == "display_300x250" for c in response.creatives)

            # Test video format filter
            response = core_list_creatives_tool(format="video_pre_roll", context=mock_context)
            assert len(response.creatives) == 3
            assert all(c.format == "video_pre_roll" for c in response.creatives)

    def test_list_creatives_with_date_filters(self, mock_context):
        """Test list_creatives filters by creation date range."""
        _, core_list_creatives_tool = self._import_mcp_tools()
        now = datetime.now(UTC)

        # Create creatives with different creation dates
        with get_db_session() as session:
            creatives = [
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id=f"date_test_old_{i}",
                    principal_id=self.test_principal_id,
                    name=f"Old Creative {i}",
                    format="display_300x250",
                    status="approved",
                    created_at=now - timedelta(days=10 + i),  # 10+ days ago
                )
                for i in range(2)
            ] + [
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id=f"date_test_recent_{i}",
                    principal_id=self.test_principal_id,
                    name=f"Recent Creative {i}",
                    format="display_300x250",
                    status="approved",
                    created_at=now - timedelta(days=2 + i),  # 2-3 days ago
                )
                for i in range(2)
            ]
            session.add_all(creatives)
            session.commit()

        with (
            patch("src.core.main._get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.main.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):

            # Test created_after filter
            created_after = (now - timedelta(days=5)).isoformat()
            response = core_list_creatives_tool(created_after=created_after, context=mock_context)
            assert len(response.creatives) == 2  # Only recent creatives

            # Test created_before filter
            created_before = (now - timedelta(days=5)).isoformat()
            response = core_list_creatives_tool(created_before=created_before, context=mock_context)
            assert len(response.creatives) == 2  # Only old creatives

    def test_list_creatives_with_search(self, mock_context):
        """Test list_creatives search functionality."""
        _, core_list_creatives_tool = self._import_mcp_tools()
        # Create creatives with searchable names
        with get_db_session() as session:
            creatives = [
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id="search_test_banner_1",
                    principal_id=self.test_principal_id,
                    name="Holiday Banner Ad",
                    format="display_300x250",
                    status="approved",
                ),
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id="search_test_video_1",
                    principal_id=self.test_principal_id,
                    name="Holiday Video Ad",
                    format="video_pre_roll",
                    status="approved",
                ),
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id="search_test_summer_1",
                    principal_id=self.test_principal_id,
                    name="Summer Sale Banner",
                    format="display_728x90",
                    status="approved",
                ),
            ]
            session.add_all(creatives)
            session.commit()

        with (
            patch("src.core.main._get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.main.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):

            # Search for "Holiday"
            response = core_list_creatives_tool(search="Holiday", context=mock_context)
            assert len(response.creatives) == 2
            assert all("Holiday" in c.name for c in response.creatives)

            # Search for "Banner"
            response = core_list_creatives_tool(search="Banner", context=mock_context)
            assert len(response.creatives) == 2
            assert all("Banner" in c.name for c in response.creatives)

    def test_list_creatives_pagination_and_sorting(self, mock_context):
        """Test list_creatives pagination and sorting options."""
        _, core_list_creatives_tool = self._import_mcp_tools()
        # Create multiple creatives for pagination testing
        with get_db_session() as session:
            creatives = [
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id=f"page_test_{i:02d}",
                    principal_id=self.test_principal_id,
                    name=f"Creative {i:02d}",
                    format="display_300x250",
                    status="approved",
                )
                for i in range(25)  # Create 25 creatives
            ]
            session.add_all(creatives)
            session.commit()

        with (
            patch("src.core.main._get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.main.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):

            # Test first page
            response = core_list_creatives_tool(page=1, limit=10, context=mock_context)
            assert len(response.creatives) == 10
            assert response.total_count == 25
            assert response.has_more is True

            # Test second page
            response = core_list_creatives_tool(page=2, limit=10, context=mock_context)
            assert len(response.creatives) == 10
            assert response.has_more is True

            # Test last page
            response = core_list_creatives_tool(page=3, limit=10, context=mock_context)
            assert len(response.creatives) == 5
            assert response.has_more is False

            # Test name sorting ascending
            response = core_list_creatives_tool(sort_by="name", sort_order="asc", limit=5, context=mock_context)
            creative_names = [c.name for c in response.creatives]
            assert creative_names == sorted(creative_names)

    def test_list_creatives_with_media_buy_assignments(self, mock_context):
        """Test list_creatives filters by media buy assignments."""
        _, core_list_creatives_tool = self._import_mcp_tools()
        # Create creatives and assignments
        with get_db_session() as session:
            # Create creatives
            creative_1 = DBCreative(
                tenant_id=self.test_tenant_id,
                creative_id="assignment_test_1",
                principal_id=self.test_principal_id,
                name="Assigned Creative 1",
                format="display_300x250",
                status="approved",
            )
            creative_2 = DBCreative(
                tenant_id=self.test_tenant_id,
                creative_id="assignment_test_2",
                principal_id=self.test_principal_id,
                name="Unassigned Creative",
                format="display_300x250",
                status="approved",
            )
            session.add_all([creative_1, creative_2])

            # Create assignment for only one creative
            assignment = CreativeAssignment(
                tenant_id=self.test_tenant_id,
                assignment_id=str(uuid.uuid4()),
                creative_id="assignment_test_1",
                media_buy_id=self.test_media_buy_id,
                package_id="test_package",
                weight=100,
            )
            session.add(assignment)
            session.commit()

        with (
            patch("src.core.main._get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.main.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):

            # Filter by media_buy_id - should only return assigned creative
            response = core_list_creatives_tool(media_buy_id=self.test_media_buy_id, context=mock_context)
            assert len(response.creatives) == 1
            assert response.creatives[0].creative_id == "assignment_test_1"

            # Filter by buyer_ref - should also work
            response = core_list_creatives_tool(buyer_ref=self.test_buyer_ref, context=mock_context)
            assert len(response.creatives) == 1
            assert response.creatives[0].creative_id == "assignment_test_1"

    def test_sync_creatives_authentication_required(self, sample_creatives):
        """Test sync_creatives requires proper authentication."""
        core_sync_creatives_tool, _ = self._import_mcp_tools()
        mock_context = MockContext("invalid-token")

        with patch("src.core.main._get_principal_id_from_context", side_effect=Exception("Invalid auth token")):

            with pytest.raises(Exception) as exc_info:
                core_sync_creatives_tool(creatives=sample_creatives, context=mock_context)

            assert "Invalid auth token" in str(exc_info.value)

    def test_list_creatives_authentication_optional(self, mock_context):
        """Test list_creatives allows optional authentication (for discovery) but rejects invalid tokens."""
        from fastmcp.exceptions import ToolError

        _, core_list_creatives_tool = self._import_mcp_tools()

        # Test 1: Invalid token should raise error
        mock_context = MockContext("invalid-token")
        with pytest.raises(ToolError) as exc_info:
            core_list_creatives_tool(context=mock_context)
        assert "INVALID_AUTH_TOKEN" in str(exc_info.value)

        # Test 2: No token (None) should work for discovery
        mock_context_no_auth = MockContext(None)
        result = core_list_creatives_tool(context=mock_context_no_auth)
        assert isinstance(result, ListCreativesResponse)
        assert result.creatives == []  # Empty list since no auth context

    def test_sync_creatives_missing_tenant(self, mock_context, sample_creatives):
        """Test sync_creatives handles missing tenant gracefully."""
        core_sync_creatives_tool, _ = self._import_mcp_tools()
        with (
            patch("src.core.main._get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.main.get_current_tenant", return_value=None),
        ):

            with pytest.raises(Exception) as exc_info:
                core_sync_creatives_tool(creatives=sample_creatives, context=mock_context)

            assert "No tenant context available" in str(exc_info.value)

    def test_list_creatives_empty_results(self, mock_context):
        """Test list_creatives handles empty results gracefully."""
        _, core_list_creatives_tool = self._import_mcp_tools()
        with (
            patch("src.core.main._get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.main.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):

            # Query with filters that match nothing
            response = core_list_creatives_tool(status="rejected", context=mock_context)  # No rejected creatives exist

            assert len(response.creatives) == 0
            assert response.total_count == 0
            assert response.has_more is False

    def test_create_media_buy_with_creative_ids(self, mock_context, sample_creatives):
        """Test create_media_buy accepts creative_ids in packages."""
        # First, sync creatives to have IDs to reference
        core_sync_creatives_tool, _ = self._import_mcp_tools()
        with (
            patch("src.core.main._get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.main.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):
            sync_response = core_sync_creatives_tool(creatives=sample_creatives, context=mock_context)
            assert len(sync_response.synced_creatives) == 3

        # Import create_media_buy tool
        from src.core.schemas import Budget, Package
        from src.core.tools import create_media_buy_raw

        # Create media buy with creative_ids in packages
        creative_ids = [c["creative_id"] for c in sample_creatives]

        with (
            patch("src.core.main._get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.main.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
            patch("src.core.main.get_principal_object") as mock_principal,
            patch("src.core.main.get_adapter") as mock_adapter,
            patch("src.core.main.get_product_catalog") as mock_catalog,
        ):
            # Mock principal
            from src.core.schemas import Principal as SchemaPrincipal

            mock_principal.return_value = SchemaPrincipal(
                principal_id=self.test_principal_id,
                name="Test Advertiser",
                platform_mappings={"mock": {"id": "test"}},
            )

            # Mock adapter
            from src.core.schemas import CreateMediaBuyResponse, TaskStatus

            mock_adapter_instance = mock_adapter.return_value
            mock_adapter_instance.create_media_buy.return_value = CreateMediaBuyResponse(
                media_buy_id="test_buy_123",
                status=TaskStatus.WORKING,
                message="Media buy created",
            )
            mock_adapter_instance.manual_approval_required = False

            # Mock product catalog
            from src.core.schemas import Product as SchemaProduct

            mock_catalog.return_value = [
                SchemaProduct(
                    product_id="prod_1",
                    name="Test Product",
                    description="Test",
                    formats=[],
                    delivery_type="non_guaranteed",
                    is_fixed_price=False,
                    price_guidance={"floor": 5.0, "p50": 10.0, "p90": 15.0},
                )
            ]

            # Create packages with creative_ids
            packages = [
                Package(
                    buyer_ref="pkg_1",
                    products=["prod_1"],
                    creative_ids=creative_ids,  # NEW: Provide creative_ids
                )
            ]

            # Call create_media_buy with packages containing creative_ids
            response = create_media_buy_raw(
                po_number="PO-TEST-123",
                promoted_offering="Test Campaign",
                packages=packages,
                start_time=datetime.now(UTC) + timedelta(days=1),
                end_time=datetime.now(UTC) + timedelta(days=30),
                budget=Budget(total=5000.0, currency="USD"),
                context=mock_context,
            )

            # Verify response
            assert response.media_buy_id == "test_buy_123"
            assert response.status == TaskStatus.WORKING

            # Verify creative assignments were created in database
            with get_db_session() as session:
                assignments = session.scalars(
                    select(CreativeAssignment).filter_by(tenant_id=self.test_tenant_id, media_buy_id="test_buy_123")
                ).all()

                # Should have 3 assignments (one per creative)
                assert len(assignments) == 3

                # Verify all creative IDs are assigned
                assigned_creative_ids = {a.creative_id for a in assignments}
                assert assigned_creative_ids == set(creative_ids)
