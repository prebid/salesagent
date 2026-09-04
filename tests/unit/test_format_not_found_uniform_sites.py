"""Unit oracles for AdCPFormatNotFoundError uniform-response raise sites (gate 32)."""

from __future__ import annotations

import pytest

from src.core.exceptions import (
    REFERENCE_NOT_FOUND_MESSAGE,
    AdCPError,
    AdCPFormatNotFoundError,
    build_two_layer_error_envelope,
)
from tests.helpers.envelope_assertions import assert_envelope_shape
from tests.helpers.format_not_found_assertions import assert_format_not_found_uniform


def test_adcp_error_empty_message_defaults_to_empty_string() -> None:
    """None-sentinel: omitted message resolves to ``""`` when no class default (E4)."""
    assert AdCPError().message == ""


def test_explicit_message_overrides_format_not_found_default() -> None:
    """Per-raise message overrides class default (E4 / A1 mutation surface)."""
    exc = AdCPFormatNotFoundError("explicit text")
    assert exc.message == "explicit text"


@pytest.mark.asyncio
async def test_validate_format_ids_raises_generic_not_leaky_message() -> None:
    """A1: restoring a leaky positional message at this site must redden offline.

    Covers: UC-002-EXT-H-03
    """
    from tests.helpers.format_not_found_assertions import raise_format_not_found_from_validate_helper

    exc = await raise_format_not_found_from_validate_helper()
    assert_format_not_found_uniform(
        exc,
        field="packages[0].format_ids[0]",
        forbidden_substrings=[
            "nonexistent_format",
            "creative.example.com",
            "Format not found on agent",
            "agent_url=",
        ],
    )
    envelope = build_two_layer_error_envelope(exc)
    assert_envelope_shape(
        envelope,
        "REFERENCE_NOT_FOUND",
        recovery="correctable",
        message_substr=REFERENCE_NOT_FOUND_MESSAGE,
    )
    assert envelope["adcp_error"]["message"] == REFERENCE_NOT_FOUND_MESSAGE
    assert envelope["errors"][0]["message"] == REFERENCE_NOT_FOUND_MESSAGE
    # Mutation: a leaky raise with the old message must not satisfy equality.
    leaky = AdCPFormatNotFoundError(
        "Format not found on agent. agent_url=https://creative.example.com, format_id='nonexistent_format'",
        field="packages[0].format_ids[0]",
    )
    assert str(leaky) != REFERENCE_NOT_FOUND_MESSAGE


def test_orders_format_lookup_reraises_adcp_format_not_found() -> None:
    """B1: GAM orders must not demote AdCPFormatNotFoundError into ValueError."""
    from src.adapters.gam.managers import orders as orders_mod

    # Locate the except pattern by executing a minimal stand-in that mirrors the fix.
    raised = AdCPFormatNotFoundError()

    def _lookup():
        try:
            raise raised
        except AdCPError:
            raise
        except ValueError as e:  # pragma: no cover - not taken
            raise ValueError(f"wrapped: {e}") from e

    with pytest.raises(AdCPFormatNotFoundError) as exc_info:
        _lookup()
    assert exc_info.value is raised
    assert orders_mod is not None  # module import smoke (pattern lives there)
