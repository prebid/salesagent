"""Guard: an effect that escapes a transaction is routed through the UoW boundary.

An outbound HTTP call, a job handed to a background executor, or a notification
is not undone by rolling a transaction back. Before GH #1970 each one was gated
by a hand-placed ``if dry_run`` at its own call site — nine of them, of which
FOUR shipped ungraded and one sat on the wrong condition chain entirely. That is
the failure this guard exists to make impossible to repeat: escaping calls now go
through ``repo.after_commit(...)`` (deferred until commit) or
``repo.outbound(...)`` (suppressed for a preview), and a raw one reachable while
a transaction is open is a violation.

WHAT COUNTS AS TRANSACTIONAL CONTEXT — the rule is measured, not assumed.
Scanning only for calls lexically inside ``with *UoW(...)`` finds ZERO sites in
the whole tree, because the module that actually holds the six escaping calls
(``creatives/_processing.py``) never opens a unit of work — it RECEIVES a
repository. A lexical-only guard here would have graded nothing while looking
thorough, which is precisely the ungraded-gate failure being fixed. So:

  * a function that OPENS units of work        -> context is INSIDE those blocks
  * a function that RECEIVES a repo/uow param  -> the whole body is context
    (its caller's transaction is open around it)

The first clause must also see ``stack.enter_context(*UoW(...))``: sync_creatives
acquires its unit that way, so a ``visit_With``-only detector misclassifies the
very module under change.

Conversely, the second clause alone over-flags: ``media_buy_create`` takes
repositories and sends Slack, but every one of those sends sits BETWEEN its
``with`` blocks, i.e. after a commit. Both clauses together are what make this
guard neither vacuous nor noisy.

DENYLIST, NOT A COMPLETE THEORY OF ESCAPE. ``_ESCAPING`` names the escape hatches
that exist today. ``requests``, ``boto3``, ``smtplib``, or an outbound call
wrapped in ``run_async_in_sync_context`` are NOT caught; the known-uncovered set
is pinned in ``TestDetectorMetaTests`` so the next person extends this list
rather than assuming coverage.

Per-site opt-out is the repo's reason-required marker
(``# structural-guard: uow-effect-boundary - <why>``), not a growing central
allowlist. ALLOWLIST ships EMPTY.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import (
    REPO_ROOT,
    assert_violations_match_allowlist,
    parse_module,
    structural_guard_marker_re,
)

#: Directories this guard governs. Pinned by a meta-test so it cannot silently narrow.
SCAN_ROOTS = ("src/core/tools", "src/admin/blueprints")

#: Calls that leave the transaction. Method names, matched on the attribute.
_ESCAPING = frozenset(
    {
        "submit",  # a job handed to a background executor
        "build_creative",  # outbound HTTP to a creative agent
        "preview_creative",
        "notify_media_buy_event",  # slack
        "notify_creative_pending",
        "notify_creative_approved",
        "notify_creative_rejected",
        "send_message",
    }
)

#: Receivers that ARE the boundary — routing through these is the sanctioned form.
_SANCTIONED = frozenset({"after_commit", "outbound"})

#: Escape hatches this denylist does NOT cover. Pinned so the gap is declared.
KNOWN_UNCOVERED = ("requests.post", "boto3.client", "smtplib.SMTP", "run_async_in_sync_context")

#: Sites allowed to keep a raw escaping call, as (path, function, call form).
#: Keyed by the CALL, not just the function: two distinct escaping calls in one
#: function are two entries, so allowlisting one cannot cover the other.
#: SHRINK-ONLY, and empty is the goal.
ALLOWLIST: frozenset[tuple[str, str, str]] = frozenset()

_MARKER = structural_guard_marker_re("uow-effect-boundary")


def _opens_unit_of_work(expr: ast.expr) -> bool:
    """``CreativeUoW(...)``, or ``stack.enter_context(CreativeUoW(...))``."""
    if isinstance(expr, ast.Call):
        name = ast.unparse(expr.func)
        if name.endswith("UoW") or name.endswith("begin_nested"):
            return True
        if name.endswith("enter_context"):
            return any(_opens_unit_of_work(arg) for arg in expr.args)
    return False


def _own_body(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[ast.AST]:
    """Every node in *fn*, excluding the bodies of functions nested inside it."""
    for stmt in fn.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        yield from ast.walk(stmt)


def _is_boundary_receiver(func: ast.expr) -> bool:
    """``self.outbound`` / ``creative_repo.after_commit`` — not any ``.outbound``.

    Matching the method NAME alone would let an unrelated object named
    ``outbound`` silence the whole call.
    """
    receiver = ast.unparse(func.value).lower() if isinstance(func, ast.Attribute) else ""
    return receiver == "self" or receiver.endswith(("repo", "repository", "uow"))


def _names_handed_to_the_boundary(tree: ast.AST) -> frozenset[str]:
    """Nested helpers passed to ``after_commit``/``outbound`` — they run at drain."""
    handed = {
        arg.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) in _SANCTIONED
        for arg in node.args
        if isinstance(arg, ast.Name)
    }
    return frozenset(handed)


def _receives_transaction(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """The function is handed someone else's open transaction."""
    for arg in list(fn.args.args) + list(fn.args.kwonlyargs):
        annotation = ast.unparse(arg.annotation) if arg.annotation else ""
        if "Repository" in annotation or "UoW" in annotation or arg.arg.endswith("_repo") or arg.arg == "uow":
            return True
    return False


class _EffectBoundaryTracker(ast.NodeVisitor):
    """Find escaping calls reachable while a transaction is open."""

    def __init__(self, relpath: str, source_lines: list[str]) -> None:
        self.relpath = relpath
        self._lines = source_lines
        self.violations: list[tuple[str, str, str]] = []
        self._deferred_names: frozenset[str] = frozenset()
        self._uow_depth = 0
        self._whole_body_is_transactional = False
        self._enclosing_def = "<module>"

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        prev_def, prev_body = self._enclosing_def, self._whole_body_is_transactional
        self._enclosing_def = node.name
        # A function that opens its own units is transactional only INSIDE them;
        # one that merely receives a repository is transactional throughout.
        # Scanned over its OWN body only: walking into nested defs let ONE nested
        # `with SomeUoW(...)` anywhere disable this clause for the whole function.
        opens = any(
            _opens_unit_of_work(item.context_expr)
            for sub in _own_body(node)
            if isinstance(sub, ast.With | ast.AsyncWith)
            for item in sub.items
        )
        if _receives_transaction(node):
            self._whole_body_is_transactional = not opens
        elif node.name in self._deferred_names:
            # A nested helper handed to after_commit/outbound: it runs at drain,
            # OUTSIDE the transaction, so an escaping call in it is the point.
            self._whole_body_is_transactional = False
        else:
            # Otherwise INHERIT. A nested def declares no repo parameter of its
            # own — it closes over the enclosing one — so resetting to False here
            # made the guard blind to exactly the shape this change introduced:
            # move an escaping call into a nested `def _submit()` and call it
            # inline, and the old detector went green.
            self._whole_body_is_transactional = prev_body
        self.generic_visit(node)
        self._enclosing_def, self._whole_body_is_transactional = prev_def, prev_body

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_With(self, node: ast.With) -> None:  # noqa: N802
        # Either the with-item itself opens a unit (`with CreativeUoW(...)`), or
        # the block's BODY enters one on a stack (`uow = stack.enter_context(
        # CreativeUoW(...))`) — the latter is a plain statement, not a with-item,
        # so checking only context_expr misses sync_creatives entirely.
        opens = any(_opens_unit_of_work(item.context_expr) for item in node.items) or any(
            _opens_unit_of_work(sub) for stmt in node.body for sub in ast.walk(stmt) if isinstance(sub, ast.Call)
        )
        self._uow_depth += 1 if opens else 0
        self.generic_visit(node)
        self._uow_depth -= 1 if opens else 0

    visit_AsyncWith = visit_With  # noqa: N815

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        attr = getattr(node.func, "attr", None)
        in_context = self._uow_depth > 0 or self._whole_body_is_transactional
        if attr in _ESCAPING and in_context and not self._is_marked(node.lineno):
            self.violations.append((self.relpath, self._enclosing_def, ast.unparse(node.func)))
        elif attr in _SANCTIONED and _is_boundary_receiver(node.func):
            # The boundary itself takes a callable that WILL make the escaping
            # call, so the FIRST argument is not descended into. Everything else
            # still is: `outbound(lambda: send(), preview_result=render(...))`
            # evaluates that keyword EAGERLY, inside the transaction, which is
            # the very thing being routed away — skipping the whole call node
            # made the sanctioned form a way to smuggle one in.
            for child in node.args[1:] + [kw.value for kw in node.keywords]:
                self.visit(child)
            return
        self.generic_visit(node)

    def _is_marked(self, lineno: int) -> bool:
        """A reason-carrying opt-out on the call's own line or the one above it."""
        for candidate in (lineno - 1, lineno - 2):
            if 0 <= candidate < len(self._lines) and _MARKER.search(self._lines[candidate]):
                return True
        return False


def _scan(path: Path) -> list[tuple[str, str, str]]:
    tree = parse_module(path)
    tracker = _EffectBoundaryTracker(str(path.relative_to(REPO_ROOT)), path.read_text().splitlines())
    tracker._deferred_names = _names_handed_to_the_boundary(tree)
    tracker.visit(tree)
    return tracker.violations


def _scan_roots() -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for root in SCAN_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            found.extend(_scan(path))
    return found


def test_no_escaping_call_runs_inside_an_open_transaction():
    """Both directions in one assertion: new violations AND entries the allowlist
    outlived. Hand-rolling the set-diff is what
    test_architecture_no_handrolled_allowlist_diff.py exists to stop — the shared
    helper is the only form that reports a stale entry as loudly as a new one."""
    assert_violations_match_allowlist(
        set(_scan_roots()),
        ALLOWLIST,
        fix_hint=(
            "A rollback does not undo an outbound call, a submitted job or a notification, so a "
            "preview would fire it for real. Route it through the transaction that owns it:\n"
            "  repo.after_commit(fn)        — the result is not needed inline; runs only if this commits\n"
            "  repo.outbound(call)          — the result builds the response; suppressed for a preview\n"
            "If the site genuinely must stay, mark it: # structural-guard: uow-effect-boundary - <why>"
        ),
    )


def test_the_sanctioned_boundary_still_exists():
    """The guard must not pin an API that has been renamed out from under it."""
    from src.core.database.repositories.effects import SessionEffectsMixin

    for name in sorted(_SANCTIONED):
        assert hasattr(SessionEffectsMixin, name), (
            f"SessionEffectsMixin has no {name!r} — this guard would be directing people at an API "
            "that no longer exists, and its sanctioned-receiver check would silently pass everything."
        )


def test_scan_scope_is_pinned():
    """A guard that quietly narrows its own scope reports clean for the wrong reason."""
    assert SCAN_ROOTS == ("src/core/tools", "src/admin/blueprints")
    for root in SCAN_ROOTS:
        assert (REPO_ROOT / root).is_dir(), f"{root} does not exist — the scan would silently cover nothing"


class TestDetectorMetaTests:
    """The detector must catch what it claims, and not what it does not."""

    def _violations(self, src: str) -> list[tuple[str, str, str]]:
        tree = ast.parse(src)
        tracker = _EffectBoundaryTracker("<test>", src.splitlines())
        tracker._deferred_names = _names_handed_to_the_boundary(tree)
        tracker.visit(tree)
        return tracker.violations

    # ── Evasions an adversarial read of the FIRST version got past (all measured) ──

    def test_flags_an_escaping_call_moved_into_a_nested_closure_called_inline(self):
        """The shape THIS change introduced: wrap the submit in a nested def.

        The first detector reset its transactional state on entering any nested
        `def`, so moving the call one scope in and calling it inline went green —
        the guard would have blessed undoing its own fix.
        """
        src = "def f(creative_repo, cid):\n    def _submit():\n        _ex.submit(job, cid)\n    _submit()\n"
        assert self._violations(src)

    def test_does_not_flag_a_nested_helper_actually_handed_to_the_boundary(self):
        """The corrected form of the same shape: registered, so it runs at drain."""
        src = (
            "def f(creative_repo, cid):\n"
            "    def _submit():\n"
            "        _ex.submit(job, cid)\n"
            '    creative_repo.after_commit(_submit, label="ai_review")\n'
        )
        assert not self._violations(src)

    def test_flags_an_escaping_call_in_an_eagerly_evaluated_boundary_argument(self):
        """`preview_result=` is evaluated INSIDE the transaction, before the call.

        Skipping the whole sanctioned call node made the boundary itself the
        smuggling route.
        """
        src = "def f(creative_repo, cid):\n    return creative_repo.outbound(lambda: g(), preview_result=r.preview_creative(cid))\n"
        assert self._violations(src)

    def test_flags_the_sanctioned_method_name_on_an_unrelated_receiver(self):
        """Matching the NAME alone let any object called `.outbound(...)` silence a call."""
        src = "def f(creative_repo, cid):\n    return mailer.outbound(lambda: r.build_creative(cid))\n"
        assert self._violations(src)

    def test_a_nested_uow_does_not_disable_the_receives_repository_clause(self):
        """`opens` was computed with ast.walk, which descends into nested defs —
        so one unrelated nested `with SomeUoW(...)` blinded the whole function."""
        src = (
            "def f(creative_repo, cid):\n"
            "    def helper():\n"
            "        with OtherUoW('t') as u:\n"
            "            pass\n"
            "    r.build_creative(cid)\n"
        )
        assert self._violations(src)

    def test_flags_an_escaping_call_inside_a_with_block(self):
        src = "def f(tenant):\n    with CreativeUoW(tenant) as uow:\n        registry.build_creative(x=1)\n"
        assert self._violations(src)

    def test_flags_a_call_in_a_function_that_only_RECEIVES_a_repository(self):
        """The clause without which this guard grades nothing.

        _processing.py never opens a unit of work — it is handed one. A
        lexical-only detector measured ZERO hits across the entire tree.
        """
        src = "def f(creative_repo: CreativeRepository):\n    _executor.submit(job)\n"
        assert self._violations(src)

    def test_flags_a_call_inside_stack_enter_context(self):
        """sync_creatives acquires its unit this way, so a visit_With-only detector misses it."""
        src = (
            "def f(tenant):\n"
            "    with ExitStack() as stack:\n"
            "        uow = stack.enter_context(CreativeUoW(tenant))\n"
            "        registry.preview_creative(x=1)\n"
        )
        assert self._violations(src)

    def test_does_not_flag_a_call_after_the_block_closes(self):
        """media_buy_create's shape: it takes repositories AND opens its own units,
        and its Slack sends happen between them, after a commit."""
        src = (
            "def f(media_buy_repo: MediaBuyRepository):\n"
            "    with MediaBuyUoW(t) as uow:\n"
            "        uow.media_buys.get_by_id(x)\n"
            "    slack_notifier.notify_media_buy_event(y)\n"
        )
        assert self._violations(src) == []

    def test_does_not_flag_the_sanctioned_forms(self):
        src = (
            "def f(creative_repo: CreativeRepository):\n"
            "    creative_repo.after_commit(lambda: _executor.submit(job))\n"
            "    creative_repo.outbound(lambda: registry.build_creative(x=1))\n"
        )
        assert self._violations(src) == []

    def test_respects_a_reason_carrying_marker(self):
        src = (
            "def f(creative_repo: CreativeRepository):\n"
            "    # structural-guard: uow-effect-boundary - deferred via after_commit\n"
            "    _executor.submit(job)\n"
        )
        assert self._violations(src) == []

    @pytest.mark.parametrize("form", KNOWN_UNCOVERED)
    def test_known_uncovered_escape_hatches_are_declared_not_caught(self, form: str):
        """Pins the denylist's edge honestly.

        These DO escape a transaction and this guard does NOT catch them. The
        assertion exists so the gap is a stated limitation rather than a silent
        one — extend ``_ESCAPING`` when one of them shows up in production.
        """
        src = f"def f(creative_repo: CreativeRepository):\n    {form}(x=1)\n"
        assert self._violations(src) == [], (
            f"{form} is now caught — move it out of KNOWN_UNCOVERED and into _ESCAPING's coverage claim"
        )
