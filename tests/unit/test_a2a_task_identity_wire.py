"""In-process JSON-RPC wire grade for A2A task ownership (#1702 / #1720).

live_server xfails only POST an unknown id — they never hit the owner-compare
branch. Routes through the shared ``post_a2a_task_method`` boundary
(``tests/a2a_helpers.py``), which drives the same
``create_jsonrpc_routes(..., enable_v0_3_compat=True)`` path production uses
over the harness ``x-adcp-auth`` header contract, seeds an owned in-memory
task on the handler instance, and asserts sibling denial matches unknown-id
on the wire (code/message shape).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from tests.a2a_helpers import (
    OWNED_TASK_ID,
    OWNED_TASK_OWNER_TOK,
    OWNED_TASK_SIBLING_TOK,
    TASK_JSONRPC_METHODS,
    XAdcpAuthContextBuilder,
    assert_wire_auth_failure,
    assert_wire_task_not_found,
    post_a2a_task_method,
    seeded_owned_a2a_handler,
    seeded_owner_sibling_resolver,
)


def _post_task(handler: AdCPRequestHandler, *, method: str, task_id: str, token: str | None) -> dict[str, Any]:
    """Thin wrapper: auth-header shaping only; POST boundary is shared."""
    headers = {"x-adcp-auth": token} if token is not None else {}
    return post_a2a_task_method(
        handler,
        method=method,
        task_id=task_id,
        context_builder=XAdcpAuthContextBuilder(),
        headers=headers,
    )


@pytest.mark.parametrize("method", TASK_JSONRPC_METHODS)
def test_sibling_wire_error_matches_unknown_id(method):
    """Sibling ownership miss and unknown id share wire code/message shape.

    Mutating ``!= expected_owner`` out of ``_get_owned_in_memory_task_or_raise``
    must redden this: sibling would get a result while unknown still errors.
    """
    handler = seeded_owned_a2a_handler()
    resolve = seeded_owner_sibling_resolver()

    with (
        patch("src.core.resolved_identity.resolve_identity", side_effect=resolve),
        patch.object(handler, "_log_a2a_operation"),
    ):
        sibling_body = _post_task(handler, method=method, task_id=OWNED_TASK_ID, token=OWNED_TASK_SIBLING_TOK)
        unknown_body = _post_task(handler, method=method, task_id="task_does_not_exist", token=OWNED_TASK_OWNER_TOK)
        owner_body = _post_task(handler, method=method, task_id=OWNED_TASK_ID, token=OWNED_TASK_OWNER_TOK)

    assert "error" in sibling_body
    assert "error" in unknown_body
    assert "result" in owner_body

    # Exact equality — startswith would miss a suffix identity leak.
    # v0.3 compat flattens structured data to null (#1670); when that lifts,
    # the strict xfail in tests/e2e/test_a2a_endpoints_working.py XPASSes.
    assert_wire_task_not_found(sibling_body["error"], OWNED_TASK_ID)
    assert_wire_task_not_found(unknown_body["error"], "task_does_not_exist")
    assert owner_body["result"]["id"] == OWNED_TASK_ID


@pytest.mark.parametrize("method", TASK_JSONRPC_METHODS)
def test_unauthenticated_wire_is_auth_failure_not_task_not_found(method):
    """No Authorization → auth-failure shape, distinct from not-found on the wire."""
    handler = seeded_owned_a2a_handler()
    with patch.object(handler, "_log_a2a_operation"):
        body = _post_task(handler, method=method, task_id=OWNED_TASK_ID, token=None)

    assert "error" in body
    assert_wire_auth_failure(body["error"])
