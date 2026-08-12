"""CapabilitiesEnv — integration test environment for get_adcp_capabilities.

Discovery operation, real DB reads (tenant config, publisher partners), best-effort
adapter channel lookup wrapped in a try/except in production — no patches needed.

Requires: integration_db fixture.
"""

from __future__ import annotations

from typing import Any

from adcp.types import GetAdcpCapabilitiesResponse

from tests.harness._base import IntegrationEnv


class CapabilitiesEnv(IntegrationEnv):
    """Integration test environment for get_adcp_capabilities.

    No patches — capabilities assembly reads real tenant config and degrades
    gracefully (try/except) around the optional adapter channel lookup.
    """

    EXTERNAL_PATCHES: dict[str, str] = {}
    REST_ENDPOINT = "/api/v1/capabilities"

    def _configure_mocks(self) -> None:
        """No mocks needed for read-only discovery operation."""

    def call_impl(self, **kwargs: Any) -> GetAdcpCapabilitiesResponse:
        """Call _get_adcp_capabilities_impl with real DB."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        req = kwargs.pop("req", None)
        return _get_adcp_capabilities_impl(req=req, identity=identity)

    def call_a2a(self, **kwargs: Any) -> Any:
        """Dispatch get_adcp_capabilities through the REAL A2A pipeline."""
        return self._run_a2a_handler("get_adcp_capabilities", GetAdcpCapabilitiesResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> Any:
        """Call get_adcp_capabilities via Client(mcp) — full pipeline dispatch."""
        return self._run_mcp_client("get_adcp_capabilities", GetAdcpCapabilitiesResponse, **kwargs)

    def _run_rest_request(self, endpoint: str, **kwargs: Any) -> Any:
        """GET /api/v1/capabilities — no request body (unlike the POST discovery routes)."""
        client, _identity = self._prepare_rest_request(kwargs)
        return client.get(endpoint)

    def parse_rest_response(self, data: dict[str, Any]) -> GetAdcpCapabilitiesResponse:
        """Parse REST JSON into GetAdcpCapabilitiesResponse."""
        return GetAdcpCapabilitiesResponse(**data)
