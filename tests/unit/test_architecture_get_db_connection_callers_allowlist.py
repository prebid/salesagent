"""Structural guard: `get_db_connection()` call sites are pinned to the PID-1 path.

Companion to `test_architecture_no_runtime_psycopg2.py`. Where that guard
prevents new raw-psycopg2 imports, this guard prevents new callers of the
public `get_db_connection()` factory — the runtime entry point into the
raw-psycopg2 `DatabaseConnection` class retained per Agent F Audit 06
Decision 2.

Scope scanned:
    - `src/**/*.py`
    - `scripts/**/*.py`

Scope excluded:
    - `src/core/database/db_config.py` — this file DEFINES `get_db_connection`.
    - `examples/` — non-shipped tutorial code.
    - `tests/` — test code may mock or reference `get_db_connection` without
      calling it for real.

Allowlist:
    Exactly ONE file:  `scripts/deploy/run_all_services.py`
    (two callers inside: `check_database_health` + `check_schema_issues`, per
    the module docstring in `src/core/database/db_config.py`).

Ratcheting rules:
    - The allowlist may SHRINK, never grow.
    - Stale-entry meta-test fails if the allowlisted file no longer calls
      `get_db_connection()`.
    - Synthetic-AST meta-tests prove the detector catches what it claims.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"

DEFINITION_FILE = "src/core/database/db_config.py"

ALLOWLIST: frozenset[str] = frozenset(
    {
        # PID-1 orchestrator — fork-safe raw-psycopg2 path (Decision 2).
        # Calls: check_database_health (line ~84), check_schema_issues (line ~135).
        "scripts/deploy/run_all_services.py",
    }
)


def _iter_python_files() -> list[Path]:
    out: list[Path] = []
    for root in (SRC, SCRIPTS):
        for p in root.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            out.append(p)
    return out


def _module_calls_get_db_connection(tree: ast.AST) -> bool:
    """True iff the AST contains a `get_db_connection(...)` Call node."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Bare `get_db_connection(...)` — matches `from X import get_db_connection; get_db_connection()`.
        if isinstance(func, ast.Name) and func.id == "get_db_connection":
            return True
        # Attribute `X.get_db_connection(...)` — matches `db_config.get_db_connection()` etc.
        if isinstance(func, ast.Attribute) and func.attr == "get_db_connection":
            return True
    return False


def _file_calls_get_db_connection(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    return _module_calls_get_db_connection(tree)


def _relpath(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def test_no_new_get_db_connection_callers() -> None:
    """No new callers of `get_db_connection()` may appear outside the allowlist."""
    violations: set[str] = set()
    for p in _iter_python_files():
        rel = _relpath(p)
        if rel == DEFINITION_FILE:
            continue  # db_config.py defines get_db_connection; skip.
        if _file_calls_get_db_connection(p):
            violations.add(rel)

    new_violations = violations - ALLOWLIST
    assert not new_violations, (
        "New callers of get_db_connection() detected. This factory returns a "
        "raw psycopg2 connection retained ONLY for the PID-1 fork-safe path "
        "(Agent F Audit 06 Decision 2). New code MUST use get_db_session() "
        "from src.core.database.database_session. "
        f"Violations: {sorted(new_violations)}"
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Meta-test: allowlisted files must still call get_db_connection()."""
    current_callers: set[str] = set()
    for p in _iter_python_files():
        rel = _relpath(p)
        if rel == DEFINITION_FILE:
            continue
        if _file_calls_get_db_connection(p):
            current_callers.add(rel)

    stale = ALLOWLIST - current_callers
    assert not stale, (
        "Allowlist contains stale entries — these files no longer call "
        f"get_db_connection() and should be removed from ALLOWLIST: {sorted(stale)}"
    )


@pytest.mark.parametrize(
    "snippet",
    [
        # Bare call after import.
        "from src.core.database.db_config import get_db_connection\nconn = get_db_connection()\n",
        # Attribute call via module alias.
        "from src.core.database import db_config\nconn = db_config.get_db_connection()\n",
        # Nested inside a function.
        "def f():\n    from src.core.database.db_config import get_db_connection\n    return get_db_connection()\n",
    ],
)
def test_detector_catches_synthetic_violations(snippet: str) -> None:
    """Meta-test: prove the AST scanner actually detects each call form."""
    tree = ast.parse(snippet)
    assert _module_calls_get_db_connection(tree), f"Detector missed call in snippet: {snippet!r}"


@pytest.mark.parametrize(
    "snippet",
    [
        # Clean: imports but never calls.
        "from src.core.database.db_config import get_db_connection  # reference only\n",
        # Clean: uses get_db_session instead.
        "from src.core.database.database_session import get_db_session\nwith get_db_session() as s:\n    pass\n",
        # Clean: a completely unrelated function call.
        "import os\nos.getenv('X')\n",
    ],
)
def test_detector_accepts_clean_snippets(snippet: str) -> None:
    """Meta-test: prove the AST scanner does not false-positive."""
    tree = ast.parse(snippet)
    assert not _module_calls_get_db_connection(tree), f"Detector false-positive on: {snippet!r}"
