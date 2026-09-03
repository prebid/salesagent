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
from unittest.mock import ANY, patch

import pytest
from a2a.types import (
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
    _safe_task_id_for_log,
    _task_not_found_message,
    _TaskOwner,
)
from src.core.exceptions import AdCPTaskNotFoundError, AdCPValidationError
from src.core.schemas import GetProductsResponse
from tests.a2a_helpers import (
    A2A_TEST_HOST_HEADERS,
    OWNED_TASK_ID,
    OWNED_TASK_OTHER_TENANT,
    OWNED_TASK_OWNER,
    OWNED_TASK_OWNER_TOK,
    OWNED_TASK_SIBLING_TOK,
    OWNED_TASK_TENANT,
    TASK_METHOD_MATRIX,
    TASK_METHOD_PAIRS,
    TASK_METHOD_WITH_OPS,
    a2a_auth_as,
    assert_task_not_found_nondisclosure,
    auth_operation_wire_phrase,
    invoke_owned_task_method,
    make_a2a_context,
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


def test_task_not_found_message_is_literal_contract():
    """Buyer-facing formatter pinned by a hand-written literal (R5-B1)."""
    assert _task_not_found_message("task_x") == "Task not found: task_x"


def test_safe_task_id_for_log_escapes_and_truncates():
    """Sanitizer oracle — allowlist + truncate-after-substitute (R5-C2 / R5-N1a)."""
    assert _safe_task_id_for_log("a\nWARNING forged") == "a?WARNING?forged"
    assert _safe_task_id_for_log("x" * 200) == "x" * 100
    # Exact 100-char prefix with a non-allowlisted char in the truncate window.
    raw = "A" * 99 + "\r" + "B" * 50
    sanitized = _safe_task_id_for_log(raw)
    assert sanitized == "A" * 99 + "?"
    assert len(sanitized) == 100


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
    """``_internal_error_for`` branches pinned (R5-D2)."""
    err = _internal_error_for("op", exc)
    assert isinstance(err, InternalError)
    assert err.message == expected_message
    assert "[SQL:" not in err.message
    assert "secret" not in err.message
    assert err.data is not None
    assert err.data["adcp_error"]["code"] == expected_code
    assert err.data["adcp_error"]["recovery"] == expected_recovery


def test_internal_error_for_sql_normalized_exception_stays_out_of_message():
    """Normalized SQLAlchemy text must not reach InternalError.message (R5-D2)."""
    sql_exc = OperationalError("SELECT principals.token WHERE tok=:tok", {"tok": "super-secret"}, None)
    err = _internal_error_for("message processing", sql_exc)
    assert err.message == "message processing failed"
    blob = f"{err.message}{err.data!s}"
    assert "super-secret" not in blob
    assert "[SQL:" not in blob


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
    other_tenant = owned_task_owner_identity(tenant_id=OWNED_TASK_OTHER_TENANT)
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

    # Symmetric sinks — omit tenant/principal on both deny paths (R5-C1).
    record_error.assert_called_once_with("a2a", operation, ANY)
    assert "tenant_id" not in record_error.call_args.kwargs
    assert "principal_id" not in record_error.call_args.kwargs
    telem = record_error.call_args.args[2]
    assert type(telem) is AdCPTaskNotFoundError
    assert telem.message == f"Task not found: {_safe_task_id_for_log(forged_id)}"
    assert any("Task access denied on" in r.getMessage() for r in caplog.records)
    assert all("\n" not in r.getMessage() for r in caplog.records)

    caplog.clear()
    with a2a_auth_as(handler, owner):
        with patch("src.a2a_server.adcp_a2a_server.record_boundary_error") as record_unknown:
            with caplog.at_level(logging.WARNING, logger="src.a2a_server.adcp_a2a_server"):
                with pytest.raises(TaskNotFoundError) as unknown_exc:
                    await invoke_owned_task_method(handler, method_name, request_cls, "task_does_not_exist")

    # Same kwargs shape as ownership miss (R5-C1 / R5-N2c).
    record_unknown.assert_called_once_with("a2a", operation, ANY)
    assert "tenant_id" not in record_unknown.call_args.kwargs
    assert "principal_id" not in record_unknown.call_args.kwargs
    unknown_telem = record_unknown.call_args.args[2]
    assert type(unknown_telem) is AdCPTaskNotFoundError
    assert unknown_telem.message == f"Task not found: {_safe_task_id_for_log('task_does_not_exist')}"
    assert any("Task access denied on" in r.getMessage() for r in caplog.records)

    assert_task_not_found_nondisclosure(deny_exc.value, forged_id)
    assert_task_not_found_nondisclosure(unknown_exc.value, "task_does_not_exist")
    # Sibling denial must not mutate cancel state.
    assert handler.tasks[forged_id].status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_cls, method_name, wire_message",
    [(row.request_cls, row.method_name, auth_operation_wire_phrase(row.operation)) for row in TASK_METHOD_MATRIX],
)
async def test_auth_infra_failure_is_internal_error_not_task_not_found(request_cls, method_name, wire_message):
    """DB/infra failure during identity resolve must not collapse to TaskNotFoundError.

    Mutating the ``_authenticate`` except branch back to ``_task_not_found`` must
    redden this test: buyers see a fixed human-phrase InternalError, not not-found.
    """
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
            await invoke_owned_task_method(handler, method_name, request_cls, OWNED_TASK_ID)

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
    """Null-principal identity must not match a (None, None) owner row (R5-A2)."""
    handler = seeded_owned_a2a_handler(tenant_id=None, principal_id=None)
    null_identity = PrincipalFactory.make_identity(principal_id=None, tenant_id=None, tenant=None, protocol="a2a")

    with patch.object(handler, "_authenticate", return_value=null_identity):
        with pytest.raises(TaskNotFoundError) as exc_info:
            await invoke_owned_task_method(handler, method_name, request_cls, OWNED_TASK_ID)

    assert_task_not_found_nondisclosure(exc_info.value, OWNED_TASK_ID)


@pytest.mark.asyncio
async def test_auth_resolve_failure_leaves_no_orphan_push_config():
    """Resolve failure must not orphan ``_task_push_configs`` (R5-A1)."""
    from a2a.types import SendMessageConfiguration, TaskPushNotificationConfig

    from tests.utils.a2a_helpers import create_a2a_message_with_skill

    handler = AdCPRequestHandler()
    push = TaskPushNotificationConfig(url="https://example.com/hook")
    params = SendMessageRequest(
        message=create_a2a_message_with_skill("get_products", {"brief": "test"}),
        configuration=SendMessageConfiguration(task_push_notification_config=push),
    )
    ctx = make_a2a_context(auth_token="tok", headers=A2A_TEST_HOST_HEADERS)

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
    # Thread request-scoped config (no orphan map write) — assert_called_once_with
    # so the weak-mock guard does not flag a split call_args inspection.
    webhook.assert_called_once_with(ANY, status="failed", config=push)


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
