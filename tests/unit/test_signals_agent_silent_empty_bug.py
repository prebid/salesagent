"""Regression tests for salesagent-9eu: silent return [] masks agent failures in signals.

signals_agent_registry.py has paths that silently return [] without raising.
get_signals() then records no error, producing an empty signals list — which
callers treat as "agent up, genuinely 0 signals" instead of recognizing a
failure condition.

These tests demonstrate the bug: each silent-[] path should raise so callers
can distinguish agent-down from genuinely-empty.

Same pattern as creative_agent_registry.py (fixed in PR #1167).

Bug: prebid/salesagent#1136
Beads: salesagent-9eu
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import AdCPAdapterError
from src.core.signals_agent_registry import SignalsAgent, SignalsAgentRegistry


@pytest.fixture
def registry():
    return SignalsAgentRegistry()


@pytest.fixture
def agent():
    return SignalsAgent(
        agent_url="https://signals.example.com",
        name="test-signals-agent",
        auth={"type": "token", "credentials": "test-token"},
        auth_header="x-test-auth",
    )


def _mock_adcp_client(mock_result: MagicMock) -> MagicMock:
    """Build a mock ADCPMultiAgentClient that returns the given result."""
    mock_agent_proxy = MagicMock()
    mock_agent_proxy.get_signals = AsyncMock(return_value=mock_result)
    mock_client = MagicMock()
    mock_client.agent.return_value = mock_agent_proxy
    return mock_client


class TestGetSignalsAnomalousStatusesMustRaise:
    """_get_signals_from_agent must raise (not return []) for anomalous statuses.

    When these paths silently return [], get_signals() records no error,
    and callers cannot distinguish "agent down" from "genuinely 0 signals".
    """

    @pytest.mark.asyncio
    async def test_completed_with_data_none_raises(self, registry, agent):
        """status=completed but data=None is anomalous — must raise, not return []."""
        mock_result = MagicMock()
        mock_result.status = "completed"
        mock_result.data = None

        mock_client = _mock_adcp_client(mock_result)

        with pytest.raises(AdCPAdapterError):
            await registry._get_signals_from_agent(mock_client, agent, brief="test", tenant_id="test_tenant")

    @pytest.mark.asyncio
    async def test_submitted_with_no_submitted_info_raises(self, registry, agent):
        """status=submitted but submitted=None is anomalous — must raise, not return []."""
        mock_result = MagicMock()
        mock_result.status = "submitted"
        mock_result.submitted = None

        mock_client = _mock_adcp_client(mock_result)

        with pytest.raises(AdCPAdapterError):
            await registry._get_signals_from_agent(mock_client, agent, brief="test", tenant_id="test_tenant")

    @pytest.mark.asyncio
    async def test_unexpected_status_raises(self, registry, agent):
        """Unexpected status (e.g. 'working') must raise, not return []."""
        mock_result = MagicMock()
        mock_result.status = "working"

        mock_client = _mock_adcp_client(mock_result)

        with pytest.raises(AdCPAdapterError):
            await registry._get_signals_from_agent(mock_client, agent, brief="test", tenant_id="test_tenant")


class TestSubmittedWithValidWebhookIsAcceptable:
    """submitted status with a valid webhook is acceptable — signals support async."""

    @pytest.mark.asyncio
    async def test_submitted_with_webhook_returns_empty_list(self, registry, agent):
        """status=submitted with valid webhook_url returns [] (async path)."""
        mock_submitted = MagicMock()
        mock_submitted.webhook_url = "https://webhook.example.com/callback"

        mock_result = MagicMock()
        mock_result.status = "submitted"
        mock_result.submitted = mock_submitted

        mock_client = _mock_adcp_client(mock_result)

        result = await registry._get_signals_from_agent(mock_client, agent, brief="test", tenant_id="test_tenant")
        assert result == [], "Valid async webhook path should return [] (results arrive later)"
