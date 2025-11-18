"""Integration tests for creative lifecycle MCP tools.

Tests sync_creatives and list_creatives MCP tools with real database operations.
These tests verify the integration between FastMCP tool definitions and database persistence,
without mocking the core business logic or database operations.

NOTE: All Creative instances require agent_url field (added in schema migration).
This field is required by the database schema (NOT NULL constraint) and AdCP v2.4 spec
for creative format namespacing - each creative format is associated with an agent URL.
Test creatives use "https://test.com" as a default value.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    Creative as DBCreative,
)
from src.core.database.models import (
    CreativeAssignment,
    MediaBuy,
    Principal,
)
from src.core.schema_adapters import ListCreativesResponse, SyncCreativesResponse
from tests.utils.database_helpers import create_tenant_with_timestamps, get_utc_now

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class MockContext:
    """Mock FastMCP Context for testing."""

    def __init__(self, auth_token="test-token-123"):
        if auth_token is None:
            self.meta = {"headers": {}}  # No auth header for testing optional auth
        else:
            self.meta = {"headers": {"x-adcp-auth": auth_token}}


@pytest.mark.requires_db
class TestCreativeLifecycleMCP:
    """Integration tests for creative lifecycle MCP tools."""

    def _import_mcp_tools(self):
        """Import MCP tools to avoid module-level database initialization."""
        from src.core.tools.creatives import list_creatives_raw, sync_creatives_raw

        return sync_creatives_raw, list_creatives_raw

    @pytest.fixture(autouse=True)
    def setup_test_data(self, integration_db):
        """Create test tenant, principal, and media buy for creative tests."""
        with get_db_session() as session:
            # Create test tenant with auto-approve mode to avoid creative approval workflows
            tenant = create_tenant_with_timestamps(
                tenant_id="creative_test",
                name="Creative Test Tenant",
                subdomain="creative-test",
                is_active=True,
                ad_server="mock",
                enable_axe_signals=True,
                authorized_emails=[],
                authorized_domains=[],
                auto_approve_format_ids=["display_300x250", "display_728x90"],
                human_review_required=False,
                approval_mode="auto-approve",  # Auto-approve creatives to avoid workflow blocking
            )
            session.add(tenant)

            # Add currency limit for USD
            from src.core.database.models import CurrencyLimit

            currency_limit = CurrencyLimit(
                tenant_id="creative_test",
                currency_code="USD",
                min_package_budget=1000.0,
                max_daily_package_spend=10000.0,
            )
            session.add(currency_limit)

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
            session.commit()  # Commit media_buy first so foreign key exists

            # Create test media packages for creative assignments
            from src.core.database.models import MediaPackage

            package_1 = MediaPackage(
                media_buy_id="test_media_buy_1",
                package_id="package_1",
                package_config={"package_id": "package_1", "name": "Package 1", "status": "active"},
            )
            package_2 = MediaPackage(
                media_buy_id="test_media_buy_1",
                package_id="package_2",
                package_config={"package_id": "package_2", "name": "Package 2", "status": "active"},
            )
            package_buyer_ref = MediaPackage(
                media_buy_id="test_media_buy_1",
                package_id="package_buyer_ref",
                package_config={"package_id": "package_buyer_ref", "name": "Package Buyer Ref", "status": "active"},
            )
            session.add(package_1)
            session.add(package_2)
            session.add(package_buyer_ref)
            session.commit()  # Commit media_packages

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
        """Sample creative data for testing.

        NOTE: Uses structured format objects with agent_url to avoid deprecated string format_ids.
        Available formats from creative agent: display_300x250, display_728x90, video_640x480, etc.
        """
        return [
            {
                "creative_id": "creative_display_1",
                "name": "Banner Ad 300x250",
                "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                "url": "https://example.com/banner.jpg",
                "click_url": "https://advertiser.com/landing",
                "width": 300,
                "height": 250,
            },
            {
                "creative_id": "creative_video_1",
                "name": "Video Ad 30sec",
                "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_640x480"},
                "url": "https://example.com/video.mp4",
                "click_url": "https://advertiser.com/video-landing",
                "width": 640,
                "height": 480,
                "duration": 30.0,
            },
            {
                "creative_id": "creative_display_2",
                "name": "Leaderboard Ad 728x90",
                "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
                "url": "https://example.com/leaderboard.jpg",
                "click_url": "https://advertiser.com/landing2",
                "width": 728,
                "height": 90,
            },
        ]

    def test_sync_creatives_create_new_creatives(self, mock_context, sample_creatives):
        """Test sync_creatives creates new creatives successfully."""
        core_sync_creatives_tool, _ = self._import_mcp_tools()

        with (
            patch("src.core.tools.creatives.get_principal_id_from_context", return_value=self.test_principal_id),
            patch(
                "src.core.tools.creatives.get_current_tenant",
                return_value={"tenant_id": self.test_tenant_id, "approval_mode": "auto-approve"},
            ),
        ):
            # Call sync_creatives tool (uses default patch=False for full upsert)
            response = core_sync_creatives_tool(creatives=sample_creatives, ctx=mock_context)

            # Verify response structure (AdCP-compliant domain response)
            assert isinstance(response, SyncCreativesResponse)
            # Domain response has creatives list with action field (not results/summary)
            assert len(response.creatives) == 3
            assert all(c.get("action") == "created" for c in response.creatives if isinstance(c, dict))
            # Verify __str__() generates correct message
            message = str(response)
            assert "3 created" in message or "Creative sync completed" in message

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
                assert display_creative.status == "approved"  # Auto-approved due to approval_mode setting

                # Verify video creative
                video_creative = next((c for c in db_creatives if c.format == "video_640x480"), None)
                assert video_creative is not None
                assert video_creative.data.get("duration") == 30.0

                # Verify leaderboard creative
                leaderboard_creative = next((c for c in db_creatives if c.format == "display_728x90"), None)
                assert leaderboard_creative is not None
                assert leaderboard_creative.data.get("width") == 728
                assert leaderboard_creative.data.get("height") == 90

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
                agent_url="https://creative.adcontextprotocol.org",
                format="display_300x250",
                status="pending",
                data={
                    "url": "https://example.com/old.jpg",
                    "width": 300,
                    "height": 250,
                    "assets": {"main": {"url": "https://example.com/old.jpg", "width": 300, "height": 250}},
                },
            )
            session.add(existing_creative)
            session.commit()

        # Now sync with updated data
        updated_creative_data = [
            {
                "creative_id": "creative_update_test",
                "name": "Updated Creative Name",
                "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                "url": "https://example.com/updated.jpg",
                "click_url": "https://advertiser.com/updated-landing",
                "width": 300,
                "height": 250,
            }
        ]

        with (
            patch("src.core.tools.creatives.get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.tools.creatives.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):
            # Upsert with patch=False (default): full replacement
            response = core_sync_creatives_tool(creatives=updated_creative_data, ctx=mock_context)

            # Verify response (domain response has creatives list, not summary/results)
            assert len(response.creatives) == 1
            # Check action on creative item
            creative_item = response.creatives[0]
            if isinstance(creative_item, dict):
                assert creative_item.get("action") == "updated"
            else:
                assert creative_item.action == "updated"

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
            patch("src.core.tools.creatives.get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.tools.creatives.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):
            # Use spec-compliant assignments dict: creative_id → package_ids
            response = core_sync_creatives_tool(
                creatives=creative_data,
                assignments={creative_id: ["package_1", "package_2"]},
                ctx=mock_context,
            )

            # Verify response structure
            assert isinstance(response, SyncCreativesResponse)
            assert len(response.creatives) > 0

            # Verify database assignments (assignments are separate from creatives list)
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
            patch("src.core.tools.creatives.get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.tools.creatives.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):
            # Use spec-compliant assignments dict
            response = core_sync_creatives_tool(
                creatives=creative_data,
                assignments={creative_id: ["package_buyer_ref"]},
                ctx=mock_context,
            )

            # Verify response structure
            assert isinstance(response, SyncCreativesResponse)
            assert len(response.creatives) > 0

            # Verify assignment in database (assignments are separate from creatives list)
            with get_db_session() as session:
                assignment = session.scalars(
                    select(CreativeAssignment).filter_by(
                        tenant_id=self.test_tenant_id, creative_id=creative_id, package_id="package_buyer_ref"
                    )
                ).first()
                assert assignment is not None
                assert assignment.media_buy_id == self.test_media_buy_id

    def test_sync_creatives_validation_failures(self, mock_context):
        """Test sync_creatives handles validation failures gracefully."""
        core_sync_creatives_tool, _ = self._import_mcp_tools()
        invalid_creatives = [
            {
                "creative_id": "valid_creative",
                "name": "Valid Creative",
                "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                "url": "https://example.com/valid.jpg",
            },
            {
                "creative_id": "invalid_creative",
                "name": "",  # Invalid: empty name
                "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
            },
        ]

        with (
            patch("src.core.tools.creatives.get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.tools.creatives.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
        ):
            response = core_sync_creatives_tool(creatives=invalid_creatives, ctx=mock_context)

            # Should sync valid creative but fail on invalid one
            # Domain response has creatives list with action field
            assert len(response.creatives) == 2
            # Count actions from creatives list (check both dict and object access)
            created_count = 0
            failed_count = 0
            for c in response.creatives:
                if isinstance(c, dict):
                    action = c.get("action")
                else:
                    action = getattr(c, "action", None)
                if action == "created":
                    created_count += 1
                elif action == "failed":
                    failed_count += 1
            assert created_count == 1, f"Expected 1 created, got {created_count}. Creatives: {response.creatives}"
            assert failed_count == 1, f"Expected 1 failed, got {failed_count}. Creatives: {response.creatives}"
            # Note: __str__() message may vary based on implementation - it's generated from creatives list

            # Verify only valid creative was persisted
            with get_db_session() as session:
                db_creatives = session.scalars(select(DBCreative).filter_by(tenant_id=self.test_tenant_id)).all()
                creative_ids = [c.creative_id for c in db_creatives]
                assert "valid_creative" in creative_ids
                assert "invalid_creative" not in creative_ids

    def test_list_creatives_no_filters(self, mock_context):
        """Test list_creatives returns all creatives when no filters applied."""
        _, core_list_creatives_tool = self._import_mcp_tools()
        # Create test creatives in database
        with get_db_session() as session:
            creatives = [
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id=f"list_test_{i}",
                    principal_id=self.test_principal_id,
                    name=f"Test Creative {i}",
                    agent_url="https://creative.adcontextprotocol.org",
                    format="display_300x250",
                    status="approved" if i % 2 == 0 else "pending_review",
                    data={
                        "url": f"https://example.com/creative_{i}.jpg",
                        "width": 300,
                        "height": 250,
                        "assets": {
                            "main": {
                                "url": f"https://example.com/creative_{i}.jpg",
                                "width": 300,
                                "height": 250,
                            }
                        },
                    },
                )
                for i in range(5)
            ]
            session.add_all(creatives)
            session.commit()

        with (
            patch("src.core.tools.creatives.get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.tools.creatives.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
            patch(
                "fastmcp.server.dependencies.get_http_headers",
                return_value={
                    "x-adcp-auth": "test-token-123",
                    "host": "creative-test.sales-agent.scope3.com",
                },
            ),
        ):
            response = core_list_creatives_tool(ctx=mock_context)

            # Verify response structure
            assert isinstance(response, ListCreativesResponse)
            assert len(response.creatives) == 5
            assert response.query_summary.total_matching == 5
            assert response.query_summary.returned == 5
            assert response.pagination.has_more is False

            # Verify creatives are sorted by created_date desc by default
            creative_names = [c.get("name") if isinstance(c, dict) else c.name for c in response.creatives]
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
                    agent_url="https://creative.adcontextprotocol.org",
                    format="display_300x250",
                    status="approved",
                    data={"assets": {"main": {"url": f"https://example.com/approved_{i}.jpg"}}},
                )
                for i in range(3)
            ] + [
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id=f"status_test_pending_{i}",
                    principal_id=self.test_principal_id,
                    name=f"Pending Creative {i}",
                    agent_url="https://creative.adcontextprotocol.org",
                    format="display_728x90",
                    status="pending_review",
                    data={"assets": {"main": {"url": f"https://example.com/pending_{i}.jpg"}}},
                )
                for i in range(2)
            ]
            session.add_all(creatives)
            session.commit()

        with (
            patch("src.core.tools.creatives.get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.tools.creatives.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
            patch(
                "fastmcp.server.dependencies.get_http_headers",
                return_value={
                    "x-adcp-auth": "test-token-123",
                    "host": "creative-test.sales-agent.scope3.com",
                },
            ),
        ):
            # Test approved filter
            response = core_list_creatives_tool(status="approved", ctx=mock_context)
            assert len(response.creatives) == 3
            # Check status field (handle both dict, object, and enum)
            for c in response.creatives:
                status_val = c.get("status") if isinstance(c, dict) else getattr(c, "status", None)
                # Handle enum values - get the string value
                from enum import Enum

                if isinstance(status_val, Enum):
                    status_val = status_val.value
                assert status_val == "approved"

            # Test pending_review filter (correct AdCP status value)
            response = core_list_creatives_tool(status="pending_review", ctx=mock_context)
            assert len(response.creatives) == 2
            # Check status field (handle both dict, object, and enum)
            for c in response.creatives:
                status_val = c.get("status") if isinstance(c, dict) else getattr(c, "status", None)
                # Handle enum values - get the string value
                from enum import Enum

                if isinstance(status_val, Enum):
                    status_val = status_val.value
                assert status_val == "pending_review"

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
                    agent_url="https://creative.adcontextprotocol.org",
                    format="display_300x250",
                    status="approved",
                    data={"assets": {"main": {"url": f"https://example.com/banner_{i}.jpg"}}},
                )
                for i in range(2)
            ] + [
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id=f"format_test_video_{i}",
                    principal_id=self.test_principal_id,
                    name=f"Video {i}",
                    agent_url="https://creative.adcontextprotocol.org",
                    format="video_640x480",
                    status="approved",
                    data={"duration": 15.0, "assets": {"main": {"url": f"https://example.com/video_{i}.mp4"}}},
                )
                for i in range(3)
            ]
            session.add_all(creatives)
            session.commit()

        with (
            patch("src.core.tools.creatives.get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.tools.creatives.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
            patch(
                "fastmcp.server.dependencies.get_http_headers",
                return_value={
                    "x-adcp-auth": "test-token-123",
                    "host": "creative-test.sales-agent.scope3.com",
                },
            ),
        ):
            # Test display format filter
            response = core_list_creatives_tool(format="display_300x250", ctx=mock_context)
            assert len(response.creatives) == 2
            # Check format field (may be string, FormatId object, or dict)
            for c in response.creatives:
                if isinstance(c, dict):
                    format_val = c.get("format")
                else:
                    format_val = getattr(c, "format", None)
                # Handle FormatId object by checking its id attribute
                if hasattr(format_val, "id"):
                    format_id = format_val.id
                elif isinstance(format_val, dict):
                    format_id = format_val.get("id")
                else:
                    format_id = format_val
                assert format_id == "display_300x250"

            # Test video format filter
            response = core_list_creatives_tool(format="video_640x480", ctx=mock_context)
            assert len(response.creatives) == 3
            # Check format field (may be string, FormatId object, or dict)
            for c in response.creatives:
                if isinstance(c, dict):
                    format_val = c.get("format")
                else:
                    format_val = getattr(c, "format", None)
                # Handle FormatId object by checking its id attribute
                if hasattr(format_val, "id"):
                    format_id = format_val.id
                elif isinstance(format_val, dict):
                    format_id = format_val.get("id")
                else:
                    format_id = format_val
                assert format_id == "video_640x480"

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
                    agent_url="https://creative.adcontextprotocol.org",
                    format="display_300x250",
                    status="approved",
                    created_at=now - timedelta(days=10 + i),  # 10+ days ago
                    data={"assets": {"main": {"url": f"https://example.com/old_{i}.jpg"}}},
                )
                for i in range(2)
            ] + [
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id=f"date_test_recent_{i}",
                    principal_id=self.test_principal_id,
                    name=f"Recent Creative {i}",
                    agent_url="https://creative.adcontextprotocol.org",
                    format="display_300x250",
                    status="approved",
                    created_at=now - timedelta(days=2 + i),  # 2-3 days ago
                    data={"assets": {"main": {"url": f"https://example.com/recent_{i}.jpg"}}},
                )
                for i in range(2)
            ]
            session.add_all(creatives)
            session.commit()

        with (
            patch("src.core.tools.creatives.get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.tools.creatives.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
            patch(
                "fastmcp.server.dependencies.get_http_headers",
                return_value={
                    "x-adcp-auth": "test-token-123",
                    "host": "creative-test.sales-agent.scope3.com",
                },
            ),
        ):
            # Test created_after filter
            created_after = (now - timedelta(days=5)).isoformat()
            response = core_list_creatives_tool(created_after=created_after, ctx=mock_context)
            assert len(response.creatives) == 2  # Only recent creatives

            # Test created_before filter
            created_before = (now - timedelta(days=5)).isoformat()
            response = core_list_creatives_tool(created_before=created_before, ctx=mock_context)
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
                    agent_url="https://creative.adcontextprotocol.org",
                    format="display_300x250",
                    status="approved",
                    data={"assets": {"main": {"url": "https://example.com/holiday_banner.jpg"}}},
                ),
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id="search_test_video_1",
                    principal_id=self.test_principal_id,
                    name="Holiday Video Ad",
                    agent_url="https://creative.adcontextprotocol.org",
                    format="video_pre_roll",
                    status="approved",
                    data={"assets": {"main": {"url": "https://example.com/holiday_video.mp4"}}},
                ),
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id="search_test_summer_1",
                    principal_id=self.test_principal_id,
                    name="Summer Sale Banner",
                    agent_url="https://creative.adcontextprotocol.org",
                    format="display_728x90",
                    status="approved",
                    data={"assets": {"main": {"url": "https://example.com/summer_banner.jpg"}}},
                ),
            ]
            session.add_all(creatives)
            session.commit()

        with (
            patch("src.core.tools.creatives.get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.tools.creatives.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
            patch(
                "fastmcp.server.dependencies.get_http_headers",
                return_value={
                    "x-adcp-auth": "test-token-123",
                    "host": "creative-test.sales-agent.scope3.com",
                },
            ),
        ):
            # Search for "Holiday"
            response = core_list_creatives_tool(search="Holiday", ctx=mock_context)
            assert len(response.creatives) == 2
            # Check name field (handle both dict and object)
            for c in response.creatives:
                name_val = c.get("name") if isinstance(c, dict) else getattr(c, "name", None)
                assert "Holiday" in name_val

            # Search for "Banner"
            response = core_list_creatives_tool(search="Banner", ctx=mock_context)
            assert len(response.creatives) == 2
            # Check name field (handle both dict and object)
            for c in response.creatives:
                name_val = c.get("name") if isinstance(c, dict) else getattr(c, "name", None)
                assert "Banner" in name_val

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
                    agent_url="https://creative.adcontextprotocol.org",
                    format="display_300x250",
                    status="approved",
                    data={"assets": {"main": {"url": f"https://example.com/creative_{i:02d}.jpg"}}},
                )
                for i in range(25)  # Create 25 creatives
            ]
            session.add_all(creatives)
            session.commit()

        with (
            patch("src.core.tools.creatives.get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.tools.creatives.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
            patch(
                "fastmcp.server.dependencies.get_http_headers",
                return_value={
                    "x-adcp-auth": "test-token-123",
                    "host": "creative-test.sales-agent.scope3.com",
                },
            ),
        ):
            # Test first page
            response = core_list_creatives_tool(page=1, limit=10, ctx=mock_context)
            assert len(response.creatives) == 10
            assert response.query_summary.total_matching == 25
            assert response.query_summary.returned == 10
            assert response.pagination.has_more is True
            assert response.pagination.current_page == 1

            # Test second page
            response = core_list_creatives_tool(page=2, limit=10, ctx=mock_context)
            assert len(response.creatives) == 10
            assert response.query_summary.returned == 10
            assert response.pagination.has_more is True
            assert response.pagination.current_page == 2

            # Test last page
            response = core_list_creatives_tool(page=3, limit=10, ctx=mock_context)
            assert len(response.creatives) == 5
            assert response.query_summary.returned == 5
            assert response.pagination.has_more is False
            assert response.pagination.current_page == 3

            # Test name sorting ascending
            response = core_list_creatives_tool(sort_by="name", sort_order="asc", limit=5, ctx=mock_context)
            creative_names = [c.get("name") if isinstance(c, dict) else c.name for c in response.creatives]
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
                agent_url="https://creative.adcontextprotocol.org",
                format="display_300x250",
                status="approved",
                data={"assets": {"main": {"url": "https://example.com/assigned_1.jpg"}}},
            )
            creative_2 = DBCreative(
                tenant_id=self.test_tenant_id,
                creative_id="assignment_test_2",
                principal_id=self.test_principal_id,
                name="Unassigned Creative",
                agent_url="https://creative.adcontextprotocol.org",
                format="display_300x250",
                status="approved",
                data={"assets": {"main": {"url": "https://example.com/unassigned.jpg"}}},
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
            patch("src.core.tools.creatives.get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.tools.creatives.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
            patch(
                "fastmcp.server.dependencies.get_http_headers",
                return_value={
                    "x-adcp-auth": "test-token-123",
                    "host": "creative-test.sales-agent.scope3.com",
                },
            ),
        ):
            # Filter by media_buy_id - should only return assigned creative
            response = core_list_creatives_tool(media_buy_id=self.test_media_buy_id, ctx=mock_context)
            assert len(response.creatives) == 1
            creative = response.creatives[0]
            creative_id = creative.get("creative_id") if isinstance(creative, dict) else creative.creative_id
            assert creative_id == "assignment_test_1"

            # Filter by buyer_ref - should also work
            response = core_list_creatives_tool(buyer_ref=self.test_buyer_ref, ctx=mock_context)
            assert len(response.creatives) == 1
            creative = response.creatives[0]
            creative_id = creative.get("creative_id") if isinstance(creative, dict) else creative.creative_id
            assert creative_id == "assignment_test_1"

    def test_sync_creatives_authentication_required(self, sample_creatives):
        """Test sync_creatives requires proper authentication."""
        core_sync_creatives_tool, _ = self._import_mcp_tools()
        mock_context = MockContext("invalid-token")

        # Test that invalid auth token fails
        # Authentication errors manifest as various exception types (ToolError, ValueError, etc.)
        from fastmcp.exceptions import ToolError

        with pytest.raises((ToolError, ValueError, RuntimeError)):
            core_sync_creatives_tool(creatives=sample_creatives, ctx=mock_context)

    def test_list_creatives_authentication_optional(self, mock_context):
        """Test list_creatives authentication behavior."""
        from fastmcp.exceptions import ToolError

        _, core_list_creatives_tool = self._import_mcp_tools()

        # Test 1: Invalid token should raise error
        mock_context = MockContext("invalid-token")
        with pytest.raises((ToolError, ValueError, RuntimeError)):
            core_list_creatives_tool(ctx=mock_context)

        # Test 2: No token also requires auth (list_creatives is not anonymous)
        mock_context_no_auth = MockContext(None)
        with pytest.raises((ToolError, ValueError, RuntimeError)):
            core_list_creatives_tool(ctx=mock_context_no_auth)

    def test_sync_creatives_missing_tenant(self, mock_context, sample_creatives):
        """Test sync_creatives when tenant lookup succeeds even with approval_mode provided.

        Note: The function uses get_principal_id_from_context which does its own tenant lookup,
        so providing tenant_id with approval_mode ensures proper creative status handling.
        """
        core_sync_creatives_tool, _ = self._import_mcp_tools()
        with (
            patch("src.core.tools.creatives.get_principal_id_from_context", return_value=self.test_principal_id),
            patch(
                "src.core.tools.creatives.get_current_tenant",
                return_value={"tenant_id": self.test_tenant_id, "approval_mode": "auto-approve"},
            ),
        ):
            # The function works with tenant_id and approval_mode
            response = core_sync_creatives_tool(creatives=sample_creatives, ctx=mock_context)
            assert isinstance(response, SyncCreativesResponse)

    def test_list_creatives_empty_results(self, mock_context):
        """Test list_creatives handles empty results gracefully."""
        _, core_list_creatives_tool = self._import_mcp_tools()
        with (
            patch("src.core.tools.creatives.get_principal_id_from_context", return_value=self.test_principal_id),
            patch("src.core.tools.creatives.get_current_tenant", return_value={"tenant_id": self.test_tenant_id}),
            patch(
                "fastmcp.server.dependencies.get_http_headers",
                return_value={
                    "x-adcp-auth": "test-token-123",
                    "host": "creative-test.sales-agent.scope3.com",
                },
            ),
        ):
            # Query with filters that match nothing
            response = core_list_creatives_tool(status="rejected", ctx=mock_context)  # No rejected creatives exist

            assert len(response.creatives) == 0
            assert response.query_summary.total_matching == 0
            assert response.query_summary.returned == 0
            assert response.pagination.has_more is False

    async def test_create_media_buy_with_creative_ids(self, mock_context, sample_creatives):
        """Test create_media_buy accepts creative_ids in packages."""
        # First, sync creatives to have IDs to reference
        core_sync_creatives_tool, _ = self._import_mcp_tools()
        with (
            patch("src.core.tools.creatives.get_principal_id_from_context", return_value=self.test_principal_id),
            patch(
                "src.core.tools.creatives.get_current_tenant",
                return_value={"tenant_id": self.test_tenant_id, "approval_mode": "require-human"},
            ),
        ):
            sync_response = core_sync_creatives_tool(creatives=sample_creatives, ctx=mock_context)
            assert len(sync_response.creatives) == 3

        # Update creatives in database to have platform_creative_id
        # This simulates that the creatives have already been uploaded to GAM
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative

        with get_db_session() as session:
            for idx, creative_data in enumerate(sample_creatives):
                stmt = select(Creative).where(Creative.creative_id == creative_data["creative_id"])
                creative = session.scalars(stmt).first()
                if creative:
                    # Set platform_creative_id in data JSON to skip upload
                    if not creative.data:
                        creative.data = {}
                    creative.data["platform_creative_id"] = f"gam_creative_{idx + 1}"
                    from sqlalchemy.orm import attributes

                    attributes.flag_modified(creative, "data")
            session.commit()

        # Import create_media_buy tool
        from src.core.schemas import Budget
        from src.core.tools import create_media_buy_raw

        # Create media buy with creative_ids in packages
        creative_ids = [c["creative_id"] for c in sample_creatives]

        with (
            patch("src.core.tools.creatives.get_principal_id_from_context", return_value=self.test_principal_id),
            patch(
                "src.core.tools.creatives.get_current_tenant",
                return_value={"tenant_id": self.test_tenant_id, "approval_mode": "require-human"},
            ),
            patch("src.core.tools.media_buy_create.get_principal_object") as mock_principal,
            patch("src.core.tools.media_buy_create.get_adapter") as mock_adapter,
            patch("src.core.main.get_product_catalog") as mock_catalog,
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch("src.core.format_spec_cache.get_cached_format") as mock_cached_format,
            patch(
                "src.core.tools.media_buy_create._validate_creatives_before_adapter_call"
            ),  # Skip creative validation
        ):
            # Mock principal
            from src.core.schemas import Principal as SchemaPrincipal

            mock_principal.return_value = SchemaPrincipal(
                principal_id=self.test_principal_id,
                name="Test Advertiser",
                platform_mappings={"mock": {"id": "test"}},
            )

            # Mock adapter
            from src.core.schema_adapters import CreateMediaBuyResponse
            from src.core.schemas import Package

            mock_adapter_instance = mock_adapter.return_value
            mock_adapter_instance.create_media_buy.return_value = CreateMediaBuyResponse(
                buyer_ref="test_buyer",
                media_buy_id="test_buy_123",
                packages=[
                    Package(
                        buyer_ref="pkg_1",
                        package_id="pkg_123",
                        product_id="prod_1",
                        status="active",
                        budget=5000.0,  # Package.budget is float, not Budget object
                    )
                ],
            )
            mock_adapter_instance.manual_approval_required = False
            # Mock upload_creatives to return platform creative IDs without validation
            mock_adapter_instance.upload_creatives.return_value = [
                {"creative_id": "creative_display_1", "platform_creative_id": "gam_creative_1"},
                {"creative_id": "creative_video_1", "platform_creative_id": "gam_creative_2"},
                {"creative_id": "creative_display_2", "platform_creative_id": "gam_creative_3"},
            ]

            # Mock get_cached_format to return valid format specs for creative validation
            # This prevents the code from trying to fetch formats from creative agent
            from tests.helpers.adcp_factories import create_test_format

            # Return different formats based on format_id argument
            def mock_get_format(format_id):
                if "video" in format_id:
                    return create_test_format(format_id=format_id, name=f"Video {format_id}", type="video")
                else:
                    return create_test_format(format_id=format_id, name=f"Display {format_id}", type="display")

            mock_cached_format.side_effect = mock_get_format

            # Mock product catalog - use our internal Product schema with implementation_config
            from src.core.schemas import Product as InternalProduct
            from tests.helpers.adcp_factories import create_test_product

            # Create library Product with factory, then convert to our extended Product
            library_product = create_test_product(
                product_id="prod_1",
                name="Test Product",
                description="Test",
                format_ids=["display_300x250"],
                delivery_type="non_guaranteed",
                pricing_options=[
                    {
                        "pricing_option_id": "cpm_usd_auction",
                        "pricing_model": "cpm",
                        "currency": "USD",
                        "is_fixed": False,
                        "price_guidance": {"floor": 5.0, "p50": 10.0, "p75": 12.0, "p90": 15.0},
                    }
                ],
            )

            # Convert to internal Product with implementation_config
            mock_catalog.return_value = [
                InternalProduct(**library_product.model_dump(), implementation_config={"line_item_type": "STANDARD"})
            ]

            # Create packages with creative_ids - use PackageRequest (request schema)
            from src.core.schemas import PackageRequest

            packages = [
                PackageRequest(
                    buyer_ref="pkg_1",
                    product_id="prod_1",
                    pricing_option_id="cpm_usd_auction",  # Required by adcp 2.5.0
                    budget=5000.0,  # Float budget, currency from pricing_option
                    creative_ids=creative_ids,  # Provide creative_ids
                )
            ]

            # Call create_media_buy with packages containing creative_ids
            response = await create_media_buy_raw(
                buyer_ref="test_buyer",
                brand_manifest={"name": "Test Campaign"},
                packages=packages,
                start_time=datetime.now(UTC) + timedelta(days=1),
                end_time=datetime.now(UTC) + timedelta(days=30),
                budget=Budget(total=5000.0, currency="USD"),
                po_number="PO-TEST-123",
                ctx=mock_context,
            )

            # Verify response (domain response doesn't have status field)
            # Note: media_buy_id may be transformed by naming template (e.g., "buy_PO-TEST-123")
            assert response.media_buy_id  # Just verify it exists
            actual_media_buy_id = response.media_buy_id
            # Protocol envelope adds status field - domain response just has media_buy_id

            # Verify creative assignments were created in database
            with get_db_session() as session:
                assignments = session.scalars(
                    select(CreativeAssignment).filter_by(
                        tenant_id=self.test_tenant_id, media_buy_id=actual_media_buy_id
                    )
                ).all()

                # Should have 3 assignments (one per creative)
                assert len(assignments) == 3

                # Verify all creative IDs are assigned
                assigned_creative_ids = {a.creative_id for a in assignments}
                assert assigned_creative_ids == set(creative_ids)
