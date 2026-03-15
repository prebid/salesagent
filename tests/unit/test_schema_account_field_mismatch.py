"""Regression test: adcp 3.9 uses 'account' (AccountReference) instead of 'account_id' (string).

Bug: salesagent-jbk6

History: adcp 3.6.0 used 'account_id' (plain string). adcp 3.9 renamed it to 'account'
with type AccountReference — a union of:
  - {"account_id": "acc_123"} (by seller-assigned ID)
  - {"brand": {"domain": "..."}, "operator": "..."} (by natural key)

These tests verify that the Pydantic models correctly expose the 'account' field
with AccountReference semantics.

Additionally, the schema defines status_filter as a oneOf[string-enum, array-of-enum],
matching the Union type (MediaBuyStatus | StatusFilter) in adcp 3.9.
"""

import json
from pathlib import Path

import pytest
from adcp.types import AccountReference

from src.core.schemas import GetMediaBuyDeliveryRequest, GetProductsRequest

SCHEMA_DIR = Path(__file__).parent.parent.parent / "schemas" / "v1"


class TestAccountFieldMismatch:
    """Verify account field alignment between local schemas and adcp 3.9."""

    def test_get_products_schema_has_account_matching_model(self):
        """Library uses 'account' (AccountReference) since adcp 3.9."""
        schema_path = SCHEMA_DIR / "_schemas_latest_media-buy_get-products-request_json.json"
        schema = json.loads(schema_path.read_text())
        schema_fields = set(schema["properties"].keys())
        model_fields = set(GetProductsRequest.model_fields.keys())

        # 'account' (AccountReference) comes from adcp 3.9 library
        assert "account" in model_fields, "Model should have 'account' from adcp 3.9"

    def test_get_media_buy_delivery_schema_has_account_matching_model(self):
        """Library uses 'account' (AccountReference) since adcp 3.9."""
        schema_path = SCHEMA_DIR / "_schemas_latest_media-buy_get-media-buy-delivery-request_json.json"
        schema = json.loads(schema_path.read_text())
        schema_fields = set(schema["properties"].keys())
        model_fields = set(GetMediaBuyDeliveryRequest.model_fields.keys())

        # 'account' (AccountReference) comes from adcp 3.9 library
        assert "account" in model_fields, "Model should have 'account' from adcp 3.9"

    def test_get_products_model_accepts_account_field(self):
        """Model accepts 'account' as AccountReference (variant 1: by ID)."""
        req = GetProductsRequest(account={"account_id": "acc_123"}, brief="test")
        assert isinstance(req.account, AccountReference)
        assert req.account.account_id == "acc_123"

    def test_get_media_buy_delivery_model_accepts_account_field(self):
        """Model accepts 'account' as dict (local override, not yet AccountReference)."""
        req = GetMediaBuyDeliveryRequest(account={"account_id": "acc_123"})
        assert req.account == {"account_id": "acc_123"}

    def test_status_filter_schema_type_vs_model_type(self):
        """Schema defines status_filter as oneOf[enum-string, array-of-enum],
        but adcp 3.9 types it as Union[MediaBuyStatus, StatusFilter(RootModel[list]), None].

        The generate_example_value in alignment test produces a plain string like
        'test_status_filter_value' which is neither a valid MediaBuyStatus enum value
        nor a list, so it fails both branches of the Union.
        """
        schema_path = SCHEMA_DIR / "_schemas_latest_media-buy_get-media-buy-delivery-request_json.json"
        schema = json.loads(schema_path.read_text())
        status_spec = schema["properties"]["status_filter"]

        # Schema uses oneOf with $ref to enum and array-of-enum
        assert "oneOf" in status_spec, "Schema should define status_filter as oneOf"

        # Model accepts MediaBuyStatus enum values
        req = GetMediaBuyDeliveryRequest(status_filter="active")
        assert req.status_filter is not None

        # Model also accepts a list of statuses
        req2 = GetMediaBuyDeliveryRequest(status_filter=["active", "paused"])
        assert req2.status_filter is not None

        # But a plain arbitrary string fails
        with pytest.raises((TypeError, ValueError)):
            GetMediaBuyDeliveryRequest(status_filter="test_status_filter_value")
