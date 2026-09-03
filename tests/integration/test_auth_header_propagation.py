"""Auth config propagation from CreativeAgent/SignalsAgent into the guarded MCP seam.

Before GH #1802, both registries built an ``adcp.AgentConfig`` (via the
deleted ``build_agent_config``/``_build_adcp_client``) and asserted on its
``auth_token``/``auth_header``/``auth_type`` fields. The OPERATOR agent path no
longer builds that config at all — it goes through
``src.core.utils.operator_mcp.call_operator_mcp_tool``, which dials
``call_mcp_tool(auth=..., auth_header=...)``; that function's own
``_build_auth_headers`` (already unit-tested in
``tests/integration/test_mcp_client_util.py::TestBuildAuthHeaders``, including
the exact Optable-shaped bearer/custom-header case) does the header
construction. What is left to prove here is narrower and different: that
``_fetch_formats_operator``/``_fetch_signals_operator`` actually FORWARD the
agent's ``auth``/``auth_header``/``timeout`` through unchanged, rather than
dropping or renaming them — the boundary contract, not the header algebra.

The patch lands on ``call_mcp_tool`` as ``operator_mcp`` imports it, one frame
BELOW the registries: that keeps the whole forwarding chain (registry ->
``call_operator_mcp_tool`` -> ``call_mcp_tool``) under test, so a parameter
dropped at either hop still fails these assertions.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.creative_agent_registry import CreativeAgent, CreativeAgentRegistry
from src.core.signals_agent_registry import SignalsAgent, SignalsAgentRegistry

_SEAM_DIAL = "src.core.utils.operator_mcp.call_mcp_tool"


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

        assert cmc.call_args.kwargs["auth"] == {"type": "bearer", "credentials": "test-token-123"}
        assert cmc.call_args.kwargs["auth_header"] == "Authorization"
        assert cmc.call_args.kwargs["timeout"] == 45

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

        assert cmc.call_args.kwargs["auth_header"] is None
        assert cmc.call_args.kwargs["auth"] == {"type": "token", "credentials": "token-456"}

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

        assert cmc.call_args.kwargs["auth"] == {"type": "bearer", "credentials": "test-signals-token"}
        assert cmc.call_args.kwargs["auth_header"] == "Authorization"
        assert cmc.call_args.kwargs["timeout"] == 60

    @pytest.mark.asyncio
    async def test_signals_agent_no_custom_header_forwards_none(self):
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

        assert cmc.call_args.kwargs["auth_header"] is None
        assert cmc.call_args.kwargs["auth"] == {"type": "token", "credentials": "signals-token-789"}
