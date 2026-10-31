"""Shared stdlib-only JSON/HTTP scaffolding for the e2e compose origins.

Two e2e services answer JSON over plain HTTP behind the shared ``tls-proxy``
front — ``webhook_capture_service`` (salesagent-mp53.9) and
``counterparty_origin_service`` (salesagent-mp53.8) — and they answered it with
byte-identical code until this module existed.

STRICTLY STDLIB, and that constraint is the whole reason this lives here rather
than under ``tests/helpers/``: both services run on a bare ``python:3.12-slim``
compose service with no project dependencies, and importing anything under
``tests.helpers`` runs that package's ``__init__``, which transitively pulls in
``tests.factories`` (factory-boy). Upstream confirmed that failure the hard way —
the container exits on import before ever binding a socket. ``tests/__init__.py``
and ``tests/e2e/__init__.py`` are themselves stdlib-safe, which is what makes a
sibling module importable where a helper is not.

What is deliberately NOT shared: the request ROUTING and storage semantics. The
capture service records raw wire bytes for the signing suite; the counterparty
service publishes identity documents. Those contracts must stay separable — a
mode flag spanning both is the coupling this split exists to avoid. Only the
transport-level plumbing every JSON origin repeats is here.

:func:`mcp_capabilities_post` is the one exception to "e2e origins only": it is
also imported by ``tests/helpers/signing.py``'s ``json_seeded_client_factory``,
which seeds the same hop over an ``httpx.MockTransport`` instead of a socket. The
import runs backwards from the usual direction (a helper reaching into a suite
package) for the reason this module exists at all — the container half CANNOT
import ``tests.helpers``, so the shared code has to live on the stdlib-only side
of that wall. ``tests/__init__.py`` and ``tests/e2e/__init__.py`` are both
stdlib-safe, which is what makes the import legal in either direction.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class JsonRequestHandler(BaseHTTPRequestHandler):
    """A ``BaseHTTPRequestHandler`` that speaks JSON and keeps quiet.

    Subclasses implement ``do_GET`` / ``do_POST`` / ``do_PUT`` and call
    :meth:`_write_json`; everything below is the framing every one of them
    repeated verbatim.
    """

    protocol_version = "HTTP/1.1"

    def _write_json(self, status: int, body: dict, extra_headers: dict[str, str] | None = None) -> None:
        """Send *body* as JSON with an explicit length and no keep-alive.

        ``Content-Length`` plus ``Connection: close`` rather than chunked: these
        origins are read by ``urllib``, ``httpx`` and the SDK's IP-pinned
        transport, and an explicit length is the one framing all three agree on
        without negotiation.

        *extra_headers* exists for one caller: an MCP endpoint assigns a session
        through the ``Mcp-Session-Id`` RESPONSE header, which is protocol framing
        rather than payload and so has no spelling in *body*. Optional and empty by
        default, so every existing call is unchanged.
        """
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _write_empty(self, status: int) -> None:
        """Send *status* with no body — what the streamable-HTTP binding wants for 202.

        An explicit zero length rather than letting the client guess: the same
        framing argument as :meth:`_write_json`, and a 202 with no
        ``Content-Length`` leaves an HTTP/1.1 keep-alive reader waiting.
        """
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def _read_raw_body(self) -> bytes:
        """The request body exactly as it arrived, or ``b""`` when there is none."""
        content_length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(content_length) if content_length else b""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        """Suppress HTTP server logs during tests."""


#: The handshake version answered when a client sends none. Every mcp client in
#: this repo sends one and gets it echoed, so this only covers a malformed
#: ``initialize``; it is deliberately an OLD version, because a fixture that
#: guessed a NEWER one than the client supports would fail the version check in
#: ``ClientSession.initialize`` instead of simply being refused as malformed.
MCP_FALLBACK_PROTOCOL_VERSION = "2024-11-05"


def mcp_capabilities_post(raw_body: bytes, documents: Mapping[str, dict]) -> tuple[int, dict[str, Any] | None]:
    """Answer one MCP streamable-HTTP POST, serving *documents* as tool results.

    The capabilities hop of ``adcp.signing.agent_resolver.async_resolve_agent`` is
    an MCP ``tools/call`` of ``get_adcp_capabilities`` over the agent's declared
    transport — ``_resolver_protocol.fetch_protocol_capabilities`` opens a real
    ``ClientSession`` against the agent url. It was a raw ``GET`` of a JSON object
    in earlier SDKs, which is what every hop-1 fixture in this repo was built
    around; the SDK now does what AdCP 3.1.1 ``L1/security.mdx``:1142 mandates
    verbatim ("invoke ``get_adcp_capabilities`` via the agent's declared transport
    (MCP ``tools/call`` or A2A skill invocation), **not** a raw HTTP ``GET``"), so
    an origin that answers only ``GET`` now returns 501 to the handshake and the
    walk dies as ``request_signature_capabilities_unreachable`` — the transport
    bucket — masking whatever signature verdict was being graded.

    Four methods, which is the whole surface one session touches:

    * ``initialize`` — answered with the client's OWN ``protocolVersion`` echoed
      back. Negotiating rather than naming a version is what keeps this fixture
      from going stale on an ``mcp`` bump: ``ClientSession.initialize`` rejects any
      version outside its ``HANDSHAKE_PROTOCOL_VERSIONS``, so a literal here would
      be a time bomb set to the next pin.
    * ``notifications/*`` — 202 with no body, per the streamable-HTTP binding.
    * ``tools/list`` — every served document's name, which ``ClientSession.call_tool``
      asks for before it returns.
    * ``tools/call`` — the named document, as BOTH ``structuredContent`` and a
      JSON text part. The SDK's ``_extract_strict_mcp_capabilities`` prefers the
      structured field and falls back to parsing the text part, and serving both
      is what makes this fixture indifferent to which branch it takes.

    No ``Mcp-Session-Id`` is issued, deliberately: the resolver sends a terminating
    ``DELETE`` only when one came back, so omitting it removes a fourth method from
    the surface without changing a single thing the walk reads.

    Returns ``(status, body)`` where a ``None`` body means "send no body" — the
    caller owns the framing, because the two callers frame differently (a socket
    handler versus an ``httpx.Response``).
    """
    try:
        request = json.loads(raw_body) if raw_body else None
    except json.JSONDecodeError:
        request = None
    if not isinstance(request, dict):
        # JSON-RPC 2.0 §5.1 names this one -32700, and the body says so rather than
        # carrying a bare ``{"error": ...}`` that no client parses. The status stays
        # 400: the resolver reads any status >= 400 at hop 1 as
        # ``capabilities_unreachable``, which is the right verdict for a body that
        # never became a request, and a 200 here would invent a protocol-level
        # answer to a transport-level failure.
        return 400, _jsonrpc_error(None, -32700, "parse error: body is not a JSON-RPC object")

    method = request.get("method")
    request_id = request.get("id")

    if isinstance(method, str) and method.startswith("notifications/"):
        return 202, None

    if method == "initialize":
        params = request.get("params")
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        return 200, _jsonrpc_result(
            request_id,
            {
                "protocolVersion": requested if isinstance(requested, str) else MCP_FALLBACK_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "e2e-capabilities-origin", "version": "0"},
            },
        )

    if method == "tools/list":
        # An ``mcp`` ``ClientSession.call_tool`` lists the tools once before it returns a
        # result, to learn the tool's output schema; a method-not-found here fails the
        # call. Every served document is listed with an open input schema and no output
        # schema, so the client validates nothing the document did not already say.
        return 200, _jsonrpc_result(
            request_id, {"tools": [{"name": name, "inputSchema": {"type": "object"}} for name in documents]}
        )

    if method == "tools/call":
        params = request.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        document = documents.get(name) if isinstance(name, str) else None
        if document is None:
            # isError rather than a JSON-RPC error: a tool this origin does not
            # serve is a TOOL outcome, and the resolver reads it as
            # ``capabilities_unreachable`` either way. Naming the tool in the text
            # part is what keeps a mis-wired fixture from looking like a network
            # fault three assertions later.
            return 200, _jsonrpc_result(
                request_id,
                {
                    "content": [{"type": "text", "text": f"no document served for tool {name!r}"}],
                    "isError": True,
                },
            )
        return 200, _jsonrpc_result(
            request_id,
            {
                "content": [{"type": "text", "text": json.dumps(document)}],
                "structuredContent": document,
                "isError": False,
            },
        )

    return 200, _jsonrpc_error(request_id, -32601, f"method not found: {method!r}")


def _jsonrpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    """A JSON-RPC 2.0 success envelope around *result*."""
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    """A JSON-RPC 2.0 error envelope, the one spelling this fixture uses.

    Both call sites used to write the envelope out by hand, which is how the parse
    error came to be answered with a shape no JSON-RPC client reads while the
    method error was answered correctly.
    """
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve_forever_in_thread(
    handler_class: type[BaseHTTPRequestHandler],
    *,
    host: str,
    port: int,
    server_attrs: dict[str, object],
    ssl_context: ssl.SSLContext | None = None,
) -> ThreadingHTTPServer:
    """Bind and start serving on a daemon thread; the caller owns teardown.

    ``request_queue_size`` overrides ``socketserver``'s default backlog of 5 —
    too small for the real concurrency here (many xdist workers under
    ``run_all_tests.sh`` posting and reading at once). Upstream confirmed the
    hard way: a burst of 20 concurrent writers reliably produced
    ``ConnectionResetError`` on a many-core box, and passed on a quieter one —
    exactly how a backlog-too-small bug hides until it doesn't.
    """
    family = socket.getaddrinfo(host, None)[0][0]
    server_class = type("_Server", (ThreadingHTTPServer,), {"address_family": family, "request_queue_size": 128})
    server = server_class((host, port), handler_class)
    for name, value in server_attrs.items():
        setattr(server, name, value)
    if ssl_context is not None:
        # Wrapped before the serving thread starts, so every accepted connection is
        # already negotiated -- the in-process stand-in for the compose TLS front.
        server.socket = ssl_context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def compose_project_name() -> str:
    """This stack's compose project, reported by ``/health``.

    A readback client uses it to prove it is talking to its OWN stack rather than
    a cross-wired sibling on the same host port — a real risk once a readback
    port is published per stack.
    """
    return os.environ.get("COMPOSE_PROJECT_NAME", "")
