"""The ``WWW-Authenticate`` a refused signature produces, END TO END and byte-for-byte.

AdCP 3.1.1 ``security.mdx`` § "``WWW-Authenticate`` format":

    AdCP does NOT define a realm value for request-signing challenges. Verifiers MUST emit
    ``WWW-Authenticate: Signature error="<code>"`` with no ``realm`` parameter and no other
    parameters.

and ``dist/compliance/3.1.1/universal/signed-requests.yaml`` grades that string directly,
so this is a conformance artifact rather than an internal detail.

WHY THIS FILE EXISTS AT ALL, and what it is really guarding
------------------------------------------------------------
Moving verification into ``_resolve_identity`` (#1721's boundary) means the challenge is no
longer written by the code that refused. The refusal is raised as an ``AdCPSalesAgentError``,
rendered into an AdCP envelope by ``failure_response``, serialized by the transport, and only
then read back off the FINISHED body by ``AuthChallengeResponder``. Four hops, and the SPECIFIC
code has to survive all of them.

Nothing about that is visible from a unit test of either end. A refusal collapsed to a generic
``AUTH_INVALID`` somewhere in the middle passes every test of the verifier (it still refuses)
and every test of the renderer (it still writes a challenge), and fails conformance — the
challenge would read ``Bearer error="invalid_token"`` where the vector demands
``Signature error="request_signature_required"``. So the assertion below runs the whole chain
and compares the exact string.
"""

from __future__ import annotations

import json

import pytest
from adcp.signing.errors import REQUEST_TO_WEBHOOK_CODE, SignatureVerificationError
from adcp.signing.middleware import unauthorized_response_headers

from src.core.auth_middleware import AuthChallengeResponder
from src.core.exceptions import adcp_error_for
from src.core.resolved_identity import TransportProtocol
from src.core.tools._boundary import failure_response
from src.core.tools._wire import to_wire

#: Every code the SDK's request-family taxonomy defines. Read from the SDK, not listed, for
#: the same reason production reads it from there: a transcribed roster drifts.
ALL_CODES = sorted(REQUEST_TO_WEBHOOK_CODE)


async def _challenge_on_the_wire(body: bytes) -> tuple[int, dict[bytes, bytes]]:
    """Drive *body* through ``AuthChallengeResponder`` and return its (status, headers).

    The real middleware over a real ASGI exchange, because the thing being graded is what it
    does to a FINISHED response: it buffers, reads the AdCP code out of the JSON, and rewrites
    the status line. A unit call to ``_challenge_for_code`` would grade the lookup while
    leaving the four hops in front of it unexamined.
    """
    sent: list[dict] = []

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def capture(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await AuthChallengeResponder(app)({"type": "http", "path": "/api/v1/products"}, receive, capture)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], dict(start["headers"])


@pytest.mark.parametrize("code", ALL_CODES)
@pytest.mark.asyncio
async def test_the_specific_code_survives_into_the_challenge(code: str) -> None:
    """A signature refusal reaches the wire as ``Signature error="<its own code>"``.

    The whole chain: the typed error the resolver raises -> ``failure_response`` ->
    ``to_wire`` -> the JSON body -> ``AuthChallengeResponder``. Parameterized over all 27 codes
    because the failure mode being guarded is a COLLAPSE, and a collapse is invisible when only
    one code is exercised.
    """
    error = adcp_error_for(SignatureVerificationError(code, step=1, message="graded here"))
    response = failure_response(TransportProtocol.REST, "get_products", error)
    body = json.dumps(to_wire(response)).encode()

    status, headers = await _challenge_on_the_wire(body)

    assert status == 401, f"a {code} refusal must reach the wire as 401, got {status}"
    assert headers[b"www-authenticate"] == f'Signature error="{code}"'.encode(), (
        f"the challenge for {code} must name that code byte-for-byte; the compliance vectors "
        f"grade this string, and a generic AUTH_INVALID here passes our tests and fails them"
    )


@pytest.mark.parametrize("code", ALL_CODES)
def test_the_challenge_matches_the_sdks_own_builder(code: str) -> None:
    """Our one expression of the string equals the SDK's ``unauthorized_response_headers``.

    The SDK stays the CROSS-CHECK rather than the source: production cannot call that helper,
    because the renderer holds a code read off a finished body and the helper takes the
    exception. This is what stops the two spellings drifting anyway.
    """
    from src.core.errors.signature_codes import challenge_for

    expected = unauthorized_response_headers(SignatureVerificationError(code))["WWW-Authenticate"]
    assert challenge_for(code) == expected


def test_the_body_carries_no_internal_detail() -> None:
    """The refusal's diagnostic text is server-log only, never on the buyer's wire.

    AdCP 3.1.1 ``transport-errors.mdx`` § Security Considerations: error responses flow
    through LLM context and MUST NOT carry internal detail. The verifier's own exception
    carries a checklist step and a sentence naming what it refused, and it rides
    ``internal_detail`` — which ``AdcpErrorResponse.of`` ignores. What the buyer gets is the
    CODE plus the table's text for it.
    """
    secret = "keyid 'abc' resolved against tenant xyz's internal keystore"
    error = adcp_error_for(SignatureVerificationError("request_signature_key_unknown", step=7, message=secret))
    body = json.dumps(to_wire(failure_response(TransportProtocol.REST, "get_products", error)))

    assert "request_signature_key_unknown" in body, "the code is the buyer's actionable signal"
    assert secret not in body
    assert "keystore" not in body
