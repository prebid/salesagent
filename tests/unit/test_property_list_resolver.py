"""Unit tests for property list resolver with caching."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from adcp.types import PropertyListReference

from src.core.exceptions import AdCPAdapterError


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the property list cache before each test."""
    from src.core.property_list_resolver import clear_cache

    clear_cache()
    yield
    clear_cache()


@pytest.fixture(autouse=True)
def _stub_dns():
    """Stub DNS resolution so tests don't make real DNS lookups.

    Returns a public IP for all hostnames by default. Tests in
    TestSSRFProtection override this where needed.
    """
    with patch(
        "src.core.security.url_validator.socket.gethostbyname",
        return_value="93.184.216.34",
    ):
        yield


def _make_ref(
    agent_url: str = "https://example.com",
    list_id: str = "list-1",
    auth_token: str | None = "test-token",
) -> PropertyListReference:
    return PropertyListReference(
        agent_url=agent_url,
        list_id=list_id,
        auth_token=auth_token,
    )


def _make_response_json(
    identifiers: list[dict] | None = None,
    cache_valid_until: str | None = None,
) -> dict:
    """Build a raw JSON dict that matches GetPropertyListResponse shape."""
    result: dict = {
        "list": {
            "list_id": "list-1",
            "name": "Test List",
        },
    }
    if identifiers is not None:
        result["identifiers"] = identifiers
    if cache_valid_until is not None:
        result["cache_valid_until"] = cache_valid_until
    return result


def _make_mock_response(response_json: dict, status_code: int = 200) -> MagicMock:
    """Create a mock httpx.Response with sync .json() and .raise_for_status()."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = response_json
    return mock_response


def _make_mock_client(get_side_effect=None, get_return_value=None) -> AsyncMock:
    """Create a mock httpx.AsyncClient with proper async context manager."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    if get_side_effect is not None:
        mock_client.get = AsyncMock(side_effect=get_side_effect)
    elif get_return_value is not None:
        mock_client.get = AsyncMock(return_value=get_return_value)
    return mock_client


class TestResolvePropertyList:
    """Tests for resolve_property_list()."""

    @pytest.mark.asyncio
    async def test_successful_resolution(self):
        """Successful HTTP call returns identifier value strings."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref()
        response_json = _make_response_json(
            identifiers=[
                {"type": "domain", "value": "example.com"},
                {"type": "domain", "value": "test.org"},
            ],
        )
        mock_response = _make_mock_response(response_json)

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await resolve_property_list(ref)

        assert result == ["example.com", "test.org"]

    @pytest.mark.asyncio
    async def test_bearer_auth_header_sent(self):
        """When auth_token is present, Bearer header is sent."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref(auth_token="my-secret-token")
        response_json = _make_response_json(
            identifiers=[{"type": "domain", "value": "a.com"}],
        )
        mock_response = _make_mock_response(response_json)

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await resolve_property_list(ref)

        call_kwargs = mock_client.get.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer my-secret-token"

    @pytest.mark.asyncio
    async def test_no_auth_header_when_token_is_none(self):
        """When auth_token is None, no Authorization header is sent."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref(auth_token=None)
        response_json = _make_response_json(
            identifiers=[{"type": "domain", "value": "a.com"}],
        )
        mock_response = _make_mock_response(response_json)

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await resolve_property_list(ref)

        call_kwargs = mock_client.get.call_args
        assert "Authorization" not in call_kwargs.kwargs["headers"]

    @pytest.mark.asyncio
    async def test_correct_url_construction(self):
        """GET request is sent to {agent_url}/lists/{list_id}."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref(agent_url="https://agent.example.com", list_id="my-list-42")
        response_json = _make_response_json(
            identifiers=[{"type": "domain", "value": "a.com"}],
        )
        mock_response = _make_mock_response(response_json)

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await resolve_property_list(ref)

        call_args = mock_client.get.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url")
        assert url == "https://agent.example.com/lists/my-list-42"

    @pytest.mark.asyncio
    async def test_empty_identifiers_returns_empty_list(self):
        """When identifiers is None in response, return empty list."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref()
        response_json = _make_response_json(identifiers=None)
        mock_response = _make_mock_response(response_json)

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await resolve_property_list(ref)

        assert result == []


class TestCaching:
    """Tests for cache behavior."""

    @pytest.mark.asyncio
    async def test_cached_result_avoids_http_call(self):
        """Second call with same ref returns cached result, no HTTP."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref()
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        response_json = _make_response_json(
            identifiers=[{"type": "domain", "value": "cached.com"}],
            cache_valid_until=future,
        )
        mock_response = _make_mock_response(response_json)

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result1 = await resolve_property_list(ref)
            result2 = await resolve_property_list(ref)

        assert result1 == ["cached.com"]
        assert result2 == ["cached.com"]
        assert mock_client.get.call_count == 1  # Only one HTTP call

    @pytest.mark.asyncio
    async def test_expired_cache_causes_refetch(self):
        """Expired cache entry triggers a new HTTP call."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref()
        past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        response_json = _make_response_json(
            identifiers=[{"type": "domain", "value": "fresh.com"}],
            cache_valid_until=past,
        )
        mock_response = _make_mock_response(response_json)

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await resolve_property_list(ref)
            await resolve_property_list(ref)

        assert mock_client.get.call_count == 2  # Both calls hit HTTP

    @pytest.mark.asyncio
    async def test_no_cache_valid_until_uses_default_ttl(self):
        """When cache_valid_until is None, cache with default TTL."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref()
        response_json = _make_response_json(
            identifiers=[{"type": "domain", "value": "default-ttl.com"}],
            cache_valid_until=None,
        )
        mock_response = _make_mock_response(response_json)

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result1 = await resolve_property_list(ref)
            result2 = await resolve_property_list(ref)

        assert result1 == ["default-ttl.com"]
        assert result2 == ["default-ttl.com"]
        assert mock_client.get.call_count == 1  # Cached despite no cache_valid_until

    @pytest.mark.asyncio
    async def test_different_list_ids_cached_separately(self):
        """Different list_ids have separate cache entries."""
        from src.core.property_list_resolver import resolve_property_list

        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

        ref1 = _make_ref(list_id="list-a")
        ref2 = _make_ref(list_id="list-b")

        response_a = _make_response_json(
            identifiers=[{"type": "domain", "value": "a.com"}],
            cache_valid_until=future,
        )
        response_b = _make_response_json(
            identifiers=[{"type": "domain", "value": "b.com"}],
            cache_valid_until=future,
        )

        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 200
            if "list-a" in url:
                resp.json.return_value = response_a
            else:
                resp.json.return_value = response_b
            return resp

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client()
            mock_client.get = mock_get
            mock_client_cls.return_value = mock_client

            result_a = await resolve_property_list(ref1)
            result_b = await resolve_property_list(ref2)

        assert result_a == ["a.com"]
        assert result_b == ["b.com"]
        assert call_count == 2


class TestErrorHandling:
    """Tests for error wrapping -- core invariant: no raw httpx exceptions escape."""

    @pytest.mark.asyncio
    async def test_http_error_raises_adcp_adapter_error(self):
        """HTTP 4xx/5xx errors are wrapped in AdCPAdapterError."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref()

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found",
            request=httpx.Request("GET", "https://example.com/lists/list-1"),
            response=httpx.Response(404),
        )

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(AdCPAdapterError, match="property list"):
                await resolve_property_list(ref)

    @pytest.mark.asyncio
    async def test_timeout_raises_adcp_adapter_error(self):
        """httpx.TimeoutException is wrapped in AdCPAdapterError."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref()

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(
                get_side_effect=httpx.TimeoutException("timed out"),
            )
            mock_client_cls.return_value = mock_client

            with pytest.raises(AdCPAdapterError, match="timed out"):
                await resolve_property_list(ref)

    @pytest.mark.asyncio
    async def test_connection_error_raises_adcp_adapter_error(self):
        """httpx.ConnectError is wrapped in AdCPAdapterError."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref()

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(
                get_side_effect=httpx.ConnectError("connection refused"),
            )
            mock_client_cls.return_value = mock_client

            with pytest.raises(AdCPAdapterError, match="connection"):
                await resolve_property_list(ref)

    @pytest.mark.asyncio
    async def test_failed_requests_are_not_cached(self):
        """Failed HTTP calls should not populate the cache."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref()

        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        success_response_json = _make_response_json(
            identifiers=[{"type": "domain", "value": "success.com"}],
            cache_valid_until=future,
        )

        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.TimeoutException("timed out")
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = success_response_json
            return resp

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client()
            mock_client.get = mock_get
            mock_client_cls.return_value = mock_client

            with pytest.raises(AdCPAdapterError):
                await resolve_property_list(ref)

            # Second call should succeed (not return cached error)
            result = await resolve_property_list(ref)

        assert result == ["success.com"]
        assert call_count == 2


class TestSSRFProtection:
    """Tests for SSRF protection — buyer-supplied agent_url must be validated."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "malicious_url,description",
        [
            ("http://127.0.0.1:8080", "loopback IPv4 via HTTP"),
            ("http://localhost:9200", "localhost hostname via HTTP"),
            ("http://10.0.0.1/internal", "RFC1918 class A via HTTP"),
            ("http://172.16.0.1/internal", "RFC1918 class B via HTTP"),
            ("http://192.168.1.1/internal", "RFC1918 class C via HTTP"),
            ("http://169.254.169.254/latest/meta-data", "AWS metadata via HTTP"),
            ("http://metadata.google.internal", "GCP metadata via HTTP"),
            ("http://[::1]:8080", "loopback IPv6 via HTTP"),
        ],
    )
    async def test_rejects_http_scheme(self, malicious_url, description):
        """HTTP URLs must be rejected regardless of destination (HTTPS required)."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref(agent_url=malicious_url)

        with pytest.raises(AdCPAdapterError, match="HTTPS"):
            await resolve_property_list(ref)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "malicious_url,resolved_ip,description",
        [
            ("https://evil.com", "127.0.0.1", "DNS rebind to loopback"),
            ("https://evil.com", "10.0.0.1", "DNS rebind to RFC1918 class A"),
            ("https://evil.com", "172.16.0.1", "DNS rebind to RFC1918 class B"),
            ("https://evil.com", "192.168.1.1", "DNS rebind to RFC1918 class C"),
            ("https://evil.com", "169.254.169.254", "DNS rebind to link-local"),
        ],
    )
    async def test_rejects_private_ip_after_dns_resolution(self, malicious_url, resolved_ip, description):
        """HTTPS URLs that resolve to private/internal IPs must be rejected."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref(agent_url=malicious_url)

        with patch(
            "src.core.security.url_validator.socket.gethostbyname",
            return_value=resolved_ip,
        ):
            with pytest.raises(AdCPAdapterError, match="[Bb]locked|[Pp]rivate|[Ii]nternal"):
                await resolve_property_list(ref)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "hostname",
        ["localhost", "metadata.google.internal", "169.254.169.254"],
    )
    async def test_rejects_blocked_hostnames(self, hostname):
        """Known internal hostnames must be rejected even with HTTPS."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref(agent_url=f"https://{hostname}")

        with pytest.raises(AdCPAdapterError, match="blocked"):
            await resolve_property_list(ref)

    @pytest.mark.asyncio
    async def test_rejects_non_https_url(self):
        """Plain HTTP agent_url must be rejected (HTTPS required)."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref(agent_url="http://external-agent.example.com")

        with pytest.raises(AdCPAdapterError, match="HTTPS"):
            await resolve_property_list(ref)

    @pytest.mark.asyncio
    async def test_rejects_non_http_schemes(self):
        """Non-HTTP schemes (file://, ftp://, etc.) must be rejected."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref(agent_url="file:///etc/passwd")

        with pytest.raises(AdCPAdapterError, match="HTTPS"):
            await resolve_property_list(ref)

    @pytest.mark.asyncio
    async def test_allows_valid_https_public_url(self):
        """A valid HTTPS URL to a public host must still work."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref(agent_url="https://agent.example.com")
        response_json = _make_response_json(
            identifiers=[{"type": "domain", "value": "pub.com"}],
        )
        mock_response = _make_mock_response(response_json)

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await resolve_property_list(ref)

        assert result == ["pub.com"]

    @pytest.mark.asyncio
    async def test_validation_happens_before_http_request(self):
        """URL validation must reject BEFORE any network I/O."""
        from src.core.property_list_resolver import resolve_property_list

        ref = _make_ref(agent_url="http://evil.internal:9200")

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            with pytest.raises(AdCPAdapterError, match="HTTPS"):
                await resolve_property_list(ref)

            # AsyncClient should never have been instantiated
            mock_client_cls.assert_not_called()
