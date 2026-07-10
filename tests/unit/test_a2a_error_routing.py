"""A2A error routing: application failures ride in failed Tasks, not JSON-RPC.

Compliance finding F7. Per AdCP 3.1.x transport rules (spec prose:
building/operating/transport-errors.mdx "Layer Separation" and the two-layer
error-handling model), application/task-execution failures must be RETURNED in
the task response body as a failed Task carrying the two-layer AdCP error
envelope artifact. JSON-RPC errors (``A2AError``) are reserved for genuine
transport faults — malformed requests, missing auth, method-not-found.

Pre-fix bug: ``on_message_send``'s outer exception handler built the correct
failed Task with the ``processing_error`` envelope artifact, then threw it
away by raising ``InternalError`` (a JSON-RPC error) instead of returning the
Task. These tests pin the returned-failed-Task contract on the wire artifact.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import InvalidRequestError, SendMessageRequest, Task, TaskState

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.exceptions import AdCPValidationError
from tests.a2a_helpers import make_a2a_context
from tests.helpers import assert_envelope_shape
from tests.utils.a2a_helpers import (
    create_a2a_message_with_skill,
    extract_processing_error_envelope,
    make_mock_a2a_identity,
    make_nl_send_message_request,
)

_MOCK_IDENTITY = make_mock_a2a_identity()
_make_nl_request = make_nl_send_message_request


def _make_handler() -> tuple[AdCPRequestHandler, object]:
    """Handler + authenticated call context for driving on_message_send."""
    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value="test-token")
    ctx = make_a2a_context(auth_token="test-token", headers={"host": "test.example.com"})
    return handler, ctx


@pytest.mark.asyncio
async def test_untyped_processing_failure_returns_failed_task_with_internal_error_envelope():
    """An unexpected exception during message processing returns a failed Task.

    The outer ``on_message_send`` handler must normalize the untyped exception
    to base ``AdCPError`` (internal INTERNAL_ERROR → wire ``SERVICE_UNAVAILABLE``
    via ``ERROR_CODE_MAPPING``), attach the two-layer envelope as the
    ``processing_error`` artifact DataPart, and RETURN the failed Task — never
    raise a JSON-RPC ``InternalError`` for an application failure.
    """
    handler, ctx = _make_handler()
    params = _make_nl_request("Show me available products in the catalog")

    with patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY):
        with patch(
            "src.a2a_server.adcp_a2a_server.core_get_products_tool",
            side_effect=RuntimeError("adapter exploded"),
        ):
            result = await handler.on_message_send(params, context=ctx)

    assert isinstance(result, Task), f"expected a returned Task, got {type(result).__name__}"
    assert result.status.state == TaskState.TASK_STATE_FAILED, (
        f"expected TASK_STATE_FAILED, got {result.status.state!r}"
    )
    envelope = extract_processing_error_envelope(result)
    # Untyped exceptions normalize to base AdCPError (INTERNAL_ERROR / terminal);
    # on the wire the code lands in STANDARD_ERROR_CODES as SERVICE_UNAVAILABLE.
    assert_envelope_shape(
        envelope,
        "SERVICE_UNAVAILABLE",
        recovery="terminal",
        message_substr="adapter exploded",
    )
    # The failed Task is also the stored lifecycle record.
    assert handler.tasks[result.id].status.state == TaskState.TASK_STATE_FAILED


@pytest.mark.asyncio
async def test_typed_adcp_error_keeps_its_own_wire_code_on_failed_task():
    """A typed AdCPError escaping to the outer handler keeps its own wire code.

    The envelope must carry the AdCPError's code (here ``VALIDATION_ERROR``),
    not a blanket ``INTERNAL_ERROR`` — ``_build_error_envelope`` passes typed
    errors through ``normalize_to_adcp_error`` unchanged.
    """
    handler, ctx = _make_handler()
    params = _make_nl_request("Show me available products in the catalog")

    with patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY):
        with patch(
            "src.a2a_server.adcp_a2a_server.core_get_products_tool",
            side_effect=AdCPValidationError("brief must not be empty"),
        ):
            result = await handler.on_message_send(params, context=ctx)

    assert isinstance(result, Task), f"expected a returned Task, got {type(result).__name__}"
    assert result.status.state == TaskState.TASK_STATE_FAILED, (
        f"expected TASK_STATE_FAILED, got {result.status.state!r}"
    )
    assert_envelope_shape(
        extract_processing_error_envelope(result),
        "VALIDATION_ERROR",
        recovery="correctable",
        message_substr="brief must not be empty",
    )


@pytest.mark.asyncio
async def test_auth_extraction_failure_returns_failed_task_before_identity_resolution():
    """Failure before identity resolution still returns the failed Task envelope.

    This pins the ``identity = None`` hoist: auth-token extraction happens before
    identity resolution, so the outer handler must not raise ``NameError`` while
    recording/logging the original failure.
    """
    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(side_effect=RuntimeError("auth context unavailable"))
    handler._send_protocol_webhook = AsyncMock()
    ctx = make_a2a_context(headers={"host": "test.example.com"})
    params = _make_nl_request("Show me available products in the catalog")

    result = await handler.on_message_send(params, context=ctx)

    assert isinstance(result, Task), f"expected a returned Task, got {type(result).__name__}"
    assert result.status.state == TaskState.TASK_STATE_FAILED
    assert_envelope_shape(
        extract_processing_error_envelope(result),
        "SERVICE_UNAVAILABLE",
        recovery="terminal",
        message_substr="auth context unavailable",
    )
    handler._send_protocol_webhook.assert_awaited_once_with(
        result,
        status="failed",
        error="auth context unavailable",
    )


@pytest.mark.asyncio
async def test_all_skills_failed_webhook_carries_joined_reasons():
    """Two failed skills → the failed-Task webhook reason joins both messages.

    The all-skills-failed branch forwards ``"; ".join(error_messages)`` to
    ``_fail_task_with_webhook``. The single-skill integration assertion in
    ``test_a2a_error_responses.py`` is degenerate for the join itself (a join
    of one message equals that message), so this pins the multi-skill case:
    reverting the join to first-message-only fails here.
    """
    handler, ctx = _make_handler()
    handler._send_protocol_webhook = AsyncMock()
    handler._handle_explicit_skill = AsyncMock(
        side_effect=[
            AdCPValidationError("first skill exploded"),
            AdCPValidationError("second skill exploded"),
        ]
    )
    message = create_a2a_message_with_skill("get_products", {})
    message.parts.append(create_a2a_message_with_skill("list_creatives", {}).parts[0])
    params = SendMessageRequest(message=message)

    with patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY):
        result = await handler.on_message_send(params, context=ctx)

    assert isinstance(result, Task), f"expected a returned Task, got {type(result).__name__}"
    assert result.status.state == TaskState.TASK_STATE_FAILED
    handler._send_protocol_webhook.assert_awaited_once_with(
        result,
        status="failed",
        error="first skill exploded; second skill exploded",
    )


@pytest.mark.asyncio
async def test_genuine_transport_fault_still_raises_json_rpc_error():
    """A transport-protocol fault must still surface as a JSON-RPC error.

    Missing authentication for a non-discovery skill is a transport-layer
    fault (the request cannot be routed at all), so ``on_message_send``
    re-raises the ``A2AError`` (here ``InvalidRequestError``) onto the
    JSON-RPC layer instead of returning a failed Task.
    """
    handler = AdCPRequestHandler()
    # No auth token at all — create_media_buy is a non-discovery skill.
    ctx = make_a2a_context(auth_token=None, headers={"host": "test.example.com"})
    message = create_a2a_message_with_skill("create_media_buy", {"product_ids": ["prod_1"]})
    params = SendMessageRequest(message=message)

    with pytest.raises(InvalidRequestError) as exc_info:
        await handler.on_message_send(params, context=ctx)

    assert "authentication" in str(exc_info.value).lower(), (
        f"transport fault should name the missing authentication; got: {exc_info.value}"
    )
