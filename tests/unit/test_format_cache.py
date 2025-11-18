"""Test format cache for backward compatibility."""

import pytest

from src.core.format_cache import (
    DEFAULT_AGENT_URL,
    get_agent_url_for_format,
    upgrade_legacy_format_id,
)
from src.core.schemas import FormatId


def test_upgrade_legacy_string_format():
    """Test upgrading legacy string format_id."""
    result = upgrade_legacy_format_id("display_300x250")

    assert isinstance(result, FormatId)
    assert result.id == "display_300x250"
    assert str(result.agent_url).rstrip("/") == DEFAULT_AGENT_URL.rstrip("/")  # AnyUrl adds trailing slash


def test_upgrade_format_id_object_passthrough():
    """Test FormatId objects pass through unchanged."""
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


def test_upgrade_dict_without_agent_url():
    """Test dict without agent_url uses default."""
    result = upgrade_legacy_format_id({"id": "display_300x250"})

    assert isinstance(result, FormatId)
    assert result.id == "display_300x250"
    assert str(result.agent_url).rstrip("/") == DEFAULT_AGENT_URL.rstrip("/")  # AnyUrl adds trailing slash


def test_get_agent_url_for_format():
    """Test getting agent URL for format ID."""
    # Known format should return default agent URL (from cache)
    url = get_agent_url_for_format("display_300x250")
    assert url == DEFAULT_AGENT_URL

    # Unknown format should also return default
    url = get_agent_url_for_format("unknown_format")
    assert url == DEFAULT_AGENT_URL


def test_upgrade_invalid_type():
    """Test upgrading invalid type raises error."""
    with pytest.raises(ValueError, match="Invalid format_id type"):
        upgrade_legacy_format_id(12345)  # type: ignore


def test_upgrade_unknown_string_format_fails():
    """Test unknown string format_id raises error (doesn't default)."""
    with pytest.raises(ValueError, match="Unknown format_id.*String format_ids are deprecated"):
        upgrade_legacy_format_id("unknown_custom_format_xyz")


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

    for format_id in common_formats:
        result = upgrade_legacy_format_id(format_id)
        assert result.id == format_id
        assert str(result.agent_url).rstrip("/") == DEFAULT_AGENT_URL.rstrip("/")  # AnyUrl adds trailing slash
