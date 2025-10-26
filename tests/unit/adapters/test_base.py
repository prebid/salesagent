from datetime import UTC, datetime, timedelta

import pytest

from src.adapters.mock_ad_server import MockAdServer
from src.core.schemas import CreateMediaBuyRequest, FormatId, MediaPackage, Package, Principal

pytestmark = pytest.mark.unit

# Default agent URL for creating FormatId objects
DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"


def make_format_id(format_id: str) -> FormatId:
    """Helper to create FormatId objects with default agent URL."""
    return FormatId(agent_url=DEFAULT_AGENT_URL, id=format_id)


@pytest.fixture
def sample_packages():
    """A fixture to create a sample list of media packages for use in tests."""
    return [
        MediaPackage(
            package_id="pkg_1",
            name="Guaranteed Banner",
            delivery_type="guaranteed",
            cpm=15.0,
            impressions=333333,  # 5000 budget / 15 CPM * 1000
            budget=5000.0,  # Budget as float in MediaPackage (internal adapter format)
            format_ids=[
                make_format_id("display_300x250"),
                make_format_id("display_728x90"),
            ],
        )
    ]


def test_mock_ad_server_create_media_buy(sample_packages, mocker):
    """
    Tests that the MockAdServer correctly creates a media buy
    when a create_media_buy request is received.
    """
    # Arrange
    principal = Principal(
        principal_id="test_principal",
        name="Test Principal",
        platform_mappings={"mock": {"advertiser_id": "test_advertiser"}},
    )

    # Mock get_current_tenant to avoid database access in unit test
    mocker.patch("src.core.config_loader.get_current_tenant", return_value={"tenant_id": "test_tenant"})

    adapter = MockAdServer({}, principal)
    start_time = datetime.now(UTC)
    end_time = start_time + timedelta(days=30)

    # Per AdCP v2.2.0: packages with budget are required
    packages = [
        Package(
            buyer_ref="pkg_ref_1",
            products=["pkg_1"],
            budget=5000.0,
            format_ids=[
                make_format_id("display_300x250"),
                make_format_id("display_728x90"),
            ],
        )
    ]

    request = CreateMediaBuyRequest(
        brand_manifest={"name": "Premium basketball shoes for sports enthusiasts"},
        buyer_ref="ref_12345",  # Required per AdCP spec
        packages=packages,  # AdCP v2.2.0: packages required
        start_time=start_time,
        end_time=end_time,
        targeting_overlay={},  # Empty targeting
        po_number="PO-12345",
    )

    # Act
    response = adapter.create_media_buy(
        request=request, packages=sample_packages, start_time=start_time, end_time=end_time
    )

    # Assert
    assert response.media_buy_id == "buy_PO-12345"
    # buyer_ref should echo back the request buyer_ref per AdCP spec
    assert response.buyer_ref == "ref_12345"

    # Check the internal state of the mock server
    internal_buy = adapter._media_buys.get("buy_PO-12345")
    assert internal_buy is not None
    assert internal_buy["total_budget"] == 5000
    assert len(internal_buy["packages"]) == 1
    assert internal_buy["packages"][0]["package_id"] == "pkg_1"
