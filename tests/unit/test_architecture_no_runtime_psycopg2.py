"""Structural guard: psycopg2 imports live only in the fork-safe PID-1 path.

Agent F Audit 06 Decision 2 (reaffirmed 2026-04-11): `psycopg2-binary` is
RETAINED in pyproject.toml — not replaced — because the PID-1 orchestrator
`scripts/deploy/run_all_services.py` spawns uvicorn via `subprocess.Popen`.
Constructing a SQLAlchemy engine in the parent (via `get_db_session()`)
would leak pooled PG sockets into the forked child and corrupt them.
Raw psycopg2 connect-query-close is fork-safe because the connection is
fully torn down before the fork.

To prevent new runtime psycopg2 usage from creeping in, this guard
AST-walks every `src/**/*.py` file and flags any module that imports
`psycopg2` (either `import psycopg2` or `from psycopg2 ...`).

Allowlist: exactly ONE file today — `src/core/database/db_config.py`
(the `DatabaseConnection` class reached only from
`scripts/deploy/run_all_services.py`). Any other file importing psycopg2
at runtime fails CI.

Ratcheting rules:
- The allowlist may SHRINK, never grow.
- Each allowlisted source has a documenting comment at the import site
  (the module docstring of `db_config.py` explains the retention).
- The stale-entry meta-test fails if an allowlisted file no longer imports
  psycopg2 — remove it from the allowlist in that PR.

Meta-test: a deliberate synthetic violation AST (built in-memory, not a
real file on disk) proves the detector catches what it claims to catch.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"

# Allowlist — files permitted to import psycopg2 at runtime.
# Keys are POSIX-style paths relative to `src/`.
ALLOWLIST: frozenset[str] = frozenset(
    {
        # Retained for Agent F Audit 06 Decision 2 (PID-1 fork safety).
        # Sole caller of this module is scripts/deploy/run_all_services.py —
        # see the module docstring in db_config.py for the full rationale.
        "core/database/db_config.py",
    }
)


def _iter_python_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def _module_imports_psycopg2(tree: ast.AST) -> bool:
    """True iff the AST contains any top-level or nested psycopg2 import."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # `import psycopg2` or `import psycopg2.extras` etc.
                if alias.name == "psycopg2" or alias.name.startswith("psycopg2."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            # `from psycopg2 import ...` or `from psycopg2.extras import ...`
            if node.module is not None and (node.module == "psycopg2" or node.module.startswith("psycopg2.")):
                return True
    return False


def _file_imports_psycopg2(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    return _module_imports_psycopg2(tree)


def _relpath(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def test_no_new_runtime_psycopg2_violations() -> None:
    """No new psycopg2 imports may be added under src/ outside the allowlist."""
    violations = {_relpath(p) for p in _iter_python_files() if _file_imports_psycopg2(p)}
    new_violations = violations - ALLOWLIST
    assert not new_violations, (
        "New runtime psycopg2 import(s) detected in src/. psycopg2 is retained "
        "only for the PID-1 fork-safe path (Agent F Audit 06 Decision 2) — all "
        "other code must use the SQLAlchemy engine via get_db_session(). "
        f"Violations: {sorted(new_violations)}"
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Meta-test: allowlisted files must still import psycopg2."""
    current_violations = {_relpath(p) for p in _iter_python_files() if _file_imports_psycopg2(p)}
    stale = ALLOWLIST - current_violations
    assert not stale, (
        "Allowlist contains stale entries — these files no longer import psycopg2 "
        f"and should be removed from ALLOWLIST: {sorted(stale)}"
    )


@pytest.mark.parametrize(
    "snippet",
    [
        # Synthetic violations the detector MUST catch.
        "import psycopg2\n",
        "import psycopg2.extras\n",
        "from psycopg2 import connect\n",
        "from psycopg2.extras import DictCursor\n",
        # Nested inside a function — still a violation (runtime import).
        "def f():\n    import psycopg2\n    return psycopg2.connect('x')\n",
    ],
)
def test_detector_catches_synthetic_violations(snippet: str) -> None:
    """Meta-test: prove the AST scanner actually detects each import form."""
    tree = ast.parse(snippet)
    assert _module_imports_psycopg2(tree), f"Detector missed violation in snippet: {snippet!r}"


@pytest.mark.parametrize(
    "snippet",
    [
        # Clean code the detector MUST NOT flag.
        "import os\n",
        "from sqlalchemy import select\n",
        "from src.core.database.database_session import get_db_session\n",
        # String that only MENTIONS psycopg2 — not an import.
        'x = "psycopg2 is the sync driver"\n',
        # Variable literally named psycopg2 — irrelevant, no import.
        "psycopg2 = None\n",
    ],
)
def test_detector_accepts_clean_snippets(snippet: str) -> None:
    """Meta-test: prove the AST scanner does not false-positive on clean code."""
    tree = ast.parse(snippet)
    assert not _module_imports_psycopg2(tree), f"Detector false-positive on: {snippet!r}"
