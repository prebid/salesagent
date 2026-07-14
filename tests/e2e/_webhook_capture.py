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
from threading import Thread


class WebhookCaptureHandler(BaseHTTPRequestHandler):
    """Default capture handler: append each POSTed JSON body to ``received_webhooks``.

    Subclass it and give the subclass its own ``received_webhooks`` list so
    captures don't bleed across suites (``do_POST`` reads ``self.received_webhooks``,
    which resolves to the subclass attribute). The a2a status-notification handler
    genuinely differs and is intentionally not folded in here.
    """

    received_webhooks: list = []

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

    # Bind 0.0.0.0 (all interfaces), not 127.0.0.1: the in-network runner reaches
    # this receiver by its compose network alias, so a loopback-only bind would be
    # unreachable from the server container. The callback host (below) is what
    # narrows reachability for loopback-only callers, not the listen address.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("0.0.0.0", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = HTTPServer(("0.0.0.0", port), handler_class)
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
        shutdown_signal.join(timeout=10)
        serve_thread.join(timeout=10)
        server.server_close()
        received.clear()
