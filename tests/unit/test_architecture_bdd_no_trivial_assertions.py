"""Guard: BDD Then steps must make meaningful assertions, not just truthiness checks.

A Then step that only does ``assert ctx.get("response")`` checks existence but
not correctness. Meaningful assertions use comparisons (==, !=, is, in),
isinstance checks, or call helper functions that contain real assertions.

**Rule**: Every @then step must contain at least one "meaningful" assertion:
  - ``assert x == y`` / ``assert x != y`` / ``assert x is y`` / ``assert x is not y``
  - ``assert x in y`` / ``assert x not in y``
  - ``assert isinstance(x, Y)``
  - ``assert len(x) == n`` (comparison, not bare ``assert len(x)``)
  - Or delegate to a helper function (function call in body)

A bare ``assert expr`` without a comparison operator is "trivial" — it only
checks truthiness, not a specific expected value.

beads: beads-y9k
"""

from __future__ import annotations

import ast

from tests.unit._bdd_guard_helpers import scan_then_steps_for_violations


def _assert_is_meaningful(assert_node: ast.Assert) -> bool:
    """Check if an assert statement makes a meaningful comparison.

    Meaningful: assert x == y, assert isinstance(...), assert x in y
    Trivial: assert x, assert ctx.get("foo"), assert len(items)
    """
    test = assert_node.test

    # Compare: assert x == y, assert x != y, etc.
    if isinstance(test, ast.Compare):
        return True

    # isinstance/issubclass call
    if isinstance(test, ast.Call):
        func = test.func
        if isinstance(func, ast.Name) and func.id in ("isinstance", "issubclass", "hasattr"):
            return True

    # UnaryOp: assert not x — check inner
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        # assert not x is trivial, but assert not isinstance(...) is odd.
        # Treat as trivial unless the operand is a Compare.
        if isinstance(test.operand, ast.Compare):
            return True

    # BoolOp: assert x and y — meaningful if ANY operand is meaningful
    if isinstance(test, ast.BoolOp):
        for value in test.values:
            fake_assert = ast.Assert(test=value, msg=None)
            if _assert_is_meaningful(fake_assert):
                return True

    return False


def _has_meaningful_assertion_or_delegation(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function has at least one meaningful assertion or delegates to a helper."""
    has_any_assert = False

    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            has_any_assert = True
            if _assert_is_meaningful(node):
                return True
        # Function call (delegation to helper) counts as meaningful
        if isinstance(node, ast.Call):
            func_node = node.func
            # Exclude ctx.get(), ctx.setdefault() etc — these are data access, not assertions
            if isinstance(func_node, ast.Attribute) and isinstance(func_node.value, ast.Name):
                if func_node.value.id == "ctx":
                    continue
            # Exclude getattr, str, type, etc — builtins used for data extraction
            if isinstance(func_node, ast.Name) and func_node.id in (
                "getattr",
                "str",
                "type",
                "len",
                "list",
                "dict",
                "set",
                "print",
            ):
                continue
            return True
        # Raise counts as meaningful (explicit failure path)
        if isinstance(node, ast.Raise):
            return True

    # If we found asserts but none were meaningful, it's trivial
    if has_any_assert:
        return False

    # No asserts and no delegation — trivial
    return False


def _not_meaningful(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Invert _has_meaningful_assertion_or_delegation for use as detector."""
    return not _has_meaningful_assertion_or_delegation(func)


class TestBddNoTrivialAssertions:
    """Structural guard: Then steps must make meaningful assertions."""

    def test_no_trivial_then_assertions(self):
        """Every @then step must assert a comparison, type check, or delegate to a helper.

        Bare truthiness checks (``assert ctx.get("response")``) don't verify
        correctness — they only verify existence.
        """
        violations = scan_then_steps_for_violations(_not_meaningful)
        assert not violations, (
            f"Found {len(violations)} Then step(s) with only trivial assertions:\n"
            + "\n".join(f"  {v}" for v in violations)
            + "\n\nEach step needs at least one comparison (==, !=, is, in), "
            "isinstance check, or delegation to a helper function."
        )
