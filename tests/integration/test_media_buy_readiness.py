"""Tests for MediaBuyReadinessService."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from src.admin.services.media_buy_readiness_service import MediaBuyReadinessService
from src.core.database.database_session import get_db_session
from src.core.database.models import Creative, CreativeAssignment, MediaBuy, Principal, Tenant


@pytest.fixture
def test_tenant(integration_db, request):
    """Create a test tenant (requires integration_db fixture)."""
    tenant_id = f"test_readiness_{request.node.name[-20:]}"  # Truncate to avoid long names
    with get_db_session() as session:
        tenant = Tenant(tenant_id=tenant_id, name="Test Tenant", subdomain="test", is_active=True, ad_server="mock")
        session.add(tenant)
        session.commit()

    yield tenant_id

    # Cleanup
    with get_db_session() as session:
        session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))
        session.commit()


@pytest.fixture
def test_principal(integration_db, test_tenant):
    """Create a test principal (requires integration_db and test_tenant fixtures)."""
    principal_id = "test_principal"
    with get_db_session() as session:
        principal = Principal(
            tenant_id=test_tenant,
            principal_id=principal_id,
            name="Test Advertiser",
            access_token="test_token",
            platform_mappings={"mock": {"advertiser_id": "test_adv_123"}},  # Required field with valid mapping
        )
        session.add(principal)
        session.commit()

    yield principal_id

    # Cleanup
    with get_db_session() as session:
        session.execute(
            delete(Principal).where(Principal.tenant_id == test_tenant, Principal.principal_id == principal_id)
        )
        session.commit()


class TestMediaBuyReadinessService:
    """Test readiness state computation."""

    def test_draft_state_no_packages(self, test_tenant, test_principal):
        """Media buy with no packages should be 'draft'."""
        media_buy_id = "mb_draft"
        now = datetime.now(UTC)

        with get_db_session() as session:
            # Create media buy with no packages
            media_buy = MediaBuy(
                media_buy_id=media_buy_id,
                tenant_id=test_tenant,
                principal_id=test_principal,
                order_name="Draft Order",
                advertiser_name="Test Advertiser",
                budget=1000.0,
                start_date=(now + timedelta(days=1)).date(),
                end_date=(now + timedelta(days=7)).date(),
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=7),
                status="active",
                raw_request={"packages": []},  # No packages
            )
            session.add(media_buy)
            session.commit()

        # Check readiness
        readiness = MediaBuyReadinessService.get_readiness_state(media_buy_id, test_tenant)
        assert readiness["state"] == "draft"
        assert not readiness["is_ready_to_activate"]
        assert readiness["packages_total"] == 0

        # Cleanup
        with get_db_session() as session:
            session.execute(delete(MediaBuy).where(MediaBuy.media_buy_id == media_buy_id))
            session.commit()

    def test_needs_creatives_state(self, test_tenant, test_principal):
        """Media buy with packages but no creatives should be 'needs_creatives'."""
        media_buy_id = "mb_needs_creatives"
        now = datetime.now(UTC)

        with get_db_session() as session:
            # Create media buy with packages but no creative assignments
            media_buy = MediaBuy(
                media_buy_id=media_buy_id,
                tenant_id=test_tenant,
                principal_id=test_principal,
                order_name="Needs Creatives Order",
                advertiser_name="Test Advertiser",
                budget=1000.0,
                start_date=(now + timedelta(days=1)).date(),
                end_date=(now + timedelta(days=7)).date(),
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=7),
                status="active",
                raw_request={"packages": [{"package_id": "pkg_1", "product_id": "prod_1"}]},
            )
            session.add(media_buy)
            session.commit()

        # Check readiness
        readiness = MediaBuyReadinessService.get_readiness_state(media_buy_id, test_tenant)
        assert readiness["state"] == "needs_creatives"
        assert not readiness["is_ready_to_activate"]
        assert readiness["packages_total"] == 1
        assert readiness["packages_with_creatives"] == 0
        assert "missing creative assignments" in readiness["blocking_issues"][0]

        # Cleanup
        with get_db_session() as session:
            session.execute(delete(MediaBuy).where(MediaBuy.media_buy_id == media_buy_id))
            session.commit()

    def test_needs_approval_state(self, test_tenant, test_principal):
        """Media buy with pending creatives should be 'needs_approval'."""
        media_buy_id = "mb_needs_approval"
        creative_id = "cr_pending"
        now = datetime.now(UTC)

        with get_db_session() as session:
            # Create media buy
            media_buy = MediaBuy(
                media_buy_id=media_buy_id,
                tenant_id=test_tenant,
                principal_id=test_principal,
                order_name="Needs Approval Order",
                advertiser_name="Test Advertiser",
                budget=1000.0,
                start_date=(now + timedelta(days=1)).date(),
                end_date=(now + timedelta(days=7)).date(),
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=7),
                status="active",
                raw_request={"packages": [{"package_id": "pkg_1", "product_id": "prod_1"}]},
            )
            session.add(media_buy)

            # Create pending creative
            creative = Creative(
                creative_id=creative_id,
                tenant_id=test_tenant,
                principal_id=test_principal,
                name="Pending Creative",
                format="display_300x250",
                status="pending",  # Pending approval
                data={},
            )
            session.add(creative)

            # Create assignment
            assignment = CreativeAssignment(
                assignment_id="assign_1",
                tenant_id=test_tenant,
                creative_id=creative_id,
                media_buy_id=media_buy_id,
                package_id="pkg_1",
            )
            session.add(assignment)
            session.commit()

        # Check readiness
        readiness = MediaBuyReadinessService.get_readiness_state(media_buy_id, test_tenant)
        assert readiness["state"] == "needs_approval"
        assert not readiness["is_ready_to_activate"]
        assert readiness["creatives_pending"] == 1
        assert "pending approval" in readiness["warnings"][0]

        # Cleanup
        with get_db_session() as session:
            session.execute(delete(CreativeAssignment).where(CreativeAssignment.media_buy_id == media_buy_id))
            session.execute(delete(Creative).where(Creative.creative_id == creative_id))
            session.execute(delete(MediaBuy).where(MediaBuy.media_buy_id == media_buy_id))
            session.commit()

    def test_scheduled_state(self, test_tenant, test_principal):
        """Media buy ready but before start date should be 'scheduled'."""
        media_buy_id = "mb_scheduled"
        creative_id = "cr_approved"
        now = datetime.now(UTC)

        with get_db_session() as session:
            # Create media buy starting in future
            media_buy = MediaBuy(
                media_buy_id=media_buy_id,
                tenant_id=test_tenant,
                principal_id=test_principal,
                order_name="Scheduled Order",
                advertiser_name="Test Advertiser",
                budget=1000.0,
                start_date=(now + timedelta(days=1)).date(),
                end_date=(now + timedelta(days=7)).date(),
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=7),
                status="active",
                raw_request={"packages": [{"package_id": "pkg_1", "product_id": "prod_1"}]},
            )
            session.add(media_buy)

            # Create approved creative
            creative = Creative(
                creative_id=creative_id,
                tenant_id=test_tenant,
                principal_id=test_principal,
                name="Approved Creative",
                format="display_300x250",
                status="approved",
                data={},
            )
            session.add(creative)

            # Create assignment
            assignment = CreativeAssignment(
                assignment_id="assign_1",
                tenant_id=test_tenant,
                creative_id=creative_id,
                media_buy_id=media_buy_id,
                package_id="pkg_1",
            )
            session.add(assignment)
            session.commit()

        # Check readiness
        readiness = MediaBuyReadinessService.get_readiness_state(media_buy_id, test_tenant)
        assert readiness["state"] == "scheduled"
        assert readiness["is_ready_to_activate"]
        assert readiness["creatives_approved"] == 1
        assert len(readiness["blocking_issues"]) == 0

        # Cleanup
        with get_db_session() as session:
            session.execute(delete(CreativeAssignment).where(CreativeAssignment.media_buy_id == media_buy_id))
            session.execute(delete(Creative).where(Creative.creative_id == creative_id))
            session.execute(delete(MediaBuy).where(MediaBuy.media_buy_id == media_buy_id))
            session.commit()

    def test_live_state(self, test_tenant, test_principal):
        """Media buy during flight with approved creatives should be 'live'."""
        media_buy_id = "mb_live"
        creative_id = "cr_live"
        now = datetime.now(UTC)

        with get_db_session() as session:
            # Create media buy currently in flight
            media_buy = MediaBuy(
                media_buy_id=media_buy_id,
                tenant_id=test_tenant,
                principal_id=test_principal,
                order_name="Live Order",
                advertiser_name="Test Advertiser",
                budget=1000.0,
                start_date=(now - timedelta(days=1)).date(),
                end_date=(now + timedelta(days=6)).date(),
                start_time=now - timedelta(days=1),
                end_time=now + timedelta(days=6),
                status="active",
                raw_request={"packages": [{"package_id": "pkg_1", "product_id": "prod_1"}]},
            )
            session.add(media_buy)

            # Create approved creative
            creative = Creative(
                creative_id=creative_id,
                tenant_id=test_tenant,
                principal_id=test_principal,
                name="Live Creative",
                format="display_300x250",
                status="approved",
                data={},
            )
            session.add(creative)

            # Create assignment
            assignment = CreativeAssignment(
                assignment_id="assign_1",
                tenant_id=test_tenant,
                creative_id=creative_id,
                media_buy_id=media_buy_id,
                package_id="pkg_1",
            )
            session.add(assignment)
            session.commit()

        # Check readiness
        readiness = MediaBuyReadinessService.get_readiness_state(media_buy_id, test_tenant)
        assert readiness["state"] == "live"
        assert readiness["is_ready_to_activate"]

        # Cleanup
        with get_db_session() as session:
            session.execute(delete(CreativeAssignment).where(CreativeAssignment.media_buy_id == media_buy_id))
            session.execute(delete(Creative).where(Creative.creative_id == creative_id))
            session.execute(delete(MediaBuy).where(MediaBuy.media_buy_id == media_buy_id))
            session.commit()

    def test_completed_state(self, test_tenant, test_principal):
        """Media buy past end date should be 'completed'."""
        media_buy_id = "mb_completed"
        now = datetime.now(UTC)

        with get_db_session() as session:
            # Create media buy that ended yesterday
            media_buy = MediaBuy(
                media_buy_id=media_buy_id,
                tenant_id=test_tenant,
                principal_id=test_principal,
                order_name="Completed Order",
                advertiser_name="Test Advertiser",
                budget=1000.0,
                start_date=(now - timedelta(days=7)).date(),
                end_date=(now - timedelta(days=1)).date(),
                start_time=now - timedelta(days=7),
                end_time=now - timedelta(days=1),
                status="completed",
                raw_request={"packages": []},
            )
            session.add(media_buy)
            session.commit()

        # Check readiness
        readiness = MediaBuyReadinessService.get_readiness_state(media_buy_id, test_tenant)
        assert readiness["state"] == "completed"

        # Cleanup
        with get_db_session() as session:
            session.execute(delete(MediaBuy).where(MediaBuy.media_buy_id == media_buy_id))
            session.commit()

    def test_tenant_readiness_summary(self, test_tenant, test_principal):
        """Test tenant-wide readiness summary."""
        now = datetime.now(UTC)

        with get_db_session() as session:
            # Create multiple media buys in different states
            media_buys = [
                MediaBuy(
                    media_buy_id="mb_live_1",
                    tenant_id=test_tenant,
                    principal_id=test_principal,
                    order_name="Live 1",
                    advertiser_name="Test",
                    budget=1000.0,
                    start_date=(now - timedelta(days=1)).date(),
                    end_date=(now + timedelta(days=6)).date(),
                    start_time=now - timedelta(days=1),
                    end_time=now + timedelta(days=6),
                    status="active",
                    raw_request={"packages": []},
                ),
                MediaBuy(
                    media_buy_id="mb_completed_1",
                    tenant_id=test_tenant,
                    principal_id=test_principal,
                    order_name="Completed 1",
                    advertiser_name="Test",
                    budget=1000.0,
                    start_date=(now - timedelta(days=7)).date(),
                    end_date=(now - timedelta(days=1)).date(),
                    start_time=now - timedelta(days=7),
                    end_time=now - timedelta(days=1),
                    status="completed",
                    raw_request={"packages": []},
                ),
            ]
            for mb in media_buys:
                session.add(mb)
            session.commit()

        # Get summary
        summary = MediaBuyReadinessService.get_tenant_readiness_summary(test_tenant)
        assert summary["completed"] >= 1
        # Note: exact counts depend on how empty buys are classified

        # Cleanup
        with get_db_session() as session:
            session.execute(delete(MediaBuy).where(MediaBuy.tenant_id == test_tenant))
            session.commit()
