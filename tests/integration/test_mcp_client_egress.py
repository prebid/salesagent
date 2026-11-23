"""The fastmcp MCP-client fetch, against a real origin and the real egress seam.

``src/core/utils/mcp_client.py`` builds an fastmcp ``StreamableHttpTransport``
to dial a buyer-supplied creative/signals ``agent_url`` (reachable by any
authenticated advertiser through ``sync_creatives``). That transport is the
highest-privilege outbound path in this application, and it must obey the same
egress policy every other outbound call does: scheme + address validation, IP
pinning, and — the point of this file — refusing to follow redirects.

fastmcp does not dial httpx directly; when ``StreamableHttpTransport`` is built
without an ``httpx_client_factory`` it falls back to
``mcp.shared._httpx_utils.create_mcp_http_client``, whose client is built with
``follow_redirects=True`` and no pinned transport. So the ingest-time
``validate_url`` pre-check guards only the FIRST address — a counterparty that
answers ``302 -> http://169.254.169.254/`` has its redirect followed to the
metadata endpoint, unvalidated and unpinned. That is the live hole this file
grades closed, by injecting the seam's guarded client factory at the transport.

Spec grounding — pinned AdCP 3.1.1, ``building/by-layer/L1/security.mdx``,
"Webhook URL validation (SSRF)": a fetcher MUST (3) pin the connection to the
validated IP and (4) refuse to follow redirects. Points 3 and 4 are exactly
what the unpinned, redirect-following fallback client loses.

Why the redirect target is a SECOND local origin and not the literal metadata
address: a pinned transport also refuses a wrong-host connect for its OWN
reasons, so an ``assert the call failed`` test stays green even when the redirect
IS followed — it observes the outcome, which both postures share, not the
attempt, which is the thing that differs. The only honest grade is the hit count
on the redirect target: 0 means the redirect was refused, >0 means it was
followed. Observe the attempt.

The redirect target closes the connection the instant it is reached
(:meth:`LocalOrigin.close_without_responding`) so the "redirect followed" path
fails in milliseconds instead of stalling on fastmcp's SSE read — the hit is
recorded before the close, so the grade survives the speed-up.
"""

from __future__ import annotations

import pytest

from src.core.security.outbound_http import OutboundRequestBlocked
from src.core.utils.mcp_client import call_mcp_tool
from tests.helpers.egress_backoff import fast_backoff
from tests.helpers.local_http_origin import run_local_origin
from tests.integration.property_list_helpers import allow_local_origin, enforce_egress_policy

pytestmark = [pytest.mark.integration]


class TestRedirectIsNotFollowed:
    """The redirect a counterparty uses to reach an internal address is refused."""

    @pytest.mark.timeout(30)
    async def test_a_302_to_a_metadata_address_is_not_followed(self, local_origin_tls, monkeypatch):
        """The buyer's ``agent_url`` answers ``302 -> <metadata stand-in>``; the target is never reached.

        ``local_origin_tls`` is the buyer-supplied ``agent_url``. It answers every
        request with a redirect to a SECOND local origin standing in for
        ``http://169.254.169.254/`` — an address we can hit-count, which the real
        metadata endpoint is not. Both hatches are open so the loopback origin is
        dialable at all; that makes the redirect target loopback too, so if the
        redirect were followed the stand-in WOULD be reached. Its hit count is
        therefore the whole grade: 0 is a refused redirect, anything else is a
        followed one.

        The connection ultimately fails either way (a 302 is not an MCP
        handshake), so the exception is not asserted on — only the attempt is.
        """
        allow_local_origin(monkeypatch)
        fast_backoff(monkeypatch)

        with run_local_origin() as metadata_standin:
            # If reached, record the hit then drop the socket — a followed
            # redirect fails fast instead of stalling fastmcp's SSE read.
            metadata_standin.close_without_responding()
            local_origin_tls.redirect_to(metadata_standin.base_url, status=302)

            with pytest.raises(Exception):  # noqa: B017 - the outcome is shared by both postures; the attempt is the grade
                await call_mcp_tool(agent_url=local_origin_tls.base_url, tool="noop", arguments={})

            assert metadata_standin.hits == 0, (
                f"the redirect to the metadata stand-in was followed: {metadata_standin.requests}"
            )
            assert local_origin_tls.hits >= 1, "the buyer's agent_url was never dialed — the test graded nothing"


class TestMetadataAddressIsRefusedWithoutDialing:
    """A buyer ``agent_url`` that IS a reserved address is refused before any socket."""

    @pytest.mark.timeout(30)
    async def test_a_reserved_address_agent_url_is_refused_with_no_dial(self, monkeypatch):
        """Under the production posture the seam refuses ``169.254.169.254`` before connecting.

        This is the first-hop case the pre-check already covers, pinned here so
        the guarded transport cannot regress it: a reserved-range destination is
        an ``OutboundRequestBlocked`` refusal — not a fetch, not a generic
        connection failure — and there is no origin to hit.
        """
        enforce_egress_policy(monkeypatch)

        with pytest.raises(OutboundRequestBlocked):
            await call_mcp_tool(agent_url="http://169.254.169.254/mcp", tool="noop", arguments={})
