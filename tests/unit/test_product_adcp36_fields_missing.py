"""Regression test: 6 adcp 3.6.0 Product fields have no database columns.

Bug: salesagent-qo8a

After upgrading adcp from 3.2.0 to 3.6.0, the Product Pydantic schema inherits
6 new fields from the library that have no corresponding database columns:
- catalog_match (CatalogMatch | None)
- catalog_types (list[CatalogType] | None)
- conversion_tracking (ConversionTracking | None)
- data_provider_signals (list[DataProviderSignalSelector] | None)
- forecast (DeliveryForecast | None)
- signal_targeting_allowed (bool | None)

Without DB columns, these fields:
1. Cannot be persisted when received from buyers
2. Cannot be queried/filtered
3. Will silently drop data on schema → DB → schema roundtrip
"""

from src.core.database.models import Product as ProductModel
from src.core.schemas import Product as ProductSchema

ADCP_36_PRODUCT_FIELDS = {
    "catalog_match",
    "catalog_types",
    "conversion_tracking",
    "data_provider_signals",
    "forecast",
    "signal_targeting_allowed",
}


class TestProductAdcp36FieldsPersistence:
    """Verify adcp 3.6.0 Product fields exist in both schema and database."""

    def test_adcp_36_fields_exist_in_schema(self):
        """All 6 fields should exist in the Product Pydantic schema (from adcp library)."""
        schema_fields = set(ProductSchema.model_fields.keys())
        missing = ADCP_36_PRODUCT_FIELDS - schema_fields
        assert not missing, f"Fields missing from Product schema: {missing}"

    def test_adcp_36_fields_exist_in_database(self):
        """All 6 fields should have corresponding database columns.

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
        )

        dumped = product.model_dump()
        assert dumped["signal_targeting_allowed"] is True, "signal_targeting_allowed should survive model_dump()"

        # Restore from dict
        restored = ProductSchema(**dumped)
        assert restored.signal_targeting_allowed is True
