"""Structural guard: admin route handlers are sync ``def``, not ``async def``.

Per Flask→FastAPI v2.0 migration invariant #4 (CLAUDE.md):

> Layers 0-4: Admin handlers use **sync `def`** with sync SQLAlchemy.
> FastAPI runs sync handlers in its AnyIO threadpool; because there is no
> thread-local session registry, thread reuse cannot leak session state
> between requests.

This guard scans ``src/admin/routers/`` for module-scope ``async def`` with a
public name (not starting with ``_``). Private async helpers (e.g. an
internal webhook dispatcher that lives inside a router module) are allowed;
what is forbidden is an async *handler* exposed as a route.

A small FROZEN carve-out covers the authorization-code-exchange callbacks
that MUST remain async because the OAuth libraries require an awaitable
context. Today the set is empty — all current handlers are either sync
(Flask) or still private helpers.

Meta-guard: planted fixture file with a public async function must trip the
scanner.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
row #2 of the §5.5 Structural Guards Inventory.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    SRC,
    iter_python_files,
    relpath,
)

FIXTURE = FIXTURES_DIR / "test_handlers_use_sync_def_meta_fixture.py.txt"

# FROZEN carve-out: handlers whose underlying library demands ``async def``.
# Format: "relative/path.py::function_name". MUST stay small and each entry
# MUST have a code comment at the source site explaining why async is
# required. Empty at L0 — no async handlers exist yet.
OAUTH_ASYNC_HANDLERS_FROZEN: frozenset[str] = frozenset()

ADMIN_ROOTS = [SRC / "admin" / "routers"]


def _find_public_async_functions(tree: ast.AST) -> list[str]:
    """Return names of module-scope ``async def`` functions whose name does not start with ``_``."""
    names: list[str] = []
    if not isinstance(tree, ast.Module):
        return names
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and not node.name.startswith("_"):
            names.append(node.name)
    return names


def _scan_file(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    return _find_public_async_functions(tree)


def test_admin_handlers_are_sync_def() -> None:
    """No public ``async def`` at module scope in admin router files (minus frozen carve-out)."""
    violations: list[str] = []
    for path in iter_python_files(ADMIN_ROOTS):
        for name in _scan_file(path):
            key = f"{relpath(path)}::{name}"
            if key in OAUTH_ASYNC_HANDLERS_FROZEN:
                continue
            violations.append(key)
    assert not violations, (
        "Admin handlers must be sync `def`. FastAPI runs sync handlers in its "
        "AnyIO threadpool; each `with get_db_session()` yields a fresh Session "
        "from a bare sessionmaker (migration Decision D2). Offending async "
        "handlers:\n" + "\n".join(f"  - {v}" for v in sorted(violations))
    )


def test_frozen_carveout_entries_exist() -> None:
    """Every entry in OAUTH_ASYNC_HANDLERS_FROZEN must resolve to a real async def."""
    stale: list[str] = []
    for key in OAUTH_ASYNC_HANDLERS_FROZEN:
        rel, _, name = key.partition("::")
        path = SRC.parent.parent / rel if not rel.startswith("src/") else SRC.parent / rel[len("src/") :]
        if not path.exists():
            stale.append(f"{key} (file does not exist)")
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            stale.append(f"{key} (parse error)")
            continue
        found = any(
            isinstance(n, ast.AsyncFunctionDef) and n.name == name
            for n in (tree.body if isinstance(tree, ast.Module) else [])
        )
        if not found:
            stale.append(f"{key} (no async def with that name)")
    assert not stale, "Stale OAUTH_ASYNC_HANDLERS_FROZEN entries:\n" + "\n".join(f"  - {s}" for s in stale)


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    names = _find_public_async_functions(tree)
    assert names, f"AST scanner FAILED to detect the public async handler in {FIXTURE.name}. The guard is broken."


@pytest.mark.parametrize(
    "snippet,expected",
    [
        ("async def list_accounts(): pass\n", ["list_accounts"]),
        ("async def get_tenant(): pass\nasync def _helper(): pass\n", ["get_tenant"]),
        ("def sync_handler(): pass\n", []),
        ("async def _private(): pass\n", []),
        # Nested async def inside a class — NOT module-scope, should be ignored.
        ("class C:\n    async def method(self): pass\n", []),
    ],
)
def test_detector_behavior(snippet: str, expected: list[str]) -> None:
    tree = ast.parse(snippet)
    assert _find_public_async_functions(tree) == expected
