"""Test creative conversion with new assets dict format."""

from datetime import UTC, datetime

from src.core.helpers.creative_helpers import _convert_creative_to_adapter_asset
from src.core.schemas import Creative, FormatId


def test_convert_image_creative_from_assets():
    """Test converting image creative with assets dict to adapter format.

    Per AdCP v1 image-asset.json spec:
    - Required: url
    - Optional: width, height, format, alt_text
    - No asset_type field (detection based on presence of url + width/height)
    """
    creative = Creative(
        creative_id="test_123",
        name="Test Banner",
        format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
        assets={
            "banner_image": {
                "url": "https://example.com/banner.jpg",
                "width": 300,
                "height": 250,
            },
            "click_url": {"url": "https://example.com/landing", "url_type": "clickthrough"},
        },
        principal_id="prin_123",
        created_date=datetime.now(UTC),
        updated_date=datetime.now(UTC),
    )

    adapter_asset = _convert_creative_to_adapter_asset(creative, ["pkg_1"])

    assert adapter_asset["creative_id"] == "test_123"
    assert adapter_asset["name"] == "Test Banner"
    assert adapter_asset["format"] == "display_300x250"
    assert adapter_asset["media_url"] == "https://example.com/banner.jpg"
    assert adapter_asset["url"] == "https://example.com/banner.jpg"
    assert adapter_asset["click_url"] == "https://example.com/landing"
    assert adapter_asset["width"] == 300
    assert adapter_asset["height"] == 250
    assert adapter_asset["package_assignments"] == ["pkg_1"]


def test_convert_video_creative_from_assets():
    """Test converting video creative with assets dict.

    Per AdCP v1 video-asset.json spec:
    - Required: url
    - Optional: width, height, duration_ms (NOT duration), format, bitrate_kbps
    - No asset_type field (detection based on presence of url + duration_ms)
    - Conversion function converts duration_ms to seconds for adapter
    """
    creative = Creative(
        creative_id="video_456",
        name="Test Video",
        format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_1280x720"),
        assets={
            "video_file": {
                "url": "https://example.com/video.mp4",
                "width": 1280,
                "height": 720,
                "duration_ms": 30000,  # 30 seconds in milliseconds (spec field)
            },
            "clickthrough": {"url": "https://example.com/product", "url_type": "clickthrough"},
        },
    )

    adapter_asset = _convert_creative_to_adapter_asset(creative, ["pkg_2"])

    assert adapter_asset["creative_id"] == "video_456"
    assert adapter_asset["media_url"] == "https://example.com/video.mp4"
    assert adapter_asset["click_url"] == "https://example.com/product"
    assert adapter_asset["width"] == 1280
    assert adapter_asset["height"] == 720
    assert adapter_asset["duration"] == 30.0  # Converted to seconds for adapter


def test_convert_html_creative_from_assets():
    """Test converting HTML snippet creative.

    Per AdCP v1 html-asset.json spec:
    - Required: content
    - Optional: version
    - No asset_type field (detection based on presence of content, no url)
    - Detection: has content, no url, no module_type → HTML
    """
    creative = Creative(
        creative_id="html_789",
        name="HTML Ad",
        format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="html_300x250"),
        assets={
            "main": {
                "content": "<div>Ad content</div>",
            }
        },
    )

    adapter_asset = _convert_creative_to_adapter_asset(creative, ["pkg_3"])

    assert adapter_asset["creative_id"] == "html_789"
    assert adapter_asset["snippet"] == "<div>Ad content</div>"
    assert adapter_asset["snippet_type"] == "html"


def test_convert_creative_with_tracking_urls():
    """Test extracting tracking URLs from assets.

    Per AdCP v1 url-asset.json spec:
    - Required: url
    - Optional: url_type (clickthrough | tracker_pixel | tracker_script)
    - No asset_type field
    - tracker_pixel = impression tracking (image pixel)
    - tracker_script = impression tracking (JavaScript SDK)
    """
    creative = Creative(
        creative_id="tracked_123",
        name="Tracked Creative",
        format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_728x90"),
        assets={
            "banner_image": {"url": "https://example.com/banner.jpg"},
            "impression_tracker_1": {
                "url": "https://tracker.example.com/imp1",
                "url_type": "tracker_pixel",  # Spec-compliant url_type
            },
            "impression_tracker_2": {
                "url": "https://tracker.example.com/imp2",
                "url_type": "tracker_script",  # Spec-compliant url_type
            },
        },
    )

    adapter_asset = _convert_creative_to_adapter_asset(creative, ["pkg_4"])

    assert "delivery_settings" in adapter_asset
    assert "tracking_urls" in adapter_asset["delivery_settings"]
    tracking = adapter_asset["delivery_settings"]["tracking_urls"]
    assert "impression" in tracking
    assert len(tracking["impression"]) == 2
    assert "https://tracker.example.com/imp1" in tracking["impression"]
    assert "https://tracker.example.com/imp2" in tracking["impression"]


def test_convert_javascript_creative_from_assets():
    """Test converting JavaScript creative with assets dict.

    Per AdCP v1 javascript-asset.json spec:
    - Required: content
    - Optional: module_type (esm | cjs | iife)
    - No asset_type field
    - Detection: has content, no url, has module_type OR role contains 'javascript' → JavaScript
    """
    creative = Creative(
        creative_id="js_101",
        name="JavaScript Ad",
        format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="js_widget"),
        assets={
            "javascript_tag": {
                "content": "(function() { console.log('Ad loaded'); })()",
                "module_type": "iife",
            }
        },
    )

    adapter_asset = _convert_creative_to_adapter_asset(creative, ["pkg_5"])

    assert adapter_asset["creative_id"] == "js_101"
    assert adapter_asset["snippet"] == "(function() { console.log('Ad loaded'); })()"
    assert adapter_asset["snippet_type"] == "javascript"


def test_convert_vast_url_creative():
    """Test converting VAST URL creative.

    Per AdCP v1 vast-asset.json spec:
    - Required: url XOR content (exactly one, never both)
    - Optional: vast_version, vpaid_enabled, duration_ms, tracking_events
    - No asset_type field
    - Detection: has url, role contains 'vast' → VAST URL
    - Conversion function converts duration_ms to seconds
    """
    creative = Creative(
        creative_id="vast_202",
        name="VAST Video Ad",
        format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_vast"),
        assets={
            "vast_tag": {
                "url": "https://example.com/vast.xml",
                "vast_version": "4.0",
                "duration_ms": 15000,  # 15 seconds
            }
        },
    )

    adapter_asset = _convert_creative_to_adapter_asset(creative, ["pkg_6"])

    assert adapter_asset["creative_id"] == "vast_202"
    assert adapter_asset["snippet"] == "https://example.com/vast.xml"
    assert adapter_asset["snippet_type"] == "vast_url"
    assert adapter_asset["duration"] == 15.0  # Converted to seconds


def test_convert_vast_xml_creative():
    """Test converting VAST XML inline creative.

    Per AdCP v1 vast-asset.json spec:
    - Required: url XOR content (exactly one, never both)
    - Optional: vast_version, vpaid_enabled, duration_ms, tracking_events
    - No asset_type field
    - Detection: has content, role contains 'vast' → VAST XML
    - Conversion function converts duration_ms to seconds
    """
    vast_xml = '<?xml version="1.0"?><VAST version="4.0">...</VAST>'
    creative = Creative(
        creative_id="vast_303",
        name="VAST XML Ad",
        format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_vast"),
        assets={
            "vast_xml": {
                "content": vast_xml,
                "vast_version": "4.0",
                "duration_ms": 30000,  # 30 seconds
            }
        },
    )

    adapter_asset = _convert_creative_to_adapter_asset(creative, ["pkg_7"])

    assert adapter_asset["creative_id"] == "vast_303"
    assert adapter_asset["snippet"] == vast_xml
    assert adapter_asset["snippet_type"] == "vast_xml"
    assert adapter_asset["duration"] == 30.0  # Converted to seconds


def test_convert_creative_without_url_type_fallback():
    """Test click URL extraction when url_type is missing (fallback to role name).

    When url_type field is not present, the conversion function should fall back
    to role name matching for backward compatibility with legacy data.
    """
    creative = Creative(
        creative_id="fallback_404",
        name="Legacy Click URL Format",
        format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_468x60"),
        assets={
            "banner_image": {"url": "https://example.com/banner.jpg"},
            "click": {"url": "https://example.com/landing"},  # No url_type field
        },
    )

    adapter_asset = _convert_creative_to_adapter_asset(creative, ["pkg_8"])

    # Should fall back to role name "click" for click URL extraction
    assert adapter_asset["click_url"] == "https://example.com/landing"
