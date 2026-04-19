"""Structural guard (Captured→shrink): no new `werkzeug` imports.

Part of the Flask→FastAPI v2.0 migration. Guard #34 in the §5.5 Structural
Guards Inventory, reassigned to L0-01 per the v2 plan §7.1 RATIFIED.

``werkzeug`` is Flask's WSGI toolkit; imports of it will disappear entirely
when Flask is removed at L2. Until then, the allowlist
(``tests/unit/architecture/allowlists/no_werkzeug_imports.txt``) captures
every file that imports werkzeug TODAY. The list MAY shrink but MUST NOT
grow; new code must not pick up new werkzeug dependencies.

Meta-guards via planted fixture + parametrized detector tests.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    SCRIPTS,
    SRC,
    TESTS,
    find_stale_allowlist_entries,
    iter_python_files,
    read_allowlist,
    relpath,
)

ALLOWLIST_FILE = "no_werkzeug_imports.txt"
FIXTURE = FIXTURES_DIR / "test_no_werkzeug_imports_meta_fixture.py.txt"


def _imports_werkzeug(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "werkzeug" or alias.name.startswith("werkzeug."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and (node.module == "werkzeug" or node.module.startswith("werkzeug.")):
                return True
    return False


def _file_imports_werkzeug(path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    return _imports_werkzeug(tree)


def test_no_new_werkzeug_imports() -> None:
    allowlist = read_allowlist(ALLOWLIST_FILE)
    violations = {relpath(p) for p in iter_python_files([SRC, TESTS, SCRIPTS]) if _file_imports_werkzeug(p)}
    new_violations = violations - allowlist
    assert not new_violations, (
        "New files are importing `werkzeug` (Flask's WSGI toolkit). "
        "New code must use Starlette/FastAPI equivalents. Offending files:\n"
        + "\n".join(f"  - {v}" for v in sorted(new_violations))
    )


def test_allowlist_shrinks_never_grows() -> None:
    stale = find_stale_allowlist_entries(
        ALLOWLIST_FILE,
        still_violates=_file_imports_werkzeug,
        removal_reason="no longer imports werkzeug",
    )
    assert not stale, "Stale entries in no_werkzeug_imports.txt:\n" + "\n".join(f"  - {s}" for s in stale)


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    assert _imports_werkzeug(tree), f"AST scanner FAILED to detect the werkzeug import in {FIXTURE.name}."


@pytest.mark.parametrize(
    "snippet",
    [
        "from werkzeug.middleware.proxy_fix import ProxyFix\n",
        "from werkzeug.wrappers import Response\n",
        "import werkzeug\n",
        "import werkzeug.serving\n",
    ],
)
def test_detector_catches_synthetic_violations(snippet: str) -> None:
    tree = ast.parse(snippet)
    assert _imports_werkzeug(tree), f"Detector missed violation in: {snippet!r}"


@pytest.mark.parametrize(
    "snippet",
    [
        "from starlette.middleware.base import BaseHTTPMiddleware\n",
        "from starlette.responses import Response\n",
        'x = "werkzeug deprecated"\n',
    ],
)
def test_detector_accepts_clean_snippets(snippet: str) -> None:
    tree = ast.parse(snippet)
    assert not _imports_werkzeug(tree), f"Detector false-positive on: {snippet!r}"
