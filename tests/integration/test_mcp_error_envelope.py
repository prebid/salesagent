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

Production-validator tests (BUDGET_TOO_LOW, INVALID_REQUEST past start_time)
drive REAL invalid input through the full pipeline — no ``_impl`` patching.
This exercises the actual production validators
(src/core/tools/media_buy_create.py:1756 budget, :1791 start_time) and the
_StructuredValidationError → AdCPValidationError translation at line 2221.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastmcp import Client

from src.core.database.database_session import get_db_session
from src.core.main import mcp
from src.core.resolved_identity import ResolvedIdentity
from tests.factories.principal import PrincipalFactory
from tests.helpers.adcp_factories import create_test_package_request_dict, setup_error_test_tenant_chain

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


_TENANT_ID = "mcp_envelope_test"
_PRINCIPAL_ID = "mcp_envelope_principal"
_ACCESS_TOKEN = "mcp_envelope_token_456"
_PRODUCT_ID = "mcp_envelope_product"


@pytest.fixture
def mcp_real_tenant_setup(integration_db):
    """Create tenant + principal + product so production validators see a real DB context.

    Uses the shared ``setup_error_test_tenant_chain`` helper. Returns a
    fully-bound ResolvedIdentity ready for end-to-end MCP wire tests against
    real validators (BUDGET_TOO_LOW, INVALID_REQUEST past start_time, etc.).
    """
    from src.core.config_loader import set_current_tenant

    with get_db_session() as session:
        result = setup_error_test_tenant_chain(
            session,
            tenant_id=_TENANT_ID,
            principal_id=_PRINCIPAL_ID,
            access_token=_ACCESS_TOKEN,
            product_id=_PRODUCT_ID,
            subdomain="mcpenv",
            tenant_name="MCP Envelope Test Tenant",
            advertiser_id="mock_adv_456",
        )

        set_current_tenant(result["tenant_dict"])

        identity = ResolvedIdentity(
            principal_id=_PRINCIPAL_ID,
            tenant_id=_TENANT_ID,
            tenant=result["tenant_dict"],
            auth_token=_ACCESS_TOKEN,
            protocol="mcp",
        )
        yield identity


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

    def _call_mcp_tool_capturing_envelope(self, tool_name: str, params: dict, identity) -> tuple[bool, dict | None]:
        """Shared helper: invoke an MCP tool and return (is_error, parsed_envelope).

        Single source of truth for the "Client(mcp) → patch identity → call_tool →
        parse content[0].text as JSON envelope" pattern used by every wire test.
        Returns ``None`` envelope when the result lacks content (caller asserts
        that's not the case).
        """

        async def _call() -> tuple[bool, str | None]:
            with patch(
                "src.core.mcp_auth_middleware.resolve_identity_from_context",
                return_value=identity,
            ):
                async with Client(mcp) as client:
                    result = await client.call_tool(tool_name, params, raise_on_error=False)
                    if not result.content:
                        return result.is_error, None
                    text = None
                    for c in result.content:
                        if hasattr(c, "text"):
                            text = c.text
                            break
                    return result.is_error, text

        is_error, envelope_text = asyncio.run(_call())
        if envelope_text is None:
            return is_error, None
        return is_error, json.loads(envelope_text)

    def test_create_media_buy_budget_too_low_emits_envelope_on_wire(self, mcp_real_tenant_setup):
        """Production BUDGET_TOO_LOW validator surfaces as a spec two-layer envelope on the wire.

        Drives REAL invalid input (per-package ``budget=0``) through the full pipeline:
            Client(mcp).call_tool("create_media_buy", real_invalid_payload)
              → middleware resolves identity (patched to use real tenant/principal)
              → create_media_buy MCP wrapper builds CreateMediaBuyRequest
              → _create_media_buy_impl validation: get_total_budget() == 0
              → raise _StructuredValidationError(code="BUDGET_TOO_LOW") (line 1758)
              → except block translates to AdCPValidationError (line 2221)
              → with_error_logging → _translate_to_tool_error → wire envelope

        No ``_impl`` patching — exercises the actual production validator
        and the structured-error→AdCPError translation path.

        BUDGET_TOO_LOW is a spec STANDARD code (passthrough — not remapped).
        """
        identity = mcp_real_tenant_setup
        start_time = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        end_time = (datetime.now(UTC) + timedelta(days=31)).isoformat()

        is_error, envelope = self._call_mcp_tool_capturing_envelope(
            "create_media_buy",
            {
                "brand": {"domain": "wiretest.example"},
                "packages": [
                    create_test_package_request_dict(
                        product_id=_PRODUCT_ID,
                        pricing_option_id="cpm_usd_fixed",
                        budget=0,
                    )
                ],
                "start_time": start_time,
                "end_time": end_time,
            },
            identity,
        )

        assert is_error, "BUDGET_TOO_LOW must produce a tool error"
        assert envelope is not None, "Error must include content text carrying the envelope"
        assert "adcp_error" in envelope, f"Wire envelope must include adcp_error. Got: {sorted(envelope.keys())}"
        assert "errors" in envelope, f"Wire envelope must include errors[]. Got: {sorted(envelope.keys())}"
        assert (
            envelope["adcp_error"]["code"] == "BUDGET_TOO_LOW"
        ), f"Envelope code must be BUDGET_TOO_LOW (STANDARD passthrough), got {envelope['adcp_error'].get('code')}"
        assert (
            envelope["adcp_error"]["recovery"] == "correctable"
        ), f"AdCPValidationError default recovery is correctable, got {envelope['adcp_error'].get('recovery')}"
        # errors[0] mirrors envelope-level adcp_error.
        err = envelope["errors"][0]
        assert err["code"] == "BUDGET_TOO_LOW", f"errors[0].code must match envelope code, got {err.get('code')}"

    def test_create_media_buy_validation_error_emits_envelope_on_wire(self, mcp_real_tenant_setup):
        """Production past-start-time validator surfaces INVALID_REQUEST on the wire.

        Drives REAL invalid input (``start_time`` in the past) through the
        full pipeline. Production validator at
        src/core/tools/media_buy_create.py:1791 raises
        ``_StructuredValidationError(code="INVALID_REQUEST")``; the except
        block at line 2221 translates to ``AdCPValidationError``; the MCP
        boundary translator builds the wire envelope.

        No ``_impl`` patching — exercises the actual production validator.

        INVALID_REQUEST is a spec STANDARD code (passthrough — not remapped).
        """
        identity = mcp_real_tenant_setup

        is_error, envelope = self._call_mcp_tool_capturing_envelope(
            "create_media_buy",
            {
                "brand": {"domain": "wiretest.example"},
                "packages": [
                    create_test_package_request_dict(
                        product_id=_PRODUCT_ID,
                        pricing_option_id="cpm_usd_fixed",
                        budget=5000.0,
                    )
                ],
                "start_time": "2020-01-01T00:00:00Z",  # in the past
                "end_time": "2020-02-01T00:00:00Z",
            },
            identity,
        )

        assert is_error, "INVALID_REQUEST must produce a tool error"
        assert envelope is not None, "Error must include content text carrying the envelope"
        assert (
            envelope["adcp_error"]["code"] == "INVALID_REQUEST"
        ), f"Envelope code must be INVALID_REQUEST for past start_time, got {envelope['adcp_error'].get('code')}"
        assert (
            envelope["adcp_error"]["recovery"] == "correctable"
        ), f"AdCPValidationError default recovery is correctable, got {envelope['adcp_error'].get('recovery')}"
        msg_lower = envelope["adcp_error"]["message"].lower()
        assert (
            "past" in msg_lower or "start" in msg_lower
        ), f"Envelope message must explain the failure, got: {envelope['adcp_error']['message']}"

    def test_get_media_buy_delivery_missing_identity_emits_auth_envelope_on_wire(self, integration_db):
        """Missing identity in get_media_buy_delivery surfaces AUTH_TOKEN_INVALID on the MCP wire.

        Flow:
            Client(mcp).call_tool("get_media_buy_delivery", {...}) with identity=None
              → MCP wrapper resolve_identity returns None
              → _get_media_buy_delivery_impl raises AdCPAuthRequiredError("Identity is required")
              → AdCPAuthRequiredError carries error_code="AUTH_TOKEN_INVALID" (passthrough STANDARD code)
              → with_error_logging → _translate_to_tool_error → wire envelope

        AUTH_TOKEN_INVALID is a STANDARD spec code — passes through unchanged.
        """

        async def _call_no_identity() -> tuple[bool, str | None]:
            # Patch identity resolution to return None (simulates missing/unparseable auth).
            with patch(
                "src.core.mcp_auth_middleware.resolve_identity_from_context",
                return_value=None,
            ):
                async with Client(mcp) as client:
                    result = await client.call_tool(
                        "get_media_buy_delivery",
                        {"media_buy_ids": ["any_id"]},
                        raise_on_error=False,
                    )
                    if not result.content:
                        return result.is_error, None
                    text = None
                    for c in result.content:
                        if hasattr(c, "text"):
                            text = c.text
                            break
                    return result.is_error, text

        is_error, envelope_text = asyncio.run(_call_no_identity())

        assert is_error, "Missing identity must produce a tool error"
        assert envelope_text is not None, "Error must include content text carrying the envelope"

        envelope = json.loads(envelope_text)
        assert "adcp_error" in envelope, f"Wire envelope must include adcp_error. Got: {sorted(envelope.keys())}"

        # AdCPAuthRequiredError -> AUTH_TOKEN_INVALID (spec STANDARD passthrough, not AUTH_REQUIRED).
        assert envelope["adcp_error"]["code"] == "AUTH_TOKEN_INVALID", (
            f"Envelope code must be AUTH_TOKEN_INVALID (passthrough STANDARD code), "
            f"got {envelope['adcp_error'].get('code')}"
        )
        # Recovery is terminal for AdCPAuthenticationError (per adcp 4.3 STANDARD_ERROR_CODES).
        assert (
            envelope["adcp_error"]["recovery"] == "terminal"
        ), f"AdCPAuthRequiredError default recovery is terminal, got {envelope['adcp_error'].get('recovery')}"
        assert "identity" in envelope["adcp_error"]["message"].lower() or (
            "auth" in envelope["adcp_error"]["message"].lower()
        ), f"Envelope message must mention identity/auth, got: {envelope['adcp_error']['message']}"
