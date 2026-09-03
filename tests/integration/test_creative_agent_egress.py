"""The creative-agent raw-MCP fetch, against a real origin and the real egress seam.

``CreativeAgentRegistry._fetch_formats_raw_mcp`` is the COUNTERPARTY-agent_url
fetch path (a buyer-supplied ``format_id.agent_url``) — the operator-agent path
moved onto the guarded fastmcp seam (``call_mcp_tool``, via
``call_operator_mcp_tool`` from ``_fetch_formats_operator``) in GH #1802,
so this raw-JSON-RPC-over-asend method now has exactly the one caller. It issues its own outbound HTTP: once it
fetches through ``src/core/security/outbound_http.py``, scheme policy, address
validation, IP pinning, redirect refusal, the response-size cap, the retry
count and the backoff schedule all belong to the seam and are graded once, in
``tests/integration/test_outbound_http.py``. Re-asserting them here would be the
duplication the seam exists to delete, and patching ``httpx.AsyncClient`` — what
the unit tests this file replaces did — deletes all six at once.

What does NOT move to the seam, and is therefore graded here:

* **The error taxonomy.** The seam reports one class of failure
  (``OutboundDeliveryFailed`` / SERVICE_UNAVAILABLE / transient) with a typed
  ``http_status``. Only this application knows that a 429 from a creative agent
  is RATE_LIMITED, that a 4xx is terminal rather than transient, and that a
  REFUSED agent URL is a seller-side misconfiguration the buyer cannot fix.
* **The attempt count the buyer pays for.** A 5xx creative agent used to cost
  ONE origin hit — ``raise_for_status`` fired on the first attempt and the
  ``except httpx.HTTPStatusError`` arm raised immediately. Through the seam a
  5xx is retryable, so it costs three. That is a real, buyer-visible behaviour
  change on the ``create_media_buy`` path and it is graded by counting hits on a
  server that actually ran.

``ADCP_TESTING=true`` is autouse for the whole suite (``tests/conftest.py``), and
the public entry point (``get_formats_for_agent``) skips this path entirely for
an operator agent — only a COUNTERPARTY ``agent_url`` reaches it, ahead of the
testing short-circuit (see ``_is_operator_agent`` in creative_agent_registry.py).
The fetch is therefore driven directly — as the unit tests it replaces did —
because a test that went through the public entry points would assert on
formats that were never fetched.

Spec grounding — pinned AdCP 3.1.1, read with ``git -C <adcp> show v3.1.1:<path>``:

* ``dist/schemas/3.1.1/core/error.json`` — ``retry_after`` is a TOP-LEVEL member
  of the error object, ``"type": "number"``, ``minimum: 1``, ``maximum: 3600``,
  "Sellers MUST return values between 1 and 3600. Clients MUST clamp values
  outside this range."
* ``dist/schemas/3.1.1/error-details/rate-limited.json`` — enumerates
  limit / remaining / window_seconds / scope and models NO ``retry_after``. The
  ``details["retry_after"]`` this path emits today is not the spec's home for
  the value. (Named, not hidden: ``dist/compliance/3.1.1/test-kits/
  rate-limit-trip-runner.yaml`` reads ``error.details.retry_after`` twice, so the
  pinned spec is internally inconsistent here. ``core/error.json`` is
  authoritative for the EMITTED shape.)
* ``dist/schemas/3.1.1/enums/error-code.json`` enumMetadata —
  ``CONFIGURATION_ERROR`` = {recovery: terminal, suggestion: "surface to a human
  at the seller — the buyer cannot resolve a seller-side deployment
  misconfiguration and MUST NOT auto-retry"}. That is the grounding for the
  refused-URL arm: the creative agent's URL is the OPERATOR's registered
  endpoint, so VALIDATION_ERROR / correctable would tell a buyer to fix a field
  they never sent. ``RATE_LIMITED`` = transient, "retry after the retry_after
  interval"; ``SERVICE_UNAVAILABLE`` = transient.

Conformance storyboard: **ungraded**. No scenario in ``dist/compliance/3.1.1/``
exercises a creative-agent egress failure; ``universal/error-compliance.yaml``
step ``validate_error_shape`` treats ``retry_after`` as an OPTIONAL field on any
error, which is documentary rather than a grade of this path.
"""

from __future__ import annotations

import json

import pytest

from src.core.creative_agent_registry import CreativeAgent, CreativeAgentRegistry
from src.core.exceptions import (
    AdCPConfigurationError,
    AdCPRateLimitError,
    AdCPServiceUnavailableError,
    build_two_layer_error_envelope,
)
from src.core.security.outbound_http import OperatorEndpoint
from tests.helpers import assert_envelope_shape
from tests.helpers.local_http_origin import LocalOrigin

# Reused rather than restated: which escape hatches a case opens is one decision
# with one home (``tests/integration/property_list_helpers``, whose docstring
# claims exactly that), and the backoff knob that keeps a retry case fast is the
# seam suite's own helper.
from tests.integration.property_list_helpers import allow_local_origin, enforce_egress_policy
from tests.integration.test_outbound_http import fast_backoff

pytestmark = [pytest.mark.integration]

# One format, in the TextContent shape this fallback exists to parse: the adcp
# SDK rejects it, the registry re-fetches raw and reads it out of
# ``result.content[].text``.
_SAMPLE_FORMATS = {
    "formats": [
        {
            "format_id": {"agent_url": "https://creative.example.com", "id": "display_image"},
            "name": "Display Image",
            "type": "display",
        }
    ]
}


def _jsonrpc_body() -> bytes:
    """A JSON-RPC ``tools/call`` result carrying the sample formats as TextContent."""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps(_SAMPLE_FORMATS)}]},
        }
    ).encode()


def _sse_body() -> bytes:
    """The same result, framed as a single ``text/event-stream`` data event."""
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps(_SAMPLE_FORMATS)}]},
        }
    )
    return f"data: {payload}\n\n".encode()


@pytest.fixture
def registry() -> CreativeAgentRegistry:
    return CreativeAgentRegistry()


def agent_at(origin: LocalOrigin) -> CreativeAgent:
    """A creative agent registered at the running local origin.

    ``agent_url`` is OPERATOR configuration — a row in the tenant's creative-agent
    table, or the system default. The buyer can select among registered agents on
    ``create_media_buy`` but cannot aim the socket, which is why nothing here
    passes a ``field`` to the seam: there is no request-payload path to name.
    """
    return CreativeAgent(agent_url=origin.base_url, name="test-agent", timeout=5)


# ``_fetch_formats_raw_mcp`` now requires ``provenance`` — no default means "no
# opinion" cannot compile. ``agent_at``'s agent is OPERATOR configuration (see its
# docstring), so every direct call below constructs this rather than relying on
# a since-deleted None-means-operator default.
_OPERATOR_PROVENANCE = OperatorEndpoint("creative agent test-agent")


class TestAttemptsThroughTheSeam:
    """How many times the origin is actually reached — the real behaviour change."""

    async def test_a_5xx_creative_agent_now_costs_three_origin_hits(self, registry, local_origin_tls, monkeypatch):
        """A 5xx is retried to the seam's default max_attempts instead of failing on the first.

        Today this path calls ``raise_for_status`` and raises out of
        ``except httpx.HTTPStatusError`` on attempt one, so a 5xx costs exactly
        one hit. The seam classifies 5xx as retryable, so it costs three — and
        three in-request waits on the ``create_media_buy`` path. Counting hits on
        a real server is the only way to state that: an attempt count read off a
        mock is a count of calls the test itself arranged.
        """
        allow_local_origin(monkeypatch)
        fast_backoff(monkeypatch)
        local_origin_tls.respond_with(503, body=b'{"error": "unavailable"}')

        with pytest.raises(AdCPServiceUnavailableError):
            await registry._fetch_formats_raw_mcp(agent_at(local_origin_tls), provenance=_OPERATOR_PROVENANCE)

        assert local_origin_tls.hits == 3, f"expected the seam's three attempts, got {local_origin_tls.hits}"

    async def test_the_fetch_lands_on_the_mcp_endpoint_of_the_registered_agent(
        self, registry, local_origin_tls, monkeypatch
    ):
        """``/mcp`` is appended to the registered agent URL, and the JSON-RPC call is what crosses the socket.

        Read off the bytes the origin received, not off ``call_args`` on a
        substituted client: the latter reads back the object the caller passed,
        which proves nothing about what was actually sent.
        """
        allow_local_origin(monkeypatch)
        local_origin_tls.respond_with(200, body=_jsonrpc_body())

        await registry._fetch_formats_raw_mcp(agent_at(local_origin_tls), provenance=_OPERATOR_PROVENANCE)

        assert local_origin_tls.paths == ["/mcp"]
        assert local_origin_tls.last_request.json() == {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "list_creative_formats", "arguments": {}},
            "id": 1,
        }


class TestParsing:
    """The two response framings the fallback exists to read.

    Migrating to the seam is a mechanical swap for this half —
    ``result.headers`` / ``result.text`` / ``result.json()`` —
    so these cases pass before and after. They are here because the unit tests
    that covered them patched ``httpx.AsyncClient`` and go away with the
    migration: the contract must not go away with them.
    """

    async def test_a_json_response_parses_formats(self, registry, local_origin_tls, monkeypatch):
        """``application/json`` with a JSON-RPC result yields the formats it carries."""
        allow_local_origin(monkeypatch)
        local_origin_tls.respond_with(200, body=_jsonrpc_body())

        formats = await registry._fetch_formats_raw_mcp(agent_at(local_origin_tls), provenance=_OPERATOR_PROVENANCE)

        assert [fmt.format_id.id for fmt in formats] == ["display_image"]

    async def test_an_sse_response_parses_formats(self, registry, local_origin_tls, monkeypatch):
        """``text/event-stream`` is read off the same body, one ``data:`` line at a time."""
        allow_local_origin(monkeypatch)
        local_origin_tls.respond_with(200, body=_sse_body(), content_type="text/event-stream")

        formats = await registry._fetch_formats_raw_mcp(agent_at(local_origin_tls), provenance=_OPERATOR_PROVENANCE)

        assert [fmt.format_id.id for fmt in formats] == ["display_image"]


class TestTaxonomy:
    """How the seam's one failure class is named in this application's error taxonomy.

    Every case asserts on the two-layer wire envelope, not on the exception's
    attributes: the envelope is the buyer-facing contract, and the recovery hint
    on it is the whole point of re-mapping a status the seam has already
    classified as transient.
    """

    async def test_a_404_is_terminal_not_transient(self, registry, local_origin_tls, monkeypatch):
        """A 4xx that is not 429 tells the buyer to stop, not to retry forever.

        The seam raises ``OutboundDeliveryFailed``, which is a SERVICE_UNAVAILABLE
        / **transient** error — correct for a 5xx, wrong for a 404 the origin will
        keep returning. The mapper therefore re-raises it as
        ``AdCPConfigurationError``: this endpoint is OPERATOR configuration, so a
        404 from it means the deployment points at something that is not the tool
        it claims to be, and the pinned enumMetadata classifies CONFIGURATION_ERROR
        terminal — "surface to a human at the seller ... MUST NOT auto-retry".

        Expressing terminal by CHOOSING that code, rather than by hand-typing
        ``recovery="terminal"`` onto SERVICE_UNAVAILABLE, is the point: the pair
        this asserts now agrees with the pin instead of contradicting it, so a
        buyer classifying by code and a buyer classifying by recovery read the
        same thing. The hit count is unchanged (a 4xx was never retried) and is
        asserted only so a regression there cannot hide behind a green
        classification.
        """
        allow_local_origin(monkeypatch)
        local_origin_tls.respond_with(404, body=b'{"error": "no such tool"}')

        with pytest.raises(AdCPConfigurationError) as excinfo:
            await registry._fetch_formats_raw_mcp(agent_at(local_origin_tls), provenance=_OPERATOR_PROVENANCE)

        assert_envelope_shape(
            build_two_layer_error_envelope(excinfo.value),
            "CONFIGURATION_ERROR",
            recovery="terminal",
        )
        assert local_origin_tls.hits == 1

    async def test_a_429_carries_retry_after_on_the_envelope_top_level(self, registry, local_origin_tls, monkeypatch):
        """A rate-limited creative agent is RATE_LIMITED, with the wait in the spec's own slot.

        ``core/error.json`` @3.1.1 puts ``retry_after`` at the top level of the
        error object; ``error-details/rate-limited.json`` models no such member,
        so today's ``details={"retry_after": ...}`` is a slot this call site
        invented — and once the fetch goes through the seam, which discards every
        non-final response, the call site cannot read the header at all. The value
        has to arrive on the typed error the seam raises.

        The origin answers ``Retry-After: 0``, which is why this case costs no
        wall time: the seam waits the raw header (nothing) and carries the
        spec-clamped value (1). Fusing those two uses would make every case in
        this class sleep.
        """
        allow_local_origin(monkeypatch)
        fast_backoff(monkeypatch)
        local_origin_tls.respond_with(429, body=b'{"error": "slow down"}', headers={"Retry-After": "0"})

        with pytest.raises(AdCPRateLimitError) as excinfo:
            await registry._fetch_formats_raw_mcp(agent_at(local_origin_tls), provenance=_OPERATOR_PROVENANCE)

        envelope = build_two_layer_error_envelope(excinfo.value)
        assert_envelope_shape(envelope, "RATE_LIMITED", recovery="transient")
        assert envelope["adcp_error"].get("retry_after") == 1, (
            f"adcp_error carries no top-level retry_after: {envelope}"
        )
        assert envelope["errors"][0].get("retry_after") == 1, f"errors[0] carries no top-level retry_after: {envelope}"
        assert "retry_after" not in (envelope["errors"][0].get("details") or {}), (
            f"retry_after is still riding in details, which models no such member: {envelope}"
        )

    async def test_a_refused_agent_url_is_a_seller_side_configuration_error(self, registry, local_origin, monkeypatch):
        """An agent URL egress policy refuses is CONFIGURATION_ERROR / terminal, and nothing is fetched.

        This is a NEW failure mode at this call site: before the migration the
        registry connected to whatever was configured. The seam refuses a
        non-HTTPS or reserved-range destination before opening a socket, and the
        default VALIDATION_ERROR / correctable would hand the buyer of
        ``create_media_buy`` an error only a seller-side admin can act on. The
        pinned enumMetadata says so outright — "the buyer cannot resolve a
        seller-side deployment misconfiguration and MUST NOT auto-retry".

        ``hits == 0`` is the proof that this is a refusal rather than a failed
        fetch, and no ``field`` may appear: the buyer's request document contains
        no path to the operator's registered endpoint.
        """
        enforce_egress_policy(monkeypatch)

        with pytest.raises(AdCPConfigurationError) as excinfo:
            await registry._fetch_formats_raw_mcp(agent_at(local_origin), provenance=_OPERATOR_PROVENANCE)

        envelope = build_two_layer_error_envelope(excinfo.value)
        assert_envelope_shape(envelope, "CONFIGURATION_ERROR", recovery="terminal")
        assert local_origin.hits == 0, f"a refused agent URL was still fetched: {local_origin.requests}"
        assert "field" not in envelope["errors"][0], (
            f"errors[0] names a request-payload path for an operator-configured URL: {envelope}"
        )

    async def test_a_delivery_failure_does_not_echo_the_operators_endpoint(
        self, registry, local_origin_tls, monkeypatch
    ):
        """The failure envelope names attempts and status — never the registered agent URL.

        Today's message is ``f"Creative agent unavailable (HTTP 503): {mcp_url}"``,
        which hands a buyer who never supplied the URL the operator's internal
        endpoint, host and port. The seam's message interpolates nothing (spec
        point 6), so the migration closes that leak; this pins it closed.
        """
        allow_local_origin(monkeypatch)
        fast_backoff(monkeypatch)
        local_origin_tls.respond_with(503, body=b'{"detail": "LEAKED-ORIGIN-BODY-MARKER"}')

        with pytest.raises(AdCPServiceUnavailableError) as excinfo:
            await registry._fetch_formats_raw_mcp(agent_at(local_origin_tls), provenance=_OPERATOR_PROVENANCE)

        envelope = build_two_layer_error_envelope(excinfo.value)
        assert_envelope_shape(envelope, "SERVICE_UNAVAILABLE", recovery="transient")

        serialized = json.dumps(envelope)
        assert local_origin_tls.host not in serialized, f"the operator's endpoint host leaked into {envelope}"
        assert str(local_origin_tls.port) not in serialized, f"the operator's endpoint port leaked into {envelope}"
        assert "LEAKED-ORIGIN-BODY-MARKER" not in serialized, f"the origin's body leaked into {envelope}"
