"""One capture point for outbound webhook bytes, and the two wire oracles (GH #1291).

Every AdCP webhook sender in ``src/`` is graded the same way: run the REAL
production sender, replace only the SOCKET, and assert on the bytes and headers
that would have gone out. On this tree every one of them converges on the single
egress seam, so one capture spans them all:

* ``protocol_webhook_service._send_with_retry_and_logging`` — ``adeliver_webhook``
* ``webhook_delivery_service._send_webhook_enhanced`` — ``deliver_webhook``
* ``order_approval_service`` (approval notifications) — ``deliver_webhook``
* ``core.webhook_delivery`` (operator notifications) — ``deliver_webhook``
* ``mock_ad_server`` (dev adapter) — ``deliver_webhook``
* ``signing.outbound.send_signed_challenge`` — ``outbound_http.asend`` directly. The
  proof-of-control challenge (GH #1291 C2); its POST lives in the signing boundary
  rather than in a service, because the challenge is the one delivery whose key is
  chosen by the boundary itself.

``deliver_webhook`` / ``adeliver_webhook`` are the two twins over
:func:`src.core.security.outbound_http.send` / ``asend``, which builds and discards
one ``httpx.Client`` / ``httpx.AsyncClient`` per delivery on a per-destination pinned
transport. So the whole table reduces to ``httpx`` — that collapse is GH #1802's, not
this helper's, and it is why the ``requests`` arms below now catch nothing in ``src/``.
They stay anyway: this capture's failure mode is SILENT (a POST on a client it does not
watch reads as "no webhook was sent", and every downstream assertion goes vacuous
against an empty list), and two extra patches are a cheaper price than a green test
that graded nothing. ``tests/`` is deliberately outside ``ruff-egress.toml``, so a
test-side sender can still be on ``requests``.

``adcp.webhooks.WebhookSender`` is NOT in that list and never enters this tree: it owns
its own httpx client, so it cannot carry the per-destination IP pin the seam owes every
dial, and it is not a sanctioned dialer in ``outbound_http``.

Only the network is replaced. httpx and requests still perform their own header
building and body encoding, so :attr:`CapturedWebhook.content` is byte-for-byte
what the socket would have carried — which is the whole point, since the defect
class under test (#1441) is precisely "the bytes signed are not the bytes sent".

``build_async_ip_pinned_transport`` is stubbed alongside the clients because it
resolves the destination through real DNS before the SDK signs anything. DNS is a
true external; leaving it live would make every test depend on whether the box can
resolve ``buyer.example.com`` (it cannot), and the resulting ``SSRFValidationError``
would masquerade as a signing failure.

``socket.gethostbyname`` is stubbed for exactly the same reason one layer up: a sender
that pre-flights its destination at fire time
(``notification_proof_service``'s ``validate_url(url, provenance=CounterpartyUrl(field=None))``,
GH #1291 C2) resolves it ITSELF before it signs anything, and an unresolvable name there
reports "refused: Cannot resolve hostname" — which reads like a signing or policy failure
and is really just the box having no answer for a test domain. The stub answers a PUBLIC
address, so the SSRF policy under test still runs for real: a sender pointed at a private
range is still refused, because that decision is made from the address, not from the lookup.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import requests

#: What a captured delivery answers with. Success, so a sender's retry loop stops
#: after one attempt and the capture holds exactly the deliveries the code chose
#: to make rather than a burst of retries.
#:
#: Deliberately NOT a proof-of-control echo: a receiver that merely accepts the POST
#: has proven nothing (#1291 C2), so the default answer is the one that must FAIL the
#: echo check. :func:`echoing_challenge_response` is the opt-in that passes it.
_ACCEPTED_BODY = b'{"status":"received"}'

#: A public address every stubbed DNS lookup resolves to. Public on purpose: the SSRF
#: policy is evaluated FROM the resolved address, so answering a private one would make
#: every sender refuse and the tests would grade the refusal path.
_STUB_RESOLVED_IP = "93.184.216.34"


@dataclass(frozen=True)
class CapturedWebhook:
    """One outbound POST, as the receiving socket would have seen it."""

    url: str
    headers: httpx.Headers
    content: bytes

    @property
    def payload(self) -> dict[str, Any]:
        """The body parsed as JSON. Raises if the sender sent something else."""
        return json.loads(self.content)


def _record(captured: list[CapturedWebhook], url: str, headers: Any, body: Any) -> None:
    if body is None:
        body = b""
    if isinstance(body, str):
        body = body.encode("utf-8")
    captured.append(CapturedWebhook(url=str(url), headers=httpx.Headers(headers), content=bytes(body)))


def _requests_response(status_code: int) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = _ACCEPTED_BODY
    response.headers["Content-Type"] = "application/json"
    return response


@contextmanager
def stub_outbound_webhooks(responder: Callable[..., Any]) -> Iterator[None]:
    """Replace the SOCKET under every outbound webhook client with *responder*.

    ``responder(url, headers=<dict>, content=<bytes>)`` is called once per POST
    and returns anything carrying a ``status_code`` — or raises, which is how the
    timeout / connection-error ladders are driven. It is deliberately callable
    rather than a fixed answer so a ``unittest.mock.MagicMock`` can BE the
    responder: the harness gets ``call_args`` / ``call_count`` / ``side_effect``
    on real wire bytes, and the signing suites get :func:`capture_outbound_webhooks`
    — one stub, two shapes, instead of two stubs that drift.
    """
    real_client = httpx.Client
    real_async_client = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        request.read()
        answer = responder(str(request.url), headers=httpx.Headers(request.headers), content=request.content)
        # A responder MAY dictate the response body. It has to be able to: a sender whose
        # proof is the receiver's ECHO of a value the sender just generated cannot be
        # graded against a fixed body at all (#1291 C2).
        #
        # "Did the responder supply a body" is decided on the VALUE, never on whether the
        # attribute exists. A bare ``MagicMock`` is an advertised responder shape (see this
        # function's docstring) and answers every attribute access with a new child mock —
        # so an ``is None`` test reads a MagicMock as a supplied body and feeds it to
        # ``httpx.Response``, breaking every delivery in the suite. The BDD env mixin that
        # first hit this has since been retired (``delivery_circuit_breaker`` serves a real
        # local origin instead), but the shape is still offered, so the guard stays.
        body = getattr(answer, "content", None)
        if not isinstance(body, bytes | bytearray):
            body = None
        return httpx.Response(
            int(answer.status_code),
            content=_ACCEPTED_BODY if body is None else body,
            headers={"Content-Type": "application/json"},
        )

    transport = httpx.MockTransport(_handler)

    # SUBCLASSES, not factory functions. ``patch("httpx.AsyncClient", <function>)`` makes
    # the name a function for as long as the patch is active, and any module IMPORTED in
    # that window whose class body evaluates ``httpx.AsyncClient | None`` at runtime dies
    # with "unsupported operand type(s) for |: 'function' and 'NoneType'". That is not
    # hypothetical: ``a2a.client.client`` is imported lazily inside the delivery path, so
    # a webhook send under this stub raised before it ever reached the transport — the
    # send failed and the capture recorded nothing, which read as "the buyer was never
    # told". A type supports ``|``; a function does not.
    class _SyncClient(real_client):  # type: ignore[misc,valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    class _AsyncClient(real_async_client):  # type: ignore[misc,valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    _sync_client = _SyncClient
    _async_client = _AsyncClient

    def _session_post(_self: requests.Session, url: str, **kwargs: Any) -> requests.Response:
        return _requests_post(url, **kwargs)

    def _requests_post(url: str, **kwargs: Any) -> requests.Response:
        # Prepared through requests' own machinery, so the recorded bytes are the
        # ones requests would have written — including its ``{"a": 1}`` spacing,
        # which is the divergence #1441 is about.
        prepared = requests.Request(
            method="POST",
            url=url,
            headers=kwargs.get("headers"),
            json=kwargs.get("json"),
            data=kwargs.get("data"),
        ).prepare()
        answer = responder(str(prepared.url), headers=httpx.Headers(prepared.headers), content=prepared.body or b"")
        return _requests_response(int(answer.status_code))

    with ExitStack() as stack:
        stack.enter_context(patch("httpx.Client", _sync_client))
        stack.enter_context(patch("httpx.AsyncClient", _async_client))
        # Patched at BOTH the definition module and the SDK sender module that
        # imports the name at import time — patching only one leaves a live DNS
        # lookup on whichever path the code under test happens to take. The
        # definition-module target covers every SDK caller that imports it
        # function-locally (``signing.jwks``, ``adagents``, ``webhooks``); only
        # ``adcp.webhook_sender`` binds it at module scope and needs its own row.
        for target in (
            "adcp.signing.ip_pinned_transport.build_async_ip_pinned_transport",
            "adcp.webhook_sender.build_async_ip_pinned_transport",
        ):
            stack.enter_context(patch(target, lambda *a, **k: transport))
        stack.enter_context(patch.object(requests.Session, "post", _session_post))
        stack.enter_context(patch("requests.post", _requests_post))
        # Patched on the module the resolver lives on rather than at each caller, so a
        # sender that grows its own fire-time SSRF check is covered without editing this
        # helper again.
        stack.enter_context(patch("socket.gethostbyname", lambda _host: _STUB_RESOLVED_IP))
        # BOTH resolver calls, because the two senders do not agree on which one they use.
        # #1802 moved the dial-time SSRF check into ``EgressPolicy.resolve_for_dial``,
        # which re-resolves through ``adcp.signing`` — and that resolves with
        # ``socket.getaddrinfo`` (ip_pinned_transport.py, jwks.py), NOT
        # ``gethostbyname``. With only the latter stubbed, the seam performed a LIVE
        # lookup of the test's ``buyer.example.com``, failed it, and refused the
        # destination before any client was constructed, so the capture recorded zero
        # POSTs and every assertion downstream read an empty list.
        stack.enter_context(
            patch(
                "socket.getaddrinfo",
                lambda host, port, *a, **k: [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", (_STUB_RESOLVED_IP, port or 0))
                ],
            )
        )
        yield


#: How a receiver answers ONE captured POST: ``(status_code, body)``. ``body`` may be
#: ``None`` to keep the default accepted body.
Answer = Callable[[CapturedWebhook], "tuple[int, bytes | None]"]


def echoing_challenge_response(field: str = "challenge") -> Answer:
    """Answer a proof-of-control challenge the way a receiver that CONTROLS the endpoint does.

    Reads the single-use value out of the POSTed body and echoes it back, which is the
    only answer that proves control: a 2xx alone is produced by any endpoint that accepts
    POSTs, including one an attacker pointed at us (``sync_accounts.mdx`` @ v3.1.1
    :223-235). Echoing from the CAPTURED body rather than from a value the test also knows
    is what keeps the assertion about production's own nonce.

    ``field`` selects which of the two schema-permitted response fields is used —
    ``webhook-challenge-response.json`` requires exactly one of ``challenge`` / ``token``,
    so both spellings have to be answerable.
    """

    def _answer(captured: CapturedWebhook) -> tuple[int, bytes | None]:
        value = captured.payload.get("challenge")
        if not value:
            # Not a challenge POST, or one carrying no value to echo. Answering the
            # default keeps this responder usable as a blanket answer without inventing
            # an echo for a body that has nothing to echo.
            return 200, None
        return 200, json.dumps({field: value}).encode()

    return _answer


@contextmanager
def capture_outbound_webhooks(
    status_codes: Sequence[int] = (), *, responder: Answer | None = None
) -> Iterator[list[CapturedWebhook]]:
    """Record every outbound POST made through httpx or requests inside the block.

    The list is appended to in call order, so ``len(captured)`` grades "was the
    receiver told at all" — a leg that must be asserted before any header
    assertion, because "no webhook" and "an unsigned webhook" are different
    defects.

    ``status_codes`` answers the Nth delivery with the Nth code, holding the last
    one thereafter; the default answers every delivery ``200``. It exists so a
    sender's retry ladder can be driven through the same capture that grades its
    bytes.

    ``responder`` additionally shapes the response BODY from the captured request — see
    :func:`echoing_challenge_response`. Recording happens either way, so a test never has
    to choose between controlling the answer and grading the bytes.
    """
    captured: list[CapturedWebhook] = []

    # SCOPED TO THIS TEST'S OWN TRAFFIC (#2055). The rebind below is process-global — it
    # replaces the socket, so it sees EVERY outbound POST, including one made by a
    # background thread belonging to a test that has already finished. That made this
    # helper structurally unable to tell "my sender delivered twice" from "someone else's
    # delivery landed in my list", and every assertion that COUNTS captures inherited the
    # blindness: tests/unit/test_order_approval_service.py had already been widened to
    # tolerate it ("may see 4 calls ... 3 + 1 pollution"), and its idempotency-key
    # assertion — which cannot be widened, because an intruder brings its own key —
    # reddened the CI matrix twice.
    #
    # A delivery is OURS when it comes from the thread that opened this block, or from a
    # thread that did not exist when it opened (one the test itself spawned). A thread
    # alive BEFORE the block and not ours belongs to somebody else. That is a property of
    # provenance rather than of timing, so it does not depend on how slow the runner is —
    # which is what made this reproduce on CI (~620s) and not locally (~175s).
    owner = threading.get_ident()
    pre_existing = {thread.ident for thread in threading.enumerate()} - {owner}

    def _is_ours() -> bool:
        return threading.get_ident() not in pre_existing

    def _responder(url: str, *, headers: Any, content: Any) -> SimpleNamespace:
        if not _is_ours():
            # Answer it — the foreign sender is mid-delivery and must not crash — but do
            # not let it into this test's evidence.
            return SimpleNamespace(status_code=200, content=None)
        _record(captured, url, headers, content)
        status = 200 if not status_codes else status_codes[min(len(captured) - 1, len(status_codes) - 1)]
        body: bytes | None = None
        if responder is not None:
            status, body = responder(captured[-1])
        return SimpleNamespace(status_code=status, content=body)

    with stub_outbound_webhooks(_responder):
        yield captured


# ``constructed_http_clients()`` — a spy that recorded every REAL httpx client a sender
# built, so a test could grade ``follow_redirects`` / ``timeout`` on the INSTANCE — is
# deliberately not ported. It arrived here with zero callers: GH #1802 moved client
# construction into ``outbound_http.send``/``asend``, which builds and discards a pinned
# client per delivery, so neither property is assertable from a sender's test without
# mocking the seam. Both are owned and graded where they now live — the timeout is the
# argument the sender hands ``deliver_webhook``, and the redirect refusal is the seam's
# unconditional ``follow_redirects=False`` — and the replacement assertion is strictly
# stronger anyway: count the hops that actually reached the wire in ``captured``. See the
# retirement record in ``tests/unit/test_protocol_webhook_ssrf.py``.


def signature_input_label(captured: CapturedWebhook, label: str = "sig1") -> Any:
    """The whole RFC 9421 ``Signature-Input`` entry for *label*, parsed by the SDK's parser.

    Returns the SDK's ``SignatureInputLabel``: ``.components`` is what the signature
    COVERS, ``.params`` carries ``tag`` / ``keyid`` / ``created`` / ``alg``. Parsing
    rather than substring-matching is what makes either assertion real — both are
    structured-field constructs, so a hand-rolled ``in`` check would pass for a tag
    that merely CONTAINS the profile string, and equally for a ``content-digest``
    that appears anywhere in the header rather than in the covered component list.
    """
    from adcp.signing.canonical import parse_signature_input_header

    header = captured.headers.get("signature-input")
    assert header, (
        "no Signature-Input header on the outbound webhook — the receiver has nothing to verify; "
        f"headers were {sorted(captured.headers.keys())}"
    )
    parsed = parse_signature_input_header(header)
    assert label in parsed, f"Signature-Input carries labels {sorted(parsed)}, not {label!r}"
    return parsed[label]


def signature_input_params(captured: CapturedWebhook, label: str = "sig1") -> dict[str, str | int]:
    """The RFC 9421 ``Signature-Input`` parameters, parsed by the SDK's own parser.

    Parsing rather than substring-matching is what makes the ``tag=`` assertion
    real: ``tag`` is a structured-field parameter, and a hand-rolled ``in`` check
    would also pass for a tag that merely CONTAINS the profile string.
    """
    return signature_input_label(captured, label).params
