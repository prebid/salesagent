"""Structural guard: no DB engine / session construction at module top level.

Agent F action F6.2.6 (MUST, pre-L0) — mitigation for Risk #33 of the
async-audit risk matrix.

Rationale:
    Calling `get_engine()`, `get_db_session()`, or `create_async_engine()`
    at a module's top level runs that call at import time. Import time is
    the WORST time to touch the database:

    1. It happens once, before `app.state` / lifespan setup — so the
       resulting engine/session is bound to the ambient state at import
       and cannot be swapped for tests or lifespan-managed pools.
    2. Under `subprocess.Popen` fork (as in scripts/deploy/run_all_services.py),
       a module-level engine leaks its pooled sockets into the child. This
       is the same fork-safety bug Decision 2 mitigates at the raw-psycopg2
       layer — the async engine has the exact same problem (Risk #33).
    3. It makes import ordering load-bearing: any `import src.X` in a test
       can spontaneously open a DB connection.

    All DB engine/session construction must live inside a function body,
    lifespan hook, dependency, or class method — NEVER at module scope.

What this guard checks:
    For every `.py` file under `src/`, walks the module-level AST nodes
    (top-of-file `ast.Module.body`) and fails if any contain a direct call
    to `get_engine`, `get_db_session`, or `create_async_engine` (matched by
    bare `ast.Name` OR `ast.Attribute` form).

    The check is strictly "top-level" — calls inside functions, classes,
    methods, and nested constructs are ignored. `if __name__ == "__main__"`
    guards are NOT treated as top-level protection (they still execute at
    import if the module is run directly).

Ratcheting rules:
    - Allowlist is empty. No file in `src/` may construct an engine/session
      at module scope. The allowlist exists as a frozenset so that if a
      forced exception appears later, it can be added with a FIXME tag.
    - Stale-entry meta-test: if an allowlisted file becomes clean, the
      entry must be removed.

Meta-tests:
    Synthetic AST snippets prove the detector catches each banned pattern
    AND does not false-positive on clean code (calls inside functions,
    calls on unrelated names).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"

BANNED_CALL_NAMES: frozenset[str] = frozenset(
    {
        "get_engine",
        "get_db_session",
        "create_async_engine",
    }
)

# Empty allowlist — no file may construct engines/sessions at module scope.
ALLOWLIST: frozenset[str] = frozenset()


def _iter_python_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def _is_banned_call(node: ast.AST) -> bool:
    """True iff the node is a Call whose func matches a banned name."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id in BANNED_CALL_NAMES:
        return True
    if isinstance(func, ast.Attribute) and func.attr in BANNED_CALL_NAMES:
        return True
    return False


_FUNC_OR_CLASS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _module_top_level_contains_banned_call(tree: ast.Module) -> bool:
    """Scan ONLY the module's direct top-level statements (no descent into function/class BODIES).

    Top-level statements include:
      - imports, assignments, expressions, if/for/while/with/try blocks at module scope
      - the `if __name__ == "__main__":` guard (also module-scope-executed)
      - decorators on top-level function/class definitions
      - default arg/kwarg values on top-level function definitions
    These ALL run at import time and are in scope.

    Descent stops at the body of any FunctionDef / AsyncFunctionDef / ClassDef —
    calls inside method/function/class bodies are NOT module-level.
    """
    for stmt in tree.body:
        if isinstance(stmt, _FUNC_OR_CLASS):
            # Top-level `def`/`class`: only decorators + default values count as
            # module-executed. The body runs only when the function/class is called.
            for dec in stmt.decorator_list:
                for child in _walk_no_funcs(dec):
                    if _is_banned_call(child):
                        return True
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for default in stmt.args.defaults:
                    for child in _walk_no_funcs(default):
                        if _is_banned_call(child):
                            return True
                for default in stmt.args.kw_defaults:
                    if default is None:
                        continue
                    for child in _walk_no_funcs(default):
                        if _is_banned_call(child):
                            return True
            continue

        # All other top-level statements: fully scan, but don't descend into
        # nested function/class bodies that appear INSIDE them.
        for child in _walk_no_funcs(stmt):
            if _is_banned_call(child):
                return True
    return False


def _walk_no_funcs(node: ast.AST):
    """Yield `node` and descendants, skipping body statements of nested functions/classes.

    Decorators and default argument values on nested defs ARE yielded because
    they execute in the enclosing scope at the time the inner `def` is evaluated
    (which, from the caller's perspective, is whenever the outer statement runs).
    """
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _FUNC_OR_CLASS):
            for dec in child.decorator_list:
                yield from _walk_no_funcs(dec)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for default in child.args.defaults:
                    yield from _walk_no_funcs(default)
                for default in child.args.kw_defaults:
                    if default is not None:
                        yield from _walk_no_funcs(default)
            # Skip body.
            continue
        yield from _walk_no_funcs(child)


def _file_has_module_level_violation(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    return _module_top_level_contains_banned_call(tree)


def _relpath(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def test_no_module_level_engine_construction() -> None:
    """No file in src/ may call get_engine / get_db_session / create_async_engine at module scope."""
    violations = {_relpath(p) for p in _iter_python_files() if _file_has_module_level_violation(p)}
    new_violations = violations - ALLOWLIST
    assert not new_violations, (
        "Module-level DB engine/session construction detected. These calls run "
        "at import time and leak across subprocess forks (Risk #33). Move the "
        "call into a function body, lifespan hook, dependency, or class method. "
        f"Violations: {sorted(new_violations)}"
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Meta-test: allowlist entries must still have violations (starts empty)."""
    current_violations = {_relpath(p) for p in _iter_python_files() if _file_has_module_level_violation(p)}
    stale = ALLOWLIST - current_violations
    assert not stale, (
        "Allowlist contains stale entries — these files no longer construct "
        f"engines at module scope and should be removed from ALLOWLIST: {sorted(stale)}"
    )


@pytest.mark.parametrize(
    "snippet",
    [
        # Top-level bare call, assigned to a module global.
        "from x import get_engine\n_engine = get_engine()\n",
        # Top-level attribute call.
        "import mod\n_engine = mod.get_engine()\n",
        # Top-level session construction.
        "from x import get_db_session\nsession = get_db_session()\n",
        # Top-level async engine construction.
        "from sqlalchemy.ext.asyncio import create_async_engine\nengine = create_async_engine('url')\n",
        # Inside a top-level `if`.
        "from x import get_engine\nif True:\n    e = get_engine()\n",
        # Inside a top-level `with`.
        "from x import get_db_session\nwith get_db_session() as s:\n    x = 1\n",
        # As a decorator argument (runs at module load).
        "from x import get_engine\n@some_decorator(get_engine())\ndef f():\n    pass\n",
        # As a default argument (evaluated at def time = module load time).
        "from x import get_engine\ndef f(engine=get_engine()):\n    return engine\n",
    ],
)
def test_detector_catches_synthetic_violations(snippet: str) -> None:
    """Meta-test: prove the AST scanner catches each top-level form."""
    tree = ast.parse(snippet)
    assert _module_top_level_contains_banned_call(tree), f"Detector missed violation in snippet: {snippet!r}"


@pytest.mark.parametrize(
    "snippet",
    [
        # Call inside a function body — fine.
        "from x import get_engine\ndef f():\n    return get_engine()\n",
        # Call inside a class method — fine.
        "from x import get_engine\nclass C:\n    def m(self):\n        return get_engine()\n",
        # Call inside a nested function — fine.
        "from x import get_engine\ndef outer():\n    def inner():\n        return get_engine()\n    return inner\n",
        # Import only, no call — fine.
        "from x import get_engine\n",
        # Unrelated call at module scope — fine.
        "import os\n_cwd = os.getcwd()\n",
        # Function with similarly-named but NOT banned call.
        "from x import get_user\n_u = get_user()\n",
    ],
)
def test_detector_accepts_clean_snippets(snippet: str) -> None:
    """Meta-test: prove the AST scanner does not false-positive."""
    tree = ast.parse(snippet)
    assert not _module_top_level_contains_banned_call(tree), f"Detector false-positive on: {snippet!r}"
