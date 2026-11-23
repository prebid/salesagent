"""Outbound credential propagation from CreativeAgent/SignalsAgent into the guarded MCP seam.

Before salesagent-4n88, both registries built an ``adcp.AgentConfig`` (via the
deleted ``build_agent_config``/``_build_adcp_client``) and asserted on its
``auth_token``/``auth_header``/``auth_type`` fields. The OPERATOR agent path no
longer builds that config at all — it goes through
``src.core.utils.operator_mcp.call_operator_mcp_tool``, which dials
``call_mcp_tool(auth=..., auth_header=..., sign=...)``; that function's own
``_build_auth_headers`` (already unit-tested in
``tests/integration/test_mcp_client_util.py::TestBuildAuthHeaders``, including
the exact Optable-shaped bearer/custom-header case) does the header
construction. What is left to prove here is narrower and different: that
``_fetch_formats_operator``/``_fetch_signals_operator`` actually FORWARD the
agent's ``auth``/``auth_header``/``timeout`` through unchanged, rather than
dropping or renaming them — the boundary contract, not the header algebra.

Two credentials now ride that one dial, so both are graded here. The bearer or
custom auth header is one; the RFC 9421 request signature is the other. Signing
is NOT a header this file can inspect: ``sign=`` is a CALLBACK the seam installs
as an httpx request hook and invokes per attempt over the exact wire bytes, so
the only thing observable at this boundary — and the only thing these registries
are responsible for — is that the callback
:func:`~src.core.helpers.adapter_helpers.request_signer_for_tenant` resolved for
the RIGHT tenant reaches the seam verbatim, ``None`` included. A tenant-less
dial resolving to ``sign=None`` is asserted explicitly rather than left
unmentioned: silently dropping a signer the tenant does have is exactly the
downgrade that seam exists to make unconstructible.

The patch lands on ``call_mcp_tool`` as ``operator_mcp`` imports it, one frame
BELOW the registries: that keeps the whole forwarding chain (registry ->
``call_operator_mcp_tool`` -> ``call_mcp_tool``) under test, so a parameter
dropped at either hop still fails these assertions.
"""

from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest

from src.core.creative_agent_registry import CreativeAgent, CreativeAgentRegistry
from src.core.signals_agent_registry import SignalsAgent, SignalsAgentRegistry

_SEAM_DIAL = "src.core.utils.operator_mcp.call_mcp_tool"
_CREATIVE_SIGNER = "src.core.creative_agent_registry.request_signer_for_tenant"
_SIGNALS_SIGNER = "src.core.signals_agent_registry.request_signer_for_tenant"

# What each registry's request model serialises to for the dials below. Stated
# as literals rather than rebuilt from the request classes, so a change to the
# dialled arguments fails here instead of being mirrored into the expectation.
_NO_FILTER_FORMAT_ARGS: dict = {}
_SIGNALS_ARGS = {"discovery_mode": "brief", "signal_spec": "test"}


def _mock_call_mcp_tool(payload: dict) -> AsyncMock:
    return AsyncMock(return_value=MagicMock(structured_content=payload, content=[]))


class TestAuthConfigForwardedToGuardedSeam:
    """The registry forwards auth/auth_header/timeout to call_mcp_tool unchanged."""

    @pytest.mark.asyncio
    async def test_creative_agent_custom_auth_header_forwarded(self):
        """A custom Authorization header (like Optable) reaches call_mcp_tool."""
        registry = CreativeAgentRegistry()
        agent = CreativeAgent(
            agent_url="https://sandbox.optable.co/admin/adcp/creative/mcp",
            name="Optable Creative",
            auth={"type": "bearer", "credentials": "test-token-123"},
            auth_header="Authorization",
            timeout=45,
        )

        with patch(
            _SEAM_DIAL,
            _mock_call_mcp_tool({"formats": []}),
        ) as cmc:
            await registry._fetch_formats_operator(agent)

        cmc.assert_called_once_with(
            agent_url="https://sandbox.optable.co/admin/adcp/creative/mcp",
            tool="list_creative_formats",
            arguments=_NO_FILTER_FORMAT_ARGS,
            auth={"type": "bearer", "credentials": "test-token-123"},
            auth_header="Authorization",
            timeout=45,
            sign=None,
        )

    @pytest.mark.asyncio
    async def test_creative_agent_no_custom_header_forwards_none(self):
        """auth_header=None is forwarded as-is — call_mcp_tool applies its own default."""
        registry = CreativeAgentRegistry()
        agent = CreativeAgent(
            agent_url="https://creative.example.com/mcp",
            name="Standard Agent",
            auth={"type": "token", "credentials": "token-456"},
            auth_header=None,
        )

        with patch(
            _SEAM_DIAL,
            _mock_call_mcp_tool({"formats": []}),
        ) as cmc:
            await registry._fetch_formats_operator(agent)

        cmc.assert_called_once_with(
            agent_url="https://creative.example.com/mcp",
            tool="list_creative_formats",
            arguments=_NO_FILTER_FORMAT_ARGS,
            auth={"type": "token", "credentials": "token-456"},
            auth_header=None,
            timeout=30,
            sign=None,
        )

    @pytest.mark.asyncio
    async def test_signals_agent_custom_auth_header_forwarded(self):
        """A custom Authorization header (like Optable) reaches call_mcp_tool for signals too."""
        registry = SignalsAgentRegistry()
        agent = SignalsAgent(
            agent_url="https://sandbox.optable.co/admin/adcp/signals/mcp",
            name="Optable Signals",
            auth={"type": "bearer", "credentials": "test-signals-token"},
            auth_header="Authorization",
            timeout=60,
        )

        with patch(
            _SEAM_DIAL,
            _mock_call_mcp_tool({"signals": []}),
        ) as cmc:
            await registry._fetch_signals_operator(agent, brief="test")

        cmc.assert_called_once_with(
            agent_url="https://sandbox.optable.co/admin/adcp/signals/mcp",
            tool="get_signals",
            arguments=_SIGNALS_ARGS,
            auth={"type": "bearer", "credentials": "test-signals-token"},
            auth_header="Authorization",
            timeout=60,
            sign=None,
        )

    @pytest.mark.asyncio
    async def test_signals_agent_no_custom_header_forwards_none(self):
        """auth_header=None is forwarded as-is on the signals dial too."""
        registry = SignalsAgentRegistry()
        agent = SignalsAgent(
            agent_url="https://signals.example.com/mcp",
            name="Standard Signals Agent",
            auth={"type": "token", "credentials": "signals-token-789"},
            auth_header=None,
        )

        with patch(
            _SEAM_DIAL,
            _mock_call_mcp_tool({"signals": []}),
        ) as cmc:
            await registry._fetch_signals_operator(agent, brief="test")

        cmc.assert_called_once_with(
            agent_url="https://signals.example.com/mcp",
            tool="get_signals",
            arguments=_SIGNALS_ARGS,
            auth={"type": "token", "credentials": "signals-token-789"},
            auth_header=None,
            timeout=30,
            sign=None,
        )

    @pytest.mark.asyncio
    async def test_each_agent_dials_with_its_own_auth_header(self):
        """Three agents with three header conventions do not contaminate each other.

        The predecessor graded this against a single ``ADCPMultiAgentClient``
        built from a LIST of agent configs, where cross-contamination was a live
        risk of the batch construction. There is no batch any more — each agent
        is its own dial — so the same obligation is graded across successive
        dials on one seam: agent N's header must not leak into agent N+1's call.
        """
        registry = CreativeAgentRegistry()
        agents = [
            CreativeAgent(
                agent_url="https://optable.co/creative",
                name="Optable",
                priority=1,
                auth={"type": "bearer", "credentials": "optable-token"},
                auth_header="Authorization",
            ),
            CreativeAgent(
                agent_url="https://standard.co/creative",
                name="Standard",
                priority=2,
                auth={"type": "token", "credentials": "standard-token"},
                auth_header=None,  # call_mcp_tool applies its own default
            ),
            CreativeAgent(
                agent_url="https://custom.co/creative",
                name="Custom",
                priority=3,
                auth={"type": "token", "credentials": "custom-token"},
                auth_header="x-api-key",
            ),
        ]

        with patch(
            _SEAM_DIAL,
            _mock_call_mcp_tool({"formats": []}),
        ) as cmc:
            for agent in agents:
                await registry._fetch_formats_operator(agent)

        assert cmc.call_args_list == [
            call(
                agent_url="https://optable.co/creative",
                tool="list_creative_formats",
                arguments=_NO_FILTER_FORMAT_ARGS,
                auth={"type": "bearer", "credentials": "optable-token"},
                auth_header="Authorization",
                timeout=30,
                sign=None,
            ),
            call(
                agent_url="https://standard.co/creative",
                tool="list_creative_formats",
                arguments=_NO_FILTER_FORMAT_ARGS,
                auth={"type": "token", "credentials": "standard-token"},
                auth_header=None,
                timeout=30,
                sign=None,
            ),
            call(
                agent_url="https://custom.co/creative",
                tool="list_creative_formats",
                arguments=_NO_FILTER_FORMAT_ARGS,
                auth={"type": "token", "credentials": "custom-token"},
                auth_header="x-api-key",
                timeout=30,
                sign=None,
            ),
        ]


class TestRequestSignerForwardedToGuardedSeam:
    """The tenant's RFC 9421 signing callback reaches the same dial as the auth header."""

    @pytest.mark.asyncio
    async def test_creative_agent_forwards_tenant_request_signer(self):
        """The signer resolved for the dial's tenant is handed to the seam verbatim.

        ``sign=`` is a per-attempt callback, not a header, so the registry's whole
        obligation is: resolve it for THIS tenant, and pass that exact object down
        beside the auth config. Whatever the callback later computes happens inside
        the seam's pinned transport, over the bytes httpx is about to write.
        """
        registry = CreativeAgentRegistry()
        agent = CreativeAgent(
            agent_url="https://sandbox.optable.co/admin/adcp/creative/mcp",
            name="Optable Creative",
            auth={"type": "bearer", "credentials": "test-token-123"},
            auth_header="Authorization",
        )
        signer = Mock(name="build_signed_headers")

        with (
            patch(_SEAM_DIAL, _mock_call_mcp_tool({"formats": []})) as cmc,
            patch(_CREATIVE_SIGNER, return_value=signer) as signer_for_tenant,
        ):
            await registry._fetch_formats_operator(agent, tenant_id="tenant_123")

        signer_for_tenant.assert_called_once_with(tenant_id="tenant_123")
        cmc.assert_called_once_with(
            agent_url="https://sandbox.optable.co/admin/adcp/creative/mcp",
            tool="list_creative_formats",
            arguments=_NO_FILTER_FORMAT_ARGS,
            auth={"type": "bearer", "credentials": "test-token-123"},
            auth_header="Authorization",
            timeout=30,
            sign=signer,
        )

    @pytest.mark.asyncio
    async def test_signals_agent_forwards_tenant_request_signer(self):
        """Signals carries the tenant on the dial config, and signs from it."""
        registry = SignalsAgentRegistry()
        agent = SignalsAgent(
            agent_url="https://sandbox.optable.co/admin/adcp/signals/mcp",
            name="Optable Signals",
            auth={"type": "bearer", "credentials": "test-signals-token"},
            auth_header="Authorization",
            tenant_id="tenant_123",
        )
        signer = Mock(name="build_signed_headers")

        with (
            patch(_SEAM_DIAL, _mock_call_mcp_tool({"signals": []})) as cmc,
            patch(_SIGNALS_SIGNER, return_value=signer) as signer_for_tenant,
        ):
            await registry._fetch_signals_operator(agent, brief="test")

        signer_for_tenant.assert_called_once_with(tenant_id="tenant_123")
        cmc.assert_called_once_with(
            agent_url="https://sandbox.optable.co/admin/adcp/signals/mcp",
            tool="get_signals",
            arguments=_SIGNALS_ARGS,
            auth={"type": "bearer", "credentials": "test-signals-token"},
            auth_header="Authorization",
            timeout=30,
            sign=signer,
        )
