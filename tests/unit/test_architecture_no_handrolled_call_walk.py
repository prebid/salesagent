"""Guard: Call-detection loops must use iter_call_expressions.

Hand-rolled ``for node in ast.walk(...): if not isinstance(node, ast.Call)``
loops duplicate the helper and drift when one copy is updated. All guard
Call-detection loops route through ``tests.unit._architecture_helpers.iter_call_expressions``.

Deliberately out of scope (not flagged by this guard):

- Pre-collected nodes, e.g. ``nodes = list(ast.walk(t))`` then loop over ``nodes``
- A statement before the ``if`` guard in the loop body (not ``body[0]``)
- ``from ast import walk`` instead of ``ast.walk``
- Compound conditions, e.g. ``if isinstance(n, ast.Call) and ...:``
- ``type(n) is not ast.Call`` instead of ``isinstance``
- Dotted ``ast.Call`` references, e.g. ``isinstance(node, mod.ast.Call)``
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import iter_architecture_guard_trees

_EXEMPT = {
    Path("tests/unit/_architecture_helpers.py"),
    Path("tests/unit/test_architecture_helpers_contract.py"),
    Path("tests/unit/test_architecture_no_handrolled_call_walk.py"),
}


def _is_ast_walk_iter(for_node: ast.For) -> bool:
    """True when the For loop iterates ``ast.walk(...)``."""
    iter_node = for_node.iter
    return (
        isinstance(iter_node, ast.Call)
        and isinstance(iter_node.func, ast.Attribute)
        and iter_node.func.attr == "walk"
        and isinstance(iter_node.func.value, ast.Name)
        and iter_node.func.value.id == "ast"
    )


def _for_target_name(for_node: ast.For) -> str | None:
    target = for_node.target
    if isinstance(target, ast.Name):
        return target.id
    return None


def _is_continue_only(body: list[ast.stmt]) -> bool:
    return len(body) == 1 and isinstance(body[0], ast.Continue)


def _is_handrolled_call_continue(for_node: ast.For) -> bool:
    """True when body starts with ``if not isinstance(<target>, ast.Call): continue``."""
    target_name = _for_target_name(for_node)
    if target_name is None or not for_node.body:
        return False
    first = for_node.body[0]
    if not isinstance(first, ast.If):
        return False
    test = first.test
    if not (
        isinstance(test, ast.UnaryOp)
        and isinstance(test.op, ast.Not)
        and isinstance(test.operand, ast.Call)
        and isinstance(test.operand.func, ast.Name)
        and test.operand.func.id == "isinstance"
        and len(test.operand.args) == 2
        and isinstance(test.operand.args[0], ast.Name)
        and test.operand.args[0].id == target_name
        and isinstance(test.operand.args[1], ast.Attribute)
        and isinstance(test.operand.args[1].value, ast.Name)
        and test.operand.args[1].value.id == "ast"
        and test.operand.args[1].attr == "Call"
    ):
        return False
    return _is_continue_only(first.body)


def _is_handrolled_call_positive(for_node: ast.For) -> bool:
    """True when body starts with ``if isinstance(<target>, ast.Call):`` and processes it."""
    target_name = _for_target_name(for_node)
    if target_name is None or not for_node.body:
        return False
    first = for_node.body[0]
    if not isinstance(first, ast.If):
        return False
    test = first.test
    if not (
        isinstance(test, ast.Call)
        and isinstance(test.func, ast.Name)
        and test.func.id == "isinstance"
        and len(test.args) == 2
        and isinstance(test.args[0], ast.Name)
        and test.args[0].id == target_name
        and isinstance(test.args[1], ast.Attribute)
        and isinstance(test.args[1].value, ast.Name)
        and test.args[1].value.id == "ast"
        and test.args[1].attr == "Call"
    ):
        return False
    if _is_continue_only(first.orelse):
        return False
    if not first.body or (len(first.body) == 1 and isinstance(first.body[0], ast.Pass)):
        return False
    return True


def _for_is_handrolled_call_walk(for_node: ast.For) -> bool:
    if not _is_ast_walk_iter(for_node):
        return False
    return _is_handrolled_call_continue(for_node) or _is_handrolled_call_positive(for_node)


def _for_nodes_from_source(source: str) -> list[ast.For]:
    tree = ast.parse(source)
    return [node for node in ast.walk(tree) if isinstance(node, ast.For)]


def _find_handrolled_call_walks() -> list[str]:
    violations: list[str] = []
    for tree, rel in iter_architecture_guard_trees(exempt=_EXEMPT):
        for node in ast.walk(tree):
            if isinstance(node, ast.For) and _for_is_handrolled_call_walk(node):
                lineno = getattr(node, "lineno", "?")
                violations.append(f"{rel}:{lineno}: hand-rolled ast.walk Call loop")
    return violations


@pytest.mark.arch_guard
def test_no_handrolled_call_walk() -> None:
    """Guard tests must not inline ast.walk + isinstance(ast.Call) Call-detection loops."""
    violations = _find_handrolled_call_walks()
    assert not violations, "Hand-rolled Call walk found — use iter_call_expressions():\n" + "\n".join(
        f"  {v}" for v in violations
    )


@pytest.mark.arch_guard
def test_call_walk_guard_catches_negative_continue_pattern() -> None:
    """Self-test: the scanner flags ``if not isinstance(node, ast.Call): continue``."""
    for_nodes = _for_nodes_from_source(
        "import ast\n"
        "tree = ast.parse('x()')\n"
        "for node in ast.walk(tree):\n"
        "    if not isinstance(node, ast.Call):\n"
        "        continue\n"
    )
    assert any(_for_is_handrolled_call_walk(node) for node in for_nodes)


@pytest.mark.arch_guard
def test_call_walk_guard_catches_positive_isinstance_pattern() -> None:
    """Self-test: the scanner flags ``if isinstance(node, ast.Call):`` processing."""
    for_nodes = _for_nodes_from_source(
        "import ast\n"
        "tree = ast.parse('x()')\n"
        "for node in ast.walk(tree):\n"
        "    if isinstance(node, ast.Call):\n"
        "        _ = node.func\n"
    )
    assert any(_for_is_handrolled_call_walk(node) for node in for_nodes)


@pytest.mark.arch_guard
def test_call_walk_guard_allows_iter_call_expressions() -> None:
    """Self-test: iter_call_expressions() is not flagged."""
    for_nodes = _for_nodes_from_source(
        "import ast\n"
        "from tests.unit._architecture_helpers import iter_call_expressions\n"
        "tree = ast.parse('x()')\n"
        "for node in iter_call_expressions(tree):\n"
        "    _ = node.func\n"
    )
    assert not any(_for_is_handrolled_call_walk(node) for node in for_nodes)


@pytest.mark.arch_guard
def test_call_walk_guard_allows_out_of_scope_shapes() -> None:
    """Self-test: documented out-of-scope shapes are not flagged (empty scan != total coverage)."""
    out_of_scope_sources = [
        "import ast\n"
        "tree = ast.parse('x()')\n"
        "nodes = list(ast.walk(tree))\n"
        "for node in nodes:\n"
        "    if not isinstance(node, ast.Call):\n"
        "        continue\n",
        "import ast\n"
        "tree = ast.parse('x()')\n"
        "for node in ast.walk(tree):\n"
        "    _ = node\n"
        "    if not isinstance(node, ast.Call):\n"
        "        continue\n",
        "import ast\n"
        "tree = ast.parse('x()')\n"
        "for node in ast.walk(tree):\n"
        "    if type(node) is not ast.Call:\n"
        "        continue\n",
        "import ast\n"
        "tree = ast.parse('x()')\n"
        "for node in ast.walk(tree):\n"
        "    if not isinstance(node, mod.ast.Call):\n"
        "        continue\n",
    ]
    for source in out_of_scope_sources:
        for_nodes = _for_nodes_from_source(source)
        assert not any(_for_is_handrolled_call_walk(node) for node in for_nodes)
