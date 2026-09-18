"""Guard: the signed ``@target-uri`` is built from the RAW wire bytes, and the
strict header gate reads the RAW header list.

Two halves of one disease — reading a NORMALIZED view of the request where the
signature-covered bytes are required. Both were live defects; this guard is what
stops either coming back.

Where the two subjects live now
------------------------------
#1721 split the single ``request_verifier_middleware.py`` that used to hold both
halves. The obligations are unchanged; the code that carries them moved:

* ``@target-uri`` is built in :mod:`src.core.signing.capture` — the ASGI capture
  that records the HTTP message on ``scope["state"]`` while its bytes still
  exist. ``_signed_path`` reads the raw bytes, ``_target_uri`` assembles the URL,
  ``SignedExchangeCapture.__call__`` stores it on the ``HttpExchange``.
* the strict header gate is :func:`src.core.signing.verifier._strict_header_precheck`,
  which now runs inside the boundary's resolver rather than in a middleware, so
  it is HANDED ``HttpExchange.raw_headers`` instead of reading the scope itself.
  That is why half 2 is graded across the two hops that carry it.

**Half 1 — the path.** ``scope["path"]`` is percent-DECODED by every real ASGI
server (uvicorn sets ``path = unquote(raw_path)``). The signature covers the
bytes the client sent, so a decoded path yields a signature base the client never
signed and rejects a legitimate request. ``_signed_path`` therefore reads
``scope["raw_path"]``. Conformance vectors ``positive/008`` (percent-encoded
non-ASCII), ``009`` (percent-encoded unreserved) and ``010`` (``%2F`` preserved)
are what prove it.

**Half 2 — the headers.** ``_strict_header_precheck`` runs over the raw
``list[tuple[bytes, bytes]]`` the capture recorded from ``scope["headers"]``, and
NOT over a collapsed dict view. ``headers_from_asgi_scope`` — like every dict
view of ASGI headers — LAST-WINS on a repeated header line rather than joining
it, so a proxy-inserted second ``Content-Type`` / ``Content-Digest`` /
``Signature-Input`` line rewrites a covered value before any check over the dict
could run. That second-line form is the attack ``negative/021``, ``022``, ``023``
and ``026`` actually describe; the vectors merely happen to EXPRESS it as one
comma-joined value, so a gate written over the collapsed dict passes all four
vectors while missing the threat.

**Why a blanket "always use raw_path" rule would be a REGRESSION — and why the
allowlist below is not a weakening.** Some sites read the DECODED path ON
PURPOSE, because they are routing predicates that MUST agree with the Starlette
router, which itself routes on the decoded path: ``src/core/http_utils.py``'s
``path_from_asgi_scope`` (the published route-table path rule), ``src/app.py``'s
REST error label and its ``/a2a`` predicate, and the capture's own surface test.
Making those read raw bytes would pull a different set of requests under the
capture than the dispatcher actually routes — the mirror defect, and a worse one.
So:

* the scan is SCOPED to ``src/core/signing/`` (where ``@target-uri`` is built),
  not to the whole tree;
* inside that scope, the surface predicate in ``SignedExchangeCapture.__call__``
  is ALLOWLISTED BY NAME with its reason: it feeds ``is_adcp_surface``, which
  decides whether to capture at all, and it must select the same requests the
  router dispatches to ``/mcp``, ``/a2a`` and ``/api/v1``. It reads
  ``path_from_asgi_scope`` — the published route-table rule — and not
  ``scope["path"]``, because those two are not the same path: ``scope["path"]``
  still carries ``root_path``, the router strips it, and under any prefix mount
  the predicate stopped selecting requests the router was still dispatching. The
  capture then recorded nothing and the verifier read every signed request as
  unsigned, silently. The row names the helper form for that reason;
* the documented fallback inside ``_signed_path`` (``raw_path`` absent or
  non-ASCII -> the decoded path, degradation stated at the source) is
  allowlisted, and ``test_signed_path_reads_raw_path`` separately pins that the
  fallback cannot become the only source;
* ``TestDeliberatelyDecodedSitesStayDecoded`` pins the out-of-scope routing
  sites as still DECODED, so a future blanket "fix" fails here instead of
  silently desynchronising us from the router.

Everything here is AST-based — there is no regex component, so there is no
near-miss/"would-be-missed" regex variant to pin.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import (
    REPO_ROOT,
    assert_detector_catches_ast_snippets,
    assert_violations_match_allowlist,
    iter_call_expressions,
    iter_module_trees,
    parse_module,
    walk_with_enclosing_function,
)

_SIGNING_DIR = REPO_ROOT / "src" / "core" / "signing"
#: Where ``@target-uri`` is derived — the ASGI capture.
_CAPTURE = _SIGNING_DIR / "capture.py"
#: Where the strict header gate runs — inside the boundary's resolver.
_VERIFIER = _SIGNING_DIR / "verifier.py"

_DECODED_PATH_HELPER = "path_from_asgi_scope"
_COLLAPSED_HEADERS_HELPER = "headers_from_asgi_scope"

_SIGNED_PATH_FN = "_signed_path"
_TARGET_URI_FN = "_target_uri"
_CAPTURE_ASGI_FN = "__call__"
_HEADER_PRECHECK_FN = "_strict_header_precheck"

#: The gate's parameter type — the raw header LINES, not a collapsed view.
_RAW_HEADER_LIST_TYPE = "tuple[tuple[bytes, bytes], ...]"
#: The attribute on ``HttpExchange`` that carries them.
_RAW_HEADERS_ATTR = "raw_headers"

# Forms this guard names in its findings.
_FORM_SUBSCRIPT = 'scope["path"]'
_FORM_GET = 'scope.get("path")'
_FORM_URL_PATH = ".url.path"
_FORM_HELPER = f"{_DECODED_PATH_HELPER}()"

#: ``(repo-relative file, enclosing function, form)``. MAY ONLY SHRINK.
#: Every row is a DELIBERATE decoded read inside the signing package; each
#: reason is spelled out in the module docstring.
_DECODED_PATH_ALLOWLIST: set[tuple[str, str, str]] = {
    # The documented fallback inside _signed_path: raw_path absent, or raw_path
    # present but not ASCII. On an ASGI server that omits raw_path the encoded
    # bytes are gone before we are called; failing every signed request instead
    # would be worse. Pinned as a FALLBACK (not the only source) by
    # test_signed_path_reads_raw_path.
    ("src/core/signing/capture.py", _SIGNED_PATH_FN, _FORM_GET),
    # Routing predicate: SignedExchangeCapture.__call__ asks is_adcp_surface()
    # whether to capture at all, so it must select the same requests the
    # Starlette router dispatches to the AdCP surfaces. It reads the PUBLISHED
    # route-path rule (path_from_asgi_scope) rather than scope["path"], which is
    # what makes "the same requests" true rather than merely intended: read raw,
    # the predicate and the router disagreed under any root_path mount and the
    # capture silently recorded nothing.
    ("src/core/signing/capture.py", _CAPTURE_ASGI_FN, _FORM_HELPER),
}

#: Sites OUTSIDE ``src/core/signing/`` that must STAY decoded. This is the other
#: half of the distinction: it fails if someone "fixes" a routing predicate to
#: read raw bytes. MAY ONLY SHRINK.
_DELIBERATELY_DECODED_FILES = (
    "src/core/http_utils.py",
    "src/app.py",
)


def _is_scope_key(node: ast.expr, key: str) -> bool:
    """True for ``scope["<key>"]``."""
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "scope"
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == key
    )


def _is_scope_get(call: ast.Call, key: str) -> bool:
    """True for ``scope.get("<key>", ...)``."""
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "get"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "scope"
        and bool(call.args)
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == key
    )


def _is_url_path_attribute(node: ast.expr) -> bool:
    """True for ``<anything>.url.path`` (Starlette's decoded ``request.url.path``)."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "path"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "url"
    )


def _function_by_lineno(tree: ast.AST) -> dict[int, str]:
    """Line number -> innermost enclosing function name."""
    mapping: dict[int, str] = {}
    for node, func_name in walk_with_enclosing_function(tree):
        lineno = getattr(node, "lineno", None)
        if lineno is not None:
            mapping.setdefault(lineno, func_name)
    return mapping


def _find_decoded_path_reads(tree: ast.AST) -> list[tuple[int, str, str]]:
    """``(lineno, enclosing function, form)`` for every DECODED-path read."""
    functions = _function_by_lineno(tree)
    found: list[tuple[int, str, str]] = []

    for node, func_name in walk_with_enclosing_function(tree):
        if isinstance(node, ast.expr) and _is_scope_key(node, "path"):
            found.append((node.lineno, func_name, _FORM_SUBSCRIPT))
        elif isinstance(node, ast.expr) and _is_url_path_attribute(node):
            found.append((node.lineno, func_name, _FORM_URL_PATH))

    for call in iter_call_expressions(tree):
        enclosing = functions.get(call.lineno, "<module>")
        if _is_scope_get(call, "path"):
            found.append((call.lineno, enclosing, _FORM_GET))
        elif isinstance(call.func, ast.Name) and call.func.id == _DECODED_PATH_HELPER:
            found.append((call.lineno, enclosing, _FORM_HELPER))

    return found


def _decoded_path_linenos(tree: ast.AST) -> list[int]:
    """Detector shape ``assert_detector_catches_ast_snippets`` expects."""
    return [lineno for lineno, _fn, _form in _find_decoded_path_reads(tree)]


def _find_collapsed_header_reads(tree: ast.AST) -> list[int]:
    """Line numbers of ``headers_from_asgi_scope(...)`` calls — the last-wins dict view."""
    return [
        call.lineno
        for call in iter_call_expressions(tree, name=_COLLAPSED_HEADERS_HELPER)
        if isinstance(call.func, ast.Name)
    ]


def _scan_signing_package() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for tree, rel_path in iter_module_trees([_SIGNING_DIR]):
        for _lineno, func_name, form in _find_decoded_path_reads(tree):
            found.add((rel_path, func_name, form))
    return found


def _function_node(path: Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = parse_module(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path} — the guard's subject moved or was renamed")


def _exchange_kwarg(name: str) -> ast.expr:
    """The expression assigned to ``name=`` in the ``HttpExchange(...)`` the capture records.

    Both assertions below bind to the KEYWORD ARGUMENT rather than to "a call of the right
    function appears somewhere in ``__call__``". The looser form was a real hole once #1721
    moved the capture: ``__call__`` also evaluates the surface predicate, so it carries an
    allowlisted decoded-path read, and a recorder that called ``_target_uri`` for the
    predicate while assembling ``url=`` from ``scope["path"]`` satisfied every check while
    handing the verifier a ``@target-uri`` the client never signed.
    """
    recorder = _function_node(_CAPTURE, _CAPTURE_ASGI_FN)
    for call in iter_call_expressions(recorder, name="HttpExchange"):
        for kw in call.keywords:
            if kw.arg == name:
                return kw.value
    raise AssertionError(f"{_CAPTURE_ASGI_FN} does not construct HttpExchange(..., {name}=...)")


class TestSignedTargetUriUsesRawPath:
    """Half 1 — ``@target-uri`` is built from ``raw_path``."""

    @pytest.mark.arch_guard
    def test_no_unclassified_decoded_path_reads_in_signing_package(self) -> None:
        """Every decoded-path read under ``src/core/signing/`` is a named exception."""
        assert_violations_match_allowlist(
            _scan_signing_package(),
            _DECODED_PATH_ALLOWLIST,
            fix_hint=(
                "A new decoded-path read inside src/core/signing/. If it feeds the signed "
                "@target-uri it is a defect — read scope['raw_path'] (see _signed_path). If "
                "it is a routing predicate or route-table lookup it must stay decoded: add "
                "it to _DECODED_PATH_ALLOWLIST with the reason. The allowlist only shrinks."
            ),
        )

    @pytest.mark.arch_guard
    def test_signed_path_reads_raw_path(self) -> None:
        """The decoded read inside ``_signed_path`` is a FALLBACK, not the source."""
        node = _function_node(_CAPTURE, _SIGNED_PATH_FN)
        reads_raw = any(
            _is_scope_key(child, "raw_path") for child in ast.walk(node) if isinstance(child, ast.expr)
        ) or any(_is_scope_get(call, "raw_path") for call in iter_call_expressions(node))
        assert reads_raw, (
            f"{_SIGNED_PATH_FN} must read scope['raw_path'] — the bytes the client signed. "
            "Its scope['path'] read is allowlisted only as the raw_path-absent fallback; if "
            "raw_path is gone the fallback silently became the only source and vectors "
            "positive/008, 009 and 010 stop being gradeable."
        )

    @pytest.mark.arch_guard
    def test_target_uri_builds_through_signed_path_and_reaches_the_capture(self) -> None:
        """Non-vacuity: the raw path must actually reach the recorded ``@target-uri``.

        Two hops since #1721 — ``_signed_path`` -> ``_target_uri`` -> the ``url`` on the
        ``HttpExchange`` the verifier grades — and either one going dead makes every
        raw-path assertion above vacuous.
        """
        builder = _function_node(_CAPTURE, _TARGET_URI_FN)
        assert list(iter_call_expressions(builder, name=_SIGNED_PATH_FN)), (
            f"{_TARGET_URI_FN} must build the URL through {_SIGNED_PATH_FN}; otherwise the "
            f"raw-path handling in {_SIGNED_PATH_FN} is dead code."
        )
        url_expr = _exchange_kwarg("url")
        assert (
            isinstance(url_expr, ast.Call)
            and isinstance(url_expr.func, ast.Name)
            and url_expr.func.id == _TARGET_URI_FN
        ), (
            f"HttpExchange(url=...) must be {_TARGET_URI_FN}(scope), not {ast.unparse(url_expr)}. A "
            "capture that assembles the URL anywhere else hands the verifier a @target-uri the "
            f"client never signed — and the decoded-path read inside {_CAPTURE_ASGI_FN} is "
            "allowlisted for the SURFACE PREDICATE only, so the allowlist cannot be leaned on here."
        )

    @pytest.mark.arch_guard
    def test_detector_catches_known_bad_decoded_path_reads(self) -> None:
        """Positive meta-test: every decoded-path form is flagged."""
        assert_detector_catches_ast_snippets(
            _decoded_path_linenos,
            snippets={
                "scope-subscript": ("def _signed_path(scope):\n    return scope['path']\n"),
                "scope-get": ("def _signed_path(scope):\n    return str(scope.get('path', ''))\n"),
                "request-url-path": ("def _signed_path(request):\n    return request.url.path\n"),
                "decoded-helper": (
                    "from src.core.http_utils import path_from_asgi_scope\n\n"
                    "def _signed_path(scope):\n"
                    "    return path_from_asgi_scope(scope)\n"
                ),
                "interpolated-into-target-uri": (
                    "def _verify_url(scope, headers):\n    return f\"https://{headers['host']}{scope['path']}\"\n"
                ),
            },
        )

    @pytest.mark.arch_guard
    @pytest.mark.parametrize(
        ("label", "source"),
        [
            ("raw-path-subscript", "def _signed_path(scope):\n    return scope['raw_path']\n"),
            ("raw-path-get", "def _signed_path(scope):\n    return scope.get('raw_path')\n"),
            ("query-string", "def _verify_url(scope):\n    return scope.get('query_string', b'')\n"),
            ("headers-list", "def _precheck(scope):\n    return scope['headers']\n"),
            ("unrelated-path-attr", "def _f(cfg):\n    return cfg.path\n"),
            ("unrelated-mapping-key", "def _f(payload):\n    return payload['path']\n"),
        ],
    )
    def test_detector_passes_clean_shapes(self, label: str, source: str) -> None:
        """Negative meta-test: raw reads and unrelated ``path`` names are not flagged."""
        assert _find_decoded_path_reads(ast.parse(source)) == [], label


class TestStrictHeaderGateReadsRawHeaderList:
    """Half 2 — the pre-parse gate reads the raw header LINES, never the collapsed dict."""

    @pytest.mark.arch_guard
    def test_precheck_reads_the_raw_header_list(self) -> None:
        """The gate's input is the raw header list, recorded verbatim from the scope.

        #1721 moved the gate out of the middleware, so it no longer reads the scope
        itself: the capture records ``scope["headers"]`` and the gate is handed that
        list. Both hops are graded here, because a collapse at either one erases the
        repeated header LINE before anything can refuse it.
        """
        gate = _function_node(_VERIFIER, _HEADER_PRECHECK_FN)
        annotations = [
            ast.unparse(arg.annotation)
            for arg in (*gate.args.posonlyargs, *gate.args.args)
            if arg.annotation is not None
        ]
        assert annotations == [_RAW_HEADER_LIST_TYPE], (
            f"{_HEADER_PRECHECK_FN} must take the raw header list ({_RAW_HEADER_LIST_TYPE}), "
            f"not {annotations}. Repeated header LINES are what negative/021, 022, 023 and "
            "026 describe, and they are indistinguishable once collapsed into a dict."
        )

        # Scoped to the KWARG, not to the whole function: "scope['headers'] is mentioned
        # somewhere in __call__" is satisfied by a recorder that mentions it and then
        # collapses it, which is exactly the lossy value this asserts against.
        headers_expr = _exchange_kwarg(_RAW_HEADERS_ATTR)
        records_raw = any(
            _is_scope_key(child, "headers") for child in ast.walk(headers_expr) if isinstance(child, ast.expr)
        ) or any(_is_scope_get(child, "headers") for child in ast.walk(headers_expr) if isinstance(child, ast.Call))
        collapses = [
            ast.unparse(child)
            for child in ast.walk(headers_expr)
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id in ("dict", _COLLAPSED_HEADERS_HELPER)
        ]
        assert records_raw and not collapses, (
            f"HttpExchange({_RAW_HEADERS_ATTR}=...) must be built from scope['headers'] — the raw "
            f"list[tuple[bytes, bytes]] — and must not collapse it{f' (found {collapses})' if collapses else ''}. "
            f"A dict()/{_COLLAPSED_HEADERS_HELPER}() collapse is LAST-WINS on a repeated header LINE, "
            "which is what negative/021, 022, 023 and 026 attack, and it satisfies the gate's raw "
            "parameter type with an already-lossy value."
        )

    @pytest.mark.arch_guard
    def test_precheck_never_reads_the_collapsed_dict(self) -> None:
        node = _function_node(_VERIFIER, _HEADER_PRECHECK_FN)
        collapsed = _find_collapsed_header_reads(node)
        assert collapsed == [], (
            f"{_HEADER_PRECHECK_FN} calls {_COLLAPSED_HEADERS_HELPER}() at line(s) {collapsed}. "
            "That dict view LAST-WINS on a repeated header line rather than joining it, so a "
            "proxy-inserted second covered-header line is erased before the gate can see it. "
            f"The gate must read scope['headers'] directly. Other callers of "
            f"{_COLLAPSED_HEADERS_HELPER}() are fine — this is scoped to the gate."
        )

    @pytest.mark.arch_guard
    def test_precheck_is_actually_wired(self) -> None:
        """Non-vacuity: a gate nobody calls is inert, and both tests above pass anyway."""
        tree = parse_module(_VERIFIER)
        functions = _function_by_lineno(tree)
        calls = [
            call
            for call in iter_call_expressions(tree, name=_HEADER_PRECHECK_FN)
            if functions.get(call.lineno) != _HEADER_PRECHECK_FN
        ]
        assert calls, (
            f"{_HEADER_PRECHECK_FN} is defined but never called — the strict pre-parse gate "
            "would be inert and every assertion about it vacuous."
        )
        passed = [ast.unparse(call.args[0]) if call.args else "<no argument>" for call in calls]
        assert all(
            call.args and isinstance(call.args[0], ast.Attribute) and call.args[0].attr == _RAW_HEADERS_ATTR
            for call in calls
        ), (
            f"{_HEADER_PRECHECK_FN} must be called with the capture's .{_RAW_HEADERS_ATTR}, not "
            f"{passed}. Any other argument means the gate grades a header view that was "
            "assembled somewhere else, and the collapse it exists to refuse may already have "
            "happened."
        )

    @pytest.mark.arch_guard
    def test_detector_catches_collapsed_header_read(self) -> None:
        """Positive meta-test: the collapsed-dict read is flagged."""
        assert_detector_catches_ast_snippets(
            _find_collapsed_header_reads,
            snippets={
                "collapsed-dict-gate": (
                    "from src.core.http_utils import headers_from_asgi_scope\n\n"
                    "def _strict_header_precheck(scope):\n"
                    "    headers = headers_from_asgi_scope(scope)\n"
                    "    if ',' in headers.get('content-type', ''):\n"
                    "        raise ValueError('malformed')\n"
                ),
            },
        )

    @pytest.mark.arch_guard
    def test_detector_passes_raw_header_iteration(self) -> None:
        """Negative meta-test: iterating the raw list is not flagged."""
        source = (
            "def _strict_header_precheck(scope):\n"
            "    lines = {}\n"
            "    for raw_name, raw_value in scope.get('headers', []):\n"
            "        lines.setdefault(raw_name.decode('latin-1').lower(), []).append(raw_value)\n"
            "    return lines\n"
        )
        assert _find_collapsed_header_reads(ast.parse(source)) == []


class TestDeliberatelyDecodedSitesStayDecoded:
    """The distinction, pinned from the other side: a blanket rule would be a defect."""

    @pytest.mark.arch_guard
    @pytest.mark.parametrize("rel_path", _DELIBERATELY_DECODED_FILES)
    def test_routing_sites_outside_signing_still_read_the_decoded_path(self, rel_path: str) -> None:
        """These agree with the Starlette router by construction; raw bytes would desync them.

        Scoping the ``@target-uri`` guard to ``src/core/signing/`` is only safe if
        the routing sites keep reading the DECODED path. If a future change moves
        one of them onto ``raw_path``, the route-table path rule and the REST error
        labelling stop matching the dispatcher — the mirror defect — and it fails
        here rather than silently.
        """
        reads = _find_decoded_path_reads(parse_module(REPO_ROOT / rel_path))
        assert reads, (
            f"{rel_path} no longer reads the decoded request path. Routing predicates and "
            "route-table lookups MUST stay decoded so they agree with the Starlette router "
            "that actually dispatches; raw bytes there desynchronise the signing surface "
            "allowlist and the operation resolver from the dispatcher. If this file "
            "legitimately stopped doing path routing, remove it from "
            "_DELIBERATELY_DECODED_FILES in the same change."
        )
