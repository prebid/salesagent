"""Structural guard (Captured→shrink, retires at L2): no NEW
``request.form.getlist(...)`` under ``src/admin/``.

FastAPI's idiomatic replacement for Flask's ``request.form.getlist('x')`` is
``x: list[str] = Form(...)`` in the handler signature. Mixing the two is a
parity bug — the Flask path works today but breaks the moment a router is
ported without updating its template/JS contract.

Current call sites are captured in
``tests/unit/architecture/allowlists/no_form_getlist.txt``. The list MAY
shrink (as routers are ported) but MUST NOT grow. Retires entirely at L2.

Meta-guard: planted fixture with a direct ``request.form.getlist(...)``
call trips the detector.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
row #14 of the §5.5 Structural Guards Inventory.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    REPO_ROOT,
    SRC,
    iter_python_files,
    read_allowlist,
    relpath,
)

ALLOWLIST_FILE = "no_form_getlist.txt"
FIXTURE = FIXTURES_DIR / "test_form_getlist_parity_meta_fixture.py.txt"
ADMIN_ROOT = SRC / "admin"


def _has_form_getlist_call(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "getlist":
            continue
        # The receiver must itself be an attribute access ending in ``.form``
        # so that the full chain is ``<anything>.form.getlist(...)``.
        receiver = func.value
        if isinstance(receiver, ast.Attribute) and receiver.attr == "form":
            return True
    return False


def _file_has(path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    return _has_form_getlist_call(tree)


def test_no_new_form_getlist_under_admin() -> None:
    allowlist = read_allowlist(ALLOWLIST_FILE)
    violations = {relpath(p) for p in iter_python_files([ADMIN_ROOT]) if _file_has(p)}
    new_violations = violations - allowlist
    assert not new_violations, (
        "`request.form.getlist(...)` found in NEW files under src/admin/. "
        "New FastAPI handlers must use `x: list[str] = Form(...)` instead. "
        "Offending files:\n" + "\n".join(f"  - {v}" for v in sorted(new_violations))
    )


def test_allowlist_shrinks_never_grows() -> None:
    allowlist = read_allowlist(ALLOWLIST_FILE)
    stale: list[str] = []
    for rel in allowlist:
        path = REPO_ROOT / rel
        if not path.exists():
            stale.append(f"{rel} (file does not exist)")
            continue
        if not _file_has(path):
            stale.append(f"{rel} (no longer uses request.form.getlist — remove)")
    assert not stale, "Stale entries in no_form_getlist.txt:\n" + "\n".join(f"  - {s}" for s in stale)


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    assert _has_form_getlist_call(tree), f"AST scanner FAILED to detect request.form.getlist() in {FIXTURE.name}."


@pytest.mark.parametrize(
    "snippet,expected",
    [
        ("request.form.getlist('x')\n", True),
        ("flask.request.form.getlist('x')\n", True),
        ("req.form.getlist('x')\n", True),
        # NOT form.getlist — some other chain.
        ("request.args.getlist('x')\n", False),
        # Just getlist on a non-form receiver.
        ("x.getlist('y')\n", False),
        # Defining, not calling.
        ("def getlist(): pass\n", False),
    ],
)
def test_detector_behavior(snippet: str, expected: bool) -> None:
    assert _has_form_getlist_call(ast.parse(snippet)) is expected
