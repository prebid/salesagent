"""Unit tests for adcp library schema compatibility.

These tests verify that the schemas returned by the adcp library (v1.0.1)
are compatible with our internal Pydantic models and AdCP v2.2.0 spec.
"""

import pytest
from pydantic import ValidationError

from src.core.schemas import Format, FormatId, Signal


class TestADCPSchemaCompatibility:
    """Test suite for adcp library schema compatibility with our models."""

    def test_format_from_adcp_response(self):
        """Test Format model can be created from adcp library response format."""
        # Simulate format data as returned by adcp library (minimal required fields)
        adcp_format_data = {
            "format_id": {
                "agent_url": "https://creative.adcontextprotocol.org",
                "id": "display_300x250",
            },
            "name": "Display 300x250",
            "type": "display",
        }

        # Should successfully create Format object
        format_obj = Format(**adcp_format_data)

        assert format_obj.format_id.id == "display_300x250"
        assert (
            str(format_obj.format_id.agent_url).rstrip("/") == "https://creative.adcontextprotocol.org"
        )  # AnyUrl adds trailing slash
        assert format_obj.name == "Display 300x250"
        assert format_obj.type.value == "display"  # Type is an enum, compare with .value

    def test_format_id_from_adcp_response(self):
        """Test FormatId model can be created from adcp library format_id."""
        adcp_format_id = {
            "agent_url": "https://creative.adcontextprotocol.org",
            "id": "video_1920x1080",
        }

        format_id = FormatId(**adcp_format_id)

        assert format_id.id == "video_1920x1080"
        assert (
            str(format_id.agent_url).rstrip("/") == "https://creative.adcontextprotocol.org"
        )  # AnyUrl adds trailing slash

    def test_format_id_requires_both_fields(self):
        """Test FormatId requires both agent_url and id (AdCP v2.4 compliance)."""
        # Missing agent_url should raise validation error
        with pytest.raises(ValidationError, match="agent_url"):
            FormatId(id="display_300x250")  # type: ignore[call-arg]

        # Missing id should raise validation error
        with pytest.raises(ValidationError, match="id"):
            FormatId(agent_url="https://example.com")  # type: ignore[call-arg]

    def test_signal_from_adcp_response(self):
        """Test Signal model can be created from adcp library signal format."""
        # Simulate signal data as returned by adcp library (all required fields)
        adcp_signal_data = {
            "signal_agent_segment_id": "segment_123",
            "name": "Automotive Enthusiasts",
            "description": "Users interested in automotive content",
            "signal_type": "marketplace",
            "data_provider": "Optable",
            "coverage_percentage": 85.0,
            "deployments": [
                {
                    "platform": "gam",
                    "is_live": True,
                    "scope": "platform-wide",
                }
            ],
            "pricing": {"cpm": 5.0, "currency": "USD"},
        }

        signal = Signal(**adcp_signal_data)

        assert signal.signal_agent_segment_id == "segment_123"
        assert signal.name == "Automotive Enthusiasts"
        assert signal.signal_type == "marketplace"
        assert signal.data_provider == "Optable"
        assert signal.coverage_percentage == 85.0
        assert len(signal.deployments) == 1
        assert signal.pricing.cpm == 5.0

    def test_format_with_agent_url_override(self):
        """Test Format model does NOT allow agent_url at top level (only in format_id)."""
        # Per adcp library schema, agent_url only exists in format_id, not at Format level
        adcp_format_data = {
            "format_id": {
                "agent_url": "https://creative.adcontextprotocol.org",
                "id": "display_728x90",
            },
            "name": "Display 728x90",
            "type": "display",
        }

        format_obj = Format(**adcp_format_data)

        # Only FormatId has agent_url
        assert (
            str(format_obj.format_id.agent_url).rstrip("/") == "https://creative.adcontextprotocol.org"
        )  # AnyUrl adds trailing slash
        assert format_obj.type.value == "display"  # Type is an enum

    def test_format_with_renders(self):
        """Test Format model handles renders correctly (AdCP v2.4 spec)."""
        format_data = {
            "format_id": {
                "agent_url": "https://test.com",
                "id": "test_format",
            },
            "name": "Test Format",
            "type": "display",
            "renders": [{"role": "primary", "dimensions": {"width": 1920, "height": 1080, "unit": "px"}}],
        }

        format_obj = Format(**format_data)
        assert format_obj.type.value == "display"  # Type is an enum
        # Renders is a list of Render objects (Pydantic models), compare dict representation
        assert len(format_obj.renders) == 1
        assert format_obj.renders[0].role == "primary"
        assert format_obj.renders[0].dimensions.width == 1920
        assert format_obj.renders[0].dimensions.height == 1080
        assert format_obj.renders[0].dimensions.unit.value == "px"  # Unit is an enum

    def test_format_minimal_required_fields(self):
        """Test Format model works with only required fields."""
        format_data = {
            "format_id": {
                "agent_url": "https://test.com",
                "id": "minimal_format",
            },
            "name": "Minimal Format",
            "type": "audio",
        }

        format_obj = Format(**format_data)
        assert format_obj.name == "Minimal Format"
        assert format_obj.type.value == "audio"  # Type is an enum, compare with .value
        assert format_obj.renders is None

    def test_format_with_platform_config(self):
        """Test Format model handles platform_config correctly."""
        format_data = {
            "format_id": {
                "agent_url": "https://test.com",
                "id": "test_format",
            },
            "name": "Test Format",
            "type": "display",
            "platform_config": {
                "gam": {"creative_template_id": "123"},
                "kevel": {"template_id": "456"},
            },
        }

        format_obj = Format(**format_data)
        assert format_obj.platform_config == {
            "gam": {"creative_template_id": "123"},
            "kevel": {"template_id": "456"},
        }

    def test_signal_roundtrip_with_model_dump(self):
        """Test Signal can roundtrip through model_dump and reconstruction."""
        original_data = {
            "signal_agent_segment_id": "roundtrip_seg",
            "name": "Roundtrip Signal",
            "description": "Test signal for roundtrip",
            "signal_type": "marketplace",  # Must be one of: marketplace, custom, owned
            "data_provider": "TestProvider",
            "coverage_percentage": 90.5,
            "deployments": [
                {
                    "platform": "gam",
                    "is_live": True,
                    "scope": "platform-wide",
                }
            ],
            "pricing": {"cpm": 10.0, "currency": "USD"},
        }

        # Create signal from data
        signal = Signal(**original_data)

        # Dump to dict
        dumped = signal.model_dump()

        # Reconstruct from dumped data
        reconstructed = Signal(**dumped)

        # Verify all fields match
        assert reconstructed.signal_agent_segment_id == original_data["signal_agent_segment_id"]
        assert reconstructed.name == original_data["name"]
        assert reconstructed.description == original_data["description"]
        assert reconstructed.signal_type == original_data["signal_type"]
        assert reconstructed.data_provider == original_data["data_provider"]
        assert reconstructed.coverage_percentage == original_data["coverage_percentage"]

    def test_format_roundtrip_with_model_dump(self):
        """Test Format can roundtrip through model_dump and reconstruction."""
        original_data = {
            "format_id": {
                "agent_url": "https://roundtrip.example.com",
                "id": "roundtrip_format",
            },
            "name": "Roundtrip Format",
            "type": "video",
            "renders": [{"role": "primary", "dimensions": {"width": 1280, "height": 720, "unit": "px"}}],
        }

        # Create format from data
        format_obj = Format(**original_data)

        # Dump to dict (this is what adcp library does)
        dumped = format_obj.model_dump()

        # Reconstruct from dumped data
        reconstructed = Format(**dumped)

        # Verify all fields match
        assert reconstructed.format_id.id == "roundtrip_format"
        assert (
            str(reconstructed.format_id.agent_url).rstrip("/") == "https://roundtrip.example.com"
        )  # AnyUrl adds trailing slash
        assert reconstructed.name == "Roundtrip Format"
        assert reconstructed.type.value == "video"  # Type is an enum, compare with .value
        # Renders is a list of Render Pydantic objects
        assert len(reconstructed.renders) == 1
        assert reconstructed.renders[0].role == "primary"
        assert reconstructed.renders[0].dimensions.width == 1280
        assert reconstructed.renders[0].dimensions.height == 720
        assert reconstructed.renders[0].dimensions.unit.value == "px"  # Unit is an enum
