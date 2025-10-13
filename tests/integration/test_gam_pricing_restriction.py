"""Integration test for GAM pricing model restrictions (AdCP PR #88).

Tests that GAM adapter properly enforces CPM-only restriction.
"""

from decimal import Decimal

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import CurrencyLimit, PricingOption, Principal, Product, Tenant
from src.core.main import _create_media_buy_impl
from src.core.schemas import CreateMediaBuyRequest, Package, PricingModel
from tests.utils.database_helpers import create_tenant_with_timestamps

pytestmark = pytest.mark.requires_db


@pytest.fixture
def setup_gam_tenant_with_non_cpm_product(integration_db):
    """Create a GAM tenant with a product offering non-CPM pricing."""
    with get_db_session() as session:
        # Create GAM tenant
        tenant = create_tenant_with_timestamps(
            tenant_id="test_gam_tenant",
            name="GAM Test Publisher",
            subdomain="gam-test",
            ad_server="google_ad_manager",
        )
        session.add(tenant)
        session.flush()

        # Add currency limit
        currency_limit = CurrencyLimit(
            tenant_id="test_gam_tenant",
            currency_code="USD",
            max_daily_package_spend=Decimal("50000.00"),
        )
        session.add(currency_limit)

        # Create principal
        principal = Principal(
            tenant_id="test_gam_tenant",
            principal_id="test_advertiser",
            name="Test Advertiser",
            access_token="test_gam_token",
            platform_mappings={"google_ad_manager": {"advertiser_id": "gam_adv_123"}},
        )
        session.add(principal)

        # Create product with CPCV pricing (not supported by GAM)
        product = Product(
            tenant_id="test_gam_tenant",
            product_id="prod_gam_cpcv",
            name="Video Ads - CPCV",
            description="Video inventory with CPCV pricing",
            formats=["video_instream"],
            delivery_type="non-guaranteed",
            targeting_template={},
            implementation_config={},
        )
        session.add(product)
        session.flush()

        # Add CPCV pricing option
        pricing_cpcv = PricingOption(
            tenant_id="test_gam_tenant",
            product_id="prod_gam_cpcv",
            pricing_model="cpcv",
            rate=Decimal("0.40"),
            currency="USD",
            is_fixed=True,
            price_guidance=None,
            parameters=None,
            min_spend_per_package=None,
        )
        session.add(pricing_cpcv)

        # Create product with CPM pricing (supported by GAM)
        product_cpm = Product(
            tenant_id="test_gam_tenant",
            product_id="prod_gam_cpm",
            name="Display Ads - CPM",
            description="Display inventory with CPM pricing",
            formats=["display_300x250"],
            delivery_type="guaranteed",
            targeting_template={},
            implementation_config={},
        )
        session.add(product_cpm)
        session.flush()

        # Add CPM pricing option
        pricing_cpm = PricingOption(
            tenant_id="test_gam_tenant",
            product_id="prod_gam_cpm",
            pricing_model="cpm",
            rate=Decimal("12.50"),
            currency="USD",
            is_fixed=True,
            price_guidance=None,
            parameters=None,
            min_spend_per_package=None,
        )
        session.add(pricing_cpm)

        # Create product with multiple pricing models including non-CPM
        product_multi = Product(
            tenant_id="test_gam_tenant",
            product_id="prod_gam_multi",
            name="Premium Package",
            description="Multiple pricing models (some unsupported)",
            formats=["display_300x250", "video_instream"],
            delivery_type="non-guaranteed",
            targeting_template={},
            implementation_config={},
        )
        session.add(product_multi)
        session.flush()

        # Add CPM (supported)
        pricing_multi_cpm = PricingOption(
            tenant_id="test_gam_tenant",
            product_id="prod_gam_multi",
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
            price_guidance=None,
            parameters=None,
            min_spend_per_package=None,
        )
        session.add(pricing_multi_cpm)

        # Add CPP (not supported by GAM)
        pricing_multi_cpp = PricingOption(
            tenant_id="test_gam_tenant",
            product_id="prod_gam_multi",
            pricing_model="cpp",
            rate=Decimal("250.00"),
            currency="USD",
            is_fixed=True,
            price_guidance=None,
            parameters={"demographic": "A18-49"},
            min_spend_per_package=None,
        )
        session.add(pricing_multi_cpp)

        session.commit()

    yield

    # Cleanup
    with get_db_session() as session:
        session.query(PricingOption).filter_by(tenant_id="test_gam_tenant").delete()
        session.query(Product).filter_by(tenant_id="test_gam_tenant").delete()
        session.query(Principal).filter_by(tenant_id="test_gam_tenant").delete()
        session.query(Tenant).filter_by(tenant_id="test_gam_tenant").delete()
        session.commit()


@pytest.mark.requires_db
def test_gam_rejects_cpcv_pricing_model(setup_gam_tenant_with_non_cpm_product):
    """Test that GAM adapter rejects CPCV pricing model with clear error."""
    request = CreateMediaBuyRequest(
        promoted_offering="https://example.com/product",
        packages=[
            Package(
                package_id="pkg_1",
                products=["prod_gam_cpcv"],
                pricing_model=PricingModel.CPCV,  # Not supported by GAM
                budget=10000.0,
            )
        ],
        budget={"total": 10000.0, "currency": "USD"},
        currency="USD",
        flight_start_date="2025-02-01",
        flight_end_date="2025-02-28",
    )

    class MockContext:
        http_request = type("Request", (), {"headers": {"x-adcp-auth": "test_gam_token"}})()

    with get_db_session() as session:
        tenant_obj = session.query(Tenant).filter_by(tenant_id="test_gam_tenant").first()
        tenant = {
            "tenant_id": tenant_obj.tenant_id,
            "name": tenant_obj.name,
            "config": tenant_obj.config,
            "ad_server": tenant_obj.ad_server,
        }

        principal_obj = session.query(Principal).filter_by(tenant_id="test_gam_tenant").first()

    # This should fail with a clear error about GAM not supporting CPCV
    with pytest.raises(Exception) as exc_info:
        _create_media_buy_impl(request, MockContext(), tenant, principal_obj)

    error_msg = str(exc_info.value)
    # Should mention GAM limitation and CPCV
    assert "gam" in error_msg.lower() or "google" in error_msg.lower()
    assert "cpcv" in error_msg.lower() or "pricing" in error_msg.lower()


@pytest.mark.requires_db
def test_gam_accepts_cpm_pricing_model(setup_gam_tenant_with_non_cpm_product):
    """Test that GAM adapter accepts CPM pricing model."""
    request = CreateMediaBuyRequest(
        promoted_offering="https://example.com/product",
        packages=[
            Package(
                package_id="pkg_1",
                products=["prod_gam_cpm"],
                pricing_model=PricingModel.CPM,  # Supported by GAM
                budget=10000.0,
            )
        ],
        budget={"total": 10000.0, "currency": "USD"},
        currency="USD",
        flight_start_date="2025-02-01",
        flight_end_date="2025-02-28",
    )

    class MockContext:
        http_request = type("Request", (), {"headers": {"x-adcp-auth": "test_gam_token"}})()

    with get_db_session() as session:
        tenant_obj = session.query(Tenant).filter_by(tenant_id="test_gam_tenant").first()
        tenant = {
            "tenant_id": tenant_obj.tenant_id,
            "name": tenant_obj.name,
            "config": tenant_obj.config,
            "ad_server": tenant_obj.ad_server,
        }

        principal_obj = session.query(Principal).filter_by(tenant_id="test_gam_tenant").first()

    # This should succeed
    response = _create_media_buy_impl(request, MockContext(), tenant, principal_obj)

    assert response.media_buy_id is not None
    assert response.status in ["active", "pending"]


@pytest.mark.requires_db
def test_gam_rejects_cpp_from_multi_pricing_product(setup_gam_tenant_with_non_cpm_product):
    """Test that GAM adapter rejects CPP when buyer chooses it from multi-pricing product."""
    request = CreateMediaBuyRequest(
        promoted_offering="https://example.com/product",
        packages=[
            Package(
                package_id="pkg_1",
                products=["prod_gam_multi"],
                pricing_model=PricingModel.CPP,  # Not supported by GAM
                budget=15000.0,
            )
        ],
        budget={"total": 15000.0, "currency": "USD"},
        currency="USD",
        flight_start_date="2025-02-01",
        flight_end_date="2025-02-28",
    )

    class MockContext:
        http_request = type("Request", (), {"headers": {"x-adcp-auth": "test_gam_token"}})()

    with get_db_session() as session:
        tenant_obj = session.query(Tenant).filter_by(tenant_id="test_gam_tenant").first()
        tenant = {
            "tenant_id": tenant_obj.tenant_id,
            "name": tenant_obj.name,
            "config": tenant_obj.config,
            "ad_server": tenant_obj.ad_server,
        }

        principal_obj = session.query(Principal).filter_by(tenant_id="test_gam_tenant").first()

    # This should fail with clear error about GAM not supporting CPP
    with pytest.raises(Exception) as exc_info:
        _create_media_buy_impl(request, MockContext(), tenant, principal_obj)

    error_msg = str(exc_info.value)
    assert "cpp" in error_msg.lower() or "pricing" in error_msg.lower()


@pytest.mark.requires_db
def test_gam_accepts_cpm_from_multi_pricing_product(setup_gam_tenant_with_non_cpm_product):
    """Test that GAM adapter accepts CPM when buyer chooses it from multi-pricing product."""
    request = CreateMediaBuyRequest(
        promoted_offering="https://example.com/product",
        packages=[
            Package(
                package_id="pkg_1",
                products=["prod_gam_multi"],
                pricing_model=PricingModel.CPM,  # Supported by GAM
                budget=10000.0,
            )
        ],
        budget={"total": 10000.0, "currency": "USD"},
        currency="USD",
        flight_start_date="2025-02-01",
        flight_end_date="2025-02-28",
    )

    class MockContext:
        http_request = type("Request", (), {"headers": {"x-adcp-auth": "test_gam_token"}})()

    with get_db_session() as session:
        tenant_obj = session.query(Tenant).filter_by(tenant_id="test_gam_tenant").first()
        tenant = {
            "tenant_id": tenant_obj.tenant_id,
            "name": tenant_obj.name,
            "config": tenant_obj.config,
            "ad_server": tenant_obj.ad_server,
        }

        principal_obj = session.query(Principal).filter_by(tenant_id="test_gam_tenant").first()

    # This should succeed - buyer chose CPM from multi-option product
    response = _create_media_buy_impl(request, MockContext(), tenant, principal_obj)

    assert response.media_buy_id is not None
    assert response.status in ["active", "pending"]
