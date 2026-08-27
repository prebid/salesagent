"""Authenticity guard for TransportResult.wire_response.

The UC-005 format_id federation-contract scenario asserts the ``{agent_url, id}``
object shape on ``wire_response`` for REST/A2A/MCP. That is only meaningful if
``wire_response`` carries the *real* serialized bytes rather than a re-serialization
of the already-validated typed payload — otherwise the wire assertions would be
tautological again (the typed payload can never be a bare string by construction).

These tests pin that contract against ``list_creative_formats`` so a future refactor
cannot quietly substitute a reconstruction. IMPL has no wire by definition.

MCP has no envelope-only markers (GH #1710): before that fix, the MCP wrapper
handed ``ToolResult`` the raw pydantic response object, which
FastMCP serializes via ``pydantic_core.to_jsonable_python()`` — bypassing
``AdCPBaseModel``'s ``exclude_none=True`` default, so unset optional fields
(``task_id``, ``adcp_version``) leaked onto the wire as ``null``. Those leaked
nulls were incidentally usable as "this must be real wire, not a reconstruction"
markers. The fix makes the MCP wrapper pass ``response.model_dump(mode="json")``
instead — the *same* call A2A/REST already use — so MCP's ``structured_content``
is now BYTE-IDENTICAL to a plain payload dump: there is no separate MCP envelope
layer, by design (FastMCP's ``structured_content`` IS the tool's typed output).
So the MCP authenticity check below asserts round-trip fidelity (a fabricated or
partial dict wouldn't parse back into the response type and re-dump identically)
rather than envelope-only-key presence.
"""

from __future__ import annotations

import pytest

from src.core.schemas import ListCreativeFormatsResponse
from tests.harness import CreativeFormatsEnv
from tests.harness.transport import Transport


@pytest.mark.requires_db
class TestWireResponseIsRealWire:
    """wire_response surfaces the real serialized success-path wire, per transport."""

    # A2A-only keys, present only because A2A wraps the payload:
    # `_stamp_a2a_protocol_fields` (adcp_a2a_server.py, reached via
    # `_serialize_for_a2a`) explicitly overwrites `message` with
    # `str(response)` and derives `success` from the `errors` field — both are
    # synthesized wrapper keys, always present, and genuinely absent from a
    # bare payload reconstruction and from the REST HTTP body.
    #
    # MCP has NO equivalent wrapper-only marker anymore (GH #1710; see module
    # docstring): MCP's `structured_content` is now built via
    # `response.model_dump(mode="json")` — the identical call REST/A2A use —
    # so MCP and REST wire shapes correctly converge (both honor
    # `exclude_none=True` per AdCP 3.1.1 absent-means-absent). There is no
    # longer a field present in MCP's wire but absent from REST's:
    # `task_id`/`adcp_version` are optional payload fields that only ever
    # "worked" as markers because MCP's old serialization bug preserved them
    # as `null`; `status` is REQUIRED but is a payload field too, so it
    # appears on REST's body identically. See
    # `test_mcp_wire_is_real_wire_not_a_reconstruction` below for MCP's
    # authenticity checks (payload provenance + round-trip fidelity) instead.
    ENVELOPE_MARKERS = {
        Transport.A2A: ("success", "message"),
    }

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
            for marker in (m for markers in self.ENVELOPE_MARKERS.values() for m in markers):
                assert marker not in result.wire_response, (
                    f"REST wire (bare HTTP body) unexpectedly carries envelope field {marker!r}"
                )

    def test_a2a_wire_carries_envelope_fields(self, integration_db):
        """A2A wire carries transport-envelope fields a payload reconstruction would lack.

        A payload model_dump() exposes only the response model's fields (formats,
        creative_agents, pagination, ...). The A2A envelope adds success/message
        (injected by ``_serialize_for_a2a`` via ``_stamp_a2a_protocol_fields``, not
        part of the response model), always present regardless of the payload's own
        (unrelated, optional) ``message`` field — asserting these makes the oracle
        distinguish real serialized wire from a reconstruction.
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

    def test_mcp_wire_is_real_wire_not_a_reconstruction(self, integration_db):
        """MCP wire is this run's payload, byte-faithfully serialized.

        MCP has no envelope-only marker left to assert (GH #1710; see module
        docstring): its ``structured_content`` is now exactly
        ``response.model_dump(mode="json")`` — the same call REST/A2A use — so
        the two authenticity signals that remain are asserted together here:

        1. **Provenance** — ``wire_response`` equals THIS run's typed payload's
           own serialization. A harness bug that captured ``wire_response`` from
           a different source (re-serializing with different kwargs, or stashing
           a stale value from another call) diverges from this.
        2. **Round-trip fidelity** — the wire parses back into
           ``ListCreativeFormatsResponse`` and re-dumps identically. A
           fabricated/partial reconstruction would either fail to construct
           (missing/wrong-typed required fields) or fail to re-dump identically
           (extra/dropped/differently-shaped fields).

        Neither subsumes the other: (1) pins the wire to the run, (2) pins it to
        the declared response schema.
        """
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.MCP)
        assert isinstance(result.wire_response, dict), "MCP: wire_response not a dict"
        assert "formats" in result.wire_response, "MCP: wire_response missing formats"

        # (1) provenance: the wire is this run's payload, not some other source
        assert result.payload is not None, "MCP: no typed payload captured"
        assert result.wire_response == result.payload.model_dump(mode="json"), (
            "MCP wire_response diverged from payload.model_dump(mode='json') — "
            "structured_content may no longer be sourced from the real wire"
        )

        # (2) round-trip fidelity: the wire is a complete, valid instance of the
        # declared response type
        reparsed = ListCreativeFormatsResponse(**result.wire_response)
        assert reparsed.model_dump(mode="json") == result.wire_response, (
            "MCP wire_response does not round-trip through ListCreativeFormatsResponse — "
            "looks like a fabricated/partial reconstruction, not real wire"
        )

    def test_impl_has_no_wire(self, integration_db):
        """IMPL is an in-process call — no wire by definition."""
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.IMPL)
            assert result.wire_response is None
