"""Every REST v1 route must run AdCP version negotiation, after auth where required.

Version negotiation is deliberately NOT a blanket router dependency — a router-wide
dependency would reject an unsupported pin BEFORE authentication and disclose
``supported_versions`` to an anonymous caller. The cost of that decision is that
each route now opts in individually, and a route added without the dependency
silently skips negotiation: it accepts a pin it cannot serve and answers in
whatever shape its handler happens to produce.

Nothing else catches that. Stubbing ``_validate_version_pins`` reddens a single
route's test, so eleven of the twelve routes could lose the dependency with the
suite still green. This guard enumerates the routes instead of sampling one.

Two spellings are accepted because both are in use and both work:
  * ``@router.post("/x", dependencies=[Depends(_version_after_require)])``
  * ``async def handler(..., negotiated_version=Depends(_version_after_resolve))``
    — used where the handler needs the negotiated release for response compat.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_V1 = _REPO_ROOT / "src" / "routes" / "api_v1.py"

# The auth-first dependency and its auth-optional sibling (discovery routes).
_VERSION_DEPENDENCIES = frozenset({"_version_after_require", "_version_after_resolve"})


def _route_handlers() -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, list[ast.expr]]]:
    """Return ``(route_label, handler_node, decorator_nodes)`` for every ``@router.*`` route."""
    tree = ast.parse(_API_V1.read_text(encoding="utf-8"))
    routes: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, list[ast.expr]]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "router"):
                continue
            method = func.attr.upper()
            path = decorator.args[0].value if decorator.args and isinstance(decorator.args[0], ast.Constant) else "?"
            routes.append((f"{method} {path}", node, node.decorator_list))
    return routes


def _dependency_names(nodes: list[ast.AST]) -> set[str]:
    """Names passed to any ``Depends(...)`` call reachable from ``nodes``."""
    names: set[str] = set()
    for root in nodes:
        for child in ast.walk(root):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "Depends":
                for arg in child.args:
                    if isinstance(arg, ast.Name):
                        names.add(arg.id)
    return names


def test_api_v1_exposes_the_expected_number_of_routes() -> None:
    """A route count that silently drops to one would make the sweep below vacuous."""
    routes = _route_handlers()
    assert len(routes) >= 12, f"Expected the full /api/v1 surface, found only {[label for label, _, _ in routes]}"


@pytest.mark.parametrize(
    "route_label,handler,decorators", _route_handlers(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_every_rest_route_declares_version_negotiation(
    route_label: str,
    handler: ast.FunctionDef | ast.AsyncFunctionDef,
    decorators: list[ast.expr],
) -> None:
    """A route without the dependency accepts pins it cannot serve."""
    declared = _dependency_names(list(decorators)) | _dependency_names(list(handler.args.defaults))

    assert declared & _VERSION_DEPENDENCIES, (
        f"{route_label} ({_API_V1.relative_to(_REPO_ROOT)}:{handler.lineno}) declares no version-negotiation "
        f"dependency. Add Depends(_version_after_require) for auth-required routes, or "
        f"Depends(_version_after_resolve) for auth-optional ones. Found Depends: {sorted(declared) or 'none'}."
    )
