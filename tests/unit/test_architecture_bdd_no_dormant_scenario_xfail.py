"""Guard: no scenario-specific imperative ``pytest.xfail()`` in the BDD harness.

Recurrence-prevention guard, complementary to
``test_architecture_bdd_ext_fallback_xfail`` (which *requires* ``pytest.xfail``
on the catch-all ``else`` of a UC dispatch branch). This guard forbids the
opposite shape: an ``if``/``elif`` in ``tests/bdd/conftest.py::_harness_env``
that singles out a *specific* ``@T-UC-*`` scenario tag and whose body only
``pytest.xfail(...)``s or ``pytest.skip(...)``s (no ``yield`` into a harness env).

Why the shape is a defect:
  * An imperative ``pytest.xfail()``/``pytest.skip()`` raised in the autouse
    ``_harness_env`` fixture aborts the scenario at SETUP, before any ``@then``
    step runs — so the assertion-strength guards (which scan step-def source) are
    structurally blind to it.
  * Unlike a declarative ``@pytest.mark.xfail(strict=True)`` marker, an imperative
    ``pytest.xfail()`` (or ``pytest.skip()``) can never XPASS/FAIL, so when the
    underlying behavior becomes gradable the scenario stays green-dormant instead
    of flipping to a failure that forces the gate to be removed.
  * ``make quality`` does not run BDD, so the whole class is invisible to the
    normal gate. The idiom already appears ~10x in ``_harness_env``; a new
    dormant scenario is a 2-line copy-paste.

The correct home for a specific expected-to-fail scenario is the declarative
strict-xfail registry consumed by ``pytest_collection_modifyitems`` (see
``_UC002_VALIDATION_XFAIL`` in ``tests/bdd/conftest.py``): it runs the scenario
body and flips XPASS -> FAILED when the wiring lands.

Catch-all ``else`` fallbacks and family gates (``any(t.startswith("T-UC-...")``)
are the honest "this whole family isn't wired" state and are NOT flagged.

This is a syntactic (AST) guard, so positive + negative meta-tests suffice.
"""

import ast
from pathlib import Path

from tests.unit._architecture_helpers import assert_violations_match_allowlist, iter_call_expressions

_CONFTEST = Path(__file__).resolve().parents[1] / "bdd" / "conftest.py"
_TARGET_FUNC = "_harness_env"

# Shrink-only allowlist of scenario tags currently gated by an imperative
# dormant xfail, keyed by TAG (not line — line keys break on formatter shifts).
# Each entry is a real dormant scenario pending harness wiring; remove it when
# the scenario is migrated to the declarative strict-xfail registry or wired.
# FIXME(#1652): wire this validation scenario through the real harness.
_KNOWN_DORMANT = frozenset(
    {
        "T-UC-018-ext-c",
    }
)


def _get_harness_env(source: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == _TARGET_FUNC:
            return node
    raise AssertionError(f"{_TARGET_FUNC} not found")


def _prefix_match_constants(test: ast.expr) -> set[str]:
    """Tag constants used as a ``.startswith()``/``.endswith()`` argument.

    These are FAMILY gates (e.g. ``any(t.startswith("T-UC-011-") ...)``), not a
    specific-scenario match, so they are excluded from the specific-tag set.
    """
    out: set[str] = set()
    for call in iter_call_expressions(test):
        func = call.func
        if isinstance(func, ast.Attribute) and func.attr in ("startswith", "endswith"):
            for arg in call.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.add(arg.value)
    return out


def _specific_scenario_tags(test: ast.expr) -> set[str]:
    """Specific ``T-UC-*`` scenario tags referenced by an if/elif test.

    Captures the forms whose tags appear as INLINE string literals in the test:
    ``"T-UC-x" in names``, ``names == "T-UC-x"``, ``names & {"T-UC-x"}``,
    ``names in {"T-UC-x", ...}`` — while excluding ``startswith`` family gates.

    KNOWN LIMITATION (FIXME(#1652)): membership against a NAMED set constant
    (``marker_names & _UC0XX_DORMANT_TAGS`` — the prevailing idiom elsewhere in
    ``_harness_env``) is NOT resolved: the tags live in a module-level assignment,
    not the test expression, so a dormant branch gated by a named set evades this
    guard. Full module-level set-constant resolution (plus triage of the existing
    named-set xfail branches) is tracked as guard hardening. Until then the guard
    covers the inline-literal + ``skip``/``xfail`` forms — which is how the
    recurrence it targets (a copy-pasted inline dormant branch) is introduced.
    """
    excluded = _prefix_match_constants(test)
    tags: set[str] = set()
    for node in ast.walk(test):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("T-UC-")
            and node.value not in excluded
        ):
            tags.add(node.value)
    return tags


_DORMANCY_MARKERS = ("xfail", "skip")


def _body_calls_dormancy_marker(body: list[ast.stmt]) -> bool:
    """True if the body imperatively terminates the scenario as dormant.

    Both ``pytest.xfail()`` AND ``pytest.skip()`` qualify: each aborts the
    scenario at setup, before any ``@then`` step, and can never surface as
    FAILED/XPASS — so both leave the scenario green-dormant. ``pytest.skip()``
    is the *documented* idiom for "no harness" (conftest.py:88), which is exactly
    why a specific-tag skip is the same recurrence risk as a specific-tag xfail.
    """
    for stmt in body:
        for call in iter_call_expressions(stmt):
            func = call.func
            if isinstance(func, ast.Attribute) and func.attr in _DORMANCY_MARKERS:
                return True
            if isinstance(func, ast.Name) and func.id in _DORMANCY_MARKERS:
                return True
    return False


def _has_yield_in_scope(node: ast.AST) -> bool:
    """Yield/YieldFrom in THIS scope, NOT descending into a nested def/lambda.

    A ``yield`` inside a nested generator or lambda defined within a dormant
    branch belongs to that inner scope — it does not make the branch itself a
    real harness-env generator, so it must not rescue the branch from the guard.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(child, (ast.Yield, ast.YieldFrom)):
            return True
        if _has_yield_in_scope(child):
            return True
    return False


def _body_has_yield(body: list[ast.stmt]) -> bool:
    for stmt in body:
        # A nested def/lambda opens its own scope — its yields are not the
        # branch's, so a nested generator must not rescue a dormant branch.
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if _has_yield_in_scope(stmt):
            return True
    return False


def _violations(source: str) -> list[tuple[int, str]]:
    """(lineno, tag) for each if/elif that singles out a specific scenario tag
    and whose body only ``pytest.xfail()``s (no ``yield`` into a harness env)."""
    func = _get_harness_env(source)
    bad: list[tuple[int, str]] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.If):
            continue
        tags = _specific_scenario_tags(node.test)
        if not tags:
            continue
        if _body_calls_dormancy_marker(node.body) and not _body_has_yield(node.body):
            for tag in sorted(tags):
                bad.append((node.lineno, tag))
    return bad


def test_no_new_dormant_scenario_specific_xfail():
    """conftest: no NEW scenario-specific imperative dormant xfail beyond the allowlist."""
    source = _CONFTEST.read_text()

    # Anchor: the dispatcher exists and has marker-membership branches to scan
    # (guards against a rename/restructure making this pass vacuously).
    func = _get_harness_env(source)
    membership_ifs = [
        n
        for n in ast.walk(func)
        if isinstance(n, ast.If)
        and any(isinstance(name, ast.Name) and name.id == "marker_names" for name in ast.walk(n.test))
    ]
    assert membership_ifs, (
        "_harness_env has no `marker_names` membership branches — the dispatcher was "
        "restructured; update this guard's anchor."
    )

    # Keyed by tag (not line — line keys break on formatter shifts). The helper
    # reports NEW violations and STALE allowlist entries in one assertion.
    found = {(tag,) for _, tag in _violations(source)}
    allowlist = {(tag,) for tag in _KNOWN_DORMANT}
    assert_violations_match_allowlist(
        found,
        allowlist,
        fix_hint=(
            "A scenario-specific imperative pytest.xfail() aborts the scenario at setup "
            "(before any @then step) and can never XPASS — it stays green-dormant. Move the "
            "tag to the declarative strict-xfail registry consumed by pytest_collection_modifyitems "
            "(e.g. _UC002_VALIDATION_XFAIL), which runs the body and flips XPASS->FAILED when "
            "wired, OR wire the scenario into its harness env."
        ),
    )


# --- Meta-tests: verify the guard logic itself ---

_POS_IN = """
def _harness_env(request, ctx):
    marker_names = {m.name for m in request.node.iter_markers()}
    if uc == "UC-002":
        if marker_names & {"account-ref"}:
            with Env() as env:
                ctx["env"] = env
                yield
        elif "T-UC-002-inv-015-6" in marker_names:
            pytest.xfail("T-UC-002-inv-015-6 harness wiring is tracked in #1652")
        else:
            pytest.xfail("UC-002 harness not yet wired for non-extension scenarios")
"""

_POS_SET = """
def _harness_env(request, ctx):
    marker_names = {m.name for m in request.node.iter_markers()}
    if uc == "UC-018":
        if marker_names & {"concept-id"}:
            with Env() as env:
                ctx["env"] = env
                yield
        elif marker_names & {"T-UC-018-ext-c"}:
            pytest.xfail("T-UC-018-ext-c harness wiring is tracked in #1652")
        else:
            pytest.xfail("UC-018 harness wired only for a few scenarios")
"""

_POS_EQ = """
def _harness_env(request, ctx):
    marker = next(iter(request.node.iter_markers())).name
    if marker == "T-UC-099-foo":
        pytest.xfail("dormant")
    else:
        pytest.xfail("family not wired")
"""

_NEG_CATCHALL = """
def _harness_env(request, ctx):
    marker_names = {m.name for m in request.node.iter_markers()}
    if uc == "UC-002":
        if marker_names & {"account-ref"}:
            with Env() as env:
                ctx["env"] = env
                yield
        else:
            pytest.xfail("UC-002 harness not yet wired for non-extension scenarios")
"""

_NEG_WIRED = """
def _harness_env(request, ctx):
    marker_names = {m.name for m in request.node.iter_markers()}
    if uc == "UC-002":
        if "T-UC-002-inv-015-6" in marker_names:
            with Env() as env:
                ctx["env"] = env
                yield
        else:
            pytest.xfail("not wired")
"""

_NEG_FAMILY_STARTSWITH = """
def _harness_env(request, ctx):
    marker_names = {m.name for m in request.node.iter_markers()}
    if uc == "UC-011":
        if any(t.startswith("T-UC-011-") for t in marker_names):
            pytest.xfail("UC-011 family not yet wired")
        else:
            with Env() as env:
                ctx["env"] = env
                yield
"""

_POS_SKIP = """
def _harness_env(request, ctx):
    marker_names = {m.name for m in request.node.iter_markers()}
    if uc == "UC-002":
        if marker_names & {"account-ref"}:
            with Env() as env:
                ctx["env"] = env
                yield
        elif "T-UC-002-inv-042-1" in marker_names:
            pytest.skip("T-UC-002-inv-042-1 harness wiring is tracked in #1652")
        else:
            pytest.xfail("UC-002 harness not yet wired for non-extension scenarios")
"""

_POS_XFAIL_NESTED_YIELD = """
def _harness_env(request, ctx):
    marker_names = {m.name for m in request.node.iter_markers()}
    if "T-UC-055-foo" in marker_names:
        def _inner_gen():
            yield 1
        pytest.xfail("dormant — the nested generator's yield must not rescue this branch")
    else:
        with Env() as env:
            ctx["env"] = env
            yield
"""

_NEG_ENV_SKIP = """
def _harness_env(request, ctx):
    marker_names = {m.name for m in request.node.iter_markers()}
    if uc == "UC-007":
        if e2e_config is None:
            pytest.skip("e2e stack not available")
        with Env() as env:
            ctx["env"] = env
            yield
"""


def test_guard_flags_in_membership_form():
    assert [tag for _, tag in _violations(_POS_IN)] == ["T-UC-002-inv-015-6"]


def test_guard_flags_set_intersection_form():
    assert [tag for _, tag in _violations(_POS_SET)] == ["T-UC-018-ext-c"]


def test_guard_flags_equality_form():
    assert [tag for _, tag in _violations(_POS_EQ)] == ["T-UC-099-foo"]


def test_guard_ignores_catchall_else():
    assert _violations(_NEG_CATCHALL) == []


def test_guard_ignores_wired_specific_branch():
    assert _violations(_NEG_WIRED) == []


def test_guard_ignores_startswith_family_gate():
    assert _violations(_NEG_FAMILY_STARTSWITH) == []


def test_guard_flags_skip_body():
    """A specific-tag branch whose body only ``pytest.skip()``s is dormant too."""
    assert [tag for _, tag in _violations(_POS_SKIP)] == ["T-UC-002-inv-042-1"]


def test_guard_flags_dormant_branch_with_nested_generator():
    """A nested ``def`` that yields must NOT rescue a dormant xfail branch."""
    assert [tag for _, tag in _violations(_POS_XFAIL_NESTED_YIELD)] == ["T-UC-055-foo"]


def test_guard_ignores_env_availability_skip():
    """A ``pytest.skip()`` with no specific ``T-UC-*`` tag (env-availability guard)
    is not flagged — adding ``skip`` to the matcher introduces no false positive."""
    assert _violations(_NEG_ENV_SKIP) == []
