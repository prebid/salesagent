"""Guard: auth/ownership guard calls echo request context where it is available.

The sibling guard ``test_architecture_auth_helper_signature.py`` makes the auth.py
helper SIGNATURES consistent (every ``require_*`` / ``*_or_raise`` accepts a
keyword-only ``context=``). This guard closes the loop on the CALL SITES.

In ``src/core/tools/``, every call to one of those helpers — plus the local
``_verify_principal`` ownership check that forwards ``context`` to them — must pass
``context=`` when the enclosing function has a request context available to pass.
The context is echoed into the failure envelope so a buyer agent can correlate the
auth/authorization error to its request.

"Context available" = the enclosing function has a ``req`` parameter (every ``_impl``
takes a validated ``*Request`` whose ``.context`` is the AdCP ``ContextObject``) OR a
``context`` parameter annotated with ``ContextObject`` (the individual-parameter
impls — ``_sync_creatives_impl`` / ``_list_creatives_impl`` / ``_activate_signal_impl``
— and the threaded ``_verify_principal``).

Deliberately NOT enforced: functions whose only context-like parameter is the
FastMCP transport ``Context`` / ``ToolContext`` (the ``task_management`` tools). The
auth helpers take an AdCP ``ContextObject``, never the transport object — passing the
transport ``Context`` would be a type error, so those call sites stay context-less.
That exclusion falls out of the ``ContextObject`` annotation check, so it needs no
allowlist.

Allowlist is empty: every qualifying call site is wired. A new tool that resolves
identity without echoing its request context fails the build.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist
from tests.unit._ast_helpers import iter_module_trees

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "src" / "core" / "tools"

# auth.py helpers that echo context into their error envelope, plus the local
# _verify_principal ownership check that forwards context to them.
AUTH_HELPERS = frozenset(
    {
        "require_identity",
        "require_principal_id",
        "require_tenant",
        "resolve_principal_or_raise",
        "_verify_principal",
    }
)

# Keyed by (relative_path, enclosing_function). Must only shrink.
KNOWN_VIOLATIONS: set[tuple[str, str]] = set()

FuncDef = (ast.FunctionDef, ast.AsyncFunctionDef)


def _function_has_request_context(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function can supply an AdCP context to an auth-helper call.

    Either it takes a ``req`` parameter (every ``_impl`` request carries ``.context``)
    or a ``context`` parameter annotated with ``ContextObject`` (NOT the transport
    ``Context``).
    """
    args = node.args
    for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
        if arg.arg == "req":
            return True
        if arg.arg == "context" and arg.annotation is not None and "ContextObject" in ast.unparse(arg.annotation):
            return True
    return False


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _call_passes_context(call: ast.Call) -> bool:
    return any(kw.arg == "context" for kw in call.keywords)


def _scan_module(tree: ast.Module, rel: str) -> list[tuple[str, str, int, str]]:
    """Yield (rel, enclosing_function, lineno, helper) for each unwired qualifying call."""
    found: list[tuple[str, str, int, str]] = []

    def visit(node: ast.AST, func_name: str, qualifies: bool) -> None:
        if isinstance(node, FuncDef):
            func_name = node.name
            qualifies = _function_has_request_context(node)
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in AUTH_HELPERS and qualifies and not _call_passes_context(node):
                found.append((rel, func_name, node.lineno, name))
        for child in ast.iter_child_nodes(node):
            visit(child, func_name, qualifies)

    visit(tree, "<module>", False)
    return found


def _find_unwired_call_sites() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for tree, rel_path in iter_module_trees([TOOLS_DIR]):
        for rel, func, _, _ in _scan_module(tree, rel_path):
            out.add((rel, func))
    return out


@pytest.mark.arch_guard
def test_auth_helper_calls_echo_context():
    """Every auth/ownership guard call in a context-bearing function passes context=."""
    assert_violations_match_allowlist(
        _find_unwired_call_sites(),
        KNOWN_VIOLATIONS,
        fix_hint=(
            "Pass `context=req.context` (or `context=context`) into auth helper calls "
            "when the enclosing function has a request context available."
        ),
    )
