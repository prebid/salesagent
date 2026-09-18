"""Capture the HTTP message a signature covers, where its bytes still exist. DECIDES NOTHING.

The one thing #1721's boundary cannot reconstruct
-------------------------------------------------
``request-lifecycle.md`` states the principle this module is scoped by: "``src/app.py``
registers three HTTP middlewares, and **none of them reads a credential or resolves an
identity** … No middleware decides auth." A fourth one that VERIFIED a signature would
break that. This one reads no credential, resolves nothing, refuses nothing and never
short-circuits: it records three values on ``scope["state"]`` and calls the app.

It has to exist because RFC 9421 signs the HTTP MESSAGE, and by the time the boundary runs,
two of the three things it signed are gone:

* ``content-digest`` covers the EXACT bytes received. The boundary's ``raw`` is already
  decoded (``model_validate(raw)``), and re-serializing it is not byte-identical, so a
  digest computed from it would verify a different message than the buyer signed.
* ``@target-uri`` covers the URL AS SENT, percent-encoding intact. ``scope["path"]`` is
  percent-DECODED by every real server (uvicorn sets ``path = unquote(raw_path)``), so only
  ``raw_path`` — bytes, on the scope — can rebuild it.

A third, ``@method``, is merely absent from what a transport hands the boundary.

The design note this was built from proposed ``signed_body: bytes | None`` alone, sourced
per transport. Bytes alone are not enough for the two reasons above, and per-transport
sourcing would put three copies of the ``@target-uri`` derivation in the tree — the one
derivation whose divergence fails EVERY signed request rather than an edge case. One
capture, one derivation, three readers.

Scoped to the AdCP surfaces, and the buffer is lossless
-------------------------------------------------------
The allowlist is a list of AdCP surfaces, not a denylist of everything else, which is what
permanently exempts the trust-root documents: a body-buffering middleware in front of
``/.well-known/jwks.json`` costs nothing but a verifier in front of it would be a bootstrap
deadlock, and an allowlist cannot forget.

Whatever is read is replayed. The downstream app builds its own ``Request`` from the same
scope, so the receive channel drained here is the SAME one the app will read; a buffer that
consumed and discarded would hand the handler a destroyed body. Every exit replays every
byte — over-cap included, where only the HASHING is refused, and the disconnect included,
which must still reach the app.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any, Final

from src.core.http_utils import path_from_asgi_scope

logger = logging.getLogger(__name__)

Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

#: The key the capture is stashed under on ``scope["state"]``.
CAPTURED_EXCHANGE = "adcp_signed_exchange"

#: The AdCP protocol surfaces, and ONLY those. Everything else — admin, health, debug, the
#: landing page, the A2A agent card and every trust-root document — passes through untouched
#: by construction rather than by remembering to exempt it. Prefix plus segment boundary, so
#: ``/api/v1x`` cannot sneak in.
ADCP_SURFACE_PREFIXES: Final[tuple[str, ...]] = ("/mcp", "/a2a", "/api/v1")


def is_adcp_surface(path: str) -> bool:
    """Whether *path* targets one of the AdCP protocol surfaces.

    THE boundary predicate, with the segment rule written once. A bare ``str.startswith``
    would match ``/mcpx`` and silently pull a non-AdCP surface under the capture.

    *path* is a ROUTE path — decoded, ``root_path`` stripped — because this predicate must
    agree with the router, and the router matches on that one
    (``src.core.http_utils.path_from_asgi_scope``). Do not hand it ``scope["path"]``.
    """
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in ADCP_SURFACE_PREFIXES)


def joined_headers(raw_headers: Iterable[tuple[bytes, bytes]]) -> dict[str, str]:
    """THE mapping view of a request's header LINES. One derivation, every reader.

    ``Mapping[str, str]`` cannot represent two lines of one name, so every mapping built
    from ASGI headers has to answer what a repeat means — and until this function existed
    each transport answered differently, from its own container, with nobody choosing:

    * REST handed the resolver a Starlette ``Headers``, whose ``__getitem__`` returns the
      FIRST line;
    * MCP handed it ``get_http_headers(include_all=True)``, a dict built by iterating
      ``.items()``, which yields every line and so keeps the LAST;
    * A2A handed it ``dict(request.headers)``, which goes through ``keys()`` and
      ``__getitem__`` and so keeps the FIRST.

    One identical HTTP message therefore produced two different credentials, two different
    tenants and two different signatures depending on the surface it arrived on. Nothing
    chose that; it fell out of three container types.

    The rule here is RFC 9110 §5.3: repeated field lines are equivalent to one value with
    the lines joined by ``", "``. That is the only reading that discards no line, and it is
    the one the strict pre-check already assumes — ``_multi_valued_content_type`` looks for
    a comma, ``_duplicate_digest_algorithm`` splits on one. So a repeat now REACHES those
    rules as an ambiguity they can refuse, instead of being resolved silently upstream of
    them by whichever container the transport happened to use.

    For a field with no such rule the join is still the safe answer: a joined
    ``Authorization`` is a credential this seller does not accept (AUTH_INVALID) and a
    joined ``x-adcp-tenant`` names no tenant. Both fail closed, and both fail the same way
    on every transport, which is the property that was missing.
    """
    joined: dict[str, str] = {}
    for raw_name, raw_value in raw_headers:
        name = raw_name.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        joined[name] = f"{joined[name]}, {value}" if name in joined else value
    return joined


@dataclass(frozen=True)
class HttpExchange:
    """The HTTP message a signature covers, as it arrived.

    Frozen, and built once per request: the verifier reads it after identity resolution has
    already loaded a tenant, and a value that could change between the capture and the read
    would let a request be admitted under one message and graded under another.
    """

    #: ``@method``.
    method: str
    #: ``@target-uri``, as the CLIENT addressed it.
    url: str
    #: The header list as received — ``list[tuple[bytes, bytes]]`` from the scope, NOT a
    #: collapsed dict. Checklist step 1 refuses shapes that are invisible once headers
    #: collapse: every dict view of ASGI headers LAST-WINS on a repeated name rather than
    #: joining it, so a proxy-inserted second line rewrites a covered value with nothing
    #: anywhere to notice.
    raw_headers: tuple[tuple[bytes, bytes], ...]
    #: The body bytes, exactly as received.
    body: bytes
    #: The body was longer than the configured cap, so only what fits was retained. The
    #: replay downstream is lossless either way; this says the DIGEST cannot be computed.
    over_cap: bool = False
    #: The client disconnected mid-body. There is nothing to verify and nothing to answer.
    complete: bool = True

    def presents_signature(self) -> bool:
        """Whether EITHER signature header is present.

        Both absent, exactly one present, and both present are three different outcomes, and
        only the third is a question the SDK can be asked. Exactly one present is a REFUSAL
        the SDK raises (``request_signature_header_malformed``): the two headers are a bound
        pair, and one without the other is a downgrade vector where a proxy strips
        ``Signature-Input`` and leaves ``Signature`` (security.mdx @ v3.1.1 :1225). So this
        answers "either", and the verifier sorts out which.

        A METHOD on the capture rather than a helper beside each caller, because it has two
        callers that must never disagree: ``_resolve_identity`` asks it to decide whether an
        absent bearer is an absent CREDENTIAL, and the verifier asks it to decide whether to
        run the checklist. Two copies of that test would be two definitions of "this request
        is signed", and the disagreement they could produce is a 401 for a caller that did
        present a credential.
        """
        return any(name.lower() in (b"signature", b"signature-input") for name, _value in self.raw_headers)

    def headers(self) -> Mapping[str, str]:
        """The captured lines as ONE mapping — what the resolver reads this request's headers from.

        The same argument :func:`joined_headers` makes, in the place that makes it binding:
        whenever an HTTP message WAS captured, its lines are the authority, and the three
        transports stop each handing the resolver a container of their own. A transport that
        captured nothing (an in-process invocation, MCP's ``get_http_headers`` returning
        ``{}``) has no lines to be the authority, and the resolver keeps what it was given.
        """
        return joined_headers(self.raw_headers)


def captured_exchange(scope: Mapping[str, Any]) -> HttpExchange | None:
    """The capture for this request, or ``None`` when nothing captured one.

    ``None`` is the honest answer for a call that reached a tool outside an HTTP request at
    all — an in-process invocation, or MCP's ``get_http_headers`` returning ``{}``. The
    verifier reads it as "this request presented no signature", which is what it is.
    """
    exchange = (scope.get("state") or {}).get(CAPTURED_EXCHANGE)
    return exchange if isinstance(exchange, HttpExchange) else None


@dataclass(frozen=True)
class _BufferedBody:
    """A fully-read request body plus a ``receive`` that replays it downstream."""

    body: bytes
    receive: Receive
    complete: bool
    over_cap: bool


async def _buffer_body(receive: Receive, max_bytes: int) -> _BufferedBody:
    """Read the body up to *max_bytes*, and return a ``receive`` that replays it whole.

    LOSSLESS in every exit. ``more_body`` on the replayed message tells the app whether to
    keep reading the rest off the raw channel:

    * whole body read           -> ``complete``, one final message;
    * cap hit on the last chunk -> ``over_cap`` AND ``complete`` — the body is all here, it
      is merely too big to HASH;
    * cap hit mid-body          -> ``over_cap``, ``more_body=True``, remainder streams
      through untouched;
    * client disconnected       -> what arrived, then the disconnect, which must still reach
      the app (a one-shot closure returning a fixed message would swallow it).
    """
    chunks: list[bytes] = []
    size = 0
    trailing: list[MutableMapping[str, Any]] = []
    complete = False
    over_cap = False
    more_body = True

    while more_body:
        message = await receive()
        if message["type"] == "http.disconnect":
            trailing.append(message)
            break
        chunk = bytes(message.get("body", b""))
        chunks.append(chunk)
        size += len(chunk)
        more_body = bool(message.get("more_body", False))
        if size > max_bytes:
            over_cap = True
            complete = not more_body
            break
    else:
        complete = True

    body = b"".join(chunks)
    pending: list[MutableMapping[str, Any]] = []
    if chunks or complete:
        pending.append({"type": "http.request", "body": body, "more_body": not complete})
    pending.extend(trailing)

    async def replay() -> MutableMapping[str, Any]:
        if pending:
            return pending.pop(0)
        return await receive()

    return _BufferedBody(body=body, receive=replay, complete=complete, over_cap=over_cap)


def _signed_path(scope: Mapping[str, Any]) -> str:
    """The path AS SENT — percent-encoding intact, mount prefix intact.

    Deliberately the OPPOSITE of a route-table path read, and a blanket "always use
    ``raw_path``" rule would be a defect: a router needs the DECODED path with ``root_path``
    stripped, because it must agree with the Starlette router that dispatches. This feeds
    ``@target-uri``, whose only authority is the wire.

    * ``raw_path`` is BYTES and ALREADY carries ``root_path`` (uvicorn builds
      ``full_raw_path`` that way in both its h11 and httptools implementations). The client
      signed the URL it dialed, mount prefix included, so it is NOT stripped.
    * The decode is STRICT ASCII. A non-ASCII raw path is unrepresentable in a signed
      ``@target-uri`` — percent-encoding is mandatory on the wire — so it is a malformed
      request, not an encoding puzzle. ``latin-1`` would mojibake it into a base that could
      still VERIFY, and UTF-8 would mask it; both are silent-acceptance bugs. Capture keeps
      the decoded path in that case and the verifier refuses it, because refusing is a
      DECISION and this module makes none.
    * Anything from the first ``?`` is dropped: some ASGI servers and test clients put the
      query INSIDE ``raw_path``, and the query has exactly one source
      (``scope["query_string"]``).
    * ``raw_path`` absent -> the decoded path, with the degradation known and accepted: on
      such a server the encoded bytes are gone before we are called. Failing every signed
      request instead would be worse.
    """
    raw = scope.get("raw_path")
    if not isinstance(raw, bytes | bytearray):
        return str(scope.get("path", ""))
    try:
        return bytes(raw).decode("ascii").split("?", 1)[0]
    except UnicodeDecodeError:
        return str(scope.get("path", ""))


def _target_uri(scope: Mapping[str, Any]) -> str:
    """The URL the signature covers, as the CLIENT addressed it.

    Authority comes from the ``Host`` header as received and the scheme from the first hop of
    ``X-Forwarded-Proto`` — the client-facing scheme our own edge proxy terminated. Never
    from proxy ROUTING state (``Apx-Incoming-Host`` and friends), which security.mdx step 10
    forbids deriving identity from: the signer signed the URL it dialed, and a rewritten one
    would fail ``@target-uri`` on every legitimate request behind TLS termination.

    Through :func:`joined_headers`, not a dict comprehension of its own. This function used
    to build one, and it collapsed last-wins — so a second ``Host`` line silently chose the
    authority that entered the signature base while the resolver, reading its own container,
    could choose the other. The ``Host`` rule has one answer per request now rather than one
    per reader.
    """
    headers = joined_headers(scope.get("headers", []))

    forwarded = headers.get("x-forwarded-proto", "")
    scheme = forwarded.split(",")[0].strip().lower() if forwarded else ""
    if scheme not in ("http", "https"):
        scheme = str(scope.get("scheme", "http"))

    authority = headers.get("host")
    if not authority:
        server = scope.get("server") or ("", None)
        authority = f"{server[0]}:{server[1]}" if server[1] else str(server[0])

    query = scope.get("query_string", b"").decode("latin-1")
    return f"{scheme}://{authority}{_signed_path(scope)}" + (f"?{query}" if query else "")


class SignedExchangeCapture:
    """Record the HTTP message on ``scope["state"]`` so the boundary's resolver can verify it.

    A pure-ASGI class, not ``BaseHTTPMiddleware``: that base sits on the RESPONSE path,
    wrapping the downstream app in a task and pumping its response through a queue, which is
    what breaks MCP's streamable-HTTP responses — and it loses ContextVar writes across the
    request (Starlette issue #1729). This touches the request and gets out of the way; it
    never observes the response at all.

    It must be registered INNERMOST of the app's middlewares, and the reason is specific: any
    middleware that REWRITES the request body must not run between the signer and this
    capture, or the digest would be computed over bytes the signer never sent.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: MutableMapping[str, Any], receive: Receive, send: Send) -> None:
        # ``path_from_asgi_scope``, never ``scope["path"]``: this predicate has to select
        # exactly the requests the ROUTER dispatches, and Starlette routes on the path with
        # ``root_path`` stripped (``starlette.routing.get_route_path``). Read raw, the two
        # disagree the moment the app is mounted under a prefix — uvicorn ``--root-path``,
        # ``FastAPI(root_path=...)``, a proxy that sets one — and they disagree SILENTLY in
        # the one direction that matters: the router reaches every AdCP tool while nothing
        # captures the message, so ``SignatureSubject.exchange`` is ``None`` and the verifier
        # reads every signed request as unsigned. Nothing raises and nothing logs.
        #
        # ``_signed_path`` below is the deliberate OPPOSITE and must stay that way: it feeds
        # ``@target-uri``, whose only authority is the URL the client dialled, prefix and
        # percent-encoding intact. One rule per question, and the two are not the same rule.
        if scope.get("type") != "http" or not is_adcp_surface(path_from_asgi_scope(scope)):
            await self.app(scope, receive, send)
            return

        from src.core.config import get_settings

        buffered = await _buffer_body(receive, get_settings().signing.max_signed_body_bytes)
        # ``setdefault``: ``scope["state"]`` is supplied by the ASGI server from lifespan
        # state, and a server or a test client that supplies none must not make the capture
        # disappear silently.
        scope.setdefault("state", {})[CAPTURED_EXCHANGE] = HttpExchange(
            method=str(scope.get("method", "GET")),
            url=_target_uri(scope),
            raw_headers=tuple((bytes(name), bytes(value)) for name, value in scope.get("headers", [])),
            body=buffered.body,
            over_cap=buffered.over_cap,
            complete=buffered.complete,
        )
        await self.app(scope, buffered.receive, send)


@dataclass(frozen=True)
class SignatureSubject:
    """What the BOUNDARY knows about the request, as the verifier needs it.

    Built by ``invoke_tool``, which is the only place all three are known at once: the
    registry key it dispatched on, the validated request it read the escalation off, and the
    capture the transport's ASGI scope carried.
    """

    #: The AdCP operation name — the registry key. Graded against the AdCP-namespace buckets
    #: and NEVER against ``protocol_methods_*``; see ``bucket_for``.
    operation: str
    #: security.mdx :1462-1465 — the payload registers webhook credentials, so a signature is
    #: mandatory whatever bucket the operation falls in.
    registers_credentials: bool
    #: The HTTP message, captured where its bytes still existed. ``None`` for an invocation
    #: that did not arrive over HTTP at all, which presents no signature by definition.
    exchange: HttpExchange | None
