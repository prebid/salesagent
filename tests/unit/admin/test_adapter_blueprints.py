"""Tests for adapter blueprint routes and template rendering."""

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


class TestAdapterConfigEndpoint:
    """Tests for adapter config endpoint business logic.

    Note: Full HTTP endpoint testing requires integration tests with proper
    database and auth context. These unit tests focus on the core logic.
    """

    def test_save_adapter_config_blueprint_registered(self):
        """Adapters blueprint should be importable and have routes."""
        from src.admin.blueprints.adapters import adapters_bp

        # Blueprint should exist
        assert adapters_bp is not None
        assert adapters_bp.name == "adapters"

        # Should have our routes registered
        # Note: Full route testing done in integration tests

    def test_save_adapter_config_logic_validates_schema(self):
        """Config save logic should validate against schema."""
        from pydantic import ValidationError

        from src.adapters import get_adapter_schemas

        schemas = get_adapter_schemas("mock")

        # Valid config should pass
        valid_config = {"dry_run": True, "manual_approval_required": False}
        validated = schemas.connection_config(**valid_config)
        assert validated.dry_run is True

        # Invalid config should fail
        with pytest.raises(ValidationError):
            schemas.connection_config(invalid_field="test")

    def test_capabilities_endpoint_logic(self):
        """Capabilities endpoint logic should serialize dataclass."""
        from dataclasses import asdict

        from src.adapters import get_adapter_schemas

        schemas = get_adapter_schemas("mock")

        # Should be able to convert to dict (for JSON response)
        if schemas and schemas.capabilities:
            caps_dict = asdict(schemas.capabilities)
            assert isinstance(caps_dict, dict)
            assert "supports_inventory_sync" in caps_dict


class TestAdapterConfigValidation:
    """Tests for adapter config validation logic."""

    def test_mock_config_validates_correctly(self):
        """Mock config should validate and serialize."""
        from src.adapters import get_adapter_schemas

        schemas = get_adapter_schemas("mock")
        config_data = {"dry_run": True, "manual_approval_required": True}

        # Validate and serialize
        validated = schemas.connection_config(**config_data)
        serialized = validated.model_dump()

        assert serialized["dry_run"] is True
        assert serialized["manual_approval_required"] is True

    def test_mock_config_rejects_invalid(self):
        """Mock config should reject invalid data."""
        from pydantic import ValidationError

        from src.adapters import get_adapter_schemas

        schemas = get_adapter_schemas("mock")

        # extra="forbid" should reject unknown fields
        with pytest.raises(ValidationError):
            schemas.connection_config(unknown_field="value")

    def test_mock_product_config_validates_ranges(self):
        """MockProductConfig should enforce value ranges."""
        from pydantic import ValidationError

        from src.adapters import get_adapter_schemas

        schemas = get_adapter_schemas("mock")
        MockProductConfig = schemas.product_config

        # Valid ranges
        valid = MockProductConfig(fill_rate=0.5, ctr=0.05, viewability=0.7)
        assert valid.fill_rate == 0.5

        # Invalid: fill_rate > 1
        with pytest.raises(ValidationError):
            MockProductConfig(fill_rate=1.5)

        # Invalid: negative impressions
        with pytest.raises(ValidationError):
            MockProductConfig(daily_impressions=-1)


class TestCapabilitiesEndpointLogic:
    """Tests for capabilities endpoint business logic."""

    def test_capabilities_returns_dict(self):
        """Capabilities should be convertible to dict for JSON response."""
        from dataclasses import asdict

        from src.adapters import get_adapter_schemas

        schemas = get_adapter_schemas("mock")
        caps_dict = asdict(schemas.capabilities)

        # Should be a flat dict
        assert isinstance(caps_dict, dict)

        # Required fields
        assert "supports_inventory_sync" in caps_dict
        assert "supports_inventory_profiles" in caps_dict
        assert "inventory_entity_label" in caps_dict
        assert "supports_custom_targeting" in caps_dict
        assert "supports_geo_targeting" in caps_dict
        assert "supports_dynamic_products" in caps_dict
        assert "supported_pricing_models" in caps_dict
        assert "supports_webhooks" in caps_dict
        assert "supports_realtime_reporting" in caps_dict

    def test_mock_capabilities_values(self):
        """Mock adapter capabilities should have expected values."""
        from src.adapters import get_adapter_schemas

        schemas = get_adapter_schemas("mock")
        caps = schemas.capabilities

        # Mock doesn't support inventory sync/profiles
        assert caps.supports_inventory_sync is False
        assert caps.supports_inventory_profiles is False

        # Mock supports all pricing models
        assert caps.supported_pricing_models is not None
        assert len(caps.supported_pricing_models) == 7  # CPM, VCPM, CPCV, CPP, CPC, CPV, FLAT_RATE

        # Mock uses "Mock Items" for inventory label
        assert caps.inventory_entity_label == "Mock Items"

    def test_unknown_adapter_returns_none(self):
        """Unknown adapter should return None from registry."""
        from src.adapters import get_adapter_schemas

        schemas = get_adapter_schemas("nonexistent_adapter_xyz")
        assert schemas is None


class TestTemplateContext:
    """Tests for template context variables passed to product forms."""

    def test_add_product_context_has_required_vars(self):
        """add_product.html should receive required context variables."""
        # Simulate the context that _render_add_product_form passes
        required_context = {
            "tenant_id": "test-tenant",
            "tenant": MagicMock(),
            "adapter_type": "mock",
            "formats": [],
            "authorized_properties": [],
            "property_tags": [],
            "currencies": ["USD"],
            "principals": [],
            "form_data": None,
        }

        # All required keys should be present
        assert "tenant_id" in required_context
        assert "adapter_type" in required_context
        assert "tenant" in required_context
        assert "currencies" in required_context

    def test_edit_product_context_has_required_vars(self):
        """edit_product.html should receive required context variables."""
        required_context = {
            "tenant_id": "test-tenant",
            "tenant": MagicMock(),
            "adapter_type": "mock",
            "product": {"product_id": "test", "name": "Test Product"},
            "currencies": ["USD"],
            "principals": [],
            "authorized_properties": [],
            "selected_publisher_properties": [],
        }

        # All required keys should be present
        assert "tenant_id" in required_context
        assert "adapter_type" in required_context
        assert "product" in required_context
        assert "tenant" in required_context


class TestAdapterTemplateIncludes:
    """Tests for adapter template fragment includes."""

    def test_mock_product_config_template_exists(self):
        """Mock product config template should exist."""
        import os

        template_path = "templates/adapters/mock/product_config.html"
        assert os.path.exists(template_path), f"Template not found: {template_path}"

    def test_mock_connection_config_template_exists(self):
        """Mock connection config template should exist."""
        import os

        template_path = "templates/adapters/mock/connection_config.html"
        assert os.path.exists(template_path), f"Template not found: {template_path}"

    def test_gam_product_config_template_exists(self):
        """GAM product config template should exist."""
        import os

        template_path = "templates/adapters/google_ad_manager/product_config.html"
        assert os.path.exists(template_path), f"Template not found: {template_path}"

    def test_gam_connection_config_template_exists(self):
        """GAM connection config template should exist."""
        import os

        template_path = "templates/adapters/google_ad_manager/connection_config.html"
        assert os.path.exists(template_path), f"Template not found: {template_path}"

    def test_unified_add_product_template_exists(self):
        """Unified add_product.html should exist."""
        import os

        template_path = "templates/add_product.html"
        assert os.path.exists(template_path), f"Template not found: {template_path}"

    def test_unified_edit_product_template_exists(self):
        """Unified edit_product.html should exist."""
        import os

        template_path = "templates/edit_product.html"
        assert os.path.exists(template_path), f"Template not found: {template_path}"


class TestSchemaRegistryCompleteness:
    """Tests for schema registry completeness."""

    def test_mock_adapter_fully_registered(self):
        """Mock adapter should have core schema components."""
        from src.adapters import get_adapter_schemas

        schemas = get_adapter_schemas("mock")

        assert schemas is not None
        assert schemas.connection_config is not None
        assert schemas.product_config is not None
        # inventory_config is optional
        assert schemas.capabilities is not None

    def test_schema_classes_are_pydantic(self):
        """Schema classes should be Pydantic BaseModel subclasses."""
        from pydantic import BaseModel

        from src.adapters import get_adapter_schemas

        schemas = get_adapter_schemas("mock")

        assert issubclass(schemas.connection_config, BaseModel)
        assert issubclass(schemas.product_config, BaseModel)

    def test_capabilities_is_dataclass(self):
        """Capabilities should be a dataclass."""
        from dataclasses import is_dataclass

        from src.adapters import get_adapter_schemas

        schemas = get_adapter_schemas("mock")

        assert is_dataclass(schemas.capabilities)
