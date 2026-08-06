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

from tests.helpers.format_assertions import assert_wire_format_id_is_object, wire_format_id_identity


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


class TestWireFormatIdIdentityCanonicalization:
    """wire_format_id_identity must canonicalize agent_url the same way the
    typed-side format_id_identity does — the wire preserves a trailing slash
    the typed value does not, so an uncanonicalized comparison would silently
    defeat any collision falsifier built on it (salesagent-oyiv.14)."""

    def test_strips_trailing_slash(self):
        """The wire's trailing slash must not affect identity equality with a
        typed-side value that never had one."""
        with_slash = wire_format_id_identity({"agent_url": "https://creative.adcontextprotocol.org/", "id": "x"})
        without_slash = wire_format_id_identity({"agent_url": "https://creative.adcontextprotocol.org", "id": "x"})
        assert with_slash == without_slash == ("https://creative.adcontextprotocol.org", "x")

    def test_matches_typed_side_format_id_identity(self):
        """The wire-side identity must agree with the production typed-side
        identity for the same logical format_id, proving both sides of an
        expected-vs-actual comparison meet in the same canonical space."""
        from src.core.schemas import FormatId, format_id_identity

        typed = FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250")
        wire = {"agent_url": "https://creative.adcontextprotocol.org/", "id": "display_300x250"}
        assert wire_format_id_identity(wire) == format_id_identity(typed)
