"""Long-lived webhook-capture compose service (GH #1802).

Turns ``tests/e2e/_webhook_capture.py``'s old per-test, ephemeral-port,
in-process TLS receiver into a real network service: fixed name, fixed port,
fronted by the shared ``tls-proxy`` (GH #1802) at
``webhooks.adcp.test``. That is what a webhook receiver is in production.

Two traffic patterns, never conflated:

* DELIVERY — ``adcp-server`` (or any sender) POSTs a captured payload to
  ``/webhook/<key>``. This is the path the shared TLS front terminates.
* READBACK — the test process reads/drains a key's captures over
  ``GET``/``DELETE /webhook/<key>``. This is test control-plane, plain HTTP,
  never routed through the TLS front (see ``tests/e2e/_webhook_capture.py``).

Storage is a ``dict[key, list[payload]]`` keyed by an opaque per-test token
(never a single global list) — the isolation mechanism concurrent e2e modules
under xdist depend on. ``ThreadingHTTPServer`` serves each request on its own
thread, so the store guards every read/write with an explicit
``threading.Lock`` rather than relying on GIL atomicity as the only safety
net, and ``DELETE`` drains-and-returns a key's list in one atomic round trip
(never a separate read-then-clear) so a capture landing between the two can
never be silently lost.

``GET /health`` reports this instance's own ``COMPOSE_PROJECT_NAME`` so a
readback client can assert it is talking to its own stack, not a cross-wired
sibling on the same host port (a real risk once the readback port is
published per-stack — see ``docker-compose.e2e.ports.yml``).

Runs in the compose service on a bare ``python:3.12-slim`` image — no project
dependencies, no build step (see docker-compose.e2e.yml's ``webhook-capture``
service comment for why: ``Dockerfile.test`` bakes a multi-minute
playwright/chromium install this service has no use for). That constraint once forced
the thread-serving bootstrap to be INLINED rather than imported from
``tests.helpers.local_http_origin.serve_in_thread``, the otherwise-obvious reuse
target: importing anything under ``tests.helpers`` ran its package
``__init__.py``, which eagerly imported ``tests.factories`` (``factory-boy`` et
al.) — dev-only dependencies a bare stdlib image does not have, and the
container exited on import before ever binding a socket. That package now
re-exports lazily, so the leaf helper imports with nothing but the stdlib and
the copy is gone. This module and its two package ``__init__.py`` files stay
stdlib-only, so ``python -m tests.e2e.webhook_capture_service`` still never
touches the heavy chain.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler

from tests.helpers.local_http_origin import serve_in_thread

_WEBHOOK_PATH_RE = re.compile(r"^/webhook/(?P<key>[^/]+)/?$")
_CONTROL_PATH_RE = re.compile(r"^/control/(?P<key>[^/]+)/?$")


class _CaptureStore:
    """Thread-safe, per-key capture storage — a dict-of-lists, never one global list.

    Also holds each key's REJECTION PROGRAMME (GH #1802): how many of
    the next deliveries to that key answer a non-200 status. Nothing else in the
    compose stack can make the deployed server's delivery path fail, so without
    this the server's circuit breaker never records a real failure.

    Programmes live in their own dict, deliberately: ``DELETE`` drains a key's
    captures as a READBACK of what arrived, and must not double as a reset of how
    the endpoint answers. A scenario that opens the breaker, drains, then keeps
    delivering depends on the unspent programme surviving the drain.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._captures: dict[str, list[dict]] = {}
        self._programmes: dict[str, tuple[int, int]] = {}

    def program(self, key: str, status: int, count: int) -> None:
        """Answer ``status`` for ``key``'s next ``count`` deliveries, then 200 again.

        Replaces any programme still unspent on that key — a scenario that lets a
        failing endpoint recover reprograms the same key rather than a fresh one.
        """
        with self._lock:
            self._programmes[key] = (status, count)

    def append(self, key: str, payload: dict) -> tuple[int, list[dict]]:
        """Record ``payload`` under ``key``; return the status to answer and the captures so far.

        Recording and consuming the programme happen under ONE lock hold, so two
        concurrent deliveries can never both spend the same remaining rejection.

        A rejected delivery is still recorded. The scenarios count what the
        endpoint received, and a rejection that vanished from the captures would
        be indistinguishable from a delivery the breaker suppressed — which is
        the one distinction those scenarios exist to make.
        """
        with self._lock:
            bucket = self._captures.setdefault(key, [])
            bucket.append(payload)
            status, remaining = self._programmes.get(key, (200, 0))
            if remaining > 0:
                self._programmes[key] = (status, remaining - 1)
            else:
                status = 200
            return status, list(bucket)

    def get(self, key: str) -> list[dict]:
        with self._lock:
            return list(self._captures.get(key, []))

    def drain(self, key: str) -> list[dict]:
        """Atomically read-and-clear ``key`` in one round trip — never list-then-clear as two calls."""
        with self._lock:
            return self._captures.pop(key, [])


class _CaptureRequestHandler(BaseHTTPRequestHandler):
    """Route ``/health`` and ``/webhook/<key>`` against ``self.server``'s store."""

    protocol_version = "HTTP/1.1"

    def _write_json(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(content_length) if content_length else b""
        return json.loads(raw) if raw else {}

    def _handle_health(self) -> None:
        self._write_json(200, {"compose_project_name": self.server.compose_project_name})  # type: ignore[attr-defined]

    def _handle_webhook(self, method: str) -> None:
        match = _WEBHOOK_PATH_RE.match(self.path)
        if not match:
            self._write_json(404, {"error": f"no key in path {self.path!r}"})
            return
        key = match.group("key")
        store: _CaptureStore = self.server.store  # type: ignore[attr-defined]

        if method == "POST":
            payload = self._read_json_body()
            status, received = store.append(key, payload)
            self._write_json(status, {"received": received})
        elif method == "GET":
            self._write_json(200, {"received": store.get(key)})
        elif method == "DELETE":
            self._write_json(200, {"received": store.drain(key)})

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
        if self.path == "/health":
            self._handle_health()
        else:
            self._handle_webhook("GET")

    def _handle_control(self, match: re.Match[str]) -> None:
        """Program ``key``'s rejection run: ``{"status": int, "count": int}``."""
        body = self._read_json_body()
        try:
            status = int(body["status"])
            count = int(body["count"])
        except (KeyError, TypeError, ValueError):
            self._write_json(400, {"error": 'expected {"status": <int>, "count": <int>}'})
            return
        store: _CaptureStore = self.server.store  # type: ignore[attr-defined]
        store.program(match.group("key"), status, count)
        self._write_json(200, {"programmed": {"status": status, "count": count}})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
        # Matched BEFORE the webhook fallthrough. Every POST used to route to the
        # capture handler, so an unmatched control POST would land in the store as
        # a delivery and corrupt the very count the circuit-breaker Then steps read.
        control = _CONTROL_PATH_RE.match(self.path)
        if control:
            self._handle_control(control)
            return
        self._handle_webhook("POST")

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler name
        self._handle_webhook("DELETE")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        """Suppress HTTP server logs during tests."""


@contextlib.contextmanager
def run_capture_service(*, host: str = "0.0.0.0", port: int = 8080) -> Iterator[str]:
    """Run the webhook-capture service, yielding its base URL (``http://host:port``).

    Reads ``COMPOSE_PROJECT_NAME`` once at start so ``GET /health`` can report
    which compose stack this instance belongs to.
    """
    compose_project_name = os.environ.get("COMPOSE_PROJECT_NAME", "")
    with serve_in_thread(
        _CaptureRequestHandler,
        listen_host=host,
        port=port,
        server_attrs={"store": _CaptureStore(), "compose_project_name": compose_project_name},
    ) as server:
        actual_port = server.server_address[1]
        yield f"http://{host}:{actual_port}"


def main() -> None:
    """Entry point for ``python -m tests.e2e.webhook_capture_service`` inside the compose service."""
    port = int(os.environ.get("PORT", "8080"))
    with run_capture_service(host="0.0.0.0", port=port):
        threading.Event().wait()


if __name__ == "__main__":
    main()
