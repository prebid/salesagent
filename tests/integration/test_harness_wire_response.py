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

    # A2A-only keys: `_serialize_for_a2a` (adcp_a2a_server.py) explicitly
    # overwrites `message` with `str(response)` and sets `success` from the
    # `errors` field — both are synthesized wrapper keys, always present,
    # genuinely absent from a bare payload reconstruction and from the REST
    # HTTP body.
    #
    # MCP has NO equivalent wrapper-only marker post-salesagent-rrz8: MCP's
    # `structured_content` is now built via `response.model_dump(mode="json")`
    # — the identical call REST uses — so MCP and REST wire shapes correctly
    # converge (both honor `exclude_none=True` per AdCP 3.1.1 absent-means-
    # absent). There is no longer a field present in MCP's wire but absent
    # from REST's: `task_id`/`adcp_version` are optional payload fields that
    # only ever "worked" as markers because MCP's old serialization bug
    # preserved them as `null`; `status` is REQUIRED but is a payload field
    # too, so it appears on REST's body identically. See
    # `test_mcp_wire_is_the_serialized_payload` below for MCP's authenticity
    # check instead.
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
        creative_agents, pagination, ...). The A2A envelope adds success/message,
        synthesized by `_serialize_for_a2a` and always present regardless of the
        payload's own (unrelated, optional) `message` field. Asserting these makes
        the oracle distinguish real serialized wire from a reconstruction.
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

    def test_mcp_wire_is_the_serialized_payload(self, integration_db):
        """MCP wire_response equals payload.model_dump(mode="json") (provenance check).

        Post-salesagent-rrz8, MCP's ToolResult.structured_content is built via
        response.model_dump(mode="json") — the same call REST uses — so MCP has
        no wrapper-only envelope key left to distinguish it from a reconstruction
        (unlike A2A's synthesized success/message). Exact equality with the
        payload's own serialization IS the authenticity check here: a harness bug
        that captured wire_response from a different source (e.g. re-serializing
        the typed payload with different kwargs, or stashing a stale value) would
        diverge from this.
        """
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.MCP)
            assert isinstance(result.wire_response, dict), "MCP: wire_response not a dict"
            assert result.payload is not None, "MCP: no typed payload captured"
            assert result.wire_response == result.payload.model_dump(mode="json"), (
                "MCP wire_response diverged from payload.model_dump(mode='json') — "
                "structured_content may no longer be sourced from the real wire"
            )

    def test_impl_has_no_wire(self, integration_db):
        """IMPL is an in-process call — no wire by definition."""
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.IMPL)
            assert result.wire_response is None
