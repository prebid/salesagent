"""Exact-set lock for the FOURTH escape-hatch route: an inline xfail in a step body.

``test_architecture_e2e_rest_escape_hatches.py`` enumerates three routes that
turn a failing scenario into a non-blocking xfail and gives each an exact-set
AST lock:

1. the nodeid ledger (``test_e2e_rest_ledger_state.py``);
2. an ``is_e2e_rest``-gated xfail route in the BDD conftest's
   ``pytest_collection_modifyitems``;
3. an env-level ``E2EUnsupportedSetup`` declaration in ``tests/harness/``.

Route 4 — a bare ``pytest.xfail(...)`` called from inside a step definition (or
a private helper a step calls) — had no lock. It is the route with the *least*
review surface of the four: it registers nothing, it is keyed to no scenario
tag, no ledger equality test can see it, and because ``pytest.xfail()`` raises
immediately it also ABORTS the scenario, silently killing every later
assertion in the same scenario as dead code.

Core invariant (#1858 round-2): **a "known gap" is registered
exactly ONE way in this repo — a scenario/Examples-row tag in the ratcheted
ledger — never as a per-assertion escape hatch inside a step body.**

Why this guard is NOT hosted in ``test_architecture_bdd_assertion_strength.py``:
that module scans via ``tests/unit/_bdd_guard_helpers.iter_then_functions``,
which yields ONLY ``@then``-decorated functions and SKIPS files whose name
starts with ``_``. It is therefore structurally blind to a private module
helper such as ``_response_or_xfail`` — the exact shape this guard outlaws.
This module follows ``test_architecture_bdd_no_swallowed_dispatch_errors.py``
instead: every ``FunctionDef``/``AsyncFunctionDef`` in every ``.py`` under
``tests/bdd/steps``, plus module scope.

Detection is AST (``ast.Call`` resolving to the name ``xfail``), never grep:
prose in a docstring that mentions ``pytest.xfail(...)`` is not a call and must
not count, and a count "achieved" by editing prose must not turn the guard
green.

Both detectors are exercised by meta-tests below against known-bad and
known-good synthetic sources, so a detector regression cannot silently blind
the lock (repo precedent: #1498).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    assert_violations_match_allowlist,
)

_BDD_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"

#: Triple identifying one inline-xfail site: (path relative to tests/bdd/steps,
#: enclosing function name or "<module>", number of xfail calls in that scope).
XfailSite = tuple[str, str, int]

MODULE_SCOPE = "<module>"


def _is_xfail_call(node: ast.Call) -> bool:
    """Match a call whose callee resolves to the name ``xfail``.

    Covers the bare ``xfail(...)`` (``from pytest import xfail``), the
    attribute ``pytest.xfail(...)``, and ``pytest.mark.xfail(...)`` used
    inside a step module — all three are the same route: a step-local xfail
    that no ledger equality test can observe.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "xfail"
    return isinstance(func, ast.Attribute) and func.attr == "xfail"


def find_inline_xfail_calls(tree: ast.Module) -> list[tuple[int, str]]:
    """Return ``(lineno, enclosing function name)`` for every inline xfail call.

    The walk tracks the enclosing scope explicitly so a call inside a nested
    helper attributes to that helper, and a call at module level attributes to
    ``"<module>"``. A docstring or comment mentioning ``pytest.xfail`` is not
    an ``ast.Call`` and is therefore never reported.
    """
    found: list[tuple[int, str]] = []

    def _visit(node: ast.AST, scope: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = node.name
        if isinstance(node, ast.Call) and _is_xfail_call(node):
            found.append((node.lineno, scope))
        for child in ast.iter_child_nodes(node):
            _visit(child, scope)

    _visit(tree, MODULE_SCOPE)
    return found


def collect_xfail_sites(tree: ast.Module, relative_path: str) -> set[XfailSite]:
    """Return the ``(relative_path, function_name, xfail_call_count)`` triples.

    The COUNT element is load-bearing: ``(path, function)`` pairs alone would
    let a hatch be deleted in one branch of an already-listed function and
    re-added in another with no failure.
    """
    counts: dict[str, int] = {}
    for _lineno, scope in find_inline_xfail_calls(tree):
        counts[scope] = counts.get(scope, 0) + 1
    return {(relative_path, scope, count) for scope, count in counts.items()}


def step_module_paths() -> list[Path]:
    """Every module under tests/bdd/steps — helpers and ``_``-prefixed files INCLUDED."""
    return sorted(_BDD_STEPS_DIR.rglob("*.py"))


def _scan_bdd_steps() -> set[XfailSite]:
    found: set[XfailSite] = set()
    for py_file in step_module_paths():
        tree = ast.parse(py_file.read_text(), filename=str(py_file))
        relative = str(py_file.relative_to(_BDD_STEPS_DIR))
        found |= collect_xfail_sites(tree, relative)
    return found


# ---------------------------------------------------------------------------
# The pin. Allowlists can only SHRINK — never add a new triple, fix it instead.
# ---------------------------------------------------------------------------
# FIXME(#1858): 100 pre-existing inline-xfail sites (132 calls across 10 files)
# predate that Core Invariant. They are PINNED here, not swept: each must
# migrate to a ledger tag (tests/bdd/conftest.py's *_XFAIL_TAGS maps, or
# tests/bdd/e2e_rest_known_failures.txt) as its use case is next touched.
# One reference for the whole set — deliberately NOT 101 annotated call sites.
#
# tests/bdd/steps/domain/uc006_storyboard_creative_sync.py must appear ZERO
# times: any triple bearing that path is a NEW violation, not an allowlisted one.
_ALLOWLIST: set[XfailSite] = {
    ("domain/uc002_create_media_buy.py", "_assert_pipeline_routing", 1),
    ("domain/uc003_ext_error_scenarios.py", "given_seller_minimum_budget", 1),
    # Merge reconciliation (spec-gaps-1210 <- main): this row was authored against
    # main's copy of the function (3 calls). The branch's copy has 4: its single
    # request-level hatch was SPLIT in two — "wire code mismatched" and "the request
    # failed before it ever reached the wire" — while grading moved off the rebuilt
    # exception's class onto the wire code (salesagent-3dawm.18), which is strictly
    # tighter. No new gap category is admitted; one decision point became two calls.
    # This is the ONE row here whose count moved UP, and it is a follow-up: collapse
    # those two calls back into one in uc006_sync_creatives.py and re-key to 3.
    # UNCONDITIONAL, and that is why it stays: "this is not implemented" is a legible
    # claim, unlike a conditional xfail keyed on the outcome, which passes when production
    # agrees and excuses itself when it does not. The other 102
    # conditional sites in tests/bdd/steps are gone; this one was never one of them.
    # Graduated 7 -> 5 (salesagent-3dawm.18): the two prose-routed hatches
    # ("package not found"/"not supported by product" substring matches) are DELETED
    # and replaced with an unconditional wire-code assertion. Both were already dead —
    # CODE_TABLE derives the message from the code, so neither substring can appear.
    # Graduated 2 -> 1 (salesagent-3dawm.18): the "AdCPNotFoundError.error_code is
    # 'NOT_FOUND' — needs a domain-specific subclass" hatch is DELETED; that subclass
    # exists (AdCPPackageNotFoundError, _assignments.py) and the step now asserts
    # PACKAGE_NOT_FOUND on the wire unconditionally.
    ("domain/uc011_accounts.py", "then_account_transitions", 1),
    ("domain/uc011_accounts.py", "then_push_sent", 1),
    ("domain/uc011_accounts.py", "then_response_includes_context", 1),
    ("domain/uc011_accounts.py", "then_webhook_registered", 1),
    ("domain/uc019_query_media_buys.py", "given_creative_status_extra", 1),
    ("domain/uc019_query_media_buys.py", "given_creative_status_simple", 1),
    ("domain/uc019_query_media_buys.py", "given_creative_status_with_reason", 1),
    ("domain/uc019_query_media_buys.py", "given_no_creative_exists", 1),
    ("domain/uc019_query_media_buys.py", "given_package_creative_assignment", 1),
    ("generic/given_media_buy.py", "given_product_minimum_spend", 1),
    ("generic/given_media_buy.py", "given_proposal_budget_guidance_min", 1),
    ("generic/given_media_buy.py", "given_proposal_not_exists", 1),
}

_FIX_HINT = (
    "Register the gap the ONE sanctioned way: a scenario/Examples-row tag in the "
    "ratcheted ledger (a *_XFAIL_TAGS map in tests/bdd/conftest.py, or a nodeid in "
    "tests/bdd/e2e_rest_known_failures.txt), and make the Then step assert the "
    "obligation unconditionally. An inline pytest.xfail registers nothing, is keyed "
    "to no scenario, and aborts every later assertion in the scenario as dead code."
)


def find_conditional_xfail_calls(tree: ast.Module) -> list[tuple[int, str]]:
    """Every ``pytest.xfail`` reachable only through a branch, as ``(lineno, guard)``.

    A conditional xfail is categorically worse than an unconditional one, and the
    difference is not stylistic. Unconditional says "this is not implemented" — a legible,
    falsifiable claim that stops being true when someone implements it. Conditional says
    "excuse me IF the outcome is the one I was written to catch", so the step passes when
    production agrees and excuses itself when it does not: it cannot fail in either
    direction.

    Guarded by branch context rather than by reason text, because the reason is prose and
    drifts; ``if`` / ``try`` / ``except`` is structure and cannot.
    """
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "xfail"
            and getattr(node.func.value, "id", "") == "pytest"
        ):
            continue
        parent = parents.get(node)
        while parent is not None:
            if isinstance(parent, ast.If):
                out.append((node.lineno, ast.unparse(parent.test)))
                break
            if isinstance(parent, (ast.Try, ast.ExceptHandler)):
                out.append((node.lineno, "try/except"))
                break
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                break
            parent = parents.get(parent)
    return out


class TestBddNoConditionalXfail:
    """Structural guard: ZERO conditional xfails, with no allowlist.

    There is deliberately no allowlist here. 103 of these existed and all 103 are gone,
    so the honest pin is zero — an allowlist would only be a place to put the next one.
    """

    @pytest.mark.arch_guard
    def test_no_conditional_xfail_anywhere_in_steps(self):
        offenders: list[str] = []
        for py_file in step_module_paths():
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
            relative = str(py_file.relative_to(_BDD_STEPS_DIR))
            for lineno, guard in find_conditional_xfail_calls(tree):
                offenders.append(f"  {relative}:{lineno}  guarded by: {guard}")
        assert not offenders, (
            f"{len(offenders)} conditional pytest.xfail call(s) in tests/bdd/steps:\n"
            + "\n".join(sorted(offenders))
            + "\n\nA conditional xfail is keyed on the OUTCOME, so the step passes when "
            "production agrees and excuses itself when it does not — it cannot fail in the "
            "one direction that matters. Assert the obligation unconditionally, and declare "
            "any genuine gap as a scenario/Examples-row tag in the ratcheted ledger "
            "(a *_XFAIL_TAGS map in tests/bdd/conftest.py, or a nodeid in "
            "tests/bdd/e2e_rest_known_failures.txt), where strict=True makes it XPASS loudly "
            "the day the gap closes. If the guard is about the ENVIRONMENT rather than the "
            "outcome (no DB session, wrong transport), it is a skip or a harness fix, not an "
            "expected failure."
        )

    @pytest.mark.arch_guard
    def test_detector_catches_a_conditional_xfail(self):
        """The guard must go red on the shape it bans — a passing guard proves nothing."""
        bad = ast.parse(
            "import pytest\n"
            "def then_x(ctx):\n"
            "    error = ctx.get('error')\n"
            "    if error is not None:\n"
            "        pytest.xfail('SPEC-PRODUCTION GAP: excuse')\n"
        )
        assert find_conditional_xfail_calls(bad), "detector missed a conditional xfail"

    @pytest.mark.arch_guard
    def test_detector_allows_an_unconditional_xfail(self):
        """An unconditional xfail is a legible claim and must NOT trip this guard."""
        good = ast.parse("import pytest\ndef then_x(ctx):\n    pytest.xfail('not implemented')\n")
        assert not find_conditional_xfail_calls(good), "detector flagged an unconditional xfail"


class TestBddNoInlineXfail:
    """Structural guard: no inline xfail call in any tests/bdd/steps module."""

    @pytest.mark.arch_guard
    def test_no_new_inline_xfail(self):
        found = _scan_bdd_steps()
        new = found - _ALLOWLIST
        assert not new, (
            f"Found {len(new)} inline-xfail site(s) in tests/bdd/steps that are not "
            "in the pinned allowlist:\n"
            + "\n".join(f"  {path}::{func} ({count} call(s))" for path, func, count in sorted(new))
            + "\n\n"
            + _FIX_HINT
        )

    @pytest.mark.arch_guard
    def test_allowlist_matches_tree_exactly(self):
        """Exact-set equality: a NEW site and a STALE entry both fail here.

        Equality in both directions is what makes the pin a ratchet — deleting
        an inline xfail without removing its triple fails just as loudly as
        adding one, so the count can only go down and every move is reviewed
        in the same change.
        """
        assert_violations_match_allowlist(
            _scan_bdd_steps(),
            _ALLOWLIST,
            fix_hint=(
                "Removed an inline xfail? Delete its triple from _ALLOWLIST in the same change "
                "(allowlists only shrink). Added one? " + _FIX_HINT
            ),
        )


class TestDetectorMetaTests:
    """Meta-tests: the LIVE detector catches known-bad and passes known-good shapes."""

    @pytest.mark.arch_guard
    def test_detector_catches_known_bad(self):
        assert_detector_catches_ast_snippets(
            lambda tree: [line for line, _ in find_inline_xfail_calls(tree)],
            snippets={
                "inline-xfail-in-then-body": (
                    "@then('the result is graded')\n"
                    "def then_result(ctx):\n"
                    "    if ctx['response'].status is None:\n"
                    '        pytest.xfail("SPEC-PRODUCTION GAP: status never populated")\n'
                ),
                "inline-xfail-in-private-helper": (
                    "def _response_or_xfail(ctx, expectation):\n"
                    "    resp = ctx.get('response')\n"
                    "    if resp is not None:\n"
                    "        return resp\n"
                    '    pytest.xfail(f"SPEC-PRODUCTION GAP: {expectation}")\n'
                ),
                "bare-imported-xfail": ("from pytest import xfail\n\ndef then_result(ctx):\n    xfail('gap')\n"),
                "xfail-at-module-scope": ("import pytest\n\npytest.xfail('whole module is a gap')\n"),
                "pytest-mark-xfail-in-step-module": (
                    "def then_result(ctx):\n    item.add_marker(pytest.mark.xfail(reason='gap', strict=False))\n"
                ),
            },
        )

    @pytest.mark.arch_guard
    def test_detector_passes_known_good(self):
        good_snippets = {
            # C5: the target file's module docstring ADVERTISES the practice in prose.
            # An AST detector must not count it — and a prose edit must not be able to
            # turn the guard green while calls remain.
            "docstring-prose-mentioning-xfail": (
                '"""Steps that honestly ``pytest.xfail("SPEC-PRODUCTION GAP: ...")`` when\n'
                'production diverges from the pinned spec."""\n'
                "\n"
                "def then_result(ctx):\n"
                "    assert ctx['response'].status == 'approved'\n"
            ),
            "comment-mentioning-xfail": (
                "def then_result(ctx):\n"
                "    # do NOT pytest.xfail() here — ledger the scenario tag instead\n"
                "    assert ctx['response'].status == 'approved'\n"
            ),
            "string-literal-mentioning-xfail": (
                "def then_result(ctx):\n"
                "    reason = 'pytest.xfail is banned in step bodies'\n"
                "    assert reason in ctx['docs']\n"
            ),
            "pytest-fail-is-a-hard-failure": (
                "def then_result(ctx):\n    if ctx['response'] is None:\n        pytest.fail('dispatch failed')\n"
            ),
            "pytest-skip-is-a-different-route": (
                "def then_result(ctx):\n    if ctx['transport'] == 'e2e_rest':\n        pytest.skip('no surface')\n"
            ),
            "name-containing-xfail-is-not-a-call": (
                "def then_result(ctx):\n    xfail_reason = ctx.get('xfail_reason')\n    assert xfail_reason is None\n"
            ),
        }
        for label, source in good_snippets.items():
            tree = ast.parse(source, filename=f"<known-good:{label}>")
            assert not find_inline_xfail_calls(tree), f"False positive on known-good: {label}"

    @pytest.mark.arch_guard
    def test_triples_carry_per_function_call_counts(self):
        """The COUNT element closes the same-function swap.

        ``(path, function)`` pairs alone would let one xfail be deleted and
        another added inside an already-listed function with no failure — the
        hole that matters most in uc006_sync_creatives.py, which carries 87
        calls across only 59 functions.
        """
        source = (
            "def then_two_hatches(ctx):\n"
            "    if ctx['a'] is None:\n"
            "        pytest.xfail('gap a')\n"
            "    if ctx['b'] is None:\n"
            "        pytest.xfail('gap b')\n"
            "\n"
            "def then_one_hatch(ctx):\n"
            "    pytest.xfail('gap c')\n"
            "\n"
            "pytest.xfail('module scope')\n"
        )
        tree = ast.parse(source, filename="<counts>")
        assert collect_xfail_sites(tree, "domain/fake.py") == {
            ("domain/fake.py", "then_two_hatches", 2),
            ("domain/fake.py", "then_one_hatch", 1),
            ("domain/fake.py", MODULE_SCOPE, 1),
        }

    @pytest.mark.arch_guard
    def test_scan_covers_underscore_prefixed_modules(self):
        """The scan domain must include ``_``-prefixed step modules.

        ``iter_then_functions`` (the assertion-strength host) skips them, which
        is precisely why this guard does not live there.
        """
        from tests.unit._bdd_guard_helpers import iter_then_functions

        scanned = {p.name for p in step_module_paths()}
        underscore_modules = {p.name for p in _BDD_STEPS_DIR.rglob("_*.py")}
        assert underscore_modules, "expected at least one _-prefixed module under tests/bdd/steps"
        assert underscore_modules <= scanned, (
            f"scan domain misses _-prefixed modules: {sorted(underscore_modules - scanned)}"
        )

        # And the host this guard deliberately does NOT use cannot see them.
        then_host_files = {key.split(":")[0].rsplit("/", 1)[-1] for key, _func in iter_then_functions()}
        assert not (underscore_modules & then_host_files), (
            "iter_then_functions unexpectedly yields _-prefixed modules — re-evaluate the host choice"
        )
