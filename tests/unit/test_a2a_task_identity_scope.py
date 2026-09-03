"""Identity-scope gate for A2A in-memory tasks/get and tasks/cancel (#1702).

A bare ``self.tasks.get(task_id)`` served (or canceled) any caller's request
once they knew the id. These tests pin auth-first ownership against
``_task_owners`` and prove wrong-principal callers get the same
``TaskNotFoundError`` as an unknown id (no existence oracle). Auth failures
propagate as ``InvalidRequestError`` and do not collapse to not-found.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from unittest.mock import ANY, AsyncMock, patch

import pytest
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    InternalError,
    InvalidRequestError,
    SendMessageRequest,
    TaskNotFoundError,
    TaskState,
    TaskStatus,
)
from sqlalchemy.exc import OperationalError

from src.a2a_server.adcp_a2a_server import (
    AdCPRequestHandler,
    _internal_error_for,
    _safe_id_for_log,
    _task_not_found_message,
    _TaskOwner,
)
from src.core.exceptions import AdCPTaskNotFoundError, AdCPValidationError
from src.core.schemas import GetProductsResponse
from tests.a2a_helpers import (
    A2A_TEST_HOST_HEADERS,
    OWNED_TASK_FORBIDDEN_SUBSTRINGS,
    OWNED_TASK_ID,
    OWNED_TASK_OWNER,
    OWNED_TASK_OWNER_TOK,
    OWNED_TASK_SIBLING,
    OWNED_TASK_SIBLING_TOK,
    OWNED_TASK_TENANT,
    TASK_METHOD_MATRIX,
    TASK_METHOD_PAIRS,
    TASK_METHOD_WITH_OPS,
    a2a_auth_as,
    assert_no_identity_leak,
    assert_task_not_found_nondisclosure,
    invoke_owned_task_method,
    make_a2a_context,
    message_send_with_push,
    owned_task_other_tenant_identity,
    owned_task_owner_identity,
    owned_task_sibling_identity,
    seeded_owned_a2a_handler,
    seeded_owner_sibling_resolver,
)
from tests.factories import PrincipalFactory
from tests.utils.a2a_helpers import create_a2a_text_message

_OP_ID_RE = re.compile(r"^[a-z]+(_[a-z]+)*$")
_A2A_SERVER = Path(__file__).resolve().parents[2] / "src" / "a2a_server" / "adcp_a2a_server.py"


def _authenticate_operation_literals() -> list[str]:
    """String literals passed as ``operation=`` into ``_authenticate`` / owned lookup."""
    tree = ast.parse(_A2A_SERVER.read_text(), filename=str(_A2A_SERVER))
    ops: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in {"_authenticate", "_get_owned_in_memory_task_or_raise"}:
                for kw in node.keywords:
                    if kw.arg != "operation":
                        continue
                    # Parameter forward inside ``_get_owned_in_memory_task_or_raise``.
                    if isinstance(kw.value, ast.Name) and kw.value.id == "operation":
                        continue
                    assert isinstance(kw.value, ast.Constant), (
                        f"operation= must be a string Constant, got {type(kw.value).__name__}"
                    )
                    assert isinstance(kw.value.value, str), (
                        f"operation= Constant must be str, got {type(kw.value.value).__name__}"
                    )
                    ops.append(kw.value.value)
            self.generic_visit(node)

    _Visitor().visit(tree)
    return ops


def _assert_symmetric_deny_telemetry(mock, operation, task_id, caplog, *, tenant_id, principal_id):
    """Shared deny-path telemetry oracle (ownership miss and unknown id)."""
    mock.assert_called_once_with(
        "a2a",
        operation,
        ANY,
        tenant_id=tenant_id,
        principal_id=principal_id,
    )
    telem = mock.call_args.args[2]
    assert type(telem) is AdCPTaskNotFoundError
    assert telem.message == f"Task not found: {_safe_id_for_log(task_id)}"
    assert any("Task access denied on" in r.getMessage() for r in caplog.records)
    assert all("\n" not in r.getMessage() for r in caplog.records)
    assert all("ownership_miss=" not in r.getMessage() for r in caplog.records)


def test_task_not_found_message_is_literal_contract():
    """Buyer-facing formatter pinned by a hand-written literal."""
    assert _task_not_found_message("task_x") == "Task not found: task_x"


def test_safe_id_for_log_escapes_and_truncates():
    """Sanitizer oracle — allowlist + truncate."""
    assert _safe_id_for_log("a\nWARNING forged") == "a?WARNING?forged"
    assert _safe_id_for_log("x" * 200) == "x" * 100
    assert "\\" not in _safe_id_for_log("A" * 99 + "\r" + "B" * 50)


@pytest.mark.parametrize(
    "exc, expected_message, expected_code, expected_recovery",
    [
        (
            RuntimeError("boom [SQL: SELECT x] [parameters: {'tok': 'secret'}]"),
            "op failed",
            "SERVICE_UNAVAILABLE",
            "transient",
        ),
        (
            AdCPValidationError("capability missing"),
            "capability missing",
            "VALIDATION_ERROR",
            "correctable",
        ),
    ],
)
def test_internal_error_for_typed_and_untyped(exc, expected_message, expected_code, expected_recovery):
    """``_internal_error_for`` branches pinned."""
    err = _internal_error_for("op", exc)
    assert isinstance(err, InternalError)
    assert err.message == expected_message
    assert "[SQL:" not in err.message
    assert "secret" not in err.message
    assert err.data is not None
    assert err.data["adcp_error"]["code"] == expected_code
    assert err.data["adcp_error"]["recovery"] == expected_recovery


def test_internal_error_for_sql_normalized_exception_stays_out_of_message():
    """Normalized SQLAlchemy text must not reach InternalError.message."""
    sql_exc = OperationalError("SELECT principals.token WHERE tok=:tok", {"tok": "super-secret"}, None)
    err = _internal_error_for("message processing", sql_exc)
    assert err.message == "message processing failed"
    blob = f"{err.message}{err.data!s}"
    assert "super-secret" not in blob
    assert "[SQL:" not in blob


def _make_nl_message(text: str) -> SendMessageRequest:
    message = Message(
        message_id=str(uuid.uuid4()),
        role=Role.ROLE_USER,
    )
    message.parts.append(Part(text=text))
    return SendMessageRequest(message=message)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_cls, method_name",
    TASK_METHOD_PAIRS,
)
async def test_create_records_owner_and_scopes_poll(request_cls, method_name):
    """Real constructor create→poll: owner allowed; sibling/other-tenant denied."""
    handler = AdCPRequestHandler()
    owner = owned_task_owner_identity()
    sibling = owned_task_sibling_identity()
    other_tenant = owned_task_other_tenant_identity()
    ctx = make_a2a_context(auth_token="test-token", headers=A2A_TEST_HOST_HEADERS)
    params = SendMessageRequest(message=create_a2a_text_message("Show me available products in the catalog"))

    with patch("src.core.resolved_identity.resolve_identity", return_value=owner):
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_products:
            mock_products.return_value = GetProductsResponse(products=[])
            created = await handler.on_message_send(params, context=ctx)

    task_id = created.id
    assert handler._task_owners[task_id] == _TaskOwner(tenant_id=OWNED_TASK_TENANT, principal_id=OWNED_TASK_OWNER)

    with a2a_auth_as(handler, owner):
        task = await invoke_owned_task_method(handler, method_name, request_cls, task_id)
    assert task.id == task_id
    if method_name == "on_cancel_task":
        assert task.status.state == TaskState.TASK_STATE_CANCELED
    else:
        assert task.status.state == TaskState.TASK_STATE_COMPLETED

    # Re-seed completed state so deny checks do not depend on cancel mutation.
    handler.tasks[task_id].status.CopyFrom(TaskStatus(state=TaskState.TASK_STATE_COMPLETED))

    with a2a_auth_as(handler, sibling):
        with pytest.raises(TaskNotFoundError) as sibling_exc:
            await invoke_owned_task_method(handler, method_name, request_cls, task_id)

    with a2a_auth_as(handler, other_tenant):
        with pytest.raises(TaskNotFoundError) as other_exc:
            await invoke_owned_task_method(handler, method_name, request_cls, task_id)

    with a2a_auth_as(handler, owner):
        with pytest.raises(TaskNotFoundError) as unknown_exc:
            await invoke_owned_task_method(handler, method_name, request_cls, "task_does_not_exist")

    assert_task_not_found_nondisclosure(sibling_exc.value, task_id)
    assert_task_not_found_nondisclosure(other_exc.value, task_id)
    assert_task_not_found_nondisclosure(unknown_exc.value, "task_does_not_exist")
    # Deny shape for the owned id matches the canonical not-found from this handler.
    assert sibling_exc.value.message == other_exc.value.message
    assert sibling_exc.value.data == other_exc.value.data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_cls, method_name",
    TASK_METHOD_PAIRS,
)
async def test_owner_can_access_owned_in_memory_task(request_cls, method_name):
    """The recorded owner authenticates and is served / can cancel."""
    handler = seeded_owned_a2a_handler()
    identity = owned_task_owner_identity()

    with a2a_auth_as(handler, identity):
        task = await invoke_owned_task_method(handler, method_name, request_cls, OWNED_TASK_ID)

    assert task.id == OWNED_TASK_ID
    if method_name == "on_cancel_task":
        assert task.status.state == TaskState.TASK_STATE_CANCELED
    else:
        assert task.status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_cls, method_name, operation",
    TASK_METHOD_WITH_OPS,
)
async def test_sibling_principal_denied_same_as_unknown(request_cls, method_name, operation, caplog):
    """Same-tenant sibling must not read or cancel — identical to unknown id."""
    forged_id = f"{OWNED_TASK_ID}\nWARNING forged"
    handler = seeded_owned_a2a_handler(task_id=forged_id)
    sibling = owned_task_sibling_identity()
    owner = owned_task_owner_identity()

    with a2a_auth_as(handler, sibling):
        with patch("src.a2a_server.adcp_a2a_server.record_boundary_error") as record_error:
            with caplog.at_level(logging.WARNING, logger="src.a2a_server.adcp_a2a_server"):
                with pytest.raises(TaskNotFoundError) as deny_exc:
                    await invoke_owned_task_method(handler, method_name, request_cls, forged_id)

    _assert_symmetric_deny_telemetry(
        record_error,
        operation,
        forged_id,
        caplog,
        tenant_id=OWNED_TASK_TENANT,
        principal_id=OWNED_TASK_SIBLING,
    )

    caplog.clear()
    with a2a_auth_as(handler, owner):
        with patch("src.a2a_server.adcp_a2a_server.record_boundary_error") as record_unknown:
            with caplog.at_level(logging.WARNING, logger="src.a2a_server.adcp_a2a_server"):
                with pytest.raises(TaskNotFoundError) as unknown_exc:
                    await invoke_owned_task_method(handler, method_name, request_cls, "task_does_not_exist")

    _assert_symmetric_deny_telemetry(
        record_unknown,
        operation,
        "task_does_not_exist",
        caplog,
        tenant_id=OWNED_TASK_TENANT,
        principal_id=OWNED_TASK_OWNER,
    )

    assert_task_not_found_nondisclosure(deny_exc.value, forged_id)
    assert_task_not_found_nondisclosure(unknown_exc.value, "task_does_not_exist")
    # Sibling denial must not mutate cancel state.
    assert handler.tasks[forged_id].status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name, wire_message",
    [
        ("on_get_task", "get task failed"),
        ("on_cancel_task", "cancel task failed"),
        ("on_get_task_push_notification_config", "get push notification config failed"),
        ("on_create_task_push_notification_config", "set push notification config failed"),
        ("on_list_task_push_notification_configs", "list push notification configs failed"),
        ("on_delete_task_push_notification_config", "delete push notification config failed"),
    ],
)
async def test_auth_infra_failure_is_internal_error_not_task_not_found(method_name, wire_message):
    """DB/infra failure during identity resolve must not collapse to TaskNotFoundError.

    Mutating the ``_authenticate`` except branch back to ``_task_not_found_error`` must
    redden this test: buyers see a fixed human-phrase InternalError, not not-found.
    Also pins ``operation.replace("_", " ")`` as the buyer-facing phrase for the
    four push-config methods (same ``_authenticate`` seam).
    """
    from unittest.mock import MagicMock

    handler = seeded_owned_a2a_handler()

    with (
        patch.object(handler, "_get_auth_token", return_value="tok"),
        patch.object(
            handler,
            "_resolve_a2a_identity",
            side_effect=OperationalError("db down", None, None),
        ),
    ):
        with pytest.raises(InternalError) as exc_info:
            if method_name in {"on_get_task", "on_cancel_task"}:
                request_cls = GetTaskRequest if method_name == "on_get_task" else CancelTaskRequest
                await invoke_owned_task_method(handler, method_name, request_cls, OWNED_TASK_ID)
            else:
                await getattr(handler, method_name)(MagicMock(), context=None)

    raised = exc_info.value
    assert raised.message == wire_message
    assert "_" not in raised.message
    # Spec MUST: senders populate error.recovery on every error —
    # by-layer/L3/error-handling.mdx § Forward-compatible decoding (normative), AdCP 3.1.1.
    assert raised.data is not None
    assert raised.data["adcp_error"]["recovery"] == "transient"
    assert raised.data["adcp_error"]["code"] == "SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_cls, method_name",
    TASK_METHOD_PAIRS,
)
async def test_sibling_denied_via_real_auth_token_path(request_cls, method_name):
    """Ownership compare with real ``_get_auth_token`` (only resolve_identity patched).

    Unlike ``a2a_auth_as`` tests, the token is extracted from ServerCallContext so
    mutating ``!= expected_owner`` out of the gate reddens this path — unknown-id
    alone would stay green.
    """
    handler = seeded_owned_a2a_handler()
    resolve = seeded_owner_sibling_resolver()

    with patch("src.core.resolved_identity.resolve_identity", side_effect=resolve):
        sibling_ctx = make_a2a_context(auth_token=OWNED_TASK_SIBLING_TOK, headers=A2A_TEST_HOST_HEADERS)
        with pytest.raises(TaskNotFoundError) as deny_exc:
            await getattr(handler, method_name)(request_cls(id=OWNED_TASK_ID), context=sibling_ctx)

        owner_ctx = make_a2a_context(auth_token=OWNED_TASK_OWNER_TOK, headers=A2A_TEST_HOST_HEADERS)
        with pytest.raises(TaskNotFoundError) as unknown_exc:
            await getattr(handler, method_name)(request_cls(id="task_does_not_exist"), context=owner_ctx)

    assert_task_not_found_nondisclosure(deny_exc.value, OWNED_TASK_ID)
    assert_task_not_found_nondisclosure(unknown_exc.value, "task_does_not_exist")
    assert handler.tasks[OWNED_TASK_ID].status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_cls, method_name",
    TASK_METHOD_PAIRS,
)
async def test_unauthenticated_poller_raises_invalid_request(request_cls, method_name):
    """Missing token raises InvalidRequestError — must not collapse to TaskNotFoundError."""
    handler = seeded_owned_a2a_handler()

    with patch.object(handler, "_get_auth_token", return_value=None):
        with pytest.raises(InvalidRequestError):
            await invoke_owned_task_method(handler, method_name, request_cls, OWNED_TASK_ID)

    assert handler.tasks[OWNED_TASK_ID].status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_cls, method_name",
    TASK_METHOD_PAIRS,
)
async def test_null_principal_denied_against_null_owner_row(request_cls, method_name):
    """Null-principal identity must not match a (None, None) owner row."""
    handler = seeded_owned_a2a_handler(tenant_id=None, principal_id=None)
    null_identity = PrincipalFactory.make_identity(principal_id=None, tenant_id=None, tenant=None, protocol="a2a")

    with patch.object(handler, "_authenticate", return_value=null_identity):
        with pytest.raises(TaskNotFoundError) as exc_info:
            await invoke_owned_task_method(handler, method_name, request_cls, OWNED_TASK_ID)

    assert_task_not_found_nondisclosure(exc_info.value, OWNED_TASK_ID)


class _HasUrl:
    """``==`` matcher: object has the expected ``.url`` (real config carries a fresh uuid ``.id``)."""

    def __init__(self, url: str) -> None:
        self._url = url

    def __eq__(self, other: object) -> bool:
        return getattr(other, "url", None) == self._url

    def __repr__(self) -> str:
        return f"_HasUrl({self._url!r})"


@pytest.mark.asyncio
async def test_auth_resolve_failure_leaves_no_orphan_push_config():
    """Resolve failure must not orphan ``_task_push_configs``."""
    handler, push, params, ctx = message_send_with_push("https://example.com/hook")

    with (
        patch.object(handler, "_get_auth_token", return_value="tok"),
        patch.object(
            handler,
            "_resolve_a2a_identity",
            side_effect=RuntimeError("resolve exploded"),
        ),
        patch.object(handler, "_send_protocol_webhook", return_value=None) as webhook,
    ):
        with pytest.raises(InternalError):
            await handler.on_message_send(params, context=ctx)

    assert handler.tasks == {}
    assert handler._task_owners == {}
    assert handler._task_push_configs == {}
    # Thread request-scoped accepted registration (no orphan map write) —
    # assert_called_once_with so the weak-mock guard does not flag a split
    # call_args inspection. Match on URL: registration carries a fresh uuid id.
    webhook.assert_called_once_with(ANY, status="failed", config=_HasUrl(push.url))


@pytest.mark.asyncio
async def test_auth_resolve_failure_still_sends_real_webhook():
    """The ``config=`` operand actually drives a real send, not just a mock call shape (#1720).

    Companion to ``test_auth_resolve_failure_leaves_no_orphan_push_config``,
    which patches ``_send_protocol_webhook`` out and can never detect a
    regression to the early-map-write shape or to dropping the ``config=``
    operand entirely. This test leaves ``_send_protocol_webhook`` real and
    patches only the webhook service it calls, so it reddens on either
    reversion.
    """
    handler, push, params, ctx = message_send_with_push("https://example.com/hook")

    mock_service = AsyncMock()
    mock_service.notify.return_value = True

    with (
        patch.object(handler, "_get_auth_token", return_value="tok"),
        patch.object(handler, "_resolve_a2a_identity", side_effect=RuntimeError("resolve exploded")),
        patch(
            "src.a2a_server.adcp_a2a_server.get_protocol_webhook_service",
            return_value=mock_service,
        ),
    ):
        with pytest.raises(InternalError):
            await handler.on_message_send(params, context=ctx)

    # Equality matchers (not exact objects — push registration carries a
    # freshly minted uuid; payload is built inside notify()) so the single
    # assert_called_once_with() stays atomic per
    # test_architecture_weak_mock_assertions.py instead of splitting count and
    # argument checks across two statements.
    mock_service.notify.assert_called_once_with(
        _HasUrl(push.url),
        task=ANY,
        status=ANY,
        result=ANY,
        protocol="a2a",
        context_id=ANY,
    )


def test_resolve_identity_without_principal_id_raises_invalid_request():
    """Authenticated resolve with no principal_id hits the no-principal guard."""
    handler = AdCPRequestHandler()
    no_principal = PrincipalFactory.make_identity(principal_id=None, tenant_id=OWNED_TASK_TENANT, protocol="a2a")
    ctx = make_a2a_context(auth_token="tok", headers=A2A_TEST_HOST_HEADERS)

    with patch("src.core.resolved_identity.resolve_identity", return_value=no_principal):
        with pytest.raises(InvalidRequestError, match="invalid or expired"):
            handler._resolve_a2a_identity("tok", require_valid_token=True, context=ctx)


def test_auth_operation_ids_are_underscore_replaceable():
    """Op ids reaching ``_authenticate`` must match snake_case (no hand-maintained map)."""
    ops = _authenticate_operation_literals()
    assert ops, "expected at least one operation= literal in adcp_a2a_server.py"
    for op_id in ops:
        assert _OP_ID_RE.fullmatch(op_id), f"op id {op_id!r} is not underscore-replaceable"


def test_anonymous_a2a_identity_has_no_testing_context():
    """Anonymous factory must match production ``from_headers({})`` → None testing_context."""
    assert PrincipalFactory.make_anonymous_a2a_identity().testing_context is None


@pytest.mark.asyncio
async def test_discovery_create_records_anonymous_owner_without_auth():
    """Unauthenticated discovery records owner from ResolvedIdentity (never None).

    Regression for Integration infra: mocking ``_resolve_a2a_identity`` to return
    ``None`` passed before create+own co-location, then AttributeError on
    ``identity.tenant_id``. Production always returns ResolvedIdentity.
    """
    from src.core.exceptions import AdCPValidationError
    from tests.utils.a2a_helpers import create_a2a_message_with_skill

    handler = AdCPRequestHandler()
    anonymous = PrincipalFactory.make_anonymous_a2a_identity(tenant_id=OWNED_TASK_TENANT)

    async def raise_validation(params, identity):
        raise AdCPValidationError("synthetic discovery failure")

    with patch.object(handler, "_handle_get_products_skill", raise_validation):
        handler._get_auth_token = lambda context=None: None
        handler._resolve_a2a_identity = lambda *args, **kwargs: anonymous
        created = await handler.on_message_send(
            SendMessageRequest(message=create_a2a_message_with_skill("get_products", {"brief": "test"})),
            context=make_a2a_context(auth_token=None),
        )

    assert created.id in handler._task_owners
    assert handler._task_owners[created.id] == _TaskOwner(tenant_id=OWNED_TASK_TENANT, principal_id=None)
    assert created.id in handler.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_cls, method_name",
    [(row[0], row[1]) for row in TASK_METHOD_MATRIX],
)
async def test_null_tenant_denied_against_null_tenant_owner_row(request_cls, method_name):
    """Null-tenant identity must not match a (None, shared_pid) owner row."""
    handler = seeded_owned_a2a_handler(tenant_id=None, principal_id=OWNED_TASK_OWNER)
    null_tenant = PrincipalFactory.make_identity(
        principal_id=OWNED_TASK_OWNER, tenant_id=None, tenant=None, protocol="a2a"
    )

    with patch.object(handler, "_authenticate", return_value=null_tenant):
        with pytest.raises(TaskNotFoundError) as exc_info:
            await invoke_owned_task_method(handler, method_name, request_cls, OWNED_TASK_ID)

    assert_task_not_found_nondisclosure(exc_info.value, OWNED_TASK_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_cls, method_name",
    [(row[0], row[1]) for row in TASK_METHOD_MATRIX],
)
async def test_owner_row_without_task_is_not_found(request_cls, method_name):
    """Orphan owner row (no task) must not-found — grades ``task is None`` operand."""
    from tests.a2a_helpers import record_a2a_task_owner

    handler = AdCPRequestHandler()
    orphan_id = "task_owner_without_task"
    record_a2a_task_owner(handler, orphan_id, tenant_id=OWNED_TASK_TENANT, principal_id=OWNED_TASK_OWNER)
    assert orphan_id not in handler.tasks

    with a2a_auth_as(handler, owned_task_owner_identity()):
        with pytest.raises(TaskNotFoundError) as exc_info:
            await invoke_owned_task_method(handler, method_name, request_cls, orphan_id)

    assert_task_not_found_nondisclosure(exc_info.value, orphan_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_cls, method_name",
    TASK_METHOD_PAIRS,
)
async def test_task_without_owner_record_is_not_found(request_cls, method_name):
    """Task present with no ``_task_owners`` row must fail closed (same as unknown id).

    Inverse of ``test_owner_row_without_task_is_not_found``. A denied cancel
    must not mutate the stored Task — there is no recorded owner who could
    poll it on the wire.
    """
    handler = seeded_owned_a2a_handler(record_owner=False)
    assert OWNED_TASK_ID in handler.tasks
    assert OWNED_TASK_ID not in handler._task_owners
    prior = handler.tasks[OWNED_TASK_ID].status.state

    with a2a_auth_as(handler, owned_task_owner_identity()):
        with pytest.raises(TaskNotFoundError) as exc_info:
            await invoke_owned_task_method(handler, method_name, request_cls, OWNED_TASK_ID)

    assert_task_not_found_nondisclosure(exc_info.value, OWNED_TASK_ID)
    assert handler.tasks[OWNED_TASK_ID].status.state == prior


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_cls, method_name",
    [(row[0], row[1]) for row in TASK_METHOD_MATRIX],
)
async def test_ownership_lookup_is_unconditional_on_unknown_id(request_cls, method_name):
    """Unknown-id path must call ``_task_owners.get`` at least once (no existence oracle)."""

    class _CountingDict(dict):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.get_calls = 0

        def get(self, key, default=None):  # noqa: A003
            self.get_calls += 1
            return super().get(key, default)

    handler = seeded_owned_a2a_handler()
    counting = _CountingDict(handler._task_owners)
    handler._task_owners = counting

    with a2a_auth_as(handler, owned_task_owner_identity()):
        with pytest.raises(TaskNotFoundError):
            await invoke_owned_task_method(handler, method_name, request_cls, "task_does_not_exist")

    assert counting.get_calls >= 1


@pytest.mark.asyncio
async def test_success_path_push_config_sends_real_webhook_from_map():
    """Second webhook-guard operand (``_task_push_configs.get``) must drive a real send.

    Companion to ``test_auth_resolve_failure_still_sends_real_webhook`` (first
    operand / request-scoped ``config=``). Leaves ``_send_protocol_webhook`` real.
    """
    handler, push, params, ctx = message_send_with_push("https://example.com/hook-success")
    owner = owned_task_owner_identity()

    mock_service = AsyncMock()
    mock_service.notify.return_value = True

    with (
        patch.object(handler, "_get_auth_token", return_value="tok"),
        patch.object(handler, "_resolve_a2a_identity", return_value=owner),
        patch(
            "src.a2a_server.adcp_a2a_server.get_protocol_webhook_service",
            return_value=mock_service,
        ),
        patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_products,
    ):
        mock_products.return_value = GetProductsResponse(products=[])
        created = await handler.on_message_send(params, context=ctx)

    assert created.id in handler._task_push_configs
    # Success-path webhook must have been sent via the map-stored config.
    assert mock_service.notify.call_count >= 1
    mock_service.notify.assert_any_call(
        _HasUrl(push.url),
        task=ANY,
        status=ANY,
        result=ANY,
        protocol="a2a",
        context_id=ANY,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_cls, method_name",
    [(row[0], row[1]) for row in TASK_METHOD_MATRIX],
)
async def test_missing_auth_token_carries_adcp_envelope(request_cls, method_name):
    """Shared missing-token seam must attach AUTH_REQUIRED envelope (stripping reddens)."""
    handler = seeded_owned_a2a_handler()

    with patch.object(handler, "_get_auth_token", return_value=None):
        with pytest.raises(InvalidRequestError) as exc_info:
            await invoke_owned_task_method(handler, method_name, request_cls, OWNED_TASK_ID)

    raised = exc_info.value
    assert raised.message == "Missing authentication token"
    assert isinstance(raised.data, dict)
    assert raised.data["adcp_error"]["code"] == "AUTH_REQUIRED"
    assert raised.data["adcp_error"]["recovery"] == "correctable"
    assert raised.data["adcp_error"].get("suggestion")


def test_leaked_tenant_needle_has_producer():
    """OWNED_TASK_FORBIDDEN_SUBSTRINGS includes leaked_tenant and the scan fires on it."""
    with pytest.raises(AssertionError, match="leaked_tenant"):
        assert_no_identity_leak("ok", {"x": "leaked_tenant"}, OWNED_TASK_FORBIDDEN_SUBSTRINGS)
