"""Webhook capture for e2e tests — the TLS origin, and the loopback fallback.

TWO receivers live here, and which one a suite uses is a statement about what it
can grade.

**The TLS capture origin (preferred).** ``https://webhooks.adcp-e2e.dev:8443`` is
a real HTTPS origin on a non-private address, served by the long-lived
``webhook-capture`` compose service behind the shared ``tls-proxy``. Production's
UNPATCHED ``check_url_ssrf`` accepts it on its own terms, so a delivery graded
there is a delivery to the shape a real receiver has. Deliveries arrive through
the TLS front; the test process reads them back over the plain-HTTP control plane
with :func:`captures` / :func:`assert_capture_service_is_live`, and reconstructs
each one with :func:`captured_delivery`. Requires the compose network — hence
in-network only.

**The in-process plaintext receiver (fallback).** :func:`run_webhook_capture_server`
binds a throwaway socket in the test process. It exists for the one caller that
reaches the live server over HOST loopback and therefore needs a callback the
server can dial back on a loopback literal — it cannot address a compose-network
service at all. Anything graded here is graded over plaintext against a private
address, which is why the guard
``tests/unit/test_architecture_e2e_webhook_receivers_migrated.py`` keeps its
caller list shrink-only.

``host`` is REQUIRED on that fallback. It used to default to
``os.getenv("ADCP_WEBHOOK_HOST", "localhost")``, which silently fell back to
loopback whenever the variable was unset — re-entering the allowance the TLS
origin exists to remove, with no error anywhere. A required parameter turns that
mistake into a TypeError at the call site (salesagent-og9k.12 A2).
"""

import contextlib
import json
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import httpx

from tests.e2e.webhook_capture_service import decode_body, header_value, webhook_path
from tests.helpers.ports import free_port
from tests.helpers.webhook_wire import CapturedWebhook

#: Where the SERVER delivers. A real HTTPS origin, resolvable only inside the
#: compose network, whose address production's SSRF gate accepts unpatched.
CAPTURE_DELIVERY_ORIGIN = "https://webhooks.adcp-e2e.dev:8443"

#: Where the TEST reads captures back. Plain HTTP by the compose service name and
#: deliberately NOT routed through the TLS front: this is the test control plane,
#: not traffic under test, and sending it through the front would make a readback
#: failure look like a delivery failure. Publishes no host port, which is what
#: makes every consumer in-network only.
CAPTURE_READBACK_ORIGIN = "http://webhook-capture:8080"


def delivery_url(key: str, *, echo: str | None = None) -> str:
    """The URL to register so deliveries for *key* land on the TLS capture origin."""
    return f"{CAPTURE_DELIVERY_ORIGIN}{webhook_path(key, echo=echo)}"


def assert_capture_service_is_live() -> None:
    """Fail — never skip — when the receiver is not answering, BEFORE any leg runs.

    Fail-closed assertions elsewhere read "the receiver captured nothing". That
    claim is worthless if the receiver could not have captured anything, and the
    failure would otherwise surface as a passing test.
    """
    try:
        response = httpx.get(f"{CAPTURE_READBACK_ORIGIN}/health", timeout=10.0)
    except httpx.HTTPError as exc:
        raise AssertionError(
            f"the webhook-capture service is not answering at {CAPTURE_READBACK_ORIGIN!r} ({exc!r})"
        ) from exc
    assert response.status_code == 200, f"GET /health must succeed; got HTTP {response.status_code}"
    assert response.json().get("compose_project_name"), (
        "the capture service must report the compose project it belongs to, or a readback cannot tell "
        f"this stack's receiver from a cross-wired sibling's; served {response.json()!r}"
    )


def captures(key: str) -> dict[str, list[dict]]:
    """Everything the capture service recorded under *key*."""
    try:
        response = httpx.get(f"{CAPTURE_READBACK_ORIGIN}{webhook_path(key)}", timeout=10.0)
    except httpx.HTTPError as exc:
        raise AssertionError(
            f"the webhook-capture readback plane is unreachable at {CAPTURE_READBACK_ORIGIN!r} ({exc!r}) — "
            "the compose service is not running, so 'zero captures' below would be true of every leg and "
            "the calling module would grade nothing"
        ) from exc
    assert response.status_code == 200, (
        f"readback of capture key {key!r} must succeed; got HTTP {response.status_code}: {response.text[:300]!r}"
    )
    return response.json()


def captured_delivery(entry: dict) -> CapturedWebhook:
    """One ``received_raw`` entry as the SENDER addressed it.

    The URL is rebuilt as ``https://{Host}{path}`` — never from the URL the test
    registered, and never with an ``http://`` scheme. The tls-proxy terminates TLS
    and forwards ``Host`` verbatim, so the authority an RFC 9421 signature covers
    is the https one, even though the capture service itself only ever sees
    plaintext. Reconstructing it as http fails as ``webhook_signature_invalid``,
    which reads like a crypto bug and is really a reconstruction bug
    (salesagent-og9k.12 A4).
    """
    host = header_value(entry, "host")
    assert host, f"the captured delivery carries no Host header, so its @target-uri cannot be rebuilt: {entry!r}"
    return CapturedWebhook(
        url=f"https://{host}{entry['path']}",
        headers=httpx.Headers(entry["headers"]),
        content=decode_body(entry),
    )


def captured_deliveries(key: str) -> list[CapturedWebhook]:
    """Every delivery recorded under *key*, in arrival order."""
    return [captured_delivery(entry) for entry in captures(key).get("received_raw") or []]


class CaptureHandle:
    """One test's view of the TLS capture origin, keyed to its own capture token.

    The plaintext receiver handed a suite a dict whose lists the server mutated in
    place, so a polling loop could hold ``info["received"]`` and watch it fill. The
    capture service is a different process, so every accessor here RE-READS it —
    that is the one behavioural difference a migrating caller must absorb: bind the
    call inside the poll loop, not before it.

    Key isolation is per-test and opaque, which is what lets modules run
    concurrently against one shared receiver without reading each other's captures.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        self.url = delivery_url(key)

    def raw(self) -> list[dict]:
        """Capture entries exactly as the service recorded them."""
        return captures(self.key).get("received_raw") or []

    def payloads(self) -> list[dict]:
        """Each captured body parsed as JSON, in arrival order."""
        return [json.loads(decode_body(entry)) for entry in self.raw()]

    def deliveries(self) -> list[CapturedWebhook]:
        """Each capture as the sender addressed it, for signature assertions."""
        return [captured_delivery(entry) for entry in self.raw()]


@contextlib.contextmanager
def tls_capture(key_prefix: str) -> Iterator[CaptureHandle]:
    """A capture key on the shared TLS receiver, asserted live before it is used.

    Yields a :class:`CaptureHandle`. The liveness check runs on entry precisely
    because every assertion a caller makes is fail-closed ("nothing was captured"),
    and that claim is worthless if the receiver was never reachable.
    """
    assert_capture_service_is_live()
    yield CaptureHandle(f"{key_prefix}-{uuid.uuid4().hex}")


class WebhookCaptureHandler(BaseHTTPRequestHandler):
    """Default capture handler: append each POSTed JSON body to ``received_webhooks``.

    Subclass it and give the subclass its own ``received_webhooks`` list so
    captures don't bleed across suites (``do_POST`` reads ``self.received_webhooks``,
    which resolves to the subclass attribute). Handlers that store more than the
    raw payload (e.g. the a2a status-notification classifier) override
    :meth:`record` — the HTTP framing is never copied.
    """

    received_webhooks: list = []

    #: Every request as the socket delivered it: ``(url, headers, body_bytes)``.
    #: Class-level like ``received_webhooks``, and for the same reason — a subclass
    #: that declares its own list keeps its captures to itself.
    #:
    #: #1291 C1 (``salesagent-z6nr.18``): RFC 9421 verification needs the exact
    #: bytes AND the ``Signature`` / ``Signature-Input`` / ``Content-Digest``
    #: headers, both of which :meth:`record` discards by design (it maps to a
    #: parsed payload). Recording them here rather than adding a parallel receiver
    #: keeps every e2e suite on one server.
    received_raw: list = []

    def record(self, payload):
        """Map an inbound JSON payload to the entry appended to ``received_webhooks``.

        Subclass hook. A raised exception is answered with a 500 and is visible
        to the test via the sender's delivery failure — never swallowed here.
        """
        return payload

    def record_raw(self, url: str, headers, body: bytes) -> None:
        """Append the untouched request to ``received_raw``.

        Runs BEFORE :meth:`record`, so a handler whose ``record`` rejects a
        payload still leaves the raw request available to explain why.
        """
        self.received_raw.append((url, dict(headers.items()), body))

    def do_POST(self):
        """Handle POST requests (webhook notifications)."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            self.record_raw(self.path, self.headers, body)
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


@contextlib.contextmanager
def run_webhook_capture_server(
    handler_class: type[BaseHTTPRequestHandler],
    received: list,
    host: str,
) -> Iterator[dict]:
    """Run a daemon PLAINTEXT receiver on a free port and yield its webhook handle.

    Prefer the TLS capture origin (:func:`delivery_url` + :func:`captures`). Use
    this only where the caller reaches the live server over HOST loopback and so
    needs a callback address on a loopback literal.

    ``handler_class`` records inbound POST bodies into ``received`` (a list it
    mutates in place). ``host`` is the callback hostname and is REQUIRED: it
    previously defaulted to ``os.getenv("ADCP_WEBHOOK_HOST", "localhost")``, which
    meant an unset variable silently produced a loopback callback and re-entered
    the allowance the TLS origin removes. Passing it explicitly makes the choice
    visible at every call site.

    Yields ``{"url", "server", "received", "received_raw"}``. Both lists are
    cleared on entry and exit so each test sees only its own captures;
    ``received_raw`` carries ``(path, headers, body_bytes)`` for assertions that
    need the wire rather than the parsed payload (signature verification).
    """
    received.clear()
    raw: list = getattr(handler_class, "received_raw", WebhookCaptureHandler.received_raw)
    raw.clear()

    # Bind 0.0.0.0 (all interfaces), not 127.0.0.1: the in-network runner reaches
    # this receiver by its compose network alias, so a loopback-only bind would be
    # unreachable from the server container. The callback host (below) is what
    # narrows reachability for loopback-only callers, not the listen address.
    port = free_port()

    server = HTTPServer(("0.0.0.0", port), handler_class)
    Thread(target=server.serve_forever, daemon=True).start()

    try:
        yield {
            "url": f"http://{host}:{port}/webhook",
            "server": server,
            "received": received,
            "received_raw": raw,
        }
    finally:
        server.shutdown()
        server.server_close()
        received.clear()
        raw.clear()
