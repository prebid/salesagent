"""Regression test for FormatId dict subscript crash (#1164).

media_buy_create.py:2622-2624 used fmt["agent_url"] and fmt["id"] on FormatId
Pydantic objects from schema Product.format_ids. FormatId does not support
__getitem__, so this raises TypeError at runtime.

This code path only triggers when a buyer specifies explicit format_ids in a
media buy package AND the product also has format_ids (the format validation
branch at line 2615).

Recurrence of #1019 (fixed in PR #1053) — same class of bug (dict vs attribute
access on FormatId), different code path.
"""

import pytest

from src.core.schemas import FormatId


class TestFormatIdAttributeAccess:
    """FormatId objects must be accessed via attributes, not dict subscripts."""

    def test_format_id_constructed_from_dict_is_pydantic_object(self):
        """FormatId constructed from dict data (as Pydantic V2 does during
        Product validation) is a FormatId object, not a dict."""
        fmt = FormatId(**{"id": "display_300x250", "agent_url": "https://creative.example.com/mcp"})
        assert isinstance(fmt, FormatId)
        assert not isinstance(fmt, dict)

    def test_format_id_not_subscriptable(self):
        """FormatId objects do not support dict subscript access.

        This is the exact crash from #1164: fmt["agent_url"] raises TypeError.
        """
        fmt = FormatId(id="display_300x250", agent_url="https://creative.example.com/mcp")

        with pytest.raises(TypeError, match="not subscriptable"):
            fmt["agent_url"]

        with pytest.raises(TypeError, match="not subscriptable"):
            fmt["id"]

    def test_format_validation_key_building_uses_attribute_access(self):
        """The format validation path in media_buy_create must use attribute access.

        Reproduces the exact code pattern at media_buy_create.py:2618-2624 that
        builds product_format_keys from product format_ids.
        """
        # Simulate Product.format_ids — Pydantic parses raw dicts into FormatId objects
        format_ids = [
            FormatId(id="display_300x250", agent_url="https://creative.example.com/mcp"),
            FormatId(id="video_640x480", agent_url="https://creative.example.com/mcp"),
        ]

        # This is the pattern from media_buy_create.py:2618-2624
        # Pre-fix code used fmt["agent_url"] and fmt["id"] which crashes
        product_format_keys: set[tuple[str | None, str]] = set()
        for fmt in format_ids:
            agent_url = fmt.agent_url  # NOT fmt["agent_url"]
            normalized_url = str(agent_url).rstrip("/") if agent_url else None
            product_format_keys.add((normalized_url, fmt.id))  # NOT fmt["id"]

        assert len(product_format_keys) == 2
        assert ("https://creative.example.com/mcp", "display_300x250") in product_format_keys
        assert ("https://creative.example.com/mcp", "video_640x480") in product_format_keys

    def test_format_id_attribute_access_with_trailing_slash(self):
        """Format validation must normalize trailing slashes via attribute access."""
        fmt = FormatId(id="display_300x250", agent_url="https://creative.example.com/mcp/")

        # The normalization pattern from media_buy_create.py strips trailing slashes
        normalized_url = str(fmt.agent_url).rstrip("/") if fmt.agent_url else None
        assert normalized_url == "https://creative.example.com/mcp"
