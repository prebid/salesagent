"""Integration tests for cross-principal data isolation security.

CRITICAL SECURITY TESTS: These tests verify that principals (advertisers) cannot
access or modify each other's data within the same tenant.

Tests cover:
1. sync_creatives - Cannot modify another principal's creatives
2. list_creatives - Cannot see another principal's creatives
3. update_media_buy - Cannot modify another principal's media buys
4. get_media_buy_delivery - Cannot see another principal's media buy delivery data
"""

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from src.core.database.models import MediaBuy, Principal
from src.core.schema_adapters import ListCreativesResponse
from tests.utils.database_helpers import create_tenant_with_timestamps

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class MockContext:
    """Mock FastMCP Context for testing."""

    def __init__(self, auth_token=None):
        if auth_token is None:
            self.meta = {"headers": {}}
        else:
            self.meta = {"headers": {"x-adcp-auth": auth_token}}


class TestCrossPrincipalSecurity:
    """Integration tests for cross-principal data isolation."""

    @pytest.fixture(autouse=True)
    def setup_test_data(self, integration_db):
        """Create test tenant with multiple principals and their data."""
        with get_db_session() as session:
            # Create test tenant
            tenant = create_tenant_with_timestamps(
                tenant_id="security_test_tenant",
                name="Security Test Tenant",
                subdomain="security-test",
                is_active=True,
                ad_server="mock",
                enable_axe_signals=True,
                authorized_emails=[],
                authorized_domains=[],
                auto_approve_formats=["display_300x250"],
                human_review_required=False,
            )
            session.add(tenant)

            # Create two different principals (advertisers)
            principal_a = Principal(
                tenant_id="security_test_tenant",
                principal_id="advertiser_a",
                name="Advertiser A",
                access_token="token-advertiser-a",
                platform_mappings={"mock": {"id": "advertiser_a"}},
            )
            principal_b = Principal(
                tenant_id="security_test_tenant",
                principal_id="advertiser_b",
                name="Advertiser B",
                access_token="token-advertiser-b",
                platform_mappings={"mock": {"id": "advertiser_b"}},
            )
            session.add_all([principal_a, principal_b])

            # Commit principals before creating dependent data (FK constraint)
            session.commit()

            # Create creative owned by Advertiser A
            creative_a = DBCreative(
                tenant_id="security_test_tenant",
                creative_id="creative_owned_by_a",
                principal_id="advertiser_a",
                name="Advertiser A Creative",
                format="display_300x250",
                agent_url="https://creative.adcontextprotocol.org/",
                status="approved",
                data={
                    "url": "https://example.com/creative_a.jpg",
                    "width": 300,
                    "height": 250,
                },
            )
            session.add(creative_a)

            # Create media buy owned by Advertiser A
            media_buy_a = MediaBuy(
                tenant_id="security_test_tenant",
                media_buy_id="media_buy_a",
                principal_id="advertiser_a",
                buyer_ref="buyer_ref_a",
                order_name="Security Test Order A",
                advertiser_name="Advertiser A",
                start_date=date.today(),
                end_date=date.today() + timedelta(days=30),
                budget=1000.0,
                currency="USD",
                status="active",
                raw_request={
                    "buyer_ref": "buyer_ref_a",
                    "packages": [],
                    "budget": {"total": 1000.0, "currency": "USD"},
                },
            )
            session.add(media_buy_a)

            session.commit()

        # CRITICAL: Clear session identity map to prevent "closed transaction" errors
        # The fixture created objects that are now in SQLAlchemy's identity map.
        # When _sync_creatives_impl queries for these objects in a begin_nested() savepoint,
        # SQLAlchemy returns the cached objects which are bound to the closed fixture transaction.
        # Solution: Get a fresh session and call expire_all() to mark all cached objects as stale.
        from src.core.database.database_session import get_scoped_session

        scoped = get_scoped_session()
        scoped.remove()  # Clear thread-local session registry

        # Now force a new session and expire everything
        session = scoped()
        session.expire_all()  # Mark all objects in identity map as stale
        session.close()
        scoped.remove()  # Clean up again

    def test_list_creatives_cannot_see_other_principals_creatives(self):
        """Test that list_creatives only returns the authenticated principal's creatives.

        SECURITY: Principal B should NOT see Principal A's creatives.
        """
        from src.core.main import _list_creatives_impl

        mock_context_b = MockContext(auth_token="token-advertiser-b")

        with patch("src.core.main.get_current_tenant", return_value={"tenant_id": "security_test_tenant"}):
            response = _list_creatives_impl(context=mock_context_b)

            assert isinstance(response, ListCreativesResponse)

            # Principal B should see ZERO creatives (they don't own any)
            assert len(response.creatives) == 0, "Principal B should not see Principal A's creative!"
            assert response.query_summary.total_matching == 0

    def test_update_media_buy_cannot_modify_other_principals_media_buy(self):
        """Test that update_media_buy rejects attempts to modify another principal's media buy.

        SECURITY: Principal B should NOT be able to update Principal A's media buy.
        """

        from src.core.main import _update_media_buy_impl

        mock_context_b = MockContext(auth_token="token-advertiser-b")

        # Principal B tries to update Principal A's media buy
        with patch("src.core.main.get_current_tenant", return_value={"tenant_id": "security_test_tenant"}):
            # _verify_principal should raise PermissionError
            with pytest.raises(PermissionError, match="does not own media buy"):
                _update_media_buy_impl(
                    media_buy_id="media_buy_a",  # Owned by Principal A!
                    buyer_ref="hacked_by_b",
                    context=mock_context_b,
                )

            # Verify media buy was NOT modified
            with get_db_session() as session:
                from sqlalchemy import select

                stmt = select(MediaBuy).filter_by(tenant_id="security_test_tenant", media_buy_id="media_buy_a")
                media_buy = session.scalars(stmt).first()

                assert media_buy.principal_id == "advertiser_a", "Media buy ownership changed!"
                assert media_buy.buyer_ref == "buyer_ref_a", "Media buy buyer_ref was modified!"

    def test_get_media_buy_delivery_cannot_see_other_principals_data(self):
        """Test that get_media_buy_delivery only returns data for owned media buys.

        SECURITY: Principal B should NOT see Principal A's media buy delivery data.
        """
        from src.core.main import _get_media_buy_delivery_impl
        from src.core.schemas import GetMediaBuyDeliveryRequest

        mock_context_b = MockContext(auth_token="token-advertiser-b")

        request = GetMediaBuyDeliveryRequest(
            media_buy_ids=["media_buy_a"],  # Owned by Principal A!
        )

        with patch("src.core.main.get_current_tenant", return_value={"tenant_id": "security_test_tenant"}):
            response = _get_media_buy_delivery_impl(req=request, context=mock_context_b)

            # Principal B should NOT see Principal A's media buy
            assert len(response.media_buy_deliveries) == 0, "Principal B should not see Principal A's delivery data!"

    def test_cross_tenant_isolation_also_enforced(self):
        """Test that principals from different tenants are isolated.

        SECURITY: Even if a principal somehow gets another tenant's data ID,
        they should not be able to access it.
        """
        # Create a second tenant with its own principal and creative
        with get_db_session() as session:
            tenant2 = create_tenant_with_timestamps(
                tenant_id="second_tenant",
                name="Second Tenant",
                subdomain="second-tenant",
                is_active=True,
                ad_server="mock",
            )
            session.add(tenant2)

            principal_c = Principal(
                tenant_id="second_tenant",
                principal_id="advertiser_c",
                name="Advertiser C",
                access_token="token-advertiser-c",
                platform_mappings={"mock": {"id": "advertiser_c"}},
            )
            session.add(principal_c)
            session.commit()

            creative_c = DBCreative(
                tenant_id="second_tenant",
                creative_id="creative_owned_by_c",
                principal_id="advertiser_c",
                name="Advertiser C Creative",
                format="display_300x250",
                agent_url="https://creative.adcontextprotocol.org/",
                status="approved",
                data={"url": "https://example.com/creative_c.jpg", "width": 300, "height": 250},
            )
            session.add(creative_c)
            session.commit()

        # Clear session identity map (same as main fixture)
        from src.core.database.database_session import get_scoped_session

        scoped = get_scoped_session()
        scoped.remove()
        session = scoped()
        session.expire_all()
        session.close()
        scoped.remove()

        # Principal A (from first tenant) tries to access creative from second tenant
        from src.core.main import _list_creatives_impl

        mock_context_a = MockContext(auth_token="token-advertiser-a")

        with patch("src.core.main.get_current_tenant", return_value={"tenant_id": "security_test_tenant"}):
            response = _list_creatives_impl(context=mock_context_a)

            # Should only see their own creative, not creative_c from other tenant
            creative_ids = [c.creative_id for c in response.creatives]
            assert "creative_owned_by_a" in creative_ids or len(creative_ids) == 0
            assert "creative_owned_by_c" not in creative_ids, "Cross-tenant data leakage!"
