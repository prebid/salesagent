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

Testing:
- When ADCP_TESTING=true, returns mock formats instead of calling external services
- This avoids timeouts in CI when external creative agents are unreachable
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from adcp import ADCPMultiAgentClient, ListCreativeFormatsRequest
from adcp.exceptions import ADCPAuthenticationError, ADCPConnectionError, ADCPError, ADCPTimeoutError
from adcp.types import AssetContentType as AssetType
from adcp.types.generated_poc.core.error import Error as AdCPResponseError
from adcp.types.generated_poc.core.format import Assets
from yarl import URL

from src.core.exceptions import AdCPAdapterError
from src.core.schemas import Format, FormatId, url


@dataclass
class FormatFetchResult:
    """Result from list_all_formats_with_errors() — formats + per-agent errors.

    Decision: docs/design/error-propagation-in-format-discovery.md
    """

    formats: list[Format]
    errors: list[AdCPResponseError]


from src.core.utils.mcp_client import create_mcp_client  # Keep for custom tools (preview, build)


def _create_mock_format(format_id_str: str, name: str, asset_type: str) -> Format:
    """Create a single mock format with proper typing for testing."""
    from adcp.types.generated_poc.core.format import Assets5

    # adcp 3.6.0: Assets classes are type-discriminated with Literal asset_type fields.
    # Assets = image, Assets5 = video. Pass asset_type as plain string (not enum).
    if asset_type == "video":
        asset_item: Assets | Assets5 = Assets5(
            item_type="individual",
            asset_id="primary",
            asset_type="video",
            required=True,
        )
    else:
        asset_item = Assets(
            item_type="individual",
            asset_id="primary",
            asset_type="image",
            required=True,
        )
    assets: list[Assets | Assets5] = [asset_item]
    # Use Format (our extended class) instead of AdcpFormat to include is_standard field
    # Explicitly pass None for optional internal fields to satisfy mypy
    return Format(
        format_id=FormatId(id=format_id_str, agent_url=url("https://creative.adcontextprotocol.org")),
        name=name,
        assets=assets,
        is_standard=True,  # Mock formats are standard formats
        platform_config=None,
        category=None,
        requirements=None,
        iab_specification=None,
        accepts_3p_tags=None,
    )


def _get_mock_formats() -> list[Format]:
    """Return mock formats for testing mode (ADCP_TESTING=true).

    These formats match what the real creative agent returns, but without
    making external HTTP calls. Used in CI to avoid timeouts.
    """
    # Create mock formats using our Format class (which includes is_standard field)
    return [
        _create_mock_format("display_300x250_image", "Medium Rectangle", "image"),
        _create_mock_format("display_728x90_image", "Leaderboard", "image"),
        _create_mock_format("display_300x600_image", "Half Page", "image"),
        _create_mock_format("display_160x600_image", "Wide Skyscraper", "image"),
        _create_mock_format("display_320x50_image", "Mobile Leaderboard", "image"),
        _create_mock_format("video_standard", "Standard Video", "video"),
        _create_mock_format("video_standard_30s", "Standard Video 30s", "video"),
        _create_mock_format("video_vast", "VAST Video", "video"),
        _create_mock_format("display_image", "Display Image", "image"),
        _create_mock_format("display_html", "Display HTML", "image"),
        _create_mock_format("display_js", "Display JavaScript", "image"),
    ]


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
    # Reads CREATIVE_AGENT_URL env var so CI can point at a containerized agent.
    DEFAULT_AGENT = CreativeAgent(
        agent_url=os.environ.get("CREATIVE_AGENT_URL", "https://creative.adcontextprotocol.org"),
        name="AdCP Standard Creative Agent",
        enabled=True,
        priority=1,
    )

    def __init__(self):
        """Initialize registry with empty cache."""
        self._format_cache: dict[str, CachedFormats] = {}  # Key: normalized agent_url

    @staticmethod
    def _cache_key(agent_url: str) -> str:
        """Canonicalize agent URL for consistent cache keys (RFC 3986).

        yarl handles: scheme/host lowercase, default port removal, percent-encoding.
        We additionally strip trailing slash so `/` and empty path are equivalent.
        """
        return str(URL(str(agent_url))).rstrip("/")

    def _build_adcp_client(self, agents: list[CreativeAgent]) -> ADCPMultiAgentClient:
        """Build AdCP client from creative agent configs."""
        from src.core.helpers.adapter_helpers import build_agent_config

        return ADCPMultiAgentClient(agents=[build_agent_config(agent) for agent in agents])

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

            # Build request parameters
            # Note: type_filter (FormatCategory) removed in adcp 3.12 — formats are
            # categorized structurally via assets[].asset_type, not a top-level enum.
            request = ListCreativeFormatsRequest(
                max_width=max_width,
                max_height=max_height,
                min_width=min_width,
                min_height=min_height,
                is_responsive=is_responsive,
                asset_types=typed_asset_types,
                name_search=name_search,
            )

            # Call agent using adcp library
            logger.info(f"_fetch_formats_from_agent: Calling {agent.name} at {agent.agent_url}")
            result = await client.agent(agent.name).list_creative_formats(request)
            logger.info(f"_fetch_formats_from_agent: Got result status={result.status}, type={type(result)}")

            # Handle response based on status
            if result.status == "completed":
                formats_data = result.data
                if formats_data is None:
                    raise AdCPAdapterError("Completed status but no data in response")

                logger.info(
                    f"_fetch_formats_from_agent: Got response with {len(formats_data.formats) if hasattr(formats_data, 'formats') else 'N/A'} formats"
                )

                # Convert to Format objects
                # Note: Format now extends adcp library's Format class.
                # fmt_data is a library Format with format_id.agent_url already set per spec.
                # We convert to our Format subclass to get any additional internal fields.
                formats = []
                for fmt_data in formats_data.formats:
                    # Convert library Format to our local Format subclass
                    # from_attributes=True allows accepting parent class instances
                    formats.append(Format.model_validate(fmt_data, from_attributes=True))

                return formats

            elif result.status == "submitted":
                raise AdCPAdapterError(f"Unexpected submitted status for list_creative_formats from {agent.name}")

            elif result.status == "failed":
                # Log detailed error information for debugging
                # Use getattr for safe access in case response structure varies
                error_msg = (
                    getattr(result, "error", None) or getattr(result, "message", None) or "No error details provided"
                )

                # adcp SDK 3.6.0 requires structuredContent but some creative agents
                # return TextContent with JSON. Also falls back when the SDK fails
                # with generic errors (e.g., "no running event loop" → "No error
                # details provided") that indicate an SDK-level transport issue.
                sdk_transport_error = (
                    "structuredContent" in str(error_msg)
                    or "No error details provided" in str(error_msg)
                    or "no running event loop" in str(error_msg)
                    or "Failed to connect" in str(error_msg)
                )
                if sdk_transport_error:
                    logger.warning(f"adcp SDK transport issue, falling back to raw HTTP: {error_msg}")
                    return await self._fetch_formats_raw_mcp(agent)

                logger.error(f"Creative agent {agent.name} returned FAILED status. Error: {error_msg}")
                debug_info = getattr(result, "debug_info", None)
                if debug_info:
                    logger.debug(f"Debug info: {debug_info}")
                raise AdCPAdapterError(f"Creative agent format fetch failed: {error_msg}")

            else:
                raise AdCPAdapterError(f"Unexpected result status from {agent.name}: {result.status}")

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

    async def _fetch_formats_raw_mcp(self, agent: CreativeAgent) -> list[Format]:
        """Fallback: fetch formats via raw HTTP when adcp SDK rejects TextContent.

        The adcp SDK 3.6.0 requires structuredContent in MCP responses, but some
        creative agents return TextContent with JSON. This method calls the MCP
        endpoint directly via HTTP and parses the JSON response.
        """
        import json
        import logging

        import httpx

        logger = logging.getLogger(__name__)
        agent_url = str(agent.agent_url).rstrip("/")
        # MCP endpoint may be at /mcp (as per adcp SDK fallback behavior)
        mcp_url = f"{agent_url}/mcp" if not agent_url.endswith("/mcp") else agent_url

        # Build headers with auth credentials if configured
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if agent.auth:
            auth_header = agent.auth_header or "x-adcp-auth"
            auth_token = agent.auth.get("credentials")
            if auth_token:
                headers[auth_header] = auth_token

        import asyncio

        max_retries = 3
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=agent.timeout) as http:
                    response = await http.post(
                        mcp_url,
                        json={
                            "jsonrpc": "2.0",
                            "method": "tools/call",
                            "params": {"name": "list_creative_formats", "arguments": {}},
                            "id": 1,
                        },
                        headers=headers,
                    )
                    if response.status_code == 429 and attempt < max_retries - 1:
                        retry_after = int(response.headers.get("Retry-After", 2**attempt))
                        logger.warning(f"Creative agent 429, retrying in {retry_after}s (attempt {attempt + 1})")
                        await asyncio.sleep(retry_after)
                        continue
                    response.raise_for_status()
                    break  # success
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                logger.error(f"Creative agent fallback HTTP error: {exc.response.status_code} from {mcp_url}")
                raise RuntimeError(f"Creative agent HTTP error: {exc.response.status_code}") from exc
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < max_retries - 1:
                    logger.warning(f"Creative agent fallback timed out, retrying (attempt {attempt + 1})")
                    await asyncio.sleep(2**attempt)
                    continue
                logger.error(f"Creative agent fallback timed out: {mcp_url}")
                raise RuntimeError(f"Request timed out: {mcp_url}") from exc
            except httpx.RequestError as exc:
                last_exc = exc
                logger.error(f"Creative agent fallback connection failed: {mcp_url} — {exc}")
                raise RuntimeError(f"Connection failed: {mcp_url} — {exc}") from exc
        else:
            raise RuntimeError(f"Creative agent HTTP error after {max_retries} retries") from last_exc

        # Parse SSE or JSON response
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            for line in response.text.split("\n"):
                if line.startswith("data: "):
                    event_data = json.loads(line[6:])
                    if "result" in event_data:
                        return self._parse_mcp_tool_result(event_data["result"], logger)
        else:
            data = response.json()
            if "result" in data:
                return self._parse_mcp_tool_result(data["result"], logger)

        raise AdCPAdapterError(f"No parseable result in MCP response from {agent.agent_url}")

    def _parse_mcp_tool_result(self, result: dict, logger: Any) -> list[Format]:
        """Parse formats from an MCP tools/call result."""
        import json

        content_list = result.get("content", [])
        for item in content_list:
            if item.get("type") == "text" and item.get("text"):
                data = json.loads(item["text"])
                formats_list = data.get("formats", [])
                formats = [Format.model_validate(fmt_data) for fmt_data in formats_list]
                logger.info(f"_fetch_formats_raw_mcp: Parsed {len(formats)} formats from TextContent")
                return formats
        raise AdCPAdapterError("No text content in MCP tool result")

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
        # In testing mode (ADCP_TESTING=true), return mock formats to avoid external HTTP calls
        if os.environ.get("ADCP_TESTING", "").lower() == "true":
            return _get_mock_formats()

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

        cache_key = self._cache_key(agent.agent_url)
        cached = self._format_cache.get(cache_key)
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
            self._format_cache[cache_key] = CachedFormats(
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

        Backward-compatible wrapper — returns only formats, discards errors.
        For error visibility, use list_all_formats_with_errors() instead.
        """
        result = await self.list_all_formats_with_errors(
            tenant_id=tenant_id,
            force_refresh=force_refresh,
            max_width=max_width,
            max_height=max_height,
            min_width=min_width,
            min_height=min_height,
            is_responsive=is_responsive,
            asset_types=asset_types,
            name_search=name_search,
            type_filter=type_filter,
        )
        return result.formats

    async def list_all_formats_with_errors(
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
    ) -> FormatFetchResult:
        """List all formats from all registered agents, with per-agent error reporting.

        Decision: docs/design/error-propagation-in-format-discovery.md

        Returns FormatFetchResult with:
        - formats: Formats from all healthy agents
        - errors: One AdCP error per failed agent (code + message)

        When all agents succeed, errors is empty.
        When some agents fail, returns partial results + errors for failed agents.
        """
        import logging

        logger = logging.getLogger(__name__)

        # In testing mode (ADCP_TESTING=true), return mock formats to avoid external HTTP calls
        if os.environ.get("ADCP_TESTING", "").lower() == "true":
            logger.info("list_all_formats: Using mock formats (ADCP_TESTING=true)")
            return FormatFetchResult(formats=_get_mock_formats(), errors=[])

        agents = self._get_tenant_agents(tenant_id)
        all_formats: list[Format] = []
        errors: list[AdCPResponseError] = []

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

                cache_key = self._cache_key(agent.agent_url)
                cached = self._format_cache.get(cache_key)
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
                        self._format_cache[cache_key] = CachedFormats(
                            formats=formats, fetched_at=datetime.now(UTC), ttl_seconds=3600
                        )

                logger.info(f"list_all_formats: Got {len(formats)} formats from {agent.agent_url}")
                all_formats.extend(formats)
            except Exception as e:
                logger.error(f"Failed to fetch formats from {agent.agent_url}: {e}", exc_info=True)
                errors.append(
                    AdCPResponseError(
                        code="AGENT_UNREACHABLE",
                        message=f"Creative agent at {agent.agent_url} is unreachable: {e}",
                    )
                )
                continue

        logger.info(f"list_all_formats: Returning {len(all_formats)} formats, {len(errors)} errors")
        return FormatFetchResult(formats=all_formats, errors=errors)

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
