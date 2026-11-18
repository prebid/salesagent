"""The capture middleware records what a signature covers, and gives the body back.

Two obligations, and they pull in opposite directions, which is why they are graded together:

* it must RECORD the HTTP message as it arrived — the exact bytes, the method, and a
  ``@target-uri`` built from ``raw_path`` rather than the percent-decoded ``path``;
* it must leave the request untouched for the handler behind it. It drains the ASGI receive
  channel to read the body, and that is the SAME channel the app will read, so anything it
  consumes and does not replay is a body the handler never sees.

A middleware that satisfies the first and not the second breaks every POST on the three AdCP
surfaces, silently, in a way no signature test would notice.
"""

from __future__ import annotations

import pytest

from src.core.signing.capture import CAPTURED_EXCHANGE, SignedExchangeCapture, captured_exchange, is_adcp_surface


async def _drive(path: str, body: bytes, *, raw_path: bytes | None = None, headers: list | None = None) -> dict:
    """Run one request through the capture and return what the app behind it saw."""
    seen: dict = {}

    async def app(scope, receive, send):
        chunks = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        seen["body"] = b"".join(chunks)
        seen["exchange"] = captured_exchange(scope)

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": raw_path if raw_path is not None else path.encode(),
        "query_string": b"",
        "headers": headers if headers is not None else [(b"host", b"seller.example.com")],
        "scheme": "http",
        "state": {},
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        return None

    await SignedExchangeCapture(app)(scope, receive, send)
    return seen


def test_the_surface_predicate_has_a_segment_boundary() -> None:
    """``/api/v1x`` is not ``/api/v1``. A bare ``startswith`` would pull it under the capture."""
    assert is_adcp_surface("/mcp")
    assert is_adcp_surface("/api/v1/products")
    assert not is_adcp_surface("/mcpx")
    assert not is_adcp_surface("/api/v1x/products")
    assert not is_adcp_surface("/.well-known/jwks.json"), (
        "the allowlist permanently exempts the trust-root documents; a verifier in front of "
        "the document a counterparty fetches to obtain its key is a bootstrap deadlock"
    )


@pytest.mark.asyncio
async def test_the_handler_still_receives_the_whole_body() -> None:
    """The replay is lossless. Without it, every POST on an AdCP surface arrives empty."""
    payload = b'{"brand":"acme","packages":[]}'
    seen = await _drive("/api/v1/products", payload)
    assert seen["body"] == payload


@pytest.mark.asyncio
async def test_it_records_the_exact_bytes_the_client_sent() -> None:
    """``content-digest`` covers the bytes, so a re-serialization would verify a different message.

    The whole reason this middleware exists: by the time the boundary holds a request it has
    been through ``model_validate``, and dumping that model back to JSON is not byte-identical
    to what arrived — key order, number formatting, whitespace.
    """
    payload = b'{"b": 1,   "a": 2.50}'
    seen = await _drive("/api/v1/products", payload)
    assert seen["exchange"] is not None
    assert seen["exchange"].body == payload


@pytest.mark.asyncio
async def test_the_target_uri_keeps_the_percent_encoding_the_client_sent() -> None:
    """``@target-uri`` is built from ``raw_path``, never the decoded ``path``.

    Every real ASGI server percent-DECODES ``scope["path"]`` (uvicorn sets
    ``path = unquote(raw_path)``), so a ``@target-uri`` built from it turns ``%2F`` into a real
    separator and rejects a legitimate signed request as ``request_signature_invalid``. This
    is reachable in production on any ``/api/v1`` route with a path parameter.
    """
    seen = await _drive("/api/v1/tasks/a/b", b"{}", raw_path=b"/api/v1/tasks/a%2Fb")
    assert seen["exchange"].url == "http://seller.example.com/api/v1/tasks/a%2Fb"


@pytest.mark.asyncio
async def test_the_scheme_comes_from_the_client_facing_hop() -> None:
    """Behind TLS termination the signer signed ``https``, whatever the inner hop speaks.

    The FIRST hop of ``X-Forwarded-Proto``, because that is the scheme the client addressed;
    later hops are our own edge's business. Getting this wrong fails ``@target-uri`` on every
    legitimate request in any deployment with a proxy in front — which is all of them.
    """
    seen = await _drive(
        "/api/v1/products",
        b"{}",
        headers=[(b"host", b"seller.example.com"), (b"x-forwarded-proto", b"https, http")],
    )
    assert seen["exchange"].url.startswith("https://seller.example.com/")


@pytest.mark.asyncio
async def test_a_non_adcp_path_is_not_captured_at_all() -> None:
    """The admin UI and the trust-root documents stream through untouched."""
    seen = await _drive("/admin/dashboard", b"x")
    assert seen["exchange"] is None
    assert seen["body"] == b"x", "and the body still reaches the handler"


@pytest.mark.asyncio
async def test_the_raw_header_list_survives_a_repeated_header() -> None:
    """Checklist step 1 refuses shapes that are invisible once headers collapse into a dict.

    Every dict view of ASGI headers LAST-WINS on a repeated name rather than joining it, so a
    proxy-inserted second ``Content-Digest`` line would rewrite a covered value with nothing
    anywhere to notice. The capture keeps the list.
    """
    seen = await _drive(
        "/api/v1/products",
        b"{}",
        headers=[
            (b"host", b"seller.example.com"),
            (b"content-digest", b"sha-256=:AAAA:"),
            (b"content-digest", b"sha-256=:BBBB:"),
        ],
    )
    digests = [value for name, value in seen["exchange"].raw_headers if name == b"content-digest"]
    assert digests == [b"sha-256=:AAAA:", b"sha-256=:BBBB:"]


@pytest.mark.asyncio
async def test_an_over_cap_body_is_flagged_and_still_replayed() -> None:
    """Over-cap refuses the DIGEST, never the body: the handler gets every byte regardless.

    The two are separate answers. An unsigned request larger than the hashing cap is an
    ordinary request this agent serves; only a SIGNED one is refused, by the verifier, because
    a digest it cannot compute is a signature it cannot check.
    """
    from src.core.config import get_settings

    signing = get_settings().signing
    previous = signing.max_signed_body_bytes
    signing.max_signed_body_bytes = 4
    try:
        seen = await _drive("/api/v1/products", b"0123456789")
    finally:
        signing.max_signed_body_bytes = previous

    assert seen["exchange"].over_cap is True
    assert seen["body"] == b"0123456789"


@pytest.mark.asyncio
async def test_the_capture_lands_where_every_transport_reads_it() -> None:
    """``scope["state"]`` is the one place all three transports can reach.

    REST holds the ``Request``, A2A gets a ``ServerCallContext`` built from one, and MCP has
    neither — FastMCP hands a tool the headers and a ``Request`` whose body was already
    consumed by the streamable-HTTP transport. The scope is what they share, and Starlette's
    ``Mount`` mutates the one dict rather than copying it, so the dict FastMCP hands back is
    this one.
    """
    seen = await _drive("/mcp", b'{"method":"tools/call"}')
    assert seen["exchange"] is not None
    assert captured_exchange({"state": {CAPTURED_EXCHANGE: seen["exchange"]}}) is seen["exchange"]
    assert captured_exchange({}) is None, "and an invocation outside an HTTP request presents nothing"


def test_the_a2a_leg_really_reads_the_capture(monkeypatch) -> None:
    """The A2A transport's leg of the claim above, EXECUTED rather than asserted in prose.

    ``test_the_capture_lands_where_every_transport_reads_it`` names all three transports and
    drives two: ``/mcp`` and ``/api/v1``. A2A was the one leg no test ran, and it is the leg
    where a fork would be least visible — ``/mcp`` is a Starlette ``Mount`` (which mutates the
    one scope dict), while ``/a2a`` is a ``Route`` appended straight onto the app's route
    table, reached through ``AdCPCallContextBuilder`` rather than through the scope directly.
    Two different route kinds and two different sourcing paths for the ONE derivation that
    must not fork: if it did, every signed A2A request would fail while MCP passed, and
    nothing in the suite would say so.

    So this drives the REAL app over HTTP and asserts on the exchange the A2A context builder
    actually placed — the four things a signature covers, each compared against what the
    client sent rather than against a value the test also computed.
    """
    from starlette.testclient import TestClient

    from src.a2a_server import context_builder
    from src.app import app

    placed: list = []
    build = context_builder.AdCPCallContextBuilder.build

    def _record(self, request):
        context = build(self, request)
        placed.append(context.state.get(context_builder.EXCHANGE_STATE_KEY))
        return context

    # Observing the real builder, not standing in for it: the value asserted below is the one
    # production put on the call context.
    monkeypatch.setattr(context_builder.AdCPCallContextBuilder, "build", _record)

    # The bytes are written out here rather than re-serialized from a dict, because they ARE
    # the subject: a Content-Digest covers this exact octet sequence, and two json.dumps calls
    # with different separators produce two different digests over the same object.
    sent = (
        b'{"jsonrpc":"2.0","id":"1","method":"SendMessage","params":{"message":'
        b'{"messageId":"m1","role":"ROLE_USER","parts":[{"text":"hi"}]}}}'
    )
    with TestClient(app) as client:
        client.post("/a2a", content=sent, headers={"Content-Type": "application/json"})

    assert placed, "the A2A context builder never ran — this test stopped grading the leg it exists for"
    exchange = placed[0]
    assert exchange is not None, (
        "the A2A leg received no capture. /a2a is a Route appended to the app's route table "
        "rather than a Mount, so this is where the capture would be missed — and a missing "
        "exchange reads to the verifier as 'this request presented no signature', which "
        "fails every signed A2A request while MCP keeps passing."
    )
    assert exchange.method == "POST"
    assert exchange.url == "http://testserver/a2a", "@target-uri must be the URL the CLIENT addressed"
    assert exchange.body == sent, (
        "the capture must carry the exact bytes the client sent — a Content-Digest is over these bytes and nothing else"
    )
    assert any(name.lower() == b"content-type" for name, _ in exchange.raw_headers), (
        "the raw header LINES must survive to the A2A leg, not a collapsed dict view"
    )
