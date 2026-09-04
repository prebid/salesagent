"""Uniform-response assertions for AdCPFormatNotFoundError (exception-object shape).

Covers the eight dimensions ChrisHuie called out for format-miss grading:
internal code / wire code / recovery / suggestion / message equality /
no format_id / no agent_url / no tenant leak. Wire envelope grading stays in
``assert_envelope_shape``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.core.exceptions import REFERENCE_NOT_FOUND_MESSAGE, AdCPFormatNotFoundError
from tests.helpers import pinned_schema


def assert_format_not_found_uniform(
    exc: AdCPFormatNotFoundError,
    *,
    field: str | None = None,
    forbidden_substrings: list[str] | None = None,
) -> None:
    """Assert uniform-response contract on a raised ``AdCPFormatNotFoundError``."""
    assert isinstance(exc, AdCPFormatNotFoundError)
    assert exc.error_code == "FORMAT_NOT_FOUND"
    assert exc.wire_error_code == "REFERENCE_NOT_FOUND"
    assert exc.recovery == "correctable"
    assert str(exc) == REFERENCE_NOT_FOUND_MESSAGE
    assert exc.message == REFERENCE_NOT_FOUND_MESSAGE
    assert exc.details is None
    assert exc.field == field
    expected_suggestion = pinned_schema.error_code_suggestion("REFERENCE_NOT_FOUND")
    assert exc.suggestion == expected_suggestion
    for token in forbidden_substrings or []:
        assert token not in str(exc), f"uniform-response leak: {token!r} in {str(exc)!r}"


async def raise_format_not_found_from_validate_helper(
    *,
    agent_url: str = "https://creative.example.com",
    format_id: str = "nonexistent_format",
    tenant_id: str = "test_tenant",
    package_idx: int = 0,
) -> AdCPFormatNotFoundError:
    """Drive ``_validate_and_convert_format_ids`` to a registered-agent format miss.

    Shared setup for the unit + integration oracles so the mock/registry block
    is not copy-pasted (R0801). Caller still owns the uniform-response asserts.
    """
    from src.core.tools.media_buy_create import _validate_and_convert_format_ids

    mock_agent = MagicMock()
    mock_agent.agent_url = agent_url

    with (
        patch("src.core.creative_agent_registry.CreativeAgentRegistry") as mock_registry_cls,
        patch("src.core.validation.normalize_agent_url", side_effect=lambda x: x),
    ):
        mock_registry = MagicMock()
        mock_registry._get_tenant_agents.return_value = [mock_agent]
        mock_registry.get_format = AsyncMock(return_value=None)
        mock_registry_cls.return_value = mock_registry

        try:
            await _validate_and_convert_format_ids(
                format_ids=[{"agent_url": agent_url, "id": format_id}],
                tenant_id=tenant_id,
                package_idx=package_idx,
            )
        except AdCPFormatNotFoundError as exc:
            return exc
    raise AssertionError("expected AdCPFormatNotFoundError from _validate_and_convert_format_ids")
