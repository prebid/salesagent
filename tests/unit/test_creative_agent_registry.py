"""Unit tests for Creative Agent Registry adcp library integration."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import AnyUrl

from src.core.creative_agent_registry import CreativeAgent, CreativeAgentRegistry
from src.core.exceptions import AdCPAdapterError


class TestCacheKeyAcceptsAnyUrl:
    """Regression tests for #1106: _cache_key must accept Pydantic AnyUrl.

    FormatId.agent_url is AnyUrl (not a str subclass in Pydantic v2).
    When GAM line item creation resolves formats, the AnyUrl flows through
    format_resolver → creative_agent_registry._cache_key → yarl.URL().
    yarl.URL() rejects non-str input with TypeError.
    """

    def test_cache_key_accepts_pydantic_anyurl(self):
        """_cache_key must not crash when given AnyUrl instead of str."""
        registry = CreativeAgentRegistry()
        agent_url = AnyUrl("https://creative.adcontextprotocol.org/")
        result = registry._cache_key(agent_url)
        assert result == "https://creative.adcontextprotocol.org"

    def test_cache_key_normalizes_anyurl_same_as_str(self):
        """AnyUrl and equivalent str must produce the same cache key."""
        registry = CreativeAgentRegistry()
        str_key = registry._cache_key("https://creative.adcontextprotocol.org/")
        anyurl_key = registry._cache_key(AnyUrl("https://creative.adcontextprotocol.org/"))
        assert str_key == anyurl_key

    @pytest.mark.asyncio
    async def test_get_format_accepts_anyurl_agent_url(self, monkeypatch):
        """get_format must accept AnyUrl and use the local SDK reference catalog."""
        monkeypatch.delenv("ADCP_TESTING", raising=False)
        registry = CreativeAgentRegistry()

        # Patch _fetch to avoid real HTTP — we only test the cache_key path
        async def mock_fetch(*args, **kwargs):
            return []

        monkeypatch.setattr(registry, "_fetch_formats_from_agent", mock_fetch)

        result = await registry.get_format(AnyUrl("https://creative.adcontextprotocol.org/"), "display_300x250_image")
        assert result is not None
        assert result.format_id.id == "display_300x250_image"


class TestCreativeAgentRegistry:
    """Test suite for Creative Agent Registry adcp integration."""

    def test_build_adcp_client_with_custom_auth_header(self):
        """Test _build_adcp_client correctly maps custom auth headers."""
        registry = CreativeAgentRegistry()

        # Test agent with custom auth header
        test_agents = [
            CreativeAgent(
                agent_url="https://test-agent.example.com/mcp",
                name="Test Agent",
                enabled=True,
                priority=1,
                auth={"type": "bearer", "credentials": "test-token-123"},
                auth_header="Authorization",  # Custom header
            )
        ]

        client = registry._build_adcp_client(test_agents)

        # Verify client was created
        assert client is not None

        # Verify agent config is correct (check via client._agents if accessible)
        # Note: We can't easily verify internal AgentConfig without accessing private attrs
        # But we can verify the method doesn't raise and returns a client
        assert hasattr(client, "agent")

    def test_build_adcp_client_with_default_auth_header(self):
        """Test _build_adcp_client uses default x-adcp-auth when no custom header."""
        registry = CreativeAgentRegistry()

        test_agents = [
            CreativeAgent(
                agent_url="https://default-agent.example.com/mcp",
                name="Default Agent",
                enabled=True,
                priority=1,
                auth={"type": "token", "credentials": "token-456"},
                auth_header=None,  # No custom header
            )
        ]

        client = registry._build_adcp_client(test_agents)

        assert client is not None
        assert hasattr(client, "agent")

    def test_build_adcp_client_with_no_auth(self):
        """Test _build_adcp_client handles agents without auth."""
        registry = CreativeAgentRegistry()

        test_agents = [
            CreativeAgent(
                agent_url="https://public-agent.example.com/mcp",
                name="Public Agent",
                enabled=True,
                priority=1,
                auth=None,
                auth_header=None,
            )
        ]

        client = registry._build_adcp_client(test_agents)

        assert client is not None

    @pytest.mark.asyncio
    async def test_fetch_formats_from_agent_with_adcp_success(self):
        """Test _fetch_formats_from_agent with successful adcp response."""
        registry = CreativeAgentRegistry()

        test_agent = CreativeAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            priority=1,
        )

        # Mock ADCPMultiAgentClient
        mock_client = Mock()
        mock_agent_client = Mock()

        # Mock format data as dicts (as returned by adcp library)
        # Using spec-compliant renders array for dimensions (not top-level dimensions field)
        mock_formats = [
            {
                "format_id": {"agent_url": "https://test-agent.example.com/mcp", "id": "display_300x250"},
                "name": "Display 300x250",
                "type": "display",
                "renders": [{"role": "primary", "dimensions": {"width": 300, "height": 250, "unit": "px"}}],
            },
            {
                "format_id": {"agent_url": "https://test-agent.example.com/mcp", "id": "display_728x90"},
                "name": "Display 728x90",
                "type": "display",
                "renders": [{"role": "primary", "dimensions": {"width": 728, "height": 90, "unit": "px"}}],
            },
        ]

        mock_result = Mock()
        mock_result.status = "completed"
        mock_result.data = Mock()
        mock_result.data.formats = mock_formats

        mock_agent_client.list_creative_formats = AsyncMock(return_value=mock_result)
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Call the method
        formats = await registry._fetch_formats_from_agent(mock_client, test_agent, max_width=1920, max_height=1080)

        # Verify results
        assert len(formats) == 2
        assert formats[0].format_id.id == "display_300x250"
        assert formats[1].format_id.id == "display_728x90"

        # Verify agent_url was set
        # Note: Can't directly check since Format is constructed, but method should set it

    @pytest.mark.asyncio
    async def test_fetch_formats_from_agent_with_async_submission(self):
        """Test _fetch_formats_from_agent handles async webhook submission."""
        registry = CreativeAgentRegistry()

        test_agent = CreativeAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            priority=1,
        )

        # Mock async submission response
        mock_client = Mock()
        mock_agent_client = Mock()

        mock_result = Mock()
        mock_result.status = "submitted"
        mock_result.submitted = Mock()
        mock_result.submitted.webhook_url = "https://webhook.example.com/callback"

        mock_agent_client.list_creative_formats = AsyncMock(return_value=mock_result)
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Submitted status is anomalous for list_creative_formats — must raise
        # Fix for salesagent-kwws: silent return [] masked failures as 'no formats'
        with pytest.raises(AdCPAdapterError, match="Unexpected submitted status"):
            await registry._fetch_formats_from_agent(mock_client, test_agent)

    @pytest.mark.asyncio
    async def test_fetch_formats_from_agent_handles_auth_error(self):
        """Test _fetch_formats_from_agent handles authentication errors."""
        from adcp.exceptions import ADCPAuthenticationError

        registry = CreativeAgentRegistry()

        test_agent = CreativeAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            priority=1,
        )

        # Mock authentication error
        mock_client = Mock()
        mock_agent_client = Mock()

        auth_error = ADCPAuthenticationError("Invalid credentials")
        mock_agent_client.list_creative_formats = AsyncMock(side_effect=auth_error)
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Should raise RuntimeError (wrapped)
        with pytest.raises(RuntimeError, match="Authentication failed"):
            await registry._fetch_formats_from_agent(mock_client, test_agent)

    @pytest.mark.asyncio
    async def test_fetch_formats_from_agent_handles_timeout_error(self):
        """Test _fetch_formats_from_agent handles timeout errors."""
        from adcp.exceptions import ADCPTimeoutError

        registry = CreativeAgentRegistry()

        test_agent = CreativeAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            priority=1,
        )

        # Mock timeout error
        mock_client = Mock()
        mock_agent_client = Mock()

        timeout_error = ADCPTimeoutError(
            message="Request timed out",
            agent_id="Test Agent",
            agent_uri="https://test-agent.example.com/mcp",
            timeout=30.0,
        )
        mock_agent_client.list_creative_formats = AsyncMock(side_effect=timeout_error)
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Should raise RuntimeError with timeout message
        with pytest.raises(RuntimeError, match="Request timed out"):
            await registry._fetch_formats_from_agent(mock_client, test_agent)

    @pytest.mark.asyncio
    async def test_fetch_formats_from_agent_handles_connection_error(self):
        """Test _fetch_formats_from_agent handles connection errors."""
        from adcp.exceptions import ADCPConnectionError

        registry = CreativeAgentRegistry()

        test_agent = CreativeAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            priority=1,
        )

        # Mock connection error
        mock_client = Mock()
        mock_agent_client = Mock()

        conn_error = ADCPConnectionError("Connection refused")
        mock_agent_client.list_creative_formats = AsyncMock(side_effect=conn_error)
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Should raise RuntimeError
        with pytest.raises(RuntimeError, match="Connection failed"):
            await registry._fetch_formats_from_agent(mock_client, test_agent)

    @pytest.mark.asyncio
    async def test_fetch_formats_from_agent_handles_library_format(self):
        """Test _fetch_formats_from_agent converts library Format to local Format via model_validate."""
        from adcp.types import Format as LibraryFormat

        registry = CreativeAgentRegistry()

        test_agent = CreativeAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            priority=1,
        )

        # Use a real library Format object (as returned by adcp client)
        mock_client = Mock()
        mock_agent_client = Mock()

        library_format = LibraryFormat(
            format_id={"agent_url": "https://test-agent.example.com/mcp", "id": "display_300x250"},
            name="Display 300x250",
            type="display",
            renders=[{"role": "primary", "dimensions": {"width": 300, "height": 250}}],
        )

        mock_result = Mock()
        mock_result.status = "completed"
        mock_result.data = Mock()
        mock_result.data.formats = [library_format]

        mock_agent_client.list_creative_formats = AsyncMock(return_value=mock_result)
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Call the method
        formats = await registry._fetch_formats_from_agent(mock_client, test_agent)

        # Verify format was constructed as our local Format subclass
        assert len(formats) == 1
        assert formats[0].format_id.id == "display_300x250"


class TestStaleCacheFallback:
    """When a live fetch fails and a cached entry exists, serve the cache
    instead of returning an empty list to the caller.

    Issue: bokelley/salesagent#523
    """

    @pytest.fixture(autouse=True)
    def _disable_testing_mode(self, monkeypatch):
        monkeypatch.delenv("ADCP_TESTING", raising=False)

    @staticmethod
    def _agent() -> CreativeAgent:
        return CreativeAgent(
            agent_url="https://creative.example.com",
            name="Test Agent",
            enabled=True,
            priority=1,
        )

    @staticmethod
    def _seed_cache(registry: CreativeAgentRegistry, agent: CreativeAgent, *, age_seconds: int, formats):
        from datetime import UTC, datetime, timedelta

        from src.core.creative_agent_registry import CachedFormats

        key = registry._cache_key(agent.agent_url)
        registry._format_cache[key] = CachedFormats(
            formats=formats,
            fetched_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
            ttl_seconds=3600,
        )

    @staticmethod
    async def _call_helper(registry, agent, *, has_filters: bool = False):
        """Invoke _fetch_for_agent_with_cache with required kwargs (client mocked).

        _fetch_formats_from_agent is patched in each test, so the client value
        never reaches the network.
        """
        return await registry._fetch_for_agent_with_cache(
            client=Mock(),
            agent=agent,
            force_refresh=False,
            has_filters=has_filters,
            max_width=None,
            max_height=None,
            min_width=None,
            min_height=None,
            is_responsive=None,
            asset_types=None,
            name_search=None,
            type_filter=None,
        )

    @pytest.mark.asyncio
    async def test_fresh_cache_hit_skips_fetch(self, monkeypatch):
        """Fresh cache: no fetch, returns cached formats."""
        registry = CreativeAgentRegistry()
        agent = self._agent()
        cached_formats = [Mock(name="cached_fmt")]
        self._seed_cache(registry, agent, age_seconds=60, formats=cached_formats)

        fetch_mock = AsyncMock(side_effect=AssertionError("should not be called on fresh cache"))
        monkeypatch.setattr(registry, "_fetch_formats_from_agent", fetch_mock)

        result = await self._call_helper(registry, agent)

        assert result.formats is cached_formats
        assert result.stale is False
        assert result.cause is None
        fetch_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_expired_cache_with_fetch_failure_serves_stale(self, monkeypatch):
        """Cache expired, fetch fails → stale cache served with cause + age."""
        registry = CreativeAgentRegistry()
        agent = self._agent()
        cached_formats = [Mock(name="cached_fmt")]
        # 2 hours old → expired (default TTL 3600s)
        self._seed_cache(registry, agent, age_seconds=7200, formats=cached_formats)

        boom = RuntimeError("agent flaky")
        monkeypatch.setattr(registry, "_fetch_formats_from_agent", AsyncMock(side_effect=boom))

        result = await self._call_helper(registry, agent)

        assert result.formats is cached_formats
        assert result.stale is True
        assert result.cause is boom
        assert result.cache_age_seconds is not None and result.cache_age_seconds >= 7200

    @pytest.mark.asyncio
    async def test_no_cache_fetch_failure_reraises(self, monkeypatch):
        """No prior cache + fetch failure → re-raise (caller emits AGENT_UNREACHABLE)."""
        registry = CreativeAgentRegistry()
        agent = self._agent()

        boom = RuntimeError("agent down")
        monkeypatch.setattr(registry, "_fetch_formats_from_agent", AsyncMock(side_effect=boom))

        with pytest.raises(RuntimeError, match="agent down"):
            await self._call_helper(registry, agent)

    @pytest.mark.asyncio
    async def test_filtered_fetch_failure_skips_stale_fallback(self, monkeypatch):
        """Filters are active → cache is not consulted on failure (re-raise).

        Cache only stores the unfiltered full result; filtered results aren't cached,
        so a filtered request with a stale fallback could return the wrong subset.
        """
        registry = CreativeAgentRegistry()
        agent = self._agent()
        cached_formats = [Mock(name="cached_fmt")]
        self._seed_cache(registry, agent, age_seconds=7200, formats=cached_formats)

        boom = RuntimeError("agent flaky")
        monkeypatch.setattr(registry, "_fetch_formats_from_agent", AsyncMock(side_effect=boom))

        with pytest.raises(RuntimeError, match="agent flaky"):
            await self._call_helper(registry, agent, has_filters=True)

    @pytest.mark.asyncio
    async def test_successful_fetch_refreshes_cache(self, monkeypatch):
        """Successful fetch updates the cache for next call."""
        registry = CreativeAgentRegistry()
        agent = self._agent()
        # Seed expired cache to force fetch
        self._seed_cache(registry, agent, age_seconds=7200, formats=[Mock(name="old")])

        fresh_formats = [Mock(name="fresh")]
        monkeypatch.setattr(registry, "_fetch_formats_from_agent", AsyncMock(return_value=fresh_formats))

        result = await self._call_helper(registry, agent)

        assert result.formats == fresh_formats
        assert result.stale is False
        # Cache should now hold the fresh formats
        key = registry._cache_key(agent.agent_url)
        assert registry._format_cache[key].formats == fresh_formats

    @pytest.mark.asyncio
    async def test_list_all_formats_with_errors_emits_stale_response_warning(self, monkeypatch):
        """list_all_formats_with_errors surfaces stale fallback as STALE_RESPONSE warning,
        not AGENT_UNREACHABLE — and still includes the cached formats in the response.
        """
        registry = CreativeAgentRegistry()
        # Patch out client construction — _fetch_formats_from_agent is mocked anyway.
        registry._build_adcp_client = lambda _agents: Mock()  # type: ignore[assignment]

        # Only the default agent in scope (no tenant agents)
        default_agent = registry.DEFAULT_AGENT
        cached_formats = [Mock(name="cached_fmt", spec=[])]
        self._seed_cache(registry, default_agent, age_seconds=7200, formats=cached_formats)

        boom = RuntimeError("upstream 503")
        monkeypatch.setattr(registry, "_fetch_formats_from_agent", AsyncMock(side_effect=boom))

        result = await registry.list_all_formats_with_errors(tenant_id=None)

        assert result.formats == cached_formats, "stale cache must populate formats[]"
        assert len(result.errors) == 1
        err = result.errors[0]
        assert err.code == "STALE_RESPONSE"
        assert err.recovery.value == "transient"
        assert err.details is not None
        assert err.details["served_from_cache"] is True
        assert err.details["cache_age_seconds"] >= 7200
        assert err.details["agent_url"] == str(default_agent.agent_url)

    @pytest.mark.asyncio
    async def test_list_all_formats_with_errors_falls_back_to_agent_unreachable(self, monkeypatch):
        """No usable cache + fetch failure → AGENT_UNREACHABLE (existing behavior preserved)."""
        registry = CreativeAgentRegistry()
        registry._build_adcp_client = lambda _agents: Mock()  # type: ignore[assignment]

        boom = RuntimeError("upstream 503")
        monkeypatch.setattr(registry, "_fetch_formats_from_agent", AsyncMock(side_effect=boom))

        result = await registry.list_all_formats_with_errors(tenant_id=None)

        assert result.formats == []
        assert len(result.errors) == 1
        assert result.errors[0].code == "AGENT_UNREACHABLE"


class TestListAllFormatsParallelFetch:
    """Regression tests for the /api/formats/list 503 incident.

    Before the fix, list_all_formats iterated agents sequentially with no
    global timeout — N agents × per-agent timeout = total wall time, which
    exceeded the upstream LB timeout and surfaced as 503.

    These tests pin the contract: parallel fetch, bounded total time,
    partial results on per-agent failure or timeout.
    """

    @pytest.fixture(autouse=True)
    def _disable_testing_mode(self, monkeypatch):
        # ADCP_TESTING short-circuits to mock formats and skips the gather path entirely.
        monkeypatch.delenv("ADCP_TESTING", raising=False)

    @staticmethod
    def _patch_agents(registry, agents):
        registry._get_tenant_agents = lambda tenant_id=None: agents  # type: ignore[assignment]
        # _build_adcp_client constructs a real ADCPMultiAgentClient (network setup
        # is slow enough on these synthetic URLs to swamp the timing assertions).
        # Patch it out — _fetch_formats_from_agent is mocked anyway, so the client
        # is never used.
        registry._build_adcp_client = lambda _agents: Mock()  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_agents_are_fetched_in_parallel_not_serially(self, monkeypatch):
        """Two slow agents must complete in ~max(t), not sum(t).

        Pins the gather (vs for-loop) implementation. If someone reintroduces
        a serial loop, this test catches it.
        """
        import time

        registry = CreativeAgentRegistry()
        agents = [
            CreativeAgent(agent_url=f"https://agent-{i}.example.com", name=f"Agent {i}", enabled=True) for i in range(3)
        ]
        self._patch_agents(registry, agents)

        per_agent_delay = 0.3

        async def slow_fetch(client, agent, **kwargs):
            await asyncio.sleep(per_agent_delay)
            return []

        monkeypatch.setattr(registry, "_fetch_formats_from_agent", slow_fetch)

        start = time.monotonic()
        result = await registry.list_all_formats_with_errors(tenant_id="t1")
        elapsed = time.monotonic() - start

        assert result.errors == []
        # Serial would be 3 * 0.3 = 0.9s. Parallel should be ~0.3s.
        # Allow generous headroom for slow CI (still well under serial worst case).
        assert elapsed < per_agent_delay * 2, (
            f"Expected parallel fetch (~{per_agent_delay}s), got {elapsed:.2f}s — likely serial"
        )

    @pytest.mark.asyncio
    async def test_slow_agent_is_capped_and_surfaces_as_unreachable(self, monkeypatch):
        """An agent that exceeds CREATIVE_FORMAT_FETCH_TIMEOUT becomes AGENT_UNREACHABLE.

        Pins the global wait timeout. Without it, one slow agent blocks the
        whole request past the upstream LB timeout (the original 503 cause).
        """
        monkeypatch.setenv("CREATIVE_FORMAT_FETCH_TIMEOUT", "0.2")

        registry = CreativeAgentRegistry()
        fast = CreativeAgent(agent_url="https://fast.example.com", name="Fast", enabled=True)
        slow = CreativeAgent(agent_url="https://slow.example.com", name="Slow", enabled=True)
        self._patch_agents(registry, [fast, slow])

        async def fetch(client, agent, **kwargs):
            if agent.name == "Slow":
                await asyncio.sleep(5.0)  # well past the 0.2s cap
            return []

        monkeypatch.setattr(registry, "_fetch_formats_from_agent", fetch)

        result = await registry.list_all_formats_with_errors(tenant_id="t1")

        # Fast agent's empty list is a successful response, not an error.
        assert len(result.errors) == 1
        assert result.errors[0].code == "AGENT_UNREACHABLE"
        assert "slow.example.com" in result.errors[0].message
        assert "0.2s" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_no_agents_returns_empty_result_not_crash(self, monkeypatch):
        """A tenant with zero enabled agents must not crash asyncio.wait.

        Regression: asyncio.wait([]) raises ValueError. The for-loop didn't have
        this problem; gather/wait does. Code review caught this.
        """
        registry = CreativeAgentRegistry()
        self._patch_agents(registry, [])

        result = await registry.list_all_formats_with_errors(tenant_id="t1")

        assert result.formats == []
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_one_failing_agent_does_not_block_others(self, monkeypatch):
        """A raising agent must surface as AGENT_UNREACHABLE while siblings return formats."""
        registry = CreativeAgentRegistry()
        good = CreativeAgent(agent_url="https://good.example.com", name="Good", enabled=True)
        bad = CreativeAgent(agent_url="https://bad.example.com", name="Bad", enabled=True)
        self._patch_agents(registry, [good, bad])

        from tests.factories import FormatFactory

        good_format = FormatFactory(format_id__id="display_300x250_image")

        async def fetch(client, agent, **kwargs):
            if agent.name == "Bad":
                raise RuntimeError("Connection refused")
            return [good_format]

        monkeypatch.setattr(registry, "_fetch_formats_from_agent", fetch)

        result = await registry.list_all_formats_with_errors(tenant_id="t1")

        assert len(result.formats) == 1
        assert result.formats[0].format_id.id == "display_300x250_image"
        assert len(result.errors) == 1
        assert result.errors[0].code == "AGENT_UNREACHABLE"
        assert "bad.example.com" in result.errors[0].message
        assert "Connection refused" in result.errors[0].message


class TestResolveFetchTimeout:
    """The CREATIVE_FORMAT_FETCH_TIMEOUT env var must degrade gracefully.

    Operator-controlled (not tenant), but a typo should fall back to the
    default — not 500 the route or cause asyncio.wait to behave pathologically.
    """

    def test_unset_returns_default(self, monkeypatch):
        from src.core.creative_agent_registry import _DEFAULT_FETCH_TIMEOUT_SECONDS, _resolve_fetch_timeout

        monkeypatch.delenv("CREATIVE_FORMAT_FETCH_TIMEOUT", raising=False)
        assert _resolve_fetch_timeout() == _DEFAULT_FETCH_TIMEOUT_SECONDS

    def test_valid_float_string(self, monkeypatch):
        from src.core.creative_agent_registry import _resolve_fetch_timeout

        monkeypatch.setenv("CREATIVE_FORMAT_FETCH_TIMEOUT", "5.5")
        assert _resolve_fetch_timeout() == 5.5

    def test_non_numeric_falls_back_to_default(self, monkeypatch):
        from src.core.creative_agent_registry import _DEFAULT_FETCH_TIMEOUT_SECONDS, _resolve_fetch_timeout

        monkeypatch.setenv("CREATIVE_FORMAT_FETCH_TIMEOUT", "30s")  # common typo
        assert _resolve_fetch_timeout() == _DEFAULT_FETCH_TIMEOUT_SECONDS

    def test_zero_or_negative_clamped_to_minimum(self, monkeypatch):
        from src.core.creative_agent_registry import _MIN_FETCH_TIMEOUT_SECONDS, _resolve_fetch_timeout

        monkeypatch.setenv("CREATIVE_FORMAT_FETCH_TIMEOUT", "0")
        assert _resolve_fetch_timeout() == _MIN_FETCH_TIMEOUT_SECONDS

        monkeypatch.setenv("CREATIVE_FORMAT_FETCH_TIMEOUT", "-5")
        assert _resolve_fetch_timeout() == _MIN_FETCH_TIMEOUT_SECONDS

    def test_nan_clamped_to_minimum(self, monkeypatch):
        from src.core.creative_agent_registry import _MIN_FETCH_TIMEOUT_SECONDS, _resolve_fetch_timeout

        monkeypatch.setenv("CREATIVE_FORMAT_FETCH_TIMEOUT", "nan")
        assert _resolve_fetch_timeout() == _MIN_FETCH_TIMEOUT_SECONDS
