"""Signals Agent Registry for upstream signals discovery integration.

This module provides:
1. Signals agent registry (tenant-specific agents)
2. Dynamic signals discovery via AdCP library
3. Multi-agent support for different signals providers

Architecture:
- No default agent (tenant-specific only)
- Tenant agents: Configured in signals_agents database table
- Signals resolution: Query agents via adcp library, handle responses

Schema Version: AdCP v2.2.0
- Uses signal_spec (not brief from v1)
- Uses deliver_to.platforms as array of strings ["all"] (not single string "all")
- Supports custom auth headers via auth_header parameter

Security:
- Auth credentials stored in database (tenant-specific)
- Custom auth headers supported (e.g., Authorization, x-api-key)
- Bearer token format: "Bearer {token}"
- Token format: "{token}"

Migration Note: Now uses official `adcp` library (v1.0.1) instead of custom MCP client.
- ~100 lines of custom code replaced with official library
- Custom auth headers now fully supported (was critical blocker)
- Maintains backward compatibility with existing API
"""

import logging
from dataclasses import dataclass
from typing import Any

from adcp.types import GetSignalsResponse as LibraryGetSignalsResponse
from pydantic import ValidationError

from src.core.database.models import SignalsAgent as DBSignalsAgent
from src.core.exceptions import AdCPConfigurationError
from src.core.schemas import GetSignalsRequest
from src.core.utils.operator_mcp import ProbeResult, call_operator_mcp_tool, probe_failure

logger = logging.getLogger(__name__)


@dataclass
class SignalsAgent:
    """Represents a signals discovery agent that provides product enhancement via signals.

    Note: priority, max_signal_products, and fallback_to_database are configured per-product,
    not per-agent.
    """

    agent_url: str
    name: str
    enabled: bool = True
    auth: dict[str, Any] | None = None  # Optional auth config for private agents
    auth_header: str | None = None  # HTTP header name for auth (e.g., "Authorization", "x-api-key")
    forward_promoted_offering: bool = True
    timeout: int = 30


class SignalsAgentRegistry:
    """Registry of signals discovery agents with dynamic discovery.

    Usage:
        registry = SignalsAgentRegistry()

        # Get signals from all agents
        signals = await registry.get_signals(
            brief="automotive targeting",
            tenant_id="tenant_123",
            promoted_offering="Tesla Model 3"
        )
    """

    def __init__(self):
        """Initialize registry."""
        pass  # No cache needed - adcp library handles connection pooling

    def _get_tenant_agents(self, tenant_id: str) -> list[SignalsAgent]:
        """Get list of signals agents for a tenant.

        Returns:
            List of SignalsAgent instances (tenant-specific only)
        """
        # Annotated because the list is now filled by extend() from a generator:
        # the old append-in-a-loop gave mypy an element type to infer, and extend
        # does not.
        agents: list[SignalsAgent] = []

        # Load tenant-specific agents from database
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import SignalsAgent as SignalsAgentModel

        with get_db_session() as session:
            stmt = select(SignalsAgentModel).filter_by(tenant_id=tenant_id, enabled=True)
            db_agents = session.scalars(stmt).all()

            agents.extend(self.config_for(db_agent) for db_agent in db_agents)

        # Sort by name for consistent ordering
        agents.sort(key=lambda a: a.name)
        return [a for a in agents if a.enabled]

    async def _fetch_signals_operator(self, agent: SignalsAgent, brief: str) -> list[dict[str, Any]]:
        """Fetch signals from an OPERATOR-configured signals agent, through the guarded MCP seam.

        Routes through ``call_operator_mcp_tool`` — a real MCP handshake, IP-pinned,
        redirect-refusing — rather than ``adcp.ADCPMultiAgentClient``, whose own
        httpx stack no egress policy of ours could reach (adcp 6.6.0 exposes no
        transport injection point; upstream adcp-client-python#1004). Closes the
        gap tracked by GH #1802. Signals agents are ALWAYS
        operator-configured (tenant DB rows) — there is no counterparty-supplied
        signals URL, so every call here takes this path.

        The MCP protocol path only ever returns COMPLETED or FAILED (never
        SUBMITTED — that status exists in the adcp SDK's abstraction for other
        protocols, not for a synchronous MCP tool call), so there is no webhook/
        async branch to preserve here.

        Args:
            agent: SignalsAgent to query
            brief: Search brief/query (mapped onto AdCP's ``signal_spec``)

        Returns:
            List of signal dicts from the agent
        """
        import time

        start_time = time.time()

        request = GetSignalsRequest(signal_spec=brief)
        args = request.model_dump(mode="json", exclude_none=True)

        logger.info(f"[TIMING] Calling agent {agent.name}, brief: {brief[:50]}...")
        payload = await call_operator_mcp_tool(
            agent.agent_url,
            "get_signals",
            args,
            label=f"signals agent {agent.name}",
            auth=agent.auth,
            auth_header=agent.auth_header,
            timeout=agent.timeout,
        )

        if not payload:
            # An empty payload means neither structured_content nor a TextContent
            # block carried anything parseable. GetSignalsResponse.model_validate({})
            # would otherwise validate CLEANLY with signals=None — every field is
            # optional — silently producing signals=[] and masking a genuine
            # agent failure as "agent up, 0 signals" (GH #1802 class bug).
            raise AdCPConfigurationError(f"No parseable content in get_signals response from {agent.name}")
        try:
            parsed = LibraryGetSignalsResponse.model_validate(payload)
        except ValidationError as e:
            raise AdCPConfigurationError(f"Signals agent {agent.name} returned an invalid response") from e

        signals = parsed.signals or []
        total_duration = time.time() - start_time
        logger.info(f"[TIMING] Got {len(signals)} signals in {total_duration:.2f}s")
        return [signal if isinstance(signal, dict) else signal.model_dump(mode="json") for signal in signals]

    async def get_signals(
        self,
        brief: str,
        tenant_id: str,
        principal_id: str | None = None,
        context: dict[str, Any] | None = None,
        principal_data: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Get signals from all registered agents for a tenant.

        Args:
            brief: Search brief/query
            tenant_id: Tenant identifier
            principal_id: Optional principal identifier
            context: Optional context data (may include promoted_offering)
            principal_data: Optional principal information

        Returns:
            List of all signal objects across all agents
        """
        agents = self._get_tenant_agents(tenant_id)
        all_signals: list[dict[str, Any]] = []

        logger.info(f"get_signals: Found {len(agents)} agents for tenant {tenant_id}")

        if not agents:
            return all_signals

        for agent in agents:
            logger.info(f"get_signals: Fetching from {agent.agent_url}")
            try:
                signals = await self._fetch_signals_operator(agent, brief=brief)
                logger.info(f"get_signals: Got {len(signals)} signals from {agent.agent_url}")
                all_signals.extend(signals)
            except Exception as e:
                # Log error but continue with other agents (graceful degradation)
                logger.error(f"Failed to fetch signals from {agent.agent_url}: {e}", exc_info=True)
                continue

        logger.info(f"get_signals: Returning {len(all_signals)} total signals")
        return all_signals

    @staticmethod
    def config_for(db_agent: DBSignalsAgent) -> SignalsAgent:
        """The ONE place a stored signals-agent row becomes a dial config.

        Mirrors :meth:`CreativeAgentRegistry.config_for` for the same reason: a
        probe that rebuilds this mapping by hand dials with a different config
        than production, so a passing probe proves nothing about the path that
        runs. The stored ``timeout`` is read here rather than hard-coded to 30,
        which is what the hand-built version discarded.
        """
        auth = None
        if db_agent.auth_type and db_agent.auth_credentials:
            auth = {"type": db_agent.auth_type, "credentials": db_agent.auth_credentials}
        return SignalsAgent(
            agent_url=db_agent.agent_url,
            name=db_agent.name,
            enabled=db_agent.enabled,
            auth=auth,
            auth_header=db_agent.auth_header,
            # Was passed by _get_tenant_agents' inline mapping and omitted here, so
            # the two disagreed for any row where an operator turned it off: this
            # function fell back to the field default (True). Nothing in src/ reads
            # the field yet, so no dial behaviour differed -- the inline block was
            # the ONLY carrier of the stored value, and dropping it without adding
            # it here would have lost the column's meaning entirely.
            forward_promoted_offering=db_agent.forward_promoted_offering,
            timeout=db_agent.timeout,
        )

    async def probe_agent(self, db_agent: DBSignalsAgent) -> ProbeResult:
        """Dial a stored signals agent exactly as production dials it.

        The public entry point for the operator's test-connection button, with
        the same shape as the creative registry's: the route holds a row, and
        everything between that row and the dial belongs to this class.
        """
        agent = self.config_for(db_agent)
        try:
            signals = await self._fetch_signals_operator(agent, brief="test")
        except Exception as exc:  # noqa: BLE001 - an operator probe reports every failure, it never 500s
            return probe_failure(exc, logger=logger)

        return ProbeResult(
            ok=True,
            message="Successfully connected to signals agent",
            count=len(signals),
        )


# Global registry instance
_registry: SignalsAgentRegistry | None = None


def get_signals_agent_registry() -> SignalsAgentRegistry:
    """Get the global signals agent registry instance."""
    global _registry
    if _registry is None:
        _registry = SignalsAgentRegistry()
    return _registry
