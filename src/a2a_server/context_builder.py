"""Carry the captured HTTP message onto the A2A call context, beside the headers.

The a2a-sdk hands a handler a :class:`ServerCallContext`, not a request, and its default
builder puts ``dict(request.headers)`` on ``state["headers"]`` — which is everything
``on_message_send`` needed until an RFC 9421 signature became a credential the boundary
reads. A signature covers ``@method``, ``@target-uri`` and the exact body bytes as well as
the headers, so one more value has to travel the same road.

It is a SUBCLASS of the SDK's own builder, not a reimplementation: the user, the auth scope
and the requested-extensions parse are the SDK's business and copying them here would be
three things to keep in step with an SDK bump. This adds one key.

``request.scope`` and not ``await request.body()``: the A2A routes are appended directly to
the FastAPI route table rather than mounted as a sub-application, so ``scope["state"]`` —
where :class:`~src.core.signing.capture.SignedExchangeCapture` left the capture — is visible
here. ``build`` is synchronous, so awaiting a body was never an option anyway.
"""

from __future__ import annotations

from a2a.server.context import ServerCallContext
from a2a.server.routes.common import DefaultServerCallContextBuilder
from starlette.requests import Request

from src.core.signing.capture import captured_exchange

#: The key the capture is carried under on ``ServerCallContext.state``.
EXCHANGE_STATE_KEY = "adcp_signed_exchange"


class AdCPCallContextBuilder(DefaultServerCallContextBuilder):
    """The SDK's context, plus the captured HTTP message this request arrived as."""

    def build(self, request: Request) -> ServerCallContext:
        context = super().build(request)
        # ``None`` when nothing captured one — a non-AdCP path, or a test client that drives
        # the handler without the middleware. The handler passes it through and the verifier
        # reads it as "this request presented no signature", which is what it is.
        context.state[EXCHANGE_STATE_KEY] = captured_exchange(request.scope)
        return context
