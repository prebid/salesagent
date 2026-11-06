"""Guard: the tests/ <-> scripts/audit liveness contract has ONE owner.

A real layering constraint — a pytest plugin must not import a CLI, and
``scripts/audit`` (imported *by* tests) cannot depend on ``tests/helpers`` — was
resolved by COPYING constants and RE-IMPLEMENTING lookups on each side. The two
env-route resolvers already disagree (``tests/bdd/conftest.py``'s
bucket-keys-plus-hardcoded-elif chain vs
``scripts/audit/scenario_liveness_join.registry_wired``'s tag-row-then-UC-bucket
lookup), producing the exact dormant-claim false positive the join was built to
eliminate.

Core Invariant : a real layering constraint is resolved by
extracting a shared, dependency-free contract module both sides import — never by
copying constants or re-implementing a lookup on each side.

This module grades the STRUCTURE of that extraction. The behavioral half — that
the two call sites now agree per collected scenario — lives in
``tests/collection/test_architecture_env_route_agreement.py``.

## The pinned contract (authored RED before implementation)

``scripts/audit/storyboard_spec.py`` is the owner: stdlib-only at module scope,
already imported by ~20 test modules, and importing a module that DEFINES a CLI
entry point does not EXECUTE it. It owns:

* ``resolve_env_route(marker_names, env_routes)`` — the ONE routing decision.
* ``parse_ledger_lines(path, *, grammar)`` — the ONE ledger line scan, with the
  grammar injected by the caller (the two ledgers have genuinely different
  grammars over different files; see ``_LEDGER_LOADERS``).
* ``ARTIFACT_ENV_VAR`` / the default artifact path / the ``T-UC-`` tag regex /
  the storyboard tag in ONE representation.

A tests-side leaf module owns ``derive_marker_names(node)`` — the ONE marker-set
derivation both ``tests/bdd/conftest.py`` and ``tests/bdd/scenario_liveness.py``
call. It cannot live in ``conftest.py`` (the plugin importing conftest is a
partial-import cycle) nor in ``storyboard_spec.py`` (stdlib-only, no pytest), so
per binding input G2 the implement atom RECORDS its home in the bead and fills
``_DERIVATION_ACCESSOR_MODULE`` below.

## Why the graded unit is REACHABILITY, not a function body

An implementer who moves the six marker-set-keyed prose branches into six new
``_detect_ucNNN_harness()`` helpers and calls ``resolve_env_route`` once at the
top of ``_harness_env`` passes a body-scoped guard verbatim, with the routing
decision one call-frame over and still invisible to the join — the house style
already demonstrates it (``_detect_delivery_harness`` derives its own marker set
and runs every UC-004 membership test, so the UC-004 branch contains zero
``iter_markers`` today). The graded unit is therefore AST reachability from
``_harness_env`` across ``tests/``: 47 defs over 23 modules today, and the whole
``tests/harness`` + ``tests/helpers`` closure contains ZERO marker/tag logic, so
the unit is satisfiable with exactly the two per-capability carve-outs below.

## The carve-outs are PER-CAPABILITY (binding input G1), never blanket

* the derivation accessor is exempt ONLY from the marker-DERIVATION ban (it IS
  the single definition site) — it is NOT exempt from the routing-predicate ban.
* ``resolve_env_route`` is exempt ONLY from the routing-PREDICATE ban (it IS the
  single routing site) — it is NOT a licence to derive markers there.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    assert_violations_match_allowlist,
    iter_call_expressions,
    parse_module,
    repo_root,
)
from tests.unit._liveness_contract_pins import (
    ABSORBED_HELPERS as _ABSORBED_HELPERS,
)
from tests.unit._liveness_contract_pins import (
    CONFTEST as _CONFTEST,
)
from tests.unit._liveness_contract_pins import (
    DERIVATION_ACCESSOR as _DERIVATION_ACCESSOR,
)
from tests.unit._liveness_contract_pins import (
    DERIVATION_ACCESSOR_MODULE as _DERIVATION_ACCESSOR_MODULE,
)
from tests.unit._liveness_contract_pins import (
    FIX_HINT as _FIX_HINT,
)
from tests.unit._liveness_contract_pins import (
    LEDGER_PARSER as _LEDGER_PARSER,
)
from tests.unit._liveness_contract_pins import (
    OWNER as _OWNER,
)
from tests.unit._liveness_contract_pins import (
    PLUGIN as _PLUGIN,
)
from tests.unit._liveness_contract_pins import (
    ROUTE_RESOLVER as _ROUTE_RESOLVER,
)

pytestmark = pytest.mark.arch_guard

# --- the pinned contract -----------------------------------------------------
# The names above are pinned in tests/unit/_liveness_contract_pins.py, shared
# with the behavioral grader so the two cannot drift apart.

# The two ledger loaders. They are NOT two implementations of one function: one
# reads plain pytest-nodeid lines out of tests/bdd/e2e_rest_known_failures.txt,
# the other parses the bracket grammar
# ``protocol::track::storyboard::step`` out of tests/storyboard/known_failures.txt.
# One parse function, grammar supplied by the caller.
_LEDGER_LOADERS = (
    (Path("tests/helpers/ledger.py"), "load_ledger_nodeids"),
    (Path("scripts/audit/ledger.py"), "load"),
)

# Modules that participate in the contract. tests/unit and tests/integration are
# deliberately out of scope: a test module naming a constant to assert on it is
# not a second definition site.
_CONTRACT_SCAN_DIRS = (Path("tests/bdd"), Path("tests/helpers"), Path("scripts/audit"))

# Copied constants, verified in-tree. Each must survive in exactly ONE module.
_SHARED_LITERALS = (
    ("artifact env var", frozenset({"BDD_LIVENESS_ARTIFACT"})),
    ("artifact default path", frozenset({"bdd_scenario_liveness.json"})),
    ("UC tag regex", frozenset({r"^T-UC-(\d{3})(?:-|$)"})),
    # The twins are not even identical: scenario_liveness.py:75 holds
    # "storyboard-v3.1" (no @) while storyboard_spec.py:401 holds
    # "@storyboard-v3.1" (with @), so any consumer comparing them must already
    # know which convention it holds. ONE representation, one accessor.
    ("storyboard tag", frozenset({"storyboard-v3.1", "@storyboard-v3.1"})),
)

_MARKER_DERIVATION_ATTRS = frozenset({"iter_markers", "own_markers"})

# An identifier that names a marker/tag collection. A routing predicate that
# tests one is a routing predicate wherever it is written.
_MARKERISH = re.compile(r"marker|tag", re.I)
# A routing tag written as a literal: the six prose branches key on these.
_TAG_LITERAL = re.compile(r"^(T-|UC-\d|ADMIN$|COMPAT)")

Symbol = tuple[str, str]  # (repo-relative module path, def name)


# --- AST plumbing ------------------------------------------------------------


def _repo_py_files(dirs: tuple[Path, ...]) -> list[Path]:
    repo = repo_root()
    return sorted(p for d in dirs for p in (repo / d).rglob("*.py"))


@lru_cache(maxsize=1)
def _tests_index() -> tuple[dict[str, ast.Module], dict[Symbol, ast.AST], dict[str, dict[str, Symbol]]]:
    """Parse every module under ``tests/`` once: trees, defs, and import bindings."""
    repo = repo_root()
    trees: dict[str, ast.Module] = {}
    by_dotted: dict[str, str] = {}
    for path in sorted((repo / "tests").rglob("*.py")):
        rel = str(path.relative_to(repo))
        trees[rel] = parse_module(path)
        by_dotted[".".join(path.relative_to(repo).with_suffix("").parts)] = rel

    defs: dict[Symbol, ast.AST] = {}
    for rel, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                defs[(rel, node.name)] = node

    bindings: dict[str, dict[str, Symbol]] = {}
    for rel, tree in trees.items():
        local: dict[str, Symbol] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                target = by_dotted.get(node.module) or by_dotted.get(f"{node.module}.__init__")
                if target is not None:
                    for alias in node.names:
                        local[alias.asname or alias.name] = (target, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    target = by_dotted.get(alias.name)
                    if target is not None:
                        local[alias.asname or alias.name.split(".")[-1]] = (target, "<module>")
        bindings[rel] = local
    return trees, defs, bindings


def _call_targets(node: ast.AST) -> set[tuple[str, str | None]]:
    """``(callee name, module alias)`` for every call expression inside *node*."""
    targets: set[tuple[str, str | None]] = set()
    for call in iter_call_expressions(node):
        func = call.func
        if isinstance(func, ast.Name):
            targets.add((func.id, None))
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            targets.add((func.attr, func.value.id))
    return targets


def _resolve(module: str, name: str, alias: str | None) -> Symbol | None:
    """Resolve a callee to a def under ``tests/``, or None when it leaves the tree."""
    _, defs, bindings = _tests_index()
    if alias is not None:
        bound = bindings[module].get(alias)
        if bound is not None and bound[1] == "<module>" and (bound[0], name) in defs:
            return (bound[0], name)
        return None
    if (module, name) in defs:
        return (module, name)
    bound = bindings[module].get(name)
    if bound is not None and bound in defs:
        return bound
    return None


def _env_route_callbacks() -> list[Symbol]:
    """The ``ENV_ROUTES`` row callbacks, reached through the dataclass field, not by name."""
    _, defs, _ = _tests_index()
    conftest = str(_CONFTEST)
    roots: list[Symbol] = []
    for node in ast.walk(_tests_index()[0][conftest]):
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "ENV_ROUTES" for t in targets) or node.value is None:
            continue
        for referenced in ast.walk(node.value):
            if isinstance(referenced, ast.Name) and (conftest, referenced.id) in defs:
                roots.append((conftest, referenced.id))
    return roots


@lru_cache(maxsize=1)
def _graded_unit() -> frozenset[Symbol]:
    """AST reachability from ``_harness_env`` across ``tests/`` (classes included).

    A class instantiation traverses the class's methods: the harness envs
    ``_harness_env`` builds are the largest part of the closure, and pass-6
    verified that whole tree is marker-free.
    """
    _, defs, _ = _tests_index()
    pending: list[Symbol] = [(str(_CONFTEST), "_harness_env"), *_env_route_callbacks()]
    seen: set[Symbol] = set()
    while pending:
        current = pending.pop()
        if current in seen or current not in defs:
            continue
        seen.add(current)
        node = defs[current]
        members: list[ast.AST] = [node]
        if isinstance(node, ast.ClassDef):
            members = [m for m in node.body if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)]
        for member in members:
            for name, alias in _call_targets(member):
                resolved = _resolve(current[0], name, alias)
                if resolved is not None:
                    pending.append(resolved)
    return frozenset(seen)


# --- the two capability detectors --------------------------------------------


def find_marker_derivation_violations(tree: ast.AST) -> list[int]:
    """Lines that DERIVE a marker set from a pytest node."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _MARKER_DERIVATION_ATTRS:
            lines.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id in _MARKER_DERIVATION_ATTRS:
            lines.append(node.lineno)
    return lines


def _mentions_marker_collection(node: ast.AST) -> bool:
    for inner in ast.walk(node):
        if isinstance(inner, ast.Name) and _MARKERISH.search(inner.id):
            return True
        if isinstance(inner, ast.Attribute) and _MARKERISH.search(inner.attr):
            return True
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str) and _TAG_LITERAL.match(inner.value):
            return True
    return False


def find_routing_predicate_violations(tree: ast.AST) -> list[int]:
    """Lines that TEST a marker set — membership, intersection, or tag-prefix."""
    lines: list[int] = []
    candidate: ast.expr
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and any(isinstance(op, ast.In | ast.NotIn) for op in node.ops):
            candidate = node
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitAnd):
            candidate = node
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "startswith":
            candidate = node
        else:
            continue
        if _mentions_marker_collection(candidate):
            lines.append(candidate.lineno)
    return lines


# --- detector self-tests ------------------------------------------------------


def test_marker_derivation_detector_catches_known_bad() -> None:
    assert_detector_catches_ast_snippets(
        find_marker_derivation_violations,
        snippets={
            "set comprehension over iter_markers": "names = {m.name for m in request.node.iter_markers()}",
            "own_markers": "names = [m.name for m in item.own_markers]",
        },
    )


def test_routing_predicate_detector_catches_known_bad() -> None:
    assert_detector_catches_ast_snippets(
        find_routing_predicate_violations,
        snippets={
            "membership": 'if "account" in marker_names:\n    pass',
            "intersection with a set literal": 'hit = marker_names & {"list-after-sync", "concept-id"}',
            "intersection with a named set": "hit = marker_names & _UC003_MANUAL_APPROVAL",
            "tag-prefix scan": 'hit = any(t.startswith("T-UC-002-ext-") for t in names)',
            "tag literal on the left": 'if "T-UC-018-ext-c" in names:\n    pass',
        },
    )


def test_routing_predicate_detector_ignores_the_post_move_dispatch() -> None:
    """The shape ``_harness_env`` must be free to keep: dispatch on the resolver's result."""
    source = (
        "route = resolve_env_route(names, ENV_ROUTES)\nif route is None or route.xfail_reason is not None:\n    pass"
    )
    assert find_routing_predicate_violations(ast.parse(source)) == []


# --- the MOVE guard -----------------------------------------------------------


def test_route_resolver_is_defined_exactly_once_in_the_stdlib_only_owner() -> None:
    """One routing decision, in the module both sides can import."""
    found = {
        str(path.relative_to(repo_root()))
        for path in _repo_py_files(_CONTRACT_SCAN_DIRS)
        for node in ast.walk(parse_module(path))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == _ROUTE_RESOLVER
    }
    assert found == {str(_OWNER)}, (
        f"{_ROUTE_RESOLVER}() must be defined exactly once, in {_OWNER}. Found: {sorted(found) or 'nowhere'}.\n"
        f"{_FIX_HINT}"
    )


def test_harness_env_makes_exactly_one_routing_call() -> None:
    """The grader's oracle: one call, dispatched on. Otherwise §5's agreement test
    can only be written as a THIRD transcription of the predicates."""
    _, defs, _ = _tests_index()
    calls = [
        (module, name)
        for module, name in sorted(_graded_unit())
        for _ in iter_call_expressions(defs[(module, name)], name=_ROUTE_RESOLVER)
    ]
    assert len(calls) == 1, (
        f"expected exactly ONE {_ROUTE_RESOLVER}() call reachable from _harness_env, found {len(calls)}: "
        f"{calls}.\n{_FIX_HINT}"
    )


def test_graded_unit_derives_markers_only_in_the_pinned_accessor() -> None:
    """G1: the accessor is exempt from THIS ban only — and nothing else is."""
    _, defs, _ = _tests_index()
    found = {
        (module, name, line)
        for module, name in _graded_unit()
        for line in find_marker_derivation_violations(defs[(module, name)])
    }
    allowed = {
        (module, name, line)
        for module, name, line in found
        if _DERIVATION_ACCESSOR_MODULE is not None
        and module == str(_DERIVATION_ACCESSOR_MODULE)
        and name == _DERIVATION_ACCESSOR
    }
    assert_violations_match_allowlist(
        found,
        allowed,
        fix_hint=(
            f"Only {_DERIVATION_ACCESSOR}() may derive a marker set, and it must be the accessor pinned in "
            f"_DERIVATION_ACCESSOR_MODULE. {_FIX_HINT}"
        ),
    )


def test_graded_unit_contains_no_routing_predicates() -> None:
    """G1: ``resolve_env_route`` is exempt from THIS ban only — including the accessor."""
    _, defs, _ = _tests_index()
    found = {
        (module, name, line)
        for module, name in _graded_unit()
        if name != _ROUTE_RESOLVER
        for line in find_routing_predicate_violations(defs[(module, name)])
    }
    assert_violations_match_allowlist(
        found,
        set(),
        fix_hint=(
            f"Every marker-set routing predicate reachable from _harness_env must move into {_ROUTE_RESOLVER}() "
            f"in {_OWNER}, which the join calls too. {_FIX_HINT}"
        ),
    )


def test_absorbed_helpers_are_gone_not_preserved_as_callees() -> None:
    """Absorbed into ``resolve_env_route``, not moved one call-frame over."""
    _, defs, _ = _tests_index()
    found = {(module, name) for module, name in defs if name in _ABSORBED_HELPERS}
    assert_violations_match_allowlist(
        found,
        set(),
        fix_hint=(
            "These four helpers hold the routing predicates the join cannot see. Absorb them into "
            f"{_ROUTE_RESOLVER}(); preserving them as callees leaves two implementations standing. {_FIX_HINT}"
        ),
    )


def test_derivation_accessor_is_defined_once_and_called_by_both_sites() -> None:
    """4b: one canonical derivation. Two derivations feeding one resolver reproduce
    this lane's disease one layer out, where the agreement test cannot see it."""
    _, defs, _ = _tests_index()
    definitions = {module for module, name in defs if name == _DERIVATION_ACCESSOR}
    expected = {str(_DERIVATION_ACCESSOR_MODULE)} if _DERIVATION_ACCESSOR_MODULE is not None else set()
    assert definitions == expected, (
        f"{_DERIVATION_ACCESSOR}() must be defined exactly once, in the module pinned as "
        f"_DERIVATION_ACCESSOR_MODULE. Found: {sorted(definitions) or 'nowhere'}."
    )

    callers = {
        (str(module), name)
        for module, name in ((_CONFTEST, "_harness_env"), (_PLUGIN, "pytest_bdd_before_scenario"))
        if any(iter_call_expressions(defs[(str(module), name)], name=_DERIVATION_ACCESSOR))
    }
    assert callers == {(str(_CONFTEST), "_harness_env"), (str(_PLUGIN), "pytest_bdd_before_scenario")}, (
        f"Both call sites must derive their marker set through {_DERIVATION_ACCESSOR}(). "
        f"Callers found: {sorted(callers) or 'none'}."
    )


# --- the copied constants -----------------------------------------------------


def _docstring_nodes(tree: ast.Module) -> set[int]:
    return {
        id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }


def _modules_holding(literals: frozenset[str]) -> set[str]:
    repo = repo_root()
    holders: set[str] = set()
    for path in _repo_py_files(_CONTRACT_SCAN_DIRS):
        tree = parse_module(path)
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in literals
                and id(node) not in docstrings
            ):
                holders.add(str(path.relative_to(repo)))
    return holders


@pytest.mark.parametrize(("label", "literals"), _SHARED_LITERALS, ids=[label for label, _ in _SHARED_LITERALS])
def test_shared_constant_has_exactly_one_definition_site(label: str, literals: frozenset[str]) -> None:
    holders = _modules_holding(literals)
    assert holders == {str(_OWNER)}, (
        f"the {label} literal(s) {sorted(literals)} must live only in {_OWNER}; every other module imports it. "
        f"Found in: {sorted(holders)}.\n{_FIX_HINT}"
    )


# --- the ledger grammar -------------------------------------------------------


def _loader_def(module: Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(parse_module(repo_root() / module)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{module}::{name} not found — the pinned ledger loader moved or was renamed")


def test_ledger_line_parser_is_defined_exactly_once_in_the_owner() -> None:
    found = {
        str(path.relative_to(repo_root()))
        for path in _repo_py_files(_CONTRACT_SCAN_DIRS)
        for node in ast.walk(parse_module(path))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == _LEDGER_PARSER
    }
    assert found == {str(_OWNER)}, (
        f"{_LEDGER_PARSER}() must be defined exactly once, in {_OWNER}. Found: {sorted(found) or 'nowhere'}.\n"
        f"{_FIX_HINT}"
    )


@pytest.mark.parametrize(("module", "name"), _LEDGER_LOADERS, ids=[f"{m}::{n}" for m, n in _LEDGER_LOADERS])
def test_ledger_loader_injects_its_own_grammar(module: Path, name: str) -> None:
    """One parse function, grammar supplied by the caller — the two ledgers
    legitimately produce different types over different files."""
    node = _loader_def(module, name)
    calls = list(iter_call_expressions(node, name=_LEDGER_PARSER))
    assert len(calls) == 1, (
        f"{module}::{name} must reach {_LEDGER_PARSER}() exactly once, found {len(calls)}.\n{_FIX_HINT}"
    )
    keywords = {kw.arg for kw in calls[0].keywords}
    assert "grammar" in keywords, (
        f"{module}::{name} must INJECT its grammar into {_LEDGER_PARSER}(grammar=...); "
        f"keywords passed: {sorted(k for k in keywords if k)}."
    )


@pytest.mark.parametrize(("module", "name"), _LEDGER_LOADERS, ids=[f"{m}::{n}" for m, n in _LEDGER_LOADERS])
def test_ledger_loader_does_not_reimplement_the_line_scan(module: Path, name: str) -> None:
    source = ast.unparse(_loader_def(module, name))
    assert "splitlines()" not in source, (
        f"{module}::{name} still scans the file itself — the line scan belongs to {_LEDGER_PARSER}() "
        f"in {_OWNER}.\n{_FIX_HINT}"
    )


def test_bracket_grammar_rejection_is_loud_not_a_silent_drop(tmp_path: Path) -> None:
    """LOUD-CASE SCOPE: scoped to the BRACKET grammar, deliberately.

    ``tests/helpers/ledger.py``'s plain grammar is TOTAL — it strips and filters
    comments, so no line can fail to parse and it has no failure mode to
    surface. The bracket grammar does: ``scripts/audit/ledger.load`` currently
    drops a grammar-drifted line on the floor, which is how a ledger entry stops
    grading anything while the file still looks maintained. LOUD means the
    caller's INJECTED grammar rejected this line AND the rejection surfaces.
    """
    from scripts.audit.ledger import load

    good = "tests/storyboard/test_storyboard_conformance.py::test_storyboard_check[mcp::core::webhook_emission::step-1]"
    ledger = tmp_path / "known_failures.txt"
    ledger.write_text(
        f"# a comment\n\n{good}\ntest_storyboard_check[mcp::core::drifted-short-form]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as excinfo:
        load(ledger)

    message = str(excinfo.value)
    assert "drifted-short-form" in message, f"the rejection must name the offending line; got: {message}"
    assert "4" in message, f"the rejection must name the offending line NUMBER (4); got: {message}"

    # The loud change must not cost the loader its real behavior: comments and
    # blank lines are still skipped, and a well-formed line still round-trips.
    ledger.write_text(f"# a comment\n\n{good}\n", encoding="utf-8")
    parsed = load(ledger)
    assert [entry.format() for entry in parsed] == ["mcp::core::webhook_emission::step-1"]


def test_silent_drop_is_not_ratified_in_the_loader_docstring() -> None:
    """The docstring at scripts/audit/ledger.py:97-101 does not merely describe the
    drop — it RATIFIES it as intended. Removing the branch and leaving the
    rationale leaves the next author a licence to restore it."""
    docstring = ast.get_docstring(_loader_def(Path("scripts/audit/ledger.py"), "load")) or ""
    assert "silent" not in docstring.lower(), (
        f"scripts/audit/ledger.load's docstring still ratifies silently dropping unparsable lines:\n{docstring}"
    )
