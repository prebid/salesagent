"""Structural guard: CSRF exemption list covers every AdCP public path.

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

Activated in the L2-prep refactor (PR #1221 follow-up): the guard now
requires at least one module under ``src/`` to declare ``CSRF_EXEMPT_PATHS``
containing every required entry. Today that module is ``src/admin/csrf.py``.

The guard combines the historical OAuth-callback list with the transport
prefix list under a single unified name — see ``src/admin/csrf.py`` for
the authoritative declaration and ``_is_exempt``'s dual-semantic predicate
(exact-match for bare entries, prefix-match for trailing-slash entries).

The meta-guard proves the detector works against a planted fixture that
DECLARES the expected constant but MISSES entries — the detector reports
the missing ones.

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
    """Return the set of string literals assigned to ``CSRF_EXEMPT_PATHS``, or None.

    Accepts both bare assignment (``CSRF_EXEMPT_PATHS = (...)``) and the
    annotated form (``CSRF_EXEMPT_PATHS: tuple[str, ...] = (...)``) since
    the authoritative declaration in ``src/admin/csrf.py`` uses the
    annotated form for mypy.
    """
    if not isinstance(tree, ast.Module):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == CONSTANT_NAME:
                    return _literal_string_collection(node.value)
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == CONSTANT_NAME and node.value is not None:
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


def test_csrf_exempt_declaration_covers_required_paths() -> None:
    """Every module declaring ``CSRF_EXEMPT_PATHS`` must list every required path.

    Any module under ``src/`` that declares ``CSRF_EXEMPT_PATHS`` must be a
    superset of the required AdCP-plus-OAuth-callback path set. Missing an
    entry opens a CSRF-rejection on a path that cannot be CSRF-protected
    (OAuth callbacks originate from the provider's origin; MCP/A2A carry
    their own auth surface and never use session cookies).
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


def test_csrf_exempt_declaration_is_active_at_l2_prep() -> None:
    """Anti-vacuous guard: at least one module under ``src/`` must declare ``CSRF_EXEMPT_PATHS``.

    Without this check, the ``_covers_required_paths`` test above trivially
    passes when nothing declares the constant — a silent regression surface.
    ``src/admin/csrf.py`` is the authoritative source today; if it ever loses
    the declaration, this test catches it before the coverage check does.
    """
    declaring_modules: list[str] = []
    for path in iter_python_files([SRC]):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        if _find_csrf_exempt_paths(tree) is not None:
            declaring_modules.append(path.as_posix())

    assert declaring_modules, (
        f"No module under src/ declares {CONSTANT_NAME}. The CSRF exemption "
        "contract is unsatisfied; a module must declare the constant so the "
        "coverage guard can validate it."
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
