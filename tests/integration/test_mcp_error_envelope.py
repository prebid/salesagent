#!/usr/bin/env python3
"""Wire-level MCP error envelope tests.

Companion to tests/integration/test_a2a_error_responses.py — verifies that
typed AdCPError raised inside an MCP-routed _impl surfaces on the wire as a
spec two-layer envelope (``adcp_error`` + ``errors[]``) inside the
FastMCP CallToolResult content text. The MCP boundary translator
(src/core/tool_error_logging.py:_translate_to_tool_error) builds the envelope
via build_two_layer_error_envelope and wraps it in an AdCPToolError whose
``str(self)`` is the JSON-encoded envelope.

Exercises the full FastMCP pipeline end-to-end:
Client(mcp) → middleware → TypeAdapter → tool → _impl → typed raise →
boundary translator → wire envelope. Mock-only equivalents do not prove
this wiring.

Production-validator tests (BUDGET_TOO_LOW, INVALID_REQUEST past start_time)
drive REAL invalid input through the full pipeline — no ``_impl`` patching.
This exercises the actual production validators
(src/core/tools/media_buy_create.py:1756 budget, :1791 start_time) and the
AdCPValidationError raised directly.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastmcp import Client

from src.core.main import mcp
from tests.factories.principal import PrincipalFactory
from tests.helpers import assert_envelope_shape
from tests.helpers.adcp_factories import create_test_package_request_dict
from tests.integration.conftest import seed_error_test_tenant

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


_TENANT_ID = "mcp_envelope_test"
_PRINCIPAL_ID = "mcp_envelope_principal"
_ACCESS_TOKEN = "mcp_envelope_token_456"
_PRODUCT_ID = "mcp_envelope_product"


@pytest.fixture
def mcp_real_tenant_setup(integration_db):
    """Real-DB ResolvedIdentity for end-to-end MCP wire tests against production validators."""
    from tests.harness._base import IntegrationEnv

    with IntegrationEnv():
        yield seed_error_test_tenant(
            tenant_id=_TENANT_ID,
            principal_id=_PRINCIPAL_ID,
            access_token=_ACCESS_TOKEN,
            product_id=_PRODUCT_ID,
            subdomain="mcpenv",
            tenant_name="MCP Envelope Test Tenant",
            advertiser_id="mock_adv_456",
        )["identity"]


@pytest.mark.integration
@pytest.mark.requires_db
class TestMcpWireErrorEnvelope:
    """MCP-routed _impl raises typed AdCPError → spec two-layer envelope on wire."""

    def test_update_media_buy_not_found_emits_two_layer_envelope_on_wire(self, integration_db):
        """AdCPMediaBuyNotFoundError from _impl surfaces as a two-layer envelope on the MCP wire.

        Flow exercised end-to-end:
            Client(mcp).call_tool("update_media_buy", {"media_buy_id": "nonexistent", "paused": True})
              → FastMCP middleware chain
              → TypeAdapter validates args
              → update_media_buy MCP wrapper (src/core/tools/media_buy_update.py)
              → _update_media_buy_impl → MediaBuyRepository.get_by_id returns None
              → raise AdCPMediaBuyNotFoundError("Media buy 'nonexistent' not found.")
              → with_error_logging wrapper catches it
              → _translate_to_tool_error builds envelope via build_two_layer_error_envelope
              → raises AdCPToolError(envelope, status_code=404)
              → FastMCP serializes str(error) = JSON envelope into CallToolResult.content[0].text

        This is the wire-level shape. The harness's _unwrap_mcp_tool_error would
        normally parse this and reconstruct an AdCPError — we bypass it here to
        inspect the wire bytes directly.

        MEDIA_BUY_NOT_FOUND is a STANDARD_ERROR_CODES entry — it passes through
        the boundary translator unchanged (no ERROR_CODE_MAPPING rewrite).
        """
        identity = PrincipalFactory.make_identity(protocol="mcp")

        is_error, envelope = self._call_mcp_tool_capturing_envelope(
            "update_media_buy",
            {
                "media_buy_id": "mb_does_not_exist_mcp_wire",
                "paused": True,  # need ≥1 updatable field to pass pre-lookup validation
            },
            identity,
        )

        assert is_error, "Nonexistent media_buy_id must produce a tool error"
        assert envelope is not None, "Error must include content text carrying the envelope"

        # MEDIA_BUY_NOT_FOUND is a STANDARD_ERROR_CODES entry — passes through unchanged.
        # AdCPMediaBuyNotFoundError overrides AdCPNotFoundError's terminal default
        # with correctable (the buyer can correct by supplying the right media_buy_id).
        assert_envelope_shape(
            envelope,
            "MEDIA_BUY_NOT_FOUND",
            recovery="correctable",
            message_substr="mb_does_not_exist_mcp_wire",
        )
        # Two-layer invariant: errors[0].message is byte-identical to adcp_error.message.
        single_error_msg = "errors[0].message must be byte-identical to adcp_error.message in single-error case"
        assert envelope["errors"][0]["message"] == envelope["adcp_error"]["message"], single_error_msg

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
              → raise AdCPBudgetTooLowError(...) (line 1758)
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
                "idempotency_key": f"int-key-{uuid.uuid4().hex}",
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
        assert_envelope_shape(envelope, "BUDGET_TOO_LOW", recovery="correctable")

    def test_create_media_buy_validation_error_emits_envelope_on_wire(self, mcp_real_tenant_setup):
        """Production past-start-time validator surfaces INVALID_REQUEST on the wire.

        Drives REAL invalid input (``start_time`` in the past) through the
        full pipeline. Production validator at
        src/core/tools/media_buy_create.py:1791 raises
        ``AdCPValidationError(error_code="INVALID_REQUEST")`` directly; the
        MCP boundary translator builds the wire envelope.

        No ``_impl`` patching — exercises the actual production validator.

        INVALID_REQUEST is a spec STANDARD code (passthrough — not remapped).
        """
        identity = mcp_real_tenant_setup

        is_error, envelope = self._call_mcp_tool_capturing_envelope(
            "create_media_buy",
            {
                "brand": {"domain": "wiretest.example"},
                "idempotency_key": f"int-key-{uuid.uuid4().hex}",
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
        assert_envelope_shape(envelope, "INVALID_REQUEST", recovery="correctable")
        msg_lower = envelope["adcp_error"]["message"].lower()
        past_or_start_msg = f"Envelope message must explain the failure, got: {envelope['adcp_error']['message']}"
        assert "past" in msg_lower or "start" in msg_lower, past_or_start_msg

    def test_get_media_buy_delivery_missing_identity_emits_auth_envelope_on_wire(self, integration_db):
        """Missing identity in get_media_buy_delivery surfaces AUTH_REQUIRED on the MCP wire.

        Flow:
            Client(mcp).call_tool("get_media_buy_delivery", {...}) with identity=None
              → MCP wrapper resolve_identity returns None
              → _get_media_buy_delivery_impl raises AdCPAuthRequiredError("Authentication required...")
              → AdCPAuthRequiredError carries error_code="AUTH_REQUIRED" (passthrough STANDARD code)
              → with_error_logging → _translate_to_tool_error → wire envelope

        AUTH_REQUIRED is a STANDARD spec code — passes through unchanged.
        """
        is_error, envelope = self._call_mcp_tool_capturing_envelope(
            "get_media_buy_delivery",
            {"media_buy_ids": ["any_id"]},
            identity=None,
        )

        assert is_error, "Missing identity must produce a tool error"
        assert envelope is not None, "Error must include content text carrying the envelope"

        # AdCPAuthRequiredError -> AUTH_REQUIRED (AdCP 3.1 spec code, passed through unchanged).
        # Recovery is correctable per the pinned error-code enum (salesagent-xc2j).
        assert_envelope_shape(envelope, "AUTH_REQUIRED", recovery="correctable")
        assert "identity" in envelope["adcp_error"]["message"].lower() or (
            "auth" in envelope["adcp_error"]["message"].lower()
        ), f"Envelope message must mention identity/auth, got: {envelope['adcp_error']['message']}"

    def test_get_media_buys_unsupported_account_filter_emits_envelope_on_wire(self, integration_db):
        """``get_media_buys`` with ``account_id`` surfaces UNSUPPORTED_FEATURE on the MCP wire.

        Flow:
            Client(mcp).call_tool("get_media_buys", {"account_id": "..."})
              → middleware resolves identity (patched)
              → _get_media_buys_impl raises AdCPCapabilityNotSupportedError
              → error_code "UNSUPPORTED_FEATURE" passes through STANDARD_ERROR_CODES
              → boundary translator builds the wire envelope

        Recovery is ``correctable`` per the documented spec divergence (the
        buyer can retry without the unsupported parameter); a generic
        VALIDATION_ERROR would not carry that retry semantic. Pins the
        production raise → wire shape end-to-end.
        """
        from tests.factories import PrincipalFactory

        identity = PrincipalFactory.make_identity(
            tenant_id="any_tenant_unsupported_wire",
            principal_id="any_principal_unsupported_wire",
            protocol="mcp",
        )

        is_error, envelope = self._call_mcp_tool_capturing_envelope(
            "get_media_buys",
            {"account_id": "acc_unsupported_wire_test"},
            identity,
        )

        assert is_error, "Unsupported account_id filter must produce a tool error"
        assert envelope is not None, "Error must include content text carrying the envelope"
        assert_envelope_shape(envelope, "UNSUPPORTED_FEATURE", recovery="correctable")
        account_msg = (
            f"Envelope message must explain the unsupported parameter, got: {envelope['adcp_error']['message']}"
        )
        assert "account" in envelope["adcp_error"]["message"].lower(), account_msg
