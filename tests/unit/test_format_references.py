"""Tests for typed FormatId boundary parsing helpers."""

import pytest

from src.core.format_references import format_id_from_ref, format_id_identity, format_id_storage_dict
from src.core.schemas import FormatId


def test_format_id_from_ref_accepts_flat_form_shape():
    format_id = format_id_from_ref(
        {
            "agent_url": "https://creative.adcontextprotocol.org",
            "id": "display_image",
            "width": "300",
            "height": "250",
        }
    )

    assert isinstance(format_id, FormatId)
    assert format_id.id == "display_image"
    assert format_id.width == 300
    assert format_id.height == 250


def test_format_id_from_ref_accepts_nested_format_id_shape():
    format_id = format_id_from_ref(
        {
            "format_id": {
                "agent_url": "https://creative.adcontextprotocol.org",
                "id": "display_image",
            },
            "width": 300,
            "height": 250,
        }
    )

    assert format_id_storage_dict(format_id) == {
        "agent_url": "https://creative.adcontextprotocol.org",
        "id": "display_image",
        "width": 300,
        "height": 250,
    }


def test_format_id_from_ref_accepts_legacy_flat_format_id_key_without_storing_extra_key():
    format_id = format_id_from_ref(
        {
            "agent_url": "https://creative.adcontextprotocol.org",
            "format_id": "video_standard",
            "duration_ms": "15000",
        }
    )

    assert format_id_storage_dict(format_id) == {
        "agent_url": "https://creative.adcontextprotocol.org",
        "id": "video_standard",
        "duration_ms": 15000.0,
    }


def test_format_id_storage_dict_canonicalizes_reference_agent_aliases():
    format_id = format_id_from_ref(
        {
            "agent_url": "https://adcontextprotocol.org/agents/formats/mcp",
            "id": "display_image",
        }
    )

    assert format_id_storage_dict(format_id) == {
        "agent_url": "https://creative.adcontextprotocol.org",
        "id": "display_image",
    }


def test_format_id_identity_normalizes_reference_agent_aliases():
    left = format_id_from_ref({"agent_url": "https://adcontextprotocol.org/agents/formats/mcp", "id": "display_image"})
    right = format_id_from_ref({"agent_url": "https://creative.adcontextprotocol.org/", "id": "display_image"})

    assert format_id_identity(left) == format_id_identity(right)


def test_format_id_from_ref_rejects_malformed_data():
    with pytest.raises(ValueError, match="Invalid FormatId reference"):
        format_id_from_ref({"id": "display_image"})
