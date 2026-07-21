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
    CancelTaskRequest,
    GetTaskRequest,
    InternalError,
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
from tests.helpers.secret_scrub import SECRET_BEARING_MESSAGE, assert_no_secret_leak
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
async def test_untyped_crash_raises_sanitized_internal_error_without_leaking_secrets():
    """An untyped internal crash → sanitized JSON-RPC InternalError; the raw exception
    text (which may carry credentials/SQL/hostnames) never reaches the client.

    Per transport-errors.mdx an internal crash is a TRANSPORT-layer error, and the
    error-security requirements forbid exposing internals. So untyped exceptions are
    logged server-side and surfaced as a generic InternalError — only TYPED
    ``AdCPError`` (controlled messages) become failed Tasks. Uses a secret-shaped
    message to pin that it is not echoed back.
    """
    handler, ctx = _make_handler()
    params = make_nl_send_message_request("Show me available products in the catalog")
    secret = SECRET_BEARING_MESSAGE

    with patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY):
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool", side_effect=RuntimeError(secret)):
            with pytest.raises(InternalError) as exc_info:
                await handler.on_message_send(params, context=ctx)

    err = exc_info.value
    client_facing = f"{err.message} {json.dumps(err.data)}"
    assert_no_secret_leak(client_facing)
    # Still a structured error with a safe, generic wire code + message.
    assert err.data["adcp_error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "internal error" in err.message.lower()


@pytest.mark.asyncio
async def test_untyped_crash_leaves_no_orphan_working_task():
    """An untyped crash routes to a sanitized InternalError AND leaves no retrievable task.

    Regression: the provisional Task is stored WORKING before dispatch; the untyped-error
    branch used to raise the sanitized InternalError without finalizing or removing it, so
    ``tasks/get`` still returned a task stuck in WORKING. The crash is a TRANSPORT-layer
    error (a JSON-RPC InternalError), not a Task-layer outcome, so the branch now drops the
    provisional task + push config before raising — nothing should remain retrievable.
    """
    handler, ctx = _make_handler()
    params = make_nl_send_message_request("Show me available products in the catalog")

    with patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY):
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool", side_effect=RuntimeError("boom")):
            with pytest.raises(InternalError):
                await handler.on_message_send(params, context=ctx)

    # The provisional WORKING task (and any push config) must be gone — no orphan.
    assert handler.tasks == {}, f"orphan task(s) left in WORKING after untyped crash: {list(handler.tasks)}"
    assert handler._task_push_configs == {}, "orphan push config left after untyped crash"


@pytest.mark.asyncio
async def test_explicit_skill_untyped_crash_scrubs_secret_from_failed_task():
    """An untyped crash inside an EXPLICIT-skill invocation returns a failed Task whose
    artifact is scrubbed of the raw exception — in BOTH the DataPart envelope and the
    human-readable TextPart.

    Regression: the explicit-skill path routed ``str(exc)`` onto the wire (via
    ``normalize_to_adcp_error`` → ``AdCPError(str(exc))``) while the outer/NL path was
    already sanitized. Both paths now share the single ``_safe_adcp_error`` policy, so an
    untyped crash becomes a generic internal error regardless of which path caught it.
    Mirrors the reviewer's repro: patch ``_handle_explicit_skill`` to raise a
    secret-shaped exception and inspect the returned failed Task.
    """
    handler, ctx = _make_handler()
    params = SendMessageRequest(message=create_a2a_message_with_skill("get_products", {"brief": "video"}))
    secret = SECRET_BEARING_MESSAGE

    with patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY):
        with patch.object(handler, "_handle_explicit_skill", new_callable=AsyncMock, side_effect=RuntimeError(secret)):
            result = await handler.on_message_send(params, context=ctx)

    assert isinstance(result, Task), f"expected a returned failed Task, got {type(result).__name__}"
    assert result.status.state == TaskState.TASK_STATE_FAILED, (
        f"expected TASK_STATE_FAILED, got {result.status.state!r}"
    )
    # Scan the ENTIRE failed artifact: the structured DataPart envelope AND every TextPart.
    artifact = result.artifacts[0]
    envelope = extract_data_from_artifact(artifact)
    text_parts = [p.text for p in artifact.parts if p.HasField("text")]
    client_facing = json.dumps(envelope) + " " + " ".join(text_parts)
    assert_no_secret_leak(client_facing)
    # Sanitized to a generic internal error, not a str(exc)-derived message/code.
    assert envelope["errors"][0]["code"] == "SERVICE_UNAVAILABLE"
    assert "internal error" in envelope["errors"][0]["message"].lower()


@pytest.mark.asyncio
async def test_failed_explicit_skill_task_is_remembered_and_pollable():
    """A synchronously-returned FAILED explicit-skill Task must be remembered under its owner
    (like the submitted/successful paths), so the buyer can poll tasks/get on it — a failed Task
    is a Task-layer outcome, not a transport error. Regression: the failed-batch branch used to
    return without `_remember_task`, leaving an ownerless orphan in the in-memory maps that
    `tasks/get` could never serve (and that diverged from the pollable NL-failed path)."""
    handler, ctx = _make_handler()
    params = SendMessageRequest(message=create_a2a_message_with_skill("get_products", {"brief": "video"}))

    async def _boom(params, identity):
        raise ValueError("boom")

    with patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY):
        with patch.object(handler, "_handle_get_products_skill", new_callable=AsyncMock, side_effect=_boom):
            result = await handler.on_message_send(params, context=ctx)

    assert isinstance(result, Task) and result.status.state == TaskState.TASK_STATE_FAILED
    # The fix: the failed Task is recorded WITH an owner (not an ownerless orphan) — this is what
    # makes it servable through the memory path of tasks/get.
    assert result.id in handler._task_owner, "failed explicit-skill Task left ownerless (not pollable)"
    assert result.id in handler.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc_factory", "expected_code", "msg_keyword"),
    [
        pytest.param(lambda s: ValueError(s), "VALIDATION_ERROR", "validate", id="ValueError"),
        pytest.param(lambda s: PermissionError(s), "AUTH_REQUIRED", "credential", id="PermissionError"),
    ],
)
async def test_explicit_skill_raw_builtin_scrubs_secret_but_keeps_semantic_code(
    exc_factory, expected_code, msg_keyword
):
    """A raw ``ValueError``/``PermissionError`` raised INSIDE a skill returns a failed Task whose
    envelope keeps the SEMANTIC code the synchronous boundaries emit (VALIDATION_ERROR /
    AUTH_REQUIRED) but is scrubbed of the raw ``str(e)``.

    This patches the SKILL (``_handle_get_products_skill``), NOT ``_handle_explicit_skill``, so the
    exception flows through the real normalization seam at ``_handle_explicit_skill`` — the seam the
    prior secret test bypassed by patching ``_handle_explicit_skill`` itself. Before the
    provenance-vs-semantics split, that seam normalized the built-in to a *trusted*
    ``AdCPValidationError``/``AdCPAuthorizationError`` and its raw message survived to the wire.
    """
    handler, ctx = _make_handler()
    params = SendMessageRequest(message=create_a2a_message_with_skill("get_products", {"brief": "video"}))
    secret = SECRET_BEARING_MESSAGE

    with patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY):
        with patch.object(
            handler, "_handle_get_products_skill", new_callable=AsyncMock, side_effect=exc_factory(secret)
        ):
            result = await handler.on_message_send(params, context=ctx)

    assert isinstance(result, Task), f"expected a returned failed Task, got {type(result).__name__}"
    assert result.status.state == TaskState.TASK_STATE_FAILED
    artifact = result.artifacts[0]
    envelope = extract_data_from_artifact(artifact)
    text_parts = [p.text for p in artifact.parts if p.HasField("text")]
    client_facing = json.dumps(envelope) + " " + " ".join(text_parts)
    assert_no_secret_leak(client_facing)
    # SEMANTIC code preserved (matches the synchronous boundary), message scrubbed AND
    # category-appropriate — a VALIDATION_ERROR / AUTH_REQUIRED must not read "internal error".
    assert envelope["errors"][0]["code"] == expected_code
    message = envelope["errors"][0]["message"].lower()
    assert msg_keyword in message
    assert "internal error" not in message


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
    errors through ``_safe_adcp_error`` unchanged (only untyped crashes are
    replaced with a generic error).
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
async def test_auth_extraction_failure_raises_sanitized_internal_error_no_nameerror():
    """A crash before identity resolution raises a sanitized InternalError, no NameError.

    Pins the ``identity = None`` hoist: auth-token extraction happens before identity
    resolution, so the untyped-crash handler must read ``identity`` (None) without a
    ``NameError`` while logging. The crash is untyped → sanitized JSON-RPC
    InternalError (not a failed Task), the raw text is not leaked, and no webhook.
    """
    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(side_effect=RuntimeError("auth context unavailable at db.internal"))
    handler._send_protocol_webhook = AsyncMock()
    ctx = make_a2a_context(headers={"host": "test.example.com"})
    params = make_nl_send_message_request("Show me available products in the catalog")

    with pytest.raises(InternalError) as exc_info:
        await handler.on_message_send(params, context=ctx)

    err = exc_info.value
    client_facing = f"{err.message} {json.dumps(err.data)}"
    assert "db.internal" not in client_facing and "auth context unavailable" not in client_facing, client_facing
    assert err.data["adcp_error"]["code"] == "SERVICE_UNAVAILABLE"
    handler._send_protocol_webhook.assert_not_awaited()


@pytest.mark.asyncio
async def test_multi_skill_message_rejected_before_any_side_effect():
    """A message carrying >1 skill is rejected up front — no skill runs, no side effects.

    Aggregating divergent per-skill outcomes into one Task is incoherent when a skill
    has real side effects (a submitted create_media_buy persists a workflow while a
    sibling fails). So a multi-skill batch is rejected as a typed application failure
    (UNSUPPORTED_FEATURE) BEFORE dispatch — ``_handle_explicit_skill`` is never
    called, and the immediate terminal failure sends no webhook.
    """
    handler, ctx = _make_handler()
    handler._send_protocol_webhook = AsyncMock()
    handler._handle_explicit_skill = AsyncMock()  # must NOT be called
    message = create_a2a_message_with_skill("create_media_buy", {})
    message.parts.append(create_a2a_message_with_skill("approve_creative", {}).parts[0])
    params = SendMessageRequest(message=message)

    with patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY):
        result = await handler.on_message_send(params, context=ctx)

    assert isinstance(result, Task)
    assert result.status.state == TaskState.TASK_STATE_FAILED
    envelope = extract_processing_error_envelope(result)
    assert envelope["adcp_error"]["code"] == "UNSUPPORTED_FEATURE", envelope
    assert "multiple skills" in envelope["errors"][0]["message"].lower()
    handler._handle_explicit_skill.assert_not_awaited()  # zero side effects
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
async def test_send_protocol_webhook_serializes_every_artifact_including_duplicate_names():
    """``_send_protocol_webhook`` puts EVERY artifact's data on the wire — including
    duplicate-named ones — never a single stale value or a flattened error string.

    Scope note: this is a focused unit test of the webhook serializer at the status
    production actually calls it with (``submitted``); the real async *failure*
    transition is emitted elsewhere (``src/core/context_manager.py``) and is out of
    this helper's scope. Two artifacts share the name ``error_result`` (repeated
    skill in one message) plus a distinct sibling — all three must survive
    serialization, so name-based overwriting is caught.
    """
    from google.protobuf import json_format

    handler = AdCPRequestHandler()

    env_a = AdCPRequestHandler._build_error_envelope(AdCPValidationError("first skill exploded"))
    env_b = AdCPRequestHandler._build_error_envelope(AdCPValidationError("second skill exploded"))
    task = Task(id="task_sub", context_id="ctx_sub", status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED))
    # Two artifacts with the SAME name (repeated skill) + a distinct sibling.
    task.artifacts.append(
        Artifact(artifact_id="skill_result_1", name="error_result", parts=[Part(data=_dict_to_value(env_a))])
    )
    task.artifacts.append(
        Artifact(artifact_id="skill_result_2", name="error_result", parts=[Part(data=_dict_to_value(env_b))])
    )
    task.artifacts.append(
        Artifact(
            artifact_id="skill_result_3",
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

    # create_a2a_webhook_payload is NOT mocked — we verify the real emitted wire payload.
    with patch("src.a2a_server.adcp_a2a_server.get_protocol_webhook_service", return_value=service):
        await handler._send_protocol_webhook(task, status="submitted")

    service.send_notification.assert_awaited_once()
    wire = json.dumps(json_format.MessageToDict(captured["payload"]))
    # BOTH same-named error envelopes AND the sibling survive (no name overwrite, no flatten).
    assert "first skill exploded" in wire, f"first same-named artifact dropped: {wire}"
    assert "second skill exploded" in wire, f"second same-named artifact overwritten by name collision: {wire}"
    assert "p-sibling" in wire, f"sibling artifact dropped: {wire}"


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


@pytest.mark.parametrize(
    ("handler_method", "params"),
    [
        ("on_get_task", GetTaskRequest(id="task_auth")),
        ("on_cancel_task", CancelTaskRequest(id="task_auth")),
    ],
)
@pytest.mark.parametrize("auth_token", [None, "invalid-token"])
@pytest.mark.asyncio
async def test_task_management_auth_failures_stay_on_json_rpc_wire(handler_method, params, auth_token):
    """Missing/invalid task-management auth must remain a serialized transport error.

    ``_durable_lookup_identity`` previously swallowed every resolver exception and made
    tasks/get + tasks/cancel return ``None`` (indistinguishable from task-not-found).
    Drive both public handlers and serialize the real ``InvalidRequestError`` through
    the SDK dispatcher helper so the regression is pinned at the JSON-RPC wire altitude.
    """
    from a2a.server.request_handlers.response_helpers import build_error_response

    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value=auth_token)
    if auth_token:
        handler._resolve_a2a_identity = MagicMock(
            side_effect=InvalidRequestError(message="Authentication token is invalid or expired.")
        )

    with pytest.raises(InvalidRequestError) as exc_info:
        await getattr(handler, handler_method)(params, context=None)

    wire = build_error_response("req-auth", exc_info.value)
    serialized = json.dumps(wire if isinstance(wire, dict) else wire.model_dump(), default=str)
    assert "Authentication" in serialized or "authentication" in serialized
    assert "task_auth" not in serialized, "auth failures must not be downgraded to task-not-found output"
