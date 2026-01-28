"""Unit tests for get_adcp_capabilities tool.

Tests the capabilities endpoint that returns what this sales agent supports
per the AdCP spec.
"""

from unittest.mock import MagicMock, patch

import pytest
from adcp.types import GetAdcpCapabilitiesResponse
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
    SupportedProtocol,
)


class TestGetAdcpCapabilitiesSchema:
    """Test GetAdcpCapabilitiesResponse schema validation."""

    def test_response_requires_adcp_field(self):
        """Test that response requires adcp field."""
        # Must have adcp and supported_protocols per spec
        with pytest.raises(ValueError):
            GetAdcpCapabilitiesResponse(supported_protocols=[SupportedProtocol.media_buy])

    def test_response_requires_supported_protocols(self):
        """Test that response requires supported_protocols field."""
        from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
            Adcp,
            MajorVersion,
        )

        # Must have supported_protocols (non-empty list)
        with pytest.raises(ValueError):
            GetAdcpCapabilitiesResponse(
                adcp=Adcp(major_versions=[MajorVersion(root=3)]),
                supported_protocols=[],  # Empty not allowed
            )

    def test_valid_minimal_response(self):
        """Test creating a valid minimal response."""
        from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
            Adcp,
            MajorVersion,
        )

        response = GetAdcpCapabilitiesResponse(
            adcp=Adcp(major_versions=[MajorVersion(root=3)]),
            supported_protocols=[SupportedProtocol.media_buy],
        )

        assert response.adcp is not None
        assert len(response.adcp.major_versions) == 1
        assert response.adcp.major_versions[0].root == 3
        assert SupportedProtocol.media_buy in response.supported_protocols

    def test_response_with_media_buy_capabilities(self):
        """Test creating response with media_buy capabilities."""
        from adcp.types.generated_poc.core.media_buy_features import MediaBuyFeatures
        from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
            Adcp,
            Execution,
            MajorVersion,
            MediaBuy,
            Portfolio,
            PublisherDomain,
            Targeting,
        )

        response = GetAdcpCapabilitiesResponse(
            adcp=Adcp(major_versions=[MajorVersion(root=3)]),
            supported_protocols=[SupportedProtocol.media_buy],
            media_buy=MediaBuy(
                portfolio=Portfolio(
                    description="Test portfolio",
                    publisher_domains=[PublisherDomain(root="example.com")],
                ),
                features=MediaBuyFeatures(
                    content_standards=True,
                    inline_creative_management=True,
                    property_list_filtering=True,
                ),
                execution=Execution(
                    targeting=Targeting(
                        geo_countries=True,
                        geo_regions=True,
                    ),
                ),
            ),
        )

        assert response.media_buy is not None
        assert response.media_buy.portfolio is not None
        assert len(response.media_buy.portfolio.publisher_domains) == 1
        assert response.media_buy.features is not None
        assert response.media_buy.features.content_standards is True


class TestGetAdcpCapabilitiesImports:
    """Test that get_adcp_capabilities can be imported correctly."""

    def test_capabilities_module_imports(self):
        """Test that the capabilities module can be imported."""
        from src.core.tools import capabilities

        assert capabilities is not None

    def test_impl_function_exists(self):
        """Test that the impl function exists."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        assert callable(_get_adcp_capabilities_impl)

    def test_mcp_wrapper_exists(self):
        """Test that the MCP wrapper function exists."""
        from src.core.tools.capabilities import get_adcp_capabilities

        assert callable(get_adcp_capabilities)

    def test_raw_function_exists(self):
        """Test that the raw function exists."""
        from src.core.tools.capabilities import get_adcp_capabilities_raw

        assert callable(get_adcp_capabilities_raw)

    def test_raw_function_exported_from_tools(self):
        """Test that the raw function is exported from tools module."""
        from src.core.tools import get_adcp_capabilities_raw

        assert callable(get_adcp_capabilities_raw)


class TestGetAdcpCapabilitiesImpl:
    """Test the _get_adcp_capabilities_impl function."""

    def test_impl_returns_response_without_context(self):
        """Test that impl returns minimal response when no context is available."""
        from src.core.config_loader import current_tenant
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        # Reset tenant context to ensure clean state (tests may have set it)
        current_tenant.set(None)

        # Call without context - should return minimal response
        response = _get_adcp_capabilities_impl(None, None)

        assert isinstance(response, GetAdcpCapabilitiesResponse)
        assert response.adcp is not None
        assert response.adcp.major_versions[0].root == 3
        assert SupportedProtocol.media_buy in response.supported_protocols

    def test_impl_returns_valid_adcp_response(self):
        """Test that impl response can be serialized to valid JSON."""
        from src.core.config_loader import current_tenant
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        # Reset tenant context to ensure clean state
        current_tenant.set(None)

        response = _get_adcp_capabilities_impl(None, None)

        # Should be able to serialize - use mode="json" for JSON-compatible output
        data = response.model_dump(mode="json")

        assert "adcp" in data
        assert "supported_protocols" in data
        assert data["supported_protocols"] == ["media_buy"]


class TestGetAdcpCapabilitiesWithTenant:
    """Test get_adcp_capabilities with mocked tenant context."""

    def test_impl_returns_full_response_with_tenant(self):
        """Test that impl returns full capabilities when tenant context is available."""
        from src.core.config_loader import current_tenant
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        # Set up mock tenant
        mock_tenant = {
            "tenant_id": "test-tenant-123",
            "name": "Test Publisher",
            "subdomain": "testpub",
            "advertising_policy": {"description": "Family-friendly content only"},
        }
        current_tenant.set(mock_tenant)

        try:
            # Mock the database session to avoid actual DB calls
            with patch("src.core.tools.capabilities.get_db_session") as mock_db:
                mock_session = MagicMock()
                mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
                mock_db.return_value.__exit__ = MagicMock(return_value=False)
                mock_session.scalars.return_value.all.return_value = []

                # Mock get_principal_from_context to return tenant info
                with patch("src.core.tools.capabilities.get_principal_from_context") as mock_auth:
                    mock_auth.return_value = (None, mock_tenant)

                    response = _get_adcp_capabilities_impl(None, None)

                    # Verify full response structure
                    assert response.adcp is not None
                    assert response.adcp.major_versions[0].root == 3
                    assert SupportedProtocol.media_buy in response.supported_protocols

                    # Should have media_buy capabilities with portfolio
                    assert response.media_buy is not None
                    assert response.media_buy.portfolio is not None
                    assert response.media_buy.portfolio.description == "Advertising inventory from Test Publisher"

                    # Should have features
                    assert response.media_buy.features is not None
                    assert response.media_buy.features.inline_creative_management is True

                    # Should have execution with targeting
                    assert response.media_buy.execution is not None
                    assert response.media_buy.execution.targeting is not None
        finally:
            # Reset tenant context
            current_tenant.set(None)

    def test_impl_includes_targeting_from_adapter(self):
        """Test that targeting capabilities come from adapter."""
        from src.adapters.base import TargetingCapabilities
        from src.core.config_loader import current_tenant
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        mock_tenant = {
            "tenant_id": "test-tenant-456",
            "name": "GAM Publisher",
            "subdomain": "gampub",
        }
        current_tenant.set(mock_tenant)

        try:
            # Create mock adapter with targeting capabilities
            mock_adapter = MagicMock()
            mock_adapter.default_channels = ["display", "video"]
            mock_adapter.get_targeting_capabilities.return_value = TargetingCapabilities(
                geo_countries=True,
                geo_regions=True,
                nielsen_dma=True,
                us_zip=True,
            )

            with patch("src.core.tools.capabilities.get_db_session") as mock_db:
                mock_session = MagicMock()
                mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
                mock_db.return_value.__exit__ = MagicMock(return_value=False)
                mock_session.scalars.return_value.all.return_value = []

                with patch("src.core.tools.capabilities.get_principal_from_context") as mock_auth:
                    mock_auth.return_value = ("principal-123", mock_tenant)

                    with patch("src.core.tools.capabilities.get_principal_object") as mock_principal:
                        mock_principal.return_value = MagicMock()

                        with patch("src.core.tools.capabilities.get_adapter") as mock_get_adapter:
                            mock_get_adapter.return_value = mock_adapter

                            response = _get_adcp_capabilities_impl(None, None)

                            # Verify targeting from adapter
                            assert response.media_buy is not None
                            assert response.media_buy.execution is not None
                            targeting = response.media_buy.execution.targeting
                            assert targeting is not None
                            assert targeting.geo_countries is True
                            assert targeting.geo_regions is True

                            # Should have geo_metros with nielsen_dma
                            assert targeting.geo_metros is not None
                            assert targeting.geo_metros.nielsen_dma is True

                            # Should have geo_postal_areas with us_zip
                            assert targeting.geo_postal_areas is not None
                            assert targeting.geo_postal_areas.us_zip is True
        finally:
            current_tenant.set(None)


class TestGetAdcpCapabilitiesA2AIntegration:
    """Test A2A integration for get_adcp_capabilities."""

    def test_skill_in_discovery_skills(self):
        """Test that get_adcp_capabilities is in DISCOVERY_SKILLS."""
        from src.a2a_server.adcp_a2a_server import DISCOVERY_SKILLS

        assert "get_adcp_capabilities" in DISCOVERY_SKILLS

    def test_skill_handler_exists(self):
        """Test that the skill handler method exists."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
        assert hasattr(handler, "_handle_get_adcp_capabilities_skill")
        assert callable(handler._handle_get_adcp_capabilities_skill)
