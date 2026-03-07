"""Unit tests for get_adcp_capabilities tool.

Tests the capabilities endpoint that returns what this sales agent supports
per the AdCP spec.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from adcp.types import GetAdcpCapabilitiesResponse
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
    SupportedProtocol,
)

if TYPE_CHECKING:
    from src.core.resolved_identity import ResolvedIdentity


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

                # Pass identity with tenant info directly (no auth extraction in _impl)
                from src.core.resolved_identity import ResolvedIdentity

                identity = ResolvedIdentity(
                    principal_id=None,
                    tenant_id="test-tenant-123",
                    tenant=mock_tenant,
                    protocol="mcp",
                )
                response = _get_adcp_capabilities_impl(None, identity)

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

                from src.core.resolved_identity import ResolvedIdentity

                identity = ResolvedIdentity(
                    principal_id="principal-123",
                    tenant_id="test-tenant-456",
                    tenant=mock_tenant,
                    protocol="mcp",
                )

                with patch("src.core.tools.capabilities.get_principal_object") as mock_principal:
                    mock_principal.return_value = MagicMock()

                    with patch("src.core.tools.capabilities.get_adapter") as mock_get_adapter:
                        mock_get_adapter.return_value = mock_adapter

                        response = _get_adcp_capabilities_impl(None, identity)

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


# ===========================================================================
# Channel mapping and adapter integration tests
# Reference: beads salesagent-7xc7
# ===========================================================================


def _make_capabilities_identity(
    principal_id: str | None = "principal-123",
    tenant_id: str = "test-tenant",
    tenant: dict | None = None,
) -> ResolvedIdentity:
    """Build a ResolvedIdentity for capabilities tests."""
    from src.core.resolved_identity import ResolvedIdentity

    if tenant is None:
        tenant = {"tenant_id": tenant_id, "name": "Test Publisher", "subdomain": "testpub"}
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=tenant,
        protocol="mcp",
    )


def _patch_capabilities_deps(
    adapter=None,
    db_partners=None,
):
    """Return a context manager stack patching common capabilities dependencies.

    Args:
        adapter: Mock adapter to return from get_adapter (None = no adapter).
        db_partners: List of mock PublisherPartner objects from DB query.
    """
    from contextlib import ExitStack

    stack = ExitStack()

    # Mock database session
    mock_session = MagicMock()
    mock_session.scalars.return_value.all.return_value = db_partners or []
    mock_db = MagicMock()
    mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_db.return_value.__exit__ = MagicMock(return_value=False)
    stack.enter_context(patch("src.core.tools.capabilities.get_db_session", mock_db))

    # Mock log_tool_activity (no-op)
    stack.enter_context(patch("src.core.tools.capabilities.log_tool_activity"))

    # Mock get_principal_object
    if adapter is not None:
        stack.enter_context(patch("src.core.tools.capabilities.get_principal_object", return_value=MagicMock()))
        stack.enter_context(patch("src.core.tools.capabilities.get_adapter", return_value=adapter))
    else:
        stack.enter_context(patch("src.core.tools.capabilities.get_principal_object", return_value=None))

    return stack


class TestChannelMapping:
    """Test CHANNEL_MAPPING integration in _get_adcp_capabilities_impl."""

    def test_channel_aliases_video_maps_to_olv(self):
        """Video channel alias maps to MediaChannel.olv in response."""
        from adcp.types.generated_poc.enums.channels import MediaChannel

        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        mock_adapter = MagicMock()
        mock_adapter.default_channels = ["video"]
        mock_adapter.get_targeting_capabilities.return_value = None

        identity = _make_capabilities_identity()
        stack = _patch_capabilities_deps(adapter=mock_adapter)

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.media_buy is not None
        assert response.media_buy.portfolio is not None
        assert MediaChannel.olv in response.media_buy.portfolio.primary_channels

    def test_channel_aliases_audio_maps_to_streaming_audio(self):
        """Audio channel alias maps to MediaChannel.streaming_audio in response."""
        from adcp.types.generated_poc.enums.channels import MediaChannel

        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        mock_adapter = MagicMock()
        mock_adapter.default_channels = ["audio"]
        mock_adapter.get_targeting_capabilities.return_value = None

        identity = _make_capabilities_identity()
        stack = _patch_capabilities_deps(adapter=mock_adapter)

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.media_buy is not None
        assert MediaChannel.streaming_audio in response.media_buy.portfolio.primary_channels

    def test_unknown_channel_names_gracefully_ignored(self):
        """Unknown channel names are silently ignored (not in CHANNEL_MAPPING)."""
        from adcp.types.generated_poc.enums.channels import MediaChannel

        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        mock_adapter = MagicMock()
        mock_adapter.default_channels = ["unknown_channel", "display"]
        mock_adapter.get_targeting_capabilities.return_value = None

        identity = _make_capabilities_identity()
        stack = _patch_capabilities_deps(adapter=mock_adapter)

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        channels = response.media_buy.portfolio.primary_channels
        assert MediaChannel.display in channels
        # Unknown channel is silently skipped
        assert len(channels) == 1

    def test_no_adapter_channels_defaults_to_display(self):
        """When adapter has no default_channels, defaults to display."""
        from adcp.types.generated_poc.enums.channels import MediaChannel

        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        # Adapter without default_channels attribute
        mock_adapter = MagicMock(spec=[])
        identity = _make_capabilities_identity()
        stack = _patch_capabilities_deps(adapter=mock_adapter)

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.media_buy is not None
        assert MediaChannel.display in response.media_buy.portfolio.primary_channels


class TestGracefulDegradation:
    """Test graceful degradation when adapter or DB raises exceptions."""

    def test_adapter_exception_falls_back_to_display(self):
        """Adapter exception during channel detection falls back to display channel."""
        from adcp.types.generated_poc.enums.channels import MediaChannel

        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        identity = _make_capabilities_identity()

        with (
            patch("src.core.tools.capabilities.get_db_session") as mock_db,
            patch("src.core.tools.capabilities.log_tool_activity"),
            patch("src.core.tools.capabilities.get_principal_object", return_value=MagicMock()),
            patch("src.core.tools.capabilities.get_adapter", side_effect=Exception("Adapter init failed")),
        ):
            mock_session = MagicMock()
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.scalars.return_value.all.return_value = []

            response = _get_adcp_capabilities_impl(None, identity)

        # Should still succeed with display as default
        assert response.media_buy is not None
        assert MediaChannel.display in response.media_buy.portfolio.primary_channels

    def test_db_exception_uses_placeholder_domain(self):
        """Database exception during publisher domain query uses placeholder domain."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        identity = _make_capabilities_identity(
            tenant={"tenant_id": "t1", "name": "Test", "subdomain": "testpub"},
        )

        with (
            patch("src.core.tools.capabilities.get_db_session", side_effect=Exception("DB down")),
            patch("src.core.tools.capabilities.log_tool_activity"),
            patch("src.core.tools.capabilities.get_principal_object", return_value=None),
        ):
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.media_buy is not None
        domains = response.media_buy.portfolio.publisher_domains
        assert len(domains) == 1
        assert "testpub.example.com" in domains[0].root


class TestAdvertisingPolicies:
    """Test advertising policy extraction from tenant config."""

    def test_advertising_policy_description_extracted(self):
        """Advertising policy description is extracted from tenant config."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        tenant = {
            "tenant_id": "t1",
            "name": "Policy Pub",
            "subdomain": "policypub",
            "advertising_policy": {"description": "No adult content allowed"},
        }
        identity = _make_capabilities_identity(principal_id=None, tenant=tenant)
        stack = _patch_capabilities_deps()

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.media_buy is not None
        assert response.media_buy.portfolio.advertising_policies == "No adult content allowed"

    def test_no_advertising_policy_returns_none(self):
        """When tenant has no advertising_policy, portfolio.advertising_policies is None."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        tenant = {"tenant_id": "t1", "name": "No Policy Pub", "subdomain": "nopolicy"}
        identity = _make_capabilities_identity(principal_id=None, tenant=tenant)
        stack = _patch_capabilities_deps()

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.media_buy is not None
        assert response.media_buy.portfolio.advertising_policies is None


class TestPublisherDomains:
    """Test publisher domain extraction from database."""

    def test_publisher_domains_from_database(self):
        """Publisher domains are read from PublisherPartner records in DB."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        # Create mock partner records
        partner1 = MagicMock()
        partner1.publisher_domain = "example.com"
        partner2 = MagicMock()
        partner2.publisher_domain = "news.org"

        identity = _make_capabilities_identity(principal_id=None)
        stack = _patch_capabilities_deps(db_partners=[partner1, partner2])

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.media_buy is not None
        domains = [d.root for d in response.media_buy.portfolio.publisher_domains]
        assert "example.com" in domains
        assert "news.org" in domains

    def test_partner_without_domain_skipped(self):
        """Partners with publisher_domain=None are skipped."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        partner_with = MagicMock()
        partner_with.publisher_domain = "real.com"
        partner_without = MagicMock()
        partner_without.publisher_domain = None

        identity = _make_capabilities_identity(principal_id=None)
        stack = _patch_capabilities_deps(db_partners=[partner_with, partner_without])

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        domains = [d.root for d in response.media_buy.portfolio.publisher_domains]
        assert "real.com" in domains
        assert len(domains) == 1


class TestResponseShapeCapabilities:
    """Test response structure and serialization for get_adcp_capabilities."""

    def test_last_updated_present_with_tenant(self):
        """Response includes last_updated when tenant context is available."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        identity = _make_capabilities_identity(principal_id=None)
        stack = _patch_capabilities_deps()

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.last_updated is not None

    def test_last_updated_absent_without_tenant(self):
        """Response has no last_updated when no tenant context (minimal response)."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        response = _get_adcp_capabilities_impl(None, None)
        assert response.last_updated is None

    def test_features_defaults_with_tenant(self):
        """Features defaults: content_standards=False, inline_creative_management=True, property_list_filtering=False."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        identity = _make_capabilities_identity(principal_id=None)
        stack = _patch_capabilities_deps()

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        features = response.media_buy.features
        assert features.content_standards is False
        assert features.inline_creative_management is True
        assert features.property_list_filtering is True

    def test_full_response_serialization_shape(self):
        """Full response model_dump(mode='json') has expected keys."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        identity = _make_capabilities_identity(principal_id=None)
        stack = _patch_capabilities_deps()

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        data = response.model_dump(mode="json")
        assert "adcp" in data
        assert "supported_protocols" in data
        assert "media_buy" in data
        assert data["supported_protocols"] == ["media_buy"]
        assert "portfolio" in data["media_buy"]
        assert "features" in data["media_buy"]
        assert "execution" in data["media_buy"]

    def test_minimal_response_no_media_buy(self):
        """Minimal response (no tenant) omits media_buy from serialized output."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        response = _get_adcp_capabilities_impl(None, None)
        assert response.media_buy is None
        data = response.model_dump(mode="json")
        # media_buy is excluded from serialization when None
        assert "media_buy" not in data


class TestDevicePlatformCapability:
    """Test device_platform targeting advertised in capabilities."""

    def test_device_platform_true_in_targeting(self):
        """Capabilities response advertises device_platform: true."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        identity = _make_capabilities_identity(principal_id=None)
        stack = _patch_capabilities_deps()

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        targeting = response.media_buy.execution.targeting
        assert targeting.device_platform is True

    def test_device_platform_in_serialized_output(self):
        """device_platform: true appears in JSON-serialized capabilities."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        identity = _make_capabilities_identity(principal_id=None)
        stack = _patch_capabilities_deps()

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        data = response.model_dump(mode="json")
        targeting_data = data["media_buy"]["execution"]["targeting"]
        assert targeting_data["device_platform"] is True


class TestGeoPostalAreas:
    """Test geo_postal_areas building from targeting capabilities."""

    def test_geo_postal_areas_built_from_adapter(self):
        """geo_postal_areas are populated from adapter targeting capabilities."""
        from src.adapters.base import TargetingCapabilities
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        mock_adapter = MagicMock()
        mock_adapter.default_channels = ["display"]
        mock_adapter.get_targeting_capabilities.return_value = TargetingCapabilities(
            geo_countries=True,
            geo_regions=True,
            us_zip=True,
            ca_fsa=True,
            gb_outward=True,
        )

        identity = _make_capabilities_identity()
        stack = _patch_capabilities_deps(adapter=mock_adapter)

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        postal = response.media_buy.execution.targeting.geo_postal_areas
        assert postal is not None
        assert postal.us_zip is True
        assert postal.ca_fsa is True
        assert postal.gb_outward is True
        # Fields not set should be None
        assert postal.de_plz is None
        assert postal.fr_code_postal is None

    def test_no_postal_targeting_means_none(self):
        """When no postal targeting capabilities, geo_postal_areas is None."""
        from src.adapters.base import TargetingCapabilities
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        mock_adapter = MagicMock()
        mock_adapter.default_channels = ["display"]
        mock_adapter.get_targeting_capabilities.return_value = TargetingCapabilities(
            geo_countries=True,
            geo_regions=True,
            # No postal targeting set
        )

        identity = _make_capabilities_identity()
        stack = _patch_capabilities_deps(adapter=mock_adapter)

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.media_buy.execution.targeting.geo_postal_areas is None
