"""Guard: ``_NESTED_PYTEST_MODULES`` names exactly the modules that start a nested pytest session.

``tests/conftest.py`` puts every module in that set into one ``xdist_group``, so under the
unit suite's ``--dist loadgroup`` at most one nested session runs at a time. A spawner left
out runs one nested session per worker that picks up its items; at ``-n 16`` that killed
the unit leg (the history is in the comment above the set). A module left in after it
stopped spawning pins its items to one worker for nothing.

The set was hand-maintained and drifted both ways, so it is DERIVED here from the modules
the unit env runs (``tests/unit`` and ``tests/harness``, the paths ``[testenv:unit]``
names). A module starts a nested session when it calls

* ``subprocess.<run|Popen|check_output|check_call|call>`` with a ``"pytest"`` argument;
* ``pytest.main`` outside an ``if __name__ == "__main__":`` block;
* a function it imports from another ``tests`` module that does either of the above.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.conftest import _NESTED_PYTEST_MODULES
from tests.unit._architecture_helpers import assert_violations_match_allowlist, parse_module, repo_root

_UNIT_ENV_PATHS = ("tests/unit", "tests/harness")
_SPAWN_CALLS = frozenset({"run", "Popen", "check_output", "check_call", "call"})


def _is_main_guard(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    )


def _starts_pytest(call: ast.Call, *, under_main_guard: bool) -> bool:
    func = call.func
    if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
        return False
    if func.value.id == "subprocess" and func.attr in _SPAWN_CALLS:
        return any(isinstance(n, ast.Constant) and n.value == "pytest" for arg in call.args for n in ast.walk(arg))
    return func.value.id == "pytest" and func.attr == "main" and not under_main_guard


def _spawns(tree: ast.AST) -> bool:
    """Whether *tree* contains a call that starts a nested pytest session."""
    stack: list[tuple[ast.AST, bool]] = [(tree, False)]
    while stack:
        node, guarded = stack.pop()
        guarded = guarded or _is_main_guard(node)
        if isinstance(node, ast.Call) and _starts_pytest(node, under_main_guard=guarded):
            return True
        stack.extend((child, guarded) for child in ast.iter_child_nodes(node))
    return False


def _imported_spawners(tree: ast.Module, repo: Path) -> set[str]:
    """Names this module imports from a ``tests`` module whose definition of that name spawns."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("tests.")):
            continue
        source = repo / Path(*node.module.split(".")).with_suffix(".py")
        if not source.is_file():
            continue
        definitions = {
            fn.name: fn for fn in parse_module(source).body if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        found |= {alias.name for alias in node.names if alias.name in definitions and _spawns(definitions[alias.name])}
    return found


def nested_pytest_modules(repo: Path) -> set[str]:
    """The basenames of the unit-env modules that start a nested pytest session."""
    modules = set()
    for directory in _UNIT_ENV_PATHS:
        for path in sorted((repo / directory).glob("test_*.py")):
            tree = parse_module(path)
            if _spawns(tree) or _imported_spawners(tree, repo):
                modules.add(path.stem)
    return modules


@pytest.mark.arch_guard
def test_the_nested_pytest_group_names_exactly_the_spawning_modules() -> None:
    found = nested_pytest_modules(repo_root())
    # A derivation that matches nothing would declare an empty set correct.
    assert found, "the derivation found no spawning module; it is grading nothing"
    assert_violations_match_allowlist(
        {(name,) for name in found},
        {(name,) for name in _NESTED_PYTEST_MODULES},
        fix_hint=(
            "Add a spawning module to _NESTED_PYTEST_MODULES in tests/conftest.py, or stop it "
            "spawning (read the run's collection record from tests/collection/ instead). "
            "Remove an entry whose module no longer spawns."
        ),
    )


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    ("source", "spawns"),
    [
        ('import subprocess, sys\nsubprocess.run([sys.executable, "-m", "pytest", "x"])\n', True),
        ('import subprocess\nsubprocess.check_output(["pytest", "--collect-only"])\n', True),
        ("import pytest\npytest.main(['x'])\n", True),
        ("import pytest\nif __name__ == '__main__':\n    pytest.main([__file__])\n", False),
        ('import subprocess\nsubprocess.run(["bash", "run_all_tests.sh"])\n', False),
    ],
)
def test_the_derivation_tells_a_spawn_from_its_lookalikes(source: str, spawns: bool) -> None:
    assert _spawns(ast.parse(source)) is spawns
