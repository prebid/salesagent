"""Shared builder for a minimal valid GetMediaBuyDeliveryResponse in scheduler tests."""

from src.core.schemas import GetMediaBuyDeliveryResponse


def make_delivery_response(media_buy_id: str) -> GetMediaBuyDeliveryResponse:
    """Minimal spec-valid delivery response for one media buy (1000 imps, $10)."""
    return GetMediaBuyDeliveryResponse(
        reporting_period={"start": "2025-01-01T00:00:00Z", "end": "2025-01-02T00:00:00Z"},
        currency="USD",
        media_buy_deliveries=[
            {
                "media_buy_id": media_buy_id,
                "status": "active",  # Required field per AdCP spec
                "totals": {"impressions": 1000, "spend": 10.0, "clicks": 5},
                "by_package": [],
            }
        ],
        aggregated_totals={  # Required field per AdCP spec
            "spend": 10.0,
            "impressions": 1000,
            "clicks": 5,
            "media_buy_count": 1,
        },
    )
