"""Tests for the AAO registry client."""

import httpx
import pytest
import respx

from src.services.registry_client import RegistryClient


class TestRegistryClient:
    """Tests for RegistryClient."""

    def test_init_default_url(self):
        client = RegistryClient()
        assert client.base_url == "https://agenticadvertising.org"

    def test_init_custom_url(self):
        client = RegistryClient(base_url="https://custom.example.com/")
        assert client.base_url == "https://custom.example.com"

    @pytest.mark.asyncio
    @respx.mock
    async def test_resolve_properties_bulk_success(self):
        """Registry returns resolved properties for domains."""
        client = RegistryClient(base_url="https://registry.test")

        respx.post("https://registry.test/api/properties/resolve/bulk").mock(
            return_value=httpx.Response(
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
        )

        results = await client.resolve_properties_bulk(["ladepeche.fr", "example.com"])

        assert "ladepeche.fr" in results
        assert results["ladepeche.fr"]["verified"] is True
        assert len(results["ladepeche.fr"]["properties"]) == 1
        assert results["ladepeche.fr"]["properties"][0]["id"] == "la_depeche"
        assert results["example.com"] is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_resolve_properties_bulk_empty_domains(self):
        """Empty domain list returns empty dict without API call."""
        client = RegistryClient(base_url="https://registry.test")
        results = await client.resolve_properties_bulk([])
        assert results == {}

    @pytest.mark.asyncio
    @respx.mock
    async def test_resolve_properties_bulk_api_error(self):
        """API error returns empty dict."""
        client = RegistryClient(base_url="https://registry.test")

        respx.post("https://registry.test/api/properties/resolve/bulk").mock(
            return_value=httpx.Response(500, json={"error": "Internal error"})
        )

        results = await client.resolve_properties_bulk(["ladepeche.fr"])
        assert results == {}

    @pytest.mark.asyncio
    @respx.mock
    async def test_resolve_property_success(self):
        """Single domain resolution works."""
        client = RegistryClient(base_url="https://registry.test")

        respx.get("https://registry.test/api/properties/resolve").mock(
            return_value=httpx.Response(
                200,
                json={
                    "publisher_domain": "ladepeche.fr",
                    "source": "adagents_json",
                    "properties": [{"id": "la_depeche", "type": "website"}],
                    "verified": True,
                },
            )
        )

        result = await client.resolve_property("ladepeche.fr")
        assert result is not None
        assert result["publisher_domain"] == "ladepeche.fr"

    @pytest.mark.asyncio
    @respx.mock
    async def test_resolve_property_not_found(self):
        """404 returns None."""
        client = RegistryClient(base_url="https://registry.test")

        respx.get("https://registry.test/api/properties/resolve").mock(
            return_value=httpx.Response(404, json={"error": "not found", "domain": "x.com"})
        )

        result = await client.resolve_property("x.com")
        assert result is None
