"""RED grader for GH #1802: one classifier, both seams agree (GH #1802).

``src/core/helpers/outbound_error_mapping.py`` housed ``raise_mapped_outbound_error``
(the raw ``send``/``asend`` seam) and the now-deleted ``mcp_seam_error_mapping.py``
housed ``raise_mapped_mcp_error`` (the guarded MCP seam) — each hand-wrote the SAME
status -> AdCP-class table, with drifting spellings (#1802's F2 finding).
tbrk.4 collapses both onto one ``adcp_error_for_status(status, *, retry_after,
provenance)`` classifier and, as part of that, retires ``raise_mapped_mcp_error``'s
``agent_label: str`` parameter in favour of the same ``provenance:
UrlProvenance`` union ``raise_mapped_outbound_error`` already takes (Epic B,
GH #1802) — D2 is binding: "the classifier takes the union, and
agent_label: str retires with Epic B's raise_mapped_outbound_error rewrite."

This file drives the SAME simulated origin behavior through BOTH seams and
asserts the two-layer wire envelope they each produce is identical where the
plan says it must be:

* a 429 with ``Retry-After: 7`` -> ``RATE_LIMITED`` with the same clamped
  ``retry_after`` on both;
* a 418 (a terminal 4xx neither seam retries) -> the same terminal class
  (``AdCPConfigurationError``) and wire code (``CONFIGURATION_ERROR``) on both;
* a 503, retried to exhaustion, on the OUTBOUND path only -> ``attempts`` and
  ``last_status`` survive onto the buyer-visible envelope ``details`` (the
  outbound seam's richer failure object; the MCP seam has no ``attempts`` to
  preserve — J3/J2 in tbrk.4's design record that asymmetry as inherent, not a
  bug this table grades away).

RED today, for the right reason: the first two classes call
``raise_mapped_mcp_error(..., provenance=..., logger=...)`` — the SIGNATURE
tbrk.4 will land — against the function's CURRENT signature,
``raise_mapped_mcp_error(exc, *, agent_label: str, logger)``. That is a
``TypeError`` raised from inside the test body (not a collection error): it is
exactly AC4 ("no ``agent_label: str`` parameter survives on any mapper entry
point") failing to hold, which is the behavior this lane exists to build. The
503-exhaustion class exercises ONLY the outbound path (already provenance-typed
today, per tbrk.4's verified starting state) and is expected to already pass —
it pins the attempts/last_status preservation (AC6) that must survive the
refactor unchanged (J3), not new behavior.

Spec grounding (AdCP 3.1.1, ``dist/schemas/3.1.1/enums/error-code.json``
``enumMetadata`` — the authority, not ``STANDARD_ERROR_CODES``): ``RATE_LIMITED``
is ``transient`` (retry after ``retry_after``); ``CONFIGURATION_ERROR`` is
``terminal`` ("surface to a human at the seller ... MUST NOT auto-retry");
``SERVICE_UNAVAILABLE`` is ``transient``. Graded (error-code emission).
Conformance storyboard: ungraded — this is an internal mapper-parity
obligation, not a wire contract any ``dist/compliance/3.1.1/`` scenario
exercises (same finding as the sibling files this test borrows its shape
from, ``test_operator_agent_mcp_seam_egress.py`` / ``test_url_provenance_wire.py``).

beads: GH #1802 / GH #1802
"""

from __future__ import annotations

import logging

import httpx
import pytest

from src.core.exceptions import (
    RECOVERY_BY_WIRE_CODE,
    AdCPConfigurationError,
    AdCPRateLimitError,
    AdCPServiceUnavailableError,
    build_two_layer_error_envelope,
)
from src.core.helpers.outbound_error_mapping import raise_mapped_mcp_error, raise_mapped_outbound_error
from src.core.security.outbound_http import OperatorEndpoint, OutboundDeliveryFailed, send
from src.core.utils.mcp_client import MCPConnectionError
from tests.helpers import assert_envelope_shape
from tests.integration.test_outbound_http import fast_backoff, rate_limited, set_flags

pytestmark = [pytest.mark.integration]

_LOGGER = logging.getLogger(__name__)

# A registered agent, not something the buyer supplied — the ONLY provenance
# either mapper's operator arm is reachable with (design J1: CounterpartyUrl
# structurally never reaches either mapper's status-classification branch
# today, so this table does not invent that taxonomy either).
_PROVENANCE = OperatorEndpoint("the parity-test operator agent")


def _outbound_delivery_failure(local_origin_tls, monkeypatch, *, max_attempts: int) -> OutboundDeliveryFailed:
    """Drive a real response through the real outbound seam and return what it raised.

    Real socket, real seam, real ``Attempts`` state machine — the same seam
    ``raise_mapped_outbound_error`` is documented to receive from, per the
    project's "use the real database/service/transport" test policy. Only the
    MCP-seam side (below) is a constructed exception, because reaching
    ``raise_mapped_mcp_error`` over a live MCP handshake would grade fastmcp's
    protocol library, not the classifier — the same call the sibling suite
    (``test_operator_agent_mcp_seam_egress.py``) already made for this exact
    seam.
    """
    set_flags(monkeypatch, private=True)
    fast_backoff(monkeypatch)
    try:
        send(f"{local_origin_tls.base_url}/dial", provenance=_PROVENANCE, max_attempts=max_attempts)
    except OutboundDeliveryFailed as exc:
        return exc
    raise AssertionError("expected the outbound seam to raise OutboundDeliveryFailed — the origin answered success")


def _mcp_status_error(status: int, *, retry_after: str | None = None) -> MCPConnectionError:
    """Build the same wrapped-``httpx.HTTPStatusError`` shape the guarded MCP seam surfaces on failure.

    Mirrors ``test_operator_agent_mcp_seam_egress.py``'s
    ``test_a_rejected_handshake_is_configuration_error``: the guarded seam's
    handshake never speaks real MCP over a bad status, so the mapper is
    exercised against its own documented contract (an ``httpx.HTTPStatusError``
    chained via ``__cause__`` under ``MCPConnectionError``) rather than a live
    socket.
    """
    headers = {"Retry-After": retry_after} if retry_after is not None else None
    response = httpx.Response(
        status,
        headers=headers,
        request=httpx.Request("POST", "https://operator.test/mcp"),
    )
    wrapped = httpx.HTTPStatusError(f"{status} error", request=response.request, response=response)
    seam_error = MCPConnectionError("Failed to connect to MCP agent after 3 attempts")
    seam_error.__cause__ = wrapped
    return seam_error


class TestA429WithRetryAfterIsRateLimitedIdenticallyAcrossSeams:
    """AC5: a 429 + ``Retry-After: 7`` through BOTH seams -> ``RATE_LIMITED``, same clamped ``retry_after``.

    ``raise_mapped_outbound_error`` already accepts ``provenance:
    UrlProvenance`` (Epic B landed). ``raise_mapped_mcp_error`` does not yet —
    calling it with ``provenance=`` against today's ``agent_label: str``
    signature is the RED: a ``TypeError`` from inside this test, not a
    collection failure, because the import above resolves to the function that
    exists today (pre-tbrk.4) under its pre-tbrk.4 signature.
    """

    def test_429_retry_after_matches_across_seams(self, local_origin_tls, monkeypatch):
        rate_limited(local_origin_tls, retry_after="7")
        outbound_exc = _outbound_delivery_failure(local_origin_tls, monkeypatch, max_attempts=1)

        with pytest.raises(AdCPRateLimitError) as outbound_info:
            raise_mapped_outbound_error(outbound_exc, provenance=_PROVENANCE, logger=_LOGGER)
        outbound_err = outbound_info.value

        mcp_exc = _mcp_status_error(429, retry_after="7")
        with pytest.raises(AdCPRateLimitError) as mcp_info:
            raise_mapped_mcp_error(mcp_exc, provenance=_PROVENANCE, logger=_LOGGER)
        mcp_err = mcp_info.value

        assert outbound_err.retry_after == 7, f"outbound retry_after={outbound_err.retry_after!r}, expected 7"
        assert mcp_err.retry_after == 7, f"mcp retry_after={mcp_err.retry_after!r}, expected 7"
        assert outbound_err.retry_after == mcp_err.retry_after, (
            "the two seams disagree on the clamped retry_after for the SAME origin answer: "
            f"outbound={outbound_err.retry_after!r} mcp={mcp_err.retry_after!r}"
        )

        recovery = RECOVERY_BY_WIRE_CODE["RATE_LIMITED"]
        assert_envelope_shape(build_two_layer_error_envelope(outbound_err), "RATE_LIMITED", recovery=recovery)
        assert_envelope_shape(build_two_layer_error_envelope(mcp_err), "RATE_LIMITED", recovery=recovery)


class TestATerminal4xxIsConfigurationErrorIdenticallyAcrossSeams:
    """AC5: a terminal 4xx (418) through BOTH seams -> the same class + code.

    Same RED mechanism as the 429 class above: ``raise_mapped_mcp_error``
    called with ``provenance=`` TypeErrors against today's signature.
    """

    def test_418_is_configuration_error_on_both_seams(self, local_origin_tls, monkeypatch):
        local_origin_tls.respond_with(418, body=b'{"error": "teapot"}')
        outbound_exc = _outbound_delivery_failure(local_origin_tls, monkeypatch, max_attempts=1)

        with pytest.raises(AdCPConfigurationError) as outbound_info:
            raise_mapped_outbound_error(outbound_exc, provenance=_PROVENANCE, logger=_LOGGER)
        outbound_err = outbound_info.value

        mcp_exc = _mcp_status_error(418)
        with pytest.raises(AdCPConfigurationError) as mcp_info:
            raise_mapped_mcp_error(mcp_exc, provenance=_PROVENANCE, logger=_LOGGER)
        mcp_err = mcp_info.value

        assert type(outbound_err) is AdCPConfigurationError, f"outbound raised {type(outbound_err).__name__}"
        assert type(mcp_err) is AdCPConfigurationError, f"mcp raised {type(mcp_err).__name__}"

        recovery = RECOVERY_BY_WIRE_CODE["CONFIGURATION_ERROR"]
        assert_envelope_shape(build_two_layer_error_envelope(outbound_err), "CONFIGURATION_ERROR", recovery=recovery)
        assert_envelope_shape(build_two_layer_error_envelope(mcp_err), "CONFIGURATION_ERROR", recovery=recovery)


class TestOutboundExhaustionPreservesAttempts:
    """AC6: a 503 retried to exhaustion preserves ``attempts``/``last_status`` on the outbound path.

    Outbound-only (the MCP seam's failure surface carries no ``attempts`` to
    preserve — J2/J3 in tbrk.4's design). Exercises ONLY the already-correct
    ``raise_mapped_outbound_error`` re-raise arm, so this is expected to PASS
    today: it pins behavior tbrk.4's refactor (J3: routing this arm THROUGH
    ``adcp_error_for_status`` instead of short-circuiting before it) must not
    change, not new behavior this lane introduces.
    """

    def test_503_exhausted_preserves_attempts_and_last_status(self, local_origin_tls, monkeypatch):
        local_origin_tls.respond_with(503, body=b'{"error": "unavailable"}')
        outbound_exc = _outbound_delivery_failure(local_origin_tls, monkeypatch, max_attempts=2)

        with pytest.raises(AdCPServiceUnavailableError) as outbound_info:
            raise_mapped_outbound_error(outbound_exc, provenance=_PROVENANCE, logger=_LOGGER)
        err = outbound_info.value

        envelope = build_two_layer_error_envelope(err)
        assert envelope["errors"][0]["details"] == {"attempts": 2, "last_status": 503}, (
            f"attempts/last_status did not survive the re-raise onto the wire: {envelope['errors'][0]['details']!r}"
        )

        recovery = RECOVERY_BY_WIRE_CODE["SERVICE_UNAVAILABLE"]
        assert_envelope_shape(envelope, "SERVICE_UNAVAILABLE", recovery=recovery)
