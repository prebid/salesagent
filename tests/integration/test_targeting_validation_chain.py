"""Integration tests for targeting validation chain wiring in create_media_buy.

Tests that validate_geo_overlap composes correctly through the real
_create_media_buy_impl call path — Pydantic parsing → model_dump → validation
→ error response. No mocked validators.

Note: validate_overlay_targeting (managed-only) and validate_unknown_targeting_fields
are effectively guarded by the Pydantic model layer (Targeting.model_dump excludes
managed-only fields; extra="forbid" rejects unknown fields before validators run).

Covers: salesagent-31v (PR review #10).
"""

from decimal import Decimal

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import PricingOption, Product
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import CreateMediaBuyError, CreateMediaBuyRequest
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.media_buy_create import _create_media_buy_impl
from tests.helpers.adcp_factories import create_test_package_request
from tests.utils.database_helpers import future_iso_date_range, seed_targeting_test_tenant

pytestmark = pytest.mark.requires_db

TENANT_ID = "test_targeting_validation"


@pytest.fixture
def targeting_tenant(integration_db):
    """Create minimal tenant with one product — enough to reach targeting validation."""
    with get_db_session() as session:
        seed_targeting_test_tenant(
            session,
            tenant_id=TENANT_ID,
            tenant_name="Targeting Validation Publisher",
            subdomain="targeting-val",
            access_token="test_token",
        )

        product = Product(
            tenant_id=TENANT_ID,
            product_id="prod_display",
            name="Display Ads",
            description="Standard display",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            delivery_type="guaranteed",
            targeting_template={},
            implementation_config={},
            property_tags=["all_inventory"],
        )
        session.add(product)
        session.flush()

        session.add(
            PricingOption(
                tenant_id=TENANT_ID,
                product_id="prod_display",
                pricing_model="cpm",
                rate=Decimal("10.00"),
                currency="USD",
                is_fixed=True,
            )
        )
        session.commit()

    yield TENANT_ID


def _make_identity() -> ResolvedIdentity:
    from tests.factories import PrincipalFactory

    return PrincipalFactory.make_identity(
        principal_id="test_adv",
        tenant_id=TENANT_ID,
        protocol="mcp",
        testing_context=AdCPTestContext(dry_run=True, test_session_id="test_targeting"),
    )


@pytest.mark.requires_db
async def test_geo_overlap_rejected_through_full_path(targeting_tenant):
    """Same country in include and exclude → validation error via real wiring."""
    start, end = future_iso_date_range()
    request = CreateMediaBuyRequest(
        brand={"domain": "testbrand.com"},
        packages=[
            create_test_package_request(
                product_id="prod_display",
                budget=5000.0,
                pricing_option_id="cpm_usd_fixed",
                targeting_overlay={
                    "geo_countries": ["US"],
                    "geo_countries_exclude": ["US"],
                },
            )
        ],
        start_time=start,
        end_time=end,
    )

    response, status = await _create_media_buy_impl(req=request, identity=_make_identity())

    assert isinstance(response, CreateMediaBuyError), f"Expected error response, got {type(response).__name__}"
    error_text = response.errors[0].message
    assert "geo_countries/geo_countries_exclude conflict" in error_text
    assert "US" in error_text


@pytest.mark.requires_db
async def test_geo_metro_overlap_rejected_through_full_path(targeting_tenant):
    """Same metro DMA in include and exclude → validation error via real wiring."""
    start, end = future_iso_date_range()
    request = CreateMediaBuyRequest(
        brand={"domain": "testbrand.com"},
        packages=[
            create_test_package_request(
                product_id="prod_display",
                budget=5000.0,
                pricing_option_id="cpm_usd_fixed",
                targeting_overlay={
                    "geo_metros": [{"system": "nielsen_dma", "values": ["501", "803"]}],
                    "geo_metros_exclude": [{"system": "nielsen_dma", "values": ["501"]}],
                },
            )
        ],
        start_time=start,
        end_time=end,
    )

    response, status = await _create_media_buy_impl(req=request, identity=_make_identity())

    assert isinstance(response, CreateMediaBuyError), f"Expected error response, got {type(response).__name__}"
    error_text = response.errors[0].message
    assert "geo_metros/geo_metros_exclude conflict" in error_text
    assert "501" in error_text
