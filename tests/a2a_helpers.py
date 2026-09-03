"""Shared test helpers for A2A handler tests.

Provides make_a2a_context() to build a ServerCallContext the same way
AdCPCallContextBuilder.build() does in production, but without needing
a Starlette request object.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from types import MappingProxyType
from typing import Any, NamedTuple
from unittest.mock import patch

from a2a.server.context import ServerCallContext
from a2a.server.routes.common import ServerCallContextBuilder
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.types import CancelTaskRequest, GetTaskRequest, Task, TaskNotFoundError, TaskState, TaskStatus
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler, _safe_task_id_for_log, _TaskOwner
from src.core.auth_context import AUTH_CONTEXT_STATE_KEY, AuthContext
from src.core.resolved_identity import ResolvedIdentity
from tests.factories.principal import PrincipalFactory

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


def owned_task_owner_identity(*, tenant_id: str = OWNED_TASK_TENANT) -> ResolvedIdentity:
    """Owner principal identity for the shared ownership fixtures."""
    return PrincipalFactory.make_identity(
        principal_id=OWNED_TASK_OWNER,
        tenant_id=tenant_id,
        protocol="a2a",
    )


def owned_task_sibling_identity(*, tenant_id: str = OWNED_TASK_TENANT) -> ResolvedIdentity:
    """Same-tenant sibling identity for the shared ownership fixtures."""
    return PrincipalFactory.make_identity(
        principal_id=OWNED_TASK_SIBLING,
        tenant_id=tenant_id,
        protocol="a2a",
    )


def seeded_owner_sibling_resolver() -> Callable[..., ResolvedIdentity]:
    """Token → owner/sibling identity map used by real-auth unit + wire altitudes."""
    return token_identity_resolver(
        {
            OWNED_TASK_SIBLING_TOK: owned_task_sibling_identity(),
            OWNED_TASK_OWNER_TOK: owned_task_owner_identity(),
        }
    )


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
    """Act half shared by ownership unit tests (auth already patched via ``a2a_auth_as``)."""
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
    """Minimal owned in-memory task handler (bypasses ``__init__`` for unit/wire)."""
    handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
    handler.tasks = {task_id: Task(id=task_id, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))}
    handler._task_owners = {}
    if record_owner:
        record_a2a_task_owner(handler, task_id, tenant_id=tenant_id, principal_id=principal_id)
    return handler


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


def assert_task_not_found_nondisclosure(
    exc: TaskNotFoundError,
    task_id: str,
    *,
    forbidden_substrings: tuple[str, ...] = OWNED_TASK_FORBIDDEN_SUBSTRINGS,
) -> None:
    """Shared non-disclosure oracle for unit-altitude TaskNotFoundError objects.

    Expected message is a hand-written literal (never derived from production
    ``_task_not_found_message`` — both sides would move together). Sanitizer
    treatment matches ``_task_not_found`` so control-character ids stay joined.
    """
    safe_id = _safe_task_id_for_log(task_id)
    assert exc.message == f"Task not found: {safe_id}"
    assert isinstance(exc.data, dict)
    assert exc.data.get("task_id") == safe_id
    assert "adcp_error" in exc.data
    adcp_error = exc.data["adcp_error"]
    assert isinstance(adcp_error, dict)
    assert adcp_error["code"] == "REFERENCE_NOT_FOUND"
    assert adcp_error["recovery"] == "correctable"
    blob = f"{exc.message}{exc.data!s}"
    for needle in forbidden_substrings:
        assert needle not in blob


class XAdcpAuthContextBuilder(ServerCallContextBuilder):
    """Per-request token extraction on the harness's ``x-adcp-auth`` contract.

    Not ``Authorization: Bearer`` — both are production-valid, but pinning the
    wire unit test to a second header vocabulary means the A2A auth-context
    seam has to change in two places on a future edit (#1720 review).
    """

    def build(self, request: Request) -> ServerCallContext:
        token = request.headers.get("x-adcp-auth") or None
        return ServerCallContext(
            state={
                AUTH_CONTEXT_STATE_KEY: AuthContext(
                    auth_token=token,
                    headers=auth_headers_mapping(dict(request.headers.items())),
                )
            }
        )


def build_a2a_jsonrpc_client(
    handler: AdCPRequestHandler,
    *,
    context_builder: ServerCallContextBuilder | None = None,
) -> TestClient:
    """Shared route+client build for the live v0.3-compat JSON-RPC wire grade.

    Drives the same ``create_jsonrpc_routes(..., enable_v0_3_compat=True)``
    production call. Default ``context_builder`` is the harness ``x-adcp-auth``
    contract; callers that need a prepared context (BDD) pass their own.
    Prefer ``post_a2a_task_method`` for the full POST+200+json boundary.
    """
    routes = create_jsonrpc_routes(
        request_handler=handler,
        rpc_url="/a2a",
        context_builder=context_builder or XAdcpAuthContextBuilder(),
        enable_v0_3_compat=True,
    )
    return TestClient(Starlette(routes=routes))


def post_a2a_task_method(
    handler: AdCPRequestHandler,
    *,
    method: str,
    task_id: str,
    context_builder: ServerCallContextBuilder,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """One production JSON-RPC POST for ``tasks/get`` / ``tasks/cancel``.

    Shared boundary for the harness (``run_a2a_task_method``) and the
    in-process wire unit test. Route+client construction goes through
    ``build_a2a_jsonrpc_client`` so ``enable_v0_3_compat`` (and any future
    flag flip) has a single home — do not re-hand-roll
    ``create_jsonrpc_routes`` here (KM Aug-05).
    """
    with build_a2a_jsonrpc_client(handler, context_builder=context_builder) as client:
        response = client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": {"id": task_id}},
            headers=dict(headers) if headers else {},
        )
        assert response.status_code == 200, f"JSON-RPC {method} returned HTTP {response.status_code}: {response.text}"
        return response.json()


def assert_wire_auth_failure(err: Mapping[str, object]) -> None:
    """Wire-body oracle for unauthenticated A2A task calls (v0.3 compat).

    Under v0.3 compat the envelope may flatten to ``data: null`` (#1670); when
    ``data`` is present we grade ``AUTH_REQUIRED`` / ``correctable`` on the
    two-layer envelope attached at the raise site.
    """
    assert err.get("code") == -32603
    assert err.get("message") == "Missing authentication token"
    data = err.get("data")
    if isinstance(data, dict) and "adcp_error" in data:
        adcp_error = data["adcp_error"]
        assert isinstance(adcp_error, dict)
        assert adcp_error.get("code") == "AUTH_REQUIRED"
        assert adcp_error.get("recovery") == "correctable"


def assert_wire_no_identity_leak(
    error: Mapping[str, object],
    needles: tuple[str, ...] = OWNED_TASK_FORBIDDEN_SUBSTRINGS,
) -> None:
    """Wire-dict sibling of ``assert_task_not_found_nondisclosure`` (#1720 review).

    The object-altitude oracle only grades a ``TaskNotFoundError`` instance;
    the wire/BDD altitude captures a plain JSON-RPC error dict instead, so it
    needs its own sweep over the *same* forbidden-substring set — the wire
    path is the copy most likely to be missed if the non-disclosure policy
    changes.
    """
    blob = f"{error.get('message', '')}{error.get('data')!s}"
    for needle in needles:
        assert needle not in blob, f"identity leak {needle!r} in wire error body: {error!r}"


def assert_wire_task_not_found(err: Mapping[str, object], task_id: str) -> None:
    """Exact wire-body oracle for not-found (v0.3 compat: code -32603, data null).

    Both the in-process live-POST harness (#1780) and
    ``tests/unit/test_a2a_task_identity_wire.py`` share this un-parameterized
    oracle — do not add per-caller ``code=`` / ``message=`` knobs; a split
    would let the harness silently drift from the buyer wire.

    Expected message is a literal over the raw *task_id* (not
    ``_task_not_found_message`` / ``_safe_task_id_for_log``) so a static identity
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
