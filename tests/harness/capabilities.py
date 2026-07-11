"""CapabilitiesEnv — integration test environment for _get_adcp_capabilities_impl.

Patches: get_adapter ONLY (the adapter supplies channels + targeting
capabilities). Real: principal lookup, TenantConfigUoW publisher partners,
audit activity logging, all transport wrappers (MCP tool, A2A skill,
REST GET /api/v1/capabilities).

Requires: integration_db fixture.

Usage::

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with CapabilitiesEnv() as env:
            tenant, principal = env.setup_default_data()
            env.set_adapter_channels(["display", "ctv"])

            response = env.call_impl()
            assert response.media_buy is not None

Available mocks via env.mock:
    "adapter" -- get_adapter mock (default_channels + get_targeting_capabilities)
"""

from __future__ import annotations

from typing import Any

from src.core.tools.capabilities import GetAdcpCapabilitiesResponse
from tests.harness._base import IntegrationEnv


class CapabilitiesEnv(IntegrationEnv):
    """Integration test environment for get_adcp_capabilities.

    Only the adapter is mocked (external ad server). Publisher partners come
    from real DB rows (PublisherPartnerFactory); principal resolution and the
    transport wrappers are production code.
    """

    EXTERNAL_PATCHES = {
        "adapter": "src.core.tools.capabilities.get_adapter",
    }

    REST_ENDPOINT = "/api/v1/capabilities"
    REST_METHOD = "get"

    def _configure_mocks(self) -> None:
        from src.adapters.base import TargetingCapabilities

        adapter = self.mock["adapter"].return_value
        adapter.default_channels = ["display"]
        adapter.get_targeting_capabilities.return_value = TargetingCapabilities(geo_countries=True)

    def set_adapter_channels(self, channels: list[str]) -> None:
        """Configure the channels the mock adapter reports."""
        self.mock["adapter"].return_value.default_channels = channels

    def set_targeting_capabilities(self, capabilities: Any) -> None:
        """Configure the TargetingCapabilities the mock adapter reports."""
        self.mock["adapter"].return_value.get_targeting_capabilities.return_value = capabilities

    def call_impl(self, **kwargs: Any) -> GetAdcpCapabilitiesResponse:
        """Call _get_adcp_capabilities_impl with real DB."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        return _get_adcp_capabilities_impl(kwargs.pop("req", None), identity)

    def call_a2a(self, **kwargs: Any) -> Any:
        """Dispatch through the REAL A2A pipeline (_handle_get_adcp_capabilities_skill)."""
        return self._run_a2a_handler("get_adcp_capabilities", GetAdcpCapabilitiesResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> Any:
        """Call get_adcp_capabilities via Client(mcp) — full pipeline dispatch."""
        return self._run_mcp_client("get_adcp_capabilities", GetAdcpCapabilitiesResponse, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """GET route — no body."""
        return {}

    def parse_rest_response(self, data: dict[str, Any]) -> GetAdcpCapabilitiesResponse:
        """Parse REST JSON response into GetAdcpCapabilitiesResponse."""
        return GetAdcpCapabilitiesResponse(**data)
