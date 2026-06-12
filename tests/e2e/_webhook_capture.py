"""Shared webhook-capture HTTP receiver for e2e tests.

Several e2e suites stand up a throwaway HTTP server to capture the webhooks the
sales agent posts back (delivery reports, A2A status notifications, reference
async notifications). They all need the same bootstrap: bind a free port, serve
on a daemon thread, hand back the callback URL, and tear the socket down
cleanly afterwards. This is the single implementation of that — kept here
instead of copy-pasted per test (PR #1420 / #1423).
"""

import contextlib
import os
import socket
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread


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

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("0.0.0.0", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = HTTPServer(("0.0.0.0", port), handler_class)
    Thread(target=server.serve_forever, daemon=True).start()

    webhook_host = host if host is not None else os.getenv("ADCP_WEBHOOK_HOST", "localhost")
    try:
        yield {
            "url": f"http://{webhook_host}:{port}/webhook",
            "server": server,
            "received": received,
        }
    finally:
        server.shutdown()
        server.server_close()
        received.clear()
