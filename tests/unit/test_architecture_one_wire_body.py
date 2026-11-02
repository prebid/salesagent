"""Guard: every transport sends the body ``to_wire`` produced, and adds nothing to it.

THE RULE. A transport chooses its own top-level container -- MCP a ``ToolResult``, A2A an
artifact ``DataPart``, REST the HTTP body -- because those genuinely differ. What goes inside
is one function's output, ``src.core.tools._wire.to_wire``, unmodified. A transport that
serializes a response itself, or that writes a key into the body afterwards, is a transport
deciding what a buyer receives.

A transport that stamps a key into the payload puts a property none of the fourteen pinned
response schemas declares on the wire, and one response object produces three different
documents.

Envelope fields need no transport help, which is what makes the rule cheap to keep: every
response model inherits ``adcp.types.ProtocolEnvelope``, so ``status``, ``message``,
``replayed`` and the rest serialize as ordinary fields and reach all three transports at once.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: The response path of each transport: the module, and the function inside it that turns a
#: response model into the body. Named rather than discovered -- there are exactly three, and
#: a fourth transport should have to add itself here deliberately.
_TRANSPORT_RESPONSE_PATHS: dict[str, str] = {
    "src/core/tools/_mcp.py": "mcp_result",
    "src/routes/api_v1.py": "handler",
    "src/a2a_server/adcp_a2a_server.py": "_dispatch_skill",
}


def _function(path: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(Path(path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path} no longer defines {name}() -- update _TRANSPORT_RESPONSE_PATHS")


@pytest.mark.arch_guard
@pytest.mark.parametrize(("path", "func"), sorted(_TRANSPORT_RESPONSE_PATHS.items()))
def test_transport_calls_to_wire_and_does_not_serialize_itself(path: str, func: str) -> None:
    """The response path calls ``to_wire`` and never ``model_dump`` on the response."""
    node = _function(path, func)
    calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
    names = {ast.unparse(c.func) for c in calls}

    assert "to_wire" in names, (
        f"{path}::{func} does not call to_wire(). Every transport sends the same body; only "
        f"the container around it differs."
    )
    serializes = sorted(n for n in names if n.endswith(("response.model_dump", "result.model_dump")))
    assert serializes == [], (
        f"{path}::{func} serializes the response itself via {serializes}. Call to_wire() -- a "
        f"second serialization is a second answer to what the buyer receives."
    )


@pytest.mark.arch_guard
@pytest.mark.parametrize(("path", "func"), sorted(_TRANSPORT_RESPONSE_PATHS.items()))
def test_transport_writes_no_key_into_the_body(path: str, func: str) -> None:
    """Nothing is assigned into the produced body after ``to_wire`` returns it.

    Catches the shape the A2A stamping had: ``data = to_wire(response)`` followed by
    ``data["success"] = ...``. A field a buyer should receive belongs on the response model,
    where all three transports pick it up; a field only one transport wants belongs in that
    transport's own envelope, not in the payload.
    """
    node = _function(path, func)
    written: list[str] = []
    for stmt in ast.walk(node):
        targets: list[ast.expr] = []
        if isinstance(stmt, ast.Assign):
            targets = list(stmt.targets)
        elif isinstance(stmt, ast.AugAssign | ast.AnnAssign):
            targets = [stmt.target]
        for target in targets:
            if isinstance(target, ast.Subscript):
                written.append(ast.unparse(target))
        if isinstance(stmt, ast.Call) and isinstance(stmt.func, ast.Attribute):
            if stmt.func.attr in {"setdefault", "update"}:
                written.append(ast.unparse(stmt))

    assert written == [], (
        f"{path}::{func} writes {written} into the response body. Put the field on the "
        f"response model so every transport emits it, or put it in this transport's own "
        f"envelope -- never into the buyer's payload from one transport only."
    )
