"""Structural guard (DORMANT at L0): CSRF exemption list covers every AdCP
public path.

Required entries (per CLAUDE.md invariant #6 and foundation-modules.md):

* ``/mcp``
* ``/a2a``
* ``/api/v1/``
* ``/_internal/``
* ``/.well-known/``
* ``/agent.json``
* ``/admin/auth/google/callback``
* ``/admin/auth/oidc/callback``
* ``/admin/auth/gam/callback``

At L0 ``CSRFOriginMiddleware`` does not yet exist (L0-06 will add it). The
scanner therefore returns an empty result today and this test passes
vacuously. The meta-guard still proves the detector works against a planted
fixture that DECLARES the expected constant but MISSES entries — the
detector reports the missing ones.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
row #6 of the §5.5 Structural Guards Inventory.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    SRC,
    iter_python_files,
)

FIXTURE = FIXTURES_DIR / "test_csrf_exempt_covers_adcp_meta_fixture.py.txt"

REQUIRED_CSRF_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/mcp",
        "/a2a",
        "/api/v1/",
        "/_internal/",
        "/.well-known/",
        "/agent.json",
        "/admin/auth/google/callback",
        "/admin/auth/oidc/callback",
        "/admin/auth/gam/callback",
    }
)

CONSTANT_NAME = "CSRF_EXEMPT_PATHS"


def _find_csrf_exempt_paths(tree: ast.AST) -> frozenset[str] | None:
    """Return the set of string literals assigned to ``CSRF_EXEMPT_PATHS``, or None."""
    if not isinstance(tree, ast.Module):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == CONSTANT_NAME:
                return _literal_string_collection(node.value)
    return None


def _literal_string_collection(value: ast.AST) -> frozenset[str] | None:
    """Extract string literals from a tuple/list/set/frozenset(...) expression."""
    if isinstance(value, ast.Tuple | ast.List | ast.Set):
        return frozenset(
            elt.value for elt in value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        )
    if isinstance(value, ast.Call):
        func = value.func
        if isinstance(func, ast.Name) and func.id in {"frozenset", "set", "tuple", "list"} and value.args:
            return _literal_string_collection(value.args[0])
    return None


def _missing_paths(declared: frozenset[str]) -> frozenset[str]:
    return REQUIRED_CSRF_EXEMPT_PATHS - declared


def test_csrf_exempt_declaration_is_dormant_at_l0() -> None:
    """DORMANT at L0 — no ``CSRF_EXEMPT_PATHS`` constant is expected yet.

    When L0-06 lands ``CSRFOriginMiddleware``, a module under src/ must
    declare ``CSRF_EXEMPT_PATHS`` containing every entry in
    ``REQUIRED_CSRF_EXEMPT_PATHS``. Today no declaration exists and this
    test passes without firing.
    """
    for path in iter_python_files([SRC]):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        declared = _find_csrf_exempt_paths(tree)
        if declared is None:
            continue
        missing = _missing_paths(declared)
        assert not missing, (
            f"{path.as_posix()} declares {CONSTANT_NAME} but is missing: "
            f"{sorted(missing)}. Every AdCP public path must be exempt from CSRF."
        )


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    declared = _find_csrf_exempt_paths(tree)
    assert declared is not None, "Detector failed to locate CSRF_EXEMPT_PATHS in fixture."
    missing = _missing_paths(declared)
    assert missing, (
        f"Detector FAILED to report missing paths in {FIXTURE.name}. "
        "The fixture omits several required entries but the detector returned "
        "no missing paths — guard is broken."
    )


@pytest.mark.parametrize(
    "snippet,expect_missing",
    [
        # Declaration missing everything → ALL required paths missing.
        ("CSRF_EXEMPT_PATHS = ()\n", True),
        # Declaration containing everything → no missing.
        (
            "CSRF_EXEMPT_PATHS = (\n"
            '    "/mcp", "/a2a", "/api/v1/", "/_internal/", "/.well-known/",\n'
            '    "/agent.json", "/admin/auth/google/callback",\n'
            '    "/admin/auth/oidc/callback", "/admin/auth/gam/callback",\n'
            ")\n",
            False,
        ),
        # Declaration via frozenset(...) form.
        (
            "CSRF_EXEMPT_PATHS = frozenset({\n"
            '    "/mcp", "/a2a", "/api/v1/", "/_internal/", "/.well-known/",\n'
            '    "/agent.json", "/admin/auth/google/callback",\n'
            '    "/admin/auth/oidc/callback", "/admin/auth/gam/callback",\n'
            "})\n",
            False,
        ),
    ],
)
def test_detector_behavior(snippet: str, expect_missing: bool) -> None:
    tree = ast.parse(snippet)
    declared = _find_csrf_exempt_paths(tree)
    assert declared is not None, "Detector should locate CSRF_EXEMPT_PATHS."
    missing = _missing_paths(declared)
    assert bool(missing) is expect_missing
