"""Long-lived webhook-capture compose service (salesagent-amht.3 + salesagent-mp53.9).

Turns ``tests/e2e/_webhook_capture.py``'s old per-test, ephemeral-port, in-process
TLS receiver into a real network service: fixed name, fixed port, fronted by the
shared ``tls-proxy`` (salesagent-amht.2). The SNI map routes BOTH
``webhooks.adcp.test`` and ``webhooks.adcp-e2e.dev`` to this one upstream, and the
generated leaf certificate covers both names — the two hostnames are two doors onto
the same receiver, not two receivers. That is what a webhook receiver is in
production, and it is what lets production's UNPATCHED ``check_url_ssrf`` accept the
destination on its own terms instead of the stack relaxing the gate to reach a
plaintext private address.

Two traffic patterns, never conflated:

* DELIVERY — ``adcp-server`` (or any sender) POSTs to ``/webhook/<key>``. This is
  the path the shared TLS front terminates.
* READBACK / CONTROL — the test process reads, drains, or programs a key over
  ``GET``/``DELETE /webhook/<key>`` and ``POST /control/<key>``, addressed by
  ``WEBHOOK_CAPTURE_HOST``/``WEBHOOK_CAPTURE_PORT`` (the variables that replaced the
  retired ``ADCP_WEBHOOK_HOST``). Test control-plane, plain HTTP, never routed
  through the TLS front — sending it through the front would make a readback failure
  look like a delivery failure.

This service answers TWO independent needs that both require a REAL receiver, and
neither may be dropped:

* SIGNED-WIRE READBACK (mp53.9). Every delivery is recorded as the wire carried it —
  path, headers verbatim, body BYTES — because RFC 9421's ``content-digest`` covers
  the body bytes and ``Signature``/``Signature-Input`` are the artifacts under test
  (``tests/e2e/test_webhook_signature_e2e.py``). A receiver that stored only
  ``store.append(key, parsed_json)`` would silently gut the signing suite: a
  re-serialized payload has a different digest, and every signature over it fails.
* PROGRAMMABLE REFUSAL (amht.3 / #2060). ``POST /control/<key>`` arms a per-key
  rejection run, so a delivery can be made to FAIL against a real server. Nothing
  else in the compose stack can do that — the capture service answers every delivery
  200 — so without it the deployed server's circuit breaker and the egress refusal
  path are never exercised against anything real.

Storage is keyed by an opaque per-test token (never a single global list) — the
isolation mechanism concurrent e2e modules under xdist depend on.
``ThreadingHTTPServer`` serves each request on its own thread, so the store guards
every read/write with an explicit ``threading.Lock`` rather than relying on GIL
atomicity, and ``DELETE`` drains-and-returns in one atomic round trip (never
read-then-clear as two calls) so a capture landing between the two can never be
silently lost.

``GET /health`` reports this instance's own ``COMPOSE_PROJECT_NAME`` so a readback
client can assert it is talking to its own stack, not a cross-wired sibling on the
same host port (a real risk once the readback port is published per-stack — see
``docker-compose.e2e.ports.yml``).

Runs on a bare ``python:3.12-slim`` image — no project dependencies, no build step
(``Dockerfile.test`` bakes a multi-minute playwright/chromium install this service
has no use for). That constraint is why the shared JSON/HTTP scaffolding lives at
``tests/e2e/_stdlib_json_http`` rather than under ``tests.helpers.``: importing
anything from that package runs its ``__init__``, which has transitively imported
``tests.factories`` (``factory-boy`` et al.) — dev-only dependencies a bare stdlib
image does not have. Upstream confirmed this the hard way: the container exited on
import before ever binding a socket. ``tests/__init__.py`` and
``tests/e2e/__init__.py`` are themselves stdlib-safe, which is what makes a sibling
module importable where a helper is not. Keep this module stdlib-only.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import os
import re
import threading
import time
from collections.abc import Iterator
from urllib.parse import parse_qs

from tests.e2e._stdlib_json_http import JsonRequestHandler, compose_project_name, serve_forever_in_thread

_WEBHOOK_PATH_RE = re.compile(r"^/webhook/(?P<key>[^/]+)/?$")
_CONTROL_PATH_RE = re.compile(r"^/control/(?P<key>[^/]+)/?$")

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

    Each key also carries a REJECTION PROGRAMME (#2060): how many of
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
        self._parsed: dict[str, list[dict]] = {}
        self._raw: dict[str, list[dict]] = {}
        self._programmes: dict[str, tuple[int, int]] = {}

    def _snapshot(self, key: str) -> dict[str, list[dict]]:
        """Both sides of *key*, copied. The caller MUST already hold ``self._lock``."""
        return {
            "received": list(self._parsed.get(key, [])),
            "received_raw": list(self._raw.get(key, [])),
        }

    def program(self, key: str, status: int, count: int) -> None:
        """Answer ``status`` for ``key``'s next ``count`` deliveries, then 200 again.

        Replaces any programme still unspent on that key — a scenario that lets a
        failing endpoint recover reprograms the same key rather than a fresh one,
        and ``count=0`` is how a caller spells "healthy again".
        """
        with self._lock:
            self._programmes[key] = (status, count)

    def append(self, key: str, *, wire: dict, payload: dict | None) -> tuple[int, dict[str, list[dict]]]:
        """Record one delivery; return the status to answer and a snapshot of both sides.

        Always records the wire entry; records the payload only when the body
        parsed. Recording and consuming the programme happen under ONE lock hold,
        so two concurrent deliveries can never both spend the same remaining
        rejection, and the snapshot handed back always already contains this
        delivery.

        A rejected delivery is still recorded. The scenarios count what the
        endpoint received, and a rejection that vanished from the captures would
        be indistinguishable from a delivery the breaker suppressed — which is
        the one distinction those scenarios exist to make.

        A body that did not parse (``payload is None``) does NOT spend the
        programme: the handler already answers it 400, and letting a malformed
        delivery burn a programmed rejection would silently shorten a
        circuit-breaker scenario's run.
        """
        with self._lock:
            self._raw.setdefault(key, []).append(wire)
            status = 200
            if payload is not None:
                self._parsed.setdefault(key, []).append(payload)
                programmed, remaining = self._programmes.get(key, (200, 0))
                if remaining > 0:
                    self._programmes[key] = (programmed, remaining - 1)
                    status = programmed
            return status, self._snapshot(key)

    def get(self, key: str) -> dict[str, list[dict]]:
        with self._lock:
            return self._snapshot(key)

    def drain(self, key: str) -> dict[str, list[dict]]:
        """Atomically read-and-clear both capture sides of ``key`` in one round trip.

        Leaves the rejection programme alone — see the class docstring.
        """
        with self._lock:
            return {
                "received": self._parsed.pop(key, []),
                "received_raw": self._raw.pop(key, []),
            }


class _CaptureRequestHandler(JsonRequestHandler):
    """Route ``/health``, ``/control/<key>`` and ``/webhook/<key>`` against ``self.server``'s store."""

    @property
    def _store(self) -> _CaptureStore:
        return self.server.store  # type: ignore[attr-defined]

    def _wire_entry(self, raw: bytes) -> dict:
        """The exact request as it arrived: path, headers verbatim, body base64, receipt time.

        Header names are kept as sent rather than lower-cased — readers do a
        case-insensitive lookup, and normalizing here would destroy evidence
        about what the sender actually emitted. The body is base64 so bytes that
        are not valid UTF-8 survive the JSON readback hop intact.

        ``received_at`` is a monotonic receipt stamp, and it is the only way a retry
        SCHEDULE is observable across the Docker boundary. The runner's ``env.mock["sleep"]``
        records what THIS process waited; under e2e_rest the sender is the live server, so
        its waits happen in another process entirely and that mock stays empty. What the
        receiver can still see is WHEN each attempt landed, and the gaps between consecutive
        receipts of the same key ARE the waits — measured on the wire rather than
        reconstructed from a patched clock.

        ``time.monotonic`` rather than wall clock deliberately: the gaps are the whole
        content, and a wall clock can step backwards under NTP mid-scenario, which would
        read as a negative wait.
        """
        return {
            "path": self.path,
            "headers": dict(self.headers.items()),
            "body_b64": base64.b64encode(raw).decode("ascii"),
            "received_at": time.monotonic(),
        }

    def _read_json_body(self) -> dict:
        """The request body parsed as JSON. Raises when the body is not a JSON document."""
        raw = self._read_raw_body()
        return json.loads(raw) if raw else {}

    def _handle_health(self) -> None:
        self._write_json(200, {"compose_project_name": self.server.compose_project_name})  # type: ignore[attr-defined]

    def _route(self, method: str) -> None:
        """Split the query off ONCE, then dispatch on the bare path.

        The query is split FIRST because both path patterns are anchored: a
        delivery in echo mode (``/webhook/<key>?echo=challenge``) would otherwise
        404 as if the key were malformed.

        ``/control/<key>`` is matched BEFORE the webhook fallthrough. Every POST
        used to route to the capture handler, so an unmatched control POST landed
        in the store as a delivery and corrupted the very count the
        circuit-breaker Then steps read.
        """
        path, _, query = self.path.partition("?")

        if method == "POST":
            control = _CONTROL_PATH_RE.match(path)
            if control:
                self._handle_control(control.group("key"))
                return

        match = _WEBHOOK_PATH_RE.match(path)
        if not match:
            self._write_json(404, {"error": f"no key in path {self.path!r}"})
            return
        self._handle_webhook(method, match.group("key"), query)

    def _handle_control(self, key: str) -> None:
        """Program ``key``'s rejection run: ``{"status": int, "count": int}``."""
        try:
            body = self._read_json_body()
            status = int(body["status"])
            count = int(body["count"])
        except (KeyError, TypeError, ValueError):
            self._write_json(400, {"error": 'expected {"status": <int>, "count": <int>}'})
            return
        self._store.program(key, status, count)
        self._write_json(200, {"programmed": {"status": status, "count": count}})

    def _handle_webhook(self, method: str, key: str, query: str) -> None:
        if method == "POST":
            self._handle_delivery(key, echo_field=parse_qs(query).get(_ECHO_PARAM, [None])[0])
        elif method == "GET":
            self._write_json(200, self._store.get(key))
        elif method == "DELETE":
            self._write_json(200, self._store.drain(key))

    def _handle_delivery(self, key: str, *, echo_field: str | None = None) -> None:
        """Capture the wire unconditionally; answer 400 if the body was not JSON.

        The capture happens BEFORE the parse decision so an unparseable delivery
        is still evidence — that is what lets a test assert on the bytes a
        malformed sender produced. The error response is deliberate: answering a
        quiet 200 to a body we could not read would let a sender's corruption
        pass as a successful delivery, which is the failure this service exists
        to make visible.

        A parseable delivery is answered with whatever status the key's rejection
        programme still owes (200 when nothing is programmed), which is how a real
        server dialling a real receiver can be made to see a real refusal.

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
            _, snapshot = self._store.append(key, wire=wire, payload=None)
            self._write_json(400, {"error": f"body is not valid JSON: {exc}", **snapshot})
            return
        status, snapshot = self._store.append(key, wire=wire, payload=payload)
        if echo_field is not None:
            self._write_json(status, {echo_field: payload.get(_CHALLENGE_FIELD)})
            return
        self._write_json(status, snapshot)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
        if self.path == "/health":
            self._handle_health()
        else:
            self._route("GET")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
        self._route("POST")

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler name
        self._route("DELETE")


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
def run_capture_service(*, host: str = "0.0.0.0", port: int = 8080) -> Iterator[str]:  # noqa: S104
    """Run the webhook-capture service, yielding its base URL (``http://host:port``).

    The ``0.0.0.0`` default is the CONTAINER's network namespace, not the host's:
    sibling compose services (``tls-proxy`` forwarding a terminated delivery, and
    any in-network readback client) reach this receiver by its network alias, so a
    loopback-only bind inside the container would be unreachable to every one of
    them. Nothing here publishes a host port — the only host-visible exposure is
    ``docker-compose.e2e.ports.yml``'s
    ``127.0.0.1:${WEBHOOK_CAPTURE_PORT:-8090}:8080``, which is loopback-scoped.
    In-process callers (the contract test) pass ``host="127.0.0.1"`` explicitly.

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
    with run_capture_service(host="0.0.0.0", port=port):  # noqa: S104 - container namespace; see run_capture_service
        threading.Event().wait()


if __name__ == "__main__":
    main()
