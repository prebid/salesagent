"""A tool registered auth-optional must not enforce a principal in its ``_impl``.

The two transport registries — ``AUTH_OPTIONAL_TOOLS`` (MCP) and
``DISCOVERY_SKILLS`` (A2A) — decide whether the boundary rejects an
unauthenticated caller BEFORE the version pin is validated. When a registry
claims a tool is public but its ``_impl`` calls ``require_principal_id``, the
two disagree and the caller gets the wrong error: the boundary waves the
request through, the version gate fires first, and an anonymous caller learns
``supported_versions`` from a VERSION_UNSUPPORTED rejection for a task that was
going to raise AUTH_REQUIRED anyway.

That is exactly how ``list_accounts`` drifted. AdCP 3.1.1
(``dist/docs/3.1.0/accounts/tasks/list_accounts.mdx``) defines it as returning
"all accounts the authenticated agent can operate on", and the ``_impl``
enforced that — but both registries listed it as public discovery, so MCP and
A2A disclosed the negotiation metadata that REST correctly withheld.

The registries are the declaration; ``require_principal_id`` is the behavior.
This guard makes them agree.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import iter_call_expressions

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOLS_DIR = _REPO_ROOT / "src" / "core" / "tools"

_PRINCIPAL_GATE = "require_principal_id"


def _auth_optional_tool_names() -> set[str]:
    """The union of both transports' auth-optional registries, read from source."""
    from src.a2a_server.adcp_a2a_server import DISCOVERY_SKILLS
    from src.core.mcp_auth_middleware import AUTH_OPTIONAL_TOOLS

    return set(AUTH_OPTIONAL_TOOLS) | set(DISCOVERY_SKILLS)


def _impl_functions() -> dict[str, tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Map every ``_<tool>_impl`` in the tools package to its file and AST node."""
    found: dict[str, tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for path in sorted(_TOOLS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("_"):
                if node.name.endswith("_impl"):
                    found[node.name] = (path, node)
    return found


def _calls_principal_gate(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int | None:
    """Return the line of the first ``require_principal_id`` call, or ``None``."""
    for call in iter_call_expressions(node, _PRINCIPAL_GATE):
        return call.lineno
    return None


@pytest.mark.parametrize("tool_name", sorted(_auth_optional_tool_names()))
def test_auth_optional_tool_has_a_resolvable_impl(tool_name: str) -> None:
    """A registry entry that names no ``_impl`` would make the gate check vacuous."""
    impls = _impl_functions()
    assert f"_{tool_name}_impl" in impls, (
        f"{tool_name!r} is registered auth-optional but no _{tool_name}_impl exists under "
        f"{_TOOLS_DIR.relative_to(_REPO_ROOT)}. Rename the registry entry to match the impl, "
        "or drop it — an unresolvable entry silently exempts the tool from this guard."
    )


@pytest.mark.parametrize("tool_name", sorted(_auth_optional_tool_names()))
def test_auth_optional_tool_does_not_enforce_a_principal(tool_name: str) -> None:
    """Registered public, but gated on a principal, means the wrong error reaches the buyer."""
    impls = _impl_functions()
    path, node = impls[f"_{tool_name}_impl"]
    gate_line = _calls_principal_gate(node)

    assert gate_line is None, (
        f"{tool_name!r} is registered auth-optional (AUTH_OPTIONAL_TOOLS / DISCOVERY_SKILLS) but "
        f"_{tool_name}_impl calls {_PRINCIPAL_GATE}() at "
        f"{path.relative_to(_REPO_ROOT)}:{gate_line}. Either remove it from both registries so the "
        "boundary rejects AUTH before the version gate discloses supported_versions, or drop the "
        "principal requirement from the impl. Cite the spec for whichever way you resolve it."
    )
