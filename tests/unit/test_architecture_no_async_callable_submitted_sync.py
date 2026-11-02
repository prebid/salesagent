"""Guard: an ``async def`` never reaches a SYNCHRONOUS invoker as a bare callable.

This is the shape GH #1972 removed. ``_ai_review_creative_async``
was an ``async def`` handed to ``_ai_review_executor.submit(...)``. ``submit()`` CALLS
the callable on a worker thread, and calling an ``async def`` only constructs a
coroutine object — the body never runs. The worker stored that object as the Future's
result and finished. The AI creative review therefore never executed: no verdict was
ever committed, the Slack notification and push webhook never fired, and creatives sat
at ``pending_review`` waiting for a reviewer that never started, while the log line
said "Submitted AI review for ...". It failed SILENTLY for the entire life of the
feature.

WHY A NEW GUARD, measured rather than assumed. ``iscoroutinefunction`` appears in
NO ``tests/unit/test_architecture_*.py``; ``ast.AsyncFunctionDef`` appears in several
but only as ``isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)`` traversal
boilerplate. Zero guards reason about the async COLOUR of a submitted callable. The
nearest sibling, ``test_architecture_uow_effect_boundary.py``, grades WHETHER an
escaping call routes through ``after_commit``/outbound — ``_defer_ai_review``
SATISFIED it while being 100% broken. That is precisely the gap this guard closes.

WHAT IS MEASURED — three clauses:

  * clause A — DIRECT submission. ``<x>.submit(<name>, ...)``,
    ``<x>.map(<name>, ...)``, ``run_in_executor(<exec>, <name>, ...)``,
    ``Thread(target=<name>)``, and ``functools.partial(<name>, ...)`` handed to any
    of those, where ``<name>`` resolves — through the module's own defs or its
    ``from x import y`` map — to an ``async def``. This is the live bug.

  * clause B — DECORATOR-MEDIATED submission. A decorator is classified ONCE, by its
    OWN body: a decorator whose wrapper does ``executor.submit(func, ...)`` or
    ``Thread(target=func)`` on the function it wraps IS a synchronous invoker. Any
    ``async def`` carrying such a decorator is a violation. ``src/adapters/utils/
    timeout.py`` is exactly that inside ``@timeout(seconds=N)``: applied to an
    ``async def`` it reproduces GH #1972 identically and just as silently. A guard
    phrased only as "literal ``Executor.submit(<async def>)``" leaves that back door
    open, which is why clause B is mandatory rather than nice-to-have.

    Clause B grades ZERO today — all 11 live ``@timeout`` usages decorate plain
    ``def``s. That is a PINNED KNOWN-ZERO, declared here rather than presented as
    coverage; ``TestGuardIsNotVacuous`` proves the classifier still reaches
    ``timeout.py`` in the real tree, so the zero is a measurement, not a silence.

  * clause C — LAMBDA-WRAPPED submission. ``submit(lambda: <async def>(...))`` where
    the lambda body does NOT drive the coroutine (no ``asyncio.run`` /
    ``run_async_in_sync_context`` / ``run_until_complete``). The SAFE form of this
    exists in-tree — ``src/core/tools/creative_formats.py`` does
    ``submit(lambda: asyncio.run(...))`` — so the negative case is graded against
    real production material, not only a synthetic fixture.

WHAT IS DELIBERATELY NOT RESOLVED. Name resolution is narrow on purpose: module-local
defs plus import-resolved ``ast.Name``, one hop through a package ``__init__``
re-export. Method calls (``self._review``), registry-pulled callables and
``getattr``-style dispatch are NOT resolved — see ``KNOWN_UNCOVERED``. The limit is
stated rather than left silent, per the precedent in
``test_architecture_nested_unit_of_work.py``.

THE ALLOWLIST SHIPS EMPTY. The prkv.14 fix removed the only instance, so shrink-only
is trivially satisfied and any future entry is a regression, not inherited debt.

Per-site opt-out (reason REQUIRED):
    # structural-guard: async-callable-submitted-sync - <why this is safe>
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
    iter_call_expressions,
    module_name_for,
    parse_sources,
    read_source_roots,
    structural_guard_marker_re,
)

#: Directories this guard governs. Pinned by a meta-test so it cannot silently narrow.
SCAN_ROOTS = ("src", "scripts")

#: Attribute names that CALL their first callable argument on another thread.
_SUBMIT_ATTRS = frozenset({"submit", "map"})

#: ``loop.run_in_executor(executor, fn, ...)`` — the callable is the SECOND argument.
_RUN_IN_EXECUTOR = "run_in_executor"

#: Callables whose ``target=`` keyword is invoked synchronously on a new thread.
_THREAD_CTORS = frozenset({"Thread", "threading.Thread", "Timer", "threading.Timer"})

#: Calls that actually DRIVE a coroutine to completion. Their presence inside a
#: lambda body is what makes ``submit(lambda: ...)`` the sanctioned shape.
#: Deliberately does NOT contain a bare ``run``: matching every ``<x>.run(...)`` as a
#: driver would let ``submit(lambda: worker.run(coro()))`` through. The cost is that
#: ``from asyncio import run`` then ``run(coro())`` reads as a violation — a FALSE
#: POSITIVE, which fails loudly, rather than a false negative, which is the failure
#: mode that produced GH #1972. Zero sites in the tree use that import form.
_DRIVERS = frozenset(
    {
        "asyncio.run",
        "asyncio.runners.run",
        "run_async_in_sync_context",
        "run_until_complete",
        "run_coroutine_threadsafe",
    }
)

#: Shapes that DO hand an ``async def`` to a synchronous invoker and that this guard
#: does NOT catch. Declared so the limit is a stated one, not a silent one. Widen
#: ``_resolve`` when one of these shows up in production.
KNOWN_UNCOVERED = (
    "executor.submit(self._review)",  # method call — receiver type is not resolved
    "executor.submit(handler)",  # a callable pulled out of a registry/dict
    "executor.submit(getattr(mod, name))",  # dynamic dispatch
)

#: Sites allowed to keep an async callable on a synchronous invoker, as
#: (path, enclosing function, callee). SHRINK-ONLY. EMPTY BY CONSTRUCTION: the
#: prkv.14 fix removed the only instance, so any entry added here is a NEW defect.
#: ``assert_violations_match_allowlist`` reports a stale entry as loudly as a new
#: violation, so the list cannot rot.
ALLOWLIST: frozenset[tuple[str, str, str]] = frozenset()

_MARKER = structural_guard_marker_re("async-callable-submitted-sync")


# ---------------------------------------------------------------------------
# Shape predicates
# ---------------------------------------------------------------------------


def _dotted(node: ast.expr) -> str:
    """``a.b.c`` for Name/Attribute chains, ``""`` for anything else."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    else:
        return ""
    return ".".join(reversed(parts))


def _unwrap_partial(expr: ast.expr) -> ast.expr:
    """``functools.partial(fn, ...)`` -> ``fn``. Otherwise *expr* unchanged."""
    while isinstance(expr, ast.Call):
        name = _dotted(expr.func)
        if name not in ("functools.partial", "partial") or not expr.args:
            break
        expr = expr.args[0]
    return expr


def _submitted_callables(call: ast.Call) -> list[ast.expr]:
    """The expressions *call* will INVOKE on another thread, if any.

    Covers ``.submit``/``.map`` (first positional), ``run_in_executor`` (second
    positional), and ``Thread``/``Timer`` (``target=`` keyword).
    """
    func_name = _dotted(call.func)
    tail = func_name.rsplit(".", 1)[-1] if func_name else ""

    if tail == _RUN_IN_EXECUTOR:
        return [_unwrap_partial(call.args[1])] if len(call.args) >= 2 else []
    if tail in _SUBMIT_ATTRS and isinstance(call.func, ast.Attribute):
        return [_unwrap_partial(call.args[0])] if call.args else []
    if func_name in _THREAD_CTORS:
        return [_unwrap_partial(kw.value) for kw in call.keywords if kw.arg == "target"]
    return []


def _drives_a_coroutine(node: ast.AST) -> bool:
    """True when the subtree contains a call that runs a coroutine to completion."""
    for sub in iter_call_expressions(node):
        name = _dotted(sub.func)
        if name in _DRIVERS or name.rsplit(".", 1)[-1] in _DRIVERS:
            return True
    return False


def _decorator_base_name(decorator: ast.expr) -> str:
    """``@timeout(seconds=60)`` -> ``timeout``; ``@foo`` -> ``foo``; ``@a.foo`` -> ``foo``."""
    node = decorator.func if isinstance(decorator, ast.Call) else decorator
    dotted = _dotted(node)
    return dotted.rsplit(".", 1)[-1] if dotted else ""


def _invokes_its_wrapped_function(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when *fn* takes a callable parameter and SUBMITS it to a thread.

    ``timeout.py``'s inner ``decorator(func)`` is the exemplar: the submit lives in
    a further-nested ``wrapper``, so the whole subtree is searched for a submit whose
    callable argument is a bare reference to one of ``fn``'s parameters.
    """
    params = {a.arg for a in fn.args.args + fn.args.posonlyargs + fn.args.kwonlyargs}
    if not params:
        return False
    for node in iter_call_expressions(fn):
        for submitted in _submitted_callables(node):
            if isinstance(submitted, ast.Name) and submitted.id in params:
                return True
    return False


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

Violation = tuple[str, str, str]


@dataclass(frozen=True)
class _Index:
    trees: dict[str, ast.Module]
    lines: dict[str, list[str]]
    is_async: dict[str, bool]
    imports: dict[str, dict[str, tuple[str, str]]]
    modules: dict[str, str]
    sync_invoker_decorators: frozenset[str]


def build_index(sources: dict[str, str]) -> _Index:
    """Index every module ONCE: which names are ``async def``, and which top-level
    functions are decorators that synchronously invoke what they wrap."""
    trees, lines = parse_sources(sources)

    is_async: dict[str, bool] = {}
    imports: dict[str, dict[str, tuple[str, str]]] = {}
    modules = {module_name_for(relpath): relpath for relpath in trees}
    sync_invokers: set[str] = set()

    for relpath, tree in trees.items():
        imports[relpath] = import_map(relpath, tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                is_async[f"{relpath}::{node.name}"] = isinstance(node, ast.AsyncFunctionDef)
        # Clause B classification. A decorator FACTORY (``timeout(seconds)``) and a
        # plain decorator (``def deco(func)``) are both named by their OUTERMOST
        # module-level def, because that is the name written at the ``@`` site.
        for outer in tree.body:
            if not isinstance(outer, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if any(
                isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef) and _invokes_its_wrapped_function(inner)
                for inner in ast.walk(outer)
            ):
                sync_invokers.add(f"{relpath}::{outer.name}")

    return _Index(trees, lines, is_async, imports, modules, frozenset(sync_invokers))


def _resolve(index: _Index, relpath: str, name: str) -> str | None:
    """A bare name -> the qualname of the function it refers to, or None.

    Imports first (``from src.admin.blueprints.creatives import _ai_review_creative``),
    then the referring module's own defs. One hop through a package ``__init__``
    re-export is followed, because ``src/core/tools/creatives/__init__.py`` is
    exactly that.
    """
    imported = index.imports.get(relpath, {}).get(name)
    if imported:
        target, original = imported
        candidate = index.modules.get(target)
        if not candidate:
            return None
        if f"{candidate}::{original}" in index.is_async:
            return f"{candidate}::{original}"
        if candidate.endswith("__init__.py"):
            reexport = index.imports.get(candidate, {}).get(original)
            if reexport:
                inner = index.modules.get(reexport[0])
                if inner and f"{inner}::{reexport[1]}" in index.is_async:
                    return f"{inner}::{reexport[1]}"
        return None
    qualname = f"{relpath}::{name}"
    return qualname if qualname in index.is_async else None


def _resolves_to_async_def(index: _Index, relpath: str, expr: ast.expr) -> str | None:
    """The qualname *expr* names, when it names an ``async def``. Else None."""
    if not isinstance(expr, ast.Name):
        return None
    qualname = _resolve(index, relpath, expr.id)
    return qualname if qualname and index.is_async.get(qualname) else None


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class _AsyncSubmissionTracker(ast.NodeVisitor):
    """Async callables handed to a synchronous invoker: directly (A), through a
    submitting decorator (B), or inside a non-driving lambda (C)."""

    def __init__(self, index: _Index, relpath: str) -> None:
        self._index = index
        self._relpath = relpath
        self._lines = index.lines.get(relpath, [])
        self._stack: list[str] = []
        self.violations: list[Violation] = []

    # -- scope tracking ----------------------------------------------------
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._check_decorators(node)
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def _enclosing(self) -> str:
        return "::".join(self._stack) if self._stack else "<module>"

    # -- clause B ----------------------------------------------------------
    def _check_decorators(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if not isinstance(node, ast.AsyncFunctionDef):
            return
        for decorator in node.decorator_list:
            name = _decorator_base_name(decorator)
            if not name:
                continue
            qualname = self._resolve_any(name)
            if qualname in self._index.sync_invoker_decorators and not self._is_marked(decorator.lineno):
                self.violations.append((self._relpath, node.name, f"@{name}"))

    def _resolve_any(self, name: str) -> str | None:
        """Resolve a decorator name even though decorators are not ``async def``."""
        imported = self._index.imports.get(self._relpath, {}).get(name)
        if imported:
            target, original = imported
            candidate = self._index.modules.get(target)
            return f"{candidate}::{original}" if candidate else None
        return f"{self._relpath}::{name}"

    # -- clauses A and C ---------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        for submitted in _submitted_callables(node):
            if self._is_marked(node.lineno):
                continue
            qualname = _resolves_to_async_def(self._index, self._relpath, submitted)
            if qualname:  # clause A
                self.violations.append((self._relpath, self._enclosing(), qualname.split("::")[-1]))
            elif isinstance(submitted, ast.Lambda):  # clause C
                self._check_lambda(submitted)
        self.generic_visit(node)

    def _check_lambda(self, lam: ast.Lambda) -> None:
        if _drives_a_coroutine(lam.body):
            return  # the sanctioned shape: submit(lambda: asyncio.run(coro()))
        for sub in iter_call_expressions(lam.body):
            qualname = _resolves_to_async_def(self._index, self._relpath, sub.func)
            if qualname:
                self.violations.append((self._relpath, self._enclosing(), f"lambda -> {qualname.split('::')[-1]}"))

    # -- opt-out -----------------------------------------------------------
    def _is_marked(self, lineno: int) -> bool:
        """A reason-carrying ``# structural-guard:`` comment on the line or the two above."""
        for offset in (0, -1, -2):
            candidate = lineno - 1 + offset
            if 0 <= candidate < len(self._lines) and _MARKER.search(self._lines[candidate]):
                return True
        return False


def find_violations(sources: dict[str, str]) -> list[Violation]:
    """Pure function over source text — the unit the meta-tests exercise."""
    index = build_index(sources)
    return collect_visitor_violations(index.trees, lambda relpath: _AsyncSubmissionTracker(index, relpath))


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_no_async_callable_is_submitted_to_a_synchronous_invoker():
    """Both directions in one assertion: new violations AND entries the allowlist
    outlived. The allowlist is EMPTY, so any output at all is a regression."""
    assert_violations_match_allowlist(
        set(find_violations(read_source_roots(SCAN_ROOTS))),
        ALLOWLIST,
        fix_hint=(
            "Calling an `async def` from a worker thread only CONSTRUCTS a coroutine object — "
            "the body never runs, the Future completes immediately with that object, and the "
            "work is silently dropped. This is GH #1972 (the AI creative review that never ran).\n"
            "  make the job a plain `def`:   the prkv.14 fix — do this when the body has no `await`\n"
            "  or drive the coroutine:       submit(lambda: run_async_in_sync_context(coro()))\n"
            "                                (run_async_in_sync_context takes a coroutine OBJECT and\n"
            "                                 raises TypeError on a coroutine FUNCTION, so the mistake\n"
            "                                 cannot be silent a second time)\n"
            "  decorators count too:         @timeout(...) submits what it wraps, so it must not be\n"
            "                                applied to an `async def`\n"
            "If the site genuinely must stay, mark it: "
            "# structural-guard: async-callable-submitted-sync - <why>"
        ),
    )


def test_scan_scope_is_pinned():
    """A guard that quietly narrows its own scope reports clean for the wrong reason."""
    assert SCAN_ROOTS == ("src", "scripts")
    for root in SCAN_ROOTS:
        assert (REPO_ROOT / root).is_dir(), f"{root} does not exist — the scan would silently cover nothing"


def test_the_allowlist_is_empty():
    """The prkv.14 fix removed the only instance. Shrink-only from zero means any
    entry is a NEW defect and must be fixed, never admitted."""
    assert ALLOWLIST == frozenset(), (
        "This allowlist shipped empty and may only shrink. A new entry means an async callable "
        "was handed to a synchronous invoker again — fix the site instead."
    )


def test_the_sanctioned_bridge_still_exists():
    """The guard's fix hint points at a helper; if it is renamed the hint rots."""
    from src.core.validation_helpers import run_async_in_sync_context

    assert callable(run_async_in_sync_context)


class TestDetectorMetaTests:
    """The detector must catch what it claims, and not what it does not."""

    # A minimal two-module world, so the real resolution path (an import from
    # another module) is exercised rather than a single-file shortcut.
    ASYNC_JOB = "async def review_creative(creative_id):\n    return 1\n"
    SYNC_JOB = "def review_creative(creative_id):\n    return 1\n"
    SUBMITTING_DECORATOR = (
        "import concurrent.futures\n"
        "def timeout(seconds=300):\n"
        "    def decorator(func):\n"
        "        def wrapper(*args, **kwargs):\n"
        "            with concurrent.futures.ThreadPoolExecutor() as executor:\n"
        "                future = executor.submit(func, *args, **kwargs)\n"
        "                return future.result(timeout=seconds)\n"
        "        return wrapper\n"
        "    return decorator\n"
    )

    def _violations(self, caller_src: str, *, job: str | None = None, deco: str | None = None) -> list[Violation]:
        sources = {"src/caller.py": caller_src}
        sources["src/jobs.py"] = self.ASYNC_JOB if job is None else job
        sources["src/decorators.py"] = self.SUBMITTING_DECORATOR if deco is None else deco
        return find_violations(sources)

    # -- clause A: direct submission ---------------------------------------

    def test_flags_the_prkv14_shape_if_it_is_reintroduced(self):
        """The literal GH #1972 site: an imported async def handed to submit()."""
        src = (
            "from src.jobs import review_creative\n"
            "def _defer(executor, creative_id):\n"
            "    executor.submit(review_creative, creative_id=creative_id)\n"
        )
        assert self._violations(src) == [("src/caller.py", "_defer", "review_creative")]

    def test_flags_a_module_local_async_def_submitted(self):
        src = "async def job():\n    return 1\n\ndef go(executor):\n    executor.submit(job)\n"
        assert self._violations(src) == [("src/caller.py", "go", "job")]

    def test_flags_a_thread_target(self):
        src = (
            "import threading\n"
            "from src.jobs import review_creative\n"
            "def go():\n"
            "    threading.Thread(target=review_creative).start()\n"
        )
        assert self._violations(src) == [("src/caller.py", "go", "review_creative")]

    def test_flags_run_in_executor_second_argument(self):
        src = "from src.jobs import review_creative\n\ndef go(loop, pool):\n    loop.run_in_executor(pool, review_creative)\n"
        assert self._violations(src) == [("src/caller.py", "go", "review_creative")]

    def test_flags_executor_map(self):
        src = "from src.jobs import review_creative\n\ndef go(executor, ids):\n    list(executor.map(review_creative, ids))\n"
        assert self._violations(src) == [("src/caller.py", "go", "review_creative")]

    def test_flags_a_partial_wrapped_async_def(self):
        """``functools.partial`` is the obvious way to slip past a shallow matcher."""
        src = (
            "import functools\n"
            "from src.jobs import review_creative\n"
            "def go(executor, cid):\n"
            "    executor.submit(functools.partial(review_creative, cid))\n"
        )
        assert self._violations(src) == [("src/caller.py", "go", "review_creative")]

    def test_flags_a_submission_from_a_nested_closure(self):
        """The real site submits from a closure handed to ``after_commit``."""
        src = (
            "from src.jobs import review_creative\n"
            "def _defer_ai_review(repo, executor):\n"
            "    def _submit():\n"
            "        executor.submit(review_creative)\n"
            "    repo.after_commit(_submit)\n"
        )
        assert self._violations(src) == [("src/caller.py", "_defer_ai_review::_submit", "review_creative")]

    # -- clause B: decorator-mediated submission ---------------------------

    def test_flags_an_async_def_carrying_a_submitting_decorator(self):
        """@timeout applied to an async def reproduces GH #1972 through the back door."""
        src = "from src.decorators import timeout\n\n@timeout(seconds=60)\nasync def fetch():\n    return 1\n"
        assert self._violations(src) == [("src/caller.py", "fetch", "@timeout")]

    def test_flags_a_bare_submitting_decorator_on_an_async_def(self):
        deco = (
            "import concurrent.futures\n"
            "def offload(func):\n"
            "    def wrapper(*a, **k):\n"
            "        with concurrent.futures.ThreadPoolExecutor() as ex:\n"
            "            return ex.submit(func, *a, **k).result()\n"
            "    return wrapper\n"
        )
        src = "from src.decorators import offload\n\n@offload\nasync def fetch():\n    return 1\n"
        assert self._violations(src, deco=deco) == [("src/caller.py", "fetch", "@offload")]

    def test_does_not_flag_a_submitting_decorator_on_a_plain_def(self):
        """The 11 live ``@timeout`` usages — all on plain defs — must stay silent."""
        src = "from src.decorators import timeout\n\n@timeout(seconds=60)\ndef fetch():\n    return 1\n"
        assert self._violations(src) == []

    def test_does_not_flag_a_decorator_that_does_not_submit(self):
        deco = "import functools\ndef retry(n=3):\n    def decorator(func):\n        @functools.wraps(func)\n        def wrapper(*a, **k):\n            return func(*a, **k)\n        return wrapper\n    return decorator\n"
        src = "from src.decorators import retry\n\n@retry(3)\nasync def fetch():\n    return 1\n"
        assert self._violations(src, deco=deco) == []

    # -- clause C: lambda-wrapped submission -------------------------------

    def test_flags_a_lambda_that_calls_an_async_def_without_driving_it(self):
        src = "from src.jobs import review_creative\n\ndef go(executor):\n    executor.submit(lambda: review_creative(1))\n"
        assert self._violations(src) == [("src/caller.py", "go", "lambda -> review_creative")]

    def test_does_not_flag_the_asyncio_run_lambda(self):
        """``submit(lambda: asyncio.run(coro()))`` — the in-tree safe form at
        ``src/core/tools/creative_formats.py``."""
        src = (
            "import asyncio\n"
            "from src.jobs import review_creative\n"
            "def go(executor):\n"
            "    executor.submit(lambda: asyncio.run(review_creative(1)))\n"
        )
        assert self._violations(src) == []

    def test_does_not_flag_the_sanctioned_helper_lambda(self):
        src = (
            "from src.core.validation_helpers import run_async_in_sync_context\n"
            "from src.jobs import review_creative\n"
            "def go(executor):\n"
            "    executor.submit(lambda: run_async_in_sync_context(review_creative(1)))\n"
        )
        assert self._violations(src) == []

    # -- negatives: the shapes the .3 trace atom proved SAFE ---------------

    def test_does_not_flag_a_plain_def_submitted(self):
        src = "from src.jobs import review_creative\n\ndef go(executor):\n    executor.submit(review_creative)\n"
        assert self._violations(src, job=self.SYNC_JOB) == []

    def test_does_not_flag_an_async_def_handed_to_an_awaiting_framework(self):
        """Route(...)/@router.get/FastMCP lifespan registrations are the 45 SAFE
        sites the prkv.14 trace atom enumerated — the false-positive acceptance bar."""
        src = (
            "from starlette.routing import Route\n"
            "from src.jobs import review_creative\n"
            "def build():\n"
            "    return [Route('/x', review_creative)]\n"
        )
        assert self._violations(src) == []

    def test_does_not_flag_an_awaited_call(self):
        src = "from src.jobs import review_creative\n\nasync def go():\n    return await review_creative(1)\n"
        assert self._violations(src) == []

    def test_does_not_flag_a_same_named_function_in_an_unimported_module(self):
        """Resolution is import-driven: a same-named async def elsewhere is not a match."""
        src = "def review_creative(cid):\n    return cid\n\ndef go(executor):\n    executor.submit(review_creative)\n"
        assert self._violations(src) == []

    def test_respects_a_reason_carrying_marker(self):
        src = (
            "from src.jobs import review_creative\n"
            "def go(executor):\n"
            "    # structural-guard: async-callable-submitted-sync - the pool awaits it itself\n"
            "    executor.submit(review_creative)\n"
        )
        assert self._violations(src) == []

    def test_a_bare_marker_without_a_reason_does_not_silence_the_site(self):
        """A marker is a justification, not an off switch."""
        src = (
            "from src.jobs import review_creative\n"
            "def go(executor):\n"
            "    # structural-guard: async-callable-submitted-sync\n"
            "    executor.submit(review_creative)\n"
        )
        assert self._violations(src) == [("src/caller.py", "go", "review_creative")]

    @pytest.mark.parametrize("form", KNOWN_UNCOVERED)
    def test_known_uncovered_dispatch_forms_are_declared_not_caught(self, form: str):
        """These shapes ARE the disease and are NOT detected. The gap is a declared
        one — widen ``_resolve`` when one of them shows up in production."""
        src = f"def go(executor, mod, name):\n    {form}\n"
        assert self._violations(src) == [], (
            f"{form} is now resolved — move it out of KNOWN_UNCOVERED and into the coverage claim"
        )


class TestGuardIsNotVacuous:
    """This guard legitimately finds ZERO live violations: the prkv.14 fix removed
    the only one. A zero is worthless unless the detector provably REACHES the real
    production shapes it grades, so each clause is measured against the live tree.
    """

    @pytest.fixture(scope="class")
    def index(self) -> _Index:
        return build_index(read_source_roots(SCAN_ROOTS))

    def test_clause_a_the_pre_fix_site_is_flagged_when_reconstructed(self):
        """The exact pre-fix shape from commit d0ee22f4b, rebuilt from the real
        module layout. If this stops failing the detector has gone blind."""
        sources = {
            "src/admin/blueprints/creatives.py": (
                "async def _ai_review_creative_async(creative_id, tenant_id, webhook_url):\n    return None\n"
            ),
            "src/core/tools/creatives/_processing.py": (
                "def _defer_ai_review(creative_repo, creative_id, tenant):\n"
                "    from src.admin.blueprints.creatives import _ai_review_executor\n"
                "    def _submit():\n"
                "        from src.admin.blueprints.creatives import _ai_review_creative_async\n"
                "        _ai_review_executor.submit(_ai_review_creative_async, creative_id=creative_id)\n"
                "    creative_repo.after_commit(_submit)\n"
            ),
        }
        assert find_violations(sources) == [
            ("src/core/tools/creatives/_processing.py", "_defer_ai_review::_submit", "_ai_review_creative_async")
        ]

    def test_clause_a_the_fixed_site_is_now_a_plain_def(self):
        """The live tree's proof that clause A's zero is the FIX, not blindness."""
        index = build_index(read_source_roots(SCAN_ROOTS))
        assert index.is_async.get("src/admin/blueprints/creatives.py::_ai_review_creative") is False, (
            "_ai_review_creative is not a plain def in src/admin/blueprints/creatives.py — either it "
            "was renamed (update this test) or GH #1972 has been reintroduced."
        )
        assert "src/admin/blueprints/creatives.py::_ai_review_creative_async" not in index.is_async, (
            "The pre-fix async name is back in the tree."
        )

    def test_clause_b_classifies_the_real_timeout_decorator(self, index: _Index):
        """@timeout IS a synchronous invoker, found by reading its own body in the
        real tree. Clause B is not a synthetic-only clause."""
        assert "src/adapters/utils/timeout.py::timeout" in index.sync_invoker_decorators, (
            "The @timeout decorator is no longer classified as a synchronous invoker. Either "
            "src/adapters/utils/timeout.py changed shape, or _invokes_its_wrapped_function broke "
            "— in which case clause B now grades nothing while looking thorough."
        )

    def test_clause_b_grades_zero_because_every_timeout_usage_is_a_plain_def(self, index: _Index):
        """The pinned known-zero, re-measured live rather than asserted once and
        trusted. If a @timeout ever lands on an async def, the main guard fails and
        this test explains why the zero stopped holding."""
        decorated: list[str] = []
        for relpath, tree in index.trees.items():
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                for decorator in node.decorator_list:
                    if _decorator_base_name(decorator) == "timeout":
                        decorated.append(f"{relpath}::{node.name}::{type(node).__name__}")
        assert decorated, "No @timeout usages remain in the tree — clause B's known-zero premise is gone."
        async_decorated = [d for d in decorated if d.endswith("AsyncFunctionDef")]
        assert async_decorated == [], f"@timeout applied to an async def: {async_decorated}"

    def test_clause_c_reaches_the_real_lambda_submission(self, index: _Index):
        """The one in-tree ``submit(lambda: ...)`` — creative_formats.py — must be
        SEEN by clause C and classified safe because the lambda drives the coroutine.
        Seeing it is what proves clause C is wired to production, not only fixtures."""
        seen: list[str] = []
        driving: list[str] = []
        for relpath, tree in index.trees.items():
            for node in iter_call_expressions(tree):
                for submitted in _submitted_callables(node):
                    if isinstance(submitted, ast.Lambda):
                        seen.append(f"{relpath}:{node.lineno}")
                        if _drives_a_coroutine(submitted.body):
                            driving.append(f"{relpath}:{node.lineno}")
        assert seen, (
            "Clause C found no lambda submissions anywhere in src/ — _submitted_callables no longer "
            "reaches them and clause C is vacuous."
        )
        assert seen == driving, "A lambda is submitted without driving its coroutine: " + ", ".join(
            sorted(set(seen) - set(driving))
        )

    def test_a_name_only_detector_would_have_missed_the_bug(self):
        """The bug's own name said ``_async``. A guard keyed on naming — or on
        ``iscoroutinefunction`` at import time, which cannot run over a tree — grades
        the label, not the colour. This guard reads ``ast.AsyncFunctionDef``."""
        sources = {
            "src/jobs.py": "async def review_creative(cid):\n    return cid\n",
            "src/caller.py": "from src.jobs import review_creative\n\ndef go(executor):\n    executor.submit(review_creative)\n",
        }
        assert find_violations(sources) == [("src/caller.py", "go", "review_creative")], (
            "An async def with no _async in its name must still be caught — the colour is the "
            "signal, never the identifier."
        )
