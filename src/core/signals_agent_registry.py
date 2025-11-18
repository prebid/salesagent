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

from adcp import ADCPMultiAgentClient, AgentConfig, GetSignalsRequest, Protocol
from adcp.exceptions import ADCPAuthenticationError, ADCPConnectionError, ADCPError, ADCPTimeoutError
from adcp.types.generated_poc.destination import Destination1
from adcp.types.generated_poc.get_signals_request import DeliverTo

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
        agents = []

        # Load tenant-specific agents from database
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import SignalsAgent as SignalsAgentModel

        with get_db_session() as session:
            stmt = select(SignalsAgentModel).filter_by(tenant_id=tenant_id, enabled=True)
            db_agents = session.scalars(stmt).all()

            for db_agent in db_agents:
                # Parse auth credentials if present
                auth = None
                if db_agent.auth_type and db_agent.auth_credentials:
                    auth = {
                        "type": db_agent.auth_type,
                        "credentials": db_agent.auth_credentials,
                    }

                agents.append(
                    SignalsAgent(
                        agent_url=db_agent.agent_url,
                        name=db_agent.name,
                        enabled=db_agent.enabled,
                        auth=auth,
                        auth_header=db_agent.auth_header,
                        forward_promoted_offering=db_agent.forward_promoted_offering,
                        timeout=db_agent.timeout,
                    )
                )

        # Sort by name for consistent ordering
        agents.sort(key=lambda a: a.name)
        return [a for a in agents if a.enabled]

    def _build_adcp_client(self, agents: list[SignalsAgent]) -> ADCPMultiAgentClient:
        """Build AdCP client from signals agent configs.

        Args:
            agents: List of SignalsAgent instances

        Returns:
            Configured ADCPMultiAgentClient
        """
        agent_configs = []

        for agent in agents:
            # Determine auth type and token
            auth_type = "token"  # Default
            auth_token = None

            if agent.auth:
                auth_type = agent.auth.get("type", "token")
                auth_token = agent.auth.get("credentials")

            # Map to AgentConfig
            config = AgentConfig(
                id=agent.name,  # Use name as ID for readability
                agent_uri=str(agent.agent_url),  # Convert AnyUrl to string for adcp 2.5.0
                protocol=Protocol.MCP,  # Signals agents use MCP protocol
                auth_token=auth_token,
                auth_type=auth_type,
                auth_header=agent.auth_header or "x-adcp-auth",
                timeout=float(agent.timeout),
            )
            agent_configs.append(config)

        return ADCPMultiAgentClient(agents=agent_configs)

    async def _get_signals_from_agent(
        self,
        client: ADCPMultiAgentClient,
        agent: SignalsAgent,
        brief: str,
        tenant_id: str,
        principal_id: str | None = None,
        context: dict[str, Any] | None = None,
        principal_data: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch signals from a signals discovery agent via adcp library.

        Args:
            client: AdCP client instance
            agent: SignalsAgent to query
            brief: Search brief/query
            tenant_id: Tenant identifier
            principal_id: Optional principal identifier
            context: Optional context data
            principal_data: Optional principal information

        Returns:
            List of signal objects from the agent
        """
        import time

        start_time = time.time()

        try:
            # Build request parameters using new AdCP v2.2.0 schema
            # Map our old 'brief' parameter to 'signal_spec'
            signal_spec = brief

            # Build deliver_to (required in new schema)
            # Per AdCP spec, deliver_to requires countries and destinations arrays
            # destinations requires at least 1 item - use a generic "all platforms" destination
            deliver_to = DeliverTo(
                countries=[],  # Empty = all countries
                destinations=[
                    Destination1(
                        type="platform",  # Generic platform destination
                        platform="all",  # All platforms
                    )
                ],
            )

            # Create typed request (AdCP v2.2.0 format)
            request = GetSignalsRequest(
                signal_spec=signal_spec,
                deliver_to=deliver_to,
            )

            logger.info(f"[TIMING] Calling agent {agent.name} for tenant {tenant_id}, brief: {brief[:50]}...")
            call_start = time.time()

            # Call agent
            result = await client.agent(agent.name).get_signals(request)

            call_duration = time.time() - call_start
            logger.info(f"[TIMING] Agent call completed in {call_duration:.2f}s, status: {result.status}")

            # Handle response based on status
            if result.status == "completed":
                # Synchronous completion
                if result.data is None:
                    logger.warning("Completed status but no data in response")
                    return []
                signals = result.data.signals
                total_duration = time.time() - start_time
                logger.info(f"[TIMING] Got {len(signals)} signals synchronously in {total_duration:.2f}s total")
                # Convert Signal objects to dicts for internal use
                # AdCP library returns Signal objects, but our internal code expects dicts
                # Handle both Signal objects (from adcp library) and dicts (from some test/error scenarios)
                result_signals = []
                for signal in signals:
                    if isinstance(signal, dict):
                        # Already a dict, use as-is
                        result_signals.append(signal)
                    else:
                        # Pydantic Signal object, convert to dict
                        result_signals.append(signal.model_dump(mode="json"))
                return result_signals

            elif result.status == "submitted":
                # Asynchronous completion - webhook registered
                total_duration = time.time() - start_time
                if result.submitted is None:
                    logger.warning("Submitted status but no submitted info in response")
                    return []
                logger.info(
                    f"[TIMING] Async operation submitted in {total_duration:.2f}s, "
                    f"webhook: {result.submitted.webhook_url}"
                )
                # For now, return empty list (webhook will deliver results later)
                return []

            else:
                logger.warning(f"_get_signals_from_agent: Unexpected status: {result.status}")
                return []

        except ADCPAuthenticationError as e:
            logger.error(f"Authentication failed for {agent.name}: {e.message}")
            raise RuntimeError(f"Authentication failed: {e.message}") from e

        except ADCPTimeoutError as e:
            logger.error(f"Request timed out for {agent.name}: {e.message}")
            raise RuntimeError(f"Request timed out: {e.message}") from e

        except ADCPConnectionError as e:
            logger.error(f"Connection failed for {agent.name}: {e.message}")
            raise RuntimeError(f"Connection failed: {e.message}") from e

        except ADCPError as e:
            logger.error(f"AdCP error for {agent.name}: {e.message}")
            raise RuntimeError(f"AdCP error: {e.message}") from e

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

        # Build AdCP client for all agents and use as async context manager
        client = self._build_adcp_client(agents)

        # Use async context manager to ensure proper cleanup
        async with client:
            # Query each agent
            for agent in agents:
                logger.info(f"get_signals: Fetching from {agent.agent_url}")
                try:
                    signals = await self._get_signals_from_agent(
                        client,
                        agent,
                        brief=brief,
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        context=context,
                        principal_data=principal_data,
                    )
                    logger.info(f"get_signals: Got {len(signals)} signals from {agent.agent_url}")
                    all_signals.extend(signals)
                except Exception as e:
                    # Log error but continue with other agents (graceful degradation)
                    logger.error(f"Failed to fetch signals from {agent.agent_url}: {e}", exc_info=True)
                    continue

        logger.info(f"get_signals: Returning {len(all_signals)} total signals")
        return all_signals

    async def test_connection(
        self, agent_url: str, auth: dict[str, Any] | None = None, auth_header: str | None = None
    ) -> dict[str, Any]:
        """Test connection to a signals agent.

        Args:
            agent_url: URL of the signals agent
            auth: Optional authentication configuration
            auth_header: Optional custom auth header name

        Returns:
            dict with success status and message/error
        """
        try:
            # Create test agent config
            test_agent = SignalsAgent(
                agent_url=agent_url,
                name="Test Agent",
                enabled=True,
                auth=auth,
                auth_header=auth_header,
                timeout=30,
            )

            # Build AdCP client and use as async context manager
            client = self._build_adcp_client([test_agent])

            async with client:
                # Try to fetch signals with minimal query
                signals = await self._get_signals_from_agent(
                    client,
                    test_agent,
                    brief="test",
                    tenant_id="test_tenant",
                )

                return {
                    "success": True,
                    "message": "Successfully connected to signals agent",
                    "signal_count": len(signals),
                }

        except ADCPAuthenticationError as e:
            logger.error(f"Connection test failed (auth): {e.message}")
            return {
                "success": False,
                "error": f"Authentication failed: {e.message}. Check credentials and auth header.",
            }

        except ADCPConnectionError as e:
            logger.error(f"Connection test failed (connection): {e.message}")
            return {
                "success": False,
                "error": f"Connection failed: {e.message}. Check agent URL and network.",
            }

        except Exception as e:
            logger.error(f"Connection test failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Connection failed: {str(e)}",
            }


# Global registry instance
_registry: SignalsAgentRegistry | None = None


def get_signals_agent_registry() -> SignalsAgentRegistry:
    """Get the global signals agent registry instance."""
    global _registry
    if _registry is None:
        _registry = SignalsAgentRegistry()
    return _registry
