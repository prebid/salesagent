"""Structural guard (DORMANT at L0): ``ApproximatedExternalDomainMiddleware``
only fires on ``/admin`` and ``/tenant`` path prefixes.

Per CLAUDE.md invariant #5 (middleware stack at L2):

> Fly → ExternalDomain → TrustedHost → SecurityHeaders → UnifiedAuth → …

``ApproximatedExternalDomainMiddleware`` rewrites requests based on the
``Apx-Incoming-Host`` header for the multi-tenant custom-domain feature.
It MUST be path-gated so AdCP protocol paths (``/mcp``, ``/a2a``,
``/api/v1/``) are NEVER rewritten — tenants are resolved via auth headers
on those paths, not via the custom-domain lookup.

At L0 the middleware class does not exist yet (L0-07 will add it). When it
does, its ``dispatch()`` method MUST contain a path-prefix check referring
to ``/admin`` or ``/tenant``. The scanner looks for a class named
``ApproximatedExternalDomainMiddleware`` and inspects its ``dispatch``
method body for a ``startswith("/admin")`` or ``startswith("/tenant")``
call.

Meta-guard: planted fixture where the class has NO such gate must trip the
detector.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
row #7 of the §5.5 Structural Guards Inventory.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    SRC,
    iter_python_files,
)

FIXTURE = FIXTURES_DIR / "test_approximated_middleware_path_gated_meta_fixture.py.txt"
CLASS_NAME = "ApproximatedExternalDomainMiddleware"
REQUIRED_GATE_PREFIXES: frozenset[str] = frozenset({"/admin", "/tenant"})


def _find_dispatch_body(tree: ast.AST) -> list[ast.stmt] | None:
    """Return the body of ``ApproximatedExternalDomainMiddleware.dispatch``, or None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == CLASS_NAME:
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == "dispatch":
                    return item.body
    return None


def _contains_path_gate(body: list[ast.stmt]) -> bool:
    """Search for ``X.startswith("/admin")`` or ``X.startswith("/tenant")`` anywhere in the body."""
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "startswith":
                continue
            if not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and arg.value in REQUIRED_GATE_PREFIXES:
                return True
            # Tuple argument: startswith(("/admin", "/tenant")).
            if isinstance(arg, ast.Tuple | ast.List):
                for elt in arg.elts:
                    if isinstance(elt, ast.Constant) and elt.value in REQUIRED_GATE_PREFIXES:
                        return True
    return False


def test_approximated_middleware_is_path_gated_when_present() -> None:
    """DORMANT at L0: the class does not exist in src/ yet."""
    for path in iter_python_files([SRC]):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        body = _find_dispatch_body(tree)
        if body is None:
            continue
        assert _contains_path_gate(body), (
            f"{path.as_posix()}::{CLASS_NAME}.dispatch must gate on "
            f"path.startswith('/admin') or path.startswith('/tenant'). "
            "Without the gate, AdCP protocol paths (/mcp, /a2a, /api/v1/) "
            "would be subject to custom-domain rewriting."
        )


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    body = _find_dispatch_body(tree)
    assert body is not None, f"Detector did not locate {CLASS_NAME}.dispatch in fixture."
    assert not _contains_path_gate(body), (
        f"Detector FAILED to report missing path gate in {FIXTURE.name}. "
        "The fixture has no startswith() gate but the detector said it did."
    )


@pytest.mark.parametrize(
    "body_src,has_gate",
    [
        ('path = request.url.path\nif not path.startswith("/admin"):\n    return await call_next(request)\n', True),
        (
            'if not request.url.path.startswith(("/admin", "/tenant")):\n    return await call_next(request)\n',
            True,
        ),
        ("return await call_next(request)\n", False),
        ('if request.url.path.startswith("/mcp"):\n    return await call_next(request)\n', False),
    ],
)
def test_gate_detector_behavior(body_src: str, has_gate: bool) -> None:
    stmts = ast.parse(body_src).body
    assert _contains_path_gate(stmts) is has_gate
