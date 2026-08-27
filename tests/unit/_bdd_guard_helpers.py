"""Shared helpers for BDD structural guard tests.

Extracts common AST scanning patterns used by multiple guard test files
to avoid code duplication (DRY invariant).

Every BDD guard that needs to walk ``tests/bdd/steps/`` and pick out
``@given`` / ``@when`` / ``@then`` functions goes through :func:`iter_bdd_steps`.
Guards keep their OWN detector (what counts as a violation); only the
find-the-step-functions machinery is shared.
"""

from __future__ import annotations

import ast
from collections.abc import Collection, Iterable, Iterator
from pathlib import Path
from typing import NamedTuple

BDD_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"

#: pytest-bdd step decorators, in the order guards report them.
STEP_DECORATORS: tuple[str, ...] = ("given", "when", "then")

_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def decorator_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Bare decorator names on *func*, covering ``@x`` and ``@x(...)`` alike.

    Dotted decorators (``@pytest.mark.foo``) contribute nothing — pytest-bdd
    steps are always applied as bare names.
    """
    names: set[str] = set()
    for dec in func.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            names.add(target.id)
    return names


def is_step_decorated(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    step_names: Collection[str] = STEP_DECORATORS,
) -> bool:
    """True when *func* carries any of *step_names* as a decorator."""
    return bool(decorator_names(func) & set(step_names))


def is_then_decorated(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function is decorated with @then(...)."""
    return is_step_decorated(func, ("then",))


class BddStep(NamedTuple):
    """One step function found under ``tests/bdd/steps/``."""

    relative: str
    """Repo-relative-ish path, e.g. ``bdd/steps/domain/uc003_x.py``."""

    name: str
    lineno: int
    node: ast.FunctionDef | ast.AsyncFunctionDef

    decorators: tuple[str, ...]
    """Requested step decorators this function actually carries, in caller order."""

    @property
    def key(self) -> str:
        """``path:lineno name`` — the location format guard failures print."""
        return f"{self.relative}:{self.lineno} {self.name}"


def _candidate_files(files: Iterable[Path] | None) -> Iterator[Path]:
    """Python files to scan: the whole steps tree, or an explicit subset."""
    if files is None:
        for py_file in sorted(BDD_STEPS_DIR.rglob("*.py")):
            if py_file.name.startswith("_"):
                continue
            yield py_file
        return
    for py_file in files:
        if py_file.exists():
            yield py_file


def iter_bdd_steps(
    *,
    step_names: Collection[str] = STEP_DECORATORS,
    files: Iterable[Path] | None = None,
) -> Iterator[BddStep]:
    """Yield every step function under ``tests/bdd/steps/`` matching *step_names*.

    *files* overrides the default recursive scan with an explicit list (missing
    paths are skipped). Nested function definitions are reached too — the walk
    is ``ast.walk``, not a body scan.
    """
    wanted = tuple(dict.fromkeys(step_names))
    for py_file in _candidate_files(files):
        tree = ast.parse(py_file.read_text(), filename=str(py_file))
        relative = str(py_file.relative_to(BDD_STEPS_DIR.parent.parent))

        for node in ast.walk(tree):
            if not isinstance(node, _FUNCTION_NODES):
                continue
            present = decorator_names(node)
            matched = tuple(name for name in wanted if name in present)
            if not matched:
                continue
            yield BddStep(relative, node.name, node.lineno, node, matched)


def iter_then_functions() -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Return (relative_path:lineno func_name, func_node) for all Then steps."""
    return [(step.key, step.node) for step in iter_bdd_steps(step_names=("then",))]


def scan_then_steps_for_violations(
    detector: object,
) -> list[str]:
    """Run a detector across all Then steps, return violation keys."""
    violations = []
    for key, func in iter_then_functions():
        if detector(func):  # type: ignore[operator]
            violations.append(key)
    return violations
