"""CapabilitiesEnv — integration test environment for get_adcp_capabilities.

Nothing external is mocked: capabilities is a read-only discovery call whose
whole answer is derived from the tenant row, its publisher partnerships and the
bound ad-server adapter. Those all live in the real database, so the env seeds a
tenant/principal via factories (``ad_server="mock"`` → ``MockAdServer``) and lets
production resolve the adapter for real. The one scenario-scoped override is
``set_adapter_pricing_models`` (degrade partitions) — it pins the resolved
adapter's declared pricing surface without touching adapter resolution itself.

Transport coverage: A2A (``get_adcp_capabilities`` skill), MCP
(``get_adcp_capabilities`` tool), and REST. The REST route is
``GET /api/v1/capabilities`` — the only harness endpoint that is not a POST —
so this env overrides ``_run_rest_request`` to issue a bodyless GET and declares
``REST_METHOD`` for the e2e dispatcher (precedent: ``media_buy_dual.py``).

Usage::

    with CapabilitiesEnv() as env:
        env.setup_default_data()
        result = env.call_via(Transport.MCP)
        assert result.payload.media_buy.supported_pricing_models
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from adcp.types import GetAdcpCapabilitiesResponse

from tests.harness._base import IntegrationEnv
from tests.harness._realize import e2e_unsupported, realize_e2e


class CapabilitiesEnv(IntegrationEnv):
    """Integration test environment for ``_get_adcp_capabilities_impl``."""

    EXTERNAL_PATCHES: dict[str, str] = {}
    REST_ENDPOINT = "/api/v1/capabilities"
    # Read the dispatcher contract: RestE2EDispatcher does
    # ``getattr(env, "REST_METHOD", "post")``. Capabilities is a GET.
    REST_METHOD = "get"

    @realize_e2e(
        e2e_unsupported(
            "the live stack resolves the tenant's real bound adapter, whose pricing "
            "surface is fixed production code — a degenerate or off-enum adapter "
            "cannot be injected over e2e"
        )
    )
    def set_adapter_pricing_models(self, models: set[str]) -> None:
        """Pin what the bound (mock) adapter reports as its pricing surface.

        The degrade partitions of POST-S10 need an adapter that reports nothing
        or off-enum strings; production still resolves the REAL ``MockAdServer``
        (``EXTERNAL_PATCHES`` stays empty), only its declared pricing surface is
        overridden. The patch rides ``self._patchers`` so ``__exit__`` stops it
        with the base teardown — no bleed into sibling scenarios.
        """
        from src.adapters.mock_ad_server import MockAdServer

        patcher = patch.object(MockAdServer, "get_supported_pricing_models", return_value=set(models))
        patcher.start()
        self._patchers.append(patcher)

    def call_impl(self, **kwargs: Any) -> GetAdcpCapabilitiesResponse:
        """Call ``_get_adcp_capabilities_impl`` directly (no wire)."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        kwargs.setdefault("req", None)
        return _get_adcp_capabilities_impl(kwargs["req"], kwargs["identity"])

    def call_a2a(self, **kwargs: Any) -> GetAdcpCapabilitiesResponse:
        """Call the get_adcp_capabilities skill via the real AdCPRequestHandler."""
        return self._run_a2a_handler("get_adcp_capabilities", GetAdcpCapabilitiesResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> GetAdcpCapabilitiesResponse:
        """Call the get_adcp_capabilities tool via Client(mcp) — full pipeline."""
        return self._run_mcp_client("get_adcp_capabilities", GetAdcpCapabilitiesResponse, **kwargs)

    def _run_rest_request(self, endpoint: str, **kwargs: Any) -> Any:
        """Issue the discovery GET.

        The inherited implementation hardcodes ``client.post(...)`` with a JSON
        body; ``/api/v1/capabilities`` is a GET with no body and would 405.
        Everything before the verb (identity pop, factory commit, auth-dep
        override) is reused via ``_prepare_rest_request``.
        """
        client, _identity = self._prepare_rest_request(kwargs)
        return client.get(endpoint)

    def parse_rest_response(self, data: dict[str, Any]) -> GetAdcpCapabilitiesResponse:
        """Parse REST JSON into GetAdcpCapabilitiesResponse."""
        return GetAdcpCapabilitiesResponse(**data)
