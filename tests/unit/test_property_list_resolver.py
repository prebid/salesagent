"""Unit tests for property list resolver with caching."""

import json
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

    Returns a public IP for all hostnames by default — both ``gethostbyname``
    (check_url_ssrf) and ``getaddrinfo`` (resolve_validated_ip's all-records validation
    + connection pinning). Tests in TestSSRFProtection override these where needed.
    """
    with (
        patch("src.core.security.url_validator.socket.gethostbyname", return_value="93.184.216.34"),
        patch(
            "src.core.security.url_validator.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ),
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
    """Tests for resolve_property_list_typed()."""

    @pytest.mark.asyncio
    async def test_successful_resolution(self):
        """Successful HTTP call returns identifier value strings."""
        from src.core.property_list_resolver import resolve_property_list_typed

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

            result = await resolve_property_list_typed(ref)

        assert [(ident.type.value, ident.value) for ident in result] == [
            ("domain", "example.com"),
            ("domain", "test.org"),
        ]

    @pytest.mark.asyncio
    async def test_bearer_auth_header_sent(self):
        """When auth_token is present, Bearer header is sent."""
        from src.core.property_list_resolver import resolve_property_list_typed

        ref = _make_ref(auth_token="my-secret-token")
        response_json = _make_response_json(
            identifiers=[{"type": "domain", "value": "a.com"}],
        )
        mock_response = _make_mock_response(response_json)

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await resolve_property_list_typed(ref)

        call_kwargs = mock_client.get.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer my-secret-token"

    @pytest.mark.asyncio
    async def test_no_auth_header_when_token_is_none(self):
        """When auth_token is None, no Authorization header is sent."""
        from src.core.property_list_resolver import resolve_property_list_typed

        ref = _make_ref(auth_token=None)
        response_json = _make_response_json(
            identifiers=[{"type": "domain", "value": "a.com"}],
        )
        mock_response = _make_mock_response(response_json)

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await resolve_property_list_typed(ref)

        call_kwargs = mock_client.get.call_args
        assert "Authorization" not in call_kwargs.kwargs["headers"]

    @pytest.mark.asyncio
    async def test_correct_url_construction(self):
        """GET request is sent to {agent_url}/lists/{list_id}."""
        from src.core.property_list_resolver import resolve_property_list_typed

        ref = _make_ref(agent_url="https://agent.example.com", list_id="my-list-42")
        response_json = _make_response_json(
            identifiers=[{"type": "domain", "value": "a.com"}],
        )
        mock_response = _make_mock_response(response_json)

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await resolve_property_list_typed(ref)

        call_args = mock_client.get.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url")
        assert url == "https://agent.example.com/lists/my-list-42"

    @pytest.mark.asyncio
    async def test_empty_identifiers_returns_empty_list(self):
        """When identifiers is None in response, return empty list."""
        from src.core.property_list_resolver import resolve_property_list_typed

        ref = _make_ref()
        response_json = _make_response_json(identifiers=None)
        mock_response = _make_mock_response(response_json)

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await resolve_property_list_typed(ref)

        assert result == []


class TestNonJsonPayload:
    """A non-JSON 2xx maps to a typed AdCPAdapterError naming the list service."""

    @pytest.mark.asyncio
    async def test_async_non_json_2xx_raises_typed_adapter_error(self):
        from src.core.property_list_resolver import resolve_property_list_typed

        ref = _make_ref()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.side_effect = json.JSONDecodeError("Expecting value", "<html>", 0)

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(AdCPAdapterError, match="non-JSON response"):
                await resolve_property_list_typed(ref)

    def test_sync_non_json_2xx_raises_typed_adapter_error_and_is_not_cached(self):
        from src.core.property_list_resolver import clear_cache, resolve_property_list_typed_sync

        clear_cache()
        ref = _make_ref()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.side_effect = json.JSONDecodeError("Expecting value", "<html>", 0)

        sync_client = MagicMock()
        sync_client.__enter__ = MagicMock(return_value=sync_client)
        sync_client.__exit__ = MagicMock(return_value=False)
        sync_client.get.return_value = mock_response

        with patch("src.core.property_list_resolver.httpx.Client", return_value=sync_client):
            with pytest.raises(AdCPAdapterError, match="non-JSON response"):
                resolve_property_list_typed_sync(ref)
            # The failed decode must not poison the cache.
            with pytest.raises(AdCPAdapterError, match="non-JSON response"):
                resolve_property_list_typed_sync(ref)
        assert sync_client.get.call_count == 2
        clear_cache()


class TestCaching:
    """Tests for cache behavior."""

    @pytest.mark.asyncio
    async def test_cached_result_avoids_http_call(self):
        """Second call with same ref returns cached result, no HTTP."""
        from src.core.property_list_resolver import resolve_property_list_typed

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

            result1 = await resolve_property_list_typed(ref)
            result2 = await resolve_property_list_typed(ref)

        assert [ident.value for ident in result1] == ["cached.com"]
        assert result2 == result1
        assert mock_client.get.call_count == 1  # Only one HTTP call

    @pytest.mark.asyncio
    async def test_expired_cache_causes_refetch(self):
        """Expired cache entry triggers a new HTTP call."""
        from src.core.property_list_resolver import resolve_property_list_typed

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

            await resolve_property_list_typed(ref)
            await resolve_property_list_typed(ref)

        assert mock_client.get.call_count == 2  # Both calls hit HTTP

    @pytest.mark.asyncio
    async def test_no_cache_valid_until_uses_default_ttl(self):
        """When cache_valid_until is None, cache with default TTL."""
        from src.core.property_list_resolver import resolve_property_list_typed

        ref = _make_ref()
        response_json = _make_response_json(
            identifiers=[{"type": "domain", "value": "default-ttl.com"}],
            cache_valid_until=None,
        )
        mock_response = _make_mock_response(response_json)

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result1 = await resolve_property_list_typed(ref)
            result2 = await resolve_property_list_typed(ref)

        assert [ident.value for ident in result1] == ["default-ttl.com"]
        assert result2 == result1
        assert mock_client.get.call_count == 1  # Cached despite no cache_valid_until

    @pytest.mark.asyncio
    async def test_different_list_ids_cached_separately(self):
        """Different list_ids have separate cache entries."""
        from src.core.property_list_resolver import resolve_property_list_typed

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

            result_a = await resolve_property_list_typed(ref1)
            result_b = await resolve_property_list_typed(ref2)

        assert [ident.value for ident in result_a] == ["a.com"]
        assert [ident.value for ident in result_b] == ["b.com"]
        assert call_count == 2


class TestErrorHandling:
    """Tests for error wrapping -- core invariant: no raw httpx exceptions escape."""

    @pytest.mark.asyncio
    async def test_http_error_raises_adcp_adapter_error(self):
        """HTTP 5xx errors are wrapped in AdCPAdapterError (transient).

        4xx responses are the buyer's error and map to a correctable
        VALIDATION_ERROR instead — pinned by ``TestFetchErrorTaxonomy``.
        """
        from src.core.property_list_resolver import resolve_property_list_typed

        ref = _make_ref()

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Service Unavailable",
            request=httpx.Request("GET", "https://example.com/lists/list-1"),
            response=httpx.Response(503),
        )

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(AdCPAdapterError, match="property list"):
                await resolve_property_list_typed(ref)

    @pytest.mark.asyncio
    async def test_timeout_raises_adcp_adapter_error(self):
        """httpx.TimeoutException is wrapped in AdCPAdapterError."""
        from src.core.property_list_resolver import resolve_property_list_typed

        ref = _make_ref()

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(
                get_side_effect=httpx.TimeoutException("timed out"),
            )
            mock_client_cls.return_value = mock_client

            with pytest.raises(AdCPAdapterError, match="timed out"):
                await resolve_property_list_typed(ref)

    @pytest.mark.asyncio
    async def test_connection_error_raises_adcp_adapter_error(self):
        """httpx.ConnectError is wrapped in AdCPAdapterError."""
        from src.core.property_list_resolver import resolve_property_list_typed

        ref = _make_ref()

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(
                get_side_effect=httpx.ConnectError("connection refused"),
            )
            mock_client_cls.return_value = mock_client

            with pytest.raises(AdCPAdapterError, match="connection"):
                await resolve_property_list_typed(ref)

    @pytest.mark.asyncio
    async def test_failed_requests_are_not_cached(self):
        """Failed HTTP calls should not populate the cache."""
        from src.core.property_list_resolver import resolve_property_list_typed

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
                await resolve_property_list_typed(ref)

            # Second call should succeed (not return cached error)
            result = await resolve_property_list_typed(ref)

        assert [ident.value for ident in result] == ["success.com"]
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
        from src.core.property_list_resolver import resolve_property_list_typed

        ref = _make_ref(agent_url=malicious_url)

        with pytest.raises(AdCPAdapterError, match="HTTPS"):
            await resolve_property_list_typed(ref)

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
        """HTTPS URLs that resolve to private/internal IPs must be rejected.

        resolve_validated_ip validates every getaddrinfo result, so a host resolving to a
        private IP is rejected before any connect — and because the fetch is pinned to the
        validated IP, a later rebind to a private address cannot redirect the connection.
        """
        from src.core.property_list_resolver import resolve_property_list_typed

        ref = _make_ref(agent_url=malicious_url)

        with patch(
            "src.core.security.url_validator.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", (resolved_ip, 0))],
        ):
            with pytest.raises(AdCPAdapterError, match="[Bb]locked|[Pp]rivate|[Ii]nternal"):
                await resolve_property_list_typed(ref)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "hostname",
        ["localhost", "metadata.google.internal", "169.254.169.254"],
    )
    async def test_rejects_blocked_hostnames(self, hostname):
        """Known internal hostnames must be rejected even with HTTPS."""
        from src.core.property_list_resolver import resolve_property_list_typed

        ref = _make_ref(agent_url=f"https://{hostname}")

        with pytest.raises(AdCPAdapterError, match="blocked"):
            await resolve_property_list_typed(ref)

    @pytest.mark.asyncio
    async def test_rejects_non_https_url(self):
        """Plain HTTP agent_url must be rejected (HTTPS required)."""
        from src.core.property_list_resolver import resolve_property_list_typed

        ref = _make_ref(agent_url="http://external-agent.example.com")

        with pytest.raises(AdCPAdapterError, match="HTTPS"):
            await resolve_property_list_typed(ref)

    @pytest.mark.asyncio
    async def test_rejects_non_http_schemes(self):
        """Non-HTTP schemes (file://, ftp://, etc.) must be rejected."""
        from src.core.property_list_resolver import resolve_property_list_typed

        ref = _make_ref(agent_url="file:///etc/passwd")

        with pytest.raises(AdCPAdapterError, match="HTTPS"):
            await resolve_property_list_typed(ref)

    @pytest.mark.asyncio
    async def test_allows_valid_https_public_url(self):
        """A valid HTTPS URL to a public host must still work."""
        from src.core.property_list_resolver import resolve_property_list_typed

        ref = _make_ref(agent_url="https://agent.example.com")
        response_json = _make_response_json(
            identifiers=[{"type": "domain", "value": "pub.com"}],
        )
        mock_response = _make_mock_response(response_json)

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = _make_mock_client(get_return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await resolve_property_list_typed(ref)

        assert [ident.value for ident in result] == ["pub.com"]

    @pytest.mark.asyncio
    async def test_validation_happens_before_http_request(self):
        """URL validation must reject BEFORE any network I/O."""
        from src.core.property_list_resolver import resolve_property_list_typed

        ref = _make_ref(agent_url="http://evil.internal:9200")

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            with pytest.raises(AdCPAdapterError, match="HTTPS"):
                await resolve_property_list_typed(ref)

            # AsyncClient should never have been instantiated
            mock_client_cls.assert_not_called()


class TestThreadSafety:
    """The module-level cache is shared across threads — sync adapters
    (KevelSiteResolver), the async discovery path, and the create-media-buy
    advisory all touch it. Reads/writes/expiry-drops must be atomic.
    """

    def test_concurrent_expired_drop_does_not_raise(self):
        """Concurrent expiry drops use ``pop(key, None)``, not ``del`` — a
        second thread reaching the expiry branch after the first removed the
        key must not ``KeyError``. The lock also keeps the cache write atomic
        so every thread converges on the same identifiers.
        """
        import threading

        from src.core import property_list_resolver as plr
        from src.core.property_list_resolver import resolve_property_list_typed_sync

        ref = _make_ref()
        agent_url = str(ref.agent_url)
        # Pre-populate an EXPIRED entry to force the expiry-pop branch on entry.
        plr._cache.store((agent_url, ref.list_id), [], datetime.now(UTC) - timedelta(seconds=1))

        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        response_json = _make_response_json(
            identifiers=[{"type": "domain", "value": "fresh.com"}],
            cache_valid_until=future,
        )

        def _make_sync_client():
            client = MagicMock()
            client.__enter__ = MagicMock(return_value=client)
            client.__exit__ = MagicMock(return_value=None)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = response_json
            resp.raise_for_status = MagicMock()
            client.get = MagicMock(return_value=resp)
            return client

        errors: list[Exception] = []
        results: list[list] = []
        results_lock = threading.Lock()

        def _run():
            try:
                res = resolve_property_list_typed_sync(ref)
                with results_lock:
                    results.append(res)
            except Exception as exc:  # noqa: BLE001 — captured for assertion
                with results_lock:
                    errors.append(exc)

        with patch(
            "src.core.property_list_resolver.httpx.Client",
            side_effect=lambda *a, **k: _make_sync_client(),
        ):
            threads = [threading.Thread(target=_run) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

        assert not errors, f"Concurrent resolve raised (expiry-drop race / torn read): {errors!r}"
        assert len(results) == 8, f"Expected 8 thread results, got {len(results)} — a thread hung"
        assert all([ident.value for ident in r] == ["fresh.com"] for r in results), (
            "Concurrent resolvers returned inconsistent identifiers — cache write was not atomic"
        )


class TestFetchErrorTaxonomy:
    """E6: buyer-correctable 4xx splits from transient infrastructure failures.

    A typo'd list_id answered 404 by the list service is the BUYER's error —
    classifying it transient (retry-after-delay) sends buyers into indefinite
    retry loops on input they must correct. 5xx/timeout/connect stay transient.
    """

    @staticmethod
    def _sync_client_raising(status_code: int):
        request = httpx.Request("GET", "https://gov.example/lists/x")
        response = httpx.Response(status_code, request=request)
        exc = httpx.HTTPStatusError("err", request=request, response=response)
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = exc
        sync_client = MagicMock()
        sync_client.__enter__ = MagicMock(return_value=sync_client)
        sync_client.__exit__ = MagicMock(return_value=False)
        sync_client.get.return_value = mock_response
        return sync_client

    def test_sync_404_is_correctable_validation_error(self):
        from src.core.exceptions import AdCPValidationError
        from src.core.property_list_resolver import clear_cache, resolve_property_list_typed_sync

        clear_cache()
        ref = _make_ref()
        with patch("src.core.property_list_resolver.httpx.Client", return_value=self._sync_client_raising(404)):
            with pytest.raises(AdCPValidationError) as excinfo:
                resolve_property_list_typed_sync(ref)
        assert excinfo.value.error_code == "VALIDATION_ERROR"
        assert excinfo.value.recovery == "correctable"
        assert ref.list_id in str(excinfo.value)

    def test_sync_4xx_message_sanitizes_buyer_list_id(self):
        # CWE-117: list_id carries no charset constraint, so an embedded newline could forge
        # operator log lines once this 4xx message reaches the boundary error logger. The message
        # must route list_id through loggable_list_id — a revert to a raw f-string reddens this.
        from src.core.exceptions import AdCPValidationError
        from src.core.property_list_resolver import clear_cache, resolve_property_list_typed_sync

        clear_cache()
        ref = _make_ref(list_id="evil\nINJECTED-LOG-LINE")
        with patch("src.core.property_list_resolver.httpx.Client", return_value=self._sync_client_raising(404)):
            with pytest.raises(AdCPValidationError) as excinfo:
                resolve_property_list_typed_sync(ref)
        message = str(excinfo.value)
        assert "evil\nINJECTED-LOG-LINE" not in message, "raw newline-bearing list_id must not reach the message"
        assert "evilINJECTED-LOG-LINE" in message, "the sanitized list_id (newline stripped) should still be shown"

    def test_sync_500_stays_transient(self):
        from src.core.property_list_resolver import clear_cache, resolve_property_list_typed_sync

        clear_cache()
        ref = _make_ref()
        with patch("src.core.property_list_resolver.httpx.Client", return_value=self._sync_client_raising(500)):
            with pytest.raises(AdCPAdapterError) as excinfo:
                resolve_property_list_typed_sync(ref)
        assert excinfo.value.recovery == "transient"

    def test_sync_429_is_transient_rate_limit(self):
        # 429 is a rate limit (the spec's transient example), NOT a buyer-correctable
        # 4xx — classifying it correctable would wrongly tell the buyer to fix the request
        # instead of backing off and retrying.
        from src.core.exceptions import AdCPRateLimitError
        from src.core.property_list_resolver import clear_cache, resolve_property_list_typed_sync

        clear_cache()
        ref = _make_ref()
        with patch("src.core.property_list_resolver.httpx.Client", return_value=self._sync_client_raising(429)):
            with pytest.raises(AdCPRateLimitError) as excinfo:
                resolve_property_list_typed_sync(ref)
        assert excinfo.value.error_code == "RATE_LIMITED"
        assert excinfo.value.recovery == "transient"

    @pytest.mark.asyncio
    async def test_async_403_is_correctable_validation_error(self):
        from src.core.exceptions import AdCPValidationError
        from src.core.property_list_resolver import clear_cache, resolve_property_list_typed

        clear_cache()
        ref = _make_ref()
        request = httpx.Request("GET", "https://gov.example/lists/x")
        response = httpx.Response(403, request=request)
        exc = httpx.HTTPStatusError("err", request=request, response=response)
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = exc

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value = _make_mock_client(get_return_value=mock_response)
            with pytest.raises(AdCPValidationError) as excinfo:
                await resolve_property_list_typed(ref)
        assert excinfo.value.recovery == "correctable"

    @pytest.mark.asyncio
    async def test_async_timeout_stays_transient(self):
        from src.core.property_list_resolver import clear_cache, resolve_property_list_typed

        clear_cache()
        ref = _make_ref()
        mock_client = _make_mock_client(get_return_value=None)
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("slow"))
        with patch("src.core.property_list_resolver.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(AdCPAdapterError) as excinfo:
                await resolve_property_list_typed(ref)
        assert excinfo.value.recovery == "transient"


def _package_with(ref: PropertyListReference | None):
    """A package-shaped object exposing ``targeting_overlay.property_list``."""
    pkg = MagicMock()
    pkg.targeting_overlay = MagicMock() if ref is not None else None
    if ref is not None:
        pkg.targeting_overlay.property_list = ref
    return pkg


class TestPropertyListCacheKey:
    """The canonical (agent_url, list_id, auth_partition) key — one source for every consumer."""

    def test_stringifies_agent_url_and_keeps_list_id(self):
        from src.core.property_list_resolver import property_list_cache_key

        ref = _make_ref(agent_url="https://lists.example.com", list_id="cb_v1", auth_token="tok")
        key = property_list_cache_key(ref)
        assert key[0] == "https://lists.example.com/"
        assert key[1] == "cb_v1"
        # AnyUrl str() must be used so the key is hashable/comparable across sites.
        assert all(isinstance(part, str) for part in key)
        # Third element partitions by auth_token via a non-reversible HMAC (not the
        # plaintext token).
        assert len(key) == 3
        assert key[2] and "tok" not in key[2]

    def test_same_ref_yields_equal_keys(self):
        from src.core.property_list_resolver import property_list_cache_key

        assert property_list_cache_key(_make_ref()) == property_list_cache_key(_make_ref())

    def test_different_auth_token_partitions_same_list(self):
        # Two principals reference the SAME (agent_url, list_id) with different
        # bearer tokens -> different cache keys, so one cannot read another's
        # access-gated list from the shared cache. Same token -> same key (a
        # principal still shares its own cache entry).
        from src.core.property_list_resolver import property_list_cache_key

        common = {"agent_url": "https://lists.example.com", "list_id": "cb_v1"}
        key_a = property_list_cache_key(_make_ref(**common, auth_token="token-A"))
        key_b = property_list_cache_key(_make_ref(**common, auth_token="token-B"))
        assert key_a != key_b
        assert key_a == property_list_cache_key(_make_ref(**common, auth_token="token-A"))

    def test_missing_auth_token_partitions_consistently(self):
        # A None token maps to a stable sentinel partition (unauthenticated lists
        # still share one entry) distinct from any real token's partition.
        from src.core.property_list_resolver import property_list_cache_key

        common = {"agent_url": "https://lists.example.com", "list_id": "cb_v1"}
        key_none = property_list_cache_key(_make_ref(**common, auth_token=None))
        assert key_none == property_list_cache_key(_make_ref(**common, auth_token=None))
        assert key_none != property_list_cache_key(_make_ref(**common, auth_token="token-A"))


class TestCacheAuthPartitioning:
    """The shared process-global cache must not leak one principal's access-gated
    list to another principal that references the same (agent_url, list_id)."""

    def test_sync_resolver_does_not_serve_cross_principal_cache_hit(self):
        from src.core.property_list_resolver import resolve_property_list_typed_sync

        common = {"agent_url": "https://lists.example.com", "list_id": "shared-list"}
        ref_a = _make_ref(**common, auth_token="token-A")
        ref_b = _make_ref(**common, auth_token="token-B")

        # Principal A resolves and populates the cache.
        resp_a = _make_mock_response(_make_response_json(identifiers=[{"type": "domain", "value": "a-only.example"}]))
        with patch("src.core.property_list_resolver.httpx.Client") as client_cls_a:
            client_cls_a.return_value.__enter__.return_value.get.return_value = resp_a
            ids_a = resolve_property_list_typed_sync(ref_a)

        # Principal B references the SAME list with a different token. The list
        # service would 401/403 B, so the cache must NOT serve A's identifiers —
        # B must fetch fresh.
        resp_b = _make_mock_response(_make_response_json(identifiers=[{"type": "domain", "value": "b-only.example"}]))
        with patch("src.core.property_list_resolver.httpx.Client") as client_cls_b:
            client_b = client_cls_b.return_value.__enter__.return_value
            client_b.get.return_value = resp_b
            ids_b = resolve_property_list_typed_sync(ref_b)
            # B fetched fresh with ITS OWN token (0 calls would mean a
            # cross-principal cache hit serving A's entry).
            client_b.get.assert_called_once_with(
                "https://lists.example.com/lists/shared-list",
                headers={"Authorization": "Bearer token-B"},
            )

        assert [i.value for i in ids_a] == ["a-only.example"]
        assert [i.value for i in ids_b] == ["b-only.example"]

    def test_same_principal_reuses_cache_entry(self):
        # Control: the SAME token still shares the cache, so auth-partitioning does
        # not break legitimate same-principal reuse (only one HTTP fetch).
        from src.core.property_list_resolver import resolve_property_list_typed_sync

        ref = _make_ref(agent_url="https://lists.example.com", list_id="shared-list", auth_token="token-A")
        resp = _make_mock_response(_make_response_json(identifiers=[{"type": "domain", "value": "a.example"}]))
        with patch("src.core.property_list_resolver.httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.get.return_value = resp
            resolve_property_list_typed_sync(ref)
            resolve_property_list_typed_sync(ref)
            # Cached after the first fetch: the second call is served from cache.
            client.get.assert_called_once_with(
                "https://lists.example.com/lists/shared-list",
                headers={"Authorization": "Bearer token-A"},
            )


class TestMalformedResponseRecovery:
    """A schema-invalid (valid-JSON) list-service response is the SERVICE
    misbehaving — surfaced as a transient AdCPAdapterError consistently across both
    resolver paths, never a raw ValidationError that the Kevel path normalizes to
    terminal INTERNAL_ERROR (and the async advisory path silently swallows)."""

    def test_sync_malformed_response_is_transient(self):
        from src.core.exceptions import AdCPAdapterError
        from src.core.property_list_resolver import resolve_property_list_typed_sync

        bad = _make_response_json()
        bad["identifiers"] = "not-a-list"  # valid JSON, schema-invalid (expects a list)
        resp = _make_mock_response(bad)
        with patch("src.core.property_list_resolver.httpx.Client") as client_cls:
            client_cls.return_value.__enter__.return_value.get.return_value = resp
            with pytest.raises(AdCPAdapterError) as exc_info:
                resolve_property_list_typed_sync(_make_ref())
        assert exc_info.value.recovery == "transient"

    @pytest.mark.asyncio
    async def test_async_malformed_response_is_transient(self):
        from src.core.exceptions import AdCPAdapterError
        from src.core.property_list_resolver import resolve_property_list_typed

        bad = _make_response_json()
        bad["identifiers"] = "not-a-list"
        resp = _make_mock_response(bad)
        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value = _make_mock_client(get_return_value=resp)
            with pytest.raises(AdCPAdapterError) as exc_info:
                await resolve_property_list_typed(_make_ref())
        assert exc_info.value.recovery == "transient"


class TestIterPackagePropertyListRefs:
    """The shared walk/pluck/key skeleton consumed by prefetch, advisory, and the Kevel gate."""

    def test_yields_index_package_ref_key_and_skips_refless_packages(self):
        from src.core.property_list_resolver import iter_package_property_list_refs, property_list_cache_key

        ref0 = _make_ref(list_id="a")
        ref2 = _make_ref(list_id="c")
        pkg0, pkg1, pkg2 = _package_with(ref0), _package_with(None), _package_with(ref2)

        rows = list(iter_package_property_list_refs([pkg0, pkg1, pkg2]))

        # The ref-less middle package is skipped, but indices reflect the ORIGINAL
        # position so ``packages[i]`` error field paths stay correct.
        assert [(index, ref, key) for index, _pkg, ref, key in rows] == [
            (0, ref0, property_list_cache_key(ref0)),
            (2, ref2, property_list_cache_key(ref2)),
        ]
        assert [pkg for _i, pkg, _r, _k in rows] == [pkg0, pkg2]

    def test_empty_iterable_yields_nothing(self):
        from src.core.property_list_resolver import iter_package_property_list_refs

        assert list(iter_package_property_list_refs([])) == []
