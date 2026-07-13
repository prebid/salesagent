"""Unit tests for Creative Agent Registry adcp library integration."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import AnyUrl

from src.core.creative_agent_registry import (
    _KNOWN_ASSET_TYPES,
    CreativeAgent,
    CreativeAgentRegistry,
    _get_mock_formats,
)
from src.core.exceptions import AdCPAdapterError, AdCPAuthenticationError, AdCPServiceUnavailableError


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
        """get_format must not crash when agent_url is AnyUrl (GAM line item path)."""
        monkeypatch.delenv("ADCP_TESTING", raising=False)
        registry = CreativeAgentRegistry()

        # Patch _fetch to avoid real HTTP — we only test the cache_key path
        async def mock_fetch(*args, **kwargs):
            return []

        monkeypatch.setattr(registry, "_fetch_formats_from_agent", mock_fetch)

        result = await registry.get_format(AnyUrl("https://creative.adcontextprotocol.org/"), "display_300x250_image")
        assert result is None  # Not found, but no TypeError


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

        # Should re-raise as typed src.core.AdCPAuthenticationError (wrapped)
        with pytest.raises(AdCPAuthenticationError, match="Authentication failed"):
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

        # Should raise typed AdCPServiceUnavailableError with timeout message
        with pytest.raises(AdCPServiceUnavailableError, match="Request timed out"):
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

        # Should raise typed AdCPServiceUnavailableError
        with pytest.raises(AdCPServiceUnavailableError, match="Connection failed"):
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


class TestKnownAssetTypes:
    """_KNOWN_ASSET_TYPES includes 'url' (Change 4).

    AdCP 3.1 adds 'url' as a valid asset type for text_ad_search formats.
    The tolerant ingestion must not reject formats that use 'url' assets.
    """

    def test_url_in_known_asset_types(self):
        """'url' must be in _KNOWN_ASSET_TYPES after Change 4."""
        assert "url" in _KNOWN_ASSET_TYPES, (
            "'url' must be in _KNOWN_ASSET_TYPES so formats with url assets "
            "are not rejected by the tolerant ingestion path"
        )

    def test_known_asset_types_is_frozenset(self):
        """_KNOWN_ASSET_TYPES must be a frozenset (immutable, hashable)."""
        assert isinstance(_KNOWN_ASSET_TYPES, frozenset), (
            "_KNOWN_ASSET_TYPES must be a frozenset so it cannot be mutated at runtime"
        )

    def test_known_asset_types_derived_from_enum(self):
        """_KNOWN_ASSET_TYPES must include image, video, and text from the AssetContentType enum.

        The pre-5.7 annotation-walk over Format.assets collected nothing under the
        Annotated[…, Discriminator] shape introduced in adcp 5.7.  The fix derives
        from the enum directly so the set is never silently empty.
        """
        assert "image" in _KNOWN_ASSET_TYPES, (
            "'image' must be in _KNOWN_ASSET_TYPES — derivation from AssetContentType enum is broken"
        )
        assert "video" in _KNOWN_ASSET_TYPES, (
            "'video' must be in _KNOWN_ASSET_TYPES — derivation from AssetContentType enum is broken"
        )
        assert "text" in _KNOWN_ASSET_TYPES, (
            "'text' must be in _KNOWN_ASSET_TYPES — derivation from AssetContentType enum is broken"
        )

    def test_zip_in_known_asset_types(self):
        """'zip' must be in _KNOWN_ASSET_TYPES.

        'zip' is a valid asset_type Literal on the SDK's individual-asset shapes
        (Assets32/Assets43 in the generated union) but is absent from the
        AssetContentType response enum, so deriving _KNOWN_ASSET_TYPES from the
        enum alone silently drops it.
        """
        assert "zip" in _KNOWN_ASSET_TYPES, (
            "'zip' must be in _KNOWN_ASSET_TYPES — it's a real SDK asset_type Literal "
            "not covered by the AssetContentType enum"
        )

    def test_card_in_known_asset_types(self):
        """'card' must be in _KNOWN_ASSET_TYPES.

        'card' is the asset_type discriminator for RepeatableAssetGroup member
        assets (CardAsset) but is absent from the AssetContentType response enum.
        """
        assert "card" in _KNOWN_ASSET_TYPES, (
            "'card' must be in _KNOWN_ASSET_TYPES — it's a real SDK asset_type Literal "
            "not covered by the AssetContentType enum"
        )

    def test_known_asset_types_covers_full_sdk_union(self):
        """_KNOWN_ASSET_TYPES must match all 16 asset_type Literals the SDK's
        Format.assets discriminated union actually accepts (14 from
        AssetContentType + zip + card), not just the 14-member response enum.
        """
        expected = {
            "image",
            "video",
            "audio",
            "text",
            "markdown",
            "html",
            "css",
            "javascript",
            "vast",
            "daast",
            "url",
            "webhook",
            "brief",
            "catalog",
            "zip",
            "card",
        }
        assert _KNOWN_ASSET_TYPES == expected, (
            f"_KNOWN_ASSET_TYPES drifted from the SDK's full asset_type union: "
            f"missing={expected - _KNOWN_ASSET_TYPES}, extra={_KNOWN_ASSET_TYPES - expected}"
        )

    def test_text_ad_search_mock_format_present(self):
        """text_ad_search mock format must be in _get_mock_formats() (Change 4)."""
        mock_formats = _get_mock_formats()
        format_ids = {fmt.format_id.id for fmt in mock_formats}
        assert "text_ad_search" in format_ids, "text_ad_search mock format must be registered for testing mode"

    def test_text_ad_search_format_has_assets(self):
        """text_ad_search mock format must have assets defined."""
        mock_formats = _get_mock_formats()
        text_ad_search = next((fmt for fmt in mock_formats if fmt.format_id.id == "text_ad_search"), None)
        assert text_ad_search is not None
        assert text_ad_search.assets is not None
        assert len(text_ad_search.assets) > 0, "text_ad_search must have at least one asset slot"


class TestBuildCreativeUsesADCPClient:
    """build_creative uses ADCPMultiAgentClient + BuildCreativeRequest (Change 3).

    Verifies that:
    - gemini_api_key is NOT a parameter (removed in Change 3)
    - ADCPMultiAgentClient is used for the call
    - BuildCreativeRequest is constructed with target_format_id and idempotency_key
    - brand string is converted to BrandRef dict before the request
    - Result is returned as a plain dict
    """

    @pytest.mark.asyncio
    async def test_build_creative_no_gemini_api_key_param(self):
        """build_creative must NOT accept gemini_api_key parameter (Change 3)."""
        import inspect

        registry = CreativeAgentRegistry()
        sig = inspect.signature(registry.build_creative)
        assert "gemini_api_key" not in sig.parameters, (
            "build_creative must not accept gemini_api_key — "
            "Change 3 removed this dependency in favour of ADCPMultiAgentClient"
        )

    @pytest.mark.asyncio
    async def test_build_creative_uses_adcp_multi_agent_client(self):
        """build_creative must use ADCPMultiAgentClient, not raw MCP client."""
        registry = CreativeAgentRegistry()

        mock_result = Mock()
        mock_result.model_dump = Mock(return_value={"status": "draft", "context_id": "ctx-1"})

        mock_agent_client = Mock()
        mock_agent_client.build_creative = AsyncMock(return_value=mock_result)

        mock_adcp_client = Mock()
        mock_adcp_client.agent = Mock(return_value=mock_agent_client)

        with patch.object(registry, "_build_adcp_client", return_value=mock_adcp_client) as mock_build:
            result = await registry.build_creative(
                agent_url="https://creative.example.com",
                format_id="display_300x250_generative",
                message="Build a banner ad",
            )

        # _build_adcp_client must have been called with a list of CreativeAgent objects
        from unittest.mock import ANY

        mock_build.assert_called_once_with(ANY)
        # build_creative on the agent client must have been called with a BuildCreativeRequest
        mock_agent_client.build_creative.assert_called_once_with(ANY)
        # Result must be a plain dict
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_build_creative_passes_idempotency_key(self):
        """build_creative must pass idempotency_key in the BuildCreativeRequest."""
        from adcp import BuildCreativeRequest

        registry = CreativeAgentRegistry()

        captured_request: list[BuildCreativeRequest] = []

        async def capture_build(request):
            captured_request.append(request)
            return {"status": "draft"}

        mock_agent_client = Mock()
        mock_agent_client.build_creative = capture_build

        mock_adcp_client = Mock()
        mock_adcp_client.agent = Mock(return_value=mock_agent_client)

        with patch.object(registry, "_build_adcp_client", return_value=mock_adcp_client):
            await registry.build_creative(
                agent_url="https://creative.example.com",
                format_id="display_300x250_generative",
                message="Build a banner ad",
            )

        assert len(captured_request) == 1
        req = captured_request[0]
        assert req.idempotency_key is not None, (
            "BuildCreativeRequest must include idempotency_key (required by AdCP 3.1)"
        )
        assert len(req.idempotency_key) > 0

    @pytest.mark.asyncio
    async def test_build_creative_brand_str_converted_to_ref(self):
        """build_creative converts brand string to typed BrandReference before the request."""
        from adcp import BuildCreativeRequest
        from adcp.types import BrandReference

        registry = CreativeAgentRegistry()

        captured_request: list[BuildCreativeRequest] = []

        async def capture_build(request):
            captured_request.append(request)
            return {"status": "draft"}

        mock_agent_client = Mock()
        mock_agent_client.build_creative = capture_build

        mock_adcp_client = Mock()
        mock_adcp_client.agent = Mock(return_value=mock_agent_client)

        with patch.object(registry, "_build_adcp_client", return_value=mock_adcp_client):
            await registry.build_creative(
                agent_url="https://creative.example.com",
                format_id="display_300x250_generative",
                message="Build a banner ad",
                brand="https://advertiser.example.com/brand",
            )

        assert len(captured_request) == 1
        req = captured_request[0]
        # brand must be a typed BrandReference, not a raw string or dict
        assert req.brand is not None, "brand must be forwarded to BuildCreativeRequest"
        assert isinstance(req.brand, BrandReference), "brand must be a typed BrandReference (not a raw string or dict)"
        assert req.brand.domain == "advertiser.example.com"

    @pytest.mark.asyncio
    async def test_build_creative_returns_dict(self):
        """build_creative always returns a plain dict (not a Pydantic model)."""
        registry = CreativeAgentRegistry()

        mock_result = Mock()
        mock_result.model_dump = Mock(return_value={"status": "draft", "context_id": "ctx-abc"})

        mock_agent_client = Mock()
        mock_agent_client.build_creative = AsyncMock(return_value=mock_result)

        mock_adcp_client = Mock()
        mock_adcp_client.agent = Mock(return_value=mock_agent_client)

        with patch.object(registry, "_build_adcp_client", return_value=mock_adcp_client):
            result = await registry.build_creative(
                agent_url="https://creative.example.com",
                format_id="display_300x250_generative",
                message="Build a banner ad",
            )

        assert isinstance(result, dict), "build_creative must return a plain dict for downstream processing"
        assert result.get("status") == "draft"

    @pytest.mark.asyncio
    async def test_build_creative_translates_auth_error_to_terminal(self):
        """build_creative must translate ADCPAuthenticationError to a terminal AdCPAuthenticationError.

        Mirrors _fetch_formats_from_agent's translation via raise_mapped_adcp_error:
        an SDK auth failure must not fall through to a blanket except that would
        classify it as retryable "transient" — rejected credentials are terminal.
        """
        from adcp.exceptions import ADCPAuthenticationError

        registry = CreativeAgentRegistry()

        mock_agent_client = Mock()
        mock_agent_client.build_creative = AsyncMock(
            side_effect=ADCPAuthenticationError("invalid credentials", agent_id="agent-1")
        )

        mock_adcp_client = Mock()
        mock_adcp_client.agent = Mock(return_value=mock_agent_client)

        with patch.object(registry, "_build_adcp_client", return_value=mock_adcp_client):
            with pytest.raises(AdCPAuthenticationError) as exc_info:
                await registry.build_creative(
                    agent_url="https://creative.example.com",
                    format_id="display_300x250_generative",
                    message="Build a banner ad",
                )

        assert exc_info.value.recovery == "terminal", (
            "Rejected credentials must be terminal — retrying a rejected auth token loops forever"
        )

    @pytest.mark.asyncio
    async def test_build_creative_translates_timeout_error_to_transient(self):
        """build_creative must translate ADCPTimeoutError to a transient AdCPServiceUnavailableError."""
        from adcp.exceptions import ADCPTimeoutError

        registry = CreativeAgentRegistry()

        mock_agent_client = Mock()
        mock_agent_client.build_creative = AsyncMock(
            side_effect=ADCPTimeoutError("request timed out", agent_id="agent-1")
        )

        mock_adcp_client = Mock()
        mock_adcp_client.agent = Mock(return_value=mock_agent_client)

        with patch.object(registry, "_build_adcp_client", return_value=mock_adcp_client):
            with pytest.raises(AdCPServiceUnavailableError) as exc_info:
                await registry.build_creative(
                    agent_url="https://creative.example.com",
                    format_id="display_300x250_generative",
                    message="Build a banner ad",
                )

        assert exc_info.value.recovery == "transient", "A timeout may succeed on retry — recovery must be transient"


class TestBuildCreativeManifestValidation:
    """build_creative validates creative_manifest via model_validate (not model_construct).

    Covers the review's "untested strictness" gap: a realistic complete manifest
    must pass through to the request, and a realistic partial/malformed manifest
    (missing required asset fields) must raise rather than silently forward a
    broken manifest to the creative agent.
    """

    @pytest.mark.asyncio
    async def test_realistic_complete_manifest_forwarded(self):
        """A complete, valid creative_manifest dict is forwarded as a typed CreativeManifest."""
        from adcp import BuildCreativeRequest
        from adcp.types.generated_poc.core.creative_manifest import CreativeManifest

        registry = CreativeAgentRegistry()

        captured_request: list[BuildCreativeRequest] = []

        async def capture_build(request):
            captured_request.append(request)
            return {"status": "draft"}

        mock_agent_client = Mock()
        mock_agent_client.build_creative = capture_build

        mock_adcp_client = Mock()
        mock_adcp_client.agent = Mock(return_value=mock_agent_client)

        manifest_dict = {
            "format_id": {"id": "display_300x250", "agent_url": "https://creative.example.com"},
            "assets": {
                "main_image": {
                    "asset_type": "image",
                    "url": "https://example.com/img.png",
                    "width": 300,
                    "height": 250,
                }
            },
        }

        with patch.object(registry, "_build_adcp_client", return_value=mock_adcp_client):
            await registry.build_creative(
                agent_url="https://creative.example.com",
                format_id="display_300x250",
                message="Build a banner ad",
                creative_manifest=manifest_dict,
            )

        assert len(captured_request) == 1
        req = captured_request[0]
        assert req.creative_manifest is not None
        assert isinstance(req.creative_manifest, CreativeManifest), (
            "creative_manifest must be a typed CreativeManifest (validated, not constructed unchecked)"
        )

    @pytest.mark.asyncio
    async def test_realistic_partial_manifest_raises(self):
        """A partial manifest (image asset missing required width/height) raises rather than
        silently forwarding a broken manifest to the creative agent.

        model_validate() (not model_construct()) enforces the asset schema's field
        validators — this pins that strictness against silent regression to a lenient
        construction path.
        """
        from pydantic import ValidationError

        registry = CreativeAgentRegistry()

        mock_agent_client = Mock()
        mock_agent_client.build_creative = AsyncMock(return_value={"status": "draft"})

        mock_adcp_client = Mock()
        mock_adcp_client.agent = Mock(return_value=mock_agent_client)

        # Missing required width/height on the image asset.
        partial_manifest = {
            "format_id": {"id": "display_300x250", "agent_url": "https://creative.example.com"},
            "assets": {"main_image": {"asset_type": "image", "url": "https://example.com/img.png"}},
        }

        with patch.object(registry, "_build_adcp_client", return_value=mock_adcp_client):
            with pytest.raises(ValidationError):
                await registry.build_creative(
                    agent_url="https://creative.example.com",
                    format_id="display_300x250",
                    message="Build a banner ad",
                    creative_manifest=partial_manifest,
                )
