"""In-process loopback webhook receiver for HERMETIC (no-Docker-stack) tests only.

Relocated out of ``tests/e2e/_webhook_capture.py`` by GH #1802, which
moved the compose-coupled receiver to a real long-lived service
(``tests/e2e/webhook_capture_service.py``) fronted by the shared ``tls-proxy``.
That migration does not apply here: a hermetic test (no Docker stack, no
database — e.g. ``test_a2a_webhook_payload_types.py::TestProtocolWebhookWireFormat``,
whose sender is an in-process ``ProtocolWebhookService`` running on the host)
cannot resolve ``webhooks.adcp.test`` at all — that name is Docker-embedded-DNS,
unresolvable outside the compose network. This module is the one place left
that still terminates TLS in-process, by design, for exactly that class of
test (owner scope decision, 2026-08-05, recorded in GH #1802).

Everything here is unchanged from before the split — only the file moved.
"""

import contextlib
import json
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler

from tests.helpers.local_http_origin import serve_in_thread
from tests.helpers.test_tls_material import load_gen_test_tls, server_ssl_context


class WebhookCaptureHandler(BaseHTTPRequestHandler):
    """Default capture handler: append each POSTed JSON body to ``received_webhooks``.

    Subclass it and give the subclass its own ``received_webhooks`` list so
    captures don't bleed across suites (``do_POST`` reads ``self.received_webhooks``,
    which resolves to the subclass attribute). Handlers that store more than the
    raw payload (e.g. the a2a status-notification classifier) override
    :meth:`record` — the HTTP framing is never copied.
    """

    received_webhooks: list = []

    def record(self, payload):
        """Map an inbound JSON payload to the entry appended to ``received_webhooks``.

        Subclass hook. A raised exception is answered with a 500 and is visible
        to the test via the sender's delivery failure — never swallowed here.
        """
        return payload

    def do_POST(self):
        """Handle POST requests (webhook notifications)."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            self.received_webhooks.append(self.record(json.loads(body.decode("utf-8"))))
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


def _tls_context_for(webhook_host: str):
    """A server-side TLS context for ``webhook_host``, or ``None`` if it can't verify.

    Trusts exactly the names the generated CA's SAN actually covers
    (``scripts/dev/gen_test_tls.py`` is the single source of that list — never
    duplicated here): the ``*.adcp.test``/``*.localhost`` wildcards, the exact
    DNS names, and the loopback IP SANs. Any other host gets no cert and keeps
    serving plain http, unchanged. This module now serves loopback-only
    callers (``host="127.0.0.1"``), so the covered-host check is a safety net,
    not the load-bearing dispatch it was when this also handled the in-network
    callback host.
    """
    gen_test_tls = load_gen_test_tls()
    covered_exact = set(gen_test_tls.SAN_DNS_NAMES) | set(gen_test_tls.SAN_IP_ADDRESSES)
    is_covered = webhook_host in covered_exact or any(
        name.startswith("*.") and webhook_host.endswith(name[1:]) for name in gen_test_tls.SAN_DNS_NAMES
    )
    if not is_covered:
        return None
    gen_test_tls.ensure_test_tls()
    return server_ssl_context(gen_test_tls)


@contextlib.contextmanager
def run_webhook_capture_server(
    handler_class: type[BaseHTTPRequestHandler],
    received: list,
    host: str = "127.0.0.1",
) -> Iterator[dict]:
    """Run a daemon HTTP receiver on a free port and yield its webhook handle.

    HERMETIC CALLERS ONLY (no Docker stack) — see module docstring. ``host``
    defaults to loopback since that is the only caller left; pass a different
    loopback-covered name/IP if a hermetic test needs one.

    ``handler_class`` records inbound POST bodies into ``received`` (a list it
    mutates in place). Serves https (real TLS, verification ON) when the
    generated CA covers ``host`` (the loopback names/IPs always are); plain
    http otherwise.

    Yields ``{"url", "server", "received"}``. ``received`` is cleared on entry
    and exit so each test sees only its own captures.
    """
    received.clear()

    ssl_context = _tls_context_for(host)
    scheme = "https" if ssl_context is not None else "http"

    with serve_in_thread(handler_class, listen_host=host, ssl_context=ssl_context) as server:
        port = server.server_address[1]
        try:
            yield {
                "url": f"{scheme}://{host}:{port}/webhook",
                "server": server,
                "received": received,
            }
        finally:
            received.clear()
