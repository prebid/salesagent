"""Guard: BDD Then steps must make meaningful assertions, not just truthiness checks.

A Then step that only does ``assert ctx.get("response")`` checks existence but
not correctness. Meaningful assertions use comparisons (==, !=, is, in),
isinstance checks, or call helper functions that contain real assertions.

**Rule**: Every @then step must contain at least one "meaningful" assertion:
  - ``assert x == y`` / ``assert x != y`` / ``assert x is y`` / ``assert x is not y``
  - ``assert x in y`` / ``assert x not in y``
  - ``assert isinstance(x, Y)``
  - ``assert len(x) == n`` (comparison, not bare ``assert len(x)``)
  - Or delegate to a helper function (function call in body)

A bare ``assert expr`` without a comparison operator is "trivial" — it only
checks truthiness, not a specific expected value.

**Second rule — REACHABILITY**: a Then must not merely CONTAIN a meaningful
assertion, it must not be able to RETURN without executing one.

Containment was not enough. ``then_no_dry_run_include`` held
``assert dry_run is None`` — a meaningful comparison, so the presence rule
passed it — with the whole body under ``if resp is not None:`` and no ``else``.
On an errored dispatch the payload is None, the body is skipped, and the step
returns having asserted nothing. That reads in a report exactly like a scenario
that verified its claim (GH #1802).

The check is accessor-agnostic on purpose: it looks at control flow, not at
which reader produced the value, so it catches the same shape whether the guard
came from ``payload_or_none``, ``ctx.get(...)`` or ``hasattr``. Legislating
which accessor a step chose would be a style rule, and a step that dereferences
a lenient reader already fails — just with a worse message.

**Two stated limits.** Both are boundaries of what control flow can decide, not
oversights, and naming them is cheaper than a reader discovering them:

1. A step that takes SOME verdict and then returns early past its OWN claim is
   not caught — ``resp = require_payload(ctx)`` already raises on an absent
   payload, so a later ``if not resp.accounts: return`` leaves the rule
   satisfied while the specific claim goes ungraded. Separating "a verdict" from
   "the verdict this step's text promises" needs the step text, not the AST.
2. A ``for`` body counts as terminating, which is a deliberate limit rather
   than an oversight: a loop over an empty collection also returns without
asserting, but that is a distinct shape with its own census (15 sites, filed
separately) and folding it in would have widened this sweep from 10 sites to 25.
Removing ``ast.For`` from the terminator set below is the one-line
generalization once those are fixed (GH: filed as its own bug).

What the rule DOES catch is the shape that produced GH #1802's finding and is
proven by mutation in this module's own history: every assertion under an ``if``
with no ``else``, at any nesting depth, whatever accessor produced the value.

"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import iter_call_expressions
from tests.unit._bdd_guard_helpers import BDD_STEPS_DIR, iter_bdd_steps


def _assert_is_meaningful(assert_node: ast.Assert) -> bool:
    """Check if an assert statement makes a meaningful comparison.

    Meaningful: assert x == y, assert isinstance(...), assert x in y
    Trivial: assert x, assert ctx.get("foo"), assert len(items)
    """
    test = assert_node.test

    # Compare: assert x == y, assert x != y, etc.
    if isinstance(test, ast.Compare):
        return True

    # isinstance/issubclass call
    if isinstance(test, ast.Call):
        func = test.func
        if isinstance(func, ast.Name) and func.id in ("isinstance", "issubclass", "hasattr"):
            return True

    # UnaryOp: assert not x — check inner
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        # assert not x is trivial, but assert not isinstance(...) is odd.
        # Treat as trivial unless the operand is a Compare.
        if isinstance(test.operand, ast.Compare):
            return True

    # BoolOp: assert x and y — meaningful if ANY operand is meaningful
    if isinstance(test, ast.BoolOp):
        for value in test.values:
            fake_assert = ast.Assert(test=value, msg=None)
            if _assert_is_meaningful(fake_assert):
                return True

    return False


def _has_meaningful_assertion_or_delegation(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function has at least one meaningful assertion or delegates to a helper."""
    has_any_assert = False

    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            has_any_assert = True
            if _assert_is_meaningful(node):
                return True
        if isinstance(node, ast.Raise):
            return True

    for node in iter_call_expressions(func):
        func_node = node.func
        # Placeholder helpers do not implement assertions.
        if isinstance(func_node, ast.Name) and func_node.id == "_pending":
            continue
        # Exclude ctx.get(), ctx.setdefault() etc — these are data access, not assertions
        if isinstance(func_node, ast.Attribute) and isinstance(func_node.value, ast.Name):
            if func_node.value.id == "ctx":
                continue
        # Exclude getattr, str, type, etc — builtins used for data extraction
        if isinstance(func_node, ast.Name) and func_node.id in (
            "getattr",
            "str",
            "type",
            "len",
            "list",
            "dict",
            "set",
            "print",
        ):
            continue
        return True

    # If we found asserts but none were meaningful, it's trivial
    if has_any_assert:
        return False

    # No asserts and no delegation — trivial
    return False


# Calls that END a path without an assert: an explicit xfail/fail/skip is a
# deliberate verdict, not a silent fall-through.
_TERMINAL_CALLS = frozenset({"xfail", "fail", "skip"})

# Data extraction, not delegation — a call to one of these does not grade
# anything. Mirrors the exclusions the presence rule already makes.
_NON_ASSERTING_CALLS = frozenset(
    {"getattr", "str", "type", "len", "list", "dict", "set", "print", "int", "float", "sorted", "any", "all"}
)


def _has_a_skippable_branch(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Does this step contain an ``if`` at all?

    The reachability rule is about a path that SKIPS the assertions. A
    straight-line body has no such path: every statement runs, so whatever the
    presence rule accepted is executed. Restricting to branching steps is what
    keeps the rule aimed at the disease instead of at delegation — a one-line
    ``then_error_recovery_hint_correctable(ctx)`` that forwards to another step,
    or a ``with pytest.raises(...):`` whose verdict IS the context manager, has
    no skippable path to worry about and must not be flagged for lacking one.
    """
    return any(isinstance(n, ast.If) for n in ast.walk(func))


def _is_asserting_helper_name(name: str) -> bool:
    """Does this callee name promise an assertion by convention?

    Module-local resolution cannot see across an import, and this codebase
    factors shared verdicts into other modules —
    ``assert_audit_logged(ctx, ...)`` in ``_outcome_helpers``,
    ``assert_envelope_shape(...)`` in ``tests.helpers``. Without this a step
    that delegates correctly is flagged for having no reachable assertion, which
    would punish exactly the steps that share their contract instead of copying
    it.

    ``require_*`` counts for the same reason and is the same promise stated
    differently: ``require_payload`` RAISES when the value is absent, which is
    exactly the verdict this rule is looking for. The two prefixes are the
    codebase's existing vocabulary, not a category invented here.

    The convention is load-bearing rather than cosmetic: a helper named
    ``assert_*`` or ``require_*`` that neither asserts nor raises is its own
    defect, and one the presence rule above already grades wherever such a
    helper is a step.
    """
    return name.startswith(("assert_", "_assert_", "require_", "_require_"))


def _asserting_helpers(tree: ast.Module) -> frozenset[str]:
    """Module-level functions whose own body contains an assert, raise or xfail.

    Resolved from the source rather than guessed from the name, so a step that
    factors its verdict into a helper still counts — and a READER like
    ``payload_or_none`` or ``ctx.get`` does not, because it asserts nothing.
    """
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, (ast.Assert, ast.Raise)):
                names.add(node.name)
                break
            if isinstance(inner, ast.Call):
                called = getattr(inner.func, "attr", getattr(inner.func, "id", ""))
                if called in _TERMINAL_CALLS:
                    names.add(node.name)
                    break
    return frozenset(names)


def _executes_an_assertion(stmts: list[ast.stmt], asserting_helpers: frozenset[str]) -> bool:
    """Does EVERY path through *stmts* execute an assertion (or an explicit verdict)?

    Walks the statement list in order and stops at the first statement that
    terminates every path through it. Recurses into nested suites, so an assert
    at any depth on the otherwise-silent path counts.

    An ``if`` without an ``else`` never terminates: that is precisely the shape
    this rule exists to catch. An ``if``/``else`` terminates only when BOTH branches
    do. A ``return`` reached before any assertion is a silent exit — it makes
    the whole list non-terminating, which is what turns an early ``return`` in a
    guard branch into a violation rather than a loophole.
    """
    for stmt in stmts:
        if isinstance(stmt, (ast.Assert, ast.Raise)):
            return True
        # A delegation counts only when the callee ACTUALLY asserts. Accepting any
        # call would make the rule vacuous: `resp = payload_or_none(ctx)` is the
        # first statement of nearly every Then, so treating it as a verdict marks
        # every step terminating and the guard grades nothing — the exact disease
        # this rule exists to catch, in the rule itself. Measured: accepting bare
        # calls dropped the violation count from 10 to 2 and stopped reddening on
        # a restored copy of the original bug.
        call = None
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign)) and isinstance(stmt.value, ast.Call):
            call = stmt.value
        if call is not None:
            func = call.func
            name = getattr(func, "attr", getattr(func, "id", ""))
            if name in _TERMINAL_CALLS:
                return True
            if name in asserting_helpers or _is_asserting_helper_name(name):
                return True  # a helper whose own body asserts — `pkgs = _assert_has_packages(ctx)`
        if isinstance(stmt, ast.If):
            if (
                stmt.orelse
                and _executes_an_assertion(stmt.body, asserting_helpers)
                and _executes_an_assertion(stmt.orelse, asserting_helpers)
            ):
                return True
        if isinstance(stmt, (ast.With, ast.AsyncWith)) and _executes_an_assertion(stmt.body, asserting_helpers):
            return True
        if isinstance(stmt, ast.Try) and _executes_an_assertion(stmt.body, asserting_helpers):
            return True
        if isinstance(stmt, (ast.For, ast.AsyncFor)) and _executes_an_assertion(stmt.body, asserting_helpers):
            return True  # see the module docstring: a deliberate limit, filed separately
        if isinstance(stmt, ast.Return):
            return False
    return False


def _body_without_docstring(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    return [s for s in func.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]


#: Packages a step file may import a shared verdict FROM. Both are test-support trees
#: whose whole job is holding assertions the steps share; ``src`` is deliberately absent,
#: because production code asserting is not a step grading its claim.
_HELPER_IMPORT_ROOTS = ("tests.bdd.steps", "tests.helpers")

#: The repository root, derived from the shared discovery constant rather than restated.
_REPO_ROOT = BDD_STEPS_DIR.parents[2]


def _imported_asserting_helpers(tree: ast.Module, cache: dict[str, frozenset[str]]) -> set[str]:
    """Names this module IMPORTS that are asserting helpers where they are defined.

    :func:`_is_asserting_helper_name` exists because "module-local resolution cannot see
    across an import", and it answers that with a NAME convention: ``assert_*`` /
    ``require_*``. That convention does not cover every shared verdict this codebase has
    — ``_outcome_helpers.wire_absent`` and ``wire_dict`` (the wire-envelope assertions
    tests/CLAUDE.md's error-verification policy directs Then steps to use) both end in a
    bare ``assert`` and follow neither prefix, so a step delegating to them read as
    having no reachable assertion at all.

    Resolving the import is strictly better than widening the convention: the helper has
    to REALLY assert, checked in its own source by the same
    :func:`_asserting_helpers` walk used module-locally. Adding a ``wire_*`` prefix to
    the name list would have credited any future ``wire_``-named reader that grades
    nothing.

    ONE level, deliberately. A helper that only delegates further is not credited, which
    keeps the resolution decidable and cheap; the cost of that limit is a false positive
    on a two-hop delegation, which fails loudly and is fixable by naming the hop
    ``assert_*``.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 0 or not node.module:
            continue
        if not node.module.startswith(_HELPER_IMPORT_ROOTS):
            continue
        path = _REPO_ROOT / (node.module.replace(".", "/") + ".py")
        if not path.is_file():
            continue
        key = f"import::{node.module}"
        if key not in cache:
            cache[key] = _asserting_helpers(ast.parse(path.read_text(), filename=str(path)))
        defined = cache[key]
        # The step binds `asname or name`; the DEFINITION is `name`.
        names.update(alias.asname or alias.name for alias in node.names if alias.name in defined)
    return names


def _asserting_helpers_for(relative: str, cache: dict[str, frozenset[str]]) -> frozenset[str]:
    """Asserting helpers a step in *relative* can delegate to: defined here, or imported.

    ``iter_bdd_steps`` yields step FUNCTIONS, not the module they came from, but
    the reachability rule resolves delegation against the defining module. The
    file is re-parsed once per path and memoised in *cache*, so the shared
    discovery walk stays shared without re-parsing per step.
    """
    if relative not in cache:
        path = BDD_STEPS_DIR.parent.parent / relative
        tree = ast.parse(path.read_text(), filename=str(path))
        cache[relative] = _asserting_helpers(tree) | _imported_asserting_helpers(tree, cache)
    return cache[relative]


def _scan_bdd_steps() -> list[str]:
    """Find Then steps that do not grade what they claim.

    Discovery is shared (``iter_bdd_steps``); both rules below are this guard's
    own — presence of a meaningful assertion, then its reachability.
    """
    violations = []
    helpers_by_file: dict[str, frozenset[str]] = {}

    for step in iter_bdd_steps(step_names=("then",)):
        if not _has_meaningful_assertion_or_delegation(step.node):
            violations.append(f"{step.key} [no meaningful assertion]")
        elif _has_a_skippable_branch(step.node) and not _executes_an_assertion(
            _body_without_docstring(step.node), _asserting_helpers_for(step.relative, helpers_by_file)
        ):
            violations.append(f"{step.key} [assertion is unreachable on some path]")

    return violations


class TestBddNoTrivialAssertions:
    """Structural guard: Then steps must make meaningful assertions."""

    @pytest.mark.arch_guard
    def test_no_trivial_then_assertions(self):
        """Every @then step must assert a comparison, type check, or delegate to a helper.

        Bare truthiness checks (``assert ctx.get("response")``) don't verify
        correctness — they only verify existence.
        """
        violations = _scan_bdd_steps()
        assert not violations, (
            f"Found {len(violations)} Then step(s) that do not grade what they claim:\n"
            + "\n".join(f"  {v}" for v in violations)
            + "\n\n[no meaningful assertion]: the step needs a comparison (==, !=, is, in), "
            "an isinstance check, or delegation to a helper that asserts.\n"
            "[assertion is unreachable on some path]: the step CONTAINS a real assertion but can "
            "return without executing it — typically every assertion sits under an `if` with no "
            "`else`, so an errored dispatch skips the body and the step passes having graded "
            "nothing. Give the other path a verdict: require the value, assert the error, or "
            "pytest.xfail explicitly."
        )
