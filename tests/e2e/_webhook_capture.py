"""Shared webhook-capture HTTP receiver for e2e tests.

Several e2e suites stand up a throwaway HTTP server to capture the webhooks the
sales agent posts back (delivery reports, A2A status notifications, reference
async notifications). They all need the same bootstrap: bind a free port, serve
on a daemon thread, hand back the callback URL, and tear the socket down
cleanly afterwards. This is the single implementation of that — kept here
instead of copy-pasted per test (PR #1420 / #1423).
"""

import contextlib
import json
import os
import socket
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Lock, Thread
from typing import Any


class WebhookCaptureHandler(BaseHTTPRequestHandler):
    """Default capture handler: append each POSTed JSON body to ``received_webhooks``.

    Subclass it and give the subclass its own ``received_webhooks`` list so
    captures don't bleed across suites (``do_POST`` reads ``self.received_webhooks``,
    which resolves to the subclass attribute). The a2a status-notification handler
    genuinely differs and is intentionally not folded in here.
    """

    received_webhooks: list = []

    # Per-connection socket timeout (seconds). StreamRequestHandler.setup() applies
    # settimeout() to every accepted connection when this class attr is non-None, so
    # a client that opens a socket and then stalls mid-request cannot wedge the
    # serve_forever loop past teardown's bounded join. This is the idiomatic stdlib
    # lever — NOT HTTPServer.timeout, which only governs handle_request() polling.
    #
    # MUST stay STRICTLY BELOW the teardown join budget (see run_webhook_capture_server:
    # shutdown_signal.join(10) + serve_thread.join(10) = 20s). A handler stalled in a
    # partial-body read aborts at this deadline — well inside that window — so it can
    # never outlive the context and later append into the next test's shared list.
    # (Teardown also force-closes accepted sockets, which unblocks such a read
    # immediately; this timeout is the belt-and-suspenders fallback.)
    timeout = 5

    def do_POST(self):
        """Handle POST requests (webhook notifications)."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            self.received_webhooks.append(json.loads(body.decode("utf-8")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "received"}')
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        """Suppress HTTP server logs during tests."""
        pass


class _ConnectionTrackingHTTPServer(HTTPServer):
    """HTTPServer that remembers every accepted connection so teardown can force it
    closed. Closing a stalled handler's socket from the teardown thread unblocks a
    handler wedged in a partial-body ``rfile.read`` IMMEDIATELY, so no handler can
    outlive the context and later mutate the shared capture list (the cross-test
    contamination the bounded join alone could not prevent)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._active_conns: set[socket.socket] = set()
        self._conns_lock = Lock()

    def get_request(self) -> tuple[socket.socket, Any]:
        conn, addr = super().get_request()
        with self._conns_lock:
            self._active_conns.add(conn)
        return conn, addr

    def shutdown_request(self, request: Any) -> None:
        with self._conns_lock:
            self._active_conns.discard(request)
        super().shutdown_request(request)

    def close_active_connections(self) -> None:
        """Shut down + close every still-open accepted connection.

        ``shutdown(SHUT_RDWR)`` (not merely ``close``) is what forces a peer thread
        blocked in ``recv`` to return, aborting a stalled handler at once.
        """
        with self._conns_lock:
            conns = list(self._active_conns)
            self._active_conns.clear()
        for conn in conns:
            with contextlib.suppress(OSError):
                conn.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                conn.close()


@contextlib.contextmanager
def run_webhook_capture_server(
    handler_class: type[BaseHTTPRequestHandler],
    received: list,
    host: str | None = None,
) -> Iterator[dict]:
    """Run a daemon HTTP receiver on a free port and yield its webhook handle.

    ``handler_class`` records inbound POST bodies into ``received`` (a list it
    mutates in place). ``host`` controls the callback hostname: the default
    honors ``ADCP_WEBHOOK_HOST`` so the server reaches this receiver both on the
    host ('localhost', which the server rewrites to host.docker.internal) and
    in-network (the runner's alias 'tests', left un-rewritten). Pass an explicit
    host (e.g. '127.0.0.1') when the receiver is only reachable on loopback.

    Yields ``{"url", "server", "received"}``. ``received`` is cleared on entry
    and exit so each test sees only its own captures.
    """
    received.clear()

    # A caller may pass a bare handler class without a per-connection timeout;
    # apply the same default so a stalled client cannot wedge teardown regardless
    # of which handler subclass is used. Only set it when unset — never override an
    # explicit choice.
    if getattr(handler_class, "timeout", None) is None:
        handler_class.timeout = 5  # type: ignore[attr-defined]

    # Bind 0.0.0.0 (all interfaces), not 127.0.0.1: the in-network runner reaches
    # this receiver by its compose network alias, so a loopback-only bind would be
    # unreachable from the server container. The callback host (below) is what
    # narrows reachability for loopback-only callers, not the listen address.
    #
    # Bind port 0 DIRECTLY on the server and read back the kernel-assigned port from
    # server.server_address — no probe-socket close/rebind dance (which had a TOCTOU
    # window where another process could grab the port between the probe close and the
    # server bind).
    server = _ConnectionTrackingHTTPServer(("0.0.0.0", 0), handler_class)
    port = server.server_address[1]
    serve_thread = Thread(target=server.serve_forever, daemon=True)
    serve_thread.start()

    webhook_host = host if host is not None else os.getenv("ADCP_WEBHOOK_HOST", "localhost")
    try:
        yield {
            "url": f"http://{webhook_host}:{port}/webhook",
            "server": server,
            "received": received,
        }
    finally:
        # Bound the teardown: shutdown() waits for the serve_forever loop to
        # acknowledge, which can race with a slow/mid-request handler on a
        # loaded CI runner and previously ate the whole 300s pytest-timeout at
        # TEARDOWN of an otherwise-green test. Signal shutdown from a helper
        # thread, give the loop a bounded window, then close the socket
        # regardless — the daemon thread cannot outlive the test process.
        shutdown_signal = Thread(target=server.shutdown, daemon=True)
        shutdown_signal.start()
        # Force-close any accepted connection so a handler stalled in a partial-body
        # read aborts NOW (rather than waiting out its per-connection timeout), letting
        # serve_forever observe the shutdown flag and exit inside the join window below.
        server.close_active_connections()
        shutdown_signal.join(timeout=10)
        serve_thread.join(timeout=10)
        server.server_close()
        # Fail loud if the loop or the shutdown signaller is still alive after the
        # bounded joins: a live handler could still mutate `received`, so we must
        # NOT clear shared state under it (that would race a late append into the
        # NEXT test's captures). The entry-side received.clear() already guarantees
        # next-test isolation even when this raises, so a stuck server surfaces as a
        # loud teardown error instead of silent cross-test contamination.
        if serve_thread.is_alive() or shutdown_signal.is_alive():
            raise RuntimeError(
                "webhook-capture server did not shut down within the bounded teardown window "
                f"(serve_thread alive={serve_thread.is_alive()}, "
                f"shutdown_signal alive={shutdown_signal.is_alive()}) — refusing to clear shared "
                "state under a live handler."
            )
        received.clear()
