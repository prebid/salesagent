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

# An ``httpx`` handler that catches ``httpx.HTTPStatusError`` / its base ``httpx.HTTPError`` (a status CAN
# be present) but re-raises a directly-constructed ``AdCP*Error`` — inline or assign-then-raise — hard-codes
# ONE recovery class and discards the status, so a /v1/site 4xx surfaces transient (retry forever) instead
# of correctable. MUST stay empty — route the handler through ``adcp_error_for_httpx_exc(exc, message)``
# (the httpx dual of ``wrap_request_errors``) instead. The matcher is immune to the message form
# (f-string / repr / kw-arg), to the assign-then-raise form, and to the base-class ``HTTPError`` catch.
# Residual it does NOT model (accepted — contrived, no live site): an aliased import
# (``from httpx import HTTPStatusError as HSE``), and a dead status-mapper call placed in the handler
# alongside a real status-discarding raise purely to satisfy the routing check.
HTTPX_STATUS_DISCARD_ALLOWLIST: frozenset[str] = frozenset()

_ADAPTERS_DIR = REPO_ROOT / "src/adapters"

# This PR added a buyer-facing HTTP write OUTSIDE src/adapters/: the property-list fetch in
# src/core/property_list_resolver.py (raise_for_status, wrapped via _raise_fetch_error). The guard's
# invariant ("an HTTP write that reaches the buyer must surface a typed AdCPError") applies there too,
# so the scan covers src/adapters/ PLUS that file. Scoped to this file (not all of src/core/) so it
# does not pull pre-existing unrelated HTTP-write sites into this PR's guard.
_EXTRA_SCAN_FILES: tuple = (REPO_ROOT / "src/core/property_list_resolver.py",)


def _iter_scan_files() -> list:
    """Every .py file this guard scans: all of src/adapters/ + the explicit extra HTTP-write files."""
    return sorted(_ADAPTERS_DIR.rglob("*.py")) + [f for f in _EXTRA_SCAN_FILES if f.exists()]


# The shared status->recovery mappers (src/core/exceptions.py). An httpx-status handler is status-
# preserving when it routes through one of these instead of constructing a fixed-recovery error.
_STATUS_MAPPERS: frozenset[str] = frozenset({"adcp_error_for_http_status", "adcp_error_for_httpx_exc"})

# Catching the status-bearing ``HTTPStatusError`` OR its base ``HTTPError`` (which includes it) means a
# status CAN be present, so a status-discarding re-raise is a defect. ``RequestError`` is the status-LESS
# sibling — deliberately excluded so correct timeout/connect handlers are not false-flagged.
_HTTPX_STATUS_CATCH_NAMES: frozenset[str] = frozenset({"HTTPStatusError", "HTTPError"})

# Exception types whose catch actually intercepts a ``raise_for_status()`` transport failure. A handler
# that raises a typed error but catches none of these (e.g. ``except KeyError``) does NOT protect the write.
_HTTP_TRANSPORT_EXC_NAMES: frozenset[str] = frozenset(
    {
        "RequestException",
        "HTTPError",
        "ConnectionError",
        "Timeout",
        "RequestError",
        "HTTPStatusError",
        "TimeoutException",
        "TransportError",
        "NetworkError",
    }
)

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

    Safe re-raises are either a direct ``AdCP*Error(...)`` construction or the RESULT of a shared
    status->recovery mapper (``raise adcp_error_for_httpx_exc(exc, ...)`` — the mapper returns a typed
    ``AdCPError``). A bare ``raise`` (re-raises the caught transport exception) or a ``raise`` of any
    other expression lets the raw exception escape and is unsafe.
    """
    for node in ast.walk(handler):
        if not isinstance(node, ast.Raise):
            continue
        exc = node.exc
        if exc is None:
            return False  # bare ``raise`` re-raises the raw transport exception
        if isinstance(exc, ast.Call):
            fn = exc.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
            if name and (_is_adcp_error_name(name) or name in _STATUS_MAPPERS):
                continue
        return False  # raises something other than a typed AdCP error or a status-mapper result
    return True  # no raise in the handler -> swallowed


def _handler_catches_http_transport(handler: ast.ExceptHandler) -> bool:
    """True if the ``except`` can actually intercept a ``raise_for_status()`` transport failure.

    A handler that RAISES a typed error only protects the write if it also CATCHES the raw exception:
    ``except KeyError: raise AdCPAdapterError(...)`` is "safe" by the raise check yet lets the HTTPError
    escape. A bare ``except:`` or ``except Exception``/``BaseException`` catches everything; otherwise the
    catch-set must name an HTTP/transport exception type.
    """
    exc_type = handler.type
    if exc_type is None:
        return True  # bare ``except:`` catches the transport failure
    candidates = exc_type.elts if isinstance(exc_type, ast.Tuple) else [exc_type]
    for c in candidates:
        name = c.attr if isinstance(c, ast.Attribute) else (c.id if isinstance(c, ast.Name) else None)
        if name in _HTTP_TRANSPORT_EXC_NAMES or name in ("Exception", "BaseException"):
            return True
    return False


def _try_protects(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """True if some enclosing ``try`` (with ``node`` in its body) safely intercepts the transport failure.

    Requires BOTH: every handler is safe (swallows or re-raises only as a typed AdCP error), AND at least
    one handler actually catches an HTTP/transport exception — otherwise the raw failure escapes a
    non-transport ``except`` (e.g. ``except KeyError``) while the guard reads it as protected.
    """
    for try_node in _enclosing(node, parents, ast.Try):
        # Only count the try if the node is in its body, not in a handler/finally.
        in_body = any(node is descendant for stmt in try_node.body for descendant in ast.walk(stmt))  # type: ignore[attr-defined]
        if not in_body or not try_node.handlers:  # type: ignore[attr-defined]
            continue
        handlers = try_node.handlers  # type: ignore[attr-defined]
        if all(_handler_is_safe(h) for h in handlers) and any(_handler_catches_http_transport(h) for h in handlers):
            return True
    return False


def collect_unwrapped_http_writes() -> list[str]:
    """Return ``path:line`` for every unprotected ``raise_for_status()`` under src/adapters."""
    unwrapped: list[str] = []
    for filepath in _iter_scan_files():
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
    for filepath in _iter_scan_files():
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


def _handler_catches_httpx_status_error(handler: ast.ExceptHandler) -> bool:
    """True if the ``except`` catches ``httpx.HTTPStatusError`` or its base ``httpx.HTTPError``.

    Catching either means a status CAN be present on the caught exception (``HTTPError`` is the base of
    both the status-bearing ``HTTPStatusError`` and the status-less ``RequestError``), so a re-raise that
    does not consult the status discards it for the 4xx case. ``RequestError`` alone is excluded — it
    carries no status, so a transient re-wrap there is correct.
    """
    exc_type = handler.type
    if exc_type is None:
        return False
    candidates = exc_type.elts if isinstance(exc_type, ast.Tuple) else [exc_type]
    for c in candidates:
        name = c.attr if isinstance(c, ast.Attribute) else (c.id if isinstance(c, ast.Name) else None)
        if name in _HTTPX_STATUS_CATCH_NAMES:
            return True
    return False


def _handler_calls_status_mapper(handler: ast.ExceptHandler) -> bool:
    """True if the handler routes through a shared status->recovery mapper (status-preserving)."""
    for node in ast.walk(handler):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
            if name in _STATUS_MAPPERS:
                return True
    return False


def _handler_raises_constructed_adcp_error(handler: ast.ExceptHandler) -> bool:
    """True if the handler raises a directly-constructed typed ``AdCP*Error``.

    Two forms are caught: inline (``raise AdCPAdapterError(...)``) and assign-then-raise
    (``err = AdCPAdapterError(...); raise err``). Direct construction hard-codes a single recovery class;
    from an ``HTTPStatusError`` handler that discards the real status. Raising a status mapper's RESULT
    (``raise adcp_error_for_httpx_exc(exc, ...)``) is a function call, not a direct ``AdCP*Error``
    construction, so it is not flagged. Independent of the message form (f-string, ``repr``, keyword arg).
    """
    # Names bound to an AdCP*Error construction in the handler body (for the assign-then-raise form).
    adcp_bound: set[str] = set()
    for node in ast.walk(handler):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fn = node.value.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
            if name and _is_adcp_error_name(name):
                adcp_bound.update(t.id for t in node.targets if isinstance(t, ast.Name))
    for node in ast.walk(handler):
        if not (isinstance(node, ast.Raise) and node.exc is not None):
            continue
        exc = node.exc
        if isinstance(exc, ast.Call):
            fn = exc.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
            if name and _is_adcp_error_name(name):
                return True
        elif isinstance(exc, ast.Name) and exc.id in adcp_bound:
            return True
    return False


def collect_status_discarding_httpx_rewraps() -> list[str]:
    """Return ``path:line`` for every adapter ``except httpx.HTTPStatusError`` handler that re-raises a
    directly-constructed ``AdCP*Error`` without routing through a shared status->recovery mapper."""
    out: list[str] = []
    for filepath in _iter_scan_files():
        tree = safe_parse(filepath)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ExceptHandler)
                and _handler_catches_httpx_status_error(node)
                and _handler_raises_constructed_adcp_error(node)
                and not _handler_calls_status_mapper(node)
            ):
                out.append(f"{rel(filepath)}:{node.lineno}")
    return out


class TestAdapterHttpWritesWrapped:
    """Every adapter HTTP write must surface a typed AdCPError, never a raw transport exception."""

    def test_every_adapter_http_write_is_wrapped(self):
        # Self-check: the scan must find raise_for_status sites, or a refactor
        # silently broke the AST walk and this guard passes vacuously.
        sites_seen = 0
        for filepath in _iter_scan_files():
            tree = safe_parse(filepath)
            if tree is None:
                continue
            sites_seen += sum(
                1 for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "raise_for_status"
            )
        assert sites_seen > 0, "no raise_for_status sites found in the scan set — scan likely broken"

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

    def test_no_status_discarding_httpx_rewrap(self):
        # A future ``except httpx.HTTPStatusError: raise AdCPAdapterError(f"...{exc}")`` catches a
        # status-bearing failure but forces a single recovery class — a /v1/site 4xx then reports
        # transient (retry forever) instead of correctable. Route it through adcp_error_for_httpx_exc.
        offenders = sorted(set(collect_status_discarding_httpx_rewraps()) - HTTPX_STATUS_DISCARD_ALLOWLIST)
        if offenders:
            detail = "\n".join(f"  - {site}" for site in offenders)
            raise AssertionError(
                "An httpx handler catches HTTPStatusError but re-raises a directly-constructed "
                f"AdCP*Error, discarding the status (a 4xx wrongly reported transient):\n{detail}\n\n"
                "Route the handler through `adcp_error_for_httpx_exc(exc, message)` "
                "(src/core/exceptions.py — the httpx dual of wrap_request_errors) instead. "
                "Do NOT add to HTTPX_STATUS_DISCARD_ALLOWLIST."
            )

    def test_httpx_status_discard_allowlist_is_empty(self):
        assert HTTPX_STATUS_DISCARD_ALLOWLIST == frozenset(), (
            f"HTTPX_STATUS_DISCARD_ALLOWLIST must be empty but contains {sorted(HTTPX_STATUS_DISCARD_ALLOWLIST)}. "
            "Route the handler through adcp_error_for_httpx_exc instead of allowlisting it."
        )

    def test_httpx_status_discard_matcher_detects_and_ignores(self):
        # Positive + negative self-tests so the httpx scan cannot pass vacuously: the matcher must
        # flag a direct AdCP*Error re-raise from an HTTPStatusError handler, and must NOT flag a
        # handler routing through either status mapper, nor a swallowing handler.
        discard = ast.parse(
            "import httpx\n"
            "def f():\n"
            "    try:\n"
            "        client.get('x').raise_for_status()\n"
            "    except (httpx.HTTPStatusError, httpx.RequestError) as e:\n"
            "        raise AdCPAdapterError(f'failed: {e}') from e\n"
        )
        mapped_httpx = ast.parse(
            "import httpx\n"
            "def f():\n"
            "    try:\n"
            "        client.get('x').raise_for_status()\n"
            "    except (httpx.HTTPStatusError, httpx.RequestError) as e:\n"
            "        raise adcp_error_for_httpx_exc(e, 'failed') from e\n"
        )
        mapped_status = ast.parse(
            "import httpx\n"
            "def f():\n"
            "    try:\n"
            "        client.get('x').raise_for_status()\n"
            "    except httpx.HTTPStatusError as e:\n"
            "        raise adcp_error_for_http_status(e.response.status_code, 'failed') from e\n"
        )
        swallow = ast.parse(
            "import httpx\n"
            "def f():\n"
            "    try:\n"
            "        client.get('x').raise_for_status()\n"
            "    except httpx.HTTPStatusError as e:\n"
            "        log(e)\n"
        )
        base_class = ast.parse(
            "import httpx\n"
            "def f():\n"
            "    try:\n"
            "        client.get('x').raise_for_status()\n"
            "    except httpx.HTTPError as e:\n"  # base class — includes HTTPStatusError
            "        raise AdCPAdapterError(f'failed: {e}') from e\n"
        )
        assign_then_raise = ast.parse(
            "import httpx\n"
            "def f():\n"
            "    try:\n"
            "        client.get('x').raise_for_status()\n"
            "    except httpx.HTTPStatusError as e:\n"
            "        err = AdCPAdapterError(f'failed: {e}')\n"
            "        raise err from e\n"
        )

        def _hits(tree: ast.AST) -> int:
            return sum(
                1
                for n in ast.walk(tree)
                if isinstance(n, ast.ExceptHandler)
                and _handler_catches_httpx_status_error(n)
                and _handler_raises_constructed_adcp_error(n)
                and not _handler_calls_status_mapper(n)
            )

        assert _hits(discard) == 1, "matcher failed to detect the status-discarding httpx re-wrap"
        assert _hits(mapped_httpx) == 0, "matcher wrongly flagged a handler routing through adcp_error_for_httpx_exc"
        assert _hits(mapped_status) == 0, "matcher wrongly flagged a handler routing through adcp_error_for_http_status"
        assert _hits(swallow) == 0, "matcher wrongly flagged a swallowing handler"
        assert _hits(base_class) == 1, "matcher must flag the base-class httpx.HTTPError status discard"
        assert _hits(assign_then_raise) == 1, "matcher must flag the assign-then-raise status discard"

    def test_wrapping_check_requires_an_http_transport_catch(self):
        # A handler that re-raises a typed AdCP error but catches a NON-transport type (KeyError) does not
        # actually intercept the raise_for_status failure — the raw HTTPError escapes. _try_protects must
        # NOT treat it as protected; a real transport catch (RequestException) must.
        import textwrap

        def _protected(src: str) -> bool:
            tree = ast.parse(textwrap.dedent(src))
            parents = _build_parents(tree)
            rfs = next(n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "raise_for_status")
            return _try_protects(rfs, parents)

        wrong_catch = """
            def f():
                try:
                    resp.raise_for_status()
                except KeyError:
                    raise AdCPAdapterError("nope")
        """
        http_catch = """
            import requests
            def f():
                try:
                    resp.raise_for_status()
                except requests.exceptions.RequestException:
                    raise AdCPAdapterError("wrapped")
        """
        assert not _protected(wrong_catch), "a KeyError catch must not count as protecting the HTTP write"
        assert _protected(http_catch), "a RequestException catch + typed raise must protect the HTTP write"
