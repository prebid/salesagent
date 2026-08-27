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
"""

from __future__ import annotations

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class JsonRequestHandler(BaseHTTPRequestHandler):
    """A ``BaseHTTPRequestHandler`` that speaks JSON and keeps quiet.

    Subclasses implement ``do_GET`` / ``do_POST`` / ``do_PUT`` and call
    :meth:`_write_json`; everything below is the framing every one of them
    repeated verbatim.
    """

    protocol_version = "HTTP/1.1"

    def _write_json(self, status: int, body: dict) -> None:
        """Send *body* as JSON with an explicit length and no keep-alive.

        ``Content-Length`` plus ``Connection: close`` rather than chunked: these
        origins are read by ``urllib``, ``httpx`` and the SDK's IP-pinned
        transport, and an explicit length is the one framing all three agree on
        without negotiation.
        """
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _read_raw_body(self) -> bytes:
        """The request body exactly as it arrived, or ``b""`` when there is none."""
        content_length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(content_length) if content_length else b""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        """Suppress HTTP server logs during tests."""


def serve_forever_in_thread(
    handler_class: type[BaseHTTPRequestHandler],
    *,
    host: str,
    port: int,
    server_attrs: dict[str, object],
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
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def compose_project_name() -> str:
    """This stack's compose project, reported by ``/health``.

    A readback client uses it to prove it is talking to its OWN stack rather than
    a cross-wired sibling on the same host port — a real risk once a readback
    port is published per stack.
    """
    return os.environ.get("COMPOSE_PROJECT_NAME", "")
