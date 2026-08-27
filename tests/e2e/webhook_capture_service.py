"""Long-lived webhook-capture compose service (salesagent-mp53.9).

Ported from ``feat/secure-outbound-fetch`` @ ``b22ec6f9d`` (salesagent-amht.3),
EXTENDED here to record the wire — headers and raw body bytes — not just the
parsed payload. The upstream service stored ``store.append(key, parsed_json)``
and dropped everything else; this branch's RFC 9421 work cannot use that, because
``content-digest`` covers the body BYTES and ``Signature``/``Signature-Input``
are the artifacts under test (see ``tests/e2e/test_webhook_signature_e2e.py``).
Porting it verbatim would have silently gutted the signing e2e suite.

Turns the old per-test, ephemeral-port, in-process receiver into a real network
service: fixed name, fixed port, fronted by the shared ``tls-proxy`` at
``webhooks.adcp-e2e.dev``. That is what a webhook receiver is in production —
and it is what lets ``check_url_ssrf`` accept the destination on its own terms
instead of the stack relaxing the gate to reach a plaintext private address.

Two traffic patterns, never conflated:

* DELIVERY — ``adcp-server`` (or any sender) POSTs to ``/webhook/<key>``. This
  is the path the shared TLS front terminates.
* READBACK — the test process reads/drains a key's captures over
  ``GET``/``DELETE /webhook/<key>``. Test control-plane, plain HTTP, never
  routed through the TLS front.

Storage is keyed by an opaque per-test token (never a single global list) — the
isolation mechanism concurrent e2e modules under xdist depend on.
``ThreadingHTTPServer`` serves each request on its own thread, so the store
guards every read/write with an explicit ``threading.Lock`` rather than relying
on GIL atomicity, and ``DELETE`` drains-and-returns in one atomic round trip
(never read-then-clear as two calls) so a capture landing between the two can
never be silently lost.

``GET /health`` reports this instance's own ``COMPOSE_PROJECT_NAME`` so a
readback client can assert it is talking to its own stack, not a cross-wired
sibling on the same host port.

Runs on a bare ``python:3.12-slim`` image — no project dependencies, no build
step. That constraint is why the shared JSON/HTTP scaffolding lives at
``tests/e2e/_stdlib_json_http`` rather than under ``tests.helpers.``: importing
anything from that package runs its ``__init__``, which transitively imports
``tests.factories`` (``factory-boy`` et al.) — dev-only dependencies a bare
stdlib image does not have. Upstream confirmed this the hard way: the container
exited on import before ever binding a socket. Keep this module stdlib-only.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import os
import re
import threading
from collections.abc import Iterator
from urllib.parse import parse_qs

from tests.e2e._stdlib_json_http import JsonRequestHandler, compose_project_name, serve_forever_in_thread

_WEBHOOK_PATH_RE = re.compile(r"^/webhook/(?P<key>[^/]+)/?$")

#: The query parameter that puts one delivery into ECHO mode (salesagent-mp53.4).
_ECHO_PARAM = "echo"

#: The REQUEST field carrying the value to echo. Always this one, whatever the
#: response is spelled as: the spec defines the challenge document's field, and
#: the two accepted RESPONSE spellings ("challenge" / "token") are a property of
#: the answer, not of what the receiver reads.
_CHALLENGE_FIELD = "challenge"


def webhook_path(key: str, *, echo: str | None = None) -> str:
    """The delivery path for *key*, optionally in echo mode.

    Echo mode is named IN THE URL rather than configured on the service, and that
    is the whole point: proof-of-control is decided by what the receiver answers
    to ONE challenge, so "echoes" and "does not echo" must be two different
    destinations a registration can point at — not two states of one receiver
    that a test flips between. A side channel would let the echoing and
    non-echoing legs race each other, and would make the non-echo control
    ("answers 2xx WITHOUT the echo must NOT activate") depend on ordering.

    The receiver stays honest either way: it captures the wire identically in
    both modes, so a challenge is recorded whether or not it is echoed back.
    """
    return f"/webhook/{key}" if echo is None else f"/webhook/{key}?{_ECHO_PARAM}={echo}"


class _CaptureStore:
    """Thread-safe per-key storage of BOTH the parsed payload and the raw wire.

    The two are kept in step deliberately: a body that fails to parse still
    records a wire entry, so ``received_raw`` is the complete record of what
    arrived and ``received`` is only the subset that was valid JSON. A caller
    verifying a signature reads the raw side; a caller asserting on fields reads
    the parsed side.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._parsed: dict[str, list[dict]] = {}
        self._raw: dict[str, list[dict]] = {}

    def append(self, key: str, *, wire: dict, payload: dict | None) -> None:
        """Record one delivery: always its wire entry, and its payload if it parsed."""
        with self._lock:
            self._raw.setdefault(key, []).append(wire)
            if payload is not None:
                self._parsed.setdefault(key, []).append(payload)

    def get(self, key: str) -> dict[str, list[dict]]:
        with self._lock:
            return {
                "received": list(self._parsed.get(key, [])),
                "received_raw": list(self._raw.get(key, [])),
            }

    def drain(self, key: str) -> dict[str, list[dict]]:
        """Atomically read-and-clear both sides of ``key`` in one round trip."""
        with self._lock:
            return {
                "received": self._parsed.pop(key, []),
                "received_raw": self._raw.pop(key, []),
            }


class _CaptureRequestHandler(JsonRequestHandler):
    """Route ``/health`` and ``/webhook/<key>`` against ``self.server``'s store."""

    def _wire_entry(self, raw: bytes) -> dict:
        """The exact request as it arrived: path, headers verbatim, body base64.

        Header names are kept as sent rather than lower-cased — readers do a
        case-insensitive lookup, and normalizing here would destroy evidence
        about what the sender actually emitted. The body is base64 so bytes that
        are not valid UTF-8 survive the JSON readback hop intact.
        """
        return {
            "path": self.path,
            "headers": dict(self.headers.items()),
            "body_b64": base64.b64encode(raw).decode("ascii"),
        }

    def _handle_health(self) -> None:
        self._write_json(200, {"compose_project_name": self.server.compose_project_name})  # type: ignore[attr-defined]

    def _handle_webhook(self, method: str) -> None:
        # Split the query FIRST: the path regex is anchored, so a delivery in
        # echo mode (`/webhook/<key>?echo=challenge`) would otherwise 404 as if
        # the key were malformed.
        path, _, query = self.path.partition("?")
        match = _WEBHOOK_PATH_RE.match(path)
        if not match:
            self._write_json(404, {"error": f"no key in path {self.path!r}"})
            return
        key = match.group("key")
        store: _CaptureStore = self.server.store  # type: ignore[attr-defined]

        if method == "POST":
            self._handle_delivery(key, store, echo_field=parse_qs(query).get(_ECHO_PARAM, [None])[0])
        elif method == "GET":
            self._write_json(200, store.get(key))
        elif method == "DELETE":
            self._write_json(200, store.drain(key))

    def _handle_delivery(self, key: str, store: _CaptureStore, *, echo_field: str | None = None) -> None:
        """Capture the wire unconditionally; answer 400 if the body was not JSON.

        The capture happens BEFORE the parse decision so an unparseable delivery
        is still evidence — that is what lets a test assert on the bytes a
        malformed sender produced. The error response is deliberate: answering a
        quiet 200 to a body we could not read would let a sender's corruption
        pass as a successful delivery, which is the failure this service exists
        to make visible.

        In ECHO mode (salesagent-mp53.4) the answer carries the challenge value
        read back OUT OF THE POSTED BODY — never a value the receiver was
        configured with. A receiver that echoed a preloaded constant would answer
        correctly to a challenge it never actually read, which is precisely the
        proof-of-control failure the echo exists to detect.

        ``?echo=`` names only the RESPONSE key: the spec lets a receiver answer
        ``{"challenge": ...}`` or ``{"token": ...}`` and the SDK's
        ``validate_webhook_challenge_response`` accepts either, so both spellings
        must be exercisable. The value is always read from the request's
        ``challenge`` field, because that is the one field the spec says the
        receiver echoes — keying the read off the response spelling instead would
        make the ``token`` leg answer ``null`` and pass only because nothing
        compared it.
        """
        raw = self._read_raw_body()
        wire = self._wire_entry(raw)
        try:
            payload = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            store.append(key, wire=wire, payload=None)
            self._write_json(400, {"error": f"body is not valid JSON: {exc}", **store.get(key)})
            return
        store.append(key, wire=wire, payload=payload)
        if echo_field is not None:
            self._write_json(200, {echo_field: payload.get(_CHALLENGE_FIELD)})
            return
        self._write_json(200, store.get(key))

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
        if self.path == "/health":
            self._handle_health()
        else:
            self._handle_webhook("GET")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
        self._handle_webhook("POST")

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler name
        self._handle_webhook("DELETE")


def decode_body(entry: dict) -> bytes:
    """The raw request bytes from a ``received_raw`` entry.

    Exported so readback callers verifying an RFC 9421 ``content-digest`` do not
    each re-derive the base64 hop this service uses to carry non-UTF-8 bytes
    through JSON.
    """
    try:
        return base64.b64decode(entry["body_b64"], validate=True)
    except (KeyError, binascii.Error) as exc:
        raise ValueError(f"capture entry has no decodable body_b64: {entry!r}") from exc


def header_value(entry: dict, name: str) -> str | None:
    """Case-insensitive header lookup on a ``received_raw`` entry.

    Header names are stored as the sender emitted them; every HTTP client treats
    them case-insensitively, so readers must too.
    """
    lowered = name.lower()
    for key, value in entry.get("headers", {}).items():
        if key.lower() == lowered:
            return value
    return None


@contextlib.contextmanager
def run_capture_service(*, host: str = "0.0.0.0", port: int = 8080) -> Iterator[str]:
    """Run the webhook-capture service, yielding its base URL (``http://host:port``).

    Reads ``COMPOSE_PROJECT_NAME`` once at start so ``GET /health`` can report
    which compose stack this instance belongs to.
    """
    server = serve_forever_in_thread(
        _CaptureRequestHandler,
        host=host,
        port=port,
        server_attrs={"store": _CaptureStore(), "compose_project_name": compose_project_name()},
    )
    try:
        yield f"http://{host}:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    """Entry point for ``python -m tests.e2e.webhook_capture_service`` inside the compose service."""
    port = int(os.environ.get("PORT", "8080"))
    with run_capture_service(host="0.0.0.0", port=port):
        threading.Event().wait()


if __name__ == "__main__":
    main()
