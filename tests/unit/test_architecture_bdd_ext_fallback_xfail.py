"""Guard: every ext/non-ext UC dispatch branch in _harness_env must xfail its fallback.

Recurrence-prevention guard. Twice a catch-all else-branch in the BDD harness
dispatcher (tests/bdd/conftest.py::_harness_env) had its honest
`pytest.xfail("... not yet wired ...")` guard deleted and replaced with a route
into a harness env — flipping hundreds of UNWIRED scenarios from xfail to hard
fail. Because `make quality` does not run BDD, the regression was invisible to
the normal gate.

  - UC-002: fixed in commit 655ba1f56
  - UC-003: fixed in salesagent-j4bo (this guard's motivating bug)

The disease pattern (SYNTACTIC, AST-detectable): a UC branch that splits on an
extension marker via `any(t.startswith("T-UC-XXX-ext-") ...)` must guard the
`else` (the non-extension fallback for not-yet-wired scenarios) with a call to
`pytest.xfail(...)`. An `else` that yields a harness env instead is the bug.

This guard walks _harness_env's AST, finds every `if` whose test calls
`str.startswith("...-ext-...")`, and asserts the matching `else` contains a
`pytest.xfail(...)` call. A non-regex (AST) guard, so positive + negative
meta-tests suffice (no regex-slip case).

beads: salesagent-j4bo
"""

import ast
from pathlib import Path

_CONFTEST = Path(__file__).resolve().parents[1] / "bdd" / "conftest.py"
_TARGET_FUNC = "_harness_env"


def _get_harness_env(source: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == _TARGET_FUNC:
            return node
    raise AssertionError(f"{_TARGET_FUNC} not found")


def _test_calls_ext_startswith(test: ast.expr) -> bool:
    """True if the if-test contains a `<x>.startswith("...-ext-...")` call."""
    for sub in ast.walk(test):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Attribute) and func.attr == "startswith" and sub.args:
            arg = sub.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "-ext-" in arg.value:
                return True
    return False


def _orelse_calls_xfail(orelse: list[ast.stmt]) -> bool:
    """True if the else-body contains a `pytest.xfail(...)` call."""
    for stmt in orelse:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Call):
                func = sub.func
                if isinstance(func, ast.Attribute) and func.attr == "xfail":
                    return True
                if isinstance(func, ast.Name) and func.id == "xfail":
                    return True
    return False


def _find_ext_branches(func: ast.FunctionDef) -> list[ast.If]:
    """Every `if` node inside func whose test splits on an `-ext-` marker."""
    return [n for n in ast.walk(func) if isinstance(n, ast.If) and _test_calls_ext_startswith(n.test)]


def _violations(source: str) -> list[int]:
    """Line numbers of ext-split `if` nodes whose else does NOT call pytest.xfail."""
    func = _get_harness_env(source)
    bad = []
    for node in _find_ext_branches(func):
        # An ext-split branch must have an else, and that else must xfail.
        if not node.orelse or not _orelse_calls_xfail(node.orelse):
            bad.append(node.lineno)
    return bad


def test_ext_fallback_branches_xfail_in_conftest():
    """Production conftest: every ext/non-ext UC split guards its else with pytest.xfail."""
    source = _CONFTEST.read_text()
    # Sanity: the pattern actually exists (guard is not vacuously passing).
    func = _get_harness_env(source)
    branches = _find_ext_branches(func)
    assert branches, (
        "No ext/non-ext UC split branches found in _harness_env — the guard's "
        "anchor pattern disappeared. If the dispatcher was restructured, update this guard."
    )
    bad = _violations(source)
    assert bad == [], (
        f"_harness_env has ext/non-ext UC split branch(es) at line(s) {bad} whose "
        f"else (non-extension fallback) does NOT call pytest.xfail. Unwired scenarios "
        f"routed into a harness env hard-fail instead of xfailing (see salesagent-j4bo). "
        f"Restore: else: pytest.xfail('UC-XXX harness not yet wired for non-extension scenarios')."
    )


# --- Meta-tests: verify the guard logic itself ---

_GOOD_SNIPPET = '''
def _harness_env(request, ctx):
    if uc == "UC-003":
        marker_names = {m.name for m in request.node.iter_markers()}
        if any(t.startswith("T-UC-003-ext-") for t in marker_names):
            with MediaBuyDualEnv() as env:
                ctx["env"] = env
                yield
        else:
            pytest.xfail("UC-003 harness not yet wired for non-extension scenarios")
'''

_BAD_SNIPPET = '''
def _harness_env(request, ctx):
    if uc == "UC-003":
        marker_names = {m.name for m in request.node.iter_markers()}
        if any(t.startswith("T-UC-003-ext-") for t in marker_names):
            with MediaBuyDualEnv() as env:
                ctx["env"] = env
                yield
        else:
            with MediaBuyDualEnv() as env:
                ctx["env"] = env
                yield
'''


def test_guard_negative_accepts_xfail_fallback():
    """Meta: a branch whose else calls pytest.xfail passes the guard."""
    assert _violations(_GOOD_SNIPPET) == []
    # And the anchor pattern is detected (not vacuous).
    assert _find_ext_branches(_get_harness_env(_GOOD_SNIPPET))


def test_guard_positive_catches_env_fallback():
    """Meta: a branch whose else yields a harness env (the bug) is caught."""
    bad = _violations(_BAD_SNIPPET)
    assert bad, "Guard failed to catch an ext-split else that routes into a harness env"
