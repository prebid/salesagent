"""HTTP-client webhook receiver for e2e tests (GH #1802).

Backed by the long-lived ``webhook-capture`` compose service
(``tests/e2e/webhook_capture_service.py``), fronted by the shared
``tls-proxy`` at a fixed ``webhooks.adcp.test`` alias
(GH #1802) — not a per-test, ephemeral-port, in-process receiver
anymore. That is what a webhook receiver is in production.

Two traffic patterns, never conflated:

* DELIVERY — the sales agent POSTs a captured payload to
  ``https://webhooks.adcp.test:8443/webhook/<key>``. TLS terminates at the
  shared front; this module never runs a TLS server of its own.
* READBACK — this test process reads/drains its own key's captures over
  plain HTTP, directly against the service
  (``WEBHOOK_CAPTURE_HOST``/``WEBHOOK_CAPTURE_PORT``, mirroring
  ``e2e_host()``'s localhost-vs-service-name duality in ``tests/e2e/conftest.py``).
  This is test control-plane, not part of the graded TLS/egress surface.

Scope note: this module serves the 3 COMPOSE-COUPLED e2e consumers only.
``test_a2a_webhook_payload_types.py::TestProtocolWebhookWireFormat`` is
hermetic (no Docker stack — its sender runs in-process on the host) and
cannot resolve ``webhooks.adcp.test`` at all; it keeps using
``tests.e2e._webhook_capture_loopback`` unchanged (owner scope decision,
2026-08-05, recorded in GH #1802).
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from collections.abc import Iterator
from urllib.error import URLError
from urllib.request import Request, urlopen

_READBACK_TIMEOUT_SECONDS = 5.0
_DELIVERY_URL_TEMPLATE = "https://webhooks.adcp.test:8443/webhook/{key}"


class WebhookReadbackError(RuntimeError):
    """The webhook-capture service's readback control-plane failed or answered wrong.

    Raised loudly rather than degrading to an empty capture list, which would
    read as "no webhook arrived" (No Quiet Failures) when the actual cause is
    a transport error or a cross-wired sibling stack on the same published
    readback port.
    """


def _readback_base_url() -> str:
    host = os.getenv("WEBHOOK_CAPTURE_HOST", "localhost")
    port = os.getenv("WEBHOOK_CAPTURE_PORT", "8080")
    return f"http://{host}:{port}"


def _readback_request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{_readback_base_url()}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = Request(url, method=method, data=data, headers=headers)
    try:
        with urlopen(req, timeout=_READBACK_TIMEOUT_SECONDS) as resp:  # noqa: S310 - test-only, own compose network
            return json.loads(resp.read())
    except URLError as exc:
        raise WebhookReadbackError(f"webhook-capture readback {method} {url} failed: {exc}") from exc


def delivery_url_for(key: str) -> str:
    """The URL a sender POSTs to for ``key`` — through the shared TLS front.

    One template, one caller-visible spelling. A second copy is how the harness
    and the e2e fixtures would drift onto different hosts.
    """
    return _DELIVERY_URL_TEMPLATE.format(key=key)


def program_rejections(key: str, *, status: int, count: int) -> None:
    """Make the capture service answer ``status`` for ``key``'s next ``count`` deliveries.

    Rides the plain-HTTP readback port, never the TLS front: this is test
    control-plane, the same side of the house as reading captures back. Delivery
    is the only traffic the front terminates.

    Without this the compose stack has no way to make the deployed server's
    delivery FAIL — the capture service answers every delivery 200 — so the
    server's circuit breaker could never record a real failure.
    """
    _readback_request("POST", f"/control/{key}", body={"status": status, "count": count})


def _assert_own_stack() -> None:
    """Fail loudly if the readback port belongs to a different (cross-wired) stack."""
    body = _readback_request("GET", "/health")
    expected = os.environ.get("COMPOSE_PROJECT_NAME", "")
    actual = body.get("compose_project_name", "")
    if expected and actual != expected:
        raise WebhookReadbackError(
            f"webhook-capture at {_readback_base_url()} belongs to stack {actual!r}, "
            f"expected {expected!r} — cross-wired readback port from a concurrent stack"
        )


class ReceivedView:
    """Live view over one key's captures — every read is a fresh readback round trip.

    There is no local cache, so ``not received`` / ``received[0]`` / ``for w in
    received`` all reflect the service's CURRENT state. ``.clear()`` atomically
    drains the key server-side (never a separate read-then-clear), so a
    capture landing between two calls is never silently lost — the same
    guarantee the old in-process shared list gave for free.
    """

    def __init__(self, key: str) -> None:
        self._key = key

    def _fetch(self) -> list[dict]:
        return _readback_request("GET", f"/webhook/{self._key}")["received"]

    def __bool__(self) -> bool:
        return bool(self._fetch())

    def __len__(self) -> int:
        return len(self._fetch())

    def __getitem__(self, index):
        return self._fetch()[index]

    def __iter__(self):
        return iter(self._fetch())

    def clear(self) -> None:
        _readback_request("DELETE", f"/webhook/{self._key}")


def register_capture_key() -> tuple[str, ReceivedView]:
    """Claim a fresh per-scenario capture key, drained and ready.

    Fails loudly first if the service is unreachable or belongs to a different
    compose stack, rather than handing back a key whose captures would silently
    never arrive. Shared by the e2e fixture below and by the BDD harness's e2e
    realization of the webhook endpoint, so both claim keys the same way.
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
    ``received.clear()`` on entry and exit, same as the old in-process
    receiver, so each test starts and ends clean. Fails loudly at entry if the
    service is unreachable or belongs to a different compose stack, rather
    than yielding a handle whose captures would silently never arrive.
    """
    key, received = register_capture_key()
    try:
        yield {"url": delivery_url_for(key), "received": received}
    finally:
        received.clear()
