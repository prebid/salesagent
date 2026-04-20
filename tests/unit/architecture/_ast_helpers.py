"""Shared AST-walking helpers for structural-guard tests.

These utilities back the L0-01 batch of structural guards landed under
`tests/unit/architecture/`. Guards that need to iterate over source files,
parse ASTs, or read allowlists should import from here rather than copy
the boilerplate.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
and the DRY invariant in `CLAUDE.md`.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"
TESTS = REPO_ROOT / "tests"
SCRIPTS = REPO_ROOT / "scripts"
ALLOWLIST_DIR = Path(__file__).resolve().parent / "allowlists"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def iter_python_files(roots: list[Path]) -> list[Path]:
    """Yield every .py file under the supplied roots, skipping __pycache__."""
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            files.append(p)
    return files


def walk_py_files(roots: list[Path]) -> Iterator[tuple[Path, ast.AST]]:
    """Yield ``(path, ast_tree)`` for every .py file under the supplied roots.

    Files that fail to parse are silently skipped. Callers that care about
    parse errors should open the file themselves.
    """
    for path in iter_python_files(roots):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        yield path, tree


def relpath(path: Path) -> str:
    """Return ``path`` relative to the repository root in POSIX form."""
    return path.relative_to(REPO_ROOT).as_posix()


def read_allowlist(name: str) -> frozenset[str]:
    """Read ``tests/unit/architecture/allowlists/<name>``.

    One entry per line. Blank lines and lines starting with ``#`` are ignored.
    Returns an empty frozenset if the file does not exist, so missing-file
    and empty-file are indistinguishable at the API level. Guards that require
    the file to exist should assert it separately.
    """
    path = ALLOWLIST_DIR / name
    if not path.exists():
        return frozenset()
    entries: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        entries.add(line)
    return frozenset(entries)


def find_stale_allowlist_entries(
    allowlist_name: str,
    still_violates: Callable[[Path], bool],
    removal_reason: str,
) -> list[str]:
    """Return allowlist entries whose target files no longer violate the guard.

    Shared implementation for the ``test_allowlist_shrinks_never_grows`` meta-tests
    across Captured→shrink guards. Each guard supplies a ``still_violates`` predicate
    that checks whether the file at ``path`` still exhibits the pattern the guard
    forbids. Entries pointing at missing files, or at files that no longer violate,
    are returned as stale-and-must-be-removed — the allowlist can only shrink.

    ``removal_reason`` is appended to stale entries whose file still exists so the
    caller's assertion message tells the developer WHY the entry is stale (e.g.
    "no longer imports flask" vs "no longer uses os.environ.get").

    Args:
        allowlist_name: Filename under ``ALLOWLIST_DIR`` (e.g. ``"no_flask_imports.txt"``).
        still_violates: Predicate ``(path: Path) -> bool`` — True iff the file still
            exhibits the forbidden pattern. Receives absolute paths.
        removal_reason: Short phrase describing why an entry is stale when the file
            exists but no longer violates (e.g. ``"no longer imports flask"``).

    Returns:
        List of human-readable stale descriptions. Empty if the allowlist is clean.
    """
    allowlist = read_allowlist(allowlist_name)
    stale: list[str] = []
    for rel in allowlist:
        path = REPO_ROOT / rel
        if not path.exists():
            stale.append(f"{rel} (file does not exist)")
            continue
        if not still_violates(path):
            stale.append(f"{rel} ({removal_reason} — remove from allowlist)")
    return stale


def call_func_name(node: ast.Call) -> str | None:
    """Return the plain or attribute name of an ``ast.Call``'s target, or None.

    For ``foo()`` returns ``"foo"``; for ``bar.foo()`` returns ``"foo"``;
    for more exotic expression targets (e.g. ``(a or b)()``) returns None.
    Shared helper across structural guards that filter calls by name.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def iter_route_decorator_calls(tree: ast.AST) -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.Call]]:
    """Yield ``(handler_node, decorator_call)`` for every FastAPI route decorator.

    Shared implementation for admin-router guards that inspect route registration
    (e.g. ``test_architecture_admin_routes_named``, ``test_architecture_admin_route_names_unique``).
    Yields only ``ast.Call`` decorators (``@router.get(...)``) — bare-name decorators
    (``@router.get`` without parens) and non-route decorators (``@property``,
    ``@classmethod``) are skipped.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            yield node, deco


__all__ = [
    "ALLOWLIST_DIR",
    "FIXTURES_DIR",
    "REPO_ROOT",
    "SCRIPTS",
    "SRC",
    "TESTS",
    "call_func_name",
    "find_stale_allowlist_entries",
    "iter_python_files",
    "iter_route_decorator_calls",
    "read_allowlist",
    "relpath",
    "walk_py_files",
]
