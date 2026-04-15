"""Client for the AAO Property Registry API.

Resolves publisher domains to their full property definitions via the
AgenticAdvertising.org registry, which handles authoritative_location
delegation, property_ids resolution, and all authorization models.
"""

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

AAO_REGISTRY_URL = os.environ.get("AAO_REGISTRY_URL", "https://agenticadvertising.org")


class RegistryClient:
    """Client for the AAO property registry API."""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or AAO_REGISTRY_URL).rstrip("/")

    async def resolve_properties_bulk(
        self, domains: list[str], timeout: float = 15.0
    ) -> dict[str, dict[str, Any] | None]:
        """Resolve publisher domains to their property definitions via the AAO registry.

        Args:
            domains: Publisher domains to resolve (max 100)
            timeout: Request timeout in seconds

        Returns:
            Dict mapping domain to resolved property data (or None if not found).
            Each resolved property contains:
            - publisher_domain: str
            - source: "adagents_json" | "hosted" | "discovered"
            - authorized_agents: [{url, authorized_for}]
            - properties: [{id, type, name, identifiers, tags}]
            - contact: {name, email}
            - verified: bool
        """
        if not domains:
            return {}

        url = f"{self.base_url}/api/properties/resolve/bulk"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json={"domains": domains[:100]},
                headers={"User-Agent": "AdCP-SalesAgent/1.0"},
                timeout=timeout,
            )

        if response.status_code != 200:
            logger.error(f"Registry bulk resolve failed: HTTP {response.status_code}")
            return {}

        data = response.json()
        return data.get("results", {})

    async def resolve_property(self, domain: str, timeout: float = 10.0) -> dict[str, Any] | None:
        """Resolve a single publisher domain to its property definitions.

        Args:
            domain: Publisher domain to resolve
            timeout: Request timeout in seconds

        Returns:
            Resolved property data or None if not found
        """
        url = f"{self.base_url}/api/properties/resolve"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                params={"domain": domain},
                headers={"User-Agent": "AdCP-SalesAgent/1.0"},
                timeout=timeout,
            )

        if response.status_code == 404:
            return None
        if response.status_code != 200:
            logger.error(f"Registry resolve failed for {domain}: HTTP {response.status_code}")
            return None

        return response.json()


def get_registry_client() -> RegistryClient:
    """Get a registry client instance."""
    return RegistryClient()
