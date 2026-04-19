"""Structural guard (Captured→shrink): no new `flask` imports under src/.

Part of the Flask→FastAPI v2.0 migration L0-01 batch. Prevents NEW code from
importing Flask while the migration is in flight. The allowlist
(``tests/unit/architecture/allowlists/no_flask_imports.txt``) captures every
file that imports Flask TODAY; it MAY shrink as files are ported to FastAPI
but it MUST NOT grow.

Meta-guards:

* A planted fixture (`fixtures/test_no_flask_imports_meta_fixture.py.txt`)
  proves the AST scanner actually detects `from flask import` and
  `import flask` forms.
* Parametrized unit tests lock down the detector's positive and negative
  behavior on synthetic snippets.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
row #1 of the §5.5 Structural Guards Inventory.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    SRC,
    find_stale_allowlist_entries,
    iter_python_files,
    read_allowlist,
    relpath,
)

ALLOWLIST_FILE = "no_flask_imports.txt"
FIXTURE = FIXTURES_DIR / "test_no_flask_imports_meta_fixture.py.txt"


def _imports_flask(tree: ast.AST) -> bool:
    """True iff AST contains `import flask[.x]` or `from flask[.x] import ...`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "flask" or alias.name.startswith("flask."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and (node.module == "flask" or node.module.startswith("flask.")):
                return True
    return False


def _file_imports_flask(path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    return _imports_flask(tree)


def test_no_new_flask_imports_under_src() -> None:
    """No new files under src/ may import flask. Allowlist may shrink, never grow."""
    allowlist = read_allowlist(ALLOWLIST_FILE)
    violations = {relpath(p) for p in iter_python_files([SRC]) if _file_imports_flask(p)}
    new_violations = violations - allowlist
    assert not new_violations, (
        "New files are importing `flask`. This is not allowed while the "
        "Flask→FastAPI v2.0 migration is in flight. New admin code must use "
        "FastAPI (APIRouter). Offending files:\n" + "\n".join(f"  - {v}" for v in sorted(new_violations))
    )


def test_allowlist_shrinks_never_grows() -> None:
    """Meta-test: every allowlisted path must still exist and still import flask.

    When a file is ported to FastAPI and no longer imports flask, its entry
    MUST be removed from the allowlist (the list shrinks). Stale allowlist
    entries are a guard bug — they let real violations slip through.
    """
    stale = find_stale_allowlist_entries(
        ALLOWLIST_FILE,
        still_violates=_file_imports_flask,
        removal_reason="no longer imports flask",
    )
    assert not stale, "Stale entries in no_flask_imports.txt:\n" + "\n".join(f"  - {s}" for s in stale)


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), (
        f"Meta-fixture missing at {FIXTURE}. Required to prove the AST scanner "
        "detects `from flask import` / `import flask` forms."
    )


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    assert _imports_flask(tree), (
        f"AST scanner FAILED to detect the flask import in {FIXTURE.name}. "
        "The guard is broken — real violations would slip past undetected."
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "from flask import Blueprint\n",
        "from flask import Flask, request\n",
        "import flask\n",
        "import flask.views\n",
        "from flask.helpers import url_for\n",
        # Nested — still a violation.
        "def f():\n    from flask import g\n    return g\n",
    ],
)
def test_detector_catches_synthetic_violations(snippet: str) -> None:
    tree = ast.parse(snippet)
    assert _imports_flask(tree), f"Detector missed violation in snippet: {snippet!r}"


@pytest.mark.parametrize(
    "snippet",
    [
        # Clean code the detector MUST NOT flag.
        "from fastapi import APIRouter\n",
        "from starlette.requests import Request\n",
        'x = "flask was removed"\n',
        "flask_like_name = 42\n",
        # String literal mentioning flask is not an import.
        "import os  # 'flask' in comment\n",
    ],
)
def test_detector_accepts_clean_snippets(snippet: str) -> None:
    tree = ast.parse(snippet)
    assert not _imports_flask(tree), f"Detector false-positive on: {snippet!r}"
