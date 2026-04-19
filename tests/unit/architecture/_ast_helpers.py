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
from collections.abc import Iterator
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


__all__ = [
    "ALLOWLIST_DIR",
    "FIXTURES_DIR",
    "REPO_ROOT",
    "SCRIPTS",
    "SRC",
    "TESTS",
    "iter_python_files",
    "read_allowlist",
    "relpath",
    "walk_py_files",
]
