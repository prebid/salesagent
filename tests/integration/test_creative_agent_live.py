"""Integration tests against live creative agent.

These tests call the real creative agent at https://creative.adcontextprotocol.org
to verify format discovery and resolution works correctly.

The creative agent is stable production infrastructure that doesn't change frequently,
making it suitable for integration testing.

NOTE: The conftest test_environment fixture sets ADCP_TESTING=true globally,
which enables mock mode. These tests override it back to false via the
enable_live_mode fixture so they call the real creative agent.
"""

import pytest

from src.core.creative_agent_registry import CreativeAgent, CreativeAgentRegistry

# The live creative agent URL
CREATIVE_AGENT_URL = "https://creative.adcontextprotocol.org"
CREATIVE_AGENT_URL_WITH_SLASH = "https://creative.adcontextprotocol.org/"


@pytest.fixture(autouse=True)
def enable_live_mode(monkeypatch):
    """Override ADCP_TESTING for live creative agent tests.

    The conftest test_environment fixture sets ADCP_TESTING=true globally,
    which enables mock mode. These tests intentionally call the real creative
    agent, so we override it back to false.
    """
    monkeypatch.setenv("ADCP_TESTING", "false")


@pytest.fixture
def registry():
    """Fresh registry instance for each test."""
    return CreativeAgentRegistry()


class TestCreativeAgentLiveConnection:
    """Test live connection to creative agent."""

    @pytest.mark.asyncio
    async def test_can_fetch_formats_from_creative_agent(self, registry):
        """Verify we can fetch formats from the live creative agent."""
        formats = await registry.list_all_formats(tenant_id=None)

        assert len(formats) > 0, "Should return at least one format"
        # Creative agent has ~49 formats as of this writing
        assert len(formats) >= 10, f"Expected many formats, got {len(formats)}"

    @pytest.mark.asyncio
    async def test_formats_have_required_fields(self, registry):
        """Verify returned formats have all required fields."""
        formats = await registry.list_all_formats(tenant_id=None)

        for fmt in formats[:5]:  # Check first 5
            assert fmt.format_id is not None, "format_id required"
            assert fmt.format_id.id is not None, "format_id.id required"
            assert fmt.format_id.agent_url is not None, "format_id.agent_url required"
            assert fmt.name is not None, "name required"
            assert fmt.type is not None, "type required"


class TestDisplayImageFormat:
    """Test the display_image parameterized format specifically."""

    @pytest.mark.asyncio
    async def test_display_image_format_exists(self, registry):
        """Verify display_image format is returned by creative agent."""
        formats = await registry.list_all_formats(tenant_id=None)

        format_ids = [fmt.format_id.id for fmt in formats]
        assert "display_image" in format_ids, f"display_image not found in formats. Available: {format_ids[:20]}..."

    @pytest.mark.asyncio
    async def test_display_image_has_correct_type(self, registry):
        """Verify display_image has type 'display'."""
        formats = await registry.list_all_formats(tenant_id=None)

        display_image = next((fmt for fmt in formats if fmt.format_id.id == "display_image"), None)
        assert display_image is not None, "display_image format not found"

        # Type might be enum or string
        type_value = display_image.type.value if hasattr(display_image.type, "value") else str(display_image.type)
        assert type_value == "display", f"Expected type 'display', got '{type_value}'"

    @pytest.mark.asyncio
    async def test_can_get_display_image_by_id(self, registry):
        """Verify we can look up display_image format directly."""
        fmt = await registry.get_format(CREATIVE_AGENT_URL, "display_image")

        assert fmt is not None, "get_format should return display_image"
        assert fmt.format_id.id == "display_image"

    @pytest.mark.asyncio
    async def test_all_parameterized_display_formats_exist(self, registry):
        """Verify all parameterized display formats from UI exist."""
        formats = await registry.list_all_formats(tenant_id=None)
        format_ids = {fmt.format_id.id for fmt in formats}

        # These are the formats the UI's FORMAT_TEMPLATES expects
        expected_formats = ["display_image", "display_html", "display_js"]

        missing = [f for f in expected_formats if f not in format_ids]
        assert not missing, f"Missing expected formats: {missing}"

    @pytest.mark.asyncio
    async def test_video_parameterized_formats_exist(self, registry):
        """Verify parameterized video formats exist."""
        formats = await registry.list_all_formats(tenant_id=None)
        format_ids = {fmt.format_id.id for fmt in formats}

        expected_formats = ["video_standard", "video_vast"]

        missing = [f for f in expected_formats if f not in format_ids]
        assert not missing, f"Missing expected video formats: {missing}"


class TestURLNormalization:
    """Test URL handling with trailing slashes."""

    @pytest.mark.asyncio
    async def test_fetch_formats_without_trailing_slash(self, registry):
        """Fetch formats using URL without trailing slash."""
        agent = CreativeAgent(
            agent_url=CREATIVE_AGENT_URL,  # No trailing slash
            name="Test Agent",
            enabled=True,
        )

        formats = await registry.get_formats_for_agent(agent)
        assert len(formats) > 0, "Should return formats without trailing slash"

    @pytest.mark.asyncio
    async def test_fetch_formats_with_trailing_slash(self, registry):
        """Fetch formats using URL with trailing slash."""
        agent = CreativeAgent(
            agent_url=CREATIVE_AGENT_URL_WITH_SLASH,  # With trailing slash
            name="Test Agent",
            enabled=True,
        )

        formats = await registry.get_formats_for_agent(agent)
        assert len(formats) > 0, "Should return formats with trailing slash"

    @pytest.mark.asyncio
    async def test_get_format_without_trailing_slash(self, registry):
        """Get specific format using URL without trailing slash."""
        fmt = await registry.get_format(CREATIVE_AGENT_URL, "display_image")
        assert fmt is not None, "Should find display_image without trailing slash"

    @pytest.mark.asyncio
    async def test_get_format_with_trailing_slash(self, registry):
        """Get specific format using URL with trailing slash."""
        fmt = await registry.get_format(CREATIVE_AGENT_URL_WITH_SLASH, "display_image")
        assert fmt is not None, "Should find display_image with trailing slash"

    @pytest.mark.asyncio
    async def test_agent_url_in_format_response(self, registry):
        """Check what agent_url the creative agent returns in format_id."""
        formats = await registry.list_all_formats(tenant_id=None)

        # Find any format and check its agent_url
        fmt = formats[0]
        agent_url = str(fmt.format_id.agent_url)

        # Document what the creative agent actually returns
        print(f"Creative agent returns agent_url: '{agent_url}'")
        print(f"Has trailing slash: {agent_url.endswith('/')}")

        # This test documents the behavior - the creative agent returns URLs with trailing slash
        assert agent_url.startswith("https://creative.adcontextprotocol.org")


class TestCacheConsistency:
    """Test cache behavior with URL variations."""

    @pytest.mark.asyncio
    async def test_cache_works_after_first_fetch(self, registry):
        """Verify cache is populated and used on second fetch."""
        # First fetch - should call creative agent
        formats1 = await registry.list_all_formats(tenant_id=None)

        # Check cache is populated
        assert len(registry._format_cache) > 0, "Cache should be populated"

        # Second fetch - should use cache
        formats2 = await registry.list_all_formats(tenant_id=None)

        # Should return same formats
        assert len(formats1) == len(formats2)

    @pytest.mark.asyncio
    async def test_cache_key_matches_default_agent(self, registry):
        """Verify cache key matches DEFAULT_AGENT URL."""
        # Fetch formats (populates cache)
        await registry.list_all_formats(tenant_id=None)

        # DEFAULT_AGENT uses URL without trailing slash
        expected_key = CREATIVE_AGENT_URL  # No trailing slash

        cache_keys = list(registry._format_cache.keys())
        print(f"Cache keys: {cache_keys}")

        assert expected_key in cache_keys, f"Expected cache key '{expected_key}' not found. Keys: {cache_keys}"

    @pytest.mark.asyncio
    async def test_url_variations_share_single_cache_entry(self, registry):
        """URL with and without trailing slash share the same cache entry."""
        # Fetch with no trailing slash (DEFAULT_AGENT style)
        agent_no_slash = CreativeAgent(
            agent_url=CREATIVE_AGENT_URL,
            name="No Slash",
            enabled=True,
        )
        await registry.get_formats_for_agent(agent_no_slash)
        cache_after_no_slash = len(registry._format_cache)

        # Fetch with trailing slash — should hit cache, not create new entry
        agent_with_slash = CreativeAgent(
            agent_url=CREATIVE_AGENT_URL_WITH_SLASH,
            name="With Slash",
            enabled=True,
        )
        await registry.get_formats_for_agent(agent_with_slash)
        cache_after_with_slash = len(registry._format_cache)

        assert cache_after_with_slash == cache_after_no_slash, (
            f"Expected 1 cache entry, got {cache_after_with_slash}. Keys: {list(registry._format_cache.keys())}"
        )


class TestFormatResolverIntegration:
    """Test format_resolver with real creative agent."""

    def test_get_format_without_slash(self):
        """Test format_resolver.get_format with URL without trailing slash."""
        from src.core.format_resolver import get_format

        fmt = get_format(
            format_id="display_image",
            agent_url=CREATIVE_AGENT_URL,
            tenant_id=None,
            product_id=None,
        )

        assert fmt is not None, "Should find display_image via format_resolver"
        assert fmt.format_id.id == "display_image"

    def test_get_format_with_trailing_slash(self):
        """Test format_resolver.get_format with URL with trailing slash."""
        from src.core.format_resolver import get_format

        fmt = get_format(
            format_id="display_image",
            agent_url=CREATIVE_AGENT_URL_WITH_SLASH,
            tenant_id=None,
            product_id=None,
        )

        assert fmt is not None, "Should find display_image with trailing slash"
        assert fmt.format_id.id == "display_image"

    def test_get_format_error_for_nonexistent(self):
        """Test format_resolver raises clear error for nonexistent format."""
        from src.core.format_resolver import get_format

        with pytest.raises(ValueError) as exc_info:
            get_format(
                format_id="nonexistent_format_xyz",
                agent_url=CREATIVE_AGENT_URL,
                tenant_id=None,
                product_id=None,
            )

        error_msg = str(exc_info.value)
        assert "Unknown format_id" in error_msg
        assert "nonexistent_format_xyz" in error_msg
