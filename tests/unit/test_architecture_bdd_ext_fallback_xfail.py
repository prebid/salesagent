"""Guard: every UC with an ext-split routing row must also carry a not-wired row.

Recurrence-prevention guard. Twice a catch-all else-branch in the BDD harness
dispatcher (tests/bdd/conftest.py::_harness_env) had its honest
`pytest.xfail("... not yet wired ...")` guard deleted and replaced with a route
into a harness env — flipping hundreds of UNWIRED scenarios from xfail to hard
fail. Because `make quality` does not run BDD, the regression was invisible to
the normal gate.

  - UC-002: fixed in commit 655ba1f56
  - UC-003: fixed in #1417 (this guard's motivating bug)
  - UC-027: `@sibling-principal` membership split (#1812 / #1945) — same disease,
    different syntactic form than the historical `-ext-` startswith splits

RESTRUCTURED FOR LANE F, as this guard's own failure
message instructed. The `if/elif/else` chain it used to walk is gone: routing is
now a declarative table of `EnvRoute` rows resolved by
`storyboard_spec.resolve_env_route`, so the extension split is a row carrying a
`when` predicate and the fallback is a row carrying an `xfail_reason`.

The INVARIANT is unchanged, and so is the bug it catches. Before: an `else` that
yielded a harness env instead of xfailing. Now: a UC whose ext-split row exists
while its not-wired row has been deleted — which would route every unwired
scenario in that UC to a real env and flip it from xfail to hard fail, exactly
as before.

Detection stays AST over the conftest source (never an import of the live table),
so a row deleted in the source is caught even if some other module happens to
reconstruct an equivalent table at runtime.

"""

import ast
from pathlib import Path

_CONFTEST = Path(__file__).resolve().parents[2] / "tests" / "bdd" / "conftest.py"

#: A row's predicate splits on extension scenarios when it tests a tag prefix of
#: this shape — the same syntactic signal the previous version anchored on.
_EXT_PREFIX_FRAGMENT = "-ext-"


def _route_rows(source: str) -> list[ast.Call]:
    """Every ``EnvRoute(...)`` construction in the routing table."""
    tree = ast.parse(source)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "EnvRoute"
    ]


def _kwarg(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _uc_of(call: ast.Call) -> str | None:
    """The UC a row is scoped to, read off its ``_uc("UC-00N", ...)`` predicate."""
    when = _kwarg(call, "when")
    if not isinstance(when, ast.Call) or not isinstance(when.func, ast.Name) or when.func.id != "_uc":
        return None
    if not when.args or not isinstance(when.args[0], ast.Constant):
        return None
    value = when.args[0].value
    return value if isinstance(value, str) else None


def _splits_on_ext(call: ast.Call) -> bool:
    """Does this row's predicate branch on an extension-tag prefix?"""
    when = _kwarg(call, "when")
    if when is None:
        return False
    return any(
        isinstance(node, ast.Constant) and isinstance(node.value, str) and _EXT_PREFIX_FRAGMENT in node.value
        for node in ast.walk(when)
    )


def _has_xfail_reason(call: ast.Call) -> bool:
    reason = _kwarg(call, "xfail_reason")
    return reason is not None


def _ucs_missing_not_wired_row(source: str) -> list[str]:
    """UCs that split on ext scenarios but have no xfail-carrying fallback row."""
    rows = _route_rows(source)
    ext_ucs = {uc for call in rows if _splits_on_ext(call) and (uc := _uc_of(call)) is not None}
    fallback_ucs = {uc for call in rows if _has_xfail_reason(call) and (uc := _uc_of(call)) is not None}
    return sorted(ext_ucs - fallback_ucs)


def test_ext_split_ucs_keep_a_not_wired_row():
    """Production conftest: every ext-splitting UC still xfails its unwired scenarios."""
    source = _CONFTEST.read_text()

    # Sanity: the pattern actually exists (the guard is not vacuously passing).
    rows = _route_rows(source)
    assert rows, "No EnvRoute rows found in conftest — the routing table's shape changed again."
    ext_rows = [call for call in rows if _splits_on_ext(call)]
    assert ext_rows, (
        "No ext-splitting routing rows found — the guard's anchor pattern disappeared. "
        "If routing was restructured again, update this guard."
    )

    missing = _ucs_missing_not_wired_row(source)
    assert missing == [], (
        f"{missing} split on extension scenarios but carry no row with an xfail_reason. "
        "Every unwired scenario in those UCs would route into a real harness env and hard-fail "
        "instead of xfailing — the exact regression this guard exists to prevent."
    )


def test_guard_catches_a_deleted_fallback_row():
    """Negative meta-test: removing a UC's not-wired row must be detected."""
    source = """
ENV_ROUTES = [
    EnvRoute(
        tag="uc002-ext",
        when=_uc("UC-002", lambda m: any(t.startswith("T-UC-002-ext-") for t in m)),
        env_builder=_env("x.Y"),
    ),
]
"""
    assert _ucs_missing_not_wired_row(source) == ["UC-002"]


def test_guard_passes_when_the_fallback_row_is_present():
    """Positive meta-test: the ext row plus its fallback row is the healthy shape."""
    source = """
ENV_ROUTES = [
    EnvRoute(
        tag="uc002-ext",
        when=_uc("UC-002", lambda m: any(t.startswith("T-UC-002-ext-") for t in m)),
        env_builder=_env("x.Y"),
    ),
    EnvRoute(
        tag="uc002-not-wired",
        when=_uc("UC-002", lambda m: True),
        env_builder=_env("x.Y"),
        xfail_reason="UC-002 harness not yet wired for non-extension scenarios",
    ),
]
"""
    assert _ucs_missing_not_wired_row(source) == []
