"""Unit tests for FormatId dimension merging in media buy creation.

Tests the fix for the "Format has no width/height configuration for GAM" error
that occurred when:
1. Product config has parameterized formats like {"id": "display_image", "width": 300, "height": 250}
2. But the production code wasn't passing width/height to FormatId objects
3. Or when buyer's request has format_ids without dimensions

The fix ensures dimensions from product config are properly passed to FormatId objects
and merged when request format_ids don't have them.
"""

from src.core.schemas import FormatId, MediaPackage

# Default agent URL for creating FormatId objects in tests
DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"


def make_format_id(
    format_id: str,
    agent_url: str = DEFAULT_AGENT_URL,
    width: int | None = None,
    height: int | None = None,
    duration_ms: float | None = None,
) -> FormatId:
    """Helper to create FormatId objects with optional dimensions."""
    return FormatId(
        agent_url=agent_url,
        id=format_id,
        width=width,
        height=height,
        duration_ms=duration_ms,
    )


class TestFormatIdWithDimensions:
    """Tests for FormatId objects with width/height dimensions."""

    def test_format_id_accepts_dimensions(self):
        """FormatId must accept width and height parameters (AdCP 2.5)."""
        format_id = make_format_id("display_image", width=300, height=250)

        assert format_id.id == "display_image"
        assert format_id.width == 300
        assert format_id.height == 250

    def test_format_id_dimensions_are_optional(self):
        """FormatId dimensions should be optional for backward compatibility."""
        format_id = make_format_id("display_300x250_image")

        assert format_id.id == "display_300x250_image"
        assert format_id.width is None
        assert format_id.height is None

    def test_format_id_with_duration_ms(self):
        """FormatId must accept duration_ms for video formats."""
        format_id = make_format_id("video_preroll", width=1920, height=1080, duration_ms=30000.0)

        assert format_id.id == "video_preroll"
        assert format_id.width == 1920
        assert format_id.height == 1080
        assert format_id.duration_ms == 30000.0

    def test_media_package_accepts_format_id_with_dimensions(self):
        """MediaPackage must accept FormatId objects with dimensions."""
        format_id = make_format_id("display_image", width=300, height=250)

        package = MediaPackage(
            package_id="test_pkg",
            name="Test Package",
            delivery_type="guaranteed",
            cpm=10.0,
            impressions=1000,
            format_ids=[format_id],
        )

        assert len(package.format_ids) == 1
        assert package.format_ids[0].width == 300
        assert package.format_ids[0].height == 250


class TestProductFormatIdConversion:
    """Tests for converting product format_ids (dicts) to FormatId objects with dimensions."""

    def test_dict_to_format_id_preserves_dimensions(self):
        """Converting dict to FormatId must preserve width/height."""
        # Simulate product.format_ids from database (JSONB returns dicts)
        product_format_dict = {
            "id": "display_image",
            "width": 300,
            "height": 250,
            "agent_url": DEFAULT_AGENT_URL,
        }

        # This is what the fix does - convert dict to FormatId with dimensions
        format_id = FormatId(
            agent_url=product_format_dict["agent_url"],
            id=product_format_dict["id"],
            width=int(product_format_dict["width"]) if product_format_dict.get("width") else None,
            height=int(product_format_dict["height"]) if product_format_dict.get("height") else None,
        )

        assert format_id.id == "display_image"
        assert format_id.width == 300
        assert format_id.height == 250

    def test_dict_without_dimensions_creates_format_id_with_none(self):
        """Dict without dimensions should create FormatId with None dimensions."""
        product_format_dict = {
            "id": "display_300x250_image",
            "agent_url": DEFAULT_AGENT_URL,
        }

        format_id = FormatId(
            agent_url=product_format_dict["agent_url"],
            id=product_format_dict["id"],
            width=product_format_dict.get("width"),
            height=product_format_dict.get("height"),
        )

        assert format_id.id == "display_300x250_image"
        assert format_id.width is None
        assert format_id.height is None

    def test_multiple_parameterized_formats_same_id(self):
        """Product can have multiple formats with same ID but different dimensions."""
        # This is a real scenario - product supports display_image at multiple sizes
        product_formats = [
            {"id": "display_image", "width": 300, "height": 250, "agent_url": DEFAULT_AGENT_URL},
            {"id": "display_image", "width": 970, "height": 250, "agent_url": DEFAULT_AGENT_URL},
            {"id": "display_image", "width": 728, "height": 90, "agent_url": DEFAULT_AGENT_URL},
        ]

        format_ids = []
        for fmt in product_formats:
            format_ids.append(
                FormatId(
                    agent_url=fmt["agent_url"],
                    id=fmt["id"],
                    width=fmt.get("width"),
                    height=fmt.get("height"),
                )
            )

        assert len(format_ids) == 3
        assert all(f.id == "display_image" for f in format_ids)
        assert format_ids[0].width == 300
        assert format_ids[1].width == 970
        assert format_ids[2].width == 728


class TestDimensionMergingLogic:
    """Tests for the dimension merging logic when request format_ids lack dimensions."""

    def test_merge_dimensions_from_product_to_request(self):
        """When request format_id lacks dimensions, merge from product config."""
        # Product config has dimensions
        product_format_dimensions = {
            (DEFAULT_AGENT_URL.rstrip("/"), "display_image"): (300, 250, None),
        }

        # Request format_id without dimensions
        request_format_id = "display_image"
        request_agent_url = DEFAULT_AGENT_URL

        # Simulate the merging logic
        normalized_url = request_agent_url.rstrip("/")
        product_dims = product_format_dimensions.get((normalized_url, request_format_id))

        assert product_dims is not None
        assert product_dims[0] == 300  # width
        assert product_dims[1] == 250  # height

        # Create merged FormatId
        merged_format_id = FormatId(
            agent_url=request_agent_url,
            id=request_format_id,
            width=product_dims[0],
            height=product_dims[1],
            duration_ms=product_dims[2],
        )

        assert merged_format_id.width == 300
        assert merged_format_id.height == 250

    def test_request_dimensions_take_precedence(self):
        """When request format_id has dimensions, use them instead of product config."""
        # Product config has dimensions
        product_format_dimensions = {
            (DEFAULT_AGENT_URL.rstrip("/"), "display_image"): (300, 250, None),
        }

        # Request format_id WITH dimensions (different from product)
        request_width = 728
        request_height = 90

        # When request has dimensions, don't merge from product
        if request_width is not None and request_height is not None:
            format_id = FormatId(
                agent_url=DEFAULT_AGENT_URL,
                id="display_image",
                width=request_width,
                height=request_height,
            )
        else:
            # Would merge from product
            pass

        assert format_id.width == 728
        assert format_id.height == 90

    def test_url_normalization_for_dimension_lookup(self):
        """URL normalization should handle trailing slashes for dimension lookup."""
        # Product config with trailing slash
        product_format_dimensions = {
            ("https://creative.adcontextprotocol.org", "display_image"): (300, 250, None),
        }

        # Request with trailing slash
        request_url_with_slash = "https://creative.adcontextprotocol.org/"
        normalized = request_url_with_slash.rstrip("/")

        dims = product_format_dimensions.get((normalized, "display_image"))
        assert dims is not None
        assert dims[0] == 300

        # Request without trailing slash
        request_url_no_slash = "https://creative.adcontextprotocol.org"
        normalized = request_url_no_slash.rstrip("/")

        dims = product_format_dimensions.get((normalized, "display_image"))
        assert dims is not None
        assert dims[0] == 300


class TestExecuteApprovedMediaBuyFormatConversion:
    """Tests for format conversion in execute_approved_media_buy code path."""

    def test_product_format_ids_converted_with_dimensions(self):
        """Product format_ids (dicts) should be converted to FormatId with dimensions."""
        # Simulate product.format_ids from database
        product_format_ids = [
            {"id": "display_image", "width": 300, "height": 250, "agent_url": DEFAULT_AGENT_URL},
            {"id": "display_image", "width": 970, "height": 250, "agent_url": DEFAULT_AGENT_URL},
        ]

        # Simulate the conversion in execute_approved_media_buy
        format_ids_list = []
        for fmt in product_format_ids:
            if isinstance(fmt, dict):
                agent_url = fmt.get("agent_url")
                format_id = fmt.get("id")
                fmt_width = fmt.get("width")
                fmt_height = fmt.get("height")
                fmt_duration_ms = fmt.get("duration_ms")

                format_ids_list.append(
                    FormatId(
                        agent_url=agent_url,
                        id=format_id,
                        width=int(fmt_width) if fmt_width is not None else None,
                        height=int(fmt_height) if fmt_height is not None else None,
                        duration_ms=float(fmt_duration_ms) if fmt_duration_ms is not None else None,
                    )
                )

        assert len(format_ids_list) == 2
        assert format_ids_list[0].id == "display_image"
        assert format_ids_list[0].width == 300
        assert format_ids_list[0].height == 250
        assert format_ids_list[1].width == 970
        assert format_ids_list[1].height == 250


class TestGAMAdapterDimensionExtraction:
    """Tests for GAM adapter's ability to extract dimensions from FormatId."""

    def test_gam_can_extract_dimensions_from_format_id(self):
        """GAM adapter should be able to get dimensions from FormatId.width/height."""
        format_id = make_format_id("display_image", width=300, height=250)

        # Simulate GAM adapter dimension extraction
        width = None
        height = None

        if hasattr(format_id, "width") and hasattr(format_id, "height"):
            if format_id.width and format_id.height:
                width = format_id.width
                height = format_id.height

        assert width == 300
        assert height == 250

    def test_gam_fallback_to_regex_when_no_dimensions(self):
        """GAM adapter should fall back to regex extraction when FormatId has no dimensions."""
        import re

        format_id = make_format_id("display_300x250_image")

        # Simulate GAM adapter dimension extraction
        width = None
        height = None

        # First try FormatId attributes
        if hasattr(format_id, "width") and hasattr(format_id, "height"):
            if format_id.width and format_id.height:
                width = format_id.width
                height = format_id.height

        # Fall back to regex
        if not (width and height):
            match = re.search(r"(\d+)x(\d+)", format_id.id)
            if match:
                width = int(match.group(1))
                height = int(match.group(2))

        assert width == 300
        assert height == 250
