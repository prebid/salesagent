"""Regression test: adcp 3.9 uses 'account' (AccountReference) instead of 'account_id' (string).

Bug: salesagent-jbk6

History: adcp 3.6.0 used 'account_id' (plain string). adcp 3.9 renamed it to 'account'
with type AccountReference — a union of:
  - {"account_id": "acc_123"} (by seller-assigned ID)
  - {"brand": {"domain": "..."}, "operator": "..."} (by natural key)

These tests verify that the Pydantic models correctly expose the 'account' field
with AccountReference semantics.
"""

import pytest
from adcp.types import AccountReference

from src.core.schemas import GetMediaBuyDeliveryRequest, GetProductsRequest


class TestAccountFieldMismatch:
    """Verify account field alignment with adcp 3.9 AccountReference."""

    def test_get_products_model_accepts_account_field(self):
        """Model accepts 'account' as AccountReference (variant 1: by ID)."""
        req = GetProductsRequest(account={"account_id": "acc_123"}, brief="test")
        assert isinstance(req.account, AccountReference)
        assert req.account.account_id == "acc_123"

    def test_get_media_buy_delivery_model_accepts_account_field(self):
        """Model accepts 'account' as AccountReference (adcp 3.10 library type).

        Since adcp 3.10, account is typed as AccountReference | None (no longer
        a dict override). Account resolution happens at the transport boundary.
        """
        req = GetMediaBuyDeliveryRequest(account={"account_id": "acc_123"})
        assert req.account is not None
        assert req.account.account_id == "acc_123"

    def test_status_filter_accepts_enum_value(self):
        """Model accepts MediaBuyStatus enum values."""
        req = GetMediaBuyDeliveryRequest(status_filter="active")
        assert req.status_filter is not None

    def test_status_filter_accepts_list(self):
        """Model accepts a list of statuses."""
        req = GetMediaBuyDeliveryRequest(status_filter=["active", "paused"])
        assert req.status_filter is not None

    def test_status_filter_rejects_arbitrary_string(self):
        """Arbitrary string fails both enum and list branches."""
        with pytest.raises((TypeError, ValueError)):
            GetMediaBuyDeliveryRequest(status_filter="test_status_filter_value")
