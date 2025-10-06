from datetime import datetime, timedelta

import pytest

from src.adapters.mock_ad_server import MockAdServer
from src.core.schemas import CreateMediaBuyRequest, MediaPackage, Principal

pytestmark = pytest.mark.unit


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
            format_ids=["display_300x250", "display_728x90"],
        )
    ]


def test_mock_ad_server_create_media_buy(sample_packages):
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
    adapter = MockAdServer({}, principal)
    start_time = datetime.now()
    end_time = start_time + timedelta(days=30)

    # CreateMediaBuyRequest now uses product_ids, not selected_packages
    request = CreateMediaBuyRequest(
        promoted_offering="Premium basketball shoes for sports enthusiasts",
        product_ids=["pkg_1"],
        start_date=start_time.date(),
        end_date=end_time.date(),
        total_budget=5000.0,  # Legacy format uses total_budget
        targeting_overlay={},  # Empty targeting
        po_number="PO-12345",
    )

    # Act
    response = adapter.create_media_buy(
        request=request, packages=sample_packages, start_time=start_time, end_time=end_time
    )

    # Assert
    assert response.media_buy_id == "buy_PO-12345"
    # buyer_ref is None since not provided by client (it's their identifier, not ours)
    assert response.buyer_ref is None

    # Check the internal state of the mock server
    internal_buy = adapter._media_buys.get("buy_PO-12345")
    assert internal_buy is not None
    assert internal_buy["total_budget"] == 5000
    assert len(internal_buy["packages"]) == 1
    assert internal_buy["packages"][0]["package_id"] == "pkg_1"
