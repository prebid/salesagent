"""Guard: the harness has ONE dispatch mechanism, and it lives on the base.

The invariant: ``AdCPTestClient`` is the
implementation ``call_via``/``call_mcp``/``call_a2a`` delegate to, **not** a
peer beside them. Today the harness carries two live dispatch mechanisms — the
generic ``AdCPTestClient`` (``tests/harness/client.py``) and the hand-written
``call_mcp``/``call_a2a`` quartet re-implemented on every env class — and the
old quartet is still the default authoring path, so each new env adds a 35th,
36th... copy.

This guard pins the finished shape:

1. ``call_mcp`` / ``call_a2a`` are defined ONCE, on ``BaseTestEnv``
   (``tests/harness/_base.py``), as ``deliver_*(...).payload``, and are NEVER
   overridden by a subclass. A new env written on the old pattern fails CI.
2. Transport overrides that genuinely need to stay are ``deliver_mcp`` /
   ``deliver_a2a`` returning a ``DeliverResult`` — counted against a
   shrink-only allowlist. Per the reviewed change-set's binding input R2, the
   census MUST count ``def deliver_mcp``/``def deliver_a2a``: a
   ``call_mcp``/``call_a2a`` census reads ZERO after the rename and would
   grade nothing, passing vacuously.
3. ``_last_wire_response`` — the per-env private wire-stash attribute the
   dispatchers reach across object boundaries to read — does not exist. The
   wire travels on the RETURN VALUE (``DeliverResult.wire_response``).
   Deleting the attribute is what makes a second writer structurally
   impossible (change-set B2 + binding input R3).

MEMBERSHIP IS AST-DERIVED, NOT A DIRECTORY GLOB (change-set §5, finding 4).
A ``tests/harness/*.py`` glob is evadable and the evasion path is ALREADY
occupied by two env subclasses defined outside it — one in
``tests/integration/test_request_validation_suggestion_parity.py`` and one
nested inside a test function in ``tests/harness/test_harness_base.py``. This
guard resolves class ancestry by name, transitively, across the whole ``tests/``
tree, so an env is caught wherever it is written. It is also why the scan is
AST and not text: ``tests/unit/test_architecture_harness_mcp_with_error_logging.py``
holds a ``def call_mcp`` inside a meta-test STRING literal, which a grep census
would miscount as a real definition.

"""

from __future__ import annotations

import ast

from tests.unit._architecture_helpers import assert_violations_match_allowlist, repo_root

_TESTS_DIR = repo_root() / "tests"

# The module that owns the ONE definition of each dispatch method. Overrides
# are counted everywhere else.
_BASE_MODULE = "tests/harness/_base.py"

# Roots of the harness env hierarchy (declared in _BASE_MODULE).
_ENV_ROOTS = frozenset({"BaseTestEnv", "IntegrationEnv", "BareIntegrationEnv"})

# The old quartet's transport entry points. After the single-dispatch change these exist ONLY on
# BaseTestEnv, as ``return self.deliver_*(**kwargs).payload``.
_LEGACY_DISPATCH_METHODS = frozenset({"call_mcp", "call_a2a"})

# The new override point: the method the dispatchers call and envs override.
_DELIVER_METHODS = frozenset({"deliver_mcp", "deliver_a2a"})

# The deleted per-env wire stash (change-set B2 / R3).
_WIRE_STASH_ATTR = "_last_wire_response"

# Shrink-only allowlist of JUSTIFIED deliver_* overrides, as
# ``(repo_relative_path, class_name, method_name)``. Seeded once by the single-dispatch
# implement atom from its B1 JUSTIFIED-OVERRIDE enumeration (each entry
# carrying a one-line reason at the source site); it only shrinks afterwards.
# An env that can delegate to the base MUST NOT appear here.
_KNOWN_DELIVER_OVERRIDES: set[tuple[str, str, str]] = {
    # Seeded ONCE from the B1 JUSTIFIED-OVERRIDE enumeration. Every entry
    # is an env that CANNOT delegate to the base, with the reason. Shrink-only:
    # an env that can delegate must not be added here.
    #
    # Selects BOTH tool and parser from request CONTENT, so it can declare no
    # single MCP_TOOL/A2A_SKILL.
    # Selects the transport target from request CONTENT (_is_list_request), so
    # it can declare no single MCP_TOOL/A2A_SKILL.
    ("tests/harness/media_buy_create_list.py", "MediaBuyCreateListEnv", "deliver_mcp"),
    ("tests/harness/media_buy_create_list.py", "MediaBuyCreateListEnv", "deliver_a2a"),
    ("tests/harness/media_buy_dual.py", "MediaBuyDualEnv", "deliver_mcp"),
    ("tests/harness/media_buy_dual.py", "MediaBuyDualEnv", "deliver_a2a"),
    # Reshapes kwargs (_flatten_request) and parses the success|error union via
    # parse_rest_response, which needs the media_buy_id discriminator.
    ("tests/harness/media_buy_create.py", "MediaBuyCreateEnv", "deliver_mcp"),
    ("tests/harness/media_buy_create.py", "MediaBuyCreateEnv", "deliver_a2a"),
    # Supplies a required kwargs default (creatives=[]) before dispatch.
    ("tests/harness/creative_sync.py", "CreativeSyncEnv", "deliver_mcp"),
    # Commits factory data, defaults creatives=[], and JSON-normalizes kwargs
    # through build_rest_body before dispatch, so it cannot delegate to the base.
    # (The previous reason — "deliberate raw-wrapper bypass ... LANE C owns
    # replacing the bypass" — is stale: it WAS replaced in dc280c5ee, and
    # deliver_a2a now calls the real _run_a2a_handler. The entry stays because of
    # the setup above, not because of a bypass that no longer exists; an allowlist
    # whose recorded reasons drift false cannot be audited.)
    ("tests/harness/creative_sync.py", "CreativeSyncEnv", "deliver_a2a"),
    # Uses the legacy _run_mcp_wrapper mechanism (mock Context -> async wrapper),
    # not _run_mcp_client, and observes no structured_content wire.
    ("tests/harness/media_buy_list.py", "MediaBuyListEnv", "deliver_mcp"),
    # FIXME(#1928): get_media_buys' wire omits the pinned-required confirmed_at +
    # revision on every media_buys item, so the core's pinned parse fails. This
    # entry is removed — not re-justified — when #1928 lands; the override exists
    # only to keep the gap attributable instead of hidden by loosening the core.
    ("tests/harness/media_buy_list.py", "MediaBuyListEnv", "deliver_a2a"),
    # FIXME(#2012): by_package entries omit the pinned-required
    # pricing_model/rate/currency, so the core's pinned parse fails on every
    # delivery response. Both entries are removed when #2012 lands.
    ("tests/harness/delivery_poll.py", "DeliveryPollEnv", "deliver_mcp"),
    ("tests/harness/delivery_poll.py", "DeliveryPollEnv", "deliver_a2a"),
    # Their real wire does not satisfy the tool's PINNED response model, which
    # the client core parses into — so joining the core would turn a live
    # conformance gap into a dispatch error. Each is a schema gap graded
    # elsewhere (list_creative_formats assets). TaskManagementEnv WAS here for
    # the list_tasks query_summary/pagination gap; that gap is now fixed in
    # production, so it delegates and its entry is gone — the allowlist shrank.
    ("tests/harness/creative_formats.py", "CreativeFormatsEnv", "deliver_mcp"),
    ("tests/harness/creative_formats.py", "CreativeFormatsEnv", "deliver_a2a"),
    # Selects get_task vs complete_task from kwargs (``tool=``), so it cannot
    # declare a single MCP_TOOL / A2A_SKILL for base client-core delegation.
    ("tests/harness/task_management.py", "TaskEnv", "deliver_mcp"),
    ("tests/harness/task_management.py", "TaskEnv", "deliver_a2a"),
    # Test doubles, not real envs: they exist to prove the dispatch contract.
    ("tests/harness/test_harness_base.py", "_TestEnv", "deliver_mcp"),
    # Contrasts CreativeSyncEnv's bypass by dispatching sync_creatives through
    # the REAL A2A handler — the divergence IS the point of that test.
    ("tests/integration/test_request_validation_suggestion_parity.py", "_RealA2AWireCreativeSyncEnv", "deliver_a2a"),
}


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
        modules.append((str(py_file.relative_to(repo_root())), tree))
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

    Binding input R1 abolished the "non-convertible override that stays as
    ``call_mcp``" category: because the dispatchers now read the wire off the
    RETURN VALUE, every MCP/A2A override must BOTH rename to ``deliver_*`` AND
    return a ``DeliverResult``. So this allowlist is empty by construction —
    a justified override is expressed as a ``deliver_*`` entry in
    ``_KNOWN_DELIVER_OVERRIDES``, never as a surviving ``call_*``.
    """
    violations = _scan_method_defs(_LEGACY_DISPATCH_METHODS)
    assert violations == set(), (
        f"{len(violations)} env subclass(es) still define their own call_mcp/call_a2a instead of "
        f"delegating to BaseTestEnv (which defines each once as `return self.deliver_*(**kwargs).payload`): "
        f"{sorted(violations)}. Rename the override to deliver_mcp/deliver_a2a returning a DeliverResult "
        f"and allowlist it in _KNOWN_DELIVER_OVERRIDES, or delete it and declare MCP_TOOL/A2A_SKILL so the "
        f"base delegates for you."
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
        f"dispatchers call and envs override. Missing: {sorted(_DELIVER_METHODS - defined)} "
        f"See this module's docstring for why the pair is singular."
    )
    assert _LEGACY_DISPATCH_METHODS <= defined, (
        f"BaseTestEnv must keep defining {sorted(_LEGACY_DISPATCH_METHODS)} — the typed-payload return "
        f"34 out-of-harness call sites depend on. Missing: {sorted(_LEGACY_DISPATCH_METHODS - defined)}."
    )

    # ...and each legacy entry point is exactly the one-line delegation, so a
    # second dispatch implementation cannot grow back inside the base itself.
    for legacy, deliver in (("call_mcp", "deliver_mcp"), ("call_a2a", "deliver_a2a")):
        func = next(s for s in base.body if isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef) and s.name == legacy)
        body = [s for s in func.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        assert len(body) == 1 and isinstance(body[0], ast.Return), (
            f"BaseTestEnv.{legacy} must be a single `return self.{deliver}(**kwargs).payload` statement, "
            f"got {len(body)} statement(s) — a second dispatch implementation must not grow back inside the base."
        )
        returned = body[0].value
        assert (
            isinstance(returned, ast.Attribute)
            and returned.attr == "payload"
            and isinstance(returned.value, ast.Call)
            and isinstance(returned.value.func, ast.Attribute)
            and returned.value.func.attr == deliver
        ), f"BaseTestEnv.{legacy} must return `self.{deliver}(...).payload`, got {ast.dump(returned)[:160]}"


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
    """The two env subclasses defined OUTSIDE ``tests/harness/*.py`` are members.

    These are the evasion path the change-set's finding 4 named as already
    occupied — a ``tests/harness/*.py`` glob would miss both, so the guard
    would grade a strict subset of the real population.
    """
    names = env_class_names()
    assert "_RealA2AWireCreativeSyncEnv" in names, (
        "the env subclass in tests/integration/test_request_validation_suggestion_parity.py "
        "must be derived as a harness env — a directory glob would miss it"
    )
    assert "_TestEnv" in names, (
        "the env subclass nested inside a test function in tests/harness/test_harness_base.py "
        "must be derived as a harness env — a directory glob would miss it"
    )


def test_meta_scan_ignores_definitions_inside_string_literals():
    """A ``def call_mcp`` inside a meta-test STRING is not a definition.

    ``tests/unit/test_architecture_harness_mcp_with_error_logging.py`` holds
    exactly that in its ``_BAD_SNIPPET`` constant; a text census would count
    it, this AST census must not.
    """
    offenders = {path for path, _cls, _method in _scan_method_defs(_LEGACY_DISPATCH_METHODS)}
    offenders |= {path for path, _cls, _method in _scan_method_defs(_DELIVER_METHODS)}
    assert "tests/unit/test_architecture_harness_mcp_with_error_logging.py" not in offenders


def test_meta_scanner_flags_an_override_on_an_env_and_ignores_a_non_env():
    """Positive + negative: the method scan keys on env membership, not name."""
    index = _class_index([("x.py", ast.parse(_SNIPPET_ENV_SUBCLASS)), ("y.py", ast.parse(_SNIPPET_NON_ENV))])
    assert _is_env_class("ProductEnv", index) is True
    assert _is_env_class("NotAnEnv", index) is False
