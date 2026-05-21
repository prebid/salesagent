"""Tests for adapter schema infrastructure."""

import pytest
from pydantic import ValidationError

from src.adapters import (
    ADAPTER_REGISTRY,
    AdapterCapabilities,
    AdapterSchemas,
    BaseConnectionConfig,
    BaseProductConfig,
    TargetingCapabilities,
    get_adapter_schemas,
)
from src.adapters.mock_ad_server import MockConnectionConfig, MockProductConfig

pytestmark = pytest.mark.unit


class TestBaseSchemas:
    """Tests for base schema classes."""

    def test_base_connection_config_defaults(self):
        """BaseConnectionConfig should have sensible defaults."""
        config = BaseConnectionConfig()
        assert config.manual_approval_required is False

    def test_base_connection_config_forbids_extra_fields(self):
        """BaseConnectionConfig should reject unknown fields."""
        with pytest.raises(ValidationError) as exc_info:
            BaseConnectionConfig(unknown_field="value")
        assert "extra_forbidden" in str(exc_info.value)

    def test_base_product_config_empty(self):
        """BaseProductConfig should allow empty instantiation."""
        config = BaseProductConfig()
        assert config is not None


class TestMockSchemas:
    """Tests for Mock adapter schemas."""

    def test_mock_connection_config_defaults(self):
        """MockConnectionConfig should have expected defaults."""
        config = MockConnectionConfig()
        assert config.manual_approval_required is False
        assert config.dry_run is False

    def test_mock_connection_config_custom_values(self):
        """MockConnectionConfig should accept custom values."""
        config = MockConnectionConfig(
            manual_approval_required=True,
            dry_run=True,
        )
        assert config.manual_approval_required is True
        assert config.dry_run is True

    def test_mock_product_config_defaults(self):
        """MockProductConfig should have simulation defaults (percents)."""
        config = MockProductConfig()
        assert config.daily_impressions == 100000
        assert config.fill_rate == 85.0
        assert config.ctr == 0.5
        assert config.viewability_rate == 70.0
        assert config.test_mode == "normal"
        assert config.latency_ms == 50
        assert config.error_rate == 0.1
        assert config.verbose_logging is False
        assert config.predictable_ids is False
        assert config.delivery_simulation.enabled is False

    def test_mock_product_config_validation(self):
        """MockProductConfig should validate field constraints (percents 0-100)."""
        # fill_rate must be between 0 and 100
        with pytest.raises(ValidationError):
            MockProductConfig(fill_rate=101.0)

        with pytest.raises(ValidationError):
            MockProductConfig(fill_rate=-1.0)

        # daily_impressions must be non-negative
        with pytest.raises(ValidationError):
            MockProductConfig(daily_impressions=-100)

        # viewability_rate must be between 0 and 100
        with pytest.raises(ValidationError):
            MockProductConfig(viewability_rate=101.0)

        # error_rate must be between 0 and 100
        with pytest.raises(ValidationError):
            MockProductConfig(error_rate=-0.1)

    def test_mock_product_config_round_trip(self):
        """MockProductConfig should round-trip through model_dump/model_validate."""
        config = MockProductConfig(
            daily_impressions=50000,
            fill_rate=75.0,
            ctr=1.5,
            viewability_rate=60.0,
            test_mode="stress",
            delivery_simulation={"enabled": True, "time_acceleration": 1800, "update_interval_seconds": 2.0},
        )
        dumped = config.model_dump()
        restored = MockProductConfig.model_validate(dumped)
        assert restored.fill_rate == 75.0
        assert restored.test_mode == "stress"
        assert restored.delivery_simulation.enabled is True
        assert restored.delivery_simulation.time_acceleration == 1800


class TestAdapterCapabilities:
    """Tests for AdapterCapabilities dataclass."""

    def test_default_capabilities(self):
        """AdapterCapabilities should have sensible defaults."""
        caps = AdapterCapabilities()
        assert caps.supports_inventory_sync is False
        assert caps.supports_inventory_profiles is False
        assert caps.inventory_entity_label == "Items"
        assert caps.supports_custom_targeting is False
        assert caps.supports_geo_targeting is True
        assert caps.supports_dynamic_products is False
        assert caps.supported_pricing_models is None
        assert caps.supports_webhooks is False
        assert caps.supports_realtime_reporting is False

    def test_custom_capabilities(self):
        """AdapterCapabilities should accept custom values."""
        caps = AdapterCapabilities(
            supports_inventory_sync=True,
            supports_inventory_profiles=True,
            inventory_entity_label="Ad Units",
            supports_custom_targeting=True,
            supported_pricing_models=["CPM", "FLAT_RATE"],
        )
        assert caps.supports_inventory_sync is True
        assert caps.inventory_entity_label == "Ad Units"
        assert caps.supported_pricing_models == ["CPM", "FLAT_RATE"]


class TestTargetingCapabilities:
    """Tests for TargetingCapabilities dataclass."""

    def test_default_targeting_capabilities(self):
        """TargetingCapabilities should default to all False."""
        caps = TargetingCapabilities()
        assert caps.geo_countries is False
        assert caps.geo_regions is False
        assert caps.nielsen_dma is False
        assert caps.us_zip is False

    def test_custom_targeting_capabilities(self):
        """TargetingCapabilities should accept custom values."""
        caps = TargetingCapabilities(
            geo_countries=True,
            geo_regions=True,
            nielsen_dma=True,
        )
        assert caps.geo_countries is True
        assert caps.geo_regions is True
        assert caps.nielsen_dma is True


class TestAdapterRegistry:
    """Tests for the adapter registry."""

    def test_mock_adapter_registered(self):
        """Mock adapter should be registered."""
        assert "mock" in ADAPTER_REGISTRY

    def test_get_adapter_schemas_mock(self):
        """get_adapter_schemas should return Mock adapter schemas."""
        schemas = get_adapter_schemas("mock")
        assert schemas is not None
        assert isinstance(schemas, AdapterSchemas)
        assert schemas.connection_config is not None
        assert schemas.product_config is not None
        assert schemas.capabilities is not None

    def test_get_adapter_schemas_unknown(self):
        """get_adapter_schemas should return None for unknown adapters."""
        schemas = get_adapter_schemas("nonexistent_adapter")
        assert schemas is None

    def test_mock_capabilities_pricing_models(self):
        """Mock adapter should declare all pricing models."""
        schemas = get_adapter_schemas("mock")
        assert schemas.capabilities is not None
        assert schemas.capabilities.supported_pricing_models is not None
        expected_models = ["cpm", "vcpm", "cpcv", "cpp", "cpc", "cpv", "flat_rate"]
        assert set(schemas.capabilities.supported_pricing_models) == set(expected_models)


class TestSchemaJsonSerialization:
    """Tests for JSON schema generation from Pydantic models."""

    def test_mock_connection_config_json_schema(self):
        """MockConnectionConfig should generate valid JSON schema."""
        schema = MockConnectionConfig.model_json_schema()
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "dry_run" in schema["properties"]
        assert "manual_approval_required" in schema["properties"]

    def test_mock_product_config_json_schema(self):
        """MockProductConfig should generate valid JSON schema."""
        schema = MockProductConfig.model_json_schema()
        assert "properties" in schema
        assert "daily_impressions" in schema["properties"]
        assert "fill_rate" in schema["properties"]
        assert "viewability_rate" in schema["properties"]
        # Rate fields use percents (0–100)
        fill_rate_schema = schema["properties"]["fill_rate"]
        assert fill_rate_schema.get("minimum") == 0.0
        assert fill_rate_schema.get("maximum") == 100.0

    def test_schema_descriptions_present(self):
        """Schema fields should have descriptions."""
        schema = MockConnectionConfig.model_json_schema()
        dry_run_schema = schema["properties"]["dry_run"]
        assert "description" in dry_run_schema
        assert len(dry_run_schema["description"]) > 0
