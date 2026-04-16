"""Tests for the AAO registry client."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.services.registry_client import RegistryClient


class TestRegistryClientInit:
    def test_default_url(self):
        client = RegistryClient()
        assert client.base_url == "https://agenticadvertising.org"

    def test_custom_url_strips_trailing_slash(self):
        client = RegistryClient(base_url="https://custom.example.com/")
        assert client.base_url == "https://custom.example.com"


class TestResolvePropertiesBulk:
    @pytest.mark.asyncio
    async def test_success_returns_results(self):
        """Registry returns resolved properties for domains."""
        client = RegistryClient(base_url="https://registry.test")

        mock_response = httpx.Response(
            200,
            json={
                "results": {
                    "ladepeche.fr": {
                        "publisher_domain": "ladepeche.fr",
                        "source": "adagents_json",
                        "properties": [
                            {
                                "id": "la_depeche",
                                "type": "website",
                                "name": "La Dépêche",
                                "identifiers": [{"type": "domain", "value": "ladepeche.fr"}],
                                "tags": [],
                            }
                        ],
                        "authorized_agents": [
                            {"url": "https://sales-agent.claire.pub", "authorized_for": "All properties"}
                        ],
                        "verified": True,
                    },
                    "example.com": None,
                }
            },
        )

        with patch("src.services.registry_client.httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await client.resolve_properties_bulk(["ladepeche.fr", "example.com"])

        assert "ladepeche.fr" in results
        assert results["ladepeche.fr"]["verified"] is True
        assert len(results["ladepeche.fr"]["properties"]) == 1
        assert results["ladepeche.fr"]["properties"][0]["id"] == "la_depeche"
        assert results["example.com"] is None

    @pytest.mark.asyncio
    async def test_empty_domains_returns_empty_dict(self):
        """Empty domain list returns empty dict without API call."""
        client = RegistryClient(base_url="https://registry.test")
        results = await client.resolve_properties_bulk([])
        assert results == {}

    @pytest.mark.asyncio
    async def test_api_error_returns_empty_dict(self):
        """API error returns empty dict."""
        client = RegistryClient(base_url="https://registry.test")

        mock_response = httpx.Response(500, json={"error": "Internal error"})

        with patch("src.services.registry_client.httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await client.resolve_properties_bulk(["ladepeche.fr"])

        assert results == {}

    @pytest.mark.asyncio
    async def test_limits_to_100_domains(self):
        """Bulk request caps at 100 domains."""
        client = RegistryClient(base_url="https://registry.test")

        mock_response = httpx.Response(200, json={"results": {}})

        with patch("src.services.registry_client.httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            domains = [f"domain{i}.com" for i in range(150)]
            await client.resolve_properties_bulk(domains)

            call_args = mock_ctx.post.call_args
            sent_domains = call_args[1]["json"]["domains"]
            assert len(sent_domains) == 100


class TestResolveProperty:
    @pytest.mark.asyncio
    async def test_success(self):
        """Single domain resolution works."""
        client = RegistryClient(base_url="https://registry.test")

        mock_response = httpx.Response(
            200,
            json={
                "publisher_domain": "ladepeche.fr",
                "source": "adagents_json",
                "properties": [{"id": "la_depeche", "type": "website"}],
                "verified": True,
            },
        )

        with patch("src.services.registry_client.httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.resolve_property("ladepeche.fr")

        assert result is not None
        assert result["publisher_domain"] == "ladepeche.fr"

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        """404 returns None."""
        client = RegistryClient(base_url="https://registry.test")

        mock_response = httpx.Response(404, json={"error": "not found"})

        with patch("src.services.registry_client.httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.resolve_property("x.com")

        assert result is None

    @pytest.mark.asyncio
    async def test_server_error_returns_none(self):
        """Non-404 error returns None."""
        client = RegistryClient(base_url="https://registry.test")

        mock_response = httpx.Response(503, json={"error": "unavailable"})

        with patch("src.services.registry_client.httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.resolve_property("example.com")

        assert result is None
