"""Unit tests for Product schema model_dump branches.

Covers three untested branches in src/core/schemas/product.py:
1. publisher_properties validator — raises ValueError when empty
2. formats → format_ids rename in model_dump() — ensures correct wire format
3. Empty pricing_options=[] — response shape contract for anonymous users

These are pure Pydantic schema tests — no database or transport required.

Covers: salesagent-xsn4
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.schemas import Product
from tests.helpers.adcp_factories import (
    create_test_cpm_pricing_option,
    create_test_format_id,
    create_test_product,
)


class TestPublisherPropertiesValidator:
    """Product validator rejects empty publisher_properties."""

    def test_empty_list_raises_validation_error(self):
        """publisher_properties=[] raises ValidationError per AdCP spec (line 121).

        The library enforces min_length=1 on publisher_properties, which
        catches the empty list before our after-validator at line 120.
        Either way, empty publisher_properties is rejected.
        """
        with pytest.raises(ValidationError):
            create_test_product(publisher_properties=[])

    def test_none_raises_validation_error(self):
        """publisher_properties=None raises ValidationError per AdCP spec (line 120).

        The library field is non-optional with min_length=1, so None is
        rejected at the field level. Must bypass factory (it sets a default).
        """
        with pytest.raises(ValidationError):
            Product(
                product_id="test",
                name="Test",
                description="Test",
                publisher_properties=None,
                format_ids=[create_test_format_id("display_300x250")],
                delivery_type="guaranteed",
                pricing_options=[create_test_cpm_pricing_option()],
                delivery_measurement={"provider": "test", "notes": "Test"},
            )

    def test_valid_publisher_properties_accepted(self):
        """Non-empty publisher_properties passes validation."""
        product = create_test_product()
        assert len(product.publisher_properties) > 0


class TestFormatIdsRenameInModelDump:
    """model_dump() renames internal 'formats' to 'format_ids' for wire format."""

    def test_output_has_format_ids_not_formats(self):
        """model_dump() outputs 'format_ids', not 'formats' (line 203)."""
        product = create_test_product()
        data = product.model_dump()

        assert "format_ids" in data, "Wire format must use 'format_ids'"
        assert "formats" not in data, "'formats' must be renamed to 'format_ids'"

    def test_format_ids_preserves_values(self):
        """Renamed format_ids contains the correct values."""
        product = create_test_product(format_ids=["display_300x250", "video_1920x1080"])
        data = product.model_dump()

        assert "format_ids" in data
        assert len(data["format_ids"]) == 2


class TestEmptyPricingOptionsInModelDump:
    """model_dump() includes pricing_options=[] for anonymous user path.

    In production, products are constructed with valid pricing_options
    (min_length=1), then pricing_options is set to [] for anonymous users
    (see src/core/tools/products.py:852). model_dump() must preserve the
    empty list in the output to maintain the response shape contract.
    """

    def test_empty_pricing_options_included(self):
        """pricing_options=[] appears in model_dump() output (lines 222-226).

        Simulates the anonymous user path: product created with pricing,
        then pricing_options cleared before serialization.
        """
        product = create_test_product()
        # Simulate anonymous path: clear pricing after construction
        product.pricing_options = []
        data = product.model_dump()

        assert "pricing_options" in data, "Empty pricing_options must be present in output"
        assert data["pricing_options"] == []

    def test_populated_pricing_options_included(self):
        """Non-empty pricing_options also appear in output."""
        product = create_test_product()
        data = product.model_dump()

        assert "pricing_options" in data
        assert len(data["pricing_options"]) > 0
