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

Detection (AST): inside functions named ``*_impl`` under ``src/core/tools/``,
an ``except`` handler that sits directly in a ``for``/``while`` loop (not
nested inside another handler — best-effort cleanup like audit-log writes is
exempt) is a violation when it:

- contains no ``raise``, AND
- calls no ``.append(...)`` / ``.extend(...)`` / ``.add(...)``, AND
- either contains ``continue`` (item explicitly skipped) or consists solely
  of expression/``pass`` statements (log-only fall-through).

Handlers that assign a fallback value and let the iteration proceed are fine —
the item still reaches the response.

Allowlist can only SHRINK. Every entry has a FIXME(#gh-issue) at the source.
"""

import ast

import pytest

from tests.unit._architecture_helpers import (
    REPO_ROOT,
    assert_detector_catches_ast_snippets,
    assert_violations_match_allowlist,
    format_failure,
    iter_module_trees,
)

SCAN_DIRS = [REPO_ROOT / "src/core/tools"]

# Pre-existing violations, keyed (repo-relative file, enclosing function).
# Each has a FIXME(#gh-issue) comment at the source. Shrink-only.
SILENT_LOOP_HANDLER_ALLOWLIST: set[tuple[str, str]] = {
    # FIXME(#1566): unparseable Broadstreet template dropped from formats silently
    ("src/core/tools/creative_formats.py", "_list_creative_formats_impl"),
    # FIXME(#1566): creative-association failure logged only, absent from response
    ("src/core/tools/media_buy_create.py", "_create_media_buy_impl"),
}

FIX_HINT = (
    "Surface the failure: append an advisory Error to the response errors[] list "
    "(see the SERVICE_UNAVAILABLE handler in _get_media_buy_delivery_impl), raise an "
    "AdCPError, or assign a fallback the response can carry. If the swallow is "
    "genuinely correct, allowlist it with a FIXME(#gh-issue) at the source."
)


def _handler_is_silent(handler: ast.ExceptHandler) -> bool:
    """True when the handler swallows the failure without surfacing it.

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
    log_only = all(isinstance(stmt, ast.Expr | ast.Pass) for stmt in handler.body)
    return has_continue or log_only


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
    "fallback-assignment": (
        "def _ok3_impl(req):\n"
        "    for item in req.items:\n"
        "        try:\n"
        "            status = parse(item.status)\n"
        "        except ValueError:\n"
        "            status = 'pending_review'\n"
        "        results.append(build(item, status))\n"
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
        new = [(f, fn, line) for f, fn, line in found if (f, fn) not in SILENT_LOOP_HANDLER_ALLOWLIST]
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
        found_keys = {(f, fn) for f, fn, _ in _scan_all()}
        assert_violations_match_allowlist(
            found_keys,
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
