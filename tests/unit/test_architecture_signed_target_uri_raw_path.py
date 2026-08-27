"""Guard: the signed ``@target-uri`` is built from the RAW wire bytes, and the
strict header gate reads the RAW header list.

Two halves of one disease — reading a NORMALIZED view of the request where the
signature-covered bytes are required. Both were live defects; both are fixed in
``src/core/signing/request_verifier_middleware.py``; this guard is what stops
either coming back.

**Half 1 — the path.** ``scope["path"]`` is percent-DECODED by every real ASGI
server (uvicorn sets ``path = unquote(raw_path)``). The signature covers the
bytes the client sent, so a decoded path yields a signature base the client never
signed and rejects a legitimate request. ``_signed_path`` therefore reads
``scope["raw_path"]``. Conformance vectors ``positive/008`` (percent-encoded
non-ASCII), ``009`` (percent-encoded unreserved) and ``010`` (``%2F`` preserved)
are what prove it.

**Half 2 — the headers.** ``_strict_header_precheck`` runs over
``scope["headers"]``, the raw ``list[tuple[bytes, bytes]]``, and NOT over a
collapsed dict view. ``headers_from_asgi_scope`` — like every dict view of ASGI
headers — LAST-WINS on a repeated header line rather than joining it, so a
proxy-inserted second ``Content-Type`` / ``Content-Digest`` / ``Signature-Input``
line rewrites a covered value before any check over the dict could run. That
second-line form is the attack ``negative/021``, ``022``, ``023`` and ``026``
actually describe; the vectors merely happen to EXPRESS it as one comma-joined
value, so a gate written over the collapsed dict passes all four vectors while
missing the threat.

**Why a blanket "always use raw_path" rule would be a REGRESSION — and why the
allowlist below is not a weakening.** Five sites in this tree read the DECODED
path ON PURPOSE, because they are routing predicates and route-table lookups
that MUST agree with the Starlette router, which itself routes on the decoded
path: ``src/core/http_utils.py::path_from_asgi_scope``, ``src/app.py``'s
telemetry label and its ``/a2a`` predicate, and ``src/routes/rest_compat_middleware.py``'s
two route lookups. Making those read raw bytes would make our allowlist and
operation resolution disagree with the dispatcher — the mirror defect, and a
worse one. So:

* the scan is SCOPED to ``src/core/signing/`` (where ``@target-uri`` is built),
  not to the whole tree;
* inside that scope, the two ``path_from_asgi_scope`` callers are ALLOWLISTED
  BY NAME with their reason — ``_is_adcp_surface`` matches the middleware's
  surface allowlist against the route table, and ``RegistryOperationResolver.resolve``
  looks the operation up in the same route table. Both must stay decoded;
* the ONE documented fallback inside ``_signed_path`` (``raw_path`` absent ->
  the decoded path, degradation stated at the source) is allowlisted, and
  ``test_signed_path_reads_raw_path`` separately pins that the fallback cannot
  become the only source;
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
_MIDDLEWARE = _SIGNING_DIR / "request_verifier_middleware.py"

_DECODED_PATH_HELPER = "path_from_asgi_scope"
_COLLAPSED_HEADERS_HELPER = "headers_from_asgi_scope"

_SIGNED_PATH_FN = "_signed_path"
_VERIFY_URL_FN = "_verify_url"
_HEADER_PRECHECK_FN = "_strict_header_precheck"

# Forms this guard names in its findings.
_FORM_SUBSCRIPT = 'scope["path"]'
_FORM_GET = 'scope.get("path")'
_FORM_URL_PATH = ".url.path"
_FORM_HELPER = f"{_DECODED_PATH_HELPER}()"

#: ``(repo-relative file, enclosing function, form)``. MAY ONLY SHRINK.
#: Every row is a DELIBERATE decoded read inside the signing package; each
#: reason is spelled out in the module docstring.
_DECODED_PATH_ALLOWLIST: set[tuple[str, str, str]] = {
    # The documented raw_path-absent fallback. On an ASGI server that omits
    # raw_path the encoded bytes are gone before we are called; failing every
    # signed request instead would be worse. Pinned as a FALLBACK (not the only
    # source) by test_signed_path_reads_raw_path.
    ("src/core/signing/request_verifier_middleware.py", _SIGNED_PATH_FN, _FORM_GET),
    # Routing predicate: matches ADCP_SURFACE_PREFIXES against the route table,
    # so it must agree with the Starlette router (decoded).
    ("src/core/signing/request_verifier_middleware.py", "_is_adcp_surface", _FORM_HELPER),
    # Route-table lookup: resolves the AdCP operation from the registered REST
    # routes. Same reason — must agree with the dispatcher.
    ("src/core/signing/operations.py", "resolve", _FORM_HELPER),
}

#: Sites OUTSIDE ``src/core/signing/`` that must STAY decoded. This is the other
#: half of the distinction: it fails if someone "fixes" a routing predicate to
#: read raw bytes. MAY ONLY SHRINK.
_DELIBERATELY_DECODED_FILES = (
    "src/core/http_utils.py",
    "src/app.py",
    "src/routes/rest_compat_middleware.py",
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
        node = _function_node(_MIDDLEWARE, _SIGNED_PATH_FN)
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
    def test_verify_url_builds_target_uri_through_signed_path(self) -> None:
        """Non-vacuity: the raw path must actually reach ``@target-uri``."""
        node = _function_node(_MIDDLEWARE, _VERIFY_URL_FN)
        calls = list(iter_call_expressions(node, name=_SIGNED_PATH_FN))
        assert calls, (
            f"{_VERIFY_URL_FN} must build the URL through {_SIGNED_PATH_FN}; otherwise the "
            f"raw-path handling in {_SIGNED_PATH_FN} is dead code and the guard above is vacuous."
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
    """Half 2 — the pre-parse gate reads ``scope["headers"]``, never the collapsed dict."""

    @pytest.mark.arch_guard
    def test_precheck_reads_the_raw_header_list(self) -> None:
        node = _function_node(_MIDDLEWARE, _HEADER_PRECHECK_FN)
        reads_raw = any(
            _is_scope_key(child, "headers") for child in ast.walk(node) if isinstance(child, ast.expr)
        ) or any(_is_scope_get(call, "headers") for call in iter_call_expressions(node))
        assert reads_raw, (
            f"{_HEADER_PRECHECK_FN} must iterate scope['headers'] — the raw "
            "list[tuple[bytes, bytes]]. Repeated header LINES are what negative/021, 022, "
            "023 and 026 describe, and they are indistinguishable once collapsed."
        )

    @pytest.mark.arch_guard
    def test_precheck_never_reads_the_collapsed_dict(self) -> None:
        node = _function_node(_MIDDLEWARE, _HEADER_PRECHECK_FN)
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
        tree = parse_module(_MIDDLEWARE)
        functions = _function_by_lineno(tree)
        callers = [
            func_name
            for call in iter_call_expressions(tree, name=_HEADER_PRECHECK_FN)
            if (func_name := functions.get(call.lineno)) != _HEADER_PRECHECK_FN
        ]
        assert callers, (
            f"{_HEADER_PRECHECK_FN} is defined but never called — the strict pre-parse gate "
            "would be inert and every assertion about it vacuous."
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
        one of them onto ``raw_path``, our surface allowlist and operation
        resolution stop matching the dispatcher — the mirror defect — and it
        fails here rather than silently.
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
