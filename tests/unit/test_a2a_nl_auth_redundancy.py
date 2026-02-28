"""Test that A2A NL dispatch does not re-run auth for logging.

Bug salesagent-anjp: Each NL dispatch branch calls _create_tool_context_from_a2a()
twice — once inside the handler (for actual identity resolution) and once outside
(just for tenant_id/principal_id logging). The second call triggers redundant DB
queries via resolve_identity(). Fix: reuse the identity from the handler call.

This test verifies resolve_identity() is called at most once per NL request.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.resolved_identity import ResolvedIdentity
from tests.a2a_helpers import make_a2a_context

_MOCK_IDENTITY = ResolvedIdentity(
    principal_id="test-principal",
    tenant_id="test-tenant",
    tenant={"tenant_id": "test-tenant"},
    protocol="a2a",
)


def _make_nl_message(text: str):
    """Build a minimal A2A MessageSendParams with NL text (no skills)."""
    from a2a.types import Message, MessageSendParams, Part, TextPart

    message = Message(
        messageId=str(uuid.uuid4()),
        role="user",
        parts=[Part(root=TextPart(text=text))],
    )
    return MessageSendParams(message=message)


@pytest.mark.asyncio
async def test_nl_product_query_calls_resolve_identity_once():
    """NL product query should call resolve_identity at most once, not twice.

    Current bug: _get_products() calls _create_tool_context_from_a2a() internally
    (which calls resolve_identity), then the NL dispatch code calls it AGAIN just
    for logging. This test will FAIL until the redundant logging call is removed.
    """
    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value="test-token")
    ctx = make_a2a_context(auth_token="test-token", headers={"host": "test.example.com"})

    params = _make_nl_message("Show me available products in the catalog")

    with patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY) as mock_resolve:
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_products:
            mock_products.return_value = {"products": [], "message": "No products found"}

            await handler.on_message_send(params, context=ctx)

    # BUG: Currently called 2x — once in _get_products():2046 and once for logging:649
    # FIX target: should be called exactly 1x
    assert mock_resolve.call_count == 1, (
        f"resolve_identity called {mock_resolve.call_count} times for a single NL request. "
        f"Expected 1 (handler call only), but logging code re-runs auth redundantly."
    )


@pytest.mark.asyncio
async def test_nl_pricing_query_calls_resolve_identity_once():
    """NL pricing query should call resolve_identity at most once.

    Pricing NL path (line 674) → _handle_get_products_skill (line 1398 calls
    _create_tool_context_from_a2a) → then line 682 calls it AGAIN for logging.
    """
    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value="test-token")
    ctx = make_a2a_context(auth_token="test-token", headers={"host": "test.example.com"})

    params = _make_nl_message("What is the pricing for CPM ads?")

    with patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY) as mock_resolve:
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_products:
            # Return a dict to bypass model_dump() path
            mock_products.return_value = {"products": [], "message": "No products found"}

            await handler.on_message_send(params, context=ctx)

    assert mock_resolve.call_count == 1, (
        f"resolve_identity called {mock_resolve.call_count} times for pricing NL request. Expected 1."
    )


@pytest.mark.asyncio
async def test_nl_targeting_query_calls_resolve_identity_once():
    """NL targeting query should call resolve_identity at most once.

    Targeting NL path (line 708) → _handle_get_adcp_capabilities_skill (line 1769
    calls _create_tool_context_from_a2a) → then line 713 calls it AGAIN for logging.
    """
    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value="test-token")
    ctx = make_a2a_context(auth_token="test-token", headers={"host": "test.example.com"})

    params = _make_nl_message("Show me audience targeting options")

    with patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY) as mock_resolve:
        # Mock the core capabilities function (not the handler — to expose both calls)
        with patch("src.core.tools.capabilities.get_adcp_capabilities_raw") as mock_caps:
            mock_caps.return_value = {"protocols": [], "targeting": {}}

            await handler.on_message_send(params, context=ctx)

    assert mock_resolve.call_count == 1, (
        f"resolve_identity called {mock_resolve.call_count} times for targeting NL request. Expected 1."
    )


@pytest.mark.asyncio
async def test_nl_media_buy_query_calls_resolve_identity_once():
    """NL media buy query should call resolve_identity at most once.

    Media buy NL path (line 738) → _create_media_buy (line 2126 calls
    _create_tool_context_from_a2a) → then line 742 calls it AGAIN for logging.
    """
    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value="test-token")
    ctx = make_a2a_context(auth_token="test-token", headers={"host": "test.example.com"})

    params = _make_nl_message("Create a campaign for Nike")

    with patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY) as mock_resolve:
        # Let _create_media_buy run naturally — it calls _create_tool_context_from_a2a
        # internally, then the logging code calls it again
        await handler.on_message_send(params, context=ctx)

    assert mock_resolve.call_count == 1, (
        f"resolve_identity called {mock_resolve.call_count} times for media buy NL request. Expected 1."
    )
