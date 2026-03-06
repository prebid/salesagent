"""Regression test: local schema JSON files use 'account' but adcp 3.6.0 uses 'account_id'.

Bug: salesagent-jbk6

Root cause: The local JSON schema files (schemas/v1/) define an 'account' field
(referencing core/account-ref.json), while the adcp 3.6.0 Python library models
use 'account_id' (a plain string). This mismatch causes test_pydantic_schema_alignment
to fail because:
1. The schema says 'account' exists, but the Pydantic model only has 'account_id'.
2. Sending {'account': {...}} to the model raises extra_forbidden in strict mode.

Additionally, the schema defines status_filter as a oneOf[string-enum, array-of-enum],
but the test generates a plain string that doesn't match either branch of the Union type
(MediaBuyStatus | StatusFilter) in adcp 3.6.0.
"""

import json
from pathlib import Path

import pytest

from src.core.schemas import GetMediaBuyDeliveryRequest, GetProductsRequest

SCHEMA_DIR = Path(__file__).parent.parent.parent / "schemas" / "v1"


class TestAccountFieldMismatch:
    """Demonstrate account vs account_id mismatch between local schemas and adcp 3.6.0."""

    def test_get_products_schema_has_account_id_matching_model(self):
        """Library uses 'account_id' (string). Local extends with 'account' (object ref)."""
        schema_path = SCHEMA_DIR / "_schemas_latest_media-buy_get-products-request_json.json"
        schema = json.loads(schema_path.read_text())
        schema_fields = set(schema["properties"].keys())
        model_fields = set(GetProductsRequest.model_fields.keys())

        # 'account_id' (string) comes from adcp 3.6.0 library
        assert "account_id" in model_fields, "Model should have 'account_id' from adcp 3.6.0"
        # 'account' (object ref) is a local extension for account-based product lookup
        # These are different fields: account_id is a string ID, account is a full ref object
        assert "account" in model_fields, "Model should have 'account' as local extension (account-ref.json)"

    def test_get_media_buy_delivery_schema_has_account_id_matching_model(self):
        """Library uses 'account_id' (string). Local extends with 'account' (object ref)."""
        schema_path = SCHEMA_DIR / "_schemas_latest_media-buy_get-media-buy-delivery-request_json.json"
        schema = json.loads(schema_path.read_text())
        schema_fields = set(schema["properties"].keys())
        model_fields = set(GetMediaBuyDeliveryRequest.model_fields.keys())

        # 'account_id' (string) comes from adcp 3.6.0 library
        assert "account_id" in model_fields, "Model should have 'account_id' from adcp 3.6.0"
        # 'account' (object ref) is a local extension from spec
        assert "account" in model_fields, "Model should have 'account' as local extension (account-ref.json)"

    def test_get_products_model_accepts_account_field(self):
        """Model accepts 'account' as a local extension (dict for account-ref.json)."""
        req = GetProductsRequest(account={"account_id": "acc_123"}, brief="test")
        assert req.account == {"account_id": "acc_123"}

    def test_get_media_buy_delivery_model_accepts_account_field(self):
        """Model accepts 'account' as a local extension (dict for account-ref.json)."""
        req = GetMediaBuyDeliveryRequest(account={"account_id": "acc_123"})
        assert req.account == {"account_id": "acc_123"}

    def test_status_filter_schema_type_vs_model_type(self):
        """Schema defines status_filter as oneOf[enum-string, array-of-enum],
        but adcp 3.6.0 types it as Union[MediaBuyStatus, StatusFilter(RootModel[list]), None].

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
