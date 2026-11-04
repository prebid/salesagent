"""Exact-set lock for the e2e_rest xfail escape hatches (PR #1430 review).

The e2e_rest known-failures ledger has an exact-set lock
(``test_e2e_rest_ledger_state.py``), so a still-failing scenario cannot be
silently added to or dropped from the ledger. But the ledger is only one of
four routes that take a failing e2e_rest scenario out of blocking grading:

1. the nodeid ledger (locked);
2. an ``is_e2e_rest``-gated xfail route in the BDD conftest's
   ``pytest_collection_modifyitems`` (tag/substring conditions);
3. an env-level ``E2EUnsupportedSetup`` declaration in ``tests/harness/``
   (translated to xfail by the conftest report hook);
4. never parametrizing e2e_rest at all, in ``pytest_generate_tests`` — the
   quietest of the four, because a transport dropped at collection is exactly
   as ungraded as an xfail while showing no marker anywhere (#1802).

Routes 2, 3 and 4 had no lock: a scenario relocated there escaped tracking
silently. This guard gives them the same exact-set treatment — adding OR
removing a route fails here, forcing a reviewable pin update in the same
change (the ledger discipline). There is deliberately no separate
``count <= len(pin)`` ratchet: the exact-set comparison already fails in both
directions, and a ceiling derived from the pin can never fail independently.

Route 4 is pinned four ways, because it can be written four ways: the gate
condition on the append (detector 3), the hook's call-site argument that
carries ``Transport.E2E_REST`` (detector 3), every early return or transports
rebind upstream of it, and the single-transport tag map.

Every detector is exercised by meta-tests below against known-bad synthetic
sources, so a detector regression cannot silently blind the lock (repo
precedent: #1498).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BDD_CONFTEST = _REPO_ROOT / "tests" / "bdd" / "conftest.py"
_HARNESS_DIR = _REPO_ROOT / "tests" / "harness"

# ---------------------------------------------------------------------------
# Detector 1: is_e2e_rest-gated xfail routes in pytest_collection_modifyitems
# ---------------------------------------------------------------------------


def find_e2e_rest_xfail_conditions(tree: ast.Module) -> list[str]:
    """Return the unparsed condition of every xfail route touching is_e2e_rest.

    A route is an ``if`` statement inside ``pytest_collection_modifyitems``
    whose condition references the ``is_e2e_rest`` name and whose subtree
    (either branch) reaches a ``…xfail`` attribute — i.e. adds or builds a
    ``pytest.mark.xfail``. Conditions of BOTH polarities are pinned: a
    ``not is_e2e_rest`` exclusion asserts e2e_rest must pass, so flipping it
    is also a tracking change.
    """
    hooks = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "pytest_collection_modifyitems"
    ]
    conditions: list[str] = []
    for hook in hooks:
        for node in ast.walk(hook):
            if not isinstance(node, ast.If):
                continue
            test_names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
            if "is_e2e_rest" not in test_names:
                continue
            reaches_xfail = any(isinstance(sub, ast.Attribute) and sub.attr == "xfail" for sub in ast.walk(node))
            if reaches_xfail:
                conditions.append(ast.unparse(node.test))
    return sorted(conditions)


# The pinned route set. Duplicates are real (the uc005 filter tags xfail from
# two loops), so this is a sorted tuple, not a set. When a route is added,
# removed, or reworded, update this pin IN THE SAME CHANGE and say why in the
# commit — exactly like EXPECTED_LEDGER graduations.
EXPECTED_XFAIL_ROUTES: tuple[str, ...] = (
    # The T-UC-002-alt-manual route ("... and (is_mcp or is_rest or is_e2e_rest)")
    # was retired in PR #1567: it xfailed the pre-3.1.1 workflow_step_id assertion,
    # which the spec-reconciled scenario no longer makes — the scenario now grades
    # the CreateMediaBuySubmitted envelope live on all four transports.
    # REMOVED with the route: T-UC-004-boundary-ownership "differs from owner". Its
    # strict xfail said the REST layer did not enforce the ownership boundary, which is
    # what that marker says when a scenario demands a HARD refusal and production answers
    # 200 plus an empty list. The demand was the wrong one -- 3.1.1's
    # get-media-buy-delivery-response.json declares errors[] for missing delivery data --
    # so the row now asserts the per-id MEDIA_BUY_NOT_FOUND advisory, which production
    # emits on the REST wire too. Observed xpassing in innet_150926_0049.
    "'T-UC-004-dim-sortby-fallback' in marker_names and is_e2e_rest",
    # REMOVED with the route: T-UC-019-boundary-principal. The outline's two rows demanding
    # an identity resolved WITHOUT a principal are gone -- get_media_buys declares
    # ResolvedIdentity, on which the principal is not optional, so that state is
    # unconstructible -- and the surviving row presents a credential the resolver really
    # rejects (AUTH_INVALID, terminal) rather than injecting a null identity.
    # REMOVED 2026-09-15: "is_e2e_rest and 'T-UC-019-ext-a' in marker_names".
    # #1762 narrowed this route from "(is_rest or is_e2e_rest)" to e2e_rest only and
    # kept the last transport on the ground that the local run could not exercise the
    # live stack. The in-network run innet_150926_0531 exercised it: the node XPASSED.
    # The route's reason was a REST-only auth suggestion string ("authenticate" vs
    # "authentication"), which cannot exist any more -- suggestions derive from
    # CODE_TABLE[code], one source shared by every transport, so no transport can
    # carry a different one (salesagent-3dawm.14). The hatch is now closed entirely.
    "(is_rest or is_e2e_rest) and 'T-UC-019-partition-principal-invalid' in marker_names",
    # REMOVED 2026-09-15 with the sampling scenario family: the route gated
    # T-UC-004-partition-sampling, whose outline graded `sampling_method` -- a field
    # AdCP 3.1.1 does not define anywhere. Scenario and route deleted together.
    "is_e2e_rest",
    "is_e2e_rest and 'T-UC-002-nfr-001-enforcement' in marker_names",
    "is_e2e_rest and 'T-UC-004-daterange-end-only' in marker_names",
    "is_e2e_rest and 'T-UC-005-empty-catalog' in marker_names",
    # REMOVED 2026-09-15 with the sampling scenario family:
    # "is_e2e_rest and 'Unknown string not in enum' in nodeid" -- a strict xfail whose
    # reason was "Docker does not validate sampling_method". Nothing was owed:
    # sampling_method is not an AdCP 3.1.1 field, so there was no obligation for the
    # live server to validate and no scenario left to route.
    "is_e2e_rest and any((t.startswith('T-UC-019') for t in marker_names))",
    "is_e2e_rest and marker_names & _UC004_E2E_WEBHOOK_INTERNAL_TAGS",
    "is_e2e_rest and marker_names & _UC005_E2E_FIXTURE_INJECTION_TAGS",
    "is_e2e_rest and tag in uc005_filter_e2e_untestable",
    "is_e2e_rest and tag in uc005_filter_e2e_untestable",
    # REMOVED with the route: the _UC005_PARTIAL_TAGS in-process arm. Its rows were the
    # disclosure_positions scenarios, which xpassed against an EMPTY catalog -- the UC-005
    # env seeded its seller only in e2e mode, so an exclusion assertion passed trivially.
    # With the seller seeded on every transport the filter is implemented (3.1.1
    # list-creative-formats-request.json declares it) and the rows grade for real.
    #
    "not is_e2e_rest",
)


def test_conftest_e2e_rest_xfail_routes_match_pin() -> None:
    """Every is_e2e_rest xfail route in the BDD conftest is pinned exactly."""
    tree = ast.parse(_BDD_CONFTEST.read_text())
    actual = find_e2e_rest_xfail_conditions(tree)
    expected = sorted(EXPECTED_XFAIL_ROUTES)
    added = [c for c in actual if actual.count(c) > expected.count(c)]
    removed = [c for c in expected if expected.count(c) > actual.count(c)]
    assert actual == expected, (
        "e2e_rest xfail routes in tests/bdd/conftest.py drifted from the pin.\n"
        "A failing e2e_rest scenario must NOT be silently rerouted around the "
        "ledger — update EXPECTED_XFAIL_ROUTES in the same change and justify it.\n"
        f"New/changed routes: {sorted(set(added))}\n"
        f"Routes removed or reworded: {sorted(set(removed))}"
    )


# ---------------------------------------------------------------------------
# Detector 3: the parametrize-time E2E_REST gate, and its call sites
# ---------------------------------------------------------------------------
#
# #1802: _NO_E2E_REST_TAGS was a tag-set-gated `if` ANDed onto the condition
# that appends Transport.E2E_REST inside pytest_generate_tests — a silent,
# parametrize-time drop neither detector 1 (pytest_collection_modifyitems xfail
# routes) nor detector 2 (tests/harness/ E2EUnsupportedSetup) could see, because
# it lives in a different hook and never raises an xfail. #1802 deleted the tag
# set; the merged conftest keeps that deletion, and
# tests/unit/test_e2e_rest_ssrf_blocked_scenario_collected.py proves
# behaviourally that the one scenario it used to drop
# (T-UC-004-webhook-ssrf-blocked) is collected on e2e_rest again — reachable
# because the outbound-egress seam's ADCP_TESTING loopback allowance lets the
# live stack dial the e2e capture receiver. This detector pins the gate so the
# drop cannot come back silently: a future `and not (marker_names &
# _SOME_NEW_TAGS)` changes the unparsed condition and fails the pin below.
#
# The gate is no longer written inside the hook. `_parametrize_ctx` (extracted
# so the AdCP branch and the admin branch share ONE append-e2e-when-enabled tail —
# duplicating it is the R0801 shape the DRY invariant treats as a defect) moved
# the append into a module-level function that appends a PARAMETER, so an
# `E2E_REST`-attribute scan anchored to `pytest_generate_tests` finds nothing at
# all. A detector that returns None there does not fail honestly, it fails
# BLIND, and the mismatch reads as drift. So the scan is module-wide and keys on
# the control-flow PROPERTY — an `if` that reads BDD_E2E_ENABLED and appends —
# rather than on where the append happens to be written; and a second pin covers
# the hook's call sites, which is where the merged shape lets you write the next
# drop: `None if <new condition> else Transport.E2E_REST`.

_E2E_ENABLED_FLAG = "BDD_E2E_ENABLED"


def _names_e2e_rest(node: ast.AST) -> bool:
    """Whether *node*'s subtree reaches an ``E2E_REST`` attribute access."""
    return any(isinstance(sub, ast.Attribute) and sub.attr == "E2E_REST" for sub in ast.walk(node))


def find_e2e_rest_parametrize_gates(tree: ast.Module) -> tuple[str, ...]:
    """Every ``if`` in the module that gates an e2e transport append.

    A gate is an ``if`` whose condition names ``BDD_E2E_ENABLED`` and whose body
    reaches an ``.append(...)`` or ``.extend(...)``. That half is what separates the gate
    from the xdist-incompatibility check at the top of the conftest, which reads the
    same env var and raises. Reported in source order; an EMPTY tuple (the
    append became unconditional, or moved somewhere this scan cannot see) fails
    the pin below rather than passing vacuously.

    ``extend`` is here because the helper now takes a SEQUENCE of e2e transports rather
    than one, so it extends instead of appending. Matching only ``append`` made this
    finder return an empty tuple the moment that changed -- and the empty tuple failed
    the pin, which is the behaviour the paragraph above promises and the reason this was
    caught instead of quietly becoming a detector that watches nothing.
    """
    gates: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        literals = {n.value for n in ast.walk(node.test) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        if _E2E_ENABLED_FLAG not in literals:
            continue
        appends = any(
            isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in ("append", "extend")
            for sub in ast.walk(node)
        )
        if appends:
            gates.append((node.lineno, ast.unparse(node.test)))
    return tuple(test for _, test in sorted(gates))


# The pinned gate conditions. Update IN THE SAME CHANGE as any edit to them, and
# say why in the commit — exactly like EXPECTED_XFAIL_ROUTES.
EXPECTED_E2E_REST_PARAMETRIZE_GATES: tuple[str, ...] = ("e2e_members and os.environ.get('BDD_E2E_ENABLED') == 'true'",)


def test_e2e_rest_parametrize_gates_match_pin() -> None:
    """Every BDD_E2E_ENABLED-gated transport append is pinned exactly."""
    tree = ast.parse(_BDD_CONFTEST.read_text())
    actual = find_e2e_rest_parametrize_gates(tree)
    assert actual == EXPECTED_E2E_REST_PARAMETRIZE_GATES, (
        "The e2e parametrize gate(s) in tests/bdd/conftest.py drifted from the pin.\n"
        "A scenario must NOT be silently dropped from e2e_rest parametrization by a new "
        "tag-set ANDed onto this condition — route the exclusion through E2EUnsupportedSetup "
        "instead (detector 2, below) so it is reviewable, or update "
        "EXPECTED_E2E_REST_PARAMETRIZE_GATES in the same change and justify it.\n"
        f"Expected: {EXPECTED_E2E_REST_PARAMETRIZE_GATES!r}\n"
        f"Actual:   {actual!r}"
    )


def find_e2e_rest_append_expressions(tree: ast.Module) -> tuple[str, ...]:
    """Every expression in ``pytest_generate_tests`` that decides E2E_REST's fate.

    With the append extracted into a helper, the hook's remaining say over
    e2e_rest is the ARGUMENT it hands that helper — today
    ``None if no_rest_uc else Transport.E2E_REST``. That conditional is the
    natural place to write the next silent drop (``None if skip else …``) and no
    other detector in this module can see it: it is not an xfail, not a harness
    declaration, not a gate condition, and not an early return or a rebind.

    Reported as the OUTERMOST expression naming ``E2E_REST``, so whatever
    condition is attached to it is part of the pinned text. An inline
    ``transports.append(Transport.E2E_REST)`` is reported the same way, so the
    pin keeps working if the helper is ever inlined again.
    """
    hooks = [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "pytest_generate_tests"
    ]
    found: list[str] = []

    def _visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr) and _names_e2e_rest(child):
                found.append(ast.unparse(child))
            else:
                _visit(child)

    for hook in hooks:
        _visit(hook)
    return tuple(found)


# The pinned call sites. Same discipline as the gate pin above.
# E2E_REST is named UNCONDITIONALLY here, and that is the property this pin now protects:
# the list it seeds carries no gate, so no scenario can lose e2e_rest. E2E_MCP and E2E_A2A
# are appended under BDD_E2E_TRANSPORTS=all on the following line, which this detector does
# not see because it looks for E2E_REST — deliberate. Those two are a capacity rollout
# (salesagent-e0enw); e2e_rest is the transport this module exists to keep ungated.
EXPECTED_E2E_REST_APPEND_EXPRESSIONS: tuple[str, ...] = ("[Transport.E2E_REST]",)


def test_e2e_rest_append_expressions_match_the_pin() -> None:
    """Every expression that hands E2E_REST to (or withholds it from) parametrization is pinned."""
    tree = ast.parse(_BDD_CONFTEST.read_text())
    actual = find_e2e_rest_append_expressions(tree)
    assert actual == EXPECTED_E2E_REST_APPEND_EXPRESSIONS, (
        "The E2E_REST call site(s) in tests/bdd/conftest.py's pytest_generate_tests drifted "
        "from the pin.\n"
        "Withholding e2e_rest by widening the condition on the transport argument is exactly "
        "as ungraded as an xfail and invisible to every other detector here — route it through "
        "E2EUnsupportedSetup instead (detector 2, below), or update "
        "EXPECTED_E2E_REST_APPEND_EXPRESSIONS in the same change and justify it.\n"
        f"Expected: {EXPECTED_E2E_REST_APPEND_EXPRESSIONS!r}\n"
        f"Actual:   {actual!r}"
    )


def _appends_e2e_rest(node: ast.If) -> bool:
    """Whether *node* is the gate that APPENDS ``Transport.E2E_REST``."""
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "append"
            and any(isinstance(arg, ast.Attribute) and arg.attr == "E2E_REST" for arg in ast.walk(sub))
        ):
            return True
    return False


def find_e2e_rest_exclusion_points(tree: ast.Module) -> tuple[str, ...]:
    """Every condition in ``pytest_generate_tests`` that can withhold e2e_rest.

    ``find_e2e_rest_parametrize_gate`` above reports only the LAST gate -- the
    ``if`` whose body appends ``Transport.E2E_REST``. Everything upstream of it
    is invisible to that detector, and there is plenty: five ``if ...: return``
    statements drop a scenario before the append is reached, and one
    ``if no_rest_uc: transports = [...]`` REBINDS the transport list so the
    append never applies. A new exclusion written in either shape would be a
    scenario silently un-graded on the live stack, with every existing detector
    green.

    So this reports the control-flow PROPERTY -- any statement before the append
    that changes what e2e_rest sees -- rather than one syntactic shape of it:

    * every ``if`` whose body contains a bare ``return``;
    * every ``if`` whose body rebinds ``transports`` or ``ids``.

    Returned unparsed, in source order, for pinning.

    Residual, stated rather than claimed away: an exclusion expressed a THIRD
    way -- a helper called from the hook that hands back a narrowed list -- is
    not caught, because this does not follow calls. Pinning the OUTCOME (the
    collected set of e2e_rest-parametrized ids) would catch that and is strictly
    stronger; it needs a full BDD collection under ``BDD_E2E_ENABLED=true``
    inside a unit test and pins thousands of churning ids, so it belongs to
    whoever owns the e2e_rest ledger, not here.
    """
    hooks = [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "pytest_generate_tests"
    ]
    found: list[tuple[int, str]] = []
    for hook in hooks:
        for node in ast.walk(hook):
            if not isinstance(node, ast.If):
                continue
            # Skip ONLY the append gate itself, identified by the call it makes.
            # Skipping any `if` that MENTIONS E2E_REST would hide the most natural
            # way to write the next drop --
            #   if flaky: transports = [t for t in transports if t is not Transport.E2E_REST]
            # -- which names the constant and rebinds, and would go unreported.
            if _appends_e2e_rest(node):
                continue  # the final gate, already pinned above
            returns = any(isinstance(sub, ast.Return) for sub in ast.walk(node))
            rebinds = any(
                isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store) and sub.id in {"transports", "ids"}
                for sub in ast.walk(node)
            )
            if returns or rebinds:
                found.append((node.lineno, ast.unparse(node.test)))
    # Sorted by line, because ``ast.walk`` is breadth-first: the ``_IMPL_ONLY``
    # gate is nested inside a ``for`` and would otherwise sort AFTER a shallower
    # ``if`` that follows it in the source. A pin whose order depends on nesting
    # depth reorders itself when someone wraps a condition in a loop, and reads
    # as drift.
    return tuple(test for _, test in sorted(found))


# The pinned exclusion points. Update IN THE SAME CHANGE as any edit to
# pytest_generate_tests' control flow, and say why in the commit -- exactly like
# EXPECTED_XFAIL_ROUTES and EXPECTED_E2E_REST_PARAMETRIZE_GATE.
EXPECTED_E2E_REST_EXCLUSION_POINTS: tuple[str, ...] = (
    "'ctx' not in metafunc.fixturenames",
    "marker_names & _TRANSPORT_SPECIFIC_TAGS",
    # The UC-010 auth outline dispatches per-row via its own channel column and
    # has no e2e leg (#1592); it returns before any transport list is built.
    "single",
    "any((t.startswith(_ADMIN_TAG_PREFIX) for t in marker_names))",
    "any((t.startswith(tag_prefix) for t in marker_names)) and required_tag in marker_names",
    # "no_rest_uc" was here: a per-UC exclusion for tools with no REST route, driven by a
    # hand-maintained tag-prefix tuple that had been permanently empty. Deleted rather
    # than kept as a dormant branch -- a tool's reachability is the ToolSpec row's answer
    # (MCP registration, the A2A card and the REST route are all generated from it, and
    # all 14 rows carry a RestBinding), so a tag list restating it was a second
    # declaration free to drift. One fewer way to withhold a transport, which is the
    # direction this pin exists to enforce.
)


def test_e2e_rest_exclusion_points_match_the_pin() -> None:
    """Every path that can withhold e2e_rest from a scenario is pinned."""
    tree = ast.parse(_BDD_CONFTEST.read_text())
    actual = find_e2e_rest_exclusion_points(tree)
    assert actual == EXPECTED_E2E_REST_EXCLUSION_POINTS, (
        "The set of paths that can withhold e2e_rest from a scenario in "
        "tests/bdd/conftest.py's pytest_generate_tests drifted from the pin.\n"
        "A scenario must NOT be silently dropped from live grading by a new early "
        "return or a new transports rebind -- route the exclusion through "
        "E2EUnsupportedSetup instead (detector 2, below) so it is reviewable, or "
        "update EXPECTED_E2E_REST_EXCLUSION_POINTS in the same change and justify it.\n"
        f"Expected: {EXPECTED_E2E_REST_EXCLUSION_POINTS!r}\n"
        f"Actual:   {actual!r}"
    )


def test_exclusion_point_detector_sees_a_rebind_with_no_return() -> None:
    """The detector's reason for existing: a drop that never returns.

    ``if no_rest_uc: transports = [...]`` is live in the tree today and is
    invisible to both the final-gate detector and to any early-return scan.
    """
    source = (
        "def pytest_generate_tests(metafunc):\n"
        "    transports = [Transport.A2A]\n"
        "    if sneaky:\n"
        "        transports = [Transport.MCP]\n"
        "    if enabled:\n"
        "        transports.append(Transport.E2E_REST)\n"
    )
    assert find_e2e_rest_exclusion_points(ast.parse(source)) == ("sneaky",)


def test_exclusion_point_detector_sees_a_named_e2e_rest_filter() -> None:
    """A drop that NAMES E2E_REST and rebinds must still be reported.

    The most natural way to write the next exclusion mentions the constant. An
    earlier form of this detector skipped any ``if`` whose subtree named
    ``E2E_REST``, to avoid re-reporting the final append gate -- and skipped this
    with it.
    """
    source = (
        "def pytest_generate_tests(metafunc):\n"
        "    transports = [Transport.A2A, Transport.E2E_REST]\n"
        "    if flaky:\n"
        "        transports = [t for t in transports if t is not Transport.E2E_REST]\n"
        "    if enabled:\n"
        "        transports.append(Transport.E2E_REST)\n"
    )
    assert find_e2e_rest_exclusion_points(ast.parse(source)) == ("flaky",)


def test_exclusion_point_detector_sees_an_early_return() -> None:
    """The early-return branch, proved synthetically like its rebind sibling."""
    source = (
        "def pytest_generate_tests(metafunc):\n"
        "    transports = [Transport.A2A]\n"
        "    if bailing:\n"
        "        return\n"
        "    if enabled:\n"
        "        transports.append(Transport.E2E_REST)\n"
    )
    assert find_e2e_rest_exclusion_points(ast.parse(source)) == ("bailing",)


def test_exclusion_point_detector_ignores_an_unrelated_conditional() -> None:
    """An ``if`` that neither returns nor rebinds is not an exclusion point."""
    source = (
        "def pytest_generate_tests(metafunc):\n"
        "    transports = [Transport.A2A]\n"
        "    if noisy:\n"
        "        print('hello')\n"
        "    if enabled:\n"
        "        transports.append(Transport.E2E_REST)\n"
    )
    assert find_e2e_rest_exclusion_points(ast.parse(source)) == ()


# The pinned membership of the single-transport tag map. A scenario listed here
# is graded on ONE transport by design; adding an entry moves a scenario off
# three transports, which is exactly the kind of narrowing that must not be
# silent. Update IN THE SAME CHANGE and say why in the commit.
EXPECTED_SINGLE_TRANSPORT_TAGS: dict[str, str] = {
    "a2a_untyped_ingest": "A2A",
    # local-pre-dispatch-refusals.feature grades refusals made BEFORE any tool runs: a body
    # that is not a JSON object, a name the registry lacks, an A2A message naming no skill
    # or two. The refused shape is the transport's own frame -- an HTTP body, a JSON-RPC
    # message, an MCP arguments object -- so one sentence cannot mean the same bytes on all
    # three, and each scenario names the one transport whose frame it sends.
    "predispatch-rest": "REST",
    "predispatch-a2a": "A2A",
    "predispatch-mcp": "MCP",
}


def test_single_transport_tags_match_the_pin() -> None:
    """_SINGLE_TRANSPORT_TAGS is exactly the pinned map."""
    from tests.bdd.conftest import _SINGLE_TRANSPORT_TAGS

    assert _SINGLE_TRANSPORT_TAGS == EXPECTED_SINGLE_TRANSPORT_TAGS, (
        "tests/bdd/conftest.py's _SINGLE_TRANSPORT_TAGS drifted from the pin.\n"
        "Each entry takes a scenario off three transports and grades it on one. "
        "That is sometimes right, but never silent.\n"
        f"Expected: {EXPECTED_SINGLE_TRANSPORT_TAGS!r}\n"
        f"Actual:   {_SINGLE_TRANSPORT_TAGS!r}"
    )


# ---------------------------------------------------------------------------
# Detector 2: env-level E2EUnsupportedSetup declarations in tests/harness/
# ---------------------------------------------------------------------------


def find_unsupported_declarations(tree: ast.Module, relpath: str) -> list[tuple[str, str, str]]:
    """Return (relpath, enclosing def, reason) for every declaration site.

    Sites are calls to ``e2e_unsupported(...)`` (including as a decorator
    argument) and direct ``raise E2EUnsupportedSetup(...)``. A non-constant
    reason (f-string) is recorded as ``<dynamic>``. The walk tracks the
    enclosing function explicitly so decorator arguments attribute to the
    decorated method, not the module.
    """
    found: list[tuple[str, str, str]] = []

    def _reason(call: ast.Call) -> str:
        arg = call.args[0] if call.args else None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        return "<dynamic>"

    def _visit(node: ast.AST, scope: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = node.name
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "e2e_unsupported":
            found.append((relpath, scope, _reason(node)))
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            func = node.exc.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "E2EUnsupportedSetup":
                found.append((relpath, scope, _reason(node.exc)))
        for child in ast.iter_child_nodes(node):
            _visit(child, scope)

    _visit(tree, "<module>")
    return found


def _harness_declaration_sites() -> list[tuple[str, str, str]]:
    sites: list[tuple[str, str, str]] = []
    for path in sorted(_HARNESS_DIR.glob("*.py")):
        # The harness's own test_*.py construct E2EUnsupportedSetup to test the
        # realize mechanism itself; they declare nothing about scenarios.
        # _realize.py defines the exception/factory.
        if path.name.startswith("test_") or path.name == "_realize.py":
            continue
        relpath = f"tests/harness/{path.name}"
        sites.extend(find_unsupported_declarations(ast.parse(path.read_text()), relpath))
    return sorted(sites)


# The pinned declaration set: every "this setup intent has no live-server
# surface" declaration. Adding one moves scenarios out of live grading — that
# is sometimes right (format-injection has no surface), but never silent.
EXPECTED_UNSUPPORTED_DECLARATIONS: frozenset[tuple[str, str, str]] = frozenset(
    {
        # Added by #1721, both for scenarios that did not exist before it. Noted
        # against this pin's own direction of travel (the remediation plan wants this
        # SET to shrink): neither scenario could exist at all without the declaration,
        # and the alternative for the creative one was a nodeid entry in
        # e2e_rest_known_failures.txt, which is the registry this mechanism exists to
        # replace. Both grade fully on the three in-process transports.
        (
            "tests/harness/capabilities.py",
            "make_adapter_channel_enumeration_fail",
            "the fault is 'iterating the adapter's default_channels raises', which is a property of the in-process adapter object. Unlike 'unavailable' -- which get_adapter_class_for_tenant honours from AdapterConfig.test_behavior -- production has no read that could make channel ENUMERATION fail on a real adapter, and adding one would put a fault-injection branch in production for a test's benefit. The non-cascade it grades is transport-independent (one function's control flow in capabilities.py), so the in-process transports grade it fully",
        ),
        (
            "tests/harness/creative_sync.py",
            "configure_agent_served_creative",
            "the out-of-transaction effects this configures are observed as CALLS on in-process mocks (registry.build_creative / preview_creative, and the _ai_review_executor submit). Over real HTTP those objects live in the server process, so the assertions have nothing to read and the real creative agent answers for itself -- the scenario would grade the agent, not the gates. Observing them e2e needs effect capture at the server (a request sink + a review-verdict read-back), which is its own build",
        ),
        # The CREATIVE_REJECTED-with-reasons scenario: the rejection production makes when
        # the agent answers a preview with nothing and the creative has no media_url. The
        # "nothing" is an in-process mock answer; the live agent answers for itself. Same
        # family and same reason as configure_agent_served_creative, which this composes.
        (
            "tests/harness/creative_sync.py",
            "configure_agent_no_preview",
            "a live creative agent answers a preview request for itself; it cannot be told to answer "
            "with no previews, which is the rejection this configures",
        ),
        # Added by prkv.16 (salesagent-aqqfx.6), noted against this pin's own
        # shrink-only direction of travel like the #1721 and prkv.18 precedents
        # above. The workflow-step write path (GH #2002) is graded by comparing
        # what an INDEPENDENT database connection can see at two instants; both
        # halves of that observation are bound to THIS process (a second pooled
        # connection to the engine under test, and an in-process mock of
        # _send_creative_notifications), and neither has a live-server
        # equivalent without building a server-side effect/read-back surface.
        # The obligation grades fully on the three in-process transports.
        (
            "tests/harness/creative_sync.py",
            "observe_effects_at_notification",
            "the notification observer is a side_effect installed on an in-process mock of "
            "_send_creative_notifications. Under e2e_rest the sync runs in the Docker server "
            "process, where that mock does not exist and the real notifier answers -- nothing "
            "would ever be recorded and the ordering assertions would read None. Observing it "
            "e2e needs effect capture at the server (a notification sink the test can poll), "
            "which is its own build",
        ),
        (
            "tests/harness/creative_sync.py",
            "committed_sync_effects",
            "every field of this snapshot is read over a second pooled connection to the engine "
            "THIS process is bound to. Under e2e_rest the request runs in the Docker server "
            "process against its own database, so the read answers about the wrong database -- it "
            "would report zero rows on every branch and grade nothing, which is strictly worse than "
            "not grading. Observing it e2e needs a server-side read-back surface (a tenant-scoped "
            "admin endpoint over workflow_steps / object_workflow_mapping / creative_assignments), "
            "which is its own build",
        ),
        (
            "tests/harness/_mixins.py",
            "set_adapter_error",
            "adapter fault-injection has no server surface; needs an ADCP_TESTING fault-injection control (#1418)",
        ),
        # Added by 9921b8189 ("uc011 scenarios that never dispatched now let the seller
        # answer"), and noted against this pin's shrink-only direction like the
        # precedents above. It is a NET GAIN even so: the step it replaces constructed an
        # AdCPSalesAgentError in the TEST process and returned without dispatching, so the
        # scenario graded an object the test had just made and never exercised the
        # boundary's error translation at all -- on any transport. Now three transports
        # grade it for real and one declares it cannot, which is strictly more coverage
        # than four transports grading nothing. The declaration is honest rather than
        # convenient: the fault is injected at the REPOSITORY, one layer below the impl,
        # so everything between it and the wire stays real -- and reaching that layer
        # needs in-process patching, which a live HTTP server has no equivalent of.
        # Lifting it needs a server-side fault-injection control, the same build
        # set_adapter_error above is waiting on (#1418).
        (
            "tests/harness/account_sync.py",
            "fail_the_sync_internally",
            "the live server exposes no fault-injection surface for sync_accounts",
        ),
        # #1802 replaces the old _NO_E2E_REST_TAGS silent
        # parametrize-drop (invisible to both detectors in this module) with a
        # reviewable, pinned declaration. then_webhook_skipped_no_post's other
        # two assertions (success is False; env.delivery_attempts == 0) are
        # genuinely wire-observable and now run unconditionally on e2e_rest —
        # only the retry-schedule discriminator is declared unsupported here.
        (
            "tests/harness/_mixins.py",
            "assert_no_retry_schedule_entered",
            "the seam's BR-RULE-029 retry-schedule sleep count is process-local "
            "(env.mock['sleep']), not observable across the Docker HTTP boundary",
        ),
        # Added by the rfc9421-onto-#1721 merge, and noted against this pin's
        # shrink-only direction of travel like the #1721 and prkv.16/.18 precedents
        # above. WHAT CHANGED UNDERNEATH, precisely: the 9421 branch gave
        # ``deliver_webhook`` an e2e realization (``_deliver_via_live_server``) so the
        # DEPLOYMENT makes the delivery over e2e_rest instead of the runner — the
        # transport bypass salesagent-n78j0.1.4 existed to remove. The #1721 side had
        # graduated T-UC-004-webhook-retry-5xx and -no-retry-4xx off
        # _UC004_E2E_WEBHOOK_INTERNAL_TAGS on the explicit premise that "the webhook
        # SENDER runs in the test process on every transport, e2e_rest included"
        # (tests/bdd/conftest.py). That premise is false on the merged tree, and the
        # two legs failed on exactly the two assertions it protected: a sleep list
        # with no calls and a log handler with no records.
        #
        # These two declarations are what the graduation is worth keeping FOR. Routing
        # the tags back would take the whole scenario out of e2e grading again;
        # declaring the two runner-local READS instead leaves the payload-compliance,
        # notification_type, retry-COUNT, no-retry and marked-failed Thens grading the
        # live server's real delivery, which is more than either parent graded. They
        # come out when the capture service records receipt times (the backoff
        # schedule becomes gap arithmetic, graded by the same shared
        # assert_backoff_schedule with jitter=None) and when a server-side log
        # read-back surface exists.
        #
        # GRADUATED: assert_retry_backoff_schedule. Its declaration named the missing
        # capability -- "the capture service records no receipt times to reconstruct them
        # from" -- and that capability was BUILT rather than the declaration re-justified:
        # every capture now carries a monotonic ``received_at``
        # (tests/e2e/webhook_capture_service.py::_wire_entry), so the gaps between
        # consecutive receipts ARE the waits, measured on the wire. The e2e realization
        # (_mixins.py::_e2e_assert_retry_backoff_schedule) grades the wait COUNT exactly and
        # each gap against its own rung of the 1s/2s/4s schedule with the jitter range as the
        # upper bound. Only the white-box DRAW COUNT has no wire projection, and the
        # realization says so in place rather than leaving a silent difference.
        # #1802: get_service() under e2e_rest is a fresh, in-process
        # WebhookDeliveryService never touched by the live server's actual
        # delivery — service._circuit_breakers has no wire surface at all.
        (
            "tests/harness/_mixins.py",
            "assert_circuit_breaker_failure_recorded",
            "get_service() constructs a fresh in-process WebhookDeliveryService under e2e_rest, "
            "disconnected from the live server's real circuit-breaker state — no wire surface",
        ),
        # The circuit-breaker SEEDS, added alongside the reader above rather than
        # after it. Declaring only the Then was the hole: the Givens still ran, so the
        # scenarios arranged a breaker in the runner's own process and then drove REAL
        # deliveries against an endpoint programmed to fail — which is how they passed
        # while production did the opposite, and how one per-worker server was driven
        # unhealthy. Two independent disqualifiers, spelled out at
        # ``_mixins._BREAKER_IS_PROCESS_LOCAL``: wrong process (the runner's
        # WebhookDeliveryService, not the server's) and wrong sender (the e2e delivery
        # path is ProtocolWebhookService, which has no breaker at all).
        #
        # Parking is honest rather than lossy: AdCP 3.1.1 mandates no circuit breaker —
        # webhooks.mdx :528 is its only, descriptive mention and nothing in
        # dist/compliance/3.1.1/ grades it. The spec's buyer-visible observable is
        # webhook_activity[] on get_media_buys (include_webhook_activity;
        # core/webhook-activity-record.json), which this repo does not implement. These
        # five come OUT when it lands and the Thens are rewritten against it.
        # "<dynamic>" because all five share ONE reason constant
        # (``_mixins._BREAKER_IS_PROCESS_LOCAL``) rather than five copies of a long
        # string, and the detector can only read a literal. Same precedent as
        # _validate_registry_formats below. What the pin still locks is the SET of
        # parked method names, which is the tracking property that matters; the reason
        # itself is reviewable at the source, where it is stated once.
        ("tests/harness/_mixins.py", "seed_breaker_failures", "<dynamic>"),
        ("tests/harness/_mixins.py", "set_breaker_state", "<dynamic>"),
        ("tests/harness/_mixins.py", "elapse_breaker_timeout", "<dynamic>"),
        ("tests/harness/_mixins.py", "drive_breaker_transition", "<dynamic>"),
        ("tests/harness/_mixins.py", "breaker_snapshot", "<dynamic>"),
        (
            "tests/harness/creative_formats.py",
            "_validate_registry_formats",
            "live stack always serves the agent catalog; an empty catalog cannot be realized over e2e",
        ),
        ("tests/harness/creative_formats.py", "_validate_registry_formats", "<dynamic>"),
        (
            "tests/harness/capabilities.py",
            "break_tenant_config_db",
            "no production DB fault hook; TenantConfigUoW read failure cannot be injected over real HTTP",
        ),
        (
            "tests/harness/capabilities.py",
            "set_supported_versions",
            "no production tenant-config surface for the seller's advertised adcp version set "
            "(SUPPORTED_ADCP_VERSIONS/MAJORS are process-wide constants) — a "
            "module-constant monkeypatch cannot cross a real HTTP process boundary",
        ),
        (
            "tests/harness/capabilities.py",
            "set_build_version",
            "no production tenant-config surface for the seller's advertised build_version "
            "(src.core.version.get_version() is a process-wide package-metadata read) "
            "— cannot be injected over real HTTP",
        ),
        (
            "tests/harness/capabilities.py",
            "set_idempotency_posture",
            "no production tenant-config surface for the adcp.idempotency posture "
            "(get_idempotency_posture() is a process-wide provider) — a "
            "module-function monkeypatch cannot cross a real HTTP process boundary",
        ),
        # Added by prkv.18 (salesagent-kloo2), noted against this pin's own
        # shrink-only direction of travel, same as the #1721 precedent entries
        # above: BaseTestEnv.inject_untyped_exception patches a skill's _impl
        # to raise a bare exception directly in-process. There is no live-server
        # control that makes an already-running remote process's business logic
        # raise on demand -- that would require its own fault-injection channel
        # (e.g. an ADCP_TESTING header the production code branches on), which
        # is a separate build, not a gap in this scenario's own test setup. The
        # obligation still grades fully on the three in-process transports
        # (a2a/mcp/rest).
        # Added by salesagent-b341x.19's inherited-failure sweep. set_gemini_api_key was
        # created by b341x.9's routing, which moved four env.mock reaches out of uc006 step
        # bodies and INTO the harness — where this file's scan can finally see them. The
        # method sets an in-process config mock, and the key it sets belongs to the SERVER's
        # own configuration, so over e2e_rest there is no surface to set it on: the honest
        # options were "declare unrealizable" or "silently no-op and assert against
        # unconfigured server state". The scenarios it gates (BR-RULE-036's generative-build
        # preconditions) still grade fully on a2a/mcp/rest.
        (
            "tests/harness/creative_sync.py",
            "set_gemini_api_key",
            "GEMINI_API_KEY is read from the SERVER's own configuration, and e2e_rest talks to "
            "a process this harness does not configure — there is no surface for setting or "
            "clearing another process's env-derived config mid-scenario",
        ),
        (
            "tests/harness/_base.py",
            "inject_untyped_exception",
            "no server fault-injection surface for a genuinely untyped exception on a live "
            "remote process (same structural limitation prkv.8's own e2e-verify atom hit)",
        ),
    }
)


def test_harness_unsupported_declarations_match_pin() -> None:
    """Every env-level E2EUnsupportedSetup declaration is pinned exactly."""
    actual = frozenset(_harness_declaration_sites())
    added = actual - EXPECTED_UNSUPPORTED_DECLARATIONS
    removed = EXPECTED_UNSUPPORTED_DECLARATIONS - actual
    assert actual == EXPECTED_UNSUPPORTED_DECLARATIONS, (
        "E2EUnsupportedSetup declarations in tests/harness/ drifted from the pin.\n"
        "Declaring a setup unrealizable moves its scenarios out of live grading — "
        "update EXPECTED_UNSUPPORTED_DECLARATIONS in the same change and justify it.\n"
        f"New declarations: {sorted(added)}\n"
        f"Removed declarations: {sorted(removed)}"
    )


def test_unsupported_declarations_never_cite_a_beads_id() -> None:
    """CLAUDE.md: a tracked gap cites a GH issue/PR number, never a local beads
    id -- beads ids don't resolve for outside contributors (#1721).

    Checks the LIVE source, not just the pin, so a new declaration can't slip
    in with a beads citation even before EXPECTED_UNSUPPORTED_DECLARATIONS is
    updated to match it.
    """
    offenders = [
        (relpath, scope, reason) for relpath, scope, reason in _harness_declaration_sites() if "salesagent-" in reason
    ]
    assert not offenders, (
        "escape-hatch reason(s) cite an unresolvable local beads id instead of a GH issue/PR "
        f"number (or no ticket, per the break_tenant_config_db precedent): {offenders}"
    )


# ---------------------------------------------------------------------------
# Meta-tests: the LIVE detectors catch known-bad mutations (#1498 discipline)
# ---------------------------------------------------------------------------

_SYNTHETIC_CONFTEST = """
def pytest_collection_modifyitems(config, items):
    for item in items:
        nodeid = item.nodeid
        marker_names = {m.name for m in item.iter_markers()}
        is_e2e_rest = "[e2e_rest" in nodeid
        if is_e2e_rest and "T-UC-099-new-hatch" in marker_names:
            item.add_marker(pytest.mark.xfail(reason="sneaky reroute", strict=False))
        if "T-UC-098-unrelated" in marker_names:
            item.add_marker(pytest.mark.xfail(reason="not e2e_rest gated", strict=False))
        if is_e2e_rest and "no-xfail-here" in marker_names:
            item.add_marker(pytest.mark.skip(reason="skip is not xfail"))
"""


def test_detector_catches_new_xfail_route_and_ignores_ungated_ones() -> None:
    """The live route detector reports exactly the is_e2e_rest-gated xfail."""
    conditions = find_e2e_rest_xfail_conditions(ast.parse(_SYNTHETIC_CONFTEST))
    assert conditions == ["is_e2e_rest and 'T-UC-099-new-hatch' in marker_names"]


_SYNTHETIC_HARNESS = """
from tests.harness._realize import E2EUnsupportedSetup, e2e_unsupported, realize_e2e


class SomeEnvMixin:
    @realize_e2e(e2e_unsupported("brand-new unrealizable intent"))
    def set_new_thing(self, value):
        self.mock["thing"].value = value

    def other_method(self, formats):
        if not formats:
            raise E2EUnsupportedSetup(f"dynamic {formats!r} reason")
"""


def test_detector_catches_new_unsupported_declarations() -> None:
    """The live declaration detector attributes decorator args to the method."""
    sites = find_unsupported_declarations(ast.parse(_SYNTHETIC_HARNESS), "tests/harness/fake.py")
    assert sorted(sites) == [
        ("tests/harness/fake.py", "other_method", "<dynamic>"),
        ("tests/harness/fake.py", "set_new_thing", "brand-new unrealizable intent"),
    ]


_SYNTHETIC_HARNESS_WITH_BEADS_CITATION = """
from tests.harness._realize import e2e_unsupported, realize_e2e


class SomeEnvMixin:
    @realize_e2e(e2e_unsupported("some gap that cites a beads id — salesagent-abcd"))
    def set_new_thing(self, value):
        self.mock["thing"].value = value
"""


def test_beads_citation_check_catches_a_synthetic_offender() -> None:
    """Negative meta-test for test_unsupported_declarations_never_cite_a_beads_id:
    a synthetic declaration citing a beads id must be flagged as an offender by
    the same substring check the real test runs, so the check itself can't
    silently go blind (#1498 discipline)."""
    sites = find_unsupported_declarations(ast.parse(_SYNTHETIC_HARNESS_WITH_BEADS_CITATION), "tests/harness/fake.py")
    offenders = [(relpath, scope, reason) for relpath, scope, reason in sites if "salesagent-" in reason]
    assert offenders == [("tests/harness/fake.py", "set_new_thing", "some gap that cites a beads id — salesagent-abcd")]


def test_beads_citation_check_does_not_flag_gh_citations_or_ticket_free_reasons() -> None:
    """Positive/control case: a GH-issue citation and a ticket-free structural
    reason (the break_tenant_config_db precedent) must NOT be flagged."""
    sites = find_unsupported_declarations(ast.parse(_SYNTHETIC_HARNESS), "tests/harness/fake.py")
    offenders = [(relpath, scope, reason) for relpath, scope, reason in sites if "salesagent-" in reason]
    assert offenders == []


_SYNTHETIC_GENERATE_TESTS_CLEAN = """
def pytest_generate_tests(metafunc):
    transports = [Transport.A2A, Transport.MCP, Transport.REST]
    ids = ["a2a", "mcp", "rest"]
    if os.environ.get("BDD_E2E_ENABLED") == "true" and not no_rest_uc:
        transports.append(Transport.E2E_REST)
        ids.append("e2e_rest")
    metafunc.parametrize("ctx", transports, ids=ids, indirect=True)
"""

# The disease this guards against: a silent, tag-set-gated re-introduction of
# _NO_E2E_REST_TAGS — ANDing a new condition onto the append so a specific
# scenario is dropped without an xfail marker anywhere.
_SYNTHETIC_GENERATE_TESTS_SNEAKY_REROUTE = """
def pytest_generate_tests(metafunc):
    transports = [Transport.A2A, Transport.MCP, Transport.REST]
    ids = ["a2a", "mcp", "rest"]
    if os.environ.get("BDD_E2E_ENABLED") == "true" and not no_rest_uc and not (marker_names & _NEW_QUIET_TAGS):
        transports.append(Transport.E2E_REST)
        ids.append("e2e_rest")
    metafunc.parametrize("ctx", transports, ids=ids, indirect=True)
"""

# Today's shape: the append lives in a module-level helper and the hook decides
# by ARGUMENT. Both pins have to keep working across this refactor, because it
# is the shape the merged tree is actually in.
_SYNTHETIC_GENERATE_TESTS_HELPER = """
def _parametrize_ctx(metafunc, base_transports, base_ids, e2e_member, e2e_id):
    transports = list(base_transports)
    ids = list(base_ids)
    if e2e_member is not None and os.environ.get("BDD_E2E_ENABLED") == "true":
        transports.append(e2e_member)
        ids.append(e2e_id)
    metafunc.parametrize("ctx", transports, ids=ids, indirect=True)


def pytest_generate_tests(metafunc):
    transports = [Transport.A2A, Transport.MCP, Transport.REST]
    ids = ["a2a", "mcp", "rest"]
    _parametrize_ctx(metafunc, transports, ids, Transport.E2E_REST, "e2e_rest")
"""

# The same disease written in the helper shape: the drop moves into the ARGUMENT,
# leaving the gate condition itself untouched.
_SYNTHETIC_GENERATE_TESTS_SNEAKY_CALL_SITE = """
def _parametrize_ctx(metafunc, base_transports, e2e_members):
    transports = list(base_transports)
    if e2e_members and os.environ.get("BDD_E2E_ENABLED") == "true":
        transports.extend(e2e_members)
    metafunc.parametrize("ctx", transports, ids=[t.value for t in transports], indirect=True)


def pytest_generate_tests(metafunc):
    transports = [Transport.A2A, Transport.MCP, Transport.REST]
    ids = ["a2a", "mcp", "rest"]
    quiet = marker_names & _NEW_QUIET_TAGS
    _parametrize_ctx(metafunc, transports, [] if quiet else [Transport.E2E_REST])
"""


def test_gate_detector_matches_the_inline_shape() -> None:
    """The live gate detector reports the condition of an inline append gate."""
    gates = find_e2e_rest_parametrize_gates(ast.parse(_SYNTHETIC_GENERATE_TESTS_CLEAN))
    assert gates == ("os.environ.get('BDD_E2E_ENABLED') == 'true' and (not no_rest_uc)",)


def test_gate_detector_catches_sneaky_e2e_rest_reroute() -> None:
    """A new tag-set ANDed onto the gate changes the detected condition — would fail the pin."""
    gates = find_e2e_rest_parametrize_gates(ast.parse(_SYNTHETIC_GENERATE_TESTS_SNEAKY_REROUTE))
    assert gates != EXPECTED_E2E_REST_PARAMETRIZE_GATES
    assert "_NEW_QUIET_TAGS" in gates[0]


def test_gate_detector_sees_a_helper_extracted_append() -> None:
    """The reason the scan is module-wide: today's gate is not inside the hook.

    A detector anchored to ``pytest_generate_tests`` reports nothing here — not
    a drift, a blind spot.
    """
    gates = find_e2e_rest_parametrize_gates(ast.parse(_SYNTHETIC_GENERATE_TESTS_HELPER))
    assert gates == ("e2e_member is not None and os.environ.get('BDD_E2E_ENABLED') == 'true'",)


def test_gate_detector_ignores_a_non_appending_read_of_the_flag() -> None:
    """The xdist-incompatibility check reads BDD_E2E_ENABLED and raises; not a gate."""
    source = (
        "def pytest_configure(config):\n"
        "    if os.environ.get('BDD_E2E_ENABLED') == 'true' and numprocesses:\n"
        "        raise pytest.UsageError('no xdist')\n"
    )
    assert find_e2e_rest_parametrize_gates(ast.parse(source)) == ()


def test_append_expression_detector_pins_the_call_site_condition() -> None:
    """The hook's E2E_REST argument is reported outermost, condition included."""
    expressions = find_e2e_rest_append_expressions(ast.parse(_SYNTHETIC_GENERATE_TESTS_HELPER))
    assert expressions == ("_parametrize_ctx(metafunc, transports, ids, Transport.E2E_REST, 'e2e_rest')",)


def test_append_expression_detector_catches_a_sneaky_call_site_drop() -> None:
    """A drop written in the ARGUMENT leaves the gate condition untouched.

    This is the shape detector 3's gate pin cannot see, and the reason the call
    sites are pinned separately.
    """
    tree = ast.parse(_SYNTHETIC_GENERATE_TESTS_SNEAKY_CALL_SITE)
    assert find_e2e_rest_parametrize_gates(tree) == EXPECTED_E2E_REST_PARAMETRIZE_GATES
    expressions = find_e2e_rest_append_expressions(tree)
    assert expressions != EXPECTED_E2E_REST_APPEND_EXPRESSIONS
    assert "_NEW_QUIET_TAGS" not in expressions[0]
    assert expressions == ("_parametrize_ctx(metafunc, transports, [] if quiet else [Transport.E2E_REST])",)


# ---------------------------------------------------------------------------
# Detector 5: env.mock reaches in the BDD step tree
# ---------------------------------------------------------------------------

_STEPS_DIR = _REPO_ROOT / "tests" / "bdd" / "steps"

#: Matches the reach the census counts (``scripts/audit/build_bdd_decisions.py``'s
#: ``step-reaches-env-mock``), so the two instruments cannot disagree about what a
#: reach IS while disagreeing about how many there are.
_ENV_MOCK_REACH = re.compile(r"env\.mock")


def find_env_mock_reaches(tree: ast.Module, path: str) -> list[tuple[str, str]]:
    """Every function in a step module whose body reaches ``env.mock``.

    EVERY function, not only the decorated step ones. The census counts step bodies
    and reports 37; this guard finds 44, because seven reaches sit in undecorated
    helpers those steps call (``_assert_audit_logged_mock``, ``_inject_privilege_error``,
    ``_configure_adapter_manual_approval``, ...). A reach relocated into a helper is
    exactly as unroutable as one in the step body, and a detector that saw only
    decorated functions would let the 45th site be added tomorrow by putting it in a
    helper — the blind-to-part-of-its-domain failure this file's meta-tests exist to
    prevent.
    """
    return [
        (path, node.name)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _ENV_MOCK_REACH.search(ast.unparse(node))
    ]


def _env_mock_reach_sites() -> list[tuple[str, str]]:
    """Every ``env.mock`` reach in ``tests/bdd/steps/``, as (path, function)."""
    sites: list[tuple[str, str]] = []
    for path in sorted(_STEPS_DIR.rglob("*.py")):
        rel = path.relative_to(_REPO_ROOT).as_posix()
        sites.extend(find_env_mock_reaches(ast.parse(path.read_text()), rel))
    return sites


#: THE FIFTH ROUTE OUT OF LIVE GRADING, and the one route 3 cannot cover.
#:
#: ``E2EUnsupportedSetup`` is how this repo declares a setup unrealizable against a live
#: server — but the declaration mechanism lives in ``tests/harness/`` and detector 4's
#: scan is HARNESS-ONLY. A step body that reaches into ``env.mock[...]`` bypasses the
#: realization seam without being declarable anywhere: it silently works in-process and
#: silently cannot work on e2e_rest, which is the same ungraded-but-unmarked outcome the
#: other four routes are locked against. Until a reach is routed through a named harness
#: method it is invisible to every existing lock, and a 45th could be added with nothing
#: going red.
#:
#: EXACT SET, NOT SHRINK-ONLY. THIS IS A DELIBERATE OVERRIDE OF THE TASK THAT FILED IT
#: (salesagent-b341x.9) AND OF THE "shrink-only ratchet" WORDING IN THE PARENT EPIC. It is
#: not a deviation to be tidied back; read this before "restoring" a ceiling.
#:
#: Three reasons, in order of weight:
#:
#: 1. A ceiling derived from the pin CAN NEVER FAIL INDEPENDENTLY — this file's own
#:    docstring says so at the top, about its other four pins, and the argument does not
#:    change for the fifth. An exact set fails in BOTH directions.
#: 2. The record settles it empirically. This population was 41 step-body reaches when the
#:    task was filed and is 37 now, because four gemini Givens in uc006 were routed through
#:    a real ``env.set_gemini_api_key()`` seam. Under a ceiling pinned at 41 those four
#:    repairs pass unnoticed AND leave four slots of headroom — a new reach could then be
#:    added with nothing going red. That is the fence-with-headroom defect arriving through
#:    the very mechanism meant to prevent it.
#: 3. A repair costing one reviewable line of pin update is a FEATURE. Routing a reach away
#:    is the direction of travel this pin exists to encourage, and it should be recorded
#:    rather than silently absorbed.
EXPECTED_ENV_MOCK_REACHES: frozenset[tuple[str, str]] = frozenset(
    {
        ("tests/bdd/steps/_outcome_helpers.py", "_assert_audit_adapter_mock"),
        ("tests/bdd/steps/_outcome_helpers.py", "_assert_audit_approval_mock"),
        ("tests/bdd/steps/_outcome_helpers.py", "_assert_audit_logged_mock"),
        ("tests/bdd/steps/domain/codes_open_vocabulary.py", "given_seller_will_reject"),
        ("tests/bdd/steps/domain/uc002_create_media_buy.py", "given_media_buy_already_created_same_key"),
        ("tests/bdd/steps/domain/uc002_create_media_buy.py", "given_tenant_auto_approval"),
        ("tests/bdd/steps/domain/uc002_create_media_buy.py", "then_slack_notification_sent"),
        ("tests/bdd/steps/domain/uc002_nfr.py", "given_observe_high_value_alerts"),
        ("tests/bdd/steps/domain/uc002_nfr.py", "then_auth_before_business_logic"),
        ("tests/bdd/steps/domain/uc002_nfr.py", "then_no_adapter_calls"),
        ("tests/bdd/steps/domain/uc002_nfr.py", "then_response_within_sla"),
        ("tests/bdd/steps/domain/uc003_ext_error_scenarios.py", "_inject_privilege_error"),
        ("tests/bdd/steps/domain/uc003_ext_error_scenarios.py", "given_adapter_error_during_update"),
        ("tests/bdd/steps/domain/uc003_ext_error_scenarios.py", "given_creative_sync_fails"),
        ("tests/bdd/steps/domain/uc003_ext_error_scenarios.py", "given_media_buy_uncancellable"),
        ("tests/bdd/steps/domain/uc003_ext_error_scenarios.py", "given_update_requires_admin"),
        ("tests/bdd/steps/domain/uc003_update_media_buy.py", "given_adapter_result"),
        ("tests/bdd/steps/domain/uc003_update_media_buy.py", "given_tenant_approval_mode"),
        ("tests/bdd/steps/domain/uc004_delivery.py", "then_no_billing"),
        ("tests/bdd/steps/domain/uc004_delivery.py", "then_single_probe"),
        ("tests/bdd/steps/domain/uc004_delivery.py", "then_webhook_skipped_no_post"),
        ("tests/bdd/steps/domain/uc006_sync_creatives.py", "_assert_generative_build"),
        ("tests/bdd/steps/domain/uc006_sync_creatives.py", "_assert_standard_processing"),
        ("tests/bdd/steps/domain/uc006_sync_creatives.py", "given_creative_agent_is_reachable"),
        ("tests/bdd/steps/domain/uc006_sync_creatives.py", "given_creative_with_unknown_format"),
        ("tests/bdd/steps/domain/uc006_sync_creatives.py", "given_creative_with_unreachable_agent"),
        ("tests/bdd/steps/domain/uc006_sync_creatives.py", "then_creative_has_generated_content"),
        ("tests/bdd/steps/domain/uc006_sync_creatives.py", "then_creative_validated_by_agent"),
        ("tests/bdd/steps/domain/uc006_sync_creatives.py", "then_generative_build_skipped"),
        ("tests/bdd/steps/domain/uc006_sync_creatives.py", "then_generative_build_uses_prompt"),
        ("tests/bdd/steps/domain/uc006_sync_creatives.py", "then_invoke_generative_with_asset_prompt"),
        ("tests/bdd/steps/domain/uc006_sync_creatives.py", "then_processed_as_generative"),
        ("tests/bdd/steps/domain/uc019_query_media_buys.py", "given_adapter_exists"),
        ("tests/bdd/steps/domain/uc019_query_media_buys.py", "given_adapter_no_realtime"),
        ("tests/bdd/steps/domain/uc019_query_media_buys.py", "given_adapter_no_reporting"),
        ("tests/bdd/steps/domain/uc019_query_media_buys.py", "given_adapter_supports_reporting"),
        ("tests/bdd/steps/generic/given_media_buy.py", "_configure_adapter_manual_approval"),
        ("tests/bdd/steps/generic/given_media_buy.py", "given_ad_server_rejects_creative_upload"),
        ("tests/bdd/steps/generic/given_media_buy.py", "given_adapter_error"),
        ("tests/bdd/steps/generic/given_media_buy.py", "given_adapter_success"),
        ("tests/bdd/steps/generic/then_error.py", "then_no_new_ad_platform_order"),
        ("tests/bdd/steps/generic/then_media_buy.py", "then_adapter_executed"),
        # then_success.py::then_no_real_api_calls REMOVED 2026-09-15 — the good
        # direction this pin exists to encourage, and the same repair uc006's gemini
        # Givens took. Its reach was ``any(mock.called for mock in env.mock.values())``,
        # which answers "did production run its external seams" only in process; over
        # e2e_rest the mocks are in the wrong process and it reported False for a call
        # the live server served (the UC-018 sandbox scenario failed on it in
        # innet_150926_0258). It now asks ``env.external_seams_exercised``, a named
        # harness accessor whose e2e branch reads the audit row the server wrote.
        #
        # LATER THE SAME DAY: the step itself is gone, sentence and all. The obligation it
        # bound ("no real ad platform API calls") has no wire observable, is true by
        # construction inside a mocked harness, and production violates it (GH #2074:
        # resolve_account() discards the sandbox flag, so a sandbox account reaches the real
        # ad server). See the note at the foot of tests/bdd/steps/generic/then_success.py.
    }
)


def test_env_mock_reaches_match_the_pin() -> None:
    """Every ``env.mock`` reach in the step tree is pinned exactly."""
    actual = frozenset(_env_mock_reach_sites())
    added = actual - EXPECTED_ENV_MOCK_REACHES
    removed = EXPECTED_ENV_MOCK_REACHES - actual
    assert actual == EXPECTED_ENV_MOCK_REACHES, (
        "env.mock reaches in tests/bdd/steps/ drifted from the pin.\n"
        "A step reaching into the mock registry bypasses the realization seam: it works "
        "in-process, cannot work on e2e_rest, and is declarable nowhere — E2EUnsupportedSetup "
        "lives in tests/harness/ and this file's harness scan cannot see a step body.\n"
        "ADDING one needs justification here. REMOVING one is the good direction — route it "
        "through a named harness method, as uc006's gemini Givens were — and still updates the "
        "pin in the same change.\n"
        f"New reaches: {sorted(added)}\n"
        f"Removed reaches: {sorted(removed)}"
    )


def test_env_mock_detector_sees_a_reach_in_an_undecorated_helper() -> None:
    """The blindness this detector is shaped to avoid: a reach moved into a helper.

    A detector keyed on the given/when/then decorators would return () here, and the
    45th site could be added tomorrow by writing it as a helper.
    """
    source = (
        "def _configure(env):\n"
        "    env.mock['adapter'].return_value.create.side_effect = RuntimeError\n"
        "\n"
        "@then('it works')\n"
        "def then_it_works(ctx):\n"
        "    _configure(ctx['env'])\n"
    )
    assert find_env_mock_reaches(ast.parse(source), "x.py") == [("x.py", "_configure")]


def test_env_mock_detector_ignores_a_step_that_does_not_reach() -> None:
    source = "@then('it works')\ndef then_it_works(ctx):\n    assert ctx['env'].delivered_requests\n"
    assert find_env_mock_reaches(ast.parse(source), "x.py") == []
