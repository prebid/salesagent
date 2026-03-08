"""Unit tests for delivery_measurement REQUIRED enforcement on Product.

Per AdCP v3.5/3.6, delivery_measurement is REQUIRED on all products.
These tests verify:
1. Schema validation rejects products without delivery_measurement
2. Adapter defaults are applied correctly during conversion
3. model_dump always includes delivery_measurement
4. GAM defaults to google_ad_manager provider
5. Mock defaults to mock provider
6. Generic fallback uses "publisher" provider

beads: salesagent-pxhs
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.adapters import get_adapter_default_delivery_measurement
from src.core.database.models import PricingOption
from src.core.database.models import Product as ProductModel
from src.core.product_conversion import convert_product_model_to_schema
from src.core.schemas import Product
from tests.helpers.adcp_factories import (
    create_test_cpm_pricing_option,
    create_test_db_product,
    create_test_publisher_properties_by_tag,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_schema_product(**overrides) -> Product:
    """Create a minimal valid Product schema object."""
    defaults = {
        "product_id": "dm_test",
        "name": "DM Test Product",
        "description": "Test product for delivery_measurement",
        "format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
        "delivery_type": "guaranteed",
        "delivery_measurement": {"provider": "test_provider"},
        "publisher_properties": [create_test_publisher_properties_by_tag()],
        "pricing_options": [create_test_cpm_pricing_option()],
    }
    defaults.update(overrides)
    return Product(**defaults)


def _make_db_product(**overrides) -> ProductModel:
    """Create a DB Product with PricingOption attached (no session needed)."""
    defaults = {
        "tenant_id": "dm_test_tenant",
        "product_id": "dm_test_001",
        "name": "DM Test Product",
        "delivery_type": "guaranteed",
        "delivery_measurement": {"provider": "test_provider"},
    }
    defaults.update(overrides)
    product = create_test_db_product(**defaults)
    pricing = PricingOption(
        tenant_id=defaults["tenant_id"],
        product_id=defaults["product_id"],
        pricing_model="cpm",
        rate=Decimal("10.0"),
        currency="USD",
        is_fixed=True,
    )
    product.pricing_options = [pricing]
    return product


# ---------------------------------------------------------------------------
# 1. Schema validation: delivery_measurement is REQUIRED
# ---------------------------------------------------------------------------


class TestDeliveryMeasurementRequired:
    """Verify that delivery_measurement is required per AdCP spec."""

    def test_product_without_delivery_measurement_fails_validation(self):
        """Omitting delivery_measurement raises ValidationError."""
        with pytest.raises(ValidationError, match="delivery_measurement"):
            Product(
                product_id="no_dm",
                name="No DM",
                description="Missing delivery_measurement",
                format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
                delivery_type="guaranteed",
                publisher_properties=[create_test_publisher_properties_by_tag()],
                pricing_options=[create_test_cpm_pricing_option()],
                # delivery_measurement intentionally omitted
            )

    def test_product_with_delivery_measurement_passes(self):
        """Product with delivery_measurement passes validation."""
        product = _make_schema_product()
        assert product.delivery_measurement is not None

    def test_delivery_measurement_requires_provider(self):
        """delivery_measurement must have a provider field."""
        with pytest.raises(ValidationError, match="provider"):
            _make_schema_product(delivery_measurement={"notes": "no provider"})


# ---------------------------------------------------------------------------
# 2. Adapter default delivery_measurement resolution
# ---------------------------------------------------------------------------


class TestAdapterDefaultDeliveryMeasurement:
    """Verify adapter-specific defaults for delivery_measurement."""

    def test_gam_default_provider(self):
        """GAM adapter defaults to google_ad_manager provider."""
        dm = get_adapter_default_delivery_measurement("google_ad_manager")
        assert dm["provider"] == "google_ad_manager"

    def test_mock_default_provider(self):
        """Mock adapter defaults to mock provider."""
        dm = get_adapter_default_delivery_measurement("mock")
        assert dm["provider"] == "mock"

    def test_unknown_adapter_falls_back_to_publisher(self):
        """Unknown adapter type falls back to publisher."""
        dm = get_adapter_default_delivery_measurement("unknown_adapter")
        assert dm["provider"] == "publisher"

    def test_empty_adapter_type_falls_back_to_publisher(self):
        """Empty string adapter type falls back to publisher."""
        dm = get_adapter_default_delivery_measurement("")
        assert dm["provider"] == "publisher"


# ---------------------------------------------------------------------------
# 3. Product conversion applies adapter defaults
# ---------------------------------------------------------------------------


class TestConversionDeliveryMeasurementDefaults:
    """Verify convert_product_model_to_schema applies adapter defaults."""

    def test_conversion_preserves_existing_delivery_measurement(self):
        """Products with delivery_measurement keep their configured value."""
        db_product = _make_db_product(
            delivery_measurement={"provider": "ias", "notes": "IAS viewability"},
        )
        schema_product = convert_product_model_to_schema(db_product, adapter_type="google_ad_manager")
        assert schema_product.delivery_measurement.provider == "ias"
        assert schema_product.delivery_measurement.notes == "IAS viewability"

    def test_conversion_uses_gam_default_when_missing(self):
        """Products without delivery_measurement get GAM default when adapter is GAM."""
        db_product = _make_db_product(delivery_measurement=None)
        schema_product = convert_product_model_to_schema(db_product, adapter_type="google_ad_manager")
        assert schema_product.delivery_measurement.provider == "google_ad_manager"

    def test_conversion_uses_mock_default_when_missing(self):
        """Products without delivery_measurement get mock default when adapter is mock."""
        db_product = _make_db_product(delivery_measurement=None)
        schema_product = convert_product_model_to_schema(db_product, adapter_type="mock")
        assert schema_product.delivery_measurement.provider == "mock"

    def test_conversion_uses_publisher_fallback_when_no_adapter(self):
        """Products without delivery_measurement get publisher fallback when no adapter_type."""
        db_product = _make_db_product(delivery_measurement=None)
        schema_product = convert_product_model_to_schema(db_product, adapter_type=None)
        assert schema_product.delivery_measurement.provider == "publisher"

    def test_conversion_uses_publisher_fallback_for_unknown_adapter(self):
        """Products without delivery_measurement get publisher fallback for unknown adapter."""
        db_product = _make_db_product(delivery_measurement=None)
        schema_product = convert_product_model_to_schema(db_product, adapter_type="unknown")
        assert schema_product.delivery_measurement.provider == "publisher"


# ---------------------------------------------------------------------------
# 4. model_dump always includes delivery_measurement
# ---------------------------------------------------------------------------


class TestDeliveryMeasurementSerialization:
    """Verify delivery_measurement always appears in serialized output."""

    def test_model_dump_includes_delivery_measurement(self):
        """delivery_measurement always appears in model_dump output."""
        product = _make_schema_product(
            delivery_measurement={"provider": "ias"},
        )
        dump = product.model_dump()
        assert "delivery_measurement" in dump
        assert dump["delivery_measurement"]["provider"] == "ias"

    def test_model_dump_includes_delivery_measurement_with_notes(self):
        """delivery_measurement with notes includes both fields."""
        product = _make_schema_product(
            delivery_measurement={"provider": "moat", "notes": "Viewability measurement"},
        )
        dump = product.model_dump()
        assert dump["delivery_measurement"]["provider"] == "moat"
        assert dump["delivery_measurement"]["notes"] == "Viewability measurement"

    def test_model_dump_delivery_measurement_is_core_field(self):
        """delivery_measurement is in core_fields and always serialized."""
        product = _make_schema_product()
        dump = product.model_dump()
        # Core fields are always present even when other optional fields are null
        assert "delivery_measurement" in dump
        assert "product_id" in dump
        assert "name" in dump


# ---------------------------------------------------------------------------
# 5. Adapter class attributes
# ---------------------------------------------------------------------------


class TestAdapterClassAttributes:
    """Verify adapter classes declare default_delivery_measurement."""

    def test_gam_adapter_has_delivery_measurement(self):
        """GoogleAdManager declares default_delivery_measurement."""
        from src.adapters.google_ad_manager import GoogleAdManager

        assert hasattr(GoogleAdManager, "default_delivery_measurement")
        assert GoogleAdManager.default_delivery_measurement["provider"] == "google_ad_manager"

    def test_mock_adapter_has_delivery_measurement(self):
        """MockAdServer declares default_delivery_measurement."""
        from src.adapters.mock_ad_server import MockAdServer

        assert hasattr(MockAdServer, "default_delivery_measurement")
        assert MockAdServer.default_delivery_measurement["provider"] == "mock"

    def test_base_adapter_has_default(self):
        """AdServerAdapter base class has publisher default."""
        from src.adapters.base import AdServerAdapter

        assert hasattr(AdServerAdapter, "default_delivery_measurement")
        assert AdServerAdapter.default_delivery_measurement["provider"] == "publisher"
