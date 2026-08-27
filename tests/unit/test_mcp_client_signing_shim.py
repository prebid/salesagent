"""TDD RED for the MCP outbound-signing shim (#1291 C3, step 1, salesagent-z6nr.29).

Core Invariant under test (bd show salesagent-z6nr.29): every outbound AdCP
protocol request we make to a counterparty agent (creative agent, signals
agent) is RFC 9421-signed when the tenant holds an active signing key --
strictly additive, never breaking a call that works unsigned today. This
module pins the mechanism that makes signing possible AT ALL on the MCP
transport, which the epic's architect review (salesagent-js3z.20) found is a
verified no-op on adcp==6.6.0's own scoping.

Empirically verified by THIS atom (salesagent-js3z.22), before writing this
test, against the installed ``adcp==6.6.0`` + ``mcp==1.28.1`` source (not just
docs) and a REAL local ``fastmcp`` counterparty
(``tests.helpers.mcp_signing_server.LocalSigningMCPServer``):

* ``adcp/protocols/mcp.py:545`` calls ``self._get_session()`` -- which lazily
  connects and, via ``mcp/client/streamable_http.py:659-667``, spawns the
  persistent ``post_writer`` background task via ``tg.start_soon`` -- BEFORE
  ``mcp.py:560`` sets ``current_operation`` in the calling task. ``asyncio``/
  ``anyio`` copy the SPAWNING task's context at ``start_soon`` time, so
  ``post_writer``'s copy is frozen with ``current_operation`` still unset.
  Every later message ``post_writer`` dispatches (``tg.start_soon(
  handle_request_async)``, :569) is itself spawned FROM POST_WRITER'S OWN
  task, inheriting ITS frozen context -- never the live value in the
  original calling task. Reproduced end-to-end: baseline (no wrap, today's
  production call shape) produces ZERO ``Signature`` header on the wire,
  deterministically, every trial.
* The owner-directed fix ("Owner Direction on HIGH Finding", bd show
  salesagent-z6nr.29): wrap the ENTIRE outbound call -- including the lazy
  connect -- in ``signing_operation(operation)`` from OUTSIDE the SDK's own
  internal (too-late) ``.set()``. Because ``post_writer`` is spawned via
  ``tg.start_soon`` WHILE our wrap is active, its captured context already
  carries the operation name, and every message it later dispatches over
  that session inherits it correctly. Reproduced end-to-end: the SAME call,
  wrapped, produces a valid ``Signature``/``Signature-Input``/
  ``Content-Digest`` on the wire, with ``keyid`` matching the
  ``SigningConfig`` used -- deterministically, every trial.

**A second hazard this atom found and recorded on salesagent-z6nr.29** (not
yet resolved -- left for the implement atom, salesagent-js3z.23): because
``post_writer``'s frozen context lasts for the WHOLE session, the SDK's own
recursive ``fetch_capabilities()`` call inside ``_sign_outgoing_request``
(``client.py:986``) -- which reuses the SAME session -- also gets
mis-tagged with the OUTER wrap's operation name instead of
``"get_adcp_capabilities"``, defeating the SDK's bootstrap carve-out and
HANGING indefinitely when capabilities are not already cached. This test
isolates the ContextVar-propagation fix (step 1) from that still-open
capabilities-caching problem (steps 3/4) by giving the client instance a
pre-resolved capability directly, matching how the design says the
implement atom must ultimately avoid the recursive fetch for a session
opened under the wrap.

Nothing in this module passes until ``sign_scoped_mcp_call`` exists --
matching this codebase's established TDD-red convention of importing the
not-yet-built production symbol INSIDE the test body (see
``tests/integration/test_webhook_signing_boundary.py``).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from adcp import ADCPClient, AgentConfig, ListCreativeFormatsRequest
from adcp.signing.autosign import SigningConfig
from adcp.signing.canonical import parse_signature_input_header
from adcp.types.core import Protocol
from cryptography.hazmat.primitives.asymmetric import ed25519

from tests.helpers.mcp_signing_server import LocalSigningMCPServer

pytestmark = [pytest.mark.unit, pytest.mark.slow]

_OPERATION = "list_creative_formats"
_KEY_ID = "outbound-shim-test-key-1"


class _RequestSigning:
    """Stands in for the SDK's generated ``RequestSigning`` capability block."""

    def __init__(self, operation: str) -> None:
        self.supported = True
        self.required_for = [operation]
        self.warn_for: list[str] = []
        self.supported_for: list[str] = []
        self.covers_content_digest = None


class _FakeCapabilities:
    """Pre-resolved capabilities, set directly on the client instance.

    Isolates the ContextVar-propagation fix under test from the SEPARATE,
    not-yet-resolved capabilities-recursion hazard this atom found (see
    module docstring) -- that hazard belongs to design steps 3/4, not to
    this shim.
    """

    def __init__(self, operation: str) -> None:
        self.request_signing = _RequestSigning(operation)


def _signing_config() -> SigningConfig:
    key = ed25519.Ed25519PrivateKey.generate()
    return SigningConfig(private_key=key, key_id=_KEY_ID)


def _build_client(server_url: str) -> ADCPClient:
    """A fresh ADCPClient per call -- matches production (both registries
    build a fresh ADCPMultiAgentClient per outbound call)."""
    agent_config = AgentConfig(
        id="outbound-shim-test-agent",
        agent_uri=server_url,
        protocol=Protocol.MCP,
        mcp_transport="streamable_http",
    )
    client = ADCPClient(agent_config=agent_config, signing=_signing_config())
    # Pre-resolve capabilities directly on the instance so `_sign_outgoing_request`'s
    # `await self.fetch_capabilities()` returns synchronously from cache instead of
    # recursing into a NEW request over the same (frozen-context) session -- see
    # module docstring's "second hazard".
    client._capabilities = _FakeCapabilities(_OPERATION)  # type: ignore[assignment]
    client._capabilities_fetched_at = time.monotonic()
    return client


async def _call_list_creative_formats(client: ADCPClient) -> Any:
    return await client.list_creative_formats(ListCreativeFormatsRequest())


class TestSignScopedMcpCall:
    """``sign_scoped_mcp_call`` is what makes the MCP transport actually sign."""

    def test_wrapped_call_carries_a_valid_signature_on_the_wire(self) -> None:
        """The ``tools/call`` POST for ``list_creative_formats`` is RFC 9421-signed
        when wrapped through ``sign_scoped_mcp_call`` -- against a REAL local
        counterparty, not a mocked transport."""
        from src.core.signing._mcp_client_signing_shim import sign_scoped_mcp_call

        with LocalSigningMCPServer() as server:
            client = _build_client(server.url)
            try:
                asyncio.run(sign_scoped_mcp_call(_OPERATION, lambda: _call_list_creative_formats(client)))
            finally:
                asyncio.run(client.close())

            calls = server.calls("tools/call")
            assert calls, "the tool call never reached the counterparty -- nothing to assert on"
            headers = calls[-1].headers

            assert "signature" in headers, (
                "sign_scoped_mcp_call did not produce a Signature header on the wire "
                f"for a counterparty that requires signing this operation; headers were {sorted(headers)}"
            )
            assert "content-digest" in headers, (
                "no Content-Digest header -- the signature would cover nothing of the request body"
            )

            parsed = parse_signature_input_header(headers["signature-input"])
            assert "sig1" in parsed, f"Signature-Input carries labels {sorted(parsed)}, not 'sig1'"
            params = parsed["sig1"].params
            assert params["keyid"] == _KEY_ID, (
                f"Signature-Input keyid={params.get('keyid')!r} does not match the SigningConfig "
                f"key_id={_KEY_ID!r} used for this call -- the wrong credential (or none) signed it"
            )

    def test_unwrapped_call_through_the_same_transport_is_never_signed(self) -> None:
        """Regression guard for the mechanism itself: bypassing the shim (today's
        production call shape -- a bare ``await client.list_creative_formats(...)``)
        must stay unsigned on this transport. Proves the assertion above is really
        exercising the shim's fix rather than some other, already-working code path.
        """
        with LocalSigningMCPServer() as server:
            client = _build_client(server.url)
            try:
                asyncio.run(_call_list_creative_formats(client))
            finally:
                asyncio.run(client.close())

            calls = server.calls("tools/call")
            assert calls, "the tool call never reached the counterparty -- nothing to assert on"
            assert "signature" not in calls[-1].headers, (
                "an UNWRAPPED call ended up signed on this transport -- either adcp shipped an upstream fix "
                "for adcontextprotocol/adcp-client-python#1017 (re-verify the HIGH finding before trusting "
                "this suite's baseline) or this test's fixture has drifted from the production call shape"
            )
