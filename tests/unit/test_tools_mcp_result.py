"""Unit tests for src.core.tools._mcp.mcp_result.

Covers the value-level hole the guard tests (AST shape/boundary checks) cannot
see: a call that keeps model_dump() but drops mode="json" (datetime survives
as a Python object instead of an ISO string), or that leaks an unset optional
field as wire null instead of omitting it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from adcp.types.base import AdCPBaseModel

from src.core.tools._mcp import mcp_result


class _SampleResponse(AdCPBaseModel):
    required_field: str
    optional_field: str | None = None
    timestamp: datetime


def test_unset_optional_field_absent_from_structured_content():
    response = _SampleResponse(required_field="ok", timestamp=datetime(2026, 1, 1, tzinfo=UTC))

    result = mcp_result(response)

    assert "optional_field" not in result.structured_content


def test_datetime_serializes_to_iso_string():
    response = _SampleResponse(required_field="ok", timestamp=datetime(2026, 1, 1, tzinfo=UTC))

    result = mcp_result(response)

    assert result.structured_content["timestamp"] == "2026-01-01T00:00:00Z"


def test_content_defaults_to_str_of_response():
    response = _SampleResponse(required_field="ok", timestamp=datetime(2026, 1, 1, tzinfo=UTC))

    result = mcp_result(response)

    assert result.content[0].text == str(response)


def test_content_override_used_verbatim():
    response = _SampleResponse(required_field="ok", timestamp=datetime(2026, 1, 1, tzinfo=UTC))

    result = mcp_result(response, content="human readable summary")

    assert result.content[0].text == "human readable summary"
