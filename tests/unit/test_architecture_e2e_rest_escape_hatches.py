"""Exact-set lock for the e2e_rest xfail escape hatches (PR #1430 review).

The e2e_rest known-failures ledger has an exact-set lock
(``test_e2e_rest_ledger_state.py``), so a still-failing scenario cannot be
silently added to or dropped from the ledger. But the ledger is only one of
three routes that turn a failing e2e_rest scenario into a non-blocking xfail:

1. the nodeid ledger (locked);
2. an ``is_e2e_rest``-gated xfail route in the BDD conftest's
   ``pytest_collection_modifyitems`` (tag/substring conditions);
3. an env-level ``E2EUnsupportedSetup`` declaration in ``tests/harness/``
   (translated to xfail by the conftest report hook).

Routes 2 and 3 had no lock: a scenario relocated there escaped tracking
silently. This guard gives them the same exact-set treatment — adding OR
removing a route fails here, forcing a reviewable pin update in the same
change (the ledger discipline). There is deliberately no separate
``count <= len(pin)`` ratchet: the exact-set comparison already fails in both
directions, and a ceiling derived from the pin can never fail independently.

Both detectors are exercised by meta-tests below against known-bad synthetic
sources, so a detector regression cannot silently blind the lock (repo
precedent: #1498).
"""

from __future__ import annotations

import ast
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
    "'T-UC-004-boundary-ownership' in marker_names and is_e2e_rest and ('differs from owner' in nodeid)",
    "'T-UC-004-dim-sortby-fallback' in marker_names and is_e2e_rest",
    "(is_rest or is_e2e_rest) and 'T-UC-019-boundary-principal' in marker_names",
    # T-UC-019-ext-a / partition-principal-invalid identity_missing REST xfails
    # removed in #1950: AUTH_REQUIRED_SUGGESTION already matches the scenario.
    "_samp_is_named and (is_rest or is_e2e_rest)",
    "is_e2e_rest",
    "is_e2e_rest and 'T-UC-002-nfr-001-enforcement' in marker_names",
    "is_e2e_rest and 'T-UC-004-daterange-end-only' in marker_names",
    "is_e2e_rest and 'T-UC-005-empty-catalog' in marker_names",
    "is_e2e_rest and 'Unknown string not in enum' in nodeid",
    "is_e2e_rest and any((t.startswith('T-UC-019') for t in marker_names))",
    "is_e2e_rest and marker_names & _UC004_E2E_WEBHOOK_INTERNAL_TAGS",
    "is_e2e_rest and marker_names & _UC005_E2E_FIXTURE_INJECTION_TAGS",
    "is_e2e_rest and tag in uc005_filter_e2e_untestable",
    "is_e2e_rest and tag in uc005_filter_e2e_untestable",
    "marker_names & _UC005_PARTIAL_TAGS and (not is_e2e_rest)",
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
# Detector 3: the pytest_generate_tests-level E2E_REST parametrize gate
# ---------------------------------------------------------------------------
#
# #1802: _NO_E2E_REST_TAGS was a tag-set-gated `if` ANDed onto the
# condition that appends Transport.E2E_REST inside pytest_generate_tests — a
# silent, parametrize-time drop neither detector 1 (pytest_collection_modifyitems
# xfail routes) nor detector 2 (tests/harness/ E2EUnsupportedSetup) could see,
# because it lives in a different hook and never raises an xfail. This detector
# pins that gate's exact condition so the same shape can't reappear silently: a
# future `and not (marker_names & _SOME_NEW_TAGS)` addition changes the
# unparsed condition and fails the pin below until reviewed.


def find_e2e_rest_parametrize_gate(tree: ast.Module) -> str | None:
    """Return the unparsed condition of the `if` gating the E2E_REST append.

    Walks ``pytest_generate_tests`` for the ``if`` statement whose subtree
    reaches an ``E2E_REST`` attribute access (i.e. builds
    ``transports.append(Transport.E2E_REST)``). Returns ``None`` if no such
    gate exists (the append became unconditional, which is also a change this
    guard should surface via the pin comparison, not by vacuously passing).
    """
    hooks = [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "pytest_generate_tests"
    ]
    for hook in hooks:
        for node in ast.walk(hook):
            if not isinstance(node, ast.If):
                continue
            reaches_e2e_rest = any(isinstance(sub, ast.Attribute) and sub.attr == "E2E_REST" for sub in ast.walk(node))
            if reaches_e2e_rest:
                return ast.unparse(node.test)
    return None


# The pinned gate condition. Update IN THE SAME CHANGE as any edit to this
# condition, and say why in the commit — exactly like EXPECTED_XFAIL_ROUTES.
EXPECTED_E2E_REST_PARAMETRIZE_GATE = "os.environ.get('BDD_E2E_ENABLED') == 'true' and (not no_rest_uc)"


def test_e2e_rest_parametrize_gate_matches_pin() -> None:
    """The E2E_REST parametrize gate in pytest_generate_tests is pinned exactly."""
    tree = ast.parse(_BDD_CONFTEST.read_text())
    actual = find_e2e_rest_parametrize_gate(tree)
    assert actual == EXPECTED_E2E_REST_PARAMETRIZE_GATE, (
        "The E2E_REST parametrize gate in tests/bdd/conftest.py's pytest_generate_tests "
        "drifted from the pin.\n"
        "A scenario must NOT be silently dropped from e2e_rest parametrization by a new "
        "tag-set ANDed onto this condition — route it through E2EUnsupportedSetup instead "
        "(detector 2, above) so the exclusion is reviewable, or update "
        "EXPECTED_E2E_REST_PARAMETRIZE_GATE in the same change and justify it.\n"
        f"Expected: {EXPECTED_E2E_REST_PARAMETRIZE_GATE!r}\n"
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
    "single",
    "any((t.startswith(_ADMIN_TAG_PREFIX) for t in marker_names))",
    "any((t.startswith(tag_prefix) for t in marker_names)) and required_tag in marker_names",
    "no_rest_uc",
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
    """The early-return arm, proved synthetically like its rebind sibling."""
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
EXPECTED_SINGLE_TRANSPORT_TAGS: dict[str, str] = {"a2a_untyped_ingest": "A2A"}


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
        (
            "tests/harness/_mixins.py",
            "set_adapter_error",
            "adapter fault-injection has no server surface; needs an ADCP_TESTING fault-injection control (#1418)",
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
        # #1802: get_service() under e2e_rest is a fresh, in-process
        # WebhookDeliveryService never touched by the live server's actual
        # delivery — service._circuit_breakers has no wire surface at all.
        (
            "tests/harness/_mixins.py",
            "assert_circuit_breaker_failure_recorded",
            "get_service() constructs a fresh in-process WebhookDeliveryService under e2e_rest, "
            "disconnected from the live server's real circuit-breaker state — no wire surface",
        ),
        (
            "tests/harness/creative_formats.py",
            "_validate_registry_formats",
            "live stack always serves the agent catalog; an empty catalog cannot be realized over e2e",
        ),
        ("tests/harness/creative_formats.py", "_validate_registry_formats", "<dynamic>"),
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


def test_detector_matches_pin_on_clean_generate_tests() -> None:
    """The live gate detector reports the pinned condition on today's shape."""
    condition = find_e2e_rest_parametrize_gate(ast.parse(_SYNTHETIC_GENERATE_TESTS_CLEAN))
    assert condition == "os.environ.get('BDD_E2E_ENABLED') == 'true' and (not no_rest_uc)"


def test_detector_catches_sneaky_e2e_rest_reroute() -> None:
    """A new tag-set ANDed onto the gate changes the detected condition — would fail the pin."""
    condition = find_e2e_rest_parametrize_gate(ast.parse(_SYNTHETIC_GENERATE_TESTS_SNEAKY_REROUTE))
    assert condition != EXPECTED_E2E_REST_PARAMETRIZE_GATE
    assert "_NEW_QUIET_TAGS" in condition
