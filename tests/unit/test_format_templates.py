"""Tests for parameterized format templates (AdCP 2.5).

Issue #782: Support creative format templates with dynamic width/height/duration.
"""

from adcp.types.domains.core.format import Dimensions, Renders  # TODO: no stable alias in adcp.types
from adcp.types.domains.core.format_id import FormatReferenceStructuredObject as FormatId

from src.core.helpers import _extract_format_namespace
from src.core.schemas import Format
from src.core.schemas import FormatId as SchemasFormatId


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

        # Verbatim, not AnyUrl-normalised -- see test_format_from_pydantic_object.
        assert str(format_obj.agent_url) == "https://creative.example.com"
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

        # Verbatim, not AnyUrl-normalised -- see test_format_from_pydantic_object.
        assert str(format_obj.agent_url) == "https://creative.example.com"
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

    # The two partial-dimension tests that stood here are DELETED, not moved.
    #
    # They graded get_dimensions() on a FormatId carrying exactly one of width and
    # height. core/format-id.json states
    #     "dependencies": {"width": ["height"], "height": ["width"]}
    # so width and height are mutually dependent and a one-sided pair is not a
    # document the pin admits. The rebuilt SDK compiles that onto the exported class
    # and refuses it; the previous SDK did not, which is the only reason the tests
    # ever ran (salesagent-8qela.121).
    #
    # The correct behaviour is that get_dimensions() has exactly two cases, a
    # complete pair and absent dimensions, and both are graded directly above by
    # test_get_dimensions_with_both and test_get_dimensions_without_params. There is
    # no third case to assert, so there is nothing here to re-ground.

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
        )
        assert fmt.get_primary_dimensions() is None

    def test_works_with_library_format_id(self):
        """get_primary_dimensions must work when format_id is library FormatId (not our subclass).

        Bug #1067 / PR #1079: When formats come from the creative agent, they are
        deserialized with the library's FormatId which lacks get_dimensions(). The old
        code called format_id.get_dimensions() which only exists on the custom FormatId
        subclass. This caused a 500 error on the New Product page.
        """
        # Use the library FormatId directly (as happens when deserializing from creative agent)
        fmt = Format(
            format_id=FormatId(
                agent_url="https://creative.adcontextprotocol.org",
                id="display_static",
                width=300,
                height=250,
            ),
            name="Static Display",
        )
        assert fmt.get_primary_dimensions() == (300, 250)

    def test_works_with_library_format_id_no_dimensions(self):
        """get_primary_dimensions returns None with library FormatId that has no dimensions."""
        fmt = Format(
            format_id=FormatId(
                agent_url="https://creative.adcontextprotocol.org",
                id="video_hosted",
            ),
            name="Hosted Video",
        )
        assert fmt.get_primary_dimensions() is None

    def test_falls_back_to_renders(self):
        """Falls back to renders when format_id has no dimensions."""
        fmt = Format(
            format_id=FormatId(
                agent_url="https://creative.example.com",
                id="display_static",
            ),
            name="Static Display",
            renders=[Renders(role="primary", dimensions=Dimensions(width=728, height=90))],
        )
        assert fmt.get_primary_dimensions() == (728, 90)

    # test_partial_format_id_dimensions_falls_through is DELETED for the reason given
    # in TestFormatIdGetDimensions above: its format_id carried width without height,
    # which core/format-id.json's dependencies forbid. The fall-through it claimed to
    # grade — a format_id with no usable dimensions deferring to requirements — is
    # graded by test_falls_back_to_requirements, whose fixture is legal.

    def test_format_id_takes_priority_over_renders_and_requirements(self):
        """format_id dimensions take priority over both renders and requirements."""
        fmt = Format(
            format_id=FormatId(
                agent_url="https://creative.example.com",
                id="display_static",
                width=300,
                height=250,
            ),
            name="Static Display",
            renders=[Renders(role="primary", dimensions=Dimensions(width=640, height=480))],
            requirements={"width": 728, "height": 90},
        )
        assert fmt.get_primary_dimensions() == (300, 250)


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
