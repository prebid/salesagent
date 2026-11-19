"""Guard: request.getfixturevalue("integration_db") has exactly one call site.

_db_scope_for (tests/bdd/conftest.py) is the sanctioned DB entry point for BDD
harness branches: in-process transports pull the per-test ``integration_db``
fixture, while e2e_rest points production's engine at the live server DB
instead. A direct ``request.getfixturevalue("integration_db")`` call anywhere
else in tests/bdd/ bypasses that choice — under the e2e_rest parametrization
it would repoint production's cached engine at an empty per-test DB while the
harness env's factories write to the live server DB, so any in-process
production call inside that scenario (a raw ``get_db_session()`` read-back in
a Then step, a TRANSPORT-BYPASS Given calling an ``_impl``) reads the wrong
database.

This guard pins the call count to exactly one and pins that one call site
inside ``_db_scope_for`` itself. There is no allowlist: any new direct call
site is a defect, not debt to track.

"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import iter_call_expressions, rel

_BDD_DIR = Path(__file__).resolve().parents[1] / "bdd"
_CONFTEST = _BDD_DIR / "conftest.py"
_SANCTIONED_FUNCTION = "_db_scope_for"


def _is_integration_db_getfixturevalue(call: ast.Call) -> bool:
    """True for `<expr>.getfixturevalue("integration_db")` call nodes."""
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr == "getfixturevalue"):
        return False
    if not call.args:
        return False
    first_arg = call.args[0]
    return isinstance(first_arg, ast.Constant) and first_arg.value == "integration_db"


def _find_enclosing_function(tree: ast.Module, call: ast.Call) -> str | None:
    """Return the name of the innermost function def containing `call`, if any."""
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start, end = node.lineno, node.end_lineno or node.lineno
        if start <= call.lineno <= end:
            if best is None or (node.end_lineno or 0) - (node.lineno or 0) < (best.end_lineno or 0) - (
                best.lineno or 0
            ):
                best = node
    return best.name if best else None


def _scan_call_sites() -> list[tuple[Path, int, str | None]]:
    """Find every `getfixturevalue("integration_db")` call site under tests/bdd/.

    Returns (file, lineno, enclosing_function_name) tuples.
    """
    sites: list[tuple[Path, int, str | None]] = []
    for py_file in sorted(_BDD_DIR.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        for call in iter_call_expressions(tree):
            if _is_integration_db_getfixturevalue(call):
                sites.append((py_file, call.lineno, _find_enclosing_function(tree, call)))
    return sites


class TestBddSingleDbScopeEntry:
    """Structural guard: getfixturevalue(integration_db) has exactly one call site."""

    @pytest.mark.arch_guard
    def test_exactly_one_call_site(self):
        """No allowlist: any direct call site beyond the sanctioned one is a defect."""
        sites = _scan_call_sites()
        assert len(sites) == 1, (
            f'Expected exactly one request.getfixturevalue("integration_db") call site in '
            f"tests/bdd/, found {len(sites)}:\n"
            + "\n".join(f"  {rel(path, _BDD_DIR.parent.parent)}:{lineno}" for path, lineno, _ in sites)
            + f"\nRoute new DB access through {_SANCTIONED_FUNCTION}(request, e2e_config) instead."
        )

    @pytest.mark.arch_guard
    def test_call_site_is_inside_db_scope_for(self):
        """The one sanctioned call site must live inside _db_scope_for in conftest.py."""
        sites = _scan_call_sites()
        assert len(sites) == 1, "test_exactly_one_call_site should catch a count mismatch first"
        path, lineno, enclosing_function = sites[0]
        assert path == _CONFTEST, (
            f"Sanctioned getfixturevalue(integration_db) call site must live in "
            f"{rel(_CONFTEST, _BDD_DIR.parent.parent)}, found it in "
            f"{rel(path, _BDD_DIR.parent.parent)}:{lineno}"
        )
        assert enclosing_function == _SANCTIONED_FUNCTION, (
            f"Sanctioned getfixturevalue(integration_db) call site must be inside "
            f"{_SANCTIONED_FUNCTION}(), found it inside {enclosing_function!r} at "
            f"{rel(path, _BDD_DIR.parent.parent)}:{lineno}"
        )
