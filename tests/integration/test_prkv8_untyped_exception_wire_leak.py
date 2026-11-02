"""The A2A JSON-RPC InternalError carries the two-layer envelope in its ``data``.

WHAT THIS FILE NO LONGER TESTS, and why. It began as a reproduction for
#1587: an untyped exception's own ``str()`` reaching the buyer as the
error ``message``. That leak is now structurally impossible rather than merely
fixed. ``AdCPSalesAgentError.__init__`` takes no ``message`` parameter at all,
and ``message`` is a read-only property returning ``CODE_TABLE[code].message``
(src/core/exceptions.py), so no raise site can interpolate anything into
buyer-facing text. ``on_message_send`` builds its JSON-RPC message from the failure
response's own error message, which is the same derived property.

An assertion that an invented marker string is absent from that text therefore
cannot fail unless the code table itself contains the marker. It was a tautology,
and the version that ran it across A2A, MCP, and REST was the same tautology
three times, needing real dispatch and a database to observe a value read from a
constant. AdCP 3.1.1 transport-errors.mdx still lists credentials, SQL,
hostnames, and stack traces as MUST-NOT — the table, and the ``internal_detail``
convention that routes raw text to the server log, are what satisfy it.

What remains is falsifiable and A2A-specific: the JSON-RPC envelope can carry a
code, or it can carry nothing, and only this path can say which.
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestInternalErrorCarriesTheEnvelope:
    """``on_message_send`` answers a pre-dispatch fault with a JSON-RPC error carrying the body.

    Only a fault raised before any tool ran reaches that path. A dispatched skill's own
    failure is the boundary's ``AdcpErrorResponse``, which ``_dispatch_skill`` serializes with
    ``to_wire``; tests/unit/test_error_envelope.py grades that body directly.

    The fault is a call context built from no request: the handler subscripts
    ``state["headers"]`` inside that outer try/except before skill dispatch, and a context
    without them raises there. Nothing raised inside dispatch can reach it: ``serve``
    answers every tool failure as a response.
    """

    def test_internal_error_carries_the_envelope_in_data(self, integration_db):
        import asyncio

        from a2a.server.routes.common import ServerCallContext
        from a2a.types import SendMessageRequest

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler, InternalError
        from tests.utils.a2a_helpers import create_a2a_message_with_skill

        handler = AdCPRequestHandler()

        message = create_a2a_message_with_skill(skill_name="get_products", parameters={"brief": "video ads"})
        params = SendMessageRequest(message=message)

        with pytest.raises(InternalError) as exc_info:
            asyncio.run(handler.on_message_send(params, ServerCallContext()))

        # PRESENCE, not the absence of a marker string. Whether the message leaks is settled
        # by construction; whether this path attaches an envelope at all is not, and an
        # empty data= would satisfy any absence check.
        envelope = exc_info.value.data
        assert envelope is not None, "the A2A JSON-RPC InternalError must carry the envelope in data="
        assert envelope.get("adcp_error", {}).get("code"), f"expected a code in InternalError.data: {envelope!r}"
