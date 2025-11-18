"""Creative Agent Registry for dynamic format discovery per AdCP v2.4.

This module provides:
1. Creative agent registry (system defaults + tenant-specific)
2. Dynamic format discovery via MCP
3. Format caching (in-memory with TTL)
4. Multi-agent support for DCO platforms, custom creative agents

Architecture:
- Default agent: https://creative.adcontextprotocol.org (always available)
- Tenant agents: Configured in creative_agents database table
- Format resolution: Query agents via MCP, cache results
- Preview generation: Delegate to creative agent
- Generative creative: Use agent's create_generative_creative tool
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from adcp import ADCPMultiAgentClient, AgentConfig, ListCreativeFormatsRequest, Protocol
from adcp.exceptions import ADCPAuthenticationError, ADCPConnectionError, ADCPError, ADCPTimeoutError
from adcp.types.generated_poc.list_creative_formats_request import AssetType
from adcp.types.generated_poc.list_creative_formats_request import Type as FormatType

from src.core.schemas import Format, FormatId
from src.core.utils.mcp_client import create_mcp_client  # Keep for custom tools (preview, build)


@dataclass
class CreativeAgent:
    """Represents a creative agent that provides format definitions and creative services."""

    agent_url: str
    name: str
    enabled: bool = True
    priority: int = 1  # Lower = higher priority in search results
    auth: dict[str, Any] | None = None  # Optional auth config for private agents
    auth_header: str | None = None  # Optional custom auth header name
    timeout: int = 30  # Request timeout in seconds


@dataclass
class CachedFormats:
    """Cached format list from a creative agent."""

    formats: list[Format]
    fetched_at: datetime
    ttl_seconds: int = 3600  # 1 hour default

    def is_expired(self) -> bool:
        """Check if cache has expired."""
        return datetime.now(UTC) > self.fetched_at + timedelta(seconds=self.ttl_seconds)


class CreativeAgentRegistry:
    """Registry of creative agents with dynamic format discovery and caching.

    Usage:
        registry = CreativeAgentRegistry()

        # Get all formats from all agents
        formats = await registry.list_all_formats(tenant_id="tenant_123")

        # Search formats across agents
        results = await registry.search_formats(query="300x250", tenant_id="tenant_123")

        # Get specific format
        fmt = await registry.get_format(
            agent_url="https://creative.adcontextprotocol.org",
            format_id="display_300x250_image"
        )
    """

    # Default creative agent (always available)
    # Note: agent_url is the base URL for the creative agent (e.g., https://creative.adcontextprotocol.org)
    # The MCP server endpoint (/mcp) is appended by the MCP client when connecting
    DEFAULT_AGENT = CreativeAgent(
        agent_url="https://creative.adcontextprotocol.org",
        name="AdCP Standard Creative Agent",
        enabled=True,
        priority=1,
    )

    def __init__(self):
        """Initialize registry with empty cache."""
        self._format_cache: dict[str, CachedFormats] = {}  # Key: agent_url

    def _build_adcp_client(self, agents: list[CreativeAgent]) -> ADCPMultiAgentClient:
        """Build AdCP client from creative agent configs.

        Args:
            agents: List of CreativeAgent instances

        Returns:
            ADCPMultiAgentClient configured with all agents
        """
        agent_configs = []
        for agent in agents:
            # Extract auth configuration
            auth_type = "token"
            auth_token = None
            if agent.auth:
                auth_type = agent.auth.get("type", "token")
                auth_token = agent.auth.get("credentials")

            config = AgentConfig(
                id=agent.name,
                agent_uri=str(agent.agent_url),  # Convert AnyUrl to string for adcp 2.5.0
                protocol=Protocol.MCP,
                auth_token=auth_token,
                auth_type=auth_type,
                auth_header=agent.auth_header or "x-adcp-auth",
                timeout=30.0,
            )
            agent_configs.append(config)

        return ADCPMultiAgentClient(agents=agent_configs)

    def _get_tenant_agents(self, tenant_id: str | None) -> list[CreativeAgent]:
        """Get list of creative agents for a tenant.

        Returns:
            List of CreativeAgent instances (default + tenant-specific)
        """
        agents = [self.DEFAULT_AGENT]

        if not tenant_id:
            return agents

        # Load tenant-specific agents from database
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import CreativeAgent as CreativeAgentModel

        with get_db_session() as session:
            stmt = select(CreativeAgentModel).filter_by(tenant_id=tenant_id, enabled=True)
            db_agents = session.scalars(stmt).all()

            for db_agent in db_agents:
                # Parse auth credentials if present
                auth = None
                if db_agent.auth_type and db_agent.auth_credentials:
                    auth = {
                        "type": db_agent.auth_type,
                        "credentials": db_agent.auth_credentials,
                    }
                    # Add auth_header if present (e.g., "Authorization", "x-api-key")
                    if db_agent.auth_header:
                        auth["header"] = db_agent.auth_header

                agents.append(
                    CreativeAgent(
                        agent_url=db_agent.agent_url,
                        name=db_agent.name,
                        enabled=db_agent.enabled,
                        priority=db_agent.priority,
                        auth=auth,
                        auth_header=db_agent.auth_header,
                        timeout=db_agent.timeout,
                    )
                )

        # Sort by priority (lower number = higher priority)
        agents.sort(key=lambda a: a.priority)
        return [a for a in agents if a.enabled]

    async def _fetch_formats_from_agent(
        self,
        client: ADCPMultiAgentClient,
        agent: CreativeAgent,
        max_width: int | None = None,
        max_height: int | None = None,
        min_width: int | None = None,
        min_height: int | None = None,
        is_responsive: bool | None = None,
        asset_types: list[str] | None = None,
        name_search: str | None = None,
        type_filter: str | None = None,
    ) -> list[Format]:
        """Fetch format list from a creative agent via MCP.

        Args:
            client: ADCPMultiAgentClient to use for requests
            agent: CreativeAgent to query
            max_width: Maximum width in pixels (inclusive)
            max_height: Maximum height in pixels (inclusive)
            min_width: Minimum width in pixels (inclusive)
            min_height: Minimum height in pixels (inclusive)
            is_responsive: Filter for responsive formats
            asset_types: Filter by asset types
            name_search: Search by name
            type_filter: Filter by format type (display, video, audio)

        Returns:
            List of Format objects from the agent
        """
        import logging

        logger = logging.getLogger(__name__)

        try:
            # Convert string asset_types to AssetType enums
            typed_asset_types: list[AssetType] | None = None
            if asset_types:
                typed_asset_types = [AssetType(at) for at in asset_types]

            # Convert string type_filter to FormatType enum
            typed_format_type: FormatType | None = None
            if type_filter:
                typed_format_type = FormatType(type_filter)

            # Build request parameters
            request = ListCreativeFormatsRequest(
                max_width=max_width,
                max_height=max_height,
                min_width=min_width,
                min_height=min_height,
                is_responsive=is_responsive,
                asset_types=typed_asset_types,
                name_search=name_search,
                type=typed_format_type,
            )

            # Call agent using adcp library
            logger.info(f"_fetch_formats_from_agent: Calling {agent.name} at {agent.agent_url}")
            result = await client.agent(agent.name).list_creative_formats(request)
            logger.info(f"_fetch_formats_from_agent: Got result status={result.status}, type={type(result)}")

            # Handle response based on status
            if result.status == "completed":
                formats_data = result.data
                if formats_data is None:
                    logger.warning("Completed status but no data in response")
                    return []

                logger.info(
                    f"_fetch_formats_from_agent: Got response with {len(formats_data.formats) if hasattr(formats_data, 'formats') else 'N/A'} formats"
                )

                # Convert to Format objects
                # Note: Format now extends adcp library's Format class.
                # fmt_data is a library Format with format_id.agent_url already set per spec.
                # We convert to our Format subclass to get any additional internal fields.
                formats = []
                for fmt_data in formats_data.formats:
                    # Convert library Format to our Format (which extends it)
                    # model_dump() preserves Pydantic types (AnyUrl, etc.) since we're in the same type hierarchy
                    fmt_dict = fmt_data if isinstance(fmt_data, dict) else fmt_data.model_dump()
                    formats.append(Format(**fmt_dict))

                return formats

            elif result.status == "submitted":
                # Webhook registered for async completion
                logger.info(
                    f"Async operation submitted, webhook: {result.submitted.webhook_url if result.submitted else 'none'}"
                )
                return []

            else:
                logger.warning(f"Unexpected result status: {result.status}")
                return []

        except ADCPAuthenticationError as e:
            logger.error(f"Authentication failed for creative agent {agent.name}: {e.message}")
            raise RuntimeError(f"Authentication failed: {e.message}") from e
        except ADCPTimeoutError as e:
            logger.error(f"Request to creative agent {agent.name} timed out: {e.message}")
            raise RuntimeError(f"Request timed out: {e.message}") from e
        except ADCPConnectionError as e:
            logger.error(f"Failed to connect to creative agent {agent.name}: {e.message}")
            raise RuntimeError(f"Connection failed: {e.message}") from e
        except ADCPError as e:
            logger.error(f"AdCP error with creative agent {agent.name}: {e.message}")
            raise RuntimeError(str(e.message)) from e

    async def get_formats_for_agent(
        self,
        agent: CreativeAgent,
        force_refresh: bool = False,
        max_width: int | None = None,
        max_height: int | None = None,
        min_width: int | None = None,
        min_height: int | None = None,
        is_responsive: bool | None = None,
        asset_types: list[str] | None = None,
        name_search: str | None = None,
        type_filter: str | None = None,
    ) -> list[Format]:
        """Get formats from agent with caching.

        Args:
            agent: CreativeAgent to query
            force_refresh: Skip cache and fetch fresh data
            max_width: Maximum width in pixels (inclusive)
            max_height: Maximum height in pixels (inclusive)
            min_width: Minimum width in pixels (inclusive)
            min_height: Minimum height in pixels (inclusive)
            is_responsive: Filter for responsive formats
            asset_types: Filter by asset types
            name_search: Search by name
            type_filter: Filter by format type (display, video, audio)

        Returns:
            List of Format objects
        """
        # Check cache - only use cache if no filtering parameters provided
        has_filters = any(
            [
                max_width is not None,
                max_height is not None,
                min_width is not None,
                min_height is not None,
                is_responsive is not None,
                asset_types is not None,
                name_search is not None,
                type_filter is not None,
            ]
        )

        cached = self._format_cache.get(agent.agent_url)
        if cached and not cached.is_expired() and not force_refresh and not has_filters:
            return cached.formats

        # Build client for this agent
        client = self._build_adcp_client([agent])

        # Fetch from agent
        formats = await self._fetch_formats_from_agent(
            client,
            agent,
            max_width=max_width,
            max_height=max_height,
            min_width=min_width,
            min_height=min_height,
            is_responsive=is_responsive,
            asset_types=asset_types,
            name_search=name_search,
            type_filter=type_filter,
        )

        # Update cache only if no filtering parameters (cache full result set)
        if not has_filters:
            self._format_cache[agent.agent_url] = CachedFormats(
                formats=formats, fetched_at=datetime.now(UTC), ttl_seconds=3600
            )

        return formats

    async def list_all_formats(
        self,
        tenant_id: str | None = None,
        force_refresh: bool = False,
        max_width: int | None = None,
        max_height: int | None = None,
        min_width: int | None = None,
        min_height: int | None = None,
        is_responsive: bool | None = None,
        asset_types: list[str] | None = None,
        name_search: str | None = None,
        type_filter: str | None = None,
    ) -> list[Format]:
        """List all formats from all registered agents.

        Args:
            tenant_id: Optional tenant ID for tenant-specific agents
            force_refresh: Skip cache and fetch fresh data
            max_width: Maximum width in pixels (inclusive)
            max_height: Maximum height in pixels (inclusive)
            min_width: Minimum width in pixels (inclusive)
            min_height: Minimum height in pixels (inclusive)
            is_responsive: Filter for responsive formats
            asset_types: Filter by asset types
            name_search: Search by name
            type_filter: Filter by format type (display, video, audio)

        Returns:
            List of all Format objects across all agents
        """
        agents = self._get_tenant_agents(tenant_id)
        all_formats = []

        import logging

        logger = logging.getLogger(__name__)
        logger.info(f"list_all_formats: Found {len(agents)} agents for tenant {tenant_id}")

        # Build client for all agents
        client = self._build_adcp_client(agents)

        for agent in agents:
            logger.info(f"list_all_formats: Fetching from {agent.agent_url}")
            try:
                # Check cache first if no filters and not forcing refresh
                has_filters = any(
                    [
                        max_width is not None,
                        max_height is not None,
                        min_width is not None,
                        min_height is not None,
                        is_responsive is not None,
                        asset_types is not None,
                        name_search is not None,
                        type_filter is not None,
                    ]
                )

                cached = self._format_cache.get(agent.agent_url)
                if cached and not cached.is_expired() and not force_refresh and not has_filters:
                    formats = cached.formats
                else:
                    # Fetch from agent
                    formats = await self._fetch_formats_from_agent(
                        client,
                        agent,
                        max_width=max_width,
                        max_height=max_height,
                        min_width=min_width,
                        min_height=min_height,
                        is_responsive=is_responsive,
                        asset_types=asset_types,
                        name_search=name_search,
                        type_filter=type_filter,
                    )

                    # Update cache only if no filtering parameters
                    if not has_filters:
                        self._format_cache[agent.agent_url] = CachedFormats(
                            formats=formats, fetched_at=datetime.now(UTC), ttl_seconds=3600
                        )

                logger.info(f"list_all_formats: Got {len(formats)} formats from {agent.agent_url}")
                all_formats.extend(formats)
            except Exception as e:
                # Log error but continue with other agents
                logger.error(f"Failed to fetch formats from {agent.agent_url}: {e}", exc_info=True)
                continue

        logger.info(f"list_all_formats: Returning {len(all_formats)} total formats")
        return all_formats

    async def search_formats(
        self, query: str, tenant_id: str | None = None, type_filter: str | None = None
    ) -> list[Format]:
        """Search formats across all agents.

        Args:
            query: Search query (matches format_id, name, description)
            tenant_id: Optional tenant ID for tenant-specific agents
            type_filter: Optional format type filter (display, video, etc.)

        Returns:
            List of matching Format objects
        """
        all_formats = await self.list_all_formats(tenant_id)
        query_lower = query.lower()

        results = []
        for fmt in all_formats:
            # Match query against format_id, name, or description
            # format_id is a FormatId object, so we need to access .id
            format_id_str = fmt.format_id.id if isinstance(fmt.format_id, FormatId) else str(fmt.format_id)
            if (
                query_lower in format_id_str.lower()
                or query_lower in fmt.name.lower()
                or (fmt.description and query_lower in fmt.description.lower())
            ):
                # Apply type filter if provided
                if type_filter and fmt.type != type_filter:
                    continue

                results.append(fmt)

        return results

    async def get_format(self, agent_url: str, format_id: str) -> Format | None:
        """Get a specific format from an agent.

        Args:
            agent_url: URL of the creative agent
            format_id: Format ID to retrieve

        Returns:
            Format object or None if not found
        """
        # Find agent
        agent = CreativeAgent(agent_url=agent_url, name="Unknown", enabled=True)

        # Get formats (uses cache)
        formats = await self.get_formats_for_agent(agent)

        # Find matching format
        for fmt in formats:
            # fmt.format_id is a FormatId object with .id attribute, format_id parameter is a string
            if fmt.format_id.id == format_id:
                return fmt

        return None

    async def preview_creative(
        self, agent_url: str, format_id: str, creative_manifest: dict[str, Any]
    ) -> dict[str, Any]:
        """Generate preview renderings for a creative using the creative agent.

        Args:
            agent_url: URL of the creative agent
            format_id: Format ID for the creative
            creative_manifest: Complete creative manifest with all required assets.
                Assets MUST be a dictionary keyed by asset_id from format's asset_requirements.
                Example: {
                    "creative_id": "c123",
                    "name": "Banner Ad",
                    "format_id": "display_300x250",
                    "assets": {
                        "main_image": {"asset_type": "image", "url": "https://..."},
                        "logo": {"asset_type": "image", "url": "https://..."}
                    }
                }

        Returns:
            Preview response containing array of preview variants with preview_url.
            Example: {
                "previews": [{
                    "name": "Default",
                    "renders": [{
                        "preview_url": "https://...",
                        "dimensions": {"width": 300, "height": 250}
                    }]
                }]
            }
        """
        # Use custom MCP client for non-standard tools (preview_creative not in AdCP spec)
        async with create_mcp_client(agent_url=agent_url, timeout=30) as client:
            result = await client.call_tool(
                "preview_creative", {"format_id": format_id, "creative_manifest": creative_manifest}
            )

            # Use structured_content field for JSON response (MCP protocol update)
            if hasattr(result, "structured_content") and result.structured_content:
                return result.structured_content

            # Fallback: Parse result from content field (legacy)
            import json

            if isinstance(result.content, list) and result.content:
                preview_data = result.content[0].text if hasattr(result.content[0], "text") else result.content[0]
                if isinstance(preview_data, str):
                    preview_data = json.loads(preview_data)
                return preview_data

            return {}

    async def build_creative(
        self,
        agent_url: str,
        format_id: str,
        message: str,
        gemini_api_key: str,
        promoted_offerings: dict[str, Any] | None = None,
        context_id: str | None = None,
        finalize: bool = False,
    ) -> dict[str, Any]:
        """Build a creative using AI generation via the creative agent.

        This calls the creative agent's build_creative tool which requires the user's
        Gemini API key (the creative agent doesn't pay for API calls).

        Args:
            agent_url: URL of the creative agent
            format_id: Format ID (must be generative type like display_300x250_generative)
            message: Creative brief or refinement instructions
            gemini_api_key: User's Gemini API key (REQUIRED)
            promoted_offerings: Brand and product information for AI generation
            context_id: Session ID for iterative refinement (optional)
            finalize: Set to true to finalize the creative (default: False)

        Returns:
            Build response containing:
            - message: Status message
            - context_id: Session ID for refinement
            - status: "draft" or "finalized"
            - creative_output: Generated creative manifest with output_format
        """
        # Use custom MCP client for non-standard tools (build_creative not in AdCP spec)
        async with create_mcp_client(agent_url=agent_url, timeout=30) as client:
            params = {
                "message": message,
                "format_id": format_id,
                "gemini_api_key": gemini_api_key,
                "finalize": finalize,
            }

            if promoted_offerings:
                params["promoted_offerings"] = promoted_offerings

            if context_id:
                params["context_id"] = context_id

            result = await client.call_tool("build_creative", params)

            # Use structured_content field for JSON response (MCP protocol update)
            if hasattr(result, "structured_content") and result.structured_content:
                return result.structured_content

            # Fallback: Parse result from content field (legacy)
            import json

            if isinstance(result.content, list) and result.content:
                creative_data = result.content[0].text if hasattr(result.content[0], "text") else result.content[0]
                if isinstance(creative_data, str):
                    creative_data = json.loads(creative_data)
                return creative_data

            return {}


# Global registry instance
_registry: CreativeAgentRegistry | None = None


def get_creative_agent_registry() -> CreativeAgentRegistry:
    """Get the global creative agent registry instance."""
    global _registry
    if _registry is None:
        _registry = CreativeAgentRegistry()
    return _registry
