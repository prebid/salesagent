"""Unit tests for _validate_creative_assets helper function.

Every rejection is BUYER input, so it must be a typed ``AdCPValidationError``
(``VALIDATION_ERROR`` / ``correctable``) rather than a bare ``ValueError``: an
untyped exception raised inside the creative-agent ``try`` in ``_processing.py``
is reported to the buyer as "agent unreachable … retry recommended"
(``transient``) — a retry hint for an error only the buyer can fix.
"""

import pytest

from src.core.exceptions import AdCPValidationError
from src.core.helpers import _validate_creative_assets
from tests.factories.creative_asset import make_legacy_asset_dict


def test_validate_assets_valid_dict():
    """Test that valid dict assets pass through unchanged."""
    assets = make_legacy_asset_dict("main_image", asset_type="image", url="https://example.com/image.jpg")

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
        **make_legacy_asset_dict("hero_image", asset_type="image", url="https://example.com/hero.jpg"),
        **make_legacy_asset_dict("logo", asset_type="image", url="https://example.com/logo.jpg"),
    }

    result = _validate_creative_assets(assets)

    assert result == assets


def test_validate_assets_list_rejected():
    """Test that list format is rejected."""
    assets = [{"asset_type": "image", "url": "https://example.com/image.jpg"}]

    with pytest.raises(AdCPValidationError, match="Invalid assets format.*expected dict.*got list"):
        _validate_creative_assets(assets)


def test_validate_assets_string_rejected():
    """Test that string format is rejected."""
    assets = "invalid_string"

    with pytest.raises(AdCPValidationError, match="Invalid assets format.*expected dict.*got str"):
        _validate_creative_assets(assets)


def test_validate_assets_int_rejected():
    """Test that int format is rejected."""
    assets = 123

    with pytest.raises(AdCPValidationError, match="Invalid assets format.*expected dict.*got int"):
        _validate_creative_assets(assets)


def test_validate_assets_non_string_key():
    """Test that non-string asset keys are rejected."""
    assets = {123: {"asset_type": "image", "url": "https://example.com/image.jpg"}}

    with pytest.raises(AdCPValidationError, match="Asset key must be a string.*got int"):
        _validate_creative_assets(assets)


def test_validate_assets_empty_string_key():
    """Test that empty string asset keys are rejected."""
    assets = {"": {"asset_type": "image", "url": "https://example.com/image.jpg"}}

    with pytest.raises(AdCPValidationError, match="Asset key.*cannot be empty"):
        _validate_creative_assets(assets)


def test_validate_assets_whitespace_only_key():
    """Test that whitespace-only asset keys are rejected."""
    assets = {"   ": {"asset_type": "image", "url": "https://example.com/image.jpg"}}

    with pytest.raises(AdCPValidationError, match="Asset key.*cannot be empty"):
        _validate_creative_assets(assets)


def test_validate_assets_non_dict_value():
    """Test that non-dict asset values are rejected."""
    assets = {"main_image": "not_a_dict"}

    with pytest.raises(AdCPValidationError, match="Asset 'main_image' data must be a dict.*got str"):
        _validate_creative_assets(assets)


def test_validate_assets_list_value():
    """Test that list asset values are rejected."""
    assets = {"main_image": [{"asset_type": "image"}]}

    with pytest.raises(AdCPValidationError, match="Asset 'main_image' data must be a dict.*got list"):
        _validate_creative_assets(assets)


def test_validate_assets_uppercase_key_rejected():
    """Asset slot keys must be lowercase per AdCP creative-manifests spec (^[a-z0-9_]+$)."""
    assets = make_legacy_asset_dict("MainImage", asset_type="image", url="https://example.com/image.jpg")

    with pytest.raises(AdCPValidationError, match=r"asset_id must match \^\[a-z0-9_\]\+\$"):
        _validate_creative_assets(assets)


def test_validate_assets_hyphenated_key_rejected():
    """Asset slot keys must not contain hyphens per AdCP creative-manifests spec."""
    assets = make_legacy_asset_dict("main-image", asset_type="image", url="https://example.com/image.jpg")

    with pytest.raises(AdCPValidationError, match=r"asset_id must match \^\[a-z0-9_\]\+\$"):
        _validate_creative_assets(assets)


def test_validate_assets_dotted_key_rejected():
    """Asset slot keys must not contain periods per AdCP creative-manifests spec."""
    assets = make_legacy_asset_dict("main.image", asset_type="image", url="https://example.com/image.jpg")

    with pytest.raises(AdCPValidationError, match=r"asset_id must match \^\[a-z0-9_\]\+\$"):
        _validate_creative_assets(assets)


def test_validate_assets_lowercase_underscore_key_accepted():
    """A key matching ^[a-z0-9_]+$ (lowercase + digits + underscore) is valid."""
    assets = make_legacy_asset_dict("main_image_2", asset_type="image", url="https://example.com/image.jpg")

    result = _validate_creative_assets(assets)

    assert result == assets


def test_rejection_is_classified_correctable():
    """The rejection carries the buyer-fixable classification, not a retry hint.

    Pins the reason this helper raises a typed error: ``VALIDATION_ERROR`` is
    ``correctable`` in ``enums/error-code.json @ 3.1.1``, so the buyer is told to
    fix the asset key rather than to retry an unreachable agent.
    """
    assets = make_legacy_asset_dict("Main-Image", asset_type="image", url="https://example.com/image.jpg")

    with pytest.raises(AdCPValidationError) as exc_info:
        _validate_creative_assets(assets)

    assert exc_info.value.error_code == "VALIDATION_ERROR"
    assert exc_info.value.recovery == "correctable"
