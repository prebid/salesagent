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
from uuid import uuid4

import pytest
from adcp.types import AccountReference, CreativeFilters
from adcp.types.generated_poc.creative.sync_creatives_request import Assignment
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
from src.core.exceptions import AdCPAuthenticationError, AdCPAuthRequiredError
from src.core.schemas import CreateMediaBuyRequest, ListCreativesResponse, SyncCreativesRequest, SyncCreativesResponse
from src.core.schemas.creative import ListCreativesRequest
from tests.factories import PricingOptionFactory
from tests.factories.creative_asset import build_assets, image_spec
from tests.factories.principal import plaintext_token_for
from tests.helpers.credentials import credential_headers
from tests.helpers.envelope_assertions import raises_adcp
from tests.utils.database_helpers import create_tenant_with_timestamps, get_utc_now

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


ACCOUNT_ID = "acct_lifecycle"


def _seed_account(tenant_id: str, principal_ids: tuple[str, ...]) -> None:
    """Add the Account sync-creatives-request.json requires, and grant these principals access.

    Principal ids are passed in rather than queried: reading them back would need a session
    in a test body, which test_architecture_repository_pattern.py forbids -- and the caller
    already knows them, having just created them.
    """
    from tests.factories import AccountFactory, AgentAccountAccessFactory

    AccountFactory(tenant_id=tenant_id, account_id=ACCOUNT_ID)
    for pid in principal_ids:
        AgentAccountAccessFactory(tenant_id=tenant_id, principal_id=pid, account_id=ACCOUNT_ID)


def _list_creatives(**kwargs):
    """Build the request from its fields, then dispatch it at the boundary.

    ``format`` and ``page`` are ListCreativesRequest fields that no transport puts on the
    wire, so they are set on the built model the way an internal caller would.
    """
    import asyncio

    from src.core.resolved_identity import TransportProtocol
    from src.core.tools._boundary import invoke_tool

    headers = kwargs.pop("headers")
    kwargs.pop("ctx", None)
    internal = {name: kwargs.pop(name) for name in ("format", "page") if name in kwargs}
    req = ListCreativesRequest(**kwargs)
    if internal:
        req = req.model_copy(update=internal)
    return asyncio.run(invoke_tool("list_creatives", req, headers, TransportProtocol.MCP))


def _sync_creatives(**kwargs):
    """Build a SyncCreativesRequest from flat fields, then dispatch it at the boundary.

    ``invoke_tool`` is the path every transport takes -- identity resolution, account
    resolution and the idempotency probe included -- so a call site here reaches production
    the way a buyer does. It takes the request's HEADERS, never an identity: the resolver
    is their one reader. This module's call sites stay flat.
    """
    import asyncio

    from src.core.resolved_identity import TransportProtocol
    from src.core.tools._boundary import invoke_tool

    headers = kwargs.pop("headers")
    kwargs.pop("ctx", None)
    return asyncio.run(invoke_tool("sync_creatives", SyncCreativesRequest(**kwargs), headers, TransportProtocol.MCP))


# (Deleted) MockContext stood in for a FastMCP Context so a test could hand one to a tool.
# Nothing takes a Context: a transport hands the boundary the request's headers, and the
# tests here present theirs through ``credential_headers``.


@pytest.mark.requires_db
class TestCreativeLifecycleMCP:
    """Integration tests for creative lifecycle MCP tools."""

    def _import_mcp_tools(self):
        """Import MCP tools to avoid module-level database initialization."""
        return _sync_creatives, _list_creatives

    def _headers(self, tenant_id=None, principal_id=None):
        """The headers a call arrives with, for the tenant and principal this file seeds.

        The boundary takes headers and resolves the caller itself, so a test states the
        credential and the seller and nothing else -- the tenant it reads is the ROW this
        fixture wrote (``approval_mode="auto-approve"``), not an in-memory override.
        """
        return credential_headers(
            token=plaintext_token_for(principal_id or self.test_principal_id),
            tenant=tenant_id or self.test_tenant_id,
        )

    @pytest.fixture(autouse=True)
    def mock_format_registry(self):
        """Mock creative agent format registry to avoid real API calls."""
        from tests.helpers.adcp_factories import create_test_format

        # Mock formats that tests use - create with factory
        mock_formats = {
            "display_300x250_image": create_test_format(
                format_id="display_300x250_image",
                name="Display 300x250 Image",
                type="display",
                description="Standard display banner",
            ),
            "display_728x90_image": create_test_format(
                format_id="display_728x90_image",
                name="Display 728x90 Image",
                type="display",
                description="Leaderboard banner",
            ),
            "video_instream_15s": create_test_format(
                format_id="video_instream_15s",
                name="Video Instream 15s",
                type="video",
                description="15 second instream video",
            ),
        }

        with patch("src.core.creative_agent_registry.CreativeAgentRegistry.get_format") as mock_get:
            # Mock get_format to return format from our dict
            def get_format_side_effect(agent_url, format_id, **_kwargs):
                return mock_formats.get(format_id)

            mock_get.side_effect = get_format_side_effect
            yield mock_get

    @pytest.fixture(autouse=True)
    def setup_test_data(self, integration_db, bound_factory_session):
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
                auto_approve_format_ids=["display_300x250_image", "display_728x90_image"],
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
            principal = Principal.with_token(
                plaintext_token_for("test_advertiser"),
                tenant_id="creative_test",
                principal_id="test_advertiser",
                name="Test Advertiser",
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
                raw_request={
                    "test": True,
                    "packages": [
                        {"package_id": "package_1", "paused": False},  # adcp 2.12.0+
                        {"package_id": "package_2", "paused": False},  # adcp 2.12.0+
                        {"package_id": "package_buyer_ref", "paused": False},  # adcp 2.12.0+
                    ],
                },
            )
            session.add(media_buy)
            session.commit()  # Commit media_buy first so foreign key exists

            # Create test media packages for creative assignments
            from src.core.database.models import MediaPackage
            from src.core.database.models import Product as DBProduct

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

            # Create test product for create_media_buy tests
            # (create_media_buy queries DB directly, not get_product_catalog)
            # Include all formats from sample_creatives fixture
            test_product = DBProduct(
                tenant_id="creative_test",
                product_id="prod_1",
                name="Test Product",
                description="Test product for creative lifecycle test",
                format_ids=[
                    {"agent_url": "https://test.com", "id": "display_300x250_image"},
                    {"agent_url": "https://test.com", "id": "video_instream_15s"},
                    {"agent_url": "https://test.com", "id": "display_728x90_image"},
                ],
                targeting_template={},
                delivery_type="non_guaranteed",
                properties=[{"publisher_domain": "test.com", "selection_type": "all"}],
            )
            session.add(test_product)

            # Create pricing option for the product
            pricing_option = PricingOptionFactory.build(
                tenant_id="creative_test",
                product_id="prod_1",
                pricing_model="cpm",
                currency="USD",
                is_fixed=False,
                price_guidance={"floor": 5.0, "p50": 10.0, "p75": 12.0, "p90": 15.0},
            )
            session.add(pricing_option)
            session.commit()  # Commit media_packages and product

        # Store test data for easy access
        self.test_tenant_id = "creative_test"
        self.test_principal_id = "test_advertiser"
        self.test_media_buy_id = "test_media_buy_1"

        # ``account`` is in sync-creatives-request.json /required, so every sync call in
        # this file needs one the calling principal can reach. Factories, per CLAUDE.md.
        _seed_account("creative_test", ("test_advertiser",))

    @pytest.fixture
    def sample_creatives(self):
        """Sample creative data for testing.

        NOTE: Uses structured format objects with agent_url to avoid deprecated string format_ids.
        Available formats from creative agent: display_300x250_image, display_728x90_image, etc.
        Uses "https://test.com" as agent_url to match test products in setup_test_data.
        """
        return [
            {
                "creative_id": "creative_display_1",
                "name": "Banner Ad 300x250",
                "format_id": {"agent_url": "https://test.com", "id": "display_300x250_image"},
                "assets": build_assets(image_spec("banner")),
            },
            {
                "creative_id": "creative_video_1",
                "name": "Video Ad 30sec",
                "format_id": {"agent_url": "https://test.com", "id": "video_instream_15s"},
                "assets": build_assets(image_spec("banner")),
            },
            {
                "creative_id": "creative_display_2",
                "name": "Leaderboard Ad 728x90",
                "format_id": {"agent_url": "https://test.com", "id": "display_728x90_image"},
                "assets": build_assets(image_spec("banner")),
            },
        ]

    def test_sync_creatives_create_new_creatives(self, sample_creatives):
        """Test sync_creatives creates new creatives successfully."""
        core_sync_creatives_tool, _ = self._import_mcp_tools()

        # The seeded tenant row is already approval_mode="auto-approve", and the resolver
        # reads that row -- an in-memory override would not reach production anyway.
        headers = self._headers()

        # Call sync_creatives tool (uses default patch=False for full upsert)
        response = core_sync_creatives_tool(
            creatives=sample_creatives,
            # A FRESH key per call, as sync-creatives-request.json directs ("Use a fresh
            # UUID v4 for each request"). These were derived from the creative_id or
            # hardcoded, so a test syncing the same creative twice with different content
            # reused one key across two payloads -- correctly an IDEMPOTENCY_CONFLICT now
            # that sync_creatives honours the key.
            idempotency_key=f"sync-{uuid4().hex}",
            account=AccountReference(root={"account_id": ACCOUNT_ID}),
            headers=headers,
        )

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
            display_creative = next((c for c in db_creatives if c.format == "display_300x250_image"), None)
            assert display_creative is not None
            assert display_creative.name == "Banner Ad 300x250"
            # url/width/height were PRE-3.x top-level fields. 3.1.1 carries the media
            # reference inside ``assets``, so what is persisted is the assets block --
            # asserting the old flat keys graded a shape the schema no longer has.
            assert display_creative.status == "approved"  # Auto-approved due to approval_mode setting

            # Verify video creative
            video_creative = next((c for c in db_creatives if c.format == "video_instream_15s"), None)
            assert video_creative is not None

            # Verify leaderboard creative
            leaderboard_creative = next((c for c in db_creatives if c.format == "display_728x90_image"), None)
            assert leaderboard_creative is not None

    def test_sync_creatives_upsert_existing_creative(self):
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
                format="display_300x250_image",
                status="pending",
                data={
                    "assets": build_assets(image_spec("banner")),
                },
            )
            session.add(existing_creative)
            session.commit()

        # Now sync with updated data
        updated_creative_data = [
            {
                "creative_id": "creative_update_test",
                "name": "Updated Creative Name",
                "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"},
                "assets": build_assets(image_spec("banner")),
            }
        ]

        headers = self._headers()

        # Upsert with patch=False (default): full replacement
        response = core_sync_creatives_tool(
            creatives=updated_creative_data,
            idempotency_key=f"sync-{uuid4().hex}",
            account=AccountReference(root={"account_id": ACCOUNT_ID}),
            headers=headers,
        )

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
            assert updated_creative.updated_at is not None

    def test_sync_creatives_with_package_assignments(self, sample_creatives):
        """Test sync_creatives assigns creatives to packages using spec-compliant assignments dict."""
        core_sync_creatives_tool, _ = self._import_mcp_tools()

        # Get the creative_id from the first sample creative
        creative_data = sample_creatives[:1]
        creative_id = creative_data[0]["creative_id"]

        headers = self._headers()

        # Use spec-compliant assignments dict: creative_id -> package_ids
        response = core_sync_creatives_tool(
            creatives=creative_data,
            assignments=[
                Assignment(creative_id=creative_id, package_id="package_1"),
                Assignment(creative_id=creative_id, package_id="package_2"),
            ],
            # Both are in sync-creatives-request.json /required.
            idempotency_key=f"sync-{uuid4().hex}",
            account=AccountReference(root={"account_id": ACCOUNT_ID}),
            headers=headers,
        )

        # Verify response structure
        assert isinstance(response, SyncCreativesResponse)
        assert len(response.creatives) > 0

        # Verify database assignments (assignments are separate from creatives list)
        with get_db_session() as session:
            assignments = session.scalars(
                select(CreativeAssignment).filter_by(tenant_id=self.test_tenant_id, media_buy_id=self.test_media_buy_id)
            ).all()

            assert len(assignments) == 2
            package_ids = [a.package_id for a in assignments]
            assert "package_1" in package_ids
            assert "package_2" in package_ids

    def test_sync_creatives_with_assignments_lookup(self, sample_creatives):
        """Test sync_creatives with assignments dict (spec-compliant approach)."""
        core_sync_creatives_tool, _ = self._import_mcp_tools()

        # Get the creative_id from the first sample creative
        creative_data = sample_creatives[:1]
        creative_id = creative_data[0]["creative_id"]

        headers = self._headers()

        # Use spec-compliant assignments dict
        response = core_sync_creatives_tool(
            creatives=creative_data,
            assignments=[Assignment(creative_id=creative_id, package_id="package_buyer_ref")],
            # Both are in sync-creatives-request.json /required.
            idempotency_key=f"sync-{uuid4().hex}",
            account=AccountReference(root={"account_id": ACCOUNT_ID}),
            headers=headers,
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

    def test_sync_creatives_validation_failures(self):
        """Test sync_creatives handles validation failures gracefully."""
        core_sync_creatives_tool, _ = self._import_mcp_tools()
        invalid_creatives = [
            {
                "creative_id": "valid_creative",
                "name": "Valid Creative",
                "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"},
                "assets": build_assets(image_spec("banner")),
            },
            {
                "creative_id": "invalid_creative",
                "name": "",  # Invalid: empty name -- a BUSINESS failure, so the entry is
                # otherwise schema-valid on purpose. A schema-invalid entry (e.g. no assets)
                # would reject the whole request, which is correct but grades something else:
                # partial success is for per-creative business failures.
                "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"},
                "assets": build_assets(image_spec("banner")),
            },
        ]

        headers = self._headers()

        response = core_sync_creatives_tool(
            creatives=invalid_creatives,
            idempotency_key=f"sync-{uuid4().hex}",
            account=AccountReference(root={"account_id": ACCOUNT_ID}),
            headers=headers,
        )

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
            if action in ("created",):
                created_count += 1
            elif action in ("failed",):
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

    def test_list_creatives_no_filters(self):
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
                    format="display_300x250_image",
                    status="approved" if i % 2 == 0 else "pending_review",
                    data={
                        "url": f"https://example.com/creative_{i}.jpg",
                        "assets": build_assets(image_spec("banner")),
                    },
                )
                for i in range(5)
            ]
            session.add_all(creatives)
            session.commit()

        headers = self._headers()

        response = core_list_creatives_tool(headers=headers)

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

    def test_list_creatives_with_status_filter(self):
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
                    format="display_300x250_image",
                    status="approved",
                    data={"assets": build_assets(image_spec("banner"))},
                )
                for i in range(3)
            ] + [
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id=f"status_test_pending_{i}",
                    principal_id=self.test_principal_id,
                    name=f"Pending Creative {i}",
                    agent_url="https://creative.adcontextprotocol.org",
                    format="display_728x90_image",
                    status="pending_review",
                    data={"assets": build_assets(image_spec("banner"))},
                )
                for i in range(2)
            ]
            session.add_all(creatives)
            session.commit()

        headers = self._headers()

        # Test approved filter
        response = core_list_creatives_tool(filters=CreativeFilters(statuses=["approved"]), headers=headers)
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
        response = core_list_creatives_tool(filters=CreativeFilters(statuses=["pending_review"]), headers=headers)
        assert len(response.creatives) == 2
        # Check status field (handle both dict, object, and enum)
        for c in response.creatives:
            status_val = c.get("status") if isinstance(c, dict) else getattr(c, "status", None)
            # Handle enum values - get the string value
            from enum import Enum

            if isinstance(status_val, Enum):
                status_val = status_val.value
            assert status_val == "pending_review"

    def test_list_creatives_with_date_filters(self):
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
                    format="display_300x250_image",
                    status="approved",
                    created_at=now - timedelta(days=10 + i),  # 10+ days ago
                    data={"assets": build_assets(image_spec("banner"))},
                )
                for i in range(2)
            ] + [
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id=f"date_test_recent_{i}",
                    principal_id=self.test_principal_id,
                    name=f"Recent Creative {i}",
                    agent_url="https://creative.adcontextprotocol.org",
                    format="display_300x250_image",
                    status="approved",
                    created_at=now - timedelta(days=2 + i),  # 2-3 days ago
                    data={"assets": build_assets(image_spec("banner"))},
                )
                for i in range(2)
            ]
            session.add_all(creatives)
            session.commit()

        headers = self._headers()

        # Test created_after filter. AdCP 3.1.1 puts both dates inside `filters`
        # (core/creative-filters.json) as real date-times; the flat ISO-string aliases the
        # builder used to parse are gone.
        cutoff = now - timedelta(days=5)
        response = core_list_creatives_tool(filters=CreativeFilters(created_after=cutoff), headers=headers)
        assert len(response.creatives) == 2  # Only recent creatives

        # Test created_before filter
        response = core_list_creatives_tool(filters=CreativeFilters(created_before=cutoff), headers=headers)
        assert len(response.creatives) == 2  # Only old creatives

    def test_list_creatives_with_search(self):
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
                    format="display_300x250_image",
                    status="approved",
                    data={"assets": build_assets(image_spec("banner"))},
                ),
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id="search_test_video_1",
                    principal_id=self.test_principal_id,
                    name="Holiday Video Ad",
                    agent_url="https://creative.adcontextprotocol.org",
                    format="video_instream_15s",
                    status="approved",
                    data={"assets": build_assets(image_spec("banner"))},
                ),
                DBCreative(
                    tenant_id=self.test_tenant_id,
                    creative_id="search_test_summer_1",
                    principal_id=self.test_principal_id,
                    name="Summer Sale Banner",
                    agent_url="https://creative.adcontextprotocol.org",
                    format="display_728x90_image",
                    status="approved",
                    data={"assets": build_assets(image_spec("banner"))},
                ),
            ]
            session.add_all(creatives)
            session.commit()

        headers = self._headers()

        # Search for "Holiday"
        response = core_list_creatives_tool(filters=CreativeFilters(name_contains="Holiday"), headers=headers)
        assert len(response.creatives) == 2
        # Check name field (handle both dict and object)
        for c in response.creatives:
            name_val = c.get("name") if isinstance(c, dict) else getattr(c, "name", None)
            assert "Holiday" in name_val

        # Search for "Banner"
        response = core_list_creatives_tool(filters=CreativeFilters(name_contains="Banner"), headers=headers)
        assert len(response.creatives) == 2
        # Check name field (handle both dict and object)
        for c in response.creatives:
            name_val = c.get("name") if isinstance(c, dict) else getattr(c, "name", None)
            assert "Banner" in name_val

    def test_list_creatives_with_media_buy_assignments(self):
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
                format="display_300x250_image",
                status="approved",
                data={"assets": build_assets(image_spec("banner"))},
            )
            creative_2 = DBCreative(
                tenant_id=self.test_tenant_id,
                creative_id="assignment_test_2",
                principal_id=self.test_principal_id,
                name="Unassigned Creative",
                agent_url="https://creative.adcontextprotocol.org",
                format="display_300x250_image",
                status="approved",
                data={"assets": build_assets(image_spec("banner"))},
            )
            session.add_all([creative_1, creative_2])

            # Create assignment for only one creative
            assignment = CreativeAssignment(
                tenant_id=self.test_tenant_id,
                principal_id=self.test_principal_id,
                assignment_id=str(uuid.uuid4()),
                creative_id="assignment_test_1",
                media_buy_id=self.test_media_buy_id,
                package_id="test_package",
                weight=100,
            )
            session.add(assignment)
            session.commit()

        headers = self._headers()

        # Filter by media_buy_id - should only return assigned creative
        response = core_list_creatives_tool(
            filters=CreativeFilters(media_buy_ids=[self.test_media_buy_id]), headers=headers
        )
        assert len(response.creatives) == 1
        creative = response.creatives[0]
        creative_id = creative.get("creative_id") if isinstance(creative, dict) else creative.creative_id
        assert creative_id == "assignment_test_1"

    def test_sync_creatives_authentication_required(self, sample_creatives):
        """Test sync_creatives requires proper authentication."""
        core_sync_creatives_tool, _ = self._import_mcp_tools()

        # A credential that resolves to no principal. The boundary answers with the wire
        # refusal and raises AdcpFailure carrying it, so the CODE is what this grades --
        # presented-but-rejected is AUTH_INVALID (v3.1.1 error-code.json).
        with raises_adcp(AdCPAuthenticationError):
            core_sync_creatives_tool(
                creatives=sample_creatives,
                idempotency_key=f"sync-{uuid4().hex}",
                account=AccountReference(root={"account_id": ACCOUNT_ID}),
                headers=credential_headers(token="invalid-token", tenant=self.test_tenant_id),
            )

    def test_list_creatives_authentication_required(self):
        """list_creatives refuses a rejected credential, and refuses none at all."""
        _, core_list_creatives_tool = self._import_mcp_tools()

        # Presented and rejected -> AUTH_INVALID.
        with raises_adcp(AdCPAuthenticationError):
            core_list_creatives_tool(headers=credential_headers(token="invalid-token", tenant=self.test_tenant_id))

        # None presented -> AUTH_MISSING: list_creatives is not a public tool.
        with raises_adcp(AdCPAuthRequiredError):
            core_list_creatives_tool(headers=credential_headers(tenant=self.test_tenant_id))

    def test_sync_creatives_missing_tenant(self, sample_creatives):
        """Test sync_creatives when tenant lookup succeeds even with approval_mode provided.

        Note: The function uses identity.principal_id and identity.tenant for auth/tenant context,
        so providing tenant_id with approval_mode ensures proper creative status handling.
        """
        core_sync_creatives_tool, _ = self._import_mcp_tools()

        # The seeded tenant row is already approval_mode="auto-approve", and the resolver
        # reads that row -- an in-memory override would not reach production anyway.
        headers = self._headers()

        # The function works with tenant_id and approval_mode
        response = core_sync_creatives_tool(
            creatives=sample_creatives,
            idempotency_key=f"sync-{uuid4().hex}",
            account=AccountReference(root={"account_id": ACCOUNT_ID}),
            headers=headers,
        )
        assert isinstance(response, SyncCreativesResponse)

    def test_list_creatives_empty_results(self):
        """Test list_creatives handles empty results gracefully."""
        _, core_list_creatives_tool = self._import_mcp_tools()

        headers = self._headers()

        # Query with filters that match nothing
        response = core_list_creatives_tool(
            filters=CreativeFilters(statuses=["rejected"]), headers=headers
        )  # none exist

        assert len(response.creatives) == 0
        assert response.query_summary.total_matching == 0
        assert response.query_summary.returned == 0
        assert response.pagination.has_more is False

    # The missing-media-URL gate (now VALIDATION_ERROR per 3.1.1 enums/error-code.json) is
    # graded by TestCreativeMissingUrl in test_create_media_buy_behavioral.py and by the
    # @T-UC-002-partition-creative-asset row missing_required_assets; the copy that stood
    # here still expected the retired CREATIVE_REJECTED and was deleted.

    async def test_create_media_buy_with_creative_ids(self, sample_creatives):
        """Test create_media_buy accepts creative_ids in packages."""
        # First, sync creatives to have IDs to reference. Awaited directly rather than
        # through this module's ``_sync_creatives`` seam: that seam wraps the boundary in
        # asyncio.run for its many SYNC callers, and this is the one async test -- a second
        # loop inside the running one raises.
        from src.core.resolved_identity import TransportProtocol
        from src.core.tools._boundary import invoke_tool

        headers = self._headers()
        sync_response = await invoke_tool(
            "sync_creatives",
            SyncCreativesRequest(
                creatives=sample_creatives,
                idempotency_key=f"sync-{uuid4().hex}",
                account=AccountReference(root={"account_id": ACCOUNT_ID}),
            ),
            headers,
            TransportProtocol.MCP,
        )
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

        # Note: Product and PricingOption are created in setup_test_data fixture

        from src.core.tools._boundary import invoke_tool

        # Create media buy with creative_ids in packages
        creative_ids = [c["creative_id"] for c in sample_creatives]

        with (
            patch("src.core.tools.media_buy_create.get_adapter") as mock_adapter,
            patch("src.core.tools.products.get_product_catalog") as mock_catalog,
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch(
                "src.core.tools.media_buy_create._validate_creatives_before_adapter_call"
            ),  # Skip creative validation
        ):
            # No principal patch: the resolver loads the seeded row from the credential in
            # ``headers``, so the caller is the real principal this fixture created.

            # Mock adapter. create_media_buy returns AdapterCreateResult -- the carrier
            # the tool reads and never serializes (src/adapters/base.py) -- not the
            # buyer's CreateMediaBuySuccess, which the tool builds from the written row.
            from src.adapters.base import AdapterCreateResult
            from src.core.schemas import Package

            mock_adapter_instance = mock_adapter.return_value
            mock_adapter_instance.get_supported_pricing_models.return_value = {"cpm", "vcpm", "cpc", "flat_rate"}
            mock_adapter_instance.validate_media_buy_request.return_value = []
            mock_adapter_instance.create_media_buy.return_value = AdapterCreateResult(
                media_buy_id="test_buy_123",
                packages=[
                    Package(
                        package_id="pkg_123",
                        product_id="prod_1",
                        paused=False,  # adcp 2.12.0+: replaced 'status' with 'paused'
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

            # Format validation is now handled by mock_format_registry fixture
            # (see conftest.py - mocks CreativeAgentRegistry.get_format())

            # Mock product catalog - use our internal Product schema with implementation_config
            from src.core.schemas import Product as InternalProduct
            from tests.helpers.adcp_factories import create_test_product

            # Create library Product with factory, then convert to our extended Product
            library_product = create_test_product(
                product_id="prod_1",
                name="Test Product",
                description="Test",
                format_ids=["display_300x250_image"],
                delivery_type="non_guaranteed",
                pricing_options=[
                    {
                        # V3 shape. Was the pre-V3 form -- "is_fixed": False plus
                        # price_guidance.floor -- which the pinned schema replaced:
                        # auction pricing carries floor_price at option level, and
                        # PriceGuidance declares only p25/p50/p75/p90. It survived
                        # because the SDK DTO's extra="allow" accepted both keys
                        # silently; our own members forbid them, which is the point.
                        "pricing_option_id": "cpm_usd_auction",
                        "pricing_model": "cpm",
                        "currency": "USD",
                        "floor_price": 5.0,
                        "price_guidance": {"p50": 10.0, "p75": 12.0, "p90": 15.0},
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
                    product_id="prod_1",
                    pricing_option_id="cpm_usd_auction",  # Required by adcp 2.5.0
                    budget=5000.0,  # Float budget, currency from pricing_option
                    bid_price=10.0,  # Required for auction pricing (floor=5.0, p50=10.0)
                    creative_ids=creative_ids,  # Provide creative_ids
                )
            ]

            # Call create_media_buy with packages containing creative_ids, through the
            # boundary every transport enters.
            response = await invoke_tool(
                "create_media_buy",
                CreateMediaBuyRequest(
                    # This module seeds ACCOUNT_ID, not the suite default; the boundary
                    # resolves the reference, so it has to name the row this file created.
                    account={"account_id": ACCOUNT_ID},
                    brand={"domain": "testbrand.com"},
                    packages=packages,
                    start_time=datetime.now(UTC) + timedelta(days=1),
                    end_time=datetime.now(UTC) + timedelta(days=30),
                    po_number="PO-TEST-123",
                    idempotency_key=f"sync-{uuid4().hex}",
                ),
                headers,
                TransportProtocol.MCP,
            )

            # create_media_buy returns the oneOf BRANCH itself; the protocol status is a
            # field on it, not a second element of a pair.
            domain_response = response
            print(f"DEBUG create_media_buy response: {domain_response}")
            if hasattr(domain_response, "errors") and domain_response.errors:
                print(f"DEBUG errors: {domain_response.errors}")
            msg = f"Expected CreateMediaBuySuccess, got: {type(domain_response).__name__}"
            assert hasattr(domain_response, "media_buy_id"), msg
            assert domain_response.media_buy_id  # Just verify it exists
            actual_media_buy_id = domain_response.media_buy_id
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
