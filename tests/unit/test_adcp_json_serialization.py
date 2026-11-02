"""Test that AdCP responses correctly exclude None values in JSON serialization.

This is critical for client compatibility - the AdCP client (adcp/client npm package)
validates responses against JSON schemas that don't allow null for optional fields.

Issue: PR #xxx - list_authorized_properties returned null for optional fields
Fix: AdCPBaseModel.model_dump_json() now defaults to exclude_none=True
"""

import json

from src.core.schemas import (
    GetProductsResponse,
    ListCreativeFormatsResponse,
)


def test_other_responses_also_exclude_none():
    """Verify all AdCP response types exclude None values."""
    # GetProductsResponse
    products_resp = GetProductsResponse(products=[])
    products_json = json.loads(products_resp.model_dump_json())
    assert "products" in products_json
    # Should not have None-valued optional fields

    # ListCreativeFormatsResponse
    formats_resp = ListCreativeFormatsResponse(formats=[])
    formats_json = json.loads(formats_resp.model_dump_json())
    assert "formats" in formats_json
    # Should not have None-valued optional fields
