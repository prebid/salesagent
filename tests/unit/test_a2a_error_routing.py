"""A2A error routing: application failures ride in failed Tasks, not JSON-RPC.

Compliance finding F7. Per AdCP 3.1.x transport rules (spec prose:
building/operating/transport-errors.mdx "Layer Separation" and the two-layer
error-handling model), application/task-execution failures must be RETURNED in
the task response body as a failed Task carrying the two-layer AdCP error
envelope artifact. JSON-RPC errors (``A2AError``) are reserved for genuine
transport faults — malformed requests, missing auth, and unknown JSON-RPC
*methods*. Unknown or unimplemented *skills* are application-layer failures
(the ``message/send`` method is valid; routing failed inside skill dispatch),
so they return a failed Task with ``UNSUPPORTED_FEATURE`` — see the dispatch-
registry wire assertions in ``test_a2a_transport_contract.py``.

Pre-fix bug: ``on_message_send``'s outer exception handler built the correct
failed Task with the ``processing_error`` envelope artifact, then threw it
away by raising ``InternalError`` (a JSON-RPC error) instead of returning the
Task. These tests pin the returned-failed-Task contract on the wire artifact.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import (
    Artifact,
    InvalidRequestError,
    Part,
    SendMessageRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
)

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler, _dict_to_value
from src.core.exceptions import AdCPCapabilityNotSupportedError, AdCPValidationError
from tests.a2a_helpers import make_a2a_context
from tests.helpers import assert_envelope_shape
from tests.utils.a2a_helpers import (
    create_a2a_message_with_skill,
    extract_data_from_artifact,
    extract_processing_error_envelope,
    make_nl_send_message_request,
    make_test_a2a_identity,
)

_TEST_IDENTITY = make_test_a2a_identity()


def _first_error_message(artifact: Artifact) -> str:
    """First error message from an artifact's two-layer envelope DataPart."""
    return extract_data_from_artifact(artifact)["errors"][0]["message"]


def _make_handler() -> tuple[AdCPRequestHandler, object]:
    """Handler + authenticated call context for driving on_message_send."""
    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value="test-token")
    ctx = make_a2a_context(auth_token="test-token", headers={"host": "test.example.com"})
    return handler, ctx


@pytest.mark.asyncio
async def test_untyped_processing_failure_returns_failed_task_with_internal_error_envelope():
    """An unexpected exception during message processing returns a failed Task.

    The injected ``RuntimeError`` is an UNTYPED internal crash. The AdCP
    3.1.0-beta.3 transport-errors.mdx "Layer Separation" table classifies an
    "internal crash" under the TRANSPORT layer, so routing it to a failed Task
    (rather than a JSON-RPC error) is the project's deliberate uniform-envelope
    choice — not a mandatory spec rule (production carries this distinction in
    the ``on_message_send`` except-block comment). What this test pins is the
    normalization + wire shape of that deliberate choice: base ``AdCPError``
    (internal INTERNAL_ERROR → wire ``SERVICE_UNAVAILABLE`` via
    ``ERROR_CODE_MAPPING``), the two-layer envelope on the ``processing_error``
    artifact DataPart, and a RETURNED failed Task — never a raised JSON-RPC
    ``InternalError``. (Typed application failures, by contrast, are spec-
    mandated to ride in the task body — see the other tests here.)
    """
    handler, ctx = _make_handler()
    params = make_nl_send_message_request("Show me available products in the catalog")

    with patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY):
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
    # `terminal` is the deliberate base-class divergence from that code's canonical
    # `transient` recovery (a genuine bug won't heal on retry) — see the
    # `_default_recovery` note in src/core/exceptions.py.
    assert_envelope_shape(
        envelope,
        "SERVICE_UNAVAILABLE",
        recovery="terminal",
        message_substr="adapter exploded",
    )
    # The failed Task is also the stored lifecycle record.
    assert handler.tasks[result.id].status.state == TaskState.TASK_STATE_FAILED


@pytest.mark.asyncio
async def test_unknown_skill_records_boundary_error_exactly_once():
    """An unknown skill emits exactly one boundary-observability record.

    Regression for the observability gap: the unknown-skill check used to raise
    BEFORE the logged try, so `record_boundary_error` never fired for it while the
    outer catch assumed the inner boundary had already logged. The check now lives
    inside the logged boundary, so an unknown skill records exactly once — not zero,
    not twice.
    """
    handler = AdCPRequestHandler()
    with patch("src.a2a_server.adcp_a2a_server.record_boundary_error") as mock_record:
        with pytest.raises(AdCPCapabilityNotSupportedError) as exc_info:
            await handler._handle_explicit_skill("nonexistent_skill", {}, _TEST_IDENTITY)
    # Exactly one boundary record, for this skill, carrying the re-raised error.
    mock_record.assert_called_once_with(
        "a2a",
        "nonexistent_skill",
        exc_info.value,
        tenant_id=_TEST_IDENTITY.tenant_id,
        principal_id=_TEST_IDENTITY.principal_id or "anonymous",
    )


@pytest.mark.asyncio
async def test_typed_adcp_error_keeps_its_own_wire_code_on_failed_task():
    """A typed AdCPError escaping to the outer handler keeps its own wire code.

    The envelope must carry the AdCPError's code (here ``VALIDATION_ERROR``),
    not a blanket ``INTERNAL_ERROR`` — ``_build_error_envelope`` passes typed
    errors through ``normalize_to_adcp_error`` unchanged.
    """
    handler, ctx = _make_handler()
    params = make_nl_send_message_request("Show me available products in the catalog")

    with patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY):
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
    recording/logging the original failure. It also pins the immediate-terminal
    no-webhook rule: this failed Task is returned synchronously, so no protocol
    webhook is emitted (a2a-guide.mdx terminal-state rule).
    """
    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(side_effect=RuntimeError("auth context unavailable"))
    handler._send_protocol_webhook = AsyncMock()
    ctx = make_a2a_context(headers={"host": "test.example.com"})
    params = make_nl_send_message_request("Show me available products in the catalog")

    result = await handler.on_message_send(params, context=ctx)

    assert isinstance(result, Task), f"expected a returned Task, got {type(result).__name__}"
    assert result.status.state == TaskState.TASK_STATE_FAILED
    assert_envelope_shape(
        extract_processing_error_envelope(result),
        "SERVICE_UNAVAILABLE",
        recovery="terminal",
        message_substr="auth context unavailable",
    )
    # Immediate terminal response → no webhook (the caller already has the Task).
    handler._send_protocol_webhook.assert_not_awaited()


@pytest.mark.asyncio
async def test_all_skills_failed_returns_failed_task_with_per_skill_artifacts_no_webhook():
    """All skills fail → immediate failed Task carrying each skill's envelope, no webhook.

    Each failed skill contributes its OWN ``error_result`` artifact (two-layer
    envelope) to the Task body, so both reasons are preserved without a joined
    webhook string. Because the failed Task is returned synchronously (immediate
    terminal), no protocol webhook is emitted — a2a-guide.mdx terminal-state rule.
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

    with patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY):
        result = await handler.on_message_send(params, context=ctx)

    assert isinstance(result, Task), f"expected a returned Task, got {type(result).__name__}"
    assert result.status.state == TaskState.TASK_STATE_FAILED

    # Both failures ride in the Task body as separate error artifacts — the
    # per-skill reasons are preserved without any joined webhook string.
    assert len(result.artifacts) == 2, f"expected one error artifact per failed skill, got {len(result.artifacts)}"
    messages = [_first_error_message(a) for a in result.artifacts]
    assert messages == ["first skill exploded", "second skill exploded"], messages

    # Immediate terminal response → no webhook.
    handler._send_protocol_webhook.assert_not_awaited()


@pytest.mark.asyncio
async def test_immediate_completed_task_sends_no_webhook():
    """An immediately-completed task returns synchronously and sends no webhook.

    a2a-guide.mdx "Webhook Trigger Rules for Terminal States": no push is sent
    when the initial response is already terminal — the buyer has the result in
    the response. Only non-terminal (submitted) initial responses notify.
    """
    handler, ctx = _make_handler()
    handler._send_protocol_webhook = AsyncMock()
    # A plain completed result (no "status": "submitted") → task completes immediately.
    handler._handle_explicit_skill = AsyncMock(return_value={"products": [{"id": "p1"}]})
    message = create_a2a_message_with_skill("get_products", {})
    params = SendMessageRequest(message=message)

    with patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY):
        result = await handler.on_message_send(params, context=ctx)

    assert result.status.state == TaskState.TASK_STATE_COMPLETED
    handler._send_protocol_webhook.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["submitted_first", "failed_first"])
async def test_mixed_submitted_and_failed_preserves_failure_envelope(order):
    """A mixed submitted+failed batch is terminal-failed with EVERY artifact preserved.

    Regression for the early-return bug: the submitted scan used to short-circuit
    before artifacts were built, returning SUBMITTED with zero artifacts and
    silently dropping the failed skill's UNSUPPORTED_FEATURE envelope. Now status
    is decided by precedence (failed > submitted > completed) AFTER all artifacts
    are built, so both invocation orders preserve the failure and the pending
    result. Immediate terminal failure → no webhook.
    """
    handler, ctx = _make_handler()
    handler._send_protocol_webhook = AsyncMock()
    submitted = {"media_buy_id": "mb-1", "status": "submitted"}
    failure = AdCPCapabilityNotSupportedError(message="approve_creative skill not yet implemented")

    if order == "submitted_first":
        skills = [("create_media_buy", {}), ("approve_creative", {})]
        handler._handle_explicit_skill = AsyncMock(side_effect=[submitted, failure])
    else:
        skills = [("approve_creative", {}), ("create_media_buy", {})]
        handler._handle_explicit_skill = AsyncMock(side_effect=[failure, submitted])

    message = create_a2a_message_with_skill(*skills[0])
    message.parts.append(create_a2a_message_with_skill(*skills[1]).parts[0])
    params = SendMessageRequest(message=message)

    with patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY):
        result = await handler.on_message_send(params, context=ctx)

    # Failure precedence → terminal FAILED, but every result is preserved as an artifact.
    assert result.status.state == TaskState.TASK_STATE_FAILED
    assert len(result.artifacts) == 2, f"both results must be preserved, got {len(result.artifacts)} artifacts"
    all_data = [extract_data_from_artifact(a) for a in result.artifacts]
    codes = [d.get("adcp_error", {}).get("code") for d in all_data]
    assert "UNSUPPORTED_FEATURE" in codes, f"failure envelope dropped: {all_data}"
    assert any(d.get("status") == "submitted" for d in all_data), f"submitted result dropped: {all_data}"
    handler._send_protocol_webhook.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_failure_webhook_preserves_all_artifacts_on_the_wire():
    """A failure webhook (async transition) serializes ALL of the Task's artifacts.

    Immediate terminal failures do not notify, but a task that fails after being
    returned async must push the full Task per a2a-guide.mdx ("FINAL STATES:
    Extract from .artifacts"). This tests the REAL serializer output (the wire
    payload emitted to the push service), not the mocked builder: a two-DataPart
    Task must not be reduced to a single stale value, and the envelope must never
    flatten to a bare ``{"error": str}``.
    """
    from google.protobuf import json_format

    handler = AdCPRequestHandler()

    envelope = AdCPRequestHandler._build_error_envelope(AdCPValidationError("brief must not be empty"))
    task = Task(
        id="task_async_fail",
        context_id="ctx_async_fail",
        status=TaskStatus(state=TaskState.TASK_STATE_FAILED),
    )
    # Two distinct DataParts — the second must survive (the old first-only decoder dropped it).
    task.artifacts.append(
        Artifact(artifact_id="error_1", name="processing_error", parts=[Part(data=_dict_to_value(envelope))])
    )
    task.artifacts.append(
        Artifact(
            artifact_id="ctx_1",
            name="get_products_result",
            parts=[Part(data=_dict_to_value({"products": [{"id": "p-sibling"}]}))],
        )
    )
    handler._task_push_configs[task.id] = TaskPushNotificationConfig(id="pnc_1", url="https://buyer.example/webhook")

    captured: dict = {}

    async def _capture(*, push_notification_config, payload, metadata):
        captured["payload"] = payload

    service = MagicMock()
    service.send_notification = AsyncMock(side_effect=_capture)

    # NOTE: create_a2a_webhook_payload is NOT mocked — we verify the real emitted wire Task.
    with patch("src.a2a_server.adcp_a2a_server.get_protocol_webhook_service", return_value=service):
        await handler._send_protocol_webhook(task, status="failed")

    service.send_notification.assert_awaited_once()
    wire = json.dumps(json_format.MessageToDict(captured["payload"]))
    # Both artifacts' structured data survive serialization; envelope not flattened.
    assert "VALIDATION_ERROR" in wire, f"envelope code missing from wire payload: {wire}"
    assert "brief must not be empty" in wire
    assert "p-sibling" in wire, f"second DataPart dropped from wire payload (first-only regression): {wire}"


def test_read_failed_a2a_task_strict_asserts_on_artifactless_task():
    """Strict mode must trip the artifact-present pin on an artifact-less failed Task.

    Pins the branch order in ``_read_failed_a2a_task``: the
    ``expect_processing_error`` dispatch happens BEFORE the ``task.artifacts``
    guard, so the strict reader's "failed Task must carry the error envelope
    artifact" assertion is reachable. Reverting to guard-first silently
    downgrades strict mode to the loose fallback and this test goes red.
    """
    from a2a.types import TaskStatus

    from tests.harness._base import _read_failed_a2a_task

    bare_failed = Task(id="t-bare", status=TaskStatus(state=TaskState.TASK_STATE_FAILED))

    with pytest.raises(AssertionError, match="must carry the error envelope artifact"):
        _read_failed_a2a_task(bare_failed, fallback_message="x", expect_processing_error=True)


def test_read_failed_a2a_task_loose_falls_back_on_artifactless_task():
    """Loose mode keeps the harness fallback: (None, bare AdCPError) — no raise."""
    from a2a.types import TaskStatus

    from src.core.exceptions import AdCPError
    from tests.harness._base import _read_failed_a2a_task

    bare_failed = Task(id="t-bare", status=TaskStatus(state=TaskState.TASK_STATE_FAILED))

    envelope, error = _read_failed_a2a_task(bare_failed, fallback_message="x")

    assert envelope is None
    assert type(error) is AdCPError, f"expected bare AdCPError fallback, got {type(error).__name__}"
    assert "A2A task failed" in str(error)


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
