"""Structural guard (DORMANT at L0): every admin route decorator includes a
``name=`` keyword argument.

Per CLAUDE.md invariant #1 (templates use ``{{ url_for('name', **params) }}``
exclusively): every admin route MUST have ``name=`` so ``url_for`` can
resolve it. Starlette's ``include_router(prefix=...)`` does not set
``scope['root_path']``, making ``url_for`` the only correct URL generator.

At L0 admin routers are still Flask blueprints; no FastAPI
``@router.<method>`` decorators exist under ``src/admin/routers/``. The test
passes vacuously. Meta-guard proves the detector catches a planted missing
``name=``.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
row #9 of the §5.5 Structural Guards Inventory.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    SRC,
    iter_python_files,
    relpath,
)

FIXTURE = FIXTURES_DIR / "test_admin_routes_named_meta_fixture.py.txt"
ADMIN_ROUTERS_ROOT = SRC / "admin" / "routers"
# FastAPI route decorator methods only. Flask's `@bp.route(...)` is NOT in
# this set — Flask routers are being ported to FastAPI as part of the
# migration, and until then their decorators are out of scope. ``api_route``
# is FastAPI's catch-all.
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "api_route"}


def _iter_route_decorators(tree: ast.AST):
    """Yield (lineno, func_name, decorator_call_node) for every ``@router.<method>(...)`` decorator."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            func = deco.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in HTTP_METHODS:
                continue
            yield (deco.lineno, node.name, deco)


def _has_name_kwarg(call: ast.Call) -> bool:
    return any(kw.arg == "name" for kw in call.keywords)


def test_admin_route_decorators_have_name() -> None:
    violations: list[str] = []
    for path in iter_python_files([ADMIN_ROUTERS_ROOT]):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for lineno, func_name, deco in _iter_route_decorators(tree):
            if not _has_name_kwarg(deco):
                violations.append(f"{relpath(path)}:{lineno} (handler: {func_name})")
    assert not violations, (
        "Admin route decorators must include `name=`. Templates use "
        "`url_for('name', **params)` and Starlette's include_router(prefix=...) "
        "does not set scope['root_path']. Offending decorators:\n" + "\n".join(f"  - {v}" for v in sorted(violations))
    )


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    decos = list(_iter_route_decorators(tree))
    assert decos, f"Detector found no route decorators in {FIXTURE.name}."
    assert not any(
        _has_name_kwarg(d) for _, _, d in decos
    ), f"Detector FAILED to notice missing `name=` kwarg in {FIXTURE.name}."


@pytest.mark.parametrize(
    "snippet,should_flag",
    [
        ("@router.get('/x')\ndef h(): pass\n", True),
        ("@router.get('/x', name='h')\ndef h(): pass\n", False),
        ("@router.post('/y', name='post_y', status_code=201)\ndef h(): pass\n", False),
        # Plain function, no decorator.
        ("def h(): pass\n", False),
    ],
)
def test_detector_behavior(snippet: str, should_flag: bool) -> None:
    tree = ast.parse(snippet)
    decos = list(_iter_route_decorators(tree))
    if should_flag:
        assert decos and not _has_name_kwarg(decos[0][2])
    else:
        # Either no decorator or it HAS name=.
        assert not decos or _has_name_kwarg(decos[0][2])
