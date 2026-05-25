#!/usr/bin/env python3
"""Wire-level MCP error envelope tests.

Companion to tests/integration/test_a2a_error_responses.py — verifies that
typed AdCPError raised inside an MCP-routed _impl surfaces on the wire as a
spec two-layer envelope (``adcp_error`` + ``errors[]``) inside the
FastMCP CallToolResult content text. The MCP boundary translator
(src/core/tool_error_logging.py:_translate_to_tool_error) builds the envelope
via build_two_layer_error_envelope and wraps it in an AdCPToolError whose
``str(self)`` is the JSON-encoded envelope.

Konstantine review (PR #1306, 2026-05-24): mock-only tests do not prove
wiring; this exercises the full FastMCP pipeline end-to-end with
Client(mcp) → middleware → TypeAdapter → tool → _impl → typed raise →
boundary translator → wire envelope.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from fastmcp import Client

from src.core.main import mcp
from tests.factories.principal import PrincipalFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.mark.integration
@pytest.mark.requires_db
class TestMcpWireErrorEnvelope:
    """MCP-routed _impl raises typed AdCPError → spec two-layer envelope on wire."""

    def test_update_media_buy_not_found_emits_two_layer_envelope_on_wire(self, integration_db):
        """AdCPNotFoundError from _impl surfaces as a two-layer envelope on the MCP wire.

        Flow exercised end-to-end:
            Client(mcp).call_tool("update_media_buy", {"media_buy_id": "nonexistent", "paused": True})
              → FastMCP middleware chain
              → TypeAdapter validates args
              → update_media_buy MCP wrapper (src/core/tools/media_buy_update.py)
              → _update_media_buy_impl → MediaBuyRepository.get_by_id returns None
              → raise AdCPNotFoundError("Media buy 'nonexistent' not found.")
              → with_error_logging wrapper catches it
              → _translate_to_tool_error builds envelope via build_two_layer_error_envelope
              → raises AdCPToolError(envelope, status_code=404)
              → FastMCP serializes str(error) = JSON envelope into CallToolResult.content[0].text

        This is the wire-level shape. The harness's _unwrap_mcp_tool_error would
        normally parse this and reconstruct an AdCPError — we bypass it here to
        inspect the wire bytes directly.

        NOT_FOUND is translated to wire code INVALID_REQUEST via ERROR_CODE_MAPPING
        (see src/core/exceptions.py:35 — STANDARD_ERROR_CODES mapping).
        """
        identity = PrincipalFactory.make_identity(protocol="mcp")

        async def _call() -> tuple[bool, str | None]:
            with patch(
                "src.core.mcp_auth_middleware.resolve_identity_from_context",
                return_value=identity,
            ):
                async with Client(mcp) as client:
                    result = await client.call_tool(
                        "update_media_buy",
                        {
                            "media_buy_id": "mb_does_not_exist_pr1306_wire_test",
                            "paused": True,  # need ≥1 updatable field to pass pre-lookup validation
                        },
                        raise_on_error=False,
                    )
                    if not result.content:
                        return result.is_error, None
                    # First content part text is the JSON-encoded envelope.
                    text = None
                    for c in result.content:
                        if hasattr(c, "text"):
                            text = c.text
                            break
                    return result.is_error, text

        is_error, envelope_text = asyncio.run(_call())

        assert is_error, "Nonexistent media_buy_id must produce a tool error"
        assert envelope_text is not None, "Error must include content text carrying the envelope"

        # Parse the wire envelope from the JSON-encoded ToolError content text.
        envelope = json.loads(envelope_text)

        # CRITICAL: spec two-layer envelope shape on the wire.
        assert (
            "adcp_error" in envelope
        ), f"Wire envelope must include top-level adcp_error. Got keys: {sorted(envelope.keys())}"
        assert "errors" in envelope, f"Wire envelope must include errors array. Got keys: {sorted(envelope.keys())}"

        # NOT_FOUND → INVALID_REQUEST via STANDARD_ERROR_CODES wire translation.
        assert envelope["adcp_error"]["code"] == "INVALID_REQUEST", (
            f"Envelope-level code: NOT_FOUND must translate to INVALID_REQUEST on the wire, "
            f"got {envelope['adcp_error'].get('code')}"
        )
        assert (
            envelope["adcp_error"]["recovery"] == "terminal"
        ), f"AdCPNotFoundError default recovery is terminal, got {envelope['adcp_error'].get('recovery')}"
        assert (
            "mb_does_not_exist_pr1306_wire_test" in envelope["adcp_error"]["message"]
        ), f"Envelope message must echo the missing media_buy_id, got: {envelope['adcp_error']['message']}"

        # errors[0] mirrors envelope-level adcp_error (single-error case).
        assert len(envelope["errors"]) > 0, "errors array must be non-empty"
        err = envelope["errors"][0]
        assert err["code"] == "INVALID_REQUEST", f"errors[0].code must match envelope code, got: {err.get('code')}"
        assert (
            err["recovery"] == "terminal"
        ), f"errors[0].recovery must match envelope recovery, got: {err.get('recovery')}"
        assert (
            err["message"] == envelope["adcp_error"]["message"]
        ), "errors[0].message must be byte-identical to adcp_error.message in single-error case"
