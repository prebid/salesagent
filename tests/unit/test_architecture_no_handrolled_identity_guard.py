"""Guard: business-logic _impl functions must use the typed identity helpers.

The transport boundary resolves identity; ``_impl`` functions receive a
``ResolvedIdentity`` and must narrow it through ``src.core.auth`` helpers
(``require_identity`` / ``require_principal_id`` / ``require_tenant``), which raise a
canonical ``AdCPAuthRequiredError`` with a wire code + recovery. They must NOT
hand-roll the guard via:

  - ``assert identity is not None``  — stripped by ``python -O``; emits no wire error.
  - ``if identity is None [or identity.principal_id is None or identity.tenant_id is None]:``
    ``raise ...`` — divergent messages, bypasses the canonical wire envelope, and
    is the byte-identical duplication that accumulates when one ``_impl`` is migrated
    and its sibling is not.

Scans ``src/core/tools``, ``src/core/helpers``, ``src/adapters``. ``assert identity
is not None`` is flagged in ANY function (no legitimate use — a wrapper should raise,
not assert). Bare ``X is None`` identity guards are flagged only inside ``*_impl``
functions AND only when the branch RAISES; transport wrappers (``*_raw``, MCP tool
functions) legitimately guard at the boundary and are out of scope, and a non-raising
``if X is None`` branch (graceful degradation — strip pricing for an anonymous caller,
return minimal capabilities) is legitimate business logic, not a hand-rolled guard.

Allowlist is empty: every business-logic identity guard must route through the helpers.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [
    REPO_ROOT / "src" / "core" / "tools",
    REPO_ROOT / "src" / "core" / "helpers",
    REPO_ROOT / "src" / "adapters",
]

# (relative_path, enclosing_function_name) — empty: migrate every site to the helpers.
KNOWN_VIOLATIONS: set[tuple[str, str]] = set()

# Targets whose ``X is None`` check belongs in require_identity / require_principal_id /
# require_tenant rather than a hand-rolled guard.
_IDENTITY_TARGETS = frozenset({"identity", "identity.principal_id", "identity.tenant_id", "principal_id", "tenant_id"})


def _is_x_is_none(test: ast.expr) -> bool:
    """True if ``test`` is ``<target> is None`` for an identity-ish target."""
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Is):
        return False
    if len(test.comparators) != 1:
        return False
    comp = test.comparators[0]
    if not (isinstance(comp, ast.Constant) and comp.value is None):
        return False
    return ast.unparse(test.left) in _IDENTITY_TARGETS


def _is_assert_identity_not_none(node: ast.AST) -> bool:
    """True if ``node`` is ``assert identity is not None`` (any message)."""
    if not isinstance(node, ast.Assert) or not isinstance(node.test, ast.Compare):
        return False
    t = node.test
    if len(t.ops) != 1 or not isinstance(t.ops[0], ast.IsNot) or len(t.comparators) != 1:
        return False
    comp = t.comparators[0]
    return ast.unparse(t.left) == "identity" and isinstance(comp, ast.Constant) and comp.value is None


def _body_raises(body: list[ast.stmt]) -> bool:
    """True if the statement list contains a direct ``raise`` — the hand-rolled-guard tell."""
    return any(isinstance(stmt, ast.Raise) for stmt in body)


def _if_identity_guard_kind(node: ast.AST) -> str | None:
    """Return a label if ``node`` is an ``if <identity> is None``-style guard that RAISES.

    Only raising guards are flagged: ``require_identity`` / ``require_principal_id`` /
    ``require_tenant`` exist to replace the hand-rolled *raise*. A non-raising
    ``if principal_id is None`` branch (anonymous-caller graceful degradation —
    stripping pricing, returning minimal capabilities) is legitimate and out of scope.
    """
    if not isinstance(node, ast.If) or not _body_raises(node.body):
        return None
    test = node.test
    compares = test.values if (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or)) else [test]
    if compares and all(_is_x_is_none(c) for c in compares):
        return "if " + " or ".join(f"{ast.unparse(c.left)} is None" for c in compares)  # type: ignore[union-attr]
    return None


def _scan_file(py_file: Path) -> list[tuple[str, int, str]]:
    """Return (enclosing_function, lineno, kind) for hand-rolled identity guards."""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except (OSError, SyntaxError):
        return []

    hits: list[tuple[str, int, str]] = []

    def walk(node: ast.AST, func: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, child.name)
                continue
            lineno = getattr(child, "lineno", 0)
            # assert identity is not None — flagged in any function
            if func is not None and _is_assert_identity_not_none(child):
                hits.append((func, lineno, "assert identity is not None"))
            # bare/or-chain identity None-guard — flagged only inside *_impl functions
            elif func is not None and func.endswith("_impl"):
                kind = _if_identity_guard_kind(child)
                if kind is not None:
                    hits.append((func, lineno, kind))
            walk(child, func)

    walk(tree, None)
    return hits


def _find_violations() -> list[tuple[str, str, int, str]]:
    """Return (relative_path, function, lineno, kind) across the scan dirs."""
    out: list[tuple[str, str, int, str]] = []
    for scan_dir in SCAN_DIRS:
        for py_file in sorted(scan_dir.rglob("*.py")):
            rel = str(py_file.relative_to(REPO_ROOT))
            for func, lineno, kind in _scan_file(py_file):
                out.append((rel, func, lineno, kind))
    return out


class TestNoHandrolledIdentityGuard:
    """Business-logic identity guards must use require_identity / require_principal_id / require_tenant."""

    def test_no_handrolled_identity_guards(self):
        new = [
            f"  {rel}:{lineno} in {func}()  [{kind}]"
            for rel, func, lineno, kind in _find_violations()
            if (rel, func) not in KNOWN_VIOLATIONS
        ]
        assert not new, (
            f"Found {len(new)} hand-rolled identity guard(s). Replace with the typed helpers in "
            "src.core.auth (require_identity / require_principal_id / require_tenant):\n" + "\n".join(new)
        )

    def test_known_violations_not_stale(self):
        actual = {(rel, func) for rel, func, _, _ in _find_violations()}
        stale = KNOWN_VIOLATIONS - actual
        assert not stale, "Stale allowlist entries (guard migrated or moved):\n" + "\n".join(
            f"  {rel} :: {func}" for rel, func in sorted(stale)
        )
