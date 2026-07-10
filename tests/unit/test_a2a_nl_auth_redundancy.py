"""Test that A2A NL dispatch does not re-run auth for logging.

Bug salesagent-anjp: Each NL dispatch branch calls _create_tool_context_from_a2a()
twice — once inside the handler (for actual identity resolution) and once outside
(just for tenant_id/principal_id logging). The second call triggers redundant DB
queries via resolve_identity(). Fix: reuse the identity from the handler call.

This test verifies resolve_identity() is called at most once per NL request.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from tests.a2a_helpers import make_a2a_context
from tests.utils.a2a_helpers import (
    extract_processing_error_envelope,
    make_mock_a2a_identity,
    make_nl_send_message_request,
)

_MOCK_IDENTITY = make_mock_a2a_identity()
_make_nl_message = make_nl_send_message_request


@pytest.mark.asyncio
async def test_nl_product_query_calls_resolve_identity_once():
    """NL product query should call resolve_identity at most once, not twice.

    Pre-fix bug: _get_products() called _create_tool_context_from_a2a()
    internally (which calls resolve_identity), then the NL dispatch code
    called it AGAIN for logging. Fix removed the redundant logging call.
    """
    from src.core.schemas import GetProductsResponse

    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value="test-token")
    ctx = make_a2a_context(auth_token="test-token", headers={"host": "test.example.com"})

    params = _make_nl_message("Show me available products in the catalog")

    with patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY) as mock_resolve:
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_products:
            # core_get_products_tool returns a Pydantic GetProductsResponse;
            # NL _get_products iterates response.products so the mock must
            # return a real model (not a raw dict).
            mock_products.return_value = GetProductsResponse(products=[])

            await handler.on_message_send(params, context=ctx)

    assert mock_resolve.call_count == 1, (
        f"resolve_identity called {mock_resolve.call_count} times for a single NL request. "
        f"Expected 1 (handler call only); regression means the logging code re-runs auth."
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
async def test_nl_media_buy_returns_failed_task_with_envelope():
    """NL media buy is not supported — invocation must surface a typed AdCPError.

    Pre-fix the NL stub returned ``{"success": False, "message": "use explicit"}``
    which bypassed the two-layer-envelope contract — storyboard runners
    parsed the artifact as ``MCP_ERROR``. The handler now raises
    ``AdCPCapabilityNotSupportedError`` which propagates to the outer
    ``on_message_send`` error handler that attaches a spec-compliant
    envelope to the failed Task artifact and RETURNS the failed Task
    (per AdCP 3.1.x transport-errors.mdx "Layer Separation" — JSON-RPC
    errors are reserved for transport faults, never application failures).

    Pin: identity resolution still runs once (the route dispatch happens
    before ``_create_media_buy`` raises), and the failed Task must surface
    a wire envelope (not a fake-success dict, not a raised InternalError).
    """
    from a2a.types import TaskState

    from tests.helpers import assert_envelope_shape

    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value="test-token")
    ctx = make_a2a_context(auth_token="test-token", headers={"host": "test.example.com"})

    params = _make_nl_message("Create a campaign for Nike")

    with patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY) as mock_resolve:
        task = await handler.on_message_send(params, context=ctx)

    # Identity is resolved once during route dispatch before the raise.
    assert mock_resolve.call_count == 1, (
        f"resolve_identity called {mock_resolve.call_count} times for media buy NL request. Expected 1."
    )
    assert task.status.state == TaskState.TASK_STATE_FAILED, f"Expected failed Task, got state {task.status.state!r}"
    envelope = extract_processing_error_envelope(task)
    # AdCPCapabilityNotSupportedError → UNSUPPORTED_FEATURE (correctable —
    # matches AdCP error-code.json enumMetadata).
    assert_envelope_shape(
        envelope,
        "UNSUPPORTED_FEATURE",
        recovery="correctable",
        message_substr="create_media_buy",
    )
