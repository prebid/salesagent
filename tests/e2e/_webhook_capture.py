"""Test-side client for the compose ``webhook-capture`` service.

Both branches converged on the same architecture and neither half may be
dropped: deliveries land on the long-lived ``webhook-capture`` compose service
(``tests/e2e/webhook_capture_service.py``) fronted by the shared ``tls-proxy``,
and the test process reads them back over a PLAIN-HTTP control plane. This
module is the only place that spells either address.

Two traffic patterns, never conflated:

* DELIVERY — the sales agent POSTs to ``https://webhooks.…:8443/webhook/<key>``.
  A real HTTPS origin on a non-private address, so production's UNPATCHED
  ``check_url_ssrf`` accepts it on its own terms instead of the stack relaxing
  the gate to reach a plaintext private address. TLS terminates at the shared
  front; this module never runs a TLS server of its own.
* READBACK / CONTROL — this test process reads, drains, or programs its own key
  over plain HTTP, straight at the service. Test control-plane, NOT part of the
  graded TLS/egress surface: routing it through the front would make a readback
  failure look like a delivery failure.

TWO ADDRESSING FLAVORS, ONE RECEIVER — and the difference is reachability, not
identity. ``config/nginx/nginx-tls-test.conf.template`` maps BOTH
``webhooks.adcp-e2e.dev`` and ``webhooks.adcp.test`` to the same
``webhook-capture:8080`` upstream, and the generated leaf covers both names
(``*.adcp-e2e.dev`` and ``*.adcp.test`` are both in the SAN), so the two
hostnames are two doors onto one store:

* :func:`delivery_url` / :func:`captures` — delivery under ``.adcp-e2e.dev``
  (the namespace the SERVER dials outbound, whose reserved-TLD gate in
  ``notification_proof_service`` refuses ``.test``), read back at the compose
  service NAME (:data:`CAPTURE_READBACK_ORIGIN`). Publishes no host port, so
  every consumer of this flavor is IN-NETWORK ONLY.
* :func:`delivery_url_for` / :class:`ReceivedView` — delivery under
  ``.adcp.test``, read back at ``WEBHOOK_CAPTURE_HOST``/``WEBHOOK_CAPTURE_PORT``
  (the variables that replaced the retired ``ADCP_WEBHOOK_HOST``), which
  ``docker-compose.e2e.ports.yml`` publishes loopback-scoped. Reachable from the
  HOST as well as in-network.

Because the store is one flat dict keyed by an opaque token, the hostname a
delivery used never affects which bucket it lands in — only the KEY does. A
caller that mints a key through one flavor and reads back through the other
reads a bucket nothing was ever delivered to, and the service SYNTHESIZES
``{"received": [], "received_raw": []}`` for a key it has never seen (see
``_CaptureStore.get``), so the mistake answers 200/empty rather than failing.
Keep a key's minting, its delivery address, and its readback on ONE flavor.

The in-process plaintext receiver is NOT here. It moved to
``tests/e2e/_webhook_capture_loopback.py`` for the one class of caller a compose
service cannot serve: a HERMETIC test (no Docker stack) whose sender runs on the
host and cannot resolve a Docker-embedded-DNS name at all. Anything that can
reach the compose network belongs on this module instead — that is what the
guard ``tests/unit/test_architecture_e2e_webhook_receivers_migrated.py`` keeps
shrink-only.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from collections.abc import Iterator

import httpx

from tests.e2e.webhook_capture_service import decode_body, header_value, webhook_path
from tests.helpers.webhook_wire import CapturedWebhook

#: Where the SERVER delivers under the outbound-origin namespace. Read back at
#: :data:`CAPTURE_READBACK_ORIGIN`.
CAPTURE_DELIVERY_ORIGIN = "https://webhooks.adcp-e2e.dev:8443"

#: Where the SERVER delivers under the in-stack namespace. Same upstream as
#: :data:`CAPTURE_DELIVERY_ORIGIN` (see the module docstring's SNI note); the
#: distinguishing property is that its readback plane is host-reachable.
ALT_CAPTURE_DELIVERY_ORIGIN = "https://webhooks.adcp.test:8443"

#: Where the TEST reads captures back, by compose service name. Plain HTTP and
#: deliberately NOT routed through the TLS front: this is the test control
#: plane, not traffic under test. Publishes no host port, which is what makes
#: every consumer of this spelling in-network only.
CAPTURE_READBACK_ORIGIN = "http://webhook-capture:8080"

#: The more generous of the two merged timeouts. A readback that times out fails
#: LOUDLY either way, so the tighter value bought no signal and only risked a
#: flake when several stacks share a box under xdist.
_READBACK_TIMEOUT_SECONDS = 10.0


class WebhookReadbackError(RuntimeError):
    """The webhook-capture readback control-plane failed or answered wrong.

    Raised loudly rather than degrading to an empty capture list, which would
    read as "no webhook arrived" (No Quiet Failures) when the actual cause is a
    transport error, a service that never started, or a cross-wired sibling
    stack on the same published readback port.
    """


def _readback_base_url() -> str:
    """The host-reachable readback origin for the ``.adcp.test`` flavor."""
    host = os.getenv("WEBHOOK_CAPTURE_HOST", "localhost")
    port = os.getenv("WEBHOOK_CAPTURE_PORT", "8080")
    return f"http://{host}:{port}"


def _readback(base_url: str, method: str, path: str, *, body: dict | None = None) -> httpx.Response:
    """One readback/control round trip against a capture-service base URL.

    ONE implementation for both addressing flavors: they differ only in which
    base URL reaches the service, never in how a round trip is made or in what a
    failure means. A non-2xx answer raises here rather than being handed back,
    so no caller can mistake an error page for an empty capture list.
    """
    url = f"{base_url}{path}"
    try:
        response = httpx.request(method, url, json=body, timeout=_READBACK_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise WebhookReadbackError(f"webhook-capture readback {method} {url} failed: {exc!r}") from exc
    if response.status_code != 200:
        raise WebhookReadbackError(
            f"webhook-capture readback {method} {url} answered HTTP {response.status_code}: {response.text[:300]!r}"
        )
    return response


def _readback_json(base_url: str, method: str, path: str, *, body: dict | None = None) -> dict:
    """:func:`_readback`, decoded. The service answers a JSON object on every route."""
    return dict(_readback(base_url, method, path, body=body).json())


def _assert_health(base_url: str) -> None:
    """The receiver at *base_url* answers AND belongs to THIS compose stack.

    Both halves are load-bearing, and they came from opposite branches:

    * a receiver that reports NO project name cannot be told apart from a
      cross-wired sibling at all, so an unnamed one is refused outright;
    * when ``COMPOSE_PROJECT_NAME`` is set, the name must MATCH — the published
      readback port is per-stack and a concurrent stack answering on it would
      hand back a confident, wrong, empty capture list.

    Checked before any leg runs, because every assertion a caller makes is
    fail-closed ("nothing was captured") and that claim is worthless if the
    receiver could not have captured anything.
    """
    body = _readback_json(base_url, "GET", "/health")
    actual = body.get("compose_project_name", "")
    if not actual:
        raise WebhookReadbackError(
            f"the capture service at {base_url!r} reports no compose project, so a readback cannot tell "
            f"this stack's receiver from a cross-wired sibling's; served {body!r}"
        )
    expected = os.environ.get("COMPOSE_PROJECT_NAME", "")
    if expected and actual != expected:
        raise WebhookReadbackError(
            f"webhook-capture at {base_url!r} belongs to stack {actual!r}, expected {expected!r} — "
            "cross-wired readback port from a concurrent stack"
        )


# ── The ``.adcp-e2e.dev`` flavor: in-network delivery + in-network readback ──


def delivery_url(key: str, *, echo: str | None = None) -> str:
    """The URL to register so deliveries for *key* land on the TLS capture origin.

    ``echo`` names the RESPONSE spelling for a proof-of-control challenge; see
    :func:`tests.e2e.webhook_capture_service.webhook_path` for why the mode is
    part of the ADDRESS rather than a state the receiver is flipped between.
    """
    return f"{CAPTURE_DELIVERY_ORIGIN}{webhook_path(key, echo=echo)}"


def assert_capture_service_is_live() -> None:
    """Fail — never skip — when the receiver is not answering, BEFORE any leg runs.

    Translates to ``AssertionError`` deliberately: the callers of this spelling
    state it as a precondition of their own assertions, and a pytest report that
    names it an assertion failure reads as the setup claim it is.
    """
    try:
        _assert_health(CAPTURE_READBACK_ORIGIN)
    except WebhookReadbackError as exc:
        raise AssertionError(
            f"the webhook-capture service is not answering at {CAPTURE_READBACK_ORIGIN!r}: {exc}"
        ) from exc


def captures(key: str) -> dict[str, list[dict]]:
    """Everything the capture service recorded under *key*.

    Returns BOTH sides the service keeps: ``received`` (bodies that parsed as
    JSON) and ``received_raw`` (every request as the wire carried it — ``path``,
    verbatim ``headers``, base64 ``body_b64``). Signature callers read the raw
    side; field assertions read the parsed side.
    """
    try:
        return _readback_json(CAPTURE_READBACK_ORIGIN, "GET", webhook_path(key))
    except WebhookReadbackError as exc:
        raise AssertionError(
            f"readback of capture key {key!r} failed ({exc}) — the compose service is not answering, so "
            "'zero captures' below would be true of every leg and the calling module would grade nothing"
        ) from exc


def captured_delivery(entry: dict) -> CapturedWebhook:
    """One ``received_raw`` entry as the SENDER addressed it.

    The URL is rebuilt as ``https://{Host}{path}`` — never from the URL the test
    registered, and never with an ``http://`` scheme. The tls-proxy terminates
    TLS and forwards ``Host`` verbatim, so the authority an RFC 9421 signature
    covers is the https one, even though the capture service itself only ever
    sees plaintext. Reconstructing it as http fails as
    ``webhook_signature_invalid``, which reads like a crypto bug and is really a
    reconstruction bug (salesagent-og9k.12 A4).
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

    The old in-process receiver handed a suite a dict whose lists the server
    mutated in place, so a polling loop could hold ``info["received"]`` and watch
    it fill. The capture service is a different process, so every accessor here
    RE-READS it — that is the one behavioural difference a migrating caller must
    absorb: bind the call inside the poll loop, not before it.

    Key isolation is per-test and opaque, which is what lets modules run
    concurrently against one shared receiver without reading each other's
    captures.
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
    because every assertion a caller makes is fail-closed ("nothing was
    captured"), and that claim is worthless if the receiver was never reachable.
    """
    assert_capture_service_is_live()
    yield CaptureHandle(f"{key_prefix}-{uuid.uuid4().hex}")


# ── The ``.adcp.test`` flavor: host-reachable readback + rejection control ──


def delivery_url_for(key: str) -> str:
    """The URL a sender POSTs to for ``key`` — through the shared TLS front.

    One template, one caller-visible spelling. A second copy is how the harness
    and the e2e fixtures would drift onto different hosts.
    """
    return f"{ALT_CAPTURE_DELIVERY_ORIGIN}{webhook_path(key)}"


def program_rejections(key: str, *, status: int, count: int) -> None:
    """Make the capture service answer ``status`` for ``key``'s next ``count`` deliveries.

    Rides the plain-HTTP readback port, never the TLS front: this is test
    control-plane, the same side of the house as reading captures back. Delivery
    is the only traffic the front terminates.

    Without this the compose stack has no way to make the deployed server's
    delivery FAIL — the capture service answers every delivery 200 — so the
    server's circuit breaker could never record a real failure. ``count=0`` is
    how a caller spells "healthy again".
    """
    _readback_json(_readback_base_url(), "POST", f"/control/{key}", body={"status": status, "count": count})


def _assert_own_stack() -> None:
    """Fail loudly if the readback port belongs to a different (cross-wired) stack."""
    _assert_health(_readback_base_url())


class ReceivedView:
    """Live view over one key's captures — every read is a fresh readback round trip.

    There is no local cache, so ``not received`` / ``received[0]`` / ``for w in
    received`` all reflect the service's CURRENT state. ``.clear()`` atomically
    drains the key server-side (never a separate read-then-clear), so a capture
    landing between two calls is never silently lost — the same guarantee the old
    in-process shared list gave for free.

    Iterates the PARSED side; :meth:`raw` is the same captures WITH their headers
    and body bytes. Both come off one readback document, so neither can observe a
    delivery the other cannot.

    :func:`captures` / :func:`captured_deliveries` answer the same questions for
    the OTHER addressing flavor and are not interchangeable with these — see the
    module docstring: a key read back through the wrong flavor gets a
    synthesized empty bucket rather than an error.
    """

    def __init__(self, key: str) -> None:
        self._key = key

    def _fetch_doc(self) -> dict:
        return _readback_json(_readback_base_url(), "GET", f"/webhook/{self._key}")

    def _fetch(self) -> list[dict]:
        return self._fetch_doc()["received"]

    def raw(self) -> list[CapturedWebhook]:
        """Every delivery under this key, with headers verbatim and body BYTES.

        The parsed side drops exactly what a signature or an auth-header assertion
        needs, and the service has always recorded both (``_CaptureStore`` keeps
        ``received`` and ``received_raw``) — only this reader was parsed-only, which
        made every header-grading e2e_rest leg unable to observe its own evidence.

        Reuses :func:`captured_delivery` for the per-entry shape, so the
        ``https://{Host}{path}`` reconstruction an RFC 9421 ``@target-uri`` is
        verified against is spelled once, not once per flavor.
        """
        return [captured_delivery(entry) for entry in self._fetch_doc().get("received_raw") or []]

    def __bool__(self) -> bool:
        return bool(self._fetch())

    def __len__(self) -> int:
        return len(self._fetch())

    def __getitem__(self, index):
        return self._fetch()[index]

    def __iter__(self):
        return iter(self._fetch())

    def clear(self) -> None:
        _readback_json(_readback_base_url(), "DELETE", f"/webhook/{self._key}")


def register_capture_key() -> tuple[str, ReceivedView]:
    """Claim a fresh per-scenario capture key, drained and ready.

    Fails loudly first if the service is unreachable or belongs to a different
    compose stack, rather than handing back a key whose captures would silently
    never arrive. Shared by the e2e fixture below and by the BDD harness's e2e
    realization of the webhook endpoint, so both claim keys the same way.

    The key returned here is addressed with :func:`delivery_url_for` and read
    back with :class:`ReceivedView`. Do not hand it to :func:`captures`, and do
    not read a :func:`delivery_url` key with a :class:`ReceivedView` — see the
    module docstring on why that crosswire answers 200/empty instead of failing.
    """
    _assert_own_stack()
    key = uuid.uuid4().hex
    received = ReceivedView(key)
    received.clear()
    return key, received


@contextlib.contextmanager
def run_webhook_capture_server() -> Iterator[dict]:
    """Register a fresh per-test capture key and yield its webhook handle.

    Yields ``{"url", "received"}`` — the delivery URL through the shared TLS
    front, and a live :class:`ReceivedView` over this key's captures.
    ``received.clear()`` on entry and exit, same as the old in-process receiver,
    so each test starts and ends clean. Fails loudly at entry if the service is
    unreachable or belongs to a different compose stack, rather than yielding a
    handle whose captures would silently never arrive.

    This does NOT start a server. A hermetic test that needs a socket it can
    actually bind — no Docker stack, so no compose-DNS name to resolve — wants
    ``tests.e2e._webhook_capture_loopback.run_webhook_capture_server`` instead.
    """
    key, received = register_capture_key()
    try:
        yield {"url": delivery_url_for(key), "received": received}
    finally:
        received.clear()
