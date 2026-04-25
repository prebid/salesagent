"""Guard: BDD step functions must not have empty or placeholder bodies.

Then steps with ``pass``, no statements (only docstring), or placeholder
delegation like ``_pending(...)`` claim to verify behavior but assert nothing.
Given/When steps with empty bodies promise data setup or actions but deliver
nothing.

Scanning approach: AST — find functions decorated with ``@given/@when/@then``
in ``tests/bdd/steps/`` and check that the body contains at least one statement
beyond the docstring.

beads: beads-5rt
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Literal

_BDD_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"

# Allowlist for empty Given/When steps. Must only shrink — never add entries.
# Fixed in #1181: given_tenant_exists and given_account_not_exists now have real bodies.
_EMPTY_GIVEN_WHEN_ALLOWLIST: set[tuple[str, str]] = set()


def _is_decorated_with(func: ast.FunctionDef | ast.AsyncFunctionDef, decorator_name: str) -> bool:
    """Check if function is decorated with @<decorator_name>(...)."""
    for dec in func.decorator_list:
        if isinstance(dec, ast.Call):
            func_node = dec.func
            if isinstance(func_node, ast.Name) and func_node.id == decorator_name:
                return True
        if isinstance(dec, ast.Name) and dec.id == decorator_name:
            return True
    return False


def _body_is_empty(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function body is effectively empty (pass, ellipsis, or docstring-only)."""
    stmts = func.body
    effective = []
    for i, stmt in enumerate(stmts):
        if (
            i == 0
            and isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            continue  # skip docstring
        effective.append(stmt)

    if not effective:
        return True
    if len(effective) == 1:
        s = effective[0]
        if isinstance(s, ast.Pass):
            return True
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is ...:
            return True
    return False


def _body_has_assert_or_call(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function body contains an assert statement or a function call."""
    for node in ast.walk(func):
        if isinstance(node, (ast.Assert, ast.Call, ast.Raise)):
            return True
    return False


def _contains_placeholder_call(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if the function delegates to a known placeholder helper like _pending()."""
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "_pending":
            return True
    return False


StepKind = Literal["given", "when", "then"]


def _iter_step_functions(
    decorator_names: set[StepKind],
) -> list[tuple[str, str, int, StepKind, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Yield (relative_path, func_name, lineno, decorator, func_node) for matching steps."""
    results = []
    for py_file in sorted(_BDD_STEPS_DIR.rglob("*.py")):
        if py_file.name.startswith("_"):
            continue
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        relative = str(py_file.relative_to(_BDD_STEPS_DIR.parent.parent))

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec_name in decorator_names:
                if _is_decorated_with(node, dec_name):
                    results.append((relative, node.name, node.lineno, dec_name, node))
    return results


class TestBddNoPassSteps:
    """Structural guard: BDD steps must have meaningful bodies."""

    def test_no_empty_then_steps(self):
        """Every @then step must contain an assert, function call, or raise."""
        violations = []
        for rel, name, lineno, _, func in _iter_step_functions({"then"}):
            if _body_is_empty(func):
                violations.append(f"{rel}:{lineno} {name} — empty body (pass/docstring-only)")
            elif _contains_placeholder_call(func):
                violations.append(f"{rel}:{lineno} {name} — placeholder delegation (_pending) is not a real assertion")
            elif not _body_has_assert_or_call(func):
                violations.append(f"{rel}:{lineno} {name} — no assert or function call")

        assert not violations, f"Found {len(violations)} Then step(s) with empty/assertion-free bodies:\n" + "\n".join(
            f"  {v}" for v in violations
        )

    def test_no_placeholder_given_when_steps(self):
        """Given/When steps must not delegate to placeholder helpers like _pending()."""
        violations = []
        for rel, name, lineno, dec_name, func in _iter_step_functions({"given", "when"}):
            if _contains_placeholder_call(func):
                violations.append(f"{rel}:{lineno} @{dec_name} {name} — placeholder delegation (_pending)")

        assert not violations, (
            f"Found {len(violations)} Given/When step(s) using placeholder delegation:\n"
            + "\n".join(f"  {v}" for v in violations)
            + "\n\nFix: implement the step with real setup/action logic."
        )

    def test_no_empty_given_when_steps(self):
        """Every @given/@when step must have a non-empty body.

        A Given step that says 'a tenant with products configured' must actually
        create a tenant with products. An empty body means the step text is lying.
        """
        violations = []
        for rel, name, lineno, dec_name, func in _iter_step_functions({"given", "when"}):
            if _body_is_empty(func) and (rel, name) not in _EMPTY_GIVEN_WHEN_ALLOWLIST:
                violations.append(f"{rel}:{lineno} @{dec_name} {name} — empty body")

        assert not violations, (
            f"Found {len(violations)} Given/When step(s) with empty bodies:\n"
            + "\n".join(f"  {v}" for v in violations)
            + "\n\nFix: implement the step, or add to _EMPTY_GIVEN_WHEN_ALLOWLIST with FIXME."
        )

    def test_empty_given_when_allowlist_not_stale(self):
        """Allowlisted empty Given/When steps must still be empty.

        When someone fixes an allowlisted step, this test reminds them to remove
        it from the allowlist.
        """
        stale = []
        for rel, name, lineno, _, func in _iter_step_functions({"given", "when"}):
            if (rel, name) in _EMPTY_GIVEN_WHEN_ALLOWLIST and not _body_is_empty(func):
                stale.append(f"{rel}:{lineno} {name}")

        assert not stale, (
            f"Found {len(stale)} allowlisted step(s) that are no longer empty — remove from allowlist:\n"
            + "\n".join(f"  {v}" for v in stale)
        )
