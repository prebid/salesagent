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
    "'T-UC-002-alt-manual' in marker_names and (is_mcp or is_rest or is_e2e_rest)",
    "'T-UC-004-boundary-ownership' in marker_names and is_e2e_rest and ('differs from owner' in nodeid)",
    "'T-UC-004-dim-sortby-fallback' in marker_names and is_e2e_rest",
    "(is_rest or is_e2e_rest) and 'T-UC-019-boundary-principal' in marker_names",
    "(is_rest or is_e2e_rest) and 'T-UC-019-ext-a' in marker_names",
    "(is_rest or is_e2e_rest) and 'T-UC-019-partition-principal-invalid' in marker_names",
    "_samp_is_named and (is_rest or is_e2e_rest)",
    "is_e2e_rest",
    "is_e2e_rest and 'T-UC-002-nfr-001-enforcement' in marker_names",
    "is_e2e_rest and 'T-UC-004-daterange-end-only' in marker_names",
    "is_e2e_rest and 'T-UC-005-empty-catalog' in marker_names",
    "is_e2e_rest and 'Unknown string not in enum' in nodeid",
    "is_e2e_rest and any((s in nodeid for s in ('account exists', 'single match')))",
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
