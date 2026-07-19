"""Authenticity guard for TransportResult.wire_response.

The UC-005 format_id federation-contract scenario asserts the ``{agent_url, id}``
object shape on ``wire_response`` for REST/A2A/MCP. That is only meaningful if
``wire_response`` carries the *real* serialized bytes rather than a re-serialization
of the already-validated typed payload — otherwise the wire assertions would be
tautological again (the typed payload can never be a bare string by construction).

These tests pin that contract against ``list_creative_formats`` so a future refactor
cannot quietly substitute a reconstruction. MCP provenance is established by
comparison with the actual ``CallToolResult.structured_content``; A2A provenance
uses its transport-only success/message fields. IMPL has no wire by definition.
"""

from __future__ import annotations

import pytest

from tests.harness import CreativeFormatsEnv
from tests.harness.transport import Transport


@pytest.mark.requires_db
class TestWireResponseIsRealWire:
    """wire_response surfaces the real serialized success-path wire, per transport."""

    # A2A-only fields added by _serialize_for_a2a. FastMCP structured_content
    # is the response body itself, so MCP provenance is asserted against the
    # captured CallToolResult rather than optional response-model fields.
    A2A_ENVELOPE_MARKERS = ("success", "message")

    def test_rest_wire_response_is_the_http_body(self, integration_db):
        """REST wire_response is the actual HTTP JSON body (provenance check).

        REST serializes the payload directly, so wire_response == payload.model_dump();
        asserting == raw_response.json() therefore pins *provenance* (the field is the
        real HTTP response body), not a reconstruction-difference. Symmetrically, the
        bare HTTP body must NOT carry the A2A transport-envelope markers.
        """
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.REST)
            assert result.wire_response == result.raw_response.json()
            assert "formats" in result.wire_response
            for marker in self.A2A_ENVELOPE_MARKERS:
                assert marker not in result.wire_response, (
                    f"REST wire (bare HTTP body) unexpectedly carries envelope field {marker!r}"
                )

    def test_a2a_wire_carries_envelope_fields(self, integration_db):
        """A2A wire carries transport-envelope fields a payload reconstruction would lack.

        A payload model_dump() exposes only the response model's fields (formats,
        creative_agents, pagination, ...). The A2A envelope adds success/message,
        so asserting their values distinguishes real serialized wire from a
        reconstruction.
        """
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.A2A)
            assert isinstance(result.wire_response, dict), "A2A wire_response not a dict"
            assert "formats" in result.wire_response, "A2A wire_response missing formats"
            assert result.wire_response["success"] is True
            assert result.wire_response["message"]

    def test_mcp_wire_response_is_call_tool_structured_content(self, integration_db):
        """MCP wire_response comes from the actual in-memory CallToolResult."""
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.MCP)
            assert result.raw_response is not None, "MCP dispatch did not expose its CallToolResult"
            assert result.wire_response == result.raw_response.structured_content
            assert "formats" in result.wire_response

    def test_impl_has_no_wire(self, integration_db):
        """IMPL is an in-process call — no wire by definition."""
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.IMPL)
            assert result.wire_response is None
