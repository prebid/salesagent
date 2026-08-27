"""One capture point for outbound webhook bytes, and the two wire oracles (#1291 C1).

Every AdCP webhook sender in ``src/`` is graded the same way: run the REAL
production sender, replace only the SOCKET, and assert on the bytes and headers
that would have gone out. That needs one capture that spans the three HTTP
clients the senders use today and the one the SDK uses after C1
(``salesagent-z6nr.18``) routes them all through
:class:`adcp.webhooks.WebhookSender`:

===========================================  ==========================================
sender                                       client
===========================================  ==========================================
``protocol_webhook_service.py:296``          ``requests.Session.post``
``webhook_delivery_service.py:484``          ``httpx.Client.post``
``order_approval_service.py:403``            ``httpx.Client.post``
``webhook_sender_factory.send_signed_challenge``  ``httpx.AsyncClient.post`` (#1291 C2 — the
                                             proof-of-control challenge; its POST lives in
                                             the boundary module, not in the service)
``mock_ad_server.py:512``                    ``requests.post``
``adcp.webhooks.WebhookSender._send_bytes``  ``httpx.AsyncClient.post`` over a pinned
                                             IP transport
===========================================  ==========================================

Spanning all of them is what keeps a red test red for the RIGHT reason: a capture
that only knew ``httpx`` would report "no webhook was sent" for a sender that is
merely still on ``requests``, hiding the assertion the test exists to make.

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
that runs ``check_url_ssrf(url, require_https=True)`` at fire time
(``notification_proof_service``, #1291 C2) resolves the destination ITSELF before it
signs anything, and an unresolvable name there reports "refused: Cannot resolve
hostname" — which reads like a signing or policy failure and is really just the box
having no answer for a test domain. The stub answers a PUBLIC address, so the SSRF
policy under test still runs for real: a sender pointed at a private range is still
refused, because that decision is made from the address, not from the lookup.
"""

from __future__ import annotations

import json
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
        # attribute exists. The BDD harness's responder IS a bare ``MagicMock``
        # (``_mixins.install_webhook_wire``), which answers every attribute access with a
        # new child mock — so an ``is None`` test reads a MagicMock as a supplied body and
        # feeds it to ``httpx.Response``, breaking every delivery in the suite.
        body = getattr(answer, "content", None)
        if not isinstance(body, bytes | bytearray):
            body = None
        return httpx.Response(
            int(answer.status_code),
            content=_ACCEPTED_BODY if body is None else body,
            headers={"Content-Type": "application/json"},
        )

    transport = httpx.MockTransport(_handler)

    def _sync_client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    def _async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

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
        # lookup on whichever path the code under test happens to take.
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


@contextmanager
def constructed_http_clients() -> Iterator[list[Any]]:
    """Record every REAL httpx client a sender constructs inside the block.

    Grades transport-level properties that live on the CLIENT rather than in the
    request bytes — ``follow_redirects`` and ``timeout`` — which
    :func:`capture_outbound_webhooks` cannot see because it replaces the socket,
    not the client.

    Asserting on the constructed INSTANCE rather than on a constructor mock's call
    args is what keeps the obligation true whichever client the delivery path
    builds: the sync client a service opens itself, or the ``AsyncClient`` the RFC
    9421 signing boundary owns (#1291 C1). A constructor-mock assertion goes
    silently vacuous the moment delivery moves between those two seams -- which is
    exactly what a ``follow_redirects=False`` assertion did when C1 relocated the
    client, so it is deliberately not the shape used here.

    Note the property is graded as ``is False``, never as "was passed explicitly":
    httpx's own default is already ``False``, so a sender that never names the
    kwarg still satisfies the no-open-redirect obligation.
    """
    real_sync, real_async = httpx.Client, httpx.AsyncClient
    built: list[Any] = []

    def _spy_sync(*args: Any, **kwargs: Any) -> Any:
        built.append(real_sync(*args, **kwargs))
        return built[-1]

    def _spy_async(*args: Any, **kwargs: Any) -> Any:
        built.append(real_async(*args, **kwargs))
        return built[-1]

    with patch("httpx.Client", _spy_sync), patch("httpx.AsyncClient", _spy_async):
        yield built


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
