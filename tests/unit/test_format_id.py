"""Test FormatId and Creative format_id field with AdCP v2.4 requirements."""

from datetime import datetime

import pytest

from src.core.schemas import Creative, FormatId


def test_format_id_object_structure():
    """Test that FormatId accepts AdCP v2.4 structure."""
    format_id = FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250")
    assert (
        str(format_id.agent_url).rstrip("/") == "https://creative.adcontextprotocol.org"
    )  # AnyUrl adds trailing slash
    assert format_id.id == "display_300x250"


def test_format_id_validation():
    """Test FormatId validation."""
    # Valid format
    FormatId(agent_url="https://example.com", id="valid_format-123")

    # Invalid id pattern
    with pytest.raises(ValueError):
        FormatId(agent_url="https://example.com", id="invalid format!")


def test_creative_upgrades_string_format():
    """Test Creative automatically upgrades legacy string format_id."""
    creative = Creative(
        creative_id="c1",
        name="Test Creative",
        format_id="display_300x250",  # Legacy string - auto-upgraded
        assets={
            "banner_image": {
                "url": "https://example.com/creative.jpg",
                "width": 300,
                "height": 250,
                "asset_type": "image",
            }
        },
        principal_id="p1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    # Should be automatically upgraded to FormatId object
    assert isinstance(creative.format, FormatId)
    assert isinstance(creative.format_id, FormatId), "format_id is now a FormatId object (library pattern)"
    assert creative.format_id.id == "display_300x250", "Access string ID via format_id.id"
    assert (
        str(creative.format_agent_url).rstrip("/") == "https://creative.adcontextprotocol.org"
    )  # AnyUrl adds trailing slash


def test_creative_accepts_format_id_object():
    """Test Creative accepts FormatId object (AdCP v2.4)."""
    format_id = FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250")
    creative = Creative(
        creative_id="c1",
        name="Test Creative",
        format_id=format_id,
        assets={
            "banner_image": {
                "url": "https://example.com/creative.jpg",
                "width": 300,
                "height": 250,
                "asset_type": "image",
            }
        },
        principal_id="p1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    assert isinstance(creative.format, FormatId)
    assert isinstance(creative.format_id, FormatId), "format_id is now a FormatId object (library pattern)"
    assert creative.format_id.id == "display_300x250", "Access string ID via format_id.id"
    assert (
        str(creative.format_agent_url).rstrip("/") == "https://creative.adcontextprotocol.org"
    )  # AnyUrl adds trailing slash


def test_creative_from_dict_with_format_id_object():
    """Test Creative can be created from dict with format_id as object."""
    data = {
        "creative_id": "c1",
        "name": "Test Creative",
        "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
        "assets": {
            "banner_image": {
                "url": "https://example.com/creative.jpg",
                "width": 300,
                "height": 250,
                "asset_type": "image",
            }
        },
        "principal_id": "p1",
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }
    creative = Creative(**data)
    assert creative.format_id.id == "display_300x250", "Access string ID via format_id.id"
    assert (
        str(creative.format_agent_url).rstrip("/") == "https://creative.adcontextprotocol.org"
    )  # AnyUrl adds trailing slash


def test_creative_upgrades_dict_without_agent_url():
    """Test Creative auto-upgrades format_id dict missing agent_url."""
    creative = Creative(
        creative_id="c1",
        name="Test Creative",
        format_id={"id": "display_300x250"},  # Missing agent_url - auto-upgraded
        assets={
            "banner_image": {
                "url": "https://example.com/creative.jpg",
                "width": 300,
                "height": 250,
                "asset_type": "image",
            }
        },
        principal_id="p1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    # Should be automatically upgraded with default agent_url
    assert isinstance(creative.format, FormatId)
    assert isinstance(creative.format_id, FormatId), "format_id is now a FormatId object (library pattern)"
    assert creative.format_id.id == "display_300x250", "Access string ID via format_id.id"
    assert (
        str(creative.format_agent_url).rstrip("/") == "https://creative.adcontextprotocol.org"
    )  # AnyUrl adds trailing slash


def test_extract_format_namespace():
    """Test _extract_format_namespace helper function.

    Note: This function is used internally and expects already-upgraded FormatId objects.
    For string upgrade, use upgrade_legacy_format_id() first.
    """
    from src.core.helpers.creative_helpers import _extract_format_namespace

    # Dict format with agent_url
    agent_url, format_id = _extract_format_namespace({"agent_url": "https://example.com", "id": "display_300x250"})
    assert str(agent_url).rstrip("/") == "https://example.com"  # AnyUrl adds trailing slash
    assert format_id == "display_300x250"

    # FormatId object
    format_obj = FormatId(agent_url="https://example.com", id="display_300x250")
    agent_url, format_id = _extract_format_namespace(format_obj)
    assert str(agent_url).rstrip("/") == "https://example.com"  # AnyUrl adds trailing slash
    assert format_id == "display_300x250"

    # String format (should be rejected - use upgrade_legacy_format_id first)
    with pytest.raises(ValueError, match="must be an object"):
        _extract_format_namespace("display_300x250")


def test_normalize_format_value():
    """Test _normalize_format_value helper function (legacy compatibility)."""
    from src.core.helpers.creative_helpers import _normalize_format_value

    # Dict format
    assert _normalize_format_value({"agent_url": "https://example.com", "id": "display_300x250"}) == "display_300x250"

    # FormatId object
    format_id = FormatId(agent_url="https://example.com", id="display_300x250")
    assert _normalize_format_value(format_id) == "display_300x250"

    # String format (should be rejected)
    with pytest.raises(ValueError, match="must be an object"):
        _normalize_format_value("display_300x250")
