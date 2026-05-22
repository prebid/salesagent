"""Structural guard: every transport boundary serializes errors via the two-layer envelope.

Every transport translator MUST call ``build_two_layer_error_envelope()`` so
the wire response has both ``adcp_error.code`` (envelope) and ``errors[0].code``
(payload) — required by AdCP spec 3.0.6 and by storyboard runners that check
either layer.

This guard is AST-based: scan the production boundary functions and verify their
bodies contain a call to ``build_two_layer_error_envelope``. If someone later
removes the call (e.g., a refactor that consolidates error handling), this
test fires.

Boundaries enforced (production paths only — dead helpers are deliberately not
pinned because a guard against unreachable code is a false positive of safety):
  - ``src/core/tool_error_logging.py::_translate_to_tool_error`` (MCP)
  - ``src/a2a_server/adcp_a2a_server.py::AdCPRequestHandler._build_error_envelope`` (A2A — production path called from on_message_send)
  - ``src/app.py::adcp_error_handler`` (REST/FastAPI)
"""

from __future__ import annotations

import ast
from pathlib import Path

# (filepath, qualified_name) — qualified_name supports `Class.method` for class methods.
BOUNDARY_FUNCTIONS = [
    ("src/core/tool_error_logging.py", "_translate_to_tool_error"),
    ("src/a2a_server/adcp_a2a_server.py", "AdCPRequestHandler._build_error_envelope"),
    ("src/app.py", "adcp_error_handler"),
]

ENVELOPE_BUILDER = "build_two_layer_error_envelope"


def _collect_module_functions(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return ``{name: FunctionDef}`` for every function in ``tree``.

    Indexes both module-level functions (``def foo``) and class methods
    (``def Class.method``) so guards can pin production paths that live
    inside a class body (e.g., ``AdCPRequestHandler._build_error_envelope``).
    """
    out: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for item in cls.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[f"{cls.name}.{item.name}"] = item
    return out


def _body_contains_builder_call(
    body_node: ast.AST, all_funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef], seen: set[str]
) -> bool:
    """Return True if ``body_node`` calls ``build_two_layer_error_envelope`` directly or transitively.

    N-level transitive call analysis with cycle detection via ``seen``: handler →
    in-module helper → … → builder. Recursion stops when a helper is revisited
    (cycle) or the call graph is exhausted. Prevents DRY refactors (extracting
    the envelope-building call into a shared ``_envelope_response`` helper) from
    defeating the guard without weakening its actual intent — every boundary's
    wire response must reach the builder somewhere in its call chain.
    """
    for child in ast.walk(body_node):
        if not isinstance(child, ast.Call):
            continue
        f = child.func
        if isinstance(f, ast.Name) and f.id == ENVELOPE_BUILDER:
            return True
        if isinstance(f, ast.Attribute) and f.attr == ENVELOPE_BUILDER:
            return True
        # Direct call to an in-module helper → recurse into the helper's body.
        callee_name = f.id if isinstance(f, ast.Name) else None
        if callee_name and callee_name in all_funcs and callee_name not in seen:
            seen.add(callee_name)
            if _body_contains_builder_call(all_funcs[callee_name], all_funcs, seen):
                return True
    return False


def _function_calls_builder(filepath: str, func_name: str) -> bool:
    """Return True if ``func_name`` in ``filepath`` reaches ``build_two_layer_error_envelope``.

    Accepts both direct calls and N-level transitive calls through helpers
    defined in the same module, so DRY refactors that extract a shared
    envelope-response helper still satisfy the guard.
    """
    path = Path(filepath)
    if not path.exists():
        return False
    try:
        tree = ast.parse(path.read_text(), filename=filepath)
    except SyntaxError:
        return False

    all_funcs = _collect_module_functions(tree)
    target = all_funcs.get(func_name)
    if target is None:
        return False
    return _body_contains_builder_call(target, all_funcs, seen={func_name})


class TestBoundaryTranslatorsUseEnvelope:
    """Each boundary must call build_two_layer_error_envelope()."""

    def test_mcp_boundary_uses_envelope(self):
        """``_translate_to_tool_error`` must call build_two_layer_error_envelope()."""
        path, fn = "src/core/tool_error_logging.py", "_translate_to_tool_error"
        assert _function_calls_builder(path, fn), (
            f"{path}::{fn} must call ``{ENVELOPE_BUILDER}`` so the MCP wire response "
            f"carries both adcp_error.code and errors[0].code (spec 3.0.6)."
        )

    def test_a2a_boundary_uses_envelope(self):
        """``AdCPRequestHandler._build_error_envelope`` must call build_two_layer_error_envelope().

        This is the production A2A path — called from ``on_message_send`` whenever
        an AdCPError reaches the dispatcher. The standalone ``_adcp_to_a2a_error``
        helper at module scope is intentionally NOT pinned here because it has no
        production callers (verified by grep); pinning unreachable code would be
        a false positive of safety.
        """
        path, fn = "src/a2a_server/adcp_a2a_server.py", "AdCPRequestHandler._build_error_envelope"
        assert _function_calls_builder(path, fn), (
            f"{path}::{fn} must call ``{ENVELOPE_BUILDER}`` so the A2A failed-Task DataPart "
            f"carries the spec two-layer envelope alongside legacy keys."
        )

    def test_rest_boundary_uses_envelope(self):
        """``adcp_error_handler`` must call build_two_layer_error_envelope()."""
        path, fn = "src/app.py", "adcp_error_handler"
        assert _function_calls_builder(path, fn), (
            f"{path}::{fn} must call ``{ENVELOPE_BUILDER}`` so REST responses have the spec two-layer envelope shape."
        )

    def test_envelope_builder_exported(self):
        """The envelope builder is the single source of truth — verify it exists and is callable."""
        from src.core.exceptions import build_two_layer_error_envelope

        assert callable(build_two_layer_error_envelope)
