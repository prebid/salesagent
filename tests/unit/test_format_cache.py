"""Test format cache for backward compatibility."""

import pytest

from src.core.format_cache import (
    DEFAULT_AGENT_URL,
    canonical_format_identity,
    canonical_format_matches,
    get_agent_url_for_format,
    upgrade_legacy_format_id,
)
from src.core.schemas import FormatId


def test_upgrade_legacy_string_format():
    """Legacy fixed-size display IDs normalize to canonical display_image."""
    result = upgrade_legacy_format_id("display_300x250")

    assert isinstance(result, FormatId)
    assert result.id == "display_image"
    assert result.width == 300
    assert result.height == 250
    assert str(result.agent_url).rstrip("/") == DEFAULT_AGENT_URL.rstrip("/")  # AnyUrl adds trailing slash


def test_upgrade_format_id_object_passthrough():
    """Custom-agent FormatId objects pass through unchanged."""
    original = FormatId(agent_url="https://custom.example.com", id="custom_format")
    result = upgrade_legacy_format_id(original)

    assert result is original
    assert str(result.agent_url).rstrip("/") == "https://custom.example.com"  # AnyUrl adds trailing slash


def test_upgrade_dict_with_agent_url():
    """Test dict with agent_url converts to FormatId."""
    result = upgrade_legacy_format_id({"agent_url": "https://custom.example.com", "id": "custom_format"})

    assert isinstance(result, FormatId)
    assert str(result.agent_url).rstrip("/") == "https://custom.example.com"  # AnyUrl adds trailing slash
    assert result.id == "custom_format"


def test_upgrade_structured_legacy_reference_agent_format_normalizes_to_canonical():
    """Structured legacy reference-agent IDs normalize without requiring string input."""
    result = upgrade_legacy_format_id(
        {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90_html"}
    )

    assert result.id == "display_html"
    assert result.width == 728
    assert result.height == 90


def test_custom_agent_legacy_like_format_id_is_not_rewritten():
    """Only the reference agent's legacy IDs are canonicalized."""
    result = upgrade_legacy_format_id({"agent_url": "https://custom.example.com", "id": "display_300x250"})

    assert result.id == "display_300x250"
    assert result.width is None
    assert result.height is None


def test_upgrade_dict_without_agent_url():
    """Test dict without agent_url uses default."""
    result = upgrade_legacy_format_id({"id": "display_300x250"})

    assert isinstance(result, FormatId)
    assert result.id == "display_image"
    assert result.width == 300
    assert result.height == 250
    assert str(result.agent_url).rstrip("/") == DEFAULT_AGENT_URL.rstrip("/")  # AnyUrl adds trailing slash


def test_get_agent_url_for_format():
    """Test getting agent URL for format ID."""
    # Known format should return default agent URL (from cache)
    url = get_agent_url_for_format("display_300x250")
    assert url == DEFAULT_AGENT_URL

    # Unknown format should also return default
    url = get_agent_url_for_format("unknown_format")
    assert url == DEFAULT_AGENT_URL


def test_canonical_format_identity_preserves_dimensions_for_assignment_matching():
    legacy = canonical_format_identity({"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250"})
    canonical = canonical_format_identity(
        {"agent_url": DEFAULT_AGENT_URL, "id": "display_image", "width": 300, "height": 250}
    )
    different_size = canonical_format_identity({"agent_url": DEFAULT_AGENT_URL, "id": "display_728x90"})

    assert legacy == canonical
    assert legacy != different_size


def test_canonical_format_identity_accepts_mcp_suffix_and_format_id_alias():
    legacy_alias = canonical_format_identity({"agent_url": f"{DEFAULT_AGENT_URL}/mcp/", "format_id": "display_300x250"})
    canonical = canonical_format_identity(
        {"agent_url": DEFAULT_AGENT_URL, "id": "display_image", "width": 300, "height": 250}
    )

    assert legacy_alias == canonical


def test_canonical_format_matches_legacy_fixed_size_to_canonical_parameters():
    assert canonical_format_matches(
        {"agent_url": DEFAULT_AGENT_URL, "id": "display_image", "width": 300, "height": 250},
        {"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250"},
    )
    assert not canonical_format_matches(
        {"agent_url": DEFAULT_AGENT_URL, "id": "display_image", "width": 728, "height": 90},
        {"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250"},
    )


def test_canonical_format_matches_respects_duration_when_both_sides_are_specific():
    assert canonical_format_matches(
        {"agent_url": DEFAULT_AGENT_URL, "id": "video_vast", "duration_ms": 15000},
        {"agent_url": DEFAULT_AGENT_URL, "id": "video_vast", "duration_ms": 15000},
    )
    assert not canonical_format_matches(
        {"agent_url": DEFAULT_AGENT_URL, "id": "video_vast", "duration_ms": 15000},
        {"agent_url": DEFAULT_AGENT_URL, "id": "video_vast", "duration_ms": 30000},
    )
    assert canonical_format_matches(
        {"agent_url": DEFAULT_AGENT_URL, "id": "video_vast"},
        {"agent_url": DEFAULT_AGENT_URL, "id": "video_vast", "duration_ms": 30000},
    )


def test_upgrade_invalid_type():
    """Test upgrading invalid type raises error."""
    with pytest.raises(ValueError, match="Invalid format_id type"):
        upgrade_legacy_format_id(12345)  # type: ignore


def test_upgrade_unknown_string_format_fails():
    """Test unknown string format_id raises error (doesn't default)."""
    with pytest.raises(ValueError, match="Unknown format_id.*String format_ids are deprecated"):
        upgrade_legacy_format_id("unknown_custom_format_xyz")


def test_upgrade_legacy_string_emits_deprecation_warning():
    """String format_id upgrade emits DeprecationWarning naming the sunset target.

    Buyers still on the legacy wire shape need a migration signal in their own
    test runs / log streams, not just a server-side log line. Issue #289.
    """
    from src.core._deprecations import LEGACY_FORMAT_ID_SUNSET

    with pytest.warns(DeprecationWarning, match=r"display_300x250") as captured:
        result = upgrade_legacy_format_id("display_300x250")

    assert isinstance(result, FormatId)
    assert result.id == "display_image"
    assert result.width == 300
    assert result.height == 250
    assert any(LEGACY_FORMAT_ID_SUNSET in str(w.message) for w in captured), (
        f"sunset version {LEGACY_FORMAT_ID_SUNSET} should appear in the warning"
    )


def test_common_formats_in_cache():
    """Test common IAB formats are in the cache."""
    common_formats = [
        "display_300x250",
        "display_728x90",
        "display_160x600",
        "video_640x480",
        "audio_30s",
        "native_1x1",
    ]

    expected = {
        "display_300x250": ("display_image", 300, 250),
        "display_728x90": ("display_image", 728, 90),
        "display_160x600": ("display_image", 160, 600),
        "video_640x480": ("video_standard", 640, 480),
        "audio_30s": ("audio_30s", None, None),
        "native_1x1": ("native_standard", None, None),
    }

    for format_id in common_formats:
        result = upgrade_legacy_format_id(format_id)
        expected_id, expected_width, expected_height = expected[format_id]
        assert result.id == expected_id
        assert result.width == expected_width
        assert result.height == expected_height
        assert str(result.agent_url).rstrip("/") == DEFAULT_AGENT_URL.rstrip("/")  # AnyUrl adds trailing slash
