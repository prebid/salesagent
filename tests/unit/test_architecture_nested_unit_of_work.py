"""Guard: a write never escapes the transaction that owns it by opening a second unit.

The sibling guard ``test_architecture_uow_effect_boundary.py`` covers the EFFECT
half of GH #1970 — an outbound call or notification that a rollback cannot undo.
This guard covers the WRITE half, the shape GH #2002 removed: a request
owns a unit of work, and a helper it calls opens its OWN. The rows that helper
writes then commit on their own transaction, so the owning request's rollback
does not reach them (a preview leaves them behind) and — worse — the helper's exit
tears the session out from under its caller.

WHY THE SESSION TEARS. ``get_db_session()`` yields the THREAD-SCOPED session
(``database_session.py``): the same ``Session`` object the outer unit already
holds, with no nesting refcount. The inner unit's ``__exit__`` runs
``session.close(); scoped.remove()``, so the outer unit is left holding a closed
session; on a preview branch the inner rollback discards the OUTER unit's writes too.
That is GH #1644 (site 1 in its comment thread), a live P1 defect, and it is what
this guard measures.

WHAT COUNTS AS TRANSACTIONAL CONTEXT — measured, not assumed. This is the trap
the sibling guard's docstring names, and it is sharper here. The prkv.16 disease
scan (recorded on the bead, "Scan B") found ZERO lexically nested
``with XUoW(...)`` blocks across all 67 unit-opening sites in ``src/``. A detector
phrased as "no ``with UoW`` inside a ``with UoW``" therefore grades NOTHING while
looking thorough. So does the inverse phrasing "a function that RECEIVES a uow
must not open its own unit" — measured over all of ``src/``, it flags zero sites
(clause C below), because the offenders do not receive one; they are simply
CALLED from inside someone else's open unit. Only a call-graph-aware rule catches
them:

  * clause A — a call made while a unit is lexically open, to a function that
    (transitively, import-resolved) opens its own unit
  * clause B — the same, where the transactional context is that the ENCLOSING
    function was handed someone else's session/uow/repository
  * clause C — a function handed a transaction handle that opens its own unit
    inline anyway, without conditioning on that handle being absent

Measured on the tree this guard was written against: clause A finds 1 site, clause
B finds 1 more, clause C finds 0. Clause C is kept as the DEFINITION-SITE pin for
prkv.16's fix (``_create_sync_workflow_steps`` now takes ``uow: CreativeUoW``;
re-adding a ``with WorkflowUoW(...)`` to its body must redden something) and is
declared here as currently grading zero rather than being presented as coverage.

THE SANCTIONED FORM is "join the caller's transaction when given one, own one
otherwise" — ``_assignments.py`` (``if uow is None: uow = stack.enter_context(...)``)
and ``blueprints/creatives.py`` (``AdminCreativeUoW(t) if db_session is None else
nullcontext()``). Two exemptions follow from it, and both are earned, not assumed:
a CALL is clean when the call site hands the callee a transaction handle, and a
conditional open is clean when it is guarded on that handle being ``None``.

RESOLUTION IS DELIBERATELY NARROW, and the gap is pinned in ``KNOWN_UNCOVERED``
rather than left silent: only ``ast.Name`` calls resolved through the module's own
imports or its module-level defs are followed. A method call (``self._helper()``)
or a callable pulled from a registry is NOT resolved, so it is not caught.
Widening resolution by simple name alone was measured first: it returned ten hits
where import-resolved lookup returns two, the eight extras being chains through
unrelated functions that merely share a name — noise that gets a guard weakened
rather than obeyed.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

import pytest

from tests.unit._architecture_helpers import (
    REPO_ROOT,
    assert_violations_match_allowlist,
    collect_visitor_violations,
    import_map,
    module_name_for,
    parse_sources,
    read_source_roots,
    structural_guard_marker_re,
)

#: Directories this guard governs. Pinned by a meta-test so it cannot silently narrow.
SCAN_ROOTS = ("src",)

#: How many call-graph hops to follow from a call site before giving up.
#: Measured: the violation set is identical at 1, 2, 3 and 5 hops, so this buys
#: indirection coverage at zero false-positive cost. Pinned by a meta-test.
MAX_CALL_DEPTH = 3

#: Parameter names that mean "you were handed someone else's open transaction".
_HANDLE_NAMES = frozenset({"uow", "session", "db_session"})
_HANDLE_SUFFIXES = ("_uow", "_session", "_repo")

#: Shapes that DO nest a unit of work and that this guard does NOT catch.
#: Declared so the limit is a stated one. Extend resolution when one shows up.
KNOWN_UNCOVERED = (
    "self._helper()",  # method call — receiver type is not resolved
    "handler()",  # a callable pulled out of a registry/dict
    "getattr(mod, name)()",  # dynamic dispatch
)

#: Sites allowed to keep a nested unit, as (path, enclosing function, callee).
#: Line numbers are deliberately absent so a refactor does not churn the list.
#: SHRINK-ONLY. Each entry is an OPEN defect, not a blessing — when its ticket
#: lands, ``assert_violations_match_allowlist`` reports the entry as stale and
#: fails until it is deleted.
ALLOWLIST: frozenset[tuple[str, str, str]] = frozenset(
    {
        # FIXME(#1644): P1. update_media_buy calls _sync_creatives_impl
        # from inside its own MediaBuyUoW; the inner unit commits the outer
        # update's in-flight writes and then closes the shared scoped session.
        # Fix = the optional-uow shape from _assignments.py:88-91.
        ("src/core/tools/media_buy_update.py", "_update_media_buy_impl", "sync_creatives"),
        # FIXME(#1644): the delivery webhook scheduler holds a
        # get_db_session() session open, hands it to _send_report_for_media_buy,
        # and that helper calls delivery_for_media_buy, which opens its own
        # MediaBuyUoW on the same scoped session — and the caller keeps using the
        # session afterwards (session.scalar/scalars/expunge).
        (
            "src/services/delivery_webhook_scheduler.py",
            "_send_report_for_media_buy",
            "delivery_for_media_buy",
        ),
    }
)

_MARKER = structural_guard_marker_re("nested-unit-of-work")


# ---------------------------------------------------------------------------
# Shape predicates
# ---------------------------------------------------------------------------


def _opens_unit_of_work(expr: ast.expr) -> bool:
    """``CreativeUoW(...)``, or ``stack.enter_context(CreativeUoW(...))``."""
    if isinstance(expr, ast.Call):
        name = ast.unparse(expr.func)
        if name.endswith("UoW"):
            return True
        if name.endswith("enter_context"):
            return any(_opens_unit_of_work(arg) for arg in expr.args)
    return False


def _own_body(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    """*fn*'s statements, excluding functions nested inside it.

    A nested ``def`` has its own qualname in the index, so counting its body
    here would let one unrelated inner helper mark the outer function as a
    unit-opener — the same blinding the sibling guard hit with ``ast.walk``.
    """
    return [s for s in fn.body if not isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef)]


def _walk_own_body(fn: ast.FunctionDef | ast.AsyncFunctionDef):
    for stmt in _own_body(fn):
        yield from ast.walk(stmt)


def _function_opens_unit(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in _walk_own_body(fn):
        if isinstance(node, ast.With | ast.AsyncWith) and any(_opens_unit_of_work(i.context_expr) for i in node.items):
            return True
        if isinstance(node, ast.Call) and _opens_unit_of_work(node):
            return True
    return False


def _transaction_handle_params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    """Parameters through which the caller's open transaction arrives."""
    names: list[str] = []
    for arg in list(fn.args.args) + list(fn.args.kwonlyargs) + list(fn.args.posonlyargs):
        annotation = ast.unparse(arg.annotation) if arg.annotation else ""
        if (
            arg.arg in _HANDLE_NAMES
            or arg.arg.endswith(_HANDLE_SUFFIXES)
            or "UoW" in annotation
            or "Repository" in annotation
        ):
            names.append(arg.arg)
    return tuple(names)


def _hands_over_transaction(call: ast.Call) -> bool:
    """The call site gives the callee its open transaction, so the callee joins it."""
    for kw in call.keywords:
        if kw.arg and (kw.arg in _HANDLE_NAMES or kw.arg.endswith(_HANDLE_SUFFIXES)):
            return True
    for arg in call.args:
        rendered = ast.unparse(arg).lower()
        if rendered.split(".")[0] in _HANDLE_NAMES or rendered.endswith((".session", *_HANDLE_SUFFIXES)):
            return True
    return False


def _guards_on_absent_handle(test: ast.expr, handles: tuple[str, ...]) -> bool:
    """``if uow is None:`` / ``if not db_session:`` — the sanctioned conditional open."""
    rendered = ast.unparse(test)
    return any(f"{h} is None" in rendered or f"not {h}" in rendered for h in handles)


# ---------------------------------------------------------------------------
# Cross-module index
# ---------------------------------------------------------------------------

Violation = tuple[str, str, str]


@dataclass(frozen=True)
class _Index:
    trees: dict[str, ast.Module]
    lines: dict[str, list[str]]
    opens_own: dict[str, bool]
    handles: dict[str, tuple[str, ...]]
    imports: dict[str, dict[str, tuple[str, str]]]
    modules: dict[str, str]
    edges: dict[str, frozenset[str]]


def build_index(sources: dict[str, str]) -> _Index:
    """Index every module ONCE: what each function opens, receives, and calls."""
    trees, lines = parse_sources(sources)

    opens_own: dict[str, bool] = {}
    handles: dict[str, tuple[str, ...]] = {}
    imports: dict[str, dict[str, tuple[str, str]]] = {}
    modules = {module_name_for(relpath): relpath for relpath in trees}
    for relpath, tree in trees.items():
        imports[relpath] = import_map(relpath, tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                qualname = f"{relpath}::{node.name}"
                opens_own[qualname] = _function_opens_unit(node)
                handles[qualname] = _transaction_handle_params(node)

    index = _Index(trees, lines, opens_own, handles, imports, modules, {})
    edges: dict[str, set[str]] = {}
    for relpath, tree in trees.items():
        for caller, call in _iter_calls_with_enclosing_qualname(relpath, tree):
            if _hands_over_transaction(call) or not isinstance(call.func, ast.Name):
                continue
            callee = resolve(index, relpath, call.func.id)
            if callee:
                edges.setdefault(caller, set()).add(callee)
    return _Index(trees, lines, opens_own, handles, imports, modules, {k: frozenset(v) for k, v in edges.items()})


def _iter_calls_with_enclosing_qualname(relpath: str, tree: ast.Module):
    stack: list[str] = []

    def walk(node: ast.AST):
        entered = False
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            stack.append(f"{relpath}::{node.name}")
            entered = True
        if isinstance(node, ast.Call) and stack:
            yield stack[-1], node
        for child in ast.iter_child_nodes(node):
            yield from walk(child)
        if entered:
            stack.pop()

    yield from walk(tree)


def resolve(index: _Index, relpath: str, name: str) -> str | None:
    """A bare call name -> the qualname of the function it refers to, or None.

    Imports first (``from ._sync import _sync_creatives_impl``), then the calling
    module's own defs. One hop through a package ``__init__`` re-export is
    followed, because ``src/core/tools/creatives/__init__.py`` is exactly that.
    """
    imported = index.imports.get(relpath, {}).get(name)
    if imported:
        target, original = imported
        candidate = index.modules.get(target)
        if not candidate:
            return None
        if f"{candidate}::{original}" in index.opens_own:
            return f"{candidate}::{original}"
        if candidate.endswith("__init__.py"):
            reexport = index.imports.get(candidate, {}).get(original)
            if reexport:
                inner = index.modules.get(reexport[0])
                if inner and f"{inner}::{reexport[1]}" in index.opens_own:
                    return f"{inner}::{reexport[1]}"
        return None
    qualname = f"{relpath}::{name}"
    return qualname if qualname in index.opens_own else None


def opening_chain(index: _Index, qualname: str, budget: int, seen: frozenset[str] = frozenset()) -> list[str] | None:
    """The shortest call chain from *qualname* to a function that opens a unit."""
    if qualname in seen:
        return None
    seen = seen | {qualname}
    if index.opens_own.get(qualname):
        return [qualname]
    if budget <= 0:
        return None
    for callee in sorted(index.edges.get(qualname, ())):
        rest = opening_chain(index, callee, budget - 1, seen)
        if rest:
            return [qualname] + rest
    return None


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class _NestedUnitTracker(ast.NodeVisitor):
    """Calls that open a second unit while one is already open, plus clause C."""

    def __init__(self, index: _Index, relpath: str) -> None:
        self._index = index
        self._relpath = relpath
        self._lines = index.lines.get(relpath, [])
        self.violations: list[Violation] = []
        self._uow_depth = 0
        self._handles: tuple[str, ...] = ()
        self._whole_body_is_transactional = False
        self._enclosing_def = "<module>"

    # -- clause B + clause C are decided on entering a function ------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        previous = (self._enclosing_def, self._whole_body_is_transactional, self._handles, self._uow_depth)
        self._enclosing_def = node.name
        handles = self._index.handles.get(f"{self._relpath}::{node.name}", ())
        if handles:
            # Clause B: it was handed someone else's transaction, so its whole
            # body runs with that transaction open.
            self._handles = handles
            self._whole_body_is_transactional = True
        else:
            # Otherwise INHERIT: a nested def declares no parameters of its own
            # but closes over the enclosing ones, so resetting to False here
            # would let "move the call into a nested def" evade the guard.
            self._handles = previous[2]
            self._whole_body_is_transactional = previous[1]
        # Depth is per-function: a `with` block in the caller does not extend
        # lexically into a callee's body.
        self._uow_depth = 0
        self._check_inline_open(node, handles)
        self.generic_visit(node)
        self._enclosing_def, self._whole_body_is_transactional, self._handles, self._uow_depth = previous

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def _check_inline_open(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, handles: tuple[str, ...]) -> None:
        """Clause C: handed a transaction, opens its own anyway."""
        if not handles:
            return

        def scan(node: ast.AST, guarded: bool) -> None:
            if isinstance(node, ast.If):
                inner = guarded or _guards_on_absent_handle(node.test, handles)
                for child in node.body:
                    scan(child, inner)
                for child in node.orelse:
                    scan(child, guarded)
                return
            if isinstance(node, ast.IfExp) and _guards_on_absent_handle(node.test, handles):
                return
            if isinstance(node, ast.With | ast.AsyncWith):
                for item in node.items:
                    if _opens_unit_of_work(item.context_expr) and not guarded and not self._is_marked(node.lineno):
                        self.violations.append((self._relpath, fn.name, f"opens {ast.unparse(item.context_expr)}"))
                # Only the BODY is descended into: the item expression has just
                # been judged, and re-walking it would report the same open
                # twice (once as a with-item, once as the bare call inside it).
                for child in node.body:
                    scan(child, guarded)
                return
            if isinstance(node, ast.Call) and _opens_unit_of_work(node):
                if not guarded and not self._is_marked(node.lineno):
                    self.violations.append((self._relpath, fn.name, f"opens {ast.unparse(node)}"))
                return
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                scan(child, guarded)

        for stmt in _own_body(fn):
            scan(stmt, False)

    # -- clause A ---------------------------------------------------------

    def visit_With(self, node: ast.With) -> None:  # noqa: N802
        # Either the with-item opens a unit (`with CreativeUoW(...)`), or the
        # block's BODY enters one on a stack (`uow = stack.enter_context(...)`)
        # — the latter is a plain statement, not a with-item, so checking only
        # context_expr misses sync_creatives, the module under change.
        opens = any(_opens_unit_of_work(item.context_expr) for item in node.items) or any(
            _opens_unit_of_work(sub) for stmt in node.body for sub in ast.walk(stmt) if isinstance(sub, ast.Call)
        )
        self._uow_depth += 1 if opens else 0
        self.generic_visit(node)
        self._uow_depth -= 1 if opens else 0

    visit_AsyncWith = visit_With  # noqa: N815

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        in_context = self._uow_depth > 0 or self._whole_body_is_transactional
        if (
            in_context
            and isinstance(node.func, ast.Name)
            and not _hands_over_transaction(node)
            and not self._is_marked(node.lineno)
        ):
            callee = resolve(self._index, self._relpath, node.func.id)
            if callee and opening_chain(self._index, callee, MAX_CALL_DEPTH - 1):
                self.violations.append((self._relpath, self._enclosing_def, node.func.id))
        self.generic_visit(node)

    def _is_marked(self, lineno: int) -> bool:
        """A reason-carrying opt-out on the site's own line or the one above it."""
        for candidate in (lineno - 1, lineno - 2):
            if 0 <= candidate < len(self._lines) and _MARKER.search(self._lines[candidate]):
                return True
        return False


def find_violations(sources: dict[str, str]) -> list[Violation]:
    index = build_index(sources)
    return collect_visitor_violations(index.trees, lambda relpath: _NestedUnitTracker(index, relpath))


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_no_helper_opens_a_second_unit_of_work():
    """Both directions in one assertion: new violations AND entries the allowlist
    outlived. The shared helper is the only form that reports a stale entry as
    loudly as a new one (test_architecture_no_handrolled_allowlist_diff.py)."""
    assert_violations_match_allowlist(
        set(find_violations(read_source_roots(SCAN_ROOTS))),
        ALLOWLIST,
        fix_hint=(
            "get_db_session() hands back the THREAD-SCOPED session, with no nesting refcount. A "
            "second unit of work opened while one is already open commits the outer unit's "
            "in-flight writes, then closes the session out from under it — and on a preview branch "
            "its rollback discards the outer unit's writes too.\n"
            "  join the caller's unit:   def helper(..., uow: SomeUoW)      — the prkv.16 fix\n"
            "  optional join:            if uow is None: uow = stack.enter_context(SomeUoW(...))\n"
            "                            (src/core/tools/creatives/_assignments.py is the exemplar)\n"
            "If the site genuinely must stay, mark it: # structural-guard: nested-unit-of-work - <why>"
        ),
    )


def test_scan_scope_is_pinned():
    """A guard that quietly narrows its own scope reports clean for the wrong reason."""
    assert SCAN_ROOTS == ("src",)
    for root in SCAN_ROOTS:
        assert (REPO_ROOT / root).is_dir(), f"{root} does not exist — the scan would silently cover nothing"


def test_call_depth_is_pinned():
    """Shrinking the depth is the cheapest way to make this guard stop seeing things.

    The violation set was measured identical at 1, 2, 3 and 5 hops, so the value
    is not load-bearing for today's tree — but a future indirection would hide
    behind a silently lowered bound.
    """
    assert MAX_CALL_DEPTH == 3


def test_the_unit_of_work_base_still_exists():
    """The guard must not pin an API that has been renamed out from under it."""
    from src.core.database.repositories.uow import CreativeUoW

    assert CreativeUoW.__name__.endswith("UoW"), (
        "The *UoW naming convention this guard matches on has changed — _opens_unit_of_work "
        "would silently match nothing and the whole scan would report clean."
    )


def test_every_allowlist_entry_names_a_real_file():
    """A path typo would silently retire an entry AND hide the violation behind it."""
    for path, _, _ in sorted(ALLOWLIST):
        assert (REPO_ROOT / path).is_file(), f"allowlisted path {path} does not exist"


class TestDetectorMetaTests:
    """The detector must catch what it claims, and not what it does not."""

    # A minimal two-module world: the real resolution path (an import from a
    # sibling module) is exercised, not a single-file shortcut.
    CALLEE_OPENS = "from src.core.database.repositories.uow import CreativeUoW\n\ndef helper(tenant):\n    with CreativeUoW(tenant) as uow:\n        uow.creatives.create(1)\n"
    CALLEE_JOINS = "def helper(tenant, uow):\n    uow.creatives.create(1)\n"

    def _violations(self, caller_src: str, callee_src: str = CALLEE_OPENS) -> list[Violation]:
        return find_violations({"src/pkg/caller.py": caller_src, "src/pkg/callee.py": callee_src})

    # ── clause A: a call made while a unit is lexically open ──────────────

    def test_flags_a_call_inside_an_open_unit_to_a_function_that_opens_its_own(self):
        """GH #1644 in miniature — the site this guard was measured against."""
        src = (
            "from src.pkg.callee import helper\n"
            "\n"
            "def outer(tenant):\n"
            "    with MediaBuyUoW(tenant) as uow:\n"
            "        helper(tenant)\n"
        )
        assert self._violations(src) == [("src/pkg/caller.py", "outer", "helper")]

    def test_flags_a_call_inside_a_unit_entered_on_an_exit_stack(self):
        """sync_creatives acquires its unit this way, so a with-item-only check misses it."""
        src = (
            "from src.pkg.callee import helper\n"
            "\n"
            "def outer(tenant):\n"
            "    with ExitStack() as stack:\n"
            "        uow = stack.enter_context(CreativeUoW(tenant))\n"
            "        helper(tenant)\n"
        )
        assert self._violations(src) == [("src/pkg/caller.py", "outer", "helper")]

    def test_flags_the_prkv16_shape_if_it_is_reintroduced(self):
        """The exact revert of the change this guard was written for: put the
        workflow-step helper back on its own WorkflowUoW while sync_creatives
        calls it from inside the CreativeUoW it owns."""
        callee = "def _create_sync_workflow_steps(creatives, tenant):\n    with WorkflowUoW(tenant) as uow:\n        uow.workflows.create_step(1)\n"
        src = (
            "from src.pkg.callee import _create_sync_workflow_steps\n"
            "\n"
            "def _sync_creatives_impl(tenant):\n"
            "    with ExitStack() as stack:\n"
            "        uow = stack.enter_context(CreativeUoW(tenant))\n"
            "        _create_sync_workflow_steps(creatives=[], tenant=tenant)\n"
        )
        assert self._violations(src, callee) == [
            ("src/pkg/caller.py", "_sync_creatives_impl", "_create_sync_workflow_steps")
        ]

    def test_flags_a_call_reached_through_one_hop_of_indirection(self):
        """Wrapping the opener in a thin passthrough must not launder it."""
        callee = (
            "def helper(tenant):\n"
            "    with CreativeUoW(tenant) as uow:\n"
            "        uow.creatives.create(1)\n"
            "\n"
            "def passthrough(tenant):\n"
            "    return helper(tenant)\n"
        )
        src = (
            "from src.pkg.callee import passthrough\n"
            "\n"
            "def outer(tenant):\n"
            "    with MediaBuyUoW(tenant) as uow:\n"
            "        passthrough(tenant)\n"
        )
        assert self._violations(src, callee) == [("src/pkg/caller.py", "outer", "passthrough")]

    def test_flags_a_call_moved_into_a_nested_closure(self):
        """A nested def declares no parameters of its own — it closes over the
        enclosing unit — so resetting transactional state on entering one would
        make "wrap it in a def" a one-line evasion."""
        src = (
            "from src.pkg.callee import helper\n"
            "\n"
            "def outer(tenant, uow):\n"
            "    def _inner():\n"
            "        helper(tenant)\n"
            "    _inner()\n"
        )
        assert ("src/pkg/caller.py", "_inner", "helper") in self._violations(src)

    # ── clause B: the enclosing function was handed a transaction ─────────

    def test_flags_a_call_from_a_function_that_was_handed_a_session(self):
        """The clause that found the delivery-webhook scheduler: no `with` block
        is in sight — the caller two frames up holds the session open and passes
        it down."""
        src = (
            "from src.pkg.callee import helper\n"
            "\n"
            "def report(media_buy, session):\n"
            "    helper(media_buy.tenant_id)\n"
            "    session.scalar(stmt)\n"
        )
        assert self._violations(src) == [("src/pkg/caller.py", "report", "helper")]

    # ── clause C: handed a transaction, opens its own anyway ──────────────

    def test_flags_a_function_that_receives_a_uow_and_opens_its_own_unit(self):
        """The definition-site pin for prkv.16's fix."""
        src = (
            "def _create_sync_workflow_steps(creatives, tenant, uow: CreativeUoW):\n"
            "    with WorkflowUoW(tenant) as own:\n"
            "        own.workflows.create_step(1)\n"
        )
        assert self._violations(src) == [
            ("src/pkg/caller.py", "_create_sync_workflow_steps", "opens WorkflowUoW(tenant)")
        ]

    def test_does_not_flag_the_optional_join_shape(self):
        """_assignments.py:88-91 — own a unit only when not given one."""
        src = (
            "def _process_assignments(tenant, uow=None):\n"
            "    with ExitStack() as stack:\n"
            "        if uow is None:\n"
            "            uow = stack.enter_context(CreativeUoW(tenant))\n"
            "        uow.creatives.assign(1)\n"
        )
        assert self._violations(src) == []

    def test_does_not_flag_the_conditional_expression_join_shape(self):
        """blueprints/creatives.py — the same decision written as an IfExp."""
        src = (
            "def _review(tenant, db_session=None):\n"
            "    cm = AdminCreativeUoW(tenant) if db_session is None else contextlib.nullcontext()\n"
            "    with cm as uow:\n"
            "        pass\n"
        )
        assert self._violations(src) == []

    # ── negatives: the sanctioned forms and the ordinary ones ─────────────

    def test_does_not_flag_a_call_that_hands_the_callee_the_open_unit(self):
        """The prkv.16 fix itself: `_create_sync_workflow_steps(..., uow=uow)`."""
        src = (
            "from src.pkg.callee import helper\n"
            "\n"
            "def outer(tenant):\n"
            "    with CreativeUoW(tenant) as uow:\n"
            "        helper(tenant, uow=uow)\n"
        )
        assert self._violations(src, self.CALLEE_JOINS) == []

    def test_does_not_flag_a_call_that_hands_over_a_bare_session(self):
        """`_ai_review_creative_impl(db_session=uow.session)` — same handover, other name."""
        src = (
            "from src.pkg.callee import helper\n"
            "\n"
            "def outer(tenant):\n"
            "    with AdminCreativeUoW(tenant) as uow:\n"
            "        helper(tenant, db_session=uow.session)\n"
        )
        assert self._violations(src, self.CALLEE_JOINS) == []

    def test_does_not_flag_a_call_after_the_unit_closes(self):
        """Sequential units in one request path are normal — `_audit_log_sync` runs
        after sync_creatives' ExitStack exits, and is a separate question
        (salesagent-lslut), not a nested unit."""
        src = (
            "from src.pkg.callee import helper\n"
            "\n"
            "def outer(tenant):\n"
            "    with CreativeUoW(tenant) as uow:\n"
            "        uow.creatives.create(1)\n"
            "    helper(tenant)\n"
        )
        assert self._violations(src) == []

    def test_does_not_flag_a_callee_that_opens_nothing(self):
        src = (
            "from src.pkg.callee import helper\n"
            "\n"
            "def outer(tenant):\n"
            "    with CreativeUoW(tenant) as uow:\n"
            "        helper(tenant, uow)\n"
        )
        assert self._violations(src, "def helper(tenant, uow):\n    uow.creatives.create(1)\n") == []

    def test_does_not_flag_a_same_named_function_in_an_unimported_module(self):
        """Resolution is by import, not by bare name. Matching names alone was
        measured first and returned ten hits where this returns two."""
        src = "def outer(tenant):\n    with CreativeUoW(tenant) as uow:\n        helper(tenant)\n"
        assert self._violations(src) == []

    def test_respects_a_reason_carrying_marker(self):
        src = (
            "from src.pkg.callee import helper\n"
            "\n"
            "def outer(tenant):\n"
            "    with MediaBuyUoW(tenant) as uow:\n"
            "        # structural-guard: nested-unit-of-work - callee runs on its own engine\n"
            "        helper(tenant)\n"
        )
        assert self._violations(src) == []

    def test_a_bare_marker_without_a_reason_does_not_silence_the_site(self):
        src = (
            "from src.pkg.callee import helper\n"
            "\n"
            "def outer(tenant):\n"
            "    with MediaBuyUoW(tenant) as uow:\n"
            "        # structural-guard: nested-unit-of-work\n"
            "        helper(tenant)\n"
        )
        assert self._violations(src) == [("src/pkg/caller.py", "outer", "helper")]

    @pytest.mark.parametrize("form", KNOWN_UNCOVERED)
    def test_known_uncovered_dispatch_forms_are_declared_not_caught(self, form: str):
        """Pins resolution's edge honestly.

        These DO reach a unit-opening function and this guard does NOT follow
        them. The assertion exists so the gap is a stated limitation rather than
        a silent one — widen ``resolve`` when one of them shows up in production.
        """
        src = f"def outer(tenant):\n    with CreativeUoW(tenant) as uow:\n        {form}\n"
        assert self._violations(src) == [], (
            f"{form} is now resolved — move it out of KNOWN_UNCOVERED and into the coverage claim"
        )


class TestGuardIsNotVacuous:
    """The measurement that makes the whole guard meaningful, run against the
    REAL tree rather than a synthetic snippet: a lexical-only detector grades
    NOTHING here, and this one does not."""

    def test_a_lexically_nested_with_uow_detector_would_find_nothing(self):
        """The prkv.16 disease scan's headline number, re-measured live.

        If this ever fails, someone has written a plainly nested unit of work
        and clause A's call-graph machinery was not what caught it — good news,
        but the docstring's justification for the machinery needs revisiting.
        """
        nested = []
        for relpath, text in read_source_roots(SCAN_ROOTS).items():
            try:
                tree = ast.parse(text, filename=relpath)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.With | ast.AsyncWith):
                    continue
                if not any(_opens_unit_of_work(i.context_expr) for i in node.items):
                    continue
                for stmt in node.body:
                    for sub in ast.walk(stmt):
                        if isinstance(sub, ast.With | ast.AsyncWith) and any(
                            _opens_unit_of_work(i.context_expr) for i in sub.items
                        ):
                            nested.append(f"{relpath}:{sub.lineno}")
        assert nested == [], (
            "A lexically nested `with UoW(...)` now exists: " + ", ".join(nested) + ". Fix it, then "
            "update this guard's docstring — the claim that a lexical detector grades zero no longer holds."
        )

    def test_the_guard_actually_grades_the_production_tree(self):
        """A guard whose detector reaches nothing real is the failure mode this
        one was written to avoid. The allowlist is the measured evidence that it
        does not: every entry is a site the detector found in ``src/``."""
        found = set(find_violations(read_source_roots(SCAN_ROOTS)))
        assert found, (
            "The detector found ZERO sites in src/. Either every nested unit of work has been "
            "fixed — in which case empty the ALLOWLIST and delete this test's premise — or "
            "resolution broke and the guard is now vacuous."
        )
