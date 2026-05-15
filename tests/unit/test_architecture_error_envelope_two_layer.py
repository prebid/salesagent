"""Structural guard: every transport boundary serializes errors via the two-layer envelope.

Every transport translator MUST call ``build_two_layer_error_envelope()`` so
the wire response has both ``adcp_error.code`` (envelope) and ``errors[0].code``
(payload) — required by AdCP spec 3.0.6 and by storyboard runners that check
either layer.

This guard is AST-based: scan the three boundary functions and verify their
bodies contain a call to ``build_two_layer_error_envelope``. If someone later
removes the call (e.g., a refactor that consolidates error handling), this
test fires.

Boundaries enforced:
  - ``src/core/tool_error_logging.py::_translate_to_tool_error`` (MCP)
  - ``src/a2a_server/adcp_a2a_server.py::_adcp_to_a2a_error`` (A2A)
  - ``src/app.py::adcp_error_handler`` (REST/FastAPI)
"""

from __future__ import annotations

import ast
from pathlib import Path

BOUNDARY_FUNCTIONS = [
    ("src/core/tool_error_logging.py", "_translate_to_tool_error"),
    ("src/a2a_server/adcp_a2a_server.py", "_adcp_to_a2a_error"),
    ("src/app.py", "adcp_error_handler"),
]

ENVELOPE_BUILDER = "build_two_layer_error_envelope"


def _function_calls_builder(filepath: str, func_name: str) -> bool:
    """Return True if ``func_name`` in ``filepath`` calls ``build_two_layer_error_envelope``."""
    path = Path(filepath)
    if not path.exists():
        return False
    try:
        tree = ast.parse(path.read_text(), filename=filepath)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    f = child.func
                    if isinstance(f, ast.Name) and f.id == ENVELOPE_BUILDER:
                        return True
                    if isinstance(f, ast.Attribute) and f.attr == ENVELOPE_BUILDER:
                        return True
            return False
    return False  # function not found


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
        """``_adcp_to_a2a_error`` must call build_two_layer_error_envelope()."""
        path, fn = "src/a2a_server/adcp_a2a_server.py", "_adcp_to_a2a_error"
        assert _function_calls_builder(path, fn), (
            f"{path}::{fn} must call ``{ENVELOPE_BUILDER}`` so the A2AError.data "
            f"carries the spec two-layer envelope alongside legacy keys."
        )

    def test_rest_boundary_uses_envelope(self):
        """``adcp_error_handler`` must call build_two_layer_error_envelope()."""
        path, fn = "src/app.py", "adcp_error_handler"
        assert _function_calls_builder(
            path, fn
        ), f"{path}::{fn} must call ``{ENVELOPE_BUILDER}`` so REST responses have the spec two-layer envelope shape."

    def test_envelope_builder_exported(self):
        """The envelope builder is the single source of truth — verify it exists and is callable."""
        from src.core.exceptions import build_two_layer_error_envelope

        assert callable(build_two_layer_error_envelope)
