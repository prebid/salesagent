"""Shared MagicMock builders for media-buy unit tests.

Extracted so ``test_media_buy`` and ``test_create_media_buy_behavioral`` share a
single pricing-option mock builder (DRY — the duplication guard flags a copy).
"""

from decimal import Decimal
from unittest.mock import MagicMock


def mock_pricing_option(currency: str = "USD") -> MagicMock:
    """A mock pricing_option: single fixed CPM at 5.00, no per-package minimum."""
    pricing_option = MagicMock(
        spec=["pricing_model", "currency", "is_fixed", "rate", "min_spend_per_package", "root"],
    )
    pricing_option.pricing_model = "cpm"
    pricing_option.currency = currency
    pricing_option.is_fixed = True
    pricing_option.rate = Decimal("5.00")
    pricing_option.min_spend_per_package = None
    pricing_option.root = pricing_option
    return pricing_option
