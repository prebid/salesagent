"""Tests for parameterized format templates (AdCP 2.5).

Issue #782: Support creative format templates with dynamic width/height/duration.
"""

import pytest
from adcp.types import FormatId

from src.core.helpers import _extract_format_info, _extract_format_namespace
from src.core.helpers.creative_helpers import (
    _convert_creative_to_adapter_asset,
)
from src.core.schemas import Creative, Format
from src.core.schemas import FormatId as SchemasFormatId


class TestExtractFormatInfo:
    """Test _extract_format_info function for parameterized formats."""

    def test_basic_format_without_parameters(self):
        """Basic format with just agent_url and id."""
        format_value = {"agent_url": "https://creative.example.com", "id": "display_static"}
        result = _extract_format_info(format_value)

        assert result["agent_url"] == "https://creative.example.com"
        assert result["format_id"] == "display_static"
        assert result["parameters"] is None

    def test_format_with_dimensions(self):
        """Format with width and height parameters."""
        format_value = {
            "agent_url": "https://creative.example.com",
            "id": "display_static",
            "width": 300,
            "height": 250,
        }
        result = _extract_format_info(format_value)

        assert result["agent_url"] == "https://creative.example.com"
        assert result["format_id"] == "display_static"
        assert result["parameters"] == {"width": 300, "height": 250}

    def test_format_with_duration(self):
        """Format with duration_ms parameter (video)."""
        format_value = {
            "agent_url": "https://creative.example.com",
            "id": "video_hosted",
            "duration_ms": 15000,
        }
        result = _extract_format_info(format_value)

        assert result["agent_url"] == "https://creative.example.com"
        assert result["format_id"] == "video_hosted"
        assert result["parameters"] == {"duration_ms": 15000.0}

    def test_format_with_all_parameters(self):
        """Format with dimensions and duration (video with specific size)."""
        format_value = {
            "agent_url": "https://creative.example.com",
            "id": "video_outstream",
            "width": 1920,
            "height": 1080,
            "duration_ms": 30000,
        }
        result = _extract_format_info(format_value)

        assert result["agent_url"] == "https://creative.example.com"
        assert result["format_id"] == "video_outstream"
        assert result["parameters"] == {"width": 1920, "height": 1080, "duration_ms": 30000.0}

    def test_format_from_pydantic_object(self):
        """Extract format info from Pydantic FormatId object."""
        format_obj = FormatId(
            agent_url="https://creative.example.com",
            id="display_static",
            width=728,
            height=90,
        )
        result = _extract_format_info(format_obj)

        assert result["agent_url"] == "https://creative.example.com/"  # AnyUrl normalizes
        assert result["format_id"] == "display_static"
        assert result["parameters"] == {"width": 728, "height": 90}

    def test_format_ignores_none_parameters(self):
        """None parameters should not be included."""
        format_value = {
            "agent_url": "https://creative.example.com",
            "id": "display_static",
            "width": None,
            "height": None,
        }
        result = _extract_format_info(format_value)

        assert result["parameters"] is None

    def test_format_partial_parameters(self):
        """Only non-None parameters should be included."""
        format_value = {
            "agent_url": "https://creative.example.com",
            "id": "display_static",
            "width": 300,
            "height": None,  # Only width specified
        }
        result = _extract_format_info(format_value)

        assert result["parameters"] == {"width": 300}

    def test_string_format_raises_error(self):
        """String format should raise clear error."""
        with pytest.raises(ValueError, match="String format_id is no longer supported"):
            _extract_format_info("display_static")

    def test_missing_agent_url_raises_error(self):
        """Missing agent_url should raise error."""
        with pytest.raises(ValueError, match="must have both 'agent_url' and 'id' fields"):
            _extract_format_info({"id": "display_static"})

    def test_missing_id_raises_error(self):
        """Missing id should raise error."""
        with pytest.raises(ValueError, match="must have both 'agent_url' and 'id' fields"):
            _extract_format_info({"agent_url": "https://creative.example.com"})


class TestExtractFormatNamespaceBackwardCompat:
    """Test that _extract_format_namespace still works for backward compat."""

    def test_basic_extraction(self):
        """Basic extraction still works."""
        format_value = {"agent_url": "https://creative.example.com", "id": "display_static"}
        agent_url, format_id = _extract_format_namespace(format_value)

        assert agent_url == "https://creative.example.com"
        assert format_id == "display_static"

    def test_ignores_parameters(self):
        """Parameters are ignored (old function doesn't know about them)."""
        format_value = {
            "agent_url": "https://creative.example.com",
            "id": "display_static",
            "width": 300,
            "height": 250,
        }
        agent_url, format_id = _extract_format_namespace(format_value)

        # Should return just agent_url and id
        assert agent_url == "https://creative.example.com"
        assert format_id == "display_static"


class TestFormatIdReconstruction:
    """Test reconstructing FormatId from database fields."""

    def test_reconstruct_without_parameters(self):
        """Reconstruct FormatId without parameters."""
        format_obj = FormatId(
            agent_url="https://creative.example.com",
            id="display_static",
        )

        assert str(format_obj.agent_url) == "https://creative.example.com/"
        assert format_obj.id == "display_static"
        assert format_obj.width is None
        assert format_obj.height is None
        assert format_obj.duration_ms is None

    def test_reconstruct_with_parameters(self):
        """Reconstruct FormatId with stored parameters."""
        # Simulate what list_creatives does
        format_kwargs = {
            "agent_url": "https://creative.example.com",
            "id": "display_static",
        }

        # Simulate format_parameters from database
        params = {"width": 300, "height": 250}
        if "width" in params:
            format_kwargs["width"] = params["width"]
        if "height" in params:
            format_kwargs["height"] = params["height"]

        format_obj = FormatId(**format_kwargs)

        assert str(format_obj.agent_url) == "https://creative.example.com/"
        assert format_obj.id == "display_static"
        assert format_obj.width == 300
        assert format_obj.height == 250

    def test_reconstruct_video_format(self):
        """Reconstruct video format with duration."""
        params = {"duration_ms": 15000}
        format_obj = FormatId(
            agent_url="https://creative.example.com",
            id="video_hosted",
            duration_ms=params["duration_ms"],
        )

        assert format_obj.id == "video_hosted"
        assert format_obj.duration_ms == 15000


class TestFormatParametersRoundTrip:
    """Test round-trip: FormatId -> storage -> reconstruction."""

    def test_roundtrip_display_format(self):
        """Display format roundtrip preserves parameters."""
        # Original FormatId from AdCP request
        original = FormatId(
            agent_url="https://creative.example.com",
            id="display_static",
            width=300,
            height=250,
        )

        # Extract for storage
        format_info = _extract_format_info(original)

        # Verify storage format
        assert format_info["agent_url"] == "https://creative.example.com/"
        assert format_info["format_id"] == "display_static"
        assert format_info["parameters"] == {"width": 300, "height": 250}

        # Reconstruct from storage
        format_kwargs = {
            "agent_url": format_info["agent_url"],
            "id": format_info["format_id"],
        }
        if format_info["parameters"]:
            format_kwargs.update(format_info["parameters"])

        reconstructed = FormatId(**format_kwargs)

        # Verify equality
        assert reconstructed.id == original.id
        assert reconstructed.width == original.width
        assert reconstructed.height == original.height

    def test_roundtrip_video_format(self):
        """Video format roundtrip preserves duration."""
        original = FormatId(
            agent_url="https://creative.example.com",
            id="video_hosted",
            width=1920,
            height=1080,
            duration_ms=30000,
        )

        # Extract for storage
        format_info = _extract_format_info(original)

        # Reconstruct
        format_kwargs = {
            "agent_url": format_info["agent_url"],
            "id": format_info["format_id"],
        }
        if format_info["parameters"]:
            format_kwargs.update(format_info["parameters"])

        reconstructed = FormatId(**format_kwargs)

        # Verify all fields preserved
        assert reconstructed.id == original.id
        assert reconstructed.width == original.width
        assert reconstructed.height == original.height
        assert reconstructed.duration_ms == original.duration_ms

    def test_roundtrip_template_format_no_parameters(self):
        """Template format without parameters roundtrips correctly."""
        original = FormatId(
            agent_url="https://creative.example.com",
            id="display_static",
        )

        # Extract for storage
        format_info = _extract_format_info(original)

        # Verify no parameters stored
        assert format_info["parameters"] is None

        # Reconstruct
        format_kwargs = {
            "agent_url": format_info["agent_url"],
            "id": format_info["format_id"],
        }

        reconstructed = FormatId(**format_kwargs)

        assert reconstructed.id == original.id
        assert reconstructed.width is None
        assert reconstructed.height is None


class TestAdapterAssetConversion:
    """Test _convert_creative_to_adapter_asset extracts format dimensions."""

    def test_display_creative_with_format_dimensions(self):
        """Display creative gets width/height from format_id parameters."""
        creative = Creative(
            creative_id="cr_123",
            name="Test Banner",
            format_id=FormatId(
                agent_url="https://creative.example.com",
                id="display_static",
                width=300,
                height=250,
            ),
            assets={
                "banner_image": {
                    "url": "https://example.com/banner.jpg",
                }
            },
        )

        asset = _convert_creative_to_adapter_asset(creative, ["pkg_1"])

        assert asset["width"] == 300
        assert asset["height"] == 250
        assert asset["creative_id"] == "cr_123"
        assert asset["format"] == "display_static"

    def test_video_creative_with_format_duration(self):
        """Video creative gets duration_ms from format_id parameters."""
        creative = Creative(
            creative_id="cr_456",
            name="Test Video",
            format_id=FormatId(
                agent_url="https://creative.example.com",
                id="video_hosted",
                width=1920,
                height=1080,
                duration_ms=30000,
            ),
            assets={
                "video": {
                    "url": "https://example.com/video.mp4",
                }
            },
        )

        asset = _convert_creative_to_adapter_asset(creative, ["pkg_1"])

        assert asset["width"] == 1920
        assert asset["height"] == 1080
        # Duration is converted to seconds for adapter
        assert asset["duration"] == 30.0

    def test_template_format_no_dimensions(self):
        """Template format without parameters doesn't add dimensions."""
        creative = Creative(
            creative_id="cr_789",
            name="Test Creative",
            format_id=FormatId(
                agent_url="https://creative.example.com",
                id="display_static",
                # No width/height
            ),
            assets={
                "banner_image": {
                    "url": "https://example.com/banner.jpg",
                }
            },
        )

        asset = _convert_creative_to_adapter_asset(creative, ["pkg_1"])

        assert "width" not in asset
        assert "height" not in asset

    def test_leaderboard_dimensions(self):
        """Leaderboard (728x90) dimensions are extracted correctly."""
        creative = Creative(
            creative_id="cr_leaderboard",
            name="Leaderboard Ad",
            format_id=FormatId(
                agent_url="https://creative.example.com",
                id="display_static",
                width=728,
                height=90,
            ),
            assets={
                "banner_image": {
                    "url": "https://example.com/leaderboard.jpg",
                }
            },
        )

        asset = _convert_creative_to_adapter_asset(creative, ["pkg_1"])

        assert asset["width"] == 728
        assert asset["height"] == 90


class TestFormatIdGetDimensions:
    """Test FormatId.get_dimensions() helper method."""

    def test_get_dimensions_with_both(self):
        """Returns dimensions when both width and height are set."""
        format_id = SchemasFormatId(
            agent_url="https://creative.example.com",
            id="display_static",
            width=300,
            height=250,
        )
        assert format_id.get_dimensions() == (300, 250)

    def test_get_dimensions_without_params(self):
        """Returns None when no dimensions set."""
        format_id = SchemasFormatId(
            agent_url="https://creative.example.com",
            id="display_static",
        )
        assert format_id.get_dimensions() is None

    def test_get_dimensions_partial_width_only(self):
        """Returns None when only width is set (both required)."""
        format_id = SchemasFormatId(
            agent_url="https://creative.example.com",
            id="display_static",
            width=300,
        )
        assert format_id.get_dimensions() is None

    def test_get_dimensions_partial_height_only(self):
        """Returns None when only height is set (both required)."""
        format_id = SchemasFormatId(
            agent_url="https://creative.example.com",
            id="display_static",
            height=250,
        )
        assert format_id.get_dimensions() is None

    def test_get_duration_ms(self):
        """Returns duration_ms when set."""
        format_id = SchemasFormatId(
            agent_url="https://creative.example.com",
            id="video_hosted",
            duration_ms=15000,
        )
        assert format_id.get_duration_ms() == 15000

    def test_get_duration_ms_not_set(self):
        """Returns None when duration_ms not set."""
        format_id = SchemasFormatId(
            agent_url="https://creative.example.com",
            id="video_hosted",
        )
        assert format_id.get_duration_ms() is None


class TestFormatGetPrimaryDimensionsWithFormatId:
    """Test Format.get_primary_dimensions() uses FormatId parameters."""

    def test_prioritizes_format_id_dimensions(self):
        """Format_id dimensions take priority over renders."""
        fmt = Format(
            format_id=SchemasFormatId(
                agent_url="https://creative.example.com",
                id="display_static",
                width=300,
                height=250,
            ),
            name="Static Display",
            type="display",
            # Even if requirements say something different, format_id wins
            requirements={"width": 728, "height": 90},
        )
        assert fmt.get_primary_dimensions() == (300, 250)

    def test_falls_back_to_requirements(self):
        """Falls back to requirements when format_id has no dimensions."""
        fmt = Format(
            format_id=SchemasFormatId(
                agent_url="https://creative.example.com",
                id="display_static",
            ),
            name="Static Display",
            type="display",
            requirements={"width": 728, "height": 90},
        )
        assert fmt.get_primary_dimensions() == (728, 90)

    def test_returns_none_when_no_dimensions(self):
        """Returns None when no dimensions available anywhere."""
        fmt = Format(
            format_id=SchemasFormatId(
                agent_url="https://creative.example.com",
                id="display_static",
            ),
            name="Static Display",
            type="display",
        )
        assert fmt.get_primary_dimensions() is None


class TestFormatTemplatesAPI:
    """Tests for format templates API endpoint (/api/formats/templates)."""

    def test_templates_endpoint_structure(self):
        """Test the format templates endpoint returns expected structure."""
        # Simulates what the endpoint returns
        templates = {
            "display_static": {
                "id": "display_static",
                "name": "Static Display",
                "type": "display",
                "parameter_type": "dimensions",
                "gam_supported": True,
            },
            "video_hosted": {
                "id": "video_hosted",
                "name": "Hosted Video",
                "type": "video",
                "parameter_type": "both",
                "gam_supported": True,
            },
            "audio": {
                "id": "audio",
                "name": "Audio Ad",
                "type": "audio",
                "parameter_type": "duration",
                "gam_supported": False,
            },
        }

        # Test GAM adapter filtering (audio not supported)
        gam_templates = {k: v for k, v in templates.items() if v.get("gam_supported", True)}
        assert "display_static" in gam_templates
        assert "video_hosted" in gam_templates
        assert "audio" not in gam_templates

    def test_common_sizes_from_gam_constants(self):
        """Test that common sizes come from GAM_STANDARD_SIZES."""
        from src.adapters.gam.utils.constants import GAM_STANDARD_SIZES

        # Verify expected sizes are present
        assert "medium_rectangle" in GAM_STANDARD_SIZES
        assert GAM_STANDARD_SIZES["medium_rectangle"] == (300, 250)
        assert "leaderboard" in GAM_STANDARD_SIZES
        assert GAM_STANDARD_SIZES["leaderboard"] == (728, 90)
        assert "wide_skyscraper" in GAM_STANDARD_SIZES
        assert GAM_STANDARD_SIZES["wide_skyscraper"] == (160, 600)


class TestParameterizedFormatParsing:
    """Tests for parsing parameterized format_ids from form data."""

    def test_parse_legacy_format(self):
        """Legacy format {agent_url, format_id} should be accepted."""
        parsed = {"agent_url": "https://creative.example.com", "format_id": "display_300x250"}

        # Legacy uses 'format_id', new uses 'id'
        format_id = parsed.get("id") or parsed.get("format_id")
        assert format_id == "display_300x250"

    def test_parse_new_parameterized_format(self):
        """New format {agent_url, id, width, height} should be accepted."""
        parsed = {
            "agent_url": "https://creative.example.com",
            "id": "display_static",
            "width": 300,
            "height": 250,
        }

        format_id = parsed.get("id") or parsed.get("format_id")
        assert format_id == "display_static"
        assert parsed.get("width") == 300
        assert parsed.get("height") == 250

    def test_parse_video_format_with_duration(self):
        """Video format with duration_ms should be accepted."""
        parsed = {
            "agent_url": "https://creative.example.com",
            "id": "video_hosted",
            "width": 1920,
            "height": 1080,
            "duration_ms": 30000,
        }

        assert parsed["id"] == "video_hosted"
        assert parsed["duration_ms"] == 30000

    def test_build_format_entry_with_params(self):
        """Format entry should include all specified parameters."""
        fmt = {
            "agent_url": "https://creative.example.com",
            "id": "display_static",
            "width": 728,
            "height": 90,
        }

        # Simulate backend format entry building
        format_entry = {"agent_url": fmt["agent_url"], "id": fmt["id"]}
        if fmt.get("width") is not None:
            format_entry["width"] = int(fmt["width"])
        if fmt.get("height") is not None:
            format_entry["height"] = int(fmt["height"])

        assert format_entry == {
            "agent_url": "https://creative.example.com",
            "id": "display_static",
            "width": 728,
            "height": 90,
        }

    def test_format_entry_without_params(self):
        """Format entry without params should only have agent_url and id."""
        fmt = {
            "agent_url": "https://creative.example.com",
            "id": "native",
        }

        format_entry = {"agent_url": fmt["agent_url"], "id": fmt["id"]}
        if fmt.get("width") is not None:
            format_entry["width"] = int(fmt["width"])
        if fmt.get("height") is not None:
            format_entry["height"] = int(fmt["height"])

        assert format_entry == {
            "agent_url": "https://creative.example.com",
            "id": "native",
        }
        assert "width" not in format_entry
        assert "height" not in format_entry
