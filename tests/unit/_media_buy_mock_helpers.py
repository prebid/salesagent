"""Shared MagicMock builders for media-buy unit tests.

Extracted so ``test_media_buy`` and ``test_create_media_buy_behavioral`` share a
single pricing-option mock builder (DRY — the duplication guard flags a copy).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock


def future(days: int = 7) -> str:
    """Return an ISO 8601 datetime string N days in the future."""
    dt = datetime.now(UTC) + timedelta(days=days)
    return dt.isoformat()


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


def stub_media_buy_reads(repo, row):
    """Point BOTH row accessors at *row*.

    The update tool reads the row back through ``get_by_id_or_raise`` (the
    repository's typed not-found accessor). A stub that wires only ``get_by_id``
    leaves the other returning a bare ``MagicMock``, whose ``.status`` is not a real
    status — which now fails loudly at the vocabulary boundary instead of being
    silently interpreted. One helper so the two never drift apart again.
    """
    repo.get_by_id.return_value = row
    if row is None:
        # Faithful to the repository: the *_or_raise accessor does not return None,
        # it raises. A stub that returned None here would let production walk past a
        # missing row and fail later with an AttributeError instead of the typed
        # MEDIA_BUY_NOT_FOUND the buyer is owed.
        from src.core.exceptions import AdCPMediaBuyNotFoundError

        repo.get_by_id_or_raise.side_effect = AdCPMediaBuyNotFoundError(
            "Media buy not found",
            suggestion="Verify the media_buy_id is correct and belongs to your account.",
        )
    else:
        repo.get_by_id_or_raise.return_value = row
