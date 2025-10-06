"""Test that list_creative_formats accepts and uses filter parameters."""

from src.core.schemas import ListCreativeFormatsRequest


def test_list_creative_formats_request_minimal():
    """Test that ListCreativeFormatsRequest works with no params (all defaults)."""
    req = ListCreativeFormatsRequest()
    assert req.adcp_version == "1.0.0"
    assert req.type is None
    assert req.standard_only is None
    assert req.category is None
    assert req.format_ids is None


def test_list_creative_formats_request_with_all_params():
    """Test that ListCreativeFormatsRequest accepts all optional filter parameters."""
    req = ListCreativeFormatsRequest(
        adcp_version="1.5.0",
        type="video",
        standard_only=True,
        category="standard",
        format_ids=["video_16x9", "video_4x3"],
    )
    assert req.adcp_version == "1.5.0"
    assert req.type == "video"
    assert req.standard_only is True
    assert req.category == "standard"
    assert req.format_ids == ["video_16x9", "video_4x3"]


def test_filtering_by_type():
    """Test that type filter works correctly."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    from src.core.schemas import Format
    from src.core.tool_context import ToolContext
    from src.core.tools import list_creative_formats_raw

    # Create real ToolContext
    context = ToolContext(
        context_id="test",
        tenant_id="test_tenant",
        principal_id="test_principal",
        tool_name="list_creative_formats",
        request_timestamp=datetime.now(UTC),
        metadata={},
        testing_context={},
    )

    # Mock get_current_tenant to return a test tenant
    with patch("src.core.main.get_current_tenant", return_value={"tenant_id": "test_tenant"}):
        # Test filtering by type
        req = ListCreativeFormatsRequest(type="video")
        response = list_creative_formats_raw(req, context)

        # Handle both dict and object responses
        if isinstance(response, dict):
            formats = response.get("formats", [])
            # Convert dicts to Format objects if needed
            if formats and isinstance(formats[0], dict):
                formats = [Format(**f) for f in formats]
        else:
            formats = response.formats

        # All returned formats should be video type
        assert all(f.type == "video" for f in formats), "All formats should be video type"
        assert len(formats) > 0, "Should have at least some video formats"


def test_filtering_by_standard_only():
    """Test that standard_only filter works correctly."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    from src.core.schemas import Format
    from src.core.tool_context import ToolContext
    from src.core.tools import list_creative_formats_raw

    # Create real ToolContext
    context = ToolContext(
        context_id="test",
        tenant_id="test_tenant",
        principal_id="test_principal",
        tool_name="list_creative_formats",
        request_timestamp=datetime.now(UTC),
        metadata={},
        testing_context={},
    )

    # Mock get_current_tenant to return a test tenant
    with patch("src.core.main.get_current_tenant", return_value={"tenant_id": "test_tenant"}):
        # Test filtering by standard_only
        req = ListCreativeFormatsRequest(standard_only=True)
        response = list_creative_formats_raw(req, context)

        # Handle both dict and object responses
        if isinstance(response, dict):
            formats = response.get("formats", [])
            if formats and isinstance(formats[0], dict):
                formats = [Format(**f) for f in formats]
        else:
            formats = response.formats

        # All returned formats should be standard
        assert all(f.is_standard for f in formats), "All formats should be standard"
        assert len(formats) > 0, "Should have at least some standard formats"


def test_filtering_by_format_ids():
    """Test that format_ids filter works correctly."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    from src.core.schemas import Format
    from src.core.tool_context import ToolContext
    from src.core.tools import list_creative_formats_raw

    # Create real ToolContext
    context = ToolContext(
        context_id="test",
        tenant_id="test_tenant",
        principal_id="test_principal",
        tool_name="list_creative_formats",
        request_timestamp=datetime.now(UTC),
        metadata={},
        testing_context={},
    )

    # Mock get_current_tenant to return a test tenant
    with patch("src.core.main.get_current_tenant", return_value={"tenant_id": "test_tenant"}):
        # Test filtering by specific format IDs
        target_ids = ["display_300x250", "display_728x90"]
        req = ListCreativeFormatsRequest(format_ids=target_ids)
        response = list_creative_formats_raw(req, context)

        # Handle both dict and object responses
        if isinstance(response, dict):
            formats = response.get("formats", [])
            if formats and isinstance(formats[0], dict):
                formats = [Format(**f) for f in formats]
        else:
            formats = response.formats

        # Should only return the requested formats (that exist)
        returned_ids = [f.format_id for f in formats]
        assert all(f.format_id in target_ids for f in formats), "All formats should be in target list"
        # At least one of the target formats should exist
        assert len(formats) > 0, "Should return at least one format if they exist"


def test_filtering_combined():
    """Test that multiple filters work together."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    from src.core.schemas import Format
    from src.core.tool_context import ToolContext
    from src.core.tools import list_creative_formats_raw

    # Create real ToolContext
    context = ToolContext(
        context_id="test",
        tenant_id="test_tenant",
        principal_id="test_principal",
        tool_name="list_creative_formats",
        request_timestamp=datetime.now(UTC),
        metadata={},
        testing_context={},
    )

    # Mock get_current_tenant to return a test tenant
    with patch("src.core.main.get_current_tenant", return_value={"tenant_id": "test_tenant"}):
        # Test combining type and standard_only filters
        req = ListCreativeFormatsRequest(type="display", standard_only=True)
        response = list_creative_formats_raw(req, context)

        # Handle both dict and object responses
        if isinstance(response, dict):
            formats = response.get("formats", [])
            if formats and isinstance(formats[0], dict):
                formats = [Format(**f) for f in formats]
        else:
            formats = response.formats

        # All returned formats should match both filters
        assert all(f.type == "display" and f.is_standard for f in formats), "All formats should be display AND standard"
