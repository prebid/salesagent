"""Create a real mock-adapter buy shared by adapter integration tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.adapters.base import AdapterCreateRequest
from src.adapters.mock_ad_server import MockAdServer
from src.core.schemas import FormatId, MediaPackage


def create_mock_media_buy(adapter: MockAdServer, *, package_id: str) -> str:
    """Register the buy through the real adapter before uploading creative assets."""
    start = datetime.now(UTC)
    result = adapter.create_media_buy(
        AdapterCreateRequest(brand={"domain": "example.com"}, total_budget=Decimal("5000.00")),
        [
            MediaPackage(
                package_id=package_id,
                name="Asset contract",
                delivery_type="guaranteed",
                cpm=5.0,
                impressions=10000,
                format_ids=[FormatId(agent_url="https://creative.test", id="display_300x250")],
                product_id="prod_asset_contract",
            )
        ],
        start,
        start + timedelta(days=30),
    )
    return result.media_buy_id
