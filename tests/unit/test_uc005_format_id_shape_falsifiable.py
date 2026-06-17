"""Falsifiability guard for the format_id federation-contract assertion.

The UC-005 ``format-id-shape`` scenario asserts every serialized ``format_id``
is an object with ``agent_url`` + ``id``. Because production types ``format_id``
as a required structured object, the typed payload can never be a bare string by
construction — so the assertion is only falsifiable against serialized wire bytes.

These tests prove ``assert_wire_format_id_is_object`` actually bites: a flattened
(bare-string) ``format_id`` and an object missing ``agent_url`` must raise, while a
proper object passes. Without this, the scenario could pass by construction.
"""

from __future__ import annotations

import pytest

from tests.helpers.format_assertions import assert_wire_format_id_is_object


def test_bare_string_format_id_is_rejected():
    """A flattened string-serialized format_id (the regression) must fail."""
    with pytest.raises(AssertionError):
        assert_wire_format_id_is_object("display_html")


def test_object_missing_agent_url_is_rejected():
    """An object lacking agent_url breaks the federation contract."""
    with pytest.raises(AssertionError):
        assert_wire_format_id_is_object({"id": "display_html"})


def test_object_format_id_is_accepted():
    """The correct {agent_url, id} object shape passes."""
    assert_wire_format_id_is_object({"agent_url": "https://creative.adcontextprotocol.org/", "id": "display_html"})


def test_object_with_optional_5_7_0_fields_is_accepted():
    """adcp 5.7.0 adds optional width/height/duration_ms — extra keys are valid."""
    assert_wire_format_id_is_object(
        {
            "agent_url": "https://creative.adcontextprotocol.org/",
            "id": "display_300x250",
            "width": 300,
            "height": 250,
        }
    )
