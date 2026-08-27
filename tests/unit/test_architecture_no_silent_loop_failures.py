"""Guard: per-item failures in _impl loops must be surfaced, not swallowed.

CLAUDE.md rule: "No Quiet Failures". When an ``_impl`` function iterates to
build a response and an item's processing fails, the failure must be visible
to the caller — a raised ``AdCPError``, an advisory appended to the response's
``errors[]`` list, or at minimum a recorded per-item result. A handler that
only logs (or logs and ``continue``s) makes the item silently vanish from the
response: the buyer sees a shorter list with no signal that anything failed.

Origin: PR #1545 review — ``_get_media_buy_delivery_impl`` had two sibling
handlers on the same loop path; the inner adapter handler appended a
``SERVICE_UNAVAILABLE`` advisory while the outer handler only logged and fell
through, so a failure in the status/model path dropped the buy with no signal.

TWO RULES live here, and they PARTITION the handler space by position — the loop
rule requires ``in_loop``, the straight-line rule forbids it, so no handler is
reported twice and neither allowlist can quietly cover the other's ground:

1. ``find_silent_loop_handlers`` — per-item failures in a ``for``/``while`` loop
   (described below). Allowlist: ``SILENT_LOOP_HANDLER_ALLOWLIST``.
2. ``find_silent_advisory_handlers`` — STRAIGHT-LINE degradations that drop
   content from the response as a whole. In scope when the ``_impl`` builds an
   advisory container OR its return annotation names a response type carrying
   ``errors[]`` (deliberately shallow — see ``_returns_response_with_errors``).
   Allowlist: ``SILENT_ADVISORY_HANDLER_ALLOWLIST``, plus source-level
   ``# structural-guard:`` resolutions applied in ``_scan_all_advisory``.

Neither rule sees a handler NESTED inside another handler: best-effort cleanup
on a failure path (an audit-log write inside a SERVICE_UNAVAILABLE handler) is
exempt by design. That is why the partition is "at most one rule", not "exactly
one" — see ``TestSilentHandlerRulePartition``.

Both allowlists are keyed (file, function, ORDINAL-within-function), not
(file, function): a coarser key let a second violation hide behind an existing
entry, and a line-based key went stale on any edit above the handler.

Detection for rule 1 (AST): inside functions named ``*_impl`` under
``src/core/tools/``, an ``except`` handler that sits directly in a
``for``/``while`` loop (not nested inside another handler) is a violation when
it:

- contains no ``raise``, AND
- calls no ``.append(...)`` / ``.extend(...)`` / ``.add(...)``, AND
- contains ``continue`` (item explicitly skipped), OR consists solely of
  expression/``pass`` statements (log-only fall-through), OR LOGS while assigning
  a fallback.

Handlers that SILENTLY assign a fallback value and let the iteration proceed are
fine — the item still reaches the response and nothing claims otherwise. Adding a
log line to such a handler is the handler admitting the value it substituted is
not the real one, and that admission must reach the buyer too: see
``_handler_is_silent``.

Allowlist can only SHRINK. Every entry has a FIXME(#gh-issue) at the source.
"""

import ast

import pytest

from tests.unit._architecture_helpers import (
    REPO_ROOT,
    assert_detector_catches_ast_snippets,
    assert_violations_match_allowlist,
    format_failure,
    iter_call_expressions,
    iter_module_trees,
    parse_module,
)

SCAN_DIRS = [REPO_ROOT / "src/core/tools"]


def _ordinal_keys(found: list[tuple[str, str, int]]) -> list[tuple[str, str, int]]:
    """Re-key (file, function, LINE) violations as (file, function, ORDINAL).

    Ordinal is the handler's index among the silent handlers *of that function*, in
    source order. Neither of the obvious alternatives works:

    - (file, function) alone is BLIND to a new violation inside an
      already-allowlisted function. Mutation-proven, not theorised:
      ``_list_creative_formats_impl`` holds TWO degrading handlers behind ONE key
      today, so adding a third changes nothing the guard can see.
    - (file, function, line) goes stale on any edit ABOVE the handler. This ticket
      deletes four ``policy_disabled_reason`` lines in products.py, which would have
      invalidated every line-keyed entry below them for no behavioural reason.

    Ordinal is stable under edits elsewhere in the file and still distinguishes
    siblings within one function.
    """
    by_function: dict[tuple[str, str], list[int]] = {}
    for relpath, func, line in found:
        by_function.setdefault((relpath, func), []).append(line)
    for lines in by_function.values():
        lines.sort()
    return [(relpath, func, by_function[(relpath, func)].index(line)) for relpath, func, line in found]


# Pre-existing violations, keyed (repo-relative file, enclosing function, ordinal
# of the silent handler within that function). Each has a FIXME(#gh-issue) comment
# at the source. Shrink-only.
SILENT_LOOP_HANDLER_ALLOWLIST: set[tuple[str, str, int]] = {
    # FIXME(#1566): unparseable Broadstreet template dropped from formats silently
    ("src/core/tools/creative_formats.py", "_list_creative_formats_impl", 0),
    # FIXME(#1566): creative-association failure logged only, absent from response
    ("src/core/tools/media_buy_create.py", "_create_media_buy_impl", 0),
}

#: Logger method names. Used by BOTH rules: a handler that calls one is admitting
#: something went wrong, which is what disqualifies the otherwise-exempt silent
#: fallback assignment in each detector.
_LOG_METHODS = frozenset({"debug", "info", "warning", "error", "exception", "critical"})

FIX_HINT = (
    "Surface the failure: append an advisory Error to the response errors[] list "
    "(see the SERVICE_UNAVAILABLE handler in _get_media_buy_delivery_impl), raise an "
    "AdCPError, or assign a fallback the response can carry. If the swallow is "
    "genuinely correct, allowlist it with a FIXME(#gh-issue) at the source."
)


def _handler_is_silent(handler: ast.ExceptHandler) -> bool:
    """True when the handler swallows the failure without surfacing it.

    Three swallowing shapes, all equivalent from the buyer's seat:

    - ``continue`` — the item is explicitly skipped;
    - log-only fall-through — the body is nothing but expression/``pass``;
    - ASSIGN-A-FALLBACK-**AND**-LOG — the item reaches the response, but carrying a
      value the seller never asserted, and the log line is the handler ADMITTING
      that. This is the rule ``_handler_is_exempt_shape``'s docstring already
      states for the straight-line detector — "A handler that LOGS is admitting
      something went wrong and must still surface it; only the silent, deliberate
      default is exempt" — applied here. A SILENT fallback assignment stays exempt
      (the ``creative_formats`` event-loop retry and cursor reset), so this rule
      adds no violations today; what it PINS is that a site which chose to log
      cannot later drop its ``errors[]`` advisory and keep only the log.

    KNOWN OVER-APPROXIMATION: a handler is treated as *surfacing* if it raises OR
    calls any ``.append``/``.extend``/``.add`` — regardless of the target. A
    handler that appends to an unrelated scratch buffer (``log; scratch.append(x);
    continue``) is therefore a FALSE NEGATIVE this guard will not catch: proving,
    via AST alone, that the append target is the response's ``errors[]`` list
    would require whole-function dataflow the guard deliberately avoids. So an
    empty allowlist means "no handler that both loops-and-continues AND does
    nothing list-like was found" — NOT "every dropped item is provably surfaced."
    The append-to-``errors[]`` convention is the enforceable proxy; genuine
    surfacing is still a human-review responsibility.
    """
    has_continue = False
    has_log = False
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return False
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"append", "extend", "add"}
        ):
            return False
        if isinstance(node, ast.Continue):
            has_continue = True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _LOG_METHODS:
            has_log = True
    log_only = all(isinstance(stmt, ast.Expr | ast.Pass) for stmt in handler.body)
    return has_continue or log_only or has_log


def find_silent_loop_handlers(tree: ast.Module, relpath: str) -> list[tuple[str, str, int]]:
    """Return (relpath, function_name, lineno) for silent handlers in _impl loops."""
    violations: list[tuple[str, str, int]] = []

    def visit(node: ast.AST, func_name: str, in_loop: bool, in_handler: bool) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            func_name = node.name
            in_loop = False  # loop/handler context does not cross function boundaries
            in_handler = False
        if isinstance(node, ast.For | ast.AsyncFor | ast.While):
            in_loop = True
        if isinstance(node, ast.ExceptHandler):
            if in_loop and not in_handler and func_name.endswith("_impl") and _handler_is_silent(node):
                violations.append((relpath, func_name, node.lineno))
            in_handler = True
        for child in ast.iter_child_nodes(node):
            visit(child, func_name, in_loop, in_handler)

    visit(tree, "<module>", False, False)
    return violations


def _scan_all() -> list[tuple[str, str, int]]:
    violations: list[tuple[str, str, int]] = []
    for tree, relpath in iter_module_trees(SCAN_DIRS):
        violations.extend(find_silent_loop_handlers(tree, relpath))
    return violations


KNOWN_BAD_SNIPPETS = {
    "log-only-fallthrough": (
        "async def _foo_impl(req):\n"
        "    for item in req.items:\n"
        "        try:\n"
        "            results.append(process(item))\n"
        "        except Exception as e:\n"
        "            logger.error(f'failed {item}: {e}')\n"
    ),
    "log-and-continue": (
        "def _bar_impl(req):\n"
        "    for item in req.items:\n"
        "        try:\n"
        "            results.append(process(item))\n"
        "        except ValueError as e:\n"
        "            logger.warning('skipping %s', item)\n"
        "            continue\n"
    ),
    "bare-pass": (
        "def _baz_impl(req):\n"
        "    while req.pending:\n"
        "        try:\n"
        "            step(req)\n"
        "        except Exception:\n"
        "            pass\n"
    ),
    # salesagent-zm5l: list_creatives substituted a CreativeStatus for an unparseable
    # stored one, so the item DID reach the response — carrying a lifecycle claim the
    # seller never made. The fix logs AND appends a CONFIGURATION_ERROR advisory; this
    # snippet is the fix with the append removed, which must stay flagged or nothing
    # pins the advisory.
    "log-and-fallback-without-advisory": (
        "def _qux_impl(req):\n"
        "    for row in req.rows:\n"
        "        try:\n"
        "            status = Status(row.status)\n"
        "        except ValueError:\n"
        "            logger.warning('unreadable status %r on %s', row.status, row.id)\n"
        "            status = Status.processing\n"
        "        results.append(build(row, status))\n"
    ),
}

KNOWN_GOOD_SNIPPETS = {
    "appends-advisory": (
        "def _ok_impl(req):\n"
        "    for item in req.items:\n"
        "        try:\n"
        "            results.append(process(item))\n"
        "        except Exception as e:\n"
        "            errors.append(Error(code='SERVICE_UNAVAILABLE', message=str(e)))\n"
        "            continue\n"
    ),
    "reraises": (
        "def _ok2_impl(req):\n"
        "    for item in req.items:\n"
        "        try:\n"
        "            results.append(process(item))\n"
        "        except AdCPError:\n"
        "            raise\n"
    ),
    # A SILENT, deliberate default. Add a log line and it becomes
    # "log-and-fallback-without-advisory" above, which is BAD — that pair is what
    # keeps the salesagent-zm5l advisory pinned.
    "fallback-assignment": (
        "def _ok3_impl(req):\n"
        "    for item in req.items:\n"
        "        try:\n"
        "            status = parse(item.status)\n"
        "        except ValueError:\n"
        "            status = 'pending_review'\n"
        "        results.append(build(item, status))\n"
    ),
    # The salesagent-zm5l fix itself: assigns a placeholder, logs, AND surfaces an
    # advisory the buyer reads. The append is what makes it good.
    "log-and-fallback-with-advisory": (
        "def _ok5_impl(req):\n"
        "    for row in req.rows:\n"
        "        try:\n"
        "            status = Status(row.status)\n"
        "        except ValueError:\n"
        "            logger.warning('unreadable status %r on %s', row.status, row.id)\n"
        "            advisories.append(Error(code='CONFIGURATION_ERROR', message=row.id))\n"
        "            status = Status.processing\n"
        "        results.append(build(row, status))\n"
    ),
    "cleanup-inside-handler-exempt": (
        "def _ok4_impl(req):\n"
        "    for item in req.items:\n"
        "        try:\n"
        "            results.append(process(item))\n"
        "        except Exception as e:\n"
        "            try:\n"
        "                audit(e)\n"
        "            except Exception as audit_err:\n"
        "                logger.error('audit failed: %s', audit_err)\n"
        "            errors.append(Error(code='SERVICE_UNAVAILABLE', message=str(e)))\n"
    ),
    "non-impl-function-out-of-scope": (
        "def helper(items):\n"
        "    for item in items:\n"
        "        try:\n"
        "            step(item)\n"
        "        except Exception:\n"
        "            pass\n"
    ),
}


class TestNoSilentLoopFailuresInImpl:
    """Per-item failures in _impl response loops must be surfaced."""

    @pytest.mark.arch_guard
    def test_no_new_silent_loop_handlers(self):
        """No _impl loop handler swallows a per-item failure outside the allowlist."""
        found = _scan_all()
        keys = _ordinal_keys(found)
        new = [
            (f, fn, line)
            for (f, fn, line), key in zip(found, keys, strict=True)
            if key not in SILENT_LOOP_HANDLER_ALLOWLIST
        ]
        assert not new, format_failure(
            summary=(
                f"Found {len(new)} except handler(s) in _impl loops that swallow "
                "per-item failures without surfacing them:"
            ),
            violations=[f"{f}:{line}: in {fn}" for f, fn, line in new],
            fix_hint=FIX_HINT,
            docs_link="CLAUDE.md § No Quiet Failures",
        )

    @pytest.mark.arch_guard
    def test_allowlist_entries_still_exist(self):
        """Every allowlisted violation must still exist (stale-entry detection)."""
        assert_violations_match_allowlist(
            set(_ordinal_keys(_scan_all())),
            SILENT_LOOP_HANDLER_ALLOWLIST,
            fix_hint=FIX_HINT,
        )

    @pytest.mark.arch_guard
    def test_detector_catches_known_bad_snippets(self):
        """Detector self-test: known-bad shapes must be flagged."""
        assert_detector_catches_ast_snippets(
            lambda tree: [line for _, _, line in find_silent_loop_handlers(tree, "<snippet>")],
            snippets=KNOWN_BAD_SNIPPETS,
        )

    @pytest.mark.arch_guard
    def test_detector_passes_known_good_snippets(self):
        """Detector self-test: surfaced/fallback/exempt shapes must NOT be flagged."""
        false_positives = []
        for label, source in KNOWN_GOOD_SNIPPETS.items():
            tree = ast.parse(source, filename=f"<known-good:{label}>")
            if find_silent_loop_handlers(tree, "<snippet>"):
                false_positives.append(label)
        assert not false_positives, "Detector flagged known-good snippet(s):\n" + "\n".join(
            f"  {s}" for s in false_positives
        )


# ══════════════════════════════════════════════════════════════════════════════
# Straight-line degradation handlers (salesagent-3xmz B6)
# ══════════════════════════════════════════════════════════════════════════════
#
# The detector above only sees handlers INSIDE a for/while loop, and it exempts
# "handlers that assign a fallback value and let iteration proceed". Both
# exclusions are right for the per-item case, and both make it structurally blind
# to the OTHER shape of the same disease: a straight-line `try/except` around a
# whole lookup that logs, falls through to a placeholder, and returns a silently
# degraded response.
#
# That is what `_get_adcp_capabilities_impl` did at five sites before B5 — the
# buyer could not tell "this seller has none" from "the lookup failed".
#
# Keyed STRUCTURALLY, not by function name: an `*_impl` that builds an advisory
# list and passes it to a response `errors=` argument has opted into surfacing
# degradations, so every straight-line handler in it must append to that list. A
# rule keyed to the literal name `_get_adcp_capabilities_impl` would die silently
# on a rename or a helper extraction — which the B5 "extract ONE helper" step
# makes likely. Keying on the advisory list means the guard follows the pattern,
# not the identifier.
#
# Deliberately NARROW: functions with no advisory list are not in scope, so
# products.py (which degrades but builds no advisory list) stays invisible here.
# Widening the detector to those is the scope widening below, which surfaces that
# group all at once.
#
# "Advisory list" means a CONTAINER this function holds and a handler can reach —
# see `_advisory_list_names`. `_create_media_buy_impl` derives its advisories from
# the request (`errors=property_list_unsupported_advisories(req.packages, adapter)`)
# and holds no container, so it is out of scope and no longer allowlisted. Its
# best-effort Slack / activity-feed / audit-log handlers were never this guard's
# business; the per-item row in SILENT_LOOP_HANDLER_ALLOWLIST above still covers
# the one loop-shaped swallow it does have.

_ADVISORY_FIX_HINT_HAS_LIST = (
    "This _impl builds an advisory list passed to a response errors= argument, so "
    "every straight-line except handler in it must append to that list (see "
    "_record_degradation in src/core/tools/capabilities.py). Log-and-fall-through "
    "leaves the buyer with a silently degraded response. If the handler genuinely "
    "needs no advisory, raise instead, or allowlist it with a FIXME(#gh-issue)."
)

_ADVISORY_FIX_HINT_NO_LIST = (
    "This _impl returns a response type carrying errors[], but builds no advisory "
    "container — so there is no list to append to and 'append to that list' is not "
    "the fix. Either (a) resolve it AT THE SOURCE with a `# structural-guard:` "
    "comment in the handler body citing the pinned AdCP section that makes dropping "
    "this content conformant — quote the schema/storyboard, do not merely assert it "
    "(see the handlers in _get_products_impl); or (b) if the pin DOES require "
    "surfacing, build the advisory container and pass it to errors= the way "
    "_get_media_buy_delivery_impl does. Allowlist entries only shrink."
)


def _advisory_fix_hint(violations: list[tuple[str, str, int]]) -> str:
    """Pick the hint that is actually followable for these violations.

    A function with no advisory container cannot 'append to that list' — telling an
    implementer to do so sends them to write a container the response never reads.
    """
    for relpath, func_name, _ in violations:
        tree = parse_module(REPO_ROOT / relpath)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == func_name:
                if not _advisory_list_names(node):
                    return _ADVISORY_FIX_HINT_NO_LIST
    return _ADVISORY_FIX_HINT_HAS_LIST


# Straight-line handlers that log without surfacing, in _impl functions that DO
# build an advisory list. This is the NEW guard's initial baseline (the same way
# SILENT_LOOP_HANDLER_ALLOWLIST above was seeded), not allowlist growth: these
# predate the guard. Shrink-only from here.
#
# `_get_adcp_capabilities_impl` is deliberately ABSENT — salesagent-3xmz B5 fixed
# all five of its sites, and that absence is what makes this guard a pin on the
# fix rather than a description of it.
SILENT_ADVISORY_HANDLER_ALLOWLIST: set[tuple[str, str, int]] = {
    # FIXME(#1566): exactly TWO handlers, both response-degrading and both with a
    # FIXME at the source: creative_formats.py:297 drops the adapter's formats and
    # :487 drops the creative-agent referrals, each with nothing but a log line, so
    # the buyer reads "this seller has none" off a failed lookup. Same disease as
    # the loop-shaped row already allowlisted for this function above.
    # They are now TWO entries (ordinals 0 and 1) rather than one (file, function)
    # key covering both: a single key made a third violation in this function
    # invisible — see `_ordinal_keys`.
    #
    # (The other two handlers in this function — the :225 event-loop retry and the
    # :447 cursor reset — are silent fallbacks the detector now exempts, and
    # `_create_media_buy_impl` is gone from this list entirely: it has no advisory
    # container at all, so it was never in scope. See the two notes below.)
    ("src/core/tools/creative_formats.py", "_list_creative_formats_impl", 0),
    ("src/core/tools/creative_formats.py", "_list_creative_formats_impl", 1),
}


def _advisory_list_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Local names that ARE the advisory container passed to a response ``errors=``.

    Matches the direct form (``errors=advisories``, ``errors=agent_errors if
    agent_errors else None``) and the normalized form
    (``errors=normalize_advisory_errors(advisories) or None``), since the
    normalizer is the sanctioned wrapper around the container.

    Two filters keep this to the CONTAINER rather than every name in the
    expression. Harvesting every ``ast.Name`` (the first cut of this detector)
    read ``errors=property_list_unsupported_advisories(req.packages, adapter)`` in
    ``_create_media_buy_impl`` as three advisory lists — ``req``, ``adapter`` and
    the callee. That function computes its advisories from the REQUEST and holds
    no container at all, so the guard's own fix hint ("append to that list") was
    impossible to follow there; worse, treating ``req``/``adapter`` as advisory
    names made every handler that merely passes one of them to a call score as
    "surfacing" — a false NEGATIVE. So:

    - a name must be *bound in this function* and not a parameter, and
    - a name reached only as a call argument counts only in the wrapper position
      (first positional arg of the call producing the value); a helper that
      DERIVES advisories from other inputs contributes no container.

    Strictly narrower than harvesting every name, so it can only remove functions
    from scope, never add them.
    """
    bound = {n.id for n in ast.walk(func) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    params = {a.arg for a in (*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs)}
    if func.args.vararg:
        params.add(func.args.vararg.arg)
    if func.args.kwarg:
        params.add(func.args.kwarg.arg)
    local_names = bound - params

    candidates: set[str] = set()
    for node in iter_call_expressions(func):
        for kw in node.keywords:
            if kw.arg != "errors" or kw.value is None:
                continue
            candidates.update(_container_candidates(kw.value))
    return candidates & local_names


def _container_candidates(value: ast.expr) -> set[str]:
    """Names in an ``errors=`` value that could be the advisory container itself."""
    disqualified: set[str] = set()
    for call in iter_call_expressions(value):
        # the callee is the wrapper, never the container
        disqualified.update(_names_in(call.func))
        for pos, arg in enumerate(call.args):
            if pos == 0 and isinstance(arg, ast.Name):
                continue  # normalize_advisory_errors(advisories) — the wrapper's subject
            disqualified.update(_names_in(arg))
        for kw in call.keywords:
            disqualified.update(_names_in(kw.value))
    return _names_in(value) - disqualified


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _handler_appends_to(handler: ast.ExceptHandler, names: set[str]) -> bool:
    """True when the handler surfaces via one of *names*, directly or via a helper.

    A call passing an advisory-list name as an argument counts: the B5 fix routes
    all five sites through ``_record_degradation(advisories, ...)``, and requiring
    a literal ``advisories.append`` would punish exactly the DRY extraction the
    disease scan asked for.
    """
    if any(isinstance(n, ast.Raise) for n in ast.walk(handler)):
        return True
    for node in iter_call_expressions(handler):
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"append", "extend"}
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in names
        ):
            return True
        if any(isinstance(a, ast.Name) and a.id in names for a in node.args):
            return True
    return False


def _handler_is_exempt_shape(handler: ast.ExceptHandler) -> bool:
    """True for the two non-swallowing shapes the sibling loop detector exempts.

    ``_handler_is_silent`` above flags a loop handler only when its body is
    *solely* expression/``pass`` statements, so a handler that RETURNS or that
    ASSIGNS a fallback is already exempt there. Both shapes reach this detector
    too and were false positives until now:

    - ``return <helper>``: control leaves the function — the failure is resolved
      or re-raised downstream, never absorbed into a degraded response
      (``except IntegrityError: return _resolve_idempotency_race_or_raise(...)``).
    - a SILENT fallback assignment: the handler substitutes a default and moves on
      (the ``except RuntimeError:`` event-loop retry and the ``except ValueError:
      start_index = 0`` cursor reset in ``creative_formats``).

    The fallback exemption is deliberately NARROWER than the sibling's. A
    fallback assignment is structurally indistinguishable from the placeholder
    degradation salesagent-3xmz B5 fixed in ``capabilities`` (``except: channels =
    [DEFAULT]`` leaves the buyer unable to tell "none" from "lookup failed"), so
    exempting it wholesale would un-pin that fix. A handler that LOGS is admitting
    something went wrong and must still surface it; only the silent, deliberate
    default is exempt.
    """
    if any(isinstance(n, ast.Return) for n in ast.walk(handler)):
        return True
    logs = any(
        isinstance(n.func, ast.Attribute) and n.func.attr in _LOG_METHODS for n in iter_call_expressions(handler)
    )
    assigns = any(isinstance(n, ast.Assign | ast.AnnAssign | ast.AugAssign) for n in ast.walk(handler))
    return assigns and not logs


def _returns_response_with_errors(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when the function's return annotation names a response carrying ``errors[]``.

    DELIBERATELY SHALLOW: reads ``errors`` off the annotated type's
    OWN model fields and never follows a nested response field. ``CreateMediaBuyResult``
    has fields {replayed, response, status} — no ``errors`` — but it WRAPS a response
    that has one. Following the wrapper would pull in six best-effort Slack /
    activity-feed / audit-log handlers in ``_create_media_buy_impl`` whose failure
    leaves the buyer's response byte-identical, i.e. six false positives.
    ``test_scope_predicate_is_not_transitive`` pins this.

    Resolved against the live schema classes rather than by name-matching, so a
    response that gains or loses ``errors[]`` changes this guard's scope automatically.
    """
    annotation = func.returns
    if annotation is None:
        return False

    # Unwrap the annotation to bare names: `X`, `X | None`, `Awaitable[X]`.
    candidates: list[str] = []
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name):
            candidates.append(node.id)
        elif isinstance(node, ast.Attribute):
            candidates.append(node.attr)
    if not candidates:
        return False

    from src.core import schemas

    for name in candidates:
        model = getattr(schemas, name, None)
        fields = getattr(model, "model_fields", None)
        if fields and "errors" in fields:
            return True
    return False


def find_silent_advisory_handlers(tree: ast.Module, relpath: str) -> list[tuple[str, str, int]]:
    """Return (relpath, function_name, lineno) for straight-line handlers that
    fail to surface a degradation in an advisory-emitting ``*_impl``."""
    violations: list[tuple[str, str, int]] = []

    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not func.name.endswith("_impl"):
            continue
        names = _advisory_list_names(func)
        if not names and not _returns_response_with_errors(func):
            # Neither an advisory-emitting _impl nor one returning a response that
            # carries errors[] — out of scope (widened from the
            # first condition alone, which left _get_products_impl invisible).
            continue

        def visit(
            node: ast.AST,
            in_loop: bool,
            in_handler: bool,
            _names: set[str] = names,
            _fname: str = func.name,
        ) -> None:
            if isinstance(node, ast.For | ast.AsyncFor | ast.While):
                in_loop = True
            if isinstance(node, ast.ExceptHandler):
                # loop handlers belong to the per-item detector above; nested
                # handlers are best-effort cleanup, exempt by the same rule
                if (
                    not in_loop
                    and not in_handler
                    and not _handler_appends_to(node, _names)
                    and not _handler_is_exempt_shape(node)
                ):
                    violations.append((relpath, _fname, node.lineno))
                in_handler = True
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue  # nested function: its own scope
                visit(child, in_loop, in_handler)

        visit(func, False, False)

    return violations


_SOURCE_SKIP_MARKER = "# structural-guard:"


def _scan_all_advisory() -> list[tuple[str, str, int]]:
    """Straight-line violations across SCAN_DIRS, minus source-resolved sites.

    The ``# structural-guard:`` filter lives HERE and not in
    ``find_silent_advisory_handlers`` because only the scan has a real file to read:
    the detector self-tests call the finder with a synthetic ``"<snippet>"`` path.
    The detector reports STRUCTURE; the scan applies SOURCE-level resolutions.
    Same marker and same line-span check as
    test_architecture_no_error_construction_in_impl.py:40,75.
    """
    found: list[tuple[str, str, int]] = []
    for tree, relpath in iter_module_trees(SCAN_DIRS):
        violations = find_silent_advisory_handlers(tree, relpath)
        if not violations:
            continue
        source_lines = (REPO_ROOT / relpath).read_text().splitlines()
        handlers = {h.lineno: h for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler)}
        for violation in violations:
            handler = handlers.get(violation[2])
            if handler is not None:
                start = handler.lineno - 1
                end = handler.end_lineno or handler.lineno
                if any(_SOURCE_SKIP_MARKER in line for line in source_lines[start:end]):
                    continue  # resolved at the source with a cited justification
            found.append(violation)
    return found


_ADVISORY_BAD = {
    "log_only_in_advisory_impl": (
        "def _thing_impl():\n"
        "    advisories = []\n"
        "    try:\n"
        "        x = lookup()\n"
        "    except Exception as e:\n"
        "        logger.warning(e)\n"
        "    return Response(errors=advisories or None)\n"
    ),
    # The salesagent-3xmz B5 shape: log, substitute a placeholder, hand the buyer a
    # response that cannot be told apart from "this seller genuinely has none". The
    # log line is what separates it from the exempt silent-default shape below.
    "log_and_fallback_without_advisory": (
        "def _thing_impl():\n"
        "    advisories = []\n"
        "    try:\n"
        "        channels = lookup()\n"
        "    except Exception as e:\n"
        "        logger.warning('channel lookup failed: %s', e)\n"
        "        channels = [DEFAULT]\n"
        "    return Response(errors=normalize(advisories))\n"
    ),
}

_ADVISORY_GOOD = {
    "appends_directly": (
        "def _thing_impl():\n"
        "    advisories = []\n"
        "    try:\n"
        "        x = lookup()\n"
        "    except Exception as e:\n"
        "        advisories.append(Err(code='SERVICE_UNAVAILABLE', message=str(e)))\n"
        "    return Response(errors=advisories or None)\n"
    ),
    # would-be-missed: the DRY extraction the disease scan asked for. A detector
    # requiring a literal `advisories.append` would flag this CORRECT code.
    "appends_via_helper": (
        "def _thing_impl():\n"
        "    advisories = []\n"
        "    try:\n"
        "        x = lookup()\n"
        "    except Exception as e:\n"
        "        _record_degradation(advisories, 'thing', e)\n"
        "    return Response(errors=normalize(advisories) or None)\n"
    ),
    "no_advisory_list_is_out_of_scope": (
        "def _other_impl():\n"
        "    try:\n"
        "        x = lookup()\n"
        "    except Exception as e:\n"
        "        logger.warning(e)\n"
        "    return Response()\n"
    ),
    "raises_instead": (
        "def _thing_impl():\n"
        "    advisories = []\n"
        "    try:\n"
        "        x = lookup()\n"
        "    except Exception as e:\n"
        "        raise AdCPError(str(e)) from e\n"
        "    return Response(errors=advisories or None)\n"
    ),
    # shape (a): control leaves the function — the helper resolves the race or raises
    "returns_helper_that_raises": (
        "def _thing_impl():\n"
        "    advisories = []\n"
        "    try:\n"
        "        x = persist()\n"
        "    except IntegrityError as exc:\n"
        "        return _resolve_idempotency_race_or_raise(exc, tenant_id)\n"
        "    return Response(errors=advisories or None)\n"
    ),
    # shape (b): a deliberate, SILENT default — the cursor reset / event-loop retry.
    # Add a log line and it becomes `log_and_fallback_without_advisory` above, which
    # stays BAD: that is what keeps the B5 placeholder fix pinned.
    "silent_fallback_default": (
        "def _thing_impl():\n"
        "    advisories = []\n"
        "    try:\n"
        "        start_index = int(decode(cursor))\n"
        "    except ValueError:\n"
        "        start_index = 0\n"
        "    return Response(errors=advisories or None)\n"
    ),
    # advisories DERIVED from the request, no container the handler could reach:
    # the guard's fix hint is unfollowable here, so the function is out of scope.
    "derived_advisories_have_no_container": (
        "def _thing_impl(req):\n"
        "    adapter = get_adapter(req)\n"
        "    try:\n"
        "        notify(req)\n"
        "    except Exception as e:\n"
        "        logger.warning('notify failed: %s', e)\n"
        "    return Response(errors=unsupported_advisories(req.packages, adapter))\n"
    ),
}


class TestNoSilentAdvisoryHandlersInImpl:
    """Straight-line degradations in advisory-emitting _impls must be surfaced."""

    @pytest.mark.arch_guard
    def test_no_new_silent_advisory_handlers(self):
        found = _scan_all_advisory()
        keys = _ordinal_keys(found)
        new = [
            (f, fn, line)
            for (f, fn, line), key in zip(found, keys, strict=True)
            if key not in SILENT_ADVISORY_HANDLER_ALLOWLIST
        ]
        assert not new, format_failure(
            summary=(
                f"Found {len(new)} straight-line except handler(s) in advisory-emitting "
                "_impl functions that degrade the response without surfacing it:"
            ),
            violations=[f"{f}:{line}: in {fn}" for f, fn, line in new],
            fix_hint=_advisory_fix_hint(new),
            docs_link="CLAUDE.md § No Quiet Failures",
        )

    @pytest.mark.arch_guard
    def test_advisory_allowlist_entries_still_exist(self):
        assert_violations_match_allowlist(
            set(_ordinal_keys(_scan_all_advisory())),
            SILENT_ADVISORY_HANDLER_ALLOWLIST,
            fix_hint=_advisory_fix_hint(_scan_all_advisory()),
        )

    @pytest.mark.arch_guard
    def test_advisory_detector_catches_known_bad(self):
        assert_detector_catches_ast_snippets(
            lambda tree: [line for _, _, line in find_silent_advisory_handlers(tree, "<snippet>")],
            snippets=_ADVISORY_BAD,
        )

    @pytest.mark.arch_guard
    def test_advisory_detector_passes_known_good(self):
        false_positives = []
        for label, source in _ADVISORY_GOOD.items():
            tree = ast.parse(source, filename=f"<known-good:{label}>")
            if find_silent_advisory_handlers(tree, f"<known-good:{label}>"):
                false_positives.append(label)
        assert not false_positives, f"detector flagged correct shapes: {false_positives}"


# ══════════════════════════════════════════════════════════════════════════════
# Scope widening: response types that carry errors[]
# ══════════════════════════════════════════════════════════════════════════════
#
# The straight-line rule above is in scope only for an `*_impl` that BUILDS an
# advisory container (`_advisory_list_names`). `_get_products_impl` builds none,
# so its five straight-line degradations are invisible to both rules today even
# though `GetProductsResponse` carries `errors[]` — the exact blind spot the
# comment at :272 flags as the remaining blind spot.
#
# The widened predicate is: in scope if the function builds an advisory container
# OR its RETURN ANNOTATION names a response type carrying `errors[]`. It must stay
# SHALLOW. `_create_media_buy_impl` returns `CreateMediaBuyResult`, whose fields
# are {replayed, response, status} — no `errors` — but it WRAPS a response that
# has one. A transitive predicate pulls in six best-effort Slack / activity-feed /
# audit-log handlers whose failure leaves the response byte-identical.
#
# Resolution of a newly-in-scope site is at the SOURCE with a `# structural-guard:`
# marker carrying the spec citation (the mechanism already used at
# src/core/exceptions.py:277 and src/core/tools/accounts.py:674), never by growing
# an allowlist. That marker check therefore belongs in `_scan_all_advisory`, which
# has the file to read — NOT in `find_silent_advisory_handlers`, which the
# detector self-tests call with a synthetic `"<snippet>"` path and no file behind
# it. The detector reports structure; the scan applies source-level resolutions.

_SKIP_MARKER = "# structural-guard:"  # same marker as test_architecture_no_error_construction_in_impl.py:40

_PRODUCTS_REL = "src/core/tools/products.py"

# The five straight-line degradations in `_get_products_impl`, identified by a
# distinctive substring of the handler BODY rather than a line number: the
# same change also deletes the dead `policy_disabled_reason` variable in
# the first handler, which shifts every line below it. Order is source order.
_PRODUCTS_DEGRADATION_SUBJECTS = (
    "Policy check failed for tenant",  # policy service unreachable -> fail open
    "Failed to generate dynamic product variants",  # variants dropped
    "Failed to enrich products with dynamic pricing",  # pricing enrichment dropped
    "Failed to apply AI product ranking",  # ranking dropped (superset still returned)
    "Failed to annotate pricing options with adapter support",  # non-spec extension dropped
)

_MEDIA_BUY_CREATE_REL = "src/core/tools/media_buy_create.py"

# Verified with the scope predicate forced open: the benign handlers a TRANSITIVE
# predicate would drag in through `CreateMediaBuyResult.response`. Line numbers are
# for the failure message only — the assertion is on the whole file.
_TRANSITIVE_HAZARD_LINES = (2797, 2905, 2925, 3229, 4142, 4224)


def _handlers_by_lineno(tree: ast.Module) -> dict[int, ast.ExceptHandler]:
    return {h.lineno: h for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler)}


def _handler_source(handler: ast.ExceptHandler, source_lines: list[str]) -> str:
    end = handler.end_lineno or handler.lineno
    return "\n".join(source_lines[handler.lineno - 1 : end])


def _module_source_lines(relpath: str) -> list[str]:
    return (REPO_ROOT / relpath).read_text().splitlines()


def _flagged_products_handlers() -> tuple[list[tuple[str, str, int]], dict[int, ast.ExceptHandler], list[str]]:
    """Straight-line handlers the detector reports in products.py, with their source."""
    tree = parse_module(REPO_ROOT / _PRODUCTS_REL)
    return (
        find_silent_advisory_handlers(tree, _PRODUCTS_REL),
        _handlers_by_lineno(tree),
        _module_source_lines(_PRODUCTS_REL),
    )


_SCOPE_FIX_HINT = (
    "Widen the scope predicate in find_silent_advisory_handlers (the "
    "`if not names: continue` at the top of the function loop): a function is in "
    "scope when it builds an advisory container OR its return annotation names a "
    "response type carrying errors[]. Keep it SHALLOW — a response type that merely "
    "wraps another response is NOT in scope."
)

_MARKER_FIX_HINT = (
    f"Resolve each newly-in-scope handler AT THE SOURCE with a `{_SKIP_MARKER}` comment "
    "in the handler body, citing the pinned AdCP section that makes dropping this "
    "content conformant (or migrate it to the advisory pattern). Do NOT add allowlist "
    "entries — SILENT_ADVISORY_HANDLER_ALLOWLIST only shrinks."
)


class TestStraightLineScopeCoversResponseErrors:
    """`_impl`s returning a response with errors[] are in scope, shallowly."""

    @pytest.mark.arch_guard
    def test_products_straight_line_degradations_are_in_scope(self):
        """All five `_get_products_impl` degradations are seen by the straight-line rule.

        `GetProductsResponse` carries errors[], so every straight-line handler in
        `_get_products_impl` that drops content from it is this guard's business —
        whether or not the function happens to build an advisory container.
        """
        found, handlers, source = _flagged_products_handlers()
        subjects = [
            next(
                (s for s in _PRODUCTS_DEGRADATION_SUBJECTS if s in _handler_source(handlers[line], source)),
                f"<unrecognized handler at {_PRODUCTS_REL}:{line}>",
            )
            for _, _, line in found
        ]
        assert subjects == list(_PRODUCTS_DEGRADATION_SUBJECTS), format_failure(
            summary=(
                f"The straight-line rule sees {len(found)} handler(s) in {_PRODUCTS_REL}, "
                f"expected the {len(_PRODUCTS_DEGRADATION_SUBJECTS)} known degradations:"
            ),
            violations=[f"saw: {s}" for s in subjects] or ["saw: nothing — products.py is out of scope"],
            fix_hint=_SCOPE_FIX_HINT,
            docs_link="CLAUDE.md § No Quiet Failures",
        )
        assert {fn for _, fn, _ in found} == {"_get_products_impl"}, (
            f"expected every flagged products.py handler in _get_products_impl, got {sorted({fn for _, fn, _ in found})}"
        )

    @pytest.mark.arch_guard
    def test_newly_in_scope_products_handlers_are_resolved_at_source(self):
        """Every in-scope products.py handler carries a `# structural-guard:` marker.

        Resolution is at the source with a spec citation, not by allowlist growth.
        A handler that instead surfaces the degradation stops being reported at all,
        which also satisfies this test.
        """
        found, handlers, source = _flagged_products_handlers()
        assert found, (
            f"No straight-line handler found in {_PRODUCTS_REL} — the scope predicate has not been "
            f"widened yet, so this test cannot pass vacuously. {_SCOPE_FIX_HINT}"
        )
        unmarked = [
            f"{_PRODUCTS_REL}:{line}: in {fn}"
            for _, fn, line in found
            if _SKIP_MARKER not in _handler_source(handlers[line], source)
        ]
        assert not unmarked, format_failure(
            summary=f"{len(unmarked)} in-scope handler(s) drop response content with no marker and no advisory:",
            violations=unmarked,
            fix_hint=_MARKER_FIX_HINT,
            docs_link="CLAUDE.md § No Quiet Failures",
        )

    @pytest.mark.arch_guard
    def test_scope_predicate_is_not_transitive(self):
        """A response type that only WRAPS an errors[]-carrying response is out of scope.

        `_create_media_buy_impl` returns `CreateMediaBuyResult` — fields {replayed,
        response, status}, no `errors`. Its straight-line handlers are Slack
        notifications, activity-feed writes and audit-log writes that run after the
        response payload is determined; the buyer's response is byte-identical whether
        they succeed or fail. A transitive predicate (following `.response` to a type
        that does carry errors[]) would flag all six as silent degradations.
        """
        wrapper_snippet = (
            "def _wrapper_impl(req) -> CreateMediaBuyResult:\n"
            "    try:\n"
            "        notify_slack(req)\n"
            "    except Exception as e:\n"
            "        logger.warning('slack notify failed: %s', e)\n"
            "    return CreateMediaBuyResult(status='ok', response=inner, replayed=False)\n"
        )
        tree = ast.parse(wrapper_snippet, filename="<wraps-errors-response>")
        assert not find_silent_advisory_handlers(tree, "<snippet>"), (
            "detector flagged a handler in an _impl returning a wrapper type with no errors[] field"
        )

        flagged = [f"{f}:{line}: in {fn}" for f, fn, line in _scan_all_advisory() if f == _MEDIA_BUY_CREATE_REL]
        assert not flagged, format_failure(
            summary=(
                f"The straight-line rule reached {_MEDIA_BUY_CREATE_REL}, which means the scope "
                "predicate became transitive. These handlers are best-effort side effects that "
                f"leave the response byte-identical (expected at lines {list(_TRANSITIVE_HAZARD_LINES)}):"
            ),
            violations=flagged,
            fix_hint=(
                "Keep the predicate SHALLOW: read `errors` off the annotated response type's own "
                "fields only. Do not follow a wrapper's nested response field."
            ),
            docs_link="CLAUDE.md § No Quiet Failures",
        )


class TestSilentHandlerRulePartition:
    """The two rules partition the handler space — neither allowlist can cover the other."""

    @pytest.mark.arch_guard
    def test_at_most_one_rule_sees_each_handler(self):
        """No handler is reported by both rules.

        AT MOST one, not exactly one: both rules exclude `in_handler`, so a NESTED
        handler is seen by neither (see the test below). Disjointness is carried by
        the `in_loop` flag alone — the loop rule requires it, the straight-line rule
        forbids it. Widening the straight-line rule changes only its FUNCTION-scope
        predicate and never touches `in_loop`, so it cannot create overlap; this test
        is what makes that argument checkable instead of asserted in a comment.
        """
        loop_sites = {(f, line) for f, _, line in _scan_all()}
        straight_sites = {(f, line) for f, _, line in _scan_all_advisory()}
        assert loop_sites and straight_sites, (
            "both rules must report at least one site for this partition check to mean anything; "
            f"loop={sorted(loop_sites)} straight-line={sorted(straight_sites)}"
        )
        overlap = loop_sites & straight_sites
        assert not overlap, format_failure(
            summary=f"{len(overlap)} handler(s) reported by BOTH rules — the allowlists now overlap:",
            violations=[f"{f}:{line}" for f, line in sorted(overlap)],
            fix_hint=(
                "The loop rule requires in_loop, the straight-line rule forbids it. If a handler "
                "reaches both, the in_loop flag was changed — revert that, do not allowlist twice."
            ),
            docs_link="CLAUDE.md § No Quiet Failures",
        )

    @pytest.mark.arch_guard
    def test_nested_handlers_are_out_of_scope_by_design(self):
        """Handlers nested inside another handler are seen by neither rule.

        Best-effort cleanup inside a failure path (the audit-log write inside the
        SERVICE_UNAVAILABLE handler of `_get_media_buy_delivery_impl`) is exempt in
        both rules. That is the reason the partition is "at most one" and not
        "exactly one", so the live instance is pinned rather than left implicit.
        """
        relpath = "src/core/tools/media_buy_delivery.py"
        tree = parse_module(REPO_ROOT / relpath)
        nested = [
            inner
            for outer in ast.walk(tree)
            if isinstance(outer, ast.ExceptHandler)
            for inner in ast.walk(outer)
            if isinstance(inner, ast.ExceptHandler) and inner is not outer
        ]
        assert any(h.name == "audit_err" for h in nested), (
            f"the nested audit-log handler in {relpath} is gone — re-point this pin at a live "
            "nested handler rather than deleting it"
        )
        seen = {line for _, _, line in find_silent_loop_handlers(tree, relpath)} | {
            line for _, _, line in find_silent_advisory_handlers(tree, relpath)
        }
        reported = sorted({h.lineno for h in nested} & seen)
        assert not reported, format_failure(
            summary=f"{len(reported)} nested handler(s) reported — nested cleanup is exempt in both rules:",
            violations=[f"{relpath}:{line}" for line in reported],
            fix_hint="Both rules must keep excluding handlers reached with in_handler=True.",
            docs_link="CLAUDE.md § No Quiet Failures",
        )
