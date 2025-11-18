"""Test format_id parsing in sync_creatives and related operations."""

import pytest

from src.core.helpers import _extract_format_namespace, _normalize_format_value
from src.core.schemas import FormatId


def test_extract_format_namespace_with_dict():
    """Test _extract_format_namespace with dict format from wire."""
    agent_url, format_id = _extract_format_namespace(
        {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}
    )
    assert agent_url == "https://creative.adcontextprotocol.org"
    assert format_id == "display_300x250"


def test_extract_format_namespace_with_format_id_object():
    """Test _extract_format_namespace with FormatId object."""
    format_obj = FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250")
    agent_url, format_id = _extract_format_namespace(format_obj)
    assert str(agent_url).rstrip("/") == "https://creative.adcontextprotocol.org"
    assert format_id == "display_300x250"


def test_extract_format_namespace_rejects_string():
    """Test _extract_format_namespace rejects string format_id."""
    with pytest.raises(ValueError, match="String format_id is no longer supported"):
        _extract_format_namespace("display_300x250")


def test_extract_format_namespace_rejects_incomplete_dict():
    """Test _extract_format_namespace rejects dict without agent_url."""
    with pytest.raises(ValueError, match="must have both 'agent_url' and 'id'"):
        _extract_format_namespace({"id": "display_300x250"})


def test_normalize_format_value_extracts_id():
    """Test _normalize_format_value extracts ID from FormatId object."""
    result = _normalize_format_value({"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"})
    assert result == "display_300x250"


def test_normalize_format_value_rejects_string():
    """Test _normalize_format_value rejects string format_id."""
    with pytest.raises(ValueError, match="String format_id is no longer supported"):
        _normalize_format_value("display_300x250")
