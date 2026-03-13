"""Integration test: execute_approved_media_buy must persist _platform_line_item_ids.

Bug: salesagent-biv (GitHub #1037)
Root cause: execute_approved_media_buy calls adapter, gets _platform_line_item_ids
back on the response object, but never persists them to MediaPackage.package_config.
The auto-approval path in _create_media_buy_impl DOES persist them (lines 3047-3079).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from src.core.database.database_session import get_db_session, get_engine
from src.core.database.models import MediaPackage as DBMediaPackage
from src.core.schemas import CreateMediaBuySuccess

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def pending_media_buy_with_package(integration_db):
    """Create a media buy in pending_approval status with a package using factories."""
    from sqlalchemy.orm import Session as SASession

    from tests.factories import (
        ALL_FACTORIES,
        MediaBuyFactory,
        MediaPackageFactory,
        PricingOptionFactory,
        PrincipalFactory,
        ProductFactory,
        PropertyTagFactory,
        TenantFactory,
    )

    engine = get_engine()
    session = SASession(bind=engine)
    try:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = session

        tenant = TenantFactory(tenant_id="test_tenant")
        PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
        principal = PrincipalFactory(
            tenant=tenant,
            principal_id="test_principal",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        product = ProductFactory(
            tenant=tenant,
            product_id="guaranteed_display",
            name="Guaranteed Display Ads",
            delivery_type="guaranteed",
        )
        PricingOptionFactory(
            product=product,
            pricing_model="cpm",
            rate=15.0,
            currency="USD",
            is_fixed=True,
        )

        now = datetime.now(UTC)
        start = now + timedelta(days=1)
        end = now + timedelta(days=8)

        mb = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id="mb_approval_test",
            buyer_ref="approval-test-buyer",
            order_name="Approval Test Order",
            advertiser_name="Test Advertiser",
            currency="USD",
            start_date=start.date(),
            end_date=end.date(),
            start_time=start,
            end_time=end,
            status="pending_approval",
            raw_request={
                "buyer_ref": "approval-test-buyer",
                "brand": {"domain": "testbrand.com"},
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "packages": [
                    {
                        "product_id": "guaranteed_display",
                        "pricing_option_id": "po_1",
                        "buyer_ref": "pkg-1",
                        "budget": 5000.0,
                    }
                ],
            },
        )

        MediaPackageFactory(
            media_buy=mb,
            package_id="pkg_001",
            package_config={
                "product_id": "guaranteed_display",
                "name": "Guaranteed Display Ads",
                "budget": 5000.0,
                "pricing_model": "cpm",
            },
        )

        yield {
            "media_buy_id": "mb_approval_test",
            "tenant_id": tenant.tenant_id,
            "package_id": "pkg_001",
        }
    finally:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = None
        session.close()


class TestExecuteApprovedPlatformIds:
    """execute_approved_media_buy must persist _platform_line_item_ids to package_config."""

    def test_platform_line_item_ids_persisted_after_approval(self, pending_media_buy_with_package):
        """After adapter execution via manual approval, platform_line_item_id
        must be written to MediaPackage.package_config for each package.

        This is the regression test for salesagent-biv (GitHub #1037).
        """
        media_buy_id = pending_media_buy_with_package["media_buy_id"]
        tenant_id = pending_media_buy_with_package["tenant_id"]
        package_id = pending_media_buy_with_package["package_id"]

        # Build adapter response with _platform_line_item_ids attached
        adapter_response = CreateMediaBuySuccess(
            media_buy_id=media_buy_id,
            buyer_ref="approval-test-buyer",
            packages=[],
        )
        # This is how GAM/Broadstreet adapters attach the mapping
        object.__setattr__(
            adapter_response,
            "_platform_line_item_ids",
            {package_id: "GAM_LINE_ITEM_12345"},
        )

        with (
            patch(
                "src.core.tools.media_buy_create._execute_adapter_media_buy_creation",
                return_value=adapter_response,
            ),
            patch("src.core.tools.media_buy_create._validate_creatives_before_adapter_call"),
            patch(
                "src.core.helpers.adapter_helpers.get_adapter",
                return_value=type("MockAdapter", (), {"orders_manager": None})(),
            ),
        ):
            from src.core.tools.media_buy_create import execute_approved_media_buy

            success, error = execute_approved_media_buy(media_buy_id, tenant_id)

        assert success is True, f"execute_approved_media_buy failed: {error}"

        # THE KEY ASSERTION: platform_line_item_id must be in package_config
        from sqlalchemy import select

        with get_db_session() as session:
            pkg = session.scalars(
                select(DBMediaPackage).filter_by(
                    media_buy_id=media_buy_id,
                    package_id=package_id,
                )
            ).first()

            assert pkg is not None, f"Package {package_id} not found"
            assert "platform_line_item_id" in pkg.package_config, (
                f"platform_line_item_id NOT persisted in package_config. Got keys: {list(pkg.package_config.keys())}"
            )
            assert pkg.package_config["platform_line_item_id"] == "GAM_LINE_ITEM_12345", (
                f"Wrong platform_line_item_id value: {pkg.package_config.get('platform_line_item_id')}"
            )
