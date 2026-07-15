"""Regression test for webhook-capture teardown isolation (#1546 Fix 3).

The e2e webhook-capture receiver previously used a per-connection handler timeout
(30s) LONGER than its teardown join budget (10s + 10s = 20s). A client that opened
a socket and stalled mid-body kept the handler alive past teardown; the daemon
handler could then finish reading and append into the shared capture list AFTER the
next capture context had already cleared it — cross-test contamination the
"entry-clear guarantees isolation" comment wrongly claimed was impossible.

The fix shortens the handler timeout below the join budget AND force-closes every
accepted connection during teardown, so no handler can outlive its context. This
test drives a deterministic client that finishes its body only during the SECOND
context and asserts that late write CANNOT reach the second context.
"""

import contextlib
import socket
import threading
import time

from tests.e2e._webhook_capture import WebhookCaptureHandler, run_webhook_capture_server

_LEAK_BODY = b'{"leak":"from-context-1"}'


class _IsolatedHandler(WebhookCaptureHandler):
    """Own capture list so this test never touches other suites' shared state."""

    received_webhooks: list = []


def _stall_then_complete(host: str, port: int, ready: threading.Event, complete: threading.Event) -> None:
    """Open a POST, send only PART of a valid body, stall until ``complete``, then
    send the rest — completing a valid JSON body. If the handler is still alive at
    that point (the bug), it reads the full body and appends; if the fix force-closed
    the socket at teardown, the send lands on a dead socket and nothing is recorded."""
    conn = socket.create_connection((host, port), timeout=10)
    try:
        headers = (
            b"POST /webhook HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(_LEAK_BODY)).encode() + b"\r\n"
            b"\r\n"
        )
        conn.sendall(headers + _LEAK_BODY[:8])  # partial body — handler blocks in read()
        ready.set()
        complete.wait(timeout=30)
        with contextlib.suppress(OSError):
            conn.sendall(_LEAK_BODY[8:])  # finish the body (only succeeds if socket alive)
        time.sleep(0.3)
    finally:
        with contextlib.suppress(OSError):
            conn.close()


def test_stalled_handler_completing_late_cannot_contaminate_next_context():
    _IsolatedHandler.received_webhooks = []
    ready = threading.Event()
    complete = threading.Event()
    client: threading.Thread | None = None

    # --- Context 1: accept a stalled partial-body connection, then tear down. ---
    teardown_raised = False
    try:
        with run_webhook_capture_server(
            _IsolatedHandler, _IsolatedHandler.received_webhooks, host="127.0.0.1"
        ) as handle:
            port = handle["server"].server_address[1]
            client = threading.Thread(
                target=_stall_then_complete,
                args=("127.0.0.1", port, ready, complete),
                daemon=True,
            )
            client.start()
            assert ready.wait(timeout=5), "client never connected"
            time.sleep(0.3)  # ensure the handler is blocked inside rfile.read()
    except RuntimeError:
        # Task permits the "or raises" branch; the no-contamination invariant must
        # still hold below.
        teardown_raised = True

    # --- Context 2 reuses the SAME handler class + list (cleared on entry). ---
    with run_webhook_capture_server(_IsolatedHandler, _IsolatedHandler.received_webhooks, host="127.0.0.1") as handle2:
        # Let the context-1 client finish its body NOW, inside context 2's lifetime —
        # the exact cross-test contamination window.
        complete.set()
        time.sleep(0.6)
        assert handle2["received"] == [], (
            f"context-1 handler's late write leaked into context 2 "
            f"(teardown_raised={teardown_raised}, list={_IsolatedHandler.received_webhooks})"
        )

    if client is not None:
        client.join(timeout=5)


def test_bound_port_is_reported_from_server_address():
    """Port comes straight off the bound server (no probe-close/rebind race)."""
    with run_webhook_capture_server(_IsolatedHandler, _IsolatedHandler.received_webhooks, host="127.0.0.1") as handle:
        port = handle["server"].server_address[1]
        assert port > 0
        assert handle["url"].endswith(f":{port}/webhook")
