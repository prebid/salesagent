"""Unit tests for _validate_creative_assets helper function."""

import pytest

from src.core.main import _validate_creative_assets


def test_validate_assets_valid_dict():
    """Test that valid dict assets pass through unchanged."""
    assets = {"main_image": {"asset_type": "image", "url": "https://example.com/image.jpg"}}

    result = _validate_creative_assets(assets)

    assert result == assets


def test_validate_assets_none():
    """Test that None assets return None."""
    result = _validate_creative_assets(None)

    assert result is None


def test_validate_assets_empty_dict():
    """Test that empty dict passes through."""
    assets = {}

    result = _validate_creative_assets(assets)

    assert result == {}


def test_validate_assets_multiple_assets():
    """Test dict with multiple assets."""
    assets = {
        "hero_image": {"asset_type": "image", "url": "https://example.com/hero.jpg"},
        "logo": {"asset_type": "image", "url": "https://example.com/logo.jpg"},
    }

    result = _validate_creative_assets(assets)

    assert result == assets


def test_validate_assets_list_rejected():
    """Test that list format is rejected."""
    assets = [{"asset_type": "image", "url": "https://example.com/image.jpg"}]

    with pytest.raises(ValueError, match="Invalid assets format.*expected dict.*got list"):
        _validate_creative_assets(assets)


def test_validate_assets_string_rejected():
    """Test that string format is rejected."""
    assets = "invalid_string"

    with pytest.raises(ValueError, match="Invalid assets format.*expected dict.*got str"):
        _validate_creative_assets(assets)


def test_validate_assets_int_rejected():
    """Test that int format is rejected."""
    assets = 123

    with pytest.raises(ValueError, match="Invalid assets format.*expected dict.*got int"):
        _validate_creative_assets(assets)


def test_validate_assets_non_string_key():
    """Test that non-string asset keys are rejected."""
    assets = {123: {"asset_type": "image", "url": "https://example.com/image.jpg"}}

    with pytest.raises(ValueError, match="Asset key must be a string.*got int"):
        _validate_creative_assets(assets)


def test_validate_assets_empty_string_key():
    """Test that empty string asset keys are rejected."""
    assets = {"": {"asset_type": "image", "url": "https://example.com/image.jpg"}}

    with pytest.raises(ValueError, match="Asset key.*cannot be empty"):
        _validate_creative_assets(assets)


def test_validate_assets_whitespace_only_key():
    """Test that whitespace-only asset keys are rejected."""
    assets = {"   ": {"asset_type": "image", "url": "https://example.com/image.jpg"}}

    with pytest.raises(ValueError, match="Asset key.*cannot be empty"):
        _validate_creative_assets(assets)


def test_validate_assets_non_dict_value():
    """Test that non-dict asset values are rejected."""
    assets = {"main_image": "not_a_dict"}

    with pytest.raises(ValueError, match="Asset 'main_image' data must be a dict.*got str"):
        _validate_creative_assets(assets)


def test_validate_assets_list_value():
    """Test that list asset values are rejected."""
    assets = {"main_image": [{"asset_type": "image"}]}

    with pytest.raises(ValueError, match="Asset 'main_image' data must be a dict.*got list"):
        _validate_creative_assets(assets)
