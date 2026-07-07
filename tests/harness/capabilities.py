"""CapabilitiesEnv — integration test environment for get_adcp_capabilities.

Wired for the BR-UC-010 version-negotiation scenarios: every dispatch can
carry the AdCP version-negotiation envelope (``adcp_version`` /
``adcp_major_version``), so each transport exercises its own boundary
validation — the MCP RequestCompatMiddleware (Step 2), the A2A explicit-skill
dispatch, and the REST router dependency — before the tool runs.

Requires: integration_db fixture (creates test PostgreSQL DB). The tool's
DB reads (principal lookup, TenantConfigUoW) run for real against
factory-created rows from ``setup_default_data()``.
"""

from __future__ import annotations

from typing import Any

from adcp.types import GetAdcpCapabilitiesResponse

from src.core.request_compat import ADCP_NEGOTIATION_FIELDS
from tests.harness._base import IntegrationEnv


class CapabilitiesEnv(IntegrationEnv):
    """Integration test environment for _get_adcp_capabilities_impl.

    No external services to mock — capabilities is a pure discovery read.
    """

    REST_ENDPOINT = "/api/v1/capabilities"
    REST_METHOD = "get"

    def setup_default_data(self) -> tuple[Any, Any]:
        """Create the tenant + principal rows the identity references."""
        from tests.factories import PrincipalFactory, TenantFactory

        tenant = TenantFactory(tenant_id=self._tenant_id)
        principal = PrincipalFactory(tenant=tenant, principal_id=self._principal_id)
        self._commit_factory_data()
        return tenant, principal

    def call_impl(self, **kwargs: Any) -> GetAdcpCapabilitiesResponse:
        """Call _get_adcp_capabilities_impl.

        Negotiation-envelope kwargs are dropped: version negotiation is a
        transport-boundary concern and production ``_impl`` never sees the
        fields (they are validated and stripped by each wrapper).
        """
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        self._commit_factory_data()
        for field in ADCP_NEGOTIATION_FIELDS:
            kwargs.pop(field, None)
        kwargs.setdefault("identity", self.identity)
        kwargs.setdefault("req", None)
        return _get_adcp_capabilities_impl(**kwargs)

    def call_a2a(self, **kwargs: Any) -> GetAdcpCapabilitiesResponse:
        """Call get_adcp_capabilities via real AdCPRequestHandler — full A2A pipeline."""
        return self._run_a2a_handler("get_adcp_capabilities", GetAdcpCapabilitiesResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> GetAdcpCapabilitiesResponse:
        """Call get_adcp_capabilities via Client(mcp) — full pipeline dispatch."""
        return self._run_mcp_client("get_adcp_capabilities", GetAdcpCapabilitiesResponse, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """GET /capabilities has no body — flat kwargs become the query string.

        The negotiation pin travels as query params on REST (there is no JSON
        payload on a GET), which is exactly what the api_v1 router dependency
        validates.
        """
        return {k: v for k, v in kwargs.items() if v is not None}

    def parse_rest_response(self, data: dict[str, Any]) -> GetAdcpCapabilitiesResponse:
        """Parse REST JSON into GetAdcpCapabilitiesResponse."""
        return GetAdcpCapabilitiesResponse(**data)
