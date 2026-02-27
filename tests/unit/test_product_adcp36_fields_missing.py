"""Regression test: 7 adcp 3.6.0 Product fields have no database columns.

Bug: salesagent-qo8a, salesagent-kntn

After upgrading adcp from 3.2.0 to 3.6.0, the Product Pydantic schema inherits
7 new fields from the library that have no corresponding database columns:
- catalog_match (CatalogMatch | None)
- catalog_types (list[CatalogType] | None)
- conversion_tracking (ConversionTracking | None)
- data_provider_signals (list[DataProviderSignalSelector] | None)
- forecast (DeliveryForecast | None)
- property_targeting_allowed (bool | None)
- signal_targeting_allowed (bool | None)

Without DB columns, these fields:
1. Cannot be persisted when received from buyers
2. Cannot be queried/filtered
3. Will silently drop data on schema → DB → schema roundtrip
"""

from unittest.mock import MagicMock

from src.core.database.models import Product as ProductModel
from src.core.product_conversion import convert_product_model_to_schema
from src.core.schemas import Product as ProductSchema

ADCP_36_PRODUCT_FIELDS = {
    "catalog_match",
    "catalog_types",
    "conversion_tracking",
    "data_provider_signals",
    "forecast",
    "property_targeting_allowed",
    "signal_targeting_allowed",
}


class TestProductAdcp36FieldsPersistence:
    """Verify adcp 3.6.0 Product fields exist in both schema and database."""

    def test_adcp_36_fields_exist_in_schema(self):
        """All 7 fields should exist in the Product Pydantic schema (from adcp library)."""
        schema_fields = set(ProductSchema.model_fields.keys())
        missing = ADCP_36_PRODUCT_FIELDS - schema_fields
        assert not missing, f"Fields missing from Product schema: {missing}"

    def test_adcp_36_fields_exist_in_database(self):
        """All 7 fields should have corresponding database columns.

        This is the core failure: these fields are in the schema but not in the DB.
        Until the migration is added, this test will FAIL.
        """
        db_columns = {col.name for col in ProductModel.__table__.columns}
        missing = ADCP_36_PRODUCT_FIELDS - db_columns
        assert not missing, (
            f"adcp 3.6.0 Product fields missing from database: {missing}. "
            f"These fields cannot be persisted without DB columns."
        )

    def test_roundtrip_data_preservation(self):
        """Fields set in the schema should survive a schema → dict → schema roundtrip.

        This verifies the fields are real schema fields (not computed/transient)
        and that setting them produces values that can be serialized and restored.
        """
        product = ProductSchema(
            product_id="roundtrip_test_001",
            name="Roundtrip Test",
            description="Testing data preservation",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            delivery_type="non_guaranteed",
            publisher_properties=[
                {
                    "selection_type": "by_id",
                    "publisher_domain": "example.com",
                    "property_ids": ["all_inventory"],
                }
            ],
            pricing_options=[
                {
                    "pricing_option_id": "cpm_usd_fixed",
                    "pricing_model": "cpm",
                    "currency": "USD",
                    "fixed_price": 10.0,
                }
            ],
            delivery_measurement={"provider": "publisher", "notes": "test"},
            signal_targeting_allowed=True,
            property_targeting_allowed=True,
        )

        dumped = product.model_dump()
        assert dumped["signal_targeting_allowed"] is True, "signal_targeting_allowed should survive model_dump()"
        assert dumped["property_targeting_allowed"] is True, "property_targeting_allowed should survive model_dump()"

        # Restore from dict
        restored = ProductSchema(**dumped)
        assert restored.signal_targeting_allowed is True
        assert restored.property_targeting_allowed is True

    def test_property_targeting_allowed_defaults_to_false(self):
        """property_targeting_allowed should default to False when not set."""
        product = ProductSchema(
            product_id="default_test_001",
            name="Default Test",
            description="Testing default behavior",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            delivery_type="non_guaranteed",
            publisher_properties=[
                {
                    "selection_type": "by_id",
                    "publisher_domain": "example.com",
                    "property_ids": ["all_inventory"],
                }
            ],
            pricing_options=[
                {
                    "pricing_option_id": "cpm_usd_fixed",
                    "pricing_model": "cpm",
                    "currency": "USD",
                    "fixed_price": 10.0,
                }
            ],
            delivery_measurement={"provider": "publisher"},
        )
        assert product.property_targeting_allowed is False


class TestPropertyTargetingAllowedConversion:
    """Verify property_targeting_allowed survives DB model → schema conversion."""

    def _make_product_model(self, **overrides):
        """Create a mock product model with required fields."""
        model = MagicMock()
        model.product_id = "conv_test_001"
        model.name = "Conversion Test"
        model.description = "Testing conversion"
        model.delivery_type = "non_guaranteed"
        model.effective_format_ids = [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}]
        model.effective_properties = [
            {
                "selection_type": "by_id",
                "publisher_domain": "example.com",
                "property_ids": ["all_inventory"],
            }
        ]
        model.pricing_options = [
            MagicMock(
                pricing_model="cpm",
                is_fixed=True,
                currency="USD",
                rate=10.0,
                price_guidance=None,
                min_spend_per_package=None,
                parameters=None,
            )
        ]
        model.delivery_measurement = {"provider": "publisher"}
        model.measurement = None
        model.creative_policy = None
        model.countries = None
        model.channels = None
        model.product_card = None
        model.product_card_detailed = None
        model.placements = None
        model.reporting_capabilities = None
        model.is_custom = False
        model.signal_targeting_allowed = None
        model.catalog_match = None
        model.catalog_types = None
        model.conversion_tracking = None
        model.data_provider_signals = None
        model.forecast = None
        model.effective_implementation_config = None
        model.allowed_principal_ids = None
        # Apply overrides
        for k, v in overrides.items():
            setattr(model, k, v)
        return model

    def test_conversion_includes_property_targeting_allowed_true(self):
        """property_targeting_allowed=True on DB model should appear in converted schema."""
        model = self._make_product_model(property_targeting_allowed=True)
        product = convert_product_model_to_schema(model)
        assert product.property_targeting_allowed is True

    def test_conversion_omits_property_targeting_allowed_when_none(self):
        """property_targeting_allowed=None on DB model should result in False (library default)."""
        model = self._make_product_model(property_targeting_allowed=None)
        product = convert_product_model_to_schema(model)
        assert product.property_targeting_allowed is False
