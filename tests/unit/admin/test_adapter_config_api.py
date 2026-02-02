"""Tests for adapter configuration API endpoints."""

import pytest

from src.adapters import get_adapter_schemas

pytestmark = pytest.mark.unit


class TestAdapterConfigAPI:
    """Tests for /api/adapter-config endpoint."""

    def test_get_adapter_schemas_mock(self):
        """Should return schemas for mock adapter."""
        schemas = get_adapter_schemas("mock")
        assert schemas is not None
        assert schemas.connection_config is not None
        assert schemas.capabilities is not None

    def test_get_adapter_schemas_unknown(self):
        """Should return None for unknown adapter."""
        schemas = get_adapter_schemas("nonexistent")
        assert schemas is None

    def test_mock_connection_config_validation(self):
        """MockConnectionConfig should validate input."""
        schemas = get_adapter_schemas("mock")
        config_class = schemas.connection_config

        # Valid config
        valid = config_class(dry_run=True, manual_approval_required=False)
        assert valid.dry_run is True
        assert valid.manual_approval_required is False

        # Defaults
        default = config_class()
        assert default.dry_run is False
        assert default.manual_approval_required is False

    def test_mock_capabilities(self):
        """Mock adapter should declare its capabilities."""
        schemas = get_adapter_schemas("mock")
        caps = schemas.capabilities

        assert caps is not None
        assert caps.supports_geo_targeting is True
        assert caps.supported_pricing_models is not None
        assert "cpm" in caps.supported_pricing_models
        assert "flat_rate" in caps.supported_pricing_models


class TestCapabilitiesEndpoint:
    """Tests for /api/adapters/<type>/capabilities endpoint."""

    def test_capabilities_structure(self):
        """Capabilities should be serializable to dict."""
        from dataclasses import asdict

        schemas = get_adapter_schemas("mock")
        caps_dict = asdict(schemas.capabilities)

        assert isinstance(caps_dict, dict)
        assert "supports_inventory_sync" in caps_dict
        assert "supports_geo_targeting" in caps_dict
        assert "supported_pricing_models" in caps_dict
