"""Guard: the harness has ONE dispatch mechanism, and it lives on the base.

The shared client core owns MCP/A2A dispatch. Environment hooks address the tool
and select its local reader; the base delivery methods retain the result and wire.

This guard pins that shape:

1. ``call_mcp`` / ``call_a2a`` live once on ``BaseTestEnv`` and return a parsed
   payload or raise the exact captured exception.
2. ``deliver_mcp`` / ``deliver_a2a`` also live once on the base, returning
   ``TransportResult``. The override census is empty and may not grow.
3. ``_last_wire_response`` does not exist. Wire evidence travels on the result.

MEMBERSHIP IS AST-DERIVED, NOT A DIRECTORY GLOB (change-set §5, finding 4).
A ``tests/harness/*.py`` glob is evadable and the evasion path is ALREADY
occupied by env subclasses defined outside it — a dozen of them live under
``tests/integration/`` (the pinned exemplar is ``_RefusalDialectEnv`` in
``tests/integration/test_harness_rest_refusal.py``, which reaches an env root
only TRANSITIVELY, via the harness class ``MediaBuyCreateListEnv``) — plus one
nested inside a test function in ``tests/harness/test_harness_base.py``. This
guard resolves class ancestry by name, transitively, across the whole ``tests/``
tree, so an env is caught wherever it is written. It is also why the scan is
AST and not text: a ``def call_mcp`` inside a STRING literal, which a grep census
would miscount as a real definition, is not a definition.

"""

from __future__ import annotations

import ast

from tests.unit._architecture_helpers import assert_violations_match_allowlist, rel, repo_root

_TESTS_DIR = repo_root() / "tests"

# The module that owns the ONE definition of each dispatch method. Overrides
# are counted everywhere else.
_BASE_MODULE = "tests/harness/_base.py"

# Roots of the harness env hierarchy (declared in _BASE_MODULE).
_ENV_ROOTS = frozenset({"BaseTestEnv", "IntegrationEnv", "BareIntegrationEnv"})

# The old quartet's transport entry points. After the single-dispatch change these exist ONLY on
# BaseTestEnv, as adapters for the captured result.
_LEGACY_DISPATCH_METHODS = frozenset({"call_mcp", "call_a2a"})

# The delivery methods called by transport dispatchers.
_DELIVER_METHODS = frozenset({"deliver_mcp", "deliver_a2a"})

# The deleted per-env wire stash (change-set B2 / R3).
_WIRE_STASH_ATTR = "_last_wire_response"

# The pinned out-of-harness env exemplar for the anti-glob meta-test below.
# ``_RefusalDialectEnv`` (tests/integration/test_harness_rest_refusal.py) derives
# from the harness class ``MediaBuyCreateListEnv``, so it reaches an env root only
# TRANSITIVELY and from a module a tests/harness/*.py glob never opens.
_EXEMPLAR_OUT_OF_HARNESS_ENV = "_RefusalDialectEnv"

# Shrink-only allowlist of JUSTIFIED deliver_* overrides, as
# ``(repo_relative_path, class_name, method_name)``. Seeded once by the single-dispatch
# implement atom from its B1 JUSTIFIED-OVERRIDE enumeration (each entry
# carrying a one-line reason at the source site); it only shrinks afterwards.
# An env that can delegate to the base MUST NOT appear here.
_KNOWN_DELIVER_OVERRIDES: set[tuple[str, str, str]] = set()


# --------------------------------------------------------------------------
# AST-derived membership: every harness-env subclass, wherever it is defined
# --------------------------------------------------------------------------


def _iter_test_modules() -> list[tuple[str, ast.Module]]:
    """``(repo_relative_path, tree)`` for every parseable ``.py`` under tests/."""
    modules: list[tuple[str, ast.Module]] = []
    for py_file in sorted(_TESTS_DIR.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:  # pragma: no cover - a syntax error fails elsewhere
            continue
        modules.append((rel(py_file, repo_root()), tree))
    return modules


def _base_names(cls: ast.ClassDef) -> set[str]:
    """Base-class NAMES of *cls* — ``Foo`` and ``mod.Foo`` both yield ``Foo``."""
    names: set[str] = set()
    for base in cls.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def _class_index(modules: list[tuple[str, ast.Module]]) -> dict[str, set[str]]:
    """``class name -> union of its base-class names`` across the whole tests tree.

    Nested classes (defined inside a function body — the exact shape both
    out-of-harness env definitions take) are included: ``ast.walk`` descends
    into function bodies, so a ``class _TestEnv(BaseTestEnv)`` written inside a
    test function is indexed like a module-level one.
    """
    index: dict[str, set[str]] = {}
    for _path, tree in modules:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                index.setdefault(node.name, set()).update(_base_names(node))
    return index


def _is_env_class(name: str, index: dict[str, set[str]]) -> bool:
    """True when *name* transitively derives from a harness env root."""
    if name in _ENV_ROOTS:
        return True
    seen: set[str] = set()
    frontier = [name]
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        for base in index.get(current, set()):
            if base in _ENV_ROOTS:
                return True
            frontier.append(base)
    return False


def env_class_names() -> set[str]:
    """Every harness-env class name defined anywhere under ``tests/``."""
    modules = _iter_test_modules()
    index = _class_index(modules)
    return {name for name in index if _is_env_class(name, index)}


def _defining_module_paths(class_name: str) -> set[str]:
    """Repo-relative paths of every module under ``tests/`` defining *class_name*.

    ``ast.walk`` descends into function bodies, so a class nested inside a test
    function counts as defined by its module — the same membership rule
    ``_class_index`` uses.
    """
    paths: set[str] = set()
    for path, tree in _iter_test_modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                paths.add(path)
    return paths


def _scan_method_defs(method_names: frozenset[str]) -> set[tuple[str, str, str]]:
    """``(path, class, method)`` for each *method_names* def on an env subclass.

    ``_BASE_MODULE`` is excluded: it holds the ONE canonical definition, which
    is the thing every other site must delegate to rather than re-implement.
    """
    modules = _iter_test_modules()
    index = _class_index(modules)
    found: set[tuple[str, str, str]] = set()
    for path, tree in modules:
        if path == _BASE_MODULE:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not _is_env_class(node.name, index):
                continue
            for stmt in node.body:
                if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef) and stmt.name in method_names:
                    found.add((path, node.name, stmt.name))
    return found


def _scan_wire_stash_references() -> set[tuple[str, int]]:
    """``(path, lineno)`` for every attribute access named ``_last_wire_response``.

    Reads AND writes: R3 verified the tree-wide reader set is fully
    dispositioned by the change-set, so afterwards neither may remain — an
    orphaned reader is how the attribute grows a second writer back.
    """
    found: set[tuple[str, int]] = set()
    for path, tree in _iter_test_modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == _WIRE_STASH_ATTR:
                found.add((path, node.lineno))
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Attribute):
                if node.target.attr == _WIRE_STASH_ATTR:
                    found.add((path, node.lineno))
    return found


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------


def test_no_env_subclass_overrides_the_legacy_dispatch_methods():
    """``call_mcp``/``call_a2a`` live ONLY on ``BaseTestEnv``.

    Environment differences belong in the core's addressing and reader hooks.
    """
    violations = _scan_method_defs(_LEGACY_DISPATCH_METHODS)
    assert violations == set(), (
        f"{len(violations)} env subclass(es) still define their own call_mcp/call_a2a instead of "
        f"delegating to BaseTestEnv: {sorted(violations)}. Declare MCP_TOOL/A2A_SKILL and use "
        f"address_tool/response_parser for request routing and local response models."
    )


def test_deliver_overrides_match_allowlist():
    """The R2 census: ``def deliver_mcp``/``def deliver_a2a`` on env subclasses.

    Shrink-only — a converted env must be REMOVED from the allowlist (the
    stale half of this assertion), and a new hand-rolled override is a new
    violation, never an allowlist addition.
    """
    assert_violations_match_allowlist(
        _scan_method_defs(_DELIVER_METHODS),
        _KNOWN_DELIVER_OVERRIDES,
        fix_hint=(
            "Declare MCP_TOOL/A2A_SKILL (and, if the response needs a non-default parser, the "
            "response_parser(self, tool) instance hook) so BaseTestEnv.deliver_* dispatches through "
            "the one AdCPTestClient core instead of a per-env copy."
        ),
    )


def test_base_env_owns_the_one_dispatch_pair():
    """Anti-vacuity pin for the R2 census: the counted symbol must EXIST.

    ``BaseTestEnv`` defines ``deliver_mcp``/``deliver_a2a`` (the method the
    dispatchers call and envs override) and defines ``call_mcp``/``call_a2a``
    exactly once, delegating to them. Without this, ``_scan_method_defs`` would
    survey a symbol nothing in the tree defines and
    ``test_deliver_overrides_match_allowlist`` would pass against an empty
    universe.
    """
    tree = ast.parse((repo_root() / _BASE_MODULE).read_text(encoding="utf-8"))
    base = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "BaseTestEnv"),
        None,
    )
    assert base is not None, f"BaseTestEnv disappeared from {_BASE_MODULE}"
    defined = {stmt.name for stmt in base.body if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)}
    assert _DELIVER_METHODS <= defined, (
        f"BaseTestEnv must define {sorted(_DELIVER_METHODS)} — the ONE dispatch pair the transport "
        f"dispatchers call. Missing: {sorted(_DELIVER_METHODS - defined)} "
        f"See this module's docstring for why the pair is singular."
    )
    assert _LEGACY_DISPATCH_METHODS <= defined, (
        f"BaseTestEnv must keep defining {sorted(_LEGACY_DISPATCH_METHODS)} — the typed-payload return "
        f"34 out-of-harness call sites depend on. Missing: {sorted(_LEGACY_DISPATCH_METHODS - defined)}."
    )

    # Both adapters dispatch once and raise the original captured exception.
    for suffix in ("mcp", "a2a"):
        legacy = f"call_{suffix}"
        source = f"""result = self.deliver_{suffix}(**kwargs)
if result.error is not None:
    raise result.error
return result.payload"""
        func = next(s for s in base.body if isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef) and s.name == legacy)
        body = [s for s in func.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        actual = ast.dump(ast.Module(body=body, type_ignores=[]))
        expected = ast.dump(ast.parse(source))
        assert actual == expected, (
            f"BaseTestEnv.{legacy} must deliver once and adapt only its captured result; "
            f"expected {source!r}, got {actual[:300]}"
        )


def test_no_per_env_wire_stash_attribute():
    """``_last_wire_response`` is deleted — the wire rides the return value.

    Change-set B2 + R3: the attribute's readers (``dispatchers.py``,
    ``client.py``'s two success unwrappers, ``test_client.py``) are all
    dispositioned onto ``DeliverResult.wire_response``, so no reference may
    remain. This is the B2 reversion grader: re-introducing a write to a
    per-env wire attribute fails here because the attribute no longer exists.
    """
    references = _scan_wire_stash_references()
    assert references == set(), (
        f"{len(references)} reference(s) to the deleted per-env wire stash "
        f"`{_WIRE_STASH_ATTR}` remain: {sorted(references)}. The success-path wire travels on "
        f"DeliverResult.wire_response (the dispatchers read the RETURN VALUE, never another object's "
        f"private attribute)."
    )


# --------------------------------------------------------------------------
# Meta-tests: the scanner itself, pinned independently of the tree's state
# --------------------------------------------------------------------------

_SNIPPET_ENV_SUBCLASS = """
class ProductEnv(IntegrationEnv):
    def deliver_mcp(self, **kwargs):
        return DeliverResult(payload=None, wire_response=None)
"""

_SNIPPET_NON_ENV = """
class NotAnEnv:
    def deliver_mcp(self, **kwargs):
        return None
"""


def test_meta_membership_resolves_transitively_through_a_mixin():
    """A class reaching an env root through a MIXIN base is still an env."""
    index = {
        "DeliveryPollEnv": {"DeliveryPollMixin", "IntegrationEnv"},
        "DeliveryPollMixin": set(),
        "SubEnv": {"DeliveryPollEnv"},
        "Unrelated": {"object"},
    }
    assert _is_env_class("DeliveryPollEnv", index) is True
    assert _is_env_class("SubEnv", index) is True
    assert _is_env_class("Unrelated", index) is False


def test_meta_membership_is_not_a_directory_glob():
    """The env subclasses defined OUTSIDE ``tests/harness/*.py`` are members.

    These are the evasion path the change-set's finding 4 named as already
    occupied — a ``tests/harness/*.py`` glob would miss both, so the guard
    would grade a strict subset of the real population.

    The out-of-harness exemplar is pinned by NAME, not derived, so the assertion
    cannot decay into "whatever the scan happens to find". Its second half
    re-establishes, from the AST, that the exemplar really is defined outside
    ``tests/harness/`` — otherwise the exemplar could silently migrate into the
    harness directory and this test would keep passing while grading nothing.
    """
    names = env_class_names()
    assert _EXEMPLAR_OUT_OF_HARNESS_ENV in names, (
        f"the env subclass {_EXEMPLAR_OUT_OF_HARNESS_ENV} in tests/integration/ must be derived as a "
        "harness env — a directory glob would miss it"
    )
    defining_paths = _defining_module_paths(_EXEMPLAR_OUT_OF_HARNESS_ENV)
    assert defining_paths and not any(p.startswith("tests/harness/") for p in defining_paths), (
        f"{_EXEMPLAR_OUT_OF_HARNESS_ENV} is no longer an OUT-OF-HARNESS exemplar (defined in "
        f"{sorted(defining_paths)}), so it no longer proves membership is derivation-based rather than "
        "a tests/harness/*.py glob. Re-point this test at another env subclass defined outside "
        "tests/harness/ — do not delete the assertion."
    )
    assert "_TestEnv" in names, (
        "the env subclass nested inside a test function in tests/harness/test_harness_base.py "
        "must be derived as a harness env — a directory glob would miss it"
    )


def test_meta_scanner_flags_an_override_on_an_env_and_ignores_a_non_env():
    """Positive + negative: the method scan keys on env membership, not name."""
    index = _class_index([("x.py", ast.parse(_SNIPPET_ENV_SUBCLASS)), ("y.py", ast.parse(_SNIPPET_NON_ENV))])
    assert _is_env_class("ProductEnv", index) is True
    assert _is_env_class("NotAnEnv", index) is False
