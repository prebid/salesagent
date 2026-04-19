"""Structural guard (Captured→shrink): no NEW direct env-var access under
``src/admin/`` or ``src/core/`` (except ``src/core/config.py``, the central
entry point).

Per the Flask→FastAPI v2.0 foundation-modules plan: environment variables
should be read ONCE at startup inside ``src.core.config`` and distributed
as typed settings; scattering ``os.environ.get(...)`` / ``os.getenv(...)``
calls across the codebase is a correctness hazard (different modules read
different values at different times, tests can't reliably override them).

Each existing call site is captured at ``path:line`` granularity in
``tests/unit/architecture/allowlists/no_direct_env_access.txt``. MAY shrink
but MUST NOT grow.

Meta-guard: planted fixture with both ``os.environ.get`` and ``os.getenv``
call forms trips the detector.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
row #16 of the §5.5 Structural Guards Inventory.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    SRC,
    iter_python_files,
    read_allowlist,
    relpath,
)

ALLOWLIST_FILE = "no_direct_env_access.txt"
FIXTURE = FIXTURES_DIR / "test_no_direct_env_access_meta_fixture.py.txt"

# Sanctioned-config module — allowed to read env directly.
CONFIG_MODULE = SRC / "core" / "config.py"


def _is_direct_env_access(call: ast.Call) -> bool:
    func = call.func
    # os.getenv(...)
    if isinstance(func, ast.Attribute) and func.attr == "getenv":
        return True
    # os.environ.get(...)
    if isinstance(func, ast.Attribute) and func.attr == "get":
        receiver = func.value
        if isinstance(receiver, ast.Attribute) and receiver.attr == "environ":
            return True
        if isinstance(receiver, ast.Name) and receiver.id == "environ":
            return True
    return False


def _find_direct_env_lines(tree: ast.AST) -> list[int]:
    out: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_direct_env_access(node):
            out.append(node.lineno)
    return out


def _scan_repo() -> set[str]:
    """Return {"relpath:lineno"} for every direct env access under admin/core (minus config.py)."""
    hits: set[str] = set()
    for root in [SRC / "admin", SRC / "core"]:
        for path in iter_python_files([root]):
            if path == CONFIG_MODULE:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for lineno in _find_direct_env_lines(tree):
                hits.add(f"{relpath(path)}:{lineno}")
    return hits


def test_no_new_direct_env_access() -> None:
    allowlist = read_allowlist(ALLOWLIST_FILE)
    violations = _scan_repo()
    new_violations = violations - allowlist
    assert not new_violations, (
        "New direct `os.environ.get(...)` / `os.getenv(...)` calls under "
        "src/admin/ or src/core/. Read env via src.core.config instead. "
        "Offending sites:\n" + "\n".join(f"  - {v}" for v in sorted(new_violations))
    )


def test_allowlist_shrinks_never_grows() -> None:
    allowlist = read_allowlist(ALLOWLIST_FILE)
    current = _scan_repo()
    stale = sorted(allowlist - current)
    assert not stale, (
        "Stale entries in no_direct_env_access.txt (source site no longer "
        "has a direct env access at that exact line — remove the entry):\n" + "\n".join(f"  - {s}" for s in stale)
    )


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    lines = _find_direct_env_lines(tree)
    assert len(lines) >= 2, (
        f"AST scanner caught {len(lines)} env accesses in {FIXTURE.name} "
        "(expected ≥2 — one for os.environ.get, one for os.getenv). Guard is broken."
    )


@pytest.mark.parametrize(
    "snippet,expected",
    [
        ("os.getenv('FOO')\n", 1),
        ("os.environ.get('FOO')\n", 1),
        ("os.environ.get('FOO', 'default')\n", 1),
        ("environ.get('FOO')\n", 1),
        # Non-env access — NOT a violation.
        ("d.get('FOO')\n", 0),
        ("dict.getenv('FOO')\n", 1),  # `.getenv` on anything — conservative.
        ("settings.FOO\n", 0),
        # Function definition, not call.
        ("def getenv(): pass\n", 0),
    ],
)
def test_detector_behavior(snippet: str, expected: int) -> None:
    tree = ast.parse(snippet)
    assert len(_find_direct_env_lines(tree)) == expected
