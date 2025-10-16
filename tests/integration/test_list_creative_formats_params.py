"""Integration tests for list_creative_formats filtering parameters.

These are integration tests because they:
1. Use real database queries (FORMAT_REGISTRY + CreativeFormat table)
2. Exercise the full implementation stack (tools.py → main.py → database)
3. Test tenant resolution and audit logging
4. Validate actual filtering logic with real data

Per architecture guidelines: "Integration over Mocking - Use real DB, mock only external services"
"""

from datetime import UTC, datetime
from unittest.mock import patch

from src.core.schemas import Format, ListCreativeFormatsRequest
from src.core.tool_context import ToolContext
from src.core.tools import list_creative_formats_raw


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
    from src.core.schemas import FormatId

    # AdCP v2.4 requires structured FormatId objects, not strings
    format_ids = [
        FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_16x9"),
        FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_4x3"),
    ]

    req = ListCreativeFormatsRequest(
        adcp_version="1.5.0",
        type="video",
        standard_only=True,
        category="standard",
        format_ids=format_ids,
    )
    assert req.adcp_version == "1.5.0"
    assert req.type == "video"
    assert req.standard_only is True
    assert req.category == "standard"
    assert len(req.format_ids) == 2
    assert req.format_ids[0].id == "video_16x9"
    assert req.format_ids[1].id == "video_4x3"


def test_filtering_by_type(integration_db, sample_tenant):
    """Test that type filter works correctly."""
    # Create real ToolContext
    context = ToolContext(
        context_id="test",
        tenant_id=sample_tenant["tenant_id"],
        principal_id="test_principal",
        tool_name="list_creative_formats",
        request_timestamp=datetime.now(UTC),
        metadata={},
        testing_context={},
    )

    # Mock tenant resolution to return our test tenant
    with patch("src.core.main.get_current_tenant", return_value=sample_tenant):
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


def test_filtering_by_standard_only(integration_db, sample_tenant):
    """Test that standard_only filter works correctly."""
    # Create real ToolContext
    context = ToolContext(
        context_id="test",
        tenant_id=sample_tenant["tenant_id"],
        principal_id="test_principal",
        tool_name="list_creative_formats",
        request_timestamp=datetime.now(UTC),
        metadata={},
        testing_context={},
    )

    # Mock tenant resolution to return our test tenant
    with patch("src.core.main.get_current_tenant", return_value=sample_tenant):
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


def test_filtering_by_format_ids(integration_db, sample_tenant):
    """Test that format_ids filter works correctly."""
    # Create real ToolContext
    context = ToolContext(
        context_id="test",
        tenant_id=sample_tenant["tenant_id"],
        principal_id="test_principal",
        tool_name="list_creative_formats",
        request_timestamp=datetime.now(UTC),
        metadata={},
        testing_context={},
    )

    # Mock tenant resolution to return our test tenant
    with patch("src.core.main.get_current_tenant", return_value=sample_tenant):
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


def test_filtering_combined(integration_db, sample_tenant):
    """Test that multiple filters work together."""
    # Create real ToolContext
    context = ToolContext(
        context_id="test",
        tenant_id=sample_tenant["tenant_id"],
        principal_id="test_principal",
        tool_name="list_creative_formats",
        request_timestamp=datetime.now(UTC),
        metadata={},
        testing_context={},
    )

    # Mock tenant resolution to return our test tenant
    with patch("src.core.main.get_current_tenant", return_value=sample_tenant):
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
        assert len(formats) > 0, "Should have at least some display standard formats"
