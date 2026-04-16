"""Structural guard: no scoped_session imports or calls in src/.

Decision D2 (Flask→FastAPI migration) — scoped_session is being deleted from
src/core/database/database_session.py because FastAPI's threadpool makes
thread-local session scoping unnecessary and actively wrong under `async def`
handlers. Remaining call sites are two GAM service modules that still construct
their own module-level scoped_session; they are allowlisted here and scheduled
for deletion in L4 per Decision 7 (ContextManager stateless refactor / Spike 4.5).

Ratcheting rules:
- The allowlist may SHRINK, never grow.
- Each allowlisted file has a `FIXME(salesagent-xxxx)` comment at the violation site.
- The stale-entry meta-test fails if an allowlisted file no longer has a violation.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"

# Allowlist: files that still legitimately import/call scoped_session.
# These are deleted/rewritten in L4 per Decision 7 and the GAM service rewrite.
ALLOWLIST: frozenset[str] = frozenset(
    {
        "services/gam_orders_service.py",  # FIXME(salesagent-b2-a): L4 rewrite
        "services/gam_inventory_service.py",  # FIXME(salesagent-b2-b): L4 rewrite
    }
)


def _iter_python_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def _file_has_scoped_session(path: Path) -> bool:
    """True iff the file imports scoped_session OR calls scoped_session(...)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        # `from sqlalchemy.orm import scoped_session[, ...]`
        if isinstance(node, ast.ImportFrom) and node.module == "sqlalchemy.orm":
            if any(alias.name == "scoped_session" for alias in node.names):
                return True
        # `scoped_session(...)` call
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "scoped_session":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "scoped_session":
                return True
    return False


def _relpath(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def test_no_new_scoped_session_violations() -> None:
    """No new scoped_session usage may be added outside the allowlist."""
    violations = {_relpath(p) for p in _iter_python_files() if _file_has_scoped_session(p)}
    new_violations = violations - ALLOWLIST
    assert not new_violations, (
        "New scoped_session violations found (Decision D2 blocker). scoped_session is "
        "forbidden in src/ — FastAPI's threadpool makes thread-local sessions "
        "unnecessary and actively wrong under `async def` handlers. Remove "
        f"scoped_session from: {sorted(new_violations)}"
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Meta-test: allowlist entries must still have violations (no stale entries)."""
    current_violations = {_relpath(p) for p in _iter_python_files() if _file_has_scoped_session(p)}
    stale = ALLOWLIST - current_violations
    assert not stale, (
        "Allowlist contains stale entries — these files no longer import/call "
        f"scoped_session and should be removed from ALLOWLIST: {sorted(stale)}"
    )


@pytest.mark.parametrize("relpath", sorted(ALLOWLIST))
def test_allowlisted_file_has_fixme(relpath: str) -> None:
    """Every allowlisted file must have a `FIXME(salesagent-...)` comment."""
    content = (SRC / relpath).read_text(encoding="utf-8")
    assert "FIXME(salesagent-" in content, (
        f"Allowlisted file {relpath!r} is missing a "
        "`FIXME(salesagent-xxxx)` comment documenting why it is allowlisted "
        "and which PR removes it."
    )
