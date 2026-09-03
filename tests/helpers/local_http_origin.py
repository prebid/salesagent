"""Suite-neutral local HTTP origin: one stdlib server core, two consumers.

Two test suites need a throwaway HTTP server on an ephemeral port:

* ``tests/e2e/_webhook_capture.py`` captures the webhooks the sales agent posts
  back. It binds ``0.0.0.0`` and advertises a *different* callback host, because
  the server runs in a container and must reach the receiver by network alias.
* ``tests/integration/`` drives the outbound egress seam against a real origin
  and needs to program the response (status, headers, redirect, delay, chunked
  body, a per-attempt response sequence, or no response at all) and count exact
  hits.

Both are the same bootstrap — bind a free port, serve on a daemon thread, hand
back a URL, tear the socket down — so the bootstrap lives here once and the
listen host and the callback host are *parameters*, not forks. No new
dependency: stdlib ``http.server`` only.
"""

from __future__ import annotations

import contextlib
import json
import socket
import ssl
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from time import sleep as _sleep
from typing import Any, ClassVar

# Default body for a programmable origin that a test never configures.
_DEFAULT_BODY = b'{"ok": true}'

# Headers the origin writes for itself, from fields that already exist on
# :class:`OriginResponse`. Programming one of these through the extra-header
# passthrough would emit it TWICE — and a duplicate ``Content-Length`` is a
# framing bug the client reports as a protocol error, i.e. a test failure with
# nothing to do with what the test meant to grade. Refused loudly at
# programming time rather than silently dropped: a test that means to set
# ``Content-Type`` has a field for it.
_ORIGIN_OWNED_HEADERS = frozenset({"content-type", "content-length", "connection", "transfer-encoding", "location"})

# ``_sleep`` is bound at import, on purpose. A caller under test may be patching
# ``<its module>.time.sleep`` to make its own retry backoff instant — and because
# ``import time`` yields the one shared module object, that patch lands on
# ``time.sleep`` for the whole process, this server thread included. Looking the
# name up through the module at call time would therefore make the origin's
# stall silently vanish, and a stall that does not stall cannot trip a timeout.


class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` bound over IPv6, for hosts that resolve to ``::1`` first."""

    address_family = socket.AF_INET6


def _server_class_for(listen_host: str) -> type[ThreadingHTTPServer]:
    """The server class whose address family matches ``listen_host``'s FIRST resolution.

    ``ThreadingHTTPServer`` is AF_INET-only, which is fine for the literal
    loopback addresses tests usually bind — but a *name* like ``localhost``
    resolves to BOTH families, and the egress seam's IP pin takes the first
    ``getaddrinfo`` answer (``adcp.signing`` resolves once, then pins every
    connect to that address). On a resolver that orders ``::1`` first, an
    AF_INET server would sit on ``127.0.0.1`` while the pinned client dials
    ``[::1]`` and gets a connection refused that has nothing to do with what
    the test meant to grade. Resolving here with the same call the pin makes —
    ``getaddrinfo(host, None)``, first answer — keeps origin and client on one
    address deterministically, whichever family the platform prefers.
    """
    family = socket.getaddrinfo(listen_host, None)[0][0]
    return _IPv6ThreadingHTTPServer if family == socket.AF_INET6 else ThreadingHTTPServer


@contextlib.contextmanager
def serve_in_thread(
    handler_class: type[BaseHTTPRequestHandler],
    *,
    listen_host: str = "127.0.0.1",
    port: int = 0,
    server_attrs: dict[str, Any] | None = None,
    ssl_context: ssl.SSLContext | None = None,
    request_queue_size: int = 128,
) -> Iterator[ThreadingHTTPServer]:
    """Serve ``handler_class`` on ``port`` of ``listen_host``, in a daemon thread.

    ``port`` defaults to 0 (ephemeral): binds any free port and reads the
    kernel-assigned value back off ``server.server_address`` rather than
    probing for a free port and rebinding it — the probe-close-rebind form
    races another xdist worker between the close and the rebind. Pass a fixed
    port for a long-lived service with a well-known address (GH #1802).

    ``server_attrs`` are set on the server instance *before* the serving thread
    starts, so a handler may read per-server state (e.g. the programmable
    origin) on its very first request without a data race.

    ``request_queue_size`` is the listen backlog, and it is a CONSTRUCTOR
    parameter rather than a ``server_attrs`` entry because socketserver reads it
    at BIND time — setting it after construction, which is all ``server_attrs``
    can do, is too late to have any effect. It defaults to 128 rather than
    socketserver's 5: a burst of ~20 concurrent writers against a backlog-5
    listener reliably produces ``ConnectionResetError`` on a many-core box and
    passes on a quieter one, which is exactly how a backlog-too-small bug hides
    until CI is busy.

    ``ssl_context``, if given, wraps the listening socket in TLS before the
    serving thread starts accepting — every accepted connection then comes
    back already negotiated, so nothing downstream of ``accept()`` changes.
    Callers build the context from the SAME generated CA/leaf material the
    rest of the e2e stack trusts (``scripts/dev/gen_test_tls.py``); this
    helper does not generate or know about any certificate itself.
    """
    # Subclass to carry the backlog: request_queue_size is read during bind, so it
    # has to be a CLASS attribute present before construction — the same reason
    # server_attrs (setattr, post-construct) cannot carry it.
    base = _server_class_for(listen_host)
    server_class = type(base.__name__, (base,), {"request_queue_size": request_queue_size})
    server = server_class((listen_host, port), handler_class)
    for name, value in (server_attrs or {}).items():
        setattr(server, name, value)
    if ssl_context is not None:
        server.socket = ssl_context.wrap_socket(server.socket, server_side=True)

    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@dataclass(frozen=True)
class OriginRequest:
    """One request the origin actually received — headers and body included.

    The headers are the raw ``http.client.HTTPMessage``, so lookups are
    case-insensitive exactly as they are on the wire; a plain ``dict`` would
    make ``"X-ADCP-Signature" in request.headers`` depend on the casing the
    sender happened to choose.
    """

    method: str
    path: str
    headers: Any = field(default_factory=dict)
    body: bytes = b""

    def json(self) -> Any:
        """Decode the request body as JSON — what the origin was actually sent.

        This is the honest counterpart of reading ``call_args.kwargs["json"]``
        off a transport mock: that reads back the object the caller *passed*,
        which proves nothing about serialization; this reads the bytes that
        crossed the socket.
        """
        return json.loads(self.body)


@dataclass(frozen=True)
class OriginResponse:
    """One programmed answer: everything the origin does for a single request.

    A ``LocalOrigin`` in ``fixed`` mode answers every request with a snapshot of
    its own fields; in ``sequence`` mode it answers with the next entry. Making
    both the same object is what lets a sequence express *faults* — a hang-up, a
    stall past the caller's timeout, a body that does not decode — and not just
    a list of status codes. A retry policy is only graded honestly when the
    failure it recovers from is a real one.
    """

    mode: str = "fixed"
    status: int = 200
    body: bytes = _DEFAULT_BODY
    content_type: str = "application/json"
    location: str = ""
    total_bytes: int = 0
    chunk_size: int = 64 * 1024
    delay_seconds: float = 0.0
    # Extra response headers, as an ordered pair tuple rather than a dict so the
    # dataclass stays frozen-and-copyable through ``replace()``. A real origin
    # answers with more than a status: ``Retry-After`` on a 429 is a *protocol*
    # instruction to the caller, and a retry policy that claims to honour it can
    # only be graded against a server that actually sent it.
    headers: tuple[tuple[str, str], ...] = ()


def _checked_headers(headers: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    """Normalise extra headers to pairs, refusing the ones the origin owns."""
    if not headers:
        return ()
    for name in headers:
        if name.lower() in _ORIGIN_OWNED_HEADERS:
            raise ValueError(
                f"{name!r} is written by the origin from its own fields — "
                f"set the matching OriginResponse field instead of passing it as an extra header"
            )
    return tuple((str(name), str(value)) for name, value in headers.items())


def responds(
    status: int = 200,
    *,
    body: bytes = _DEFAULT_BODY,
    delay_seconds: float = 0.0,
    headers: Mapping[str, str] | None = None,
) -> OriginResponse:
    """A complete, well-formed response — optionally after stalling ``delay_seconds``.

    A stall longer than the caller's timeout is how a *real* timeout is
    produced: the request provably arrived (it is counted in :attr:`LocalOrigin.hits`)
    and the caller's own clock is what gives up.

    ``headers`` are extra response headers, e.g. ``{"Retry-After": "5"}``. They
    go through the factory rather than only through
    :meth:`LocalOrigin.respond_with` because a *sequence* entry is built here —
    "429 with Retry-After, then 200" is a per-answer fact, not a per-origin one.
    """
    return OriginResponse(
        mode="fixed",
        status=status,
        body=body,
        delay_seconds=delay_seconds,
        headers=_checked_headers(headers),
    )


def hangs_up() -> OriginResponse:
    """Accept the request, then close the connection without a status line."""
    return OriginResponse(mode="error")


def sends_chunked_body(total_bytes: int, *, status: int = 200, chunk_size: int = 64 * 1024) -> OriginResponse:
    """A chunked answer of ``total_bytes``, as a SEQUENCE entry.

    :meth:`LocalOrigin.respond_chunked` programs the same oversized answer for
    every request, which can only ever trip a size cap on the FIRST attempt.
    This is the per-answer form, so a sequence can put the oversized body after
    something else — "429 with a Retry-After, then a 200 whose body runs past
    the cap" is the only way to reach the cap on a later attempt, and that is
    the sequence that grades whether the earlier answer's state leaks into the
    abort.
    """
    return OriginResponse(mode="chunked", status=status, total_bytes=total_bytes, chunk_size=chunk_size)


def sends_malformed_body() -> OriginResponse:
    """Answer with valid headers and a body that violates chunked framing.

    The status line and headers parse, so this is not a connection failure; the
    body then fails to decode mid-read. That distinction matters because HTTP
    clients classify the two differently (``requests`` raises
    ``ChunkedEncodingError``, not ``ConnectionError``), and a retry policy that
    only handles connection failures would drop this one on the floor.
    """
    return OriginResponse(mode="malformed")


@dataclass
class LocalOrigin:
    """Control surface and request log of a running local origin.

    A test programs the next response through one of the ``respond_*`` methods
    and then asserts on :attr:`hits` — the exact number of times the origin was
    reached. Exact hit counts are the whole point: "the redirect was not
    followed" and "a 404 was not retried" are only provable by counting.
    """

    host: str = "127.0.0.1"
    port: int = 0
    scheme: str = "http"
    requests: list[OriginRequest] = field(default_factory=list)

    # Programmable response state. ``mode`` selects which branch the handler takes.
    mode: str = "fixed"
    status: int = 200
    body: bytes = _DEFAULT_BODY
    content_type: str = "application/json"
    location: str = ""
    delay_seconds: float = 0.0
    total_bytes: int = 0
    chunk_size: int = 64 * 1024
    headers: tuple[tuple[str, str], ...] = ()
    sequence: list[OriginResponse] = field(default_factory=list)

    @property
    def base_url(self) -> str:
        """``{scheme}://host:port`` — no trailing slash. ``scheme`` defaults to plain ``http``;
        callers running the origin over TLS (see :func:`run_local_origin`'s ``ssl_context``)
        set it to ``https`` so assertions and URLs built from this origin agree with what
        actually went over the wire.
        """
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def hits(self) -> int:
        """Number of requests the origin actually received."""
        return len(self.requests)

    @property
    def paths(self) -> list[str]:
        return [req.path for req in self.requests]

    def respond_with(
        self,
        status: int = 200,
        *,
        body: bytes = _DEFAULT_BODY,
        content_type: str = "application/json",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Answer every subsequent request with a fixed status, body and extra headers.

        ``headers`` is how a fixed-mode origin says something the status alone
        cannot — ``{"Retry-After": "5"}`` on a 429 is the origin instructing the
        caller how long to wait, and a policy that claims to honour it is only
        graded honestly against a server that really sent the header.
        """
        self.mode = "fixed"
        self.status = status
        self.body = body
        self.content_type = content_type
        self.headers = _checked_headers(headers)

    def respond_in_sequence(
        self,
        responses: Sequence[tuple[int, bytes] | OriginResponse],
        *,
        content_type: str = "application/json",
    ) -> None:
        """Answer each subsequent request with the next entry; the last entry repeats.

        This is what a real origin needs in order to express *recovery*: ``503,
        503, 200`` is a transient failure that clears on the third attempt, and
        only a queue can say that — a fixed status can say "always fails" or
        "always succeeds" and nothing in between.

        An entry is either a ``(status, body)`` pair or a full
        :class:`OriginResponse`, so the failure a caller recovers *from* can be
        a genuine fault — :func:`hangs_up`, :func:`sends_malformed_body`, or a
        :func:`responds` with a stall past the caller's timeout — and not merely
        an unhappy status code.

        The last entry repeating rather than the queue running dry means a test
        never has to know how many attempts the caller will actually make. It
        programs the recovery point and then asserts on :attr:`hits`, which is
        the number that grades the retry policy.
        """
        if not responses:
            raise ValueError("respond_in_sequence needs at least one response")
        self.mode = "sequence"
        self.sequence = [
            item if isinstance(item, OriginResponse) else responds(item[0], body=item[1]) for item in responses
        ]
        self.content_type = content_type

    def take_from_sequence(self) -> OriginResponse:
        """Consume the next programmed response; the final entry repeats forever.

        Consuming the queue rather than indexing it by hit count keeps "the last
        entry repeats" as a single expression and keeps :meth:`reset` honest —
        there is no separate counter that a reset could forget to clear.
        """
        if len(self.sequence) > 1:
            return self.sequence.pop(0)
        return self.sequence[0]

    def next_answer(self) -> OriginResponse:
        """What the origin does for the request it is serving right now.

        In ``sequence`` mode this consumes the queue; in every other mode it is
        a snapshot of the fixed programming. One object either way, so the
        handler has exactly one dispatch table and a mode cannot be reachable
        from a fixed response but not from a sequenced one.

        A sequence entry that carries no stall — or no extra headers — of its own
        inherits the origin's :meth:`delay` and :meth:`respond_with` headers, so
        programming either once still applies it to every answer.
        """
        if self.mode != "sequence":
            return OriginResponse(
                mode=self.mode,
                status=self.status,
                body=self.body,
                content_type=self.content_type,
                location=self.location,
                total_bytes=self.total_bytes,
                chunk_size=self.chunk_size,
                delay_seconds=self.delay_seconds,
                headers=self.headers,
            )
        answer = self.take_from_sequence()
        if not answer.delay_seconds and self.delay_seconds:
            answer = replace(answer, delay_seconds=self.delay_seconds)
        if not answer.headers and self.headers:
            answer = replace(answer, headers=self.headers)
        if answer.content_type == "application/json" and self.content_type != "application/json":
            answer = replace(answer, content_type=self.content_type)
        return answer

    def close_without_responding(self) -> None:
        """Accept the request, record the hit, then close the connection with no response.

        The hit is recorded first, so the request provably arrived; nothing is
        written after it, so the client sees the socket close before a status
        line and raises a *genuine* transport error of its own making. A mock
        with an exception on its ``side_effect`` can only restate which
        exception the test already chose; this proves the client raises it.
        """
        self.mode = "error"

    def redirect_to(self, location: str, *, status: int = 302) -> None:
        """Answer with a redirect to ``location`` (an arbitrary URL, including a blocked one)."""
        self.mode = "redirect"
        self.status = status
        self.location = location

    def respond_chunked(self, total_bytes: int, *, chunk_size: int = 64 * 1024, status: int = 200) -> None:
        """Answer with ``Transfer-Encoding: chunked`` and ``total_bytes`` of body.

        Chunked rather than a large ``Content-Length``: a body whose size is not
        declared up front is what forces a reader to accumulate and abort, so
        this is the mode that grades a response-size cap honestly.
        """
        self.mode = "chunked"
        self.status = status
        self.total_bytes = total_bytes
        self.chunk_size = chunk_size

    def delay(self, seconds: float) -> None:
        """Stall ``seconds`` before answering, after logging the hit.

        The hit is recorded *before* the stall, so a caller that times out still
        shows up in :attr:`hits` — which is how a per-attempt timeout is graded.
        """
        self.delay_seconds = seconds

    def record(self, method: str, path: str, headers: Any = None, body: bytes = b"") -> None:
        self.requests.append(
            OriginRequest(method=method, path=path, headers=headers if headers is not None else {}, body=body)
        )

    @property
    def last_request(self) -> OriginRequest:
        """The most recent request, or a failed assertion naming the silence."""
        assert self.requests, "The origin received no requests at all"
        return self.requests[-1]

    def payloads(self) -> list[Any]:
        """Every request body decoded as JSON, oldest first."""
        return [req.json() for req in self.requests]

    def reset(self) -> None:
        self.requests.clear()
        self.mode = "fixed"
        self.status = 200
        self.body = _DEFAULT_BODY
        self.content_type = "application/json"
        self.location = ""
        self.delay_seconds = 0.0
        self.total_bytes = 0
        self.headers = ()
        self.sequence.clear()


class ProgrammableOriginHandler(BaseHTTPRequestHandler):
    """Serve whatever ``self.server.origin`` is currently programmed to serve."""

    # HTTP/1.1 is required for Transfer-Encoding: chunked. Every response also
    # carries ``Connection: close`` so each request gets its own connection —
    # that keeps hit counting unambiguous and avoids keep-alive interactions
    # with a client that aborts mid-body.
    protocol_version = "HTTP/1.1"

    def _serve(self) -> None:
        origin: LocalOrigin = self.server.origin  # type: ignore[attr-defined]

        content_length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(content_length) if content_length else b""

        origin.record(self.command, self.path, self.headers, body)

        answer = origin.next_answer()
        if answer.delay_seconds:
            _sleep(answer.delay_seconds)

        try:
            self._SENDERS[answer.mode](self, answer)
        except (BrokenPipeError, ConnectionResetError):
            # The caller aborted — a timeout or a size cap tripping is exactly
            # the behaviour under test, so this is expected, not a failure.
            pass

    def _send_redirect(self, answer: OriginResponse) -> None:
        self.send_response(answer.status)
        self.send_header("Location", answer.location)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def _send_fixed(self, answer: OriginResponse) -> None:
        """Write one complete, length-declared response, plus any extra headers.

        The extra headers are written alongside the framing ones the origin owns;
        ``_checked_headers`` has already refused any name that would duplicate
        one of those, so no header is ever emitted twice.
        """
        self.send_response(answer.status)
        self.send_header("Content-Type", answer.content_type)
        self.send_header("Content-Length", str(len(answer.body)))
        self.send_header("Connection", "close")
        for name, value in answer.headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(answer.body)

    def _send_nothing(self, answer: OriginResponse) -> None:
        """Write no bytes at all and close the connection.

        ``close_connection`` ends ``BaseHTTPRequestHandler``'s keep-alive loop,
        after which ``socketserver`` closes the socket. The client has already
        sent a complete request and gets a close instead of a status line, which
        is a real protocol violation on the wire — so the transport error it
        raises is its own, not one a test handed it.
        """
        self.close_connection = True

    def _send_chunked(self, answer: OriginResponse) -> None:
        self._begin_chunked(answer.status)

        remaining = answer.total_bytes
        filler = b"x" * answer.chunk_size
        while remaining > 0:
            chunk = filler[: min(answer.chunk_size, remaining)]
            self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
            remaining -= len(chunk)
        self.wfile.write(b"0\r\n\r\n")

    def _send_malformed(self, answer: OriginResponse) -> None:
        """Announce a chunked body and then write a chunk-size line that is not a number.

        The headers are valid, so the client accepts the response and begins
        reading; the framing then fails mid-body. That is a different failure
        class from a connection that never answered, and clients report it as
        such.
        """
        self._begin_chunked(answer.status)
        self.wfile.write(b"not-a-chunk-length\r\n")
        self.close_connection = True

    def _begin_chunked(self, status: int) -> None:
        """Send the header block that announces a chunked body."""
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()

    # Mode -> sender. A table rather than an if/elif ladder: adding a mode is
    # one entry, and an unrecognised mode is a KeyError raised at the origin
    # rather than a silent fall-through to "fixed" that would quietly grade the
    # wrong behaviour.
    _SENDERS: ClassVar[dict[str, Callable[[Any, OriginResponse], None]]] = {
        "fixed": _send_fixed,
        "redirect": _send_redirect,
        "chunked": _send_chunked,
        "malformed": _send_malformed,
        "error": _send_nothing,
    }

    do_GET = _serve
    do_POST = _serve
    do_PUT = _serve
    do_DELETE = _serve

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        """Suppress HTTP server logs during tests."""


@contextlib.contextmanager
def run_local_origin(
    *, listen_host: str = "127.0.0.1", ssl_context: ssl.SSLContext | None = None
) -> Iterator[LocalOrigin]:
    """Run a programmable local origin and yield its control surface.

    In-process callers only, so the listen host is loopback by default: nothing
    outside this machine should be able to reach a test origin.

    ``ssl_context``, if given, serves the origin over TLS (see
    :func:`serve_in_thread`) and the yielded origin's ``base_url``/``scheme``
    report ``https`` accordingly — existing callers that never pass it keep the
    plain-``http`` origin unchanged.
    """
    origin = LocalOrigin(host=listen_host, scheme="https" if ssl_context is not None else "http")
    with serve_in_thread(
        ProgrammableOriginHandler,
        listen_host=listen_host,
        server_attrs={"origin": origin},
        ssl_context=ssl_context,
    ) as server:
        origin.port = server.server_address[1]
        yield origin
