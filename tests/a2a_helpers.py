"""Shared test helpers for A2A handler tests.

Provides make_a2a_context() to build a ServerCallContext the same way
AdCPCallContextBuilder.build() does in production, but without needing
a Starlette request object.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NamedTuple, TypedDict
from unittest.mock import patch

from a2a.server.context import ServerCallContext
from a2a.server.routes.common import ServerCallContextBuilder
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.types import CancelTaskRequest, GetTaskRequest, Task, TaskNotFoundError, TaskState, TaskStatus
from starlette.applications import Starlette
from starlette.testclient import TestClient

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler, _safe_id_for_log, _TaskOwner
from src.core.auth_context import AUTH_CONTEXT_STATE_KEY, AuthContext
from src.core.resolved_identity import ResolvedIdentity
from tests.factories.principal import PrincipalFactory

if TYPE_CHECKING:
    from a2a.compat.v0_3.types import Task as WireTask
    from a2a.compat.v0_3.types import TaskState as WireTaskState


class JsonRpcError(TypedDict):
    """Fixed JSON-RPC error body shape (v0.3 compat task-method denials)."""

    code: int
    message: str
    data: object | None


class JsonRpcEnvelope(TypedDict, total=False):
    """Fixed JSON-RPC success/error envelope from ``post_a2a_task_method``."""

    jsonrpc: str
    id: int | str | None
    result: dict[str, Any]
    error: JsonRpcError


# Shared ownership fixtures for unit + in-process wire altitudes (#1702 / #1720 / #1780).
OWNED_TASK_TENANT = "tenant_a"
OWNED_TASK_OWNER = "principal_owner"
OWNED_TASK_SIBLING = "principal_sibling"
# Cross-tenant isolation uses the SAME principal_id under a different tenant so
# the tenant half of ``_TaskOwner`` is graded independently of the principal half.
OWNED_TASK_OTHER_TENANT = "tenant_b"
OWNED_TASK_OTHER_PRINCIPAL = OWNED_TASK_OWNER
OWNED_TASK_ID = "task_owned_abc"
OWNED_TASK_OWNER_TOK = "owner-tok"
OWNED_TASK_SIBLING_TOK = "sibling-tok"

# Default host header for ServerCallContext builders (unit + wire altitudes).
A2A_TEST_HOST_HEADERS: dict[str, str] = {"host": "test.example.com"}

# Default non-disclosure needles — every role id that appears at any altitude.
OWNED_TASK_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    OWNED_TASK_TENANT,
    OWNED_TASK_OWNER,
    OWNED_TASK_SIBLING,
    OWNED_TASK_OTHER_TENANT,
    "leaked_tenant",
)

type TaskRequestCls = type[GetTaskRequest] | type[CancelTaskRequest]


class TaskMethodRow(NamedTuple):
    """One A2A task method row — named columns so consumers never index positionally."""

    request_cls: TaskRequestCls
    method_name: str
    jsonrpc_method: str
    operation: str


# request_cls / method_name / JSON-RPC method / _authenticate operation id —
# one vocabulary for unit + wire. Wire phrase = ``op.replace("_", " ") + " failed"``
# (same transform production ``_authenticate`` uses).
TASK_METHOD_MATRIX: tuple[TaskMethodRow, ...] = (
    TaskMethodRow(GetTaskRequest, "on_get_task", "tasks/get", "get_task"),
    TaskMethodRow(CancelTaskRequest, "on_cancel_task", "tasks/cancel", "cancel_task"),
)

# Hot slices — one home so unit/wire sites do not re-project by index.
TASK_METHOD_PAIRS: tuple[tuple[TaskRequestCls, str], ...] = tuple(
    (row.request_cls, row.method_name) for row in TASK_METHOD_MATRIX
)
TASK_METHOD_WITH_OPS: tuple[tuple[TaskRequestCls, str, str], ...] = tuple(
    (row.request_cls, row.method_name, row.operation) for row in TASK_METHOD_MATRIX
)
TASK_JSONRPC_METHODS: tuple[str, ...] = tuple(row.jsonrpc_method for row in TASK_METHOD_MATRIX)


def auth_operation_wire_phrase(operation: str) -> str:
    """Buyer-facing InternalError phrase for an ``_authenticate`` op id."""
    return f"{operation.replace('_', ' ')} failed"


def post_a2a_task_method(
    handler: AdCPRequestHandler,
    *,
    method: str,
    task_id: str,
    context_builder: ServerCallContextBuilder,
    headers: Mapping[str, str] | None = None,
) -> JsonRpcEnvelope:
    """One production JSON-RPC POST for ``tasks/get`` / ``tasks/cancel``.

    Shared boundary for the harness (``tests.harness._base.run_a2a_task_method``)
    and the in-process wire unit test (``tests/unit/test_a2a_task_identity_wire.py``):
    both build ``create_jsonrpc_routes(..., enable_v0_3_compat=True)``, open a
    ``TestClient(Starlette(...))``, POST the same ``{jsonrpc, id, method,
    params: {id}}`` body, assert HTTP 200, and return the parsed JSON-RPC
    envelope. Only ``context_builder`` differs between callers — the harness
    passes a fixed prepared context (auth already resolved), the wire test
    passes a header-parsing builder (bearer token in ``headers``). Hand-rolling
    this sequence twice let the harness's captured wire silently drift from
    what the unit test posts — the exact failure this boundary exists to
    prevent.
    """
    routes = create_jsonrpc_routes(
        request_handler=handler,
        rpc_url="/a2a",
        context_builder=context_builder,
        enable_v0_3_compat=True,
    )
    with TestClient(Starlette(routes=routes)) as client:
        response = client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": {"id": task_id}},
            headers=dict(headers) if headers else {},
        )
        assert response.status_code == 200, f"JSON-RPC {method} returned HTTP {response.status_code}: {response.text}"
        body: JsonRpcEnvelope = response.json()
        return body


def make_a2a_context(
    auth_token: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> ServerCallContext:
    """Build a ServerCallContext for A2A handler tests.

    Mirrors AdCPCallContextBuilder.build() — populates state["auth_context"]
    with an AuthContext containing the given token and headers.

    Args:
        auth_token: Bearer token (None for unauthenticated).
        headers: HTTP headers dict (e.g., {"host": "acme.example.com"}).

    Returns:
        ServerCallContext ready to pass to handler.on_message_send(params, context=ctx).
    """
    auth_ctx = AuthContext(
        auth_token=auth_token,
        headers=auth_headers_mapping(headers) if headers is not None else auth_headers_mapping({}),
    )
    return ServerCallContext(state={AUTH_CONTEXT_STATE_KEY: auth_ctx})


@contextmanager
def a2a_auth_as(handler: AdCPRequestHandler, identity: ResolvedIdentity) -> Iterator[None]:
    """Patch token extract + identity resolve for a single authenticated call."""
    with (
        patch.object(handler, "_get_auth_token", return_value="tok"),
        patch.object(handler, "_resolve_a2a_identity", return_value=identity),
    ):
        yield


async def invoke_owned_task_method(
    handler: AdCPRequestHandler,
    method_name: str,
    request_cls: TaskRequestCls,
    task_id: str,
) -> Task:
    """Act half shared by ownership unit tests (auth already patched via ``a2a_auth_as``).

    Returns the protobuf ``a2a.types.Task`` on success; denial paths raise
    ``TaskNotFoundError`` rather than returning a value.
    """
    return await getattr(handler, method_name)(request_cls(id=task_id), context=None)


def record_a2a_task_owner(
    handler: AdCPRequestHandler,
    task_id: str,
    *,
    tenant_id: str | None,
    principal_id: str | None,
) -> None:
    """Write the in-memory ownership record (shared by unit seed + harness seed)."""
    handler._task_owners[task_id] = _TaskOwner(tenant_id=tenant_id, principal_id=principal_id)


def seeded_owned_a2a_handler(
    *,
    task_id: str = OWNED_TASK_ID,
    tenant_id: str = OWNED_TASK_TENANT,
    principal_id: str = OWNED_TASK_OWNER,
    record_owner: bool = True,
) -> AdCPRequestHandler:
    """Minimal owned in-memory task handler (real ``__init__``, override ``tasks``)."""
    handler = AdCPRequestHandler()
    handler.tasks = {task_id: Task(id=task_id, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))}
    if record_owner:
        record_a2a_task_owner(handler, task_id, tenant_id=tenant_id, principal_id=principal_id)
    return handler


def seeded_owner_sibling_resolver() -> Callable[..., ResolvedIdentity]:
    """Token → owner/sibling identity map used by real-auth unit + wire altitudes."""
    return token_identity_resolver(
        {
            OWNED_TASK_SIBLING_TOK: owned_task_sibling_identity(),
            OWNED_TASK_OWNER_TOK: owned_task_owner_identity(),
        }
    )


def token_identity_resolver(
    mapping: Mapping[str, ResolvedIdentity],
) -> Callable[..., ResolvedIdentity]:
    """``resolve_identity`` side_effect: Bearer token → identity (shared by unit+wire)."""

    def resolve(*, auth_token: str | None, **_kwargs: object) -> ResolvedIdentity:
        # Explicit None check restores the narrowing mypy needs for Mapping.__getitem__.
        if auth_token is None or auth_token not in mapping:
            raise AssertionError(f"unexpected token: {auth_token!r}")
        return mapping[auth_token]

    return resolve


def auth_headers_mapping(headers: Mapping[str, str]) -> MappingProxyType[str, str]:
    """Immutable header map for ``AuthContext`` (matches production typing)."""
    return MappingProxyType({k.lower(): v for k, v in headers.items()})


def message_send_with_push(
    url: str,
) -> tuple[AdCPRequestHandler, Any, Any, ServerCallContext]:
    """Shared create-with-push-config fixture for ownership unit tests.

    Returns ``(handler, push, params, ctx)`` so each test keeps only its
    distinct patches/asserts. ``push`` / ``params`` stay ``Any`` at this
    altitude to avoid pulling SendMessage* into the module import graph for
    non-push callers; the concrete types are ``TaskPushNotificationConfig``
    and ``SendMessageRequest``.
    """
    from a2a.types import SendMessageConfiguration, SendMessageRequest, TaskPushNotificationConfig

    from tests.utils.a2a_helpers import create_a2a_message_with_skill

    handler = AdCPRequestHandler()
    push = TaskPushNotificationConfig(url=url)
    params = SendMessageRequest(
        message=create_a2a_message_with_skill("get_products", {"brief": "test"}),
        configuration=SendMessageConfiguration(task_push_notification_config=push),
    )
    ctx = make_a2a_context(auth_token="tok", headers={"host": "test.example.com"})
    return handler, push, params, ctx


# One role→(tenant_id, principal_id) table for unit + harness altitudes (#1780).
ROLE_TARGETS: dict[str, tuple[str, str]] = {
    "owner": (OWNED_TASK_TENANT, OWNED_TASK_OWNER),
    "sibling": (OWNED_TASK_TENANT, OWNED_TASK_SIBLING),
    "other_tenant": (OWNED_TASK_OTHER_TENANT, OWNED_TASK_OTHER_PRINCIPAL),
}


def owned_task_identity(role: str) -> ResolvedIdentity:
    """Build ``ResolvedIdentity`` for an ownership role (unit + wire altitudes)."""

    tenant_id, principal_id = ROLE_TARGETS[role]
    return PrincipalFactory.make_identity(principal_id=principal_id, tenant_id=tenant_id, protocol="a2a")


def owned_task_owner_identity() -> ResolvedIdentity:
    """Owner identity for OWNED_TASK_* fixtures (unit + wire)."""
    return owned_task_identity("owner")


def owned_task_sibling_identity() -> ResolvedIdentity:
    """Same-tenant sibling identity for OWNED_TASK_* fixtures (unit + wire)."""
    return owned_task_identity("sibling")


def owned_task_other_tenant_identity() -> ResolvedIdentity:
    """Cross-tenant identity reusing OWNED_TASK_OWNER principal_id."""
    return owned_task_identity("other_tenant")


def assert_no_identity_leak(message: str, data: object, needles: Iterable[str]) -> None:
    """Shape-agnostic forbidden-substring scan over a not-found message + data.

    Shared by the unit-altitude ``TaskNotFoundError`` oracle and the BDD wire
    step (JSON-RPC dict) so one needle set covers both altitudes.
    """
    blob = f"{message}{data!s}"
    for needle in needles:
        assert needle not in blob, f"identity leak {needle!r} in not-found body: message={message!r} data={data!r}"


def assert_task_not_found_nondisclosure(
    exc: TaskNotFoundError,
    task_id: str,
    *,
    forbidden_substrings: tuple[str, ...] = OWNED_TASK_FORBIDDEN_SUBSTRINGS,
) -> None:
    """Shared non-disclosure oracle for unit-altitude TaskNotFoundError objects.

    Expected message is a hand-written literal (never derived from production
    ``_task_not_found_message`` — both sides would move together). Sanitizer
    treatment matches ``_task_not_found_error`` so control-character ids stay joined.
    """
    safe_id = _safe_id_for_log(task_id)
    assert exc.message == f"Task not found: {safe_id}"
    assert isinstance(exc.data, dict)
    # Exact key set — membership-only checks miss unlisted leak keys.
    assert set(exc.data) == {"task_id", "adcp_error", "errors"}
    assert exc.data.get("task_id") == safe_id
    assert_no_identity_leak(exc.message, exc.data, forbidden_substrings)


def assert_wire_task_not_found(err: JsonRpcError | Mapping[str, object], task_id: str) -> None:
    """Exact wire-body oracle for not-found (v0.3 compat: code -32603, data null).

    Both the in-process live-POST harness (#1780) and
    ``tests/unit/test_a2a_task_identity_wire.py`` share this un-parameterized
    oracle — do not add per-caller ``code=`` / ``message=`` knobs; a split
    would let the harness silently drift from the buyer wire.

    Expected message is a literal over the raw *task_id* (not
    ``_task_not_found_message`` / ``_safe_id_for_log``) so a static identity
    leak inside the production builder cannot move both sides together. Clean
    task ids only — control-character sanitizer joining is graded at unit
    altitude via ``assert_task_not_found_nondisclosure``.

    When #1670 removes flattening, the strict xfail sibling at
    ``tests/e2e/test_a2a_endpoints_working.py`` (TestA2AServerIntegration) XPASSes
    by design while this hard-fails — keep the pointer at the failure site.
    """
    assert err == {
        "code": -32603,
        "message": f"Task not found: {task_id}",
        "data": None,
    }


def assert_wire_task_served(task: WireTask | None, task_id: str, state: WireTaskState) -> None:
    """Exact oracle for a served v0.3 compat wire Task (the success-path sibling of denial).

    Reads the typed wire slot (``env.last_a2a_wire_task`` /
    ``run_a2a_task_method``'s return value) rather than ``handler.tasks`` — a
    success-path adapter serialization regression must redden owner-get /
    owner-cancel, not just an in-memory-store check. Sibling to
    ``assert_wire_task_not_found`` / ``assert_wire_auth_failure`` so every
    Then in ``a2a_task_ownership.py`` asserts through one oracle per outcome
    instead of grading the served case inline.
    """
    assert task is not None, f"No Task returned for {task_id}"
    assert task.id == task_id
    assert task.status.state == state


def assert_wire_auth_failure(err: JsonRpcError | Mapping[str, object]) -> None:
    """Exact wire-body oracle for missing-auth (v0.3 compat: code -32603, data null).

    Same JSON-RPC shape as ``assert_wire_task_not_found`` (compat flattens both
    to -32603 until #1670) but a distinct ``message`` literal, so this proves
    auth failure never collapses into the ownership-denial not-found body.
    Shared by the wire unit test and the BDD auth-failure Then so both altitudes
    move together.
    """
    assert err == {
        "code": -32603,
        "message": "Missing authentication token",
        "data": None,
    }
