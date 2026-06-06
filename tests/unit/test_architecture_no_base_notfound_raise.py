"""Guard: raise a typed AdCPNotFoundError subclass, never the base class.

The base ``AdCPNotFoundError`` carries the internal ``NOT_FOUND`` code (wire →
``INVALID_REQUEST``, recovery=terminal). Entity-specific subclasses carry a typed
identity and recovery=correctable:

    AdCPMediaBuyNotFoundError, AdCPPackageNotFoundError, AdCPProductNotFoundError,
    AdCPAccountNotFoundError, AdCPContextNotFoundError, AdCPCreativeNotFoundError,
    AdCPFormatNotFoundError, AdCPTaskNotFoundError

Raising the BASE class in business logic loses that identity and emits a generic
terminal not-found. This guard forbids ``raise AdCPNotFoundError(...)`` anywhere
in ``src/`` — use (or create) a specific subclass.

Only a fresh ``raise`` of the base is flagged. Catching it in an ``except``
clause (e.g. ``except AdCPNotFoundError``) is fine and intentionally allowed —
the base is the right thing to catch, just not to raise.

Allowlist: one sanctioned generic — ``account_helpers.resolve_account`` raises the
base for an unsupported ``AccountReference`` union variant, a defensive fall-through
that is unreachable for validated input (the union is exhaustive) and is not a real
entity-not-found. Every other not-found raise across src/ uses a typed subclass.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit._ast_helpers import iter_module_trees

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [REPO_ROOT / "src"]

# Keyed by (relative_path, enclosing_function). Must only shrink.
KNOWN_VIOLATIONS: set[tuple[str, str]] = {
    # Defensive, unreachable fall-through for an unsupported AccountReference union
    # variant — a malformed-request guard, not an entity-not-found. Sanctioned generic.
    ("src/core/helpers/account_helpers.py", "resolve_account"),
}

FuncDef = (ast.FunctionDef, ast.AsyncFunctionDef)


def _raises_base_notfound(node: ast.Raise) -> bool:
    """True for ``raise AdCPNotFoundError`` / ``raise AdCPNotFoundError(...)`` (the base, exactly)."""
    exc = node.exc
    if exc is None:
        return False  # bare re-raise
    target = exc.func if isinstance(exc, ast.Call) else exc
    if isinstance(target, ast.Name):
        return target.id == "AdCPNotFoundError"
    if isinstance(target, ast.Attribute):
        return target.attr == "AdCPNotFoundError"
    return False


def _scan_module(tree: ast.Module, rel: str) -> list[tuple[str, str, int]]:
    found: list[tuple[str, str, int]] = []

    def visit(node: ast.AST, func_name: str) -> None:
        if isinstance(node, FuncDef):
            func_name = node.name
        if isinstance(node, ast.Raise) and _raises_base_notfound(node):
            found.append((rel, func_name, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, func_name)

    visit(tree, "<module>")
    return found


def _find_base_notfound_raises() -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    for tree, rel_path in iter_module_trees(SCAN_DIRS):
        out.extend(_scan_module(tree, rel_path))
    return out


def test_no_base_notfound_raise():
    """No fresh raise of the base AdCPNotFoundError in src/core/tools or src/core/helpers."""
    violations = [
        f"  {rel}:{lineno} in {func}()"
        for rel, func, lineno in _find_base_notfound_raises()
        if (rel, func) not in KNOWN_VIOLATIONS
    ]
    assert not violations, (
        f"Found {len(violations)} raise(s) of the base AdCPNotFoundError.\n"
        "Raise a typed subclass (AdCPMediaBuyNotFoundError, AdCPProductNotFoundError, "
        "AdCPCreativeNotFoundError, AdCPFormatNotFoundError, AdCPTaskNotFoundError, "
        "AdCPContextNotFoundError, ...) so the error carries a typed identity and "
        "recovery=correctable. Create a new subclass if none fits:\n\n" + "\n".join(violations)
    )


def test_known_violations_not_stale():
    """Every allowlisted (file, function) must still raise the base class."""
    actual = {(rel, func) for rel, func, _ in _find_base_notfound_raises()}
    stale = KNOWN_VIOLATIONS - actual
    assert not stale, "Stale allowlist entries (now using a subclass):\n" + "\n".join(
        f"  {rel} :: {func}" for rel, func in sorted(stale)
    )
