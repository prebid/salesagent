"""Authenticity guard for TransportResult.wire_response.

The UC-005 format_id federation-contract scenario asserts the ``{agent_url, id}``
object shape on ``wire_response`` for REST/A2A/MCP. That is only meaningful if
``wire_response`` carries the *real* serialized bytes rather than a re-serialization
of the already-validated typed payload — otherwise the wire assertions would be
tautological again (the typed payload can never be a bare string by construction).

These tests pin that contract against ``list_creative_formats`` so a future refactor
cannot quietly substitute a reconstruction. IMPL has no wire by definition.
"""

from __future__ import annotations

import pytest

from tests.harness import CreativeFormatsEnv
from tests.harness.transport import Transport


@pytest.mark.requires_db
class TestWireResponseIsRealWire:
    """wire_response surfaces the real serialized success-path wire, per transport."""

    # Envelope-only keys present only because A2A wraps the payload — absent
    # from a bare payload reconstruction and from the REST HTTP body. MCP has
    # no equivalent: ToolResult(structured_content=response) hands FastMCP the
    # SAME response model REST serializes, with no extra transport wrapping —
    # task_id/adcp_version aren't MCP envelope additions, they're ordinary
    # optional response fields that happen to be unset for list_creative_formats
    # (confirmed empirically: post-fix, MCP's wire key-set is byte-identical to
    # REST's for this response). They only looked like MCP-only markers while
    # the null-exclusion bug leaked them onto the wire as null — see the
    # NestedModelSerializerMixin fix. See test_mcp_wire_matches_rest_field_shape
    # below for MCP's actual authenticity check.
    ENVELOPE_MARKERS = {
        Transport.A2A: ("success", "message"),
    }

    def test_rest_wire_response_is_the_http_body(self, integration_db):
        """REST wire_response is the actual HTTP JSON body (provenance check).

        REST serializes the payload directly, so wire_response == payload.model_dump();
        asserting == raw_response.json() therefore pins *provenance* (the field is the
        real HTTP response body), not a reconstruction-difference. Symmetrically, the
        bare HTTP body must NOT carry the A2A/MCP transport-envelope markers.
        """
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.REST)
            assert result.wire_response == result.raw_response.json()
            assert "formats" in result.wire_response
            for marker in (m for markers in self.ENVELOPE_MARKERS.values() for m in markers):
                assert marker not in result.wire_response, (
                    f"REST wire (bare HTTP body) unexpectedly carries envelope field {marker!r}"
                )

    def test_a2a_wire_carries_envelope_fields(self, integration_db):
        """A2A wire carries transport-envelope fields a payload reconstruction would lack.

        A payload model_dump() exposes only the response model's fields (formats,
        creative_agents, pagination, ...). The A2A envelope separately adds
        success/message. Asserting these makes the oracle distinguish real
        serialized wire from a reconstruction.
        """
        with CreativeFormatsEnv() as env:
            for transport, markers in self.ENVELOPE_MARKERS.items():
                result = env.call_via(transport)
                assert isinstance(result.wire_response, dict), f"{transport}: wire_response not a dict"
                assert "formats" in result.wire_response, f"{transport}: wire_response missing formats"
                for key in markers:
                    assert key in result.wire_response, (
                        f"{transport}: wire_response missing envelope field {key!r} — "
                        "looks like a payload reconstruction, not real wire"
                    )

    def test_mcp_wire_matches_rest_field_shape(self, integration_db):
        """MCP wire is a dict carrying the same response-model fields REST does.

        MCP has no distinguishing envelope-only key (see ENVELOPE_MARKERS'
        comment) — its authenticity signal is that FastMCP's structured_content
        genuinely reflects the response model's real field set, not `None`
        (the legacy _run_mcp_wrapper failure mode) and not a hand-reconstructed
        dict with a different key-set than the same model produces on REST.
        """
        with CreativeFormatsEnv() as env:
            mcp_result = env.call_via(Transport.MCP)
            rest_result = env.call_via(Transport.REST)

        assert isinstance(mcp_result.wire_response, dict), "MCP: wire_response not a dict"
        assert "formats" in mcp_result.wire_response, "MCP: wire_response missing formats"
        assert set(mcp_result.wire_response) == set(rest_result.wire_response), (
            "MCP wire key-set diverged from REST's for the same response model — "
            f"MCP: {sorted(mcp_result.wire_response)}, REST: {sorted(rest_result.wire_response)}"
        )

    def test_impl_has_no_wire(self, integration_db):
        """IMPL is an in-process call — no wire by definition."""
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.IMPL)
            assert result.wire_response is None
