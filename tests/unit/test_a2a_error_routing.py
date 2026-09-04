"""A2A error routing: a Task-execution failure rides in a failed Task; only a
transport fault with no Task uses JSON-RPC.

Pinned AdCP 3.1.1, ``a2a-response-format.mdx`` "Where the Error Lives: Decision
Rule": when a Task exists and structured error data is in hand — a typed
``AdCPError`` OR an untyped system crash alike — the failure is RETURNED as
``status: failed`` carrying the two-layer AdCP envelope in a DataPart. JSON-RPC
is reserved for failures where no Task artifact was produced (parse/auth-handshake/
malformed request — the re-raised ``A2AError``). An untyped crash normalizes to
base ``AdCPError`` → wire ``SERVICE_UNAVAILABLE``, the same as MCP/REST/explicit-skill.

Grading: ungraded — A2A transport mechanic, unit-graded here. No conformance
storyboard under ``dist/compliance/3.1.1/`` exercises A2A failed-Task routing.

These tests pin both sides: typed and untyped Task-execution failures return a
failed Task envelope; only a genuine ``A2AError`` transport fault raises to JSON-RPC.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import (
    InvalidRequestError,
    SendMessageConfiguration,
    SendMessageRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
)

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.exceptions import AdCPValidationError
from tests.a2a_helpers import (
    extract_processing_error_envelope,
    make_a2a_context,
    make_a2a_handler,
    make_mock_a2a_identity,
    make_nl_send_message_request,
)
from tests.helpers import assert_envelope_shape
from tests.utils.a2a_helpers import create_a2a_message_with_skill

_MOCK_IDENTITY = make_mock_a2a_identity()


def _make_nl_request_with_push(text: str, url: str) -> SendMessageRequest:
    """NL request carrying a protocol-level push-notification config."""
    request = make_nl_send_message_request(text)
    request.configuration.CopyFrom(
        SendMessageConfiguration(task_push_notification_config=TaskPushNotificationConfig(url=url))
    )
    return request


@pytest.mark.asyncio
async def test_untyped_processing_failure_returns_service_unavailable_failed_task():
    """An untyped crash returns a failed Task envelope, not a JSON-RPC error.

    Pinned AdCP 3.1.1 a2a-response-format.mdx "Where the Error Lives: Decision
    Rule": a system error where a Task exists is RETURNED as ``status: failed``
    carrying the adcp_error DataPart — the same path MCP/REST/explicit-skill take
    via ``normalize_to_adcp_error`` (base AdCPError → wire SERVICE_UNAVAILABLE).
    The inline terminal failure drops the accepted push config and emits no
    webhook. (Scrubbing the untyped error message off the wire is a shared-seam
    concern tracked separately, not asserted here.)
    """
    handler, ctx = make_a2a_handler()
    params = _make_nl_request_with_push(
        "Show me available products in the catalog", "https://buyer.example.com/webhook"
    )
    handler._send_protocol_webhook = AsyncMock()

    with (
        patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY),
        patch("src.a2a_server.adcp_a2a_server._accept_a2a_push_config", return_value=MagicMock()),
        patch(
            "src.a2a_server.adcp_a2a_server.core_get_products_tool",
            side_effect=RuntimeError("adapter exploded: secret-canary"),
        ),
    ):
        result = await handler.on_message_send(params, context=ctx)

    assert isinstance(result, Task), f"expected a returned Task, got {type(result).__name__}"
    assert result.status.state == TaskState.TASK_STATE_FAILED, (
        f"expected TASK_STATE_FAILED, got {result.status.state!r}"
    )
    assert_envelope_shape(
        extract_processing_error_envelope(result),
        "SERVICE_UNAVAILABLE",
        recovery="transient",
    )
    # Inline terminal failure: the accepted push config is dropped, no webhook fires.
    assert not handler._task_push_configs, "terminal failure must not retain the push config"
    handler._send_protocol_webhook.assert_not_awaited()


@pytest.mark.asyncio
async def test_typed_adcp_error_keeps_its_own_wire_code_on_failed_task():
    """A typed AdCPError escaping to the outer handler keeps its own wire code.

    The envelope must carry the AdCPError's code (here ``VALIDATION_ERROR``),
    not a blanket ``INTERNAL_ERROR`` — ``_build_error_envelope`` passes typed
    errors through ``normalize_to_adcp_error`` unchanged.
    """
    handler, ctx = make_a2a_handler()
    params = make_nl_send_message_request("Show me available products in the catalog")

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
    # Failed-task artifact pairs a human-readable TextPart with the adcp_error DataPart
    # (a2a-response-format.mdx "Required Structure": DataPart required, TextPart recommended).
    parts = result.artifacts[0].parts
    assert any(p.HasField("text") and "brief must not be empty" in p.text for p in parts), (
        "failed-task artifact must carry a TextPart with the error message"
    )
    assert any(p.HasField("data") for p in parts), "failed-task artifact must carry the DataPart"


@pytest.mark.asyncio
async def test_no_webhook_on_inline_typed_failure():
    """A typed failure returns a terminal failed Task inline — no push webhook.

    Pinned AdCP 3.1.1 ``webhooks.mdx:160`` (MUST NOT) + ``a2a-guide:484``: an
    inline terminal response emits no push notification, even when the caller
    registered an accepted push config. The failed Task carries the envelope
    synchronously, so a webhook would be a duplicate delivery of the same
    terminal state.
    """
    handler, ctx = make_a2a_handler()
    params = _make_nl_request_with_push(
        "Show me available products in the catalog", "https://buyer.example.com/webhook"
    )
    handler._send_protocol_webhook = AsyncMock()

    with (
        patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY),
        patch("src.a2a_server.adcp_a2a_server._accept_a2a_push_config", return_value=MagicMock()) as mock_accept,
        patch(
            "src.a2a_server.adcp_a2a_server.core_get_products_tool",
            side_effect=AdCPValidationError("brief must not be empty"),
        ),
    ):
        result = await handler.on_message_send(params, context=ctx)

    assert isinstance(result, Task), f"expected a returned Task, got {type(result).__name__}"
    assert result.status.state == TaskState.TASK_STATE_FAILED, (
        f"expected TASK_STATE_FAILED, got {result.status.state!r}"
    )
    # An accepted push config WAS registered for this task (the buyer's URL) ...
    mock_accept.assert_called_once_with("https://buyer.example.com/webhook", None, None)
    # ... but the inline terminal failure drops it — no orphaned URL/credentials.
    assert not handler._task_push_configs, "terminal typed failure must not retain the push config"
    # ... and no webhook is emitted for the inline terminal failed Task.
    handler._send_protocol_webhook.assert_not_awaited()


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
    # The provisional Task is registered before the auth gate raises; the A2AError branch
    # must pop it so a transport-rejected request leaves no orphaned lifecycle Task
    # (buyer-observable via tasks/get). Removing that pop reddens this assertion.
    assert not handler.tasks, "A2AError transport fault must not leave an orphaned Task"


@pytest.mark.asyncio
async def test_send_protocol_webhook_skips_inline_terminal_state():
    """A terminal task is the inline response — the sender must not deliver a webhook.

    Pinned AdCP 3.1.1 webhooks.mdx:160 (MUST NOT). The guard returns before the service
    is even resolved, so an inline completed/failed/rejected/canceled task never notifies.
    """
    handler = AdCPRequestHandler()
    task = Task(id="task_term", status=TaskStatus(state=TaskState.TASK_STATE_FAILED))
    handler._task_push_configs[task.id] = MagicMock(url="https://buyer.example.com/webhook")

    with patch("src.a2a_server.adcp_a2a_server.get_protocol_webhook_service") as mock_get_service:
        await handler._send_protocol_webhook(task, status="failed")

    mock_get_service.assert_not_called()


@pytest.mark.asyncio
async def test_send_protocol_webhook_delivers_for_non_terminal_state():
    """A non-terminal (submitted) status is a legitimate notification and is delivered."""
    handler = AdCPRequestHandler()
    task = Task(id="task_sub", status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED))
    handler._task_push_configs[task.id] = MagicMock(url="https://buyer.example.com/webhook")
    notify = AsyncMock(return_value=True)

    with patch(
        "src.a2a_server.adcp_a2a_server.get_protocol_webhook_service",
        return_value=MagicMock(notify=notify),
    ):
        await handler._send_protocol_webhook(task, status="submitted")

    notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_webhook_on_inline_explicit_skill_failure():
    """The explicit-skill all-failed path returns a terminal Task inline — no webhook.

    Converges with the NL-outer failure path: neither notifies on the inline terminal
    response (KM :1050 — outcome must not depend on NL vs explicit-skill request shape).
    """
    handler, ctx = make_a2a_handler()
    message = create_a2a_message_with_skill("get_products", {"brief": "test"})
    params = _make_nl_request_with_push("unused", "https://buyer.example.com/webhook")
    params.message.CopyFrom(message)
    notify = AsyncMock(return_value=True)

    with (
        patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY),
        patch("src.a2a_server.adcp_a2a_server._accept_a2a_push_config", return_value=MagicMock()),
        patch(
            "src.a2a_server.adcp_a2a_server.core_get_products_tool",
            side_effect=RuntimeError("adapter exploded"),
        ),
        patch(
            "src.a2a_server.adcp_a2a_server.get_protocol_webhook_service",
            return_value=MagicMock(notify=notify),
        ),
    ):
        result = await handler.on_message_send(params, context=ctx)

    assert result.status.state == TaskState.TASK_STATE_FAILED
    notify.assert_not_awaited()
