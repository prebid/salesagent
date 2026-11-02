"""Guard: the BDD step classifier answers "does this grade" by FOLLOWING THE CALL.

``scripts/audit/build_bdd_decisions.py`` is the instrument salesagent-b9hi1's scope was
read off — 337 sites, 174 bare-truthiness, 65 grading nothing. It decided "delegates to
an asserting helper" by NAME PREFIX::

    delegates = any(c.startswith(("assert_", "require_", "expect_", "verify_", "check_")) ...)

That is a proxy for "this call asserts", and it is wrong in the direction that inflates
the finding. Measured on the no-creative-literal half of the tree while working
salesagent-b341x.7: of 49 sites reading as "grades nothing", every one reached an
assertion — most through ``_expect_flag``, ``wire_dict``, ``wire_field`` or
``_assert_billing_party_array``, none of which match the prefix list. Of 205 flagged
sites in that half, five were real defects (salesagent-v03pe.4).

WHAT THIS FILE PINS, one test per obligation. The fixtures are synthetic on purpose: a
guard that reads the live tree passes for whatever the tree happens to contain today,
and the point is the CLASSIFIER's behaviour, not this week's counts.

1. THE VERDICT HAS THREE STATES and UNDECIDABLE is one of them, never folded into
   either side. An instrument that files what it cannot evaluate under "fine" is the
   same defect as one that files it under "broken".
2. RESOLUTION IS TRANSITIVE, and the name is not consulted. The two directions are
   tested separately, because a classifier can be right about one and wrong about the
   other: a badly-named helper that DOES assert must read GRADES, and a
   well-named helper that does NOT assert must not.
3. AN ANTI-VACUITY GUARD IS NOT A DEFECT. ``assert rows, "expected a non-empty array"``
   in front of ``assert returned == expected`` is good practice and dominated the class
   it used to inflate.
4. A COMPARISON IS A COMPARISON WHEREVER IT SITS in the test expression.
   ``assert any(kw in text for kw in KEYWORDS)`` is an ``ast.Call`` at its top node; a
   top-node match read it as truthiness. Found by spot-checking this classifier's own
   first output.
5. GRADING CLASSES ARE THEN-ONLY. A ``when`` step asserting ``account_id`` before
   navigating is a setup guard; grading is not its job. Also found by spot-check.
6. CYCLES TERMINATE.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "audit" / "build_bdd_decisions.py"


def _load():
    spec = importlib.util.spec_from_file_location("build_bdd_decisions", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bdd():
    return _load()


def _fn(source: str) -> ast.FunctionDef:
    """The first function defined in *source*."""
    return next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef))


def test_verdict_has_exactly_three_states_and_undecidable_is_one(bdd):
    """A two-state verdict is what let 'cannot tell' print as 'grades nothing'."""
    assert {bdd.GRADES, bdd.GRADES_NOTHING, bdd.UNDECIDABLE} == {"GRADES", "GRADES_NOTHING", "UNDECIDABLE"}
    assert bdd.GRADES != bdd.GRADES_NOTHING != bdd.UNDECIDABLE


def test_a_step_that_asserts_directly_grades(bdd):
    assert bdd.assertion_verdict(_fn("def s(ctx):\n    assert ctx['a'] == 1\n")) == bdd.GRADES


def test_a_step_that_raises_grades(bdd):
    """A conditional ``raise AssertionError`` is an assertion by another spelling."""
    src = "def s(ctx):\n    if ctx['a'] != 1:\n        raise AssertionError('no')\n"
    assert bdd.assertion_verdict(_fn(src)) == bdd.GRADES


def test_an_empty_step_grades_nothing_and_that_absence_is_proven(bdd):
    assert bdd.assertion_verdict(_fn("def s(ctx):\n    pass\n")) == bdd.GRADES_NOTHING


def test_a_helper_whose_name_matches_no_prefix_still_makes_the_step_grade(bdd, monkeypatch):
    """THE REGRESSION. ``_expect_flag``/``wire_field``/``wire_dict`` all assert and all
    read as non-grading under the prefix rule. The name must not be consulted."""
    helper = _fn("def _expect_flag(ctx, path, expected):\n    assert ctx[path] == expected\n")
    monkeypatch.setattr(bdd, "_definitions", lambda: {"_expect_flag": [helper]})
    step = _fn("def s(ctx):\n    _expect_flag(ctx, 'a', True)\n")
    assert bdd.assertion_verdict(step) == bdd.GRADES


def test_a_helper_named_like_an_assertion_that_does_not_assert_does_not_make_it_grade(bdd, monkeypatch):
    """The other direction of the same proxy, and the one that would hide a real defect."""
    helper = _fn("def assert_everything_is_fine(ctx):\n    return True\n")
    monkeypatch.setattr(bdd, "_definitions", lambda: {"assert_everything_is_fine": [helper]})
    step = _fn("def s(ctx):\n    assert_everything_is_fine(ctx)\n")
    assert bdd.assertion_verdict(step) == bdd.GRADES_NOTHING


def test_resolution_is_transitive_through_a_chain(bdd, monkeypatch):
    outer = _fn("def outer(ctx):\n    inner(ctx)\n")
    inner = _fn("def inner(ctx):\n    assert ctx['a'] == 1\n")
    monkeypatch.setattr(bdd, "_definitions", lambda: {"outer": [outer], "inner": [inner]})
    assert bdd.assertion_verdict(_fn("def s(ctx):\n    outer(ctx)\n")) == bdd.GRADES


def test_an_unresolvable_callee_is_undecidable_not_grades_nothing(bdd, monkeypatch):
    """The whole point of the third state: a helper this scanner cannot follow is a
    question, and reporting it as a defect is how a proxy manufactures a population."""
    monkeypatch.setattr(bdd, "_definitions", dict)
    assert bdd.assertion_verdict(_fn("def s(ctx):\n    some_import_we_cannot_follow(ctx)\n")) == bdd.UNDECIDABLE


def test_inert_builtins_are_not_treated_as_unresolved(bdd, monkeypatch):
    """Otherwise every step reads UNDECIDABLE and the instrument says nothing at all."""
    monkeypatch.setattr(bdd, "_definitions", dict)
    assert bdd.assertion_verdict(_fn("def s(ctx):\n    len(ctx)\n    sorted(ctx)\n")) == bdd.GRADES_NOTHING


def test_a_cycle_terminates(bdd, monkeypatch):
    a = _fn("def a(ctx):\n    b(ctx)\n")
    b = _fn("def b(ctx):\n    a(ctx)\n")
    monkeypatch.setattr(bdd, "_definitions", lambda: {"a": [a], "b": [b]})
    assert bdd.assertion_verdict(_fn("def s(ctx):\n    a(ctx)\n")) in {bdd.GRADES_NOTHING, bdd.UNDECIDABLE}


def test_a_guard_in_front_of_a_comparison_is_not_presence_only(bdd):
    """The anti-vacuity guard that dominated the 174."""
    src = "def s(ctx):\n    rows = ctx['rows']\n    assert rows, 'expected a non-empty array'\n    assert rows == [1]\n"
    fn = _fn(src)
    asserts = [n for n in ast.walk(fn) if isinstance(n, ast.Assert)]
    assert any(bdd._is_comparison(a.test) for a in asserts), "guard-plus-comparison must read as comparing"


def test_a_membership_test_inside_a_call_counts_as_a_comparison(bdd):
    """``assert any(kw in text for kw in KEYWORDS)`` — an ast.Call at the top node."""
    fn = _fn("def s(ctx):\n    assert any(k in ctx['t'] for k in ('a', 'b'))\n")
    a = next(n for n in ast.walk(fn) if isinstance(n, ast.Assert))
    assert bdd._is_comparison(a.test)


def test_a_bare_truthiness_assert_is_not_a_comparison(bdd):
    fn = _fn("def s(ctx):\n    assert ctx['rows']\n")
    a = next(n for n in ast.walk(fn) if isinstance(n, ast.Assert))
    assert not bdd._is_comparison(a.test)


def test_callee_asserts_ignores_the_callers_own_assert(bdd, monkeypatch):
    """``assertion_verdict`` short-circuits on the caller's own assert, so the
    presence-only class needs the other question. Asking the first one empties the
    class to zero, which is how this was caught."""
    monkeypatch.setattr(bdd, "_definitions", dict)
    assert not bdd._callee_asserts(_fn("def s(ctx):\n    assert ctx['rows']\n"))


def test_the_live_tree_reports_no_class_it_cannot_defend(bdd):
    """Integration check on the real tree: whatever the counts are, every class must be
    a list of locations, UNDECIDABLE must be reported separately from GRADES_NOTHING,
    and the two must never be summed anywhere in the output."""
    found = bdd.collect()

    # The two verdicts are reported under SEPARATE keys. If a future edit merges them,
    # the count that made this epic's scope becomes unauditable again.
    decision_keys = {key for key, _title, _why, _ask in bdd.DECISIONS}
    assert "then-grades-nothing" in decision_keys
    assert "then-undecidable" in decision_keys
    assert not (set(found["then-grades-nothing"]) & set(found["then-undecidable"])), (
        "a site is either proven to grade nothing or undecidable, never counted as both"
    )

    # Every emitted finding carries a location and its evidence, so a reader can check it
    # without re-running the scan.
    for key, items in found.items():
        for loc, snippet in items:
            assert ":" in loc, f"{key} location is not file:line — {loc!r}"
            assert isinstance(snippet, str) and snippet, f"{key} carries no evidence at {loc}"


# ── Shadow classification: a shadow only competes if both modules share a scope ──


def test_two_globally_registered_modules_compete(bdd, monkeypatch):
    """The real hazard: registration order decides which body runs."""
    monkeypatch.setattr(
        bdd, "_registration_scopes", lambda: {"a.py": frozenset({"GLOBAL"}), "b.py": frozenset({"GLOBAL"})}
    )
    assert bdd.classify_shadow(["a.py", "b.py"]) == "COMPETING"


def test_a_global_and_a_locally_scoped_module_is_a_deliberate_override(bdd, monkeypatch):
    """uc019's arrangement: it star-imports its steps into ONE test module so its
    redefinitions apply to its own scenarios and no other UC's."""
    monkeypatch.setattr(
        bdd,
        "_registration_scopes",
        lambda: {"generic.py": frozenset({"GLOBAL"}), "uc.py": frozenset({"test_uc.py"})},
    )
    assert bdd.classify_shadow(["generic.py", "uc.py"]) == "SCOPED_OVERRIDE"


def test_an_unregistered_module_cannot_compete(bdd, monkeypatch):
    """then_media_buy.py is registered nowhere on purpose, so its definitions never run
    and registration order decides nothing."""
    monkeypatch.setattr(bdd, "_registration_scopes", lambda: {"live.py": frozenset({"GLOBAL"})})
    assert bdd.classify_shadow(["live.py", "helper.py"]) == "NOT_REGISTERED"


def test_two_modules_local_to_the_SAME_test_module_compete(bdd, monkeypatch):
    """Scoping only helps when the scopes differ — same scope is the hazard again."""
    monkeypatch.setattr(
        bdd,
        "_registration_scopes",
        lambda: {"a.py": frozenset({"test_x.py"}), "b.py": frozenset({"test_x.py"})},
    )
    assert bdd.classify_shadow(["a.py", "b.py"]) == "COMPETING"
