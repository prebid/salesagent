"""Integration tests for pricing models (AdCP PR #88).

Tests the full flow: create product with pricing_options → get products → create media buy.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import CurrencyLimit, PricingOption, Principal, Product, PropertyTag, Tenant
from src.core.exceptions import AdCPValidationError
from src.core.schemas import GetProductsRequest, PricingModel
from src.core.tools.media_buy_create import _create_media_buy_impl
from src.core.tools.products import _get_products_impl
from tests.factories import PricingOptionFactory
from tests.factories.account import seed_default_account
from tests.factories.principal import PrincipalFactory, plaintext_token_for
from tests.helpers.adcp_factories import create_test_media_buy_request, create_test_package_request
from tests.integration.media_buy_helpers import assert_created, make_media_buy_identity
from tests.utils.database_helpers import bind_factories_to_session, create_tenant_with_timestamps
from tests.utils.tenant_setup import seed_setup_checklist_rows

pytestmark = pytest.mark.requires_db


TENANT_ID = "test_pricing_tenant"
PRINCIPAL_ID = "test_advertiser"


def _identity():
    """The caller ``_create_media_buy_impl`` takes: principal, tenant AND account.

    The account is spec-required on the request and the implementation reads
    ``identity.account.account_id``, so a plain ``ResolvedIdentity`` is the wrong TYPE
    here -- see ``make_media_buy_identity``.
    """
    return make_media_buy_identity(PRINCIPAL_ID, TENANT_ID)


def _get_future_date_range() -> tuple[str, str]:
    """Get a valid future date range for tests.

    Returns start_time (tomorrow) and end_time (30 days from now) as ISO strings.
    """
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    end_date = tomorrow + timedelta(days=30)
    start_time = tomorrow.strftime("%Y-%m-%dT00:00:00Z")
    end_time = end_date.strftime("%Y-%m-%dT23:59:59Z")
    return start_time, end_time


@pytest.fixture
def setup_tenant_with_pricing_products(integration_db):
    """Create a tenant with products using various pricing models."""
    with get_db_session() as session:
        # Create tenant
        tenant = create_tenant_with_timestamps(
            tenant_id="test_pricing_tenant",
            name="Pricing Test Publisher",
            subdomain="pricing-test",
            ad_server="mock",
            # Half of the setup checklist's "SSO configured" task; the other half is the
            # TenantAuthConfig row seed_setup_checklist_rows writes below.
            auth_setup_mode=False,
            # The column defaults to True, which routes every create to the
            # manual-approval branch and answers `submitted` with no media_buy_id. These
            # tests grade PRICING on a completed create, so the gate is off -- the same
            # reason the GAM pricing fixtures set it.
            human_review_required=False,
        )
        session.add(tenant)
        session.flush()

        # create_media_buy validates the setup checklist for every caller now, so the
        # tenant these tests drive has to really be set up.
        seed_setup_checklist_rows(session, tenant)

        # Add property tag (required for products)
        property_tag = PropertyTag(
            tenant_id="test_pricing_tenant",
            tag_id="all_inventory",
            name="All Inventory",
            description="All available inventory",
        )
        session.add(property_tag)

        # Add currency limit
        currency_limit = CurrencyLimit(
            tenant_id="test_pricing_tenant",
            currency_code="USD",
            max_daily_package_spend=Decimal("50000.00"),
        )
        session.add(currency_limit)

        # Add principal for authentication
        principal = Principal.with_token(
            plaintext_token_for("test_advertiser"),
            tenant_id="test_pricing_tenant",
            principal_id="test_advertiser",
            name="Test Advertiser",
            platform_mappings={"mock": {"advertiser_id": "mock_adv_123"}},
        )
        session.add(principal)
        session.flush()

        # The ACCOUNT the request names, and this principal's access to it. Required on
        # create-media-buy-request.json, and resolved for real -- a payload naming an
        # account with no row, or with no grant for the caller, is refused before the
        # pricing logic under test runs.
        with bind_factories_to_session(session):
            seed_default_account(TENANT_ID, PRINCIPAL_ID)

        # Product 1: CPM fixed rate
        product_cpm_fixed = Product(
            tenant_id="test_pricing_tenant",
            product_id="prod_cpm_fixed",
            name="Display Ads - Fixed CPM",
            description="Standard display inventory",
            format_ids=[
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
            ],
            delivery_type="guaranteed",
            targeting_template={},
            implementation_config={},
            property_tags=["all_inventory"],
        )
        session.add(product_cpm_fixed)
        session.flush()

        pricing_cpm_fixed = PricingOptionFactory.build(
            tenant_id="test_pricing_tenant",
            product_id="prod_cpm_fixed",
            pricing_model="cpm",
            rate=Decimal("12.50"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing_cpm_fixed)

        # Product 2: CPM auction
        product_cpm_auction = Product(
            tenant_id="test_pricing_tenant",
            product_id="prod_cpm_auction",
            name="Display Ads - Auction CPM",
            description="Programmatic display inventory",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            delivery_type="non_guaranteed",
            targeting_template={},
            implementation_config={},
            property_tags=["all_inventory"],
        )
        session.add(product_cpm_auction)
        session.flush()

        pricing_cpm_auction = PricingOptionFactory.build(
            tenant_id="test_pricing_tenant",
            product_id="prod_cpm_auction",
            pricing_model="cpm",
            rate=None,
            currency="USD",
            is_fixed=False,
            price_guidance={"floor": 8.0, "p25": 10.0, "p50": 12.0, "p75": 15.0, "p90": 18.0},
        )
        session.add(pricing_cpm_auction)

        # Product 3: CPCV fixed rate with min spend
        product_cpcv = Product(
            tenant_id="test_pricing_tenant",
            product_id="prod_cpcv",
            name="Video Ads - CPCV",
            description="Cost per completed view video inventory",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "video_instream"}],
            delivery_type="non_guaranteed",
            targeting_template={},
            implementation_config={},
            property_tags=["all_inventory"],
        )
        session.add(product_cpcv)
        session.flush()

        pricing_cpcv = PricingOptionFactory.build(
            tenant_id="test_pricing_tenant",
            product_id="prod_cpcv",
            pricing_model="cpcv",
            rate=Decimal("0.35"),
            currency="USD",
            is_fixed=True,
            min_spend_per_package=Decimal("5000.00"),
        )
        session.add(pricing_cpcv)

        # Product 4: Multiple pricing models
        product_multi = Product(
            tenant_id="test_pricing_tenant",
            product_id="prod_multi",
            name="Premium Package - Multiple Models",
            description="Choose your pricing model",
            format_ids=[
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_instream"},
            ],
            delivery_type="non_guaranteed",
            targeting_template={},
            implementation_config={},
            property_tags=["all_inventory"],
        )
        session.add(product_multi)
        session.flush()

        # Add CPM option
        pricing_multi_cpm = PricingOptionFactory.build(
            tenant_id="test_pricing_tenant",
            product_id="prod_multi",
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing_multi_cpm)

        # Add CPCV option
        pricing_multi_cpcv = PricingOptionFactory.build(
            tenant_id="test_pricing_tenant",
            product_id="prod_multi",
            pricing_model="cpcv",
            rate=Decimal("0.40"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing_multi_cpcv)

        # Add CPP option with demographics
        pricing_multi_cpp = PricingOptionFactory.build(
            tenant_id="test_pricing_tenant",
            product_id="prod_multi",
            pricing_model="cpp",
            rate=Decimal("250.00"),
            currency="USD",
            is_fixed=True,
            parameters={"demographic": "A18-49", "min_points": 5.0},
            min_spend_per_package=Decimal("10000.00"),
        )
        session.add(pricing_multi_cpp)

        session.commit()

    yield

    # Cleanup
    with get_db_session() as session:
        from sqlalchemy import delete, select

        from src.core.database.models import MediaBuy, MediaPackage

        # Delete in correct order to respect foreign keys
        # 1. Delete child records first (MediaPackage references MediaBuy)
        # Note: MediaPackage doesn't have tenant_id directly, must filter by media_buy_id
        tenant_media_buy_ids = session.scalars(
            select(MediaBuy.media_buy_id).where(MediaBuy.tenant_id == "test_pricing_tenant")
        ).all()
        if tenant_media_buy_ids:
            session.execute(delete(MediaPackage).where(MediaPackage.media_buy_id.in_(tenant_media_buy_ids)))
        session.execute(delete(MediaBuy).where(MediaBuy.tenant_id == "test_pricing_tenant"))

        # 2. Delete product-related records
        session.execute(delete(PricingOption).where(PricingOption.tenant_id == "test_pricing_tenant"))
        session.execute(delete(Product).where(Product.tenant_id == "test_pricing_tenant"))
        session.execute(delete(PropertyTag).where(PropertyTag.tenant_id == "test_pricing_tenant"))

        # 3. Delete principal and tenant records
        session.execute(delete(Principal).where(Principal.tenant_id == "test_pricing_tenant"))
        session.execute(delete(CurrencyLimit).where(CurrencyLimit.tenant_id == "test_pricing_tenant"))
        session.execute(delete(Tenant).where(Tenant.tenant_id == "test_pricing_tenant"))
        session.commit()


@pytest.mark.requires_db
async def test_get_products_returns_pricing_options(setup_tenant_with_pricing_products):
    """Test that get_products returns pricing_options for products."""
    request = GetProductsRequest(brief="display ads", brand={"domain": "testbrand.com"})

    # get_products is a PUBLIC tool and names no account: the plain resolved caller.
    identity = PrincipalFactory.make_identity(
        principal_id="test_advertiser",
        tenant_id=TENANT_ID,
        tenant={"tenant_id": TENANT_ID},
    )

    response = await _get_products_impl(request, identity)

    assert response.products is not None
    assert len(response.products) > 0

    # Find the CPM fixed product
    cpm_product = next((p for p in response.products if p.product_id == "prod_cpm_fixed"), None)
    assert cpm_product is not None
    assert cpm_product.pricing_options is not None
    assert len(cpm_product.pricing_options) == 1
    # adcp 2.14.0+ uses RootModel wrapper - access via .root
    pricing_inner = getattr(cpm_product.pricing_options[0], "root", cpm_product.pricing_options[0])
    assert pricing_inner.pricing_model == PricingModel.cpm.value
    # V3: is_fixed removed - fixed pricing has fixed_price field
    assert pricing_inner.fixed_price == 12.50

    # Find the multi-pricing product
    multi_product = next((p for p in response.products if p.product_id == "prod_multi"), None)
    assert multi_product is not None
    assert multi_product.pricing_options is not None
    assert len(multi_product.pricing_options) == 3

    # Verify all three pricing models exist
    # adcp 2.14.0+ uses RootModel wrapper - access via .root
    pricing_models = {getattr(opt, "root", opt).pricing_model for opt in multi_product.pricing_options}
    assert pricing_models == {PricingModel.cpm.value, PricingModel.cpcv.value, PricingModel.cpp.value}


@pytest.mark.requires_db
async def test_create_media_buy_with_cpm_fixed_pricing(setup_tenant_with_pricing_products):
    """Test creating media buy with fixed CPM pricing."""
    start_time, end_time = _get_future_date_range()
    request = create_test_media_buy_request(
        packages=[
            create_test_package_request(
                product_id="prod_cpm_fixed",
                pricing_option_id="cpm_usd_fixed",  # Format: {model}_{currency}_{fixed|auction}
                budget=10000.0,
            )
        ],
        start_time=start_time,
        end_time=end_time,
    )

    identity = _identity()

    response = await _create_media_buy_impl(req=request, identity=identity)

    assert_created(response)


@pytest.mark.requires_db
async def test_create_media_buy_with_cpm_auction_pricing(setup_tenant_with_pricing_products):
    """Test creating media buy with auction CPM pricing."""
    start_time, end_time = _get_future_date_range()
    request = create_test_media_buy_request(
        packages=[
            create_test_package_request(
                product_id="prod_cpm_auction",
                pricing_option_id="cpm_usd_auction",  # Format: {model}_{currency}_{fixed|auction}
                bid_price=15.0,  # Above floor of 8.0
                budget=10000.0,
            )
        ],
        start_time=start_time,
        end_time=end_time,
    )

    identity = _identity()

    response = await _create_media_buy_impl(req=request, identity=identity)

    assert_created(response)


@pytest.mark.requires_db
async def test_create_media_buy_auction_bid_below_floor_fails(setup_tenant_with_pricing_products):
    """Test that auction bid below floor price fails."""
    start_time, end_time = _get_future_date_range()
    request = create_test_media_buy_request(
        packages=[
            create_test_package_request(
                product_id="prod_cpm_auction",
                pricing_option_id="cpm_usd_auction",  # Format: {model}_{currency}_{fixed|auction}
                bid_price=5.0,  # Below floor of 8.0
                budget=10000.0,
            )
        ],
        start_time=start_time,
        end_time=end_time,
    )

    identity = _identity()

    # Boundary-validation failures are now signaled via typed AdCPSalesAgentError that
    # propagates past the narrowed (ValueError, PermissionError) catch in _impl
    # and is translated to the spec two-layer envelope at the transport
    # boundary. Verify the typed raise at the layer above the transport.
    with pytest.raises(AdCPValidationError) as excinfo:
        await _create_media_buy_impl(req=request, identity=identity)

    exc = excinfo.value
    assert exc.error_code == "VALIDATION_ERROR"


@pytest.mark.requires_db
async def test_create_media_buy_with_cpcv_pricing(setup_tenant_with_pricing_products):
    """Test creating media buy with CPCV pricing."""
    start_time, end_time = _get_future_date_range()
    request = create_test_media_buy_request(
        packages=[
            create_test_package_request(
                product_id="prod_cpcv",
                pricing_option_id="cpcv_usd_fixed",  # Format: {model}_{currency}_{fixed|auction}
                budget=8000.0,  # Above min spend of 5000
            )
        ],
        start_time=start_time,
        end_time=end_time,
    )

    identity = _identity()

    response = await _create_media_buy_impl(req=request, identity=identity)

    assert_created(response)


@pytest.mark.requires_db
async def test_create_media_buy_below_min_spend_fails(setup_tenant_with_pricing_products):
    """Test that budget below min_spend_per_package fails."""
    start_time, end_time = _get_future_date_range()
    request = create_test_media_buy_request(
        packages=[
            create_test_package_request(
                product_id="prod_cpcv",
                pricing_option_id="cpcv_usd_fixed",  # Format: {model}_{currency}_{fixed|auction}
                budget=3000.0,  # Below min spend of 5000
            )
        ],
        start_time=start_time,
        end_time=end_time,
    )

    identity = _identity()

    # Boundary-validation failures are now signaled via typed AdCPSalesAgentError that
    # propagates past the narrowed (ValueError, PermissionError) catch in _impl
    # and is translated to the spec two-layer envelope at the transport
    # boundary. Verify the typed raise at the layer above the transport.
    with pytest.raises(AdCPValidationError) as excinfo:
        await _create_media_buy_impl(req=request, identity=identity)

    exc = excinfo.value
    assert exc.error_code == "VALIDATION_ERROR"


@pytest.mark.requires_db
async def test_create_media_buy_multi_pricing_choose_cpp(setup_tenant_with_pricing_products):
    """Test creating media buy choosing CPP from multi-pricing product."""
    start_time, end_time = _get_future_date_range()
    request = create_test_media_buy_request(
        packages=[
            create_test_package_request(
                product_id="prod_multi",
                pricing_option_id="cpp_usd_fixed",  # Format: {model}_{currency}_{fixed|auction}
                budget=15000.0,  # Above min spend of 10000
            )
        ],
        start_time=start_time,
        end_time=end_time,
    )

    identity = _identity()

    response = await _create_media_buy_impl(req=request, identity=identity)

    assert_created(response)


@pytest.mark.requires_db
async def test_create_media_buy_invalid_pricing_model_fails(setup_tenant_with_pricing_products):
    """Test that requesting unavailable pricing model fails."""
    start_time, end_time = _get_future_date_range()
    request = create_test_media_buy_request(
        packages=[
            create_test_package_request(
                product_id="prod_cpm_fixed",  # Only offers CPM
                pricing_option_id="cpcv_usd_fixed",  # Requesting CPCV (should fail)
                budget=10000.0,
            )
        ],
        start_time=start_time,
        end_time=end_time,
    )

    identity = _identity()

    # Boundary-validation failures are now signaled via typed AdCPSalesAgentError that
    # propagates past the narrowed (ValueError, PermissionError) catch in _impl
    # and is translated to the spec two-layer envelope at the transport
    # boundary. Verify the typed raise at the layer above the transport.
    with pytest.raises(AdCPValidationError) as excinfo:
        await _create_media_buy_impl(req=request, identity=identity)

    exc = excinfo.value
    assert exc.error_code == "VALIDATION_ERROR"
