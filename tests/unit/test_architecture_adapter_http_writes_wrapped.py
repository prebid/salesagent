"""Structural guard: every adapter HTTP write surfaces a typed error, never a raw one.

This is the dual of ``test_architecture_adapter_raise_site_coverage.py``. That
guard checks that every *explicit* ``raise AdCP*Error(...)`` in an adapter has a
raise-site test — but it is blind to the *absence* of a raise: a bare
``requests.<verb>(...).raise_for_status()`` has no ``raise`` at all. On an
ad-server outage that bare call emits a ``requests`` ``HTTPError``, which escapes
the adapter to the transport boundary, where ``normalize_to_adcp_error`` wraps it
in the base ``AdCPError`` -> ``INTERNAL_ERROR`` / recovery ``terminal``. Per the
AdCP recovery taxonomy the buyer agent then escalates to a human instead of
retrying. The sibling write paths wrap the same outage as ``AdCPAdapterError``
(``SERVICE_UNAVAILABLE`` / recovery ``transient`` -> retry), so the buyer gets
contradictory recovery instructions for one underlying condition.

This guard makes wrapping mandatory: every ``response.raise_for_status()`` under
``src/adapters/`` must be protected so a transport failure cannot escape the
method as a non-``AdCPError``. A site is protected when it is either:

1. inside a ``with wrap_request_errors():`` block (the shared mapping in
   ``src/adapters/utils/http_errors.py``), or
2. inside a ``try`` whose handlers are all *safe* — each handler either swallows
   the exception (no ``raise`` at all, e.g. a best-effort webhook) or re-raises
   only as a typed ``AdCP*Error``. A bare ``raise`` or a ``raise`` of a
   non-``AdCP`` error leaves the original transport exception free to escape and
   is a violation.

The ALLOWLIST is EMPTY and MUST STAY EMPTY: a new unwrapped adapter HTTP write
fails this guard immediately. Wrap it (prefer ``wrap_request_errors``) instead of
allowlisting it.
"""

from __future__ import annotations

import ast

from tests.unit._architecture_helpers import REPO_ROOT, rel, safe_parse

# Adapter HTTP-write sites that emit a raw (non-AdCPError) transport failure.
# MUST stay empty — wrap the write instead of allowlisting it.
ALLOWLIST: frozenset[str] = frozenset()

# Hand-rolled ``except <RequestException>: raise AdCPAdapterError(str(e))`` re-wraps that
# bypass ``wrap_request_errors`` (and so miss its shared status->recovery mapping, e.g. a
# 4xx wrongly reported transient). MUST stay empty — route the write through
# ``with wrap_request_errors():`` instead. A re-raise that ADDS context (an f-string
# message, e.g. gam_reporting's "Failed to download GAM report") is legitimately enriching
# and is NOT flagged; a handler that swallows (no AdCPAdapterError raise) is graceful
# degradation and is NOT flagged.
HAND_ROLLED_REWRAP_ALLOWLIST: frozenset[str] = frozenset()

_ADAPTERS_DIR = REPO_ROOT / "src/adapters"

# The canonical home of the RequestException -> AdCPAdapterError mapping; its bare
# ``AdCPAdapterError(str(e))`` is the SOURCE, not a hand-rolled copy.
_WRAP_HELPER_FILE = "http_errors.py"

_WRAPPER_NAME = "wrap_request_errors"


def _is_adcp_error_name(name: str) -> bool:
    return name.startswith("AdCP") and name.endswith("Error") and name != "AdCPError"


def _build_parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _enclosing(node: ast.AST, parents: dict[ast.AST, ast.AST], kind: type) -> list[ast.AST]:
    out: list[ast.AST] = []
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, kind):
            out.append(cur)
        cur = parents.get(cur)
    return out


def _with_wraps_request_errors(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """True if ``node`` is inside a ``with wrap_request_errors():`` block."""
    for with_node in _enclosing(node, parents, ast.With):
        for item in with_node.items:  # type: ignore[attr-defined]
            ctx = item.context_expr
            if isinstance(ctx, ast.Call):
                fn = ctx.func
                name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
                if name == _WRAPPER_NAME:
                    return True
    return False


def _handler_is_safe(handler: ast.ExceptHandler) -> bool:
    """A handler is safe if it swallows the exception or re-raises only as a typed AdCP error.

    A bare ``raise`` (re-raises the caught transport exception) or a ``raise`` of
    any non-``AdCP`` error lets the raw exception escape and is unsafe.
    """
    for node in ast.walk(handler):
        if not isinstance(node, ast.Raise):
            continue
        exc = node.exc
        if exc is None:
            return False  # bare ``raise`` re-raises the raw transport exception
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) and _is_adcp_error_name(exc.func.id):
            continue
        return False  # raises something other than a typed AdCP error
    return True  # no raise in the handler -> swallowed


def _try_protects(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """True if some enclosing ``try`` (with ``node`` in its body) has all-safe handlers."""
    for try_node in _enclosing(node, parents, ast.Try):
        # Only count the try if the node is in its body, not in a handler/finally.
        in_body = any(node is descendant for stmt in try_node.body for descendant in ast.walk(stmt))  # type: ignore[attr-defined]
        if not in_body or not try_node.handlers:  # type: ignore[attr-defined]
            continue
        if all(_handler_is_safe(h) for h in try_node.handlers):  # type: ignore[attr-defined]
            return True
    return False


def collect_unwrapped_http_writes() -> list[str]:
    """Return ``path:line`` for every unprotected ``raise_for_status()`` under src/adapters."""
    unwrapped: list[str] = []
    for filepath in sorted(_ADAPTERS_DIR.rglob("*.py")):
        tree = safe_parse(filepath)
        if tree is None:
            continue
        parents = _build_parents(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute) and node.attr == "raise_for_status"):
                continue
            if _with_wraps_request_errors(node, parents) or _try_protects(node, parents):
                continue
            unwrapped.append(f"{rel(filepath)}:{node.lineno}")
    return unwrapped


def _handler_catches_request_exception(handler: ast.ExceptHandler) -> bool:
    """True if the ``except`` catches ``requests`` ``RequestException`` (alone or in a tuple)."""
    exc_type = handler.type
    if exc_type is None:
        return False
    candidates = exc_type.elts if isinstance(exc_type, ast.Tuple) else [exc_type]
    for c in candidates:
        name = c.attr if isinstance(c, ast.Attribute) else (c.id if isinstance(c, ast.Name) else None)
        if name == "RequestException":
            return True
    return False


def _handler_raises_bare_adapter_error(handler: ast.ExceptHandler) -> bool:
    """True if the handler raises ``AdCPAdapterError(str(...))`` — the byte-identical
    ``wrap_request_errors`` body (a bare conversion of the caught exception). A re-raise
    that ADDS context (an f-string / literal message) is legitimately enriching and is
    not flagged."""
    for node in ast.walk(handler):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
            continue
        fn = node.exc.func
        name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
        if name != "AdCPAdapterError" or not node.exc.args:
            continue
        arg0 = node.exc.args[0]
        if isinstance(arg0, ast.Call) and isinstance(arg0.func, ast.Name) and arg0.func.id == "str":
            return True
    return False


def collect_hand_rolled_request_exception_rewraps() -> list[str]:
    """Return ``path:line`` for every adapter ``except <RequestException>`` handler that
    re-raises ``AdCPAdapterError(str(...))`` — the hand-rolled copy of ``wrap_request_errors``.
    Excludes the canonical helper file itself."""
    out: list[str] = []
    for filepath in sorted(_ADAPTERS_DIR.rglob("*.py")):
        if filepath.name == _WRAP_HELPER_FILE:
            continue
        tree = safe_parse(filepath)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ExceptHandler)
                and _handler_catches_request_exception(node)
                and _handler_raises_bare_adapter_error(node)
            ):
                out.append(f"{rel(filepath)}:{node.lineno}")
    return out


class TestAdapterHttpWritesWrapped:
    """Every adapter HTTP write must surface a typed AdCPError, never a raw transport exception."""

    def test_every_adapter_http_write_is_wrapped(self):
        # Self-check: the scan must find raise_for_status sites, or a refactor
        # silently broke the AST walk and this guard passes vacuously.
        sites_seen = 0
        for filepath in sorted(_ADAPTERS_DIR.rglob("*.py")):
            tree = safe_parse(filepath)
            if tree is None:
                continue
            sites_seen += sum(
                1 for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "raise_for_status"
            )
        assert sites_seen > 0, f"no raise_for_status sites found under {_ADAPTERS_DIR} — scan likely broken"

        unwrapped = sorted(set(collect_unwrapped_http_writes()) - ALLOWLIST)
        if unwrapped:
            detail = "\n".join(f"  - {site}" for site in unwrapped)
            raise AssertionError(
                "Adapter HTTP writes emit a raw transport exception (INTERNAL_ERROR / terminal) "
                "instead of a typed AdCPAdapterError (SERVICE_UNAVAILABLE / transient):\n"
                f"{detail}\n\n"
                "Wrap the request in `with wrap_request_errors():` (src/adapters/utils/http_errors.py), "
                "or use a try/except that raises a typed AdCP*Error. Do NOT add to ALLOWLIST."
            )

    def test_allowlist_is_empty(self):
        assert ALLOWLIST == frozenset(), (
            f"ALLOWLIST must be empty but contains {sorted(ALLOWLIST)}. "
            "Wrapping adapter HTTP writes is mandatory: wrap the write instead of allowlisting it."
        )

    def test_no_hand_rolled_request_exception_rewrap(self):
        # A future ``except RequestException: raise AdCPAdapterError(str(e))`` bypasses
        # wrap_request_errors and its status->recovery mapping (a 4xx wrongly reported
        # transient). Route the write through ``with wrap_request_errors():`` instead.
        offenders = sorted(set(collect_hand_rolled_request_exception_rewraps()) - HAND_ROLLED_REWRAP_ALLOWLIST)
        if offenders:
            detail = "\n".join(f"  - {site}" for site in offenders)
            raise AssertionError(
                "Hand-rolled RequestException->AdCPAdapterError(str(e)) re-wrap bypasses "
                f"wrap_request_errors:\n{detail}\n\n"
                "Wrap the call in `with wrap_request_errors():` (src/adapters/utils/http_errors.py) "
                "instead. Do NOT add to HAND_ROLLED_REWRAP_ALLOWLIST."
            )

    def test_hand_rolled_rewrap_allowlist_is_empty(self):
        assert HAND_ROLLED_REWRAP_ALLOWLIST == frozenset(), (
            f"HAND_ROLLED_REWRAP_ALLOWLIST must be empty but contains {sorted(HAND_ROLLED_REWRAP_ALLOWLIST)}. "
            "Route the write through wrap_request_errors instead of allowlisting it."
        )

    def test_rewrap_matcher_detects_antipattern_and_ignores_legitimate(self):
        # Positive + negative self-tests so the scan cannot pass vacuously: the matcher
        # must flag the bare re-wrap, and must NOT flag a swallowing handler or a
        # context-adding (f-string) re-raise.
        bare_rewrap = ast.parse(
            "import requests\n"
            "def f():\n"
            "    try:\n"
            "        requests.get('x').raise_for_status()\n"
            "    except requests.exceptions.RequestException as e:\n"
            "        raise AdCPAdapterError(str(e)) from e\n"
        )
        swallow = ast.parse(
            "import requests\n"
            "def f():\n"
            "    try:\n"
            "        requests.get('x').raise_for_status()\n"
            "    except requests.exceptions.RequestException as e:\n"
            "        log(e)\n"
        )
        context_rewrap = ast.parse(
            "import requests\n"
            "def f():\n"
            "    try:\n"
            "        requests.get('x').raise_for_status()\n"
            "    except requests.exceptions.RequestException as e:\n"
            "        raise AdCPAdapterError(f'download failed: {str(e)}') from e\n"
        )

        def _hits(tree: ast.AST) -> int:
            return sum(
                1
                for n in ast.walk(tree)
                if isinstance(n, ast.ExceptHandler)
                and _handler_catches_request_exception(n)
                and _handler_raises_bare_adapter_error(n)
            )

        assert _hits(bare_rewrap) == 1, "matcher failed to detect the bare hand-rolled re-wrap"
        assert _hits(swallow) == 0, "matcher wrongly flagged a swallowing handler"
        assert _hits(context_rewrap) == 0, "matcher wrongly flagged a context-adding re-raise"
